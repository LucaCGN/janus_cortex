# Crypto Options Promotion And Demotion Policy

Updated: 2026-06-08T00:30:00Z

Runtime checkpoint: 2026-06-08T07:55:00Z

## Signal Promotion

Signal rows are not live-safe just because they are `PASSED`, `SELECTED`, or `STRUCTURAL_ALTERNATE`.

Valid terminal states:

- `PROMOTED`
- `REVIEW`
- `RETIRED`
- `BLOCKED`
- `NEEDS_VARIANT`

`Strict replay required` means not promotable.

Machine-readable contract: strategy promotion summaries expose
`policy_contract.schema_version = crypto_options_promotion_policy_contract_v1`.
Fixed chats and frontend views should render that contract rather than
recreating promotion rules from prose.

## Strategy Promotion

Every new strategy should be able to progress to live trading if it survives
its own strategy-defined criteria and the app runtime gates. Codex defines or
reviews strategy-specific criteria in the strategy spec/script; the app
promotion manager executes those criteria. Codex does not manually choose
promotion, demotion, scaling, or live entry.

Strategy-specific criteria may intentionally trade off win rate against PnL and
loss controls. For example, a 50-70% win-rate strategy can be eligible only if
its spec defines stronger positive-PnL, max-loss, stop-gate, lifecycle,
reconciliation, and drift requirements.

Default live promotion requires all of:

- 12+ recent distinct economic samples.
- Win rate greater than 70%; exactly `70.0%` remains `SHADOW_REVIEW`.
- Positive simulated PnL.
- Lifecycle coverage passed.
- Reconciliation passed.
- No strict signal blockers.
- Shadow/live-replay drift within strategy tolerance.

The global thresholds are defaults. Strategy specs may override supported
promotion fields through `metadata.promotion_policy`,
`metadata.promotion_criteria`, `risk_gates.promotion_policy`, or
`live_pulse_requirements.promotion_policy`. Overrides must make the strategy
more explicit, not bypass lifecycle, reconciliation, executor, budget, stop, or
drift gates.

`LIVE_CANDIDATE` is an executable app state. The queue worker runs the
live-candidate phase automatically through strategy criteria, executor,
budget/risk, lifecycle, reconciliation, stop, and demotion gates. If those
runtime gates fail, the strategy is blocked or marked for review; it is not
held for Codex/user approval.

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

- Budget is strategy-owned. A strategy spec should declare its live budget cap,
  target order notional, minimum order notional, minimum share size, scaling
  rules, and demotion rules.
- Max allowed realized live PnL loss is a separate strategy policy field from
  budget. Current live-validation policy defaults every strategy to a `10.0`
  USD loss stop. Some legacy metadata keys still use
  `max_supervised_live_loss_usd`; treat that as a compatibility field name for
  the live loss stop, not as a human approval model.
- The app may enforce a global validation guardrail across a batch, but that
  guardrail is not the per-strategy sizing policy.
- Strategies that need paired limit exits must buy enough shares to make the
  paired sell practical. Current profile-follow live validation lanes use a
  minimum of `5` shares and an `8.0` USD target notional under a `30.0` USD
  strategy cap.
- Scale only after repeated positive reconciled live evidence.
- Descale on loss streak, negative PnL, win-rate breach, or drift.

## Active Live Orders

A submitted live order that is still active but unfilled is live lane activity.
It must not be marked `REVIEW_BLOCKED` only because no filled-position lifecycle
row exists yet. Once that order fills, cancels, expires, or otherwise resolves,
the normal lifecycle, reconciliation, PnL, loss-streak, drift, and demotion
checks apply.

## Live Slot Policy

- Current validation mode allows at most `3` active strategy lanes in live trading.
- Remaining eligible strategies stay in shadow/replay and queued
  `LIVE_CANDIDATE` state until a live slot opens. A live-slot overflow requeues
  `recent_shadow_sample` so the waiting strategy keeps proving or disqualifying
  itself instead of sitting idle.
- If a shadow strategy records negative PnL or breaches its strategy-defined
  win-rate/loss-streak/PnL criteria, it is marked `SHADOW_REVIEW` and should not
  advance to live.
- If a shadow strategy outperforms an active live strategy, the app should only
  switch lanes after the weaker live lane is demoted/blocked/resolved by
  strategy-defined live PnL, lifecycle, reconciliation, stop, or drift gates.
  The freed slot is then filled by the strongest queued `LIVE_CANDIDATE` through
  the queue worker.

## Technical Issue Handoff

If a strategy or signal fails its own criteria, it returns to review/shadow or
is retired. If the feature that should evaluate the criteria fails, such as the
backtest engine, replay engine, live-shadow engine, promotion gate,
reconciliation, lifecycle audit, queue ownership, or frontend/API contract, the
lane must stop patching that subsystem and hand it off through:

- a GitHub issue/comment when available
- `crypto_options_app/artifacts/team_coordination/technical_issue_handoff_log.md`
- an `OPEN` item in `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`

The master/hourly lane owns those technical handoffs.

## Safety

No manual orders by Codex. App-level live/order flags may be enabled for
policy-gated automated testing. Live execution only through the promotion
manager, executor boundary, budget/risk gates, lifecycle, reconciliation, stop
gates, and demotion policy.
