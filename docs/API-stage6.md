# Stage 6 API: rounds, checkpoints, speed (for the front-end team)

An event with **no rounds** behaves exactly as before. As soon as it has rounds, leaderboard / judging / results are per round, and `?round=<id>` selects one (default: the latest round that has started).

## Rounds
- `GET /api/rounds/presets` -> `[ {id, description, rounds:[{name, accepts, advance, checkpoint_minutes}]} ]` (`single`, `submit_finals`, `idea_build_final`, `four_stage`)
- `POST /api/events/{id}/rounds/preset` `{preset, start?, hours?: [..], replace?}` -> rounds laid out back to back from `start`
- `GET|POST /api/events/{id}/rounds`; `PATCH|DELETE /api/rounds/{rid}` (delete refused once it has evaluations or scores)
- Round body: `{name, description, starts_at, ends_at, accepts: [summary|deck|repo|demo|video], checks: {code,pitch,commits,landscape}, criteria: [{id, weight, ai_assisted?}], advance: {mode: none|manual|top_n|top_percent|threshold, value, auto}, checkpoint_minutes (5-1440), close_profile: broad|deep}`
  - `criteria` picks from the event rubric, plus `problem_clarity` and `solution_feasibility` (scored from slides). Omit = the event rubric.
  - No `starts_at`/`ends_at` = the organizer opens/closes by hand: `POST /api/rounds/{rid}/open`, `/close`.
- Round object adds `state` (`upcoming|open|closed`, computed from the clock), `team_count`, `evaluated`, `advanced_count`, `criteria[]`, `announced`.
- `POST /api/rounds/{rid}/advance?force=` applies the rule, writes `RoundTeam` rows, 409 while evaluations are running. `PATCH /api/rounds/{rid}/teams/{tid}` `{advanced}` is a hand decision the rule never overwrites. `PATCH /api/rounds/{rid}` `{announced:true}` publishes the shortlist.
- Closing a round (by clock or by hand) freezes each team's deck/links/profile (`team.rounds[].snapshot`) and queues the full evaluation on the repo **as of the deadline** (later commits are ignored and flagged).

## Leaderboard / judging
- `GET /api/events/{id}/leaderboard?round=` returns `round`, `rounds[]`, and rows only for teams in that round (`advanced` per row). `results.csv?round=`.
- Judge: `GET /public/judge/{code}/session?round=`, `/teams/{tid}?round=`, `PUT /teams/{tid}/score?round=`. A judge has a separate scorecard per round. The session returns `round`, `rounds[]` and that round's `criteria`.
- Teams: `GET /public/submit/{tok}` returns `round` (what is open now, its `accepts`, `ends_at`, or a "closed / next round" message), `submissions_open`, `rounds_standing[]` (announced rounds). Uploads/links the open round does not accept get 409.
- `GET /public/results/{tok}` adds `shortlists[{round, advanced[{code,name,title}]}]` for announced rounds; the final ranking is the last round's.

## Checkpoints (automatic cheap analysis)
- While a round with `checkpoint_minutes` is open, a background scheduler checks each repo's latest commit (`git ls-remote`, no API quota); if it changed it runs the free offline analysis (no Gemini). They never appear on the leaderboard.
- `GET /api/teams/{tid}/checkpoints?round=` -> `[{at, kind: checkpoint|full, sha, score, high_flags, commits, lines, flags[]}]` oldest first, for a progress timeline.

## Progress
`GET /api/events/{id}/progress?round=` -> `{teams, queued, running, done, failed, not_started, workers, avg_seconds_per_run, eta_seconds}`. Poll it while "Evaluate all" runs. `POST /evaluate` also takes `skip_unchanged: true` and `?round=`.
