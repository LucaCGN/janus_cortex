-- v0.3.4: append-only market history storage

CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS market_data.outcome_price_ticks (
    outcome_id UUID NOT NULL REFERENCES catalog.outcomes(outcome_id) ON DELETE RESTRICT,
    ts TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    price NUMERIC(10, 6),
    bid NUMERIC(10, 6),
    ask NUMERIC(10, 6),
    volume NUMERIC(18, 6),
    liquidity NUMERIC(18, 6),
    raw_json JSONB,
    PRIMARY KEY (outcome_id, ts, source)
);

CREATE TABLE IF NOT EXISTS market_data.orderbook_snapshots (
    orderbook_snapshot_id UUID PRIMARY KEY,
    outcome_id UUID NOT NULL REFERENCES catalog.outcomes(outcome_id) ON DELETE RESTRICT,
    captured_at TIMESTAMPTZ NOT NULL,
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    spread NUMERIC(10, 6),
    mid_price NUMERIC(10, 6),
    bid_depth NUMERIC(18, 6),
    ask_depth NUMERIC(18, 6),
    raw_json JSONB
);

CREATE TABLE IF NOT EXISTS market_data.orderbook_levels (
    orderbook_snapshot_id UUID NOT NULL
        REFERENCES market_data.orderbook_snapshots(orderbook_snapshot_id) ON DELETE RESTRICT,
    side TEXT NOT NULL CHECK (side IN ('bid', 'ask')),
    level_no INTEGER NOT NULL,
    price NUMERIC(10, 6),
    size NUMERIC(18, 6),
    order_count INTEGER,
    PRIMARY KEY (orderbook_snapshot_id, side, level_no)
);

CREATE INDEX IF NOT EXISTS ix_market_data_outcome_price_ticks_outcome_ts_desc
    ON market_data.outcome_price_ticks(outcome_id, ts DESC);

CREATE INDEX IF NOT EXISTS ix_market_data_orderbook_snapshots_outcome_captured_desc
    ON market_data.orderbook_snapshots(outcome_id, captured_at DESC);

CREATE OR REPLACE FUNCTION market_data.enforce_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'append-only table %.% does not allow % operations',
        TG_TABLE_SCHEMA,
        TG_TABLE_NAME,
        TG_OP
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_append_only_outcome_price_ticks ON market_data.outcome_price_ticks;
CREATE TRIGGER trg_append_only_outcome_price_ticks
    BEFORE UPDATE OR DELETE ON market_data.outcome_price_ticks
    FOR EACH ROW
    EXECUTE FUNCTION market_data.enforce_append_only();

DROP TRIGGER IF EXISTS trg_append_only_orderbook_snapshots ON market_data.orderbook_snapshots;
CREATE TRIGGER trg_append_only_orderbook_snapshots
    BEFORE UPDATE OR DELETE ON market_data.orderbook_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION market_data.enforce_append_only();

DROP TRIGGER IF EXISTS trg_append_only_orderbook_levels ON market_data.orderbook_levels;
CREATE TRIGGER trg_append_only_orderbook_levels
    BEFORE UPDATE OR DELETE ON market_data.orderbook_levels
    FOR EACH ROW
    EXECUTE FUNCTION market_data.enforce_append_only();

