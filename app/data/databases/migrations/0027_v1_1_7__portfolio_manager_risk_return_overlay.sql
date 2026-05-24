-- v1.1.7: portfolio-manager risk/return and scalability overlay

ALTER TABLE portfolio.manager_candidate_queue
    ADD COLUMN IF NOT EXISTS strategy_style TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS expected_hold_days INTEGER,
    ADD COLUMN IF NOT EXISTS expected_return_cents NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS expected_return_on_notional_percent NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS estimated_entry_slippage_cents NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS slippage_to_edge_ratio NUMERIC(12, 6),
    ADD COLUMN IF NOT EXISTS liquidity_capacity_usd NUMERIC(14, 4),
    ADD COLUMN IF NOT EXISTS payoff_velocity_score INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sizing_tier TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS sizing_guidance JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS risk_return_flags JSONB NOT NULL DEFAULT '[]'::JSONB;

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_candidate_queue_sizing_tier_updated
    ON portfolio.manager_candidate_queue(sizing_tier, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_portfolio_manager_candidate_queue_strategy_score
    ON portfolio.manager_candidate_queue(strategy_style, score DESC, updated_at DESC);
