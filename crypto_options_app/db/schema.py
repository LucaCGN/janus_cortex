from __future__ import annotations

import sqlite3
from pathlib import Path

from crypto_options_app.db.connection import connect, default_db_path
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime


SCHEMA_VERSION = "crypto_options_app_canonical_schema_v1"

EXPECTED_TABLES = {
    "app_settings",
    "data_service_watermarks",
    "worker_runs",
    "profiles",
    "profile_refs",
    "profile_fetch_runs",
    "profile_raw_activity",
    "profile_period_performance",
    "profile_grades",
    "profile_grade_history",
    "profile_event_orders",
    "profile_event_positions",
    "profile_event_reconstructions",
    "profile_event_styles",
    "profile_generator_scores",
    "profile_buying_ahead",
    "profile_distribution_snapshots",
    "profile_distribution_components",
    "events",
    "event_tokens",
    "event_outcomes",
    "event_rollups",
    "polymarket_price_ticks",
    "polymarket_order_books",
    "polymarket_order_book_levels",
    "polymarket_trade_prints",
    "polymarket_updown_pair_snapshots",
    "polymarket_event_path_stats",
    "strategy_replay_candidate_cache_runs",
    "strategy_replay_candidate_scenarios",
    "underlying_price_ticks",
    "underlying_candles",
    "indicator_definitions",
    "indicator_snapshots",
    "external_technical_observer_snapshots",
    "external_technical_observer_components",
    "event_indicator_context",
    "indicator_backtest_results",
    "data_signal_readiness_snapshots",
    "replay_datasets",
    "replay_frames",
    "replay_runs",
    "replay_candidate_signals",
    "replay_fill_simulations",
    "replay_exit_simulations",
    "replay_trade_groups",
    "replay_component_results",
    "replay_reports",
    "signal_specs",
    "signal_versions",
    "signal_queue_items",
    "signal_validation_runs",
    "signal_validation_results",
    "signal_observations",
    "signal_artifacts",
    "strategy_specs",
    "strategy_versions",
    "strategy_readiness",
    "strategy_promotion_state",
    "strategy_run_configs",
    "strategy_validation_runs",
    "validation_budget_ledger",
    "strategy_candidates",
    "execution_intents",
    "orders",
    "fills",
    "positions",
    "exit_plans",
    "exit_orders",
    "settlements",
    "pnl_snapshots",
    "risk_gate_evaluations",
    "stop_gate_events",
    "exposure_snapshots",
    "system_status_snapshots",
    "strategy_reports",
    "run_reports",
}

EVENT_PATH_STATS_EXTRA_COLUMNS = {
    "event_price_points_json": "TEXT NOT NULL DEFAULT '{}'",
    "pre_event_price_points_json": "TEXT NOT NULL DEFAULT '{}'",
    "level_first_touch_seconds_json": "TEXT NOT NULL DEFAULT '{}'",
    "tail_comeback_table_json": "TEXT NOT NULL DEFAULT '{}'",
    "path_direction": "TEXT",
    "path_efficiency": "REAL",
    "time_to_first_extreme_seconds": "REAL",
    "avg_swing_distance": "REAL",
    "max_swing_distance": "REAL",
    "avg_rolling_30s_range": "REAL",
    "max_rolling_30s_range": "REAL",
    "avg_rolling_60s_range": "REAL",
    "max_rolling_60s_range": "REAL",
}

POSTGRES_READY_CHECK_TABLES = {
    "app_settings",
    "data_service_watermarks",
    "polymarket_event_path_stats",
    "profile_distribution_snapshots",
    "strategy_validation_runs",
    "run_reports",
}


def initialize_schema(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path is not None else default_db_path()
    if should_use_postgres_runtime(path):
        if _postgres_runtime_schema_ready(path):
            return path
        from crypto_options_app.db.postgres import (
            CryptoOptionsPostgresSettings,
            initialize_postgres_schema,
        )

        initialize_postgres_schema(
            CryptoOptionsPostgresSettings.from_url(),
        )
        return path
    with connect(path) as conn:
        create_schema(conn)
    return path


def _postgres_runtime_schema_ready(path: Path) -> bool:
    """Return true when the runtime Postgres DB can be used without DDL.

    Replay and worker persistence paths call initialize_schema defensively. For
    Postgres, repeatedly executing CREATE TABLE/ALTER TABLE under concurrent
    workers can deadlock even when all objects already exist. This fast path is
    intentionally read-only and bounded so routine runtime writes do not compete
    on schema locks.
    """

    try:
        with connect(path) as conn:
            tables = list_tables(conn)
            if not POSTGRES_READY_CHECK_TABLES.issubset(tables):
                return False
            column_rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                """,
                ("polymarket_event_path_stats",),
            ).fetchall()
            columns = {str(row["column_name"]) for row in column_rows}
            if not set(EVENT_PATH_STATS_EXTRA_COLUMNS).issubset(columns):
                return False
            row = conn.execute(
                """
                SELECT setting_value
                FROM app_settings
                WHERE setting_key = ?
                LIMIT 1
                """,
                ("schema_version",),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def create_schema(conn: sqlite3.Connection) -> None:
    if getattr(conn, "is_postgres", False):
        from crypto_options_app.db.postgres import render_postgres_schema_sql

        conn.executescript(render_postgres_schema_sql())
        _ensure_event_path_stats_columns_postgres(conn)
        conn.execute(
            """
            INSERT INTO app_settings(setting_key, setting_value, updated_at_utc)
            VALUES(%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value=EXCLUDED.setting_value,
                updated_at_utc=EXCLUDED.updated_at_utc
            """,
            ("schema_version", SCHEMA_VERSION),
        )
        return
    conn.executescript(_DDL)
    _ensure_event_path_stats_columns(conn)
    conn.execute(
        """
        INSERT INTO app_settings(setting_key, setting_value, updated_at_utc)
        VALUES('schema_version', ?, datetime('now'))
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value=excluded.setting_value,
            updated_at_utc=excluded.updated_at_utc
        """,
        (SCHEMA_VERSION,),
    )


def list_tables(conn: sqlite3.Connection) -> set[str]:
    if getattr(conn, "is_postgres", False):
        rows = conn.execute(
            """
            SELECT table_name AS name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            """
        ).fetchall()
        return {str(row["name"]) for row in rows}
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_event_path_stats_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(polymarket_event_path_stats)").fetchall()}
    for column, ddl in EVENT_PATH_STATS_EXTRA_COLUMNS.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE polymarket_event_path_stats ADD COLUMN {column} {ddl}")


def _ensure_event_path_stats_columns_postgres(conn) -> None:
    if not getattr(conn, "is_postgres", False):
        return
    for column, ddl in EVENT_PATH_STATS_EXTRA_COLUMNS.items():
        conn.execute(f"ALTER TABLE polymarket_event_path_stats ADD COLUMN IF NOT EXISTS {column} {ddl}")


_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_service_watermarks (
    service_name TEXT NOT NULL,
    module_id TEXT NOT NULL,
    last_run_at_utc TEXT,
    status TEXT NOT NULL,
    source TEXT,
    rows_observed INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(service_name, module_id)
);

CREATE TABLE IF NOT EXISTS worker_runs (
    worker_run_id TEXT PRIMARY KEY,
    worker_name TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    rows_observed INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    profile_key TEXT PRIMARY KEY,
    normalized_ref TEXT,
    handle TEXT,
    proxy_wallet TEXT,
    profile_name TEXT,
    first_seen_at_utc TEXT,
    last_seen_at_utc TEXT,
    latest_activity_utc TEXT,
    source_table TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_refs (
    ref_key TEXT PRIMARY KEY,
    profile_key TEXT,
    normalized_ref TEXT,
    raw_ref TEXT,
    handle TEXT,
    address TEXT,
    source TEXT,
    first_seen_at_utc TEXT,
    last_seen_at_utc TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_fetch_runs (
    fetch_run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    requested_ref_count INTEGER NOT NULL DEFAULT 0,
    fetched_profile_count INTEGER NOT NULL DEFAULT 0,
    failed_profile_count INTEGER NOT NULL DEFAULT 0,
    max_concurrency INTEGER,
    page_limit INTEGER,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_raw_activity (
    raw_activity_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    event_key TEXT,
    event_slug TEXT,
    condition_id TEXT,
    market_slug TEXT,
    symbol TEXT,
    order_side TEXT,
    outcome_side TEXT,
    token_id TEXT,
    price REAL,
    shares REAL,
    notional_usd REAL,
    activity_at_utc TEXT,
    observed_at_utc TEXT,
    event_start_time_utc TEXT,
    event_end_time_utc TEXT,
    seconds_before_event_start REAL,
    buying_ahead INTEGER NOT NULL DEFAULT 0,
    active_during_event INTEGER NOT NULL DEFAULT 0,
    source_table TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(profile_key) REFERENCES profiles(profile_key)
);

CREATE TABLE IF NOT EXISTS profile_period_performance (
    period_performance_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    period TEXT NOT NULL,
    evaluated_at_utc TEXT,
    pnl_usd REAL,
    closed_win_rate REAL,
    closed_return_pct REAL,
    reconstructed_event_win_rate REAL,
    reconstructed_event_return_pct REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(profile_key) REFERENCES profiles(profile_key)
);

CREATE TABLE IF NOT EXISTS profile_grades (
    profile_key TEXT PRIMARY KEY,
    evaluated_at_utc TEXT NOT NULL,
    grade TEXT NOT NULL,
    score REAL,
    trading_style TEXT,
    trading_style_detail TEXT,
    frequency_class TEXT,
    source_table TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(profile_key) REFERENCES profiles(profile_key)
);

CREATE TABLE IF NOT EXISTS profile_grade_history (
    grade_history_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    grade TEXT NOT NULL,
    score REAL,
    source_table TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(profile_key) REFERENCES profiles(profile_key)
);

CREATE TABLE IF NOT EXISTS profile_event_orders (
    profile_event_order_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    event_key TEXT,
    event_token_key TEXT,
    raw_activity_key TEXT,
    order_side TEXT,
    outcome TEXT,
    price REAL,
    shares REAL,
    notional_usd REAL,
    activity_at_utc TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(profile_key) REFERENCES profiles(profile_key),
    FOREIGN KEY(event_token_key) REFERENCES event_tokens(event_token_key)
);

CREATE TABLE IF NOT EXISTS profile_event_positions (
    profile_event_position_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_token_key TEXT,
    outcome TEXT,
    shares REAL,
    cost_basis_usd REAL,
    weighted_avg_price REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_event_reconstructions (
    reconstruction_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_slug TEXT,
    evaluated_at_utc TEXT,
    event_style TEXT,
    buy_count INTEGER NOT NULL DEFAULT 0,
    sell_count INTEGER NOT NULL DEFAULT 0,
    up_open_shares REAL,
    down_open_shares REAL,
    realized_pnl_usd REAL,
    event_pnl_usd REAL,
    event_effective_win INTEGER,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_event_styles (
    profile_event_style_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_style TEXT NOT NULL,
    confidence REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_generator_scores (
    generator_score_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    account_type TEXT,
    grade TEXT,
    score REAL NOT NULL,
    status TEXT NOT NULL,
    can_emit_live INTEGER NOT NULL DEFAULT 0,
    usage_eligible INTEGER NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_buying_ahead (
    buying_ahead_key TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    event_key TEXT NOT NULL,
    raw_activity_key TEXT,
    token_id TEXT,
    outcome_side TEXT,
    activity_at_utc TEXT,
    event_start_time_utc TEXT,
    seconds_before_event_start REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    UNIQUE(profile_key, event_key, raw_activity_key)
);

CREATE TABLE IF NOT EXISTS profile_distribution_snapshots (
    distribution_snapshot_key TEXT PRIMARY KEY,
    event_key TEXT,
    event_slug TEXT,
    symbol TEXT,
    phase TEXT NOT NULL,
    computed_at_utc TEXT NOT NULL,
    event_start_time_utc TEXT,
    event_end_time_utc TEXT,
    source_mode TEXT NOT NULL,
    canonical_method TEXT NOT NULL,
    profile_count INTEGER NOT NULL DEFAULT 0,
    component_count INTEGER NOT NULL DEFAULT 0,
    up_weight REAL NOT NULL DEFAULT 0,
    down_weight REAL NOT NULL DEFAULT 0,
    up_share_weight REAL NOT NULL DEFAULT 0,
    down_share_weight REAL NOT NULL DEFAULT 0,
    up_cost_weight REAL NOT NULL DEFAULT 0,
    down_cost_weight REAL NOT NULL DEFAULT 0,
    up_count_weight REAL NOT NULL DEFAULT 0,
    down_count_weight REAL NOT NULL DEFAULT 0,
    distribution_json TEXT NOT NULL DEFAULT '{}',
    source_json TEXT NOT NULL DEFAULT '{}',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_distribution_components (
    distribution_component_key TEXT PRIMARY KEY,
    distribution_snapshot_key TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    handle TEXT,
    grade TEXT,
    trading_style TEXT,
    source_mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    net_shares REAL NOT NULL DEFAULT 0,
    net_notional_usd REAL NOT NULL DEFAULT 0,
    cost_basis_usd REAL NOT NULL DEFAULT 0,
    grade_weight REAL NOT NULL DEFAULT 1,
    style_weight REAL NOT NULL DEFAULT 1,
    final_weight REAL NOT NULL DEFAULT 1,
    contribution_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(distribution_snapshot_key) REFERENCES profile_distribution_snapshots(distribution_snapshot_key)
);

CREATE TABLE IF NOT EXISTS events (
    event_key TEXT PRIMARY KEY,
    event_slug TEXT,
    condition_id TEXT,
    market_id TEXT,
    market_slug TEXT,
    symbol TEXT,
    cadence_seconds INTEGER,
    event_start_time_utc TEXT,
    event_end_time_utc TEXT,
    settlement_threshold REAL,
    source_table TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_tokens (
    event_token_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    condition_id TEXT,
    event_slug TEXT,
    market_id TEXT,
    symbol TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    closed INTEGER NOT NULL DEFAULT 0,
    source_table TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(event_key) REFERENCES events(event_key)
);

CREATE TABLE IF NOT EXISTS event_outcomes (
    event_outcome_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    resolved_outcome TEXT,
    resolved_at_utc TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_rollups (
    event_rollup_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    rollup_type TEXT NOT NULL,
    computed_at_utc TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS polymarket_price_ticks (
    price_tick_key TEXT PRIMARY KEY,
    event_token_key TEXT,
    event_key TEXT,
    token_id TEXT NOT NULL,
    event_slug TEXT,
    outcome TEXT,
    chart_timestamp_utc TEXT,
    system_received_at_utc TEXT NOT NULL,
    system_inserted_at_utc TEXT NOT NULL,
    source_latency_ms REAL,
    insert_latency_ms REAL,
    mid_price REAL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    depth_top3_bid_size REAL,
    depth_top3_ask_size REAL,
    trade_price REAL,
    trade_size REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(event_token_key) REFERENCES event_tokens(event_token_key)
);

CREATE TABLE IF NOT EXISTS polymarket_order_books (
    order_book_key TEXT PRIMARY KEY,
    event_token_key TEXT,
    token_id TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    bids_json TEXT NOT NULL DEFAULT '[]',
    asks_json TEXT NOT NULL DEFAULT '[]',
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS polymarket_order_book_levels (
    order_book_level_key TEXT PRIMARY KEY,
    order_book_key TEXT,
    event_token_key TEXT,
    token_id TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    level_index INTEGER NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(event_token_key) REFERENCES event_tokens(event_token_key)
);

CREATE TABLE IF NOT EXISTS polymarket_trade_prints (
    trade_print_key TEXT PRIMARY KEY,
    event_token_key TEXT,
    token_id TEXT NOT NULL,
    trade_at_utc TEXT NOT NULL,
    price REAL,
    size REAL,
    side TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS polymarket_updown_pair_snapshots (
    pair_snapshot_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    event_slug TEXT,
    symbol TEXT,
    bucket_timestamp_utc TEXT NOT NULL,
    up_event_token_key TEXT,
    down_event_token_key TEXT,
    up_token_id TEXT,
    down_token_id TEXT,
    up_best_bid REAL,
    up_best_ask REAL,
    up_mid_price REAL,
    down_best_bid REAL,
    down_best_ask REAL,
    down_mid_price REAL,
    up_depth_top3_bid_size REAL,
    up_depth_top3_ask_size REAL,
    down_depth_top3_bid_size REAL,
    down_depth_top3_ask_size REAL,
    source_latency_ms REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(event_key) REFERENCES events(event_key)
);

CREATE TABLE IF NOT EXISTS polymarket_event_path_stats (
    event_path_stats_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    event_slug TEXT,
    symbol TEXT,
    event_start_time_utc TEXT,
    event_end_time_utc TEXT,
    computed_at_utc TEXT NOT NULL,
    first_snapshot_at_utc TEXT,
    last_snapshot_at_utc TEXT,
    snapshot_count INTEGER NOT NULL DEFAULT 0,
    up_first_price REAL,
    up_last_price REAL,
    up_min_price REAL,
    up_max_price REAL,
    up_range REAL,
    up_abs_move_sum REAL,
    up_abs_move_per_minute REAL,
    up_stddev REAL,
    event_price_points_json TEXT NOT NULL DEFAULT '{}',
    pre_event_price_points_json TEXT NOT NULL DEFAULT '{}',
    level_first_touch_seconds_json TEXT NOT NULL DEFAULT '{}',
    tail_comeback_table_json TEXT NOT NULL DEFAULT '{}',
    path_direction TEXT,
    path_efficiency REAL,
    time_to_first_extreme_seconds REAL,
    avg_swing_distance REAL,
    max_swing_distance REAL,
    avg_rolling_30s_range REAL,
    max_rolling_30s_range REAL,
    avg_rolling_60s_range REAL,
    max_rolling_60s_range REAL,
    level_crossing_count INTEGER NOT NULL DEFAULT 0,
    level_crossings_json TEXT NOT NULL DEFAULT '{}',
    price_bucket_counts_json TEXT NOT NULL DEFAULT '{}',
    near_50c_sample_count INTEGER NOT NULL DEFAULT 0,
    extreme_sample_count INTEGER NOT NULL DEFAULT 0,
    rebound_direction_flip_count INTEGER NOT NULL DEFAULT 0,
    strong_rebound_touch_count INTEGER NOT NULL DEFAULT 0,
    pair_sum_range REAL,
    avg_pair_depth_pressure REAL,
    avg_source_latency_ms REAL,
    max_source_latency_ms REAL,
    trade_print_count INTEGER NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(event_key) REFERENCES events(event_key)
);

CREATE TABLE IF NOT EXISTS strategy_replay_candidate_cache_runs (
    candidate_cache_run_key TEXT PRIMARY KEY,
    selector TEXT NOT NULL,
    strategy_signature TEXT NOT NULL,
    forward_mark_horizon_seconds REAL NOT NULL DEFAULT 60,
    generated_at_utc TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    source_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_replay_candidate_scenarios (
    candidate_key TEXT PRIMARY KEY,
    candidate_cache_run_key TEXT NOT NULL,
    selector TEXT NOT NULL,
    strategy_signature TEXT NOT NULL,
    event_key TEXT,
    event_token_key TEXT,
    token_id TEXT,
    event_slug TEXT,
    outcome TEXT,
    system_received_at_utc TEXT,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    depth_top3_bid_size REAL,
    depth_top3_ask_size REAL,
    mid_price REAL,
    forward_best_bid REAL,
    forward_best_ask REAL,
    forward_mid_price REAL,
    forward_mark_at_utc TEXT,
    forward_horizon_seconds REAL,
    profile_context_available INTEGER NOT NULL DEFAULT 0,
    option_path_context_available INTEGER NOT NULL DEFAULT 0,
    replay_tail_touch_count REAL,
    replay_strong_rebounds REAL,
    replay_tail_1c_max_after REAL,
    replay_tail_5c_max_after REAL,
    replay_tail_10c_max_after REAL,
    event_elapsed_seconds REAL,
    time_remaining_seconds REAL,
    score REAL NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS underlying_price_ticks (
    tick_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    exchange_timestamp_utc TEXT,
    price REAL NOT NULL,
    bid REAL,
    ask REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS underlying_candles (
    candle_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    interval TEXT NOT NULL,
    opened_at_utc TEXT NOT NULL,
    closed_at_utc TEXT,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_definitions (
    indicator_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_snapshots (
    snapshot_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    indicator_id TEXT NOT NULL,
    computed_at_utc TEXT NOT NULL,
    direction TEXT,
    confidence REAL,
    signal_value REAL,
    components_json TEXT NOT NULL DEFAULT '{}',
    quality_flags_json TEXT NOT NULL DEFAULT '{}',
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_technical_observer_snapshots (
    observer_snapshot_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    source_url TEXT,
    request_started_at_utc TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    latency_ms INTEGER,
    summary_label TEXT,
    summary_score REAL,
    buy_count INTEGER NOT NULL DEFAULT 0,
    sell_count INTEGER NOT NULL DEFAULT 0,
    neutral_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    component_count INTEGER NOT NULL DEFAULT 0,
    components_json TEXT NOT NULL DEFAULT '{}',
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_technical_observer_components (
    observer_component_key TEXT PRIMARY KEY,
    observer_snapshot_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    component_group TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_value TEXT,
    action TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(observer_snapshot_key) REFERENCES external_technical_observer_snapshots(observer_snapshot_key)
);

CREATE TABLE IF NOT EXISTS event_indicator_context (
    context_key TEXT PRIMARY KEY,
    event_key TEXT,
    event_token_key TEXT,
    symbol TEXT NOT NULL,
    side TEXT,
    event_threshold_price REAL,
    computed_at_utc TEXT NOT NULL,
    underlying_price REAL,
    target_delta_abs REAL,
    target_delta_signed_for_side REAL,
    indicator_summary_json TEXT NOT NULL DEFAULT '{}',
    blocker_summary_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_backtest_results (
    result_key TEXT PRIMARY KEY,
    indicator_id TEXT NOT NULL,
    backtest_run_id TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0,
    hit_rate REAL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_signal_readiness_snapshots (
    readiness_key TEXT PRIMARY KEY,
    data_block TEXT NOT NULL,
    module_id TEXT NOT NULL,
    symbol TEXT,
    generated_at_utc TEXT NOT NULL,
    target_refresh_seconds INTEGER NOT NULL,
    status TEXT NOT NULL,
    latest_source_at_utc TEXT,
    source_age_seconds REAL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_datasets (
    replay_dataset_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS replay_frames (
    replay_frame_key TEXT PRIMARY KEY,
    replay_dataset_key TEXT,
    event_key TEXT,
    event_token_key TEXT,
    replay_timestamp_utc TEXT NOT NULL,
    source_observed_at_utc TEXT,
    decision_at_utc TEXT,
    frame_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_runs (
    replay_run_key TEXT PRIMARY KEY,
    strategy_id TEXT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS replay_candidate_signals (
    replay_candidate_signal_key TEXT PRIMARY KEY,
    replay_run_key TEXT,
    strategy_id TEXT NOT NULL,
    event_token_key TEXT,
    decision_at_utc TEXT NOT NULL,
    signal_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_fill_simulations (
    replay_fill_simulation_key TEXT PRIMARY KEY,
    replay_run_key TEXT,
    replay_candidate_signal_key TEXT,
    fill_model_timestamp_utc TEXT,
    fillability_status TEXT NOT NULL,
    simulation_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_exit_simulations (
    replay_exit_simulation_key TEXT PRIMARY KEY,
    replay_run_key TEXT,
    replay_trade_group_key TEXT,
    exit_status TEXT NOT NULL,
    simulation_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_trade_groups (
    replay_trade_group_key TEXT PRIMARY KEY,
    replay_run_key TEXT,
    strategy_id TEXT NOT NULL,
    event_key TEXT,
    event_token_key TEXT,
    outcome_status TEXT,
    pnl_usd REAL,
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_component_results (
    replay_component_result_key TEXT PRIMARY KEY,
    replay_run_key TEXT,
    component_id TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    hit_rate REAL,
    average_forward_return REAL,
    blocker_reasons_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_reports (
    replay_report_key TEXT PRIMARY KEY,
    replay_run_key TEXT,
    generated_at_utc TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS signal_specs (
    signal_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    variant TEXT NOT NULL,
    version TEXT NOT NULL,
    filename TEXT NOT NULL,
    purpose TEXT NOT NULL,
    event_phase_relevance TEXT NOT NULL,
    refresh_rate_seconds INTEGER,
    time_frames_relevant_json TEXT NOT NULL DEFAULT '[]',
    required_data_blocks_json TEXT NOT NULL DEFAULT '[]',
    validation_target TEXT NOT NULL,
    win_criteria TEXT NOT NULL,
    sample_unit TEXT NOT NULL,
    impact_if_degraded TEXT NOT NULL,
    description TEXT NOT NULL,
    signal_payload_example_json TEXT NOT NULL DEFAULT '{}',
    spec_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_versions (
    signal_version_key TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_signal_id TEXT,
    supersedes_signal_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(signal_id, version),
    FOREIGN KEY(signal_id) REFERENCES signal_specs(signal_id)
);

CREATE TABLE IF NOT EXISTS signal_queue_items (
    queue_item_key TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    version TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    owner_id TEXT,
    owned_at_utc TEXT,
    owner_expires_at_utc TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    parent_signal_id TEXT,
    supersedes_signal_id TEXT,
    last_error TEXT,
    queue_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES signal_specs(signal_id)
);

CREATE TABLE IF NOT EXISTS signal_validation_runs (
    validation_run_key TEXT PRIMARY KEY,
    queue_item_key TEXT,
    signal_id TEXT NOT NULL,
    version TEXT NOT NULL,
    phase TEXT NOT NULL,
    owner_id TEXT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(queue_item_key) REFERENCES signal_queue_items(queue_item_key),
    FOREIGN KEY(signal_id) REFERENCES signal_specs(signal_id)
);

CREATE TABLE IF NOT EXISTS signal_validation_results (
    validation_result_key TEXT PRIMARY KEY,
    validation_run_key TEXT,
    queue_item_key TEXT,
    signal_id TEXT NOT NULL,
    version TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    hit_rate REAL,
    average_forward_return REAL,
    blockers_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(validation_run_key) REFERENCES signal_validation_runs(validation_run_key),
    FOREIGN KEY(queue_item_key) REFERENCES signal_queue_items(queue_item_key),
    FOREIGN KEY(signal_id) REFERENCES signal_specs(signal_id)
);

CREATE TABLE IF NOT EXISTS signal_observations (
    observation_key TEXT PRIMARY KEY,
    validation_run_key TEXT,
    signal_id TEXT NOT NULL,
    version TEXT NOT NULL,
    phase TEXT NOT NULL,
    event_key TEXT,
    event_token_key TEXT,
    decision_at_utc TEXT NOT NULL,
    emitted_signal INTEGER NOT NULL DEFAULT 0,
    observed_value REAL,
    expected_direction TEXT,
    outcome_direction TEXT,
    hit INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    inserted_at_utc TEXT NOT NULL,
    FOREIGN KEY(validation_run_key) REFERENCES signal_validation_runs(validation_run_key),
    FOREIGN KEY(signal_id) REFERENCES signal_specs(signal_id)
);

CREATE TABLE IF NOT EXISTS signal_artifacts (
    signal_artifact_key TEXT PRIMARY KEY,
    signal_id TEXT,
    version TEXT,
    artifact_type TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    artifact_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS v_crypto_options_app_signal_validation_status AS
    SELECT
        s.signal_id,
        s.family,
        s.signal_type,
        s.variant,
        s.version,
        s.purpose,
        s.impact_if_degraded,
        q.queue_item_key,
        q.phase AS queue_phase,
        q.status AS queue_status,
        q.owner_id,
        q.owner_expires_at_utc,
        q.attempt_count,
        r.phase AS latest_result_phase,
        r.status AS latest_result_status,
        r.sample_count AS latest_sample_count,
        r.hit_rate AS latest_hit_rate,
        r.average_forward_return AS latest_average_forward_return,
        r.evaluated_at_utc AS latest_evaluated_at_utc,
        r.blockers_json AS latest_blockers_json
    FROM signal_specs s
    LEFT JOIN signal_queue_items q
      ON q.signal_id = s.signal_id
     AND q.version = s.version
    LEFT JOIN signal_validation_results r
      ON r.validation_result_key = (
        SELECT rr.validation_result_key
        FROM signal_validation_results rr
        WHERE rr.signal_id = s.signal_id
          AND rr.version = s.version
        ORDER BY rr.evaluated_at_utc DESC
        LIMIT 1
     );

CREATE TABLE IF NOT EXISTS strategy_specs (
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    spec_json TEXT NOT NULL,
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(strategy_id, strategy_version)
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_version_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_readiness (
    readiness_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    readiness_state TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_promotion_state (
    promotion_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    promotion_state TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_run_configs (
    strategy_run_config_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_validation_runs (
    strategy_validation_run_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    run_type TEXT NOT NULL,
    run_phase TEXT NOT NULL,
    run_status TEXT NOT NULL,
    run_id TEXT,
    validation_run_id TEXT,
    signal_dependencies_json TEXT NOT NULL DEFAULT '[]',
    scoped_live_flags_json TEXT NOT NULL DEFAULT '{}',
    max_notional_usd REAL,
    max_events INTEGER,
    max_trades INTEGER,
    max_wall_time_seconds INTEGER,
    stop_rules_json TEXT NOT NULL DEFAULT '{}',
    lifecycle_audit_status TEXT,
    reconciliation_status TEXT,
    budget_ledger_required INTEGER NOT NULL DEFAULT 0,
    cash_balance_status TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_budget_ledger (
    validation_budget_ledger_key TEXT PRIMARY KEY,
    validation_run_id TEXT NOT NULL,
    strategy_or_component_id TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    budget_cap_usd REAL NOT NULL,
    notional_submitted_usd REAL NOT NULL DEFAULT 0,
    notional_filled_usd REAL NOT NULL DEFAULT 0,
    realized_pnl_usd REAL NOT NULL DEFAULT 0,
    open_cost_usd REAL NOT NULL DEFAULT 0,
    remaining_validation_budget_usd REAL,
    cash_balance_before_usd REAL,
    cash_balance_after_usd REAL,
    cash_balance_status TEXT NOT NULL DEFAULT 'cash_balance_unavailable',
    hard_stop_triggered INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    lifecycle_audit_status TEXT,
    reconciliation_status TEXT,
    ledger_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_candidates (
    candidate_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT,
    event_key TEXT,
    event_token_key TEXT,
    candidate_side TEXT,
    decision_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    blocker_json TEXT NOT NULL DEFAULT '[]',
    candidate_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_intents (
    intent_key TEXT PRIMARY KEY,
    candidate_key TEXT,
    strategy_id TEXT NOT NULL,
    event_token_key TEXT,
    intent_type TEXT NOT NULL,
    order_type TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    intent_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_key TEXT PRIMARY KEY,
    intent_key TEXT,
    exchange_order_id TEXT,
    status TEXT NOT NULL,
    order_json TEXT NOT NULL DEFAULT '{}',
    submitted_at_utc TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_key TEXT PRIMARY KEY,
    order_key TEXT,
    event_token_key TEXT,
    filled_size REAL,
    filled_price REAL,
    filled_at_utc TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    position_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    event_token_key TEXT NOT NULL,
    shares REAL NOT NULL,
    cost_basis_usd REAL,
    status TEXT NOT NULL,
    opened_at_utc TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exit_plans (
    exit_plan_key TEXT PRIMARY KEY,
    position_key TEXT,
    coverage_type TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exit_orders (
    exit_order_key TEXT PRIMARY KEY,
    exit_plan_key TEXT,
    order_key TEXT,
    status TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlements (
    settlement_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    resolved_outcome TEXT,
    settled_at_utc TEXT,
    settlement_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    pnl_snapshot_key TEXT PRIMARY KEY,
    strategy_id TEXT,
    run_id TEXT,
    computed_at_utc TEXT NOT NULL,
    realized_pnl_usd REAL,
    unrealized_pnl_usd REAL,
    active_cost_usd REAL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_gate_evaluations (
    risk_gate_evaluation_key TEXT PRIMARY KEY,
    strategy_id TEXT,
    gate_id TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stop_gate_events (
    stop_gate_event_key TEXT PRIMARY KEY,
    strategy_id TEXT,
    run_id TEXT,
    gate_id TEXT NOT NULL,
    triggered_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS exposure_snapshots (
    exposure_snapshot_key TEXT PRIMARY KEY,
    strategy_id TEXT,
    event_key TEXT,
    event_token_key TEXT,
    computed_at_utc TEXT NOT NULL,
    exposure_json TEXT NOT NULL DEFAULT '{}',
    inserted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_status_snapshots (
    system_status_snapshot_key TEXT PRIMARY KEY,
    generated_at_utc TEXT NOT NULL,
    status_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_reports (
    strategy_report_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_reports (
    run_report_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE VIEW IF NOT EXISTS v_crypto_options_app_latest_polymarket_prices AS
    SELECT p.*
    FROM polymarket_price_ticks p
    JOIN (
        SELECT token_id, MAX(system_received_at_utc) AS latest_received_at_utc
        FROM polymarket_price_ticks
        GROUP BY token_id
    ) latest
      ON latest.token_id = p.token_id
     AND latest.latest_received_at_utc = p.system_received_at_utc;

CREATE VIEW IF NOT EXISTS v_crypto_options_app_latest_underlying_prices AS
    SELECT t.*
    FROM underlying_price_ticks t
    JOIN (
        SELECT symbol, MAX(observed_at_utc) AS latest_observed_at_utc
        FROM underlying_price_ticks
        GROUP BY symbol
    ) latest
      ON latest.symbol = t.symbol
     AND latest.latest_observed_at_utc = t.observed_at_utc;

CREATE VIEW IF NOT EXISTS v_crypto_options_app_latest_external_technical_observers AS
    SELECT s.*
    FROM external_technical_observer_snapshots s
    JOIN (
        SELECT provider, symbol, interval, MAX(completed_at_utc) AS latest_completed_at_utc
        FROM external_technical_observer_snapshots
        GROUP BY provider, symbol, interval
    ) latest
      ON latest.provider = s.provider
     AND latest.symbol = s.symbol
     AND latest.interval = s.interval
     AND latest.latest_completed_at_utc = s.completed_at_utc;

CREATE VIEW IF NOT EXISTS v_crypto_options_app_latest_data_signal_readiness AS
    SELECT r.*
    FROM data_signal_readiness_snapshots r
    JOIN (
        SELECT data_block, module_id, COALESCE(symbol, '') AS symbol_key,
               MAX(generated_at_utc) AS latest_generated_at_utc
        FROM data_signal_readiness_snapshots
        GROUP BY data_block, module_id, COALESCE(symbol, '')
    ) latest
      ON latest.data_block = r.data_block
     AND latest.module_id = r.module_id
     AND latest.symbol_key = COALESCE(r.symbol, '')
     AND latest.latest_generated_at_utc = r.generated_at_utc;

CREATE VIEW IF NOT EXISTS v_crypto_options_app_latest_profile_distributions AS
    SELECT s.*
    FROM profile_distribution_snapshots s
    JOIN (
        SELECT COALESCE(event_key, event_slug, '') AS event_ref,
               MAX(computed_at_utc) AS latest_computed_at_utc
        FROM profile_distribution_snapshots
        GROUP BY COALESCE(event_key, event_slug, '')
    ) latest
      ON latest.event_ref = COALESCE(s.event_key, s.event_slug, '')
     AND latest.latest_computed_at_utc = s.computed_at_utc;

CREATE INDEX IF NOT EXISTS idx_crypto_options_event_path_stats_computed
    ON polymarket_event_path_stats(computed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_event_path_stats_event_computed
    ON polymarket_event_path_stats(event_key, computed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_replay_cache_run_selector_signature
    ON strategy_replay_candidate_cache_runs(selector, strategy_signature, generated_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_replay_cache_candidate_run_score
    ON strategy_replay_candidate_scenarios(candidate_cache_run_key, score DESC, system_received_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_pair_snapshots_event_bucket
    ON polymarket_updown_pair_snapshots(event_key, bucket_timestamp_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_price_ticks_received
    ON polymarket_price_ticks(system_received_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_price_ticks_token_received
    ON polymarket_price_ticks(event_token_key, system_received_at_utc);

CREATE INDEX IF NOT EXISTS idx_crypto_options_price_ticks_event_received
    ON polymarket_price_ticks(event_key, system_received_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_profile_distribution_event_computed
    ON profile_distribution_snapshots(event_key, computed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_profile_distribution_components_snapshot_weight
    ON profile_distribution_components(distribution_snapshot_key, final_weight DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_external_observers_symbol_observed
    ON external_technical_observer_snapshots(symbol, observed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_indicator_snapshots_symbol_computed
    ON indicator_snapshots(symbol, computed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_underlying_ticks_symbol_observed
    ON underlying_price_ticks(symbol, observed_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_signal_results_signal_phase
    ON signal_validation_results(signal_id, version, phase, evaluated_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_signal_results_signal_evaluated
    ON signal_validation_results(signal_id, version, evaluated_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_crypto_options_signal_observations_signal_event
    ON signal_observations(signal_id, version, event_key);

CREATE INDEX IF NOT EXISTS idx_crypto_options_signal_observations_signal_phase_event
    ON signal_observations(signal_id, version, phase, event_key, event_token_key);

CREATE INDEX IF NOT EXISTS idx_crypto_options_signal_observations_signal_phase_decision
    ON signal_observations(signal_id, version, phase, decision_at_utc);

CREATE INDEX IF NOT EXISTS idx_crypto_options_signal_queue_signal_version
    ON signal_queue_items(signal_id, version, phase, status);
"""
