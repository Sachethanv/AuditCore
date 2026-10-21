import { useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api.js";
import { useTheme } from "./hooks.js";
import { TokenGate, ToastProvider } from "./components/ui.jsx";
import CommandPalette from "./components/CommandPalette.jsx";
import Events from "./pages/Events.jsx";
import EventPage from "./pages/EventPage.jsx";
import TeamPage from "./pages/TeamPage.jsx";
import { QuickCheck, EvalPage } from "./pages/Quick.jsx";
import SubmitPage from "./pages/SubmitPage.jsx";
import JudgePage from "./pages/JudgePage.jsx";
import ResultsPage from "./pages/ResultsPage.jsx";
import { EventsCtx } from "./ctx.js";


function Shell() {
  const [events, setEvents] = useState([]);
  const [health, setHealth] = useState({});
  const [theme, toggleTheme] = useTheme();
  const [palette, setPalette] = useState(false);
  const loc = useLocation();
  const refresh = useCallback(() => api("/events").then(setEvents).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh, loc.pathname]);
  useEffect(() => { api("/health").then(setHealth).catch(() => {}); }, []);
  useEffect(() => {
    const k = e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(p => !p); } };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  useEffect(() => { window.scrollTo(0, 0); }, [loc.pathname]);
  const evId = (loc.pathname.match(/^\/events\/(\d+)/) || [])[1];
  const isDark = theme ? theme === "dark" : typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches;
  return (
    <EventsCtx.Provider value={{ events, refresh, health }}>
      <div className="shell">
        <aside className="rail">
          <NavLink className="brand" to="/events"><i />AuditCore</NavLink>
          <button className="search-btn" onClick={() => setPalette(true)} aria-label="Search teams and events">
            <span>Search…</span><span className="hint" style={{ marginLeft: "auto" }}><span className="kbd">Ctrl K</span></span>
          </button>
          <nav className="nav" aria-label="Spaces">
            <NavLink to="/events" className={({ isActive }) => (isActive || loc.pathname.startsWith("/teams") ? "active" : "")} end={false}>Hackathons<small>Teams, judges, leaderboard</small></NavLink>
            <div className="sub">{events.map(e => <NavLink key={e.id} to={`/events/${e.id}`} className={String(e.id) === evId ? "active" : ""} style={String(e.id) === evId ? { background: "var(--teal-soft)", color: "var(--teal-ink)" } : null}>{e.name}</NavLink>)}</div>
            <NavLink to="/quick">Quick check<small>One repository</small></NavLink>
          </nav>
          <div className="foot">
            <span>Review engine: <b>{health.gemini ? "Gemini" : "offline estimate"}</b>{health.gemini ? "" : " (no Gemini key)"}</span>
            <button className="quiet small" onClick={toggleTheme}>{isDark ? "Light mode" : "Dark mode"}</button>
          </div>
        </aside>
        <main id="main" tabIndex={-1}><Outlet /></main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} events={events} />
    </EventsCtx.Provider>
  );
}

function NotFound() {
  const nav = useNavigate();
  return <div><h1>Page not found</h1><p className="lede">That address doesn’t match anything here.</p><p><button onClick={() => nav("/events")}>Go to hackathons</button></p></div>;
}

export default function App() {
  return (
    <ToastProvider>
      <TokenGate />
      <Routes>
        <Route path="/submit/:tok" element={<SubmitPage />} />
        <Route path="/judge/:code" element={<JudgePage />} />
        <Route path="/results/:tok" element={<ResultsPage />} />
        <Route element={<Shell />}>
          <Route index element={<Navigate to="/events" replace />} />
          <Route path="/events" element={<Events />} />
          <Route path="/events/:id" element={<EventPage />} />
          <Route path="/events/:id/:tab" element={<EventPage />} />
          <Route path="/teams/:id" element={<TeamPage />} />
          <Route path="/teams/:id/:tab" element={<TeamPage />} />
          <Route path="/quick" element={<QuickCheck />} />
          <Route path="/quick/:id" element={<EvalPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </ToastProvider>
  );
}
