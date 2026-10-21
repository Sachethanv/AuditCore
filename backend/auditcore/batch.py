"""Batch test: run many repositories through AuditCore and report speed, token cost, stability and whether the scores separate winners.

  python -m auditcore.batch data/ethglobal_tokyo_2026_57.csv --workers 12
  python -m auditcore.batch data/ethglobal_tokyo_2026_57.csv --workers 12 --landscape --repeat 3 --repeat-n 5

CSV columns: repo_url (required), team_name, team_id, and optionally label (prized | none | unclear) and prize.
Writes batch_results.csv and batch_summary.json (no keys or secrets in either). Uses GEMINI_API_KEY / GITHUB_TOKEN when set;
without a Gemini key it runs the free offline review, which tells you the clone/analysis speed but not the AI quality."""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import cli, llm
from .analyzers import landscape
from .paths import RUBRIC_PATH



def _dt(s):
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def run_one(row: dict, rubric: dict, start, end, model, with_landscape: bool) -> dict:
    t0 = time.time()
    usage = llm.track_usage()
    out = {"team_id": row.get("team_id", ""), "team_name": row.get("team_name", ""), "repo_url": row["repo_url"], "label": row.get("label", ""),
           "ok": False, "error": "", "seconds": 0.0}
    try:
        res = cli.evaluate(row["repo_url"], rubric, start, end, model)
        rv = res["review"]
        out.update(ok=True, overall=res["overall_score"], mode=rv["mode"].split(" (")[0].split(" [")[0], confidence=rv.get("confidence"),
                   high_flags=sum(1 for f in res["integrity_flags"] if f["severity"] == "high"),
                   capped=sum(1 for c in rv["criteria"] if c.get("capped")),
                   design_decisions=len(rv.get("design_decisions") or []), design_questions=len(rv.get("design_questions") or []),
                   verified=(rv.get("evidence_stats") or {}).get("verified"), cited=(rv.get("evidence_stats") or {}).get("cited"),
                   commits=(res.get("commits") or {}).get("total_commits"), code_loc=(res.get("security") or {}).get("code_loc"))
        for c in rv["criteria"]:
            out[c["id"]] = c["score"]
        analysis_s = time.time() - t0
        out["analysis_seconds"] = round(analysis_s, 1)
        if with_landscape:
            t1 = time.time()
            ls = landscape.research({"title": row.get("project_title") or row.get("team_name"), "summary": row.get("description"),
                                     "problem": row.get("problem"), "what_it_does": rv.get("what_it_does")},
                                    res["meta"]["repo"])
            out.update(novelty=(ls.get("assessment") or {}).get("novelty", ""), landscape_status=ls["status"],
                       landscape_seconds=round(time.time() - t1, 1), landscape_candidates=len(ls.get("candidates") or []))
    except Exception as e:  # noqa: BLE001 - one bad repo must not stop the batch
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["seconds"] = round(time.time() - t0, 1)
    out["tokens"] = usage["prompt_tokens"] + usage["output_tokens"]
    out["gemini_calls"] = usage["calls"]
    return out


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Chance that a random prized project scores above a random non-prized one (0.5 = no signal, 1.0 = perfect)."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def summarize(results: list[dict], wall: float, workers: int) -> dict:
    ok = [r for r in results if r["ok"]]
    secs = [r["seconds"] for r in ok]
    toks = [r["tokens"] for r in ok]
    s = {"repos": len(results), "succeeded": len(ok), "failed": len(results) - len(ok), "workers": workers,
         "wall_clock_seconds": round(wall, 1), "repos_per_minute": round(len(ok) / wall * 60, 1) if wall else None,
         "seconds_per_repo": {"mean": round(statistics.mean(secs), 1), "median": round(statistics.median(secs), 1), "max": round(max(secs), 1)} if secs else None,
         "tokens": {"total": sum(toks), "mean_per_repo": int(statistics.mean(toks)) if toks else 0, "gemini_calls": sum(r["gemini_calls"] for r in ok)},
         "modes": {m: sum(1 for r in ok if r["mode"] == m) for m in {r["mode"] for r in ok}},
         "errors": sorted({r["error"][:90] for r in results if r["error"]})[:8]}
    for_100 = (statistics.mean(secs) * 100 / workers) if secs else None
    s["projected_minutes_for_100_repos_at_these_workers"] = round(for_100 / 60, 1) if for_100 else None
    crit_ids = [k for k in (ok[0] if ok else {}) if k in ("system_design", "completeness", "code_quality", "robustness", "security_config", "technical_implementation")]
    spread = {}
    for k in crit_ids:
        v = [r[k] for r in ok if k in r]
        if v:
            spread[k] = {"min": min(v), "max": max(v), "mean": round(statistics.mean(v), 2), "stdev": round(statistics.pstdev(v), 2)}
    s["score_spread"] = spread
    if "overall" in (ok[0] if ok else {}):
        v = [r["overall"] for r in ok]
        s["overall"] = {"min": min(v), "max": max(v), "mean": round(statistics.mean(v), 1), "stdev": round(statistics.pstdev(v), 1)}
    lab = [r for r in ok if r.get("label") in ("prized", "none")]
    if lab and any(r["label"] == "prized" for r in lab):
        sep = {}
        for k in ["overall"] + crit_ids:
            pos = [r[k] for r in lab if r["label"] == "prized" and k in r]
            neg = [r[k] for r in lab if r["label"] == "none" and k in r]
            sep[k] = {"auc": auc(pos, neg), "prized_mean": round(statistics.mean(pos), 2) if pos else None, "others_mean": round(statistics.mean(neg), 2) if neg else None}
        s["winner_separation"] = {"prized": sum(1 for r in lab if r["label"] == "prized"), "not_prized": sum(1 for r in lab if r["label"] == "none"), "by_score": sep,
                                  "how_to_read": "AUC 0.5 = scores say nothing about who won, 1.0 = every winner outscored every non-winner. With ~11 winners, anything between 0.4 and 0.6 is noise."}
    nov = [r.get("novelty") for r in ok if r.get("novelty") is not None]
    if nov:
        s["novelty"] = {n: nov.count(n) for n in set(nov)}
        ls = [r["landscape_seconds"] for r in ok if "landscape_seconds" in r]
        s["landscape_seconds_mean"] = round(statistics.mean(ls), 1) if ls else None
    return s


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="auditcore.batch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0, help="only the first N rows (try 5 first)")
    p.add_argument("--model", default=None, help="Gemini model; default = GEMINI_MODEL or auto-discovered")
    p.add_argument("--rubric", default=str(RUBRIC_PATH))
    p.add_argument("--hackathon-start")
    p.add_argument("--hackathon-end")
    p.add_argument("--landscape", action="store_true", help="also run the GitHub prior-art research (uses GITHUB_TOKEN)")
    p.add_argument("--repeat", type=int, default=0, help="re-run the first --repeat-n repos this many extra times to measure score stability")
    p.add_argument("--repeat-n", type=int, default=5)
    p.add_argument("--out", default="batch_results.csv")
    a = p.parse_args(argv)

    rows = [r for r in csv.DictReader(open(a.csv, encoding="utf-8-sig")) if (r.get("repo_url") or "").strip()]
    if a.limit:
        import random
        rows = random.Random(1).sample(rows, min(a.limit, len(rows)))  # mixed sample, the CSV is sorted prized-first
    rubric = json.load(open(a.rubric, encoding="utf-8"))
    start, end = _dt(a.hackathon_start), _dt(a.hackathon_end)
    key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    print(f"{len(rows)} repos, {a.workers} workers, Gemini {'ON' if key else 'OFF (offline estimates only)'}, GitHub token {'set' if os.environ.get('GITHUB_TOKEN') or os.environ.get('GITHUB_TOKENS') else 'not set'}", flush=True)

    results, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(run_one, r, rubric, start, end, a.model, a.landscape): r for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results.append(r)
            print(f"[{i}/{len(rows)}] {r['team_name'][:24]:24} {'OK ' + str(r.get('overall')) if r['ok'] else 'FAIL ' + r['error'][:70]}  {r['seconds']}s  {r['tokens']} tok", flush=True)
    wall = time.time() - t0
    results.sort(key=lambda r: r["team_id"])

    summary = summarize(results, wall, a.workers)
    if a.repeat and a.repeat_n:
        sub = [r for r in rows[:a.repeat_n]]
        runs: dict[str, list[dict]] = {r["repo_url"]: [x for x in results if x["repo_url"] == r["repo_url"] and x["ok"]] for r in sub}
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            for out in pool.map(lambda r: run_one(r, rubric, start, end, a.model, False), [r for r in sub for _ in range(a.repeat)]):
                if out["ok"]:
                    runs[out["repo_url"]].append(out)
        stab = {}
        for url, rs in runs.items():
            if len(rs) > 1:
                stab[url.split("github.com/")[-1]] = {k: (max(x.get(k, 0) for x in rs) - min(x.get(k, 0) for x in rs)) for k in ("overall", "system_design", "completeness") if k in rs[0]}
        worst = max((v.get("system_design", 0) for v in stab.values()), default=0)
        summary["stability"] = {"repos": len(stab), "runs_each": a.repeat + 1, "max_system_design_difference": worst, "per_repo_max_differences": stab,
                                "how_to_read": "Differences of 0-1 on a 0-10 scale are normal LLM noise; 2+ means the review is not repeatable for that repo."}

    fields = []
    for r in results:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    with open("batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\n=== SUMMARY (copy this back to Claude) ===")
    print(json.dumps(summary, indent=2))
    print(f"\nPer-repo rows: {a.out}   Summary: batch_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
