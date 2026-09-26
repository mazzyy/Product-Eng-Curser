"""
The agent's tools - every model in the prototype, as small read-only functions that return compact JSON.

The agent (agent.py) can only state what these return. Each result carries IDs the answer can cite
(incident #, case #, change C-xx, rule IDs). Nothing here changes the line: approving, stopping or
releasing stays with people - the tools only check and explain.

    python agent_tools.py morning_brief                      # try any tool from the shell
    python agent_tools.py explain_problem '{"incident_id": 13}'
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from station_db import DB_PATH, ROOT

MAX_ROWS = 40


def ro():
    """Read-only connection: the agent can look at everything and change nothing."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def q(sql, params=()) -> pd.DataFrame:
    con = ro()
    try:
        return pd.read_sql(sql, con, params=params)
    finally:
        con.close()


def has_table(name) -> bool:
    return not q("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).empty


def clean(x):
    """pandas / numpy -> plain JSON, NaN -> None, floats rounded."""
    if isinstance(x, pd.DataFrame):
        return [clean(r) for r in x.to_dict("records")]
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if hasattr(x, "item"):
        x = x.item()
    if isinstance(x, float):
        return None if math.isnan(x) else round(x, 3)
    return x


# ---------------------------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------------------------
def morning_brief() -> dict:
    """What needs the engineer's attention now: top problems, 7,500 status, alerts, what the last shifts wrote."""
    out = {}
    if has_table("impact_summary"):
        s = dict(q("SELECT key, value FROM impact_summary").values)
        out["week"] = {k: s.get(k) for k in ("week", "target_per_week", "cars_built", "attainment", "capacity",
                                               "bottleneck", "projected_next_week", "projected_attainment",
                                               "five_day_equivalent")}
    if has_table("impacts"):
        out["top_problems"] = clean(q(
            "SELECT rank, category, round(priority,1) priority, status, title, culprit, CAST(case_id AS INTEGER) case_id, rule, action "
            "FROM impacts WHERE category = 'High' ORDER BY rank LIMIT 6"))
    alerts = []
    if has_table("changes"):
        from change_manager import current_methods
        con = sqlite3.connect(DB_PATH)
        try:
            cm = current_methods(con)
        finally:
            con.close()
        for r in cm.itertuples():
            if r.alert != "-":
                alerts.append({"type": "method", "station": r.station, "text": r.alert})
            if r.do_not_use != "-":
                alerts.append({"type": "method", "station": r.station, "text": f"blocked, not for use: {r.do_not_use}"})
    if has_table("maint_plan"):
        m = q("SELECT equipment, failure_mode, policy_rec, next_due, p_fail_7d FROM maint_plan")
        for r in m.itertuples():
            if isinstance(r.next_due, str) and r.next_due.startswith("overdue"):
                alerts.append({"type": "maintenance", "text": f"{r.equipment}: {r.policy_rec} - {r.next_due}"})
            elif r.p_fail_7d and r.p_fail_7d >= 0.1:
                alerts.append({"type": "maintenance",
                               "text": f"{r.equipment} ({r.failure_mode}): {r.p_fail_7d:.0%} chance of failure in 7 days"})
    if has_table("containment_cases"):
        now = pd.Timestamp(q("SELECT MAX(ts) t FROM st012").t.iloc[0])
        c = q("SELECT case_id, culprit, action, why, window_end, \"check\" + hold + rework waiting "
              "FROM containment_cases")
        live = c[pd.to_datetime(c.window_end) >= now - pd.Timedelta(hours=24)]
        for r in live[live.action != "NO HOLD"].itertuples():
            alerts.append({"type": "containment", "case_id": r.case_id, "text": f"{r.culprit}: {r.action} - {r.why}"})
        waiting = c[c.waiting > 0]
        out["cars_waiting_for_check"] = {"cars": int(waiting.waiting.sum()), "cases": waiting.case_id.tolist(),
                                         "note": "held this week until checked and released by quality"}
    out["now"] = str(q("SELECT MAX(ts) t FROM st012").t.iloc[0])[:16]
    out["alerts"] = alerts
    if has_table("floor_facts"):
        f = q("SELECT note_id, shift_date, shift, station, category, subject, status, link, quote FROM floor_facts "
              "WHERE link IN ('early warning','notes only','disputes','unclear') AND status != 'info' "
              "ORDER BY shift_date DESC, shift DESC LIMIT 8")
        out["floor_highlights"] = clean(f)
    return clean(out)


def list_incidents(station=None, cause=None, date=None) -> dict:
    """Problems the cause finder found (optionally filtered)."""
    sql = ("SELECT incident_id, case_id, shift_date, shift, top_station station, symptom, cause, "
           "round(confidence,2) confidence, culprit FROM incidents WHERE 1=1")
    p = []
    if station:
        sql += " AND stations LIKE ?"
        p.append(f"%{station.lower()}%")
    if cause:
        sql += " AND cause = ?"
        p.append(cause.lower())
    if date:
        sql += " AND (shift_date = ? OR substr(start_ts,1,10) = ?)"
        p += [date, date]
    d = q(sql + " ORDER BY start_ts", tuple(p))
    return {"count": len(d), "incidents": clean(d.head(MAX_ROWS))}


def explain_problem(incident_id=None, case_id=None) -> dict:
    """Everything about one problem: cause and evidence, what the floor said, impact, containment, change, maintenance."""
    if incident_id is None and case_id is None:
        return {"error": "give incident_id or case_id"}
    if case_id is None:
        r = q("SELECT case_id FROM incidents WHERE incident_id = ?", (int(incident_id),))
        if r.empty:
            return {"error": f"no incident {incident_id}"}
        case_id = int(r.case_id.iloc[0])
    inc = q("SELECT incident_id, shift_date, shift, top_station station, symptom, cause, round(confidence,2) confidence, "
            "round(p_machine,2) p_machine, round(p_people,2) p_people, round(p_method,2) p_method, "
            "round(p_station,2) p_station, culprit, evidence, graph, start_ts, end_ts FROM incidents "
            "WHERE case_id = ? ORDER BY start_ts", (int(case_id),))
    if inc.empty:
        return {"error": f"no case {case_id}"}
    culprit = inc.culprit.iloc[0]
    out = {"case_id": int(case_id), "culprit": culprit, "cause": inc.cause.iloc[0],
           "first": inc.start_ts.iloc[0], "last": inc.end_ts.iloc[-1], "incidents": clean(inc.head(8)),
           "incident_count": len(inc)}
    ids = tuple(int(i) for i in inc.incident_id)
    if has_table("floor_facts"):
        ph = ",".join("?" * len(ids))
        out["floor_said"] = clean(q(f"SELECT note_id, kind, shift_date, shift, link, lead_h, confirms, answer_cause, "
                                    f"decision, quote FROM floor_facts WHERE incident_id IN ({ph})", ids))
    if has_table("impacts"):
        out["impact"] = clean(q("SELECT rank, category, round(priority,1) priority, status, title, rule, lost_cars, "
                                "definite_cars, action FROM impacts WHERE case_id = ?", (int(case_id),)))
    if has_table("containment_cases"):
        out["containment"] = clean(q("SELECT action, why, prevent, decision_ts, window_start, window_end, basis, "
                                     "cars_in_scope, rework, \"check\", hold, audit_sample, release, who_decides "
                                     "FROM containment_cases WHERE case_id = ?", (int(case_id),)))
    if has_table("changes") and re.match(r"WI-\d+ v\d+", str(culprit)):
        out["change_record"] = clean(q("SELECT change_id, state, verdict, blocks, outcome FROM changes "
                                       "WHERE wi_version = ?", (culprit,)))
    if has_table("maint_plan") and out["cause"] == "machine":
        out["maintenance"] = clean(q("SELECT equipment, failure_mode, policy_now, policy_rec, why, next_due FROM "
                                     "maint_plan WHERE equipment = ?", (culprit,)))
    if out["cause"] == "people":
        out["note"] = "people findings are for support (training, tools, workload), never ranking or blame"
    return clean(out)


def signal_check(station, start, end) -> dict:
    """Is a drop real or noise? Compares a time window with the rest of the week at one station."""
    st = station.lower().replace(" ", "")
    if not re.fullmatch(r"st\d{3}", st):
        return {"error": "station like st012"}
    d = q(f"SELECT ts, cycle_time_s, retries, result, anomaly_flag, anomaly_type, primary_value FROM {st} "
          f"WHERE event_type = 'CYCLE'")
    d["ts"] = pd.to_datetime(d.ts)
    w = d[(d.ts >= pd.Timestamp(start)) & (d.ts < pd.Timestamp(end))]
    rest = d.drop(w.index)
    if w.empty:
        return {"error": "no cycles in that window"}

    def kpis(x):
        return {"cycles": len(x), "median_cycle_s": x.cycle_time_s.median(), "retries_per_car": x.retries.mean(),
                "nok_rate": (x.result == "NOK").mean(), "flagged_rate": (x.anomaly_flag == 1).mean(),
                "primary_mean": x.primary_value.mean()}
    a, b = kpis(w), kpis(rest)
    ratio = a["flagged_rate"] / max(b["flagged_rate"], 1e-6)
    types = w[w.anomaly_flag == 1].anomaly_type.value_counts().to_dict()
    verdict = ("real - far more flagged cycles than normal" if ratio >= 3 and a["flagged_rate"] >= 0.05 else
               "probably real - more flags than normal" if ratio >= 1.5 else "noise - within normal variation")
    return clean({"station": st, "window": [start, end], "window_kpis": a, "rest_of_week": b,
                  "flag_ratio": ratio, "flag_types": types, "verdict": verdict})


def people_findings(operator_id=None) -> dict:
    """Findings of the people model per operator-shift, with the cause finder's cross-check."""
    sql = ("SELECT operator_id, shift_date, shift, finding_type, finding, cause_check FROM operator_shifts "
           "WHERE flag = 1")
    p = ()
    if operator_id:
        sql += " AND operator_id = ?"
        p = (operator_id.upper(),)
    d = q(sql + " ORDER BY shift_date, shift", p)
    return {"note": "support, not blame - pseudonymous IDs; talk to the team lead before acting",
            "count": len(d), "findings": clean(d.head(MAX_ROWS))}


def impacts(category=None, top=10) -> dict:
    """Ranked problem list (impact ranker) and the 7,500-cars-a-week check."""
    sql = ("SELECT rank, category, round(priority,1) priority, status, title, cause, culprit, CAST(case_id AS INTEGER) case_id, rule, "
           "round(lost_cars,1) lost_cars, round(definite_cars,1) definite_cars, action FROM impacts")
    p = ()
    if category:
        sql += " WHERE category = ?"
        p = (category.capitalize(),)
    d = q(sql + " ORDER BY rank LIMIT ?", p + (int(top or 10),))
    s = dict(q("SELECT key, value FROM impact_summary").values)
    s["biggest_capacity_losses"] = clean(q("SELECT rank, title, round(lost_cars,1) lost_cars, status FROM impacts "
                                           "WHERE lost_cars > 0 ORDER BY lost_cars DESC LIMIT 5"))
    return clean({"capacity_check": s, "problems": d})


def floor_notes(shift_date=None, shift=None, highlights_only=False) -> dict:
    """What the shift notes, maintenance log and supervisor answers said (floor listener)."""
    sql = ("SELECT note_id, kind, shift_date, shift, station, category, subject, status, severity, link, lead_h, "
           "incident_id, confirms, quote FROM floor_facts WHERE 1=1")
    p = []
    if shift_date:
        sql += " AND shift_date = ?"
        p.append(shift_date)
    if shift:
        sql += " AND shift = ?"
        p.append(shift.upper())
    if highlights_only:
        sql += " AND link IN ('early warning','notes only','disputes','unclear')"
    d = q(sql + " ORDER BY shift_date, shift, note_id", tuple(p))
    b = dict(q("SELECT key, value FROM floor_eval").values) if has_table("floor_eval") else {}
    return clean({"read_by": b.get("backend"), "count": len(d), "facts": d.head(MAX_ROWS)})


def method_check(wi_version) -> dict:
    """Method checker verdict for an existing WI version (takt, safety, traceability, training, reality)."""
    v = q("SELECT wi_version, station, source, valid_from, change_note, planned_cycle_s, pct_takt, verdict, headline "
          "FROM method_verdicts WHERE wi_version = ?", (wi_version,))
    if v.empty:
        return {"error": f"{wi_version} not checked - known: " + ", ".join(q("SELECT wi_version FROM method_verdicts").wi_version)}
    c = q("SELECT grp, rule, status, message FROM method_checks WHERE wi_version = ? AND status != 'PASS'", (wi_version,))
    return clean({"verdict": v, "rules_not_passed": c})


def check_proposal(proposal_json=None, proposal_file=None) -> dict:
    """Run a proposed WI change through the method checker and say who must approve it (nothing is saved)."""
    from change_manager import needed_roles, diff, run_check
    from method_checker import planned, verdict
    if not proposal_file and not proposal_json:
        return {"error": "give proposal_file (e.g. proposals/WI-013_v9.json) or proposal_json"}
    try:
        if proposal_file:
            path = (ROOT / proposal_file).resolve()
            if ROOT not in path.parents:
                return {"error": "proposal_file must be inside the project folder"}
            spec = json.loads(path.read_text())
        else:
            spec = json.loads(proposal_json)
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            sid, prev, new, checks = run_check(con, spec["base"], spec)
        finally:
            con.close()
    except Exception as e:
        return {"error": f"could not check the proposal: {e}"}
    p = planned(new)
    return clean({"wi_version": spec.get("wi_version"), "station": sid, "base": spec["base"],
                  "planned_cycle_s": p["cycle_s"], "hands_on_s": p["op_s"], "verdict": verdict(checks),
                  "rules_not_passed": [c for c in checks if c["status"] != "PASS"],
                  "approvals_needed": needed_roles(diff(prev, new), checks),
                  "next_step": "BLOCKED - fix and resubmit" if verdict(checks) == "BLOCK" else
                  "submit it in the change manager, then approvals -> pilot (one crew) -> release"})


def change_status() -> dict:
    """All WI changes and where they stand, plus the handover sheet (current method per station)."""
    if not has_table("changes"):
        return {"error": "run change_manager.py first"}
    from change_manager import current_methods
    ch = q("SELECT change_id, wi_version, station, source, state, verdict, needs, approvals, blocks, outcome "
           "FROM changes ORDER BY change_id")
    con = sqlite3.connect(DB_PATH)
    try:
        cm = current_methods(con)
    finally:
        con.close()
    return clean({"changes": ch, "handover": cm})


def containment_advice(case_id) -> dict:
    """Which cars wait for a check, which move on, and whether to recommend a stop - for one problem case."""
    c = q("SELECT * FROM containment_cases WHERE case_id = ?", (int(case_id),))
    if c.empty:
        return {"error": f"no containment for case {case_id}; cases: " +
                ", ".join(f"{r.case_id} {r.culprit}" for r in q("SELECT case_id, culprit FROM containment_cases").itertuples())}
    h = q("SELECT disposition, where_now, COUNT(*) n FROM car_holds WHERE case_id = ? GROUP BY 1, 2", (int(case_id),))
    ex = q("SELECT vin, vin_inferred, ts, disposition, reason FROM car_holds WHERE case_id = ? AND disposition IN "
           "('CHECK','REWORK') ORDER BY ts LIMIT 8", (int(case_id),))
    return clean({"case": c.iloc[0].to_dict(), "cars_by_disposition_and_place": h, "example_cars": ex})


def maintenance_plan(equipment=None) -> dict:
    """How often each machine should be checked or serviced, and what is due next (maintenance predictor)."""
    sql = ("SELECT station, equipment, failure_mode, kind, shape, mttf_h, policy_now, policy_rec, why, next_due, "
           "p_fail_7d, window_now_h, window_rec_h, cost_now_per_year, cost_rec_per_year FROM maint_plan")
    p = ()
    if equipment:
        sql += " WHERE equipment LIKE ?"
        p = (f"%{equipment}%",)
    return clean({"cost_unit": "labour minutes per year", "plan": q(sql, p)})


def run_sql(query) -> dict:
    """Read-only SQL for anything the other tools don't cover (SELECT only, 40 rows)."""
    s = query.strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", s) or ";" in s:
        return {"error": "only a single SELECT statement is allowed"}
    try:
        d = q(s)
    except Exception as e:
        return {"error": str(e)[:300]}
    return clean({"rows": d.head(MAX_ROWS), "total_rows": len(d)})


# ---------------------------------------------------------------------------------------------
# Schemas for the LLM (strict: every parameter listed, optional ones nullable)
# ---------------------------------------------------------------------------------------------
def _p(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


S, N, I, B = ({"type": ["string", "null"]}, {"type": ["number", "null"]}, {"type": ["integer", "null"]},
              {"type": ["boolean", "null"]})
TOOLS = {
    "morning_brief": (morning_brief, _p({})),
    "list_incidents": (list_incidents, _p({"station": {**S, "description": "st012 or st013"},
                                           "cause": {**S, "description": "machine, people, method or station"},
                                           "date": {**S, "description": "YYYY-MM-DD"}})),
    "explain_problem": (explain_problem, _p({"incident_id": I, "case_id": I})),
    "signal_check": (signal_check, _p({"station": {"type": "string"}, "start": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
                                       "end": {"type": "string", "description": "YYYY-MM-DD HH:MM"}})),
    "people_findings": (people_findings, _p({"operator_id": {**S, "description": "e.g. OP-C1"}})),
    "impacts": (impacts, _p({"category": {**S, "description": "High, Medium or Low"}, "top": I})),
    "floor_notes": (floor_notes, _p({"shift_date": S, "shift": {**S, "description": "A, B or C"}, "highlights_only": B})),
    "method_check": (method_check, _p({"wi_version": {"type": "string", "description": "e.g. WI-012 v4"}})),
    "check_proposal": (check_proposal, _p({
        "proposal_json": {**S, "description": 'JSON: {"wi_version","base","change_note","changes":[{"op":"add|remove|move|edit",...}]}'},
        "proposal_file": {**S, "description": "e.g. proposals/WI-013_v9.json"}})),
    "change_status": (change_status, _p({})),
    "containment_advice": (containment_advice, _p({"case_id": {"type": "integer"}})),
    "maintenance_plan": (maintenance_plan, _p({"equipment": {**S, "description": "e.g. NR-012"}})),
    "run_sql": (run_sql, _p({"query": {"type": "string"}})),
}


def schemas() -> list[dict]:
    return [{"type": "function", "name": n, "description": f.__doc__.strip().split("\n")[0], "parameters": p,
             "strict": True} for n, (f, p) in TOOLS.items()]


def call(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    try:
        return TOOLS[name][0](**{k: v for k, v in (args or {}).items() if k in TOOLS[name][1]["properties"]})
    except Exception as e:                      # a tool error goes back to the model, not a crash
        return {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "morning_brief"
    print(json.dumps(call(name, json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}), indent=1, ensure_ascii=False)[:6000])
