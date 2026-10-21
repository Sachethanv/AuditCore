"""Stage 6: rounds, checkpoint analysis, deadline snapshots, speed controls."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from auditcore import db, ingest, rounds as rnds, server
from test_server import fake_result
from test_stage3 import b64, make_pptx, mk_event


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def rc(tmp_path, monkeypatch):
    """Client whose evaluator accepts the extra kwargs rounds pass, records every call, and fakes repo commits."""
    calls, shas, live = [], {}, {"now": 0, "max": 0}
    lock = threading.Lock()

    def ev(url, rubric, start=None, end=None, model=None, members=None, context=None, **kw):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(float(kw.pop("_sleep", 0)) or getattr(ev, "sleep", 0))
        calls.append({"url": url, "model": model, "kw": kw, "ctx": bool(context), "end": end, "ids": [c["id"] for c in rubric["criteria"]]})
        with lock:
            live["now"] -= 1
        r = fake_result(url, rubric, start, end, members=members, context=context, score=70.0)
        r["meta"]["github"] = {"evaluated_sha": shas.get(url, "sha-0"), "head_sha": shas.get(url, "sha-0")}
        return r
    ev.sleep = 0
    monkeypatch.setattr(ingest, "head_sha", lambda url, timeout=40: shas.get(url, "sha-0"))
    c = TestClient(server.create_app(f"sqlite:///{tmp_path}/r.db", evaluator=ev, workers=16))
    c.calls, c.shas, c.live, c.ev = calls, shas, live, ev
    return c


def wait_idle(c, evid, timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        p = c.get(f"/api/events/{evid}/progress").json()
        if p["queued"] + p["running"] == 0:
            return p
        time.sleep(0.05)
    raise AssertionError("evaluations did not finish")


def wait_calls(c, n, timeout=10):
    t = time.time()
    while time.time() - t < timeout and len(c.calls) < n:
        time.sleep(0.05)
    time.sleep(0.2)
    assert len(c.calls) >= n, c.calls


def team(c, evid, code, repo=None, **kw):
    return c.post(f"/api/events/{evid}/teams", json={"code": code, "name": code, "repo_url": repo, **kw}).json()


# ---------- presets and validation ----------
def test_presets_cover_common_formats(rc):
    ids = {p["id"]: p for p in rc.get("/api/rounds/presets").json()}
    assert {"single", "submit_finals", "idea_build_final", "four_stage"} <= set(ids)
    assert [r["name"] for r in ids["idea_build_final"]["rounds"]] == ["Idea round", "Prototype", "Final"]
    assert ids["idea_build_final"]["rounds"][0]["accepts"] == ["summary", "deck"]         # round 1: slides only


def test_preset_creates_dated_rounds_with_own_rubrics(rc):
    ev = mk_event(rc)
    start = utcnow() + timedelta(hours=1)
    rs = rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "idea_build_final", "start": iso(start), "hours": [2, 6, 3]}).json()
    assert [r["state"] for r in rs] == ["upcoming"] * 3
    assert rs[0]["ends_at"][:16] == iso(start + timedelta(hours=2))[:16] and rs[1]["starts_at"] == rs[0]["ends_at"]
    assert [c["id"] for c in rs[0]["criteria"]] == ["problem_clarity", "solution_feasibility", "innovation_creativity", "impact_usefulness"]
    assert rs[1]["custom_rubric"] is False and rs[2]["custom_rubric"] is True
    assert rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "single"}).status_code == 409      # no silent overwrite
    assert rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "single", "replace": True}).status_code == 201
    assert rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "nope", "replace": True}).status_code == 422


def test_custom_round_validation(rc):
    ev = mk_event(rc)
    url = f"/api/events/{ev['id']}/rounds"
    ok = rc.post(url, json={"name": "Quarter-final", "accepts": ["deck"], "checks": {"code": False}, "advance": {"mode": "top_n", "value": 5},
                            "criteria": [{"id": "innovation_creativity", "weight": 60}, {"id": "problem_clarity", "weight": 40}]})
    assert ok.status_code == 201 and ok.json()["accepts"] == ["deck"] and ok.json()["criteria"][1]["ai_assisted"] is True
    assert rc.post(url, json={"name": "x", "accepts": ["hologram"]}).status_code == 422
    assert rc.post(url, json={"name": "x", "advance": {"mode": "top_n"}}).status_code == 422
    assert rc.post(url, json={"name": "x", "advance": {"mode": "top_percent", "value": 150}}).status_code == 422
    assert rc.post(url, json={"name": "x", "criteria": [{"id": "no_such", "weight": 5}]}).status_code == 422
    t = utcnow()
    assert rc.post(url, json={"name": "x", "starts_at": iso(t), "ends_at": iso(t - timedelta(hours=1))}).status_code == 422
    assert rc.post(url, json={"name": "x", "checkpoint_minutes": 1}).status_code == 422


def test_advancement_rules():
    rows = [{"team_id": i, "rank": i, "final": 90 - i * 5} for i in range(1, 7)]
    assert rnds.select_advancing(rows, "top_n", 2) == {1, 2}
    assert rnds.select_advancing(rows, "top_percent", 50) == {1, 2, 3}
    assert rnds.select_advancing(rows, "threshold", 70) == {1, 2, 3, 4}
    assert rnds.select_advancing(rows, "manual", None) == set() and len(rnds.select_advancing(rows, "none", None)) == 6
    tied = [{"team_id": 1, "rank": 1, "final": 80}, {"team_id": 2, "rank": 2, "final": 70}, {"team_id": 3, "rank": 3, "final": 70}]
    assert rnds.select_advancing(tied, "top_n", 2) == {1, 2, 3}      # a tie at the cut-off never eliminates anyone


# ---------- submissions are gated by the open round ----------
def test_submission_gating_follows_round_accepts_and_clock(rc):
    ev = mk_event(rc)
    t = team(rc, ev["id"], "T1", "https://github.com/a/one")
    tok = rc.get(f"/api/teams/{t['id']}").json()["submit_path"].split("/")[-1]
    deck = {"filename": "d.pptx", "data_b64": b64(make_pptx([("Idea", ["x"])]))}
    assert rc.post(f"/public/submit/{tok}/deck", json=deck).status_code == 200                # no rounds: always open
    now = utcnow()
    rc.post(f"/api/events/{ev['id']}/rounds", json={"name": "Idea", "accepts": ["summary", "deck"], "starts_at": iso(now - timedelta(hours=1)), "ends_at": iso(now + timedelta(hours=1))})
    rc.post(f"/api/events/{ev['id']}/rounds", json={"name": "Build", "accepts": ["repo", "demo"], "starts_at": iso(now + timedelta(hours=2)), "ends_at": iso(now + timedelta(hours=9))})
    pub = rc.get(f"/public/submit/{tok}").json()
    assert pub["round"]["name"] == "Idea" and pub["round"]["accepts"] == ["summary", "deck"] and pub["submissions_open"] is True
    assert "Repository link" not in [i["label"] for i in pub["readiness"]["items"]]            # only what this round asks for
    assert rc.post(f"/public/submit/{tok}/deck", json=deck).status_code == 200
    assert rc.put(f"/public/submit/{tok}/profile", json={"summary": "x" * 60}).status_code == 200
    r = rc.put(f"/public/submit/{tok}/profile", json={"links": {"demo_url": "https://demo.example.com"}})
    assert r.status_code == 409 and "does not accept" in r.json()["detail"]                    # demo links are for the Build round
    # the Idea round ends: nothing is accepted until Build opens
    idea = rc.get(f"/api/events/{ev['id']}/rounds").json()[0]
    rc.patch(f"/api/rounds/{idea['id']}", json={"starts_at": iso(now - timedelta(hours=3)), "ends_at": iso(now - timedelta(minutes=1))})
    r = rc.post(f"/public/submit/{tok}/deck", json=deck)
    assert r.status_code == 409 and "Next: Build" in r.json()["detail"]
    assert rc.get(f"/public/submit/{tok}").json()["submissions_open"] is False


# ---------- slides-only round ----------
def test_idea_round_is_scored_from_slides_without_a_repo(rc):
    ev = mk_event(rc)
    now = utcnow()
    rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "idea_build_final", "start": iso(now - timedelta(hours=1)), "hours": [4, 4, 4]})
    t = team(rc, ev["id"], "T1", None, summary="Route planner for night commuters " * 3, problem_statement="Commuters feel unsafe at night " * 4)
    team(rc, ev["id"], "T2", None)                                              # nothing submitted
    rc.post(f"/api/teams/{t['id']}/deck", json={"filename": "d.pptx", "data_b64": b64(make_pptx([("Problem", ["Night commuters are unsafe"]), ("Solution", ["Crowd reports"])]))})
    r = rc.post(f"/api/events/{ev['id']}/evaluate").json()
    assert r["started"] == ["T1"] and r["skipped"][0]["reason"] == "nothing submitted yet" and r["round"] == "Idea round"
    wait_idle(rc, ev["id"])
    assert rc.calls == []                                                       # the code evaluator was never used
    lb = rc.get(f"/api/events/{ev['id']}/leaderboard").json()
    row = [x for x in lb["rows"] if x["code"] == "T1"][0]
    assert set(row["criteria"]) == {"problem_clarity", "solution_feasibility"} and row["criteria"]["problem_clarity"] <= 5   # offline estimate is capped
    d = rc.get(f"/api/teams/{t['id']}").json()
    assert "slides only" not in d["evaluation"]["mode"] and d["evaluation"]["mode"].startswith("heuristic")
    assert "Innovation & creativity" in [c["title"] for c in lb["human_criteria"]]


def test_judges_only_round_has_nothing_for_the_ai(rc):
    ev = mk_event(rc)
    rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "submit_finals", "start": iso(utcnow() - timedelta(hours=30)), "hours": [1, 48]})
    team(rc, ev["id"], "T1", "https://github.com/a/one")
    finals = rc.get(f"/api/events/{ev['id']}/rounds").json()[1]
    r = rc.post(f"/api/events/{ev['id']}/evaluate?round={finals['id']}").json()
    assert r["started"] == [] and "judges only" in r["skipped"][0]["reason"]


# ---------- closing a round: snapshot, deadline checkout, skip unchanged ----------
def test_scheduler_closes_round_freezes_submissions_and_evaluates_at_deadline(rc):
    ev = mk_event(rc)
    now = utcnow()
    rc.post(f"/api/events/{ev['id']}/rounds", json={"name": "Build", "starts_at": iso(now - timedelta(hours=2)), "ends_at": iso(now + timedelta(hours=1)),
                                                    "close_profile": "deep", "advance": {"mode": "top_n", "value": 1}})
    t = team(rc, ev["id"], "T1", "https://github.com/a/one", summary="v1 " * 30)
    rid = rc.get(f"/api/events/{ev['id']}/rounds").json()[0]["id"]
    out = rc.app.state.tick(now)
    assert out["opened"] == [rid] and out["closed"] == []                       # opened by the clock, nothing else yet
    rc.patch(f"/api/rounds/{rid}", json={"ends_at": iso(now + timedelta(seconds=1))})
    out = rc.app.state.tick(now + timedelta(minutes=5))
    assert [c["round"] for c in out["closed"]] == [rid] and out["closed"][0]["started"] == ["T1"]
    wait_calls(rc, 1)
    call = rc.calls[0]
    assert call["kw"].get("snapshot") is True and call["end"] is not None        # evaluated as of the deadline, not today's HEAD
    d = rc.get(f"/api/events/{ev['id']}/rounds").json()[0]
    assert d["state"] == "closed" and d["closed_at"]
    tm = rc.get(f"/api/teams/{t['id']}").json()
    snap = tm["rounds"][0]["snapshot"]
    assert snap["profile"]["summary"].startswith("v1") and snap["repo_url"].endswith("/one")
    rc.put(f"/public/submit/{tm['submit_path'].split('/')[-1]}/profile", json={"summary": "v2 " * 30})   # late edit is refused
    assert rc.app.state.tick(now + timedelta(minutes=6))["closed"] == []        # closing happens once
    # a second full run with an unchanged repo reuses the first result instead of analysing again
    n = len(rc.calls)
    rc.post(f"/api/events/{ev['id']}/evaluate", json={"skip_unchanged": True})
    wait_idle(rc, ev["id"])
    assert len(rc.calls) == n
    assert "[unchanged since last run]" in rc.get(f"/api/teams/{t['id']}").json()["evaluation"]["mode"]
    rc.shas["https://github.com/a/one"] = "sha-1"
    rc.post(f"/api/events/{ev['id']}/evaluate", json={"skip_unchanged": True})
    wait_idle(rc, ev["id"])
    assert len(rc.calls) == n + 1


# ---------- checkpoints ----------
def test_checkpoints_run_cheaply_on_new_commits_only_and_never_rank(rc):
    ev = mk_event(rc)
    now = utcnow()
    rc.post(f"/api/events/{ev['id']}/rounds", json={"name": "Build", "starts_at": iso(now - timedelta(hours=1)), "ends_at": iso(now + timedelta(hours=8)),
                                                    "checkpoint_minutes": 30})
    t = team(rc, ev["id"], "T1", "https://github.com/a/one")
    team(rc, ev["id"], "T2", "https://github.com/a/two")
    team(rc, ev["id"], "T3", None)                                              # no repo: nothing to poll
    out = rc.app.state.tick(now)
    assert list(out["checkpoints"].values()) == [2]
    wait_calls(rc, 2)
    assert {c["model"] for c in rc.calls} == {"offline"} and all(c["ctx"] is False for c in rc.calls)   # no Gemini, no pitch check
    wait_idle(rc, ev["id"])
    assert rc.app.state.tick(now + timedelta(minutes=10))["checkpoints"] == {}  # not due yet
    out = rc.app.state.tick(now + timedelta(minutes=31))
    assert list(out["checkpoints"].values()) == [2]
    time.sleep(0.6)
    assert len(rc.calls) == 2                                                   # same commits: nothing re-analysed
    rc.shas["https://github.com/a/one"] = "sha-1"
    rc.app.state.tick(now + timedelta(minutes=62))
    wait_calls(rc, 3)
    assert len(rc.calls) == 3 and rc.calls[-1]["url"].endswith("/one")
    cps = rc.get(f"/api/teams/{t['id']}/checkpoints").json()
    assert [c["kind"] for c in cps] == ["checkpoint", "checkpoint"] and [c["sha"] for c in cps] == ["sha-0", "sha-1"]
    lb = rc.get(f"/api/events/{ev['id']}/leaderboard").json()["rows"]
    assert all(r["evaluation_id"] is None and r["final"] is None for r in lb)   # checkpoints never reach the leaderboard
    assert rc.get(f"/api/teams/{t['id']}").json()["evaluation"] is None
    assert rc.get(f"/api/events/{ev['id']}/usage").json()["total"] == 0


# ---------- judging and advancing across rounds ----------
def test_two_rounds_judge_scores_are_per_round_and_only_advanced_teams_continue(rc):
    ev = mk_event(rc)
    now = utcnow()
    for i in (1, 2, 3):
        team(rc, ev["id"], f"T{i}", f"https://github.com/a/t{i}")
    rs = rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "submit_finals", "start": iso(now - timedelta(hours=3)), "hours": [2, 40]}).json()
    r1, r2 = rs
    rc.patch(f"/api/rounds/{r1['id']}", json={"advance": {"mode": "top_n", "value": 2}})
    rc.post(f"/api/events/{ev['id']}/evaluate?round={r1['id']}")
    wait_idle(rc, ev["id"], 15)
    code = rc.post(f"/api/events/{ev['id']}/judges", json={"name": "J"}).json()["path"].split("/")[-1]
    sess = rc.get(f"/public/judge/{code}/session").json()
    assert sess["round"]["name"] == "Finals"                                      # latest started round is the default
    sess1 = rc.get(f"/public/judge/{code}/session?round={r1['id']}").json()
    assert len(sess1["teams"]) == 3 and len(sess1["criteria"]) == 8
    levels = {"T1": 9, "T2": 6, "T3": 3}
    teams = {x["code"]: x["id"] for x in sess1["teams"]}
    for c_, tid in teams.items():
        sc = {x["id"]: {"score": levels[c_], "note": "judged in the first round for this reason"} for x in sess1["criteria"]}
        assert rc.put(f"/public/judge/{code}/teams/{tid}/score?round={r1['id']}", json={"scores": sc, "status": "submitted"}).status_code == 200
    # the finals have no members until round 1 is advanced
    assert rc.get(f"/public/judge/{code}/session").json()["teams"] == []
    assert rc.post(f"/api/rounds/{r1['id']}/advance").status_code == 200
    adv = {x["team_id"]: x for x in rc.post(f"/api/rounds/{r1['id']}/advance").json()["teams"]}
    assert [adv[teams[c]]["advanced"] for c in ("T1", "T2", "T3")] == [True, True, False] and adv[teams["T1"]]["rank"] == 1
    fin = rc.get(f"/public/judge/{code}/session").json()
    assert sorted(t["code"] for t in fin["teams"]) == ["T1", "T2"] and {c["id"] for c in fin["criteria"]} == {"system_design", "innovation_creativity", "impact_usefulness", "demo_presentation"}
    # same judge, same team, second round: a separate scorecard
    sc2 = {x["id"]: {"score": 5, "note": "finals scorecard, judged live"} for x in fin["criteria"]}
    assert rc.put(f"/public/judge/{code}/teams/{teams['T1']}/score", json={"scores": sc2, "status": "submitted"}).status_code == 200
    lb1 = {r["code"]: r for r in rc.get(f"/api/events/{ev['id']}/leaderboard?round={r1['id']}").json()["rows"]}
    lb2 = {r["code"]: r for r in rc.get(f"/api/events/{ev['id']}/leaderboard?round={r2['id']}").json()["rows"]}
    assert lb1["T1"]["final"] == 90.0 and set(lb2) == {"T1", "T2"} and lb2["T1"]["final"] == 50.0 and lb2["T1"]["rank"] == 1
    # an organizer decision survives a re-run of the rule
    rc.patch(f"/api/rounds/{r1['id']}/teams/{teams['T3']}", json={"advanced": True})
    again = {x["team_id"]: x for x in rc.post(f"/api/rounds/{r1['id']}/advance").json()["teams"]}
    assert again[teams["T3"]]["advanced"] is True and again[teams["T3"]]["decided_by"] == "organizer"
    # public page: shortlist only when announced
    rc.put(f"/api/events/{ev['id']}/results", json={"public": False})
    tok = rc.get(f"/api/events/{ev['id']}").json()["results_path"].split("/")[-1]
    assert rc.get(f"/public/results/{tok}").json()["shortlists"] == []
    rc.patch(f"/api/rounds/{r1['id']}", json={"announced": True})
    sl = rc.get(f"/public/results/{tok}").json()["shortlists"]
    assert sl[0]["round"] == "Hackathon" or sl[0]["round"] == "Submission"
    assert sorted(x["code"] for x in sl[0]["advanced"]) == ["T1", "T2", "T3"] and "final" not in str(sl)


def test_advance_waits_for_running_evaluations(rc):
    ev = mk_event(rc)
    rc.ev.sleep = 0.6
    team(rc, ev["id"], "T1", "https://github.com/a/one")
    r = rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "single"}).json()[0]
    rc.post(f"/api/events/{ev['id']}/evaluate")
    assert rc.post(f"/api/rounds/{r['id']}/advance").status_code == 409
    wait_idle(rc, ev["id"])
    assert rc.post(f"/api/rounds/{r['id']}/advance").status_code == 200


def test_round_with_work_cannot_be_deleted(rc):
    ev = mk_event(rc)
    team(rc, ev["id"], "T1", "https://github.com/a/one")
    r = rc.post(f"/api/events/{ev['id']}/rounds/preset", json={"preset": "single"}).json()[0]
    rc.post(f"/api/events/{ev['id']}/evaluate")
    wait_idle(rc, ev["id"])
    assert rc.delete(f"/api/rounds/{r['id']}").status_code == 409
    e2 = mk_event(rc)
    r2 = rc.post(f"/api/events/{e2['id']}/rounds", json={"name": "x"}).json()
    assert rc.delete(f"/api/rounds/{r2['id']}").status_code == 204


# ---------- speed ----------
def test_many_teams_run_in_parallel_and_progress_reports_eta(rc):
    ev = mk_event(rc)
    rc.ev.sleep = 0.15
    for i in range(48):
        team(rc, ev["id"], f"T{i:02d}", f"https://github.com/a/t{i}")
    t0 = time.time()
    r = rc.post(f"/api/events/{ev['id']}/evaluate").json()
    assert len(r["started"]) == 48
    p = rc.get(f"/api/events/{ev['id']}/progress").json()
    assert p["workers"] == 16 and p["teams"] == 48
    wait_idle(rc, ev["id"], 20)
    took = time.time() - t0
    assert rc.live["max"] >= 8                                                  # genuinely concurrent
    assert took < 48 * 0.15 / 4                                                 # serial would take 7.2s; 16 workers well under 2s of work
    p = rc.get(f"/api/events/{ev['id']}/progress").json()
    assert p["done"] == 48 and p["eta_seconds"] == 0 and p["avg_seconds_per_run"] >= 0.1


def test_gemini_concurrency_gate_is_configurable(monkeypatch):
    from auditcore import llm
    llm._sem.clear()
    monkeypatch.setenv("GEMINI_CONCURRENCY", "2")
    sem = llm._gate()
    assert sem._value == 2
    llm._sem.clear()


def test_token_pool_rotates(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKENS", "a, b ,c")
    assert ingest.github_tokens() == ["a", "b", "c"]
    seen = {ingest.next_token() for _ in range(6)}
    assert seen == {"a", "b", "c"}
    monkeypatch.delenv("GITHUB_TOKENS")
    monkeypatch.setenv("GITHUB_TOKEN", "solo")
    assert ingest.next_token() == "solo"


def test_search_results_are_cached_across_teams(monkeypatch):
    from auditcore.analyzers import landscape
    landscape._qcache.clear()
    n = []
    fetch = lambda url, token: n.append(url) or {"items": []}
    landscape.github_search("safe routes at night", None, fetch=fetch)
    landscape.github_search("Safe Routes At Night", None, fetch=fetch)
    assert len(n) == 1


# ---------- git-level behaviour ----------
def test_clone_can_check_out_the_repo_as_of_a_deadline(tmp_path):
    import os
    import subprocess
    src = tmp_path / "src"
    src.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    run = lambda *a, e=None: subprocess.run(a, cwd=src, check=True, capture_output=True, env={**env, **(e or {})})
    run("git", "init", "-q")
    for name, when in (("before.txt", "2026-10-10T10:00:00"), ("after.txt", "2026-10-12T10:00:00")):
        (src / name).write_text(name)
        run("git", "add", "-A")
        run("git", "commit", "-qm", name, e={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    import auditcore.ingest as ing
    orig = ing.subprocess.run

    def local_clone(cmd, *a, **k):
        if cmd[:2] == ["git", "clone"]:
            cmd = cmd[:-2] + [str(src), cmd[-1]]
        return orig(cmd, *a, **k)
    ing.subprocess.run = local_clone
    ing.fetch_meta = lambda o, n: {}
    try:
        repo = ing.clone("https://github.com/o/n", before="2026-10-11T00:00:00")
        assert os.path.exists(os.path.join(repo.path, "before.txt")) and not os.path.exists(os.path.join(repo.path, "after.txt"))
        assert repo.meta["commits_after_snapshot"] == 1 and repo.meta["evaluated_sha"] != repo.meta["head_sha"]
        repo.cleanup()
        repo = ing.clone("https://github.com/o/n")
        assert os.path.exists(os.path.join(repo.path, "after.txt")) and repo.meta["evaluated_sha"] == repo.meta["head_sha"]
        repo.cleanup()
    finally:
        ing.subprocess.run = orig


def test_old_judge_scores_table_is_migrated(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    url = f"sqlite:///{tmp_path}/old.db"
    e = create_engine(url)
    with e.begin() as cx:
        cx.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, name VARCHAR(200), rubric JSON, created_at DATETIME)"))
        cx.execute(text("CREATE TABLE teams (id INTEGER PRIMARY KEY, event_id INTEGER, code VARCHAR(40), created_at DATETIME)"))
        cx.execute(text("CREATE TABLE judges (id INTEGER PRIMARY KEY, event_id INTEGER, name VARCHAR(120), code VARCHAR(60), created_at DATETIME)"))
        cx.execute(text("CREATE TABLE judge_scores (id INTEGER PRIMARY KEY, event_id INTEGER, team_id INTEGER, judge_id INTEGER, scores JSON, comment TEXT, "
                        "conflict BOOLEAN, status VARCHAR(12), history JSON, updated_at DATETIME, UNIQUE (team_id, judge_id))"))
        cx.execute(text("INSERT INTO events (id, name, rubric) VALUES (1, 'E', '{}')"))
        cx.execute(text("INSERT INTO teams (id, event_id, code) VALUES (1, 1, 'T1')"))
        cx.execute(text("INSERT INTO judges (id, event_id, name, code) VALUES (1, 1, 'J', 'abcdefgh1234')"))
        cx.execute(text("INSERT INTO judge_scores (id, event_id, team_id, judge_id, scores, status) VALUES (1, 1, 1, 1, '{}', 'submitted')"))
    e.dispose()
    eng = db.make_engine(url)
    assert not [u for u in inspect(eng).get_unique_constraints("judge_scores") if set(u["column_names"]) == {"team_id", "judge_id"}]
    with eng.begin() as cx:
        assert cx.execute(text("SELECT status FROM judge_scores WHERE id = 1")).scalar() == "submitted"       # data kept
        cx.execute(text("INSERT INTO judge_scores (id, event_id, team_id, judge_id, round_id, scores, conflict, status, history, updated_at) VALUES (2, 1, 1, 1, 7, '{}', 0, 'draft', '[]', CURRENT_TIMESTAMP)"))   # second round OK
