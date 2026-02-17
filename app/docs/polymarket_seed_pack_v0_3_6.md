# Polymarket Seed Pack Validation (`v0.3.6`)

## Purpose
Record live integration evidence for the extra validation steps requested in `v0.3.6`:
- `3.7` past NBA game period odds history
- `3.8` upcoming NBA game availability
- `3.9` long-horizon non-sports event history (grid-style profile)

## Validation Date
- 2026-02-17

## Probe Inputs
- `https://polymarket.com/sports/nba/nba-mem-den-2026-02-11`
- `https://polymarket.com/sports/nba/nba-ind-was-2026-02-19`
- `https://polymarket.com/event/will-the-us-confirm-that-aliens-exist-before-2027`

## Execution
- Script:
  - `python -m app.data.databases.seed_packs.polymarket_event_seed_pack`
- Latest sync run id:
  - `847a11a7-be94-4881-b932-333922819bb1`
- Summary:
  - status: `success`
  - rows_read: `10132`
  - rows_written: `10287`

## Step Results
### `extra_3_7_past_nba_game_period`
- slug: `nba-mem-den-2026-02-11`
- event title: `Grizzlies vs. Nuggets`
- markets seeded: `43`
- outcomes seeded: `86`
- history sampled: `moneyline` market window around game period
- history fetched/inserted: `120 / 120`
- distinct price cents: `24`
- DB history ts window: `2026-02-11T20:00:17+00:00` to `2026-02-12T05:50:17+00:00`

### `extra_3_8_upcoming_nba_availability`
- slug: `nba-ind-was-2026-02-19`
- event title: `Pacers vs. Wizards`
- markets seeded: `32`
- outcomes seeded: `64`
- history sampled: rolling recent window for moneyline outcomes
- history fetched/inserted: `1024 / 1024`
- distinct price cents: `26`

### `extra_3_9_aliens_grid_history`
- slug: `will-the-us-confirm-that-aliens-exist-before-2027`
- event title: `Will the US confirm that aliens exist before 2027?`
- markets seeded: `1`
- outcomes seeded: `2`
- history sampled: interval-based (`1m`) long-horizon retrieval
- history fetched/inserted: `8912 / 8912`
- distinct price cents: `12`
- observed price range: `0.085` to `0.915`

## Persisted DB Snapshot (after final seed run)
- event graph counts:
  - `nba-mem-den-2026-02-11`: `43` markets, `86` outcomes
  - `nba-ind-was-2026-02-19`: `32` markets, `64` outcomes
  - `will-the-us-confirm-that-aliens-exist-before-2027`: `1` market, `2` outcomes
- history source counts:
  - `nba-mem-den-2026-02-11`: `120` rows (`clob_prices_history`)
  - `nba-ind-was-2026-02-19`: `1024` rows (`clob_prices_history`)
  - `will-the-us-confirm-that-aliens-exist-before-2027`: `8912` rows (`clob_prices_history`)

## Notes
- Migration test fixtures intentionally reset schemas; if those tests are run after this seed pack, re-run the seed pack to restore probe data.
- History rows are append-only and deduplicated by `(outcome_id, ts, source)` key semantics.
