from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.modules.agentic import runtime_control


EVENT_ID = "nba-okc-sas-2026-05-24"


def test_runtime_control_router_updates_and_reads_back_config_pytest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_control, "artifacts_root", lambda: tmp_path)

    client = TestClient(create_app())
    update = client.put(
        f"/v1/runtime/event-controls/{EVENT_ID}",
        params={"session_date": "2026-05-25"},
        json={
            "actor": "codex",
            "reason": "pytest enable deterministic signal",
            "source": "pytest",
            "signal_source_toggles": {"deterministic": True, "llm": False},
            "parameters": {"event_cap_usd": 20, "cooldown_seconds": 60, "min_confidence": 0.5},
            "stop_with_positions_policy": {
                "enabled": True,
                "max_recovery_delay_seconds": 120,
                "backup_restart_required": True,
                "set_sell_targets_if_restart_blocked": True,
                "sell_targets_must_use_janus_gates": True,
                "raw_order_bypass_allowed": False,
                "policy_action": "restart_or_set_sell_targets",
                "reason": "pytest stop recovery",
            },
        },
    )

    assert update.status_code == 200
    assert update.json()["live_order_impact"] == "none"
    assert update.json()["config"]["signal_source_toggles"] == {"deterministic": True, "llm": False}

    readback = client.get(
        f"/v1/runtime/event-controls/{EVENT_ID}",
        params={"session_date": "2026-05-25"},
    )

    assert readback.status_code == 200
    payload = readback.json()
    assert payload["config"]["updated_by"] == "codex"
    assert payload["config"]["stop_with_positions_policy"]["max_recovery_delay_seconds"] == 120
    assert payload["config"]["stop_with_positions_policy"]["raw_order_bypass_allowed"] is False
    assert payload["aggregation_control"]["enabled_signal_sources"] == ["deterministic"]
    assert payload["aggregation_control"]["event_cap_usd"] == 20


def test_runtime_control_router_rejects_unsafe_update_pytest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_control, "artifacts_root", lambda: tmp_path)

    client = TestClient(create_app())
    response = client.put(
        f"/v1/runtime/event-controls/{EVENT_ID}",
        params={"session_date": "2026-05-25"},
        json={
            "actor": "operator",
            "reason": "pytest unsafe cap",
            "parameters": {"event_cap_usd": 30},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "event_cap_above_current_live_learning_limit"


def test_runtime_control_router_rejects_stop_policy_without_recovery_action_pytest(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(runtime_control, "artifacts_root", lambda: tmp_path)

    client = TestClient(create_app())
    response = client.put(
        f"/v1/runtime/event-controls/{EVENT_ID}",
        params={"session_date": "2026-05-25"},
        json={
            "actor": "operator",
            "reason": "pytest unsafe stop policy",
            "stop_with_positions_policy": {
                "enabled": True,
                "backup_restart_required": False,
                "set_sell_targets_if_restart_blocked": False,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == (
        "stop_with_positions_policy_requires_recovery_action"
    )
