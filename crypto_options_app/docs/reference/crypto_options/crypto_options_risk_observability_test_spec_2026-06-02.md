# Crypto Options Risk, Observability, And Test Spec

Date: 2026-06-02

Status: reviewed sub-spec for gates, monitoring, and tests.

## 1. Purpose

This spec defines the shared gates and observability requirements every strategy must inherit.

Risk gates are engine mechanics, not strategy suggestions.

## 2. Risk Gates

Every strategy must declare:

- strategy budget
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

The engine must block execution when a risk gate fails.

## 3. Stop Gates

Every live/dev run must support:

- trade cap
- hard loss stop
- early failure stop
- mid failure stop
- severe failure stop
- profit giveback stop
- quality floor stop
- mechanical failure stop
- stale service stop
- no-valid-candidate warning/report gate

Loss tolerance should decrease when win rate is poor.

## 4. Mechanical Failure Gates

Immediate mechanical failures:

- malformed packet
- ready packet without current eligible source
- missing event/token identity
- stale executable quote
- candidate ledger not updating after execution
- remote/local ledger mismatch
- missing lifecycle coverage
- duplicate cashout exit
- duplicate same-side exposure where forbidden
- correlated cap violation
- executor traceback
- credentials/access failure
- service process crash
- stale monitor artifact beyond restart threshold

Mechanical failures pause or stop the affected strategy. Shared infrastructure failures stop the whole run.

## 5. Observability Fields

Every tick/report should include:

- UTC timestamp
- run root
- service PID/tree/alive/staleness
- tick index
- latest monitor artifact timestamp/source
- ready/blocked/executed counts
- current blockers by strategy
- submitted/settled/open counts
- PnL
- active cost
- profile sources used
- event ids/tokens used
- quote age
- signal age
- lifecycle coverage status
- stderr summary
- dashboard warning summary
- confirmation that manual orders were avoided

## 6. Reporting Attribution

Reports must attribute entries and exits by:

- strategy id
- strategy version
- profile source
- profile grade
- profile type
- signal generator
- event signal component
- indicator signal component
- event phase
- time remaining
- target delta
- quote state
- entry order type
- exit order type
- cashout type
- hedge state
- realized outcome

## 7. Replay And Live Pulse Requirements

Replay must verify:

- contemporaneous quote availability
- fillability
- unfilled order behavior
- partial fill behavior
- exit/cashout behavior
- settlement accounting when labels exist
- blocker correctness
- drawdown and win-rate gates

Live pulse tests are integrity checks only.

Pulse acceptance requires:

- exactly one service cadence
- no manual orders
- ledgers update
- order reconciliation works
- lifecycle coverage exists
- no duplicate exits
- no unexpected traceback
- clean first-minute monitor after restart

## 8. Unit/Module Test Matrix

Required test groups:

| Group | Required coverage |
| --- | --- |
| Strategy spec validation | required fields, unsupported mechanics, disabled strategy behavior |
| Signal contracts | profile type routing, grade filters, stale signals, indicator timing |
| Candidate pipeline | candidate dedupe, blocker propagation, event identity linking |
| Execution intents | market/limit BUY and SELL intent creation |
| Order lifecycle | submitted, rejected, partial, filled, unfilled, cancelled, expired |
| Reconciliation | local/remote mismatch, ledger updates, dust tolerance |
| Lifecycle coverage | cashout, split exit, hedge-managed, hold-to-settlement |
| Risk gates | budget, exposure, spread, stale quote, no profiles |
| Stop gates | loss, win rate, giveback, trade cap, quality floor |
| Observability | tick status, blocker counts, report attribution |
| Restart | run root preservation, open order reload, no duplicate cadence |

## 9. Development Rule

Implementation order:

1. Build/validate strategy spec parser.
2. Build signal contract readers.
3. Build candidate-to-intent pipeline.
4. Build order lifecycle/reconciliation tests.
5. Build risk/stop gates.
6. Run replay/pulse only after mechanics pass.

Do not jump directly to live strategy tuning before these mechanics are in place.
