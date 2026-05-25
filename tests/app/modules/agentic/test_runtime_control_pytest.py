from __future__ import annotations

import json

import pytest

from app.modules.agentic.runtime_control import (
    EventControlParameters,
    EventControlUpdateRequest,
    EventControlValidationError,
    event_control_to_aggregation_control,
    load_event_control_config,
    update_event_control_config,
)


EVENT_ID = "nba-okc-sas-2026-05-24"


def test_event_control_update_persists_current_and_history_pytest(tmp_path) -> None:
    result = update_event_control_config(
        EVENT_ID,
        EventControlUpdateRequest(
            actor="codex",
            reason="unit test signal toggle",
            source="pytest",
            evidence_paths=["local/shared/artifacts/ops/sample.json"],
            signal_source_toggles={"deterministic": True, "llm": False},
            parameters=EventControlParameters(
                event_cap_usd=8.5,
                cooldown_seconds=45,
                max_signal_age_seconds=120,
                min_confidence=0.55,
                allow_inventory_adding=False,
            ),
        ),
        day="2026-05-25",
        root=tmp_path,
    )

    assert result["status"] == "stored"
    loaded = load_event_control_config(EVENT_ID, day="2026-05-25", root=tmp_path)
    assert loaded.updated_by == "codex"
    assert loaded.signal_source_toggles == {"deterministic": True, "llm": False}
    assert loaded.parameters.event_cap_usd == 8.5
    assert loaded.evidence_paths == ["local/shared/artifacts/ops/sample.json"]

    current_path = tmp_path / "event-controls" / "2026-05-25" / EVENT_ID / "current.json"
    history_path = tmp_path / "event-controls" / "2026-05-25" / "event_control_updates.jsonl"
    assert json.loads(current_path.read_text(encoding="utf-8"))["reason"] == "unit test signal toggle"
    assert json.loads(history_path.read_text(encoding="utf-8").splitlines()[0])["actor"] == "codex"


def test_event_control_to_aggregation_control_readback_pytest(tmp_path) -> None:
    update_event_control_config(
        EVENT_ID,
        EventControlUpdateRequest(
            actor="operator",
            reason="enable deterministic only",
            signal_source_toggles={"deterministic": True, "ml": True, "llm": False},
            parameters=EventControlParameters(cooldown_seconds=30, event_cap_usd=5, min_confidence=0.6),
        ),
        day="2026-05-25",
        root=tmp_path,
    )

    config = load_event_control_config(EVENT_ID, day="2026-05-25", root=tmp_path)
    control = event_control_to_aggregation_control(config)

    assert control.enabled_signal_sources == ["deterministic", "ml"]
    assert control.cooldown_seconds == 30
    assert control.event_cap_usd == 5
    assert control.min_confidence == 0.6


def test_event_control_rejects_unsafe_cap_pytest(tmp_path) -> None:
    with pytest.raises(EventControlValidationError, match="event_cap_above_current_live_learning_limit"):
        update_event_control_config(
            EVENT_ID,
            EventControlUpdateRequest(
                actor="llm",
                reason="unsafe cap test",
                parameters=EventControlParameters(event_cap_usd=25),
            ),
            day="2026-05-25",
            root=tmp_path,
        )
