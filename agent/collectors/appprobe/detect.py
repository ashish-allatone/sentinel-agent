import os
from typing import List, Dict, Any
from ._util import find_cli, _home_from_exe

# process markers -> product
_MARKERS = {
    "standalone": "wildfly", "domain": "wildfly",
    "jboss-modules.jar": "wildfly", "wildfly": "wildfly", "jboss": "jboss",
}
_MGMT_PORT = 9990


def _mgmt_ports(pid: int) -> List[int]:
    ports = set()
    try:
        import psutil
        for c in psutil.Process(pid).connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                ports.add(c.laddr.port)
    except Exception:
        pass
    return sorted(ports)


def detect_appservers() -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    try:
        import psutil
    except Exception:
        return []
    for proc in psutil.process_iter(["name", "pid", "cmdline", "exe"]):
        try:
            hay = " ".join(proc.info.get("cmdline") or []).lower() + " " + (proc.info.get("name") or "").lower()
            product = next((v for k, v in _MARKERS.items() if k in hay), None)
            if not product:
                continue
            pid = proc.info["pid"]
            ports = _mgmt_ports(pid)
            mgmt = _MGMT_PORT if _MGMT_PORT in ports else (ports[0] if ports else _MGMT_PORT)
            exe = proc.info.get("exe")
            # try to locate jboss-cli from this process's install folder
            import os as _os
            home = _home_from_exe(exe) if exe else None
            cli_path = None
            if home:
                for nm in ("jboss-cli.bat", "jboss-cli.sh"):
                    cand = _os.path.join(home, "bin", nm)
                    if _os.path.isfile(cand):
                        cli_path = cand
                        break
            if not cli_path:
                cli_path = find_cli()
            key = f"{product}:{mgmt}"
            found.setdefault(key, {
                "engine": "product",
                "running": True, "pid": pid,
                "exe_path": exe,
                "jboss_home": home,
                "host": "127.0.0.1", "port": mgmt,
                "ports": ports,
                "cli_available": bool(cli_path),
                "cli_path": cli_path,
            })
        except Exception:
            continue
    return list(found.values())