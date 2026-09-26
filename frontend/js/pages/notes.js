// Shift notes - the raw text people wrote next to what the floor listener (GPT-5) extracted from it.
import { $, $$, esc, fmt, icon, animateIn, stagger, CAT_ICON } from "../util.js";

const LINK = { agrees: "good", "early warning": "warning", "notes only": "info", disputes: "serious", unclear: "" };
const FILTERS = [["all", "All"], ["early warning", "Early warnings"], ["notes only", "Only people saw it"], ["disputes", "Disputes"], ["safety", "Safety"], ["answer", "Supervisor answers"]];

function factChip(f) {
  return `<div class="fact" data-tip="${esc(f.symptom || "")}${f.action ? "<br><b>action:</b> " + esc(f.action) : ""}">
    <span class="${f.category === "safety" ? "" : "cause c-" + ({ material: "station", supply: "station", it: "station" }[f.category] || f.category)}">${icon(CAT_ICON[f.category] || "info")}</span>
    <b>${esc(f.subject || f.category)}</b><span class="muted">${esc(String(f.station || "").toUpperCase())}</span><span class="pill">${esc(f.status)}</span>
    <span class="pill ${LINK[f.link] ?? ""}">${esc(f.link)}${f.lead_h ? ` · ${fmt.n(f.lead_h)} h early` : ""}${f.incident_id ? ` · #${f.incident_id}` : ""}</span></div>`;
}

export async function render(root, params, ctx) {
  const d = await ctx.api.get("/api/notes");
  const e = d.eval || {};
  let filter = "all";
  root.innerHTML = `
    <div class="grid g4" id="nk">
      <div class="card kpi"><div class="kpi-l">${icon("notes")} Notes read</div><div class="kpi-v"><span data-count="${d.notes.length}">0</span></div><div class="kpi-s">handovers, maintenance log, supervisor answers</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("spark")} Read by</div><div class="kpi-v" style="font-size:22px">${esc(e.backend || "-")}</div><div class="kpi-s">strict JSON schema · cached for the demo</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("good")} Facts found</div><div class="kpi-v"><span data-count="${+e.facts_found || 0}">0</span><small>/ ${esc(e.facts_true || "-")}</small></div><div class="kpi-s">precision ${fmt.pct(+e.precision)} vs the answer key</div></div>
      <div class="card kpi"><div class="kpi-l">${icon("check")} Fields right</div><div class="kpi-v"><span data-count="${Math.round((+e.field_acc || 0) * 100)}" data-suffix="%">0</span></div><div class="kpi-s">supervisor answers read right ${esc(e.answers_ok || "-")}/${esc(e.answers || "-")}</div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="row" style="margin-bottom:12px"><div class="chips" id="nf">${FILTERS.map(([k, l]) => `<button class="chip ${k === filter ? "on" : ""}" data-f="${k}">${l}</button>`).join("")}</div>
        <div class="sp"></div><button class="btn sm" data-ask="What did the shifts report that the data did not show?">${icon("spark")} Ask copilot</button></div>
      <div class="grid" id="nl" style="gap:12px"></div></div>`;
  stagger($("#nk", root)); animateIn(root);

  const draw = () => {
    const keep = d.notes.filter((n) => filter === "all" || (filter === "answer" ? n.kind === "answer"
      : filter === "safety" ? n.facts.some((f) => f.category === "safety") : n.facts.some((f) => f.link === filter)));
    $("#nl", root).innerHTML = keep.map((n) => `
      <div class="card" id="note-${n.note_id}" style="box-shadow:none;background:var(--surface-2)${+params.note === n.note_id ? ";border-color:var(--accent)" : ""}">
        <div class="row" style="margin-bottom:8px"><span class="pill ${n.kind === "answer" ? "info" : n.kind === "maintenance" ? "good" : ""}">${icon(n.kind === "answer" ? "users" : n.kind === "maintenance" ? "wrench" : "notes")} ${esc(n.kind)}</span>
          <b>${esc(n.author)}</b><span class="muted small">${fmt.dt(n.written_ts)} · shift ${esc(n.shift)} of ${fmt.day(n.shift_date)}</span>
          ${n.case_id ? `<span class="sp"></span><span class="link-chip" data-go="investigate" data-case="${n.case_id}">case #${n.case_id}</span>` : ""}</div>
        <div class="note-card">
          <div>${n.question ? `<div class="small muted" style="margin-bottom:4px">${icon("spark")} Copilot asked: ${esc(n.question)}</div>` : ""}<div class="note-raw">${esc(n.text)}</div></div>
          <div><div class="small muted" style="margin-bottom:6px">${n.facts.length} fact${n.facts.length === 1 ? "" : "s"} extracted${n.facts[0]?.backend ? " · " + esc(n.facts[0].backend) : ""}</div>
            <div class="grid" style="gap:6px">${n.facts.map(factChip).join("") || `<span class="muted small">nothing with production meaning - chit-chat skipped</span>`}</div>
            ${n.facts.find((f) => f.confirms && f.confirms !== "n/a") ? (() => { const f = n.facts.find((x) => x.confirms && x.confirms !== "n/a");
              return `<div class="row" style="margin-top:8px"><span class="pill ${f.confirms === "yes" ? "good" : f.confirms === "no" ? "serious" : ""}">supervisor ${f.confirms === "yes" ? "confirms" : f.confirms === "no" ? "disputes" : "is unsure about"} the cause</span>${f.decision ? `<span class="small t2">${esc(f.decision)}</span>` : ""}</div>`; })() : ""}
          </div></div></div>`).join("") || `<div class="empty">No notes in this filter.</div>`;
    stagger($("#nl", root));
  };
  draw();
  if (params.note) setTimeout(() => $(`#note-${params.note}`, root)?.scrollIntoView({ behavior: "smooth", block: "center" }), 350);
  $("#nf", root).onclick = (ev) => { const b = ev.target.closest("[data-f]"); if (!b) return; filter = b.dataset.f;
    $$("#nf .chip", root).forEach((x) => x.classList.toggle("on", x === b)); draw(); };
}
