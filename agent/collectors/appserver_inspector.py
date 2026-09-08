import sys
import time
import threading
import importlib

from schema.appserver_event import AppServerEvent, EventOutcome, Severity

PROBES = {"wildfly": "wildfly", "jboss": "wildfly"}   # both use the wildfly probe
_ALIAS = {"jboss-eap": "jboss", "eap": "jboss", "wildfly-full": "wildfly"}
_CONTROL_KEYS = {"server", "engine", "action", "source", "id"}
_DEFAULT_PORT = 9990


def _canon(s):
    s = str(s or "").strip().lower()
    return _ALIAS.get(s, s)


def _driver_status(name):
    if not name:
        return "ok", None
    if name in sys.modules:
        return "ok", None
    try:
        importlib.import_module(name)
        return "ok", None
    except ModuleNotFoundError as e:
        if (getattr(e, "name", "") or "").split(".")[0] == name.split(".")[0]:
            return "missing", f"driver '{name}' not installed (pip install {name})"
        return "broken", f"driver '{name}' installed but unusable: {e}"
    except BaseException as e:
        return "broken", f"driver '{name}' installed but unusable: {e}"


def _source_id(detail):
    server = _canon(detail.get("server") or detail.get("engine") or "wildfly")
    host = detail.get("host") or "127.0.0.1"
    port = int(detail.get("port") or _DEFAULT_PORT)
    backend = (detail.get("backend") or "api").lower()
    return f"{server}:{backend}:{host}:{port}"


def _build_params(detail):
    params = {k: v for k, v in (detail or {}).items() if k not in _CONTROL_KEYS}
    params.setdefault("host", "127.0.0.1")
    params.setdefault("port", _DEFAULT_PORT)
    backend = (detail.get("backend") or "auto").lower()

    if backend in ("auto", "cli"):
        cli_path = detail.get("cli_path")
        if cli_path == "string" or cli_path=="":
            try:
                from collectors.appprobe._util import find_cli
                cli_path = find_cli()          # PATH -> env -> running process/service exe
            except Exception:
                cli_path = None
        if cli_path:
            params["backend"] = "cli"
            params["cli_path"] = cli_path      # auto-filled -> also ends up in the event
        elif backend == "cli":
            params["backend"] = "cli"          # user forced cli; let probe report the miss
        else:
            params["backend"] = "api"          # auto + no cli -> api fallback
    else:
        params["backend"] = backend            # explicit api / cli-docker etc.
    return params


class AppServerInspector:
    def __init__(self, dispatch, machine_info, interval=60.0):
        self._dispatch = dispatch
        self._machine_info = machine_info
        self._interval = interval
        self._threads = {}
        self._stops = {}
        self._driver_error = {}

    def start(self, detail):
        return self._start_one(detail)

    def _start_one(self, detail):
        detail = detail or {}
        server = _canon(detail.get("server") or detail.get("engine"))
        service_name = detail.get("service_name")
        if server not in PROBES:
            return {"ok": False, "server": server, "error": "unknown app server"}
        sid = _source_id({**detail, "server": server})
        if service_name in self._threads and self._threads[service_name].is_alive():
            return {"ok": False, "source": sid, "error": "already running"}
        params = _build_params(detail)
        stop = threading.Event()
        self._stops[service_name] = stop
        t = threading.Thread(target=self._loop, name=f"appinspect-{sid}-{service_name}",
                             daemon=True, args=(sid, server, params, stop))
        self._threads[service_name] = t
        t.start()
        return {"ok": True, "source": sid, "status": "started",
                "server": server, "backend": params.get("backend"),
                "host": params.get("host"), "port": params.get("port")}

    def _loop(self, sid, server, params, stop):
        print(f"[appinspect] {sid}: thread started (backend={params.get('backend')})")
        while not stop.is_set():
            try:
                ev = self.inspect(sid, server, params)
                if ev is not None and not stop.is_set():
                    self.send(ev)
                if sid in self._driver_error:
                    print(f"[appinspect] {sid}: {self._driver_error[sid]} — stopping")
                    break
            except Exception as ex:
                print(f"[appinspect] {sid}: error {ex}")
            slept = 0.0
            while slept < self._interval and not stop.is_set():
                time.sleep(0.5); slept += 0.5
        print(f"[appinspect] {sid}: thread stopped")

    def inspect(self, sid, server, params):
        ev = AppServerEvent()
        ev.server = server
        ev.backend = params.get("backend")
        ev.action = "appserver_detected"
        ev.outcome = EventOutcome.SUCCESS
        ev.detected = True
        ev.app_host = params.get("host")
        ev.app_port = params.get("port")
        ev.exe_path = params.get("cli_path")     # show which jboss-cli was auto-resolved
        ev.target_name = f"{server}@{params.get('host')}:{params.get('port')}"
        ev.tags = ["appserver", "inspect", server, params.get("backend") or "api"]
        try:
            probe = importlib.import_module(f"collectors.appprobe.{PROBES[server]}")
            status, msg = _driver_status(getattr(probe, "DRIVER", None))
            if status != "ok":
                raise ImportError(msg)
        except BaseException as e:
            msg = str(e)[:300]
            self._driver_error[sid] = msg
            ev.running = False; ev.inspected = False; ev.severity = Severity.LOW
            ev.inspect_error = msg; ev.notes = f"probe unavailable: {msg}"
            return ev
        try:
            res = probe.inspect(params)
            if not (res.get("metrics") or {}).get("reachable", True):
                ev.running = False
                ev.inspected = False
                ev.health_status = "unreachable"
                ev.severity = Severity.HIGH
                # pull the error note from connectivity if present
                cv = (res.get("sections") or {}).get("connectivity_version") or {}
                ev.inspect_error = (cv.get("error") or "unreachable")[:300]
                ev.connectivity_version = cv
                ev.notes = "enabled but unreachable (401 = wrong mgmt user/password, or bad host:port)"
                return ev
            ev.running = True
            ev.auth_method = params.get("backend")
            ev.apply_inspect(res)
        except Exception as e:
            ev.running = False; ev.inspected = False; ev.severity = Severity.LOW
            ev.inspect_error = str(e)[:300]
            ev.notes = f"inspection failed for {sid}"
        return ev

    def send(self, ev):
        self._dispatch(ev.to_dict(), self._machine_info)

    def stop(self, args):
        if isinstance(source, (list, tuple)):
            return [self._stop_one(s) for s in source]
        source = args.get("source")
        service_name = args.get("service_name")
        return self._stop_one(source , service_name)

    def _stop_one(self, source , service_name):
        sid = _source_id(source) if isinstance(source, dict) else str(source)
        stop = self._stops.get(service_name)
        if not stop:
            return {"ok": False, "source": sid,"service_name":service_name, "error": "not running"}
        stop.set()
        t = self._threads.get(service_name)
        if t:
            t.join(timeout=5)
        self._threads.pop(service_name, None)
        self._stops.pop(service_name, None)
        self._driver_error.pop(service_name, None)
        return {"ok": True, "source": sid,"service_name": service_name, "status": "stopped"}