from __future__ import annotations

from datetime import datetime, timezone

from app.data.pipelines.daily.wnba import sync_postgres as sync


def test_json_or_none_serializes_datetime_payloads() -> None:
    payload = {
        "fetched_at": datetime(2026, 5, 18, 4, 20, tzinfo=timezone.utc),
        "nested": [{"game_date": datetime(2026, 5, 18, tzinfo=timezone.utc)}],
    }

    wrapped = sync._json_or_none(payload)

    assert wrapped is not None
    assert wrapped.adapted["fetched_at"] == "2026-05-18T04:20:00+00:00"
    assert wrapped.adapted["nested"][0]["game_date"] == "2026-05-18T00:00:00+00:00"
