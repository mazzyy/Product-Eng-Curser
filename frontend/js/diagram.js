// The workflow diagram: data from the line -> detect -> understand -> decide & act -> the engineer.
// One SVG used twice: "How it works" (static, with this week's numbers) and "Live line" (packets + pulses).
import { esc, icon, fmt } from "./util.js";

const W = 196, HN = 64;
const COL = [16, 250, 484, 718, 962];
export const COLS = ["1 · The line", "2 · Detect", "3 · Understand", "4 · Decide & act", "5 · The engineer"];

export const NODES = {
  mes:     { c: 0, y: 145, t: "Station data", s: "cycles, faults, repairs", i: "factory", k: "src", page: "investigate",
             d: "ST012 bolt-down and ST013 coolant fill & leak test. Every car: torque/angle or leak/fill values, cycle time, operator, WI version, part batch, faults, andon calls.",
             io: ["simulator (model 0) - one week, 2 stations", "22,000 records a week"], when: "every cycle (~50 s)" },
  notes:   { c: 0, y: 315, t: "Floor notes", s: "handovers, maint. log", i: "notes", k: "src", page: "notes",
             d: "What people write at shift handover and in the maintenance log - short, mixed German/English, abbreviations.",
             io: ["team leads, maintenance, supervisors", "free text"], when: "end of each shift" },
  wi:      { c: 0, y: 415, t: "Work instructions", s: "WI versions, sign-offs", i: "file", k: "src", page: "change",
             d: "The method: steps, times, tools, torques, who is qualified and who signed which version.",
             io: ["process engineering", "steps + qualifications"], when: "when a change is proposed" },
  mlog:    { c: 0, y: 515, t: "Maintenance history", s: "2 years, 12 units", i: "wrench", k: "src", page: "maintain",
             d: "Failures and repairs of every equipment type in the hall - the fleet history the Weibull fit learns from.",
             io: ["CMMS", "failures + censored runs"], when: "on every repair" },
  signal:  { c: 1, y: 100, t: "Signal checker", s: "rules + ML, every car", i: "search", k: "det", model: 1, page: "investigate",
             d: "Is this car's data normal? Hard rules (out of spec but OK, double VIN, stuck sensor) plus a model of the station's normal torque/angle pattern.",
             io: ["each cycle", "flag + reason per car"], when: "every cycle" },
  people:  { c: 1, y: 190, t: "People model", s: "per operator & shift", i: "users", k: "det", model: "1b", page: "investigate",
             d: "Pace, re-hits, technique offset, fatigue, andon support gaps - per operator and shift, compared with peers. Separates the person from the support around them.",
             io: ["cycles by operator", "findings per shift"], when: "every shift end" },
  floor:   { c: 1, y: 315, t: "Floor listener", s: "GPT-5: notes → facts", i: "spark", k: "det", model: 4, page: "notes",
             d: "Turns notes into facts (station, equipment, symptom, status) and links them to what the data shows - agrees, early warning or notes only.",
             io: ["note text", "facts + links"], when: "when a note is written" },
  method:  { c: 1, y: 415, t: "Method checker", s: "15 rules at 50 s takt", i: "clipboard", k: "det", model: 5, page: "change",
             d: "Does a WI version fit takt, stay safe, keep traceability, and can the crews do it? Checks design before go-live and reality after one shift.",
             io: ["WI steps + qualifications", "BLOCK / WARN / PASS"], when: "before go-live, one shift after" },
  cause:   { c: 2, y: 145, t: "Cause finder", s: "which of 4 causes?", i: "search", k: "und", model: 2, page: "investigate",
             d: "Cuts the week into 2-hour blocks, finds symptoms, and asks: does it follow the person, start with a WI, stop after a repair, live with a batch? A random forest names the cause and culprit.",
             io: ["all blocks so far", "incidents + cases"], when: "every 2-hour block" },
  impact:  { c: 2, y: 265, t: "Impact ranker", s: "safety, cars, cost", i: "gauge", k: "und", model: 3, page: "today",
             d: "Scores every problem on safety & quality, lost cars, rework cost and people - with hard rules that force High. The priority list.",
             io: ["cases + flags + findings", "High / Medium / Low"], when: "with every new case" },
  contain: { c: 3, y: 145, t: "Containment", s: "stop? which cars wait", i: "shield", k: "dec", model: 7, page: "contain",
             d: "For each case: the suspect window, every car in scope sorted into rework / check / hold / release, and whether to recommend a stop.",
             io: ["case + cars", "action + hold list"], when: "when a case is confirmed" },
  change:  { c: 3, y: 415, t: "Change manager", s: "gates → release", i: "clipboard", k: "dec", model: 6, page: "change",
             d: "Gates: method check, approvals by role, operator sign-off, pilot on one crew, release. The next shift always gets the current method.",
             io: ["proposed WI", "released version"], when: "on every change" },
  maint:   { c: 3, y: 515, t: "Maintenance plan", s: "Weibull → interval", i: "wrench", k: "dec", model: 8, page: "maintain",
             d: "Fits failure curves per equipment and failure mode; wear-out gets a fixed interval, random failures get a spare and the signal checker.",
             io: ["repair history", "interval + next due"], when: "on every repair" },
  app:     { c: 4, y: 265, t: "Engineer", s: "alerts, priorities", i: "today", k: "eng", page: "today",
             d: "One place for the production engineer: what needs attention now, why, who decides, and what to do next.",
             io: ["everything above", "decisions"], when: "always" },
  copilot: { c: 4, y: 415, t: "Copilot agent", s: "GPT-5, 13 tools", i: "spark", k: "eng", model: 9, page: "today",
             d: "Answers questions from the models - with incident, case, change and rule IDs. Never approves, stops or releases anything.",
             io: ["a question", "answer + evidence"], when: "on a question" },
};

// [from, to, kind]; kind: "" | "faint" | "loop"
export const EDGES = [
  ["mes", "signal"], ["mes", "people"], ["mes", "cause"], ["notes", "floor"], ["wi", "method"], ["mlog", "maint"],
  ["signal", "contain"], ["signal", "impact"], ["people", "cause"], ["people", "impact"], ["floor", "cause"],
  ["method", "change"], ["cause", "impact"], ["cause", "contain"],
  ["impact", "app"], ["contain", "app"], ["change", "app"], ["maint", "app"], ["copilot", "app"],
  ["models", "copilot", "faint"], ["change", "wi", "loop"],
];

const pillW = (v, u) => Math.round((`${fmt.n(v)} ${u || ""}`.trim().length) * 6.1 + 16);
const box = (id) => { const n = NODES[id]; return { x: COL[n.c], y: n.y - HN / 2, cx: COL[n.c] + W / 2, cy: n.y, r: COL[n.c] + W, b: n.y + HN / 2 }; };
const IN_APP = { contain: -22, impact: -4, change: 12, maint: 24 };

export function edgePath(a, b) {
  const k = `${a}>${b}`;
  if (k === "cause>impact") { const A = box(a), B = box(b); return `M${A.cx},${A.b} L${B.cx},${B.y}`; }
  if (k === "copilot>app") { const A = box(a), B = box(b); return `M${A.cx},${A.y} L${B.cx},${B.b}`; }
  if (k === "signal>contain") { const A = box(a), B = box(b); return `M${A.r},${A.cy - 10} C${A.r + 150},${A.cy - 40} ${B.x - 90},${A.cy - 40} ${B.x},${B.cy - 12}`; }
  if (k === "models>copilot") { const B = box(b); return `M922,${B.cy} L${B.x},${B.cy}`; }
  if (k === "change>wi") { const A = box(a), B = box(b); const y = 466;
    return `M${A.cx},${A.b} C${A.cx},${y} ${A.cx - 30},${y} ${A.cx - 70},${y} L${B.cx + 60},${y} C${B.cx + 20},${y} ${B.cx},${y} ${B.cx},${B.b}`; }
  const A = box(a), B = box(b);
  const y1 = A.cy, y2 = B.cy + (b === "app" ? IN_APP[a] || 0 : 0);
  const dx = Math.max(24, (B.x - A.r) / 2);
  return `M${A.r},${y1} C${A.r + dx},${y1} ${B.x - dx},${y2} ${B.x},${y2}`;
}

const CSS = `
.dg text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif; }
.dg .hd { fill: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.dg .zone { fill: none; stroke: var(--border-2); stroke-dasharray: 5 6; }
.dg .zl { fill: var(--muted); font-size: 11px; }
.dg .e { fill: none; stroke: var(--border-2); stroke-width: 1.6; marker-end: url(#dgArr); }
.dg .e.flow { stroke-dasharray: 5 7; animation: dgFlow 1.4s linear infinite; }
.dg .e.faint { stroke-dasharray: 3 5; opacity: .75; }
.dg .e.loop { stroke: var(--c-method); stroke-dasharray: 6 5; opacity: .8; }
.dg .e.hot { stroke: var(--hc, var(--accent)); stroke-width: 2.6; opacity: 1; }
.dg .ll { fill: var(--c-method); font-size: 11px; font-weight: 600; }
.dg .n { cursor: pointer; }
.dg .n rect.bg { fill: var(--surface); stroke: var(--border-2); stroke-width: 1.2; transition: stroke .2s, fill .2s; }
.dg .n:hover rect.bg, .dg .n.sel rect.bg { stroke: var(--nc); fill: var(--surface-2); }
.dg .n rect.bar { fill: var(--nc); }
.dg .n rect.ib { fill: var(--nc); opacity: .16; }
.dg .n .ic { color: var(--nc); }
.dg svg.dgi { width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.dg .n .t { fill: var(--text); font-size: 13px; font-weight: 650; }
.dg .n .s { fill: var(--muted); font-size: 11px; }
.dg .n .tag rect { fill: var(--surface-3); stroke: var(--border-2); }
.dg .n .tag text { fill: var(--nc); font-size: 10px; font-weight: 750; text-anchor: middle; letter-spacing: .04em; }
.dg .n .cnt rect { fill: var(--surface-3); stroke: var(--border-2); }
.dg .n .cnt text { fill: var(--text); font-size: 10.5px; font-weight: 700; text-anchor: end; font-variant-numeric: tabular-nums; }
.dg .n.bump .cnt rect { stroke: var(--nc); }
.dg .n.src { --nc: var(--text-2); } .dg .n.det { --nc: var(--accent); } .dg .n.und { --nc: #9b7bf0; }
.dg .n.dec { --nc: var(--c-method); } .dg .n.eng { --nc: var(--warning); }
.dg .ring { fill: none; stroke: var(--hc, var(--accent)); stroke-width: 3; opacity: 0; }
.dg .n.hot .ring { animation: dgRing 1.1s ease-out; }
.dg .pk { filter: drop-shadow(0 0 4px var(--hc, var(--accent))); }
@keyframes dgFlow { to { stroke-dashoffset: -24; } }
@keyframes dgRing { 0% { opacity: .95; stroke-width: 6; } 100% { opacity: 0; stroke-width: 1; } }
`;

export function diagram({ counts = {}, units = {}, flow = true, id = "dg" } = {}) {
  const heads = COLS.map((h, i) => `<text class="hd" x="${COL[i] + 2}" y="30">${esc(h)}</text>`).join("");
  const edges = EDGES.map(([a, b, kind = ""]) =>
    `<path class="e ${kind} ${flow && !kind ? "flow" : ""}" data-e="${a}>${b}" d="${edgePath(a, b)}"/>`).join("");
  const nodes = Object.entries(NODES).map(([k, n]) => {
    const B = box(k), v = counts[k];
    return `<g class="n ${n.k}" data-n="${k}" transform="translate(${B.x},${B.y})">
      <rect class="ring" x="-3" y="-3" width="${W + 6}" height="${HN + 6}" rx="15"/>
      <rect class="bg" width="${W}" height="${HN}" rx="12"/>
      <rect class="bar" x="0" y="14" width="3" height="${HN - 28}" rx="1.5"/>
      <rect class="ib" x="12" y="15" width="34" height="34" rx="9"/>
      <g class="ic" transform="translate(20,23)">${icon(n.i).replace('<svg class="i ', '<svg width="18" height="18" class="dgi ')}</g>
      <text class="t" x="56" y="29">${esc(n.t)}</text>
      <text class="s" x="56" y="46">${esc(n.s)}</text>
      ${n.model != null ? `<g class="tag" transform="translate(12,-9)"><rect width="30" height="18" rx="9"/><text x="15" y="12.5">M${n.model}</text></g>` : ""}
      <g class="cnt" transform="translate(${W - 8},-9)" ${v == null ? 'style="display:none"' : ""}><rect x="${-pillW(v, units[k])}" width="${pillW(v, units[k])}" height="18" rx="9"/>
        <text x="-8" y="12.5" data-v="${k}">${v == null ? "" : `${fmt.n(v)} ${esc(units[k] || "")}`}</text></g>
    </g>`;
  }).join("");
  return `<svg class="dg" id="${id}" viewBox="0 0 1168 592" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Workflow of the production copilot">
    <style>${CSS}</style>
    <defs><marker id="dgArr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,1 L9,5 L0,9 z" fill="var(--border-2)"/></marker></defs>
    <rect x="236" y="56" width="686" height="494" rx="18" class="zone"/>
    <text class="zl" x="248" y="570">The copilot reads every model in this box through 13 read-only tools - it never approves, stops or releases</text>
    ${heads}${edges}
    <text class="ll" x="300" y="460">released WI → the next shift works to the current method</text>
    ${nodes}
  </svg>`;
}

export function setCounts(svg, counts, units = {}) {
  if (!svg) return;
  for (const [k, v] of Object.entries(counts)) {
    const t = svg.querySelector(`[data-v="${k}"]`);
    if (!t || v == null) continue;
    const txt = `${fmt.n(v)} ${units[k] || ""}`.trim();
    if (t.textContent === txt) continue;
    t.textContent = txt;
    const g = t.parentNode;
    g.style.display = "";
    let w = pillW(v, units[k]);
    try { w = Math.ceil(t.getComputedTextLength()) + 16; } catch (e) { /* not rendered yet */ }
    const r = g.querySelector("rect"); r.setAttribute("x", -w); r.setAttribute("width", w);
  }
}

const SEVC = { critical: "var(--critical)", serious: "var(--serious)", warning: "var(--warning)", good: "var(--good)", info: "var(--accent)" };
export function pulse(svg, node, sev = "info") {
  const g = svg && svg.querySelector(`[data-n="${node}"]`);
  if (!g) return;
  g.style.setProperty("--hc", SEVC[sev] || SEVC.info);
  g.classList.remove("hot"); void g.getBBox(); g.classList.add("hot");
  setTimeout(() => g.classList.remove("hot"), 1200);
}

// a dot travels along an edge, then the target node pulses
export function packet(svg, a, b, sev = "info", delay = 0) {
  const e = svg && svg.querySelector(`[data-e="${a}>${b}"]`);
  if (!e) return;
  setTimeout(() => {
    if (!svg.isConnected) return;
    const col = SEVC[sev] || SEVC.info;
    e.style.setProperty("--hc", col); e.classList.add("hot");
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("r", "5"); c.setAttribute("class", "pk"); c.setAttribute("fill", col); c.style.setProperty("--hc", col);
    const m = document.createElementNS("http://www.w3.org/2000/svg", "animateMotion");
    m.setAttribute("dur", "0.75s"); m.setAttribute("path", e.getAttribute("d")); m.setAttribute("fill", "freeze");
    c.appendChild(m); svg.appendChild(c);
    try { m.beginElement(); } catch (err) { /* SMIL not supported: the pulse still shows */ }
    setTimeout(() => { c.remove(); e.classList.remove("hot"); pulse(svg, b, sev); }, 760);
  }, delay);
}

// Animate a list of [from, to] edges one after another. Returns the total duration in ms.
export function trace(svg, path, sev = "info", start = 0, step = 420) {
  path.forEach(([a, b], i) => {
    if (i === 0) setTimeout(() => pulse(svg, a, sev), start);
    packet(svg, a, b, sev, start + i * step);
  });
  return start + path.length * step;
}

// SVG -> PNG with the current theme's colours resolved (the variables do not survive outside the page)
export function exportPng(svg, name = "workflow.png", scale = 2) {
  const cs = getComputedStyle(document.documentElement);
  let src = new XMLSerializer().serializeToString(svg);
  for (let i = 0; i < 3; i++)       // inner var() first, then the ones that used it as a fallback
    src = src.replace(/var\((--[\w-]+)(?:,\s*([^()]*))?\)/g, (m, v, d) => cs.getPropertyValue(v).trim() || (d || "").trim() || "#888");
  const bg = cs.getPropertyValue("--bg").trim();
  const vb = svg.viewBox.baseVal;
  const img = new Image();
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = vb.width * scale; c.height = vb.height * scale;
    const g = c.getContext("2d");
    g.fillStyle = bg; g.fillRect(0, 0, c.width, c.height);
    g.drawImage(img, 0, 0, c.width, c.height);
    const a = document.createElement("a"); a.href = c.toDataURL("image/png"); a.download = name; a.click();
  };
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(src);
}
