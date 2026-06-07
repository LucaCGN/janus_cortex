# Crypto Options App Current State Inventory

Date: 2026-06-02

Status: technical spec for the `crypto_options_app` rebuild.

## Purpose

This inventory maps the current crypto-options implementation so the new root app can reuse proven pieces without carrying forward the unstructured V1/V2/V3/V4 service shape.

The target app lives at repo root under `crypto_options_app`. Existing crypto code remains legacy input until migrated, wrapped, replaced, or archived.

## Current Component Map

| Area | Current location | Classification | Target action |
| --- | --- | --- | --- |
| FastAPI routers | `app/api/routers/crypto_options_signals.py`, `app/api/routers/crypto_options_market_data.py` | wrap then replace | Keep routes read-only while new app API is built. Move stable endpoint contracts into `crypto_options_app/api`. |
| Profile store and grading | `app/data/pipelines/crypto/options/profile_store.py`, `profile_signals.py`, `profile_fetch_service.py` | migrate | Move schema, async fetch, grading, and generator scoring into `crypto_options_app/profiles` and canonical DB. |
| Market/event data | `app/data/pipelines/crypto/options/market_data_store.py`, `price_stream_service.py`, `polymarket_event_price_service.py`, `indicators.py` | migrate | Move feed workers and market schema into `crypto_options_app/feeds`, `indicators`, and DB modules. |
| Replay/reports | `backtests.py`, `statistical_components.py`, `statistical_signals.py`, `price_path_trace.py`, `reporting.py`, `candidate_report.py` | replace with DB-backed replay | Keep algorithms as references; implement durable replay frames and runs in the new app. |
| V2/V3/V4 live service | `codex_tool/run_crypto_options_v2_service.py` and related supervisor scripts | archive after replacement | Do not extend. Use only as evidence for lifecycle and reconciliation requirements. |
| Live micro executor | `codex_tool/run_crypto_options_live_micro_executor.py` | wrap only behind supervised runtime | Keep safety lessons; new engine must own intent/order/fill lifecycle and call executor only through gates. |
| Observer dashboard | `tools/crypto_options_v3_observer_app.py` | replace | New app reports and API should make dashboard state DB-backed. |
| Research strategy catalog | `app/services/crypto_options/strategy_catalog.py` | archive/reconcile | Supersede with the 10-candidate strategy registry in the new app. |
| Existing docs | `crypto_options_app/docs/reference/crypto_options/*.md` | source of truth | `app/docs/reference/crypto_options` is retained only as a pointer to the centralized docs. |

## Data Shards And Artifacts

Current legacy shards:

- `local/shared/artifacts/crypto-options-research/profile-store/crypto_options_profiles.sqlite`
- `local/shared/artifacts/crypto-options-research/market-data/crypto_market_data.sqlite`

Target:

- one canonical DB owned by the new app, defaulting to `crypto_options_app/data/crypto_options_data.sqlite`.

Current artifacts:

- service run roots under `local/shared/artifacts/crypto-options-research` and `local/co4`
- profile monitor JSON artifacts
- supervisor packet artifacts
- execution ledgers
- price-path traces and reports

New app rule:

- artifacts may exist for reports, but durable operational truth must live in the DB.

## Failure Modes To Preserve As Requirements

Known failures that must become technical requirements:

- V3 copied random single-side hedger/grid rows and produced directional losses.
- Hedger/grid accounts require aggregate event-level inventory reconstruction.
- Profile style metadata was lost across cache refresh and blocked valid routing.
- Monitor refresh and artifact path length failures caused stale/no-candidate phases.
- Candidate packets were sometimes ready without a current eligible executable source.
- SELL lifecycle coverage and duplicate exit prevention must be explicit.
- Submitted order does not equal filled position; reconciliation is mandatory.
- Buckets alone are not signal confidence. Event context, profile type, and executable quote state must drive candidate validity.
- Recent activity is a usage gate, not a grade component.

## Boundary Decisions

- Existing live scripts are not the new architecture.
- The new app starts as a modular FastAPI app in one repo, not a scattered CLI-first system.
- Data feeds and APIs are read-only with respect to trading.
- Strategy code emits intents; only the supervised trading runtime can submit orders.
- Issue #47 remains research history. A new implementation parent issue owns the app rebuild.

## Acceptance Criteria

- Every legacy component has an explicit target action.
- Every known loss/mechanical failure maps to a design requirement.
- No legacy live service is treated as the long-term runtime.
- New app boundaries are clear enough to drive folder, DB, API, and engine specs.
