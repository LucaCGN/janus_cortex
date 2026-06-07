# Crypto Options Storage Architecture Audit

- Generated: `2026-06-07T11:21:17.798510+00:00`
- Status: `degraded`
- Decision: `postgres_plus_redis_hot_plane_candidate`
- Postgres role: `durable_source_of_truth`
- Redis role: `gated_hot_plane_candidate`
- Manual orders avoided: `True`

## Reasons
- `postgres_container_memory_over_6gib`

## Endpoint Timings
- `health`: `ok`, 41.26 ms, 100461 bytes
- `dashboard_control_center_state`: `ok`, 41.21 ms, 1017681 bytes
- `signals_validation_status`: `ok`, 86.31 ms, 596 bytes
- `strategies_promotion`: `ok`, 47.13 ms, 254527 bytes

## Runtime Summary
- Runtime status: `degraded`
- DB backend: `postgres`
- Postgres CPU: `109.26%`
- Postgres memory: `6.714GiB / 15.47GiB`
- Redis hot plane: `disabled`
