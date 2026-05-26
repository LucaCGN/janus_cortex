from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "prewindow_sleeve_readiness_v1"


def build_prewindow_sleeve_readiness(
    *,
    event_id: str,
    strategy_plan: dict[str, Any] | None,
    backtest_review: dict[str, Any] | None,
    source: str = "janus-prewindow-readiness",
) -> dict[str, Any]:
    plan = dict(strategy_plan or {})
    review = dict(backtest_review or {})
    ranking_by_role = _rankings_by_role(review.get("sleeve_rankings"))
    promotion_rows = _promotion_rows_by_role(review.get("recommendations"))
    wnba_blockers = [dict(row) for row in review.get("wnba_blockers") or [] if isinstance(row, dict)]
    sleeve_rows = [
        _strategy_readiness_row(
            strategy=dict(strategy),
            ranking_by_role=ranking_by_role,
            promotion_rows=promotion_rows,
        )
        for strategy in plan.get("active_strategies") or []
        if isinstance(strategy, dict)
    ]
    role_counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)
    for row in sleeve_rows:
        role_counts[row["sleeve_role"]] += 1
        status_counts[row["readiness_status"]] += 1

    warnings = _global_warnings(sleeve_rows=sleeve_rows, wnba_blockers=wnba_blockers)
    status = "GREEN"
    if warnings or status_counts.get("experimental_guardrails_required"):
        status = "YELLOW"
    if status_counts.get("blocked") == len(sleeve_rows) and sleeve_rows:
        status = "RED"

    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "status": status,
        "strategy_plan_present": bool(plan),
        "active_strategy_count": len(sleeve_rows),
        "role_counts": dict(sorted(role_counts.items())),
        "readiness_status_counts": dict(sorted(status_counts.items())),
        "sleeves": sleeve_rows,
        "warnings": warnings,
        "recommended_live_window_actions": _recommended_actions(sleeve_rows=sleeve_rows, warnings=warnings),
        "backtest_review_metadata": {
            "schema_version": review.get("schema_version"),
            "generated_at_utc": review.get("generated_at_utc"),
            "cohort_count": len(review.get("cohorts") or []),
            "wnba_blocker_count": len(wnba_blockers),
        },
    }


def render_prewindow_sleeve_readiness_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Pre-Window Sleeve Readiness - {report.get('event_id')}",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Active strategies: `{report.get('active_strategy_count')}`",
        f"- Role counts: `{report.get('role_counts')}`",
        f"- Readiness counts: `{report.get('readiness_status_counts')}`",
        "",
        "## Sleeves",
        "",
        "| Sleeve | Role | Family | Status | Evidence | Action |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.get("sleeves") or []:
        evidence = row.get("evidence_summary") or "n/a"
        action = row.get("recommended_action") or "n/a"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("sleeve_id")),
                    _md(row.get("sleeve_role")),
                    _md(row.get("strategy_family")),
                    _md(row.get("readiness_status")),
                    _md(evidence),
                    _md(action),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Warnings", ""])
    warnings = report.get("warnings") or []
    if not warnings:
        lines.append("- None.")
    else:
        for warning in warnings:
            lines.append(f"- `{warning.get('code')}`: {warning.get('message')}")
    lines.extend(["", "## Recommended Actions", ""])
    for action in report.get("recommended_live_window_actions") or []:
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


def _strategy_readiness_row(
    *,
    strategy: dict[str, Any],
    ranking_by_role: dict[str, list[dict[str, Any]]],
    promotion_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
    role = str(strategy.get("sleeve_role") or entry_rules.get("sleeve_role") or "unknown").strip() or "unknown"
    family = str(strategy.get("family") or strategy.get("strategy_family") or "").strip()
    rankings = ranking_by_role.get(role, [])
    best = rankings[0] if rankings else {}
    promotions = promotion_rows.get(role, [])
    avg_return = _number(best.get("avg_return_with_slippage"))
    pnl_total = _number(best.get("min_order_pnl_total"))
    trade_count = int(best.get("trade_count") or 0)

    if role == "ultra_low_rebound":
        status = "experimental_guardrails_required"
        action = "Keep capped and evidence-tracked; do not promote naive ultra-low without tighter comeback/volatility conditions."
    elif avg_return is not None and avg_return > 0 and trade_count >= 25:
        status = "supported_for_live_validation"
        action = "Allow live validation through normal Janus gates; review paired exits and sleeve attribution postgame."
    elif avg_return is not None and avg_return > 0:
        status = "thin_positive_sample"
        action = "Allow only if event context matches the promoted family; otherwise monitor."
    elif avg_return is not None:
        status = "weak_or_negative_replay"
        action = "Monitor or replay-only unless operator explicitly overrides for development-money testing."
    else:
        status = "missing_replay_evidence"
        action = "Monitor until a matching replay fixture exists."

    return {
        "strategy_id": strategy.get("strategy_id"),
        "sleeve_id": strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy.get("strategy_id"),
        "sleeve_role": role,
        "sleeve_group": strategy.get("sleeve_group"),
        "side": strategy.get("side") or entry_rules.get("outcome_label"),
        "strategy_family": family,
        "readiness_status": status,
        "recommended_action": action,
        "evidence_summary": _evidence_summary(best, promotions),
        "best_role_backtest": best,
        "top_promotion_families": promotions[:5],
    }


def _rankings_by_role(rows: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("live_sleeve_role") or "").strip()
        if role:
            grouped[role].append(dict(row))
    for role, values in grouped.items():
        values.sort(key=lambda item: (_number(item.get("avg_return_with_slippage")) or -999.0), reverse=True)
    return dict(grouped)


def _promotion_rows_by_role(recommendations: Any) -> dict[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for section in recommendations or []:
        if not isinstance(section, dict) or section.get("area") != "sleeve_promotion_candidates":
            continue
        for row in section.get("top_rows") or []:
            if isinstance(row, dict):
                rows.append(dict(row))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        role = str(row.get("live_sleeve_role") or "").strip()
        if role:
            grouped[role].append(row)
    for values in grouped.values():
        values.sort(key=lambda item: (_number(item.get("avg_return_with_slippage")) or -999.0), reverse=True)
    return dict(grouped)


def _global_warnings(*, sleeve_rows: list[dict[str, Any]], wnba_blockers: list[dict[str, Any]]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if any(row.get("readiness_status") == "experimental_guardrails_required" for row in sleeve_rows):
        warnings.append(
            {
                "code": "ultra_low_naive_replay_negative",
                "message": "Ultra-low sleeves are present, but isolated replay was strongly negative; treat as constrained development evidence, not proven alpha.",
            }
        )
    if wnba_blockers:
        warnings.append(
            {
                "code": "wnba_replay_parity_blocked",
                "message": "WNBA has PBP rows but missing market-state/price panels, so sleeve replay parity is not ready.",
            }
        )
    return warnings


def _recommended_actions(*, sleeve_rows: list[dict[str, Any]], warnings: list[dict[str, str]]) -> list[str]:
    actions = [
        "Keep grid/core sleeves live-test eligible behind fresh scoreboard, CLOB, budget, and paired-microcycle gates.",
        "Record PBP annotation evidence every tick so postgame replay can explain why a sleeve did or did not act.",
        "Require postgame comparison by sleeve: realized_live, sleeve_isolated, aggregate_replay, and leave_one_out.",
    ]
    if any(warning.get("code") == "ultra_low_naive_replay_negative" for warning in warnings):
        actions.append("For ultra-low sleeves, cap exposure and require explicit volatility/comeback context before treating fills as strategy success.")
    if any(row.get("readiness_status") == "missing_replay_evidence" for row in sleeve_rows):
        actions.append("Add replay fixtures for sleeves with missing evidence before promoting them beyond controlled development testing.")
    return actions


def _evidence_summary(best: dict[str, Any], promotions: list[dict[str, Any]]) -> str:
    if not best:
        return "missing replay evidence"
    avg_return = _number(best.get("avg_return_with_slippage"))
    pnl = _number(best.get("min_order_pnl_total"))
    trades = int(best.get("trade_count") or 0)
    promoted = ", ".join(str(row.get("strategy_family")) for row in promotions[:3] if row.get("strategy_family"))
    summary = f"{avg_return:.2%} avg return, ${pnl:.2f} min-order PnL, {trades} trades"
    if promoted:
        summary += f"; promoted families: {promoted}"
    return summary


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


__all__ = [
    "SCHEMA_VERSION",
    "build_prewindow_sleeve_readiness",
    "render_prewindow_sleeve_readiness_markdown",
]
