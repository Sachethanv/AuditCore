"""Repository structure and engineering-hygiene signals."""
from __future__ import annotations

import os
from collections import Counter

from ..ingest import iter_files, read_text

LANG = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
        ".cs": "C#", ".cpp": "C++", ".c": "C", ".swift": "Swift", ".dart": "Dart", ".vue": "Vue",
        ".html": "HTML", ".css": "CSS", ".sol": "Solidity", ".ipynb": "Jupyter"}
MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "Pipfile", "pom.xml", "build.gradle",
             "go.mod", "Cargo.toml", "Gemfile", "composer.json", "pubspec.yaml")
LOCKS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock")
ENTRY_HINTS = ("main", "app", "server", "index", "manage", "wsgi", "asgi", "routes", "api", "__main__")


def analyze(root: str) -> dict:
    langs: Counter = Counter()
    all_files, manifests, locks = [], [], []
    ci = docker = license_ = tests_cfg = False
    readme = None
    for rel, ap in iter_files(root):
        all_files.append(rel)
        base = os.path.basename(rel)
        ext = os.path.splitext(base)[1].lower()
        if ext in LANG:
            t = read_text(ap)
            langs[LANG[ext]] += sum(1 for l in (t or "").splitlines() if l.strip())
        if base in MANIFESTS:
            manifests.append(rel)
        if base in LOCKS:
            locks.append(rel)
        if rel.startswith(".github/workflows/") or base in (".gitlab-ci.yml", "Jenkinsfile", ".travis.yml") or ".circleci/" in rel:
            ci = True
        if base.lower() in ("dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml"):
            docker = True
        if base.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            license_ = True
        if base in ("pytest.ini", "tox.ini", "jest.config.js", "jest.config.ts", "vitest.config.ts", "conftest.py"):
            tests_cfg = True
        if rel.lower() in ("readme.md", "readme.rst", "readme.txt", "readme"):
            readme = read_text(ap)

    readme = readme or ""
    rl = readme.lower()
    return {
        "file_count": len(all_files),
        "languages_loc": dict(langs.most_common(8)),
        "manifests": manifests[:10],
        "has_lockfile": bool(locks),
        "has_ci": ci, "has_docker": docker, "has_license": license_, "has_test_config": tests_cfg,
        "readme": {"present": bool(readme), "chars": len(readme),
                   "has_setup_instructions": any(k in rl for k in ("install", "setup", "getting started", "npm ", "pip ", "docker")),
                   "has_run_instructions": any(k in rl for k in ("usage", "run ", "npm start", "python ", "uvicorn", "docker compose")),
                   "mentions_architecture": any(k in rl for k in ("architecture", "diagram", "design", "tech stack", "built with")),
                   "mentions_demo": any(k in rl for k in ("demo", "screenshot", "live at", "deployed"))},
        "entry_candidates": [f for f in all_files if os.path.splitext(os.path.basename(f))[0].lower() in ENTRY_HINTS][:15],
        "tree_sample": sorted(all_files)[:300],
    }
