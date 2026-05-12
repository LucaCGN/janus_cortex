# Branch Plan: `codex/season-wnba-bootstrap`

## Role
Secondary offseason branch for keeping basketball research active through WNBA preparation.

## Target Milestone
- `v1.4.x`

## Depends On
- `codex/data-dev-db-safety`

## Owns
- WNBA coverage audit
- schema and ingestion bootstrap planning
- offseason continuity plan that keeps the research program active

## Does Not Own
- current regular-season NBA benchmark critical path
- frontend work
- immediate algorithm benchmarking delivery

## Subphases

### `W1` Source Coverage Audit
Objective:
- measure what WNBA data is already fetchable historically and what is available going forward
Status:
- complete for first-pass WNBA CDN, official live boxscore/play-by-play, balldontlie tier blocker detection, and finished Polymarket moneyline price-history discovery

### `W2` Schema And Canonical Planning
Objective:
- define how WNBA fits into the provider, canonical, and module structure
Status:
- complete for a separate `wnba` schema with WNBA teams, players, games, live snapshots, boxscores, play-by-play, analysis readiness, deterministic lane, ML, integration, and Polymarket price-history tables

### `W3` Ingestion Baseline
Objective:
- create the first safe ingestion and persistence plan without destabilizing NBA flows
Status:
- implemented as WNBA-only CDN adapters and Postgres sync scaffold; migrations still need to be applied in a safe DB target before shared runtime use

### `W4` Analysis Reuse Audit
Objective:
- decide which NBA analysis products can be reused for WNBA and which need separate logic
Status:
- complete for shadow-only scaffolds of `underdog_range_scalp`, `favorite_floor_rebound`, `micro_grid_reprice`, `lead_fragility`, `panic_fade_fast`, `quarter_open_reprice`, `halftime_gap_fill`, `q4_clutch`, and `winner_definition`
- WNBA uses 40-minute regulation timing and must not inherit NBA garbage-time or clock thresholds without recalibration

### `W5` Offseason Research Program
Objective:
- define how WNBA work keeps the broader basketball analysis effort productive during the NBA offseason
Status:
- ready to enter the standard Development Agent loop as a P1 shadow/data/replay workstream after active NBA live-safety P0 work

## Standard Development Loop Gate
- Readiness command: `python tools\run_wnba_development_loop_check.py --season 2026 --with-price-history-probe --price-event-limit 10 --migrations-not-applied`
- Current status: `ready_for_standard_loop_price_history_shadow`
- Minimum structural work before standard-loop inclusion: none
- Standard-loop scope allowed: WNBA schema, WNBA CDN ingestion, WNBA Polymarket matching, finished-event price-history backtests, passive WNBA CLOB watch capture, WNBA state panels, WNBA deterministic shadow lanes, WNBA ML dataset/model experiments, WNBA reports/handoffs
- Standard-loop scope forbidden: placing orders, changing NBA live trading logic, changing agentic execution logic, changing active NBA StrategyPlanJSON behavior, altering automations, or promoting WNBA lanes to live without a calibration gate
- First standard-loop task: apply WNBA migrations `0021` through `0024` against a safe DB target, then backfill matched finished WNBA Polymarket moneyline `prices-history`
- Second standard-loop task: run batch WNBA price-history shadow backtests with `python tools\run_wnba_polymarket_history_probe.py --season 2026 --event-limit 10 --max-targets 3` or a larger target count once provider health is stable
- Live/calibrated blockers: no safe DB migration application recorded, insufficient linked WNBA games and labeled ML rows, 2025 historical backfill requires balldontlie WNBA API key/tier, and full microstructure replay requires passive WNBA bid/ask/depth/trade capture

## Merge Gate
- WNBA path is documented and technically scoped
- offseason continuity no longer depends on ad hoc memory or branch-local notes
- standard Development Agent routing exists and keeps WNBA shadow-only until calibration evidence clears
