"""
One picture of the human side of ST012 + ST013 for the week.
Run after train_people_model.py.

    python plot_people.py            # charts/people.png
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.gridspec import GridSpec

from plot_station import AXIS, GRID, INK, INK_2, MUTED, SURFACE   # same look as the station charts
from station_db import CHARTS_DIR, STATIONS, connect, station_tables
from train_people_model import add_shift_clock, technique_offsets

ACCENT = "#eb6834"                     # highlighted operator-shifts
BAR = "#2a78d6"                        # single-series bars
LIGHT_LINE = "#c3c2b7"
# sequential blue ramp (light -> dark) for "how different from peers"
SEQ = ["#e6effb", "#b7d3f6", "#86b6ef", "#3987e5", "#256abf", "#184f95"]
SEQ_BOUNDS = [0, 1, 2, 3, 4, 6, 100]
TYPE_TEXT = {
    "rushed":       "Rushed  ·  possible skipped step",
    "struggle":     "Struggle  ·  slow, no andon pulled",
    "retries":      "Retry burst  ·  many re-hits",
    "support":      "Support gap  ·  andon unanswered",
    "login":        "Stale login  ·  two stations at once",
    "working_time": "Off-shift work  ·  rest time",
}


def load(con):
    frames = []
    for t in station_tables(con):
        df = pd.read_sql(f"SELECT * FROM {t} WHERE event_type='CYCLE'", con, parse_dates=["ts"])
        frames.append(df.assign(station=t))
    cyc = add_shift_clock(pd.concat(frames, ignore_index=True).dropna(subset=["operator_id"]))
    shifts = pd.read_sql("SELECT * FROM operator_shifts", con)
    return cyc, shifts


def main():
    con = connect()
    cyc, shifts = load(con)
    con.close()
    if cyc.human_flag.isna().all():
        raise SystemExit("no people-model results yet - run train_people_model.py first")

    fig = plt.figure(figsize=(15, 10))
    gs = GridSpec(2, 2, figure=fig, width_ratios=[1.35, 1], height_ratios=[1.05, 1],
                  hspace=0.42, wspace=0.28, left=0.07, right=0.97, top=0.87, bottom=0.07)
    n_flag = int(shifts.flag.sum())
    fig.text(0.07, 0.95, "People on ST012 + ST013 - where to support", fontsize=15, fontweight="bold", color=INK)
    fig.text(0.07, 0.915,
             f"{cyc.operator_id.nunique()} operators  |  {len(shifts)} operator-shifts  |  {len(cyc):,} cycles  |  "
             f"{n_flag} operator-shifts and {int(cyc.human_flag.sum())} cycles worth a supportive look",
             fontsize=10, color=INK_2)

    # (a) operator x day: how different from peers, with the finding written in -------------
    ax = fig.add_subplot(gs[0, 0])
    shifts["day"] = pd.to_datetime(shifts.shift_date)
    days = sorted(shifts.day.unique())
    ops = sorted(shifts.operator_id.unique(), key=lambda o: (o[3], int(o[4:])))
    score = shifts.pivot_table(index="operator_id", columns="day", values="peer_score", aggfunc="max").reindex(index=ops, columns=days)
    kinds = (shifts[shifts.flag == 1].groupby(["operator_id", "day"]).finding_type
             .agg(lambda x: " + ".join(dict.fromkeys(x))))
    cmap, norm = ListedColormap(SEQ), BoundaryNorm(SEQ_BOUNDS, len(SEQ))
    mesh = ax.pcolormesh(np.arange(len(days) + 1), np.arange(len(ops) + 1), np.ma.masked_invalid(score.values),
                         cmap=cmap, norm=norm, edgecolors=SURFACE, linewidth=2)
    for (op, day), text in kinds.items():
        i, j = ops.index(op), days.index(day)
        v = score.iat[i, j]
        ax.text(j + 0.5, i + 0.5, text.replace("_", " "), ha="center", va="center", fontsize=7.5,
                color="#ffffff" if v >= 3 else INK)
    ax.set_xticks(np.arange(len(days)) + 0.5, [pd.Timestamp(d).strftime("%a %d") for d in days])
    ax.set_yticks(np.arange(len(ops)) + 0.5, ops)
    ax.invert_yaxis()
    ax.tick_params(length=0)
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("Each operator's shift vs peers (darker = more different); flagged shifts are named")
    cb = fig.colorbar(mesh, ax=ax, fraction=0.03, pad=0.02, ticks=SEQ_BOUNDS[:-1])
    cb.set_label("difference vs peers (robust z)", color=INK_2, fontsize=8)
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=8, colors=MUTED, length=0)

    # (b) cycle-level findings -------------------------------------------------------------
    ax = fig.add_subplot(gs[0, 1])
    counts = cyc[cyc.human_flag == 1].human_type.value_counts()
    kinds_order = list(TYPE_TEXT)[::-1]
    vals = [int(counts.get(k, 0)) for k in kinds_order]
    ax.barh(range(len(kinds_order)), vals, height=0.42, color=BAR)
    for i, (k, v) in enumerate(zip(kinds_order, vals)):
        ax.text(v, i, f"  {v}", va="center", ha="left", color=INK, fontsize=9)
        ax.text(0, i + 0.28, TYPE_TEXT[k], va="bottom", ha="left", color=INK_2, fontsize=8.5)
    ax.set_yticks([])
    ax.set_ylim(-0.5, len(kinds_order) - 0.1)
    ax.set_xlim(0, max(vals + [1]) * 1.2)
    ax.spines["left"].set_visible(False)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_axisbelow(True)
    ax.set_title("Flagged cycles by kind")

    # (c) fatigue: pace through the shift ---------------------------------------------------
    ax = fig.add_subplot(gs[1, 0])
    c = cyc.copy()
    c["net"] = c.operator_time_s - c.retries.fillna(0) * c.station.map(lambda t: STATIONS.get(t, {}).get("retry_cost_s", 0))
    calm = c[(c.andon_pulled.fillna(0) == 0) & (c.human_flag != 1)]
    ref = calm.groupby(["station", "shift_date", "shift"]).net.transform("median")   # same station, same shift
    calm = calm.assign(pace=calm.net / ref * 100, hbin=(calm.hours_in * 2).astype(int) / 2 + 0.25)
    tired = shifts[shifts.finding_type == "fatigue"]
    tired_keys = set(zip(tired.operator_id, tired.shift_date, tired["shift"]))
    for key, g in calm.groupby(["operator_id", "shift_date", "shift"]):
        line = g.groupby("hbin").pace.median()
        if key in tired_keys:
            continue
        ax.plot(line.index, line.values, color=LIGHT_LINE, lw=0.8, alpha=0.6, zorder=1)
    for key, g in calm.groupby(["operator_id", "shift_date", "shift"]):
        if key not in tired_keys:
            continue
        line = g.groupby("hbin").pace.median()
        ax.plot(line.index, line.values, color=ACCENT, lw=2, zorder=3)
        ax.plot(line.index[-1], line.values[-1], "o", color=ACCENT, ms=5, mec=SURFACE, mew=1, zorder=4)
        ax.annotate(f"{key[0]}\n{pd.Timestamp(key[1]):%a %d}, shift {key[2]}", (line.index[-1], line.values[-1]),
                    xytext=(8, 0), textcoords="offset points", ha="left", va="center", color=INK, fontsize=8.5)
    ax.axhline(100, color=MUTED, lw=0.9, zorder=2)
    ax.set_xlim(0, 9.6)
    ax.set_xticks(range(0, 9))
    ax.set_xlabel("hours into shift")
    ax.set_ylabel("hands-on time, % of others on same station & shift")
    ax.set_title("Fatigue: pace through the shift (each grey line = one operator-shift)")

    # (d) technique: does the machine signature of someone's work differ? -----------------------
    ax = fig.add_subplot(gs[1, 1])
    meth = shifts[shifts.finding_type == "technique"]
    st = meth.technique_station.mode().iloc[0] if len(meth) else "st012"
    m = cyc[cyc.station == st].copy()
    m["mz"] = technique_offsets(m)
    per = m.groupby(["operator_id", "shift_date"]).mz.mean().reset_index()
    flagged_ops = set(meth.operator_id)
    rows = sorted(per.operator_id.unique(), key=lambda o: (o[3], int(o[4:])))
    for i, op in enumerate(rows):
        g = per[per.operator_id == op]
        hot = op in flagged_ops
        ax.scatter(g.mz, np.full(len(g), i), s=30 if hot else 16, c=ACCENT if hot else LIGHT_LINE,
                   edgecolors=SURFACE, linewidths=0.8, zorder=3 if hot else 2)
    ax.axvline(0, color=MUTED, lw=0.9)
    ax.set_yticks(range(len(rows)), rows)
    ax.invert_yaxis()
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    cfg = STATIONS.get(st, {})
    sec, prim = cfg.get("secondary", {}).get("name", "secondary"), cfg.get("primary", {}).get("name", "primary")
    ax.set_xlabel(f"avg {sec} offset from the normal {prim}/{sec} line (sigma)")
    ax.set_title(f"Technique: {st.upper()} signature per operator (one dot = one shift)")

    CHARTS_DIR.mkdir(exist_ok=True)
    out = CHARTS_DIR / "people.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"people chart -> {out}")


if __name__ == "__main__":
    main()
