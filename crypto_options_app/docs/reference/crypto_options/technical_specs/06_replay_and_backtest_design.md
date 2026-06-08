# Crypto Options Replay And Backtest Design

Date: 2026-06-02

Status: technical spec for DB-backed replay and strategy validation.

## Purpose

Replay must show what a strategy would have seen at each decision point, whether the candidate was executable, and whether lifecycle outcomes would have produced profit.

Replay is required before live pulse tests.

## Replay Data Model

Core tables:

- `replay_datasets`
- `replay_frames`
- `replay_runs`
- `replay_candidate_signals`
- `replay_fill_simulations`
- `replay_exit_simulations`
- `replay_trade_groups`
- `replay_component_results`
- `replay_reports`

`replay_frames` joins:

- event/token universe
- Polymarket YES/NO price ticks
- order book state
- underlying prices
- indicators
- profile raw activity
- profile grades
- generator scores
- buying-ahead flags

## No-Lookahead Rules

Replay may only use rows with source timestamps at or before the replay decision timestamp.

Replay must separate:

- decision timestamp
- source observed timestamp
- system received timestamp
- fill simulation timestamp
- settlement timestamp

If a row is not contemporaneous, it cannot support a replay candidate.

## Fill Simulation

Fill simulation must model:

- market BUY
- market SELL
- limit BUY
- limit SELL
- quote age
- spread
- depth
- slippage
- partial fill
- unfilled order
- expired order

Midpoint-only fills are not acceptable for promotion. They may be used only for exploratory component tests and must be labeled as non-executable.

## Exit And Settlement Simulation

Exit simulation must support:

- fixed cashout
- dynamic cashout
- split exits
- hedge-managed exits
- hold-to-settlement
- late rescue
- no-exit loser accounting

Settlement simulation must use resolved outcome labels when available. Unknown settlement can be reported as mark-to-market, not realized PnL.

## Component Backtests

Required component reports:

- outcome expectation
- hedge proportion
- band/rebound
- buying-ahead
- target-relative EMA momentum
- support/resistance confluence
- volume-weighted pressure
- event price volatility
- spread/liquidity filter

Each report includes:

- sample count
- hit rate
- precision/recall where applicable
- average forward return
- max adverse excursion
- max favorable excursion
- fillability
- blocker reasons
- data quality status

## Acceptance Criteria

- Each of the 10 candidates can run replay or return a structured data-coverage blocker.
- Replay never authorizes live trading.
- Replay distinguishes signal quality from executable fill quality.
- Replay reports are comparable across strategy versions.

## P2 Centralization Update

Replay must be able to consume centralized Up/Down price-path rows from `polymarket_price_ticks`, `polymarket_order_book_levels`, and `polymarket_updown_pair_snapshots`. Rotation9 readiness requires at least one replay-safe fixture or captured path that simulates entry fillability, cashout exit, rebuy target, and stale-order review without lookahead.
