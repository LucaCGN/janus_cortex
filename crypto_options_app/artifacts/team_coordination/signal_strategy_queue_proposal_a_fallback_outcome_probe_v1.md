# Queue Proposal: `a_fallback_outcome_probe_v1`

Generated: `2026-06-07T04:45:02.2845746-03:00`
Referenced handoff: `Process latest bounded cleanup batch artifact.` from `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl` at `2026-06-07T06:56:53Z`
GitHub context: `#156`, `#159`

## Row

- Cleanup classification: `NEEDS_VARIANT`
- Current queue state: `SHADOW_REVIEW`
- Proposed queue-management state: `RETIRED`
- Strategy id: `a_fallback_outcome_probe_v1`

## Evidence

- `crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md` marks the row with `shadow_live_non_positive_pnl` and `missing_recent_shadow_live_economic_evidence`.
- `crypto_options_app/docs/reference/crypto_options/technical_specs/14_strategy_testing_loop_status.md` says the lane had `10 gated/blocked rows, no live attempts` and the next bundle decision is `Replace`.
- The same status doc says the fallback conditions rarely became primary, so this row added no execution evidence in the bundle.
- `crypto_options_app/docs/reference/crypto_options/crypto_options_strategy_format_spec_2026-06-02.md` defines this as a secondary fallback lane used only when no S/S+/S++ outcome source is live, which means the row is inherently dependent on a sparse scenario and is not a good candidate for unchanged reruns.
- `crypto_options_app/workers/signal_live_runner.py` still includes this strategy in the historical first-six bundle, so retiring it should be handled as a separate curated bundle decision rather than an unscoped runtime edit in this pass.

## Decision

- Proposal only: retire `a_fallback_outcome_probe_v1` from queue-management consideration until a future handoff explicitly asks for a replacement fallback design or a curated bundle swap.
- Do not create a V2-V5 variant in this pass because the documented failure mode is weak/no activation plus missing recent evidence, not a concrete blocker fix that a small code change would solve.
- Do not treat this retirement proposal as live authority; it is queue guidance only.

## Safety

- No runtime, DB/storage, frontend, executor, or live-flag changes were made.
- No manual orders were placed or authorized.
