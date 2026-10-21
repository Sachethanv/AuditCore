import { NavLink, useParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { useLoad } from "../hooks.js";
import { ErrorNote, PageSkeleton } from "../components/ui.jsx";
import { fmtDate, plural } from "../utils.js";
import { PhaseChip } from "./Events.jsx";
import Overview from "./event/Overview.jsx";
import Leaderboard from "./event/Leaderboard.jsx";
import Teams from "./event/Teams.jsx";
import Judges from "./event/Judges.jsx";
import { Rubric, FeedbackTab, Settings } from "./event/Misc.jsx";

const TABS = [["overview", "Overview"], ["leaderboard", "Leaderboard"], ["teams", "Teams"], ["judges", "Judges"], ["rubric", "Rubric"], ["feedback", "Feedback"], ["settings", "Settings"]];
const busy = t => t.latest && ["queued", "running"].includes(t.latest.status);

export default function EventPage() {
  const { id, tab = "overview" } = useParams();
  const { data, error, reload } = useLoad(async () => {
    const [ev, teams, lb, judges] = await Promise.all([api(`/events/${id}`), api(`/events/${id}/teams`), api(`/events/${id}/leaderboard`), api(`/events/${id}/judges`)]);
    return { ev, teams, lb, judges };
  }, [id], { shouldPoll: d => !!d?.teams.some(busy), interval: 2500 });

  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return <PageSkeleton />;
  const { ev, teams, lb, judges } = data;
  const props = { ev, teams, lb, judges, reload, running: teams.some(busy) };
  return (
    <div>
      <div className="crumb"><Link to="/events">Hackathons</Link></div>
      <div className="row"><h1>{ev.name}</h1><PhaseChip ev={ev} /></div>
      <div className="muted tiny" style={{ marginTop: 6 }}>
        {plural(ev.team_count, "team")} · {ev.hackathon_start ? `${fmtDate(ev.hackathon_start)} to ${fmtDate(ev.hackathon_end) || "no end set"}` : "dates not set"}
        {ev.tracks?.length ? ` · ${ev.tracks.join(", ")}` : ""}
      </div>
      <nav className="tabs" aria-label="Sections">
        {TABS.map(([k, l]) => (
          <NavLink key={k} to={`/events/${id}/${k}`} className={tab === k ? "active" : ""} replace>
            {l}{k === "teams" ? <span className="count">{teams.length}</span> : k === "judges" && judges.length ? <span className="count">{judges.length}</span> : null}
          </NavLink>
        ))}
      </nav>
      {tab === "overview" && <Overview {...props} />}
      {tab === "leaderboard" && <Leaderboard {...props} />}
      {tab === "teams" && <Teams {...props} />}
      {tab === "judges" && <Judges {...props} />}
      {tab === "rubric" && <Rubric {...props} />}
      {tab === "feedback" && <FeedbackTab {...props} />}
      {tab === "settings" && <Settings {...props} />}
    </div>
  );
}
