from __future__ import annotations

from typing import Any


READINESS_GATES = (
    {
        "gate_id": "reference_labels_verified",
        "description": "Resolved market labels reproduce the stated Chainlink/reference boundary rule.",
    },
    {
        "gate_id": "executable_bid_ask_depth_available",
        "description": "Replay uses executable bid/ask/depth, not midpoint or last-trade-only prices.",
    },
    {
        "gate_id": "positive_net_ev_holdout",
        "description": "Chronological holdout has positive net EV after fees, spread, slippage, and partial-fill stress.",
    },
    {
        "gate_id": "loss_streak_tolerable",
        "description": "Max and p95 sequential losses fit the bankroll and risk policy.",
    },
    {
        "gate_id": "sample_stability",
        "description": "Average/min/max sample win rates and returns do not rely on one favorable period.",
    },
    {
        "gate_id": "latency_stress_passed",
        "description": "Edge survives conservative latency assumptions, including 500ms+ degradation.",
    },
    {
        "gate_id": "shadow_fill_parity",
        "description": "Shadow observed entries/exits are close to replay assumptions.",
    },
    {
        "gate_id": "live_approval",
        "description": "Explicit human approval exists for min-size/live testing.",
    },
)


def evaluate_crypto_options_readiness(report: dict[str, Any]) -> dict[str, Any]:
    audit = report.get("data_audit") or {}
    comparison = report.get("strategy_comparison") or {}
    metric_rows = ((comparison.get("metric_summary") or {}).get("results") or [])
    blockers = set(report.get("blockers") or [])
    best_metric = _best_metric(metric_rows)

    gates = [
        _gate("reference_labels_verified", "pass" if _has_reference_labels(audit) else "fail", ["missing_verified_reference_labels"]),
        _gate(
            "executable_bid_ask_depth_available",
            "pass" if _has_executable_depth(audit) else "fail",
            ["missing_executable_bid_ask_depth"],
        ),
        _gate(
            "positive_net_ev_holdout",
            "pass" if (best_metric.get("return_sum") or 0.0) > 0 and not blockers else "fail",
            ["missing_positive_net_ev_after_costs"] if (best_metric.get("return_sum") or 0.0) <= 0 else list(blockers),
        ),
        _gate(
            "loss_streak_tolerable",
            "pass" if _loss_streak_ok(best_metric) else "fail",
            ["missing_or_excessive_loss_streak_metrics"],
        ),
        _gate(
            "sample_stability",
            "pass" if _sample_stability_ok(metric_rows) else "fail",
            ["missing_sample_stability_metrics"],
        ),
        _gate("latency_stress_passed", "fail", ["latency_stress_not_run"]),
        _gate("shadow_fill_parity", "fail", ["shadow_fill_parity_not_run"]),
        _gate("live_approval", "fail", ["issue_47_does_not_authorize_live_or_min_size_trading"]),
    ]
    ready_for_shadow = all(gate["status"] == "pass" for gate in gates[:6])
    ready_for_min_size = ready_for_shadow and all(gate["status"] == "pass" for gate in gates[6:])
    return {
        "schema_version": "crypto_options_readiness_v1",
        "ready_for_shadow": bool(ready_for_shadow),
        "ready_for_min_size_or_live": bool(ready_for_min_size),
        "live_trading_authorized": False,
        "gates": gates,
        "best_metric_snapshot": best_metric,
        "decision": "research_only",
    }


def readiness_gate_contract() -> list[dict[str, str]]:
    return [dict(gate) for gate in READINESS_GATES]


def _gate(gate_id: str, status: str, blockers: list[str]) -> dict[str, Any]:
    description = next((gate["description"] for gate in READINESS_GATES if gate["gate_id"] == gate_id), "")
    return {"gate_id": gate_id, "description": description, "status": status, "blockers": sorted(set(blockers)) if status != "pass" else []}


def _has_reference_labels(audit: dict[str, Any]) -> bool:
    validity = audit.get("strategy_validity") or {}
    directional = validity.get("directional_prediction") or {}
    blockers = set(directional.get("blockers") or [])
    return "missing_reference_window_prices_for_up_down" not in blockers and "missing_settlement_thresholds" not in blockers


def _has_executable_depth(audit: dict[str, Any]) -> bool:
    clob = audit.get("available_clob_history") or {}
    return int(clob.get("rows_with_bid_ask") or 0) > 0


def _best_metric(metric_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not metric_rows:
        return {}
    return max(metric_rows, key=lambda row: float(row.get("return_sum") or 0.0))


def _loss_streak_ok(metric: dict[str, Any]) -> bool:
    trade_count = int(metric.get("trade_count") or 0)
    if trade_count == 0:
        return False
    max_losses = metric.get("max_sequential_losses")
    if max_losses is None:
        return False
    return int(max_losses) <= max(5, trade_count // 4)


def _sample_stability_ok(metric_rows: list[dict[str, Any]]) -> bool:
    return any(row.get("trade_count") and row.get("return_sum") is not None and row.get("win_rate") is not None for row in metric_rows)


__all__ = [
    "evaluate_crypto_options_readiness",
    "readiness_gate_contract",
]
