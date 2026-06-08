# Crypto Options Replay Engine Data Spec

Date: 2026-06-02

Status: reviewed target module spec. Current replay logic is partial and mostly artifact/function based.

## 1. Purpose

The replay engine must answer:

1. What would a strategy have seen at each decision time?
2. Was a candidate executable against the contemporaneous CLOB state?
3. Would fills, exits, cashouts, and settlement have produced profit?
4. Which signal components improved hit rate, drawdown, or execution quality?
5. Which strategy version should be promoted or rejected?

Replay is the bridge between profile/market data and strategy changes. It must not mutate live trading state.

## 2. Current Implementation

Current files:

- `app/data/pipelines/crypto/options/backtests.py`
- `app/data/pipelines/crypto/options/statistical_components.py`
- `app/data/pipelines/crypto/options/statistical_signals.py`
- `app/data/pipelines/crypto/options/price_path_trace.py`
- `app/data/pipelines/crypto/options/reporting.py`
- `app/data/pipelines/crypto/options/candidate_report.py`

Current status:

- Directional prediction backtest exists as a minimal research replay.
- Microstructure scalping proxy exists from CLOB observations.
- Hybrid backtest exists as a blocker-aware research comparison.
- Price-path trace exists from ledgers plus monitor observations.
- No durable replay DB tables are implemented yet.

Target canonical DB:

- `local/shared/artifacts/crypto-options-research/crypto_options_data.sqlite`

## 3. Required Replay Tables

Target tables:

- `replay_datasets`
- `replay_event_frames`
- `replay_runs`
- `replay_strategy_versions`
- `replay_candidate_signals`
- `replay_fill_simulations`
- `replay_exit_simulations`
- `replay_trade_groups`
- `replay_component_results`
- `replay_reports`

These tables should live in the same canonical DB as profile and market data.

## 4. Replay Event Frame

`replay_event_frames` is the core join table. One row should represent a strategy-observable point in time.

Inputs:

- `polymarket_crypto_event_universe`
- `polymarket_event_price_ticks`
- `crypto_price_ticks`
- `crypto_candles`
- `crypto_indicator_snapshots`
- `profile_raw_activity`
- `profile_grade_current`
- `profile_signal_generator_scores`
- `profile_event_timing_links`

Required fields:

- event identity: `event_key`, `event_slug`, `condition_id`, `market_id`, `token_id`, `outcome`
- time identity: `replay_timestamp_utc`, `source_observed_at_utc`, `system_received_at_utc`
- event window: `event_start_time_utc`, `event_end_time_utc`, `seconds_to_start`, `seconds_to_end`
- market state: `best_bid`, `best_ask`, `mid_price`, `spread`, `depth_top3_bid_size`, `depth_top3_ask_size`
- underlying state: latest BTC/ETH price, threshold delta, trend and indicators
- profile state: eligible generator signals, profile grades, account types, buying-ahead flag

## 5. Fill Simulation

Replay must separate signal quality from executable fill quality.

Minimum fill model fields:

- candidate side
- candidate token id
- decision timestamp
- quote timestamp
- quote age
- entry type: market, limit, passive, hold-to-settlement
- simulated fill price
- simulated fill shares
- simulated slippage
- fillability status
- blocker list

Do not count a replay trade if the event frame lacks contemporaneous executable quote data.

## 6. Exit And Settlement Simulation

Exit simulation must support:

- fixed cashout
- dynamic cashout
- split exit
- hold-to-settlement
- late rescue
- no-exit loser tracking

Settlement simulation must use resolved outcome labels when available. If settlement is unknown, replay can report mark-to-market, but it must not call that realized PnL.

## 7. Component Backtests

Component backtests should be written as small repeatable tests over `replay_event_frames`.

Initial components:

- profile outcome expectation
- grid/band rebound signal
- hedge proportion signal
- buying-ahead signal
- target-relative EMA momentum
- support/resistance band confluence
- volume-weighted pressure
- event price volatility
- spread/liquidity filter

Component reports must include:

- sample count
- hit rate
- precision/recall where applicable
- average forward return
- max adverse excursion
- max favorable excursion
- blocker reasons
- data quality status

## 8. Promotion Boundary

Replay does not authorize live trading.

Replay can produce:

- `compare_ready`
- `shadow_only`
- `bench_only`
- `blocked`
- `rejected`

Promotion to live requires a separate operator-approved strategy runtime gate.

## 9. Integration Contract

Replay reads all three module namespaces from the canonical DB:

- profile namespace: `profile_*`
- market namespace: `crypto_*`, `polymarket_*`
- replay namespace: `replay_*`

Replay must not read raw JSON artifacts directly when the equivalent normalized DB rows exist. Artifact reads are allowed only as migration/bootstrap fallback.

## 10. Gaps

Required next work:

1. Implement `replay_event_frames`.
2. Implement durable replay run tables.
3. Convert price-path trace reports into DB-backed trace rows.
4. Build replay frame generator from canonical DB.
5. Add API endpoints for replay datasets, runs, and compare-ready candidates.
