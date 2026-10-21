# New API in Stage 5 (for the front-end team)

All `/api/*` routes need `Authorization: Bearer <AUDITCORE_TOKEN>` when that variable is set. `/public/*` routes use the private link tokens.

## Rubric v3
Criteria ids changed: `system_design` (replaces `technical_implementation`), `innovation_creativity` (25), `completeness`, `impact_usefulness`, `demo_presentation`, `code_quality`, `robustness`, `security_config`. `documentation` is gone. Always render criteria from `event.rubric.criteria`; do not hard-code ids.
A scored criterion can now carry `capped: true` and `uncapped_score` (the AI's score before the AI-code baseline cap); show "capped at baseline" next to it. `review.baseline_notes` lists them.

## Judge / organizer evaluation payload additions (`review` and `judge_view`)
- `design_decisions[]`: `{decision, why_it_matters, file, line, quote, verified}` (only verified ones are kept)
- `design_questions[]`: `{question, listen_for, file}`; show these to judges as "Ask the team"
- `baseline_notes[]`

## Judge scoring
`PUT /public/judge/{code}/teams/{tid}/score` accepts an extra `qa: [{question, rating: strong|ok|weak|not_asked, note}]`. Returned in `GET /public/judge/{code}/teams/{tid}` as `my.qa`, and to organizers in `GET /api/teams/{tid}` `judge_scores[].qa`.
`GET /public/judge/{code}/teams/{tid}` also returns `landscape`.

## Landscape research ("Where this stands")
- `POST /api/teams/{tid}/landscape?refresh=false` runs (or returns the cached) research. 422 if the team has no description yet.
- `POST /api/events/{evid}/landscape?only_missing=true` runs it for every team in the background (202 `{queued}`).
- Cached on the team as `team.landscape` (also in `GET /api/teams/{tid}` and the teams list):
  `{status: done|unavailable, mode, queries, candidates[{repo,url,stars,description,overlap}], assessment{novelty: novel|incremental|crowded|unclear, summary, closest[{repo,why_similar,how_different}], differentiators[], judge_question}, notes[], caveat, researched_at}`
- Runs automatically after each team evaluation unless `AUDITCORE_LANDSCAPE=off`. Show `caveat` and `mode` ("offline" means keyword matching only).

## Token budget
- `GET /api/events/{evid}/usage` -> `{evaluations, landscape, explanations, total, cap, over_budget, remaining, avg_per_evaluation}`
- `PUT /api/events/{evid}/budget` `{max_tokens|null, explain_enabled, explain_llm, explain_per_team}`; also returned as `event.budget`.
- When the cap is reached new evaluations use the free offline review (the evaluation `mode` says "[token budget reached]").
- `POST /api/events/{evid}/evaluate` accepts `profile: "broad"|"deep"` and `top_n` (deep only: re-evaluates the current top N with the strongest model).

## Team side (private submit link)
- `GET /public/submit/{tok}` now has `readiness {done,total,items[{id,label,ok}],missing[]}` and `results_revealed`. Organizer team objects also carry `readiness`.
- `GET /public/submit/{tok}/results` -> `{revealed, results}`; `results` has `rank, of, final, per_criterion[], compare{enabled,left,teams_above[{code,name,rank}]}`.
- `POST /public/submit/{tok}/compare {other: "T-78"}` -> explanation of why a higher-ranked team scored above this one. Built from stored scores (no tokens). Errors: 403 not announced/not enabled, 404 not above you, 409 judging incomplete, 429 limit reached. Response: `{headline, gap, behind_on[{title,yours,theirs,points,judged_by,higher_band_looks_like,your_gaps}], you_led_on[], mode, paragraph?, cached}`.
