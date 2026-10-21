"""Per-project landscape research: what similar work already exists, and how this project differs.

Cost design (a hackathon may have hundreds of teams):
  * at most 2 Gemini calls per project: one to write search queries, one to compare against candidates;
  * the searching itself is plain GitHub search (no LLM) and only the top few candidates' names, descriptions and
    star counts are shown to the model, never their code;
  * the result is cached on the team and only recomputed on request;
  * without a Gemini key it falls back to keyword queries and a transparent overlap score, clearly labelled.

This is evidence for a judge about novelty, never a score. It can miss private, non-GitHub or very new work.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .. import ingest, llm

STOP = set("""a an and are as at be by for from has have how in is it its of on or that the this to was we our with your you will can
using use used based app application project system platform tool tools helps help provide provides allow allows make makes new
through into their they them these those than then also more most such each any all not but if so do does done via per etc""".split())
GH_SEARCH = "https://api.github.com/search/repositories"


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", (text or "").lower()) if w not in STOP}


def keyword_queries(project: dict) -> list[str]:
    """Offline query writer: the most distinctive words of the title, problem and summary."""
    out = []
    for field in ("title", "problem", "summary", "what_it_does"):
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", project.get(field) or "") if w.lower() not in STOP]
        seen, uniq = set(), []
        for w in words:
            if w.lower() not in seen:
                seen.add(w.lower())
                uniq.append(w)
        if len(uniq) >= 2:
            out.append(" ".join(uniq[:5]))
    return list(dict.fromkeys(out))[:_max_queries()]


_gate = threading.Lock()
_last = [0.0]


def _get_json(url: str, token: str | None, timeout: int = 15):
    with _gate:  # GitHub search allows ~10 requests/min unauthenticated, ~30 with a token: space them out
        pool = max(1, len(ingest.github_tokens()))
        wait = (2.2 if token else 6.5) / pool - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    h = {"Accept": "application/vnd.github+json", "User-Agent": "auditcore"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return json.load(r)


_qcache: dict = {}


def github_search(query: str, token: str | None = None, per_page: int = 8, fetch=_get_json) -> list[dict]:
    hit = _qcache.get(query.lower())
    if hit and time.time() - hit[0] < 3600:  # identical queries across teams are common; do not spend quota twice
        return hit[1]
    out = _github_search(query, token, per_page, fetch)
    _qcache[query.lower()] = (time.time(), out)
    return out


def _github_search(query: str, token: str | None, per_page: int, fetch) -> list[dict]:
    url = f"{GH_SEARCH}?{urllib.parse.urlencode({'q': query + ' in:name,description,readme fork:false', 'sort': 'stars', 'per_page': per_page})}"
    items = fetch(url, token).get("items", [])
    return [{"repo": i["full_name"], "url": i["html_url"], "stars": i.get("stargazers_count", 0),
             "description": (i.get("description") or "")[:240], "language": i.get("language"),
             "topics": (i.get("topics") or [])[:8], "pushed_at": i.get("pushed_at"), "archived": bool(i.get("archived"))}
            for i in items]


def overlap(project_text: str, c: dict) -> float:
    a, b = tokens(project_text), tokens(" ".join([c["repo"].replace("/", " ").replace("-", " ").replace("_", " "), c["description"], " ".join(c["topics"])]))
    return round(len(a & b) / max(1, min(len(a), len(b), 25)), 3)


def _max_queries() -> int:
    return max(1, min(4, int(os.environ.get("AUDITCORE_LANDSCAPE_QUERIES", "2"))))


def _queries_llm(project: dict, model, key) -> list[str]:
    prompt = f"""Write 2 GitHub repository search queries (2-5 plain keywords each, no operators) that would find existing open-source
projects similar to this hackathon project. One for the problem/domain combined with the approach, one for the closest competing product idea.
PROJECT: {json.dumps({k: (v or '')[:700] for k, v in project.items()})}
Return ONLY JSON: {{"queries": ["...", "...", "..."]}}"""
    out = llm._call_gemini(prompt, model, key)
    return [q for q in (out.get("queries") or []) if isinstance(q, str) and q.strip()][:_max_queries()]


def _compare_llm(project: dict, cands: list[dict], model, key) -> dict:
    prompt = f"""You help hackathon judges judge INNOVATION fairly. Compare the project with the existing repositories found by search.
Use ONLY the information given. Do not invent repositories; do not claim a repository does something its description does not say.
If none is really similar, say no close match was found (that is not proof of novelty).

PROJECT: {json.dumps({k: (v or '')[:900] for k, v in project.items()})}
CANDIDATES: {json.dumps(cands)}

Return ONLY JSON:
{{"novelty": "novel|incremental|crowded|unclear",
 "summary": "2-3 sentences for a judge: where this project stands relative to existing work",
 "closest": [{{"repo": "owner/name from CANDIDATES", "why_similar": "1 sentence", "how_different": "1 sentence, or 'not evident'"}}],
 "differentiators": ["what this project does that the candidates do not seem to"],
 "judge_question": "one question a judge can ask to test whether the team knows this landscape"}}
novelty: crowded = several close matches; incremental = a close match exists, with a twist; novel = no close match among candidates; unclear = too little information."""
    return llm._call_gemini(prompt, model, key)


def offline_compare(project: dict, cands: list[dict]) -> dict:
    close = [c for c in cands if c["overlap"] >= 0.3]
    some = [c for c in cands if c["overlap"] >= 0.15]
    popular = [c for c in close if c["stars"] >= 200]
    if len(close) >= 3 or popular:
        nov, msg = "crowded", "Several existing projects match this one's wording, so the idea is likely well covered."
    elif close:
        nov, msg = "incremental", "At least one existing project closely matches this one's wording."
    elif some:
        nov, msg = "incremental", "Some existing projects overlap partly."
    else:
        nov, msg = "unclear", "No close keyword match was found on GitHub. That does not prove the idea is new."
    return {"novelty": nov, "summary": msg + " (Offline keyword comparison only: it cannot tell how the project actually differs. Add a Gemini key for a written comparison.)",
            "closest": [{"repo": c["repo"], "why_similar": c["description"] or "similar keywords", "how_different": "not evaluated offline"} for c in (close or some)[:4]],
            "differentiators": [], "judge_question": "Which existing tools did you look at before building this, and what does yours do that they do not?"}


def research(project: dict, own_repo: str | None = None, model: str | None = None, search=None) -> dict:
    """project: {title, summary, problem, what_it_does, theme}. Returns a cacheable dict; never raises."""
    search = search or github_search
    usage = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0}
    prev = getattr(llm._local, "sink", None)
    sink = llm._local.sink = usage
    try:
        key = llm.api_key()
        gh_token = ingest.next_token()
        mode = "gemini" if key else "offline"
        model = model or llm.model_for("broad") or os.environ.get("GEMINI_MODEL", llm.DEFAULT_MODEL)
        notes = []
        queries: list[str] = []
        if key:
            try:
                queries = _queries_llm(project, model, key)
            except Exception as e:  # noqa: BLE001
                notes.append(f"query writing failed ({str(e)[:80]}); used keywords")
        queries = queries or keyword_queries(project)
        if not queries:
            return _result("unavailable", mode, [], {}, usage, ["Not enough project description to search with."], model)
        seen, cands = {}, []
        errors = 0
        for q in queries:
            try:
                for c in search(q, gh_token):
                    if c["repo"].lower() == (own_repo or "").lower() or c["repo"] in seen:
                        continue
                    seen[c["repo"]] = 1
                    cands.append(c)
            except Exception as e:  # noqa: BLE001 - rate limit, offline, etc.
                errors += 1
                notes.append(f"GitHub search failed for '{q}': {str(e)[:80]}")
        if errors == len(queries):
            return _result("unavailable", mode, [], {}, usage, notes, model, queries)
        text = " ".join(str(project.get(k) or "") for k in ("title", "summary", "problem", "what_it_does"))
        for c in cands:
            c["overlap"] = overlap(text, c)
        cands = sorted(cands, key=lambda c: (-c["overlap"], -c["stars"]))[:6]
        cmp_ = None
        if key and cands:
            try:
                cmp_ = _compare_llm(project, cands, model, key)
                known = {c["repo"] for c in cands}
                cmp_["closest"] = [x for x in (cmp_.get("closest") or []) if isinstance(x, dict) and x.get("repo") in known][:5]
                if cmp_.get("novelty") not in ("novel", "incremental", "crowded", "unclear"):
                    cmp_["novelty"] = "unclear"
            except Exception as e:  # noqa: BLE001
                notes.append(f"comparison failed ({str(e)[:80]}); showing keyword comparison")
        if cmp_ is None:
            cmp_ = offline_compare(project, cands)
            if key:
                mode = "offline (Gemini failed)"
        return _result("done", mode, cands, cmp_, usage, notes, model, queries)
    finally:
        llm._local.sink = prev
        if prev is not None:  # the caller's counter includes our spend
            for k in usage:
                prev[k] += usage[k]


def _result(status, mode, cands, cmp_, usage, notes, model, queries=None):
    return {"status": status, "mode": mode, "model": model if mode == "gemini" else None, "queries": queries or [],
            "candidates": cands, "assessment": cmp_, "notes": notes, "usage": usage,
            "researched_at": datetime.now(timezone.utc).isoformat(),
            "caveat": "Searched GitHub only. Private, unpublished or non-GitHub work will not appear, and no close match is not proof of novelty."}
