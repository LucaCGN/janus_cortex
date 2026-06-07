# Crypto Options Storage Architecture Decision

Updated: 2026-06-07T04:18:30Z

## Current Decision

Postgres remains durable source of truth.

Redis is implemented as a disabled-by-default Docker/config sidecar and remains gated. It is a hot-plane candidate for latest-state cache, frontend cache, queue locks, TTL ownership, pub/sub, and short-lived coordination only.

Latest audit:

- Artifact: `crypto_options_app/artifacts/reports/storage_architecture_audit_latest.json`
- Decision: `postgres_plus_redis_hot_plane_candidate`
- Reason: `postgres_container_memory_over_6gib`
- Postgres memory: `6.144GiB / 15.47GiB`
- Postgres CPU: `6.84%`
- Redis status: `disabled`
- Endpoint timing status: no slow endpoint blockers in the measured pass.
- Runtime code status: ok.

## Durable Postgres Data

- A/B/C historical data
- indicators
- signals and validation results
- strategy specs, versions, replay results, promotion state
- order lifecycle rows
- fills, positions, exits, settlement, reconciliation
- budget ledger and audit data

## Redis Candidate Data

- latest A/B/C snapshots
- frontend dashboard cache
- replay candidate cache
- worker queue locks and TTL ownership
- pub/sub or streams for data-service freshness
- rate-limit counters
- short-lived automation coordination

## Redis Prohibition

Redis must never be the only store for trading-critical truth.

## Gate

Run `python -m crypto_options_app.scripts.run_crypto_options_storage_architecture_audit --write-artifacts --json`.

Enable Redis at runtime only after adapter tests prove the specific cache/queue use case. Do not move durable truth into Redis.

Current next test: add cache/queue TTL adapter tests before turning on `JANUS_CRYPTO_OPTIONS_REDIS_ENABLED`.

## Docker Status

The compose file now includes:

- `janus-cortex-crypto-options-postgres` on `127.0.0.1:55433`
- `janus-cortex-crypto-options-redis` on `127.0.0.1:56379`

Redis remains disabled unless `JANUS_CRYPTO_OPTIONS_REDIS_ENABLED=1`.
