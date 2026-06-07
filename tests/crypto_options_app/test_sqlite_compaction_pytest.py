from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.db.sqlite_compaction import SQLiteCompactionConfig, compact_sqlite_for_local_services


def test_sqlite_compaction_preserves_replay_columns_and_drops_raw_bloat(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    initialize_schema(source)
    now = datetime.now(UTC)
    old = now - timedelta(hours=24)
    large_payload = {"bids": [{"price": "0.40", "size": "10"}] * 20}

    conn = sqlite3.connect(source)
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
                json.dumps(large_payload),
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
                (key, "token-up", "token-up", observed_at, "[]", "[]", json.dumps(large_payload), now.isoformat()),
            )
        conn.execute(
            """
            INSERT INTO polymarket_order_book_levels(
                order_book_level_key, order_book_key, event_token_key, token_id,
                observed_at_utc, side, price, size, level_index, source_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("level-1", "recent-book", "token-up", "token-up", now.isoformat(), "bid", 0.4, 10.0, 0, "{}", now.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO polymarket_updown_pair_snapshots(
                pair_snapshot_key, event_key, event_slug, symbol, bucket_timestamp_utc,
                up_event_token_key, down_event_token_key, up_token_id, down_token_id,
                up_best_bid, up_best_ask, up_mid_price, down_best_bid, down_best_ask, down_mid_price,
                up_depth_top3_bid_size, up_depth_top3_ask_size, down_depth_top3_bid_size, down_depth_top3_ask_size,
                source_latency_ms, source_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pair-1",
                "event-1",
                "btc-updown-5m-test",
                "BTC",
                now.isoformat(),
                "token-up",
                "token-down",
                "token-up",
                "token-down",
                0.41,
                0.43,
                0.42,
                0.57,
                0.59,
                0.58,
                20.0,
                18.0,
                19.0,
                21.0,
                1.0,
                json.dumps(large_payload),
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = compact_sqlite_for_local_services(
        SQLiteCompactionConfig(
            source_path=source,
            target_path=target,
            recent_raw_book_hours=6,
            batch_size=2,
        )
    )

    assert result.status == "ok"
    compacted = sqlite3.connect(target)
    try:
        assert compacted.execute("SELECT COUNT(*) FROM polymarket_price_ticks").fetchone()[0] == 1
        assert compacted.execute("SELECT COUNT(*) FROM polymarket_updown_pair_snapshots").fetchone()[0] == 1
        assert compacted.execute("SELECT COUNT(*) FROM polymarket_order_book_levels").fetchone()[0] == 0
        assert compacted.execute("SELECT COUNT(*) FROM polymarket_order_books").fetchone()[0] == 1
        assert compacted.execute("SELECT order_book_key FROM polymarket_order_books").fetchone()[0] == "recent-book"
        tick_source = compacted.execute("SELECT source_json FROM polymarket_price_ticks").fetchone()[0]
        assert json.loads(tick_source)["retained"] == "columns_only"
        assert compacted.execute("SELECT best_bid, best_ask FROM polymarket_price_ticks").fetchone() == (0.41, 0.43)
    finally:
        compacted.close()
