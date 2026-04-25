# Unified Benchmark Contract

## Purpose
Define the single replay-aware benchmark contract that all active strategy lanes must target.

This contract is additive:
- the replay-engine lane still owns the first replay contract
- this layer does not replace lane-specific artifacts
- this layer normalizes those artifacts so locked baselines, deterministic and HF candidates, ML candidates, and LLM candidates can sit on one honest scoreboard

## Source-Of-Truth Order
1. the replay lane owns the first shared replay contract:
   - [replay_contract_current.md](</C:/code-personal/janus-local/janus_cortex/shared/benchmark_contract/replay_contract_current.md>)
2. the locked controller state remains:
   - [current_analysis_system_state.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/current_analysis_system_state.md)
3. this document defines the project-wide comparison layer above those published artifacts

## Current Contract Version
- unified benchmark schema version: `integration_v1`
- compare-ready criteria version: `compare_ready_v1`
- submission example version: `submission_example_v1`
- snapshot date: `2026-04-24`
- replay contract maturity currently consumed by this layer: `execution_replay_v1_2`

Interpretation:
- replay is now the realism baseline
- standard backtest counts remain visible, but they are not treated as executable truth
- live observed results stay separate from replay so missing live runs are not mislabeled as zeros

## Shared Publication Paths
Use the shared Codex space, not repo-root temp files.

### Contract Docs
- `C:\code-personal\janus-local\janus_cortex\shared\benchmark_contract\`

### Lane Reports
- `C:\code-personal\janus-local\janus_cortex\shared\reports\<lane>\`

### Lane Artifacts
- `C:\code-personal\janus-local\janus_cortex\shared\artifacts\<lane>\`

### Lane Handoffs
- `C:\code-personal\janus-local\janus_cortex\shared\handoffs\<lane>\`

## Canonical Result Modes
Every normalized candidate row now carries three explicit result views:

### `standard_backtest`
- raw research-mode trade counts and bankroll path before replay execution realism

### `replay_result`
- replay-executed trades, replay bankroll, replay drawdown, and execution rate
- this is the realism baseline for challenger ranking

### `live_observed`
- real live-run observations when they exist
- if a candidate was not run live, the lane should publish `live_observed_flag=false`
- missing live runs should stay separate from `0` observed trades

## Required Lane Submission Shape
Future ML and LLM lanes should publish:
- `shared/reports/<lane>/benchmark_submission.json`

That submission is a compact manifest that points at existing artifacts instead of inventing a second benchmark world.

### Strict Submission Skeleton
```json
{
  "schema_version": "submission_example_v1",
  "lane_id": "ml-trading",
  "lane_label": "ML trading",
  "lane_type": "ml",
  "published_at": "2026-04-24T21:30:00+00:00",
  "comparison_scope": {
    "season": "2025-26",
    "phase_group": "play_in,playoffs",
    "replay_contract_ref": "shared/benchmark_contract/replay_contract_current.md",
    "benchmark_contract_ref": "shared/benchmark_contract/unified_benchmark_contract_current.md"
  },
  "subjects": [
    {
      "candidate_id": "ml_ranker_v1",
      "display_name": "ml_ranker_v1",
      "candidate_kind": "ml_strategy",
      "subject_type": "candidate",
      "publication_state": "published",
      "result_views": {
        "standard_backtest": {
          "trade_count": 8,
          "ending_bankroll": 13.18,
          "avg_return_with_slippage": 0.121,
          "compounded_return": 0.318,
          "max_drawdown_pct": 0.21,
          "max_drawdown_amount": 2.1
        },
        "replay_result": {
          "trade_count": 3,
          "ending_bankroll": 12.44,
          "avg_return_with_slippage": 0.149,
          "compounded_return": 0.244,
          "max_drawdown_pct": 0.18,
          "max_drawdown_amount": 1.8,
          "no_trade_count": 5,
          "execution_rate": 0.375
        },
        "live_observed": {
          "live_observed_flag": false
        }
      },
      "replay_realism": {
        "trade_gap": -5,
        "execution_rate": 0.375,
        "realism_gap_trade_rate": 0.625,
        "blocked_signal_count": 5,
        "stale_signal_suppressed_count": 4,
        "stale_signal_suppression_rate": 0.5,
        "stale_signal_share_of_blocked_signals": 0.8,
        "top_no_trade_reason": "signal_stale"
      },
      "trace_artifacts": {
        "decision_trace_json": "ml_ranker_v1_decisions.json",
        "attempt_trace_csv": "ml_ranker_v1_attempt_trace.csv"
      },
      "artifacts": {
        "report_markdown": "ml_ranker_v1_report.md"
      }
    }
  ]
}
```

Published example files:
- `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\ml_benchmark_submission_example.json`
- `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\llm_benchmark_submission_example.json`

## Canonical Normalized Candidate Fields
The dashboard normalizes everything into one candidate row.

### Identity
- `candidate_id`
- `display_name`
- `lane_id`
- `lane_label`
- `lane_type`
- `candidate_kind`
- `dashboard_bucket`
- `baseline_locked_flag`
- `publication_state`
- `comparison_ready_flag`

### Result Views
- `standard_result`
- `replay_result`
- `live_observed_result`

### Replay Realism
- `replay_realism.trade_gap`
- `replay_realism.execution_rate`
- `replay_realism.realism_gap_trade_rate`
- `replay_realism.blocked_signal_count`
- `replay_realism.stale_signal_suppressed_count`
- `replay_realism.stale_signal_suppression_rate`
- `replay_realism.stale_signal_share_of_blocked_signals`
- `replay_realism.top_no_trade_reason`

### Gate And Audit Metadata
- `compare_ready_checks`
- `artifact_paths`
- `comparison_scope`
- `notes`

## Metric Definitions
- `execution_rate`
  - `replay_trade_count / standard_trade_count`
  - null when the standard benchmark emitted `0` trades
- `realism_gap_trade_rate`
  - `(standard_trade_count - replay_trade_count) / standard_trade_count`
  - this is the default replay realism gap shown on the dashboard
- `stale_signal_suppression_rate`
  - `stale_signal_suppressed_count / standard_trade_count`
  - this is the explicit share of standard trades lost to stale signals
- `stale_signal_share_of_blocked_signals`
  - `stale_signal_suppressed_count / blocked_signal_count`
  - this shows whether stale signals are the dominant replay divergence cause
- `live_vs_backtest_gap_trade_rate`
  - `(standard_trade_count - live_trade_count) / standard_trade_count`
  - only meaningful when live observations exist
- `trade_gap`
  - `replay_trade_count - standard_trade_count`
  - negative values mean replay suppressed or rejected trades that the standard backtest counted

## Exact Compare-Ready Gate
See the detailed gate here:
- [benchmark_compare_ready_criteria.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/benchmark_compare_ready_criteria.md)

Summary:
1. lane publishes a shared handoff
2. lane publishes `benchmark_submission.json` or replay artifacts are synthesized into that shape
3. candidate has a non-zero standard trade sample
4. candidate has at least one replay-executed trade
5. replay ending bankroll and replay drawdown are published
6. stale-signal suppression count and rate are published or derivable
7. at least one accepted trace artifact path is referenced
8. live observed state is explicit: either live data exists or `live_observed_flag=false`

If those conditions are not met, the lane may appear in readiness tables, but the candidate does not join the replay-aware finalist set.

## Promotion Buckets
Meeting the compare-ready gate is necessary, but it is no longer the same thing as operator promotion.

- `live_ready`
  - current production-ready routing stack
  - current examples: the locked controller pair
- `live_probe`
  - replay-backed candidates that are the next probe tier when the executor can route them
  - current examples: `quarter_open_reprice`, `micro_momentum_continuation`
- `shadow_only`
  - compare-ready rows that stay visible and rankable, but should not advance beyond shadow review
  - current examples: `inversion`, `lead_fragility`, ML sidecar ranking/calibration rows, constrained LLM v2 rows
- `bench_only`
  - published rows that remain useful for audit, but should not be promoted globally
  - current examples: replay families explicitly marked `bench` plus rows that still fail the shared compare-ready gate

## Current Published State On April 24, 2026
- locked baselines:
  - `controller_vnext_unified_v1 :: balanced`
  - `controller_vnext_deterministic_v1 :: tight`
- current live-ready stack:
  - `controller_vnext_unified_v1 :: balanced`
  - `controller_vnext_deterministic_v1 :: tight`
- replay-aware compare-ready deterministic and HF challengers now include:
  - `inversion`
  - `quarter_open_reprice`
  - `micro_momentum_continuation`
- replay live-probe tier now includes:
  - `quarter_open_reprice`
  - `micro_momentum_continuation`
- replay shadow-only candidates now include:
  - `inversion`
  - `lead_fragility`
- replay bench-only candidates still visible include:
  - `winner_definition`
  - `halftime_gap_fill`
  - `panic_fade_fast`
  - `q4_clutch`
  - `underdog_liftoff`
- dominant current divergence cause:
  - `signal_stale`
- clearest promising new HF family from the replay lane:
  - `quarter_open_reprice`
- ML lane:
  - compare-ready shared submissions are now visible on the unified dashboard
  - current promotion bucket is `shadow_only`, because ML remains sidecar-only for ranking and calibration
  - strict example files remain published so future ML variants do not fork the schema
- LLM lane:
  - constrained compare-ready shared submissions are visible now
  - current top shared candidate is `llm_template_compiler_core_windows_v2`
  - current promotion bucket is `shadow_only`, because the lane recommendation still says shadow validation

## Finalist Rule
The dashboard does not hide ranking logic behind a composite black-box score.

Current replay-aware finalist rule:
1. always keep the locked baseline controllers visible
2. only compare-ready challengers can join the finalist set
3. rank challengers by:
   - replay ending bankroll
   - replay execution rate
   - replay drawdown control
   - stale-signal suppression strength as the next tie-breaker

## Current Delivery Surfaces
- dashboard route:
  - `GET /v1/analysis/studio/benchmark-dashboard`
- studio page:
  - `GET /analysis-studio`
- shared export command:
  - `python tools/export_benchmark_dashboard.py`

## Non-Goals
- creating a benchmark stack that bypasses the replay-engine contract
- letting each lane redefine replay realism independently
- ranking unpublished ML or LLM ideas as if they were already execution-aware
- replacing lane-specific artifacts with a lossy summary-only dashboard
