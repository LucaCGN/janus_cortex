-- v0.5.1: ops tables for API health and job orchestration

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.job_definitions (
    job_id UUID PRIMARY KEY,
    module_id UUID REFERENCES core.modules(module_id) ON DELETE SET NULL,
    job_code TEXT NOT NULL UNIQUE,
    description TEXT,
    schedule_cron TEXT,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.job_runs (
    job_run_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES ops.job_definitions(job_id) ON DELETE CASCADE,
    sync_run_id UUID REFERENCES core.sync_runs(sync_run_id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    error_text TEXT,
    metrics_json JSONB
);

CREATE TABLE IF NOT EXISTS ops.system_heartbeats (
    service_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_heartbeat TIMESTAMPTZ NOT NULL,
    message TEXT
);

CREATE INDEX IF NOT EXISTS ix_ops_job_runs_job_started_desc
    ON ops.job_runs(job_id, started_at DESC);

CREATE INDEX IF NOT EXISTS ix_ops_job_runs_status_started_desc
    ON ops.job_runs(status, started_at DESC);
