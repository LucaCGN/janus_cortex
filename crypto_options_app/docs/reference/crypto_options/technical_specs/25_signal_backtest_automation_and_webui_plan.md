# Signal Backtest Automation And Web UI Plan

Date: 2026-06-04

Status: implementation spec for P3 signal-validation lab. This layer is read-only. It must never authorize live orders, import trading executors, or mutate execution state.

## Objective

Build an automation-driven signal validation lab for the `master_hedge_grid_scalping` family before any new trading strategy implementation. The lab turns the signal taxonomy in `24_master_hedge_grid_scalping_signal_validation_plan.md` into a queue of independently owned, versioned, replay-safe signal variants.

The lab answers one question: which signal building blocks are reliable enough to be considered in the next trading-engine design round?

## Source Of Truth

- Signal taxonomy and first 23 V1 hypotheses: `24_master_hedge_grid_scalping_signal_validation_plan.md`.
- Centralized app root: `crypto_options_app`.
- Canonical DB: `crypto_options_app/data/crypto_options_data.sqlite`.
- Read-only API prefix: `/v1/crypto-options-app/signals`.
- GitHub implementation scope: P3 parent issue under #108 and #123/#134. Issue #47 remains research/history only.

## Non-Goals

- No live order placement.
- No strategy execution.
- No executor imports.
- No provider calls inside signal scripts.
- No performance tuning of a trading strategy.

`live_shadow_test` means evaluating signal outputs against current A/B/C data while remaining read-only.

## DB Design

The canonical DB owns signal validation state through these tables:

| Table | Purpose |
| --- | --- |
| `signal_specs` | One row per signal hypothesis from the registry, including family/type/sources/variant/version/purpose. |
| `signal_versions` | Version lineage, parent/supersedes links, and active/retired/promoted state. |
| `signal_queue_items` | Work queue with phase, status, owner, TTL, priority, attempts, and blocker history. |
| `signal_validation_runs` | One execution attempt by a worker over one queue item. |
| `signal_validation_results` | Phase result metrics: samples, hit rate, forward return, blockers, and metrics JSON. |
| `signal_observations` | Per-frame emitted-signal observations, expected direction, outcome, and hit field. |
| `signal_artifacts` | Review requests, design-review snapshots, reports, and generated artifacts. |

Queue states:

```text
QUEUED, OWNED, RUNNING, PASSED, FAILED, BLOCKED, RETIRED, PROMOTED
```

Ownership rules:

- The 5-minute worker must atomically mark exactly one eligible row `OWNED` before doing work.
- Lock TTL defaults to 10 minutes.
- Expired `OWNED` or `RUNNING` rows return to `QUEUED`.
- A worker must not process a row owned by another active owner.
- Failed or blocked rows may be re-claimed for a narrow retry, but design changes belong to the 15-minute reviewer.
- Initial first-batch bring-up may bulk-start every first-batch row as `RUNNING` so the Web UI and reviewer see the complete active validation universe at once. While bulk-started rows are unexpired, worker automation must monitor them and avoid serial duplicate claims.
- The worker queue must prioritize validation phase order before signal priority: finish `last_week_backtest` for the full first batch before advancing any variant to `last_month_backtest`, then repeat for random sampling and live shadow. This prevents one high-priority signal from consuming all automation passes while the rest of the batch remains untested.

## Runtime Contracts

Every signal script must follow the shared Pydantic-compatible contract:

```python
SPEC: SignalCandidateSpec

def evaluate(frame: SignalValidationFrame) -> SignalObservation:
    ...
```

Contract requirements:

- Inputs are replay-safe `SignalValidationFrame` objects built from A/B/C DB rows.
- Signal scripts read only frame data and static spec data.
- Signal scripts return structured observations; they do not write orders or call providers.
- Every result records family, type, sources, variant, version, purpose, phase, sample count, hit rate, average forward return, blockers, data freshness, and degraded-impact.
- Execution feasibility is simulated only when a signal implies order-like behavior.

## Backtest Engine

Required phases:

1. `last_week_backtest`
2. `last_month_backtest`
3. `random_sampling_backtest`
4. `live_shadow_test`

Frame rules:

- No lookahead: every source timestamp must be at or before `decision_at_utc`.
- A/B/C data is read from canonical DB tables and replay frames, not from providers.
- Signal quality and fillability are separate outputs.
- A phase can fail with structured blockers without changing unrelated variants.
- A passing phase queues the next phase for that same signal version.

Minimum result fields:

- `sample_count`
- `hit_rate`
- `average_forward_return`
- per-phase diversity metadata: distinct event count, distinct token count, distinct symbol count, distinct window count, and decision-span timestamps
- `blockers`
- `data_freshness`
- `impact_if_degraded`
- `orders_allowed=false`
- `live_trading_authorized=false`

## API And Web UI

Read-only API endpoints:

```text
GET  /v1/crypto-options-app/signals/catalog
GET  /v1/crypto-options-app/signals/catalog/{signal_id}
GET  /v1/crypto-options-app/signals/backtests
GET  /v1/crypto-options-app/signals/validation/status
GET  /v1/crypto-options-app/signals/validation/results
GET  /v1/crypto-options-app/signals/validation/queue
POST /v1/crypto-options-app/signals/validation/request-review
```

The Web UI at `/signals/backtests` must show:

| Column | Purpose |
| --- | --- |
| family | Signal family, initially `master_hedge_grid_scalping`. |
| type | Outcome, side start, grid spacing, hedge ratio, etc. |
| sources | Source block A/B/C and source names. |
| variant | Concrete hypothesis variant. |
| version | V1/V2/etc. |
| purpose | Strategy parameter, logic gate, stat, or price reference. |
| phase | Current validation phase. |
| status | Queue/result status. `PASSED` means phase-complete, not strategy-approved. |
| promotion | Strict strategy-promotion state: `PROMOTION_READY`, `STRUCTURAL_PASS`, `NEEDS_V2_REVIEW`, or `INCOMPLETE`. |
| owner | Current worker ownership. |
| hit rate | Latest phase hit rate. |
| sample count | Latest phase sample count. |
| distinct events | Distinct event coverage used by the strict promotion gate. |
| impact | Impact if degraded. |
| blockers | Current structured blocker list. |
| strict review | Promotion blockers such as synthetic frames, weak hit rate, negative forward return, or missing diversity. |
| review queue | Pending `review_request` artifacts so V2 design work and orphan-run reconciliation are visible without manual DB inspection. |
| next action | Worker/reviewer guidance. |

Promotion semantics:

- `queue_status=PASSED` only proves worker execution reached a terminal phase state.
- Only `promotion_state=PROMOTION_READY` can feed strategy prototypes.
- `promotion_state=STRUCTURAL_PASS` requires stricter replay/diversity proof before strategy use.
- `promotion_state=NEEDS_V2_REVIEW` requires a stricter variant or redesigned win criteria.
- Synthetic or structural-only evidence cannot be promoted directly.

Filters:

- signal type
- data block A/B/C
- status
- impact
- free-text search

The UI includes a read-only review request form. It writes a `review_request` artifact for the 15-minute reviewer; it does not control trading.

## Automations

### `crypto-options-signal-validator-worker`

Schedule: every 5 minutes.

Responsibilities:

- Read specs 24/25, P3 issues, DB queue, and health.
- Sync catalog rows into DB.
- Expire stale ownership.
- If an unexpired bulk-started first-batch run is active, monitor/report it and do not start duplicate serial work.
- Claim one eligible queue row by atomically marking it `OWNED`.
- Run the smallest required phase/test slice for that one variant.
- For first-batch or catch-up passes, the same script may run a bounded batch of atomically claimed rows with `--batch-size`. The initial V1 catch-up can use 23, but steady 5-minute automation should use a smaller batch such as 8 so a pass finishes before the next cadence and avoids DB contention. Each row still receives separate ownership, run, result, and observation records.
- Record run, results, observations, blockers, and artifacts.
- If the phase passes, queue the next phase.
- If the phase fails, record blocker details.
- If the failure is narrow and mechanical, queue a same-family next version only when the needed change is obvious and scoped.
- Never work a row that is currently owned and unexpired.
- Never enable live execution flags.

It does not create broad new hypotheses. It executes and iterates concrete variants.

### `crypto-options-signal-design-reviewer`

Schedule: every 15 minutes.

Responsibilities:

- Review aggregate status across all signal types, purposes, and data blocks.
- Detect uncovered critical signal purposes.
- Read `review_request` artifacts from the Web UI.
- Design new variants when an important type/source combination lacks a passing candidate.
- Retire duplicates and clearly weak variants.
- Update specs, GitHub issue comments, and queue rows.
- Produce periodic progress reports.
- Produce final "signal building blocks ready" report.

It owns design and coverage. It should not execute phase runs.

## GitHub Grounding

Create parent issue:

```text
[CRYPTO-APP-P3] Signal validation lab and automation-driven indicator proofing
```

Create child issues:

1. DB schema for signal specs, versions, queue ownership, validation runs, observations, and phase results.
2. Pydantic signal runtime contracts and per-signal script loader.
3. Replay-safe signal backtest engine for last week, last month, random sample, and live-shadow phases.
4. Signal Backtest Web UI and read-only API.
5. Five-minute worker automation for queued failed/incomplete signal variants.
6. Fifteen-minute design-review automation for variant invention, roadmap tracking, and GitHub/spec updates.
7. Final signal readiness report for trading-engine design.

Every issue must reference #108, #123/#134, and state that #47 is research/history only.

## Tests

Unit tests:

- Signal ID and filename contract.
- Pydantic model validation.
- Queue ownership and duplicate-owner prevention.
- Ownership TTL expiry.
- Phase status transitions.
- Version lineage fields.

Integration tests:

- Catalog rows persist to DB.
- Backtest runner builds no-lookahead frames from A/B/C data.
- Validation phases record structured results and observations.
- Web UI/API returns compact read-only status.
- Review requests are persisted as artifacts.

Automation tests:

- 5-minute worker claims only one variant.
- 5-minute worker skips unexpired owned rows.
- Failed variant can record blocker and remain isolated.
- 15-minute reviewer summarizes incomplete coverage.
- Both automations keep orders disabled and live trading unauthorized.

## Readiness Criteria

P3 is ready for trading-engine design only when:

- Each critical signal type has at least one passing candidate or a documented exception.
- A/B/C freshness and no-lookahead checks pass.
- Last-week, last-month, random-sample, and live-shadow phases are represented in reports.
- The Web UI shows queue, results, blockers, and ownership without manual DB inspection.
- The 5-minute and 15-minute automations are active and non-overlapping.
- Final signal readiness report explicitly lists promoted, blocked, retired, and remaining-risk variants.

Further orchestration and the transition from structural signal validation to strategy validation are defined in `26_signal_to_strategy_team_lead_automation_plan.md`.
