# Crypto Options Storage Architecture Decision

Updated: 2026-06-08T23:10:00Z

## Current Decision

Postgres remains durable source of truth. Runtime has moved from the local
Docker Postgres container to the remote ZimaOS Postgres instance at
`192.168.0.156:5432/janus-postgres`.

Redis is implemented as a disabled-by-default Docker/config sidecar and remains gated. It has a tested hot-plane adapter for JSON TTL cache entries and owner-checked TTL queue locks. It remains a candidate for latest-state cache, frontend cache, queue locks, TTL ownership, pub/sub, and short-lived coordination only.

The latest audit does **not** justify enabling Redis yet. High Postgres/Docker memory alone is a Postgres resource-review warning, not proof that Redis will help. Redis should only be enabled after a measured hot-plane use case appears: repeated latest-state reads, frontend polling pressure, queue-lock contention, or pub/sub freshness fanout.

Latest audit:

- Artifact: `crypto_options_app/artifacts/reports/storage_architecture_audit_latest.json`
- Decision: `postgres_only_for_now`
- Reasons: `postgres_container_memory_over_6gib:requires_postgres_resource_review`, `postgres_temp_bytes_over_1gb`
- Postgres memory: `6.721GiB / 15.47GiB`
- Postgres CPU: `0.23%`
- Postgres diagnostics: 6 total connections, 1 active connection, 0 long active queries, 11 GB database size.
- Largest tables: `strategy_validation_runs` (~2.4 GB), `polymarket_order_book_levels` (~2.2 GB), `profile_distribution_snapshots` (~1.1 GB), `profile_raw_activity` (~998 MB).
- Redis status: `disabled`
- Endpoint timing status: no slow endpoint blockers in the measured pass; control-center and signal-status audits now use summary endpoints.
- Runtime code status: ok.

Latest migration:

- Artifact:
  `crypto_options_app/artifacts/team_coordination/postgres_remote_migration_20260608.md`
- Source: local Docker Postgres `crypto_options` on `127.0.0.1:55433`.
- Target: remote Postgres `janus-postgres` on `192.168.0.156:5432`.
- Exact table-count parity: `78/78` tables, `0` mismatches.
- Runtime health after cutover: `ok`, Postgres backend, complete schema.
- Local Docker Postgres remains available as rollback until the next remote
  runtime validation pass completes.

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

Enable Redis at runtime only after a measured cache/queue use case is selected. Do not move durable truth into Redis.

Current next Postgres work:

- Review temp-file/temp-byte sources before widening replay/scout jobs.
- Keep endpoint audits on summary payloads.
- Keep large historical replay reads bounded or materialized in Postgres.
- Do not treat Docker memory alone as a Redis adoption trigger.

Current adapter coverage: JSON TTL cache, NX/EX lock acquisition, owner-checked release, and RESP command framing. Redis still remains disabled unless `JANUS_CRYPTO_OPTIONS_REDIS_ENABLED=1`.

## Docker Status

The compose file still includes the local fallback services:

- `janus-cortex-crypto-options-postgres` on `127.0.0.1:55433`
- `janus-cortex-crypto-options-redis` on `127.0.0.1:56379`

Redis remains disabled unless `JANUS_CRYPTO_OPTIONS_REDIS_ENABLED=1`.

Active runtime is remote Postgres via `JANUS_CRYPTO_OPTIONS_POSTGRES_URL`.
