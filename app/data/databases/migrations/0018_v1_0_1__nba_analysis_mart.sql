-- v1.0.1: regular-season NBA analysis mart tables for offline research, backtesting, and baseline modeling

CREATE TABLE IF NOT EXISTS nba.nba_analysis_game_team_profiles (
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    team_side TEXT NOT NULL,
    team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    team_slug TEXT,
    opponent_team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    opponent_team_slug TEXT,
    event_id UUID REFERENCES catalog.events(event_id) ON DELETE SET NULL,
    market_id UUID REFERENCES catalog.markets(market_id) ON DELETE SET NULL,
    outcome_id UUID REFERENCES catalog.outcomes(outcome_id) ON DELETE SET NULL,
    season TEXT NOT NULL,
    season_phase TEXT NOT NULL DEFAULT 'regular_season',
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    game_date DATE,
    game_start_time TIMESTAMPTZ,
    coverage_status TEXT,
    research_ready_flag BOOLEAN NOT NULL DEFAULT FALSE,
    price_path_reconciled_flag BOOLEAN NOT NULL DEFAULT FALSE,
    final_winner_flag BOOLEAN,
    opening_price NUMERIC(10, 6),
    closing_price NUMERIC(10, 6),
    opening_band TEXT,
    opening_band_rank INTEGER,
    pregame_price_min NUMERIC(10, 6),
    pregame_price_max NUMERIC(10, 6),
    pregame_price_range NUMERIC(10, 6),
    ingame_price_min NUMERIC(10, 6),
    ingame_price_max NUMERIC(10, 6),
    ingame_price_range NUMERIC(10, 6),
    total_price_min NUMERIC(10, 6),
    total_price_max NUMERIC(10, 6),
    total_swing NUMERIC(10, 6),
    max_favorable_excursion NUMERIC(10, 6),
    max_adverse_excursion NUMERIC(10, 6),
    inversion_count INTEGER,
    first_inversion_at TIMESTAMPTZ,
    seconds_above_50c NUMERIC(12, 3),
    seconds_below_50c NUMERIC(12, 3),
    winner_stable_70_at TIMESTAMPTZ,
    winner_stable_80_at TIMESTAMPTZ,
    winner_stable_90_at TIMESTAMPTZ,
    winner_stable_95_at TIMESTAMPTZ,
    winner_stable_70_clock_elapsed_seconds NUMERIC(12, 3),
    winner_stable_80_clock_elapsed_seconds NUMERIC(12, 3),
    winner_stable_90_clock_elapsed_seconds NUMERIC(12, 3),
    winner_stable_95_clock_elapsed_seconds NUMERIC(12, 3),
    notes_json JSONB,
    PRIMARY KEY (game_id, team_side, analysis_version),
    CONSTRAINT ck_nba_analysis_game_team_profiles_team_side
        CHECK (team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_game_team_profiles_season_ready
    ON nba.nba_analysis_game_team_profiles(season, season_phase, research_ready_flag, game_date DESC);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_game_team_profiles_team
    ON nba.nba_analysis_game_team_profiles(team_id, season, season_phase, game_date DESC);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_game_team_profiles_market
    ON nba.nba_analysis_game_team_profiles(market_id, outcome_id);


CREATE TABLE IF NOT EXISTS nba.nba_analysis_state_panel (
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    team_side TEXT NOT NULL,
    state_index INTEGER NOT NULL,
    team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    team_slug TEXT,
    opponent_team_id INTEGER REFERENCES nba.nba_teams(team_id) ON DELETE SET NULL,
    opponent_team_slug TEXT,
    event_id UUID REFERENCES catalog.events(event_id) ON DELETE SET NULL,
    market_id UUID REFERENCES catalog.markets(market_id) ON DELETE SET NULL,
    outcome_id UUID REFERENCES catalog.outcomes(outcome_id) ON DELETE SET NULL,
    season TEXT NOT NULL,
    season_phase TEXT NOT NULL DEFAULT 'regular_season',
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    game_date DATE,
    event_index BIGINT,
    action_id TEXT,
    event_at TIMESTAMPTZ,
    period INTEGER,
    period_label TEXT,
    clock TEXT,
    clock_elapsed_seconds NUMERIC(12, 3),
    seconds_to_game_end NUMERIC(12, 3),
    score_for INTEGER,
    score_against INTEGER,
    score_diff INTEGER,
    score_diff_bucket TEXT,
    context_bucket TEXT,
    team_led_flag BOOLEAN,
    team_trailed_flag BOOLEAN,
    tied_flag BOOLEAN,
    market_favorite_flag BOOLEAN,
    scoreboard_control_mismatch_flag BOOLEAN,
    final_winner_flag BOOLEAN,
    scoring_side TEXT,
    points_scored INTEGER,
    delta_for INTEGER,
    delta_against INTEGER,
    lead_changes_so_far INTEGER,
    team_points_last_5_events INTEGER,
    opponent_points_last_5_events INTEGER,
    net_points_last_5_events INTEGER,
    opening_price NUMERIC(10, 6),
    opening_band TEXT,
    team_price NUMERIC(10, 6),
    price_delta_from_open NUMERIC(10, 6),
    abs_price_delta_from_open NUMERIC(10, 6),
    price_mode TEXT,
    gap_before_seconds NUMERIC(12, 3),
    gap_after_seconds NUMERIC(12, 3),
    mfe_from_state NUMERIC(10, 6),
    mae_from_state NUMERIC(10, 6),
    large_swing_next_12_states_flag BOOLEAN,
    crossed_50c_next_12_states_flag BOOLEAN,
    winner_stable_70_after_state_flag BOOLEAN,
    winner_stable_80_after_state_flag BOOLEAN,
    winner_stable_90_after_state_flag BOOLEAN,
    winner_stable_95_after_state_flag BOOLEAN,
    PRIMARY KEY (game_id, team_side, state_index, analysis_version),
    CONSTRAINT ck_nba_analysis_state_panel_team_side
        CHECK (team_side IN ('home', 'away'))
);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_state_panel_season_team_event
    ON nba.nba_analysis_state_panel(season, season_phase, team_id, event_at);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_state_panel_context
    ON nba.nba_analysis_state_panel(season, season_phase, context_bucket, event_at);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_state_panel_market
    ON nba.nba_analysis_state_panel(market_id, outcome_id, event_at);


CREATE TABLE IF NOT EXISTS nba.nba_analysis_team_season_profiles (
    team_id INTEGER NOT NULL REFERENCES nba.nba_teams(team_id) ON DELETE CASCADE,
    team_slug TEXT,
    season TEXT NOT NULL,
    season_phase TEXT NOT NULL DEFAULT 'regular_season',
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    sample_games INTEGER,
    research_ready_games INTEGER,
    wins INTEGER,
    losses INTEGER,
    favorite_games INTEGER,
    underdog_games INTEGER,
    avg_opening_price NUMERIC(10, 6),
    avg_closing_price NUMERIC(10, 6),
    avg_pregame_range NUMERIC(10, 6),
    avg_ingame_range NUMERIC(10, 6),
    avg_total_swing NUMERIC(10, 6),
    avg_max_favorable_excursion NUMERIC(10, 6),
    avg_max_adverse_excursion NUMERIC(10, 6),
    avg_inversion_count NUMERIC(10, 6),
    games_with_inversion INTEGER,
    inversion_rate NUMERIC(10, 6),
    avg_seconds_above_50c NUMERIC(12, 3),
    avg_seconds_below_50c NUMERIC(12, 3),
    avg_favorite_drawdown NUMERIC(10, 6),
    avg_underdog_spike NUMERIC(10, 6),
    control_confidence_mismatch_rate NUMERIC(10, 6),
    opening_price_trend_slope NUMERIC(10, 6),
    winner_stable_70_rate NUMERIC(10, 6),
    winner_stable_80_rate NUMERIC(10, 6),
    winner_stable_90_rate NUMERIC(10, 6),
    winner_stable_95_rate NUMERIC(10, 6),
    avg_winner_stable_70_clock_elapsed_seconds NUMERIC(12, 3),
    avg_winner_stable_80_clock_elapsed_seconds NUMERIC(12, 3),
    avg_winner_stable_90_clock_elapsed_seconds NUMERIC(12, 3),
    avg_winner_stable_95_clock_elapsed_seconds NUMERIC(12, 3),
    rolling_10_json JSONB,
    rolling_20_json JSONB,
    notes_json JSONB,
    PRIMARY KEY (team_id, season, season_phase, analysis_version)
);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_team_season_profiles_team
    ON nba.nba_analysis_team_season_profiles(team_id, season, season_phase, computed_at DESC);


CREATE TABLE IF NOT EXISTS nba.nba_analysis_opening_band_profiles (
    season TEXT NOT NULL,
    season_phase TEXT NOT NULL DEFAULT 'regular_season',
    opening_band TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    sample_games INTEGER,
    win_rate NUMERIC(10, 6),
    avg_opening_price NUMERIC(10, 6),
    avg_closing_price NUMERIC(10, 6),
    avg_ingame_range NUMERIC(10, 6),
    avg_total_swing NUMERIC(10, 6),
    avg_max_favorable_excursion NUMERIC(10, 6),
    avg_max_adverse_excursion NUMERIC(10, 6),
    avg_inversion_count NUMERIC(10, 6),
    inversion_rate NUMERIC(10, 6),
    winner_stable_70_rate NUMERIC(10, 6),
    winner_stable_80_rate NUMERIC(10, 6),
    winner_stable_90_rate NUMERIC(10, 6),
    winner_stable_95_rate NUMERIC(10, 6),
    notes_json JSONB,
    PRIMARY KEY (season, season_phase, opening_band, analysis_version)
);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_opening_band_profiles_season
    ON nba.nba_analysis_opening_band_profiles(season, season_phase, computed_at DESC);


CREATE TABLE IF NOT EXISTS nba.nba_analysis_winner_definition_profiles (
    season TEXT NOT NULL,
    season_phase TEXT NOT NULL DEFAULT 'regular_season',
    threshold_cents INTEGER NOT NULL,
    context_bucket TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    sample_states INTEGER,
    distinct_games INTEGER,
    stable_states INTEGER,
    stable_rate NUMERIC(10, 6),
    reopen_rate NUMERIC(10, 6),
    avg_score_diff NUMERIC(10, 6),
    avg_team_price NUMERIC(10, 6),
    avg_seconds_to_game_end NUMERIC(12, 3),
    notes_json JSONB,
    PRIMARY KEY (season, season_phase, threshold_cents, context_bucket, analysis_version)
);

CREATE INDEX IF NOT EXISTS ix_nba_analysis_winner_definition_profiles_season
    ON nba.nba_analysis_winner_definition_profiles(season, season_phase, threshold_cents, computed_at DESC);
