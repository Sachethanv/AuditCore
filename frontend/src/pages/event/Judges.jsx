import { useState } from "react";
import { api } from "../../api.js";
import { Avatar, CopyBox, Empty, Field, Modal, Pill, useToast } from "../../components/ui.jsx";

const MODES = [
  ["all", "Every judge scores every team", "Simple and fair for small events."],
  ["track", "Judges are assigned by track", "Each judge sees only the teams in their tracks."],
  ["custom", "I choose teams for each judge", "Pick exactly which teams each judge scores."],
];

function Assign({ ev, teams, judge, onSave, onClose }) {
  const [tracks, setTracks] = useState(judge?.tracks || []);
  const [ids, setIds] = useState(judge?.team_ids || []);
  const [name, setName] = useState(judge?.name || "");
  const mode = ev.assignment_mode;
  const flip = (arr, set, v) => set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]);
  return (
    <form onSubmit={e => { e.preventDefault(); onSave({ name, tracks, team_ids: ids }); }}>
      <Field label="Judge name"><input required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Dr. Meera Rao" autoFocus /></Field>
      {mode === "track" ? (
        <Field label="Tracks this judge scores">
          {ev.tracks?.length ? <div className="row" style={{ marginTop: 6 }}>{ev.tracks.map(t => <label key={t} className="check"><input type="checkbox" checked={tracks.includes(t)} onChange={() => flip(tracks, setTracks, t)} />{t}</label>)}</div>
            : <p className="muted tiny">This event has no tracks yet. Add some in Settings.</p>}
        </Field>
      ) : null}
      {mode === "custom" ? (
        <Field label="Teams this judge scores" small={`(${ids.length} selected)`}>
          <div style={{ maxHeight: 240, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8, padding: 8, marginTop: 6 }}>
            {teams.map(t => <label key={t.id} className="check" style={{ display: "flex", padding: "4px 0" }}><input type="checkbox" checked={ids.includes(t.id)} onChange={() => flip(ids, setIds, t.id)} /><b>{t.code}</b> {t.name}</label>)}
          </div>
          <div className="row" style={{ marginTop: 6 }}><button type="button" className="ghost small" onClick={() => setIds(teams.map(t => t.id))}>Select all</button><button type="button" className="ghost small" onClick={() => setIds([])}>Clear</button></div>
        </Field>
      ) : null}
      <div className="row" style={{ marginTop: 18 }}><button>{judge ? "Save" : "Add judge"}</button><button type="button" className="quiet" onClick={onClose}>Cancel</button></div>
    </form>
  );
}

export default function Judges({ ev, teams, judges, reload }) {
  const toast = useToast();
  const [modal, setModal] = useState(null); // { type: "add" } or { type: "edit", j }
  const setMode = async mode => {
    try { await api(`/events/${ev.id}`, { method: "PATCH", body: { name: ev.name, hackathon_start: ev.hackathon_start, hackathon_end: ev.hackathon_end, theme: ev.theme, assignment_mode: mode } }); reload(true); }
    catch (x) { toast(x.message, true); }
  };
  const save = async body => {
    try {
      if (modal.type === "add") await api(`/events/${ev.id}/judges`, { method: "POST", body });
      else await api(`/judges/${modal.j.id}`, { method: "PATCH", body });
      setModal(null); reload(true);
    } catch (x) { toast(x.message, true); }
  };
  const invite = (j, url) => `mailto:?subject=${encodeURIComponent(`Judging ${ev.name}`)}&body=${encodeURIComponent(`Hi ${j.name},\n\nHere is your private scoring link for ${ev.name}:\n${url}\n\nPlease don't share it. It opens the teams assigned to you, with the evidence and a scorecard.\n`)}`;
  return (
    <div>
      <p className="lede" style={{ marginTop: 18 }}>Each judge gets a private link. They see every assigned team’s evidence (flags, pre-event commits, pitch match, AI scores), then score the criteria. Where a judge’s score differs from the AI’s by more than a point they must say why. A judge can step out of any team they have a conflict with.</p>
      <h2 style={{ marginTop: 22 }}>Who judges what</h2>
      <div className="grid3" style={{ marginTop: 12 }}>
        {MODES.map(([k, t, d]) => (
          <label key={k} className="card" style={{ cursor: "pointer", borderColor: ev.assignment_mode === k ? "var(--teal)" : undefined, boxShadow: ev.assignment_mode === k ? "0 0 0 2px var(--teal-soft)" : undefined }}>
            <span className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}><input type="radio" name="mode" checked={ev.assignment_mode === k} onChange={() => setMode(k)} style={{ marginTop: 4 }} /><span><b>{t}</b><br /><span className="muted tiny" style={{ fontWeight: 400 }}>{d}</span></span></span>
          </label>))}
      </div>
      <div className="row" style={{ marginTop: 24 }}><h2 style={{ margin: 0 }}>Judges</h2><span className="sp" /><button onClick={() => setModal({ type: "add" })}>Add judge</button></div>
      {!judges.length ? <Empty title="No judges yet">Add one to get their private scoring link.</Empty> : (
        <div className="tablewrap"><table><thead><tr><th>Judge</th><th>Assigned</th><th>Progress</th><th>Private link</th><th /></tr></thead>
          <tbody>{judges.map(j => {
            const url = location.origin + j.path;
            const total = j.teams, pct = total ? Math.round((j.submitted + j.recused) / total * 100) : 0;
            return (
              <tr key={j.id}>
                <td><div className="row" style={{ flexWrap: "nowrap" }}><Avatar name={j.name} /><b style={{ whiteSpace: "nowrap" }}>{j.name}</b></div></td>
                <td className="tiny">{ev.assignment_mode === "all" ? "All teams" : ev.assignment_mode === "track" ? (j.tracks.length ? j.tracks.join(", ") : <Pill kind="medium">no tracks</Pill>) : `${j.team_ids.length} teams`}<div className="muted">{total} to score</div></td>
                <td className="tiny" style={{ minWidth: 150 }}>{j.submitted} of {total} submitted{j.drafts ? `, ${j.drafts} draft` : ""}{j.recused ? `, ${j.recused} recused` : ""}<div className="progress" style={{ margin: "5px 0 0" }}><i style={{ width: pct + "%" }} /></div></td>
                <td style={{ minWidth: 260 }}><CopyBox value={url} label={`Link for ${j.name}`} /></td>
                <td><div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                  <a className="btn small quiet" href={invite(j, url)}>Email link</a>
                  {ev.assignment_mode !== "all" ? <button className="small quiet" onClick={() => setModal({ type: "edit", j })}>Assign</button> : <button className="small quiet" onClick={() => setModal({ type: "edit", j })}>Rename</button>}
                  <button className="small danger" onClick={async () => { if (confirm("Remove this judge and their scores?")) { await api("/judges/" + j.id, { method: "DELETE" }); reload(true); } }}>Remove</button></div></td>
              </tr>);
          })}</tbody></table></div>
      )}
      {judges.length ? <p className="muted tiny">Anyone with a link can score as that judge, so send each link only to that person.</p> : null}
      <Modal open={!!modal} onClose={() => setModal(null)} title={modal?.type === "add" ? "Add a judge" : "Edit judge"}>
        {modal ? <Assign ev={ev} teams={teams} judge={modal.type === "add" ? null : modal.j} onSave={save} onClose={() => setModal(null)} /> : null}
      </Modal>
    </div>
  );
}
