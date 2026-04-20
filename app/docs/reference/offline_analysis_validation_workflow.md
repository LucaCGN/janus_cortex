# Offline Analysis Validation Workflow

## Purpose
This is the canonical non-live validation flow for the offline NBA analysis module after `v1.0.2` database safety is in place.

Use it to prove:
- the current offline analysis substrate still works
- mart, report, backtest, and model commands run on a non-live database
- validation evidence is captured under the local workspace root

## Validation Targets
- `disposable`: preferred for seeded integration proof and regression checks
- `dev_clone`: preferred for realistic corpus reconciliation and season-wide count checks

## Disposable Validation Command
```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_analysis_validation.ps1 -Target disposable
```

What this does:
1. resets the disposable Docker Postgres database
2. runs DB smoke validation
3. runs the Postgres-backed NBA analysis pytest sweep
4. runs direct CLI commands for mart, report, backtests, and baselines
5. captures a final universe plus mart snapshot
6. writes `validation_summary.json` and `validation_summary.md` under `JANUS_LOCAL_ROOT\archives\output\nba_analysis_validation\...`

## Dev-Clone Validation Command
```powershell
$env:JANUS_DB_TARGET='dev_clone'
powershell -ExecutionPolicy Bypass -File .\tools\run_analysis_validation.ps1 -Target dev_clone
```

Recommended usage notes:
- confirm the clone is non-live before starting
- use `-RebuildMart` only if rebuilding the analysis mart on the clone is intended
- for corpus reconciliation, inspect the snapshot section in the validation summary

## Command Set
The validation runner executes:
- target description
- Postgres-backed NBA analysis pytest sweep
- `build_analysis_mart`
- `build_analysis_report`
- `run_analysis_backtests`
- `train_analysis_baselines`
- final validation snapshot collection

For `disposable`, it also runs the DB smoke helper from [tools/janus_db.ps1](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/tools/janus_db.ps1).

## Expected Evidence
- command stdout and stderr logs under `.../logs/`
- JSON summary at `validation_summary.json`
- Markdown summary at `validation_summary.md`
- direct command artifacts under the chosen output root

## Interpretation
- disposable validation proves migration compatibility, seeded corpus behavior, and end-to-end command wiring
- dev-clone validation proves realistic universe counts and season-wide substrate stability
- shared live should not be used for this validation flow until both lower stages are already green
