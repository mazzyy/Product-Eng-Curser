// Live line - a simulated week streamed car by car; every model runs on the data as it arrives (live.py).
import { $, $$, esc, fmt, icon, parse, SEV_ICON } from "../util.js";
import { diagram, trace, setCounts } from "../diagram.js";

const SPEED = { 900: "1 h = 4 s", 1800: "1 h = 2 s", 3600: "1 h = 1 s", 7200: "1 h = ½ s" };
const WIN_S = 6 * 3600;                 // the charts show the last 6 simulated hours
const TAKT_CARS_H = 72;                 // 50 s takt
const ACTIVE = ["preparing", "running", "paused", "finishing"];
const STATUS = { idle: "Not started", preparing: "Preparing the week…", running: "Live", paused: "Paused", finishing: "Last analysis…",
  finished: "Week complete", stopped: "Stopped", error: "Error" };
const NODE_LABEL = { mes: "Station data", notes: "Floor notes", wi: "Work instr.", mlog: "Maint. log", signal: "Signal checker",
  people: "People model", floor: "Floor listener", method: "Method checker", cause: "Cause finder", impact: "Impact ranker",
  contain: "Containment", change: "Change manager", maint: "Maintenance", copilot: "Copilot", app: "Engineer" };
const CAUSE_ICON = { machine: "machine", people: "users", method: "file", station: "box" };

function css() {
  const cs = getComputedStyle(document.documentElement), v = (n) => cs.getPropertyValue(n).trim();
  return { text: v("--text"), t2: v("--text-2"), muted: v("--muted"), faint: v("--faint"), border: v("--border"),
    good: v("--good"), goodSoft: v("--good-soft"), crit: v("--critical"), serious: v("--serious"), warn: v("--warning"),
    accent: v("--accent"), surface: v("--surface"), s2: v("--surface-2") };
}

// ---------------------------------------------------------------------------------------------
// station chart (canvas): key signal with spec limits, flagged cars, and cycle time vs takt
// ---------------------------------------------------------------------------------------------
function drawStation(cv, pts, spec, tNow, tEnd, C) {
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const L = 46, R = 10, T = 8, MH = H - 66, CT = T + MH + 16, CH = 34, PW = W - L - R;
  const t0 = tEnd - WIN_S;
  const X = (t) => L + ((t - t0) / WIN_S) * PW;
  const span = spec.usl - spec.lsl, lo = spec.lsl - 0.2 * span, hi = spec.usl + 0.2 * span;
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * MH;
  g.font = "10.5px -apple-system, Segoe UI, Roboto, sans-serif";
  // spec band + limits
  g.fillStyle = C.goodSoft; g.fillRect(L, Y(spec.usl), PW, Y(spec.lsl) - Y(spec.usl));
  g.strokeStyle = C.crit; g.setLineDash([4, 4]); g.lineWidth = 1;
  for (const [v, lab] of [[spec.usl, "USL"], [spec.lsl, "LSL"]]) {
    g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(L + PW, Y(v)); g.stroke();
    g.fillStyle = C.crit; g.textAlign = "right"; g.fillText(`${lab} ${v}`, L - 5, Y(v) + 3.5);
  }
  g.setLineDash([2, 5]); g.strokeStyle = C.muted;
  g.beginPath(); g.moveTo(L, Y(spec.nominal)); g.lineTo(L + PW, Y(spec.nominal)); g.stroke();
  g.fillStyle = C.muted; g.fillText(String(spec.nominal), L - 5, Y(spec.nominal) + 3.5);
  g.setLineDash([]);
  // hour grid
  g.textAlign = "center";
  const startMs = +parse(spec.start);
  for (let h = Math.ceil(t0 / 3600); h * 3600 <= tNow; h++) {
    const x = X(h * 3600);
    g.strokeStyle = C.border; g.globalAlpha = 0.7; g.beginPath(); g.moveTo(x, T); g.lineTo(x, CT + CH); g.stroke(); g.globalAlpha = 1;
    const d = new Date(startMs + h * 3600e3);
    g.fillStyle = C.muted; g.fillText(`${String(d.getHours()).padStart(2, "0")}:00`, x, H - 3);
  }
  g.save(); g.beginPath(); g.rect(L, T, PW, CT + CH - T); g.clip();
  const vis = pts.filter((p) => p[1] >= t0 - 60 && p[1] <= tNow);
  // every car
  g.fillStyle = C.t2; g.globalAlpha = 0.45;
  for (const p of vis) if (p[2] != null && !p[4]) g.fillRect(X(p[1]) - 1, Math.max(T, Math.min(T + MH, Y(p[2]))) - 1, 2, 2);
  g.globalAlpha = 1;
  // rolling median of the last 25 cars
  g.strokeStyle = C.accent; g.lineWidth = 1.8; g.beginPath();
  let started = false; const win = [];
  for (const p of vis) {
    if (p[2] == null) continue;
    win.push(p[2]); if (win.length > 25) win.shift();
    if (win.length < 8) continue;
    const m = [...win].sort((a, b) => a - b)[Math.floor(win.length / 2)];
    const x = X(p[1]), y = Y(m);
    if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
  }
  g.stroke(); g.lineWidth = 1;
  // flagged cars and rejects
  for (const p of vis) {
    if (!p[4] && !p[5]) continue;
    const x = X(p[1]), v = p[2] == null ? spec.nominal : p[2], y = Math.max(T + 3, Math.min(T + MH - 3, Y(v)));
    if (p[5]) { g.strokeStyle = C.crit; g.lineWidth = 1.6; g.beginPath(); g.moveTo(x - 3, y - 3); g.lineTo(x + 3, y + 3); g.moveTo(x + 3, y - 3); g.lineTo(x - 3, y + 3); g.stroke(); g.lineWidth = 1; }
    else { g.fillStyle = C.serious; g.beginPath(); g.arc(x, y, 3, 0, 7); g.fill(); }
  }
  // cycle time strip vs takt
  const CY = (s) => CT + CH - (Math.min(Math.max(s, 30), 90) - 30) / 60 * CH;
  g.strokeStyle = C.warn; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(L, CY(50)); g.lineTo(L + PW, CY(50)); g.stroke(); g.setLineDash([]);
  g.strokeStyle = C.muted; g.globalAlpha = 0.8; g.beginPath(); started = false;
  for (const p of vis) { const x = X(p[1]), y = CY(p[3]); if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y); }
  g.stroke(); g.globalAlpha = 1;
  g.restore();
  g.fillStyle = C.muted; g.textAlign = "right"; g.fillText("cycle s", L - 5, CT + 9); g.fillStyle = C.warn; g.fillText("takt 50", L - 5, CY(50) + 3.5);
  // now
  g.strokeStyle = C.accent; g.globalAlpha = 0.6; g.beginPath(); g.moveTo(X(tNow), T); g.lineTo(X(tNow), CT + CH); g.stroke(); g.globalAlpha = 1;
}

function bars(hourly) {
  const h = hourly.slice(-24), w = 150, H = 34, bw = w / 24;
  const y = (c) => H - Math.min(1, c / 90) * H;
  return `<svg width="${w}" height="${H}" viewBox="0 0 ${w} ${H}" class="kpi-bars">
    ${h.map(([, c], i) => `<rect x="${i * bw + 0.5}" y="${y(c)}" width="${bw - 1.5}" height="${H - y(c)}" rx="1.5" fill="${c >= TAKT_CARS_H * 0.9 ? "var(--good)" : "var(--warning)"}" opacity="${i === h.length - 1 ? 0.45 : 0.85}"/>`).join("")}
    <line x1="0" x2="${w}" y1="${y(TAKT_CARS_H)}" y2="${y(TAKT_CARS_H)}" stroke="var(--text-2)" stroke-dasharray="3 3"/></svg>`;
}

function caseCard(c, open) {
  const rev = c.status !== "open";
  return `<div class="lc ${rev ? "rev" : ""} ${open ? "open" : ""}" data-case="${c.id}" style="--cc:var(--c-${c.cause})">
    <div class="row" style="gap:8px"><span class="lc-id">#${c.id}</span>
      <span class="cause c-${c.cause}">${esc(c.cause)}</span><b class="lc-cul">${esc(c.culprit)}</b>
      <span class="small muted">${esc(c.family)} · ${esc(String(c.station || "").toUpperCase())}</span><span class="sp"></span>
      ${rev ? `<span class="pill">revised</span>` : ""}${c.early_h ? `<span class="pill good" data-tip="a floor note named it ${c.early_h} h before the data confirmed it">${icon("notes")} floor first</span>` : ""}
      <span class="small muted">confirmed ${fmt.dt(c.confirmed)}</span></div>
    <div class="row" style="gap:8px;margin-top:8px">
      <span class="pill ${c.action_sev} ${c.action === "STOP" ? "solid" : ""}">${icon(c.action === "STOP" ? "stop" : "shield")} ${esc(c.action_label)}</span>
      <span class="pill ${c.category === "High" ? "critical" : c.category === "Medium" ? "warning" : ""}">${esc(c.category)} · ${fmt.n(c.priority)}</span>
      ${c.action !== "NO HOLD" ? `<span class="small t2"><b style="color:var(--serious)">${fmt.n(c.check)}</b> to check · ${fmt.n(c.hold)} on hold · ${fmt.n(c.rework)} rework</span>` : `<span class="small muted">no product risk - time, not quality</span>`}
      <span class="sp"></span><span class="small muted">${fmt.n(c.confidence * 100)}% sure · ${c.incidents} incident${c.incidents > 1 ? "s" : ""}</span></div>
    <div class="lc-x">
      <div class="small t2" style="margin-top:8px"><b>Why:</b> ${esc(c.why)}</div>
      <dl class="kv" style="margin-top:8px"><dt>Symptom</dt><dd>${esc(c.symptom)}</dd><dt>Evidence</dt><dd>${esc(c.evidence)}</dd>
        <dt>Cars in scope</dt><dd>${fmt.n(c.cars_in_scope)} - ${esc(c.basis)} (${esc(c.window)})</dd>
        <dt>Owner</dt><dd>${esc(c.owner_action)}</dd>${c.rule ? `<dt>Rule</dt><dd>${esc(c.rule)}</dd>` : ""}
        ${c.prevent && c.prevent !== "-" ? `<dt>Prevention</dt><dd>${esc(c.prevent)}</dd>` : ""}</dl></div>
  </div>`;
}

export async function render(root, params, ctx) {
  const st = { ev: 0, pt: 0, d: null, pts: { st012: [], st013: [] }, events: [], simS: 0, dispS: 0, pollAt: 0, timer: 0, raf: 0,
    alive: true, casesKey: "", openCase: null, filter: "all", toastAt: 0, lastFrame: 0, seen: new Set(), scenario: "demo", speed: 3600 };
  root.innerHTML = `
    <div class="card lv-bar">
      <div class="lv-top">
        <div class="lv-state" id="lvState"><span class="ldot"></span><b id="lvStatus">Not started</b></div>
        <div class="lv-clock" id="lvClock">–</div>
        <div class="lv-prog" id="lvProg"><div class="lv-rail"></div><div class="lv-fill" id="lvFill"></div><div id="lvTicks"></div><div class="lv-days" id="lvDays"></div></div>
      </div>
      <div class="row lv-ctl">
        <div class="seg" id="lvScen"><button data-sc="demo" class="on">Demo week</button><button data-sc="random">Random week</button></div>
        <label class="small muted lv-seed" id="lvSeedW" hidden>seed <input id="lvSeed" type="number" value="21" min="1" max="9999"></label>
        <button class="btn primary" id="lvStart">${icon("play")} Start live week</button>
        <button class="btn" id="lvPause" hidden>${icon("pause")} Pause</button>
        <button class="btn ghost" id="lvStop" hidden>${icon("stop")} Stop</button>
        <span class="sp"></span>
        <span class="small muted">speed</span><div class="chips" id="lvSpeed">${Object.entries(SPEED).map(([k, v]) => `<button class="chip ${+k === 3600 ? "on" : ""}" data-speed="${k}">${v}</button>`).join("")}</div>
        <span class="pill" id="lvBackend" data-tip="how the floor listener reads the notes">${icon("spark")} –</span>
      </div>
    </div>

    <div class="card lv-intro" id="lvIntro">
      <div><h2>Stream a week through every model</h2>
        <p class="t2">The simulator builds a week of the line - 22,000 station records, floor notes, WI changes, repairs. The clock releases it car by car, the way the MES would.
        Each model runs when it would on a real line: the signal checker on every car, the cause finder every 2-hour block, the people model at every shift end,
        the floor listener when a note is written, the method checker before a WI goes live, containment and the impact ranker when a cause is confirmed.</p>
        <div class="row" style="gap:10px;margin-top:6px"><span class="pill info">${icon("clock")} a week in ~3 min at 1 h = 1 s</span>
          <span class="pill">${icon("shield")} the demo database is not changed</span></div></div>
    </div>

    <div class="grid g6" id="lvKpi">
      <div class="card kpi"><div class="kpi-l">${icon("factory")} Cars built</div><div class="kpi-v" data-k="cars">0</div>
        <div class="kpi-s" id="kCarsS">takt allows ${TAKT_CARS_H} / h</div><div id="kBars" style="margin-top:6px"></div></div>
      <div class="card kpi"><div class="kpi-l">${icon("search")} Unusual cycles</div><div class="kpi-v" data-k="flags">0</div><div class="kpi-s" id="kFlagsS">signal checker, every car</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("serious")} Problem cases</div><div class="kpi-v" data-k="open_cases">0</div><div class="kpi-s" id="kCasesS">confirmed by the cause finder</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("shield")} Cars waiting</div><div class="kpi-v" data-k="waiting">0</div><div class="kpi-s" id="kWaitS">to check or on hold</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("notes")} Floor facts</div><div class="kpi-v" data-k="facts">0</div><div class="kpi-s" id="kNotesS">from 0 notes</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("clipboard")} Method checks</div><div class="kpi-v" data-k="wi_checked">0</div><div class="kpi-s" id="kWiS">WI versions checked</div></div>
    </div>

    <div class="split" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>Stations - streaming</h3><span class="hint">last 6 h · every dot is a car · orange = flagged by the signal checker · × = rejected</span></div>
        ${["st012", "st013"].map((s) => `<div class="lv-ch"><div class="row lv-ch-h"><b>${s.toUpperCase()}</b><span class="small muted" id="h-${s}"></span><span class="sp"></span>
          <span class="small" id="r-${s}"></span></div><canvas id="cv-${s}"></canvas></div>`).join("")}</div>
      <div class="card lv-feed-c"><div class="card-h"><h3>What the models just did</h3><span class="sp"></span>
        <div class="chips" id="lvFilter"><button class="chip on" data-f="all">All</button><button class="chip" data-f="alerts">Alerts</button><button class="chip" data-f="cases">Cases</button></div></div>
        <div class="lv-feed" id="lvFeed"><div class="empty">Start a week - events appear here as the models find them.</div></div></div>
    </div>

    <div class="card" style="margin-top:16px"><div class="card-h"><h3>The models, working</h3><span class="hint" id="lvPass"></span><span class="sp"></span>
      <button class="btn sm ghost" data-go="how">${icon("flow")} How it works</button></div>
      <div class="dg-wrap">${diagram({ flow: false, id: "lvDg" })}</div></div>

    <div class="card" style="margin-top:16px"><div class="card-h"><h3>Problem cases - decided live</h3><span class="hint">containment + priority at the moment the cause was confirmed, updated every block</span></div>
      <div class="lv-cases" id="lvCases"><div class="empty">No case yet - the cause finder needs one shift to learn the normal, then a cause must hold for two blocks.</div></div></div>`;

  const svg = $("#lvDg", root);
  const kv = {};
  $$("[data-k]", root).forEach((el) => (kv[el.dataset.k] = { el, v: 0 }));
  const tween = (k, to) => {
    const o = kv[k]; if (!o || o.v === to) return;
    const from = o.v, t0 = performance.now(); o.v = to;
    const step = (t) => { const f = Math.min(1, (t - t0) / 450); o.el.textContent = fmt.n(from + (to - from) * f); if (f < 1 && st.alive) requestAnimationFrame(step); };
    requestAnimationFrame(step); setTimeout(() => (o.el.textContent = fmt.n(o.v)), 600);
  };

  // ---------- controls ----------
  const seg = $("#lvScen", root);
  seg.onclick = (e) => { const b = e.target.closest("[data-sc]"); if (!b) return; st.scenario = b.dataset.sc;
    $$("button", seg).forEach((x) => x.classList.toggle("on", x === b)); $("#lvSeedW", root).hidden = st.scenario !== "random"; };
  $("#lvStart", root).onclick = async () => {
    const seed = st.scenario === "random" ? +($("#lvSeed", root).value || 21) : 7;
    Object.assign(st, { ev: 0, pt: 0, pts: { st012: [], st013: [] }, events: [], casesKey: "", seen: new Set(), simS: 0, dispS: 0 });
    $("#lvFeed", root).innerHTML = ""; $("#lvCases", root).innerHTML = `<div class="empty">No case yet - the cause finder needs one shift to learn the normal.</div>`;
    $("#lvTicks", root).innerHTML = "";
    await ctx.api.post("/api/live/start", { scenario: st.scenario, seed, speed: st.speed });
    ctx.toast({ severity: "info", title: "Building the week…", detail: st.scenario === "demo" ? "the demo story - same week as the database" : `a new random week, seed ${seed}`, timeout: 2500 });
    kick();
  };
  $("#lvPause", root).onclick = async () => { const a = st.d?.status === "paused" ? "resume" : "pause"; apply(await ctx.api.post("/api/live/control", { action: a })); };
  $("#lvStop", root).onclick = async () => { apply(await ctx.api.post("/api/live/control", { action: "stop" })); };
  $("#lvSpeed", root).onclick = async (e) => {
    const b = e.target.closest("[data-speed]"); if (!b) return; st.speed = +b.dataset.speed;
    $$("#lvSpeed .chip", root).forEach((x) => x.classList.toggle("on", x === b));
    if (st.d && ACTIVE.includes(st.d.status)) apply(await ctx.api.post("/api/live/control", { speed: st.speed }));
  };
  $("#lvFilter", root).onclick = (e) => { const b = e.target.closest("[data-f]"); if (!b) return; st.filter = b.dataset.f;
    $$("#lvFilter .chip", root).forEach((x) => x.classList.toggle("on", x === b)); renderFeed(true); };
  $("#lvCases", root).onclick = (e) => { const c = e.target.closest("[data-case]"); if (!c) return;
    st.openCase = st.openCase === +c.dataset.case ? null : +c.dataset.case; st.casesKey = ""; renderCases(st.d?.cases || []); };
  $("#lvFeed", root).onclick = (e) => { const c = e.target.closest("[data-case]"); if (!c) return;
    st.openCase = +c.dataset.case; st.casesKey = ""; renderCases(st.d?.cases || []);
    const el = $(`.lc[data-case="${c.dataset.case}"]`, root); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); } };

  // ---------- rendering ----------
  const evHtml = (e) => `<div class="ev ${e.sev}" ${e.case ? `data-case="${e.case}"` : ""}>
      <div class="ic ${e.sev === "good" ? "good" : e.sev}">${icon(SEV_ICON[e.sev] || "info")}</div>
      <div style="min-width:0"><div class="ev-t"><span class="ev-n n-${e.node}">${esc(NODE_LABEL[e.node] || e.node)}</span>${esc(e.title)}</div>
        <div class="ev-d">${esc(e.detail)}</div></div>
      <div class="ev-time">${fmt.day(e.t)}<br>${fmt.time(e.t)}</div></div>`;
  const keep = (e) => st.filter === "all" || (st.filter === "alerts" ? e.sev !== "info" : !!e.case);
  function renderFeed(all, fresh = []) {
    const box = $("#lvFeed", root);
    if (all) { box.innerHTML = st.events.filter(keep).slice(-80).reverse().map(evHtml).join("") || `<div class="empty">Nothing yet.</div>`; return; }
    const add = fresh.filter(keep);
    if (!add.length) return;
    box.querySelector(".empty")?.remove();
    box.insertAdjacentHTML("afterbegin", add.slice(-12).reverse().map(evHtml).join(""));
    [...box.children].slice(0, Math.min(add.length, 12)).forEach((el, i) => (el.style.animationDelay = `${i * 60}ms`));
    while (box.children.length > 120) box.lastElementChild.remove();
  }
  function renderCases(cases) {
    const key = JSON.stringify(cases.map((c) => [c.id, c.status, c.action, c.check, c.hold, c.category, c.priority, c.early_h])) + st.openCase;
    if (key === st.casesKey) return;
    const before = new Map($$(".lc", root).map((el) => [el.dataset.case, el.dataset.sig]));
    st.casesKey = key;
    $("#lvCases", root).innerHTML = cases.length ? cases.map((c) => caseCard(c, c.id === st.openCase)).join("")
      : `<div class="empty">No case yet - the cause finder needs one shift to learn the normal, then a cause must hold for two blocks.</div>`;
    cases.forEach((c) => {
      const el = $(`.lc[data-case="${c.id}"]`, root); if (!el) return;
      el.dataset.sig = [c.action, c.check, c.hold, c.category].join("|");
      if (!before.has(String(c.id))) el.classList.add("new");
      else if (before.get(String(c.id)) !== el.dataset.sig) el.classList.add("flash");
    });
  }
  function renderDays(d) {
    if (!d.meta?.start || $("#lvDays", root).dataset.s === d.meta.start) return;
    $("#lvDays", root).dataset.s = d.meta.start;
    const s = +parse(d.meta.start);
    $("#lvDays", root).innerHTML = Array.from({ length: 7 }, (_, i) => `<span>${fmt.day(new Date(s + i * 864e5))}</span>`).join("");
  }

  function apply(d) {
    if (!d || !st.alive) return;
    const backlog = st.ev === 0 && (d.events || []).length > 8;
    st.d = d;
    const status = d.status;
    const act = ACTIVE.includes(status);
    ctx.setLive(status === "running");
    $("#lvStatus", root).textContent = STATUS[status] || status;
    $("#lvState", root).className = `lv-state ${status}`;
    $("#lvIntro", root).hidden = !!d.sim;
    $("#lvStart", root).innerHTML = `${icon(act ? "reset" : "play")} ${act ? "Restart" : d.sim ? "New week" : "Start live week"}`;
    $("#lvPause", root).hidden = !["running", "paused"].includes(status);
    $("#lvPause", root).innerHTML = status === "paused" ? `${icon("play")} Resume` : `${icon("pause")} Pause`;
    $("#lvStop", root).hidden = !act;
    if (d.speed) { st.speed = d.speed; $$("#lvSpeed .chip", root).forEach((x) => x.classList.toggle("on", +x.dataset.speed === d.speed)); }
    $("#lvBackend", root).innerHTML = `${icon("spark")} floor listener: ${d.backend?.startsWith("azure") ? "GPT-5 live"
      : d.backend?.startsWith("saved") ? "GPT-5 (saved answers)" : "offline rules"}`;
    if (d.error) $("#lvStatus", root).textContent = `Error - ${d.error}`;
    if (!st.synced && d.meta?.scenario) {          // show the week that is on the server
      st.synced = true; st.scenario = d.meta.scenario;
      $$("button", seg).forEach((x) => x.classList.toggle("on", x.dataset.sc === st.scenario));
      $("#lvSeedW", root).hidden = st.scenario !== "random";
      if (st.scenario === "random") $("#lvSeed", root).value = d.meta.seed;
    }
    if (!d.sim) { ctx.setClock(null); return; }
    renderDays(d);
    const startMs = +parse(d.meta.start);
    st.simS = (+parse(d.sim) - startMs) / 1000; st.pollAt = performance.now();
    if (st.dispS > st.simS || !act) st.dispS = st.simS;
    $("#lvFill", root).style.width = `${(d.progress * 100).toFixed(2)}%`;
    $("#lvClock", root).innerHTML = `${fmt.day(d.sim)} ${fmt.time(d.sim)} <small>shift ${d.shift}</small>`;
    const tag = { running: "LIVE", finishing: "LIVE", paused: "PAUSED", finished: "DONE", stopped: "STOPPED", error: "ERROR" }[status] || "…";
    ctx.setClock(`<span class="pill ${status === "running" ? "critical solid" : "info"}">${tag}</span>
      <b>${fmt.day(d.sim)} ${fmt.time(d.sim)}</b> · shift ${d.shift}${status === "running" ? ` · ${SPEED[d.speed] || ""}` : ""}`);
    // KPIs
    const k = d.kpi;
    tween("cars", k.cars); tween("flags", k.flags); tween("open_cases", k.open_cases); tween("waiting", k.check + k.hold);
    tween("facts", k.facts); tween("wi_checked", k.wi_checked);
    $("#kCarsS", root).textContent = `last hour ${k.rate_h} cars · takt allows ${TAKT_CARS_H}`;
    $("#kFlagsS", root).textContent = `${fmt.n(k.flags_h)} in the last hour`;
    $("#kCasesS", root).innerHTML = k.stops ? `<b style="color:var(--critical)">${k.stops} stop${k.stops > 1 ? "s" : ""} recommended</b>` : "confirmed by the cause finder";
    $("#kWaitS", root).textContent = `${fmt.n(k.check)} to check · ${fmt.n(k.hold)} on hold`;
    $("#kNotesS", root).textContent = `from ${k.notes} floor notes`;
    $("#kWiS", root).innerHTML = k.wi_block ? `<b style="color:var(--critical)">${k.wi_block} held at the gate</b>` : "WI versions checked";
    if (d.hourly) $("#kBars", root).innerHTML = bars(d.hourly);
    $("#lvPass", root).textContent = d.analysed ? `cause finder: ${d.passes} passes · last block ${fmt.time(d.analysed)} · ${d.pass_s} s per pass` : "cause finder learns the normal during the first shift";
    setCounts(svg, Object.fromEntries(Object.entries(d.nodes).map(([k2, v]) => [k2, v.n])),
      Object.fromEntries(Object.entries(d.nodes).map(([k2, v]) => [k2, v.unit])));
    // points
    for (const s of ["st012", "st013"]) {
      const p = d.points?.[s] || [];
      if (p.length) { st.pts[s].push(...p); st.pt = Math.max(st.pt, p[p.length - 1][0]); }
      const cut = st.simS - WIN_S - 3600;
      if (st.pts[s].length > 6000) st.pts[s] = st.pts[s].filter((x) => x[1] >= cut);
      const spec = d.meta.spec[s];
      const last = st.pts[s][st.pts[s].length - 1];
      const hour = st.pts[s].filter((x) => x[1] > st.simS - 3600);
      $(`#h-${s}`, root).textContent = `${spec.title.split(" - ")[1] || ""} · ${spec.name} ${spec.unit} · severity ${spec.severity}`;
      $(`#r-${s}`, root).innerHTML = last ? `<b>${last[2] == null ? "–" : fmt.n(last[2], spec.usl > 20 ? 1 : 2)}</b> ${esc(spec.unit)} · ${hour.length} cars/h · <span style="color:var(--serious)">${hour.filter((x) => x[4]).length} flagged</span>` : "";
    }
    // events
    const fresh = (d.events || []).filter((e) => !st.seen.has(e.seq));
    fresh.forEach((e) => st.seen.add(e.seq));
    if (fresh.length) {
      st.ev = Math.max(st.ev, ...fresh.map((e) => e.seq));
      st.events.push(...fresh);
      if (st.events.length > 600) st.events = st.events.slice(-600);
      renderFeed(false, fresh);
      const span = 7 * 86400;
      $("#lvTicks", root).insertAdjacentHTML("beforeend", fresh.filter((e) => e.sev !== "info").map((e) =>
        `<div class="tp-ev ${e.sev}" style="left:${(((+parse(e.t) - startMs) / 1000) / span * 100).toFixed(2)}%" data-tip="<b>${esc(fmt.dt(e.t))}</b> ${esc(e.title)}"></div>`).join(""));
      // animate the pipeline: the newest events, one after another
      const anim = fresh.filter((e) => e.path?.length).slice(backlog ? -2 : -6);
      let t = 0; anim.forEach((e) => { t = trace(svg, e.path, e.sev === "info" ? "info" : e.sev, t, 330) + 120; });
      // toasts: critical always, the rest rate-limited
      const now = backlog ? -1e9 : performance.now();
      if (!backlog) fresh.filter((e) => e.sev === "critical").slice(-2).forEach((e) => ctx.toast({ severity: "critical", title: e.title, detail: e.detail, timeout: 9000 }));
      const loud = fresh.filter((e) => e.sev === "serious" || (e.sev === "good" && e.node === "floor") || (e.node === "contain" && e.sev !== "info"));
      if (!backlog && loud.length && now - st.toastAt > 3500) {
        const e = loud[loud.length - 1]; st.toastAt = now;
        ctx.toast({ severity: e.sev, title: e.title, detail: e.detail, timeout: 5500,
          onClick: () => { if (e.case) { st.openCase = e.case; st.casesKey = ""; renderCases(st.d.cases || []); $(`.lc[data-case="${e.case}"]`, root)?.scrollIntoView({ block: "center" }); } } });
      }
    }
    if (d.cases) renderCases(d.cases);
  }

  // ---------- loops ----------
  async function poll() {
    if (!st.alive) return;
    clearTimeout(st.timer);
    try { apply(await ctx.api.get(`/api/live/state?ev=${st.ev}&pt=${st.pt}`, { fresh: true })); }
    catch (e) { $("#lvStatus", root).textContent = "Backend not reachable"; }
    const s = st.d?.status;
    st.timer = setTimeout(poll, s === "running" ? 450 : ACTIVE.includes(s) ? 900 : 3000);
  }
  const kick = () => { clearTimeout(st.timer); st.timer = setTimeout(poll, 150); };
  function frame(t) {
    if (!st.alive) return;
    st.raf = requestAnimationFrame(frame);
    if (t - st.lastFrame < 33 || !st.d?.sim) return;
    st.lastFrame = t;
    if (st.d.status === "running") {
      const ext = st.simS + ((t - st.pollAt) / 1000 - 0.5) * st.d.speed;
      st.dispS = Math.max(st.dispS, Math.min(st.simS, ext));
    } else st.dispS = st.simS;
    const C = css();
    for (const s of ["st012", "st013"])
      drawStation($(`#cv-${s}`, root), st.pts[s], { ...st.d.meta.spec[s], start: st.d.meta.start }, st.dispS, Math.max(st.dispS, WIN_S), C);
  }
  st.raf = requestAnimationFrame(frame);
  ctx.cleanup.push(() => { st.alive = false; clearTimeout(st.timer); cancelAnimationFrame(st.raf); ctx.setClock(null);
    ctx.setLive(st.d?.status === "running"); });
  await poll();
  if (params.autostart && !ACTIVE.includes(st.d?.status)) {
    if (params.autostart === "random") { st.scenario = "random"; seg.querySelector('[data-sc="random"]').click(); }
    $("#lvStart", root).click();
  }
}
