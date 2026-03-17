-- v0.8.1: persisted NBA regular-season game feature snapshots

CREATE TABLE IF NOT EXISTS nba.nba_game_feature_snapshots (
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    event_id UUID REFERENCES catalog.events(event_id) ON DELETE SET NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    feature_version TEXT NOT NULL,
    season TEXT NOT NULL,
    team_context_mode TEXT NOT NULL DEFAULT 'full_game',
    pbp_event_count INTEGER,
    lead_changes INTEGER,
    home_largest_lead INTEGER,
    away_largest_lead INTEGER,
    home_losing_segments INTEGER,
    away_losing_segments INTEGER,
    home_led_and_lost BOOLEAN,
    away_led_and_lost BOOLEAN,
    covered_polymarket_game_flag BOOLEAN NOT NULL DEFAULT FALSE,
    home_pre_game_price_min NUMERIC(10,6),
    home_pre_game_price_max NUMERIC(10,6),
    away_pre_game_price_min NUMERIC(10,6),
    away_pre_game_price_max NUMERIC(10,6),
    home_in_game_price_min NUMERIC(10,6),
    home_in_game_price_max NUMERIC(10,6),
    away_in_game_price_min NUMERIC(10,6),
    away_in_game_price_max NUMERIC(10,6),
    price_window_start TIMESTAMPTZ,
    price_window_end TIMESTAMPTZ,
    coverage_status TEXT,
    source_summary_json JSONB,
    PRIMARY KEY (game_id, computed_at, feature_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_nba_game_feature_snapshots_game_mode_version
    ON nba.nba_game_feature_snapshots(game_id, season, team_context_mode, feature_version);

CREATE INDEX IF NOT EXISTS ix_nba_game_feature_snapshots_game_computed_desc
    ON nba.nba_game_feature_snapshots(game_id, computed_at DESC);

CREATE INDEX IF NOT EXISTS ix_nba_game_feature_snapshots_season_coverage_computed_desc
    ON nba.nba_game_feature_snapshots(season, coverage_status, computed_at DESC);
