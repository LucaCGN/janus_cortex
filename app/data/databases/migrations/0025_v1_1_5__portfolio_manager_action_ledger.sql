-- v1.1.5: durable portfolio-manager action-plan ledger

CREATE TABLE IF NOT EXISTS portfolio.manager_action_ledger (
    ledger_id UUID PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    issue TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT NOT NULL,
    market_title TEXT,
    market_slug TEXT,
    token_id TEXT,
    execution_authorized BOOLEAN NOT NULL DEFAULT FALSE,
    order_preparation_authorized BOOLEAN NOT NULL DEFAULT FALSE,
    live_order_impact TEXT NOT NULL DEFAULT 'read-only',
    missing_gates TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    rejected_truth_sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ledger_record JSONB NOT NULL,
    source_plan JSONB NOT NULL,
    reviewed_by TEXT,
    reason TEXT,
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_action_ledger_issue_created
    ON portfolio.manager_action_ledger(issue, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_action_ledger_account_created
    ON portfolio.manager_action_ledger(account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_action_ledger_status_created
    ON portfolio.manager_action_ledger(status, created_at DESC);
