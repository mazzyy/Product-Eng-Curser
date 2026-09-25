"""
Line simulator - one week of Giga-style data for ST012 + ST013, with the true answers recorded.

Every cycle has a MACHINE side (measurements, machine time, result, faults) and a HUMAN side
(operator, hands-on time, retries, andon). Crews A/B/C with 4 operators each work 8 h shifts
and rotate every 2 h (ST013 -> ST012). Work-instruction versions and part batches change during
the week, and machines get repaired.

Three kinds of problems are planted, each with its own answer file:

1. ROOT CAUSES  (-> data/injected_causes.csv, scored by the cause finder)
   The same symptom can come from four different causes; only the evidence differs.
     symptom families: quality (re-hits, NOK) | speed (slower) | pattern (signal shift) | data (VIN errors)
     machine  tool wear, calibration drift, sticking fixture, failing scanner - ends with a repair
     people   one operator: many retries, fatigue, slow trainee, different technique, skipped scans
     method   a new work-instruction version that is worse - for everyone on that station
     station  bad part batch, supply interruption, MES outage
   The demo week (default) tells a fixed story; training weeks (--random) get random scenarios.

2. DATA AMBIGUITY  (-> data/injected_labels.csv, scored by train_model.py)
3. PEOPLE PATTERNS  (-> data/injected_people_labels.csv, scored by train_people_model.py)

The station tables carry no hint of what was planted.

    python generate_data.py                      # demo week -> data/factory.db
    python generate_data.py --random --seed 3    # a random week instead
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from station_db import (CAUSES_PATH, DB_PATH, LABELS_PATH, PEOPLE_LABELS_PATH, STATIONS, connect,
                        create_station_table)

START = datetime(2026, 9, 14, 6, 0, 0)          # Monday, start of shift A
VARIANTS = ["Model Y RWD", "Model Y Long Range", "Model Y Performance"]
VARIANT_P = [0.35, 0.50, 0.15]
SHIFT_START = {"A": 6, "B": 14, "C": 22}
CREWS = {s: [f"OP-{s}{i}" for i in range(1, 5)] for s in "ABC"}
ROUND = {"st012": (1, 1), "st013": (3, 3)}      # decimals for primary, secondary
CAUSES = ("machine", "people", "method", "station")
FAMILIES = ("quality", "speed", "pattern", "data")
HOUR = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Root-cause scenarios
# ---------------------------------------------------------------------------
@dataclass
class Scenario:
    sid: str
    cause: str                  # machine | people | method | station
    family: str                 # quality | speed | pattern | data
    stations: tuple
    start: datetime
    end: datetime
    culprit: str | None         # the operator / machine / WI version / batch / system
    desc: str
    effect: dict                # retry (x), op (+frac), fatigue, nok, prim/sec (sigma), mach (s), wait_p, miss, dup
    operator: str | None = None
    batch: str | None = None
    wi: str | None = None
    ramp: bool = False          # grows from 0 to full strength (wear, drift)
    shift_key: tuple | None = None
    bursts: list = field(default_factory=list)
    repair: str | None = None

    def strength(self, sid, t, op, sdate, sh, batch, wi) -> float:
        if sid not in self.stations or not (self.start <= t < self.end):
            return 0.0
        if self.operator and op != self.operator:
            return 0.0
        if self.shift_key and (sdate, sh) != self.shift_key:
            return 0.0
        if self.batch and batch != self.batch:
            return 0.0
        if self.wi and wi != self.wi:
            return 0.0
        if self.bursts and not any(a <= t < b for a, b in self.bursts):
            return 0.0
        return (t - self.start) / (self.end - self.start) if self.ramp else 1.0


def effects(scens, sid, t, op, h, sdate, sh, batch, wi):
    e = {"retry": 1.0, "op": 1.0, "nok": 0.0, "prim": 0.0, "sec": 0.0, "mach": 0.0,
         "wait_p": 0.0, "miss": 0.0, "dup": 0.0}
    hit = []
    for s in scens:
        k = s.strength(sid, t, op, sdate, sh, batch, wi)
        if k <= 0:
            continue
        for key, v in s.effect.items():
            if key == "retry":
                e["retry"] *= 1 + k * (v - 1)
            elif key == "op":
                e["op"] *= 1 + k * v
            elif key == "fatigue":
                e["op"] *= 1 + k * v * max(0.0, h - 2) / 6
            else:
                e[key] += k * v
        hit.append((s, k))
    return e, hit


def u(rng, a, b):
    return float(rng.uniform(a, b))


def at_day(day: float, hh: int = 0, mm: int = 0) -> datetime:
    return START.replace(hour=0) + timedelta(days=day, hours=hh, minutes=mm)


# ---------------------------------------------------------------------------
# Batches, work-instruction versions, maintenance events
# ---------------------------------------------------------------------------
def make_batches(rng, days: int) -> dict:
    out = {}
    for sid, cfg in STATIONS.items():
        t = START - timedelta(hours=u(rng, 0, 6))
        starts, ids = [], []
        while t < START + timedelta(days=days):
            starts.append(t)
            ids.append(f"{cfg['batch'][0]}-{int(rng.integers(1000, 9999))}")
            t += timedelta(hours=u(rng, 5, 9))
        out[sid] = [starts, ids]
    return out


def force_batch(batches, sid, t0, t1, bid, rng):
    """Make one batch run exactly from t0 to t1 (demo story)."""
    starts, ids = batches[sid]
    keep = [(s, i) for s, i in zip(starts, ids) if s < t0 or s >= t1]
    keep += [(t0, bid), (t1, f"{STATIONS[sid]['batch'][0]}-{int(rng.integers(1000, 9999))}")]
    keep.sort()
    batches[sid] = [[s for s, _ in keep], [i for _, i in keep]]


def batch_at(batches, sid, t):
    starts, ids = batches[sid]
    return ids[max(bisect_right(starts, t) - 1, 0)]


def make_wi(rng, days, scens, first=None, decoys=None) -> dict:
    """Work-instruction timeline per station. A method scenario = a new version that is worse;
    it ends when the next version fixes it. Decoys are harmless version changes."""
    end = START + timedelta(days=days)
    out = {}
    for sid, cfg in STATIONS.items():
        meth = [s for s in scens if s.cause == "method" and s.stations[0] == sid]
        changes = [(s.start, s) for s in meth] + [(s.end, None) for s in meth if s.end < end]
        if decoys is not None:
            ds = decoys.get(sid, [])
        else:
            ds = []
            for _ in range(int(rng.integers(1, 3))):
                for _try in range(20):
                    t = START + timedelta(hours=u(rng, 8, days * 24 - 8))
                    if all(abs((t - c).total_seconds()) > 12 * 3600 for c, _ in changes) and \
                       not any(s.start <= t < s.end for s in meth):
                        ds.append(t)
                        break
        changes += [(t, None) for t in ds]
        changes.sort(key=lambda x: x[0])
        v = (first or {}).get(sid) or int(rng.integers(2, 7))
        times, versions = [START - timedelta(days=30)], [f"{cfg['wi']} v{v}"]
        for t, s in changes:
            v += 1
            times.append(t)
            versions.append(f"{cfg['wi']} v{v}")
            if s is not None:
                s.wi = versions[-1]
                s.culprit = versions[-1]
        out[sid] = (times, versions)
    return out


def wi_at(wi, sid, t):
    times, versions = wi[sid]
    return versions[bisect_right(times, t) - 1]


def make_events(rng, days, scens, decoy_repairs=None) -> dict:
    """Planned maintenance every morning, unplanned repairs that end machine problems, decoys."""
    end = START + timedelta(days=days)
    ev = {sid: [] for sid in STATIONS}
    d = START.replace(hour=5, minute=45) + timedelta(days=1)
    while d < end:
        for sid in STATIONS:
            ev[sid].append((d, 900.0, "Planned maintenance"))
        d += timedelta(days=1)
    for s in scens:
        if s.cause == "machine" and s.end < end:
            ev[s.stations[0]].append((s.end, u(rng, 1200, 2400), f"Unplanned repair: {s.repair}"))
    if decoy_repairs is None:
        decoy_repairs = []
        for _ in range(int(rng.integers(1, 3))):
            sid = str(rng.choice(list(STATIONS)))
            eq = list(STATIONS[sid]["equipment"].values())[int(rng.integers(0, 4))]
            decoy_repairs.append((sid, START + timedelta(hours=u(rng, 8, days * 24 - 4)), eq[2]))
    for sid, t, text in decoy_repairs:
        ev[sid].append((t, u(rng, 900, 1800), f"Unplanned repair: {text}"))
    for sid in ev:
        ev[sid].sort(key=lambda x: x[0])
    return ev


# ---------------------------------------------------------------------------
# Scenario sets
# ---------------------------------------------------------------------------
def demo_scenarios(rng, batches, days) -> tuple[list, dict]:
    """The demo week: one story per cause, several per family. Returns scenarios + WI setup."""
    end = START + timedelta(days=days)
    e12, e13 = STATIONS["st012"]["equipment"], STATIONS["st013"]["equipment"]
    force_batch(batches, "st012", at_day(0, 13, 40), at_day(0, 21, 10), "BL-4471", rng)
    both = tuple(STATIONS)
    scens = [
        Scenario("M1", "machine", "pattern", ("st012",), at_day(3, 0), at_day(3, 8, 30), e12["pattern"][0],
                 e12["pattern"][1].capitalize(), {"prim": 2.5}, ramp=True, repair=e12["pattern"][2]),
        Scenario("M2", "machine", "quality", ("st013",), at_day(4, 6, 30), at_day(4, 21, 30), e13["quality"][0],
                 e13["quality"][1].capitalize(), {"retry": 4.5, "nok": 0.02}, ramp=True,
                 repair=e13["quality"][2]),
        Scenario("P1", "people", "pattern", ("st012",), START, end, "OP-B2",
                 "OP-B2 seats the subframe bolts with a different technique", {"sec": -1.3}, operator="OP-B2"),
        Scenario("P2", "people", "quality", both, START, end, "OP-C1",
                 "OP-C1 needs many re-hits / re-tests (technique or training)", {"retry": 4.0, "nok": 0.008},
                 operator="OP-C1"),
        Scenario("P3", "people", "speed", both, at_day(3, 22), at_day(4, 6), "OP-C4",
                 "OP-C4 tiring through the night shift", {"fatigue": 0.32}, operator="OP-C4",
                 shift_key=(date(2026, 9, 17), "C")),
        Scenario("P4", "people", "speed", both, at_day(5, 14), at_day(5, 22), "OP-B4",
                 "OP-B4 tiring through the shift", {"fatigue": 0.32}, operator="OP-B4",
                 shift_key=(date(2026, 9, 19), "B")),
        Scenario("T1", "method", "speed", ("st012",), at_day(1, 6), at_day(2, 14), None,
                 "New work instruction added a manual torque-check step", {"op": 0.18}),
        Scenario("T2", "method", "data", ("st013",), at_day(5, 14), end, None,
                 "New work instruction asks for a second VIN scan", {"dup": 0.08}),
        Scenario("S1", "station", "quality", ("st012",), at_day(0, 13, 40), at_day(0, 21, 10), "bolt batch BL-4471",
                 "Bolt batch BL-4471 out of tolerance", {"retry": 3.5, "nok": 0.02}, batch="BL-4471"),
        Scenario("S2", "station", "speed", ("st012",), at_day(2, 22), at_day(3, 2, 30), "Material supply to ST012",
                 "Body supply from ST011 interrupted", {"wait_p": 0.4}),
        Scenario("S3", "station", "data", both, at_day(2, 10), at_day(2, 12), "MES / line network",
                 "MES / line network outage - scans lost", {"miss": 0.5},
                 bursts=[(at_day(2, 10, 15), at_day(2, 10, 50)), (at_day(2, 11, 30), at_day(2, 11, 55))]),
    ]
    wi_setup = {"first": {"st012": 3, "st013": 6},
                "decoys": {"st012": [at_day(4, 6)], "st013": [at_day(1, 10)]},
                "repairs": [("st012", at_day(5, 9), e12["quality"][2])]}
    return scens, wi_setup


def random_scenario(rng, cause, sid_name, days, ops, batches):
    end = START + timedelta(days=days)
    fam = str(rng.choice(FAMILIES))
    sid = str(rng.choice(list(STATIONS)))
    cfg = STATIONS[sid]
    t0 = (START + timedelta(hours=u(rng, 10, (days - 1.5) * 24))).replace(second=0, microsecond=0)
    both = tuple(STATIONS)
    sign = 1.0 if rng.random() < 0.5 else -1.0

    if cause == "machine":
        name, what, repair = cfg["equipment"][fam]
        eff = {"quality": {"retry": u(rng, 3.5, 5), "nok": u(rng, 0.01, 0.03)},
               "pattern": {"prim": u(rng, 2.0, 2.8)},
               "speed": {"mach": u(rng, 6, 12)},
               "data": {"miss": u(rng, 0.08, 0.15)}}[fam]
        return Scenario(sid_name, cause, fam, (sid,), t0, min(t0 + u(rng, 8, 30) * HOUR, end), name,
                        what.capitalize(), eff, ramp=True, repair=repair)

    if cause == "people":
        op = str(rng.choice(sorted(ops)))
        crew = op[3]
        if fam == "speed" and rng.random() < 0.5:
            sdate = (START + timedelta(days=int(rng.integers(0, days - 1)))).date()
            st = datetime.combine(sdate, datetime.min.time()) + SHIFT_START[crew] * HOUR
            return Scenario(sid_name, cause, fam, both, st, st + 8 * HOUR, op, f"{op} tiring through the shift",
                            {"fatigue": u(rng, 0.25, 0.35)}, operator=op, shift_key=(sdate, crew))
        en = min(t0 + timedelta(days=u(rng, 2, 6)), end)
        eff, stations, desc = {
            "quality": ({"retry": u(rng, 3.5, 5), "nok": u(rng, 0.005, 0.015)}, both, f"{op} needs many re-hits"),
            "speed": ({"op": u(rng, 0.18, 0.28)}, both, f"{op} slower than standard (new to the job)"),
            "pattern": ({"sec": sign * u(rng, 1.2, 1.7)}, (sid,), f"{op} works with a different technique at {sid.upper()}"),
            "data": ({"miss": u(rng, 0.06, 0.12)}, both, f"{op} skips VIN scans"),
        }[fam]
        return Scenario(sid_name, cause, fam, stations, t0, en, op, desc, eff, operator=op)

    if cause == "method":
        en = min(t0 + u(rng, 24, 54) * HOUR, end)    # longer would become the new normal within one week
        eff, what = {
            "quality": ({"retry": u(rng, 3, 4)}, "changed the work sequence - more re-work"),
            "speed": ({"op": u(rng, 0.15, 0.22)}, "added a manual check step"),
            "pattern": ({"sec": sign * u(rng, 1.3, 1.8)}, "removed a preparation step"),
            "data": ({"dup": u(rng, 0.06, 0.10)}, "asks for a second VIN scan"),
        }[fam]
        return Scenario(sid_name, cause, fam, (sid,), t0, en, None, f"New work instruction {what}", eff)

    # station
    if fam in ("quality", "pattern"):
        starts, ids = batches[sid]
        cand = [i for i, s in enumerate(starts) if START + 10 * HOUR <= s <= end - 20 * HOUR]
        i = int(rng.choice(cand))
        bst, ben = starts[i], starts[i + 1] if i + 1 < len(starts) else end
        name = cfg["batch"][1]
        if fam == "quality":
            eff, desc = {"retry": u(rng, 3, 4.5), "nok": u(rng, 0.01, 0.025)}, f"{name} {ids[i]} out of tolerance"
        else:
            eff, desc = {"sec": sign * u(rng, 1.3, 1.8)}, f"{name} {ids[i]} from a different lot (coating differs)"
        return Scenario(sid_name, cause, fam, (sid,), bst, ben, f"{name} {ids[i]}", desc, eff, batch=ids[i])
    if fam == "speed":
        desc = "Body supply from ST011 interrupted" if sid == "st012" else "Coolant supply to ST013 interrupted"
        return Scenario(sid_name, cause, fam, (sid,), t0, t0 + u(rng, 3, 6) * HOUR, f"Material supply to {sid.upper()}",
                        desc, {"wait_p": u(rng, 0.3, 0.45)})
    bursts = []
    for _ in range(int(rng.integers(2, 4))):
        b0 = t0 + u(rng, 0, 5.3) * HOUR
        bursts.append((b0, b0 + timedelta(minutes=u(rng, 20, 45))))
    return Scenario(sid_name, cause, "data", both, t0, t0 + 6 * HOUR, "MES / line network",
                    "MES / line network outage - scans lost", {"miss": u(rng, 0.4, 0.6)}, bursts=sorted(bursts))


def random_scenarios(rng, days, ops, batches, n_target=6) -> list:
    """Balanced random causes; same-family scenarios at least 36 h apart (people ones may overlap)."""
    scens = []
    order = list(CAUSES) * 3
    rng.shuffle(order)
    for cause in order:
        if len(scens) >= n_target:
            break
        for _ in range(25):
            s = random_scenario(rng, cause, f"R{len(scens) + 1}", days, ops, batches)
            pad = 36 * HOUR          # people problems may overlap anything - they only hit one person's blocks
            if all(o.family != s.family or "people" in (o.cause, s.cause)
                   or s.start >= o.end + pad or o.start >= s.end + pad for o in scens):
                scens.append(s)
                break
    return scens


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
def shift_of(t: datetime) -> str:
    return "A" if 6 <= t.hour < 14 else "B" if 14 <= t.hour < 22 else "C"


def shift_info(t: datetime):
    sh = shift_of(t)
    start = t.replace(hour=SHIFT_START[sh], minute=0, second=0, microsecond=0)
    if start > t:
        start -= timedelta(days=1)
    return sh, start, (t - start).total_seconds() / 3600


def operator_at(sid: str, t: datetime):
    """2-hour job rotation: whoever ran ST013 in block b runs ST012 in block b+1."""
    sh, start, h = shift_info(t)
    block = min(int(h // 2), 3)
    idx = block if sid == "st012" else (block + 1) % 4
    return CREWS[sh][idx], sh, start.date(), h, block


def make_operators(rng) -> dict:
    ops = {}
    for crew in CREWS.values():
        for op in crew:
            skill = int(rng.choice([2, 3, 3, 4]))
            ops[op] = {
                "skill": skill,
                "speed": float(rng.normal(1.0, 0.02)) * {2: 1.04, 3: 1.0, 4: 0.97}[skill],
                "retry_rate": {2: 0.10, 3: 0.08, 4: 0.065}[skill],
            }
    return ops


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def base_row(sid: str, t: datetime, event_type: str) -> dict:
    cfg = STATIONS[sid]
    p, s = cfg["primary"], cfg["secondary"]
    return {
        "ts": t, "shift": shift_of(t), "event_type": event_type, "vin": None, "model": None,
        "work_instruction": None, "part_batch": None, "cycle_time_s": None, "machine_time_s": None,
        "operator_id": None, "operator_skill": None, "operator_time_s": None, "wait_time_s": None,
        "retries": None, "andon_pulled": None, "andon_response_s": None,
        "primary_name": p["name"], "primary_value": None, "primary_unit": p["unit"],
        "primary_lsl": p["lsl"], "primary_usl": p["usl"],
        "secondary_name": s["name"], "secondary_value": None, "secondary_unit": s["unit"],
        "secondary_lsl": s["lsl"], "secondary_usl": s["usl"],
        "temperature_c": None, "result": None, "fault_code": None, "downtime_s": None,
        "comment": None,
        "_labels": [], "_hlabels": [], "_scen": [], "_sdate": None, "_block": None, "_true_vin": None,
    }


def in_spec(sig: dict, v: float) -> bool:
    return sig["lsl"] <= v <= sig["usl"]


def out_of_spec_value(sig: dict, side: str, rng) -> float:
    step = sig["sigma"] * rng.uniform(0.5, 2.5)
    return sig["lsl"] - step if side == "low" else sig["usl"] + step


def simulate(sid, days, rng, ops, scens, batches, wi, events, upstream=None) -> pd.DataFrame:
    """Walk through time cycle by cycle. A downstream station gets the upstream station's cars."""
    cfg = STATIONS[sid]
    p, s, k = cfg["primary"], cfg["secondary"], cfg["k"]
    ct_target, op_std = cfg["cycle_time_s"], cfg["operator_time_s"]
    machine_std, retry_cost = ct_target - op_std, cfg["retry_cost_s"]
    end = START + timedelta(days=days)
    rows, t, last_end, prev_vin = [], START, None, None
    ev, ev_i = events[sid], 0

    if upstream is not None:
        up = upstream[upstream.event_type == "CYCLE"]
        vins, models = up._true_vin.tolist(), up.model.tolist()
        arrivals = (up.ts + pd.to_timedelta(up.cycle_time_s + rng.uniform(60, 120, len(up)), unit="s")).dt.floor("s").tolist()
    else:
        vins = models = arrivals = None

    serial, i = 100000, 0
    while True:
        if vins is not None:
            if i >= len(vins):
                break
            t = max(t, arrivals[i].to_pydatetime())
        if t >= end:
            break

        # maintenance: planned every morning, unplanned repairs when a machine is fixed
        if ev_i < len(ev) and t >= ev[ev_i][0]:
            et, dur, text = ev[ev_i]
            r = base_row(sid, et, "MAINTENANCE")
            r.update(downtime_s=round(dur, 0), comment=text)
            rows.append(r)
            t = max(t, et + timedelta(seconds=dur))
            last_end, ev_i = t, ev_i + 1
            continue

        # unplanned fault, about 1 per 300 cycles
        if rng.random() < 1 / 300:
            code = rng.choice(list(cfg["fault_codes"]))
            dur = round(float(rng.gamma(2.0, 90.0)), 0)
            r = base_row(sid, t, "FAULT")
            r.update(fault_code=code, downtime_s=dur, comment=cfg["fault_codes"][code])
            rows.append(r)
            t += timedelta(seconds=dur)
            last_end = t

        # ---- context of this cycle ----
        op, sh, sdate, h, block = operator_at(sid, t)
        batch, wi_now = batch_at(batches, sid, t), wi_at(wi, sid, t)
        e, hit = effects(scens, sid, t, op, h, sdate, sh, batch, wi_now)
        if e["wait_p"] > 0 and rng.random() < e["wait_p"]:               # waiting for material
            t += timedelta(seconds=u(rng, 15, 60))
            op, sh, sdate, h, block = operator_at(sid, t)

        r = base_row(sid, t, "CYCLE")
        if vins is not None:
            true_vin, r["model"] = vins[i], models[i]
        else:
            true_vin = f"XP7YSIM{serial:07d}"
            r["model"] = rng.choice(VARIANTS, p=VARIANT_P)
            serial += 1
        r["_true_vin"] = r["vin"] = true_vin
        prof = ops[op]
        r.update(operator_id=op, operator_skill=prof["skill"], _sdate=sdate, _block=block,
                 work_instruction=wi_now, part_batch=batch, _scen=[x.sid for x, _ in hit])

        # ---- human side ----
        tired = 1.0 + 0.02 * h / 8                                     # everyone slows a little
        retries = int(rng.poisson(prof["retry_rate"] * e["retry"] * (1 + 3 * (tired - 1))))
        op_time = op_std * prof["speed"] * tired * e["op"] * rng.lognormal(0, 0.045)
        andon, response = 0, None
        roll = rng.random()
        if roll < 0.004:                                               # asks for help
            andon = 1
            response = float(rng.gamma(3.0, 15.0))
            if rng.random() < 0.15:                                    # ...and nobody comes
                response = u(rng, 240, 480)
                r["_hlabels"].append("andon_no_response")
            op_time += response + float(rng.gamma(2.0, 10.0))
        elif roll < 0.006:                                             # struggles, doesn't ask
            op_time += ct_target * rng.uniform(1.2, 2.5)
            r["_labels"].append("cycle_spike")
            r["_hlabels"].append("silent_struggle")
        elif roll < 0.0077:                                            # far too fast
            op_time *= rng.uniform(0.50, 0.62)
            r["_labels"].append("fast_cycle")
            r["_hlabels"].append("rushed_cycle")
        elif roll < 0.009:                                             # re-hits until it passes
            retries += int(rng.integers(4, 8))
            r["_hlabels"].append("retry_burst")
        op_time += retries * retry_cost
        machine_time = machine_std + rng.normal(0, 0.5) + e["mach"]
        ct = machine_time + op_time

        # ---- machine side ----
        hour = t.hour + t.minute / 60
        temp = cfg["temperature_c"] + 1.5 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 0.3)
        prim = rng.normal(p["nominal"], p["sigma"])
        if sid == "st013":                                  # leak test reads a bit higher when warm
            prim += 0.05 * (temp - cfg["temperature_c"])
        prim += e["prim"] * p["sigma"]
        for x, kk in hit:
            if x.cause == "machine" and "prim" in x.effect and kk * x.effect["prim"] >= 1.0:
                r["_labels"].append("drift")
        sec = s["nominal"] + k * (prim - p["nominal"]) + rng.normal(0, s["sigma"]) + e["sec"] * s["sigma"]

        # genuine reject: clearly out of spec AND labelled NOK -> consistent, not ambiguous
        if rng.random() < 0.004 + e["nok"]:
            which, side, code = cfg["nok_modes"][0 if e["nok"] > 0 else rng.integers(len(cfg["nok_modes"]))]
            if which == "primary":
                prim = out_of_spec_value(p, side, rng)
                sec = s["nominal"] + k * (prim - p["nominal"]) + rng.normal(0, s["sigma"])   # physics still holds
            else:
                sec = out_of_spec_value(s, side, rng)
            r["comment"] = "Auto-reject, sent to rework"

        # data errors caused by a scenario (scanner, skipped scan, second scan, MES)
        if e["miss"] > 0 and rng.random() < e["miss"]:
            r["vin"] = None
            r["_labels"].append("missing_vin")
        elif e["dup"] > 0 and prev_vin and rng.random() < e["dup"]:
            r["vin"] = prev_vin
            r["_labels"].append("duplicate_vin")
        prev_vin = true_vin

        dp, ds = ROUND[sid]
        prim = max(prim, 0.0) if p["lsl"] == 0 else prim
        r.update(
            cycle_time_s=round(float(ct), 1),
            machine_time_s=round(float(machine_time), 1),
            operator_time_s=round(float(op_time), 1),
            wait_time_s=None if last_end is None else round(max(0.0, (t - last_end).total_seconds()), 1),
            retries=retries, andon_pulled=andon,
            andon_response_s=None if response is None else round(response, 0),
            primary_value=round(float(prim), dp),
            secondary_value=round(float(sec), ds),
            temperature_c=round(float(temp), 1),
        )
        ok = in_spec(p, r["primary_value"]) and in_spec(s, r["secondary_value"])
        r["result"] = "OK" if ok else "NOK"
        if not ok:
            bad_p = not in_spec(p, r["primary_value"])
            side = ("low" if r["primary_value"] < p["lsl"] else "high") if bad_p else \
                   ("low" if r["secondary_value"] < s["lsl"] else "high")
            which = "primary" if bad_p else "secondary"
            codes = [c for w, sd, c in cfg["nok_modes"] if w == which and sd == side]
            r["fault_code"] = codes[0] if codes else f"NOK-{which.upper()}-{side.upper()}"
            r["comment"] = r["comment"] or "Auto-reject, sent to rework"
        rows.append(r)

        last_end = t + timedelta(seconds=float(ct))
        t = last_end + timedelta(seconds=u(rng, 2, 4))                # index / transfer time
        i += 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Data-quality ambiguities, injected after simulation
# ---------------------------------------------------------------------------
def pick(df: pd.DataFrame, n: int, rng, used: set) -> list:
    """Normal OK cycles not touched by anything else (and not first/last rows)."""
    ok = df.index[(df.event_type == "CYCLE") & (df.result == "OK") & (df._labels.str.len() == 0)
                  & (df._hlabels.str.len() == 0) & (df._scen.str.len() == 0) & (df.andon_pulled == 0)]
    pool = [ix for ix in ok[5:-5] if ix not in used]
    chosen = list(rng.choice(pool, size=min(n, len(pool)), replace=False))
    used.update(chosen)
    return chosen


def label(df, ix, name, col="_labels"):
    df.at[ix, col] = df.at[ix, col] + [name]


def inject(sid: str, df: pd.DataFrame, rng) -> pd.DataFrame:
    cfg = STATIONS[sid]
    p, s, k = cfg["primary"], cfg["secondary"], cfg["k"]
    dp, ds = ROUND[sid]
    used: set = set()

    # stuck sensor: the primary reading freezes for 5-9 cycles in a row
    cyc = df.index[df.event_type == "CYCLE"].tolist()
    for start in pick(df, 3, rng, used):
        pos = cyc.index(start)
        frozen = df.at[start, "primary_value"]
        for ix in cyc[pos + 1:pos + int(rng.integers(5, 10))]:
            if df.at[ix, "result"] != "OK":
                break
            df.at[ix, "primary_value"] = frozen
            label(df, ix, "stuck_sensor")
            used.add(ix)

    for ix in pick(df, 20, rng, used):                  # value out of spec but result says OK
        which = rng.choice(["primary", "secondary"])
        sig = p if which == "primary" else s
        side = "high" if (sig["lsl"] == 0 or rng.random() < 0.5) else "low"
        df.at[ix, f"{which}_value"] = round(out_of_spec_value(sig, side, rng), dp if which == "primary" else ds)
        label(df, ix, "ok_but_out_of_spec")

    for ix in pick(df, 20, rng, used):                  # rejected with no reason
        df.at[ix, "result"] = "NOK"
        label(df, ix, "nok_without_reason")

    for ix in pick(df, 12, rng, used):
        df.at[ix, "vin"] = None
        label(df, ix, "missing_vin")
    for ix in pick(df, 12, rng, used):
        prev = df.loc[:ix - 1]
        df.at[ix, "vin"] = prev[prev.event_type == "CYCLE"].vin.dropna().iloc[-1]
        label(df, ix, "duplicate_vin")

    for ix in pick(df, 12, rng, used):                  # sensor dropout
        df.at[ix, "primary_value"] = None
        label(df, ix, "missing_measurement")

    for ix in pick(df, 30, rng, used):                  # in spec, but off the normal pattern
        sign = 1.0 if rng.random() < 0.5 else -1.0
        prim = p["nominal"] + sign * rng.uniform(1.5, 3.0) * p["sigma"]
        expected = s["nominal"] + k * (prim - p["nominal"])
        push = -np.sign(k * sign) if k * sign != 0 else 1.0
        sec = expected + push * rng.uniform(4.0, 7.0) * s["sigma"]
        sec = float(np.clip(sec, s["lsl"] + 0.3 * s["sigma"], s["usl"] - 0.3 * s["sigma"]))
        prim = float(np.clip(prim, p["lsl"] + 0.3 * p["sigma"], p["usl"] - 0.3 * p["sigma"]))
        df.at[ix, "primary_value"] = round(prim, dp)
        df.at[ix, "secondary_value"] = round(sec, ds)
        label(df, ix, "pattern_mismatch")

    faults = df.index[df.event_type == "FAULT"].tolist()
    for ix in rng.choice(faults, size=min(6, len(faults)), replace=False):
        df.at[ix, "fault_code"] = None
        df.at[ix, "comment"] = None
        label(df, ix, "fault_without_code")
    return df


def inject_logins(st012: pd.DataFrame, st013: pd.DataFrame, rng, ops: dict) -> None:
    """Login problems: only the operator_id is wrong, the work itself is normal."""
    cyc = st013[(st013.event_type == "CYCLE") & (st013._block > 0)]
    starts = cyc[cyc._block != cyc._block.shift()]
    for ix in rng.choice(starts.index, size=3, replace=False):
        sh, sdate, block = st013.at[ix, "shift"], st013.at[ix, "_sdate"], int(st013.at[ix, "_block"])
        stale = CREWS[sh][block % 4]                            # ST013 operator of the previous block
        same_block = cyc[(cyc._sdate == sdate) & (cyc["shift"] == sh) & (cyc._block == block)].index
        for j in same_block[: int(rng.integers(15, 35))]:
            st013.at[j, "operator_id"] = stale
            st013.at[j, "operator_skill"] = ops[stale]["skill"]
            label(st013, j, "ghost_login", "_hlabels")

    night = st012[(st012.event_type == "CYCLE") & (st012["shift"] == "C") & (st012._block == 1)]
    sdate = rng.choice(sorted(set(night._sdate))[:-1])
    cover = str(rng.choice(CREWS["A"]))
    for j in night[night._sdate == sdate].index:
        st012.at[j, "operator_id"] = cover
        st012.at[j, "operator_skill"] = ops[cover]["skill"]
        label(st012, j, "off_shift", "_hlabels")


# ---------------------------------------------------------------------------
# One week, in memory
# ---------------------------------------------------------------------------
PEOPLE_PATTERN = {"quality": "high_retries", "pattern": "technique_deviation", "data": "skipped_scans"}


def build_week(seed: int = 7, days: int = 7, demo: bool = True) -> dict:
    """Simulate one week. Returns the station tables (as written to the DB) and the answer keys."""
    rng = np.random.default_rng(seed)
    ops = make_operators(rng)
    batches = make_batches(rng, days)
    if demo:
        scens, setup = demo_scenarios(rng, batches, days)
    else:
        scens, setup = random_scenarios(rng, days, ops, batches), {}
    wi = make_wi(rng, days, scens, setup.get("first"), setup.get("decoys"))
    events = make_events(rng, days, scens, setup.get("repairs"))

    st012 = simulate("st012", days, rng, ops, scens, batches, wi, events)
    st013 = simulate("st013", days, rng, ops, scens, batches, wi, events, upstream=st012)
    st012 = inject("st012", st012, rng)
    st013 = inject("st013", st013, rng)
    inject_logins(st012, st013, rng, ops)
    lost = pick(st012, 8, rng, set())                   # ST012 records lost, cars still reach ST013
    st012 = st012.drop(index=lost)
    seen = set(st012.vin.dropna())
    for ix in st013.index[(st013.event_type == "CYCLE") & st013.vin.notna()]:
        if st013.at[ix, "vin"] not in seen:
            label(st013, ix, "skipped_upstream")

    tables, station_labels, people_labels = {}, [], []
    for sid, df in (("st012", st012), ("st013", st013)):
        df = df.sort_values("ts", kind="stable").reset_index(drop=True)
        df.insert(0, "event_id", np.arange(1, len(df) + 1))
        df["ts"] = pd.to_datetime(df.ts).dt.strftime("%Y-%m-%d %H:%M:%S")
        for c in ("anomaly_score", "anomaly_flag", "anomaly_type", "anomaly_reason",
                  "human_score", "human_flag", "human_type", "human_reason", "incident_id"):
            df[c] = None
        lab = df[df._labels.str.len() > 0][["event_id", "_labels"]].explode("_labels")
        lab.insert(0, "station", sid)
        station_labels.append(lab.rename(columns={"_labels": "pattern"}))
        hl = df[df._hlabels.str.len() > 0][["event_id", "operator_id", "_sdate", "shift", "_hlabels"]]
        hl = hl.explode("_hlabels").rename(columns={"_hlabels": "pattern", "_sdate": "shift_date"})
        hl.insert(0, "station", sid)
        hl.insert(0, "level", "cycle")
        people_labels.append(hl)
        tables[sid] = df

    # operator-shift ground truth for the people model, from the people scenarios
    both = pd.concat([st012, st013])
    both = both[both.event_type == "CYCLE"]
    shift_rows = []
    for sc in scens:
        if sc.cause != "people":
            continue
        name = "fatigue" if "fatigue" in sc.effect else "slow_pace" if sc.family == "speed" else PEOPLE_PATTERN[sc.family]
        hits = both[both._scen.apply(lambda x, i=sc.sid: i in x)]
        for (op, sdate, sh) in hits[["operator_id", "_sdate", "shift"]].drop_duplicates().itertuples(index=False):
            shift_rows.append(("operator_shift", None, None, op, str(sdate), sh, name))
    shifts = pd.DataFrame(shift_rows, columns=["level", "station", "event_id", "operator_id", "shift_date", "shift", "pattern"])

    causes = pd.DataFrame([{
        "scenario_id": sc.sid, "cause": sc.cause, "family": sc.family, "stations": ",".join(sc.stations),
        "start": sc.start.strftime("%Y-%m-%d %H:%M"), "end": sc.end.strftime("%Y-%m-%d %H:%M"),
        "culprit": sc.culprit, "description": sc.desc, "operator": sc.operator, "batch": sc.batch, "wi": sc.wi,
        "shift_date": str(sc.shift_key[0]) if sc.shift_key else None, "shift": sc.shift_key[1] if sc.shift_key else None,
        "bursts": ";".join(f"{a:%Y-%m-%d %H:%M}/{b:%Y-%m-%d %H:%M}" for a, b in sc.bursts) or None,
    } for sc in scens])

    return {
        "tables": {sid: df.drop(columns=[c for c in df.columns if c.startswith("_")]) for sid, df in tables.items()},
        "station_labels": pd.concat(station_labels, ignore_index=True),
        "people_labels": pd.concat(people_labels + [shifts], ignore_index=True),
        "causes": causes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--random", action="store_true", help="random root-cause scenarios instead of the demo story")
    args = ap.parse_args()
    week = build_week(args.seed, args.days, demo=not args.random)

    if DB_PATH.exists():
        DB_PATH.unlink()
    con = connect()
    for sid, df in week["tables"].items():
        create_station_table(con, sid)
        df.to_sql(sid, con, if_exists="append", index=False)
        print(f"{sid}: {len(df):>6} rows  ({(df.event_type == 'CYCLE').sum()} cycles)")
    con.commit()
    con.close()
    week["station_labels"].to_csv(LABELS_PATH, index=False)
    week["people_labels"].to_csv(PEOPLE_LABELS_PATH, index=False)
    week["causes"].to_csv(CAUSES_PATH, index=False)

    print(f"\nDatabase       -> {DB_PATH}")
    print(f"Answer keys    -> {LABELS_PATH.name}, {PEOPLE_LABELS_PATH.name}, {CAUSES_PATH.name}")
    print("\nplanted root causes:")
    for r in week["causes"].itertuples():
        print(f"  {r.scenario_id:<4} {r.cause:<8} {r.family:<8} {r.start} -> {r.end}  {r.culprit}: {r.description}")
    print("\nstation data patterns:", week["station_labels"].pattern.value_counts().to_dict())
    print("people patterns:", week["people_labels"].pattern.value_counts().to_dict())


if __name__ == "__main__":
    main()
