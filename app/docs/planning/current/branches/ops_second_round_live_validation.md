# Ops Second-Round Live Validation

## Branch
- recommended branch: `codex/ops-second-round-live-validation`
- category: ops/runtime
- milestone target: `v1.5.3`

## Why This Branch Exists
The first-round work produced a live executor, replay-aware probes, ML/LLM shadow sidecars, account preflight checks, and a 95% take-profit guardrail. The second round should be used to collect live/shadow evidence under real playoff conditions with minimal risk.

This branch owns day-to-day live testing and small operational fixes. It should not broaden strategy research or build new ML models.

## Owns
- second-round live-test plans and runbooks
- account, feed, orderbook, and portfolio preflight checks
- one entries-enabled path per slate when preflight is green
- dry-run and shadow comparison for all non-selected candidates
- postgame reconciliation and issue routing
- small executor/runbook fixes needed to keep live validation safe

## Does Not Own
- ML training sample expansion
- neural-network experiments
- replay-family research beyond issue routing
- benchmark semantics or promotion criteria
- LLM prompt experimentation
- live promotion of a candidate without replay plus live evidence

## Required Reads
- `app/docs/planning/current/branch_strategy.md`
- `app/docs/planning/current/agent_operating_rules.md`
- `shared/pipeline/README.md`
- `shared/pipeline/tasks/daily-live-validation.md`
- `shared/reports/daily-live-validation/playoff_live_runbook.md`
- `shared/reports/benchmark-integration/current_promoted_stack.md`
- `shared/reports/replay-engine-hf/daily_live_probe_recommendations.md`
- `shared/reports/ml-trading-lane/daily_live_validation_handoff.md`
- `shared/handoffs/llm-strategy-lane/daily_live_validation.md`

## Starting Promotion Stack
- live-ready:
  - `controller_vnext_unified_v1 :: balanced`
  - `controller_vnext_deterministic_v1 :: tight`
- live-probe:
  - `quarter_open_reprice`
  - `micro_momentum_continuation`
- shadow-only:
  - `inversion`
  - `panic_fade_fast`
  - `lead_fragility`
  - ML sidecars
  - LLM selector/compiler sidecars
- bench-only:
  - stale-dominated and weak deterministic families

## Live-Test Rules
- use one entries-enabled real-money path per slate at most
- keep all parallel candidates dry-run or shadow-only
- enforce the live budget:
  - target entry size: `$1`
  - max entry orders per game: `2`
  - max requested entry notional per game: `$2`
  - Polymarket minimum shares still applies
- always take profit at `95%`
- do not open entries if feed, orderbook, portfolio, or account preflight is not green
- report real portfolio impact separately from dry-run and shadow outcomes

## Daily Subphases
1. `pregame_preflight`
   - discover slate
   - verify NBA feed, Polymarket mapping, orderbooks, account balance/allowances, open orders, and existing positions
   - write `live_test_plan_YYYY-MM-DD.md`
2. `selected_live_probe`
   - choose exactly one entries-enabled path for the slate
   - default to `quarter_open_reprice` unless controller-chat changes it
   - use dry-run if account/preflight is not green
3. `shadow_comparison`
   - run controller pair, secondary probe, inversion, ML, and LLM in shadow/dry-run
   - log exact blocker buckets for every non-trade
4. `in_game_monitoring`
   - notify only for first non-skip signal, order attempt/fill, budget gate, feed stall, orderbook blocker, internal bug, or game final
5. `postgame_truth`
   - reconcile orders, fills, portfolio impact, shadow decisions, replay expectation, and exact blocker categories
   - route issues to replay, benchmark, ML, LLM, or execution

## Required Outputs
- `shared/reports/daily-live-validation/live_test_plan_YYYY-MM-DD.md`
- `shared/reports/daily-live-validation/postgame_report_YYYY-MM-DD.md`
- `shared/artifacts/daily-live-validation/YYYY-MM-DD/session_summary.json`
- `shared/artifacts/daily-live-validation/YYYY-MM-DD/live_vs_replay_comparison.csv`
- `shared/handoffs/daily-live-validation/status.md`

## Validation Gate
- targeted execution tests pass after any code change
- no duplicate entries after restart/resume
- no entries when preflight is red
- every skipped signal has a concrete blocker reason
- 95% take-profit appears in trace/executor events when hit
- benchmark dashboard is refreshed after final postgame bundle

## Handoff
This branch hands off to:
- benchmark integration after each postgame bundle
- replay engine when live/replay divergence shows a fidelity gap
- ML branch only through published shadow payloads and postgame evidence
