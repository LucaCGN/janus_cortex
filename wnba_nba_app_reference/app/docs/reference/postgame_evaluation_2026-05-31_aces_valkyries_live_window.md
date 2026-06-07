# May 31 Aces/Valkyries Live Window Postgame Evaluation

Status: postgame source-truth note
Event: `wnba-lva-gsv-2026-05-31` / Polymarket `wnba-las-gsv-2026-05-31`
Final: Las Vegas Aces 91, Golden State Valkyries 81

## Source Artifacts

- Runtime report: `local/shared/reports/daily-live-validation/postgame_analysis_2026-05-31_aces_valkyries_flow_watchpoints.md`
- Postgame artifact: `local/shared/artifacts/ops/2026-05-31/postgame-review_20260531T223602Z.json`
- Post-patch monitor: `local/shared/artifacts/ops/2026-05-31/live-monitor_20260531T203455Z.json`
- StrategyPlan: `local/shared/artifacts/strategy-plans/2026-05-31/wnba-lva-gsv-2026-05-31/current.json`
- Event controls: `local/shared/artifacts/event-controls/2026-05-31/wnba-lva-gsv-2026-05-31/current.json`

## Postgame Result

The system produced useful live evidence but not a profitable or promotable strategy sample.

- Live evidence present: 160 orderbook ticks, 1,047 strategy decisions, 7 order intents, 4 Janus execution rows, and 50 live-worker ticks.
- Account return: `actual_pnl_usd=0.0`, `peak_cash_at_risk_usd=0.0`, `status=flat_no_trades`, `source_confidence=account_confirmed`.
- Worker state: stopped after a safety halt around 2026-05-31T19:47Z.
- Oversized-row investigation: Janus order surfaces only supported fixed 5-share submissions; apparent 50-share and 240-share Aces rows came from current-token trade rows with timestamp `0`, not reliable current-account evidence.
- Patch state: live monitor/live tick now quarantine timestamp-0 direct trades as untrusted. The post-patch monitor showed open orders `0`, open positions `0`, trusted trades `0`, untrusted trades `4`, and unresolved inventory `false`.

## Checklist Findings

The original pregame checklist should be treated as partially proven at the plan/evidence level, but not proven at strategy-performance level.

- DB/stat context: WNBA data freshness was green, but StrategyPlan and LLM traces did not explicitly assert the team/player/stat context used. Track under `JIT-88-01` / #88.
- Parallel sleeves: the plan had eight active sleeves across both teams (`grid_scalp`, `core_hold`, `ultra_low_rebound`, `controlled_fill`), but runtime proof was incomplete because most sleeves were blocked and the worker stopped early. Track blocker scope under `JIT-89-01` / #89.
- Quarter revisions: runtime emitted LLM traces and `quarter_end` triggers, but dispatch was disabled and no durable add/remove/keep StrategyPlan revision rows were produced. Track under `JIT-87-01` / #87.
- Trigger semantics: there were order intents and replay candidates, but why-no-trade evidence had 399 blocker observations, with most scope classified as `unknown`. Track under `JIT-89-01` / #89.
- Profit-ratcheted budget: not tested because there were no account-confirmed realized gains. Track under `JIT-90-01` / #90.
- Order sizing/minimums: fixed 5-share limit policy was safe for this run, but order-type-aware Polymarket minimums remain unimplemented across all paths. Track under `JIT-86-01` / #86.
- Direct reconciliation: live monitor was patched, but postgame direct-event scope still reports `trade_count=4` without the same trusted/untrusted split. Track under `JIT-84-01` / #84.
- Runtime control: API restart after patch left a stale stopped worker config visible. It was safe only because stopped. Track fail-closed restart behavior under `JIT-85-01` / #85.

## Next Development Order

1. `JIT-92-01` / #92: canonical live-event state endpoint and JSONL evidence stream.
2. `JIT-91-01` / #91: NBA event-to-scoreboard resolution diagnostics.
3. `JIT-84-01` / #84: postgame direct-trade trust split for timestamp-0/invalid rows.
4. `JIT-85-01` / #85: fail-closed worker config after API restart.
5. `JIT-86-01` / #86: order-type-aware Polymarket minimum sizing.
6. `JIT-87-01` / #87: first-class quarter-bound StrategyPlan/LLM revision artifacts.
7. `JIT-88-01` / #88: explicit DB-stat/team-player context traces and stale NBA stat refresh.
8. `JIT-89-01` / #89: blocker-scope and trigger-semantics cleanup.
9. `JIT-90-01` / #90: dollar-for-dollar account-confirmed realized-profit budget add-ons.

## Promotion Decision

Do not promote any sleeve or cap from this game. Use it as a safety, observability, and development-prioritization sample only.
