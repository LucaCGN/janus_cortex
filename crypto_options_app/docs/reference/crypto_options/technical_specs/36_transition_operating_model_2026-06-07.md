# Crypto Options Transition Operating Model - 2026-06-07

## Summary

The crypto-options app is transitioning from ad hoc strategy-chasing into a modular operating system for research, replay, shadow testing, automated policy-gated live promotion, and demotion.

The target flow is:

`Sources -> Indicators -> Signals -> Signal backtests -> Strategy backtests -> Shadow/live-replay -> Live -> Scale/demote`

Current readiness is not sufficient for broad ungated live-promotion automation.
The app can support bounded queue cleanup, source monitoring, and frontend
reporting while the master chat finishes storage, promotion, and
repo-organization gates.

## Current Controller Model

The master chat owns:

- runtime and storage decisions
- live-promotion policy
- safety gates
- end-to-end promotion/demotion integrity
- coordination across fixed chats and automations

The first two fixed chats are:

- Signal And Strategy Management Cleanup
- Frontend Control Center Developer

Both consume and write coordination state under:

`crypto_options_app/artifacts/team_coordination`

## Storage Decision Gate

Postgres remains durable source of truth. As of 2026-06-08, active runtime has
moved to remote Postgres at `192.168.0.156:5432/janus-postgres`; the local
Docker Postgres service on `127.0.0.1:55433` remains a rollback source until
remote runtime stability is reconfirmed after the next strategy/reconciliation
validation pass.

Redis is a gated hot-plane candidate, not a replacement for Postgres. It may be introduced only for latest-state cache, frontend cache, replay candidate cache, TTL queue locks, pub/sub, rate limits, or short-lived coordination after the storage audit shows pressure that belongs outside durable historical storage.

Redis must never be the only store for trading-critical truth.

The audit command is:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_storage_architecture_audit --write-artifacts --json
```

## Runtime DB Rules

- Runtime API, dashboard, strategy manager, signal manager, replay, and data services must use the common DB adapter.
- SQLite remains allowed only for migration, import, test, or compatibility paths until the large compatibility file can be safely archived.
- Runtime audits must fail production paths that call `sqlite3.connect()` directly.
- Replay/scout queries must stay bounded and resource-aware.

## Promotion And Demotion Rules

Signal `PASSED`, `SELECTED`, or `STRUCTURAL_ALTERNATE` states are not live-safe by themselves.

Strategy live promotion uses a default conservative baseline:

- 12+ recent distinct economic samples
- win rate above 70%
- positive simulated PnL
- lifecycle coverage
- reconciliation passed
- no strict signal blockers
- shadow/live-replay drift within tolerance

Strategy specs may define their own promotion, scaling, demotion, and review
criteria through `metadata.promotion_policy`, `metadata.promotion_criteria`,
`risk_gates.promotion_policy`, or `live_pulse_requirements.promotion_policy`.
Those maps are the backward-compatible criteria surface until a dedicated
`StrategySpec.promotion_criteria` schema field is added.

Lower win-rate strategies are allowed only when their own criteria define
stronger positive-PnL floors, aggressive live loss limits, lifecycle
coverage, reconciliation, and drift gates. The system evaluates the criteria;
the chat does not manually decide promotion.

Live loss, reconciliation mismatch, missing lifecycle, large drift, negative PnL
breach, or win-rate breach demotes or blocks according to the active strategy
policy.

## Strategy Tracks

### Simple Candidate Track

Continue accumulating `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality` evidence. It must not promote until it reaches the minimum distinct-sample gate and passes promotion manager reconciliation.

### Master Hedge Grid Track

Continue `master_hedge_grid_scalping` as volatility harvesting:

- seed both sides
- scalp inversions/rebounds
- lock a protected hedge floor
- allow only floor-preserving orders
- spend only surplus on 1c/5c/10c tail optionality

Replay only when closed-cycle protected-floor candidates exist. Do not weaken floor gates to force execution.

## Repo Organization

Do not delete old work.

Planned reference roots:

- `wnba_nba_app_reference`
- `global_app_reference`

Crypto active roots:

- `crypto_options_app`
- `tests/crypto_options_app`
- crypto-specific scripts, specs, artifacts, issues, and workflows

Move legacy work only after an inventory report.

## Automation Gate

Keep the single heartbeat/master automation active.

Do not add broad standing automations until:

- coordination artifacts exist
- storage audit completes
- promotion/demotion tests are in place
- queue ownership is verified
- fixed chats are created or explicitly deferred

Allowed next automations after gates:

- DB/data observability
- signal-strategy queue worker
- frontend status reporter

No automation may bypass promotion-manager, executor, budget/risk, lifecycle,
reconciliation, stop, or demotion gates. App-level live/order flags may be
enabled for policy-gated automated testing, but Codex manual orders remain
prohibited.

## Strategy Pipeline Queue Worker

The transition uses durable queue ownership for strategy phase progression.
There should be no scheduler that periodically discovers what should exist.

The app creates queue items at the moment a strategy is registered or a
promotion state advances:

- registered strategy/version -> `historical_backtest`
- `BACKTEST_READY` -> `shadow_replay`
- `SHADOW_READY` -> repeatable `recent_shadow_sample`
- `LIVE_CANDIDATE` -> `live_candidate`

The bounded worker drains queued work:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_strategy_pipeline_queue_worker --no-refresh-promotions --max-items 4 --max-scenarios-per-action 1 --max-trades-per-strategy 1 --recent-shadow-retry-seconds 300
```

Routine automation may run the queue worker every 5 minutes while the system is
actively gathering recent evidence. That cadence only drains durable queued
work; it does not own strategy discovery. Each run claims bounded queue items,
runs bounded phases with Postgres CPU/RAM guards, writes markdown/JSON status,
and exits.

Repeatable `recent_shadow_sample` items stay queued until the promotion manager
moves the strategy to `LIVE_CANDIDATE` or review/block. The `live_candidate`
path is app-owned and gate-owned through verified candidates, preflight,
executor boundary, budget ledger, lifecycle, reconciliation, stops, and
demotion policy. It does not require Codex/user approval once strategy-defined
criteria pass. Codex manual orders remain prohibited.

## Current Live Runtime Recovery Gate

The 2026-06-08 live-loss incident changed the next live-validation gate:

- Do not widen live lanes until one fresh controlled candidate proves BUY fill,
  executable paired SELL coverage, exchange-status reconciliation, and
  strategy-owned loss demotion under the final runtime patches.
- Use remote Postgres as the runtime DB for the next validation cycle.
- Keep local Docker Postgres available as rollback only.
- Continue strategy/signal creation and replay work from GitHub issues and
  team-coordination handoff docs, but treat replay-engine and trading-engine
  defects as master-lane technical handoffs.

Spark signal/strategy cleanup and strategy creation should run at hourly cadence
until the queue worker proves that reviewed/created rows reliably progress
through backtest and shadow. They should continue to hand off replay, promotion,
lifecycle, reconciliation, DB, or frontend defects instead of patching those
subsystems themselves.

## Control Surface Runtime Semantics

- The root control surface must prefer current Postgres-backed state over expired dashboard cache. Expired control-state cache is allowed only as a fallback after a DB read error.
- Runtime badges distinguish app capability from manual order authority. `orders_allowed=true` and `live_trading_authorized=true` mean policy-gated live testing can run when promotion/runtime gates pass; they do not authorize Codex manual orders or gate bypasses.
- Portfolio top-line metrics show current live exposure only. If the latest live-dashboard artifact is stale, historical realized PnL/open cost must be preserved as excluded evidence instead of shown as current open cost.
- Strategy runtime rows from stale stopped live tests should render as historical evidence, not active live lanes.
- Event and profile panels should prefer usable display rows for operator trust: completed event paths with price bucket points for charts, and robust profile-distribution snapshots over thin newest snapshots when both are available.
- Historical closed-event unresolved rows remain a reconciliation issue, not a frontend truth source. Track reconciliation cleanup under issue #168 and the technical handoff log.
