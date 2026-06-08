# Crypto Options Compatibility Wrapper Audit

- Generated: `2026-06-07T05:17:36.675453+00:00`
- Status: `degraded`
- Manual orders avoided: `True`
- Live trading authorized: `False`

## Summary
- Wrapper candidates: `131`
- Wrappers referenced by active crypto code/tests: `23`
- Wrappers referenced only by docs/reference text: `52`
- Wrappers with no detected references: `57`
- Scanned files: `650`

## Gates
- `automatic_wrapper_moves_allowed`: `False`
- `active_import_cutover_required`: `True`
- `frontend_fixed_chat_can_start_after_batch_3`: `True`
- `signal_strategy_fixed_chat_requires_batch_4_and_github_source_of_truth`: `True`
- `github_source_of_truth_ready`: `False`

## Fixed Chat Prompt Paths
- `bootstrap`: `crypto_options_app/artifacts/team_coordination/fixed_chat_bootstrap.md`
- `frontend`: `crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md`
- `signal_strategy`: `crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md`

## Families
- `legacy_api_router`: `2`
- `legacy_cli_script_wrapper`: `23`
- `legacy_crypto_data_node`: `3`
- `legacy_crypto_documentation`: `3`
- `legacy_crypto_pipeline`: `1`
- `legacy_crypto_service`: `10`
- `legacy_crypto_test_wrapper`: `49`
- `legacy_options_pipeline`: `34`
- `legacy_polymarket_crypto_data_node`: `5`
- `legacy_tool_wrapper`: `1`

## Recommended Decisions
- `hold_for_import_audit`: `1`
- `hold_for_runtime_import_audit`: `8`
- `hold_for_strategy_signal_import_cutover`: `24`
- `keep_temporarily_cut_over_active_callers`: `23`
- `migrate_or_drop_duplicate_test_after_active_suite_mapping`: `49`
- `migrate_to_crypto_options_docs_or_reference`: `3`
- `replace_with_crypto_options_app_script_entrypoint`: `23`

## Wrapper Samples
- `app/api/routers/crypto_options_market_data.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `3`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_options_market_price_data_spec_2026-06-02.md`
  - `crypto_options_app/docs/reference/crypto_options/crypto_price_stream_indicator_system_spec_2026-06-02.md`
  - `crypto_options_app/docs/reference/crypto_options/technical_specs/00_current_state_inventory.md`
- `app/api/routers/crypto_options_signals.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `2`)
  - `crypto_options_app/docs/reference/crypto_options/technical_specs/00_current_state_inventory.md`
  - `tests/crypto_options_app/test_compatibility_wrapper_audit_pytest.py`
- `app/data/nodes/crypto/__init__.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `2`)
  - `crypto_options_app/scripts/run_crypto_options_research.py`
  - `tests/app/services/crypto_options/test_service_pytest.py`
- `app/data/nodes/crypto/candles.py` -> `keep_temporarily_cut_over_active_callers` (active refs: `1`, all refs: `4`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_price_stream_indicator_system_spec_2026-06-02.md`
  - `crypto_options_app/scripts/run_crypto_options_research.py`
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/nodes/crypto/reference.py` -> `keep_temporarily_cut_over_active_callers` (active refs: `1`, all refs: `3`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_price_stream_indicator_system_spec_2026-06-02.md`
  - `crypto_options_app/scripts/run_crypto_options_research.py`
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/nodes/polymarket/crypto/__init__.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `1`)
  - `tests/app/services/crypto_options/test_service_pytest.py`
- `app/data/nodes/polymarket/crypto/accounts.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `0`)
- `app/data/nodes/polymarket/crypto/history.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `2`)
  - `app/docs/reference/crypto_options_research_module.md`
  - `tests/app/services/crypto_options/test_service_pytest.py`
- `app/data/nodes/polymarket/crypto/live_capture.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `0`)
- `app/data/nodes/polymarket/crypto/markets.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `2`)
  - `app/docs/reference/crypto_options_research_module.md`
  - `tests/app/services/crypto_options/test_service_pytest.py`
- `app/data/pipelines/crypto/__init__.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `28`)
  - `crypto_options_app/scripts/run_crypto_options_candidate_report.py`
  - `crypto_options_app/scripts/run_crypto_options_cashout_simulation.py`
  - `crypto_options_app/scripts/run_crypto_options_decision_review.py`
- `app/data/pipelines/crypto/options/__init__.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `28`)
  - `crypto_options_app/scripts/run_crypto_options_candidate_report.py`
  - `crypto_options_app/scripts/run_crypto_options_cashout_simulation.py`
  - `crypto_options_app/scripts/run_crypto_options_decision_review.py`
- `app/data/pipelines/crypto/options/audit.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `1`)
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/pipelines/crypto/options/backtests.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `2`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_options_replay_engine_data_spec_2026-06-02.md`
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/pipelines/crypto/options/candidate_report.py` -> `keep_temporarily_cut_over_active_callers` (active refs: `1`, all refs: `2`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_options_replay_engine_data_spec_2026-06-02.md`
  - `crypto_options_app/scripts/run_crypto_options_candidate_report.py`
- `app/data/pipelines/crypto/options/cashout_simulator.py` -> `keep_temporarily_cut_over_active_callers` (active refs: `1`, all refs: `1`)
  - `crypto_options_app/scripts/run_crypto_options_cashout_simulation.py`
- `app/data/pipelines/crypto/options/contracts.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `1`)
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/pipelines/crypto/options/engine.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `1`)
  - `app/docs/reference/crypto_options_research_module.md`
- ... `113` additional wrappers in JSON artifact

## Next Actions
- Do not bulk-move compatibility wrappers while active crypto code still imports old app/codex_tool paths.
- Cut active callers over to crypto_options_app modules/scripts in small tested groups.
- Keep frontend fixed chat eligible after Batch 3; it must use existing prompt and avoid backend promotion/runtime changes.
- Hold signal/strategy cleanup fixed chat until Batch 4 import decisions and GitHub issue source-of-truth are ready.
- Resolve active references for 23 wrapper candidates before moving them.
- Review 57 no-reference wrappers for compatibility archive or removal after tests.
