-- v1.1.0: generic market watch/replay and agentic strategy-plan persistence.

CREATE SCHEMA IF NOT EXISTS agentic;

CREATE TABLE IF NOT EXISTS agentic.market_events (
    market_event_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'polymarket',
    title TEXT NOT NULL,
    status TEXT,
    source_urls_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    liquidity NUMERIC(18, 6),
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentic.market_outcomes (
    market_outcome_id UUID PRIMARY KEY,
    market_event_id UUID REFERENCES agentic.market_events(market_event_id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    market_id TEXT,
    outcome_id TEXT,
    token_id TEXT,
    label TEXT NOT NULL,
    side TEXT,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentic.market_orderbook_ticks (
    market_orderbook_tick_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL,
    market_id TEXT,
    outcome_id TEXT,
    token_id TEXT,
    captured_at TIMESTAMPTZ NOT NULL,
    source_timestamp TIMESTAMPTZ,
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    spread NUMERIC(10, 6),
    mid_price NUMERIC(10, 6),
    bid_depth NUMERIC(18, 6),
    ask_depth NUMERIC(18, 6),
    source_latency_ms NUMERIC(12, 3),
    ingest_latency_ms NUMERIC(12, 3),
    levels_json JSONB,
    raw_json JSONB
);

CREATE TABLE IF NOT EXISTS agentic.market_trades (
    market_trade_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL,
    market_id TEXT,
    outcome_id TEXT,
    token_id TEXT,
    external_trade_id TEXT,
    trade_time TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    side TEXT,
    price NUMERIC(10, 6),
    size NUMERIC(18, 6),
    source_latency_ms NUMERIC(12, 3),
    raw_json JSONB
);

CREATE TABLE IF NOT EXISTS agentic.market_watch_sessions (
    watch_session_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL,
    category TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    cadence_ms INTEGER,
    passive_only BOOLEAN NOT NULL DEFAULT TRUE,
    reason TEXT,
    gap_summary_json JSONB,
    provider_errors_json JSONB,
    metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS agentic.operator_interventions (
    operator_intervention_id UUID PRIMARY KEY,
    event_key TEXT,
    market_id TEXT,
    account_id TEXT,
    detected_at TIMESTAMPTZ NOT NULL,
    action TEXT NOT NULL,
    external_order_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    reconciliation_action TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    raw_json JSONB
);

CREATE TABLE IF NOT EXISTS agentic.strategy_plan_versions (
    strategy_plan_version_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL,
    market_id TEXT,
    schema_version TEXT NOT NULL,
    plan_owner TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ,
    active_strategy_count INTEGER NOT NULL DEFAULT 0,
    plan_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentic.strategy_decisions (
    strategy_decision_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL,
    strategy_plan_version_id UUID REFERENCES agentic.strategy_plan_versions(strategy_plan_version_id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    strategy_id TEXT,
    decision_type TEXT NOT NULL,
    order_intent_json JSONB,
    blockers_json JSONB,
    fill_json JSONB,
    exit_json JSONB,
    hedge_json JSONB,
    raw_json JSONB
);

CREATE TABLE IF NOT EXISTS agentic.replay_sessions (
    replay_session_id UUID PRIMARY KEY,
    watch_session_id UUID REFERENCES agentic.market_watch_sessions(watch_session_id) ON DELETE SET NULL,
    event_key TEXT,
    output_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_tick_count INTEGER,
    source_trade_count INTEGER,
    latency_summary_json JSONB,
    replay_config_json JSONB,
    output_root TEXT
);

CREATE INDEX IF NOT EXISTS ix_agentic_market_events_category_status
    ON agentic.market_events(category, status, start_time DESC);

CREATE INDEX IF NOT EXISTS ix_agentic_market_orderbook_ticks_event_captured
    ON agentic.market_orderbook_ticks(event_key, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_agentic_market_trades_event_time
    ON agentic.market_trades(event_key, trade_time DESC);

CREATE INDEX IF NOT EXISTS ix_agentic_watch_sessions_event_started
    ON agentic.market_watch_sessions(event_key, started_at DESC);

CREATE INDEX IF NOT EXISTS ix_agentic_strategy_plan_versions_event_generated
    ON agentic.strategy_plan_versions(event_key, generated_at DESC);
