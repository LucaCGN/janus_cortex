# Janus Live Runtime Scope Map - 2026-05-24

Status: current-scope backlog map
Parent issue: https://github.com/LucaCGN/janus_cortex/issues/63

## Purpose

Map the operator's live-runtime diagram and original prompt to the current Janus app state, automation topology, GitHub issues, and near-term backlog before expanding into crypto options markets or new model domains.

The diagram is treated as a system design, not as a new standalone strategy. Strategy lanes become signal producers. Janus owns live aggregation, event budgets, StrategyPlan revisions, worker execution gates, and postgame learning.

## Diagram Flow To System Components

| Diagram element | Current state | Gap | Issue/backlog owner |
|---|---|---|---|
| `Pregame Planning Aggregation` | Pregame planning exists conceptually and StrategyPlan bootstrap now covers missing plans. | NBA/WNBA pregame research must become structured optional priors with expiry and no liveness dependency. | #72 |
| `Janus System Eye` | Live worker, StrategyPlan gate, event-scoped inventory proof, CLOB sampling, LLM trace persistence, live preflight, normalized snapshots, and first aggregation artifacts exist. | Needs live-worker adoption of aggregation/budget decisions so independent sleeve blockers stay local. | #63 |
| `Game Flow` checkpoints | Worker ticks every 30s, StrategyPlan sleeves evaluate live state, runtime controls can persist event config/readbacks, and #70 added postgame replay/config review. | Quarter/HT/score-run/material-price-change checkpoints need fuller signal-producer adoption in the worker. | #63 / focused follow-up |
| Colored deterministic/ML/LLM/Codex/operator triggers | Typed live signal schema, artifacts, LLM traces, operator/Codex issue loops, deterministic gate/blocker evidence, and #70 postgame scorecard foundation exist. | Trigger outputs need worker-level aggregation adoption for new signal families. | #63 / focused follow-up |
| `Position Closed / Outside Human In Loop Interference Detected` | Event-scoped inventory and direct CLOB truth are checked; #70 added replay/config review evidence for the 2026-05-24 cases. | Future manual-trade/operator-intervention scoring should be a focused #63/#71 follow-up if fresh evidence requires it. | #71 / focused #63 follow-up |
| `Post Game Review Phase` | Structured postgame signal review, missed-signal replay, no-bid/min-price quarantine, and project-chief readback sync are implemented as a closed #70 foundation. | Future new missed-signal families need a focused follow-up instead of reopening #70. | #71 / focused #63 follow-up |
| `System Dev Review Loop` | `oversight-devloop` and `janus-master-dev` exist. | Needs project-chief daily performance review that ranks return-improving work and feeds issues. | #71 |
| Bottom-up reliability stack | Deterministic gates, ML/replay history, and #70 replay/config review evidence exist in pieces. | Need explicit promotion ladder: deterministic/ML backtest -> internal research -> Codex review -> runtime config for new signal families. | #65, #66, #71 / focused follow-up |

## Current App State

Implemented enough for today's live run:

- Janus API and live worker can run independently of the pregame automation.
- Missing pregame plans no longer hard-stop monitoring; monitor-only fallback exists.
- Current event executable StrategyPlans can be built from Polymarket event URLs.
- NBA/WNBA plans can use 5-share grid/scalp sleeves and, for selected NBA sides, a separate 5-share core-hold sleeve.
- Live worker accepts `max_buy_notional_usd` and can enforce the current `$10` event cap boundary.
- WNBA has live sync/read parity at the API/tick level for current controlled tests.
- Runtime handoff now records the corrected 2026-05-24 NBA/WNBA live scope and stale fallback cleanup.

Implemented since the original 2026-05-24 map:

- A normalized live snapshot object shared by NBA and WNBA.
- A persisted live signal schema.
- A first aggregation/arbitration module that records event-scoped blockers and candidates.
- Runtime endpoints for event config and signal-source toggles.
- Postgame replay/config artifacts and project-chief review artifacts.
- The first issue task register for comment-loop and closure governance.

Still missing for the full diagram:

- Live-worker adoption of aggregation and event-budget decisions as the ordinary evaluation path.
- Paired microcycle order handling: filled grid/scalp buy -> paired sell, filled sell -> reviewed rebuy, and no duplicate same-cycle buy while the sell leg is unresolved.
- Replay-first no-bid/min-price calibration with direct-CLOB fillability proof.
- Structured optional NBA/WNBA pregame research artifacts.
- Obsidian-to-backlog ingestion that promotes notes into bounded GitHub issues without making Obsidian execution truth.

## Current Automation Fit

| Automation | Fits diagram? | Required adjustment |
|---|---|---|
| `janus-master-dev` | Partially. It can implement and repair issue-backed runtime slices. | Keep it as executor only; do not make it the performance-review strategist. |
| `oversight-devloop` | Partially. It monitors no-progress loops. | Add #73 issue-health scoring and stale/comment-loop interventions. |
| `janus-portfolio-manager` | Separate from Janus covered-market runtime. | Keep scoped to global portfolio foundations from closed #56/#59 and active portfolio follow-ups; do not use for NBA/WNBA covered-market live execution. |
| `oversight-portfolio` | Portfolio-specific only. | Keep separate from sports live runtime and project-chief review. |
| `janus-obsidian-builder` | Supports memory and curation. | Repair backlog ingestion under #74; still no execution authority. |
| `janus-performance-review` | Implemented as the project-chief review lane. | Keep it read-only and feed #69/#71 recommendation routing from closed #55/#70 evidence. |
| Planned NBA/WNBA pregame research | Missing as structured automation contracts. | Add under #72 as optional priors, not execution gates. |

## Current Scope Before Crypto Options

Do not expand to crypto options or new live domains until the current basketball live-runtime loop has these minimum properties:

1. Current NBA/WNBA events can run without pregame Codex dependency.
2. Each live event has a normalized snapshot with score, clock, feed freshness, CLOB, and account inventory.
3. Signals are persisted as evidence and can be replayed.
4. Aggregation prevents duplicate/cumulative overbuying and records exact blockers.
5. Event budgets are percentage-derived with absolute caps and sleeve state.
6. Runtime controls can activate/deactivate signal sources and tune parameters without code edits.
7. Postgame review scores fired, blocked, and missed signals by strategy family and sleeve.
8. The project-chief loop converts postgame results into bounded issues, config changes, backtests, or closures.
9. Pregame research exists as optional structured priors, not liveness dependencies.
10. Dev-loop/issue governance prevents repeated no-progress issue comments.

Crypto options remain #47 and should stay in idea/research until this basketball loop is demonstrably self-improving.

## Issue Map

### Keep As Active Current Scope

| Issue | Role |
|---|---|
| #61 | Closed NBA OKC/SAS live execution foundation; future NBA gaps route through focused #63 work using closed #55/#70 evidence. |
| #62 | Active WNBA live promotion evidence route. |
| #63 | Active parent for live-worker adoption of aggregation, event budgets, target coverage, and degraded-mode runtime behavior. |
| #70 | Closed foundation for postgame signal performance, missed-signal replay, and no-bid/min-price calibration. |
| #77 | Closed foundation for paired microcycle evidence and readback scoring. |
| #71 | Project-chief performance review and development-planning automation. |
| #72 | NBA/WNBA pregame research agents as optional priors. |
| #73 | Issue lifecycle anti-stagnation and closure governance. |

### Keep As Support Scope

| Issue | Support role |
|---|---|
| #42 | Minimum-order and market-order exception policy. |
| #44 | Risk ladder calibration and bankroll scaling. |
| #55 | Pregame vs live entry timing research and fillability. |
| #74 | Obsidian-to-backlog curation workflow. |

### Keep Separate From Janus Covered-Market Runtime

| Issue | Boundary |
|---|---|
| #56 | Closed global portfolio-manager action loop, grid scanner, and 20-slot governance foundation. |
| #59 | Closed portfolio-manager real-call reconciliation foundation. |
| #46 | Winning profile hypotheses. |
| #48 | Geopolitics/economics/culture future-domain monitoring. |

### Defer Until Basketball Runtime Is Stable

| Issue | Deferral rule |
|---|---|
| #47 | Crypto options research only; no live crypto execution until basketball live runtime, performance review, and issue-governance loops are stable. |

## Backlog Phases

### Phase A - Stabilize Today's Live Loop

- Keep #62 live-worker evidence fresh during the next WNBA window.
- Treat #61 as completed NBA live-test evidence unless a focused #63 follow-up is created from closed #55/#70 evidence.
- Confirm current event scope and stale fallback cleanup.
- Preserve `max_buy_notional_usd=10`, 5-share legs, and event-scoped inventory proof.
- If live orders do not happen, record the exact strategy blocker before the window ends.

### Phase B - Build The Signal Runtime

- Closed #64 normalized snapshot foundation.
- Closed #65 live signal schema foundation.
- Closed #66 aggregation arbitration foundation.
- Closed #67 event budget/sleeve foundation.
- Closed #68 deterministic fallback foundation.
- Closed #69 runtime controls foundation.
- Active #63 adoption: live worker must consume aggregation/event-budget decisions and manage lot-level targets.

### Phase C - Build The Learning Loop

- Closed #70 postgame performance review artifact and replay foundation; use #71/project-chief plus focused #63 follow-ups for new gaps.
- #71 project-chief automation.
- #72 structured pregame priors.
- #73 issue anti-stagnation report.
- #74 Obsidian backlog ingestion.

### Phase D - Promote Or Defer Existing Issues

- Keep #61 closed and use #62 for the next WNBA live lifecycle evidence.
- Split future NBA runtime gaps into focused #63 tasks using closed #55/#70 evidence rather than reopening #61 or #70.
- Keep #55/#42/#44 only if they feed concrete runtime config or tests.
- Keep closed #56/#59 foundations separate for portfolio manager; use new focused issues for future portfolio drift or expansion.
- Keep #47 deferred until current loop is stable.

## Acceptance For This Map

This map is current when:

- GitHub issues #71-#74 exist and are linked from #63/backlog docs.
- `backlog_layers.md` includes #71-#74.
- The final answer reports which diagram components are implemented, partial, and missing.
- No trading or live-worker action is authorized by this map by itself.
