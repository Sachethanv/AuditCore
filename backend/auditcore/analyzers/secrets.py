"""Secret and environment-variable hygiene checks.

Uses gitleaks when installed (better coverage, scans history); otherwise a built-in
pattern scan of the working tree. Matches are always redacted in output.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import os

from ..ingest import git, iter_files, read_text

PATTERNS = [
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "high"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "high"),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[0-9A-Za-z_]{30,}\b"), "high"),
    ("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}\b"), "high"),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}\b"), "high"),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), "high"),
    ("db_url_with_password", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/@]+:[^\s@]{3,}@"), "high"),
    ("generic_secret_assignment",
     re.compile(r"""(?i)\b(?:api[_-]?key|secret|passwd|password|auth[_-]?token|access[_-]?token)\b\s*[:=]\s*["']([^"'\s]{12,})["']"""),
     "medium"),
]
PLACEHOLDER = re.compile(r"(?i)(your|example|changeme|xxx|<.*>|\$\{|process\.env|os\.environ|placeholder|dummy|test|sample|\*{3,}|\.{3})")
ENV_USAGE = re.compile(r"os\.environ|os\.getenv|process\.env|System\.getenv|dotenv|load_dotenv|env::var|ENV\[")
SKIP_SUFFIX = (".lock", ".min.js", ".svg", ".map", ".md", ".ipynb")


def _redact(s: str) -> str:
    return s[:4] + "…" + f"({len(s)} chars)"


def _builtin_scan(root: str) -> list[dict]:
    findings = []
    for rel, ap in iter_files(root):
        if rel.endswith(SKIP_SUFFIX) or rel.lower().endswith((".example", ".sample", ".template")):
            continue
        text = read_text(ap)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if len(line) > 2000:
                continue
            for name, rx, sev in PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                val = m.group(1) if m.groups() and name == "generic_secret_assignment" else m.group(0)
                if name == "generic_secret_assignment" and PLACEHOLDER.search(val):
                    continue
                findings.append({"severity": sev, "rule": name, "file": rel, "line": i,
                                 "match": _redact(val)})
                break
    return findings


def _gitleaks_scan(root: str) -> list[dict] | None:
    exe = shutil.which("gitleaks")
    if not exe:
        return None
    fd, report = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        subprocess.run([exe, "detect", "--source", root, "--report-format", "json",
                        "--report-path", report, "--no-banner", "--exit-code", "0"],
                       capture_output=True, timeout=300)
        with open(report) as f:
            raw = json.load(f) or []
    except Exception:
        return None
    finally:
        os.path.exists(report) and os.remove(report)
    return [{"severity": "high", "rule": r.get("RuleID"), "file": r.get("File"), "line": r.get("StartLine"),
             "match": _redact(r.get("Secret", "")), "commit": (r.get("Commit") or "")[:10],
             "in_history": True} for r in raw]


def analyze(root: str) -> dict:
    gl = _gitleaks_scan(root)
    findings = gl if gl is not None else _builtin_scan(root)
    files = {rel for rel, _ in iter_files(root)}
    names = {os.path.basename(f) for f in files}

    # Was a real .env ever committed (even if deleted later)? Secrets stay in history.
    env_in_history = [p for p in git(root, "log", "--all", "--name-only", "--pretty=format:", "--diff-filter=A",
                                      "--", "*.env", ".env", ".env.*").split("\n")
                      if p and not p.endswith((".example", ".sample", ".template"))]
    env_committed_now = [f for f in files if os.path.basename(f) in (".env", ".env.local", ".env.production")]

    uses_env = 0
    for rel, ap in iter_files(root):
        if rel.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".rs", ".php", ".cs")):
            t = read_text(ap)
            if t and ENV_USAGE.search(t):
                uses_env += 1
    has_example = any(n in names for n in (".env.example", ".env.sample", ".env.template", "env.example"))
    gitignore = read_text(os.path.join(root, ".gitignore")) or ""
    ignores_env = bool(re.search(r"(?m)^\s*\.env\b|^\s*\*\.env\b", gitignore))

    hygiene = []
    if env_committed_now:
        hygiene.append({"severity": "high", "code": "env_file_committed", "message": f"Real env file(s) committed: {sorted(env_committed_now)}"})
    elif env_in_history:
        hygiene.append({"severity": "high", "code": "env_file_in_history", "message": f"An env file was committed in the past and is still recoverable from history: {sorted(set(env_in_history))[:3]}"})
    if uses_env and not has_example:
        hygiene.append({"severity": "low", "code": "no_env_example", "message": "Code reads environment variables but no .env.example documents them"})
    if uses_env and not ignores_env:
        hygiene.append({"severity": "medium", "code": "env_not_gitignored", "message": ".env is not in .gitignore"})
    if not uses_env and findings:
        hygiene.append({"severity": "medium", "code": "hardcoded_config", "message": "Hard-coded credentials found and the code never reads environment variables"})

    return {"scanner": "gitleaks" if gl is not None else "builtin-regex (working tree only)",
            "findings": findings[:100], "finding_count": len(findings),
            "files_using_env_vars": uses_env, "has_env_example": has_example,
            "gitignore_covers_env": ignores_env, "hygiene": hygiene}
