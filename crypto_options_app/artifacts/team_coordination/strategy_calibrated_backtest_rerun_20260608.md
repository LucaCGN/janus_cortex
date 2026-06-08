# Calibrated Strategy Backtest Rerun

Generated: `2026-06-08T14:08:35.097755+00:00`

Scope: strategies with previous positive PnL in the prior daily/weekly/monthly risk rankings, excluding only current `REVIEW_BLOCKED` rows from the main rerun set.

Engine: `run_crypto_options_incident_replay_calibration` with measured latency, 500ms fallback, 250ms-5000ms bounds, 60s diagnostic TTL, and path-fill simulation. This is harsher than the old ranking because it replays recorded live decisions against captured quote paths and account-resolved outcomes.

## Rerun Counts

- Previous-positive strategies considered: `15`
- Main rerun set, not `REVIEW_BLOCKED`: `12`
- Excluded current `REVIEW_BLOCKED`: `3`
- Calibrated pass: `2`
- Calibrated reject: `8`
- Hold/no calibrated sample: `1`
- Hold/other quality or state caution: `1`
- Calibration decisions: `67`
- Calibration filled/unfilled: `52` / `15`
- Calibration data quality score: `0.742537`

## Main Table

| Strategy | State | Old Daily | Old Weekly | Old Monthly | New Decisions | New Sim PnL | Account PnL | New Quality | New Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `option_tail_reversal_hold_60s_v3:v3` | `LIVE_CANDIDATE` | $0.7400 / 100.0% / ret 493.3% / DD 0.0% / L0 / n=1 | $12.3800 / 100.0% / ret 520.2% / DD 0.0% / L0 / n=32 | $12.3800 / 100.0% / ret 520.2% / DD 0.0% / L0 / n=32 | 6 | $5.7066 | $4.4750 | 75.8% | PASS CALIBRATED RETEST |
| `master_hedge_grid_floor_tail_reversal_probe_v5:v5` | `LIVE_CANDIDATE` | n/a | $39.8300 / 100.0% / ret 528.8% / DD 0.0% / L0 / n=73 | $39.8300 / 100.0% / ret 528.8% / DD 0.0% / L0 / n=73 | 5 | $5.2700 | $10.7216 | 83.0% | PASS CALIBRATED RETEST |
| `profile_splus_hedger_follow_path_active_scalp_v1:v1` | `SHADOW_REVIEW` | $2.0640 / 87.5% / ret 35.9% / DD 0.8% / L1 / n=8 | n/a | n/a | 3 | $1.2000 | $-6.0000 | 86.7% | REJECT: ACCOUNT LOSS |
| `profile_splusplus_hedger_follow_hold_60s_v4:v4` | `SHADOW_REVIEW` | $2.3139 / 100.0% / ret 35.0% / DD 0.0% / L0 / n=7 | n/a | n/a | 3 | $-3.1856 | $-10.3880 | 80.0% | REJECT: ACCOUNT LOSS |
| `master_hedge_grid_floor_tail_reversal_probe_v6:v6` | `SHADOW_REVIEW` | n/a | $12.7120 / 100.0% / ret 515.9% / DD 0.0% / L0 / n=24 | $12.7120 / 100.0% / ret 515.9% / DD 0.0% / L0 / n=24 | 5 | $-3.3750 | $-0.5112 | 70.0% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_path_active_hold_60s_v1:v1` | `SHADOW_REVIEW` | $0.9490 / 50.0% / ret 15.2% / DD 4.3% / L2 / n=10 | $4.9005 / 72.6% / ret 9.2% / DD 1.9% / L4 / n=62 | $4.9005 / 72.6% / ret 9.2% / DD 1.9% / L4 / n=62 | 5 | $-10.2885 | $-17.1135 | 75.0% | REJECT: ACCOUNT LOSS |
| `master_hedge_grid_floor_tail_reversal_probe_v4:v4` | `SHADOW_REVIEW` | n/a | $12.0400 / 100.0% / ret 605.6% / DD 0.0% / L0 / n=19 | $12.0400 / 100.0% / ret 605.6% / DD 0.0% / L0 / n=19 | 5 | $-11.2600 | $-9.4761 | 84.0% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_near50_hold_60s_v1:v1` | `SHADOW_REVIEW` | $1.0356 / 100.0% / ret 40.5% / DD 0.0% / L0 / n=4 | n/a | n/a | 7 | $-26.7580 | $-35.4188 | 69.3% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_hold_60s_v6:v6` | `SHADOW_REVIEW` | $4.1050 / 80.0% / ret 67.5% / DD 6.0% / L1 / n=10 | $6.6734 / 78.9% / ret 51.3% / DD 2.8% / L2 / n=19 | $6.6734 / 78.9% / ret 51.3% / DD 2.8% / L2 / n=19 | 5 | $-27.0720 | $-39.5602 | 80.0% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_hold_60s_v10:v10` | `SHADOW_REVIEW` | $2.2820 / 100.0% / ret 28.3% / DD 0.0% / L0 / n=8 | $4.6200 / 100.0% / ret 30.0% / DD 0.0% / L0 / n=15 | $4.6200 / 100.0% / ret 30.0% / DD 0.0% / L0 / n=15 | 6 | $-43.0000 | $-47.3250 | 71.7% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_hold_60s_v9:v9` | `SHADOW_READY` | $1.4280 / 100.0% / ret 28.4% / DD 0.0% / L0 / n=5 | n/a | n/a | 1 | $0.0000 | $0.6800 | 0.0% | HOLD: LOW QUALITY |
| `profile_splus_hedger_follow_hold_60s_v16:v16` | `SHADOW_READY` | $1.1738 / 100.0% / ret 28.6% / DD 0.0% / L0 / n=4 | n/a | n/a | n/a | n/a | n/a | n/a | NO CALIBRATED SAMPLE |

## Calibrated Passes

| Strategy | Sim PnL | Account PnL | Actual WR | Quality | Note |
|---|---:|---:|---:|---:|---|
| `option_tail_reversal_hold_60s_v3:v3` | $5.7066 | $4.4750 | 66.7% | 75.8% | Candidate for small controlled retest only; no live promotion from this report. |
| `master_hedge_grid_floor_tail_reversal_probe_v5:v5` | $5.2700 | $10.7216 | 80.0% | 83.0% | Candidate for small controlled retest only; no live promotion from this report. |

## Rejected By New Engine

| Strategy | Sim PnL | Account PnL | Quality | Reason |
|---|---:|---:|---:|---|
| `profile_splus_hedger_follow_hold_60s_v10:v10` | $-43.0000 | $-47.3250 | 71.7% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_hold_60s_v6:v6` | $-27.0720 | $-39.5602 | 80.0% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_near50_hold_60s_v1:v1` | $-26.7580 | $-35.4188 | 69.3% | REJECT: ACCOUNT LOSS |
| `master_hedge_grid_floor_tail_reversal_probe_v4:v4` | $-11.2600 | $-9.4761 | 84.0% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_path_active_hold_60s_v1:v1` | $-10.2885 | $-17.1135 | 75.0% | REJECT: ACCOUNT LOSS |
| `master_hedge_grid_floor_tail_reversal_probe_v6:v6` | $-3.3750 | $-0.5112 | 70.0% | REJECT: ACCOUNT LOSS |
| `profile_splusplus_hedger_follow_hold_60s_v4:v4` | $-3.1856 | $-10.3880 | 80.0% | REJECT: ACCOUNT LOSS |
| `profile_splus_hedger_follow_path_active_scalp_v1:v1` | $1.2000 | $-6.0000 | 86.7% | REJECT: ACCOUNT LOSS |

## Not Calibrated Yet

These had previous positive PnL, but no recorded incident-window live decision in the calibrated path report. They need a read-only historical path replay over a matching captured quote window before they can be trusted again.

| Strategy | State | Best Old Horizon | Best Old PnL | Best Old Return | Best Old WR |
|---|---|---|---:|---:|---:|
| `profile_splus_hedger_follow_hold_60s_v16:v16` | `SHADOW_READY` | daily | $1.1738 | 28.6% | 100.0% |

## Excluded Current REVIEW_BLOCKED Rows

| Strategy | Best Old Horizon | Best Old PnL | Current blockers |
|---|---|---:|---|
| `master_hedge_grid_floor_tail_reversal_probe_v3:v3` | monthly | $8.0500 | latest_lifecycle_not_passed, calibrated_replay_non_positive_pnl, account_incident_actual_negative_pnl |
| `option_tail_reversal_scalp_v3:v3` | monthly | $13.9680 | latest_lifecycle_not_passed |
| `profile_splus_hedger_follow_hold_60s_v8:v8` | monthly | $10.9040 | latest_lifecycle_not_passed, calibrated_replay_non_positive_pnl, account_incident_actual_negative_pnl |

## Interpretation

- The old profile-follow winners mostly do not survive the calibrated replay/account comparison. This confirms the old backtest/shadow ranking was too optimistic.
- The strongest current calibrated retest shapes are tail-reversal/outcome-prediction variants, not the larger profile-follow accumulator lanes.
- This report does not authorize live trading. The next safe step is to build a broader historical path-replay runner that can calibrate the `NO CALIBRATED SAMPLE` strategies without placing orders.
