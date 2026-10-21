"""HTTP API + dashboard + team submission page + judge portal.

Run: python -m auditcore.server   (or: uvicorn auditcore.server:app)

Three audiences, three kinds of access:
  * organizers  -> /api/...        protected by AUDITCORE_TOKEN (Bearer) when set
  * teams       -> /submit/<token> a private link per team (profile + deck upload + shared feedback)
  * judges      -> /judge/<code>   a private link per judge (scorecards)
Links are unguessable random secrets; treat them like passwords."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets as pysecrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import cli, db, deck as deckmod, deckreview, ingest, llm, rounds as rnds, scoring, teams as teammatch
from .analyzers import fingerprint, landscape
from .db import Ask, Evaluation, Event, Feedback, Judge, JudgeScore, Round, RoundTeam, Team, iso

from .paths import FRONTEND_DIST, LEGACY_WEB, RUBRIC_PATH

DEFAULT_RUBRIC = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
OVERLAP_MIN_SHARE, OVERLAP_MIN_FILES = 0.25, 2
OVERRIDE_NOTE_GAP = 1.0  # a judge must explain a submitted score this far from the AI score


# ---------- request models ----------
class DeckIn(BaseModel):
    filename: str
    data_b64: str


class QuickIn(BaseModel):
    repo_url: str
    hackathon_start: str | None = None
    hackathon_end: str | None = None
    title: str | None = None
    summary: str | None = None
    problem_statement: str | None = None
    deck: DeckIn | None = None


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    hackathon_start: str | None = None
    hackathon_end: str | None = None
    theme: str | None = Field(default=None, max_length=6000)
    tracks: list[str] | None = None
    assignment_mode: str | None = None


class TeamIn(BaseModel):
    code: str | None = None
    name: str = ""
    repo_url: str | None = None
    members: list[dict] = []
    project_title: str | None = None
    summary: str | None = None
    problem_statement: str | None = None
    track: str | None = None
    links: dict | None = None


class TeamPatch(BaseModel):
    name: str | None = None
    repo_url: str | None = None
    members: list[dict] | None = None
    project_title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=8000)
    problem_statement: str | None = Field(default=None, max_length=8000)
    track: str | None = Field(default=None, max_length=80)
    links: dict | None = None


class ProfileIn(BaseModel):
    project_title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=8000)
    problem_statement: str | None = Field(default=None, max_length=8000)
    track: str | None = Field(default=None, max_length=80)
    links: dict | None = None


class ResultsIn(BaseModel):
    public: bool


class JudgePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    tracks: list[str] | None = None
    team_ids: list[int] | None = None


class ImportIn(BaseModel):
    csv: str


class EvalAllIn(BaseModel):
    team_ids: list[int] | None = None
    only_unevaluated: bool = False
    skip_unchanged: bool = False  # reuse the last result for repos whose latest commit has not changed
    profile: str | None = None  # "broad" = cheaper model for everyone; "deep" = strongest model, usually with top_n
    top_n: int | None = Field(default=None, ge=1, le=500)  # with profile "deep": only the current top N ranked teams


class RoundIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    accepts: list[str] = ["summary", "deck", "repo", "demo", "video"]
    checks: dict = {"code": True, "pitch": True, "commits": True, "landscape": True}
    criteria: list[dict] | None = None  # [{id, weight, ai_assisted?}] chosen from the event rubric (or problem_clarity / solution_feasibility)
    advance: dict = {"mode": "none"}
    checkpoint_minutes: int | None = None
    close_profile: str | None = None


class RoundPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    accepts: list[str] | None = None
    checks: dict | None = None
    criteria: list[dict] | None = None
    advance: dict | None = None
    checkpoint_minutes: int | None = None
    close_profile: str | None = None
    announced: bool | None = None
    order: int | None = None


class PresetIn(BaseModel):
    preset: str
    start: str | None = None
    hours: list[float] | None = None
    replace: bool = False


class AdvanceTeamIn(BaseModel):
    advanced: bool


class BudgetIn(BaseModel):
    max_tokens: int | None = Field(default=None, ge=0)  # None/0 = no cap
    explain_enabled: bool | None = None
    explain_llm: bool | None = None
    explain_per_team: int | None = Field(default=None, ge=1, le=50)


class CompareIn(BaseModel):
    other: str = Field(min_length=1, max_length=40)


class FeedbackIn(BaseModel):
    author: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=8000)
    visible_to_team: bool = False


class FeedbackPatch(BaseModel):
    visible_to_team: bool | None = None
    body: str | None = None


class JudgeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tracks: list[str] | None = None
    team_ids: list[int] | None = None


class ScoreIn(BaseModel):
    scores: dict[str, dict] = {}
    comment: str | None = Field(default=None, max_length=6000)
    conflict: bool = False
    status: str = "draft"
    qa: list[dict] | None = None  # design Q&A the judge ran: [{question, rating, note}]


# ---------- helpers ----------
def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Invalid date/time: {s!r} (use ISO format, e.g. 2026-10-10T09:00:00+05:30)")
    return (d.astimezone(timezone.utc) if d.tzinfo else d).replace(tzinfo=None)


def aware(d: datetime | None):
    return d.replace(tzinfo=timezone.utc) if d else None


def parse_members(cell: str) -> list[dict]:
    """'Asha Rao (@asharao); Ravi K (@rk-dev)' -> [{name, github}]. Handles a bare name or @handle too."""
    out = []
    for part in re.split(r"[;\n]", cell or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?)\s*\(\s*@?([\w-]+)\s*\)$", part)
        if m:
            out.append({"name": m.group(1).strip(), "github": m.group(2)})
        elif part.startswith("@"):
            out.append({"name": part[1:], "github": part[1:]})
        else:
            out.append({"name": part})
    return out


def validate_rubric(r: dict) -> dict:
    cs = r.get("criteria") if isinstance(r, dict) else None
    if not isinstance(cs, list) or not cs:
        raise HTTPException(422, "Rubric needs a non-empty 'criteria' list")
    ids = set()
    for c in cs:
        if not isinstance(c, dict) or not c.get("id") or not c.get("title"):
            raise HTTPException(422, "Every criterion needs 'id' and 'title'")
        if c["id"] in ids:
            raise HTTPException(422, f"Duplicate criterion id {c['id']!r}")
        ids.add(c["id"])
        try:
            if float(c.get("weight", 0)) < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(422, f"Criterion {c['id']!r} has an invalid weight")
        c["weight"] = float(c["weight"])
    if not any(c.get("ai_assisted", True) and c["weight"] > 0 for c in cs):
        raise HTTPException(422, "At least one AI-assisted criterion with weight > 0 is required")
    return r


def eval_summary(e: Evaluation) -> dict:
    return {"id": e.id, "repo_url": e.repo_url, "status": e.status, "overall_score": e.overall_score,
            "high_flags": e.high_flags, "mode": e.mode, "created_at": iso(e.created_at),
            "finished_at": iso(e.finished_at), "error": e.error, "team_id": e.team_id, "event_id": e.event_id}


def eval_full(e: Evaluation) -> dict:
    d = eval_summary(e)
    d["result"] = e.result
    return d


LINK_KEYS = ("demo_url", "video_url", "docs_url", "notes")


def clean_links(raw: dict | None, old: dict | None = None) -> dict:
    """Keep only known keys; URLs must be http(s); notes are trimmed."""
    out = dict(old or {})
    for k in LINK_KEYS:
        if raw is None or k not in raw:
            continue
        v = str(raw.get(k) or "").strip()
        if not v:
            out.pop(k, None)
        elif k == "notes":
            out[k] = v[:2000]
        elif re.match(r"^https?://[^\s]+$", v, re.I) and len(v) <= 500:
            out[k] = v
        else:
            raise HTTPException(422, f"{k.replace('_', ' ').capitalize()} must be a link starting with http:// or https://")
    return out


def deck_meta(t: Team) -> dict | None:
    d = t.deck
    if not d:
        return None
    return {"filename": d.get("filename"), "size": d.get("size"), "kind": d.get("kind"),
            "slides": len(d.get("slides", [])), "chars": d.get("chars"), "uploaded_at": d.get("uploaded_at")}


def readiness(t: Team, ev: Event | None = None, accepts: list | None = None) -> dict:
    """accepts: what the open round takes (None = everything). Only items the round asks for are checked."""
    ev = ev or t.event
    L = t.links or {}
    want = lambda k: accepts is None or k in accepts
    checks = [("members", "Team members listed", bool(t.members)),
              ("title", "Project title", bool(t.project_title)),
              ("track", "Track chosen", not (ev.tracks or []) or bool(t.track))]
    if want("repo"):
        checks.insert(0, ("repo", "Repository link", bool(t.repo_url)))
    if want("summary"):
        checks += [("summary", "What the project does (a few sentences)", len(t.summary or "") >= 40),
                   ("problem", "The problem it solves", len(t.problem_statement or "") >= 40)]
    if want("deck"):
        checks.append(("deck", "Pitch deck uploaded", bool(t.deck)))
    if want("demo") or want("video"):
        checks.append(("demo", "Demo link or video", bool(L.get("demo_url") or L.get("video_url"))))
    return {"done": sum(1 for c in checks if c[2]), "total": len(checks),
            "items": [{"id": i, "label": l, "ok": ok} for i, l, ok in checks],
            "missing": [l for _, l, ok in checks if not ok]}


def team_dict(t: Team) -> dict:
    return {"readiness": readiness(t), "landscape": t.landscape, "landscape_tokens": t.landscape_tokens or 0, **_team_dict(t)}


def _team_dict(t: Team) -> dict:
    return {"id": t.id, "event_id": t.event_id, "code": t.code, "name": t.name, "repo_url": t.repo_url,
            "members": t.members or [], "project_title": t.project_title, "summary": t.summary,
            "problem_statement": t.problem_statement, "deck": deck_meta(t),
            "track": t.track, "links": t.links or {},
            "submit_path": f"/submit/{t.submit_token}" if t.submit_token else None}


def context_for(t: Team, ev: Event) -> dict | None:
    if not (t.deck or t.summary or t.problem_statement):
        return None
    return {"theme": ev.theme, "title": t.project_title, "summary": t.summary,
            "problem": t.problem_statement, "deck": t.deck}


def ai_feedback_text(res: dict) -> str:
    rv = res["review"]
    parts = [f"Technical score {res['overall_score']}/100 (review: {rv['mode']}).", rv.get("summary", "")]
    for title, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses"),
                       ("Brittleness risks", "brittleness_risks"), ("Questions for the team", "questions_for_team")):
        if rv.get(key):
            parts.append(title + ":\n" + "\n".join(f"- {x}" for x in rv[key]))
    al = res.get("alignment")
    if al:
        for title, key in (("Promised in the pitch but missing in the code", "code_gaps_vs_deck"),
                           ("Built but missing from the pitch", "undersold_in_code"),
                           ("Gaps against the problem you are solving", "problem_gaps")):
            if al.get(key):
                parts.append(title + ":\n" + "\n".join(f"- {x}" for x in al[key][:8]))
    sec = [f for f in res.get("integrity_flags", []) if f.get("source") == "secrets"]
    if sec:
        parts.append("Configuration issues:\n" + "\n".join(f"- {f['message']}" for f in sec))
    return "\n\n".join(p for p in parts if p)


def judge_view(res: dict) -> dict:
    """What a judge sees of an evaluation: evidence, flags, deck match. Not the raw internals."""
    rv = res["review"]
    cm = res.get("commits", {})
    return {"overall_score": res["overall_score"], "mode": rv["mode"], "confidence": rv["confidence"],
            "summary": rv.get("summary"), "what_it_does": rv.get("what_it_does"), "built_vs_reused": rv.get("built_vs_reused"),
            "strengths": rv.get("strengths", []), "weaknesses": rv.get("weaknesses", []),
            "brittleness_risks": rv.get("brittleness_risks", []), "questions_for_team": rv.get("questions_for_team", []),
            "red_flags": rv.get("red_flags", []), "criteria": rv.get("criteria", []),
            "design_decisions": rv.get("design_decisions", []), "design_questions": rv.get("design_questions", []),
            "baseline_notes": rv.get("baseline_notes", []),
            "integrity_flags": res.get("integrity_flags", []), "pre_event": res.get("pre_event"),
            "alignment": res.get("alignment"), "team_match": res.get("team_match"),
            "commits": {k: cm.get(k) for k in ("total_commits", "distinct_authors", "active_days", "span_days", "largest_commit")},
            "security_findings": (res.get("security") or {}).get("findings", [])[:10]}


# ---------- app ----------
def create_app(db_url: str | None = None, evaluator=None, workers: int | None = None, uploads_dir: str | None = None) -> FastAPI:
    engine = db.make_engine(db_url)
    Session = db.make_sessionmaker(engine)
    run_eval = evaluator or cli.evaluate
    # Three lanes so background work never delays the run that matters: evaluations, cheap checkpoints, prior-art research.
    pool = ThreadPoolExecutor(max_workers=workers or int(os.environ.get("AUDITCORE_WORKERS", "8")))
    cp_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("AUDITCORE_CHECKPOINT_WORKERS", "3")))
    bg_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("AUDITCORE_RESEARCH_WORKERS", "2")))
    token = os.environ.get("AUDITCORE_TOKEN")
    UPLOADS = Path(uploads_dir or os.environ.get("AUDITCORE_UPLOADS") or "uploads")

    with Session() as s:  # jobs cannot survive a restart
        for e in s.scalars(select(Evaluation).where(Evaluation.status.in_(["queued", "running"]))):
            e.status, e.error, e.finished_at = "failed", "Interrupted by server restart", db.now()
        s.commit()

    def auth(request: Request, authorization: str | None = Header(default=None)):
        if token and request.url.path.startswith("/api/health"):
            return  # lets the page learn whether it needs a token
        if token and request.url.path.startswith("/api") and not (
                authorization and pysecrets.compare_digest(authorization, f"Bearer {token}")):
            raise HTTPException(401, "Missing or wrong API token")

    app = FastAPI(title="AuditCore", version="0.3.0", dependencies=[Depends(auth)])
    app.state.pool = pool
    app.state.Session = Session

    def parse_dt_safe(v):
        return aware(parse_dt(v)) if v else None

    def job(eval_id: int):
        with Session() as s:
            e = s.get(Evaluation, eval_id)
            if not e:
                return
            e.status = "running"
            e.params = {**(e.params or {}), "started_at": db.now().isoformat()}
            s.commit()
            params = e.params or {}
            kind = e.kind or "full"
            rubric, members, context = DEFAULT_RUBRIC, None, params.get("context")
            over, rnd = False, None
            if e.event_id:
                ev = s.get(Event, e.event_id)
                rubric = ev.rubric
                over = over_budget(s, ev)
            if e.round_id:
                rnd = s.get(Round, e.round_id)
                if rnd and rnd.rubric:
                    rubric = rnd.rubric
            if e.team_id:
                t = s.get(Team, e.team_id)
                members = t.members or []
                context = context_for(t, s.get(Event, t.event_id))
            checks = (rnd.checks if rnd else None) or {}
            deck_only = bool(rnd and not checks.get("code", True))
            code_context = context if (checks.get("pitch", True) and kind == "full") else None
            run_landscape_after = kind == "full" and checks.get("landscape", True)
            url, e_team_id, round_id = e.repo_url, e.team_id, e.round_id
        # unchanged since the last full run of this round? reuse it instead of re-analysing (the fast path at deadline)
        if params.get("skip_unchanged") and e_team_id and not deck_only and url:
            sha = ingest.head_sha(url)
            with Session() as s:
                prev = s.scalar(select(Evaluation).where(Evaluation.team_id == e_team_id, Evaluation.status == "done", Evaluation.id != eval_id,
                                                         func.coalesce(Evaluation.kind, "full") == "full", Evaluation.round_id == round_id,
                                                         Evaluation.sha == sha).order_by(Evaluation.id.desc())) if sha else None
                if prev:
                    e = s.get(Evaluation, eval_id)
                    e.status, e.finished_at, e.sha, e.tokens = "done", db.now(), sha, 0
                    e.overall_score, e.high_flags, e.fingerprints = prev.overall_score, prev.high_flags, prev.fingerprints
                    e.mode = (prev.mode or "") + " [unchanged since last run]"
                    e.result = prev.result
                    s.commit()
                    return
        usage = llm.track_usage(disabled=over)  # over budget: free offline review instead of Gemini
        model = "offline" if kind == "checkpoint" else params.get("model")
        try:
            if deck_only:
                res = deckreview.review(rubric, context or {}, model)
            else:
                kw = {k: params[k] for k in ("ref", "snapshot") if params.get(k)}
                res = run_eval(url, rubric, parse_dt_safe(params.get("start")), parse_dt_safe(params.get("end")),
                               model, members, code_context, **kw)
        except Exception as ex:  # noqa: BLE001 - surface any failure on the record
            with Session() as s:
                e = s.get(Evaluation, eval_id)
                e.status, e.error, e.finished_at = "failed", f"{type(ex).__name__}: {ex}"[:1000], db.now()
                s.commit()
            return
        with Session() as s:
            e = s.get(Evaluation, eval_id)
            fp = res.pop("fingerprints", None)
            e.status, e.finished_at = "done", db.now()
            e.overall_score = res["overall_score"]
            e.high_flags = sum(1 for f in res["integrity_flags"] if f["severity"] == "high")
            e.mode = res["review"]["mode"] + (" [token budget reached]" if over else "")
            res["usage"] = dict(usage)
            e.tokens = usage["prompt_tokens"] + usage["output_tokens"]
            e.sha = ((res.get("meta") or {}).get("github") or {}).get("evaluated_sha")
            e.result, e.fingerprints = json.loads(json.dumps(res, default=str)), fp
            if e.team_id and kind == "full":  # AI draft feedback goes onto the team, hidden until an organizer shares it
                for old in s.scalars(select(Feedback).where(Feedback.team_id == e.team_id, Feedback.kind == "ai",
                                                            Feedback.visible_to_team.is_(False))):
                    s.delete(old)
                s.add(Feedback(team_id=e.team_id, evaluation_id=e.id, author="AuditCore (AI draft)", kind="ai",
                               body=ai_feedback_text(res), visible_to_team=False))
            s.commit()
        if e_team_id and run_landscape_after and os.environ.get("AUDITCORE_LANDSCAPE", "auto") != "off":
            bg_pool.submit(run_landscape_safe, e_team_id)  # off the critical path: never delays the next evaluation

    def event_tokens(s, ev: Event) -> dict:
        ev_t = s.scalar(select(func.coalesce(func.sum(Evaluation.tokens), 0)).where(Evaluation.event_id == ev.id)) or 0
        ld_t = s.scalar(select(func.coalesce(func.sum(Team.landscape_tokens), 0)).where(Team.event_id == ev.id)) or 0
        ex_t = s.scalar(select(func.coalesce(func.sum(Ask.tokens), 0)).where(Ask.event_id == ev.id)) or 0
        return {"evaluations": int(ev_t), "landscape": int(ld_t), "explanations": int(ex_t), "total": int(ev_t + ld_t + ex_t)}

    def over_budget(s, ev: Event) -> bool:
        cap = (ev.budget or {}).get("max_tokens")
        return bool(cap) and event_tokens(s, ev)["total"] >= cap

    def run_landscape_safe(team_id: int):
        try:
            run_landscape(team_id)
        except Exception:  # noqa: BLE001 - research is a bonus; never fail anything over it
            pass

    def run_landscape(team_id: int, force: bool = False) -> dict | None:
        """Prior-art research for one team, cached on the team. Skipped (not an error) when the token budget is spent."""
        with Session() as s:
            t = s.get(Team, team_id)
            ev = s.get(Event, t.event_id)
            if t.landscape and t.landscape.get("status") == "done" and not force:
                return t.landscape
            e = latest_done(s, t.id)
            rv = (e.result or {}).get("review", {}) if e else {}
            project = {"title": t.project_title, "summary": t.summary, "problem": t.problem_statement,
                       "what_it_does": rv.get("what_it_does") if rv.get("what_it_does") not in (None, "See README", "Unknown") else None,
                       "theme": ev.theme}
            if not any(project[k] for k in ("title", "summary", "problem", "what_it_does")):
                return None
            own = t.repo_key
            skip_llm = over_budget(s, ev)
        usage = llm.track_usage(disabled=skip_llm)
        res = landscape.research(project, own, model=None)
        with Session() as s:
            t = s.get(Team, team_id)
            t.landscape = res
            t.landscape_tokens = (t.landscape_tokens or 0) + res["usage"]["prompt_tokens"] + res["usage"]["output_tokens"]
            s.commit()
        return res

    def enqueue(s, repo_url: str | None, event_id=None, team_id=None, start=None, end=None, context=None, model=None,
                round_id=None, kind="full", snapshot=False, skip_unchanged=False) -> Evaluation:
        extra = {"context": context, "model": model, "snapshot": snapshot or None, "skip_unchanged": skip_unchanged or None}
        e = Evaluation(repo_url=repo_url or "", event_id=event_id, team_id=team_id, round_id=round_id, kind=kind,
                       params={"start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
                               **{k: v for k, v in extra.items() if v}})
        s.add(e)
        s.commit()
        (cp_pool if kind == "checkpoint" else pool).submit(job, e.id)
        return e

    def get_or_404(s, model, id_, what):
        o = s.get(model, id_)
        if not o:
            raise HTTPException(404, f"{what} {id_} not found")
        return o

    def new_token(n: int = 12) -> str:
        return pysecrets.token_urlsafe(n)

    def ensure_token(t: Team):
        if not t.submit_token:
            t.submit_token = new_token(12)

    def store_deck(s, t: Team, filename: str, data_b64: str):
        try:
            name, raw = deckmod.decode_upload(filename, data_b64)
            d = deckmod.parse(name, raw)
        except deckmod.DeckError as ex:
            raise HTTPException(422, str(ex))
        ext = ".pdf" if d["kind"] == "pdf" else ".pptx"
        rel = f"event{t.event_id}/team{t.id}-{d['sha1'][:10]}{ext}"
        path = UPLOADS / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        d["path"], d["uploaded_at"] = rel, iso(db.now())
        t.deck = d
        t.profile_updated_at = db.now()
        s.commit()
        return d

    def _full():
        return func.coalesce(Evaluation.kind, "full") != "checkpoint"

    def latest_done(s, team_id: int, round_id: int | None = None) -> Evaluation | None:
        q = select(Evaluation).where(Evaluation.team_id == team_id, Evaluation.status == "done", _full())
        if round_id is not None:
            q = q.where(Evaluation.round_id == round_id)
        return s.scalar(q.order_by(Evaluation.id.desc()))

    def latest_any(s, team_id: int, round_id: int | None = None) -> Evaluation | None:
        q = select(Evaluation).where(Evaluation.team_id == team_id, _full())
        if round_id is not None:
            q = q.where(Evaluation.round_id == round_id)
        return s.scalar(q.order_by(Evaluation.id.desc()))

    def busy(s, team_id: int) -> bool:
        return bool(s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.team_id == team_id, Evaluation.status.in_(["queued", "running"]))))

    # --- rounds ---
    def event_rounds(s, evid: int) -> list[Round]:
        return list(s.scalars(select(Round).where(Round.event_id == evid).order_by(Round.order, Round.id)))

    def focus_round(s, ev: Event) -> Round | None:
        """The round people are working in now: the latest that has started (or the first, before anything starts)."""
        rs = event_rounds(s, ev.id)
        if not rs:
            return None
        now = db.now()
        started = [r for r in rs if rnds.state(r, now) != "upcoming"]
        return started[-1] if started else rs[0]

    def resolve_round(s, ev: Event, round_id: int | None) -> Round | None:
        if round_id is None:
            return focus_round(s, ev)
        r = s.get(Round, round_id)
        if not r or r.event_id != ev.id:
            raise HTTPException(404, "Round not found")
        return r

    def round_team_ids(s, ev: Event, rnd: Round) -> set[int]:
        rs = event_rounds(s, ev.id)
        idx = [r.id for r in rs].index(rnd.id)
        if idx == 0:
            return {t.id for t in s.scalars(select(Team).where(Team.event_id == ev.id))}
        prev = rs[idx - 1]
        return {x.team_id for x in s.scalars(select(RoundTeam).where(RoundTeam.round_id == prev.id, RoundTeam.advanced.is_(True)))}

    def rubric_of(ev: Event, rnd: Round | None) -> dict:
        return rnd.rubric if (rnd and rnd.rubric) else ev.rubric

    def eval_for_judge(s, t: Team, rnd: Round | None) -> Evaluation | None:
        """Evidence a judge sees: this round's evaluation, else the most recent earlier one (a judges-only final reuses it)."""
        e = latest_done(s, t.id, rnd.id if rnd else None)
        return e or (latest_done(s, t.id) if rnd else None)

    # --- meta ---
    @app.get("/api/health")
    def health():
        return {"ok": True, "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
                "auth_required": bool(token), "workers": pool._max_workers}

    @app.get("/api/rubric/default")
    def default_rubric():
        return DEFAULT_RUBRIC

    # --- quick (single) evaluations ---
    @app.post("/api/evaluations", status_code=202)
    def quick_eval(body: QuickIn):
        url = teammatch.canonical_url(body.repo_url)
        if not url:
            raise HTTPException(422, "Enter a GitHub repository URL like https://github.com/owner/repo")
        ctx = None
        d = None
        if body.deck:
            try:
                name, raw = deckmod.decode_upload(body.deck.filename, body.deck.data_b64)
                d = deckmod.parse(name, raw)
            except deckmod.DeckError as ex:
                raise HTTPException(422, str(ex))
        if d or (body.summary or "").strip() or (body.problem_statement or "").strip():
            ctx = {"theme": None, "title": body.title, "summary": (body.summary or "").strip() or None,
                   "problem": (body.problem_statement or "").strip() or None, "deck": d}
        with Session() as s:
            e = enqueue(s, url, start=parse_dt(body.hackathon_start), end=parse_dt(body.hackathon_end), context=ctx)
            return eval_summary(e)

    @app.get("/api/evaluations")
    def list_quick(limit: int = 50):
        with Session() as s:
            rows = s.scalars(select(Evaluation).where(Evaluation.team_id.is_(None)).order_by(Evaluation.id.desc()).limit(min(limit, 200)))
            return [eval_summary(e) for e in rows]

    @app.get("/api/evaluations/{eid}")
    def get_eval(eid: int):
        with Session() as s:
            return eval_full(get_or_404(s, Evaluation, eid, "Evaluation"))

    # --- events ---
    def budget_of(ev: Event) -> dict:
        b = ev.budget or {}
        return {"max_tokens": b.get("max_tokens") or None, "explain_enabled": bool(b.get("explain_enabled")),
                "explain_llm": bool(b.get("explain_llm")), "explain_per_team": int(b.get("explain_per_team") or 5)}

    def event_dict(s, ev: Event) -> dict:
        return {"id": ev.id, "name": ev.name, "hackathon_start": iso(ev.hackathon_start),
                "hackathon_end": iso(ev.hackathon_end), "team_count": len(ev.teams), "rubric": ev.rubric,
                "theme": ev.theme, "created_at": iso(ev.created_at), "tracks": ev.tracks or [],
                "assignment_mode": ev.assignment_mode or "all", "results_public": bool(ev.results_public),
                "results_path": f"/results/{ev.public_token}" if ev.public_token else None,
                "budget": budget_of(ev), "round_count": len(event_rounds(s, ev.id))}

    def clean_tracks(v):
        seen, out = set(), []
        for x in v or []:
            x = str(x).strip()[:80]
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
        return out

    def check_mode(m):
        if m in (None, ""):
            return "all"
        if m not in ("all", "track", "custom"):
            raise HTTPException(422, "assignment_mode must be all, track or custom")
        return m

    @app.put("/api/events/{evid}/results")
    def set_results(evid: int, body: ResultsIn):
        """Reveal or hide the public ranking page."""
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            if not ev.public_token:
                ev.public_token = new_token(12)
            ev.results_public = body.public
            s.commit()
            return event_dict(s, ev)

    @app.post("/api/events", status_code=201)
    def create_event(body: EventIn):
        with Session() as s:
            ev = Event(name=body.name.strip(), hackathon_start=parse_dt(body.hackathon_start),
                       hackathon_end=parse_dt(body.hackathon_end), theme=(body.theme or "").strip() or None,
                       rubric=json.loads(json.dumps(DEFAULT_RUBRIC)),
                       tracks=clean_tracks(body.tracks), assignment_mode=check_mode(body.assignment_mode),
                       public_token=new_token(12))
            s.add(ev)
            s.commit()
            return event_dict(s, ev)

    @app.get("/api/events")
    def list_events():
        with Session() as s:
            return [{k: v for k, v in event_dict(s, e).items() if k != "rubric"}
                    for e in s.scalars(select(Event).order_by(Event.id.desc()))]

    @app.get("/api/events/{evid}")
    def get_event(evid: int):
        with Session() as s:
            return event_dict(s, get_or_404(s, Event, evid, "Event"))

    @app.patch("/api/events/{evid}")
    def patch_event(evid: int, body: EventIn):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            ev.name, ev.hackathon_start, ev.hackathon_end = body.name.strip(), parse_dt(body.hackathon_start), parse_dt(body.hackathon_end)
            ev.theme = (body.theme or "").strip() or None
            if body.tracks is not None:
                ev.tracks = clean_tracks(body.tracks)
            if body.assignment_mode is not None:
                ev.assignment_mode = check_mode(body.assignment_mode)
            s.commit()
            return event_dict(s, ev)

    @app.delete("/api/events/{evid}", status_code=204)
    def delete_event(evid: int):
        with Session() as s:
            s.delete(get_or_404(s, Event, evid, "Event"))
            s.commit()

    @app.put("/api/events/{evid}/rubric")
    def put_rubric(evid: int, rubric: dict):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            ev.rubric = validate_rubric(rubric)
            s.commit()
            return ev.rubric

    # --- teams ---
    def next_code(s, evid: int) -> str:
        used = {t.code for t in s.scalars(select(Team).where(Team.event_id == evid))}
        i = len(used) + 1
        while f"T-{i:03d}" in used:
            i += 1
        return f"T-{i:03d}"

    def apply_repo(t: Team, url: str | None):
        if url and url.strip():
            c = teammatch.canonical_url(url.strip())
            if not c:
                raise HTTPException(422, f"Not a valid GitHub repository URL: {url!r}")
            t.repo_url, t.repo_key = c, teammatch.normalize_repo(c)
        else:
            t.repo_url = t.repo_key = None

    def check_track(ev: Event, track: str | None):
        track = (track or "").strip()[:80] or None
        if track and (ev.tracks or []) and track.lower() not in [x.lower() for x in ev.tracks]:
            raise HTTPException(422, f"Track {track!r} is not one of this event's tracks: {', '.join(ev.tracks)}")
        if track and ev.tracks:
            track = next(x for x in ev.tracks if x.lower() == track.lower())
        return track

    @app.post("/api/events/{evid}/teams", status_code=201)
    def add_team(evid: int, body: TeamIn):
        with Session() as s:
            get_or_404(s, Event, evid, "Event")
            code = (body.code or "").strip() or next_code(s, evid)
            if s.scalar(select(Team).where(Team.event_id == evid, Team.code == code)):
                raise HTTPException(409, f"Team ID {code!r} already exists in this event")
            t = Team(event_id=evid, code=code, name=body.name.strip(), members=body.members,
                     project_title=(body.project_title or "").strip() or None,
                     summary=(body.summary or "").strip() or None,
                     problem_statement=(body.problem_statement or "").strip() or None,
                     track=check_track(s.get(Event, evid), body.track), links=clean_links(body.links))
            ensure_token(t)
            apply_repo(t, body.repo_url)
            s.add(t)
            s.commit()
            return team_dict(t)

    @app.post("/api/events/{evid}/teams/import")
    def import_teams(evid: int, body: ImportIn):
        """CSV columns (header row, any order): team_id, team_name, repo_url, members,
        and optionally project_title, description, problem. Existing team IDs are updated."""
        with Session() as s:
            get_or_404(s, Event, evid, "Event")
            rd = csv.DictReader(io.StringIO(body.csv.strip()))
            if not rd.fieldnames:
                raise HTTPException(422, "CSV is empty")
            norm = {f: re.sub(r"[^a-z]", "", f.lower()) for f in rd.fieldnames}
            pick = lambda row, *names: next((row[f].strip() for f, n in norm.items() if n in names and row.get(f)), "")
            created = updated = 0
            problems = []
            for i, row in enumerate(rd, 2):
                code = pick(row, "teamid", "id", "code", "teamcode")
                name = pick(row, "teamname", "name", "team")
                url = pick(row, "repourl", "repo", "repository", "github", "githuburl")
                members = parse_members(pick(row, "members", "member", "participants"))
                title = pick(row, "projecttitle", "project", "title", "projectname")
                summ = pick(row, "description", "summary", "about", "projectdescription")
                prob = pick(row, "problem", "problemstatement")
                trk = pick(row, "track", "category", "domain")
                lk = {"demo_url": pick(row, "demourl", "demo", "liveurl", "live", "website"),
                      "video_url": pick(row, "videourl", "video", "demovideo"),
                      "docs_url": pick(row, "docsurl", "docs", "documentation")}
                if not (code or name or url):
                    continue
                code = code or next_code(s, evid)
                t = s.scalar(select(Team).where(Team.event_id == evid, Team.code == code))
                if t:
                    updated += 1
                else:
                    t = Team(event_id=evid, code=code)
                    s.add(t)
                    created += 1
                t.name = name or t.name or ""
                if members:
                    t.members = members
                if title:
                    t.project_title = title[:200]
                if summ:
                    t.summary = summ
                if prob:
                    t.problem_statement = prob
                ensure_token(t)
                try:
                    if trk:
                        t.track = check_track(s.get(Event, evid), trk)
                    if any(lk.values()):
                        t.links = clean_links({k: v for k, v in lk.items() if v}, t.links)
                    apply_repo(t, url)
                except HTTPException as ex:  # keep the team so it can be fixed in the dashboard
                    problems.append({"row": i, "team_id": code, "problem": ex.detail})
                s.flush()
            s.commit()
            return {"created": created, "updated": updated, "problems": problems}

    @app.patch("/api/teams/{tid}")
    def patch_team(tid: int, body: TeamPatch):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            if body.name is not None:
                t.name = body.name.strip()
            if body.members is not None:
                t.members = body.members
            if body.repo_url is not None:
                apply_repo(t, body.repo_url)
            for f in ("project_title", "summary", "problem_statement"):
                v = getattr(body, f)
                if v is not None:
                    setattr(t, f, v.strip() or None)
                    t.profile_updated_at = db.now()
            if body.track is not None:
                t.track = check_track(s.get(Event, t.event_id), body.track)
            if body.links is not None:
                t.links = clean_links(body.links, t.links)
            ensure_token(t)
            s.commit()
            return team_dict(t)

    @app.delete("/api/teams/{tid}", status_code=204)
    def delete_team(tid: int):
        with Session() as s:
            s.delete(get_or_404(s, Team, tid, "Team"))
            s.commit()

    @app.post("/api/teams/{tid}/deck")
    def upload_deck_admin(tid: int, body: DeckIn):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            store_deck(s, t, body.filename, body.data_b64)
            return deck_meta(t)

    @app.delete("/api/teams/{tid}/deck", status_code=204)
    def delete_deck(tid: int):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            t.deck = None
            s.commit()

    @app.get("/api/teams/{tid}/deck/file")
    def download_deck(tid: int):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            p = (UPLOADS / t.deck["path"]) if t.deck and t.deck.get("path") else None
            if not p or not p.is_file():
                raise HTTPException(404, "No deck file stored for this team")
            return FileResponse(p, filename=t.deck.get("filename") or p.name)

    def event_overlaps(s, evid: int) -> tuple[dict, dict]:
        """(per-team notes, pair list) for shared repos, copied files and late submissions."""
        ev = s.get(Event, evid)
        ts = list(s.scalars(select(Team).where(Team.event_id == evid)))
        per: dict[int, list] = {t.id: [] for t in ts}
        pairs = []
        by_key: dict[str, list[Team]] = {}
        for t in ts:
            if t.repo_key:
                by_key.setdefault(t.repo_key, []).append(t)
        for key, group in by_key.items():
            if len(group) > 1:
                for t in group:
                    others = [o.code for o in group if o.id != t.id]
                    per[t.id].append({"severity": "high", "code": "repo_shared_by_teams",
                                      "message": f"The same repository is registered by teams {', '.join(others)}"})
        if ev.hackathon_end:
            for t in ts:
                up = (t.deck or {}).get("uploaded_at")
                if up and parse_dt(up) > ev.hackathon_end:
                    per[t.id].append({"severity": "medium", "code": "deck_after_deadline",
                                      "message": f"The deck was uploaded after the deadline ({up[:16].replace('T', ' ')} UTC)"})
        fps = {}
        for t in ts:
            e = latest_done(s, t.id)
            if e and e.fingerprints and e.repo_url:
                fps[t.id] = e.fingerprints
        ids = sorted(fps)
        by_id = {t.id: t for t in ts}
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if by_id[a].repo_key and by_id[a].repo_key == by_id[b].repo_key:
                    continue
                c = fingerprint.compare(fps[a], fps[b])
                if c["share_of_smaller"] >= OVERLAP_MIN_SHARE and c["shared_files"] >= OVERLAP_MIN_FILES:
                    sev = "high" if c["share_of_smaller"] >= 0.5 else "medium"
                    pairs.append({"a": by_id[a].code, "b": by_id[b].code, **c, "severity": sev})
                    for me, other in ((a, b), (b, a)):
                        per[me].append({"severity": sev, "code": "code_overlap",
                                        "message": f"{c['share_of_smaller']:.0%} of code (by lines, {c['shared_files']} files) is identical to team {by_id[other].code}"})
        return per, pairs

    @app.get("/api/events/{evid}/teams")
    def list_teams(evid: int):
        with Session() as s:
            get_or_404(s, Event, evid, "Event")
            per, _ = event_overlaps(s, evid)
            fr = focus_round(s, get_or_404(s, Event, evid, "Event"))
            out = []
            changed = False
            for t in s.scalars(select(Team).where(Team.event_id == evid).order_by(Team.code)):
                if not t.submit_token:  # teams created before submission links existed
                    ensure_token(t)
                    changed = True
                e = (latest_any(s, t.id, fr.id) if fr else None) or latest_any(s, t.id)
                d = team_dict(t)
                d["latest"] = eval_summary(e) if e else None
                d["cross_flags"] = per.get(t.id, [])
                out.append(d)
            if changed:
                s.commit()
            return out

    def judged(s, ev: Event, rnd: Round | None = None) -> dict[int, list[dict]]:
        rows: dict[int, list[dict]] = {}
        names = {j.id: j.name for j in s.scalars(select(Judge).where(Judge.event_id == ev.id))}
        rid = JudgeScore.round_id == rnd.id if rnd else JudgeScore.round_id.is_(None)
        for js in s.scalars(select(JudgeScore).where(JudgeScore.event_id == ev.id, JudgeScore.status == "submitted", rid)):
            if js.conflict:
                continue
            rows.setdefault(js.team_id, []).append({"judge_id": js.judge_id, "judge": names.get(js.judge_id, "?"),
                                                    "scores": js.scores or {}, "comment": js.comment})
        return rows

    def leaderboard_rows(s, ev: Event, rnd: Round | None = None):
        per, pairs = event_overlaps(s, ev.id)
        jr = judged(s, ev, rnd)
        rubric = rubric_of(ev, rnd)
        in_round = round_team_ids(s, ev, rnd) if rnd else None
        decided = {x.team_id: x for x in s.scalars(select(RoundTeam).where(RoundTeam.round_id == rnd.id))} if rnd else {}
        rows = []
        for t in s.scalars(select(Team).where(Team.event_id == ev.id)):
            if in_round is not None and t.id not in in_round:
                continue
            e = latest_done(s, t.id, rnd.id if rnd else None)
            res = e.result if e else None
            flags = (res or {}).get("integrity_flags", []) + per.get(t.id, [])
            crit = {c["id"]: c["score"] for c in (res or {}).get("review", {}).get("criteria", [])}
            comb = scoring.combine(rubric, crit, jr.get(t.id, []))
            pre = (res or {}).get("pre_event") or None
            al = (res or {}).get("alignment") or None
            newer = bool(e and t.deck and (t.deck.get("uploaded_at") or "") > iso(e.finished_at or e.created_at))
            rows.append({"team_id": t.id, "code": t.code, "name": t.name, "title": t.project_title, "repo_url": t.repo_url,
                         "track": t.track, "links": t.links or {},
                         "advanced": (decided[t.id].advanced if t.id in decided else None),
                         "score": e.overall_score if e else None, "evaluation_id": e.id if e else None,
                         "mode": e.mode if e else None, "final": comb["final"] if (e or jr.get(t.id)) else None,
                         "complete": comb["complete"], "judge_count": comb["judge_count"], "max_spread": comb["max_spread"],
                         "per_criterion": comb["per_criterion"],
                         "high_flags": sum(1 for f in flags if f["severity"] == "high"),
                         "medium_flags": sum(1 for f in flags if f["severity"] == "medium"),
                         "top_flag": next((f["message"] for f in sorted(flags, key=lambda f: {"high": 0, "medium": 1, "low": 2}.get(f["severity"], 3))
                                           if f["severity"] in ("high", "medium")), None),
                         "pre_event": pre, "has_deck": bool(t.deck), "deck_slides": len((t.deck or {}).get("slides", [])),
                         "deck_newer_than_eval": newer,
                         "alignment_score": al.get("alignment_score") if al else None,
                         "claims_unsupported": al.get("claims_unsupported") if al else None,
                         "criteria": crit})
        # Ranking rule: once any team has a complete scorecard, only complete teams are ranked (an AI-only
        # partial score is not comparable with a fully judged one). Before any judging, rank provisionally.
        any_complete = any(r["complete"] and r["final"] is not None for r in rows)
        eligible = lambda r: r["final"] is not None and (r["complete"] or not any_complete)
        rows.sort(key=lambda r: (not eligible(r), r["final"] is None, -(r["final"] or 0), r["code"]))
        rank = 0
        for r in rows:
            r["provisional"] = not any_complete
            if eligible(r):
                rank += 1
                r["rank"] = rank
        return rows, pairs, not any_complete

    @app.get("/api/events/{evid}/leaderboard")
    def leaderboard(evid: int, round_id: int | None = Query(None, alias="round")):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            rnd = resolve_round(s, ev, round_id)
            rows, pairs, provisional = leaderboard_rows(s, ev, rnd)
            rub = rubric_of(ev, rnd)
            return {"event": {"id": ev.id, "name": ev.name, "start_set": bool(ev.hackathon_start)}, "rows": rows,
                    "round": round_dict(s, ev, rnd) if rnd else None, "rounds": [round_dict(s, ev, r) for r in event_rounds(s, ev.id)],
                    "provisional": provisional,
                    "overlaps": pairs, "judges": s.query(Judge).filter(Judge.event_id == ev.id).count(),
                    "tracks": ev.tracks or [], "results_public": bool(ev.results_public),
                    "human_criteria": [c for c in rub["criteria"] if not c.get("ai_assisted", True)],
                    "ai_criteria": [c for c in rub["criteria"] if c.get("ai_assisted", True)]}

    @app.get("/api/events/{evid}/results.csv")
    def results_csv(evid: int, round_id: int | None = Query(None, alias="round")):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            rnd = resolve_round(s, ev, round_id)
            rows, _, _ = leaderboard_rows(s, ev, rnd)
            cr = rubric_of(ev, rnd)["criteria"]
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["rank", "team_id", "team_name", "project", "repo_url", "overall", "complete", "judges", "ai_technical",
                        *[c["title"] for c in cr], "high_flags", "commits_before_start", "share_of_code_before_start", "pitch_alignment", "track"])
            for r in rows:
                pre = r["pre_event"] or {}
                w.writerow([r.get("rank", ""), r["code"], r["name"], r["title"] or "", r["repo_url"] or "", r["final"] if r["final"] is not None else "",
                            "yes" if r["complete"] else "no", r["judge_count"], r["score"] if r["score"] is not None else "",
                            *[(r["per_criterion"].get(c["id"], {}).get("value") if r["per_criterion"].get(c["id"], {}).get("value") is not None else "") for c in cr],
                            r["high_flags"], pre.get("commits_before", "") if pre.get("start_set") else "",
                            f"{pre['share_lines']:.0%}" if pre.get("start_set") else "", r["alignment_score"] if r["alignment_score"] is not None else "",
                            r.get("track") or ""])
            return Response(buf.getvalue(), media_type="text/csv",
                            headers={"Content-Disposition": f"attachment; filename=event-{evid}-results.csv"})

    def queue_round_eval(s, ev: Event, rnd: Round | None, team_ids=None, model=None, snapshot=False, skip_unchanged=False,
                         only_unevaluated=False):
        """Queue full evaluations for the teams of a round (or of the whole event when it has no rounds)."""
        started, skipped = [], []
        rubric = rubric_of(ev, rnd)
        deck_only = bool(rnd and not (rnd.checks or {}).get("code", True))
        if not any(c.get("ai_assisted", True) for c in rubric["criteria"]):
            return started, [{"team": "*", "reason": "this round is scored by judges only; nothing for the AI to do"}]
        in_round = round_team_ids(s, ev, rnd) if rnd else None
        end = aware(rnd.ends_at) if (rnd and rnd.ends_at) else aware(ev.hackathon_end)
        for t in s.scalars(select(Team).where(Team.event_id == ev.id).order_by(Team.code)):
            if in_round is not None and t.id not in in_round:
                continue
            if team_ids is not None and t.id not in team_ids:
                continue
            if not t.repo_url and not deck_only:
                skipped.append({"team": t.code, "reason": "no repository URL"})
                continue
            if deck_only and not (t.deck or t.summary or t.problem_statement):
                skipped.append({"team": t.code, "reason": "nothing submitted yet"})
                continue
            if busy(s, t.id):
                skipped.append({"team": t.code, "reason": "already running"})
                continue
            if only_unevaluated and latest_done(s, t.id, rnd.id if rnd else None):
                continue
            enqueue(s, t.repo_url, ev.id, t.id, aware(ev.hackathon_start), end, model=model, round_id=rnd.id if rnd else None,
                    snapshot=snapshot, skip_unchanged=skip_unchanged)
            started.append(t.code)
        return started, skipped

    @app.post("/api/events/{evid}/evaluate", status_code=202)
    def evaluate_event(evid: int, body: EvalAllIn = EvalAllIn(), round_id: int | None = Query(None, alias="round")):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            if body.profile not in (None, "", "broad", "deep"):
                raise HTTPException(422, "profile must be broad or deep")
            rnd = resolve_round(s, ev, round_id)
            model = llm.model_for(body.profile) if body.profile else None
            only_ids = body.team_ids
            if body.profile == "deep" and body.top_n:
                rows, _, _ = leaderboard_rows(s, ev, rnd)
                top = [r["team_id"] for r in rows if r.get("rank")][:body.top_n]
                only_ids = top if only_ids is None else [i for i in only_ids if i in top]
            started, skipped = queue_round_eval(s, ev, rnd, only_ids, model, False, body.skip_unchanged, body.only_unevaluated)
            return {"started": started, "skipped": skipped, "profile": body.profile, "model": model, "round": rnd.name if rnd else None}

    @app.post("/api/teams/{tid}/evaluate", status_code=202)
    def evaluate_team(tid: int):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            ev = s.get(Event, t.event_id)
            rnd = focus_round(s, ev)
            deck_only = bool(rnd and not (rnd.checks or {}).get("code", True))
            if not t.repo_url and not deck_only:
                raise HTTPException(422, "This team has no repository URL yet")
            if busy(s, t.id):
                raise HTTPException(409, "An evaluation is already running for this team")
            end = aware(rnd.ends_at) if (rnd and rnd.ends_at) else aware(ev.hackathon_end)
            return eval_summary(enqueue(s, t.repo_url, ev.id, t.id, aware(ev.hackathon_start), end, round_id=rnd.id if rnd else None))

    @app.get("/api/teams/{tid}")
    def team_detail(tid: int):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            if not t.submit_token:
                ensure_token(t)
                s.commit()
            per, _ = event_overlaps(s, t.event_id)
            fr = focus_round(s, t.event)
            e = (latest_done(s, t.id, fr.id) if fr else None) or latest_done(s, t.id)
            hist = s.scalars(select(Evaluation).where(Evaluation.team_id == t.id, _full()).order_by(Evaluation.id.desc()).limit(20))
            fb = s.scalars(select(Feedback).where(Feedback.team_id == t.id).order_by(Feedback.id.desc()))
            judges = {j.id: j.name for j in s.scalars(select(Judge).where(Judge.event_id == t.event_id))}
            jscores = [{"round_id": j.round_id, "judge": judges.get(j.judge_id, "?"), "status": j.status, "conflict": j.conflict, "scores": j.scores, "qa": j.qa or [],
                        "comment": j.comment, "updated_at": iso(j.updated_at)}
                       for j in s.scalars(select(JudgeScore).where(JudgeScore.team_id == t.id))]
            la = latest_any(s, t.id)
            rts = {x.round_id: x for x in s.scalars(select(RoundTeam).where(RoundTeam.team_id == t.id))}
            rlist = []
            for r in event_rounds(s, t.event_id):
                x = rts.get(r.id)
                rlist.append({"id": r.id, "name": r.name, "state": rnds.state(r, db.now()), "in_round": t.id in round_team_ids(s, t.event, r),
                              "advanced": x.advanced if x else None, "rank": x.rank if x else None, "final": x.final if x else None,
                              "decided_by": x.decided_by if x else None, "snapshot": x.snapshot if x else None})
            d = team_dict(t)
            d.update({"rounds": rlist, "focus_round": fr.id if fr else None, "evaluation": eval_full(e) if e else None, "history": [eval_summary(x) for x in hist],
                      "cross_flags": per.get(t.id, []), "running": busy(s, t.id),
                      "deck_slides": (t.deck or {}).get("slides", []),
                      "deck_newer_than_eval": bool(e and t.deck and (t.deck.get("uploaded_at") or "") > iso(e.finished_at or e.created_at)),
                      "judge_scores": jscores,
                      "feedback": [{"id": f.id, "author": f.author, "kind": f.kind, "body": f.body,
                                    "visible_to_team": f.visible_to_team, "created_at": iso(f.created_at),
                                    "evaluation_id": f.evaluation_id} for f in fb],
                      "event": {"id": t.event.id, "name": t.event.name, "theme": t.event.theme,
                                "rubric": t.event.rubric}})
            return d

    # --- rounds ---
    def parse_when(v, field):
        if v in (None, ""):
            return None
        d = parse_dt(v)
        if d is None:
            raise HTTPException(422, f"{field} is not a valid date/time")
        return d

    def round_dict(s, ev: Event, r: Round) -> dict:
        now = db.now()
        rub = rubric_of(ev, r)
        ids = round_team_ids(s, ev, r)
        done = s.scalar(select(func.count(func.distinct(Evaluation.team_id))).where(Evaluation.round_id == r.id, Evaluation.status == "done", _full())) or 0
        return {"id": r.id, "event_id": r.event_id, "order": r.order, "name": r.name, "description": r.description,
                "state": rnds.state(r, now), "starts_at": iso(r.starts_at), "ends_at": iso(r.ends_at), "opened_at": iso(r.opened_at),
                "closed_at": iso(r.closed_at), "advanced_at": iso(r.advanced_at), "announced": bool(r.announced),
                "accepts": r.accepts or [], "checks": r.checks or {}, "custom_rubric": bool(r.rubric),
                "criteria": [{"id": c["id"], "title": c["title"], "weight": c.get("weight"), "ai_assisted": c.get("ai_assisted", True)} for c in rub["criteria"]],
                "advance": r.advance or {"mode": "none"}, "checkpoint_minutes": r.checkpoint_minutes, "close_profile": r.close_profile,
                "team_count": len(ids), "evaluated": done,
                "advanced_count": s.scalar(select(func.count()).select_from(RoundTeam).where(RoundTeam.round_id == r.id, RoundTeam.advanced.is_(True))) or 0}

    def build_rubric(ev: Event, criteria: list[dict]) -> dict:
        spec = []
        for c in criteria:
            if "id" not in c:
                raise HTTPException(422, "Each criterion needs an id")
            try:
                spec.append((c["id"], float(c.get("weight", 10))))
            except (TypeError, ValueError):
                raise HTTPException(422, "Criterion weight must be a number")
        try:
            rub = rnds.derive_rubric(ev.rubric, spec, judge_only=tuple(c["id"] for c in criteria if c.get("ai_assisted") is False))
        except KeyError as ex:
            raise HTTPException(422, f"Unknown criterion {ex}. Use ids from the event rubric, problem_clarity or solution_feasibility")
        for c, orig in zip(rub["criteria"], criteria):
            if orig.get("ai_assisted") is True:
                c["ai_assisted"] = True
        return validate_rubric(rub)

    def apply_round(ev: Event, r: Round, data: dict, criteria=None):
        try:
            clean = rnds.clean_round(data)
        except ValueError as ex:
            raise HTTPException(422, str(ex))
        for k, v in clean.items():
            setattr(r, k, v)
        if criteria is not None:
            r.rubric = build_rubric(ev, criteria) if criteria else None

    @app.get("/api/rounds/presets")
    def round_presets():
        return [{"id": k, "description": v, "rounds": [{"name": x["name"], "accepts": x["accepts"], "advance": x["advance"],
                                                         "checkpoint_minutes": x["checkpoint_minutes"]} for x in rnds.preset(k, DEFAULT_RUBRIC)]}
                for k, v in rnds.PRESET_INFO.items()]

    @app.get("/api/events/{evid}/rounds")
    def list_rounds(evid: int):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            return [round_dict(s, ev, r) for r in event_rounds(s, evid)]

    @app.post("/api/events/{evid}/rounds", status_code=201)
    def add_round(evid: int, body: RoundIn):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            r = Round(event_id=evid, order=len(event_rounds(s, evid)) + 1, name="x", accepts=[], checks={}, advance={})
            apply_round(ev, r, {"name": body.name, "description": body.description, "accepts": body.accepts, "checks": body.checks, "advance": body.advance,
                                "checkpoint_minutes": body.checkpoint_minutes, "close_profile": body.close_profile}, body.criteria)
            r.starts_at, r.ends_at = parse_when(body.starts_at, "starts_at"), parse_when(body.ends_at, "ends_at")
            if r.starts_at and r.ends_at and r.ends_at <= r.starts_at:
                raise HTTPException(422, "A round must end after it starts")
            s.add(r)
            s.commit()
            return round_dict(s, ev, r)

    @app.post("/api/events/{evid}/rounds/preset", status_code=201)
    def add_preset(evid: int, body: PresetIn):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            have = event_rounds(s, evid)
            if have and not body.replace:
                raise HTTPException(409, "This event already has rounds. Pass replace=true to start over (their results are kept only for events without evaluations).")
            if have:
                if s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.round_id.in_([r.id for r in have]))):
                    raise HTTPException(409, "Rounds that already have evaluations cannot be replaced. Edit them instead.")
                for r in have:
                    s.delete(r)
                s.flush()
            try:
                specs = rnds.preset(body.preset, ev.rubric, parse_when(body.start, "start"), body.hours)
            except ValueError as ex:
                raise HTTPException(422, str(ex))
            for i, sp in enumerate(specs, 1):
                s.add(Round(event_id=evid, order=i, name=sp["name"], description=sp.get("description"), starts_at=sp.get("starts_at"), ends_at=sp.get("ends_at"),
                            accepts=sp["accepts"], checks=sp["checks"], rubric=sp["rubric"], advance=sp["advance"],
                            checkpoint_minutes=sp["checkpoint_minutes"], close_profile=sp["close_profile"]))
            s.commit()
            return [round_dict(s, ev, r) for r in event_rounds(s, evid)]

    @app.patch("/api/rounds/{rid}")
    def patch_round(rid: int, body: RoundPatch):
        with Session() as s:
            r = get_or_404(s, Round, rid, "Round")
            ev = s.get(Event, r.event_id)
            d = body.model_dump(exclude_unset=True)
            criteria = d.pop("criteria", None) if "criteria" in d else None
            for k in ("starts_at", "ends_at"):
                if k in d:
                    setattr(r, k, parse_when(d.pop(k), k))
            if "announced" in d:
                r.announced = bool(d.pop("announced"))
            if "order" in d:
                r.order = int(d.pop("order"))
            apply_round(ev, r, d, criteria if "criteria" in body.model_dump(exclude_unset=True) else None)
            if r.starts_at and r.ends_at and r.ends_at <= r.starts_at:
                raise HTTPException(422, "A round must end after it starts")
            if r.closed_at and r.ends_at and r.ends_at > db.now():
                r.closed_at = None  # extended after closing: reopen it
            s.commit()
            return round_dict(s, ev, r)

    @app.delete("/api/rounds/{rid}", status_code=204)
    def delete_round(rid: int):
        with Session() as s:
            r = get_or_404(s, Round, rid, "Round")
            if s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.round_id == rid)) or \
               s.scalar(select(func.count()).select_from(JudgeScore).where(JudgeScore.round_id == rid)):
                raise HTTPException(409, "This round already has evaluations or scores and cannot be deleted")
            s.delete(r)
            s.commit()

    @app.post("/api/rounds/{rid}/open")
    def open_round(rid: int):
        with Session() as s:
            r = get_or_404(s, Round, rid, "Round")
            r.opened_at, r.closed_at = db.now(), None
            if r.ends_at and r.ends_at <= db.now():
                raise HTTPException(409, "The end time has already passed. Move ends_at first.")
            s.commit()
            return round_dict(s, s.get(Event, r.event_id), r)

    def close_round(rid: int) -> dict:
        """Close: freeze what each team submitted, then run the full evaluation on the repo as of the deadline."""
        with Session() as s:
            r = s.get(Round, rid)
            ev = s.get(Event, r.event_id)
            if not r.closed_at:
                r.closed_at = min(db.now(), r.ends_at) if r.ends_at else db.now()
                r.opened_at = r.opened_at or r.closed_at
            ids = round_team_ids(s, ev, r)
            for t in s.scalars(select(Team).where(Team.event_id == ev.id)):
                if t.id not in ids:
                    continue
                rt = s.scalar(select(RoundTeam).where(RoundTeam.round_id == r.id, RoundTeam.team_id == t.id)) or RoundTeam(round_id=r.id, team_id=t.id)
                if not rt.snapshot:
                    rt.snapshot = {"deck": deck_meta(t), "links": t.links or {}, "repo_url": t.repo_url, "at": iso(db.now()),
                                   "profile": {"title": t.project_title, "summary": t.summary, "problem": t.problem_statement}}
                s.add(rt)
            s.commit()
            model = llm.model_for(r.close_profile) if r.close_profile else None
            started, skipped = queue_round_eval(s, ev, r, None, model, snapshot=bool(r.ends_at), skip_unchanged=True)
            return {"round": round_dict(s, ev, r), "started": started, "skipped": skipped}

    @app.post("/api/rounds/{rid}/close")
    def close_round_ep(rid: int):
        with Session() as s:
            get_or_404(s, Round, rid, "Round")
        return close_round(rid)

    def advance_round(rid: int, force: bool = False) -> dict:
        with Session() as s:
            r = s.get(Round, rid)
            ev = s.get(Event, r.event_id)
            pending = s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.round_id == rid, Evaluation.status.in_(["queued", "running"])))
            if pending and not force:
                raise HTTPException(409, f"{pending} evaluation(s) are still running. Wait for them, or pass force=true.")
            rows, _, provisional = leaderboard_rows(s, ev, r)
            adv = r.advance or {"mode": "none"}
            chosen = rnds.select_advancing(rows, adv.get("mode", "none"), adv.get("value"))
            by = {x.team_id: x for x in s.scalars(select(RoundTeam).where(RoundTeam.round_id == rid))}
            for row in rows:
                rt = by.get(row["team_id"]) or RoundTeam(round_id=rid, team_id=row["team_id"])
                if rt.decided_by != "organizer":  # a hand decision is never overwritten by the rule
                    rt.advanced, rt.decided_by = row["team_id"] in chosen, "rule"
                rt.final, rt.rank, rt.decided_at = row["final"], row.get("rank"), db.now()
                s.add(rt)
            r.advanced_at = db.now()
            s.commit()
            out = [{"team_id": x.team_id, "advanced": x.advanced, "decided_by": x.decided_by, "rank": x.rank, "final": x.final}
                   for x in s.scalars(select(RoundTeam).where(RoundTeam.round_id == rid))]
            return {"round": round_dict(s, ev, r), "provisional": provisional, "teams": out,
                    "note": "Ranked on AI scores only (no complete judge scorecards yet)." if provisional else None}

    @app.post("/api/rounds/{rid}/advance")
    def advance_round_ep(rid: int, force: bool = False):
        with Session() as s:
            get_or_404(s, Round, rid, "Round")
        return advance_round(rid, force)

    @app.patch("/api/rounds/{rid}/teams/{tid}")
    def decide_team(rid: int, tid: int, body: AdvanceTeamIn):
        with Session() as s:
            r = get_or_404(s, Round, rid, "Round")
            t = get_or_404(s, Team, tid, "Team")
            if t.event_id != r.event_id:
                raise HTTPException(404, "Team not found")
            rt = s.scalar(select(RoundTeam).where(RoundTeam.round_id == rid, RoundTeam.team_id == tid)) or RoundTeam(round_id=rid, team_id=tid)
            rt.advanced, rt.decided_by, rt.decided_at = body.advanced, "organizer", db.now()
            s.add(rt)
            s.commit()
            return {"team_id": tid, "advanced": rt.advanced, "decided_by": "organizer"}

    @app.get("/api/events/{evid}/progress")
    def progress(evid: int, round_id: int | None = Query(None, alias="round")):
        """Live status of an evaluation run: counts, and an ETA from how long recent runs took."""
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            rnd = resolve_round(s, ev, round_id)
            ids = round_team_ids(s, ev, rnd) if rnd else {t.id for t in s.scalars(select(Team).where(Team.event_id == evid))}
            counts = {"queued": 0, "running": 0, "done": 0, "failed": 0, "not_started": 0}
            for tid in ids:
                e = latest_any(s, tid, rnd.id if rnd else None)
                counts[e.status if e else "not_started"] += 1
            recent = [e for e in s.scalars(select(Evaluation).where(Evaluation.event_id == evid, Evaluation.status == "done", _full())
                                           .order_by(Evaluation.id.desc()).limit(30))
                      if (e.params or {}).get("started_at") and e.finished_at and "[unchanged" not in (e.mode or "")]
            durs = []
            for e in recent:
                try:
                    durs.append((e.finished_at - datetime.fromisoformat(e.params["started_at"])).total_seconds())
                except Exception:  # noqa: BLE001
                    pass
            avg = sum(durs) / len(durs) if durs else None
            left = counts["queued"] + counts["running"]
            workers_n = pool._max_workers
            return {"round": rnd.name if rnd else None, "teams": len(ids), **counts, "workers": workers_n,
                    "avg_seconds_per_run": round(avg, 1) if avg else None,
                    "eta_seconds": int(left / workers_n * avg + (avg or 0) * (1 if left else 0) / 2) if avg and left else (0 if not left else None)}

    @app.get("/api/teams/{tid}/checkpoints")
    def team_checkpoints(tid: int, round_id: int | None = Query(None, alias="round")):
        """The cheap automatic analyses run while a round was open: what changed in the repo over time."""
        with Session() as s:
            get_or_404(s, Team, tid, "Team")
            q = select(Evaluation).where(Evaluation.team_id == tid, Evaluation.status == "done", Evaluation.sha.is_not(None))
            if round_id is not None:
                q = q.where(Evaluation.round_id == round_id)
            out = []
            for e in s.scalars(q.order_by(Evaluation.id.desc()).limit(60)):
                r = e.result or {}
                out.append({"id": e.id, "at": iso(e.finished_at), "kind": e.kind or "full", "sha": e.sha, "round_id": e.round_id,
                            "score": e.overall_score, "high_flags": e.high_flags,
                            "commits": (r.get("commits") or {}).get("total_commits"), "lines": (r.get("security") or {}).get("code_loc"),
                            "flags": [f["message"] for f in (r.get("integrity_flags") or []) if f.get("severity") in ("high", "medium")][:3]})
            return list(reversed(out))

    # --- scheduler: opens/closes rounds, polls for new commits, advances ---
    def run_checkpoints(rid: int) -> int:
        with Session() as s:
            r = s.get(Round, rid)
            ev = s.get(Event, r.event_id)
            r.last_checkpoint_at = db.now()
            if not (r.checks or {}).get("code", True):
                s.commit()
                return 0
            ids = round_team_ids(s, ev, r)
            targets = [(t.id, t.repo_url) for t in s.scalars(select(Team).where(Team.event_id == ev.id)) if t.id in ids and t.repo_url]
            s.commit()

        def probe(team_id: int, url: str):
            sha = ingest.head_sha(url)
            if not sha:
                return
            with Session() as s:
                if s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.team_id == team_id, Evaluation.sha == sha, Evaluation.status == "done")):
                    return  # nothing new since the last analysis
                if busy(s, team_id):
                    return
                r = s.get(Round, rid)
                ev = s.get(Event, r.event_id)
                enqueue(s, url, ev.id, team_id, aware(ev.hackathon_start), aware(r.ends_at) if r.ends_at else aware(ev.hackathon_end), round_id=rid, kind="checkpoint")
        for tid_, url in targets:
            cp_pool.submit(probe, tid_, url)
        return len(targets)

    def tick(now: datetime | None = None) -> dict:
        now = now or db.now()
        due_close, due_cp, due_adv, opened = [], [], [], []
        with Session() as s:
            for r in s.scalars(select(Round)):
                st = rnds.state(r, now)
                if st == "open" and not r.opened_at:
                    r.opened_at = r.starts_at or now
                    opened.append(r.id)
                if st == "closed" and not r.closed_at:
                    due_close.append(r.id)
                elif st == "open" and r.checkpoint_minutes and (not r.last_checkpoint_at or now - r.last_checkpoint_at >= timedelta(minutes=r.checkpoint_minutes)):
                    due_cp.append(r.id)
                elif r.closed_at and not r.advanced_at and (r.advance or {}).get("auto") and (r.advance or {}).get("mode") not in (None, "manual"):
                    idle = not s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.round_id == r.id, Evaluation.status.in_(["queued", "running"])))
                    done = s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.round_id == r.id, Evaluation.status == "done"))
                    if idle and done:
                        due_adv.append(r.id)
            s.commit()
        out = {"opened": opened, "closed": [], "checkpoints": {}, "advanced": []}
        for rid in due_close:
            out["closed"].append({"round": rid, **{k: v for k, v in close_round(rid).items() if k != "round"}})
        for rid in due_cp:
            out["checkpoints"][rid] = run_checkpoints(rid)
        for rid in due_adv:
            try:
                advance_round(rid)
                out["advanced"].append(rid)
            except HTTPException:
                pass
        return out
    app.state.tick = tick

    if os.environ.get("AUDITCORE_SCHEDULER", "on") != "off":
        import threading

        def loop():
            while True:
                time.sleep(float(os.environ.get("AUDITCORE_TICK_SECONDS", "30")))
                try:
                    tick()
                except Exception:  # noqa: BLE001 - a bad tick must not kill the scheduler
                    pass
        threading.Thread(target=loop, daemon=True, name="auditcore-scheduler").start()

    # --- landscape research (prior art) ---
    @app.post("/api/teams/{tid}/landscape")
    def team_landscape(tid: int, refresh: bool = False):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
        res = run_landscape(tid, force=refresh)
        if res is None:
            raise HTTPException(422, "Add a project title, summary or problem statement (or run an evaluation) before researching the landscape")
        return res

    @app.post("/api/events/{evid}/landscape", status_code=202)
    def event_landscape(evid: int, only_missing: bool = True):
        """Research every team in the background (one worker); results are cached on each team."""
        with Session() as s:
            get_or_404(s, Event, evid, "Event")
            ids = [t.id for t in s.scalars(select(Team).where(Team.event_id == evid).order_by(Team.code))
                   if not (only_missing and t.landscape and t.landscape.get("status") == "done")]

        def run_all():
            for i in ids:
                try:
                    run_landscape(i)
                except Exception:  # noqa: BLE001
                    pass
        pool.submit(run_all)
        return {"queued": len(ids)}

    # --- token budget ---
    @app.put("/api/events/{evid}/budget")
    def put_budget(evid: int, body: BudgetIn):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            b = dict(ev.budget or {})
            for k, v in body.model_dump(exclude_unset=True).items():
                b[k] = v
            ev.budget = b
            s.commit()
            return budget_of(ev)

    @app.get("/api/events/{evid}/usage")
    def event_usage(evid: int):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            used = event_tokens(s, ev)
            n = s.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.event_id == evid, Evaluation.status == "done", Evaluation.tokens > 0)) or 0
            cap = budget_of(ev)["max_tokens"]
            return {**used, "cap": cap, "over_budget": bool(cap) and used["total"] >= cap,
                    "remaining": max(0, cap - used["total"]) if cap else None,
                    "avg_per_evaluation": int(used["evaluations"] / n) if n else None, "evaluations_with_ai": n,
                    "note": "Counts Gemini prompt + output tokens only. When the cap is reached new evaluations use the free offline review."}

    @app.post("/api/teams/{tid}/feedback", status_code=201)
    def add_feedback(tid: int, body: FeedbackIn):
        with Session() as s:
            t = get_or_404(s, Team, tid, "Team")
            e = latest_done(s, t.id)
            f = Feedback(team_id=t.id, evaluation_id=e.id if e else None, author=body.author.strip(), kind="human",
                         body=body.body.strip(), visible_to_team=body.visible_to_team)
            s.add(f)
            s.commit()
            return {"id": f.id}

    @app.patch("/api/feedback/{fid}")
    def patch_feedback(fid: int, body: FeedbackPatch):
        with Session() as s:
            f = get_or_404(s, Feedback, fid, "Feedback")
            if body.visible_to_team is not None:
                f.visible_to_team = body.visible_to_team
            if body.body is not None:
                f.body = body.body
            s.commit()
            return {"id": f.id, "visible_to_team": f.visible_to_team}

    @app.delete("/api/feedback/{fid}", status_code=204)
    def del_feedback(fid: int):
        with Session() as s:
            s.delete(get_or_404(s, Feedback, fid, "Feedback"))
            s.commit()

    @app.get("/api/events/{evid}/feedback.csv")
    def feedback_export(evid: int):
        """Feedback marked 'share with team', one row per team, ready for a mail merge."""
        with Session() as s:
            get_or_404(s, Event, evid, "Event")
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["team_id", "team_name", "repo_url", "technical_score", "feedback"])
            for t in s.scalars(select(Team).where(Team.event_id == evid).order_by(Team.code)):
                shared = [f for f in t.feedback if f.visible_to_team]
                if not shared:
                    continue
                e = latest_done(s, t.id)
                w.writerow([t.code, t.name, t.repo_url or "", e.overall_score if e else "",
                            "\n\n".join(f"[{f.author}] {f.body}" for f in sorted(shared, key=lambda f: f.id))])
            return Response(buf.getvalue(), media_type="text/csv",
                            headers={"Content-Disposition": f"attachment; filename=event-{evid}-feedback.csv"})

    def assigned_teams(ev: Event, j: Judge) -> list[Team]:
        """Which teams this judge scores, according to the event's assignment mode."""
        ts = sorted(ev.teams, key=lambda t: t.code)
        mode = ev.assignment_mode or "all"
        if mode == "track":
            mine = {x.lower() for x in (j.tracks or [])}
            return [t for t in ts if (t.track or "").lower() in mine] if mine else []
        if mode == "custom":
            ids = set(j.team_ids or [])
            return [t for t in ts if t.id in ids]
        return ts

    # --- judges (organizer side) ---
    @app.post("/api/events/{evid}/judges", status_code=201)
    def add_judge(evid: int, body: JudgeIn):
        with Session() as s:
            get_or_404(s, Event, evid, "Event")
            j = Judge(event_id=evid, name=body.name.strip(), code=new_token(12),
                      tracks=clean_tracks(body.tracks), team_ids=[int(x) for x in (body.team_ids or [])])
            s.add(j)
            s.commit()
            return {"id": j.id, "name": j.name, "path": f"/judge/{j.code}", "tracks": j.tracks or [], "team_ids": j.team_ids or []}

    @app.patch("/api/judges/{jid}")
    def patch_judge(jid: int, body: JudgePatch):
        with Session() as s:
            j = get_or_404(s, Judge, jid, "Judge")
            if body.name is not None and body.name.strip():
                j.name = body.name.strip()
            if body.tracks is not None:
                j.tracks = clean_tracks(body.tracks)
            if body.team_ids is not None:
                j.team_ids = [int(x) for x in body.team_ids]
            s.commit()
            return {"id": j.id, "name": j.name, "tracks": j.tracks or [], "team_ids": j.team_ids or []}

    @app.get("/api/events/{evid}/judges")
    def list_judges(evid: int):
        with Session() as s:
            ev = get_or_404(s, Event, evid, "Event")
            out = []
            for j in s.scalars(select(Judge).where(Judge.event_id == evid).order_by(Judge.id)):
                sc = list(s.scalars(select(JudgeScore).where(JudgeScore.judge_id == j.id)))
                out.append({"id": j.id, "name": j.name, "path": f"/judge/{j.code}",
                            "tracks": j.tracks or [], "team_ids": j.team_ids or [],
                            "submitted": sum(1 for x in sc if x.status == "submitted" and not x.conflict),
                            "recused": sum(1 for x in sc if x.conflict), "drafts": sum(1 for x in sc if x.status == "draft"),
                            "teams": len(assigned_teams(ev, j))})
            return out

    @app.delete("/api/judges/{jid}", status_code=204)
    def delete_judge(jid: int):
        with Session() as s:
            s.delete(get_or_404(s, Judge, jid, "Judge"))
            s.commit()

    # --- public: team submission page (private link) ---
    def team_by_token(s, tok: str) -> Team:
        t = s.scalar(select(Team).where(Team.submit_token == tok)) if tok and len(tok) >= 8 else None
        if not t:
            raise HTTPException(404, "This link is not valid. Ask the organizers for your team's link.")
        return t

    def open_round_for(s, t: Team) -> tuple[Round | None, dict | None]:
        """(round the team can submit to now, info). Events without rounds always accept submissions."""
        rs = event_rounds(s, t.event_id)
        if not rs:
            return None, None
        now = db.now()
        for r in rs:
            if rnds.state(r, now) == "open" and t.id in round_team_ids(s, t.event, r):
                return r, {"id": r.id, "name": r.name, "description": r.description, "ends_at": iso(r.ends_at), "accepts": r.accepts or [], "state": "open"}
        nxt = next((r for r in rs if rnds.state(r, now) == "upcoming"), None)
        mine_next = [r for r in rs if rnds.state(r, now) == "upcoming" and t.id in round_team_ids(s, t.event, r)]
        out_of_play = bool(rs) and all(rnds.state(r, now) == "closed" for r in rs[:1]) and not mine_next
        return None, {"state": "none", "next": ({"name": nxt.name, "starts_at": iso(nxt.starts_at)} if nxt else None),
                      "message": ("Submissions are closed right now." if not nxt else f"Submissions are closed. Next: {nxt.name}" + (f" opens {iso(nxt.starts_at)}." if nxt.starts_at else "."))}

    def gate(s, t: Team, item: str):
        """Refuse a submission the open round does not take, or when no round is open."""
        r, info = open_round_for(s, t)
        if info is None:
            return
        if r is None:
            raise HTTPException(409, info["message"])
        if item not in (r.accepts or []):
            raise HTTPException(409, f"{r.name} does not accept this ({item}).")

    def team_public(s, t: Team) -> dict:
        ev = t.event
        orr, rinfo = open_round_for(s, t)
        standing = []
        for r in event_rounds(s, ev.id):
            if r.announced:
                x = s.scalar(select(RoundTeam).where(RoundTeam.round_id == r.id, RoundTeam.team_id == t.id))
                standing.append({"name": r.name, "advanced": x.advanced if x else None})
        return {**_team_public_core(s, t, ev, orr), "round": rinfo, "rounds_standing": standing,
                "submissions_open": rinfo is None or rinfo.get("state") == "open"}

    def _team_public_core(s, t: Team, ev: Event, orr: Round | None) -> dict:
        return {"team": {"code": t.code, "name": t.name, "repo_url": t.repo_url,
                         "members": [m.get("name") for m in (t.members or [])]},
                "event": {"name": ev.name, "theme": ev.theme, "starts_at": iso(ev.hackathon_start),
                          "ends_at": iso(ev.hackathon_end), "tracks": ev.tracks or []},
                "profile": {"project_title": t.project_title, "summary": t.summary, "problem_statement": t.problem_statement,
                            "track": t.track, "links": t.links or {}},
                "deck": deck_meta(t), "readiness": readiness(t, ev, orr.accepts if orr else None),
                "results_revealed": bool(ev.results_public),
                "after_deadline": bool(ev.hackathon_end and db.now() > ev.hackathon_end),
                "feedback": [{"author": f.author, "body": f.body, "created_at": iso(f.created_at)}
                             for f in sorted(t.feedback, key=lambda f: -f.id) if f.visible_to_team]}

    @app.get("/public/submit/{tok}")
    def pub_team(tok: str):
        with Session() as s:
            return team_public(s, team_by_token(s, tok))

    @app.put("/public/submit/{tok}/profile")
    def pub_profile(tok: str, body: ProfileIn):
        with Session() as s:
            t = team_by_token(s, tok)
            if (body.links or {}).get("demo_url") or (body.links or {}).get("video_url"):
                gate(s, t, "demo")
            if any(getattr(body, f) is not None for f in ("project_title", "summary", "problem_statement")):
                gate(s, t, "summary")
            for f in ("project_title", "summary", "problem_statement"):
                v = getattr(body, f)
                if v is not None:
                    setattr(t, f, v.strip() or None)
            if body.track is not None:
                t.track = check_track(t.event, body.track)
            if body.links is not None:
                t.links = clean_links(body.links, t.links)
            t.profile_updated_at = db.now()
            s.commit()
            return team_public(s, t)

    @app.post("/public/submit/{tok}/deck")
    def pub_deck(tok: str, body: DeckIn):
        with Session() as s:
            t = team_by_token(s, tok)
            gate(s, t, "deck")
            store_deck(s, t, body.filename, body.data_b64)
            return team_public(s, t)

    # --- public: ranking page, only once the organizer reveals it ---
    @app.get("/public/results/{tok}")
    def public_results(tok: str):
        with Session() as s:
            ev = s.scalar(select(Event).where(Event.public_token == tok)) if tok and len(tok) >= 8 else None
            if not ev:
                raise HTTPException(404, "This results link is not valid.")
            shortlists = []
            for r in event_rounds(s, ev.id):
                if r.announced:
                    ids = [x.team_id for x in s.scalars(select(RoundTeam).where(RoundTeam.round_id == r.id, RoundTeam.advanced.is_(True)))]
                    shortlists.append({"round": r.name, "advanced": [{"code": t.code, "name": t.name, "title": t.project_title}
                                                                      for t in s.scalars(select(Team).where(Team.id.in_(ids)).order_by(Team.code))] if ids else []})
            base = {"event": {"name": ev.name, "theme": ev.theme, "tracks": ev.tracks or []}, "revealed": bool(ev.results_public),
                    "shortlists": shortlists}
            if not ev.results_public:
                return {**base, "rows": []}
            rows, _, provisional = leaderboard_rows(s, ev, results_round(s, ev))
            return {**base, "provisional": provisional,
                    "rows": [{"rank": r["rank"], "code": r["code"], "name": r["name"], "title": r["title"], "track": r["track"],
                              "final": r["final"]} for r in rows if r.get("rank")]}

    # --- teams: their own result and "why was that team ahead of us" ---
    results_cache: dict = {}

    def results_round(s, ev: Event) -> Round | None:
        rs = event_rounds(s, ev.id)
        return rs[-1] if rs else None  # final standings = the last round

    def ranked_rows(s, ev: Event):
        key = ev.id
        hit = results_cache.get(key)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        rows, _, prov = leaderboard_rows(s, ev, results_round(s, ev))
        results_cache[key] = (time.time(), (rows, prov))
        return rows, prov

    def my_results(s, t: Team) -> dict | None:
        ev = t.event
        if not ev.results_public:
            return None
        rows, prov = ranked_rows(s, ev)
        mine = next((r for r in rows if r["team_id"] == t.id), None)
        ranked = [r for r in rows if r.get("rank")]
        b = budget_of(ev)
        hist = [{"round": r.name, "rank": x.rank, "final": x.final, "advanced": x.advanced} for r in event_rounds(s, ev.id)
                for x in [s.scalar(select(RoundTeam).where(RoundTeam.round_id == r.id, RoundTeam.team_id == t.id))] if x and x.decided_at]
        if not mine or not mine.get("rank"):
            return {"ranked": False, "of": len(ranked), "history": hist,
                    "note": "Your team has no final ranking (it did not reach the last round, or has an incomplete scorecard)."}
        meta = {c["id"]: c for c in rubric_of(ev, results_round(s, ev))["criteria"]}
        used = s.scalar(select(func.count()).select_from(Ask).where(Ask.team_id == t.id)) or 0
        return {"ranked": True, "rank": mine["rank"], "of": len(ranked), "final": mine["final"], "provisional": prov, "history": hist,
                "per_criterion": [{"id": cid, "title": meta[cid]["title"], "value": v["value"], "weight": v["weight"], "source": v["source"]}
                                  for cid, v in mine["per_criterion"].items() if cid in meta],
                "compare": {"enabled": b["explain_enabled"] and not prov, "left": max(0, b["explain_per_team"] - used),
                            "teams_above": [{"code": r["code"], "name": r["name"], "rank": r["rank"]} for r in ranked if r["rank"] < mine["rank"]]}}

    def explain(rubric: dict, mine: dict, other: dict, my_review: list[dict]) -> dict:
        """Template explanation built only from stored scores. No LLM, no other-team private data (no flags, notes or code)."""
        meta = {c["id"]: c for c in rubric["criteria"]}
        total_w = sum(float(c.get("weight", 0)) for c in rubric["criteria"]) or 1
        gaps_by = {c["id"]: c.get("gaps", []) for c in my_review}
        behind, ahead = [], []
        for cid, mp in mine["per_criterion"].items():
            op = other["per_criterion"].get(cid)
            if cid not in meta or not op or mp["value"] is None or op["value"] is None:
                continue
            pts = (op["value"] - mp["value"]) * float(meta[cid].get("weight", 0)) / total_w * 10
            row = {"id": cid, "title": meta[cid]["title"], "yours": mp["value"], "theirs": op["value"], "points": round(pts, 1),
                   "judged_by": f'{mp["judges"]} judge(s)' if mp["source"] == "judges" else "AI review"}
            if op["value"] - mp["value"] >= 0.5:
                anchors = meta[cid].get("anchors") or {}
                band = max((int(k) for k in anchors if int(k) <= round(op["value"])), default=None)
                row["higher_band_looks_like"] = anchors.get(str(band)) if band is not None else None
                row["your_gaps"] = [g for g in gaps_by.get(cid, [])][:3]
                behind.append(row)
            elif mp["value"] - op["value"] >= 0.5:
                ahead.append(row)
        behind.sort(key=lambda r: -r["points"])
        ahead.sort(key=lambda r: r["points"])
        gap = round(other["final"] - mine["final"], 1)
        if behind:
            top = ", ".join(f'{r["title"]} (+{r["points"]})' for r in behind[:2])
            head = f'{other["name"] or other["code"]} (#{other["rank"]}, {other["final"]}) finished ahead of you (#{mine["rank"]}, {mine["final"]}) by {gap} points. Most of the gap comes from: {top}.'
        else:
            head = f'{other["name"] or other["code"]} (#{other["rank"]}, {other["final"]}) finished {gap} points ahead; the difference is spread thinly across criteria, with no single one standing out.'
        return {"headline": head, "gap": gap, "behind_on": behind, "ahead_on": ahead,
                "you_led_on": [r["title"] for r in ahead], "mode": "template"}

    def polish(ev: Event, base: dict) -> tuple[dict, int]:
        """Optional single short Gemini call that rewrites the template in plain language. Facts are fixed by the template."""
        key = llm.api_key()
        if not key:
            return base, 0
        usage = llm.track_usage()
        prompt = ("Rewrite this hackathon score comparison as 3-5 friendly, plain sentences addressed to the losing team. "
                  "Use ONLY the facts below; do not add reasons, scores or opinions. End with one concrete thing they could improve.\n"
                  + json.dumps({k: base[k] for k in ("headline", "behind_on", "you_led_on")}) + '\nReturn ONLY JSON: {"paragraph": "..."}')
        try:
            out = llm._call_gemini(prompt, os.environ.get("GEMINI_MODEL_BROAD") or llm.model_for("broad") or os.environ.get("GEMINI_MODEL", llm.DEFAULT_MODEL), key)
            if isinstance(out.get("paragraph"), str) and out["paragraph"].strip():
                base = {**base, "paragraph": out["paragraph"].strip()[:1500], "mode": "template+gemini"}
        except Exception:  # noqa: BLE001
            pass
        return base, usage["prompt_tokens"] + usage["output_tokens"]

    @app.get("/public/submit/{tok}/results")
    def pub_my_results(tok: str):
        with Session() as s:
            t = team_by_token(s, tok)
            return {"revealed": bool(t.event.results_public), "results": my_results(s, t)}

    @app.post("/public/submit/{tok}/compare")
    def pub_compare(tok: str, body: CompareIn):
        with Session() as s:
            t = team_by_token(s, tok)
            ev = t.event
            b = budget_of(ev)
            if not ev.results_public:
                raise HTTPException(403, "Results have not been announced yet.")
            if not b["explain_enabled"]:
                raise HTTPException(403, "The organizers have not turned on score comparisons for this event.")
            rows, prov = ranked_rows(s, ev)
            if prov:
                raise HTTPException(409, "Judging is not complete, so a comparison would be misleading.")
            mine = next((r for r in rows if r["team_id"] == t.id), None)
            other = next((r for r in rows if r["code"].lower() == body.other.strip().lower()), None)
            if not mine or not mine.get("rank"):
                raise HTTPException(409, "Your team has no final ranking to compare.")
            if not other or not other.get("rank") or other["rank"] >= mine["rank"]:
                raise HTTPException(404, "You can only compare with a team that finished above you.")
            cached = s.scalar(select(Ask).where(Ask.team_id == t.id, Ask.other_team_id == other["team_id"]))
            if cached:
                return {**cached.answer, "cached": True}
            used = s.scalar(select(func.count()).select_from(Ask).where(Ask.team_id == t.id)) or 0
            if used >= b["explain_per_team"]:
                raise HTTPException(429, f"Your team has used its {b['explain_per_team']} comparisons.")
            rr = results_round(s, ev)
            e = latest_done(s, t.id, rr.id if rr else None)
            ans = explain(rubric_of(ev, rr), mine, other, ((e.result or {}).get("review", {}).get("criteria", []) if e else []))
            tokens = 0
            if b["explain_llm"] and not over_budget(s, ev):
                ans, tokens = polish(ev, ans)
            s.add(Ask(event_id=ev.id, team_id=t.id, other_team_id=other["team_id"], answer=ans, tokens=tokens))
            s.commit()
            return {**ans, "cached": False}

    # --- public: judge portal (private link) ---
    def judge_by_code(s, code: str) -> Judge:
        j = s.scalar(select(Judge).where(Judge.code == code)) if code and len(code) >= 8 else None
        if not j:
            raise HTTPException(404, "This link is not valid. Ask the organizers for your judge link.")
        return j

    def criteria_for_judge(rubric: dict):
        return [{k: c.get(k) for k in ("id", "title", "description", "weight", "ai_assisted", "anchors")}
                for c in rubric["criteria"]]

    def judge_scope(s, j: Judge, round_id):
        ev = s.get(Event, j.event_id)
        rnd = resolve_round(s, ev, round_id)
        mine_teams = assigned_teams(ev, j)
        if rnd:
            ids = round_team_ids(s, ev, rnd)
            mine_teams = [t for t in mine_teams if t.id in ids]
        return ev, rnd, mine_teams

    def my_row(s, j: Judge, tid: int, rnd: Round | None):
        rid = JudgeScore.round_id == rnd.id if rnd else JudgeScore.round_id.is_(None)
        return s.scalar(select(JudgeScore).where(JudgeScore.judge_id == j.id, JudgeScore.team_id == tid, rid))

    @app.get("/public/judge/{code}/session")
    def judge_session(code: str, round_id: int | None = Query(None, alias="round")):
        with Session() as s:
            j = judge_by_code(s, code)
            ev, rnd, mine_teams = judge_scope(s, j, round_id)
            rid = JudgeScore.round_id == rnd.id if rnd else JudgeScore.round_id.is_(None)
            mine = {x.team_id: x for x in s.scalars(select(JudgeScore).where(JudgeScore.judge_id == j.id, rid))}
            teams_out = []
            for t in mine_teams:
                e = eval_for_judge(s, t, rnd)
                m = mine.get(t.id)
                teams_out.append({"id": t.id, "code": t.code, "name": t.name, "title": t.project_title, "track": t.track,
                                  "evaluated": bool(e), "technical": e.overall_score if e else None,
                                  "high_flags": e.high_flags if e else 0, "has_deck": bool(t.deck),
                                  "my_status": ("recused" if m and m.conflict else m.status) if m else "not started"})
            return {"judge": {"name": j.name}, "event": {"name": ev.name, "theme": ev.theme, "tracks": ev.tracks or [],
                                                         "assignment_mode": ev.assignment_mode or "all"},
                    "round": round_dict(s, ev, rnd) if rnd else None, "rounds": [round_dict(s, ev, r) for r in event_rounds(s, ev.id)],
                    "criteria": criteria_for_judge(rubric_of(ev, rnd)), "teams": teams_out}

    @app.get("/public/judge/{code}/teams/{tid}")
    def judge_team(code: str, tid: int, round_id: int | None = Query(None, alias="round")):
        with Session() as s:
            j = judge_by_code(s, code)
            ev, rnd, mine_teams = judge_scope(s, j, round_id)
            t = s.get(Team, tid)
            if not t or t.event_id != j.event_id or t.id not in {x.id for x in mine_teams}:
                raise HTTPException(404, "Team not found")
            e = eval_for_judge(s, t, rnd)
            m = my_row(s, j, t.id, rnd)
            per, _ = event_overlaps(s, t.event_id)
            snap = None
            if rnd:
                rt = s.scalar(select(RoundTeam).where(RoundTeam.round_id == rnd.id, RoundTeam.team_id == t.id))
                snap = rt.snapshot if rt else None
            return {"team": {"id": t.id, "code": t.code, "name": t.name, "repo_url": t.repo_url, "title": t.project_title,
                             "summary": t.summary, "problem_statement": t.problem_statement,
                             "track": t.track, "links": t.links or {},
                             "members": [mm.get("name") for mm in (t.members or [])]},
                    "deck": {**(deck_meta(t) or {}), "slides": (t.deck or {}).get("slides", [])} if t.deck else None,
                    "cross_flags": per.get(t.id, []), "round": rnd.name if rnd else None, "submitted_at_close": snap,
                    "evaluation": judge_view(e.result) if e else None, "landscape": t.landscape,
                    "my": {"scores": m.scores, "qa": m.qa or [], "comment": m.comment, "conflict": m.conflict, "status": m.status,
                           "updated_at": iso(m.updated_at)} if m else None}

    @app.put("/public/judge/{code}/teams/{tid}/score")
    def judge_score(code: str, tid: int, body: ScoreIn, round_id: int | None = Query(None, alias="round")):
        with Session() as s:
            j = judge_by_code(s, code)
            ev, rnd, mine_teams = judge_scope(s, j, round_id)
            t = s.get(Team, tid)
            if not t or t.event_id != j.event_id or t.id not in {x.id for x in mine_teams}:
                raise HTTPException(404, "Team not found")
            if body.status not in ("draft", "submitted"):
                raise HTTPException(422, "status must be 'draft' or 'submitted'")
            rubric = rubric_of(ev, rnd)
            crit = {c["id"]: c for c in rubric["criteria"]}
            e = eval_for_judge(s, t, rnd)
            ai = {c["id"]: c["score"] for c in ((e.result or {}).get("review", {}).get("criteria", []) if e else [])}
            clean: dict = {}
            if not body.conflict:
                for cid, v in body.scores.items():
                    if cid not in crit:
                        raise HTTPException(422, f"Unknown criterion {cid!r}")
                    try:
                        sc = float(v.get("score"))
                    except (TypeError, ValueError):
                        raise HTTPException(422, f"Score for {crit[cid]['title']} must be a number from 0 to 10")
                    if not 0 <= sc <= 10:
                        raise HTTPException(422, f"Score for {crit[cid]['title']} must be between 0 and 10")
                    clean[cid] = {"score": sc, "note": str(v.get("note") or "")[:1000]}
                    if cid in ai:
                        clean[cid]["ai_score"] = ai[cid]
                if body.status == "submitted":
                    missing = [c["title"] for c in rubric["criteria"] if c["id"] not in clean and float(c.get("weight", 0)) > 0]
                    if missing:
                        raise HTTPException(422, "Score every criterion before submitting. Missing: " + ", ".join(missing))
                    unexplained = [crit[cid]["title"] for cid, v in clean.items()
                                   if "ai_score" in v and abs(v["score"] - v["ai_score"]) > OVERRIDE_NOTE_GAP and not v["note"].strip()]
                    if unexplained:
                        raise HTTPException(422, "Add a reason where your score differs from the AI score by more than "
                                                 f"{OVERRIDE_NOTE_GAP:g} point: " + ", ".join(unexplained))
            row = my_row(s, j, t.id, rnd)
            if not row:
                row = JudgeScore(event_id=ev.id, team_id=t.id, judge_id=j.id, round_id=rnd.id if rnd else None, history=[])
                s.add(row)
            if body.qa is not None:
                ok = {"strong", "ok", "weak", "not_asked"}
                row.qa = [{"question": str(q.get("question") or "")[:400], "rating": q.get("rating") if q.get("rating") in ok else "not_asked",
                           "note": str(q.get("note") or "")[:600]} for q in body.qa[:12] if q.get("question")]
            row.scores, row.comment, row.conflict, row.status = clean, (body.comment or "").strip() or None, body.conflict, body.status
            row.updated_at = db.now()
            row.history = (list(row.history or []) + [{"at": iso(db.now()), "status": body.status, "conflict": body.conflict,
                                                       "scores": {k: v["score"] for k, v in clean.items()}}])[-30:]
            s.commit()
            return {"status": row.status, "conflict": row.conflict, "saved_at": iso(row.updated_at)}

    @app.exception_handler(ingest.IngestError)
    async def _ingest_err(_, ex):
        return JSONResponse({"detail": str(ex)}, status_code=422)

    # --- pages: the React app (frontend/dist) for every non-API path; the legacy static pages if it was never built ---
    DIST = FRONTEND_DIST
    WEB = LEGACY_WEB
    if (DIST / "index.html").is_file():
        if (DIST / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "public/", "assets/")):
                raise HTTPException(404, "Not found")
            f = (DIST / path).resolve()
            if path and f.is_file() and DIST.resolve() in f.parents:  # favicon, etc.
                return FileResponse(f)
            return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
    elif WEB.is_dir():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB / "index.html")

        @app.get("/submit/{tok}", include_in_schema=False)
        def submit_page(tok: str):
            return FileResponse(WEB / "submit.html")

        @app.get("/judge/{code}", include_in_schema=False)
        def judge_page(code: str):
            return FileResponse(WEB / "judge.html")

    return app


class _LazyApp:
    """`uvicorn auditcore.server:app` builds the app on first request."""
    _app = None

    async def __call__(self, scope, receive, send):
        if _LazyApp._app is None:
            _LazyApp._app = create_app()
        await _LazyApp._app(scope, receive, send)


app = _LazyApp()


def main():
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"AuditCore dashboard: http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
