"""Well-known locations in the repository checkout (repo root = two levels above this package)."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # <repo>/backend/auditcore/paths.py -> <repo>
DATA_DIR = REPO_ROOT / "data"
RUBRIC_PATH = DATA_DIR / "rubric.json"            # default event rubric
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"   # React app, built with `npm run build` in frontend/
LEGACY_WEB = REPO_ROOT / "legacy" / "web"         # old static pages, served only if the React app is not built
