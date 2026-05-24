# Postgame Evaluation - 2026-05-20 NBA/WNBA Live Window

Status: current evaluation
Updated: 2026-05-21

## Scope

This document evaluates the 2026-05-20 Janus covered-market live-window work for:

- GitHub `#61`: Spurs vs Thunder NBA playoff live test.
- GitHub `#62`: Portland Fire vs Indiana Fever WNBA live-promotion test.
- Supporting issue `#55`: NBA pregame vs immediate-live vs post-Q1 entry timing.
- Closed support issue `#60`: WNBA passive capture/audit foundation.

Primary local evidence:

- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_nba_live_min_order_plan_20260520T235126Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_nba_post_order_monitor_plan_20260521T0040Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_spurs_target_live_20260521T0044Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_nba_post_target_monitor_plan_20260521T0045Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue62_wnba_live_canonical_plan_20260520T2359Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue62_wnba_post_order_monitor_plan_20260521T0002Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue62_wnba_monitor_extension_20260521T0038Z.json`

## Executive Read

The NBA live path proved the minimum-size Janus order path can work, but the strategy was too narrow. The live Spurs trade validated a basic micro-grid scalp: buy a 5-share underdog lot near `0.31`/`0.32`, place a 5-share target near `0.35`, and let the target handle a short-cycle profit. That is useful, but it captured only the first layer of the opportunity.

The correct next strategy is not one all-or-nothing position. The better live-game structure is parallel inventory:

- buy a base lot large enough to split, usually `10` shares while validation remains small;
- sell only half, usually `5` shares, into the first target;
- keep the remaining `5` shares as a core hold for the later game regime;
- after the target fills, allow a rebuy of `5` shares when the same side returns to a valid band;
- run the grid sleeve and the core-hold sleeve at the same time, with separate target/stop/review rules.

WNBA did not reach the same quality bar. The local artifacts show a WNBA live-order and position-management attempt, but the operator-visible conclusion is that no WNBA trade was achieved in the way needed for confidence. Treat `#62` as still open until direct account/CLOB truth, UI-visible history if available, Janus ledger, and a post-call fill reconciliation all agree that a controlled minimum-size WNBA live trade executed and was monitored.

## NBA What Worked

- Direct CLOB and Janus StrategyPlan gates were brought into the live window instead of staying as passive docs.
- The Spurs minimum-size buy path produced a concrete external order and transaction in Janus evidence.
- A target order was placed through the approved Janus portfolio-manager order-management path.
- The post-target monitor plan correctly disabled duplicate fresh entries and moved to position-management posture.
- The operator's manual reasoning matched a valid basketball pattern: underdog price was tradable during early-game oscillation, not a pregame blind bet.

## NBA What Broke Or Underperformed

- The plan treated the position as a single 5-share lot. Once a 5-share target was placed, there was no core hold left.
- The target was protective but not portfolio-aware. It did not distinguish scalp inventory from upside inventory.
- The monitor plan set `no_new_entry=true` and `position_management_only=true`, which was safe but prevented the next logical rebuy/grid cycle.
- The plan did not automatically propose a second sleeve after the first target, even though score/price movement made a parallel strategy appropriate.
- The late-game upside and favorite/underdog regime transitions were not converted into new Janus-owned StrategyPlan revisions.

## Risk Profile And Minimum-Order Conflict

The May 20 live window also exposed a structural conflict between the current account size, Polymarket minimums, and the strategy we want to test.

At 5-share minimums, each basketball leg often costs roughly `$1` to `$5`, while first-layer scalp profits can be measured in cents. That makes the ideal strategy shape hard to test with the current bankroll: we want several parallel sleeves, staggered rebuys, and partial exits, but the exchange minimum forces every sleeve to be relatively chunky. This pushes the system toward too few trades and too little strategy parallelism.

This should not make Janus more risk-averse by default. The account capital is explicitly being used as a development and learning bankroll. Returns matter, but the near-term purpose is to learn from controlled live mistakes and build unattended competence. A system that avoids all meaningful risk because the minimum order is large relative to the account cannot learn the live execution patterns we need.

The Spurs/Thunder Q3 state is the concrete example. Spurs were trading around the high `0.30s` while the game was still close and while there was a strong basketball thesis for Spurs optionality: a young, unusually dominant 7-foot-plus two-way player had already decided the prior game and remained a credible swing factor. The system should have been less hesitant to trust that kind of strong premise with a bounded test position, especially when the account is intentionally sized for development risk.

Planning implication:

- keep strict direct-CLOB/feed/worker/kill-switch gates;
- keep limit-only orders and per-event caps;
- but once gates are green, allow more deliberate minimum-size test legs instead of waiting for near-perfect certainty;
- treat `10` shares as the smallest practical parallel-sleeve proof when account/risk allows;
- if account size cannot support enough parallel sleeves, document that as a bankroll/minimum-order blocker rather than a strategy failure;
- evaluate whether a dedicated live-learning risk budget should allow higher nominal exposure during approved NBA/WNBA windows so the strategy can actually express grid plus core-hold behavior.

## Missed Q4 Rebound Entry

The Q4 state created another missed entry example. At roughly Q4 `10:35`, Spurs trailed Thunder `92-96`. The visible Polymarket chart was delayed, but the trade panel and orderbook showed the executable Spurs range around `31c` to `32c`, with a tight `1c` spread and visible ask/bid depth.

This should have triggered a rebound-entry review. The game was close, there was still enough clock for a recovery, and the price had moved into a live underdog rebound band. The system did not need the delayed chart to confirm this; the direct orderbook plus score/clock context were enough to propose a bounded minimum-size entry or record the exact blocker.

Technical implication:

- live strategy should not wait for the rendered chart when order panel/book data and direct CLOB are fresher;
- Q4 rebound logic needs an explicit trigger: close game, enough clock, underdog price in `0.25` to `0.40` band, spread `<=2c`, and no event-scoped duplicate exposure blocker;
- if triggered, Janus should either place/propose the bounded leg through gates or create a blocker artifact immediately;
- missed Q4 rebound windows should be logged as strategy misses, not compressed as no-change monitoring.

## Q4 Momentum Swing Example - Wembanyama Stretch Optionality

The later Q4 manual trade is the clearest example of the pattern Janus should attempt much more often.

Observed trade:

- game state: Spurs vs Thunder, Q4, around `5:54` remaining.
- setup: OKC lead widened to about `10`, pushing Spurs as low as roughly `5c`.
- trigger premise: while Wembanyama is not fouled out and not benched, a Spurs scoring stretch remains live because he can create abrupt score and market swings. The same logic should apply to any hot player or high-impact player whose on-court status can quickly change the win-probability band.
- execution example: manual buy around `7c`, `14.3` shares, notional about `$1.03`.
- rebound: OKC lead compressed to about `7`, and Spurs spiked as high as roughly `13c`.
- exit example: manual sell around `12c`, `14.3` shares, gross about `$1.71`, net roughly `$0.68` to `$0.71` before any exact fee/accounting reconciliation.
- later state: OKC lead returned to roughly `9`, Spurs around `11c`/`12c`, showing the band remained tradeable rather than a one-off artifact.

Interpretation:

This is exactly the live momentum micro-grid premise already implied by Janus architecture: PBP/player-impact state, direct CLOB, score/clock, and short-horizon price movement should combine into repeated bounded entries and exits. The system should not need the operator to manually notice every 5c-to-13c or 7c-to-12c move.

Strategy implication:

- add a `hot_player_stretch_optional` trigger: high-impact player on court, not fouled out, not benched, enough clock remaining, and price depressed by an adverse run;
- use direct score/clock plus CLOB price band instead of delayed chart confirmation;
- allow repeated 5-share entries when the same rebound band reappears and event risk budget allows;
- target quick exits at `+4c` to `+8c` or equivalent percentage bands, depending on price level and spread;
- after each manual/operator or Janus trade, persist the before/after score gap, price low/high, player-on-court state, and fill result for replay learning;
- classify missed repetitions of this pattern as strategy misses, not merely conservative safety decisions, when all live gates were otherwise green.

## Resistance Band Volatility Component

The operator's chart annotation makes sense as a Janus component. It should not be used as standalone live authority yet, but the Spurs/Thunder isolated evidence is strong enough to add it as a shadow-first deterministic trigger and replay lane.

Concept:

- after a price spike that precedes a drop, mark the spike area as a resistance band;
- after a local low that precedes a rebound, mark the low area as a support band;
- extend those bands forward through the live window;
- when price revisits a band, classify the retest using current spread, depth, score/clock, player context, and recent price path;
- when at least `3` bands have persisted for enough time or enough direct CLOB ticks/orders, enter a high-volatility regime where repeated buy/sell signals are expected rather than exceptional.

Direct Spurs evidence from local top-book capture supports the idea:

- Spurs token direct CLOB rows after game start: `88` top-book observations from `2026-05-21T00:30:01Z` to `2026-05-21T02:58:25Z`.
- Spurs mid-price range in that live sample: `0.045` to `0.385`.
- Persistent rounded mid bands:
  - `0.34`: `13` touches over `24.6` minutes.
  - `0.33`: `9` touches over `28.0` minutes.
  - `0.32`: `7` touches over `98.2` minutes.
  - `0.30`: `8` touches over `41.8` minutes.
  - `0.23`: `7` touches over `15.4` minutes.
  - `0.21`: `8` touches over `15.1` minutes.
  - `0.17`: `6` touches over `45.9` minutes.
- The live-monitor artifact independently classified the game as `jagged_oscillation`, with `36` oscillation bands, `39` grid opportunities, and `19` spikes in the monitored window.

Rough isolated replay:

- Rule used for this first check: after at least `3` persistent bands exist, buy `5` shares on a downward retest into a persistent support band when the recent move is at least `3c` down, spread is `<=2c`, and the next target band is at least `4c` above entry.
- Trade 1: buy `5` at `0.24`, stop at `0.21`, gross `-0.15`.
- Trade 2: buy `5` at `0.18`, sell at `0.31`, gross `+0.65`.
- Aggregate rough result: `+0.50` gross on about `$2.10` deployed notional, about `+23.8%` gross deployed-notional return before any fee, slippage, duplicate tick, or latency adjustment.
- The manual Q4 low-band trade around `7c -> 12c` adds operator-observed confirmation that lower late-game support bands were also tradeable, even though the captured tick cadence was too sparse to fully replay that exact move.

This is not enough to claim production edge. It is enough to add a replayable strategy lane because the signal showed repeated bands, a high-volatility regime, and profitable gross behavior in a small isolated sample.

### Proposed Strategy: `resistance_band_volatility_v1`

Inputs:

- direct CLOB bid/ask/mid, spread, and available depth;
- score, clock, period, score gap, recent run, and lead changes;
- player-impact state when available, especially hot-player on-court/foul/bench status;
- event-scoped inventory and open orders;
- tick cadence, source latency, and stale-feed flags;
- current StrategyPlan sleeve state.

Band creation:

- create a resistance band from a local spike followed by a drop of at least `max(3c, 10% relative move)`;
- create a support band from a local low followed by a rebound of at least `max(3c, 10% relative move)`;
- round bands to the market tick or `1c` buckets for basketball moneylines;
- merge nearby bands inside `1c` when tick size and spread make them indistinguishable.

Band persistence:

- a band is persistent when it has at least `3` retests over `>=5` minutes; or
- direct orderbook/depth remains clustered within `+/-1c` of the band for `N` valid ticks; or
- both sides of a pair show inverse confirmation, such as underdog support with favorite resistance.

High-volatility trigger:

- at least `3` persistent bands exist in the trailing game window;
- trailing mid range is at least `10c` or a configurable percent threshold;
- spread is `<=2c`;
- score/clock still allows a meaningful comeback or continuation move;
- feed freshness, direct CLOB, inventory, worker, kill-switch, and risk gates are green.

Actions:

- on support retest after a downward impulse, buy a `5` share grid leg when event budget allows;
- target the next upper band or a `+4c` to `+8c` move, whichever is compatible with spread and depth;
- if holding existing inventory near resistance, sell only the grid sleeve and keep core hold if the basketball thesis remains valid;
- after a grid target fill, allow a `5` share rebuy only after fresh score/CLOB review confirms the lower band still holds;
- if a band breaks with score/player context against the thesis, stop/reduce rather than averaging down blindly.

Promotion path:

- first implement as shadow/replay output against captured games;
- then add it as a deterministic candidate trigger that can request a StrategyPlan revision;
- then expose its features to the short-horizon ML lane;
- only then allow live authority, and only under Janus StrategyPlan/live-worker/order-management gates.

Acceptance criteria before live authority:

- replay artifact for Spurs/Thunder showing all band detections, signals, hypothetical fills, and misses;
- tests for band creation, persistence, high-volatility classification, target selection, stop/rebuy behavior, and duplicate-order prevention;
- direct CLOB fillability proof using best bid/ask and depth, not delayed chart pixels;
- postgame comparison against operator manual trades, Janus trades, and missed windows;
- explicit handling of the minimum-order conflict, including when a `5` share leg is too chunky to express the intended sleeve structure.

## WNBA What Worked

- WNBA passive/shadow foundations were not confused with live authority.
- The May 20 live window forced the system to resolve a concrete WNBA target event and canonical mapping.
- WNBA event slug handling and canonical Janus IDs were improved enough to create a live-window plan artifact.
- The worker and switchboard path had enough shape to attempt a controlled live-promotion workflow.

## WNBA What Broke Or Remains Ambiguous

- The user-facing result is still "no WNBA trade" for practical confidence purposes.
- Local evidence is not enough by itself; `#62` needs direct fill/position/order reconciliation that proves the WNBA trade lifecycle end to end.
- The WNBA note still says WNBA is not live-trading cleared, and that remains correct until this reconciliation is done.
- The WNBA plan was still primarily a favorite/reference test, not a mature multi-sleeve basketball strategy.
- Any future WNBA promotion must prove feed freshness, CLOB fillability, StrategyPlan adoption, worker scope, risk budget, and post-call reconciliation in one bounded pass.

## Parallel Basketball Strategy Template V1

Use this only after direct CLOB, feed freshness, event inventory, worker, kill-switch, budget, and explicit approval gates are green.

### Entry

- Choose one side from current game context, not from stale pregame state.
- Target total initial size: `10` shares while validation remains small.
- Minimum fallback: if account/risk constraints force `5` shares, mark the trade as `single_sleeve_validation_only` and do not claim it proves the parallel strategy.
- Use limit-only order placement at current best ask or better.
- Skip when spread is above `2c`, top book is stale, or game feed is stale.

### Sleeve A - Grid/Scalp

- Size: `5` shares.
- Target: usually `+2c` to `+5c`, or about `8%` to `15%` from entry depending on price band and spread.
- After target fill: allow a rebuy of `5` shares only if direct CLOB, score/clock, and thesis still agree.
- Rebuy should be lower than the target fill and should not exceed the active per-event budget.

### Sleeve B - Core Hold

- Size: `5` shares.
- Purpose: keep exposure to the larger game thesis after the first scalp pays.
- Review triggers: quarter end, score gap break, lead change, price flip, player-status shock, unexplained CLOB move, or final two minutes.
- Exit rule: do not hold blindly to final; require a fresh late-game review.

### Sleeve C - Revision/Protection

- No automatic new exposure.
- Owns stop/reduce/hedge/rebuy proposals after triggers.
- Must write a StrategyPlan revision or exact blocker, not just a comment.

## Technical Fix Plan

### P0 - WNBA Fill-Confirmed Live Trade

`#62` is not complete until the system can show, for a WNBA event:

- current StrategyPlan adopted under canonical Janus event/market/outcome IDs;
- sports-live preflight ready with `blockers=[]`;
- worker running with WNBA event/account scope;
- direct CLOB orderbook and event-scoped inventory fresh before order;
- one controlled minimum-size order submitted only through Janus gates;
- post-call direct account/CLOB reconciliation showing open order, fill, or position state;
- if filled, a target/stop/review plan created immediately;
- if not filled, explicit cancel/replace/review policy recorded.

### P0 - NBA Parallel Sleeve Strategy

Implement a StrategyPlan template that can represent:

- `core_hold_size`;
- `grid_size`;
- `target_order_for_grid_size`;
- `rebuy_after_target_fill`;
- `uncovered_core_hold_requires_review`;
- per-sleeve target/stop/review triggers;
- per-event max shares and max notional.

### P1 - In-Game Builder Workflow

During live games, the active Codex thread should do one of two things:

- if live gates are green, keep StrategyPlan/live-worker evidence fresh and propose or apply bounded Janus-owned revisions;
- if live gates are blocked, add a section to this evaluation or the WNBA/NBA readiness docs with the exact blocker and next patch.

No live-game pass should spend the window on unrelated global-portfolio or crypto exploration while `#61/#62` lacks a fill-confirmed result or exact blocker.

## Prompt For Live-Window Work

Use this prompt in the live-game thread when you want Codex to build and monitor while games are running:

```text
We are inside an NBA/WNBA live-game window. Treat this as Janus covered-market live readiness, not global portfolio work.

Priority order:
1. Fix WNBA #62 first if WNBA does not have a direct fill-confirmed controlled minimum-size trade and post-call reconciliation.
2. For NBA #61, move from single-lot target management to a parallel sleeve StrategyPlan: initial 10-share validation when risk gates allow, 5-share grid/scalp target, 5-share core hold, and 5-share rebuy only after target fill plus fresh score/CLOB review.
3. If live execution gates are green, use only Janus StrategyPlan/live-worker/order-management gates. Do not place orders outside Janus.
4. If a gate blocks live action, patch the narrow blocker immediately if it is code/config/docs-owned; otherwise record the exact blocker once and update the NBA/WNBA evaluation docs.
5. Keep evidence fresh every pass: score/clock/period, direct CLOB book, event-scoped inventory, open orders, fills, positions, worker heartbeat, StrategyPlan version, and post-call reconciliation.

Operator approval for this prompt is bounded to controlled Janus live tests only:
- max 10 shares per event unless I explicitly approve more;
- max 5 shares per grid leg;
- limit orders only;
- no live order if direct CLOB/feed/worker/kill-switch/risk gates are not green;
- no global portfolio or crypto work while active NBA/WNBA live-readiness is blocked.

If no order can safely run, spend the pass extending the postgame/live evaluation docs with strategy findings and technical blockers so we can review before the next game.
```

## Issue #70 Structured Signal Performance Review - 2026-05-24 Pass

Status: bounded postgame signal-performance pass
Queue scope: active `#70` postgame review doc lock
Trading impact: read-only review; no order, cancel, replace, redeem, signing, broadcasting, or worker action performed by this pass.

### Reviewed Events

| Event | Completion status | Included in scoring? | Reason |
|---|---|---:|---|
| Spurs at Thunder, NBA, 2026-05-20 | Final: Thunder 122, Spurs 113 | Yes | Completed event with StrategyPlan, worker, orderbook, order/position, and postgame evidence. |
| Portland Fire at Indiana Fever, WNBA, 2026-05-20 | Final: Indiana 90, Portland 73 | Partial | Completed event, but WNBA position lifecycle and scoreboard/fill reconciliation remain ambiguous. |
| NBA/WNBA 2026-05-24 live-worker scope | Not completed during this pass | No | Ticks show pregame NBA and WNBA `scoreboard_freshness_required` blockers; defer to a later postgame pass. |

Primary evidence for this structured pass:

- `local/shared/artifacts/live-strategy-worker/2026-05-20/ticks.jsonl`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_nba_live_min_order_plan_20260520T235126Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_nba_post_order_monitor_plan_20260521T0040Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_spurs_target_live_20260521T0044Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue61_nba_post_target_monitor_plan_20260521T0045Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue62_wnba_live_canonical_plan_20260520T2359Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue62_wnba_post_order_monitor_plan_20260521T0002Z.json`
- `local/shared/artifacts/final-system-controller/live-window/2026-05-20/issue62_wnba_monitor_extension_20260521T0038Z.json`

### Fired Signals

| Event | Signal | Source | Evidence | Result | Scoring |
|---|---|---|---|---|---|
| NBA Spurs/Thunder | `issue61-sas-okc-spurs-post-q1-underdog-micro-candidate` buy | StrategyPlan/live worker | Fired at `2026-05-21T00:37Z`, intent price `0.31`, size `5`, Janus order submitted. | Position observed at Q1 `11:26`; target sell at `0.35` was submitted through approved order-management path. Direct event order/position state later cleared. | Positive execution-path proof; do not overfit because it was one 5-share sleeve. |
| NBA Spurs/Thunder | Spurs target sell | Operator/order-management reaction | `issue61_spurs_target_live_20260521T0044Z.json`, sell `5` at `0.35`, notional `$1.75`, response `submitted`. | Target protection existed, but it consumed the whole 5-share lot. | Positive for protective order path; negative for parallel-sleeve strategy learning. |
| WNBA Portland/Indiana | Indiana favorite buy | StrategyPlan/live worker | Fired at `2026-05-21T00:01Z`, intent price `0.95`, size `5`, order submitted; position observed at half. | Position then disappeared from event-scoped direct state while game was still at half; final favored Indiana. | Do not count as fully confidence-building until fill/position/order lifecycle is reconciled end to end. |

### Blocked Signals

| Event | Blocked signal | Reason codes | Evidence | Action |
|---|---|---|---|---|
| NBA Spurs/Thunder | Thunder favorite buy reference | `entry_disabled`, favorite price too expensive for first minimum-size test | Initial plan kept Thunder monitor-only at reference `0.69` ask. | Correct block; keep favorite-side signal as reference until aggregator can compare both sides. |
| NBA Spurs/Thunder | Fresh Spurs re-entry after initial lot | `position_management_only`, `no_new_entry`, `target_required`, `llm_revision_unavailable` | Post-order plan disabled fresh exposure after one 5-share buy; later ticks repeatedly show unavailable LLM revision. | Replace single-lot posture with explicit grid/core/rebuy sleeves under #67/#66. |
| WNBA Portland/Indiana | Portland underdog micro candidate | `entry_disabled`, `no_new_entry`, candidate not live-authorized | Canonical WNBA plan had Portland candidate ask `0.13`, size `8`, but disabled it. | Correct as live-safety posture; still useful for replay only. |
| WNBA Portland/Indiana | WNBA post-fill target/protection loop | `llm_revision_review_required`, `llm_revision_unavailable`, incomplete reconciliation | Worker detected WNBA position-management need, but no target/protection plan reached the same confidence as NBA. | Keep #62 open until WNBA direct account/CLOB/UI-visible history and Janus ledgers agree. |
| May 24 NBA/WNBA scope | All live-money intents | `scoreboard_freshness_required` | 2026-05-24 ticks had current StrategyPlans and fresh CLOB, but NBA was pregame and WNBA scoreboard age was unavailable. | Defer postgame scoring; treat as pregame/live readiness evidence only. |

### Missed Signals

| Event | Missed signal | Trigger that should have existed | Evidence | Recommended owner |
|---|---|---|---|---|
| NBA Spurs/Thunder | Q4 rebound entry | Close game, Q4, enough clock, underdog in `0.25`-`0.40`, spread `<=2c`, direct CLOB fresher than chart. | Around Q4 `10:35`, Spurs trailed `92-96` and executable range was about `31c`-`32c`. | #65 signal schema plus #66 aggregation/blocker artifact. |
| NBA Spurs/Thunder | Hot-player stretch optionality | High-impact player on court, not fouled out/benched, adverse run depresses side below live optionality band. | Manual low-band Spurs trade around `7c` to `12c` showed the pattern remained tradable late. | Basketball-intelligence signal producer under #65; replay test under #70. |
| NBA Spurs/Thunder | Resistance/support band retest | At least `3` persistent bands, downward impulse into support, spread `<=2c`, next band at least `4c` above. | Direct CLOB sample showed persistent Spurs mid bands from `0.34` down to `0.17`; rough isolated replay was positive before fees/slippage. | Shadow `resistance_band_volatility_v1` replay and tests under #70/#65. |
| WNBA Portland/Indiana | No scored missed live entry | Feed and reconciliation were not good enough to classify misses fairly. | WNBA live ticks had direct CLOB but scoreboard age unavailable and lifecycle ambiguity. | First fix #62/#64; then replay missed WNBA signals. |

### Feed Latency And Fillability

| Event | Score/PBP freshness | CLOB freshness | Fillability read | Risk interpretation |
|---|---|---|---|---|
| NBA Spurs/Thunder | Live ticks mostly usable, but scoreboard age reached stale values in postgame/long-tail worker ticks; 6 live outcome observations exceeded `45s`. | Direct top-book age stayed fresh, max about `1.83s`; spread max `2c`. | Good enough for 5-share buy/sell proof and band replay; should not depend on delayed chart pixels. | Increase confidence in direct CLOB fillability; require normalized snapshot freshness before automated authority. |
| WNBA Portland/Indiana | WNBA game state was present, but live outcome rows lacked usable scoreboard age metadata. | Direct top-book age was fresh, max about `0.43s`; spread was usually tight but reached `6c` in some rows. | CLOB could submit a high-priced favorite order, but lifecycle evidence is not sufficient for promotion. | Keep WNBA execution confidence low until #64/#62 close the feed/reconciliation gap. |
| May 24 active scope | NBA pregame; WNBA scoreboard unavailable. | Direct CLOB fresh and StrategyPlans current. | Fillability cannot be scored postgame yet. | Do not mark missed signals; record blockers only. |

### Sleeve PnL And Lifecycle Attribution

| Event | Sleeve | Entry | Exit/settlement | Gross PnL read | Confidence |
|---|---|---:|---:|---:|---|
| NBA Spurs/Thunder | Grid/scalp only | 5 Spurs at about `0.31` | Target sell 5 at `0.35` submitted and direct state later cleared | About `+$0.20` gross if the sell fill is accepted as complete | Medium; order and state evidence align, but ledger should expose exact fill timestamp and sleeve attribution. |
| NBA Spurs/Thunder | Core hold | Not present | Not present | `$0.00` because no core sleeve existed | High negative strategy lesson: single 5-share lot could not test the intended parallel behavior. |
| NBA Spurs/Thunder | Manual late micro-grid | Operator-observed buy around `0.07` | Operator-observed sell around `0.12` | About `+$0.68` to `+$0.71` gross, not Janus-ledger-attributed | Useful missed-signal evidence only; not Janus sleeve PnL. |
| WNBA Portland/Indiana | Indiana favorite validation | 5 at about `0.95` submitted; position briefly observed | Final Indiana win, but event-scoped position disappeared before a clean lifecycle record | Theoretical +`$0.25` to resolution if held; do not book as confirmed sleeve PnL | Low; reconciliation gap is the main result. |

### Strategy Confidence Changes

| Strategy family | Change | Rationale |
|---|---:|---|
| `price_stability_micro_grid` / NBA underdog live scalp | Increase slightly | Direct CLOB, Janus order path, 5-share entry, and target placement worked on one live event. |
| Single-lot target management | Decrease | It protected the position but prevented grid plus core-hold learning. |
| Parallel grid/core/rebuy sleeves | Increase as required architecture, not as proven edge | The postgame miss is structural: the system needed at least 10 shares or explicit single-sleeve blocker notation. |
| WNBA controlled minimum-size promotion | Decrease until reconciliation | Submitted/observed evidence exists, but confidence is limited by scoreboard age metadata and ambiguous position lifecycle. |
| `resistance_band_volatility_v1` | Promote to shadow/replay candidate | Direct CLOB band evidence and operator manual low-band example justify replay tests, not live authority. |
| LLM-dependent live revision | Decrease as liveness dependency | Repeated `llm_revision_unavailable` after live orders confirms deterministic/operator-safe revision paths are required. |

### Recommended Follow-Ups

| Type | Recommendation | Owner |
|---|---|---|
| Issue | Keep #70 open until this structured table is generated by code or fixture-backed replay, not only maintained by hand. | #70 |
| Issue | Add a WNBA fill-confirmed reconciliation subtask or checklist under #62: submitted order, direct trade, position, open order, target/stop, and final settlement must agree. | #62 |
| Config | Add explicit `q4_rebound_band`, `hot_player_stretch_optional`, and `resistance_band_volatility_v1` signal reason codes to the signal schema and replay fixtures. | #65/#70 |
| Runtime | Add deterministic target/rebuy revision fallback when LLM revision is unavailable after a live fill. | #66/#69 |
| Sleeve | Make `single_sleeve_validation_only` an explicit blocker/status when risk cannot support 10 shares for grid plus core. | #67 |
| Docs | Convert this table shape into the durable postgame artifact schema expected by #70 acceptance criteria. | #70 docs/test slice |
| Obsidian | Update an existing Spurs/Thunder game review or trading concept note rather than creating a duplicate; link the Q4 rebound, hot-player optionality, and resistance-band lessons to #70/#65/#66. | Obsidian builder under curation policy |
