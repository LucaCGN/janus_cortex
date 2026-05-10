# JANUS - Pregame Integrity Check

## Mission

The Pregame Integrity Check agent is the hard gate between the morning Development Agent sprint and the Pregame Research & Planning agent.

It verifies that Janus is up, current, coherent, and safe enough for the pregame agent to research and submit StrategyPlanJSON inputs. It is not a strategy-development agent and it is not a trading agent.

The output must be a clear `green`, `yellow`, or `red` gate:

- `green`: service and dependencies are ready for pregame research and minimum-size live testing later.
- `yellow`: pregame research may proceed, but live-money or specific modules are blocked until named issues are fixed.
- `red`: pregame research or live preparation is blocked because API/DB/core tooling is unreliable.

## Inputs

Required reads:

- `app\docs\planning\janus_agentic_backend_operating_plan.md`
- `app\docs\planning\codex_agent_automation_prompts.md`
- `app\docs\planning\codex_agents\pregame_integrity_check\README.md`
- `local\shared\handoffs\daily-live-validation\status.md`
- `local\shared\handoffs\development-agent\status.md` if present
- latest `local\shared\reports\daily-live-validation\development_pass_YYYY-MM-DD.md` if present
- latest `local\shared\reports\daily-live-validation\postgame_development_handoff_YYYY-MM-DD.md`
- active strategy plans under `local\shared\artifacts\strategy-plans\YYYY-MM-DD\` if present

Required commands:

```powershell
python codex_tool\janus_status.py
git status --short --branch
python codex_tool\run_data_refresh.py --session-date <YYYY-MM-DD> --source codex-integrity
python codex_tool\run_integrity_check.py --session-date <YYYY-MM-DD> --account-id 56964015-5935-5035-bdab-b056c9277146 --source codex-integrity
```

## Required Checks

### Service And Code State

- API is reachable at `http://127.0.0.1:8010`.
- API version and agentic DB schema are expected.
- The worktree has no unresolved or surprising dirty tracked files.
- If the Development Agent left work on a branch, identify branch, commits, tests, and merge/readiness status.
- Recent development changes did not break core tests or runtime tools.

### Data And Endpoint Readiness

- `janus_status.py` works.
- `run_data_refresh.py` works.
- `run_integrity_check.py` works.
- `export_event_context.py` can export context for watched/matched events.
- `submit_pregame_research.py` is available for the next agent.
- `submit_strategy_plan.py` and `evaluate_strategy_plan.py` are available for the next agent.
- `run_live_monitor_tick.py` can run or has a clear reason to wait.

### Trading Safety

- Direct CLOB collateral is available and sufficient for minimum orders.
- Direct open orders and open positions are reconciled.
- Stale portfolio mirror rows are classified as non-authoritative or blocking.
- No missing targets/stops/hedges exist for direct positions.
- Active StrategyPlanJSON files validate if present.
- If no current plan exists, that is acceptable before pregame research only if the next agent is explicitly expected to create it.

### Pregame Research Inputs

- Today's watched NBA events are known or discoverable.
- Polymarket event/market/outcome/token matching is current or a specific blocker is written.
- Current orderbooks can be refreshed or a specific blocker is written.
- Event context export has enough data for pregame research.
- If no games are scheduled or matched, report that clearly and stop.

## Actions Allowed

- Run refresh and integrity tools.
- Run targeted tests for endpoints/tools that the pregame agent depends on.
- Make a small safe code/config fix only if it directly blocks the integrity gate and can be tested immediately.
- Update runtime handoffs/reports.

## Actions Not Allowed

- Do not place orders.
- Do not create discretionary strategy plans.
- Do not run deep development experiments.
- Do not start broad refactors.
- Do not delete, pause, disable, or alter automations.

## Outputs

Write:

- `local\shared\reports\daily-live-validation\pregame_integrity_YYYY-MM-DD.md`
- update `local\shared\handoffs\daily-live-validation\status.md`
- optionally update `local\shared\handoffs\pregame-integrity-check\status.md`

The report must include:

- gate result: `green`, `yellow`, or `red`
- service/API status
- git/development sprint status
- tools/endpoints checked
- direct CLOB readiness
- data/event/market matching status
- blockers for pregame research
- blockers for later live testing
- exact handoff to the Pregame Research & Planning agent

## Closeout Rule

If the gate is `yellow` or `red`, the report must say exactly what the Pregame Research agent should still do and what it must not do.
