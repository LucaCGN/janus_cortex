# Crypto Options Repo Cleanup Batches

- Generated: `2026-06-07T05:00:39.230778+00:00`
- Status: `degraded`
- Source inventory: `2026-06-07T05:00:38.799750+00:00`
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
- Entries: `402`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Establish active crypto baseline and focused tests before reference moves.
  - `crypto_options_app/artifacts/automation/codex-signal-v5-complete-20260605T191003Z.err.txt` -> `none`
  - `crypto_options_app/artifacts/automation/codex-signal-v5-complete-20260605T191003Z.out.json` -> `none`
  - `crypto_options_app/artifacts/automation/codex-signal-v5-retry-complete-20260605T192649Z.err.txt` -> `none`
  - `crypto_options_app/artifacts/automation/codex-signal-v5-retry-complete-20260605T192649Z.out.json` -> `none`
  - `crypto_options_app/artifacts/automation/option_price_capture_active_process.json` -> `none`
  - `crypto_options_app/artifacts/automation/option_price_capture_status.json` -> `none`
  - `crypto_options_app/artifacts/automation/postgres_hot_sync_status.json` -> `none`
  - `crypto_options_app/artifacts/automation/profile_distribution_external_probe_status.json` -> `none`
  - ... `394` additional paths in JSON artifact

### Local State And Root Config Review
- ID: `batch_1_local_state_root_config_review`
- Branch: `codex/crypto-repo-local-state-cleanup`
- Entries: `1`
- Review required: `1`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Decide ignore/archive behavior for local automation state and root config churn.
  - `requirements.txt` -> `none`

### WNBA/NBA Reference Move
- ID: `batch_2_wnba_nba_reference_move`
- Branch: `codex/crypto-repo-wnba-nba-reference`
- Entries: `0`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Move reviewed sports-bot references into wnba_nba_app_reference without deleting work.

### Global Legacy Reference Move
- ID: `batch_3_global_reference_move`
- Branch: `codex/crypto-repo-global-reference`
- Entries: `0`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Move reviewed non-crypto Janus references into global_app_reference.

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
- Entries: `0`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Existing reference-root files require no move.

### Manual Review
- ID: `manual_review`
- Branch: `codex/crypto-repo-manual-review`
- Entries: `0`
- Review required: `0`
- Move candidates: `0`
- Move allowed now: `False`
- Purpose: Paths that do not fit a safe automatic batch.

## Next Actions
- Review and commit the current batch branch before opening reference-move branches.
- Keep reference moves separate from active crypto baseline and local-state cleanup.
- Hold root dependency changes until their owning app/tooling branch is clear.
- Review batch_4 compatibility wrappers before moving any wrapper paths.
- Create GitHub milestones/issues only after cleanup branches are reviewable.
