# Crypto Options Repo Cleanup Inventory

- Generated: `2026-06-07T11:44:14.901346+00:00`
- Status: `degraded`
- Manual orders avoided: `True`

## Summary
- Dirty paths: `637`
- Review-required paths: `132`
- Active crypto paths: `505`
- Crypto compatibility wrapper candidates: `131`
- Legacy move candidates: `0`
- Unknown/root review paths: `0`

## Gates
- Automatic moves allowed: `False`
- Repo move ready: `False`
- Fixed chats start ready: `True`
- Fixed chat gate: `ready_per_fixed_chat_startup_readiness`
- Fixed chat readiness report: `crypto_options_app\artifacts\reports\fixed_chat_startup_readiness_latest.json`
- GitHub issue creation ready: `True`

## Classification Counts
- `crypto_active`: `505`
- `crypto_compatibility_wrapper_candidate`: `131`
- `root_config_review`: `1`

## Warnings
- `repo_dirty_requires_review_before_moves`
- `crypto_compatibility_wrappers_need_cutover_decision`

## Proposed Move Samples
- `M` `crypto_options_app/artifacts/reports/fixed_chat_startup_readiness_latest.json` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/artifacts/reports/fixed_chat_startup_readiness_latest.md` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/artifacts/team_coordination/automation_status/db_data_observability_latest.md` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/artifacts/team_coordination/automation_status/frontend_status_reporter_latest.md` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/artifacts/team_coordination/automation_status/signal_strategy_queue_worker_latest.md` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/reports/repo_cleanup_batches.py` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/reports/repo_cleanup_inventory.py` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `crypto_options_app/scripts/run_crypto_options_repo_cleanup_inventory.py` -> `crypto_active` / `keep_active_crypto` / `none`
- `M` `requirements.txt` -> `root_config_review` / `manual_root_config_review` / `none`
- `??` `app/api/routers/crypto_options_market_data.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/api/routers/crypto_options_market_data.py`
- `??` `app/api/routers/crypto_options_signals.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/api/routers/crypto_options_signals.py`
- `??` `app/data/nodes/crypto/__init__.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/crypto/__init__.py`
- `??` `app/data/nodes/crypto/candles.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/crypto/candles.py`
- `??` `app/data/nodes/crypto/reference.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/crypto/reference.py`
- `??` `app/data/nodes/polymarket/crypto/__init__.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/__init__.py`
- `??` `app/data/nodes/polymarket/crypto/accounts.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/accounts.py`
- `??` `app/data/nodes/polymarket/crypto/history.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/history.py`
- `??` `app/data/nodes/polymarket/crypto/live_capture.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/live_capture.py`
- `??` `app/data/nodes/polymarket/crypto/markets.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/markets.py`
- `??` `app/data/pipelines/crypto/__init__.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/__init__.py`
- ... `617` additional paths in JSON artifact

## Next Actions
- Review the JSON path_entries list before moving files.
- Create a branch/commit plan that separates crypto active work from reference moves.
- Decide which crypto compatibility wrappers must stay until runtime routes are fully cut over.
- Start only the ready fixed chats from fixed_chat_bootstrap; keep future-only prompts closed.
