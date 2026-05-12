-- v1.1.4: WNBA Polymarket closed-event price history for first-level historical backtests.

CREATE SCHEMA IF NOT EXISTS wnba;

CREATE TABLE IF NOT EXISTS wnba.wnba_polymarket_price_history (
    token_id TEXT NOT NULL,
    price_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL DEFAULT 'polymarket_clob_prices_history',
    game_id TEXT REFERENCES wnba.wnba_games(game_id) ON DELETE SET NULL,
    team_side TEXT,
    team_tricode TEXT,
    event_slug TEXT,
    market_id TEXT,
    condition_id TEXT,
    outcome TEXT,
    price NUMERIC(10, 6) NOT NULL,
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    spread NUMERIC(10, 6),
    fidelity_minutes INTEGER,
    raw_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (token_id, price_at, source),
    CONSTRAINT ck_wnba_polymarket_price_history_side
        CHECK (team_side IS NULL OR team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_wnba_polymarket_price_history_game_side
    ON wnba.wnba_polymarket_price_history(game_id, team_side, price_at);

CREATE INDEX IF NOT EXISTS ix_wnba_polymarket_price_history_market
    ON wnba.wnba_polymarket_price_history(event_slug, market_id, token_id);


CREATE TABLE IF NOT EXISTS wnba.wnba_price_history_backtest_runs (
    backtest_run_id UUID PRIMARY KEY,
    season TEXT,
    season_phase TEXT,
    analysis_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL,
    game_id TEXT REFERENCES wnba.wnba_games(game_id) ON DELETE SET NULL,
    event_slug TEXT,
    market_id TEXT,
    state_panel_rows INTEGER NOT NULL DEFAULT 0,
    price_history_rows INTEGER NOT NULL DEFAULT 0,
    lane_count INTEGER NOT NULL DEFAULT 0,
    complete_lane_count INTEGER NOT NULL DEFAULT 0,
    blocked_lane_count INTEGER NOT NULL DEFAULT 0,
    blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_wnba_price_history_backtest_runs_status
    ON wnba.wnba_price_history_backtest_runs(status, created_at DESC);
