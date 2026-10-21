import { Link } from "react-router-dom";
import { api } from "../../api.js";
import { CopyBox, Pill, useToast } from "../../components/ui.jsx";
import { phaseOf } from "../../utils.js";

const SEV = { high: 0, medium: 1, low: 2 };

/** What an organizer should look at next, most urgent first. */
export function attentionItems(teams, lb) {
  const rows = Object.fromEntries(lb.rows.map(r => [r.team_id, r]));
  const out = [];
  for (const t of teams) {
    const r = rows[t.id] || {};
    if (!t.repo_url) out.push({ t, sev: 0, why: "No repository link yet" });
    else if (t.latest?.status === "failed") out.push({ t, sev: 0, why: "Evaluation failed: " + (t.latest.error || "").slice(0, 80) });
    else if (!t.latest) out.push({ t, sev: 1, why: "Not evaluated yet" });
    if (r.high_flags) out.push({ t, sev: 0, why: r.top_flag || `${r.high_flags} high flag(s)` });
    if (r.deck_newer_than_eval) out.push({ t, sev: 1, why: "New deck since the last evaluation: evaluate again" });
    if (r.claims_unsupported >= 3) out.push({ t, sev: 1, why: `${r.claims_unsupported} claims in the pitch are not in the code` });
    if (r.max_spread >= 3) out.push({ t, sev: 1, why: "Judges disagree by 3+ points on a criterion" });
    if (t.repo_url && !t.deck && !(t.summary)) out.push({ t, sev: 2, why: "No pitch deck or description" });
  }
  const seen = new Set();
  return out.sort((a, b) => a.sev - b.sev).filter(x => (seen.has(x.t.id + x.why) ? false : seen.add(x.t.id + x.why))).slice(0, 12);
}

function Stat({ value, total, label, to }) {
  const inner = (
    <>
      <b>{value}{total != null ? <span className="muted" style={{ fontSize: "1rem", fontWeight: 500 }}> / {total}</span> : null}</b>
      <span>{label}</span>
      {total ? <div className="meter"><i style={{ width: Math.min(100, (value / total) * 100) + "%" }} /></div> : null}
    </>
  );
  return to ? <Link className="stat" to={to}>{inner}</Link> : <div className="stat">{inner}</div>;
}

function Distribution({ rows }) {
  const vals = rows.map(r => r.final).filter(v => v != null);
  if (!vals.length) return <p className="muted tiny">Scores appear here once teams are evaluated.</p>;
  const bins = Array.from({ length: 10 }, (_, i) => vals.filter(v => (i === 9 ? v >= 90 : v >= i * 10 && v < i * 10 + 10)).length);
  const max = Math.max(...bins);
  return (
    <div role="img" aria-label={`Score distribution across ${vals.length} teams`}>
      <div className="dist">{bins.map((n, i) => <div key={i} className={n ? "has" : ""} style={{ height: Math.max(4, (n / max) * 100) + "%" }} title={`${i * 10}–${i * 10 + 9}: ${n} team(s)`} />)}</div>
      <div className="dist-axis"><span>0</span><span>50</span><span>100</span></div>
    </div>
  );
}

export default function Overview({ ev, teams, lb, judges, reload }) {
  const toast = useToast();
  const phase = phaseOf(ev);
  const submitted = judges.reduce((a, j) => a + j.submitted, 0);
  const results = ev.results_public;
  const step = results ? 3 : submitted || phase.key === "closed" ? 2 : phase.key === "live" ? 1 : 0;
  const stepName = ["Upcoming", "Hacking", "Judging", "Results"];
  const evaluated = lb.rows.filter(r => r.score != null).length;
  const flagged = lb.rows.filter(r => r.high_flags > 0).length;
  const queue = attentionItems(teams, lb);
  const toggle = async () => {
    try { await api(`/events/${ev.id}/results`, { method: "PUT", body: { public: !results } }); toast(results ? "Results hidden" : "Results are now public"); reload(); }
    catch (x) { toast(x.message, true); }
  };
  const link = ev.results_path ? location.origin + ev.results_path : "";
  return (
    <div>
      <div className="steps" aria-label="Event progress">{stepName.map((s, i) => <div key={s} className={i < step ? "done" : i === step ? "now" : ""}>{s}</div>)}</div>
      <div className="stats">
        <Stat value={teams.length} label="Teams" to={`/events/${ev.id}/teams`} />
        <Stat value={teams.filter(t => t.repo_url).length} total={teams.length} label="With a repository" />
        <Stat value={teams.filter(t => t.deck).length} total={teams.length} label="Pitch decks received" />
        <Stat value={evaluated} total={teams.length} label="Evaluated" to={`/events/${ev.id}/leaderboard`} />
        <Stat value={flagged} label="Teams with high flags" to={`/events/${ev.id}/leaderboard`} />
        <Stat value={submitted} total={judges.length ? judges.reduce((a, j) => a + j.teams, 0) : null} label={judges.length ? "Scorecards submitted" : "Judges invited"} to={`/events/${ev.id}/judges`} />
      </div>

      <div className="grid2" style={{ marginTop: 8, alignItems: "start" }}>
        <section>
          <h2>Needs your attention</h2>
          {queue.length ? (
            <div className="queue">{queue.map((q, i) => (
              <Link key={i} to={`/teams/${q.t.id}`}>
                <Pill kind={["high", "medium", ""][q.sev]}>{["Act", "Check", "Note"][q.sev]}</Pill>
                <b>{q.t.code}</b> {q.t.name}<small>{q.why}</small>
              </Link>))}</div>
          ) : <p className="muted">{teams.length ? "Nothing is waiting on you." : "Add teams to get started."}</p>}
        </section>
        <section>
          <h2>Score spread</h2>
          <div className="card" style={{ marginTop: 12 }}><Distribution rows={lb.rows} /></div>
          <h2>Public results</h2>
          <div className="card" style={{ marginTop: 12 }}>
            <p style={{ marginTop: 0 }}>{results ? <><Pill kind="ok">Public</Pill> Anyone with the link can see the ranking (team, project, rank and final score only; never flags).</> : <><Pill>Hidden</Pill> Reveal the ranking when you are ready. Flags and AI notes are never shown there.</>}</p>
            {results && link ? <CopyBox value={link} label="Public results link" /> : null}
            <p style={{ marginBottom: 0 }}><button className={results ? "quiet" : ""} onClick={toggle}>{results ? "Hide results" : "Reveal results"}</button></p>
          </div>
        </section>
      </div>
    </div>
  );
}
