"""
One picture of the cause finder: every incident of the week on a timeline, coloured by its
cause, plus how well the model does on weeks it never saw and which evidence it relies on.
Run after find_causes.py.

    python plot_causes.py            # charts/causes.png
"""
from __future__ import annotations

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Patch

from plot_station import GRID, INK, INK_2, MUTED, SURFACE   # same look as the other charts
from station_db import CHARTS_DIR, MODELS_DIR, connect

CAUSE_COLOR = {"machine": "#2a78d6", "people": "#eb6834", "method": "#1baf7a", "station": "#eda100",
               "unclear": "#c3c2b7"}
FAMILY_TEXT = {"quality": "quality", "speed": "speed", "pattern": "pattern", "data": "data"}
EVIDENCE_TEXT = {
    "h_to_repair": "stops at a repair", "wi_lift": "tied to an instruction version",
    "op_lift_week": "follows one operator", "run_blocks": "how long it lasts in a row",
    "crews_24h": "how many crews see it", "batch_lift": "tied to a part batch",
    "batch_cover": "share of the batch affected", "run_trend": "gets worse over time",
    "h_since_wi": "starts at a version change", "station_share": "share of the shift's blocks",
    "op_follow_shift": "operator's blocks this shift", "simultaneous": "both stations at once",
    "wait_only": "only waiting time", "h_batch_start": "starts with a batch", "h_batch_end": "ends with a batch",
    "op_lift_other": "follows operator to other station", "prev24": "seen the day before",
    "next24": "seen the day after", "op_diversity": "how many operators involved", "n_blocks": "blocks affected",
    "n_stations": "stations affected",
}
BLUES = LinearSegmentedColormap.from_list("blues", ["#f3f7fd", "#b7d3f6", "#6da7ec", "#2a78d6", "#184f95"])


def main():
    con = connect()
    inc = pd.read_sql("SELECT * FROM incidents", con, parse_dates=["start_ts", "end_ts"])
    con.close()
    bundle = joblib.load(MODELS_DIR / "cause_finder.joblib")
    if inc.empty:
        raise SystemExit("no incidents - run find_causes.py first")

    fig = plt.figure(figsize=(15, 10))
    gs = GridSpec(2, 2, figure=fig, height_ratios=[1.25, 1], width_ratios=[1, 1.25],
                  hspace=0.38, wspace=0.42, left=0.1, right=0.97, top=0.87, bottom=0.07)
    counts = inc.cause.value_counts()
    fig.text(0.1, 0.95, "Cause finder - why things went wrong on ST012 + ST013", fontsize=15,
             fontweight="bold", color=INK)
    fig.text(0.1, 0.915,
             f"{len(inc)} incidents grouped into {inc.case_id.nunique()} cases  |  "
             + "  ".join(f"{c} {counts.get(c, 0)}" for c in CAUSE_COLOR if counts.get(c, 0))
             + f"  |  accuracy {bundle['heldout_accuracy']:.0%} on simulated weeks the model never saw",
             fontsize=10, color=INK_2)

    # (a) timeline: lanes = station x symptom family, bars = incident blocks coloured by cause ------
    ax = fig.add_subplot(gs[0, :])
    lanes = [(s, f) for s in sorted(inc.top_station.unique()) for f in FAMILY_TEXT]
    y = {l: i for i, l in enumerate(lanes)}
    for r in inc.itertuples():
        for s in r.stations.split(","):
            yy = y[(s, r.family)]
            ax.barh(yy, (r.end_ts - r.start_ts) / pd.Timedelta(days=1), left=mdates.date2num(r.start_ts),
                    height=0.56, color=CAUSE_COLOR[r.cause], edgecolor=SURFACE, linewidth=1.5,
                    alpha=1.0 if s == r.top_station else 0.45)
    seen = set()
    for r in inc.sort_values("start_ts").itertuples():             # label each case once
        if r.case_id in seen:
            continue
        seen.add(r.case_id)
        label = r.culprit if r.cause != "unclear" else "unclear"
        ax.text(mdates.date2num(r.start_ts), y[(r.top_station, r.family)] + 0.36, label, fontsize=7.5,
                color=INK, va="bottom", ha="left", clip_on=False)
    ax.set_yticks(range(len(lanes)), [f"{s.upper()}  {FAMILY_TEXT[f]}" for s, f in lanes])
    ax.set_ylim(-0.6, len(lanes) - 0.2)
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
    ax.grid(axis="x", visible=True, color=GRID)
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    for i in range(1, len(lanes)):
        if lanes[i][0] != lanes[i - 1][0]:
            ax.axhline(i - 0.5, color=MUTED, lw=0.8)
    ax.set_title("Every incident of the week, coloured by its cause (label = the culprit; faded = echo at the other station)")
    ax.legend(handles=[Patch(color=CAUSE_COLOR[c], label=c) for c in CAUSE_COLOR if c != "unclear" or counts.get(c, 0)],
              loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=5, frameon=False, fontsize=9, labelcolor=INK_2,
              handlelength=1.2, columnspacing=1.4)

    # (b) held-out confusion matrix ------------------------------------------------------------
    ax = fig.add_subplot(gs[1, 0])
    cm = bundle["heldout_confusion"]
    share = cm.div(cm.sum(axis=1).replace(0, 1), axis=0)
    ax.imshow(share.values, cmap=BLUES, vmin=0, vmax=1)
    for i in range(len(cm)):
        for j in range(len(cm)):
            v = cm.iat[i, j]
            ax.text(j, i, f"{v}", ha="center", va="center", fontsize=10,
                    color="#ffffff" if share.iat[i, j] > 0.6 else INK)
    ax.set_xticks(range(len(cm)), cm.columns)
    ax.set_yticks(range(len(cm)), cm.index)
    ax.set_xlabel("model says")
    ax.set_ylabel("true cause")
    ax.tick_params(length=0)
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title(f"Tested on {max(1, bundle['weeks'] // 5)} simulated weeks it never saw (incidents)")

    # (c) evidence the model relies on ----------------------------------------------------------
    ax = fig.add_subplot(gs[1, 1])
    imp = bundle["importance"]
    imp = imp[[k for k in imp.index if k in EVIDENCE_TEXT]].head(9)[::-1]
    ax.barh(range(len(imp)), imp.values, height=0.5, color="#2a78d6")
    for i, v in enumerate(imp.values):
        ax.text(v, i, f"  {v:.0%}", va="center", ha="left", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(imp)), [EVIDENCE_TEXT[k] for k in imp.index])
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, imp.max() * 1.25)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_axisbelow(True)
    ax.set_title("Evidence the model leans on most (share of its decisions)")

    CHARTS_DIR.mkdir(exist_ok=True)
    out = CHARTS_DIR / "causes.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"cause chart -> {out}")


if __name__ == "__main__":
    main()
