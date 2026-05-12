-- v1.1.1: WNBA data foundation for schedule, live context, replay, and passive market watch.

CREATE SCHEMA IF NOT EXISTS wnba;

CREATE TABLE IF NOT EXISTS wnba.wnba_teams (
    team_id INTEGER PRIMARY KEY,
    team_slug TEXT,
    team_tricode TEXT,
    team_name TEXT NOT NULL,
    team_city TEXT,
    conference TEXT,
    division TEXT,
    source TEXT NOT NULL DEFAULT 'wnba_cdn',
    fetched_at TIMESTAMPTZ,
    raw_payload_json JSONB,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wnba_teams_tricode
    ON wnba.wnba_teams(team_tricode)
    WHERE team_tricode IS NOT NULL;


CREATE TABLE IF NOT EXISTS wnba.wnba_players (
    player_id INTEGER PRIMARY KEY,
    player_name TEXT NOT NULL,
    first_name TEXT,
    family_name TEXT,
    team_id INTEGER REFERENCES wnba.wnba_teams(team_id) ON DELETE SET NULL,
    team_tricode TEXT,
    jersey_num TEXT,
    position TEXT,
    status TEXT,
    source TEXT NOT NULL DEFAULT 'wnba_cdn',
    fetched_at TIMESTAMPTZ,
    raw_payload_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_wnba_players_team
    ON wnba.wnba_players(team_id, player_name);


CREATE TABLE IF NOT EXISTS wnba.wnba_games (
    game_id TEXT PRIMARY KEY,
    season TEXT NOT NULL,
    league_id TEXT NOT NULL DEFAULT '10',
    game_code TEXT,
    game_date DATE,
    game_start_time TIMESTAMPTZ,
    game_status INTEGER,
    game_status_text TEXT,
    period INTEGER,
    game_clock TEXT,
    season_phase TEXT NOT NULL DEFAULT 'regular_season',
    season_phase_label TEXT,
    season_phase_sub_label TEXT,
    season_phase_subtype TEXT,
    series_text TEXT,
    series_game_number TEXT,
    home_team_id INTEGER REFERENCES wnba.wnba_teams(team_id) ON DELETE SET NULL,
    away_team_id INTEGER REFERENCES wnba.wnba_teams(team_id) ON DELETE SET NULL,
    home_team_tricode TEXT,
    away_team_tricode TEXT,
    home_team_slug TEXT,
    away_team_slug TEXT,
    home_score INTEGER,
    away_score INTEGER,
    arena_name TEXT,
    arena_city TEXT,
    arena_state TEXT,
    is_neutral BOOLEAN,
    postponed_status TEXT,
    source TEXT NOT NULL DEFAULT 'wnba_cdn',
    fetched_at TIMESTAMPTZ,
    raw_payload_json JSONB,
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_wnba_games_season_phase_date_status
    ON wnba.wnba_games(season, season_phase, game_date DESC, game_status);

CREATE INDEX IF NOT EXISTS ix_wnba_games_team_date
    ON wnba.wnba_games(home_team_id, away_team_id, game_date DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_game_event_links (
    wnba_game_event_link_id UUID PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    catalog_event_id UUID REFERENCES catalog.events(event_id) ON DELETE SET NULL,
    agentic_market_event_id UUID REFERENCES agentic.market_events(market_event_id) ON DELETE SET NULL,
    agentic_event_key TEXT,
    polymarket_event_slug TEXT,
    polymarket_market_id TEXT,
    home_outcome_token_id TEXT,
    away_outcome_token_id TEXT,
    confidence NUMERIC(5, 4),
    matching_json JSONB,
    linked_by TEXT,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wnba_game_event_links_game_catalog
    ON wnba.wnba_game_event_links(game_id, catalog_event_id)
    WHERE catalog_event_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wnba_game_event_links_game_agentic
    ON wnba.wnba_game_event_links(game_id, agentic_market_event_id)
    WHERE agentic_market_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_wnba_game_event_links_event_key
    ON wnba.wnba_game_event_links(agentic_event_key);


CREATE TABLE IF NOT EXISTS wnba.wnba_live_game_snapshots (
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    game_status INTEGER,
    game_status_text TEXT,
    period INTEGER,
    clock TEXT,
    home_team_id INTEGER,
    away_team_id INTEGER,
    home_team_tricode TEXT,
    away_team_tricode TEXT,
    home_score INTEGER,
    away_score INTEGER,
    normalized_json JSONB,
    raw_payload_json JSONB,
    PRIMARY KEY (game_id, captured_at, source)
);

CREATE INDEX IF NOT EXISTS ix_wnba_live_snapshots_game_captured_desc
    ON wnba.wnba_live_game_snapshots(game_id, captured_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_team_boxscore_snapshots (
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES wnba.wnba_teams(team_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    team_side TEXT NOT NULL,
    team_tricode TEXT,
    period INTEGER,
    clock TEXT,
    minutes TEXT,
    points INTEGER,
    rebounds INTEGER,
    assists INTEGER,
    steals INTEGER,
    blocks INTEGER,
    turnovers INTEGER,
    fgm INTEGER,
    fga INTEGER,
    fg3m INTEGER,
    fg3a INTEGER,
    ftm INTEGER,
    fta INTEGER,
    plus_minus NUMERIC(10, 3),
    stats_json JSONB,
    raw_payload_json JSONB,
    PRIMARY KEY (game_id, team_id, captured_at, source),
    CONSTRAINT ck_wnba_team_boxscore_snapshots_side
        CHECK (team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_wnba_team_boxscore_game_captured_desc
    ON wnba.wnba_team_boxscore_snapshots(game_id, captured_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_player_boxscore_snapshots (
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL,
    team_id INTEGER REFERENCES wnba.wnba_teams(team_id) ON DELETE SET NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    team_side TEXT NOT NULL,
    team_tricode TEXT,
    player_name TEXT,
    first_name TEXT,
    family_name TEXT,
    jersey_num TEXT,
    position TEXT,
    status TEXT,
    starter BOOLEAN,
    oncourt BOOLEAN,
    played BOOLEAN,
    order_no INTEGER,
    minutes TEXT,
    minutes_calculated TEXT,
    points INTEGER,
    rebounds INTEGER,
    assists INTEGER,
    steals INTEGER,
    blocks INTEGER,
    turnovers INTEGER,
    fgm INTEGER,
    fga INTEGER,
    fg3m INTEGER,
    fg3a INTEGER,
    ftm INTEGER,
    fta INTEGER,
    fouls_personal INTEGER,
    plus_minus NUMERIC(10, 3),
    stats_json JSONB,
    raw_player_json JSONB,
    PRIMARY KEY (game_id, player_id, captured_at, source),
    CONSTRAINT ck_wnba_player_boxscore_snapshots_side
        CHECK (team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_wnba_player_boxscore_game_captured_desc
    ON wnba.wnba_player_boxscore_snapshots(game_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS ix_wnba_player_boxscore_player_captured_desc
    ON wnba.wnba_player_boxscore_snapshots(player_id, captured_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_play_by_play (
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    event_index BIGINT NOT NULL,
    action_id TEXT,
    action_number BIGINT,
    order_number BIGINT,
    period INTEGER,
    period_type TEXT,
    clock TEXT,
    time_actual TIMESTAMPTZ,
    team_id INTEGER,
    team_tricode TEXT,
    person_id INTEGER,
    player_name TEXT,
    action_type TEXT,
    sub_type TEXT,
    description TEXT,
    home_score INTEGER,
    away_score INTEGER,
    points_home INTEGER,
    points_away INTEGER,
    is_score_change BOOLEAN,
    scoring_team_id INTEGER,
    scoring_team_tricode TEXT,
    substitution_direction TEXT,
    substitution_person_id INTEGER,
    substitution_player_name TEXT,
    qualifiers_json JSONB,
    source TEXT NOT NULL DEFAULT 'wnba_cdn',
    fetched_at TIMESTAMPTZ,
    raw_payload_json JSONB,
    PRIMARY KEY (game_id, event_index)
);

CREATE INDEX IF NOT EXISTS ix_wnba_play_by_play_game_period_event
    ON wnba.wnba_play_by_play(game_id, period, event_index);

CREATE INDEX IF NOT EXISTS ix_wnba_play_by_play_game_action_number
    ON wnba.wnba_play_by_play(game_id, action_number);

CREATE INDEX IF NOT EXISTS ix_wnba_play_by_play_player
    ON wnba.wnba_play_by_play(person_id, game_id, event_index)
    WHERE person_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS wnba.wnba_backfill_blockers (
    blocker_id UUID PRIMARY KEY,
    season TEXT NOT NULL,
    source TEXT NOT NULL,
    requirement TEXT NOT NULL,
    status TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    details_json JSONB,
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_wnba_backfill_blockers_season_status
    ON wnba.wnba_backfill_blockers(season, status, detected_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_clob_watch_targets (
    watch_target_id UUID PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    agentic_event_key TEXT,
    polymarket_event_slug TEXT,
    polymarket_market_id TEXT,
    home_outcome_token_id TEXT,
    away_outcome_token_id TEXT,
    match_status TEXT NOT NULL DEFAULT 'candidate',
    passive_only BOOLEAN NOT NULL DEFAULT TRUE,
    clob_capture_required BOOLEAN NOT NULL DEFAULT TRUE,
    confidence NUMERIC(5, 4),
    matched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL DEFAULT 'polymarket_gamma',
    matching_json JSONB,
    watch_plan_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_wnba_clob_watch_targets_status
    ON wnba.wnba_clob_watch_targets(match_status, matched_at DESC);

CREATE INDEX IF NOT EXISTS ix_wnba_clob_watch_targets_event_key
    ON wnba.wnba_clob_watch_targets(agentic_event_key);


CREATE TABLE IF NOT EXISTS wnba.wnba_market_state_panels (
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    team_side TEXT NOT NULL,
    state_index INTEGER NOT NULL,
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    event_index BIGINT,
    action_id TEXT,
    period INTEGER,
    clock TEXT,
    seconds_to_game_end NUMERIC(12, 3),
    score_for INTEGER,
    score_against INTEGER,
    score_diff INTEGER,
    scoring_side TEXT,
    points_scored INTEGER,
    player_id INTEGER,
    player_name TEXT,
    action_type TEXT,
    team_price NUMERIC(10, 6),
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    spread NUMERIC(10, 6),
    mid_price NUMERIC(10, 6),
    liquidity_context_json JSONB,
    player_context_json JSONB,
    raw_state_json JSONB,
    PRIMARY KEY (game_id, team_side, state_index, analysis_version),
    CONSTRAINT ck_wnba_market_state_panels_side
        CHECK (team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_wnba_market_state_panels_game_team
    ON wnba.wnba_market_state_panels(game_id, team_side, state_index);

CREATE INDEX IF NOT EXISTS ix_wnba_market_state_panels_context
    ON wnba.wnba_market_state_panels(analysis_version, period, seconds_to_game_end);
