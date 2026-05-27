# Live Runtime Scenario, Risk, ML, And Opportunistic Signal Plan - 2026-05-27

Status: implemented slice
Owner issue: #63
Related issues: #44, #80, #81

## Context

The 2026-05-26 OKC/SAS live run proved the Janus live worker can run a six-sleeve StrategyPlan, but it also showed that sleeve names alone are not enough. The live tick needs explicit event context that explains:

- current game scenario/classification;
- which sleeve families the scenario should favor or suppress;
- where ML/PBP confidence exists and whether it is executable or evidence-only;
- whether realized in-game profit unlocks additional risk;
- whether a valid entry/exit point exists outside the currently configured sleeves.

## Findings From Current App Review

1. Scenario classification existed in `app/modules/agentic/basketball_logic.py` but was not emitted by the live tick.
2. Profit-ratcheted risk existed in the same module but was not connected to event-level aggregation.
3. PBP annotation existed as evidence-only deterministic fallback for the future nano path, but sleeve-level confidence was not visible in the tick artifact.
4. Aggregation candidates without a matching StrategyPlan strategy were blocked for buy intents because buy lifecycle policy was only read from StrategyPlan strategies.
5. WNBA and NBA already share the live tick and aggregation path; WNBA parity remains blocked by missing market-state/price-history panels, tracked by #80/JIT-80-01 and #63/JIT-63-14.

## Implemented Slice

New module:

`app/modules/agentic/live_game_context.py`

The module emits `live_game_context_evidence_v1` with:

- `game_scenario`: S/A/B/C/D classifier result from normalized live snapshot plus outcome states;
- `classification_snapshot`: period, clock, score gap, favorite/underdog price, feed latency inputs, player shock flags, and recent run evidence;
- `sleeve_candidate_review`: scenario-derived sleeve suggestions and duplicate checks;
- `ml_confidence_by_sleeve`: per-sleeve confidence using scenario classifier plus PBP annotation tags, with `executable=false` while the nano path remains evidence-only;
- `dynamic_risk_state`: realized-profit risk ladder output;
- `opportunistic_signal_candidates`: standalone entry candidates only when realized-profit budget funds a minimum order and the candidate declares a paired lifecycle policy.

Live tick wiring:

- `codex_tool/run_live_strategy_tick.py` now attaches `market_state.live_game_context`.
- `live_signal_aggregation` artifacts include the same context.
- Opportunistic signals from this context can enter aggregation like any other signal.

Aggregation/evaluator changes:

- `LiveSignalOrderIntentCandidate` now carries `lifecycle_policy`, `game_scenario`, `dynamic_risk_state`, and `ml_confidence`.
- StrategyPlan evaluation still blocks standalone buys unless the candidate declares an exit/stop/hold lifecycle policy.
- Accepted standalone candidates carry paired lifecycle metadata into the Janus `OrderIntent`.

## Current Behavior

This does not make arbitrary independent trading live. It creates the first bounded path:

1. Scenario must not be `D` or `U`.
2. Fresh outcome price must be available.
3. Realized event/day profit must create enough realized-profit risk budget to fund the exchange minimum.
4. The standalone candidate must include lifecycle policy: target, stop, hold, and rebuy review behavior.
5. Normal Janus live gates still apply: kill switch, direct CLOB/account truth, event budget, max notional, minimum size, StrategyPlan evaluate/execute, worker controls, and order-management reconciliation.

## ML/LLM Status

Current ML use is evidence-only in this slice:

- PBP annotation is deterministic fallback with an intended nano model.
- Sleeve confidence is generated from PBP tags and scenario classifier output.
- These values inform aggregation/postgame/readback, but do not independently authorize orders.

The next #81 slice should replace or augment deterministic PBP tags with a real nano dispatcher while keeping outputs non-executable until reviewed StrategyPlan or explicit signal policy promotes them.

## Validation

Focused tests added or updated:

- `tests/app/modules/agentic/test_live_game_context_pytest.py`
- `tests/app/modules/agentic/test_signal_aggregation_pytest.py`
- `tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py`
- `tests/tools/test_run_live_strategy_tick_pytest.py`

Initial focused validation:

- `python -m pytest -q tests/app/modules/agentic/test_live_game_context_pytest.py tests/app/modules/agentic/test_signal_aggregation_pytest.py tests/app/modules/agentic/test_strategy_plan_contracts_pytest.py`
- `python -m pytest -q tests/tools/test_run_live_strategy_tick_pytest.py`

## Next Work

- #81: real nano PBP dispatcher and aggregate-window escalation policy.
- #80/#63-JIT-63-14: WNBA market-state/price-history panels for parity.
- #44: calibrate realized-profit budget percentages against account-scoped postgame reports.
- #63/#79: let postgame leave-one-out and missed-window sections suggest side/phase budget updates.
