import threading
import time

from config.config import SEVERITY_ORDER

from output.writer import EventDispatcher
from collectors.file_collector import FileCollector
from collectors.auth_collector import create_auth_collector
from collectors.network_collector import NetworkCollector
from collectors.process_collector import ProcessCollector
from collectors.usb_collector import USBCollector
# from collectors.harddisk_collector import HardDiskCollector   # see block at end of start()
from collectors.capacity_monitoring_collector import ResourceCollector
from collectors.engines_handler import EnginesHandler
from collectors.web_inspector import WebInspector
from collectors.appserver_inspector import AppServerInspector

from utils.utils import get_machine_info
from utils.command_registry import (register, add_static_collector,
                                    stop_dynamic_collectors, get_status)
from utils.shutdown import call_with_timeout, force_exit_if_stuck, install_signal_handlers


class SentinelAgent:
    def __init__(self, config: dict, agent_name: str):
        self.config = config
        self._collectors = []
        self._dispatcher = None
        self._running = False
        self._stopping = False
        self._stop_lock = threading.Lock()      # stop() can arrive from signal + command
        self.machine_info = get_machine_info()
        self.machine_info["agent_name"] = agent_name

    # ----------------------------------------------------------------- setup --

    def _build_dispatcher(self):
        cfg = self.config["output"]
        return EventDispatcher(stdout=cfg.get("stdout", False))

    def _make_dispatch(self):
        """Returns a filtered dispatch function."""
        cfg = self.config.get("filters", {})
        min_sev_str = cfg.get("min_severity", "info")
        min_sev_idx = SEVERITY_ORDER.index(min_sev_str) if min_sev_str in SEVERITY_ORDER else 0
        excl_cats = set(cfg.get("exclude_categories", []))
        excl_actions = set(cfg.get("exclude_actions", []))

        def dispatch(event_dict: dict, machine_info):
            sev = event_dict.get("severity", "info")
            if SEVERITY_ORDER.index(sev) < min_sev_idx:
                return
            if event_dict.get("category") in excl_cats:
                return
            if event_dict.get("action") in excl_actions:
                return
            self._dispatcher.push(event_dict, machine_info)

        return dispatch

    def _add_handler(self, name, factory):
        """On-demand inspector: constructed and registered, started later by a
        start_* command."""
        try:
            register(name, factory())
        except Exception as e:
            print(f"{name} error: {e}")

    def _add_collector(self, name, factory):
        """Always-on collector: started now, and registered as static so a
        global pause can stop it.

        Started BEFORE registering, so a collector that fails to start is not
        left in the static set for a later resume to trip over.
        """
        try:
            c = factory()
            c.start()
            add_static_collector(name, c)
            self._collectors.append(c)
            print(f"{name} started")
        except ImportError as e:
            print(f"{name} unavailable: {e}")
        except Exception as e:
            print(f"{name} error: {e}")

    def start(self):
        self._dispatcher = self._build_dispatcher()
        dispatch = self._make_dispatch()
        mi = self.machine_info

        # --- on-demand inspectors (driven by start_*/stop_* commands) ---------
        self._add_handler("Appserver_inspector",
                          lambda: AppServerInspector(dispatch, machine_info=mi))
        self._add_handler("engines_handler",
                          lambda: EnginesHandler(dispatch=dispatch, machine_info=mi))
        self._add_handler("web_inspector",
                          lambda: WebInspector(dispatch=dispatch, machine_info=mi))

        # --- always-on collectors --------------------------------------------
        self._add_collector("Resource collector", lambda: ResourceCollector(
            dispatch=dispatch, machine_info=mi, poll_interval=10.0))

        self._add_collector("File collector", lambda: FileCollector(
            dispatch=dispatch, machine_info=mi, watch_paths=None,
            ignore_dirs=None, recursive=True, use_polling=False))

        self._add_collector("Auth collector", lambda: create_auth_collector(
            dispatch=dispatch, machine_info=mi))

        self._add_collector("Network collector", lambda: NetworkCollector(
            dispatch=dispatch, machine_info=mi, poll_interval=2.0, track_bandwidth=True))

        self._add_collector("Process collector", lambda: ProcessCollector(
            dispatch=dispatch, machine_info=mi, poll_interval=1.5,
            resource_interval=30.0, hash_executables=True))

        self._add_collector("Usb collector", lambda: USBCollector(
            dispatch=dispatch, machine_info=mi, poll_interval=3.0,
            scan_on_connect=True, transfer_threshold_bytes=524288000))

        # Hard disk collector — re-enable by uncommenting the import above too.
        # hd = self.config.get("collectors", {}).get("harddisk", {})
        # if hd.get("enabled", True):
        #     self._add_collector("HardDisk collector", lambda: HardDiskCollector(
        #         dispatch=dispatch, machine_info=mi,
        #         poll_interval=hd.get("poll_interval", 30.0),
        #         smart_interval=hd.get("smart_interval", 300.0),
        #         warn_percent=hd.get("warn_percent", 85.0),
        #         critical_percent=hd.get("critical_percent", 95.0),
        #         enable_smart=hd.get("enable_smart", True)))

        self._running = True
        print("Agent running. Press Ctrl+C to stop.")

    # -------------------------------------------------------------- shutdown --

    def stop(self):
        with self._stop_lock:
            if self._stopping:          # second Ctrl+C, or pause-then-Ctrl+C
                return
            self._stopping = True
        self._running = False           # releases wait()

        print("Stopping collectors...")

        # 1. per-service inspectors first: they push events, and they are the
        #    ones the control server thinks it started.
        call_with_timeout(stop_dynamic_collectors, 20.0, "dynamic collectors")

        # 2. always-on collectors. Skipped if a global pause already stopped
        #    them — otherwise a collector with a slow stop() costs the timeout
        #    twice for no reason. (stop() should still be idempotent.)
        if get_status() != "pause":
            for c in self._collectors:
                call_with_timeout(c.stop, 5.0, type(c).__name__)
        else:
            print("Agent is paused; collectors are already stopped.")

        # 3. flush queued events BEFORE any hard exit, or they are lost.
        if self._dispatcher:
            call_with_timeout(self._dispatcher.flush_and_stop, 10.0, "dispatcher")

        print("Sentinel Agent stopped.")

        # 4. last resort, and it must be last: os._exit kills everything above.
        force_exit_if_stuck(grace=3.0)

    def wait(self):
        """Block the main thread until stopped. Call from the main thread."""
        install_signal_handlers(self.stop)      # handles SIGINT and SIGTERM
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:               # fallback if handlers didn't install
            print("\nCtrl+C received.")
            self.stop()


def deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result