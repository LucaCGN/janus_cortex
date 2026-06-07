# Crypto Options Trading Engine Master Spec

Date: 2026-06-02

Status: reviewed planning spec for the next trading-engine/strategy architecture pass.

Scope: trading engine and strategy execution design. Specific initial strategy candidates are cataloged in `crypto_options_strategy_format_spec_2026-06-02.md`; they are replay/pulse candidates, not live approval.

## 1. Objective

Build a modular crypto options trading engine that can safely execute many strategy variants over the same data system.

Every strategy variant will generally be a combination of:

- outcome prediction
- scalping
- cashout
- hedge management

The engine must support those mechanics without hardcoding one strategy style into the runtime.

## 2. Non-Negotiable Boundary

The trading engine may submit orders only through the supervised service, isolated micro-executor, ledger gates, and reconciliation layer.

No chat, agent prompt, report, strategy spec, or data pipeline may manually:

- place orders
- cancel orders
- sign orders
- broadcast transactions
- redeem positions
- route individual orders
- recommend or authorize individual live orders

Strategy code emits structured intents. The executor decides whether an intent can become an order.

## 3. Required Engine Capabilities

The engine must support:

- market BUY orders
- market SELL orders
- limit BUY orders
- limit SELL orders
- partial fills
- unfilled orders
- order cancellation/expiry state
- cashout exits
- split exits
- hold-to-settlement lifecycle coverage
- hedged inventory management
- per-strategy budgets
- per-strategy risk gates
- per-strategy attribution
- restart from existing run root and ledgers
- one live cadence per active run root

The engine must separate:

- candidate
- intent
- order
- fill
- position
- exit
- settlement
- PnL
- report attribution

## 4. Data System Dependency

The trading engine reads from the canonical crypto data system.

Required namespaces:

- profile rows: `profile_*`
- event/market rows: `polymarket_*`
- underlying/indicator rows: `crypto_*`
- future replay rows: `replay_*`

The engine must use shared event identity:

- `event_slug`
- `condition_id`
- `market_id`
- `token_id`
- `outcome`

Any strategy candidate that cannot be linked to the canonical event/token universe is not executable.

## 5. Signal Families

All strategy variants consume signals from three families.

### 5.1 Profile-Based Signals

Profile signals must specify:

- profile types used
- profile grades allowed
- activity recency required
- aggregation latency
- aggregation window
- aggregation method
- conflict handling
- buying-ahead handling
- strategy role: directional, hedge proportion, band/rebound, volatility/liquidity, or metadata

Valid profile account types:

- `outcome_predictor`
- `grid_buyer`
- `hedger`
- `scalping_trader`
- `unknown`

Profile grade and profile usage are separate. Recent activity is a usage gate, not a grading component.

### 5.2 Event-Based Signals

Event signals must specify:

- last event outcomes
- current delta from target
- signed side delta from target
- time remaining
- pre-start/active/final-minute/expired phase
- event threshold availability
- current YES/NO quote availability
- event price fluctuation
- buying-ahead behavior

`target_delta_abs` means underlying price distance from event threshold, not cashout target distance.

### 5.3 Indicator-Based Signals

Indicator signals must specify whether each indicator is:

- pre-event grounding
- active directional signal
- blocker
- sizing input
- report-only context

Initial indicator set:

- `target_relative_ema_momentum_v1`
- `volume_weighted_pressure_v1`
- `support_resistance_band_confluence_v1`
- `volatility_per_second_5m`
- `volatility_per_second_1h`
- `volatility_per_second_1d`

Timing is part of the indicator contract. A 1d volatility signal and a 5m momentum signal do not belong to the same timing class.

## 6. Strategy Format

Every strategy variant must be represented as a structured strategy spec.

At minimum, a strategy spec declares:

- `strategy_id`
- `strategy_family`
- signal family inputs
- allowed profile types
- allowed profile grades
- event timing rules
- indicator timing rules
- order types allowed
- duplicate exposure rules
- hedge rules
- cashout rules
- lifecycle coverage rules
- budget
- risk gates
- stop gates
- replay requirements
- live-pulse requirements
- observability fields

Example naming shape only:

- `profilebased_outcomeprediction_hedge_scalping`
- `proprietarysignals_outcomeprediction`
- `profilebased_outcome_prediction_hold`
- `outcome_prediction_price_signals`

These names are placeholders for format design. This spec does not approve those variants.

The active initial candidate catalog is maintained in `crypto_options_strategy_format_spec_2026-06-02.md`. The engine must treat every catalog entry as a structured strategy spec that still has to pass validation, replay, pulse integrity checks, and risk gates before execution.

## 7. Timing Model

Timing is a first-class input.

The engine must support:

- pre-event
- event-start
- early active event
- mid-event
- final-minute
- post-event
- settlement/reconciliation

Examples:

- `volatility_per_second_1h` and `volatility_per_second_1d` may be valid only as pre-event grounding.
- `volatility_per_second_5m` may be reviewed at a specific event mark.
- `volume_weighted_pressure_v1`, `support_resistance_band_confluence_v1`, and `target_relative_ema_momentum_v1` may be active loop signals if a strategy declares that role.

The strategy spec must define timing, not infer it ad hoc.

## 8. Mandatory Block Gates

The engine must block execution when:

- no eligible profiles exist for a profile-driven strategy
- no current executable quote exists
- event/token identity is missing
- event threshold is missing when required
- event start/end time is missing when required
- quote age is stale
- profile data is stale
- monitor artifact is stale beyond threshold
- spread/liquidity is outside strategy limits
- candidate packet is ready but lacks a current eligible source
- lifecycle coverage cannot be created
- correlated exposure cap would be violated
- strategy budget would be exceeded
- order state reconciliation is uncertain

Blockers must be structured, counted, and reported.

## 9. Required Unit/Module Tests

The next implementation pass must include tests for:

- strategy spec parsing and validation
- event identity linking
- profile routing by grade/type
- signal aggregation window behavior
- indicator timing role behavior
- candidate to intent conversion
- intent idempotency
- market order intent building
- limit order intent building
- stale quote blocking
- stale profile blocking
- no-profile blocking
- duplicate same-side exposure blocking
- lifecycle coverage requirement
- partial fill handling
- unfilled order handling
- ledger reconciliation state transitions
- stop gate triggers
- restart preserving run root and ledgers

No live strategy should run until these basic mechanics are tested.

## 10. Sub-Specs

This master spec is supported by:

- `crypto_options_order_lifecycle_reconciliation_spec_2026-06-02.md`
- `crypto_options_signal_contracts_spec_2026-06-02.md`
- `crypto_options_strategy_format_spec_2026-06-02.md`
- `crypto_options_risk_observability_test_spec_2026-06-02.md`
