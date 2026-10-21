import { useParams } from "react-router-dom";
import { pub } from "../api.js";
import { useLoad } from "../hooks.js";
import { Pill, Ring } from "../components/ui.jsx";

export default function ResultsPage() {
  const { tok } = useParams();
  const { data: d, error } = useLoad(() => pub(`/results/${encodeURIComponent(tok)}`), [tok], { shouldPoll: x => !!x && !x.revealed, interval: 10000 });
  const brand = <div className="brand" style={{ padding: 0 }}><i />AuditCore</div>;
  if (error) return <div className="public">{brand}<h1 style={{ marginTop: 22 }}>This link doesn’t work</h1><p className="lede">{error.message}</p></div>;
  if (!d) return <div className="public">{brand}<p className="muted" style={{ marginTop: 22 }}>Loading…</p></div>;
  const top = d.rows.filter(r => r.rank <= 3);
  const order = [top.find(r => r.rank === 2), top.find(r => r.rank === 1), top.find(r => r.rank === 3)].filter(Boolean);
  document.title = `${d.event.name} · Results`;
  return (
    <div className="public" style={{ maxWidth: 980 }}>
      <div className="head">{brand}</div>
      <div className="hero"><Pill>Results</Pill><h1 style={{ marginTop: 10 }}>{d.event.name}</h1>{d.event.theme ? <p>{d.event.theme.slice(0, 220)}</p> : null}</div>
      {!d.revealed ? (
        <div className="empty" style={{ marginTop: 24 }}><b>Results are not public yet</b>The organizers will reveal the ranking when judging is finished. This page checks again every few seconds.</div>
      ) : (
        <>
          {d.provisional ? <div className="notice warn">Provisional ranking: judges have not finished scoring.</div> : null}
          {order.length > 1 ? (
            <div className="podium">{order.map(r => (
              <div key={r.code} className={"pod p" + r.rank}><span className="medal">{r.rank}</span>
                <Ring value={r.final} size={r.rank === 1 ? 100 : 82} stroke={9} />
                <h3>{r.name || r.code}</h3><span className="tiny muted">{r.title}</span>{r.track ? <span className="tiny"><Pill>{r.track}</Pill></span> : null}</div>))}</div>
          ) : null}
          <div className="tablewrap"><table><thead><tr><th className="num">Rank</th><th>Team</th><th>Project</th><th className="num">Score</th></tr></thead>
            <tbody>{d.rows.map(r => <tr key={r.code} className={r.rank === 1 ? "top" : ""}><td className={"rankno" + (r.rank === 1 ? " r1" : "")}>{r.rank}</td><td><b>{r.name}</b> <span className="muted tiny">{r.code}</span>{r.track ? <> <Pill>{r.track}</Pill></> : null}</td><td>{r.title}</td><td className="num"><b>{r.final}</b></td></tr>)}</tbody></table></div>
        </>
      )}
    </div>
  );
}
