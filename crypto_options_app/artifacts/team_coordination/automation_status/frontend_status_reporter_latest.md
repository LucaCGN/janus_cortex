# Crypto Options Frontend Status Reporter Latest

Generated: `2026-06-07T07:43:30Z`

## Endpoints checked

Canonical backend API: `http://127.0.0.1:8011/v1/crypto-options-app`

| Endpoint | Status | Latency | Notes |
| --- | ---: | ---: | --- |
| `/v1/crypto-options-app` | `200` | `90.63ms` | Backend-rendered control-center page is reachable. |
| `/v1/crypto-options-app/health` | `200` | `3.76ms` | Runtime remains reachable; health status is still degraded at the app level. |
| `/v1/crypto-options-app/dashboard/control-center-state` | `200` | `48.95ms` | Large payload around `1.0MB`; frontend should avoid unnecessary refetch churn. |
| `/v1/crypto-options-app/signals/validation/status` | `200` | `1433.93ms` | Slow but bounded; signal page should render loading/error states clearly. |
| `/v1/crypto-options-app/strategies/promotion` | `200` | `33.07ms` | Promotion contract is available through the backend payload. |
| `http://127.0.0.1:8012/` | `ERR` | `2023.36ms` | Separate frontend service is not currently listening. |

## Frontend blockers

- Separate frontend service on `8012` is offline. The backend-rendered UI on `8011` is currently the reachable frontend surface.
- Signal validation status is materially slower than the other checked endpoints and should keep visible loading/error/empty states.
- Control-center state payload is large enough that the frontend should use stable polling intervals and avoid duplicate full-state fetches.
- Frontend fixed chat should keep work scoped to UI/API contracts and should not change DB, promotion, replay, or trading runtime logic.

## Proposed next UI slice

- Start the Frontend Control Center fixed chat from `crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md`.
- First implementation slice should build a contract/page map for `Sources -> Indicators -> Signals -> Signal backtests -> Strategy backtests -> Shadow/live-replay -> Live -> Portfolio`.
- Treat `8011` as the canonical working target until a separate React/frontend service is intentionally started or reintroduced.

## Files changed

- `crypto_options_app/artifacts/team_coordination/automation_status/frontend_status_reporter_latest.md`

## Tests run

- Lightweight HTTP endpoint checks only; no frontend code changed.

## Live activity status

- No live trading authorized or attempted.
- No DB/storage, promotion, replay, strategy, executor, or trading runtime changes were made.

## Manual orders avoided

- Confirmed: no manual orders were placed or authorized in this pass.
