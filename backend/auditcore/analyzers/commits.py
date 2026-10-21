"""Commit-history forensics. Produces metrics plus flags that always carry evidence.

Flags are signals for a human judge, never verdicts: squashed or rewritten history
can hide or fake any of them.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from ..ingest import git

LOW_INFO = re.compile(
    r"^(fix|fixes|update|updates|wip|test|tests|commit|changes?|stuff|asdf+|\.+|"
    r"initial commit|first commit|added? files?|done|final|misc|minor)\s*\d*$", re.I)
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
             "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock")
SEP = "\x1e"


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.strip()).astimezone(timezone.utc)


def _flag(severity: str, code: str, message: str, evidence: dict) -> dict:
    return {"severity": severity, "code": code, "message": message, "evidence": evidence}


def analyze(repo_path: str, hackathon_start: datetime | None = None,
            hackathon_end: datetime | None = None, max_commits: int = 3000) -> dict:
    out = git(repo_path, "log", "--all", f"--max-count={max_commits}", "--numstat",
              f"--pretty=format:{SEP}%H|%an|%ae|%aI|%cI|%s")
    commits = []
    for block in out.split(SEP):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        parts = lines[0].split("|", 5)
        if len(parts) < 6:
            continue
        h, an, ae, adate, cdate, subj = parts
        added = deleted = files = 0
        for ln in lines[1:]:
            cols = ln.split("\t")
            if len(cols) != 3 or any(cols[2].endswith(l) for l in LOCKFILES):
                continue  # skip blanks, and lockfiles which distort size
            a, d, _ = cols
            if a == "-":  # binary
                files += 1
                continue
            added += int(a); deleted += int(d); files += 1
        try:
            commits.append({"hash": h, "author": an, "email": ae,
                            "date": _parse_dt(adate), "commit_date": _parse_dt(cdate),
                            "subject": subj, "added": added, "deleted": deleted, "files": files})
        except ValueError:
            continue
    if not commits:
        return {"total_commits": 0, "flags": [_flag("high", "no_history", "Repository has no readable commit history", {})]}

    commits.sort(key=lambda c: c["date"])
    n = len(commits)
    first, last = commits[0]["date"], commits[-1]["date"]
    span_days = max((last - first).total_seconds() / 86400, 0.0)
    days = Counter(c["date"].date().isoformat() for c in commits)
    busiest_day, busiest_n = days.most_common(1)[0]
    total_added = sum(c["added"] for c in commits) or 1
    biggest = max(commits, key=lambda c: c["added"])
    low = sum(1 for c in commits if LOW_INFO.match(c["subject"].strip()) or len(c["subject"].strip()) < 4)
    authors = Counter(c["email"] for c in commits)
    names = {}
    for c in commits:
        names.setdefault(c["email"], c["author"])
    gaps_h = [(b["date"] - a["date"]).total_seconds() / 3600 for a, b in zip(commits, commits[1:])]

    metrics = {
        "total_commits": n,
        "distinct_authors": len(authors),
        "author_commit_counts": dict(authors.most_common(10)),
        "authors": [{"name": names[e], "email": e, "commits": k} for e, k in authors.most_common(30)],
        "first_commit": first.isoformat(), "last_commit": last.isoformat(),
        "span_days": round(span_days, 2), "active_days": len(days),
        "busiest_day": busiest_day, "busiest_day_share": round(busiest_n / n, 3),
        "total_lines_added": total_added if total_added != 1 else 0,
        "largest_commit": {"hash": biggest["hash"][:10], "lines_added": biggest["added"],
                           "share_of_all_added": round(biggest["added"] / total_added, 3),
                           "files": biggest["files"], "subject": biggest["subject"][:80]},
        "low_information_message_share": round(low / n, 3),
        "median_gap_hours": round(sorted(gaps_h)[len(gaps_h) // 2], 2) if gaps_h else 0,
    }

    flags: list[dict] = []
    # 1. Bulk dump: one commit holds most of the code.
    if n >= 2 and metrics["largest_commit"]["share_of_all_added"] >= 0.6 and biggest["added"] > 500:
        flags.append(_flag("high", "bulk_commit",
            f"One commit adds {biggest['added']} lines ({metrics['largest_commit']['share_of_all_added']:.0%} of all code added)",
            metrics["largest_commit"]))
    # 2. Single-commit / near-empty history.
    if n <= 3 and total_added > 500:
        flags.append(_flag("high", "tiny_history",
            f"Only {n} commit(s) for {total_added} lines of code; incremental development cannot be verified",
            {"commits": n, "lines": total_added}))
    # 3. Everything within one day (hackathons are short, so only flag if 1 commit-day AND dump-like).
    if n >= 5 and metrics["busiest_day_share"] >= 0.9 and len(days) == 1 and span_days < 0.2:
        flags.append(_flag("medium", "single_burst",
            f"All {n} commits fall within {span_days*24:.1f} hours; possibly a scripted history replay",
            {"span_hours": round(span_days * 24, 1)}))
    # 4. Pre-existing work: commits before the hackathon start.
    if hackathon_start:
        before = [c for c in commits if c["date"] < hackathon_start]
        lines_before = sum(c["added"] for c in before)
        metrics["commits_before_start"] = len(before)
        metrics["lines_added_before_start"] = lines_before
        metrics["share_lines_before_start"] = round(lines_before / total_added, 3) if total_added else 0.0
        metrics["pre_event"] = {
            "start_set": True, "commits_before": len(before), "total_commits": n,
            "share_commits": round(len(before) / n, 3), "lines_before": lines_before,
            "share_lines": metrics["share_lines_before_start"],
            "earliest": before[0]["date"].isoformat() if before else None,
            "latest_before": before[-1]["date"].isoformat() if before else None,
            "sample": [{"hash": c["hash"][:10], "date": c["date"].date().isoformat(), "subject": c["subject"][:60],
                        "lines_added": c["added"]} for c in before[:6]],
        }
        if before:
            share_c, share_l = len(before) / n, metrics["share_lines_before_start"]
            sev = "high" if (share_l >= 0.3 or share_c >= 0.5 or len(before) >= 10) else "medium"
            flags.append(_flag(sev, "pre_existing_work",
                f"{len(before)} of {n} commits ({share_l:.0%} of the code by lines) were made before the hackathon started "
                f"({hackathon_start.date()}); earliest {before[0]['date'].date()}",
                metrics["pre_event"]))
    else:
        metrics["pre_event"] = {"start_set": False}
    if hackathon_end:
        after = [c for c in commits if c["date"] > hackathon_end]
        metrics["commits_after_end"] = len(after)
        if after:
            flags.append(_flag("medium", "after_deadline",
                f"{len(after)} commit(s) after the deadline; latest {after[-1]['date'].isoformat()}",
                {"count": len(after), "latest": after[-1]["date"].isoformat()}))
    # 5. Author vs committer date mismatch -> rebased/amended/rewritten history.
    skew = [c for c in commits if abs((c["commit_date"] - c["date"]).total_seconds()) > 3 * 86400]
    if len(skew) >= max(3, n * 0.3):
        flags.append(_flag("medium", "rewritten_history",
            f"{len(skew)} commits have author and committer dates >3 days apart (rebase or history rewrite)",
            {"count": len(skew)}))
    # 6. Message quality.
    if n >= 8 and metrics["low_information_message_share"] >= 0.5:
        flags.append(_flag("low", "low_info_messages",
            f"{metrics['low_information_message_share']:.0%} of commit messages are uninformative",
            {"share": metrics["low_information_message_share"]}))
    # 7. Contribution imbalance in a multi-author team.
    if len(authors) > 1:
        top_share = authors.most_common(1)[0][1] / n
        metrics["top_author_share"] = round(top_share, 3)
        if top_share >= 0.9 and n >= 10:
            flags.append(_flag("low", "single_contributor_dominates",
                f"One author made {top_share:.0%} of commits in a multi-author repo", dict(authors.most_common(3))))
    metrics["flags"] = flags
    return metrics
