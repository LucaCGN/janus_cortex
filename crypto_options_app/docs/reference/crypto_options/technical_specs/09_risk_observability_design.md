# Crypto Options Risk And Observability Design

Date: 2026-06-02

Status: technical spec for risk gates, stop gates, monitoring, and reports.

## Purpose

Risk and observability are shared engine mechanics. They are not optional strategy suggestions.

## Risk Gates

Every strategy declares:

- budget
- max active cost
- max event exposure
- max same-side exposure
- max correlated exposure
- max spread
- max quote age
- max profile data age
- max signal data age
- max slippage
- min liquidity/depth
- final-minute entry behavior

The engine blocks execution when a gate fails.

## Stop Gates

Every live/dev run supports:

- trade cap
- hard loss stop
- early failure stop
- mid failure stop
- severe failure stop
- profit giveback stop
- quality floor stop
- mechanical failure stop
- stale service stop
- no-valid-candidate report gate

Loss tolerance decreases when win rate is poor.

## Mechanical Failures

Immediate mechanical failures:

- malformed packet
- ready packet without current eligible source
- missing event/token identity
- stale executable quote
- candidate ledger not updating
- remote/local ledger mismatch
- missing lifecycle coverage
- duplicate cashout exit
- duplicate same-side exposure where forbidden
- correlated cap violation
- executor traceback
- credentials/access failure
- service process crash
- stale monitor artifact beyond threshold

## Observability Snapshot

Every tick/report includes:

- UTC timestamp
- run id and run root
- service PID/tree/alive/staleness
- tick index
- latest feed watermarks
- ready/blocked/executed counts
- blockers by strategy
- submitted/settled/open counts
- PnL
- active cost
- profile sources used
- event ids/tokens used
- quote age
- signal age
- lifecycle coverage status
- stderr/error summary
- manual orders avoided confirmation

## Health Endpoint

The monitor automation uses `GET /v1/crypto-options-app/health` as the single read-only integrity endpoint. It must report:

- canonical DB path, schema completeness, lifecycle table counts, and feed watermarks
- registered strategies and families
- latest live-validation artifacts and per-strategy status
- missing exchange-audit fields such as token id, event slug, outcome, exchange order id, fill price/size, and reconciliation evidence
- readiness blockers before longer live tests
- `orders_allowed: false`
- `live_trading_authorized: false`
- manual-order avoidance confirmation

`GET /v1/crypto-options-app/health/ping` is process liveness only and is not sufficient to start longer tests.

If the health endpoint finds live validation artifacts without canonical DB lifecycle rows or without exchange-audit identifiers, longer strategy comparison runs remain blocked.

## External Exchange Status Gate

`GET /v1/crypto-options-app/health` must include `external_services.polymarket` from the public Polymarket status API. The report must expose page status, CLOB API component status, active incidents, active maintenance windows, blocking CLOB-specific incidents/maintenances, nonblocking warnings, and whether CLOB trading is currently available.

Live validation and comparison runs must treat CLOB API maintenance, CLOB/order-submission/exchange incidents, or an unreachable status provider as stop/pause conditions. Page-level or account/login/email incidents are surfaced as warnings when the CLOB API component itself is operational, but they do not make CLOB trading unavailable. These conditions are not strategy failures and must not be reported as PnL or signal-quality evidence. The supervised executor must check the exchange-status gate before submitting an order and block before CLOB submission when the status is not tradable.

Known status blockers:

- `polymarket_clob_trading_unavailable`
- `polymarket_status:exchange_status_not_operational`
- `polymarket_status:polymarket_active_maintenance`
- `polymarket_status:polymarket_active_incident` for CLOB/order-submission/exchange incidents only
- `polymarket_status:polymarket_status_unavailable`

When a pre-submit exchange-status block occurs, candidate rows must record the signal context and blocker, avoid creating a position, avoid lifecycle coverage claims, and avoid labeling the row as a reconciliation mismatch unless exchange evidence indicates a possible fill.

## Safety Invariants

- One live cadence per active run.
- No manual order placement from chat.
- Data feeds do not authorize orders.
- Strategy manager emits specs and readiness only.
- Trading engine emits intents and state.
- Executor boundary owns order submission through supervised gates.

## Acceptance Criteria

- Risk gates are tested before live pulse.
- Stop gates can pause a strategy or stop the run.
- Every report can explain why a candidate was executed or blocked.
- Observability can detect stale data, stale service, duplicate cadence, and missing coverage.

## P2 Centralization Update

Health and dashboard reports must expose centralized DB/artifact/profile-pool/docs paths, data-service watermarks, and row counts for option price ticks, order books, normalized depth, pair snapshots, and indicators. Legacy paths are compatibility or historical evidence only.

## P2 Profile Distribution Observability

Health must expose latest Block B readiness rows and recent `profile_distribution_snapshots`. Strategy runs that depend on profile distribution must block when:

- `top_profiles_distribution` readiness is `missing` or `degraded` for the target symbol/event.
- source age exceeds the strategy max profile data age.
- the distribution has zero components or zero Up/Down weight.
- the snapshot source mode is weaker than the strategy allows.

These are data readiness blockers, not strategy performance failures.
