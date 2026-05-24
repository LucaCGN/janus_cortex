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
| `Janus System Eye` | Live worker, StrategyPlan gate, event-scoped inventory proof, CLOB sampling, LLM trace persistence, and live preflight exist. | Needs normalized snapshot contract and signal aggregation module rather than direct strategy-lane execution. | #63, #64, #65, #66 |
| `Game Flow` checkpoints | Worker ticks every 30s and StrategyPlan sleeves can evaluate live state. | Quarter/HT/score-run/material-price-change checkpoints need explicit signal producers and config. | #65, #66, #69 |
| Colored deterministic/ML/LLM/Codex/operator triggers | LLM traces, operator/Codex issue loops, and deterministic gate/blocker evidence exist. | Trigger outputs need one typed signal schema and persistence. | #65 |
| `Position Closed / Outside Human In Loop Interference Detected` | Event-scoped inventory and direct CLOB truth are checked. | Manual trades and operator interventions need reconciliation into event review and signal scoring. | #70, #71 |
| `Post Game Review Phase` | Narrative postgame docs exist; #70 is open. | Needs structured fired/blocked/missed signal replay, sleeve PnL, latency/fillability review, and config recommendations. | #70 |
| `System Dev Review Loop` | `oversight-devloop` and `janus-master-dev` exist. | Needs project-chief daily performance review that ranks return-improving work and feeds issues. | #71 |
| Bottom-up reliability stack | Deterministic gates and ML/replay history exist in pieces. | Need explicit promotion ladder: deterministic/ML backtest -> internal research -> Codex review -> runtime config. | #65, #66, #70, #71 |

## Current App State

Implemented enough for today's live run:

- Janus API and live worker can run independently of the pregame automation.
- Missing pregame plans no longer hard-stop monitoring; monitor-only fallback exists.
- Current event executable StrategyPlans can be built from Polymarket event URLs.
- NBA/WNBA plans can use 5-share grid/scalp sleeves and, for selected NBA sides, a separate 5-share core-hold sleeve.
- Live worker accepts `max_buy_notional_usd` and can enforce the current `$10` event cap boundary.
- WNBA has live sync/read parity at the API/tick level for current controlled tests.
- Runtime handoff now records the corrected 2026-05-24 NBA/WNBA live scope and stale fallback cleanup.

Still missing for the full diagram:

- A normalized live snapshot object shared by NBA and WNBA.
- A persisted live signal schema.
- A real aggregation/arbitration module that merges signals before order intents.
- Runtime endpoints for event config and signal-source toggles.
- A postgame performance review artifact that scores every strategy/signal and missed opportunity.
- A project-chief automation that turns performance review into daily development priorities.
- Structured optional NBA/WNBA pregame research artifacts.
- Issue lifecycle scoring to stop repeated comments without closure.
- Obsidian-to-backlog ingestion that promotes notes into bounded GitHub issues without making Obsidian execution truth.

## Current Automation Fit

| Automation | Fits diagram? | Required adjustment |
|---|---|---|
| `janus-master-dev` | Partially. It can implement and repair issue-backed runtime slices. | Keep it as executor only; do not make it the performance-review strategist. |
| `oversight-devloop` | Partially. It monitors no-progress loops. | Add #73 issue-health scoring and stale/comment-loop interventions. |
| `janus-portfolio-manager` | Separate from Janus covered-market runtime. | Keep scoped to global portfolio foundations from closed #56/#59 and active portfolio follow-ups; do not use for NBA/WNBA covered-market live execution. |
| `oversight-portfolio` | Portfolio-specific only. | Keep separate from sports live runtime and project-chief review. |
| `janus-obsidian-builder` | Supports memory and curation. | Repair backlog ingestion under #74; still no execution authority. |
| Planned `janus-performance-review` | Missing. | Add under #71 after #70 has first artifact schema. |
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
| #63 | Parent Janus core live runtime and signal aggregation redesign. |
| #61 | NBA live execution evidence route. |
| #62 | WNBA live promotion evidence route. |
| #64 | NBA/WNBA live snapshot and feed adapter parity. |
| #65 | Live signal schema and persistence. |
| #66 | Aggregation arbitration and blocker artifacts. |
| #67 | Event budget and sleeve manager. |
| #68 | Deterministic fallback when pregame/LLM fails. |
| #69 | Runtime event config and signal toggle endpoints. |
| #70 | Postgame signal performance and missed-signal replay. |
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

- Keep #61/#62 live worker evidence fresh.
- Confirm current event scope and stale fallback cleanup.
- Preserve `max_buy_notional_usd=10`, 5-share legs, and event-scoped inventory proof.
- If live orders do not happen, record the exact strategy blocker before the window ends.

### Phase B - Build The Signal Runtime

- #64 normalized snapshot.
- #65 live signal schema.
- #66 aggregation arbitration.
- #67 event budget/sleeve state.
- #68 deterministic fallback tests.
- #69 runtime controls.

### Phase C - Build The Learning Loop

- #70 postgame performance review artifact and replay.
- #71 project-chief automation.
- #72 structured pregame priors.
- #73 issue anti-stagnation report.
- #74 Obsidian backlog ingestion.

### Phase D - Promote Or Defer Existing Issues

- Close or split #61/#62 after today's live-window evidence.
- Keep #55/#42/#44 only if they feed concrete runtime config or tests.
- Keep closed #56/#59 foundations separate for portfolio manager; use new focused issues for future portfolio drift or expansion.
- Keep #47 deferred until current loop is stable.

## Acceptance For This Map

This map is current when:

- GitHub issues #71-#74 exist and are linked from #63/backlog docs.
- `backlog_layers.md` includes #71-#74.
- The final answer reports which diagram components are implemented, partial, and missing.
- No trading or live-worker action is authorized by this map by itself.
