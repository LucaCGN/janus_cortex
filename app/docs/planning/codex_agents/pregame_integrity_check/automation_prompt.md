# Automation Prompt

Schedule:

```text
FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=13;BYMINUTE=0
```

Prompt:

```text
Run one JANUS Pregame Integrity Check pass using app\docs\planning\codex_agents\pregame_integrity_check\README.md as the contract.

Work from C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex. Derive today's session date in America/Sao_Paulo.

Read the operating plan, prompt index, Pregame Integrity Check README, daily-live-validation status, Development Agent status if present, latest development pass report if present, and latest postgame development handoff.

Run janus_status, git status, run_data_refresh, and run_integrity_check. Verify that the API, DB, agentic schema, Codex tools, recent development sprint state, direct CLOB readiness, direct open orders/positions, stale mirror classification, watched events, market/outcome/token matching, current orderbook refresh, event-context export, StrategyPlanJSON validation/evaluation tools, and live-monitor tick tool are working or have explicit blockers.

Use model-tier routing from app\docs\planning\llm_model_routing.md: gpt-5.4-nano for status normalization, gpt-5.4-mini for the readiness judgment, and gpt-5.5 only for ambiguous live-money safety failures.

Produce a green/yellow/red gate for the Pregame Research & Planning agent. Write local\shared\reports\daily-live-validation\pregame_integrity_YYYY-MM-DD.md and update local\shared\handoffs\daily-live-validation\status.md with exact blockers, allowed next actions, and what Pregame Research must not do.

Do not place orders. Do not create discretionary StrategyPlanJSON. Do not run deep development experiments. Make only tiny tested fixes if directly required to unblock this integrity gate; otherwise route issues back to the Development Agent.
```
