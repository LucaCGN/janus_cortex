# Crypto Options Promotion And Demotion Policy

Updated: 2026-06-07T03:35:00Z

## Signal Promotion

Signal rows are not live-safe just because they are `PASSED`, `SELECTED`, or `STRUCTURAL_ALTERNATE`.

Valid terminal states:

- `PROMOTED`
- `REVIEW`
- `RETIRED`
- `BLOCKED`
- `NEEDS_VARIANT`

`Strict replay required` means not promotable.

## Strategy Promotion

Supervised live promotion requires all of:

- 12+ recent distinct economic samples.
- Win rate greater than 70%.
- Positive simulated PnL.
- Lifecycle coverage passed.
- Reconciliation passed.
- No strict signal blockers.
- Shadow/live-replay drift within strategy tolerance.

## Demotion

Demote or block immediately on:

- realized live loss beyond lane policy
- reconciliation mismatch
- missing lifecycle row
- large shadow/live drift
- live win-rate breach
- loss-streak breach
- negative PnL breach

## Budget Scaling

- Start minimal.
- Scale only after repeated positive reconciled live evidence.
- Descale on loss streak, negative PnL, win-rate breach, or drift.

## Safety

No manual orders. Live execution only through the supervised runtime with scoped child-process live flags and full ledger/reconciliation gates.
