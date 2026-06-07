-- v1.1.8: portfolio-manager four-pillar liquidity, price-memory, and worker state

ALTER TABLE portfolio.manager_slots
    ADD COLUMN IF NOT EXISTS premise_state TEXT NOT NULL DEFAULT 'unreviewed',
    ADD COLUMN IF NOT EXISTS hygiene_bucket TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS market_volume_usd NUMERIC(18, 4),
    ADD COLUMN IF NOT EXISTS volume_24h_usd NUMERIC(18, 4),
    ADD COLUMN IF NOT EXISTS price_bucket TEXT,
    ADD COLUMN IF NOT EXISTS cashout_score INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS price_memory_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS worker_allocation_json JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE portfolio.manager_slot_reviews
    ADD COLUMN IF NOT EXISTS premise_state TEXT,
    ADD COLUMN IF NOT EXISTS price_memory_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS worker_allocation_json JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE portfolio.manager_budget_snapshots
    ADD COLUMN IF NOT EXISTS base_sleeve_cap_usd NUMERIC(14, 4) NOT NULL DEFAULT 50.0000,
    ADD COLUMN IF NOT EXISTS realized_global_pnl_usd NUMERIC(14, 4) NOT NULL DEFAULT 0.0000,
    ADD COLUMN IF NOT EXISTS dynamic_sleeve_cap_usd NUMERIC(14, 4) NOT NULL DEFAULT 50.0000,
    ADD COLUMN IF NOT EXISTS budget_source_confidence TEXT NOT NULL DEFAULT 'account_confirmed_or_zero',
    ADD COLUMN IF NOT EXISTS budget_source_caveats JSONB NOT NULL DEFAULT '[]'::JSONB;

ALTER TABLE portfolio.manager_candidate_queue
    ADD COLUMN IF NOT EXISTS market_volume_usd NUMERIC(18, 4),
    ADD COLUMN IF NOT EXISTS volume_24h_usd NUMERIC(18, 4),
    ADD COLUMN IF NOT EXISTS liquidity_usd NUMERIC(18, 4),
    ADD COLUMN IF NOT EXISTS price_bucket TEXT,
    ADD COLUMN IF NOT EXISTS low_price_score INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cashout_score INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS price_history_features JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS grid_backtest_summary JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE portfolio.manager_grid_eligibility_reviews
    ADD COLUMN IF NOT EXISTS validator_profile TEXT NOT NULL DEFAULT 'legacy_30d',
    ADD COLUMN IF NOT EXISTS premise_state TEXT NOT NULL DEFAULT 'unreviewed',
    ADD COLUMN IF NOT EXISTS one_day_range_cents NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS seven_day_range_cents NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS thirty_day_range_cents NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS one_day_backtest_positive BOOLEAN,
    ADD COLUMN IF NOT EXISTS three_day_backtest_positive BOOLEAN,
    ADD COLUMN IF NOT EXISTS seven_day_backtest_positive BOOLEAN,
    ADD COLUMN IF NOT EXISTS recommended_worker_duration_hours INTEGER,
    ADD COLUMN IF NOT EXISTS backtest_json JSONB NOT NULL DEFAULT '{}'::JSONB;

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_slots_premise_hygiene_updated
    ON portfolio.manager_slots(premise_state, hygiene_bucket, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_candidate_queue_volume_price
    ON portfolio.manager_candidate_queue(market_volume_usd DESC, price_bucket, score DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_grid_reviews_validator_created
    ON portfolio.manager_grid_eligibility_reviews(validator_profile, status, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_market_registry (
    registry_id UUID PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    market_title TEXT NOT NULL,
    market_slug TEXT,
    token_id TEXT,
    outcome TEXT,
    domain TEXT,
    source TEXT NOT NULL,
    current_price NUMERIC(14, 6),
    market_volume_usd NUMERIC(18, 4),
    volume_24h_usd NUMERIC(18, 4),
    liquidity_usd NUMERIC(18, 4),
    premise_state TEXT NOT NULL DEFAULT 'unreviewed',
    slot_state TEXT NOT NULL DEFAULT 'candidate',
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    registry_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_portfolio_manager_market_registry_token_source
    ON portfolio.manager_market_registry(source, COALESCE(token_id, ''), COALESCE(market_slug, ''));

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_market_registry_seen
    ON portfolio.manager_market_registry(last_seen_at DESC, market_volume_usd DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_price_feature_snapshots (
    snapshot_id UUID PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    market_slug TEXT,
    token_id TEXT,
    outcome TEXT,
    source TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    one_day_range_cents NUMERIC(10, 4),
    seven_day_range_cents NUMERIC(10, 4),
    thirty_day_range_cents NUMERIC(10, 4),
    one_day_trend_cents NUMERIC(10, 4),
    seven_day_trend_cents NUMERIC(10, 4),
    thirty_day_trend_cents NUMERIC(10, 4),
    features_json JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_price_features_token_captured
    ON portfolio.manager_price_feature_snapshots(token_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_price_features_market_captured
    ON portfolio.manager_price_feature_snapshots(market_slug, captured_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_strategy_workers (
    worker_id TEXT PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    slot_id TEXT REFERENCES portfolio.manager_slots(slot_id) ON DELETE SET NULL,
    market_slug TEXT,
    token_id TEXT,
    strategy_mode TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'planned',
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    premise_state TEXT NOT NULL DEFAULT 'unreviewed',
    grid_step_price NUMERIC(14, 6),
    review_after_at TIMESTAMPTZ,
    max_notional_usd NUMERIC(14, 4),
    config_json JSONB NOT NULL,
    last_heartbeat_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    order_preparation_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    order_submission_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_strategy_workers_state_updated
    ON portfolio.manager_strategy_workers(state, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_strategy_workers_token_mode
    ON portfolio.manager_strategy_workers(token_id, strategy_mode, updated_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_strategy_worker_ledger (
    ledger_id UUID PRIMARY KEY,
    worker_id TEXT REFERENCES portfolio.manager_strategy_workers(worker_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_status TEXT NOT NULL,
    idempotency_key TEXT,
    event_json JSONB NOT NULL,
    order_preparation_attempted BOOLEAN NOT NULL DEFAULT FALSE,
    order_submission_attempted BOOLEAN NOT NULL DEFAULT FALSE,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_strategy_worker_ledger_worker_recorded
    ON portfolio.manager_strategy_worker_ledger(worker_id, recorded_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_portfolio_manager_strategy_worker_ledger_idempotency
    ON portfolio.manager_strategy_worker_ledger(worker_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
