"""Clone a GitHub repository and collect basic metadata. Standard library only."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__",
    ".venv", "venv", "env", ".next", ".nuxt", ".gradle", ".idea", ".vscode",
    "coverage", ".pytest_cache", ".mypy_cache", "bower_components", "Pods",
}
MAX_FILE_BYTES = 1_000_000  # ignore files larger than this for text analysis

_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$"
)


class IngestError(Exception):
    pass


@dataclass
class Repo:
    url: str
    owner: str
    name: str
    path: str
    tmpdir: str | None = None
    meta: dict = field(default_factory=dict)

    def cleanup(self) -> None:
        if self.tmpdir and os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)


def parse_github_url(url: str) -> tuple[str, str]:
    m = _URL_RE.match(url.strip())
    if not m:
        raise IngestError(f"Not a recognizable GitHub repository URL: {url!r}")
    owner, name = m.group(1), m.group(2)
    if set(owner) <= {"."} or set(name) <= {"."}:
        raise IngestError(f"Not a recognizable GitHub repository URL: {url!r}")
    return owner, name


_tok_lock = threading.Lock()
_tok_i = [0]


def github_tokens() -> list[str]:
    """GITHUB_TOKENS (comma separated) or GITHUB_TOKEN. GitHub limits are per account, so extra tokens only help when
    they belong to different real accounts (for example each organizer's own)."""
    raw = os.environ.get("GITHUB_TOKENS") or os.environ.get("GITHUB_TOKEN") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]


def next_token() -> str | None:
    toks = github_tokens()
    if not toks:
        return None
    with _tok_lock:
        _tok_i[0] += 1
        return toks[_tok_i[0] % len(toks)]


def head_sha(url: str, timeout: int = 40) -> str | None:
    """Latest commit of the default branch via `git ls-remote` (no API quota). None if unreachable or private."""
    try:
        owner, name = parse_github_url(url)
        out = subprocess.run(["git", "ls-remote", f"https://github.com/{owner}/{name}.git", "HEAD"], capture_output=True, text=True,
                             timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).stdout.split()
        return out[0] if out and len(out[0]) >= 40 else None
    except (IngestError, subprocess.SubprocessError, OSError):
        return None


def _api_get(path: str) -> dict | None:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "auditcore"}
    token = next_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def fetch_meta(owner: str, name: str) -> dict:
    """Best-effort GitHub API metadata (fork status, dates). Never raises."""
    data = _api_get(f"/repos/{owner}/{name}")
    if not data:
        return {}
    parent = data.get("parent") or {}
    return {
        "created_at": data.get("created_at"),
        "pushed_at": data.get("pushed_at"),
        "is_fork": data.get("fork"),
        "parent": parent.get("full_name"),
        "stars": data.get("stargazers_count"),
        "default_branch": data.get("default_branch"),
        "size_kb": data.get("size"),
        "description": data.get("description"),
    }


def clone(url: str, timeout: int = 300, ref: str | None = None, before: str | None = None) -> Repo:
    """ref: check out this commit. before: ISO time; check out the last commit made before it (the repo as of a deadline)."""
    owner, name = parse_github_url(url)
    tmpdir = tempfile.mkdtemp(prefix="auditcore_")
    dest = os.path.join(tmpdir, name)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never block on credentials
    try:
        subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{owner}/{name}.git", dest],
            check=True, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise IngestError(
            f"git clone failed (private, missing, or rate limited?): {e.stderr.strip()[:300]}"
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise IngestError(f"git clone timed out after {timeout}s")
    meta = fetch_meta(owner, name)
    try:
        head = git(dest, "rev-parse", "HEAD").strip()
        target = ref
        if not target and before:
            target = git(dest, "rev-list", "-1", f"--before={before}", "HEAD").strip() or None
            if not target:
                meta["all_commits_after_snapshot"] = True  # nothing existed at the deadline; the latest state is evaluated and flagged
            if target:
                after = git(dest, "rev-list", "--count", f"{target}..HEAD").strip()
                meta["commits_after_snapshot"] = int(after) if after.isdigit() else None
        if target and target != head:
            subprocess.run(["git", "-C", dest, "checkout", "-q", target], check=True, capture_output=True, text=True, timeout=120)
            meta["snapshot_sha"] = target
        meta["head_sha"] = head
        meta["evaluated_sha"] = target or head
    except (subprocess.SubprocessError, OSError) as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise IngestError(f"could not check out the requested commit: {str(e)[:200]}")
    return Repo(url=url, owner=owner, name=name, path=dest, tmpdir=tmpdir, meta=meta)


def iter_files(root: str):
    """Yield (relative_path, absolute_path) for non-skipped, reasonably sized files."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            try:
                if os.path.islink(ap) or os.path.getsize(ap) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield os.path.relpath(ap, root).replace(os.sep, "/"), ap


def read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None  # binary
    return raw.decode("utf-8", errors="replace")


def git(repo_path: str, *args: str, timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", repo_path, *args], capture_output=True,
                       text=True, timeout=timeout, errors="replace")
    return r.stdout
