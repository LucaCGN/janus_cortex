# Crypto Options Fixed Chat Prompts

Updated: 2026-06-07T05:35:00Z

## Purpose

These prompts are the starting contracts for fixed Codex chats that work beside the master chat.

All fixed chats must read and write:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`

## Start Gates

- `frontend_control_center_developer.md`: can start after Batch 3. It must stay frontend/API-contract scoped.
- `signal_strategy_management_cleanup.md`: wait for Batch 4 compatibility-wrapper decisions and GitHub issues/milestones.
- The DB/data, indicator, signal, and strategy specialist prompts exist as future lane contracts. Do not start them as standing fixed chats until the master chat opens that lane.

## Required Behavior

- Do not silently mutate broad state.
- Do not run live trading.
- Do not authorize manual orders.
- Append status to the lane prompt file or a dated note under `crypto_options_app/artifacts/team_coordination/`.
- Add technical-spec updates only when behavior or policy changes.
