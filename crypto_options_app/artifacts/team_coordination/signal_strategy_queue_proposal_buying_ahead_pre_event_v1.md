# Signal Strategy Queue Proposal: `buying_ahead_pre_event_v1`

Generated: `2026-06-07T04:30:29.3094662-03:00`

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

## Row

- Classification in cleanup batch: `NEEDS_VARIANT`
- Strategy id: `buying_ahead_pre_event_v1`
- Current state: `SHADOW_REVIEW`
- Reported blockers: `shadow_live_non_positive_pnl`, `missing_recent_shadow_live_economic_evidence`

## Evidence

- The cleanup batch already says to revise or retire unchanged failed lanes.
- The strategy format spec defines this lane as requiring a future-event universe and pre-event prices.
- Prior strategy testing notes say the lane produced no live signals and should be redesigned around a dedicated future-event/pre-start feed before retesting.

## Proposal

Queue-management decision: `BLOCKED`

Reason:

- The missing capability is a source/feed-path gap, not a small V2 tuning change.
- Creating a V2-V5 variant without a dedicated pre-event universe would not address the documented blocker.
- Keeping the row in `NEEDS_VARIANT` risks repeated unchanged reruns with no new evidence.

Suggested next action:

- Keep `buying_ahead_pre_event_v1` out of active cleanup promotion work until a bounded handoff explicitly adds a pre-event universe/feed implementation plan.
- When that handoff exists, reopen as a focused variant or redesign task instead of replaying the current lane unchanged.

## Safety

- This proposal is queue guidance only.
- It does not authorize live trading.
- It does not authorize manual orders.
