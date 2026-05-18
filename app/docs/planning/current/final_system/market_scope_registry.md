# Janus Market And Scope Registry

Status: draft control contract
Created: 2026-05-17

## Purpose

Define the axes the master controller uses before choosing a persona or task.

The old `live / pregame / postgame / development` framing is useful for basketball, but it is not general enough for Janus as an expectation-markets system. Market domain must be a first-class axis.

## Controller Axes

| Axis | Meaning | Examples |
|---|---|---|
| `market_domain` | Polymarket or Janus market category | `sports`, `crypto`, `geopolitics`, `economics`, `culture`, `global-portfolio` |
| `market_subdomain` | Domain-specific subdivision | `basketball`, `nba`, `wnba`, `btc-up-down`, `election-futures` |
| `event_lifecycle` | Domain-specific event phase | `pregame`, `live`, `postgame`, `settlement`, `monitor`, `research`, `backtest` |
| `janus_control_level` | Who owns strategy/execution authority | `janus-controlled`, `codex-assisted`, `operator-manual`, `watch-only` |
| `system_work_mode` | What kind of work is needed now | `monitoring`, `planning`, `review`, `development`, `docs-sync`, `issue-triage`, `no-op` |
| `maturity_stage` | How much authority the domain has earned | `idea`, `research`, `shadow`, `min-size-test`, `live-limited`, `active`, `scaled` |
| `risk_state` | Current allowed aggression | `protect-only`, `base-scalp`, `realized-profit-expansion`, `tail-sleeve` |

The controller should evaluate all axes before selecting a persona.

## Initial Domain Registry

| Domain | Subdomain | Current Stage | Default Control Level | Current Purpose |
|---|---|---|---|---|
| `sports` | `basketball/nba` | `live-limited` | `janus-controlled` plus `codex-assisted` | First mature implementation base; source of current live-event architecture and internal covered-market portfolio/inventory behavior. |
| `sports` | `basketball/wnba` | `min-size-test-pending` | `shadow` to `codex-assisted` | High-priority portability lane; needs capture/replay/fillability evidence, careful minimal testing, and internal covered-market inventory handling before live expansion. |
| `global-portfolio` | `polymarket-account` | `research / order-path-incubation` | `codex-assisted` after approved gates | Codex global portfolio management for operator/global positions, exits, stale targets, rebuy zones, concentration risk, and uncovered-market trend scouting. It is separate from Janus covered-market portfolio/inventory. |
| `crypto` | `up-down-options` | `idea` | `watch-only` | Future high-frequency domain requiring extensive backtest, tick data, and separate risk model. |
| `geopolitics` | `long-term-events` | `idea` | `watch-only` | Future long-horizon signal and target/rebuy domain. |
| `economics` | `macro-events` | `idea` | `watch-only` | Future domain after core ledger/risk/review infrastructure is stable. |
| `culture` | `long-tail-events` | `idea` | `watch-only` | Future opportunistic monitoring only. |

## Domain Promotion Ladder

| Stage | Allowed Work | Required Evidence To Promote |
|---|---|---|
| `idea` | Capture concept in backlog/Obsidian. | Operator approval to research. |
| `research` | Profile studies, external signal map, data-source audit. | Clear data sources, market mechanics, and risk hazards. |
| `shadow` | Passive capture, replay, no orders. | Replay artifacts, fillability estimates, calibration caveats. |
| `min-size-test` | Minimum allowed limit-order tests under explicit gates. | Clean ledger, direct CLOB proof, postgame review, no unresolved inventory. |
| `live-limited` | Bounded live strategy execution with low risk. | Repeated reviewed success, low drawdown, working shutdown and reporting. |
| `active` | Normal risk-managed operation. | Validated risk ladder, review bundle, issue governance, mature monitoring. |
| `scaled` | Progressive capital increase. | Consistent realized returns, attribution, calibrated risk model, operator approval. |

Promotion must be evidence-based and reversible.

## Conservatism Rule

All new domains start conservative. The system becomes less conservative only when realized returns, sample size, attribution quality, drawdown behavior, fillability evidence, and review-bundle quality justify it.

Open unrealized profit does not promote a domain or unlock risk.

## Basketball Mapping

Basketball-specific lifecycle:

| Lifecycle | Controller Meaning |
|---|---|
| `pregame` | Integrity and planning before a watched NBA/WNBA event. |
| `live` | Janus runtime monitoring and event intervention. No broad development. |
| `postgame` | Event review, attribution, issue generation, memory update. |
| `settlement` | Direct CLOB/account settlement verification. |

NBA and WNBA share contracts where possible, but WNBA retains separate liquidity, player, quarter, spread, and market-microstructure calibration.

## Global Portfolio Mapping

Global portfolio management is not the same as live sports trading and not the same as the internal Janus covered-market portfolio/inventory manager.

Initial allowed work:

- read-only position scan
- target/stale-order detection
- concentration and correlation notes
- proposed exits/rebuys as issues or operator-review tasks
- Obsidian profile/market notes
- proactive trend-opportunity scouting in uncovered categories when higher-priority live safety and NBA/WNBA readiness work is not active

Execution authority requires a separate safety policy and must not inherit basketball live permissions or NBA/WNBA testing budget.

The approved execution surface may be either:

- Janus portfolio order-management through Janus API/runtime gates.
- Independent direct Polymarket fallback through `codex_tools/polymarket/*` only after `automation/codex_tooling_contract.md` and `#53` are implemented and approved.

## Crypto Mapping

Crypto up/down options require a separate incubation path:

1. Market mechanics and fee/liquidity study.
2. Tick-source evaluation.
3. Backtest framework.
4. Shadow predictions.
5. Fillability simulation.
6. Minimum-size tests.
7. Live-limited promotion only after review.

This domain should share CLOB, ledger, reporting, and risk infrastructure, but not basketball-specific quarter/PBP logic.
