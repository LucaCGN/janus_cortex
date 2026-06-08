# Crypto Options Strategy Testing Loop Status

Date: 2026-06-03

This document is the living strategy-test status file for the isolated `crypto_options_app` runtime. Update it after every supervised run with new findings, tested strategies, blockers, and next steps.

## Current Objective

Move from structural validation into bounded live strategy testing while preserving system-integrity controls:

- Live activity only through the supervised runner.
- No manual order placement, cancellation, signing, broadcasting, redeeming, routing, recommendation, or authorization from chat.
- Global/API live flags remain false; live flags are scoped only to the supervised child process.
- Every BUY path must retain lifecycle coverage and reconciliation evidence.
- Strategy underperformance is noted during validation; mechanical failures stop and trigger isolated patch/test.

## Current Five-Lane Bundle

Run id prefix: `signal-live-baseline5`

Budget model:

- Per-lane budget cap: `$20`.
- Bundle hard cap: `$100`.
- Minimal sizing remains enabled.
- Loss-streak lane stop is enabled.

Stop gate for each lane:

- Current settled loss streak `>= 3`.
- At least `3` settled positions.
- Realized PnL `< $0`.
- Win rate `< 50%`.
- When triggered, only that lane is blocked; other lanes continue.

The current five lanes are:

| Lane | Strategy | Reason for inclusion |
| --- | --- | --- |
| 1 | `profile_hedge_scalping_v1` | Current best live performer and positive-control lane. |
| 2 | `event_context_outcome_v1` | Tests non-profile event context and target/price mechanics. |
| 3 | `indicator_confirmed_outcome_v1` | Tests indicator/context confirmation in the live strategy path. |
| 4 | `s_tier_outcome_hold_to_settlement_v1` | Pure S-tier outcome-prediction hold comparator. |
| 5 | `a_fallback_outcome_probe_v1` | Tests A-grade fallback only when S-tier outcome source is absent. |

## Prior Performance Context

Partial settled strategy aggregates before this bundle:

| Strategy | Settled record | PnL | Current read |
| --- | ---: | ---: | --- |
| `profile_hedge_scalping_v1` | 12W / 6L, 66.7% | +$11.60 | Best current candidate; needs longer validation. |
| `hedger_ratio_replication_v1` | 5W / 9L, 35.7% | +$5.59 | Structurally works, but latest high-volume run was poor; needs redesign before promotion. |
| `s_tier_outcome_hold_to_settlement_v1` | 2W / 4L, 33.3% | +$1.50 | Weak but kept as a profile baseline comparator. |
| `volatility_spread_scalping_probe_v1` | 1W / 1L, 50.0% | -$0.55 | Sparse and not useful for the first longer bundle. |
| `grid_buyer_band_rebound_v1` | 6W / 6L, 50.0% | -$4.35 | Better treated as a signal component than a standalone lane for now. |
| `s_tier_outcome_consensus_cashout_v1` | 0W / 5L, 0.0% | -$5.22 | Rejected for current testing loop. |

Important prior runs:

- `signal-live-highvolume-hedge-grid-scalp-20260603T134831Z`: 8 cycles, 13 filled BUYs, exact CLOB audit, DB lifecycle coverage, spend `$14.15`, partial settlement 3W / 8L, realized `-$8.27`. Structurally clean, performance poor.
- `signal-live-best3-30budget-10events-20260603T140955Z`: reconstructed final artifact, exact CLOB audit, DB lifecycle coverage, 2W / 2L, realized `-$1.64`; exited early after 2 events due stale duplicate automation/active-state issue that has since been cleaned up.

## Excluded From This Bundle

| Strategy | Why excluded now | Next use |
| --- | --- | --- |
| `hedger_ratio_replication_v1` | Needs higher-budget or redesigned profile-position translation; latest high-volume signal was poor. | Reintroduce after position-ratio planner v2 or as signal input. |
| `grid_buyer_band_rebound_v1` | Standalone negative; useful as band/rebound signal component. | Use inside hybrid profile/context strategies. |
| `s_tier_outcome_consensus_cashout_v1` | Strongly negative in current evidence. | Replace with redesigned cashout mechanics before retest. |
| `volatility_spread_scalping_probe_v1` | Sparse and slightly negative; not enough flow for baseline run. | Keep as volatility/liquidity metadata probe. |
| `buying_ahead_pre_event_v1` | Specialized pre-event behavior; not required for first five-lane baseline. | Test after baseline if buying-ahead profile flow is active. |

## Observability Requirements

The FastAPI app exposes a read-only strategy dashboard:

- HTML: `/v1/crypto-options-app/dashboard`
- JSON state: `/v1/crypto-options-app/dashboard/state`

The dashboard must show:

- Active run id and stage.
- Running/validated/blocked status.
- Estimated spend.
- PnL and win rate when settlement is available.
- Filled trades, cycles, average win, average loss.
- Per-lane live/blocked/waiting state.
- Current blockers and audit status.

The dashboard is read-only and must never authorize trading.

Status semantics during an active supervised run:

- `live`: at least one filled live structural BUY has been recorded for the lane.
- `probing`: the lane has submitted structurally valid orders that did not fill.
- `waiting`: the lane is healthy but currently has no eligible signal, such as `no_indicator_confirmed_outcome_signal`.
- `gated`: the lane is intentionally suppressed by a valid routing rule, such as A-fallback waiting while S-tier sources exist.
- `blocked`: reserved for mechanical/systematic blockers such as missing lifecycle coverage, reconciliation mismatch, malformed rows, credentials failure, or strategy-spec violation.
- `active_run_pending_final_audit`: final CLOB audit is expected after run completion; during the run, the dashboard may show the count of recorded filled BUY rows but must not claim a final exchange match until the audit artifact exists.

## Next Run

Run the five-lane bundle for up to 10 cycles using the supervised signal-live runner:

- `--strategy-ids profile_hedge_scalping_v1 event_context_outcome_v1 indicator_confirmed_outcome_v1 s_tier_outcome_hold_to_settlement_v1 a_fallback_outcome_probe_v1`
- `--max-event-cycles 10`
- `--max-cycles-per-event-slug 1`
- `--total-budget-cap-usd 100`
- `--per-strategy-budget-cap-usd 20`
- `--enable-lane-stop-gates`
- `--lane-loss-streak-limit 3`
- `--lane-stop-min-settled 3`
- `--lane-stop-max-win-rate 0.5`
- `--lane-stop-max-pnl-usd 0`

Before start:

- `/v1/crypto-options-app/health` must not report CLOB maintenance/trading unavailable.
- No duplicate supervised runner should be active.
- Global/API live flags must remain false.

After run:

- Audit exchange order ids against CLOB exactly.
- Verify canonical DB lifecycle rows.
- Reconcile settlements when events resolve.
- Update this document with per-lane performance and the next candidate bundle.

## P2 Price-Path And Rotation9 Hardening Update

Date: 2026-06-04

Rotation9 should remain paused until price-path replay and managed exits are ready.

Completed in the P2 hardening pass:

- Added official Polymarket data-source map in `19_polymarket_official_data_source_map.md`.
- Added `polymarket_event_path_stats` to the centralized DB and backfilled completed captured events.
- Added read-only REST live-activity capture scaffolding and tests.
- Exposed latest completed event-path stats through `/health`.
- Patched managed profile-derived entries so Rotation9-style high-conflict, near-extreme, too-late entries remain observation-only.

Current data finding:

- Up/Down quote paths and visible order-book pressure are being captured.
- Completed path stats now include elapsed price checkpoints, pre-event checkpoints, volatility, level crossings, bucket counts, rebound touches, pair-sum drift, pair-depth pressure, high/low swing distance, rolling 30s/60s high-low range, and latency.
- `polymarket_trade_prints` remains empty from the sampled REST live-activity endpoint, so matched trade-flow capture must move to official websocket `activity.trades` / `activity.orders_matched` before replay can model executed market flow.

Current Rotation9 blocker interpretation:

- `submit_error_no_fill` was not a generic executor failure. The triggering row was a managed profile/scalp lane converting high-conflict profile pressure into a near-98c executable entry with poor managed-exit geometry.
- The managed-entry gate is patched and tested, but Rotation9 still needs websocket trade capture and replay-safe managed exit simulation before another supervised live run.

## Latest Five-Lane Baseline Result

Completed run:

- Run id: `signal-live-baseline5-20lane-10cycles-20260603T184442Z`
- Window: `2026-06-03T18:44:42Z` to `2026-06-03T19:03:07Z`
- Completed event cycles: `10 / 10`
- Estimated spend: `$2.399994`
- Lane stop gates triggered: none
- Manual orders avoided: yes
- Final CLOB audit: `matched` with `2 / 2` exact BUY matches and `0` unmatched recorded/exchange BUYs
- Settlement status: `settled` for both filled positions, total realized PnL `+$0.657991`
- Mechanical blockers: none in this bundle; no restart or patch was required

Per-lane bundle outcome:

| Lane | Strategy | Run outcome | Spend | Settled result | Next bundle decision |
| --- | --- | --- | ---: | ---: | --- |
| 1 | `profile_hedge_scalping_v1` | 1 filled, 9 no-signal blocks | `$1.229997` | `1W / 0L`, `+$0.288518` | Keep |
| 2 | `event_context_outcome_v1` | 1 filled, 9 unfilled probes | `$1.169997` | `1W / 0L`, `+$0.369473` | Keep |
| 3 | `indicator_confirmed_outcome_v1` | 1 unfilled probe, 9 no-signal blocks | `$0.000000` | none | Replace |
| 4 | `s_tier_outcome_hold_to_settlement_v1` | 1 unfilled probe, 9 no-signal blocks | `$0.000000` | none | Replace |
| 5 | `a_fallback_outcome_probe_v1` | 10 gated/blocked rows, no live attempts | `$0.000000` | none | Replace |

Detailed notes:

- `profile_hedge_scalping_v1` remained the positive-control lane. It only produced one eligible signal in this 10-cycle window, but that signal filled, reconciled exactly to CLOB, and settled positive.
- `event_context_outcome_v1` generated the most activity. Nine additional probes remained unfilled but reconciled cleanly, so this lane stays useful for controlled live traffic without introducing mechanical risk.
- `indicator_confirmed_outcome_v1` and `s_tier_outcome_hold_to_settlement_v1` were structurally clean in the bundle but mostly inactive. This is underperformance / low activation, not a system failure, so no patch is justified during validation. They should be replaced in the next bundle rather than blocking the current system state.
- `a_fallback_outcome_probe_v1` added no execution evidence in this bundle because fallback conditions rarely became primary. It should be replaced in the next comparison bundle unless the candidate set specifically targets missing S-tier source conditions.

Canonical lifecycle and reconciliation summary:

- `event_context_outcome_v1` retained the full `candidate -> intent -> order -> fill -> position -> exit_plan -> settlement` path for exchange order id `0x766abbf9be88855378148e5344bb57ede221041c4d92e2ae1f30c5652d3a8bb0`.
- `profile_hedge_scalping_v1` retained the same full lifecycle path for exchange order id `0x26b66952970052c100ef450556668d907b7182e1ae54e7cf65dddc299429e954`.
- Per-event run reports were written throughout the bundle, and the final audit wrapper reported `matched` with no reconciliation blockers.

Next candidate bundle recommendation:

- Keep `profile_hedge_scalping_v1`.
- Keep `event_context_outcome_v1`.
- Replace `indicator_confirmed_outcome_v1`.
- Replace `s_tier_outcome_hold_to_settlement_v1`.
- Replace `a_fallback_outcome_probe_v1`.

## Latest Rotation2 Result

Completed run:

- Run id: `signal-live-rotation2-hedge-grid-scalp-20lane-10cycles-20260603T192340Z`
- Window: `2026-06-03T19:23:40Z` to `2026-06-03T19:39:16Z`
- Completed event cycles: `10 / 10`
- Estimated spend: `$7.999998`
- Lane stop gates triggered: none
- Manual orders avoided: yes
- Final CLOB audit: `matched` with `6 / 6` exact BUY matches and `0` unmatched recorded/exchange BUYs
- Settlement status: `5` settled positions, `1` unresolved position, total realized PnL `-$0.999999`
- Mechanical blockers: none in this bundle; no strategy-spec, lifecycle, or reconciliation failure was found

Per-lane rotation2 outcome:

| Lane | Strategy | Run outcome | Spend | Settled result | Current decision |
| --- | --- | --- | ---: | ---: | --- |
| 1 | `profile_hedge_scalping_v1` | 1 filled, 1 unfilled, 8 no-signal waits | `$1.98` | `1W / 0L`, `+$0.02` | Keep as positive-control, but activation remains sparse |
| 2 | `event_context_outcome_v1` | 2 filled, 8 unfilled probes | `$1.999998` | `0W / 1L`, `-$0.999999`, 1 unresolved | Keep only as traffic/control lane; performance needs longer evidence |
| 3 | `hedger_ratio_replication_v1` | 2 filled, 8 no-signal waits | `$3.02` | `2W / 0L`, `+$0.98` | Promote to next bundle candidate |
| 4 | `grid_buyer_band_rebound_v1` | 1 filled, 1 unfilled, 8 no-signal waits | `$1.00` | `0W / 1L`, `-$1.00` | Replace or redesign before another standalone run |
| 5 | `volatility_spread_scalping_probe_v1` | 10 no-signal waits | `$0.00` | none | Replace; no live activation evidence |

Observability note:

- The dashboard correctly reported `validated` and `matched`.
- `/health` was still too large for some PowerShell parsing paths because it embedded full order-audit match rows and settlement rows. This was patched so health now summarizes audit and settlement counts while preserving detailed artifacts on disk.

Next candidate bundle recommendation after rotation2:

- Keep `profile_hedge_scalping_v1`.
- Keep `hedger_ratio_replication_v1`.
- Keep `event_context_outcome_v1` only as a controlled traffic lane unless a better event-context variant is ready.
- Replace `grid_buyer_band_rebound_v1`.
- Replace `volatility_spread_scalping_probe_v1`.
- Candidate replacements should prioritize higher-activation variants, especially profile/event hybrids that use hedge proportion and profile aggregate signals without relying on sparse standalone indicator gates.

## Latest Rotation2 Result

Completed run:

- Run id: `signal-live-rotation2-hedge-grid-scalp-20lane-10cycles-20260603T192340Z`
- Stage: `rotation2_hedge_grid_scalp_20_per_lane_10cycle_strategy_test`
- Window: `2026-06-03T19:23:40Z` to `2026-06-03T19:39:16Z`
- Completed event cycles: `10 / 10`
- Estimated spend: `$7.999998`
- Lane stop gates triggered: none
- Manual orders avoided: yes
- Final CLOB audit: `matched` with `6 / 6` exact BUY matches and `0` unmatched recorded/exchange BUYs
- Canonical DB lifecycle: filled BUY rows retained `order -> fill -> position` coverage for all 6 matched exchange order ids; `exit_plans` exist for 5 currently materialized positions in settlement reporting
- Settlement status: `partial`; `5` settled positions and `1` unresolved position, total realized PnL `-$0.999999`
- Mechanical blockers: none in this bundle; no restart or patch was required

Per-lane rotation2 outcome:

| Lane | Strategy | Run outcome | Spend | Settled / active result | Decision |
| --- | --- | --- | ---: | ---: | --- |
| 1 | `profile_hedge_scalping_v1` | 1 filled, 1 unfilled probe, 8 no-signal blocks | `$1.98` | `1W / 0L`, `+$0.02` | Keep |
| 2 | `event_context_outcome_v1` | 2 filled, 8 unfilled probes | `$1.999998` | `0W / 1L` settled, `-$0.999999` realized, `1` unresolved open position | Keep |
| 3 | `hedger_ratio_replication_v1` | 2 filled, 8 no-signal blocks | `$3.02` | `2W / 0L`, `+$0.98` | Keep |
| 4 | `grid_buyer_band_rebound_v1` | 1 filled, 1 unfilled probe, 8 no-signal blocks | `$1.00` | `0W / 1L`, `-$1.00` | Redesign |
| 5 | `volatility_spread_scalping_probe_v1` | 10 no-signal waits, no live attempts | `$0.00` | none | Replace |

Detailed notes:

- `profile_hedge_scalping_v1` stayed structurally clean, produced one exact-match fill, and settled slightly positive. It remains a stable control lane even with sparse activation.
- `event_context_outcome_v1` continued to generate the most traffic and preserved exact reconciliation across both fills, but its currently settled contribution is negative and one later fill remains unresolved. Keep it in the comparison set, but treat it as high-activity / mixed-quality flow until the unresolved leg settles.
- `hedger_ratio_replication_v1` was the best performer in this rotation with two settled wins and exact exchange/DB reconciliation. This run offset the weaker prior high-volume read enough to keep it in the next controlled bundle.
- `grid_buyer_band_rebound_v1` produced clean mechanics but another negative settled outcome and limited activation. It should move out of the standalone lane set and be redesigned as a signal component or tighter hybrid trigger.
- `volatility_spread_scalping_probe_v1` never produced a tradeable probe in the full 10-cycle window. That is a utilization problem, not a system failure, so replacement is preferred over patching during validation.

Exact match and settlement summary:

- Exact CLOB BUY matches: `0xe0edd1d2d6e0b4db65e15b511ae08bd12f6cc2a28a788df3b38df9a34d211ba9`, `0x66aa30226f35b214406fa417b0c480a3a59bd22136a0be97c1f0f996a6963864`, `0x31bdc8e7004a2d5ac228b991011e23215f29576180f4da17ed74ba22dd40617d`, `0x1cff979a5d3e03b34cb5e6162cbbc32beccff5eca0e9993457d621ecbdc955bd`, `0x57e2bb23909e25a07c97554e92f70bd4cb8c81cf3075f193993c21a9b6c84c81`, `0xeba308c6f68882c53b559c5bdc646ebf42e26c2942ba59eb617ec72e757ce2d7`.
- Settled winners: `profile_hedge_scalping_v1` `+$0.02`, `hedger_ratio_replication_v1` `+$0.98` across two positions.
- Settled losers: `event_context_outcome_v1` `-$0.999999`, `grid_buyer_band_rebound_v1` `-$1.00`.
- Unresolved position: `event_context_outcome_v1` order `0x57e2bb23909e25a07c97554e92f70bd4cb8c81cf3075f193993c21a9b6c84c81`, active cost `~$1.00`.

Next bundle recommendation after rotation2:

- Keep `profile_hedge_scalping_v1`.
- Keep `event_context_outcome_v1`.
- Keep `hedger_ratio_replication_v1`.
- Redesign `grid_buyer_band_rebound_v1`.
- Replace `volatility_spread_scalping_probe_v1`.
- Do not restart rotation2; wait for the unresolved `event_context_outcome_v1` leg to settle, then choose rotation3 replacements for the redesign/replace slots.

## Rotation3 V2 Promotion Plan

User direction after rotation2: every kept lane should move to an explicit `v2` variant because the v1 lanes worked structurally but produced too little live volume.

Implemented v2 lanes:

| V1 lane kept | V2 lane | Why v2 exists | Volume adjustment | Safety preserved |
| --- | --- | --- | --- | --- |
| `hedger_ratio_replication_v1` | `hedger_ratio_replication_v2` | Best rotation2 lane, but only 2 fills in 10 cycles. | Prefer S/S+/S++ hedger/grid rows; if none exist, allow A-grade hedger/grid fallback; if the profile row has signal quality but missing/non-executable quote fields, use the verified same-event/same-side event-context quote. | Same supervised executor, lifecycle coverage, CLOB audit, lane stop gates, and FAK structural execution. |
| `profile_hedge_scalping_v1` | `profile_hedge_scalping_v2` | Structurally clean positive-control lane, but sparse. | Broaden eligible high-grade profile styles to include outcome-predictor flow as a profile-driven scalp source; allow A fallback only after S-tier exhaustion; use same-event/same-side event-context quote fallback. | Same supervised executor, lifecycle coverage, CLOB audit, lane stop gates, and FAK structural execution. |
| `event_context_outcome_v1` | `event_context_outcome_v2` | Best traffic/control lane, but too many FOK unfilled probes. | Preserve the event-context signal path while switching the structural execution style to FAK with slightly wider slippage tolerance. | Same event-context source, same risk/reconciliation/lifecycle gates, no profile-based signal bypass. |

Focused tests run:

- `python -m pytest tests/crypto_options_app/test_strategy_manager_pytest.py tests/crypto_options_app/test_signal_live_runner_pytest.py -q`
- Result: `27 passed`

Rotation3 bundle to validate:

- `hedger_ratio_replication_v2`
- `profile_hedge_scalping_v2`
- `event_context_outcome_v2`

Recommended first run parameters:

- `--max-event-cycles 10`
- `--max-cycles-per-event-slug 1`
- `--total-budget-cap-usd 60`
- `--per-strategy-budget-cap-usd 20`
- lane stop gates enabled with the current `3` settled-loss-streak / negative-PnL / win-rate-below-50% quality stop.

Rotation3 objective:

- Validate that the v2 volume changes produce meaningfully more fills than v1 without introducing reconciliation, lifecycle, duplicate cadence, or health regressions.
- Treat strategy underperformance as performance evidence only; patch only on systematic/mechanical faults.

## Rotation3 First Attempt Blocker And Patch

First rotation3 v2 attempt:

- Run id: `signal-live-rotation3-v2-kept-20lane-10cycles-20260603T201819Z`
- Result: stopped on first event with `0` spend.
- Mechanical blocker: `polymarket_status_unavailable` caused executor-side `exchange_status_not_operational`.
- External observation: local DNS could not resolve `status.polymarket.com`, while the CLOB quote path still produced a fresh verified event-context candidate.
- Profile-volume observation: the profile monitor did find profile signals, but they were for an expiring event while the executable event-context candidate had already advanced to the next event. The profile lanes correctly refused to attach those profile signals to a different event.

Patch applied:

- `crypto_options_app/trading/polymarket_supervised_executor.py`
  - Added `allow_status_unavailable_with_verified_market`.
  - This allows the supervised runner to proceed only when the sole status blocker is `polymarket_status_unavailable` and the strategy has a fresh verified CLOB market quote.
  - It still blocks explicit maintenance, incidents, or exchange-not-operational status.
- `crypto_options_app/workers/signal_live_runner.py`
  - Enables the verified-market status fallback only for supervised signal-live runs.
  - Expands profile monitor max time remaining from `300s` to `900s` to capture ahead-of-time profile signals instead of only current/expiring events.

Focused tests run after patch:

- `python -m pytest tests/crypto_options_app/test_polymarket_supervised_executor_pytest.py tests/crypto_options_app/test_signal_live_runner_pytest.py tests/crypto_options_app/test_polymarket_status_pytest.py -q`
- Result: `39 passed`

Patched retry:

- Run id: `signal-live-rotation3-v2-kept-patched-20lane-10cycles-20260603T203449Z`
- Early progress: `4` event cycles, `$5.229998` estimated spend, no runtime blockers.
- Early spend by lane: `event_context_outcome_v2=$4.179999`, `profile_hedge_scalping_v2=$1.049999`, `hedger_ratio_replication_v2=$0.00`.
- Current read: the event-context lane volume is fixed; the profile-scalping v2 lane has some activation; the hedger v2 lane remains sparse and should be reviewed after the run completes.

## Latest Rotation3 V2 Result

Completed attempt:

- Run id: `signal-live-rotation3-v2-kept-20lane-10cycles-20260603T201819Z`
- Stage: `rotation3_v2_kept_20_per_lane_10cycle_strategy_test`
- Window: `2026-06-03T20:18:19Z` to `2026-06-03T20:21:47Z`
- Completed event cycles: `1 / 10`
- Estimated spend: `$0.00`
- Disabled lanes: none
- Manual orders avoided: yes
- Final CLOB audit: `matched` with `0 / 0` exact BUY matches and `0` unmatched recorded/exchange BUYs
- Canonical DB lifecycle: no new `candidate -> intent -> order -> fill -> position -> exit_plan -> settlement` path was created because no BUY filled
- Settlement status: no new positions, realized PnL `$0.00`
- Mechanical blocker: yes; the run stopped on breaker after `event_context_outcome_v2` hit `exchange_status_not_operational` plus `polymarket_status_unavailable` during runtime, even though the health-after artifact fell back to the status page and still showed `CLOB API` operational

Per-lane attempt outcome:

| Lane | Strategy | Run outcome | Spend | Current decision |
| --- | --- | --- | ---: | --- |
| 1 | `hedger_ratio_replication_v2` | 1 waiting/blocked row, `no_hedge_proportion_v2_volume_probe_signal` | `$0.00` | No new evidence; keep pending rerun |
| 2 | `profile_hedge_scalping_v2` | 1 waiting/blocked row, `no_profile_hedge_scalping_v2_volume_probe_signal` | `$0.00` | No new evidence; keep pending rerun |
| 3 | `event_context_outcome_v2` | 1 blocked runtime row, no fill, breaker stop | `$0.00` | Blocked by mechanical status/runtime issue; do not judge strategy yet |

Detailed notes:

- This attempt does not provide usable volume validation evidence. The bundle stopped after the first event because the supervised child treated Polymarket status as unavailable/non-operational at runtime.
- The post-run health snapshot still showed `orders_allowed=false`, `live_trading_authorized=false`, and global/API live flags false in the parent environment, so the safety model held.
- The same health-after artifact recorded `json_api_error=URLError:<urlopen error [Errno 11001] getaddrinfo failed>` and then used `polymarket_status_page_html_fallback`, which reported the Polymarket page operational and `CLOB API` trading available. That inconsistency should be treated as a mechanical/runtime health-status problem, not strategy underperformance.
- Because no live BUY filled, there is no new settlement/PnL evidence and no lane-stop gate input from this run.

Next action before another rotation3 v2 start:

- Keep `hedger_ratio_replication_v2`, `profile_hedge_scalping_v2`, and `event_context_outcome_v2` in the validation bundle.
- Do not replace or tune any lane from this attempt because the stop condition was mechanical, not performance-driven.
- Clear the stale active-process marker after audit, then investigate or harden the runtime status-provider path so transient Polymarket status JSON/DNS failures do not falsely trip `exchange_status_not_operational` when fallback health still shows tradable CLOB status.
- Restart rotation3 v2 only after that health/runtime consistency issue is understood or intentionally accepted.

## Rotation3 V2 Patched Final Result

Completed patched retry:

- Run id: `signal-live-rotation3-v2-kept-patched-20lane-10cycles-20260603T203449Z`
- Stage: `rotation3_v2_kept_patched_20_per_lane_10cycle_strategy_test`
- Completed event cycles: `10 / 10`
- Estimated spend: `$11.469998`
- Final run artifact: `local/shared/artifacts/crypto-options-app/live-validation/signal-live-rotation3-v2-kept-patched-20lane-10cycles-20260603T203449Z.json`
- Order audit: `matched`
- Exact recorded/exchange BUY matches: `11 / 11`
- Weak matches: `0`
- Unmatched recorded BUYs: `0`
- Unmatched exchange BUYs: `0`
- Unexpected exchange SELLs in validation scope: `0`
- Manual orders avoided: yes

Settlement/PnL snapshot:

- Settlement status: `partial`
- Open blocker: `some_events_unresolved`
- Filled positions: `11`
- Settled positions: `4`
- Unresolved positions: `7`
- Settled result so far: `1W / 3L`
- Settled win rate so far: `25.0%`
- Realized PnL so far: `-$2.109998`

Per-lane final outcome:

| Lane | Strategy | Run outcome | Spend | Settled / active result | Decision |
| --- | --- | --- | ---: | ---: | --- |
| 1 | `hedger_ratio_replication_v2` | 1 unfilled probe, 9 no-signal blocks | `$0.00` filled | no settled positions | Pause/redesign before another live bundle |
| 2 | `profile_hedge_scalping_v2` | 1 filled, 9 no-signal blocks | `$1.049999` | `0W / 1L`, `-$1.049999` | Pause until profile signal alignment improves |
| 3 | `event_context_outcome_v2` | 10 filled | `$10.419999` | `1W / 2L`, `-$1.059999` realized, `7` unresolved | Keep only as a traffic/control lane or redesign with confirmation |

Interpretation:

- The patched executor/status path is mechanically usable: the run completed all 10 cycles, preserved scoped live flags, produced exact CLOB/order matches, and recorded DB lifecycle evidence.
- The volume problem was solved only for `event_context_outcome_v2`. It filled almost every cycle but is not directionally reliable as a standalone lane on the currently settled sample.
- `profile_hedge_scalping_v2` and `hedger_ratio_replication_v2` did not achieve the intended volume increase. The profile/hedge signal pipeline is still too sparse or misaligned with executable event windows.
- `hedger_ratio_replication_v2` still does not validate the original Bonereaper-style aggregate position replication concept. It needs a real aggregate position/ratio planner and cannot be treated as tested by sparse row selection.
- The dashboard/API observability path should remain under watch: the dashboard rendered after the run, but `/health` timed out once in controller inspection. That is an observability reliability issue, not an exchange-order integrity issue.

Next decision:

- Do not rerun `rotation3_v2_kept_patched` unchanged.
- Block the current five-lane/rotation3 automation from starting another live child until a rotation4 design is selected.
- Rotation4 should focus on fixing signal generation before increasing budget:
  - profile lanes: event alignment, ahead-of-event profile activity, and quote attachment.
  - hedger lanes: aggregate Up/Down inventory planner instead of sparse candidate selection.
  - event-context lane: require profile/indicator confirmation or use only as a control/traffic lane.

## Rotation4 Profile-Pressure Carry-Forward Plan

Reason for rotation4:

- Rotation3 V2 proved executor/order/DB/audit mechanics, but profile-driven volume stayed too low.
- Artifact review showed profile monitor outputs had strong S/S++ profile activity, but the activity often lagged the current executable event by one or more 5-minute windows.
- Rerunning v2 unchanged would spend mostly on the standalone event-context lane again.

Implemented rotation4 lanes:

| Lane | Strategy | Purpose | Signal change | Safety/attribution |
| --- | --- | --- | --- | --- |
| 1 | `hedger_ratio_replication_v3` | Test whether recent hedger/grid pressure can produce hedge-proportion volume. | Uses recent same-symbol/same-outcome hedger/grid profile pressure and attaches it to the current executable event quote. | `signal_context.cross_event_profile_pressure=true` when the source profile row is from a prior event. |
| 2 | `profile_hedge_scalping_v3` | Test high-volume profile-pressure scalping flow. | Uses recent same-symbol/same-outcome profile pressure from hedger/grid/scalping/outcome styles. | Same lifecycle/reconciliation gates as v2; cross-event source is explicit in attribution. |
| 3 | `event_context_profile_confirmed_v1` | Reduce random event-context directionality. | Executes current event-context quote only when recent same-symbol/same-outcome profile pressure confirms it. | Keeps event-context volume but requires profile confirmation. |

Offline replay against `signal-live-rotation3-v2-kept-patched-20lane-10cycles-20260603T203449Z` monitor artifacts:

- Events checked: `3`, `4`, `8`, `10`
- All three rotation4 lanes produced verified candidates on those checked events.
- Grade pool was `S_or_better`.
- Each checked candidate used `cross_event_profile_pressure=true`, confirming that v2 sparsity was caused by event-window lag rather than absence of profile signals.

Rotation4 validation bundle:

- `hedger_ratio_replication_v3`
- `profile_hedge_scalping_v3`
- `event_context_profile_confirmed_v1`

Recommended run parameters:

- `--max-event-cycles 10`
- `--max-cycles-per-event-slug 1`
- `--total-budget-cap-usd 60`
- `--per-strategy-budget-cap-usd 20`
- `--active-profile-pool-limit 80`
- `--max-workers 16`
- lane stop gates enabled with the current `3` settled-loss-streak / negative-PnL / win-rate-below-50% quality stop.

Rotation4 objective:

- Validate whether profile-pressure carry-forward materially increases volume for profile/hedge lanes.
- Treat performance as early evidence only; patch only on systematic/mechanical faults.
- If Rotation4 still underperforms directionally, do not widen execution. Move to deeper profile stream architecture and aggregate position reconstruction before further budget increases.

## Rotation4 Profile-Pressure Final Result

Completed run:

- Run id: `signal-live-rotation4-profile-pressure-20lane-10cycles-20260603T210821Z`
- Stage: `rotation4_profile_pressure_carry_forward_20_per_lane_10cycle_strategy_test`
- Completed event cycles: `10 / 10`
- Estimated spend: `$15.499983`
- Final run artifact: `local/shared/artifacts/crypto-options-app/live-validation/signal-live-rotation4-profile-pressure-20lane-10cycles-20260603T210821Z.json`
- Order audit: `matched`
- Exact recorded/exchange BUY matches: `14 / 14`
- Weak matches: `0`
- Unmatched recorded BUYs: `0`
- Unmatched exchange BUYs: `0`
- Unexpected exchange SELLs in validation scope: `0`
- Manual orders avoided: yes

Per-lane execution:

| Lane | Strategy | Filled | Unfilled | Blocked | Spend | Lifecycle coverage |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | `hedger_ratio_replication_v3` | `7` | `3` | `0` | `$7.609989` | all filled BUYs covered |
| 2 | `profile_hedge_scalping_v3` | `4` | `2` | `4` | `$4.749995` | all filled BUYs covered |
| 3 | `event_context_profile_confirmed_v1` | `3` | `3` | `4` | `$3.139999` | all filled BUYs covered |

Settlement/PnL snapshot:

- Settlement status at final audit time: unresolved.
- Filled positions: `14`
- Settled positions at final audit time: `0`
- Unresolved positions at final audit time: `14`
- Realized PnL at final audit time: `$0.00`
- Active cost at final audit time: `$15.499983`

Settlement refresh:

- Refreshed at: `2026-06-03T21:22:09Z`
- Settlement status: `partial`
- Settled positions: `5`
- Unresolved positions: `9`
- Realized PnL: `+$0.891312`
- Win rate on currently settled rows: `60.0%`

Per-lane refreshed settlement:

| Lane | Strategy | Settled | Win rate | Realized PnL | Active/unresolved cost |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | `hedger_ratio_replication_v3` | `3` | `33.3%` | `-$1.388688` | `$4.499991` |
| 2 | `profile_hedge_scalping_v3` | `1` | `100.0%` | `+$1.240000` | `$3.509995` |
| 3 | `event_context_profile_confirmed_v1` | `1` | `100.0%` | `+$1.040000` | `$2.099999` |

Observability note:

- Runtime blockers were empty and the final CLOB audit matched exactly.
- Dashboard blocker warnings seen during the active run were quote-source diagnostics from blocked non-executed rows, not mechanical blockers for filled BUYs.
- Dashboard display should suppress those non-critical missing quote diagnostics once a lane has live fills, while preserving them if a lane never becomes executable.

Interpretation:

- Rotation4 solved the main Rotation3 volume issue for profile-pressure and hedger lanes: all three lanes produced live candidates and fills across the 10-cycle window.
- The carry-forward profile-pressure mechanic is mechanically viable enough for deeper testing.
- Performance cannot be judged from this run until settlement resolves; the run only proves signal formation, order lifecycle, reconciliation, and dashboard/audit flow under live conditions.
- Next testing should compare these v3 lanes against refined variants, but avoid widening budget until settlement evidence is available.

## Rotation5 Five-Lane Technical And Strategy Batch

Reason for rotation5:

- The next batch must test strategy promise and technical robustness together, not only structural mechanics.
- Rotation4 identified three lanes with live signal formation and early positive aggregate settlement, but it did not exercise enough grid/band or volatility/scalping mechanics.
- Health/dashboard observability had two technical blockers that could hide real strategy outcomes:
  - completed/audited runs could still display as `stale`;
  - `/health` could block on Polymarket status DNS latency or embed excessive validation artifact detail.

Selected lanes:

| Lane | Strategy | Why included |
| --- | --- | --- |
| 1 | `hedger_ratio_replication_v3` | Highest-volume hedge-ratio lane so far; still mixed performance, but mandatory to evaluate hedge proportion mechanics. |
| 2 | `profile_hedge_scalping_v3` | Best profile-pressure/scalping evidence so far; Rotation4 settlement snapshot was positive. |
| 3 | `event_context_profile_confirmed_v1` | Event-context lane filtered by profile pressure; Rotation4 settlement snapshot was positive. |
| 4 | `grid_buyer_band_rebound_v1` | Needed to test grid/band rebound signal mechanics that were under-tested in Rotation4. |
| 5 | `volatility_spread_scalping_probe_v1` | Needed to test volatility/liquidity/scalping mechanics and quote fallback under live pressure. |

Technical hardening before retry:

- Dashboard now promotes completed `validated` + `matched` runs over stale active-process state.
- `/health` now summarizes only bounded validation artifacts and omits full strategy rows by default.
- `/health` now treats Polymarket status DNS timeout/unavailable as a degraded warning requiring per-order verified-market fallback, not as a hard CLOB-down blocker by itself.
- The supervised executor fallback now correctly treats `quote_age_seconds=0.0` as a fresh quote and accepts both `polymarket_status_unavailable` and `polymarket_status_timeout` when the current market quote is verified.
- Focused tests passed:
  - `tests/crypto_options_app/test_live_dashboard_pytest.py`
  - `tests/crypto_options_app/test_system_integrity_health_pytest.py`
  - `tests/crypto_options_app/test_polymarket_status_pytest.py`
  - `tests/crypto_options_app/test_polymarket_supervised_executor_pytest.py`
  - `tests/crypto_options_app/test_signal_live_runner_pytest.py`

First attempt:

- Run id: `signal-live-rotation5-five-promising-20lane-10cycles-20260603T214847Z`
- Outcome: stopped before live submission after `1` event cycle.
- Spend: `$0.00`
- Mechanical blocker: false runtime breaker from `exchange_status_not_operational` + `polymarket_status_unavailable` while the event quote itself was fresh and verified.
- Resolution: patched executor fallback zero-age handling and status timeout/unavailable handling.

Active retry:

- Run id: `signal-live-rotation5-five-promising-retry1-20lane-10cycles-20260603T220339Z`
- Budget: `$20` per lane, `$100` total cap.
- Event cap: `10` event cycles.
- Lane stops: enabled with `3` settled-loss streak, at least `3` settled positions, negative realized PnL, and win rate below `50%`.
- First-minute status: runner stayed alive, no false status breaker, `1` event cycle processed, `0` filled trades so far.

Monitoring rule:

- Do not patch for strategy underperformance during this run.
- Patch only systematic/mechanical errors: reconciliation mismatch, missing lifecycle coverage after filled BUY, duplicate cadence, stale critical service, unexpected traceback, credentials/access failure, actual exchange maintenance/incident, false status blocker, or DB/audit mismatch.
- If the run completes mechanically clean with low volume, choose the next batch based on signal formation and partial settlement evidence rather than rerunning unchanged lanes.

## Rotation5 Retry1 Final Result

Completed run:

- Run id: `signal-live-rotation5-five-promising-retry1-20lane-10cycles-20260603T220339Z`
- Stage: `rotation5_five_promising_retry1_status_fallback_patched_20_per_lane_10cycle_batch`
- Completed event cycles: `10 / 10`
- Estimated spend: `$8.739992`
- Final run artifact: `local/shared/artifacts/crypto-options-app/live-validation/signal-live-rotation5-five-promising-retry1-20lane-10cycles-20260603T220339Z.json`
- Order audit: `matched`
- Exact recorded/exchange BUY matches: `7 / 7`
- Weak matches: `0`
- Unmatched recorded BUYs: `0`
- Unmatched exchange BUYs: `0`
- Unexpected exchange SELLs in validation scope: `0`
- Manual orders avoided: yes

Per-lane execution:

| Lane | Strategy | Filled | Spend | Initial settlement/PnL read | Decision |
| --- | --- | ---: | ---: | --- | --- |
| 1 | `hedger_ratio_replication_v3` | `2` | `$2.309999` | `1W / 0L`, `+$0.326962` realized, `1` unresolved | Keep |
| 2 | `profile_hedge_scalping_v3` | `2` | `$2.549999` | `0W / 1L`, `-$1.079999` realized, `1` unresolved | Keep, but watch quality |
| 3 | `event_context_profile_confirmed_v1` | `2` | `$2.470000` | `0W / 1L`, `-$1.000000` realized, `1` unresolved | Keep, but watch quality |
| 4 | `grid_buyer_band_rebound_v1` | `1` | `$1.409994` | `1W / 0L`, `+$0.139450` realized | Keep |
| 5 | `volatility_spread_scalping_probe_v1` | `0` | `$0.000000` | No live fills | Replace |

Integrity notes:

- Dashboard count of `7` filled trades was verified against the run artifact, exact CLOB order-id audit, and canonical DB lifecycle rows.
- The Polymarket web UI may show fewer rows in the visible history viewport or lag behind, but the exchange audit found exactly `7` scoped BUYs and all were strongly matched.
- Canonical DB lifecycle was complete for all `7` exchange order IDs: candidate -> intent -> order -> fill -> position -> exit plan.
- Three positions were still unresolved at the initial settlement refresh, so realized PnL remains partial.

Interpretation:

- Rotation5 is mechanically clean and confirms the status-page fallback patches worked.
- Volume remains below what we need for a serious strategy comparison: `7` fills across `10` event cycles.
- `volatility_spread_scalping_probe_v1` did not produce live fills and should be replaced before the next batch.
- `event_context_outcome_v2` should return as a high-volume control lane. It is not currently a production candidate on quality, but it is useful for exercising event-context signal formation, FAK order lifecycle, settlement, and stop-gate behavior under higher fill volume.

## Rotation6-10 Priority Shift

Primary goal:

- Move from pure technical validation into strategy evidence collection.
- Keep technical stability as a hard prerequisite, but the active objective is now to find `5` runnable strategies that are profitable as a bundle and where no retained lane has win rate below `30%`.
- If Rotation6 is good enough, produce a full report and pause for review before the next major architecture step.
- If Rotation6 is not good enough, continue rotating batches up to Rotation10, aiming for at least `3` runnable strategies that are profitable together.

Corrected run semantics:

- The previous `10` event-cycle cap was too short because a processed cycle can be blocked, unfilled, or otherwise produce no actual evidence.
- Evidence runs should use:
  - up to `100` processed cycles;
  - target `10` filled event-slugs globally, or a future strategy-level filled-event target when needed;
  - per-lane realized-PnL stop at `-$5`, disabling only the problematic lane;
  - per-lane win-rate floor at `30%` after enough settled rows, disabling only the problematic lane;
  - lane stops still enabled for settled loss streak `>=3`, settled positions `>=3`, realized PnL `<0`, and win rate `<50%`.
- A run that reaches the processed-cycle cap without enough filled event evidence is a low-volume strategy result, not a mechanical pass.

Forward-looking strategy concepts to preserve for the next architecture block:

- Pre-event `50/50` Up/Down seeding when indicators imply no clear trend and likely target oscillation.
- Reversion sniping on `<20c` contracts as a higher-risk, lower-win-rate but asymmetric-return lane.
- Outcome-prediction timing variants at early, mid, and final-minute checkpoints.
- Grid/scalping variants that buy/sell between `40c` and `60c` when recent event oscillation and indicators support range behavior.
- Bonereaper-like budget-band replication remains a long-term target, but needs deeper event-level position reconstruction and strategy-specific scaling rules.
- Strategies must include explicit scaling, halting, and blocking rules and eventually be pulled dynamically from the DB without app restart.

## Rotation6 Planned Five-Lane Evidence Batch

Selected lanes:

| Lane | Strategy | Why included |
| --- | --- | --- |
| 1 | `hedger_ratio_replication_v3` | Best hedge-proportion volume lane so far; still needs more settled evidence. |
| 2 | `profile_hedge_scalping_v3` | Profile-pressure scalping lane with previous positive Rotation4 snapshot but mixed Rotation5 settlement. |
| 3 | `event_context_profile_confirmed_v1` | Profile-confirmed event-context lane; keeps event signal path grounded in profile pressure. |
| 4 | `grid_buyer_band_rebound_v1` | Grid/band lane produced one filled winner in Rotation5 and needs more volume. |
| 5 | `event_context_outcome_v2` | High-volume event-context control lane to exercise FAK fills, reconciliation, and stop gates; quality must be judged separately. |

Run guard:

- Same `$20` per-lane cap and `$100` bundle cap.
- Up to `100` processed cycles and target `10` filled event-slugs globally.
- Per-lane stops: `-$5` realized PnL floor, `<30%` win-rate floor after enough settled rows, and the existing loss-streak quality stop. These stop only the problematic lane; healthy lanes keep running.
- One cycle per event slug unless an explicit high-frequency strategy test requires otherwise.
- Lane stop gates remain enabled.
- Patch only systematic/mechanical failures; record strategy underperformance without changing premises mid-run.
- Retention bar after the run: profitable or strategically necessary, with no retained lane below `30%` win rate unless sample size is still insufficient and the lane is being carried only as a control/mechanics lane.

## Rotation6 Dashboard Review And Next Adjustment

Observed completed state:

- Run id: `signal-live-rotation6-evidence-20lane-100cycles-20260603T224800Z`
- Dashboard status: `validated`, audit `matched`, manual orders avoided.
- Filled trades: `15`
- Event cycles: `9`
- Estimated spend: `$15.659984`
- Realized PnL at first settlement read: `+$2.888446`
- Open/unresolved exposure: `$13.419997`

Why some filled lanes showed `n/a` win rate or `$0.00` PnL:

- The dashboard PnL is realized settlement PnL, not mark-to-market open PnL.
- `event_context_outcome_v2` had `7` fills, `0` settled positions, and `$7.28` open cost, so realized PnL and win rate were correctly unavailable.
- `hedger_ratio_replication_v3` had `3` fills, `0` settled positions, and `$3.079998` open cost, so realized PnL and win rate were correctly unavailable.
- The dashboard now exposes open cost and unresolved position count to prevent filled/open lanes from looking like zero-result lanes.

Why `grid_buyer_band_rebound_v1` stayed on `probing`:

- It produced no fills in Rotation6.
- Its dominant blocker was `no_band_rebound_minimal_probe_signal`.
- This is a strategy signal-volume issue, not a reconciliation/audit issue.
- It should not remain as an unchanged standalone comparison lane.

Technical patch:

- Added dashboard stop-for-review controls:
  - `POST /v1/crypto-options-app/dashboard/stop-for-review`
  - durable request artifact: `local/shared/artifacts/crypto-options-app/automation/stop_for_review_request.json`
  - append-only log: `local/shared/artifacts/crypto-options-app/automation/stop_for_review_requests.jsonl`
- The control records an automation request only; it does not place, cancel, redeem, or route orders.
- Added `grid_buyer_band_rebound_v2`, a higher-volume grid/band replacement that carries recent same-symbol/same-outcome grid-buyer pressure into the current verified event quote, matching the carry-forward pattern already used by `hedger_ratio_replication_v3` and `profile_hedge_scalping_v3`.

Focused tests passed:

- `tests/crypto_options_app/test_live_dashboard_pytest.py`
- `tests/crypto_options_app/test_strategy_manager_pytest.py`
- `tests/crypto_options_app/test_signal_live_runner_pytest.py`
- `tests/crypto_options_app/test_system_integrity_health_pytest.py`
- `tests/crypto_options_app/test_polymarket_supervised_executor_pytest.py`

Rotation7 adjusted five-lane batch:

| Lane | Strategy | Decision |
| --- | --- | --- |
| 1 | `event_context_profile_confirmed_v1` | Keep. Positive settled result and validates profile-confirmed event context. |
| 2 | `profile_hedge_scalping_v3` | Keep. Positive settled result and validates profile-pressure scalp flow. |
| 3 | `event_context_outcome_v2` | Keep provisionally. High fill volume, but judge performance only after unresolved positions settle. |
| 4 | `hedger_ratio_replication_v3` | Keep provisionally. Valid hedge-ratio execution path, but judge performance only after unresolved positions settle. |
| 5 | `grid_buyer_band_rebound_v2` | Replace `grid_buyer_band_rebound_v1`. Tests grid/band premise with carry-forward profile pressure instead of same-pulse executable-row dependence. |

Automation instruction:

- Before starting any new run, inspect `stop_for_review_request.json`.
- If status is `pending`, do not start a new live child. If a child is already alive, stop it cleanly, audit current artifacts, and report the operator message for review.
- Mark the request as handled only after the review action is reported.

## Rotation7 GridV2 Final Result

Completed run:

- Run id: `signal-live-rotation7-gridv2-20lane-100cycles-20260603T233704Z`
- Stage: `rotation7_gridv2_100cycle_10_global_filled_event_evidence_batch`
- Processed event cycles: `11`
- Global filled event-slugs: `10 / 10`
- Estimated spend: `$18.779996`
- Final run artifact: `local/shared/artifacts/crypto-options-app/live-validation/signal-live-rotation7-gridv2-20lane-100cycles-20260603T233704Z.json`
- Settlement report: `local/shared/artifacts/crypto-options-app/reports/signal-live-rotation7-gridv2-20lane-100cycles-20260603T233704Z_settlement_performance.json`
- Order audit: `matched`
- Exact recorded/exchange BUY matches: `18 / 18`
- Weak matches: `0`
- Unmatched recorded BUYs: `0`
- Unmatched exchange BUYs: `0`
- Unexpected exchange SELLs in validation scope: `0`
- Manual orders avoided: yes

Per-lane settlement snapshot:

| Strategy | Filled | Settled | Open | Realized PnL | Win rate | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `event_context_outcome_v2` | `7` | `1` | `6` | `-$1.040000` | `0.0%` | Do not keep as-is; high-volume control only unless filtered. |
| `event_context_profile_confirmed_v1` | `4` | `2` | `2` | `-$2.040000` | `0.0%` | Replace or redesign; profile confirmation was not sufficient. |
| `grid_buyer_band_rebound_v2` | `3` | `3` | `0` | `-$3.119998` | `0.0%` | Remove from active comparison; v2 improved volume but failed quality. |
| `hedger_ratio_replication_v3` | `3` | `3` | `0` | `-$0.759999` | `33.3%` | Provisional only; above the 30% floor but below target quality. |
| `profile_hedge_scalping_v3` | `1` | `1` | `0` | `+$1.519047` | `100.0%` | Keep, but volume remains too low. |

Bundle settlement snapshot:

- Settled positions: `10`
- Unresolved positions: `8`
- Realized PnL: `-$5.440950`
- Settled win rate: `20.0%`
- Active/open cost: `$8.320000`
- Settlement status: `partial` because some events were still unresolved at refresh.

Interpretation:

- Rotation7 was mechanically clean: CLOB audit, canonical lifecycle, DB persistence, and dashboard reporting all held.
- The result is not good enough to retain the bundle. The run crossed the intended lane-quality learning boundary without a mechanical failure.
- `grid_buyer_band_rebound_v2` should not be carried forward unchanged; the carry-forward grid pressure produced enough fills to test the mechanic, but all settled rows lost.
- `event_context_profile_confirmed_v1` also needs a stricter or different confirmation rule before more budget is spent.
- `event_context_outcome_v2` is useful as a high-volume control lane, but it should not be treated as a candidate without additional filters because the first settled row lost and most exposure remains unresolved.
- `hedger_ratio_replication_v3` remains a possible hedge-proportion lane, but only as a provisional candidate.
- `profile_hedge_scalping_v3` remains the best observed lane, though it needs a volume-focused variant before longer strategy testing.

Rotation8 direction:

- Keep `profile_hedge_scalping_v3`.
- Keep `hedger_ratio_replication_v3` only provisionally, preferably alongside a tighter hedge-ratio variant.
- Replace `grid_buyer_band_rebound_v2`.
- Replace or redesign `event_context_profile_confirmed_v1`.
- Treat `event_context_outcome_v2` as a control lane only unless a filtered variant is ready.
- Prefer replacements that add explicit price/timing filters or profile-pressure carry-forward while avoiding unfiltered 50c event-context buys.

## Rotation8 Mechanical Patch Scope

User review blockers from Rotation7:

- Persistent `duplicate_event_token_prevented` blockers distorted multi-lane comparison evidence and may have suppressed valid same-token lane expression.
- Persistent `final_minute_entry_block` rows showed that event selection was still drifting too close to expiry.
- Grid/band testing needs explicit order notional sizing so the order amount is large enough to satisfy exchange/CLOB minimums and produce a meaningful live probe.
- The dashboard needed both processed-cycle count and elapsed 5-minute window count from run start.
- Lanes with `0%` settled win rate after enough settled rows must be disabled as lane-level failures, not allowed to keep spending just because the bundle target is not reached.
- Positive lanes may be manually assigned larger order notional during evidence runs; this is a manual test parameter for now, not an automatic sizing algorithm.

Patch applied before Rotation8:

- `prevent_duplicate_event_tokens` is now configurable in `SignalLiveRunConfig`; comparison/evidence runs default it off so lanes can independently express the same event-token signal. Production-style duplicate protection remains opt-in through `--prevent-duplicate-event-tokens`.
- The signal live CLI now accepts `--strategy-order-notional-usd STRATEGY=USD` for manual lane sizing.
- The runner sizes the runtime scenario and supervised executor min/max notional from that per-strategy override while retaining total and per-lane budget caps.
- The signal live CLI now accepts `--min-seconds-remaining`; Rotation8 should use a higher value to avoid final-minute entries instead of letting the final-minute gate dominate the blocker table.
- The dashboard now displays processed cycles separately from elapsed 5-minute windows and run start time.

Focused tests passed:

- `tests/crypto_options_app/test_signal_live_runner_pytest.py`
- `tests/crypto_options_app/test_live_dashboard_pytest.py`
- `tests/crypto_options_app/test_polymarket_supervised_executor_pytest.py`
- `tests/crypto_options_app/test_system_integrity_health_pytest.py`

Rotation8 selected lanes:

| Lane | Strategy | Rotation8 reason |
| --- | --- | --- |
| 1 | `profile_hedge_scalping_v3` | Best observed lane; manually increased notional for the current win streak. |
| 2 | `hedger_ratio_replication_v3` | Provisional hedge-ratio lane; keep small but continue evidence. |
| 3 | `s_tier_outcome_hold_to_settlement_v1` | Strict S-tier outcome lane; replaces failed profile-confirmed event lane. |
| 4 | `s_tier_outcome_consensus_cashout_v1` | Strict S-tier cashout lane; tests cashout/outcome path without unfiltered event-context buys. |
| 5 | `buying_ahead_pre_event_v1` | Tests pre-event profile behavior and buying-ahead generator. |

Rotation8 run rules:

- Do not include `grid_buyer_band_rebound_v2` or `event_context_profile_confirmed_v1` unchanged.
- Do not include `event_context_outcome_v2` as a production-quality lane; it can return later only as a labeled high-volume control.
- Use `--min-seconds-remaining 120` to reduce final-minute blockers.
- Use explicit order notional overrides:
  - `profile_hedge_scalping_v3=$2.50`
  - `hedger_ratio_replication_v3=$1.50`
  - strict S-tier/buying-ahead lanes remain at `$1.00` unless they show live quality.
- If sparse valid gates prevent enough fills, patch signal formation or add focused variants; do not loosen safety gates or rerun failed lanes unchanged.

## Rotation8 Final Result

Completed run:

- Run id: `signal-live-rotation8-fixed-20lane-100cycles-20260604T003151Z`
- Stage: `rotation8_mechanical_fixes_profile_hedge_s_tier_buying_ahead_100cycle_10_global_filled_event_evidence_batch`
- Run start: `2026-06-04T00:31:51.7642870Z`
- Processed event artifacts: `14`
- Elapsed 5-minute windows from start: `9`
- Global filled event-slugs: `10 / 10`
- Estimated spend: `$42.219985`
- Final run artifact: `local/shared/artifacts/crypto-options-app/live-validation/signal-live-rotation8-fixed-20lane-100cycles-20260604T003151Z.json`
- Settlement report: `local/shared/artifacts/crypto-options-app/reports/signal-live-rotation8-fixed-20lane-100cycles-20260604T003151Z_settlement_performance.json`
- Order audit: `matched`
- Exact recorded/exchange BUY matches: `23 / 23`
- Weak matches: `0`
- Unmatched recorded BUYs: `0`
- Unmatched exchange BUYs: `0`
- Unexpected exchange SELLs in validation scope: `0`
- Manual orders avoided: yes

Mechanical checks:

- `duplicate_event_token_prevented` did not appear in Rotation8 artifacts.
- `final_minute_entry_block` did not appear after `--min-seconds-remaining 120`.
- Dashboard displayed processed event artifacts, elapsed 5-minute windows, and run start time.
- Lane-level stop gates worked: failing lanes were disabled while remaining lanes continued to the global filled-event target.
- Canonical DB lifecycle was complete for all executed rows: `23 / 23` candidate, intent, order, fill, position, exit-plan, and run-report rows matched the artifact keys.

Per-lane settlement snapshot:

| Strategy | Filled | Settled | Open | Realized PnL | Win rate | Stop/decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `profile_hedge_scalping_v3` | `7` | `7` | `0` | `-$6.754680` | `28.6%` | Stopped by realized PnL floor. Do not keep unchanged. |
| `hedger_ratio_replication_v3` | `10` | `9` | `1` | `-$7.708510` | `22.2%` | Stopped by win-rate floor. Do not keep unchanged. |
| `s_tier_outcome_hold_to_settlement_v1` | `3` | `2` | `1` | `-$0.999166` | `50.0%` | Sparse, acceptable floor; keep as strict S-tier control only. |
| `s_tier_outcome_consensus_cashout_v1` | `3` | `2` | `1` | `-$1.020000` | `50.0%` | Sparse, acceptable floor; keep as strict S-tier cashout control only. |
| `buying_ahead_pre_event_v1` | `0` | `0` | `0` | `n/a` | `n/a` | No live signals. Redesign or move to a dedicated pre-event monitor before retesting. |

Bundle settlement snapshot:

- Settled positions: `20`
- Unresolved positions: `3`
- Realized PnL: `-$16.482356`
- Active/open cost: `$3.889998`
- Settled win rate: `30.0%`
- Settlement status: `partial` because three positions were still unresolved at refresh.

Interpretation:

- Rotation8 validates the repaired mechanics but fails as a candidate bundle.
- The two profile-pressure lanes produced enough volume to test, but both failed quality thresholds and were correctly stopped.
- The strict S-tier lanes are safer but too sparse to carry a five-lane evidence run by themselves.
- `buying_ahead_pre_event_v1` did not produce any runnable signal in this live-event batch, which suggests the premise needs a separate pre-event universe/feed path rather than another unchanged in-event run.

Rotation9 direction:

- Do not rerun `profile_hedge_scalping_v3` or `hedger_ratio_replication_v3` unchanged.
- Keep `s_tier_outcome_hold_to_settlement_v1` and `s_tier_outcome_consensus_cashout_v1` as strict control lanes only if the next batch needs S-tier coverage.
- Replace or redesign the profile-pressure lanes with variants that add outcome conflict filters, price-band filters, or profile freshness filters before acting.
- Redesign `buying_ahead_pre_event_v1` around a dedicated future-event/pre-start feed before it is counted as a comparison lane.
- Next evidence batch should prioritize three new or revised lanes with explicit filters, plus at most two strict control lanes.

## P2 Centralization And Rotation9 Gate

P2 implementation is grounded in parent issue #123 and child issues #124-#134. The canonical source tree is now `crypto_options_app`; old docs, artifacts, and `codex_tool` scripts are compatibility or history only.

Rotation9 is not allowed until:

- centralized defaults and audit tests pass;
- Polymarket option price-path capture writes ticks, normalized depth, Up/Down pair snapshots, and watermarks to `crypto_options_app/data/crypto_options_data.sqlite`;
- profile-pressure rows are split into observation-only versus executable signals and stale/high-conflict rows are rejected;
- managed runtime primitives cover cashout/rebuy, hedge rebalance, and stale-order review;
- `/health` and dashboard expose centralized paths and data-service freshness.

Planned Rotation9 lanes:

- `profile_hedge_scalping_v1`
- `event_context_profile_confirmed_v1`
- `hedger_ratio_replication_v4`
- `profile_hedge_scalping_v4`
- `grid_band_rebound_v3`

## Rotation9 Start State

Readiness gate cleared on 2026-06-04 after:

- centralized crypto app suite passed: `python -m pytest tests/crypto_options_app -q` -> `167 passed`;
- legacy profile and market shards were imported into `crypto_options_app/data/crypto_options_data.sqlite` with parity checks passing;
- read-only Polymarket option price capture loop started under `crypto_options_app/scripts/run_crypto_options_option_price_capture.py`;
- central DB had live price-path rows, normalized order-book levels, Up/Down pair snapshots, and healthy/degraded watermarks depending on per-token CLOB book availability;
- managed Rotation9 variants were routed through executable signal formation instead of the prior `managed_runtime_not_ready` hard block;
- runtime attribution and canonical DB persistence now include managed exit/rebuy/rebalance plan coverage for managed lanes;
- DB-backed replay frames can be built from captured price paths and used by fill/cashout simulation.

Active Rotation9 run:

- Run id: `signal-live-rotation9-managed-pricepath-20260604T062245Z`
- Strategy ids: `profile_hedge_scalping_v1`, `event_context_profile_confirmed_v1`, `hedger_ratio_replication_v4`, `profile_hedge_scalping_v4`, `grid_band_rebound_v3`
- Global target: `10` filled event-slugs
- Budget caps: `$100` bundle, `$20` per lane
- Lane stop gates: enabled
- Live flags: scoped to supervised child process only
- Global/API live flags: false
- Manual orders: avoided

Operational caveat:

- Managed v4/grid lanes currently submit only supervised BUY entries and persist managed exit/rebuy/rebalance plans for lifecycle coverage. Live SELL/cashout execution remains a separate supervised manager scope and must not be treated as implemented production cashout automation yet.

## 2026-06-06 Strategy Scout Follow-Up: A+C Control Lane

Registered lane:

- `crypto_direction_option_context_hold_60s_v1`

Purpose:

- First simple non-profile promotion candidate.
- Uses A Crypto direction/context indicators plus C Polymarket option support/depth context.
- No B profile dependency.
- Read-only shadow/live-replay only; no live orders.
- Exit economics use the 60-second forward mark from captured Polymarket price paths.

Validation run:

- Run id: `strategy-live-replay-crypto-direction-option-context-20260606T0156Z`
- Mode: `shadow`
- Scenario source: `polymarket_price_ticks_forward_mark`
- Samples: `12`
- Structural pass count: `12`
- Blocked count: `0`
- Persisted rows: `12` candidates, intents, orders, fills, positions, exit plans, run reports, and strategy validation runs.

Forward-mark economics:

| Metric | Value |
| --- | ---: |
| Samples | `12` |
| Wins | `2` |
| Losses | `10` |
| Win rate | `16.7%` |
| Simulated PnL | `-$0.15` |

Decision:

- Structurally valid, but not promotion-quality.
- Keep as a control/reference lane only.
- Do not promote live.
- Next A+C variant should add stricter directional agreement and option-price context filters before entry instead of rerunning this lane unchanged.
