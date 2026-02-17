-- v0.3.1: core + catalog MVP baseline

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS catalog;

CREATE TABLE IF NOT EXISTS core.providers (
    provider_id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    base_url TEXT,
    auth_type TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.modules (
    module_id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    owner TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.event_types (
    event_type_id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    description TEXT,
    default_horizon TEXT,
    resolution_policy TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.information_profiles (
    information_profile_id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    min_sources INTEGER NOT NULL DEFAULT 1,
    required_fields_json JSONB,
    refresh_interval_sec INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.events (
    event_id UUID PRIMARY KEY,
    event_type_id UUID NOT NULL REFERENCES catalog.event_types(event_type_id) ON DELETE RESTRICT,
    information_profile_id UUID REFERENCES catalog.information_profiles(information_profile_id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    canonical_slug TEXT,
    status TEXT NOT NULL,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    resolution_time TIMESTAMPTZ,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.event_external_refs (
    event_ref_id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES catalog.events(event_id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES core.providers(provider_id) ON DELETE RESTRICT,
    external_id TEXT NOT NULL,
    external_slug TEXT,
    external_url TEXT,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    raw_summary_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.markets (
    market_id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES catalog.events(event_id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    market_type TEXT,
    condition_id TEXT,
    market_slug TEXT,
    open_time TIMESTAMPTZ,
    close_time TIMESTAMPTZ,
    settled_time TIMESTAMPTZ,
    settlement_status TEXT,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.market_external_refs (
    market_ref_id UUID PRIMARY KEY,
    market_id UUID NOT NULL REFERENCES catalog.markets(market_id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES core.providers(provider_id) ON DELETE RESTRICT,
    external_market_id TEXT NOT NULL,
    external_condition_id TEXT,
    external_slug TEXT,
    raw_summary_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.outcomes (
    outcome_id UUID PRIMARY KEY,
    market_id UUID NOT NULL REFERENCES catalog.markets(market_id) ON DELETE CASCADE,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    token_id TEXT,
    is_winner BOOLEAN,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_events_canonical_slug_not_null
    ON catalog.events(canonical_slug)
    WHERE canonical_slug IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_event_refs_provider_external
    ON catalog.event_external_refs(provider_id, external_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_market_refs_provider_external_market
    ON catalog.market_external_refs(provider_id, external_market_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_outcomes_market_outcome_index
    ON catalog.outcomes(market_id, outcome_index);

CREATE INDEX IF NOT EXISTS ix_catalog_outcomes_token_id
    ON catalog.outcomes(token_id);

