# Janus Agentic Backend Operating Loop

## Summary

Janus is moving to a backend-first local production service. The internal LLM system has real strategy authority through structured JSON plans that the trading engine validates, monitors, and executes. Codex agents remain external operators for research, health checks, postgame review, and continuous development.

Codex automations, or an equivalent external agent framework, are part of the required operating loop for CI/CD, research, audits, and continuous improvement. They are not part of the critical runtime path for order execution: Janus must continue to ingest data, watch markets, evaluate active plans, reconcile the portfolio, and preserve replay data from the latest valid local state even when Codex is offline.

Live StrategyPlanJSON evaluation is now service-owned. The API exposes a live strategy worker that repeatedly runs the same quote-aware shadow/live strategy tick path that Codex previously had to call manually. Codex can start, stop, inspect, or trigger one worker tick for operator control and debugging, but the worker heartbeat is the proof that Janus itself is watching active plans.

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

Codex Pregame Research is context-only. It can propose game thesis, strategy families, trigger conditions, stop/target/hedge logic, and revision watchpoints, but it does not define order sizing or portfolio exposure. Operator sizing policy is configured by the owner and injected by Janus/live tooling at execution time.

Pregame StrategyPlanJSON is not the live LLM brain. Pregame plans provide initial context, candidate families, and event-specific passive revision watchpoints. The application runtime owns the live LLM revision loop: it detects quarter boundaries, Janus order submissions, fills, cancels, stale orders, target fills, target cancels, failed protective target placements, manual/operator orders/trades/positions, player-status shocks, stale-feed recovery, unexplained CLOB moves, price/favorite flips, scoreboard leadership switches, recent scoring runs, score-gap band breaks, garbage-time state, and ML/PBP valuation triggers, then builds auditable `LLMRevisionRequest` payloads. StrategyPlanJSON `revision_triggers` are additional event-specific hints only; they are not the dispatcher.

The first runtime slices are fail-closed and order-safe. `codex_tool/run_live_strategy_tick.py` emits `LLMRuntimeTrigger`/`LLMRuntimeTrace` evidence, persists prompt/routing/response artifacts under `local\shared\artifacts\llm-runtime\YYYY-MM-DD\`, and can dispatch the routed OpenAI model only behind the explicit `--enable-llm-dispatch` flag. `app\modules\agentic\live_strategy_worker.py` now owns repeated service-side invocation of that same validated tick path and writes heartbeat/tick proof under `local\shared\artifacts\live-strategy-worker\YYYY-MM-DD\`. Missing credentials, unavailable clients, schema failures, and model call errors record `skipped_unavailable`; no StrategyPlanJSON is auto-replaced and no order endpoint authority is granted to the LLM.

Reviewed LLM adoption is a separate, explicit API step. `POST /v1/events/{event_id}/llm-revision/adopt` accepts a recorded `LLMRevisionResponse` or trace artifact only with `reviewed_by` and `review_reason`, validates the embedded `StrategyPlanJSON`, records an adoption artifact with trigger/model/diff metadata, and writes the current plan only when the request explicitly asks to apply it. `codex_tool/adopt_llm_revision.py` is the safe operator/Codex wrapper for this endpoint; it records candidate adoption by default and requires `--apply-current` for promotion. This endpoint and tool never call order endpoints; live execution still flows through StrategyPlan evaluation, direct CLOB checks, operator sizing, and order-manager validation.

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

Active strategies may also carry first-class trader-sleeve metadata:

- `sleeve_id`
- `sleeve_group`
- `sleeve_role`

StrategyPlan evaluation reports sleeve states for every active strategy. Each order intent and blocker preserves sleeve identity so Live Monitor/Postgame can show that one sleeve was blocked, for example by garbage-time no-new-entry or unresolved exposure, while another reviewed sleeve remained eligible.

Operational status surfaces must preserve that evidence. `/v1/ops/live-monitor` reports configured sleeves from the current plan in the StrategyPlan gate, and `codex_tool/run_live_strategy_tick.py` returns evaluated `sleeve_states` plus a `strategy_sleeve_status` summary from the shadow/live evaluation result.

Direct-CLOB intervention protection must preserve the same sleeve identity. When the live tick reactor adopts an uncovered direct position, unknown direct order, or unknown direct trade, emits adverse-position review evidence, recommends/submits a protective target, or writes a position/order/trade-management candidate plan, the audit payload must include the matched strategy sleeve so Postgame can tie target/stop/hedge/reconciliation work back to the responsible trader sleeve.

Supported strategy families include deterministic lanes, grid trading, resistance-band rebound, winner definition, underdog optionality, high-frequency scalping, momentum capture, hedges, bracket exits, and subjective event-specific triggers.

## Operator Sizing Authority

Order sizing is not owned by the planning agent. The active operator policy controls live order size, live exposure, and future portfolio-based resizing.

Current live testing policy:

- limit orders only;
- buy size must satisfy at least `5` shares and at least `$1.00` buy notional;
- live execution uses direct CLOB truth, not the quarantined portfolio mirror;
- StrategyPlanJSON `budget_usd`, `max_positions`, and any entry-rule `size` are advisory or routing metadata whenever an operator sizing policy is present;
- Janus records any LLM-requested size/budget in order metadata for audit, but live order size comes from `operator_sizing_policy`.

Sizing can move from minimum-order testing to portfolio-relative sizing only after multiple profitable days and clean reconciliation prove the system.

## LLM Model Routing

Model-tier routing is tracked in `app\docs\planning\llm_model_routing.md`.

- Use `gpt-5.4-nano` for extraction, play-by-play tagging, tick compression, and repetitive summaries.
- Use `gpt-5.4-mini` for routine pregame synthesis, ordinary StrategyPlanJSON drafting/revision, routine live-monitor analysis, and first-pass postgame classification.
- Use `gpt-5.5` for critical reasoning: high-uncertainty final plan review, live open-position stop/hedge decisions, manual intervention reconciliation, material postgame failures, lane promotion/demotion, and architecture/deep development.

## Runtime Flow

Always-running services:

- NBA/hoopstats watcher updates schedule, live scoreboard, play-by-play, team context, player context, advanced stats, and availability.
- Polymarket watcher captures selected CLOB ticks every 1-3 seconds while events are watched.
- Portfolio watcher reconciles direct CLOB collateral, orders, account-scoped de-duplicated fills, positions, manual interventions, and stale local mirrors. Integrity snapshots must mark mismatched local portfolio mirrors as non-authoritative/quarantined while direct CLOB truth remains the live execution source.
- Replay watcher persists observed latency and tick cadence so future backtests replay what Janus actually saw live.
- Live strategy worker repeatedly evaluates current valid StrategyPlanJSON files, refreshes live scoreboard/orderbook context, records shadow decisions, detects LLM runtime revision triggers, and submits live-money orders only when explicitly configured with `execute=true`, `live_money=true`, an account id, and all safety gates pass.

Pregame:

- Codex pregame research writes dated markdown under `local\shared\reports\daily-live-validation`.
- Janus internal LLM reads lane suggestions, ML sidecar context, deterministic candidates, matchup data, Codex research, and market state.
- Janus persists initial `StrategyPlanJSON` per event under the DB and `local\shared\artifacts\strategy-plans`.

Live:

- Janus revises the active plan when an order fires, a target fills/cancels/fails placement, manual intervention is detected, a quarter ends, a price/favorite flip occurs, scoreboard leadership changes, a recent run, score-gap band break, or garbage-time state is detected, a pregame trigger occurs, a Codex monitor requests review, a player/stat trigger fires, or portfolio truth becomes inconsistent.
- Live LLM revision triggers are application-owned and recorded as `LLMRuntimeTrigger`/`LLMRuntimeTrace` audit objects before and after optional model dispatch. The prompt contract always includes Janus risk profile, event context, deterministic candidates, ML/PBP evidence, direct CLOB truth, portfolio/orders/positions/trades, scoreboard/play-by-play summary, current plan stale reason, operator sizing policy, and a JSON-only output schema. The LLM is never allowed to call order endpoints; it can only return structured revision/reconciliation actions for Janus to validate. Live ticks with revision-required triggers must either record a valid response for review or fail closed with a blocker such as `llm_revision_unavailable`; even a valid response remains `llm_revision_review_required` until a reviewed StrategyPlanJSON replacement exists.
- Each revision writes a new plan version and reconciles orders/positions before any new exposure.
- Live monitor and postgame review calls that name reviewed events must report a StrategyPlanJSON gate; missing current plans are blockers, not implicit permission to trade from notes or chat context.
- The trading engine compiles the active plan into triggers and order intents.
- The order manager executes valid intents and immediately creates protective targets, stops, or hedges when required.
- Codex Live Monitor is no longer the runtime scheduler. It monitors worker health, live blockers, LLM runtime traces, portfolio truth, and handoffs. It may call the worker control endpoints for inspection or one-off debugging, but a healthy live session requires the service-owned worker heartbeat.

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

- As of 2026-05-11, the NBA live controller and `codex_tool/run_live_strategy_tick.py` both mirror captured live CLOB orderbook ticks into generic `market_watch_sessions` and `market_orderbook_ticks` while preserving legacy/orderbook API traces. Reconciled live-run order fills are also mirrored into `agentic.market_trades` with deterministic upsert ids and `live_controller_order_fills` source metadata. `POST /v1/replay/from-watch-session` resolves persisted watch data into `replay_sessions` with source tick/trade counts, latency/cadence summary, and controller-decision comparison metadata. Public CLOB market-trade polling remains a separate feed integration task.
- Portfolio reconciliation exposes a non-destructive duplicate-fill report at `GET /v1/portfolio/trades/reconciliation` and through `codex_tool/reconcile_trades.py`; destructive historical cleanup must remain a separately reviewed operation. Order lifecycle reconciliation now also exposes `GET /v1/portfolio/orders/reconciliation`, joining local orders to linked fills, actor labels, cashflow, and direct-flat unknown classifications without mutating order rows. When `include_direct_clob_evidence=true` is supplied with an account, the endpoint reads direct CLOB open orders/trades and open-position counts to classify orders whose local linked fills are missing and to report direct/effective fill cashflow. Reviewed stale-status repair uses `POST /v1/portfolio/orders/reconciliation/status-backfill`, which is dry-run by default and only updates open local orders to `filled` when full fill evidence covers the requested size; direct-flat orders without fill evidence remain review-required. Reviewed direct-trade persistence uses `POST /v1/portfolio/orders/reconciliation/trade-backfill`, also dry-run by default, to convert matched direct CLOB trade evidence into idempotent `portfolio.trades` rows plus audit order events. Read-only PnL attribution uses `GET /v1/portfolio/orders/reconciliation/pnl-attribution` to bucket known cashflow by actor label, final winning outcome, direct-flat state, and optional direct collateral delta residuals. `POST /v1/ops/postgame-review` now embeds the same actor-level attribution for reviewed events in its recorded artifact when an account id and current StrategyPlanJSON/event ids are available.
- Pending live-entry protection now treats active local `portfolio.orders` buy rows (`pending_submit`, `submitted`, `open`, `working`, `pending`, or partial statuses) as pending intents until direct CLOB or reconciliation clears them. `codex_tool/run_live_strategy_tick.py` injects those pending intents into StrategyPlan evaluation alongside direct CLOB open-order and open-position counts; matching strategies block with `pending_intent_limit_reached`, and unavailable pending-intent state blocks evaluation rather than allowing a duplicate buy.
- Event-start order expiry is now explicit runtime state. If the event start time has passed and a local pending/open order is no longer present in direct CLOB open orders or matched direct fills, the live tick classifies it as `event_start_expired_orders` instead of a pending intent. Planned target orders that disappear at event start are deterministic target-replacement candidates when a direct open position still exists; they do not require frontier LLM routing just to rediscover that Polymarket cleared resting orders.
- `codex_tool/run_live_strategy_tick.py` now parses recent NBA play-by-play for player-status shocks (`ejection`, `flagrant_type_2`, watched-player `technical`, `injury`, `sub_out_star`, `foul_count_threshold`, and feed/status conflicts) and passes structured `player_status_shocks` into StrategyPlan evaluation. Autonomous buys fail closed with `player_status_shock_revision_required` until a fresh StrategyPlanJSON explicitly marks the shock reviewed or allows post-shock entry.
- Operator-intervention reconciliation stores adoption/rejection metadata in `raw_json` and reports whether the record includes external order/trade references, matched strategy family or manual-only reason, target/stop/hedge status, expected close path, and final PnL. Ops integrity/live-monitor snapshots now poll direct CLOB trades for current StrategyPlanJSON token ids and expose them as `direct_clob.current_token_trades`. The live strategy tick direct-position reactor emits no-new-entry revision requests for direct CLOB positions, unknown direct CLOB open orders, and direct CLOB trades/fills that do not map to known local order ids. It scopes direct CLOB event slugs through StrategyPlan event aliases, treats current-plan `live_order_external_id`/`external_order_id` values as known order ids alongside local portfolio rows, and classifies sell-target residuals below exchange minimum size as covered dust rather than new adoption work. It mirrors observed direct trades into `POST /v1/watchlists/trades`, omitting numeric DB columns that exceed market-trade bounds while preserving raw direct CLOB evidence for audit, compiles a schema-valid candidate position/order/trade-management StrategyPlanJSON payload with fresh buys disabled, and can submit that candidate through the existing current-plan API only when the explicit reviewed `--submit-candidate-strategy-plan` flag is set. It also persists the protective reaction with target/stop/hedge metadata after a target submit. `codex_tool/reconcile_orders.py` exposes the same fields for postgame cleanup.
- Price-stability micro-grid buys are a first-class StrategyPlan pattern, not an ultra-low-only exception. The core target rule is `entry + max(1c, 10% of entry price)`, so a `20c` entry targets about `22c`, a `5c` entry targets about `6c`, and lower bands still require at least a one-cent absolute move. These strategies must be bounded by fresh scoreboard/orderbook context, spread limits, score-gap or stability rules, target policy, stop policy, and operator sizing.
- Ultra-low underdog buys are guarded in both StrategyPlan evaluation and the legacy live-controller entry path. Underdog buys below `19c` require explicit low-price allowance, fresh scoreboard/score-gap evidence, and target/stop policy. Underdog buys below `10c` can compile autonomous order intents only when the plan explicitly opts into a protected sub-10c micro-grid lane such as `allow_sub_10c_underdog_grid`; otherwise they remain blocked.
- NBA schedule sync now captures a capped set of recently finished scoreboard games into the same per-game live context path as active games, persisting final score snapshots and play-by-play rows so postgame reviews can tie fills and market moves to score/clock context.
- Live shadow snapshots now include a `live_shadow_comparison_v1` report and `shadow_live_comparison_latest.csv`, comparing live controller actions against shadow-family signals plus ML/LLM sidecar rows by game/side/family with missed-signal bands, blocker buckets, sidecar scores/decisions, live-fill state, and orderbook context for postgame review.

## Backend Interfaces

- `GET /v1/ops/status`
- `POST /v1/ops/data-refresh`
- `POST /v1/ops/integrity-check`
- `POST /v1/ops/pregame-plan`
- `POST /v1/ops/live-monitor`
- `GET /v1/ops/live-strategy-worker/status`
- `POST /v1/ops/live-strategy-worker/start`
- `POST /v1/ops/live-strategy-worker/stop`
- `POST /v1/ops/live-strategy-worker/tick`
- `POST /v1/ops/postgame-review`
- `GET /v1/events/{event_id}/agent-context`
- `POST /v1/events/{event_id}/strategy-plan`
- `GET /v1/events/{event_id}/strategy-plan/current`
- `POST /v1/events/{event_id}/llm-revision/adopt`
- `POST /v1/events/{event_id}/strategy-plan/evaluate`
- `POST /v1/events/{event_id}/strategy-plan/execute`
- `POST /v1/watchlists/events`
- `POST /v1/watchlists/sessions`
- `POST /v1/watchlists/orderbook-ticks`
- `POST /v1/watchlists/trades`
- `POST /v1/replay/from-watch-session`
- `POST /v1/operator/interventions/reconcile`
- `GET /v1/portfolio/trades/reconciliation`
- `GET /v1/portfolio/orders/reconciliation`
- `GET /v1/portfolio/orders/reconciliation/pnl-attribution`
- `POST /v1/portfolio/orders/reconciliation/status-backfill`
- `POST /v1/portfolio/orders/reconciliation/trade-backfill`

## Codex Tooling

Codex agents use scripts under `codex_tool/` to interact with the local API:

- `janus_status.py`
- `run_data_refresh.py`
- `run_integrity_check.py`
- `export_event_context.py`
- `submit_pregame_research.py`
- `submit_strategy_plan.py`
- `run_live_monitor_tick.py`
- `live_strategy_worker_status.py`
- `start_live_strategy_worker.py`
- `stop_live_strategy_worker.py`
- `run_live_strategy_worker_tick.py`
- `run_postgame_review.py`
- `reconcile_orders.py`
- `watch_market.py`
- `start_watch_session.py`
- `record_orderbook_tick.py`
- `record_market_trade.py`
- `build_replay_from_watch_session.py`
- `evaluate_strategy_plan.py`
- `adopt_llm_revision.py`
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
