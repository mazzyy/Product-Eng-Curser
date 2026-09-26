"""
Agent (model 9) - the production engineer's copilot. Ask in plain words; it answers from the
other eight models, which it uses as tools (agent_tools.py), and shows its work.

  LLM       Azure OpenAI GPT-5 (Responses API) with function calling. It must call tools before
            stating a fact, cite IDs (incident #, case #, change C-xx, rule IDs), keep the engineer's
            boundaries (supervisors stop the line, quality releases cars, maintenance repairs), and
            never approve, stop or release anything itself.
  memory    follow-up questions continue the same conversation (previous_response_id).
  cache     every answer is saved in data/agent_cache.json with its tool trace. Asked again with no
            key (or --offline), it replays - so a live demo cannot be broken by the network.
  offline   with no key and no cached answer, a simple keyword router calls the right tool and
            prints its result, so the prototype still answers the core questions.

    python agent.py "What should I look at first this morning?"
    python agent.py --chat                  # conversation with follow-ups
    python agent.py --demo                  # the 7 demo questions (fills the cache for the frontend)
    python agent.py --trace "Should we stop ST012?"     # also print every tool call
Settings: the same .env as the floor listener (AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_MODEL).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd

from agent_tools import call as call_tool, q, schemas
from floor_listener import azure_settings, load_env, response_text
from station_db import DB_PATH, ROOT

CACHE_PATH = ROOT / "data" / "agent_cache.json"
PROMPT_VERSION = "agent-v1"
MAX_ROUNDS = 8               # tool-calling rounds per question
MAX_TOOL_CHARS = 16000       # a tool result is cut to this many characters before it goes to the model

SYSTEM = """You are the copilot of a production engineer on a car assembly line (Giga-style plant, simulated week
14.09-21.09.2026; "now" is Mon 21.09 06:00 unless the user says otherwise).
Stations: ST012 front subframe bolt-down with nutrunner NR-012 (safety-critical joint, severity 9) and ST013 coolant
fill and leak test (severity 8), rotating crews A/B/C, target 7,500 cars a week, takt 50 s.

The engineer decides HOW the work is done (methods, work instructions), finds WHY results are off (machine, people,
method or station - one investigation), decides whether a drop is real, which problem to work on first, recommends a
stop or a small setting change, tells planning what the line can still produce, and decides with quality whether a
car waits for a check. Others decide the rest: supervisors run the shift, assign people and decide an actual stop;
maintenance repairs; planning sets the schedule; quality releases held cars.

Rules:
1. Facts come only from tools. Call the tools you need before answering; never invent numbers, IDs, causes or quotes.
   If the tools do not have it, say so.
2. Cite what you use: incident #, case #, change C-xx, rule IDs (e.g. TRC-1), equipment IDs, short note quotes.
3. Keep it short (about 150 words unless asked for more), in this shape:
   first line = the answer; then "Why:" 2-4 bullets of evidence; then "Next:" the action and who decides it.
4. Keep what the data shows apart from what people wrote in notes, and say when they disagree.
5. People findings are for support (training, tools, workload), never blame or ranking. Use operator IDs only.
6. Never approve, release or stop anything yourself - say which gate or person is next.
7. If a question is outside production engineering, say briefly that you only cover this line."""

DEMO_QUESTIONS = [
    "What should I look at first this morning?",
    "Was the torque shift on ST012 early on Thursday 17.09 (00:00-08:30) real, and why did it happen?",
    "Should we have stopped ST012 for the nutrunner problem, and which cars have to wait for a check?",
    "Would the method checker have let WI-012 v4 through? And check my proposal proposals/WI-013_v9.json.",
    "Can we still hit 7,500 cars next week, and what costs us the most capacity?",
    "How often should we check the nutrunner, and what maintenance is due next?",
    "What did the shifts report that the data did not show?",
]


# ---------------------------------------------------------------------------------------------
# Azure Responses API with tools
# ---------------------------------------------------------------------------------------------
def post(body: dict, cfg: dict, retries: int = 3) -> dict:
    req = urllib.request.Request(f"{cfg['endpoint']}/responses", data=json.dumps(body).encode(), method="POST",
                                 headers={"api-key": cfg["key"], "Content-Type": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"Azure HTTP {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"cannot reach {cfg['endpoint']}: {e.reason}") from None


def preview(result) -> str:
    s = json.dumps(result, ensure_ascii=False)
    return s if len(s) <= 160 else s[:157] + "..."


def fingerprint() -> str:
    """Changes when the models' results change, so old cached answers are not replayed on new data."""
    parts = []
    for t, col in (("incidents", "COUNT(*)"), ("impacts", "COUNT(*)"), ("changes", "MAX(updated_ts)"),
                   ("floor_facts", "COUNT(*)"), ("containment_cases", "COUNT(*)"), ("maint_plan", "COUNT(*)")):
        try:
            parts.append(str(q(f"SELECT {col} v FROM {t}").v.iloc[0]))
        except Exception:
            parts.append("-")
    return "|".join(parts)


class Agent:
    def __init__(self, backend: str = "auto", use_cache: bool = True):
        load_env()
        self.cfg = azure_settings()
        self.backend = backend if backend != "auto" else ("azure" if self.cfg["key"] else "offline")
        if self.backend == "azure" and not self.cfg["key"]:
            raise SystemExit("AZURE_OPENAI_API_KEY is not set (environment or .env) - or use --offline")
        self.use_cache = use_cache
        self.cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
        self.prev_id = None           # the conversation so far, kept by Azure
        self.asked = []               # questions so far, for the cache key

    def key(self, question: str) -> str:
        raw = "|".join([PROMPT_VERSION, self.cfg["endpoint"], self.cfg["model"], fingerprint(), *self.asked, question])
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def ask(self, question: str) -> dict:
        t0 = time.time()
        k = self.key(question)
        hit = self.cache.get(k)
        if hit and (self.use_cache or not self.cfg["key"]) and (self.backend != "azure" or self.use_cache):
            out = {**hit, "backend": "cache (" + hit.get("backend", "?") + ")", "seconds": round(time.time() - t0, 2)}
            self.prev_id = hit.get("response_id")
        elif self.backend == "azure":
            try:
                out = self._azure(question)
            except Exception as e:             # network / quota trouble must not end the demo
                out = hit or offline_answer(question)
                out = {**out, "backend": ("cache (" + hit["backend"] + ")" if hit else "offline") +
                       f" - Azure failed: {str(e)[:120]}"}
        else:
            out = offline_answer(question)
        out["seconds"] = out.get("seconds") or round(time.time() - t0, 1)
        self.asked.append(question)
        if out["backend"] == "azure":
            self.cache[k] = {k2: out[k2] for k2 in ("answer", "trace", "backend", "response_id")}
            CACHE_PATH.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False))
        log(question, out)
        return out

    def _azure(self, question: str) -> dict:
        t0 = time.time()
        base = {"model": self.cfg["model"], "instructions": SYSTEM, "tools": schemas(), "max_output_tokens": 6000}
        if self.cfg["reasoning"]:
            base["reasoning"] = {"effort": self.cfg["reasoning"]}
        body = {**base, "input": [{"role": "user", "content": question}]}
        if self.prev_id:
            body["previous_response_id"] = self.prev_id
        resp, trace = post(body, self.cfg), []
        for _ in range(MAX_ROUNDS):
            calls = [i for i in resp.get("output", []) if i.get("type") == "function_call"]
            if not calls:
                break
            outputs = []
            for c in calls:
                try:
                    args = json.loads(c.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = call_tool(c["name"], args)
                trace.append({"tool": c["name"], "args": args, "result": preview(result)})
                outputs.append({"type": "function_call_output", "call_id": c["call_id"],
                                "output": json.dumps(result, ensure_ascii=False)[:MAX_TOOL_CHARS]})
            resp = post({**base, "previous_response_id": resp["id"], "input": outputs}, self.cfg)
        self.prev_id = resp.get("id")
        try:
            answer = response_text(resp)
        except RuntimeError as e:
            answer = f"(no answer: {e})"
        return {"answer": answer.strip(), "trace": trace, "backend": "azure", "response_id": self.prev_id,
                "seconds": round(time.time() - t0, 1)}


def log(question: str, out: dict):
    try:
        con = sqlite3.connect(DB_PATH)
        pd.DataFrame([{"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "question": question,
                       "answer": out["answer"], "tools": json.dumps([t["tool"] for t in out.get("trace", [])]),
                       "backend": out["backend"], "seconds": out.get("seconds")}]).to_sql(
            "agent_log", con, if_exists="append", index=False)
        con.commit()
        con.close()
    except Exception:
        pass                          # logging must never break an answer


# ---------------------------------------------------------------------------------------------
# Offline fallback: keyword router -> one or two tools -> plain summary (no LLM)
# ---------------------------------------------------------------------------------------------
def _case_for(text: str):
    cases = q("SELECT case_id, culprit, action FROM containment_cases")
    for r in cases.itertuples():
        ids = re.findall(r"[A-Z]{2}-\d{3,4}|WI-\d+ v\d+|OP-[ABC]\d", r.culprit)
        if any(i.lower() in text.lower() for i in ids) or r.culprit.lower().split()[0] in text.lower():
            return int(r.case_id)
    stop = cases[cases.action == "STOP"]
    return int(stop.case_id.iloc[0]) if len(stop) else None


def offline_answer(question: str) -> dict:
    ql, trace, lines = question.lower(), [], []

    def use(name, args):
        r = call_tool(name, args)
        trace.append({"tool": name, "args": args, "result": preview(r)})
        return r
    m = re.search(r"proposals/[\w\-.]+\.json", question)
    if m:
        r = use("check_proposal", {"proposal_file": m.group(0), "proposal_json": None})
        lines.append(f"{r.get('wi_version')}: {r.get('verdict')} ({r.get('planned_cycle_s')} s planned). "
                     + "; ".join(f"{c['rule']} {c['message']}" for c in r.get("rules_not_passed", []) if c["status"] != "INFO")
                     + f". Approvals needed: {', '.join(r.get('approvals_needed', []))}. Next: {r.get('next_step')}.")
    wi = re.search(r"WI-\d+ v\d+", question)
    if wi:
        r = use("method_check", {"wi_version": wi.group(0)})
        v = (r.get("verdict") or [{}])[0]
        lines.append(f"{wi.group(0)}: {v.get('verdict')} - {v.get('headline')}")
    if re.search(r"stop|hold|contain|which cars|wait for", ql):
        cid = _case_for(question)
        if cid:
            r = use("containment_advice", {"case_id": cid})["case"]
            lines.append(f"Case {cid} ({r['culprit']}): {r['action']} - {r['why']}. Scope {r['cars_in_scope']} cars "
                         f"({r['basis']}): check {r['check']}, hold {r['hold']} for a {r['audit_sample']}-car audit, "
                         f"rework {r['rework']}. {r['who_decides']}." + (f" {r['prevent']}." if r["prevent"] != "-" else ""))
    if re.search(r"7,?500|capacity|next week|target", ql):
        s = use("impacts", {"category": "High", "top": 3})
        c = s["capacity_check"]
        lines.append(f"Built {c['cars_built']} of {c['target_per_week']} ({c['attainment']}); capacity {c['capacity']}, "
                     f"next week ~{c['projected_next_week']} ({c['projected_attainment']}); a 5-day week would give "
                     f"{c['five_day_equivalent']}. Biggest capacity losses: "
                     + "; ".join(f"{p['title']} ({p['lost_cars']:.0f} cars, {p['status']})"
                                 for p in c["biggest_capacity_losses"][:3]))
    if re.search(r"maint|how often|due", ql):
        eq = re.search(r"(NR|FX|SC|CF|LT)-0\d\d", question.upper()) or (re.search("nutrunner", ql) and "NR-012")
        r = use("maintenance_plan", {"equipment": eq if isinstance(eq, str) else eq.group(0) if eq else None})
        lines += [f"{p['equipment']} ({p['failure_mode']}): {p['policy_now']} -> {p['policy_rec']}; next due {p['next_due']}"
                  for p in r["plan"]]
    if re.search(r"report|notes?|shift said|floor|did not show|didn't show", ql):
        r = use("floor_notes", {"shift_date": None, "shift": None, "highlights_only": True})
        lines += [f"{f['shift_date'][5:]} {f['shift']} {f['link']}: {f['subject'] or f['category']} - \"{f['quote']}\""
                  for f in r["facts"]]
    inc = re.search(r"#(\d+)|incident (\d+)", ql)
    if inc or re.search(r"why|real|cause|explain", ql):
        iid = int(next(g for g in inc.groups() if g)) if inc else None
        cid = None if iid else _case_for(question)
        if iid or cid:
            r = use("explain_problem", {"incident_id": iid, "case_id": cid})
            i0 = r["incidents"][0]
            lines.append(f"Case {r['case_id']}: {r['cause']} - {r['culprit']} (confidence {i0['confidence']:.0%}). "
                         f"{i0['symptom']}. Evidence: {i0['evidence']}."
                         + "".join(f" Floor ({f['link']}): \"{f['quote']}\"." for f in (r.get("floor_said") or [])[:2]))
    if not lines or re.search(r"first|today|morning|brief|priorit", ql):
        b = use("morning_brief", {})
        lines.insert(0, "Top problems: " + "; ".join(f"#{p['rank']} {p['title']} ({p['status']})" for p in b["top_problems"][:4]))
        lines.insert(1, "Alerts: " + "; ".join(a["text"] for a in b["alerts"][:4]))
    return {"answer": "\n".join(f"- {l}" for l in lines) + "\n(offline mode: tool results without the LLM)",
            "trace": trace, "backend": "offline", "response_id": None}


# ---------------------------------------------------------------------------------------------
def show(q_, out, trace=False):
    print(f"\nQ: {q_}\n[{out['backend']}, {out.get('seconds', 0)} s, tools: "
          f"{', '.join(t['tool'] for t in out['trace']) or '-'}]\n{out['answer']}")
    if trace:
        for t in out["trace"]:
            print(f"   > {t['tool']}({json.dumps(t['args'])}) -> {t['result']}")


def main():
    ap = argparse.ArgumentParser(description="Production engineer copilot")
    ap.add_argument("question", nargs="*")
    ap.add_argument("--chat", action="store_true")
    ap.add_argument("--demo", action="store_true", help="ask the demo questions (each a fresh conversation)")
    ap.add_argument("--offline", action="store_true", help="never call Azure (cache, then keyword router)")
    ap.add_argument("--no-cache", action="store_true", help="always ask Azure again")
    ap.add_argument("--trace", action="store_true", help="print every tool call")
    a = ap.parse_args()
    backend = "offline" if a.offline else "auto"
    if a.demo:
        for qq in DEMO_QUESTIONS:
            ag = Agent(backend, not a.no_cache)
            show(qq, ag.ask(qq), a.trace)
        return
    ag = Agent(backend, not a.no_cache)
    print(f"Copilot - backend: {ag.backend}" + (f" ({ag.cfg['model']})" if ag.backend == "azure" else ""))
    if a.question:
        show(" ".join(a.question), ag.ask(" ".join(a.question)), a.trace)
    if a.chat:
        while True:
            try:
                qq = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if qq.lower() in ("", "exit", "quit"):
                break
            show(qq, ag.ask(qq), a.trace)


if __name__ == "__main__":
    main()
