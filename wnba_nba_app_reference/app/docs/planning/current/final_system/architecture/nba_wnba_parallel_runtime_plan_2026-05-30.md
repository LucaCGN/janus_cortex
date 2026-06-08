# NBA/WNBA Parallel Runtime And Performance Plan - 2026-05-30

Status: source-truth planning artifact
Created: 2026-05-30
Primary issue: https://github.com/LucaCGN/janus_cortex/issues/63
Related issues: #44, #80, #81, #82

## Purpose

This artifact updates the intended Janus NBA/WNBA live-runtime diagrams after the May 27-29 live-money windows.

The central correction is that NBA and WNBA are the same Janus covered-market platform after feed normalization. The remaining differences should be source adapters, market mappings, active signal producers, and league-specific config. They should not be separate execution logic.

This artifact is also the source-truth bridge for the next performance refactor:

1. duplicate same-sleeve/same-band signals must not consume the event budget twice;
2. order sizing must support parallel sleeves instead of one duplicate pair eating the full test budget;
3. budget accounting must use cost basis, open buy-order notional, peak cash at risk, and realized closed PnL, not current mark value or gross churn;
4. underwater inventory must not automatically block a distinct lower-band scalp/grid cycle when the score, time left, liquidity, and remaining budget justify the new cycle;
5. `gpt-5.4-nano` must become a visible evidence-only PBP classifier path, with deterministic fallback recorded when dispatch cannot run.

## Current Performance Diagnosis

Recent live windows showed three performance buckets:

| Bucket | Observed behavior | System lesson |
|---|---|---|
| High | Trades occur in one price band and the system also holds the winning side. | This can make money, but it is not yet proof of parallel sleeve intelligence. |
| Middle | Scalp/target trades work, but losing residual inventory remains unmanaged. | Reduce/stop must manage the residual lot, and new rebuys must require fresh thesis review. |
| Low | Early entry falls sharply and no useful add, hedge, reduce, or independent lower-band trade follows. | Inventory risk and opportunity risk need separate buckets. Existing losing inventory cannot be the only reason a stronger lower-band signal is ignored. |

The recurring defect behind all three is that the system often behaves like one effective sleeve per side. Multiple producers may emit evidence, but same-sleeve duplicate signals and coarse event-position blockers collapse the runtime into one budget-consuming action. The design target is parallel bounded cycles, not repeated buys from the same lane.

## 1. Shared NBA/WNBA Runtime

```mermaid
flowchart TD
  NBA["NBA feed adapters"] --> SNAP["Normalized live snapshot"]
  WNBA["WNBA feed adapters"] --> SNAP
  CLOB["Polymarket CLOB/orderbook"] --> SNAP
  ACCT["Direct account inventory/orders/fills"] --> SNAP
  PRIOR["Optional pregame priors"] --> SNAP
  CTRL["Runtime event controls"] --> SNAP

  SNAP --> BUS["Signal/input bus"]

  BUS --> DET["Deterministic producers"]
  BUS --> ML["ML producers"]
  BUS --> LLM["LLM producers"]
  BUS --> OP["Codex/operator proposals"]
  BUS --> LIFE["Order-fill/target/reduce lifecycle"]

  DET --> AGG["Live signal aggregator"]
  ML --> AGG
  LLM --> AGG
  OP --> AGG
  LIFE --> AGG

  AGG --> DEDUPE["Same sleeve/side/band/cycle dedupe"]
  DEDUPE --> BUDGET["Parallel sleeve budget allocator"]
  BUDGET --> PLAN["StrategyPlan revision or order-intent candidate"]
  PLAN --> GATES["Janus evaluate/execute/live-worker gates"]
  GATES --> OMS["Order-management path"]
  OMS --> CLOB2["Polymarket CLOB"]
  CLOB2 --> RECON["Post-call direct reconciliation"]

  RECON --> TARGET["Target management evidence"]
  RECON --> MICRO["Paired microcycle evidence"]
  RECON --> REDUCE["Reduce/stop evidence"]
  RECON --> PNL["Account-return and peak-risk evidence"]
  PNL --> POST["Postgame review"]
  POST --> LOOP["Issue/docs/Obsidian learning loop"]
```

Binding interpretation:

- LLMs and Codex do not execute orders. They create evidence, proposals, or reviewed StrategyPlan/config changes.
- Account-return reporting must separate gross activity from actual PnL and peak cash at risk.
- Parallel sleeve allocation happens before order intent selection. If a duplicate same-sleeve signal appears, it is activity evidence, not a second budget claim.

## 2. Live Game Tick Loop

```mermaid
flowchart TD
  T["Worker tick every N seconds"] --> FEED["Refresh scoreboard/PBP/stat feeds"]
  T --> BOOK["Refresh CLOB book"]
  T --> ACCOUNT["Refresh direct account inventory/open orders/fills"]
  T --> PLAN["Read StrategyPlan and event controls"]
  T --> PRIOR["Read optional pregame prior"]

  FEED --> SNAP["Normalized snapshot"]
  BOOK --> SNAP
  ACCOUNT --> SNAP
  PLAN --> SNAP
  PRIOR --> SNAP

  SNAP --> TRIG["Detect triggers"]
  TRIG --> QTR["Quarter/half/end triggers"]
  TRIG --> SCORE["Score-gap/spread triggers"]
  TRIG --> PRICE["Price-band/retest triggers"]
  TRIG --> PBP["PBP/player shock triggers"]
  TRIG --> FILL["Order-fill/target/reduce triggers"]
  TRIG --> MANUAL["Manual/Codex intervention triggers"]

  QTR --> SIG["Produce structured signals"]
  SCORE --> SIG
  PRICE --> SIG
  PBP --> SIG
  FILL --> SIG
  MANUAL --> SIG

  SIG --> DEDUPE["Dedupe same sleeve/side/band/cycle"]
  DEDUPE --> LOCAL["Apply sleeve-local gates"]
  LOCAL --> RISK["Compute cost-basis/open-order/peak-risk budget"]
  RISK --> PRIORITY["Prioritize reduce/exit before buy/rebuy"]
  PRIORITY --> SAFE{"Global safety gates green?"}
  SAFE -- "No" --> BLOCK["Block execution; write exact blocker evidence"]
  SAFE -- "Yes" --> INTENT{"Order intent selected?"}
  INTENT -- "No" --> MONITOR["Monitor only; write evidence"]
  INTENT -- "Yes" --> SUBMIT["Submit only through Janus gates"]
  SUBMIT --> RECON["Reconcile direct CLOB/account"]
  RECON --> T
```

Binding interpretation:

- Entry-only blockers such as `position_limit_reached`, `price_band_not_met`, controlled-entry guards, and clock no-entry windows should block only the relevant entry path.
- Reduce/exit candidates must outrank buy/rebuy candidates when direct inventory is adverse.
- Current market value is not the event-budget denominator. The budget readback must include cost basis, pending open buys, realized closed PnL, and peak cash at risk.

## 3. Trigger And LLM Placement

```mermaid
flowchart LR
  PRE["Pregame research"] --> PREM["gpt-5.5 or mini optional prior"]
  PBP["Live play-by-play stream"] --> NANO["gpt-5.4-nano or deterministic parser"]
  CRIT["Critical live revision or deep loss exposure"] --> FRONT["gpt-5.5 reviewed proposal"]
  MINIIN["Main app review loop"] --> MINI["gpt-5.4-mini reviewed context"]
  CODEX["Codex/operator insight"] --> PROPOSAL["Event-control or StrategyPlan proposal"]

  PREM --> BUS["Signal/input bus"]
  NANO --> BUS
  FRONT --> BUS
  MINI --> BUS
  PROPOSAL --> BUS

  SCORE["Scoreboard trigger"] --> BUS
  PRICE["CLOB price-band trigger"] --> BUS
  QTR["Quarter-end trigger"] --> BUS
  FILL["Order-fill trigger"] --> BUS

  BUS --> AGG["Aggregator"]
  AGG --> NOEXEC["No direct LLM execution"]
  NOEXEC --> REVIEW["Reviewed StrategyPlan/config/order-intent candidate"]
  REVIEW --> GATES["Janus live-worker gates"]
```

Binding interpretation:

- Nano is evidence-only and should run cheaply over PBP deltas when dispatch is configured.
- Mini is the normal reviewed context path for the application.
- Frontier/deep model usage is reserved for kickoff planning, critical exposure review, or postgame synthesis.
- A missing nano path is a visibility/runtime bug, but deterministic fallback must keep the live tick functional.

## 4. Local Strategy Gates Vs Global Safety Gates

```mermaid
flowchart TD
  A["Signals from multiple sleeves"] --> B["Aggregator"]

  B --> C["Sleeve-local entry gates"]
  C --> C1["Band spread/price rules"]
  C --> C2["Score-gap range"]
  C --> C3["Duplicate sleeve/band/cycle cooldown"]
  C --> C4["LLM/model unavailable"]
  C --> C5["Entry-only position budget"]

  C1 --> D["Only blocks that sleeve or cycle"]
  C2 --> D
  C3 --> D
  C4 --> D
  C5 --> D

  B --> E["Global execution gates"]
  E --> E1["Kill switch"]
  E --> E2["Direct CLOB freshness"]
  E --> E3["Token/team mapping"]
  E --> E4["Account/inventory truth"]
  E --> E5["Event cap breach"]
  E --> E6["Worker/session active"]
  E --> E7["Order-management approval"]

  E1 --> F["Blocks every live order"]
  E2 --> F
  E3 --> F
  E4 --> F
  E5 --> F
  E6 --> F
  E7 --> F

  D --> G["Other sleeves remain eligible"]
  F --> H["No execution; write exact blocker"]
```

Binding interpretation:

- Position exposure is not one number. It needs side, sleeve, band, cycle, and lot dimensions.
- A losing 40c lot can block a same-cycle duplicate rebuy while still allowing a distinct 20c-to-30c scalp cycle if event controls and remaining budget permit it.
- Event cap remains global. The opportunity-budget bucket cannot bypass true event cap, stale feeds, token mapping, account truth, or order-management approval.

## 5. Parallel Sleeve And Budget Model

```mermaid
flowchart TD
  EVENT["Event cap"] --> ALLOC["Parallel sleeve budget allocator"]

  ALLOC --> PM["Position-management budget"]
  ALLOC --> OPP["Opportunity budget"]
  ALLOC --> OPEN["Open buy-order reserve"]
  ALLOC --> REAL["Realized closed-PnL adjustment"]

  PM --> CORE["Core hold sleeve"]
  PM --> REDUCE["Reduce/stop sleeve"]
  PM --> MANUAL["Manual imported sleeve"]

  OPP --> GRID["Grid/scalp sleeve"]
  OPP --> REBOUND["Rebound/add-down sleeve"]
  OPP --> INDEP["Independent opportunity candidate"]

  GRID --> MICRO["Paired microcycle engine"]
  REBOUND --> MICRO
  INDEP --> MICRO

  MICRO --> BASIS["Lot/cycle basis and peak-risk accounting"]
  BASIS --> TARGET["Paired target/rebuy/reduce policy"]
  TARGET --> GATES["Janus gates"]
```

Sizing rules to promote:

- Base live testing should prefer smaller per-cycle orders when the event cap is small, so at least two independent cycles can coexist.
- Worker/runtime config must keep per-order buy cap separate from per-game event cap. `max_buy_notional_usd` / `max_order_buy_notional_usd` caps one buy intent; `event_cap_usd` caps the full game across positions, open buy orders, and pending intents.
- For parallel-sleeve tests, preferred live config is smaller `min_size`/per-order cap when exchange rules allow it, paired with a larger explicit `event_cap_usd` and `side_budget_mode=balanced_50_50` when both sides are valid.
- Duplicate same-price/same-sleeve buys are not parallelism; they are a dedupe/cooldown defect unless the StrategyPlan explicitly declares a multi-lot ladder.
- Parallelism means different side, sleeve, band, cycle, or reviewed thesis.
- Open buy orders reserve budget until they fill, cancel, expire, or reconcile stale.
- Realized closed profit can increase opportunity budget only when source confidence is `account_confirmed` or `db_confirmed`.

## 6. Paired Microcycle And Underwater Opportunity State

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> BuyCandidate: signal selected
  BuyCandidate --> BuyOpen: buy submitted through gates
  BuyOpen --> BuyFilled: direct fill detected
  BuyFilled --> SellCandidate: paired sell needed
  SellCandidate --> SellOpen: sell submitted through gates
  SellOpen --> SellFilled: target fill detected
  SellOpen --> SellStale: stale/missing/wrong target
  SellStale --> ReplaceSellCandidate: replace review
  ReplaceSellCandidate --> SellOpen: replace submitted through gates
  SellFilled --> RebuyReview: fresh score/CLOB/event-control review
  RebuyReview --> RebuyCandidate: rebuy allowed
  RebuyReview --> Closed: cycle complete
  RebuyCandidate --> BuyOpen: rebuy submitted through gates
  BuyFilled --> UnderwaterLot: price drops below adverse threshold
  UnderwaterLot --> ReduceReview: stop/Q4/thesis failure
  UnderwaterLot --> IndependentLowerBandReview: distinct lower-band signal
  IndependentLowerBandReview --> BuyCandidate: budget and gates allow
  ReduceReview --> ReduceCandidate: reduce/exit selected
  ReduceCandidate --> SellOpen: reduce submitted through gates
  BuyFilled --> DuplicateBuyBlocked: same sleeve/band/cycle unresolved
  DuplicateBuyBlocked --> SellCandidate
  Closed --> [*]
```

Underwater opportunity rule:

An underwater lot should block duplicate averaging only for the same unresolved cycle unless the StrategyPlan explicitly allows laddering. It should not automatically block a distinct lower-band cycle when:

- the game remains close enough for a rebound thesis;
- enough time remains;
- direct CLOB spread/depth is fillable;
- event cap and opportunity budget have room;
- paired exit/reduce policy is declared before entry;
- direct account inventory and open orders are reconciled.

## 7. CI/CD Learning Loop For 2026-05-30

```mermaid
flowchart TD
  PRE["Pre-2pm source-truth and readiness"] --> G1["2pm WNBA single-game live window"]
  G1 --> PG1["Immediate single-game postgame review"]
  PG1 --> DEV["Patch narrow gaps before evening"]
  DEV --> PREG2["Regenerate/verify 7pm/9pm WNBA and 9pm NBA plans"]
  PREG2 --> G2["Evening multi-game live window"]
  G2 --> PG2["Full postgame review"]
  PG2 --> TASK["Issue/docs/Obsidian task conversion"]
  TASK --> CI["Regression tests and next-day automation loop"]
  CI --> PRE
```

Today should be treated as two live development windows:

1. The 2pm WNBA game is a standalone validation window. It should test feed normalization, direct CLOB/account evidence, duplicate-signal behavior, order sizing, nano/fallback PBP evidence, and account-return reporting.
2. The gap before 7pm is a patch window. Only narrow, tested fixes should be promoted.
3. The 7pm/9pm WNBA games and 9pm NBA game are the evening validation window. They should not inherit stale 2pm scope, stale open-order assumptions, or unreviewed risk changes.

## Immediate Development Plan

### P0 Before 2pm

- Confirm all StrategyPlans and event controls exist for the 2pm game.
- Confirm worker scope does not include stale prior-day events.
- Confirm account-return reports use `actual_pnl_usd`, `peak_cash_at_risk_usd`, and source confidence.
- Inspect duplicate same-sleeve/same-band cooldown evidence before kickoff.
- Inspect whether nano PBP dispatch can run; if not, require explicit deterministic fallback evidence.
- Prefer smaller per-cycle sizing when event cap is small so at least two independent cycles can be tested.

### 2pm Live Window

- Keep live-money testing inside Janus gates only.
- Do not classify normal monitored exposure as RED. RED is only active unsafe/damaging behavior: wrong scope/mapping, stale feed allowing buys, orders outside gates, unreconciled executions, repeated same-cycle buys, event cap breach, or adverse direct inventory without reduce candidate after the defined threshold.
- Capture whether each signal is duplicate, local-blocked, global-blocked, selected, or executed.

### Post-2pm Patch Window

- Run fast postgame with account-return calculator.
- If duplicate same-band orders consumed budget, patch dedupe/cooldown before evening.
- If sizing prevented parallel lanes, patch StrategyPlan size defaults/config before evening.
- If nano still never appears and credentials/dispatch are enabled, patch the narrow dispatch wiring before evening; otherwise record deterministic fallback as expected.
- If reduce/stop does not appear for adverse inventory, patch #82 before evening even if it interrupts readiness.

### Evening Window

- Validate 7pm/9pm WNBA and 9pm NBA on the same normalized runtime.
- Confirm no separate WNBA execution architecture exists.
- Confirm account inventory, open orders, fills, target management, paired microcycle, reduce/stop, and post-call reconciliation are event-scoped.
- After final, produce complete per-game storylines with actual return on peak cash at risk, not gross turnover.

## May 30 2pm Readiness Checkpoint

Event: `wnba-sea-tor-2026-05-30`.

Pre-window evidence:

- StrategyPlan exists at `local/shared/artifacts/strategy-plans/2026-05-30/wnba-sea-tor-2026-05-30/current.json`.
- Event controls exist at `local/shared/artifacts/event-controls/2026-05-30/wnba-sea-tor-2026-05-30/current.json`.
- Prewindow readiness exists at `local/shared/artifacts/prewindow-sleeve-readiness/2026-05-30/wnba-sea-tor-2026-05-30/prewindow_sleeve_readiness_20260530T130734Z.json`.
- Integrity check exists at `local/shared/artifacts/ops/2026-05-30/integrity-check_20260530T130803Z.json` and marks direct CLOB readiness sufficient for minimum live orders, with only the known portfolio mirror cash mismatch as non-blocking.
- The stale May 29 worker scope was stopped through Janus controls before the May 30 worker was started.
- The live worker is scoped only to `wnba-sea-tor-2026-05-30` with `execute=true`, `live_money=true`, `enable_llm_dispatch=true`, `max_buy_notional_usd=10`, and `max_intents=4`.
- The first rehearsal with `max_buy_notional_usd=4` proved why worker-level max notional cannot double as event cap for parallel tests: it collapsed the event budget to one small cycle. The live start corrected this to `$10` event cap while preserving `$4` per-strategy intent sizing.
- Pregame live ticks show `duplicate_signal_count=0`, no order candidates, and only expected pregame local blockers such as `scoreboard_freshness_required`.
- The nano PBP path was fixed before kickoff. Latest live tick evidence shows `pbp_annotation.model_tier=nano` and `nano_dispatch.status=response_recorded`; tags remain evidence-only and cannot authorize orders.

Open validation for the live/postgame pass:

- Whether duplicate same-sleeve/same-band cooldown remains clean once live signals appear.
- Whether paired targets, reduce/stop lifecycle, and account-return sections stay correct after actual fills.
- Whether 5-share exchange minimums are small enough for useful parallelism at the current cap, or whether the evening StrategyPlans need a narrower live-money profile.

## May 30 Post-2pm Sizing/Budget Hotfix

Evidence from `wnba-sea-tor-2026-05-30` showed the remaining budget design problem clearly:

- same-side duplicate buys were patched with economic duplicate dedupe, but one runtime knob still carried two meanings;
- the live worker treated `max_buy_notional_usd` as both per-order sizing cap and event cap;
- that made it impossible to run smaller orders with a larger game budget for multiple independent sleeves or both-side strategies.

The runtime now separates those controls:

| Control | Meaning | Live testing use |
|---|---|---|
| `order_sizing_mode=fixed_min_shares` | Fixed-share live-test order sizing. | NBA/WNBA default: each buy is exactly `min_size` shares. Do not express live order size as dollars. |
| `min_size` | Minimum shares for an order intent. | Keep at the exchange minimum, currently 5 shares, unless direct exchange evidence proves another value. |
| `min_buy_notional_usd` | Legacy minimum-notional scaler. | Set to `0` or ignore under `fixed_min_shares`; do not let it resize a 5-share order. |
| `max_buy_notional_usd` / `max_order_buy_notional_usd` | Legacy maximum notional for one buy intent. | Do not use as the NBA/WNBA order-size control; use only for explicit notional-capped experiments. |
| `event_cap_usd` | Full per-game cap across current position cost basis, open buy orders, and pending buy intents. | Raise independently when the operator approves more parallel live testing room. |
| `side_budget_mode=balanced_50_50` | Local side cap for both-side participation. | Use when both outcomes can be traded as distinct sleeves without one side exhausting the whole event cap. |
| `max_same_side_exposure_pct` | Optional same-side local cap. | Use when balanced mode is not appropriate but one side still needs a ceiling. |

Acceptance rule: a live configuration is valid for parallel testing only when buy order size is fixed at the exchange-minimum share count, event cap is large enough for multiple distinct 5-share cycles, and the StrategyPlan contains distinct side/sleeve/band/cycle identities. Duplicate same-price same-side orders remain blockers unless explicitly reviewed as laddered lots.

## Issue Routing

| Issue | Routing |
|---|---|
| #63 | Parent runtime and parallel sleeve architecture; this artifact belongs here. |
| #82 | Reduce/stop lifecycle, underwater-lot reduce/exit behavior, Q4/endgame loss mode. |
| #81 | Nano PBP evidence path, model-tier readback, deterministic fallback visibility. |
| #44 | Risk calibration, risk ratchet, peak cash at risk, confirmed PnL-only risk promotion. |
| #80 | WNBA replay parity and postgame replay fixtures. |
| #83 | Closed accounting correction; do not reopen unless actual/account-return separation regresses. |

## May 30 Final Readback

The final daily postgame source truth is `app/docs/reference/postgame_evaluation_2026-05-30_nba_wnba_live_window.md`.

Account-confirmed return was positive overall: `+$6.616514` actual PnL on `$35.908342` peak cash at risk, or `+18.426119%` return on peak risk. That result validates the account-return denominator correction, but it does not validate unattended strategy quality.

The next development loop should focus on:

1. rebound-snipe sleeves separate from micro-band scalping;
2. manual-interference rebase and budget separation;
3. player-catalyst PBP/status shocks, especially foul trouble and bench/rest windows;
4. duplicate signal pressure reduction without reducing parallel event caps;
5. final cleanup and residual lifecycle readback.

## Non-Negotiable Boundaries

- No order placement, cancel, replace, submit, sign, broadcast, redeem, or manual order management outside Janus StrategyPlan/live-worker/order-management gates.
- Optional pregame priors are context only.
- Chat, screenshots, Obsidian, and stale mirrors are not live trading truth.
- Direct CLOB/account truth outranks Janus DB, runtime artifacts, docs, and inference.
- Docs and diagrams can define target architecture, but code/tests/live artifacts decide whether a behavior is implemented.
