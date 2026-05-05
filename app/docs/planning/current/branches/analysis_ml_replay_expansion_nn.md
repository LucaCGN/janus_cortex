# Analysis ML Replay Expansion And Neural Sidecar

## Branch
- recommended branch: `codex/analysis-ml-replay-expansion-nn`
- category: analysis core
- milestone target: `v1.5.4`

## Why This Branch Exists
The current ML lane is compare-ready but still sidecar-only. The limiting factor is no longer raw NBA game count; the database has `1224` finished regular-season games and the replay manifest covers them. The limiting factor is replay-labeled candidate density: the current regular-season replay training artifact has `743` focused replay-labeled candidate rows across `348` games.

This branch expands the replay-labeled ML sample before testing neural methods. Neural-network experiments must be judged on holdout stability and live-shadow compatibility, not headline bankroll.

## Owns
- full regular-season replay label generation for ML training
- replay artifact builder changes needed to create `full_regular_execution_replay_v1`
- ML feature extraction and shadow-sidecar model comparisons
- PyTorch tabular sidecar experiments
- optional state-window scalping label builder if candidate-level labels are insufficient
- ML lane benchmark submission and shared handoff updates

## Does Not Own
- live order placement
- Polymarket account, allowance, or portfolio execution code
- daily live-test run selection
- promotion of ML into execution authority
- LLM prompt or model-routing changes
- broad new deterministic strategy-family discovery

## Required Reads
- `app/docs/planning/current/branch_strategy.md`
- `app/docs/planning/current/agent_operating_rules.md`
- `shared/pipeline/README.md`
- `shared/pipeline/tasks/ml-trading-lane.md`
- `shared/benchmark_contract/replay_contract_current.md`
- `shared/benchmark_contract/unified_benchmark_contract_current.md`
- `shared/reports/replay-engine-hf/ranked_memo.md`
- `shared/reports/benchmark-integration/unified_benchmark_dashboard.md`
- `shared/reports/ml-trading-lane/research_memo.md`

## Starting Evidence
- full regular-season games in DB: `1224`
- regular-season replay manifest games: `1224`
- current regular-season replay-labeled ML candidate rows: `743`
- current all-season ML dataset: `854` rows
- current phase holdout: `743` regular-season training rows, `111` play-in/playoff holdout rows
- current top ML sidecars:
  - `ml_focus_family_reranker_v2`: replay bankroll `29.5087`, replay trades `5`, shadow-only
  - `ml_sidecar_union_v2`: replay bankroll `29.5087`, replay trades `5`, shadow-only
  - `ml_controller_focus_calibrator_v2`: replay bankroll `18.1158`, replay trades `2`, shadow-only

## Progress
- `2026-05-04`: built `shared/artifacts/replay-engine-hf/2025-26/full_regular_execution_replay_v1`.
- Expanded regular-season replay labels from `743` focused rows to `4976` rows across `12` subjects.
- Expanded artifact coverage: `1224` finished regular-season games, `1198` state-panel games, `26` derived-bundle games.
- Replay engine full-regular run now uses cached per-game state/tick lookups to avoid repeated per-poll table scans.
- ML handoff report: `shared/reports/ml-trading-lane/sample_coverage_report.md`.

## Subphases
1. `sample_audit`
   - count finished games, linked markets, state-panel games, standard candidates, replay-labeled candidates, and blockers by phase
   - publish a sample coverage report under `shared/reports/ml-trading-lane/`
2. `full_regular_replay_labels`
   - build `full_regular_execution_replay_v1`
   - include all deterministic families and controller candidates, not just the focused families
   - keep output reproducible through explicit family/controller scope flags
3. `candidate_level_ml`
   - rerun current logistic/OLS sidecars on the expanded sample
   - compare against the current `ml_*_v2` sidecars and deterministic replay families
4. `neural_sidecar_v1`
   - add a small PyTorch tabular MLP sidecar
   - train on regular-season labels
   - evaluate on play-in/playoffs only
   - publish calibrated and raw outputs separately
5. `scalping_window_probe`
   - only if candidate-level density is still thin, build state-window labels for short-horizon price movement
   - keep this as a separate shadow subject, not a replacement for candidate reranking
6. `benchmark_publish`
   - write updated ML benchmark submission and handoff
   - refresh benchmark dashboard if needed

## Required Outputs
- `shared/artifacts/replay-engine-hf/2025-26/full_regular_execution_replay_v1/...`
- `shared/reports/ml-trading-lane/sample_coverage_report.md`
- `shared/artifacts/ml-trading-lane/2025-26/ml_neural_sidecar_v1/...`
- `shared/reports/ml-trading-lane/research_memo.md`
- `shared/reports/ml-trading-lane/benchmark_submission.json`
- `shared/handoffs/ml-trading-lane/status.md`

## Validation Gate
- full unit tests for changed replay and ML code
- replay artifact loads without custom local patches
- expanded ML dataset reports phase split and leakage prevention
- neural sidecar beats or matches current sidecar on holdout stability, not just bankroll
- no ML subject is marked `live_ready` or `live_probe`
- dashboard keeps ML in `shadow_only`

## Handoff
This branch hands off to:
- benchmark integration, for updated ranking and compare-ready ingestion
- daily live validation, for shadow payload logging only
- live second-round validation branch, only through published shared artifacts
