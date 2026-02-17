-- v0.3.3: portfolio MVP tables and query indexes

CREATE SCHEMA IF NOT EXISTS portfolio;

CREATE TABLE IF NOT EXISTS portfolio.trading_accounts (
    account_id UUID PRIMARY KEY,
    provider_id UUID NOT NULL REFERENCES core.providers(provider_id) ON DELETE RESTRICT,
    account_label TEXT NOT NULL,
    wallet_address TEXT,
    proxy_wallet_address TEXT,
    chain_id INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio.position_snapshots (
    account_id UUID NOT NULL REFERENCES portfolio.trading_accounts(account_id) ON DELETE CASCADE,
    outcome_id UUID NOT NULL REFERENCES catalog.outcomes(outcome_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    size NUMERIC(18, 6),
    avg_price NUMERIC(10, 6),
    current_price NUMERIC(10, 6),
    current_value NUMERIC(18, 6),
    unrealized_pnl NUMERIC(18, 6),
    realized_pnl NUMERIC(18, 6),
    raw_json JSONB,
    PRIMARY KEY (account_id, outcome_id, captured_at, source)
);

CREATE TABLE IF NOT EXISTS portfolio.orders (
    order_id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES portfolio.trading_accounts(account_id) ON DELETE RESTRICT,
    market_id UUID NOT NULL REFERENCES catalog.markets(market_id) ON DELETE RESTRICT,
    outcome_id UUID REFERENCES catalog.outcomes(outcome_id) ON DELETE RESTRICT,
    external_order_id TEXT,
    client_order_id TEXT,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    time_in_force TEXT,
    limit_price NUMERIC(10, 6),
    size NUMERIC(18, 6),
    status TEXT NOT NULL,
    placed_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS portfolio.trades (
    trade_id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES portfolio.trading_accounts(account_id) ON DELETE RESTRICT,
    order_id UUID REFERENCES portfolio.orders(order_id) ON DELETE SET NULL,
    market_id UUID NOT NULL REFERENCES catalog.markets(market_id) ON DELETE RESTRICT,
    outcome_id UUID REFERENCES catalog.outcomes(outcome_id) ON DELETE RESTRICT,
    external_trade_id TEXT,
    tx_hash TEXT,
    side TEXT NOT NULL,
    price NUMERIC(10, 6),
    size NUMERIC(18, 6),
    fee NUMERIC(18, 6),
    fee_asset TEXT,
    liquidity_role TEXT,
    trade_time TIMESTAMPTZ NOT NULL,
    raw_json JSONB
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_orders_account_client_order
    ON portfolio.orders(account_id, client_order_id)
    WHERE client_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_portfolio_orders_account_status_placed_at
    ON portfolio.orders(account_id, status, placed_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_trades_account_trade_time
    ON portfolio.trades(account_id, trade_time DESC);

