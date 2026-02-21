-- v0.4.6: derived candle aggregation storage for backfill/retry flows

CREATE TABLE IF NOT EXISTS market_data.outcome_price_candles (
    outcome_id UUID NOT NULL REFERENCES catalog.outcomes(outcome_id) ON DELETE RESTRICT,
    timeframe TEXT NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    open NUMERIC(10, 6),
    high NUMERIC(10, 6),
    low NUMERIC(10, 6),
    close NUMERIC(10, 6),
    volume NUMERIC(18, 6),
    raw_json JSONB,
    PRIMARY KEY (outcome_id, timeframe, open_time, source)
);

CREATE INDEX IF NOT EXISTS ix_market_data_outcome_price_candles_outcome_tf_open_desc
    ON market_data.outcome_price_candles(outcome_id, timeframe, open_time DESC);
