"""Slides-only review for rounds that do not look at code (an idea round).

Scores the round's AI criteria from the deck and the team's description. Every score needs a quote that really appears in
that text; without one the score is capped. Offline fallback is a labelled, capped heuristic."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import llm, report


def _text(ctx: dict) -> tuple[str, list[tuple[str, str]]]:
    parts = []
    for k, label in (("title", "title"), ("summary", "description"), ("problem", "problem statement")):
        if ctx.get(k):
            parts.append((label, str(ctx[k])))
    for sl in ((ctx.get("deck") or {}).get("slides") or []):
        parts.append((f"slide {sl.get('n')}", str(sl.get("text") or "")))
    return "\n".join(f"[{w}] {t}" for w, t in parts), parts


def _prompt(rubric: dict, ctx: dict, body: str) -> str:
    crit = "\n".join(f'- {c["id"]} (weight {c["weight"]}): {c["title"]}. {c["description"]}' + "".join(
        f"\n    score {k} looks like: {v}" for k, v in sorted((c.get("anchors") or {}).items())) for c in rubric["criteria"])
    return f"""You are helping hackathon judges review an early-stage idea from its slides and description ONLY. There is no code to read.
Be skeptical: buzzwords, unsupported claims and generic ideas score low. Do not reward polish or length. Event theme: {ctx.get('theme') or 'n/a'}

CRITERIA (integer 0-10; 5 = acceptable for a hackathon idea):
{crit}

TEAM MATERIAL (each part is prefixed with where it comes from):
{body[:30000]}

Return ONLY JSON:
{{"summary": "3 sentences for a judge", "what_it_does": "1-2 sentences",
 "criteria": [{{"id": "<criterion id>", "score": 0-10, "reasoning": "2 sentences",
    "evidence": [{{"file": "title|description|problem statement|slide N", "line": 0, "quote": "verbatim snippet (<=120 chars)"}}], "gaps": ["..."]}}],
 "strengths": ["..."], "weaknesses": ["..."], "questions_for_team": ["2-4 sharp questions for the pitch Q&A"], "confidence": "low|medium|high"}}"""


def _verify(rev: dict, parts: list[tuple[str, str]]) -> dict:
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    hay = {w: norm(t) for w, t in parts}
    total = kept = 0
    for c in rev.get("criteria", []):
        good = []
        for ev in c.get("evidence", []) or []:
            total += 1
            q = norm(str(ev.get("quote", "")))
            if q and (q in hay.get(str(ev.get("file")), "") or any(q in h for h in hay.values())):
                ev["verified"] = True
                good.append(ev)
                kept += 1
        c["evidence"] = good
        c["score"] = max(0, min(10, int(c.get("score", 0) or 0)))
        if not good:
            c["low_evidence"] = True
            c["score"] = min(c["score"], 5)  # a claim about the pitch that cannot be quoted is capped
    rev["evidence_stats"] = {"cited": total, "verified": kept}
    return rev


def _heuristic(rubric: dict, ctx: dict, parts) -> dict:
    slides = len((ctx.get("deck") or {}).get("slides") or [])
    words = sum(len(t.split()) for _, t in parts)
    base = 2 + (1 if slides >= 5 else 0) + (1 if len(ctx.get("problem") or "") >= 80 else 0) + (1 if words >= 250 else 0)
    return {"mode": "heuristic (deck review offline: counts slides and words, cannot judge the idea)",
            "summary": "Offline estimate only. Add a Gemini key for a review of the idea itself.",
            "what_it_does": (ctx.get("summary") or "")[:300] or "Unknown",
            "criteria": [{"id": c["id"], "score": min(5, base), "reasoning": "Heuristic from deck length and description.", "evidence": [], "gaps": [], "low_evidence": True}
                         for c in rubric["criteria"]],
            "strengths": [], "weaknesses": [], "brittleness_risks": [], "questions_for_team": [], "design_decisions": [], "design_questions": [],
            "confidence": "low", "evidence_stats": {"cited": 0, "verified": 0}}


def review(rubric: dict, ctx: dict, model: str | None = None) -> dict:
    """Returns a result dict shaped like cli.evaluate's, so the rest of the system treats it the same."""
    ai = {**rubric, "criteria": [c for c in rubric["criteria"] if c.get("ai_assisted", True)]}
    body, parts = _text(ctx)
    key = None if model == "offline" else llm.api_key()
    rev, err = None, ""
    if key and ai["criteria"] and parts:
        try:
            rev = _verify(llm._call_gemini(_prompt(ai, ctx, body), model or __import__("os").environ.get("GEMINI_MODEL", llm.DEFAULT_MODEL), key), parts)
            rev["mode"] = f"gemini:{model or __import__('os').environ.get('GEMINI_MODEL', llm.DEFAULT_MODEL)} (slides only)"
        except llm.LLMError as e:
            rev = None
            err = str(e)[:100]
    if rev is None:
        rev = _heuristic(ai, ctx, parts)
        if key:
            rev["mode"] += f" [Gemini error: {err}]"
    rev.setdefault("design_decisions", [])
    rev.setdefault("design_questions", [])
    rev = llm.apply_baseline(ai, rev)
    return {"meta": {"repo": None, "url": None, "evaluated_at": datetime.now(timezone.utc).isoformat(), "github": {}, "kind": "deck"},
            "overall_score": report.weighted_score(ai, rev) if ai["criteria"] else None, "review": rev, "integrity_flags": [], "commits": {},
            "structure": {}, "security": {"findings": []}, "secrets": {"findings": []}, "team_match": None, "pre_event": None,
            "alignment": None, "rubric": ai}
