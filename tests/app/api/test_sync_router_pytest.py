from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.models import SyncTriggerResponse
from app.api.routers import sync as sync_router


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
            "schedule_window_days": 2,
            "include_live_snapshots": True,
            "include_play_by_play": True,
            "include_final_context": True,
            "final_context_game_limit": 2,
        },
    )

    assert response.status_code == 202
    assert captured["include_final_context"] is True
    assert captured["final_context_game_limit"] == 2
    assert response.json()["summary"]["final_context_games"] == 2
