// Factory map - where on the Giga site each problem happened. The site plan and zones come from the
// gigafactory-monitor prototype; our line (ST011-ST014) is drawn inside General Assembly (A109).
// Sources: this week's models (/api/map, follows the week replay) or the live line (/api/live/state).
import { $, $$, esc, fmt, icon, parse, SEV_ICON } from "../util.js";
import { MAP_W, MAP_H, MAP_IMG, ZONES, ZONE, CONVEYOR, STATIONS, EQUIP, OPERATOR, PLACES, FLOWS, locate, equipOf, CAUSE_COLOR } from "../map/site.js";

const SEV_RANK = { critical: 4, serious: 3, warning: 2, info: 1, good: 0 };
const SEV_COL = { critical: "var(--critical)", serious: "var(--serious)", warning: "var(--warning)", info: "var(--info)", good: "var(--good)" };
const KIND_COL = { cars: "var(--good)", maint: "#9b7bf0", repair: "#9b7bf0", method: "var(--c-method)", note: "var(--accent)", flag: "var(--text-2)" };
const KIND_ICON = { cars: "factory", maint: "wrench", repair: "wrench", method: "file", note: "notes", flag: "search" };
const CAUSE_ICON = { machine: "machine", people: "users", method: "file", station: "box", unclear: "search" };
const LAYERS = [["problems", "Problems"], ["cars", "Cars waiting"], ["maint", "Machines"], ["method", "Methods"], ["note", "Floor"], ["flag", "Other flags"], ["flows", "Flows"]];
const LAYER_OF = { case: "problems", cars: "cars", maint: "maint", repair: "maint", method: "method", note: "note", flag: "flag" };
const CLUSTER_K = 2.3;           // zoomed out further than this: one badge per station / building

// ---------------------------------------------------------------------------------------------
// items: everything that gets a pin, from the week (database) or the live engine
// ---------------------------------------------------------------------------------------------
function weekItems(d) {
  const out = [];
  for (const c of d.cases) {
    // open: still active, or cars still wait for a check
    const open = !["fixed", "over"].includes(c.status) || (c.check || 0) + (c.hold || 0) > 0;
    const sev = c.action && c.action !== "NO HOLD" ? c.action_sev : c.category === "High" ? "serious" : c.category === "Medium" ? "warning" : "info";
    out.push({ id: `case-${c.case_id}`, kind: "case", sev, open, ts: c.first_ts, cause: c.cause, culprit: c.culprit,
      station: c.station, title: c.culprit, sub: `${c.cause} · ${c.family}`, label: short(c.culprit), c });
  }
  const held = { yard: 0, gate: 0 }, byCase = { yard: [], gate: [] };
  for (const c of d.cases) {
    const w = c.where || {};
    const y = w["in the plant"] || 0, g = Object.entries(w).filter(([k]) => k.startsWith("shipped")).reduce((a, [, v]) => a + v, 0);
    if (y) { held.yard += y; byCase.yard.push([c, y]); }
    if (g) { held.gate += g; byCase.gate.push([c, g]); }
  }
  for (const place of ["yard", "gate"]) if (held[place]) out.push({ id: `cars-${place}`, kind: "cars", place, sev: place === "gate" ? "critical" : "serious",
    open: true, ts: d.start, title: `${fmt.n(held[place])} cars ${place === "gate" ? "shipped - field review" : "waiting for a check"}`,
    label: fmt.n(held[place]), n: held[place], cases: byCase[place] });
  for (const m of d.maint) {
    const over = String(m.next_due || "").startsWith("overdue");
    const soon = !over && m.next_due && +parse(m.next_due) <= +parse(d.end) + 24 * 3600e3;
    if (!over && !soon && !(m.p_fail_7d >= 0.1)) continue;
    out.push({ id: `maint-${m.equipment}-${m.family}`, kind: "maint", sev: over ? "serious" : "warning", open: true, ts: d.start, station: m.station,
      equipment: m.equipment, title: `${m.equipment}: ${over ? "maintenance overdue" : soon ? `due ${fmt.dt(m.next_due)}` : `${fmt.pct(m.p_fail_7d)} failure risk in 7 days`}`,
      sub: m.failure_mode, label: equipOf(m.equipment, m.station) || m.equipment, m });
  }
  for (const r of d.repairs) out.push({ id: `rep-${r.station}-${r.ts}`, kind: "repair", sev: "info", open: false, ts: r.ts, station: r.station,
    equipment: r.text, title: `Repair: ${r.text}`, sub: `${fmt.dt(r.ts)} · ${r.downtime_min} min down`, label: equipOf(r.text, r.station) || "repair", r });
  for (const w of d.methods) if (w.verdict !== "PASS") out.push({ id: `wi-${w.wi_version}`, kind: "method", sev: w.verdict === "BLOCK" ? "serious" : "warning",
    open: true, ts: w.valid_from, station: w.station, wi: w.wi_version, title: `${w.wi_version}: method check ${w.verdict}`, sub: w.change_note, label: w.wi_version, w });
  const notes = {};
  for (const n of d.notes) (notes[n.station] = notes[n.station] || []).push(n);
  for (const [s, list] of Object.entries(notes)) {
    const early = list.filter((n) => n.link === "early warning").length;
    out.push({ id: `notes-${s}`, kind: "note", sev: early ? "good" : "info", open: true, ts: list[0].ts, station: s, list,
      title: `${list.length} facts from the floor at ${s.toUpperCase()}`, sub: early ? `${early} early warnings - the floor saw it before the data` : "handover notes, read by the floor listener",
      label: String(list.length) });
  }
  for (const o of d.other.slice(0, 14)) {
    const s = String(o.stations || "st012").split(",")[0];
    out.push({ id: `flag-${o.impact_id}`, kind: "flag", sev: o.category === "High" ? "warning" : "info", open: o.status !== "one-off", ts: o.first_ts,
      station: s, title: o.title, sub: `${o.source} · ${o.category} ${fmt.n(o.priority)}`, label: "", o });
  }
  return out;
}

function liveItems(L) {
  const out = [];
  for (const c of L.cases || []) {
    if (c.status !== "open") continue;
    out.push({ id: `lcase-${c.id}`, kind: "case", sev: c.action !== "NO HOLD" ? c.action_sev : c.category === "High" ? "serious" : "warning", open: true,
      ts: c.confirmed, cause: c.cause, culprit: c.culprit, station: c.station, title: c.culprit, sub: `${c.cause} · ${c.family}`, label: short(c.culprit),
      c: { ...c, case_id: c.id, first_ts: c.first, last_ts: c.last, owner: c.owner_action, live: true } });
  }
  const k = L.kpi || {};
  if ((k.check || 0) + (k.hold || 0)) out.push({ id: "lcars-yard", kind: "cars", place: "yard", sev: "serious", open: true, ts: L.sim,
    title: `${fmt.n(k.check + k.hold)} cars waiting for a check`, label: fmt.n(k.check + k.hold), n: k.check + k.hold,
    cases: (L.cases || []).filter((c) => c.status === "open" && c.check + c.hold).map((c) => [{ culprit: c.culprit, case_id: c.id }, c.check + c.hold]) });
  return out;
}
const short = (s) => String(s || "").replace(/^(bolt|coolant) batch /, "").replace("Material supply to ", "supply ").replace("MES / line network", "MES")
  .replace(/^(Nutrunner|Fill head|Leak tester|VIN scanner|Fill pump|Subframe fixture) /, "");

// ---------------------------------------------------------------------------------------------
// static SVG layers
// ---------------------------------------------------------------------------------------------
function zonesSvg() {
  return ZONES.map((zz) => `<polygon class="zn ${zz.ours ? "ours" : ""} ${zz.added ? "added" : ""}" data-zone="${zz.id}" points="${zz.polygon}"
    data-tip="<b>${esc(zz.code)}</b> ${esc(zz.name)}<br><span style='opacity:.7'>${esc(zz.hall)}${zz.ours ? " · our line ST012-ST013" : ""}${zz.added ? " · added for this prototype" : ""}</span>"/>`).join("")
    + ZONES.filter((zz) => zz.tag).map((zz) => `<text class="zn-lab" x="${zz.tag[0]}" y="${zz.tag[1]}">${esc(zz.tag[2])}</text>`).join("");
}
function flowsSvg() {
  return FLOWS.map((f) => `<g class="fl fl-${f.kind}" data-flow="${f.id}"><path id="fl-${f.id}" d="${f.d}"/>
    ${[0, 1, 2].map((i) => `<circle r="2.1"><animateMotion dur="${f.kind === "method" ? 16 : 9}s" begin="${-i * 3}s" repeatCount="indefinite" path="${f.d}"/></circle>`).join("")}
    <text class="fl-t"><textPath href="#fl-${f.id}" startOffset="${f.kind === "method" ? "38%" : "50%"}" text-anchor="middle">${esc(f.label)}</textPath></text></g>`).join("");
}
function lineSvg() {
  const cars = Array.from({ length: 11 }, (_, i) => `<g class="car"><rect x="-4.6" y="-2.6" width="9.2" height="5.2" rx="1.6"/>
    <animateMotion dur="48s" begin="${-i * 4.36}s" repeatCount="indefinite" rotate="auto" path="${CONVEYOR}"/></g>`).join("");
  const st = Object.entries(STATIONS).map(([k, s]) => `<g class="stn ${s.ours ? "ours" : ""}" data-st="${k}" data-tip="<b>${s.code}</b> ${esc(s.name)}${s.ours ? ` · severity ${s.severity}` : " · not monitored in this prototype"}">
      <rect x="${s.x - 26}" y="${s.y - 17}" width="52" height="34" rx="3"/>
      <text class="stn-c" x="${s.x}" y="${s.y - 19.5}">${s.code}</text>
      <text class="stn-n" x="${s.x}" y="${s.y + 22.5}">${esc(s.name)}</text>
      <circle class="stn-dot" cx="${s.x + 22}" cy="${s.y - 13}" r="2.2"/></g>`).join("");
  const eq = Object.entries(EQUIP).map(([k, e]) => `<g class="eq eq-${e.kind}" data-eq="${k}" data-tip="<b>${k}</b> ${esc(e.name)}">
      <circle cx="${e.x}" cy="${e.y}" r="3.6"/><text x="${e.x}" y="${e.y + (e.y < 322 ? -5 : 7.5)}">${k}</text></g>`).join("");
  const ops = Object.values(OPERATOR).map((o) => `<g class="op"><circle cx="${o.x}" cy="${o.y}" r="2.4"/><path d="M${o.x - 3.4},${o.y + 5.5} q3.4,-4.5 6.8,0"/></g>`).join("");
  return `<g class="ln-lite"><path class="ln-glow" d="${CONVEYOR}"/>${Object.values(STATIONS).filter((s) => s.ours).map((s) => `<circle class="ln-st" cx="${s.x}" cy="${s.y}" r="5"/>`).join("")}</g>
    <g class="ln-detail" id="mpDetail">
      <rect class="ln-band" x="244" y="296" width="466" height="64" rx="5"/>
      <text class="ln-title" x="249" y="301.5">LINE 1 · FINAL ASSEMBLY · A109 · takt 50 s</text>
      <path class="conv" d="${CONVEYOR}"/><path class="conv-mv" d="${CONVEYOR}"/>
      ${st}${cars}${eq}${ops}
      <text class="ln-dir" x="258" y="330">→ flow</text>
    </g>`;
}

// ---------------------------------------------------------------------------------------------
// the page
// ---------------------------------------------------------------------------------------------
export async function render(root, params, ctx) {
  const st = { src: "week", week: null, live: null, items: [], vis: [], cam: { cx: MAP_W / 2, cy: MAP_H / 2, s: 1 }, fitS: 1, sel: null,
    layers: new Set(["problems", "cars", "maint", "method", "note", "flows"]), blueprint: document.documentElement.dataset.theme !== "light",
    follow: true, sound: false, replayT: null, shown: new Set(), ev: 0, alive: true, timer: 0, anim: 0, mode: null, liveSeen: new Set(), audio: null };
  root.innerHTML = `
  <div class="mp">
    <div class="card mp-main">
      <div class="row mp-bar">
        <div class="seg" id="mpSrc"><button data-src="week" class="on">This week</button><button data-src="live">Live line <span class="live-dot" id="mpLiveDot" hidden></span></button></div>
        <div class="chips" id="mpLayers">${LAYERS.map(([k, l]) => `<button class="chip ${st.layers.has(k) ? "on" : ""}" data-layer="${k}">${l}</button>`).join("")}</div>
        <span class="sp"></span>
        <button class="btn sm ghost" id="mpFollow" data-tip="fly to new problems as they appear (live line or week replay)">${icon("eye")} Follow</button>
        <button class="btn sm ghost" id="mpSound" data-tip="alarm tones for new critical and serious problems">${icon("bell")} Sound off</button>
        <button class="btn sm ghost" id="mpBlue" data-tip="blueprint / plan colours">${icon("flow")} Blueprint</button>
      </div>
      <div class="mp-view" id="mpView" tabindex="0">
        <svg id="mpSvg" class="mp-svg" xmlns="http://www.w3.org/2000/svg">
          <defs><filter id="mpGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter></defs>
          <image href="${MAP_IMG}" x="0" y="0" width="${MAP_W}" height="${MAP_H}" class="mp-img" preserveAspectRatio="none"/>
          <g id="gZones">${zonesSvg()}</g>
          <g id="gFlows">${flowsSvg()}</g>
          <g id="gLine">${lineSvg()}</g>
          <g id="gLinks"></g><g id="gRipples"></g><g id="gPins"></g>
        </svg>
        <div class="mp-call" id="mpCall" hidden></div>
        <div class="mp-ctl"><button class="btn icon" id="mpIn" title="Zoom in">+</button><button class="btn icon" id="mpOut" title="Zoom out">−</button>
          <button class="btn icon" id="mpHome" title="Whole site">${icon("factory")}</button><button class="btn sm" id="mpLine" title="Zoom to our line">Our line</button></div>
        <div class="mp-legend">
          ${["machine", "people", "method", "station"].map((c) => `<span><i style="background:var(--c-${c})"></i>${c}</span>`).join("")}
          <span><i class="ring"></i>ring = severity</span>
          <span class="lg-f"><b class="f-material"></b>material</span><span class="lg-f"><b class="f-cars"></b>cars</span><span class="lg-f"><b class="f-data"></b>data</span>
          <span class="lg-f"><b class="f-method"></b>methods</span><span class="lg-f"><b class="f-maint"></b>technicians</span></div>
        <div class="mp-zoom" id="mpZoom"></div>
        <div class="mp-tick" id="mpTick" hidden></div>
      </div>
    </div>
    <div class="mp-side">
      <div class="card mp-sum" id="mpSum"></div>
      <div class="card"><div class="card-h"><h3>On the map</h3><span class="hint" id="mpCount"></span></div><div class="mp-list" id="mpList"></div></div>
    </div>
  </div>`;
  const view = $("#mpView", root), svg = $("#mpSvg", root), call = $("#mpCall", root);
  const gPins = $("#gPins", root), gLinks = $("#gLinks", root), gRip = $("#gRipples", root), gDetail = $("#mpDetail", root);
  svg.classList.toggle("blue", st.blueprint);

  // ---------- camera ----------
  const size = () => ({ w: view.clientWidth || 800, h: view.clientHeight || 560 });
  function fit() { const { w, h } = size(); st.fitS = Math.min(w / MAP_W, h / MAP_H) * 1.02; }
  function apply() {
    const { w, h } = size(), c = st.cam;
    svg.setAttribute("viewBox", `${c.cx - w / 2 / c.s} ${c.cy - h / 2 / c.s} ${w / c.s} ${h / c.s}`);
    const k = c.s / st.fitS;
    const det = Math.max(0, Math.min(1, (k - 1.7) / 1.1));
    gDetail.style.opacity = det; gDetail.style.pointerEvents = det > 0.3 ? "" : "none";
    $(".ln-lite", svg).style.opacity = 1 - det;
    svg.style.setProperty("--k", k); svg.style.setProperty("--s", c.s);
    svg.classList.toggle("near", k > 2.6); svg.classList.toggle("far", k < 1.6); svg.classList.toggle("vnear", k > 7.5);
    const mode = k < CLUSTER_K ? "cluster" : "detail";
    if (mode !== st.mode) { st.mode = mode; drawPins(); } else placePins();
    placeCall();
    $("#mpZoom", root).textContent = `${Math.round(k * 100)}%`;
  }
  const toScreen = (x, y) => { const { w, h } = size(), c = st.cam; return [(x - c.cx) * c.s + w / 2, (y - c.cy) * c.s + h / 2]; };
  const toMap = (px, py) => { const { w, h } = size(), c = st.cam; return [(px - w / 2) / c.s + c.cx, (py - h / 2) / c.s + c.cy]; };
  const clampS = (s) => Math.max(st.fitS * 0.85, Math.min(st.fitS * 14, s));
  function fly(cx, cy, k, dur = 900) {
    cancelAnimationFrame(st.anim);
    const a = { ...st.cam }, b = { cx, cy, s: clampS(st.fitS * k) }, t0 = performance.now();
    const step = (t) => {
      const u = Math.min(1, (t - t0) / dur), e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
      st.cam = { cx: a.cx + (b.cx - a.cx) * e, cy: a.cy + (b.cy - a.cy) * e, s: a.s * Math.pow(b.s / a.s, e) };
      apply(); if (u < 1 && st.alive) st.anim = requestAnimationFrame(step);
    };
    st.anim = requestAnimationFrame(step);
  }
  const home = () => fly(MAP_W / 2, MAP_H / 2, 1);
  const toLine = () => fly(477, 334, 3.1);
  // gestures
  let drag = null;
  view.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".mp-call, .mp-ctl, .btn")) return;
    drag = { x: e.clientX, y: e.clientY, cx: st.cam.cx, cy: st.cam.cy, moved: false }; view.setPointerCapture(e.pointerId);
  });
  view.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    if (drag.moved) { cancelAnimationFrame(st.anim); st.cam.cx = drag.cx - dx / st.cam.s; st.cam.cy = drag.cy - dy / st.cam.s; view.classList.add("grab"); apply(); }
  });
  view.addEventListener("pointerup", (e) => {
    const d = drag; drag = null; view.classList.remove("grab");
    if (!d || d.moved) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const pin = el?.closest?.("[data-pin]"), cl = el?.closest?.("[data-cl]"), zn = el?.closest?.("[data-zone]"), stn = el?.closest?.("[data-st]");
    if (pin) select(pin.dataset.pin, true);
    else if (cl) { const [x, y] = cl.dataset.at.split(",").map(Number); fly(x, y, 5.2); }
    else if (stn) { const s = STATIONS[stn.dataset.st]; fly(s.x, s.y, 7.5); zoneCard("a109-general-assembly", s); }
    else if (zn) zoneCard(zn.dataset.zone);
    else { st.sel = null; call.hidden = true; drawLinks(); }
  });
  view.addEventListener("wheel", (e) => {
    e.preventDefault(); cancelAnimationFrame(st.anim);
    const r = view.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    const [mx, my] = toMap(px, py);
    const s = clampS(st.cam.s * Math.exp(-e.deltaY * 0.0016));
    const { w, h } = size();
    st.cam = { s, cx: mx - (px - w / 2) / s, cy: my - (py - h / 2) / s }; apply();
  }, { passive: false });
  const zoomBy = (f) => fly(st.cam.cx, st.cam.cy, (st.cam.s * f) / st.fitS, 350);
  $("#mpIn", root).onclick = () => zoomBy(1.6); $("#mpOut", root).onclick = () => zoomBy(1 / 1.6);
  $("#mpHome", root).onclick = home; $("#mpLine", root).onclick = toLine;
  const ro = new ResizeObserver(() => { const k = st.cam.s / st.fitS; fit(); st.cam.s = st.fitS * k; apply(); });
  ro.observe(view);

  // ---------- pins ----------
  const visible = () => st.items.filter((it) => st.layers.has(LAYER_OF[it.kind]) && (st.replayT == null || +parse(it.ts) <= st.replayT));
  function pinSvg(it) {
    const L = it.loc, col = it.kind === "case" ? CAUSE_COLOR[it.cause] || CAUSE_COLOR.unclear : KIND_COL[it.kind];
    const ic = it.kind === "case" ? CAUSE_ICON[it.cause] || "search" : KIND_ICON[it.kind];
    const pulse = it.open && SEV_RANK[it.sev] >= 3 ? `<circle class="halo" cy="-21" r="15"/>` : "";
    if (it.kind === "cars") { const pw = 36 + it.label.length * 7.6;
      return `<g class="pin cars sev-${it.sev}" data-pin="${it.id}" data-x="${L.x}" data-y="${L.y}">${pulse}
        <rect class="pill" x="${-pw / 2}" y="-36" width="${pw}" height="24" rx="12" style="--pc:${col}"/>${icon("factory").replace('<svg class="i ', `<svg x="${-pw / 2 + 6}" y="-31" class="pi `)}
        <text class="pn" x="${-pw / 2 + 25}" y="-19.5">${esc(it.label)}</text><path class="tip" d="M-5,-12 L0,-3 L5,-12Z"/><circle class="gnd" r="2.6"/></g>`; }
    if (it.kind === "note" || it.kind === "flag" || it.kind === "repair")
      return `<g class="pin small k-${it.kind} sev-${it.sev} ${it.open ? "" : "done"}" data-pin="${it.id}" data-x="${L.x}" data-y="${L.y}">
        <circle class="bub" r="10" style="--pc:${col}"/>${icon(KIND_ICON[it.kind]).replace('<svg class="i ', '<svg x="-6" y="-6" class="pi sm ')}
        ${it.label ? `<text class="pn sm" x="11" y="-6">${esc(it.label)}</text>` : ""}</g>`;
    return `<g class="pin k-${it.kind} sev-${it.sev} ${it.open ? "" : "done"}" data-pin="${it.id}" data-x="${L.x}" data-y="${L.y}">${pulse}
      <path class="drop" style="--pc:${col}" d="M0,0 C-3,-7 -13,-11 -13,-21 A13,13 0 1 1 13,-21 C13,-11 3,-7 0,0Z"/>
      ${icon(ic).replace('<svg class="i ', '<svg x="-7.5" y="-28.5" class="pi ')}
      <circle class="gnd" r="2.6"/>${it.label ? `<text class="pl" y="-39">${esc(it.label)}</text>` : ""}</g>`;
  }
  function clusterSvg(key, list) {
    const pool = list.filter((i) => i.open).length ? list.filter((i) => i.open) : list;
    const top = pool.reduce((a, b) => (SEV_RANK[b.sev] > SEV_RANK[a.sev] || (SEV_RANK[b.sev] === SEV_RANK[a.sev] && b.open && !a.open) ? b : a));
    const x = list.reduce((a, b) => a + b.loc.x, 0) / list.length, y = list.reduce((a, b) => a + b.loc.y, 0) / list.length;
    const at = STATIONS[key] ? [STATIONS[key].x, STATIONS[key].y] : [x, y];
    const lab = STATIONS[key] ? STATIONS[key].code : PLACES[key]?.label.split(" (")[0].split(" - ")[0] || key;
    const cars = list.filter((i) => i.kind === "cars");
    const openN = list.filter((i) => i.open && i.kind !== "note").length;
    const n = cars.length === list.length ? cars.reduce((a, b) => a + b.n, 0) : openN || list.length;
    const hot = list.some((i) => i.open && SEV_RANK[i.sev] >= 3);
    return `<g class="cl sev-${top.sev} ${openN ? "" : "done"}" data-cl="${key}" data-at="${at.join(",")}" data-x="${at[0]}" data-y="${at[1]}">
      ${hot ? `<circle class="halo" r="22"/>` : ""}<circle class="cl-b" r="16"/>
      <text class="cl-n" y="4.5">${cars.length === list.length ? fmt.n(n) : n}</text><text class="cl-l" y="30">${esc(lab)}${cars.length === list.length ? " cars" : ""}</text></g>`;
  }
  function drawPins() {
    const vis = visible();
    st.vis = vis;
    if (st.mode === "cluster") {
      const g = {};
      for (const it of vis) (g[it.loc.cluster] = g[it.loc.cluster] || []).push(it);
      gPins.innerHTML = Object.entries(g).map(([k, l]) => clusterSvg(k, l)).join("");
    } else {
      // spread pins that share a spot
      const spot = {};
      vis.forEach((it) => { const k = `${Math.round(it.loc.x / 6)},${Math.round(it.loc.y / 6)}`; (spot[k] = spot[k] || []).push(it); });
      vis.forEach((it) => { it.off = [0, 0]; });
      Object.values(spot).forEach((l) => { if (l.length > 1) l.forEach((it, i) => { const a = (i / l.length) * Math.PI * 2 - Math.PI / 2; it.off = [Math.cos(a) * 20, Math.sin(a) * 14]; }); });
      const order = [...vis].sort((a, b) => SEV_RANK[a.sev] - SEV_RANK[b.sev]);
      gPins.innerHTML = order.map(pinSvg).join("");
    }
    placePins(); drawLinks(); stationStatus();
  }
  function placePins() {
    const inv = 1 / st.cam.s;
    for (const g of gPins.children) {
      const it = st.vis.find((i) => i.id === g.dataset.pin);
      const ox = it?.off?.[0] || 0, oy = it?.off?.[1] || 0;
      g.setAttribute("transform", `translate(${g.dataset.x},${g.dataset.y}) scale(${inv}) translate(${ox},${oy})`);
      g.classList.toggle("sel", !!it && it.id === st.sel);
    }
  }
  function drawLinks() {
    const show = st.vis.filter((it) => it.loc.from && (it.id === st.sel || (it.open && it.kind === "case")));
    gLinks.innerHTML = show.map((it) => {
      const a = it.loc.from, b = it.loc, mx = (a.x + b.x) / 2, my = Math.min(a.y, b.y) - Math.abs(a.x - b.x) * 0.18 - 20;
      const sel = it.id === st.sel;
      return `<g class="lk ${sel ? "sel" : ""}" style="--lc:${it.kind === "case" ? CAUSE_COLOR[it.cause] : KIND_COL[it.kind]}">
        <path d="M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}"/>${sel ? `<circle class="src" cx="${a.x}" cy="${a.y}" r="5"/><text class="src-t" x="${a.x}" y="${a.y - 9}">${esc(a.label)}</text>` : ""}</g>`;
    }).join("");
  }
  function stationStatus() {
    for (const [k] of Object.entries(STATIONS)) {
      const el = $(`[data-st="${k}"]`, svg); if (!el) continue;
      let worst = 0;
      st.vis.filter((i) => i.loc.st === k && i.open && i.kind !== "note").forEach((i) => (worst = Math.max(worst, SEV_RANK[i.sev])));
      if (st.src === "live" && st.live?.kpi?.station_flags_h?.[k] >= 6) worst = Math.max(worst, 2);
      el.dataset.s = worst >= 4 ? "critical" : worst === 3 ? "serious" : worst === 2 ? "warning" : "ok";
    }
    const zs = {};
    st.vis.filter((i) => i.open && i.kind !== "note").forEach((i) => {
      const z = i.loc.st ? "a109-general-assembly" : PLACES[i.place]?.zone;
      if (z) zs[z] = Math.max(zs[z] || 0, SEV_RANK[i.sev]);
      if (i.loc.from && i.id === st.sel) { const fz = Object.values(PLACES).find((p) => p.x === i.loc.from.x && p.y === i.loc.from.y)?.zone; if (fz) zs[fz] = Math.max(zs[fz] || 0, 2); }
    });
    $$("[data-zone]", svg).forEach((p) => { const r = zs[p.dataset.zone]; p.dataset.s = r >= 4 ? "critical" : r === 3 ? "serious" : r === 2 ? "warning" : ""; });
  }
  function ripple(x, y, sev) {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", 3); c.setAttribute("class", `rp sev-${sev}`);
    c.style.setProperty("--sc", `${60 / st.cam.s / 3}`);
    gRip.appendChild(c); setTimeout(() => c.remove(), 1800);
  }

  // ---------- callout ----------
  const crumbs = (p) => `<div class="mp-crumb">${p.map(esc).join(` <span>›</span> `)}</div>`;
  function callHtml(it) {
    const head = (sevLabel, title) => `<div class="row" style="gap:8px;margin-bottom:4px"><span class="pill ${it.sev === "good" ? "good" : it.sev} ${it.sev === "critical" ? "solid" : ""}">${icon(SEV_ICON[it.sev] || "info")} ${esc(sevLabel)}</span>
      <span class="sp"></span><button class="x" data-close>${icon("x")}</button></div><div class="mp-ct">${esc(title)}</div>${crumbs(it.loc.path)}`;
    if (it.kind === "case") {
      const c = it.c, w = c.where || {};
      return head(c.action_label || c.category || "case", `${c.culprit}`) + `
        <div class="row" style="gap:6px;margin:8px 0"><span class="cause c-${c.cause}">${esc(c.cause)}</span><span class="small muted">${esc(c.family)} · ${Math.round((c.confidence || 0) * 100)}% sure · ${c.incidents} incident${c.incidents > 1 ? "s" : ""}</span>
          ${c.category ? `<span class="pill ${c.category === "High" ? "critical" : c.category === "Medium" ? "warning" : ""}">${esc(c.category)} ${fmt.n(c.priority)}</span>` : ""}</div>
        <div class="small t2">${esc(c.symptom || "")}</div>
        ${c.why ? `<div class="small" style="margin-top:6px"><b>Decision:</b> ${esc(c.why)}</div>` : ""}
        ${c.check + c.hold + c.rework ? `<div class="mp-cars"><span><b style="color:var(--serious)">${fmt.n(c.check)}</b> check</span><span><b>${fmt.n(c.hold)}</b> hold</span><span><b style="color:var(--critical)">${fmt.n(c.rework)}</b> rework</span>
          ${Object.entries(w).map(([k, v]) => `<span class="muted">${fmt.n(v)} ${esc(k.replace("built after the decision - check in line", "checked in line"))}</span>`).join("")}</div>` : ""}
        ${it.loc.from ? `<div class="small muted" style="margin-top:6px">${icon("arrow")} from: ${esc(it.loc.from.label)}</div>` : ""}
        <div class="small muted" style="margin-top:4px">${c.live ? `confirmed live ${fmt.dt(c.confirmed)}` : `${fmt.dt(c.first_ts)} → ${fmt.dt(c.last_ts)} · ${esc(c.status || "")}`}</div>
        <div class="row" style="margin-top:10px;gap:6px">${c.live ? `<button class="btn sm" data-go="live">${icon("pulse")} Live line</button>` :
          `<button class="btn sm primary" data-go="investigate" data-case="${c.case_id}">${icon("search")} Investigate</button><button class="btn sm" data-go="contain" data-case="${c.case_id}">${icon("shield")} Contain</button>`}
          <button class="btn sm ghost" data-ask="Where exactly is the problem with ${esc(c.culprit)} and what do we do about it?">${icon("spark")}</button></div>`;
    }
    if (it.kind === "cars") return head(it.place === "gate" ? "shipped" : "on hold", it.title) + `
      <div class="mp-rows">${it.cases.map(([c, n]) => `<div><span>${esc(c.culprit)}</span><b>${fmt.n(n)}</b></div>`).join("")}</div>
      <div class="small muted" style="margin-top:6px">${it.place === "gate" ? "these cars left the plant - quality starts a field review" : "finished cars parked in the quarantine lane - quality releases them after the check"}</div>
      <div class="row" style="margin-top:10px"><button class="btn sm" data-go="${st.src === "live" ? "live" : "contain"}">${icon("shield")} Hold lists</button></div>`;
    if (it.kind === "maint") { const m = it.m; return head("maintenance", it.title) + `
      <dl class="kv" style="margin-top:8px"><dt>Failure mode</dt><dd>${esc(m.failure_mode)}</dd><dt>Plan</dt><dd>${esc(m.policy_rec)}</dd>
      <dt>Next due</dt><dd>${esc(m.next_due || "-")}</dd><dt>7-day risk</dt><dd>${fmt.pct(m.p_fail_7d, 1)}</dd></dl>
      <div class="small muted" style="margin-top:6px">${icon("arrow")} technicians from the maintenance workshop (WO)</div>
      <div class="row" style="margin-top:10px"><button class="btn sm" data-go="maintain">${icon("wrench")} Maintain</button></div>`; }
    if (it.kind === "repair") return head("repair", it.title) + `<div class="small t2" style="margin-top:6px">${esc(it.sub)}</div>`;
    if (it.kind === "method") { const w = it.w; return head(`method ${w.verdict}`, it.title) + `<div class="small t2" style="margin-top:6px">${esc(w.change_note)} · live ${fmt.dt(w.valid_from)}</div>
      <div class="small" style="margin-top:6px">${esc(w.headline)}</div><div class="small muted" style="margin-top:6px">${icon("arrow")} released by the engineering office (ED)</div>
      <div class="row" style="margin-top:10px"><button class="btn sm" data-go="change">${icon("clipboard")} Change</button></div>`; }
    if (it.kind === "note") return head("floor", it.title) + `<div class="mp-notes">${it.list.slice(-6).reverse().map((n) => `<div><span class="pill ${n.link === "early warning" ? "good" : ""}">${esc(n.link)}</span>
      <span class="quote">"${esc(n.quote)}"</span><span class="muted small"> - ${esc(n.author)}, ${fmt.dt(n.ts)}</span></div>`).join("")}</div>
      <div class="row" style="margin-top:10px"><button class="btn sm" data-go="notes">${icon("notes")} Shift notes</button></div>`;
    const o = it.o || {};
    return head(o.category || "flag", it.title) + `<div class="small t2" style="margin-top:6px">${esc(it.sub)}</div>${o.action ? `<div class="small" style="margin-top:6px"><b>Owner:</b> ${esc(o.action)}</div>` : ""}`;
  }
  function select(id, flyTo = false) {
    const it = st.items.find((i) => i.id === id); if (!it) return;
    st.sel = id; call.hidden = false; call.className = `mp-call sev-${it.sev}`; call.innerHTML = callHtml(it); call.dataset.id = id;
    if (flyTo || st.mode === "cluster") {
      const k = Math.max(it.loc.st ? (it.loc.equip ? 6.2 : 5) : 3, flyTo ? 0 : st.cam.s / st.fitS);
      const s = clampS(st.fitS * k);            // leave room for the callout right of the pin
      fly(it.loc.x + Math.min(190, size().w * 0.22) / s, it.loc.y - 20 / s, k);
    }
    placePins(); drawLinks(); stationStatus(); placeCall();
    $$(".mp-li", root).forEach((l) => l.classList.toggle("sel", l.dataset.id === id));
  }
  function zoneCard(zid, stn) {
    const zz = ZONE[zid]; if (!zz) return;
    const here = st.vis.filter((i) => (i.loc.st ? "a109-general-assembly" : PLACES[i.place]?.zone) === zid && (!stn || STATIONS[i.loc.st] === stn));
    st.sel = null; call.hidden = false; call.className = "mp-call"; call.dataset.id = `zone:${zid}`;
    call.dataset.x = stn ? stn.x : zz.c.x; call.dataset.y = stn ? stn.y : zz.c.y;
    call.innerHTML = `<div class="row"><span class="pill">${esc(zz.hall)}</span><span class="sp"></span><button class="x" data-close>${icon("x")}</button></div>
      <div class="mp-ct">${esc(stn ? `${stn.code} ${stn.name}` : `${zz.code} ${zz.name}`)}</div>${crumbs(stn ? [zz.hall, `${zz.code} ${zz.name}`] : [zz.hall])}
      <div class="small muted" style="margin-top:8px">${stn ? "equipment" : "in this building"}</div>
      <div class="mp-rows">${(stn ? Object.entries(EQUIP).filter(([, e]) => STATIONS[e.st] === stn).map(([k, e]) => e.name) : zz.assets).map((a) => `<div><span>${esc(a)}</span></div>`).join("") || `<div><span class="muted">-</span></div>`}</div>
      ${here.length ? `<div class="small muted" style="margin-top:8px">${here.length} item${here.length > 1 ? "s" : ""} on the map here</div><div class="mp-rows">${here.slice(0, 6).map((i) => `<div class="click" data-sel="${i.id}"><span>${esc(i.title)}</span><b class="dotc sev-${i.sev}"></b></div>`).join("")}</div>` : ""}`;
    if (!stn) fly(zz.c.x, zz.c.y, zid === "a109-general-assembly" ? 3.2 : 3.6);
    placeCall();
  }
  function placeCall() {
    if (call.hidden) return;
    let x, y;
    const it = st.items.find((i) => i.id === call.dataset.id);
    if (it) { x = it.loc.x; y = it.loc.y; } else { x = +call.dataset.x; y = +call.dataset.y; }
    const [sx, sy] = toScreen(x, y), { w, h } = size();
    const cw = call.offsetWidth || 320, ch = call.offsetHeight || 200;
    let left = sx + 24, top = sy - ch / 2 - 18;
    if (left + cw > w - 10) left = sx - cw - 24;
    top = Math.max(10, Math.min(h - ch - 10, top)); left = Math.max(10, left);
    call.style.left = `${left}px`; call.style.top = `${top}px`;      // (a transform would fight the entry animation)
  }
  call.onclick = (e) => {
    if (e.target.closest("[data-close]")) { call.hidden = true; st.sel = null; drawLinks(); placePins(); return; }
    const s = e.target.closest("[data-sel]"); if (s) select(s.dataset.sel, true);
  };

  // ---------- side panel ----------
  function drawSide() {
    const vis = st.vis, open = vis.filter((i) => i.open);
    const cases = vis.filter((i) => i.kind === "case");
    const cars = vis.filter((i) => i.kind === "cars").reduce((a, b) => a + b.n, 0);
    const where = {};
    open.filter((i) => i.kind !== "note").forEach((i) => { const k = i.loc.st ? STATIONS[i.loc.st].code : PLACES[i.place]?.label.split(" (")[0].split(" - ")[0]; where[k] = (where[k] || 0) + 1; });
    const src = st.src === "live" ? (st.live?.sim ? `live · ${fmt.dt(st.live.sim)}` : "live line not running") : st.replayT != null ? `replay · ${fmt.dt(new Date(st.replayT))}` : "this week";
    $("#mpSum", root).innerHTML = `<div class="row"><div><div class="small muted">${esc(src)}</div><div class="mp-big">${cases.filter((c) => c.open).length}<small> open problems</small></div></div><span class="sp"></span>
        ${cars ? `<div style="text-align:right"><div class="small muted">cars waiting</div><div class="mp-big" style="color:var(--serious)">${fmt.n(cars)}</div></div>` : ""}</div>
      <div class="mp-where">${Object.entries(where).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span class="pill">${icon("pin")} ${esc(k)} <b>${v}</b></span>`).join("") || `<span class="small muted">nothing open</span>`}</div>`;
    const list = [...vis].sort((a, b) => (b.open - a.open) || (SEV_RANK[b.sev] - SEV_RANK[a.sev]) || String(b.ts).localeCompare(String(a.ts)));
    $("#mpCount", root).textContent = `${vis.length} items`;
    $("#mpList", root).innerHTML = list.map((it) => `<div class="mp-li ${it.open ? "" : "done"} ${it.id === st.sel ? "sel" : ""}" data-id="${it.id}">
        <span class="mp-li-i" style="--pc:${it.kind === "case" ? CAUSE_COLOR[it.cause] : KIND_COL[it.kind]}">${icon(it.kind === "case" ? CAUSE_ICON[it.cause] || "search" : KIND_ICON[it.kind])}</span>
        <div style="min-width:0"><div class="mp-li-t"><b class="dotc sev-${it.sev}"></b>${esc(it.title)}</div><div class="mp-li-p">${esc(it.loc.path.slice(-2).join(" › "))}</div></div></div>`).join("")
      || `<div class="empty">${st.src === "live" ? "Start the live line - problems appear here and on the map as the models find them." : "Nothing in the selected layers."}</div>`;
  }
  $("#mpList", root).onclick = (e) => { const l = e.target.closest("[data-id]"); if (l) select(l.dataset.id, true); };

  // ---------- data ----------
  function setItems(items, announce) {
    const before = new Set(st.items.map((i) => i.id));
    items.forEach((it) => (it.loc = locate(it)));
    st.items = items; st.mode = null; apply(); drawSide();
    if (st.sel && !items.find((i) => i.id === st.sel)) { st.sel = null; call.hidden = true; }
    if (announce) {
      const fresh = visible().filter((i) => !before.has(i.id));
      fresh.forEach((i) => ripple(i.loc.x, i.loc.y, i.sev));
      const top = fresh.filter((i) => i.kind === "case" || SEV_RANK[i.sev] >= 3).sort((a, b) => SEV_RANK[b.sev] - SEV_RANK[a.sev])[0];
      if (top) { if (st.follow) select(top.id, true); beep(top.sev); }
    }
  }
  async function loadWeek() {
    if (!st.week) st.week = await ctx.api.get("/api/map");
    setItems(weekItems(st.week), false);
  }
  const TICK = { signal: "Signal checker", cause: "Cause finder", contain: "Containment", impact: "Impact ranker", floor: "Floor listener", maint: "Maintenance",
    method: "Method checker", change: "Change manager", wi: "Work instruction", people: "People model", mes: "Line", app: "Engineer" };
  function liveEvents(evs) {
    for (const e of evs) {
      const s = STATIONS[e.station] ? e.station : null;
      let p = null;
      if (e.node === "maint" && /repair/i.test(e.title)) { const eq = equipOf(e.title + " " + e.detail, s); p = eq ? EQUIP[eq] : s && STATIONS[s]; }
      else if (["change", "method"].includes(e.node) && e.sev !== "good" && !s) p = PLACES.eng;
      else if (s) p = e.node === "floor" ? { x: STATIONS[s].x + 22, y: STATIONS[s].y + 26 } : STATIONS[s];
      else if (e.node === "floor" || e.node === "people") p = { x: 428, y: 346 };
      if (p) ripple(p.x, p.y, e.sev);
    }
    const last = [...evs].reverse().find((e) => e.sev !== "info") || evs[evs.length - 1];
    if (last) {
      const t = $("#mpTick", root); t.hidden = false;
      t.innerHTML = `<span class="pill ${last.sev === "good" ? "good" : last.sev}">${esc(TICK[last.node] || last.node)}</span><b>${esc(last.title)}</b><span class="muted">${esc(fmt.dt(last.t))}</span>`;
      t.classList.remove("in"); void t.offsetWidth; t.classList.add("in");
    }
  }
  async function pollLive() {
    if (!st.alive) return;
    clearTimeout(st.timer);
    try {
      const L = await ctx.api.get(`/api/live/state?ev=${st.ev}&pt=999999999`, { fresh: true });
      $("#mpLiveDot", root).hidden = L.status !== "running";
      if (st.src === "live") {
        st.live = L;
        const evs = (L.events || []).filter((e) => e.seq > st.ev);
        if (L.seq < st.ev) st.ev = 0;                      // the live week was restarted
        if (evs.length) { st.ev = Math.max(...evs.map((e) => e.seq)); if (st.primed) liveEvents(evs.slice(-8)); }
        setItems(liveItems(L), st.primed);
        st.primed = true;
        try { L.status === "running" ? svg.unpauseAnimations() : svg.pauseAnimations(); } catch (e) { /* */ }
      }
      st.timer = setTimeout(pollLive, st.src === "live" ? (L.status === "running" ? 800 : 2000) : 4000);
    } catch (e) { st.timer = setTimeout(pollLive, 4000); }
  }
  async function setSrc(src) {
    st.src = src; st.sel = null; call.hidden = true; $("#mpTick", root).hidden = true;
    $$("#mpSrc button", root).forEach((b) => b.classList.toggle("on", b.dataset.src === src));
    try { svg.unpauseAnimations(); } catch (e) { /* */ }
    if (src === "week") { await loadWeek(); } else { st.ev = 0; st.primed = false; st.items = []; await pollLive(); }
  }
  $("#mpSrc", root).onclick = (e) => { const b = e.target.closest("[data-src]"); if (b && b.dataset.src !== st.src) setSrc(b.dataset.src); };
  $("#mpLayers", root).onclick = (e) => {
    const b = e.target.closest("[data-layer]"); if (!b) return;
    const k = b.dataset.layer; st.layers.has(k) ? st.layers.delete(k) : st.layers.add(k); b.classList.toggle("on", st.layers.has(k));
    $("#gFlows", root).style.display = st.layers.has("flows") ? "" : "none";
    st.mode = null; apply(); drawSide();
  };
  const setBtn = (id, on, a, b) => { const el = $(id, root); el.classList.toggle("on", on); if (a) el.innerHTML = on ? a : b; };
  $("#mpFollow", root).onclick = () => { st.follow = !st.follow; setBtn("#mpFollow", st.follow); };
  $("#mpBlue", root).onclick = () => { st.blueprint = !st.blueprint; svg.classList.toggle("blue", st.blueprint); setBtn("#mpBlue", st.blueprint); };
  $("#mpSound", root).onclick = () => { st.sound = !st.sound; setBtn("#mpSound", st.sound, `${icon("bell")} Sound on`, `${icon("bell")} Sound off`); if (st.sound) beep("warning"); };
  setBtn("#mpFollow", st.follow); setBtn("#mpBlue", st.blueprint);

  // alarm tones (Web Audio - no files), only after the user switched sound on
  function beep(sev) {
    if (!st.sound || !SEV_RANK[sev] || SEV_RANK[sev] < 2) return;
    try {
      const A = st.audio || (st.audio = new (window.AudioContext || window.webkitAudioContext)());
      const n = sev === "critical" ? 3 : sev === "serious" ? 2 : 1, f = sev === "critical" ? 880 : sev === "serious" ? 740 : 560;
      for (let i = 0; i < n; i++) {
        const o = A.createOscillator(), g = A.createGain(), t = A.currentTime + i * 0.2;
        o.type = "triangle"; o.frequency.value = f; g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16); o.connect(g).connect(A.destination); o.start(t); o.stop(t + 0.18);
      }
    } catch (e) { /* no audio */ }
  }

  // week replay (R) drives the map too: pins appear when their problem started
  const onReplay = (e) => {
    if (st.src !== "week") return;
    const t = e.detail ? e.detail.t : null;
    const was = st.replayT;
    st.replayT = t;
    if (t == null) { if (was != null) { st.mode = null; apply(); drawSide(); } return; }
    const before = new Set(st.vis.map((i) => i.id));
    st.mode = null; apply(); drawSide();
    if (was != null && t >= was) {
      const fresh = st.vis.filter((i) => !before.has(i.id));
      fresh.forEach((i) => ripple(i.loc.x, i.loc.y, i.sev));
      const top = fresh.filter((i) => i.kind === "case").sort((a, b) => SEV_RANK[b.sev] - SEV_RANK[a.sev])[0];
      if (top) { if (st.follow) select(top.id, true); beep(top.sev); }
    }
  };
  window.addEventListener("replay", onReplay);
  ctx.cleanup.push(() => { st.alive = false; clearTimeout(st.timer); cancelAnimationFrame(st.anim); ro.disconnect(); window.removeEventListener("replay", onReplay); try { st.audio?.close(); } catch (e) { /* */ } });

  // ---------- start ----------
  fit(); st.cam = { cx: MAP_W / 2, cy: MAP_H / 2, s: st.fitS };
  let liveOn = false;
  try { const L = await ctx.api.get("/api/live/state?ev=999999999&pt=999999999", { fresh: true }); liveOn = ["running", "paused", "finishing"].includes(L.status); $("#mpLiveDot", root).hidden = L.status !== "running"; } catch (e) { /* */ }
  const src = params.src || (params.case || params.eq ? "week" : liveOn ? "live" : "week");
  await setSrc(src);
  if (src === "week") pollLive();
  if (params.case) setTimeout(() => select(`case-${params.case}`, true), 250);
  else if (params.eq) { const m = st.items.find((i) => (i.kind === "maint" || i.kind === "repair") && i.loc.equip === params.eq); if (m) setTimeout(() => select(m.id, true), 250); }
  else setTimeout(() => fly(470, 420, 1.55, 1400), 350);
}
