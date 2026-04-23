from __future__ import annotations

import threading
from typing import Any

from app.modules.nba.execution.contracts import LiveRunConfig, LiveRunCreateRequest
from app.modules.nba.execution.runner import LiveRunWorker


class LiveRunService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, LiveRunWorker] = {}

    def start_or_resume_run(self, payload: LiveRunCreateRequest | LiveRunConfig) -> dict[str, Any]:
        config = payload.to_config() if isinstance(payload, LiveRunCreateRequest) else payload
        with self._lock:
            worker = self._runs.get(config.run_id)
            if worker is None:
                worker = LiveRunWorker(config)
                self._runs[config.run_id] = worker
            worker.start()
            return worker.summary_snapshot()

    def require_run(self, run_id: str) -> LiveRunWorker:
        with self._lock:
            worker = self._runs.get(run_id)
        if worker is None:
            raise KeyError(f"Live run not found: {run_id}")
        return worker

    def get_run_summary(self, run_id: str) -> dict[str, Any]:
        return self.require_run(run_id).summary_snapshot()

    def get_run_games(self, run_id: str) -> dict[str, Any]:
        return {"games": self.require_run(run_id).game_snapshot()}

    def get_run_orders(self, run_id: str) -> dict[str, Any]:
        worker = self.require_run(run_id)
        return {"orders": worker.order_snapshot(), "positions": worker.position_snapshot()}

    def get_run_events(self, run_id: str) -> dict[str, Any]:
        return {"events": self.require_run(run_id).event_snapshot()}

    def get_run_summary_cards(self, run_id: str) -> dict[str, Any]:
        return {"fills": self.require_run(run_id).fills_summary()}

    def pause_entries(self, run_id: str) -> dict[str, Any]:
        worker = self.require_run(run_id)
        worker.pause_entries()
        return worker.summary_snapshot()

    def resume_entries(self, run_id: str) -> dict[str, Any]:
        worker = self.require_run(run_id)
        worker.resume_entries()
        return worker.summary_snapshot()

    def stop_run(self, run_id: str) -> dict[str, Any]:
        worker = self.require_run(run_id)
        worker.request_stop()
        return worker.summary_snapshot()


_SERVICE: LiveRunService | None = None


def get_live_run_service() -> LiveRunService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = LiveRunService()
    return _SERVICE


__all__ = ["LiveRunService", "get_live_run_service"]
