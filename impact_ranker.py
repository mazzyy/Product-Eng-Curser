"""
Impact ranker (model 3) - which problems matter most, and can we hit 7,500 cars a week?

It does not re-analyse the raw data. It takes what the other models already found - the
cause-finder cases, signal-checker flags, people-model findings, plus faults and repairs -
measures each one in four "currencies", turns them into one score 0-100 and sorts everything
into High / Medium / Low. It also scores problems that could happen next (impact_catalog).

THE MATH (per item = one cause-finder case, or a group of similar flags)

1. Four measurements in natural units
   R  risk cars  = sum over affected cars of a weight w (how likely the car is not OK and nobody knows)
                   w = 1.0  known bad (passed OK although out of spec), no VIN / double scan, no valid
                            measurement, no record at the upstream station        -> "definite" cars
                   w = m    built while the key signal was shifted; m = share of the safety margin used
                   w = 0.5  possible skipped step (rushed), single car off the normal pattern
                   w = 0.3  wrong operator on the record (stale login), unexplained very fast cycle
                   w = 0.2  rejected with no reason
                   w = 0.1  needed >= 3 re-hits, rejected and reworked, in spec but unusually high/low
   L  lost cars  = extra seconds at the bottleneck station / bottleneck cycle time
                   other stations: per car only beyond their spare time; stops beyond ~1 h of spare time
   C  rework h   = 20 min per extra rejected car + 10 min per car to verify + 10 min per fault without
                   a code + 1 technician x fault time + 2 technicians x unplanned repair time
   P  people h   = hours people worked under strain: fatigue, re-hit bursts, waiting for help,
                   struggling alone, working without the legal 11 h rest

2. Sub-scores 0-10, log scale (10x more impact = a fixed step up), capped at 10
   SQ = S/10 * 10 log(1+R) / log(1+100)     S = severity of the station's key characteristic, 1-10
   DL =        10 log(1+L) / log(1+75)      75 cars = 1% of the 7,500 weekly target
   CO =        10 log(1+C) / log(1+40)      40 h = one person-week
   PE =        10 log(1+P) / log(1+8)       8 h = one full shift under strain

3. Score 0-100 = 10 * (0.40 SQ + 0.30 DL + 0.15 CO + 0.15 PE)   safety & quality > delivery > cost, people

4. Future: an item still active at the end of the data (no repair, no new instruction version, batch
   still in use, seen in the last 24 h) is projected over the next 7 days at its observed rate;
   recurring groups (seen on 3+ days) repeat. Priority = max(score now, score next week).

5. Category: High >= 50, Medium >= 25, Low below - and hard rules that override the score:
     always HIGH  known-bad cars passed on a station with S >= 8
                  definite cars >= 10 x 3^(9-S)   (S=9: 10 cars, S=8: 30 cars)
                  signal used >= 30% of the safety margin on a safety-critical characteristic (S >= 9)
                  lost cars >= 75 (1% of the weekly target);  a legal rest-time breach
     at least MEDIUM  S >= 8 and R >= 1

    python impact_ranker.py
"""
from __future__ import annotations

import re
import time

import numpy as np
import pandas as pd

from station_db import IMPACTS_SQL, STATIONS, connect, station_tables

TARGET_PER_WEEK = 7500
WEIGHTS = {"sq": 0.40, "dl": 0.30, "co": 0.15, "pe": 0.15}
REF = {"risk": 100.0, "lost": 0.01 * TARGET_PER_WEEK, "cost": 40.0, "people": 8.0}
HIGH, MEDIUM = 50, 25
MARGIN_HIGH = 0.30
INDEX_S = 3.0              # transfer time between two cars (line design)
REWORK_MIN, CHECK_MIN, FAULT_CHECK_MIN, REPAIR_CREW = 20, 10, 10, 2
RECOVERY_H = 1.0           # a station with spare time catches up a stop within about an hour
H = pd.Timedelta(hours=1)
LEVELS = ["High", "Medium", "Low"]
FAMILY_TEXT = {"quality": "re-hits / rejects up", "speed": "cycles slower", "pattern": "signal off its normal pattern",
               "data": "VIN records missing / double"}
# single flags that belong to a case of this family when they fall inside its window
FAMILY_KINDS = {"data": {"no_vin", "double_scan"}, "pattern": {"in_spec_outlier", "off_pattern_car"},
                "quality": {"retries"}, "speed": {"slow_cycle", "struggle"}}


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------
def empty() -> dict:
    return {"cars": 0, "risk_cars": 0.0, "known_bad": 0.0, "definite_cars": 0.0, "margin_used": 0.0,
            "lost_cars": 0.0, "rework_h": 0.0, "people_h": 0.0, "legal": False}


def logscore(x: float, ref: float) -> float:
    return float(min(10.0, 10 * np.log10(1 + max(x, 0.0)) / np.log10(1 + ref)))


def score(m: dict, sev: float):
    sub = {"sq": sev / 10 * logscore(m["risk_cars"], REF["risk"]),
           "dl": logscore(m["lost_cars"], REF["lost"]),
           "co": logscore(m["rework_h"], REF["cost"]),
           "pe": logscore(m["people_h"], REF["people"])}
    return round(10 * sum(WEIGHTS[k] * v for k, v in sub.items()), 1), sub


def categorise(total: float, m: dict, sev: float):
    rules = []
    if sev >= 8 and m["known_bad"] >= 1:
        rules.append(f"{m['known_bad']:.0f} known out-of-spec cars passed as OK")
    if m["definite_cars"] >= 10 * 3 ** (9 - sev):
        rules.append(f"{m['definite_cars']:.0f} cars certainly need a check (severity {sev})")
    if sev >= 9 and m["margin_used"] >= MARGIN_HIGH:
        rules.append(f"safety-critical signal used {m['margin_used']:.0%} of its safety margin")
    if m["lost_cars"] >= REF["lost"]:
        rules.append(f"{m['lost_cars']:.0f} lost cars (>= 1% of the weekly target)")
    if m["legal"]:
        rules.append("legal rest-time breach")
    if rules:
        return "High", "; ".join(rules)
    if total >= HIGH:
        return "High", ""
    if total >= MEDIUM:
        return "Medium", ""
    if sev >= 8 and m["risk_cars"] >= 1:
        return "Medium", "safety-relevant station with cars at risk"
    return "Low", ""


# ---------------------------------------------------------------------------
# The line: healthy baselines, bottleneck, spare time
# ---------------------------------------------------------------------------
class Line:
    def __init__(self, tables: dict):
        self.base, self.cycles, self.events = {}, {}, {}
        for s, df in tables.items():
            c = df[df.event_type == "CYCLE"].copy()
            self.cycles[s] = c
            self.events[s] = df[df.event_type != "CYCLE"].copy()
            healthy = c[c.incident_id.isna() & (c.anomaly_flag.fillna(0) == 0) & (c.human_flag.fillna(0) == 0)
                        & (c.andon_pulled.fillna(0) == 0)]
            ok = healthy[(healthy.result == "OK") & healthy.primary_value.notna()]
            slope, icpt = np.polyfit(ok.primary_value, ok.secondary_value, 1)
            cfg = STATIONS.get(s, {})
            self.base[s] = {
                "cycle": healthy.cycle_time_s.median(), "wait": healthy.wait_time_s.median(),
                "op": healthy.operator_time_s.median(), "nok": (healthy.result == "NOK").mean(),
                "rehit3": (healthy.retries >= 3).mean(), "untrace": healthy.vin.isna().mean(),
                "retry": healthy.retries.mean(),
                "p_med": ok.primary_value.median(), "s_med": ok.secondary_value.median(),
                "slope": slope, "icpt": icpt,
                "p_lim": (cfg.get("primary", {}).get("lsl"), cfg.get("primary", {}).get("usl")),
                "s_lim": (cfg.get("secondary", {}).get("lsl"), cfg.get("secondary", {}).get("usl")),
                "retry_cost": cfg.get("retry_cost_s", 0.0), "severity": cfg.get("severity", 5),
            }
        eff = {s: b["cycle"] + INDEX_S for s, b in self.base.items()}
        self.bottleneck = max(eff, key=eff.get)
        self.takt = eff[self.bottleneck]
        self.slack = {s: self.takt - e for s, e in eff.items()}
        allc = pd.concat(self.cycles.values())
        self.start, self.end = allc.ts.min(), allc.ts.max()
        self.hours = (self.end - self.start) / H
        self.cars_per_h = {s: len(c) / self.hours for s, c in self.cycles.items()}

    def lost_from_extra(self, s, extra_s, n):
        """Extra seconds spread over n cars at station s -> cars lost at the end of the line."""
        if s == self.bottleneck:
            return max(0.0, extra_s) / self.takt
        return max(0.0, extra_s - self.slack[s] * n) / self.takt

    def lost_from_stops(self, s, stops_s):
        """Stops: the bottleneck loses them all, other stations recover within ~1 h of spare time."""
        stops = [max(0.0, float(d)) for d in stops_s]
        if s == self.bottleneck:
            return sum(stops) / self.takt
        absorb = self.slack[s] * self.cars_per_h[s] * RECOVERY_H
        return sum(max(0.0, d - absorb) for d in stops) / self.takt

    def margin_used(self, X, s) -> float:
        """Share of the safety margin the key signal (or its pattern) used up, 0..1."""
        b, ok = self.base[s], X[(X.result == "OK") & X.primary_value.notna()]
        if len(ok) < 20:
            return 0.0
        sp = ok.primary_value.mean() - b["p_med"]
        dp = (b["p_lim"][1] - b["p_med"]) if sp > 0 else (b["p_med"] - b["p_lim"][0])
        sr = (ok.secondary_value - (b["icpt"] + b["slope"] * ok.primary_value)).mean()
        ds = (b["s_lim"][1] - b["s_med"]) if sr > 0 else (b["s_med"] - b["s_lim"][0])
        return float(min(1.0, max(abs(sp) / dp if dp else 0.0, abs(sr) / ds if ds else 0.0)))

    def costs(self, m, s, X, time_mode):
        """Delivery + rework of a group of cycles vs the healthy baseline.
        time_mode: 'cycle' (every car slower), 'retries' (time spent re-hitting), 'stops' (single long
        cycles), 'none' (no time effect)."""
        b, n = self.base[s], len(X)
        m["cars"] += n
        if time_mode == "cycle":
            extra = (X.cycle_time_s - b["cycle"]).sum()
            if s == self.bottleneck:                            # waiting at the bottleneck = lost output
                extra += (X.wait_time_s - b["wait"]).clip(lower=0).sum()
            m["lost_cars"] += self.lost_from_extra(s, extra, n)
        elif time_mode == "retries":
            extra = (X.retries.sum() - n * b["retry"]) * b["retry_cost"]
            m["lost_cars"] += self.lost_from_extra(s, extra, n)
        elif time_mode == "stops":
            m["lost_cars"] += self.lost_from_stops(s, (X.cycle_time_s - b["cycle"]).clip(lower=0).values)
        extra_nok = max(0.0, (X.result == "NOK").sum() - n * b["nok"])
        m["rework_h"] += extra_nok * REWORK_MIN / 60
        return extra_nok


# ---------------------------------------------------------------------------
# Items from the cause finder: one per case
# ---------------------------------------------------------------------------
def case_items(line, inc, wi_changes, batch_last) -> list:
    items = []
    for cid, g in inc.groupby("case_id"):
        r0 = g.iloc[0]
        fam, cause, top = r0.family, r0.cause, r0.top_station
        blocks = sorted({(b.split("@")[0], pd.Timestamp(b.split("@")[1]))
                         for bl in g.block_list for b in bl.split("; ")})
        stations = sorted({b[0] for b in blocks})
        m = empty()
        mode = {"speed": "cycle", "quality": "retries"}.get(fam, "none")
        for s in stations:
            c, b = line.cycles[s], line.base[s]
            per_block = [c[(c.ts >= t0) & (c.ts < t0 + 2 * H)] for st, t0 in blocks if st == s]
            X = pd.concat(per_block)
            if X.empty:
                continue
            extra_nok = line.costs(m, s, X, mode)
            if fam == "quality":
                rehit3 = max(0.0, (X.retries >= 3).sum() - len(X) * b["rehit3"])
                m["risk_cars"] += 0.1 * (extra_nok + rehit3)
            elif fam == "pattern":
                used = [line.margin_used(Xb, s) for Xb in per_block]
                m["risk_cars"] += sum(u * len(Xb) for u, Xb in zip(used, per_block)) + 0.1 * extra_nok
                m["margin_used"] = max([m["margin_used"]] + used)
            elif fam == "data":
                untr = max(0.0, X.vin.isna().sum() + X.vin.duplicated().sum() - len(X) * b["untrace"])
                m["risk_cars"] += untr
                m["definite_cars"] += untr
                m["rework_h"] += untr * CHECK_MIN / 60
            if cause == "people":
                if fam == "speed":                                   # hours worked tired
                    m["people_h"] += X.operator_time_s.sum() / 3600
                elif fam == "quality":                               # time spent re-hitting
                    m["people_h"] += X.retries.sum() * b["retry_cost"] / 3600
        first, last = g.start_ts.min(), g.end_ts.max()
        ev = line.events[top]
        rep = ev[ev.comment.fillna("").str.startswith("Unplanned") & (ev.ts >= first) & (ev.ts <= last + 4 * H)]
        if cause != "machine":
            rep = rep.iloc[0:0]
        m["lost_cars"] += line.lost_from_stops(top, rep.downtime_s.values)
        m["rework_h"] += rep.downtime_s.sum() / 3600 * REPAIR_CREW
        sev = 3 if fam == "speed" else max(line.base[s]["severity"] for s in stations)
        items.append({
            "source": "cause finder", "title": f"{r0.culprit} - {FAMILY_TEXT.get(fam, fam)}", "detail": r0.symptom,
            "family": fam,
            "stations": ",".join(stations), "cause": cause, "culprit": r0.culprit, "case_id": int(cid),
            "status": case_status(cause, r0.culprit, top, last, line, wi_changes, batch_last, rep),
            "first_ts": first, "last_ts": last, "m": m, "sev": sev, "repairs": set(rep.ts),
            "action": case_action(cause, fam, r0.culprit, top),
        })
    return items


def case_status(cause, culprit, top, last, line, wi_changes, batch_last, rep):
    to_end = (line.end - last) / H
    if cause == "machine" and len(rep):
        return "fixed"
    if cause == "method" and any(t > last - 2 * H and v != culprit for t, v in wi_changes.get(top, [])):
        return "fixed"
    if cause == "station" and "batch" in culprit:
        if batch_last.get(top, {}).get(culprit.split()[-1], line.end) < line.end - H:
            return "over"
    if cause == "station" and to_end > 12:
        return "over"
    return "active" if to_end <= 24 else "quiet"


def case_action(cause, fam, culprit, top):
    T = top.upper()
    if cause == "station":
        if "MES" in culprit:
            return "IT: fix MES / network; re-scan and verify the cars in the outage window"
        if "supply" in culprit:
            return f"Logistics: secure material supply to {T}"
        return f"Quality: quarantine {culprit}, check with supplier, verify the cars built with it"
    return {
        "machine": f"Maintenance: repair / recalibrate {culprit}; verify cars built meanwhile; add to preventive plan",
        "people": (f"Team lead: check workload, breaks and rotation for {culprit}" if fam == "speed"
                   else f"Team lead: coach {culprit} against standard work; check tool and part fit"),
        "method": f"Engineering: review {culprit} (fits takt? safe? trained?) - fix or roll back",
    }.get(cause, "Investigate")


# ---------------------------------------------------------------------------
# Items from single flags that are not part of any incident, grouped by kind
# ---------------------------------------------------------------------------
SIGNAL_KINDS = [  # regex on anomaly_reason, kind, weight, definite, known bad, time, check min, title, action
    (r"^result OK but", "ok_but_oos", 1.0, True, True, "none", CHECK_MIN,
     "cars passed OK although out of spec", "Containment: hold and re-check these cars; fix the OK/NOK logic"),
    (r"^rejected \(NOK\)", "nok_no_reason", 0.2, False, False, "none", REWORK_MIN,
     "cars rejected with no reason", "Quality: find out why they were rejected (false rejects cost rework)"),
    (r"^cycle recorded without VIN", "no_vin", 1.0, True, False, "none", CHECK_MIN,
     "cars without VIN on record", "Containment: identify and verify these cars; check the scanner"),
    (r"^VIN already recorded", "double_scan", 1.0, True, False, "none", CHECK_MIN,
     "double VIN scans (another car left without record)", "Containment: find the missed cars; check the scan step"),
    (r"^VIN never recorded at upstream", "skipped_upstream", 1.0, True, False, "none", CHECK_MIN,
     "cars with no record at the upstream station", "Containment: prove the upstream work was done on these cars"),
    (r"stuck at", "stuck_sensor", 1.0, True, False, "none", CHECK_MIN,
     "cars measured by a stuck sensor", "Maintenance: check the sensor; re-verify these cars"),
    (r"missing \(sensor dropout\)", "no_measurement", 1.0, True, False, "none", CHECK_MIN,
     "cars without a valid measurement", "Maintenance: check sensor cable / signal; re-verify these cars"),
    (r"^fault / downtime logged with no fault code", "fault_no_code", 0.0, False, False, "none", FAULT_CHECK_MIN,
     "faults logged without a code", "Shift lead: enforce fault codes - uncoded stops can't be improved"),
    (r"doesn't fit", "off_pattern_car", 0.5, False, False, "none", 0,
     "single cars off the normal pattern (e.g. cross-thread)", "Quality: check these cars"),
    (r"unusually fast cycle", "fast_cycle", 0.3, False, False, "none", 0,
     "unexplained very fast cycles", "Quality: check these cars for skipped steps"),
    (r"^slow cycle", "slow_cycle", 0.0, False, False, "stops", 0,
     "unexplained slow cycles", "Shift lead: ask what happened"),
    (r"drifting from baseline|unusually (?:high|low)", "in_spec_outlier", 0.1, False, False, "none", 0,
     "in-spec but unusual values", "Watch"),
]
PEOPLE_KINDS = {   # human_type: weight, time mode, title, action
    "rushed": (0.5, "none", "rushed cycles - possible skipped step", "Team lead: check these cars, talk to the operator"),
    "struggle": (0.0, "stops", "operators struggling without asking for help", "Team lead: make andon easy to pull"),
    "retries": (0.1, "retries", "re-hit bursts on single cars", "Team lead: check tool, part fit and technique"),
    "login": (0.3, "none", "cycles recorded under the wrong operator (stale login)", "Fix the login step at rotation"),
}


def group_item(source, title, stations, cause, X, m, sev, action, detail=""):
    return {"source": source, "title": title, "detail": detail, "stations": stations, "cause": cause,
            "culprit": None, "case_id": None, "status": "recurring" if X.ts.dt.date.nunique() >= 3 else "one-off",
            "first_ts": X.ts.min(), "last_ts": X.ts.max(), "m": m, "sev": sev, "action": action}


def belongs_to_case(X, s, kind, cases):
    """Flags inside the window of a case of the same family (and the same person for people cases)."""
    hit = pd.Series(False, index=X.index)
    for it in cases:
        if s not in it["stations"].split(",") or kind not in FAMILY_KINDS.get(it["family"], ()):
            continue
        inside = (X.ts >= it["first_ts"] - H) & (X.ts <= it["last_ts"] + H)
        if it["cause"] == "people":
            inside &= X.operator_id == it["culprit"]
        if inside.any():
            it.setdefault("absorbed", []).append((s, kind, X[inside & ~hit]))
        hit |= inside
    return hit


def absorb_into_cases(line, cases):
    """Count the absorbed flags into their case (data flags = more cars to verify)."""
    for it in cases:
        for s, kind, X in it.pop("absorbed", []):
            if kind in ("no_vin", "double_scan") and len(X):
                it["m"]["risk_cars"] += len(X)
                it["m"]["definite_cars"] += len(X)
                it["m"]["rework_h"] += len(X) * CHECK_MIN / 60


def flag_items(line, cases=()) -> list:
    items = []
    support = []
    for s, c in line.cycles.items():
        free = c[c.incident_id.isna()]
        sig = free[(free.anomaly_flag == 1) & (free.human_flag.fillna(0) == 0)]
        for pat, kind, w, definite, bad, mode, mins, title, action in SIGNAL_KINDS:
            X = sig[sig.anomaly_reason.fillna("").str.contains(pat, regex=True)]
            sig = sig.drop(X.index)
            X = X[~belongs_to_case(X, s, kind, cases)]
            if X.empty:
                continue
            m = empty()
            line.costs(m, s, X, mode)
            m["risk_cars"] += w * len(X)
            m["definite_cars"] += len(X) if definite else 0
            m["known_bad"] += len(X) if bad else 0
            m["rework_h"] += len(X) * mins / 60
            up = STATIONS.get(s, {}).get("upstream")
            sev = line.base[up]["severity"] if kind == "skipped_upstream" and up in line.base else line.base[s]["severity"]
            if kind in ("slow_cycle", "fault_no_code"):
                sev = 3
            items.append(group_item("signal checker", f"{s.upper()}: {len(X)} {title}", s, "data", X, m, sev, action))
        hum = free[free.human_flag == 1]
        for kind, (w, mode, title, action) in PEOPLE_KINDS.items():
            X = hum[hum.human_type == kind]
            X = X[~belongs_to_case(X, s, kind, cases)]
            if X.empty:
                continue
            m = empty()
            line.costs(m, s, X, mode)
            m["risk_cars"] += w * len(X)
            if kind == "struggle":
                m["people_h"] += (X.operator_time_s - line.base[s]["op"]).clip(lower=0).sum() / 3600
            if kind == "retries":
                m["people_h"] += X.retries.sum() * line.base[s]["retry_cost"] / 3600
            sev = 3 if kind == "struggle" else line.base[s]["severity"]
            items.append(group_item("people model", f"{s.upper()}: {len(X)} {title}", s, "people", X, m, sev, action))
        support.append((s, c[c.human_type == "support"]))
    # andon calls nobody answered in time: one item for the whole line
    if sum(len(X) for _, X in support):
        m = empty()
        for s, X in support:
            if len(X):
                line.costs(m, s, X, "stops")
                m["people_h"] += X.andon_response_s.sum() / 3600
        allx = pd.concat([X for _, X in support])
        items.append(group_item("people model", f"{len(allx)} andon calls with no team lead within 3 min",
                                ",".join(s for s, X in support if len(X)), "people", allx, m, 3,
                                "Shift lead: team-lead coverage for andon (support gap, not the operators)"))
    return items


def rest_time_items(line) -> list:
    """Work outside the operator's own crew shift (people model), with the rest time before it."""
    items = []
    X = pd.concat([c[c.human_type == "working_time"].assign(station=s) for s, c in line.cycles.items()])
    if X.empty:
        return items
    X = X.assign(day=X.human_reason.str.extract(r"on (\d{4}-\d{2}-\d{2})")[0])
    for (op, day), g in X.groupby(["operator_id", "day"]):
        reason = g.human_reason.iloc[0]
        rest = re.search(r"~([\d.]+) h", reason)
        m = empty()
        m["cars"] = len(g)
        m["people_h"] = (g.ts.max() - g.ts.min()) / H + 0.5
        m["legal"] = "< 11 h" in reason
        rest_txt = f", only {rest.group(1)} h rest" if rest else ""
        items.append({"source": "people model", "title": f"{op} worked outside their crew's shift on {day}{rest_txt}",
                      "detail": reason, "stations": ",".join(sorted(g.station.unique())), "cause": "people",
                      "culprit": op, "case_id": None, "status": "one-off", "first_ts": g.ts.min(),
                      "last_ts": g.ts.max(), "m": m, "sev": 3,
                      "action": "Shift planning / HR: respect rest time; plan cover without double shifts"})
    return items


def downtime_items(line, case_repairs: set) -> list:
    """Faults (grouped by station + code) and unplanned repairs that no incident explains."""
    items = []
    for s, ev in line.events.items():
        f = ev[ev.event_type == "FAULT"].copy()
        f["code"] = f.fault_code.fillna("no code")
        for code, g in f.groupby("code"):
            m = empty()
            m["lost_cars"] = line.lost_from_stops(s, g.downtime_s.values)
            m["rework_h"] = g.downtime_s.sum() / 3600
            text = g.comment.dropna().iloc[0] if g.comment.notna().any() else "reason unknown"
            items.append(group_item("downtime", f"{s.upper()}: fault {code} - {text} ({len(g)}x, "
                                    f"{g.downtime_s.sum() / 60:.0f} min down)", s, "machine", g, m, 3,
                                    "Maintenance: fault Pareto - fix the top codes"))
        rep = ev[ev.comment.fillna("").str.startswith("Unplanned") & ~ev.ts.isin(case_repairs)]
        for r in rep.itertuples():
            m = empty()
            m["lost_cars"] = line.lost_from_stops(s, [r.downtime_s])
            m["rework_h"] = r.downtime_s / 3600 * REPAIR_CREW
            items.append({"source": "downtime", "title": f"{s.upper()}: {r.comment} ({r.downtime_s / 60:.0f} min, "
                          f"no problem seen before it)", "detail": "", "stations": s, "cause": "machine",
                          "culprit": None, "case_id": None, "status": "one-off", "first_ts": r.ts, "last_ts": r.ts,
                          "m": m, "sev": 3, "action": "Maintenance: was it needed? plan it outside production"})
    return items


# ---------------------------------------------------------------------------
# Future: next week if nothing changes, and a catalog of what could happen
# ---------------------------------------------------------------------------
def next_week(it: dict, line) -> dict:
    if it["status"] == "recurring":
        f = 1.0                                     # this week repeats
    elif it["status"] == "active":
        f = 7 * 24 / max((it["last_ts"] - it["first_ts"]) / H, 24)
    else:
        return empty()
    m = it["m"]
    out = {k: v * f for k, v in m.items() if k not in ("legal", "margin_used")}
    out["legal"], out["margin_used"] = m["legal"], m["margin_used"]
    return out


CATALOG = [   # cause, family, problem, assumption, hours of exposure, effects per car
    ("machine", "quality", "Tool wear: re-hits rising until repaired", "12 h until repair", 12,
     {"retries": 0.25, "nok": 0.02, "rehit3": 0.05, "repair_h": 0.5}),
    ("machine", "pattern", "Calibration drift until noticed", "8 h, a third of the safety margin used", 8,
     {"margin": 0.33, "repair_h": 0.5}),
    ("machine", "speed", "Fixture / pump slowing", "12 h, +9 s machine time", 12, {"extra_s": 9, "repair_h": 0.5}),
    ("machine", "data", "Scanner failing", "12 h, 10% of cars unread", 12, {"untrace": 0.10, "repair_h": 0.3}),
    ("people", "quality", "One operator needs many re-hits", "their blocks for a week (14 h)", 14,
     {"retries": 0.25, "nok": 0.01, "rehit3": 0.05, "people": True}),
    ("people", "speed", "Fatigue in one shift", "4 late hours, +25% hands-on time", 4, {"op_frac": 0.25, "people": True}),
    ("people", "pattern", "Different technique", "their blocks for a week, 15% of margin", 14, {"margin": 0.15}),
    ("people", "data", "Skipped scans", "their blocks for a week, 8% of cars", 14, {"untrace": 0.08}),
    ("method", "quality", "Instruction adds re-work", "2 days, +0.2 re-hits per car", 48, {"retries": 0.2, "rehit3": 0.03}),
    ("method", "speed", "Instruction adds a step", "2 days, +18% hands-on time", 48, {"op_frac": 0.18}),
    ("method", "pattern", "Instruction removes a prep step", "2 days, 30% of margin", 48, {"margin": 0.30}),
    ("method", "data", "Instruction asks for a second scan", "2 days, 8% double scans", 48, {"untrace": 0.08}),
    ("station", "quality", "Bad part batch", "one batch (7 h)", 7, {"retries": 0.2, "nok": 0.02, "rehit3": 0.05}),
    ("station", "pattern", "Different supplier lot", "one batch (7 h), 30% of margin", 7, {"margin": 0.30}),
    ("station", "speed", "Supply interruption", "4 h, +14 s waiting per car", 4, {"wait_s": 14}),
    ("station", "data", "MES / network outage", "1 h, half the scans lost", 1, {"untrace": 0.5}),
]


def catalog(line, seen: set) -> pd.DataFrame:
    rows = []
    for s in line.cycles:
        b, cars = line.base[s], line.cars_per_h[s]
        for cause, fam, problem, assumed, hours, e in CATALOG:
            n = cars * hours
            m = empty()
            extra = n * (e.get("retries", 0) * b["retry_cost"] + e.get("op_frac", 0) * b["op"] + e.get("extra_s", 0))
            m["lost_cars"] = line.lost_from_extra(s, extra, n) + line.lost_from_stops(s, [e.get("repair_h", 0) * 3600])
            if s == line.bottleneck:
                m["lost_cars"] += n * e.get("wait_s", 0) / line.takt
            m["risk_cars"] = n * (0.1 * e.get("nok", 0) + 0.1 * e.get("rehit3", 0) + e.get("margin", 0) + e.get("untrace", 0))
            m["definite_cars"] = n * e.get("untrace", 0)
            m["margin_used"] = e.get("margin", 0)
            m["rework_h"] = n * (e.get("nok", 0) * REWORK_MIN + e.get("untrace", 0) * CHECK_MIN) / 60 + \
                e.get("repair_h", 0) * REPAIR_CREW
            if e.get("people"):
                m["people_h"] = hours if fam == "speed" else n * e.get("retries", 0) * b["retry_cost"] / 3600
            sev = 3 if fam == "speed" else b["severity"]
            sc, _ = score(m, sev)
            cat, rule = categorise(sc, m, sev)
            rows.append({"station": s, "cause": cause, "family": fam, "problem": problem, "assumed": assumed,
                         "risk_cars": round(m["risk_cars"], 1), "definite_cars": round(m["definite_cars"], 1),
                         "lost_cars": round(m["lost_cars"], 1), "rework_h": round(m["rework_h"], 1),
                         "people_h": round(m["people_h"], 1), "score": sc, "category": cat, "rule": rule,
                         "seen_this_week": int((s, cause, fam) in seen)})
    df = pd.DataFrame(rows)
    return df.assign(_o=df.category.map(LEVELS.index)).sort_values(["_o", "score"], ascending=[True, False]).drop(columns="_o")


# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    con = connect()
    names = station_tables(con)
    tables = {t: pd.read_sql(f"SELECT * FROM {t}", con, parse_dates=["ts"]) for t in names}
    has_inc = con.execute("SELECT name FROM sqlite_master WHERE name='incidents'").fetchone()
    if tables[names[0]].anomaly_flag.isna().all() or tables[names[0]].human_flag.isna().all() or not has_inc:
        raise SystemExit("run the other models first: train_model.py, train_people_model.py, find_causes.py")
    inc = pd.read_sql("SELECT * FROM incidents", con, parse_dates=["start_ts", "end_ts"])
    line = Line(tables)

    wi_changes, batch_last = {}, {}
    for s, c in line.cycles.items():
        cs = c.sort_values("ts")
        ch = cs.work_instruction != cs.work_instruction.shift()
        wi_changes[s] = list(zip(cs.ts[ch], cs.work_instruction[ch]))
        batch_last[s] = cs.groupby("part_batch").ts.max().to_dict()

    items = case_items(line, inc, wi_changes, batch_last) if len(inc) else []
    case_repairs = set().union(*[it["repairs"] for it in items]) if items else set()
    flags = flag_items(line, items)
    absorb_into_cases(line, items)
    items += flags + rest_time_items(line) + downtime_items(line, case_repairs)

    rows = []
    for it in items:
        now, sub = score(it["m"], it["sev"])
        nxt_m = next_week(it, line)
        nxt, _ = score(nxt_m, it["sev"])
        cat, rule = categorise(max(now, nxt), it["m"] if now >= nxt else nxt_m, it["sev"])
        if rule and nxt > now + 0.5:
            rule = "next week: " + rule
        m = it["m"]
        rows.append({
            "category": cat, "priority": max(now, nxt), "score_now": now, "score_next": nxt, "rule": rule,
            "source": it["source"], "title": it["title"], "detail": it["detail"], "stations": it["stations"],
            "cause": it["cause"], "culprit": it["culprit"], "case_id": it["case_id"], "status": it["status"],
            "first_ts": pd.Timestamp(it["first_ts"]).strftime("%Y-%m-%d %H:%M"),
            "last_ts": pd.Timestamp(it["last_ts"]).strftime("%Y-%m-%d %H:%M"),
            "cars": int(m["cars"]), "risk_cars": round(m["risk_cars"], 1), "known_bad": int(round(m["known_bad"])),
            "definite_cars": int(round(m["definite_cars"])), "margin_used": round(m["margin_used"], 2),
            "lost_cars": round(m["lost_cars"], 1), "rework_h": round(m["rework_h"], 1), "people_h": round(m["people_h"], 1),
            **{k: round(v, 2) for k, v in sub.items()}, "action": it["action"],
        })
    df = pd.DataFrame(rows)
    df = df.assign(_o=df.category.map(LEVELS.index)).sort_values(["_o", "priority"], ascending=[True, False]).drop(columns="_o")
    df.insert(0, "rank", range(1, len(df) + 1))
    df.insert(0, "impact_id", df["rank"])

    # ---- can we hit 7,500? ----
    built = len(line.cycles[names[-1]])                   # cars that left the last station
    planned = line.events[line.bottleneck].query("comment == 'Planned maintenance'").downtime_s.sum()
    capacity = (line.hours * 3600 - planned) / line.takt
    lost_now = df.lost_cars.sum()
    lost_next = sum(next_week(it, line)["lost_cars"] for it in items)
    gap = capacity - built - lost_now                     # what the ranked problems don't explain
    projected = capacity - lost_next - max(gap, 0)
    five_day = built / (line.hours / 24) * 5
    summary = {
        "week": f"{line.start:%Y-%m-%d %H:%M} to {line.end:%Y-%m-%d %H:%M}", "target_per_week": TARGET_PER_WEEK,
        "cars_built": built, "attainment": f"{built / TARGET_PER_WEEK:.0%}", "bottleneck": line.bottleneck,
        "bottleneck_cycle_s": round(line.takt, 1), "capacity": round(capacity), "lost_cars_ranked": round(lost_now),
        "unexplained_gap": round(gap), "projected_next_week": round(projected),
        "projected_attainment": f"{projected / TARGET_PER_WEEK:.0%}", "five_day_equivalent": round(five_day),
        **{f"items_{c.lower()}": int((df.category == c).sum()) for c in LEVELS},
    }
    seen = {(s, r.cause, r.family) for r in inc.drop_duplicates("case_id").itertuples() for s in r.stations.split(",")}
    cat_df = catalog(line, seen)

    con.executescript(IMPACTS_SQL)
    df.to_sql("impacts", con, if_exists="append", index=False)
    cat_df.to_sql("impact_catalog", con, if_exists="append", index=False)
    pd.DataFrame([(k, str(v)) for k, v in summary.items()], columns=["key", "value"]).to_sql(
        "impact_summary", con, if_exists="append", index=False)
    con.commit()
    con.close()

    # ---- report ----
    print(f"impact ranker: {len(df)} impacts ranked in {time.time() - t0:.1f}s  ->  "
          + "  ".join(f"{c} {(df.category == c).sum()}" for c in LEVELS))
    print(f"\nCan we hit {TARGET_PER_WEEK:,} cars/week?  built {built:,} ({summary['attainment']}); bottleneck "
          f"{line.bottleneck.upper()} at {line.takt:.0f} s/car; capacity {capacity:,.0f}; lost to the ranked problems "
          f"{lost_now:,.0f}; next week if nothing changes ~{projected:,.0f} ({summary['projected_attainment']})")
    print(f"  (the simulated line runs 24/7 - on a 5-day week the same daily rate is {five_day:,.0f} = "
          f"{five_day / TARGET_PER_WEEK:.0%} of target; every 75 lost cars = 1 point)")
    for cat in LEVELS:
        g = df[df.category == cat]
        print(f"\n{cat.upper()} ({len(g)})")
        for r in g.head(14).itertuples():
            nxt = f", next week {r.score_next:.0f}" if r.score_next > r.score_now + 0.5 else ""
            print(f"  #{r.rank:<2} {r.priority:5.1f}  {r.title}  ({r.status}{nxt})" + (f"  [{r.rule}]" if r.rule else ""))
            print(f"        risk cars {r.risk_cars:g}, lost cars {r.lost_cars:g}, rework {r.rework_h:g} h, "
                  f"people {r.people_h:g} h  ->  {r.action}")
        if len(g) > 14:
            print(f"  ... {len(g) - 14} more")
    print("\nIf it happened next week (impact_catalog, top 8 of 32):")
    for r in cat_df.head(8).itertuples():
        print(f"  {r.category:<6} {r.score:5.1f}  {r.station.upper()} {r.cause:<8} {r.problem} ({r.assumed})"
              + (" - seen this week" if r.seen_this_week else ""))


if __name__ == "__main__":
    main()
