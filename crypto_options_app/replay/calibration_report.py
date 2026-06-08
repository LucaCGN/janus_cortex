from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CALIBRATION_REPORT = Path("crypto_options_app/artifacts/reports/strategy_replay_calibration_latest.json")


def calibrated_replay_evidence_for_strategy(
    strategy_id: str,
    *,
    report_path: Path = DEFAULT_CALIBRATION_REPORT,
) -> dict[str, Any] | None:
    """Return latest path-calibrated replay evidence for a strategy.

    The report is generated from actual live decisions and captured quote paths.
    It is intentionally advisory unless data quality is high enough for scoring.
    """

    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if report.get("promotion_scoring_enabled") is not True:
        return None
    strategy_rows = report.get("strategy_summary")
    if not isinstance(strategy_rows, list):
        return None
    matching = [
        row for row in strategy_rows
        if isinstance(row, dict) and str(row.get("strategy_key") or "").split(":")[0] == strategy_id
    ]
    if not matching:
        return None
    decisions = sum(int(float(row.get("decisions") or 0)) for row in matching)
    simulated_pnl = sum(float(row.get("simulated_pnl") or 0.0) for row in matching)
    actual_pnl_values = [float(row["actual_resolved_pnl"]) for row in matching if row.get("actual_resolved_pnl") is not None]
    actual_pnl = sum(actual_pnl_values) if actual_pnl_values else None
    filled = sum(int(float(row.get("simulated_filled") or 0)) for row in matching)
    data_quality_score = sum(float(row.get("data_quality_score") or 0.0) for row in matching) / max(1, len(matching))
    report_quality = report.get("data_quality") if isinstance(report.get("data_quality"), dict) else {}
    scoring_ready = decisions > 0 and data_quality_score >= 0.55 and filled > 0
    blockers: list[str] = []
    if decisions <= 0:
        blockers.append("calibrated_replay_no_decisions")
    if filled <= 0:
        blockers.append("calibrated_replay_no_simulated_fills")
    if data_quality_score < 0.55:
        blockers.append("calibrated_replay_data_quality_below_floor")
    if actual_pnl is not None and actual_pnl < 0:
        blockers.append("account_incident_actual_negative_pnl")
    if scoring_ready and simulated_pnl <= 0:
        blockers.append("calibrated_replay_non_positive_pnl")
    if scoring_ready and actual_pnl is not None and actual_pnl < 0 and simulated_pnl > 0:
        blockers.append("calibrated_replay_live_loss_after_positive_simulation")
    return {
        "schema_version": "crypto_options_calibrated_replay_strategy_evidence_v1",
        "source_report": str(report_path),
        "latency_mode": report.get("latency_mode"),
        "latency_ms": report.get("latency_ms"),
        "ttl_seconds": report.get("ttl_seconds"),
        "decisions": decisions,
        "simulated_filled": filled,
        "simulated_pnl_usd": round(simulated_pnl, 8),
        "actual_resolved_pnl_usd": None if actual_pnl is None else round(actual_pnl, 8),
        "data_quality_score": round(data_quality_score, 6),
        "report_data_quality_score": report_quality.get("score"),
        "scoring_ready": scoring_ready,
        "blockers": blockers,
    }
