# Janus Cortex

Personal prediction-market data framework focused on production-grade data structures and service layers before strategy automation.

## Current Status
- Active checkpoint: `v0.3.4` (Append-only history storage rules)
- Checkpoint ledger source of truth: `dev-checkpoint/README.md`
- Current scope: schema + ingestion + API readiness, not autonomous strategy execution.

## v1 Target Stack
- Postgres (primary relational store)
- ChromaDB (event-document collections)
- FastAPI (service and orchestration API)
- Dockerized multi-service runtime

## Architecture Direction
The project now uses a provider/category/module split:
- `app/providers/*`: upstream connectors (Polymarket, NBA, HoopsStats, JinaAI)
- `app/domain/events/*`: canonical contracts + category-aware event domain logic
- `app/ingestion/*`: canonical mapping + ingestion pipelines
- `app/modules/*`: module-level serving/orchestration surfaces
- Legacy compatibility paths remain under `app/data/*` while migration completes.

## Roadmap (from `dev-checkpoint/README.md`)

### Completed block
- `v0.1.1` - `v0.1.6`: node and method validation baseline
- `v0.2.1` - `v0.2.6`: canonical contracts, adapters, ID rules, scoring, quality gates, integration packs
- `v0.2.7` - `v0.2.9`: app structure refactor gate, pytest topology hardening, docs synchronization

### In progress
- `v0.3.4`: Append-only history storage rules

### Planned lanes
1. `v0.3.*` Database MVP and migration discipline
2. `v0.4.*` Ingestion pipelines to new schema
3. `v0.5.*` FastAPI core layer
4. `v0.6.*` Portfolio and market-data service layer
5. `v0.7.*` NBA module serving layer
6. `v0.8.*` Chroma event-document layer
7. `v0.9.*` Production hardening and v1 cut

## Key Planning Docs
- `app/docs/development_guide.md`
- `app/docs/scalable_db_schema_proposal.md`
- `app/docs/scalable_api_routes_proposal.md`
- `app/docs/source_temporal_coverage.md`
- `app/docs/app_structure_modularization_plan.md`
- `dev-checkpoint/README.md`

## Testing
Pytest naming/discovery:
- file suffix: `*_pytest.py`
- mirrored test paths under top-level `tests/app/...`
- live external tests are gated by `JANUS_RUN_LIVE_TESTS=1`

Common commands:
- `python -m pytest -q`
- `$env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/nodes/test_temporal_coverage_live_pytest.py`
- `python -m app.data.databases.migrate`
- `$env:JANUS_RUN_DB_TESTS='1'; python -m pytest -q tests/app/data/databases/test_postgres_migrations_pytest.py`

## Notes
- `dev-checkpoint/*` is a session execution ledger and may be gitignored depending on local policy.
- Strategy logic is intentionally not part of core v1 readiness scope until data contracts/services are stable.
