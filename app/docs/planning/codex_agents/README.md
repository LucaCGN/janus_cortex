# Codex Agents

This directory contains the organized prompt contracts for the five Janus Codex agents.

Janus is the independent runtime service. Codex agents are the external CI/CD, research, audit, and development loop. Agents must read API/DB/runtime state first and treat chat history as non-authoritative.

## Agent Folders

| Agent | Folder | Status |
|---|---|---|
| `JANUS - Post Game System Review` | `post_game_system_review` | active contract |
| `JANUS - Development Agent` | `development_agent` | active contract |
| `JANUS - Pregame Integrity Check` | `pregame_integrity_check` | active contract |
| `JANUS - Pregame Research & Planning` | `pregame_research_planning` | pending focused refinement |
| `JANUS - Live System Monitor` | `live_system_monitor` | pending focused refinement |

## Shared Rules

- Start with `python codex_tool\janus_status.py`.
- Direct CLOB collateral, orders, fills, and positions are authoritative over stale local mirrors.
- Runtime files under `local\` are not committed. Summarize material runtime state in handoffs and reports.
- Do not fabricate state when the API or DB is unavailable.
- Do not place live orders unless the active StrategyPlanJSON, direct CLOB truth, and integrity gate explicitly allow it.
- Keep reports useful for the next agent in sequence, not just for human reading.
