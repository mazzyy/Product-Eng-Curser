// Capacity - the answer for planning: can we hit 7,500, what the line can still produce, where capacity goes.
import { $, esc, fmt, icon, animateIn, stagger } from "../util.js";
import { columns, waterfall } from "../charts.js";

export async function render(root, params, ctx) {
  const c = await ctx.api.get("/api/capacity");
  const s = c.summary, target = +s.target_per_week, cap = +s.capacity, built = +s.cars_built;
  const proj = +s.projected_next_week, five = +s.five_day_equivalent;
  const top = c.losses.slice(0, 5), rest = c.losses.slice(5).reduce((a, x) => a + x.lost_cars, 0);
  const items = [...top.map((x) => ({ label: x.title.split(" - ")[0], v: Math.round(x.lost_cars), cause: x.cause })),
    ...(rest > 1 ? [{ label: "other problems", v: Math.round(rest), cause: "unclear" }] : [])];
  const gap = cap - items.reduce((a, x) => a + x.v, 0) - built;
  if (gap >= 1) items.push({ label: "unexplained", v: Math.round(gap), cause: "unclear" });
  const msg = `Planning update: the line built ${fmt.n(built)} cars this week (${s.attainment} of ${fmt.n(target)}). Capacity is ${fmt.n(cap)} cars/week with ST012 as bottleneck (${s.bottleneck_cycle_s} s/car). Next week we expect about ${fmt.n(proj)} cars (${s.projected_attainment}); on a 5-day week about ${fmt.n(five)} (${fmt.pct(five / target)}).`;
  const maxLoss = Math.max(...c.by_cause.map((x) => x.lost_cars), 1);

  root.innerHTML = `
    <div class="banner good">
      <div class="ic" style="color:var(--good)">${icon("good")}</div>
      <div><div class="small muted">Can we hit 7,500 next week?</div><h2>Yes - about ${fmt.n(proj)} cars expected (${esc(s.projected_attainment)})</h2>
        <div class="t2">On a 5-day week it is tighter: ${fmt.n(five)} cars (${fmt.pct(five / target)}). ST012 sets the pace at ${esc(s.bottleneck_cycle_s)} s per car.</div></div>
      <div class="stack" style="gap:6px;justify-items:end"><button class="btn sm primary" id="copy">${icon("copy")} Copy update for planning</button>
        <button class="btn sm" data-ask="Can we still hit 7,500 cars next week, and what costs us the most capacity?">${icon("spark")} Ask copilot</button></div>
    </div>
    <div class="grid g4" style="margin-top:16px" id="ck">
      <div class="card kpi"><div class="kpi-l">Built this week</div><div class="kpi-v"><span data-count="${built}">0</span></div><div class="kpi-s">${esc(s.attainment)} of target</div></div>
      <div class="card kpi"><div class="kpi-l">Capacity</div><div class="kpi-v"><span data-count="${cap}">0</span></div><div class="kpi-s">cars / week at the bottleneck pace</div></div>
      <div class="card kpi"><div class="kpi-l">Lost to ranked problems</div><div class="kpi-v" style="color:var(--serious)"><span data-count="${+s.lost_cars_ranked}">0</span></div><div class="kpi-s">cars this week</div></div>
      <div class="card kpi"><div class="kpi-l">5-day week equivalent</div><div class="kpi-v"><span data-count="${five}">0</span></div><div class="kpi-s">${fmt.pct(five / target)} of target</div></div>
    </div>
    <div class="split" style="margin-top:16px">
      <div class="card chart"><div class="card-h"><h3>Cars per day</h3><span class="hint">end of line (ST013) · dashed = 7,500 / 7 days</span></div>
        ${columns(c.cars_per_day, { value: "cars", label: "label", target: target / 7, targetLabel: "", color: "var(--accent)", unit: "cars" })}</div>
      <div class="card"><div class="card-h"><h3>Where capacity goes, by cause</h3><span class="hint">lost cars</span></div>
        ${c.by_cause.map((x) => `<div class="hbar"><span class="cause c-${esc(x.cause)}">${esc(x.cause)}</span><div class="b"><i data-w="${(x.lost_cars / maxLoss) * 100}%" style="background:var(--c-${esc(x.cause)}, var(--text-2))"></i></div><span class="num small">${fmt.n(x.lost_cars)}</span></div>`).join("")}
        <div class="sep"></div>
        <div class="small muted">Station pace (median cycle)</div>
        ${Object.entries(c.stations).map(([k, v]) => `<div class="hbar"><span>${k.toUpperCase()}</span><div class="b"><i data-w="${(v.ct_med / 55) * 100}%" style="background:${k === s.bottleneck ? "var(--serious)" : "var(--text-2)"}"></i></div><span class="num small">${fmt.n(v.ct_med, 1)} s</span></div>`).join("")}
        <div class="small t2">${esc(String(s.bottleneck).toUpperCase())} is the bottleneck - a minute lost there is a minute lost for the line; ST013 has slack.</div></div>
    </div>
    <div class="card chart" style="margin-top:16px"><div class="card-h"><h3>From capacity to cars built</h3><span class="hint">the biggest losses, from the impact ranker${gap < 0 ? ` · the ranked losses are about ${fmt.n(-gap)} cars (${fmt.pct(-gap / +s.lost_cars_ranked)}) higher than the real gap - good enough to rank with` : ""}</span></div>
      ${waterfall(items, { w: 1100, h: 260, start: cap, end: built, startLabel: "capacity", endLabel: "built" })}</div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>Capacity losses</h3></div>
      <table class="tbl"><tr><th>#</th><th>problem</th><th>cause</th><th>status</th><th class="n">lost cars</th></tr>
      ${c.losses.map((x) => `<tr><td>${x.rank}</td><td>${esc(x.title)}</td><td>${x.cause ? `<span class="cause c-${esc(x.cause)}">${esc(x.cause)}</span>` : ""}</td><td><span class="pill">${esc(x.status)}</span></td><td class="n num">${fmt.n(x.lost_cars, 1)}</td></tr>`).join("")}</table></div>`;
  stagger(root); stagger($("#ck", root)); animateIn(root);
  $("#copy", root).onclick = async () => {
    try { await navigator.clipboard.writeText(msg); ctx.toast({ severity: "good", title: "Copied for planning", detail: msg.slice(0, 120) + "…", timeout: 3500 }); }
    catch (e) { ctx.toast({ severity: "info", title: "Planning update", detail: msg, timeout: 10000 }); }
  };
}
