// Contain - for each problem case: stop or not, which cars wait for a check, which move on, and where they are.
import { $, $$, esc, fmt, icon, parse, animateIn, stagger, download } from "../util.js";

const DISP = [["REWORK", "Rework", "rejected or out of spec"], ["CHECK", "Check", "suspect - check before release"],
  ["HOLD", "Hold", "waits for the sample audit"], ["RELEASE", "Release", "moves on"]];

function caseCard(c, sel) {
  const risk = c.action !== "NO HOLD";
  return `<div class="case-c ${c.id === sel ? "sel" : ""}" data-sel="${c.case_id}" style="--cc:var(--${c.action_sev === "info" ? "faint" : c.action_sev})">
    <div class="row"><span class="pill ${c.action_sev} ${c.action === "STOP" ? "solid" : ""}">${icon(c.action === "STOP" ? "stop" : "shield")} ${esc(c.action_label)}</span>
      <span class="sp"></span><span class="small muted">${c.station.toUpperCase()} · ${fmt.dt(c.decision_ts)}</span></div>
    <div class="strong">${esc(c.culprit)}</div>
    ${risk ? `<div class="small t2">${fmt.n(c.cars_in_scope)} cars in scope · <b style="color:var(--serious)">${fmt.n(c.check)}</b> to check · ${fmt.n(c.hold)} on hold · ${fmt.n(c.rework)} rework</div>`
      : `<div class="small muted">${esc(c.cause)} / ${esc(c.family)} - no product risk</div>`}</div>`;
}

async function renderCase(el, id, ctx) {
  el.innerHTML = `<div class="skel" style="height:460px"></div>`;
  const { case: c, cars } = await ctx.api.get(`/api/containment/${id}`);
  const w0 = +parse(c.window_start), w1 = +parse(c.window_end), dt = +parse(c.decision_ts);
  const lo = Math.min(w0, dt), hi = Math.max(w1, dt), pad = (hi - lo) * 0.12 || 3600e3;
  const pos = (t) => (((t - (lo - pad)) / (hi - lo + 2 * pad)) * 100).toFixed(2) + "%";
  const counts = { REWORK: c.rework, CHECK: c.check, HOLD: c.hold, RELEASE: c.release };
  const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0) || 1;
  const where = {}; cars.forEach((x) => (where[x.where_now] = (where[x.where_now] || 0) + 1));
  const stop = c.action === "STOP";
  el.innerHTML = `
    <div class="banner ${c.action_sev}">
      <div class="ic" style="color:var(--${c.action_sev})">${icon(stop ? "stop" : "shield")}</div>
      <div><div class="small muted">case ${c.case_id} · ${esc(c.cause)} / ${esc(c.family)} · ${c.station.toUpperCase()} (severity ${c.severity}) · decided ${fmt.dt(c.decision_ts)}</div>
        <h2>${esc(c.action_label)} - ${esc(c.culprit)}</h2><div class="t2" style="margin-top:3px">${esc(c.why)}</div></div>
      <div class="stack" style="gap:6px;justify-items:end">
        ${stop ? `<span class="pill">${icon("users")} Supervisor decides the stop</span>` : ""}
        ${c.check + c.hold ? `<span class="pill">${icon("good")} Quality releases held cars</span>` : ""}
        <button class="btn sm" data-ask="Should we stop for case ${c.case_id} (${esc(c.culprit)}) and which cars have to wait for a check?">${icon("spark")} Ask copilot</button>
        <button class="btn sm ghost" data-go="map" data-case="${c.case_id}">${icon("pin")} On the map</button>
      </div>
    </div>
    ${c.action !== "NO HOLD" ? `
    <div class="card"><div class="card-h"><h3>Suspect window</h3><span class="hint">${esc(c.basis)}</span></div>
      <div class="window"><div class="w-rail"></div>
        <div class="w-span" style="left:${pos(w0)};width:calc(${pos(w1)} - ${pos(w0)})"></div>
        <div class="w-lab" style="left:${pos(w0)};transform:none">${esc(c.cause === "machine" && c.family === "pattern" ? "last good check" : "from")} ${fmt.dt(c.window_start)}</div>
        <div class="w-lab" style="left:${pos(w1)};transform:translateX(-100%)">${esc(c.cause === "machine" ? "repaired" : "until")} ${fmt.dt(c.window_end)}</div>
        <div class="w-mark" style="left:${pos(dt)}"></div><div class="w-lab b" style="left:${pos(dt)};color:var(--text);font-weight:650">decision ${fmt.dt(c.decision_ts)}</div>
      </div>
      <div class="row small t2" style="margin-top:10px">${fmt.n(c.window_h, 0)} h · ${fmt.n(c.cars_in_scope)} cars in scope</div>
      ${c.prevent && c.prevent !== "-" ? `<div class="banner good" style="margin-top:12px;grid-template-columns:34px 1fr;padding:10px 12px">
        <div class="ic" style="width:34px;height:34px;color:var(--good)">${icon("wrench")}</div><div class="small"><b>Prevention:</b> ${esc(c.prevent)}</div></div>` : ""}
    </div>
    <div class="card"><div class="card-h"><h3>Every car in scope</h3><span class="hint">${c.audit_sample ? `sample audit: ${c.audit_sample} of the ${fmt.n(c.hold)} held cars` : ""}</span></div>
      <div class="stackbar">${DISP.map(([k]) => counts[k] ? `<div class="d-${k}" style="flex:${counts[k]}" data-tip="<b>${k}</b> ${fmt.n(counts[k])} cars">${counts[k] / total > 0.06 ? fmt.n(counts[k]) : ""}</div>` : "").join("")}</div>
      <div class="legend" style="margin-top:10px">${DISP.map(([k, l, d]) => `<span><span class="sw d-${k}"></span><b>${l}</b> ${fmt.n(counts[k])} <span class="muted">- ${d}</span></span>`).join("")}</div>
      <div class="row" style="margin-top:12px">${Object.entries(where).map(([k, v]) => `<span class="pill ${k.startsWith("shipped") ? "critical" : k.startsWith("built after") ? "info" : ""}">${icon(k.startsWith("shipped") ? "serious" : "factory")} ${fmt.n(v)} ${esc(k)}</span>`).join("")}</div>
    </div>
    <div class="card"><div class="card-h"><h3>Hold list</h3><span class="hint">${fmt.n(cars.length)} cars that do not move on yet</span><span class="sp"></span>
        <button class="btn sm" id="csv">${icon("download")} Export CSV</button></div>
      <div class="row" style="margin-bottom:10px"><div class="chips" id="df">${["All", "REWORK", "CHECK", "HOLD"].map((k, i) => `<button class="chip ${i ? "" : "on"}" data-d="${k}">${k === "All" ? "All" : k.toLowerCase()} <span class="n">${k === "All" ? cars.length : cars.filter((x) => x.disposition === k).length}</span></button>`).join("")}</div>
        <div class="sp"></div><input id="vin" placeholder="search VIN…" style="height:30px;border-radius:8px;border:1px solid var(--border);background:var(--surface-2);padding:0 10px;width:200px"></div>
      <div class="scroll" id="cars"></div></div>` : `<div class="card empty">${icon("good")} No product risk - this is a delivery problem (time, not quality). No car needs to wait.</div>`}`;
  stagger(el); animateIn(el);
  if (c.action === "NO HOLD") return;
  let df = "All", q = "", limit = 150;
  const drawCars = () => {
    const rows = cars.filter((x) => (df === "All" || x.disposition === df) && (!q || String(x.vin).toLowerCase().includes(q)));
    $("#cars", el).innerHTML = `<table class="tbl"><tr><th>VIN</th><th>built</th><th>disposition</th><th>why</th><th>where now</th></tr>
      ${rows.slice(0, limit).map((x) => `<tr><td class="mono">${esc(x.vin)}${x.vin_inferred ? ` <span class="pill warning" data-tip="no scan - inferred from the neighbouring car">inferred</span>` : ""}</td>
        <td>${fmt.dt(x.ts)}</td><td><span class="pill ${x.disposition === "REWORK" ? "critical" : x.disposition === "CHECK" ? "serious" : "warning"}">${esc(x.disposition.toLowerCase())}</span></td>
        <td class="small t2">${esc(x.reason)}</td><td class="small">${esc(x.where_now)}</td></tr>`).join("")}</table>
      ${rows.length > limit ? `<div class="empty"><button class="btn sm" id="more">Show all ${fmt.n(rows.length)}</button></div>` : ""}`;
    const m = $("#more", el); if (m) m.onclick = () => { limit = 1e9; drawCars(); };
  };
  drawCars();
  $("#df", el).onclick = (e) => { const b = e.target.closest("[data-d]"); if (!b) return; df = b.dataset.d; limit = 150;
    $$("#df .chip", el).forEach((x) => x.classList.toggle("on", x === b)); drawCars(); };
  $("#vin", el).oninput = (e) => { q = e.target.value.trim().toLowerCase(); drawCars(); };
  $("#csv", el).onclick = () => {
    const lines = ["vin,vin_inferred,station,built,disposition,reason,where_now",
      ...cars.map((x) => [x.vin, x.vin_inferred, x.station, x.ts, x.disposition, `"${String(x.reason).replace(/"/g, "'")}"`, `"${x.where_now}"`].join(","))];
    download(`hold-list-case-${c.case_id}.csv`, lines.join("\n"));
    ctx.toast({ severity: "good", title: "Hold list exported", detail: `${cars.length} cars - hand it to quality`, timeout: 3000 });
  };
}

export async function render(root, params, ctx) {
  const list = await ctx.api.get("/api/containment");
  const risk = list.filter((c) => c.action !== "NO HOLD"), none = list.filter((c) => c.action === "NO HOLD");
  let sel = +(params.case || 0) || risk[0]?.case_id;
  if (!list.find((c) => c.case_id === sel)) sel = risk[0]?.case_id;
  const totals = risk.reduce((a, c) => ({ check: a.check + c.check, hold: a.hold + c.hold, rework: a.rework + c.rework }), { check: 0, hold: 0, rework: 0 });
  root.innerHTML = `
    <div class="grid g4" id="ck">
      <div class="card kpi"><div class="kpi-l">${icon("stop")} Stops recommended</div><div class="kpi-v" style="color:var(--critical)"><span data-count="${list.filter((c) => c.action === "STOP").length}">0</span></div><div class="kpi-s">the supervisor decides a stop</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("serious")} Cars to check</div><div class="kpi-v" style="color:var(--serious)"><span data-count="${totals.check}">0</span></div><div class="kpi-s">suspect - checked before release</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("clock")} Cars on hold</div><div class="kpi-v"><span data-count="${totals.hold}">0</span></div><div class="kpi-s">released when the sample audit passes</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("wrench")} Rework</div><div class="kpi-v"><span data-count="${totals.rework}">0</span></div><div class="kpi-s">rejected or out of spec</div></div>
    </div>
    <div class="split r" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>Problem cases</h3><span class="hint">decided when the cause finder found them</span></div>
        <div class="grid" style="gap:8px" id="cl">${risk.map((c) => caseCard(c, sel)).join("")}</div>
        <div class="al-g" style="margin-top:16px">No product risk · ${none.length}</div>
        <div class="grid" style="gap:8px">${none.map((c) => caseCard(c, sel)).join("")}</div></div>
      <div class="stack" id="cd"></div>
    </div>`;
  stagger($("#ck", root)); animateIn(root);
  const select = (id) => { sel = +id; $$("[data-sel]", root).forEach((x) => x.classList.toggle("sel", +x.dataset.sel === sel)); renderCase($("#cd", root), sel, ctx); };
  if (sel) select(sel);
  root.onclick = (e) => { const t = e.target.closest("[data-sel]"); if (t) select(t.dataset.sel); };
}
