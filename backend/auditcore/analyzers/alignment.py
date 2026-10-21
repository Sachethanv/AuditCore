"""Does the pitch match the project? Compares the deck and the team's own description with
the repository and the problem they say they solve.

Output (same shape from Gemini or the offline fallback):
  claims[]              what the deck promises, each marked supported / partial / unsupported /
                        not_checkable, with verbatim code evidence that is verified against the files
  code_gaps_vs_deck[]   promised in the pitch but missing in the code
  undersold_in_code[]   built in the code but absent from the pitch
  problem_fit           does the work address the stated problem?
  problem_gaps[]        what the stated problem needs that neither pitch nor code provides
  alignment_score       deterministic 0-100 from claim coverage + problem fit (not a model guess)
"""
from __future__ import annotations

import json
import os
import re

from .. import deck as deckmod
from .. import llm
from ..ingest import iter_files, read_text

STATUS_VALUE = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
FIT_VALUE = {"addressed": 1.0, "partially": 0.5, "unclear": 0.4, "not_addressed": 0.0}

# (label, regex that finds it in the DECK, regex that finds it in CODE, kind)
KEYWORDS = [
    ("React", r"\breact\b", r"\breact\b|from ['\"]react", "tech"),
    ("Next.js", r"next\.?js", r"\bnext\b.*['\"]|from ['\"]next", "tech"),
    ("Vue", r"\bvue\b", r"\bvue\b", "tech"),
    ("Angular", r"\bangular\b", r"@angular", "tech"),
    ("Flutter", r"\bflutter\b", r"flutter|\.dart\b", "tech"),
    ("React Native", r"react[ -]native", r"react-native", "tech"),
    ("Tailwind", r"tailwind", r"tailwind", "tech"),
    ("Node.js / Express", r"\bexpress\b|node\.?js", r"require\(['\"]express|from ['\"]express|\"express\"", "tech"),
    ("FastAPI", r"fastapi", r"fastapi", "tech"),
    ("Flask", r"\bflask\b", r"\bflask\b", "tech"),
    ("Django", r"django", r"django", "tech"),
    ("Spring", r"spring ?boot|\bspring\b", r"springframework|spring-boot", "tech"),
    ("MongoDB", r"mongo", r"mongo|mongoose|pymongo", "tech"),
    ("PostgreSQL", r"postgres", r"postgres|psycopg|\bpg\b|asyncpg", "tech"),
    ("MySQL", r"mysql", r"mysql", "tech"),
    ("SQLite", r"sqlite", r"sqlite", "tech"),
    ("Firebase", r"firebase|firestore", r"firebase|firestore", "tech"),
    ("Supabase", r"supabase", r"supabase", "tech"),
    ("Redis", r"\bredis\b", r"\bredis\b", "tech"),
    ("GraphQL", r"graphql", r"graphql", "tech"),
    ("Docker", r"docker", r"dockerfile|docker-compose|FROM [a-z]", "tech"),
    ("AWS", r"\baws\b|amazon web", r"boto3|aws-sdk|@aws-sdk|amazonaws", "tech"),
    ("Google Cloud", r"google cloud|\bgcp\b|vertex", r"google\.cloud|@google-cloud|vertexai", "tech"),
    ("Azure", r"\bazure\b", r"azure", "tech"),
    ("OpenAI", r"openai|gpt-?4|chatgpt", r"openai|gpt-", "tech"),
    ("Gemini", r"gemini", r"gemini|google\.generativeai|google-genai|generativelanguage", "tech"),
    ("LangChain", r"langchain", r"langchain", "tech"),
    ("Hugging Face / Transformers", r"hugging ?face|transformers", r"transformers|huggingface", "tech"),
    ("TensorFlow", r"tensorflow", r"tensorflow|keras", "tech"),
    ("PyTorch", r"pytorch|\btorch\b", r"\btorch\b|pytorch", "tech"),
    ("scikit-learn", r"scikit|sklearn", r"sklearn|scikit", "tech"),
    ("OpenCV", r"opencv", r"\bcv2\b|opencv", "tech"),
    ("Stripe / Razorpay", r"stripe|razorpay", r"stripe|razorpay", "tech"),
    ("Twilio / SMS", r"twilio|\bsms\b", r"twilio", "tech"),
    ("Solidity / Web3", r"solidity|web3|smart contract|blockchain", r"solidity|web3|ethers|pragma solidity", "tech"),
    ("WebSockets", r"websocket|socket\.io", r"websocket|socket\.io|socketio", "tech"),
    # features
    ("User login / authentication", r"login|log in|sign ?in|authenticat|\boauth\b|\bsso\b", r"login|signin|authenticat|passport|jwt|oauth|session\[", "feature"),
    ("Payments", r"payment|checkout|billing|subscription", r"stripe|razorpay|paypal|payment|checkout", "feature"),
    ("Real-time updates", r"real[- ]time|live updates|streaming", r"websocket|socket\.io|sse|EventSource|pubsub|onSnapshot", "feature"),
    ("Notifications / email", r"notification|alert(s)?\b|e-?mail", r"notification|sendgrid|smtp|nodemailer|twilio|push", "feature"),
    ("Recommendations", r"recommend", r"recommend|similarity|collaborative", "feature"),
    ("Chatbot / conversational AI", r"chat ?bot|conversational|assistant", r"chat|completion|messages\.create|generate_content", "feature"),
    ("Maps / location", r"\bmap\b|maps\b|location|gps|geo", r"leaflet|mapbox|google\.maps|geolocation|latitude", "feature"),
    ("Machine-learning model", r"machine learning|\bml\b|neural|deep learning|\bmodel\b trained|prediction", r"\.fit\(|train\(|predict\(|\.pkl|\.h5|torch|tensorflow|sklearn", "feature"),
    ("Dashboard / analytics", r"dashboard|analytics|visuali[sz]", r"dashboard|chart|plotly|recharts|matplotlib|d3\b", "feature"),
    ("Mobile app", r"mobile app|android|\bios\b", r"android|flutter|react-native|swift|kotlin", "feature"),
    ("Multi-language / localisation", r"multi-?lingual|regional language|hindi|translat|locali[sz]", r"i18n|translate|locale|gettext|intl", "feature"),
    ("Offline support", r"offline|low connectivity|low[- ]bandwidth", r"serviceworker|service-worker|indexeddb|localstorage|workbox|offline", "feature"),
]
SKIP_EXT = (".md", ".rst", ".txt", ".lock", ".svg", ".json", ".csv")
KEEP_NAMES = ("package.json", "requirements.txt", "pyproject.toml", "pom.xml", "build.gradle", "Dockerfile", "docker-compose.yml", "pubspec.yaml", "go.mod", "Cargo.toml")


def _code_index(root: str, cap_files: int = 900):
    docs = []
    for rel, ap in iter_files(root):
        base = os.path.basename(rel)
        if rel.endswith(SKIP_EXT) and base not in KEEP_NAMES:
            continue
        t = read_text(ap)
        if t:
            docs.append((rel, t[:200_000]))
        if len(docs) >= cap_files:
            break
    return docs


def _find(docs, rx):
    c = re.compile(rx, re.I)
    for rel, text in docs:
        m = c.search(text)
        if m:
            line_no = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[line_no - 1].strip()[:140]
            return {"file": rel, "line": line_no, "quote": line}
    return None


def _slide_of(deck: dict, rx: str):
    c = re.compile(rx, re.I)
    for s in deck.get("slides", []):
        if c.search(s["text"] + " " + s.get("notes", "")):
            return s["n"]
    return None


def heuristic(root: str, ctx: dict) -> dict:
    deck = ctx.get("deck") or {"slides": []}
    pitch = deckmod.as_text(deck, 200_000) + "\n" + (ctx.get("summary") or "") + "\n" + (ctx.get("problem") or "")
    docs = _code_index(root)
    claims, undersold = [], []
    for label, drx, crx, kind in KEYWORDS:
        in_pitch = bool(re.search(drx, pitch, re.I))
        ev = _find(docs, crx)
        if in_pitch:
            claims.append({"claim": f"Pitch mentions {label}", "slide": _slide_of(deck, drx),
                           "status": "supported" if ev else "unsupported",
                           "note": "Found in the code." if ev else "No trace of this in the source files or dependency lists.",
                           "evidence": [ev] if ev else [], "kind": kind})
        elif kind == "feature" and ev and label not in ("Dashboard / analytics",):
            undersold.append(f"{label} appears in the code ({ev['file']}:{ev['line']}) but is not mentioned in the pitch")
    gaps = [f"{c['claim']}: nothing in the code backs this up" for c in claims if c["status"] == "unsupported"]
    return _finish({
        "mode": "heuristic (keyword matching only; add a Gemini key for claim-by-claim checking)",
        "summary": "Offline keyword check of the pitch against the repository. It finds technologies and features the deck names that never appear in the code, but cannot judge whether the problem is actually solved.",
        "claims": claims, "code_gaps_vs_deck": gaps, "undersold_in_code": undersold[:8],
        "problem_fit": {"stated_problem": ctx.get("problem") or "", "verdict": "unclear",
                        "reasoning": "Judging problem fit needs the Gemini review.", "evidence": []},
        "problem_gaps": [], "declared_summary_check": {"verdict": "unclear", "note": ""},
        "questions_for_team": [], "confidence": "low",
    })


def _prompt(ctx: dict, facts: dict, digest: str) -> str:
    return f"""You are a skeptical senior engineer checking whether a hackathon team's PITCH matches their CODE and the PROBLEM they say they solve.
Be fair but strict: a claim is "supported" only when you can quote verbatim code that implements it. README text is not code.

EVENT THEME / PROBLEM STATEMENT (may be empty):
{ctx.get('theme') or '(none given)'}

TEAM'S OWN DESCRIPTION:
Title: {ctx.get('title') or '(none)'}
What it is about: {ctx.get('summary') or '(none)'}
Problem they say they solve: {ctx.get('problem') or '(none)'}

PITCH DECK TEXT:
{deckmod.as_text(ctx.get('deck'), 22000) or '(no deck uploaded)'}

AUTOMATED FACTS (ground truth from static analysis):
{json.dumps(facts, indent=1, default=str)[:6000]}

REPOSITORY FILES (line numbers prefixed as "N: "):
{digest}

Return ONLY JSON of this exact shape:
{{
 "summary": "3-4 sentences: how well do pitch, code and stated problem line up?",
 "declared_summary_check": {{"verdict": "matches|partly|differs", "note": "does the team's own description match what the code does?"}},
 "claims": [{{"claim": "specific thing the pitch promises (max 15 claims, most important first)", "slide": <int or null>,
    "status": "supported|partial|unsupported|not_checkable",
    "note": "1 sentence",
    "evidence": [{{"file": "path as shown", "line": <int>, "quote": "verbatim snippet <=120 chars"}}]}}],
 "code_gaps_vs_deck": ["promised in the pitch but missing/stubbed in the code"],
 "undersold_in_code": ["real functionality in the code that the pitch never mentions"],
 "problem_fit": {{"stated_problem": "the problem in one sentence", "verdict": "addressed|partially|not_addressed|unclear",
    "reasoning": "2-3 sentences", "evidence": [{{"file": "...", "line": 1, "quote": "..."}}]}},
 "problem_gaps": ["what the stated problem needs that neither pitch nor code provides (e.g. its users, data, scale, constraints)"],
 "questions_for_team": ["3-4 pointed questions a judge should ask about the gaps"],
 "confidence": "low|medium|high"
}}
Every "supported" or "partial" claim needs at least one evidence item with a verbatim quote; otherwise use "unsupported" or "not_checkable"."""


def _verify(root: str, al: dict) -> dict:
    cache: dict = {}

    def ok(ev):
        f = str(ev.get("file", "")).lstrip("./")
        p = os.path.join(root, f)
        if f not in cache:
            cache[f] = llm._norm(read_text(p) or "") if os.path.isfile(p) else None
        q = llm._norm(str(ev.get("quote", "")))
        return cache[f] is not None and q and q in cache[f]

    cited = kept = 0
    for c in al.get("claims", []):
        good = []
        for ev in c.get("evidence") or []:
            cited += 1
            if ok(ev):
                ev["verified"] = True
                good.append(ev)
                kept += 1
        c["evidence"] = good
        if c.get("status") in ("supported", "partial") and not good:  # no verified proof -> downgrade
            c["status"] = "not_checkable"
            c["note"] = (c.get("note", "") + " (downgraded: no verifiable code citation)").strip()
    pf = al.get("problem_fit") or {}
    pf["evidence"] = [e for e in (pf.get("evidence") or []) if ok(e)]
    al["problem_fit"] = pf
    al["evidence_stats"] = {"cited": cited, "verified": kept}
    return al


def _finish(al: dict) -> dict:
    claims = [c for c in al.get("claims", []) if c.get("status") in STATUS_VALUE]
    cov = sum(STATUS_VALUE[c["status"]] for c in claims) / len(claims) if claims else None
    fit = FIT_VALUE.get((al.get("problem_fit") or {}).get("verdict", "unclear"), 0.4)
    al["claims_checked"] = len(claims)
    al["claims_supported"] = sum(1 for c in claims if c["status"] == "supported")
    al["claims_unsupported"] = sum(1 for c in claims if c["status"] == "unsupported")
    al["claim_coverage"] = round(cov, 3) if cov is not None else None
    al["alignment_score"] = round(100 * (0.7 * cov + 0.3 * fit)) if cov is not None else None
    return al


def flags(al: dict | None) -> list[dict]:
    if not al:
        return []
    out = []
    n, bad = al.get("claims_checked", 0), al.get("claims_unsupported", 0)
    if n >= 3 and (bad >= 3 or bad / n >= 0.4):
        out.append({"severity": "medium", "code": "deck_claims_unsupported", "source": "deck",
                    "message": f"{bad} of {n} checkable claims in the pitch have no support in the code"})
    verdict = (al.get("problem_fit") or {}).get("verdict")
    if verdict == "not_addressed":
        out.append({"severity": "medium", "code": "problem_not_addressed", "source": "deck",
                    "message": "The code does not appear to address the problem the team says it solves"})
    elif verdict == "partially":
        out.append({"severity": "low", "code": "problem_partly_addressed", "source": "deck",
                    "message": "The code only partly addresses the stated problem"})
    if (al.get("declared_summary_check") or {}).get("verdict") == "differs":
        out.append({"severity": "medium", "code": "description_differs", "source": "deck",
                    "message": "The team's own description differs from what the code actually does"})
    return out


def analyze(root: str, ctx: dict, facts: dict, model: str | None = None) -> dict | None:
    """ctx: {theme, title, summary, problem, deck}. Returns None when there is nothing to compare."""
    if not (ctx.get("deck") or ctx.get("summary") or ctx.get("problem")):
        return None
    key = None if model == "offline" else llm.api_key()
    if not key:
        return heuristic(root, ctx)
    model = model or os.environ.get("GEMINI_MODEL", llm.DEFAULT_MODEL)
    try:
        digest, included = llm.build_digest(root, max_chars=90_000)
        al = llm._call_gemini(_prompt(ctx, facts, digest), model, key)
    except llm.LLMError as e:
        al = heuristic(root, ctx)
        al["mode"] += f" [Gemini error: {str(e)[:100]}]"
        return al
    for k, v in (("claims", []), ("code_gaps_vs_deck", []), ("undersold_in_code", []), ("problem_gaps", []), ("questions_for_team", [])):
        if not isinstance(al.get(k), list):
            al[k] = v
    al = _verify(root, al)
    al["mode"] = f"gemini:{model}"
    return _finish(al)
