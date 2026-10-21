"""Database models. SQLite by default; set DATABASE_URL for Postgres
(e.g. postgresql+psycopg://user:pass@host/auditcore, after `pip install psycopg[binary]`)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    hackathon_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hackathon_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rubric: Mapped[dict] = mapped_column(JSON)
    theme: Mapped[str | None] = mapped_column(Text, nullable=True)  # problem statement / theme of the event
    tracks: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e.g. ["Health", "Education"]
    assignment_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # all | track | custom
    results_public: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # organizer has revealed the ranking
    public_token: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    budget: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {max_tokens, explain_enabled, explain_llm, explain_per_team}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    teams: Mapped[list["Team"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("event_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))  # the team ID organizers use, e.g. T-014
    name: Mapped[str] = mapped_column(String(200), default="")
    repo_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    repo_key: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)  # owner/name lowercased
    members: Mapped[list] = mapped_column(JSON, default=list)  # [{name, github?, email?}]
    project_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the project is about
    problem_statement: Mapped[str | None] = mapped_column(Text, nullable=True)  # the problem it solves
    submit_token: Mapped[str | None] = mapped_column(String(60), nullable=True, unique=True, index=True)
    deck: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {filename,size,sha1,uploaded_at,slides:[{n,text}],path}
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    track: Mapped[str | None] = mapped_column(String(80), nullable=True)
    links: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {demo_url, video_url, docs_url, notes}
    landscape: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # cached prior-art research (landscape.py)
    landscape_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Gemini tokens spent on it, cumulative
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    event: Mapped[Event] = relationship(back_populates="teams")
    evaluations: Mapped[list["Evaluation"]] = relationship(back_populates="team", cascade="all, delete-orphan")
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="team", cascade="all, delete-orphan")


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True)
    repo_url: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_flags: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fingerprints: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Gemini tokens this evaluation used
    kind: Mapped[str | None] = mapped_column(String(12), nullable=True)  # full (default) | checkpoint (cheap, offline, never ranked)
    sha: Mapped[str | None] = mapped_column(String(64), nullable=True)  # commit that was evaluated
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    team: Mapped[Team | None] = relationship(back_populates="evaluations")


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluations.id", ondelete="SET NULL"), nullable=True)
    author: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="human")  # ai|human
    body: Mapped[str] = mapped_column(Text)
    visible_to_team: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    team: Mapped[Team] = relationship(back_populates="feedback")


class Judge(Base):
    __tablename__ = "judges"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)  # secret in the judge's link
    tracks: Mapped[list | None] = mapped_column(JSON, nullable=True)  # used when the event assigns by track
    team_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)  # used when the event assigns by hand
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JudgeScore(Base):
    __tablename__ = "judge_scores"
    __table_args__ = (UniqueConstraint("team_id", "judge_id", "round_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # None for events without rounds
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("judges.id", ondelete="CASCADE"), index=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)  # {criterion_id: {score, note, ai_score?}}
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)  # judge recused themselves
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft|submitted
    qa: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{question, rating: strong|ok|weak|not_asked, note}] design Q&A
    history: Mapped[list] = mapped_column(JSON, default=list)  # audit trail of saves
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Round(Base):
    """One stage of a hackathon. An event without rounds behaves as a single implicit round."""
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = organizer opens it by hand
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)    # None = organizer closes it by hand
    accepts: Mapped[list] = mapped_column(JSON, default=list)  # subset of: summary, deck, repo, demo, video
    checks: Mapped[dict] = mapped_column(JSON, default=dict)   # {code, pitch, commits, landscape: bool}
    rubric: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # None = the event rubric
    advance: Mapped[dict] = mapped_column(JSON, default=dict)  # {mode: none|top_n|top_percent|threshold|manual, value, auto}
    checkpoint_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # poll repos for new commits while open
    close_profile: Mapped[str | None] = mapped_column(String(10), nullable=True)   # broad | deep model for the closing run
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    advanced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    announced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # shortlist is public
    last_checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RoundTeam(Base):
    """A team's outcome in a round, written when the round is advanced. Also freezes what they had submitted."""
    __tablename__ = "round_teams"
    __table_args__ = (UniqueConstraint("round_id", "team_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    advanced: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by: Mapped[str | None] = mapped_column(String(12), nullable=True)  # rule | organizer
    final: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {deck, links, profile, sha} as of the close
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Ask(Base):
    """A team's 'why was that team ranked above us' question. Doubles as the cache and the per-team limit counter."""
    __tablename__ = "asks"
    __table_args__ = (UniqueConstraint("team_id", "other_team_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    other_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    answer: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def ensure_columns(engine) -> list[str]:
    """Tiny auto-upgrade: add columns that newer models have but an older database lacks
    (new tables are created by create_all). Lets a Stage 2 database keep working."""
    from sqlalchemy import inspect, text
    added = []
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have:
                ddl = col.type.compile(engine.dialect)
                with engine.begin() as cx:
                    cx.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))
                added.append(f"{table.name}.{col.name}")
    return added


def relax_judge_unique(engine) -> bool:
    """Older databases enforce one score row per (team, judge); rounds need one per (team, judge, round). Rebuild if needed."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if not insp.has_table("judge_scores"):
        return False
    old = [u for u in insp.get_unique_constraints("judge_scores") if set(u["column_names"]) == {"team_id", "judge_id"}]
    if not old:
        return False
    if engine.dialect.name == "sqlite":
        cols = [c["name"] for c in insp.get_columns("judge_scores")]
        with engine.begin() as cx:
            for ix in insp.get_indexes("judge_scores"):
                cx.execute(text(f'DROP INDEX IF EXISTS "{ix["name"]}"'))
            cx.execute(text("ALTER TABLE judge_scores RENAME TO judge_scores_old"))
            JudgeScore.__table__.create(cx)
            keep = [c for c in cols if c in JudgeScore.__table__.columns]
            fill = {"conflict": "0", "status": "'draft'", "scores": "'{}'", "history": "'[]'", "updated_at": "CURRENT_TIMESTAMP"}
            sel = ", ".join(f'COALESCE("{c}", {fill[c]})' if c in fill else f'"{c}"' for c in keep)
            cx.execute(text(f"INSERT INTO judge_scores ({', '.join(chr(34) + c + chr(34) for c in keep)}) SELECT {sel} FROM judge_scores_old"))
            cx.execute(text("DROP TABLE judge_scores_old"))
    else:
        with engine.begin() as cx:
            for u in old:
                if u.get("name"):
                    cx.execute(text(f'ALTER TABLE judge_scores DROP CONSTRAINT "{u["name"]}"'))
            cx.execute(text("ALTER TABLE judge_scores ADD CONSTRAINT judge_scores_team_judge_round_key UNIQUE (team_id, judge_id, round_id)"))
    return True


def make_engine(url: str | None = None):
    url = url or os.environ.get("DATABASE_URL") or "sqlite:///auditcore.db"
    kw = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi, _):  # WAL lets the dashboard read while jobs write
            cur = dbapi.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(eng)
    ensure_columns(eng)
    relax_judge_unique(eng)
    return eng


def make_sessionmaker(engine):
    return sessionmaker(engine, expire_on_commit=False)


def iso(d: datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None
