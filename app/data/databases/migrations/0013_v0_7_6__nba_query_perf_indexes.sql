-- v0.7.6: nba query tuning indexes for live/context read paths

CREATE INDEX IF NOT EXISTS ix_nba_games_status_date_start_desc
    ON nba.nba_games(game_status, game_date DESC, game_start_time DESC);

CREATE INDEX IF NOT EXISTS ix_nba_play_by_play_game_event_desc
    ON nba.nba_play_by_play(game_id, event_index DESC);

CREATE INDEX IF NOT EXISTS ix_nba_team_stats_team_season_metric_captured_desc
    ON nba.nba_team_stats_snapshots(team_id, season, metric_set, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_nba_team_insights_team_captured_desc
    ON nba.nba_team_insights(team_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_nba_player_stats_team_season_captured_desc
    ON nba.nba_player_stats_snapshots(team_id, season, captured_at DESC);
