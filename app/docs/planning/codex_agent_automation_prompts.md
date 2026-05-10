# Codex Agent Automation Prompts

Use one pinned Codex chat per agent. Every agent should treat DB/API output and tracked docs as authoritative; chat memory is secondary.

Common required reads:

- `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\app\docs\planning\janus_agentic_backend_operating_plan.md`
- `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local\shared\pipeline\README.md`
- `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local\shared\handoffs\`
- `python codex_tool\janus_status.py`

## JANUS - Post Game System Review

Schedule:

`FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=4;BYMINUTE=0`

Prompt:

```text
Run the Janus postgame system review. Read the operating plan, current ops status, all handoff files, latest live-validation artifacts, latest strategy-plan versions, order/fill/position reconciliation, benchmark stack, replay reports, ML reports, and LLM reports.

Evaluate the prior day per event: realized portfolio impact, strategy-plan quality, LLM revision usefulness, deterministic lane behavior, ML context usefulness, missed opportunities, stale feeds, CLOB latency, orderbook gaps, manual interventions, fills, cancels, stops, hedges, and replay divergence.

Write a concise postgame report under local\shared\reports\daily-live-validation and update local\shared\handoffs\daily-live-validation\status.md plus any lane handoff that needs next-day work. Do not place orders. If no material data changed, update status briefly and stop.
```

## JANUS - Development Agent

Schedule:

`FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=6,8,10;BYMINUTE=0`

Prompt:

```text
Run one Janus development pass. Read the operating plan, current ops status, postgame review, benchmark stack, replay/ML/LLM handoffs, and git status.

Work on code only when there is a clear routed task. Priorities are: keep main clean, fix live safety blockers, improve strategy-plan execution, improve replay fidelity from captured watch sessions, improve lane tests, and update tracked docs. Do not touch live orders. Do not commit unrelated dirty files. Run targeted tests for any code touched and update the relevant handoff with what changed, what passed, and what remains blocked.
```

## JANUS - Pregame Integrity Check

Schedule:

`FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=12;BYMINUTE=0`

Prompt:

```text
Run the Janus pregame integrity check. Read the operating plan and current ops status. Run python codex_tool\run_integrity_check.py. Verify local root resolution, API availability, DB/migration state, current watchlist, CLOB collateral, direct CLOB open orders, portfolio mirror, stale feeds, current strategy-plan schema validity, and live-run safety gates.

If a code or config bug blocks safe operation, fix the smallest safe issue and run targeted tests. If live money is not safe, write the blocker and force dry-run/entries-disabled posture in the daily-live-validation handoff. Do not create discretionary trades.
```

## JANUS - Pregame Research & Planning

Schedule:

`FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=14;BYMINUTE=0`

Prompt:

```text
Run pregame research and planning for today's watched events. Read the operating plan, current ops status, current promoted stack, replay/ML/LLM handoffs, and event contexts exported with codex_tool\export_event_context.py.

Use local DB/API context plus web research when useful for current injuries, lineup, narrative, and matchup context. Write dated pregame research under local\shared\reports\daily-live-validation. Submit it with codex_tool\submit_pregame_research.py. If a strategy-plan JSON should be created or revised, write it under local\shared\artifacts\strategy-plans and submit it with codex_tool\submit_strategy_plan.py. Keep live sizing at minimum practical order size until profitable multi-day evidence exists.
```

## JANUS - Live System Monitor

Schedule:

`FREQ=MINUTELY;INTERVAL=30`

Prompt:

```text
Run one bounded Janus live-monitor tick. Read the operating plan, current ops status, active strategy plans, current order/fill/position truth, direct CLOB collateral, and live event context.

Call codex_tool\run_live_monitor_tick.py. Reconcile human/manual interventions with codex_tool\reconcile_orders.py when detected. Trigger a strategy-plan evaluation or revision when an order fires, a manual intervention appears, a quarter ends, a strategy trigger occurs, feed state changes, or portfolio truth is inconsistent. Use codex_tool\evaluate_strategy_plan.py for evaluation or audited execution checks. Report only material changes: signal, order intent, order submit/fill/cancel, missing target/stop/hedge, stale feed, CLOB issue, manual intervention, final game, or bug. Do not submit live orders unless the active strategy plan, integrity status, and explicit run posture all allow it.
```
