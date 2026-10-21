"""Combine AI scores and judge scorecards into one result per team.

Per criterion: if at least one judge scored it, the value is the mean of the judges'
scores (for AI-assisted criteria the judge starts from the AI score and may override it);
otherwise AI-assisted criteria fall back to the AI score; criteria that only judges can
score stay empty until a judge scores them. The overall score is the weighted mean over
the criteria that have a value, so a partial result is labelled rather than guessed.
"""
from __future__ import annotations

SPREAD_FLAG = 3.0  # judges more than this many points apart on one criterion


def combine(rubric: dict, ai_scores: dict[str, float], judge_rows: list[dict]) -> dict:
    per, num, den = {}, 0.0, 0.0
    max_spread = 0.0
    for c in rubric["criteria"]:
        cid, w = c["id"], float(c.get("weight", 0))
        vals = [r["scores"][cid]["score"] for r in judge_rows if cid in r["scores"] and r["scores"][cid].get("score") is not None]
        if vals:
            v, src = sum(vals) / len(vals), "judges"
            spread = max(vals) - min(vals) if len(vals) > 1 else 0.0
        elif cid in ai_scores and c.get("ai_assisted", True):
            v, src, spread = float(ai_scores[cid]), "ai", 0.0
        else:
            v, src, spread = None, None, 0.0
        max_spread = max(max_spread, spread)
        per[cid] = {"value": round(v, 2) if v is not None else None, "source": src, "judges": len(vals),
                    "spread": round(spread, 2), "weight": w}
        if v is not None and w > 0:
            num += v * w
            den += w
    total_w = sum(float(c.get("weight", 0)) for c in rubric["criteria"]) or 1
    return {"final": round(num / den * 10, 1) if den else None,
            "complete": all(p["value"] is not None for p in per.values()),
            "coverage": round(den / total_w, 3), "judge_count": len(judge_rows),
            "max_spread": round(max_spread, 2), "per_criterion": per}
