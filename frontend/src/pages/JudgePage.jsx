import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { pub } from "../api.js";
import { Alignment, DeckText, Flags, LinkRow, PreEvent, Receipt } from "../components/Report.jsx";
import { Avatar, Pill } from "../components/ui.jsx";
import { clamp } from "../utils.js";

const STATUS = { "not started": ["", "Not started"], draft: ["medium", "Draft"], submitted: ["ok", "Submitted"], recused: ["", "Recused"] };
const SPill = ({ s }) => { const [k, l] = STATUS[s] || ["", s]; return <Pill kind={k}>{l}</Pill>; };
const List = ({ items }) => <ul>{(items || []).map((x, i) => <li key={i}>{x}</li>)}</ul>;

function Scorecard({ code, S, D, onSaved }) {
  const t = D.team, ev = D.evaluation, my = D.my, crit = S.criteria;
  const aiBy = useMemo(() => Object.fromEntries(((ev && ev.criteria) || []).map(c => [c.id, c])), [ev]);
  const init = () => Object.fromEntries(crit.map(c => {
    const mine = my?.scores?.[c.id], ai = aiBy[c.id];
    return [c.id, { score: mine ? String(mine.score) : ai ? String(ai.score) : "", note: mine?.note || "" }];
  }));
  const [vals, setVals] = useState(init);
  const [comment, setComment] = useState(my?.comment || "");
  const [conflict, setConflict] = useState(!!my?.conflict);
  const [msg, setMsg] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { // warn before leaving with unsaved scores
    const h = e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);
  const edit = (id, k, v) => { setVals(a => ({ ...a, [id]: { ...a[id], [k]: v } })); setDirty(true); };
  let num = 0, den = 0, n = 0;
  crit.forEach(c => { const v = parseFloat(vals[c.id].score); if (!isNaN(v)) { num += v * c.weight; den += c.weight; n++; } });
  const need = c => { const ai = aiBy[c.id], v = parseFloat(vals[c.id].score); return !conflict && ai && !isNaN(v) && Math.abs(v - ai.score) > 1; };
  const save = async status => {
    setBusy(true); setMsg(null);
    const scores = {};
    if (!conflict) crit.forEach(c => { const v = vals[c.id].score; if (v !== "") scores[c.id] = { score: parseFloat(v), note: vals[c.id].note }; });
    try {
      await pub(`/judge/${encodeURIComponent(code)}/teams/${t.id}/score`, { method: "PUT", body: { scores, comment, conflict, status } });
      setDirty(false); onSaved(t.id, conflict ? "recused" : status, { conflict, status, scores, comment });
      setMsg({ ok: true, t: conflict ? "Saved: you are recused from this team." : status === "submitted" ? "Scores submitted. You can still change them and submit again." : "Draft saved." });
    } catch (x) { setMsg({ ok: false, t: x.message }); }
    setBusy(false);
  };
  return (
    <form onSubmit={e => e.preventDefault()}>
      {msg ? <div className={"notice" + (msg.ok ? "" : " err")} role="status">{msg.t}</div> : null}
      <label className="check" style={{ fontWeight: 600, margin: "6px 0 12px" }}><input type="checkbox" checked={conflict} onChange={e => { setConflict(e.target.checked); setDirty(true); }} /> I have a conflict of interest with this team and won’t score it</label>
      <div className="ledger">{crit.map(c => {
        const ai = aiBy[c.id], v = vals[c.id];
        return (
          <section className="crit" key={c.id} style={{ opacity: conflict ? .5 : 1 }}>
            <div className="score-row">
              <div><h3>{c.title} <span className="muted tiny">weight {c.weight} · {c.ai_assisted === false ? "your call" : "AI scored"}</span></h3><p className="muted" style={{ margin: ".3rem 0 0" }}>{c.description}</p></div>
              <div style={{ textAlign: "right" }}><input className="numin" type="number" min="0" max="10" step="0.5" inputMode="decimal" disabled={conflict} value={v.score} onChange={e => edit(c.id, "score", e.target.value)} aria-label={`Your score for ${c.title}`} /><div className="tiny muted">/ 10</div></div>
            </div>
            <input type="range" min="0" max="10" step="0.5" disabled={conflict} value={v.score === "" ? 5 : clamp(+v.score, 0, 10)} onChange={e => edit(c.id, "score", e.target.value)} aria-hidden="true" tabIndex={-1} style={{ marginTop: 10, opacity: v.score === "" ? .35 : 1 }} />
            {c.anchors ? <details><summary>What 2, 5 and 8 look like</summary><dl className="kv">{Object.entries(c.anchors).flatMap(([k, a]) => [<dt key={k + "t"}>{k}</dt>, <dd key={k + "d"}>{a}</dd>])}</dl></details> : null}
            {ai ? <><p className="aiscore" style={{ margin: "8px 0 0" }}><b>AuditCore gave {ai.score}/10.</b> {ai.reasoning}</p>{(ai.evidence || []).slice(0, 3).map((x, i) => <Receipt key={i} x={x} />)}{ai.low_evidence ? <p className="lowev">The AI had no verified code citation for this score.</p> : null}</> : null}
            <Field2 label="Reason or note" hint={need(c) ? `(required: you differ from the AI’s ${ai.score} by more than 1)` : ""}><textarea style={{ minHeight: 60 }} disabled={conflict} value={v.note} onChange={e => edit(c.id, "note", e.target.value)} /></Field2>
          </section>);
      })}</div>
      <div className="field"><label>Overall comment <small>(optional, for the organizers)</small><textarea value={comment} onChange={e => { setComment(e.target.value); setDirty(true); }} /></label></div>
      <div className="sticky-total">
        <span style={{ fontWeight: 600 }}>{conflict ? "You are recused from this team." : `Your weighted total: ${den ? ((num / den) * 10).toFixed(1) : "–"} / 100 · ${n} of ${crit.length} scored`}</span>
        {dirty ? <Pill kind="medium">Unsaved changes</Pill> : null}
        <span className="sp" /><button type="button" className="quiet" id="draft" disabled={busy} onClick={() => save("draft")}>Save draft</button><button type="button" id="submit" disabled={busy} onClick={() => save("submitted")}>Submit scores</button>
      </div>
    </form>
  );
}
const Field2 = ({ label, hint, children }) => <label className="field" style={{ marginTop: 10, display: "block" }}>{label} <small style={{ color: "var(--amber)" }}>{hint}</small>{children}</label>;

function TeamView({ code, S, team, onSaved }) {
  const [D, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setD(null); setErr(null);
    pub(`/judge/${encodeURIComponent(code)}/teams/${team.id}`).then(setD).catch(setErr);
    window.scrollTo(0, 0);
  }, [team.id, code]);
  if (err) return <div className="notice err">{err.message}</div>;
  if (!D) return <p className="muted">Loading team…</p>;
  const t = D.team, ev = D.evaluation, my = D.my;
  const flags = [...(D.cross_flags || []), ...((ev && ev.integrity_flags) || [])];
  return (
    <div>
      <div className="row"><h1><span className="code" style={{ color: "var(--muted)" }}>{t.code}</span> {t.name}</h1>{t.track ? <Pill>{t.track}</Pill> : null}<span className="sp" /><SPill s={my ? (my.conflict ? "recused" : my.status) : "not started"} /></div>
      {t.title ? <p style={{ margin: "4px 0 0", fontSize: "1.1rem" }}>{t.title}</p> : null}
      <dl className="kv"><dt>Repository</dt><dd>{t.repo_url ? <a href={t.repo_url} target="_blank" rel="noopener noreferrer">{t.repo_url.replace("https://github.com/", "")}</a> : "none"}</dd><dt>Members</dt><dd>{(t.members || []).join(", ") || "not listed"}</dd><dt>Links</dt><dd><LinkRow links={t.links} /></dd></dl>
      <h2>What the team says</h2>
      <dl className="kv"><dt>About</dt><dd>{t.summary || <span className="muted">not written yet</span>}</dd><dt>Problem</dt><dd>{t.problem_statement || <span className="muted">not written yet</span>}</dd></dl>
      {ev?.what_it_does && ev.what_it_does !== "See README" ? <dl className="kv"><dt>What the code does</dt><dd>{ev.what_it_does}</dd>{ev.built_vs_reused ? <><dt>Built vs reused</dt><dd>{ev.built_vs_reused}</dd></> : null}</dl> : null}
      {!ev ? <div className="notice">This team has not been analysed yet, so there is no AI evidence or score to start from.</div> : (
        <>
          <h2>Flags to check</h2><Flags flags={flags} />
          <h2>Before the hackathon started</h2><PreEvent pe={ev.pre_event} />
          <h2>Pitch vs code</h2><Alignment al={ev.alignment} />
          {(ev.questions_for_team || []).length ? <><h2>Questions worth asking</h2><List items={ev.questions_for_team} /></> : null}
          {(ev.red_flags || []).length ? <><h3 style={{ marginTop: 18 }}>Red flags from the code review</h3><List items={ev.red_flags} /></> : null}
          <div className="cols"><div>{(ev.strengths || []).length ? <><h3 style={{ marginTop: 18 }}>Strengths</h3><List items={ev.strengths} /></> : null}</div><div>{(ev.weaknesses || []).length ? <><h3 style={{ marginTop: 18 }}>Weaknesses</h3><List items={ev.weaknesses} /></> : null}</div></div>
        </>
      )}
      {D.deck?.slides ? <DeckText slides={D.deck.slides} /> : <p className="muted tiny" style={{ marginTop: 14 }}>No deck was uploaded.</p>}
      <h2>Your scorecard</h2>
      <Scorecard key={t.id} code={code} S={S} D={D} onSaved={onSaved} />
    </div>
  );
}

export default function JudgePage() {
  const { code } = useParams();
  const [sp, setSp] = useSearchParams();
  const [S, setS] = useState(null);
  const [err, setErr] = useState(null);
  const [filter, setFilter] = useState("all");
  useEffect(() => { pub(`/judge/${encodeURIComponent(code)}/session`).then(s => { setS(s); document.title = `Judging · ${s.event.name}`; }).catch(setErr); }, [code]);
  const cur = sp.get("team") ? +sp.get("team") : null;
  const list = useMemo(() => (S ? S.teams.filter(t => filter === "all" || (filter === "todo" ? !["submitted", "recused"].includes(t.my_status) : t.my_status === filter)) : []), [S, filter]);
  const go = useCallback(id => setSp(id ? { team: id } : {}), [setSp]);
  useEffect(() => { // J / K move between teams, as long as nobody is typing
    const h = e => {
      if (!S || e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName)) return;
      const i = S.teams.findIndex(t => t.id === cur);
      if (e.key === "j") go(S.teams[Math.min(i + 1, S.teams.length - 1)]?.id);
      if (e.key === "k" && i > 0) go(S.teams[i - 1].id);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [S, cur, go]);
  const saved = (id, status) => setS(s => ({ ...s, teams: s.teams.map(t => (t.id === id ? { ...t, my_status: status } : t)) }));
  if (err) return <div className="public"><div className="brand" style={{ padding: 0 }}><i />AuditCore</div><h1 style={{ marginTop: 22 }}>This link doesn’t work</h1><p className="lede">{err.message}</p></div>;
  if (!S) return <div className="public"><p className="muted">Loading…</p></div>;
  const done = S.teams.filter(t => ["submitted", "recused"].includes(t.my_status)).length;
  const team = S.teams.find(t => t.id === cur);
  return (
    <div className="sheet">
      <aside aria-label="Teams">
        <div className="brand" style={{ paddingBottom: 6 }}><i />AuditCore</div>
        <div className="row" style={{ margin: "6px 4px 0", flexWrap: "nowrap" }}><Avatar name={S.judge.name} /><div><b>{S.judge.name}</b><div className="muted tiny">{S.event.name}</div></div></div>
        <div className="progress"><i style={{ width: (S.teams.length ? (done / S.teams.length) * 100 : 0) + "%" }} /></div>
        <div className="muted tiny" style={{ margin: "-8px 4px 10px" }}>{done} of {S.teams.length} done</div>
        <select value={filter} onChange={e => setFilter(e.target.value)} aria-label="Filter teams" style={{ marginBottom: 8 }}>
          <option value="all">All teams</option><option value="todo">Still to do</option><option value="draft">Drafts</option><option value="submitted">Submitted</option><option value="recused">Recused</option>
        </select>
        {list.map(t => (
          <a key={t.id} href={`?team=${t.id}`} className={"team-link" + (cur === t.id ? " active" : "")} onClick={e => { e.preventDefault(); go(t.id); }} aria-current={cur === t.id ? "true" : undefined}>
            <b>{t.code}</b> {t.name}{t.title ? <small>{t.title}</small> : null}
            <small><SPill s={t.my_status} />{t.technical != null ? <span>AI {t.technical}</span> : <span>not analysed</span>}{t.high_flags ? <span style={{ color: "var(--flag)" }}>{t.high_flags} high flag{t.high_flags > 1 ? "s" : ""}</span> : null}</small>
          </a>))}
        {!list.length ? <p className="muted tiny" style={{ margin: 8 }}>No teams match this filter.</p> : null}
        <p className="muted tiny" style={{ margin: "14px 6px 0" }}>Tip: press <span className="kbd">J</span> / <span className="kbd">K</span> for the next / previous team.</p>
      </aside>
      <main className="pane" tabIndex={-1} style={{ padding: undefined }}>
        {team ? <TeamView key={team.id} code={code} S={S} team={team} onSaved={saved} /> : (
          <div>
            <h1>{S.event.name}</h1>
            <p className="lede">Welcome, {S.judge.name}. Pick a team on the left. For each one you will see what AuditCore found in its code, what the team says about the project, and a scorecard.</p>
            {!S.teams.length ? <div className="notice warn">No teams are assigned to you yet. Ask the organizers.</div> : null}
            {S.event.theme ? <><h2>Theme</h2><p style={{ whiteSpace: "pre-wrap", maxWidth: "70ch" }}>{S.event.theme}</p></> : null}
            <h2>How scoring works</h2>
            <ul>
              <li>The AI has already scored the technical criteria from the repository. Your box starts at the AI’s score; change it where you disagree.</li>
              <li>If your score differs from the AI’s by more than a point, write a short reason. It is shown to the organizers.</li>
              <li>Innovation, impact and demo are yours alone; the AI doesn’t score them.</li>
              <li>If you know a team personally, tick the conflict box on its page and you won’t score it.</li>
            </ul>
            <p className="muted tiny">Flags are signals to check, not verdicts. Save a draft any time; submit when every criterion is scored.</p>
            {S.teams.length ? <p><button onClick={() => go((S.teams.find(t => !["submitted", "recused"].includes(t.my_status)) || S.teams[0]).id)}>Start with the first team</button></p> : null}
          </div>
        )}
      </main>
    </div>
  );
}
