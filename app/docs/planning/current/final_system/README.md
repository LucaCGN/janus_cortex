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
| `automation/master_controller_contract.md` | Mutable source-of-truth contract for the 10-minute controller automation. |
| `automation/task_queue_schema.md` | Initial queue, lane, lock, issue, and agent state schema. |
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
