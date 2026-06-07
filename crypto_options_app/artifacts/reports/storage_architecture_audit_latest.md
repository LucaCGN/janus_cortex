# Crypto Options Storage Architecture Audit

- Generated: `2026-06-07T04:18:03.962532+00:00`
- Status: `degraded`
- Decision: `postgres_plus_redis_hot_plane_candidate`
- Postgres role: `durable_source_of_truth`
- Redis role: `gated_hot_plane_candidate`
- Manual orders avoided: `True`

## Reasons
- `postgres_container_memory_over_6gib`

## Endpoint Timings
- `health`: `ok`, 1097.92 ms, 94894 bytes
- `dashboard_control_center_state`: `ok`, 66.82 ms, 1017371 bytes
- `signals_validation_status`: `ok`, 1892.86 ms, 987507 bytes
- `strategies_promotion`: `ok`, 67.49 ms, 199080 bytes

## Runtime Summary
- Runtime status: `degraded`
- DB backend: `postgres`
- Postgres CPU: `6.84%`
- Postgres memory: `6.144GiB / 15.47GiB`
- Redis hot plane: `disabled`
