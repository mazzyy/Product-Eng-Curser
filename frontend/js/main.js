// App shell: router, navigation, alert center, toasts, copilot drawer, week replay.
import { api } from "./api.js";
import { $, $$, esc, fmt, icon, store, toast, initTooltip, SEV_ICON, parse, skeleton, MARK } from "./util.js";

const PAGES = {
  today:       { title: "Today", sub: "Results, priorities and what the last shifts said", icon: "today" },
  investigate: { title: "Investigate", sub: "Is a drop real - and is it the machine, the people, the method or the station?", icon: "search" },
  contain:     { title: "Contain", sub: "Which cars wait for a check, which move on - and should the line stop?", icon: "shield" },
  change:      { title: "Change", sub: "Check, approve, pilot, release - so the next shift has the current method", icon: "clipboard" },
  maintain:    { title: "Maintain", sub: "How often each machine is checked, and what is due next", icon: "wrench" },
  capacity:    { title: "Capacity", sub: "What the line can still produce - the number for planning", icon: "gauge" },
  notes:       { title: "Shift notes", sub: "What people wrote, read by the floor listener", icon: "notes" },
  map:         { title: "Factory map", sub: "Where on the site each problem happened - station, machine, source", icon: "pin", sec: "The system" },
  live:        { title: "Live line", sub: "A new week streamed car by car - every model runs as the data arrives", icon: "pulse" },
  how:         { title: "How it works", sub: "From the station data to the engineer's decision - the workflow of all models", icon: "flow" },
};
const ORDER = Object.keys(PAGES);
const state = { meta: null, alerts: [], acks: new Set(store.get("acks", [])), page: null, params: {} };

// ---------------------------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------------------------
export function go(page, params = {}) {
  const qs = new URLSearchParams(params).toString();
  location.hash = `#/${page}${qs ? "?" + qs : ""}`;
}
function parseHash() {
  const [p, q] = location.hash.replace(/^#\/?/, "").split("?");
  return { page: PAGES[p] ? p : "today", params: Object.fromEntries(new URLSearchParams(q || "")) };
}
async function route() {
  const { page, params } = parseHash();
  const same = state.page === page;
  state.page = page; state.params = params;
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
  $("#pageTitle").textContent = PAGES[page].title;
  $("#pageSub").textContent = PAGES[page].sub;
  const root = $("#page");
  ctx.cleanup.splice(0).forEach((f) => { try { f(); } catch (e) { /* ignore */ } });
  root.onclick = root.onchange = root.oninput = null;
  if (!same) { root.innerHTML = skeleton(3, 130); $("#content").scrollTop = 0; }
  try {
    const mod = await import(`./pages/${page}.js`);
    if (state.page !== page) return;
    await mod.render(root, params, ctx);
  } catch (e) {
    console.error(e);
    root.innerHTML = `<div class="card empty">${icon("serious")} Could not load this page: ${esc(e.message)}<br>
      <span class="small">Is <span class="mono">python app.py</span> running and the pipeline done?</span></div>`;
  }
}

// ---------------------------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------------------------
async function loadAlerts(first = false) {
  state.alerts = await api.get("/api/alerts", { fresh: true });
  renderAlerts();
  if (first && !sessionStorage.getItem("pc-toasted")) {
    try { sessionStorage.setItem("pc-toasted", "1"); } catch (e) { /* ignore */ }
    state.alerts.filter((a) => a.severity === "critical" && !state.acks.has(a.id)).slice(0, 3).forEach((a, i) =>
      setTimeout(() => toast({ severity: a.severity, title: a.title, detail: a.detail, onClick: () => openAlert(a) }), 700 + i * 450));
    const n = state.alerts.filter((a) => a.severity === "serious" && !state.acks.has(a.id)).length;
    if (n) setTimeout(() => toast({ severity: "serious", title: `${n} more alerts need a look`, detail: "Open the alert center to see them all",
      onClick: () => openDrawer("alertsDrawer") }), 2200);
    setTimeout(() => $("#bellBtn").classList.add("shake"), 700);
  }
}
function renderAlerts() {
  const open = state.alerts.filter((a) => !state.acks.has(a.id) && a.severity !== "info");
  const b = $("#bellBadge");
  b.hidden = !open.length; b.textContent = open.length;
  $("#alertsSub").textContent = `${open.length} open · ${state.alerts.length} total`;
  const groups = ["critical", "serious", "warning", "info"];
  const label = { critical: "Critical - act now", serious: "Serious", warning: "Watch", info: "For information" };
  $("#alertsList").innerHTML = groups.map((g) => {
    const list = state.alerts.filter((a) => a.severity === g);
    if (!list.length) return "";
    return `<div class="al-g">${label[g]} · ${list.length}</div>` + list.map((a) => `
      <div class="al ${state.acks.has(a.id) ? "ack" : ""}" data-id="${esc(a.id)}">
        <div class="ic ${a.severity}">${icon(SEV_ICON[a.severity])}</div>
        <div><div class="li-t">${esc(a.title)}</div><div class="li-d">${esc(a.detail)}</div>
          <div class="small muted" style="margin-top:3px">${PAGES[a.page]?.title || ""}</div></div>
        <button class="btn sm ghost" data-ack="${esc(a.id)}" title="Acknowledge">${state.acks.has(a.id) ? icon("check") : "Ack"}</button>
      </div>`).join("");
  }).join("");
  // nav badges: open alerts per page
  ORDER.forEach((p) => {
    const n = open.filter((a) => a.page === p && (a.severity === "critical" || a.severity === "serious")).length;
    const el = $(`#nav a[data-page="${p}"] .badge`);
    if (el) { el.hidden = !n; el.textContent = n; }
  });
  const st = { st012: "ok", st013: "ok" };
  state.alerts.forEach((a) => {
    const s = (String(a.target || "") + " " + a.title).toLowerCase().match(/st01[23]/);
    if (s && a.severity === "critical") st[s[0]] = "crit"; else if (s && a.severity === "serious" && st[s[0]] === "ok") st[s[0]] = "warn";
  });
  $("#railFoot").innerHTML = `
    <div class="st-row"><span class="dot ${st.st012 === "crit" ? "crit" : st.st012 === "warn" ? "warn" : ""}"></span>ST012 subframe bolt-down<span class="sp"></span><span class="small muted">bottleneck</span></div>
    <div class="st-row"><span class="dot ${st.st013 === "crit" ? "crit" : st.st013 === "warn" ? "warn" : ""}"></span>ST013 coolant fill & leak test</div>
    <div class="role">Production engineer · crews A / B / C · takt 50 s</div>`;
}
function openAlert(a) {
  closeDrawers();
  const p = {};
  if (a.page === "contain" && typeof a.target === "number") p.case = a.target;
  if (a.page === "today" && typeof a.target === "number") { go("investigate", { case: a.target }); return; }
  if (a.page === "maintain" && a.target) p.eq = a.target;
  if (a.page === "notes" && a.target) p.note = a.target;
  if (a.page === "change" && a.target) p.station = a.target;
  go(a.page, p);
}
function ack(id) { state.acks.add(id); store.set("acks", [...state.acks]); renderAlerts(); }

// ---------------------------------------------------------------------------------------------
// Drawers
// ---------------------------------------------------------------------------------------------
function openDrawer(id) { closeDrawers(); $("#" + id).classList.add("on"); $("#scrim").classList.add("on"); }
function closeDrawers() { $$(".drawer").forEach((d) => d.classList.remove("on")); $("#scrim").classList.remove("on"); }

// ---------------------------------------------------------------------------------------------
// Copilot
// ---------------------------------------------------------------------------------------------
const cp = { session: null, busy: false, presets: [] };
function linkify(s) {
  return esc(s)
    .replace(/\b[Cc]ase #?(\d+)/g, '<span class="link-chip" data-go="investigate" data-case="$1">case #$1</span>')
    .replace(/\b(WI-0\d\d v\d+)/g, '<span class="link-chip" data-go="change">$1</span>')
    .replace(/\b((?:NR|FX|CF|LT|SC)-0\d\d)\b/g, '<span class="link-chip" data-go="maintain" data-eq="$1">$1</span>')
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}
function renderAnswer(text) {
  const lines = String(text || "").split("\n").map((l) => l.trimEnd());
  let html = "", inList = false, first = true;
  const close = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    const l = raw.trim();
    if (!l) { close(); continue; }
    const head = l.match(/^(Why|Next|Answer|Risks?|Evidence)\s*:\s*(.*)$/i);
    if (first && !head) { html += `<div class="hl">${linkify(l.replace(/^[-•*]\s*/, ""))}</div>`; first = false; continue; }
    first = false;
    if (head) { close(); html += `<h4>${esc(head[1])}</h4>`; if (head[2]) html += `<div>${linkify(head[2])}</div>`; continue; }
    const li = l.match(/^[-•*]\s+(.*)$/) || l.match(/^\d+[.)]\s+(.*)$/);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${linkify(li[1])}</li>`; continue; }
    close(); html += `<div>${linkify(l)}</div>`;
  }
  close();
  return html;
}
function cpBackendLabel(b) {
  if (!b) return "";
  if (b.startsWith("azure")) return `${icon("spark")} Live · Azure GPT-5`;
  if (b.startsWith("cache")) return `${icon("clock")} Saved GPT-5 answer (replayed)`;
  return `${icon("info")} Offline - tool results without the LLM`;
}
function cpIntro() {
  const b = state.meta?.copilot;
  $("#cpBackend").innerHTML = b ? `${b.backend === "azure" ? "Azure " + esc(b.model) + " · live" : "offline mode"} · ${b.cached_answers} saved answers` : "";
  $("#cpBody").innerHTML = `
    <div class="msg ai"><div class="hl">Good morning - this is TAKT. I answer from the nine models and show my work.</div>
      <div class="t2">Ask anything about the line. Every fact comes from a tool, with the incident, case, change or rule ID.
      I never approve, stop or release anything - I tell you who does.</div></div>
    <div class="presets">${cp.presets.map((q) => `<button class="preset" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
}
export async function ask(q) {
  q = String(q || "").trim();
  if (!q || cp.busy) return;
  openDrawer("copilot");
  const body = $("#cpBody");
  $$(".presets", body).forEach((p) => p.remove());
  body.insertAdjacentHTML("beforeend", `<div class="msg me">${esc(q)}</div><div class="typing"><i></i><i></i><i></i><span style="margin-left:6px">checking the models…</span></div>`);
  const scroller = body.parentElement;
  scroller.scrollTop = scroller.scrollHeight;
  cp.busy = true; $("#cpSend").disabled = true;
  try {
    const r = await api.post("/api/copilot/ask", { question: q, session: cp.session });
    cp.session = r.session;
    $(".typing", body)?.remove();
    const id = "m" + Math.random().toString(36).slice(2, 8);
    body.insertAdjacentHTML("beforeend", `<div class="msg ai" id="${id}">${renderAnswer(r.answer)}
      <div class="msg-f"><span>${cpBackendLabel(r.backend)}</span><span>· ${fmt.n(r.seconds, 1)} s</span>
        ${r.trace?.length ? `<span class="sp"></span><button class="btn sm ghost" data-trace="${id}">${icon("eye")} Show work (${r.trace.length} tools)</button>` : ""}</div>
      <div class="trace" id="${id}-t">${(r.trace || []).map((t) => `<div class="tr" data-tip="${esc(t.result)}"><b>${esc(t.tool)}</b>(${esc(JSON.stringify(t.args))}) → ${esc(t.result)}</div>`).join("")}</div></div>`);
    const m = $("#" + id);
    [...m.children].forEach((c, i) => { c.style.animation = `fadeUp .35s ${i * 70}ms both`; });
    scroller.scrollTop = scroller.scrollHeight;
  } catch (e) {
    $(".typing", body)?.remove();
    body.insertAdjacentHTML("beforeend", `<div class="msg ai"><div class="hl">Could not answer</div><div class="t2">${esc(e.message)}</div></div>`);
  } finally { cp.busy = false; $("#cpSend").disabled = false; }
}

// ---------------------------------------------------------------------------------------------
// Replay the week
// ---------------------------------------------------------------------------------------------
const rp = { data: null, t0: 0, t1: 0, t: 0, playing: false, speed: 1, fired: new Set(), last: 0, raf: 0, on: false };
const WEEK_SECONDS = 60;             // one simulated week in 60 s at 1x
async function openReplay() {
  if (!rp.data) {
    rp.data = await api.get("/api/replay");
    rp.t0 = +parse(rp.data.start); rp.t1 = +parse(rp.data.end);
  }
  rp.on = true; rp.t = rp.t0; rp.fired = new Set(); rp.speed = rp.speed || 1;
  renderTransport(); $("#transport").classList.add("on");
  play(true);
}
function closeReplay() {
  play(false); rp.on = false; $("#transport").classList.remove("on");
  window.dispatchEvent(new CustomEvent("replay", { detail: null }));
  updateClock();
}
function play(on) {
  rp.playing = on;
  cancelAnimationFrame(rp.raf);
  const b = $("#tpPlay"); if (b) b.innerHTML = icon(on ? "pause" : "play");
  if (on) { if (rp.t >= rp.t1) { rp.t = rp.t0; rp.fired = new Set(); } rp.last = performance.now(); rp.raf = requestAnimationFrame(tick); }
}
function tick(now) {
  const dt = now - rp.last; rp.last = now;
  rp.t = Math.min(rp.t1, rp.t + dt * ((rp.t1 - rp.t0) / (WEEK_SECONDS * 1000)) * rp.speed);
  fireEvents(true);
  paintTransport();
  if (rp.t >= rp.t1) { play(false); toast({ severity: "good", title: "Replay finished", detail: "That was the week. Everything you saw is in the pages now." }); return; }
  if (rp.playing) rp.raf = requestAnimationFrame(tick);
}
function fireEvents(show) {
  rp.data.events.forEach((e, i) => {
    if (rp.fired.has(i) || +parse(e.ts) > rp.t) return;
    rp.fired.add(i);
    if (!show) return;
    $("#tpTicker").innerHTML = `<span class="pill ${e.severity}">${icon(SEV_ICON[e.severity] || "info")}${fmt.dt(e.ts)}</span><b>${esc(e.title)}</b><span class="muted">${esc(e.detail)}</span>`;
    if (e.severity !== "info") {
      toast({ severity: e.severity, title: e.title, detail: e.detail, timeout: e.severity === "critical" ? 9000 : 5000,
        onClick: () => { play(false); go(e.page, e.target != null ? { [e.page === "contain" || e.page === "investigate" ? "case" : e.page === "notes" ? "note" : "station"]: e.target } : {}); } });
      if (e.severity === "critical") { const b = $("#bellBtn"); b.classList.remove("shake"); void b.offsetWidth; b.classList.add("shake"); }
    }
  });
}
function renderTransport() {
  const span = rp.t1 - rp.t0;
  const days = []; for (let t = rp.t0; t < rp.t1; t += 864e5) days.push(fmt.day(new Date(t)));
  $("#transport").innerHTML = `
    <button class="btn icon primary" id="tpPlay" title="Play / pause (space)">${icon("pause")}</button>
    <div class="tp-time" id="tpTime"></div>
    <div class="tp-track" id="tpTrack"><div class="tp-rail"></div><div class="tp-fill" id="tpFill"></div>
      ${rp.data.events.map((e) => `<div class="tp-ev ${e.severity}" style="left:${((+parse(e.ts) - rp.t0) / span) * 100}%" data-tip="<b>${esc(fmt.dt(e.ts))}</b> ${esc(e.title)}"></div>`).join("")}
      <div class="tp-knob" id="tpKnob"></div><div class="tp-days">${days.map((d) => `<span>${d}</span>`).join("")}</div></div>
    <div class="row">
      <div class="chips">${[0.5, 1, 2, 4].map((s) => `<button class="chip ${s === rp.speed ? "on" : ""}" data-speed="${s}">${s}×</button>`).join("")}</div>
      <div class="tp-cars"><b id="tpCars">0</b> <span class="muted small">/ 7,500 cars</span><small id="tpShift"></small></div>
      <button class="btn icon ghost" id="tpClose" title="Close replay">${icon("x")}</button></div>
    <div class="tp-ticker" id="tpTicker"><span class="muted">Replaying the week: problems, floor notes, method changes and decisions as they happened.</span></div>`;
  $("#tpPlay").onclick = () => play(!rp.playing);
  $("#tpClose").onclick = closeReplay;
  $$("#transport [data-speed]").forEach((b) => (b.onclick = () => { rp.speed = +b.dataset.speed; $$("#transport [data-speed]").forEach((x) => x.classList.toggle("on", x === b)); }));
  $("#tpTrack").onclick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    rp.t = rp.t0 + ((e.clientX - r.left) / r.width) * (rp.t1 - rp.t0);
    rp.fired = new Set(); fireEvents(false); paintTransport();
  };
  paintTransport();
}
function paintTransport() {
  const k = (rp.t - rp.t0) / (rp.t1 - rp.t0);
  $("#tpFill").style.width = k * 100 + "%"; $("#tpKnob").style.left = k * 100 + "%";
  const d = new Date(rp.t);
  $("#tpTime").innerHTML = `${fmt.day(d)} ${fmt.time(d)}<small>shift ${fmt.shift(d)} · replay</small>`;
  const prod = rp.data.production; let cars = 0;
  for (const p of prod) { if (+parse(p.t) <= rp.t) cars = p.cum; else break; }
  $("#tpCars").textContent = fmt.n(cars);
  $("#tpShift").textContent = cars >= 7500 ? "weekly target reached" : `${fmt.pct(cars / 7500)} of the weekly target`;
  $("#clock").innerHTML = `<span class="pill info">REPLAY</span><b>${fmt.day(d)} ${fmt.time(d)}</b> · shift ${fmt.shift(d)}`;
  window.dispatchEvent(new CustomEvent("replay", { detail: { t: rp.t, t0: rp.t0, t1: rp.t1 } }));
}

// ---------------------------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------------------------
function updateClock() {
  if (!state.meta) return;
  const n = state.meta.now;
  $("#clock").innerHTML = `${icon("clock")}<b>${fmt.day(n)} ${fmt.time(n)}</b> · shift ${fmt.shift(n)} starting`;
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("takt-theme", t); } catch (e) { /* ignore */ }
  $("#themeBtn").innerHTML = icon(t === "light" ? "moon" : "sun");
}
const ctx = { api, go, ask, toast, state, cleanup: [], refreshAlerts: () => loadAlerts(false),
  setClock: (html) => { if (html == null) updateClock(); else $("#clock").innerHTML = html; },
  setLive: (on) => { const d = $("#navLive"); if (d) d.hidden = !on; } };

async function boot() {
  initTooltip();
  $("#brandMark").innerHTML = MARK; $("#cpMark").innerHTML = MARK;
  $("#nav").innerHTML = ORDER.map((p, i) => `${PAGES[p].sec ? `<div class="nav-sec">${PAGES[p].sec}</div>` : ""}<a href="#/${p}" data-page="${p}">${icon(PAGES[p].icon)}<span>${PAGES[p].title}</span>
    <span class="badge" hidden>0</span>${p === "live" ? `<span class="live-dot" id="navLive" hidden></span>` : ""}<span class="k">${(i + 1) % 10}</span></a>`).join("");
  $("#replayBtn").innerHTML = `${icon("play")} Replay week`;
  $("#bellBtn").innerHTML = icon("bell");
  $("#copilotBtn").innerHTML = `${icon("spark")} Ask copilot <span class="kbd">/</span>`;
  $("#cpSend").innerHTML = icon("send");
  $$("[data-close]").forEach((b) => { b.innerHTML = icon("x"); b.onclick = closeDrawers; });
  setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

  $("#themeBtn").onclick = () => setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  $("#bellBtn").onclick = () => openDrawer("alertsDrawer");
  $("#bellBtn").addEventListener("animationend", (e) => e.currentTarget.classList.remove("shake"));
  $("#copilotBtn").onclick = () => openDrawer("copilot");
  $("#replayBtn").onclick = () => (rp.on ? closeReplay() : openReplay());
  $("#scrim").onclick = closeDrawers;
  $("#ackAll").onclick = () => { state.alerts.forEach((a) => state.acks.add(a.id)); store.set("acks", [...state.acks]); renderAlerts(); };
  $("#alertsList").onclick = (e) => {
    const a = e.target.closest("[data-ack]"); if (a) { e.stopPropagation(); ack(a.dataset.ack); return; }
    const row = e.target.closest(".al"); if (row) openAlert(state.alerts.find((x) => x.id === row.dataset.id));
  };
  $("#cpSend").onclick = () => { const v = $("#cpInput").value; $("#cpInput").value = ""; ask(v); };
  $("#cpInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#cpSend").click(); } });
  $("#cpNew").onclick = () => { cp.session = null; cpIntro(); };
  document.addEventListener("click", (e) => {
    const p = e.target.closest(".preset"); if (p) { ask(p.dataset.q); return; }
    const t = e.target.closest("[data-trace]"); if (t) { $("#" + t.dataset.trace + "-t").classList.toggle("on"); return; }
    const l = e.target.closest("[data-go]");
    if (l) { closeDrawers(); const prm = {}; if (l.dataset.case) prm.case = l.dataset.case; if (l.dataset.eq) prm.eq = l.dataset.eq; go(l.dataset.go, prm); }
    const q = e.target.closest("[data-ask]"); if (q) ask(q.dataset.ask);
  });
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
    if (e.key === "Escape") { closeDrawers(); return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); openDrawer("copilot"); setTimeout(() => $("#cpInput").focus(), 250); }
    else if (e.key === "r" || e.key === "R") { rp.on ? closeReplay() : openReplay(); }
    else if (e.key === " " && rp.on) { e.preventDefault(); play(!rp.playing); }
    else if (/^[0-9]$/.test(e.key) && ORDER[(+e.key + 9) % 10]) go(ORDER[(+e.key + 9) % 10]);
  });
  window.addEventListener("hashchange", route);

  try {
    state.meta = await api.get("/api/meta");
    cp.presets = (await api.get("/api/copilot/presets")).questions;
  } catch (e) {
    $("#page").innerHTML = `<div class="card empty">The backend is not reachable. Start it with <span class="mono">python app.py</span>.</div>`;
    return;
  }
  updateClock(); cpIntro();
  route();
  loadAlerts(true);
}
boot();
