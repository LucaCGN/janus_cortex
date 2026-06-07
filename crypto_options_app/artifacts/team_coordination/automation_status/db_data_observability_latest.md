# Crypto Options DB/Data Observability

Generated at UTC: `2026-06-07T07:32:47.076323+00:00`

## DB backend

- Runtime DB backend: `postgres`
- Runtime source of truth: `postgres`
- DB read status: `ok`
- Postgres connection: `ok` on `127.0.0.1:55433/crypto_options`
- Postgres/resource status: `degraded` in storage audit because container memory is `6.144GiB / 15.47GiB` with warning `postgres_container_memory_over_6gib`
- Storage audit decision: `postgres_plus_redis_hot_plane_candidate`
- Redis hot-plane gate: `disabled`; keep Redis non-authoritative and off until explicitly enabled by a master/user task
- Runtime SQLite audit status: `ok`; `0` production runtime direct-connect blockers, `0` review-required SQLite usages, `11` allowed migration/test/compat usages
- Postgres shadow parity artifact: `ok`; `runtime_read_cutover_allowed=true`, latest artifact completed `2026-06-06T13:37:01.399904+00:00`

## A/B/C/D freshness

- A Crypto: `ready`. Underlying prices last source `2026-06-07T07:32:35.076327+00:00` for `BTC` and `ETH` (~12s age). Technical observers last source `2026-06-07T07:32:10.204924+00:00` for `BTC` (~37s) and `2026-06-07T07:32:19.324871+00:00` for `ETH` (~28s).
- B Profiles: `stale but not hard-blocked`. `top_profiles_distribution` latest source `2026-06-07T04:31:03+00:00` for `BTC` (~10905s / ~3.0h age). This pass fixed the health report so stale rows no longer appear as `source_age_seconds=0`.
- C Options: mixed. `BTC` `ready` with latest Polymarket source `2026-06-07T07:32:42.822248+00:00` (~5s age). `ETH` `degraded` with latest source `2026-06-06T18:37:35.414361+00:00` (~46512s / ~12.9h age), blocker `stale_updown_pair_snapshot`.
- D Portfolio/order lifecycle: `stale/inactive`. Latest observed lifecycle timestamps across `orders`, `positions`, `exit_plans`, `execution_intents`, `run_reports`, and `strategy_validation_runs` are clustered at `2026-06-07T02:20:47Z` (~5.2h old). `signal_validation_runs` is older at `2026-06-06T13:46:51.627941+00:00`.

## Postgres/resource status

- Watermarks now show fresh runs for `polymarket_option_price_capture` (`2026-06-07T07:32:42.822251+00:00`), `underlying_market_prices` (`2026-06-07T07:32:35.076327+00:00`), and `underlying_technical_observers` (`2026-06-07T07:32:01.940491+00:00`).
- `top_profiles_distribution` watermark is stale at `2026-06-07T04:30:21.979768+00:00`.
- No live-trading authority was detected in DB/runtime artifacts.

## Endpoint latency

- Latest bounded endpoint timings from `transition_readiness_latest.json`: `dashboard_control_center_state=41.27ms`, `health=1065.34ms`, `signals_validation_status=1573.88ms`, `strategies_promotion=58.31ms`; all recorded `200 OK`.
- Direct HTTP probe to `127.0.0.1:8000` during this pass failed with remote-connection refusal, so no new live endpoint sweep was started.

## Blockers

- `B` profile distribution freshness is about three hours stale and needs the profile distribution service checked or intentionally paused.
- `C` ETH option data is about 12.9 hours stale with `stale_updown_pair_snapshot`.
- Postgres remains usable but resource status is still degraded due to memory over `6GiB`.
- Direct runtime HTTP probe was unavailable on `127.0.0.1:8000`; rely on bounded readiness artifacts until the app listener is confirmed.

## Files changed

- `crypto_options_app/artifacts/team_coordination/automation_status/db_data_observability_latest.md`
- `crypto_options_app/reports/system_integrity.py`
- `tests/crypto_options_app/test_system_integrity_health_pytest.py`

## Tests run

- `python -m pytest tests/crypto_options_app/test_system_integrity_health_pytest.py -q` -> `29 passed`

## Live activity status

- Live trading authorized: `false`
- Orders allowed: `false`
- This pass did not start or stop services, run imports, replay jobs, or place orders.

## Manual orders avoided

- Confirmed: manual orders were avoided for this pass.
