-- v0.8.4: NBA odds coverage audit rows for regular-season game mapping and history retrieval

CREATE TABLE IF NOT EXISTS nba.nba_odds_coverage_audits (
    odds_coverage_audit_id UUID PRIMARY KEY,
    season TEXT NOT NULL,
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    event_id UUID REFERENCES catalog.events(event_id) ON DELETE SET NULL,
    market_id UUID REFERENCES catalog.markets(market_id) ON DELETE SET NULL,
    outcome_id UUID REFERENCES catalog.outcomes(outcome_id) ON DELETE SET NULL,
    audited_at TIMESTAMPTZ NOT NULL,
    coverage_scope TEXT NOT NULL,
    coverage_status TEXT NOT NULL,
    history_points INTEGER,
    fallback_points INTEGER,
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ,
    issue_code TEXT,
    details_json JSONB
);

CREATE INDEX IF NOT EXISTS ix_nba_odds_coverage_audits_game_scope_audited_desc
    ON nba.nba_odds_coverage_audits(game_id, coverage_scope, audited_at DESC);

CREATE INDEX IF NOT EXISTS ix_nba_odds_coverage_audits_event_status_audited_desc
    ON nba.nba_odds_coverage_audits(event_id, coverage_status, audited_at DESC);
