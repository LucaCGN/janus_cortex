from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.db.sqlite_retention import SQLiteRetentionConfig, trim_sqlite_raw_option_storage


def test_sqlite_retention_trims_raw_depth_and_preserves_replay_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto_options_data.sqlite"
    initialize_schema(db_path)
    now = datetime.now(UTC)
    old = now - timedelta(hours=24)
    payload = json.dumps({"raw": "x" * 1000})
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO polymarket_price_ticks(
                price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                source_latency_ms, insert_latency_ms, mid_price, best_bid, best_ask,
                spread, depth_top3_bid_size, depth_top3_ask_size, trade_price, trade_size, source_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tick-1",
                "token-up",
                "event-1",
                "token-up",
                "btc-updown-5m-test",
                "Up",
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                1.0,
                1.0,
                0.42,
                0.41,
                0.43,
                0.02,
                20.0,
                18.0,
                None,
                None,
                payload,
            ),
        )
        for key, observed_at in (("old-book", old.isoformat()), ("recent-book", now.isoformat())):
            conn.execute(
                """
                INSERT INTO polymarket_order_books(
                    order_book_key, event_token_key, token_id, observed_at_utc,
                    bids_json, asks_json, source_json, inserted_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key, "token-up", "token-up", observed_at, "[]", "[]", payload, now.isoformat()),
            )
        conn.execute(
            """
            INSERT INTO polymarket_order_book_levels(
                order_book_level_key, order_book_key, event_token_key, token_id,
                observed_at_utc, side, price, size, level_index, source_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("level-1", "recent-book", "token-up", "token-up", now.isoformat(), "bid", 0.4, 10.0, 0, payload, now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    result = trim_sqlite_raw_option_storage(
        SQLiteRetentionConfig(
            db_path=db_path,
            recent_raw_book_hours=2,
            batch_size=1,
        )
    )

    assert result.status == "ok"
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM polymarket_order_book_levels").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM polymarket_order_books").fetchone()[0] == 1
        assert conn.execute("SELECT order_book_key FROM polymarket_order_books").fetchone()[0] == "recent-book"
        row = conn.execute(
            "SELECT best_bid, best_ask, source_json FROM polymarket_price_ticks WHERE price_tick_key='tick-1'"
        ).fetchone()
        assert row[0:2] == (0.41, 0.43)
        assert json.loads(row[2])["retained"] == "columns_only"
    finally:
        conn.close()
