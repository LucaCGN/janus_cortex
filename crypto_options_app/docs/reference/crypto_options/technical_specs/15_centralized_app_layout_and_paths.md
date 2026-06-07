# Centralized App Layout And Paths

Canonical root:

`crypto_options_app/`

Legacy paths are migration sources or compatibility wrappers only. New crypto-options code, docs, defaults, artifacts, and data-service entrypoints must live under the canonical root.

## Source Of Truth

| Asset | Canonical path | Legacy handling |
| --- | --- | --- |
| Technical specs | `crypto_options_app/docs/reference/crypto_options` | `app/docs/reference/crypto_options` is a pointer only. |
| Runtime scripts | `crypto_options_app/scripts` | `codex_tool/run_crypto_options*.py` wrappers only. |
| Data services | `crypto_options_app/data_services` | No legacy source of truth. |
| Canonical DB | `crypto_options_app/data/crypto_options_data.sqlite` | Old SQLite shards are import/migration sources. |
| Active profile pool | `crypto_options_app/data/profile-pool/active_crypto_profile_pool.txt` | Old pool paths are migration sources. |
| Live artifacts | `crypto_options_app/artifacts/live-validation` | Old live-validation artifacts are historical evidence. |
| Reports | `crypto_options_app/artifacts/reports` | Old reports are historical evidence. |
| Automation state | `crypto_options_app/artifacts/automation` | Old automation state is historical evidence. |

## Required Defaults

All app defaults must resolve under `crypto_options_app` unless a command explicitly receives a legacy migration source path.

- `CryptoOptionsAppConfig.db_path`: `crypto_options_app/data/crypto_options_data.sqlite`
- `CryptoOptionsAppConfig.artifact_root`: `crypto_options_app/artifacts`
- `CryptoOptionsAppConfig.active_profile_pool_path`: `crypto_options_app/data/profile-pool/active_crypto_profile_pool.txt`
- `CryptoOptionsAppConfig.docs_root`: `crypto_options_app/docs/reference/crypto_options`

## Audit Gate

`tests/crypto_options_app/test_crypto_options_centralization_pytest.py` fails when runtime defaults still point at old crypto artifact roots or when `codex_tool` crypto scripts stop acting as wrappers.

## GitHub Grounding

- Parent implementation issue: #123.
- Centralization child issue: #124.
- Prior app build parent: #108.
- Current app readiness issue: #122.
- Issue #47 remains research/history only.
