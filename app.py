"""
TAKT (production engineer copilot) - the app. A FastAPI backend over all nine models plus the agent, and the
frontend in frontend/ (plain HTML/CSS/JS, no build step, works offline).

    pip install -r requirements.txt
    python app.py                      # opens http://127.0.0.1:8000 (or the next free port)

Everything the UI shows comes from data/factory.db (run the pipeline first, see README).
The copilot uses Azure GPT-5 when .env has a key, otherwise its cache / offline router.
Change-manager actions (submit, approve, sign, pilot, release) write to the database;
everything else is read-only.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent_tools as T
import change_manager as CM
import live
from containment import ACTION_LABEL, ACTION_SEV
from agent import DEMO_QUESTIONS, Agent
from station_db import DB_PATH, ROOT, STATIONS, connect

FRONTEND = ROOT / "frontend"
app = FastAPI(title="TAKT - production copilot")


def ok(obj) -> JSONResponse:
    return JSONResponse(T.clean(obj))


def rows(sql, params=()) -> list[dict]:
    return T.clean(T.q(sql, params))


@lru_cache(maxsize=1)
def now_ts() -> pd.Timestamp:
    return pd.Timestamp(T.q("SELECT MAX(ts) t FROM st012").t.iloc[0]).ceil("h")


@lru_cache(maxsize=1)
def week_start() -> pd.Timestamp:
    return pd.Timestamp(T.q("SELECT MIN(ts) t FROM st012").t.iloc[0]).floor("D") + timedelta(hours=6)


def prod_day(ts: pd.Series) -> pd.Series:
    return (ts - timedelta(hours=6)).dt.floor("D")


# ---------------------------------------------------------------------------------------------
# Line data (static for the simulated week -> cached)
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=4)
def cycles(sid: str) -> pd.DataFrame:
    d = T.q(f"SELECT ts, cycle_time_s, retries, result, anomaly_flag, primary_value, vin FROM {sid} "
            f"WHERE event_type = 'CYCLE'")
    d["ts"] = pd.to_datetime(d.ts)
    return d


@lru_cache(maxsize=1)
def hourly() -> dict:
    out = {}
    for sid, cfg in STATIONS.items():
        d = cycles(sid)
        g = d.groupby(d.ts.dt.floor("h"))
        h = pd.DataFrame({"cycles": g.size(), "ct_med": g.cycle_time_s.median(), "prim": g.primary_value.mean(),
                          "flag_rate": g.anomaly_flag.mean(), "nok": g.result.apply(lambda r: int((r == "NOK").sum())),
                          "retries": g.retries.mean()}).reset_index().rename(columns={"ts": "t"})
        mu, sd = h.prim.mean(), h.prim.std() or 1
        h["prim_z"] = (h.prim - mu) / sd
        h["t"] = h.t.dt.strftime("%Y-%m-%d %H:%M")
        p = cfg["primary"]
        out[sid] = {"title": cfg["title"], "severity": cfg["severity"], "signal": p["name"], "unit": p["unit"],
                    "hours": T.clean(h)}
    return out


@lru_cache(maxsize=1)
def cars_per_day() -> list[dict]:
    d = cycles("st013")
    g = d.groupby(prod_day(d.ts)).size()
    return [{"day": k.strftime("%Y-%m-%d"), "label": k.strftime("%a %d.%m"), "cars": int(v)} for k, v in g.items()]


@lru_cache(maxsize=1)
def cars_per_hour() -> list[dict]:
    d = cycles("st013")
    g = d.groupby(d.ts.dt.floor("h")).size().cumsum()
    return [{"t": k.strftime("%Y-%m-%d %H:%M"), "cum": int(v)} for k, v in g.items()]


# ---------------------------------------------------------------------------------------------
# Meta, overview, alerts
# ---------------------------------------------------------------------------------------------
@app.get("/api/meta")
def meta():
    ag = Agent()
    return ok({"now": now_ts().strftime("%Y-%m-%d %H:%M"), "week_start": week_start().strftime("%Y-%m-%d %H:%M"),
               "stations": [{"id": s, "title": c["title"], "severity": c["severity"]} for s, c in STATIONS.items()],
               "copilot": {"backend": ag.backend, "model": ag.cfg["model"], "cached_answers": len(ag.cache)},
               "floor_reader": dict(T.q("SELECT key, value FROM floor_eval").values).get("backend")
               if T.has_table("floor_eval") else None})


@app.get("/api/overview")
def overview():
    brief = T.morning_brief()
    s = dict(T.q("SELECT key, value FROM impact_summary").values)
    imp = rows("SELECT impact_id, rank, category, round(priority,1) priority, round(score_now,1) score_now, "
               "round(score_next,1) score_next, rule, source, title, detail, stations, cause, culprit, "
               "CAST(case_id AS INTEGER) case_id, status, first_ts, last_ts, cars, round(risk_cars,1) risk_cars, "
               "known_bad, definite_cars, round(margin_used,2) margin_used, round(lost_cars,1) lost_cars, "
               "round(rework_h,1) rework_h, round(people_h,1) people_h, round(sq,1) sq, round(dl,1) dl, "
               "round(co,1) co, round(pe,1) pe, action FROM impacts ORDER BY rank")
    return ok({"summary": s, "cars_per_day": cars_per_day(), "queue": imp, "brief": brief,
               "handover": T.change_status().get("handover", [])})


def build_alerts() -> list[dict]:
    now = now_ts()
    out = []

    def add(aid, sev, title, detail, page, target=None, ts=None):
        out.append({"id": aid, "severity": sev, "title": title, "detail": detail, "page": page, "target": target,
                    "ts": ts or now.strftime("%Y-%m-%d %H:%M")})
    if T.has_table("changes"):
        for r in T.change_status()["handover"]:
            if r["alert"] != "-":
                add(f"method-{r['station']}-{r['current']}", "critical", f"{r['station'].upper()} runs a method that fails the check",
                    r["alert"], "change", r["station"])
            if r["do_not_use"] != "-":
                add(f"blocked-{r['do_not_use']}", "info", f"Blocked proposal: {r['do_not_use']}",
                    "Not for use on the line - fix and resubmit", "change")
    if T.has_table("containment_cases"):
        c = T.q("SELECT case_id, culprit, station, action, why, window_end, \"check\" + hold + rework waiting "
                "FROM containment_cases")
        live = c[(pd.to_datetime(c.window_end) >= now - timedelta(hours=24)) & (c.action != "NO HOLD")]
        for r in live.itertuples():
            add(f"contain-{r.case_id}", ACTION_SEV.get(r.action, "info"),
                f"{r.culprit}: {ACTION_LABEL.get(r.action, r.action)}", r.why, "contain", int(r.case_id))
        w = c[c.waiting > 0]
        if len(w):
            add("holds", "serious", f"{int(w.waiting.sum()):,} cars wait for a check",
                f"{len(w)} cases this week - quality releases them after the checks", "contain")
    if T.has_table("maint_plan"):
        for r in T.q("SELECT equipment, failure_mode, policy_rec, next_due, p_fail_7d FROM maint_plan").itertuples():
            if isinstance(r.next_due, str) and r.next_due.startswith("overdue"):
                add(f"maint-over-{r.equipment}-{r.failure_mode}", "serious", f"{r.equipment}: maintenance overdue",
                    f"{r.policy_rec} - {r.next_due}", "maintain", r.equipment)
            elif isinstance(r.next_due, str) and pd.Timestamp(r.next_due) <= now + timedelta(hours=24):
                add(f"maint-due-{r.equipment}-{r.failure_mode}", "warning", f"{r.equipment}: due {r.next_due[5:]}",
                    f"{r.policy_rec} ({r.failure_mode})", "maintain", r.equipment)
            if r.p_fail_7d and r.p_fail_7d >= 0.1:
                add(f"maint-risk-{r.equipment}-{r.failure_mode}", "warning",
                    f"{r.equipment}: {r.p_fail_7d:.0%} failure risk in 7 days", r.failure_mode, "maintain", r.equipment)
    if T.has_table("floor_facts"):
        f = T.q("SELECT note_id, shift_date, shift, station, subject, quote FROM floor_facts "
                "WHERE category = 'safety' AND status != 'fixed'")
        for r in f.itertuples():
            add(f"safety-{r.note_id}-{r.subject}", "warning", f"Safety from the floor: {r.subject}",
                f"{r.shift_date[5:]} {r.shift}: \"{r.quote}\"", "notes", int(r.note_id))
    if T.has_table("impacts"):
        for r in T.q("SELECT rank, title, status, action, CAST(case_id AS INTEGER) case_id FROM impacts "
                     "WHERE category = 'High' AND status IN ('active', 'recurring') ORDER BY rank LIMIT 4").itertuples():
            add(f"impact-{r.rank}-{r.title}", "serious", f"High priority, {r.status}: {r.title}", r.action, "today",
                None if pd.isna(r.case_id) else int(r.case_id))
    order = {"critical": 0, "serious": 1, "warning": 2, "info": 3}
    return sorted(out, key=lambda a: order[a["severity"]])


@app.get("/api/alerts")
def alerts():
    return ok(build_alerts())


# ---------------------------------------------------------------------------------------------
# Investigate
# ---------------------------------------------------------------------------------------------
@app.get("/api/timeline")
def timeline():
    inc = rows("SELECT incident_id, case_id, family, shift_date, shift, start_ts, end_ts, stations, top_station, "
               "symptom, cause, round(confidence,2) confidence, culprit FROM incidents ORDER BY start_ts")
    wi = rows("SELECT w.wi_version, w.station, w.valid_from, w.change_note, v.verdict FROM work_instructions w "
              "LEFT JOIN method_verdicts v ON v.wi_version = w.wi_version AND v.source = 'history' "
              "WHERE w.valid_from >= ? ORDER BY w.valid_from", (week_start().strftime("%Y-%m-%d %H:%M"),))
    reps = []
    for sid in STATIONS:
        for r in T.q(f"SELECT ts, comment FROM {sid} WHERE comment LIKE 'Unplanned repair:%'").itertuples():
            reps.append({"station": sid, "ts": r.ts, "text": r.comment.replace("Unplanned repair: ", "")})
    notes = rows("SELECT f.fact_id, f.note_id, n.written_ts ts, f.station, f.category, f.subject, f.status, f.link, "
                 "f.lead_h, f.incident_id, f.quote FROM floor_facts f JOIN floor_notes n USING (note_id) "
                 "WHERE f.link IN ('early warning', 'notes only', 'disputes', 'unclear') OR f.category = 'safety'") \
        if T.has_table("floor_facts") else []
    cont = rows("SELECT case_id, station, decision_ts, action FROM containment_cases") \
        if T.has_table("containment_cases") else []
    return ok({"start": week_start().strftime("%Y-%m-%d %H:%M"), "end": now_ts().strftime("%Y-%m-%d %H:%M"),
               "stations": hourly(), "incidents": inc, "wi_changes": wi, "repairs": reps, "notes": notes,
               "containment": cont})


@app.get("/api/case/{case_id}")
def case_detail(case_id: int):
    out = T.explain_problem(case_id=case_id)
    if "error" in out:
        raise HTTPException(404, out["error"])
    inc = T.q("SELECT top_station, start_ts, end_ts FROM incidents WHERE case_id = ?", (case_id,))
    sid = inc.top_station.mode().iloc[0]
    out["signal_check"] = T.signal_check(sid, str(inc.start_ts.min())[:16], str(inc.end_ts.max())[:16])
    if out["cause"] == "people":
        out["people"] = T.people_findings(out["culprit"])
    return ok(out)


@app.get("/api/people")
def people():
    return ok(T.people_findings())


# ---------------------------------------------------------------------------------------------
# Contain
# ---------------------------------------------------------------------------------------------
@app.get("/api/containment")
def containment():
    c = rows("SELECT * FROM containment_cases")
    rank = {"STOP": 0, "QUARANTINE BATCH": 1, "100% CHECK": 2, "ROLL BACK WI": 3, "FIX WI": 3, "HOLD + CHECK": 4,
            "MANUAL RECORD": 5, "SUPPORT": 6, "NO HOLD": 7}
    for r in c:
        r["action_label"], r["action_sev"] = ACTION_LABEL.get(r["action"], r["action"]), ACTION_SEV.get(r["action"], "info")
    return ok(sorted(c, key=lambda r: (rank.get(r["action"], 9), -(r["check"] or 0))))


@app.get("/api/containment/{case_id}")
def containment_case(case_id: int):
    c = rows("SELECT * FROM containment_cases WHERE case_id = ?", (case_id,))
    if not c:
        raise HTTPException(404, f"no case {case_id}")
    cars = rows("SELECT vin, vin_inferred, station, ts, disposition, reason, where_now FROM car_holds "
                "WHERE case_id = ? ORDER BY ts", (case_id,))
    c[0]["action_label"], c[0]["action_sev"] = ACTION_LABEL.get(c[0]["action"]), ACTION_SEV.get(c[0]["action"], "info")
    return ok({"case": c[0], "cars": cars})


# ---------------------------------------------------------------------------------------------
# Change (methods + change manager; the POSTs are the only writes)
# ---------------------------------------------------------------------------------------------
class Proposal(BaseModel):
    file: str | None = None
    spec: dict | None = None


class Action(BaseModel):
    role: str | None = None
    by: str | None = None
    accept: str | None = None
    crew: str | None = None


def proposal_spec(p: Proposal) -> dict:
    if p.spec:
        return p.spec
    if not p.file:
        raise HTTPException(400, "give a proposal file or spec")
    path = (ROOT / p.file).resolve()
    if ROOT not in path.parents or not path.exists():
        raise HTTPException(400, f"no proposal {p.file}")
    return json.loads(path.read_text())


@app.get("/api/methods")
def methods():
    v = rows("SELECT * FROM method_verdicts ORDER BY station, wi_version")
    c = rows("SELECT wi_version, source, grp, rule, status, message FROM method_checks")
    steps = rows("SELECT wi_version, step_no, key, text, kind, role, time_s, every, tool, manual_torque_nm, lift_kg, "
                 "lift_assist, hazard, ppe, critical, control_plan, qualification, min_skill FROM wi_steps "
                 "ORDER BY wi_version, step_no")
    ev = dict(T.q("SELECT key, value FROM method_eval").values) if T.has_table("method_eval") else {}
    from method_checker import TAKT_S
    return ok({"takt_s": TAKT_S, "versions": v, "checks": c, "steps": steps, "eval": ev})


@app.get("/api/changes")
def changes():
    st = T.change_status()
    ev = rows("SELECT * FROM change_events ORDER BY ts") if T.has_table("change_events") else []
    for c in st.get("changes", []):
        c["approvals"] = json.loads(c.get("approvals") or "{}")
    props = []
    for f in sorted((ROOT / "proposals").glob("*.json")):
        props.append({"file": f"proposals/{f.name}", "spec": json.loads(f.read_text())})
    return ok({**st, "events": ev, "proposals": props, "now": now_ts().strftime("%Y-%m-%d %H:%M")})


@app.post("/api/changes/check")
def change_check(p: Proposal):
    return ok(T.check_proposal(proposal_json=json.dumps(proposal_spec(p))))


def gate(fn):
    con = connect()
    try:
        CM.init(con)
        return ok(fn(con))
    except CM.GateError as e:
        return JSONResponse({"refused": str(e)}, status_code=409)
    finally:
        con.close()


@app.post("/api/changes/submit")
def change_submit(p: Proposal):
    spec = proposal_spec(p)
    return gate(lambda con: CM.submit(con, spec, "engineer", now_ts()))


@app.post("/api/changes/{cid}/approve")
def change_approve(cid: str, a: Action):
    return gate(lambda con: CM.approve(con, cid, a.role, a.by or a.role, a.accept, now_ts() + timedelta(hours=1)))


@app.post("/api/changes/{cid}/sign")
def change_sign(cid: str, a: Action):
    def run(con):
        c = CM.get(con, cid)
        when = pd.Timestamp(c["rollout_ts"]) - timedelta(hours=3) if c.get("rollout_ts") else now_ts()
        return {"signed": CM.sign(con, cid, a.crew or "A", when)}
    return gate(run)


@app.post("/api/changes/{cid}/pilot")
def change_pilot(cid: str, a: Action):
    return gate(lambda con: CM.pilot(con, cid, a.crew or "A"))


@app.post("/api/changes/{cid}/release")
def change_release(cid: str):
    return gate(lambda con: CM.release(con, cid))


@app.post("/api/changes/reset")
def change_reset():
    """Back to the start of the demo: the week's changes replayed, the proposals not yet submitted."""
    def run(con):
        CM.init(con, reset=True)
        props = [json.loads(f.read_text())["wi_version"] for f in (ROOT / "proposals").glob("*.json")]
        s = pd.read_sql("SELECT * FROM wi_signoffs", con)
        s[~s.wi_version.isin(props)].to_sql("wi_signoffs", con, if_exists="replace", index=False)
        return {"replayed": len(CM.replay(con))}
    return gate(run)


# ---------------------------------------------------------------------------------------------
# Maintain, capacity, notes
# ---------------------------------------------------------------------------------------------
@app.get("/api/maintenance")
def maintenance():
    plan = rows("SELECT * FROM maint_plan")
    now = now_ts()
    for p in plan:
        k, lam = p["shape"], p["scale_h"]
        tmax = max(2.2 * lam, (p.get("age_now_h") or 0) * 1.2, (p.get("interval_h") or 0) * 1.5)
        t = np.linspace(0, tmax, 80)
        p["curve"] = [{"t": round(float(x), 1), "r": round(float(math.exp(-(x / lam) ** k)), 4)} for x in t]
        p["b10_h"] = round(lam * (-math.log(0.9)) ** (1 / k), 1)
        nd = p.get("next_due")
        p["due_in_h"] = None if not isinstance(nd, str) or nd.startswith("overdue") else \
            round((pd.Timestamp(nd) - now).total_seconds() / 3600, 1)
        p["overdue"] = isinstance(nd, str) and nd.startswith("overdue")
    hist = rows("SELECT station, family, COUNT(*) runs, SUM(event = 'failure') failures FROM maint_history "
                "GROUP BY 1, 2")
    return ok({"now": now.strftime("%Y-%m-%d %H:%M"), "plan": plan, "history": hist})


@app.get("/api/capacity")
def capacity():
    s = dict(T.q("SELECT key, value FROM impact_summary").values)
    losses = rows("SELECT rank, category, title, cause, status, round(lost_cars,1) lost_cars FROM impacts "
                  "WHERE lost_cars > 0.5 ORDER BY lost_cars DESC")
    by_cause = {}
    for r in losses:
        by_cause[r["cause"] or "other"] = by_cause.get(r["cause"] or "other", 0) + r["lost_cars"]
    return ok({"summary": s, "cars_per_day": cars_per_day(), "losses": losses,
               "by_cause": [{"cause": k, "lost_cars": round(v, 1)} for k, v in sorted(by_cause.items(), key=lambda x: -x[1])],
               "stations": {sid: {"ct_med": float(cycles(sid).cycle_time_s.median())} for sid in STATIONS}})


@app.get("/api/notes")
def notes():
    n = rows("SELECT * FROM floor_notes ORDER BY written_ts")
    f = rows("SELECT * FROM floor_facts") if T.has_table("floor_facts") else []
    by = {}
    for x in f:
        by.setdefault(x["note_id"], []).append(x)
    for x in n:
        x["facts"] = by.get(x["note_id"], [])
    case_of = dict(T.q("SELECT incident_id, case_id FROM incidents").values)
    for x in n:
        x["case_id"] = case_of.get(x.get("incident_id"))
        for f in x["facts"]:
            f["case_id"] = case_of.get(f.get("incident_id"))
    ev = dict(T.q("SELECT key, value FROM floor_eval").values) if T.has_table("floor_eval") else {}
    return ok({"notes": n, "eval": ev})


# ---------------------------------------------------------------------------------------------
# Replay: the week as a stream of events, for the "play the week" demo
# ---------------------------------------------------------------------------------------------
@app.get("/api/replay")
def replay():
    ev = []

    def add(ts, kind, sev, station, title, detail, page, target=None):
        ev.append({"ts": str(ts)[:16], "kind": kind, "severity": sev, "station": station, "title": title,
                   "detail": detail, "page": page, "target": target})
    for r in T.q("SELECT w.wi_version, w.station, w.valid_from, w.change_note, v.verdict, v.headline "
                 "FROM work_instructions w JOIN method_verdicts v ON v.wi_version = w.wi_version AND v.source='history' "
                 "WHERE w.valid_from >= ?", (week_start().strftime("%Y-%m-%d %H:%M"),)).itertuples():
        add(r.valid_from, "method", "critical" if r.verdict == "BLOCK" else "info", r.station,
            f"{r.wi_version} goes live on {r.station.upper()}",
            f"{r.change_note}. Method check: {r.verdict}" + (f" - {r.headline.split('|')[0]}" if r.verdict != "PASS" else ""),
            "change", r.station)
    if T.has_table("floor_facts"):
        for r in T.q("SELECT n.written_ts, f.station, f.category, f.subject, f.link, f.lead_h, f.quote, n.note_id "
                     "FROM floor_facts f JOIN floor_notes n USING (note_id) WHERE n.kind != 'answer' AND "
                     "(f.link = 'early warning' OR (f.category = 'safety' AND f.status != 'fixed'))").itertuples():
            ew = r.link == "early warning"
            add(r.written_ts, "note", "warning", r.station, ("Early warning from the floor" if ew else "Safety note from the floor")
                + f": {r.subject or r.category}", f"\"{r.quote}\"", "notes", int(r.note_id))
    first = T.q("SELECT i.case_id, i.start_ts, i.symptom, i.cause, i.confidence, i.culprit, i.top_station, "
                "p.category FROM incidents i LEFT JOIN (SELECT case_id, MIN(category) category FROM impacts "
                "GROUP BY case_id) p ON p.case_id = i.case_id ORDER BY i.start_ts").groupby("case_id").head(1)
    for r in first.itertuples():
        add(pd.Timestamp(r.start_ts) + timedelta(hours=2), "incident", "serious" if r.category == "High" else "warning",
            r.top_station, f"Cause finder: {r.cause} - {r.culprit}", f"{r.symptom} ({r.confidence:.0%} sure)",
            "investigate", int(r.case_id))
    if T.has_table("containment_cases"):
        for r in T.q("SELECT case_id, station, decision_ts, action, why, culprit FROM containment_cases "
                     "WHERE action != 'NO HOLD'").itertuples():
            add(r.decision_ts, "containment", ACTION_SEV.get(r.action, "info"), r.station,
                f"Containment: {ACTION_LABEL.get(r.action, r.action)} - {r.culprit}", r.why, "contain", int(r.case_id))
    for sid in STATIONS:
        for r in T.q(f"SELECT ts, comment FROM {sid} WHERE comment LIKE 'Unplanned repair:%'").itertuples():
            add(r.ts, "repair", "good", sid, f"Maintenance: {r.comment.replace('Unplanned repair: ', '')}",
                f"{sid.upper()} back in normal condition", "maintain")
    ev.sort(key=lambda e: e["ts"])
    return ok({"start": week_start().strftime("%Y-%m-%d %H:%M"), "end": now_ts().strftime("%Y-%m-%d %H:%M"),
               "production": cars_per_hour(), "target": 7500, "events": ev})


# ---------------------------------------------------------------------------------------------
# Copilot
# ---------------------------------------------------------------------------------------------
SESSIONS: dict[str, Agent] = {}


class Ask(BaseModel):
    question: str
    session: str | None = None


# ---------------------------------------------------------------------------------------------
# How it works + the live line
# ---------------------------------------------------------------------------------------------
def count(sql: str):
    try:
        return int(T.q(sql).iloc[0, 0] or 0)
    except Exception:
        return None


@app.get("/api/flow")
def flow():
    """What each model produced for the week in the database - the numbers on the workflow diagram."""
    cyc = "(SELECT * FROM st012 UNION ALL SELECT * FROM st013)"
    return ok({
        "mes": count(f"SELECT COUNT(*) FROM {cyc} WHERE event_type = 'CYCLE'"),
        "notes": count("SELECT COUNT(*) FROM floor_notes"),
        "wi": count("SELECT COUNT(*) FROM work_instructions"),
        "mlog": count(f"SELECT COUNT(*) FROM {cyc} WHERE event_type = 'MAINTENANCE'"),
        "signal": count(f"SELECT COUNT(*) FROM {cyc} WHERE anomaly_flag = 1"),
        "people": count("SELECT COUNT(*) FROM operator_shifts WHERE finding_type IS NOT NULL"),
        "floor": count("SELECT COUNT(*) FROM floor_facts"),
        "method": count("SELECT COUNT(*) FROM method_verdicts"),
        "cause": count("SELECT COUNT(*) FROM incidents"),
        "impact": count("SELECT COUNT(*) FROM impacts"),
        "contain": count("SELECT COUNT(*) FROM car_holds"),
        "change": count("SELECT COUNT(*) FROM changes"),
        "maint": count("SELECT COUNT(*) FROM maint_plan"),
        "copilot": len(T.schemas()) if hasattr(T, "schemas") else 13,
        "app": len(build_alerts()),
    })


@app.get("/api/map")
def factory_map():
    """Everything that has a place on the site map: problem cases, other ranked problems, held cars,
    maintenance, work-instruction verdicts and what the floor wrote - with the station / equipment it
    belongs to. The frontend (js/map/site.js) turns stations, equipment and culprits into coordinates."""
    cases = []
    if T.has_table("incidents"):
        inc = T.q("SELECT * FROM incidents ORDER BY start_ts")
        imp = T.q("SELECT CAST(case_id AS INTEGER) case_id, category, round(priority,1) priority, status, action owner, "
                  "rule, round(risk_cars,1) risk_cars, round(lost_cars,1) lost_cars FROM impacts WHERE case_id IS NOT NULL") \
            if T.has_table("impacts") else pd.DataFrame()
        con = T.q("SELECT * FROM containment_cases") if T.has_table("containment_cases") else pd.DataFrame()
        where = T.q("SELECT case_id, where_now, disposition, COUNT(*) n FROM car_holds GROUP BY case_id, where_now, "
                    "disposition") if T.has_table("car_holds") else pd.DataFrame()
        for cid, g in inc.groupby("case_id"):
            r0 = g.iloc[-1]
            c = {"case_id": int(cid), "cause": r0.cause, "culprit": r0.culprit, "family": r0.family,
                 "station": g.top_station.mode().iloc[0],
                 "stations": sorted({x for v in g.stations for x in v.split(",")}), "incidents": len(g),
                 "first_ts": g.start_ts.min(), "last_ts": g.end_ts.max(), "confidence": float(g.confidence.max()),
                 "symptom": r0.symptom, "evidence": g.evidence.iloc[0]}
            if len(imp):
                m = imp[imp.case_id == cid]
                if len(m):
                    c.update(m.iloc[0].drop("case_id").to_dict())
            if len(con):
                m = con[con.case_id == cid]
                if len(m):
                    m = m.iloc[0]
                    c.update({"action": m.action, "action_label": ACTION_LABEL.get(m.action, m.action),
                              "action_sev": ACTION_SEV.get(m.action, "info"), "why": m.why, "decision_ts": m.decision_ts,
                              "check": int(m.check), "hold": int(m.hold), "rework": int(m.rework),
                              "window": f"{m.window_start} - {m.window_end}", "basis": m.basis})
            if len(where):
                w = where[where.case_id == cid]
                c["where"] = {k: int(v) for k, v in w.groupby("where_now").n.sum().items()}
            cases.append(c)
    other = rows("SELECT impact_id, category, round(priority,1) priority, source, title, detail, stations, status, "
                 "action, first_ts, last_ts FROM impacts WHERE case_id IS NULL AND category IN ('High', 'Medium') "
                 "ORDER BY rank") if T.has_table("impacts") else []
    maint = rows("SELECT station, equipment, family, failure_mode, policy_rec, interval_h, next_due, p_fail_7d, "
                 "last_renewal FROM maint_plan") if T.has_table("maint_plan") else []
    repairs = []
    for sid in STATIONS:
        for r in T.q(f"SELECT ts, comment, downtime_s FROM {sid} WHERE comment LIKE 'Unplanned repair:%'").itertuples():
            repairs.append({"station": sid, "ts": r.ts, "text": r.comment.replace("Unplanned repair: ", ""),
                            "downtime_min": round((r.downtime_s or 0) / 60)})
    methods = rows("SELECT v.wi_version, v.station, v.verdict, v.headline, w.valid_from, w.change_note "
                   "FROM method_verdicts v JOIN work_instructions w USING (wi_version) WHERE v.source = 'history' "
                   "ORDER BY w.valid_from") if T.has_table("method_verdicts") else []
    notes = rows("SELECT f.fact_id, f.note_id, n.written_ts ts, n.author, f.station, f.category, f.subject, f.status, "
                 "f.link, f.lead_h, f.incident_id, f.quote FROM floor_facts f JOIN floor_notes n USING (note_id) "
                 "WHERE f.station IS NOT NULL") if T.has_table("floor_facts") else []
    line = {}
    for sid, cfg in STATIONS.items():
        d = cycles(sid)
        line[sid] = {"title": cfg["title"], "severity": cfg["severity"], "cars": len(d),
                     "flags": int(d.anomaly_flag.fillna(0).sum()), "nok": int((d.result == "NOK").sum()),
                     "equipment": {f: {"name": e[0], "mode": e[1]} for f, e in cfg["equipment"].items()}}
    return ok({"start": week_start().strftime("%Y-%m-%d %H:%M"), "end": now_ts().strftime("%Y-%m-%d %H:%M"),
               "cases": cases, "other": other, "maint": maint, "repairs": repairs, "methods": methods,
               "notes": notes, "line": line, "alerts": build_alerts()})


class LiveStart(BaseModel):
    scenario: str = "demo"
    seed: int = 7
    speed: int = 3600
    backend: str = "auto"


class LiveControl(BaseModel):
    action: str | None = None
    speed: int | None = None


@app.post("/api/live/start")
def live_start(p: LiveStart):
    live.ENGINE.start("random" if p.scenario == "random" else "demo", p.seed, p.speed,
                      p.backend if p.backend in ("auto", "rules", "azure") else "auto")
    return ok(live.ENGINE.state(summary=True))


@app.post("/api/live/control")
def live_control(p: LiveControl):
    return ok(live.ENGINE.control(p.action, p.speed))


@app.get("/api/live/state")
def live_state(ev: int = 0, pt: int = 0):
    return ok(live.ENGINE.state(ev, pt))


# ---------------------------------------------------------------------------------------------
# Bridge to the gigafactory-monitor tablet HMI (its WebSocket "ML feed", see its README)
# ---------------------------------------------------------------------------------------------
HMI_SEV = {"critical": "critical", "serious": "high", "warning": "medium", "good": "low", "info": "low"}


def hmi_alert(e: dict) -> dict | None:
    """A live-line event -> the HMI's alert format (zoneId, type, severity, ...). Info events are skipped."""
    if e["sev"] == "info":
        return None
    text = f"{e['title']} {e['detail']}".lower()
    if e["node"] in ("maint",) or "nutrunner" in text or "fill head" in text or "leak tester" in text:
        typ = "equipment_failure"
    elif "safety" in text:
        typ = "safety_hazard"
    elif "supply" in text or "batch" in text:
        typ = "material_shortage"
    elif "vin" in text or "mes" in text or "stuck" in text or "sensor" in text:
        typ = "sensor_failure"
    else:
        typ = "quality_defect"
    st = str(e.get("station") or "")
    eq = next((w for w in ("NR-012", "FX-012", "SC-012", "CF-013", "LT-013", "SC-013") if w.lower() in text), None)
    return {"zoneId": "a109-general-assembly", "type": typ, "severity": HMI_SEV.get(e["sev"], "low"),
            "equipmentId": eq or (f"Line 1 {st.upper()}" if st else "Line 1 ST012-ST013"),
            "description": e["title"], "suggestedAction": e["detail"][:220],
            "timestamp": e["t"].replace(" ", "T") + ":00", "confidence": 0.9}


@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket):
    """Streams the live line's alerts to the gigafactory-monitor HMI (VITE_ALERT_WS_URL=ws://127.0.0.1:<port>/ws/alerts)."""
    await ws.accept()
    last = max(0, live.ENGINE.seq - 6)
    try:
        while True:
            st = live.ENGINE.state(last, 10 ** 12)
            if st.get("seq", 0) < last:            # the live week was restarted
                last = 0
            evs = [e for e in st.get("events", []) if e["seq"] > last]
            if evs:
                last = evs[-1]["seq"]
                msgs = [m for m in map(hmi_alert, evs) if m]
                if msgs:
                    await ws.send_json(msgs)
            await asyncio.sleep(0.8)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.get("/api/copilot/presets")
def presets():
    return ok({"questions": DEMO_QUESTIONS})


@app.post("/api/copilot/ask")
def ask(a: Ask):
    sid = a.session or uuid.uuid4().hex[:12]
    ag = SESSIONS.get(sid) or SESSIONS.setdefault(sid, Agent())
    out = ag.ask(a.question.strip()[:2000])
    return ok({**{k: out.get(k) for k in ("answer", "trace", "backend", "seconds")}, "session": sid})


# ---------------------------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND), name="static")

def port_taken(port: int) -> bool:
    """True if anything answers on this port - IPv4 or IPv6 (a Mac's "localhost" tries IPv6 first)."""
    import socket
    for fam, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            pass
    return False


if __name__ == "__main__":
    import argparse
    import threading
    import webbrowser

    import uvicorn
    ap = argparse.ArgumentParser(description="TAKT - production copilot app")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args()
    port = next((p for p in range(args.port, args.port + 20) if not port_taken(p)), None)
    if port is None:
        raise SystemExit(f"ports {args.port}-{args.port + 19} are all in use - try: python app.py --port 9000")
    url = f"http://127.0.0.1:{port}"
    print(f"\n  TAKT - production copilot is running  ->  {url}")
    if port != args.port:
        print(f"  (port {args.port} is used by another program on this computer, so the app uses {port})")
    print("  Keep this window open while you use the app. Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
