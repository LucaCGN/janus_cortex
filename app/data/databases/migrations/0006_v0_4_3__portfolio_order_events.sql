-- v0.4.3: portfolio mirror event timeline table

CREATE TABLE IF NOT EXISTS portfolio.order_events (
    order_event_id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES portfolio.orders(order_id) ON DELETE CASCADE,
    event_time TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    filled_size_delta NUMERIC(18, 6),
    filled_notional_delta NUMERIC(18, 6),
    raw_json JSONB
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_order_events_order_event_time_type
    ON portfolio.order_events(order_id, event_time, event_type);

CREATE INDEX IF NOT EXISTS ix_portfolio_order_events_order_event_time_desc
    ON portfolio.order_events(order_id, event_time DESC);
