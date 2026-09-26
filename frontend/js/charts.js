// Small SVG charts. One axis per chart, thin marks, rounded data ends, labels in text colours,
// identity by legend + direct labels; every mark has a data-tip for hover.
import { esc, fmt } from "./util.js";

export function sparkline(values, { w = 120, h = 34, color = "var(--accent)", fill = true } = {}) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = values.map((x, i) => [(i / (values.length - 1)) * w, h - 3 - ((x - lo) / span) * (h - 6)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `${d} L ${w} ${h} L 0 ${h} Z`;
  const last = pts[pts.length - 1];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    ${fill ? `<path d="${area}" fill="${color}" opacity=".12"/>` : ""}
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="${color}"/></svg>`;
}

// Vertical columns with an optional target line (e.g. cars per day vs the daily share of 7,500).
export function columns(data, { w = 640, h = 220, value = "v", label = "label", target = null, targetLabel = "",
  color = "var(--accent)", unit = "" } = {}) {
  const pad = { l: 40, r: 12, t: 16, b: 26 };
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const max = Math.max(target || 0, ...data.map((d) => d[value])) * 1.12 || 1;
  const bw = Math.min(46, (iw / data.length) * 0.62);
  const x = (i) => pad.l + (iw / data.length) * (i + 0.5);
  const y = (v) => pad.t + ih - (v / max) * ih;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => Math.round((max * k) / 100) * 100);
  let s = `<svg viewBox="0 0 ${w} ${h}" role="img"><g class="axis">`;
  ticks.forEach((t) => { s += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(t)}" y2="${y(t)}" stroke-dasharray="${t ? "2 4" : ""}"/><text x="${pad.l - 6}" y="${y(t) + 4}" text-anchor="end">${fmt.n(t)}</text>`; });
  s += "</g>";
  data.forEach((d, i) => {
    const v = d[value], top = y(v), bh = pad.t + ih - top;
    s += `<g class="hover-t" data-tip="<b>${esc(d[label])}</b><br>${fmt.n(v)} ${unit}">
      <rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${bh}" rx="4" fill="${color}">
        <animate attributeName="height" from="0" to="${bh}" dur=".7s" fill="freeze" calcMode="spline" keySplines=".2 .8 .2 1"/>
        <animate attributeName="y" from="${pad.t + ih}" to="${top}" dur=".7s" fill="freeze" calcMode="spline" keySplines=".2 .8 .2 1"/></rect>
      <text class="lbl" x="${x(i)}" y="${h - 8}" text-anchor="middle">${esc(d[label])}</text>
      <text class="val" x="${x(i)}" y="${top - 5}" text-anchor="middle">${fmt.n(v)}</text></g>`;
  });
  if (target) {
    s += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(target)}" y2="${y(target)}" stroke="var(--critical)" stroke-width="1.6" stroke-dasharray="6 4"/>
      <text x="${w - pad.r}" y="${y(target) - 6}" text-anchor="end" style="fill:var(--critical);font-size:11px;font-weight:650">${esc(targetLabel)}</text>`;
  }
  return s + "</svg>";
}

// Reliability curve R(t) for the maintenance predictor, with "age now" and the recommended interval.
export function survival(curve, { w = 420, h = 210, age = null, interval = null, b10 = null } = {}) {
  const pad = { l: 40, r: 10, t: 22, b: 26 };
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const tmax = curve[curve.length - 1].t;
  const x = (t) => pad.l + (Math.min(t, tmax) / tmax) * iw, y = (r) => pad.t + (1 - r) * ih;
  const d = curve.map((p, i) => (i ? "L" : "M") + x(p.t).toFixed(1) + " " + y(p.r).toFixed(1)).join(" ");
  const days = tmax / 24, step = days > 120 ? 30 : days > 40 ? 10 : days > 14 ? 7 : 1;
  let s = `<svg viewBox="0 0 ${w} ${h}"><g class="axis">`;
  [0, 0.25, 0.5, 0.75, 1].forEach((r) => { s += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(r)}" y2="${y(r)}" stroke-dasharray="${r ? "2 4" : ""}"/><text x="${pad.l - 6}" y="${y(r) + 4}" text-anchor="end">${r * 100}%</text>`; });
  for (let dd = 0; dd <= days; dd += step) s += `<text x="${x(dd * 24)}" y="${h - 10}" text-anchor="middle">${dd}d</text>`;
  s += `</g><path d="${d} L ${x(tmax)} ${y(0)} L ${x(0)} ${y(0)} Z" fill="var(--accent)" opacity=".10"/>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-dasharray="1400" stroke-dashoffset="1400">
      <animate attributeName="stroke-dashoffset" from="1400" to="0" dur="1.1s" fill="freeze"/></path>`;
  if (interval) s += `<line x1="${x(interval)}" x2="${x(interval)}" y1="${pad.t}" y2="${pad.t + ih}" stroke="var(--good)" stroke-width="2"/>
    <text x="${x(interval) + 5}" y="${pad.t + 26}" style="fill:var(--good);font-size:11px;font-weight:650">every ${fmt.hours(interval)}</text>`;
  if (b10) s += `<circle cx="${x(b10)}" cy="${y(0.9)}" r="4" fill="var(--warning)" data-tip="B10 life: 10% have failed by ${fmt.hours(b10)}"/>`;
  if (age != null) s += `<line x1="${x(age)}" x2="${x(age)}" y1="${pad.t}" y2="${pad.t + ih}" stroke="var(--text)" stroke-width="1.5" stroke-dasharray="3 3"/>
    <text x="${x(age) + 5}" y="${pad.t + ih - 8}" style="fill:var(--text);font-size:11px">now: ${fmt.hours(age)} old</text>`;
  s += `<text x="${pad.l}" y="${pad.t - 9}" style="fill:var(--muted);font-size:11px">chance it is still running after …</text>`;
  return s + "</svg>";
}

// Capacity waterfall: capacity -> losses -> built.
export function waterfall(items, { w = 640, h = 250, start, end, startLabel, endLabel } = {}) {
  const pad = { l: 46, r: 12, t: 18, b: 44 };
  const all = [{ label: startLabel, v: start, kind: "total" }, ...items.map((d) => ({ ...d, kind: "loss" })), { label: endLabel, v: end, kind: "total" }];
  const lo = Math.min(end, start - items.reduce((a, d) => a + d.v, 0)) * 0.94, hi = start * 1.02;
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b, bw = (iw / all.length) * 0.62;
  const x = (i) => pad.l + (iw / all.length) * (i + 0.5), y = (v) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;
  let s = `<svg viewBox="0 0 ${w} ${h}"><g class="axis">`;
  for (let k = 0; k <= 4; k++) { const v = lo + ((hi - lo) * k) / 4; s += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke-dasharray="2 4"/><text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end">${fmt.n(Math.round(v / 100) * 100)}</text>`; }
  s += "</g>";
  let level = start;
  all.forEach((d, i) => {
    let top, bot, fill;
    if (d.kind === "total") { top = y(d.v); bot = y(lo); fill = i === 0 ? "var(--text-2)" : "var(--accent)"; }
    else { top = y(level); bot = y(level - d.v); level -= d.v; fill = `var(--c-${d.cause || "unclear"})`; }
    const lab = String(d.label).length > 16 ? String(d.label).slice(0, 15) + "…" : d.label;
    s += `<g class="hover-t" data-tip="<b>${esc(d.label)}</b><br>${d.kind === "loss" ? "−" : ""}${fmt.n(d.v)} cars">
      <rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(2, bot - top)}" rx="3" fill="${fill}" style="animation:fadeUp .5s ${i * 60}ms both"/>
      <text class="val" x="${x(i)}" y="${top - 5}" text-anchor="middle">${d.kind === "loss" ? "−" : ""}${fmt.n(d.v)}</text>
      <text class="lbl" x="${x(i)}" y="${h - 26}" text-anchor="middle">${esc(lab)}</text></g>`;
  });
  return s + "</svg>";
}
