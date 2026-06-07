# Strategy Promotion Pipeline Checkpoint

Date: 2026-06-05

## Scope

This checkpoint connects the end-to-end development flow in the app:

1. Sources: event, profile, and crypto data services.
2. Indicators: derived A/B/C stats and distributions.
3. Signals: backtested, strict-reviewed, and selected building blocks.
4. Strategies: historical replay and live-shadow validation.
5. Promotion: automatic state labels from replay, signal, lifecycle, reconciliation, PnL, and budget evidence.
6. Supervised live: still only allowed through scoped supervised runtime gates.

The promotion layer is not a chat order path and not a UI order path.

## Implemented

- Added `GET /v1/crypto-options-app/strategies/promotion`.
- The endpoint evaluates and persists `strategy_promotion_state` rows.
- The frontend Strategy tab now renders a promotion matrix with:
  - Strategy id and version.
  - Promotion state.
  - Historical replay pass count.
  - Live-shadow replay pass count.
  - Supervised-live proof count.
  - Blockers.
  - Next action.
- The flow summary now reports shadow-ready and live-candidate counts.
- The promotion policy includes demotion from supervised live back to shadow when live ledger evidence is losing.
- The promotion policy now distinguishes structural live-shadow evidence from economic live-shadow evidence:
  - A strategy cannot become promotion-clean from a structural-only shadow pass.
  - Shadow/live replay must record simulated PnL evidence before the row can be treated as promotion-quality.
  - Positive shadow economics followed by negative supervised-live PnL creates a drift/slippage review blocker.

## Promotion States

| State | Meaning |
| --- | --- |
| `NEEDS_BACKTEST` | Strategy has not passed historical replay and live-shadow replay. |
| `BACKTEST_READY` | Historical replay passed; live-shadow replay is still required. |
| `SHADOW_READY` | Historical replay and live-shadow replay passed, but live promotion blockers remain. |
| `LIVE_CANDIDATE` | Replay, signal, lifecycle, reconciliation, and policy gates are clean enough for a supervised live child proposal. |
| `LIVE_RUNNING` | Supervised live proof exists and no blockers remain. |
| `DEMOTED_TO_SHADOW` | Supervised live evidence shows negative realized PnL; strategy must return to shadow/review. |
| `REVIEW_BLOCKED` | Mechanical, lifecycle, reconciliation, hard-stop, or prior live blocker requires review. |

## Current Central DB State

Evaluated against `crypto_options_app/data/crypto_options_data.sqlite`:

| Metric | Value |
| --- | ---: |
| Strategy promotion states | `NEEDS_BACKTEST`: 15, `SHADOW_REVIEW`: 4, `REVIEW_BLOCKED`: 1 |
| Strategy live-shadow replay rows | 10 total, 10 lifecycle pass, 10 reconciled |
| Promotion-ready signals | 69 |
| Signal revision debt | 100 `NEEDS_V2_REVIEW` |
| Signal count | 169 |
| Orders allowed | false |
| Live trading authorized | false |

The five `SHADOW_READY` strategies are:

- `profile_hedge_scalping_v1`
- `event_context_profile_confirmed_v1`
- `hedger_ratio_replication_v4`
- `profile_hedge_scalping_v4`
- `grid_band_rebound_v3`

The previous `no_promotion_ready_signals` blocker has been resolved by the V5 building-block promotion policy. The active blockers have shifted to strategy-side proof:

- The existing strategy roster must be rebuilt or revalidated against the 69 promoted signal building blocks.
- Fifteen registered strategies still need backtest.
- Four strategies have shadow evidence but require review rather than live promotion.
- One strategy remains review-blocked.
- Any live candidate still needs recent 1h/12-economic-sample shadow proof with win rate above 70%, positive simulated PnL, lifecycle audit, reconciliation, and no strict signal blockers.

The promoted signal base increased again to 69 after completing the V5 queue. The strategy backlog remains unchanged until those building blocks are wired into historical replay and live-shadow strategy evidence.

After the promotion layer was added, the selected five were refreshed through one additional read-only historical replay and one additional read-only live-shadow replay:

- `strategy-backtest-replay-promotion-refresh-20260605`
- `strategy-live-replay-promotion-refresh-20260605`

Each of the five selected strategies now has two historical replay passes and two live-shadow replay passes, but zero live-candidate promotions.

## Live Budget Rule

The user-assigned `$100` budget is treated as a supervised validation budget cap for promotion proposals, not as a chat/manual order authorization. Promotion can label a strategy `LIVE_CANDIDATE`, but actual live orders remain restricted to the supervised runtime with scoped child-process live flags, ledger gates, risk gates, reconciliation, and stop gates.

## Shadow Economics Rule

Live-shadow strategy runs are split into two classes:

- Structural shadow proof: candidate, intent, fill simulation, lifecycle, and reconciliation rows exist.
- Economic shadow proof: the same run also records simulated PnL or equivalent strategy economics in `strategy_validation_runs.evidence_json`.

Only economic shadow proof can support live promotion. If live-shadow rows pass structurally but do not include PnL evidence, promotion adds `missing_shadow_live_economic_evidence`. If the shadow PnL is non-positive, promotion adds `shadow_live_non_positive_pnl`. If the shadow win rate is below the policy floor, promotion adds `shadow_live_win_rate_below_floor`.

If a supervised-live run loses money after positive shadow economics, promotion adds `live_loss_after_positive_shadow_requires_review`. If the live-vs-shadow PnL gap exceeds the policy threshold, promotion also adds `live_shadow_actual_drift_exceeds_limit`, which should be treated as a slippage, fill simulation, or live execution modeling bug until proven otherwise.

## Demotion Rule

If supervised-live ledger evidence reports negative realized PnL for a strategy, the promotion manager marks it `DEMOTED_TO_SHADOW` and requires shadow replay/review before another live attempt.

If supervised-live evidence reports a mechanical blocker, hard stop, failed lifecycle coverage, or reconciliation mismatch, the promotion manager marks it `REVIEW_BLOCKED`.

## Verified Tests

Command:

```powershell
python -m pytest tests\crypto_options_app -q
```

Result:

```text
211 passed
```

Focused coverage added:

- Strategy promotion blocks live until historical replay, live replay, and signal gates are clean.
- Replay-clean strategies become `SHADOW_READY`, not live candidates, while signal promotion is blocked.
- Structural-only live-shadow replay now creates `missing_shadow_live_economic_evidence`.
- Losing supervised-live ledger evidence demotes a strategy to `DEMOTED_TO_SHADOW`.
- Positive shadow economics followed by negative supervised-live PnL creates explicit drift/slippage review blockers.
- Strategy lab route exposes the promotion endpoint and updated UI.

## Next Required Work

Before any automatic live promotion can start:

1. Promote at least one signal to `PROMOTION_READY` through strict replay and live-shadow criteria.
2. Reduce selected strict replay debt for the signals used by candidate strategies.
3. Run true captured-path strategy replay, not only fixture/structural replay.
4. Require trusted balance source before any live budget scaling.
5. Add an execution automation that only starts supervised live child processes for `LIVE_CANDIDATE` rows and immediately demotes or blocks on PnL, lifecycle, reconciliation, or stop-gate failures.
