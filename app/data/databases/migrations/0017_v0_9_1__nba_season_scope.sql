-- v0.9.1: normalize NBA season-phase scope across games, snapshots, insights, and rollups

ALTER TABLE nba.nba_games
    ADD COLUMN IF NOT EXISTS season_phase TEXT NOT NULL DEFAULT 'regular_season',
    ADD COLUMN IF NOT EXISTS season_phase_label TEXT,
    ADD COLUMN IF NOT EXISTS season_phase_sub_label TEXT,
    ADD COLUMN IF NOT EXISTS season_phase_subtype TEXT,
    ADD COLUMN IF NOT EXISTS series_text TEXT,
    ADD COLUMN IF NOT EXISTS series_game_number TEXT;

UPDATE nba.nba_games
SET season_phase = CASE substring(game_id from 1 for 3)
    WHEN '001' THEN 'preseason'
    WHEN '002' THEN 'regular_season'
    WHEN '003' THEN 'all_star'
    WHEN '004' THEN 'playoffs'
    WHEN '005' THEN 'play_in'
    WHEN '006' THEN 'nba_cup'
    ELSE 'other'
END
WHERE season_phase IS NULL
   OR season_phase = ''
   OR season_phase = 'regular_season';

CREATE INDEX IF NOT EXISTS ix_nba_games_season_phase_date_status
    ON nba.nba_games(season, season_phase, game_date DESC, game_status);

ALTER TABLE nba.nba_team_stats_snapshots
    ADD COLUMN IF NOT EXISTS season_type TEXT NOT NULL DEFAULT 'Regular Season',
    ADD COLUMN IF NOT EXISTS season_phase TEXT NOT NULL DEFAULT 'regular_season';

ALTER TABLE nba.nba_player_stats_snapshots
    ADD COLUMN IF NOT EXISTS season_type TEXT NOT NULL DEFAULT 'Regular Season',
    ADD COLUMN IF NOT EXISTS season_phase TEXT NOT NULL DEFAULT 'regular_season';

ALTER TABLE nba.nba_team_stats_snapshots
    DROP CONSTRAINT IF EXISTS nba_team_stats_snapshots_pkey;
ALTER TABLE nba.nba_team_stats_snapshots
    ADD CONSTRAINT nba_team_stats_snapshots_pkey
    PRIMARY KEY (team_id, season, season_type, captured_at, metric_set);

ALTER TABLE nba.nba_player_stats_snapshots
    DROP CONSTRAINT IF EXISTS nba_player_stats_snapshots_pkey;
ALTER TABLE nba.nba_player_stats_snapshots
    ADD CONSTRAINT nba_player_stats_snapshots_pkey
    PRIMARY KEY (player_id, season, season_type, captured_at, metric_set);

CREATE INDEX IF NOT EXISTS ix_nba_team_stats_team_season_phase_metric_captured_desc
    ON nba.nba_team_stats_snapshots(team_id, season, season_phase, metric_set, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_nba_player_stats_team_season_phase_captured_desc
    ON nba.nba_player_stats_snapshots(team_id, season, season_phase, captured_at DESC);

ALTER TABLE nba.nba_team_insights
    ADD COLUMN IF NOT EXISTS season TEXT,
    ADD COLUMN IF NOT EXISTS season_type TEXT,
    ADD COLUMN IF NOT EXISTS season_phase TEXT;

CREATE INDEX IF NOT EXISTS ix_nba_team_insights_team_season_phase_captured_desc
    ON nba.nba_team_insights(team_id, season, season_phase, captured_at DESC);

ALTER TABLE nba.nba_team_feature_rollups
    ADD COLUMN IF NOT EXISTS season_phase TEXT NOT NULL DEFAULT 'regular_season';

DROP INDEX IF EXISTS nba.uq_nba_team_feature_rollups_team_season_version;
CREATE UNIQUE INDEX IF NOT EXISTS uq_nba_team_feature_rollups_team_season_phase_version
    ON nba.nba_team_feature_rollups(team_id, season, season_phase, feature_version);

CREATE INDEX IF NOT EXISTS ix_nba_team_feature_rollups_team_season_phase_computed_desc
    ON nba.nba_team_feature_rollups(team_id, season, season_phase, computed_at DESC);
