# Crypto Options App API Design

Date: 2026-06-02

Status: technical spec for FastAPI endpoint design.

## Purpose

Define the FastAPI surface for the root `crypto_options_app` modular app.

The API organizes data, replay, strategy management, trading state, and reports. It does not grant order authority by itself.

## API Groups

Base prefix:

- `/v1/crypto-options-app`

Routers:

| Router | Prefix | Purpose |
| --- | --- | --- |
| Health | `/health` | App status, DB status, worker status. |
| Profiles | `/profiles` | Profile universe, grades, activity, reconstruction, generator scores. |
| Signals | `/signals` | Aggregated profile/event/indicator signals. |
| Market data | `/market-data` | Event universe, YES/NO prices, order book snapshots, underlying prices. |
| Indicators | `/indicators` | Indicator definitions, latest snapshots, event contexts. |
| Replay | `/replay` | Datasets, replay frames, replay runs, component results. |
| Strategies | `/strategies` | Strategy specs, registry, versions, readiness, promotion state. |
| Trading state | `/trading` | Candidates, intents, orders, fills, positions, exits, settlements, PnL snapshots. |
| Risk | `/risk` | Risk gate results, stop gates, exposure, stale-data blockers. |
| Reports | `/reports` | Run reports, strategy reports, system summaries. |

## Endpoint Contract Rules

Every response includes:

- `schema_version`
- `generated_at_utc`
- `orders_allowed`
- `live_trading_authorized`

Read-only endpoints set:

- `orders_allowed: false`
- `live_trading_authorized: false`

Execution-adjacent endpoints must require:

- supervised runtime enabled
- explicit live risk acknowledgement
- strategy readiness status
- risk gate pass
- executor boundary pass

The API never accepts ad hoc order instructions from chat or reports.

## Minimum Endpoints

Health:

- `GET /health` - full local integrity snapshot for DB, feed watermarks, strategy registry, validation artifacts, audit completeness, and readiness blockers.
- `GET /health/ping` - process liveness only.
- `GET /health/watermarks`
- `GET /health/workers`

Profiles:

- `GET /profiles`
- `GET /profiles/{profile_key}`
- `GET /profiles/generator-scores`
- `GET /profiles/event-reconstructions`
- `GET /profiles/buying-ahead`

Signals:

- `GET /signals/latest`
- `GET /signals/by-strategy/{strategy_id}`
- `POST /signals/aggregate-preview`

Market and indicators:

- `GET /market-data/events`
- `GET /market-data/event-prices/latest`
- `GET /market-data/underlying/latest`
- `GET /indicators/latest`
- `GET /indicators/event-context/{event_key}`

Replay:

- `POST /replay/datasets`
- `POST /replay/runs`
- `GET /replay/runs/{run_id}`
- `GET /replay/component-results`

Strategies:

- `GET /strategies`
- `GET /strategies/{strategy_id}`
- `POST /strategies/validate`
- `GET /strategies/{strategy_id}/readiness`

Trading state:

- `GET /trading/candidates`
- `GET /trading/intents`
- `GET /trading/orders`
- `GET /trading/positions`
- `GET /trading/exits`

Risk and reports:

- `GET /risk/status`
- `GET /risk/stop-gates`
- `GET /reports/runs/{run_id}`
- `GET /reports/strategies/{strategy_id}`

## Safety Boundary

- API can expose trading state.
- API can validate strategy specs.
- API can request replay jobs.
- API can expose supervised runtime status.
- API cannot submit individual orders directly.
- Strategy execution endpoints, if later added, must only start supervised run configurations and never accept a raw order payload.
- The full `/health` endpoint is read-only and must never authorize trading; it reports whether supervised runtime readiness blockers remain.
- `/health/ping` must not be treated as proof that strategy, order, DB, or reconciliation systems are ready.

## Acceptance Criteria

- API surface covers every app module.
- Endpoint names are stable enough for tests and CD/CI automation.
- Read-only payloads are explicit.
- Execution authority remains isolated in the trading runtime.
