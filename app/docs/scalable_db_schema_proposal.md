# Janus Cortex DB Schema Proposal (Phase-Driven to v1)

## Why this revision
The previous proposal is structurally good but too broad for early validation.
This version keeps the full target model while forcing phased adoption:
- validate nodes and methods first (`v0.1.*`),
- then validate schema slices,
- then expose only endpoints backed by validated tables.
- before heavy `v0.3` migration coding, align app folder responsibilities per `app/docs/app_structure_modularization_plan.md` so schemas map cleanly to provider/category/module boundaries.

## Versioning model
- Main lane: `v0.X.Y` where `X` is milestone area and `Y` is expansion slot.
- Current active phase: `v0.5.1`.
- v1 definition: Postgres + FastAPI + Chroma in docker, production-grade data serving only (no autonomous strategy generation).

## Schema implementation policy
1. Do not create all tables at once.
2. Each table has an activation phase.
3. A table can only be consumed by API routes after its phase is completed.
4. History tables are append-only unless explicitly marked as snapshot.
5. Every ingestion-critical table has `raw_json` and/or provider refs.
6. Every node method refactor/new method must include dedicated `pytest` module updates in the same phase.
7. Temporal coverage (past/current/future) for source methods must be documented in `app/docs/source_temporal_coverage.md` before promoting related schema usage.
8. For query-window-sensitive sources (e.g., Gamma events), persist window/filter metadata in `core.sync_runs.meta_json` to keep ingest behavior auditable.
9. Historical odds collectors should preserve provenance in `market_data.outcome_price_ticks.source` (`clob_prices_history`, `snapshot_fallback`, `fallback_stream`) to keep direct and fallback samples distinguishable.
10. Canonical mapping outputs from `app/domain/events/canonical/*` and `app/ingestion/mappings/canonical/*` (with compatibility wrappers to `app/data/pipelines/canonical/*`) are the mandatory pre-DB contract; table writes in `v0.3.*` must consume canonical ids (`canonical_event_id`, `canonical_market_id`, `canonical_outcome_id`) and provider refs without re-shaping source payloads.

## Phase summary
- `v0.1.*`: Node and method validation before schema rollout.
- `v0.2.*`: Canonical contracts, mapping logic, and pre-DB structure/testing/docs hardening gates (`v0.2.7`-`v0.2.9`) (completed).
- `v0.3.*`: DB MVP schema and migration baseline.
- `v0.4.*`: Pipeline ingestion to new schema.
- `v0.5.*`: Core API serving validated schema.
- `v0.6.*`: Portfolio + market data API blocks.
- `v0.7.*`: NBA module serving layer.
- `v0.8.*`: Chroma event-doc blocks.
- `v0.9.*`: Production hardening and v1 release gates.

## v0.3 migration inventory (implemented to date)
- `0001_v0_3_1__catalog_core_mvp.sql`
  - activates: `core.providers`, `core.modules`, `catalog.event_types`, `catalog.information_profiles`, `catalog.events`, `catalog.event_external_refs`, `catalog.markets`, `catalog.market_external_refs`, `catalog.outcomes`
  - includes phase indexes/uniqueness for `v0.3.1`
- `0002_v0_3_2__sync_and_raw_payloads.sql`
  - activates: `core.sync_runs`, `core.raw_payloads`, `catalog.event_module_bindings`
  - includes uniqueness on `(event_id, module_id)` bindings
- `0003_v0_3_3__portfolio_core_tables.sql`
  - activates: `portfolio.trading_accounts`, `portfolio.position_snapshots`, `portfolio.orders`, `portfolio.trades`
  - includes phase indexes/uniqueness for `v0.3.3`
- `0004_v0_3_4__market_data_append_only.sql`
  - activates: `market_data.outcome_price_ticks`, `market_data.orderbook_snapshots`, `market_data.orderbook_levels`
  - enforces append-only behavior with update/delete-block triggers on all three history tables
  - includes `ix_market_data_outcome_price_ticks_outcome_ts_desc` and `ix_market_data_orderbook_snapshots_outcome_captured_desc`
- migration registry table: `core.schema_migrations` (managed by `app/data/databases/migrate.py`)

## v0.4 migration inventory (implemented to date)
- `0005_v0_4_2__catalog_market_state_snapshots.sql`
  - activates: `catalog.market_state_snapshots`
  - includes uniqueness on `(market_id, captured_at, sync_run_id)` and query indexes
- `0006_v0_4_3__portfolio_order_events.sql`
  - activates: `portfolio.order_events`
  - includes uniqueness on `(order_id, event_time, event_type)` and timeline index
- `0007_v0_4_4__nba_ingestion_tables.sql`
  - activates: `nba.nba_teams`, `nba.nba_games`, `nba.nba_game_event_links`, `nba.nba_team_stats_snapshots`, `nba.nba_player_stats_snapshots`, `nba.nba_team_insights`, `nba.nba_live_game_snapshots`, `nba.nba_play_by_play`
  - includes core game/date and link indexes
- `0008_v0_4_5__catalog_event_information_scores.sql`
  - activates: `catalog.event_information_scores`
  - includes scored-at and trade-eligible indexes
- `0009_v0_4_6__market_data_outcome_price_candles.sql`
  - activates: `market_data.outcome_price_candles`
  - includes `(outcome_id, timeframe, open_time DESC)` index

## v0.3.5 repository/upsert primitives (implemented)
- Module: `app/data/databases/repositories/upsert_primitives.py`
- Exposed entrypoint: `JanusUpsertRepository`
- Scope:
  - idempotent upserts for active `core`, `catalog`, and `portfolio` tables
  - append-only insert helpers for `market_data` history tables with duplicate-safe (`ON CONFLICT DO NOTHING`) option
- Validation:
  - `tests/app/data/databases/test_upsert_primitives_pytest.py` (live Postgres, gated by `JANUS_RUN_DB_TESTS=1`)

## v0.3.6 DB integration seed packs (implemented)
- Module:
  - `app/data/databases/seed_packs/polymarket_event_seed_pack.py`
- Live probe pack:
  - `extra_3_7_past_nba_game_period`
  - `extra_3_8_upcoming_nba_availability`
  - `extra_3_9_aliens_grid_history`
- Persisted tables used:
  - `core.sync_runs`, `core.raw_payloads`
  - `catalog.events`, `catalog.event_external_refs`, `catalog.markets`, `catalog.market_external_refs`, `catalog.outcomes`
  - `market_data.outcome_price_ticks`
- Validation:
  - `tests/app/data/databases/test_polymarket_event_seed_pack_live_pytest.py` (requires both `JANUS_RUN_DB_TESTS=1` and `JANUS_RUN_LIVE_TESTS=1`)
  - detailed evidence: `app/docs/polymarket_seed_pack_v0_3_6.md`

## v0.4.1 Polymarket events ingestion pipeline (implemented)
- Modules:
  - `app/data/databases/seed_packs/polymarket_event_seed_pack.py`
  - `app/data/pipelines/daily/polymarket/sync_events.py`
  - `app/ingestion/pipelines/prediction_market_polymarket/sync_events.py`
- Main capabilities:
  - Dynamic today-NBA probe discovery from live scoreboard (`finished`, `live`, optional `upcoming`) with deterministic slug mapping.
  - Idempotent event/market/outcome ingestion to active `catalog` graph tables.
  - Append-only history ingestion using:
    - direct CLOB history (`source=clob_prices_history`)
    - snapshot fallback (`source=snapshot_fallback`)
    - live stream fallback (`source=fallback_stream`) from sampled moneyline snapshots.
  - Secondary stream fallback path for live games: repeated event-slug sampling persists `fallback_stream` ticks when primary stream poll returns zero rows.
- Persisted tables used:
  - `core.sync_runs`, `core.raw_payloads`
  - `catalog.events`, `catalog.event_external_refs`, `catalog.markets`, `catalog.market_external_refs`, `catalog.outcomes`
  - `market_data.outcome_price_ticks`
- Validation:
  - `tests/app/data/databases/test_polymarket_event_probe_builder_pytest.py`
  - `tests/app/data/databases/test_polymarket_event_seed_pack_v0_4_live_pytest.py`
  - `tests/app/ingestion/pipelines/prediction_market_polymarket/test_sync_events_wrapper_pytest.py`
  - detailed evidence: `app/docs/polymarket_pipeline_v0_4_1.md`
  - live run command evidence:
    - `python -m app.data.pipelines.daily.polymarket.sync_events --probe-set today_nba --max-finished 1 --max-live 1`

## v0.4.2 Polymarket markets/outcomes snapshot sync (implemented)
- Modules:
  - `app/data/pipelines/daily/polymarket/sync_markets.py`
  - `app/ingestion/pipelines/prediction_market_polymarket/sync_markets.py`
- Main capabilities:
  - Reuses event seed logic for idempotent market/outcome refresh.
  - Persists per-market capture points into `catalog.market_state_snapshots`.
  - Supports `--missing-only` to target same-day slug gaps.
- Validation:
  - `tests/app/ingestion/pipelines/prediction_market_polymarket/test_sync_markets_wrapper_pytest.py`
  - `tests/app/data/databases/test_polymarket_event_seed_pack_v0_4_live_pytest.py`

## v0.4.3 Portfolio mirror sync (implemented)
- Modules:
  - `app/data/pipelines/daily/polymarket/sync_portfolio.py`
  - `app/ingestion/pipelines/prediction_market_polymarket/sync_portfolio.py`
- Main capabilities:
  - Mirrors open/closed positions, orders, order events, and trades from Data API payloads.
  - Uses deterministic resolution maps from `catalog` refs to bind portfolio rows to canonical market/outcome ids.
- Validation:
  - `tests/app/data/pipelines/daily/polymarket/test_sync_portfolio_pytest.py`
  - `tests/app/ingestion/pipelines/prediction_market_polymarket/test_sync_portfolio_wrapper_pytest.py`

## v0.4.4 NBA metadata/live ingestion (implemented)
- Modules:
  - `app/data/pipelines/daily/nba/sync_postgres.py`
  - `app/ingestion/pipelines/sports_nba/sync_postgres.py`
- Main capabilities:
  - Upserts teams/games from schedule + live scoreboard.
  - Detects same-day missing schedule games and inserts scoreboard-only games.
  - Streams live snapshots and normalized play-by-play for ongoing games.
- Validation:
  - `tests/app/data/pipelines/daily/nba/test_sync_postgres_pytest.py`
  - `tests/app/data/pipelines/daily/nba/test_sync_postgres_live_pytest.py`
  - `tests/app/ingestion/pipelines/sports_nba/test_sync_postgres_wrapper_pytest.py`

## v0.4.5 Cross-domain mappings and scoring (implemented)
- Modules:
  - `app/data/pipelines/daily/cross_domain/sync_mappings.py`
  - `app/ingestion/pipelines/cross_domain/sync_mappings.py`
- Main capabilities:
  - Links NBA games to `catalog.events` via deterministic slug mapping.
  - Writes `catalog.event_information_scores` for coverage/quality/latency eligibility audits.
- Validation:
  - `tests/app/data/pipelines/daily/cross_domain/test_sync_mappings_pytest.py`
  - `tests/app/ingestion/pipelines/cross_domain/test_sync_mappings_wrapper_pytest.py`

## v0.4.6 Backfill/retry + candle aggregation (implemented)
- Modules:
  - `app/data/pipelines/daily/polymarket/backfill_retry.py`
  - `app/ingestion/pipelines/prediction_market_polymarket/backfill_retry.py`
- Main capabilities:
  - Re-runs missing/ongoing today probes and optional retry probes from recent failed sync runs.
  - Aggregates `market_data.outcome_price_ticks` into `market_data.outcome_price_candles`.
- Validation:
  - `tests/app/data/pipelines/daily/polymarket/test_backfill_retry_pytest.py`
  - `tests/app/data/pipelines/daily/polymarket/test_backfill_retry_live_pytest.py`
  - `tests/app/ingestion/pipelines/prediction_market_polymarket/test_backfill_retry_wrapper_pytest.py`

## v0.2 Canonical mapping outputs (implemented, pre-table activation)

### Canonical object contracts
- `CanonicalEvent`
  - keys: `canonical_event_id`, `canonical_slug`, `title`, `event_kind`, `status`, `start_time`, `end_time`
  - linkage: `source_refs[]`, `markets[]`
  - metadata: `home_entity`, `away_entity`, `tags[]`, `information_profile_code`, `metadata_json`
- `CanonicalMarket`
  - keys: `canonical_market_id`, `canonical_event_id`, `question`, `market_kind`, `status`
  - linkage: `source_refs[]`, `outcomes[]`
- `CanonicalOutcome`
  - keys: `canonical_outcome_id`, `label`, `token_id`
  - prices: `implied_prob`, `last_price`
  - linkage: `source_refs[]`
- `CanonicalProviderRef`
  - provenance: `provider_code`, `external_id`, `external_slug`, `external_url`, `fetched_at`, `raw_summary_json`

### Mapping services and fixtures
- Domain + ingestion wrappers (active structure):
  - `app/domain/events/canonical/*`
  - `app/ingestion/mappings/canonical/*`
- Adapter layer:
  - `app/data/pipelines/canonical/adapters/gamma_nba.py`
  - `app/data/pipelines/canonical/adapters/nba_schedule.py`
- Scoring and quality gates:
  - `app/data/pipelines/canonical/information_profiles.py`
  - `app/data/pipelines/canonical/quality_gates.py`
- Orchestration:
  - `app/data/pipelines/canonical/mapping_service.py`
  - `app/data/pipelines/canonical/dev_run_mapping.py`
- Fixture packs for deterministic integration tests:
  - `app/data/pipelines/canonical/fixtures/gamma_nba_events_fixture.json`
  - `app/data/pipelines/canonical/fixtures/gamma_nba_moneyline_fixture.json`
  - `app/data/pipelines/canonical/fixtures/nba_schedule_fixture.json`

### Canonical-to-table field intent (for `v0.3.*` migrations)
- `CanonicalEvent` -> `catalog.events`, `catalog.event_external_refs`
- `CanonicalMarket` -> `catalog.markets`, `catalog.market_external_refs`
- `CanonicalOutcome` -> `catalog.outcomes`
- outcome prices/provenance -> `market_data.outcome_price_ticks` (`source` from canonical metadata/provenance)

## Table inventory with full columns and activation phase

### CORE

#### `core.providers` (activate: `v0.3.1`)
- `provider_id` UUID PK
- `code` TEXT UNIQUE NOT NULL
- `name` TEXT NOT NULL
- `category` TEXT NOT NULL
- `base_url` TEXT
- `auth_type` TEXT
- `is_active` BOOLEAN NOT NULL DEFAULT TRUE
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `core.modules` (activate: `v0.3.1`)
- `module_id` UUID PK
- `code` TEXT UNIQUE NOT NULL
- `name` TEXT NOT NULL
- `description` TEXT
- `owner` TEXT
- `is_active` BOOLEAN NOT NULL DEFAULT TRUE
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `core.sync_runs` (activate: `v0.3.2`)
- `sync_run_id` UUID PK
- `provider_id` UUID FK -> `core.providers.provider_id`
- `module_id` UUID FK -> `core.modules.module_id`
- `pipeline_name` TEXT NOT NULL
- `run_type` TEXT NOT NULL
- `status` TEXT NOT NULL
- `started_at` TIMESTAMPTZ NOT NULL
- `ended_at` TIMESTAMPTZ
- `rows_read` BIGINT
- `rows_written` BIGINT
- `error_text` TEXT
- `meta_json` JSONB

#### `core.raw_payloads` (activate: `v0.3.2`)
- `raw_payload_id` UUID PK
- `sync_run_id` UUID FK -> `core.sync_runs.sync_run_id`
- `provider_id` UUID FK -> `core.providers.provider_id`
- `endpoint` TEXT NOT NULL
- `external_id` TEXT
- `fetched_at` TIMESTAMPTZ NOT NULL
- `payload_json` JSONB NOT NULL

### CATALOG

#### `catalog.event_types` (activate: `v0.3.1`)
- `event_type_id` UUID PK
- `code` TEXT UNIQUE NOT NULL
- `name` TEXT NOT NULL
- `domain` TEXT NOT NULL
- `description` TEXT
- `default_horizon` TEXT
- `resolution_policy` TEXT
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.information_profiles` (activate: `v0.3.1`)
- `information_profile_id` UUID PK
- `code` TEXT UNIQUE NOT NULL
- `name` TEXT NOT NULL
- `description` TEXT
- `min_sources` INTEGER NOT NULL DEFAULT 1
- `required_fields_json` JSONB
- `refresh_interval_sec` INTEGER
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.events` (activate: `v0.3.1`)
- `event_id` UUID PK
- `event_type_id` UUID FK -> `catalog.event_types.event_type_id`
- `information_profile_id` UUID FK -> `catalog.information_profiles.information_profile_id`
- `title` TEXT NOT NULL
- `canonical_slug` TEXT
- `status` TEXT NOT NULL
- `start_time` TIMESTAMPTZ
- `end_time` TIMESTAMPTZ
- `resolution_time` TIMESTAMPTZ
- `metadata_json` JSONB
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.event_external_refs` (activate: `v0.3.1`)
- `event_ref_id` UUID PK
- `event_id` UUID FK -> `catalog.events.event_id`
- `provider_id` UUID FK -> `core.providers.provider_id`
- `external_id` TEXT NOT NULL
- `external_slug` TEXT
- `external_url` TEXT
- `is_primary` BOOLEAN NOT NULL DEFAULT FALSE
- `raw_summary_json` JSONB
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.markets` (activate: `v0.3.1`)
- `market_id` UUID PK
- `event_id` UUID FK -> `catalog.events.event_id`
- `question` TEXT NOT NULL
- `market_type` TEXT
- `condition_id` TEXT
- `market_slug` TEXT
- `open_time` TIMESTAMPTZ
- `close_time` TIMESTAMPTZ
- `settled_time` TIMESTAMPTZ
- `settlement_status` TEXT
- `metadata_json` JSONB
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.market_external_refs` (activate: `v0.3.1`)
- `market_ref_id` UUID PK
- `market_id` UUID FK -> `catalog.markets.market_id`
- `provider_id` UUID FK -> `core.providers.provider_id`
- `external_market_id` TEXT NOT NULL
- `external_condition_id` TEXT
- `external_slug` TEXT
- `raw_summary_json` JSONB
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.outcomes` (activate: `v0.3.1`)
- `outcome_id` UUID PK
- `market_id` UUID FK -> `catalog.markets.market_id`
- `outcome_index` INTEGER NOT NULL
- `outcome_label` TEXT NOT NULL
- `token_id` TEXT
- `is_winner` BOOLEAN
- `metadata_json` JSONB
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.event_module_bindings` (activate: `v0.3.2`)
- `event_module_binding_id` UUID PK
- `event_id` UUID FK -> `catalog.events.event_id`
- `module_id` UUID FK -> `core.modules.module_id`
- `priority` INTEGER NOT NULL DEFAULT 100
- `enabled_for_trading` BOOLEAN NOT NULL DEFAULT TRUE
- `notes` TEXT
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `catalog.event_information_scores` (activate: `v0.4.5`)
- `event_id` UUID FK -> `catalog.events.event_id`
- `scored_at` TIMESTAMPTZ NOT NULL
- `information_profile_id` UUID FK -> `catalog.information_profiles.information_profile_id`
- `coverage_score` NUMERIC(5,2)
- `quality_score` NUMERIC(5,2)
- `latency_score` NUMERIC(5,2)
- `is_trade_eligible` BOOLEAN NOT NULL DEFAULT FALSE
- `missing_fields_json` JSONB
- PK (`event_id`, `scored_at`)

#### `catalog.market_state_snapshots` (activate: `v0.4.2`)
- `market_state_snapshot_id` UUID PK
- `market_id` UUID FK -> `catalog.markets.market_id`
- `sync_run_id` UUID FK -> `core.sync_runs.sync_run_id`
- `captured_at` TIMESTAMPTZ NOT NULL
- `last_price` NUMERIC(10,6)
- `volume` NUMERIC(18,6)
- `liquidity` NUMERIC(18,6)
- `best_bid` NUMERIC(10,6)
- `best_ask` NUMERIC(10,6)
- `mid_price` NUMERIC(10,6)
- `market_status` TEXT
- `raw_json` JSONB

### MARKET DATA

#### `market_data.outcome_price_ticks` (activate: `v0.3.4`)
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `ts` TIMESTAMPTZ NOT NULL
- `source` TEXT NOT NULL
- `price` NUMERIC(10,6)
- `bid` NUMERIC(10,6)
- `ask` NUMERIC(10,6)
- `volume` NUMERIC(18,6)
- `liquidity` NUMERIC(18,6)
- `raw_json` JSONB
- PK (`outcome_id`, `ts`, `source`)
- append-only enforcement via `market_data.enforce_append_only()` trigger (migration `0004_v0_3_4__market_data_append_only.sql`)

#### `market_data.outcome_price_candles` (activate: `v0.4.6`)
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `timeframe` TEXT NOT NULL
- `open_time` TIMESTAMPTZ NOT NULL
- `source` TEXT NOT NULL
- `open` NUMERIC(10,6)
- `high` NUMERIC(10,6)
- `low` NUMERIC(10,6)
- `close` NUMERIC(10,6)
- `volume` NUMERIC(18,6)
- `raw_json` JSONB
- PK (`outcome_id`, `timeframe`, `open_time`, `source`)

#### `market_data.orderbook_snapshots` (activate: `v0.3.4`)
- `orderbook_snapshot_id` UUID PK
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `captured_at` TIMESTAMPTZ NOT NULL
- `best_bid` NUMERIC(10,6)
- `best_ask` NUMERIC(10,6)
- `spread` NUMERIC(10,6)
- `mid_price` NUMERIC(10,6)
- `bid_depth` NUMERIC(18,6)
- `ask_depth` NUMERIC(18,6)
- `raw_json` JSONB
- append-only enforcement via `market_data.enforce_append_only()` trigger (migration `0004_v0_3_4__market_data_append_only.sql`)

#### `market_data.orderbook_levels` (activate: `v0.3.4`)
- `orderbook_snapshot_id` UUID FK -> `market_data.orderbook_snapshots.orderbook_snapshot_id`
- `side` TEXT NOT NULL
- `level_no` INTEGER NOT NULL
- `price` NUMERIC(10,6)
- `size` NUMERIC(18,6)
- `order_count` INTEGER
- PK (`orderbook_snapshot_id`, `side`, `level_no`)
- append-only enforcement via `market_data.enforce_append_only()` trigger (migration `0004_v0_3_4__market_data_append_only.sql`)

### PORTFOLIO

#### `portfolio.trading_accounts` (activate: `v0.3.3`)
- `account_id` UUID PK
- `provider_id` UUID FK -> `core.providers.provider_id`
- `account_label` TEXT NOT NULL
- `wallet_address` TEXT
- `proxy_wallet_address` TEXT
- `chain_id` INTEGER
- `is_active` BOOLEAN NOT NULL DEFAULT TRUE
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `portfolio.position_snapshots` (activate: `v0.3.3`)
- `account_id` UUID FK -> `portfolio.trading_accounts.account_id`
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `captured_at` TIMESTAMPTZ NOT NULL
- `source` TEXT NOT NULL
- `size` NUMERIC(18,6)
- `avg_price` NUMERIC(10,6)
- `current_price` NUMERIC(10,6)
- `current_value` NUMERIC(18,6)
- `unrealized_pnl` NUMERIC(18,6)
- `realized_pnl` NUMERIC(18,6)
- `raw_json` JSONB
- PK (`account_id`, `outcome_id`, `captured_at`, `source`)

#### `portfolio.orders` (activate: `v0.3.3`)
- `order_id` UUID PK
- `account_id` UUID FK -> `portfolio.trading_accounts.account_id`
- `market_id` UUID FK -> `catalog.markets.market_id`
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `external_order_id` TEXT
- `client_order_id` TEXT
- `side` TEXT NOT NULL
- `order_type` TEXT NOT NULL
- `time_in_force` TEXT
- `limit_price` NUMERIC(10,6)
- `size` NUMERIC(18,6)
- `status` TEXT NOT NULL
- `placed_at` TIMESTAMPTZ NOT NULL
- `updated_at` TIMESTAMPTZ NOT NULL
- `metadata_json` JSONB

#### `portfolio.order_events` (activate: `v0.4.3`)
- `order_event_id` UUID PK
- `order_id` UUID FK -> `portfolio.orders.order_id`
- `event_time` TIMESTAMPTZ NOT NULL
- `event_type` TEXT NOT NULL
- `filled_size_delta` NUMERIC(18,6)
- `filled_notional_delta` NUMERIC(18,6)
- `raw_json` JSONB

#### `portfolio.trades` (activate: `v0.3.3`)
- `trade_id` UUID PK
- `account_id` UUID FK -> `portfolio.trading_accounts.account_id`
- `order_id` UUID FK -> `portfolio.orders.order_id`
- `market_id` UUID FK -> `catalog.markets.market_id`
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `external_trade_id` TEXT
- `tx_hash` TEXT
- `side` TEXT NOT NULL
- `price` NUMERIC(10,6)
- `size` NUMERIC(18,6)
- `fee` NUMERIC(18,6)
- `fee_asset` TEXT
- `liquidity_role` TEXT
- `trade_time` TIMESTAMPTZ NOT NULL
- `raw_json` JSONB

#### `portfolio.valuation_snapshots` (activate: `v0.6.2`)
- `account_id` UUID FK -> `portfolio.trading_accounts.account_id`
- `captured_at` TIMESTAMPTZ NOT NULL
- `equity_usd` NUMERIC(18,6)
- `cash_usd` NUMERIC(18,6)
- `positions_value_usd` NUMERIC(18,6)
- `realized_pnl_usd` NUMERIC(18,6)
- `unrealized_pnl_usd` NUMERIC(18,6)
- PK (`account_id`, `captured_at`)

### STRATEGY CONTROL PLANE

#### `strategy.strategy_types` (activate: `v0.3.5`)
- `strategy_type_id` UUID PK
- `module_id` UUID FK -> `core.modules.module_id`
- `code` TEXT UNIQUE NOT NULL
- `name` TEXT NOT NULL
- `description` TEXT
- `execution_mode` TEXT
- `parameter_schema_json` JSONB
- `risk_schema_json` JSONB
- `is_active` BOOLEAN NOT NULL DEFAULT TRUE
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `strategy.strategy_definitions` (activate: `v0.3.5`)
- `strategy_definition_id` UUID PK
- `strategy_type_id` UUID FK -> `strategy.strategy_types.strategy_type_id`
- `name` TEXT NOT NULL
- `version` TEXT NOT NULL
- `config_json` JSONB NOT NULL
- `default_risk_json` JSONB
- `is_active` BOOLEAN NOT NULL DEFAULT TRUE
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `strategy.strategy_instances` (activate: `v0.3.5`)
- `strategy_instance_id` UUID PK
- `strategy_definition_id` UUID FK -> `strategy.strategy_definitions.strategy_definition_id`
- `account_id` UUID FK -> `portfolio.trading_accounts.account_id`
- `state` TEXT NOT NULL
- `capital_allocated_usd` NUMERIC(18,6)
- `started_at` TIMESTAMPTZ
- `ended_at` TIMESTAMPTZ
- `notes` TEXT
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `strategy.strategy_targets` (activate: `v0.3.5`)
- `strategy_target_id` UUID PK
- `strategy_instance_id` UUID FK -> `strategy.strategy_instances.strategy_instance_id`
- `event_id` UUID FK -> `catalog.events.event_id`
- `market_id` UUID FK -> `catalog.markets.market_id`
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `target_role` TEXT
- `entry_window_start` TIMESTAMPTZ
- `entry_window_end` TIMESTAMPTZ
- `priority` INTEGER NOT NULL DEFAULT 100
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `strategy.strategy_signals` (activate: `v0.4.5`)
- `signal_id` UUID PK
- `strategy_instance_id` UUID FK -> `strategy.strategy_instances.strategy_instance_id`
- `event_id` UUID FK -> `catalog.events.event_id`
- `outcome_id` UUID FK -> `catalog.outcomes.outcome_id`
- `fired_at` TIMESTAMPTZ NOT NULL
- `signal_type` TEXT
- `confidence` NUMERIC(5,4)
- `score` NUMERIC(10,6)
- `decision` TEXT
- `reasoning` TEXT
- `features_json` JSONB

### NBA MODULE (EXTENSION)

#### `nba.nba_teams` (activate: `v0.4.4`)
- `team_id` INTEGER PK
- `team_slug` TEXT NOT NULL
- `team_name` TEXT NOT NULL
- `team_city` TEXT
- `conference` TEXT
- `division` TEXT
- `metadata_json` JSONB
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `nba.nba_games` (activate: `v0.4.4`)
- `game_id` TEXT PK
- `season` TEXT
- `game_date` DATE
- `game_start_time` TIMESTAMPTZ
- `game_status` INTEGER
- `game_status_text` TEXT
- `period` INTEGER
- `game_clock` TEXT
- `home_team_id` INTEGER FK -> `nba.nba_teams.team_id`
- `away_team_id` INTEGER FK -> `nba.nba_teams.team_id`
- `home_team_slug` TEXT
- `away_team_slug` TEXT
- `home_score` INTEGER
- `away_score` INTEGER
- `updated_at` TIMESTAMPTZ

#### `nba.nba_game_event_links` (activate: `v0.4.4`)
- `nba_game_event_link_id` UUID PK
- `game_id` TEXT FK -> `nba.nba_games.game_id`
- `event_id` UUID FK -> `catalog.events.event_id`
- `confidence` NUMERIC(5,4)
- `linked_by` TEXT
- `linked_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `nba.nba_team_stats_snapshots` (activate: `v0.4.4`)
- `team_id` INTEGER FK -> `nba.nba_teams.team_id`
- `season` TEXT NOT NULL
- `captured_at` TIMESTAMPTZ NOT NULL
- `metric_set` TEXT NOT NULL
- `stats_json` JSONB NOT NULL
- `source` TEXT
- PK (`team_id`, `season`, `captured_at`, `metric_set`)

#### `nba.nba_player_stats_snapshots` (activate: `v0.4.4`)
- `player_id` INTEGER NOT NULL
- `player_name` TEXT
- `team_id` INTEGER FK -> `nba.nba_teams.team_id`
- `season` TEXT NOT NULL
- `captured_at` TIMESTAMPTZ NOT NULL
- `metric_set` TEXT NOT NULL
- `stats_json` JSONB NOT NULL
- `source` TEXT
- PK (`player_id`, `season`, `captured_at`, `metric_set`)

#### `nba.nba_team_insights` (activate: `v0.4.4`)
- `insight_id` UUID PK
- `team_id` INTEGER FK -> `nba.nba_teams.team_id`
- `insight_type` TEXT
- `category` TEXT
- `text` TEXT
- `condition` TEXT
- `value` TEXT
- `source` TEXT
- `captured_at` TIMESTAMPTZ NOT NULL

#### `nba.nba_live_game_snapshots` (activate: `v0.1.5`, persisted to new schema in `v0.4.4`)
- `game_id` TEXT FK -> `nba.nba_games.game_id`
- `captured_at` TIMESTAMPTZ NOT NULL
- `period` INTEGER
- `clock` TEXT
- `home_score` INTEGER
- `away_score` INTEGER
- `payload_json` JSONB
- PK (`game_id`, `captured_at`)

#### `nba.nba_play_by_play` (activate: `v0.1.5`, persisted to new schema in `v0.4.4`)
- `game_id` TEXT FK -> `nba.nba_games.game_id`
- `event_index` BIGINT NOT NULL
- `action_id` TEXT
- `period` INTEGER
- `clock` TEXT
- `description` TEXT
- `home_score` INTEGER
- `away_score` INTEGER
- `is_score_change` BOOLEAN
- `payload_json` JSONB
- PK (`game_id`, `event_index`)

#### `nba.nba_context_cache` (activate: `v0.7.4`)
- `game_id` TEXT FK -> `nba.nba_games.game_id`
- `context_type` TEXT NOT NULL
- `generated_at` TIMESTAMPTZ NOT NULL
- `payload_json` JSONB NOT NULL
- PK (`game_id`, `context_type`, `generated_at`)

### RESEARCH / CHROMA LINK

#### `research.event_collections` (activate: `v0.8.2`)
- `event_collection_id` UUID PK
- `event_id` UUID FK -> `catalog.events.event_id`
- `collection_name` TEXT NOT NULL
- `collection_type` TEXT NOT NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `research.event_documents` (activate: `v0.8.3`)
- `event_document_id` UUID PK
- `event_id` UUID FK -> `catalog.events.event_id`
- `source` TEXT
- `title` TEXT
- `url` TEXT
- `published_at` TIMESTAMPTZ
- `ingested_at` TIMESTAMPTZ NOT NULL
- `document_json` JSONB NOT NULL
- `chroma_doc_id` TEXT
- `metadata_json` JSONB

### OPS

#### `ops.job_definitions` (activate: `v0.5.1`)
- `job_id` UUID PK
- `module_id` UUID FK -> `core.modules.module_id`
- `job_code` TEXT UNIQUE NOT NULL
- `description` TEXT
- `schedule_cron` TEXT
- `is_enabled` BOOLEAN NOT NULL DEFAULT TRUE
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

#### `ops.job_runs` (activate: `v0.5.1`)
- `job_run_id` UUID PK
- `job_id` UUID FK -> `ops.job_definitions.job_id`
- `sync_run_id` UUID FK -> `core.sync_runs.sync_run_id`
- `started_at` TIMESTAMPTZ NOT NULL
- `ended_at` TIMESTAMPTZ
- `status` TEXT NOT NULL
- `error_text` TEXT
- `metrics_json` JSONB

#### `ops.system_heartbeats` (activate: `v0.5.1`)
- `service_name` TEXT PK
- `status` TEXT NOT NULL
- `last_heartbeat` TIMESTAMPTZ NOT NULL
- `message` TEXT

## Minimal table rollout (phase-aligned)
### `v0.3.1` (`0001_v0_3_1__catalog_core_mvp.sql`)
1. `core.providers`
2. `core.modules`
3. `catalog.event_types`
4. `catalog.information_profiles`
5. `catalog.events`
6. `catalog.event_external_refs`
7. `catalog.markets`
8. `catalog.market_external_refs`
9. `catalog.outcomes`

### `v0.3.2` (`0002_v0_3_2__sync_and_raw_payloads.sql`)
1. `core.sync_runs`
2. `core.raw_payloads`
3. `catalog.event_module_bindings`

### `v0.3.3` (`0003_v0_3_3__portfolio_core_tables.sql`)
1. `portfolio.trading_accounts`
2. `portfolio.position_snapshots`
3. `portfolio.orders`
4. `portfolio.trades`

### `v0.3.4` (`0004_v0_3_4__market_data_append_only.sql`)
1. `market_data.outcome_price_ticks`
2. `market_data.orderbook_snapshots`
3. `market_data.orderbook_levels`

### `v0.4.2` (`0005_v0_4_2__catalog_market_state_snapshots.sql`)
1. `catalog.market_state_snapshots`

### `v0.4.3` (`0006_v0_4_3__portfolio_order_events.sql`)
1. `portfolio.order_events`

### `v0.4.4` (`0007_v0_4_4__nba_ingestion_tables.sql`)
1. `nba.nba_teams`
2. `nba.nba_games`
3. `nba.nba_game_event_links`
4. `nba.nba_team_stats_snapshots`
5. `nba.nba_player_stats_snapshots`
6. `nba.nba_team_insights`
7. `nba.nba_live_game_snapshots`
8. `nba.nba_play_by_play`

### `v0.4.5` (`0008_v0_4_5__catalog_event_information_scores.sql`)
1. `catalog.event_information_scores`

### `v0.4.6` (`0009_v0_4_6__market_data_outcome_price_candles.sql`)
1. `market_data.outcome_price_candles`

All other tables remain deferred until their checkpoint phase is completed.

## Required indices and uniqueness (apply by phase)
- `catalog.events(canonical_slug)` unique where not null (`v0.3.1`)
- `catalog.event_external_refs(provider_id, external_id)` unique (`v0.3.1`)
- `catalog.market_external_refs(provider_id, external_market_id)` unique (`v0.3.1`)
- `catalog.outcomes(market_id, outcome_index)` unique (`v0.3.1`)
- `catalog.outcomes(token_id)` index (`v0.3.1`)
- `catalog.event_module_bindings(event_id, module_id)` unique (`v0.3.2`)
- `core.sync_runs(provider_id, started_at DESC)` index (`v0.3.2`)
- `market_data.outcome_price_ticks(outcome_id, ts DESC)` index (`v0.3.4`)
- `market_data.orderbook_snapshots(outcome_id, captured_at DESC)` index (`v0.3.4`)
- `portfolio.orders(account_id, client_order_id)` unique where `client_order_id` is not null (`v0.3.3`)
- `portfolio.orders(account_id, status, placed_at DESC)` index (`v0.3.3`)
- `portfolio.trades(account_id, trade_time DESC)` index (`v0.3.3`)
- `catalog.market_state_snapshots(market_id, captured_at, sync_run_id)` unique (`v0.4.2`)
- `catalog.market_state_snapshots(market_id, captured_at DESC)` index (`v0.4.2`)
- `portfolio.order_events(order_id, event_time, event_type)` unique (`v0.4.3`)
- `portfolio.order_events(order_id, event_time DESC)` index (`v0.4.3`)
- `nba.nba_games(game_date, game_status)` index (`v0.4.4`)
- `nba.nba_game_event_links(game_id)` index (`v0.4.4`)
- `nba.nba_game_event_links(event_id)` index (`v0.4.4`)
- `nba.nba_live_game_snapshots(game_id, captured_at DESC)` index (`v0.4.4`)
- `nba.nba_play_by_play(game_id, period, event_index)` index (`v0.4.4`)
- `catalog.event_information_scores(scored_at DESC)` index (`v0.4.5`)
- `catalog.event_information_scores(is_trade_eligible, scored_at DESC)` index (`v0.4.5`)
- `market_data.outcome_price_candles(outcome_id, timeframe, open_time DESC)` index (`v0.4.6`)

## Table-phase-columns relation checklist
This document is authoritative for:
- full table inventory,
- full column inventory,
- phase activation rules.

On every schema change you must update:
1. this file,
2. `app/docs/development_guide.md` current phase section,
3. corresponding file under `dev-checkpoint/`.

