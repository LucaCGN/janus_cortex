-- v1.1.3: WNBA deterministic lane, ML model, and integration-readiness products.

CREATE SCHEMA IF NOT EXISTS wnba;

ALTER TABLE wnba.wnba_market_state_panels
    ADD COLUMN IF NOT EXISTS event_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS clock_elapsed_seconds NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS score_diff_bucket TEXT,
    ADD COLUMN IF NOT EXISTS context_bucket TEXT,
    ADD COLUMN IF NOT EXISTS team_tricode TEXT,
    ADD COLUMN IF NOT EXISTS opponent_tricode TEXT,
    ADD COLUMN IF NOT EXISTS team_led_flag BOOLEAN,
    ADD COLUMN IF NOT EXISTS team_trailed_flag BOOLEAN,
    ADD COLUMN IF NOT EXISTS tied_flag BOOLEAN,
    ADD COLUMN IF NOT EXISTS recent_team_points_5_events INTEGER,
    ADD COLUMN IF NOT EXISTS recent_opponent_points_5_events INTEGER,
    ADD COLUMN IF NOT EXISTS recent_net_points_5_events INTEGER,
    ADD COLUMN IF NOT EXISTS opening_price NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS opening_band TEXT,
    ADD COLUMN IF NOT EXISTS price_delta_from_open NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS abs_price_delta_from_open NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS price_mode TEXT,
    ADD COLUMN IF NOT EXISTS market_age_seconds NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS token_id TEXT,
    ADD COLUMN IF NOT EXISTS market_id TEXT,
    ADD COLUMN IF NOT EXISTS outcome_id TEXT,
    ADD COLUMN IF NOT EXISTS backtest_eligible BOOLEAN;

CREATE INDEX IF NOT EXISTS ix_wnba_market_state_panels_replay_context
    ON wnba.wnba_market_state_panels(analysis_version, context_bucket, event_at);


CREATE TABLE IF NOT EXISTS wnba.wnba_deterministic_lane_configs (
    lane_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    entry_rule TEXT NOT NULL,
    exit_rule TEXT NOT NULL,
    description TEXT,
    comparator_group TEXT,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE,
    orders_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    requires_clob BOOLEAN NOT NULL DEFAULT TRUE,
    requires_trade_microstructure BOOLEAN NOT NULL DEFAULT FALSE,
    config_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_wnba_deterministic_lane_configs_family
    ON wnba.wnba_deterministic_lane_configs(family, analysis_version);


CREATE TABLE IF NOT EXISTS wnba.wnba_lane_signal_rows (
    lane_signal_id UUID PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES wnba.wnba_games(game_id) ON DELETE CASCADE,
    team_side TEXT NOT NULL,
    state_index INTEGER NOT NULL,
    analysis_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    lane_id TEXT NOT NULL,
    family TEXT NOT NULL,
    signal_status TEXT NOT NULL,
    signal_type TEXT,
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE,
    orders_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    requires_clob BOOLEAN NOT NULL DEFAULT TRUE,
    requires_trade_microstructure BOOLEAN NOT NULL DEFAULT FALSE,
    entry_price NUMERIC(10, 6),
    target_price NUMERIC(10, 6),
    stop_price NUMERIC(10, 6),
    score_diff INTEGER,
    period INTEGER,
    seconds_to_game_end NUMERIC(12, 3),
    blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    features_json JSONB,
    lane_config_json JSONB,
    CONSTRAINT ck_wnba_lane_signal_rows_side
        CHECK (team_side IN ('home', 'away')),
    CONSTRAINT ck_wnba_lane_signal_orders_disabled
        CHECK (orders_allowed = FALSE)
);

CREATE INDEX IF NOT EXISTS ix_wnba_lane_signal_rows_family_status
    ON wnba.wnba_lane_signal_rows(family, signal_status, computed_at DESC);

CREATE INDEX IF NOT EXISTS ix_wnba_lane_signal_rows_game_state
    ON wnba.wnba_lane_signal_rows(game_id, team_side, state_index);


CREATE TABLE IF NOT EXISTS wnba.wnba_ml_model_runs (
    model_run_id UUID PRIMARY KEY,
    feature_version TEXT NOT NULL,
    target_column TEXT NOT NULL,
    trained_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    training_rows INTEGER NOT NULL DEFAULT 0,
    validation_rows INTEGER NOT NULL DEFAULT 0,
    distinct_games INTEGER NOT NULL DEFAULT 0,
    blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    feature_columns_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_json JSONB,
    metrics_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_wnba_ml_model_runs_status
    ON wnba.wnba_ml_model_runs(feature_version, status, trained_at DESC);


CREATE TABLE IF NOT EXISTS wnba.wnba_integration_readiness_audits (
    integration_audit_id UUID PRIMARY KEY,
    analysis_version TEXT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    orders_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    passive_shadow_ready BOOLEAN NOT NULL DEFAULT FALSE,
    calibrated_backtesting_ready BOOLEAN NOT NULL DEFAULT FALSE,
    expected_lane_families_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    configured_lane_families_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    signal_lane_families_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    entry_candidate_count INTEGER NOT NULL DEFAULT 0,
    blocked_signal_count INTEGER NOT NULL DEFAULT 0,
    structural_blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    calibration_blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    verdict TEXT,
    raw_json JSONB,
    CONSTRAINT ck_wnba_integration_orders_disabled
        CHECK (orders_allowed = FALSE)
);

CREATE INDEX IF NOT EXISTS ix_wnba_integration_readiness_status
    ON wnba.wnba_integration_readiness_audits(status, evaluated_at DESC);
