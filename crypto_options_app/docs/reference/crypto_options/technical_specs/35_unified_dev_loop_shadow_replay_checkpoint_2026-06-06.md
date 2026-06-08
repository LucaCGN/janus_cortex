# Unified Dev Loop Shadow Replay Checkpoint - 2026-06-06

## Scope

This checkpoint records the first broad read-only replay pass after wiring A/B/C context into strategy shadow economics.

No live orders were placed. Global/API live flags remained false. Manual orders were avoided.

## Current Source State

- A Crypto: technical observer service healthy, latest watermark observed during this pass.
- B Profiles: profile distribution service healthy and refreshing near the 30 second target. Runtime context now includes aggregate distribution plus grade/style/grade-style breakdowns.
- C Options: option price capture service healthy with forward price marks available for replay.
- Health status remains `degraded` because of historical readiness/stale-fallback criteria and the SQLite read fallback, not because A/B/C services were down.

## Signal Review State

- Signal selection no longer treats `NEEDS_V2_REVIEW` as a pending human review state.
- These rows are now surfaced as `revision_candidate`, so the UI can show strict-review work without blocking the whole lab as "review".
- Latest verified state before this checkpoint: 169 signals, 45 selected, 100 revision candidates, 0 pending review.

## Strategy Replay Changes

- Strategy replay scenarios now carry A/B/C context:
  - profile aggregate Up/Down ratio
  - source freshness and profile row counts
  - reconstructed profile Up/Down prices
  - profile component breakdown by grade, style, and grade/style
  - crypto technical observer summaries
  - option price forward marks
- Strategy runtime now supports:
  - profile `follow` vs `contrarian` direction mode
  - profile group targeting via `by_grade`, `by_style`, or `by_grade_style`
  - configurable profile imbalance threshold
  - optional reconstructed profile pair-sum sanity bounds
  - strategy-specific sizing for grid/scalp/hedge/control lanes

## Replays Run

### Broad Profile Group Replay

Run id: `strategy-live-replay-broad-profile-groups-20260606T025557Z`

- Strategies: 42
- Scenarios: 80
- Evaluations: 3,360
- Executed/passed structural rows: 1,500
- Gated/blocked rows: 1,860

Notable results:

| Strategy | Samples | Win Rate | Sim PnL | ROI | Read |
| --- | ---: | ---: | ---: | ---: | --- |
| `profile_splus_hedger_follow_hold_60s_v1` | 7 | 100.0% | +1.9721 | +35.0% | Too sparse but promising |
| `profile_outcome_predictor_follow_hold_60s_v1` | 1 | 100.0% | +0.5400 | +122.7% | Too sparse |
| `crypto_direction_option_context_hold_60s_v1` | 40 | 35.0% | +3.2000 | +15.5% | Positive ROI, weak hit rate |
| C-only / event-context controls | 79-80 | 16-18% | negative | negative | Not promotable |

### Targeted Profile Follow Replay

Run id: `strategy-live-replay-targeted-profile-follow-20260606T025822Z`

- Strategies: 6
- Scenarios: 600
- Evaluations: 3,600

Notable results:

| Strategy | Samples | Win Rate | Sim PnL | ROI | Read |
| --- | ---: | ---: | ---: | ---: | --- |
| `profile_outcome_predictor_follow_hold_60s_v1` | 29 | 51.7% | +1.5300 | +12.7% | Positive but too sparse/noisy |
| `profile_splus_hedger_follow_hold_60s_v1` | 115 | 36.5% | +6.7144 | +8.1% | Positive ROI but below win-rate policy |
| `profile_hedge_scalping_v1` | 267 | 15.7% | +4.3680 | +2.6% | Too low hit rate |

### Strict V2/V3 Profile Replay

Run id: `strategy-live-replay-profile-strict-v2v3-20260606T030355Z`

- Strategies: 9
- Scenarios: 600
- Evaluations: 5,400

Notable results:

| Strategy | Samples | Win Rate | Sim PnL | ROI | Read |
| --- | ---: | ---: | ---: | ---: | --- |
| `profile_outcome_predictor_follow_hold_60s_v1/v2/v3` | 3 | 100.0% | +1.3700 | +89.5% | Too sparse, needs more source coverage |
| `profile_splus_hedger_follow_hold_60s_v1` | 123 | 53.7% | +9.0007 | +9.5% | Best current volume/ROI balance, not promotable |
| `profile_splus_hedger_follow_scalp_v1` | 120 | 52.5% | +8.4000 | +9.3% | Similar to hold lane, not promotable |
| stricter S+ v2/v3 | 60-62 | 46-48% | positive | +6.8-8.3% | Stricter filters reduced volume without improving hit rate |

### Horizon Scout

Partial run ids:

- `strategy-live-replay-profile-horizon-scout-20260606T030800Z-h15`
- `strategy-live-replay-profile-horizon-scout-20260606T030800Z-h30`
- `strategy-live-replay-profile-long-horizon-scout-20260606T031619Z-h90`
- `strategy-live-replay-profile-long-horizon-scout-20260606T031619Z-h120`

Result:

| Horizon | Best S+ Hedger Samples | Win Rate | Sim PnL | ROI | Read |
| ---: | ---: | ---: | ---: | ---: | --- |
| 15s | 96 | 39.6% | +0.6010 | +0.9% | Weak |
| 30s | 70 | 31.4% | +1.4966 | +3.2% | Weak |
| 60s | 123 | 53.7% | +9.0007 | +9.5% | Best tested so far |
| 90s | 33 | 21.2% | -2.1707 | -12.4% | Reject |
| 120s | 15 | 53.3% | +1.0552 | +11.0% | Sparse positive |

The 180s scout did not finish within the command timeout and should be rerun as a smaller bounded pass only if needed.

## Current Interpretation

The data supports the user's core intuition that B profile pressure has exploitable information, but the current simple strategies are not promotion-ready.

The best current simple family is S+ hedger follow around a 60s forward mark. It is profitable in shadow replay but has a hit rate near 50%, not above the required 70%. This means it is not a standalone live candidate yet, but it is a strong building block for:

- asymmetric payoff strategies
- filters that avoid bad event phases
- combining B profile pressure with C option path/rebound state
- combining B profile pressure with A crypto technical context

## Key Blockers

1. No current strategy reaches the promotion rule of at least 12 economic samples, >70% win rate, positive simulated PnL, lifecycle audit, reconciliation, and no strict signal blockers.
2. S+ hedger follow is profitable but too noisy. Threshold-only V2/V3 did not fix this.
3. Outcome-predictor subgroup may be strong but is too sparse due missing group coverage.
4. C-only and crypto observer controls are currently negative or low hit-rate.
5. SQLite remains a bottleneck for long broad replays and live service reads; Postgres adapter/import work remains important.

## Next Actions

1. Build a phase-aware profile strategy variant that only evaluates S+ hedger follow in event windows where C option path stats show a tradable 60s regime.
2. Add a combined B+C signal/strategy variant using:
   - S+ hedger follow
   - option pair near-50c / band state
   - recent rolling 30s/60s range
   - sane profile pair-sum bounds
3. Improve outcome-predictor profile source coverage before promoting any outcome-predictor strategy.
4. Keep 60s as the default profile-follow validation horizon for now; do not use 15s/30s/90s for this family unless a new mechanic specifically targets those horizons.
5. Continue Postgres read-adapter/import work so broad replay and dashboard reads stop fighting active SQLite writers.
