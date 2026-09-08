import sys
import time
import threading
import importlib

from schema.web_event_base import EventOutcome, Severity
from schema.web_server_event import WebServerEvent

PROBES = {"nginx": "nginx", "apache": "apache"}
_ALIAS = {"httpd": "apache", "apache2": "apache", "apache_http": "apache",
          "nginx_server": "nginx"}
_DEFAULT_PORT = 80

_STATUS_PATH = {"nginx": "/nginx_status",
                "apache": "/server-status?auto"}
_CONTROL_KEYS = {"server", "engine", "action"}


def _canon(s):
    s = str(s or "").strip().lower()
    return _ALIAS.get(s, s)


def _driver_status(name):
    """Return ('ok'|'missing'|'broken', message).

    Same reasoning as the db inspector: importlib.util.find_spec() only LOCATES
    a module, it never executes it, so an installed-but-unloadable package
    still passes. Import it for real and tell the two failure kinds apart.
    """
    if not name:
        return "ok", None
    if name in sys.modules:
        return "ok", None
    try:
        importlib.import_module(name)
        return "ok", None
    except ModuleNotFoundError as e:
        if (getattr(e, "name", "") or "").split(".")[0] == name.split(".")[0]:
            return "missing", (f"driver '{name}' not installed on this agent "
                               f"host (pip install {name})")
        return "broken", f"driver '{name}' installed but unusable: {e}"
    except BaseException as e:
        return "broken", f"driver '{name}' installed but unusable: {e}"


def _status_url(server, host, port):
    """Build the status URL automatically.

        nginx  -> http://host:port/nginx_status
        apache -> http://host:port/server-status?auto
    """
    path = _STATUS_PATH.get(server)
    if not path:
        return None
    port = int(port or _DEFAULT_PORT)
    scheme = "https" if port == 443 else "http"
    # leave the default port off so the URL stays clean
    netloc = host if port in (80, 443) else f"{host}:{port}"
    return f"{scheme}://{netloc}{path}"


def _build_params(server, detail):
    """Turn the API detail dict into the params dict handed to the probe.

    Everything the caller sent is preserved, so probe-specific fields such as
    access_log / error_log / tls_hosts just work. status_url is generated from
    server+host+port when the caller did not supply one.
    """
    params = {k: v for k, v in (detail or {}).items() if k not in _CONTROL_KEYS}
    params.setdefault("host", "127.0.0.1")
    if not params.get("port"):
        params["port"] = _DEFAULT_PORT
    if not params.get("status_url"):
        params["status_url"] = _status_url(server, params["host"], params["port"])
    return params


class WebInspector:
    

    def __init__(self, dispatch, machine_info, interval=60.0):
        self._dispatch = dispatch
        self._machine_info = machine_info
        self._interval = interval
        self._threads = {}        # server -> Thread
        self._stops = {}          # server -> Event
        self._driver_error = {}   # server -> message (unrecoverable)

    # ── start one server, or many if a list of details is given ──
    def start(self, detail:dict):
        return self._start_one(detail)

    def _start_one(self, detail):
        detail = detail or {}
        server = _canon(detail.get("server") or detail.get("engine"))
        service_name = detail.get("service_name")
        if server not in PROBES:
            return {"ok": False, "server": server, "error": "unknown web server"}
        if server in self._threads and self._threads[server].is_alive():
            return {"ok": False, "server": server, "error": "already running"}

        params = _build_params(server, detail)

        stop = threading.Event()
        self._stops[service_name] = stop
        t = threading.Thread(target=self._loop, name=f"webinspect-{server}-{service_name}",
                             daemon=True, args=(server, params, stop))
        self._threads[service_name] = t
        t.start()
        return {"ok": True, "server": server, "status": "started",
                "host": params.get("host"), "port": params.get("port"),
                "status_url": params.get("status_url")}

    # ── the per-server thread: loop inspect() -> send() ──
    def _loop(self, server, params, stop):
        print(f"[webinspect] {server}: thread started -> {params.get('status_url')}")
        while not stop.is_set():
            try:
                ev = self.inspect(server, params)
                if ev is not None:
                    self.send(ev)
                if server in self._driver_error:
                    print(f"[webinspect] {server}: {self._driver_error[server]}")
                    print(f"[webinspect] {server}: stopping (restart via API once fixed)")
                    break
            except Exception as ex:
                print(f"[webinspect] {server}: error {ex}")
            slept = 0.0
            while slept < self._interval and not stop.is_set():
                time.sleep(0.5)
                slept += 0.5
        print(f"[webinspect] {server}: thread stopped")

    # ── inspect: build the event, call webprobe with params ──
    def inspect(self, server, params):
        host = params.get("host")
        ev = WebServerEvent()
        ev.server = server
        ev.action = "web_detected"
        ev.outcome = EventOutcome.SUCCESS
        ev.detected = True
        ev.db_host = host
        ev.db_port = params.get("port")
        ev.target_name = f"{server}@{host}"
        ev.tags = ["webserver", "inspect", server]

        try:
            probe = importlib.import_module(f"collectors.webprobe.{PROBES[server]}")
            status, msg = _driver_status(getattr(probe, "DRIVER", None))
            if status != "ok":
                raise ImportError(msg)
        except BaseException as e:
            msg = str(e)[:300]
            self._driver_error[server] = msg
            ev.running = False
            ev.inspected = False
            ev.severity = Severity.LOW
            ev.inspect_error = msg
            ev.notes = f"probe unavailable for {server}: {msg}"
            return ev

        try:
            
            res = probe.inspect(params)          # whole dict goes to the probe
            ev.running = True
            ev.auth_method = "configured"
            ev.apply_inspect(res)
        except Exception as e:
            ev.running = False
            ev.inspected = False
            ev.severity = Severity.LOW
            ev.inspect_error = str(e)[:300]
            ev.notes = ("inspection failed (check status_url is enabled: "
                        f"{params.get('status_url')})")
        return ev

    # ── send: dispatch the event out ──
    def send(self, ev):
        self._dispatch(ev.to_dict(), self._machine_info)

    # ── stop one server by name, or many if a list is given ──
    def stop(self, args):
        server = args.get("server")
        service_name = args.get("service_name")
        return self._stop_one(server , service_name)

    def _stop_one(self, server , service_name):
        server = _canon(server)
        stop = self._stops.get(service_name)
        if not stop:
            return {"ok": False, "server": server,"service_name" : service_name, "error": "not running"}
        stop.set()
        t = self._threads.get(service_name)
        if t:
            t.join(timeout=5)
        self._threads.pop(service_name, None)
        self._stops.pop(service_name, None)
        self._driver_error.pop(service_name, None)
        return {"ok": True, "server": server, "status": "stopped"}
