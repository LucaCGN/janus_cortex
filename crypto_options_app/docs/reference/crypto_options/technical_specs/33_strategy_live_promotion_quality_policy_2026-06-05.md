# Strategy Live Promotion Quality Policy

Status: implemented checkpoint for the strategy promotion manager.

This policy keeps strategy generation and shadow/live-replay experimentation open, but makes supervised live promotion strict. A strategy can be explored with simple validation mechanics, but it cannot become a live candidate unless recent shadow evidence shows it deserves real execution.

## Core Rule

Live promotion requires a recent one-hour shadow proof:

- At least `12` recent economic samples, equivalent to roughly one hour of 5-minute event windows.
- Recent shadow/live-replay win rate at least `70%`.
- Recent shadow/live-replay simulated PnL greater than `0`.
- Lifecycle audit must pass.
- Reconciliation must pass.
- Required promoted signals must be available.
- Signals with strict-review blockers cannot be treated as live-promotion inputs.

The one-hour proof is deliberately separate from longer historical backtests. Historical and random-sample backtests are still required to reduce overfitting, but live promotion also needs current market-regime evidence.

## State Semantics

`NEEDS_BACKTEST`
: Historical replay and/or live replay is missing.

`BACKTEST_READY`
: Historical replay exists, but live-shadow evidence is missing.

`SHADOW_READY`
: The strategy can continue shadow work, but still lacks enough promotion proof.

`SHADOW_REVIEW`
: Shadow economics are weak. This includes non-positive shadow PnL or win rate below the 70% floor after enough recent samples. The strategy should be revised before any live attempt.

`LIVE_CANDIDATE`
: The strategy passed historical replay, recent one-hour shadow proof, lifecycle, reconciliation, signal gates, and budget policy. This is still not live authorization; it only means the supervised runtime may consider it.

`LIVE_RUNNING`
: The strategy has supervised live proof and no current blockers. Live orders still require scoped child-process live gates and the executor boundary.

`DEMOTED_TO_SHADOW`
: Any negative supervised-live realized PnL demotes the strategy back to shadow. If shadow was positive while live loses, compare fill assumptions, slippage, latency, and quote freshness before retrying live.

`REVIEW_BLOCKED`
: Mechanical integrity failed: lifecycle, reconciliation, hard stop, duplicate cadence, or another structural blocker.

## Shadow Vs Live Drift

If shadow/live-replay looks good but supervised live performs materially worse, the system treats it as an integrity warning, not merely bad luck.

Current hard checks:

- Live loss after positive shadow adds `live_loss_after_positive_shadow_requires_review`.
- Live-vs-shadow PnL gap above `$2` adds `live_shadow_actual_drift_exceeds_limit`.
- Live-vs-recent-shadow win-rate gap above `20pp` adds `live_shadow_win_rate_drift_exceeds_limit` when supervised-live win-rate evidence is available.

Repeated drift across multiple strategies means one of the following is likely wrong:

- Fill simulation is too optimistic.
- Live execution is missing slippage, queue position, spread, or stale-order behavior.
- Signal timing is using data not actually available at decision time.
- Settlement/PnL attribution is mismatched.

## Shadow Economics Persistence

Strategy replay rows must persist economics separately from lifecycle coverage. A row that proves `candidate -> intent -> order -> fill -> position -> exit_plan` is not enough for promotion unless it also carries an economic sample.

Current implementation:

- Filled shadow rows persist `result.economics` and `result.shadow_economics`.
- Economics are conservatively marked to the observed bid/mark from the signal context.
- If the mark is below the entry price, the sample records negative simulated PnL. This exposes spread/slippage drag instead of treating lifecycle success as strategy quality.
- No-fill or blocked rows carry `sample_count=0` and are ignored by the promotion economics extractor.

Checkpoint from the unified loop on 2026-06-05:

- Fresh read-only shadow replay was run for `hedger_ratio_replication_v4`, `grid_band_rebound_v3`, `profile_hedge_scalping_v4`, `profile_hedge_scalping_v1`, and `event_context_profile_confirmed_v1`.
- All five wrote complete lifecycle rows without live authorization or manual orders.
- The promotion manager now sees economic samples for those rows.
- Current blocker moved from `missing_shadow_live_economic_evidence` to real strategy economics: one recent sample each, negative mark-to-bid PnL of about `$0.01`, and far below the 12-sample one-hour floor.
- This means the next loop should improve strategy price selection, fillability, and spread/slippage assumptions before any supervised live promotion.

## Development Priority

The main development loop remains signal-first:

1. Expand and strict-review the indicator/signal universe across Events, Profiles, and Crypto sources.
2. Promote only diverse, non-tautological, replay-safe signals.
3. Use simple strategy shells to validate mechanics end-to-end.
4. Keep strategies in shadow until they pass the recent one-hour quality gate.
5. Promote to live only through supervised runtime gates.
6. Demote immediately on live losses, then analyze shadow/live drift before retrying.

This policy does not block strategy generation. It blocks premature live promotion.

## Implemented Surfaces

- `crypto_options_app/strategies/promotion.py`
  - Adds one-hour recent shadow sample, win-rate, and PnL gates.
  - Adds shadow review and live demotion decisions.
  - Adds live-vs-shadow PnL and win-rate drift blockers.

- `crypto_options_app/frontend/index.html`
- `crypto_options_app/frontend/assets/app.js`
  - Strategy Lab now displays recent shadow win rate, sample count, and PnL.

- `tests/crypto_options_app/test_strategy_promotion_manager_pytest.py`
  - Covers under-sampled shadow proof.
  - Covers weak recent shadow routed to review.
  - Covers strict recent shadow proof becoming live-candidate eligible when signal gate is bypassed for the focused policy test.

## Safety

The promotion endpoint never authorizes orders. It only labels strategy state. Live orders remain possible only through the supervised child runtime with scoped live flags, executor boundary, ledgers, risk gates, reconciliation, and manual-order prohibition.
