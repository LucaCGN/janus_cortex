# Live Reconciliation Runtime Report - 2026-06-08

## Current Runtime State

- App-owned live order flow is enabled only when `JANUS_CRYPTO_OPTIONS_ORDERS_ALLOWED=true` and `JANUS_CRYPTO_OPTIONS_LIVE_TRADING_AUTHORIZED=true`.
- Codex/manual orders remain prohibited.
- After the latest reconciliation pass, exchange open orders are `0`, active live app positions are `0`, and active live app order count is `0`.
- Remaining live-candidate strategies are:
  - `master_hedge_grid_floor_paired_seed_builder_v5`
  - `master_hedge_grid_floor_paired_seed_builder_v6`
  - `profile_splus_outcome_prediction_confluence_v1`
- The latest bounded queue passes processed those candidates, but they did not submit new orders because current market/profile/path conditions failed strategy-defined entry gates.

## Last Candidate Outcomes

| Strategy | State | Latest queue result | Meaning |
| --- | --- | --- | --- |
| `option_tail_reversal_hold_60s_v3` | moved out of active live after reconciliation | submitted entry and paired sell; paired sell was correctly priced above basis, then expired without fill | paired-exit price fix worked, but strategy did not realize profit in that event |
| `master_hedge_grid_floor_paired_seed_builder_v6` | `LIVE_CANDIDATE` | blocked on low grid viability, low inversion intensity, low range, low crossings, low rebounds, missing path density, and low entry ask gap | valid no-trade under current market conditions |
| `profile_splus_outcome_prediction_confluence_v1` | `LIVE_CANDIDATE` | blocked on top-profile concentration too high | valid no-trade under current profile distribution conditions |
| `master_hedge_grid_floor_paired_seed_builder_v5` | `LIVE_CANDIDATE` | blocked on low inversion/range/crossing/rebound/path-density conditions | valid no-trade under current market conditions |

## Adjustments Completed

### Paired Exit Pricing

The live paired-exit calculation now uses concrete effective entry basis before falling back to scenario targets:

- realized fill price
- position cost divided by shares
- raw fill payload
- live executor order-request price
- submitted/pre-JIT/realized execution quality prices
- estimated total cost divided by size

The previous failure mode allowed a sell to be placed at the same effective level as the buy or at an unrealistic scenario target such as `0.99`. The current test case verifies a buy near `0.5325` produces a paired sell near `0.5425`, not `0.51` and not `0.99`.

### Underpriced Paired-Exit Safety

Account reconciliation now checks app-created paired exit orders that are still open on the exchange. If the sell price is below effective entry basis plus the minimum intended profit, the app cancels the order and records a ledger blocker.

This protects against:

- same-price paired sells
- stale paired-exit prices after fill-quality drift
- entry fills whose all-in cost is higher than the intended sell level

### Pending Order Reconciliation

The queue worker and standalone reconciliation script now run paired-exit safety before normal pending-order reconciliation. If an order is no longer present in open orders and there is no matching trade fill, the local order is marked expired/cancelled without fill.

### Loss Gate Enforcement

Strategy promotion now demotes when cumulative app-live realized PnL is less than or equal to the strategy max-loss budget. This fixes the exact edge where a strategy at `-$10.00` could avoid demotion if the comparison was strict `< -10`.

Several previously poor live performers are now demoted or review-blocked from the promotion layer.

## Trading Engine Premises

### Order Ownership

- Codex may create/review strategy and signal scripts.
- Codex must not place manual orders.
- Strategy scripts define promotion, demotion, scaling, budgets, max loss, win-rate, PnL, lifecycle, reconciliation, and drift criteria.
- The app runtime owns queue execution, live submission, paired-exit placement, reconciliation, and stop gates.

### Entry Orders

- Entry orders are strategy-generated and submitted through the executor boundary.
- Live entry fills must record realized fill price, share count, exchange order id, latency, submitted/pre-JIT price, and effective cost.
- Entry cost must be treated as the actual all-in basis for later paired-exit validation.

### Paired Exits

- Every relevant live buy must produce a paired sell or an explicit strategy-managed alternative.
- Cashout-watch is not sufficient for scalping/grid lanes because latency makes reactive exits unreliable.
- Paired exits must be placed above effective basis plus strategy target profit.
- Underpriced paired exits are unsafe and must be cancelled or replaced.

### Loss Handling

- Strategy budget is not the same as max allowed loss.
- Max allowed loss is strategy-defined and currently should be treated as around `$10` for validation candidates unless a strategy sets a stricter value.
- Negative resolved PnL, loss streaks, reconciliation mismatch, hard stops, lifecycle mismatch, and shadow/live drift must demote or block according to strategy policy.

### Redeems And Settlement

- Winning Polymarket positions may redeem automatically.
- Losing positions can remain visible as zero-value positions until redeemed/closed.
- Reconciliation must not infer PnL only from visible position rows; it must combine trade history, closed/open positions, event resolution, local order/fill rows, and ledger state.
- Local zero-value losing positions should be closed in app state when event resolution confirms loss.

### Price Tracking

- The effective buy price is not always the requested limit price.
- The executor records submitted price, pre-JIT price, realized price, filled notional, filled shares, quote-to-submit latency, and remote/order payloads.
- Backtest/replay must score fills using latency/slippage/fillability assumptions that are harsher than raw path midpoint fills.

## Points Still Requiring Validation

1. **Live paired-exit replacement path**
   - We verified underpriced cancellation and correct fresh paired-exit pricing.
   - We have not yet verified an actual profitable paired exit fill after the final pricing fix.

2. **Loss streak accounting**
   - Cumulative loss demotion works.
   - Recent live loss streak should still be audited for every strategy because aggregate positive PnL can mask recent bad runs.

3. **Grid strategy runtime**
   - The paired seed builders correctly refused low-volatility/low-inversion events.
   - The desired high-frequency both-side grid with 2c/3c/4c levels still needs a dedicated runtime test once paired-exit replacement is stable.

4. **Open exchange order disappearance**
   - Latest paired sell disappeared from open orders without matching trade fill and was expired locally.
   - This can be normal expiry/cancel behavior near event end, but it must remain visible in reports because it directly affects realized PnL.

5. **Default live flags in tests**
   - Promotion tests pass under the actual live-runtime environment variables.
   - Without those env vars, one promotion-manager assertion expects live flags true while default config reports false. This is an environment-contract issue, not a paired-exit failure.

## Verification

- `13 passed` for paired-exit pricing, underpriced paired-exit cancellation, and queue worker tests.
- `22 passed` for promotion-manager tests under live-runtime env flags.
- Standalone account reconciliation completed with:
  - open orders fetched: `0`
  - pending orders checked: `1`
  - expired/cancelled without fill: `1`
  - closed local positions: `1`
  - manual orders avoided: `true`

## Next Safe Runtime Step

Do not force trades while all live candidates are failing their own entry gates. Continue bounded queue passes with:

- max live slots: `3`
- max parallel live candidates: `1` until a fresh paired exit fills profitably
- validation budget cap: `$10` at worker boundary
- strategy-level budget and order sizing preserved

Only widen to three simultaneous active live lanes after one fresh entry plus paired-exit cycle is reconciled correctly under the final pricing fix.
