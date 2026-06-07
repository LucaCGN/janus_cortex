from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_MICRO_TEST_MONITOR_SCHEMA_VERSION = "crypto_options_micro_test_monitor_v1"

REFERENCE_ACCOUNT_WATCH_CONFIG = {
    "watch_id": "reference_account_ladder_watch_only",
    "description": (
        "Observation-only lane for Bonereaper-style laddered crypto up/down entries. "
        "This is not an approved live-test gate."
    ),
    "price_bands": [
        {"name": "cheap_convex_ladder", "best_ask_min": 0.20, "best_ask_max": 0.40},
        {"name": "lower_mid_ladder", "best_ask_min": 0.40, "best_ask_max": 0.55},
        {"name": "upper_mid_ladder", "best_ask_min": 0.55, "best_ask_max": 0.70},
        {"name": "dominant_side_ladder", "best_ask_min": 0.70, "best_ask_max": 0.90},
        {"name": "late_high_confidence_ladder", "best_ask_min": 0.90, "best_ask_max": 0.98},
    ],
    "time_remaining_seconds_min": 20,
    "time_remaining_seconds_max": 900,
    "spread_max": 0.05,
    "top_ask_size_min": 5,
    "depth_top3_ask_size_min": 25,
    "quote_age_seconds_max": 5,
    "underlying_proxy_age_seconds_max": 10,
    "max_total_budget_usd": 20.0,
}


def build_micro_test_monitor(
    protocol_payload: dict[str, Any],
    decision_review_payload: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    manual_no_open_position_confirmed: bool = False,
    trade_ledger_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate live decision-review candidates against the approved protocol.

    This is a manual-only monitor. It never places, cancels, signs, broadcasts,
    redeems, routes, or authorizes orders.
    """

    generated_at = generated_at or datetime.now(timezone.utc)
    protocol_status = str(protocol_payload.get("protocol_status") or "")
    entry_gates = protocol_payload.get("entry_gates") or [protocol_payload.get("entry_gate") or {}]
    entry_gates = [gate for gate in entry_gates if gate.get("strategy_id")]
    entry_gate_by_strategy = {str(gate.get("strategy_id")): gate for gate in entry_gates}
    budget = protocol_payload.get("budget") or {}
    ledger_state = _trade_ledger_state(trade_ledger_payload, budget=budget)
    blockers: list[str] = []
    warnings: list[str] = []
    if protocol_status != "approved_protocol_ready_for_separate_execution_design":
        blockers.append("micro_test_protocol_not_approved")
    if protocol_payload.get("orders_allowed") is not False or protocol_payload.get("live_trading_authorized") is not False:
        blockers.append("protocol_boundary_not_read_only")
    if decision_review_payload.get("orders_allowed") is not False or decision_review_payload.get("live_trading_authorized") is not False:
        blockers.append("decision_review_boundary_not_read_only")
    if not manual_no_open_position_confirmed:
        blockers.append("manual_no_open_position_confirmation_missing")
    if ledger_state["trade_count"] >= int(budget.get("max_test_trades_before_review") or 999999):
        blockers.append("max_test_trades_reached")
    if ledger_state["loss_count"] >= int(budget.get("hard_stop_full_losses") or 999999):
        blockers.append("hard_stop_loss_count_reached")
    hard_stop_loss_usd = _float(budget.get("hard_stop_loss_usd"))
    if hard_stop_loss_usd is not None and ledger_state["net_realized_loss_usd"] >= hard_stop_loss_usd:
        blockers.append("hard_stop_loss_usd_reached")

    observed_candidates: list[dict[str, Any]] = []
    eligible_candidates: list[dict[str, Any]] = []
    reference_watch_candidates: list[dict[str, Any]] = []
    reference_watch_observations: list[dict[str, Any]] = []
    for row in decision_review_payload.get("candidate_reviews") or []:
        watch_row = _evaluate_reference_account_watch(row, generated_at=generated_at)
        if watch_row is not None:
            reference_watch_observations.append(watch_row)
            if not watch_row["failed_checks"]:
                reference_watch_candidates.append(watch_row)
        entry_gate = entry_gate_by_strategy.get(str(row.get("strategy_id")))
        if entry_gate is None:
            continue
        evaluated = _evaluate_candidate(row, entry_gate=entry_gate, budget=budget, generated_at=generated_at)
        observed_candidates.append(evaluated)
        if not evaluated["failed_checks"] and not blockers:
            eligible_candidates.append(evaluated)
    observed_candidates = _dedupe_candidate_rows(observed_candidates)
    eligible_candidates = _dedupe_candidate_rows(eligible_candidates)
    reference_watch_observations = _dedupe_candidate_rows(reference_watch_observations)
    reference_watch_candidates = _dedupe_candidate_rows(reference_watch_candidates)

    status = "eligible_manual_candidate_present" if eligible_candidates else "blocked"
    if not observed_candidates:
        status = "no_protocol_strategy_candidates"
    payload = {
        "schema_version": CRYPTO_OPTIONS_MICRO_TEST_MONITOR_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "protocol_status": protocol_status,
        "monitor_status": status,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_no_open_position_confirmed": bool(manual_no_open_position_confirmed),
        "manual_test_state": ledger_state,
        "decision_review_generated_at_utc": decision_review_payload.get("generated_at_utc"),
        "decision_review_capture_dir": decision_review_payload.get("capture_dir"),
        "observed_candidate_count": int(len(observed_candidates)),
        "eligible_manual_candidate_count": int(len(eligible_candidates)),
        "eligible_manual_candidates": eligible_candidates[:10],
        "observed_candidates": observed_candidates[:50],
        "reference_account_watch": {
            **REFERENCE_ACCOUNT_WATCH_CONFIG,
            "watch_only": True,
            "not_live_test_eligible": True,
            "orders_allowed": False,
            "live_trading_authorized": False,
            "observed_count": int(len(reference_watch_observations)),
            "watch_only_candidate_count": int(len(reference_watch_candidates)),
            "watch_only_candidates": reference_watch_candidates[:25],
            "observations": reference_watch_observations[:100],
        },
        "blockers": blockers,
        "warnings": warnings,
        "required_user_action": _required_user_action(status, blockers),
    }
    return strict_jsonable(payload)


def write_micro_test_monitor_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_dir) if output_dir else resolve_shared_root() / "artifacts" / "crypto-options-research" / day
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_micro_test_monitor_{stamp}.json"
    md_path = root / f"crypto_options_micro_test_monitor_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_micro_test_monitor_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_micro_test_monitor_markdown(payload: dict[str, Any]) -> str:
    state = payload.get("manual_test_state") or {}
    lines = [
        "# Crypto Options Micro-Test Monitor",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Monitor status: `{payload.get('monitor_status')}`",
        f"- Live trading authorized: `{payload.get('live_trading_authorized')}`",
        f"- Orders allowed: `{payload.get('orders_allowed')}`",
        f"- Observed protocol candidates: `{payload.get('observed_candidate_count')}`",
        f"- Eligible manual candidates: `{payload.get('eligible_manual_candidate_count')}`",
        f"- Manual ledger trades/losses: `{state.get('trade_count')}` / `{state.get('loss_count')}`",
        f"- Manual ledger net PnL: `${state.get('realized_pnl_net_usd')}`",
        f"- Net loss stop: `${state.get('net_realized_loss_usd')}` / `${state.get('hard_stop_loss_usd')}`",
        "",
    ]
    watch = payload.get("reference_account_watch") or {}
    lines.extend(
        [
            "## Reference Account Watch",
            "",
            f"- Watch ID: `{watch.get('watch_id')}`",
            f"- Watch-only candidates: `{watch.get('watch_only_candidate_count')}`",
            f"- Live-test eligible: `{not watch.get('not_live_test_eligible', True)}`",
            "",
        ]
    )
    if watch.get("watch_only_candidates"):
        for row in watch.get("watch_only_candidates") or []:
            lines.append(
                "- WATCH ONLY `{event}` `{outcome}` band=`{band}` ask=`{ask}` spread=`{spread}` depth_top3=`{depth}` quote_age=`{age}` remaining=`{remaining}` cost=`{cost}`".format(
                    event=row.get("event_slug"),
                    outcome=row.get("outcome"),
                    band=row.get("price_band"),
                    ask=row.get("best_ask"),
                    spread=row.get("spread"),
                    depth=row.get("depth_top3_ask_size"),
                    age=row.get("monitor_quote_age_seconds"),
                    remaining=row.get("time_remaining_seconds"),
                    cost=row.get("min_order_total_cost"),
                )
            )
        lines.append("")
    if payload.get("eligible_manual_candidates"):
        lines.extend(["## Eligible Manual Candidates", ""])
        for row in payload.get("eligible_manual_candidates") or []:
            ticket = row.get("manual_execution_ticket") or {}
            lines.append(
                "- `{strategy}` `{event}` `{outcome}` shares=`{shares}` ask=`{ask}` spread=`{spread}` depth_top3=`{depth}` quote_age=`{age}` remaining=`{remaining}` cost=`{cost}` max_cost=`{max_cost}`".format(
                    strategy=row.get("strategy_id"),
                    event=row.get("event_slug"),
                    outcome=row.get("outcome"),
                    shares=ticket.get("shares"),
                    ask=row.get("best_ask"),
                    spread=row.get("spread"),
                    depth=row.get("depth_top3_ask_size"),
                    age=row.get("monitor_quote_age_seconds"),
                    remaining=row.get("time_remaining_seconds"),
                    cost=row.get("min_order_total_cost"),
                    max_cost=ticket.get("max_total_cost_usd"),
                )
            )
    if payload.get("blockers"):
        lines.extend(["## Blockers", ""])
        lines.extend([f"- `{blocker}`" for blocker in payload.get("blockers") or []])
    lines.extend(
        [
            "## Boundary",
            "",
            "This monitor is manual-only evidence. It does not place, cancel, sign, broadcast, redeem, route, or authorize orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def _evaluate_candidate(row: dict[str, Any], *, entry_gate: dict[str, Any], budget: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    failed: list[str] = []
    quote_at = _timestamp(row.get("quote_at_utc"))
    quote_age = (generated_at - quote_at).total_seconds() if quote_at else None
    if row.get("status") != entry_gate.get("candidate_status_required"):
        failed.append("candidate_status_not_required_value")
    if row.get("outcome") == "Up+Down":
        failed.append("two_sided_candidate_not_allowed")
    if entry_gate.get("symbol_required") and str(row.get("symbol") or "").upper() != str(entry_gate.get("symbol_required")).upper():
        failed.append("symbol_not_required_value")
    if entry_gate.get("reference_ladder_band_required") and row.get("reference_ladder_band") != entry_gate.get("reference_ladder_band_required"):
        failed.append("reference_ladder_band_not_required_value")
    _check_min_max(failed, "time_remaining_seconds", _float(row.get("time_remaining_seconds")), entry_gate.get("time_remaining_seconds_min"), entry_gate.get("time_remaining_seconds_max"))
    _check_min_max(failed, "best_ask", _float(row.get("best_ask")), entry_gate.get("best_ask_min"), entry_gate.get("best_ask_max"))
    _check_min(failed, "spread", _float(row.get("spread")), entry_gate.get("spread_min"))
    _check_max(failed, "spread", _float(row.get("spread")), entry_gate.get("spread_max"))
    _check_min(failed, "depth_top3_ask_size", _float(row.get("depth_top3_ask_size")), entry_gate.get("depth_top3_ask_size_min"))
    _check_max(failed, "depth_top3_ask_size", _float(row.get("depth_top3_ask_size")), entry_gate.get("depth_top3_ask_size_max"))
    _check_min(failed, "ask_size", _float(row.get("ask_size")), entry_gate.get("top_ask_size_min"))
    _check_max(failed, "monitor_quote_age_seconds", quote_age, entry_gate.get("quote_age_seconds_max"))
    _check_max(failed, "underlying_proxy_age_seconds", _float(row.get("underlying_proxy_age_seconds")), entry_gate.get("underlying_proxy_age_seconds_max"))
    _check_max(failed, "min_order_total_cost", _float(row.get("min_order_total_cost")), budget.get("max_total_budget_usd"))
    _check_max(failed, "min_order_total_cost", _float(row.get("min_order_total_cost")), budget.get("max_position_cost_usd"))
    ticket = _manual_execution_ticket(row, budget=budget)
    return {
        "strategy_id": row.get("strategy_id"),
        "event_slug": row.get("event_slug"),
        "symbol": row.get("symbol"),
        "outcome": row.get("outcome"),
        "token_id": row.get("token_id"),
        "quote_at_utc": row.get("quote_at_utc"),
        "monitor_quote_age_seconds": quote_age,
        "time_remaining_seconds": row.get("time_remaining_seconds"),
        "best_ask": row.get("best_ask"),
        "spread": row.get("spread"),
        "ask_size": row.get("ask_size"),
        "depth_top3_ask_size": row.get("depth_top3_ask_size"),
        "underlying_proxy_age_seconds": row.get("underlying_proxy_age_seconds"),
        "min_order_total_cost": row.get("min_order_total_cost"),
        "reference_ladder_band": row.get("reference_ladder_band"),
        "profile_filter_name": row.get("profile_filter_name"),
        "failed_checks": failed,
        "manual_execution_ticket": ticket,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _evaluate_reference_account_watch(row: dict[str, Any], *, generated_at: datetime) -> dict[str, Any] | None:
    best_ask = _float(row.get("best_ask"))
    price_band = _reference_price_band(best_ask)
    if price_band is None:
        return None
    failed: list[str] = []
    quote_at = _timestamp(row.get("quote_at_utc"))
    quote_age = (generated_at - quote_at).total_seconds() if quote_at else None
    _check_min_max(
        failed,
        "time_remaining_seconds",
        _float(row.get("time_remaining_seconds")),
        REFERENCE_ACCOUNT_WATCH_CONFIG["time_remaining_seconds_min"],
        REFERENCE_ACCOUNT_WATCH_CONFIG["time_remaining_seconds_max"],
    )
    _check_max(failed, "spread", _float(row.get("spread")), REFERENCE_ACCOUNT_WATCH_CONFIG["spread_max"])
    _check_min(failed, "ask_size", _float(row.get("ask_size")), REFERENCE_ACCOUNT_WATCH_CONFIG["top_ask_size_min"])
    _check_min(
        failed,
        "depth_top3_ask_size",
        _float(row.get("depth_top3_ask_size")),
        REFERENCE_ACCOUNT_WATCH_CONFIG["depth_top3_ask_size_min"],
    )
    _check_max(failed, "monitor_quote_age_seconds", quote_age, REFERENCE_ACCOUNT_WATCH_CONFIG["quote_age_seconds_max"])
    _check_max(
        failed,
        "underlying_proxy_age_seconds",
        _float(row.get("underlying_proxy_age_seconds")),
        REFERENCE_ACCOUNT_WATCH_CONFIG["underlying_proxy_age_seconds_max"],
    )
    _check_max(
        failed,
        "min_order_total_cost",
        _float(row.get("min_order_total_cost")),
        REFERENCE_ACCOUNT_WATCH_CONFIG["max_total_budget_usd"],
    )
    return {
        "watch_id": REFERENCE_ACCOUNT_WATCH_CONFIG["watch_id"],
        "watch_only": True,
        "not_live_test_eligible": True,
        "source_strategy_id": row.get("strategy_id"),
        "event_slug": row.get("event_slug"),
        "symbol": row.get("symbol"),
        "outcome": row.get("outcome"),
        "token_id": row.get("token_id"),
        "quote_at_utc": row.get("quote_at_utc"),
        "monitor_quote_age_seconds": quote_age,
        "time_remaining_seconds": row.get("time_remaining_seconds"),
        "price_band": price_band,
        "best_ask": best_ask,
        "spread": row.get("spread"),
        "ask_size": row.get("ask_size"),
        "depth_top3_ask_size": row.get("depth_top3_ask_size"),
        "underlying_proxy_age_seconds": row.get("underlying_proxy_age_seconds"),
        "min_order_total_cost": row.get("min_order_total_cost"),
        "failed_checks": failed,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _reference_price_band(best_ask: float | None) -> str | None:
    if best_ask is None:
        return None
    for band in REFERENCE_ACCOUNT_WATCH_CONFIG["price_bands"]:
        lower = _float(band.get("best_ask_min"))
        upper = _float(band.get("best_ask_max"))
        if lower is None or upper is None:
            continue
        if lower <= best_ask <= upper:
            return str(band["name"])
    return None


def _dedupe_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = (
            row.get("strategy_id"),
            row.get("watch_id"),
            row.get("event_slug"),
            row.get("token_id"),
            row.get("outcome"),
            row.get("quote_at_utc"),
            row.get("best_ask"),
            row.get("spread"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _trade_ledger_state(payload: dict[str, Any] | None, *, budget: dict[str, Any]) -> dict[str, Any]:
    rows = (payload or {}).get("trades") or []
    closed = [row for row in rows if str(row.get("status") or "settled").lower() not in {"cancelled", "void", "skipped"}]
    losses = [row for row in closed if _is_loss_trade(row)]
    pnl_values = [_float(row.get("pnl_net") if row.get("pnl_net") is not None else row.get("pnl")) for row in closed]
    realized_pnl_net = sum(value for value in pnl_values if value is not None)
    net_realized_loss_usd = max(0.0, -realized_pnl_net)
    gross_realized_loss_usd = sum(abs(value) for value in pnl_values if value is not None and value < 0.0)
    hard_stop_loss_usd = _float(budget.get("hard_stop_loss_usd")) or 0.0
    return {
        "schema_version": "crypto_options_manual_trade_ledger_state_v1",
        "trade_count": int(len(closed)),
        "loss_count": int(len(losses)),
        "realized_pnl_net_usd": round(float(realized_pnl_net), 6),
        "net_realized_loss_usd": round(float(net_realized_loss_usd), 6),
        "gross_realized_loss_usd": round(float(gross_realized_loss_usd), 6),
        "max_test_trades_before_review": int(budget.get("max_test_trades_before_review") or 0),
        "hard_stop_full_losses": int(budget.get("hard_stop_full_losses") or 0),
        "hard_stop_loss_usd": round(float(hard_stop_loss_usd), 6),
        "remaining_trade_slots": max(0, int(budget.get("max_test_trades_before_review") or 0) - int(len(closed))),
        "remaining_losses_before_stop": max(0, int(budget.get("hard_stop_full_losses") or 0) - int(len(losses))),
        "remaining_net_loss_usd_before_stop": round(max(0.0, hard_stop_loss_usd - net_realized_loss_usd), 6),
    }


def _is_loss_trade(row: dict[str, Any]) -> bool:
    result = str(row.get("result") or row.get("outcome_result") or "").lower()
    if result in {"loss", "full_loss", "lost"}:
        return True
    pnl = _float(row.get("pnl_net") if row.get("pnl_net") is not None else row.get("pnl"))
    return pnl is not None and pnl < 0.0


def _manual_execution_ticket(row: dict[str, Any], *, budget: dict[str, Any]) -> dict[str, Any]:
    shares = _float(budget.get("max_position_shares")) or _float(budget.get("min_order_size_shares")) or 5.0
    cost = _float(row.get("min_order_total_cost"))
    max_cost = _float(budget.get("max_position_cost_usd"))
    return {
        "schema_version": "crypto_options_manual_execution_ticket_v1",
        "ticket_type": "manual_polymarket_micro_test",
        "event_slug": row.get("event_slug"),
        "strategy_id": row.get("strategy_id"),
        "symbol": row.get("symbol"),
        "outcome": row.get("outcome"),
        "token_id": row.get("token_id"),
        "shares": float(shares),
        "observed_best_ask": row.get("best_ask"),
        "estimated_total_cost_usd": cost,
        "max_total_cost_usd": max_cost,
        "cost_within_ticket_cap": bool(cost is not None and (max_cost is None or cost <= max_cost)),
        "quote_at_utc": row.get("quote_at_utc"),
        "orders_allowed": False,
        "live_trading_authorized": False,
        "boundary": "This is a manual execution ticket for review and ledger capture; it does not place, sign, broadcast, route, cancel, redeem, or authorize orders.",
    }


def _check_min_max(failed: list[str], name: str, value: float | None, min_value: Any, max_value: Any) -> None:
    _check_min(failed, name, value, min_value)
    _check_max(failed, name, value, max_value)


def _check_min(failed: list[str], name: str, value: float | None, min_value: Any) -> None:
    minimum = _float(min_value)
    if minimum is None:
        return
    if value is None or value < minimum:
        failed.append(f"{name}_below_min_or_missing")


def _check_max(failed: list[str], name: str, value: float | None, max_value: Any) -> None:
    maximum = _float(max_value)
    if maximum is None:
        return
    if value is None or value > maximum:
        failed.append(f"{name}_above_max_or_missing")


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _required_user_action(status: str, blockers: list[str]) -> str:
    if status == "eligible_manual_candidate_present":
        return "user_may_manually_review_candidate_on_polymarket_using_protocol; no automated_order_path_exists"
    if "manual_no_open_position_confirmation_missing" in blockers:
        return "confirm_no_open_crypto_options_micro_test_position_before_considering_any_manual_trade"
    return "wait_for_fresh_protocol_candidate_or_resolve_monitor_blockers"


__all__ = [
    "build_micro_test_monitor",
    "render_micro_test_monitor_markdown",
    "write_micro_test_monitor_artifacts",
]
