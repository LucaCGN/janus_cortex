from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


WNBA_DEVELOPMENT_LOOP_VERSION = "wnba_standard_dev_loop_v0_1"

_CANONICAL_BLOCKERS = {
    "insufficient_distinct_games_for_wnba_ml": "insufficient_distinct_wnba_ml_games",
    "missing_labeled_clob_price_windows": "missing_labeled_wnba_clob_price_windows",
}


def _unique(values: list[str]) -> list[str]:
    return sorted({_CANONICAL_BLOCKERS.get(str(value), str(value)) for value in values if str(value or "").strip()})


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _analysis_blockers(analysis_payload: dict[str, Any]) -> list[str]:
    integration = analysis_payload.get("integration_readiness") or {}
    data_audit = analysis_payload.get("data_audit") or {}
    blockers: list[str] = []
    blockers.extend(str(value) for value in integration.get("structural_blockers") or [])
    if not bool(integration.get("passive_shadow_ready")):
        blockers.extend(str(value) for value in data_audit.get("blockers") or [])
    return _unique(blockers)


def _calibration_blockers(analysis_payload: dict[str, Any], price_history_payload: dict[str, Any] | None) -> list[str]:
    integration = analysis_payload.get("integration_readiness") or {}
    ml_training = analysis_payload.get("ml_training") or {}
    historical = analysis_payload.get("historical_backfill") or {}
    has_price_history_probe = _price_history_probe_ready(price_history_payload)
    blockers: list[str] = []
    blockers.extend(str(value) for value in integration.get("calibration_blockers") or [])
    blockers.extend(str(value) for value in ml_training.get("blockers") or [])
    if historical.get("status") == "blocked":
        blockers.append("historical_wnba_backfill_blocked")
    if price_history_payload:
        ml_readiness = price_history_payload.get("ml_readiness") or {}
        blockers.extend(str(value) for value in ml_readiness.get("blockers") or [])
    blockers.append("missing_passive_wnba_clob_tick_trade_capture_for_full_microstructure_replay")
    if has_price_history_probe:
        blockers = [
            blocker
            for blocker in blockers
            if blocker not in {"missing_wnba_clob_price_path", "missing_labeled_wnba_clob_price_windows"}
        ]
    return _unique(blockers)


def _price_history_probe_ready(price_history_payload: dict[str, Any] | None) -> bool:
    if not price_history_payload:
        return False
    complete_statuses = {"price_history_backtest_complete", "batch_price_history_backtest_complete"}
    return (
        price_history_payload.get("status") in complete_statuses
        and _as_int(price_history_payload.get("price_history_rows")) > 0
        and _as_int(price_history_payload.get("state_panel_rows")) > 0
    )


def _build_next_tasks(*, has_price_history_probe: bool, migrations_applied: bool | None) -> list[dict[str, Any]]:
    migration_status = "pending_validation" if migrations_applied is None else "complete" if migrations_applied else "blocked"
    return [
        {
            "id": "apply_wnba_migrations_safe_db",
            "priority": "P1",
            "status": migration_status,
            "description": "Apply WNBA migrations 0021-0024 in a disposable or otherwise safe Postgres target before any shared DB sync.",
        },
        {
            "id": "backfill_closed_wnba_polymarket_price_history",
            "priority": "P1",
            "status": "ready" if has_price_history_probe else "needs_probe",
            "description": "Fetch all matched finished WNBA moneyline token price histories and persist them into wnba.wnba_polymarket_price_history.",
        },
        {
            "id": "run_wnba_price_history_shadow_backtests",
            "priority": "P1",
            "status": "ready" if has_price_history_probe else "blocked_by_price_history",
            "description": "Run all WNBA deterministic lanes over linked PBP plus Polymarket price paths and publish season-level results.",
        },
        {
            "id": "start_passive_wnba_clob_watch_capture",
            "priority": "P1",
            "status": "pending_runtime_review",
            "description": "Wire WNBA moneyline watch targets into generic passive CLOB tick/trade capture with orders_allowed=false.",
        },
        {
            "id": "train_wnba_short_horizon_ml",
            "priority": "P2",
            "status": "blocked_by_labeled_sample_size",
            "description": "Train WNBA ML only after enough linked games and before/after price-window labels exist.",
        },
    ]


def evaluate_wnba_standard_development_loop(
    *,
    analysis_payload: dict[str, Any],
    price_history_payload: dict[str, Any] | None = None,
    migrations_applied: bool | None = None,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Decide how WNBA should enter the normal JANUS development queue.

    This gate is intentionally about development-flow routing, not live trading authority.
    A ready status here only means the standard Development Agent can safely pick up
    WNBA data/replay/ML tasks as shadow-only work.
    """
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    integration = analysis_payload.get("integration_readiness") or {}
    data_audit = analysis_payload.get("data_audit") or {}
    historical = analysis_payload.get("historical_backfill") or {}
    has_price_history_probe = _price_history_probe_ready(price_history_payload)

    passive_shadow_ready = bool(integration.get("passive_shadow_ready"))
    orders_allowed = bool(integration.get("orders_allowed"))
    structural_blockers = _analysis_blockers(analysis_payload)
    minimum_before_standard_loop = list(structural_blockers)
    if orders_allowed:
        minimum_before_standard_loop.append("wnba_orders_allowed_must_remain_false")

    standard_loop_allowed = passive_shadow_ready and not orders_allowed and not structural_blockers
    if not standard_loop_allowed:
        status = "blocked"
    elif has_price_history_probe:
        status = "ready_for_standard_loop_price_history_shadow"
    else:
        status = "ready_for_standard_loop_passive_shadow"

    calibrated_or_live_blockers = _calibration_blockers(analysis_payload, price_history_payload)
    if migrations_applied is False:
        calibrated_or_live_blockers.append("wnba_migrations_not_applied_to_safe_db")
    calibrated_or_live_blockers = _unique(calibrated_or_live_blockers)

    return {
        "status": status,
        "version": WNBA_DEVELOPMENT_LOOP_VERSION,
        "evaluated_at": evaluated_at,
        "standard_loop_allowed": standard_loop_allowed,
        "routing_priority": "P1_after_active_NBA_live_safety_P0",
        "orders_allowed": False,
        "live_trading_allowed": False,
        "passive_shadow_ready": passive_shadow_ready,
        "price_history_probe_ready": has_price_history_probe,
        "data_status": data_audit.get("status"),
        "integration_status": integration.get("status"),
        "historical_backfill_status": historical.get("status"),
        "minimum_before_standard_loop": _unique(minimum_before_standard_loop),
        "calibrated_or_live_blockers": calibrated_or_live_blockers,
        "allowed_scopes": [
            "wnba_schema_and_migrations",
            "wnba_cdn_ingestion",
            "wnba_polymarket_market_matching",
            "wnba_finished_event_price_history_backtests",
            "wnba_passive_clob_watch_capture",
            "wnba_state_panels",
            "wnba_deterministic_shadow_lanes",
            "wnba_ml_dataset_and_shadow_models",
            "wnba_reports_and_handoffs",
        ],
        "disallowed_scopes": [
            "placing_orders",
            "changing_nba_live_trading_logic",
            "changing_agentic_execution_logic",
            "changing_active_nba_strategy_plan_json_behavior",
            "altering_automations",
            "promoting_wnba_lanes_to_live_without_calibration_gate",
        ],
        "next_tasks": _build_next_tasks(
            has_price_history_probe=has_price_history_probe,
            migrations_applied=migrations_applied,
        ),
        "verdict": _verdict(
            status=status,
            calibrated_or_live_blockers=calibrated_or_live_blockers,
        ),
    }


def _verdict(*, status: str, calibrated_or_live_blockers: list[str]) -> str:
    if status == "blocked":
        return "WNBA should not enter the standard Development Agent loop until structural blockers are cleared."
    if status == "ready_for_standard_loop_price_history_shadow":
        return (
            "WNBA can enter the standard Development Agent loop now as a P1 shadow/data/replay workstream. "
            "Finished Polymarket price histories support first-level backtests, while calibrated/live use remains blocked by: "
            + ", ".join(calibrated_or_live_blockers)
        )
    return (
        "WNBA can enter the standard Development Agent loop now as a P1 passive/shadow workstream. "
        "Closed-event price-history backfill should be the first standard-loop task before calibration claims."
    )


__all__ = [
    "WNBA_DEVELOPMENT_LOOP_VERSION",
    "evaluate_wnba_standard_development_loop",
]
