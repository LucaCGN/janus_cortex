# Current Analysis System State

## Snapshot Date
- `2026-04-22`

## Current Playoff-Tuned Controller
- primary live candidate: `controller_vnext_unified_v1 :: balanced`
- deterministic fallback: `controller_vnext_deterministic_v1 :: tight`
- current reference: [controller_vnext_final_tuning.md](C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex\app\docs\reference\controller_vnext_final_tuning.md)

Interpretation:
- the older finalist controllers still have higher raw postseason upside
- the new vNext tuned controllers are materially smoother and are now the preferred candidates for actual playoff deployment

## Current Release Baseline
- analysis module baseline: `v1_0_1`
- benchmark contract: `v11`
- status: regular-season corpus validated, postseason coverage wired end to end, and final 4-option adverse-execution showdown completed

Completed implementation wave:
- `A0` contracts and package split
- `A1` research universe and QA gate
- `A2` game-team and season mart profiles
- `A3` state panel and winner-definition profiles
- `A4` descriptive report pack
- `A5` reusable backtest engine
- `A6` interpretable predictive baselines
- `A7` player-impact shadow lane

Completed release wave:
- `v1.0.2` safe DB validation workflow
- `v1.0.3` non-live validation workflow
- `v1.0.4` strategy-lab expansion
- `v1.1.0` benchmarked multi-algorithm backtest workflow
- `v1.2.0` stable read-only consumer adapters
- `v1.3.0` analysis studio alpha
- `v1.4.0` sequential portfolio benchmark
- `v1.4.1` repeated-seed robustness and combined keep-family sleeve
- `v1.4.2` refined underdog continuation and first routing lane
- `v1.4.3` realistic execution replay and quarter-specific sleeves
- `v1.4.4` master-router baseline and expanded family research
- `v1.4.5` LLM router benchmarking and finalist dashboard
- `v1.4.6` postseason event coverage, exact game-event linking, adverse slippage contract, and final 4-option showdown
- `v1.4.7` controller-vNext playoff tuning, uncertainty-band LLM review, stop overlays, and family-aware sizing

## Current CLI Surface
- `build_analysis_mart`
- `build_analysis_report`
- `run_analysis_backtests`
- `train_analysis_baselines`

## Corpus Snapshot
### Regular Season
- season: `2025-26`
- phase: `regular_season`
- finished games: `1224`
- research-ready games: `1198`
- descriptive-only games: `26`

### Postseason Validation Slice
- phases: `play_in`, `playoffs`
- finished games validated: `20`
- split:
  - `play_in=6`
  - `playoffs=14`
- research-ready games: `20 / 20`
- state-panel rows:
  - `play_in=7,128`
  - `playoffs=15,990`
  - combined=`23,118`

## Frozen Underlying Strategy Stack
These are the kept underlying methods that still compose the deterministic controller:

### Core Families
- `winner_definition`
  - rule: reach `80c`, break back through `75c` or `76c`, otherwise hold to end
- `inversion`
  - rule: dynamic underdog continuation through the `45c/50c` reclaim line with exit below `49c`
- `underdog_liftoff`
  - rule: sub-`42c` openers, rebound through `36c`, exit at `50c` or `-3c`

### Independent Sleeves
- `q1_repricing`
  - first-quarter repricing continuation
- `q4_clutch`
  - late close-game continuation after repeated lead changes

## Final Compared Options
The repo is now frozen around these four externally compared options:
- `winner_definition`
- `master_strategy_router_v1`
- `gpt-5.4 :: llm_hybrid_freedom_compact_v1`
- `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1`

Interpretation:
- `winner_definition` is the definitive single-family reference
- `master_strategy_router_v1` is the definitive deterministic controller reference
- the two LLM freedom lanes are the final bounded controller variants that remain worth comparing against the deterministic router

## Current Execution Contract
- initial bankroll: `$10.00`
- position size fraction: `20%`
- max concurrent positions: `5`
- concurrency mode: `shared_cash_equal_split`
- min order: `$1.00`
- min shares: `5`
- deterministic slippage: `0c`
- random adverse slippage: `0-20c`
- random slippage seed: `20260422`

This contract is intentionally hostile and is now the current truth for controller hardening work.

## Final Postseason Validation Result
On the fixed chronological `20`-game postseason slice:

| Option | Ending Bankroll | Compounded Return | Max Drawdown | Trades | Avg Trade Return | LLM Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `master_strategy_router_v1` | `$3.97` | `-60.25%` | `60.25%` | `11` | `-17.50%` | `$0.0000` |
| `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1` | `$3.71` | `-62.87%` | `67.78%` | `19` | `-9.77%` | `$0.0206` |
| `gpt-5.4 :: llm_hybrid_freedom_compact_v1` | `$3.62` | `-63.77%` | `63.77%` | `21` | `-8.18%` | `$0.0622` |
| `winner_definition` | `$2.68` | `-73.21%` | `73.21%` | `9` | `-20.15%` | `$0.0000` |

## Current Controller Read
Deterministic router mix on the validated postseason slice:
- `winner_definition`: `17`
- `underdog_liftoff`: `2`
- `inversion`: `1`
- triggered `q4_clutch`: `2`

LLM freedom mix:
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

Current interpretation:
- the postseason slice is substantially harder than the regular-season growth runs
- all four finalist options lose money under the `v11` adverse-execution contract
- `master_strategy_router_v1` preserves bankroll best
- the LLM freedom lanes do not justify promotion over the deterministic router under this contract
- LLM value should now be treated as a selective review or override path, not as the default controller

## Archived Or Demoted Methods
These are not part of the active stack anymore:
- `favorite_panic_fade_v1`
- `halftime_q3_repricing_v1`
- `comeback_reversion_v2`
- `model_residual_dislocation_v1`
- `reversion`
- `comeback_reversion`
- `volatility_scalp`
- `statistical_routing_v1`
- `combined_keep_families`

They remain useful as historical research context only.

## Validation Snapshot
Validated on `2026-04-22`:
- `python -m pytest -q tests/app/data/pipelines/daily/nba/test_analysis_backtests_pytest.py`
- `python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 20 --skip-live-snapshots --skip-play-by-play`
- `python -m app.data.pipelines.daily.cross_domain.sync_mappings --lookback-days 14 --lookahead-days 0`
- `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_mart --season 2025-26 --season-phase play_in --analysis-version v1_0_1 --rebuild`
- `python -m app.data.pipelines.daily.nba.analysis_module build_analysis_mart --season 2025-26 --season-phase playoffs --analysis-version v1_0_1 --rebuild`
- `python -m app.data.pipelines.daily.nba.analysis_module run_analysis_backtests --season 2025-26 --season-phase postseason_final_20 --season-phases play_in,playoffs --analysis-version v1_0_1 --portfolio-initial-bankroll 10 --portfolio-position-size-fraction 0.2 --portfolio-game-limit 20 --portfolio-min-order-dollars 1 --portfolio-min-shares 5 --portfolio-max-concurrent-positions 5 --portfolio-concurrency-mode shared_cash_equal_split --portfolio-random-slippage-max-cents 20 --portfolio-random-slippage-seed 20260422 --slippage-cents 0 --llm-compare-models gpt-5.4,gpt-5.4-mini`

## Current Frontend Surface
- the studio remains read-only
- it should now be tuned around the frozen final-option comparison and review queue, not around broad family-lab exploration

## Current Next Branches
- `codex/analysis-routing-allocation`
  - retargeted to controller hardening under the `v11` contract
- `codex/analysis-context-models`
  - payout policy and context models around the frozen controller
- `codex/frontend-analysis-portfolio-viz`
  - final review dashboard for the four compared options and their internal route mix

## Output Root Convention
- repo outputs remain read-only snapshots
- branch-independent artifacts and quicklook material still belong under `C:\code-personal\janus-local\janus_cortex`
