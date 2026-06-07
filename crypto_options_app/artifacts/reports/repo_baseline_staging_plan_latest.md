# Crypto Options Batch 0 Baseline Staging Plan

- Generated: `2026-06-07T04:35:05.241530+00:00`
- Status: `ok`
- Manual orders avoided: `True`

## Summary
- Batch 0 paths: `681`
- Stage candidates: `295`
- Hold paths: `386`
- Manual review paths: `0`

## Stage Categories
- `hold_data_file`: `1`
- `hold_generated_artifact`: `205`
- `hold_generated_report`: `179`
- `hold_runtime_state`: `1`
- `stage_coordination_artifacts`: `11`
- `stage_docs_specs`: `48`
- `stage_source`: `174`
- `stage_tests`: `52`
- `stage_transition_reports`: `10`

## Git Actions
- `hold`: `386`
- `stage`: `295`

## Stage Candidate Samples
- `stage_source` `crypto_options_app/.env.postgres.example`
- `stage_source` `crypto_options_app/__init__.py`
- `stage_source` `crypto_options_app/api/__init__.py`
- `stage_source` `crypto_options_app/api/app.py`
- `stage_source` `crypto_options_app/api/routers/__init__.py`
- `stage_source` `crypto_options_app/api/routers/dashboard.py`
- `stage_source` `crypto_options_app/api/routers/health.py`
- `stage_source` `crypto_options_app/api/routers/signals.py`
- `stage_source` `crypto_options_app/api/routers/strategies.py`
- `stage_transition_reports` `crypto_options_app/artifacts/reports/repo_baseline_staging_plan_latest.json`
- `stage_transition_reports` `crypto_options_app/artifacts/reports/repo_baseline_staging_plan_latest.md`
- `stage_transition_reports` `crypto_options_app/artifacts/reports/repo_cleanup_batches_latest.json`
- `stage_transition_reports` `crypto_options_app/artifacts/reports/repo_cleanup_batches_latest.md`
- `stage_transition_reports` `crypto_options_app/artifacts/reports/repo_cleanup_inventory_latest.json`
- ... `281` additional `stage` paths in JSON artifact

## Hold Samples
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/codex-signal-v5-complete-20260605T191003Z.err.txt`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/codex-signal-v5-complete-20260605T191003Z.out.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/codex-signal-v5-retry-complete-20260605T192649Z.err.txt`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/codex-signal-v5-retry-complete-20260605T192649Z.out.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/option_price_capture_active_process.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/option_price_capture_status.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/postgres_hot_sync_status.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/profile_distribution_external_probe_status.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/profile_distribution_service_status.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/profile_distribution_service_status.json.3396.tmp`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/profile_distribution_status.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/signal_live_active_process.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/signal_live_status.json`
- `hold_generated_artifact` `crypto_options_app/artifacts/automation/stop_for_review_request.json`
- ... `372` additional `hold` paths in JSON artifact

## Next Actions
- Review stage candidates before staging Batch 0.
- Keep held runtime/data/generated artifacts unstaged unless explicitly promoted to source-of-truth.
- Add ignore/archive rules for recurring runtime outputs after confirming none are needed for source-of-truth.
- Stage Batch 0 candidates on codex/crypto-transition-control-plane only after this plan is accepted.
