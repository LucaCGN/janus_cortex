# Codex Agent Automation Prompt Index

This file is an index. Agent-specific prompt contracts live under:

`app\docs\planning\codex_agents\`

Janus is independent runtime infrastructure. Codex agents, or an equivalent external agent framework, provide CI/CD, research, audits, postgame review, live monitoring, and continuous development.

## Canonical Agent Docs

| Agent | Folder | Automation Prompt |
|---|---|---|
| `JANUS - Post Game System Review` | `app\docs\planning\codex_agents\post_game_system_review` | `automation_prompt.md` |
| `JANUS - Development Agent` | `app\docs\planning\codex_agents\development_agent` | `automation_prompt.md` |
| `JANUS - Pregame Integrity Check` | `app\docs\planning\codex_agents\pregame_integrity_check` | `automation_prompt.md` |
| `JANUS - Pregame Research & Planning` | `app\docs\planning\codex_agents\pregame_research_planning` | `automation_prompt.md` |
| `JANUS - Live System Monitor` | `app\docs\planning\codex_agents\live_system_monitor` | `automation_prompt.md` |

## Shared Context

- repository root: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
- runtime root: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`
- API root: `http://127.0.0.1:8010`
- live account id: `56964015-5935-5035-bdab-b056c9277146`
- operating plan: `app\docs\planning\janus_agentic_backend_operating_plan.md`
- daily handoff: `local\shared\handoffs\daily-live-validation\status.md`
- Codex tools: `codex_tool\`

## Shared Rules

- Start with `python codex_tool\janus_status.py`.
- API output, DB state, direct CLOB truth, tracked docs, and runtime handoffs are authoritative.
- Chat memory is useful but not authoritative.
- Direct CLOB collateral, open orders, and open positions are authoritative over stale local portfolio mirrors.
- Minimum live buy order remains `5` shares and `$1.00` notional until multiple profitable days and clean reconciliation justify resizing.
- Live game execution must be owned by the Janus live strategy worker. Codex may inspect, start, stop, or trigger a one-off worker tick through `codex_tool\live_strategy_worker_status.py`, `start_live_strategy_worker.py`, `stop_live_strategy_worker.py`, and `run_live_strategy_worker_tick.py`, but Codex prompts must not be the only recurring scheduler during a game.
- Pregame Research is context-only and does not define order size, notional budget, or portfolio exposure.
- Live order sizing is operator policy supplied to Janus/live tooling, not strategy-plan sizing metadata.
- Model-tier routing lives in `app\docs\planning\llm_model_routing.md`: nano for extraction/summaries, mini for routine reasoning, and `gpt-5.5` for critical decisions.
- Runtime files under `local\` are untracked; summarize material runtime changes in handoffs/reports instead of committing them.

## Schedule Table

| Agent | Schedule | RRULE |
|---|---:|---|
| `JANUS - Post Game System Review` | Daily 04:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=4;BYMINUTE=0` |
| `JANUS - Development Agent` | Every 30 minutes; self-gated by BRT time | `FREQ=MINUTELY;INTERVAL=30` |
| `JANUS - Pregame Integrity Check` | Daily 13:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=13;BYMINUTE=0` |
| `JANUS - Pregame Research & Planning` | Daily 14:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=14;BYMINUTE=0` |
| `JANUS - Live System Monitor` | Every 30 minutes, self-gated by active events | `FREQ=MINUTELY;INTERVAL=30` |

## Current Focus

All five prompt contracts are active.
