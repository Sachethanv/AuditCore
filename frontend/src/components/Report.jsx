import { ago, fmtDay } from "../utils.js";
import { Pill, Ring } from "./ui.jsx";

export const Receipt = ({ x }) => (
  <div className="receipt"><span className="where">{x.file}:{x.line ?? "?"}</span>{x.quote}</div>
);
const List = ({ items }) => <ul>{(items || []).map((x, i) => <li key={i}>{x}</li>)}</ul>;
export const Flag = ({ f }) => <div className={"flag " + f.severity}><div><span className="sev">{f.severity}</span>{f.message}</div></div>;
export const Flags = ({ flags }) => flags?.length ? flags.map((f, i) => <Flag key={i} f={f} />) : <p className="muted">Nothing raised.</p>;

export function PreEvent({ pe }) {
  if (!pe || !pe.start_set)
    return <div className="preevent"><b>Not checked.</b> No hackathon start time was set, so work done before the event cannot be identified. Set the start time (event Settings, or the dates on Quick check) and evaluate again.</div>;
  const note = pe.repo_created_at ? (
    <div className="tiny muted" style={{ marginTop: 6 }}>
      Repository created {fmtDay(pe.repo_created_at)}{pe.repo_created_days_before_start > 0 ? `, ${Math.round(pe.repo_created_days_before_start)} day(s) before the start` : ""}.
    </div>
  ) : null;
  if (!pe.commits_before)
    return <div className="preevent clear"><b>No commits before the start.</b> All {pe.total_commits} commits were made after the hackathon began.{note}</div>;
  const sev = pe.share_lines >= 0.3 || pe.share_commits >= 0.5 || pe.commits_before >= 10 ? "high" : "medium";
  return (
    <div className={"preevent " + sev}>
      <b>{pe.commits_before} of {pe.total_commits} commits ({Math.round(pe.share_lines * 100)}% of the code by lines) were made before the hackathon started.</b>
      <div className="tiny muted" style={{ marginTop: 4 }}>Earliest commit {fmtDay(pe.earliest)}; latest before the start {fmtDay(pe.latest_before)}. A judge should ask what was built during the event.</div>
      <table><thead><tr><th>Date</th><th>Commit message</th><th className="num">Lines added</th></tr></thead>
        <tbody>{(pe.sample || []).map((c, i) => <tr key={i}><td className="code">{c.date}</td><td>{c.subject}</td><td className="num">{c.lines_added}</td></tr>)}</tbody></table>
      {note}
    </div>
  );
}

const CLAIM = { supported: "Supported", partial: "Partly", unsupported: "Not in code", not_checkable: "Can’t check" };
const FIT = { addressed: ["ok", "Addressed"], partially: ["medium", "Partly"], not_addressed: ["high", "Not addressed"], unclear: ["", "Unclear"] };

export function Alignment({ al }) {
  if (!al) return <p className="muted">No pitch was attached, so nothing was compared. Add a project description and deck to check the pitch against the code.</p>;
  const pf = al.problem_fit || {}, ds = al.declared_summary_check || {};
  const heur = al.mode?.startsWith("heuristic");
  return (
    <div>
      <div className="row" style={{ marginTop: 8, gap: 18 }}>
        <Ring value={al.alignment_score} size={104} stroke={10} label="pitch match" />
        <div className="tiny muted">{al.claims_supported ?? 0} of {al.claims_checked ?? 0} checkable claims supported<br />{al.mode}</div>
      </div>
      {heur ? <div className="notice">Offline keyword check: it finds technologies and features the pitch names that never appear in the code. Add a Gemini key for claim-by-claim checking and the problem-fit review.</div> : null}
      <p className="summary" style={{ fontSize: "1rem" }}>{al.summary}</p>
      {ds.verdict && ds.verdict !== "unclear" ? (
        <dl className="kv"><dt>Team’s description</dt><dd><Pill kind={ds.verdict === "differs" ? "high" : ds.verdict === "partly" ? "medium" : "ok"}>{ds.verdict}</Pill> {ds.note}</dd></dl>
      ) : null}
      {(al.claims || []).length ? (
        <div className="tablewrap"><table><thead><tr><th>Check</th><th>What the pitch says</th><th>What the code shows</th></tr></thead>
          <tbody>{al.claims.map((c, i) => (
            <tr key={i}>
              <td><Pill kind={c.status}>{CLAIM[c.status] || c.status}</Pill>{c.slide ? <div className="tiny muted">slide {c.slide}</div> : null}</td>
              <td>{c.claim}</td>
              <td>{c.note}{(c.evidence || []).map((x, j) => <Receipt key={j} x={x} />)}</td>
            </tr>))}</tbody></table></div>
      ) : null}
      <div className="cols">
        <div>{(al.code_gaps_vs_deck || []).length ? <><h3 style={{ marginTop: 20 }}>Promised but not in the code</h3><List items={al.code_gaps_vs_deck} /></> : null}</div>
        <div>{(al.undersold_in_code || []).length ? <><h3 style={{ marginTop: 20 }}>In the code but not in the pitch</h3><List items={al.undersold_in_code} /></> : null}</div>
      </div>
      {pf.verdict ? (
        <>
          <h3 style={{ marginTop: 20 }}>Does it solve the stated problem?</h3>
          <p style={{ margin: ".3rem 0" }}><Pill kind={(FIT[pf.verdict] || [""])[0]}>{(FIT[pf.verdict] || [0, pf.verdict])[1]}</Pill> {pf.reasoning}</p>
          {(pf.evidence || []).map((x, i) => <Receipt key={i} x={x} />)}
        </>
      ) : null}
      {(al.problem_gaps || []).length ? <><h3 style={{ marginTop: 20 }}>Gaps against the problem</h3><List items={al.problem_gaps} /></> : null}
      {(al.questions_for_team || []).length ? <><h3 style={{ marginTop: 20 }}>Questions about the gaps</h3><List items={al.questions_for_team} /></> : null}
    </div>
  );
}

/** One criterion with its score ruler (ticks at 2, 5 and 8 mark the rubric anchors) and cited code. */
export function CriterionCard({ c, meta, heuristic }) {
  return (
    <section className="crit">
      <header>
        <h3>{meta.title} <span className="muted tiny">weight {meta.weight}</span></h3>
        <span className="sc">{c.score}<small className="muted" style={{ fontSize: ".9rem", fontWeight: 500 }}>/10</small></span>
      </header>
      <div className="ruler" role="img" aria-label={`Score ${c.score} out of 10`}><b style={{ width: c.score * 10 + "%" }} /><s style={{ left: "20%" }} /><s style={{ left: "50%" }} /><s style={{ left: "80%" }} /></div>
      <div className="ruler-key" aria-hidden="true"><span style={{ left: "20%" }}>2</span><span style={{ left: "50%" }}>5</span><span style={{ left: "80%" }}>8</span></div>
      <p style={{ margin: ".5rem 0 0" }}>{c.reasoning}</p>
      {(c.evidence || []).map((x, i) => <Receipt key={i} x={x} />)}
      {c.low_evidence ? <p className="lowev">No verified citation for this score{heuristic ? " (offline estimate)" : ""}. Treat it with caution.</p> : null}
      {(c.gaps || []).length ? <ul className="plain">{c.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul> : null}
      {meta.anchors ? (
        <details><summary>What 2, 5 and 8 look like</summary>
          <dl className="kv">{Object.entries(meta.anchors).flatMap(([k, v]) => [<dt key={k + "t"}>{k}</dt>, <dd key={k + "d"}>{v}</dd>])}</dl>
        </details>
      ) : null}
    </section>
  );
}

export function EvaluationReport({ r, showTitle = true, parts, noRing }) {
  const rv = r.review, byId = Object.fromEntries((r.rubric.criteria || []).map(c => [c.id, c]));
  const heur = rv.mode.startsWith("heuristic");
  const st = rv.evidence_stats || {};
  const sec = r.secrets, cm = r.commits, tm = r.team_match;
  const sfind = (r.security.findings || []).slice(0, 25);
  const show = k => !parts || parts.includes(k);
  return (
    <div>
      {showTitle ? (
        <>
          <h1>{r.meta.repo}</h1>
          <div className="row tiny muted" style={{ marginTop: 6, gap: 16 }}>
            <a href={r.meta.url} target="_blank" rel="noopener noreferrer">Open on GitHub</a>
            <span>Reviewed {ago(r.meta.evaluated_at)}</span><span>Review: {rv.mode}</span><span>Confidence: {rv.confidence}</span>
            {st.cited ? <span>{st.verified} of {st.cited} citations verified against the code</span> : null}
          </div>
        </>
      ) : null}
      {show("summary") ? (
        <>
          <div className="row" style={{ marginTop: 20, gap: 22 }}>
            {noRing ? null : <Ring value={r.overall_score} size={132} stroke={12} label="technical" />}
            <p className="summary" style={{ flex: 1, minWidth: 260, margin: 0 }}>{rv.summary}</p>
          </div>
          {heur ? <div className="notice">Offline estimate: no Gemini key is configured, so scores come only from static signals and are rough. Add GEMINI_API_KEY for the cited review.</div> : null}
          {rv.what_it_does && rv.what_it_does !== "See README" ? (
            <dl className="kv"><dt>What it does</dt><dd>{rv.what_it_does}</dd>{rv.built_vs_reused ? <><dt>Built vs reused</dt><dd>{rv.built_vs_reused}</dd></> : null}</dl>
          ) : null}
        </>
      ) : null}
      {show("integrity") ? (
        <>
          <h2>Before the hackathon started</h2><PreEvent pe={r.pre_event} />
          <h2>Flags for a human to check</h2>
          <p className="muted tiny" style={{ margin: "4px 0 0" }}>Signals, not verdicts. Rewritten or squashed history can hide or fake any of them.</p>
          <Flags flags={r.integrity_flags} />
          {(rv.red_flags || []).length ? <><h3 style={{ marginTop: 18 }}>Red flags from the code review</h3><List items={rv.red_flags} /></> : null}
        </>
      ) : null}
      {show("pitch") ? <><h2>Pitch vs code</h2><Alignment al={r.alignment} /></> : null}
      {show("rubric") ? (
        <>
          <h2>Rubric review</h2>
          <div className="ledger">{(rv.criteria || []).map(c => <CriterionCard key={c.id} c={c} meta={byId[c.id] || { title: c.id, weight: "" }} heuristic={heur} />)}</div>
          <div className="cols">
            <div>{(rv.strengths || []).length ? <><h2>Strengths</h2><List items={rv.strengths} /></> : null}</div>
            <div>{(rv.weaknesses || []).length ? <><h2>Weaknesses</h2><List items={rv.weaknesses} /></> : null}</div>
          </div>
          <div className="cols">
            <div>{(rv.brittleness_risks || []).length ? <><h2>How it could break</h2><List items={rv.brittleness_risks} /></> : null}</div>
            <div>{(rv.questions_for_team || []).length ? <><h2>Questions to ask the team</h2><List items={rv.questions_for_team} /></> : null}</div>
          </div>
        </>
      ) : null}
      {show("details") ? (
        <>
          {tm ? (
            <>
              <h2>Team and committers</h2>
              <dl className="kv">
                <dt>Matched</dt><dd>{tm.matched.length ? tm.matched.map((m, i) => <div key={i}>{m.member} → {m.committer} ({m.commits} commits)</div>) : "No listed member matches a committer"}</dd>
                {tm.silent_members.length ? <><dt>No commits</dt><dd>{tm.silent_members.join(", ")}</dd></> : null}
                {tm.unmatched_authors.length ? <><dt>Other committers</dt><dd>{tm.unmatched_authors.map(a => `${a.name} (${a.commits})`).join(", ")}</dd></> : null}
              </dl>
            </>
          ) : null}
          <h2>Secrets and environment</h2>
          <dl className="kv"><dt>Scanner</dt><dd>{sec.scanner}</dd><dt>Findings</dt><dd>{sec.finding_count}</dd><dt>Files reading env vars</dt><dd>{sec.files_using_env_vars}</dd>
            <dt>.env.example</dt><dd>{sec.has_env_example ? "yes" : "no"}</dd><dt>.env ignored by git</dt><dd>{sec.gitignore_covers_env ? "yes" : "no"}</dd></dl>
          {(sec.findings || []).slice(0, 10).map((f, i) => <div className="receipt" key={i}><span className="where">{f.file}:{f.line}</span>{f.severity} · {f.rule} · {f.match}</div>)}
          <h2>Static security and robustness findings</h2>
          {sfind.length ? (
            <div className="tablewrap"><table><thead><tr><th>Severity</th><th>Finding</th><th>Where</th></tr></thead>
              <tbody>{sfind.map((f, i) => <tr key={i}><td><Pill kind={f.severity}>{f.severity}</Pill></td><td>{f.message}<div className="code muted tiny">{f.snippet}</div></td><td className="code">{f.file}:{f.line}</td></tr>)}</tbody></table></div>
          ) : <p className="muted">No patterns matched.</p>}
          <h2>Commit history</h2>
          <dl className="kv"><dt>Commits</dt><dd>{cm.total_commits}</dd><dt>Authors</dt><dd>{cm.distinct_authors ?? "–"}</dd>
            <dt>Active days</dt><dd>{cm.active_days ?? "–"} over {cm.span_days ?? "–"} days</dd>
            <dt>Largest commit</dt><dd>{cm.largest_commit ? `${cm.largest_commit.lines_added} lines (${Math.round(cm.largest_commit.share_of_all_added * 100)}% of all code)` : "–"}</dd>
            <dt>Uninformative messages</dt><dd>{cm.low_information_message_share != null ? Math.round(cm.low_information_message_share * 100) + "%" : "–"}</dd></dl>
        </>
      ) : null}
    </div>
  );
}

export function DeckText({ slides }) {
  if (!slides?.length) return null;
  return (
    <details><summary>Read the slide text ({slides.length} slides)</summary>
      {slides.map(s => <div className="fb" key={s.n}><b>Slide {s.n}</b><pre>{s.text || "(no text)"}</pre>{s.notes ? <div className="tiny muted">Speaker notes: {s.notes}</div> : null}</div>)}
    </details>
  );
}

export function LinkRow({ links }) {
  const L = links || {};
  const items = [["demo_url", "Live demo"], ["video_url", "Demo video"], ["docs_url", "Documentation"]].filter(([k]) => L[k]);
  if (!items.length && !L.notes) return <span className="muted">No demo, video or docs links yet.</span>;
  return (
    <div>
      <div className="links-list">{items.map(([k, l]) => <a key={k} href={L[k]} target="_blank" rel="noopener noreferrer">{l} ↗</a>)}</div>
      {L.notes ? <p className="tiny muted" style={{ whiteSpace: "pre-wrap" }}>Notes from the team: {L.notes}</p> : null}
    </div>
  );
}
