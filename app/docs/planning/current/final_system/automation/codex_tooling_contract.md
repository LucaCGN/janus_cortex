# Janus Codex Tooling Contract

Status: active design contract
Created: 2026-05-18
GitHub issue: https://github.com/LucaCGN/janus_cortex/issues/53

## Purpose

Define the tool boundary between Codex, Janus, and direct Polymarket execution surfaces.

The current `codex_tool/` package is useful, but it is mostly a thin Codex-to-Janus API wrapper layer. The portfolio-manager automation and live-monitor fallback need a second class of tools: direct Polymarket account/CLOB tools that can keep operating when the Janus API/runtime is degraded.

## Current State

- `codex_tool/*` exists and is imported by current tests and reports.
- Existing `codex_tool` scripts should be treated as compatibility entrypoints until `#53` migrates them safely.
- Most current `codex_tool` scripts call Janus API endpoints on `http://127.0.0.1:8010`.
- The `codex_tools/janus/` and `codex_tools/polymarket/` base namespaces now exist with tests, preview-first direct Polymarket safety gates, read-only account snapshot helpers, inert fallback ledger writes, 1c grid candidate planning, gated service-spawn planning, required portfolio-manager action planning, and selected Janus compatibility migrations.
- `tools/polymarket_smoke_order.py` is not an automation path and must remain retired from controller/portfolio-manager use.

Do not rename or delete `codex_tool/` until compatibility tests prove the existing imports and automation prompts still work.

## Target Split

The target package namespace is `codex_tools/`, with two primary subpackages:

| Package | Role | Order Authority |
|---|---|---|
| `codex_tools/janus/` | Janus-facing status, data refresh, integrity, live monitor, StrategyPlanJSON, worker, ledger, and reconciliation wrappers. | Uses Janus API/order-manager gates only. |
| `codex_tools/polymarket/` | Direct Polymarket account, CLOB, orderbook, positions, orders, fills, Janus-mediated one-shot portfolio orders, and emergency service loops for portfolio-manager/live-monitor fallback. | One-shot portfolio orders route through the approved Janus order-management endpoint; independent direct execution may execute only under the independent Polymarket execution gate below. |

`codex_tool/` may remain as a compatibility shim that imports from `codex_tools/janus/` after migration.

## Independent Polymarket Execution Gate

Direct Polymarket tools are intended for cases where Janus API/runtime is unavailable, stale, or unsafe, but the operator still needs portfolio management or live-monitor protection.

They may place, cancel, replace, close, or open positions only when all gates are true:

1. Fresh direct CLOB/account truth resolves collateral, position, open-order, fill, market, token, side, price, size, and tick-size state.
2. Janus API/runtime degradation is explicitly recorded, or the portfolio-manager contract explicitly chooses the direct Polymarket path.
3. A separate global-portfolio or live-monitor risk budget is selected before the action.
4. Polymarket minimum-size/minimum-notional and market-order exception policy pass.
5. A concrete target/stop/rebuy policy is present for the action, including policy name, target logic or target price when applicable, stop handling, rebuy handling, and strategy reason.
6. Kill-switch status is checked and permissive.
7. A local durable ledger entry with idempotency key is written before submission and finalized after direct CLOB confirmation.
8. The action records strategy reason, target/stop/rebuy policy, source evidence, order ids, and reconciliation plan.
9. The action is later reconciled back into Janus DB/API once Janus is healthy.
10. The tool defaults to preview/dry-run unless an explicit approved execution flag/config is present.

If any gate is missing, the tool must return a management plan or blocker and must not prepare, sign, submit, cancel, or replace an order.

## Resolved-Market Redemption Gate

Resolved-market redemption is separate from the CLOB order path. It claims collateral from a resolved conditional-token market; it is not a market buy, sell, close, cancel, or replace action.

`codex_tools/polymarket/` may add a redeem preview and, later, a gated execution path only under [#58](https://github.com/LucaCGN/janus_cortex/issues/58). Until that implementation is complete, tools must treat redemption as preview/blocker state only.

A non-dry-run redeem path must require all of these gates:

1. Fresh direct account truth proves the position still exists and is not a stale mirror.
2. Direct market/token truth resolves condition id, token id, outcome index, resolved outcome, payout status, expected proceeds, and zero-value losing cases.
3. Direct open-order truth proves there are no event-scoped open orders or fill ambiguities for the same market.
4. Wallet, chain, signer, gas/fee, and collateral readiness are explicitly checked.
5. Kill-switch status is checked and permissive.
6. A local durable settlement ledger row is prewritten with an idempotency key, expected payout, source evidence, and post-redeem reconciliation plan.
7. Explicit Janus+Codex operator approval config is true for redemption. This approval is a system gate, not an ad hoc human chat instruction.
8. Post-redeem direct account/CLOB reconciliation updates Janus and the local ledger before the residual is treated as cleared.

If any gate is missing, the tool must return a redeem preview or blocker and must not prepare, sign, submit, or broadcast a redemption transaction.

## Portfolio Action And Grid-Service Tools

The first approved grid surface is `codex_tools/polymarket preview-grid-service`. It reads a direct account snapshot and produces inert candidates for one-cent sell/rebuy grids on ongoing positions that have meaningful movement, including global categories and basketball markets outside the covered NBA/WNBA Janus modules.

The approved active-manager planning surface is `codex_tools/polymarket plan-manager-action`. It reads direct account truth plus optional frontend catalog/profile-study observations and forces one of these per-run outcomes:

- manage an existing position with a target/close/hold/rebuy/grid decision
- select a new frontend/profile-informed market for a bounded micro-position candidate
- record the exact blocker that prevented a required action

Profile-study observations may include structured `recent_trades` and `active_positions` rows from public winning-profile pages. The planner must normalize those rows into `winning_profile_recent_trade` and `winning_profile_active_position` candidates so the automation explicitly considers mimicking the newest studied-profile trade or position. These candidates are discovery inputs only until mapped to fresh direct CLOB/token/orderbook truth and passed through the same risk, ledger, kill-switch, approval, and reconciliation gates as any other one-shot order.

The action planner is not an order endpoint. It must return `order_preparation_attempted=false` and `order_submission_attempted=false`; it exists so the automation cannot hide behind passive monitoring when an action candidate exists.

The approved one-shot portfolio order surface is `codex_tools/polymarket portfolio-manager-order`. It calls Janus `POST /v1/portfolio/manager/order-management` with an action plan, requested order, Janus portfolio account UUID, and optional non-dry-run execution approval. Dry-run mode is the default in rehearsal. With `--execute --execution-approved --reviewed-by <persona> --reason <reason>`, the tool may place a limit buy/sell only if the running Janus API accepts every server-side gate: runtime flag, kill switch, concrete proof bundle, risk/rate limits, DB market/outcome mapping, ledger/idempotency, and post-call reconciliation. The account id must be `portfolio.trading_accounts.account_id`, not a Polymarket wallet/proxy address. This is the normal path for one-shot open/close/target/rebuy portfolio-manager actions.

The approved service-spawn proof surface is `codex_tools/polymarket plan-grid-service-spawn`. It may return `service_spawn_authorized=true` only when an explicit non-dry-run service-spawn intent and all service gates are present. Starting a service is still not order authority; every service leg must separately prove the independent execution gate before any order preparation or submission.

Grid tooling must require:

- service-spawn approval and owner persona
- named global-portfolio grid budget
- per-market and aggregate max notional
- maximum concurrent legs and rate limits
- direct-CLOB freshness requirement before every leg
- kill-switch polling and shutdown behavior
- idempotent ledger write before each action and CLOB confirmation after each action
- Janus reconciliation artifacts for every fill, cancel, replace, target, and rebuy

High-frequency services must be supervised as independent running services with their own heartbeat, lock, ledger, and kill switch. They must not be hidden inside a recurring Codex prompt pass.

## Persona Use

- `codex-global-portfolio-agent` may use `codex_tools/polymarket/` for approved portfolio management when Janus is degraded or when the portfolio-manager contract selects the direct path. It must use `plan-manager-action` or equivalent before reporting a passive portfolio no-op. The older `global-portfolio-agent` name is only a compatibility alias for this Codex global portfolio persona.
- `janus-covered-market-portfolio-agent` uses `codex_tools/janus/` and Janus API/order-manager gates for covered markets such as NBA/WNBA. It must not use direct Polymarket fallback tools for speculative uncovered-market scouting.
- `live-monitor-analyst` may use `codex_tools/polymarket/` only for urgent protect/close/cancel/replace actions during a Janus runtime break, and only under the independent Polymarket execution gate.
- `master-controller` may inspect tool availability, create issues, route blockers, and no-op. It must not execute orders itself.
- `development-agent` owns implementation under `#53`.
- Closed `#56` completed active-manager action planning, frontend/profile discovery enforcement, and gated grid/scalping service spawn hardening after the `#53` foundation. New tooling drift or expansion needs a focused follow-up issue.

## Implementation Acceptance

`#53` base acceptance is complete when:

- `codex_tools/janus/` and `codex_tools/polymarket/` package skeletons exist.
- Existing `codex_tool` imports are preserved.
- Janus-facing wrappers and direct Polymarket tools are separated in tests and docs.
- Direct Polymarket execution defaults to read-only/dry-run.
- Tests cover blocked execution without direct truth, without risk budget, without ledger/idempotency, without kill-switch clearance, and without explicit execution approval.

Concrete non-dry-run direct execution is a separate implementation issue after base tooling acceptance. It must not be smuggled into `#53` as a residual comment thread.
