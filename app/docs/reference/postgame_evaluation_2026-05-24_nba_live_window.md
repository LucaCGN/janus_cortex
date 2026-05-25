# Postgame Evaluation - 2026-05-24 NBA Live Window

Status: current evaluation
Updated: 2026-05-25

## Scope

This document records the live Janus validation work for `nba-okc-sas-2026-05-24` during the Spurs vs Thunder live window.

Primary local evidence:

- `local/shared/artifacts/live-strategy-worker/2026-05-24/ticks.jsonl`
- `local/shared/artifacts/strategy-plans/2026-05-24/nba-okc-sas-2026-05-24/current.json`
- Janus live worker status snapshots from the active run source `codex-manual-live-window-20260524-low-latency-nba`

## Result

The live worker ran through Q4 and was stopped after final. Final Janus-observed score was Spurs `103`, Thunder `82`.

The system did execute controlled OKC/Thunder buys and target orders earlier in the live window, then correctly stopped adding when the event reached a late no-bid state and the OKC position was already target-covered.

This was a useful live-system proof, but it also exposed a remaining strategy gap: Janus has no explicit no-bid/min-price lottery sleeve for `0.001` entries, and current spread-gate behavior blocks that scenario even when the operator thesis is that a tiny price can still bounce on human hype.

## Live Orders Observed

Janus-submitted OKC/Thunder buys during the run included:

- BUY `5 @ 0.22`
- BUY `5 @ 0.22`
- BUY `26.579 @ 0.038`
- BUY `33.667 @ 0.03`
- BUY `50.5 @ 0.02`
- BUY `202 @ 0.005`
- BUY `252.5 @ 0.004`
- BUY `202 @ 0.005`
- BUY `202 @ 0.005`

Janus also created sell targets across the ladder, including targets around `0.06`, `0.04`, `0.02`, and `0.01`. By Q4 under `4:00`, the OKC position was covered except for dust below exchange minimum:

- position size: `1221.7366`
- open sell size: `1221.73`
- uncovered dust: `0.0066`

## In-Game Fixes Applied

The live window required narrow code changes while the worker was running:

- Scoreboard freshness now prefers capture age before interpreting clock stalls as stale data.
- Auto-protection can target operator/manual excess size rather than only strategy-owned lots.
- Sub-cent target ticks are allowed when a StrategyPlan explicitly opts in with `target_tick_size` and `min_target_price`.
- Fresh uncovered sub-cent lots can target from current price with `target_basis=current_price` instead of aggregate average position price.

Validation after the game:

- `python -m pytest tests\app\modules\agentic\test_strategy_plan_contracts_pytest.py tests\tools\test_run_live_strategy_tick_pytest.py -q` passed with `71 passed`.
- `python -m compileall app\modules\agentic\engine.py codex_tool\run_live_strategy_tick.py` passed.
- `git diff --check` passed with only existing CRLF warnings.

## What Worked

- The live worker stayed healthy through the fourth quarter.
- The direct CLOB path was fast enough for sub-cent orderbook observations.
- Janus generated and submitted multiple live OKC buy legs through the approved worker path.
- The target-covering path caught the large aggregate/manual-influenced position and created a sell ladder.
- Event-level exposure did not continue growing after the position was effectively fully covered.

## What Blocked Late Q4 Trading

Late Q4 repeatedly showed:

- OKC best ask: `0.001`
- OKC best bid: `null`
- spread: `null`
- blocker reason: `orderbook_spread_required`

The current engine requires a valid bid/spread before it treats the book as tradable. That is appropriate for normal grid/scalp logic, but it blocks the specific operator thesis:

- when a comeback side reaches `0.001` to `0.004`,
- the cash loss is bounded by tiny entry price,
- the upside can still be `2x` to `10x` on a brief hype bounce,
- and the correct behavior may be a deliberately isolated lottery sleeve, not ordinary spread-gated grid logic.

This should not be implemented as a global bypass. It should be a separate, opt-in StrategyPlan sleeve with strict limits.

## Required Follow-Up

Add a deterministic strategy family, tentatively `no_bid_min_price_lottery_v1`, with these constraints:

- active only when explicitly enabled in the StrategyPlan;
- max entry price such as `0.001` or `0.002`;
- max one minimum-notional order per event or per late-game phase;
- must require final-quarter clock context and a live comeback/hype thesis;
- must not count as ordinary grid/scalp spread-compliant evidence;
- must auto-place a sub-cent-compatible target if the entry fills;
- must never bypass event-level budget or kill-switch gates.

Also add lot-level target management so Janus can replace or improve stale targets when a later StrategyPlan uses a lower sub-cent target basis. Today, target coverage existed, but coverage alone prevented later target improvement and fresh lot attribution.

## Strategy Lesson

The Q4 flow supports the overall architecture from the Janus diagram: multiple sleeves should operate independently under one event budget. A global spread/band blocker should not silence every sleeve. Ordinary grid sleeves can require bid/spread quality, while a separate lottery sleeve can intentionally trade a no-bid tail at tiny notional if the StrategyPlan says so.

