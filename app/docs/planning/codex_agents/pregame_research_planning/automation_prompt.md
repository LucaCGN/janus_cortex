# Automation Prompt

Schedule:

```text
FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=14;BYMINUTE=0
```

Prompt:

```text
Run one JANUS Pregame Research & Planning pass using app\docs\planning\codex_agents\pregame_research_planning\README.md as the contract.

Work from C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex. Derive today's session date in America/Sao_Paulo.

Read the operating plan, prompt index, Pregame Research README, daily-live-validation status, latest pregame integrity report, latest postgame report, current StrategyPlanJSON files, and relevant replay/benchmark/ML/LLM/controller handoffs.

Run janus_status and export_event_context for each watched/matched event from the integrity handoff. Use local DB/API context plus web research for current injuries, lineups, rotations, rest/fatigue, game stakes, matchup context, beat-report news, and late market-relevant facts.

For each game, produce a structured analysis: event/market state, current CLOB prices and spreads, current plan state, team/player matchup context, market mispricing thesis, strategy-family fit, exact trigger design, stop/target/hedge logic, shadow-only/disabled families, and LLM live-revision watchpoints.

Do not define order sizing, budget, or portfolio exposure. The owner/operator controls sizing policy; current live tooling injects the minimum-order policy separately. If a StrategyPlanJSON compatibility field such as budget_usd, max_positions, or size is present, treat it as non-authoritative metadata and state that operator policy overrides it.

Write local\shared\reports\daily-live-validation\pregame_research_YYYY-MM-DD.md and local\shared\reports\daily-live-validation\live_test_plan_YYYY-MM-DD.md. Submit research with codex_tool\submit_pregame_research.py. Submit revised StrategyPlanJSON only if the research changes assumptions, triggers, guardrails, or active/shadow strategy choices; if revised, dry-evaluate with codex_tool\evaluate_strategy_plan.py.

Use model-tier routing from app\docs\planning\llm_model_routing.md: gpt-5.4-nano for extraction/summaries, gpt-5.4-mini for routine game synthesis, and gpt-5.5 only for critical conflicting lineup/market context or high-uncertainty final plan review.

Do not place orders. Do not run --execute. Do not treat dry-run/compiler intents as live authorization. Preserve execution guardrails as context only: limit-only, direct-CLOB-gated, fresh-scoreboard/orderbook-gated, and immediate target/stop/hedge policy required.
```
