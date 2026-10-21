import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { Modal } from "./ui.jsx";

/** Ctrl/Cmd+K: jump to any hackathon, tab or team. */
export default function CommandPalette({ open, onClose, events }) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [teams, setTeams] = useState([]);
  const [sel, setSel] = useState(0);
  const input = useRef();
  useEffect(() => {
    if (!open) return;
    setQ(""); setSel(0);
    Promise.all(events.slice(0, 8).map(e => api(`/events/${e.id}/teams`).then(ts => ts.map(t => ({ ...t, ev: e.name }))).catch(() => [])))
      .then(a => setTeams(a.flat()));
    setTimeout(() => input.current?.focus(), 30);
  }, [open]); // eslint-disable-line
  const items = useMemo(() => {
    const base = [
      { label: "Hackathons", hint: "Page", to: "/events" },
      { label: "Quick check", hint: "Page", to: "/quick" },
      ...events.flatMap(e => [
        { label: e.name, hint: "Hackathon", to: `/events/${e.id}` },
        { label: `${e.name} · Leaderboard`, hint: "Tab", to: `/events/${e.id}/leaderboard` },
        { label: `${e.name} · Judges`, hint: "Tab", to: `/events/${e.id}/judges` },
      ]),
      ...teams.map(t => ({ label: `${t.code} ${t.name}${t.project_title ? " · " + t.project_title : ""}`, hint: t.ev, to: `/teams/${t.id}` })),
    ];
    const s = q.trim().toLowerCase();
    return (s ? base.filter(i => i.label.toLowerCase().includes(s)) : base).slice(0, 40);
  }, [q, events, teams]);
  const go = i => { if (i) { onClose(); nav(i.to); } };
  return (
    <Modal open={open} onClose={onClose} className="palette">
      <input ref={input} value={q} placeholder="Search teams, hackathons, pages…" aria-label="Search"
        onChange={e => { setQ(e.target.value); setSel(0); }}
        onKeyDown={e => {
          if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(s + 1, items.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); }
          else if (e.key === "Enter") go(items[sel]);
        }} />
      <ul role="listbox">
        {items.map((i, n) => <li key={i.to + i.label} role="option" aria-selected={n === sel} onMouseEnter={() => setSel(n)} onClick={() => go(i)}>{i.label}<small>{i.hint}</small></li>)}
        {!items.length ? <li style={{ color: "var(--muted)" }}>Nothing matches “{q}”.</li> : null}
      </ul>
    </Modal>
  );
}
