-- v0.4.2: market state snapshots for polymarket markets/outcomes ingestion

CREATE TABLE IF NOT EXISTS catalog.market_state_snapshots (
    market_state_snapshot_id UUID PRIMARY KEY,
    market_id UUID NOT NULL REFERENCES catalog.markets(market_id) ON DELETE RESTRICT,
    sync_run_id UUID REFERENCES core.sync_runs(sync_run_id) ON DELETE SET NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    last_price NUMERIC(10, 6),
    volume NUMERIC(18, 6),
    liquidity NUMERIC(18, 6),
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    mid_price NUMERIC(10, 6),
    market_status TEXT,
    raw_json JSONB
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_market_state_snapshots_market_captured_sync
    ON catalog.market_state_snapshots(market_id, captured_at, sync_run_id);

CREATE INDEX IF NOT EXISTS ix_catalog_market_state_snapshots_market_captured_desc
    ON catalog.market_state_snapshots(market_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_catalog_market_state_snapshots_sync_run
    ON catalog.market_state_snapshots(sync_run_id);
