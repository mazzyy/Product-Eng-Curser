"""
Containment (model 7) - which cars wait for a check, which move on, and should the line stop?

For every problem case the cause finder found, at the moment it would have found it:
  1. scope     which cars can be affected - by genealogy, not by guessing:
                 machine  -> every car since the machine's last good check (a drifting tool can't be
                             trusted back to its last verification) until the repair
                 batch    -> every car built with that part batch
                 method   -> every car built with that work-instruction version
                 people   -> the operator's cars in the affected shifts
                 data     -> cars with no VIN / no upstream record in the outage
  2. sort      each car in scope:
                 REWORK   rejected, or passed OK although out of spec
                 CHECK    suspect: re-hits / re-tests, flagged by the signal checker, more than half
                          of its tolerance used, bad batch on a safety-critical joint, no record
                 HOLD     waits until a sample audit of the rest passes (1 in 20, at least 5)
                 RELEASE  moves on
               and where it is: still in the plant, or already shipped (> 24 h after its last station)
  3. advise    stop / quarantine batch / roll back WI / 100 % check at the station / no action.
               The engineer recommends - the supervisor decides a stop, quality decides the release.

Tables: containment_cases (one row per case), car_holds (one row per car in scope, not RELEASE).

    python containment.py            # after find_causes.py (and maintenance.py for the prevention note)
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

import pandas as pd

from station_db import STATIONS, connect, station_tables

YARD_HOURS = 24          # cars leave the plant about a day after the last station
DETECT_LAG_H = 2         # the cause finder works in 2-hour blocks: a case is known at the end of its first block
MARGIN_CHECK = 0.5       # a suspect car that used more than half its tolerance gets checked
STOP_RATE = 0.2          # >= 20 % suspect cars in the last 2 h on a safety-critical station -> recommend a stop
AUDIT_EVERY, AUDIT_MIN = 20, 5
PRODUCT_RISK = {"quality", "pattern", "data"}     # speed problems cost time, not product quality

ACTION_LABEL = {"STOP": "Stop recommended", "QUARANTINE BATCH": "Quarantine batch", "100% CHECK": "100% check",
                "ROLL BACK WI": "Roll back WI", "FIX WI": "Fix WI", "HOLD + CHECK": "Hold + check",
                "MANUAL RECORD": "Manual VIN record", "SUPPORT": "Support operator", "NO HOLD": "No hold"}
ACTION_SEV = {"STOP": "critical", "QUARANTINE BATCH": "serious", "100% CHECK": "serious", "ROLL BACK WI": "serious",
              "FIX WI": "serious", "HOLD + CHECK": "warning", "MANUAL RECORD": "warning", "SUPPORT": "info",
              "NO HOLD": "info"}


def margin_used(v, sid) -> float:
    """How much of the tolerance a value used, only on the sides that matter (0 = nominal, 1 = at the limit)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return float("nan")
    p = STATIONS[sid]["primary"]
    sides = {side for sig, side, _ in STATIONS[sid]["nok_modes"] if sig == "primary"}
    hi = (v - p["nominal"]) / (p["usl"] - p["nominal"]) if "high" in sides and v >= p["nominal"] else 0.0
    lo = (p["nominal"] - v) / (p["nominal"] - p["lsl"]) if "low" in sides and v < p["nominal"] else 0.0
    return max(hi, lo)


def infer_vin(prev_vin) -> str | None:
    """VINs are sequential on the line: a car with no scan is the one after its neighbour."""
    if not isinstance(prev_vin, str):
        return None
    m = re.match(r"(.*?)(\d+)$", prev_vin)
    return f"{m.group(1)}{int(m.group(2)) + 1:0{len(m.group(2))}d}" if m else None


COLS = ["event_id", "ts", "event_type", "vin", "part_batch", "work_instruction", "operator_id", "result",
        "primary_value", "primary_lsl", "primary_usl", "retries", "anomaly_flag", "anomaly_type", "anomaly_reason",
        "comment"]


def load(con):
    cyc, ev = prepare({sid: pd.read_sql(f"SELECT {', '.join(COLS)} FROM {sid}", con) for sid in station_tables(con)})
    inc = pd.read_sql("SELECT * FROM incidents", con, parse_dates=["start_ts", "end_ts"])
    return cyc, ev, inc


def prepare(frames: dict):
    """Station tables (the database or a live stream) -> cycles with margin / spec / VIN flags, and events."""
    cyc, ev = {}, {}
    for sid, d in frames.items():
        d = d[COLS].copy()
        d["ts"] = pd.to_datetime(d.ts)
        e = d[d.event_type == "MAINTENANCE"][["ts", "comment"]].sort_values("ts")
        d = d[d.event_type == "CYCLE"].sort_values("ts").reset_index(drop=True)
        d["prev_vin"] = d.vin.ffill().shift()
        d["margin"] = [margin_used(v, sid) for v in d.primary_value]
        d["out_of_spec"] = (d.primary_value < d.primary_lsl) | (d.primary_value > d.primary_usl)
        d["dup_vin"] = d.vin.notna() & d.vin.duplicated(keep=False)
        cyc[sid], ev[sid] = d, e
    return cyc, ev


def case_table(inc: pd.DataFrame) -> pd.DataFrame:
    g = inc.groupby("case_id")
    return pd.DataFrame({
        "culprit": g.culprit.first(), "cause": g.cause.first(), "family": g.family.first(),
        "station": g.top_station.agg(lambda s: s.mode().iloc[0]),
        "stations": g.stations.agg(lambda s: ",".join(sorted({x for v in s for x in v.split(",")}))),
        "start": g.start_ts.min(), "end": g.end_ts.max(), "incidents": g.incident_id.agg(list),
        "windows": g.apply(lambda x: list(zip(x.start_ts, x.end_ts)), include_groups=False),
    }).reset_index()


def equipment_repair(ev: pd.DataFrame, sid: str, family: str, after):
    """First unplanned repair of the equipment behind this symptom family after the case started."""
    fix_text = STATIONS[sid]["equipment"][family][2]            # e.g. "Nutrunner recalibrated"
    rep = ev[(ev.ts >= after) & ev.comment.str.contains(fix_text, regex=False)]
    return rep.ts.iloc[0] if len(rep) else None


def scope(c, cyc, ev) -> tuple:
    """-> (cars in scope, window start, window end, basis text)"""
    sid, cul = c.station, c.culprit
    d = cyc[sid]
    if c.cause == "machine":
        fix = equipment_repair(ev[sid], sid, c.family, c.start) or c.end
        if c.family == "pattern":         # silent drift: nothing since the last good check can be trusted
            last = ev[sid][ev[sid].ts < c.start].ts
            start = last.iloc[-1] if len(last) else d.ts.min()
            basis = f"back to the last good check of {cul} ({start:%a %H:%M})"
        else:                             # wear shows up as re-hits / re-tests: from the first symptoms
            start = c.start - timedelta(hours=DETECT_LAG_H)
            basis = f"from the first symptoms until the repair of {cul}"
        return d[(d.ts >= start) & (d.ts < fix)], start, fix, basis
    if c.cause == "station" and "batch" in cul:
        b = re.search(r"[A-Z]{2}-\d{4}", cul).group(0)
        s = d[d.part_batch == b]
        return s, s.ts.min(), s.ts.max(), f"every car built with {b} (part genealogy)"
    if c.cause == "method":
        s = d[d.work_instruction == cul]
        return s, s.ts.min(), s.ts.max(), f"every car built with {cul}"
    if c.cause == "people":
        parts = [x[(x.operator_id == cul) & x.ts.between(a, b)] for a, b in c.windows for x in
                 (cyc[s] for s in c.stations.split(","))]
        s = pd.concat(parts).drop_duplicates("event_id") if parts else d.iloc[:0]
        return s, c.start, c.end, f"{cul}'s cars in the affected shifts"
    s = pd.concat([cyc[x][cyc[x].ts.between(c.start, c.end)] for x in c.stations.split(",")])
    return s, c.start, c.end, "cars built during the problem"


def sort_car(r, c, sev) -> tuple[str, str]:
    if r.result == "NOK":
        return "REWORK", "rejected at the station - confirm the rework is done"
    if bool(r.out_of_spec):
        return "REWORK", "passed OK although out of spec"
    if c.family == "data":
        if not isinstance(r.vin, str):
            return "CHECK", f"no VIN on record - probably {infer_vin(r.prev_vin)}; confirm it was processed"
        if r.anomaly_type == "traceability" and not r.dup_vin:
            return "CHECK", "no record at the upstream station"
        return "RELEASE", "double scan: the car is fine, only the MES count is wrong"
    flagged = r.anomaly_flag == 1 and r.anomaly_type in ("pattern", "label_conflict", "sensor")
    m = r.margin if not (isinstance(r.margin, float) and math.isnan(r.margin)) else 0
    if c.family == "quality":
        if c.cause == "station" and sev >= 8:
            return "CHECK", "built with the bad batch on a safety-critical joint"
        if r.retries >= (2 if c.cause == "people" else 1):
            return "CHECK", f"{int(r.retries)} re-hits / re-tests on a suspect {'operator' if c.cause == 'people' else 'part or tool'}"
        return "RELEASE", "no re-hits, values fine"
    # pattern
    if flagged or m >= MARGIN_CHECK:
        return "CHECK", ("flagged by the signal checker" if flagged else f"used {m:.0%} of its tolerance")
    if c.cause == "people":
        return "RELEASE", "values in spec - technique difference only"
    return "HOLD", "waits for the sample audit"


def advise(c, cars, dec_ts, fix_ts, sid, cyc) -> tuple[str, str]:
    sev = STATIONS[sid]["severity"]
    active = fix_ts is None or fix_ts > dec_ts
    if c.family not in PRODUCT_RISK:
        return "NO HOLD", "no product risk - a delivery problem (see the impact ranker)"
    recent = cyc[sid][cyc[sid].ts.between(dec_ts - timedelta(hours=2), dec_ts)]
    bad = cars[cars.ts.between(dec_ts - timedelta(hours=2), dec_ts) & cars.disposition.isin(["CHECK", "REWORK"])]
    rate = len(bad) / max(len(recent), 1)
    if not active:
        return "HOLD + CHECK", "the cause is already fixed - check and release the cars in scope"
    if c.cause == "machine" and c.family == "pattern" and sev >= 8 and rate >= STOP_RATE:
        return "STOP", (f"{rate:.0%} of cars in the last 2 h are suspect on a safety-critical joint and the tool "
                        f"cannot be trusted - stop {sid.upper()} until {c.culprit} is re-checked")
    if c.cause == "station" and "batch" in c.culprit:
        return "QUARANTINE BATCH", f"block the rest of {c.culprit}, switch to the next pallet - no line stop needed"
    if c.cause == "method":
        return ("ROLL BACK WI" if c.family != "data" else "FIX WI",
                f"go back to the previous version of {c.culprit.split(' v')[0]} (change manager)")
    if c.family == "data":
        return "MANUAL RECORD", "keep running, record VINs by hand, check the unrecorded cars"
    if c.cause == "people":
        return "SUPPORT", f"pair or re-train {c.culprit}; re-check the cars listed - no stop"
    return "100% CHECK", f"check every car at {sid.upper()} until {c.culprit} is repaired ({rate:.0%} suspect in the last 2 h)"


def prevention(con, c, window_h) -> str:
    """For a silent machine drift: what the maintenance plan's check interval would do to this window."""
    if c.cause != "machine" or c.family != "pattern":
        return "-"
    try:
        m = pd.read_sql("SELECT * FROM maint_plan WHERE station = ? AND family = ?", con, params=(c.station, c.family))
    except Exception:
        return "-"
    if m.empty or pd.isna(m.interval_h.iloc[0]):
        return "-"
    r = m.iloc[0]
    return (f"maintenance plan: {r.policy_rec} -> this window would be at most {r.interval_h:.0f} h "
            f"(~{r.cars_at_risk_rec:,.0f} cars) instead of {window_h:.0f} h")


def run(con):
    cyc, ev, inc = load(con)
    cases = case_table(inc)
    rows, holds = [], []
    for c in cases.itertuples():
        sid = c.station
        sev = STATIONS[sid]["severity"]
        dec_ts = c.start + timedelta(hours=DETECT_LAG_H)
        cars, w0, w1, basis = scope(c, cyc, ev)
        cars = cars.copy()
        if c.family in PRODUCT_RISK:
            s = [sort_car(r, c, sev) for r in cars.itertuples()]
            cars["disposition"] = [x[0] for x in s]
            cars["reason"] = [x[1] for x in s]
        else:
            cars["disposition"], cars["reason"] = "RELEASE", "no product risk"
        n_hold = int((cars.disposition == "HOLD").sum())
        audit = min(n_hold, max(AUDIT_MIN, math.ceil(n_hold / AUDIT_EVERY))) if n_hold else 0
        # where is each car at the decision time?
        last_seen = pd.concat([cyc[x][["vin", "ts"]] for x in cyc]).dropna().groupby("vin").ts.max()
        seen = cars.vin.map(last_seen).fillna(cars.ts)
        cars["where"] = ["built after the decision - check in line" if t > dec_ts else
                         "shipped - quality field review" if (dec_ts - s).total_seconds() / 3600 > YARD_HOURS else
                         "in the plant" for t, s in zip(cars.ts, seen)]
        fix_ts = w1 if c.cause in ("machine", "method") or "batch" in c.culprit else c.end
        action, why = advise(c, cars, dec_ts, fix_ts, sid, cyc)
        n = cars.disposition.value_counts()
        rows.append({"case_id": c.case_id, "culprit": c.culprit, "cause": c.cause, "family": c.family, "station": sid,
                     "severity": sev, "incidents": ",".join(map(str, c.incidents)),
                     "decision_ts": f"{dec_ts:%Y-%m-%d %H:%M}", "window_start": f"{w0:%Y-%m-%d %H:%M}",
                     "window_end": f"{w1:%Y-%m-%d %H:%M}", "window_h": round((w1 - w0).total_seconds() / 3600, 1),
                     "basis": basis, "cars_in_scope": len(cars), "rework": int(n.get("REWORK", 0)),
                     "check": int(n.get("CHECK", 0)), "hold": n_hold, "audit_sample": audit,
                     "release": int(n.get("RELEASE", 0)),
                     "shipped": int((cars.disposition != "RELEASE").mul(cars["where"].str.startswith("shipped")).sum()),
                     "action": action, "why": why, "prevent": prevention(con, c, (w1 - w0).total_seconds() / 3600),
                     "who_decides": "supervisor decides the stop; quality releases held cars" if action == "STOP"
                     else "quality releases held cars" if n.get("CHECK", 0) + n_hold else "-"})
        keep = cars[cars.disposition != "RELEASE"]
        holds += [{"case_id": c.case_id, "vin": r.vin if isinstance(r.vin, str) else infer_vin(r.prev_vin),
                   "vin_inferred": not isinstance(r.vin, str), "station": sid, "ts": f"{r.ts:%Y-%m-%d %H:%M:%S}",
                   "disposition": r.disposition, "reason": r.reason, "where_now": r.where} for r in keep.itertuples()]
    out, hl = pd.DataFrame(rows), pd.DataFrame(holds)
    out.to_sql("containment_cases", con, if_exists="replace", index=False)
    hl.to_sql("car_holds", con, if_exists="replace", index=False)
    con.commit()
    return out, hl


def main():
    con = connect()
    out, hl = run(con)
    con.close()
    print("Containment - which cars wait for a check, and should the line stop?\n")
    for r in out.sort_values(["severity", "check"], ascending=False).itertuples():
        print(f"case {r.case_id:>2}  {r.culprit:<26} {r.cause}/{r.family:<8} {r.station.upper()}  decided {r.decision_ts[5:]}"
              f"  ->  {r.action}")
        print(f"         {r.why}")
        if r.prevent != "-":
            print(f"         {r.prevent}")
        if r.cars_in_scope and r.family in PRODUCT_RISK:
            print(f"         scope {r.cars_in_scope} cars, {r.window_h:.0f} h ({r.basis}): rework {r.rework}, "
                  f"check {r.check}, hold {r.hold} (audit {r.audit_sample}), release {r.release}"
                  + (f", {r.shipped} already shipped" if r.shipped else ""))
    stops = out[out.action == "STOP"]
    print(f"\n{len(out)} cases: " + ", ".join(f"{k} {v}" for k, v in out.action.value_counts().items())
          + f"  |  cars waiting: {int((hl.disposition != 'RELEASE').sum()) if len(hl) else 0} "
          f"(check {int((hl.disposition == 'CHECK').sum()) if len(hl) else 0})")
    for r in stops.itertuples():
        print(f"stop recommended: {r.station.upper()} on {r.decision_ts} - {r.culprit}")


if __name__ == "__main__":
    main()
