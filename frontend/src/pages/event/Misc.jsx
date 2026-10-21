import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api.js";
import { Field, useToast } from "../../components/ui.jsx";
import { download, fromLocalInput, toLocalInput } from "../../utils.js";
import { TrackEditor } from "../Events.jsx";
import { useEvents } from "../../ctx.js";

export function Rubric({ ev, reload }) {
  const toast = useToast();
  const [rb, setRb] = useState(() => JSON.parse(JSON.stringify(ev.rubric)));
  const [json, setJson] = useState(() => JSON.stringify(ev.rubric, null, 2));
  const total = rb.criteria.reduce((a, c) => a + (+c.weight || 0), 0);
  const setW = (i, v) => setRb({ ...rb, criteria: rb.criteria.map((c, k) => (k === i ? { ...c, weight: +v || 0 } : c)) });
  const save = async body => { try { const r = await api(`/events/${ev.id}/rubric`, { method: "PUT", body }); setRb(r); setJson(JSON.stringify(r, null, 2)); toast("Rubric saved"); reload(true); } catch (x) { toast(x.message, true); } };
  return (
    <div>
      <h2 style={{ marginTop: 20 }}>Scoring rubric</h2>
      <p className="lede">Criteria marked AI are scored by AuditCore from the repository. The others are scored by judges because code cannot show them. Adjust weights to fit this event; changes apply to evaluations and scorecards from now on.</p>
      <div className="tablewrap"><table><thead><tr><th>Criterion</th><th>Scored by</th><th className="num">Weight</th></tr></thead>
        <tbody>{rb.criteria.map((c, i) => (
          <tr key={c.id}><td><b>{c.title}</b><div className="tiny muted" style={{ maxWidth: "70ch" }}>{c.description}</div></td>
            <td><span className={"pill " + (c.ai_assisted === false ? "" : "run")}>{c.ai_assisted === false ? "Judge" : "AI"}</span></td>
            <td className="num"><input type="number" min="0" step="1" value={c.weight} onChange={e => setW(i, e.target.value)} style={{ width: 84 }} aria-label={`Weight for ${c.title}`} /></td></tr>))}</tbody></table></div>
      <div className="row" style={{ marginTop: 14 }}><span className="muted">Total weight {total}</span><span className="sp" /><button onClick={() => save(rb)}>Save weights</button></div>
      <details style={{ marginTop: 18 }}><summary>Edit the full rubric as JSON (titles, descriptions, anchors)</summary>
        <textarea style={{ minHeight: 300, fontFamily: "var(--mono)", fontSize: ".8rem" }} spellCheck={false} value={json} onChange={e => setJson(e.target.value)} />
        <p><button className="quiet" onClick={() => { try { save(JSON.parse(json)); } catch { toast("That isn’t valid JSON", true); } }}>Save JSON</button></p>
      </details>
    </div>
  );
}

export function FeedbackTab({ ev }) {
  const toast = useToast();
  return (
    <div>
      <h2 style={{ marginTop: 20 }}>Share feedback with teams</h2>
      <p className="lede">Each evaluation drafts feedback on the team’s page. Review it there and tick “Share with team” on what should go out. Teams then see it on their private link, and you can also download everything marked as shared.</p>
      <p><button onClick={async () => { try { download(await api(`/events/${ev.id}/feedback.csv`), `feedback-${ev.id}.csv`); } catch (x) { toast(x.message, true); } }}>Download shared feedback (CSV)</button></p>
    </div>
  );
}

export function Settings({ ev, reload }) {
  const toast = useToast();
  const nav = useNavigate();
  const { refresh } = useEvents();
  const [f, setF] = useState({ name: ev.name, s: toLocalInput(ev.hackathon_start), e: toLocalInput(ev.hackathon_end), theme: ev.theme || "", tracks: ev.tracks || [] });
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const save = async e => {
    e.preventDefault();
    try { await api(`/events/${ev.id}`, { method: "PATCH", body: { name: f.name, hackathon_start: fromLocalInput(f.s), hackathon_end: fromLocalInput(f.e), theme: f.theme, tracks: f.tracks } }); toast("Settings saved"); refresh(); reload(true); }
    catch (x) { toast(x.message, true); }
  };
  return (
    <div>
      <h2 style={{ marginTop: 20 }}>Event settings</h2>
      <form onSubmit={save} className="card" style={{ marginTop: 12 }}>
        <div className="inline"><label className="grow">Event name<input required maxLength={200} value={f.name} onChange={set("name")} /></label><label>Starts<input type="datetime-local" value={f.s} onChange={set("s")} /></label><label>Ends<input type="datetime-local" value={f.e} onChange={set("e")} /></label></div>
        <p className="muted tiny" style={{ margin: "6px 0 0" }}>Commits before the start time are flagged on every team; decks uploaded after the end time are flagged too. After changing the start time, evaluate teams again.</p>
        <Field label="Theme or problem statement" small="(teams’ pitches are compared with it)"><textarea value={f.theme} onChange={set("theme")} /></Field>
        <Field label="Tracks"><TrackEditor tracks={f.tracks} onChange={tracks => setF({ ...f, tracks })} /></Field>
        <p style={{ marginTop: 16 }}><button>Save settings</button></p>
      </form>
      <h2>Delete this hackathon</h2>
      <p className="muted">Removes its teams, evaluations, feedback and judge scores permanently.</p>
      <button className="danger" onClick={async () => { if (confirm(`Delete “${ev.name}” and everything in it?`)) { try { await api("/events/" + ev.id, { method: "DELETE" }); refresh(); nav("/events"); } catch (x) { toast(x.message, true); } } }}>Delete hackathon</button>
    </div>
  );
}
