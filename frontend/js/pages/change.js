// Change - the method: handover sheet, the change pipeline with its gates (live actions),
// new proposals through the method checker, and every WI version with its steps and rules.
import { $, $$, esc, fmt, icon, animateIn, stagger } from "../util.js";

const COLS = [["CHECKED", "Checked", "info"], ["APPROVED", "Approved", "info"], ["PILOT", "Pilot shift", "warning"],
  ["RELEASED", "Released", "good"], ["CLOSED", "Closed", "good"], ["BLOCKED", "Blocked", "critical"]];
const V = { PASS: "good", WARN: "warning", BLOCK: "critical", INFO: "info" };
const GROUPS = ["TIME", "SAFETY", "TRACE", "PEOPLE", "REALITY"];

function changeText(ch) {
  if (ch.op === "add") return `<b>+ add</b> "${esc(ch.step.text)}" · ${fmt.n(ch.step.time_s, 1)} s${ch.step.every && ch.step.every < 1 ? ` on ${fmt.pct(ch.step.every)} of cars` : ""}${ch.step.qualification ? ` · needs ${esc(ch.step.qualification)}` : ""}${ch.step.hazard ? ` · ${esc(ch.step.hazard)}${ch.step.ppe ? "" : " <span style='color:var(--critical)'>(no PPE)</span>"}` : ""}`;
  if (ch.op === "remove") return `<b>− remove</b> step "${esc(ch.key)}"`;
  if (ch.op === "move") return `<b>↕ move</b> "${esc(ch.key)}" before "${esc(ch.before)}"`;
  if (ch.op === "edit") return `<b>✎ edit</b> "${esc(ch.key)}": ${Object.entries(ch.set).map(([k, v]) => `${esc(k)} → ${esc(v)}`).join(", ")}`;
  return esc(JSON.stringify(ch));
}

function card(c) {
  const needs = String(c.needs || "").split(",").filter(Boolean), done = c.approvals || {};
  const acts = [];
  if (c.state === "CHECKED") needs.filter((r) => !done[r]).forEach((r) => acts.push(`<button class="btn sm" data-act="approve" data-role="${r}">${icon("check")} Approve as ${r}</button>`));
  if (c.state === "APPROVED") acts.push(`<button class="btn sm" data-act="sign" data-crew="A">Sign-off crew A</button>`, `<button class="btn sm primary" data-act="pilot">${icon("play")} Pilot crew A</button>`);
  if (c.state === "PILOT") acts.push(`<button class="btn sm" data-act="sign" data-crew="all">Sign-off all crews</button>`, `<button class="btn sm primary" data-act="release">${icon("arrow")} Release</button>`);
  if (c.state === "BLOCKED" && c.source === "proposal") acts.push(`<button class="btn sm ghost" data-act="approve" data-role="engineer">Try to approve</button>`);
  return `<div class="kc ${c.state === "BLOCKED" ? "blocked" : ""}" data-cid="${esc(c.change_id)}">
    <div class="row"><span class="mono muted">${esc(c.change_id)}</span><span class="pill ${V[c.verdict] || ""}">${esc(c.verdict)}</span><span class="sp"></span>
      <span class="small muted">${c.source === "proposal" ? "proposal" : "replay"}</span></div>
    <div class="kc-t" style="margin-top:4px">${esc(c.wi_version)} <span class="muted small">${esc(c.station.toUpperCase())}</span></div>
    ${c.state === "BLOCKED" ? `<div class="kc-s" style="color:var(--critical)">${esc([...new Set(String(c.blocks || "").match(/\b[A-Z]{3,4}-\d\b/g) || [])].join(" · "))} - fix and resubmit</div>` : ""}
    ${c.outcome ? `<div class="kc-s" data-tip="${esc(c.outcome)}">${esc(c.outcome.length > 110 ? c.outcome.slice(0, 108) + "…" : c.outcome)}</div>` : ""}
    ${c.source === "proposal" ? `<div class="appr">${needs.map((r) => `<span class="${done[r] ? "done" : ""}">${done[r] ? "✓ " : ""}${r}</span>`).join("")}</div>`
      : `<div class="small muted" style="margin-top:6px">would need: ${esc(needs.join(", "))}</div>`}
    ${acts.length ? `<div class="row">${acts.join("")}</div>` : ""}</div>`;
}

export async function render(root, params, ctx) {
  const [ch, me] = await Promise.all([ctx.api.get("/api/changes"), ctx.api.get("/api/methods")]);
  const stations = [...new Set(me.versions.map((v) => v.station))];
  let station = params.station && stations.includes(params.station) ? params.station : stations[0];
  let propFile = ch.proposals[0]?.file;
  const byCol = (s) => ch.changes.filter((c) => c.state === s);

  root.innerHTML = `
    <div class="grid g2" id="ho">${ch.handover.map((h) => `
      <div class="card" style="${h.alert !== "-" ? "border-color:color-mix(in srgb,var(--critical) 50%,var(--border))" : ""}">
        <div class="card-h"><h3>${icon("clipboard")} ${esc(h.station.toUpperCase())} - next shift works to</h3><span class="sp"></span><span class="small muted">handover sheet</span></div>
        <div class="row"><span style="font-size:22px;font-weight:750;letter-spacing:-.4px">${esc(h.current)}</span><span class="muted small">since ${fmt.dt(h.since)} · ${esc(h.what_changed)}</span></div>
        ${h.alert !== "-" ? `<div class="banner critical" style="margin-top:10px;grid-template-columns:34px 1fr;padding:10px 12px"><div class="ic" style="width:34px;height:34px;color:var(--critical)">${icon("critical")}</div><div class="small"><b>${esc(h.alert)}</b></div></div>` : ""}
        <dl class="kv" style="margin-top:10px"><dt>next</dt><dd>${esc(h.next)}</dd><dt>still to sign</dt><dd>${esc(h.must_sign)}</dd>
          <dt>not for use</dt><dd>${h.do_not_use !== "-" ? `<span class="pill critical">${esc(h.do_not_use)}</span>` : "-"}</dd><dt>pilot</dt><dd>${esc(h.pilot)}</dd></dl>
      </div>`).join("")}</div>

    <div class="card" style="margin-top:16px">
      <div class="card-h"><h3>Change pipeline</h3><span class="hint">check → approve (by role) → pilot one crew, one shift → release → after-check. Nothing skips a gate.</span>
        <span class="sp"></span><button class="btn sm ghost" id="reset">${icon("reset")} Reset demo</button></div>
      <div class="kanban" id="kb">${COLS.map(([k, l, sev]) => `<div class="col"><div class="col-h"><span class="dot" style="background:var(--${sev})"></span>${l}<span class="sp"></span><span class="badge soft">${byCol(k).length}</span></div>
        ${byCol(k).map(card).join("") || `<div class="small muted" style="padding:6px">-</div>`}</div>`).join("")}</div>
    </div>

    <div class="split" style="margin-top:16px">
      <div class="card"><div class="card-h"><h3>New proposal</h3><span class="hint">run it through the method checker before anyone approves it</span></div>
        <div class="row" style="margin-bottom:10px"><div class="chips" id="pf">${ch.proposals.map((p) => `<button class="chip ${p.file === propFile ? "on" : ""}" data-p="${esc(p.file)}">${esc(p.spec.wi_version)}</button>`).join("")}</div></div>
        <div id="pv"></div>
        <div class="row" style="margin-top:12px"><button class="btn" id="chk">${icon("clipboard")} Run method check</button>
          <button class="btn primary" id="sub">${icon("send")} Submit to change manager</button>
          <button class="btn ghost" data-ask="Check my proposal ${esc(propFile || "")} - will it pass, and who has to approve it?">${icon("spark")} Ask copilot</button></div>
        <div id="pr" style="margin-top:12px"></div>
      </div>
      <div class="card"><div class="card-h"><h3>How the gates work</h3></div>
        <div class="li"><div class="ic info">${icon("clipboard")}</div><div><div class="li-t">Method check</div><div class="li-d">Takt ${me.takt_s} s, safety (critical-step checks, control plan, torque, lifting, PPE), one VIN scan, training. BLOCK stops here.</div></div></div>
        <div class="li"><div class="ic info">${icon("users")}</div><div><div class="li-t">Approval by role</div><div class="li-d">Engineer always; quality for critical, check, scan or control-plan steps; supervisor when operators' work or training changes.</div></div></div>
        <div class="li"><div class="ic warning">${icon("play")}</div><div><div class="li-t">Pilot - one crew, one shift</div><div class="li-d">Only when every operator of that crew has signed the new version.</div></div></div>
        <div class="li"><div class="ic good">${icon("good")}</div><div><div class="li-t">Release + after-check</div><div class="li-d">All crews signed. First shift: cycle within takt, hands-on within 8% of plan, no incident blamed on it - else roll back.</div></div></div>
        ${me.eval?.weeks ? `<div class="sep"></div><div class="small t2">Tested on ${me.eval.weeks} simulated weeks: caught <b>${me.eval.caught}/${me.eval.bad}</b> bad versions, <b>${me.eval.false_alarms}</b> false alarms on ${me.eval.harmless} harmless ones.</div>` : ""}
      </div>
    </div>

    <div class="card" style="margin-top:16px"><div class="card-h"><h3>Work-instruction versions</h3><span class="hint">what each version asks, and what the method checker says</span><span class="sp"></span>
        <div class="chips" id="sf">${stations.map((s) => `<button class="chip ${s === station ? "on" : ""}" data-s="${s}">${s.toUpperCase()}</button>`).join("")}</div></div>
      <div id="wv"></div></div>`;
  stagger(root); animateIn(root);

  // ---- proposal preview / check / submit
  const drawProp = () => {
    const p = ch.proposals.find((x) => x.file === propFile); if (!p) { $("#pv", root).innerHTML = `<div class="muted">No proposals in proposals/</div>`; return; }
    const s = p.spec;
    $("#pv", root).innerHTML = `<div class="row"><span class="strong">${esc(s.wi_version)}</span><span class="muted small">based on ${esc(s.base)} · rollout ${fmt.dt(s.rollout)}</span></div>
      <div class="t2" style="margin:4px 0 8px">${esc(s.change_note)}</div>
      <div class="steps">${s.changes.map((c) => `<div class="step" style="grid-template-columns:1fr"><span>${changeText(c)}</span></div>`).join("")}</div>`;
    $("#pr", root).innerHTML = "";
    const a = $("[data-ask]", $("#chk", root).parentElement); if (a) a.dataset.ask = `Check my proposal ${propFile} - will it pass, and who has to approve it?`;
  };
  drawProp();
  $("#pf", root).onclick = (e) => { const b = e.target.closest("[data-p]"); if (!b) return; propFile = b.dataset.p;
    $$("#pf .chip", root).forEach((x) => x.classList.toggle("on", x === b)); drawProp(); };
  $("#chk", root).onclick = async () => {
    const r = await ctx.api.post("/api/changes/check", { file: propFile });
    const sev = V[r.verdict] || "info", pct = r.planned_cycle_s / me.takt_s;
    $("#pr", root).innerHTML = `<div class="banner ${sev}" style="grid-template-columns:40px 1fr"><div class="ic" style="width:40px;height:40px;color:var(--${sev})">${icon(sev === "good" ? "good" : "critical")}</div>
      <div><h2 style="font-size:16px">${esc(r.wi_version)}: ${esc(r.verdict)}</h2><div class="small t2">${esc(r.next_step)} · approvals needed: <b>${esc((r.approvals_needed || []).join(", "))}</b></div></div></div>
      <div class="hbar" style="grid-template-columns:120px 1fr 70px;margin-top:12px"><span>planned cycle</span><div class="b" style="position:relative"><i data-w="${Math.min(100, pct * 90)}%" style="background:${pct > 1 ? "var(--critical)" : pct > 0.95 ? "var(--warning)" : "var(--good)"}"></i>
        <span class="tick" style="position:absolute;left:90%;top:-4px;bottom:-4px;width:2px;background:var(--text)"></span></div><span class="num small">${fmt.n(r.planned_cycle_s, 1)} s / ${me.takt_s}</span></div>
      ${(r.rules_not_passed || []).filter((x) => x.status !== "INFO").map((x) => `<div class="li"><div class="ic ${V[x.status]}">${icon(x.status === "BLOCK" ? "critical" : "warning")}</div><div><div class="li-t">${esc(x.rule)} <span class="pill ${V[x.status]}">${esc(x.status)}</span></div><div class="li-d">${esc(x.message)}</div></div></div>`).join("") || `<div class="small muted" style="margin-top:8px">${icon("good")} every rule passes</div>`}`;
    animateIn($("#pr", root));
  };
  $("#sub", root).onclick = async () => {
    try {
      const r = await ctx.api.post("/api/changes/submit", { file: propFile });
      ctx.toast({ severity: r.state === "BLOCKED" ? "critical" : "good", title: `${r.change_id} ${r.wi_version}: ${r.state}`,
        detail: r.state === "BLOCKED" ? r.blocks : `needs ${r.needs.replace(/,/g, ", ")}` });
      await render(root, { ...params, station }, ctx); ctx.refreshAlerts();
      $(`[data-cid="${r.change_id}"]`, root)?.classList.add("flash");
    } catch (e) { ctx.toast({ severity: "warning", title: "Could not submit", detail: e.message }); }
  };
  $("#reset", root).onclick = async () => {
    await ctx.api.post("/api/changes/reset");
    ctx.toast({ severity: "info", title: "Demo reset", detail: "The week's changes replayed; the proposals are not submitted yet.", timeout: 3500 });
    await render(root, params, ctx); ctx.refreshAlerts();
  };

  // ---- gate actions on the kanban cards
  $("#kb", root).onclick = async (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const card = b.closest("[data-cid]"), cid = card.dataset.cid, act = b.dataset.act;
    b.disabled = true;
    try {
      const body = { role: b.dataset.role, crew: b.dataset.crew, by: b.dataset.role };
      const r = await ctx.api.post(`/api/changes/${cid}/${act}`, body);
      const msg = act === "sign" ? `Signed: ${(r.signed || []).join(", ") || "nobody left"}` : `${cid} is now ${r.state}`;
      ctx.toast({ severity: "good", title: act === "approve" ? `Approved as ${b.dataset.role}` : act === "sign" ? "Sign-off recorded" : act === "pilot" ? "Pilot started" : "Released to all crews", detail: msg, timeout: 3500 });
      await render(root, { ...params, station }, ctx); ctx.refreshAlerts();
      $(`[data-cid="${cid}"]`, root)?.classList.add("flash");
    } catch (err) {
      card.classList.remove("refuse"); void card.offsetWidth; card.classList.add("refuse");
      ctx.toast({ severity: "warning", title: "Gate refused", detail: err.message, timeout: 7000 });
      b.disabled = false;
    }
  };

  // ---- WI versions
  const drawVersions = () => {
    const vs = me.versions.filter((v) => v.station === station).sort((a, b) => (+a.wi_version.split("v")[1]) - (+b.wi_version.split("v")[1]));
    let selV = vs.find((v) => v.verdict === "BLOCK" && v.source === "history")?.wi_version || vs[0].wi_version;
    const drawOne = () => {
      const v = vs.find((x) => x.wi_version === selV), idx = vs.indexOf(v);
      const prevKeys = new Set(me.steps.filter((s) => s.wi_version === (vs[idx - 1] || {}).wi_version).map((s) => s.key));
      const steps = me.steps.filter((s) => s.wi_version === selV);
      const checks = me.checks.filter((c) => c.wi_version === selV);
      const worst = (g) => { const x = checks.filter((c) => c.grp === g); if (!x.length) return ["INFO", "n/a"];
        const o = ["BLOCK", "WARN", "INFO", "PASS"].find((s) => x.some((c) => c.status === s)); return [o, x.filter((c) => c.status === o && o !== "PASS").map((c) => c.rule).join(" ") || "ok"]; };
      const max = me.takt_s * 1.15;
      $("#vd", root).innerHTML = `
        <div class="split" style="grid-template-columns:minmax(0,1fr) minmax(0,1.1fr)">
          <div><div class="row"><span style="font-size:18px;font-weight:700">${esc(v.wi_version)}</span><span class="pill ${V[v.verdict]}">${esc(v.verdict)}</span>
              <span class="muted small">${v.source === "proposal" ? "proposal" : v.change_note === "Baseline" ? "baseline" : "live " + fmt.dt(v.valid_from)}</span></div>
            <div class="t2" style="margin:4px 0 12px">${esc(v.change_note)}</div>
            <div class="small muted">planned time per car</div>
            <div style="position:relative;height:30px;background:var(--surface-2);border-radius:8px;margin-top:6px;overflow:visible;display:flex">
              <div style="width:${(v.planned_op_s / max) * 100}%;background:var(--accent-2);border-radius:8px 0 0 8px;display:grid;place-items:center;color:#fff;font-size:11px;font-weight:700;animation:grow .7s both;transform-origin:left">hands-on ${fmt.n(v.planned_op_s, 1)} s</div>
              <div style="width:${(v.planned_machine_s / max) * 100}%;background:var(--faint);display:grid;place-items:center;font-size:11px;font-weight:700;animation:grow .7s .1s both;transform-origin:left">machine ${fmt.n(v.planned_machine_s, 1)} s</div>
              <div style="position:absolute;left:${(me.takt_s / max) * 100}%;top:-6px;bottom:-6px;width:2px;background:var(--critical)" data-tip="takt ${me.takt_s} s"></div>
            </div>
            <div class="row small" style="margin-top:6px"><b class="num">${fmt.n(v.planned_cycle_s, 1)} s</b><span class="muted">= ${fmt.pct(v.pct_takt)} of the ${me.takt_s} s takt</span></div>
            <div class="matrix" style="margin-top:14px">${GROUPS.map((g) => { const [st, txt] = worst(g); return `<div class="mx ${st}"><b>${g}</b>${esc(txt)}</div>`; }).join("")}</div>
            ${checks.filter((c) => c.status === "BLOCK" || c.status === "WARN").map((c) => `<div class="li"><div class="ic ${V[c.status]}">${icon(c.status === "BLOCK" ? "critical" : "warning")}</div><div><div class="li-t">${esc(c.rule)}</div><div class="li-d">${esc(c.message)}</div></div></div>`).join("")}
          </div>
          <div><div class="small muted" style="margin-bottom:6px">${steps.length} steps · red edge = new or changed in this version</div>
            <div class="steps">${steps.map((s) => `<div class="step ${idx > 0 && !prevKeys.has(s.key) ? "new" : ""}"><span class="no">${s.step_no}</span>
              <span>${esc(s.text)}<span class="small muted"> · ${s.kind}${s.role !== "operator" ? " · " + esc(s.role) : ""}${s.qualification ? " · " + esc(s.qualification) : ""}</span></span>
              <span class="row" style="gap:5px">${s.critical ? `<span data-tip="critical step">${icon("shield")}</span>` : ""}${s.kind === "scan" ? `<span data-tip="VIN scan">${icon("scan")}</span>` : ""}
                ${s.hazard ? `<span data-tip="${esc(s.hazard)}${s.ppe ? " - PPE: " + esc(s.ppe) : " - no PPE listed"}" style="color:${s.ppe ? "var(--text-2)" : "var(--critical)"}">${icon("flask")}</span>` : ""}
                ${s.manual_torque_nm ? `<span data-tip="${s.manual_torque_nm} Nm by hand" style="color:var(--serious)">${icon("wrench")}</span>` : ""}
                <b class="num small">${fmt.n(s.time_s, 1)} s${s.every < 1 ? ` ×${fmt.pct(s.every)}` : ""}</b></span></div>`).join("")}</div></div>
        </div>`;
      $$("#vc .vchip", root).forEach((x) => x.classList.toggle("sel", x.dataset.v === selV));
    };
    $("#wv", root).innerHTML = `<div class="vchips" id="vc">${vs.map((v) => `<div class="vchip" data-v="${esc(v.wi_version)}"><b>${esc(v.wi_version)} <span class="pill ${V[v.verdict]}" style="height:18px">${esc(v.verdict)}</span></b>
      <small>${v.source === "proposal" ? "proposal" : v.change_note === "Baseline" ? "baseline" : fmt.dt(v.valid_from)}</small></div>`).join(`<span class="muted">${icon("chev")}</span>`)}</div>
      <div class="sep"></div><div id="vd"></div>`;
    $("#vc", root).onclick = (e) => { const c = e.target.closest("[data-v]"); if (!c) return; selV = c.dataset.v; drawOne(); };
    drawOne();
  };
  drawVersions();
  $("#sf", root).onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; station = b.dataset.s;
    $$("#sf .chip", root).forEach((x) => x.classList.toggle("on", x === b)); drawVersions(); };
}
