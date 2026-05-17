# Codex Agents

This directory contains the organized prompt contracts for the five Janus Codex agents.

Janus is the independent runtime service. Codex agents are the external CI/CD, research, audit, and development loop. Agents must read API/DB/runtime state first and treat chat history as non-authoritative.

The shared file/folder communication contract is:

`app\docs\planning\codex_agents\shared_file_communication_contract.md`

Use that document as the source of truth for how agents exchange state through `local\shared\handoffs`, `local\shared\reports`, `local\shared\artifacts`, tracked planning docs, and Codex tools.

## Agent Folders

| Agent | Folder | Status |
|---|---|---|
| `JANUS - Post Game System Review` | `post_game_system_review` | active contract |
| `JANUS - Development Agent` | `development_agent` | active contract |
| `JANUS - Pregame Integrity Check` | `pregame_integrity_check` | active contract |
| `JANUS - Pregame Research & Planning` | `pregame_research_planning` | active contract |
| `JANUS - Live System Monitor` | `live_system_monitor` | active contract |

## Shared Rules

- Start with `python codex_tool\janus_status.py`.
- Read `shared_file_communication_contract.md` before relying on any handoff/report/artifact path.
- Direct CLOB collateral, orders, fills, and positions are authoritative over stale local mirrors.
- Runtime files under `local\` are not committed. Summarize material runtime state in handoffs and reports.
- Do not fabricate state when the API or DB is unavailable.
- Live monitor execution is service-owned. Codex monitors `codex_tool\live_strategy_worker_status.py` and starts the worker with `codex_tool\start_live_strategy_worker.py --execute --live-money` only when active plans and all StrategyPlanJSON/direct-CLOB/feed gates pass.
- Pregame Research is context-only: it proposes thesis, strategy families, triggers, stop/target/hedge logic, and revision watchpoints, but not order size, budget, or portfolio exposure.
- Live order sizing is controlled by operator policy supplied to Janus/live tooling, currently minimum `5` shares and minimum `$1.00` buy notional.
- Follow model-tier routing in `app\docs\planning\llm_model_routing.md`: nano for extraction/summaries, mini for routine reasoning, full `gpt-5.5` for critical decisions.
- Do not place live orders unless the active StrategyPlanJSON, direct CLOB truth, quote/score state, and integrity gate explicitly allow it.
- Keep reports useful for the next agent in sequence, not just for human reading.
