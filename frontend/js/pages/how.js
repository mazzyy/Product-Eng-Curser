// How it works - the workflow of all models, with this week's numbers and animated example traces.
import { $, $$, esc, icon, fmt, stagger, animateIn } from "../util.js";
import { diagram, NODES, COLS, trace, pulse, exportPng } from "../diagram.js";

const UNITS = { mes: "cycles", notes: "notes", wi: "WI versions", mlog: "entries", signal: "cars flagged", people: "findings",
  floor: "facts", method: "versions checked", cause: "incidents", impact: "ranked", contain: "cars held", change: "changes",
  maint: "failure modes", copilot: "tools", app: "alerts" };

const TRACES = [
  { k: "machine", t: "Nutrunner drifts", sev: "critical", steps: [
    ["mes", "signal", "Torque creeps up car by car - still in spec, but off the station's normal pattern"],
    ["signal", "contain", "The signal checker flags each unusual car - these cars get checked first"],
    ["mes", "cause", "Block by block the symptom does not follow the operators and stops after the repair: machine"],
    ["cause", "impact", "Safety-critical joint, 46 % of the margin used: High priority"],
    ["cause", "contain", "Containment: every car since the last good check is in scope - stop or 100 % check"],
    ["contain", "app", "The engineer sees the stop recommendation, the hold list and who decides"]] },
  { k: "method", t: "A bad WI change", sev: "serious", steps: [
    ["wi", "method", "A new WI version adds a VIN confirmation scan"],
    ["method", "change", "Method checker: TRC-1 BLOCK - two scans book every car twice"],
    ["change", "app", "The change manager holds it at the gate: it does not reach the line"],
    ["change", "wi", "Fixed version approved, signed, piloted, released - the next shift works to it"]] },
  { k: "people", t: "A tired operator", sev: "warning", steps: [
    ["mes", "people", "Hands-on time rises towards the end of the shift - for one operator only"],
    ["people", "cause", "The people model sees fatigue; the cause finder sees it follow the person across stations"],
    ["people", "impact", "Lost cars, and the hours worked tired, go into the priority"],
    ["impact", "app", "Owner: the team lead - workload, breaks, rotation. Not a blame list"]] },
  { k: "floor", t: "The floor says it first", sev: "good", steps: [
    ["notes", "floor", "\"NR-012 feels different today, watching it\" - a handover note"],
    ["floor", "cause", "GPT-5 turns it into a fact (machine / NR-012 / monitoring) and links it to the cause finder"],
    ["cause", "impact", "The data confirms it hours later - the note was the early warning"],
    ["impact", "app", "The engineer sees both: what people said and what the data proves"]] },
  { k: "maint", t: "Set a check interval", sev: "info", steps: [
    ["mlog", "maint", "Two years of repairs on 12 identical units: the Weibull fit says wear-out (shape > 1)"],
    ["maint", "app", "Plan: calibration check every shift - it caps the suspect window at 8 h of cars"]] },
  { k: "copilot", t: "Ask the copilot", sev: "info", steps: [
    ["models", "copilot", "\"Should we stop ST012?\" - the agent calls the containment and cause tools"],
    ["copilot", "app", "Answer with case and rule IDs - and who decides the stop"]] },
];

function detail(k, counts) {
  const n = NODES[k];
  const v = counts[k];
  return `<div class="row" style="gap:12px;align-items:flex-start">
      <div class="dg-ic ${n.k}">${icon(n.i)}</div>
      <div style="flex:1;min-width:0"><div class="small muted">${esc(COLS[n.c])}${n.model != null ? ` · model ${n.model}` : ""}</div>
        <h3 style="margin:2px 0 4px;font-size:17px">${esc(n.t)}</h3><div class="t2">${esc(n.d)}</div></div></div>
    <dl class="kv" style="margin:14px 0 0">
      <dt>Input</dt><dd>${esc(n.io[0])}</dd><dt>Output</dt><dd>${esc(n.io[1])}</dd>
      <dt>Runs</dt><dd>${esc(n.when)}</dd>
      ${v != null ? `<dt>This week</dt><dd><b>${fmt.n(v)}</b> ${esc(UNITS[k])}</dd>` : ""}
    </dl>
    <div class="row" style="margin-top:14px">
      <button class="btn sm" data-go="${n.page}">${icon("arrow")} Open ${esc(n.page)}</button>
      <button class="btn sm ghost" data-ask="What does the ${esc(n.t.toLowerCase())} do and what did it find this week?">${icon("spark")} Ask copilot</button>
    </div>`;
}

export async function render(root, params, ctx) {
  let counts = {};
  try { counts = await ctx.api.get("/api/flow"); } catch (e) { /* the diagram works without numbers */ }
  root.innerHTML = `
    <div class="hero"><div><h2>From a torque reading to a decision</h2>
      <p>Nine models in five layers. Data comes in on the left, the engineer decides on the right - every box shows what it produced this week.</p></div>
      <div class="sp"></div>
      <button class="btn" id="png">${icon("download")} Download PNG</button>
      <button class="btn primary" data-go="live">${icon("pulse")} See it live</button></div>
    <div class="card" style="padding:14px 14px 8px">
      <div class="row" style="margin:0 4px 10px"><span class="small muted" style="margin-right:4px">Trace a problem</span>
        <div class="chips" id="tr">${TRACES.map((t) => `<button class="chip" data-t="${t.k}">${esc(t.t)}</button>`).join("")}</div>
        <div class="sp"></div><span class="small muted">click any box for details</span></div>
      <div class="dg-wrap" id="dgw">${diagram({ counts, units: UNITS })}</div>
      <div class="narr" id="narr"><span class="muted">Pick a problem above to follow it through the models.</span></div>
    </div>
    <div class="split" style="margin-top:16px">
      <div class="card" id="nd">${detail("cause", counts)}</div>
      <div class="card"><div class="card-h"><h3>When each model runs on the live line</h3></div>
        <table class="tbl"><tr><th>Model</th><th>Runs</th><th>Produces</th></tr>
        ${Object.entries(NODES).filter(([, n]) => n.model != null).map(([k, n]) => `<tr class="click" data-n="${k}" style="cursor:pointer">
          <td><b>${esc(n.t)}</b> <span class="muted small">M${n.model}</span></td><td>${esc(n.when)}</td><td class="t2">${esc(n.io[1])}</td></tr>`).join("")}
        </table></div>
    </div>`;
  stagger(root); animateIn(root);
  const svg = $("#dg", root);
  let sel = "cause", timers = [];
  const select = (k) => {
    sel = k;
    $$(".dg .n", root).forEach((g) => g.classList.toggle("sel", g.dataset.n === k));
    $("#nd", root).innerHTML = detail(k, counts);
    $("#nd", root).animate([{ opacity: 0.4, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }], { duration: 250 });
    pulse(svg, k, "info");
  };
  select("cause");
  const clear = () => { timers.forEach(clearTimeout); timers = []; };
  ctx.cleanup.push(clear);
  const play = (t) => {
    clear();
    $$("#tr .chip", root).forEach((c) => c.classList.toggle("on", c.dataset.t === t.k));
    const step = 1500;
    trace(svg, t.steps.map((s) => [s[0], s[1]]), t.sev, 0, step);
    t.steps.forEach((s, i) => timers.push(setTimeout(() => {
      $("#narr", root).innerHTML = `<span class="pill ${t.sev === "good" ? "good" : t.sev}">${i + 1} / ${t.steps.length}</span>
        <b>${esc(NODES[s[0]]?.t || "All models")} → ${esc(NODES[s[1]].t)}</b><span class="t2">${esc(s[2])}</span>`;
    }, i * step)));
    timers.push(setTimeout(() => $$("#tr .chip", root).forEach((c) => c.classList.remove("on")), t.steps.length * step + 600));
  };
  root.onclick = (e) => {
    const n = e.target.closest("[data-n]"); if (n) { select(n.dataset.n); if (n.tagName === "TR") $("#dgw", root).scrollIntoView({ block: "center" }); return; }
    const t = e.target.closest("[data-t]"); if (t) play(TRACES.find((x) => x.k === t.dataset.t));
    if (e.target.closest("#png")) { exportPng(svg, "workflow.png"); ctx.toast({ severity: "good", title: "Diagram saved", detail: "workflow.png - in the current theme", timeout: 2500 }); }
  };
  if (params.trace) { const t = TRACES.find((x) => x.k === params.trace); if (t) setTimeout(() => play(t), 600); }
}
