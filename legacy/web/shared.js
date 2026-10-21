/* Display helpers shared by the organizer dashboard and the judge page. */
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtDate = s => s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
const fmtDay = s => s ? new Date(s).toLocaleDateString(undefined, { dateStyle: "medium" }) : "";
const ago = s => { if (!s) return ""; const m = Math.round((Date.now() - new Date(s)) / 60000); return m < 1 ? "just now" : m < 60 ? m + " min ago" : m < 1440 ? Math.round(m / 60) + " h ago" : fmtDate(s); };

function renderPreEvent(pe) {
  if (!pe || !pe.start_set) return `<div class="preevent"><b>Not checked.</b> No hackathon start time was set, so work done before the event cannot be identified. Set the start time (event Settings, or the dates on Quick check) and evaluate again.</div>`;
  const repoNote = pe.repo_created_at ? `<div class="tiny muted" style="margin-top:6px">Repository created ${fmtDay(pe.repo_created_at)}${pe.repo_created_days_before_start > 0 ? `, ${Math.round(pe.repo_created_days_before_start)} day(s) before the start` : ""}.</div>` : "";
  if (!pe.commits_before) return `<div class="preevent clear"><b>No commits before the start.</b> All ${pe.total_commits} commits were made after the hackathon began.${repoNote}</div>`;
  const sev = (pe.share_lines >= 0.3 || pe.share_commits >= 0.5 || pe.commits_before >= 10) ? "high" : "medium";
  return `<div class="preevent ${sev}"><b>${pe.commits_before} of ${pe.total_commits} commits (${Math.round(pe.share_lines * 100)}% of the code by lines) were made before the hackathon started.</b>
    <div class="tiny muted" style="margin-top:4px">Earliest commit ${fmtDay(pe.earliest)}; latest before the start ${fmtDay(pe.latest_before)}. A judge should ask what was built during the event.</div>
    <table><thead><tr><th>Date</th><th>Commit message</th><th class="num">Lines added</th></tr></thead><tbody>${(pe.sample || []).map(c => `<tr><td class="code">${esc(c.date)}</td><td>${esc(c.subject)}</td><td class="num">${c.lines_added}</td></tr>`).join("")}</tbody></table>${repoNote}</div>`;
}

function renderAlignment(al) {
  if (!al) return `<p class="muted">No pitch was attached, so nothing was compared. Add a project description and deck to check the pitch against the code.</p>`;
  const li = a => (a || []).map(x => `<li>${esc(x)}</li>`).join("");
  const pf = al.problem_fit || {}, ds = al.declared_summary_check || {};
  const label = { supported: "Supported", partial: "Partly", unsupported: "Not in code", not_checkable: "Can’t check" };
  return `<div class="row" style="margin-top:6px"><div class="bigscore" style="font-size:2.8rem">${al.alignment_score ?? "–"}<small>/100 pitch match</small></div>
      <div class="tiny muted">${al.claims_supported ?? 0} of ${al.claims_checked ?? 0} checkable claims supported · ${esc(al.mode)}</div></div>
    ${al.mode.startsWith("heuristic") ? `<div class="notice">Offline keyword check: it finds technologies and features the pitch names that never appear in the code. Add a Gemini key for claim-by-claim checking and the problem-fit review.</div>` : ""}
    <p class="summary" style="font-size:1rem">${esc(al.summary)}</p>
    ${ds.verdict && ds.verdict !== "unclear" ? `<dl class="kv"><dt>Team’s description</dt><dd><span class="pill ${ds.verdict === "differs" ? "high" : ds.verdict === "partly" ? "medium" : "ok"}">${esc(ds.verdict)}</span> ${esc(ds.note)}</dd></dl>` : ""}
    ${(al.claims || []).length ? `<table class="claims"><thead><tr><th>Check</th><th>What the pitch says</th><th>What the code shows</th></tr></thead><tbody>${al.claims.map(c => `<tr><td><span class="pill ${esc(c.status)}">${esc(label[c.status] || c.status)}</span>${c.slide ? `<div class="tiny muted">slide ${c.slide}</div>` : ""}</td><td>${esc(c.claim)}</td><td>${esc(c.note)}${(c.evidence || []).map(x => `<div class="receipt"><span class="where">${esc(x.file)}:${esc(x.line ?? "?")}</span>${esc(x.quote)}</div>`).join("")}</td></tr>`).join("")}</tbody></table>` : ""}
    <div class="cols"><div>${(al.code_gaps_vs_deck || []).length ? `<h3 style="margin-top:20px">Promised but not in the code</h3><ul>${li(al.code_gaps_vs_deck)}</ul>` : ""}</div>
      <div>${(al.undersold_in_code || []).length ? `<h3 style="margin-top:20px">In the code but not in the pitch</h3><ul>${li(al.undersold_in_code)}</ul>` : ""}</div></div>
    ${pf.verdict ? `<h3 style="margin-top:20px">Does it solve the stated problem?</h3><p style="margin:.3rem 0"><span class="pill ${pf.verdict === "addressed" ? "ok" : pf.verdict === "not_addressed" ? "high" : pf.verdict === "partially" ? "medium" : ""}">${esc(({ addressed: "Addressed", partially: "Partly", not_addressed: "Not addressed", unclear: "Unclear" })[pf.verdict] || pf.verdict)}</span> ${esc(pf.reasoning)}</p>
      ${(pf.evidence || []).map(x => `<div class="receipt"><span class="where">${esc(x.file)}:${esc(x.line ?? "?")}</span>${esc(x.quote)}</div>`).join("")}` : ""}
    ${(al.problem_gaps || []).length ? `<h3 style="margin-top:20px">Gaps against the problem</h3><ul>${li(al.problem_gaps)}</ul>` : ""}
    ${(al.questions_for_team || []).length ? `<h3 style="margin-top:20px">Questions about the gaps</h3><ul>${li(al.questions_for_team)}</ul>` : ""}`;
}

