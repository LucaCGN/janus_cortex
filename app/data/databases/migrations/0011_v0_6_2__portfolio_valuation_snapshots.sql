-- v0.6.2: valuation snapshots for portfolio summary endpoints

CREATE TABLE IF NOT EXISTS portfolio.valuation_snapshots (
    account_id UUID NOT NULL REFERENCES portfolio.trading_accounts(account_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    equity_usd NUMERIC(18, 6),
    cash_usd NUMERIC(18, 6),
    positions_value_usd NUMERIC(18, 6),
    realized_pnl_usd NUMERIC(18, 6),
    unrealized_pnl_usd NUMERIC(18, 6),
    raw_json JSONB,
    PRIMARY KEY (account_id, captured_at)
);

CREATE INDEX IF NOT EXISTS ix_portfolio_valuation_snapshots_account_captured_desc
    ON portfolio.valuation_snapshots(account_id, captured_at DESC);
