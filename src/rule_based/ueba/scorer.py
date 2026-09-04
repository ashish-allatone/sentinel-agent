"""
ueba/scorer.py
==============
STAGE 2 (baseline) + STAGE 3 (score).

Baseline  = each entity's normal, learned from a rolling history window (30d).
            numeric behaviours -> mean & std ; categorical -> the SET seen.
Score     = for a new entity-window:
              numeric  -> z = (value - mean) / std      (flag |z| > 3)
              new-value-> value never in the entity's set (flag)
            combined into one deviation score + human-readable reasons.

Everything is per-ENTITY: alice is compared to alice, host-7 to host-7.
"""
import math
from typing import Dict, Any, List

Z_THRESHOLD = 3.0          # how many std devs = anomalous
MIN_SAMPLES = 5            # need this many history windows before we trust a baseline
NEW_VALUE_WEIGHT = 4.0     # score bump for a never-seen value (host/country/etc.)


# ── STAGE 2: build baselines from history ───────────────────────────────────
def build_numeric_baseline(hist_df, entity_col: str, numeric_cols: List[str]) -> Dict[str, Dict]:
    """mean/std/count per entity per numeric behaviour, from the history frame."""
    base = {}
    g = hist_df.groupby(entity_col)
    for ent, sub in g:
        stats = {}
        for c in numeric_cols:
            vals = sub[c].dropna()
            stats[c] = {"mean": float(vals.mean()) if len(vals) else 0.0,
                        "std": float(vals.std(ddof=0)) if len(vals) > 1 else 0.0,
                        "n": int(len(vals))}
        base[ent] = stats
    return base


def build_set_baseline(hist_df, entity_col: str, list_col: str) -> Dict[Any, set]:
    """the set of values (e.g. hosts) each entity has ever shown."""
    seen = {}
    for _, row in hist_df.iterrows():
        ent = row[entity_col]
        vals = row.get(list_col) or []
        s = seen.setdefault(ent, set())
        for v in vals:
            if v is not None:
                s.add(v)
    return seen


# ── STAGE 3: score a new window against the baseline ────────────────────────
def _z(value, mean, std):
    if std and std > 0:
        return (float(value) - mean) / std
    # no variance in history: any nonzero difference is notable, but don't div0
    return 0.0 if (value == mean) else (NEW_VALUE_WEIGHT if value > mean else 0.0)


def score_row(row, entity_col, numeric_cols, num_base,
              set_base=None, set_value_col=None,
              usual_hours_base=None) -> Dict[str, Any]:
    ent = row[entity_col]
    reasons, zmax = [], 0.0
    stats = num_base.get(ent)

    # cold start: not enough history to judge this entity yet
    if not stats or all(stats[c]["n"] < MIN_SAMPLES for c in numeric_cols):
        return {"entity": ent, "window": str(row.get("window")),
                "score": 0.0, "cold_start": True, "reasons": ["insufficient history"]}

    for c in numeric_cols:
        st = stats.get(c, {})
        if st.get("n", 0) < MIN_SAMPLES:
            continue
        z = _z(row.get(c, 0) or 0, st["mean"], st["std"])
        if abs(z) >= Z_THRESHOLD:
            reasons.append(f"{c}={row.get(c)} (z={z:.1f}, usual≈{st['mean']:.1f})")
        zmax = max(zmax, abs(z))

    score = zmax

    # new-value behaviour (e.g. a host this user never used)
    if set_base is not None and set_value_col:
        known = set_base.get(ent, set())
        for v in (row.get(set_value_col) or []):
            if v is not None and v not in known:
                reasons.append(f"new {set_value_col[:-1] if set_value_col.endswith('s') else set_value_col}: {v}")
                score += NEW_VALUE_WEIGHT

    # unusual-hour behaviour
    if usual_hours_base is not None:
        usual = usual_hours_base.get(ent)
        h = row.get("first_hour")
        if usual and h is not None and h not in usual:
            reasons.append(f"active at hour {int(h)} (unusual for this entity)")
            score += 2.0

    return {"entity": ent, "window": str(row.get("window")),
            "score": round(float(score), 2), "cold_start": False,
            "reasons": reasons or ["within normal range"]}


def usual_hours(hist_df, entity_col, hour_col="first_hour") -> Dict[Any, set]:
    out = {}
    for ent, sub in hist_df.groupby(entity_col):
        out[ent] = set(int(h) for h in sub[hour_col].dropna().tolist())
    return out
