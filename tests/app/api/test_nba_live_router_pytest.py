from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.routers.nba_live as nba_live_router
from app.api.main import create_app


def test_get_live_run_shadow_route_forwards_query_params_pytest(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeService:
        def capture_run_shadow(
            self,
            run_id: str,
            *,
            game_ids: list[str] | None = None,
            families: list[str] | None = None,
            persist: bool = True,
        ) -> dict[str, object]:
            captured["run_id"] = run_id
            captured["game_ids"] = game_ids
            captured["families"] = families
            captured["persist"] = persist
            return {
                "run_id": run_id,
                "game_ids": game_ids or [],
                "families": families or [],
                "persist": persist,
                "summary": [],
                "family_shadow": [],
            }

    monkeypatch.setattr(nba_live_router, "get_live_run_service", lambda: _FakeService())

    client = TestClient(create_app())
    response = client.get(
        "/v1/nba/live/runs/live-demo/shadow",
        params=[
            ("game_id", "0042500173"),
            ("game_id", "0042500113"),
            ("family", "quarter_open_reprice"),
            ("family", "inversion"),
            ("persist", "false"),
        ],
    )

    assert response.status_code == 200
    assert captured == {
        "run_id": "live-demo",
        "game_ids": ["0042500173", "0042500113"],
        "families": ["quarter_open_reprice", "inversion"],
        "persist": False,
    }
