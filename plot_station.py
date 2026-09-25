"""
One picture per station: what normal looks like, and where the model found
noise / ambiguity. Run after train_model.py.

    python plot_station.py                 # charts/st012.png, charts/st013.png
    python plot_station.py --station st013
"""
from __future__ import annotations

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.gridspec import GridSpec

from station_db import CHARTS_DIR, STATIONS, connect, station_tables

# ---- look & feel (light chart surface, recessive chrome) -------------------
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
NORMAL = "#c3c2b7"
# colour follows the anomaly type, same everywhere (fixed order, validated palette)
TYPES = {
    "pattern":        ("#2a78d6", "Pattern (model)",  "off-pattern, drift"),
    "sensor":         ("#eb6834", "Sensor noise",     "stuck value, dropout"),
    "label_conflict": ("#1baf7a", "Label conflict",   "OK/NOK contradicts data"),
    "unlogged_event": ("#eda100", "Unlogged event",   "slow cycle, fault w/o code"),
    "traceability":   ("#e87ba4", "Traceability",     "VIN missing / double / skipped"),
}

_fonts = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams.update({
    "font.family": next((f for f in ("Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans") if f in _fonts), "sans-serif"),
    "font.size": 9, "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
    "axes.titlesize": 10.5, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 10,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.6,
})


def normal_dots(ax, x, y):
    ax.scatter(x, y, s=3, c=NORMAL, alpha=0.55, linewidths=0, rasterized=True, zorder=1)


def flag_dots(ax, x, y, kind):
    ax.scatter(x, y, s=22, c=TYPES[kind][0], edgecolors=SURFACE, linewidths=0.9, zorder=3,
               label=TYPES[kind][1])


def ref_line(ax, y, text):
    """Solid hairline with its label sitting just above the right end."""
    ax.axhline(y, color=MUTED, lw=0.9, zorder=2)
    ax.annotate(text, xy=(1, y), xycoords=("axes fraction", "data"), xytext=(-4, 3),
                textcoords="offset points", ha="right", va="bottom", color=INK_2, fontsize=8)


def nice(name: str) -> str:
    return name.replace("_", " ")


def plot_station(con, table: str) -> None:
    df = pd.read_sql(f"SELECT * FROM {table} ORDER BY ts", con, parse_dates=["ts"])
    if df.anomaly_flag.isna().all():
        raise SystemExit(f"{table}: no model results yet - run train_model.py first")
    cyc = df[df.event_type == "CYCLE"]
    flagged = df[df.anomaly_flag == 1]
    r0 = cyc.iloc[0]
    p, s = nice(r0.primary_name), nice(r0.secondary_name)
    pu, su = r0.primary_unit, r0.secondary_unit
    title = STATIONS.get(table, {}).get("title", table.upper())

    fig = plt.figure(figsize=(15, 9.2))
    gs = GridSpec(2, 3, figure=fig, width_ratios=[1.15, 1.15, 0.9], hspace=0.42, wspace=0.32,
                  left=0.06, right=0.95, top=0.86, bottom=0.07)
    fig.text(0.06, 0.95, title, fontsize=15, fontweight="bold", color=INK)
    fig.text(0.06, 0.915,
             f"{len(cyc):,} cycles  |  {df.ts.min():%d %b} - {df.ts.max():%d %b %Y}  |  "
             f"{len(flagged):,} rows flagged as noisy or ambiguous ({len(flagged) / len(df):.1%})",
             fontsize=10, color=INK_2)

    # (a) primary signal over time, with spec limits --------------------------------
    ax = fig.add_subplot(gs[0, :2])
    shown = ("pattern", "sensor", "label_conflict")
    rest = cyc[~cyc.anomaly_type.isin(shown)]
    normal_dots(ax, rest.ts, rest.primary_value)
    for kind in shown:
        f = cyc[(cyc.anomaly_type == kind) & cyc.primary_value.notna()]
        if len(f):
            flag_dots(ax, f.ts, f.primary_value, kind)
    ref_line(ax, r0.primary_usl, f"USL {r0.primary_usl:g}")
    ref_line(ax, r0.primary_lsl, f"LSL {r0.primary_lsl:g}")
    ax.set_title(f"{p.capitalize()} over time ({pu})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
    ax.legend(loc="lower right", frameon=False, fontsize=8.5, ncol=3, markerscale=1.1,
              bbox_to_anchor=(1.0, 1.0), labelcolor=INK_2, handletextpad=0.2, columnspacing=1.2)

    # (d) what was flagged — bar per type, doubles as the colour key -----------------
    ax = fig.add_subplot(gs[0, 2])
    counts = flagged.anomaly_type.value_counts()
    kinds = list(TYPES)[::-1]
    vals = [int(counts.get(k, 0)) for k in kinds]
    ax.barh(range(len(kinds)), vals, height=0.42, color=[TYPES[k][0] for k in kinds])
    for i, (k, v) in enumerate(zip(kinds, vals)):
        ax.text(v, i, f"  {v}", va="center", ha="left", color=INK, fontsize=9)
        ax.text(0, i + 0.28, f"{TYPES[k][1]}  ·  {TYPES[k][2]}", va="bottom", ha="left",
                color=INK_2, fontsize=8.5)
    ax.set_yticks([])
    ax.set_ylim(-0.5, len(kinds) - 0.1)
    ax.spines["left"].set_visible(False)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_xlim(0, max(vals + [1]) * 1.22)
    ax.set_title("What was flagged (rows)")
    ax.tick_params(axis="y", length=0)
    ax.set_axisbelow(True)

    # (b) the station's pattern: how secondary follows primary --------------------
    ax = fig.add_subplot(gs[1, 0])
    good = cyc[(cyc.anomaly_type != "pattern") & cyc.primary_value.notna()]
    normal_dots(ax, good.primary_value, good.secondary_value)
    clean = good[good.anomaly_flag != 1]
    slope, icpt = np.polyfit(clean.primary_value, clean.secondary_value, 1)
    xs = np.array([r0.primary_lsl, r0.primary_usl])
    ax.plot(xs, icpt + slope * xs, color=INK_2, lw=1.2, zorder=2)
    f = cyc[(cyc.anomaly_type == "pattern") & cyc.primary_value.notna()]
    flag_dots(ax, f.primary_value, f.secondary_value, "pattern")
    ax.set_xlim(r0.primary_lsl, r0.primary_usl)
    ax.set_ylim(r0.secondary_lsl, r0.secondary_usl)
    ax.set_xlabel(f"{p} ({pu})")
    ax.set_ylabel(f"{s} ({su})")
    ax.grid(axis="x", visible=True)
    ax.set_title(f"Normal pattern: {s} follows {p}")
    ax.text(0.02, 0.03, "axes = spec limits; everything shown is in spec", transform=ax.transAxes,
            color=MUTED, fontsize=8)

    # (c) cycle time over time -----------------------------------------------------
    ax = fig.add_subplot(gs[1, 1:])
    rest = cyc[cyc.anomaly_type != "unlogged_event"]
    normal_dots(ax, rest.ts, rest.cycle_time_s)
    f = cyc[cyc.anomaly_type == "unlogged_event"]
    flag_dots(ax, f.ts, f.cycle_time_s, "unlogged_event")
    med = cyc.cycle_time_s.median()
    ref_line(ax, med, f"normal {med:.0f}s")
    top = max(f.cycle_time_s.max() if len(f) else 0, med * 2) * 1.1
    above = rest[rest.cycle_time_s > top]
    ax.set_ylim(0, top)
    if len(above):
        n_andon = int((above.get("andon_pulled", pd.Series(0, index=above.index)).fillna(0) == 1).sum())
        note = f"{len(above)} longer cycles above the axis"
        if n_andon:
            note += f" ({n_andon} with an andon call, which explains them)"
        ax.text(0.99, 0.98, note, transform=ax.transAxes, ha="right", va="top", color=MUTED, fontsize=8)
    ax.set_title("Cycle time (s) - slow or fast cycles nothing in the log explains")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))

    CHARTS_DIR.mkdir(exist_ok=True)
    out = CHARTS_DIR / f"{table}.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"{table}: chart -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station")
    args = ap.parse_args()
    con = connect()
    for t in ([args.station] if args.station else station_tables(con)):
        plot_station(con, t)
    con.close()


if __name__ == "__main__":
    main()
