# Signal Selection Frontend Checkpoint - 2026-06-05

## Scope

This checkpoint replaces the brittle inline Signal Backtest Lab HTML with a proper static frontend under the centralized app root:

`crypto_options_app/frontend`

The page remains read-only. It does not enable live orders, strategy execution, or executor imports.

## Current Signal Review Result

Current canonical DB signal state before the action-state pass:

- Total signal variants: 152
- Queue state: all 152 are `PASSED`
- Promotion state:
  - `STRUCTURAL_PASS`: 129
  - `NEEDS_V2_REVIEW`: 23
  - `PROMOTION_READY`: 0

Important interpretation:

- `PASSED` means the queue phase completed.
- `STRUCTURAL_PASS` means the signal is useful for strategy-design discussion but still has strict replay/live-data caveats.
- `PROMOTION_READY` remains empty and should stay empty until strict no-lookahead replay, random sampling, and live-shadow evidence meet promotion thresholds.

## Curated Selection API

New endpoint:

`GET /v1/crypto-options-app/signals/selection`

It hydrates validation rows with registry metadata and classifies rows into:

- `selected`: current best building blocks by signal type for strategy-design review.
- `structural`: passed structurally but lower-ranked than selected peers.
- `review`: needs revision, up to V5, or conversion into a non-directional gate.
- `discarded`: weak crypto-only directional variants removed from the primary directional candidate set.

Every row must include `next_action`, a concise instruction for the next reviewer/agent. This field is separate from strict review reasons: strict review explains why a signal is not promoted; next action says what to do next.

After the 2026-06-05 action-state pass, every row must also include:

- `action_state`: one of `STRICT_REPLAY_REQUIRED`, `STRUCTURAL_ALTERNATE`, `REVISION_REQUIRED`, `RETIRED_FROM_PRIMARY`, or `VALIDATION_REQUIRED`.
- `action_owner`: the next logical worker role, such as `strict-replay-worker`, `signal-reviewer`, or `signal-design-reviewer`.
- `next_action_detail`: longer rationale that explains why the action exists and how to handle the row.

The catalog now includes two concrete V4 revisions for the active IFCM support/resistance review gap:

- `ifcm_pivot_distance_option_reclaim_positive_return_filter`
- `ifcm_pivot_distance_grid_reference_forward_return_guard`

These force IFCM pivot distance to behave as an option-confirmed grid reference rather than a standalone directional signal.

Manual validation pass:

- Both V4 rows were run through `last_week_backtest`, `last_month_backtest`, `random_sampling_backtest`, and `live_shadow_test`.
- All 8 phase runs passed with read-only `orders_allowed=false` and `live_trading_authorized=false`.
- Current post-validation action split:
  - `STRICT_REPLAY_REQUIRED`: 73
  - `STRUCTURAL_ALTERNATE`: 58
  - `RETIRED_FROM_PRIMARY`: 22
  - `REVISION_REQUIRED`: 1
  - `VALIDATION_REQUIRED`: 0

The remaining `REVISION_REQUIRED` row is the original crypto-only `ifcm_pivot_distance_v1`, which should stay as negative/revision evidence rather than be used directly.

The selection endpoint explicitly reports:

- `orders_allowed=false`
- `live_trading_authorized=false`
- A/B/C selected coverage
- action-state counts
- selection policy and type quotas

Selection is not trading approval.

## Frontend Architecture

New files:

- `crypto_options_app/frontend/index.html`
- `crypto_options_app/frontend/assets/styles.css`
- `crypto_options_app/frontend/assets/app.js`

FastAPI mounts static assets at:

`/v1/crypto-options-app/ui/assets`

The following routes now serve the same frontend shell:

- `/v1/crypto-options-app/signals/backtests`
- `/v1/crypto-options-app/strategies/lab`

The Strategy section is a placeholder only. It exists to reserve the app architecture for the next strategy reference prompt.

## UI Requirements Met

- Dense operator layout instead of inline dashboard fragments.
- Explicit loading/error states.
- Signal table with filters for type, data block, status, selection tier, and search text.
- Signal table includes both `Strict Review` and `Next Action` columns.
- Inspector panel for selected row details, strict review reasons, validation target, and win criteria.
- Read-only design review request form.
- Strategy placeholder tab.
- Development-loop map: sources -> indicators -> signals -> strategy shadow -> shadow trading -> live promotion -> live management.
- System tab with health and selection policy payloads.

## Remaining Signal Work Before Strategy Build

- Do not treat `STRUCTURAL_PASS` as final promotion.
- For the 23 internal `NEEDS_V2_REVIEW` rows, show the user-facing label as `NEEDS REVISION` and avoid recycling weak crypto-only directional variants unless converted to avoid/confluence gates.
- Keep signal expansion bounded to V5 unless a signal type/source combination has no viable candidate.
- Before live strategy use, require stricter replay based on real A/B/C rows, not structural synthetic frames.
