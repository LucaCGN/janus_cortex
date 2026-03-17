# Janus Cortex API Routes Proposal (Phase-Gated to v1)

## Purpose
This route plan is coupled to `app/docs/scalable_db_schema_proposal.md`.
A route can only be implemented when all required tables are active in its phase.

## API principles
- Prefix: `/v1`
- JSON only
- Idempotency key on write endpoints
- Long sync calls: `202 Accepted` + `job_run_id`
- No autonomous strategy execution in v1 (data serving only)
- Route activation requires matching `pytest` coverage for backing node methods and endpoint behavior.

## Activation matrix

### `v0.5.1` - System and registry foundation
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/health` | `ops.system_heartbeats` |
| GET | `/v1/providers` | `core.providers` |
| POST | `/v1/providers` | `core.providers` |
| GET | `/v1/modules` | `core.modules` |
| POST | `/v1/modules` | `core.modules` |
| GET | `/v1/sync-runs` | `core.sync_runs` |
| GET | `/v1/sync-runs/{sync_run_id}` | `core.sync_runs`, `core.raw_payloads` |

### `v0.5.2` - Catalog read/write base
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/event-types` | `catalog.event_types` |
| POST | `/v1/event-types` | `catalog.event_types` |
| GET | `/v1/information-profiles` | `catalog.information_profiles` |
| POST | `/v1/information-profiles` | `catalog.information_profiles` |
| POST | `/v1/events` | `catalog.events` |
| GET | `/v1/events` | `catalog.events` |
| GET | `/v1/events/{event_id}` | `catalog.events` |
| PATCH | `/v1/events/{event_id}` | `catalog.events` |

### `v0.5.3` - Event import and market graph
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/events/import-url` | `catalog.events`, `catalog.event_external_refs`, `catalog.markets`, `catalog.outcomes`, `core.sync_runs` |
| GET | `/v1/events/{event_id}/markets` | `catalog.markets` |
| GET | `/v1/markets/{market_id}` | `catalog.markets` |
| GET | `/v1/markets/{market_id}/outcomes` | `catalog.outcomes` |
| GET | `/v1/outcomes/{outcome_id}` | `catalog.outcomes` |
| GET | `/v1/outcomes/by-token/{token_id}` | `catalog.outcomes` |

### `v0.5.4` - Sync trigger routes (no strategy behavior)
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/sync/polymarket/events` | `core.sync_runs`, `catalog.events`, `catalog.event_external_refs` |
| POST | `/v1/sync/polymarket/markets` | `core.sync_runs`, `catalog.markets`, `catalog.market_external_refs`, `catalog.outcomes`, `catalog.market_state_snapshots` |
| POST | `/v1/sync/nba/schedule` | `core.sync_runs`, `nba.nba_games` |
| POST | `/v1/sync/nba/teams` | `core.sync_runs`, `nba.nba_teams`, `nba.nba_team_stats_snapshots` |
| POST | `/v1/sync/nba/players` | `core.sync_runs`, `nba.nba_player_stats_snapshots` |
| POST | `/v1/sync/nba/insights` | `core.sync_runs`, `nba.nba_team_insights` |
| POST | `/v1/sync/nba/mappings` | `core.sync_runs`, `nba.nba_game_event_links` |
| GET | `/v1/sync/jobs/runs` | `ops.job_runs`, `ops.job_definitions`, `core.sync_runs` |

### `v0.5.6` - Validation bridge routes (early activation)
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/games` | `nba.nba_games` |

### `v0.6.1` - Market data retrieval routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/outcomes/{outcome_id}/prices/ticks` | `market_data.outcome_price_ticks` |
| GET | `/v1/outcomes/{outcome_id}/prices/candles` | `market_data.outcome_price_candles` |
| GET | `/v1/outcomes/{outcome_id}/orderbook/latest` | `market_data.orderbook_snapshots`, `market_data.orderbook_levels` |
| GET | `/v1/outcomes/{outcome_id}/orderbook/history` | `market_data.orderbook_snapshots`, `market_data.orderbook_levels` |
| GET | `/v1/markets/{market_id}/state/latest` | `catalog.market_state_snapshots` |
| GET | `/v1/events/{event_id}/odds/latest` | `catalog.outcomes`, `market_data.outcome_price_ticks` |

### `v0.6.2` - Portfolio read routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/portfolio/accounts` | `portfolio.trading_accounts` |
| POST | `/v1/portfolio/accounts` | `portfolio.trading_accounts` |
| GET | `/v1/portfolio/summary` | `portfolio.valuation_snapshots`, `portfolio.position_snapshots` |
| GET | `/v1/portfolio/positions` | `portfolio.position_snapshots` |
| GET | `/v1/portfolio/positions/history` | `portfolio.position_snapshots` |
| GET | `/v1/portfolio/orders` | `portfolio.orders` |
| GET | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events` |
| GET | `/v1/portfolio/trades` | `portfolio.trades` |

### `v0.6.3` - Portfolio action routes (manual only)
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/portfolio/orders` | `portfolio.orders`, `portfolio.order_events` |
| DELETE | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events` |
| POST | `/v1/sync/polymarket/positions` | `portfolio.position_snapshots`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/orders` | `portfolio.orders`, `portfolio.order_events`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/trades` | `portfolio.trades`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/orderbook` | `market_data.orderbook_snapshots`, `market_data.orderbook_levels`, `core.sync_runs` |
| POST | `/v1/sync/polymarket/prices` | `market_data.outcome_price_ticks`, `market_data.outcome_price_candles`, `core.sync_runs` |

### `v0.6.4` - Order lifecycle capture and reconciliation routes
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/portfolio/orders` | `portfolio.orders`, `portfolio.order_events` |
| DELETE | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events` |
| GET | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events`, `portfolio.trades` |

### `v0.6.5` - Risk guards and rate controls
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/portfolio/orders` | `portfolio.orders`, `portfolio.order_events` |
| DELETE | `/v1/portfolio/orders/{order_id}` | `portfolio.orders`, `portfolio.order_events` |

### `v0.6.6` - End-to-end validation routes in scope
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/games` | `nba.nba_games` |
| POST | `/v1/sync/nba/schedule` | `core.sync_runs`, `nba.nba_games`, `nba.nba_live_game_snapshots`, `nba.nba_play_by_play` |
| POST | `/v1/sync/polymarket/markets` | `core.sync_runs`, `catalog.events`, `catalog.markets`, `catalog.outcomes`, `catalog.market_state_snapshots` |
| GET | `/v1/events/{event_id}/odds/latest` | `catalog.markets`, `catalog.outcomes`, `market_data.outcome_price_ticks` |
| GET | `/v1/outcomes/{outcome_id}/prices/ticks` | `market_data.outcome_price_ticks` |
| GET | `/v1/portfolio/orders` | `portfolio.orders` |

### `v0.7.1` - NBA read routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/games` | `nba.nba_games` |
| GET | `/v1/nba/games/{game_id}` | `nba.nba_games` |
| GET | `/v1/nba/games/{game_id}/event-links` | `nba.nba_game_event_links`, `catalog.events` |
| POST | `/v1/nba/games/{game_id}/event-links` | `nba.nba_game_event_links` |
| GET | `/v1/nba/teams` | `nba.nba_teams` |
| GET | `/v1/nba/teams/{team_id}/stats` | `nba.nba_team_stats_snapshots` |
| GET | `/v1/nba/teams/{team_id}/insights` | `nba.nba_team_insights` |
| GET | `/v1/nba/players` | `nba.nba_player_stats_snapshots` |

### `v0.7.2` - Closure validation support route
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/sync/polymarket/closed-positions/consolidate` | `portfolio.trading_accounts`, `portfolio.position_snapshots`, `portfolio.valuation_snapshots`, `portfolio.orders`, `portfolio.trades`, `catalog.events`, `catalog.markets`, `catalog.outcomes`, `core.sync_runs` |

### `v0.7.3` - NBA live snapshot and play-by-play routes
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/sync/nba/live/{game_id}` | `nba.nba_live_game_snapshots`, `nba.nba_play_by_play`, `core.sync_runs` |
| GET | `/v1/nba/games/{game_id}/live` | `nba.nba_live_game_snapshots`, `nba.nba_play_by_play` |
| GET | `/v1/nba/games/{game_id}/play-by-play` | `nba.nba_play_by_play` |

### `v0.7.4` - NBA context routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/games/{game_id}/context/pre` | `nba.nba_context_cache` or on-demand pipelines |
| GET | `/v1/nba/games/{game_id}/context/live` | `nba.nba_context_cache` or on-demand pipelines |

### `v0.8.5` - NBA regular-season feature routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/games/{game_id}/features/latest` | `nba.nba_game_feature_snapshots`, `nba.nba_games` |
| GET | `/v1/nba/seasons/{season}/games/features` | `nba.nba_game_feature_snapshots`, `nba.nba_games` |
| GET | `/v1/nba/seasons/{season}/odds-coverage` | `nba.nba_odds_coverage_audits`, `nba.nba_games`, `catalog.events` |

### `v0.8.6` - NBA regular-season refresh routes
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/sync/nba/season-refresh` | `ops.job_runs`, `ops.job_definitions`, `nba.nba_game_feature_snapshots`, `nba.nba_odds_coverage_audits`, `nba.nba_team_feature_rollups` |

### `v0.8.7` - NBA team rollup routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/seasons/{season}/teams/feature-rollups` | `nba.nba_team_feature_rollups`, `nba.nba_teams` |
| GET | `/v1/nba/teams/{team_id}/feature-rollups` | `nba.nba_team_feature_rollups`, `nba.nba_teams` |

### `v0.9.4` - NBA playoff routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/nba/playoffs/{season}/series` | `nba.nba_playoff_series` |
| GET | `/v1/nba/playoffs/series/{series_id}` | `nba.nba_playoff_series`, `nba.nba_playoff_series_game_links`, `nba.nba_games` |
| GET | `/v1/nba/playoffs/series/{series_id}/features` | `nba.nba_playoff_feature_snapshots` |
| GET | `/v1/nba/playoffs/games/{game_id}/features/latest` | `nba.nba_playoff_feature_snapshots` |

### `v1.5.1` - Sports orchestration routes
| Method | Route | Required tables |
|---|---|---|
| GET | `/v1/jobs` | `ops.job_definitions` |
| POST | `/v1/jobs` | `ops.job_definitions` |
| POST | `/v1/jobs/{job_code}/run` | `ops.job_runs`, `core.sync_runs` |
| GET | `/v1/jobs/runs` | `ops.job_runs` |

### `v1.7.1` - Sports research routes
| Method | Route | Required tables |
|---|---|---|
| POST | `/v1/research/collections` | `research.event_collections` |
| GET | `/v1/research/collections` | `research.event_collections` |
| POST | `/v1/research/documents` | `research.event_documents` |
| POST | `/v1/research/documents/batch` | `research.event_documents` |
| GET | `/v1/events/{event_id}/documents` | `research.event_documents` |
| POST | `/v1/events/{event_id}/documents/query` | `research.event_documents` plus optional research backend |

## Compatibility aliases (temporary)
- `POST /place_order` -> `POST /v1/portfolio/orders`
- `GET /view_portfolio` -> `GET /v1/portfolio/positions`
- `GET /view_trade_history` -> `GET /v1/portfolio/trades`
- `POST /cancel_order` -> `DELETE /v1/portfolio/orders/{order_id}`
- `POST /add_event` -> `POST /v1/events/import-url`

## NBA-first v1 endpoint completion checklist
NBA-first v1 readiness is reached when all route groups below are active and tested:
1. `v0.5.*` system, catalog, and sync triggers
2. `v0.6.*` market data, portfolio, and lifecycle or risk controls
3. `v0.7.*` NBA live module and context delivery
4. `v0.8.*` regular-season feature dataset and coverage-audit delivery
5. `v0.9.*` playoff series and playoff feature delivery

Later `v1.*` route work adds sports orchestration and sports-only research memory, but it does not block the NBA-first cut.

## Route governance rules
- Every new route must reference:
  - activation phase
  - required tables
  - required node methods
  - test script path
- For time-based sync/read routes, tests must explicitly validate past/current/future behavior (or document source-imposed limits).
- No route may be marked active if its dependencies are not complete in corresponding checkpoint file.

## Current source constraints (2026-03-14)
- DB migration baseline is now live through `v0.4.6` using `app/data/databases/migrate.py`; active schemas/tables cover `core`, `catalog`, `portfolio`, `market_data`, and `nba` tables required by the `v0.5.x` API foundation.
- Pre-`v0.8.1` season audit confirmed full finished-game play-by-play coverage and only partial league-wide Polymarket moneyline coverage for `2025/26`; route priorities therefore shift first to feature delivery and coverage auditing, not research-memory endpoints.
- Repository/upsert primitives are now available in `app/data/databases/repositories/upsert_primitives.py` (`JanusUpsertRepository`) and should be reused by future FastAPI handlers to keep write semantics idempotent and append-only-safe.
- Live seed-pack integration for URL-driven event ingestion is validated in `app/data/databases/seed_packs/polymarket_event_seed_pack.py` using three concrete Polymarket URLs (past NBA, upcoming NBA, long-horizon non-sports), providing a direct implementation reference for future `/v1/events/import-url` behavior.
- `v0.4.1` event ingestion pipeline is active via `app/data/pipelines/daily/polymarket/sync_events.py` and `app/ingestion/pipelines/prediction_market_polymarket/sync_events.py`; this is the baseline for `/v1/sync/polymarket/events`.
- `v0.4.2` market/outcome snapshot sync is active via `app/data/pipelines/daily/polymarket/sync_markets.py`; this is the baseline for `/v1/sync/polymarket/markets` and `/v1/markets/{market_id}/state/latest`.
- `v0.4.3` portfolio mirror sync is active via `app/data/pipelines/daily/polymarket/sync_portfolio.py`; this is the baseline for `/v1/sync/polymarket/positions`, `/v1/sync/polymarket/orders`, and `/v1/sync/polymarket/trades`.
- `v0.4.4` NBA metadata/live sync is active via `app/data/pipelines/daily/nba/sync_postgres.py`; this is the baseline for `/v1/sync/nba/*` and `/v1/nba/*` routes.
- `v0.4.5` cross-domain mapping sync is active via `app/data/pipelines/daily/cross_domain/sync_mappings.py`; this is the baseline for `/v1/sync/nba/mappings` and event-quality eligibility views.
- `v0.4.6` backfill/retry orchestration is active via `app/data/pipelines/daily/polymarket/backfill_retry.py`; this is the baseline for reprocessing and candle-building sync triggers.
- Structure gate (`v0.2.7`-`v0.2.9`) is complete: provider-centric wrappers now exist under `app/providers/*`, canonical domain wrappers under `app/domain/events/canonical/*`, and ingestion wrappers under `app/ingestion/*`; upcoming route/service work in `v0.3+` should target these paths first.
- Canonical mapping pre-route layer is now validated in `app/data/pipelines/canonical/*` with fixture-backed integration tests (`tests/app/data/pipelines/canonical/*_pytest.py`); route groups stay phase-gated, but future `/v1/events/import-url` and `/v1/sync/*` routes should consume this canonical contract directly.
- Gamma events sync routes must use timezone-aware filters and split-window validation (past/future windows separately) due query-window sensitivity.
- Gamma events route logic should prefer `tag_slug=nba` and snake_case date params; broad windows still need client-side validation.
- Gamma moneyline route no longer hard-blocked at zero rows after query hardening, but must keep fallback path (`/events` nested markets) and client-side game-date filtering.
- Historical odds acquisition is validated via `app/data/nodes/polymarket/gamma/nba/odds_history_node.py` using CLOB `/prices-history` by token id, with interval fallback and snapshot fallback when direct history is empty.
- Fallback stream-to-history collection is validated via `app/data/nodes/polymarket/gamma/nba/fallback_stream_history_collector.py`, emitting append-only samples (`source=fallback_stream`) for windows where direct history under-returns.
- NBA play-by-play ingestion contract is validated in `app/data/nodes/nba/live/play_by_play.py` with deterministic normalization (`event_index`, score deltas) and idempotent sqlite upsert helper for pre-schema persistence checks.
- Polymarket orderbook polling contract is validated in `app/data/nodes/polymarket/blockchain/stream_orderbook.py` (`OrderbookStreamConfig` + `stream_orderbook`) and is ready for future sync route wiring.
- `v0.5.*` and `v0.6.*` FastAPI routes are now implemented in `app/api/*` and validated with:
  - DB tests: `tests/app/api/test_system_registry_routes_pytest.py`, `tests/app/api/test_catalog_routes_pytest.py`, `tests/app/api/test_sync_routes_pytest.py`, `tests/app/api/test_error_model_pytest.py`
- OpenAPI lock test: `tests/app/api/test_openapi_lock_pytest.py` (snapshot: `app/docs/openapi_v0_8_snapshot.json`)
  - live endpoint validation: `tests/app/api/test_live_today_games_endpoints_pytest.py`
- `v0.6.2` added migration `0011_v0_6_2__portfolio_valuation_snapshots.sql`; `/v1/portfolio/summary` now reads latest valuation rows with fallback aggregation from latest position snapshots.
- `v0.6.3` activated additional sync endpoints:
  - `POST /v1/sync/polymarket/positions`
  - `POST /v1/sync/polymarket/orders`
  - `POST /v1/sync/polymarket/trades`
  - `POST /v1/sync/polymarket/orderbook`
  - `POST /v1/sync/polymarket/prices`
- `v0.6.4` lifecycle capture is active on manual order routes with `portfolio.order_events` entries on place/cancel transitions.
- `v0.6.5` risk controls are active on manual order routes (size/notional bounds + per-account action rate limiting).
- Endpoint-level validation for current NBA slate is active via `GET /v1/nba/games` and expanded `v0.7.1` NBA read routes.
- latest live API validation run (`2026-02-23` UTC) confirmed:
  - `scoreboard_games_total=11`, `scoreboard_status_counts={2:3,3:4,1:4}`
  - `/v1/nba/games` covered same-day slate, live, and upcoming status queries
  - `/v1/sync/polymarket/markets` + `/v1/events` yielded `scoreboard_slug_missing_in_events=0`.
- `v0.7.1` NBA read routes are now validated and active with dedicated pytest coverage:
  - `tests/app/api/test_nba_read_routes_pytest.py`
- manual order path revalidated against requested BOS/LAL event (`nba-bos-lal-2026-02-22`) with real CLOB responses:
  - place+cancel probe succeeded (`manual_place_submitted` -> `manual_cancel_submitted`)
  - two `$1` notional submitted orders left open (one per team outcome).
- `v0.7.2` added closure-validation support route:
  - `POST /v1/sync/polymarket/closed-positions/consolidate`
  - consolidates `data_api_closed_position` snapshots into normalized zero-size closure rows.
  - writes account-level `portfolio.valuation_snapshots`.
  - detects stale event/market conclusion candidates from account exposures.
- `v0.7.3` NBA live/pbp routes are now active and covered by:
  - `tests/app/data/pipelines/daily/nba/test_sync_live_game_pytest.py`
  - `tests/app/api/test_nba_live_context_routes_pytest.py`
  - `tests/app/api/test_nba_selected_game_validation_live_pytest.py`
  - active routes:
    - `POST /v1/sync/nba/live/{game_id}`
    - `GET /v1/nba/games/{game_id}/live`
    - `GET /v1/nba/games/{game_id}/play-by-play`
- `v0.7.4` context routes are now active:
  - `GET /v1/nba/games/{game_id}/context/pre`
  - `GET /v1/nba/games/{game_id}/context/live`
  - backed by `nba.nba_context_cache` plus on-demand rebuild logic in `app/modules/nba/context/service.py`.
- `v0.7.5` selected-game validation packs now rehydrate the current season and verify finished, live, and upcoming game behavior through the API.
- `v0.7.6` added read-path indexes and refreshed the API snapshot to version `0.7.6`.
- `v0.8.1` to `v0.8.8` now have live partial implementation on top of the current DB:
  - new read routes for game features, odds coverage, and team rollups
  - new sync route `POST /v1/sync/nba/season-refresh`
  - FastAPI version now `0.8.1`
  - bounded live validation on `2026-03-14`:
    - `POST /v1/sync/nba/season-refresh` -> `202 success`
    - `GET /v1/nba/seasons/2025-26/games/features` -> populated rows
    - `GET /v1/nba/seasons/2025-26/odds-coverage` -> populated audit rows
    - `GET /v1/nba/seasons/2025-26/teams/feature-rollups` -> populated rollup rows
- refreshed DB snapshot after `v0.7.6` validation:
  - `nba.nba_games=1322`
  - `nba.nba_live_game_snapshots=23`
  - `nba.nba_play_by_play=1571`
  - `nba.nba_context_cache=12`
  - `market_data.outcome_price_ticks=1513`
  - `catalog.events=8`
  - `core.sync_runs=26`
- OpenAPI lock snapshot refreshed after `v0.8.1` activation (`app/docs/openapi_v0_8_snapshot.json`).
