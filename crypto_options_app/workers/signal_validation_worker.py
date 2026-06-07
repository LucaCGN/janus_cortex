from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from crypto_options_app.signals.validation.models import model_to_dict
from crypto_options_app.signals.validation.runner import run_next_signal_validation


@dataclass(frozen=True)
class SignalValidationWorkerConfig:
    db_path: Path | None = None
    owner_id: str = "crypto-options-signal-validator-worker"
    max_frames: int = 100
    ttl_minutes: int = 10
    batch_size: int = 1


def run_signal_validation_worker_once(config: SignalValidationWorkerConfig) -> dict:
    result = None
    for attempt in range(1, 6):
        try:
            result = run_next_signal_validation(
                db_path=config.db_path,
                owner_id=config.owner_id,
                max_frames=config.max_frames,
                ttl_minutes=config.ttl_minutes,
            )
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= 5:
                raise
            time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
    assert result is not None
    return {
        "schema_version": "crypto_options_signal_validation_worker_result_v1",
        "status": result.status,
        "validation_run_key": result.validation_run_key,
        "queue_item": None if result.queue_item is None else model_to_dict(result.queue_item),
        "phase_result": None if result.phase_result is None else model_to_dict(result.phase_result),
        "observation_count": len(result.observations),
        "blockers": list(result.blockers),
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def run_signal_validation_worker_batch(config: SignalValidationWorkerConfig) -> dict:
    """Run a bounded read-only batch of signal queue items.

    Each item is still claimed atomically before execution. The batch wrapper
    only prevents the 5-minute automation from advancing a 23-signal first
    batch one row at a time.
    """

    batch_size = max(1, int(config.batch_size))
    results: list[dict] = []
    for index in range(batch_size):
        item_config = SignalValidationWorkerConfig(
            db_path=config.db_path,
            owner_id=f"{config.owner_id}-{index + 1:02d}",
            max_frames=config.max_frames,
            ttl_minutes=config.ttl_minutes,
            batch_size=1,
        )
        payload = run_signal_validation_worker_once(item_config)
        results.append(payload)
        if "no_claimable_signal_queue_item" in payload.get("blockers", []):
            break
    statuses: dict[str, int] = {}
    phases: dict[str, int] = {}
    for payload in results:
        status = str(payload.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        phase = ((payload.get("queue_item") or {}).get("phase")) or "none"
        phases[str(phase)] = phases.get(str(phase), 0) + 1
    return {
        "schema_version": "crypto_options_signal_validation_worker_batch_result_v1",
        "status": "complete" if results else "blocked",
        "batch_size_requested": batch_size,
        "run_count": len(results),
        "status_counts": statuses,
        "phase_counts": phases,
        "results": results,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
