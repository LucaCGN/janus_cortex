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
| [#59](https://github.com/LucaCGN/janus_cortex/issues/59) | P0 | Planned/sprint | Prove portfolio-manager real-call reconciliation before operational activation. |
| [#61](https://github.com/LucaCGN/janus_cortex/issues/61) | P0 | Active/sprint | Execute the next NBA playoff min-size live trade through Janus StrategyPlan/evaluate/execute/live-worker gates or record the exact blocker before the live window ends. |
| [#62](https://github.com/LucaCGN/janus_cortex/issues/62) | P0 | Active/sprint | Promote WNBA from passive/shadow capture to controlled min-size live test readiness with a WNBA StrategyPlan, direct CLOB evidence, and explicit gate/blocker proof. |
| [#63](https://github.com/LucaCGN/janus_cortex/issues/63) | P0 | Active/sprint | Build the independent Janus covered-market live trading runtime and signal aggregation system so pregame Codex/LLM availability is not a liveness dependency. |
| [#64](https://github.com/LucaCGN/janus_cortex/issues/64) | P0 | Planned/sprint | Normalize NBA/WNBA live snapshots and feed adapter parity. |
| [#65](https://github.com/LucaCGN/janus_cortex/issues/65) | P0 | Planned/sprint | Implement live signal schema and persistence. |
| [#66](https://github.com/LucaCGN/janus_cortex/issues/66) | P0 | Planned/sprint | Build signal aggregation arbitration and blocker artifacts. |
| [#67](https://github.com/LucaCGN/janus_cortex/issues/67) | P0 | Planned/sprint | Implement event risk budget and sleeve manager. |
| [#68](https://github.com/LucaCGN/janus_cortex/issues/68) | P0 | Planned/sprint | Preserve deterministic fallback when pregame or LLM inputs fail. |
| [#69](https://github.com/LucaCGN/janus_cortex/issues/69) | P1 | Planned backlog | Add runtime control endpoints for event config and signal toggles. |
| [#70](https://github.com/LucaCGN/janus_cortex/issues/70) | P1 | Planned backlog | Add postgame signal performance review and missed-signal replay. |
| [#71](https://github.com/LucaCGN/janus_cortex/issues/71) | P1 | Planned backlog | Add project-chief performance review and development-planning automation for daily return-focused system improvement. |
| [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | P1 | Planned backlog | Formalize NBA/WNBA pregame research agents as optional priors with expiry and no liveness dependency. |
| [#73](https://github.com/LucaCGN/janus_cortex/issues/73) | P1 | Planned/sprint | Harden issue lifecycle anti-stagnation and closure governance for repeated comments, stale blockers, oversized issues, and missing validation/closure. |
| [#74](https://github.com/LucaCGN/janus_cortex/issues/74) | P2 | Planned backlog | Repair Obsidian-to-backlog ingestion and curation workflow so notes become bounded issue candidates rather than execution authority. |
| [#57](https://github.com/LucaCGN/janus_cortex/issues/57) | P0 | Closed foundation | Spurs/Thunder final settlement and residual Thunder direct-CLOB exposure reconciled. |
| [#58](https://github.com/LucaCGN/janus_cortex/issues/58) | P0 | Closed foundation | Resolved-market redeem workflow and unredeemed residual tolerance implemented so settled positions do not block new live readiness after direct-truth classification. |
| [#49](https://github.com/LucaCGN/janus_cortex/issues/49) | P1 | Closed foundation | Direct open CLOB order mirror endpoint implemented and runtime-validated. |
| [#50](https://github.com/LucaCGN/janus_cortex/issues/50) | P1 | Closed foundation | WNBA passive/shadow baseline and blocker report published. Remaining active-window WNBA capture/audit work split to #60. |
| [#60](https://github.com/LucaCGN/janus_cortex/issues/60) | P1 | Closed foundation | Sustained WNBA active-window passive CLOB capture and audit integration completed; remaining WNBA live-promotion blockers require follow-up scope. |
| [#55](https://github.com/LucaCGN/janus_cortex/issues/55) | P1 | Research backlog | Compare NBA pregame, immediate-live, post-Q1, and post-Q1-stability entry timing with fillability and event-start cancellation effects; supports #61 but is not the live execution checklist. |
| [#56](https://github.com/LucaCGN/janus_cortex/issues/56) | P1 | Planned/sprint | Build active portfolio-manager action planning, frontend/profile discovery enforcement, one-shot portfolio order routing, approved global portfolio 1c grid service spawn proof, and cross-league basketball scanner. |
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
| `#61/#62` | Keep as NBA/WNBA event-readiness evidence routes until the new runtime can execute and review controlled min-size trades. |
| `#55` | Keep as research/backtest evidence feeding signal confidence and timing config. |
| `#42/#44` | Keep as support for exchange minimums, order exceptions, risk budget, and bankroll scaling. |
| `#56/#59` | Keep scoped to Codex global portfolio-manager. Do not use them as Janus NBA/WNBA live-runtime owners. |
| `#46/#47/#48` | Keep as profile/future-domain incubation, not covered-market live authority. |

Implementation children created from `#63`: `#64` data adapters, `#65` signal schema, `#66` aggregator arbitration, `#67` event budget/sleeves, `#68` deterministic fallback, `#69` runtime control endpoints, and `#70` postgame signal-performance review. Each child issue must retain acceptance criteria and file/module ownership before code begins.

Current-scope expansion created 2026-05-24:

| Issue | Role |
|---|---|
| [#71](https://github.com/LucaCGN/janus_cortex/issues/71) | Adds the project-chief loop that reviews strategy responsiveness, pregame accuracy, signal performance, issue progress, and next development priorities after #70 artifacts exist. |
| [#72](https://github.com/LucaCGN/janus_cortex/issues/72) | Converts NBA/WNBA pregame research into structured optional priors; missing/stale priors cannot block Janus runtime liveness. |
| [#73](https://github.com/LucaCGN/janus_cortex/issues/73) | Gives `oversight-devloop` a concrete issue-health and anti-stagnation contract so issues with repeated comments must close, split, or get a real blocker. |
| [#74](https://github.com/LucaCGN/janus_cortex/issues/74) | Repairs Obsidian backlog ingestion while preserving the rule that Obsidian is curated memory, not runtime execution truth. |

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
