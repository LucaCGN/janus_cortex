# Janus Current Architecture And Degradation Map

Status: current control map
Created: 2026-05-18
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/40

## Purpose

Map the current Janus architecture as it exists now, not as a future rewrite target.

This document gives the master controller and follow-on agents a concise way to answer:

1. What components exist today?
2. Which surfaces own which responsibility?
3. What can continue when a service, feed, LLM lane, CLOB path, or mirror is degraded?
4. Which legacy NBA controller pieces should be kept, wrapped, migrated, or retired?

This is a source-of-truth map, not live trading authority. Direct CLOB truth, Janus DB/API, runtime artifacts, runtime handoffs, and event-specific integrity gates still outrank it.

## Current Shape

Janus is currently a single-repo FastAPI modular monolith with repo-local runtime state.

| Layer | Current Surface | Role |
|---|---|---|
| API process | `app/api/main.py` | Starts the FastAPI app, installs routers, and owns process lifespan for the app-owned live strategy worker. |
| API routers | `app/api/routers/*` | Expose catalog, market data, NBA read/live, ops, portfolio, sync, and system-registry endpoints. |
| DB | `app/data/databases/*`, migrations `0001` through `0024` | Stores catalog, market data, portfolio, NBA/WNBA data, and agentic runtime state. |
| Repo-local runtime root | `app/runtime/local_paths.py` | Resolves `local`, `local/shared/artifacts`, `local/shared/handoffs`, `local/shared/reports`, and `local/tracks/live-controller`. |
| Controller queue | `app/runtime/controller_queue.py`, `tools/controller_queue.py` | Owns active locks, stale/duplicate/dirty-worktree checks, completed locks, and pass ledger. |
| Codex/Janus wrappers | `codex_tool/*` today; target `codex_tools/janus/*` | Thin CLI/API wrappers used by Codex automations for Janus status, data refresh, integrity, live monitor, strategy plan, review, reconciliation, and worker control. Existing `codex_tool` imports are compatibility entrypoints until `#53` migrates them. |
| Codex/Polymarket fallback tools | `codex_tools/polymarket/*` | Direct Polymarket account/CLOB/orderbook/portfolio-manager planning tools, including read-only account snapshots, fallback gates, required action plans, grid candidate previews, settlement previews, and gated grid-service spawn plans. Non-dry-run orders still require `#54/#56/#59` gates and runtime approval. |
| Operator tools | `tools/*` | Bounded scripts for startup reconciliation, operational cycles, WNBA checks, replay, profile reports, microstructure analysis, and controller queue operations. |
| Obsidian | `C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain` | Curated memory, rationale, indexes, and strategy knowledge; never live runtime truth. |
| GitHub issues | `LucaCGN/janus_cortex` | Durable backlog identity, acceptance criteria, and operator-visible work state. |

The default runtime root is repo-local when `JANUS_LOCAL_ROOT` is unset:

`C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`

## Component Map

| Component | Primary Files | Current Responsibility | Live Authority |
|---|---|---|---|
| FastAPI app | `app/api/main.py` | Mount routers and start/stop the app-owned live strategy worker if explicitly enabled by environment. | Process host only; does not grant live readiness by itself. |
| Ops router | `app/api/routers/ops.py` | Status, data refresh, integrity check, pregame plan, live monitor, postgame review, StrategyPlanJSON current/evaluate/execute, manual assistant, LLM revision adoption, watch sessions, and replay requests. | Routes execution only through validators and order adapter paths. |
| Sync router | `app/api/routers/sync.py` | NBA schedule/live/PBP refresh, Polymarket event/market/price/orderbook probes, mappings, portfolio mirror sync, and closed-position consolidation. | Read/write data only; not order authority. |
| Portfolio router | `app/api/routers/portfolio.py` | Trading account records, portfolio summaries, positions/orders/trades, order lifecycle reconciliation, direct trade backfill, and manual order create/cancel endpoints. | Order endpoints are live-impacting and require explicit request paths plus guards. |
| NBA live router | `app/api/routers/nba_live.py` | Legacy/live-run service endpoints for NBA live runs, games, orders, shadow capture, pause/resume, and stop. | Wrapped live-run lane; not the controller's primary StrategyPlanJSON gate. |
| Agentic contracts | `app/modules/agentic/contracts.py` | Pydantic contracts for StrategyPlanJSON, ops cycle payloads, manual assistant, LLM revision, watch sessions, replay, and worker controls. | Contract layer only. |
| StrategyPlan evaluator | `app/modules/agentic/engine.py` | Converts current StrategyPlanJSON plus market/portfolio state into order intents or blockers. Enforces shadow-only, market-order-disabled, size/notional, stale/expired, low-underdog, and budget gates. | Produces intents; execution still requires explicit execute path and order adapter. |
| Integrity checks | `app/modules/agentic/ops_checks.py` | Builds DB/account/direct-CLOB/portfolio-mirror readiness snapshots and quarantines stale mirrors. | Direct CLOB readiness can permit minimum-order readiness; mirror mismatch is non-authoritative. |
| LLM runtime | `app/modules/agentic/llm_runtime.py` | Trigger detection, model routing baseline, budget/dedup/final-flat controls, prompt contract, runtime trace artifacts, and internal LLM revision responses. | Never calls order endpoints; output must be adopted and validated. |
| App-owned live worker | `app/modules/agentic/live_strategy_worker.py` | Optional background loop that discovers current plans, runs live strategy ticks, persists heartbeat artifacts, and only acts when enabled with explicit flags. | Defaults stopped/non-executing; live money requires explicit flags and current plans. |
| Manual CLOB assistant | `app/modules/agentic/manual_order_assistant.py` | Deterministic review for manual/Codex order intent: event/outcome match, order type, price/notional, book freshness, spread, depth, and inventory. | Preview by default; execute only through audited portfolio/order adapter path when approved. |
| Order adapter | `app/modules/nba/execution/adapter.py` | Resolves Polymarket account, CLOB collateral, orderbook, direct trades, and create/cancel order operations. | Direct order path; must be gated by callers. |
| Legacy live-run service | `app/modules/nba/execution/*`, `app/api/routers/nba_live.py` | Live-run worker/tracks, controller baseline/fallback names, shadow capture, and historical live-run artifacts. | Wrap for compatibility and shadow evidence; migrate useful pieces into StrategyPlanJSON and ops contracts. |
| NBA data pipelines | `app/data/pipelines/daily/nba/*`, `app/providers/nba/*` | Schedule, live snapshots, play-by-play, state panels, mart features, analysis reports, replay/backtest lanes, and ML sidecars. | Feed/evidence layer; not execution authority. |
| WNBA data pipelines | `app/data/pipelines/daily/wnba/*`, `tools/run_wnba_*` | Current-season sync, price-history probe, readiness checks, passive capture, state panels, ML dataset/model path. | Shadow/min-size-test candidate only; no inherited NBA live authority. |
| Polymarket providers | `app/providers/polymarket/*`, `app/data/nodes/polymarket/*` | Gamma catalog/event/market/price data, CLOB orderbooks, direct account positions/orders/trades, portfolio mirror sync. | Direct CLOB account/order truth outranks Gamma and portfolio mirror. |
| Startup reconciliation | `tools/run_janus_startup_reconciliation.py`, `tools/run_janus_operational_cycle.py` | Rebuild schedule, probes, mappings, and portfolio mirrors after API startup without starting workers or placing orders. | Read/write data only; no live-order impact. |
| Controller automation | `app/docs/planning/current/final_system/automation/*`, `tools/controller_queue.py` | Axis-first routing, personas, queue locks, issue discipline, no-op compression, sub-agent rules. | Codex orchestration only; cannot place orders. |

## Runtime State At This Map

Observed during the 2026-05-18 `#40` controller pass:

| Surface | State |
|---|---|
| Branch | `main` synced with `origin/main` at `e58855a`. |
| Worktree | Clean before the `#40` claim. |
| Controller queue | `active_lock_count=0`, `stale_lock_count=0` before the `#40` claim. |
| Janus status | `python codex_tool/janus_status.py` returned `ok=true`; agentic tables present. |
| StrategyPlanJSON | `current_plan_count_today=0`. |
| Live/readiness | RED; event-specific StrategyPlanJSON, direct CLOB, worker, feed freshness, cost, and integrity gates are not globally green. |
| Live worker | Not started by this pass. |
| Live-order impact | None. No orders placed, cancelled, replaced, submitted, or prepared. |

## Dependency And Degradation Map

| Dependency | Independent Work Still Allowed | Degraded Work | Blocked Or Fail-Closed Work |
|---|---|---|---|
| Direct CLOB read access unavailable | Repo docs, issue triage, Obsidian, offline tests, historical replay. | Portfolio mirror and DB-only review may continue with explicit caveat. | Live execution, live readiness GREEN, current-event inventory proof, settlement claims. |
| Direct CLOB write/order credentials unavailable | Read-only integrity, pregame planning, shadow replay, Codex fallback drafting. | Manual assistant can preview only. | Order creation/cancel/replacement, live worker execution, urgent profit capture. |
| Janus DB unavailable | Repo docs, GitHub/Obsidian, pure unit tests, code review. | Some provider/API probes may run outside DB only if explicitly designed. | API-up validation, sync persistence, StrategyPlanJSON persistence, integrity, live monitor, worker execution. |
| FastAPI down | Repo docs, direct CLI/provider inspection, code/tests. Future `codex_tools/polymarket/*` may inspect direct CLOB/account truth if implemented. | Startup reconciliation can report connection failure. Direct Polymarket fallback may produce management plans. | API route validation, live monitor, worker HTTP tick path, pregame/postgame endpoint proofs. Any direct Polymarket order action remains blocked unless the independent execution gate in `automation/codex_tooling_contract.md` is implemented and approved. |
| NBA schedule/PBP feed stale | Non-NBA work, issue-backed code/docs, global portfolio read-only scan. | Pregame planning may draft only with stale-feed caveat. | NBA live readiness, NBA live strategy execution, postgame review completeness claims. |
| WNBA feed/history incomplete | NBA work, generic docs, WNBA source audit. | WNBA shadow/backtest can continue with blocker notes. | WNBA min-size-test promotion and WNBA live authority. |
| Polymarket Gamma/catalog probes fail | Existing mapped-event review may continue if DB/direct CLOB truth is enough. | Startup reconciliation reports blocker categories. | New market/outcome/token discovery, fresh DB mapping, new StrategyPlanJSON for unmapped markets. |
| Portfolio mirror stale or mismatched | Direct CLOB can still govern live safety if direct truth is clean. | Ledger/review can continue with mirror quarantine noted. | Treating mirror as live authority, performance claims from mirror alone. |
| Internal LLM unavailable or budget-blocked | Deterministic/ML lanes, existing StrategyPlanJSON evaluation, Codex fallback drafting. | Janus should expose Codex-required state and reason codes; see `#41`. | Raw LLM order execution, unreviewed LLM plan adoption, frontier default use. |
| Codex unavailable | App-owned deterministic/runtime lanes can run if all Janus gates pass. | Repo/docs/issue/Obsidian automation stops until Codex returns. | Codex fallback strategy drafting and controller development work. |
| StrategyPlanJSON missing | Data refresh, integrity, planning, issue-backed development, global portfolio management/scouting outside Janus-controlled live events. | Live monitor reports strategy-plan gate blockers. | Live strategy execution and live readiness GREEN. |
| Live worker stopped | Data refresh, integrity, planning, postgame, development. | Live monitor can report worker blocker. | Janus-controlled live execution. |
| Controller queue unavailable | Read-only inspection and no-op summaries. | Manual review of locks can happen with caveat. | Any write scope that needs code/docs/handoff/GitHub/Obsidian mutation. |
| GitHub unavailable | Local code/docs/tests can proceed only if issue state is already known and a lock exists. | Commit can be local briefly but not complete. | Treating work as done, closing issues, starting ambiguous new issue-backed scope. |
| Obsidian unavailable | Repo/runtime/GitHub work can continue. | Docs-memory pass records vault unavailable. | Curated memory update acceptance criteria for docs-memory issues. |

## Lane State Matrix

| Lane | Independent When Healthy | Degraded Mode | Fail-Closed Condition |
|---|---|---|---|
| Data refresh | FastAPI and DB up; provider feeds reachable. | Record blocker summaries for failed provider calls. | Do not infer missing market mappings from screenshots. |
| Pregame integrity | DB/API, direct CLOB, mapped events/tokens, current account, and feed freshness available. | Produce blockers only. | Any unclear live-money state or missing direct CLOB truth. |
| Pregame planning | Integrity is current enough and watched events are mapped. | Draft watchpoints and candidate StrategyPlanJSON with caveats. | Planning cannot place orders or bypass StrategyPlan validators. |
| Live monitor | Active event, current plan, direct CLOB truth, worker/readiness evidence. | Read-only safety inspection and issue creation. | Broad development during active Janus-controlled live event. |
| Live execution | Current StrategyPlanJSON, direct CLOB, worker, feed, costs, validators, and explicit flags pass. | Shadow/live-monitor only. | Missing current plan, stale feed, direct CLOB failure, worker stopped, or budget/cost block. |
| Postgame review | Closed event data, direct/account truth, DB/API, review bundle. | Review with explicit unresolved rows and mapping blockers. | Profitability or settlement claims without direct CLOB/account evidence. |
| Development | No live/pregame/postgame safety preemption; issue-backed claim succeeds. | Read-only review or blocker ledger. | Dirty worktree or duplicate/stale lock outside owned scope. |
| Global portfolio | Direct/account/API truth available, active-manager contract current, frontend/profile catalog can be inspected, and action planner/grid tools can produce bounded candidates. | Select a required action candidate and record exact execution blocker. | Any execution, order preparation, service leg, or risk-budget merge with Janus sports testing without gates. |
| WNBA readiness | Shared contracts, WNBA data, passive capture/replay, calibration evidence. | Shadow-only dry run with blocker report. | Live WNBA orders without core safety gates and operator approval. |

## Legacy NBA Controller Classification

| Surface | Classification | Reason |
|---|---|---|
| `app/modules/agentic/engine.py` StrategyPlanJSON evaluator | Keep | Central validator/gate for app-owned strategy intents. |
| `app/modules/agentic/contracts.py` StrategyPlan/ops contracts | Keep | Defines the canonical runtime contract used by API, Codex wrappers, and tests. |
| `app/api/routers/ops.py` integrity/live-monitor/current-plan/review endpoints | Keep | Current orchestration API for pregame/live/postgame/controller work. |
| `app/modules/agentic/ops_checks.py` direct CLOB integrity snapshot | Keep | Correct authority boundary: direct CLOB over mirror. |
| `app/modules/agentic/manual_order_assistant.py` | Keep | Needed audited manual/Codex path; defaults preview and validates guardrails. |
| `app/modules/agentic/llm_runtime.py` | Keep and harden under `#41` | Implements trigger/budget/dedup/final-flat foundation; budget-aware routing still needs validation. |
| `app/modules/agentic/live_strategy_worker.py` | Keep with strict gates | App-owned worker path, disabled by default and useful after current-plan/integrity gates pass. |
| `codex_tool/*` wrappers | Wrap | Useful operator/Codex interface. Preserve as compatibility entrypoints while migrating Janus-facing wrappers to `codex_tools/janus/*` under `#53`. |
| `codex_tools/polymarket/*` direct fallback tools | Harden under `#56/#59` after `#53` foundation | Needed for portfolio-manager and live-monitor break cases where Janus API/runtime is unavailable or the portfolio-manager contract selects the direct path. Current surface supports read-only snapshots, fallback gates, required action planning, grid preview/spawn proof, settlement preview, and ledgers. Non-dry-run orders require explicit gates, runtime approval, and reconciliation. |
| `tools/run_janus_startup_reconciliation.py` and `tools/run_janus_operational_cycle.py` | Wrap | Useful deterministic restart/data-refresh proof; not a live controller. |
| `/v1/nba/live/runs` and `app/modules/nba/execution/*` live-run service | Wrap then migrate | Keeps historical live-run/shadow artifacts and pause/resume mechanics; primary strategy authority should move through StrategyPlanJSON, ops live-monitor, and event review contracts. |
| `controller_vnext_*` backtest scripts and replay subjects | Migrate selectively | Convert validated controller lessons into StrategyPlan templates, deterministic lanes, or ML/replay features; do not swap live baseline by script label alone. |
| `app/data/pipelines/daily/nba/analysis/backtests/*` family experiments | Migrate selectively | Useful research/backtest library; promotion needs fillability, CLOB depth, and review evidence. |
| Legacy external local-root references | Retire | Current runtime state belongs under repo-local `local`; old paths are historical only. |
| Screenshot/chat-driven postgame reconstruction | Retire as authority | May provide hypotheses, but API/direct CLOB/artifacts must be the evidence. |
| `tools/polymarket_smoke_order.py` | Retire from automation use | Direct smoke order tooling is live-impacting and should not be used by controller automation. |

## Known Gaps And Existing Follow-Ups

No new GitHub issue is required from this map. The material implementation gaps already have durable issue coverage:

| Gap | Existing Issue |
|---|---|
| Budget-aware model routing, OpenAI balance/bankroll-aware escalation, Codex-required state proof | `#41` |
| API-up validation of closed seed foundations across the running service | `#33` |
| WNBA dry run and minimal-readiness blocker proof | `#34` |
| Polymarket minimum order constraints and market-order exception policy | `#42` |
| Analytical chart-equivalent microstructure metrics for review/live context | `#43` |
| Profit-ratcheted risk ladder calibration from mapped account/DB histories | `#44` |
| Global portfolio target/rebuy ledger and watchlist schema | `#45` |
| Codex tooling split into Janus wrappers and independent Polymarket execution fallback | `#53` |

## Controller Use

The master controller should use this map after the higher-authority runtime and issue state checks.

Recommended routing effect:

1. If live/current-event safety is unclear, ignore this doc and inspect direct CLOB, DB/API, worker, plans, and handoffs first.
2. If no live safety task is active and the queue is clean, this map makes `#41` the next P0 activation task after `#40`.
3. If a component is degraded, use the dependency table to decide whether the pass can still perform read-only inspection, docs/issue work, shadow/replay work, or no-op.
4. If an agent proposes a broad rewrite, require concrete evidence that the modular monolith cannot meet the need before introducing Redis, a new service, or a new app boundary.
