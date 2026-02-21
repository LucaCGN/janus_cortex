-- v0.4.5: cross-domain event information quality scoring

CREATE TABLE IF NOT EXISTS catalog.event_information_scores (
    event_id UUID NOT NULL REFERENCES catalog.events(event_id) ON DELETE CASCADE,
    scored_at TIMESTAMPTZ NOT NULL,
    information_profile_id UUID REFERENCES catalog.information_profiles(information_profile_id) ON DELETE SET NULL,
    coverage_score NUMERIC(5, 2),
    quality_score NUMERIC(5, 2),
    latency_score NUMERIC(5, 2),
    is_trade_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    missing_fields_json JSONB,
    PRIMARY KEY (event_id, scored_at)
);

CREATE INDEX IF NOT EXISTS ix_catalog_event_information_scores_scored_at_desc
    ON catalog.event_information_scores(scored_at DESC);

CREATE INDEX IF NOT EXISTS ix_catalog_event_information_scores_trade_eligible
    ON catalog.event_information_scores(is_trade_eligible, scored_at DESC);
