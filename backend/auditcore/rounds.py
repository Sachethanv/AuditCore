"""Rounds: the stages of a hackathon, configured per event (idea round with slides only, prototype round with a repo, finals...).

Pure helpers: state, validation, presets and advancement rules. Database work lives in server.py."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

ITEMS = ("summary", "deck", "repo", "demo", "video")
CHECKS = ("code", "pitch", "commits", "landscape")
MODES = ("none", "manual", "top_n", "top_percent", "threshold")

# Criteria that only make sense for slide-based rounds; scored from the deck and description, not from code.
DECK_CRITERIA = {
    "problem_clarity": {"id": "problem_clarity", "ai_assisted": True, "title": "Problem & audience clarity",
        "description": "Is there a specific user, a concrete problem and evidence that it matters? Judge only from the deck and description.",
        "anchors": {"2": "Vague problem or no stated user.", "5": "A real problem and user named, little evidence it matters.", "8": "Specific user, concrete problem, evidence or numbers."}},
    "solution_feasibility": {"id": "solution_feasibility", "ai_assisted": True, "title": "Solution & feasibility",
        "description": "Does the proposed approach plausibly solve the problem and could this team build it in the time? Reward concrete design choices over buzzwords.",
        "anchors": {"2": "Buzzwords only ('AI-powered platform').", "5": "A clear approach, little detail on how.", "8": "Concrete architecture or method, realistic scope."}},
}


def state(r, now: datetime) -> str:
    """upcoming | open | closed. Works in real time, even before the scheduler has run."""
    if r.closed_at or (r.ends_at and now >= r.ends_at):
        return "closed"
    if r.opened_at or (r.starts_at and now >= r.starts_at):
        return "open"
    return "upcoming"


def clean_round(d: dict) -> dict:
    out = {}
    if "name" in d:
        out["name"] = str(d["name"]).strip()[:120]
        if not out["name"]:
            raise ValueError("Round name is required")
    if "description" in d:
        out["description"] = (str(d["description"])[:2000] if d["description"] else None)
    if "accepts" in d:
        bad = [x for x in d["accepts"] if x not in ITEMS]
        if bad:
            raise ValueError(f"Unknown submission item(s) {bad}; use {list(ITEMS)}")
        out["accepts"] = list(dict.fromkeys(d["accepts"]))
    if "checks" in d:
        bad = [x for x in d["checks"] if x not in CHECKS]
        if bad:
            raise ValueError(f"Unknown check(s) {bad}; use {list(CHECKS)}")
        out["checks"] = {k: bool(d["checks"].get(k)) for k in CHECKS}
    if "advance" in d:
        a = d["advance"] or {}
        mode = a.get("mode", "none")
        if mode not in MODES:
            raise ValueError(f"advance.mode must be one of {list(MODES)}")
        val = a.get("value")
        if mode in ("top_n", "top_percent", "threshold"):
            try:
                val = float(val)
            except (TypeError, ValueError):
                raise ValueError("advance.value must be a number")
            if val <= 0 or (mode == "top_percent" and val > 100) or (mode == "threshold" and val > 100):
                raise ValueError("advance.value is out of range")
        out["advance"] = {"mode": mode, "value": val, "auto": bool(a.get("auto"))}
    if "checkpoint_minutes" in d:
        v = d["checkpoint_minutes"]
        if v in (None, 0, ""):
            out["checkpoint_minutes"] = None
        else:
            v = int(v)
            if not 5 <= v <= 1440:
                raise ValueError("checkpoint_minutes must be between 5 and 1440")
            out["checkpoint_minutes"] = v
    if "close_profile" in d:
        if d["close_profile"] not in (None, "", "broad", "deep"):
            raise ValueError("close_profile must be broad or deep")
        out["close_profile"] = d["close_profile"] or None
    return out


def derive_rubric(event_rubric: dict, spec: list[tuple], judge_only: tuple = ()) -> dict:
    """spec: [(criterion_id, weight)] taken from the event rubric or DECK_CRITERIA. judge_only ids lose their AI pre-score."""
    base = {c["id"]: c for c in event_rubric["criteria"]}
    base.update({k: v for k, v in DECK_CRITERIA.items() if k not in base})
    crit = []
    for cid, w in spec:
        c = dict(base[cid])
        c["weight"] = w
        if cid in judge_only:
            c["ai_assisted"] = False
        crit.append(c)
    return {**{k: v for k, v in event_rubric.items() if k != "criteria"}, "criteria": crit}


ALL_CHECKS = {k: True for k in CHECKS}
NO_CODE = {"code": False, "pitch": False, "commits": False, "landscape": True}
NONE = {k: False for k in CHECKS}


def preset(name: str, event_rubric: dict, start: datetime | None = None, hours: list[float] | None = None) -> list[dict]:
    idea = derive_rubric(event_rubric, [("problem_clarity", 25), ("solution_feasibility", 20), ("innovation_creativity", 35), ("impact_usefulness", 20)])
    final = derive_rubric(event_rubric, [("system_design", 25), ("innovation_creativity", 25), ("impact_usefulness", 20), ("demo_presentation", 30)],
                          judge_only=("system_design",))
    live = derive_rubric(event_rubric, [("system_design", 20), ("innovation_creativity", 25), ("impact_usefulness", 20), ("demo_presentation", 35)],
                         judge_only=("system_design",))
    everything = ["summary", "deck", "repo", "demo", "video"]
    P = {
        "single": [
            dict(name="Hackathon", accepts=everything, checks=ALL_CHECKS, rubric=None, advance=dict(mode="none"), checkpoint_minutes=30, close_profile=None)],
        "submit_finals": [
            dict(name="Submission", accepts=everything, checks=ALL_CHECKS, rubric=None, advance=dict(mode="top_n", value=10, auto=False), checkpoint_minutes=60, close_profile="broad"),
            dict(name="Finals", accepts=["demo", "video", "deck"], checks=NONE, rubric=final, advance=dict(mode="none"), checkpoint_minutes=None, close_profile=None)],
        "idea_build_final": [
            dict(name="Idea round", description="Slides and a short description only. No code is looked at.", accepts=["summary", "deck"], checks=NO_CODE, rubric=idea,
                 advance=dict(mode="top_percent", value=50, auto=False), checkpoint_minutes=None, close_profile="broad"),
            dict(name="Prototype", description="Repository and demo. Commits are checked while the round runs.", accepts=["summary", "deck", "repo", "demo"], checks=ALL_CHECKS,
                 rubric=None, advance=dict(mode="top_n", value=10, auto=False), checkpoint_minutes=30, close_profile="broad"),
            dict(name="Final", accepts=["repo", "demo", "video", "deck"], checks=ALL_CHECKS, rubric=final, advance=dict(mode="none"), checkpoint_minutes=30, close_profile="deep")],
        "four_stage": [
            dict(name="Idea round", accepts=["summary", "deck"], checks=NO_CODE, rubric=idea, advance=dict(mode="top_percent", value=60, auto=False), checkpoint_minutes=None, close_profile="broad"),
            dict(name="Prototype", accepts=["summary", "deck", "repo", "demo"], checks=ALL_CHECKS, rubric=None, advance=dict(mode="top_n", value=15, auto=False), checkpoint_minutes=30, close_profile="broad"),
            dict(name="Semi-final", description="Judges shortlist; teams keep building.", accepts=["repo", "demo", "video"], checks=ALL_CHECKS, rubric=None,
                 advance=dict(mode="top_n", value=5, auto=False), checkpoint_minutes=30, close_profile="deep"),
            dict(name="Final pitch", description="Live pitch and Q&A.", accepts=["demo", "video", "deck"], checks=NONE, rubric=live, advance=dict(mode="none"), checkpoint_minutes=None, close_profile=None)],
    }
    if name not in P:
        raise ValueError(f"Unknown preset {name!r}; use {list(P)}")
    out, t = [], start
    for i, spec in enumerate(P[name]):
        spec = dict(spec)
        if t is not None:
            h = (hours[i] if hours and i < len(hours) else 24)
            spec["starts_at"], spec["ends_at"] = t, t + timedelta(hours=h)
            t = spec["ends_at"]
        out.append(spec)
    return out


PRESET_INFO = {
    "single": "One round: everything is submitted and judged together.",
    "submit_finals": "Submission round, then a finals round for the top teams (live demos).",
    "idea_build_final": "Idea round (slides only) -> Prototype (repo + demo) -> Final.",
    "four_stage": "Idea -> Prototype -> Semi-final -> Final pitch.",
}


def select_advancing(rows: list[dict], mode: str, value) -> set[int]:
    """rows: leaderboard rows (team_id, rank, final), only teams with a final rank. Ties at the cut-off all advance."""
    ranked = sorted((r for r in rows if r.get("rank")), key=lambda r: r["rank"])
    if mode == "none":
        return {r["team_id"] for r in ranked}
    if mode == "manual" or not ranked:
        return set()
    if mode == "threshold":
        return {r["team_id"] for r in ranked if (r["final"] or 0) >= float(value)}
    n = int(value) if mode == "top_n" else max(1, math.ceil(len(ranked) * float(value) / 100))
    if n >= len(ranked):
        return {r["team_id"] for r in ranked}
    cut = ranked[n - 1]["final"]
    return {r["team_id"] for r in ranked if r["rank"] <= n or r["final"] == cut}
