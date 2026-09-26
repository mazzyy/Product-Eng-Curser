"""
One picture of the method checker: does each work instruction fit the takt, and which rule
groups pass, warn or block. Run after method_checker.py (and --propose for the proposals).

    python plot_method.py            # charts/method.png
"""
from __future__ import annotations

import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch, Rectangle

from method_checker import GROUPS, ORDER, TAKT_S, TAKT_WARN
from plot_station import AXIS, GRID, INK, INK_2, MUTED, SURFACE   # same look as the other charts
from station_db import CHARTS_DIR, connect

HANDS, MACHINE = "#2a78d6", "#c3c2b7"
STATUS = {"BLOCK": ("#d03b3b", "white"), "WARN": ("#fab219", INK), "INFO": (GRID, INK_2), "PASS": ("#f1f0ea", MUTED)}


def main():
    con = connect()
    v = pd.read_sql("SELECT * FROM method_verdicts", con)
    c = pd.read_sql("SELECT * FROM method_checks", con)
    try:
        ev = dict(con.execute("SELECT key, value FROM method_eval").fetchall())
    except Exception:
        ev = {}
    con.close()
    if v.empty:
        raise SystemExit("no verdicts - run method_checker.py first")
    v["no"] = v.wi_version.str.extract(r"v(\d+)").astype(int)
    v = v.sort_values(["station", "no"]).reset_index(drop=True)
    n = len(v)
    y = np.arange(n)[::-1]

    fig = plt.figure(figsize=(17, 1.6 + 0.62 * n + 1.2))
    ax = fig.add_axes([0.1, 0.12, 0.25, 0.68])
    mx = fig.add_axes([0.41, 0.12, 0.57, 0.68])
    cnt = v.verdict.value_counts()
    fig.text(0.02, 0.95, "Method checker - does each work instruction fit the takt, is it safe, are people trained?",
             fontsize=15, fontweight="bold", color=INK)
    sub = (f"{(v.source == 'history').sum()} versions from the simulated week + {(v.source == 'proposal').sum()} proposals  |  "
           + "  ".join(f"{k} {cnt.get(k, 0)}" for k in ("BLOCK", "WARN", "PASS"))
           + f"  |  fixed rules, no ML; takt {TAKT_S:.0f} s")
    if ev:
        sub += (f"  |  on {ev['weeks']} random simulated weeks: caught {ev['caught']}/{ev['bad']} bad versions, "
                f"{ev['false_alarms']} false alarms on {ev['harmless']} harmless ones")
    fig.text(0.02, 0.915, sub, fontsize=10, color=INK_2)

    # (a) planned cycle vs takt ------------------------------------------------------------------
    ax.barh(y, v.planned_op_s, height=0.55, color=HANDS, edgecolor=SURFACE, linewidth=2, label="hands-on (operator)")
    ax.barh(y, v.planned_machine_s, left=v.planned_op_s, height=0.55, color=MACHINE, edgecolor=SURFACE, linewidth=2,
            label="machine")
    ax.axvline(TAKT_S, color=INK, lw=1.3, zorder=4)
    ax.axvline(TAKT_S * TAKT_WARN, color=MUTED, lw=1, ls=(0, (3, 3)), zorder=4)
    ax.text(TAKT_S + 0.4, n - 0.35, f"takt {TAKT_S:.0f} s", ha="left", va="bottom", fontsize=8.5, color=INK)
    ax.text(TAKT_S * TAKT_WARN - 0.4, n - 0.35, "95 %", ha="right", va="bottom", fontsize=8, color=MUTED)
    for yy, r in zip(y, v.itertuples()):       # values in their own column, clear of the takt lines
        over = r.planned_cycle_s > TAKT_S
        ax.text(60.5, yy, f"{r.planned_cycle_s:.1f} s", va="center", ha="left", fontsize=9,
                color=INK, fontweight="bold" if over else "normal")
    ax.set_yticks(y, [f"{r.wi_version}{'  (proposal)' if r.source == 'proposal' else ''}" for r in v.itertuples()],
                  fontsize=9.5, color=INK)
    ax.set_xlim(0, 60)
    ax.set_ylim(-0.6, n - 0.1)
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("planned cycle time per car (s)")
    ax.set_title("Planned time vs takt", loc="left", pad=22)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.09), ncol=2, frameon=False, fontsize=8.5)
    for i in range(1, n):            # a thin line between the two stations
        if v.station.iloc[i] != v.station.iloc[i - 1]:
            ax.axhline(y[i] + 0.5, color=AXIS, lw=0.8)
            mx.axhline(y[i] + 0.5, color=AXIS, lw=0.8)

    # (b) rule matrix ----------------------------------------------------------------------------
    cols = ["Verdict"] + GROUPS
    w = [1.1] + [1.35] * len(GROUPS)
    xs = np.concatenate([[0], np.cumsum(w)])
    for yy, r in zip(y, v.itertuples()):
        cc = c[c.wi_version == r.wi_version]
        cells = [(r.verdict, r.verdict)]
        for g in GROUPS:
            gc = cc[cc.grp == g]
            if gc.empty:
                cells.append(("INFO", "n/a"))
                continue
            worst = min(gc.status, key=lambda s: ORDER[s])
            bad = gc[gc.status == worst].rule.tolist() if worst in ("BLOCK", "WARN") else []
            cells.append((worst, " ".join(bad) if bad else ("info" if worst == "INFO" else "ok")))
        for j, (st, txt) in enumerate(cells):
            bg, fg = STATUS[st]
            mx.add_patch(Rectangle((xs[j] + 0.04, yy - 0.3), w[j] - 0.08, 0.6, color=bg, lw=0))
            mx.text(xs[j] + w[j] / 2, yy, txt, ha="center", va="center", fontsize=8 if j else 8.5, color=fg,
                    fontweight="bold" if j == 0 or st in ("BLOCK", "WARN") else "normal")
        mx.text(xs[-1] + 0.25, yy, textwrap.fill(r.change_note, 46), va="center", fontsize=8.3, color=INK_2,
                linespacing=1.25)
    for j, name in enumerate(cols):
        mx.text(xs[j] + w[j] / 2, n - 0.35, name, ha="center", va="bottom", fontsize=9, color=INK, fontweight="bold")
    mx.text(xs[-1] + 0.25, n - 0.35, "What changed", va="bottom", fontsize=9, color=INK, fontweight="bold")
    mx.set_xlim(0, xs[-1] + 6.2)
    mx.set_ylim(-0.6, n - 0.1)
    mx.axis("off")
    mx.set_title("Rules: worst result per group (the rule IDs that fired)", loc="left", pad=22)
    mx.legend(handles=[Patch(color=STATUS[k][0], label=l) for k, l in
                       (("BLOCK", "BLOCK - must fix before rollout"), ("WARN", "WARN - fix or accept"),
                        ("PASS", "ok"), ("INFO", "info / not applicable"))],
              loc="upper left", bbox_to_anchor=(0, 0.0), ncol=4, frameon=False, fontsize=8.5)
    out = CHARTS_DIR / "method.png"
    CHARTS_DIR.mkdir(exist_ok=True)
    fig.savefig(out, dpi=140)
    print(f"method chart -> {out}")


if __name__ == "__main__":
    main()
