# Crypto Options Transition Readiness Review

- Generated: `2026-06-07T11:29:12.510409+00:00`
- Status: `degraded`
- Manual orders avoided: `True`

## Readiness
- `runtime_data_stability`: `70%`
- `replay_integrity`: `75%`
- `promotion_demotion_trust`: `50%`
- `signal_strategy_queue_readiness`: `55-60%`
- `frontend_control_center_readiness`: `45%`
- `repo_github_workflow_readiness`: `40%`
- `coordination_readiness`: `70%`
- `storage_architecture_readiness`: `60%`
- `broad_automation_readiness`: `not_ready`

## Blockers
- `none`

## Warnings
- `repo_dirty_requires_inventory_cleanup`
- `storage_audit_degraded`

## Promotion Summary
- Strategy count: `110`
- Live candidates: `0`
- Shadow ready: `12`
- Strict signal blockers: `0`
- Policy contract: `crypto_options_promotion_policy_contract_v1`

## Repo Summary
- Dirty paths: `534`
- Crypto active dirty paths: `402`
- Reference candidate dirty paths: `0`

## Next Actions
- Continue Batch 4 compatibility-wrapper review and keep generated/runtime artifacts unstaged.
- Reduce measured Postgres memory/query pressure before enabling Redis or widening replay/data-service workers.
- Review SHADOW_READY rows for recent one-hour economic proof before any live promotion.
- Keep fixed chats and limited automations on their GitHub issue and team_coordination handoff contracts.
