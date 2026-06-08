from __future__ import annotations

import sqlite3
from pathlib import Path

from crypto_options_app.db.connection import connect, count_rows, table_exists
from crypto_options_app.db.imports import import_legacy_shards, run_import_parity_checks
from crypto_options_app.db.schema import EXPECTED_TABLES, SCHEMA_VERSION, initialize_schema, list_tables
from crypto_options_app.trading.candidates import has_executable_event_token


def test_canonical_db_initializes_all_schema_groups_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "canonical.sqlite"

    initialized_path = initialize_schema(db_path)

    assert initialized_path == db_path
    with connect(db_path) as conn:
        tables = list_tables(conn)
        assert EXPECTED_TABLES.issubset(tables)
        assert table_exists(conn, "profiles")
        assert table_exists(conn, "polymarket_price_ticks")
        assert table_exists(conn, "strategy_candidates")
        assert table_exists(conn, "strategy_validation_runs")
        assert table_exists(conn, "validation_budget_ledger")
        assert table_exists(conn, "risk_gate_evaluations")
        assert table_exists(conn, "run_reports")
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key='schema_version'"
        ).fetchone()
        assert row["setting_value"] == SCHEMA_VERSION
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM v_crypto_options_app_latest_polymarket_prices"
        ).fetchone()["c"] == 0
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='polymarket_price_ticks'"
            )
        }
        assert "idx_crypto_options_price_ticks_received" in indexes
        assert "idx_crypto_options_price_ticks_token_received" in indexes
        assert "idx_crypto_options_price_ticks_event_received" in indexes


def test_imports_legacy_profile_and_market_shards_with_parity_pytest(tmp_path: Path) -> None:
    profile_db = tmp_path / "legacy_profiles.sqlite"
    market_db = tmp_path / "legacy_market.sqlite"
    target_db = tmp_path / "canonical.sqlite"
    _create_profile_fixture(profile_db)
    _create_market_fixture(market_db)

    result = import_legacy_shards(
        target_db_path=target_db,
        profile_db_path=profile_db,
        market_db_path=market_db,
    )
    parity = run_import_parity_checks(
        target_db_path=target_db,
        profile_db_path=profile_db,
        market_db_path=market_db,
    )

    assert result["orders_allowed"] is False
    assert result["live_trading_authorized"] is False
    assert parity["passed"] is True
    with connect(target_db) as conn:
        assert count_rows(conn, "profiles") == 1
        assert count_rows(conn, "profile_raw_activity") == 1
        assert count_rows(conn, "profile_event_orders") == 1
        assert count_rows(conn, "profile_grades") == 1
        assert count_rows(conn, "profile_generator_scores") == 1
        assert count_rows(conn, "profile_buying_ahead") == 1
        assert count_rows(conn, "events") == 1
        assert count_rows(conn, "event_tokens") == 1
        assert count_rows(conn, "polymarket_price_ticks") == 1
        latest = conn.execute(
            "SELECT event_token_key, mid_price FROM v_crypto_options_app_latest_polymarket_prices"
        ).fetchone()
        assert latest["event_token_key"] == "event-1:up"
        assert latest["mid_price"] == 0.62

    with sqlite3.connect(profile_db) as source:
        assert source.execute("SELECT COUNT(*) FROM profile_universe").fetchone()[0] == 1
    with sqlite3.connect(market_db) as source:
        assert source.execute("SELECT COUNT(*) FROM polymarket_crypto_event_universe").fetchone()[0] == 1


def test_candidates_without_event_token_key_are_not_executable_pytest() -> None:
    assert has_executable_event_token({"event_token_key": "event-1:up"}) is True
    assert has_executable_event_token({"event_key": "event-1"}) is False
    assert has_executable_event_token({}) is False


def _create_profile_fixture(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE profile_universe (
                profile_key TEXT PRIMARY KEY,
                normalized_ref TEXT,
                handle TEXT,
                proxy_wallet TEXT,
                profile_name TEXT,
                first_seen_at_utc TEXT,
                last_seen_at_utc TEXT,
                latest_activity_utc TEXT
            );

            CREATE TABLE profile_refs (
                normalized_ref TEXT PRIMARY KEY,
                profile_key TEXT,
                raw_ref TEXT,
                handle TEXT,
                address TEXT,
                source TEXT,
                first_seen_at_utc TEXT,
                last_seen_at_utc TEXT
            );

            CREATE TABLE profile_raw_activity (
                raw_activity_key TEXT PRIMARY KEY,
                profile_key TEXT,
                event_key TEXT,
                event_token_key TEXT,
                event_slug TEXT,
                condition_id TEXT,
                market_id TEXT,
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
                buying_ahead INTEGER,
                active_during_event INTEGER
            );

            CREATE TABLE profile_grade_current (
                profile_key TEXT PRIMARY KEY,
                evaluated_at_utc TEXT,
                grade TEXT,
                score REAL,
                trading_style TEXT,
                trading_style_detail TEXT,
                frequency_class TEXT
            );

            CREATE TABLE profile_signal_generator_scores (
                profile_key TEXT,
                generator_id TEXT,
                evaluated_at_utc TEXT,
                account_type TEXT,
                grade TEXT,
                score REAL,
                status TEXT,
                can_emit_live INTEGER,
                usage_eligible INTEGER
            );

            CREATE TABLE profile_event_timing_links (
                profile_key TEXT,
                event_key TEXT,
                raw_activity_key TEXT,
                token_id TEXT,
                outcome_side TEXT,
                activity_at_utc TEXT,
                event_start_time_utc TEXT,
                seconds_before_event_start REAL,
                buying_ahead INTEGER
            );
            """
        )
        conn.execute(
            """
            INSERT INTO profile_universe
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "profile-1",
                "@profile1",
                "profile1",
                "0xabc",
                "Profile One",
                "2026-06-02T00:00:00Z",
                "2026-06-02T01:00:00Z",
                "2026-06-02T01:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO profile_refs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "@profile1",
                "profile-1",
                "@profile1",
                "profile1",
                "0xabc",
                "fixture",
                "2026-06-02T00:00:00Z",
                "2026-06-02T01:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO profile_raw_activity
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "raw-1",
                "profile-1",
                "event-1",
                "event-1:up",
                "btc-updown-1",
                "cond-1",
                "market-1",
                "btc-market-1",
                "BTC",
                "BUY",
                "Up",
                "token-up",
                0.6,
                5.0,
                3.0,
                "2026-06-02T00:59:00Z",
                "2026-06-02T00:59:01Z",
                "2026-06-02T01:00:00Z",
                "2026-06-02T01:05:00Z",
                60.0,
                1,
                0,
            ),
        )
        conn.execute(
            "INSERT INTO profile_grade_current VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "profile-1",
                "2026-06-02T01:00:00Z",
                "S+",
                92.5,
                "outcome_predictor",
                "fixture",
                "high",
            ),
        )
        conn.execute(
            "INSERT INTO profile_signal_generator_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "profile-1",
                "outcome_expectation_v1",
                "2026-06-02T01:00:00Z",
                "outcome_predictor",
                "S+",
                92.5,
                "active",
                1,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO profile_event_timing_links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "profile-1",
                "event-1",
                "raw-1",
                "token-up",
                "Up",
                "2026-06-02T00:59:00Z",
                "2026-06-02T01:00:00Z",
                60.0,
                1,
            ),
        )


def _create_market_fixture(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE polymarket_crypto_event_universe (
                event_key TEXT,
                event_token_key TEXT,
                event_slug TEXT,
                condition_id TEXT,
                market_id TEXT,
                market_slug TEXT,
                symbol TEXT,
                cadence_seconds INTEGER,
                event_start_time_utc TEXT,
                event_end_time_utc TEXT,
                settlement_threshold REAL,
                token_id TEXT,
                outcome TEXT,
                active INTEGER,
                closed INTEGER
            );

            CREATE TABLE polymarket_event_price_ticks (
                event_price_tick_key TEXT PRIMARY KEY,
                event_key TEXT,
                event_token_key TEXT,
                event_slug TEXT,
                condition_id TEXT,
                token_id TEXT,
                outcome TEXT,
                chart_timestamp_utc TEXT,
                system_received_at_utc TEXT,
                system_inserted_at_utc TEXT,
                source_latency_ms REAL,
                insert_latency_ms REAL,
                mid_price REAL,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                depth_top3_bid_size REAL,
                depth_top3_ask_size REAL,
                trade_price REAL,
                trade_size REAL
            );

            CREATE TABLE crypto_price_ticks (
                tick_key TEXT PRIMARY KEY,
                symbol TEXT,
                source TEXT,
                observed_at_utc TEXT,
                exchange_timestamp_utc TEXT,
                price REAL,
                bid REAL,
                ask REAL,
                inserted_at_utc TEXT
            );

            CREATE TABLE crypto_indicator_snapshots (
                snapshot_key TEXT PRIMARY KEY,
                symbol TEXT,
                interval TEXT,
                indicator_id TEXT,
                computed_at_utc TEXT,
                direction TEXT,
                confidence REAL,
                signal_value REAL,
                components_json TEXT,
                quality_flags_json TEXT,
                inserted_at_utc TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO polymarket_crypto_event_universe VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-1",
                "event-1:up",
                "btc-updown-1",
                "cond-1",
                "market-1",
                "btc-market-1",
                "BTC",
                300,
                "2026-06-02T01:00:00Z",
                "2026-06-02T01:05:00Z",
                100000.0,
                "token-up",
                "Up",
                1,
                0,
            ),
        )
        conn.execute(
            "INSERT INTO polymarket_event_price_ticks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "price-1",
                "event-1",
                "event-1:up",
                "btc-updown-1",
                "cond-1",
                "token-up",
                "Up",
                "2026-06-02T01:00:01Z",
                "2026-06-02T01:00:02Z",
                "2026-06-02T01:00:02Z",
                100.0,
                10.0,
                0.62,
                0.61,
                0.63,
                0.02,
                100.0,
                100.0,
                0.62,
                5.0,
            ),
        )
        conn.execute(
            "INSERT INTO crypto_price_ticks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "tick-1",
                "BTC",
                "fixture",
                "2026-06-02T01:00:02Z",
                "2026-06-02T01:00:01Z",
                100050.0,
                100049.0,
                100051.0,
                "2026-06-02T01:00:02Z",
            ),
        )
        conn.execute(
            "INSERT INTO crypto_indicator_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "indicator-1",
                "BTC",
                "5m",
                "target_relative_ema_momentum_v1",
                "2026-06-02T01:00:02Z",
                "up",
                0.8,
                1.0,
                "{}",
                "{}",
                "2026-06-02T01:00:02Z",
            ),
        )
