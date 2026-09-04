"""
correlation_engine.py
═════════════════════
The box after your three detectors. Reads all three signals, correlates them
BY ENTITY within a time window, produces one risk score per entity, and raises
incidents — with an agreement boost so that "two/three detectors agree on the
same entity" outranks any single loud detector.

Inputs (already produced by your existing files):
  • Sigma      -> security_alerts (rule_id, severity, agent_name, entity, last_seen, technique)
  • IForest    -> risk_score / anomaly columns on the event tables (per entity+window)
  • UEBA       -> ueba_alerts (entity_type, entity, window, score, reasons)

Outputs:
  • entity_risk    one combined score per entity per window (the "Risk scoring" box)
  • incidents      prioritized, deduped, with contributing signals (the "Incident" box)
python correlation_engine.py --dsn "postgresql://user:pass@host/db" --lookback "24 hours"
Run it right after the three detectors on each cycle (hourly), same DSN.

python correlation_engine.py --dsn "postgresql://dbmasteruser:dbmasterpassword@80.225.239.163/testdb" --lookback "30 days"

"""
import argparse
import json
from datetime import datetime, timezone, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor

# ── how much each detector contributes, and the agreement multiplier ────────
WEIGHTS = {"sigma": 0.45, "iforest": 0.30, "ueba": 0.25}   # sum = 1.0
# a single detector alone is capped; agreement lifts the score
AGREEMENT_BOOST = {1: 1.0, 2: 1.35, 3: 1.7}                 # ×score by # of detectors
# a lone detector cannot exceed this on its own — correlation (agreement) is what
# earns incident-level risk. Prevents one loud signal from dominating.
SINGLE_DETECTOR_CAP = 55.0
INCIDENT_THRESHOLD = 60.0                                   # raise incident at/above

DDL = """
CREATE TABLE IF NOT EXISTS entity_risk (
    id          BIGSERIAL PRIMARY KEY,
    entity      TEXT NOT NULL,
    entity_type TEXT,
    time_window      TIMESTAMPTZ,
    risk_score  DOUBLE PRECISION,
    detectors   JSONB,          -- which fired + their sub-scores
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (entity, time_window)
);
CREATE INDEX IF NOT EXISTS ix_er_score ON entity_risk (risk_score DESC);

CREATE TABLE IF NOT EXISTS incidents (
    id           BIGSERIAL PRIMARY KEY,
    entity       TEXT NOT NULL,
    entity_type  TEXT,
    risk_score   DOUBLE PRECISION,
    detector_count INT,
    signals      JSONB,         -- the evidence: sigma rules, ueba reasons, iforest score
    techniques   JSONB,         -- MITRE from sigma
    status       TEXT DEFAULT 'open',
    first_seen   TIMESTAMPTZ,
    last_seen    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (entity, first_seen)
);
CREATE INDEX IF NOT EXISTS ix_inc_score ON incidents (risk_score DESC);
CREATE INDEX IF NOT EXISTS ix_inc_status ON incidents (status);
"""


# ── pull each detector's recent signals, keyed by entity ────────────────────
def _sigma_signals(cur, lookback):
    cur.execute("""
        SELECT entity, agent_name,
               max(severity)                         AS max_sev,
               count(*)                              AS rule_hits,
               array_agg(DISTINCT rule_id)           AS rules,
               array_agg(DISTINCT technique) FILTER (WHERE technique <> '') AS techniques,
               min(first_seen) AS first_seen, max(last_seen) AS last_seen
          FROM security_alerts
         WHERE last_seen >= now() - %s::interval
         GROUP BY entity, agent_name
    """, (lookback,))
    out = {}
    for r in cur.fetchall():
        ent = r["entity"] or str(r["agent_name"])
        # sigma severity is 1..5 (critical). map to 0..100 sub-score.
        sub = min(100.0, (r["max_sev"] or 1) / 5.0 * 100)
        out[ent] = {"score": sub, "rules": r["rules"], "hits": r["rule_hits"],
                    "techniques": r["techniques"] or [],
                    "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
    # print("sigma",out)
    return out


def _ueba_signals(cur, lookback):
    cur.execute("""
        SELECT entity, entity_type,
               max(score)                 AS max_score,
               array_agg(reasons)         AS reasons,
               min(time_window) AS first_seen, max(time_window) AS last_seen
          FROM ueba_alerts
         WHERE created_at >= now() - %s::interval
         GROUP BY entity, entity_type
    """, (lookback,))
    out = {}
    for r in cur.fetchall():
        # ueba score is an unbounded z-ish number; squash to 0..100 (z=10 -> ~100)
        sub = min(100.0, (r["max_score"] or 0) * 10.0)
        reasons = []
        for grp in (r["reasons"] or []):
            if isinstance(grp, list):
                reasons.extend(grp)
            elif grp:
                reasons.append(grp)
        out[r["entity"]] = {"score": sub, "entity_type": r["entity_type"],
                            "reasons": reasons[:8],
                            "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
        # print("ueba",out)
    return out


def _iforest_signals(cur, lookback, tables):
    """IForest wrote risk_score/anomaly onto the event rows. Roll up per entity."""
    out = {}
    entity_cols = {"auth_events": "username", "network_events": "agent_name",
                   "process_events": "agent_name", "file_events": "agent_name"}
    for t in tables:
        ecol = entity_cols.get(t, "agent_name")
        try:
            cur.execute(f"""
                SELECT {ecol}::text AS entity,
                       max(risk_score) AS max_risk,
                       bool_or(anomaly) AS any_anom,
                       min(timestamp) AS first_seen, max(timestamp) AS last_seen
                  FROM {t}
                 WHERE timestamp >= now() - %s::interval
                   AND risk_score IS NOT NULL
                 GROUP BY {ecol}
            """, (lookback,))
        except Exception:
            continue
        for r in cur.fetchall():
            ent = r["entity"]
            if ent is None:
                continue
            prev = out.get(ent, {"score": 0.0})
            if (r["max_risk"] or 0) > prev["score"]:
                out[ent] = {"score": float(r["max_risk"] or 0),
                            "anomaly": bool(r["any_anom"]),
                            "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
        # print("forest",out)
    return out


# ── the correlation itself ──────────────────────────────────────────────────
def correlate(sigma, ueba, iforest):
    """Union all entities; for each, combine the sub-scores it has, boost by
    how many detectors agree."""
    entities = set(sigma) | set(ueba) | set(iforest)
    results = []
    for ent in entities:
        parts, fired, times, techs, evidence = {}, 0, [], [], {}
        if ent in sigma:
            parts["sigma"] = sigma[ent]["score"]; fired += 1
            techs += sigma[ent]["techniques"]
            evidence["sigma_rules"] = sigma[ent]["rules"]
            times += [sigma[ent]["first_seen"], sigma[ent]["last_seen"]]
        if ent in iforest:
            parts["iforest"] = iforest[ent]["score"]; fired += 1
            evidence["iforest_risk"] = iforest[ent]["score"]
            times += [iforest[ent]["first_seen"], iforest[ent]["last_seen"]]
        if ent in ueba:
            parts["ueba"] = ueba[ent]["score"]; fired += 1
            evidence["ueba_reasons"] = ueba[ent]["reasons"]
            times += [ueba[ent]["first_seen"], ueba[ent]["last_seen"]]

        # weighted sum of whatever fired, then agreement boost
        base = sum(parts[d] * WEIGHTS[d] for d in parts)
        # renormalize by the weight actually present so a lone detector isn't unfairly tiny
        wsum = sum(WEIGHTS[d] for d in parts)
        base = base / wsum if wsum else 0.0
        score = min(100.0, base * AGREEMENT_BOOST.get(fired, 1.0))
        # one detector alone can't reach incident level — agreement must corroborate
        if fired <= 1:
            score = min(score, SINGLE_DETECTOR_CAP)

        etype = ueba.get(ent, {}).get("entity_type") or \
            ("user" if ent and not str(ent).isdigit() else "host")
        times = [t for t in times if t]
        results.append({
            "entity": ent, "entity_type": etype,
            "risk_score": round(score, 1), "detector_count": fired,
            "detectors": parts, "signals": evidence,
            "techniques": sorted(set(techs)),
            "first_seen": min(times) if times else None,
            "last_seen": max(times) if times else None,
        })
    results.sort(key=lambda r: r["risk_score"], reverse=True)
    return results


def _persist(conn, results):
    with conn.cursor() as cur:
        for r in results:
            # print("check",r)
            try:
                cur.execute("""
                    INSERT INTO entity_risk (entity, entity_type, time_window, risk_score, detectors)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (entity, time_window) DO UPDATE
                    SET risk_score = EXCLUDED.risk_score, detectors = EXCLUDED.detectors
                """, (r["entity"], r["entity_type"], r["last_seen"], r["risk_score"],
                    json.dumps(r["detectors"])))
            except Exception as e:
                print(f"Error inserting row for {r['entity']}: {e}")
                conn.rollback() # <--- CRITICAL: Resets the transaction block so the loop can continue




            # cur.execute("""
            #     INSERT INTO entity_risk (entity, entity_type, time_window, risk_score, detectors)
            #     VALUES (%s,%s,%s,%s,%s)
            #     ON CONFLICT (entity, time_window) DO UPDATE
            #       SET risk_score = EXCLUDED.risk_score, detectors = EXCLUDED.detectors
            # """, (r["entity"], r["entity_type"], r["last_seen"], r["risk_score"],
            #       json.dumps(r["detectors"])))
            if r["risk_score"] >= INCIDENT_THRESHOLD:
                cur.execute("""
                    INSERT INTO incidents
                      (entity, entity_type, risk_score, detector_count, signals,
                       techniques, first_seen, last_seen)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (entity, first_seen) DO UPDATE
                      SET risk_score = EXCLUDED.risk_score,
                          detector_count = EXCLUDED.detector_count,
                          signals = EXCLUDED.signals,
                          last_seen = EXCLUDED.last_seen
                """, (r["entity"], r["entity_type"], r["risk_score"], r["detector_count"],
                      json.dumps(r["signals"]), json.dumps(r["techniques"]),
                      r["first_seen"], r["last_seen"]))
    conn.commit()


def run(dsn, lookback="24 hours",
        tables=("auth_events", "network_events", "process_events", "file_events")):
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    with conn.cursor() as cur:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                cur.execute(stmt)
    conn.commit()

    with conn.cursor() as cur:
        sigma = _sigma_signals(cur, lookback)
        ueba = _ueba_signals(cur, lookback)
        iforest = _iforest_signals(cur, lookback, tables)

    results = correlate(sigma, ueba, iforest)
    _persist(conn, results)

    incidents = [r for r in results if r["risk_score"] >= INCIDENT_THRESHOLD]
    print(f"[correlate] entities={len(results)} "
          f"sigma={len(sigma)} iforest={len(iforest)} ueba={len(ueba)} "
          f"incidents={len(incidents)}")
    for r in incidents[:10]:
        print(f"  INCIDENT {r['entity']:>18}  risk={r['risk_score']:5}  "
              f"detectors={r['detector_count']} {list(r['detectors'])}  "
              f"{r['techniques']}")
    conn.close()
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--lookback", default="24 hours")
    ap.parse_args() and run(**vars(ap.parse_args()))
