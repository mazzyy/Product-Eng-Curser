"""
Shift handover notes, maintenance log entries and supervisor answers - the input of the floor
listener (model 4).

Written the way people write on the floor: short, English with some German, abbreviations,
times like "~14:00" or "around 3", operator codes like "C1". Generated from the simulated week,
so we know exactly what each note says:
  - every planted root cause is mentioned by the crews that saw it (not every shift, not always
    with an ID, sometimes before the data shows it);
  - unplanned repairs get a maintenance log entry;
  - harmless WI changes and repairs get a neutral mention;
  - some things only people see: early warnings, safety observations, a blocked aisle;
  - the copilot asks the supervisor about a few incidents and the answers are free text too.

Table: floor_notes.   Answer key: data/injected_note_facts.csv (one row per fact, for evaluation).
Where a label is honestly ambiguous, the key also lists a second accepted reading (alt_* columns):
a parked forklift in the aisle (other or safety), a preventive repair (fixed or info), and the
station of an MES answer (line or the station in the question).

    python generate_notes.py        # after find_causes.py (the questions refer to incidents)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from station_db import CAUSES_PATH, ROOT, STATIONS, connect, station_tables

NOTES_TRUTH_PATH = ROOT / "data" / "injected_note_facts.csv"
SEED = 11
SH_START = {"A": 6, "B": 14, "C": 22}
FILLER = ["canteen closed early", "all good otherwise", "reminder: safety walk thu 10:00", "5S audit ok",
          "coffee machine broken again", "handover done at the board", "Schichtübergabe ok", "nothing else",
          "2 visitors from quality on the line, no issues", "new gloves arrived"]
SIG = {"st012": {"sig": "torque", "redo": "re-hits", "prep": "wiping the mating surface", "topic": "bolt station"},
       "st013": {"sig": "leak rate", "redo": "re-tests", "prep": "the port check", "topic": "coolant station"}}

T = {  # (cause, family, moment) -> templates. {eq} equipment, {wi} work instruction, {o} operator, {b} buddy ...
    ("machine", "pattern", "first"): ["{eq} {sig} running high {t}, lots of Nacharbeit. maint. informed",
                                      "{st}: {eq} values drifting {t}, more {redo} than normal - ticket open",
                                      "{eq} Störung? {sig} looks off {t}, Instandhaltung called"],
    ("machine", "quality", "first"): ["{eq} - {redo} going up {t}, looks worn, maint. informed",
                                      "more {redo} on {st} {t}, probably {eq} wearing - ticket open"],
    ("machine", "speed", "first"): ["{eq} slow {t}, machine time creeping up",
                                    "{st} cycle slower, {eq} takes longer {t} - maint. informed"],
    ("machine", "data", "first"): ["{eq} not reading VINs well {t}, lots of manual entries",
                                   "VIN read errors on {eq} {t}, ticket open"],
    ("machine", "*", "early"): ["{eq} makes a strange noise, nothing in the numbers yet - keeping an eye on it",
                                "{eq} feels different today, watching it"],
    ("machine", "*", "during"): ["{eq} still not right, {redo} high", "still problems with {eq}, waiting for maint."],
    ("machine", "*", "fixed"): ["{repair} on {eq} at {t_end}, back to normal",
                                "maint. did the repair on {eq} ({repair_l}) {t_end} -> ok again"],
    ("method", "speed", "first"): ["new {wi} live {t} - the extra check step makes us slow, can't keep up",
                                   "{wi}: extra check on every car, {st} cycle now over 50 s",
                                   "{st} behind all shift because of the new check step in {wi}"],
    ("method", "data", "first"): ["{wi} wants a second VIN scan now - MES counter looks doubled",
                                  "since {wi} we scan twice at {st}, production count on the board is wrong"],
    ("method", "quality", "first"): ["{wi} changed the order, result check now before the run -> more Nacharbeit",
                                     "new sequence in {wi} confusing people, more {redo}"],
    ("method", "pattern", "first"): ["{wi} dropped {prep}, values look different since",
                                     "since {wi} we skip {prep} - {sig} pattern changed"],
    ("method", "*", "during"): ["still slow at {st}, {wi} check step eating time", "{wi} still causing trouble at {st}"],
    ("method", "data", "during"): ["double scans at {st} again, board count still wrong ({wi})"],
    ("method", "speed", "fixed"): ["{wi_next} live since {t_end}, extra check gone -> back on pace",
                                   "{wi_next} came {t_end}, only an audit now, pace ok"],
    ("method", "*", "fixed"): ["{wi_next} live {t_end}, old sequence back, all fine"],
    ("method", "*", "decoy"): ["{wi} live - only new photos, no issue", "new {wi} - just wording, nothing changed for us"],
    ("people", "quality", "first"): ["{o} needs a lot of {redo}, paired with {b} for a few hours",
                                     "{o} struggling with the {redo} again, showed the technique once more"],
    ("people", "pattern", "first"): ["{o} does the bolts a bit differently, results look ok",
                                     "{o} has his own way at {st}, no NOKs though"],
    ("people", "fatigue", "first"): ["{o} very tired towards the end, swapped to the easier job {t}",
                                     "{o} slowed down late in the shift, long week - gave an extra break"],
    ("people", "speed", "first"): ["{o} still slower, new on the job - supporting"],
    ("people", "data", "first"): ["reminded {o} to scan every car"],
    ("station", "quality", "first"): ["lots of {sig} NOK after the new pallet ({batch}) {t}",
                                      "{batch} parts bad? many {redo} {t}"],
    ("station", "pattern", "first"): ["{batch} parts look different, {sig} values shifted {t}"],
    ("station", "quality", "fixed"): ["batch {batch} blocked {t_end}, new pallet ok",
                                      "{batch} quarantined {t_end} -> {redo} back to normal"],
    ("station", "pattern", "fixed"): ["batch {batch} blocked {t_end}, new pallet ok"],
    ("station", "speed", "first"): ["no bodies from ST011 for a while {t}, logistics issue",
                                    "waited for bodies {t}, supply from ST011 stopped"],
    ("station", "speed", "fixed"): ["supply from ST011 back {t_end}"],
    ("station", "data", "first"): ["MES down twice {t}, scans lost, IT restarted a switch",
                                   "network/MES Störung {t} - VINs not booked, IT fixed it"],
}
CAT = {"machine": "machine", "method": "method", "people": "people"}


def eq_id(name) -> str | None:
    m = re.search(r"[A-Z]{2}-\d{3}", str(name))
    return m.group(0) if m else None


def shift_of(t: datetime):
    sh = "A" if 6 <= t.hour < 14 else "B" if 14 <= t.hour < 22 else "C"
    d = (t - timedelta(days=1)).date() if t.hour < 6 else t.date()
    return d, sh


def window(d, sh):
    s = datetime.combine(d, datetime.min.time()) + timedelta(hours=SH_START[sh])
    return s, s + timedelta(hours=8)


def shifts_between(a: datetime, b: datetime) -> list:
    out, (d, sh) = [], shift_of(a)
    s, _ = window(d, sh)
    while s < b:
        out.append(shift_of(s))
        s += timedelta(hours=8)
    return out


class Writer:
    def __init__(self, rng):
        self.rng = rng

    def pick(self, xs):
        return xs[int(self.rng.integers(0, len(xs)))]

    def when(self, t: datetime) -> str:
        opts = [f"~{t:%H:%M}", f"since {t:%H:%M}", f"around {t.hour}" if t.minute < 20 else f"at {t:%H:%M}"]
        if t.hour in (0, 1):
            opts.append("after midnight")
        return self.pick(opts)

    def st(self, sid: str) -> str:
        return self.pick([sid.upper(), sid[2:], "ST " + sid[2:], SIG[sid]["topic"]])

    def op(self, o: str) -> str:
        return self.pick([o, o[3:], o[3:]])

    def text(self, key, **kw) -> str:
        cause, fam, moment = key
        for k in ((cause, fam, moment), (cause, "*", moment)):
            if k in T:
                s = self.pick(T[k]).format(**kw)
                if self.rng.random() < 0.3 and s[:2].isalpha() and s[1].islower():   # never "nR-012"
                    s = s[0].lower() + s[1:]
                return s
        raise KeyError(key)


def main():
    rng = np.random.default_rng(SEED)
    w = Writer(rng)
    con = connect()
    causes = pd.read_csv(CAUSES_PATH)
    for c in ("start", "end"):
        causes[c] = pd.to_datetime(causes[c])
    ev, versions = [], []
    for sid in station_tables(con):
        df = pd.read_sql(f"SELECT ts, event_type, comment, downtime_s, work_instruction FROM {sid}", con)
        df["ts"] = pd.to_datetime(df.ts)
        e = df[df.comment.fillna("").str.startswith("Unplanned repair")].copy()
        e["station"] = sid
        ev.append(e)
        v = df[df.event_type == "CYCLE"].groupby("work_instruction").ts.min().reset_index()
        v["station"] = sid
        versions.append(v)
    repairs = pd.concat(ev)
    versions = pd.concat(versions).sort_values("ts")
    week_start, week_end = versions.ts.min().floor("D") + timedelta(hours=6), versions.ts.min().floor("D") + timedelta(days=7, hours=6)
    try:
        inc = pd.read_sql("SELECT * FROM incidents ORDER BY incident_id", con)
    except Exception:
        inc = pd.DataFrame()

    facts = {}          # (date, shift) -> list of facts

    def add(t_or_shift, sid, cat, subject, status, text, source):
        key = shift_of(t_or_shift) if isinstance(t_or_shift, (datetime, pd.Timestamp)) else t_or_shift
        facts.setdefault(key, []).append(dict(station=sid, category=cat, subject=subject, status=status,
                                              text=text, source=source))

    def add_story(c, sid, cat, subj, first, fixed, ended):
        """first mention; if the problem also ends in that shift, one 'fixed' fact instead of two."""
        if ended and fixed and shift_of(c.start) == shift_of(c.end):
            add(c.start, sid, cat, subj, "fixed", f"{first} - {fixed}", c.scenario_id)
            return True
        add(c.start, sid, cat, subj, "open", first, c.scenario_id)
        return False

    def prev_shift(t):
        return shift_of(window(*shift_of(t))[0] - timedelta(minutes=1))

    def next_version(sid, t):
        v = versions[(versions.station == sid) & (versions.ts >= t - timedelta(minutes=10))]
        return v.work_instruction.iloc[0] if len(v) else None

    scen_repairs = set()
    for c in causes.itertuples():
        sid = c.stations.split(",")[0]
        sg = SIG[sid]
        base = dict(st=w.st(sid), t=w.when(c.start), t_end=f"{c.end:%H:%M}", **sg)
        shifts = shifts_between(c.start, min(c.end, week_end))
        ended = c.end < week_end - timedelta(minutes=30)
        if c.cause == "machine":
            eq = eq_id(c.culprit)
            rep = repairs[(repairs.station == sid) & ((repairs.ts - c.end).abs() <= timedelta(minutes=5))]
            repair = rep.comment.iloc[0].replace("Unplanned repair: ", "") if len(rep) else "Repair"
            scen_repairs |= set(rep.index.map(lambda i, s=sid: (s, i)))
            kw = dict(base, eq=eq, repair=repair, repair_l=repair.lower())
            if rng.random() < 0.8:
                add(prev_shift(c.start), sid, "machine", eq, "monitoring",
                    w.text(("machine", "*", "early"), **kw), c.scenario_id + "-early")
            one = add_story(c, sid, "machine", eq, w.text(("machine", c.family, "first"), **kw),
                            w.text(("machine", "*", "fixed"), **kw), ended)
            for s in shifts[1:-1]:
                if rng.random() < 0.35:
                    add(s, sid, "machine", eq, "open", w.text(("machine", "*", "during"), **kw), c.scenario_id)
            if ended and not one:
                add(c.end, sid, "machine", eq, "fixed", w.text(("machine", "*", "fixed"), **kw), c.scenario_id)
        elif c.cause == "method":
            wi = c.culprit
            kw = dict(base, wi=wi)
            add(c.start, sid, "method", wi, "open", w.text(("method", c.family, "first"), **kw), c.scenario_id)
            for s in shifts[1:-1]:
                if rng.random() < 0.4:
                    add(s, sid, "method", wi, "open", w.text(("method", c.family, "during"), **kw), c.scenario_id)
            if ended:
                nxt = next_version(sid, c.end)
                add(c.end, sid, "method", nxt, "fixed", w.text(("method", c.family, "fixed"), **dict(kw, wi_next=nxt)),
                    c.scenario_id)
        elif c.cause == "people":
            o = c.operator
            crew = o[3]
            buddy = f"OP-{crew}{(int(o[4]) % 4) + 1}"
            fam = "fatigue" if "tiring" in c.description else c.family
            if isinstance(c.shift, str):
                mine = [(pd.Timestamp(c.shift_date).date(), c.shift)]
                p_first, p_more = 0.95, 0.0
            else:
                mine = [s for s in shifts if s[1] == crew]
                p_first, p_more = 0.9, 0.25 if fam != "pattern" else 0.1
            for i, s in enumerate(mine):
                if rng.random() < (p_first if i == 0 else p_more):
                    t0 = window(*s)[0] + timedelta(hours=float(rng.uniform(5, 7)))
                    kw = dict(base, o=w.op(o), b=w.op(buddy), t=w.when(t0))
                    status = "info" if fam == "pattern" else "monitoring"
                    add(s, sid if fam in ("pattern", "quality") else None, "people", o, status,
                        w.text(("people", fam, "first"), **kw), c.scenario_id)
        else:   # station: parts, supply, IT
            cat = {"quality": "material", "pattern": "material", "speed": "supply", "data": "it"}[c.family]
            subj = {"material": c.batch if isinstance(c.batch, str) else None, "supply": "ST011", "it": "MES"}[cat]
            kw = dict(base, batch=subj)
            where = sid if cat != "it" else "line"
            first = w.text(("station", c.family, "first"), **kw)
            fixed = w.text(("station", c.family, "fixed"), **kw) if ("station", c.family, "fixed") in T else None
            if cat == "it":     # short outage, the note already says it was fixed
                add(c.start, where, cat, subj, "fixed", first, c.scenario_id)
            elif not add_story(c, where, cat, subj, first, fixed, ended) and ended and fixed:
                add(c.end, where, cat, subj, "fixed", fixed, c.scenario_id)

    # harmless WI changes
    bad = set(causes[causes.cause == "method"].culprit)
    ends = causes[causes.cause == "method"].end
    for v in versions.itertuples():
        if v.ts <= week_start + timedelta(minutes=30) or v.work_instruction in bad or \
                any(abs(v.ts - e) <= timedelta(minutes=10) for e in ends):
            continue
        if rng.random() < 0.8:
            add(v.ts, v.station, "method", v.work_instruction, "info",
                w.text(("method", "*", "decoy"), wi=v.work_instruction), "decoy-wi")

    # maintenance log + harmless repairs
    notes = []
    for i, r in repairs.iterrows():
        sid = r.station
        repair = r.comment.replace("Unplanned repair: ", "")
        eq = next((eq_id(e[0]) for e in STATIONS[sid]["equipment"].values() if e[2] == repair), None)
        own = (sid, i) in scen_repairs
        found = (causes[(causes.cause == "machine") & ((causes.end - r.ts).abs() <= timedelta(minutes=5))]
                 .description.str.lower().tolist() or ["preventive, no fault found"])[0]
        notes.append(dict(kind="maintenance", ts=r.ts, author="Maintenance", question=None, incident_id=None,
                          text=f"{r.ts:%d.%m %H:%M} {sid.upper()}: {repair} ({eq}). Down {r.downtime_s / 60:.0f} min. "
                               f"Found: {found}.",
                          facts=[dict(station=sid, category="machine", subject=eq, status="fixed", text="",
                                      source="repair" if own else "decoy-repair")]))
        if not own:
            notes[-1]["facts"][0]["alt"] = {"status": "info"}       # preventive, no fault: fixed or info
            add(r.ts, sid, "machine", eq, "fixed", f"{eq} {repair.lower()} as a precaution, no issue", "decoy-repair")
            facts[shift_of(r.ts)][-1]["alt"] = {"status": "info"}

    # things only people see
    all_shifts = shifts_between(week_start, week_end)
    only_floor = [("st013", "safety", "coolant puddle", "open",
                   "small coolant puddle under the 013 conveyor - slip risk, cleaned, ticket open"),
                  ("st012", "safety", "light curtain", "open", "light curtain at ST012 flickers sometimes, reported to maint."),
                  ("st013", "other", None, "info", "forklift parked in the aisle next to 013 for ~10 min")]
    for sid, cat, subj, status, text in only_floor:
        add(all_shifts[int(rng.integers(2, len(all_shifts)))], sid, cat, subj, status, text, "floor-only")
        if cat == "other":             # a blocked walkway is a fair "safety" reading too
            last = [f for fs in facts.values() for f in fs if f["text"] == text][0]
            last["alt"] = {"category": "safety"}

    # handover notes, one per shift
    for d, sh in all_shifts:
        fs = facts.get((d, sh), [])
        lines = [f["text"] for f in fs]
        n_fill = int(rng.integers(0, 3)) if fs else 1
        for x in rng.choice(FILLER, size=n_fill, replace=False):
            lines.insert(int(rng.integers(0, len(lines) + 1)), str(x))
        head = w.pick([f"Übergabe {d:%d.%m} Schicht {sh}", f"Handover {d:%d.%m} shift {sh}", f"shift {sh} {d:%d.%m}"])
        sep = w.pick(["\n- ", "\n", ". "])
        notes.append(dict(kind="handover", ts=window(d, sh)[1], author=f"TL crew {sh}", question=None, incident_id=None,
                          text=head + sep + sep.join(lines), facts=fs))

    # the copilot asks, the supervisor answers
    notes += answers(inc, causes, rng, w)

    notes.sort(key=lambda n: n["ts"])
    rows, truth = [], []
    for i, n in enumerate(notes, 1):
        d, sh = shift_of(n["ts"] - timedelta(minutes=1))
        rows.append({"note_id": i, "kind": n["kind"], "written_ts": f"{n['ts']:%Y-%m-%d %H:%M:%S}", "shift_date": str(d),
                     "shift": sh, "author": n["author"], "incident_id": n["incident_id"], "question": n["question"],
                     "text": n["text"]})
        for j, f in enumerate(n["facts"], 1):
            alt = f.get("alt") or {}
            truth.append({"note_id": i, "fact_no": j, "station": f["station"], "category": f["category"],
                          "subject": f["subject"], "status": f["status"], "source": f["source"],
                          "confirms": f.get("confirms"), "cause": f.get("cause"),
                          # a second reading that is also right, where the label is honestly ambiguous
                          "alt_station": alt.get("station"), "alt_category": alt.get("category"),
                          "alt_status": alt.get("status")})
    pd.DataFrame(rows).to_sql("floor_notes", con, if_exists="replace", index=False)
    con.commit()
    con.close()
    truth = pd.DataFrame(truth)
    truth.to_csv(NOTES_TRUTH_PATH, index=False)
    kinds = pd.Series([r["kind"] for r in rows]).value_counts().to_dict()
    print(f"floor_notes: {len(rows)} notes {kinds}; {len(truth)} facts in the answer key -> {NOTES_TRUTH_PATH.name}")
    print("facts by category:", truth.category.value_counts().to_dict())


def answers(inc: pd.DataFrame, causes: pd.DataFrame, rng, w: Writer) -> list[dict]:
    """A few incidents the copilot asks the supervisor about, with free-text answers."""
    if inc.empty:
        return []
    first = inc.groupby("culprit", sort=False).head(1)
    picks = []
    for want in [("method", None), ("machine", None), ("station", "quality"), ("people", "quality"),
                 ("method", None), ("people", "pattern"), ("station", "data")]:
        c = first[(first.cause == want[0]) & (first.family == want[1] if want[1] else True)
                  & ~first.incident_id.isin([p.incident_id for p in picks])]
        if len(c):
            picks.append(c.iloc[0])
    out = []
    for r in picks:
        t = pd.Timestamp(r.end_ts).to_pydatetime() + timedelta(hours=float(rng.uniform(3, 10)))
        sid, conf = r.top_station, f"{r.confidence:.0%}"
        head = f"Incident {r.incident_id} ({sid.upper()}, {r.shift_date[5:]} shift {r['shift']}): {r.symptom}. "
        sc = causes[causes.culprit == r.culprit]
        desc = sc.description.iloc[0].lower() if len(sc) else ""
        subj, cause, confirms = r.culprit, r.cause, "yes"
        if r.cause == "method" and r.family == "speed":
            q = head + f"Cause finder: method - {r.culprit} ({conf}). Was the extra check meant for every car?"
            a = w.pick([f"no, quality only wanted the check as an audit after the bolt problem. 1 in 20 cars is enough, "
                        f"fixed in the next version", f"yes it came with {r.culprit}, but it should only be an audit - "
                        f"we cut it to 1 in 20 in the next version"])
            decision = "audit 1 in 20 cars"
        elif r.cause == "method":
            q = head + f"Cause finder: method - {r.culprit} ({conf}). Is the change in {r.culprit} the reason?"
            a = f"yes, the second scan is from {r.culprit}. quality asked for it but it double books the car, we remove it next version"
            decision = "remove the second scan"
        elif r.cause == "machine":
            eq = eq_id(r.culprit)
            subj = eq
            q = head + f"Cause finder: machine - {r.culprit} ({conf}). Did maintenance find something?"
            a = f"correct, Instandhaltung found it: {desc}. repaired, fine since"
            decision = "repaired"
        elif r.cause == "station" and r.family == "quality":
            q = head + f"Cause finder: station - {r.culprit} ({conf}). Can you confirm the parts?"
            batch = re.search(r"[A-Z]{2}-\d{4}", r.culprit).group(0)
            subj = batch
            a = f"yes, supplier confirmed {batch} out of spec. pallets blocked and sent back"
            decision = "batch blocked and returned"
        elif r.cause == "people" and r.family == "quality":
            q = head + f"Cause finder: people - {r.culprit} ({conf}). Does {r.culprit} need support?"
            buddy = f"{r.culprit[3]}{(int(r.culprit[4]) % 4) + 1}"
            a = f"{r.culprit[3:]} is new on the torque station since 2 weeks. trainer session booked thursday, paired with {buddy} until then"
            decision = "training booked, pairing until then"
        elif r.cause == "people":
            q = head + f"Cause finder: people - {r.culprit} ({conf}). Is this a technique difference?"
            a = (f"don't think so. {r.culprit[3:]} works like we trained everyone and torque is in spec. "
                 f"maybe the tool? I'd like maint. to look at NR-012 first")
            confirms, cause, decision, subj = "no", "machine", "check NR-012 first", r.culprit
        else:
            q = head + f"Cause finder: station - {r.culprit} ({conf}). Can you confirm?"
            a = "not sure, IT didn't tell us anything. ask the IT on-call"
            confirms, cause, decision = "unclear", None, "ask IT on-call"
            subj = "MES" if r.family == "data" else "ST011"
        cat = {"machine": "machine", "method": "method", "people": "people"}.get(r.cause, "material" if r.family == "quality"
                                                                              else "it" if r.family == "data" else "supply")
        out.append(dict(kind="answer", ts=t, author="Supervisor", question=q, incident_id=int(r.incident_id), text=a,
                        facts=[dict(station="line" if cat == "it" else sid, category=cat, subject=subj, status="info",
                                    text=a, source="answer", confirms=confirms, cause=cause,
                                    alt={"station": sid} if cat == "it" else {})]))
    return out


if __name__ == "__main__":
    main()
