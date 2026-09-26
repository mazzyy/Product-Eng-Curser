"""
Floor listener (model 4) - turns handover notes, maintenance log entries and supervisor answers
into structured facts, and links them to what the models found.

  1. read   floor_notes (free text, see generate_notes.py)
  2. parse  each note -> facts {station, time, category, subject, symptom, action, status, severity}
            with an LLM (Azure OpenAI GPT-5, Responses API, strict JSON schema) - or, when no API key
            is set, with a simple offline keyword parser so the prototype always runs
  3. link   each fact to the cause finder's incidents:
              agrees         same station, time and cause as an incident
              early warning  the floor saw it before the data did (incident starts later)
              disputes       a supervisor answer disagrees with the cause finder
              notes only     nothing in the data - only people saw it (safety, aisles, ...)
  4. score  against the answer key (data/injected_note_facts.csv)

Azure settings come from the environment or a .env file next to this script:
    AZURE_OPENAI_API_KEY=...                         (required for the LLM)
    AZURE_OPENAI_ENDPOINT=https://tasting-resource.services.ai.azure.com/openai/v1
    AZURE_OPENAI_MODEL=gpt-5                         (your deployment name)
    AZURE_OPENAI_REASONING=low                       (minimal / low / medium)
LLM answers are cached in data/llm_cache.json, so a re-run costs nothing and gives the same result -
and once every note is cached, the LLM results replay without a key (handy for demos).

    python floor_listener.py                  # auto: Azure if a key is set, else offline rules
    python floor_listener.py --backend rules  # force the offline parser
    python floor_listener.py --backend azure --no-cache
    python floor_listener.py --misses         # also list every difference from the answer key
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from station_db import ROOT, connect

NOTES_TRUTH_PATH = ROOT / "data" / "injected_note_facts.csv"
CACHE_PATH = ROOT / "data" / "llm_cache.json"
DEFAULT_ENDPOINT = "https://tasting-resource.services.ai.azure.com/openai/v1"
PROMPT_VERSION = "floor-v2"      # bump when SYSTEM changes: old cache entries are then ignored
SH_START = {"A": 6, "B": 14, "C": 22}
CATEGORIES = ["machine", "method", "people", "material", "supply", "it", "safety", "other"]
STATUSES = ["open", "monitoring", "fixed", "info"]
CAUSE_OF = {"machine": "machine", "method": "method", "people": "people",
            "material": "station", "supply": "station", "it": "station"}

SYSTEM = """You turn shift handover notes, maintenance log entries and supervisor answers from a car assembly
line into structured facts. Notes are short and informal, English with some German (Nacharbeit = rework,
Störung = fault or outage, Instandhaltung = maintenance, Übergabe / Schicht = handover / shift).

The line, upstream first:
- ST011 delivers bodies to ST012. Bodies or parts not arriving = category "supply", subject "ST011", station "st012".
- ST012 front subframe bolt-down: nutrunner NR-012 (socket, calibration), fixture FX-012, VIN scanner SC-012,
  bolt batches BL-####, work instruction WI-012 vN. Words: torque, angle, re-hits, bolts, "bolt station", "012".
- ST013 coolant fill and leak test: fill head / fill pump CF-013 (seal), leak tester LT-013, VIN scanner SC-013,
  coolant batches CL-####, work instruction WI-013 vN. Words: leak rate, fill, re-tests, "coolant station", "013".
- MES / line network books every VIN scan: category "it", subject "MES", station "line".
Operators are pseudonymous IDs OP-<crew><n> (crews A, B, C; n = 1-4). Notes often write just "C1" = "OP-C1".

Output one fact per distinct problem or observation. Skip chit-chat with no production meaning (canteen, coffee,
visitors, gloves, reminders, "all good"). If one note mentions the same problem twice, output it once.
Consequences of the same problem belong to that fact, not a new one: "since WI-013 v8 we scan twice, the board
count is wrong" is ONE fact (method, WI-013 v8) - the wrong count is its symptom.
Fields:
- station: st012, st013, line (both stations / whole line) or null if the note does not say. Use the station
  words even when no ID is written ("re-hits" -> st012, "re-tests" -> st013).
- time_hint: "HH:MM" if the note gives a clock time ("around 3" -> "03:00"), else null.
- category: the CAUSE the note points to, not where the symptom shows. If a WI version is named as the reason,
  it is method (double scans "(WI-013 v8)" = method, not it). Options: machine (equipment fault or wear),
  method (work instruction, sequence, standard work),
  people (an operator needs support: training, fatigue, technique), material (a part batch),
  supply (parts or bodies not arriving), it (MES, network), safety (hazard to people), other.
- subject: the ID it is about: equipment "NR-012", WI version "WI-012 v5", batch "BL-4471", operator "OP-C1",
  "ST011" or "MES"; for safety and other a 1-3 word name like "coolant puddle"; null if none.
- symptom: what was observed, at most 12 words, English.
- action: what was done or decided, at most 12 words, English, or null.
- status:
  open = a problem is still there, even if maintenance was informed or a ticket is open;
  monitoring = nothing confirmed yet and someone is watching early signs ("feels different, watching it"),
    or a person is being supported (paired, shown the technique again, swapped to an easier job);
  fixed = repaired, replaced, blocked, back to normal, or a new WI version removed a problem ("extra check gone");
  info = an observation with no problem ("results ok", "no NOKs") or a neutral change (new photos, wording).
- severity: high (safety risk, bad parts, line cannot keep up), medium, low (info only).
- quote: the exact words from the note, at most 20 words.
People facts describe support needs neutrally. Never judge, blame or rank a person.

A supervisor answer comes with the copilot's question, which names the cause the cause finder proposed.
Fill "answer": confirms = yes if the supervisor agrees with that cause (even if they add detail or a fix),
no if they believe another cause, unclear if they do not know; cause = the cause the supervisor believes
(machine, method, people, station) or null; decision = what will be done, or null. Also output one fact
about the incident's subject with status "info". For handover and maintenance notes set
answer = {"confirms": "n/a", "cause": null, "decision": null}."""

NULLABLE = lambda t: {"type": [t, "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["facts", "answer"],
    "properties": {
        "facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["station", "time_hint", "category", "subject", "symptom", "action", "status", "severity", "quote"],
            "properties": {
                "station": {"type": ["string", "null"], "enum": ["st012", "st013", "line", None]},
                "time_hint": NULLABLE("string"),
                "category": {"type": "string", "enum": CATEGORIES},
                "subject": NULLABLE("string"),
                "symptom": {"type": "string"},
                "action": NULLABLE("string"),
                "status": {"type": "string", "enum": STATUSES},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "quote": {"type": "string"},
            }}},
        "answer": {"type": "object", "additionalProperties": False, "required": ["confirms", "cause", "decision"],
                   "properties": {
                       "confirms": {"type": "string", "enum": ["yes", "no", "unclear", "n/a"]},
                       "cause": {"type": ["string", "null"], "enum": ["machine", "method", "people", "station", None]},
                       "decision": NULLABLE("string")}},
    },
}


# ---------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------
def load_env():
    """KEY=VALUE lines from .env (does not override variables already set)."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def azure_settings() -> dict:
    ep = os.environ.get("AZURE_OPENAI_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
    ep = re.sub(r"/responses$", "", ep)
    return {"key": os.environ.get("AZURE_OPENAI_API_KEY"), "endpoint": ep,
            "model": os.environ.get("AZURE_OPENAI_MODEL", "gpt-5"),
            "reasoning": os.environ.get("AZURE_OPENAI_REASONING", "low")}


# ---------------------------------------------------------------------------------------------
# Backend 1: Azure OpenAI (Responses API)
# ---------------------------------------------------------------------------------------------
def note_input(n: dict) -> str:
    s = datetime.strptime(n["shift_date"], "%Y-%m-%d") + timedelta(hours=SH_START[n["shift"]])
    lines = [f"kind: {n['kind']}", f"shift: {n['shift_date']} {n['shift']} ({s:%H:%M}-{s + timedelta(hours=8):%H:%M})",
             f"author: {n['author']}"]
    if n.get("question"):
        lines.append(f"question: {n['question']}")
    lines += ["note:", n["text"]]
    return "\n".join(lines)


def response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    return c["text"]
                if c.get("type") == "refusal":
                    raise RuntimeError(f"model refused: {c.get('refusal')}")
    raise RuntimeError(f"no text in response (status {data.get('status')}, {data.get('incomplete_details')})")


def call_azure(text: str, cfg: dict, retries: int = 3) -> dict:
    body = {"model": cfg["model"], "instructions": SYSTEM, "input": text, "max_output_tokens": 6000,
            "text": {"format": {"type": "json_schema", "name": "floor_facts", "strict": True, "schema": SCHEMA}}}
    if cfg["reasoning"]:
        body["reasoning"] = {"effort": cfg["reasoning"]}
    req = urllib.request.Request(f"{cfg['endpoint']}/responses", data=json.dumps(body).encode(), method="POST",
                                 headers={"api-key": cfg["key"], "Content-Type": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(response_text(json.loads(r.read())))
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:400]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"Azure HTTP {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"cannot reach {cfg['endpoint']}: {e.reason}") from None


class Cache:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.data = json.loads(CACHE_PATH.read_text()) if enabled and CACHE_PATH.exists() else {}

    @staticmethod
    def key(cfg, text):
        return hashlib.sha256(f"{PROMPT_VERSION}|{cfg['model']}|{cfg['reasoning']}|{text}".encode()).hexdigest()[:24]

    def get(self, k):
        return self.data.get(k) if self.enabled else None

    def put(self, k, v):
        self.data[k] = v

    def save(self):
        CACHE_PATH.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------
# Backend 2: offline keyword parser (fallback, no network)
# ---------------------------------------------------------------------------------------------
EQ_WORDS = {"NR-012": ["nutrunner", "socket"], "FX-012": ["fixture", "clamp"], "CF-013": ["fill head", "fill pump", "seal"],
            "LT-013": ["leak tester"]}
KW = {
    "safety": ["puddle", "slip", "light curtain", "injur", "near miss", r"\bppe\b"],
    "other": ["forklift", "aisle"],
    "it": [r"\bmes\b", "network", r"\bit (restarted|fixed|on-call)"],
    "supply": ["st011", "bodies", "logistics", "supply"],
    "material": ["pallet", "batch", "supplier"],
    "method": ["work instruction", "check step", "scan twice", "second vin scan", "double scan", "sequence"],
    "people": ["tired", "new on the job", "own way", "differently", "reminded"],
    "machine": ["maint", "instandhaltung", "noise", "drifting", "worn", "wearing", "recalibrat", "feels different",
                "not reading", "read errors"],
}
FIXED = ["replaced", "recalibrat", "repaired", "fixed", "back to normal", "ok again", "back on pace", "blocked",
         "quarantined", "restarted", "gone", r"\bback \d", "pace ok"]
MONITOR = ["watching", "keeping an eye", "paired", "swapped", "extra break", "showed", "supporting", "reminded"]
INFO = ["no issue", "only new photos", "just wording", "look ok", "no noks", "for ~"]


def has(s, words):
    return any(re.search(w, s) for w in words)


def rule_facts(text: str) -> list[dict]:
    body = re.sub(r"^(übergabe|handover|shift)\b[^\n/.]*?\d\d\.\d\d( (schicht|shift) [abc])?", "", text.strip(),
                  flags=re.I)
    parts = [p.strip(" -.") for p in re.split(r"\n|//|(?<=[a-z0-9)])\. |;", body) if p.strip(" -.")]
    out = []
    for p in parts:
        s = p.lower()
        if "safety walk" in s:
            continue
        ids = {
            "eq": [f"{a.upper()}-0{b[-2:]}" for a, b in re.findall(r"\b(nr|fx|sc|cf|lt)-?(0?1[23])\b", s)],
            "wi": [f"WI-0{a} v{b}" for a, b in re.findall(r"\bwi-?0?(1[23]) ?v ?(\d+)", s)],
            "batch": [f"{a.upper()}-{b}" for a, b in re.findall(r"\b(bl|cl)-(\d{4})\b", s)],
            "op": [f"OP-{a.upper()}" for a in re.findall(r"\b(?:op-)?([abc][1-4])\b", s)],
        }
        cat = None
        for c in ("safety", "other", "it", "supply"):
            if has(s, KW[c]):
                cat = c
                break
        if cat is None:
            cat = ("material" if ids["batch"] else "method" if ids["wi"] else "people" if ids["op"]
                   else "machine" if ids["eq"] else next((c for c in ("material", "method", "people", "machine")
                                                         if has(s, KW[c])), None))
        if cat is None:
            continue
        eq = ids["eq"][0] if ids["eq"] else next((k for k, ws in EQ_WORDS.items() if has(s, ws)), None)
        subject = {"material": (ids["batch"] or [None])[0], "method": (ids["wi"] or [None])[0],
                   "people": (ids["op"] or [None])[0], "machine": eq, "it": "MES", "supply": "ST011",
                   "safety": "light curtain" if "light curtain" in s else "coolant puddle" if "puddle" in s else None,
                   "other": None}[cat]
        m = re.search(r"\b(?:st ?)?0(1[23])\b", s)
        station = (f"st0{m.group(1)}" if m else
                   "st012" if (subject or "").endswith("012") or (subject or "").startswith("BL") or cat == "supply"
                   or "bolt" in s or "torque" in s or "re-hits" in s else
                   "st013" if (subject or "").endswith("013") or (subject or "").startswith("CL") or "coolant" in s
                   or "re-tests" in s else "line" if cat == "it" else None)
        if cat == "method" and subject:
            station = "st0" + subject[4:6]
        status = ("fixed" if has(s, FIXED) else "monitoring" if has(s, MONITOR) else "info" if has(s, INFO)
                  else "open")
        t = re.search(r"(\d{1,2}):(\d\d)", s) or re.search(r"around (\d{1,2})\b", s)
        th = f"{int(t.group(1)):02d}:{t.group(2) if t.lastindex == 2 else '00'}" if t else None
        sev = ("high" if cat == "safety" or has(s, ["can't keep up", r"\bnok\b", "stopped", "bad"]) else
               "low" if status == "info" else "medium")
        out.append({"station": station, "time_hint": th, "category": cat, "subject": subject, "symptom": p[:80],
                    "action": None, "status": status, "severity": sev, "quote": p[:120]})
    return out


def rule_answer(text: str, question: str) -> dict:
    s = text.lower().strip()
    if has(s, ["not sure", "no idea", "don't know", r"^ask "]):
        confirms = "unclear"
    elif re.match(r"(no\b|don't think|dont think|not really|disagree)", s) and not s.startswith("no, quality"):
        confirms = "no"
    else:
        confirms = "yes"
    m = re.search(r"cause finder: (\w+)", question or "", re.I)
    proposed = m.group(1).lower() if m else None
    cause = proposed if confirms == "yes" else ("machine" if confirms == "no" and has(s, ["tool", "maint", "nr-012"])
                                                else None)
    dec = next((p.strip() for p in re.split(r"[.,]", text) if has(p.lower(), ["booked", "blocked", "remove", "cut",
                                                                                "audit", "repaired", "look at", "ask"])), None)
    return {"confirms": confirms, "cause": cause, "decision": dec}


def rule_parse(n: dict) -> dict:
    if n["kind"] == "answer":
        q = n.get("question") or ""
        facts = rule_facts(q.split("Cause finder:")[-1] + " " + q.split(":")[0])[:1]
        st = re.search(r"\((ST0\d\d)", q)
        for f in facts:
            f.update(status="info", station=st.group(1).lower() if st else f["station"], quote=n["text"][:120])
        return {"facts": facts, "answer": rule_answer(n["text"], q)}
    return {"facts": rule_facts(n["text"]), "answer": {"confirms": "n/a", "cause": None, "decision": None}}


# ---------------------------------------------------------------------------------------------
# Clean-up, linking, scoring
# ---------------------------------------------------------------------------------------------
def norm_subject(x, cat):
    if not x:
        return None
    s = str(x).strip()
    m = re.search(r"\b(?:OP-?)?([ABC])-?([1-4])\b", s, re.I)
    if cat == "people" and m:
        return f"OP-{m.group(1).upper()}{m.group(2)}"
    m = re.search(r"WI[- ]?0?(1[23])[ ,]*v ?(\d+)", s, re.I)
    if m:
        return f"WI-0{m.group(1)} v{m.group(2)}"
    m = re.search(r"\b(NR|FX|SC|CF|LT)-?0?(1[23])\b", s, re.I)
    if m:
        return f"{m.group(1).upper()}-0{m.group(2)}"
    m = re.search(r"\b(BL|CL)-?(\d{4})\b", s, re.I)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    if re.search(r"\bmes\b", s, re.I):
        return "MES"
    if re.search(r"st ?011", s, re.I):
        return "ST011"
    return s.lower()


def clean(parsed: dict) -> dict:
    facts = []
    for f in parsed.get("facts", []):
        cat = f.get("category") if f.get("category") in CATEGORIES else "other"
        st = (f.get("station") or "").lower().replace(" ", "") or None
        facts.append({**f, "category": cat, "station": st if st in ("st012", "st013", "line") else None,
                      "subject": norm_subject(f.get("subject"), cat),
                      "status": f.get("status") if f.get("status") in STATUSES else "open"})
    return {"facts": facts, "answer": parsed.get("answer") or {"confirms": "n/a", "cause": None, "decision": None}}


def window(shift_date: str, shift: str):
    s = datetime.strptime(shift_date, "%Y-%m-%d") + timedelta(hours=SH_START[shift])
    return s, s + timedelta(hours=8)


def subj_match(subject, culprit) -> bool:
    if not subject or not isinstance(culprit, str):
        return False
    return subject.lower() in culprit.lower() or (subject == "ST011" and "supply" in culprit.lower())


def note_time(f: dict, note: dict) -> datetime:
    """When the floor saw it: the clock time in the note if there is one, else the end of the shift."""
    w0, w1 = window(note["shift_date"], note["shift"])
    th = f.get("time_hint")
    if isinstance(th, str) and re.fullmatch(r"\d\d:\d\d", th):
        t = w0.replace(hour=int(th[:2]), minute=int(th[3:]))
        while t < w0:
            t += timedelta(days=1)
        if t <= w1:
            return t
    return w1


def link(f: dict, note: dict, inc: pd.DataFrame) -> tuple:
    """-> (incident_id, link type, lead hours)"""
    if note["kind"] == "answer" and note.get("incident_id") is not None:
        a = note["_answer"]["confirms"]
        return int(note["incident_id"]), {"yes": "agrees", "no": "disputes"}.get(a, "unclear"), None
    want = CAUSE_OF.get(f["category"])
    if want is None or inc.empty:
        return None, "notes only", None
    w0, w1 = window(note["shift_date"], note["shift"])
    c = inc[(inc.start < w1 + timedelta(hours=2)) & (inc.end > w0 - timedelta(hours=2)) & (inc.cause == want)]
    if f["station"] in ("st012", "st013") and want != "people":       # operators rotate between stations
        c = c[c.stations.str.contains(f["station"])]
    named = c[c.culprit.apply(lambda x: subj_match(f["subject"], x))]
    if f["status"] == "info":        # a neutral mention only counts when it names the culprit
        c = named
    if len(c):
        return int((named if len(named) else c).incident_id.iloc[0]), "agrees", None
    # early warning: the data finds the same thing later, and had not found it before
    seen = note_time(f, note)
    same = inc[inc.culprit.apply(lambda x: subj_match(f["subject"], x))]
    later = same[(same.start > seen) & (same.start <= seen + timedelta(hours=24))]
    if len(later) and not (same.start <= seen).any():
        r = later.sort_values("start").iloc[0]
        return int(r.incident_id), "early warning", round((r.start - seen).total_seconds() / 3600, 1)
    return None, "notes only", None


def score(facts: pd.DataFrame, notes: pd.DataFrame, misses: list | None = None) -> dict:
    """Match predicted facts to the answer key per note; alt_* columns list a second accepted reading."""
    if not NOTES_TRUTH_PATH.exists():
        return {}
    truth = pd.read_csv(NOTES_TRUTH_PATH).astype(object)
    truth = truth.where(truth.notna(), None)          # NaN -> None (pandas 2 and 3)
    for c in ("alt_station", "alt_category", "alt_status"):
        if c not in truth:
            truth[c] = None
    facts = facts.astype(object)
    facts = facts.where(facts.notna(), None)
    ok = lambda value, key, alt: value == key or (alt is not None and value == alt)
    ok_cat = lambda p, t: ok(p.category, t.category, t.alt_category)
    ok_st = lambda p, t: ok(p.station, t.station, t.alt_station) or (t.station is None and p.station == "line")
    ok_status = lambda p, t: ok(p.status, t.status, t.alt_status)
    ok_subj = lambda p, t: bool(t.subject) and bool(p.subject) and (
        str(t.subject).lower() in str(p.subject).lower() or str(p.subject).lower() in str(t.subject).lower())
    show = lambda r: f"{r.category}/{r.station or '-'}/{r.subject or '-'}/{r.status}"
    matched, fields, used = 0, [], set()
    for t in truth.itertuples():
        cand = facts[(facts.note_id == t.note_id) & ~facts.fact_id.isin(used)]
        best, bs = None, 0
        for p in cand.itertuples():
            sc = 2 * ok_subj(p, t) + ok_cat(p, t) + ok_st(p, t)
            if sc > bs:
                best, bs = p, sc
        if best is None or bs < 2:
            if misses is not None:
                misses.append((t.note_id, f"missed      key {show(t)}"))
            continue
        used.add(best.fact_id)
        matched += 1
        res = {"category": ok_cat(best, t), "station": ok_st(best, t), "status": ok_status(best, t)}
        if t.subject and re.search(r"[A-Z]{2,}-|WI-|MES|ST011", str(t.subject)):
            res["subject"] = ok_subj(best, t)
        fields += list(res.values())
        if misses is not None and not all(res.values()):
            wrong = ", ".join(k for k, v in res.items() if not v)
            misses.append((t.note_id, f"wrong {wrong:<9} key {show(t)}  vs  {show(best)}  | {best.quote[:60]}"))
    if misses is not None:
        for p in facts[~facts.fact_id.isin(used)].itertuples():
            misses.append((p.note_id, f"extra       {show(p)}  | {p.quote[:60]}"))
    ans_t = truth[truth.source == "answer"].set_index("note_id").confirms
    ans_p = notes.set_index("note_id")["confirms"]
    ans_ok = sum(ans_p.get(i) == c for i, c in ans_t.items())
    return {"facts_true": len(truth), "facts_found": matched, "facts_pred": len(facts),
            "recall": matched / len(truth), "precision": matched / max(len(facts), 1),
            "field_acc": sum(fields) / max(len(fields), 1), "answers_ok": ans_ok, "answers": len(ans_t)}


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Floor listener - notes to structured facts")
    ap.add_argument("--backend", choices=["auto", "azure", "rules"], default="auto")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="only the first N notes (for a quick test)")
    ap.add_argument("--misses", action="store_true", help="list every difference from the answer key")
    args = ap.parse_args()
    load_env()
    cfg = azure_settings()
    con = connect()
    notes = pd.read_sql("SELECT * FROM floor_notes ORDER BY note_id", con)
    if args.limit:
        notes = notes.head(args.limit)
    cache = Cache(not args.no_cache)
    cached_all = all(cache.get(Cache.key(cfg, note_input(r))) is not None for r in notes.to_dict("records"))
    backend = args.backend if args.backend != "auto" else ("azure" if cfg["key"] or cached_all else "rules")
    if backend == "azure" and not cfg["key"] and not cached_all:
        raise SystemExit("AZURE_OPENAI_API_KEY is not set (environment or .env) - or run with --backend rules")
    try:
        inc = pd.read_sql("SELECT incident_id, stations, top_station, start_ts, end_ts, cause, family, culprit "
                          "FROM incidents", con)
        inc["start"], inc["end"] = pd.to_datetime(inc.start_ts), pd.to_datetime(inc.end_ts)
    except Exception:
        inc = pd.DataFrame()
    recs = [{k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.items()}
            for r in notes.to_dict("records")]

    print(f"Floor listener - {len(recs)} notes, backend: "
          + (f"Azure {cfg['model']} ({cfg['endpoint']}, reasoning {cfg['reasoning']})" if backend == "azure"
             else "offline keyword rules") + (" - all answers cached, no key needed" if backend == "azure" and cached_all
                                              and not cfg["key"] else ""))
    errors = []

    def parse(n):
        if backend == "rules":
            return "rules", rule_parse(n)
        text = note_input(n)
        k = Cache.key(cfg, text)
        hit = cache.get(k)
        if hit is not None:
            return "azure (cached)", hit
        try:
            out = call_azure(text, cfg)
            cache.put(k, out)
            return "azure", out
        except Exception as e:           # one bad call must not stop the run
            errors.append(f"note {n['note_id']}: {e}")
            return "rules (fallback)", rule_parse(n)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers if backend == "azure" else 1) as pool:
        results = list(pool.map(parse, recs))
    if backend == "azure" and cfg["key"]:
        cache.save()
    for e in errors[:5]:
        print("  !", e)

    rows = []
    for n, (used, parsed) in zip(recs, results):
        p = clean(parsed)
        n["_answer"], n["confirms"] = p["answer"], p["answer"].get("confirms")
        for f in p["facts"]:
            iid, lk, lead = link(f, n, inc)
            rows.append({"fact_id": len(rows) + 1, "note_id": n["note_id"], "kind": n["kind"],
                         "shift_date": n["shift_date"], "shift": n["shift"], "backend": used, **f,
                         "incident_id": iid, "link": lk, "lead_h": lead,
                         "confirms": p["answer"].get("confirms") if n["kind"] == "answer" else None,
                         "answer_cause": p["answer"].get("cause") if n["kind"] == "answer" else None,
                         "decision": p["answer"].get("decision") if n["kind"] == "answer" else None})
    facts = pd.DataFrame(rows)
    facts.to_sql("floor_facts", con, if_exists="replace", index=False)
    misses = []
    s = score(facts, notes.assign(confirms=[n["confirms"] for n in recs]), misses)
    label = f"Azure {cfg['model']}" if backend == "azure" else "offline keyword rules"
    pd.DataFrame({"key": ["backend"] + list(s), "value": [label] + [str(round(x, 4)) for x in s.values()]}).to_sql(
        "floor_eval", con, if_exists="replace", index=False)
    con.commit()
    con.close()

    # ---- report
    print(f"{len(facts)} facts from {len(recs)} notes in {time.time() - t0:.1f} s  "
          f"({facts.backend.value_counts().to_dict()})")
    print("links:", facts.link.value_counts().to_dict())
    if not inc.empty:
        seen = set(facts.incident_id.dropna().astype(int))
        told = set(inc[inc.incident_id.isin(seen)].culprit)
        silent = sorted(set(inc.culprit) - told)
        print(f"problems (cause finder culprits) the floor also reported: {len(told)}/{inc.culprit.nunique()}; "
              f"only in the data: {', '.join(silent) or '-'}")
    show = facts[facts.link.isin(["early warning", "notes only", "disputes", "unclear"])]
    for r in show.itertuples():
        extra = f" - {r.lead_h:.0f} h before the data" if r.link == "early warning" else ""
        print(f"  {r.link:<13} {r.shift_date[5:]} {r.shift}  {str(r.station or '-'):<5} {r.category:<8} "
              f"{str(r.subject or '-'):<15} {r.quote[:70]}{extra}")
    if s:
        print(f"\nvs answer key: found {s['facts_found']}/{s['facts_true']} facts (recall {s['recall']:.0%}, "
              f"precision {s['precision']:.0%}), fields right {s['field_acc']:.0%}, "
              f"supervisor answers read right {s['answers_ok']}/{s['answers']}")
    if args.misses and misses:
        print("\ndifferences from the answer key (note id):")
        for nid, m in sorted(misses, key=lambda x: x[0]):
            print(f"  {nid:>3}  {m}")


if __name__ == "__main__":
    main()
