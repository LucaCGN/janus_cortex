-- v0.8.7: NBA team-season rollups for strategy-facing regular-season aggregates

CREATE TABLE IF NOT EXISTS nba.nba_team_feature_rollups (
    team_id INTEGER NOT NULL REFERENCES nba.nba_teams(team_id) ON DELETE CASCADE,
    season TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    feature_version TEXT NOT NULL,
    sample_games INTEGER,
    covered_games INTEGER,
    wins INTEGER,
    losses INTEGER,
    avg_lead_changes NUMERIC(10,4),
    avg_losing_segments NUMERIC(10,4),
    avg_largest_lead_in_losses NUMERIC(10,4),
    losses_after_leading INTEGER,
    underdog_games_with_coverage INTEGER,
    favorite_games_with_coverage INTEGER,
    avg_underdog_in_game_range NUMERIC(10,6),
    avg_favorite_in_game_range NUMERIC(10,6),
    classification_tags_json JSONB,
    notes_json JSONB,
    PRIMARY KEY (team_id, season, computed_at, feature_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_nba_team_feature_rollups_team_season_version
    ON nba.nba_team_feature_rollups(team_id, season, feature_version);

CREATE INDEX IF NOT EXISTS ix_nba_team_feature_rollups_team_season_computed_desc
    ON nba.nba_team_feature_rollups(team_id, season, computed_at DESC);
