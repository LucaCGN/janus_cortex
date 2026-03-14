-- v0.7.4: nba context cache for pre/live context endpoints

CREATE TABLE IF NOT EXISTS nba.nba_context_cache (
    game_id TEXT NOT NULL REFERENCES nba.nba_games(game_id) ON DELETE CASCADE,
    context_type TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    PRIMARY KEY (game_id, context_type, generated_at)
);

CREATE INDEX IF NOT EXISTS ix_nba_context_cache_game_type_generated_desc
    ON nba.nba_context_cache(game_id, context_type, generated_at DESC);
