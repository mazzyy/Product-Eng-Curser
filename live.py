"""
Live line - stream a simulated week in accelerated real time and run every model on the data as it arrives.

The simulator (model 0) builds a week - the demo story or a new random one. The clock then releases that
data car by car, as the MES would, and each model runs when it would run on a real line:

  every cycle          signal checker (model 1)   rules + the trained isolation forest, on each new cycle
  every 2 h block      cause finder (model 2)      on all data so far; a cause must hold for 2 passes
                       -> impact ranker (3)        priority of each confirmed case
                       -> containment (7)          stop or not, which cars wait for a check
  every shift end      people model                operator-shift findings of the shift that just ended
  when a note is       floor listener (4)          GPT-5 (or the offline rules) -> facts, linked to the
  written                                          incidents the cause finder knows at that moment
  before a WI goes     method checker (5)          design + people rules; the change manager's gate (6)
  live / 8 h after                                 measured hands-on time vs the plan
  on a repair          maintenance plan (8)        next check due from the Weibull plan

Nothing is written to the database: the live week lives in memory, the demo database stays as it is.

    python live.py --fast            # run a whole week as fast as possible and print the event log
    python live.py --fast --random --seed 21
    (the app starts it from the Live line page: POST /api/live/start)
"""
from __future__ import annotations

import argparse
import sqlite3
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import joblib
import numpy as np
import pandas as pd

import change_manager as CM
import containment as CT
import floor_listener as FL
import generate_notes
import impact_ranker as IR
import method_checker as MC
import method_data
from find_causes import find, load_bundle
from generate_data import build_week
from station_db import DB_PATH, MODELS_DIR, STATIONS
from train_model import FEATURES, ROLL, Z_CAP, Z_GUARD, explain, rule_checks
from train_people_model import run_people

H = pd.Timedelta(hours=1)
ORDER = ["st012", "st013"]            # upstream first, so ST013 can check its VINs against ST012
SPEEDS = [900, 1800, 3600, 7200]      # simulated seconds per real second (3600: one hour per second)
TICK_S = 0.2                          # real seconds between clock ticks
CONTEXT = 60                          # earlier rows the signal checker sees around new cycles
WARMUP_H = 8                          # the cause finder learns each station's normal from the first shift
CONFIRM_PASSES = 2                    # a cause must hold in 2 passes in a row (4 h of data) before we act
CONFIRM_FAST = 0.85                   # ... or be this sure at once
MIN_CONF = 0.6
OLD_WI_DAYS = 7                       # a WI that ran for weeks is not blamed for a sudden change unless the finder is sure
BURST_N, BURST_GAP_H = 6, 3           # unusual cycles at one station within an hour -> one alert, then quiet
PRECHECK_H = 2                        # the method checker sees a new WI this long before it goes live
AFTER_H = 8                           # ... and checks the reality one shift after
MAX_POINTS = 4000                     # per station, kept for the charts
PEOPLE_SEV = {"technique": "warning", "fatigue": "warning", "pace": "warning", "retries": "warning",
              "quality": "warning", "struggle": "warning", "rushed": "warning"}
SEV_RANK = {"info": 0, "good": 0, "warning": 1, "serious": 2, "critical": 3}
LEVEL_SEV = {"High": "serious", "Medium": "warning", "Low": "info"}

NODES = {   # id: (label, what the counter counts) - the same ids as the workflow diagram
    "mes": ("Station data", "cycles"), "notes": ("Floor notes", "notes"), "wi": ("Work instructions", "versions"),
    "mlog": ("Maintenance log", "entries"), "signal": ("Signal checker", "flags"), "people": ("People model", "findings"),
    "floor": ("Floor listener", "facts"), "method": ("Method checker", "checks"), "cause": ("Cause finder", "incidents"),
    "impact": ("Impact ranker", "ranked"), "contain": ("Containment", "cars held"), "change": ("Change manager", "gates"),
    "maint": ("Maintenance plan", "repairs"), "copilot": ("Copilot", "tools"), "app": ("Engineer", "alerts"),
}


def ts(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M")


def shift_of(t: pd.Timestamp) -> tuple[str, str]:
    """(shift_date, shift) of the shift running at t."""
    h = t.hour
    sh = "A" if 6 <= h < 14 else "B" if 14 <= h < 22 else "C"
    start = t.normalize() + pd.Timedelta(hours={"A": 6, "B": 14, "C": 22}[sh])
    if start > t:
        start -= pd.Timedelta(days=1)
    return start.strftime("%Y-%m-%d"), sh


class Case:
    def __init__(self, cid, key):
        self.id, self.key = cid, key
        self.cause, self.culprit, self.family = key
        self.status, self.missing = "open", 0
        self.data: dict = {}


class LiveEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.gen = 0
        self.status, self.error = "idle", None
        self._reset_state()

    # ------------------------------------------------------------------------------------------
    # control
    # ------------------------------------------------------------------------------------------
    def _reset_state(self):
        self.events, self.seq, self.pseq = [], 0, 0
        self.points = {s: deque(maxlen=MAX_POINTS) for s in ORDER}
        self.count = {k: 0 for k in NODES}
        self.count["copilot"] = 13
        self.cases: dict = {}
        self.jobs = deque()
        self.meta = {}
        self.sim = self.start_ts = self.end_ts = None
        self.speed, self.running = 3600, False
        self.backend = "rules"
        self.analyse_due = self.analysed = None
        self.passes, self.pass_s = 0, 0.0

    def start(self, scenario="demo", seed=7, speed=3600, backend="auto", threaded=True):
        with self.lock:
            self.gen += 1
            gen = self.gen
            self._reset_state()
            self.status, self.error = "preparing", None
            self.speed = speed if speed in SPEEDS else 3600
            self.meta = {"scenario": scenario, "seed": int(seed)}
        if not threaded:
            self._setup(gen, scenario, int(seed), backend)
            return
        threading.Thread(target=self._boot, args=(gen, scenario, int(seed), backend), daemon=True).start()

    def _boot(self, gen, scenario, seed, backend):
        try:
            self._setup(gen, scenario, seed, backend)
        except Exception as e:                           # the page shows it instead of hanging
            traceback.print_exc()
            with self.lock:
                if gen == self.gen:
                    self.status, self.error = "error", f"{type(e).__name__}: {e}"
            return
        with self.lock:
            if gen != self.gen:
                return
            self.status, self.running = "running", True
        threading.Thread(target=self._clock, args=(gen,), daemon=True).start()
        threading.Thread(target=self._worker, args=(gen,), daemon=True).start()

    def control(self, action=None, speed=None):
        with self.lock:
            if speed in SPEEDS:
                self.speed = speed
            if action == "pause" and self.status == "running":
                self.running, self.status = False, "paused"
            elif action == "resume" and self.status == "paused":
                self.running, self.status = True, "running"
            elif action == "stop":
                self.gen += 1
                self.running, self.status = False, "stopped"
        return self.state(summary=True)

    # ------------------------------------------------------------------------------------------
    # setup: the week, the trained models, the notes and work instructions of the week
    # ------------------------------------------------------------------------------------------
    def _setup(self, gen, scenario, seed, backend):
        t0 = time.time()
        week = build_week(seed, 7, demo=(scenario == "demo"))
        tabs = {}
        for sid in ORDER:
            d = week["tables"][sid].copy()
            d["ts"] = pd.to_datetime(d.ts)
            assert (d.event_id.values == np.arange(1, len(d) + 1)).all()
            tabs[sid] = d.reset_index(drop=True)
        sig = {sid: joblib.load(MODELS_DIR / f"{sid}.joblib") for sid in ORDER}
        cause_bundle = load_bundle()
        md = method_data.build(week["tables"], week["causes"])
        frames = {s: d[["ts", "event_type", "comment", "downtime_s", "work_instruction"]] for s, d in tabs.items()}
        notes, _ = generate_notes.build(week["causes"], frames, None)
        notes = sorted(notes, key=lambda n: n["written_ts"])
        try:
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as con:
                plan = pd.read_sql("SELECT * FROM maint_plan", con)
        except Exception:
            plan = pd.DataFrame()
        FL.load_env()
        cfg = FL.azure_settings()
        use_azure = backend == "azure" or (backend == "auto" and bool(cfg["key"]))
        start = min(d.ts.min() for d in tabs.values()).floor("h")
        with self.lock:
            if gen != self.gen:
                return
            self.week, self.tabs, self.sig, self.cause_bundle = week, tabs, sig, cause_bundle
            n = {s: len(d) for s, d in tabs.items()}
            self.a_score = {s: np.full(n[s], np.nan) for s in ORDER}
            self.a_flag = {s: np.zeros(n[s], dtype=np.int8) for s in ORDER}
            self.a_type = {s: np.full(n[s], None, dtype=object) for s in ORDER}
            self.a_reason = {s: np.full(n[s], None, dtype=object) for s in ORDER}
            self.h_flag = {s: np.zeros(n[s], dtype=np.int8) for s in ORDER}
            self.ptr = {s: 0 for s in ORDER}
            self.vins = {s: set() for s in ORDER}
            self.first_pos = {s: {} for s in ORDER}
            self.last_wi = {s: None for s in ORDER}
            self.flag_times = {s: deque() for s in ORDER}
            self.last_burst = {s: None for s in ORDER}
            self.flag_kinds = {s: {} for s in ORDER}
            self.md, self.notes, self.plan = md, notes, plan
            self.note_i = 0
            self.facts = []
            self.inc_ids, self.inc_seen, self.streak = {}, set(), {}
            self.inc_live = pd.DataFrame()
            self.start_ts, self.end_ts = start, start + pd.Timedelta(days=7)
            self.sim = start
            self.next_block = start + pd.Timedelta(hours=2)
            self.next_shift = start + pd.Timedelta(hours=8)
            self.kpi = {"cars": 0, "nok": 0, "flags": 0, "downtime_s": 0.0, "repairs": 0, "notes": 0, "facts": 0,
                        "wi_checked": 0, "wi_block": 0, "people_findings": 0, "stops": 0}
            self.hourly = deque(maxlen=400)      # (sim hour, cars) for the output chart
            self.cfg, self.cache = cfg, FL.Cache(True)
            saved = all(self.cache.get(FL.Cache.key(cfg, FL.note_input(n))) is not None for n in notes)
            self.backend = f"azure {cfg['model']}" if use_azure else f"saved {cfg['model']}" if saved else "rules"
            self.pool = ThreadPoolExecutor(max_workers=3) if use_azure else None
            self.schedule = self._method_schedule()
            self.meta.update({"start": ts(start), "end": ts(self.end_ts), "setup_s": round(time.time() - t0, 1),
                              "scenario_label": "Demo week - the story in the database" if scenario == "demo"
                              else f"New random week (seed {seed})",
                              "planted": len(week["causes"]),
                              "spec": {s: {"name": STATIONS[s]["primary"]["name"], "unit": STATIONS[s]["primary"]["unit"],
                                           "lsl": STATIONS[s]["primary"]["lsl"], "usl": STATIONS[s]["primary"]["usl"],
                                           "nominal": STATIONS[s]["primary"]["nominal"], "title": STATIONS[s]["title"],
                                           "severity": STATIONS[s]["severity"]} for s in ORDER}})
            base = [w for w in md["work_instructions"].itertuples() if pd.Timestamp(w.valid_from) < start]
            self._emit("mes", "info", f"Line live: {self.meta['scenario_label']}",
                       f"{sum(n.values()):,} station records to stream · {len(notes)} floor notes · "
                       f"{len(md['work_instructions'])} WI versions · floor listener: {self.backend}", [])
            for w in base:
                self._method_event(w, "baseline")

    def _method_schedule(self):
        out = []
        for w in self.md["work_instructions"].itertuples():
            vf = pd.Timestamp(w.valid_from)
            if vf < self.start_ts:
                continue
            out += [(vf - PRECHECK_H * H, "pre", w), (vf + AFTER_H * H, "after", w)]
        return sorted(out, key=lambda x: x[0])

    # ------------------------------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------------------------------
    def _emit(self, node, sev, title, detail, path, t=None, **extra):
        self.seq += 1
        e = {"seq": self.seq, "t": ts(t if t is not None else self.sim), "node": node, "sev": sev,
             "title": title, "detail": detail, "path": path, **extra}
        self.events.append(e)
        if sev not in ("info",):
            self.count["app"] += 1
        return e

    # ------------------------------------------------------------------------------------------
    # the clock: release data, score every cycle, schedule the heavier models
    # ------------------------------------------------------------------------------------------
    def _clock(self, gen):
        last = time.monotonic()
        while True:
            time.sleep(TICK_S)
            now = time.monotonic()
            dt, last = now - last, now
            with self.lock:
                if gen != self.gen:
                    return
                if not self.running:
                    continue
                target = min(self.end_ts, self.sim + pd.Timedelta(seconds=dt * self.speed))
                self._advance(target)
                if self.sim >= self.end_ts:
                    self.running, self.status = False, "finishing"
                    self.analyse_due = self.end_ts
                    self.jobs.append(("finish", None))
                    return

    def _advance(self, target):
        """Everything that happens between self.sim and target (lock held)."""
        for sid in ORDER:
            df = self.tabs[sid]
            lo = self.ptr[sid]
            hi = int(np.searchsorted(df.ts.values, np.datetime64(target), side="right"))
            if hi > lo:
                self._score(sid, lo, hi)
                self._stream(sid, lo, hi)
                self.ptr[sid] = hi
        self.sim = target
        while self.next_block <= target:
            if self.next_block >= self.start_ts + WARMUP_H * H:
                self.analyse_due = self.next_block
            elif self.next_block == self.start_ts + 2 * H:
                self._emit("cause", "info", "Cause finder is learning the normal",
                           "it compares every 2-hour block with the station's normal - the first shift sets that normal",
                           [["mes", "cause"]], t=self.next_block)
            self.next_block += 2 * H
        while self.next_shift <= target:
            self.jobs.append(("people", self.next_shift))
            self.next_shift += 8 * H
        while self.note_i < len(self.notes) and pd.Timestamp(self.notes[self.note_i]["written_ts"]) <= target:
            n = dict(self.notes[self.note_i])
            self.note_i += 1
            self.count["notes"] += 1
            self.jobs.append(("note", n))
        while self.schedule and self.schedule[0][0] <= target:
            t, kind, w = self.schedule.pop(0)
            if kind == "pre":
                self._method_event(w, "pre", t)
            else:
                self._method_after(w, t)
        hr = target.floor("h")
        if not self.hourly or self.hourly[-1][0] != hr:
            self.hourly.append([hr, 0])

    def _score(self, sid, lo, hi):
        """Signal checker on rows lo..hi, with the rows before as context (lock held)."""
        df, bundle = self.tabs[sid], self.sig[sid]
        c0 = max(0, lo - CONTEXT)
        w = df.iloc[c0:hi]
        up = STATIONS[sid]["upstream"]
        rules = rule_checks(w, self.vins[up] if up else None, up)
        cyc = w[w.event_type == "CYCLE"]
        new = cyc.index[cyc.index >= lo]
        ml_hit, F = pd.Series(False, index=cyc.index), None
        if len(new):
            st = bundle["stats"]
            cost = STATIONS[sid].get("retry_cost_s", 0.0)
            ct_net = cyc.cycle_time_s - cyc.retries.fillna(0) * cost
            explained = cyc.andon_pulled.fillna(0) == 1
            prim = cyc.primary_value.fillna(st["p_med"])
            roll = prim.rolling(ROLL, min_periods=10).median()          # trailing: the future is not known yet
            resid = cyc.secondary_value - (st["intercept"] + st["slope"] * prim)
            F = pd.DataFrame({"primary_z": (prim - st["p_med"]) / (st["p_mad"] + 1e-9),
                              "pattern_resid_z": resid / (st["r_mad"] + 1e-9),
                              "cycle_time_z": (ct_net - st["ct_med"]) / (st["ct_mad"] + 1e-9),
                              "drift_z": (roll - st["p_med"]) / (st["roll_mad"] + 1e-9), "_roll": roll}, index=cyc.index)
            Fn = F.loc[new]
            X = Fn[FEATURES].fillna(0).clip(-Z_CAP, Z_CAP)
            score = -bundle["model"].score_samples(X)
            guard = Fn[FEATURES].abs().max(axis=1) >= Z_GUARD
            c = cyc.loc[new]
            oos = ~(c.primary_value.between(c.primary_lsl, c.primary_usl)
                    & c.secondary_value.between(c.secondary_lsl, c.secondary_usl))
            confirmed_nok = (c.result == "NOK") & oos & c.fault_code.notna()
            hit = ((score >= bundle["threshold"]) | guard.values) & ~confirmed_nok.values
            ml_hit.loc[new] = hit
            self.a_score[sid][new.values] = np.round(score, 4)
        for p in range(lo, hi):
            r = df.iloc[p]
            reasons = list(rules[p])
            if r.event_type == "CYCLE" and isinstance(r.vin, str):
                f = self.first_pos[sid].get(r.vin)
                if f is not None and f < c0:
                    reasons.append(("traceability", f"VIN already recorded here at {df.ts.iloc[f]:%Y-%m-%d %H:%M:%S} (double scan?)"))
                self.first_pos[sid].setdefault(r.vin, p)
                self.vins[sid].add(r.vin)
            if not reasons and bool(ml_hit.get(p, False)):
                kind, text = explain(r, F.loc[p], self.sig[sid]["stats"])
                if not (kind == "unlogged_event" and r.andon_pulled == 1):
                    reasons.append((kind, text))
            if rules[p]:
                self.a_score[sid][p] = 1.0
            if reasons:
                self.a_flag[sid][p] = 1
                self.a_type[sid][p] = reasons[0][0]
                self.a_reason[sid][p] = "; ".join(t for _, t in reasons)

    def _stream(self, sid, lo, hi):
        """Points for the charts, KPIs and the events a single record can raise (lock held)."""
        df = self.tabs[sid]
        for p in range(lo, hi):
            r = df.iloc[p]
            t = r.ts
            if r.event_type == "CYCLE":
                flag = int(self.a_flag[sid][p])
                self.pseq += 1
                self.points[sid].append([self.pseq, int((t - self.start_ts).total_seconds()),
                                         None if pd.isna(r.primary_value) else float(r.primary_value),
                                         float(r.cycle_time_s), flag, int(r.result == "NOK")])
                self.count["mes"] += 1
                if sid == "st013":
                    self.kpi["cars"] += 1
                    if self.hourly and self.hourly[-1][0] == t.floor("h"):
                        self.hourly[-1][1] += 1
                    else:
                        self.hourly.append([t.floor("h"), 1])
                self.kpi["nok"] += int(r.result == "NOK")
                if self.last_wi[sid] is not None and r.work_instruction != self.last_wi[sid]:
                    self._wi_live(sid, r.work_instruction, t)
                self.last_wi[sid] = r.work_instruction
                if flag:
                    self._flag(sid, p, r, t)
            elif r.event_type == "MAINTENANCE":
                self.count["mlog"] += 1
                self._maintenance(sid, r, t)
            elif r.event_type == "FAULT":
                self.kpi["downtime_s"] += float(r.downtime_s or 0)

    def _flag(self, sid, p, r, t):
        self.kpi["flags"] += 1
        self.count["signal"] += 1
        kind, reason = self.a_type[sid][p], str(self.a_reason[sid][p])
        if reason.startswith("result OK but"):
            self._emit("signal", "warning", f"{sid.upper()}: car passed OK although out of spec",
                       f"{r.vin or 'no VIN'} - {reason.split(';')[0]}. It waits for a check.",
                       [["mes", "signal"], ["signal", "contain"], ["contain", "app"]], t=t, station=sid)
            return
        q = self.flag_times[sid]
        q.append(t)
        k = self.flag_kinds[sid]
        label = ("VIN / traceability" if kind == "traceability" else "sensor" if kind == "sensor" else
                 "slow cycles nothing explains" if kind == "unlogged_event" else "signal off its normal pattern")
        k[label] = k.get(label, 0) + 1
        while q and q[0] < t - H:
            q.popleft()
        lb = self.last_burst[sid]
        if len(q) >= BURST_N and (lb is None or t - lb > BURST_GAP_H * H):
            self.last_burst[sid] = t
            top = sorted(k.items(), key=lambda x: -x[1])
            self._emit("signal", "warning", f"{sid.upper()}: unusual cycles rising ({len(q)} in the last hour)",
                       "since the last alert: " + ", ".join(f"{n} {lbl}" for lbl, n in top[:3])
                       + f" - e.g. {reason.split(';')[0]}",
                       [["mes", "signal"], ["signal", "impact"]], t=t, station=sid)
            self.flag_kinds[sid] = {}

    def _maintenance(self, sid, r, t):
        c = str(r.comment or "")
        if c.startswith("Unplanned repair"):
            self.kpi["repairs"] += 1
            self.count["maint"] += 1
            fam = next((f for f, eq in STATIONS[sid]["equipment"].items() if eq[2] in c), None)
            eq = STATIONS[sid]["equipment"].get(fam, ("equipment",))[0]
            nxt = ""
            if len(self.plan) and fam:
                m = self.plan[(self.plan.station == sid) & (self.plan.family == fam)]
                if len(m):
                    m = m.iloc[0]
                    if pd.notna(m.interval_h):
                        nxt = f" Plan: {m.policy_rec} -> next check {ts(t + pd.Timedelta(hours=float(m.interval_h)))}."
                    else:
                        nxt = f" Plan: {m.policy_rec}."
            self._emit("maint", "warning", f"Unplanned repair at {sid.upper()}: {c.split(':', 1)[-1].strip()}",
                       f"{eq} down {float(r.downtime_s or 0) / 60:.0f} min.{nxt}",
                       [["mlog", "maint"], ["maint", "app"], ["mlog", "cause"]], t=t, station=sid)
        elif c.startswith("Planned maintenance") and sid == "st012":
            self._emit("maint", "info", "Planned maintenance window",
                       f"both stations, {float(r.downtime_s or 0) / 60:.0f} min - the checks the plan puts here are done now",
                       [["mlog", "maint"]], t=t)

    # ------------------------------------------------------------------------------------------
    # method checker + change manager
    # ------------------------------------------------------------------------------------------
    def _checks(self, w):
        steps = self.md["wi_steps"]
        wis = self.md["work_instructions"]
        s = MC.steps_of(steps, w.wi_version)
        older = wis[(wis.station == w.station) & (wis.version_no < w.version_no)].sort_values("version_no")
        prev = MC.steps_of(steps, older.wi_version.iloc[-1]) if len(older) else None
        checks = MC.check_design(w.station, s, prev) + MC.check_people(s, self.md["qualifications"])
        return s, prev, checks

    def _method_event(self, w, when, t=None):
        s, prev, checks = self._checks(w)
        v = MC.verdict(checks)
        self.kpi["wi_checked"] += 1
        self.count["method"] += 1
        self.count["wi"] += 1
        top = [c for c in checks if c["status"] in ("BLOCK", "WARN")]
        top.sort(key=lambda c: MC.ORDER[c["status"]])
        why = "; ".join(f"{c['rule']} {c['message']}" for c in top[:2]) or "all 15 rules pass"
        p = MC.planned(s)
        fit = f"planned cycle {p['cycle_s']:.1f} s of the {MC.TAKT_S:.0f} s takt"
        if when == "baseline":
            self._emit("method", "info" if v == "PASS" else "warning", f"{w.wi_version} in use - method check {v}",
                       f"{fit}. {why}", [["wi", "method"]], t=self.start_ts, station=w.station)
            return
        sev = {"BLOCK": "critical", "WARN": "warning", "PASS": "good"}[v]
        self._emit("method", sev, f"{w.wi_version} proposed ({w.change_note}) - method check {v}",
                   f"goes live {ts(w.valid_from)}. {fit}. {why}", [["wi", "method"], ["method", "change"]], t=t,
                   station=w.station)
        roles = CM.needed_roles(CM.diff(prev or [], s), checks) if prev else ["engineer"]
        if v == "BLOCK":
            self.kpi["wi_block"] += 1
            self.count["change"] += 1
            self._emit("change", "serious", f"Change manager: {w.wi_version} held at the gate",
                       "a BLOCK rule has to be fixed first - this version should not reach the line",
                       [["method", "change"], ["change", "app"]], t=t, station=w.station)
        else:
            self._emit("change", "info", f"Change manager: {w.wi_version} needs {', '.join(roles)} approval",
                       "then every operator in the rotation signs it before their first car",
                       [["method", "change"]], t=t, station=w.station)

    def _wi_live(self, sid, version, t):
        wis = self.md["work_instructions"]
        w = wis[wis.wi_version == version]
        if w.empty:
            return
        _, _, checks = self._checks(next(w.itertuples()))
        v = MC.verdict(checks)
        extra = (" The method checker said BLOCK - with the change manager in place this version would have "
                 "stopped at the gate. Watch what the data does." if v == "BLOCK" else "")
        self._emit("wi", "warning" if v == "BLOCK" else "info", f"{version} goes live on {sid.upper()}",
                   f"{w.change_note.iloc[0]}.{extra}", [["change", "wi"], ["wi", "mes"]], t=t, station=sid)

    def _method_after(self, w, t):
        df = self.tabs[w.station].iloc[:self.ptr[w.station]]
        cyc = df[df.event_type == "CYCLE"][["ts", "work_instruction", "operator_id", "operator_time_s", "retries",
                                             "cycle_time_s"]]
        sign = self.md["wi_signoffs"].copy()
        sign["signed_ts"] = pd.to_datetime(sign.signed_ts)
        s = MC.steps_of(self.md["wi_steps"], w.wi_version)
        checks = MC.check_history(w.station, w.wi_version, cyc, sign, MC.planned(s)["op_s"], None, False)
        bad = [c for c in checks if c["status"] == "WARN"]
        if not checks:
            return
        self.count["method"] += 1
        self._emit("method", "warning" if bad else "good",
                   f"{w.wi_version} after one shift: {'WARN' if bad else 'as planned'}",
                   " · ".join(f"{c['rule']} {c['message']}" for c in (bad or checks)[:2]),
                   [["mes", "method"], ["method", "app"]], t=t, station=w.station)

    # ------------------------------------------------------------------------------------------
    # the worker: cause finder passes, people model, floor notes
    # ------------------------------------------------------------------------------------------
    def _worker(self, gen):
        while True:
            with self.lock:
                if gen != self.gen:
                    return
                job = self.jobs.popleft() if self.jobs else None
                if job is None and self.analyse_due is not None and (self.analysed is None or self.analyse_due > self.analysed):
                    job = ("analyse", self.analyse_due)
            if job is None:
                time.sleep(0.05)
                continue
            try:
                self._run_job(job, gen)
            except Exception:
                traceback.print_exc()
            if job[0] == "finish":
                return

    def _run_job(self, job, gen=None):
        kind, arg = job
        if kind == "analyse":
            self._analyse(arg, gen)
        elif kind == "people":
            self._people(arg, gen)
        elif kind == "note":
            self._note(arg)
        elif kind == "finish":
            if self.analyse_due and (self.analysed is None or self.analysed < self.analyse_due):
                self._analyse(self.analyse_due, gen)
            if self.pool:
                self.pool.shutdown(wait=True)
                self.cache.save()
            with self.lock:
                if gen is not None and gen != self.gen:
                    return
                self.status = "finished"
                n = sum(c.status == "open" for c in self.cases.values())
                self._emit("app", "good", "Week complete",
                           f"{self.kpi['cars']:,} cars built · {n} problem cases decided · {self.kpi['flags']:,} unusual "
                           f"cycles flagged · {self.kpi['facts']} facts from {self.kpi['notes']} floor notes · "
                           f"{self.kpi['wi_checked']} method checks", [])

    def _snapshot(self, t_cut=None) -> dict:
        with self.lock:
            snap = {}
            for sid in ORDER:
                n = self.ptr[sid]
                d = self.tabs[sid].iloc[:n].copy()
                d["anomaly_score"], d["anomaly_flag"] = self.a_score[sid][:n].copy(), self.a_flag[sid][:n].copy()
                d["anomaly_type"], d["anomaly_reason"] = self.a_type[sid][:n].copy(), self.a_reason[sid][:n].copy()
                d["human_flag"] = self.h_flag[sid][:n].copy()
                snap[sid] = d
        if t_cut is not None:
            snap = {s: d[d.ts < t_cut] for s, d in snap.items()}
        return snap

    # ---- cause finder -> impact ranker + containment ---------------------------------------
    def _analyse(self, t_cut, gen=None):
        t0 = time.time()
        snap = self._snapshot(t_cut)
        try:
            inc, _ = find(snap, self.cause_bundle)
        except Exception:
            traceback.print_exc()
            with self.lock:
                self.analysed = t_cut
            return
        with self.lock:
            if gen is not None and gen != self.gen:
                return
            self.analysed = t_cut
            self.passes += 1
        if inc.empty:
            return
        inc["start_ts"], inc["end_ts"] = pd.to_datetime(inc.start_ts), pd.to_datetime(inc.end_ts)
        inc["start"], inc["end"] = inc.start_ts, inc.end_ts
        with self.lock:
            for r in inc.itertuples():
                k = (r.family, r.shift_date, r.shift)
                if k not in self.inc_ids:
                    self.inc_ids[k] = len(self.inc_ids) + 1
            inc["incident_id"] = [self.inc_ids[(r.family, r.shift_date, r.shift)] for r in inc.itertuples()]
            self.count["cause"] = len(self.inc_ids)
            self.inc_live = inc
            for r in inc[inc.cause == "unclear"].itertuples():
                if r.incident_id not in self.inc_seen:
                    self.inc_seen.add(r.incident_id)
                    self._emit("cause", "info", f"Symptom at {r.top_station.upper()}: {r.family} - cause not clear yet",
                               f"{r.symptom}. Best guess so far: {r.culprit.split('best guess: ')[-1].rstrip(')')} "
                               f"- the cause finder waits for more evidence", [["mes", "cause"]], t=t_cut)
        conf = inc[inc.cause != "unclear"]
        groups = {k: g for k, g in conf.groupby(["cause", "culprit", "family"])}
        with self.lock:
            for k in list(self.streak):
                if k not in groups:
                    self.streak[k] = 0
            for k in groups:
                self.streak[k] = self.streak.get(k, 0) + 1
        todo = []
        for k, g in groups.items():
            known = self.cases.get(k)
            if known is not None:
                known.missing = 0
                if known.status == "revised":
                    known.status = "open"
                todo.append((known, g, False))
            elif ((self.streak[k] >= CONFIRM_PASSES and g.confidence.max() >= MIN_CONF)
                  or g.confidence.max() >= CONFIRM_FAST) and self._plausible(k, g):
                todo.append((None, g, True))
        for c in self.cases.values():
            recent = c.data and pd.Timestamp(c.data["last"]) >= t_cut - 12 * H
            if c.key not in groups and c.status == "open" and recent:
                c.missing += 1
                if c.missing == 3:
                    c.status = "revised"
                    with self.lock:
                        self._emit("cause", "info", f"Cause finder revised: {c.culprit}",
                                   "with more data this is no longer the best explanation of the symptoms",
                                   [["cause", "app"]], t=t_cut)
        if todo:
            ctx = self._context(snap, inc, t_cut)
            for case, g, new in todo:
                last = g.end_ts.max()
                if not new and last < t_cut - 6 * H and case.data:
                    continue                                   # quiet case: nothing new to re-assess
                try:
                    d = self._assess(g, ctx, t_cut)
                except Exception:
                    traceback.print_exc()
                    continue
                with self.lock:
                    if gen is not None and gen != self.gen:
                        return
                    if new:
                        case = Case(len(self.cases) + 1, (g.cause.iloc[0], g.culprit.iloc[0], g.family.iloc[0]))
                        self.cases[case.key] = case
                        case.data = {**d, "confirmed": ts(t_cut), "first_action": d["action"]}
                        self._announce(case, g, t_cut)
                    else:
                        self._update(case, d, t_cut)
        with self.lock:
            self.pass_s = round(time.time() - t0, 2)
            self.count["impact"] = sum(1 for c in self.cases.values() if c.data)
            self.count["contain"] = sum(c.data.get("check", 0) + c.data.get("hold", 0) + c.data.get("rework", 0)
                                        for c in self.cases.values() if c.status == "open")

    def _plausible(self, key, g) -> bool:
        """Engineering sense check before acting: the WI system knows how long a version has been in use."""
        cause, culprit, _ = key
        if cause == "method" and g.confidence.max() < CONFIRM_FAST:
            w = self.md["work_instructions"]
            w = w[w.wi_version == culprit]
            if len(w) and pd.Timestamp(w.valid_from.iloc[0]) < self.start_ts - pd.Timedelta(days=OLD_WI_DAYS):
                return False
        return True

    def _context(self, snap, inc, t_cut):
        """Shared inputs of one pass: containment frames, the impact ranker's line model."""
        marked = set()
        for bl in inc.block_list:
            for b in bl.split("; "):
                s, t = b.split("@")
                marked.add((s, pd.Timestamp(t)))
        tabs = {}
        for sid, d in snap.items():
            d = d.copy()
            key = list(zip([sid] * len(d), d.ts.dt.floor("2h")))
            d["incident_id"] = [1.0 if k in marked else np.nan for k in key]
            tabs[sid] = d
        line = IR.Line(tabs)
        wi_changes, batch_last = {}, {}
        for s, c in line.cycles.items():
            cs = c.sort_values("ts")
            ch = cs.work_instruction != cs.work_instruction.shift()
            wi_changes[s] = list(zip(cs.ts[ch], cs.work_instruction[ch]))
            batch_last[s] = cs.groupby("part_batch").ts.max().to_dict()
        cyc, ev = CT.prepare({s: d for s, d in snap.items()})
        return {"line": line, "wi": wi_changes, "batch": batch_last, "cyc": cyc, "ev": ev, "snap": snap}

    def _assess(self, g, ctx, t_cut) -> dict:
        """Impact ranker + containment for one case at time t_cut."""
        g = g.assign(case_id=1)
        it = IR.case_items(ctx["line"], g, ctx["wi"], ctx["batch"])[0]
        now, sub = IR.score(it["m"], it["sev"])
        nxt_m = IR.next_week(it, ctx["line"])
        nxt, _ = IR.score(nxt_m, it["sev"])
        cat, rule = IR.categorise(max(now, nxt), it["m"] if now >= nxt else nxt_m, it["sev"])
        if rule and nxt > now + 0.5:
            rule = "next week if nothing changes: " + rule
        c = next(CT.case_table(g).itertuples())
        sid, cyc, ev = c.station, ctx["cyc"], ctx["ev"]
        sev = STATIONS[sid]["severity"]
        cars, w0, w1, basis = CT.scope(c, cyc, ev)
        cars = cars.copy()
        if c.family in CT.PRODUCT_RISK and len(cars):
            s = [CT.sort_car(r, c, sev) for r in cars.itertuples()]
            cars["disposition"], cars["reason"] = [x[0] for x in s], [x[1] for x in s]
        else:
            cars["disposition"], cars["reason"] = "RELEASE", "no product risk"
        fix = None
        if c.cause == "machine":
            fix = CT.equipment_repair(ev[sid], sid, c.family, c.start)
        elif c.cause == "method":
            later = [t for t, v in ctx["wi"].get(sid, []) if t > c.start and v != c.culprit]
            fix = later[0] if later else None
        elif c.cause == "station" and "batch" in c.culprit:
            last = ctx["batch"].get(sid, {}).get(c.culprit.split()[-1])
            fix = last if last is not None and last < t_cut - H else None
        elif c.cause == "station" and c.end < t_cut - 4 * H:
            fix = c.end                                # the outage / supply gap is over
        action, why = CT.advise(c, cars, t_cut, fix, sid, cyc)
        n = cars.disposition.value_counts()
        hold = int(n.get("HOLD", 0))
        prevent = "-"
        try:
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as con:
                prevent = CT.prevention(con, c, (w1 - w0).total_seconds() / 3600)
        except Exception:
            pass
        return {"station": sid, "stations": c.stations, "incidents": len(g), "first": ts(c.start), "last": ts(c.end),
                "confidence": round(float(g.confidence.max()), 2), "symptom": g.sort_values("start_ts").symptom.iloc[-1],
                "evidence": g.evidence.iloc[0], "category": cat, "priority": round(max(now, nxt), 1),
                "score_now": now, "rule": rule, "risk_cars": round(it["m"]["risk_cars"], 1),
                "lost_cars": round(it["m"]["lost_cars"], 1), "owner_action": it["action"], "impact_status": it["status"],
                "action": action, "action_label": CT.ACTION_LABEL.get(action, action),
                "action_sev": CT.ACTION_SEV.get(action, "info"), "why": why, "basis": basis,
                "window": f"{ts(w0)} - {ts(w1)}", "cars_in_scope": int(len(cars)), "rework": int(n.get("REWORK", 0)),
                "check": int(n.get("CHECK", 0)), "hold": hold, "release": int(n.get("RELEASE", 0)),
                "prevent": prevent, "fixed": fix is not None and fix <= t_cut}

    def _announce(self, case, g, t_cut):
        d = case.data
        self.count["cause"] = max(self.count["cause"], len(self.inc_ids))
        path = [["mes", "cause"]] + ([["people", "cause"]] if case.cause == "people" else [])
        self._emit("cause", "warning", f"Cause found: {case.culprit}",
                   f"{case.cause} · {case.family} at {d['station'].upper()} ({d['confidence']:.0%} sure, "
                   f"{d['incidents']} incident{'s' if d['incidents'] > 1 else ''}) - {d['symptom']}",
                   path, t=t_cut, case=case.id)
        risk = f"{d['risk_cars']:g} cars at risk · {d['lost_cars']:g} cars lost"
        self._emit("impact", LEVEL_SEV[d["category"]], f"Priority {d['category']} ({d['priority']:.0f}): {case.culprit}",
                   (d["rule"] + " · " if d["rule"] else "") + risk + f" · owner: {d['owner_action']}",
                   [["cause", "impact"], ["impact", "app"]], t=t_cut, case=case.id)
        if d["action"] == "STOP":
            self.kpi["stops"] += 1
        counts = (f"{d['check']:,} cars to check, {d['hold']:,} on hold, {d['rework']:,} rework"
                  if d["action"] != "NO HOLD" else "no car has to wait")
        cpath = [["cause", "contain"], ["signal", "contain"], ["contain", "app"]]
        if d["prevent"] != "-":
            cpath.append(["maint", "contain"])
        self._emit("contain", d["action_sev"], f"{d['action_label']}: {case.culprit}",
                   f"{d['why']} · {counts}", cpath, t=t_cut, case=case.id, station=d["station"])
        # did the floor say it first?
        if d.get("fixed"):
            d["fixed_seen"] = True
        first = pd.Timestamp(d["first"])
        for f in self.facts:
            if (FL.subj_match(f.get("subject"), case.culprit) and first - 24 * H <= f["time"] < t_cut - H
                    and f["status"] != "info"):
                lead = (t_cut - f["time"]).total_seconds() / 3600
                before = (first - f["time"]).total_seconds() / 3600
                when = (f"{before:.1f} h before the first symptom in the data" if before > 0 else
                        f"{-before:.1f} h after the first symptom")
                self._emit("floor", "good", f"The floor named it {lead:.0f} h before the data confirmed it",
                           f"{f['author']} wrote \"{f['quote'][:90]}\" at {ts(f['time'])[5:]} - {when}",
                           [["notes", "floor"], ["floor", "cause"]], t=t_cut, case=case.id)
                d["early_h"] = round(lead, 1)
                break

    def _update(self, case, d, t_cut):
        old = case.data
        up_action = (SEV_RANK.get(d["action_sev"], 0) > SEV_RANK.get(old.get("action_sev"), 0)
                     and d["action"] != "HOLD + CHECK")
        up_cat = IR.LEVELS.index(d["category"]) < IR.LEVELS.index(old.get("category", "Low"))
        keep = {k: old[k] for k in ("confirmed", "first_action", "early_h", "fixed_seen") if k in old}
        case.data = {**d, **keep}
        if up_action:
            if d["action"] == "STOP":
                self.kpi["stops"] += 1
            self._emit("contain", d["action_sev"], f"Escalated - {d['action_label']}: {case.culprit}",
                       f"{d['why']} · {d['check']:,} cars to check, {d['hold']:,} on hold",
                       [["cause", "contain"], ["contain", "app"]], t=t_cut, case=case.id, station=d["station"])
        elif up_cat:
            self._emit("impact", LEVEL_SEV[d["category"]], f"Priority up to {d['category']}: {case.culprit}",
                       d["rule"] or f"score {d['priority']:.0f}", [["impact", "app"]], t=t_cut, case=case.id)
        if d["fixed"] and not old.get("fixed_seen"):
            case.data["fixed_seen"] = True
            self._emit("contain", "good", f"Fixed: {case.culprit}",
                       f"the cause is gone - check and release the {d['check'] + d['hold']:,} cars in scope",
                       [["contain", "app"]], t=t_cut, case=case.id)

    # ---- people model at shift end -------------------------------------------------------------
    def _people(self, t_end, gen=None):
        snap = self._snapshot(t_end)
        frames = [d[d.event_type == "CYCLE"].assign(station=s) for s, d in snap.items() if len(d)]
        if not frames or min(len(f) for f in frames) < 200:
            return
        try:
            cyc, shifts, _ = run_people(frames, 0.005)
        except Exception:
            traceback.print_exc()
            return
        date, sh = shift_of(t_end - pd.Timedelta(minutes=1))
        rows = shifts[(shifts.shift_date == date) & (shifts["shift"] == sh) & shifts.finding_type.notna()]
        with self.lock:
            if gen is not None and gen != self.gen:
                return
            for sid in ORDER:
                ids = cyc[(cyc.station == sid) & (cyc.human_flag == 1)].event_id.astype(int).values - 1
                ids = ids[ids < len(self.h_flag[sid])]
                self.h_flag[sid][:] = 0
                self.h_flag[sid][ids] = 1
            if rows.empty:
                return
            self.kpi["people_findings"] += len(rows)
            self.count["people"] += len(rows)
            main = rows[rows.finding_type.isin(PEOPLE_SEV)]
            sev = "warning" if len(main) else "info"
            parts = [f"{r.operator_id} {r.finding_type}" for r in (main if len(main) else rows).itertuples()][:4]
            support = int((rows.finding_type == "support").sum())
            detail = ", ".join(parts)
            if len(main):
                detail += " - " + str(main.finding.iloc[0])
            if support and len(main):
                detail += f" · {support} support gap(s): the team lead's, not the operator's"
            elif support:
                detail = f"{support} andon call(s) with no team lead within 3 min - a support gap, not the operator"
            self._emit("people", sev, f"People model · shift {sh} {date[8:10]}.{date[5:7]}: {len(rows)} finding(s)",
                       detail, [["mes", "people"], ["people", "impact"], ["people", "cause"]], t=t_end)

    # ---- floor listener ------------------------------------------------------------------------
    def _note(self, n):
        text = FL.note_input(n)
        k = FL.Cache.key(self.cfg, text)
        hit = self.cache.get(k)
        if hit is not None:                                # a GPT-5 answer saved from an earlier run
            self._note_done(n, "GPT-5, saved", hit)
            return
        if self.pool is None:
            self._note_done(n, "rules", FL.rule_parse(n))
            return

        def call():
            try:
                out = FL.call_azure(text, self.cfg)
                self.cache.put(k, out)
                return "GPT-5", out
            except Exception as e:                       # the rules take over for this note
                print("floor listener:", e)
                return "rules (fallback)", FL.rule_parse(n)
        gen = self.gen
        fut = self.pool.submit(call)
        fut.add_done_callback(lambda f: gen == self.gen and self._note_done(n, *f.result()))

    def _note_done(self, n, backend, parsed):
        p = FL.clean(parsed)
        with self.lock:
            inc = self.inc_live
            if len(inc):
                inc = inc[["incident_id", "stations", "top_station", "start", "end", "cause", "family", "culprit"]]
            links, facts = [], []
            for f in p["facts"]:
                iid, lk, _ = FL.link(f, n, inc if len(inc) else pd.DataFrame())
                links.append(lk)
                t = pd.Timestamp(FL.note_time(f, n))
                facts.append({**f, "time": t, "author": n["author"], "link": lk, "incident": iid})
            self.facts += facts
            self.kpi["notes"] += 1
            self.kpi["facts"] += len(facts)
            self.count["floor"] += len(facts)
            if not facts:
                self._emit("floor", "info", f"{n['author']}: nothing to act on",
                           n["text"].replace("\n", " ")[:120], [["notes", "floor"]], t=n["written_ts"])
                return
            sev = ("serious" if any(f["category"] == "safety" for f in facts) else
                   "warning" if any(f["status"] == "open" and f["category"] in ("machine", "method", "material", "people")
                                    for f in facts) else "info")
            agree = [f for f in facts if f["link"] == "agrees"]
            first = facts[0]
            tail = (f" · matches incident #{agree[0]['incident']} of the cause finder" if agree else
                    " · not in the data yet - watching" if first["status"] in ("open", "monitoring") else "")
            subj = f"{first['category']}" + (f" / {first['subject']}" if first.get("subject") else "")
            self._emit("floor", sev, f"{n['author']} note: {len(facts)} fact{'s' if len(facts) > 1 else ''} ({backend})",
                       f"{subj} {first['status']}: \"{first.get('quote', '')[:90]}\"{tail}",
                       [["notes", "floor"]] + ([["floor", "cause"]] if agree else []), t=n["written_ts"],
                       text=n["text"])

    # ------------------------------------------------------------------------------------------
    # what the page polls
    # ------------------------------------------------------------------------------------------
    def state(self, ev_after=0, pt_after=0, summary=False) -> dict:
        with self.lock:
            out = {"status": self.status, "error": self.error, "speed": self.speed, "speeds": SPEEDS,
                   "meta": self.meta, "backend": self.backend, "seq": self.seq}
            if self.sim is None:
                return out
            span = (self.end_ts - self.start_ts).total_seconds()
            el = (self.sim - self.start_ts).total_seconds()
            hours = el / 3600
            plan = IR.TARGET_PER_WEEK * el / span
            last_h = [c for h, c in list(self.hourly)[-2:-1]] or [0]
            out.update({"sim": self.sim.strftime("%Y-%m-%d %H:%M:%S"), "progress": round(el / span, 4),
                        "shift": shift_of(self.sim)[1], "passes": self.passes, "pass_s": self.pass_s,
                        "analysed": ts(self.analysed) if self.analysed is not None else None,
                        "kpi": {**self.kpi, "plan": round(plan), "hours": round(hours, 1),
                                "rate_h": last_h[0], "target_h": round(IR.TARGET_PER_WEEK / 168, 1),
                                "open_cases": sum(c.status == "open" for c in self.cases.values()),
                                "check": sum(c.data.get("check", 0) for c in self.cases.values() if c.status == "open"),
                                "hold": sum(c.data.get("hold", 0) for c in self.cases.values() if c.status == "open"),
                                "flags_h": sum(len(q) for q in self.flag_times.values()),
                                "station_flags_h": {s: len(q) for s, q in self.flag_times.items()}},
                        "nodes": {k: {"label": v[0], "unit": v[1], "n": self.count[k]} for k, v in NODES.items()}})
            if summary:
                return out
            evs = [e for e in self.events if e["seq"] > ev_after]
            out["events"] = evs[-150:]
            out["points"] = {s: [p for p in self.points[s] if p[0] > pt_after][-1500:] for s in ORDER}
            out["hourly"] = [[int((h - self.start_ts).total_seconds() // 3600), c] for h, c in self.hourly]
            out["cases"] = sorted(
                [{"id": c.id, "cause": c.cause, "culprit": c.culprit, "family": c.family, "status": c.status, **c.data}
                 for c in self.cases.values() if c.data],
                key=lambda r: (r["status"] != "open", IR.LEVELS.index(r["category"]), -r["priority"]))
            return out

    # ------------------------------------------------------------------------------------------
    # offline: the whole week as fast as the models run (tests, evaluation)
    # ------------------------------------------------------------------------------------------
    def run_fast(self, scenario="demo", seed=7, step_min=10, backend="rules"):
        self.start(scenario, seed, 3600, backend, threaded=False)
        self.status, self.running = "running", True
        t = self.start_ts
        while t < self.end_ts:
            t = min(self.end_ts, t + pd.Timedelta(minutes=step_min))
            with self.lock:
                self._advance(t)
            while self.jobs:
                self._run_job(self.jobs.popleft())
            if self.analyse_due is not None and (self.analysed is None or self.analyse_due > self.analysed):
                self._run_job(("analyse", self.analyse_due))
        self.analyse_due = self.end_ts
        self._run_job(("finish", None))
        return self


ENGINE = LiveEngine()


def evaluate(eng: LiveEngine):
    """Which planted problems did the live pipeline confirm, and how many hours after they started?"""
    causes = eng.week["causes"]
    rows = []
    for sc in causes.itertuples():
        hit = [c for c in eng.cases.values() if c.cause == sc.cause and
               (sc.culprit.lower() in c.culprit.lower() or c.culprit.lower() in sc.culprit.lower())]
        if hit:
            c = min(hit, key=lambda c: c.data["confirmed"])
            lag = (pd.Timestamp(c.data["confirmed"]) - pd.Timestamp(sc.start)).total_seconds() / 3600
            rows.append((sc.scenario_id, sc.cause, sc.family, sc.culprit, c.id, c.data["confirmed"], round(lag, 1),
                         c.data["first_action"]))
        else:
            rows.append((sc.scenario_id, sc.cause, sc.family, sc.culprit, None, None, None, None))
    df = pd.DataFrame(rows, columns=["scenario", "cause", "family", "culprit", "case", "confirmed", "hours_after_start",
                                     "first_action"])
    planted = {(sc.cause, sc.culprit.lower()) for sc in causes.itertuples()}
    extra = [c for c in eng.cases.values() if not any(c.cause == a and (b in c.culprit.lower() or c.culprit.lower() in b)
                                                      for a, b in planted)]
    return df, extra


def main():
    ap = argparse.ArgumentParser(description="Live line - a simulated week streamed through all the models")
    ap.add_argument("--fast", action="store_true", help="run the whole week now and print the event log")
    ap.add_argument("--random", action="store_true", help="a new random week instead of the demo story")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--backend", choices=["rules", "auto", "azure"], default="rules")
    ap.add_argument("--quiet", action="store_true", help="only the evaluation, not every event")
    args = ap.parse_args()
    t0 = time.time()
    eng = LiveEngine().run_fast("random" if args.random else "demo", args.seed, backend=args.backend)
    if not args.quiet:
        for e in eng.events:
            if e["sev"] != "info" or e["node"] in ("cause", "people"):
                print(f"{e['t'][5:]}  {e['sev']:<8} {e['node']:<8} {e['title']}  |  {e['detail'][:150]}")
    k = eng.kpi
    print(f"\n{eng.meta['scenario_label']}: {len(eng.events)} events in {time.time() - t0:.0f}s "
          f"({eng.passes} cause-finder passes, last took {eng.pass_s}s)")
    print(f"cars {k['cars']:,} · flags {k['flags']:,} · notes {k['notes']} -> {k['facts']} facts · "
          f"WI checks {k['wi_checked']} ({k['wi_block']} BLOCK) · repairs {k['repairs']} · people findings {k['people_findings']}")
    df, extra = evaluate(eng)
    print("\nplanted problems -> live cases:")
    print(df.to_string(index=False))
    found = df.case.notna()
    print(f"\nconfirmed {found.sum()} of {len(df)} planted problems; median {df.hours_after_start[found].median():.1f} h "
          f"after they started; {len(extra)} other case(s): " + ", ".join(f"{c.cause}/{c.culprit}" for c in extra))


if __name__ == "__main__":
    main()
