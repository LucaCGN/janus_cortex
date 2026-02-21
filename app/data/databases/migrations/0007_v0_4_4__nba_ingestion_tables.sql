-- v0.4.4: nba metadata/live ingestion tables

CREATE SCHEMA IF NOT EXISTS nba;

CREATE TABLE IF NOT EXISTS nba.nba_teams (
    team_id INTEGER PRIMARY KEY,
    team_slug TEXT NOT NULL,
    team_name TEXT NOT NULL,
    team_city TEXT,
    conference TEXT,
    division TEXT,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nba.nba_games (
    game_id TEXT PRIMARY KEY,
    season TEXT,
    game_date DATE,
    game_start_time TIMESTAMPTZ,
    game_status INTEGER,
    game_status_text TEXT,
    period INTEGER,
    game_clock TEXT,
    home_team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    away_team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    home_team_slug TEXT,
    away_team_slug TEXT,
    home_score INTEGER,
    away_score INTEGER,
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS nba.nba_game_event_links (
    nba_game_event_link_id UUID PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES catalog.events(event_id) ON DELETE CASCADE,
    confidence NUMERIC(5, 4),
    linked_by TEXT,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_nba_game_event_links_game_event
    ON nba.nba_game_event_links(game_id, event_id);

CREATE TABLE IF NOT EXISTS nba.nba_team_stats_snapshots (
    team_id INTEGER NOT NULL REFERENCES nba.nba_teams(team_id) ON DELETE CASCADE,
    season TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    metric_set TEXT NOT NULL,
    stats_json JSONB NOT NULL,
    source TEXT,
    PRIMARY KEY (team_id, season, captured_at, metric_set)
);

CREATE TABLE IF NOT EXISTS nba.nba_player_stats_snapshots (
    player_id INTEGER NOT NULL,
    player_name TEXT,
    team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    season TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    metric_set TEXT NOT NULL,
    stats_json JSONB NOT NULL,
    source TEXT,
    PRIMARY KEY (player_id, season, captured_at, metric_set)
);

CREATE TABLE IF NOT EXISTS nba.nba_team_insights (
    insight_id UUID PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES nba.nba_teams(team_id) ON DELETE CASCADE,
    insight_type TEXT,
    category TEXT,
    text TEXT,
    condition TEXT,
    value TEXT,
    source TEXT,
    captured_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS nba.nba_live_game_snapshots (
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    period INTEGER,
    clock TEXT,
    home_score INTEGER,
    away_score INTEGER,
    payload_json JSONB,
    PRIMARY KEY (game_id, captured_at)
);

CREATE TABLE IF NOT EXISTS nba.nba_play_by_play (
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    event_index BIGINT NOT NULL,
    action_id TEXT,
    period INTEGER,
    clock TEXT,
    description TEXT,
    home_score INTEGER,
    away_score INTEGER,
    is_score_change BOOLEAN,
    payload_json JSONB,
    PRIMARY KEY (game_id, event_index)
);

CREATE INDEX IF NOT EXISTS ix_nba_games_game_date_status
    ON nba.nba_games(game_date, game_status);

CREATE INDEX IF NOT EXISTS ix_nba_game_event_links_game_id
    ON nba.nba_game_event_links(game_id);

CREATE INDEX IF NOT EXISTS ix_nba_game_event_links_event_id
    ON nba.nba_game_event_links(event_id);

CREATE INDEX IF NOT EXISTS ix_nba_live_game_snapshots_game_captured_desc
    ON nba.nba_live_game_snapshots(game_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_nba_play_by_play_game_period_event
    ON nba.nba_play_by_play(game_id, period, event_index);
