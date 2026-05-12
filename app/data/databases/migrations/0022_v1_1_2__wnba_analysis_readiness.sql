-- v1.1.2: WNBA data sufficiency, ML feature, and shadow backtest metadata tables.

CREATE SCHEMA IF NOT EXISTS wnba;

CREATE TABLE IF NOT EXISTS wnba.wnba_data_sufficiency_audits (
    audit_id UUID PRIMARY KEY,
    season TEXT NOT NULL,
    season_phase TEXT,
    audited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL,
    schedule_games INTEGER,
    games_with_boxscore INTEGER,
    games_with_play_by_play INTEGER,
    play_by_play_rows INTEGER,
    player_boxscore_rows INTEGER,
    market_link_count INTEGER,
    clob_tick_count INTEGER,
    clob_trade_count INTEGER,
    state_panel_rows INTEGER,
    ml_feature_rows INTEGER,
    lane_readiness_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    ml_readiness_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_wnba_data_sufficiency_audits_season_status
    ON wnba.wnba_data_sufficiency_audits(season, status, audited_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_pbp_ml_feature_rows (
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    team_side TEXT NOT NULL,
    state_index INTEGER NOT NULL,
    feature_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    period INTEGER,
    clock TEXT,
    seconds_to_game_end NUMERIC(12, 3),
    score_diff INTEGER,
    recent_net_points INTEGER,
    action_type TEXT,
    sub_type TEXT,
    player_id INTEGER,
    player_name TEXT,
    team_tricode TEXT,
    opponent_tricode TEXT,
    team_price NUMERIC(10, 6),
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    spread NUMERIC(10, 6),
    label_horizon_states INTEGER,
    label_price_delta NUMERIC(10, 6),
    label_up_2c BOOLEAN,
    label_down_2c BOOLEAN,
    label_crossed_50c BOOLEAN,
    label_status TEXT NOT NULL,
    features_json JSONB,
    raw_state_json JSONB,
    PRIMARY KEY (game_id, team_side, state_index, feature_version),
    CONSTRAINT ck_wnba_pbp_ml_feature_rows_side
        CHECK (team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_wnba_pbp_ml_feature_rows_feature_status
    ON wnba.wnba_pbp_ml_feature_rows(feature_version, label_status, computed_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_shadow_backtest_runs (
    backtest_run_id UUID PRIMARY KEY,
    season TEXT,
    season_phase TEXT,
    lane_id TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL,
    sample_states INTEGER,
    trade_count INTEGER,
    win_rate NUMERIC(10, 6),
    avg_return NUMERIC(10, 6),
    total_return NUMERIC(10, 6),
    blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    config_json JSONB,
    result_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_wnba_shadow_backtest_runs_lane_status
    ON wnba.wnba_shadow_backtest_runs(lane_id, status, created_at DESC);
