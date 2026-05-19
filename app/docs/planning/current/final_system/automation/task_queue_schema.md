# Janus Task Queue And Lane Schema

Status: implemented repo-local v1

## Purpose

Define the minimum structure needed for the master controller to reason about tasks, lanes, GitHub issues, branches, worktrees, agents, and live-event locks.

The v1 repo-local implementation is `app/runtime/controller_queue.py`, with CLI access through `python tools/controller_queue.py`.

## Queue Item Fields

| Field | Required | Meaning |
|---|---|---|
| `task_id` | Yes | Stable local id such as `JANUS-P0-001`. |
| `github_issue` | Preferred | GitHub issue URL or number once created. |
| `priority` | Yes | `P0`, `P1`, `P2`, `P3`. |
| `lane` | Yes | Work lane such as `runtime-cost`, `event-review`, `wnba-readiness`. |
| `market_domain` | Yes | Market/domain scope such as `sports`, `global-portfolio`, `crypto`. |
| `market_subdomain` | Preferred | More specific scope such as `basketball/nba`, `basketball/wnba`, `btc-up-down`. |
| `event_lifecycle` | Preferred | `pregame`, `live`, `postgame`, `settlement`, `monitor`, `research`, `backtest`. |
| `maturity_stage` | Yes | `idea`, `research`, `shadow`, `min-size-test`, `live-limited`, `active`, `scaled`. |
| `status` | Yes | `draft`, `ready`, `claimed`, `in_progress`, `blocked`, `review`, `done`. |
| `owner_agent` | Yes | Agent persona responsible for next action. |
| `write_scope` | Yes | Files/modules the worker may edit. |
| `read_scope` | Yes | Files/reports/artifacts the worker should inspect. |
| `acceptance_criteria` | Yes | Concrete completion checks. |
| `tests_required` | Yes | Targeted tests or validation commands. |
| `live_order_impact` | Yes | Usually `none`; must be explicit if any. |
| `runtime_impact` | Yes | Service restart, migration, worker changes, or none. |
| `dependencies` | No | Task ids or issue numbers that must complete first. |
| `evidence_links` | No | Reports, artifacts, commits, or Obsidian notes. |
| `next_executable_step` | Preferred | The smallest next action that can be implemented or validated in one bounded pass. |
| `last_material_update` | Preferred | Last commit, issue update, artifact, or blocker change that materially advanced the task. |
| `comment_fingerprint` | Preferred | Short summary of the latest GitHub/handoff comment, used to avoid repeating unchanged status. |

## Lane Types

| Lane | Purpose |
|---|---|
| `runtime-cost-shutdown` | LLM cost controls, final shutdown, dedup, budgets. |
| `event-review-reportability` | Event bundle, decision timeline, ledger, missed windows. |
| `basketball-intelligence` | Quarter, PBP, regime classifier, scenario taxonomy. |
| `strategy-sleeves` | Sleeve generation, dependency graph, micro-grid, OT, hedges. |
| `llm-orchestration` | Model routing, prompt compression, fallback, adoption. |
| `risk-manager` | Profit-ratcheted exposure, bankroll ledgers, tail bucket. |
| `execution-ledger` | Direct CLOB order/fill linkage, lifecycle attribution. |
| `wnba-readiness` | WNBA passive to minimal live readiness. |
| `docs-obsidian-github` | Source-of-truth docs, Obsidian, issues, automation health. |
| `covered-market-portfolio` | Internal Janus portfolio/inventory work for NBA/WNBA and future covered market lanes. |
| `global-portfolio` | Codex global portfolio management: existing operator/global position targets, stale exits/rebuys, concentration review, and uncovered-market trend scouting under gated execution contracts. |
| `future-domain` | Crypto, geopolitics, economics, culture incubation. |

## Resource Locks

Every active task should claim locks:

| Lock Type | Examples |
|---|---|
| `file` | `app/modules/agentic/llm_runtime.py` |
| `module` | `agentic-live-worker`, `portfolio-ledger` |
| `event` | `nba-cle-det-2026-05-13` |
| `service` | Janus API, live strategy worker |
| `domain` | NBA, WNBA, crypto |
| `market` | `sports/basketball/nba`, `global-portfolio`, `crypto/up-down-options` |
| `runtime` | `local/shared/artifacts/llm-runtime/YYYY-MM-DD` |

No two coding agents should write the same file/module lock unless one is explicitly reviewing the other.

## Repo-Local Queue Storage

The controller queue implementation stores runtime state under:

| Path | Purpose |
|---|---|
| `local/shared/artifacts/final-system-controller/queue/active_locks/*.json` | Current active claims. These are the authority for write ownership. |
| `local/shared/artifacts/final-system-controller/queue/completed_locks/YYYY-MM-DD/*.json` | Released claims with outcome and evidence. |
| `local/shared/artifacts/final-system-controller/queue/pass_ledger.jsonl` | Append-only pass ledger for claims, no-ops, blockers, releases, and material outputs. |

These files are runtime artifacts, not durable product contracts. The durable contract is this tracked repo doc plus `app/runtime/controller_queue.py`.

## Required Controller Lock Flow

Before any code/docs/runtime-handoff write, the controller or assigned persona must either claim or confirm ownership:

```powershell
python tools/controller_queue.py claim --issue 39 --persona development-agent --owner janus-master-controller --branch main --worktree C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex --file app/runtime/controller_queue.py --file tools/controller_queue.py --module controller-queue --require-clean-worktree
```

If the command returns `blocked_duplicate_lock`, another active claim owns at least one issue/file/event/runtime scope. The controller must not write that scope.

If the command returns `blocked_stale_lock`, a stale claim exists. The controller must surface it for review instead of overwriting it.

If the command returns `blocked_dirty_worktree`, the shared worktree is dirty before ownership is established. The controller must stop or switch to review-only unless the dirty paths are explicitly owned by the active claim.

## Dirty Worktree Cleanup Rule

Dirty tracked files are a queue state, not background noise.

When `git status --short` shows tracked modifications and `python tools/controller_queue.py status` shows no active owning lock:

1. Classify the pass as `YELLOW` process drift unless an urgent live-safety issue outranks it.
2. Do not claim unrelated development work.
3. Map every dirty path to the issue/persona that produced or should own it.
4. If the paths are coherent and validated, commit and push them before continuing.
5. If the paths are mixed but entangled at file level, create one explicit stabilization commit that names all covered issue scopes and records why finer splitting would misrepresent the state.
6. If ownership is unclear or user edits may be present, stop and request operator review instead of committing.
7. Add no repeated GitHub comments or handoff blocks for unrelated open issues until the dirty state is resolved.

An active lock may own dirty paths only when the lock scope names the files/modules/issues and carries a next validation/commit command. Released locks must not leave tracked dirty files behind.

When work finishes, release the lock:

```powershell
python tools/controller_queue.py release --lock-id <lock_id> --outcome implemented --material-output <commit-or-artifact> --evidence <issue-or-report-link>
```

When a pass intentionally does not write, record a compact ledger entry instead of creating repeated artifacts:

```powershell
python tools/controller_queue.py ledger --outcome no_material_change --classification YELLOW --persona master-controller --issue 39 --no-op-reason "controller paused; lock implementation pending"
```

Independent issues may proceed in parallel only when their active claims have no overlapping issue, file, module, event, service, market, domain, or runtime locks.

## Progress Outcomes

Every issue-backed development pass should end in one of these states:

| Outcome | Meaning |
|---|---|
| `implemented` | Code/docs/tests changed, validation passed, commit pushed, issue updated. |
| `implemented_partial` | A named sub-slice was completed with commit, validation, pushed evidence, and remaining scope. |
| `blocked_once` | Exact blocker and next unblock action were recorded; repeat unchanged blockers should no-op. |
| `handoff_ready` | File scope, next command, tests, and acceptance target are ready for the next development-agent pass. |
| `no_material_change` | State unchanged; no new issue comment or full handoff should be written. |

An issue comment without a blocker change, implementation evidence, validation result, commit link, ownership change, or acceptance-criteria update is `no_material_change`.

## Agent Personas

| Persona | Role |
|---|---|
| `master-controller` | Decide mode and route work. |
| `development-agent` | Implement issue-backed code/docs/tests. |
| `development-end-phase` | Merge, test, restart safely, update readiness. |
| `pregame-integrity` | Gate service/data/CLOB readiness. |
| `pregame-planner` | Research and StrategyPlanJSON proposals. |
| `live-monitor-analyst` | Watch live games and patch only critical issues. |
| `postgame-reviewer` | Event review, performance, missed opportunities. |
| `wnba-data-agent` | WNBA data/replay/readiness. |
| `docs-memory-agent` | Repo docs, Obsidian, issue hygiene. |
| `issue-backlog-manager` | GitHub issue taxonomy, labels, planned/sprint backlog. |
| `system-architect-spec-enforcer` | Architecture, service boundaries, source-of-truth contracts. |
| `basketball-intelligence-agent` | Basketball scenario/regime/PBP/quarter/microstructure logic. |
| `llm-orchestration-agent` | Model routing, cost controls, prompt contracts, Codex fallback. |
| `risk-ledger-agent` | Bankroll sleeves, exposure, inventory, lifecycle attribution. |
| `profile-research-agent` | Winning profile studies and caveated implications. |
| `future-domain-research-agent` | New market/domain incubation. |
| `janus-covered-market-portfolio-agent` | Internal Janus covered-market portfolio/inventory management for NBA/WNBA and future Janus-owned lanes. |
| `codex-global-portfolio-agent` | Active global portfolio management intent: existing-position target/exit/rebuy decisions, proactive trend-opportunity scouting in uncovered categories, return-receipt tracking, and gated execution only through `global_portfolio_manager_contract.md` plus `codex_tooling_contract.md`. |
| `global-portfolio-agent` | Compatibility alias for `codex-global-portfolio-agent`; do not use for internal Janus covered-market portfolio work. |

## Initial Queue Storage

Until a DB-backed or GitHub-native queue exists, the queue can be represented by:

- GitHub issues as durable backlog.
- `local/shared/handoffs/development-agent/master_queue.md` as local operational bridge.
- `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md` as issue seed.
- Future generated queue artifact under `local/shared/artifacts/final-system-controller/`.
