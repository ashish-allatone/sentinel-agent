import os
import sys
import json


#####################################
#                                   #
#          BASE DIRECTORY           #
#                                   #
#####################################

def _base_dir():
    """Folder of the exe (PyInstaller) or of this file (normal Python)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()


#####################################
#                                   #
#       LOGGING CONFIGURATIONS      #
#                                   #
#####################################

# Was "/logs", which on Windows means C:\logs (root of the drive)
JSON_LOGS_DIR = os.path.join(BASE_DIR, "logs")
JSON_LOGS_BASENAME = "logs-of"


######################################
#                                    #
#         AGENT CONFIGURATIONS       #
#                                    #
######################################

DEFAULT_CONFIG = {

    "output": {
        "stdout": False,
    },

    "collectors": {
        "file": {
            "enabled":     True,
            "watch_paths": [
                r"C:\Users\hp\Desktop",
                r"C:\Users\hp\Documents",
                r"C:\Users\hp\Downloads",
            ],
            "ignore_dirs": [
                "AppData", "Temp", "$Recycle.Bin", "node_modules", ".git",
                BASE_DIR,               # never watch the agent's own logs/cache
            ],
            "recursive":   True,
            "use_polling": False,
        },
        "auth": {
            "enabled":       True,
            "log_path":      None,      # None = auto-detect
            "parse_history": False,
            "poll_interval": 5,         # Windows only
        },
        "network": {
            "enabled":         True,
            "poll_interval":   2.0,     # Time in Seconds
            "track_bandwidth": True,
        },
        "process": {
            "enabled":           True,
            "poll_interval":     1.5,   # Time in Seconds
            "resource_interval": 30.0,
            "hash_executables":  True,
        },
        "usb": {
            "enabled":                  True,
            "poll_interval":            3.0,
            "scan_on_connect":          True,
            "transfer_threshold_bytes": 524288000,
        },
        "harddisk": {
            "enabled":          True,
            "poll_interval":    30.0,
            "smart_interval":   300.0,
            "warn_percent":     85.0,
            "critical_percent": 95.0,
            "enable_smart":     True,
        },
    },

    "filters": {
        "min_severity": "info",         # info | low | medium | warning | high | critical
        "exclude_categories": [],
        "exclude_actions": [],
    },
}

# "warning" moved below "high" (it was ranked above it before)
SEVERITY_ORDER = ["info", "low", "medium", "warning", "high", "critical"]
SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_ORDER)}


######################################
#                                    #
#   OPTIONAL OVERRIDE: config.json   #
#                                    #
######################################
# If a config.json exists next to the exe, its values replace the defaults.
# This lets you change settings later WITHOUT rebuilding the exe.

def _deep_merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


_override_path = os.path.join(BASE_DIR, "config.json")
if os.path.exists(_override_path):
    try:
        with open(_override_path, encoding="utf-8-sig") as f:
            _deep_merge(DEFAULT_CONFIG, json.load(f))
        print(f"[config] Loaded overrides from {_override_path}")
    except Exception as e:
        print(f"[config] Could not read {_override_path}: {e!r} - using defaults")