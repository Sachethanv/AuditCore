"""Stage 3: deck parsing, pitch-vs-code alignment, pre-event commits, submission links, judge portal."""
import base64
import io
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from auditcore import db, deck, scoring, server
from auditcore.analyzers import alignment, commits
from test_server import fake_result, wait_done  # noqa: F401  (shared helpers)


# ---------- helpers ----------
def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def make_pptx(slides: list[tuple[str, list[str]]], notes: str | None = None) -> bytes:
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    for title, bullets in slides:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = "\n".join(bullets)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 20
    c.save()
    return buf.getvalue()


def git_repo(files: dict, commits_: list[tuple[str, str]]):
    """commits_: [(iso date, message)]; all files are added in the first commit."""
    d = tempfile.mkdtemp()
    run = lambda *a, env=None: subprocess.run(a, cwd=d, check=True, capture_output=True, env=dict(os.environ, **(env or {})))
    run("git", "init", "-q"); run("git", "config", "user.email", "a@b.c"); run("git", "config", "user.name", "t")
    for i, (date, msg) in enumerate(commits_):
        if i == 0:
            for path, content in files.items():
                os.makedirs(os.path.dirname(os.path.join(d, path)) or d, exist_ok=True)
                open(os.path.join(d, path), "w").write(content)
        else:
            open(os.path.join(d, f"change{i}.txt"), "w").write(f"line {i}\n" * (i + 1))
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", msg, env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
    return d


# ---------- deck parsing ----------
def test_parse_real_pptx_and_notes():
    raw = make_pptx([("SafeRoute", ["Real-time maps for safer commutes", "Built with React and FastAPI"]),
                     ("How it works", ["Users log in", "We store trips in PostgreSQL"])], notes="Mention the pilot")
    d = deck.parse("pitch.pptx", raw)
    assert d["kind"] == "pptx" and len(d["slides"]) == 2
    assert "SafeRoute" in d["slides"][0]["text"] and "FastAPI" in d["slides"][0]["text"]
    assert d["slides"][1]["notes"] == "Mention the pilot"
    assert len(d["sha1"]) == 40


def test_parse_real_pdf():
    d = deck.parse("pitch.pdf", make_pdf(["Our solution uses MongoDB", "and a chatbot"]))
    assert d["kind"] == "pdf" and "MongoDB" in d["slides"][0]["text"]


def test_deck_rejects_bad_files():
    for raw, msg in ((b"\xd0\xcf\x11\xe0" + b"x" * 50, "Old .ppt"), (b"hello world", "Upload a .pptx or PDF"),
                     (b"PK\x03\x04garbage", "valid .pptx")):
        with pytest.raises(deck.DeckError, match=msg):
            deck.parse("x", raw)
    with pytest.raises(deck.DeckError, match="empty"):
        deck.decode_upload("x.pptx", "")
    with pytest.raises(deck.DeckError, match="limit"):
        deck.decode_upload("x.pptx", b64(b"a" * (deck.MAX_BYTES + 1)))
    assert deck.decode_upload("../../evil name.pptx", b64(b"abc"))[0] == "evil name.pptx"


# ---------- alignment ----------
def test_heuristic_alignment_finds_unsupported_claims_and_undersold():
    repo = git_repo({"app.py": "from flask import Flask\napp = Flask(__name__)\n@app.route('/login')\ndef login():\n    return 'jwt token'\n" + "x = 1\n" * 30,
                     "requirements.txt": "flask\nstripe\n"}, [("2026-10-11T10:00:00+00:00", "init")])
    ctx = {"title": "T", "summary": "A web app built with Flask, MongoDB and TensorFlow", "problem": "Help farmers",
           "deck": {"slides": [{"n": 1, "text": "We use Flask, MongoDB and TensorFlow with a React frontend"}]}}
    al = alignment.heuristic(repo, ctx)
    st = {c["claim"]: c["status"] for c in al["claims"]}
    assert st["Pitch mentions Flask"] == "supported"
    assert st["Pitch mentions MongoDB"] == "unsupported" and st["Pitch mentions TensorFlow"] == "unsupported"
    assert any("MongoDB" in g for g in al["code_gaps_vs_deck"])
    assert any("authentication" in u.lower() for u in al["undersold_in_code"])
    assert any("Payments" in u for u in al["undersold_in_code"])
    ev = next(c["evidence"][0] for c in al["claims"] if c["claim"].endswith("Flask"))
    assert ev["file"] and ev["quote"]
    assert al["alignment_score"] is not None and al["alignment_score"] < 60
    codes = {f["code"] for f in alignment.flags(al)}
    assert "deck_claims_unsupported" in codes


def test_alignment_none_without_pitch_and_gemini_evidence_gate(monkeypatch):
    repo = git_repo({"main.py": "def real():\n    return 1\n" + "y = 2\n" * 25}, [("2026-10-11T10:00:00+00:00", "init")])
    assert alignment.analyze(repo, {}, {}) is None
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(alignment.llm, "_call_gemini", lambda *a, **k: {
        "summary": "s", "declared_summary_check": {"verdict": "matches", "note": ""},
        "claims": [{"claim": "has real()", "slide": 1, "status": "supported", "note": "", "evidence": [{"file": "main.py", "line": 1, "quote": "def real():"}]},
                   {"claim": "invented", "slide": 2, "status": "supported", "note": "", "evidence": [{"file": "main.py", "line": 9, "quote": "def invented():"}]},
                   {"claim": "no proof", "slide": 3, "status": "partial", "note": "", "evidence": []}],
        "code_gaps_vs_deck": [], "undersold_in_code": [], "problem_fit": {"stated_problem": "p", "verdict": "addressed", "reasoning": "r", "evidence": []},
        "problem_gaps": [], "questions_for_team": [], "confidence": "high"})
    al = alignment.analyze(repo, {"summary": "x", "deck": None}, {})
    st = [c["status"] for c in al["claims"]]
    assert st == ["supported", "not_checkable", "not_checkable"]  # fabricated / unproven claims are downgraded
    assert al["claims_checked"] == 1 and al["alignment_score"] == 100 and al["mode"].startswith("gemini:")
    assert al["evidence_stats"] == {"cited": 2, "verified": 1}


# ---------- pre-event commits ----------
def test_pre_event_share_of_code_and_flag():
    repo = git_repo({"app.py": "print('x')\n" * 800}, [("2026-09-01T10:00:00+00:00", "old project"),
                                                       ("2026-10-11T10:00:00+00:00", "small change")])
    m = commits.analyze(repo, hackathon_start=datetime(2026, 10, 10, tzinfo=timezone.utc))
    pe = m["pre_event"]
    assert pe["start_set"] and pe["commits_before"] == 1 and pe["share_lines"] > 0.9 and pe["earliest"].startswith("2026-09-01")
    f = next(f for f in m["flags"] if f["code"] == "pre_existing_work")
    assert f["severity"] == "high" and "before the hackathon started" in f["message"]
    assert commits.analyze(repo)["pre_event"] == {"start_set": False}


# ---------- scoring ----------
def test_scoring_combine_mixes_ai_and_judges():
    rubric = {"criteria": [{"id": "a", "weight": 50, "ai_assisted": True}, {"id": "h", "weight": 50, "ai_assisted": False}]}
    ai_only = scoring.combine(rubric, {"a": 8}, [])
    assert ai_only["final"] == 80.0 and not ai_only["complete"] and ai_only["coverage"] == 0.5
    rows = [{"scores": {"a": {"score": 6}, "h": {"score": 9}}}, {"scores": {"a": {"score": 10}, "h": {"score": 3}}}]
    full = scoring.combine(rubric, {"a": 8}, rows)
    assert full["complete"] and full["final"] == 70.0 and full["max_spread"] == 6.0 and full["judge_count"] == 2


# ---------- API: profile, deck, submission link, judges ----------
@pytest.fixture
def client(tmp_path):
    seen = []

    def ev(url, rubric, start=None, end=None, model=None, members=None, context=None):
        seen.append(context)
        return fake_result(url, rubric, start, end, members=members, context=context, score=80.0)

    c = TestClient(server.create_app(f"sqlite:///{tmp_path}/s3.db", evaluator=ev, workers=1, uploads_dir=str(tmp_path / "up")))
    c.seen = seen
    return c


def mk_event(c, **kw):
    return c.post("/api/events", json={"name": "E", "theme": "Smart cities", **kw}).json()


def test_csv_profile_columns_and_submission_link(client):
    ev = mk_event(client)
    csvt = 'team_id,team_name,repo_url,members,project_title,description,problem\nT1,Alpha,https://github.com/a/b,Asha (@asha),SafeRoute,"Maps for safe commutes","Women feel unsafe at night"\n'
    assert client.post(f"/api/events/{ev['id']}/teams/import", json={"csv": csvt}).json()["created"] == 1
    t = client.get(f"/api/events/{ev['id']}/teams").json()[0]
    assert t["project_title"] == "SafeRoute" and t["summary"] == "Maps for safe commutes" and t["problem_statement"].startswith("Women")
    assert t["submit_path"].startswith("/submit/") and len(t["submit_path"]) > 20


def test_team_uploads_deck_and_profile_via_private_link_without_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITCORE_TOKEN", "adm")
    c = TestClient(server.create_app(f"sqlite:///{tmp_path}/p.db", evaluator=lambda *a, **k: None, uploads_dir=str(tmp_path / "up")))
    h = {"Authorization": "Bearer adm"}
    ev = c.post("/api/events", json={"name": "E", "theme": "Smart cities"}, headers=h).json()
    t = c.post(f"/api/events/{ev['id']}/teams", json={"code": "T1", "name": "Alpha", "repo_url": "https://github.com/a/b"}, headers=h).json()
    tok = t["submit_path"].split("/")[-1]
    assert c.get("/api/events", headers={}).status_code == 401           # admin API still locked
    pub = c.get(f"/public/submit/{tok}")                                  # team link works with no admin token
    assert pub.status_code == 200 and pub.json()["event"]["theme"] == "Smart cities"
    assert "members" in pub.json()["team"] and "submit_token" not in str(pub.json())
    assert c.get("/public/submit/not-a-real-token-1234").status_code == 404
    r = c.put(f"/public/submit/{tok}/profile", json={"project_title": "SafeRoute", "summary": "Safe maps", "problem_statement": "Night safety"})
    assert r.json()["profile"]["project_title"] == "SafeRoute"
    up = c.post(f"/public/submit/{tok}/deck", json={"filename": "pitch.pptx", "data_b64": b64(make_pptx([("SafeRoute", ["Uses React"])]))})
    assert up.status_code == 200 and up.json()["deck"]["slides"] == 1 and up.json()["deck"]["filename"] == "pitch.pptx"
    bad = c.post(f"/public/submit/{tok}/deck", json={"filename": "x.pptx", "data_b64": b64(b"nope")})
    assert bad.status_code == 422 and "pptx or PDF" in bad.json()["detail"]
    d = c.get(f"/api/teams/{t['id']}", headers=h).json()
    assert d["deck"]["slides"] == 1 and d["deck_slides"][0]["text"].startswith("SafeRoute")
    assert c.get(f"/api/teams/{t['id']}/deck/file", headers=h).status_code == 200


def test_context_reaches_evaluator_and_late_deck_flag(client):
    ev = mk_event(client, hackathon_end="2020-01-01T00:00:00Z")  # deadline long past, so any upload is late
    t = client.post(f"/api/events/{ev['id']}/teams", json={"code": "T1", "repo_url": "https://github.com/a/b", "summary": "About it"}).json()
    client.post(f"/api/teams/{t['id']}/deck", json={"filename": "d.pdf", "data_b64": b64(make_pdf(["slide text MongoDB"]))})
    flags = client.get(f"/api/events/{ev['id']}/teams").json()[0]["cross_flags"]
    assert any(f["code"] == "deck_after_deadline" for f in flags)
    client.post(f"/api/teams/{t['id']}/evaluate")
    wait_done(client, f"/api/events/{ev['id']}/teams", key=lambda d: d[0]["latest"]["status"] if d[0]["latest"] else "x")
    ctx = client.seen[-1]
    assert ctx["theme"] == "Smart cities" and ctx["summary"] == "About it" and "MongoDB" in ctx["deck"]["slides"][0]["text"]
    lb = client.get(f"/api/events/{ev['id']}/leaderboard").json()["rows"][0]
    assert lb["has_deck"] and lb["deck_slides"] == 1 and lb["deck_newer_than_eval"] is False


def test_quick_check_with_summary_and_deck(client):
    r = client.post("/api/evaluations", json={"repo_url": "github.com/o/r", "summary": "It does X", "problem_statement": "Y is hard",
                                              "deck": {"filename": "p.pptx", "data_b64": b64(make_pptx([("T", ["uses Flask"])]))}})
    assert r.status_code == 202
    wait_done(client, f"/api/evaluations/{r.json()['id']}")
    ctx = client.seen[-1]
    assert ctx["summary"] == "It does X" and ctx["problem"] == "Y is hard" and ctx["deck"]["slides"][0]["text"].startswith("T")
    assert client.post("/api/evaluations", json={"repo_url": "github.com/o/r", "deck": {"filename": "x", "data_b64": b64(b"zzz")}}).status_code == 422


def done_team(client, evid, code="T1", url="https://github.com/a/b"):
    t = client.post(f"/api/events/{evid}/teams", json={"code": code, "name": code, "repo_url": url}).json()
    client.post(f"/api/teams/{t['id']}/evaluate")
    wait_done(client, f"/api/events/{evid}/teams", key=lambda d: next(x for x in d if x["id"] == t["id"])["latest"]["status"])
    return t


def test_judge_portal_scoring_overrides_recusal_and_final_ranking(client):
    ev = mk_event(client)
    t1, t2 = done_team(client, ev["id"], "T1", "https://github.com/a/one"), done_team(client, ev["id"], "T2", "https://github.com/a/two")
    first = client.get(f"/api/events/{ev['id']}/leaderboard").json()
    assert first["provisional"] is True and all(r["rank"] in (1, 2) for r in first["rows"])  # AI-only ranking before judging
    j = client.post(f"/api/events/{ev['id']}/judges", json={"name": "Dr Rao"}).json()
    code = j["path"].split("/")[-1]
    sess = client.get(f"/public/judge/{code}/session").json()
    assert sess["judge"]["name"] == "Dr Rao" and len(sess["teams"]) == 2 and sess["teams"][0]["my_status"] == "not started"
    assert len(sess["criteria"]) == 8 and any(not c["ai_assisted"] for c in sess["criteria"])
    assert client.get("/public/judge/nonexistentcode12/session").status_code == 404
    view = client.get(f"/public/judge/{code}/teams/{t1['id']}").json()
    assert view["evaluation"]["overall_score"] == 80.0 and view["my"] is None and "fingerprints" not in str(view)

    ai = {c["id"]: 7 for c in view["evaluation"]["criteria"]}          # fake evaluator scores every AI criterion 7
    scores = {c["id"]: {"score": ai.get(c["id"], 6), "note": ""} for c in sess["criteria"]}
    url = f"/public/judge/{code}/teams/{t1['id']}/score"
    assert client.put(url, json={"scores": {k: v for k, v in scores.items() if k != "demo_presentation"}, "status": "submitted"}).status_code == 422  # missing one
    scores["code_quality"]["score"] = 3                                  # 4 below the AI score and no reason given
    r = client.put(url, json={"scores": scores, "status": "submitted"})
    assert r.status_code == 422 and "Code quality" in r.json()["detail"]
    assert client.put(url, json={"scores": scores, "status": "draft"}).status_code == 200  # drafts may be incomplete or unexplained
    scores["code_quality"]["note"] = "Mostly copy-pasted handlers"
    scores["demo_presentation"]["score"] = 11
    assert client.put(url, json={"scores": scores, "status": "submitted"}).status_code == 422  # out of range
    scores["demo_presentation"]["score"] = 9
    assert client.put(url, json={"scores": scores, "status": "submitted"}).status_code == 200

    # recuse from T2 (e.g. knows the team)
    assert client.put(f"/public/judge/{code}/teams/{t2['id']}/score", json={"conflict": True, "status": "submitted"}).status_code == 200
    sess = client.get(f"/public/judge/{code}/session").json()
    assert {t["code"]: t["my_status"] for t in sess["teams"]} == {"T1": "submitted", "T2": "recused"}

    lb = client.get(f"/api/events/{ev['id']}/leaderboard").json()["rows"]
    by = {r["code"]: r for r in lb}
    assert by["T1"]["judge_count"] == 1 and by["T1"]["complete"] and by["T1"]["rank"] == 1
    assert by["T2"]["judge_count"] == 0 and not by["T2"]["complete"]      # recused judge contributes nothing
    assert "rank" not in by["T2"] and by["T2"]["final"] == 70.0          # AI-only partial score is not ranked against judged teams
    assert client.get(f"/api/events/{ev['id']}/leaderboard").json()["provisional"] is False
    assert by["T1"]["per_criterion"]["code_quality"]["value"] == 3 and by["T1"]["per_criterion"]["system_design"]["source"] == "judges"
    d = client.get(f"/api/teams/{t1['id']}").json()
    assert d["judge_scores"][0]["judge"] == "Dr Rao" and d["judge_scores"][0]["scores"]["code_quality"]["ai_score"] == 7
    jl = client.get(f"/api/events/{ev['id']}/judges").json()[0]
    assert jl["submitted"] == 1 and jl["recused"] == 1
    csvt = client.get(f"/api/events/{ev['id']}/results.csv").text
    assert csvt.splitlines()[0].startswith("rank,team_id") and "T1" in csvt

    # judge from another event cannot read this event's teams
    ev2 = client.post("/api/events", json={"name": "Other"}).json()
    code2 = client.post(f"/api/events/{ev2['id']}/judges", json={"name": "Other judge"}).json()["path"].split("/")[-1]
    assert client.get(f"/public/judge/{code2}/teams/{t1['id']}").status_code == 404


def test_old_database_is_upgraded(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    url = f"sqlite:///{tmp_path}/old.db"
    e = create_engine(url)
    with e.begin() as cx:
        cx.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, name VARCHAR(200), hackathon_start DATETIME, hackathon_end DATETIME, rubric JSON, created_at DATETIME)"))
        cx.execute(text("CREATE TABLE teams (id INTEGER PRIMARY KEY, event_id INTEGER, code VARCHAR(40), name VARCHAR(200), repo_url VARCHAR(300), repo_key VARCHAR(300), members JSON, created_at DATETIME)"))
        cx.execute(text("INSERT INTO events (id, name, rubric) VALUES (1, 'Old event', :r)"), {"r": '{"criteria": [{"id": "a", "title": "A", "weight": 1}]}'})
        cx.execute(text("INSERT INTO teams (id, event_id, code, name) VALUES (1, 1, 'T-1', 'Old team')"))
    e.dispose()
    c = TestClient(server.create_app(url, evaluator=lambda *a, **k: None, uploads_dir=str(tmp_path / "u")))
    t = c.get("/api/events/1/teams").json()[0]
    assert t["name"] == "Old team" and t["submit_path"].startswith("/submit/")   # token created on first read
    assert c.get("/api/events").json()[0]["theme"] is None


def test_pages_are_served(client):
    for path in ("/", "/submit/abcdefgh1234", "/judge/abcdefgh1234"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path


# ---------- Stage 4: tracks, links, assignment, reveal ----------
def _mk(client):
    ev = client.post("/api/events", json={"name": "T4", "tracks": ["Health", "Edu", "health"], "assignment_mode": "track"}).json()
    assert ev["tracks"] == ["Health", "Edu"] and ev["assignment_mode"] == "track"
    a = client.post(f"/api/events/{ev['id']}/teams", json={"code": "A", "name": "Alpha", "repo_url": "https://github.com/q/one", "track": "health"}).json()
    b = client.post(f"/api/events/{ev['id']}/teams", json={"code": "B", "name": "Beta", "repo_url": "https://github.com/q/two", "track": "Edu"}).json()
    return ev, a, b


def test_tracks_links_and_validation(client):
    ev, a, b = _mk(client)
    assert a["track"] == "Health"  # matched case-insensitively to the event's track
    assert client.post(f"/api/events/{ev['id']}/teams", json={"code": "C", "track": "Space"}).status_code == 422
    r = client.patch(f"/api/teams/{a['id']}", json={"links": {"demo_url": "https://demo.example.com", "notes": "login: demo"}})
    assert r.json()["links"]["demo_url"] == "https://demo.example.com"
    assert client.patch(f"/api/teams/{a['id']}", json={"links": {"demo_url": "javascript:alert(1)"}}).status_code == 422
    tok = a["submit_path"].split("/")[-1]
    p = client.put(f"/public/submit/{tok}/profile", json={"links": {"video_url": "https://youtu.be/x"}}).json()
    assert p["profile"]["links"] == {"demo_url": "https://demo.example.com", "notes": "login: demo", "video_url": "https://youtu.be/x"}
    assert p["event"]["tracks"] == ["Health", "Edu"]


def test_judge_assignment_by_track_and_custom(client):
    ev, a, b = _mk(client)
    j = client.post(f"/api/events/{ev['id']}/judges", json={"name": "Dr H", "tracks": ["Health"]}).json()
    code = j["path"].split("/")[-1]
    sess = client.get(f"/public/judge/{code}/session").json()
    assert [t["code"] for t in sess["teams"]] == ["A"]
    assert client.get(f"/public/judge/{code}/teams/{b['id']}").status_code == 404
    assert client.put(f"/public/judge/{code}/teams/{b['id']}/score", json={"conflict": True, "status": "submitted"}).status_code == 404
    client.put(f"/api/events/{ev['id']}", json={}) if False else None
    client.patch(f"/api/events/{ev['id']}", json={"name": "T4", "assignment_mode": "custom"})
    assert client.get(f"/public/judge/{code}/session").json()["teams"] == []
    client.patch(f"/api/judges/{j['id']}", json={"team_ids": [b["id"]]})
    assert [t["code"] for t in client.get(f"/public/judge/{code}/session").json()["teams"]] == ["B"]
    assert client.get("/api/events/%d/judges" % ev["id"]).json()[0]["teams"] == 1


def test_results_reveal_is_controlled(client):
    ev, a, b = _mk(client)
    tok = client.put(f"/api/events/{ev['id']}/results", json={"public": False}).json()["results_path"].split("/")[-1]
    r = client.get(f"/public/results/{tok}").json()
    assert r["revealed"] is False and r["rows"] == []
    client.post(f"/api/events/{ev['id']}/evaluate", json={})
    time.sleep(0.6)
    client.put(f"/api/events/{ev['id']}/results", json={"public": True})
    r = client.get(f"/public/results/{tok}").json()
    assert r["revealed"] and {x["code"] for x in r["rows"]} == {"A", "B"}
    assert set(r["rows"][0]) == {"rank", "code", "name", "title", "track", "final"}  # no flags, no repo, no scores per criterion
    assert client.get("/public/results/nonexistenttoken").status_code == 404


def test_react_app_serves_every_route_but_not_api(client):
    from pathlib import Path
    if not (Path(server.FRONTEND_DIST) / "index.html").is_file():
        pytest.skip("frontend not built")
    for path in ("/events", "/events/1/leaderboard", "/teams/3/code", "/quick/5", "/results/abcdefgh1234"):
        r = client.get(path)
        assert r.status_code == 200 and "<div id=\"root\">" in r.text, path
    r = client.get("/api/nope")
    assert r.status_code == 404 and "text/html" not in r.headers["content-type"]
    assert client.get("/../../etc/passwd").status_code in (200, 404)  # never reads outside dist
