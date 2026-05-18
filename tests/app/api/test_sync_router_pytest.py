from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.models import SyncTriggerResponse
from app.api.routers import sync as sync_router


class FakeCursor:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, count: int = 0) -> None:
        self.rows = rows or []
        self.count = count

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query: str, params: object | None = None) -> None:
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return {"count": self.count}


class FakeConnection:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, count: int = 0) -> None:
        self.rows = rows or []
        self.count = count

    def cursor(self, *_, **__):
        return FakeCursor(rows=self.rows, count=self.count)


def test_sync_nba_schedule_forwards_final_context_options_pytest(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run_nba_metadata_sync(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            sync_run_id=None,
            status="success",
            rows_read=7,
            rows_written=3,
            final_context_games=2,
        )

    def fake_with_job_run(connection: object, *, job_code: str, description: str, runner: Any) -> SyncTriggerResponse:
        _ = (connection, job_code, description)
        summary = runner()
        return SyncTriggerResponse(
            status=str(summary.get("status")),
            rows_read=summary.get("rows_read"),
            rows_written=summary.get("rows_written"),
            summary=summary,
        )

    monkeypatch.setattr(sync_router, "run_nba_metadata_sync", fake_run_nba_metadata_sync)
    monkeypatch.setattr(sync_router, "_with_job_run", fake_with_job_run)
    app = create_app()
    app.dependency_overrides[get_db_connection] = lambda: object()
    client = TestClient(app)

    response = client.post(
        "/v1/sync/nba/schedule",
        json={
            "season": "2025-26",
            "anchor_date": "2026-05-18",
            "schedule_window_days": 2,
            "include_live_snapshots": True,
            "include_play_by_play": True,
            "include_final_context": True,
            "final_context_game_limit": 2,
        },
    )

    assert response.status_code == 202
    assert captured["anchor_date"] == date(2026, 5, 18)
    assert captured["include_final_context"] is True
    assert captured["final_context_game_limit"] == 2
    assert response.json()["summary"]["final_context_games"] == 2


def test_session_date_polymarket_probes_use_repo_schedule_rows_pytest() -> None:
    probes = sync_router._select_nba_event_probes_from_schedule(
        FakeConnection(
            rows=[
                {
                    "game_date": date(2026, 5, 17),
                    "away_team_slug": "CLE",
                    "home_team_slug": "DET",
                    "game_status": 3,
                    "game_start_time": None,
                    "game_id": "0042500204",
                }
            ]
        ),
        session_date=date(2026, 5, 17),
        max_finished=6,
        max_live=6,
        max_upcoming=6,
        include_upcoming=True,
        stream_sample_count=2,
        stream_sample_interval_sec=0.5,
        stream_max_outcomes=30,
    )

    assert len(probes) == 1
    assert probes[0].step_code == "v0_4_1_session_finished_nba-cle-det-2026-05-17"
    assert probes[0].url.endswith("/nba-cle-det-2026-05-17")
    assert probes[0].history_mode == "game_period"


def test_polymarket_events_returns_structured_blocker_when_session_schedule_missing_pytest() -> None:
    app = create_app()
    app.dependency_overrides[get_db_connection] = lambda: FakeConnection(count=0)
    client = TestClient(app)

    try:
        response = client.post(
            "/v1/sync/polymarket/events",
            json={"probe_set": "today_nba", "session_date": "2026-05-17"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["rows_read"] == 0
    assert payload["summary"]["blocker_category"] == "no_polymarket_probes_selected"
    assert "nba_schedule_rows_missing_for_session_date" in payload["summary"]["reason_codes"]
