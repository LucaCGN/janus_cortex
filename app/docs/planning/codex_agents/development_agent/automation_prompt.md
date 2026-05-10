# Automation Prompt

Schedule:

```text
FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=6,7,8,9,10,11;BYMINUTE=0,30
```

Prompt:

```text
Run one JANUS Development Agent development-window pass using app\docs\planning\codex_agents\development_agent\README.md as the contract.

Work from C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex. Derive today's session date in America/Sao_Paulo.

Read the operating plan, prompt index, Development Agent README, daily-live-validation status, latest postgame report, latest postgame_development_handoff, local\shared\handoffs\development-agent\status.md if present, and local\shared\handoffs\development-agent\master_queue.md if present. Run janus_status and git status.

Continue the previous Development Agent task if it is mid-flight and still valid. Otherwise select the highest-priority routed task that can be safely implemented and tested in this pass. Prefer P0 live-safety/data-integrity work from the latest handoff: account-scoped fill de-duplication and PnL reconciliation, StrategyPlanJSON requirement/blocking for live-reviewed events, generic watch-session tick/trade persistence, replay creation from watch sessions, manual-intervention adoption, or stale mirror quarantine. After immediate safety work is moving, select deeper development items from the master queue: complex shadow running, new deterministic strategies, replay/backtest experiments, ML methods, LLM plan/prompt experiments, and live-vs-shadow comparison tooling.

Use the automation window for substantive engineering. Do not stop after a trivial 5-minute fix if there is safe queued work available. If the first slice finishes early, continue into the next compatible task, run a meaningful backtest/shadow experiment, or write an implementation-ready design with tests and acceptance criteria.

Do not place orders. Do not touch runtime local artifacts except handoff/report updates. Do not commit unrelated dirty files. Run targeted tests, then broader tests when practical. Update local\shared\handoffs\daily-live-validation\status.md and local\shared\handoffs\development-agent\status.md. Optionally write local\shared\reports\daily-live-validation\development_pass_YYYY-MM-DD.md with files changed, tests run, remaining blockers, readiness impact, and the exact next recommended task for the next 30-minute trigger.
```
