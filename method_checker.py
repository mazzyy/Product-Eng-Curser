"""
Method checker (model 5) - does a work instruction fit the takt, is it safe, are people trained?

Fixed, explainable rules - no ML. Two ways to run it:
  history   check every WI version in the database: what would the checker have said before
            each rollout, and what happened after it (sign-offs, measured times, incidents)?
  proposal  check a new version before it goes live: a JSON file that lists the changes to an
            existing version (see proposals/).

Rules (limits are constants below)
  TIME    TIME-1  planned cycle fits the takt            block > 100 %, warn > 95 %
          TIME-2  hands-on work vs the station's line-balance standard   warn > +10 %
  SAFETY  SAF-1   every critical step has a result check after it
          SAF-2   every control-plan step is still there
          SAF-3   powered tool above 50 Nm has a reaction arm
          SAF-4   no manual torque >= 60 Nm on every car (ergonomics; sampled audits are fine)
          SAF-5   no lift above 15 kg without an assist (warn above 10 kg)
          SAF-6   steps with chemicals list the PPE
          SAF-7   no hands in the machine zone during an automatic step
  TRACE   TRC-1   exactly one VIN scan per car at the station
  PEOPLE  TRN-1   every operator in the rotation holds each step's qualification
                  (per crew: 1 gap = warn, the team lead can cover; 2+ gaps = block)
          TRN-2   operators below a step's minimum skill (warn: pair them up)
          TRN-3   cycles built before the operator signed the new version (history only)
  REALITY REA-1   measured hands-on time vs the plan, +/-8 % (history only): slower = standard
                  too optimistic, faster = steps may be skipped
          REA-2   incidents the cause finder blamed on this version (history only, info)

Verdict: BLOCK if any rule blocks, WARN if any warns, else PASS. The "design verdict" uses only
TIME, SAFETY and TRACE - the part an engineer can fix in the document itself.

    python method_checker.py                                  # all versions in the database
    python method_checker.py --propose proposals/WI-013_v9.json
    python method_checker.py --eval 12                        # rules vs 12 random simulated weeks
"""
from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

from method_data import BASE_STEPS, CONTROL_PLAN, apply_changes, build, full_step
from station_db import STATIONS, connect, station_tables

TAKT_S = 50.0              # the line takt a WI must fit (today's simulated line)
TAKT_WARN = 0.95           # above 95 % of takt: less than 2.5 s margin for normal variation
ADDED_WORK_WARN = 0.10     # +10 % hands-on work vs the station's line-balance standard
REALITY_WARN = 0.08        # measured hands-on time more than 8 % above the plan
REACTION_ARM_NM = 50.0
MANUAL_TORQUE_NM = 60.0
LIFT_BLOCK_KG, LIFT_WARN_KG = 15.0, 10.0
CARS_PER_SHIFT = int(8 * 3600 / TAKT_S)
GROUPS = ["TIME", "SAFETY", "TRACE", "PEOPLE", "REALITY"]
DESIGN_GROUPS = {"TIME", "SAFETY", "TRACE"}
ORDER = {"BLOCK": 0, "WARN": 1, "INFO": 2, "PASS": 3}


def v(x):
    """sqlite / pandas missing values -> None."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return x


def R(group, rule, status, message, value=None, limit=None):
    return {"grp": group, "rule": rule, "status": status, "message": message, "value": value, "limit_value": limit}


def planned(steps: list[dict]) -> dict:
    op = sum(s["time_s"] * s["every"] for s in steps if s["role"] == "operator" and s["kind"] != "auto")
    mach = sum(s["time_s"] for s in steps if s["kind"] == "auto")
    tl = sum(s["time_s"] * s["every"] for s in steps if s["role"] != "operator")
    return {"op_s": round(op, 2), "machine_s": round(mach, 2), "cycle_s": round(op + mach, 2), "team_lead_s": round(tl, 2)}


def is_operator(s):
    return s["role"] == "operator"


# ---------------------------------------------------------------------------------------------
# Design rules: only the step list is needed
# ---------------------------------------------------------------------------------------------
def check_design(sid: str, steps: list[dict], prev: list[dict] | None) -> list[dict]:
    out = []
    p = planned(steps)
    pct = p["cycle_s"] / TAKT_S
    st = "BLOCK" if pct > 1 else "WARN" if pct > TAKT_WARN else "PASS"
    out.append(R("TIME", "TIME-1", st, f"planned cycle {p['cycle_s']:.1f} s ({p['op_s']:.1f} s hands-on + "
                 f"{p['machine_s']:.1f} s machine) = {pct:.0%} of the {TAKT_S:.0f} s takt", p["cycle_s"], TAKT_S))
    std = STATIONS[sid]["operator_time_s"]
    rel = p["op_s"] / std - 1
    was = f"; version before {planned(prev)['op_s']:.1f} s" if prev is not None else ""
    out.append(R("TIME", "TIME-2", "WARN" if rel > ADDED_WORK_WARN else "PASS",
                 f"hands-on work {p['op_s']:.1f} s vs line-balance standard {std:.1f} s ({rel:+.0%}{was})",
                 round(rel, 3), ADDED_WORK_WARN))

    # SAF-1 a critical step needs a result check (operator, every car) after it
    bad = []
    for i, s in enumerate(steps):
        if s["critical"] and not any(t["kind"] == "verify" and is_operator(t) and t["every"] >= 1 for t in steps[i + 1:]):
            bad.append(s["text"])
    out.append(R("SAFETY", "SAF-1", "BLOCK" if bad else "PASS",
                 ("no result check after: " + "; ".join(bad)) if bad else "every critical step is checked afterwards"))
    # SAF-2 control plan
    keys = [s["key"] for s in steps]
    base_text = {s["key"]: s["text"] for s in BASE_STEPS[sid]}
    missing = [k for k in CONTROL_PLAN[sid] if k not in keys]
    out.append(R("SAFETY", "SAF-2", "BLOCK" if missing else "PASS",
                 ("control-plan step removed: " + "; ".join(base_text[k] for k in missing)) if missing
                 else f"all {len(CONTROL_PLAN[sid])} control-plan steps present"))
    # SAF-3 reaction arm
    bad = [s["text"] for s in steps if (v(s["tool_torque_nm"]) or 0) > REACTION_ARM_NM and not s["reaction_arm"]]
    out.append(R("SAFETY", "SAF-3", "BLOCK" if bad else "PASS",
                 ("powered tool above 50 Nm without reaction arm: " + "; ".join(bad)) if bad else "reaction arms in place"))
    # SAF-4 manual torque
    heavy = [s for s in steps if (v(s["manual_torque_nm"]) or 0) >= MANUAL_TORQUE_NM]
    every = [s for s in heavy if is_operator(s) and s["every"] >= 0.5]
    if every:
        s = every[0]
        msg = (f"manual torque {s['manual_torque_nm']:.0f} Nm by hand on every car (~{CARS_PER_SHIFT} cars per shift): "
               f"{s['text']}")
    elif heavy:
        msg = f"manual torque only as a sampled audit ({heavy[0]['every']:.0%} of cars, {heavy[0]['role']})"
    else:
        msg = "no heavy manual torque"
    out.append(R("SAFETY", "SAF-4", "BLOCK" if every else "PASS", msg))
    # SAF-5 lifting
    worst, st = None, "PASS"
    for s in steps:
        kg = v(s["lift_kg"]) or 0
        if kg > LIFT_BLOCK_KG and not s["lift_assist"]:
            worst, st = s, "BLOCK"
        elif kg > LIFT_WARN_KG and not s["lift_assist"] and st == "PASS":
            worst, st = s, "WARN"
    out.append(R("SAFETY", "SAF-5", st, f"{worst['lift_kg']:.0f} kg lifted without assist: {worst['text']}" if worst
                 else "no unassisted heavy lifts"))
    # SAF-6 PPE
    bad = [s["text"] for s in steps if v(s["hazard"]) == "chemical" and not v(s["ppe"])]
    out.append(R("SAFETY", "SAF-6", "BLOCK" if bad else "PASS",
                 ("chemical step without PPE: " + "; ".join(bad)) if bad else "PPE listed on every chemical step"))
    # SAF-7 hands in the machine zone
    bad = [s["text"] for s in steps if s["kind"] == "auto" and s["hands_in_zone"]]
    out.append(R("SAFETY", "SAF-7", "BLOCK" if bad else "PASS",
                 ("hands in the zone while the machine runs: " + "; ".join(bad)) if bad else "hands clear during automatic steps"))
    # TRC-1 one VIN scan
    n = sum(1 for s in steps if s["kind"] == "scan" and s["every"] >= 1)
    msg = {0: "no VIN scan - the car is not traceable at this station",
           1: "one VIN scan per car"}.get(n, f"{n} VIN scans per car - MES books the car {n} times (double counts, wrong genealogy)")
    out.append(R("TRACE", "TRC-1", "PASS" if n == 1 else "BLOCK", msg, n, 1))
    return out


# ---------------------------------------------------------------------------------------------
# People rules: steps + qualification records (+ sign-offs and cycles for history)
# ---------------------------------------------------------------------------------------------
def check_people(steps: list[dict], quals: pd.DataFrame) -> list[dict]:
    out = []
    need = sorted({s["qualification"] for s in steps if is_operator(s) and v(s["qualification"])})
    ops = quals.groupby("operator_id").agg(crew=("crew", "first"), skill=("skill", "first"),
                                           q=("qualification", set)).reset_index()
    gaps = {}
    for o in ops.itertuples():
        miss = [q for q in need if q not in o.q]
        if miss:
            gaps.setdefault(o.crew, []).append((o.operator_id, miss))
    worst = max((len(g) for g in gaps.values()), default=0)
    if gaps:
        quals_missing = sorted({q for g in gaps.values() for _, m in g for q in m})
        msg = (f"needs training on {', '.join(quals_missing)}: "
               + "; ".join(f"crew {c} {len(g)}/4 ({', '.join(o for o, _ in g)})" for c, g in sorted(gaps.items())))
    else:
        msg = f"all {len(ops)} operators in the rotation hold the {len(need)} qualifications"
    out.append(R("PEOPLE", "TRN-1", "BLOCK" if worst >= 2 else "WARN" if worst == 1 else "PASS", msg, worst, 1))

    low = []
    for s in steps:
        if is_operator(s) and (v(s["min_skill"]) or 1) > 1:
            below = ops[ops.skill < s["min_skill"]].operator_id.tolist()
            if below:
                low.append(f"{s['text']} needs skill {s['min_skill']}: {', '.join(below)}")
    out.append(R("PEOPLE", "TRN-2", "WARN" if low else "PASS",
                 ("pair with an experienced operator - " + "; ".join(low)) if low else "everyone meets the minimum skill"))
    return out


def check_history(sid, version, cycles: pd.DataFrame, signoffs: pd.DataFrame, planned_op: float,
                  incidents: pd.DataFrame | None, baseline: bool) -> list[dict]:
    out = []
    cyc = cycles[cycles.work_instruction == version]
    if not baseline and len(cyc):
        so = signoffs[signoffs.wi_version == version].set_index("operator_id").signed_ts
        before = cyc[cyc.ts < cyc.operator_id.map(so)]
        if len(before):
            who = before.operator_id.value_counts()
            msg = (f"{len(before)} cycles ({len(before) / len(cyc):.0%}) built before the operator signed {version}: "
                   + ", ".join(f"{o} {n}" for o, n in who.items()))
        else:
            msg = f"all operators signed {version} before their first cycle"
        out.append(R("PEOPLE", "TRN-3", "WARN" if len(before) else "PASS", msg, len(before), 0))
    if len(cyc) >= 30:
        cost = STATIONS[sid]["retry_cost_s"]
        net = (cyc.operator_time_s - cyc.retries.fillna(0) * cost).median()
        rel = net / planned_op - 1
        why = (" - the standard time is too optimistic" if rel > REALITY_WARN else
               " - faster than planned: check that every step is really done" if rel < -REALITY_WARN else "")
        out.append(R("REALITY", "REA-1", "WARN" if abs(rel) > REALITY_WARN else "PASS",
                     f"measured hands-on {net:.1f} s vs planned {planned_op:.1f} s ({rel:+.0%}) over {len(cyc)} cycles; "
                     f"median cycle {cyc.cycle_time_s.median():.1f} s{why}", round(rel, 3), REALITY_WARN))
    if incidents is not None and len(incidents):
        hit = incidents[incidents.culprit == version]
        if len(hit):
            shifts = ", ".join(f"{r.shift_date[5:]} {r.shift}" for r in hit.itertuples())
            out.append(R("REALITY", "REA-2", "INFO", f"cause finder blamed {version} in {len(hit)} incidents "
                         f"({hit.family.iloc[0]}; shifts {shifts})", len(hit)))
    return out


def verdict(checks: list[dict], groups=None) -> str:
    sts = [c["status"] for c in checks if groups is None or c["grp"] in groups]
    return "BLOCK" if "BLOCK" in sts else "WARN" if "WARN" in sts else "PASS"


def summarize(wi_version, sid, source, valid_from, note, steps, checks) -> dict:
    p = planned(steps)
    top = sorted([c for c in checks if c["status"] in ("BLOCK", "WARN")], key=lambda c: ORDER[c["status"]])
    return {"wi_version": wi_version, "station": sid, "source": source, "valid_from": valid_from, "change_note": note,
            "planned_op_s": p["op_s"], "planned_machine_s": p["machine_s"], "planned_cycle_s": p["cycle_s"],
            "team_lead_s": p["team_lead_s"], "takt_s": TAKT_S, "pct_takt": round(p["cycle_s"] / TAKT_S, 3),
            "design_verdict": verdict(checks, DESIGN_GROUPS), "verdict": verdict(checks),
            "n_block": sum(c["status"] == "BLOCK" for c in checks), "n_warn": sum(c["status"] == "WARN" for c in checks),
            "headline": " | ".join(f"{c['rule']} {c['message']}" for c in top[:2]) or "all rules pass",
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: v(x) for k, x in r.items()} for r in df.to_dict("records")]


# ---------------------------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------------------------
def load(con):
    wi = pd.read_sql("SELECT * FROM work_instructions ORDER BY station, version_no", con)
    steps = pd.read_sql("SELECT * FROM wi_steps ORDER BY station, wi_version, step_no", con)
    quals = pd.read_sql("SELECT * FROM qualifications", con)
    sign = pd.read_sql("SELECT * FROM wi_signoffs", con)
    return wi, steps, quals, sign


def steps_of(steps: pd.DataFrame, version: str) -> list[dict]:
    out = records(steps[steps.wi_version == version].sort_values("step_no"))
    for s in out:
        for b in ("reaction_arm", "lift_assist", "hands_in_zone", "critical", "control_plan"):
            s[b] = bool(s[b]) if s[b] is not None else None
    return out


def run_history(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    wi, steps, quals, sign = load(con)
    sign["signed_ts"] = pd.to_datetime(sign.signed_ts)
    try:
        inc = pd.read_sql("SELECT * FROM incidents", con)
    except Exception:
        inc = None
    rows, verdicts = [], []
    for sid in station_tables(con):
        cyc = pd.read_sql(f"SELECT ts, work_instruction, operator_id, operator_time_s, retries, cycle_time_s "
                          f"FROM {sid} WHERE event_type = 'CYCLE'", con)
        cyc["ts"] = pd.to_datetime(cyc.ts)
        prev = None
        for w in wi[wi.station == sid].itertuples():
            s = steps_of(steps, w.wi_version)
            checks = check_design(sid, s, prev) + check_people(s, quals)
            checks += check_history(sid, w.wi_version, cyc, sign, planned(s)["op_s"], inc, w.change_note == "Baseline")
            rows += [{"wi_version": w.wi_version, "station": sid, "source": "history", **c} for c in checks]
            verdicts.append(summarize(w.wi_version, sid, "history", w.valid_from, w.change_note, s, checks))
            prev = s
    return pd.DataFrame(rows), pd.DataFrame(verdicts)


def run_proposal(con, path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    prop = json.loads(Path(path).read_text())
    wi, steps, quals, _ = load(con)
    base = wi[wi.wi_version == prop["base"]]
    if base.empty:
        raise SystemExit(f"base version {prop['base']} not in the database")
    sid = base.station.iloc[0]
    prev = steps_of(steps, prop["base"])
    new = apply_changes([full_step(s) for s in prev], prop["changes"])
    checks = check_design(sid, new, prev) + check_people(new, quals)
    n_ops = quals.operator_id.nunique()
    checks.append(R("PEOPLE", "TRN-3", "INFO", f"all {n_ops} operators in the rotation must sign {prop['wi_version']} "
                    f"before {prop.get('rollout', 'rollout')}"))
    rows = [{"wi_version": prop["wi_version"], "station": sid, "source": "proposal", **c} for c in checks]
    ver = summarize(prop["wi_version"], sid, "proposal", prop.get("rollout"), prop.get("change_note", ""), new, checks)
    return pd.DataFrame(rows), pd.DataFrame([ver])


def save(con, checks: pd.DataFrame, verdicts: pd.DataFrame, source: str):
    for t in ("method_checks", "method_verdicts"):
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
        if exists:
            if source == "history":
                con.execute(f"DELETE FROM {t} WHERE source = 'history'")
            else:
                con.execute(f"DELETE FROM {t} WHERE wi_version IN ({','.join('?' * len(verdicts))})",
                            verdicts.wi_version.tolist())
    checks.to_sql("method_checks", con, if_exists="append", index=False)
    verdicts.to_sql("method_verdicts", con, if_exists="append", index=False)
    con.commit()


def report(checks: pd.DataFrame, verdicts: pd.DataFrame, detail: bool = True):
    for vd in verdicts.itertuples():
        when = "baseline" if vd.change_note == "Baseline" else str(vd.valid_from)[:16]
        print(f"\n{vd.wi_version:<10} {when:<17} {vd.verdict:<5}  {vd.planned_cycle_s:.1f} s ({vd.pct_takt:.0%} of takt)  "
              f"- {vd.change_note}")
        if not detail:
            continue
        c = checks[checks.wi_version == vd.wi_version].copy()
        c = c[c.status != "PASS"].sort_values("status", key=lambda s: s.map(ORDER))
        for r in c.itertuples():
            print(f"    {r.status:<5} {r.rule:<6} {r.message}")


# ---------------------------------------------------------------------------------------------
# Evaluation on random simulated weeks (the rules never see which version is bad)
# ---------------------------------------------------------------------------------------------
def eval_week(seed: int) -> list[dict]:
    from generate_data import build_week
    w = build_week(seed, 7, demo=False)
    md = build(w["tables"], w["causes"], seed)
    fam = md["_bad"]
    out = []
    for sid in md["work_instructions"].station.unique():
        prev = None
        for wv in md["work_instructions"][md["work_instructions"].station == sid].itertuples():
            s = steps_of(md["wi_steps"], wv.wi_version)
            ch = check_design(sid, s, prev)
            out.append({"seed": seed, "wi_version": wv.wi_version, "station": sid, "bad": wv.wi_version in md["_bad"],
                        "family": fam.get(wv.wi_version, "-"), "verdict": verdict(ch)})
            prev = s
    return out


def run_eval(n: int, workers: int):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        res = pd.DataFrame([r for rows in pool.map(eval_week, range(3000, 3000 + n)) for r in rows])
    res["flagged"] = res.verdict != "PASS"
    tp = int((res.bad & res.flagged).sum()); fn = int((res.bad & ~res.flagged).sum())
    fp = int((~res.bad & res.flagged).sum()); tn = int((~res.bad & ~res.flagged).sum())
    con = connect()
    pd.DataFrame({"key": ["weeks", "versions", "bad", "caught", "false_alarms", "harmless"],
                  "value": [n, len(res), int(res.bad.sum()), tp, fp, fp + tn]}).to_sql(
        "method_eval", con, if_exists="replace", index=False)
    con.close()
    print(f"{n} random weeks, {len(res)} WI versions ({int(res.bad.sum())} made worse by the simulator)")
    print(f"  caught {tp}/{tp + fn} bad versions, {fp} false alarms on {fp + tn} harmless versions")
    print("  bad versions by family and verdict:")
    print(res[res.bad].groupby(["family", "station", "verdict"]).size().to_string())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--propose", nargs="*", help="proposal JSON files to check")
    ap.add_argument("--eval", type=int, default=0, help="evaluate the design rules on N random weeks")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--brief", action="store_true", help="one line per version")
    args = ap.parse_args()
    if args.eval:
        return run_eval(args.eval, args.workers)
    con = connect()
    print(f"Method checker - takt {TAKT_S:.0f} s; rules: time, safety, traceability, people, reality")
    if args.propose:
        for p in args.propose:
            checks, verdicts = run_proposal(con, Path(p))
            save(con, checks, verdicts, "proposal")
            print(f"\nproposal {p}:", end="")
            report(checks, verdicts)
    else:
        checks, verdicts = run_history(con)
        save(con, checks, verdicts, "history")
        report(checks, verdicts, detail=not args.brief)
        n = verdicts.verdict.value_counts()
        print(f"\n{len(verdicts)} versions: " + ", ".join(f"{k} {n.get(k, 0)}" for k in ("BLOCK", "WARN", "PASS")))
    con.close()


if __name__ == "__main__":
    main()
