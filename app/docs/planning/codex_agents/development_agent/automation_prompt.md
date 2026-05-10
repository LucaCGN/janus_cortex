# Automation Prompt

Schedule:

```text
FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=6,8,10;BYMINUTE=0
```

Prompt:

```text
Run one JANUS Development Agent pass using app\docs\planning\codex_agents\development_agent\README.md as the contract.

Work from C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex. Derive today's session date in America/Sao_Paulo.

Read the operating plan, prompt index, Development Agent README, daily-live-validation status, latest postgame report, and latest postgame_development_handoff. Run janus_status and git status.

Select the highest-priority routed task that can be safely implemented and tested in this pass. Prefer P0 live-safety/data-integrity work from the latest handoff: account-scoped fill de-duplication and PnL reconciliation, StrategyPlanJSON requirement/blocking for live-reviewed events, generic watch-session tick/trade persistence, replay creation from watch sessions, manual-intervention adoption, or stale mirror quarantine.

Make the smallest coherent code/docs/tests change. Do not place orders. Do not touch runtime local artifacts except handoff/report updates. Do not commit unrelated dirty files. Run targeted tests, then broader tests when practical. Update local\shared\handoffs\daily-live-validation\status.md and optionally write local\shared\reports\daily-live-validation\development_pass_YYYY-MM-DD.md with files changed, tests run, remaining blockers, and readiness impact.
```
