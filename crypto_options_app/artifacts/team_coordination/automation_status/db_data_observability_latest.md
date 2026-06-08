# Crypto Options DB/Data Observability

Generated at UTC: `2026-06-07T08:02:00Z`

## DB backend

- Runtime DB backend: `postgres`
- Runtime source of truth: `postgres`
- DB read status: `ok`
- Canonical backend: `http://127.0.0.1:8011/v1/crypto-options-app`
- Health status: `degraded`
- Redis hot-plane gate: `disabled`; Redis remains non-authoritative and was not enabled on this pass

## A/B/C/D freshness

- A Crypto: `ready`. Underlying market prices refreshed at `2026-06-07T08:01:28.736557+00:00` for `BTC` and `ETH`; underlying technical observers refreshed at `2026-06-07T08:00:40.235837+00:00` for `BTC` and `2026-06-07T08:00:49.552726+00:00` for `ETH`.
- B Profiles: `ready` on the latest readiness rows, but still noisy. `BTC` latest source is `2026-06-07T08:01:01+00:00` (`29.4s` age) and `ETH` latest source is `2026-06-07T08:00:27+00:00` (`63.4s` age). Both rows still carry `profile_distribution_source_stale` inside `coverage_warnings`.
- C Options: `ready`. `BTC` and `ETH` latest pair snapshots are both at `2026-06-07T08:01:12.410841+00:00` with about `18s` age; prior stale ETH option-capture behavior is not present on this pass.
- D Portfolio/order lifecycle: still effectively inactive/stale for observability. `polymarket_live_activity_capture` watermark remains at `2026-06-04T07:55:30.043810+00:00`; latest dashboard order and position updates are both `2026-06-07T00:54:47.359876+00:00`.

## Postgres/resource status

- Postgres container: `janus-cortex-crypto-options-postgres`
- Container status: `Up 17 hours (healthy)`
- Current resource pressure: `331.68%` CPU and `6.45GiB / 15.47GiB` memory
- Storage audit status: `degraded`
- Storage audit decision: `postgres_plus_redis_hot_plane_candidate`
- Current storage-audit warning still in force: `postgres_container_memory_over_6gib`

## Runtime SQLite audit status

- Runtime SQLite audit: `ok`
- Production runtime direct-connect blockers: `0`
- Review-required SQLite usages: `0`
- Allowed migration/test/compat SQLite usages: `11`

## Endpoint latency

- `GET /health`: `200`, `1612.56ms`
- `GET /strategies/promotion`: `200`, `202.10ms`
- `GET /dashboard/control-center-state`: `200`, `546.85ms`
- `GET /signals/validation/status`: `200`, `1844.37ms`

## Blockers

- The `D` live-activity plane is still inactive from an observability perspective; its watermark is over three days old.
- Postgres remains readable and healthy enough for bounded reporting, but the storage audit is still degraded because memory is above `6GiB`.
- `B` recovered to `ready`, but its readiness rows still emit stale-source coverage warnings, so the profile plane is not yet clean.

## Files changed

- `crypto_options_app/artifacts/team_coordination/automation_status/db_data_observability_latest.md`
- `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`
- `$CODEX_HOME/automations/crypto-options-db-data-observability/memory.md`

## Tests run

- No tests run; this was a bounded report-only pass.

## Live activity status

- Live trading authorized: `false`
- Orders allowed: `false`
- Redis hot plane: `disabled`
- No services were started or stopped
- No imports, replay jobs, or manual orders were run

## Manual orders avoided

- Confirmed: manual orders were avoided for this pass.
