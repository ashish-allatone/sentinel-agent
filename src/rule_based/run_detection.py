"""
run_detection.py  — run Sigma WITHOUT the API
──────────────────────────────────────────────
Place this in src/ and run it directly. It opens its own async DB session via
the project's db.db, so the FastAPI app does NOT need to be running.

    cd src
    python run_detection.py                       # default: last 24 hours
    python run_detection.py --lookback "1 hour"
    python run_detection.py --rules /path/to/sigma/rules/windows/process_creation

Needs the same env the app uses (DB_USER, DB_PASSWORD, DB_ENDPOINT, DB_NAME) —
they are read from .env by db.db, same as normal.
"""

import argparse
import asyncio

from sigma_rules.sigma_runner import run_sigma_standalone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", default="24 hours",
                    help="how far back to scan, e.g. '1 hour', '7 days'")
    ap.add_argument("--rules", default=None,
                    help="path to a Sigma rules dir (defaults to "
                         "detection/sigma/enabled)")
    args = ap.parse_args()

    summary = asyncio.run(run_sigma_standalone(rules_path=args.rules,
                                               lookback=args.lookback))
    print(summary)


if __name__ == "__main__":
    main()
