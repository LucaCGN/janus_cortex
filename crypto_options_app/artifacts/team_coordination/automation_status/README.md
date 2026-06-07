# Crypto Options Automation Status Reports

Each active limited automation must write its latest status to one stable markdown file in this directory.

Required latest files:

- `db_data_observability_latest.md`
- `signal_strategy_queue_worker_latest.md`
- `frontend_status_reporter_latest.md`

Freshness verifier:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_automation_report_status --write-artifacts --markdown
```

Minimum section headers:

## DB Data Observability

- `DB backend`
- `A/B/C/D freshness`
- `Manual orders avoided`

## Signal Strategy Queue Worker

- `Rows considered`
- `Decision`
- `Manual orders avoided`

## Frontend Status Reporter

- `Endpoints checked`
- `Frontend blockers`
- `Manual orders avoided`

Safety rules:

- Reports cannot authorize live trading.
- Reports cannot authorize manual orders.
- Missing or stale reports block adding additional standing automations.
