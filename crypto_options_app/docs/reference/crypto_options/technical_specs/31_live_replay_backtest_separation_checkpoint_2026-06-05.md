# Live Replay And Backtest Separation Checkpoint - 2026-06-05

## Decision

Signals and strategies now expose historical backtests separately from live replay/shadow evidence.

This keeps the development loop explicit:

1. Historical signal backtests prove signal behavior on past A/B/C frames.
2. Signal live replay proves current read-only signal behavior against live-shadow data.
3. Historical strategy backtests remain replay/simulation evidence.
4. Strategy live replay proves strategy mechanics through candidate -> intent -> order -> fill -> position -> exit lifecycle in `shadow` mode.
5. Supervised live trading remains a separate runtime gate and is not part of live replay.

## API Surface

New read-only endpoints:

| Endpoint | Purpose | Orders |
| --- | --- | --- |
| `/v1/crypto-options-app/signals/replay/backtests` | Last-week, last-month, and random-sample signal backtest rows | Disabled |
| `/v1/crypto-options-app/signals/replay/live` | Signal `live_shadow_test` rows only | Disabled |
| `/v1/crypto-options-app/strategies/replay/backtests` | Historical strategy replay rows | Disabled |
| `/v1/crypto-options-app/strategies/replay/live` | Strategy `shadow` live-replay rows only | Disabled |

The Signal Review Console now has a dedicated `Replay` tab showing all four groups.

## Persistence Contract

`strategy_validation_runs.run_type` and `run_phase` now map as:

| Runtime mode | run_type | run_phase |
| --- | --- | --- |
| Dry run | `dry_run` | `historical_replay` |
| Shadow | `shadow` | `live_replay` |
| Supervised live | `supervised_live` | `supervised_live` |

This replaces the previous ambiguous mapping where supervised-live rows were labeled as `live_shadow_test`.

## Strategy Live Replay Worker

Added read-only worker and CLI:

- `crypto_options_app/workers/strategy_backtest_replay.py`
- `crypto_options_app/scripts/run_crypto_options_strategy_backtest_replay.py`
- `crypto_options_app/workers/strategy_live_replay.py`
- `crypto_options_app/scripts/run_crypto_options_strategy_live_replay.py`

The historical replay worker:

- Uses `mode='dry_run'`.
- Persists `strategy_validation_runs.run_phase='historical_replay'`.
- Uses a deterministic historical fixture by default for legacy structural tests.
- Can explicitly replay captured option-path scenarios with selectors such as `tail_touch`, `high_inversion`, `profile_preferred`, `profile_opposed`, and `profile_group` while remaining `dry_run`.
- Never enables live order flags.
- Does not write validation budget ledger rows.

The live replay worker:

- Uses the latest `replay_frames` row when available.
- Falls back to latest `polymarket_price_ticks` row.
- Uses a fixture only when no live/captured data exists.
- Runs selected strategies with `mode='shadow'`.
- Persists canonical lifecycle rows.
- Never enables live order flags.
- Does not write validation budget ledger rows.

## Selected Strategy Replay Batch

Historical run id:

`strategy-backtest-replay-selected-20260605`

Live replay run id:

`strategy-live-replay-selected-20260605`

Strategies:

- `profile_hedge_scalping_v1`
- `event_context_profile_confirmed_v1`
- `hedger_ratio_replication_v4`
- `profile_hedge_scalping_v4`
- `grid_band_rebound_v3`

Result:

- 5 historical strategy results with `run_type='dry_run'` and `run_phase='historical_replay'`
- 5 live replay strategy results with `run_type='shadow'` and `run_phase='live_replay'`
- 10 simulated executions total
- 10 candidates
- 10 intents
- 10 orders
- 10 fills
- 10 positions
- 10 exit plans
- 0 validation budget ledger rows
- `orders_allowed=false`
- `live_trading_authorized=false`

The scenario source was the latest centralized `polymarket_price_ticks` row.

## Endpoint Smoke Results

Observed after the selected replay batch:

| Endpoint | Rows |
| --- | ---: |
| Signal backtests | 462 |
| Signal live replay | 154 |
| Strategy backtests | 5 |
| Strategy live replay | 5 |

## Tests

Focused tests passed:

`python -m pytest tests\crypto_options_app\test_app_skeleton_pytest.py tests\crypto_options_app\test_runtime_persistence_pytest.py tests\crypto_options_app\test_strategy_manager_sync_pytest.py tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -q`

Result:

`15 passed`

After adding the historical strategy replay worker:

`python -m pytest tests\crypto_options_app\test_app_skeleton_pytest.py tests\crypto_options_app\test_runtime_persistence_pytest.py tests\crypto_options_app\test_strategy_manager_sync_pytest.py tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -q`

Result:

`16 passed`

## Next Work

- Continue broadening strict historical strategy replay rows now that captured option-path selectors are available.
- Keep live replay read-only; do not promote live replay to supervised live without separate budget, reconciliation, and stop-gate approval.
- Use the Replay tab as the operator-facing boundary between signal evidence, strategy mechanics, and future live promotion.

## Captured Historical Replay Selector Update 2026-06-06

The backtest replay CLI now supports captured scenarios:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_backtest_replay.py --scenario-selector tail_touch --max-scenarios 24 --json
```

This still writes `run_type='dry_run'` and `run_phase='historical_replay'`; it does not authorize orders and does not write budget ledger rows.

First promotion-gate checkpoint:

| Strategy | Historical Passes | Recent Shadow Samples | Shadow Win Rate | Shadow PnL | State |
| --- | ---: | ---: | ---: | ---: | --- |
| `option_tail_reversal_hold_60s_v3` | 12 | 46 | 100% | +$5.70 | `LIVE_CANDIDATE` |
| `option_tail_reversal_scalp_v3` | 12 | 46 | 100% | +$6.84 | `LIVE_CANDIDATE` |
| `master_hedge_grid_floor_tail_reversal_probe_v3` | 8 | 36 | 100% | +$7.056 | `LIVE_CANDIDATE` |

The checkpoint validates the current `master_hedge_grid_scalping` direction:

- Candidate selection is based on empirical 1c/5c/10c tail-touch comeback windows.
- Floor-preserving order math is a hard gate.
- Profile pressure is retained as confidence context, not as a hard veto for valid empirical tail windows.
- The next step is a scoped supervised child-run plan, not manual orders.

## Paired Seed Floor Economics Update 2026-06-06

The paired seed replay path now distinguishes single-leg liquidation marks from paired Up/Down economics. For equal-share paired seed lanes, shadow evidence records:

- `guaranteed_floor_value_usd`
- `guaranteed_floor_pnl_usd`
- `paired_entry_pair_sum`
- `paired_forward_mark_pnl_usd`
- `paired_forward_mark_available`

Two paired seed variants now exist:

| Strategy | Role | Gate |
| --- | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v1` | strict positive-floor paired entry | requires paired ask sum low enough to create immediate positive floor |
| `master_hedge_grid_floor_paired_seed_builder_v2` | realistic seed-and-harvest entry | allows a small negative floor only when inversion/grid viability is high |

Bounded replay on `2026-06-06` showed the distinction matters:

| Strategy | Scenarios | Executed | Blocked | Floor PnL | Forward paired PnL | Promotion |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `master_hedge_grid_floor_paired_seed_builder_v1` | 60 | 0 | 60 | n/a | n/a | no |
| `master_hedge_grid_floor_paired_seed_builder_v2` | 60 | 59 | 1 | `-$1.2071` total | `-$2.3355` total | no |

Interpretation:

- Immediate positive-floor paired entry is rare because paired ask sums usually sit near or above `1.00`.
- V2 is structurally runnable, but seed-only economics is still negative before inversion scalps are modeled.
- The next required strategy mechanic is not another seed-only replay. It is an inversion scalp replay that can convert small initial spread cost into a protected floor through sell/rebuy fills.
- No paired seed lane is live-safe until the replay engine models both legs, scalp exits/rebuys, queue/fillability, and floor-preserving follow-up orders.

## Paired Seed Scalp Path Update 2026-06-06

The replay path now attaches bounded paired Up/Down path snapshots between entry and the forward mark, allowing a seed strategy to simulate extra dip-buy / rebound-sell cycles without treating immediate bid liquidation as promotion-quality PnL.

New variants:

| Strategy | Role | Scalp Gate |
| --- | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v3` | paired seed plus aggressive inversion scalp probe | 3c dip buy, 3c target, $0.25 extra notional |
| `master_hedge_grid_floor_paired_seed_builder_v4` | paired seed plus conservative inversion scalp probe | 5c dip buy, 2c target, $0.15 extra notional |

Bounded 60-scenario high-inversion replay:

| Strategy | Executed | Promotion-Ready Samples | Completed Cycles | Cycle Profit | Final Floor PnL | Dominant Blockers | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v3` | 59/60 | 2 | 17 | `+$2.0979` | `-$3.8592` | floor not positive, open scalp positions, no completed cycles | review only |
| `master_hedge_grid_floor_paired_seed_builder_v4` | 59/60 | 4 | 17 | `+$1.2587` | `-$2.4984` | floor not positive, no completed cycles, open scalp positions | review only |

Interpretation:

- The path simulator is now exercising the intended mechanic: volatility can generate completed scalp cycles.
- The current V3/V4 rules still leave too many samples with negative final floor, no completed cycle, or open scalp exposure at the forward mark.
- V4 reduced downside versus V3 but did not create promotion-quality evidence. It has only 4 promotion-ready samples and a negative total final floor PnL.
- Do not promote V3 or V4. The next useful step is a stricter closed-cycle seed variant that only counts events with adequate paired path density, fillability, and floor-preserving closed extra positions.

## Paired Seed Closed-Cycle Scalp Update 2026-06-06

`master_hedge_grid_floor_paired_seed_builder_v5` adds the first closed-cycle paired seed scalp gate. It keeps the paired seed primitive from V2/V4 but reduces extra scalp notional, requires at least two paired path snapshots, and stops opening extra scalp positions after the first 67% of the available path window so late buys do not remain unresolved at the forward mark.

Bounded replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v5 --max-scenarios 60 --max-trades-per-strategy 60 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion --run-id bounded-master-paired-seed-v5-closedcycle-highinv-60x-20260606T222705Z --json
```

Result:

| Strategy | Executed | Promotion-Ready Samples | Completed Cycles | Cycle Profit | Final Floor PnL | Open Extra Position Samples | Dominant Blockers | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v5` | 51/60 | 16 | 17 | `+$1.0070` | `-$0.0417` | 0 | floor not positive, no completed cycles, low path density | review only |

Interpretation:

- V5 fixes the most important V3/V4 mechanical issue: no replay sample ended with extra open scalp inventory.
- The closed-cycle gate materially improved final floor economics from V4's `-$2.4984` aggregate floor PnL to `-$0.0417`.
- The variant is still not live-safe. Aggregate final floor PnL is still negative, and 35 executed samples still fail final positive-floor criteria.
- The next useful work is not to relax V5. It is to use this closed-cycle simulator to search stricter entry selectors: pair sum, spread/depth, path density, first-minute inversion, and profile pressure buckets that convert the near-breakeven floor into positive aggregate floor PnL.

## Paired Seed Entry-Gap Scalp Update 2026-06-06

`master_hedge_grid_floor_paired_seed_builder_v6` keeps the V5 closed-cycle simulator and adds a no-lookahead entry-displacement gate. It requires the Up/Down entry ask gap to be between `0.05` and `0.40`, with pair sum at most `1.02`, so the simulator avoids near-symmetric 49c/51c churn where V5 repeatedly finished close to breakeven but did not build enough protected floor.

Bounded replays:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v6 --max-scenarios 60 --max-trades-per-strategy 60 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion --run-id bounded-master-paired-seed-v6-entrygap-highinv-60x-20260606T223556Z --json
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v6 --max-scenarios 120 --max-trades-per-strategy 120 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion --run-id bounded-master-paired-seed-v6-entrygap-highinv-120x-20260606T224010Z
```

Result:

| Strategy | Run | Executed | Promotion-Ready Samples | Completed Cycles | Cycle Profit | Final Floor PnL | Open Extra Position Samples | Event Coverage | Decision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v6` | high inversion 60x | 20/60 | 16 | 16 | `+$0.9927` | `+$0.5578` | 0 | 1 BTC event | promising, not live-safe |
| `master_hedge_grid_floor_paired_seed_builder_v6` | high inversion 120x | 28/120 | 20 | 20 | `+$1.9167` | `+$1.3234` | 0 | 2 BTC events | promising, not live-safe |
| `master_hedge_grid_floor_paired_seed_builder_v6` | high inversion disjoint 60x | 2/26 | 2 | 2 | `+$0.1200` | `+$0.0804` | 0 | 1 BTC event | fails diversity proof |

Interpretation:

- V6 is the first paired seed/scalp variant with positive aggregate final floor PnL in bounded replay.
- The improvement came from a stricter entry selector, not from loosening the closed-cycle gate. Extra scalp positions were closed by the forward mark in all executed samples.
- The evidence is still too concentrated for live promotion. The 120-scenario run executed only two BTC events, so it does not yet prove the mechanic across independent event regimes.
- A stricter `high_inversion_disjoint` selector was added to cap replay at two samples per event. Under that selector, V6 only found two executed samples, both from one event. That confirms the current positive evidence is concentrated rather than general.
- Next action is not live promotion. Either collect more captured high-inversion events or build V7 around a less concentrated no-lookahead condition, likely combining entry displacement with profile pressure, crypto context, and spread/depth buckets.

## Paired Seed Profile Buckets Update 2026-06-06

Two profile-aware paired seed siblings were added after V6 failed the disjoint-event diversity proof:

| Strategy | Role | Result | Decision |
| --- | --- | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v7` | S+ hedger directional profile-coherent entry-gap scalp | 14 disjoint scenarios, 0 executions | too strict / directional profile mismatch |
| `master_hedge_grid_floor_paired_seed_builder_v8` | profile-balanced entry-gap scalp | 14 disjoint scenarios, 0 executions | useful diagnostic, not promotable |

V7 blockers showed that directional S+ hedger pressure does not currently line up with the paired seed entry-gap windows: `profile_distribution_side_mismatch`, `profile_distribution_pressure_too_balanced`, reconstructed pair-sum failures, low entry ask gap, and weak pair-depth pressure all appeared in the same small disjoint slice.

V8 changed the hypothesis to match the current master strategy premise more closely: a grid seed wants enough volatility and two-sided activity, not a pure winner pick. The runtime now has an explicit `profile_pressure_mode="balanced_required"` branch, with regression coverage confirming that balanced top-profile pressure passes while unbalanced pressure safely blocks.

Bounded V8 replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v8 --max-scenarios 30 --max-trades-per-strategy 30 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion_disjoint --run-id bounded-master-paired-seed-v8-balanced-disjoint-30x-20260606T231312Z
```

Result:

| Strategy | Scenarios | Executed | Promotion-Ready | Completed Cycles | Blocker Summary | Decision |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v8` | 14 | 0 | 0 | 0 | `profile_distribution_pressure_not_balanced` 10, `paired_seed_entry_ask_gap_low` 12, `paired_scalp_path_density_low` 4, `option_path_pair_depth_pressure_weak` 2 | review only |

Interpretation:

- The balanced-profile gate is working and is not a cosmetic filter.
- Current captured high-inversion disjoint rows mostly have directional or noisy profile pressure, not balanced pressure.
- The remaining balanced rows still fail entry displacement or path-density economics, so V8 should not be promoted or rerun unchanged.
- The next useful work is either to wait for more captured high-inversion rows or build a V9 that uses profile balance as a confidence/bucket label while testing a separate no-lookahead entry condition, rather than weakening the floor or closed-cycle gates.
- No live trading was started from V7 or V8.

## Paired Seed High-Churn Profile Bucket Update 2026-06-06

V9 and V10 test the next profile-aware branch after V8. The change is conceptual: profile balance is no longer a hard gate. It becomes a bucket/confidence label, while execution depends on stronger option-path churn and the same floor-preserving closed-cycle paired scalp economics.

| Strategy | Difference From V8 | Bounded Result | Decision |
| --- | --- | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v9` | optional profile confidence, high-churn path gate, no minimum entry ask gap, three paired path snapshots required | 10 scenarios, 0 executions | too strict for current replay granularity |
| `master_hedge_grid_floor_paired_seed_builder_v10` | same as V9, but two paired path snapshots required to match current captured replay rows | 10 scenarios, 6 lifecycle executions, 2 promotion-economics samples | review only |

V9 replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v9 --max-scenarios 20 --max-trades-per-strategy 20 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion_disjoint --run-id bounded-master-paired-seed-v9-profilebucket-disjoint-20x-20260606T232324Z
```

V9 blocked all 10 selected scenarios. It no longer failed on profile pressure; it failed mainly on `paired_scalp_path_density_low` because the current replay rows generally expose two usable paired path snapshots, not three.

V10 replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v10 --max-scenarios 20 --max-trades-per-strategy 20 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion_disjoint --run-id bounded-master-paired-seed-v10-profilebucket-disjoint-20x-20260606T232527Z
```

Result:

| Strategy | Scenarios | Lifecycle Executions | Promotion-Economics Samples | Completed Cycles | Cycle Profit | Final Floor PnL | Open Extra Positions | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `master_hedge_grid_floor_paired_seed_builder_v10` | 10 | 6 | 2 | 2 | `+$0.1000` | `-$0.0188` | 0 | review only |

Interpretation:

- V10 proves the high-churn/profile-bucket branch can execute on disjoint high-inversion replay rows without hard profile blocking.
- The floor outcome is still not promotion-quality: only 2 promotion-economics samples, aggregate final floor PnL slightly negative, and sample count below the 12+ economic gate.
- V10 should be the next scaffold, not a live candidate. The next useful sibling should improve entry quality or exit cycle completion without weakening floor preservation: for example, require positive pair-depth pressure direction by selected side, improve paired path capture density, or alter the buy-drop/target pair based on observed churn.
- No live trading was started from V9 or V10.

## Paired Seed Quality And Recent-Proof Selector Update 2026-06-06

V11 tightens V10 by requiring a usable paired seed entry gap and material pair-depth pressure before the closed-cycle paired scalp simulator can execute. This keeps the protected-floor and closed-cycle mechanics intact instead of widening toward weak rows.

| Strategy / Selector | Scenarios | Lifecycle Executions | Promotion-Economics Samples | Completed Cycles | Cycle Profit | Final Floor PnL | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `master_hedge_grid_floor_paired_seed_builder_v11` / `high_inversion_disjoint` | 14 | 2 | 2 | 2 | `+$0.1000` | `+$0.0604` | promising, not live-safe |
| `master_hedge_grid_floor_paired_seed_builder_v11` / `recent_high_inversion_disjoint` | 12 | 0 | 0 | 0 | `$0.0000` | `$0.0000` | recent windows blocked |

Historical quality replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v11 --max-scenarios 30 --max-trades-per-strategy 1 --forward-mark-horizon-seconds 60 --scenario-selector high_inversion_disjoint --run-id bounded-master-paired-seed-v11-quality-refresh-30x-20260606T233654Z
```

Result: 14 scenarios, 2 simulated executions, both promotion-economics-ready, `+$0.06039604` aggregate floor PnL, and zero open extra scalp positions. Both executed rows still came from event `563367`, so the evidence remains below the 12+ sample and disjoint-event gates.

Recent proof replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --strategy-ids master_hedge_grid_floor_paired_seed_builder_v11 --max-scenarios 16 --max-trades-per-strategy 1 --forward-mark-horizon-seconds 60 --scenario-selector recent_high_inversion_disjoint --run-id bounded-master-paired-seed-v11-recent-quality-16x-20260606T234024Z
```

Result: 12 recent scenarios across six fresh events, all safely blocked. Dominant blockers were `hedge_floor_inversion_intensity_low`, `option_path_rebound_flips_low`, `option_path_avg_rolling_60s_range_low`, and entry-gap bounds. This is the correct behavior: the current windows were not viable hedge-grid-floor environments, so the system should not promote them just because data is fresh.

Implementation note: `recent_high_inversion_disjoint` was added as a separate selector. `high_inversion_disjoint` remains the best-historical proof selector; the new selector is for recent live-shadow promotion proof and keeps the same per-event cap.

Decision:

- Do not promote V11 yet.
- Keep V11 as the current best quality-gated paired seed/scalp scaffold.
- Next work should collect/run more recent high-inversion samples and create siblings that adapt buy-drop/target spacing or pair-depth direction without weakening floor-preserving and closed-cycle gates.
- No live trading was started from V11.

### Immediate Recent-Window Comparison 2026-06-06T23:46Z

After more C Options snapshots accumulated, the recent-proof selector was rerun against three paired seed variants:

| Strategy | Run ID | Recent Scenarios | Status | Main Blockers | Decision |
| --- | --- | ---: | --- | --- | --- |
| `master_hedge_grid_floor_paired_seed_builder_v11` | `bounded-master-paired-seed-v11-recent-quality-12x-20260606T234443Z` | 12 across 6 events | 0 executions | low rolling 60s range, low inversion intensity, low rebound flips, entry gaps outside quality range | no trade |
| `master_hedge_grid_floor_paired_seed_builder_v10` | `bounded-master-paired-seed-v10-recent-quality-12x-20260606T234517Z` | 12 across 6 events | 0 executions | same low range/inversion profile plus high entry gaps | no trade |
| `master_hedge_grid_floor_paired_seed_builder_v6` | `bounded-master-paired-seed-v6-recent-quality-12x-20260606T234533Z` | 12 across 6 events | 0 executions | same low range/inversion profile plus low entry gaps | no trade |

Interpretation:

- The current market slice is not a viable `master_hedge_grid_scalping` seed environment.
- This is not a V11 over-tightening issue because V10 and V6 also blocked all recent rows.
- The correct manager action is to keep collecting C Options data and run recent-proof checks until a naturally inversion-rich window appears, while developing separate simple strategy families for non-inversion regimes instead of forcing the hedge-grid-floor lane live.

## Profile Consensus High-Entry Sibling Update 2026-06-06T23:55Z

Because recent windows were not suitable for hedge-grid-floor volatility harvesting, the profile consensus family was used as the next simple strategy lane. V7 and V8 are S+ hedger follow siblings for high-confidence profile windows. They test whether the high-entry band can be used only when subgroup pressure is materially stronger.

| Strategy | Selector | Scenarios | Executions | W/L | Simulated PnL | Decision |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `profile_splus_hedger_follow_hold_60s_v6` | `profile_group` | 24 | 1 | 1/0 | `+$0.2936` | sparse positive |
| `profile_splus_hedger_follow_hold_60s_v7` | `profile_group` | 24 | 1 | 1/0 | `+$0.3780` | sparse positive |
| `profile_splus_hedger_follow_hold_60s_v8` | `profile_group` | 24 | 3 | 3/0 | `+$0.9240` | promising, not live-safe |
| `profile_splus_hedger_follow_hold_60s_v8` | `profile_group` | 32 eligible from 80 requested | 3 | 3/0 | `+$0.9240` | promising, still sparse |

V8 run IDs:

```powershell
bounded-profile-splus-v8-profilegroup-24x-20260606T235357Z
bounded-profile-splus-v8-profilegroup-80x-20260606T235513Z
```

Executed V8 rows:

- `btc-updown-5m-1780787700`, Down, 72c entry, 99c forward bid, `+$0.3780`.
- `btc-updown-5m-1780786200`, Up, 77c entry, 99c forward bid, `+$0.3080`.
- `btc-updown-5m-1780783800`, Up, 81c entry, 98c forward bid, `+$0.2380`.

Dominant V8 blockers were `option_entry_price_above_band`, `profile_distribution_pressure_too_balanced`, `option_entry_price_below_band`, and `profile_distribution_top_profile_concentration_high`.

Decision:

- Do not promote V8. It is below the 12+ economic sample gate.
- Keep V8 as the current best simple profile-consensus candidate and run broader/live-shadow sample accumulation when DB resources are idle.
- Do not relax it into weak profile rows; the useful improvement was high-confidence profile pressure plus restored divergence tolerance, not a removal of safety gates.

### V8 Repeat-Sample Check 2026-06-07T00:04Z

The selector path was audited before rerunning V8. `profile_group` is already recent-first because it orders profile snapshots by `computed_at_utc DESC`, so no redundant recent selector was added.

Focused regression:

```powershell
python -m pytest tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -k "profile_group_selector" -q
```

Result: 1 passed.

Replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --run-id bounded-profile-splus-v8-profilegroup-12x-20260607T000105Z --strategy-ids profile_splus_hedger_follow_hold_60s_v8 --scenario-selector profile_group --max-scenarios 12 --forward-mark-horizon-seconds 60 --max-trades-per-strategy 1 --validation-budget-cap-usd 50
```

Result: 12 scenarios, 3 simulated executions, 9 blocked rows, 3 lifecycle-covered/reconciled wins, `+$0.9240` simulated PnL. The executed samples were the same three events listed above, so this run did not increase distinct evidence.

Decision:

- V8 remains promising but is not live-safe.
- Repeated successful rows must not count as accumulating proof.
- Next implementation need: add a repeat-aware promotion report/gate for strategy evidence, then either wait for fresh eligible profile windows or test a separate simple strategy family.

### Repeat-Aware Promotion Gate 2026-06-07T00:09Z

The repeat-aware gate is now enforced in the strategy promotion manager. Recent shadow/live economics must satisfy both:

- `recent_shadow_live_economic_sample_count >= 12`
- `recent_shadow_live_distinct_event_count >= 12`

This prevents the V8 situation where the same three successful events can be replayed repeatedly and appear to be accumulating promotion proof. Runtime rows derive distinct-event evidence from `event_key` / `event_slug` in the persisted validation evidence. Fixture or aggregate rows can still provide explicit diversity counts, but absent real event identity they do not override runtime evidence.

Regression coverage:

```powershell
python -m pytest tests\crypto_options_app\test_strategy_promotion_manager_pytest.py -q
python -m pytest tests\crypto_options_app\test_strategy_live_replay_worker_pytest.py -k "profile_group_selector" -q
```

Runtime smoke after the patch showed no `LIVE_CANDIDATE` rows and live flags remained disabled.

## Tail Reversal Sample Accumulation 2026-06-07T00:14Z

Tail reversal is a required building block for `master_hedge_grid_scalping` because surplus tail optionality depends on empirical 1c/5c/10c comeback behavior. The current strict selector remains sparse but useful.

Strict replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --run-id bounded-tail-probes-forward-edge-clean-12x-20260607T001245Z --strategy-ids master_hedge_grid_floor_tail_reversal_probe_v3 master_hedge_grid_floor_tail_reversal_probe_v4 master_hedge_grid_floor_tail_reversal_probe_v5 option_tail_reversal_hold_60s_v3 option_tail_reversal_scalp_v3 --scenario-selector tail_touch_forward_edge_clean --max-scenarios 12 --forward-mark-horizon-seconds 60 --max-trades-per-strategy 1 --validation-budget-cap-usd 50
```

Result:

- 2 eligible scenarios.
- 7 simulated executions, 3 blocked rows.
- `option_tail_reversal_hold_60s_v3`: 2/2, `+$0.2700`, 2 distinct events.
- `option_tail_reversal_scalp_v3`: 2/2, `+$0.3240`, 2 distinct events.
- `master_hedge_grid_floor_tail_reversal_probe_v3/v4/v5`: 1 pass and 1 block each; the weaker event blocked on `hedge_floor_tail_reversal_probability_low`.

Exploratory broad replay:

```powershell
python crypto_options_app\scripts\run_crypto_options_strategy_live_replay.py --run-id bounded-tail-c-only-touch-explore-12x-20260607T001315Z --strategy-ids option_tail_reversal_hold_60s_v3 option_tail_reversal_scalp_v3 --scenario-selector tail_touch --max-scenarios 12 --forward-mark-horizon-seconds 60 --max-trades-per-strategy 1 --validation-budget-cap-usd 50
```

Result: 12 scenarios, 2 executions, 22 blocked rows. Dominant blockers were `option_forward_cashout_edge_low`, `option_path_strong_rebound_touches_low`, and `option_path_pair_depth_pressure_weak`.

Decision:

- Do not loosen tail gates just to increase sample count.
- Strict clean-edge tail rows are sparse but positive so far.
- Promotion remains blocked until at least 12 distinct recent economic events pass with clean lifecycle/reconciliation.
- Next UI/control-center improvement should expose strict-tail eligible counts and blocker breakdowns so sparse opportunity regimes are visible.
## 2026-06-07 Lightweight Candidate Scout

Full strategy live-replay is too expensive to use as a discovery loop for
sparse selectors. The worker now exposes a read-only candidate scout:

```powershell
python -m crypto_options_app.scripts.scout_crypto_options_strategy_live_replay_candidates `
  --scenario-selector tail_touch_forward_edge_clean `
  --strategy-ids option_tail_reversal_hold_60s_v3 option_tail_reversal_scalp_v3 `
  --max-scenarios 3 `
  --forward-mark-horizon-seconds 60 `
  --json
```

The scout loads the same replay candidate windows used by strategy
live-replay, reports scenario count and distinct event/token counts, and may
refresh selector candidate caches. It does not run strategy validation, does
not write lifecycle/economic validation rows, and cannot authorize orders.

Controllers should use this scout before full replay for sparse selectors such
as `tail_touch_forward_edge_clean`, `recent_high_inversion_disjoint`, and
`profile_group`. A full replay should be started only when scout output shows
enough distinct events to be useful for promotion-quality evidence.

## 2026-06-07 Control-Center Scout Visibility

The latest scout report is persisted under:

```text
crypto_options_app/artifacts/reports/strategy_replay_candidate_scout_latest.json
```

The control-center state now surfaces this report as:

```text
replay_candidate_cache.latest_scout
```

This field is read-only and contains the scout status, selector, candidate
count, distinct event/token counts, compact scenario previews, and live-safety
flags. A fresh ready scout can make the replay cache summary `ready` even when
the DB candidate-cache table is intentionally empty, because the controller may
choose to avoid full replay writes while monitoring sparse windows.

Promotion policy is unchanged: scout evidence is only a preflight. It can
justify running a full strict replay, but it cannot promote a strategy by
itself.

### Selector-Aware Scout Files

The scout CLI accepts multiple scenario selectors in one bounded run:

```powershell
python -m crypto_options_app.scripts.scout_crypto_options_strategy_live_replay_candidates `
  --scenario-selector tail_touch_forward_edge_clean recent_high_inversion_disjoint profile_group `
  --max-scenarios 4 `
  --json
```

For each selector, it writes a selector-specific latest file:

```text
strategy_replay_candidate_scout_<selector>_latest.json
```

The dashboard control-center payload exposes these as:

```text
replay_candidate_cache.selector_scouts
```

This lets the controller distinguish "no full replay needed because the selector
is sparse" from "the app has no replay candidates." Full replay remains useful
for learning/debugging with fewer than 12 events, but live promotion evidence
still requires the reconciled 12+ economic sample gate.

### Source-Card Readiness And Replay Eligibility

The control-center source cards must represent current replay eligibility, not
stale historical row noise:

- B Profiles readiness evaluates actionable `pre` and `live` profile rows before
  completed `post` rows.
- Fresh service status files may clear stale-row fallback summaries during
  normal live reads.
- Expired-cache fallback remains strict; stale cached DB evidence must not be
  hidden by a status-file overlay.

This keeps the replay loop from freezing on stale completed rows while still
protecting against stale-cache false readiness.

## 2026-06-07 Profile Follow V10 And Limit-Order Replay Fidelity

The replay adapter now preserves strategy intent order type when building the
shadow fill model. A `limit_buy` intent must remain a `LIMIT` replay order. It
must not be silently converted into a market order, because that would let a
maker-style strategy earn shadow evidence from taker-at-ask fills that the live
strategy did not actually request.

Promotion rule: any strategy that relies on maker/limit behavior must prove
fillability under limit semantics. Crossing limits may fill immediately.
Non-crossing limits may remain unfilled or need an explicit forward path
fillability model. They cannot be counted as if they market-bought at the ask.

The profile-follow branch now has a bounded-spread sibling:

| Strategy | Purpose | Bounded Replay | Decision |
| --- | --- | --- | --- |
| `profile_splus_hedger_follow_hold_60s_v9` | require non-negative immediate liquidation before counting forward-mark wins | 12 scenarios, 2 executions, both forward winners but both blocked by negative liquidation | review only |
| `profile_splus_hedger_follow_hold_60s_v10` | allow tiny bounded liquidation loss while capping spread drag | 12 scenarios, 2 executions, 1 promotion-economics-ready row, 1 spread-drag/liquidation blocked row | review only |

V10 result:

```text
run_id = bounded-profile-splus-v10-profilegroup-12x-20260607T020315Z
scenarios = 12
executions = 2
promotion_economics_ready = 1
```

The clean V10 row had `+$0.3780` 60s forward-mark PnL with `-$0.0140`
immediate liquidation PnL and `$0.0140` spread drag. The blocked V10 row had
`+$0.2940` forward-mark PnL but `-$0.0280` liquidation PnL and `$0.0280`
spread drag, so it correctly failed `liquidation_pnl_below_shadow_gate` and
`spread_drag_above_shadow_gate`.

Interpretation:

- V10 is useful because it separates acceptable small spread drag from
  unacceptable shadow-vs-live drift.
- V10 is not live-safe. It has only one promotion-quality economic sample, far
  below the 12+ sample and distinct-event gates.
- The next profile-follow work should add a cheap scout/filter or a V11 sibling
  that targets lower entry prices, lower top-profile concentration, and bounded
  spread drag before replay starts.
- No live trading was started from V9 or V10.

### Profile Group Quality Selector

`profile_group_quality` is now the preferred selector for promotion-oriented
V10 profile-follow scouting. It is still read-only and cannot promote a
strategy, but it prevents broad `profile_group` replay from spending lifecycle
rows on cases already known to violate cheap scalar gates.

The selector filters candidate rows by:

- matching profile subgroup direction
- latest runtime profile target, not only the matched historical snapshot
- entry price band
- spread and estimated spread drag under profile-follow adjusted sizing
- top-profile concentration
- profile source age when configured
- option-path readiness, snapshot count, rolling 60s range, and pair-sum range

Bounded result:

```text
run_id = bounded-profile-splus-v10-profilegroup-quality-clean-2x-20260607T022042Z
strategy = profile_splus_hedger_follow_hold_60s_v10
selector = profile_group_quality
scenarios = 2
executions = 2
blockers = 0
promotion_economics_ready = 2
total_forward_mark_pnl_usd = +0.6860
```

This is useful progress, not live evidence. It has only two distinct clean
economic samples. The controller should keep accumulating fresh
`profile_group_quality` rows and only consider live promotion after the full
12+ distinct-sample, positive-PnL, lifecycle, reconciliation, and signal gates
are met.
