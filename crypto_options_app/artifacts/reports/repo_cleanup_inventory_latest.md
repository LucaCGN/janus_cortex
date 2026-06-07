# Crypto Options Repo Cleanup Inventory

- Generated: `2026-06-07T04:34:52.750518+00:00`
- Status: `degraded`
- Manual orders avoided: `True`

## Summary
- Dirty paths: `902`
- Review-required paths: `219`
- Active crypto paths: `681`
- Crypto compatibility wrapper candidates: `131`
- Legacy move candidates: `72`
- Unknown/root review paths: `0`

## Gates
- Automatic moves allowed: `False`
- Repo move ready: `False`
- Fixed chats start ready: `False`
- Fixed chat gate: `blocked_until_repo_cleanup_and_github_milestones_are_reviewed`
- GitHub issue creation ready: `False`

## Classification Counts
- `crypto_active`: `681`
- `crypto_compatibility_wrapper_candidate`: `131`
- `global_reference`: `1`
- `global_reference_candidate`: `69`
- `local_automation_state_review`: `15`
- `root_config_review`: `1`
- `wnba_nba_reference`: `1`
- `wnba_nba_reference_candidate`: `3`

## Warnings
- `repo_dirty_requires_review_before_moves`
- `crypto_compatibility_wrappers_need_cutover_decision`
- `legacy_reference_candidates_need_inventory_review`

## Proposed Move Samples
- `M` `app/api/main.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/api/main.py`
- `M` `app/api/models.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/api/models.py`
- `M` `app/api/routers/__init__.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/api/routers/__init__.py`
- `M` `app/api/routers/ops.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/api/routers/ops.py`
- `M` `app/api/routers/portfolio.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/api/routers/portfolio.py`
- `M` `app/data/nodes/polymarket/blockchain/manage_portfolio.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/data/nodes/polymarket/blockchain/manage_portfolio.py`
- `M` `app/docs/planning/current/final_system/architecture/janus_core_live_trading_runtime.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/architecture/janus_core_live_trading_runtime.md`
- `M` `app/docs/planning/current/final_system/automation/backlog_layers.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/automation/backlog_layers.md`
- `M` `app/docs/planning/current/final_system/automation/global_portfolio_manager_contract.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/automation/global_portfolio_manager_contract.md`
- `M` `app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/automation/global_portfolio_manager_prompt.md`
- `M` `app/docs/planning/current/final_system/automation/issue_task_register.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/automation/issue_task_register.md`
- `M` `app/docs/planning/current/final_system/automation/live_signal_aggregation_contract.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/automation/live_signal_aggregation_contract.md`
- `M` `app/docs/planning/current/final_system/backlog/live_postgame_learning_backlog.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/backlog/live_postgame_learning_backlog.md`
- `M` `app/docs/planning/current/final_system/market_scope_registry.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/market_scope_registry.md`
- `M` `app/docs/planning/current/final_system/source_of_truth_map.md` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/docs/planning/current/final_system/source_of_truth_map.md`
- `M` `app/modules/agentic/contracts.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/modules/agentic/contracts.py`
- `M` `app/modules/agentic/engine.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/modules/agentic/engine.py`
- `M` `app/modules/agentic/event_budget.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/modules/agentic/event_budget.py`
- `M` `app/modules/agentic/global_portfolio.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/modules/agentic/global_portfolio.py`
- `M` `app/modules/agentic/live_game_context.py` -> `global_reference_candidate` / `review_move_to_global_reference` / `global_app_reference/app/modules/agentic/live_game_context.py`
- ... `882` additional paths in JSON artifact

## Next Actions
- Review the JSON path_entries list before moving files.
- Create a branch/commit plan that separates crypto active work from reference moves.
- Decide which crypto compatibility wrappers must stay until runtime routes are fully cut over.
- Move reviewed legacy paths into reference roots in small, testable batches.
- Create GitHub milestones/issues after the cleanup plan is reviewable.
- Start fixed chats only after cleanup and issue source-of-truth are established.
