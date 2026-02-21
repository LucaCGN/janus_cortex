# Polymarket Pipeline Evidence (`v0.4.1`)

## Date
- 2026-02-20

## Objective
- Validate ingestion for:
  - recently finished NBA game retrieval
  - currently live NBA game stream persistence (append-only ticks)

## Command Runs
- `python -m app.data.pipelines.daily.polymarket.sync_events --probe-set today_nba --max-finished 1 --max-live 1 --stream-sample-count 3 --stream-sample-interval-sec 1.0 --stream-max-outcomes 30`

Observed output:
- `sync_run_id=df29a397-8e9a-4e18-bb52-b0929cb77467`
- `status=success`
- finished probe:
  - `step=v0_4_1_today_finished_nba-hou-cha-2026-02-19`
  - `history_fetched=116`
  - `stream_fetched=0`
- live probe:
  - `step=v0_4_1_today_live_nba-phx-sas-2026-02-19`
  - `history_fetched=552`
  - `stream_fetched=264`
  - `stream_inserted=264`

## DB Verification Query (post-run)
Query path:
- `market_data.outcome_price_ticks` joined with `catalog.outcomes -> catalog.markets -> catalog.events`

Observed rows:
- `nba-hou-cha-2026-02-19`
  - `clob_prices_history`: `121` rows
  - ts window: `2026-02-19T18:00:26+00:00` -> `2026-02-20T03:21:22+00:00`
- `nba-phx-sas-2026-02-19`
  - `clob_prices_history`: `296` rows
  - ts window: `2026-02-19T03:20:19+00:00` -> `2026-02-20T03:22:19+00:00`
  - `fallback_stream`: `1848` rows
  - ts window: `2026-02-20T03:16:10.172422+00:00` -> `2026-02-20T03:23:37.876949+00:00`

## Live Test Evidence
- `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_v0_4_live_pytest.py`
  - result: `2 passed`
- `$env:JANUS_RUN_DB_TESTS='1'; $env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/databases/test_polymarket_event_seed_pack_live_pytest.py tests/app/data/databases/test_polymarket_event_seed_pack_v0_4_live_pytest.py`
  - result: `4 passed`

## Notes
- Primary stream polling (`collect_nba_fallback_stream_df`) can under-return on some windows.
- `v0.4.1` includes a slug-snapshot streaming fallback to keep live sampling append-only and non-empty when primary stream rows are zero.
