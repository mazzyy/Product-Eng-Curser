"""
One picture of the impact ranker: what matters most this week, can we hit 7,500, and which
problems would hurt most if they happened next. Run after impact_ranker.py.

    python plot_impacts.py            # charts/impacts.png
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

from plot_station import GRID, INK, INK_2, MUTED, SURFACE   # same look as the other charts
from station_db import CHARTS_DIR, connect

LEVEL_COLOR = {"High": "#d03b3b", "Medium": "#fab219", "Low": "#c3c2b7"}   # status colours, always labelled
BUILT = "#2a78d6"
TOP_N = 24


def short(text: str, n: int = 62) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def main():
    con = connect()
    imp = pd.read_sql("SELECT * FROM impacts ORDER BY rank", con)
    cat = pd.read_sql("SELECT * FROM impact_catalog", con)
    summ = dict(con.execute("SELECT key, value FROM impact_summary").fetchall())
    con.close()
    if imp.empty:
        raise SystemExit("no impacts - run impact_ranker.py first")

    fig = plt.figure(figsize=(18, 12))
    ax_rank = fig.add_axes([0.25, 0.06, 0.36, 0.80])
    ax_target = fig.add_axes([0.70, 0.68, 0.27, 0.15])
    ax_cat = fig.add_axes([0.85, 0.06, 0.12, 0.50])
    counts = imp.category.value_counts()
    fig.text(0.02, 0.955, "Impact ranker - what matters most this week", fontsize=16, fontweight="bold", color=INK)
    fig.text(0.02, 0.925, f"{len(imp)} impacts from all models  |  "
             + "  ".join(f"{k} {counts.get(k, 0)}" for k in LEVEL_COLOR)
             + f"  |  score = 40% safety & quality + 30% delivery + 15% cost + 15% people, plus hard rules",
             fontsize=10.5, color=INK_2)

    # (a) ranked list --------------------------------------------------------------------------
    ax = ax_rank
    top = imp.head(TOP_N).iloc[::-1]
    y = np.arange(len(top))
    ax.barh(y, top.priority, height=0.62, color=[LEVEL_COLOR[c] for c in top.category])
    nxt = top[top.score_next > top.score_now + 0.5]
    ax.scatter(nxt.score_next, y[top.score_next.values > top.score_now.values + 0.5], s=40, facecolors=SURFACE,
               edgecolors=INK, linewidths=1.2, zorder=4, label="next week if nothing changes")
    for yy, r in zip(y, top.itertuples()):
        tag = "  · hard rule" if r.rule and r.category == "High" and r.priority < 50 else ""
        ax.text(r.priority + 1, yy, f"{r.category.upper()} {r.priority:.0f}{tag}", va="center", ha="left",
                fontsize=8.5, color=INK, fontweight="bold" if r.category == "High" else "normal")
    labels = [f"{short(r.title, 50)}  ·  {r.status}" for r in top.itertuples()]
    ax.set_yticks(y, labels, fontsize=9, color=INK_2)
    ax.tick_params(axis="y", length=0)
    for lim, name in ((25, "Medium from 25"), (50, "High from 50")):
        ax.axvline(lim, color=MUTED, lw=0.9)
        ax.text(lim, len(top) - 0.3, f" {name}", color=INK_2, fontsize=8, va="bottom")
    ax.set_xlim(0, 90)
    ax.set_ylim(-0.7, len(top) + 0.2)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True, color=GRID)
    ax.set_axisbelow(True)
    ax.set_xlabel("priority score 0-100 (High can also come from a hard rule, e.g. cars that must be checked)")
    ax.set_title(f"Top {len(top)} impacts, ranked (High first)", loc="left")
    ax.legend(handles=[Patch(color=LEVEL_COLOR[k], label=k) for k in LEVEL_COLOR]
              + [plt.Line2D([], [], marker="o", ls="", mfc=SURFACE, mec=INK, label="next week if nothing changes")],
              loc="lower right", frameon=False, fontsize=9, labelcolor=INK_2)

    # (b) can we hit 7,500? ----------------------------------------------------------------------
    ax = ax_target
    target, built = float(summ["target_per_week"]), float(summ["cars_built"])
    cap, proj = float(summ["capacity"]), float(summ["projected_next_week"])
    lost = imp.groupby("category").lost_cars.sum().reindex(list(LEVEL_COLOR)).fillna(0)
    rows = [("this week", built), ("next week\n(projected)", proj)]
    for i, (name, val) in enumerate(rows):
        ax.barh(i, val, height=0.5, color=BUILT)
        ax.text(val / 2, i, f"{val:,.0f} cars", ha="center", va="center", color="#ffffff", fontsize=9, fontweight="bold")
    left = built
    for k in LEVEL_COLOR:
        if lost[k] > 0:
            ax.barh(0, lost[k], left=left, height=0.5, color=LEVEL_COLOR[k], edgecolor=SURFACE, linewidth=1)
            left += lost[k]
    ax.axvline(target, color=INK, lw=1.4)
    ax.text(target, 1.45, f"target {target:,.0f}", ha="center", fontsize=9, color=INK, va="top")
    ax.axvline(cap, color=MUTED, lw=0.9)
    ax.text(cap, 1.45, f"capacity {cap:,.0f}", ha="center", fontsize=8.5, color=INK_2, va="top")
    ax.set_yticks([0, 1], [r[0] for r in rows], fontsize=9, color=INK_2)
    ax.set_ylim(-0.5, 1.6)
    ax.invert_yaxis()
    ax.set_xlim(0, cap * 1.18)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True, color=GRID)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_title(f"Can we hit {target:,.0f} cars/week?  Yes: {summ['attainment']} of target\n"
                 f"bottleneck {summ['bottleneck'].upper()} at {float(summ['bottleneck_cycle_s']):.0f} s/car; "
                 f"{lost.sum():,.0f} cars lost to ranked problems", loc="left", fontsize=10)

    # (c) if it happened next week (catalog) -------------------------------------------------------
    ax = ax_cat
    cat["key"] = cat.cause + " · " + cat.problem
    order = cat.groupby("key").score.max().sort_values(ascending=False).index
    stations = sorted(cat.station.unique())
    for i, k in enumerate(order):
        for j, s in enumerate(stations):
            r = cat[(cat.key == k) & (cat.station == s)].iloc[0]
            ax.add_patch(plt.Rectangle((j - 0.46, i - 0.42), 0.92, 0.84, color=LEVEL_COLOR[r.category]))
            txt = f"{r.category[0]} {r.score:.0f}" + ("  seen" if r.seen_this_week else "")
            ax.text(j, i, txt, ha="center", va="center", fontsize=8.5,
                    color="#ffffff" if r.category == "High" else INK)
    ax.set_xlim(-0.5, len(stations) - 0.5)
    ax.set_ylim(len(order) - 0.5, -0.5)
    ax.set_xticks(range(len(stations)), [s.upper() for s in stations])
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(order)), [short(k, 46) for k in order], fontsize=8.8, color=INK_2)
    ax.tick_params(length=0)
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("Could happen next week (same formula, typical size)\nH/M/L + score; H may come from a hard rule",
                 loc="right", pad=24, fontsize=10)

    CHARTS_DIR.mkdir(exist_ok=True)
    out = CHARTS_DIR / "impacts.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"impact chart -> {out}")


if __name__ == "__main__":
    main()
