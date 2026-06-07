# Crypto Options Transition Readiness Review

- Generated: `2026-06-07T04:24:33.405048+00:00`
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
- `health_degraded`
- `repo_dirty_requires_inventory_cleanup`
- `storage_audit_degraded`

## Promotion Summary
- Strategy count: `90`
- Live candidates: `0`
- Shadow ready: `8`
- Strict signal blockers: `0`

## Repo Summary
- Dirty paths: `877`
- Crypto active dirty paths: `656`
- Reference candidate dirty paths: `72`

## Next Actions
- Run path-level repo cleanup inventory before moving legacy files.
- Add DB/runtime adapter tests for remaining production SQLite direct-connect offenders.
- Add Redis adapter tests for cache/queue TTL before enabling Redis at runtime.
- Review SHADOW_READY rows for recent one-hour economic proof before any live promotion.
- Create GitHub milestones/issues from team_coordination issue plan before starting fixed chats.
