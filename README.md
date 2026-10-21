# AuditCore

**Evidence-backed evaluation of hackathon GitHub repositories.** Judges can't read every repo, so AuditCore clones each team's repository and analyses it. It checks commit history, security, leaked secrets, copying between teams, and whether the pitch deck matches the code, then gives judges AI pre-scores that always cite evidence. Judges keep the final say: flags are signals for a human, never verdicts.

It has two parts:

- a **Python backend** (FastAPI + SQLAlchemy, with Gemini for the AI review) that also works as a one-repo CLI;
- a **React dashboard** (Vite) for organizers, judges and teams: quick checks, hackathon events, rounds, leaderboard, judge portal, team submit pages and public results.

## Folder map

```
AuditCore/
├── backend/
│   ├── auditcore/            Python package: API server, CLI, LLM review, scoring, database
│   │   └── analyzers/        static checks on a repo: commits, security, secrets, structure, fingerprint (copying), alignment (pitch vs code), landscape (prior art)
│   └── tests/                pytest suite (no network, no Gemini key needed)
├── frontend/                 React + Vite dashboard (src/pages, src/components); builds to frontend/dist
├── legacy/web/               old static HTML dashboard, served only when frontend/dist has not been built
├── data/                     rubric.json (default scoring rubric) and sample team CSVs
├── docs/                     API notes for the frontend (stage 5 and stage 6 endpoints)
├── pyproject.toml            Python package and pytest config
├── requirements.txt          backend runtime dependencies
└── .env.example              list of environment variables (copy to .env)
```

Key backend modules (`backend/auditcore/`):

| Module | Role |
|---|---|
| `server.py` | FastAPI app: `/api/*`, `/public/*`, and serves the React build |
| `cli.py` | `evaluate()` pipeline for one repo, and the `auditcore evaluate <url>` command |
| `llm.py` | Gemini review (prompt, calls, throttling, citation checks, offline heuristic fallback) |
| `deckreview.py` | slides-only review for idea rounds |
| `scoring.py`, `report.py` | combines AI and judge scores, weighted totals, Markdown reports |
| `rounds.py` | round presets and per-round rubrics |
| `db.py` | SQLAlchemy models (SQLite by default, Postgres optional) |
| `ingest.py` | cloning, file walking, GitHub API helpers |
| `deck.py`, `teams.py` | .pptx/PDF text extraction, team CSV parsing and member matching |
| `batch.py` | command-line batch evaluation of a CSV of repos |
| `paths.py` | repo locations (rubric, frontend build, legacy pages) |

## Setup

Requirements: Python 3.10+, git, and Node 18+ only if you build the frontend.

### Backend

```powershell
cd AuditCore
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # bash: source .venv/Scripts/activate (Windows) or .venv/bin/activate
pip install -e ".[server,dev]"      # installs the auditcore package from backend/, plus test tools
```

`pip install -r requirements.txt` installs only the runtime dependencies. With that route, run commands from `backend/` or set `PYTHONPATH=backend`.

### Frontend

The server serves the built React app from `frontend/dist`. That folder is git-ignored, so build it once after cloning:

```powershell
cd frontend
npm install
npm run build      # writes frontend/dist
npm run dev        # optional: live reload on http://localhost:5173, proxies /api to the server on port 8000
```

If `frontend/dist` doesn't exist, the server falls back to the older static pages in `legacy/web/`.

## Environment variables

All are optional. Copy `.env.example` to `.env` and fill in what you need. **`.env` is git-ignored; never commit keys.** The backend reads real environment variables only and does not load `.env` by itself, so load it into your shell first:

```powershell
# PowerShell
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=.+' } | ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "env:$k" $v }
```
```bash
# bash
set -a; source .env; set +a
```

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Enables the AI review. Without it you get rough offline heuristic scores |
| `GEMINI_MODEL` | Default model (default `gemini-2.5-pro`) |
| `GEMINI_MODEL_BROAD`, `GEMINI_MODEL_DEEP` | Models for the cheap first pass over everyone and the finalist pass |
| `GEMINI_CONCURRENCY`, `GEMINI_RPM` | Parallel Gemini calls (default 8) and a requests-per-minute cap (default off) |
| `GITHUB_TOKEN` / `GITHUB_TOKENS` | Higher GitHub API limits; several comma-separated tokens allowed |
| `DATABASE_URL` | Default `sqlite:///auditcore.db` in the current directory; e.g. `postgresql+psycopg://user:pass@host/auditcore` (needs `pip install -e ".[postgres]"`) |
| `AUDITCORE_TOKEN` | If set, every `/api` call needs `Authorization: Bearer <token>`. **Set it before exposing the server beyond your machine.** |
| `AUDITCORE_WORKERS`, `AUDITCORE_CHECKPOINT_WORKERS`, `AUDITCORE_RESEARCH_WORKERS` | Parallel evaluations (8), checkpoint runs (3), prior-art research (2) |
| `AUDITCORE_LANDSCAPE` | `off` disables the automatic prior-art search after each evaluation |
| `AUDITCORE_SCHEDULER`, `AUDITCORE_TICK_SECONDS` | Background round/checkpoint scheduler (`off` to disable; tick every 30 s) |
| `AUDITCORE_UPLOADS` | Where uploaded decks are stored (default `./uploads`) |
| `HOST`, `PORT` | Server address (default `127.0.0.1:8000`) |

## Running

```powershell
python -m auditcore.server                                   # dashboard + API on http://127.0.0.1:8000
python -m auditcore evaluate https://github.com/owner/repo   # one-off CLI report for a single repo
python -m auditcore.batch data/ethglobal_tokyo_2026_57.csv   # batch evaluation from a CSV (see --help)
```

Run these from the repo root so the SQLite database (`auditcore.db`) and `uploads/` end up there. Both are git-ignored.

## Testing

```powershell
python -m pytest -q          # from the repo root; config is in pyproject.toml (testpaths = backend/tests)
```

The tests never call GitHub or Gemini. The React route test is skipped if `frontend/dist` isn't built.

## How a hackathon runs in AuditCore

1. **Hackathons → create event** with a theme and start/end times. These power the "work predates the event", "after deadline" and late-deck flags.
2. **Add teams:** paste or upload a CSV with `team_id, team_name, repo_url, members` and optionally `project_title, description, problem, track, demo_url, video_url, docs_url`. Members: `Name (@githubhandle); Name (@handle)`.
3. **Teams tab → private submit link** per team. Teams open `/submit/<token>` to describe the project and upload a .pptx or PDF deck.
4. **Leaderboard → Evaluate all teams.** Each repo is cloned and analysed, and the deck and description are checked against the code.
5. **Judges tab → add judges** and share each one's private `/judge/<code>` link. Judges see evidence and AI-prefilled scores. Overriding the AI by more than 1 point needs a reason.
6. Open a team for its report, flags and AI feedback draft. Tick **Share with team** to release the feedback.
7. Download **results.csv** and **feedback.csv**, then optionally reveal the public results page.

**Rounds** (stage 6): an event can have several rounds, each with its own dates, accepted submissions (slides only, repo + demo, …), checks, criteria and advancement rule. When a round closes, each repo is evaluated **as of the deadline**. While a round is open, a background scheduler runs free offline checkpoint analyses on repos with new commits.

**Ranking rule:** for each criterion, the mean of submitted judge scores, falling back to the AI score for AI-assisted criteria. Judge spread of 3+ points is flagged.

## What gets checked

| Area | How |
|---|---|
| Commit forensics | bulk dumps, work before the event or after the deadline, rewritten history, uninformative messages |
| Team ↔ repo | repo registered by two teams; listed members who never committed; commits from non-members |
| Cross-team copying | normalised file fingerprints compared across all teams in the event |
| Secrets & env hygiene | gitleaks if installed, else built-in patterns; `.env` committed now or in history |
| Security/robustness | explainable rules (SQLi, eval, shell=True, pickle, XSS, TLS off, CORS `*`, …) with file:line |
| Pitch vs code | claims in the deck checked against the code with cited evidence |
| Prior art | GitHub search for similar projects; evidence for the innovation score, never a score |
| Technical review | Gemini, temperature 0, per-event rubric with anchors; every score must cite a verbatim quote that is verified against the files |

**Rubric (v3, `data/rubric.json`)** assumes teams use AI coding tools, so clean code, tests and tidy structure are the baseline, not evidence of skill. System design (25) and innovation (25) carry the most weight. Code quality, robustness and security (5 each) get AI pre-scores capped at 7/7/8 unless a judge raises them. Documentation is not scored. Weights can be edited per event.

API notes for the frontend: `docs/API-stage5.md`, `docs/API-stage6.md`.

## Known limits

- Public repos only. Squashed or rewritten history can hide commit signals.
- Cross-team overlap catches copying and reformatting, not AI rewrites or copying from outside the event.
- Static analysis only; submitted code is never executed.
- No user accounts: teams and judges use private links, organizers use `AUDITCORE_TOKEN`.
- Jobs run in-process; a server restart marks running evaluations as failed (re-run them).
