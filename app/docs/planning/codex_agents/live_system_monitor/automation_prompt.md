# JANUS - Live System Monitor Automation Prompt

Run one bounded live-money monitor tick for Janus by checking the service-owned live strategy worker, not by becoming the scheduler yourself.

Workspace:

- Repo: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
- Canonical local root: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\local`
- Account: `56964015-5935-5035-bdab-b056c9277146`

Read before acting:

- `app\docs\planning\janus_agentic_backend_operating_plan.md`
- `app\docs\planning\codex_agents\live_system_monitor\README.md`
- `local\shared\handoffs\daily-live-validation\status.md`
- Today's `local\shared\reports\daily-live-validation\pregame_research_YYYY-MM-DD.md`
- Today's `local\shared\reports\daily-live-validation\live_test_plan_YYYY-MM-DD.md`
- Current StrategyPlanJSON files under `local\shared\artifacts\strategy-plans\YYYY-MM-DD\`

Primary status command:

```powershell
python codex_tool\live_strategy_worker_status.py
```

If active games have current StrategyPlanJSON files and the worker is not running, start it with the operator-approved minimum-order policy:

```powershell
python codex_tool\start_live_strategy_worker.py --session-date YYYY-MM-DD --event-id nba-nyk-phi-YYYY-MM-DD --event-id nba-sas-min-YYYY-MM-DD --account-id 56964015-5935-5035-bdab-b056c9277146 --source janus-live-strategy-worker --interval-seconds 30 --execute --live-money --max-intents 2 --min-size 5 --min-buy-notional-usd 1 --share-precision 3
```

Use this one-off debug command only if the worker is stopped or you need a bounded immediate tick:

```powershell
python codex_tool\run_live_strategy_worker_tick.py --session-date YYYY-MM-DD --event-id nba-nyk-phi-YYYY-MM-DD --event-id nba-sas-min-YYYY-MM-DD --account-id 56964015-5935-5035-bdab-b056c9277146 --source janus-live-strategy-worker --execute --live-money --max-intents 2 --min-size 5 --min-buy-notional-usd 1 --share-precision 3
```

Adjust `--event-id` values to the current day's StrategyPlanJSON event IDs from `janus_status.py` or the daily handoff.

Rules:

- Live execution must be `dry_run=false`; do not run live execution as dry-run.
- The live worker heartbeat under `local\shared\artifacts\live-strategy-worker\YYYY-MM-DD\heartbeat.json` must exist and update during active games. If it is missing, stale, or stopped, report it as a P0 runtime blocker and start the worker only when gates are valid.
- Every tick must run shadow evaluation and then the live execution pass only if gates are valid.
- Use only limit orders.
- Minimum order constraints are operator policy supplied to the tick tool: `5` shares and at least `$1.00` buy notional.
- Do not let Codex Pregame Research or StrategyPlanJSON sizing fields override operator sizing policy.
- Do not place a new order unless direct CLOB collateral is ready, direct open orders/positions are reconciled, the current plan exists, current orderbook and scoreboard states are fresh, spread is within plan limits, score gap is within plan limits, and the strategy emits a valid intent.
- Treat the local portfolio mirror as non-authoritative while quarantined.
- If a live buy fills, ensure a protective target/stop/hedge path exists before allowing additional exposure.
- If the user manually opens/closes a position, pause new exposure, reconcile it through operator-intervention tooling, and report the adoption/protection state.
- If no events are live, no plans are current, feeds are stale, or no strategy intent is valid, stop quickly after writing the blocker summary.
- Do not use `codex_tool\run_live_strategy_tick.py` as the recurring scheduler. It is now the internal tick engine behind the service worker and may be used only for local debugging when explicitly needed.
- Use model-tier routing from `app\docs\planning\llm_model_routing.md`: gpt-5.4-nano for tick summaries, gpt-5.4-mini for normal no-position monitoring, and gpt-5.5 for open exposure, manual intervention, missing protection, stale recovery, stop, or hedge decisions.

Write/update:

- `local\shared\handoffs\daily-live-validation\status.md` with material state only: live order submitted, fill, target/stop issue, feed outage, stale data, unexpected direct CLOB truth, or game final.
- `local\shared\artifacts\daily-live-validation\YYYY-MM-DD\monitor_ticks.jsonl` if additional human-readable tick notes are needed.

Never:

- Never use market orders.
- Never execute against stale or missing market/scoreboard state.
- Never use the quarantined portfolio mirror as authority.
- Never continue increasing exposure after an unresolved fill, missing target, or manual intervention.
