from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.signals.validation.result_store import queue_status, sync_signal_catalog, validation_status


@dataclass(frozen=True)
class SignalDesignReviewerConfig:
    db_path: Path | None = None


def run_signal_design_reviewer_once(config: SignalDesignReviewerConfig) -> dict:
    path = initialize_schema(config.db_path)
    with connect(path) as conn:
        sync_signal_catalog(conn, enqueue=True)
        status = validation_status(conn)
        queue = queue_status(conn)
    missing_or_failed = [
        row
        for row in status["signals"]
        if row.get("queue_status") in {None, "QUEUED", "FAILED", "BLOCKED"}
        or row.get("promotion_state") in {"NEEDS_V2_REVIEW", "INCOMPLETE"}
    ]
    strict_replay_needed = [
        row
        for row in status["signals"]
        if row.get("promotion_state") == "STRUCTURAL_PASS"
    ]
    return {
        "schema_version": "crypto_options_signal_design_reviewer_result_v1",
        "status": "needs_work" if missing_or_failed or strict_replay_needed else "complete",
        "signal_count": status["signal_count"],
        "queue_count": queue["queue_count"],
        "needs_work_count": len(missing_or_failed),
        "strict_replay_needed_count": len(strict_replay_needed),
        "promotion_state_counts": status.get("by_promotion_state", {}),
        "next_design_targets": missing_or_failed[:10],
        "next_strict_replay_targets": strict_replay_needed[:10],
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
