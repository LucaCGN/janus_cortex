# Postgres Docker Database Migration Plan

Date: 2026-06-06

Status: implementation checkpoint.

## Purpose

SQLite remains a useful local fallback, but the current crypto-options app has enough concurrent services that normal data-service writes can lock report and scout reads. The target is an app-owned Postgres service that can absorb concurrent A/B/C capture, signal validation, strategy replay, and observability reads without relying on one SQLite write lock.

## Canonical Postgres Target

- Compose file: `crypto_options_app/docker-compose.postgres.yml`
- Container: `janus-cortex-crypto-options-postgres`
- Image: `postgres:16-alpine`
- Host/port: `127.0.0.1:55433`
- Database: `crypto_options`
- User: `crypto_options`
- Example env: `crypto_options_app/.env.postgres.example`

This does not replace the older `janus-cortex-postgres-disposable` container used by NBA/WNBA experiments. That container remains legacy/shared infrastructure. The crypto-options app owns its own container, volume, port, and env names.

## Operating Commands

Start Postgres:

```powershell
docker compose -f crypto_options_app/docker-compose.postgres.yml up -d
```

Check connection:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_postgres_status --check-connection --json
```

Bootstrap the canonical schema:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_postgres_status --check-connection --bootstrap-schema --json
```

Build the SQLite migration inventory:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_sqlite_to_postgres_plan --json
```

Dry-run selected table copy:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_sqlite_to_postgres_copy --tables app_settings data_service_watermarks strategy_specs signal_specs --limit-per-table 5 --dry-run --json
```

Bounded selected table copy:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_sqlite_to_postgres_copy --tables app_settings data_service_watermarks strategy_specs signal_specs --limit-per-table 5 --batch-size 5 --json
```

## Cutover Policy

The app is not considered cut over just because the Postgres container is running. Runtime cutover requires:

- DB adapter layer for SQLite and Postgres query parameters.
- Replacement for SQLite-specific query functions such as `julianday`.
- Bulk copy path for high-volume tables, especially `polymarket_order_book_levels`.
- Read shadow mode where API reports can read from Postgres while writers still write SQLite.
- Per-worker cutover with parity checks and rollback.

## Initial Migration Phases

1. Start app-owned Postgres and keep SQLite fallback intact.
2. Bootstrap Postgres schema from the canonical schema.
3. Build and review SQLite table/row inventory.
4. Add a bulk-copy importer for append-only A/B/C data.
5. Add a DB dialect adapter for worker/report reads.
6. Shadow-read API health/dashboard from Postgres.
7. Cut over data-service writers one group at a time.

## 2026-06-06 Implementation Checkpoint

Completed:

- App-owned Postgres container is running and healthy on `127.0.0.1:55433`.
- Canonical schema bootstrapped with 75/75 expected tables.
- SQLite inventory counted 9,086,985 rows across 75 tables.
- Dialect helper added for SQLite/Postgres placeholders and timestamp delta expressions.
- Strategy live-replay forward-mark query now uses the dialect timestamp-delta helper instead of embedding the expression directly.
- Bulk-copy importer added with table selection, row limits, batching, `ON CONFLICT DO NOTHING`, dry-run mode, and structured artifacts.
- Bounded real copy succeeded for `app_settings`, `data_service_watermarks`, `strategy_specs`, and `signal_specs`.

Bounded copy result:

- Source rows seen: 16.
- Inserted rows: 15.
- Artifact: `crypto_options_app/artifacts/reports/postgres_bulk_copy_latest.json`.

Remaining before runtime cutover:

- Add read adapters for reports/API routes that currently call `sqlite3` directly.
- Port remaining SQLite-specific query functions, especially `julianday` and `json_extract`.
- Run full bulk copy in controlled groups, starting with low-volume strategy/signal tables before A/B/C high-volume capture tables.
- Shadow-read health/dashboard from Postgres and compare row counts/latest timestamps before moving any writer.

## Safety

This database work does not authorize trading. Global/API live flags remain false. Live trading still belongs only to supervised runtime with scoped child flags, ledgers, risk gates, reconciliation, and stop gates.

## 2026-06-06 Health/Dashboard Stabilization Before Read Cutover

SQLite contention from active A/B/C data-service writers caused health and control-center reads to become slow or misleading before the Postgres read path was ready. The app now has a transitional monitor-safe behavior:

- `/health` returns a fast conservative response from the DB report cache instead of performing a full inline DB scan when the cache is expired.
- A/B/C source readiness is overlaid from fresh service status files:
  - A Crypto: `underlying_technical_observers_status.json`
  - B Profiles: `profile_distribution_status.json`
  - C Options: `option_price_capture_status.json`
- `/dashboard/control-center-state` uses a cache-first path and overlays source cards from the same status files.
- A Crypto and C Options loops retry transient SQLite lock errors before marking themselves degraded.

This is not the final DB design. It only keeps observability responsive while Postgres cutover is pending. Live readiness must remain blocked if the canonical DB report cache is stale, even if source status files are healthy.

Next DB/data work:

1. Build a read adapter for report/API/scout queries with explicit SQLite/Postgres dialect handling.
2. Bulk-copy canonical SQLite tables into Postgres in controlled groups.
3. Add parity checks for row counts, latest timestamps, and selected sample rows.
4. Move health/dashboard/scout heavy reads to Postgres shadow mode only after parity passes.
5. Add source metrics for row rate, freshness, queue depth, lock waits, process PID, and watermark age.
6. Keep SQLite as fallback until dashboard, health, strategy scout, and signal scout read paths all pass parity.

## 2026-06-06 Shadow Parity And Retention Checkpoint

The first Postgres shadow-parity report is now implemented and wired into `/health`:

- Report artifact: `crypto_options_app/artifacts/reports/postgres_shadow_parity_latest.json`.
- CLI: `python -m crypto_options_app.scripts.run_crypto_options_postgres_status --check-connection --shadow-parity --write-latest --json`.
- Health field: `postgres.shadow_parity`.

Current result:

- Postgres connection: `ok`.
- Shadow parity: `degraded`.
- Monitored tables: `14`.
- Matching tables: `0`.
- Runtime read cutover: `false`.
- Blockers: `row_count_mismatch`, `latest_timestamp_mismatch`.

This is expected until a full controlled copy/import pass has been run. The parity report is now the formal gate for allowing read-heavy runtime surfaces to move from SQLite to Postgres.

A storage hard stop also occurred while writing the parity artifact because generated data exhausted `C:`. The immediate cleanup removed only generated scratch/log files and preserved canonical data:

- Deleted the concept-session scratch SQLite snapshot `20260606_tail_reversal_snapshot.sqlite`.
- Cleared oversized option-price capture stdout/stderr logs.
- Deleted the legacy pre-centralization generated tree `local/shared/artifacts/crypto-options-app/signal-live` after confirming canonical artifacts now live under `crypto_options_app/artifacts`.
- Canonical SQLite DB and markdown/session summaries were not deleted.

Retention policy is now part of the DB/data migration plan:

1. Generated concept-session SQLite snapshots must be temporary and should be deleted or compacted after their markdown/DB conclusions are recorded.
2. Data-service stdout/stderr logs must rotate or truncate by size.
3. Legacy/pre-centralization `local/shared` artifacts must be audited before deletion, then either archived or replaced with pointer docs.
4. A/B/C writers should emit compact status and metrics; large historical paths belong in Postgres/parquet archives, not unbounded JSON/log files.
5. The control center should surface disk-free and largest-artifact warnings before the drive reaches a hard stop.

## 2026-06-06 Runtime Cutover And Crash Lesson

The app runtime is now Postgres-first:

- Backend API runs separately on `127.0.0.1:8011`.
- Frontend static SPA runs separately on `127.0.0.1:8012`.
- Docker Postgres is the runtime DB on `127.0.0.1:55433`.
- `crypto_options_app.db.connection.connect()` maps the canonical logical DB path to the Postgres compatibility adapter.
- Runtime dashboard, health, strategy registry, data services, signal code, and strategy code must not call `sqlite3.connect()` directly.
- SQLite is retained only as a migration/test-fixture source until all historical raw data has been safely chunk-imported or archived.

The failed raw A/C import attempt exposed an important operational rule: do not run monolithic raw-table imports against `polymarket_order_book_levels` or other high-volume replay tables. That path saturated Docker/Postgres, triggered long autovacuum/recovery, and contributed to machine-level instability.

Current resource-safe policy:

1. Runtime audits use row-existence checks and planner estimates, not `COUNT(*)`, for high-volume tables.
2. Raw historical imports must be chunked, bounded, and committed in small table-specific batches.
3. No SQLite-to-Postgres hot-sync or bulk-copy loop may run as part of normal app runtime.
4. A/B/C collectors may run continuously only with conservative concurrency until resource telemetry proves headroom.
5. The runtime audit must report Docker/Postgres memory usage and block or warn before it reaches unsafe levels.

Operational audit command:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_runtime_audit --write-artifacts --json
```

Acceptance for app-online state:

- Docker Postgres healthy.
- Backend `8011` healthy.
- Frontend `8012` healthy.
- A/B/C processes alive and writing fresh status rows.
- Runtime DB adapter is Postgres.
- No forbidden SQLite hot-sync process is running.
- No runtime code path outside migration/test helpers uses direct `sqlite3.connect()`.
## 2026-06-07 Runtime Health Reporting Guardrail

The runtime connector can route canonical app reads to Docker Postgres while
`CryptoOptionsAppConfig.db_path` still points at the legacy SQLite compatibility
file. Health reports must therefore expose both:

- `db.configured_sqlite_path`: compatibility/migration path.
- `db.configured_sqlite_path_exists`: whether the file still exists locally.
- `db.postgres_runtime_expected`: whether the canonical path should route to
  Postgres.
- `db.connection_backend` and `db.connection_is_postgres`: the actual backend
  used by the health read.
- `db.path_role` and `db.runtime_source_of_truth`: whether the configured path is
  runtime SQLite or a compatibility path for a Postgres-backed runtime.

This prevents the control center and automations from misclassifying the app as
SQLite-backed after the runtime connector has already cut reads over to
Postgres. Deleting or compacting the SQLite file remains a separate controlled
retention/parity step, not a heartbeat action.
