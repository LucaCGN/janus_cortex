# Postseason Final 20 Validation

## Purpose
Freeze the first adverse-execution validation of the kept NBA controller stack on a concrete postseason slice instead of on regular-season random samples.

This reference records the exact data slice, execution contract, and final comparison set that were validated on `2026-04-22`.

## Validated Slice
- season: `2025-26`
- phases: `play_in`, `playoffs`
- games: first `20` finished postseason games in chronological order
- composition:
  - `6` play-in games
  - `14` playoff games

Included game ids:
- `0052500111`
- `0052500121`
- `0052500101`
- `0052500131`
- `0052500201`
- `0052500211`
- `0042500121`
- `0042500131`
- `0042500161`
- `0042500171`
- `0042500101`
- `0042500111`
- `0042500141`
- `0042500151`
- `0042500122`
- `0042500132`
- `0042500162`
- `0042500112`
- `0042500152`
- `0042500172`

## Data Preparation Completed
The following are now true for this 20-game slice:
- NBA schedule rows exist in `nba.nba_games` with correct `season_phase`
- play-by-play is present for all `20` games
- Polymarket events were seeded by exact postseason slugs
- exact slug links were written into `nba.nba_game_event_links`
- feature snapshots are `covered_pre_and_ingame` for all `20` games
- analysis marts were rebuilt successfully
- state-panel rows now exist for both postseason phases

State-panel row counts:
- `play_in`: `7,128`
- `playoffs`: `15,990`
- combined postseason slice: `23,118`

## Execution Contract
- benchmark contract: `v11`
- initial bankroll: `$10.00`
- position size fraction: `20%`
- max concurrent positions: `5`
- concurrency mode: `shared_cash_equal_split`
- minimum order dollars: `$1.00`
- minimum shares: `5`
- deterministic slippage: `0c`
- random adverse slippage: uniform integer cents in `[0, 20]`
- random slippage seed: `20260422`
- evaluation sample: one fixed chronological replay of the `20` games above

Interpretation:
- buys are penalized upward
- sells are penalized downward
- the run is intentionally hostile to the strategy engine

## Final Compared Options
External comparison was narrowed to exactly four options:
- `winner_definition`
- `master_strategy_router_v1`
- `gpt-5.4 :: llm_hybrid_freedom_compact_v1`
- `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1`

The deterministic router still uses the frozen underlying family stack:
- core families:
  - `winner_definition`
  - `inversion`
  - `underdog_liftoff`
- independent sleeves:
  - `q1_repricing`
  - `q4_clutch`

## Final Results

| Option | Ending Bankroll | Compounded Return | Max Drawdown | Executed Trades | Avg Executed Trade Return | LLM Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `master_strategy_router_v1` | `$3.97` | `-60.25%` | `60.25%` | `11` | `-17.50%` | `$0.0000` |
| `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1` | `$3.71` | `-62.87%` | `67.78%` | `19` | `-9.77%` | `$0.0206` |
| `gpt-5.4 :: llm_hybrid_freedom_compact_v1` | `$3.62` | `-63.77%` | `63.77%` | `21` | `-8.18%` | `$0.0622` |
| `winner_definition` | `$2.68` | `-73.21%` | `73.21%` | `9` | `-20.15%` | `$0.0000` |

## Internal Route Mix
Deterministic router selection mix on the same slice:
- `winner_definition`: `17`
- `underdog_liftoff`: `2`
- `inversion`: `1`

Triggered extra-sleeve counts:
- `q4_clutch`: `2`

LLM route mix:
- `gpt-5.4`
  - `winner_definition`: `18`
  - `underdog_liftoff`: `3`
  - `q4_clutch`: `2`
  - `inversion`: `1`
- `gpt-5.4-mini`
  - `winner_definition`: `18`
  - `underdog_liftoff`: `3`
  - `inversion`: `2`
  - `q4_clutch`: `2`

## Main Readout
- The postseason slice is materially harder than the earlier regular-season growth experiments.
- Under a hostile execution contract, all four options lose money over these `20` games.
- `master_strategy_router_v1` is the best bankroll-preservation option in this slice.
- Both LLM freedom lanes trade more often and achieve less-negative average trade returns, but neither beats the deterministic router on ending bankroll.
- The current evidence does not justify promoting the LLM freedom lane over the deterministic router for adverse postseason execution.

## What This Changes
The active question is no longer "which new family should we invent next?".

The active question is:
- how do we harden the frozen controller stack so it survives high-slippage, short-sample, high-overlap playoff conditions?

That means the next work wave should focus on:
- controller hardening
- payout and bankroll policy
- selective LLM override logic instead of free LLM routing
- final review dashboards over the frozen 4-option comparison

## Key Artifacts
- showdown summary:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis\2025-26\postseason_final_20\v1_0_1\backtests\benchmark_final_option_showdown_summary.csv`
- showdown daily paths:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis\2025-26\postseason_final_20\v1_0_1\backtests\benchmark_final_option_showdown_daily_paths.csv`
- showdown decisions:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis\2025-26\postseason_final_20\v1_0_1\backtests\benchmark_final_option_showdown_decisions.csv`
- full benchmark payload:
  - `C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis\2025-26\postseason_final_20\v1_0_1\backtests\run_analysis_backtests.json`

## Reproduction Commands
Data preparation:
```powershell
python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 20 --skip-live-snapshots --skip-play-by-play
python -m app.data.pipelines.daily.cross_domain.sync_mappings --lookback-days 14 --lookahead-days 0
python -m app.data.pipelines.daily.nba.analysis_module build_analysis_mart --season 2025-26 --season-phase play_in --analysis-version v1_0_1 --rebuild
python -m app.data.pipelines.daily.nba.analysis_module build_analysis_mart --season 2025-26 --season-phase playoffs --analysis-version v1_0_1 --rebuild
```

Final showdown:
```powershell
python -m app.data.pipelines.daily.nba.analysis_module run_analysis_backtests --season 2025-26 --season-phase postseason_final_20 --season-phases play_in,playoffs --analysis-version v1_0_1 --portfolio-initial-bankroll 10 --portfolio-position-size-fraction 0.2 --portfolio-game-limit 20 --portfolio-min-order-dollars 1 --portfolio-min-shares 5 --portfolio-max-concurrent-positions 5 --portfolio-concurrency-mode shared_cash_equal_split --portfolio-random-slippage-max-cents 20 --portfolio-random-slippage-seed 20260422 --slippage-cents 0 --llm-compare-models gpt-5.4,gpt-5.4-mini
```
