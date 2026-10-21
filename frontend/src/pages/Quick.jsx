import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { useLoad } from "../hooks.js";
import { EvaluationReport } from "../components/Report.jsx";
import { Empty, ErrorNote, Field, FlagPills, PageSkeleton, ScoreCell, StatusPill, useToast } from "../components/ui.jsx";
import { ago, fileB64, fromLocalInput, repoName } from "../utils.js";

const DECK_TYPES = ".pptx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation";
const busyRow = r => r.status === "queued" || r.status === "running";

export function QuickCheck() {
  const nav = useNavigate();
  const toast = useToast();
  const { data: rows, error } = useLoad(() => api("/evaluations"), [], { shouldPoll: d => !!d?.some(busyRow), interval: 2500 });
  const [f, setF] = useState({ repo_url: "", s: "", e: "", title: "", summary: "", problem: "" });
  const [deck, setDeck] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = { repo_url: f.repo_url, hackathon_start: fromLocalInput(f.s), hackathon_end: fromLocalInput(f.e), title: f.title, summary: f.summary, problem_statement: f.problem };
      if (deck) body.deck = { filename: deck.name, data_b64: await fileB64(deck) };
      const r = await api("/evaluations", { method: "POST", body });
      nav(`/quick/${r.id}`);
    } catch (x) { toast(x.message, true); setBusy(false); }
  };
  return (
    <div>
      <h1>Check a repository</h1>
      <p className="lede">Paste a public GitHub link. You get commit-history flags, leaked-secret and security findings, and a rubric review that cites the code it is based on.</p>
      <form onSubmit={submit} className="card" style={{ marginTop: 22 }}>
        <div className="inline">
          <label className="grow">GitHub repository<input required inputMode="url" placeholder="https://github.com/owner/repo" value={f.repo_url} onChange={set("repo_url")} /></label>
          <button disabled={busy}>{busy ? "Starting…" : "Evaluate"}</button>
        </div>
        <details><summary>Hackathon dates (turns on the “started before the event” check)</summary>
          <div className="inline"><label>Starts<input type="datetime-local" value={f.s} onChange={set("s")} /></label><label>Ends<input type="datetime-local" value={f.e} onChange={set("e")} /></label></div>
        </details>
        <details><summary>About the project (optional: checks the pitch against the code)</summary>
          <Field label="Project title"><input maxLength={200} value={f.title} onChange={set("title")} /></Field>
          <Field label="What is it about?"><textarea value={f.summary} onChange={set("summary")} placeholder="Two or three sentences on what the project does" /></Field>
          <Field label="What problem does it solve?"><textarea value={f.problem} onChange={set("problem")} placeholder="Who has the problem, and why it matters" /></Field>
          <Field label="Pitch deck" small="(.pptx or PDF, up to 20 MB)"><input type="file" accept={DECK_TYPES} onChange={e => setDeck(e.target.files[0] || null)} /></Field>
        </details>
      </form>
      <h2>Recent checks</h2>
      {error ? <ErrorNote error={error} /> : !rows ? <PageSkeleton /> : !rows.length ? <Empty title="No checks yet">Paste a repository link above to run your first one.</Empty> : (
        <div className="tablewrap"><table><thead><tr><th>Repository</th><th>Technical score</th><th>Flags</th><th>When</th></tr></thead>
          <tbody>{rows.map(e => (
            <tr key={e.id} className="link" onClick={() => nav(`/quick/${e.id}`)}>
              <td><Link to={`/quick/${e.id}`}>{repoName(e.repo_url)}</Link></td>
              <td>{e.status === "done" ? <ScoreCell value={e.overall_score} /> : <StatusPill e={e} />}</td>
              <td>{e.status === "done" ? <FlagPills high={e.high_flags} /> : null}</td>
              <td className="muted">{ago(e.created_at)}</td>
            </tr>))}</tbody></table></div>
      )}
    </div>
  );
}

export function EvalPage() {
  const { id } = useParams();
  const { data: e, error } = useLoad(() => api("/evaluations/" + id), [id], { shouldPoll: d => !!d && busyRow(d), interval: 2000 });
  const crumb = <div className="crumb"><Link to="/quick">Quick check</Link></div>;
  if (error) return <div>{crumb}<ErrorNote error={error} /></div>;
  if (!e) return <div>{crumb}<PageSkeleton /></div>;
  if (busyRow(e)) return (
    <div>{crumb}<h1>{repoName(e.repo_url)}</h1>
      <div className="notice" role="status">{e.status === "queued" ? "Waiting for a free worker…" : "Cloning the repository and analysing it. Large repositories and the Gemini review take up to a minute or two."}</div>
    </div>
  );
  if (e.status === "failed") return <div>{crumb}<h1>{repoName(e.repo_url)}</h1><div className="notice err">{e.error}</div><p><Link className="btn" to="/quick">Try another repository</Link></p></div>;
  return <div>{crumb}<EvaluationReport r={e.result} /></div>;
}
