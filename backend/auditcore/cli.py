"""Command line entry: python -m auditcore evaluate <github-url> [options]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import ingest, llm, report, teams
from .analyzers import alignment, commits, fingerprint, secrets, security, structure
from .paths import RUBRIC_PATH


def _dt(s: str | None):
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ai_rubric(rubric: dict) -> dict:
    """Only criteria marked ai_assisted are scored from the repo; the rest are for human judges."""
    return {**rubric, "criteria": [c for c in rubric["criteria"] if c.get("ai_assisted", True)]}


def evaluate(url: str, rubric: dict, start=None, end=None, model=None, members=None, context=None, ref=None, snapshot=False) -> dict:
    """context: optional {theme, title, summary, problem, deck} used to compare the pitch with the code."""
    rubric = ai_rubric(rubric)
    repo = ingest.clone(url, ref=ref, before=(end.isoformat() if (snapshot and end) else None))
    try:
        c = commits.analyze(repo.path, start, end)
        s = structure.analyze(repo.path)
        sec = security.analyze(repo.path)
        sc = secrets.analyze(repo.path)
        facts = {"commits": {k: v for k, v in c.items() if k != "flags"}, "commit_flags": c.get("flags", []),
                 "structure": s, "security": {k: v for k, v in sec.items() if k != "findings"} | {"findings": sec["findings"][:30]},
                 "secrets": sc, "github_meta": repo.meta}
        facts["security"]["test_files"] = sec["test_files"]
        try:
            rev = llm.review(repo.path, rubric, facts, model)
        except llm.LLMError as e:
            print(f"[warn] Gemini failed ({e}); falling back to heuristic review", file=sys.stderr)
            rev = llm.heuristic_review(rubric, {"structure": s, "security": sec, "secrets": sc, "commits": c})
            rev["mode"] += f" [Gemini error: {str(e)[:120]}]"
        rev = llm.apply_baseline(rubric, rev)
        flags = report.integrity_flags(c, sc)
        team_match = None
        if members is not None:
            team_match = teams.match_members(members, c.get("authors", []))
            flags = sorted(flags + [dict(f, source="team") for f in team_match["flags"]],
                           key=lambda f: report.SEV_ORDER.get(f["severity"], 9))
        fingerprints = fingerprint.compute(repo.path)
        # Work that started before the event: commits, share of code, and when the repo itself was created.
        pre_event = dict(c.get("pre_event") or {"start_set": False})
        created = repo.meta.get("created_at")
        if created:
            pre_event["repo_created_at"] = created
            if start:
                cd = datetime.fromisoformat(created.replace("Z", "+00:00"))
                days = (start - cd).total_seconds() / 86400
                pre_event["repo_created_days_before_start"] = round(days, 1) if days > 0 else 0
                if days >= 1 and not any(f["code"] == "pre_existing_work" for f in flags):
                    flags.append({"severity": "low", "code": "repo_created_before_event", "source": "github",
                                  "message": f"The repository was created {days:.0f} day(s) before the hackathon started (no commits before the start, so it may just have been set up in advance)"})
        facts_for_align = {"commits": {k: v for k, v in c.items() if k in ("total_commits", "span_days", "pre_event")},
                           "structure": {k: v for k, v in s.items() if k != "tree_sample"}}
        al = None
        if context:
            try:
                al = alignment.analyze(repo.path, context, facts_for_align, model)
            except Exception as ex:  # noqa: BLE001 - alignment must never sink the whole evaluation
                print(f"[warn] alignment failed: {ex}", file=sys.stderr)
            if al:
                flags = sorted(flags + alignment.flags(al), key=lambda f: report.SEV_ORDER.get(f["severity"], 9))
        if repo.meta.get("all_commits_after_snapshot"):
            flags.insert(0, {"severity": "high", "code": "all_after_deadline", "source": "commits",
                             "message": "Every commit was made after the round deadline; nothing existed when the round closed (the latest state is shown instead)"})
        elif repo.meta.get("commits_after_snapshot"):
            flags.append({"severity": "low", "code": "work_after_deadline_ignored", "source": "commits",
                          "message": f"{repo.meta['commits_after_snapshot']} commit(s) made after the round deadline were ignored; the code shown is as of the deadline"})
        if repo.meta.get("is_fork"):
            flags.insert(0, {"severity": "medium", "code": "is_fork", "source": "github",
                             "message": f"Repository is a GitHub fork of {repo.meta.get('parent')}"})
        return {"meta": {"repo": f"{repo.owner}/{repo.name}", "url": url,
                         "evaluated_at": datetime.now(timezone.utc).isoformat(), "github": repo.meta},
                "overall_score": report.weighted_score(rubric, rev), "review": rev,
                "integrity_flags": flags, "commits": c, "structure": s, "security": sec, "secrets": sc,
                "team_match": team_match, "fingerprints": fingerprints, "pre_event": pre_event,
                "alignment": al, "rubric": rubric}
    finally:
        repo.cleanup()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="auditcore")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evaluate", help="evaluate a GitHub repository")
    e.add_argument("url")
    e.add_argument("--hackathon-start", help="ISO date/time, e.g. 2026-10-10T09:00:00+05:30")
    e.add_argument("--hackathon-end")
    e.add_argument("--rubric", default=str(RUBRIC_PATH))
    e.add_argument("--model", help="Gemini model (default: $GEMINI_MODEL or built-in default)")
    e.add_argument("--out", default="reports", help="output directory")
    a = p.parse_args(argv)

    with open(a.rubric) as f:
        rubric = json.load(f)
    try:
        result = evaluate(a.url, rubric, _dt(a.hackathon_start), _dt(a.hackathon_end), a.model)
    except ingest.IngestError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, result["meta"]["repo"].replace("/", "__"))
    with open(base + ".json", "w") as f:
        json.dump({k: v for k, v in result.items() if k != "fingerprints"}, f, indent=2, default=str)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(report.to_markdown(result))
    print(f"{result['meta']['repo']}: score {result['overall_score']}/100, "
          f"{len(result['integrity_flags'])} flag(s), mode={result['review']['mode']}\n-> {base}.md / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
