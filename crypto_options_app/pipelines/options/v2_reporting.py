from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.pipelines.options.v2_candidates import V2_CANDIDATE_IDS


V2_REPORT_SCHEMA_VERSION = "crypto_options_v2_candidate_comparison_report_v1"


def build_v2_candidate_comparison_report(
    *,
    candidate_decisions: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]] | None = None,
    statistical_backtest: dict[str, Any] | None = None,
    cashout_simulation: dict[str, Any] | None = None,
    price_path_trace: dict[str, Any] | None = None,
    promotion_states: dict[str, dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    ledgers = ledgers or {}
    promotion_states = promotion_states or {}
    missing_fields: list[str] = []
    per_candidate = [_candidate_scoreboard(candidate_id, candidate_decisions, ledgers, promotion_states, missing_fields) for candidate_id in V2_CANDIDATE_IDS]
    report = {
        "schema_version": V2_REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
        "execution_boundary": "post_run_report_only",
        "live_trading_authorized": False,
        "sections": {
            "global_scoreboard": _global_scoreboard(per_candidate),
            "per_candidate_scoreboard": per_candidate,
            "statistical_component_attribution": _statistical_component_attribution(statistical_backtest, missing_fields),
            "cashout_capture": _cashout_capture(cashout_simulation, price_path_trace, missing_fields),
            "divergent_decision_comparison": _divergent_decisions(candidate_decisions),
            "cross_candidate_entry_price_comparison": _entry_price_comparison(candidate_decisions, price_path_trace, missing_fields),
            "promotion_demotion_state": _promotion_state_section(promotion_states, missing_fields),
            "safety_statement": {
                "manual_orders_placed": False,
                "unsupervised_production_authority": False,
                "live_service_started_by_report": False,
                "statement": "This V2 report is generated from artifacts only and does not authorize live trading.",
            },
            "missing_fields": sorted(set(missing_fields)),
        },
    }
    return strict_jsonable(report)


def render_v2_candidate_report_markdown(report: dict[str, Any]) -> str:
    sections = report.get("sections") or {}
    lines = [
        "# Crypto Options V2 Candidate Comparison",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Boundary: `{report.get('execution_boundary')}`",
        "",
        "## Global Scoreboard",
        "",
        _markdown_table(sections.get("global_scoreboard") or {}),
        "",
        "## Per-Candidate Scoreboard",
        "",
        _markdown_table_rows(sections.get("per_candidate_scoreboard") or []),
        "",
        "## Statistical Component Attribution",
        "",
        _markdown_table_rows((sections.get("statistical_component_attribution") or {}).get("components") or []),
        "",
        "## Cashout Capture",
        "",
        _markdown_table(sections.get("cashout_capture") or {}),
        "",
        "## Divergent Decisions",
        "",
        _markdown_table_rows(sections.get("divergent_decision_comparison") or []),
        "",
        "## Entry Price Comparison",
        "",
        _markdown_table_rows(sections.get("cross_candidate_entry_price_comparison") or []),
        "",
        "## Promotion State",
        "",
        _markdown_table_rows(sections.get("promotion_demotion_state") or []),
        "",
        "## Missing Fields",
        "",
        "\n".join(f"- `{field}`" for field in (sections.get("missing_fields") or [])) or "- none",
        "",
        "## Safety Statement",
        "",
        str((sections.get("safety_statement") or {}).get("statement") or ""),
    ]
    return "\n".join(lines) + "\n"


def _candidate_scoreboard(
    candidate_id: str,
    candidate_decisions: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
    promotion_states: dict[str, dict[str, Any]],
    missing_fields: list[str],
) -> dict[str, Any]:
    decision = next((row for row in candidate_decisions if row.get("candidate_id") == candidate_id), {})
    ledger = ledgers.get(candidate_id) or {}
    entries = [entry for entry in ledger.get("entries") or [] if isinstance(entry, dict)]
    settled = [entry for entry in entries if entry.get("realized_pnl_net_usd") is not None]
    realized = sum(_to_float(entry.get("realized_pnl_net_usd")) or 0.0 for entry in settled)
    cost = sum(abs(_to_float(entry.get("cost_usd")) or _to_float(entry.get("notional_usd")) or 0.0) for entry in settled)
    wins = sum(1 for entry in settled if (_to_float(entry.get("realized_pnl_net_usd")) or 0.0) > 0.0)
    open_count = sum(1 for entry in entries if str(entry.get("status") or "").lower() == "open")
    if not ledger:
        missing_fields.append(f"ledger_missing:{candidate_id}")
    return {
        "candidate_id": candidate_id,
        "status": decision.get("status"),
        "submitted": len([entry for entry in entries if str(entry.get("status") or "").lower() in {"submitted", "settled", "open"}]),
        "settled": len(settled),
        "open": open_count,
        "realized_pnl_usd": round(realized, 6),
        "roi_on_settled_cost": round(realized / cost, 6) if cost else None,
        "hit_rate": round(wins / len(settled), 6) if settled else None,
        "drawdown": _max_drawdown([_to_float(entry.get("realized_pnl_net_usd")) or 0.0 for entry in settled]),
        "current_exposure_usd": _to_float(decision.get("current_exposure_usd")) or 0.0,
        "cap_distance_usd": (promotion_states.get(candidate_id) or {}).get("distance_to_dynamic_loss_stop_usd"),
        "blockers": decision.get("blockers") or [],
        "ledger_path": decision.get("ledger_path"),
    }


def _global_scoreboard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    realized = sum(_to_float(row.get("realized_pnl_usd")) or 0.0 for row in rows)
    settled = sum(int(row.get("settled") or 0) for row in rows)
    submitted = sum(int(row.get("submitted") or 0) for row in rows)
    open_count = sum(int(row.get("open") or 0) for row in rows)
    return {
        "submitted": submitted,
        "settled": settled,
        "open": open_count,
        "realized_pnl_usd": round(realized, 6),
        "hit_rate": _weighted_hit_rate(rows),
        "max_drawdown_usd": min((_to_float(row.get("drawdown")) or 0.0 for row in rows), default=0.0),
        "open_exposure_usd": sum(_to_float(row.get("current_exposure_usd")) or 0.0 for row in rows),
        "global_cap_distance_usd": 100.0 + realized,
    }


def _statistical_component_attribution(backtest: dict[str, Any] | None, missing_fields: list[str]) -> dict[str, Any]:
    if not backtest:
        missing_fields.append("statistical_backtest_missing")
        return {"components": []}
    components = []
    for result in backtest.get("results") or []:
        metrics = result.get("metrics") or {}
        components.append(
            {
                "component_id": result.get("component_id"),
                "status": result.get("status"),
                "signals": result.get("signal_count"),
                "trades": result.get("trade_count"),
                "pnl": metrics.get("return_sum"),
                "hit_rate": metrics.get("win_rate"),
                "drawdown": metrics.get("max_drawdown"),
                "blockers": result.get("blockers") or [],
            }
        )
    return {"components": components, "chronological_split": backtest.get("split_summary")}


def _cashout_capture(cashout: dict[str, Any] | None, trace: dict[str, Any] | None, missing_fields: list[str]) -> dict[str, Any]:
    if not cashout:
        missing_fields.append("cashout_simulation_missing")
    if not trace:
        missing_fields.append("price_path_trace_missing")
    return {
        "coverage": (trace or {}).get("coverage") or (trace or {}).get("coverage_rate"),
        "trace_count": len((trace or {}).get("traces") or []),
        "cashout_hit_count": (cashout or {}).get("cashout_hit_count"),
        "prevented_loss_count": (cashout or {}).get("prevented_loss_count"),
        "reduced_winner_count": (cashout or {}).get("reduced_winner_count"),
        "pnl_delta_vs_hold_usd": (cashout or {}).get("total_pnl_delta_vs_hold_usd"),
    }


def _divergent_decisions(candidate_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_event: dict[str, list[dict[str, Any]]] = {}
    for decision in candidate_decisions:
        selected = decision.get("selected_candidate") or {}
        event = str(selected.get("event_slug") or selected.get("event_id") or "missing_event")
        by_event.setdefault(event, []).append(decision)
    for event, decisions in by_event.items():
        outcomes = sorted({str((row.get("selected_candidate") or {}).get("outcome")) for row in decisions if (row.get("selected_candidate") or {}).get("outcome")})
        statuses = sorted({str(row.get("status")) for row in decisions})
        if len(outcomes) > 1 or len(statuses) > 1:
            rows.append({"event": event, "outcomes": outcomes, "statuses": statuses, "candidate_count": len(decisions)})
    return rows


def _entry_price_comparison(candidate_decisions: list[dict[str, Any]], trace: dict[str, Any] | None, missing_fields: list[str]) -> list[dict[str, Any]]:
    if not trace:
        missing_fields.append("entry_price_trace_missing")
    rows = []
    for decision in candidate_decisions:
        selected = decision.get("selected_candidate") or {}
        rows.append(
            {
                "candidate_id": decision.get("candidate_id"),
                "event": selected.get("event_slug") or selected.get("event_id"),
                "outcome": selected.get("outcome"),
                "entry_price": selected.get("best_ask") or selected.get("price") or selected.get("observed_execution_price"),
                "price_bucket": _price_bucket(_to_float(selected.get("best_ask") or selected.get("price") or selected.get("observed_execution_price"))),
                "blockers": decision.get("blockers") or [],
            }
        )
    return rows


def _promotion_state_section(promotion_states: dict[str, dict[str, Any]], missing_fields: list[str]) -> list[dict[str, Any]]:
    if not promotion_states:
        missing_fields.append("promotion_states_missing")
    return [
        {
            "candidate_id": candidate_id,
            "current_ticket_usd": state.get("current_ticket_usd"),
            "pre_demotion_ticket_usd": state.get("pre_demotion_ticket_usd"),
            "dynamic_loss_stop_usd": state.get("dynamic_loss_stop_usd"),
            "promotion_unlocked": state.get("promotion_unlocked"),
            "current_ticket_promoted": state.get("current_ticket_promoted"),
            "demotion_active": state.get("demotion_active"),
            "demotion_reason": state.get("demotion_reason"),
            "disabled": state.get("disabled"),
            "disabled_reason": state.get("disabled_reason"),
        }
        for candidate_id, state in promotion_states.items()
    ]


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(max_dd, 6)


def _weighted_hit_rate(rows: list[dict[str, Any]]) -> float | None:
    settled = sum(int(row.get("settled") or 0) for row in rows)
    if not settled:
        return None
    wins = 0.0
    for row in rows:
        hit_rate = _to_float(row.get("hit_rate"))
        if hit_rate is not None:
            wins += hit_rate * int(row.get("settled") or 0)
    return round(wins / settled, 6)


def _price_bucket(price: float | None) -> str | None:
    if price is None:
        return None
    for low, high in ((0.05, 0.10), (0.10, 0.25), (0.25, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 0.90)):
        if low <= price < high:
            return f"{low:.2f}-{high:.2f}"
    return "out_of_policy_range"


def _markdown_table(mapping: dict[str, Any]) -> str:
    if not mapping:
        return "_No data._"
    return "\n".join(["| Metric | Value |", "|---|---|"] + [f"| `{key}` | `{value}` |" for key, value in mapping.items()])


def _markdown_table_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No data._"
    columns = list(rows[0].keys())
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


__all__ = [
    "V2_REPORT_SCHEMA_VERSION",
    "build_v2_candidate_comparison_report",
    "render_v2_candidate_report_markdown",
]
