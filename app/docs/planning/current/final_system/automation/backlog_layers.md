# Janus Backlog Layers

Status: draft control contract
Created: 2026-05-17

## Purpose

Separate raw ideas, planned work, sprint issues, and runtime-active tasks so the controller does not confuse brainstorms with executable work.

## Layers

| Layer | Authority | Location | Purpose |
|---|---|---|---|
| Idea backlog | Low | Repo docs and Obsidian notes | Preserve concepts and future domains without forcing execution. |
| Planned backlog | Medium | Repo docs and GitHub draft/ready issues | Scoped tasks with acceptance criteria and dependencies. |
| Sprint backlog | High for work identity | GitHub issues | Work selected for near-term implementation/review. |
| Issue task register | High for execution planning | `automation/issue_task_register.md` | Bounded next steps for open issues, blockers, owner lanes, and validation evidence. |
| Active queue | Runtime/current | `local/shared` handoffs/artifacts | Claimed tasks, locks, running agents, current blockers. |
| Evidence layer | Runtime/reporting | artifacts, reports, review bundles | Proof used to close, promote, or demote tasks/domains. |

## Idea Backlog

Use for:

- new market domains
- broad strategy concepts
- profile-study observations
- long-term global portfolio ideas
- architecture possibilities

An idea must not be treated as work-ready until it has a market scope, owner persona, risk category, and acceptance criteria.

## Planned Backlog

Use for:

- reviewed ideas ready to become issues
- specs that define implementation boundaries
- follow-up hardening after a seed issue closed
- domain promotion tasks

Planned backlog entries should map to the issue taxonomy but may not yet have a GitHub issue.

## Sprint Backlog

Sprint backlog items must be GitHub issues with:

- priority
- type
- lane
- market/domain labels
- live impact
- acceptance criteria
- validation plan
- write scope or expected lock

The immediate seed issues `#17-#29` are closed foundations. Follow-up work should be narrower hardening/calibration/readiness issues.

Follow-up issues should use GitHub issue types when available, plus the `type:*`, `priority:*`, `market:*`, `lane:*`, `phase:*`, `stage:*`, and `live-impact:*` labels from `issue_taxonomy.md`.

## Issue Task Register

The issue task register is the local bridge from broad sprint issues to executable work. It should answer: what exact task is next, who owns it, what files or evidence are in scope, and what blocker changed since the last pass.

Use `automation/issue_task_register.md` when:

- an issue is important but too broad for one automation pass;
- the same issue has repeated comments without commits, validation, closure, or blocker changes;
- live-window evidence needs to be preserved while implementation waits for postgame;
- an oversight lane needs to decide whether to split, close, or defer an issue.

Do not use the register as live trading truth or active lock authority. Runtime locks still live under `local/shared/artifacts/final-system-controller/queue`, and execution truth still comes from direct CLOB/API/artifact evidence.

## Current Sprint/Follow-Up Issues

| Issue | Priority | Layer | Purpose |
|---|---|---|---|
| [#30](https://github.com/LucaCGN/janus_cortex/issues/30) | P0 | Closed foundation | Label taxonomy created and closed seed issues `#17-#29` retro-labeled. |
| [#31](https://github.com/LucaCGN/janus_cortex/issues/31) | P0 | Closed foundation | Refresh runtime handoffs after operator event-data reconciliation. |
| [#32](https://github.com/LucaCGN/janus_cortex/issues/32) | P0 | Closed foundation | Repo-local runtime root and controller activation gate validated. |
| [#33](https://github.com/LucaCGN/janus_cortex/issues/33) | P1 | Closed foundation | API-up validation of closed seed foundations completed against the running non-live API. |
| [#34](https://github.com/LucaCGN/janus_cortex/issues/34) | P1 | Closed foundation | WNBA minimal-readiness dry run completed; WNBA remains passive/shadow only. |
| [#35](https://github.com/LucaCGN/janus_cortex/issues/35) | P1 | Closed foundation | Build/read the global portfolio explorer automation contract. |
| [#36](https://github.com/LucaCGN/janus_cortex/issues/36) | P2 | Closed foundation | Archive or delete absorbed ML replay branch after operator approval. |
| [#37](https://github.com/LucaCGN/janus_cortex/issues/37) | P0 | Closed foundation | Fresh-DB NBA probe and account mapping gaps repaired and HTTP-path validated. |
| [#38](https://github.com/LucaCGN/janus_cortex/issues/38) | P0 | Closed foundation | Encode Janus global ego and purpose contract. |
| [#39](https://github.com/LucaCGN/janus_cortex/issues/39) | P0 | Closed foundation | Controller active queue locks and pass ledger implemented in `app/runtime/controller_queue.py` and `tools/controller_queue.py`. |
| [#40](https://github.com/LucaCGN/janus_cortex/issues/40) | P0 | Closed foundation | Current architecture and degradation maps completed. |
| [#41](https://github.com/LucaCGN/janus_cortex/issues/41) | P0 | Closed foundation | Budget-aware model routing and Codex fallback StrategyPlanJSON adoption/evaluation path validated. |
| [#42](https://github.com/LucaCGN/janus_cortex/issues/42) | P1 | Planned/sprint | Validate minimum order constraints and market-order exception policy. |
| [#43](https://github.com/LucaCGN/janus_cortex/issues/43) | P1 | Closed foundation | Chart-equivalent microstructure metrics implemented for event review/live monitor. |
| [#44](https://github.com/LucaCGN/janus_cortex/issues/44) | P1 | Planned/sprint | Calibrate profit-ratcheted risk ladder from account and DB histories. |
| [#45](https://github.com/LucaCGN/janus_cortex/issues/45) | P1 | Closed foundation | Global portfolio target/rebuy ledger and watchlist schema implemented. |
| [#52](https://github.com/LucaCGN/janus_cortex/issues/52) | P0 | Closed foundation | Active Codex global portfolio-manager execution policy and trend lane defined with ledger/preview surfaces. |
| [#53](https://github.com/LucaCGN/janus_cortex/issues/53) | P0 | Closed foundation | Codex tooling split and preview-first Polymarket fallback base implemented. |
| [#54](https://github.com/LucaCGN/janus_cortex/issues/54) | P0 | Closed foundation | Approved global portfolio execution gate proof, concrete Janus order-management adapter, runtime activation guard, risk/rate evidence, ledger finalization, confirmation-id handling, and idempotency replay hardening implemented. |
| [#59](https://github.com/LucaCGN/janus_cortex/issues/59) | P0 | Closed foundation | Portfolio-manager real-call reconciliation proof completed; future activation drift or expansion needs a new focused issue. |
| [#61](https://github.com/LucaCGN/janus_cortex/issues/61) | P0 | Closed foundation | Next NBA playoff min-size live test executed through Janus gates during the 2026-05-24 OKC/SAS window; remaining lessons route to #63/#70/#55 follow-up tasks. |
| [#62](https://github.com/LucaCGN/janus_cortex/issues/62) | P0 | Active/sprint | Validate the next WNBA controlled min-size live lifecycle with the implemented WNBA StrategyPlan fallback, direct CLOB evidence, worker heartbeat, and post-call reconciliation. |
| [#63](https://github.com/LucaCGN/janus_cortex/issues/63) | P0 | Active/sprint | Build the independent Janus covered-market live trading runtime and signal aggregation system so pregame Codex/LLM availability is not a liveness dependency. |
| [#64](https://github.com/LucaCGN/janus_cortex/issues/64) | P0 | Closed foundation | Normalized NBA/WNBA live snapshot review and live-tick runtime adoption implemented in `340db2f` and `e87515f`; future HTTP/readback adoption needs a focused follow-up if #63 requires it. |
| [#65](https://github.com/LucaCGN/janus_cortex/issues/65) | P0 | Closed foundation | Live signal schema and persistence implemented in `ddbf6e0`; future schema changes need focused follow-up scope. |
| [#66](https://github.com/LucaCGN/janus_cortex/issues/66) | P0 | Closed foundation | Signal aggregation arbitration and blocker artifacts implemented in `039bfe4`; future live-worker adoption work needs focused follow-up scope. |
| [#67](https://github.com/LucaCGN/janus_cortex/issues/67) | P0 | Closed foundation | Event risk budget and sleeve manager implemented in `57da4ce`; future calibration belongs to risk/postgame/performance-review routing. |
| [#68](https://github.com/LucaCGN/janus_cortex/issues/68) | P0 | Closed foundation | Deterministic fallback/degraded-mode behavior implemented and live-validated; future regressions should open a focused #63 child follow-up instead of reopening this slice. |
| [#69](https://github.com/LucaCGN/janus_cortex/issues/69) | P1 | Closed foundation | Runtime event-control endpoints for event config and signal toggles implemented in `a86818e`. |
| [#70](https://github.com/LucaCGN/janus_cortex/issues/70) | P1 | Planned/sprint | Add postgame signal performance review, missed-signal replay, no-bid/min-price quarantine, and replay-backed config recommendations. |
| [#71](https://github.com/LucaCGN/janus_cortex/issues/71) | P1 | Closed foundation | Project-chief performance review contract, deterministic artifact generator, and first daily review artifact implemented; future improvements should route through #70/#55/#69 recommendations or focused follow-up tasks. |
| [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | P1 | Planned backlog | Formalize NBA/WNBA pregame research agents as optional priors with expiry and no liveness dependency. |
| [#73](https://github.com/LucaCGN/janus_cortex/issues/73) | P1 | Planned/sprint | Harden issue lifecycle anti-stagnation and closure governance for repeated comments, stale blockers, oversized issues, and missing validation/closure. |
| [#74](https://github.com/LucaCGN/janus_cortex/issues/74) | P2 | Planned backlog | Repair Obsidian-to-backlog ingestion and curation workflow so notes become bounded issue candidates rather than execution authority. |
| [#75](https://github.com/LucaCGN/janus_cortex/issues/75) | P1 | Closed foundation | Reconciled the 2026-05-24T18:06Z portfolio-manager artifact-only pass, memory ownership, and future queue claim/release discipline without reopening closed #56/#59. Future portfolio drift, scaling, grid-service expansion, or order-path regression needs a focused follow-up issue. |
| [#76](https://github.com/LucaCGN/janus_cortex/issues/76) | P0 | Closed foundation | Maduro target coverage restored and Colorado Avalanche lifecycle reconciled by the 2026-05-25 portfolio-manager pass; future drift needs a focused follow-up issue. |
| [#57](https://github.com/LucaCGN/janus_cortex/issues/57) | P0 | Closed foundation | Spurs/Thunder final settlement and residual Thunder direct-CLOB exposure reconciled. |
| [#58](https://github.com/LucaCGN/janus_cortex/issues/58) | P0 | Closed foundation | Resolved-market redeem workflow and unredeemed residual tolerance implemented so settled positions do not block new live readiness after direct-truth classification. |
| [#49](https://github.com/LucaCGN/janus_cortex/issues/49) | P1 | Closed foundation | Direct open CLOB order mirror endpoint implemented and runtime-validated. |
| [#50](https://github.com/LucaCGN/janus_cortex/issues/50) | P1 | Closed foundation | WNBA passive/shadow baseline and blocker report published. Remaining active-window WNBA capture/audit work split to #60. |
| [#60](https://github.com/LucaCGN/janus_cortex/issues/60) | P1 | Closed foundation | Sustained WNBA active-window passive CLOB capture and audit integration completed; remaining WNBA live-promotion blockers require follow-up scope. |
| [#55](https://github.com/LucaCGN/janus_cortex/issues/55) | P1 | Research backlog | Compare NBA/WNBA pregame, immediate-live, post-Q1, and post-Q1-stability entry timing with fillability and event-start cancellation effects; feeds #63/#70/#69 guidance but is not a live execution checklist. |
| [#56](https://github.com/LucaCGN/janus_cortex/issues/56) | P1 | Closed foundation | Active portfolio-manager action planning, frontend/profile discovery enforcement, one-shot portfolio order routing, approved global portfolio 1c grid service spawn proof, cross-league basketball scanner, and 20-slot governance completed. |
| [#46](https://github.com/LucaCGN/janus_cortex/issues/46) | P2 | Planned backlog | Turn winning profile studies into benchmark hypotheses. |
| [#47](https://github.com/LucaCGN/janus_cortex/issues/47) | P2 | Idea/planned backlog | Incubate crypto up/down options research and backtest lane. |
| [#48](https://github.com/LucaCGN/janus_cortex/issues/48) | P2 | Idea/planned backlog | Incubate geopolitics, economics, and culture monitoring lanes. |

## Active Queue

The active queue is runtime state, not planning truth.

It should track:

- current persona
- claimed issue/task
- branch/worktree
- write locks
- read scope
- blockers
- next action
- last material update

The controller should not start duplicate work if a matching active queue item exists.

## Promotion Rules

### Janus Core Live Runtime Split - 2026-05-24

Issue `#63` is the parent for Janus covered-market live runtime redesign. It does not replace the global portfolio-manager issues.

Routing rules:

| Issue family | Routing under `#63` |
|---|---|
| `#61/#62` | Keep #62 as the active WNBA live-validation route. #61 is the completed NBA live-test foundation; future NBA runtime gaps should use #63/#70/#55 focused tasks instead of reopening #61. |
| `#55` | Keep as research/backtest evidence feeding signal confidence and timing config. |
| `#42/#44` | Keep as support for exchange minimums, order exceptions, risk budget, and bankroll scaling. |
| `#56/#59` | Closed Codex global portfolio-manager foundations. Do not use them as Janus NBA/WNBA live-runtime owners or as open umbrellas for future status comments; create focused follow-up issues for new portfolio drift or expansion. |
| `#46/#47/#48` | Keep as profile/future-domain incubation, not covered-market live authority. |

Implementation children created from `#63`: `#70` postgame signal-performance review remains open; `#64` normalized live snapshots/feed parity, `#65` signal schema, `#66` aggregator arbitration, `#67` event budget/sleeves, `#68` deterministic fallback, and `#69` runtime control endpoints are closed foundations. New adoption, calibration, or regression work should use focused follow-up tasks instead of reopening closed children. Current focused follow-ups are live-worker adoption of aggregation/event budgets, lot-level target replacement, WNBA controlled live validation, and replay-only no-bid/min-price calibration.

Current-scope expansion created 2026-05-24:

| Issue | Role |
|---|---|
| [#71](https://github.com/LucaCGN/janus_cortex/issues/71) | Closed foundation for the project-chief loop that reviews strategy responsiveness, pregame accuracy, signal performance, issue progress, and next development priorities after #70 artifacts exist. |
| [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | Converts NBA/WNBA pregame research into structured optional priors; missing/stale priors cannot block Janus runtime liveness. |
| [#73](https://github.com/LucaCGN/janus_cortex/issues/73) | Gives `oversight-devloop` a concrete issue-health and anti-stagnation contract so issues with repeated comments must close, split, or get a real blocker. |
| [#74](https://github.com/LucaCGN/janus_cortex/issues/74) | Repairs Obsidian backlog ingestion while preserving the rule that Obsidian is curated memory, not runtime execution truth. |
| [#75](https://github.com/LucaCGN/janus_cortex/issues/75) | Closed after the next portfolio-manager run proved queue claim/release discipline for durable portfolio-manager writes. |
| [#76](https://github.com/LucaCGN/janus_cortex/issues/76) | Closed focused portfolio lifecycle route; Maduro target placement and Colorado close/fill reconciliation completed. Do not reopen `#56/#59/#75/#76` for future drift; open a focused follow-up issue. |

Do not expand crypto options or new model domains beyond research issue [#47](https://github.com/LucaCGN/janus_cortex/issues/47) until the current basketball runtime has normalized snapshots, persisted signals, aggregation arbitration, runtime controls, postgame replay, and daily performance-review governance.

| From | To | Required Evidence |
|---|---|---|
| Idea | Planned | Operator or controller review, clear scope, domain registry mapping. |
| Planned | Sprint issue | Acceptance criteria, owner persona, labels, validation. |
| Sprint issue | Active queue | Lock claim, branch/worktree or runtime ownership, no conflict. |
| Active queue | Done | Tests/evidence, report/handoff update, issue close or review note. |
| Done | Obsidian wisdom | Repeated evidence or high-value case memory. |

## Demotion Rules

Tasks should be demoted or paused when:

- live safety preempts work
- source-of-truth state is stale
- issue scope is too broad
- acceptance criteria are missing
- runtime/API state cannot support validation
- domain maturity is too low for the requested action
