# Crypto Options Storage Architecture Audit

- Generated: `2026-06-07T11:35:28.407136+00:00`
- Status: `degraded`
- Decision: `postgres_only_for_now`
- Postgres role: `durable_source_of_truth`
- Redis role: `gated_hot_plane_candidate`
- Manual orders avoided: `True`

## Reasons
- `postgres_container_memory_over_6gib:requires_postgres_resource_review`
- `postgres_temp_bytes_over_1gb`

## Endpoint Timings
- `health`: `ok`, 798.02 ms, 102182 bytes
- `dashboard_control_center_state`: `ok`, 32.0 ms, 15991 bytes
- `signals_validation_status`: `ok`, 72.44 ms, 596 bytes
- `strategies_promotion`: `ok`, 44.96 ms, 254527 bytes

## Runtime Summary
- Runtime status: `degraded`
- DB backend: `postgres`
- Postgres CPU: `0.23%`
- Postgres memory: `6.721GiB / 15.47GiB`
- Redis hot plane: `disabled`

## Postgres Diagnostics
- Status: `degraded`
- Database size: `11 GB`
- Active connection count: `1`
- Total connection count: `6`
- Long active query count: `0`
- Warnings: `postgres_temp_bytes_over_1gb`
