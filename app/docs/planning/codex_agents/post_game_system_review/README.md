# JANUS - Post Game System Review

## Mission

The postgame agent is not a simple reconciliation bot. It is the daily analyst that converts yesterday's games into actionable system learning.

It must answer three questions:

1. How did every active and shadow strategy behave?
2. What broke or degraded in the live system, data, order flow, or replay capture?
3. What should the development agent build, fix, promote, demote, or test next?

## Inputs

Required reads:

- `app\docs\planning\janus_agentic_backend_operating_plan.md`
- `app\docs\planning\codex_agent_automation_prompts.md`
- `app\docs\planning\codex_agents\post_game_system_review\report_contract.md`
- `local\shared\handoffs\daily-live-validation\status.md`
- `local\shared\reports\daily-live-validation\postgame_operator_observations_YYYY-MM-DD.md` when present
- `local\shared\reports\daily-live-validation\pregame_research_YYYY-MM-DD.md`
- `local\shared\reports\daily-live-validation\live_test_plan_YYYY-MM-DD.md`
- `local\shared\artifacts\daily-live-validation\YYYY-MM-DD\`
- `local\shared\artifacts\strategy-plans\YYYY-MM-DD\`
- `local\shared\artifacts\ops\YYYY-MM-DD\`
- lane handoffs for replay, benchmark, ML, LLM, and controller when present

Required commands:

```powershell
python codex_tool\janus_status.py
python codex_tool\run_postgame_review.py --session-date <YYYY-MM-DD> --account-id 56964015-5935-5035-bdab-b056c9277146 --source codex-postgame
python codex_tool\run_integrity_check.py --session-date <YYYY-MM-DD> --account-id 56964015-5935-5035-bdab-b056c9277146 --source codex-postgame
```

Use `codex_tool\export_event_context.py` for every reviewed game when available. Use web research when local data is insufficient to explain injuries, rotations, final game context, market movement, or missed opportunities.

Operator observations are not optional when present. Treat them as hypotheses and human-in-the-loop context to verify against direct CLOB truth, local play-by-play, StrategyPlanJSON revisions, watch-session ticks, and web research. If operator observations conflict with local Janus data, report the conflict explicitly and route a data-capture or context gap.

## Required Analysis

### 1. Per-Algorithm Review

Review every available live and shadow artifact, not only realized orders.

Minimum coverage:

- active StrategyPlanJSON strategies
- deterministic families and shadow cards
- controller outputs
- ML selected rows and ranking/calibration sidecars
- LLM selected rows or plan revisions
- manual interventions and whether they matched any strategy family

For each game and each strategy family, report:

- signal count
- order intent count
- fill count
- skip/blocker reasons
- price and score context at signal or missed signal
- whether the final game path says the strategy was directionally right, late, early, overfiltered, underfiltered, or untestable
- whether replay/live evidence supports promote, keep, demote, or redesign

If a shadow artifact is missing, do not ignore it. Record it as an observability defect and route a development task.

### 2. Operational Integrity Review

Perform a complete integrity check of the last run:

- direct CLOB collateral, orders, fills, positions, and closed positions
- stale portfolio mirrors
- account-scoped fill ledger availability
- orderbook freshness and spread
- scoreboard and play-by-play freshness
- watch-session tick/trade persistence
- strategy decision persistence
- replay-session creation
- manual intervention adoption
- missing target, stop, or hedge coverage
- API errors, provider stalls, endpoint failures, and slow cycles

Correct low-risk runtime/documentation issues when safe, such as stale handoff wording, missing status summaries, or missing routed blockers. Route code changes to the development agent unless the issue is a small postgame tooling/reporting bug.

### 3. Postgame Research And Development Handoff

Use the same research discipline as pregame, but with final-game evidence:

- What actually happened in the game?
- Which player, rotation, foul, injury, pace, run, or matchup factors mattered?
- Did the market overreact or underreact?
- Which price bands or score states produced opportunity?
- Which Janus strategy should have captured it?
- Was failure caused by logic, missing strategy coverage, stale data, CLOB execution, portfolio reconciliation, or missing LLM context?

Also evaluate operator-declared lessons:

- whether operator/manual intervention caused more loss than the active algorithm;
- whether the live StrategyPlanJSON was too restrictive after the game dynamic changed;
- whether small-target microstructure trades such as `+1c` or `10%` rebounds were available;
- whether full orderbook depth and latency capture were sufficient to replay high-frequency behavior;
- whether play-by-play/player-role tags should be added as ML/LLM inputs for short-horizon price-impact modeling.

Write a development handoff that is implementable:

- exact bug or improvement
- evidence path
- expected behavior
- suggested files/modules to inspect
- tests or replay samples needed
- priority and owning lane

## Outputs

Write:

- `local\shared\reports\daily-live-validation\postgame_report_YYYY-MM-DD.md`
- `local\shared\reports\daily-live-validation\postgame_development_handoff_YYYY-MM-DD.md`
- update `local\shared\handoffs\daily-live-validation\status.md`

Optional artifacts:

- `local\shared\artifacts\daily-live-validation\YYYY-MM-DD\postgame_algorithm_review.json`
- `local\shared\artifacts\daily-live-validation\YYYY-MM-DD\postgame_integrity_review.json`

## Non-Goals

- Do not place orders.
- Do not turn the report into only a balance reconciliation.
- Do not ignore shadow strategies just because they did not place orders.
- Do not silently accept missing shadow/replay/watch artifacts.
- Do not make broad ML/LLM/replay code changes inside the postgame pass. Route them precisely.
