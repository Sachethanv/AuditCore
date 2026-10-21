"""Assemble the final result and render it as Markdown."""
from __future__ import annotations

SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def weighted_score(rubric: dict, llm: dict) -> float:
    w = {c["id"]: c["weight"] for c in rubric["criteria"]}
    scored = [c for c in llm["criteria"] if c["id"] in w]
    tot = sum(w[c["id"]] for c in scored) or 1  # only criteria that were actually scored
    got = sum(c["score"] * w[c["id"]] for c in scored)
    return round(got / tot * 10, 1)  # 0-100


def integrity_flags(commits: dict, secrets: dict) -> list[dict]:
    flags = [dict(f, source="commits") for f in commits.get("flags", [])]
    flags += [{"severity": h["severity"], "code": h["code"], "message": h["message"], "source": "secrets"} for h in secrets["hygiene"]]
    return sorted(flags, key=lambda f: SEV_ORDER.get(f["severity"], 9))


def to_markdown(r: dict) -> str:
    m = r["meta"]
    L = [f"# AuditCore report: {m['repo']}", "",
         f"**Overall technical score: {r['overall_score']}/100** · review mode: `{r['review']['mode']}` · confidence: **{r['review']['confidence']}**", ""]
    L += [f"> {r['review']['summary']}", "", f"**What it does:** {r['review'].get('what_it_does','')}", ""]
    if r["review"].get("built_vs_reused"):
        L += [f"**Built vs reused:** {r['review']['built_vs_reused']}", ""]
    if r["review"].get("red_flags"):
        L += ["**Red flags:**"] + [f"- {x}" for x in r["review"]["red_flags"]] + [""]
    L += ["## Integrity flags (signals for a human, not verdicts)", ""]
    if not r["integrity_flags"]:
        L.append("None raised.")
    for f in r["integrity_flags"]:
        L.append(f"- **[{f['severity'].upper()}]** `{f['code']}`: {f['message']}")
    L += ["", "## Rubric scores", "", "| Criterion | Score | Weight |", "|---|---|---|"]
    w = {c["id"]: c for c in r["rubric"]["criteria"]}
    for c in r["review"]["criteria"]:
        L.append(f"| {w[c['id']]['title']} | {c['score']}/10{' ⚠ low evidence' if c.get('low_evidence') else ''} | {w[c['id']]['weight']} |")
    for c in r["review"]["criteria"]:
        L += ["", f"### {w[c['id']]['title']}: {c['score']}/10", c.get("reasoning", "")]
        for ev in c.get("evidence", []):
            L.append(f"- `{ev['file']}:{ev.get('line','?')}` → `{str(ev.get('quote',''))[:100]}`")
        for g in c.get("gaps", []):
            L.append(f"- Gap: {g}")
    for title, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses"),
                       ("Brittleness risks", "brittleness_risks"), ("Questions for the team", "questions_for_team")):
        if r["review"].get(key):
            L += ["", f"## {title}"] + [f"- {x}" for x in r["review"][key]]
    sec = r["secrets"]
    L += ["", "## Secrets & environment", f"- Scanner: {sec['scanner']}",
          f"- Findings: {sec['finding_count']}; files reading env vars: {sec['files_using_env_vars']}; `.env.example`: {sec['has_env_example']}; `.env` gitignored: {sec['gitignore_covers_env']}"]
    for f in sec["findings"][:10]:
        L.append(f"  - [{f['severity']}] {f['rule']} at `{f['file']}:{f['line']}` ({f['match']})")
    L += ["", "## Static security/robustness findings"]
    for f in sorted(r["security"]["findings"], key=lambda f: SEV_ORDER.get(f["severity"], 9))[:25]:
        L.append(f"- [{f['severity']}] {f['message']}: `{f['file']}:{f['line']}` `{f['snippet'][:90]}`")
    cm = r["commits"]
    L += ["", "## Commit history", f"- {cm.get('total_commits')} commits, {cm.get('distinct_authors')} author(s), {cm.get('active_days')} active day(s) over {cm.get('span_days')} days"]
    return "\n".join(L) + "\n"
