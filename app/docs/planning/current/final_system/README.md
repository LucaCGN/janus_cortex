# Janus Final System Workspace

Status: initial control workspace
Created: 2026-05-17

## Purpose

This folder is the transition layer between the current Janus system and the next-generation operating framework.

It exists to avoid another large, fragile rewrite. The rule for this phase is:

1. Normalize reviewed premises.
2. Create stable docs and queues.
3. Convert immediate work into issue-ready backlog.
4. Bootstrap Obsidian as curated memory.
5. Run a recurring controller automation that reads mutable repo docs instead of relying on chat memory.
6. Only then write final specs and begin long development execution.

## Current Files

| File | Role |
|---|---|
| `premise_decisions_2026-05-17.md` | Normalized operator review of the premise register. |
| `global_ego_and_purpose.md` | Global operating identity, User/Codex/Janus triad, ambition-vs-authority rules, and economic model-cost stance. |
| `source_of_truth_map.md` | Authority stack and stable automation anchor map. |
| `market_scope_registry.md` | Market/domain axes, maturity stages, and scope registry. |
| `automation/master_controller_contract.md` | Mutable source-of-truth contract for the recurring controller automation. |
| `automation/controller_decision_tree.md` | Axis-based controller routing rules. |
| `automation/agent_persona_registry.md` | Personas, authority limits, and output expectations. |
| `automation/task_queue_schema.md` | Initial queue, lane, lock, issue, and agent state schema. |
| `automation/issue_taxonomy.md` | GitHub issue label and sprint-readiness taxonomy. |
| `automation/backlog_layers.md` | Idea, planned, sprint, active queue, and evidence backlog layers. |
| `automation/subagent_parallelism_contract.md` | Rules for Codex sub-agent use, locks, and integration. |
| `automation/codex_tooling_contract.md` | Codex tool split between Janus API wrappers and independent Polymarket fallback tooling. |
| `automation/global_portfolio_explorer_contract.md` | Legacy/read-only global portfolio discovery contract. |
| `automation/global_portfolio_manager_contract.md` | Active portfolio-manager intent contract for existing-position management, trend scouting, and gated execution. |
| `automation/docs_memory_health_check.md` | Checklist for repo, Obsidian, GitHub, and handoff health. |
| `architecture/current_architecture_and_degradation_map.md` | Current FastAPI modular-monolith, service dependency, degradation, and legacy-controller classification map. |
| `backlog/immediate_issue_seed_2026-05-17.md` | Issue-ready backlog from the current P0/P1 lanes and new missing pieces. |
| `backlog/premise_to_backlog_map_2026-05-18.md` | Post-foundation premise-to-issue map for follow-up issues `#30`, `#32`, and `#37-#50`. |
| `obsidian/bootstrap_map.md` | Obsidian vault structure and repo-linking plan. |
| `obsidian/modular_curation_policy.md` | Edit-before-create policy that keeps Obsidian modular instead of append-only. |

## Authority

This folder is draft planning authority only. It does not override:

1. Direct CLOB truth.
2. Janus DB/API state.
3. Runtime artifacts and handoffs for active games.
4. Existing production contracts.

It becomes binding only after review and promotion into core docs.

## Operating Rule

The automation prompt should remain small and stable. Behavior should be changed by editing this folder and the Obsidian notes it references, not by rewriting the immutable automation prompt.

The controller must evaluate market/domain scope before applying lifecycle rules. `pregame`, `live`, and `postgame` are basketball lifecycle phases, not universal Janus modes.

Every controller/persona pass should inherit `global_ego_and_purpose.md`: Janus is an expectation-markets system with basketball as the immediate implementation domain, not the system boundary. That identity does not override the authority stack or safety gates.

Obsidian maintenance must follow `obsidian/modular_curation_policy.md`. Future agents should edit, merge, split, supersede, and relink existing notes before creating new notes. A docs-memory pass that creates no new note can still be successful if it improves navigation, removes duplication, or clarifies authority.

## Current Activation Gate

Do not enable the recurring controller until today's missing event data is manually reconciled by the operator in the Codex app and runtime handoffs are refreshed.

## Current Workspace Decisions

- Runtime helpers default to the repo-local `local` folder when `JANUS_LOCAL_ROOT` is unset.
- Obsidian remains the curated second-brain vault, not a runtime-artifact root.
- Every repo commit must be pushed to GitHub promptly because GitHub is the operator's current remote interaction surface.
- The tracked `frontend/` module is removed. Future UI work requires an explicit issue, source-of-truth update, and operator approval.
- The `janus-portfolio-manager` automation is separate from the master controller and is intended to manage the broader global portfolio, including existing-position target/exit/rebuy maintenance and trend-following scouting. It may trade only through an approved Janus portfolio order-management path or approved independent Polymarket fallback path with direct CLOB/account truth, separate risk budget, minimum-order compliance, ledger/idempotency evidence, reconciliation plan, and kill-switch gates. The `codex_tools/polymarket` path is not authority until `#53` is implemented and approved.
