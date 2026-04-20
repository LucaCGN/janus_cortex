# NBA Analysis Modeling And Backtesting

## Why / Revision Note
- This locks the first modeling and backtesting policy to interpretable, offline-first baselines.

## Objective
- Define the first descriptive metrics, backtest families, predictive targets, and evaluation rules that sit on top of the analysis mart.

## Primary Files / Areas
- `app/data/pipelines/daily/nba/analysis_module.py`
- `app/docs/nba_analysis_module_plan.md`
- `app/docs/nba_analysis_data_products.md`

## Descriptive Metrics
- Opening expectation:
  - `opening_price`
  - opening-band assignment
- Intragame path shape:
  - `ingame_price_range`
  - `total_swing`
  - `max_favorable_excursion`
  - `max_adverse_excursion`
- Inversion behavior:
  - `inversion_count`
  - `first_inversion_at`
  - `seconds_above_50c`
  - `seconds_below_50c`
- Winner definition:
  - earliest stable timestamps and clock-elapsed markers at `70c`, `80c`, `90c`, `95c`
- Team season summaries:
  - rolling 10-game and 20-game JSON views

## Backtest Families

### Reversion
- Trigger family:
  - strong favorite drawdown buys
- Initial baseline rule:
  - open at `>= 70c`
  - enter after `10c` drawdown
  - exit on reclaim to `open - 2c` or end of game

### Inversion
- Trigger family:
  - first cross above `50c`
- Initial baseline rule:
  - open below `50c`
  - enter at first upward cross
  - exit on break back below `50c` or end of game

### Winner Definition
- Trigger family:
  - threshold continuation
- Initial baseline rule:
  - enter at `>= 80c`
  - exit on break below `75c` or end of game

## Next Strategy Families To Add
- quarter-context comeback reversion
- opening-band volatility scalp
- scoreboard-control mismatch fade or continuation
- run-exhaustion or run-stabilization rules if the state panel supports them cleanly

The goal is not one "winner" strategy immediately.
The goal is a comparable multi-family backtest program.

## Backtest Output Contract
- Per trade:
  - entry / exit timestamps
  - entry / exit state indexes
  - entry / exit prices
  - gross return
  - gross return with configured slippage
  - hold time
  - MFE / MAE after entry
  - team / opponent / opening band / period / score bucket / context bucket
- Initial friction assumptions:
  - frictionless baseline
  - optional fixed `1c` per side slippage stress case
  - no fees in v1

## Predictive Tracks

### Volatility / Inversion
- Target:
  - `crossed_50c_next_12_states_flag`
- First baseline:
  - logistic regression
- Initial feature block:
  - opening price
  - current team price
  - distance from opening
  - absolute score diff
  - seconds to game end
  - period
  - favorite / leading / mismatch indicators
  - net points over last five timed events

### Trade Window Quality
- Targets:
  - `mfe_from_state`
  - `mae_from_state`
- First baseline:
  - linear regression
- Purpose:
  - estimate quality of post-trigger windows before adding more expressive models

### Winner Definition Timing
- Target:
  - time to stable `80c` winner definition
- First baseline:
  - grouped hazard-style timing proxy by `period_label x score_diff_bucket x opening_band`

## Evaluation Protocol
- Use time-based train / validation split only.
- Default cutoff:
  - `75%` of observed game dates unless a cutoff is explicitly provided
- In addition to time-based validation, keep a random holdout sample in the `5%-10%` range for strategy comparison.
- Metrics:
  - classification:
    - Brier score
    - log loss
    - AUC when available
  - regression / timing:
    - RMSE
    - MAE
    - rank correlation
- Strategy families must also report:
  - trade count
  - slippage-adjusted return
  - hold-time distribution
  - MFE / MAE summary
- Every model is compared against a naive baseline:
  - classification: train-set base rate
  - regression / timing: train-set global mean
- Every strategy family is also compared against:
  - no-trade baseline
  - naive winner-prediction style reference where relevant
  - repeated holdout behavior, not one single sample
- Sequential portfolio promotion also requires:
  - repeated-seed bankroll robustness on the current holdout policy
  - explicit collision analysis if multiple surviving families are replayed in one shared sleeve

## Promotion Rules
- Do not promote any model to a serving layer if:
  - validation data is insufficient
  - it loses to the naive baseline
  - feature leakage is detected
- Do not promote a strategy family if:
  - it only looks good on a narrow narrative slice
  - it fails after slippage stress
  - it cannot be explained with trade-trace artifacts
  - it cannot survive comparison on the random holdout workflow
- LLM usage remains downstream only:
  - it may consume structured mart, backtest, and baseline outputs later
  - it is not part of the baseline training loop
  - the current plausible use case is interpretation of borderline or collision-heavy cases after the statistical gate, not replacement of the statistical gate

## Artifact Sync Requirements
- When baseline families or metrics change, update:
  - `app/docs/nba_analysis_module_plan.md`
  - `app/docs/nba_analysis_data_products.md`
  - any checkpoint that promotes the analysis lane
