"""Lightweight static security/robustness pattern checks.

This is a triage layer: simple, explainable regex rules with file:line evidence.
Run Semgrep for deeper coverage (planned integration).
"""
from __future__ import annotations

import re

from ..ingest import iter_files, read_text

CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".php", ".cs", ".rs")
RULES = [
    # (id, severity, regex, languages-or-None, message)
    ("eval_exec", "high", r"\b(eval|exec)\s*\(", (".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".rb"), "Dynamic code execution (eval/exec)"),
    ("shell_true", "high", r"subprocess\.\w+\([^)]*shell\s*=\s*True|os\.system\(", (".py",), "Shell command execution; injection risk if input is user-controlled"),
    ("pickle_load", "high", r"\bpickle\.loads?\(", (".py",), "Unsafe deserialization (pickle)"),
    ("yaml_unsafe", "medium", r"\byaml\.load\((?![^)]*Loader\s*=\s*(?:yaml\.)?Safe)", (".py",), "yaml.load without SafeLoader"),
    ("sql_concat", "high", r"""(?i)\.(?:execute|query|raw)\(\s*(?:f["']|["'][^"']*["']\s*(?:\+|%)|[^)]*\.format\()[^)]*\b(select|insert|update|delete)\b""", None, "SQL built by string interpolation (SQL injection risk)"),
    ("sql_concat2", "high", r"""(?i)(?:f["']|["'])\s*(?:select|insert into|update|delete from)\b[^"']*(?:\{[^}]+\}|["']\s*\+)""", (".py", ".js", ".ts", ".java"), "SQL string built with interpolation (SQL injection risk)"),
    ("innerhtml", "medium", r"\.innerHTML\s*=|dangerouslySetInnerHTML|v-html", (".js", ".jsx", ".ts", ".tsx", ".vue", ".html"), "Raw HTML injection (XSS risk)"),
    ("tls_verify_off", "high", r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true", None, "TLS certificate verification disabled"),
    ("cors_wildcard", "medium", r"""(?i)allow_origins\s*=\s*\[\s*["']\*["']|Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*|cors\(\s*\)""", None, "CORS allows any origin"),
    ("debug_on", "medium", r"(?i)\bdebug\s*=\s*True|app\.run\([^)]*debug\s*=\s*True|DEBUG\s*=\s*True", (".py",), "Debug mode enabled"),
    ("weak_hash", "low", r"(?i)hashlib\.(md5|sha1)\(|createHash\(['\"](md5|sha1)['\"]\)", None, "Weak hash function (MD5/SHA1)"),
    ("jwt_none_or_weak", "medium", r"""(?i)algorithms?\s*=\s*\[?\s*["']none["']|jwt\.decode\([^)]*verify\s*=\s*False""", None, "JWT verification weakened"),
]
ROBUST_RULES = [
    ("bare_except", "low", r"^\s*except\s*:\s*(#.*)?$", (".py",), "Bare except swallows all errors"),
    ("empty_catch", "low", r"catch\s*\([^)]*\)\s*\{\s*\}", (".js", ".ts", ".jsx", ".tsx", ".java"), "Empty catch block silently swallows errors"),
    ("hardcoded_localhost", "low", r"https?://(?:localhost|127\.0\.0\.1)[:/]", (".js", ".jsx", ".ts", ".tsx", ".py"), "Hard-coded localhost URL; breaks outside the dev machine"),
    ("todo_marker", "info", r"\b(TODO|FIXME|HACK|XXX)\b", None, "Unfinished-work marker"),
]
_COMPILED = [(i, s, re.compile(rx), exts, m) for i, s, rx, exts, m in RULES + ROBUST_RULES]
DEV_DEFAULT = re.compile(r"process\.env|os\.environ|getenv|import\.meta\.env|\?\?|\|\||:\s*str\s*=|\bsettings\.|origins|cors", re.I)
TEST_HINT =re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)|(_test\.|\.test\.|\.spec\.|test_[^/]+\.py$)")


def analyze(root: str) -> dict:
    findings, counts = [], {}
    test_files = code_files = loc = 0
    for rel, ap in iter_files(root):
        if not rel.endswith(CODE_EXT):
            continue
        code_files += 1
        is_test = bool(TEST_HINT.search(rel))
        test_files += is_test
        text = read_text(ap)
        if not text:
            continue
        lines = text.splitlines()
        loc += sum(1 for l in lines if l.strip())
        if is_test:
            continue
        for i, line in enumerate(lines, 1):
            if len(line) > 1000:
                continue
            for rid, sev, rx, exts, msg in _COMPILED:
                if exts and not rel.endswith(exts):
                    continue
                if rid == "hardcoded_localhost" and DEV_DEFAULT.search(line):
                    continue  # env-driven value with a dev fallback is fine
                if rx.search(line):
                    counts[rid] = counts.get(rid, 0) + 1
                    if counts[rid] <= 5:  # cap evidence per rule
                        findings.append({"rule": rid, "severity": sev, "file": rel, "line": i,
                                         "message": msg, "snippet": line.strip()[:160]})
    return {"code_files": code_files, "test_files": test_files, "code_loc": loc,
            "rule_counts": counts, "findings": findings}
