"""
Find noisy / ambiguous patterns in each station table. Trains in seconds on a laptop.

Two simple, explainable layers:

  1. Rule checks — the data contradicts itself:
       result OK but a value is out of spec      | NOK with everything in spec and no code
       cycle without VIN | same VIN twice        | VIN never seen at the upstream station
       sensor stuck on one value | value missing | fault logged with no fault code

  2. Isolation Forest (+ a 6-sigma guard) — learns the station's normal pattern from clean cycles
     (primary level, how secondary follows primary, cycle time, rolling baseline)
     and scores every cycle 0..1. Catches in-spec-but-wrong cases no rule sees:
     torque/angle mismatch, slow drift, slow cycles with nothing logged.

Parts that were correctly rejected (out of spec AND NOK with a code) are real
defects the line already caught — not ambiguity — so they are not flagged.
Slow cycles the operator's own log explains (andon pulled, or 3+ retries) are
left to the people model (train_people_model.py).

Results are written back into the same station table:
    anomaly_score   0..1 model score (1.0 = hard rule violation)
    anomaly_flag    1 = needs a look
    anomaly_type    label_conflict | traceability | sensor | unlogged_event | pattern
    anomaly_reason  plain-language why

    python train_model.py                     # every station table in the DB
    python train_model.py --station st012
    python train_model.py --contamination 0.01
"""
from __future__ import annotations

import argparse
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from station_db import LABELS_PATH, MODELS_DIR, STATIONS, connect, station_tables

FEATURES = ["primary_z", "pattern_resid_z", "cycle_time_z", "drift_z"]
Z_CAP = 10           # clip features so one huge outlier doesn't hide the moderate ones
Z_GUARD = 6          # any feature this many robust-sigmas from normal is flagged, whatever the forest says
ROLL = 41            # cycles in the rolling baseline window
STUCK_MIN_RUN = 4    # identical readings in a row before we call a sensor stuck


def robust_z(x, center, scale):
    return (x - center) / (scale + 1e-9)


def mad(x) -> float:
    x = pd.Series(x).dropna()
    return float(1.4826 * (x - x.median()).abs().median())


# ---------------------------------------------------------------------------
# 1. Rules
# ---------------------------------------------------------------------------
def rule_checks(df: pd.DataFrame, upstream_vins: set | None, upstream_name: str | None) -> pd.Series:
    """Return a Series of [(anomaly_type, reason), ...], one list per row."""
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index)
    cyc = df.event_type == "CYCLE"

    def add(mask, kind, text):
        for ix in df.index[mask]:
            reasons[ix].append((kind, text(df.loc[ix]) if callable(text) else text))

    for sig in ("primary", "secondary"):
        v, lo, hi = df[f"{sig}_value"], df[f"{sig}_lsl"], df[f"{sig}_usl"]
        out = v.notna() & ((v < lo) | (v > hi))
        add(cyc & (df.result == "OK") & out, "label_conflict",
            lambda r, s=sig: f"result OK but {r[f'{s}_name']} {r[f'{s}_value']:g} {r[f'{s}_unit']} "
                             f"is outside {r[f'{s}_lsl']:g}-{r[f'{s}_usl']:g}")

    in_spec = ((df.primary_value.between(df.primary_lsl, df.primary_usl) | df.primary_value.isna())
               & df.secondary_value.between(df.secondary_lsl, df.secondary_usl))
    add(cyc & (df.result == "NOK") & in_spec & df.fault_code.isna(), "label_conflict",
        "rejected (NOK) but all values in spec and no fault code")

    add(cyc & df.vin.isna(), "traceability", "cycle recorded without VIN")
    dup = cyc & df.vin.notna() & df.vin.duplicated(keep="first")
    first_seen = df[cyc & df.vin.notna()].drop_duplicates("vin").set_index("vin").ts
    add(dup, "traceability", lambda r: f"VIN already recorded here at {first_seen[r.vin]} (double scan?)")

    add(cyc & df.primary_value.isna(), "sensor", lambda r: f"{r.primary_name} missing (sensor dropout)")

    # stuck sensor: the same reading repeated cycle after cycle
    c = df[cyc & df.primary_value.notna()]
    run_id = (c.primary_value != c.primary_value.shift()).cumsum()
    run_len = run_id.map(run_id.value_counts())
    repeat = (run_len >= STUCK_MIN_RUN) & run_id.duplicated(keep="first")
    add(df.index.isin(c.index[repeat]), "sensor",
        lambda r: f"{r.primary_name} stuck at {r.primary_value:g} {r.primary_unit} for several cycles")

    add((df.event_type == "FAULT") & df.fault_code.isna(), "unlogged_event", "fault / downtime logged with no fault code")

    if upstream_vins is not None:
        add(cyc & df.vin.notna() & ~df.vin.isin(upstream_vins), "traceability",
            f"VIN never recorded at upstream station {upstream_name}")
    return reasons


# ---------------------------------------------------------------------------
# 2. Isolation Forest on the station's normal pattern
# ---------------------------------------------------------------------------
def fit_stats(c: pd.DataFrame) -> dict:
    """Describe 'normal' for this station from clean cycles."""
    slope, intercept = np.polyfit(c.primary_value, c.secondary_value, 1)
    resid = c.secondary_value - (intercept + slope * c.primary_value)
    roll = c.primary_value.rolling(ROLL, center=True, min_periods=10).median()
    return {
        "p_med": float(c.primary_value.median()), "p_mad": mad(c.primary_value),
        "slope": float(slope), "intercept": float(intercept), "r_mad": mad(resid),
        "ct_med": float(c.ct_net.median()), "ct_mad": mad(c.ct_net),
        "roll_mad": mad(roll - c.primary_value.median()),
    }


def features(c: pd.DataFrame, st: dict) -> pd.DataFrame:
    prim = c.primary_value.fillna(st["p_med"])
    roll = prim.rolling(ROLL, center=True, min_periods=10).median()
    resid = c.secondary_value - (st["intercept"] + st["slope"] * prim)
    return pd.DataFrame({
        "primary_z": robust_z(prim, st["p_med"], st["p_mad"]),
        "pattern_resid_z": robust_z(resid, 0.0, st["r_mad"]),
        "cycle_time_z": robust_z(c.ct_net, st["ct_med"], st["ct_mad"]),
        "drift_z": robust_z(roll, st["p_med"], st["roll_mad"]),
        "_roll": roll,
    }, index=c.index)


def explain(r: pd.Series, f: pd.Series, st: dict) -> tuple[str, str]:
    """Name the feature that made this cycle unusual."""
    z = f[FEATURES].abs()
    top = z.idxmax()
    p, s, u = r.primary_name, r.secondary_name, r.primary_unit
    if top == "pattern_resid_z":
        exp = st["intercept"] + st["slope"] * r.primary_value
        return "pattern", (f"{s} {r.secondary_value:g} doesn't fit {p} {r.primary_value:g} "
                           f"(expected ~{exp:.3g} {r.secondary_unit}) - in spec, off the normal pattern")
    if top == "cycle_time_z":
        if f.cycle_time_z > 0:
            return "unlogged_event", (f"slow cycle {r.cycle_time_s:g}s (normal ~{st['ct_med']:.0f}s) "
                                      f"with no fault, downtime, andon or retries to explain it")
        return "unlogged_event", f"unusually fast cycle {r.cycle_time_s:g}s (normal ~{st['ct_med']:.0f}s)"
    if top == "drift_z":
        return "pattern", f"{p} drifting from baseline: rolling median {f._roll:.3g} vs normal {st['p_med']:.3g} {u}"
    side = "high" if f.primary_z > 0 else "low"
    return "pattern", f"{p} {r.primary_value:g} {u} unusually {side} (still in spec)"


def process_station(con, table: str, contamination: float, all_vins: dict) -> pd.DataFrame:
    t0 = time.time()
    df = pd.read_sql(f"SELECT * FROM {table} ORDER BY ts, event_id", con)
    upstream = _upstream_of(df, table, all_vins)
    rules = rule_checks(df, all_vins.get(upstream), upstream)

    cyc = df[df.event_type == "CYCLE"]
    out_of_spec = ~(cyc.primary_value.between(cyc.primary_lsl, cyc.primary_usl)
                    & cyc.secondary_value.between(cyc.secondary_lsl, cyc.secondary_usl))
    confirmed_nok = (cyc.result == "NOK") & out_of_spec & cyc.fault_code.notna()
    # time the operator already explained in their own log (people model covers these):
    # logged retries are subtracted from the cycle time, an andon pull explains the whole cycle
    cyc = cyc.assign(ct_net=cyc.cycle_time_s)
    explained = pd.Series(False, index=cyc.index)
    if "andon_pulled" in cyc:
        retry_cost = STATIONS.get(table, {}).get("retry_cost_s", 0.0)
        cyc["ct_net"] = cyc.cycle_time_s - cyc.retries.fillna(0) * retry_cost
        explained = cyc.andon_pulled.fillna(0) == 1

    # train only on cycles that look clean — the model learns "normal"
    clean = cyc[(rules[cyc.index].str.len() == 0) & ~confirmed_nok & (cyc.result == "OK") & ~explained]
    stats = fit_stats(clean)
    F_all = features(cyc, stats)
    X_all = F_all[FEATURES].fillna(0).clip(-Z_CAP, Z_CAP)
    X_train = X_all.loc[clean.index]

    model = IsolationForest(n_estimators=200, max_samples=512, random_state=0)
    model.fit(X_train)
    train_score = -model.score_samples(X_train)
    threshold = float(np.quantile(train_score, 1 - contamination))

    score = pd.Series(-model.score_samples(X_all), index=cyc.index)
    # the forest sees combinations; the guard catches single values far outside anything it
    # trained on (a forest can't score points beyond its training range very high)
    guard = F_all[FEATURES].abs().max(axis=1) >= Z_GUARD
    ml_hit = ((score >= threshold) | guard) & ~confirmed_nok

    # ---- combine and write back ----
    df["anomaly_score"] = score.round(4)
    df["anomaly_flag"] = 0
    df["anomaly_type"] = None
    df["anomaly_reason"] = None
    for ix in df.index:
        reasons = list(rules[ix])
        if not reasons and ml_hit.get(ix, False):
            kind, text = explain(df.loc[ix], F_all.loc[ix], stats)
            if not (kind == "unlogged_event" and explained.get(ix, False)):
                reasons.append((kind, text))
        if rules[ix]:
            df.at[ix, "anomaly_score"] = 1.0
        if reasons:
            df.at[ix, "anomaly_flag"] = 1
            df.at[ix, "anomaly_type"] = reasons[0][0]
            df.at[ix, "anomaly_reason"] = "; ".join(text for _, text in reasons)

    cols = ["anomaly_score", "anomaly_flag", "anomaly_type", "anomaly_reason", "event_id"]
    con.executemany(
        f"UPDATE {table} SET anomaly_score=?, anomaly_flag=?, anomaly_type=?, anomaly_reason=? WHERE event_id=?",
        [(None if pd.isna(a) else float(a), int(b), c, d, int(e))
         for a, b, c, d, e in df[cols].itertuples(index=False)],
    )
    con.commit()

    MODELS_DIR.mkdir(exist_ok=True)
    joblib.dump({"model": model, "stats": stats, "threshold": threshold, "features": FEATURES},
                MODELS_DIR / f"{table}.joblib")

    n_rule = int((rules.str.len() > 0).sum())
    n_ml = int((df.anomaly_flag.astype(bool) & (rules.str.len() == 0)).sum())
    print(f"\n{table}: trained on {len(clean):,} clean cycles in {time.time() - t0:.1f}s  "
          f"-> flagged {n_rule + n_ml} of {len(df):,} rows  ({n_rule} by rules, {n_ml} by model)")
    return df


def _upstream_of(df, table, all_vins):
    """Upstream station from the config in station_db.py (st013 -> st012)."""
    up = STATIONS.get(table, {}).get("upstream")
    return up if up in all_vins else None


def evaluate(table: str, df: pd.DataFrame) -> None:
    """Only works on generated data: compare flags with what was injected."""
    if not LABELS_PATH.exists():
        return
    lab = pd.read_csv(LABELS_PATH)
    lab = lab[lab.station == table]
    if lab.empty:
        return
    injected = set(lab.event_id)
    flagged = set(df.event_id[df.anomaly_flag == 1])
    hit = injected & flagged
    precision = len(hit) / max(len(flagged), 1)
    recall = len(hit) / max(len(injected), 1)
    print(f"  vs injected ground truth: precision {precision:.0%}  recall {recall:.0%}  "
          f"({len(flagged - injected)} flagged rows were not injected - natural outliers)")
    lab = lab.assign(found=lab.event_id.isin(flagged))
    per = lab.groupby("pattern").found.agg(["sum", "count"])
    for pat, r in per.iterrows():
        print(f"    {pat:<22} {int(r['sum']):>4}/{int(r['count']):<4} found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", help="e.g. st012 (default: all station tables)")
    ap.add_argument("--contamination", type=float, default=0.015,
                    help="share of clean-looking cycles the model may call unusual")
    args = ap.parse_args()

    con = connect()
    tables = station_tables(con)
    all_vins = {t: set(pd.read_sql(f"SELECT vin FROM {t} WHERE vin IS NOT NULL", con).vin) for t in tables}
    for t in ([args.station] if args.station else tables):
        df = process_station(con, t, args.contamination, all_vins)
        evaluate(t, df)
        print("  what was flagged (one example each):")
        for kind, g in df[df.anomaly_flag == 1].groupby("anomaly_type"):
            ex = g.sort_values("anomaly_score", ascending=False).iloc[0]
            print(f"    {kind:<15} {len(g):>4}   e.g. #{ex.event_id} {ex.ts}  {ex.anomaly_reason}")
    con.close()


if __name__ == "__main__":
    main()
