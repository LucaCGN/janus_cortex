# Crypto Options App Folder Structure

Date: 2026-06-02

Status: technical spec for root `crypto_options_app` layout.

## Purpose

Create a modular isolated app at repo root:

- `crypto_options_app/`

The new app must be isolated from current legacy crypto code while still able to import migration helpers or adapters during transition.

## Required Layout

```text
crypto_options_app/
  __init__.py
  main.py
  config.py
  api/
    __init__.py
    app.py
    routers/
      health.py
      profiles.py
      signals.py
      market_data.py
      indicators.py
      replay.py
      strategies.py
      trading_state.py
      risk.py
      reports.py
  db/
    __init__.py
    connection.py
    schema.py
    migrations/
    repositories/
  feeds/
    __init__.py
    profile_activity.py
    polymarket_events.py
    polymarket_prices.py
    underlying_prices.py
  profiles/
    __init__.py
    grading.py
    reconstruction.py
    classifier.py
    generator_scores.py
  signals/
    __init__.py
    contracts.py
    aggregation.py
    profile_signals.py
    event_signals.py
    indicator_signals.py
  indicators/
    __init__.py
    compute.py
    definitions.py
  replay/
    __init__.py
    frames.py
    fill_simulation.py
    exit_simulation.py
    reports.py
  strategies/
    __init__.py
    schema.py
    registry.py
    candidates.py
    readiness.py
  trading/
    __init__.py
    candidates.py
    intents.py
    orders.py
    fills.py
    positions.py
    exits.py
    reconciliation.py
    executor_boundary.py
  risk/
    __init__.py
    gates.py
    stop_gates.py
    exposure.py
  workers/
    __init__.py
    feed_worker.py
    signal_worker.py
    replay_worker.py
    strategy_worker.py
    supervisor.py
  reports/
    __init__.py
    run_report.py
    strategy_report.py
    system_status.py
  tests/
    fixtures/
```

## Module Responsibilities

| Module | Responsibility |
| --- | --- |
| `api` | FastAPI app and read/write-safe endpoint surfaces. |
| `db` | SQLite connection, schema initialization, migrations, repositories. |
| `feeds` | External data ingestion with async queues, rate limits, and watermarks. |
| `profiles` | Account activity normalization, grading, reconstruction, style classification. |
| `signals` | Signal contracts and aggregation outputs consumed by strategies. |
| `indicators` | Indicator definitions and computations over market/underlying data. |
| `replay` | DB-backed replay frames, fill/exit simulation, component comparisons. |
| `strategies` | Strategy spec schema, candidate registry, validation, readiness, promotion state. |
| `trading` | Candidate-to-intent lifecycle, executor boundary, reconciliation, positions, exits. |
| `risk` | Risk gates, stop gates, exposure controls, stale-data blocking. |
| `workers` | App-owned loops for feeds, signals, replay jobs, strategy runs, supervisor. |
| `reports` | Structured status, run, strategy, and promotion/rejection reports. |

## Isolation Rules

- New app code must not be added under `app/services/crypto_options`.
- New app code must not depend on `codex_tool` as an internal library.
- Legacy modules can be imported only through explicit migration/adaptor functions.
- All new durable state goes through `crypto_options_app/db`.
- Tests for the new app should live under `tests/crypto_options_app`.

## App Entrypoints

Initial entrypoints:

- `crypto_options_app/main.py`: imports and exposes the FastAPI app.
- `crypto_options_app/api/app.py`: builds the FastAPI app and mounts routers.
- `crypto_options_app/db/schema.py`: initializes all app tables.
- `crypto_options_app/workers/supervisor.py`: coordinates feed and strategy workers without authorizing orders by itself.

## Acceptance Criteria

- Folder structure can be created without modifying legacy crypto code.
- Every future issue maps to one or more new app modules.
- Legacy imports are explicitly transitional.
- API, data, strategy, trading, and risk concerns are separate modules.
