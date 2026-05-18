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
