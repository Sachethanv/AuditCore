import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api.js";
import { FlagPills, Pill, Ring, ScoreCell, StatusPill, useToast, Empty } from "../../components/ui.jsx";
import { download, repoName } from "../../utils.js";

const preCell = (r, startSet) => {
  if (!startSet) return <span className="muted tiny">set start time</span>;
  const p = r.pre_event;
  if (!p || !p.start_set) return r.score == null ? null : <span className="muted tiny">–</span>;
  if (!p.commits_before) return <Pill kind="ok">none</Pill>;
  const sev = p.share_lines >= 0.3 || p.share_commits >= 0.5 || p.commits_before >= 10 ? "high" : "medium";
  return <><Pill kind={sev}>{p.commits_before} commits</Pill><div className="tiny muted">{Math.round(p.share_lines * 100)}% of the code</div></>;
};
const pitchCell = r => !r.has_deck ? <span className="muted tiny">no deck</span> : (
  <>
    {r.alignment_score != null ? <><div className="scorecell"><span>{r.alignment_score}</span></div>{r.claims_unsupported ? <div className="tiny" style={{ color: "var(--flag)" }}>{r.claims_unsupported} not in code</div> : null}</> : <Pill kind="run">{r.deck_slides} slides</Pill>}
    {r.deck_newer_than_eval ? <div className="tiny" style={{ color: "var(--amber)" }}>new deck: re-evaluate</div> : null}
  </>
);

function Podium({ rows, provisional }) {
  const top = rows.filter(r => r.rank && r.rank <= 3);
  if (top.length < 2) return null;
  const order = [top.find(r => r.rank === 2), top.find(r => r.rank === 1), top.find(r => r.rank === 3)].filter(Boolean);
  return (
    <div className="podium" aria-label="Top three">
      {order.map(r => (
        <Link key={r.team_id} to={`/teams/${r.team_id}`} className={"pod p" + r.rank}>
          <span className="medal">{r.rank}</span>
          <Ring value={r.final} size={r.rank === 1 ? 96 : 80} stroke={9} />
          <h3>{r.name || r.code}</h3>
          <span className="tiny muted">{r.title || repoName(r.repo_url)}</span>
          {r.track ? <span className="tiny"><Pill>{r.track}</Pill></span> : null}
          {provisional ? <span className="tiny muted">provisional</span> : null}
        </Link>
      ))}
    </div>
  );
}

const COLS = {
  rank: r => r.rank ?? 9999, team: r => r.code, tech: r => r.score ?? -1, pitch: r => r.alignment_score ?? -1,
  judges: r => r.judge_count, overall: r => r.final ?? -1, flags: r => r.high_flags * 100 + r.medium_flags,
};

export default function Leaderboard({ ev, teams, lb, running, reload }) {
  const nav = useNavigate();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [track, setTrack] = useState("");
  const [flagged, setFlagged] = useState(false);
  const [sort, setSort] = useState({ k: "rank", dir: 1 });
  const done = lb.rows.filter(r => r.score != null).length;
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    let a = lb.rows.filter(r => (!s || `${r.code} ${r.name} ${r.title || ""} ${r.repo_url || ""}`.toLowerCase().includes(s)) && (!track || r.track === track) && (!flagged || r.high_flags > 0));
    const f = COLS[sort.k];
    return [...a].sort((x, y) => (f(x) > f(y) ? 1 : f(x) < f(y) ? -1 : 0) * sort.dir || x.code.localeCompare(y.code));
  }, [lb.rows, q, track, flagged, sort]);
  const runAll = async body => {
    try {
      const r = await api(`/events/${ev.id}/evaluate`, { method: "POST", body });
      toast(`Started ${r.started.length} evaluation(s)` + (r.skipped.length ? `; skipped ${r.skipped.map(s => `${s.team} (${s.reason})`).join(", ")}` : ""));
      reload(true);
    } catch (x) { toast(x.message, true); }
  };
  const th = (k, label, cls) => (
    <th className={"sortable " + (cls || "")} aria-sort={sort.k === k ? (sort.dir > 0 ? "ascending" : "descending") : undefined}
      onClick={() => setSort(s => ({ k, dir: s.k === k ? -s.dir : (k === "rank" || k === "team" ? 1 : -1) }))}>{label}{sort.k === k ? (sort.dir > 0 ? " ↑" : " ↓") : ""}</th>
  );
  return (
    <div>
      <div className="row" style={{ marginTop: 16 }}>
        <button onClick={() => runAll({})}>Evaluate all teams</button>
        <button className="quiet" onClick={() => runAll({ only_unevaluated: true })}>Only teams not yet evaluated</button>
        <span className="sp" />
        {running ? <Pill kind="run live">Evaluation in progress</Pill> : null}
        <button className="quiet" onClick={async () => { try { download(await api(`/events/${ev.id}/results.csv`), `results-${ev.id}.csv`); } catch (x) { toast(x.message, true); } }}>Download results (CSV)</button>
      </div>
      {!lb.event.start_set ? <div className="notice warn">No start time is set for this hackathon, so work done before the event can’t be flagged. <Link to={`/events/${ev.id}/settings`}>Set the start time</Link>.</div> : null}
      {lb.rows.length && lb.provisional && done ? <p className="muted tiny" style={{ margin: "12px 0 0" }}>Provisional ranking: based on AI technical scores only. Once judges submit scorecards, only teams with complete scorecards are ranked.</p> : null}
      <Podium rows={lb.rows} provisional={lb.provisional} />
      {lb.rows.length ? (
        <>
          <div className="toolbar">
            <input type="search" placeholder="Search teams…" value={q} onChange={e => setQ(e.target.value)} aria-label="Search teams" />
            {lb.tracks?.length ? <select value={track} onChange={e => setTrack(e.target.value)} aria-label="Filter by track"><option value="">All tracks</option>{lb.tracks.map(t => <option key={t}>{t}</option>)}</select> : null}
            <label className="check"><input type="checkbox" checked={flagged} onChange={e => setFlagged(e.target.checked)} /> High flags only</label>
            <span className="muted tiny">{rows.length} of {lb.rows.length}</span>
          </div>
          <div className="tablewrap"><table>
            <thead><tr>{th("rank", "#", "num")}{th("team", "Team")}{th("tech", "Technical")}{th("pitch", "Pitch match")}<th>Before event</th>{th("judges", "Judges", "num")}{th("overall", "Overall")}{th("flags", "Flags")}</tr></thead>
            <tbody>{rows.map(r => {
              const t = teams.find(x => x.id === r.team_id);
              const st = t?.latest && t.latest.status !== "done" ? <StatusPill e={t.latest} /> : null;
              return (
                <tr key={r.team_id} className={"link" + (r.rank === 1 ? " top" : "")} onClick={() => nav(`/teams/${r.team_id}`)}>
                  <td className={"rankno" + (r.rank === 1 ? " r1" : "")}>{r.rank ?? "–"}</td>
                  <td><Link to={`/teams/${r.team_id}`}><b>{r.code}</b> {r.name}</Link>{r.track ? <> <Pill>{r.track}</Pill></> : null}{r.title ? <div className="tiny">{r.title}</div> : null}<div className="tiny muted">{repoName(r.repo_url) || "No repository"}</div></td>
                  <td>{r.score != null ? <ScoreCell value={r.score} /> : (st || <Pill>Not evaluated</Pill>)}</td>
                  <td>{pitchCell(r)}</td>
                  <td>{preCell(r, lb.event.start_set)}</td>
                  <td className="num">{r.judge_count || <span className="muted">0</span>}{r.max_spread >= 3 ? <div className="tiny" style={{ color: "var(--amber)" }} title={`Judges disagree by ${r.max_spread} points on a criterion`}>disagree</div> : null}</td>
                  <td>{r.final != null ? <><ScoreCell value={r.final} />{r.complete ? null : <div className="tiny muted">partial</div>}</> : <span className="muted">–</span>}</td>
                  <td>{r.score != null ? <FlagPills high={r.high_flags} medium={r.medium_flags} /> : null}{r.top_flag ? <div className="tiny muted" style={{ maxWidth: "26ch" }}>{r.top_flag}</div> : null}</td>
                </tr>);
            })}</tbody></table></div>
        </>
      ) : <Empty title="No teams yet"><Link to={`/events/${ev.id}/teams`}>Add teams</Link> with their IDs and repository links.</Empty>}
      {lb.overlaps.length ? (
        <>
          <h2>Identical code across teams</h2>
          <div className="tablewrap"><table><thead><tr><th>Teams</th><th>Shared</th><th>Example</th></tr></thead>
            <tbody>{lb.overlaps.map((o, i) => (
              <tr key={i}><td><Pill kind={o.severity}>{o.a} and {o.b}</Pill></td><td>{Math.round(o.share_of_smaller * 100)}% of the smaller repo · {o.shared_files} files</td>
                <td className="code tiny">{o.examples.map((x, j) => <div key={j}>{x.a} = {x.b}</div>)}</td></tr>))}</tbody></table></div>
        </>
      ) : done > 1 ? <p className="muted tiny" style={{ marginTop: 18 }}>No identical source files found across {done} evaluated teams.</p> : null}
      <div className="notice" style={{ marginTop: 26 }}>
        <b>Scored by judges:</b> {lb.human_criteria.map(c => `${c.title} (${c.weight})`).join(", ") || "none"}. {lb.judges ? `${lb.judges} judge${lb.judges === 1 ? "" : "s"} invited.` : <><Link to={`/events/${ev.id}/judges`}>Invite judges</Link> to score them.</>} The AI scores the rest from the repository.
      </div>
    </div>
  );
}
