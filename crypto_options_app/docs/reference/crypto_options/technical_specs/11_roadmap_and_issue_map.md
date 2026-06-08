# Crypto Options App Roadmap And Issue Map

Date: 2026-06-02

Status: technical spec for implementation roadmap and GitHub grounding.

## Purpose

Translate the technical specs into an ordered implementation roadmap and GitHub issue map.

Issue #47 remains research/read-only history. The app rebuild is owned by a new implementation parent issue.

## Parent Issue

- [#108 `[CRYPTO-APP] Build modular crypto options app and strategy validation runtime`](https://github.com/LucaCGN/janus_cortex/issues/108)

## Child Issue Map

| Order | Issue | Linked specs |
| --- | --- | --- |
| 1 | [#109 App skeleton and folder structure](https://github.com/LucaCGN/janus_cortex/issues/109) | `00_current_state_inventory.md`, `01_app_folder_structure.md` |
| 2 | [#110 Canonical DB schema and migration/import tools](https://github.com/LucaCGN/janus_cortex/issues/110) | `02_database_design.md` |
| 3 | [#111 Data feed workers and watermarks](https://github.com/LucaCGN/janus_cortex/issues/111) | `04_data_feed_design.md` |
| 4 | [#112 Profile universe and generator scoring](https://github.com/LucaCGN/janus_cortex/issues/112) | `05_profile_and_signal_generator_design.md` |
| 5 | [#113 Market/event/indicator feed integration](https://github.com/LucaCGN/janus_cortex/issues/113) | `04_data_feed_design.md`, active market data spec |
| 6 | [#114 Replay and backtest engine](https://github.com/LucaCGN/janus_cortex/issues/114) | `06_replay_and_backtest_design.md` |
| 7 | [#115 Strategy manager and 10-candidate registry](https://github.com/LucaCGN/janus_cortex/issues/115) | `07_strategy_manager_design.md` |
| 8 | [#116 Trading engine lifecycle and reconciliation](https://github.com/LucaCGN/janus_cortex/issues/116) | `08_trading_engine_design.md` |
| 9 | [#117 Risk, stop gates, observability, and reports](https://github.com/LucaCGN/janus_cortex/issues/117) | `09_risk_observability_design.md` |
| 10 | [#118 CD/CI strategy validation automation](https://github.com/LucaCGN/janus_cortex/issues/118) | `10_testing_and_cdci_design.md` |

## Roadmap

### Milestone 1: App Foundation

- Create root `crypto_options_app` package.
- Add config, FastAPI app builder, DB initializer, health endpoints, and test scaffolding.
- Acceptance: app starts, DB initializes, health endpoint reports watermarks.

### Milestone 2: Canonical Data System

- Implement canonical schema.
- Import legacy profile and market shards.
- Add repositories and parity checks.
- Acceptance: canonical DB contains profile, event, market, and indicator namespaces.

### Milestone 3: Feeds And Signal Generators

- Implement feed workers with bounded async processing and watermarks.
- Implement reconstruction, grading, generator scores, and signal contracts.
- Acceptance: profile/event/indicator signals are DB-backed and observable.

### Milestone 4: Replay And Strategy Manager

- Implement replay frames and strategy registry.
- Register all 10 candidates.
- Acceptance: each candidate can replay or return structured data blockers.

### Milestone 5: Trading Lifecycle And Risk

- Implement candidate, intent, order, fill, position, exit, settlement, PnL, reconciliation, and risk gates.
- Acceptance: structural tests prove no order lifecycle shortcut exists.

### Milestone 6: CD/CI Validation Loop

- Implement automation issue-by-issue.
- Run structural validation before any live pulse.
- Acceptance: all 10 candidates receive structural validation, then standard and promotion tests proceed only through supervised gates.

## Cross-Spec Review Result

Review checklist:

- Canonical DB target is consistent with active data specs.
- Legacy shards are migration inputs, not target architecture.
- Data feeds do not authorize trading.
- Strategy manager emits strategy specs and readiness state only.
- Trading engine owns intents and state; executor boundary owns submitted orders.
- Every BUY path requires lifecycle coverage.
- Every candidate strategy requires replay and pulse acceptance criteria.
- Issue #47 remains research history; a new parent issue owns implementation.

## GitHub Issue URLs

- Parent: [#108](https://github.com/LucaCGN/janus_cortex/issues/108)
- Child issues: [#109](https://github.com/LucaCGN/janus_cortex/issues/109), [#110](https://github.com/LucaCGN/janus_cortex/issues/110), [#111](https://github.com/LucaCGN/janus_cortex/issues/111), [#112](https://github.com/LucaCGN/janus_cortex/issues/112), [#113](https://github.com/LucaCGN/janus_cortex/issues/113), [#114](https://github.com/LucaCGN/janus_cortex/issues/114), [#115](https://github.com/LucaCGN/janus_cortex/issues/115), [#116](https://github.com/LucaCGN/janus_cortex/issues/116), [#117](https://github.com/LucaCGN/janus_cortex/issues/117), [#118](https://github.com/LucaCGN/janus_cortex/issues/118)
