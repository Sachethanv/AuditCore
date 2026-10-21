"""Gemini-powered technical review with verifiable evidence.

Design rules:
  * temperature 0 and a fixed rubric, so reruns are as repeatable as an LLM allows;
  * every score must cite evidence (file + quote); citations are checked against the
    real files and unverifiable ones are discarded, so judges can trust what remains;
  * the model sees the static-analysis results as facts and may not contradict them;
  * with no API key, a transparent heuristic fallback is used (clearly labelled).

Uses the Gemini REST API with only the standard library.
"""
from __future__ import annotations

import json
import threading
import os
import re
import time
import urllib.error
import urllib.request

from .ingest import iter_files, read_text

DEFAULT_MODEL = "gemini-2.5-pro"  # override with GEMINI_MODEL
SOURCE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs",
              ".cpp", ".c", ".swift", ".dart", ".vue", ".sol", ".sql")
PRIORITY_HINTS = ("main", "app", "server", "index", "route", "api", "model", "service", "controller", "view", "handler", "agent", "core")
CONFIG_FILES = ("package.json", "requirements.txt", "pyproject.toml", "Dockerfile", "docker-compose.yml")


class LLMError(Exception):
    pass


class QuotaExhausted(LLMError):
    """The key's quota is spent (daily or billing limit). Retrying only wastes time, so we stop and flag the result."""


_quota_dead = [0.0]  # monotonic time until which we skip Gemini entirely after a quota failure


def quota_is_dead() -> bool:
    return time.monotonic() < _quota_dead[0]


def reset_quota() -> None:
    _quota_dead[0] = 0.0


def _retry_delay(body: str) -> float | None:
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
    return float(m.group(1)) if m else None


def _is_hard_quota(body: str) -> bool:
    b = body.lower()
    return any(x in b for x in ("perday", "per day", "daily", "limit: 0", "billing", "check your plan"))


def build_digest(root: str, max_chars: int = 120_000, per_file: int = 9_000) -> tuple[str, list[str]]:
    """Pick the most informative files within a character budget."""
    cands = []
    for rel, ap in iter_files(root):
        base = os.path.basename(rel).lower()
        ext = os.path.splitext(base)[1]
        if rel.lower().endswith(("readme.md", "readme.rst")) or base in CONFIG_FILES:
            prio = 0
        elif ext in SOURCE_EXT:
            prio = 1 if any(h in base for h in PRIORITY_HINTS) else 2
            if re.search(r"(^|/)(tests?|__tests__)/|\.test\.|\.spec\.|test_", rel):
                prio = 3
        else:
            continue
        try:
            cands.append((prio, -os.path.getsize(ap), rel, ap))
        except OSError:
            pass
    cands.sort()
    parts, used, included = [], 0, []
    for _, _, rel, ap in cands:
        text = read_text(ap)
        if not text:
            continue
        snippet = text[:per_file]
        numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(snippet.splitlines(), 1))
        block = f"\n=== FILE: {rel} ({len(text)} chars{', truncated' if len(text) > per_file else ''}) ===\n{numbered}\n"
        if used + len(block) > max_chars:
            continue
        parts.append(block)
        included.append(rel)
        used += len(block)
    return "".join(parts), included


def _prompt(rubric: dict, facts: dict, digest: str) -> str:
    def _line(c):
        a = c.get("anchors") or {}
        anchors = "".join(f"\n    score {k} looks like: {v}" for k, v in sorted(a.items()))
        return f'- {c["id"]} (weight {c["weight"]}): {c["title"]}. {c["description"]}{anchors}'
    crit = "\n".join(_line(c) for c in rubric["criteria"])
    flags = "\n".join(f"- {x}" for x in rubric.get("red_flags_for_judges", []))
    flags_block = f"\nRED FLAGS JUDGES WATCH FOR (mention any you can evidence, and let them lower the relevant scores):\n{flags}\n" if flags else ""
    return f"""You are a meticulous senior engineer helping hackathon judges who cannot read every repository.
Review the repository below. Be skeptical, specific and fair. Do NOT reward pitch/README claims that the code does not back up.
Do not infer cheating or plagiarism; that is handled elsewhere. Do not comment on README or documentation quality; nobody scores it.

ASSUME THE TEAM USED AI CODING TOOLS. Tidy structure, type hints, tests, error handling and comments are what those tools output by
default, so they are the BASELINE and not evidence of skill. Reward only what a tool does not decide for the team: a data model that
fits this problem, component boundaries, a justified choice of storage/queue/cache/API style, failure handling and degradation,
integration of several real parts, and trade-offs. Respect each criterion's baseline/cap in the rubric text.

RUBRIC (score each criterion 0-10 as an integer; 5 = acceptable for a hackathon, 8+ = genuinely strong):
{crit}

{flags_block}
AUTOMATED FACTS (ground truth from static analysis; do not contradict them):
{json.dumps(facts, indent=1, default=str)[:12000]}

REPOSITORY FILES (line numbers prefixed as "N: "):
{digest}

Return ONLY JSON with this exact shape:
{{
 "summary": "3-5 sentence overall assessment for a judge",
 "what_it_does": "1-2 sentences describing what the project actually implements",
 "built_vs_reused": "1-3 sentences: which parts look original vs scaffolding/boilerplate/third-party glue",
 "red_flags": ["each red flag you can evidence, with the file it appears in"],
 "criteria": [{{"id": "<criterion id>", "score": 0-10,
    "reasoning": "2-3 sentences",
    "evidence": [{{"file": "path as shown above", "line": <int>, "quote": "short verbatim snippet (<=120 chars) from that file"}}],
    "gaps": ["specific things that cost points"]}}],
 "design_decisions": [{{"decision": "a specific design choice visible in the code, e.g. 'job queue decouples upload from analysis'",
    "why_it_matters": "1 sentence", "file": "path", "line": <int>, "quote": "short verbatim snippet (<=120 chars)"}}],
 "design_questions": [{{"question": "a pointed system-design question a judge can ask THIS team about THEIR code, e.g. 'Why SQLite and not Postgres for concurrent judges?'",
    "listen_for": "what a team that really made this decision would say", "file": "path the question is about"}}],
 "strengths": ["..."], "weaknesses": ["..."],
 "brittleness_risks": ["concrete ways this would break in a live demo or production"],
 "questions_for_team": ["1-3 non-design questions (scope, data, demo) a judge should ask"],
 "confidence": "low|medium|high"
}}
Give 3-6 design_questions. Every criterion needs at least one evidence item with a verbatim quote. If you cannot find evidence, lower the score and say so."""


_DISCOVERED: dict = {}
_local = threading.local()


_sem: list = []
_rate_lock = threading.Lock()
_rate_last = [0.0]


def _gate():
    """Global Gemini limits so 20 parallel workers do not trigger 429s: GEMINI_CONCURRENCY (default 8) and GEMINI_RPM (default off)."""
    if not _sem:
        _sem.append(threading.BoundedSemaphore(max(1, int(os.environ.get("GEMINI_CONCURRENCY", "8")))))
    rpm = float(os.environ.get("GEMINI_RPM", "0") or 0)
    if rpm > 0:
        with _rate_lock:
            wait = 60.0 / rpm - (time.time() - _rate_last[0])
            if wait > 0:
                time.sleep(wait)
            _rate_last[0] = time.time()
    return _sem[0]


def track_usage(disabled: bool = False) -> dict:
    """Start counting Gemini calls/tokens made by this thread. disabled=True makes review/alignment fall back to the free offline paths."""
    _local.sink = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0}
    _local.disabled = disabled
    return _local.sink


def api_key() -> str | None:
    if getattr(_local, "disabled", False):
        return None
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _record(data: dict):
    sink = getattr(_local, "sink", None)
    if sink is not None:
        um = data.get("usageMetadata") or {}
        sink["calls"] += 1
        sink["prompt_tokens"] += int(um.get("promptTokenCount") or 0)
        sink["output_tokens"] += int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)


def model_for(profile: str | None) -> str | None:
    """'broad' = cheap first pass over everyone (flash); 'deep' = the strongest model, meant for finalists. None = default."""
    if profile == "broad":
        m = os.environ.get("GEMINI_MODEL_BROAD")
        k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return m or (discover_model(k, "flash") if k else None)
    return os.environ.get("GEMINI_MODEL_DEEP") or None
_SKIP = re.compile(r"(preview|exp|tts|image|audio|live|embed|thinking|vision|robotics|computer|lite|latest|learnlm|gemma|aqa|-\d{3}$)", re.I)


def discover_model(api_key: str, prefer: str = "pro") -> str | None:
    """Ask the API which models this key can use; prefer the newest stable model of the wanted kind ('pro' or 'flash')."""
    if (api_key, prefer) in _DISCOVERED:
        return _DISCOVERED[(api_key, prefer)]
    best = None
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            items = json.load(r).get("models", [])
        cands = []
        for m in items:
            name = m.get("name", "").split("/")[-1]
            m_ = re.match(r"gemini-(\d+(?:\.\d+)?)-(pro|flash)(-preview[\w.-]*)?$", name)
            if m_ and "generateContent" in m.get("supportedGenerationMethods", []):
                # newest version first, then stable over preview, then pro over flash
                cands.append((1 if m_.group(2) == prefer else 0, float(m_.group(1)), 0 if m_.group(3) else 1, name))
        if cands:
            best = max(cands)[3]
    except Exception:
        best = None
    _DISCOVERED[(api_key, prefer)] = best
    return best


def _call_gemini(prompt: str, model: str, api_key: str, retries: int = 3, _fallback: bool = True) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode()
    last = ""
    if quota_is_dead():
        raise QuotaExhausted("Gemini quota exhausted (skipping calls for a few minutes; use a new key or wait for the reset)")
    for attempt in range(retries + 2):
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
        try:
            with _gate():
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.load(r)
            _record(data)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            last = f"HTTP {e.code}: {raw[:300]}"
            if e.code == 429 and _is_hard_quota(raw) and _retry_delay(raw) is None or (e.code == 429 and "perday" in raw.lower()):
                _quota_dead[0] = time.monotonic() + 600
                raise QuotaExhausted("Gemini quota exhausted for this key: " + last[:160]) from None
            if e.code == 404 and _fallback:  # model retired or not available to this key: find one that is
                alt = discover_model(api_key)
                if alt and alt != model:
                    return _call_gemini(prompt, alt, api_key, retries, _fallback=False)
            if e.code in (429, 500, 503):
                wait = _retry_delay(raw) if e.code == 429 else None
                time.sleep(min(70.0, (wait + 1) if wait else 2 ** attempt * 3))
                continue
            break
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            last = f"bad response shape: {e}"
        except (urllib.error.URLError, TimeoutError) as e:
            last = f"network: {e}"
            time.sleep(2 ** attempt)
    raise LLMError(last)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_evidence(root: str, review: dict) -> dict:
    """Keep only citations whose file exists and whose quote really appears in it."""
    cache: dict[str, str | None] = {}
    total = kept = 0
    for c in review.get("criteria", []):
        good = []
        for ev in c.get("evidence", []) or []:
            total += 1
            f = str(ev.get("file", "")).lstrip("./")
            path = os.path.join(root, f)
            if f not in cache:
                cache[f] = _norm(read_text(path) or "") if os.path.isfile(path) else None
            body = cache[f]
            q = _norm(str(ev.get("quote", "")))
            if body is not None and q and q in body:
                ev["verified"] = True
                good.append(ev)
                kept += 1
        c["evidence"] = good
        c["score"] = max(0, min(10, int(c.get("score", 0) or 0)))
        if not good:
            c["low_evidence"] = True
    good_dec = []
    for d in review.get("design_decisions", []) or []:
        f = str(d.get("file", "")).lstrip("./")
        path = os.path.join(root, f)
        if f not in cache:
            cache[f] = _norm(read_text(path) or "") if os.path.isfile(path) else None
        q = _norm(str(d.get("quote", "")))
        total += 1
        if cache[f] is not None and q and q in cache[f]:
            d["verified"] = True
            good_dec.append(d)
            kept += 1
    review["design_decisions"] = good_dec
    review["design_questions"] = [q for q in (review.get("design_questions") or []) if isinstance(q, dict) and q.get("question")][:8]
    review["evidence_stats"] = {"cited": total, "verified": kept}
    if total and kept / total < 0.5:
        review["confidence"] = "low"
    return review


def apply_baseline(rubric: dict, review: dict) -> dict:
    """AI tools write tidy code by default, so the reviewer may not score polish (or design without evidence) near the top.
    caps: criterion.baseline_cap, and system_design <= 5 + number of verified design decisions (max 8). Judges are not capped."""
    n = len(review.get("design_decisions") or [])
    notes = []
    for c in review.get("criteria", []):
        spec = next((x for x in rubric["criteria"] if x["id"] == c["id"]), {})
        cap = spec.get("baseline_cap")
        if c["id"] == "system_design":
            cap = min(8, 5 + n) if review.get("mode", "").startswith("gemini") else 5
        if cap is not None and c["score"] > cap:
            c["uncapped_score"], c["score"], c["capped"] = c["score"], cap, True
            notes.append(f'{spec.get("title", c["id"])}: {c["uncapped_score"]} -> {cap}')
    review["baseline_notes"] = notes
    return review


def _generic_design_questions(facts: dict) -> list[dict]:
    """Offline questions built from static facts; a judge can ask these without any LLM."""
    s = facts.get("structure", {})
    langs = list((s.get("languages_loc") or {}).keys())
    qs = [("Walk us through the data flow from user action to stored result. Where could it fail, and what happens then?", "A concrete path with named components, not 'the AI handles it'."),
          ("Which part of this did you design yourselves, and which did your tools scaffold? Show me one decision you changed your mind on.", "A specific change and the reason for it.")]
    if s.get("has_docker"):
        qs.append(("Why containerise this, and what runs where in production?", "A real deployment picture."))
    if len(langs) > 1:
        qs.append((f"You use {', '.join(langs[:3])}. Why that split, and where is the boundary between them?", "A reason tied to the problem."))
    qs.append(("What breaks first if 100 users use it at once?", "Knowledge of the real bottleneck (storage, API limits, one process)."))
    return [{"question": q, "listen_for": l, "file": ""} for q, l in qs]


def heuristic_review(rubric: dict, facts: dict) -> dict:
    """Offline fallback: transparent, rule-based scores from static facts only."""
    s, sec, cm = facts["structure"], facts["security"], facts["commits"]
    hi_sec = sum(1 for f in sec["findings"] if f["severity"] == "high")
    secrets_n = facts["secrets"]["finding_count"]
    tests_ratio = sec["test_files"] / max(sec["code_files"], 1)
    r = s["readme"]
    scores = {
        # Heuristics cannot judge substance, so cap well below "strong".
        "technical_implementation": min(6, 2 + sec["code_loc"] // 800 + (1 if len(s["languages_loc"]) > 1 else 0)),
        "code_quality": 5 + (1 if s["file_count"] > 10 else 0) - min(3, hi_sec),
        "robustness": 2 + min(4, int(tests_ratio * 20)) + (2 if s["has_ci"] else 0),
        "security_config": 8 - min(5, hi_sec) - min(4, secrets_n * 2) - (2 if facts["secrets"]["hygiene"] else 0),
        "completeness": 2 + (2 if r["has_run_instructions"] else 0) + (2 if s["has_lockfile"] else 0) + (1 if s["has_docker"] else 0) + (1 if r["mentions_demo"] else 0),
        "system_design": min(5, 2 + (1 if s["has_docker"] else 0) + (1 if len(s["languages_loc"]) > 1 else 0) + (1 if sec["code_loc"] > 1500 else 0)),
    }
    return {
        "mode": "heuristic (no Gemini key; scores are rough, derived only from static signals)",
        "summary": "Offline heuristic assessment. Set GEMINI_API_KEY for an evidence-backed LLM review.",
        "what_it_does": s["readme"]["present"] and "See README" or "Unknown",
        "criteria": [{"id": c["id"], "score": max(0, min(10, scores.get(c["id"], 5))),
                      "reasoning": "Heuristic from static signals.", "evidence": [], "gaps": [], "low_evidence": True}
                     for c in rubric["criteria"]],
        "strengths": [], "weaknesses": [], "brittleness_risks": [], "questions_for_team": [],
        "design_decisions": [], "design_questions": _generic_design_questions(facts),
        "confidence": "low", "evidence_stats": {"cited": 0, "verified": 0},
    }


def review(root: str, rubric: dict, facts: dict, model: str | None = None) -> dict:
    key = None if model == "offline" else api_key()  # model="offline" = free checkpoint analysis, no Gemini
    if not key:
        return apply_baseline(rubric, heuristic_review(rubric, facts))
    model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    digest, included = build_digest(root)
    compact = {k: v for k, v in facts.items()}
    compact["structure"] = {k: v for k, v in facts["structure"].items() if k != "tree_sample"}
    compact["structure"]["tree_sample"] = facts["structure"]["tree_sample"][:120]
    out = _call_gemini(_prompt(rubric, compact, digest), model, key)
    out = verify_evidence(root, out)
    out["mode"] = f"gemini:{model}"
    out = apply_baseline(rubric, out)
    out["files_reviewed"] = included
    return out


if __name__ == "__main__":  # python -m auditcore.llm  -> list the models your key can use
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not k:
        raise SystemExit("Set GEMINI_API_KEY first.")
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": k})
    for m in json.load(urllib.request.urlopen(req, timeout=30)).get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            print(m["name"].split("/")[-1])
    print("\nAuto-pick:", discover_model(k))
