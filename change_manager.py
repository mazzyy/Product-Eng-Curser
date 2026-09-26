"""
Change manager (model 6) - no work-instruction change reaches the line without a check and an
approval, and the next shift always knows the current method.

    DRAFT --check--> CHECKED --approve--> APPROVED --pilot--> PILOT --release--> RELEASED --after-check--> CLOSED
            |                                     (one crew, one shift)                      |
            +--> BLOCKED  (the method checker said BLOCK: fix it, submit a new version)       +--> ROLLED BACK

Gates
  check      method_checker on the new step list. BLOCK stops here; WARN needs a written reason.
  approve    engineer always; quality when a critical step, result check, VIN scan or control-plan step
             is touched or a SAFETY / TRACE rule is not clean; supervisor when operators' work or
             qualifications change (they plan the training and the sign-offs).
  pilot      one crew for one shift, only when every operator of that crew has signed the version.
  release    all crews, only after the pilot shift and when everyone in the rotation has signed.
  after-check after the first shift on the new version: median cycle within takt, hands-on time
             within 8 % of plan, no incident the cause finder blames on the version. Fail -> roll back.

"Current method": per station the released version (the pilot crew uses the pilot version).
The handover sheet for the next shift lists it, what changed, and who still has to sign.

    python change_manager.py demo                       # replay the week + the two example proposals
    python change_manager.py replay                     # the week's real WI changes through the gates
    python change_manager.py submit proposals/WI-012_v7.json
    python change_manager.py approve C-07 --role engineer --by "A. Engineer" [--accept "reason for WARN"]
    python change_manager.py sign C-07 --crew A          (or --crew all)
    python change_manager.py pilot C-07 --crew A
    python change_manager.py release C-07
    python change_manager.py status                     # changes, current method, handover sheet
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from method_checker import (ORDER, TAKT_S, check_design, check_people, planned, steps_of, verdict)
from method_checker import load as load_method
from method_data import apply_changes, full_step
from station_db import STATIONS, connect

SHIFT_H = 8
AFTER_TIME_TOL = 0.08
FIELDS = ("kind", "role", "time_s", "every", "tool", "tool_torque_nm", "reaction_arm", "manual_torque_nm", "lift_kg",
          "lift_assist", "hazard", "ppe", "hands_in_zone", "critical", "control_plan", "qualification", "min_skill")

CHANGES_SQL = """CREATE TABLE IF NOT EXISTS changes (
    change_id TEXT PRIMARY KEY, wi_version TEXT, station TEXT, base_version TEXT, title TEXT, source TEXT,
    spec TEXT, state TEXT, verdict TEXT, blocks TEXT, warns TEXT, accepted_reason TEXT, needs TEXT, approvals TEXT,
    rollout_ts TEXT, pilot_crew TEXT, pilot_ts TEXT, released_ts TEXT, after_status TEXT, after_note TEXT,
    outcome TEXT, created_ts TEXT, updated_ts TEXT)"""
EVENTS_SQL = """CREATE TABLE IF NOT EXISTS change_events (
    change_id TEXT, ts TEXT, actor TEXT, action TEXT, state_from TEXT, state_to TEXT, note TEXT)"""


class GateError(Exception):
    pass


def fmt(t) -> str | None:
    return None if t is None or (isinstance(t, float) and pd.isna(t)) else pd.Timestamp(t).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------
def init(con, reset=False):
    if reset:
        con.execute("DROP TABLE IF EXISTS changes")
        con.execute("DROP TABLE IF EXISTS change_events")
    con.execute(CHANGES_SQL)
    con.execute(EVENTS_SQL)
    con.commit()


def get(con, cid) -> dict:
    r = pd.read_sql("SELECT * FROM changes WHERE change_id = ?", con, params=(cid,))
    if r.empty:
        raise GateError(f"no change {cid}")
    d = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in r.iloc[0].to_dict().items()}
    d["approvals"] = json.loads(d["approvals"] or "{}")
    return d


def put(con, c: dict, actor: str, action: str, frm, note: str, ts):
    c = dict(c)
    c["approvals"] = json.dumps(c.get("approvals") or {})
    c["updated_ts"] = fmt(ts)
    cols = list(c)
    con.execute(f"INSERT OR REPLACE INTO changes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [c[k] for k in cols])
    con.execute("INSERT INTO change_events VALUES (?,?,?,?,?,?,?)", (c["change_id"], fmt(ts), actor, action, frm,
                                                                     c["state"], note))
    con.commit()


def next_id(con) -> str:
    n = con.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
    return f"C-{n + 1:02d}"


# ---------------------------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------------------------
def diff(prev: list[dict], new: list[dict]) -> dict:
    p, n = {s["key"]: s for s in prev}, {s["key"]: s for s in new}
    added, removed = [k for k in n if k not in p], [k for k in p if k not in n]
    changed = [k for k in n if k in p and any(n[k].get(f) != p[k].get(f) for f in FIELDS)]
    common = [k for k in n if k in p]
    moved = [k for k, a, b in zip(common, common, [k for k in p if k in n]) if a != b]
    touched = [(n if k in n else p)[k] for k in added + removed + changed + moved]
    return {"added": added, "removed": removed, "changed": changed, "moved": moved, "touched": touched}


def needed_roles(d: dict, checks: list[dict]) -> list[str]:
    roles = ["engineer"]
    t = d["touched"]
    if any(s.get("critical") or s.get("control_plan") or s["kind"] in ("verify", "scan") for s in t) or \
            any(c["grp"] in ("SAFETY", "TRACE") and c["status"] != "PASS" for c in checks):
        roles.append("quality")
    if any(s["role"] == "operator" and s["kind"] != "auto" for s in t) or \
            any(c["rule"] in ("TRN-1", "TRN-2") and c["status"] != "PASS" for c in checks):
        roles.append("supervisor")
    return roles


def run_check(con, base: str, spec: dict):
    wi, steps, quals, _ = load_method(con)
    b = wi[wi.wi_version == base]
    if b.empty:
        raise GateError(f"base version {base} is not in the database")
    sid = b.station.iloc[0]
    prev = steps_of(steps, base)
    new = apply_changes([full_step(s) for s in prev], spec.get("changes", []))
    checks = check_design(sid, new, prev) + check_people(new, quals)
    return sid, prev, new, checks


def summary(checks, status) -> str:
    return "; ".join(f"{c['rule']} {c['message']}" for c in sorted(checks, key=lambda c: ORDER[c["status"]])
                     if c["status"] == status)


def submit(con, spec: dict, by="engineer", now=None, source="proposal", cid=None) -> dict:
    now = pd.Timestamp(now or datetime.now())
    sid, prev, new, checks = run_check(con, spec["base"], spec)
    v = verdict(checks)
    c = {"change_id": cid or next_id(con), "wi_version": spec["wi_version"], "station": sid,
         "base_version": spec["base"], "title": spec.get("change_note", ""), "source": source,
         "spec": json.dumps(spec), "state": "BLOCKED" if v == "BLOCK" else "CHECKED", "verdict": v,
         "blocks": summary(checks, "BLOCK"), "warns": summary(checks, "WARN"), "accepted_reason": None,
         "needs": ",".join(needed_roles(diff(prev, new), checks)), "approvals": {},
         "rollout_ts": spec.get("rollout"), "pilot_crew": None, "pilot_ts": None, "released_ts": None,
         "after_status": None, "after_note": None, "outcome": None, "created_ts": fmt(now)}
    put(con, {**c, "state": "DRAFT"}, by, "submit", None, spec.get("change_note", ""), now)
    put(con, c, "method checker", "check", "DRAFT",
        f"{v}: {c['blocks'] or c['warns'] or 'all rules pass'}; planned {planned(new)['cycle_s']:.1f} s", now)
    return c


def approve(con, cid, role, by=None, accept=None, now=None) -> dict:
    now = pd.Timestamp(now or datetime.now())
    c = get(con, cid)
    if c["state"] == "BLOCKED":
        raise GateError(f"{cid} is BLOCKED by the method checker ({c['blocks']}) - fix it and submit a new version")
    if c["state"] not in ("CHECKED", "APPROVED"):
        raise GateError(f"{cid} is {c['state']} - only a CHECKED change can be approved")
    if role not in c["needs"].split(","):
        raise GateError(f"{cid} needs {c['needs']}, not {role}")
    if c["verdict"] == "WARN" and not (accept or c["accepted_reason"]):
        raise GateError(f"{cid} has warnings ({c['warns']}) - approve with --accept \"why it is acceptable\"")
    c["approvals"][role] = {"by": by or role, "ts": fmt(now)}
    c["accepted_reason"] = c["accepted_reason"] or accept
    frm = c["state"]
    if all(r in c["approvals"] for r in c["needs"].split(",")):
        c["state"] = "APPROVED"
    put(con, c, by or role, f"approve ({role})", frm, accept or "", now)
    return c


def crew_ops(con, crew=None) -> list[str]:
    q = pd.read_sql("SELECT DISTINCT operator_id, crew FROM qualifications", con)
    return sorted(q[q.crew == crew].operator_id if crew and crew != "all" else q.operator_id)


def unsigned(con, version, crew=None) -> list[str]:
    s = set(pd.read_sql("SELECT operator_id FROM wi_signoffs WHERE wi_version = ?", con, params=(version,)).operator_id)
    return [o for o in crew_ops(con, crew) if o not in s]


def sign(con, cid, crew="all", now=None) -> list[str]:
    now = pd.Timestamp(now or datetime.now())
    c = get(con, cid)
    ops = unsigned(con, c["wi_version"], crew)
    pd.DataFrame({"operator_id": ops, "wi_version": c["wi_version"], "signed_ts": fmt(now)}).to_sql(
        "wi_signoffs", con, if_exists="append", index=False)
    con.execute("INSERT INTO change_events VALUES (?,?,?,?,?,?,?)", (cid, fmt(now), "supervisor", f"sign-off crew {crew}",
                                                                     c["state"], c["state"], ", ".join(ops) or "-"))
    con.commit()
    return ops


def pilot(con, cid, crew="A", now=None) -> dict:
    c = get(con, cid)
    now = pd.Timestamp(now or c["rollout_ts"] or datetime.now())
    if c["state"] != "APPROVED":
        raise GateError(f"{cid} is {c['state']} - approve it first (needs {c['needs']})")
    missing = unsigned(con, c["wi_version"], crew)
    if missing:
        raise GateError(f"crew {crew} has not signed {c['wi_version']}: {', '.join(missing)}")
    c.update(state="PILOT", pilot_crew=crew, pilot_ts=fmt(now))
    put(con, c, "supervisor", f"pilot crew {crew}", "APPROVED", f"one shift from {fmt(now)}", now)
    return c


def release(con, cid, now=None) -> dict:
    c = get(con, cid)
    if c["state"] != "PILOT":
        raise GateError(f"{cid} is {c['state']} - run a pilot shift first")
    now = pd.Timestamp(now or pd.Timestamp(c["pilot_ts"]) + timedelta(hours=SHIFT_H))
    if now < pd.Timestamp(c["pilot_ts"]) + timedelta(hours=SHIFT_H):
        raise GateError("the pilot shift is not over yet")
    missing = unsigned(con, c["wi_version"])
    if missing:
        raise GateError(f"not everyone in the rotation has signed {c['wi_version']}: {', '.join(missing)}")
    c.update(state="RELEASED", released_ts=fmt(now), after_status="waiting for one shift of data")
    put(con, c, "engineer", "release all crews", "PILOT", "", now)
    return c


def after_check(con, sid, version, t0, t1) -> tuple[str, str]:
    """First shift on the version: cycle vs takt, hands-on vs plan, incidents the cause finder blames on it."""
    cyc = pd.read_sql(f"SELECT ts, cycle_time_s, operator_time_s, retries FROM {sid} WHERE event_type = 'CYCLE' "
                      f"AND work_instruction = ? AND ts >= ? AND ts < ?", con, params=(version, fmt(t0), fmt(t1)))
    if len(cyc) < 30:
        return "WAITING", "not enough cycles on the new version yet"
    _, steps, _, _ = load_method(con)
    plan = planned(steps_of(steps, version))
    net = (cyc.operator_time_s - cyc.retries.fillna(0) * STATIONS[sid]["retry_cost_s"]).median()
    ct = cyc.cycle_time_s.median()
    inc = pd.read_sql("SELECT incident_id, start_ts FROM incidents WHERE culprit = ? AND start_ts < ?", con,
                      params=(version, fmt(t1)))
    fails = []
    if ct > TAKT_S:
        fails.append(f"median cycle {ct:.1f} s > takt {TAKT_S:.0f} s")
    if abs(net / plan["op_s"] - 1) > AFTER_TIME_TOL:
        fails.append(f"hands-on {net:.1f} s vs plan {plan['op_s']:.1f} s ({net / plan['op_s'] - 1:+.0%})")
    if len(inc):
        fails.append(f"cause finder blames it (incident {', '.join('#' + str(i) for i in inc.incident_id)})")
    note = f"{len(cyc)} cycles, median cycle {ct:.1f} s, hands-on {net:.1f} s (plan {plan['op_s']:.1f} s)"
    return ("FAIL", "; ".join(fails)) if fails else ("OK", note)


# ---------------------------------------------------------------------------------------------
# Replay: the week's real WI changes through the same gates
# ---------------------------------------------------------------------------------------------
def replay(con) -> pd.DataFrame:
    wi, steps, quals, sign_df = load_method(con)
    try:
        imp = pd.read_sql("SELECT culprit, lost_cars, definite_cars FROM impacts", con)
    except Exception:
        imp = pd.DataFrame(columns=["culprit", "lost_cars", "definite_cars"])
    rows = []
    for sid in wi.station.unique():
        w = wi[wi.station == sid].sort_values("version_no").reset_index(drop=True)
        for i in range(1, len(w)):
            v, base = w.loc[i], w.loc[i - 1]
            t0 = pd.Timestamp(v.valid_from)
            t1 = pd.Timestamp(v.valid_to) if isinstance(v.valid_to, str) else None
            prev, new = steps_of(steps, base.wi_version), steps_of(steps, v.wi_version)
            checks = check_design(sid, new, prev) + check_people(new, quals)
            vd = verdict(checks)
            cid = next_id(con)
            c = {"change_id": cid, "wi_version": v.wi_version, "station": sid, "base_version": base.wi_version,
                 "title": v.change_note, "source": "history (replay)", "spec": None,
                 "state": "BLOCKED" if vd == "BLOCK" else "CLOSED", "verdict": vd, "blocks": summary(checks, "BLOCK"),
                 "warns": summary(checks, "WARN"), "accepted_reason": None,
                 "needs": ",".join(needed_roles(diff(prev, new), checks)), "approvals": {},
                 "rollout_ts": fmt(t0), "pilot_crew": None, "pilot_ts": None, "released_ts": None,
                 "after_status": None, "after_note": None, "outcome": None, "created_ts": fmt(t0 - timedelta(days=1))}
            ran_h = ((t1 or pd.Timestamp(pd.read_sql(f"SELECT MAX(ts) t FROM {sid}", con).t.iloc[0])) - t0
                     ).total_seconds() / 3600
            st, note = after_check(con, sid, v.wi_version, t0, t0 + timedelta(hours=SHIFT_H))
            hit = imp[imp.culprit == v.wi_version]
            lost, defin = float(hit.lost_cars.sum()), float(hit.definite_cars.sum())
            if vd == "BLOCK":
                outcome = (f"stopped at the check - never on the line. In reality it ran {ran_h:.0f} h"
                           + (f", {lost:.0f} lost cars" if lost else "") + (f", {defin:.0f} cars to check" if defin else ""))
                if st == "FAIL":
                    saved = max(ran_h - SHIFT_H, 0) / ran_h if ran_h else 0
                    outcome += (f". Even without the check, the pilot shift's after-check would have rolled it back "
                                f"after {SHIFT_H} h ({note}) - {saved:.0%} of the damage avoided")
            else:
                outcome = f"passes the gates; after-check {st}: {note}"
            c.update(after_status=st, after_note=note, outcome=outcome)
            put(con, {**c, "state": "DRAFT"}, "engineer", "submit (replay)", None, v.change_note, t0 - timedelta(days=1))
            put(con, c, "method checker", "check (replay)", "DRAFT", f"{vd}: {c['blocks'] or c['warns'] or 'all rules pass'}",
                t0 - timedelta(days=1))
            rows.append(c)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# Current method + handover sheet
# ---------------------------------------------------------------------------------------------
def current_methods(con, at=None) -> pd.DataFrame:
    wi = pd.read_sql("SELECT * FROM work_instructions", con)
    ch = pd.read_sql("SELECT * FROM changes", con) if con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'changes'").fetchone() else pd.DataFrame()
    at = pd.Timestamp(at) if at else max(pd.Timestamp(pd.read_sql(f"SELECT MAX(ts) t FROM {s}", con).t.iloc[0])
                                         for s in wi.station.unique()).ceil("h")
    rows = []
    for sid in sorted(wi.station.unique()):
        w = wi[(wi.station == sid) & (pd.to_datetime(wi.valid_from) <= at)].sort_values("version_no")
        cur, since, what = w.wi_version.iloc[-1], w.valid_from.iloc[-1], w.change_note.iloc[-1]
        pilot_v = pilot_crew = nxt = None
        if len(ch):
            c = ch[(ch.station == sid) & (ch.source == "proposal")]
            rel = c[(c.state.isin(["RELEASED", "CLOSED"])) & (pd.to_datetime(c.released_ts) <= at)]
            if len(rel):
                r = rel.sort_values("released_ts").iloc[-1]
                cur, since, what = r.wi_version, r.released_ts, r.title
            pil = c[c.state == "PILOT"]
            if len(pil):
                pilot_v, pilot_crew = pil.wi_version.iloc[-1], pil.pilot_crew.iloc[-1]
            up = c[c.state.isin(["APPROVED", "CHECKED", "PILOT"]) | ((c.state == "RELEASED") &
                                                                      (pd.to_datetime(c.released_ts) > at))]
            if len(up):
                nxt = f"{up.wi_version.iloc[-1]} ({up.state.iloc[-1]}, rollout {up.rollout_ts.iloc[-1]})"
            blocked = c[c.state == "BLOCKED"].wi_version.tolist()
        else:
            blocked = []
        todo = unsigned(con, nxt.split(" ")[0] + " " + nxt.split(" ")[1]) if nxt else []
        failed = ch[(ch.station == sid) & (ch.wi_version == cur) & (ch.state == "BLOCKED")] if len(ch) else ch
        alert = (f"{cur} fails the method check ({failed.blocks.iloc[0].split(';')[0]}) - roll back to "
                 f"{failed.base_version.iloc[0]} or fix it") if len(failed) else "-"
        rows.append({"station": sid, "current": cur, "since": str(since)[:16], "what_changed": what, "alert": alert,
                     "pilot": f"{pilot_v} (crew {pilot_crew})" if pilot_v else "-", "next": nxt or "-",
                     "must_sign": ", ".join(todo) or "-", "do_not_use": ", ".join(blocked) or "-"})
    return pd.DataFrame(rows)


def status(con):
    ch = pd.read_sql("SELECT change_id, wi_version, source, state, verdict, needs, approvals, outcome, blocks "
                     "FROM changes ORDER BY change_id", con)
    print("Changes")
    for r in ch.itertuples():
        appr = ",".join(json.loads(r.approvals or "{}")) or "-"
        print(f"  {r.change_id}  {r.wi_version:<10} {r.state:<9} check {r.verdict:<5} needs {r.needs:<26} "
              f"approved {appr:<26} [{r.source}]")
        if r.outcome:
            print(f"        {r.outcome}")
        elif r.state == "BLOCKED":
            print(f"        blocked: {r.blocks}")
    cm = current_methods(con)
    print("\nHandover sheet for the next shift")
    for r in cm.itertuples():
        print(f"  {r.station.upper()}: work to {r.current} (since {r.since}: {r.what_changed})")
        if r.alert != "-":
            print(f"         ALERT: {r.alert}")
        if r.pilot != "-":
            print(f"         pilot: {r.pilot}")
        if r.next != "-":
            print(f"         next: {r.next}; still to sign: {r.must_sign}")
        if r.do_not_use != "-":
            print(f"         NOT for use (blocked): {r.do_not_use}")


def demo(con):
    """The week's real changes through the gates, then the two example proposals end to end."""
    init(con, reset=True)
    signed = pd.read_sql("SELECT * FROM wi_signoffs", con)
    signed[~signed.wi_version.isin(["WI-012 v7", "WI-013 v9"])].to_sql("wi_signoffs", con, if_exists="replace", index=False)
    rep = replay(con)
    print("Replay of the week's work-instruction changes")
    for r in rep.itertuples():
        print(f"  {r.change_id} {r.wi_version:<10} {r.verdict:<5} -> {r.outcome}")
    now = pd.Timestamp("2026-09-21 06:00")
    root = Path(__file__).resolve().parent / "proposals"
    print("\nProposals")
    for name in ("WI-012_v7.json", "WI-013_v9.json"):
        spec = json.loads((root / name).read_text())
        c = submit(con, spec, "engineer", now)
        print(f"  {c['change_id']} {c['wi_version']}: {c['state']} ({c['verdict']}); needs {c['needs']}"
              + (f"\n        blocked: {c['blocks']}" if c["state"] == "BLOCKED" else ""))
        if c["state"] == "BLOCKED":
            try:
                approve(con, c["change_id"], "engineer", now=now)
            except GateError as e:
                print(f"        approve refused: {e}")
            continue
        for i, role in enumerate(c["needs"].split(",")):
            approve(con, c["change_id"], role, now=now + timedelta(hours=1 + i),
                    accept="accepted in the demo" if c["verdict"] == "WARN" else None)
        rollout = pd.Timestamp(c["rollout_ts"])
        try:
            pilot(con, c["change_id"], "A", rollout)
        except GateError as e:
            print(f"        pilot refused: {e}")
        sign(con, c["change_id"], "A", rollout - timedelta(hours=3))
        pilot(con, c["change_id"], "A", rollout)
        try:
            release(con, c["change_id"])
        except GateError as e:
            print(f"        release refused: {e}")
        sign(con, c["change_id"], "all", rollout + timedelta(hours=4))
        c = release(con, c["change_id"])
        print(f"        approved by {', '.join(c['needs'].split(','))} -> pilot crew A {c['pilot_ts']} -> "
              f"released {c['released_ts']}; after-check {c['after_status']}")
    print()
    status(con)


def main():
    ap = argparse.ArgumentParser(description="Change manager")
    ap.add_argument("cmd", choices=["demo", "replay", "submit", "approve", "sign", "pilot", "release", "status"])
    ap.add_argument("arg", nargs="?", help="proposal JSON (submit) or change id")
    ap.add_argument("--role", choices=["engineer", "quality", "supervisor"])
    ap.add_argument("--by")
    ap.add_argument("--accept", help="why a WARN is acceptable")
    ap.add_argument("--crew", default="A")
    a = ap.parse_args()
    con = connect()
    init(con)
    try:
        if a.cmd == "demo":
            demo(con)
        elif a.cmd == "replay":
            con.execute("DELETE FROM changes WHERE source LIKE 'history%'")
            print(replay(con)[["change_id", "wi_version", "verdict", "outcome"]].to_string(index=False))
        elif a.cmd == "submit":
            c = submit(con, json.loads(Path(a.arg).read_text()), a.by or "engineer")
            print(f"{c['change_id']} {c['wi_version']}: {c['state']} ({c['verdict']}), needs {c['needs']}")
        elif a.cmd == "approve":
            c = approve(con, a.arg, a.role, a.by, a.accept)
            print(f"{c['change_id']}: {c['state']} - approvals {list(c['approvals'])} of {c['needs']}")
        elif a.cmd == "sign":
            print("signed:", ", ".join(sign(con, a.arg, a.crew)) or "nobody left")
        elif a.cmd == "pilot":
            print(pilot(con, a.arg, a.crew)["state"])
        elif a.cmd == "release":
            print(release(con, a.arg)["state"])
        else:
            status(con)
    except GateError as e:
        raise SystemExit(f"refused: {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
