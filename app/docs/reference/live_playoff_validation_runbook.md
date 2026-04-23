# Live Playoff Validation Runbook

## Scope
- date: `2026-04-23`
- execution profile: `v1`
- primary controller: `controller_vnext_unified_v1 :: balanced`
- fallback controller: `controller_vnext_deterministic_v1 :: tight`
- target slate:
  - `0042500123` `NYK@ATL`
  - `0042500133` `CLE@TOR`
  - `0042500163` `DEN@MIN`

## Live Policy
- fixed Polymarket minimum size only:
  - minimum `$1` notional, or
  - `5` shares when that is the effective platform minimum
- entries:
  - limit-only at current best ask
  - skip if spread is greater than `2c`
  - skip if orderbook is stale or unavailable
  - cancel unfilled entry after `15s`, then re-evaluate on the next cycle
- exits:
  - normal exit tries limit at current best bid first
  - after `10s` without fill, retry once with an aggressive market-emulated sell
  - stop-loss exit is a local trigger with immediate market-emulated sell

## Important Caveats
- true exchange-native stop orders are not assumed
- current Polymarket order placement plumbing still behaves as price-based order placement, so "market" exits are implemented as aggressive limit sells against the current bid-side context
- the upstream NBA live scoreboard and play-by-play provider can return transient JSON decode errors before tipoff or during weak upstream responses; the live executor should keep polling and log those events rather than crash

## Startup
1. Start the API without reload:

```powershell
Set-Location "C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex"
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8010
```

2. In a second terminal, start or resume the dry-run live loop:

```powershell
Set-Location "C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex"
python tools/start_live_run.py --api-root http://127.0.0.1:8010 --run-id live-2026-04-23-v1 --game-id 0042500123 --game-id 0042500133 --game-id 0042500163 --dry-run
```

3. Open the operator page:

```powershell
Start-Process "http://127.0.0.1:8010/live-control?runId=live-2026-04-23-v1"
```

## Switching To Entries Enabled
- remove `--dry-run` from the launcher command once the pre-tip smoke checks are acceptable
- use the same `run_id` so the local ledger and DB-linked state stay aligned

## Pre-Tip Smoke Checklist
- all 3 games appear in `/live-control`
- current run status is `running`
- heartbeat timestamp is updating
- each game card shows the latest controller path, strategy family, and confidence or a clear skip reason
- the orderbook path resolves without crashing the run
- dry-run order create/cancel path can be exercised
- pause / resume / stop controls return success

## Restart / Resume
1. Stop the API process or pause the run from `/live-control`.
2. Restart the API.
3. Relaunch the exact same `run_id`:

```powershell
python tools/start_live_run.py --api-root http://127.0.0.1:8010 --run-id live-2026-04-23-v1 --game-id 0042500123 --game-id 0042500133 --game-id 0042500163 --dry-run
```

4. Confirm:
- no duplicate pending entry is created for a game/outcome/side already present in DB or the local recovery snapshot
- the run heartbeat resumes
- open orders and positions reappear in the live-control surface

## Local Ledger Paths
- root:
  - `C:\code-personal\janus-local\janus_cortex\tracks\live-controller\2026-04-23\live-2026-04-23-v1\`
- required files:
  - `run_config.json`
  - `heartbeat.json`
  - `decisions.jsonl`
  - `executor_events.jsonl`
  - `recovery_snapshot.json`

## Operator Actions During Games
- `Pause entries`
  - keep polling, stop creating new entries
- `Resume entries`
  - allow fresh entries again
- `Stop run`
  - stop the worker loop cleanly after the current cycle

## What To Watch Tonight
For every attempted trade:
- signal price
- best bid / ask at decision time
- submitted price
- fill price
- fill delay
- spread at submission
- slippage vs signal
- slippage vs best quote
- order type used
- cancel / replace count
- stop-trigger flag

For every game:
- selected controller path
- selected family
- confidence
- entry attempted or skipped
- stop triggered or not
- realized PnL once closed

## Closeout
- let the run finish naturally when all tracked games are final, or stop it manually from the control page
- sync portfolio state if any real orders were placed
- archive the local ledger folder and capture a short operator note about:
  - connection quality
  - fill behavior
  - slippage observations
  - any duplicated or stale-state issues
