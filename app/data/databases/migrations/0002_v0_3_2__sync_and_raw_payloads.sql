-- v0.3.2: migration framework support tables and ingestion run metadata

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS catalog;

CREATE TABLE IF NOT EXISTS core.sync_runs (
    sync_run_id UUID PRIMARY KEY,
    provider_id UUID REFERENCES core.providers(provider_id) ON DELETE RESTRICT,
    module_id UUID REFERENCES core.modules(module_id) ON DELETE RESTRICT,
    pipeline_name TEXT NOT NULL,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    rows_read BIGINT,
    rows_written BIGINT,
    error_text TEXT,
    meta_json JSONB
);

CREATE TABLE IF NOT EXISTS core.raw_payloads (
    raw_payload_id UUID PRIMARY KEY,
    sync_run_id UUID REFERENCES core.sync_runs(sync_run_id) ON DELETE SET NULL,
    provider_id UUID NOT NULL REFERENCES core.providers(provider_id) ON DELETE RESTRICT,
    endpoint TEXT NOT NULL,
    external_id TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.event_module_bindings (
    event_module_binding_id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES catalog.events(event_id) ON DELETE CASCADE,
    module_id UUID NOT NULL REFERENCES core.modules(module_id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled_for_trading BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_event_module_binding_unique_pair
    ON catalog.event_module_bindings(event_id, module_id);

CREATE INDEX IF NOT EXISTS ix_core_sync_runs_provider_started
    ON core.sync_runs(provider_id, started_at DESC);

