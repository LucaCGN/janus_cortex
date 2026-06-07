# Crypto Options Storage Architecture Audit

- Generated: `2026-06-07T11:11:02.986849+00:00`
- Status: `degraded`
- Decision: `postgres_plus_redis_hot_plane_candidate`
- Postgres role: `durable_source_of_truth`
- Redis role: `gated_hot_plane_candidate`
- Manual orders avoided: `True`

## Reasons
- `postgres_container_memory_over_6gib`

## Endpoint Timings
- `health`: `ok`, 23.53 ms, 100397 bytes
- `dashboard_control_center_state`: `ok`, 46.74 ms, 1017680 bytes
- `signals_validation_status`: `ok`, 1420.45 ms, 987507 bytes
- `strategies_promotion`: `ok`, 48.98 ms, 254527 bytes

## Runtime Summary
- Runtime status: `degraded`
- DB backend: `postgres`
- Postgres CPU: `32.78%`
- Postgres memory: `6.722GiB / 15.47GiB`
- Redis hot plane: `disabled`
