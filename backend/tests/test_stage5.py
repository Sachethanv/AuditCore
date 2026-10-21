"""Stage 5: rubric v3 + AI-code baseline, design questions, landscape research, token budget, 'why not me' answers."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from auditcore import llm, server
from auditcore.analyzers import landscape
from auditcore.paths import RUBRIC_PATH
from test_server import fake_result, wait_done  # noqa: F401
from test_stage3 import done_team, mk_event

RUBRIC = json.load(open(RUBRIC_PATH, encoding="utf-8"))


# ---------- rubric + baseline ----------
def test_rubric_v3_shape():
    ids = {c["id"]: c for c in RUBRIC["criteria"]}
    assert "documentation" not in ids and "technical_implementation" not in ids
    assert sum(c["weight"] for c in RUBRIC["criteria"]) == 100
    assert ids["innovation_creativity"]["weight"] > ids["code_quality"]["weight"] * 3 and not ids["innovation_creativity"]["ai_assisted"]
    assert ids["system_design"]["ai_assisted"] and ids["code_quality"]["baseline_cap"] == 7


def review(scores, decisions=0, mode="gemini:x"):
    return {"mode": mode, "criteria": [{"id": k, "score": v} for k, v in scores.items()],
            "design_decisions": [{"decision": f"d{i}"} for i in range(decisions)]}


def test_baseline_caps_polish_and_unproven_design():
    r = llm.apply_baseline(RUBRIC, review({"code_quality": 10, "robustness": 9, "security_config": 10, "system_design": 10, "completeness": 9}, decisions=1))
    by = {c["id"]: c for c in r["criteria"]}
    assert by["code_quality"]["score"] == 7 and by["code_quality"]["uncapped_score"] == 10 and by["code_quality"]["capped"]
    assert by["robustness"]["score"] == 7 and by["security_config"]["score"] == 8
    assert by["system_design"]["score"] == 6          # 5 + one verified decision
    assert by["completeness"]["score"] == 9 and "capped" not in by["completeness"]  # not a polish criterion
    assert len(r["baseline_notes"]) == 4


def test_system_design_cap_needs_evidence_and_maxes_at_8():
    assert {c["id"]: c["score"] for c in llm.apply_baseline(RUBRIC, review({"system_design": 10}, decisions=0))["criteria"]}["system_design"] == 5
    assert {c["id"]: c["score"] for c in llm.apply_baseline(RUBRIC, review({"system_design": 10}, decisions=9))["criteria"]}["system_design"] == 8
    # the offline heuristic never gives more than the baseline
    assert {c["id"]: c["score"] for c in llm.apply_baseline(RUBRIC, review({"system_design": 9}, decisions=9, mode="heuristic"))["criteria"]}["system_design"] == 5


def test_design_decisions_are_verified_against_files(tmp_path):
    (tmp_path / "app.py").write_text("queue = JobQueue()\nworker.start()\n")
    rv = {"criteria": [], "design_decisions": [
        {"decision": "queue decouples upload from analysis", "file": "app.py", "line": 1, "quote": "queue = JobQueue()"},
        {"decision": "invented", "file": "app.py", "line": 9, "quote": "redis cluster sharding"},
        {"decision": "missing file", "file": "nope.py", "line": 1, "quote": "x"}],
        "design_questions": [{"question": "Why a queue?", "listen_for": "x"}, {"nope": 1}]}
    out = llm.verify_evidence(str(tmp_path), rv)
    assert [d["decision"] for d in out["design_decisions"]] == ["queue decouples upload from analysis"]
    assert [q["question"] for q in out["design_questions"]] == ["Why a queue?"]


def test_offline_review_still_offers_design_questions():
    facts = {"structure": {"languages_loc": {"Python": 900, "JavaScript": 400}, "has_docker": True, "file_count": 20, "has_ci": False,
                           "has_lockfile": False, "readme": {"chars": 10, "has_run_instructions": False, "mentions_demo": False,
                                                              "has_setup_instructions": False, "mentions_architecture": False, "present": True}},
             "security": {"findings": [], "test_files": 0, "code_files": 10, "code_loc": 1300},
             "secrets": {"finding_count": 0, "hygiene": []}, "commits": {}}
    r = llm.heuristic_review(RUBRIC, facts)
    assert len(r["design_questions"]) >= 3 and any("containeris" in q["question"] for q in r["design_questions"])
    assert "documentation" not in {c["id"] for c in r["criteria"]}


# ---------- landscape ----------
PROJECT = {"title": "SafeRoute", "summary": "Maps safe walking routes at night for women using crowd reports", "problem": "Commuters feel unsafe at night", "what_it_does": None}


def fake_search(q, token=None):
    return [{"repo": "me/saferoute", "url": "u", "stars": 1, "description": "same repo", "language": "Py", "topics": [], "pushed_at": None, "archived": False},
            {"repo": "x/safe-walk", "url": "u1", "stars": 900, "description": "safe walking routes at night with crowd reports", "language": "Py", "topics": ["maps", "safety"], "pushed_at": None, "archived": False},
            {"repo": "y/todo", "url": "u2", "stars": 5000, "description": "a todo list", "language": "JS", "topics": [], "pushed_at": None, "archived": False}]


def test_landscape_offline_finds_and_excludes_own_repo():
    res = landscape.research(PROJECT, "me/saferoute", search=fake_search)
    assert res["status"] == "done" and res["mode"] == "offline"
    names = [c["repo"] for c in res["candidates"]]
    assert "me/saferoute" not in names and names[0] == "x/safe-walk"      # most similar first, not most starred
    assert res["assessment"]["novelty"] == "crowded" and res["assessment"]["closest"][0]["repo"] == "x/safe-walk"
    assert res["usage"]["calls"] == 0 and "Searched GitHub only" in res["caveat"]


def test_landscape_no_match_is_not_called_novel():
    res = landscape.research(PROJECT, None, search=lambda q, t=None: [])
    assert res["status"] == "done" and res["assessment"]["novelty"] == "unclear"


def test_landscape_search_failure_is_reported_not_raised():
    def boom(q, t=None):
        raise OSError("rate limited")
    res = landscape.research(PROJECT, None, search=boom)
    assert res["status"] == "unavailable" and res["notes"]


def test_landscape_gemini_cannot_invent_repos(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def fake_call(prompt, model, key, **kw):
        calls.append(prompt)
        if "search queries" in prompt:
            return {"queries": ["safe routes night", "crowd safety map"]}
        return {"novelty": "incremental", "summary": "close to safe-walk", "closest": [
            {"repo": "x/safe-walk", "why_similar": "same", "how_different": "adds deck"}, {"repo": "ghost/invented", "why_similar": "?", "how_different": "?"}],
            "differentiators": ["pitch deck"], "judge_question": "q"}
    monkeypatch.setattr(llm, "_call_gemini", fake_call)
    res = landscape.research(PROJECT, "me/saferoute", model="m", search=fake_search)
    assert len(calls) == 2                                              # exactly two LLM calls per project
    assert [c["repo"] for c in res["assessment"]["closest"]] == ["x/safe-walk"] and res["mode"] == "gemini"


def test_landscape_endpoint_caches_and_tracks_tokens(client, monkeypatch):
    n = []
    monkeypatch.setattr(landscape, "github_search", lambda q, t=None: n.append(q) or fake_search(q))
    ev = mk_event(client)
    t = client.post(f"/api/events/{ev['id']}/teams", json={"code": "T1", "name": "Alpha", "repo_url": "https://github.com/me/saferoute",
                                                           "project_title": "SafeRoute", "summary": PROJECT["summary"], "problem_statement": PROJECT["problem"]}).json()
    r = client.post(f"/api/teams/{t['id']}/landscape").json()
    assert r["status"] == "done" and r["assessment"]["novelty"] == "crowded"
    first = len(n)
    assert client.post(f"/api/teams/{t['id']}/landscape").json()["researched_at"] == r["researched_at"] and len(n) == first   # cached: no new searches
    client.post(f"/api/teams/{t['id']}/landscape?refresh=true")
    assert len(n) > first
    assert client.get(f"/api/teams/{t['id']}").json()["landscape"]["status"] == "done"
    empty = client.post(f"/api/events/{ev['id']}/teams", json={"code": "T2", "name": "Beta"}).json()
    assert client.post(f"/api/teams/{empty['id']}/landscape").status_code == 422


# ---------- token budget ----------
@pytest.fixture
def metered(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = []

    def ev(url, rubric, start=None, end=None, model=None, members=None, context=None):
        seen.append({"key": llm.api_key(), "model": model})
        if llm.api_key():
            llm._record({"usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200}})
        return fake_result(url, rubric, start, end, members=members, context=context)
    c = TestClient(server.create_app(f"sqlite:///{tmp_path}/m.db", evaluator=ev, workers=1))
    c.seen = seen
    return c


def test_usage_is_counted_and_cap_switches_to_free_review(metered):
    ev = mk_event(metered)
    assert metered.get(f"/api/events/{ev['id']}/usage").json()["total"] == 0
    metered.put(f"/api/events/{ev['id']}/budget", json={"max_tokens": 2000})
    done_team(metered, ev["id"], "T1", "https://github.com/a/one")
    u = metered.get(f"/api/events/{ev['id']}/usage").json()
    assert u["evaluations"] == 1200 and u["cap"] == 2000 and u["over_budget"] is False and u["remaining"] == 800
    done_team(metered, ev["id"], "T2", "https://github.com/a/two")            # still under the cap when it started
    assert metered.get(f"/api/events/{ev['id']}/usage").json()["over_budget"] is True
    done_team(metered, ev["id"], "T3", "https://github.com/a/three")          # over: Gemini must be off for this run
    assert [bool(x["key"]) for x in metered.seen] == [True, True, False]
    assert metered.get(f"/api/events/{ev['id']}/usage").json()["evaluations"] == 2400
    t3 = [t for t in metered.get(f"/api/events/{ev['id']}/teams").json() if t["code"] == "T3"][0]
    assert "token budget reached" in t3["latest"]["mode"]


def test_broad_and_deep_profiles_pick_models_and_top_n(metered, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_BROAD", "flash-x")
    monkeypatch.setenv("GEMINI_MODEL_DEEP", "pro-x")
    ev = mk_event(metered)
    for i in (1, 2, 3):
        done_team(metered, ev["id"], f"T{i}", f"https://github.com/a/t{i}")
    metered.seen.clear()
    r = metered.post(f"/api/events/{ev['id']}/evaluate", json={"profile": "broad"}).json()
    assert r["model"] == "flash-x" and len(r["started"]) == 3
    time.sleep(0.8)
    metered.seen.clear()
    r = metered.post(f"/api/events/{ev['id']}/evaluate", json={"profile": "deep", "top_n": 2}).json()
    assert r["model"] == "pro-x" and len(r["started"]) == 2
    time.sleep(0.8)
    assert {x["model"] for x in metered.seen} == {"pro-x"}
    assert metered.post(f"/api/events/{ev['id']}/evaluate", json={"profile": "bogus"}).status_code == 422


# ---------- results + "why not me" ----------
def judged_event(c, polish=False):
    ev = mk_event(c)
    ts = [done_team(c, ev["id"], f"T{i}", f"https://github.com/a/t{i}") for i in (1, 2, 3)]
    j = c.post(f"/api/events/{ev['id']}/judges", json={"name": "J"}).json()
    code = j["path"].split("/")[-1]
    sess = c.get(f"/public/judge/{code}/session").json()
    level = {"T1": 9, "T2": 7, "T3": 4}
    for t in ts:
        sc = {x["id"]: {"score": level[t["code"]], "note": "because the demo and the answers differed"} for x in sess["criteria"]}
        r = c.put(f"/public/judge/{code}/teams/{t['id']}/score", json={"scores": sc, "status": "submitted"})
        assert r.status_code == 200, r.text
    return ev, ts


def team_token(c, t):
    return c.get(f"/api/teams/{t['id']}").json()["submit_path"].split("/")[-1]


def test_team_sees_results_only_after_reveal_and_can_compare(client):
    ev, (t1, t2, t3) = judged_event(client)
    tok3 = team_token(client, t3)
    assert client.get(f"/public/submit/{tok3}/results").json() == {"revealed": False, "results": None}
    assert client.post(f"/public/submit/{tok3}/compare", json={"other": "T1"}).status_code == 403
    client.put(f"/api/events/{ev['id']}/results", json={"public": True})
    res = client.get(f"/public/submit/{tok3}/results").json()["results"]
    assert res["rank"] == 3 and res["of"] == 3 and res["compare"]["enabled"] is False
    assert client.post(f"/public/submit/{tok3}/compare", json={"other": "T1"}).status_code == 403   # organizer has not enabled it

    client.put(f"/api/events/{ev['id']}/budget", json={"explain_enabled": True, "explain_per_team": 1})
    res = client.get(f"/public/submit/{tok3}/results").json()["results"]
    assert res["compare"]["enabled"] and res["compare"]["left"] == 1 and [x["code"] for x in res["compare"]["teams_above"]] == ["T1", "T2"]
    a = client.post(f"/public/submit/{tok3}/compare", json={"other": "t1"})
    assert a.status_code == 200
    a = a.json()
    assert a["mode"] == "template" and a["cached"] is False and a["gap"] > 0
    assert "T1" in a["headline"] and a["behind_on"] and all(r["theirs"] > r["yours"] for r in a["behind_on"])
    assert any(r["id"] == "innovation_creativity" for r in a["behind_on"])        # judge-scored criteria drive the gap
    flat = json.dumps(a)
    assert "repo_url" not in flat and "flag" not in flat.lower()                 # nothing private about the other team
    again = client.post(f"/public/submit/{tok3}/compare", json={"other": "T1"}).json()
    assert again["cached"] is True                                                # repeat is free and does not use a slot
    assert client.post(f"/public/submit/{tok3}/compare", json={"other": "T2"}).status_code == 429   # per-team limit
    # a team cannot compare with one that finished below it, or probe unknown teams
    tok1 = team_token(client, t1)
    assert client.post(f"/public/submit/{tok1}/compare", json={"other": "T3"}).status_code == 404
    assert client.post(f"/public/submit/{tok3}/compare", json={"other": "ZZ"}).status_code == 404
    assert client.get(f"/api/events/{ev['id']}/usage").json()["explanations"] == 0   # template mode costs no tokens


def test_compare_refused_while_judging_is_incomplete(client):
    ev = mk_event(client)
    t = done_team(client, ev["id"], "T1", "https://github.com/a/t1")
    client.put(f"/api/events/{ev['id']}/results", json={"public": True})
    client.put(f"/api/events/{ev['id']}/budget", json={"explain_enabled": True})
    assert client.post(f"/public/submit/{team_token(client, t)}/compare", json={"other": "T1"}).status_code in (404, 409)


def test_optional_gemini_polish_is_cached_and_counted(client, monkeypatch):
    ev, (t1, t2, t3) = judged_event(client)
    client.put(f"/api/events/{ev['id']}/results", json={"public": True})
    client.put(f"/api/events/{ev['id']}/budget", json={"explain_enabled": True, "explain_llm": True})
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def fake_call(prompt, model, key, **kw):
        calls.append(1)
        llm._record({"usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 80}})
        return {"paragraph": "You trailed on innovation. Try a sharper twist."}
    monkeypatch.setattr(llm, "_call_gemini", fake_call)
    tok = team_token(client, t3)
    a = client.post(f"/public/submit/{tok}/compare", json={"other": "T1"}).json()
    assert a["mode"] == "template+gemini" and a["paragraph"].startswith("You trailed") and a["behind_on"]
    client.post(f"/public/submit/{tok}/compare", json={"other": "T1"})
    assert len(calls) == 1 and client.get(f"/api/events/{ev['id']}/usage").json()["explanations"] == 380


# ---------- readiness + design Q&A ----------
def test_readiness_checklist(client):
    ev = mk_event(client, tracks=["Health"])
    t = client.post(f"/api/events/{ev['id']}/teams", json={"code": "T1", "name": "A", "repo_url": "https://github.com/a/b"}).json()
    rd = client.get(f"/api/teams/{t['id']}").json()["readiness"]
    assert rd["done"] < rd["total"] and "Pitch deck uploaded" in rd["missing"] and "Track chosen" in rd["missing"]
    tok = client.get(f"/api/teams/{t['id']}").json()["submit_path"].split("/")[-1]
    client.put(f"/public/submit/{tok}/profile", json={"project_title": "P", "summary": "x" * 50, "problem_statement": "y" * 50, "track": "Health",
                                                       "links": {"demo_url": "https://demo.example.com"}})
    rd = client.get(f"/public/submit/{tok}").json()["readiness"]
    assert rd["missing"] == ["Team members listed", "Pitch deck uploaded"] or "Pitch deck uploaded" in rd["missing"]
    assert "Track chosen" not in rd["missing"] and "Demo link or video" not in rd["missing"]


def test_judge_design_qa_is_saved_and_shown_to_organizers(client):
    ev = mk_event(client)
    t = done_team(client, ev["id"], "T1", "https://github.com/a/t1")
    code = client.post(f"/api/events/{ev['id']}/judges", json={"name": "J"}).json()["path"].split("/")[-1]
    qa = [{"question": "Why SQLite?", "rating": "weak", "note": "had no answer"}, {"question": "Why a queue?", "rating": "bogus"}, {"rating": "ok"}]
    r = client.put(f"/public/judge/{code}/teams/{t['id']}/score", json={"scores": {}, "status": "draft", "qa": qa})
    assert r.status_code == 200
    my = client.get(f"/public/judge/{code}/teams/{t['id']}").json()["my"]
    assert [(q["question"], q["rating"]) for q in my["qa"]] == [("Why SQLite?", "weak"), ("Why a queue?", "not_asked")]
    assert client.get(f"/api/teams/{t['id']}").json()["judge_scores"][0]["qa"][0]["note"] == "had no answer"
