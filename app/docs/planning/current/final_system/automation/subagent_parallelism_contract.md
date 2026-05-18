# Janus Sub-Agent Parallelism Contract

Status: draft control contract
Created: 2026-05-17

## Purpose

Define when the master controller may use Codex sub-agents and how those agents avoid conflicting with each other, Janus runtime state, or live events.

Sub-agents are a capability, not a default. The controller should spawn them only when parallel work materially helps and scopes are independent.

## Allowed Parallelism

| Scenario | Allowed Pattern |
|---|---|
| Multiple closed events need review | One `postgame-reviewer` per event if artifacts and reports do not overlap. |
| Docs/index sync plus unrelated read-only research | `docs-memory-agent` and `profile-research-agent` may run in parallel. |
| Independent code issues | Multiple `development-agent` workers only with disjoint file/module locks. |
| Multiple live events | Event-scoped `live-monitor-analyst` agents may observe separate events, but Janus remains execution authority. |
| WNBA passive capture and repo issue triage | `wnba-data-agent` can inspect capture state while `issue-backlog-manager` works docs/issues. |

## Disallowed Parallelism

Do not spawn parallel agents when:

- two agents need the same file/module write lock
- one agent's output is required before the other can proceed
- a live event requires the main controller's immediate attention
- runtime root/path authority is unclear
- issue scope or acceptance criteria are not defined
- the task is a broad architecture rewrite

## Sub-Agent Assignment Packet

Every spawned agent must receive:

- persona
- task or issue id
- market/domain scope
- lifecycle/scope stage
- read scope
- write scope
- explicit locks
- authority limits
- validation expected
- output path/report expected
- stop condition

## Lock Types

| Lock | Example |
|---|---|
| `file` | `app/modules/agentic/llm_runtime.py` |
| `module` | `portfolio-ledger`, `basketball-logic` |
| `event` | `nba-cle-det-2026-05-13` |
| `service` | Janus API, live strategy worker |
| `market` | `sports/basketball/wnba` |
| `runtime` | `local/shared/artifacts/llm-runtime/2026-05-17` |
| `docs` | `app/docs/planning/current/final_system/*` |
| `obsidian` | `10_System_Specs/Controller And Queue Design.md` |

## Five-Minute Loop Interaction

The recurring controller may run while sub-agents are still active.

On the next pass, it should:

1. Read active locks/running-agent registry.
2. Avoid spawning duplicate agents for the same task.
3. Check for completed outputs.
4. Integrate results only after review.
5. Start new work only if it does not conflict with active locks.

If no active-agent registry exists yet, the controller should be conservative and avoid parallel write work.

Sub-agents that produce committed repo changes must not leave those changes local-only. The parent controller or `development-end-phase` owns pull/push reconciliation before the work is considered complete.

## Live Event Rule

During live events, parallelism is read-mostly and event-scoped.

Allowed:

- monitor separate events
- inspect CLOB/account truth
- generate issue drafts
- prepare strategy review artifacts

Restricted:

- broad feature development
- risky service restarts
- unreviewed execution path changes
- multiple agents modifying StrategyPlanJSON for the same event

## Output Integration

The parent controller is responsible for integration. Sub-agents should not close broad issues, merge branches, or mark live readiness green unless explicitly assigned that authority.
