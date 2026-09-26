"""
Maintenance predictor (model 8) - how often should each machine be checked?

Every piece of equipment on ST012 / ST013 can fail in the four ways the simulator knows
(quality / pattern / speed / data). Each failure mode gets one of three policies:

  wear    the failure shows itself (re-hits, re-tests, slow cycles) and gets likelier with age
          -> replace / service every T hours, T chosen to minimise cost per hour
             cost(T) = (planned cost x R(T) + failure cost x F(T)) / expected run time up to T
  silent  a drift you cannot see until someone checks (tool calibration, leak tester) - every car
          built between the drift and the next check is suspect (see containment)
          -> check every I hours:  I* = sqrt(2 x check cost / (drift rate x cars per hour x re-check cost per car))
             and at most one shift on a safety-critical joint (severity 9)
  random  no ageing (Weibull shape ~1): a fixed interval does not help
          -> no interval; keep a spare, let the signal checker watch it

The failure behaviour (Weibull shape and scale) is fitted from the maintenance history with
right-censoring (repairs that were not failures, and machines still running). The history pools the
12 identical units of each equipment type in the hall (fleet data): 2 simulated years under today's
practice, plus the repairs of the demo week on our own unit.

Tables: maint_history (one row per run between two renewals), maint_plan (one row per failure mode).

    python maintenance.py
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from method_checker import TAKT_S
from station_db import STATIONS, connect, station_tables

SEED = 21
HISTORY_H = 2 * 365 * 24            # two years before the demo week
FLEET = 12                          # identical units of each equipment type in the hall share one history
PM_HOUR, PM_MINUTE = 5, 45          # the daily planned-maintenance window
CARS_PER_H = 3600 / TAKT_S
PRACTICAL = [8, 16, 24, 48, 72, 168, 336, 504, 720, 1008, 1440, 2160]
LABEL = {8: "every shift", 16: "every 2 shifts", 24: "daily", 48: "every 2 days", 72: "every 3 days",
         168: "weekly", 336: "every 2 weeks", 504: "every 3 weeks", 720: "monthly", 1008: "every 6 weeks",
         1440: "every 2 months", 2160: "every 3 months"}

# kind, true Weibull (shape, scale h) used only to simulate the history, today's practice, costs in labour minutes:
#   planned = a planned check / replacement inside the maintenance window
#   failure = unplanned repair incl. the line stop and rework of the cars it touched
#   per_car = re-checking one suspect car (silent modes)
MODES = {
    ("st012", "quality"): dict(kind="wear", action="replace the socket", shape=2.8, scale=1100, now=None, planned=15, failure=900),
    ("st012", "pattern"): dict(kind="silent", action="torque calibration check", shape=1.8, scale=900, now=24, planned=10, failure=120, per_car=3),
    ("st012", "speed"):   dict(kind="wear", action="service the clamp", shape=1.6, scale=2600, now=None, planned=30, failure=600),
    ("st012", "data"):    dict(kind="random", action="-", shape=1.0, scale=3500, now=None, planned=10, failure=200),
    ("st013", "quality"): dict(kind="wear", action="replace the fill-head seal", shape=3.2, scale=650, now=None, planned=15, failure=800),
    ("st013", "pattern"): dict(kind="silent", action="leak tester calibration check", shape=2.2, scale=1200, now=24, planned=10, failure=120, per_car=2),
    ("st013", "speed"):   dict(kind="wear", action="service the fill pump", shape=1.8, scale=3000, now=None, planned=45, failure=700),
    ("st013", "data"):    dict(kind="random", action="-", shape=1.0, scale=3500, now=None, planned=10, failure=200),
}


def R(t, k, lam):
    return np.exp(-(np.asarray(t, float) / lam) ** k)


def fit_weibull(age, failed, entry=None) -> tuple[float, float]:
    """Maximum-likelihood Weibull with right-censoring (still running / not a failure) and left-truncation
    (a unit already `entry` hours old when the history starts). -> (shape, scale)"""
    age, failed = np.asarray(age, float), np.asarray(failed, bool)
    entry = np.zeros_like(age) if entry is None else np.asarray(entry, float)

    def nll(p):
        k, lam = np.exp(p)
        z = age / lam
        return -(np.sum(failed * (np.log(k / lam) + (k - 1) * np.log(z))) - np.sum(z ** k) + np.sum((entry / lam) ** k))
    start = np.log([1.5, np.mean(age) * 1.2])
    res = minimize(nll, start, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
    return tuple(np.exp(res.x))


def mean_life_upto(T, k, lam, n=400) -> float:
    if not np.isfinite(T):
        return lam * math.gamma(1 + 1 / k)
    t = np.linspace(0, T, n)
    integrate = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(integrate(R(t, k, lam), t))


# ---------------------------------------------------------------------------------------------
# History: two simulated years under today's practice, then the demo week's real repairs
# ---------------------------------------------------------------------------------------------
def simulate_history(week_start: datetime, rng) -> list[dict]:
    rows = []
    t0 = week_start - timedelta(hours=HISTORY_H)
    for (sid, fam), m in MODES.items():
      eq = STATIONS[sid]["equipment"][fam][0]
      for unit in range(FLEET):              # unit 0 is the one on our station
        t = -m["scale"] * rng.uniform(0, 1)    # units were installed at different times
        while t < HISTORY_H:
            life = m["scale"] * rng.weibull(m["shape"])
            if m["kind"] == "silent":                 # drift found at the next check (every `now` hours)
                found = math.ceil((t + life) / m["now"]) * m["now"]
                end, event, hidden = t + life, "failure", found - (t + life)
                nxt = found
            else:
                end, event, hidden, nxt = t + life, "failure", 0.0, t + life
            if end > HISTORY_H:                        # still running when the history ends
                end, event, hidden, nxt = HISTORY_H, "running", 0.0, HISTORY_H
            if end > 0:                                # only what happened inside the history window
                start = max(t, 0.0)
                rows.append({"station": sid, "family": fam, "equipment": eq, "unit": unit,
                             "start": t0 + timedelta(hours=start), "end": t0 + timedelta(hours=end),
                             "age_h": end - t, "entry_h": start - t, "event": event, "hidden_h": hidden,
                             "source": "simulated history"})
            t = nxt
    return rows


def add_demo_week(hist: pd.DataFrame, con, now_ts) -> pd.DataFrame:
    """Close the last 'running' row of each mode with what happened in the demo week."""
    try:
        inc = pd.read_sql("SELECT culprit, family, top_station, start_ts FROM incidents WHERE cause = 'machine'", con,
                          parse_dates=["start_ts"])
    except Exception:
        inc = pd.DataFrame(columns=["culprit", "family", "top_station", "start_ts"])
    extra = []
    for sid in station_tables(con):
        ev = pd.read_sql(f"SELECT ts, comment FROM {sid} WHERE comment LIKE 'Unplanned repair:%'", con, parse_dates=["ts"])
        for r in ev.itertuples():
            txt = r.comment.replace("Unplanned repair: ", "")
            fam = next(f for f, e in STATIONS[sid]["equipment"].items() if e[2] == txt)
            i = hist[(hist.station == sid) & (hist.family == fam) & (hist.unit == 0)].index[-1]
            onset = inc[(inc.top_station == sid) & (inc.family == fam) & (inc.start_ts <= r.ts)].start_ts
            fail_at = onset.min() if len(onset) else None
            end = fail_at if fail_at is not None and MODES[(sid, fam)]["kind"] == "silent" else r.ts
            hist.loc[i, ["end", "event", "hidden_h", "source"]] = [end, "failure" if fail_at is not None else "preventive",
                                                                  (r.ts - end).total_seconds() / 3600, "demo week"]
            hist.loc[i, "age_h"] = hist.loc[i, "entry_h"] + (end - hist.loc[i, "start"]).total_seconds() / 3600
            extra.append({"station": sid, "family": fam, "equipment": hist.loc[i, "equipment"], "unit": 0,
                          "start": r.ts, "end": now_ts, "age_h": (now_ts - r.ts).total_seconds() / 3600,
                          "entry_h": 0.0, "event": "running", "hidden_h": 0.0, "source": "demo week"})
    # units still running at the end of the history keep running through the demo week
    run = hist.event == "running"
    hist.loc[run, "age_h"] += (now_ts - pd.to_datetime(hist.loc[run, "end"])).dt.total_seconds() / 3600
    hist.loc[run, "end"] = now_ts
    return pd.concat([hist, pd.DataFrame(extra, columns=hist.columns)], ignore_index=True)


# ---------------------------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------------------------
def due(t, now_ts) -> str:
    """A job that is already overdue goes into the next planned-maintenance window."""
    if t > now_ts:
        return t.strftime("%Y-%m-%d %H:%M")
    pm = now_ts.normalize() + timedelta(hours=PM_HOUR, minutes=PM_MINUTE)
    pm = pm if pm > now_ts else pm + timedelta(days=1)
    return f"overdue - next maintenance window {pm:%Y-%m-%d %H:%M}"


def plan(sid, fam, m, k, lam, now_ts, last_renewal) -> dict:
    sev = STATIONS[sid]["severity"]
    mttf = lam * math.gamma(1 + 1 / k)
    age_now = (now_ts - last_renewal).total_seconds() / 3600
    p7 = 1 - R(age_now + 168, k, lam) / max(R(age_now, k, lam), 1e-12)
    out = {"kind": m["kind"], "shape": round(k, 2), "scale_h": round(lam), "mttf_h": round(mttf),
           "true_shape": m["shape"], "true_scale_h": m["scale"], "age_now_h": round(age_now, 1),
           "p_fail_7d": round(float(p7), 3), "severity": sev}
    if m["kind"] == "random" or k <= 1.2:
        out.update(policy_now="run to failure", policy_rec="no fixed interval - keep a spare, watch the signal checker",
                   interval_h=None, why=f"shape {k:.1f}: failures are random, a fixed interval would not prevent them",
                   cost_now_per_year=round(8760 / mttf * m["failure"]), cost_rec_per_year=round(8760 / mttf * m["failure"]),
                   next_due=None)
        return out
    if m["kind"] == "wear":
        def rate(T):
            if not np.isfinite(T):
                return m["failure"] / mttf
            return (m["planned"] * R(T, k, lam) + m["failure"] * (1 - R(T, k, lam))) / mean_life_upto(T, k, lam)
        best = min(PRACTICAL + [math.inf], key=rate)
        rec = "run to failure" if not np.isfinite(best) else f"{m['action']} {LABEL[best]}"
        out.update(policy_now="run to failure", policy_rec=rec, interval_h=None if not np.isfinite(best) else best,
                   why=(f"wears out (shape {k:.1f}); a planned job costs {m['planned']} min, "
                        f"a failure {m['failure']} min; best cost per hour at {LABEL.get(best, 'no interval')}; "
                        f"{1 - R(best, k, lam):.0%} still fail before it" if np.isfinite(best) else
                        f"a planned replacement never pays off (shape {k:.1f})"),
                   cost_now_per_year=round(8760 * rate(math.inf)), cost_rec_per_year=round(8760 * rate(best)),
                   next_due=None if not np.isfinite(best) else due(last_renewal + timedelta(hours=best), now_ts))
        return out
    # silent drift: choose the check interval
    lam_rate = 1 / mttf
    i_opt = math.sqrt(2 * m["planned"] / (lam_rate * CARS_PER_H * m["per_car"]))
    cap = 8 if sev >= 9 else 24
    best = max([p for p in PRACTICAL if p <= min(i_opt, cap)] or [8])

    def year_cost(I):
        return 8760 / I * m["planned"] + 8760 / mttf * (m["failure"] + I / 2 * CARS_PER_H * m["per_car"])
    reason = (f"drift about every {mttf / 24:.0f} days; each check costs {m['planned']} min, each hour a drift goes "
              f"unseen puts {CARS_PER_H:.0f} cars on the re-check list -> best around {i_opt:.0f} h")
    if i_opt > cap:
        reason += f"; capped at {cap} h (severity {sev})"
    out.update(policy_now=f"{m['action']} {LABEL[m['now']]}", policy_rec=f"{m['action']} {LABEL[best]}", interval_h=best,
               why=reason, cost_now_per_year=round(year_cost(m["now"])), cost_rec_per_year=round(year_cost(best)),
               window_now_h=m["now"], window_rec_h=best,
               cars_at_risk_now=int(round(m["now"] * CARS_PER_H)), cars_at_risk_rec=int(round(best * CARS_PER_H)),
               next_due=(now_ts + timedelta(hours=best)).strftime("%Y-%m-%d %H:%M"))
    return out


def run(con):
    rng = np.random.default_rng(SEED)
    first = min(pd.read_sql(f"SELECT MIN(ts) t FROM {s}", con).t.iloc[0] for s in station_tables(con))
    last = max(pd.read_sql(f"SELECT MAX(ts) t FROM {s}", con).t.iloc[0] for s in station_tables(con))
    week_start = pd.Timestamp(first).floor("D") + timedelta(hours=6)
    now_ts = pd.Timestamp(last).ceil("h")
    hist = add_demo_week(pd.DataFrame(simulate_history(week_start.to_pydatetime(), rng)), con, now_ts)
    rows = []
    for (sid, fam), m in MODES.items():
        h = hist[(hist.station == sid) & (hist.family == fam)]
        k, lam = fit_weibull(h.age_h.clip(lower=0.1), h.event == "failure", h.entry_h)
        own = h[h.unit == 0]
        last_renewal = pd.to_datetime(own.start).max() - timedelta(hours=float(own.entry_h.iloc[-1]))
        p = plan(sid, fam, m, k, lam, now_ts, last_renewal)
        rows.append({"station": sid, "equipment": STATIONS[sid]["equipment"][fam][0], "family": fam,
                     "failure_mode": STATIONS[sid]["equipment"][fam][1], "failures_seen": int((h.event == "failure").sum()),
                     "runs": len(h), "last_renewal": f"{last_renewal:%Y-%m-%d %H:%M}", **p})
    out = pd.DataFrame(rows)
    h = hist.copy()
    for c in ("start", "end"):
        h[c] = pd.to_datetime(h[c]).dt.strftime("%Y-%m-%d %H:%M")
    h.to_sql("maint_history", con, if_exists="replace", index=False)
    out.to_sql("maint_plan", con, if_exists="replace", index=False)
    con.commit()
    return out, hist, now_ts


def main():
    con = connect()
    out, hist, now_ts = run(con)
    con.close()
    print(f"Maintenance predictor - {len(hist)} runs in the history, {int((hist.event == 'failure').sum())} failures; "
          f"now = {now_ts:%a %d.%m %H:%M}\n")
    for r in out.itertuples():
        print(f"{r.equipment:<24} {r.failure_mode:<40} {r.kind:<6} shape {r.shape:.1f} (true {r.true_shape}), "
              f"life ~{r.mttf_h / 24:.0f} d")
        print(f"   now: {r.policy_now:<34} ->  {r.policy_rec}")
        print(f"   why: {r.why}")
        extra = f"   next 7 days: {r.p_fail_7d:.0%} chance of failure" + (f"; next due {r.next_due}" if r.next_due else "")
        if r.kind == "silent":
            extra += (f"; containment window {r.window_now_h:.0f} h ({r.cars_at_risk_now:,.0f} cars) -> "
                      f"{r.window_rec_h:.0f} h ({r.cars_at_risk_rec:,.0f} cars)")
        print(extra)
        print(f"   cost per year: {r.cost_now_per_year:,} -> {r.cost_rec_per_year:,} labour min\n")
    tot_now, tot_rec = out.cost_now_per_year.sum(), out.cost_rec_per_year.sum()
    print(f"all modes: {tot_now:,} -> {tot_rec:,} labour min per year ({(tot_rec - tot_now) / tot_now:+.0%})")


if __name__ == "__main__":
    main()
