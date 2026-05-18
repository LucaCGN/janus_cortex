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
| `source_of_truth_map.md` | Authority stack and stable automation anchor map. |
| `market_scope_registry.md` | Market/domain axes, maturity stages, and scope registry. |
| `automation/master_controller_contract.md` | Mutable source-of-truth contract for the recurring controller automation. |
| `automation/controller_decision_tree.md` | Axis-based controller routing rules. |
| `automation/agent_persona_registry.md` | Personas, authority limits, and output expectations. |
| `automation/task_queue_schema.md` | Initial queue, lane, lock, issue, and agent state schema. |
| `automation/issue_taxonomy.md` | GitHub issue label and sprint-readiness taxonomy. |
| `automation/backlog_layers.md` | Idea, planned, sprint, active queue, and evidence backlog layers. |
| `automation/subagent_parallelism_contract.md` | Rules for Codex sub-agent use, locks, and integration. |
| `automation/global_portfolio_explorer_contract.md` | Separate read-only global portfolio explorer automation contract. |
| `automation/docs_memory_health_check.md` | Checklist for repo, Obsidian, GitHub, and handoff health. |
| `backlog/immediate_issue_seed_2026-05-17.md` | Issue-ready backlog from the current P0/P1 lanes and new missing pieces. |
| `obsidian/bootstrap_map.md` | Obsidian vault structure and repo-linking plan. |

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

## Current Activation Gate

Do not enable the recurring controller until today's missing event data is manually reconciled by the operator in the Codex app and runtime handoffs are refreshed.

## Current Workspace Decisions

- Runtime helpers default to the repo-local `local` folder when `JANUS_LOCAL_ROOT` is unset.
- Obsidian remains the curated second-brain vault, not a runtime-artifact root.
- Every repo commit must be pushed to GitHub promptly because GitHub is the operator's current remote interaction surface.
- The tracked `frontend/` module is removed. Future UI work requires an explicit issue, source-of-truth update, and operator approval.
- The global portfolio explorer is separate from the master controller and remains read-only until a future approved execution policy exists.
