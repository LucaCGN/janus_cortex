# Crypto Options Compatibility Wrapper Audit

- Generated: `2026-06-07T05:37:34.655876+00:00`
- Status: `review`
- Manual orders avoided: `True`
- Live trading authorized: `False`

## Summary
- Wrapper candidates: `131`
- Wrappers referenced by active crypto code/tests: `0`
- Wrappers referenced only by docs/reference text: `52`
- Wrappers with no detected references: `60`
- Scanned files: `704`

## Gates
- `automatic_wrapper_moves_allowed`: `False`
- `active_import_cutover_required`: `False`
- `frontend_fixed_chat_can_start_after_batch_3`: `True`
- `signal_strategy_fixed_chat_requires_batch_4_and_github_source_of_truth`: `True`
- `github_source_of_truth_ready`: `False`

## Fixed Chat Prompt Paths
- `bootstrap`: `crypto_options_app/artifacts/team_coordination/fixed_chat_bootstrap.md`
- `prompt_root`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/`
- `frontend`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`
- `signal_strategy`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_strategy_management_cleanup.md`
- `db_data_observability`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/db_data_observability.md`
- `indicator_dev`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/indicator_dev.md`
- `indicator_qa`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/indicator_qa.md`
- `signal_dev`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_dev.md`
- `signal_qa`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/signal_qa.md`
- `strategy_dev`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/strategy_dev.md`
- `strategy_qa`: `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/strategy_qa.md`

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
- `hold_for_runtime_import_audit`: `10`
- `hold_for_strategy_signal_import_cutover`: `45`
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
- `app/data/nodes/crypto/__init__.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `1`)
  - `tests/app/services/crypto_options/test_service_pytest.py`
- `app/data/nodes/crypto/candles.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `3`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_price_stream_indicator_system_spec_2026-06-02.md`
  - `app/docs/reference/crypto_options_research_module.md`
  - `tests/app/services/crypto_options/test_service_pytest.py`
- `app/data/nodes/crypto/reference.py` -> `hold_for_runtime_import_audit` (active refs: `0`, all refs: `2`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_price_stream_indicator_system_spec_2026-06-02.md`
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
- `app/data/pipelines/crypto/__init__.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `8`)
  - `tests/crypto_options_app/test_compatibility_wrapper_audit_pytest.py`
  - `app/api/routers/crypto_options_market_data.py`
  - `app/api/routers/crypto_options_signals.py`
- `app/data/pipelines/crypto/options/__init__.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `7`)
  - `app/api/routers/crypto_options_market_data.py`
  - `app/api/routers/crypto_options_signals.py`
  - `app/services/crypto_options/service.py`
- `app/data/pipelines/crypto/options/audit.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `1`)
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/pipelines/crypto/options/backtests.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `2`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_options_replay_engine_data_spec_2026-06-02.md`
  - `app/docs/reference/crypto_options_research_module.md`
- `app/data/pipelines/crypto/options/candidate_report.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `1`)
  - `crypto_options_app/docs/reference/crypto_options/crypto_options_replay_engine_data_spec_2026-06-02.md`
- `app/data/pipelines/crypto/options/cashout_simulator.py` -> `hold_for_strategy_signal_import_cutover` (active refs: `0`, all refs: `0`)
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
- Review 60 no-reference wrappers for compatibility archive or removal after tests.
