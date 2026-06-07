# Crypto Options Repo Cleanup Batches

- Generated: `2026-06-07T04:35:04.621148+00:00`
- Status: `degraded`
- Source inventory: `2026-06-07T04:34:52.750518+00:00`
- Manual orders avoided: `True`

## Gates
- `automatic_moves_allowed`: `False`
- `batch_0_ready_for_branch`: `True`
- `reference_move_ready`: `False`
- `fixed_chats_start_ready`: `False`
- `github_issue_creation_ready`: `False`

## Batches

### No-Move Active Crypto Baseline
- ID: `batch_0_active_crypto_baseline`
- Branch: `codex/crypto-transition-control-plane`
- Entries: `681`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Establish active crypto baseline and focused tests before reference moves.
  - `crypto_options_app/.env.postgres.example` -> `none`
  - `crypto_options_app/__init__.py` -> `none`
  - `crypto_options_app/api/__init__.py` -> `none`
  - `crypto_options_app/api/app.py` -> `none`
  - `crypto_options_app/api/routers/__init__.py` -> `none`
  - `crypto_options_app/api/routers/dashboard.py` -> `none`
  - `crypto_options_app/api/routers/health.py` -> `none`
  - `crypto_options_app/api/routers/signals.py` -> `none`
  - ... `673` additional paths in JSON artifact

### Local State And Root Config Review
- ID: `batch_1_local_state_root_config_review`
- Branch: `codex/crypto-repo-local-state-cleanup`
- Entries: `16`
- Review required: `16`
- Move candidates: `15`
- Move allowed now: `False`
- Purpose: Decide ignore/archive behavior for local automation state and root config churn.
  - `requirements.txt` -> `none`
  - `.codex_automation_memory/automations/crypto-options-frontend-developer/memory.md` -> `global_app_reference/.codex_automation_memory/automations/crypto-options-frontend-developer/memory.md`
  - `.codex_automation_memory/automations/crypto-options-indicator-dev/memory.md` -> `global_app_reference/.codex_automation_memory/automations/crypto-options-indicator-dev/memory.md`
  - `.codex_automation_memory/automations/crypto-options-indicator-qa/memory.md` -> `global_app_reference/.codex_automation_memory/automations/crypto-options-indicator-qa/memory.md`
  - `.codex_automation_memory/automations/crypto-options-signal-validator-worker/memory.md` -> `global_app_reference/.codex_automation_memory/automations/crypto-options-signal-validator-worker/memory.md`
  - `.codex_automation_memory/automations/crypto-options-strategy-dev/memory.md` -> `global_app_reference/.codex_automation_memory/automations/crypto-options-strategy-dev/memory.md`
  - `.codex_automation_memory/crypto-options-db-data-observability/memory.md` -> `global_app_reference/.codex_automation_memory/crypto-options-db-data-observability/memory.md`
  - `.codex_automation_memory/crypto-options-frontend-developer.md` -> `global_app_reference/.codex_automation_memory/crypto-options-frontend-developer.md`
  - ... `8` additional paths in JSON artifact

### WNBA/NBA Reference Move
- ID: `batch_2_wnba_nba_reference_move`
- Branch: `codex/crypto-repo-wnba-nba-reference`
- Entries: `3`
- Review required: `3`
- Move candidates: `3`
- Move allowed now: `False`
- Purpose: Move reviewed sports-bot references into wnba_nba_app_reference without deleting work.
  - `app/docs/planning/current/final_system/architecture/nba_wnba_parallel_runtime_plan_2026-05-30.md` -> `wnba_nba_app_reference/app/docs/planning/current/final_system/architecture/nba_wnba_parallel_runtime_plan_2026-05-30.md`
  - `app/docs/reference/postgame_evaluation_2026-05-30_nba_wnba_live_window.md` -> `wnba_nba_app_reference/app/docs/reference/postgame_evaluation_2026-05-30_nba_wnba_live_window.md`
  - `app/docs/reference/postgame_evaluation_2026-05-31_aces_valkyries_live_window.md` -> `wnba_nba_app_reference/app/docs/reference/postgame_evaluation_2026-05-31_aces_valkyries_live_window.md`

### Global Legacy Reference Move
- ID: `batch_3_global_reference_move`
- Branch: `codex/crypto-repo-global-reference`
- Entries: `69`
- Review required: `69`
- Move candidates: `69`
- Move allowed now: `False`
- Purpose: Move reviewed non-crypto Janus references into global_app_reference.
  - `app/api/main.py` -> `global_app_reference/app/api/main.py`
  - `app/api/models.py` -> `global_app_reference/app/api/models.py`
  - `app/api/routers/__init__.py` -> `global_app_reference/app/api/routers/__init__.py`
  - `app/api/routers/ops.py` -> `global_app_reference/app/api/routers/ops.py`
  - `app/api/routers/portfolio.py` -> `global_app_reference/app/api/routers/portfolio.py`
  - `app/data/nodes/polymarket/blockchain/manage_portfolio.py` -> `global_app_reference/app/data/nodes/polymarket/blockchain/manage_portfolio.py`
  - `app/docs/planning/current/final_system/architecture/janus_core_live_trading_runtime.md` -> `global_app_reference/app/docs/planning/current/final_system/architecture/janus_core_live_trading_runtime.md`
  - `app/docs/planning/current/final_system/automation/backlog_layers.md` -> `global_app_reference/app/docs/planning/current/final_system/automation/backlog_layers.md`
  - ... `61` additional paths in JSON artifact

### Crypto Compatibility Wrapper Decision
- ID: `batch_4_crypto_compatibility_wrapper_cutover`
- Branch: `codex/crypto-compatibility-wrapper-cutover`
- Entries: `131`
- Review required: `131`
- Move candidates: `131`
- Move allowed now: `False`
- Purpose: Keep or migrate crypto compatibility wrappers after import/runtime checks.
  - `app/api/routers/crypto_options_market_data.py` -> `crypto_options_app/compatibility/app/api/routers/crypto_options_market_data.py`
  - `app/api/routers/crypto_options_signals.py` -> `crypto_options_app/compatibility/app/api/routers/crypto_options_signals.py`
  - `app/data/nodes/crypto/__init__.py` -> `crypto_options_app/compatibility/app/data/nodes/crypto/__init__.py`
  - `app/data/nodes/crypto/candles.py` -> `crypto_options_app/compatibility/app/data/nodes/crypto/candles.py`
  - `app/data/nodes/crypto/reference.py` -> `crypto_options_app/compatibility/app/data/nodes/crypto/reference.py`
  - `app/data/nodes/polymarket/crypto/__init__.py` -> `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/__init__.py`
  - `app/data/nodes/polymarket/crypto/accounts.py` -> `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/accounts.py`
  - `app/data/nodes/polymarket/crypto/history.py` -> `crypto_options_app/compatibility/app/data/nodes/polymarket/crypto/history.py`
  - ... `123` additional paths in JSON artifact

### GitHub Source-Of-Truth Setup
- ID: `batch_5_github_source_of_truth_setup`
- Branch: `codex/crypto-github-workflow-setup`
- Entries: `0`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Prepare crypto milestones/issues after cleanup branches are reviewable.

### Reference Already Placed
- ID: `reference_already_placed`
- Branch: `none`
- Entries: `2`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Existing reference-root files require no move.
  - `global_app_reference/README.md` -> `none`
  - `wnba_nba_app_reference/README.md` -> `none`

### Manual Review
- ID: `manual_review`
- Branch: `codex/crypto-repo-manual-review`
- Entries: `0`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Paths that do not fit a safe automatic batch.

## Next Actions
- Create or switch to batch_0 branch before staging active crypto baseline.
- Keep reference moves separate from active crypto baseline.
- Review batch_4 compatibility wrappers before moving any wrapper paths.
- Create GitHub milestones/issues only after cleanup branches are reviewable.
