"""
People model — finds unusual patterns in the HUMAN side of the line and sums up
every operator's shift. Built to support people, not to rank them: each finding
is phrased as something to check or help with (a skipped step, a missing team
lead, fatigue, a tool that needs re-hits). Trains in about a second on a laptop.

Level 1 - every cycle   (written into st012 / st013: human_score/flag/type/reason)
  rules    support       andon pulled, team lead took more than 3 min
           login         operator logged in at two stations at once (stale login)
           working_time  operator working outside their crew's shift (+ rest time)
  model    Isolation Forest per station on the operator's hands-on time vs their
           OWN normal and vs the station standard, plus retries (+ a 6-sigma guard):
           rushed        far faster than they normally are  -> possible skipped step
           struggle      far slower, no andon pulled         -> needed help, didn't ask
           retries       many re-hits in one cycle           -> tool / part fit / technique

Level 2 - every operator shift   (table operator_shifts, one row per operator per shift)
  peer model: robust z-score of each operator-shift against all others on
           pace (vs others on the same station and shift, so station-wide changes cancel out),
           slowdown over the shift (fatigue), retry rate, NOK rate and
           technique offset (does the machine signature of their work differ, e.g. angle
           for the same torque at ST012), plus counts from level 1.

    python train_people_model.py
    python train_people_model.py --contamination 0.01
"""
from __future__ import annotations

import argparse
import time
from datetime import timedelta

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from station_db import (MODELS_DIR, OPERATOR_SHIFTS_SQL, PEOPLE_LABELS_PATH, STATIONS, connect,
                        station_tables)

CYCLE_FEATURES = ["self_z", "standard_z", "retries"]
SHIFT_START = {"A": 6, "B": 14, "C": 22}
ANDON_SLA_S = 180          # team lead should arrive within 3 minutes
MIN_REST_H = 11            # minimum rest between shifts (German ArbZG)
LOGIN_OVERLAP_S = 120      # two stations at once for longer than this = login problem
Z_GUARD, Z_CAP = 6, 10
PEER_Z = 4                 # operator-shift is unusual vs peers beyond this


def mad(x) -> float:
    x = pd.Series(x).dropna()
    return float(1.4826 * (x - x.median()).abs().median()) if len(x) else np.nan


def robust_z(x, center, scale):
    return (x - center) / (scale + 1e-9)


def add_shift_clock(c: pd.DataFrame) -> pd.DataFrame:
    """shift_date (the day the shift started) and hours into the shift."""
    start = c.ts.dt.normalize() + pd.to_timedelta(c["shift"].map(SHIFT_START), unit="h")
    start = start.where(start <= c.ts, start - pd.Timedelta(days=1))
    return c.assign(shift_start=start, shift_date=start.dt.strftime("%Y-%m-%d"),
                    hours_in=(c.ts - start).dt.total_seconds() / 3600)


# ---------------------------------------------------------------------------
# Level 1a - rules across stations
# ---------------------------------------------------------------------------
def login_conflicts(cyc: pd.DataFrame) -> dict:
    """Same operator logged in at two stations at the same time. The session that
    started earlier is the stale one (they moved on without logging out)."""
    out = {}
    c = cyc.sort_values("ts")
    c = c.assign(end=c.ts + pd.to_timedelta(c.cycle_time_s, unit="s"))
    new = (c.groupby(["operator_id", "station"]).ts.diff() > pd.Timedelta(minutes=15)).astype(int)
    c = c.assign(session=new.groupby([c.operator_id, c.station]).cumsum())
    sess = c.groupby(["operator_id", "station", "session"]).agg(start=("ts", "min"), end=("end", "max")).reset_index()
    for op, g in sess.groupby("operator_id"):
        rows = g.to_dict("records")
        for a in rows:
            for b in rows:
                if a["station"] >= b["station"]:
                    continue
                lo, hi = max(a["start"], b["start"]), min(a["end"], b["end"])
                if (hi - lo).total_seconds() < LOGIN_OVERLAP_S:
                    continue
                stale, other = (a, b) if a["start"] < b["start"] else (b, a)
                m = ((c.operator_id == op) & (c.station == stale["station"]) & (c.session == stale["session"])
                     & (c.ts >= lo) & (c.ts <= hi))
                for key in c.loc[m, "key"]:
                    out[key] = (f"{op} is also logged in at {other['station'].upper()} since "
                                f"{other['start']:%H:%M} - this login looks stale (not logged out at rotation?); "
                                f"who did this work?")
    return out


def off_shift_work(cyc: pd.DataFrame, home: pd.Series) -> dict:
    """Operator working on a shift that isn't their crew's, with the rest time before it."""
    out = {}
    off = cyc[cyc["shift"] != cyc.operator_id.map(home)]
    for (op, sdate, sh), g in off.groupby(["operator_id", "shift_date", "shift"]):
        first = g.ts.min()
        own = cyc[(cyc.operator_id == op) & (cyc["shift"] == home[op]) & (cyc.ts < first)]
        rest = None
        if len(own):
            own_end = own.shift_start.max() + pd.Timedelta(hours=8)
            rest = (first - own_end).total_seconds() / 3600
        rest_txt = "" if rest is None else (f" Rest since their shift {home[op]} ended: ~{rest:.1f} h"
                                            + (f" (< {MIN_REST_H} h minimum)" if rest < MIN_REST_H else ""))
        for key in g.key:
            out[key] = (f"{op} (crew {home[op]}) working shift {sh} on {sdate} - covering a gap or a double "
                        f"shift?{rest_txt}")
    return out


# ---------------------------------------------------------------------------
# Level 1b - Isolation Forest on the operator's cycle, per station
# ---------------------------------------------------------------------------
def cycle_model(c: pd.DataFrame, table: str, contamination: float):
    cfg = STATIONS.get(table, {})
    retry_cost = cfg.get("retry_cost_s", 0.0)
    c = c.assign(net_time=c.operator_time_s - c.retries.fillna(0) * retry_cost)
    base = c[c.andon_pulled.fillna(0) == 0]

    st_med, st_mad = base.net_time.median(), mad(base.net_time)
    per_op = base.groupby("operator_id").net_time.agg(["median", mad, "size"])
    per_op.columns = ["med", "mad", "n"]
    per_op.loc[per_op.n < 30, ["med", "mad"]] = [st_med, st_mad]          # too little history
    per_op["mad"] = per_op["mad"].clip(lower=0.5 * st_mad)

    med = c.operator_id.map(per_op.med).fillna(st_med)
    sc = c.operator_id.map(per_op["mad"]).fillna(st_mad)
    F = pd.DataFrame({
        "self_z": robust_z(c.net_time, med, sc),
        "standard_z": robust_z(c.net_time, st_med, st_mad),
        "retries": c.retries.fillna(0).astype(float),
        "_op_med": med,
    }, index=c.index)

    X = F[CYCLE_FEATURES].clip(-Z_CAP, Z_CAP)
    train = X[c.andon_pulled.fillna(0) == 0]
    model = IsolationForest(n_estimators=200, max_samples=512, random_state=0).fit(train)
    train_score = -model.score_samples(train)
    threshold = float(np.quantile(train_score, 1 - contamination))
    score = pd.Series(-model.score_samples(X), index=c.index)
    guard = (F.self_z.abs() >= Z_GUARD) | (F.retries >= 4)
    hit = ((score >= threshold) | guard) & (c.andon_pulled.fillna(0) == 0)

    reasons = {}
    for ix in c.index[hit]:
        r, f = c.loc[ix], F.loc[ix]
        if f.retries >= 3 and f.retries >= abs(f.self_z):
            reasons[ix] = ("retries", f"{int(f.retries)} re-hits/re-tests in one cycle (result {r.result}) - "
                                      f"check part fit, tool or technique")
        elif f.self_z < 0:
            pct = r.net_time / f._op_med
            reasons[ix] = ("rushed", f"hands-on time {r.net_time:.0f}s vs their normal {f._op_med:.0f}s "
                                     f"({pct:.0%}) - possible skipped step, check this vehicle")
        else:
            reasons[ix] = ("struggle", f"hands-on time {r.net_time:.0f}s vs their normal {f._op_med:.0f}s, "
                                       f"no andon pulled - they may have needed help")
    bundle = {"model": model, "threshold": threshold, "per_operator": per_op,
              "station_median": st_med, "station_mad": st_mad, "features": CYCLE_FEATURES}
    # pace vs everyone else on the same station in the same shift: a change that hits the whole
    # station (new work instruction, slow machine) cancels out, one person's pace does not
    shift_med = base.groupby(["shift_date", "shift"]).net_time.median().rename("shift_med")
    ref = c.join(shift_med, on=["shift_date", "shift"])["shift_med"].fillna(st_med)
    return score, reasons, c.net_time / ref, bundle


# ---------------------------------------------------------------------------
# Level 2 - operator shifts vs peers
# ---------------------------------------------------------------------------
def technique_offsets(c: pd.DataFrame) -> pd.Series:
    """How far the secondary signal sits from the station's normal primary->secondary
    line, in sigma. A person's technique can move it (e.g. angle for the same torque)."""
    ok = c[(c.result == "OK") & c.primary_value.notna()]
    slope, icpt = np.polyfit(ok.primary_value, ok.secondary_value, 1)
    resid = c.secondary_value - (icpt + slope * c.primary_value)
    return resid / mad(resid)


def operator_shifts(cyc: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (op, sdate, sh), g in cyc.groupby(["operator_id", "shift_date", "shift"]):
        calm = g[(g.andon_pulled.fillna(0) == 0) & g.human_type.isna()]
        ratio = calm.pace.clip(0.5, 2.0)
        slope = np.nan
        if calm.hours_in.max() - calm.hours_in.min() >= 1.5 and len(calm) > 50:
            slope = np.polyfit(calm.hours_in, ratio, 1)[0] * 8 * 100 / max(ratio.median(), 1e-9)
        offs = g.groupby("station").technique_z.mean()
        worst = offs.abs().idxmax() if len(offs.dropna()) else None
        rows.append({
            "operator_id": op, "shift_date": sdate, "shift": sh,
            "operator_skill": int(g.operator_skill.mode().iloc[0]),
            "stations": ",".join(sorted(g.station.unique())),
            "cycles": len(g), "first_ts": g.ts.min().strftime("%Y-%m-%d %H:%M:%S"),
            "last_ts": g.ts.max().strftime("%Y-%m-%d %H:%M:%S"),
            "pace_ratio": round(float(ratio.median()), 3),
            "slowdown_pct": None if np.isnan(slope) else round(float(slope), 1),
            "retry_rate": round(float(g.retries.mean()), 3),
            "nok_rate": round(float((g.result == "NOK").mean()), 4),
            "technique_offset": None if worst is None else round(float(offs[worst]), 2),
            "technique_station": worst,
            "rushed_cycles": int((g.human_type == "rushed").sum()),
            "struggle_cycles": int((g.human_type == "struggle").sum()),
            "retry_bursts": int((g.human_type == "retries").sum()),
            "andon_pulls": int(g.andon_pulled.fillna(0).sum()),
            "andon_unanswered": int((g.human_type == "support").sum()),
            "login_issues": int((g.human_type == "login").sum()),
            "off_shift_cycles": int((g.human_type == "working_time").sum()),
        })
    s = pd.DataFrame(rows)

    # peer comparison: robust z of each operator-shift against all operator-shifts
    n = s.cycles.clip(lower=1)
    z = pd.DataFrame(index=s.index)
    z["pace"] = robust_z(s.pace_ratio, s.pace_ratio.median(), mad(s.pace_ratio))
    z["fatigue"] = robust_z(s.slowdown_pct, s.slowdown_pct.median(), mad(s.slowdown_pct))
    rr = s.retry_rate.median()                                     # Poisson noise floor
    z["retries"] = (s.retry_rate - rr) / np.maximum(mad(s.retry_rate), np.sqrt(rr / n))
    nr = max(s.nok_rate.median(), 0.002)                           # binomial noise floor
    z["quality"] = (s.nok_rate - nr) / np.sqrt(nr * (1 - nr) / n)
    z["technique"] = robust_z(s.technique_offset, s.technique_offset.median(), mad(s.technique_offset))

    findings, types = [], []
    for i, r in s.iterrows():
        f = []
        if r.off_shift_cycles:
            f.append(("working_time", f"worked {r.off_shift_cycles} cycles outside their crew's shift - "
                                      f"check hours and rest time"))
        if z.at[i, "fatigue"] >= PEER_Z:
            f.append(("fatigue", f"slowed down {r.slowdown_pct:+.0f}% over the shift (typical "
                                 f"{s.slowdown_pct.median():+.0f}%) - check workload, breaks and rotation"))
        if abs(z.at[i, "technique"]) >= PEER_Z:
            st = STATIONS.get(r.technique_station, {})
            sec = st.get("secondary", {}).get("name", "secondary")
            prim = st.get("primary", {}).get("name", "primary")
            f.append(("technique", f"at {str(r.technique_station).upper()} their {sec} runs {r.technique_offset:+.1f} sigma off "
                                f"the normal {prim}/{sec} pattern on every cycle - compare technique with "
                                f"standard work (all parts still in spec)"))
        if z.at[i, "retries"] >= PEER_Z:
            f.append(("retries", f"{r.retry_rate:.2f} retries per cycle vs typical {rr:.2f} - check tool/fixture, "
                                 f"offer coaching"))
        if z.at[i, "quality"] >= PEER_Z:
            f.append(("quality", f"NOK rate {r.nok_rate:.1%} vs typical {nr:.1%}"))
        if abs(z.at[i, "pace"]) >= PEER_Z:
            f.append(("pace", f"needs {r.pace_ratio:.0%} of the time others need on the same station and shift "
                              f"(typical {s.pace_ratio.median():.0%})"))
        if r.rushed_cycles >= 3:
            f.append(("rushing", f"{r.rushed_cycles} rushed cycles in one shift - possible skipped steps"))
        if r.andon_unanswered:
            f.append(("support", f"{r.andon_unanswered} andon call(s) with no team lead within 3 min - "
                                 f"support gap, not the operator"))
        if r.login_issues:
            f.append(("login", f"{r.login_issues} cycles under a stale login - fix login at rotation"))
        types.append(f[0][0] if f else None)
        findings.append("; ".join(t for _, t in f) if f else None)

    # only the directions that matter (slower/faster pace and technique both ways; more fatigue,
    # retries or NOKs) — so peer_score >= PEER_Z always means a flag
    directional = pd.concat([z.pace.abs(), z.fatigue, z.retries, z.quality, z.technique.abs()], axis=1)
    s["peer_score"] = directional.max(axis=1).clip(lower=0).round(2)
    s["flag"] = [int(t is not None) for t in types]
    s["finding_type"] = types
    s["finding"] = findings
    return s


# ---------------------------------------------------------------------------
def evaluate(cyc: pd.DataFrame, shifts: pd.DataFrame) -> None:
    """Only works on generated data: compare with what was injected."""
    if not PEOPLE_LABELS_PATH.exists():
        return
    lab = pd.read_csv(PEOPLE_LABELS_PATH, dtype={"shift_date": str})
    cl = lab[lab.level == "cycle"]
    flagged = set(zip(cyc.station[cyc.human_flag == 1], cyc.event_id[cyc.human_flag == 1]))
    inj = set(zip(cl.station, cl.event_id.astype(int)))
    hit = inj & flagged
    print(f"\ncycle level vs injected: precision {len(hit) / max(len(flagged), 1):.0%}  "
          f"recall {len(hit) / max(len(inj), 1):.0%}  ({len(flagged - inj)} flagged cycles were not injected)")
    cl = cl.assign(found=[(s, int(e)) in flagged for s, e in zip(cl.station, cl.event_id)])
    for pat, g in cl.groupby("pattern"):
        print(f"    {pat:<20} {g.found.sum():>4}/{len(g):<4} found")

    # operator-shift level: injected operator patterns + shifts that contain login / off-shift / andon problems
    carry = cl[cl.pattern.isin(["ghost_login", "off_shift", "andon_no_response"])]
    expect = pd.concat([lab[lab.level == "operator_shift"], carry])[["operator_id", "shift_date", "shift", "pattern"]]
    expect = expect.drop_duplicates()
    keys = set(zip(expect.operator_id, expect.shift_date, expect["shift"]))
    fl = set(zip(shifts.operator_id[shifts.flag == 1], shifts.shift_date[shifts.flag == 1], shifts["shift"][shifts.flag == 1]))
    hit = keys & fl
    print(f"\noperator-shift level vs injected: precision {len(hit) / max(len(fl), 1):.0%}  "
          f"recall {len(hit) / max(len(keys), 1):.0%}  ({len(fl)} of {len(shifts)} operator-shifts flagged)")
    expect = expect.assign(found=[k in fl for k in zip(expect.operator_id, expect.shift_date, expect["shift"])])
    for pat, g in expect.groupby("pattern"):
        print(f"    {pat:<20} {g.found.sum():>4}/{len(g):<4} found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contamination", type=float, default=0.005,
                    help="share of normal-looking operator cycles the model may call unusual")
    args = ap.parse_args()
    t0 = time.time()

    con = connect()
    tables = station_tables(con)
    frames = []
    for t in tables:
        df = pd.read_sql(f"SELECT * FROM {t}", con, parse_dates=["ts"])
        if "operator_time_s" not in df:
            raise SystemExit(f"{t} has no human-cycle columns - run generate_data.py again")
        frames.append(df[df.event_type == "CYCLE"].assign(station=t))
    cyc, shifts, bundles = run_people(frames, args.contamination)
    MODELS_DIR.mkdir(exist_ok=True)
    for t, bundle in bundles.items():
        joblib.dump(bundle, MODELS_DIR / f"people_{t}.joblib")
    write_back(con, tables, cyc, shifts)
    report(cyc, shifts, tables, t0)


def run_people(frames: list, contamination: float = 0.005):
    """Both levels of the people model on station cycles (the database or a live stream).
    frames: one DataFrame of CYCLE rows per station, with a 'station' column. -> (cyc, shifts, bundles)"""
    tables = sorted({f.station.iloc[0] for f in frames if len(f)})
    cyc = pd.concat(frames, ignore_index=True)
    cyc = add_shift_clock(cyc[cyc.operator_id.notna()].copy())
    cyc["key"] = cyc.station + ":" + cyc.event_id.astype(str)
    home = cyc.groupby("operator_id")["shift"].agg(lambda x: x.mode().iloc[0])

    # level 1: rules first (they explain a cycle better than a score does)
    login = login_conflicts(cyc)
    off = off_shift_work(cyc, home)
    cyc["human_score"], cyc["human_type"], cyc["human_reason"] = np.nan, None, None
    cyc["pace"], cyc["technique_z"] = np.nan, np.nan

    bundles = {}
    for t in tables:
        m = cyc.station == t
        score, reasons, pace, bundle = cycle_model(cyc[m], t, contamination)
        cyc.loc[m, "human_score"] = score.round(4)
        cyc.loc[m, "pace"] = pace
        cyc.loc[m, "technique_z"] = technique_offsets(cyc[m])
        for ix, (kind, text) in reasons.items():
            cyc.at[ix, "human_type"], cyc.at[ix, "human_reason"] = kind, text
        bundles[t] = bundle

    late = (cyc.andon_pulled == 1) & (cyc.andon_response_s > ANDON_SLA_S)
    for ix in cyc.index[late]:
        cyc.at[ix, "human_type"] = "support"
        cyc.at[ix, "human_reason"] = (f"andon pulled, team lead took {cyc.at[ix, 'andon_response_s']:.0f}s "
                                      f"(> {ANDON_SLA_S // 60} min) - support gap, not the operator")
    for rules, kind in ((login, "login"), (off, "working_time")):
        for ix in cyc.index[cyc.key.isin(rules)]:
            cyc.at[ix, "human_type"], cyc.at[ix, "human_reason"] = kind, rules[cyc.at[ix, "key"]]
    rule_rows = cyc.human_type.isin(["support", "login", "working_time"])
    cyc.loc[rule_rows, "human_score"] = 1.0
    cyc["human_flag"] = cyc.human_type.notna().astype(int)
    return cyc, operator_shifts(cyc), bundles


def write_back(con, tables, cyc, shifts):
    # write level 1 back into each station table (non-cycle rows get NULL)
    for t in tables:
        g = cyc[cyc.station == t]
        con.execute(f"UPDATE {t} SET human_score=NULL, human_flag=NULL, human_type=NULL, human_reason=NULL")
        con.executemany(
            f"UPDATE {t} SET human_score=?, human_flag=?, human_type=?, human_reason=? WHERE event_id=?",
            [(None if pd.isna(a) else float(a), int(b), c, d, int(e)) for a, b, c, d, e in
             g[["human_score", "human_flag", "human_type", "human_reason", "event_id"]].itertuples(index=False)])

    # level 2: one row per operator per shift
    con.executescript(OPERATOR_SHIFTS_SQL)
    shifts.to_sql("operator_shifts", con, if_exists="append", index=False)
    con.commit()
    con.close()


def report(cyc, shifts, tables, t0):
    print(f"people model: {len(cyc):,} operator cycles, {len(shifts)} operator-shifts, "
          f"{cyc.operator_id.nunique()} operators - done in {time.time() - t0:.1f}s")
    for t in tables:
        g = cyc[(cyc.station == t) & (cyc.human_flag == 1)]
        print(f"  {t}: {len(g)} cycles flagged  " +
              "  ".join(f"{k} {v}" for k, v in g.human_type.value_counts().items()))
    evaluate(cyc, shifts)

    print("\noperator-shifts worth a conversation:")
    for r in shifts[shifts.flag == 1].sort_values(["finding_type", "shift_date"]).itertuples():
        print(f"  {r.operator_id:<6} {r.shift_date} {r.shift}  [{r.finding_type}]  {r.finding}")


if __name__ == "__main__":
    main()
