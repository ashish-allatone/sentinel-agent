import time
import platform

from config.config import SEVERITY_ORDER 

from output.writer import EventDispatcher
from collectors.file_collector import FileCollector
from collectors.auth_collector import create_auth_collector
from collectors.network_collector import NetworkCollector
from collectors.process_collector import ProcessCollector
from collectors.usb_collector import USBCollector
from collectors.harddisk_collector import HardDiskCollector


from collectors.capacity_monitoring_collector import ResourceCollector
from utils.utils import get_machine_info
from collectors.engines_handler import EnginesHandler
from utils.command_registry import register , add_static_collector
from collectors.web_inspector import WebInspector
from collectors.fly_inspector import FlyInspector
from collectors.appserver_inspector import AppServerInspector





class SentinelAgent:
    def __init__(self, config: dict , agent_name:str):
        self.config     = config
        self._collectors = []
        self._dispatcher = None
        self._running   = False
        self.machine_info = get_machine_info()
        self.machine_info["agent_name"] = agent_name

    def _build_dispatcher(self):
        cfg = self.config["output"]

        return EventDispatcher(
            stdout=cfg.get("stdout", False),
        )

    def _make_dispatch(self):
        """Returns a filtered dispatch function."""
        cfg          = self.config.get("filters", {})
        min_sev_str  = cfg.get("min_severity", "info")
        min_sev_idx  = SEVERITY_ORDER.index(min_sev_str) if min_sev_str in SEVERITY_ORDER else 0
        excl_cats    = set(cfg.get("exclude_categories", []))
        excl_actions = set(cfg.get("exclude_actions", []))

        def dispatch(event_dict: dict , machine_info):
            sev = event_dict.get("severity", "info")
            if SEVERITY_ORDER.index(sev) < min_sev_idx:
                return
            if event_dict.get("category") in excl_cats:
                return
            if event_dict.get("action") in excl_actions:
                return
            self._dispatcher.push(event_dict , machine_info)

        return dispatch

    def start(self):
        self._dispatcher = self._build_dispatcher()
        dispatch = self._make_dispatch()

        try :
            AppInsp = AppServerInspector(dispatch, machine_info=self.machine_info)
            register("Appserver_inspector", AppInsp)
            self._collectors.append(AppInsp)
        except Exception as e:
            print(f"FlyInspector error: {e}")


        try :
            insp = FlyInspector(dispatch, machine_info=self.machine_info, interval=60)
            register("fly_inspector", insp)
            self._collectors.append(insp)
        except Exception as e:
            print(f"FlyInspector error: {e}")



        try:
            eh = EnginesHandler(dispatch=dispatch, machine_info=self.machine_info)
            register("engines_handler", eh)
            self._collectors.append(eh)
        except Exception as e:
            print(f"Engine Handler error: {e}")
        try:
            wi = WebInspector(dispatch=dispatch, machine_info=self.machine_info)
            # wi.start({"server": "apache", "host": "127.0.0.1", "port": 8080})
            register("web_inspector", wi)
            self._collectors.append(wi)
        except Exception as e:
            print(f"Web_Server inspector error: {e}")
        try:
            rc = ResourceCollector(
                dispatch      = dispatch,
                machine_info  = self.machine_info,
                poll_interval = 10.0,
            )
            rc.start()
            add_static_collector(rc)
            self._collectors.append(rc)
            print("Resource Collector started")
        except Exception as e:
            print(f"Resource collector error: {e}")


        try:
            
            fc = FileCollector(
                dispatch    = dispatch,
                machine_info= self.machine_info,
                watch_paths = None,
                ignore_dirs = None,
                recursive   = True,
                use_polling = False,
            )
            add_static_collector(fc)
            fc.start()
            self._collectors.append(fc)
            print("File Collector started")
        except ImportError as e:
            print(f"File collector unavailable: {e}")
        except Exception as e:
            print(f"File collector error: {e}")


        try:
            
            ac = create_auth_collector(
                dispatch       = dispatch,
                machine_info = self.machine_info
            )
            add_static_collector(ac)
            ac.start()
            self._collectors.append(ac)
            print("Auth Collector started")
        except Exception as e:
            print(f"Auth collector error: {e}")

        try:
            
            nc = NetworkCollector(
                dispatch        = dispatch,
                machine_info= self.machine_info,
                poll_interval   = 2.0,
                track_bandwidth = True
            )
            add_static_collector(nc)
            nc.start()

            self._collectors.append(nc)
            print(" Network Collector started")
        except Exception as e:
            print(f"Network collector error: {e}")

        try:
            
            pc = ProcessCollector(
                dispatch          = dispatch,
                machine_info= self.machine_info,
                poll_interval     = 1.5,
                resource_interval = 30.0,
                hash_executables  = True
            )
            add_static_collector(pc)
            pc.start()
            self._collectors.append(pc)
            print("Process Collector started")
        except Exception as e:
            print(f"Process collector error: {e}")


        try:
            uc = USBCollector(
                dispatch                 = dispatch,
                machine_info= self.machine_info,
                poll_interval            = 3.0,
                scan_on_connect          = True,
                transfer_threshold_bytes = 524288000,
            )
            add_static_collector(uc)
            uc.start()
            self._collectors.append(uc)
            print("USB Collector started")
        except Exception as e:
            print(f"USB collector error: {e}")




        # found = run_detect(dispatch, self.machine_info)

        # Database discovery collector (detects local engines: postgres/mysql/oracle...)
        # dd_cfg = self.config.get("collectors", {}).get("db_discovery", {})
        # if dd_cfg.get("enabled", True):
        #     try:
        #         self._db_inspector = DatabaseInspector(
        #         dispatch=dispatch, machine_info=self.machine_info,
        #         config_file=dd_cfg.get("config_file"),
        #         poll_interval=dd_cfg.get("poll_interval", 300.0),
        #         control_url=os.getenv("DB_CONTROL_URL"),
        #         )
        #         self._db_inspector.set_detected(found)
        #         self._db_inspector.start()          # exits by itself while nothing is ticked
        #         self._collectors.append(self._db_inspector)

        #     except Exception as e:
        #         print(f"Database discovery collector error: {e}")


        # Hard Disk collector
        # hd_cfg = col_cfg.get("harddisk", {})
        # if hd_cfg.get("enabled", True):
        #     try:
        #         hc = HardDiskCollector(
        #             dispatch         = dispatch,
        #             machine_info= self.machine_info,
        #             poll_interval    = hd_cfg.get("poll_interval", 30.0),
        #             smart_interval   = hd_cfg.get("smart_interval", 300.0),
        #             warn_percent     = hd_cfg.get("warn_percent", 85.0),
        #             critical_percent = hd_cfg.get("critical_percent", 95.0),
        #             enable_smart     = hd_cfg.get("enable_smart", True),
        #         )
        #         hc.start()
        #         self._collectors.append(hc)
        #         print("HardDisk Collector started")
        #     except Exception as e:
        #         print(f"HardDisk collector error: {e}")
        self._running = True
        print(f"Agent running.")
        print("Press Ctrl+C to stop.")

    def stop(self):
        print("Stopping collectors...")
        for c in self._collectors:
            try:
                c.stop()
            except Exception:
                pass
        if self._dispatcher:
            self._dispatcher.flush_and_stop()
        print("Sentinel Agent stopped.")

    def wait(self):
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()



def deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result