"""
python detector.py --dsn "postgresql://developer:password@141.148.220.11:5432/developmentdb" --mode both  --train-lookback "360 days" --score-lookback "360 days"
"""

import argparse
import os
import pickle
from datetime import datetime, timezone

import numpy as np
import psycopg2
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from features import load_features, FEATURE_SETS

MODEL_DIR = os.getenv("ML_MODEL_DIR", "./models")


# ── helpers ─────────────────────────────────────────
def _matrix(rows, feature_names):
    """rows (list of dicts) -> float matrix in stable column order."""
    X = np.array([[float(r[f] if r[f] is not None else 0.0)
                   for f in feature_names] for r in rows], dtype=float)
    return X


def _model_path(table):
    return os.path.join(MODEL_DIR, f"iforest_{table}.pkl")


# ── training ────────────────────────────────────────
def train(conn, table, lookback="30 days", contamination=0.02):
    """Fit an Isolation Forest on historical windows for one table."""
  
    rows, feats = load_features(conn, table, lookback)
    if len(rows) < 50:
        return {"table": table, "trained": False,
                "reason": f"only {len(rows)} windows — need >= 50 to train"}

    X = _matrix(rows, feats)

    # RobustScaler: security features are heavy-tailed (a few huge byte counts),
    # and it uses medians/IQR so those outliers don't distort the scaling.
    scaler = RobustScaler().fit(X)
    Xs = scaler.transform(X)

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,   # expected fraction of anomalies
        random_state=42,
        n_jobs=-1,
    ).fit(Xs)
    anomaly_score = model.fit_predict(X)          # -1 = anomaly, 1 = normal
    print(anomaly_score)
    anomalous = model.score_samples(X)                # lower = more anomalous
    print(anomalous)

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(_model_path(table), "wb") as fh:
        pickle.dump({"model": model, "scaler": scaler, "features": feats,
                     "trained_at": datetime.now(timezone.utc).isoformat(),
                     "train_windows": len(rows)}, fh)

    return {"table": table, "trained": True, "windows": len(rows),
            "features": len(feats)}


# ── scoring + write-back ────────────────────────────
def score(conn, table, lookback="24 hours", threshold=None, write=True):
    """Score recent windows and update risk_score / anomaly on source rows."""
    if not os.path.isfile(_model_path(table)):
        return {"table": table, "scored": False, "reason": "no model — train first"}

    with open(_model_path(table), "rb") as fh:
        bundle = pickle.load(fh)
    model, scaler, feats = bundle["model"], bundle["scaler"], bundle["features"]

    rows, _ = load_features(conn, table, lookback)
    if not rows:
        return {"table": table, "scored": 0, "anomalies": 0}
    # print(rows)
    X = scaler.transform(_matrix(rows, feats))

    # decision_function: higher = more normal. Flip + map to 0-100 risk.
    raw = model.decision_function(X)
    preds = model.predict(X)                      # -1 anomaly, 1 normal
    risk = _to_risk(raw)
    # print(risk)
    # print(preds)
    entity_col = FEATURE_SETS[table]["entity_col"]
    anomalies, updated = 0, 0
    for r, rk, pred in zip(rows, risk, preds):
        is_anom = bool(pred == -1)
        if is_anom:
            anomalies += 1
        if write:
            updated += _write_back(conn, table, entity_col,
                                   r["entity"], r["window_start"], rk, is_anom)

    return {"table": table, "scored": len(rows), "anomalies": anomalies,
            "rows_updated": updated,
            "top": _top_anomalies(rows, risk, preds, feats)}


def _to_risk(raw_scores):
    """Map decision_function output to 0-100 (higher = riskier)."""
    inv = -raw_scores
    lo, hi = inv.min(), inv.max()
    if hi - lo < 1e-9:
        return np.full_like(inv, 10.0)
    return np.round((inv - lo) / (hi - lo) * 100, 1)


def _write_back(conn, table, entity_col, entity, window_start, risk, is_anom):
    """Set risk_score / anomaly on every event in this entity+hour window.

    Writes to the columns already defined on your event tables, so anything
    querying the table (or the console) sees the verdict with no new schema.
    """
    # print(window_start)
    # print(risk)
    # print(entity)
    # print(is_anom)
    sql = f"""
        UPDATE {table}
           SET risk_score = %(risk)s, anomaly = %(anom)s
         WHERE {entity_col} = %(entity)s
           AND date_bin('30 seconds', timestamp, timestamp '2000-01-01') = %(win)s
    """
    # print(sql)
    with conn.cursor() as cur:
        cur.execute(sql, {"risk": float(risk), "anom": is_anom,
                          "entity": entity, "win": window_start})
        return cur.rowcount


def _top_anomalies(rows, risk, preds, feats, n=5):
    """Return the highest-risk windows with WHY (which features stood out)."""
    idx = np.argsort(risk)[::-1][:n]
    out = []
    for i in idx:
        if preds[i] != -1:
            continue
        r = rows[i]
        # crude "why": features furthest above their column median
        contrib = sorted(feats, key=lambda f: -(r.get(f) or 0))[:3]
        out.append({"entity": r["entity"],
                    "window": str(r["window_start"]),
                    "risk": float(risk[i]),
                    "notable": {f: r.get(f) for f in contrib}})
    return out  


# ── cli ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--mode", choices=["train", "score", "both"], default="both")
    ap.add_argument("--tables", nargs="*",
                    default=["auth_events", "network_events",
                             "process_events", "file_events"])
    ap.add_argument("--train-lookback", default="30 days")
    ap.add_argument("--score-lookback", default="1 hours")
    ap.add_argument("--contamination", type=float, default=0.02)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = True

    for table in args.tables:
        if args.mode in ("train", "both"):
            print("[train]", train(conn, table, args.train_lookback, args.contamination))
        if args.mode in ("score", "both"):
            res = score(conn, table, args.score_lookback, write=not args.no_write)
            print("[score]", {k: v for k, v in res.items() if k != "top"})
            for t in res.get("top", []):
                print("         ANOMALY", t["entity"], "risk", t["risk"], t["notable"])
    conn.close()


if __name__ == "__main__":
    main()
