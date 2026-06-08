# Crypto Options Compatibility Wrapper Decision Plan

- Generated: `2026-06-07T05:45:27.356003+00:00`
- Status: `review`
- Source audit: `2026-06-07T05:45:17.676825+00:00`
- Manual orders avoided: `True`
- Live trading authorized: `False`

## Summary
- Wrapper candidates: `131`
- Active import blockers: `0`

## Gates
- `active_import_blockers_cleared`: `True`
- `non_active_wrapper_decisions_reviewable`: `True`
- `github_issue_creation_can_start_after_commit`: `True`
- `frontend_fixed_chat_ready`: `True`
- `signal_strategy_fixed_chat_ready`: `False`
- `signal_strategy_fixed_chat_wait_reason`: `GitHub milestones/issues must be created first.`

## Decision Buckets
- `runtime_import_audit_before_archive`: `10`
- `strategy_signal_wrapper_archive_after_tests`: `45`
- `replace_cli_wrapper_with_central_entrypoint`: `23`
- `review_legacy_duplicate_tests`: `49`
- `move_legacy_docs_to_reference`: `3`
- `archive_or_remove_after_tests`: `0`
- `manual_import_audit`: `1`

## Recommended Decision Counts
- `hold_for_import_audit`: `1`
- `hold_for_runtime_import_audit`: `10`
- `hold_for_strategy_signal_import_cutover`: `45`
- `migrate_or_drop_duplicate_test_after_active_suite_mapping`: `49`
- `migrate_to_crypto_options_docs_or_reference`: `3`
- `replace_with_crypto_options_app_script_entrypoint`: `23`

## Wrapper Samples
- `app/api/routers/crypto_options_market_data.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/api/routers/crypto_options_signals.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/crypto/__init__.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/crypto/candles.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/crypto/reference.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/polymarket/crypto/__init__.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/polymarket/crypto/accounts.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/polymarket/crypto/history.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/polymarket/crypto/live_capture.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/nodes/polymarket/crypto/markets.py` -> `runtime_import_audit_before_archive` (prove_runtime_router_or_data_node_is_not_imported_before_archiving)
- `app/data/pipelines/crypto/__init__.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/__init__.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/audit.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/backtests.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/candidate_report.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/cashout_simulator.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/contracts.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/engine.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/exit_execution_policy.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/indicators.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/labels.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/lane_system.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/live_micro_executor.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- `app/data/pipelines/crypto/options/live_review.py` -> `strategy_signal_wrapper_archive_after_tests` (archive_legacy_pipeline_or_service_after_signal_strategy_tests)
- ... `107` additional wrappers in JSON artifact

## Next Actions
- Commit this decision plan so Batch 4 has a reviewable source of truth.
- Create GitHub milestones/issues from github_issue_milestone_plan.md after this branch is reviewable.
- Keep frontend fixed chat eligible now; start signal/strategy cleanup only after GitHub issues exist.
- Do not delete wrappers in bulk; archive or replace categories in the decision order with focused tests.
