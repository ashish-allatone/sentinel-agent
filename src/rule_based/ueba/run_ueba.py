"""
ueba/run_ueba.py
================
The scheduled job. Run hourly (cron / APScheduler / a loop).

  python -m ueba.run_ueba

Flow:
  1. pull the last hour's entity-window features   (features.py)
  2. pull 30 days of history for baselines         (features.py, hours=720)
  3. build baselines + score the new windows       (scorer.py)
  4. write ueba_alerts back to Postgres            (so the dashboard/correlation
                                                    engine can read the scores)

This reads only what your consumer already stored. It does NOT touch collectors.
"""
import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

import features as F
import scorer as S

load_dotenv()

DB_URL = (f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
          f"@{os.environ['DB_ENDPOINT']}:5432/{os.environ['DB_NAME']}")

ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS ueba_alerts (
    id          BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,          -- 'host' | 'user'
    entity      TEXT NOT NULL, 
    time_window      TIMESTAMPTZ,
    score       DOUBLE PRECISION,
    reasons     JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ueba_entity ON ueba_alerts (entity_type, entity);
CREATE INDEX IF NOT EXISTS ix_ueba_score  ON ueba_alerts (score DESC);
"""

ALERT_THRESHOLD = 3.0        # only persist windows at/above this deviation


def _write_alerts(engine, entity_type, scored):
    import json
    print(scored)
    rows = [r for r in scored if not r.get("cold_start") and r["score"] >= ALERT_THRESHOLD]
    if not rows:
        return 0
    with engine.begin() as conn:
        for r in rows:
            print(r)
            conn.execute(text(
                "INSERT INTO ueba_alerts (entity_type, entity, time_window, score, reasons) "
                "VALUES (:t, :e, :w, :s, :r)"),
                {"t": entity_type, "e": str(r["entity"]), "w": r["window"],
                 "s": r["score"], "r": json.dumps(r["reasons"])})
    return len(rows)


def run():
    # print(DB_URL)
    engine = create_engine(DB_URL)
    # print(engine)
    print("last 30d host rows:", len(F.host_features(engine, hours=720)))

    # print("30d host rows:",    len(F.host_features(engine, hours=720)))
    # print("30d user rows:",    len(F.user_features(engine, hours=720)))
    with engine.begin() as conn:
        for stmt in ALERTS_DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        # print("done")

    # ---- HOST entity ----
    now_h = F.host_features(engine, hours=720)          # windows to score
    hist_h = F.host_features(engine, hours=1440)        # 30d history for baselines
    if not hist_h.empty:
        num_base = S.build_numeric_baseline(hist_h, "agent_name", F.HOST_NUMERIC)
        scored = [S.score_row(r, "agent_name", F.HOST_NUMERIC, num_base)
                  for _, r in now_h.iterrows()]
        n = _write_alerts(engine, "host", scored)
        print(f"[ueba] host: scored {len(scored)} windows, {n} alerts")

    # ---- USER entity ----
    now_u = F.user_features(engine, hours=720)
    hist_u = F.user_features(engine, hours=1440)
    if not hist_u.empty:
        num_base = S.build_numeric_baseline(hist_u, "username", F.USER_NUMERIC)
        host_sets = S.build_set_baseline(hist_u, "username", "hosts_seen")
        hours_base = S.usual_hours(hist_u, "username", "first_hour")
        scored = [S.score_row(r, "username", F.USER_NUMERIC, num_base,
                              set_base=host_sets, set_value_col="hosts_seen",
                              usual_hours_base=hours_base)
                  for _, r in now_u.iterrows()]
        n = _write_alerts(engine, "user", scored)
        print(f"[ueba] user: scored {len(scored)} windows, {n} alerts")

    print(f"[ueba] done at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    run()
