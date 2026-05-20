# Janus Live Activation Preflight Contract

Status: active source-of-truth contract
Created: 2026-05-20
Owning issues: `#55`, `#59`, `#60`
Tooling: `python codex_tool/live_activation_preflight.py`

## Purpose

Prevent another live game from becoming monitor-only because approval, plan state, worker state, LLM dispatch, or portfolio-manager execution toggles drift apart.

This contract defines the single preflight surface that must be green before tomorrow's NBA/WNBA live-testing work or global portfolio live order-management work can be treated as ready.

The preflight does not place orders, start workers, redeem, sign, broadcast, or submit anything. It reads `.env`/CLI toggles, optionally probes Janus status endpoints, and returns:

- `ready=true/false`
- exact blockers
- redacted config
- runtime worker/status evidence when probed
- exact next commands for rehearsal/live worker start, worker tick, and portfolio-manager order calls

## Scopes

### `sports-live`

Owns Janus-covered NBA/WNBA live testing through StrategyPlan/live-worker gates.

Live readiness requires:

- `JANUS_LIVE_TEST_ENABLED=true`
- `JANUS_LIVE_ACTIVATION_MODE=live`
- `JANUS_LIVE_TEST_SESSION_DATE=<YYYY-MM-DD>`
- `JANUS_LIVE_TEST_EVENT_ID=<event uuid>` or `JANUS_LIVE_TEST_EVENT_IDS=<event uuid,...>`
- `JANUS_LIVE_TEST_ACCOUNT_ID=<account uuid>`
- `JANUS_LIVE_TEST_EXECUTE=true`
- `JANUS_LIVE_TEST_LIVE_MONEY=true`
- `JANUS_LIVE_TEST_MAX_INTENTS>0`
- `JANUS_LIVE_TEST_MIN_SIZE>=5`
- `JANUS_LIVE_TEST_MIN_BUY_NOTIONAL_USD>=1`
- either `JANUS_LIVE_TEST_ENABLE_LLM_DISPATCH=true` or `JANUS_LIVE_TEST_CODEX_REVIEWED_FALLBACK_ENABLED=true`
- probed worker runtime is running/enabled and matches `execute`, `live_money`, account, and target event scope

`rehearsal` mode must keep `execute=false` and `live_money=false` while still proving event/account/max-intent/config shape. A rehearsal pass is useful for testing the switchboard but does not clear live trading.

### `portfolio-manager`

Owns one-shot global portfolio open/close/target/rebuy calls through the approved Janus portfolio order-management path.

Live readiness requires:

- `JANUS_PORTFOLIO_MANAGER_ACTIVATION_MODE=live`
- `JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED=true`
- `JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID=<account uuid>`
- `JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED=true`
- `JANUS_PORTFOLIO_MANAGER_REVIEWED_BY=<persona>`
- `JANUS_PORTFOLIO_MANAGER_REASON=<reason>`
- `JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR=true`
- `JANUS_PORTFOLIO_MANAGER_MAX_INITIAL_NOTIONAL_USD<=5`
- `JANUS_PORTFOLIO_MANAGER_TARGET_NOTIONAL_USD<=1`
- `JANUS_PORTFOLIO_MANAGER_DIRECT_TRUTH_MAX_AGE_SECONDS<=60`

The portfolio manager still needs a selected action plan, requested order JSON, fresh direct truth, server-side Janus gate acceptance, and immediate post-call direct-CLOB reconciliation. The preflight only proves the runtime switchboard is not the blocker.

## Required Commands

Pregame or pre-window config check:

```powershell
python codex_tool/live_activation_preflight.py --scope both --env-file .env --probe-api --require-ready
```

If the tool returns `blocked`, the master controller must route to the exact blocker instead of no-oping, posting duplicate comments, or switching to unrelated portfolio/crypto work.

Sports live worker rehearsal:

```powershell
python codex_tool/live_activation_preflight.py --scope sports-live --env-file .env --mode rehearsal --event-id <EVENT_ID> --account-id <ACCOUNT_ID> --max-intents 2 --enable-llm-dispatch --probe-api
```

Sports live readiness:

```powershell
python codex_tool/live_activation_preflight.py --scope sports-live --env-file .env --mode live --event-id <EVENT_ID> --account-id <ACCOUNT_ID> --execute --live-money --enable-llm-dispatch --max-intents 2 --probe-api --require-ready
```

Portfolio-manager live readiness:

```powershell
python codex_tool/live_activation_preflight.py --scope portfolio-manager --env-file .env --mode live --account-id <ACCOUNT_ID> --require-ready
```

## Controller Rule

On NBA/WNBA test days, the master controller must run or inspect a fresh preflight result before treating the live path as ready. The pass is not allowed to report only `worker stopped`, `plan expired`, `max_intents=0`, or `dispatch disabled` for more than one material live checkpoint without converting that blocker into one of:

- update `.env`/runtime toggle evidence
- start or stop a rehearsal worker
- create/adopt a fresh executable StrategyPlan
- enable reviewed LLM/Codex fallback path
- explicitly page operator/reviewer because live mode is intentionally blocked

## Success Definition

Tomorrow's live path is ready only when:

- `sports-live.ready=true` for the target NBA game before the live decision window
- `portfolio-manager.ready=true` before expecting global portfolio one-shot orders
- `#60` WNBA shadow remains `orders_allowed=false` unless a separate WNBA live preflight and StrategyPlan gate exists
- the handoff records the preflight artifact/result and exact active worker/order path state

No preflight result can override direct CLOB/account truth, Janus server-side execution gates, or kill switches.
