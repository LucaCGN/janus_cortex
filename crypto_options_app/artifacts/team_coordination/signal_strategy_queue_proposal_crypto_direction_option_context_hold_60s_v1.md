# Signal Strategy Queue Proposal: `crypto_direction_option_context_hold_60s_v1`

Generated: `2026-06-07T05:02:00-03:00`

## Scope

Single bounded queue-worker proposal from the latest cleanup batch artifact.

Referenced open handoff item:

- `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`
- `2026-06-07T06:56:53Z`
- Owner: `signal_strategy_management_cleanup`
- Task: `Process latest bounded cleanup batch artifact.`

Related GitHub context:

- `#156 [CRYPTO-P5-02] Strategy promotion state cleanup`
- `#157 [CRYPTO-P5-03] Profile-follow candidate evidence accumulation`
- `#158 [CRYPTO-P5-04] Hedge-grid protected-floor selector readiness`

## Row

- Classification in cleanup batch: `NEEDS_VARIANT`
- Strategy id: `crypto_direction_option_context_hold_60s_v1`
- Current state: `SHADOW_REVIEW`
- Reported blockers: `shadow_live_non_positive_pnl`, `missing_recent_shadow_live_economic_evidence`

## Evidence

- The cleanup batch already says to revise or retire unchanged failed lanes.
- The latest strategy revision scout names this lane as the priority-2 simple non-profile candidate family and recommends multi-scenario forward-mark sampling before any supervised live attempt.
- The latest unified dev-loop shadow replay checkpoint records `40` samples, `35.0%` win rate, `+3.2000` simulated PnL, and `+15.5%` ROI, which is economically positive but still well below the promotion policy win-rate gate.

## Proposal

Queue-management decision: `SHADOW_REQUIRED`

Reason:

- The row has enough replay evidence to avoid immediate retirement, but it still lacks the recent reconciled shadow evidence required to move beyond review.
- Positive replay economics without the required win-rate threshold means the lane is not promotable and should not be converted into a V2-V5 variant without a concrete blocker fix.
- A focused shadow/replay evidence pass is the narrowest next step that matches both the cleanup report and the revision scout.

Suggested next action:

- Keep `crypto_direction_option_context_hold_60s_v1` out of promotion work and unchanged live-lane recycling.
- Reopen it only through a bounded handoff that runs the forward-mark shadow sampler or equivalent evidence pass, then reassess whether the lane should stay `SHADOW_REQUIRED`, move to `REVIEW`, or be retired.

## Safety

- This proposal is queue guidance only.
- It does not authorize live trading.
- It does not authorize manual orders.
