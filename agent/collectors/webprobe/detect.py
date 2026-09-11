import json
import subprocess
from typing import Dict, List, Any, Optional

# process-name -> canonical server
_NAMES = {
    "nginx": "nginx", "nginx.exe": "nginx",
    "httpd": "apache", "httpd.exe": "apache",
    "apache2": "apache", "apache": "apache", "apache2.exe": "apache",
}

# substring found in an image name -> canonical server
_IMAGE_HINTS = {
    "nginx": "nginx",
    "openresty": "nginx",
    "httpd": "apache",
    "apache": "apache",
}

_DEFAULT_PORTS = {"nginx": 80, "apache": 80}


# ── local processes ───────────────────────────────────
def _listening_ports(pid: int) -> List[int]:
    """Ports this pid is LISTENing on. Often empty without admin/root."""
    ports = set()
    try:
        import psutil
        proc = psutil.Process(pid)
        # net_connections() replaces the deprecated connections() in psutil 6+
        conns = proc.net_connections if hasattr(proc, "net_connections") \
            else proc.connections
        for c in conns(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                ports.add(c.laddr.port)
    except Exception:
        pass
    return sorted(ports)


def detect_process_servers() -> List[Dict[str, Any]]:
    """Web servers running as processes on this host."""
    found: Dict[str, Dict[str, Any]] = {}
    try:
        import psutil
    except Exception:
        return []
    for proc in psutil.process_iter(["name", "pid", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            server = _NAMES.get(name)
            if not server:
                continue
            pid = proc.info["pid"]
            ports = _listening_ports(pid)
            # keep the first (usually master) process per server, enrich ports
            row = found.setdefault(server, {
                "engine": server,
                "running": True, "pid": pid,
                "exe_path": proc.info.get("exe"),
                "host": "127.0.0.1",
                "port": _DEFAULT_PORTS.get(server),
                "ports": [],
                "source": "process",
            })
            for p in ports:
                if p not in row["ports"]:
                    row["ports"].append(p)
            if ports:
                # prefer 80/443 as the headline port if present
                row["port"] = 443 if 443 in ports else (80 if 80 in ports else ports[0])
        except Exception:
            continue
    return list(found.values())


# ── docker containers ─────────────────────────────────
def _server_from_image(image: str) -> Optional[str]:
    img = (image or "").lower()
    for hint, server in _IMAGE_HINTS.items():
        if hint in img:
            return server
    return None


def _host_ports(ports_str: str) -> List[int]:
    """
    Parse the Ports column, e.g.
      "0.0.0.0:8080->80/tcp, :::8080->80/tcp"
    into the published host ports [8080].
    """
    out: List[int] = []
    for part in (ports_str or "").split(","):
        part = part.strip()
        if "->" not in part:
            continue
        left = part.split("->", 1)[0]
        hostport = left.rsplit(":", 1)[-1]
        try:
            p = int(hostport)
        except ValueError:
            continue
        if p not in out:
            out.append(p)
    return sorted(out)


def detect_docker_servers() -> List[Dict[str, Any]]:
    """
    Web servers running in local Docker containers.
    Returns [] quietly if Docker isn't installed or isn't running.
    """
    try:
        res = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    if res.returncode != 0:
        return []

    rows: List[Dict[str, Any]] = []
    for line in (res.stdout or "").strip().splitlines():
        try:
            c = json.loads(line)
        except Exception:
            continue
        image = c.get("Image", "")
        server = _server_from_image(image)
        if not server:
            continue
        ports = _host_ports(c.get("Ports", ""))
        if not ports:
            # not published to the host -> unreachable from here, skip
            continue
        headline = 443 if 443 in ports else (80 if 80 in ports else ports[0])
        rows.append({
            "engine": server,
            "running": True,
            "pid": None,
            "exe_path": None,
            "host": "127.0.0.1",
            "port": headline,
            "ports": ports,
            "source": "docker",
            "container_id": c.get("ID"),
            "container_name": c.get("Names"),
            "image": image,
            "name": f"{server}@docker/{c.get('Names')}",
        })
    return rows


# ── combined ──────────────────────────────────────────
def detect_servers(include_docker: bool = True) -> List[Dict[str, Any]]:
    """
    All locally-reachable web servers: host processes first, then containers.
    Container entries are kept separate from process entries (a container is
    not the same server as a host process of the same type).
    """
    servers = detect_process_servers()
    if include_docker:
        servers.extend(detect_docker_servers())
    return servers


if __name__ == "__main__":
    for s in detect_servers():
        print(s)