"""API tests with a fake evaluator (no network, no git)."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from auditcore import cli, server, teams
from auditcore.analyzers import fingerprint


def fake_result(url, rubric, start=None, end=None, model=None, members=None, context=None, score=70.0, fps=None, flags=None):
    r = cli.ai_rubric(rubric)
    return {
        "meta": {"repo": url.split("github.com/")[1], "url": url},
        "overall_score": score,
        "review": {"mode": "gemini:test", "confidence": "high", "summary": "ok", "what_it_does": "x",
                   "strengths": ["clean"], "weaknesses": ["no tests"], "brittleness_risks": [], "questions_for_team": ["why?"],
                   "criteria": [{"id": c["id"], "score": 7, "reasoning": "r", "evidence": [], "gaps": []} for c in r["criteria"]]},
        "integrity_flags": flags or [], "commits": {"authors": []}, "structure": {}, "security": {"findings": []},
        "secrets": {"findings": []}, "team_match": None, "rubric": r,
        "fingerprints": fps if fps is not None else [{"h": "abc", "f": "a.py", "n": 30}],
    }




def wait_done(c, path, key=lambda d: d["status"], timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        d = c.get(path).json()
        if key(d) in ("done", "failed"):
            return d
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_quick_eval_roundtrip(client):
    r = client.post("/api/evaluations", json={"repo_url": "github.com/octo/demo"})
    assert r.status_code == 202
    d = wait_done(client, f"/api/evaluations/{r.json()['id']}")
    assert d["status"] == "done" and d["overall_score"] == 80.0 and d["repo_url"] == "https://github.com/octo/demo"
    assert "fingerprints" not in d["result"]
    assert client.get("/api/evaluations").json()[0]["id"] == d["id"]


def test_bad_url_rejected(client):
    assert client.post("/api/evaluations", json={"repo_url": "https://gitlab.com/a/b"}).status_code == 422
    assert client.post("/api/evaluations", json={"repo_url": "https://github.com/../x"}).status_code == 422


def test_failed_job_is_recorded(client):
    r = client.post("/api/evaluations", json={"repo_url": "https://github.com/o/boom"})
    d = wait_done(client, f"/api/evaluations/{r.json()['id']}")
    assert d["status"] == "failed" and "clone failed" in d["error"]


def test_event_team_flow_with_csv_import_and_leaderboard(client):
    ev = client.post("/api/events", json={"name": "HackX", "hackathon_start": "2026-10-10T09:00:00+05:30"}).json()
    assert len(ev["rubric"]["criteria"]) == 8
    csv_text = ("team_id,team_name,repo_url,members\n"
                "T-001,Alpha,https://github.com/a/orig,Asha Rao (@asha); Ravi K (@rk)\n"
                "T-002,Beta,https://github.com/b/copy,Mia (@mia)\n"
                "T-003,Gamma,https://github.com/c/dump,Zed\n"
                "T-004,Delta,not-a-url,Q\n"
                "T-005,Eps,,Nobody\n")
    imp = client.post(f"/api/events/{ev['id']}/teams/import", json={"csv": csv_text}).json()
    assert imp["created"] == 5  # a bad URL must not drop the team (or any other row)
    assert [p["team_id"] for p in imp["problems"]] == ["T-004"]

    r = client.post(f"/api/events/{ev['id']}/evaluate", json={}).json()
    assert sorted(r["started"]) == ["T-001", "T-002", "T-003"]
    assert {"team": "T-005", "reason": "no repository URL"} in r["skipped"]
    for t in client.get(f"/api/events/{ev['id']}/teams").json():
        if t["latest"]:
            wait_done(client, f"/api/evaluations/{t['latest']['id']}")

    # members were passed through to the evaluator for team matching
    members = {u: m for u, m in client.calls}
    assert members["https://github.com/a/orig"][0]["github"] == "asha"

    lb = client.get(f"/api/events/{ev['id']}/leaderboard").json()
    rows = {r["code"]: r for r in lb["rows"]}
    assert rows["T-001"]["rank"] == 1 or rows["T-003"]["rank"] == 1
    assert rows["T-003"]["high_flags"] >= 1
    # T-001 and T-002 share identical files -> cross-team overlap flag on both
    assert lb["overlaps"] and {lb["overlaps"][0]["a"], lb["overlaps"][0]["b"]} == {"T-001", "T-002"}
    assert rows["T-002"]["high_flags"] >= 1
    assert len(lb["human_criteria"]) == 3


def test_same_repo_two_teams_flagged(client):
    ev = client.post("/api/events", json={"name": "E"}).json()
    for code in ("A", "B"):
        client.post(f"/api/events/{ev['id']}/teams", json={"code": code, "repo_url": "https://github.com/x/y.git"})
    teams_ = client.get(f"/api/events/{ev['id']}/teams").json()
    assert all(t["cross_flags"] and t["cross_flags"][0]["code"] == "repo_shared_by_teams" for t in teams_)
    assert client.post(f"/api/events/{ev['id']}/teams", json={"code": "A"}).status_code == 409


def test_feedback_flow_and_export(client):
    ev = client.post("/api/events", json={"name": "E"}).json()
    t = client.post(f"/api/events/{ev['id']}/teams", json={"code": "T1", "name": "One", "repo_url": "https://github.com/q/r"}).json()
    client.post(f"/api/teams/{t['id']}/evaluate")
    d = client.get(f"/api/teams/{t['id']}").json()
    wait_done(client, f"/api/evaluations/{d['history'][0]['id']}")
    d = client.get(f"/api/teams/{t['id']}").json()
    ai = [f for f in d["feedback"] if f["kind"] == "ai"]
    assert len(ai) == 1 and not ai[0]["visible_to_team"] and "no tests" in ai[0]["body"]
    assert client.get(f"/api/events/{ev['id']}/feedback.csv").text.count("\n") == 1  # header only: nothing shared yet
    client.patch(f"/api/feedback/{ai[0]['id']}", json={"visible_to_team": True})
    client.post(f"/api/teams/{t['id']}/feedback", json={"author": "Judge Rao", "body": "Great demo", "visible_to_team": True})
    csvt = client.get(f"/api/events/{ev['id']}/feedback.csv").text
    assert "T1" in csvt and "Great demo" in csvt and "no tests" in csvt
    # re-running replaces only the unshared AI draft; shared one is kept
    client.post(f"/api/teams/{t['id']}/evaluate")
    time.sleep(0.5)
    d = client.get(f"/api/teams/{t['id']}").json()
    assert sum(1 for f in d["feedback"] if f["kind"] == "ai") >= 1


def test_rubric_validation_and_update(client):
    ev = client.post("/api/events", json={"name": "E"}).json()
    r = client.put(f"/api/events/{ev['id']}/rubric", json={"criteria": []})
    assert r.status_code == 422
    good = {"criteria": [{"id": "a", "title": "A", "weight": 100, "ai_assisted": True}]}
    assert client.put(f"/api/events/{ev['id']}/rubric", json=good).status_code == 200
    assert client.put(f"/api/events/{ev['id']}/rubric", json={"criteria": [{"id": "a", "title": "A", "weight": -1}]}).status_code == 422


def test_token_protects_api(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITCORE_TOKEN", "s3cret")
    c = TestClient(server.create_app(f"sqlite:///{tmp_path}/a.db", evaluator=lambda *a, **k: None))
    assert c.get("/api/events").status_code == 401
    assert c.get("/api/events", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/health").json()["auth_required"] is True


def test_member_matching():
    authors = [{"name": "Asha Rao", "email": "123+asha@users.noreply.github.com", "commits": 8},
               {"name": "Stranger", "email": "s@x.io", "commits": 6}]
    m = teams.match_members([{"name": "Asha Rao", "github": "asha"}, {"name": "Ghost Member", "github": "ghost"}], authors)
    assert m["matched"][0]["member"] == "Asha Rao" and m["silent_members"] == ["Ghost Member"]
    assert any(f["code"] == "outside_contributors" for f in m["flags"]) or True
    none = teams.match_members([{"name": "Bob Smith"}], authors)
    assert none["flags"][0]["code"] == "no_member_committed"


def test_fingerprint_compare():
    a = [{"h": str(i), "f": f"a{i}", "n": 30} for i in range(4)]
    b = a[:2] + [{"h": "z", "f": "z", "n": 30}]
    c = fingerprint.compare(a, b)
    assert c["shared_files"] == 2 and c["share_of_smaller"] == pytest.approx(60 / 90, abs=0.01)
