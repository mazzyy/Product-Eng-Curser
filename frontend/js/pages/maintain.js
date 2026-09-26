// Maintain - how often each machine is checked or serviced (Weibull life models), what is due, and why.
import { $, $$, esc, fmt, icon, animateIn, stagger } from "../util.js";
import { survival } from "../charts.js";

const KIND = { wear: ["warning", "wears out", "replace / service on a fixed interval"],
  silent: ["critical", "silent drift", "check on a fixed interval - every car since the last check is suspect"],
  random: ["info", "random", "no interval helps - keep a spare, watch the signal"] };

function due(p) {
  if (p.overdue) return `<span class="pill critical">${icon("critical")} overdue</span>`;
  if (p.due_in_h == null) return `<span class="muted">-</span>`;
  return `<span class="pill ${p.due_in_h <= 24 ? "warning" : ""}">${icon("clock")} in ${fmt.hours(p.due_in_h)}</span>`;
}

export async function render(root, params, ctx) {
  const m = await ctx.api.get("/api/maintenance");
  const plan = m.plan;
  const now = plan.reduce((a, p) => a + p.cost_now_per_year, 0), rec = plan.reduce((a, p) => a + p.cost_rec_per_year, 0);
  const silent = plan.filter((p) => p.kind === "silent");
  let sel = plan.findIndex((p) => params.eq && p.equipment.includes(params.eq) && p.kind !== "random");
  if (sel < 0) sel = plan.findIndex((p) => p.kind === "silent");

  root.innerHTML = `
    <div class="grid g4" id="mk">
      <div class="card kpi"><div class="kpi-l">${icon("wrench")} Maintenance + failure work</div>
        <div class="kpi-v"><span data-count="${Math.round(rec / 60)}">0</span><small>h / year</small></div>
        <div class="kpi-s">today ${fmt.n(now / 60)} h → <b style="color:var(--good)">${fmt.pct((rec - now) / now)}</b> with the new plan</div></div>
      <div class="card kpi ${plan.some((p) => p.overdue) ? "alert" : ""}"><div class="kpi-l">${icon("critical")} Overdue</div>
        <div class="kpi-v" style="color:var(--serious)"><span data-count="${plan.filter((p) => p.overdue).length}">0</span></div>
        <div class="kpi-s">${esc(plan.filter((p) => p.overdue).map((p) => p.equipment).join(", ") || "none")} - next maintenance window</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("clock")} Due in 24 h</div>
        <div class="kpi-v"><span data-count="${plan.filter((p) => p.due_in_h != null && p.due_in_h <= 24).length}">0</span></div>
        <div class="kpi-s">${esc(plan.filter((p) => p.due_in_h != null && p.due_in_h <= 24).map((p) => p.equipment).join(", ") || "none")}</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("shield")} Worst-case suspect window</div>
        <div class="kpi-v"><span data-count="${silent[0]?.window_rec_h || 0}">0</span><small>h (was ${fmt.n(silent[0]?.window_now_h)} h)</small></div>
        <div class="kpi-s">calibration checks every shift → ${fmt.n(silent[0]?.cars_at_risk_rec)} instead of ${fmt.n(silent[0]?.cars_at_risk_now)} cars per drift</div></div>
    </div>
    <div class="split" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>Maintenance plan</h3><span class="hint">fitted from 2 years of fleet history (12 identical units per type) + this week's repairs</span></div>
        <table class="tbl" id="mt"><tr><th>equipment · failure mode</th><th>kind</th><th>today → recommended</th><th>next due</th><th>7-day risk</th></tr>
        ${plan.map((p, i) => `<tr data-i="${i}" style="cursor:pointer" class="${i === sel ? "sel" : ""}">
          <td><b>${esc(p.equipment)}</b><div class="small muted">${esc(p.failure_mode)}</div></td>
          <td><span class="pill ${KIND[p.kind][0]}">${KIND[p.kind][1]}</span></td>
          <td><span class="small muted">${esc(p.policy_now)}</span><div>${icon("arrow")} <b>${esc(p.policy_rec)}</b></div></td>
          <td>${due(p)}</td>
          <td style="min-width:110px"><div class="row" style="gap:6px"><div class="meter" style="flex:1;margin:0"><i data-w="${Math.min(100, p.p_fail_7d * 300)}%" style="background:${p.p_fail_7d >= 0.1 ? "var(--serious)" : "var(--text-2)"}"></i></div><span class="num small">${fmt.pct(p.p_fail_7d)}</span></div></td>
        </tr>`).join("")}</table></div>
      <div class="stack" id="md"></div>
    </div>`;
  stagger($("#mk", root)); animateIn(root);

  const draw = () => {
    const p = plan[sel];
    $$("#mt tr[data-i]", root).forEach((r) => r.classList.toggle("sel", +r.dataset.i === sel));
    $("#md", root).innerHTML = `
      <div class="card"><div class="row"><span class="pill ${KIND[p.kind][0]}">${KIND[p.kind][1]}</span><span class="small muted">${esc(KIND[p.kind][2])}</span></div>
        <h2 style="margin:8px 0 2px;font-size:20px">${esc(p.equipment)}</h2><div class="t2">${esc(p.failure_mode)}</div>
        <div class="chart" style="margin-top:12px">${survival(p.curve, { age: p.age_now_h, interval: p.interval_h, b10: p.b10_h })}</div>
        <div class="small t2" style="margin-top:6px"><b>Why:</b> ${esc(p.why)}</div>
        <dl class="kv" style="margin-top:12px">
          <dt>life model</dt><dd>Weibull shape <b>${fmt.n(p.shape, 2)}</b> (simulator's true ${fmt.n(p.true_shape, 1)}), mean life ${fmt.n(p.mttf_h / 24, 0)} days, B10 ${fmt.hours(p.b10_h)}</dd>
          <dt>history</dt><dd>${fmt.n(p.failures_seen)} failures in ${fmt.n(p.runs)} runs · last renewal ${fmt.dt(p.last_renewal)}</dd>
          <dt>age now</dt><dd>${fmt.hours(p.age_now_h)} · ${fmt.pct(p.p_fail_7d)} chance of failure in the next 7 days</dd>
          <dt>next due</dt><dd>${esc(p.next_due || "-")}</dd>
          <dt>cost per year</dt><dd>${fmt.n(p.cost_now_per_year)} → <b>${fmt.n(p.cost_rec_per_year)}</b> labour min</dd></dl>
        ${p.kind === "silent" ? `<div class="sep"></div><div class="small muted" style="margin-bottom:6px">Cars built between a drift and the check that finds it - all suspect</div>
          <div class="hbar"><span>check ${esc(String(p.policy_now).split(" ").pop())}</span><div class="b"><i data-w="100%" style="background:var(--serious)"></i></div><span class="num small">${fmt.n(p.cars_at_risk_now)}</span></div>
          <div class="hbar"><span>every shift</span><div class="b"><i data-w="${(p.cars_at_risk_rec / p.cars_at_risk_now) * 100}%" style="background:var(--good)"></i></div><span class="num small">${fmt.n(p.cars_at_risk_rec)}</span></div>` : ""}
        <div class="row" style="margin-top:12px"><button class="btn sm" data-ask="How often should we check ${esc(p.equipment)} (${esc(p.failure_mode)}), and why?">${icon("spark")} Ask copilot</button></div>
      </div>`;
    animateIn($("#md", root));
  };
  draw();
  $("#mt", root).onclick = (e) => { const r = e.target.closest("tr[data-i]"); if (r) { sel = +r.dataset.i; draw(); } };
}
