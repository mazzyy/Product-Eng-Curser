"""
Work-instruction steps and training records - the input of the method checker (model 5).

The simulator only stores the work-instruction (WI) *version* on each cycle. To check a method we
need what is inside a version: its steps, standard times, tools, hazards and the qualification
each step needs, plus who is trained on what. This module writes that, consistent with the
simulated week:

  - every WI version seen in the station tables gets a step list, built from the station's
    baseline steps (BASE_STEPS);
  - a version the simulator made worse (a "method" root cause in injected_causes.csv) gets the
    matching bad change: speed -> an extra manual check on every car, data -> a second VIN scan,
    quality -> the result check moved before the process step, pattern -> a control-plan
    preparation step removed;
  - all other versions get harmless wording/photo changes; the fix after a speed problem keeps
    the extra check as a 1-in-20 team-lead audit;
  - operator qualifications follow their skill level; WI sign-offs are mostly before the rollout,
    some are late (more often for rushed, bad versions).

The checker never sees which version is "bad" - it only reads the steps and records.

Tables: work_instructions, wi_steps, qualifications, wi_signoffs.

    python method_data.py            # after generate_data.py (reads the DB + injected_causes.csv)
"""
from __future__ import annotations

import re
from datetime import timedelta

import numpy as np
import pandas as pd

from station_db import CAUSES_PATH, STATIONS, connect, station_tables

AUTHOR = "Process engineering"
STEP_FIELDS = dict(role="operator", every=1.0, tool=None, tool_torque_nm=None, reaction_arm=None,
                   manual_torque_nm=None, lift_kg=None, lift_assist=None, hazard=None, ppe=None,
                   hands_in_zone=False, critical=False, control_plan=False, qualification=None, min_skill=1)

# Baseline steps. Manual times add up to the station's standard operator time (30 s / 18 s) and
# the automatic step is the machine time (17 s / 26 s), exactly as in the simulator.
BASE_STEPS = {
    "st012": [
        dict(key="pick", text="Pick 4 subframe bolts from the bin", kind="manual", time_s=3.0,
             qualification="ST012 basic"),
        dict(key="prep", text="Wipe the subframe mating surface", kind="manual", time_s=3.0,
             control_plan=True, qualification="ST012 basic"),
        dict(key="position", text="Position the subframe on the fixture pins with the lift assist", kind="manual",
             time_s=5.0, lift_kg=38.0, lift_assist=True, qualification="ST012 basic"),
        dict(key="handstart", text="Hand-start the 4 bolts", kind="manual", time_s=6.0, qualification="ST012 basic"),
        dict(key="process", text="Run nutrunner NR-012: 2-stage tightening to 120 Nm", kind="auto", time_s=17.0,
             tool="Nutrunner NR-012", tool_torque_nm=120.0, reaction_arm=True, critical=True, control_plan=True,
             qualification="Nutrunner NR-012", min_skill=2),
        dict(key="verify", text="Check the tool OK signal - result goes to MES", kind="verify", time_s=2.0,
             control_plan=True, qualification="ST012 basic"),
        dict(key="scan", text="Scan the VIN with SC-012", kind="scan", time_s=2.0, control_plan=True),
        dict(key="stripe", text="Paint a torque stripe on each bolt", kind="manual", time_s=4.0),
        dict(key="release", text="Unclamp and return the tool to the balancer", kind="manual", time_s=5.0),
    ],
    "st013": [
        dict(key="uncap", text="Remove the coolant cap", kind="manual", time_s=2.0),
        dict(key="prep", text="Check the coolant port is clean", kind="manual", time_s=1.5, control_plan=True),
        dict(key="connect", text="Connect fill head CF-013 to the coolant port", kind="manual", time_s=3.5,
             hazard="chemical", ppe="gloves, safety glasses", qualification="Coolant fill CF-013"),
        dict(key="scan", text="Scan the VIN with SC-013", kind="scan", time_s=2.0, control_plan=True),
        dict(key="process", text="Run vacuum, leak test and fill (LT-013 / CF-013)", kind="auto", time_s=26.0,
             critical=True, control_plan=True, qualification="Leak test LT-013", min_skill=2),
        dict(key="verify", text="Check the test result lamp - result goes to MES", kind="verify", time_s=2.0,
             control_plan=True, qualification="ST013 basic"),
        dict(key="disconnect", text="Disconnect the fill head and wipe drips", kind="manual", time_s=4.0,
             hazard="chemical", ppe="gloves, safety glasses", qualification="Coolant fill CF-013"),
        dict(key="cap", text="Fit the cap and check the click", kind="manual", time_s=3.0),
    ],
}
CONTROL_PLAN = {sid: [s["key"] for s in steps if s.get("control_plan")] for sid, steps in BASE_STEPS.items()}
BASIC_QUALS = ["ST012 basic", "ST013 basic", "Nutrunner NR-012", "Coolant fill CF-013", "Leak test LT-013"]
EXTRA_CHECK = 0.18      # the simulator's "added a manual check step" makes hands-on work 18% longer

CHECK_STEP = {
    "st012": dict(key="recheck", text="Re-check all 4 bolts with a click wrench (every car)", kind="manual",
                  manual_torque_nm=120.0, qualification="Click wrench torque check"),
    "st013": dict(key="recheck", text="Re-check the coolant level with a dipstick (every car)", kind="manual",
                  hazard="chemical", ppe="gloves, safety glasses", qualification="Coolant fill CF-013"),
}
BAD_NOTE = {"speed": "Added a manual check on every car",
            "data": "Added a VIN confirmation scan after the test",
            "quality": "Changed the work sequence: result check moved earlier",
            "pattern": "Removed a preparation step to save time"}


def full_step(d: dict) -> dict:
    return {**STEP_FIELDS, **d}


def base_steps(sid: str) -> list[dict]:
    return [full_step(s) for s in BASE_STEPS[sid]]


def apply_changes(steps: list[dict], changes: list[dict]) -> list[dict]:
    """Apply engineering changes to a step list. Used for simulated versions and for proposals.
    op: add (after=key), remove (key), move (key, before=key), edit (key, set={...})."""
    steps = [dict(s) for s in steps]
    keys = lambda: [s["key"] for s in steps]
    for ch in changes:
        op = ch["op"]
        if op == "add":
            i = keys().index(ch["after"]) + 1 if ch.get("after") in keys() else len(steps)
            steps.insert(i, full_step(ch["step"]))
        elif op == "remove":
            steps = [s for s in steps if s["key"] != ch["key"]]
        elif op == "move" and ch["key"] in keys():
            s = steps.pop(keys().index(ch["key"]))
            steps.insert(keys().index(ch["before"]) if ch.get("before") in keys() else len(steps), s)
        elif op == "edit" and ch["key"] in keys():
            steps[keys().index(ch["key"])].update(ch["set"])
        else:
            raise ValueError(f"cannot apply change {ch}")
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    return steps


def bad_changes(sid: str, family: str) -> list[dict]:
    if family == "speed":
        op_s = STATIONS[sid]["operator_time_s"]
        return [{"op": "add", "after": "verify", "step": {**CHECK_STEP[sid], "time_s": round(EXTRA_CHECK * op_s, 1)}}]
    if family == "data":
        scanner = "SC-012" if sid == "st012" else "SC-013"
        return [{"op": "add", "after": "process",
                 "step": dict(key="scan2", text=f"Scan the VIN again with {scanner} to confirm the result",
                              kind="scan", time_s=2.0)}]
    if family == "quality":
        return [{"op": "move", "key": "verify", "before": "process"}]
    if family == "pattern":
        return [{"op": "remove", "key": "prep"}]
    raise ValueError(family)


def audit_step(sid: str) -> dict:
    s = dict(CHECK_STEP[sid])
    s.update(key="audit", text=s["text"].replace("Re-check", "Audit").replace("(every car)", "on 1 in 20 cars"),
             kind="verify", role="team lead", every=0.05, time_s=round(EXTRA_CHECK * STATIONS[sid]["operator_time_s"], 1))
    return s


def vnum(v: str) -> int:
    return int(re.search(r"v(\d+)", v).group(1))


def version_spans(tables: dict) -> pd.DataFrame:
    rows = []
    for sid, df in tables.items():
        cyc = df[df.event_type == "CYCLE"]
        for v, g in cyc.groupby("work_instruction"):
            rows.append({"wi_version": v, "station": sid, "version_no": vnum(v),
                         "first_cycle": pd.to_datetime(g.ts).min(), "last_cycle": pd.to_datetime(g.ts).max()})
    out = pd.DataFrame(rows).sort_values(["station", "version_no"]).reset_index(drop=True)
    return out


def operators(tables: dict) -> pd.DataFrame:
    cyc = pd.concat([df[df.event_type == "CYCLE"][["operator_id", "operator_skill"]] for df in tables.values()])
    ops = cyc.dropna().groupby("operator_id").operator_skill.agg(lambda s: int(s.mode().iloc[0])).reset_index()
    ops.columns = ["operator_id", "skill"]
    ops["crew"] = ops.operator_id.str[3]
    return ops


def build(tables: dict, causes: pd.DataFrame, seed: int = 7) -> dict:
    """Everything the method checker needs for one simulated week, as DataFrames."""
    rng = np.random.default_rng(seed)
    spans = version_spans(tables)
    meth = causes[causes.cause == "method"].copy()
    meth["start"], meth["end"] = pd.to_datetime(meth.start), pd.to_datetime(meth.end)
    week_start = spans.first_cycle.min().normalize() + timedelta(hours=6)

    wis, steps, bad_versions = [], [], {}
    for sid, g in spans.groupby("station"):
        g = g.sort_values("version_no").reset_index(drop=True)
        for i, r in g.iterrows():
            valid_from = week_start - timedelta(days=30) if i == 0 else r.first_cycle.floor("min")
            valid_to = g.first_cycle[i + 1].floor("min") if i + 1 < len(g) else None
            # method problems active when this version starts
            here = meth[meth.stations.str.contains(sid) & (meth.start <= valid_from + timedelta(minutes=5))
                        & (meth.end > valid_from + timedelta(minutes=5))]
            ended = meth[meth.stations.str.contains(sid) & ((meth.end - valid_from).abs() <= timedelta(minutes=10))]
            s = base_steps(sid)
            if i == 0:
                note = "Baseline"
            elif len(here):
                for fam in here.family:
                    s = apply_changes(s, bad_changes(sid, fam))
                note = "; ".join(BAD_NOTE[f] for f in here.family)
                bad_versions[r.wi_version] = ",".join(here.family)
            elif len(ended) and (ended.family == "speed").any():
                s = apply_changes(s, [{"op": "add", "after": "verify", "step": audit_step(sid)}])
                note = "Fix: every-car check replaced by a 1-in-20 team-lead audit"
            elif len(ended):
                note = "Fix: back to the previous sequence"
            else:
                key = "position" if sid == "st012" else "connect"
                text = next(x["text"] for x in s if x["key"] == key)
                s = apply_changes(s, [{"op": "edit", "key": key, "set": {"text": text + " (new photo)"}}])
                note = "Updated photos and wording"
            s = apply_changes(s, [])
            wis.append({"wi_version": r.wi_version, "station": sid, "version_no": int(r.version_no),
                        "valid_from": valid_from, "valid_to": valid_to, "change_note": note, "author": AUTHOR})
            steps += [{"wi_version": r.wi_version, "station": sid, **x} for x in s]

    ops = operators(tables)
    quals = []
    for o in ops.itertuples():
        since = week_start - timedelta(days=int(rng.integers(60, 700)))
        quals += [{"operator_id": o.operator_id, "crew": o.crew, "skill": o.skill, "qualification": q,
                   "since": since} for q in BASIC_QUALS]
        if o.skill >= 3:     # only experienced operators are certified for manual torque checks
            quals.append({"operator_id": o.operator_id, "crew": o.crew, "skill": o.skill,
                          "qualification": "Click wrench torque check",
                          "since": week_start - timedelta(days=int(rng.integers(30, 400)))})

    sign = []
    wi_df = pd.DataFrame(wis)
    for w in wi_df.itertuples():
        for o in ops.itertuples():
            if w.change_note == "Baseline":
                t = w.valid_from - timedelta(days=float(rng.uniform(1, 20)))
            elif rng.random() < (0.35 if w.wi_version in bad_versions else 0.05):
                t = w.valid_from + timedelta(hours=float(rng.uniform(10, 40)))       # signed late
            else:
                t = w.valid_from - timedelta(hours=float(rng.uniform(2, 48)))
            sign.append({"operator_id": o.operator_id, "wi_version": w.wi_version, "signed_ts": t.floor("min")})

    return {"work_instructions": wi_df, "wi_steps": pd.DataFrame(steps), "qualifications": pd.DataFrame(quals),
            "wi_signoffs": pd.DataFrame(sign), "_bad": bad_versions}


def fmt_ts(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


def main():
    con = connect()
    tables = {sid: pd.read_sql(f"SELECT ts, event_type, work_instruction, operator_id, operator_skill FROM {sid}", con)
              for sid in station_tables(con)}
    causes = pd.read_csv(CAUSES_PATH)
    out = build(tables, causes)
    for name in ("work_instructions", "wi_steps", "qualifications", "wi_signoffs"):
        fmt_ts(out[name]).to_sql(name, con, if_exists="replace", index=False)
    con.commit()
    con.close()
    wi = out["work_instructions"]
    print(f"work instructions: {len(wi)} versions, {len(out['wi_steps'])} steps; "
          f"{out['qualifications'].operator_id.nunique()} operators, {len(out['qualifications'])} qualifications, "
          f"{len(out['wi_signoffs'])} sign-offs")
    for r in wi.itertuples():
        print(f"  {r.wi_version:<10} from {str(r.valid_from)[:16]}  {r.change_note}")


if __name__ == "__main__":
    main()
