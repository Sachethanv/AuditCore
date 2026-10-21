"""Offline tests: build throwaway git repos and check each analyzer + the Gemini evidence gate."""
import os
import subprocess
import tempfile
from datetime import datetime, timezone

import pytest

from auditcore import llm, report
from auditcore.analyzers import commits, secrets, security, structure


def sh(cwd, *args, env=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, env=dict(os.environ, **(env or {})))


def make_repo(files: dict[str, str], dates: list[str] | None = None) -> str:
    d = tempfile.mkdtemp()
    sh(d, "git", "init", "-q")
    sh(d, "git", "config", "user.email", "a@b.c")
    sh(d, "git", "config", "user.name", "tester")
    groups = [files] if not dates else [files]
    for path, content in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, path)) or d, exist_ok=True)
        with open(os.path.join(d, path), "w") as f:
            f.write(content)
    sh(d, "git", "add", "-A")
    date = (dates or ["2026-10-11T12:00:00+00:00"])[0]
    sh(d, "git", "commit", "-q", "-m", "initial commit", env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
    return d


BAD = {
    "app.py": "import os, pickle, subprocess\n"
              "API_KEY = 'AIzaSyA1234567890abcdefghijklmnopqrstuv1'\n"
              "def q(cur, uid):\n    cur.execute(f\"SELECT * FROM users WHERE id = {uid}\")\n"
              "def run(cmd):\n    subprocess.run(cmd, shell=True)\n"
              "def load(b):\n    return pickle.loads(b)\n" + "x = 1\n" * 600,
    ".env": "SECRET_KEY=hunter2hunter2hunter2\n",
}


def test_bulk_commit_and_pre_existing_flags():
    repo = make_repo(BAD, ["2026-09-01T12:00:00+00:00"])
    m = commits.analyze(repo, hackathon_start=datetime(2026, 10, 10, tzinfo=timezone.utc))
    codes = {f["code"] for f in m["flags"]}
    assert "tiny_history" in codes or "bulk_commit" in codes
    assert "pre_existing_work" in codes


def test_secrets_detected_and_redacted():
    repo = make_repo(BAD)
    r = secrets.analyze(repo)
    assert r["finding_count"] >= 1
    assert all("AIzaSyA1234567890" not in str(f["match"]) for f in r["findings"])  # never echo the secret
    assert any(h["code"] == "env_file_committed" for h in r["hygiene"])


def test_placeholders_not_flagged():
    repo = make_repo({"cfg.py": "api_key = 'your-api-key-here-example'\npassword = os.environ['PW']\n"})
    assert secrets.analyze(repo)["finding_count"] == 0


def test_security_rules():
    repo = make_repo(BAD)
    rules = set(security.analyze(repo)["rule_counts"])
    assert {"shell_true", "pickle_load"} <= rules
    assert rules & {"sql_concat", "sql_concat2"}


def test_localhost_with_env_fallback_not_flagged_but_hardcoded_is():
    repo = make_repo({"a.js": "export const A = process.env.API_URL ?? 'http://localhost:8000'\n",
                      "b.js": "fetch('http://localhost:8000/api/items')\n"})
    found = [f["file"] for f in security.analyze(repo)["findings"] if f["rule"] == "hardcoded_localhost"]
    assert found == ["b.js"]


def test_structure_basics():
    repo = make_repo({"README.md": "# T\n## Install\npip install x\n## Usage\nrun it\n", "main.py": "print(1)\n",
                      "requirements.txt": "flask\n"})
    s = structure.analyze(repo)
    assert s["readme"]["has_setup_instructions"] and "requirements.txt" in s["manifests"]


def test_evidence_verification_drops_fabricated_citations():
    repo = make_repo({"main.py": "def real_function():\n    return 42\n"})
    fake = {"criteria": [{"id": "technical_execution", "score": 9, "evidence": [
        {"file": "main.py", "line": 1, "quote": "def real_function():"},
        {"file": "main.py", "line": 9, "quote": "def invented_function():"},
        {"file": "ghost.py", "line": 1, "quote": "x"}]}], "confidence": "high"}
    out = llm.verify_evidence(repo, fake)
    assert out["evidence_stats"] == {"cited": 3, "verified": 1}
    assert len(out["criteria"][0]["evidence"]) == 1
    assert out["confidence"] == "low"  # most citations were fabricated


def test_gemini_path_with_mock(monkeypatch):
    repo = make_repo({"main.py": "def real_function():\n    return 42\n"})
    rubric = {"criteria": [{"id": "technical_execution", "weight": 100, "title": "T", "description": "d"}]}
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(llm, "_call_gemini", lambda *a, **k: {
        "summary": "s", "criteria": [{"id": "technical_execution", "score": 7, "reasoning": "r",
        "evidence": [{"file": "main.py", "line": 1, "quote": "def real_function():"}], "gaps": []}], "confidence": "high"})
    facts = {"structure": structure.analyze(repo), "security": security.analyze(repo),
             "secrets": secrets.analyze(repo), "commits": {}}
    rev = llm.review(repo, rubric, facts)
    assert rev["mode"].startswith("gemini:") and report.weighted_score(rubric, rev) == 70.0


def test_heuristic_review_runs_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    repo = make_repo(BAD)
    rubric = {"criteria": [{"id": "security_config", "weight": 100, "title": "S", "description": "d"}]}
    facts = {"structure": structure.analyze(repo), "security": security.analyze(repo),
             "secrets": secrets.analyze(repo), "commits": {}}
    rev = llm.review(repo, rubric, facts)
    assert rev["mode"].startswith("heuristic") and rev["criteria"][0]["score"] <= 5
