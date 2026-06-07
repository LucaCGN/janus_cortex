# Crypto Options Storage Architecture Audit

- Generated: `2026-06-07T10:50:41.155982+00:00`
- Status: `degraded`
- Decision: `postgres_plus_redis_hot_plane_candidate`
- Postgres role: `durable_source_of_truth`
- Redis role: `gated_hot_plane_candidate`
- Manual orders avoided: `True`

## Reasons
- `postgres_container_memory_over_6gib`

## Endpoint Timings
- `health`: `ok`, 42.01 ms, 98455 bytes
- `dashboard_control_center_state`: `ok`, 59.73 ms, 1017681 bytes
- `signals_validation_status`: `ok`, 1403.1 ms, 987507 bytes
- `strategies_promotion`: `ok`, 49.34 ms, 254527 bytes

## Runtime Summary
- Runtime status: `degraded`
- DB backend: `postgres`
- Postgres CPU: `137.00%`
- Postgres memory: `6.705GiB / 15.47GiB`
- Redis hot plane: `disabled`
