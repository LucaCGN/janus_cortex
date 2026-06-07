# Crypto Options Repo Cleanup Inventory

- Generated: `2026-06-07T05:00:38.799750+00:00`
- Status: `degraded`
- Manual orders avoided: `True`

## Summary
- Dirty paths: `534`
- Review-required paths: `132`
- Active crypto paths: `402`
- Crypto compatibility wrapper candidates: `131`
- Legacy move candidates: `0`
- Unknown/root review paths: `0`

## Gates
- Automatic moves allowed: `False`
- Repo move ready: `False`
- Fixed chats start ready: `False`
- Fixed chat gate: `blocked_until_repo_cleanup_and_github_milestones_are_reviewed`
- GitHub issue creation ready: `False`

## Classification Counts
- `crypto_active`: `402`
- `crypto_compatibility_wrapper_candidate`: `131`
- `root_config_review`: `1`

## Warnings
- `repo_dirty_requires_review_before_moves`
- `crypto_compatibility_wrappers_need_cutover_decision`

## Proposed Move Samples
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
- `??` `app/data/pipelines/crypto/options/__init__.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/__init__.py`
- `??` `app/data/pipelines/crypto/options/audit.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/audit.py`
- `??` `app/data/pipelines/crypto/options/backtests.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/backtests.py`
- `??` `app/data/pipelines/crypto/options/candidate_report.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/candidate_report.py`
- `??` `app/data/pipelines/crypto/options/cashout_simulator.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/cashout_simulator.py`
- `??` `app/data/pipelines/crypto/options/contracts.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/contracts.py`
- `??` `app/data/pipelines/crypto/options/engine.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/engine.py`
- `??` `app/data/pipelines/crypto/options/exit_execution_policy.py` -> `crypto_compatibility_wrapper_candidate` / `review_keep_temporarily_or_migrate_into_crypto_options_app` / `crypto_options_app/compatibility/app/data/pipelines/crypto/options/exit_execution_policy.py`
- ... `514` additional paths in JSON artifact

## Next Actions
- Review the JSON path_entries list before moving files.
- Create a branch/commit plan that separates crypto active work from reference moves.
- Decide which crypto compatibility wrappers must stay until runtime routes are fully cut over.
- Create GitHub milestones/issues after the cleanup plan is reviewable.
- Start fixed chats only after cleanup and issue source-of-truth are established.
