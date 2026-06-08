# Crypto Options App Database Design

Date: 2026-06-02

Status: technical spec for canonical DB schema.

## Purpose

The new app started with one canonical SQLite database for profile data, market data, replay, strategies, trading state, risk, reports, and service watermarks. As of the Postgres migration checkpoint, SQLite remains the fallback/local file store while an app-owned Postgres target is introduced for concurrent data services and report readers.

Current SQLite fallback:

- `crypto_options_app/data/crypto_options_data.sqlite`

Postgres target:

- Compose file: `crypto_options_app/docker-compose.postgres.yml`
- URL env: `JANUS_CRYPTO_OPTIONS_POSTGRES_URL`
- Default URL: `postgresql://crypto_options:crypto_options_dev@127.0.0.1:55433/crypto_options`
- Container: `janus-cortex-crypto-options-postgres`

Legacy shard imports:

- profile shard: `local/shared/artifacts/crypto-options-research/profile-store/crypto_options_profiles.sqlite`
- market shard: `local/shared/artifacts/crypto-options-research/market-data/crypto_market_data.sqlite`

## Schema Groups

| Group | Tables |
| --- | --- |
| Service state | `data_service_watermarks`, `worker_runs`, `app_settings` |
| Profile universe | `profiles`, `profile_refs`, `profile_fetch_runs`, `profile_raw_activity`, `profile_period_performance`, `profile_grades`, `profile_grade_history` |
| Profile reconstruction | `profile_event_orders`, `profile_event_positions`, `profile_event_reconstructions`, `profile_event_styles`, `profile_generator_scores`, `profile_buying_ahead` |
| Event universe | `events`, `event_tokens`, `event_outcomes`, `event_rollups` |
| Market prices | `polymarket_price_ticks`, `polymarket_order_books`, `polymarket_trade_prints`, `underlying_price_ticks`, `underlying_candles` |
| Indicators | `indicator_definitions`, `indicator_snapshots`, `event_indicator_context`, `indicator_backtest_results` |
| Replay | `replay_datasets`, `replay_frames`, `replay_runs`, `replay_candidate_signals`, `replay_fill_simulations`, `replay_exit_simulations`, `replay_trade_groups`, `replay_reports` |
| Strategy registry | `strategy_specs`, `strategy_versions`, `strategy_readiness`, `strategy_promotion_state`, `strategy_run_configs` |
| Trading lifecycle | `strategy_candidates`, `execution_intents`, `orders`, `fills`, `positions`, `exit_plans`, `exit_orders`, `settlements`, `pnl_snapshots` |
| Risk and reports | `risk_gate_evaluations`, `stop_gate_events`, `exposure_snapshots`, `system_status_snapshots`, `strategy_reports`, `run_reports` |

## Identity Model

Required stable event identity fields:

- `event_key`
- `event_slug`
- `condition_id`
- `market_id`
- `market_slug`
- `symbol`
- `event_start_time_utc`
- `event_end_time_utc`
- `settlement_threshold`

Required token identity fields:

- `event_token_key`
- `token_id`
- `outcome`
- `condition_id`
- `event_key`

Trading and replay rows must reference `event_token_key`. If a candidate cannot link to an event token, it is not executable.

## Time Model

Every streaming or decision row must separate:

- provider/chart timestamp
- local received timestamp
- inserted timestamp
- decision timestamp

Latency fields:

- `source_latency_ms`
- `insert_latency_ms`
- `decision_latency_ms` where applicable

## Migration Plan

1. Initialize the canonical DB with all schema groups.
2. Import profile shard rows into matching profile tables.
3. Import market shard rows into matching event, price, and indicator tables.
4. Recompute event timing links and `buying_ahead` in the canonical DB.
5. Recompute profile event reconstruction and generator scores in the canonical DB.
6. Keep legacy shards read-only for audit until parity checks pass.
7. Switch new app defaults to canonical DB only after parity checks pass.

## Postgres Migration Plan

Postgres migration is tracked in `34_postgres_docker_database_migration_plan.md`.

Initial implementation commands:

```powershell
docker compose -f crypto_options_app/docker-compose.postgres.yml up -d
python -m crypto_options_app.scripts.run_crypto_options_postgres_status --check-connection --bootstrap-schema --json
python -m crypto_options_app.scripts.run_crypto_options_sqlite_to_postgres_plan --json
```

Runtime cutover is intentionally separate from container startup. The app must first add a DB dialect adapter, replace SQLite-specific query functions such as `julianday`, bulk-copy high-volume append-only tables, and shadow-read API health/dashboard from Postgres before any writer group is cut over.

Current migration tooling:

- Dialect helper: `crypto_options_app/db/dialect.py`
- Postgres helper: `crypto_options_app/db/postgres.py`
- Bulk-copy importer: `crypto_options_app/db/postgres_import.py`
- Copy CLI: `crypto_options_app/scripts/run_crypto_options_sqlite_to_postgres_copy.py`

The first bounded copy has been validated against the running Postgres container for `app_settings`, `data_service_watermarks`, `strategy_specs`, and `signal_specs`. Full cutover remains blocked until read adapters and worker query portability are completed.

## Parity Checks

Required checks before migration completion:

- profile count matches or exceeds legacy profile shard
- raw activity count matches imported scope
- event universe count matches or exceeds market shard
- latest price views return current rows
- generator scores are recomputed
- buying-ahead rows are deduped by profile/event/action
- no table-name collisions with legacy schema

## Acceptance Criteria

- One DB can initialize all schema groups.
- All tables have stable primary keys and source timestamps.
- Migration is import-first and does not mutate legacy shards.
- Trading lifecycle tables distinguish candidate, intent, order, fill, position, exit, settlement, and PnL.

## P2 Centralization Update

The canonical DB default is now `crypto_options_app/data/crypto_options_data.sqlite`. Legacy profile and market shards remain read-only import sources only. P2 adds `polymarket_order_book_levels` and `polymarket_updown_pair_snapshots` so replay and managed strategies can reason about Up/Down price-path depth and latency.

## Postgres Foundation Update

The app now has a dedicated Dockerized Postgres target under `crypto_options_app`. This target is the preferred destination for the next DB adapter and bulk-copy phase because SQLite locks are expected when the A/B/C data services and strategy/report scouts run concurrently.
