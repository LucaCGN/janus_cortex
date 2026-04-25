# Benchmark Compare-Ready Criteria

## Purpose
Define the exact gate a lane or candidate must clear before it joins the global replay-aware benchmark comparison set.

## Version
- criteria version: `compare_ready_v1`
- applies to schema version: `integration_v1`

## Lane Requirements
1. publish a handoff under `shared/handoffs/<lane>/status.md`
2. publish `shared/reports/<lane>/benchmark_submission.json`, or publish enough replay artifacts for the integration lane to synthesize that shape
3. name the replay contract and unified benchmark contract targeted by the submission

## Candidate Requirements
1. `standard_backtest.trade_count > 0`
2. `replay_result.trade_count > 0`
3. `replay_result.ending_bankroll` is present
4. `replay_result.max_drawdown_pct` is present
5. `replay_realism.stale_signal_suppressed_count` is present or derivable
6. `replay_realism.stale_signal_suppression_rate` is present or derivable
7. at least one accepted trace artifact path is referenced
8. live state is explicit:
   - publish `live_observed_flag=true` with live counts, or
   - publish `live_observed_flag=false` when no live run exists

## Accepted Trace Artifact Keys
- `decision_trace_json`
- `decision_trace_csv`
- `attempt_trace_json`
- `attempt_trace_csv`
- `signal_summary_json`
- `signal_summary_csv`
- `subject_trace_json`
- `subject_trace_csv`
- `trade_trace_json`
- `trade_trace_csv`
- `replay_signal_summary_csv`
- `replay_attempt_trace_csv`
- `trace_json`
- `trace_csv`

## Finalist Rule
1. locked baseline controllers always remain visible
2. only compare-ready challengers can join the replay-aware finalist set
3. challenger ranking is ordered by replay ending bankroll, replay execution rate, replay drawdown control, then stale-signal suppression strength

## Visibility Rule
The dashboard separates benchmarked rows into three visible buckets:

1. `compare_ready`
   - rows that clear the shared gate and are currently eligible for global finalist comparison
2. `shadow_only`
   - rows that clear the shared gate but should stay visible without finalist promotion
   - current examples are ML sidecar ranking/calibration rows and lower-priority replay live probes
3. `bench_only`
   - rows that remain published for audit but are not globally promotable
   - this includes replay families the replay lane still marks `bench`, plus any rows that fail the shared gate

For replay-lane HF families specifically, the integration layer should follow the replay lane's own recommendation order:
- top replay live-probe candidates become `compare_ready`
- lower-priority live probes stay `shadow_only`
- explicit replay `bench` recommendations stay `bench_only`

## Strict Submission Examples
- ML:
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\ml_benchmark_submission_example.json`
- LLM:
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\llm_benchmark_submission_example.json`

## Operational Interpretation
- standard backtest remains visible for context, but it is not the realism baseline
- replay result is the executable comparison surface
- live observed stays separate so reviewers can distinguish a real `0` live-trade result from a candidate that was never run live
