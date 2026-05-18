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
- The `codex_tools/janus/` and `codex_tools/polymarket/` base namespaces now exist with tests, preview-first direct Polymarket safety gates, read-only account snapshot helpers, inert fallback ledger writes, and selected Janus compatibility migrations.
- `tools/polymarket_smoke_order.py` is not an automation path and must remain retired from controller/portfolio-manager use.

Do not rename or delete `codex_tool/` until compatibility tests prove the existing imports and automation prompts still work.

## Target Split

The target package namespace is `codex_tools/`, with two primary subpackages:

| Package | Role | Order Authority |
|---|---|---|
| `codex_tools/janus/` | Janus-facing status, data refresh, integrity, live monitor, StrategyPlanJSON, worker, ledger, and reconciliation wrappers. | Uses Janus API/order-manager gates only. |
| `codex_tools/polymarket/` | Direct Polymarket account, CLOB, orderbook, positions, orders, fills, one-shot execution, and emergency service loops for portfolio-manager/live-monitor fallback. | May execute only under the independent Polymarket execution gate below. |

`codex_tool/` may remain as a compatibility shim that imports from `codex_tools/janus/` after migration.

## Independent Polymarket Execution Gate

Direct Polymarket tools are intended for cases where Janus API/runtime is unavailable, stale, or unsafe, but the operator still needs portfolio management or live-monitor protection.

They may place, cancel, replace, close, or open positions only when all gates are true:

1. Fresh direct CLOB/account truth resolves collateral, position, open-order, fill, market, token, side, price, size, and tick-size state.
2. Janus API/runtime degradation is explicitly recorded, or the portfolio-manager contract explicitly chooses the direct Polymarket path.
3. A separate global-portfolio or live-monitor risk budget is selected before the action.
4. Polymarket minimum-size/minimum-notional and market-order exception policy pass.
5. Kill-switch status is checked and permissive.
6. A local durable ledger entry with idempotency key is written before submission and finalized after direct CLOB confirmation.
7. The action records strategy reason, target/stop/rebuy policy, source evidence, order ids, and reconciliation plan.
8. The action is later reconciled back into Janus DB/API once Janus is healthy.
9. The tool defaults to preview/dry-run unless an explicit approved execution flag/config is present.

If any gate is missing, the tool must return a management plan or blocker and must not prepare, sign, submit, cancel, or replace an order.

## Persona Use

- `codex-global-portfolio-agent` may use `codex_tools/polymarket/` for approved portfolio management when Janus is degraded or when the portfolio-manager contract selects the direct path. The older `global-portfolio-agent` name is only a compatibility alias for this Codex global portfolio persona.
- `janus-covered-market-portfolio-agent` uses `codex_tools/janus/` and Janus API/order-manager gates for covered markets such as NBA/WNBA. It must not use direct Polymarket fallback tools for speculative uncovered-market scouting.
- `live-monitor-analyst` may use `codex_tools/polymarket/` only for urgent protect/close/cancel/replace actions during a Janus runtime break, and only under the independent Polymarket execution gate.
- `master-controller` may inspect tool availability, create issues, route blockers, and no-op. It must not execute orders itself.
- `development-agent` owns implementation under `#53`.

## Implementation Acceptance

`#53` base acceptance is complete when:

- `codex_tools/janus/` and `codex_tools/polymarket/` package skeletons exist.
- Existing `codex_tool` imports are preserved.
- Janus-facing wrappers and direct Polymarket tools are separated in tests and docs.
- Direct Polymarket execution defaults to read-only/dry-run.
- Tests cover blocked execution without direct truth, without risk budget, without ledger/idempotency, without kill-switch clearance, and without explicit execution approval.

Concrete non-dry-run direct execution is a separate implementation issue after base tooling acceptance. It must not be smuggled into `#53` as a residual comment thread.
