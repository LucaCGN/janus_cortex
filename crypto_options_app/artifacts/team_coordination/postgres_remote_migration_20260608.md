# Remote Postgres Migration - 2026-06-08

## Result

The Crypto Options App runtime database was migrated from the local Docker
Postgres container to the remote ZimaOS Postgres instance.

## Active Runtime Target

- Host: `192.168.0.156`
- Port: `5432`
- Database: `janus-postgres`
- User: `admin`
- Runtime URL env: `JANUS_CRYPTO_OPTIONS_POSTGRES_URL`
- Durable source of truth: remote Postgres

## Migration Method

- Writers stopped before snapshot:
  - API on `8011`
  - A Crypto services
  - B Profiles service
  - C Options service
- Source: local Docker container `janus-cortex-crypto-options-postgres`
  database `crypto_options`.
- Dump format: compressed Postgres custom dump.
- In-container dump path:
  `/tmp/janus_crypto_options_pg16_to_remote_pg17.dump`.
- Dump size: `514.3 MB`.
- Restore target: remote `janus-postgres`.
- Local Docker Postgres remains running as rollback until the remote runtime is
  observed stable.

## Parity

Exact per-table count parity passed after restore.

- Local public tables: `78`
- Remote public tables: `78`
- Mismatched table counts: `0`

Key exact counts:

| Table | Count |
| --- | ---: |
| `orders` | 7,925 |
| `execution_intents` | 7,925 |
| `fills` | 7,846 |
| `positions` | 7,845 |
| `exit_plans` | 7,845 |
| `strategy_candidates` | 30,448 |
| `strategy_validation_runs` | 30,236 |
| `profile_raw_activity` | 910,830 |
| `external_technical_observer_components` | 1,742,200 |
| `polymarket_order_book_levels` | 406,559 |

## Runtime Verification

- `/v1/crypto-options-app/health`: `ok`
- DB backend: `postgres`
- Connection is Postgres: `true`
- Schema status: `complete`
- API and A/B/C services restarted against remote Postgres.

Fresh remote writes observed after restart:

| Table | Latest timestamp |
| --- | --- |
| `external_technical_observer_snapshots` | `2026-06-08T23:08:13Z` |
| `underlying_price_ticks` | `2026-06-08T23:08:06Z` |
| `polymarket_order_books` | `2026-06-08T23:08:23Z` |
| `polymarket_price_ticks` | `2026-06-08T23:08:23Z` |
| `profile_distribution_snapshots` | `2026-06-08T23:07:10Z` |
| `profile_raw_activity` | `2026-06-08T23:07:31Z` |

## Patch

`CryptoOptionsPostgresSettings.from_url()` now URL-decodes username and
password fields. This is required because the remote password contains reserved
URL characters.

Focused test:

```text
python -m pytest tests\crypto_options_app\test_postgres_foundation_pytest.py::test_postgres_settings_parse_url_encoded_credentials -q
```

Result: `1 passed`.

## Follow-Up

- Keep local Docker Postgres available until at least one stable remote runtime
  observation pass completes.
- Do not remove the local container or dump until the app has passed the next
  strategy/reconciliation validation cycle on remote Postgres.
- Future process launches must include `JANUS_CRYPTO_OPTIONS_POSTGRES_URL` or
  source the root `.env`.
