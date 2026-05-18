# Janus Task Queue And Lane Schema

Status: initial draft

## Purpose

Define the minimum structure needed for the master controller to reason about tasks, lanes, GitHub issues, branches, worktrees, agents, and live-event locks.

This is a schema contract, not yet an implementation.

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
| `global-portfolio` | Read-only portfolio scan, target/rebuy proposals, concentration review. |
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
| `global-portfolio-agent` | Future read-only global Polymarket portfolio review. |

## Initial Queue Storage

Until a DB-backed or GitHub-native queue exists, the queue can be represented by:

- GitHub issues as durable backlog.
- `local/shared/handoffs/development-agent/master_queue.md` as local operational bridge.
- `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md` as issue seed.
- Future generated queue artifact under `local/shared/artifacts/final-system-controller/`.
