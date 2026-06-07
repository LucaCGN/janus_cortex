# Crypto Options Storage Architecture Audit

- Generated: `2026-06-07T11:29:15.538299+00:00`
- Status: `degraded`
- Decision: `postgres_plus_redis_hot_plane_candidate`
- Postgres role: `durable_source_of_truth`
- Redis role: `gated_hot_plane_candidate`
- Manual orders avoided: `True`

## Reasons
- `postgres_container_memory_over_6gib`

## Endpoint Timings
- `health`: `ok`, 29.31 ms, 100629 bytes
- `dashboard_control_center_state`: `ok`, 44.36 ms, 15993 bytes
- `signals_validation_status`: `ok`, 106.39 ms, 596 bytes
- `strategies_promotion`: `ok`, 50.59 ms, 254527 bytes

## Runtime Summary
- Runtime status: `degraded`
- DB backend: `postgres`
- Postgres CPU: `121.82%`
- Postgres memory: `6.743GiB / 15.47GiB`
- Redis hot plane: `disabled`
