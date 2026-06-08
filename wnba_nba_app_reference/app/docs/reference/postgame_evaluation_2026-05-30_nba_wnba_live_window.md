# Postgame Evaluation - 2026-05-30 NBA/WNBA Live Window

Status: postgame-to-development source truth
Created: 2026-05-31
Owner issue: #63
Related issues: #44, #80, #81, #82
Closed accounting route: #83

## Scope

May 30 was a useful live-money learning day because it covered two WNBA windows and one NBA game with direct account activity, manual/operator interference, Janus autonomous orders, fixed 5-share sizing, and postgame account-return evidence.

Reviewed events:

- `wnba-sea-tor-2026-05-30`
- `wnba-la-conn-2026-05-30`
- `wnba-ind-por-2026-05-30`
- `nba-sas-okc-2026-05-30`

All live-money conclusions below remain bounded by the existing Janus rule: orders, cancels, submissions, signing, broadcasting, redemption, and order management may only happen through Janus StrategyPlan/live-worker/order-management gates.

## Account Return Summary

Account-return reporting worked after the May 30 correction: gross buy turnover is not invested capital, and return is measured against peak cash at risk.

| Event | Actual PnL | Peak cash at risk | Return on peak risk | Gross buy turnover | Sell proceeds | Account trades | Source confidence |
|---|---:|---:|---:|---:|---:|---:|---|
| SEA/TOR WNBA | -$1.150220 | $3.300220 | -34.852828% | $10.368720 | $9.218500 | 11 | account_confirmed |
| LA/CONN WNBA | +$1.400000 | $6.384560 | +21.927901% | $21.767680 | $20.802380 | 20 | account_confirmed |
| IND/POR WNBA | +$1.299515 | $9.930486 | +13.086117% | $31.432545 | $26.547870 | 30 | account_confirmed |
| SAS/OKC NBA | +$5.067219 | $16.293076 | +31.100444% | $41.391515 | $46.968728 | 33 | account_confirmed |
| Total | +$6.616514 | $35.908342 | +18.426119% | $104.960460 | $103.537478 | 94 | account_confirmed |

Primary artifacts:

- `local/shared/artifacts/ops/2026-05-30/postgame-review_20260530T190157Z.json`
- `local/shared/artifacts/ops/2026-05-30/postgame-review_20260531T032817Z.json`
- `local/shared/artifacts/ops/2026-05-30/postgame-review_20260531T025727Z.json`
- `local/shared/artifacts/ops/2026-05-30/postgame-review_20260531T025906Z.json`
- `local/shared/artifacts/live-strategy-worker/2026-05-30/ticks.jsonl`

## Per-Game Read

### SEA/TOR WNBA

Result for Janus/account: `-$1.150220` actual PnL on `$3.300220` peak cash at risk.

What happened:

- The 2pm standalone window proved the fixed postgame return calculator and the nano PBP path.
- `pbp_annotation.model_tier=nano` and `nano_dispatch.status=response_recorded` were observed before live order promotion; PBP remained evidence-only.
- Duplicate signal pressure appeared after the game moved live: `duplicate_signal_total=59`.
- The game ended with final losing SEA inventory/open target cleanup as postgame evidence, not a clean autonomous lifecycle win.

Lesson:

The system can now report the small loss correctly, but final cleanup and duplicate pressure still need hardening before this is a repeatable unattended WNBA pattern.

### LA/CONN WNBA

Result for Janus/account: `+$1.400000` actual PnL on `$6.384560` peak cash at risk.

What happened:

- A bounded postgame rerun for LA/CONN with direct CLOB evidence skipped produced account-confirmed PnL and closed-position override evidence.
- Postgame marked direct final flat, but local lifecycle attribution remained review-gated with `unknown_lifecycle_count=25`.
- The postgame why-no-trade section recorded `scoreboard_freshness_required=128`, `position_limit_reached=25`, and `economic_duplicate_buy_intent_blocked=2`.
- This was profitable, but the lifecycle artifact still cannot fully explain every local Janus and manual row.

Lesson:

The account result was good, but direct lifecycle attribution must become time-boxed and per-event partial by default. A profitable closed account result is not enough if local order rows remain unexplained.

### IND/POR WNBA

Result for Janus/account: `+$1.299515` actual PnL on `$9.930486` peak cash at risk.

What happened:

- Account return was positive.
- Worker evidence showed the strongest duplicate pressure of the day: `duplicate_signal_total=245`.
- Final-state cleanup evidence appeared frequently (`final_cleanup_total=70` in the worker summary).
- Late live-monitor snapshots still showed one active open position and two open orders for this event while the account-return artifact was positive and final.

Lesson:

This is the clearest reduce/stop/final-cleanup follow-up. The postgame result was positive, but Janus still needs a final-state reconciliation rule that resolves stale local targets and residual direct inventory evidence without raw manual order actions.

### SAS/OKC NBA

Result for Janus/account: `+$5.067219` actual PnL on `$16.293076` peak cash at risk.

What happened:

- The operator demonstrated the best strategy pattern of the day: rebound-sniping after price dislocation, not only high-frequency one-cent band trading.
- Manual OKC/SAS orders used the thesis that OKC depth and third-quarter strength could create rebound windows while Wemby foul trouble or bench windows changed the game state.
- Janus reconciled final account activity well enough to report positive PnL and no final open orders/positions in the latest monitor summary.
- Janus did not detect the main basketball catalyst: worker summaries had `star_foul_trouble_true=0` for the event despite the operator flagging Wemby foul trouble.
- Duplicate signal pressure remained (`duplicate_signal_total=41`), but this was less important than the missing catalyst/snipe sleeve.

Lesson:

The system needs two concurrent strategy styles:

1. Micro-band scalping: repeated 5-share buy/sell cycles around tight nominal moves.
2. Rebound-sniping: less frequent catalyst-aware entries after large price dislocation, with target ladders that can coexist with older manual targets and with micro-band sleeves.

The current architecture recognizes pieces of this, but the live runtime did not yet promote player-catalyst evidence into an explicit rebound-snipe sleeve review.

## Runtime Evidence Summary

| Event | Worker ticks | Duplicate signals | Intents | Notable LLM/PBP status | Main blockers |
|---|---:|---:|---:|---|---|
| SEA/TOR | 324 | 59 | 5 | nano observed, many skipped/unavailable periods | duplicate cooldown, event budget exceeded |
| LA/CONN | 96 | 27 | 11 | mixed dispatch/fallback; later partial postgame | scoreboard freshness, position limit, economic duplicate |
| IND/POR | 117 | 245 | 22 | response_recorded plus fallback | duplicate cooldown, duplicate exposure, final cleanup |
| SAS/OKC | 117 | 41 | 22 | player catalyst missing; fallback/unavailable common | duplicate cooldown, duplicate exposure, event budget |

## Defect Routing

### #63 - Rebound-Snipe Sleeve And Manual-Interference Rebase

The highest-value new runtime behavior is a rebound-snipe sleeve. It must be independent from micro-band scalping and manual-imported inventory. Manual/operator activity should trigger a rebase and, when material, a mini-model review of the current state. The rebase must separate:

- operator-imported risk;
- Janus autonomous micro-band budget;
- Janus autonomous rebound-snipe budget;
- open buy-order reserve;
- realized closed PnL adjustment.

### #63 - Duplicate Signal Pressure

Duplicate economic buys were patched, but duplicate signal pressure remained high, especially IND/POR. This should be fixed as signal hygiene, not by reducing the event cap. The distinction is:

- duplicate evidence is useful for confidence and diagnostics;
- duplicate same-side/same-sleeve/same-band/same-cycle budget claims are a defect unless a reviewed ladder explicitly declares the multi-lot behavior.

### #82 - Final Cleanup And Residual Lifecycle

IND/POR proved the reduce/stop/final cleanup surface is not done. A final positive account result can still leave Janus with stale local targets or residual direct-inventory confusion. Final cleanup should be explicit, event-scoped, and reconciled without emitting live sell candidates after settlement.

### #81 - Player-Catalyst PBP

The nano PBP transport worked on SEA/TOR, but the SAS/OKC high-leverage basketball catalyst did not. Wemby foul trouble, bench/rest windows, star status, score runs, and late-game depth advantages need to become first-class evidence for the rebound-snipe sleeve. Missing catalyst evidence is YELLOW unless it directly causes unsafe execution.

### #44 - Risk Calibration

May 30 is account-confirmed positive overall, but the risk ratchet should not blindly increase caps until lifecycle and manual-interference attribution are cleaner. Use the account-confirmed PnL and peak-risk rows as calibration input, but keep promotion gated by source confidence and unresolved-lifecycle counts.

### #83 - Accounting Correction Held

#83 should stay closed for the original accounting bug: gross turnover is no longer reported as invested capital or actual loss. The remaining direct-CLOB timeout and unknown-lifecycle reporting gap should route to #63 postgame/runtime hardening unless the account-return calculator itself regresses.

## Next Development Order

1. Write this postgame source truth and sync the local task register, backlog, and Obsidian.
2. Add a focused postgame/direct-evidence timeout task: per-event timebox, partial result, and unresolved lifecycle row quality.
3. Implement rebound-snipe sleeve evidence and manual-interference rebase.
4. Implement player-catalyst detection for foul trouble, rest/bench windows, score runs, and price dislocation.
5. Reduce duplicate signal pressure without reducing parallel event budget.
6. Harden final cleanup/reduce-stop readback for final WNBA residuals.
7. Run focused tests for account-return, live tick, signal aggregation, PBP annotation, reduce/stop, paired microcycle, and event budget.

## Acceptance For The Next Clean Run

- Every buy is 5 shares under `order_sizing_mode=fixed_min_shares`.
- Event cap allows multiple distinct sleeves without allowing same-cycle duplicates.
- Micro-band and rebound-snipe sleeves can coexist.
- Manual/interference orders are imported as account truth and trigger rebase evidence.
- Wemby-style foul trouble/rest/player shock creates PBP/player-catalyst evidence.
- Final rows are either resolved or explicitly unresolved with event-scoped reasons.
- Postgame can complete per-event partial reports even when direct CLOB evidence is slow.
