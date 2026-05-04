-- v1.0.2: add game stakes features for routing, replay, and live validation.

ALTER TABLE nba.nba_analysis_game_team_profiles
    ADD COLUMN IF NOT EXISTS stakes_score NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS stakes_deterministic_score NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS stakes_llm_score NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS stakes_bucket TEXT,
    ADD COLUMN IF NOT EXISTS stakes_reason TEXT;

ALTER TABLE nba.nba_analysis_state_panel
    ADD COLUMN IF NOT EXISTS stakes_score NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS stakes_deterministic_score NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS stakes_llm_score NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS stakes_bucket TEXT,
    ADD COLUMN IF NOT EXISTS stakes_reason TEXT;

CREATE INDEX IF NOT EXISTS ix_nba_analysis_game_team_profiles_stakes
    ON nba.nba_analysis_game_team_profiles(season, season_phase, stakes_bucket, game_date DESC);
