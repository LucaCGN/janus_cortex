from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.pipelines.daily.wnba.analysis.contracts import (
    WNBA_ANALYSIS_VERSION,
    default_shadow_lane_families,
)


def _status(payload: dict[str, Any] | None) -> str | None:
    return str((payload or {}).get("status") or "") or None


def evaluate_wnba_integration_readiness(
    *,
    data_audit: dict[str, Any],
    lane_signal_df: pd.DataFrame,
    backtest_bundle: dict[str, Any],
    ml_training_result: dict[str, Any],
    historical_backfill: dict[str, Any] | None = None,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Gate WNBA integration into passive/shadow surfaces without authorizing live orders."""
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    expected_families = set(default_shadow_lane_families())
    configured_families = set((backtest_bundle.get("families") or {}).keys())
    signal_families = (
        set(lane_signal_df["family"].dropna().astype(str).unique())
        if not lane_signal_df.empty and "family" in lane_signal_df.columns
        else set()
    )
    entry_candidate_count = (
        int((lane_signal_df["signal_status"] == "entry_candidate").sum())
        if not lane_signal_df.empty and "signal_status" in lane_signal_df.columns
        else 0
    )
    blocked_signal_count = (
        int((lane_signal_df["signal_status"] == "blocked").sum())
        if not lane_signal_df.empty and "signal_status" in lane_signal_df.columns
        else 0
    )

    structural_blockers: list[str] = []
    missing_configured = sorted(expected_families - configured_families)
    if missing_configured:
        structural_blockers.append("missing_wnba_backtest_lane_configs:" + ",".join(missing_configured))
    missing_signal_families = sorted(expected_families - signal_families)
    if missing_signal_families:
        structural_blockers.append("missing_wnba_lane_signal_rows:" + ",".join(missing_signal_families))
    counts = data_audit.get("counts") or {}
    if int(counts.get("state_panel_rows") or 0) <= 0:
        structural_blockers.append("missing_wnba_state_panel_rows")
    if int(counts.get("ml_feature_rows") or 0) <= 0:
        structural_blockers.append("missing_wnba_ml_feature_rows")

    calibration_blockers: list[str] = []
    calibration_blockers.extend(str(value) for value in backtest_bundle.get("blockers") or [])
    calibration_blockers.extend(str(value) for value in ml_training_result.get("blockers") or [])
    historical_status = _status(historical_backfill)
    if historical_status == "blocked":
        calibration_blockers.append("historical_wnba_backfill_blocked")
    calibration_blockers = sorted(set(calibration_blockers))

    passive_shadow_ready = not structural_blockers
    calibrated_ready = passive_shadow_ready and not calibration_blockers
    integration_status = (
        "calibrated_integration_ready"
        if calibrated_ready
        else "passive_shadow_integration_ready_with_calibration_blockers"
        if passive_shadow_ready
        else "blocked"
    )

    return {
        "status": integration_status,
        "analysis_version": analysis_version,
        "evaluated_at": evaluated_at,
        "orders_allowed": False,
        "passive_shadow_ready": passive_shadow_ready,
        "calibrated_backtesting_ready": calibrated_ready,
        "expected_lane_families": sorted(expected_families),
        "configured_lane_families": sorted(configured_families),
        "signal_lane_families": sorted(signal_families),
        "entry_candidate_count": entry_candidate_count,
        "blocked_signal_count": blocked_signal_count,
        "data_status": data_audit.get("status"),
        "backtest_status": backtest_bundle.get("status"),
        "ml_status": ml_training_result.get("status"),
        "historical_backfill_status": historical_status,
        "structural_blockers": structural_blockers,
        "calibration_blockers": calibration_blockers,
        "verdict": _verdict(
            passive_shadow_ready=passive_shadow_ready,
            calibrated_ready=calibrated_ready,
            calibration_blockers=calibration_blockers,
        ),
    }


def _verdict(
    *,
    passive_shadow_ready: bool,
    calibrated_ready: bool,
    calibration_blockers: list[str],
) -> str:
    if calibrated_ready:
        return "WNBA structural and calibrated data products are ready for integration."
    if passive_shadow_ready:
        return (
            "WNBA can be integrated into passive/shadow replay, deterministic, and ML surfaces now, "
            "but calibrated lane backtests and ML training remain blocked by: "
            + ", ".join(calibration_blockers)
        )
    return "WNBA integration is blocked because one or more structural data, lane, or ML products are missing."


__all__ = ["evaluate_wnba_integration_readiness"]
