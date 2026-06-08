# Signal Universe Expansion Checkpoint

Date: 2026-06-05

Status: manual workloop checkpoint after pausing all crypto-options automations.

## Operator Request

The 23-signal first batch was too small for the intended discovery loop. The signal lab needed a larger review universe, automatic V2 pressure on weak signals, and enough source/type coverage to expose gaps before strategy design resumes.

This checkpoint records the manual expansion and validation pass. It is read-only and does not authorize strategy execution or live orders.

## Automation State

The following automations were left paused before and during this pass:

- `crypto-options-signal-validator-worker`
- `crypto-options-signal-design-reviewer`
- `crypto-options-team-tech-lead`
- `crypto-options-final-strategy-validator`
- `crypto-options-final-strategy-validator-2`
- `crypto-v4-pulse-validation-guard`

Data/API services remained available for read-only inspection. No manual orders were placed.

## Catalog Expansion

The registry expanded from 23 V1 hypotheses to 152 signal variants.

| Dimension | Count |
| --- | ---: |
| Total signal variants | 152 |
| Required data block A, crypto technical/price | 66 |
| Required data block B, profiles/distribution | 57 |
| Required data block C, option price/path | 85 |

Type coverage:

| Signal type | Count |
| --- | ---: |
| outcome_prediction | 50 |
| side_start | 17 |
| trend_regime | 8 |
| support_resistance | 12 |
| buy_rebound | 7 |
| grid_spacing | 7 |
| grid_count | 4 |
| grid_type | 5 |
| hedge_ratio | 15 |
| cashout_rebuy | 7 |
| stale_order_review | 5 |
| final_minute | 6 |
| liquidity_depth | 5 |
| latency_quality | 4 |

Expansion groups:

- Targeted V2/V3 repairs for weak crypto-only V1 rows.
- Profile distribution variants for count, shares, cost, grade, concentration, buying-ahead, and late-shift behavior.
- Hedge-ratio variants using profile-only and profile plus option-price context.
- Option-price path variants for rebound, support/resistance, inversion, grid spacing, cashout/rebuy, stale order review, final minute, and liquidity/depth.
- A/B/C confluence variants for profile-confirmed option moves and crypto-regime filters.
- Strict replay V2 variants for structurally passing rows that still require diversity and baseline checks.
- Remediation variants for A-only directional rows that had weak hit rates or negative average forward return.

## Validation Result

Initial expansion processed 152 variants through the four signal validation phases:

1. `last_week_backtest`
2. `last_month_backtest`
3. `random_sampling_backtest`
4. `live_shadow_test`

Current queue status:

| Queue status | Count |
| --- | ---: |
| PASSED | 152 |

Strict promotion status from the initial expansion:

| Promotion state | Count |
| --- | ---: |
| STRUCTURAL_PASS | 129 |
| NEEDS_V2_REVIEW | 23 |
| PROMOTION_READY | 0 |
| STRATEGY_CANDIDATE | 0 |

Interpretation:

- `PASSED` remains a phase-completion state only.
- No signal is approved for strategy dependency yet.
- `STRUCTURAL_PASS` means the signal is mechanically valid and directionally plausible under the current structural frame set, but still requires stricter replay/diversity/baseline proof.
- `NEEDS_V2_REVIEW` means a signal needs stricter criteria, redesign, or retirement before it can be considered again.

## V5 Building-Block Promotion Update

Later in the same manual loop, the registry was expanded again from 154 to 169 variants with V5 building-block signals for:

- Profile hedge ratio distribution references.
- Reconstructed profile-weighted Up/Down price references.
- Profile distribution freshness gates.
- Option price bucket/rebound references.
- Option realized-range grid-spacing references.
- Spread/depth/liquidity guards.
- Stale order cancel/replace gates.
- Path-efficiency and inversion grid selectors.
- Cross-source A/B/C freshness alignment.

The strict promotion classifier was also corrected so non-directional reference, gate, freshness, liquidity, and strategy-parameter signals are not blocked solely by negative directional forward return. These signals still require phase completion, sample count, diversity, hit-rate, and blocker checks. Directional/outcome/entry-style signals still require non-negative forward-return evidence.

Current strict promotion status after completing the V5 queue:

| Promotion state | Count |
| --- | ---: |
| PROMOTION_READY | 69 |
| NEEDS_V2_REVIEW | 100 |

Promotion-ready source coverage now includes:

| Required block | PROMOTION_READY count |
| --- | ---: |
| A crypto | 35 |
| B profiles | 7 |
| C options | 48 |

All V5 queue rows completed their four read-only validation phases. The queue total is 676 `PASSED` phase rows for 169 signals.

Important interpretation:

- The new `PROMOTION_READY` set is mostly non-directional strategy infrastructure: support/resistance, grid spacing/count/type, stale-order review, liquidity/depth, latency/freshness, and option-path references.
- Profile-only directional outcome/hedge-ratio rows still need stricter variants or better source data before they can be trusted as primary outcome signals. The currently promoted B rows are mostly freshness/reference/confluence building blocks, not proof that B-only outcome prediction is solved.
- This is a better signal base for strategy construction than the previous A-only state, but it is not yet enough for automatic live strategy promotion by itself.

## Remaining V2 Review Queue

The 23 `NEEDS_V2_REVIEW` rows are concentrated in A-only crypto-direction logic:

| Signal type | NEEDS_V2_REVIEW count |
| --- | ---: |
| outcome_prediction | 12 |
| side_start | 5 |
| trend_regime | 5 |
| support_resistance | 1 |

Observed causes:

- Crypto-price-only directional signals tend to cluster around 43-55% hit rate under current structural frames.
- Several V2/V3 crypto filters improved from negative average forward return to positive average forward return, but still failed the impact-adjusted minimum hit-rate gate.
- `support_resistance_cryptoprice_ifcm_pivot_distance_v1` retained high hit rate but negative average forward return, so its win criterion is likely measuring a non-directional proximity condition while being judged as a directional outcome signal.
- Profile and option-price families look more promising structurally, but none should be promoted until non-synthetic replay/diversity and baseline comparisons are done.

## Status Endpoint Hardening

The 152-signal expansion exposed a dashboard scaling issue: `/signals/validation/status` timed out because strict promotion metadata was recomputed with per-signal phase and diversity queries.

Patch applied:

- Strict-promotion status now computes latest phase rows and observation diversity in bulk.
- Dashboard polling uses a compact observation diversity summary.
- Richer joined diversity remains available through detailed result inspection.
- Dashboard endpoints sync the signal catalog only when the DB catalog count diverges from the code registry. This avoids re-upserting the 152-signal catalog on every browser poll.

Observed endpoint timing after restart:

| Endpoint | Result |
| --- | --- |
| `/v1/crypto-options-app/signals/catalog` | 152 signals, about 0.02s |
| `/v1/crypto-options-app/signals/validation/status` | 152 signals, repeated calls under the 15s browser timeout, observed about 0.6s-4.4s |

Safety fields remained:

- `orders_allowed=false`
- `live_trading_authorized=false`

## Test Evidence

Focused tests:

```powershell
python -m pytest tests\crypto_options_app\test_signal_validation_runtime_pytest.py tests\crypto_options_app\test_app_skeleton_pytest.py -q
```

Result:

```text
16 passed
```

The first full crypto-options-app suite run surfaced a stale fixture in the core-flow live-runner test:

```text
tests/crypto_options_app/test_core_flow_live_runner_pytest.py::test_core_flow_live_runner_runs_three_event_cycles_with_fake_submitter_pytest
expected fake submitter calls: 9
actual fake submitter calls: 0
```

Root cause:

- The fixture used a hardcoded event end time that had become stale on 2026-06-05.
- The runner correctly blocked every fake candidate as final-minute/expired, so the fake submitter was never called.

Patch:

- The fixture now uses a dynamic future event end timestamp.

Final verification:

```powershell
python -m pytest tests\crypto_options_app\test_core_flow_live_runner_pytest.py -q
python -m pytest tests\crypto_options_app\test_signal_validation_runtime_pytest.py tests\crypto_options_app\test_app_skeleton_pytest.py -q
python -m pytest tests\crypto_options_app -q
```

Result:

```text
4 passed
16 passed
205 passed
```

The final full-suite result after the dashboard router optimization was:

```text
205 passed
```

Additional V5 promotion-policy verification:

```powershell
python -m pytest tests\crypto_options_app\test_signal_validation_catalog_pytest.py tests\crypto_options_app\test_signal_validation_runtime_pytest.py -q
```

Result:

```text
14 passed
```

The added regression verifies that non-directional reference signals can reach `PROMOTION_READY` without positive directional forward-return, while weak directional/structural rows still remain blocked for review.

## Current Guidance

Next manual signal-lab work should focus on:

- Build strict replay/non-synthetic frame generation so `STRUCTURAL_PASS` rows can be tested against true historical path diversity.
- Add baseline/tautology checks for near-perfect option-price structural signals.
- Redesign A-only crypto-direction signals as regime/avoid gates unless a stricter directional variant clears the hit-rate threshold.
- Keep profile distribution and option-price path families as the first candidates for strict replay because they have the best structural coverage.
- Do not move any signal into strategy prototypes until it reaches `PROMOTION_READY`.

Follow-on structural strategy candidate roster:

- `29_structural_simple_strategy_live_test_candidates_2026-06-05.md` defines 25 simple strategy shells that use current `STRUCTURAL_PASS` signal building blocks and avoid the `NEEDS_V2_REVIEW` signals as primary directional inputs.

## Safety Confirmation

- No manual orders were placed.
- No live trading flags were enabled.
- Signal validation stayed read-only.
- The Signal Backtest Lab remains the active monitor surface: `/v1/crypto-options-app/signals/backtests`.
