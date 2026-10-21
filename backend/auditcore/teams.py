"""Match a team to its repository: normalise URLs, detect shared repos, and compare
the team's listed members with who actually committed."""
from __future__ import annotations

import re

from .ingest import IngestError, parse_github_url


def normalize_repo(url: str) -> str | None:
    try:
        o, n = parse_github_url(url)
    except IngestError:
        return None
    if set(o) <= {"."} or set(n) <= {"."}:
        return None
    return f"{o}/{n}".lower()


def canonical_url(url: str) -> str | None:
    key = normalize_repo(url)
    return f"https://github.com/{key}" if key else None


def _handle_from_email(email: str) -> str | None:
    m = re.match(r"^(?:\d+\+)?([\w.-]+)@users\.noreply\.github\.com$", email or "", re.I)
    return m.group(1).lower() if m else None


def _tok(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2}


def match_members(members: list[dict], authors: list[dict]) -> dict:
    """members: [{name, github?, email?}], authors: [{name, email, commits}] from git history.

    A member matches when their GitHub handle, e-mail, or name tokens line up with a
    committer. Committers who match nobody are listed as 'unmatched_authors' so a judge
    can see outside contributors; members who match nobody are 'silent_members'."""
    matched, silent = [], []
    used = set()
    for m in members:
        h = (m.get("github") or "").lower().lstrip("@")
        em = (m.get("email") or "").lower()
        nt = _tok(m.get("name", ""))
        hit = None
        for i, a in enumerate(authors):
            ah = _handle_from_email(a["email"])
            if (h and (h == ah or h in _tok(a["name"]) or h == a["name"].lower())) or \
               (em and em == a["email"].lower()) or (nt and len(nt & _tok(a["name"])) >= min(2, len(nt))):
                hit = i
                break
        if hit is None:
            silent.append(m.get("name") or h)
        else:
            used.add(hit)
            matched.append({"member": m.get("name") or h, "committer": authors[hit]["name"], "commits": authors[hit]["commits"]})
    unmatched = [{"name": a["name"], "email": a["email"], "commits": a["commits"]}
                 for i, a in enumerate(authors) if i not in used and "[bot]" not in a["name"].lower()]
    flags = []
    if members and authors and not matched:
        flags.append({"severity": "high", "code": "no_member_committed",
                      "message": "None of the listed team members appear among the repository's committers"})
    elif silent and members:
        flags.append({"severity": "low", "code": "silent_members",
                      "message": f"Listed members with no matching commits: {', '.join(map(str, silent))}"})
    total = sum(a["commits"] for a in authors) or 1
    outside = sum(a["commits"] for a in unmatched)
    if members and matched and outside / total >= 0.4:
        flags.append({"severity": "medium", "code": "outside_contributors",
                      "message": f"{outside/total:.0%} of commits come from people not on the team list"})
    return {"matched": matched, "silent_members": silent, "unmatched_authors": unmatched[:10], "flags": flags}
