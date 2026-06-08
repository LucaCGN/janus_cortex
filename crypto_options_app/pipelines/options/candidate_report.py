from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.pipelines.options.metrics import metric_contract
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_CANDIDATE_REPORT_SCHEMA_VERSION = "crypto_options_candidate_report_v1"


def build_candidate_report(
    aggregate_payload: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    operator_criteria: dict[str, Any] | None = None,
    top_breakdown_rows: int = 5,
    representative_rows: int = 5,
) -> dict[str, Any]:
    """Build a complete report of every strategy candidate in an aggregate.

    This report is deliberately descriptive. It does not select candidates for
    execution; it exposes the evidence needed for operator-defined criteria.
    """

    generated_at = generated_at or datetime.now(timezone.utc)
    strategy_rows = (aggregate_payload.get("rolling_shadow_metrics") or {}).get("strategies") or []
    candidates = [
        _strategy_candidate_summary(
            row,
            top_breakdown_rows=max(0, int(top_breakdown_rows)),
            representative_rows=max(0, int(representative_rows)),
        )
        for row in strategy_rows
    ]
    criteria = operator_criteria or _default_operator_criteria()
    evaluated = [_evaluate_operator_criteria(row, criteria) for row in candidates]
    payload = {
        "schema_version": CRYPTO_OPTIONS_CANDIDATE_REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "source_aggregate_schema_version": aggregate_payload.get("schema_version"),
        "source_generated_at_utc": aggregate_payload.get("generated_at_utc"),
        "source_capture_dirs": aggregate_payload.get("capture_dirs") or [],
        "source_data_counts": aggregate_payload.get("data_counts") or {},
        "metric_contract": metric_contract(),
        "operator_selection_required": True,
        "selection_policy": {
            "selected_strategy_ids": [],
            "reason": "No strategy is auto-selected by this report. Operator criteria must explicitly approve candidates.",
        },
        "operator_criteria": criteria,
        "candidate_count": len(candidates),
        "candidates": evaluated,
        "ranked_preview": _ranked_preview(evaluated),
        "data_quality": {
            "blockers": aggregate_payload.get("blockers") or [],
            "warnings": aggregate_payload.get("warnings") or [],
            "capture_summaries": aggregate_payload.get("capture_summaries") or [],
        },
        "boundary": {
            "orders_allowed": False,
            "live_trading_authorized": False,
            "description": (
                "Report-only candidate evidence. This artifact does not place, cancel, sign, "
                "broadcast, redeem, route, recommend, or authorize orders."
            ),
        },
    }
    return strict_jsonable(payload)


def write_candidate_report_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_dir) if output_dir else resolve_shared_root() / "artifacts" / "crypto-options-research" / day
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_candidate_report_{stamp}.json"
    md_path = root / f"crypto_options_candidate_report_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_candidate_report_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_candidate_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Candidate Evidence Report",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Source aggregate: `{payload.get('source_generated_at_utc')}`",
        f"- Candidate count: `{payload.get('candidate_count')}`",
        f"- Operator selection required: `{payload.get('operator_selection_required')}`",
        f"- Orders allowed: `{(payload.get('boundary') or {}).get('orders_allowed')}`",
        f"- Live trading authorized: `{(payload.get('boundary') or {}).get('live_trading_authorized')}`",
        "",
        "## Operator Criteria",
        "",
    ]
    for key, value in (payload.get("operator_criteria") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Ranked Preview", ""])
    for row in payload.get("ranked_preview") or []:
        lines.append(
            "- `{strategy}` criteria=`{criteria}` gate_ready=`{ready}` trades=`{trades}` win=`{win}` pnl=`{pnl}` "
            "max_loss_streak=`{losses}` net_stop_min_budget=`{min_budget}` failed_gates=`{failed}`".format(
                strategy=row.get("strategy_id"),
                criteria=row.get("operator_criteria_status"),
                ready=row.get("minimum_live_test_ready"),
                trades=row.get("deduped_trade_count"),
                win=_fmt(row.get("win_rate")),
                pnl=_fmt(row.get("return_sum")),
                losses=row.get("max_sequential_losses"),
                min_budget=_fmt(row.get("budget_min_budget")),
                failed=len(row.get("failed_gates") or []),
            )
        )
    lines.extend(["", "## All Candidates", ""])
    for row in payload.get("candidates") or []:
        lines.extend(_candidate_markdown(row))
    quality = payload.get("data_quality") or {}
    if quality.get("blockers"):
        lines.extend(["", "## Data Blockers", ""])
        lines.extend([f"- `{item}`" for item in quality.get("blockers") or []])
    if quality.get("warnings"):
        lines.extend(["", "## Data Warnings", ""])
        lines.extend([f"- `{item}`" for item in quality.get("warnings") or []])
    lines.extend(["", "## Boundary", "", (payload.get("boundary") or {}).get("description", "")])
    return "\n".join(lines).rstrip() + "\n"


def _strategy_candidate_summary(
    row: dict[str, Any],
    *,
    top_breakdown_rows: int,
    representative_rows: int,
) -> dict[str, Any]:
    metrics = row.get("trade_metrics") or {}
    sample = metrics.get("sample_metrics") or {}
    gate = row.get("minimum_live_test_gate_report") or {}
    budget = row.get("budget_simulation") or {}
    candidate = {
        "strategy_id": row.get("strategy_id"),
        "minimum_live_test_ready": bool(row.get("minimum_live_test_ready")),
        "deduped_trade_count": row.get("deduped_trade_count"),
        "raw_trade_candidate_rows": row.get("raw_trade_candidate_rows"),
        "settled_rows": row.get("settled_rows"),
        "pending_rows": row.get("pending_rows"),
        "trade_metrics": metrics,
        "sample_metrics": sample,
        "budget_simulation": budget,
        "feature_quality": row.get("feature_quality") or {},
        "feature_breakdowns": _top_feature_breakdowns(row.get("feature_breakdowns") or {}, limit=top_breakdown_rows),
        "no_trade_reason_counts": row.get("no_trade_reason_counts") or {},
        "minimum_live_test_gate_report": gate,
        "failed_gates": gate.get("failed_gates") or [],
        "gate_policy": gate.get("policy") or {},
        "representative_rows": _representative_rows(row.get("rows") or [], limit=representative_rows),
    }
    return strict_jsonable(candidate)


def _evaluate_operator_criteria(row: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("trade_metrics") or {}
    budget = row.get("budget_simulation") or {}
    failed: list[str] = []
    _criteria_min(failed, "deduped_trade_count", row.get("deduped_trade_count"), criteria.get("min_deduped_trades"))
    _criteria_min(failed, "win_rate", metrics.get("win_rate"), criteria.get("min_win_rate"))
    _criteria_min(failed, "return_sum", metrics.get("return_sum"), criteria.get("min_return_sum"))
    _criteria_max(failed, "max_sequential_losses", metrics.get("max_sequential_losses"), criteria.get("max_sequential_losses"))
    _criteria_min(failed, "budget_min_budget", budget.get("min_budget"), criteria.get("min_budget_floor"))
    _criteria_min(failed, "settlement_coverage", _settlement_coverage(row), criteria.get("min_settlement_coverage"))
    return {
        **row,
        "operator_criteria_status": "passes_operator_criteria" if not failed else "fails_operator_criteria",
        "operator_criteria_failed": failed,
        "settlement_coverage": _settlement_coverage(row),
    }


def _default_operator_criteria() -> dict[str, Any]:
    return {
        "min_deduped_trades": 30,
        "min_win_rate": 0.60,
        "min_return_sum": 0.0,
        "max_sequential_losses": 3,
        "min_budget_floor": 17.0,
        "min_settlement_coverage": 0.95,
    }


def _ranked_preview(candidates: list[dict[str, Any]], *, limit: int = 25) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        metrics = row.get("trade_metrics") or {}
        budget = row.get("budget_simulation") or {}
        failed = row.get("operator_criteria_failed") or []
        return (
            len(failed),
            0 if row.get("minimum_live_test_ready") else 1,
            -(metrics.get("return_sum") or 0.0),
            -(metrics.get("win_rate") or 0.0),
            -(budget.get("min_budget") or 0.0),
        )

    preview = []
    for row in sorted(candidates, key=sort_key)[:limit]:
        metrics = row.get("trade_metrics") or {}
        budget = row.get("budget_simulation") or {}
        preview.append(
            {
                "strategy_id": row.get("strategy_id"),
                "operator_criteria_status": row.get("operator_criteria_status"),
                "operator_criteria_failed": row.get("operator_criteria_failed") or [],
                "minimum_live_test_ready": row.get("minimum_live_test_ready"),
                "deduped_trade_count": row.get("deduped_trade_count"),
                "win_rate": metrics.get("win_rate"),
                "return_sum": metrics.get("return_sum"),
                "return_avg": metrics.get("return_avg"),
                "max_sequential_losses": metrics.get("max_sequential_losses"),
                "avg_sequential_losses": metrics.get("avg_sequential_losses"),
                "max_drawdown": metrics.get("max_drawdown"),
                "profit_factor": metrics.get("profit_factor"),
                "budget_min_budget": budget.get("min_budget"),
                "budget_final_budget": budget.get("final_budget"),
                "failed_gates": row.get("failed_gates") or [],
            }
        )
    return strict_jsonable(preview)


def _top_feature_breakdowns(payload: dict[str, Any], *, limit: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, rows in payload.items():
        if not isinstance(rows, list):
            result[str(name)] = rows
            continue
        sorted_rows = sorted(
            rows,
            key=lambda item: (
                -(item.get("trade_count") or 0) if isinstance(item, dict) else 0,
                -(item.get("return_sum") or 0.0) if isinstance(item, dict) else 0.0,
            ),
        )
        result[str(name)] = sorted_rows[:limit]
    return result


def _representative_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    fields = (
        "event_slug",
        "strategy_id",
        "symbol",
        "outcome",
        "quote_at_utc",
        "time_remaining_seconds",
        "best_ask",
        "spread",
        "depth_top3_ask_size",
        "min_order_total_cost",
        "hypothetical_hold_to_settlement_pnl_net",
        "would_win",
        "settlement_status",
        "resolved_outcome",
        "reference_ladder_band",
    )
    return [{field: row.get(field) for field in fields if field in row} for row in rows[:limit]]


def _candidate_markdown(row: dict[str, Any]) -> list[str]:
    metrics = row.get("trade_metrics") or {}
    sample = row.get("sample_metrics") or {}
    budget = row.get("budget_simulation") or {}
    lines = [
        f"### `{row.get('strategy_id')}`",
        "",
        f"- Operator criteria: `{row.get('operator_criteria_status')}`",
        f"- Failed operator criteria: `{row.get('operator_criteria_failed')}`",
        f"- Live gate ready: `{row.get('minimum_live_test_ready')}`",
        f"- Failed live gates: `{row.get('failed_gates')}`",
        f"- Rows: deduped=`{row.get('deduped_trade_count')}`, raw=`{row.get('raw_trade_candidate_rows')}`, settled=`{row.get('settled_rows')}`, pending=`{row.get('pending_rows')}`",
        f"- Win/loss: wins=`{metrics.get('win_count')}`, losses=`{metrics.get('loss_count')}`, win_rate=`{_fmt(metrics.get('win_rate'))}`",
        f"- Return: sum=`{_fmt(metrics.get('return_sum'))}`, avg=`{_fmt(metrics.get('return_avg'))}`, min=`{_fmt(metrics.get('return_min'))}`, max=`{_fmt(metrics.get('return_max'))}`, std=`{_fmt(metrics.get('return_std'))}`",
        f"- Streaks: max_loss=`{metrics.get('max_sequential_losses')}`, avg_loss=`{_fmt(metrics.get('avg_sequential_losses'))}`, p95_loss=`{_fmt(metrics.get('p95_sequential_losses'))}`, max_win=`{metrics.get('max_sequential_wins')}`",
        f"- Risk: max_drawdown=`{_fmt(metrics.get('max_drawdown'))}`, profit_factor=`{_fmt(metrics.get('profit_factor'))}`, expectancy=`{_fmt(metrics.get('expectancy'))}`, payoff_ratio=`{_fmt(metrics.get('payoff_ratio'))}`",
        f"- Sample stats: samples=`{sample.get('sample_count')}`, win_avg/min/max=`{_fmt(sample.get('win_rate_avg'))}`/`{_fmt(sample.get('win_rate_min'))}`/`{_fmt(sample.get('win_rate_max'))}`, return_avg/min/max=`{_fmt(sample.get('return_avg'))}`/`{_fmt(sample.get('return_min'))}`/`{_fmt(sample.get('return_max'))}`",
        f"- Budget simulation: final=`{_fmt(budget.get('final_budget'))}`, min=`{_fmt(budget.get('min_budget'))}`, executed=`{budget.get('executed_trades')}`, budget_denied=`{budget.get('budget_denied_trades')}`, hard_stop_denied=`{budget.get('hard_stop_denied_trades')}`",
        f"- Feature quality: `{row.get('feature_quality')}`",
        "",
    ]
    return lines


def _settlement_coverage(row: dict[str, Any]) -> float | None:
    settled = row.get("settled_rows")
    pending = row.get("pending_rows")
    try:
        total = float(settled or 0) + float(pending or 0)
        if total <= 0:
            return None
        return float(settled or 0) / total
    except (TypeError, ValueError):
        return None


def _criteria_min(failed: list[str], name: str, value: Any, threshold: Any) -> None:
    if threshold is None:
        return
    if value is None or float(value) < float(threshold):
        failed.append(f"{name}_below_{threshold}")


def _criteria_max(failed: list[str], name: str, value: Any, threshold: Any) -> None:
    if threshold is None:
        return
    if value is None or float(value) > float(threshold):
        failed.append(f"{name}_above_{threshold}")


def _fmt(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


__all__ = [
    "build_candidate_report",
    "render_candidate_report_markdown",
    "write_candidate_report_artifacts",
]
