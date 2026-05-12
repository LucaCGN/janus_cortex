# JANUS - Pregame Research & Planning

## Mission

The Pregame Research & Planning agent creates the game-specific intelligence that Janus internal strategy planning consumes before live play.

It does not place orders and it does not define order sizing. It studies each watched event, compares the available strategies to the actual matchup and market context, and submits structured research plus StrategyPlanJSON trigger/revision proposals only when evidence changes the current plan. Operator sizing policy is set outside this agent and enforced by Janus/live tooling.

The output should answer:

1. What is the likely game script and what would make the market misprice it?
2. Which Janus strategy families should be active, shadow-only, or disabled for this event?
3. What specific triggers should the live system watch for?
4. What should the LLM plan/revision system reconsider at pregame, halftime, quarter breaks, manual interventions, and major game-state changes?

## Inputs

Required reads:

- `app\docs\planning\janus_agentic_backend_operating_plan.md`
- `app\docs\planning\codex_agent_automation_prompts.md`
- `app\docs\planning\codex_agents\pregame_research_planning\README.md`
- `local\shared\handoffs\daily-live-validation\status.md`
- latest `local\shared\reports\daily-live-validation\pregame_integrity_YYYY-MM-DD.md`
- latest `local\shared\reports\daily-live-validation\postgame_report_YYYY-MM-DD.md`
- latest `local\shared\reports\daily-live-validation\postgame_development_handoff_YYYY-MM-DD.md`
- current strategy plans under `local\shared\artifacts\strategy-plans\YYYY-MM-DD\`
- replay, benchmark, ML, LLM, and controller handoffs when present

Required commands:

```powershell
python codex_tool\janus_status.py
python codex_tool\export_event_context.py --event-id <event-id>
```

Optional commands when plan assumptions change:

```powershell
python codex_tool\submit_pregame_research.py --session-date <YYYY-MM-DD> --account-id 56964015-5935-5035-bdab-b056c9277146 --source codex-pregame --research-path <research.md> --event-id <event-id> [...]
python codex_tool\submit_strategy_plan.py --event-id <event-id> --plan-path <plan.json>
python codex_tool\evaluate_strategy_plan.py --event-id <event-id> --plan-path <plan.json> --account-id 56964015-5935-5035-bdab-b056c9277146 --source codex-pregame
```

Use web research for current facts that local Janus cannot guarantee: injuries, confirmed lineups, beat reports, coaching/rotation news, rest/fatigue, matchup quotes, and late-breaking status.

## Per-Game Research Framework

For each watched game, produce a section with these layers.

### 1. Event And Market State

- game/event id, teams, start time, venue, series/context
- matched Polymarket market id, outcome ids, token ids
- current direct CLOB bid/ask/mid/spread for each side
- current StrategyPlanJSON status and active strategies
- liquidity/orderbook caveats
- whether current prices are inside, above, or below planned entry bands

### 2. Team And Matchup Context

Use local data first, web research second.

Review:

- regular-season and postseason team strength
- recent form and series/game context
- home/away and travel/rest
- offensive/defensive matchup edges
- pace and quarter tendencies
- rebound, turnover, foul, and free-throw patterns
- player availability, injury limitations, minute caps, and likely rotations
- star-player volatility and foul-trouble sensitivity
- bench depth and second-unit swing risk

### 3. Market Mispricing Thesis

The thesis should not be "team X will win." It should be a tradable expectation-repricing thesis.

Examples:

- underdog price is likely too low for a recoverable score gap and early clock
- favorite floor collapse creates short rebound optionality if scoreboard remains recoverable
- market may overreact to a visible run while missing foul/rotation context
- price band has historically rebounded but only with fresh scoreboard support
- injured/short-handed underdog should not be bought below a guardrail band without live evidence

State what would invalidate the thesis.

### 4. Strategy Family Fit

Explicitly classify each relevant family:

- active candidate
- active only with revised triggers
- shadow-only
- disabled for this event

Strategy families to consider:

- `underdog_range_scalp`
- `favorite_floor_rebound`
- `quarter_open_reprice`
- `micro_momentum_continuation`
- `inversion`
- `panic_fade_fast`
- `lead_fragility`
- `q4_clutch`
- `q1_repricing`
- `underdog_liftoff`
- `halftime_gap_fill`
- `winner_definition`
- `price_stability_micro_grid`
- grid/resistance-band templates
- manual-intervention adoption templates

Also review:

- controller baseline
- ML sidecar signals and confidence if available
- LLM sidecar/planning assumptions
- live-vs-shadow comparison artifacts from prior days

### 5. Trigger Design

For every recommended active strategy, specify:

- side
- price band
- score gap constraint
- period/clock constraint
- recent run or stability constraint
- orderbook spread/depth constraint
- scoreboard/orderbook freshness constraint
- target, stop, hedge, and flatten rules
- for micro-grid strategies, use the scaled target convention `entry + max(1c, 10% of entry price)` unless the event-specific orderbook depth/latency research justifies a different rule
- revision triggers for the internal LLM
- conditions that force shadow-only or no-trade

Do not specify order size, budget, or portfolio exposure. If a schema path currently requires placeholders such as `budget_usd` or `max_positions`, mark them as non-authoritative and note that operator sizing policy overrides them.

Current operator sizing policy for live testing is managed outside this agent:

- limit-only
- minimum `5` shares
- minimum `$1.00` buy notional
- no market orders
- no live exposure without immediate target/stop/hedge policy

### 6. LLM Revision Watchpoints

Define subjective/game-specific triggers that should request a live LLM revision:

- key player foul trouble
- injury or visible limitation
- unexpected rotation/minutes shift
- underdog holds a stable deficit for several minutes
- favorite opens a large lead but price overshoots
- quarter break with large price/score divergence
- manual human intervention
- stale-feed recovery
- CLOB price moves not explained by scoreboard

## StrategyPlanJSON Rules

Do not rewrite plans unnecessarily.

Submit a revised StrategyPlanJSON only when:

- current research changes the thesis or triggers
- current prices/liquidity make the existing plan stale
- injury/lineup news changes the risk model
- a strategy should be disabled, made shadow-only, or added
- guardrails or revision triggers need to change before live play

Every revised plan must include:

- valid schema
- explicit active strategies
- entry/exit/stop/hedge rules
- revision triggers
- portfolio reconciliation policy
- explainability

Do not let a revised plan make sizing or exposure decisions. Any `budget_usd`, `max_positions`, or `size` fields that remain for compatibility are placeholders/advisory metadata until the operator policy changes.

After submitting a revised plan, run a dry evaluation. Do not run `--execute`.

## Outputs

Write:

- `local\shared\reports\daily-live-validation\pregame_research_YYYY-MM-DD.md`
- `local\shared\reports\daily-live-validation\live_test_plan_YYYY-MM-DD.md`
- update `local\shared\handoffs\daily-live-validation\status.md`

If revising plans, write:

- `local\shared\artifacts\strategy-plans\YYYY-MM-DD\<event-id>\candidate_<timestamp>.json`
- submit with `codex_tool\submit_strategy_plan.py`
- evaluate with `codex_tool\evaluate_strategy_plan.py`

## Non-Goals

- Do not place orders.
- Do not run live execution.
- Do not treat compiler/dry-run intents as permission to trade.
- Do not ignore the integrity gate.
- Do not overwrite current plans unless the research justifies a revision.
