from __future__ import annotations

import json
from pathlib import Path

import app.modules.nba.execution.service as service_mod
from app.modules.nba.execution.runner import LiveRunWorker


def test_loading_disk_run_is_read_only_and_uses_saved_run_root_pytest(tmp_path, monkeypatch) -> None:
    run_id = "live-old-completed"
    saved_run_root = tmp_path / "2026-04-28" / run_id
    saved_run_root.mkdir(parents=True)
    (saved_run_root / "run_config.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    start_calls: list[str] = []

    class _FakeWorker:
        def __init__(self, config, *, run_root: Path | None = None) -> None:
            self.config = config
            self.run_root = run_root
            self.status = "completed"

        def start(self) -> None:
            start_calls.append(self.config.run_id)

        def summary_snapshot(self) -> dict[str, object]:
            return {
                "run_id": self.config.run_id,
                "status": self.status,
                "run_root": str(self.run_root),
            }

    monkeypatch.setattr(service_mod, "resolve_live_tracks_root", lambda: tmp_path)
    monkeypatch.setattr(service_mod, "LiveRunWorker", _FakeWorker)

    summary = service_mod.LiveRunService().get_run_summary(run_id)

    assert summary["status"] == "completed"
    assert summary["run_root"] == str(saved_run_root)
    assert start_calls == []


def test_stop_loaded_disk_run_does_not_start_it_pytest(tmp_path, monkeypatch) -> None:
    run_id = "live-old-stop"
    saved_run_root = tmp_path / "2026-04-28" / run_id
    saved_run_root.mkdir(parents=True)
    (saved_run_root / "run_config.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    start_calls: list[str] = []
    stop_calls: list[str] = []

    class _FakeWorker:
        def __init__(self, config, *, run_root: Path | None = None) -> None:
            self.config = config
            self.run_root = run_root
            self.status = "completed"

        def start(self) -> None:
            start_calls.append(self.config.run_id)

        def request_stop(self) -> None:
            stop_calls.append(self.config.run_id)
            self.status = "stopped"

        def summary_snapshot(self) -> dict[str, object]:
            return {
                "run_id": self.config.run_id,
                "status": self.status,
                "run_root": str(self.run_root),
            }

    monkeypatch.setattr(service_mod, "resolve_live_tracks_root", lambda: tmp_path)
    monkeypatch.setattr(service_mod, "LiveRunWorker", _FakeWorker)

    summary = service_mod.LiveRunService().stop_run(run_id)

    assert summary["status"] == "stopped"
    assert summary["run_root"] == str(saved_run_root)
    assert start_calls == []
    assert stop_calls == [run_id]


def test_loading_disk_running_status_is_marked_inactive_without_autostart_pytest(tmp_path, monkeypatch) -> None:
    run_id = "live-old-running"
    saved_run_root = tmp_path / "2026-04-28" / run_id
    saved_run_root.mkdir(parents=True)
    (saved_run_root / "run_config.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    start_calls: list[str] = []
    restored_calls: list[str] = []

    class _FakeWorker:
        def __init__(self, config, *, run_root: Path | None = None) -> None:
            self.config = config
            self.run_root = run_root
            self.status = "running"

        def start(self) -> None:
            start_calls.append(self.config.run_id)

        def mark_restored_inactive(self) -> None:
            restored_calls.append(self.config.run_id)
            if self.status == "running":
                self.status = "stopped"

        def summary_snapshot(self) -> dict[str, object]:
            return {
                "run_id": self.config.run_id,
                "status": self.status,
                "run_root": str(self.run_root),
            }

    monkeypatch.setattr(service_mod, "resolve_live_tracks_root", lambda: tmp_path)
    monkeypatch.setattr(service_mod, "LiveRunWorker", _FakeWorker)

    summary = service_mod.LiveRunService().get_run_summary(run_id)

    assert summary["status"] == "stopped"
    assert restored_calls == [run_id]
    assert start_calls == []


def test_recovery_snapshot_restores_terminal_status_and_error_pytest(tmp_path) -> None:
    (tmp_path / "recovery_snapshot.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "entries_enabled": False,
                "last_error": "final-state",
                "last_traceback": "trace",
                "active_orders": {"abc": {"order_id": "abc"}},
                "active_positions": {},
                "fill_metrics": [],
                "cycle_count": 7,
            }
        ),
        encoding="utf-8",
    )
    worker = LiveRunWorker.__new__(LiveRunWorker)
    worker.run_root = tmp_path
    worker.status = "created"
    worker.entries_enabled = True
    worker.active_orders = {}
    worker.active_positions = {}
    worker.fill_metrics = []
    worker.last_error = None
    worker.last_traceback = None
    worker.cycle_count = 0
    worker.last_cycle_started_at = None
    worker.last_cycle_completed_at = None
    worker.last_cycle_duration_seconds = None
    worker.last_successful_cycle_at = None
    worker.account = None

    LiveRunWorker._load_recovery_snapshot(worker)

    assert worker.status == "completed"
    assert worker.entries_enabled is False
    assert worker.last_error == "final-state"
    assert worker.last_traceback == "trace"
    assert worker.active_orders == {"abc": {"order_id": "abc"}}
    assert worker.cycle_count == 7
