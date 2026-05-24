-- v1.1.6: portfolio-manager 20-slot durable state system

CREATE TABLE IF NOT EXISTS portfolio.manager_slots (
    slot_id TEXT PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    slot_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    market_title TEXT NOT NULL,
    market_slug TEXT,
    outcome TEXT,
    side TEXT,
    token_id TEXT,
    source_actor TEXT NOT NULL DEFAULT 'unknown',
    risk_cap_usd NUMERIC(14, 4) NOT NULL DEFAULT 5.0000,
    horizon TEXT NOT NULL DEFAULT 'unknown',
    confidence TEXT NOT NULL DEFAULT 'unknown',
    thesis TEXT,
    premises JSONB NOT NULL DEFAULT '[]'::JSONB,
    invalidation_signals JSONB NOT NULL DEFAULT '[]'::JSONB,
    watch_points JSONB NOT NULL DEFAULT '[]'::JSONB,
    target_stop_rebuy JSONB NOT NULL DEFAULT '{}'::JSONB,
    source_evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    direct_truth JSONB NOT NULL DEFAULT '{}'::JSONB,
    latest_action_state TEXT NOT NULL DEFAULT 'needs_review',
    obsidian_note_path TEXT,
    slot_json JSONB NOT NULL,
    last_reconciled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_slots_account_status
    ON portfolio.manager_slots(account_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_slots_token
    ON portfolio.manager_slots(token_id);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_slots_market_slug
    ON portfolio.manager_slots(market_slug);

CREATE TABLE IF NOT EXISTS portfolio.manager_slot_reviews (
    review_id UUID PRIMARY KEY,
    slot_id TEXT REFERENCES portfolio.manager_slots(slot_id) ON DELETE CASCADE,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    review_status TEXT NOT NULL,
    thesis_state TEXT,
    premise_changes JSONB NOT NULL DEFAULT '[]'::JSONB,
    invalidating_signals_seen JSONB NOT NULL DEFAULT '[]'::JSONB,
    action_plan JSONB NOT NULL DEFAULT '{}'::JSONB,
    review_json JSONB NOT NULL,
    reviewed_by TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_slot_reviews_slot_created
    ON portfolio.manager_slot_reviews(slot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_budget_snapshots (
    snapshot_id UUID PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    target_slot_count INTEGER NOT NULL DEFAULT 20,
    managed_slot_count INTEGER NOT NULL DEFAULT 0,
    empty_slot_count INTEGER NOT NULL DEFAULT 20,
    equity_usd NUMERIC(14, 4),
    cash_usd NUMERIC(14, 4),
    codex_sleeve_cap_usd NUMERIC(14, 4) NOT NULL DEFAULT 50.0000,
    codex_sleeve_max_equity_fraction NUMERIC(8, 4) NOT NULL DEFAULT 0.5000,
    effective_sleeve_cap_usd NUMERIC(14, 4) NOT NULL DEFAULT 50.0000,
    codex_sleeve_usage_usd NUMERIC(14, 4) NOT NULL DEFAULT 0.0000,
    codex_sleeve_remaining_usd NUMERIC(14, 4) NOT NULL DEFAULT 50.0000,
    per_position_cap_usd NUMERIC(14, 4) NOT NULL DEFAULT 5.0000,
    target_average_slot_notional_usd NUMERIC(14, 4) NOT NULL DEFAULT 2.5000,
    budget_status TEXT NOT NULL,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_budget_snapshots_account_created
    ON portfolio.manager_budget_snapshots(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_candidate_queue (
    candidate_id TEXT PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    market_title TEXT NOT NULL,
    market_slug TEXT,
    outcome TEXT,
    category TEXT,
    token_id TEXT,
    proposed_side TEXT NOT NULL DEFAULT 'buy',
    proposed_price NUMERIC(14, 6),
    proposed_size NUMERIC(18, 6),
    proposed_notional_usd NUMERIC(14, 4),
    horizon TEXT NOT NULL DEFAULT 'unknown',
    confidence TEXT NOT NULL DEFAULT 'unknown',
    score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    rejection_reasons JSONB NOT NULL DEFAULT '[]'::JSONB,
    edge_summary TEXT,
    source_url TEXT,
    direct_orderbook JSONB NOT NULL DEFAULT '{}'::JSONB,
    profile_signal JSONB NOT NULL DEFAULT '{}'::JSONB,
    top_holder_signal JSONB NOT NULL DEFAULT '{}'::JSONB,
    candidate_json JSONB NOT NULL,
    last_scored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_candidate_queue_account_status_score
    ON portfolio.manager_candidate_queue(account_id, status, score DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_candidate_queue_source
    ON portfolio.manager_candidate_queue(source, updated_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_profile_observations (
    observation_id UUID PRIMARY KEY,
    profile_name TEXT NOT NULL,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    source_url TEXT,
    observation_type TEXT NOT NULL,
    market_title TEXT,
    market_slug TEXT,
    side TEXT,
    token_id TEXT,
    profit_usd NUMERIC(14, 4),
    observation_json JSONB NOT NULL,
    promoted_candidate_id TEXT REFERENCES portfolio.manager_candidate_queue(candidate_id) ON DELETE SET NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_profile_observations_profile_observed
    ON portfolio.manager_profile_observations(profile_name, observed_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_top_holder_scans (
    scan_id UUID PRIMARY KEY,
    market_title TEXT NOT NULL,
    market_slug TEXT,
    source_url TEXT,
    yes_holders_seen INTEGER NOT NULL DEFAULT 0,
    no_holders_seen INTEGER NOT NULL DEFAULT 0,
    high_profit_profile_count INTEGER NOT NULL DEFAULT 0,
    high_profit_profiles JSONB NOT NULL DEFAULT '[]'::JSONB,
    scan_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_top_holder_scans_market_created
    ON portfolio.manager_top_holder_scans(market_slug, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.manager_grid_eligibility_reviews (
    review_id UUID PRIMARY KEY,
    account_id UUID REFERENCES portfolio.trading_accounts(account_id) ON DELETE SET NULL,
    slot_id TEXT REFERENCES portfolio.manager_slots(slot_id) ON DELETE SET NULL,
    market_title TEXT NOT NULL,
    market_slug TEXT,
    token_id TEXT,
    thirty_day_range_percent NUMERIC(10, 4),
    days_to_resolution INTEGER,
    stable_thesis BOOLEAN NOT NULL DEFAULT FALSE,
    spread_cents NUMERIC(10, 4),
    depth_usd NUMERIC(14, 4),
    near_binary_catalyst BOOLEAN NOT NULL DEFAULT FALSE,
    explicit_service_spawn_approval BOOLEAN NOT NULL DEFAULT FALSE,
    eligible BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL,
    blockers JSONB NOT NULL DEFAULT '[]'::JSONB,
    review_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_grid_reviews_market_created
    ON portfolio.manager_grid_eligibility_reviews(market_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_grid_reviews_status_created
    ON portfolio.manager_grid_eligibility_reviews(status, created_at DESC);
