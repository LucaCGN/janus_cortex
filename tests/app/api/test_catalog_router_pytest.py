from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app


def test_list_events_filters_by_linked_nba_game_start_time_pytest() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.query = ""
            self.params = ()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=()) -> None:
            self.query = str(query)
            self.params = tuple(params)

        def fetchall(self):
            return [
                {
                    "event_id": "5e8b16e5-7326-5c22-8a6d-d59829676bd8",
                    "event_type_id": "event-type-1",
                    "event_type_code": "sports.nba",
                    "information_profile_id": None,
                    "information_profile_code": None,
                    "title": "Pistons at Cavaliers",
                    "canonical_slug": "nba-det-cle-2026-05-11",
                    "status": "open",
                    "start_time": datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc),
                    "catalog_start_time": datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
                    "linked_nba_game_start_time": datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc),
                    "linked_nba_game_id": "0042500204",
                    "end_time": None,
                    "resolution_time": None,
                    "metadata_json": {},
                    "created_at": datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
                }
            ]

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_obj = FakeCursor()

        def cursor(self, *_, **__):
            return self.cursor_obj

    fake_connection = FakeConnection()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = lambda: fake_connection
    try:
        response = client.get(
            "/v1/events",
            params={
                "event_type_code": "sports.nba",
                "start_time_from": "2026-05-11T00:00:00Z",
                "start_time_to": "2026-05-12T03:00:00Z",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "LEFT JOIN LATERAL" in fake_connection.cursor_obj.query
    assert "COALESCE(nba_link.linked_start_time, e.start_time) >= %s" in fake_connection.cursor_obj.query
    assert "COALESCE(nba_link.linked_start_time, e.start_time) <= %s" in fake_connection.cursor_obj.query
    assert "ORDER BY COALESCE(nba_link.linked_start_time, e.start_time)" in fake_connection.cursor_obj.query
    assert fake_connection.cursor_obj.params[0] == "sports.nba"
    assert fake_connection.cursor_obj.params[-2:] == (200, 0)
    payload = response.json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["canonical_slug"] == "nba-det-cle-2026-05-11"
    assert item["start_time"] == "2026-05-12T00:00:00+00:00"
    assert item["catalog_start_time"] == "2026-05-05T00:00:00+00:00"
    assert item["linked_nba_game_start_time"] == "2026-05-12T00:00:00+00:00"
    assert item["linked_nba_game_id"] == "0042500204"
