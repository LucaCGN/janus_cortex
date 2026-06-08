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
            min_size=1.0,
            order_sizing_mode="fixed_min_shares",
            max_buy_notional_usd=2.0,
            event_cap_usd=16.0,
            side_budget_mode="balanced_50_50",
            max_same_side_exposure_pct=0.5,
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
    assert command[command.index("--min-size") + 1] == "1.0"
    assert command[command.index("--order-sizing-mode") + 1] == "fixed_min_shares"
    assert command[command.index("--max-buy-notional-usd") + 1] == "2.0"
    assert command[command.index("--event-cap-usd") + 1] == "16.0"
    assert command[command.index("--side-budget-mode") + 1] == "balanced_50_50"
    assert command[command.index("--max-same-side-exposure-pct") + 1] == "0.5"
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


def test_live_strategy_worker_overrides_can_clear_legacy_notional_caps_pytest() -> None:
    config = LiveStrategyWorkerConfig(
        max_buy_notional_usd=10.0,
        max_order_buy_notional_usd=10.0,
        event_cap_usd=20.0,
        side_budget_mode="balanced_50_50",
        max_same_side_exposure_pct=0.5,
    )

    updated = config.with_overrides(
        {
            "max_buy_notional_usd": None,
            "max_order_buy_notional_usd": None,
            "event_cap_usd": 20.0,
            "side_budget_mode": "balanced_50_50",
            "max_same_side_exposure_pct": 0.5,
        }
    )

    assert updated.max_buy_notional_usd is None
    assert updated.max_order_buy_notional_usd is None
    assert updated.event_cap_usd == 20.0
    assert updated.side_budget_mode == "balanced_50_50"
    assert updated.max_same_side_exposure_pct == 0.5


def test_live_strategy_worker_skips_overlapping_tick_before_subprocess_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(
            session_date="2026-05-13",
            event_ids=("event-valid",),
            account_id="account-1",
        )
    )
    worker._tick_lock.acquire()  # noqa: SLF001
    try:
        result = worker.run_once()
    finally:
        worker._tick_lock.release()  # noqa: SLF001

    assert result["ok"] is True
    assert result["status"] == "skipped"
    assert result["reason"] == "live_strategy_worker_tick_in_progress"
    assert called is False


def test_live_strategy_worker_env_autostart_blocks_stale_session_date_pytest(monkeypatch) -> None:
    monkeypatch.setenv("JANUS_LIVE_STRATEGY_WORKER_ENABLED", "true")
    monkeypatch.setenv("JANUS_LIVE_STRATEGY_WORKER_SESSION_DATE", "2026-05-24")
    monkeypatch.setenv("JANUS_LIVE_STRATEGY_WORKER_EVENT_IDS", "event-old")
    monkeypatch.setattr("app.modules.agentic.live_strategy_worker._brt_session_date", lambda: "2026-05-26")
    worker = LiveStrategyWorker()

    result = worker.start_if_env_enabled()

    assert result["status"] == "stopped"
    assert result["worker_thread_alive"] is False
    assert result["start_skipped_reason"] == "stale_env_session_date"
    assert result["configured_session_date"] == "2026-05-24"
    assert result["current_session_date"] == "2026-05-26"


def test_live_strategy_worker_start_requires_explicit_payload_for_stopped_config_pytest(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.agentic.live_strategy_worker._brt_session_date", lambda: "2026-06-01")
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(
            session_date="2026-06-01",
            event_ids=("nba-cle-nyk-2026-06-01",),
            account_id="account-1",
            execute=True,
            live_money=True,
            enabled=False,
        )
    )

    result = worker.start()

    assert result["status"] == "stopped"
    assert result["start_status"] == "blocked"
    assert result["start_blocker_reason"] == "explicit_start_payload_required"
    assert result["config_trust"]["config_trust_status"] == "fail_closed_stale_or_implicit_config"
    assert result["config_trust"]["schema_contract"]["schema_version"] == (
        "live_strategy_worker_config_trust_contract_v1"
    )
    assert result["config_trust"]["schema_contract"]["execution_authority"] is False
    assert "explicit_event_scope_payload" in result["config_trust"]["schema_contract"]["required_sections"]
    assert "effective_event_ids" in result["config_trust"]["schema_contract"]["required_sections"]
    assert result["config_trust"]["restart_allowed"] is False
    assert called is False


def test_live_strategy_worker_start_requires_explicit_event_scope_for_restart_pytest(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.agentic.live_strategy_worker._brt_session_date", lambda: "2026-06-01")
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(
            session_date="2026-06-01",
            event_ids=("nba-cle-nyk-2026-06-01",),
            account_id="account-1",
            enabled=False,
        )
    )

    result = worker.start({"execute": True, "live_money": True})

    assert result["status"] == "stopped"
    assert result["start_status"] == "blocked"
    assert result["start_blocker_reason"] == "explicit_event_scope_required_for_restart"
    assert result["config_trust"]["explicit_start_payload"] is True
    assert result["config_trust"]["explicit_event_scope_payload"] is False
    assert result["config_trust"]["restart_allowed"] is False
    assert called is False


def test_live_strategy_worker_start_blocks_stale_event_scope_pytest(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.agentic.live_strategy_worker._brt_session_date", lambda: "2026-06-01")
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(LiveStrategyWorkerConfig())

    result = worker.start(
        {
            "session_date": "2026-06-01",
            "event_ids": ["wnba-lva-gsv-2026-05-31"],
            "account_id": "account-1",
            "execute": True,
            "live_money": True,
        }
    )

    assert result["status"] == "stopped"
    assert result["start_status"] == "blocked"
    assert result["start_blocker_reason"] == "stale_config_event_scope"
    assert result["config_trust"]["effective_event_ids"] == ["wnba-lva-gsv-2026-05-31"]
    assert result["config_trust"]["event_scope_current"] is False
    assert called is False


def test_live_strategy_worker_stale_session_never_reports_current_event_scope_pytest(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.agentic.live_strategy_worker._brt_session_date", lambda: "2026-06-01")
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(
            session_date="2026-05-26",
            event_ids=("nba-sas-okc-2026-05-26",),
            account_id="account-1",
            execute=True,
            live_money=True,
            enabled=True,
        )
    )

    result = worker.status()

    assert result["status"] == "stopped"
    assert result["config_trust"]["stale_config_reason"] == "stale_config_session_date"
    assert result["config_trust"]["event_scope_current"] is False
    assert result["config_trust"]["restart_allowed"] is False


def test_live_strategy_worker_manual_tick_overrides_do_not_replace_stopped_config_pytest(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.agentic.live_strategy_worker._brt_session_date", lambda: "2026-06-01")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "events": []}), stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(
            session_date="2026-05-26",
            event_ids=("nba-sas-okc-2026-05-26",),
            account_id="account-1",
            execute=True,
            live_money=True,
            enabled=True,
        )
    )

    result = worker.run_once(
        {
            "session_date": "2026-06-01",
            "event_ids": ["issue85-current-scope-2026-06-01"],
            "execute": False,
            "live_money": False,
            "enable_llm_dispatch": False,
        }
    )
    status = worker.status()

    assert result["ok"] is True
    assert "--execute" not in commands[0]
    assert "--live-money" not in commands[0]
    assert status["config"]["session_date"] == "2026-05-26"
    assert status["config"]["event_ids"] == ["nba-sas-okc-2026-05-26"]
    assert status["config_trust"]["stale_config_reason"] == "stale_config_session_date"
    assert status["config_trust"]["restart_allowed"] is False


def test_live_strategy_worker_ignores_cross_date_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    plan_root = local_root / "shared" / "artifacts" / "strategy-plans" / "2026-05-26"
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    _write_json(
        plan_root / "nba-okc-sas-2026-05-24" / "current.json",
        {"event_id": "nba-okc-sas-2026-05-24", "valid_until_utc": future},
    )
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.modules.agentic.live_strategy_worker.subprocess.run", fake_run)
    worker = LiveStrategyWorker(
        LiveStrategyWorkerConfig(session_date="2026-05-26", account_id="account-1", timeout_seconds=10)
    )

    result = worker.run_once()

    assert result["ok"] is True
    assert result["status"] == "no_op"
    assert result["reason"] == "no_current_valid_strategy_plans"
    assert result["event_ids"] == []
    assert called is False


def test_live_strategy_worker_stop_reports_pending_when_tick_is_still_running_pytest() -> None:
    class ThreadStillRunning:
        def __init__(self) -> None:
            self.join_called = False

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_called = True

    worker = LiveStrategyWorker(LiveStrategyWorkerConfig())
    fake_thread = ThreadStillRunning()
    worker._thread = fake_thread  # noqa: SLF001

    result = worker.stop()

    assert fake_thread.join_called is True
    assert result["status"] == "running"
    assert result["worker_thread_alive"] is True
    assert result["stop_status"] == "stop_pending"


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
                "config_trust": {
                    "schema_version": "live_strategy_worker_config_trust_v1",
                    "config_trust_status": "fail_closed_stale_or_implicit_config",
                    "restart_allowed": False,
                    "stale_config_reason": "explicit_start_payload_required",
                },
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
    assert result["config_trust"]["schema_version"] == "live_strategy_worker_config_trust_v1"
    assert result["config_trust"]["restart_allowed"] is False
    assert result["worker_status"]["config_trust"]["stale_config_reason"] == "explicit_start_payload_required"


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
