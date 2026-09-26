"""
One picture of the floor listener: what the shift notes said, on the same timeline as the
incidents the cause finder found. Run after floor_listener.py.

    python plot_floor.py            # charts/floor.png
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from floor_listener import note_time
from plot_causes import CAUSE_COLOR
from plot_station import AXIS, GRID, INK, INK_2, MUTED, SURFACE   # same look as the other charts
from station_db import CHARTS_DIR, connect

LANES = {"st012": 2, "st013": 1, "line": 0}
LANE_TEXT = {2: "ST012", 1: "ST013", 0: "line / not said"}
MARK = {  # link type -> marker style (ink only: colour is kept for the incident causes)
    "agrees":        dict(marker="o", s=34, facecolors=INK_2, edgecolors=SURFACE, linewidths=1),
    "early warning": dict(marker="^", s=90, facecolors=INK, edgecolors=SURFACE, linewidths=1),
    "notes only":    dict(marker="D", s=44, facecolors=SURFACE, edgecolors=INK, linewidths=1.3),
    "disputes":      dict(marker="X", s=90, facecolors=INK, edgecolors=SURFACE, linewidths=1),
    "unclear":       dict(marker="o", s=40, facecolors=SURFACE, edgecolors=MUTED, linewidths=1.3),
}
LINK_TEXT = {"agrees": "agrees with an incident", "early warning": "early warning - floor saw it first",
             "notes only": "only in the notes", "disputes": "supervisor disputes the cause",
             "unclear": "supervisor unsure"}


def main():
    con = connect()
    f = pd.read_sql("SELECT * FROM floor_facts", con)
    notes = pd.read_sql("SELECT * FROM floor_notes", con).set_index("note_id")
    inc = pd.read_sql("SELECT * FROM incidents", con, parse_dates=["start_ts", "end_ts"])
    ev = dict(con.execute("SELECT key, value FROM floor_eval").fetchall())
    con.close()
    if f.empty:
        raise SystemExit("no facts - run floor_listener.py first")
    f["t"] = [note_time(r, notes.loc[r["note_id"]].to_dict() | {"shift_date": r["shift_date"], "shift": r["shift"]})
              for r in f.to_dict("records")]
    f["lane"] = f.station.map(LANES).fillna(0).astype(int)

    fig = plt.figure(figsize=(17, 8.2))
    ax = fig.add_axes([0.075, 0.2, 0.67, 0.6])
    bx = fig.add_axes([0.83, 0.47, 0.15, 0.33])
    told = inc[inc.incident_id.isin(f.incident_id.dropna())].culprit.nunique()
    lk = f.link.value_counts()
    fig.text(0.02, 0.95, "Floor listener - what the shift notes said, next to what the data found", fontsize=15,
             fontweight="bold", color=INK)
    fig.text(0.02, 0.915,
             f"{len(notes)} notes ({', '.join(f'{v} {k}' for k, v in notes.kind.value_counts().items())}) -> "
             f"{len(f)} facts, read by {ev.get('backend', '?')}  |  the floor also reported {told}/{inc.culprit.nunique()} "
             f"problems the cause finder found  |  {lk.get('early warning', 0)} early warnings, "
             f"{lk.get('notes only', 0)} things only people saw", fontsize=10, color=INK_2)

    # (a) timeline --------------------------------------------------------------------------------
    for r in inc.itertuples():
        y = LANES.get(r.top_station, 0) + 0.17
        ax.barh(y, mdates.date2num(r.end_ts) - mdates.date2num(r.start_ts), left=mdates.date2num(r.start_ts),
                height=0.2, color=CAUSE_COLOR.get(r.cause, AXIS), edgecolor=SURFACE, linewidth=1)
    for link, st in MARK.items():
        g = f[f.link == link]
        ax.scatter(mdates.date2num(g.t), g.lane - 0.12, zorder=5, **st)
    show = f[f.link.isin(["early warning", "notes only", "disputes", "unclear"]) & (f.status != "info")
             | (f.link == "disputes")].sort_values("t")
    last, done = {}, {}
    for r in show.itertuples():
        x = mdates.date2num(r.t)
        key = (r.subject, r.link)
        if key in done and x - done[key] < 0.5:          # same thing said twice within 12 h: label once
            continue
        done[key] = x
        k = 0 if x - last.get(r.lane, -9) > 0.9 else 1        # stagger labels that would touch
        last[r.lane] = x if k == 0 else last[r.lane]
        subj = r.subject or r.category
        txt = {"early warning": f"{subj}: {r.lead_h:.0f} h early" if pd.notna(r.lead_h) else subj,
               "disputes": f"{subj}: supervisor disputes", "unclear": f"{subj}: unsure"}.get(r.link, subj)
        ax.text(x, r.lane - 0.3 - 0.15 * k, txt, ha="center", va="top", fontsize=8, color=INK,
                fontweight="bold" if r.link == "early warning" else "normal")
    ax.set_yticks(list(LANE_TEXT), list(LANE_TEXT.values()), fontsize=10, color=INK)
    ax.set_ylim(-0.75, 2.5)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[6]))          # each day starts with shift A
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d.%m"))
    ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[14, 22]))
    t0 = inc.start_ts.min().normalize() + pd.Timedelta(hours=6)
    ax.set_xlim(mdates.date2num(t0), mdates.date2num(t0 + pd.Timedelta(days=7)))
    ax.grid(axis="x", which="major", color=GRID, lw=0.8)
    ax.grid(axis="x", which="minor", color=GRID, lw=0.4, ls=":")
    ax.grid(axis="y", visible=False)
    for yy in (0.5, 1.5):
        ax.axhline(yy, color=AXIS, lw=0.6)
    ax.set_title("Bars = incidents found in the data (colour = cause)   ·   marks = facts from the notes   ·   dotted = shift change", loc="left")
    h1 = [Patch(color=CAUSE_COLOR[c], label=f"incident: {c}") for c in ("machine", "people", "method", "station")]
    h2 = [Line2D([], [], ls="", marker=st["marker"], markersize=8, markerfacecolor=st["facecolors"],
                 markeredgecolor=st["edgecolors"], label=LINK_TEXT[k]) for k, st in MARK.items()]
    ax.legend(handles=h1 + h2, loc="upper left", bbox_to_anchor=(0, -0.1), ncol=5, frameon=False, fontsize=8.5)

    # (b) links + score --------------------------------------------------------------------------
    order = [k for k in MARK if k in lk.index]
    bx.barh(range(len(order))[::-1], [lk[k] for k in order], height=0.55, color=INK_2)
    for i, k in zip(range(len(order))[::-1], order):
        bx.text(lk[k] + 0.4, i, str(lk[k]), va="center", fontsize=9, color=INK)
    bx.set_yticks(range(len(order))[::-1], order, fontsize=9, color=INK)
    bx.grid(axis="y", visible=False)
    bx.grid(axis="x", color=GRID, lw=0.6)
    bx.set_title("Facts by link", loc="left")
    if "recall" in ev:
        txt = (f"Checked against the answer key\n"
               f"facts found  {float(ev['facts_found']):.0f}/{float(ev['facts_true']):.0f}  "
               f"({float(ev['recall']):.0%})\n"
               f"precision  {float(ev['precision']):.0%}\n"
               f"fields right  {float(ev['field_acc']):.0%}\n"
               f"answers read right  {float(ev['answers_ok']):.0f}/{float(ev['answers']):.0f}")
        if "rules" in ev.get("backend", ""):
            txt += "\n\noffline rules were written for this\nnote style - the LLM run is the real test"
        fig.text(0.785, 0.39, txt, fontsize=9.5, color=INK, va="top", linespacing=1.6)
    out = CHARTS_DIR / "floor.png"
    fig.savefig(out, dpi=140)
    print(f"floor chart -> {out}")


if __name__ == "__main__":
    main()
