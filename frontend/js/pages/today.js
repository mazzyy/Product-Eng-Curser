// Today - the engineer's morning: results, the prioritised problem list, alerts, what the last shifts said.
import { $, $$, esc, fmt, icon, store, animateIn, stagger, SEV_ICON, CAT_ICON } from "../util.js";
import { sparkline } from "../charts.js";

const STATUS = { active: "serious", recurring: "warning", fixed: "good", over: "info" };
const LEVEL = { High: "critical", Medium: "warning", Low: "info" };

function owner(action) {
  const m = String(action || "").match(/^([A-Z][A-Za-z ]{1,18}):/);
  return m ? m[1] : "Engineering";
}

export async function render(root, params, ctx) {
  const [ov, alerts] = await Promise.all([ctx.api.get("/api/overview"), ctx.api.get("/api/alerts")]);
  const s = ov.summary, b = ov.brief;
  const built = +s.cars_built, target = +s.target_per_week, cap = +s.capacity;
  const waiting = b.cars_waiting_for_check || { cars: 0, cases: [] };
  const urgent = alerts.filter((a) => a.severity === "critical" || a.severity === "serious");
  const filter = store.get("q-filter", "All"), hideFixed = store.get("q-hidefixed", false);
  const pins = new Set(store.get("pins", []));
  const hiNow = ov.queue.filter((q) => q.category === "High" && (q.status === "active" || q.status === "recurring")).length;

  root.innerHTML = `
  <div class="hero">
    <div><h2>Good morning - ${alerts.filter((a) => a.severity === "critical").length} critical alert${alerts.filter((a) => a.severity === "critical").length === 1 ? "" : "s"}, ${hiNow} high-priority problems still open</h2>
      <p>Week ${esc(s.week)}. Line is ${esc(s.attainment)} of the 7,500 target; ST012 is the bottleneck at ${esc(s.bottleneck_cycle_s)} s per car.</p></div>
    <div class="sp"></div>
    <button class="btn" data-ask="What should I look at first this morning?">${icon("spark")} Brief me</button>
  </div>

  <div class="grid g4" id="kpis">
    <div class="card kpi"><div class="kpi-l">${icon("factory")} Cars built this week</div>
      <div class="kpi-v"><span data-count="${built}">0</span><small>/ ${fmt.n(target)}</small></div>
      <div class="kpi-s">${esc(s.attainment)} of target</div>
      <div class="meter"><i data-w="${Math.min(100, (built / cap) * 100).toFixed(1)}%" style="background:var(--good)"></i>
        <span class="tick" style="left:${((target / cap) * 100).toFixed(1)}%" data-tip="weekly target 7,500"></span></div>
      <div class="kpi-spark">${sparkline(ov.cars_per_day.map((d) => d.cars), { w: 96, h: 30, color: "var(--good)" })}</div></div>
    <div class="card kpi"><div class="kpi-l">${icon("gauge")} Capacity</div>
      <div class="kpi-v"><span data-count="${cap}">0</span><small>cars / week</small></div>
      <div class="kpi-s">bottleneck ${esc(String(s.bottleneck).toUpperCase())} · ${esc(s.bottleneck_cycle_s)} s per car · ${fmt.n(s.lost_cars_ranked)} cars lost to the ranked problems</div></div>
    <div class="card kpi"><div class="kpi-l">${icon("arrow")} Next week, projected</div>
      <div class="kpi-v"><span data-count="${+s.projected_next_week}">0</span><small>${esc(s.projected_attainment)}</small></div>
      <div class="kpi-s">on a 5-day week: ${fmt.n(s.five_day_equivalent)} cars (${fmt.pct(s.five_day_equivalent / target)})</div></div>
    <div class="card kpi ${waiting.cars ? "alert" : ""}"><div class="kpi-l">${icon("shield")} Cars waiting for a check</div>
      <div class="kpi-v" style="color:var(--serious)"><span data-count="${waiting.cars}">0</span><small>cars</small></div>
      <div class="kpi-s">${waiting.cases.length} problem cases · quality releases them · <a href="#/contain">open</a></div></div>
  </div>

  <div class="split" style="margin-top:16px">
    <div class="card">
      <div class="card-h"><h3>Priority queue</h3><span class="hint">ranked by the impact ranker: safety & quality 40% · delivery 30% · cost 15% · people 15% + hard rules</span></div>
      <div class="row" style="margin-bottom:12px">
        <div class="chips" id="qf">${["All", "High", "Medium", "Low"].map((f) => `<button class="chip ${f === filter ? "on" : ""}" data-f="${f}">${f}
          <span class="n">${f === "All" ? ov.queue.length : ov.queue.filter((q) => q.category === f).length}</span></button>`).join("")}</div>
        <div class="sp"></div>
        <label class="small t2 row" style="cursor:pointer"><input type="checkbox" id="hideFixed" ${hideFixed ? "checked" : ""}> hide fixed / over</label>
      </div>
      <div class="queue" id="queue"></div>
    </div>
    <div class="stack">
      <div class="card"><div class="card-h"><h3>Needs a decision</h3><span class="sp"></span><span class="badge">${urgent.length}</span></div>
        ${urgent.slice(0, 6).map((a) => `<div class="li click" data-alert="${esc(a.id)}"><div class="ic ${a.severity}">${icon(SEV_ICON[a.severity])}</div>
          <div><div class="li-t">${esc(a.title)}</div><div class="li-d">${esc(a.detail)}</div></div></div>`).join("") || `<div class="empty">Nothing urgent.</div>`}
      </div>
      <div class="card"><div class="card-h"><h3>From the last shifts</h3><span class="hint">floor listener</span><span class="sp"></span><a class="small" href="#/notes">all notes</a></div>
        ${(b.floor_highlights || []).slice(0, 5).map((f) => `<div class="li"><div class="ic ${f.link === "early warning" ? "warning" : f.link === "disputes" ? "serious" : "info"}">${icon(CAT_ICON[f.category] || "notes")}</div>
          <div><div class="li-t">${esc(f.subject || f.category)} <span class="pill ${f.link === "early warning" ? "warning" : f.link === "disputes" ? "serious" : ""}">${esc(f.link)}</span></div>
          <div class="li-d quote">"${esc(f.quote)}"</div><div class="small muted">${fmt.day(f.shift_date)} · shift ${esc(f.shift)} · ${esc(f.station || "line")}</div></div></div>`).join("")}
      </div>
      <div class="card"><div class="card-h"><h3>Next shift works to</h3><span class="hint">change manager</span></div>
        ${(ov.handover || []).map((h) => `<div class="li"><div class="ic ${h.alert !== "-" ? "critical" : "good"}">${icon(h.alert !== "-" ? "critical" : "clipboard")}</div>
          <div><div class="li-t">${esc(h.station.toUpperCase())}: ${esc(h.current)}</div>
          <div class="li-d">${h.alert !== "-" ? `<span style="color:var(--critical)">${esc(h.alert)}</span>` : `since ${fmt.dt(h.since)} - ${esc(h.what_changed)}`}</div>
          ${h.next !== "-" ? `<div class="small muted">next: ${esc(h.next)}</div>` : ""}</div></div>`).join("")}
      </div>
    </div>
  </div>`;

  const queue = $("#queue", root);
  const draw = () => {
    const f = store.get("q-filter", "All"), hf = store.get("q-hidefixed", false);
    let items = ov.queue.filter((q) => (f === "All" || q.category === f) && !(hf && (q.status === "fixed" || q.status === "over")));
    items = [...items.filter((q) => pins.has(q.impact_id)), ...items.filter((q) => !pins.has(q.impact_id))];
    queue.innerHTML = items.slice(0, 30).map((q) => `
      <div class="qi ${q.category} ${pins.has(q.impact_id) ? "pinned" : ""}" data-id="${q.impact_id}">
        <div class="rank">${q.rank}</div>
        <div style="min-width:0"><div class="qi-t" title="${esc(q.title)}">${pins.has(q.impact_id) ? icon("pin") + " " : ""}${esc(q.title)}</div>
          <div class="qi-m"><span class="pill ${LEVEL[q.category]}">${esc(q.category)}</span><span class="pill ${STATUS[q.status] || ""}">${esc(q.status)}</span>
            ${q.cause ? `<span class="cause c-${esc(q.cause)}">${esc(q.cause)}</span>` : ""}<span class="owner">${esc(owner(q.action))}</span>
            <span>${esc(String(q.stations || "").toUpperCase())}</span>${q.rule ? `<span class="rule">${icon("critical")} hard rule</span>` : ""}</div></div>
        <div class="prio"><div class="prio-v num">${fmt.n(q.priority, 0)}</div><div class="prio-bar"><i data-w="${Math.min(100, q.priority)}%"></i></div>
          ${q.score_next > q.score_now + 0.5 ? `<div class="small" style="color:var(--serious)">next week ${fmt.n(q.score_next)}</div>` : `<div class="small muted">score ${fmt.n(q.score_now)}</div>`}</div>
        <div class="qi-x">
          ${q.rule ? `<div class="rule" style="margin-bottom:6px">${icon("critical")} ${esc(q.rule)}</div>` : ""}
          <div class="t2">${esc(q.detail || "")}</div>
          <div class="subs">${[["Safety & quality", q.sq], ["Delivery", q.dl], ["Cost", q.co], ["People", q.pe]].map(([l, v]) =>
            `<div><div class="sub-l">${l} <b class="num">${fmt.n(v, 1)}</b></div><div class="sub-b"><i style="width:${Math.min(100, (v || 0) * 10)}%"></i></div></div>`).join("")}</div>
          <div class="row small t2" style="gap:14px;margin-bottom:8px">
            <span>cars at risk <b>${fmt.n(q.risk_cars)}</b></span><span>to check <b>${fmt.n(q.definite_cars)}</b></span>
            <span>known bad <b>${fmt.n(q.known_bad)}</b></span><span>lost cars <b>${fmt.n(q.lost_cars)}</b></span><span>rework <b>${fmt.n(q.rework_h, 1)} h</b></span>
            ${q.first_ts ? `<span>${fmt.dt(q.first_ts)} → ${fmt.dt(q.last_ts)}</span>` : ""}</div>
          <div class="row"><span class="strong">Next:</span><span>${esc(q.action)}</span></div>
          <div class="row" style="margin-top:10px">
            ${q.case_id != null ? `<button class="btn sm" data-inv="${q.case_id}">${icon("search")} Investigate case ${q.case_id}</button>
              <button class="btn sm" data-con="${q.case_id}">${icon("shield")} Containment</button>` : ""}
            <button class="btn sm" data-ask="Explain problem #${q.rank} on the impact list (${esc(q.title)}): why is it ranked there and what should I do?">${icon("spark")} Ask copilot</button>
            <span class="sp"></span>
            <button class="btn sm ghost" data-pin="${q.impact_id}">${icon("pin")} ${pins.has(q.impact_id) ? "Unpin" : "Focus"}</button>
          </div>
        </div>
      </div>`).join("") || `<div class="empty">No problems in this filter.</div>`;
    stagger(queue); animateIn(queue);
  };
  draw();

  $("#qf", root).onclick = (e) => { const c = e.target.closest("[data-f]"); if (!c) return; store.set("q-filter", c.dataset.f);
    $$("#qf .chip", root).forEach((x) => x.classList.toggle("on", x === c)); draw(); };
  $("#hideFixed", root).onchange = (e) => { store.set("q-hidefixed", e.target.checked); draw(); };
  queue.onclick = (e) => {
    const pin = e.target.closest("[data-pin]");
    if (pin) { const id = +pin.dataset.pin; pins.has(id) ? pins.delete(id) : pins.add(id); store.set("pins", [...pins]);
      ctx.toast({ severity: "info", title: pins.has(id) ? "Pinned to the top of your queue" : "Unpinned", timeout: 2200 }); draw(); return; }
    const inv = e.target.closest("[data-inv]"); if (inv) { ctx.go("investigate", { case: inv.dataset.inv }); return; }
    const con = e.target.closest("[data-con]"); if (con) { ctx.go("contain", { case: con.dataset.con }); return; }
    if (e.target.closest("button")) return;
    e.target.closest(".qi")?.classList.toggle("open");
  };
  root.onclick = (e) => {
    const a = e.target.closest("[data-alert]");
    if (a) { const al = alerts.find((x) => x.id === a.dataset.alert); if (!al) return;
      const p = {}; if (typeof al.target === "number") p.case = al.target; if (al.page === "maintain") p.eq = al.target;
      if (al.page === "change") p.station = al.target; if (al.page === "notes") p.note = al.target;
      ctx.go(al.page === "today" ? "investigate" : al.page, p); }
  };
  stagger($("#kpis", root)); animateIn(root);
}
