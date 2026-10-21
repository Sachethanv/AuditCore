import { useState } from "react";
import { Link, NavLink, useParams } from "react-router-dom";
import { api, downloadBlob } from "../api.js";
import { useLoad } from "../hooks.js";
import { Alignment, DeckText, EvaluationReport, Flags, LinkRow, PreEvent } from "../components/Report.jsx";
import { CopyBox, Empty, ErrorNote, Field, PageSkeleton, Pill, Ring, useToast } from "../components/ui.jsx";
import { ago, fileB64, repoName, store } from "../utils.js";

const DECK_TYPES = ".pptx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation";
const TABS = [["overview", "Overview"], ["code", "Code review"], ["pitch", "Pitch vs code"], ["submission", "Submission"], ["feedback", "Feedback"], ["judges", "Scorecards"]];

function Submission({ t, reload }) {
  const toast = useToast();
  const [f, setF] = useState({ project_title: t.project_title || "", summary: t.summary || "", problem_statement: t.problem_statement || "",
    demo_url: t.links?.demo_url || "", video_url: t.links?.video_url || "", docs_url: t.links?.docs_url || "", notes: t.links?.notes || "" });
  const [file, setFile] = useState(null);
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const save = async e => {
    e.preventDefault();
    try {
      await api("/teams/" + t.id, { method: "PATCH", body: { project_title: f.project_title, summary: f.summary, problem_statement: f.problem_statement,
        links: { demo_url: f.demo_url, video_url: f.video_url, docs_url: f.docs_url, notes: f.notes } } });
      toast("Submission saved"); reload(true);
    } catch (x) { toast(x.message, true); }
  };
  const upload = async () => {
    if (!file) return toast("Choose a .pptx or PDF file first", true);
    try { await api(`/teams/${t.id}/deck`, { method: "POST", body: { filename: file.name, data_b64: await fileB64(file) } }); toast("Deck uploaded"); setFile(null); reload(true); }
    catch (x) { toast(x.message, true); }
  };
  const link = t.submit_path ? location.origin + t.submit_path : "";
  return (
    <div>
      <form onSubmit={save}>
        <h2>About the project</h2>
        <Field label="Project title"><input maxLength={200} value={f.project_title} onChange={set("project_title")} /></Field>
        <Field label="What is it about?"><textarea value={f.summary} onChange={set("summary")} /></Field>
        <Field label="What problem does it solve?"><textarea value={f.problem_statement} onChange={set("problem_statement")} /></Field>
        <h2>Links</h2>
        <div className="grid2" style={{ gap: "0 24px" }}>
          <Field label="Live demo"><input type="url" placeholder="https://" value={f.demo_url} onChange={set("demo_url")} /></Field>
          <Field label="Demo video"><input type="url" placeholder="https://" value={f.video_url} onChange={set("video_url")} /></Field>
          <Field label="Documentation"><input type="url" placeholder="https://" value={f.docs_url} onChange={set("docs_url")} /></Field>
        </div>
        <Field label="Notes for judges" small="(e.g. demo logins)"><textarea style={{ minHeight: 64 }} value={f.notes} onChange={set("notes")} /></Field>
        <p className="row" style={{ marginTop: 16 }}><button>Save submission</button><span className="muted tiny">The team can edit all of this itself on its private link.</span></p>
      </form>
      <h2>Pitch deck</h2>
      {t.deck ? (
        <>
          <dl className="kv"><dt>File</dt><dd>{t.deck.filename} · {t.deck.slides} slides · {Math.round(t.deck.size / 1024)} KB · uploaded {ago(t.deck.uploaded_at)}</dd></dl>
          <DeckText slides={t.deck_slides} />
          <div className="row" style={{ marginTop: 10 }}>
            <button className="small quiet" onClick={() => downloadBlob(`/teams/${t.id}/deck/file`, t.deck.filename).catch(x => toast(x.message, true))}>Download original</button>
            <button className="small danger" onClick={async () => { if (confirm("Remove this deck?")) { await api(`/teams/${t.id}/deck`, { method: "DELETE" }); reload(true); } }}>Remove deck</button>
          </div>
        </>
      ) : <p className="muted">No deck yet. The team can upload one on its private link, or you can upload it here.</p>}
      <div className="row" style={{ marginTop: 12 }}>
        <input type="file" accept={DECK_TYPES} style={{ width: "auto" }} aria-label="Upload deck" onChange={e => setFile(e.target.files[0] || null)} />
        <button className="small" onClick={upload}>{t.deck ? "Replace deck" : "Upload deck"}</button>
      </div>
      <h3 style={{ marginTop: 26 }}>Team’s private link</h3>
      <CopyBox value={link} label="Team link" />
      <p className="muted tiny">The team opens this to describe its project, upload its deck and read feedback you share. Anyone with the link can edit the submission.</p>
    </div>
  );
}

function Feedback({ t, reload }) {
  const toast = useToast();
  const [body, setBody] = useState("");
  const [author, setAuthor] = useState(store.get("ac_author") || "");
  const [share, setShare] = useState(false);
  const add = async e => {
    e.preventDefault();
    store.set("ac_author", author);
    try { await api(`/teams/${t.id}/feedback`, { method: "POST", body: { author, body, visible_to_team: share } }); setBody(""); setShare(false); reload(true); }
    catch (x) { toast(x.message, true); }
  };
  return (
    <div>
      <h2>Feedback</h2>
      {t.feedback.length ? t.feedback.map(f => (
        <div className="fb" key={f.id}>
          <div className="row"><b>{f.author}</b><Pill kind={f.kind === "ai" ? "run" : ""}>{f.kind === "ai" ? "AI draft" : "Judge"}</Pill><span className="muted tiny">{ago(f.created_at)}</span><span className="sp" />
            <label className="check"><input type="checkbox" defaultChecked={f.visible_to_team} onChange={async e => { try { await api("/feedback/" + f.id, { method: "PATCH", body: { visible_to_team: e.target.checked } }); toast(e.target.checked ? "The team will see this on its link" : "Hidden from the team"); } catch (x) { toast(x.message, true); } }} /> Share with team</label>
            <button className="small danger" onClick={async () => { if (confirm("Delete this feedback?")) { await api("/feedback/" + f.id, { method: "DELETE" }); reload(true); } }}>Delete</button></div>
          <pre>{f.body}</pre>
        </div>)) : <p className="muted">No feedback yet. The AI draft appears here after the first evaluation.</p>}
      <form onSubmit={add} style={{ marginTop: 18 }}>
        <Field label="Add your own feedback"><textarea required style={{ minHeight: 100 }} value={body} onChange={e => setBody(e.target.value)} /></Field>
        <div className="row" style={{ marginTop: 10 }}>
          <label>Your name <input required value={author} onChange={e => setAuthor(e.target.value)} style={{ width: 190, marginLeft: 6 }} /></label>
          <label className="check"><input type="checkbox" checked={share} onChange={e => setShare(e.target.checked)} /> Share with team</label>
          <button>Save feedback</button>
        </div>
      </form>
    </div>
  );
}

function Scorecards({ t }) {
  const rubric = t.event.rubric.criteria;
  if (!t.judge_scores.length) return <Empty title="No scorecards yet">Judges’ scores appear here once they start. <Link to={`/events/${t.event.id}/judges`}>Invite judges</Link>.</Empty>;
  return (
    <div className="tablewrap"><table>
      <thead><tr><th>Judge</th><th>Status</th>{rubric.map(c => <th className="num" key={c.id} title={c.title}>{c.title.split(/[ &]/)[0]}</th>)}</tr></thead>
      <tbody>{t.judge_scores.map((j, i) => (
        <tr key={i}>
          <td>{j.judge}{j.comment ? <div className="tiny muted" style={{ maxWidth: "30ch" }}>{j.comment}</div> : null}</td>
          <td>{j.conflict ? <Pill>recused</Pill> : <Pill kind={j.status === "submitted" ? "ok" : ""}>{j.status}</Pill>}</td>
          {rubric.map(c => { const s = (j.scores || {})[c.id]; return <td className="num" key={c.id}>{s ? s.score : "–"}{s?.note ? <div className="tiny muted" style={{ maxWidth: "20ch", textAlign: "left" }} title={s.note}>{s.note.length > 60 ? s.note.slice(0, 58) + "…" : s.note}</div> : null}</td>; })}
        </tr>))}</tbody></table></div>
  );
}

export default function TeamPage() {
  const { id, tab = "overview" } = useParams();
  const toast = useToast();
  const { data: t, error, reload } = useLoad(() => api("/teams/" + id), [id], { shouldPoll: d => !!d?.running, interval: 2500 });
  const { data: lb } = useLoad(() => (t ? api(`/events/${t.event.id}/leaderboard`) : Promise.resolve(null)), [t?.event?.id, t?.evaluation?.id, t?.running]);
  if (error && !t) return <ErrorNote error={error} />;
  if (!t) return <PageSkeleton />;
  const ev = t.evaluation, res = ev?.result, row = lb?.rows?.find(r => r.team_id === t.id);
  const run = t.running;
  const start = async () => { try { await api(`/teams/${id}/evaluate`, { method: "POST" }); reload(true); } catch (x) { toast(x.message, true); } };
  return (
    <div>
      <div className="crumb"><Link to="/events">Hackathons</Link> / <Link to={`/events/${t.event.id}/leaderboard`}>{t.event.name}</Link></div>
      <div className="row">
        <h1><span className="code" style={{ color: "var(--muted)" }}>{t.code}</span> {t.name}</h1>
        {t.track ? <Pill>{t.track}</Pill> : null}
        <span className="sp" />
        {run ? <Pill kind="run live">Analysing</Pill> : <button onClick={start} disabled={!t.repo_url}>{ev ? "Evaluate again" : "Evaluate"}</button>}
      </div>
      <p style={{ margin: "6px 0 0" }} className="muted">{t.project_title ? <b style={{ color: "var(--ink)" }}>{t.project_title} · </b> : null}
        {t.repo_url ? <a href={t.repo_url} target="_blank" rel="noopener noreferrer">{repoName(t.repo_url)}</a> : <><Pill kind="medium">No repository</Pill> Add one on the Teams tab.</>}
        {(t.members || []).length ? ` · ${t.members.map(m => m.name).join(", ")}` : ""}</p>
      {t.cross_flags.map((f, i) => <div className={"flag " + f.severity} key={i}><div><span className="sev">{f.severity}</span>{f.message}</div></div>)}
      {t.history[0]?.status === "failed" ? <div className="notice err">Last evaluation failed: {t.history[0].error}</div> : null}
      {t.deck_newer_than_eval ? <div className="notice warn">A new deck was uploaded after the last evaluation. Evaluate again to include it in the pitch-vs-code check.</div> : null}
      <nav className="tabs" aria-label="Team sections">{TABS.map(([k, l]) => <NavLink key={k} to={`/teams/${id}/${k}`} replace className={tab === k ? "active" : ""}>{l}{k === "feedback" && t.feedback.length ? <span className="count">{t.feedback.length}</span> : null}{k === "judges" && t.judge_scores.length ? <span className="count">{t.judge_scores.length}</span> : null}</NavLink>)}</nav>

      {tab === "overview" && (
        <div>
          <div className="stats" style={{ alignItems: "stretch" }}>
            <div className="stat"><Ring value={row?.final ?? null} size={92} stroke={9} label={row?.rank ? `rank ${row.rank}` : "overall"} /><div className="tiny muted" style={{ marginTop: 6 }}>{row ? (row.complete ? "Final score (judged)" : "Partial: judges still to score") : "Not scored yet"}</div></div>
            <div className="stat"><Ring value={ev ? ev.overall_score : null} size={92} stroke={9} label="technical" /><div className="tiny muted" style={{ marginTop: 6 }}>AI review of the code</div></div>
            <div className="stat"><Ring value={res?.alignment?.alignment_score ?? null} size={92} stroke={9} label="pitch" /><div className="tiny muted" style={{ marginTop: 6 }}>{res?.alignment ? `${res.alignment.claims_unsupported ?? 0} claims not in code` : "No pitch compared yet"}</div></div>
            <div className="stat"><b>{row ? row.high_flags : "–"}</b><span>high flags</span><div className="tiny muted" style={{ marginTop: 6 }}>{row ? `${row.medium_flags} medium` : ""}</div></div>
          </div>
          {res ? <EvaluationReport r={res} parts={["summary", "integrity"]} showTitle={false} noRing /> : run ? <div className="notice" role="status">Cloning the repository and analysing it…</div> : <Empty title="Not evaluated yet">Press Evaluate to review the repository.</Empty>}
        </div>
      )}
      {tab === "code" && (res ? <EvaluationReport r={res} parts={["rubric", "details"]} showTitle={false} /> : <Empty title="No code review yet">Evaluate this team first.</Empty>)}
      {tab === "pitch" && (
        <div>
          <h2 style={{ marginTop: 20 }}>What the team says</h2>
          <dl className="kv"><dt>About</dt><dd>{t.summary || <span className="muted">not written yet</span>}</dd><dt>Problem</dt><dd>{t.problem_statement || <span className="muted">not written yet</span>}</dd><dt>Links</dt><dd><LinkRow links={t.links} /></dd></dl>
          <h2>Pitch vs code</h2>
          {res ? <Alignment al={res.alignment} /> : <p className="muted">Evaluate the team to compare its pitch with its code.</p>}
          {res ? <><h2>Before the hackathon started</h2><PreEvent pe={res.pre_event} /></> : null}
        </div>
      )}
      {tab === "submission" && <Submission t={t} reload={reload} key={t.id + ":" + (t.deck?.uploaded_at || "")} />}
      {tab === "feedback" && <Feedback t={t} reload={reload} />}
      {tab === "judges" && <><h2 style={{ marginTop: 20 }}>Judge scorecards</h2><Scorecards t={t} /></>}
    </div>
  );
}
