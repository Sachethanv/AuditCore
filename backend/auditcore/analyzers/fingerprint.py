"""Content fingerprints of source files, used to find copied code across teams.

Each substantial source file is normalised (whitespace removed, comment-only lines
dropped) and hashed. Renaming files or reformatting does not change the hash; changing
logic does, so this catches copy-paste, not AI rewrites (see README limits).
"""
from __future__ import annotations

import hashlib
import os

from ..ingest import iter_files, read_text

EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs",
       ".cpp", ".c", ".h", ".swift", ".dart", ".vue", ".sol", ".sql", ".html", ".css")
MIN_LINES = 20  # tiny files (configs, __init__) match by coincidence
COMMENT_PREFIX = ("#", "//", "/*", "*", "--")


def compute(root: str) -> list[dict]:
    out = []
    for rel, ap in iter_files(root):
        if not rel.endswith(EXT) or rel.endswith((".min.js", ".lock")):
            continue
        text = read_text(ap)
        if not text:
            continue
        lines = [l.strip() for l in text.splitlines()]
        code = [l for l in lines if l and not l.startswith(COMMENT_PREFIX)]
        if len(code) < MIN_LINES:
            continue
        digest = hashlib.sha1("".join(code).encode("utf-8", "ignore")).hexdigest()[:20]
        out.append({"h": digest, "f": rel, "n": len(code)})
    return out


def compare(a: list[dict], b: list[dict]) -> dict:
    """Overlap between two fingerprint sets, measured against the smaller repo."""
    ha = {x["h"]: x for x in a}
    shared = [(ha[x["h"]]["f"], x["f"], x["n"]) for x in b if x["h"] in ha]
    lines_shared = sum(n for _, _, n in shared)
    base = min(sum(x["n"] for x in a), sum(x["n"] for x in b)) or 1
    return {"shared_files": len(shared), "shared_lines": lines_shared,
            "share_of_smaller": round(lines_shared / base, 3),
            "examples": [{"a": x, "b": y, "lines": n} for x, y, n in shared[:5]]}
