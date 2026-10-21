import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api.js";
import { CopyBox, Empty, Field, Modal, Pill, ScoreCell, StatusPill, useToast } from "../../components/ui.jsx";
import { membersStr, parseMembers, repoName } from "../../utils.js";

const SAMPLE = `team_id,team_name,repo_url,members,project_title,description,problem,track
T-001,Team Alpha,https://github.com/owner/repo,Asha Rao (@asharao); Ravi K (@rk-dev),SafeRoute,"Maps that route women home along well-lit, busy streets","Commuters feel unsafe walking at night",Health
T-002,Team Beta,https://github.com/owner/other-repo,Mia Chen (@miac),,,,`;

function AddTeam({ ev, onDone }) {
  const toast = useToast();
  const [f, setF] = useState({ code: "", name: "", repo_url: "", members: "", track: "" });
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault();
    try {
      await api(`/events/${ev.id}/teams`, { method: "POST", body: { code: f.code, name: f.name, repo_url: f.repo_url, members: parseMembers(f.members), track: f.track } });
      toast("Team added"); onDone();
    } catch (x) { toast(x.message, true); }
  };
  return (
    <form onSubmit={submit}>
      <div className="inline"><label>Team ID<input value={f.code} onChange={set("code")} placeholder="auto" /></label><label className="grow">Team name<input value={f.name} onChange={set("name")} required /></label></div>
      <Field label="Repository"><input value={f.repo_url} onChange={set("repo_url")} placeholder="https://github.com/owner/repo" /></Field>
      <Field label="Members" help="Name (@githubhandle); Name (@handle). Handles let AuditCore check that the people listed actually committed."><input value={f.members} onChange={set("members")} placeholder="Asha Rao (@asharao); Ravi K (@rk-dev)" /></Field>
      {ev.tracks?.length ? <Field label="Track"><select value={f.track} onChange={set("track")}><option value="">No track</option>{ev.tracks.map(t => <option key={t}>{t}</option>)}</select></Field> : null}
      <div className="row" style={{ marginTop: 18 }}><button>Add team</button><button type="button" className="quiet" onClick={onDone}>Cancel</button></div>
    </form>
  );
}

function ImportCsv({ ev, onDone }) {
  const toast = useToast();
  const [csv, setCsv] = useState(SAMPLE);
  const [res, setRes] = useState(null);
  const run = async () => {
    try {
      const r = await api(`/events/${ev.id}/teams/import`, { method: "POST", body: { csv } });
      setRes(r);
      if (!r.problems.length) { toast(`Imported ${r.created} new and ${r.updated} updated teams`); onDone(); }
    } catch (x) { toast(x.message, true); }
  };
  return (
    <div>
      <p className="muted">One row per team. Rows with an existing team ID update that team. Every team gets a private link where it can describe its project and upload its pitch deck.</p>
      <p className="tiny muted">Columns: <span className="code">team_id, team_name, repo_url, members</span>; optional: <span className="code">project_title, description, problem, track, demo_url, video_url, docs_url</span>.</p>
      <textarea style={{ minHeight: 190, fontFamily: "var(--mono)", fontSize: ".8rem" }} spellCheck={false} value={csv} onChange={e => setCsv(e.target.value)} aria-label="Teams CSV" />
      <div className="row" style={{ marginTop: 10 }}>
        <input type="file" accept=".csv,text/csv" aria-label="Upload a CSV file" style={{ width: "auto" }} onChange={async e => { const f = e.target.files[0]; if (f) setCsv(await f.text()); }} />
        <span className="sp" /><button type="button" className="quiet" onClick={onDone}>Close</button><button type="button" onClick={run}>Import teams</button>
      </div>
      {res?.problems?.length ? <div className="notice err">Imported {res.created} new and {res.updated} updated. Fix these on the Teams tab: {res.problems.map(p => `${p.team_id}: ${p.problem}`).join("; ")}</div> : null}
    </div>
  );
}

function EditTeam({ ev, t, onDone }) {
  const toast = useToast();
  const [f, setF] = useState({ name: t.name, repo_url: t.repo_url || "", members: membersStr(t.members), project_title: t.project_title || "", track: t.track || "" });
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault();
    try { await api("/teams/" + t.id, { method: "PATCH", body: { name: f.name, repo_url: f.repo_url, members: parseMembers(f.members), project_title: f.project_title, track: f.track } }); onDone(true); }
    catch (x) { toast(x.message, true); }
  };
  return (
    <form onSubmit={submit}>
      <Field label="Team name"><input value={f.name} onChange={set("name")} /></Field>
      <Field label="Repository"><input value={f.repo_url} onChange={set("repo_url")} placeholder="https://github.com/owner/repo" /></Field>
      <Field label="Members"><input value={f.members} onChange={set("members")} /></Field>
      <Field label="Project title"><input value={f.project_title} onChange={set("project_title")} /></Field>
      {ev.tracks?.length ? <Field label="Track"><select value={f.track} onChange={set("track")}><option value="">No track</option>{ev.tracks.map(x => <option key={x}>{x}</option>)}</select></Field> : null}
      <div className="row" style={{ marginTop: 18 }}><button>Save</button><button type="button" className="quiet" onClick={() => onDone()}>Cancel</button></div>
    </form>
  );
}

export default function Teams({ ev, teams, reload }) {
  const toast = useToast();
  const [modal, setModal] = useState(null); // { type: add | import | edit | link, t?: team }
  const [q, setQ] = useState("");
  const [track, setTrack] = useState("");
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return teams.filter(t => (!s || `${t.code} ${t.name} ${t.project_title || ""} ${t.repo_url || ""}`.toLowerCase().includes(s)) && (!track || t.track === track));
  }, [teams, q, track]);
  const close = ok => { setModal(null); if (ok !== false) reload(true); };
  const evaluate = async t => { try { await api(`/teams/${t.id}/evaluate`, { method: "POST" }); toast("Evaluation started"); reload(true); } catch (x) { toast(x.message, true); } };
  const remove = async t => { if (confirm(`Remove ${t.code} ${t.name} and its evaluations?`)) { try { await api("/teams/" + t.id, { method: "DELETE" }); reload(true); } catch (x) { toast(x.message, true); } } };
  return (
    <div>
      <div className="toolbar">
        <input type="search" placeholder="Search teams…" value={q} onChange={e => setQ(e.target.value)} aria-label="Search teams" />
        {ev.tracks?.length ? <select value={track} onChange={e => setTrack(e.target.value)} aria-label="Filter by track"><option value="">All tracks</option>{ev.tracks.map(t => <option key={t}>{t}</option>)}</select> : null}
        <span className="sp" />
        <button className="quiet" onClick={() => setModal({ type: "import" })}>Import CSV</button>
        <button onClick={() => setModal({ type: "add" })}>Add team</button>
      </div>
      {!teams.length ? <Empty title="No teams yet">Import a CSV with team IDs and repository links, or add one team.</Empty> : (
        <div className="tablewrap"><table>
          <thead><tr><th>Team ID</th><th>Name and project</th><th>Repository</th><th>Members</th><th>Pitch</th><th>Status</th><th /></tr></thead>
          <tbody>{rows.map(t => (
            <tr key={t.id}>
              <td className="code"><b>{t.code}</b></td>
              <td><Link to={`/teams/${t.id}`}>{t.name || "(no name)"}</Link>{t.track ? <> <Pill>{t.track}</Pill></> : null}{t.project_title ? <div className="tiny">{t.project_title}</div> : null}{t.summary ? null : <div className="tiny muted">no description yet</div>}</td>
              <td>{t.repo_url ? <a href={t.repo_url} target="_blank" rel="noopener noreferrer">{repoName(t.repo_url)}</a> : <Pill kind="medium">Missing</Pill>}{t.cross_flags.map((f, i) => <div key={i} className="tiny" style={{ color: "var(--flag)" }}>{f.message}</div>)}</td>
              <td className="tiny">{(t.members || []).map((m, i) => <div key={i}>{m.name}{m.github ? <span className="muted"> @{m.github}</span> : null}</div>)}</td>
              <td className="tiny">{t.deck ? <><Pill kind="ok">{t.deck.slides} slides</Pill><div className="muted">{t.deck.filename}</div></> : <Pill>No deck</Pill>}</td>
              <td>{t.latest ? (t.latest.status === "done" ? <ScoreCell value={t.latest.overall_score} /> : <StatusPill e={t.latest} />) : <span className="muted tiny">–</span>}</td>
              <td><div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                <button className="small quiet" disabled={!t.repo_url} onClick={() => evaluate(t)}>Evaluate</button>
                <button className="small quiet" onClick={() => setModal({ type: "link", t })} title="The private page where this team uploads its deck and describes its project">Team link</button>
                <button className="small quiet" onClick={() => setModal({ type: "edit", t })}>Edit</button>
                <button className="small danger" onClick={() => remove(t)}>Remove</button></div></td>
            </tr>))}</tbody></table></div>
      )}
      <Modal open={modal?.type === "add"} onClose={() => close(false)} title="Add a team"><AddTeam ev={ev} onDone={() => close()} /></Modal>
      <Modal open={modal?.type === "import"} onClose={() => close(false)} title="Import teams from CSV" wide><ImportCsv ev={ev} onDone={() => close()} /></Modal>
      <Modal open={modal?.type === "edit"} onClose={() => close(false)} title={modal?.type === "edit" ? `Edit ${modal.t.code}` : ""}>{modal?.type === "edit" ? <EditTeam ev={ev} t={modal.t} onDone={ok => close(ok)} /> : null}</Modal>
      <Modal open={modal?.type === "link"} onClose={() => close(false)} title={modal?.type === "link" ? `${modal.t.code} submission link` : ""}>
        {modal?.type === "link" ? <><p className="muted">Send this to the team. They use it to describe the project, add demo and video links, upload their deck and read feedback you share. Anyone with the link can edit the submission.</p>
          <CopyBox value={location.origin + modal.t.submit_path} label="Team link" />
          <p style={{ marginTop: 16 }}><button className="quiet" onClick={() => close(false)}>Done</button></p></> : null}
      </Modal>
    </div>
  );
}
