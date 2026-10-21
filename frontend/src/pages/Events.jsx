import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { useLoad } from "../hooks.js";
import { Empty, Field, PageSkeleton, ErrorNote, Pill, useToast } from "../components/ui.jsx";
import { fmtDate, fromLocalInput, phaseOf, plural } from "../utils.js";
import { useEvents } from "../ctx.js";

export function TrackEditor({ tracks, onChange }) {
  const [v, setV] = useState("");
  const add = () => {
    const x = v.trim();
    if (x && !tracks.some(t => t.toLowerCase() === x.toLowerCase())) onChange([...tracks, x]);
    setV("");
  };
  return (
    <div>
      <div className="row" style={{ gap: 8 }}>
        <input value={v} onChange={e => setV(e.target.value)} placeholder="e.g. Health, Education, Open track" style={{ maxWidth: 320 }}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); add(); } }} aria-label="Add a track" />
        <button type="button" className="quiet" onClick={add}>Add track</button>
      </div>
      {tracks.length ? <div className="chips">{tracks.map(t => <span className="chip" key={t}>{t}<button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(tracks.filter(x => x !== t))}>×</button></span>)}</div> : null}
    </div>
  );
}

export const PhaseChip = ({ ev }) => {
  const p = phaseOf(ev);
  return <Pill kind={p.key === "live" ? "ok live" : p.key === "closed" ? "" : p.key === "upcoming" ? "run" : ""}>{p.label}</Pill>;
};

export default function Events() {
  const { data: evs, error, loading, reload } = useLoad(() => api("/events"), []);
  const { refresh } = useEvents();
  const nav = useNavigate();
  const toast = useToast();
  const [f, setF] = useState({ name: "", s: "", e: "", theme: "", tracks: [] });
  const [busy, setBusy] = useState(false);
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const create = async ev => {
    ev.preventDefault();
    setBusy(true);
    try {
      const e = await api("/events", { method: "POST", body: { name: f.name, hackathon_start: fromLocalInput(f.s), hackathon_end: fromLocalInput(f.e), theme: f.theme, tracks: f.tracks } });
      refresh();
      nav(`/events/${e.id}/teams`);
    } catch (x) { toast(x.message, true); setBusy(false); }
  };
  return (
    <div>
      <h1>Hackathons</h1>
      <p className="lede">Create an event, add teams with their IDs and repositories, send each team its submission link, evaluate everything, and bring judges in to score.</p>

      <form className="card" onSubmit={create} style={{ marginTop: 24 }}>
        <h3>New hackathon</h3>
        <div className="inline" style={{ marginTop: 10 }}>
          <label className="grow">Event name<input required maxLength={200} value={f.name} onChange={set("name")} placeholder="e.g. Smart India Internal Round 2026" /></label>
          <label>Starts<input type="datetime-local" value={f.s} onChange={set("s")} /></label>
          <label>Ends<input type="datetime-local" value={f.e} onChange={set("e")} /></label>
        </div>
        <p className="help tiny muted" style={{ margin: "6px 0 0" }}>Start and end times power the “started before the event” and “after the deadline” checks.</p>
        <Field label="Theme or problem statement" small="(optional; teams’ pitches are compared with it)"><textarea style={{ minHeight: 70 }} value={f.theme} onChange={set("theme")} /></Field>
        <Field label="Tracks" small="(optional; lets you filter the leaderboard and assign judges by track)"><TrackEditor tracks={f.tracks} onChange={tracks => setF({ ...f, tracks })} /></Field>
        <p style={{ marginTop: 16 }}><button disabled={busy}>Create hackathon</button></p>
      </form>

      <h2>Your hackathons</h2>
      {loading && !evs ? <PageSkeleton /> : error ? <ErrorNote error={error} /> : !evs.length ? <Empty title="No hackathons yet">Name your first one above, then add teams.</Empty> : (
        <div className="grid3" style={{ marginTop: 14 }}>
          {evs.map(e => (
            <Link key={e.id} to={`/events/${e.id}`} className="card" style={{ textDecoration: "none", color: "inherit", display: "block" }}>
              <div className="row"><PhaseChip ev={e} /><span className="sp" />{e.results_public ? <Pill kind="ok">Results public</Pill> : null}</div>
              <h3 style={{ marginTop: 10, fontSize: "1.2rem" }}>{e.name}</h3>
              <p className="muted tiny" style={{ margin: "6px 0 0" }}>{plural(e.team_count, "team")}{e.tracks?.length ? ` · ${plural(e.tracks.length, "track")}` : ""}</p>
              <p className="muted tiny" style={{ margin: "2px 0 0" }}>{e.hackathon_start ? `${fmtDate(e.hackathon_start)} → ${fmtDate(e.hackathon_end) || "no end"}` : "Dates not set"}</p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
