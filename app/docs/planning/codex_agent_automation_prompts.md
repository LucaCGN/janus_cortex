# Codex Agent Automation Prompt Index

This file is an index. Agent-specific prompt contracts live under:

`app\docs\planning\codex_agents\`

Janus is independent runtime infrastructure. Codex agents, or an equivalent external agent framework, provide CI/CD, research, audits, postgame review, live monitoring, and continuous development.

## Canonical Agent Docs

| Agent | Folder | Automation Prompt |
|---|---|---|
| `JANUS - Post Game System Review` | `app\docs\planning\codex_agents\post_game_system_review` | `automation_prompt.md` |
| `JANUS - Development Agent` | `app\docs\planning\codex_agents\development_agent` | `automation_prompt.md` |
| `JANUS - Pregame Integrity Check` | `app\docs\planning\codex_agents\pregame_integrity_check` | pending focused contract |
| `JANUS - Pregame Research & Planning` | `app\docs\planning\codex_agents\pregame_research_planning` | pending focused contract |
| `JANUS - Live System Monitor` | `app\docs\planning\codex_agents\live_system_monitor` | pending focused contract |

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
- Runtime files under `local\` are untracked; summarize material runtime changes in handoffs/reports instead of committing them.

## Schedule Table

| Agent | Schedule | RRULE |
|---|---:|---|
| `JANUS - Post Game System Review` | Daily 04:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=4;BYMINUTE=0` |
| `JANUS - Development Agent` | Every 30 minutes, 06:00-11:30 BRT | Use 12 single-time weekly automations, or `FREQ=MINUTELY;INTERVAL=30` with prompt self-gate |
| `JANUS - Pregame Integrity Check` | Daily 12:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=12;BYMINUTE=0` |
| `JANUS - Pregame Research & Planning` | Daily 14:00 BRT | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=14;BYMINUTE=0` |
| `JANUS - Live System Monitor` | Every 30 minutes, self-gated by active events | `FREQ=MINUTELY;INTERVAL=30` |

## Current Focus

The Post Game System Review and Development Agent contracts are active.

Next folders should be refined one at a time after the Development Agent contract is validated in a real run.
