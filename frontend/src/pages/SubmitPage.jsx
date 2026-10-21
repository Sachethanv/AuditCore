import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { pub } from "../api.js";
import { useLoad } from "../hooks.js";
import { Field, Pill, useToast } from "../components/ui.jsx";
import { ago, fileB64, fmtDate, repoName } from "../utils.js";

const DECK_TYPES = ".pptx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation";

function Brand() { return <div className="brand" style={{ padding: 0 }}><i />AuditCore</div>; }

export default function SubmitPage() {
  const { tok } = useParams();
  const toast = useToast();
  const { data: d, error, setData } = useLoad(() => pub(`/submit/${encodeURIComponent(tok)}`), [tok]);
  const [f, setF] = useState(null);
  const [over, setOver] = useState(false);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (d && !f) setF({ project_title: d.profile.project_title || "", summary: d.profile.summary || "", problem_statement: d.profile.problem_statement || "", track: d.profile.track || "",
      demo_url: d.profile.links?.demo_url || "", video_url: d.profile.links?.video_url || "", docs_url: d.profile.links?.docs_url || "", notes: d.profile.links?.notes || "" });
  }, [d]); // eslint-disable-line
  useEffect(() => { if (d) document.title = `${d.team.code} ${d.team.name} · Your submission`; }, [d]);

  if (error) return <div className="public"><Brand /><h1 style={{ marginTop: 22 }}>This link doesn’t work</h1><p className="lede">{error.message}</p></div>;
  if (!d || !f) return <div className="public"><Brand /><p className="muted" style={{ marginTop: 22 }}>Loading your submission…</p></div>;
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const p = d.profile;
  const save = async e => {
    e.preventDefault(); setBusy(true);
    try {
      const body = { project_title: f.project_title, summary: f.summary, problem_statement: f.problem_statement,
        links: { demo_url: f.demo_url, video_url: f.video_url, docs_url: f.docs_url, notes: f.notes } };
      if (d.event.tracks.length) body.track = f.track;
      setData(await pub(`/submit/${encodeURIComponent(tok)}/profile`, { method: "PUT", body }));
      toast("Saved. Your project details are updated.");
    } catch (x) { toast(x.message, true); }
    setBusy(false);
  };
  const send = async fl => {
    if (!fl) return toast("Choose a .pptx or PDF file first.", true);
    setBusy(true);
    try { setData(await pub(`/submit/${encodeURIComponent(tok)}/deck`, { method: "POST", body: { filename: fl.name, data_b64: await fileB64(fl) } })); setFile(null); toast("Deck received. Thank you."); }
    catch (x) { toast(x.message, true); }
    setBusy(false);
  };
  const steps = [
    ["Describe your project", !!(p.project_title && p.summary)],
    ["Explain the problem it solves", !!p.problem_statement],
    ["Repository linked", !!d.team.repo_url],
    ["Pitch deck uploaded", !!d.deck],
    ["Demo or video link added", !!(p.links?.demo_url || p.links?.video_url)],
  ];
  const doneN = steps.filter(s => s[1]).length;
  return (
    <div className="public">
      <div className="head"><Brand /><Pill>Private to your team</Pill></div>
      <div className="hero">
        <Pill>{d.team.code}</Pill>
        <h1 style={{ marginTop: 10 }}>{d.team.name || d.team.code}</h1>
        <p>{d.event.name}{d.event.ends_at ? ` · submissions close ${fmtDate(d.event.ends_at)}` : ""}</p>
      </div>
      {d.after_deadline ? <div className="notice warn">The submission deadline has passed. You can still make changes, but the organizers can see when they were made.</div> : null}

      <h2>Your checklist <span className="muted" style={{ fontSize: "1rem", fontWeight: 500 }}>{doneN} of {steps.length}</span></h2>
      <div className="progress" style={{ margin: "8px 0 0" }}><i style={{ width: (doneN / steps.length) * 100 + "%" }} /></div>
      <ul className="check-list">{steps.map(([l, ok]) => <li key={l} className={ok ? "ok" : ""}><span className="tick">{ok ? "✓" : ""}</span>{l}</li>)}</ul>

      {d.event.theme ? <details><summary>The event’s theme</summary><p style={{ whiteSpace: "pre-wrap" }}>{d.event.theme}</p></details> : null}

      <form onSubmit={save}>
        <h2>About your project</h2>
        <p className="muted" style={{ margin: ".3rem 0 0" }}>Judges read this next to your code. Say what the project really does today, not what you hope to build.</p>
        <Field label="Project title"><input maxLength={200} value={f.project_title} onChange={set("project_title")} autoComplete="off" /></Field>
        {d.event.tracks.length ? <Field label="Track"><select value={f.track} onChange={set("track")}><option value="">Choose a track</option>{d.event.tracks.map(t => <option key={t}>{t}</option>)}</select></Field> : null}
        <Field label="What is it about?"><textarea maxLength={8000} value={f.summary} onChange={set("summary")} placeholder="Two or three sentences: what it does and how a person uses it" /></Field>
        <Field label="What problem does it solve?"><textarea maxLength={8000} value={f.problem_statement} onChange={set("problem_statement")} placeholder="Who has the problem, and what happens to them today without your project" /></Field>
        <h2>Links for judges</h2>
        <div className="grid2" style={{ gap: "0 24px" }}>
          <Field label="Live demo"><input type="url" placeholder="https://" value={f.demo_url} onChange={set("demo_url")} /></Field>
          <Field label="Demo video"><input type="url" placeholder="https://" value={f.video_url} onChange={set("video_url")} /></Field>
          <Field label="Documentation"><input type="url" placeholder="https://" value={f.docs_url} onChange={set("docs_url")} /></Field>
        </div>
        <Field label="Notes for judges" small="(for example a demo login)"><textarea style={{ minHeight: 64 }} value={f.notes} onChange={set("notes")} /></Field>
        <p style={{ marginTop: 16 }}><button disabled={busy}>Save project details</button></p>
      </form>

      <h2>Pitch deck</h2>
      {d.deck ? <p style={{ margin: ".4rem 0" }}><Pill kind="ok">Received</Pill> <b>{d.deck.filename}</b> · {d.deck.slides} {d.deck.kind === "pdf" ? "page" : "slide"}{d.deck.slides === 1 ? "" : "s"} · {ago(d.deck.uploaded_at)}</p> : <p className="muted" style={{ margin: ".3rem 0" }}>No deck uploaded yet.</p>}
      <div className={"dropzone" + (over ? " over" : "")} onDragOver={e => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={e => { e.preventDefault(); setOver(false); send(e.dataTransfer.files[0]); }}>
        <p style={{ margin: "0 0 10px" }}>Drop a <b>.pptx</b> or <b>PDF</b> here, or choose a file. Up to 20 MB. The text on your slides is read, so keep key points as text rather than only in images.</p>
        <div className="row" style={{ justifyContent: "center" }}>
          <input type="file" id="deck" accept={DECK_TYPES} aria-label="Choose your deck" style={{ width: "auto" }} onChange={e => setFile(e.target.files[0] || null)} />
          <button type="button" id="up" disabled={busy} onClick={() => send(file)}>{d.deck ? "Replace deck" : "Upload deck"}</button>
        </div>
      </div>

      <h2>Your repository</h2>
      <p style={{ margin: ".3rem 0" }}>{d.team.repo_url ? <><a href={d.team.repo_url} target="_blank" rel="noopener noreferrer">{repoName(d.team.repo_url)}</a> <span className="muted">· to change it, ask the organizers</span></> : <><Pill kind="medium">Not set</Pill> <span className="muted">Ask the organizers to add your GitHub link.</span></>}</p>
      {d.team.members.length ? <p className="muted tiny">Team members listed: {d.team.members.join(", ")}</p> : null}

      <h2>Feedback for your team</h2>
      {d.feedback.length ? d.feedback.map((x, i) => <div className="fb" key={i}><div className="row"><b>{x.author}</b><span className="muted tiny">{ago(x.created_at)}</span></div><pre>{x.body}</pre></div>) : <p className="muted">Nothing shared yet. Feedback appears here once the organizers release it.</p>}
      <p className="muted tiny" style={{ marginTop: 34 }}>This page is private to your team. Anyone who has this link can edit your submission, so don’t share it outside your team.</p>
    </div>
  );
}
