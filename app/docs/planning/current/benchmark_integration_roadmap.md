# Benchmark Integration Roadmap

## Purpose
Define the benchmark-control lane that keeps the active strategy lanes comparable without inventing a separate benchmark world.

Active lanes:
1. replay engine plus deterministic and higher-frequency strategies
2. ML trading
3. LLM strategy

This lane owns:
- the unified benchmark and reporting contract above those lanes
- the shared dashboard and export layer
- compare-ready and merge criteria
- doc coherence as milestone branches land

## Current State On April 24, 2026
- replay-engine lane has published the first shared replay contract:
  - [replay_contract_current.md](</C:/code-personal/janus-local/janus_cortex/shared/benchmark_contract/replay_contract_current.md>)
- replay is now the realism baseline for global comparison
- standard backtest counts remain visible, but they overstate executable trade counts
- `signal_stale` is the dominant current divergence cause
- `quarter_open_reprice` is the clearest promising new higher-frequency family from the replay lane
- `micro_momentum_continuation` is now in the same replay-backed compare-ready HF tier
- the benchmark-control dashboard exists at:
  - `/analysis-studio`
- the normalized payload exists at:
  - `/v1/analysis/studio/benchmark-dashboard`
- the shared export now publishes:
  - unified dashboard JSON and markdown
  - compare-ready criteria JSON and markdown
  - current promoted stack note
  - milestone merge plan
  - strict ML and LLM submission example files
- compare-ready published challengers now come from locked baselines, replay-engine artifacts, ML v2 sidecars, and constrained LLM v2 submissions
- current live-ready stack is still only the locked controller pair
- current live-probe tier is `quarter_open_reprice` plus `micro_momentum_continuation`
- ML is compare-ready as sidecar, but remains `shadow_only` operationally while deterministic routing stays primary
- LLM is compare-ready, but remains `shadow_only` operationally while the lane recommendation still points at shadow validation

## Milestones

| Milestone | Meaning | Status | Notes |
| --- | --- | --- | --- |
| `B0` | wait for first shared replay contract | completed | replay lane published the first shared contract; current exported maturity is `execution_replay_v1_2` |
| `B1` | normalize replay artifacts into one dashboard payload | completed | locked baselines and replay challengers now share one comparison layer |
| `B2` | publish shared export and lane coordination artifacts | completed | integration lane writes dashboard snapshot, criteria, and example files into shared space |
| `B3` | onboard ML and LLM lane submissions | completed | ML and LLM submissions now ingest under one contract without schema churn |
| `B4` | milestone review and merge-control loop | in progress | replay merges first, ML sidecar scope merges second, and LLM stays wait/shadow until validation clears |

## What This Lane Must Keep Stable
- the replay-engine lane owns the first replay semantics
- replay remains the realism baseline for ranking and merge review
- the benchmark layer remains additive over published artifacts
- locked baselines stay visible even when challengers are immature
- compare-ready ranking stays separate from operator promotion buckets (`live_ready`, `live_probe`, `shadow_only`, `bench_only`)
- docs must state exactly which lanes are compare-ready and which are still pending

## Global Compare Maturity Gate
A lane is globally comparable only when all of these are true:
1. a shared handoff exists under `shared/handoffs/<lane>/status.md`
2. a shared summary manifest exists or the integration lane can synthesize the lane from published artifacts
3. standard and replay trade counts are both published
4. replay ending bankroll and replay drawdown are both published
5. stale-signal suppression count and rate are published or derivable
6. at least one trace path is published for candidate audit
7. live trade counts are published when live data exists, or `live_observed_flag=false` is explicit

## Branch Merge Gate
A milestone branch is ready for an integration PR when:
1. the code branch passes its focused tests
2. the shared benchmark submission or artifact package is updated in the shared Codex space
3. the handoff status file names the exact contract version it targeted
4. the branch does not silently redefine trade-count, realism-gap, or drawdown semantics
5. the dashboard shows the lane as compare-ready without local ad hoc patches

If those conditions are not met, this lane should prepare a merge plan, not an integration PR.

## Current Merge Plan By Lane

### Replay Engine + Deterministic/HF
- nearest merge unit:
  - replay contract
  - replay runner code
  - higher-frequency family registry additions
  - replay tests
- current merge posture:
  - prepare one milestone PR when the branch is explicitly handed off for merge
  - do not merge controller-family expansion into the live router
- currently visible compare-ready challengers:
  - `inversion`
  - `quarter_open_reprice`
  - `micro_momentum_continuation`
- current replay shadow-only challenger:
  - `lead_fragility`
- current replay bench-only families still waiting:
  - `winner_definition`
  - `halftime_gap_fill`
  - `panic_fade_fast`
  - `q4_clutch`
  - `underdog_liftoff`
- why one PR:
  - the replay reports depend on those code changes landing together

### ML Trading
- current posture:
  - no integration PR yet
  - compare-ready as a bounded sidecar-only contribution
  - keep ML in `shadow_only` operationally while deterministic routing remains primary
- minimum next publish:
  - explicit branch handoff tied to the current shared submission
  - proof that calibration stays sidecar-only and does not silently expand into sizing or hard gates

### LLM Strategy
- current posture:
  - compare-ready on the unified dashboard, but still waiting on a code-side milestone handoff
  - operational posture remains `shadow_only`
- current strongest shared candidates:
  - `llm_template_compiler_core_windows_v2`
  - `llm_selector_core_windows_v2`
- minimum next publish:
  - code milestone handoff aligned to the shared benchmark submission
  - runtime or cost note if model calls remain in the deployed path

## Current Finalist Read
Current shortlist rule:
1. keep the two locked baselines in view
2. add only compare-ready challengers
3. order challengers by replay bankroll, execution rate, drawdown, then stale-signal suppression

As of `2026-04-24`, the strongest published non-baseline replay challenger is:
- `inversion`

The current replay-backed higher-frequency compare-ready tier is:
- `quarter_open_reprice`
- `micro_momentum_continuation`

Current operator-promoted stack:
- live-ready:
  - `controller_vnext_unified_v1 :: balanced`
  - `controller_vnext_deterministic_v1 :: tight`
- live-probe:
  - `quarter_open_reprice`
  - `micro_momentum_continuation`
- shadow-only:
  - replay: `inversion`, `lead_fragility`
  - ML: `ml_controller_focus_calibrator_v2`, `ml_sidecar_union_v2`, `ml_focus_family_reranker_v2`
  - LLM: `llm_template_compiler_core_windows_v2`, `llm_selector_core_windows_v2`

## Immediate Next Actions
1. keep exporting the shared dashboard snapshot after benchmark-lane changes
2. ask ML and LLM lanes to copy the strict published submission examples rather than inventing custom scorecards
3. review the replay-engine branch once its code milestone is explicitly handed off for merge
4. update README and current-state docs whenever the compare-ready set changes

## Deliverables From This Lane
- [unified_benchmark_contract.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/unified_benchmark_contract.md)
- [benchmark_compare_ready_criteria.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/benchmark_compare_ready_criteria.md)
- `/analysis-studio`
- `/v1/analysis/studio/benchmark-dashboard`
- `tools/export_benchmark_dashboard.py`
- shared coordination artifacts under:
  - `C:\code-personal\janus-local\janus_cortex\shared\handoffs\benchmark-integration\`
  - `C:\code-personal\janus-local\janus_cortex\shared\reports\benchmark-integration\`
  - `C:\code-personal\janus-local\janus_cortex\shared\artifacts\benchmark-integration\`
