# Janus Agentic Backend Operating Loop

## Summary

Janus is moving to a backend-first local production service. The internal LLM system has real strategy authority through structured JSON plans that the trading engine validates, monitors, and executes. Codex agents remain external operators for research, health checks, postgame review, and continuous development.

Codex automations, or an equivalent external agent framework, are part of the required operating loop for CI/CD, research, audits, and continuous improvement. They are not part of the critical runtime path for order execution: Janus must continue to ingest data, watch markets, evaluate active plans, reconcile the portfolio, and preserve replay data from the latest valid local state even when Codex is offline.

The canonical local runtime root is:

`C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`

Runtime data under `local/` is not tracked by git. Tracked code and docs define how the service reads and writes that runtime state.

## Core Architecture

- Janus owns ingestion, market watching, strategy-plan execution, order reconciliation, and replay capture.
- Codex owns external research, monitoring, development work, CI/CD-style checks, prompt/documentation discipline, and handoff review.
- Backend API and CLI tools are the production control surface.
- The frontend is removed from the operating path first, then deleted only after backend parity tests pass.
- Future crypto-options and geopolitics branches must reuse the same generic market watch/replay foundation.

## LLM Strategy Authority

The LLM does not place arbitrary undocumented orders. It writes executable `StrategyPlanJSON`.

The trading engine compiles the active plan into monitored triggers and order intents. The order manager enforces mechanical safety: schema validity, stale-feed gates, exchange/account constraints, duplicate prevention, uncovered-position prevention, and portfolio reconciliation.

Required plan fields:

- `schema_version`
- `event_id`
- `market_id`
- `generated_at_utc`
- `valid_until_utc`
- `plan_owner`
- `context_summary`
- `active_strategies`
- `trigger_conditions`
- `portfolio_reconciliation`
- `explainability`

Each active strategy must include:

- `strategy_id`
- `family`
- `side`
- `budget_usd`
- `max_positions`
- `entry_rules`
- `exit_rules`
- `stop_rules`
- `hedge_rules`
- `revision_triggers`
- `shadow_flags`

Supported strategy families include deterministic lanes, grid trading, resistance-band rebound, winner definition, underdog optionality, high-frequency scalping, momentum capture, hedges, bracket exits, and subjective event-specific triggers.

## Runtime Flow

Always-running services:

- NBA/hoopstats watcher updates schedule, live scoreboard, play-by-play, team context, player context, advanced stats, and availability.
- Polymarket watcher captures selected CLOB ticks every 1-3 seconds while events are watched.
- Portfolio watcher reconciles direct CLOB collateral, orders, account-scoped de-duplicated fills, positions, manual interventions, and stale local mirrors. Integrity snapshots must mark mismatched local portfolio mirrors as non-authoritative/quarantined while direct CLOB truth remains the live execution source.
- Replay watcher persists observed latency and tick cadence so future backtests replay what Janus actually saw live.

Pregame:

- Codex pregame research writes dated markdown under `local\shared\reports\daily-live-validation`.
- Janus internal LLM reads lane suggestions, ML sidecar context, deterministic candidates, matchup data, Codex research, and market state.
- Janus persists initial `StrategyPlanJSON` per event under the DB and `local\shared\artifacts\strategy-plans`.

Live:

- Janus revises the active plan when an order fires, manual intervention is detected, a quarter ends, a pregame trigger occurs, a Codex monitor requests review, a player/stat trigger fires, or portfolio truth becomes inconsistent.
- Each revision writes a new plan version and reconciles orders/positions before any new exposure.
- Live monitor and postgame review calls that name reviewed events must report a StrategyPlanJSON gate; missing current plans are blockers, not implicit permission to trade from notes or chat context.
- The trading engine compiles the active plan into triggers and order intents.
- The order manager executes valid intents and immediately creates protective targets, stops, or hedges when required.

Postgame:

- Janus reconciles final CLOB and DB truth.
- Janus internal LLM writes event review into DB.
- Codex postgame review evaluates live PnL, shadow PnL, latency, missed opportunities, plan quality, manual interventions, and next-day development priorities.

## Generic Market Foundation

The DB must support NBA, crypto options, and geopolitics with the same watch/replay model.

Generic tables live in the `agentic` schema:

- `market_events`
- `market_outcomes`
- `market_orderbook_ticks`
- `market_trades`
- `market_watch_sessions`
- `operator_interventions`
- `strategy_plan_versions`
- `strategy_decisions`
- `replay_sessions`

NBA watchlists are generated from the daily slate and Polymarket matching. Crypto/geopolitics watchlists are explicit operator-maintained lists seeded from public Polymarket profiles and event URLs. Passive watching is allowed without trading so Janus can collect real tick latency for future replay.

Implementation status:

- As of 2026-05-10, the NBA live controller mirrors captured live CLOB orderbook ticks into generic `market_watch_sessions` and `market_orderbook_ticks` while preserving the legacy `live_orderbook_ticks.jsonl` trace. Reconciled live-run order fills are also mirrored into `agentic.market_trades` with deterministic upsert ids and `live_controller_order_fills` source metadata. `POST /v1/replay/from-watch-session` resolves persisted watch data into `replay_sessions` with source tick/trade counts, latency/cadence summary, and controller-decision comparison metadata. Public CLOB market-trade polling remains a separate feed integration task.
- Portfolio reconciliation exposes a non-destructive duplicate-fill report at `GET /v1/portfolio/trades/reconciliation` and through `codex_tool/reconcile_trades.py`; destructive historical cleanup must remain a separately reviewed operation.
- Operator-intervention reconciliation stores adoption/rejection metadata in `raw_json` and reports whether the record includes external order/trade references, matched strategy family or manual-only reason, target/stop/hedge status, expected close path, and final PnL. `codex_tool/reconcile_orders.py` exposes the same fields for postgame cleanup.
- Ultra-low underdog buys are guarded in both StrategyPlan evaluation and the legacy live-controller entry path. Underdog buys below `19c` require explicit low-price allowance, fresh scoreboard/score-gap evidence, and target/stop policy; buys below `10c` are manual-only and cannot compile autonomous order intents.

## Backend Interfaces

- `GET /v1/ops/status`
- `POST /v1/ops/data-refresh`
- `POST /v1/ops/integrity-check`
- `POST /v1/ops/pregame-plan`
- `POST /v1/ops/live-monitor`
- `POST /v1/ops/postgame-review`
- `GET /v1/events/{event_id}/agent-context`
- `POST /v1/events/{event_id}/strategy-plan`
- `GET /v1/events/{event_id}/strategy-plan/current`
- `POST /v1/events/{event_id}/strategy-plan/evaluate`
- `POST /v1/events/{event_id}/strategy-plan/execute`
- `POST /v1/watchlists/events`
- `POST /v1/watchlists/sessions`
- `POST /v1/watchlists/orderbook-ticks`
- `POST /v1/watchlists/trades`
- `POST /v1/replay/from-watch-session`
- `POST /v1/operator/interventions/reconcile`
- `GET /v1/portfolio/trades/reconciliation`

## Codex Tooling

Codex agents use scripts under `codex_tool/` to interact with the local API:

- `janus_status.py`
- `run_data_refresh.py`
- `run_integrity_check.py`
- `export_event_context.py`
- `submit_pregame_research.py`
- `submit_strategy_plan.py`
- `run_live_monitor_tick.py`
- `run_postgame_review.py`
- `reconcile_orders.py`
- `watch_market.py`
- `start_watch_session.py`
- `record_orderbook_tick.py`
- `record_market_trade.py`
- `build_replay_from_watch_session.py`
- `evaluate_strategy_plan.py`
- `reconcile_trades.py`

## Codex Agent Schedule

Codex prompt index lives in `app\docs\planning\codex_agent_automation_prompts.md`.

Agent-specific prompt contracts live under `app\docs\planning\codex_agents\`.

Use one pinned chat per agent. DB/docs are source of truth; chat context is useful but not authoritative.

| Agent | Schedule | RRULE |
|---|---:|---|
| JANUS - Post Game System Review | Daily 04:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=4;BYMINUTE=0` |
| JANUS - Development Agent | Every 30 minutes; self-gated by BRT time | `FREQ=MINUTELY;INTERVAL=30` |
| JANUS - Pregame Integrity Check | Daily 13:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=13;BYMINUTE=0` |
| JANUS - Pregame Research & Planning | Daily 14:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=14;BYMINUTE=0` |
| JANUS - Live System Monitor | Every 30 minutes | `FREQ=MINUTELY;INTERVAL=30` |

## Main Branch Gate

Merge to main only after:

- repo-local root resolution is complete;
- runtime `local/` is ignored;
- live safety blockers are fixed;
- strategy-plan schema and ops endpoints are tested;
- Codex tools can call the API;
- dry-run operational cycle passes;
- dirty branches are split into clean commits.
