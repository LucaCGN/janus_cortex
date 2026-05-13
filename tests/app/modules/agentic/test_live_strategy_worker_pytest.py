from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.modules.agentic.live_strategy_worker import (
    LiveStrategyWorker,
    LiveStrategyWorkerConfig,
    build_live_strategy_worker_readiness,
)


def test_live_strategy_worker_discovers_valid_current_plans_and_runs_tick_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    plan_root = local_root / "shared" / "artifacts" / "strategy-plans" / "2026-05-13"
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write_json(plan_root / "event-valid" / "current.json", {"event_id": "event-valid", "valid_until_utc": future})
    _write_json(plan_root / "event-expired" / "current.json", {"event_id": "event-expired", "valid_until_utc": past})
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "events": []}), stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(
            session_date="2026-05-13",
            account_id="account-1",
            execute=True,
            live_money=True,
            timeout_seconds=10,
        )
    )

    result = worker.run_once()

    assert result["ok"] is True
    assert result["event_ids"] == ["event-valid"]
    command = commands[0]
    assert "--event-id" in command
    assert command[command.index("--event-id") + 1] == "event-valid"
    assert "event-expired" not in command
    assert "--execute" in command
    assert "--live-money" in command
    heartbeat = (
        local_root
        / "shared"
        / "artifacts"
        / "live-strategy-worker"
        / "2026-05-13"
        / "heartbeat.json"
    )
    assert heartbeat.exists()
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["ok"] is True


def test_live_strategy_worker_blocks_without_account_id_before_subprocess_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    plan_root = local_root / "shared" / "artifacts" / "strategy-plans" / "2026-05-13"
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    _write_json(plan_root / "event-valid" / "current.json", {"event_id": "event-valid", "valid_until_utc": future})
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(LiveStrategyWorkerConfig(session_date="2026-05-13", account_id=None))

    result = worker.run_once()

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["reason"] == "account_id_required"
    assert called is False


def test_live_strategy_worker_readiness_requires_running_worker_for_discovered_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    plan_root = local_root / "shared" / "artifacts" / "strategy-plans" / "2026-05-13"
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    _write_json(plan_root / "event-valid" / "current.json", {"event_id": "event-valid", "valid_until_utc": future})
    monkeypatch.setattr(
        "app.modules.agentic.live_strategy_worker._WORKER",
        SimpleNamespace(
            status=lambda: {
                "status": "stopped",
                "worker_thread_alive": False,
                "config": {"interval_seconds": 30},
            }
        ),
    )

    result = build_live_strategy_worker_readiness(
        session_date="2026-05-13",
        event_ids=[],
        strategy_plan_gate={"status": "not_required", "ready_for_strategy_evaluation": False, "current_plans": []},
    )

    assert result["status"] == "blocked"
    assert result["blocker_reason"] == "live_strategy_worker_not_running"
    assert result["worker_required"] is True
    assert result["expected_event_ids"] == ["event-valid"]
    assert result["ready_for_live_execution"] is False


def test_live_strategy_worker_readiness_accepts_fresh_matching_heartbeat_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    _write_json(
        local_root / "shared" / "artifacts" / "live-strategy-worker" / "2026-05-13" / "heartbeat.json",
        {
            "schema_version": "live_strategy_worker_heartbeat_v1",
            "status": "completed",
            "ok": True,
            "event_ids": ["event-valid"],
            "last_tick_finished_at_utc": now.isoformat(),
        },
    )
    monkeypatch.setattr(
        "app.modules.agentic.live_strategy_worker._WORKER",
        SimpleNamespace(
            status=lambda: {
                "status": "running",
                "worker_thread_alive": True,
                "tick_count": 3,
                "consecutive_failures": 0,
                "last_error": None,
                "config": {"interval_seconds": 30},
            }
        ),
    )

    result = build_live_strategy_worker_readiness(
        session_date="2026-05-13",
        event_ids=["event-valid"],
        strategy_plan_gate={"status": "ready", "ready_for_strategy_evaluation": True, "current_plans": []},
        now_utc=now + timedelta(seconds=30),
    )

    assert result["status"] == "ready"
    assert result["worker_required"] is True
    assert result["heartbeat_present"] is True
    assert result["heartbeat_fresh"] is True
    assert result["missing_heartbeat_event_ids"] == []
    assert result["ready_for_live_execution"] is True


def test_live_strategy_worker_readiness_blocks_stale_heartbeat_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    _write_json(
        local_root / "shared" / "artifacts" / "live-strategy-worker" / "2026-05-13" / "heartbeat.json",
        {
            "schema_version": "live_strategy_worker_heartbeat_v1",
            "status": "completed",
            "ok": True,
            "event_ids": ["event-valid"],
            "last_tick_finished_at_utc": (now - timedelta(minutes=10)).isoformat(),
        },
    )
    monkeypatch.setattr(
        "app.modules.agentic.live_strategy_worker._WORKER",
        SimpleNamespace(
            status=lambda: {
                "status": "running",
                "worker_thread_alive": True,
                "config": {"interval_seconds": 30},
            }
        ),
    )

    result = build_live_strategy_worker_readiness(
        session_date="2026-05-13",
        event_ids=["event-valid"],
        strategy_plan_gate={"status": "ready", "ready_for_strategy_evaluation": True, "current_plans": []},
        now_utc=now,
    )

    assert result["status"] == "blocked"
    assert result["blocker_reason"] == "live_strategy_worker_heartbeat_stale"
    assert result["heartbeat_fresh"] is False
    assert result["ready_for_live_execution"] is False


def _write_json(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
