# Crypto Options Testing And CD/CI Design

Date: 2026-06-02

Status: technical spec for validation and autonomous development loop.

## Purpose

Define how the new app is built and validated incrementally before strategy tests risk money.

The automation must develop issue-by-issue, grounded in specs and GitHub acceptance criteria.

## Test Layers

| Layer | Purpose |
| --- | --- |
| Unit | Validate schema, parsers, contracts, gates, lifecycle functions. |
| Integration | Validate DB, repositories, API routers, feed workers, strategy manager. |
| Replay | Validate candidates against historical frames with no lookahead. |
| Pulse | Validate live structure with minimal sizing and hard caps. |
| Standard strategy test | Validate candidate performance after structural integrity. |
| Promotion test | Validate longer runs for promising candidates. |

## Required Test Groups

- app startup and router registration
- canonical DB initialization
- shard import parity checks
- profile grading and usage gates
- event-level reconstruction
- signal generator contracts
- indicator computation
- replay frame generation
- fill and exit simulation
- strategy spec validation
- candidate-to-intent conversion
- market/limit BUY and SELL intents
- partial fill and unfilled order handling
- lifecycle coverage
- reconciliation mismatch
- risk gates and stop gates
- restart state reload
- report attribution

## Automation Validation Sequence

1. Structural validation:
   - Build app skeleton, DB, feeds, API, replay, strategy manager, trading lifecycle, risk gates.
   - Run unit, integration, and replay tests.
   - No live orders.

2. Minimal structural strategy test:
   - Test all 10 candidate strategies once where feasible.
   - Use minimal order sizing.
   - Max 3 trades or bounded event count per strategy.
   - Purpose is structural integrity, not profitability.

3. Standard candidate testing:
   - Run strategies in groups of 5.
   - Low-volume strategy: 10 events.
   - High-volume strategy: 50 trades.
   - Hard cap: max 2 events and 1 hour per run.
   - First run uses minimal order size.
   - Increase toward strategy recommended size only after structural and financial sanity passes.

4. Promotion testing:
   - Repeat until 3 promising candidates remain.
   - Run each promising candidate up to 3 hours with $50 budget.
   - Continue until 1 to 3 final candidates remain.

## Stop/Pause Conditions

Automation must stop or pause the affected run on:

- mechanical failure
- missing lifecycle coverage
- ledger/reconciliation mismatch
- excess drawdown
- stale feeds
- stale service
- strategy spec violation
- duplicate cadence
- credentials/access failure

## Codex Automation Prompt

Use this prompt only after the technical specs and GitHub issues are reviewed:

```text
You are implementing the Crypto Options App rebuild in repo C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex.

Source of truth:
- crypto_options_app/docs/reference/crypto_options/technical_specs
- GitHub parent issue #108 and child issues #109-#118
- issue #47 only as research/history, not implementation authority

Work issue-by-issue. Before editing, read the linked technical spec and acceptance criteria. Implement the smallest coherent slice, run focused tests, and update the issue with results. Do not live-test any strategy until structural tests for app skeleton, DB, feeds, API, replay, strategy manager, trading lifecycle, and risk gates pass.

Never manually place, cancel, sign, broadcast, redeem, route, recommend, or authorize individual orders from chat. Live activity belongs only to the supervised trading runtime through explicit executor gates, risk gates, ledgers, and reconciliation.

Validation sequence:
1. Structural validation with no live orders.
2. Minimal structural strategy test for all 10 candidates where feasible, minimal size, max 3 trades or bounded event count.
3. Standard candidate testing in groups of 5, with low-volume 10-event cap, high-volume 50-trade cap, max 2 events and 1 hour.
4. Promotion testing for 3 promising candidates, up to 3 hours and $50 budget.
5. Continue until 1 to 3 final candidates remain or a hard safety blocker requires user review.

If a patch is needed during a live-supervised phase, stop cleanly if needed, patch, test, restart the supervised service, monitor the first minute, confirm fresh DB/feed/runtime state, then report.
```

## Acceptance Criteria

- Tests are mapped to each technical spec and GitHub issue.
- CD/CI prompt is ready but does not start implementation before review.
- Live strategy testing is blocked until structural tests pass.
- Promotion and rejection criteria are explicit.
