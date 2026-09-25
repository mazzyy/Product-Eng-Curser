"""
Cause finder (model 2) - the shared logic: for every problem the line shows, collect the
evidence that tells machine, people, method and station apart.

1. Blocks     The week is cut into 2-hour rotation blocks per station. In one block, one
              operator works one station under one work-instruction version, mostly on one
              part batch - so each block is a small natural experiment.
2. Symptoms   Per block, 10 KPIs are compared with that station's normal (robust z-score):
                quality: re-hits, NOK rate         speed: hands-on time, machine time, waiting
                pattern: signal level, signal offset   data: no VIN, double VIN, no measurement
3. Incidents  Symptomatic blocks of the same family in the same shift form one incident.
4. Evidence   Each incident is linked to its candidate culprits: the operator, the station's
   graph      machine, the work-instruction version, the part batch, the supply / IT. The rest of
              the week decides how strong each link is: does the symptom follow the person
              after rotation? start with a new instruction version? stop after a repair? live
              and die with a batch? hit both stations at once?

train_cause_model.py learns (random forest) how those evidence features map to the true cause,
from many simulated weeks. find_causes.py applies it to the database.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from station_db import STATIONS

FAMILIES = ["quality", "speed", "pattern", "data"]
CAUSES = ["machine", "people", "method", "station"]
KPI = {   # name: (family, one-sided)
    "retry": ("quality", True), "nok": ("quality", True),
    "op_t": ("speed", True), "mach": ("speed", True), "wait": ("speed", True),
    "level": ("pattern", False), "offset": ("pattern", False),
    "miss": ("data", True), "dup": ("data", True), "meas": ("data", True),
}
RATES = {"retry", "nok", "miss", "dup", "meas"}   # counted per car -> Poisson-scale z
Z_SYM = 4.0            # a block shows a symptom beyond this robust z
MIN_CYCLES = 30        # ignore blocks with too little production
SHIFT_START = {"A": 6, "B": 14, "C": 22}
H = pd.Timedelta(hours=1)

FEATURES = (
    ["n_blocks", "n_stations", "simultaneous", "station_share", "op_diversity", "op_follow_shift",
     "op_lift_week", "op_lift_other", "prev24", "next24", "crews_24h", "run_blocks", "run_trend",
     "h_since_wi", "wi_lift", "batch_lift", "batch_cover", "h_batch_start", "h_batch_end", "h_to_repair",
     "wait_only"]
    + [f"{k}_z" for k in KPI] + [f"fam_{f}" for f in FAMILIES]
)


def mad(x) -> float:
    x = pd.Series(x).dropna()
    return float(1.4826 * (x - x.median()).abs().median()) if len(x) else np.nan


# ---------------------------------------------------------------------------
# 1 + 2. Blocks and symptoms
# ---------------------------------------------------------------------------
def cycles(df: pd.DataFrame, sid: str) -> pd.DataFrame:
    """Production cycles of one station with their block key and per-cycle KPI inputs."""
    c = df[(df.event_type == "CYCLE") & df.operator_id.notna()].copy()
    c["ts"] = pd.to_datetime(c.ts)
    start = c.ts.dt.normalize() + pd.to_timedelta(c["shift"].map(SHIFT_START), unit="h")
    start = start.where(start <= c.ts, start - pd.Timedelta(days=1))
    c["shift_date"] = start.dt.strftime("%Y-%m-%d")
    c["block"] = ((c.ts - start).dt.total_seconds() // 7200).clip(0, 3).astype(int)
    c["block_start"] = start + pd.to_timedelta(c.block * 2, unit="h")
    ok = c[(c.result == "OK") & c.primary_value.notna() & c.secondary_value.notna()]
    slope, icpt = np.polyfit(ok.primary_value, ok.secondary_value, 1)
    resid = c.secondary_value - (icpt + slope * c.primary_value)
    good = c.result == "OK"                      # rejected parts are a quality symptom, not a pattern one
    c["offset"] = (resid / mad(resid.loc[ok.index])).where(good)
    # level of the main signal, temperature-compensated (a leak test reads higher when warm)
    okt = ok[ok.temperature_c.notna()]
    tslope, ticpt = np.polyfit(okt.temperature_c, okt.primary_value, 1) if len(okt) > 100 else (0.0, ok.primary_value.median())
    lvl = c.primary_value - (ticpt + tslope * c.temperature_c.fillna(okt.temperature_c.median()))
    c["level"] = (lvl / mad(lvl.loc[ok.index])).where(good)
    c["miss"] = c.vin.isna().astype(float)
    c["dup"] = (c.vin.notna() & c.vin.duplicated(keep="first")).astype(float)
    c["meas"] = c.primary_value.isna().astype(float)
    c["nok"] = (c.result == "NOK").astype(float)
    c["op_net"] = c.operator_time_s - c.retries.fillna(0) * STATIONS.get(sid, {}).get("retry_cost_s", 0.0)
    c["station"] = sid
    return c


def _lower_tail(x: pd.Series, floor: float):
    """Center and scale for a KPI that problems can only push UP (re-hits, time, errors):
    estimated from the lower part of the distribution, so even if half the week is affected
    the baseline stays the healthy one. (Normal: q30 = mu - 0.52 sd, q05 = mu - 1.645 sd.)"""
    x = x.dropna()
    q05, q30 = x.quantile(0.05), x.quantile(0.30)
    sd = max((q30 - q05) / 1.12, floor)
    return float(q30 + 0.52 * sd), float(sd)


def _robust(x: pd.Series, floor: float):
    """Center and scale of a KPI across blocks, ignoring the symptomatic ones (3 passes)."""
    keep = x.dropna()
    c, s = keep.median(), floor
    for _ in range(3):
        c = keep.median()
        s = max(1.4826 * (keep - c).abs().median(), floor)
        keep = x[(x - c).abs() <= 3 * s].dropna()
    return float(c), float(s)


def block_table(tables: dict) -> pd.DataFrame:
    parts = []
    for sid, df in tables.items():
        c = cycles(df, sid)
        mode = lambda x: x.mode().iloc[0] if x.notna().any() else None
        b = c.groupby(["shift_date", "shift", "block"]).agg(
            station=("station", "first"), start=("block_start", "first"), n=("ts", "size"),
            op=("operator_id", mode), wi=("work_instruction", mode), batch=("part_batch", mode),
            batches=("part_batch", lambda x: ",".join(sorted(set(x.dropna())))),
            retry=("retries", "mean"), nok=("nok", "mean"),
            op_t=("op_net", "median"), mach=("machine_time_s", "median"),
            wait=("wait_time_s", lambda x: x.clip(upper=120).mean()),
            level=("level", "mean"), offset=("offset", "mean"),
            miss=("miss", "mean"), dup=("dup", "mean"), meas=("meas", "mean"),
        ).reset_index()
        b["end"] = b.start + 2 * H
        full = b.n >= MIN_CYCLES
        n_typ = float(b.n[full].median())
        for k, (_, one_sided) in KPI.items():
            b[f"{k}_base"] = float(b.loc[full, k].median())
            if k in RATES:      # counts per block: variance-stabilised (Anscombe) so rare events don't false-alarm
                x = 2 * np.sqrt(b[k].fillna(0) * b.n + 3 / 8)
                floor = 1.0
            else:
                x = b[k]
                base = abs(b[f"{k}_base"].iloc[0])
                floor = {"op_t": 0.03 * base, "mach": 0.01 * base, "wait": max(2.0, 0.3 * base),
                         "level": 1 / np.sqrt(n_typ), "offset": 1 / np.sqrt(n_typ)}[k]
            # waiting mostly echoes the upstream station's pace, so it gets a plain median baseline
            robust = _robust if (not one_sided or k == "wait") else _lower_tail
            c0, s0 = robust(x[full], floor)
            z = ((x - c0) / s0).where(full, 0.0).fillna(0.0)
            b[f"{k}_z"] = z.clip(lower=0) if one_sided else z
        parts.append(b)
    B = pd.concat(parts, ignore_index=True).sort_values(["start", "station"]).reset_index(drop=True)
    for fam in FAMILIES:
        cols = [f"{k}_z" for k, (f, _) in KPI.items() if f == fam]
        B[f"{fam}_z"] = B[cols].abs().max(axis=1)
        B[f"{fam}_sym"] = B[f"{fam}_z"] >= Z_SYM
    B["speed_own_z"] = B[["op_t_z", "mach_z"]].max(axis=1)      # waiting is often a downstream echo
    B["bid"] = B.index
    return B


# ---------------------------------------------------------------------------
# 3. Incidents
# ---------------------------------------------------------------------------
def incidents(B: pd.DataFrame) -> list:
    out = []
    for fam in FAMILIES:
        S = B[B[f"{fam}_sym"]]
        for (sd, sh), g in S.groupby(["shift_date", "shift"]):
            out.append({"family": fam, "shift_date": sd, "shift": sh, "bids": g.bid.tolist(),
                        "start": g.start.min()})
    return sorted(out, key=lambda x: (x["start"], x["family"]))


def context(tables: dict) -> dict:
    """Work-instruction changes, part-batch spans and unplanned repairs, straight from the station tables."""
    wi, repairs, batches = {}, {}, {}
    for sid, df in tables.items():
        c = df[df.event_type == "CYCLE"].sort_values("ts")
        g = c.assign(t=pd.to_datetime(c.ts)).groupby("part_batch").t
        batches[sid] = {b: (lo, hi) for b, lo, hi in zip(g.min().index, g.min(), g.max())}
        ch = c.work_instruction != c.work_instruction.shift()
        wi[sid] = list(zip(pd.to_datetime(c.ts[ch]).iloc[1:], c.work_instruction[ch].iloc[1:]))
        m = df[(df.event_type == "MAINTENANCE") & df.comment.fillna("").str.startswith("Unplanned")]
        repairs[sid] = list(zip(pd.to_datetime(m.ts), m.comment))
    return {"wi": wi, "repairs": repairs, "batches": batches}


# ---------------------------------------------------------------------------
# 4. Evidence features + graph
# ---------------------------------------------------------------------------
def _rate(df, col) -> float:
    return float(df[col].mean()) if len(df) else 0.0


def _run_around(Bt: pd.DataFrame, sym: str, idx) -> pd.DataFrame:
    """Consecutive symptomatic blocks at one station around the incident."""
    Bt = Bt.sort_values("start")
    seg, cur, last = [], 0, -10
    for i, flag in enumerate(Bt[sym].values):
        if flag:
            if i - last > 1:
                cur += 1
            last = i
            seg.append(cur)
        else:
            seg.append(-1)
    seg = pd.Series(seg, index=Bt.index)
    ids = set(seg.loc[list(idx)]) - {-1}
    return Bt.loc[seg[seg.isin(ids)].index]


def evidence(inc: dict, B: pd.DataFrame, ctx: dict):
    fam, sym, zc = inc["family"], f"{inc['family']}_sym", f"{inc['family']}_z"
    S = B.loc[inc["bids"]]
    score = S["speed_own_z"] if fam == "speed" and (S["speed_own_z"] >= Z_SYM).any() else S[zc]
    topb = S.loc[score.idxmax()]
    top, top_op = topb.station, topb.op
    St = S[S.station == top]
    shiftB = B[(B.shift_date == inc["shift_date"]) & (B["shift"] == inc["shift"])]
    Bt, Bo = B[B.station == top], B[B.station != top]
    f = {}
    f["n_blocks"] = len(S)
    f["n_stations"] = S.station.nunique()
    f["simultaneous"] = float(S.groupby("start").station.nunique().max() > 1)
    f["station_share"] = len(St) / max(int((shiftB.station == top).sum()), 1)
    f["op_diversity"] = S.op.nunique() / len(S)
    f["op_follow_shift"] = _rate(shiftB[shiftB.op == top_op], sym)
    mine, others = Bt[Bt.op == top_op], Bt[Bt.op != top_op]
    f["op_lift_week"] = _rate(mine, sym) - _rate(others, sym)
    f["op_lift_other"] = _rate(Bo[Bo.op == top_op], sym) - _rate(Bo[Bo.op != top_op], sym)
    t0, t1 = St.start.min(), St.end.max()
    f["prev24"] = _rate(Bt[(Bt.start >= t0 - 24 * H) & (Bt.start < t0)], sym)
    f["next24"] = _rate(Bt[(Bt.start >= t1) & (Bt.start < t1 + 24 * H)], sym)
    f["crews_24h"] = Bt[(Bt.start >= t0 - 24 * H) & (Bt.start < t1 + 24 * H) & Bt[sym]]["shift"].nunique()

    run = _run_around(Bt, sym, St.index)
    f["run_blocks"] = len(run)
    ranks = run.sort_values("start")[zc].rank().values
    if len(run) >= 3 and ranks.std() > 0:        # does it get worse block by block?
        f["run_trend"] = float(np.corrcoef(np.arange(len(run)), ranks)[0, 1])
    else:
        f["run_trend"] = 0.0
    onset, run_end = run.start.min(), run.end.max()

    changes = [(t, v) for t, v in ctx["wi"].get(top, []) if t <= onset + 0.5 * H]
    wi_change = changes[-1] if changes else None
    f["h_since_wi"] = float(np.clip((onset - wi_change[0]) / H, 0, 72)) if wi_change else 72.0
    cur_wi = topb.wi
    has_other = (Bt.wi != cur_wi).any()
    wi_in, wi_out = _rate(Bt[Bt.wi == cur_wi], sym), _rate(Bt[Bt.wi != cur_wi], sym)
    f["wi_lift"] = wi_in - wi_out if has_other else 0.0

    dom = St.batch.mode().iloc[0] if St.batch.notna().any() else ""
    in_b = Bt.batches.str.split(",").apply(lambda x: dom in x)
    near = (Bt.start >= onset - 24 * H) & (Bt.start < run_end + 24 * H)
    b_in, b_out = _rate(Bt[in_b], sym), _rate(Bt[~in_b & near], sym)
    f["batch_lift"], f["batch_cover"] = b_in - b_out, b_in
    span = ctx["batches"].get(top, {}).get(dom)                 # does it start and stop with the batch?
    f["h_batch_start"] = float(np.clip(abs((onset - span[0]) / H), 0, 24)) if span else 24.0
    f["h_batch_end"] = float(np.clip(abs((run_end - span[1]) / H), 0, 24)) if span else 24.0

    last_start = run.start.max()                                # a repair inside the last block counts
    rep = [(t, c) for t, c in ctx["repairs"].get(top, []) if last_start <= t <= run_end + 48 * H]
    repair = min(rep) if rep else None
    f["h_to_repair"] = float(np.clip((repair[0] - last_start) / H, 0, 48)) if repair else 48.0
    f["wait_only"] = float(fam == "speed" and S["speed_own_z"].max() < Z_SYM)
    for k, (_, one_sided) in KPI.items():
        v = S[f"{k}_z"].max() if one_sided else S[f"{k}_z"].abs().max()
        f[f"{k}_z"] = float(np.clip(v, 0, 30))
    for fm in FAMILIES:
        f[f"fam_{fm}"] = float(fam == fm)

    ev = {"top": top, "top_op": top_op, "topb": topb, "S": S, "run": run, "wi": cur_wi,
          "wi_change": wi_change, "wi_in": wi_in, "wi_out": wi_out, "batch": dom, "b_in": b_in,
          "b_out": b_out, "repair": repair, "mine": _rate(mine, sym), "others": _rate(others, sym),
          "n_mine": len(mine), "stations": sorted(S.station.unique()), "batch_span": span, "onset": onset,
          "run_end": run_end,
          "shift_mine": int(shiftB[shiftB.op == top_op][sym].sum()), "shift_mine_n": int((shiftB.op == top_op).sum()),
          "shift_others": int(shiftB[shiftB.op != top_op][sym].sum()), "shift_others_n": int((shiftB.op != top_op).sum())}
    return f, ev


# ---------------------------------------------------------------------------
# Plain-language output
# ---------------------------------------------------------------------------
def symptom_text(fam: str, ev: dict) -> str:
    b, top = ev["topb"], ev["top"].upper()
    cfg = STATIONS.get(ev["top"], {})
    kpis = [k for k, (f, _) in KPI.items() if f == fam]
    if fam == "speed" and b["speed_own_z"] >= Z_SYM:
        kpis = ["op_t", "mach"]
    k = max(kpis, key=lambda x: abs(b[f"{x}_z"]))
    v, base = b[k], b[f"{k}_base"]
    prim = cfg.get("primary", {}).get("name", "primary").replace("_", " ")
    sec = cfg.get("secondary", {}).get("name", "secondary").replace("_", " ")
    text = {
        "retry": f"re-hits {v:.2f} per car (normal {base:.2f})",
        "nok": f"NOK {v:.1%} (normal {base:.1%})",
        "op_t": f"hands-on time {v:.0f}s (normal {base:.0f}s)",
        "mach": f"machine time {v:.1f}s (normal {base:.1f}s)",
        "wait": f"waiting {v:.0f}s per car (normal {base:.0f}s)",
        "level": f"{prim} shifted {v:+.1f} sigma",
        "offset": f"{sec} off its normal pattern by {v:+.1f} sigma",
        "miss": f"{v:.0%} of cars without VIN",
        "dup": f"{v:.0%} double VIN scans",
        "meas": f"{v:.0%} missing measurements",
    }[k]
    others = [s.upper() for s in ev["stations"] if s != ev["top"]]
    return f"{top}: {text}" + (f" (also seen at {', '.join(others)})" if others else "")


def culprit_and_evidence(cause: str, fam: str, f: dict, ev: dict):
    top, cfg = ev["top"], STATIONS.get(ev["top"], {})
    T = top.upper()
    if cause == "people":
        op = ev["top_op"]
        if f["op_lift_week"] >= 0.4:
            txt = [f"follows {op}: symptom in {ev['mine']:.0%} of their blocks at {T} this week vs "
                   f"{ev['others']:.0%} for everyone else there"]
        else:
            txt = [f"follows {op} in this shift: {ev['shift_mine']} of their {ev['shift_mine_n']} blocks show it, "
                   f"{ev['shift_others']} of the other {ev['shift_others_n']} blocks do"]
        if len(ev["stations"]) > 1 and f["op_follow_shift"] >= 0.99:
            txt.append("it moved with them to the other station after rotation")
        return op, "; ".join(txt)
    if cause == "machine":
        name = cfg.get("equipment", {}).get(fam, (f"{T} equipment",))[0]
        txt = [f"hits every operator at {T} ({f['station_share']:.0%} of the shift's blocks, "
               f"{f['run_blocks']} blocks in a row)"]
        if f["run_trend"] > 0.4:
            txt.append("gets worse block by block")
        if ev["repair"] and f["h_to_repair"] <= 4:
            txt.append(f"stopped after '{ev['repair'][1].replace('Unplanned repair: ', '')}' at "
                       f"{ev['repair'][0]:%a %H:%M}")
        return name, "; ".join(txt)
    if cause == "method":
        txt = []
        if ev["wi_change"] and f["h_since_wi"] < 12:
            txt.append(f"started {f['h_since_wi']:.0f} h after {ev['wi_change'][1]} went live")
        txt.append(f"{ev['wi_in']:.0%} of blocks under {ev['wi']} show it vs {ev['wi_out']:.0%} under other versions")
        txt.append(f"{f['crews_24h']} crew(s) affected - not one person")
        return ev["wi"], "; ".join(txt)
    # station
    if fam == "data" and f["simultaneous"]:
        return "MES / line network", "hit both stations at the same time - shared IT, not a person or one scanner"
    if f["wait_only"]:
        return f"Material supply to {T}", "stations waiting for material; nobody works slower and no machine is slower"
    name = cfg.get("batch", ("", "part batch"))[1]
    txt = f"lives and dies with {name} {ev['batch']}"
    if ev["batch_span"]:
        lo, hi = ev["batch_span"]
        txt += (f": batch in use {lo:%a %H:%M}-{hi:%H:%M}, symptom {ev['onset']:%H:%M}-{ev['run_end']:%H:%M}, "
                f"all operators")
    return f"{name} {ev['batch']}", txt


def graph_text(fam: str, f: dict, ev: dict) -> str:
    """Candidate culprits linked to the incident, with a simple strength 0..1."""
    cfg = STATIONS.get(ev["top"], {})
    mach = cfg.get("equipment", {}).get(fam, ("equipment",))[0]
    edges = [
        (ev["top_op"], "people", max(f["op_lift_week"], 0)),
        (mach, "machine", min(1.0, 0.5 * f["station_share"] + 0.25 * (f["h_to_repair"] <= 4)
                              + 0.25 * max(f["run_trend"], 0)) * (1 - max(f["op_lift_week"], 0))),
        (ev["wi"], "method", max(f["wi_lift"], 0) * (1.0 if f["h_since_wi"] < 12 else 0.5)),
        (f"batch {ev['batch']}", "station", max(f["batch_lift"], 0)),
    ]
    if f["simultaneous"] and fam == "data":
        edges.append(("MES / line network", "station", 1.0))
    if f["wait_only"]:
        edges.append((f"supply to {ev['top'].upper()}", "station", 1.0))
    edges.sort(key=lambda e: -e[2])
    return " | ".join(f"{n} ({c}) {w:.2f}" for n, c, w in edges)


# ---------------------------------------------------------------------------
# Ground truth matching (simulated data only)
# ---------------------------------------------------------------------------
def _block_in(b, sc) -> bool:
    if b.station not in str(sc.stations).split(","):
        return False
    s0, s1 = pd.Timestamp(sc.start), pd.Timestamp(sc.end)
    if not (b.start < s1 and b.end > s0):
        return False
    if sc.cause == "people":
        if b.op != sc.operator:
            return False
        if isinstance(sc.shift_date, str) and (b.shift_date != sc.shift_date or b["shift"] != sc.shift):
            return False
    if sc.cause == "method" and b.wi != sc.wi:
        return False
    if sc.cause == "station" and isinstance(sc.batch, str) and sc.batch not in b.batches.split(","):
        return False
    if isinstance(sc.bursts, str):
        spans = [tuple(map(pd.Timestamp, x.split("/"))) for x in sc.bursts.split(";")]
        if not any(b.start < e and b.end > s for s, e in spans):
            return False
    return True


def match_scenario(inc: dict, B: pd.DataFrame, causes: pd.DataFrame):
    """Which planted root cause does this incident belong to? (label, scenario_id) or ('none', None)."""
    S = B.loc[inc["bids"]]
    cand = causes[causes.family == inc["family"]]
    best, best_n = None, 0
    for sc in cand.itertuples():
        hits = [b for _, b in S.iterrows() if _block_in(b, sc)]
        if inc["family"] == "speed" and hits:           # downstream stations wait for the same reason
            times = {(b.start) for b in hits}
            hits += [b for _, b in S.iterrows() if b.start in times and not _block_in(b, sc)]
        if len(hits) > best_n:
            best, best_n = sc, len(hits)
    return (best.cause, best.scenario_id) if best is not None else ("none", None)
