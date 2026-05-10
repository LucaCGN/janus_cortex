# Automation Prompt

Schedule:

```text
FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=4;BYMINUTE=0
```

Prompt:

```text
Run one JANUS Post Game System Review pass using app\docs\planning\codex_agents\post_game_system_review\README.md and report_contract.md as the contract.

Work from C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex. Derive the reviewed session date in America/Sao_Paulo.

Run janus_status, run_postgame_review, and run_integrity_check. Review each completed game, each active StrategyPlanJSON strategy, deterministic shadow family, controller output, ML sidecar, LLM plan/revision, and manual intervention. Use local event context and web research when needed to explain final-game behavior and missed opportunity.

Use model-tier routing from app\docs\planning\llm_model_routing.md: gpt-5.4-nano for raw status/artifact summaries, gpt-5.4-mini for normal per-game/per-lane review, and gpt-5.5 only for material PnL failures, missed opportunities, manual-intervention ambiguity, or next-day development prioritization.

Write postgame_report_YYYY-MM-DD.md, postgame_development_handoff_YYYY-MM-DD.md, update local\shared\handoffs\daily-live-validation\status.md, and record exact next tasks for the development agent. Do not place orders.
```
