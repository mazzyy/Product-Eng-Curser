// Investigate - the week on one timeline (signal, incidents by cause, floor notes, WI changes, repairs),
// and for one problem case: is it real, why (cause finder + evidence), what the floor said, what it costs.
import { $, $$, esc, fmt, icon, parse, animateIn, stagger, CAT_ICON } from "../util.js";

const CAUSES = ["machine", "people", "method", "station"];
const W = 1240, L = 104, R = 14, TOP = 30, LANE = 118, GAP = 14;

function timelineSVG(tl, filter) {
  const t0 = +parse(tl.start), t1 = +parse(tl.end);
  const x = (t) => L + ((+parse(t) - t0) / (t1 - t0)) * (W - L - R);
  const sids = Object.keys(tl.stations);
  const H = TOP + sids.length * (LANE + GAP);
  let s = `<svg class="tl" viewBox="0 0 ${W} ${H}" id="tlSvg">`;
  // day + shift grid
  for (let t = t0; t <= t1; t += 8 * 3600e3) {
    const d = new Date(t), X = L + ((t - t0) / (t1 - t0)) * (W - L - R), day = d.getHours() === 6;
    s += `<line class="${day ? "grid-l" : "grid-m"}" x1="${X}" x2="${X}" y1="${TOP - 6}" y2="${H}"/>`;
    if (day && t < t1) s += `<text x="${X + 4}" y="14" style="font-weight:600;fill:var(--text-2)">${fmt.day(d)}</text>`;
  }
  sids.forEach((sid, k) => {
    const st = tl.stations[sid], y0 = TOP + k * (LANE + GAP), mid = y0 + 34;
    s += `<rect class="lane-bg" x="${L - 6}" y="${y0}" width="${W - L - R + 12}" height="${LANE}" rx="10"/>
      <text class="lane-t" x="8" y="${y0 + 20}">${sid.toUpperCase()}</text>
      <text x="8" y="${y0 + 36}">${esc(st.signal)} · flags</text>
      <text x="8" y="${y0 + 78}">incidents</text><text x="8" y="${y0 + 104}">floor & events</text>`;
    // flagged-cycle rate (bars) and the process signal (z-score line)
    st.hours.forEach((h) => {
      if (!h.flag_rate) return;
      const X = x(h.t), bh = Math.min(28, h.flag_rate * 60);
      s += `<rect class="flag" x="${X}" y="${y0 + 58 - bh}" width="${((W - L - R) / 168) * 0.9}" height="${bh}"/>`;
    });
    const pts = st.hours.filter((h) => h.prim_z != null).map((h) => `${x(h.t).toFixed(1)},${(mid - Math.max(-3.5, Math.min(3.5, h.prim_z)) * 6).toFixed(1)}`);
    s += `<polyline class="sig" points="${pts.join(" ")}"/>`;
    // WI changes
    tl.wi_changes.filter((w) => w.station === sid).forEach((w) => {
      const X = x(w.valid_from), c = w.verdict === "BLOCK" ? "var(--critical)" : "var(--muted)";
      s += `<g class="wi" data-tip="<b>${esc(w.wi_version)}</b> live ${fmt.dt(w.valid_from)}<br>${esc(w.change_note)}<br>method check: <b>${esc(w.verdict)}</b>">
        <line x1="${X}" x2="${X}" y1="${y0 + 4}" y2="${y0 + LANE - 4}" stroke="${c}"/>
        <rect x="${X + 3}" y="${y0 + 4}" width="${w.verdict === "BLOCK" ? 82 : 62}" height="16" rx="5" fill="${c}" opacity="${w.verdict === "BLOCK" ? 1 : .25}"/>
        <text x="${X + 8}" y="${y0 + 16}" style="fill:${w.verdict === "BLOCK" ? "#fff" : "var(--text)"};font-size:10.5px;font-weight:650">${esc(w.wi_version.replace("WI-0", "WI "))}${w.verdict === "BLOCK" ? " ✕" : ""}</text></g>`;
    });
    // incidents
    tl.incidents.filter((i) => i.top_station === sid).forEach((i) => {
      const X0 = x(i.start_ts), X1 = x(i.end_ts), on = filter === "all" || filter === i.cause;
      s += `<rect class="inc ${on ? "" : "dim"}" data-case="${i.case_id}" data-start="${i.start_ts}" x="${X0}" y="${y0 + 68}" width="${Math.max(4, X1 - X0 - 1.5)}" height="16" rx="4"
        fill="var(--c-${i.cause})" data-tip="<b>#${i.incident_id} · case ${i.case_id}</b><br>${esc(i.symptom)}<br>${esc(i.cause)}: <b>${esc(i.culprit)}</b> (${fmt.pct(i.confidence)})<br>${fmt.dt(i.start_ts)} → ${fmt.time(i.end_ts)}"/>`;
    });
    // repairs + containment decisions + floor notes
    tl.repairs.filter((r) => r.station === sid).forEach((r) => {
      s += `<g data-tip="<b>Repair</b> ${fmt.dt(r.ts)}<br>${esc(r.text)}"><circle cx="${x(r.ts)}" cy="${y0 + 102}" r="7" fill="var(--good)"/>
        <text x="${x(r.ts)}" y="${y0 + 106}" text-anchor="middle" style="fill:#fff;font-size:10px;font-weight:700">R</text></g>`;
    });
    tl.containment.filter((c) => c.station === sid && c.action !== "NO HOLD").forEach((c) => {
      const col = c.action === "STOP" ? "var(--critical)" : "var(--serious)";
      s += `<g data-case="${c.case_id}" class="note" data-tip="<b>Containment ${fmt.dt(c.decision_ts)}</b><br>${esc(c.action)} (case ${c.case_id})">
        <rect x="${x(c.decision_ts) - 6}" y="${y0 + 95}" width="12" height="12" rx="3" fill="${col}" transform="rotate(45 ${x(c.decision_ts)} ${y0 + 101})"/></g>`;
    });
    tl.notes.filter((n) => (n.station || "line") === sid || (sid === "st012" && (n.station === "line" || !n.station))).forEach((n) => {
      const X = x(n.ts), Y = y0 + 101;
      const col = n.link === "early warning" ? "var(--warning)" : n.link === "disputes" ? "var(--serious)" : n.category === "safety" ? "var(--critical)" : "var(--text-2)";
      const shape = n.link === "early warning" ? `<path d="M${X} ${Y - 8} L${X + 7} ${Y + 5} L${X - 7} ${Y + 5} Z" fill="${col}"/>`
        : n.link === "disputes" ? `<path d="M${X - 5} ${Y - 5} L${X + 5} ${Y + 5} M${X + 5} ${Y - 5} L${X - 5} ${Y + 5}" stroke="${col}" stroke-width="2.5"/>`
        : `<circle cx="${X}" cy="${Y}" r="5" fill="none" stroke="${col}" stroke-width="2"/>`;
      s += `<g class="note" data-note="${n.note_id}" data-tip="<b>${esc(n.link)}</b>${n.lead_h ? ` · ${fmt.n(n.lead_h)} h before the data` : ""}<br>${esc(n.subject || n.category)}: &quot;${esc(n.quote)}&quot;">${shape}</g>`;
    });
  });
  s += `<g id="tlCursor" style="display:none"><line class="cursor" x1="0" x2="0" y1="${TOP - 8}" y2="${H}"/><path class="cursor-h" d="M-6 ${TOP - 14} L6 ${TOP - 14} L0 ${TOP - 6} Z"/></g>`;
  return s + "</svg>";
}

function graphBars(graph) {
  return String(graph || "").split("|").map((p) => p.trim().match(/^(.*) \((\w+)\) ([\d.]+)$/)).filter(Boolean).map((m) => `
    <div class="hbar"><span class="cause c-${m[2]}" title="${esc(m[1])}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m[1])}</span>
      <div class="b"><i data-w="${(+m[3] * 100).toFixed(0)}%" style="background:var(--c-${m[2]})"></i></div><span class="num small">${fmt.n(+m[3], 2)}</span></div>`).join("");
}

async function renderCase(el, id, ctx) {
  el.innerHTML = `<div class="skel" style="height:420px"></div>`;
  const c = await ctx.api.get(`/api/case/${id}`);
  const i0 = c.incidents[0], sc = c.signal_check || {}, imp = (c.impact || [])[0], con = (c.containment || [])[0];
  const verdictSev = /^real/.test(sc.verdict) ? "critical" : /probably/.test(sc.verdict) ? "warning" : "good";
  el.innerHTML = `
    <div class="card" style="border-top:3px solid var(--c-${c.cause})">
      <div class="row"><span class="cause c-${c.cause}">${esc(c.cause)}</span><span class="muted small">case ${c.case_id} · ${c.incident_count} incident${c.incident_count > 1 ? "s" : ""} · ${fmt.dt(c.first)} → ${fmt.dt(c.last)}</span></div>
      <h2 style="margin:6px 0 2px;font-size:21px;letter-spacing:-.3px">${esc(c.culprit)}</h2>
      <div class="t2">${esc(i0.symptom)} - the cause finder is <b>${fmt.pct(i0.confidence)}</b> sure it is <b>${esc(c.cause)}</b>.</div>
      <div class="row" style="margin-top:12px">
        <button class="btn sm primary" data-ask="Explain case ${c.case_id} (${esc(c.culprit)}): is it real, why did it happen, what do I do next?">${icon("spark")} Ask copilot</button>
        <button class="btn sm ghost" data-go="map" data-case="${c.case_id}">${icon("pin")} On the map</button>
        ${con ? `<button class="btn sm" data-go="contain" data-case="${c.case_id}">${icon("shield")} Containment: ${esc(con.action)}</button>` : ""}
        ${c.change_record?.length ? `<button class="btn sm" data-go="change">${icon("clipboard")} Change ${esc(c.change_record[0].change_id)}</button>` : ""}
        ${c.maintenance?.length ? `<button class="btn sm" data-go="maintain" data-eq="${esc(c.culprit.split(" ").pop())}">${icon("wrench")} Maintenance plan</button>` : ""}
      </div>
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h3>Is it real?</h3><span class="sp"></span><span class="pill ${verdictSev}">${icon(verdictSev === "good" ? "good" : "serious")} ${esc(String(sc.verdict || "-").split(" - ")[0])}</span></div>
        <div class="small t2" style="margin:-4px 0 10px">${esc(String(sc.verdict || "").split(" - ")[1] || "")} - this case's window vs the rest of the week</div>
        ${sc.window_kpis ? `
        <div class="hbar"><span>flagged cycles</span><div class="b"><i data-w="${Math.min(100, sc.window_kpis.flagged_rate * 100 * 1.6)}%" style="background:var(--serious)"></i></div><span class="num small">${fmt.pct(sc.window_kpis.flagged_rate, 1)}</span></div>
        <div class="hbar"><span class="muted">rest of week</span><div class="b"><i data-w="${Math.min(100, sc.rest_of_week.flagged_rate * 100 * 1.6)}%" style="background:var(--text-2)"></i></div><span class="num small">${fmt.pct(sc.rest_of_week.flagged_rate, 1)}</span></div>
        <div class="sep"></div>
        <dl class="kv"><dt>flag ratio</dt><dd><b>${fmt.n(sc.flag_ratio, 1)}×</b> the normal rate</dd>
          <dt>${esc(c.incidents[0].station === "st013" ? "leak rate" : "torque")} mean</dt><dd>${fmt.n(sc.window_kpis.primary_mean, 2)} vs ${fmt.n(sc.rest_of_week.primary_mean, 2)}</dd>
          <dt>re-hits per car</dt><dd>${fmt.n(sc.window_kpis.retries_per_car, 2)} vs ${fmt.n(sc.rest_of_week.retries_per_car, 2)}</dd>
          <dt>median cycle</dt><dd>${fmt.n(sc.window_kpis.median_cycle_s, 1)} s vs ${fmt.n(sc.rest_of_week.median_cycle_s, 1)} s</dd></dl>` : `<div class="muted">${esc(sc.error || "")}</div>`}
      </div>
      <div class="card"><div class="card-h"><h3>Why - cause finder</h3><span class="hint">machine · people · method · station</span></div>
        ${CAUSES.map((k) => `<div class="hbar"><span class="cause c-${k}">${k}</span><div class="b"><i data-w="${(i0["p_" + k] * 100).toFixed(0)}%" style="background:var(--c-${k})"></i></div><span class="num small">${fmt.pct(i0["p_" + k])}</span></div>`).join("")}
        <div class="sep"></div><div class="small t2"><b>Evidence:</b> ${esc(i0.evidence)}</div>
        <div class="small muted" style="margin:10px 0 4px">Evidence graph - which suspect explains the pattern best</div>${graphBars(i0.graph)}
      </div>
    </div>
    <div class="card"><div class="card-h"><h3>What the floor said</h3><span class="hint">floor listener - notes, maintenance log, supervisor answers</span></div>
      ${(c.floor_said || []).map((f) => `<div class="li"><div class="ic ${f.link === "early warning" ? "warning" : f.link === "disputes" ? "serious" : f.link === "agrees" ? "good" : "info"}">${icon(f.kind === "answer" ? "users" : "notes")}</div>
        <div><div class="li-t">${esc(f.kind)} · ${fmt.day(f.shift_date)} shift ${esc(f.shift)} <span class="pill ${f.link === "early warning" ? "warning" : f.link === "disputes" ? "serious" : f.link === "agrees" ? "good" : ""}">${esc(f.link)}${f.lead_h ? ` · ${fmt.n(f.lead_h)} h early` : ""}</span>
          ${f.confirms && f.confirms !== "n/a" ? `<span class="pill ${f.confirms === "yes" ? "good" : f.confirms === "no" ? "serious" : ""}">supervisor: ${esc(f.confirms)}</span>` : ""}</div>
        <div class="li-d quote">"${esc(f.quote)}"</div>${f.decision ? `<div class="small muted">decision: ${esc(f.decision)}</div>` : ""}</div></div>`).join("") || `<div class="muted small">Nobody wrote about this one - only the data saw it.</div>`}
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h3>What it costs</h3><span class="hint">impact ranker</span></div>
        ${imp ? `<div class="row"><span class="pill ${imp.category === "High" ? "critical" : imp.category === "Medium" ? "warning" : ""}">${esc(imp.category)} · rank ${imp.rank}</span><span class="strong num">${fmt.n(imp.priority)}</span><span class="muted small">${esc(imp.status)}</span></div>
          ${imp.rule ? `<div class="rule" style="margin-top:8px">${icon("critical")} ${esc(imp.rule)}</div>` : ""}
          <dl class="kv" style="margin-top:10px"><dt>lost cars</dt><dd>${fmt.n(imp.lost_cars)}</dd><dt>cars to check</dt><dd>${fmt.n(imp.definite_cars)}</dd><dt>next</dt><dd>${esc(imp.action)}</dd></dl>` : `<div class="muted small">Not on the impact list.</div>`}
      </div>
      <div class="card"><div class="card-h"><h3>Incidents in this case</h3></div>
        <div class="scroll" style="max-height:220px"><table class="tbl"><tr><th>#</th><th>shift</th><th>symptom</th><th class="n">sure</th></tr>
        ${c.incidents.map((i) => `<tr><td>${i.incident_id}</td><td>${fmt.day(i.shift_date)} ${esc(i.shift)}</td><td>${esc(i.symptom)}</td><td class="n">${fmt.pct(i.confidence)}</td></tr>`).join("")}</table></div>
      </div>
    </div>
    ${c.people ? `<div class="card"><div class="card-h"><h3>${icon("users")} Support for ${esc(c.culprit)}</h3><span class="hint">${esc(c.people.note)}</span></div>
      ${c.people.findings.slice(0, 6).map((p) => `<div class="li"><div class="ic info">${icon("users")}</div><div><div class="li-t">${fmt.day(p.shift_date)} shift ${esc(p.shift)} · ${esc(p.finding_type)}</div>
        <div class="li-d">${esc(p.finding)}</div>${p.cause_check ? `<div class="small muted">${esc(p.cause_check)}</div>` : ""}</div></div>`).join("")}</div>` : ""}`;
  stagger(el); animateIn(el);
}

export async function render(root, params, ctx) {
  const [tl, people] = await Promise.all([ctx.api.get("/api/timeline"), ctx.api.get("/api/people")]);
  const cases = {};
  tl.incidents.forEach((i) => {
    const c = cases[i.case_id] || (cases[i.case_id] = { id: i.case_id, cause: i.cause, culprit: i.culprit, station: i.top_station, n: 0, first: i.start_ts, conf: 0, family: i.family });
    c.n += 1; c.conf = Math.max(c.conf, i.confidence);
  });
  const list = Object.values(cases).sort((a, b) => (a.first < b.first ? -1 : 1));
  let filter = "all", sel = +(params.case || 0) || (list.find((c) => c.culprit.includes("NR-012")) || list[0]).id;

  root.innerHTML = `
    <div class="card">
      <div class="row" style="margin-bottom:10px">
        <div class="chips" id="cf"><button class="chip on" data-c="all">All causes <span class="n">${tl.incidents.length}</span></button>
          ${CAUSES.map((c) => `<button class="chip" data-c="${c}"><span class="cause c-${c}"></span>${c} <span class="n">${tl.incidents.filter((i) => i.cause === c).length}</span></button>`).join("")}</div>
        <div class="sp"></div>
        <div class="legend"><span>${`<svg width="14" height="10"><polyline points="0,7 4,3 8,6 14,2" fill="none" stroke="var(--text-2)" stroke-width="1.5"/></svg>`} signal</span>
          <span><span class="sw" style="background:var(--serious);opacity:.35"></span>flagged cycles</span>
          <span>▲ early warning</span><span>○ notes only</span><span>✕ supervisor disputes</span><span>◆ containment</span><span style="color:var(--good)">● repair</span>
          <span style="color:var(--critical)">┆ WI blocked by check</span></div>
      </div>
      <div id="tl">${timelineSVG(tl, filter)}</div>
      <div class="small muted" style="margin-top:6px">Hover anything for details · click an incident or a containment marker to open its case · press <span class="kbd" style="border-color:var(--border-2)">R</span> to replay the week on this timeline</div>
    </div>
    <div class="split r" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>Problem cases</h3><span class="hint">${list.length} cases from ${tl.incidents.length} incidents</span></div>
        <div class="grid" id="cases" style="gap:8px"></div></div>
      <div class="stack" id="detail"></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>${icon("users")} People - support, not blame</h3><span class="hint">${esc(people.note)} · ${people.count} findings</span></div>
      <div class="scroll" style="max-height:300px"><table class="tbl"><tr><th>operator</th><th>shift</th><th>finding</th><th>what it means</th><th>cause finder cross-check</th></tr>
      ${people.findings.map((p) => `<tr><td class="mono">${esc(p.operator_id)}</td><td>${fmt.day(p.shift_date)} ${esc(p.shift)}</td><td><span class="pill">${esc(p.finding_type)}</span></td>
        <td>${esc(p.finding)}</td><td class="small t2">${esc(p.cause_check || "")}</td></tr>`).join("")}</table></div></div>`;

  const drawCases = () => {
    $("#cases", root).innerHTML = list.filter((c) => filter === "all" || c.cause === filter).map((c) => `
      <div class="case-c c-${c.cause} ${c.id === sel ? "sel" : ""}" data-case-sel="${c.id}">
        <div class="row"><span class="cause c-${c.cause}">${esc(c.cause)}</span><span class="pill">${esc(c.family)}</span><span class="sp"></span><span class="small muted">${c.station.toUpperCase()} · ${fmt.dt(c.first)}</span></div>
        <div class="strong">${esc(c.culprit)}</div><div class="small muted">case ${c.id} · ${c.n} incident${c.n > 1 ? "s" : ""} · up to ${fmt.pct(c.conf)} sure</div></div>`).join("");
  };
  const select = (id) => {
    sel = +id; drawCases();
    $$("#tl .inc", root).forEach((r) => r.classList.toggle("sel", +r.dataset.case === sel));
    renderCase($("#detail", root), sel, ctx);
  };
  drawCases(); select(sel);
  stagger(root);

  $("#cf", root).onclick = (e) => {
    const c = e.target.closest("[data-c]"); if (!c) return;
    filter = c.dataset.c; $$("#cf .chip", root).forEach((x) => x.classList.toggle("on", x === c));
    $("#tl", root).innerHTML = timelineSVG(tl, filter); drawCases();
    $$("#tl .inc", root).forEach((r) => r.classList.toggle("sel", +r.dataset.case === sel));
  };
  root.onclick = (e) => {
    const t = e.target.closest("[data-case-sel]") || e.target.closest("#tl [data-case]");
    if (t) select(t.dataset.caseSel || t.dataset.case);
    const n = e.target.closest("#tl [data-note]"); if (n) ctx.go("notes", { note: n.dataset.note });
  };
  // replay cursor on the timeline
  const t0 = +parse(tl.start), t1 = +parse(tl.end);
  const onReplay = (e) => {
    const g = $("#tlCursor"); if (!g) return;
    if (!e.detail) { g.style.display = "none"; return; }
    const X = L + ((e.detail.t - t0) / (t1 - t0)) * (W - L - R);
    g.style.display = ""; g.setAttribute("transform", `translate(${X},0)`);
    $$("#tl .inc", root).forEach((r) => { r.style.opacity = +parse(r.dataset.start) <= e.detail.t ? "" : ".08"; });
  };
  window.addEventListener("replay", onReplay);
  ctx.cleanup.push(() => window.removeEventListener("replay", onReplay));
}
