# Branch Plan: `codex/analysis-portfolio-robustness`

## Role
Follow-on analysis branch for repeated-seed robustness and the first combined keep-family sleeve on top of the frozen sequential bankroll contract.

## Target Milestone
- `v1.4.1`

## Depends On
- `codex/analysis-sequential-portfolio-benchmarking`

## Owns
- repeated-seed robustness on the current sequential five-family benchmark set
- deterministic combined-sleeve construction from the `keep` set
- benchmark artifact and markdown updates needed to expose both outputs
- reference and planning updates that freeze the robustness results

## Does Not Own
- new raw strategy families
- model training or feature changes
- live-serving logic
- frontend visualization beyond documenting the read-only follow-on need
- LLM implementation

## Current Status
- `R1` complete:
  - benchmark contract moved to `v4`
  - repeated-seed robustness artifacts now exist across the full five-family set
- `R2` complete:
  - the surviving families `inversion` and `winner_definition` are both `stable_positive` across the 10-seed sweep
  - the non-surviving families now freeze as `stable_negative` or `mixed`, rather than being left outside the robustness lens
- `R3` complete:
  - the first combined sleeve `combined_keep_families` now replays `inversion,winner_definition` through one shared bankroll path
- `R4` complete:
  - markdown, reference docs, and system-state docs now reflect the frozen robustness and combined-sleeve results
- current handoff:
  - decide whether the next branch should optimize sleeve allocation and priority rules or surface the sequential outputs in read-only visualization

## Subphases

### `R1` Repeated-Seed Robustness
Objective:
- prove the single-seed holdout result was not a one-off artifact for the current benchmark family set

Deliverables:
- deterministic seed list
- per-seed bankroll detail for each strategy family
- aggregated robustness label and dispersion summary

Validation:
- the same seed list produces identical robustness tables

### `R2` Combined Keep-Family Sleeve
Objective:
- replay the current `keep` families in one shared bankroll path

Deliverables:
- combined sleeve row in the sequential portfolio artifacts
- source-family tracking in the step ledger
- documented overlap-skip behavior

Validation:
- the combined sleeve is deterministic and explicitly reports overlap friction

### `R3` Freeze And Handoff
Objective:
- publish the robustness evidence and define the next branch decision cleanly

Deliverables:
- updated reference docs
- updated roadmap and system-state docs
- explicit next-step options:
  - allocation / priority optimization
  - read-only visualization

Validation:
- the branch can hand off without reopening strategy-threshold debates

## Merge Gate
- focused pytest sweep passes
- a real backend benchmark run emits the new robustness and combined-sleeve artifacts
- reference docs record the frozen seed set and current combined-sleeve interpretation

## Handoff
Next branch:
- allocation and priority rules inside `combined_keep_families`
- or read-only visualization of sequential steps, overlap skips, and robustness tables
