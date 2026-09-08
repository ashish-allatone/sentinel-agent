import sys
import time
import threading
import importlib

from collectors.flyprobe import fly as _fly
from schema.fly_event import FlyEvent ,EventOutcome,Severity

PROBE = "collectors.flyprobe.fly"

_ALIAS = {"flyctl": "cli", "local": "cli", "host": "cli",
          "remote": "api", "token": "api",
          "container": "cli-docker", "docker": "cli-docker"}
_CONTROL_KEYS = {"server", "engine", "action", "source", "id"}


def _canon(s):
    s = str(s or "").strip().lower()
    return _ALIAS.get(s, s)



def _driver_status(name):
    """('ok'|'missing'|'broken', msg). Import for real (find_spec only locates)."""
    if not name:
        return "ok", None
    if name in sys.modules:
        return "ok", None
    try:
        importlib.import_module(name)
        return "ok", None
    except ModuleNotFoundError as e:
        if (getattr(e, "name", "") or "").split(".")[0] == name.split(".")[0]:
            return "missing", (f"driver '{name}' not installed on this agent host "
                               f"(pip install {name})")
        return "broken", f"driver '{name}' installed but unusable: {e}"
    except BaseException as e:
        return "broken", f"driver '{name}' installed but unusable: {e}"


def _source_id(detail):
    backend = _canon(detail.get("backend") or detail.get("source"))
    # print("source",backend)
    if backend == "cli-docker" and detail.get("container"):
        who = detail.get("container_name") or detail.get("container") or "?"
        return f"docker:{who}"
    # print(backend)
    return f"host:{backend or 'auto'}"


def _build_params(detail):
    params = {k: v for k, v in (detail or {}).items() if k not in _CONTROL_KEYS}
    backend = _canon(detail.get("backend") or detail.get("source"))
    if backend == "cli-docker":
        params["backend"] = "cli-docker"
        params.setdefault("container", detail.get("container"))
        params.setdefault("fly_bin", detail.get("fly_bin", "fly"))
    elif backend == "api" and not params.get("token"):
        # api needs the REAL token, not the detect flag — surface it clearly
        params["_error"] = "api backend started without a token (detect only sets api_token_present)"
    else:
        params["backend"] = backend or "auto"
    return params


class FlyInspector:
    """Per-source inspection threads, started/stopped on demand — same shape as
    WebInspector. Detection is the separate function run_fly_detect(); hand each
    detected source dict to start()."""

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
        sid = _source_id(detail)
        service_name = detail.get("service_name")
        if service_name in self._threads and self._threads[service_name].is_alive():
            return {"ok": False, "source": sid,"service_name" : service_name, "error": "already running"}
        params = _build_params(detail)
        stop = threading.Event()
        self._stops[service_name] = stop
        t = threading.Thread(target=self._loop, name=f"flyinspect-{sid}={service_name}",
                             daemon=True, args=(sid, params, stop))
        self._threads[service_name] = t
        t.start()
        return {"ok": True, "source": sid, "status": "started",
                "backend": params.get("backend"), "container": params.get("container")}

    def _loop(self, sid, params, stop):
        print(f"[flyinspect] {sid}: thread started (backend={params.get('backend')})")
        while not stop.is_set():
            try:
                ev = self.inspect(sid, params)
                if ev is not None:
                    self.send(ev)
                if sid in self._driver_error:
                    print(f"[flyinspect] {sid}: {self._driver_error[sid]}")
                    print(f"[flyinspect] {sid}: stopping (restart via API once fixed)")
                    break
            except Exception as ex:
                print(f"[flyinspect] {sid}: error {ex}")
            slept = 0.0
            while slept < self._interval and not stop.is_set():
                time.sleep(0.5); slept += 0.5
        print(f"[flyinspect] {sid}: thread stopped")

    def inspect(self, sid, params):
        ev = FlyEvent()
        ev.server = "fly"
        ev.backend = params.get("backend")
        ev.action = "fly_detected"
        ev.outcome = EventOutcome.SUCCESS
        ev.detected = True
        ev.exe_path = params.get("fly_bin")
        ev.target_name = sid
        ev.tags = ["fly", "inspect", params.get("backend") or "auto"]
        try:
            # probe = importlib.import_module(PROBE)
            status, msg = _driver_status(getattr(_fly, "DRIVER", None))
            if status != "ok":
                raise ImportError(msg)
        except BaseException as e:
            msg = str(e)[:300]
            self._driver_error[sid] = msg
            ev.running = False; ev.inspected = False; ev.severity = Severity.LOW
            ev.inspect_error = msg; ev.notes = f"probe unavailable: {msg}"
            return ev
        try:
            res = _fly.inspect(params)
            if res.get("backend") is None:
                ev.running = False; ev.inspected = False
                ev.notes = ("no usable backend — authenticate flyctl "
                            "(`fly auth login`) or provide a token")
                return ev
            ev.running = True
            ev.auth_method = res.get("backend")
            ev.apply_inspect(res)
        except Exception as e:
            ev.running = False; ev.inspected = False; ev.severity = Severity.LOW
            ev.inspect_error = str(e)[:300]
            ev.notes = f"inspection failed for {sid}"
        return ev

    def send(self, ev):
        self._dispatch(ev.to_dict(), self._machine_info)

    def stop(self, args):
        source = args.get("source")
        service_name = args.get(service_name)
        return self._stop_one(source , service_name)

    def _stop_one(self, source , service_name):
        sid = _source_id(source) if isinstance(source, dict) else str(source)
        stop = self._stops.get(service_name)
        if not stop:
            return {"ok": False, "source": sid, "service_name" : service_name ,"error": "not running"}
        stop.set()
        t = self._threads.get(service_name)
        if t:
            t.join(timeout=5)
        self._threads.pop(service_name, None)
        self._stops.pop(service_name, None)
        self._driver_error.pop(service_name, None)
        return {"ok": True, "source": sid,"service_name": service_name, "status": "stopped"}
