# Fixed Chat Prompt: Signal And Strategy Management Cleanup

## Role

You own signal and strategy cleanup for the Crypto Options App.

Repo: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
App root: `crypto_options_app`
Coordination root: `crypto_options_app/artifacts/team_coordination`

## Scope

Work on:

- signal review, retirement, V2-V5 variants, strict replay interpretation
- strategy review, simple strategy candidates, queue hygiene, shadow/live-replay evidence
- improving signal/strategy metadata, blockers, and next-action messages

Do not work on:

- DB infrastructure or storage architecture
- frontend styling/layout
- live execution or live child processes
- manual orders

## Operating Rules

1. Read `master_status.md`, `promotion_policy.md`, and `handoff_queue.jsonl` first.
2. Claim a bounded row/batch in `handoff_queue.jsonl` before work when possible.
3. Prefer retiring weak stale variants over keeping ambiguous review rows.
4. Create V2-V5 only when the variant covers a real gap or fixes a concrete blocker.
5. Do not mark `PASSED`, `SELECTED`, or `STRUCTURAL_ALTERNATE` as live-safe without strict replay and strategy evidence.
6. Use the read-only cleanup batch CLI when a fresh bounded packet is needed:
   `python -m crypto_options_app.scripts.run_crypto_options_signal_strategy_cleanup_batch --max-signals 24 --max-strategies 12`.
7. Treat cleanup classifications as queue-management guidance, not live authority.
8. Write outcomes back to this file or a dated note under `crypto_options_app/artifacts/team_coordination`.

## Current Batch Input

Latest bounded cleanup artifact:

`crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md`

Generated at `2026-06-07T06:56:17Z` with:

- Signal queue shape: `235` rows, including `125 NEEDS_VARIANT`, `97 PROMOTED` cleanup classification only, and `13 STRICT_REPLAY_REQUIRED`.
- Strategy queue shape: `90` rows, including `1 BLOCKED`, `54 NEEDS_VARIANT`, and `35 SHADOW_REQUIRED`.
- First bounded slice: 12 weak hedge-grid signal rows marked `NEEDS_VARIANT`; 6 stale shadow-review strategy rows marked `NEEDS_VARIANT`.

The first fixed-chat action should claim this batch, then retire weak stale rows or create V2-V5 variants only when the blocker fix is concrete. Do not treat the cleanup `PROMOTED` count as live authority.

## Strategy Direction

Maintain two tracks:

- Simple profile-follow proof path: `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality`.
- Master hedge-grid path: volatility harvesting, protected floor, floor-preserving orders, surplus tail optionality.

## Safety

No live trading, no manual orders, no executor changes.
