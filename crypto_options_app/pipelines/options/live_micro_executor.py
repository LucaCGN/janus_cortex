from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any, Callable

from crypto_options_app.trading.polymarket_portfolio import PolymarketCredentials
from crypto_options_app.pipelines.options.profile_signals import resolve_crypto_updown_outcome
from crypto_options_app.pipelines.options.reporting import strict_jsonable


CRYPTO_OPTIONS_LIVE_MICRO_EXECUTOR_SCHEMA_VERSION = "crypto_options_live_micro_executor_v1"
CRYPTO_OPTIONS_LIVE_EXECUTION_LEDGER_SCHEMA_VERSION = "crypto_options_live_execution_ledger_v1"
_CASHOUT_SHARE_COVERAGE_EPSILON = 0.01

DEFAULT_STRATEGY_PRIORITY = {
    "profile_signal_btc_eth_5m_mid_high": 500,
    "profile_signal_btc_5m_only": 450,
    "profile_signal_btc_eth_5m_quality_favorite": 425,
    "profile_signal_btc_eth_5m_mid_high_quality_overlay": 350,
    "profile_late_btc_deep_60_120_validation": 300,
    "profile_mid_fade_lower_mid_validation": 200,
    "profile_mid_fade_60s_validation": 100,
}
USDC_BASE_UNITS = Decimal("1000000")
CENT_QUANTUM = Decimal("0.01")


def _load_py_clob_runtime() -> dict[str, Any]:
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import (
            ApiCreds,
            AssetType,
            BalanceAllowanceParams,
            MarketOrderArgs,
            OrderArgs,
            OrderType as ClobOrderType,
        )
        from py_clob_client_v2.constants import POLYGON
    except ModuleNotFoundError as exc:
        raise RuntimeError("py_clob_client_v2_missing") from exc
    return {
        "ApiCreds": ApiCreds,
        "AssetType": AssetType,
        "BalanceAllowanceParams": BalanceAllowanceParams,
        "ClobClient": ClobClient,
        "ClobOrderType": ClobOrderType,
        "MarketOrderArgs": MarketOrderArgs,
        "OrderArgs": OrderArgs,
        "POLYGON": POLYGON,
    }


@dataclass(frozen=True)
class LiveExecutorApproval:
    execute_live: bool = False
    execution_approved: bool = False
    acknowledge_live_risk: bool = False
    operator: str | None = None
    reason: str | None = None
    order_type: str = "FOK"
    price_slippage_cents: float = 0.0
    execution_style: str = "limit"
    execution_side: str = "BUY"

    @property
    def complete(self) -> bool:
        return bool(
            self.execute_live
            and self.execution_approved
            and self.acknowledge_live_risk
            and str(self.operator or "").strip()
            and str(self.reason or "").strip()
        )


def build_live_micro_execution_tick(
    *,
    protocol_payload: dict[str, Any],
    monitor_payload: dict[str, Any],
    ledger_payload: dict[str, Any] | None = None,
    approval: LiveExecutorApproval | None = None,
    now: datetime | None = None,
    submitter: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate and optionally submit one live micro-test order.

    This is intentionally separate from the read-only research monitor. It can
    submit only when all explicit live flags and credential checks pass.
    """

    now = now or datetime.now(timezone.utc)
    approval = approval or LiveExecutorApproval()
    ledger = normalize_live_execution_ledger(ledger_payload)
    ledger = reconcile_live_execution_ledger(ledger, now=now)
    protocol_budget = protocol_payload.get("budget") or {}
    risk_state = _ledger_risk_state(ledger, protocol_budget)
    blockers = _base_blockers(protocol_payload, monitor_payload, approval, risk_state)
    selected, candidate_blockers, all_candidate_rows = select_live_candidate(monitor_payload)
    blockers.extend(candidate_blockers)

    credential_status = inspect_polymarket_credentials()
    if not credential_status["ready"]:
        blockers.append("polymarket_execution_credentials_missing")

    if selected is not None:
        ticket_cost = _float(selected.get("min_order_total_cost"))
        if ticket_cost is None:
            blockers.append("selected_candidate_cost_missing")
        elif ticket_cost > float(protocol_budget.get("max_position_cost_usd") or 5.0) + 1e-9:
            blockers.append("selected_candidate_cost_above_max")
        elif _normalized_order_side(approval.execution_side) == "BUY":
            remaining_loss_budget = float(risk_state.get("remaining_net_loss_usd_before_stop") or 0.0)
            if ticket_cost > remaining_loss_budget + 1e-9:
                blockers.append("selected_candidate_cost_exceeds_remaining_loss_budget")
        planned_order_request = _order_request_from_candidate(selected, approval=approval)
        _attach_strategy_gate_to_order_request(planned_order_request, protocol_payload=protocol_payload, selected=selected)
        planned_order_request["max_position_cost_usd"] = float(protocol_budget.get("max_position_cost_usd") or 5.0)
        min_order_notional = _float(
            protocol_budget.get("min_order_notional_usd") or protocol_budget.get("min_position_cost_usd")
        )
        if min_order_notional is not None:
            planned_order_request["min_order_notional_usd"] = float(min_order_notional)
        planned_order_request["execution_style"] = str(approval.execution_style or "limit").lower()
        if _normalized_order_side(planned_order_request.get("side")) == "BUY":
            planned_order_cost = _estimated_order_total_cost(planned_order_request)
            if planned_order_cost is None:
                blockers.append("selected_candidate_execution_cost_missing")
            elif planned_order_cost > float(protocol_budget.get("max_position_cost_usd") or 5.0) + 1e-9:
                planned_order_request["pre_jit_execution_cost_usd"] = planned_order_cost
                planned_order_request["pre_jit_execution_cost_note"] = "above_max_before_jit_resize"
        elif not _ledger_has_matching_open_position(ledger, planned_order_request):
            blockers.append("sell_requires_matching_open_position")
    else:
        planned_order_request = None

    status = "blocked"
    submission: dict[str, Any] | None = None
    order_submission_attempted = False
    if selected is None:
        status = "no_candidate"
    elif blockers:
        status = "blocked"
    elif submitter is None:
        status = "ready_no_submitter"
    else:
        order_submission_attempted = True
        order_request = planned_order_request or _order_request_from_candidate(selected, approval=approval)
        submission = submitter(order_request)
        status = "submitted" if bool(submission.get("success")) else str(submission.get("status") or "submit_error")
        ledger = append_live_execution_ledger(
            ledger,
            build_ledger_entry(
                selected_candidate=selected,
                approval=approval,
                order_request=order_request,
                submission=submission,
                status=status,
                now=now,
            ),
        )

    return strict_jsonable(
        {
            "schema_version": CRYPTO_OPTIONS_LIVE_MICRO_EXECUTOR_SCHEMA_VERSION,
            "generated_at_utc": now.isoformat(),
            "status": status,
            "branch": "codex/crypto-options-research-module",
            "issue": 47,
            "approval": {
                "execute_live": approval.execute_live,
                "execution_approved": approval.execution_approved,
                "acknowledge_live_risk": approval.acknowledge_live_risk,
                "operator": approval.operator,
                "reason": approval.reason,
                "order_type": approval.order_type,
                "price_slippage_cents": approval.price_slippage_cents,
                "execution_style": approval.execution_style,
                "execution_side": approval.execution_side,
                "complete": approval.complete,
            },
            "execution_boundary": "live_order_submission_requires_all_flags_and_env_credentials",
            "orders_allowed": approval.complete and not blockers,
            "order_preparation_attempted": bool(selected is not None and approval.complete and not blockers),
            "order_submission_attempted": order_submission_attempted,
            "credential_status": credential_status,
            "risk_state": risk_state,
            "selected_candidate": selected,
            "planned_order_request": planned_order_request,
            "candidate_count": len(all_candidate_rows),
            "candidate_rows": all_candidate_rows,
            "submission": submission,
            "ledger": ledger,
            "blockers": blockers,
        }
    )


def select_live_candidate(monitor_payload: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    rows = [dict(row) for row in monitor_payload.get("eligible_manual_candidates") or [] if isinstance(row, dict)]
    if not rows:
        return None, ["no_eligible_manual_candidates"], []

    deduped = _dedupe_candidate_rows(rows)
    event_outcomes: dict[str, set[str]] = {}
    for row in deduped:
        event_outcomes.setdefault(str(row.get("event_slug") or ""), set()).add(str(row.get("outcome") or ""))
    conflicting_events = {event for event, outcomes in event_outcomes.items() if len(outcomes) > 1}
    eligible_rows = [row for row in deduped if str(row.get("event_slug") or "") not in conflicting_events]
    blockers = [f"conflicting_outcomes_same_event:{event}" for event in sorted(conflicting_events) if event]
    if not eligible_rows:
        return None, blockers or ["no_non_conflicting_candidates"], deduped

    selected = sorted(eligible_rows, key=_candidate_sort_key)[0]
    return selected, blockers, deduped


def _dedupe_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("event_slug") or ""),
            str(row.get("token_id") or ""),
            str(row.get("outcome") or ""),
        )
        existing = by_key.get(key)
        strategy_id = str(row.get("strategy_id") or "")
        if existing is None:
            cloned = dict(row)
            cloned["strategy_ids"] = [strategy_id] if strategy_id else []
            by_key[key] = cloned
            continue
        strategy_ids = set(existing.get("strategy_ids") or [])
        if strategy_id:
            strategy_ids.add(strategy_id)
        existing["strategy_ids"] = sorted(strategy_ids)
        existing_priority = _strategy_priority(existing)
        row_priority = _strategy_priority(row)
        if row_priority > existing_priority:
            merged = dict(row)
            merged["strategy_ids"] = sorted(strategy_ids)
            by_key[key] = merged
    return list(by_key.values())


def _candidate_sort_key(row: dict[str, Any]) -> tuple[float, float, float, str]:
    return (
        -_strategy_priority(row),
        _float(row.get("monitor_quote_age_seconds")) or 9999.0,
        _float(row.get("min_order_total_cost")) or 9999.0,
        str(row.get("token_id") or ""),
    )


def _strategy_priority(row: dict[str, Any]) -> float:
    strategy_ids = row.get("strategy_ids") if isinstance(row.get("strategy_ids"), list) else [row.get("strategy_id")]
    return max(DEFAULT_STRATEGY_PRIORITY.get(str(item), 0) for item in strategy_ids if item is not None)


def _base_blockers(
    protocol_payload: dict[str, Any],
    monitor_payload: dict[str, Any],
    approval: LiveExecutorApproval,
    risk_state: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    execution_side = _normalized_order_side(approval.execution_side)
    if protocol_payload.get("protocol_status") != "approved_protocol_ready_for_separate_execution_design":
        blockers.append("protocol_not_approved_for_execution_design")
    if monitor_payload.get("monitor_status") != "eligible_manual_candidate_present":
        blockers.append("monitor_has_no_current_eligible_candidate")
    if not approval.complete:
        blockers.append("explicit_live_execution_flags_missing")
    budget = protocol_payload.get("budget") if isinstance(protocol_payload.get("budget"), dict) else {}
    multi_entry_inventory_allowed = bool(budget.get("multi_entry_inventory_allowed"))
    if execution_side == "BUY" and risk_state.get("unarmed_open_position_count", 0) > 0 and not multi_entry_inventory_allowed:
        blockers.append("open_position_requires_reconciliation")
    if execution_side == "SELL" and risk_state.get("open_position_count", 0) <= 0:
        blockers.append("sell_requires_open_position_reconciliation")
    max_test_trades = int(risk_state.get("max_test_trades_before_review") or 0)
    if max_test_trades > 0 and risk_state.get("trade_count", 0) >= max_test_trades:
        blockers.append("max_test_trades_reached")
    hard_stop_full_losses = int(risk_state.get("hard_stop_full_losses") or 0)
    if hard_stop_full_losses > 0 and risk_state.get("loss_count", 0) >= hard_stop_full_losses:
        blockers.append("hard_stop_full_losses_reached")
    if risk_state.get("net_realized_loss_usd", 0.0) >= risk_state.get("hard_stop_loss_usd", 3.0):
        blockers.append("hard_stop_loss_usd_reached")
    return blockers


def inspect_polymarket_credentials() -> dict[str, Any]:
    try:
        creds = PolymarketCredentials.from_env()
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "error": str(exc), "missing": ["credential_load_failed"]}
    checks = {
        "wallet_address": bool(str(creds.wallet_address or "").strip()),
        "private_key": bool(str(creds.private_key or "").strip()),
        "api_key": bool(str(creds.api_key or "").strip()),
        "secret": bool(str(creds.secret or "").strip()),
        "passphrase": bool(str(creds.passphrase or "").strip()),
        "funder_address": bool(str(creds.funder_address or "").strip()),
    }
    missing = [key for key, ready in checks.items() if not ready]
    return {
        "ready": not missing,
        "missing": missing,
        "configured": checks,
        "chain_id": creds.chain_id,
        "clob_host": creds.clob_host,
        "signature_type": creds.signature_type,
    }


def submit_live_micro_order(order_request: dict[str, Any]) -> dict[str, Any]:
    """Submit one gated buy or sell order through py-clob-client-v2."""

    started = time.perf_counter()
    side = _normalized_order_side(order_request.get("side"))
    creds = PolymarketCredentials.from_env()
    try:
        clob = _load_py_clob_runtime()
    except RuntimeError as exc:
        return {
            "success": False,
            "status": "submit_error",
            "error_type": str(exc),
            "error": "py-clob-client-v2 is required for live crypto-options order submission",
            "latency": {"submit_total_ms": round((time.perf_counter() - started) * 1000.0, 3)},
        }
    ClobClient = clob["ClobClient"]
    ApiCreds = clob["ApiCreds"]
    ClobOrderType = clob["ClobOrderType"]
    MarketOrderArgs = clob["MarketOrderArgs"]
    OrderArgs = clob["OrderArgs"]
    POLYGON = clob["POLYGON"]
    client = ClobClient(
        host=creds.clob_host or "https://clob.polymarket.com",
        key=creds.private_key,
        chain_id=creds.chain_id or POLYGON,
        signature_type=creds.signature_type,
        funder=creds.funder_address or creds.wallet_address,
    )
    client.set_api_creds(
        ApiCreds(
            api_key=creds.api_key,
            api_secret=creds.secret,
            api_passphrase=creds.passphrase,
        )
    )
    jit_quote = refresh_order_request_from_clob(client, order_request)
    if not bool(jit_quote.get("ready")):
        return {
            "success": False,
            "status": "blocked_jit_quote",
            "jit_quote": jit_quote,
            "latency": {"submit_total_ms": round((time.perf_counter() - started) * 1000.0, 3)},
        }
    required_notional = float(order_request["price"]) * float(order_request["size"])
    asset_check_started = time.perf_counter()
    if side == "SELL":
        asset_check = fetch_clob_conditional_status_with_client(
            client,
            creds,
            token_id=str(order_request["token_id"]),
            required_shares=float(order_request["size"]),
        )
    else:
        asset_check = fetch_clob_collateral_status_with_client(
            client,
            creds,
            required_notional_usd=required_notional,
        )
    asset_check_ms = round((time.perf_counter() - asset_check_started) * 1000.0, 3)
    if not bool(asset_check.get("ready")):
        return {
            "success": False,
            "status": "blocked_asset_check",
            "asset_check": asset_check,
            "collateral": asset_check if side == "BUY" else None,
            "conditional_token": asset_check if side == "SELL" else None,
            "jit_quote": jit_quote,
            "order_request": order_request,
            "latency": {
                "asset_check_ms": asset_check_ms,
                "submit_total_ms": round((time.perf_counter() - started) * 1000.0, 3),
            },
        }
    order_type_name = str(order_request.get("order_type") or "FOK").upper()
    clob_order_type = getattr(ClobOrderType, order_type_name, ClobOrderType.FOK)
    execution_style = str(order_request.get("execution_style") or "limit").lower()
    post_started = time.perf_counter()
    try:
        if execution_style == "market":
            raw = client.create_and_post_market_order(
                MarketOrderArgs(
                    token_id=str(order_request["token_id"]),
                    amount=float(
                        order_request.get("market_amount_shares" if side == "SELL" else "market_amount_usd")
                        or (float(order_request["size"]) if side == "SELL" else required_notional)
                    ),
                    side=side,
                    price=float(order_request["price"]),
                    order_type=clob_order_type,
                    user_usdc_balance=float(asset_check.get("balance_usd") or 0.0) if side == "BUY" else 0.0,
                ),
                order_type=clob_order_type,
            )
        else:
            order_args = OrderArgs(
                token_id=str(order_request["token_id"]),
                price=float(order_request["price"]),
                size=float(order_request["size"]),
                side=side,
            )
            raw = client.create_and_post_order(order_args, order_type=clob_order_type)
    except Exception as exc:  # noqa: BLE001
        execution_quality = build_execution_quality(order_request, jit_quote=jit_quote, raw=None)
        status = "blocked_fak_no_match" if _is_fak_no_match_error(str(exc)) else "submit_error"
        return {
            "success": False,
            "status": status,
            "error": str(exc),
            "execution_style": execution_style,
            "order_type": order_type_name,
            "order_request": order_request,
            "jit_quote": jit_quote,
            "execution_quality": execution_quality,
            "asset_check": asset_check,
            "collateral": asset_check if side == "BUY" else None,
            "conditional_token": asset_check if side == "SELL" else None,
            "latency": {
                "asset_check_ms": asset_check_ms,
                "post_order_ms": round((time.perf_counter() - post_started) * 1000.0, 3),
                "submit_total_ms": round((time.perf_counter() - started) * 1000.0, 3),
            },
        }
    remote_order: dict[str, Any] | None = None
    remote_order_error: str | None = None
    raw_order_id = _raw_order_id(raw)
    if raw_order_id:
        try:
            fetched_remote = client.get_order(raw_order_id)
            remote_order = fetched_remote if isinstance(fetched_remote, dict) else {"raw": fetched_remote}
        except Exception as exc:  # noqa: BLE001
            remote_order_error = str(exc)
    execution_quality = build_execution_quality(order_request, jit_quote=jit_quote, raw=raw)
    return {
        "success": _submission_success(raw),
        "status": "submitted" if _submission_success(raw) else "submit_error",
        "execution_style": execution_style,
        "order_type": order_type_name,
        "order_request": order_request,
        "jit_quote": jit_quote,
        "execution_quality": execution_quality,
        "raw": raw,
        "remote_order": remote_order,
        "remote_order_error": remote_order_error,
        "asset_check": asset_check,
        "collateral": asset_check if side == "BUY" else None,
        "conditional_token": asset_check if side == "SELL" else None,
        "latency": {
            "asset_check_ms": asset_check_ms,
            "post_order_ms": round((time.perf_counter() - post_started) * 1000.0, 3),
            "submit_total_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
    }


submit_fok_buy_order = submit_live_micro_order


def build_execution_quality(order_request: dict[str, Any], *, jit_quote: dict[str, Any] | None, raw: Any) -> dict[str, Any]:
    """Measure signal-to-execution drift for the live micro-test.

    Positive side-adjusted slippage is worse than the signal. Negative values
    are price improvement versus the signal.
    """

    side = _normalized_order_side(order_request.get("side"))
    signal_price = _float(order_request.get("observed_execution_price"))
    submitted_limit_price = _float(order_request.get("price"))
    jit_best_execution_price = _float((jit_quote or {}).get("best_bid" if side == "SELL" else "best_ask"))
    realized = _realized_execution_from_raw(raw, side=side)
    realized_price = realized.get("realized_price")
    reference_price = realized_price if realized_price is not None else submitted_limit_price
    return strict_jsonable(
        {
            "schema_version": "crypto_options_execution_quality_v1",
            "side": side,
            "signal_price": signal_price,
            "signal_price_source": "monitor_candidate_observed_execution_price",
            "pre_jit_limit_price": _float(order_request.get("pre_jit_price")),
            "jit_best_execution_price": jit_best_execution_price,
            "submitted_limit_price": submitted_limit_price,
            "realized_price": realized_price,
            "realized_price_source": realized.get("source"),
            "filled_shares": realized.get("filled_shares"),
            "filled_notional_usd": realized.get("filled_notional_usd"),
            "target_max_shares": order_request.get("target_max_shares") or order_request.get("size"),
            "filled_share_excess": _filled_share_excess(
                realized.get("filled_shares"),
                order_request.get("target_max_shares") or order_request.get("size"),
            ),
            "signal_to_jit_delta": _price_delta(jit_best_execution_price, signal_price),
            "signal_to_jit_slippage_cents": _side_adjusted_slippage_cents(
                signal_price,
                jit_best_execution_price,
                side=side,
            ),
            "signal_to_submitted_limit_delta": _price_delta(submitted_limit_price, signal_price),
            "signal_to_submitted_limit_slippage_cents": _side_adjusted_slippage_cents(
                signal_price,
                submitted_limit_price,
                side=side,
            ),
            "signal_to_realized_delta": _price_delta(realized_price, signal_price),
            "signal_to_realized_slippage_cents": _side_adjusted_slippage_cents(
                signal_price,
                realized_price,
                side=side,
            ),
            "signal_to_reference_execution_delta": _price_delta(reference_price, signal_price),
            "signal_to_reference_execution_slippage_cents": _side_adjusted_slippage_cents(
                signal_price,
                reference_price,
                side=side,
            ),
            "quote_to_post_submit_latency_ms": (order_request.get("jit_quote_latency_ms") or (jit_quote or {}).get("latency_ms")),
        }
    )


def refresh_order_request_from_clob(client: ClobClient, order_request: dict[str, Any]) -> dict[str, Any]:
    """Refresh executable quote just before signing and mutate order_request.

    The monitor artifact is treated as a signal trigger only. This function
    replaces the submitted limit price with a fresh CLOB book-derived price
    under the configured slippage, strategy, and ticket-cost gates.
    """

    started = time.perf_counter()
    token_id = str(order_request.get("token_id") or "").strip()
    if not token_id:
        return {"ready": False, "reason": "missing_token_id"}
    try:
        raw_book = client.get_order_book(token_id)
    except Exception as exc:  # noqa: BLE001
        return {
            "ready": False,
            "reason": "jit_orderbook_fetch_failed",
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    quote = parse_clob_orderbook_quote(raw_book)
    quote["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    side = _normalized_order_side(order_request.get("side"))
    order_type_name = str(order_request.get("order_type") or "FOK").upper()
    execution_style = str(order_request.get("execution_style") or "limit").lower()
    resting_limit_sell = side == "SELL" and order_type_name in {"GTC", "GTD"}
    size = _float(order_request.get("size")) or 5.0
    target_max_shares = float(size)
    slippage_cents = _float(order_request.get("price_slippage_cents")) or 0.0
    if resting_limit_sell:
        target_price = _float(order_request.get("price"))
        if target_price is None:
            return {"ready": False, "reason": "jit_resting_sell_limit_price_missing", **quote}
        limit_price = round(min(0.99, max(0.01, float(target_price))), 4)
    else:
        price_field = "best_bid" if side == "SELL" else "best_ask"
        if quote.get(price_field) is None:
            return {"ready": False, "reason": f"jit_{price_field}_missing", **quote}
        limit_price = _execution_price_with_slippage(_float(quote.get(price_field)), slippage_cents=slippage_cents, side=side)
        if limit_price is None:
            return {"ready": False, "reason": "jit_limit_price_missing", **quote}

    max_best_ask = _float(order_request.get("strategy_best_ask_max"))
    if side == "BUY" and max_best_ask is not None and float(quote["best_ask"]) > max_best_ask + 1e-9:
        return {"ready": False, "reason": "jit_best_ask_above_strategy_gate", **quote}
    if side == "BUY" and _price_in_blocked_entry_buckets(_float(quote.get("best_ask")), order_request.get("strategy_blocked_entry_buckets")):
        return {"ready": False, "reason": "jit_best_ask_in_blocked_entry_bucket", **quote}
    min_best_bid = _float(order_request.get("strategy_best_bid_min"))
    if (
        side == "SELL"
        and not resting_limit_sell
        and min_best_bid is not None
        and float(quote["best_bid"]) < min_best_bid - 1e-9
    ):
        return {"ready": False, "reason": "jit_best_bid_below_strategy_gate", **quote}
    max_spread = _float(order_request.get("strategy_spread_max"))
    if max_spread is not None and _float(quote.get("spread")) is not None and float(quote["spread"]) > max_spread + 1e-9:
        return {"ready": False, "reason": "jit_spread_above_strategy_gate", **quote}
    depth_field = "depth_top3_bid_size" if side == "SELL" else "depth_top3_ask_size"
    min_depth = _float(order_request.get("strategy_depth_top3_bid_size_min" if side == "SELL" else "strategy_depth_top3_ask_size_min"))
    if min_depth is not None and _float(quote.get(depth_field)) is not None and float(quote[depth_field]) < min_depth:
        return {"ready": False, "reason": f"jit_{depth_field}_below_strategy_gate", **quote}

    fillable_size = _fillable_depth_at_limit(quote, side=side, limit_price=limit_price)
    quote["fillable_size_at_limit"] = round(fillable_size, 6)
    if fillable_size <= 0.0 and not resting_limit_sell:
        return {"ready": False, "reason": f"jit_no_{'bid' if side == 'SELL' else 'ask'}_depth_at_limit", **quote}

    max_cost = _float(order_request.get("max_position_cost_usd")) or 5.0
    configured_min_notional = _float(
        order_request.get("min_order_notional_usd") or order_request.get("min_position_cost_usd")
    )
    min_marketable_buy_notional = 1.0
    if side == "BUY" and configured_min_notional is not None:
        min_marketable_buy_notional = max(1.0, float(configured_min_notional))
        if min_marketable_buy_notional > float(max_cost) + 1e-9:
            return {
                "ready": False,
                "reason": "jit_min_order_notional_above_max_position_cost",
                "min_required_marketable_buy_notional_usd": min_marketable_buy_notional,
                "max_position_cost_usd": float(max_cost),
                **quote,
            }
    if side == "BUY":
        # Polymarket limit buys can receive more shares than the nominal size
        # when the order improves versus the submitted limit. Scale the order's
        # limit size to the JIT best ask so the realized fill stays close to
        # the intended 5-share ticket cap.
        jit_best_ask = _float(quote.get("best_ask"))
        if jit_best_ask is not None and float(limit_price) > float(jit_best_ask) + 1e-9:
            capped_size = float(size) * float(jit_best_ask) / float(limit_price)
            order_request["target_max_shares"] = target_max_shares
            order_request["size_before_share_cap"] = float(size)
            order_request["share_cap_basis_price"] = float(jit_best_ask)
            order_request["share_cap_submitted_limit_price"] = float(limit_price)
            order_request["share_cap_adjustment"] = "buy_limit_size_scaled_to_jit_best_ask"
            size = max(0.01, min(float(size), capped_size))
            size = _floor_limit_buy_size_to_clob_precision(size=size, price=limit_price)
            if size <= 0.0:
                return {"ready": False, "reason": "jit_no_valid_clob_buy_size_at_limit", **quote}
            order_request["share_cap_size_after_precision"] = size
            order_request["size"] = size
        else:
            size = _floor_float_to_decimals(size, 2)
            order_request["size"] = size
    else:
        size = _floor_float_to_decimals(size, 2)
        order_request["size"] = size
    estimated_cost = size * (limit_price + _taker_fee_per_share(limit_price))
    marketable_buy_notional = float(size) * float(limit_price)
    if side == "BUY" and marketable_buy_notional < min_marketable_buy_notional - 1e-9:
        max_affordable_size = float(max_cost) / float(limit_price)
        max_adjustable_size = min(float(fillable_size), max_affordable_size)
        if execution_style == "market":
            adjusted_size = min(max_adjustable_size, min_marketable_buy_notional / float(limit_price))
        else:
            adjusted_size = _ceil_limit_buy_size_to_min_notional(
                min_notional_usd=min_marketable_buy_notional,
                price=limit_price,
                max_size=max_adjustable_size,
            )
        adjusted_cost = adjusted_size * (limit_price + _taker_fee_per_share(limit_price))
        adjusted_notional = adjusted_size * float(limit_price)
        if (
            adjusted_size > float(size) + 1e-9
            and adjusted_notional >= min_marketable_buy_notional - 1e-9
            and adjusted_notional <= max_cost + 1e-9
        ):
            order_request["size_before_min_notional_adjustment"] = float(size)
            order_request["min_notional_basis_price"] = float(limit_price)
            order_request["min_notional_required_usd"] = min_marketable_buy_notional
            order_request["min_notional_max_affordable_size"] = round(max_affordable_size, 8)
            order_request["min_notional_adjustment"] = (
                "buy_limit_size_increased_to_strategy_min_notional"
                if configured_min_notional is not None
                else "buy_limit_size_increased_to_exchange_min_notional"
            )
            size = adjusted_size
            order_request["size"] = size
            order_request["min_notional_size_after_precision"] = size
            marketable_buy_notional = float(size) * float(limit_price)
            estimated_cost = adjusted_cost
        else:
            return {
                "ready": False,
                "reason": "jit_marketable_buy_notional_below_min",
                "estimated_total_cost_usd": marketable_buy_notional,
                "min_required_marketable_buy_notional_usd": min_marketable_buy_notional,
                "max_affordable_size": round(max_affordable_size, 8),
                "max_adjustable_size": round(max_adjustable_size, 8),
                **quote,
            }
    if side == "BUY" and marketable_buy_notional > max_cost + 1e-9:
        return {"ready": False, "reason": "jit_marketable_buy_notional_above_max", "estimated_total_cost_usd": marketable_buy_notional, **quote}

    order_request["pre_jit_price"] = order_request.get("price")
    order_request["price"] = limit_price
    order_request["jit_best_ask"] = quote.get("best_ask")
    order_request["jit_spread"] = quote.get("spread")
    order_request["jit_depth_top3_ask_size"] = quote.get("depth_top3_ask_size")
    order_request["jit_depth_top3_bid_size"] = quote.get("depth_top3_bid_size")
    order_request["jit_fillable_size_at_limit"] = quote.get("fillable_size_at_limit")
    order_request["jit_quote_latency_ms"] = quote.get("latency_ms")
    if side == "SELL":
        order_request["estimated_gross_proceeds_usd"] = round(float(size) * float(limit_price), 6)
        order_request["estimated_net_proceeds_usd"] = round(float(size) * (float(limit_price) - _taker_fee_per_share(limit_price)), 6)
        order_request["market_amount_shares"] = float(size)
    else:
        order_request["estimated_total_cost_usd"] = estimated_cost
        order_request["market_amount_usd"] = min(float(max_cost), float(size) * float(limit_price))
        order_request["marketable_buy_notional_usd"] = marketable_buy_notional
    return {
        "ready": True,
        "reason": None,
        "side": side,
        "submitted_limit_price": limit_price,
        "estimated_total_cost_usd": round(estimated_cost, 6) if side == "BUY" else None,
        "estimated_gross_proceeds_usd": round(float(size) * float(limit_price), 6) if side == "SELL" else None,
        **quote,
    }


def parse_clob_orderbook_quote(raw_book: Any) -> dict[str, Any]:
    asks = sorted(_book_levels(raw_book, "asks"), key=lambda item: item["price"])
    bids = sorted(_book_levels(raw_book, "bids"), key=lambda item: item["price"], reverse=True)
    best_ask = asks[0]["price"] if asks else None
    best_bid = bids[0]["price"] if bids else None
    spread = (best_ask - best_bid) if best_ask is not None and best_bid is not None else None
    return {
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
        "ask_size": asks[0]["size"] if asks else None,
        "bid_size": bids[0]["size"] if bids else None,
        "depth_top3_ask_size": round(sum(level["size"] for level in asks[:3]), 6) if asks else None,
        "depth_top3_bid_size": round(sum(level["size"] for level in bids[:3]), 6) if bids else None,
        "asks": asks[:10],
        "bids": bids[:10],
    }


def _book_levels(raw_book: Any, side: str) -> list[dict[str, float]]:
    levels = _raw_get(raw_book, side) or []
    parsed: list[dict[str, float]] = []
    for level in levels:
        price = _float(_raw_get(level, "price"))
        size = _float(_raw_get(level, "size"))
        if price is None or size is None:
            continue
        parsed.append({"price": price, "size": size})
    return parsed


def _raw_get(raw: Any, key: str) -> Any:
    if isinstance(raw, dict):
        return raw.get(key)
    return getattr(raw, key, None)


def _fillable_depth_at_limit(quote: dict[str, Any], *, side: str, limit_price: float) -> float:
    if side == "SELL":
        return float(
            sum(float(level.get("size") or 0.0) for level in quote.get("bids") or [] if float(level.get("price") or 0.0) >= limit_price - 1e-9)
        )
    return float(
        sum(float(level.get("size") or 0.0) for level in quote.get("asks") or [] if float(level.get("price") or 0.0) <= limit_price + 1e-9)
    )


def _clob_limit_size_float(size_cents: int) -> float:
    # py-clob-client floors size * 100 internally. A tiny positive offset
    # avoids binary float underflow turning 4.60 into 4.59 before signing.
    return round(float(size_cents) / 100.0 + 1e-9, 10)


def _floor_float_to_decimals(value: float, decimals: int) -> float:
    quant = Decimal("1").scaleb(-int(decimals))
    return float(Decimal(str(max(0.0, float(value)))).quantize(quant, rounding=ROUND_DOWN))


def _floor_limit_buy_size_to_clob_precision(*, size: float, price: float) -> float:
    """Return a buy limit size that keeps server-side amounts valid.

    The CLOB currently rejects buy orders when the generated USDC maker amount
    has more than cent precision. The client rounds 0.01-tick limit-order size
    to two decimals before multiplying by price, so use the largest two-decimal
    size under the requested cap whose submitted-limit notional is exact cents.
    """

    size_cents = int(Decimal(str(max(0.0, float(size)))) * Decimal("100"))
    price_cents = int((Decimal(str(float(price))) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_DOWN))
    if size_cents <= 0 or price_cents <= 0:
        return 0.0
    while size_cents > 0:
        if (price_cents * size_cents) % 100 == 0:
            return _clob_limit_size_float(size_cents)
        size_cents -= 1
    return 0.0


def _ceil_limit_buy_size_to_min_notional(*, min_notional_usd: float, price: float, max_size: float) -> float:
    """Return the smallest valid buy limit size that clears a notional floor."""

    price_cents = int((Decimal(str(float(price))) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_DOWN))
    max_size_cents = int(Decimal(str(max(0.0, float(max_size)))) * Decimal("100"))
    min_product = int((Decimal(str(float(min_notional_usd))) * Decimal("10000")).to_integral_value(rounding=ROUND_UP))
    if price_cents <= 0 or max_size_cents <= 0 or min_product <= 0:
        return 0.0
    size_cents = int((Decimal(min_product) / Decimal(price_cents)).to_integral_value(rounding=ROUND_UP))
    size_cents = max(1, size_cents)
    while size_cents <= max_size_cents:
        product = price_cents * size_cents
        if product >= min_product and product % 100 == 0:
            return _clob_limit_size_float(size_cents)
        size_cents += 1
    return 0.0


def fetch_clob_collateral_status_with_client(
    client: Any,
    creds: PolymarketCredentials,
    *,
    required_notional_usd: float = 0.0,
) -> dict[str, Any]:
    required = max(0.0, float(required_notional_usd))
    try:
        clob = _load_py_clob_runtime()
    except RuntimeError as exc:
        return {
            "ready": False,
            "reason": str(exc),
            "required_notional_usd": round(required, 4),
            "error": "py-clob-client-v2 is required for CLOB collateral checks",
        }
    BalanceAllowanceParams = clob["BalanceAllowanceParams"]
    AssetType = clob["AssetType"]
    try:
        raw = client.get_balance_allowance(
            BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=creds.signature_type,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ready": False,
            "reason": "clob_collateral_check_failed",
            "required_notional_usd": round(required, 4),
            "error": str(exc),
        }
    return _parse_clob_collateral_response(raw, required_notional_usd=required)


def fetch_clob_conditional_status_with_client(
    client: Any,
    creds: PolymarketCredentials,
    *,
    token_id: str,
    required_shares: float = 0.0,
) -> dict[str, Any]:
    required = max(0.0, float(required_shares))
    try:
        clob = _load_py_clob_runtime()
    except RuntimeError as exc:
        return {
            "ready": False,
            "reason": str(exc),
            "token_id": token_id,
            "required_shares": round(required, 4),
            "error": "py-clob-client-v2 is required for CLOB conditional token checks",
        }
    BalanceAllowanceParams = clob["BalanceAllowanceParams"]
    AssetType = clob["AssetType"]
    try:
        raw = client.get_balance_allowance(
            BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=creds.signature_type,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ready": False,
            "reason": "clob_conditional_check_failed",
            "token_id": token_id,
            "required_shares": round(required, 4),
            "error": str(exc),
        }
    parsed = _parse_clob_asset_response(raw, required_amount=required)
    parsed["token_id"] = token_id
    parsed["required_shares"] = round(required, 4)
    if parsed["reason"] == "clob_asset_balance_too_low":
        parsed["reason"] = "clob_conditional_balance_too_low"
    elif parsed["reason"] == "clob_asset_allowance_too_low":
        parsed["reason"] = "clob_conditional_allowance_too_low"
    return parsed


def fetch_clob_collateral_status(
    creds: PolymarketCredentials,
    *,
    required_notional_usd: float = 0.0,
) -> dict[str, Any]:
    """Return direct CLOB USDC balance/allowance readiness for one order.

    This is duplicated locally instead of importing the NBA execution adapter so
    the crypto-options live test lane remains isolated from NBA/WNBA runtime
    modules.
    """

    required = max(0.0, float(required_notional_usd))
    try:
        clob = _load_py_clob_runtime()
    except RuntimeError as exc:
        return {
            "ready": False,
            "reason": str(exc),
            "required_notional_usd": round(required, 4),
            "error": "py-clob-client-v2 is required for CLOB collateral checks",
        }
    ClobClient = clob["ClobClient"]
    ApiCreds = clob["ApiCreds"]
    AssetType = clob["AssetType"]
    BalanceAllowanceParams = clob["BalanceAllowanceParams"]
    POLYGON = clob["POLYGON"]
    try:
        client = ClobClient(
            host=creds.clob_host or "https://clob.polymarket.com",
            key=creds.private_key,
            chain_id=creds.chain_id or POLYGON,
            signature_type=creds.signature_type,
            funder=creds.funder_address or creds.wallet_address,
        )
        if creds.api_key and creds.secret and creds.passphrase:
            client.set_api_creds(
                ApiCreds(
                    api_key=creds.api_key,
                    api_secret=creds.secret,
                    api_passphrase=creds.passphrase,
                )
            )
        raw = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=creds.signature_type))
    except Exception as exc:  # noqa: BLE001
        return {
            "ready": False,
            "reason": "clob_collateral_check_failed",
            "required_notional_usd": round(required, 4),
            "error": str(exc),
        }
    return _parse_clob_collateral_response(raw, required_notional_usd=required)


def _parse_clob_collateral_response(raw: Any, *, required_notional_usd: float) -> dict[str, Any]:
    parsed = _parse_clob_asset_response(raw, required_amount=max(0.0, float(required_notional_usd)))
    return {
        "ready": parsed["ready"],
        "reason": {
            "clob_asset_balance_too_low": "clob_collateral_balance_too_low",
            "clob_asset_allowance_too_low": "clob_collateral_allowance_too_low",
        }.get(parsed["reason"], parsed["reason"]),
        "required_notional_usd": round(max(0.0, float(required_notional_usd)), 4),
        "balance_usd": parsed["balance"],
        "max_allowance_usd": parsed["max_allowance"],
        "allowances_usd": parsed["allowances"],
        "allowance_usd": parsed["allowance"],
    }


def _parse_clob_asset_response(raw: Any, *, required_amount: float) -> dict[str, Any]:
    required = max(0.0, float(required_amount))
    raw_balance = raw.get("balance") if isinstance(raw, dict) else None
    raw_allowances = raw.get("allowances") if isinstance(raw, dict) else None
    raw_allowance = raw.get("allowance") if isinstance(raw, dict) else None
    balance_usd = _parse_clob_usdc_base_units(raw_balance)
    allowance_usd_by_address = {
        str(address): _parse_clob_usdc_base_units(amount)
        for address, amount in dict(raw_allowances or {}).items()
    }
    allowance_values = [amount for amount in allowance_usd_by_address.values() if amount is not None]
    parsed_allowance = _parse_clob_usdc_base_units(raw_allowance)
    if parsed_allowance is not None:
        allowance_values.append(parsed_allowance)
    max_allowance_usd = max(allowance_values) if allowance_values else None
    balance_ok = balance_usd is not None and balance_usd + 0.000001 >= required
    allowance_ok = max_allowance_usd is not None and max_allowance_usd + 0.000001 >= required
    if not balance_ok:
        reason = "clob_asset_balance_too_low"
    elif not allowance_ok:
        reason = "clob_asset_allowance_too_low"
    else:
        reason = None
    return {
        "ready": bool(balance_ok and allowance_ok),
        "reason": reason,
        "required_amount": round(required, 4),
        "balance": round(balance_usd, 6) if balance_usd is not None else None,
        "max_allowance": round(max_allowance_usd, 6) if max_allowance_usd is not None else None,
        "allowances": {
            address: round(amount, 6) if amount is not None else None
            for address, amount in allowance_usd_by_address.items()
        },
        "allowance": round(parsed_allowance, 6) if parsed_allowance is not None else None,
    }


def _submission_success(raw: Any) -> bool:
    if isinstance(raw, dict):
        if raw.get("success") is False or raw.get("error"):
            return False
        if raw.get("orderID") or raw.get("orderId") or raw.get("id") or raw.get("transactionsHashes"):
            return True
    return bool(raw)


def _raw_order_id(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("orderID") or raw.get("orderId") or raw.get("id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_clob_usdc_base_units(value: Any) -> float | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return float(parsed / USDC_BASE_UNITS)


def _order_request_from_candidate(row: dict[str, Any], *, approval: LiveExecutorApproval) -> dict[str, Any]:
    ticket = row.get("manual_execution_ticket") if isinstance(row.get("manual_execution_ticket"), dict) else {}
    size = _float(ticket.get("shares")) or 5.0
    side = _normalized_order_side(ticket.get("execution_side") or row.get("execution_side") or approval.execution_side)
    observed_price = _float(
        ticket.get("observed_best_bid" if side == "SELL" else "observed_best_ask")
        or row.get("best_bid" if side == "SELL" else "best_ask")
    )
    price = _execution_price_with_slippage(
        observed_price,
        slippage_cents=_float(approval.price_slippage_cents) or 0.0,
        side=side,
    )
    token_id = str(ticket.get("token_id") or row.get("token_id") or "").strip()
    return {
        "schema_version": "crypto_options_live_micro_order_request_v1",
        "idempotency_key": _idempotency_key(row, size=size, price=price, side=side),
        "event_slug": row.get("event_slug"),
        "token_id": token_id,
        "outcome": row.get("outcome"),
        "symbol": row.get("symbol"),
        "side": side,
        "price": price,
        "observed_best_ask": row.get("best_ask"),
        "observed_best_bid": row.get("best_bid"),
        "observed_execution_price": observed_price,
        "price_slippage_cents": _float(approval.price_slippage_cents) or 0.0,
        "size": size,
        "order_type": str(approval.order_type or "FOK").upper(),
        "operator": approval.operator,
        "reason": approval.reason,
        "strategy_ids": row.get("strategy_ids") or [row.get("strategy_id")],
        "lane_id": row.get("lane_id"),
        "lane_decision_id": row.get("lane_decision_id"),
        "source_quote_at_utc": row.get("quote_at_utc"),
    }


def _attach_strategy_gate_to_order_request(
    order_request: dict[str, Any],
    *,
    protocol_payload: dict[str, Any],
    selected: dict[str, Any],
) -> None:
    strategy_ids = selected.get("strategy_ids") if isinstance(selected.get("strategy_ids"), list) else [selected.get("strategy_id")]
    entry_gates = protocol_payload.get("entry_gates") or [protocol_payload.get("entry_gate") or {}]
    matching = [
        gate
        for gate in entry_gates
        if str(gate.get("strategy_id") or "") in {str(strategy_id) for strategy_id in strategy_ids if strategy_id}
    ]
    if not matching:
        return
    best_ask_values = [_float(gate.get("best_ask_max")) for gate in matching if _float(gate.get("best_ask_max")) is not None]
    if best_ask_values:
        order_request["strategy_best_ask_max"] = min(best_ask_values)
    best_bid_values = [_float(gate.get("best_bid_min")) for gate in matching if _float(gate.get("best_bid_min")) is not None]
    if best_bid_values:
        order_request["strategy_best_bid_min"] = max(best_bid_values)
    spread_values = [_float(gate.get("spread_max")) for gate in matching if _float(gate.get("spread_max")) is not None]
    if spread_values:
        order_request["strategy_spread_max"] = min(spread_values)
    depth_values = [
        _float(gate.get("depth_top3_ask_size_min"))
        for gate in matching
        if _float(gate.get("depth_top3_ask_size_min")) is not None
    ]
    if depth_values:
        order_request["strategy_depth_top3_ask_size_min"] = max(depth_values)
    bid_depth_values = [
        _float(gate.get("depth_top3_bid_size_min"))
        for gate in matching
        if _float(gate.get("depth_top3_bid_size_min")) is not None
    ]
    if bid_depth_values:
        order_request["strategy_depth_top3_bid_size_min"] = max(bid_depth_values)
    blocked_buckets: list[dict[str, float]] = []
    for gate in matching:
        for bucket in gate.get("blocked_entry_buckets") or []:
            if not isinstance(bucket, dict):
                continue
            low = _float(bucket.get("min"))
            high = _float(bucket.get("max"))
            if low is None or high is None:
                continue
            blocked_buckets.append({"min": low, "max": high})
    if blocked_buckets:
        order_request["strategy_blocked_entry_buckets"] = blocked_buckets


def _price_in_blocked_entry_buckets(price: float | None, buckets: Any) -> bool:
    if price is None:
        return False
    for bucket in buckets or []:
        if not isinstance(bucket, dict):
            continue
        low = _float(bucket.get("min"))
        high = _float(bucket.get("max"))
        if low is None or high is None:
            continue
        if float(low) <= float(price) < float(high):
            return True
    return False


def _idempotency_key(row: dict[str, Any], *, size: float, price: float | None, side: str = "BUY") -> str:
    strategy_ids = row.get("strategy_ids")
    if not isinstance(strategy_ids, list):
        strategy_ids = [row.get("strategy_id")]
    payload = {
        "lane_id": row.get("lane_id"),
        "candidate_id": row.get("candidate_id"),
        "strategy_ids": [str(strategy_id) for strategy_id in strategy_ids if strategy_id],
        "event_slug": row.get("event_slug"),
        "token_id": row.get("token_id"),
        "outcome": row.get("outcome"),
        "side": _normalized_order_side(side),
        "size": size,
        "price": price,
        "quote_at_utc": row.get("quote_at_utc"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]


def _execution_price_with_slippage(price: float | None, *, slippage_cents: float, side: str = "BUY") -> float | None:
    if price is None:
        return None
    direction = -1.0 if _normalized_order_side(side) == "SELL" else 1.0
    return round(min(0.99, max(0.01, float(price) + direction * max(0.0, float(slippage_cents)) / 100.0)), 4)


def _estimated_order_total_cost(order_request: dict[str, Any] | None) -> float | None:
    if not order_request:
        return None
    price = _float(order_request.get("price"))
    size = _float(order_request.get("size"))
    if price is None or size is None:
        return None
    if _normalized_order_side(order_request.get("side")) == "SELL":
        return 0.0
    return float(size) * (float(price) + _taker_fee_per_share(float(price)))


def _taker_fee_per_share(price: float) -> float:
    return 0.07 * float(price) * (1.0 - float(price))


def normalize_live_execution_ledger(payload: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("schema_version") == CRYPTO_OPTIONS_LIVE_EXECUTION_LEDGER_SCHEMA_VERSION:
        ledger = dict(payload)
        ledger.setdefault("entries", [])
        return ledger
    return {
        "schema_version": CRYPTO_OPTIONS_LIVE_EXECUTION_LEDGER_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "entries": [],
    }


def reconcile_live_execution_ledger(
    ledger: dict[str, Any],
    *,
    now: datetime | None = None,
    resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mark open hold-to-settlement entries as settled when Gamma has a resolved outcome."""

    now = now or datetime.now(timezone.utc)
    resolver = resolver or resolve_crypto_updown_outcome
    updated = normalize_live_execution_ledger(ledger)
    entries: list[dict[str, Any]] = []
    outcome_cache: dict[str, dict[str, Any]] = {}
    changed = False
    for raw_entry in updated.get("entries") or []:
        entry = dict(raw_entry) if isinstance(raw_entry, dict) else {}
        if entry.get("settlement_status") != "open_requires_reconciliation":
            entries.append(entry)
            continue
        event_slug = str(entry.get("event_slug") or "")
        if not event_slug:
            entries.append(entry)
            continue
        if event_slug not in outcome_cache:
            try:
                outcome_cache[event_slug] = resolver(event_slug)
            except Exception as exc:  # noqa: BLE001 - reconciliation should not block future inspection.
                outcome_cache[event_slug] = {"status": "fetch_error", "error": f"{type(exc).__name__}:{exc}"}
        resolved = outcome_cache[event_slug]
        if resolved.get("status") != "resolved":
            entry["last_reconciliation_status"] = resolved.get("status")
            entry["last_reconciliation_at_utc"] = now.isoformat()
            entries.append(entry)
            changed = True
            continue
        realized = _settlement_pnl_for_entry(entry, resolved_outcome=str(resolved.get("resolved_outcome") or ""))
        entry.update(realized)
        entry["settlement_status"] = "settled"
        entry["settlement_source"] = resolved.get("source")
        entry["settlement_reconciled_at_utc"] = now.isoformat()
        entries.append(entry)
        changed = True
    if changed:
        updated["entries"] = entries
        updated["updated_at_utc"] = now.isoformat()
    return updated


def _settlement_pnl_for_entry(entry: dict[str, Any], *, resolved_outcome: str) -> dict[str, Any]:
    expected = str(entry.get("outcome") or "")
    execution_quality = entry.get("execution_quality") if isinstance(entry.get("execution_quality"), dict) else {}
    price = _float(execution_quality.get("realized_price")) or _float(entry.get("price")) or 0.0
    requested_size = _float(entry.get("size")) or 0.0
    filled_size = _float(execution_quality.get("filled_shares")) or requested_size
    fee = _taker_fee_per_share(price)
    win = expected == resolved_outcome
    pnl = float(filled_size) * ((1.0 - price - fee) if win else -(price + fee))
    return {
        "resolved_outcome": resolved_outcome,
        "settlement_win": win,
        "settled_size": round(float(filled_size), 8),
        "settlement_price": price,
        "settlement_fee_per_share": fee,
        "realized_pnl_net_usd": round(pnl, 8),
    }


def append_live_execution_ledger(ledger: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    updated = normalize_live_execution_ledger(ledger)
    entries = [dict(item) for item in updated.get("entries") or [] if isinstance(item, dict)]
    entry_key = entry.get("idempotency_key")
    replaced = False
    if entry_key:
        for index, item in enumerate(entries):
            if item.get("idempotency_key") != entry_key:
                continue
            if _ledger_entry_should_replace_existing(item, entry):
                entries[index] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    updated["entries"] = entries
    updated["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return updated


def _ledger_entry_should_replace_existing(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Promote a retried ledger row when a prior blocked attempt later submits.

    Candidate packets reuse idempotency keys across retry attempts for the same
    decision. The first attempt can be a JIT/FOK block and a later attempt can
    submit successfully; keeping the first row would hide live exposure from
    the service guard and correlated-entry caps.
    """

    existing_status = str(existing.get("status") or "").lower()
    incoming_status = str(incoming.get("status") or "").lower()
    if incoming_status == "submitted" and existing_status != "submitted":
        return True
    if incoming_status == "submitted" and str(existing.get("settlement_status") or "") == "not_open":
        return True
    if existing_status.startswith("blocked") and incoming_status.startswith("blocked"):
        return True
    return False


def build_ledger_entry(
    *,
    selected_candidate: dict[str, Any],
    approval: LiveExecutorApproval,
    order_request: dict[str, Any],
    submission: dict[str, Any],
    status: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_live_execution_ledger_entry_v1",
        "recorded_at_utc": now.isoformat(),
        "status": status,
        "idempotency_key": order_request.get("idempotency_key"),
        "event_slug": selected_candidate.get("event_slug"),
        "token_id": selected_candidate.get("token_id"),
        "symbol": selected_candidate.get("symbol"),
        "outcome": selected_candidate.get("outcome"),
        "strategy_ids": selected_candidate.get("strategy_ids") or [selected_candidate.get("strategy_id")],
        "lane_id": selected_candidate.get("lane_id") or order_request.get("lane_id"),
        "lane_decision_id": selected_candidate.get("lane_decision_id") or order_request.get("lane_decision_id"),
        "side": order_request.get("side"),
        "size": order_request.get("size"),
        "target_max_shares": order_request.get("target_max_shares") or order_request.get("size"),
        "size_before_share_cap": order_request.get("size_before_share_cap"),
        "share_cap_adjustment": order_request.get("share_cap_adjustment"),
        "price": order_request.get("price"),
        "estimated_total_cost_usd": order_request.get("estimated_total_cost_usd") or selected_candidate.get("min_order_total_cost"),
        "estimated_gross_proceeds_usd": order_request.get("estimated_gross_proceeds_usd"),
        "source_signal_attribution": selected_candidate.get("source_signal_attribution"),
        "v3_profile_support_summary": selected_candidate.get("v3_profile_support_summary"),
        "v3_bucket_label": selected_candidate.get("v3_bucket_label"),
        "v3_size_multiplier": selected_candidate.get("v3_size_multiplier"),
        "entry_cashout_target": selected_candidate.get("entry_cashout_target"),
        "supporting_profiles": selected_candidate.get("supporting_profiles"),
        "support_weight": selected_candidate.get("support_weight") or selected_candidate.get("signal_weight"),
        "conflict_weight": selected_candidate.get("conflict_weight"),
        "conflict_ratio": selected_candidate.get("conflict_ratio"),
        "execution_quality": submission.get("execution_quality"),
        "operator": approval.operator,
        "reason": approval.reason,
        "submission": submission,
        "settlement_status": _ledger_settlement_status(order_request, status),
        "realized_pnl_net_usd": None,
    }


def _ledger_settlement_status(order_request: dict[str, Any], status: str) -> str:
    if status != "submitted":
        return "not_open"
    if _normalized_order_side(order_request.get("side")) == "SELL":
        return "exit_submitted_requires_reconciliation"
    return "open_requires_reconciliation"


def _ledger_has_matching_open_position(ledger: dict[str, Any], order_request: dict[str, Any]) -> bool:
    token_id = str(order_request.get("token_id") or "")
    event_slug = str(order_request.get("event_slug") or "")
    for entry in ledger.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("settlement_status") != "open_requires_reconciliation":
            continue
        if token_id and str(entry.get("token_id") or "") != token_id:
            continue
        if event_slug and str(entry.get("event_slug") or "") != event_slug:
            continue
        return True
    return False


def _ledger_has_submitted_exit_for_entry(ledger: dict[str, Any], entry: dict[str, Any]) -> bool:
    return _ledger_cashout_coverage_for_entry(ledger, entry)["uncovered_shares"] <= _CASHOUT_SHARE_COVERAGE_EPSILON


def _ledger_cashout_coverage_for_entry(ledger: dict[str, Any], entry: dict[str, Any]) -> dict[str, float]:
    token_id = str(entry.get("token_id") or "")
    event_slug = str(entry.get("event_slug") or "")
    if not token_id and not event_slug:
        return {"open_buy_shares": 1.0, "submitted_sell_shares": 0.0, "uncovered_shares": 1.0}
    open_buy_shares = 0.0
    submitted_sell_shares = 0.0
    for row in ledger.get("entries") or []:
        if not isinstance(row, dict):
            continue
        if token_id and str(row.get("token_id") or "") != token_id:
            continue
        if event_slug and str(row.get("event_slug") or "") != event_slug:
            continue
        side = _normalized_order_side(row.get("side") or "BUY")
        if side == "BUY" and row.get("settlement_status") == "open_requires_reconciliation":
            open_buy_shares += _ledger_entry_shares(row)
        if (
            side == "SELL"
            and row.get("status") == "submitted"
            and row.get("settlement_status") == "exit_submitted_requires_reconciliation"
        ):
            submitted_sell_shares += _ledger_entry_shares(row)
    uncovered = max(0.0, open_buy_shares - submitted_sell_shares)
    if uncovered <= _CASHOUT_SHARE_COVERAGE_EPSILON:
        uncovered = 0.0
    return {
        "open_buy_shares": round(open_buy_shares, 8),
        "submitted_sell_shares": round(submitted_sell_shares, 8),
        "uncovered_shares": round(uncovered, 8),
    }


def _ledger_entry_shares(entry: dict[str, Any]) -> float:
    quality = entry.get("execution_quality") if isinstance(entry.get("execution_quality"), dict) else {}
    return _float(quality.get("filled_shares")) or _float(entry.get("size")) or 1.0


def _realized_execution_from_raw(raw: Any, *, side: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"source": None, "realized_price": None, "filled_shares": None, "filled_notional_usd": None}
    making = _float(raw.get("makingAmount"))
    taking = _float(raw.get("takingAmount"))
    if making is None or taking is None or making <= 0.0 or taking <= 0.0:
        return {"source": None, "realized_price": None, "filled_shares": None, "filled_notional_usd": None}
    if _normalized_order_side(side) == "SELL":
        return {
            "source": "clob_raw_takingAmount_div_makingAmount_sell",
            "realized_price": round(taking / making, 8),
            "filled_shares": round(making, 8),
            "filled_notional_usd": round(taking, 8),
        }
    return {
        "source": "clob_raw_makingAmount_div_takingAmount_buy",
        "realized_price": round(making / taking, 8),
        "filled_shares": round(taking, 8),
        "filled_notional_usd": round(making, 8),
    }


def _price_delta(execution_price: float | None, signal_price: float | None) -> float | None:
    if execution_price is None or signal_price is None:
        return None
    return round(float(execution_price) - float(signal_price), 8)


def _side_adjusted_slippage_cents(signal_price: float | None, execution_price: float | None, *, side: str) -> float | None:
    if signal_price is None or execution_price is None:
        return None
    delta = float(execution_price) - float(signal_price)
    if _normalized_order_side(side) == "SELL":
        delta = -delta
    return round(delta * 100.0, 4)


def _is_fak_no_match_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return (
        "no orders found to match with fak order" in lowered
        or "fok orders are fully filled or killed" in lowered
        or "order couldn't be fully filled" in lowered
    )


def _filled_share_excess(filled_shares: float | None, target_shares: float | None) -> float | None:
    if filled_shares is None or target_shares is None:
        return None
    return round(max(0.0, float(filled_shares) - float(target_shares)), 8)


def _ledger_risk_state(ledger: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    entries = [item for item in ledger.get("entries") or [] if isinstance(item, dict)]
    submitted_entries = [
        item
        for item in entries
        if item.get("status") == "submitted" or ("status" not in item and item.get("settlement_status") != "not_open")
    ]
    active = [item for item in submitted_entries if item.get("settlement_status") == "open_requires_reconciliation"]
    unarmed_active = [item for item in active if not _ledger_has_submitted_exit_for_entry(ledger, item)]
    realized = [item for item in submitted_entries if item.get("realized_pnl_net_usd") is not None]
    net_pnl = sum(float(item.get("realized_pnl_net_usd") or 0.0) for item in realized)
    losses = [item for item in realized if float(item.get("realized_pnl_net_usd") or 0.0) < 0.0]
    hard_stop_loss_usd = float(budget.get("hard_stop_loss_usd") or 3.0)
    return {
        "attempt_count": len(entries),
        "trade_count": len(submitted_entries),
        "open_position_count": len(active),
        "unarmed_open_position_count": len(unarmed_active),
        "realized_trade_count": len(realized),
        "loss_count": len(losses),
        "realized_pnl_net_usd": round(net_pnl, 6),
        "net_realized_loss_usd": round(max(0.0, -net_pnl), 6),
        "max_test_trades_before_review": _int_budget_value(budget, "max_test_trades_before_review", default=20),
        "hard_stop_full_losses": _int_budget_value(budget, "hard_stop_full_losses", default=3),
        "hard_stop_loss_usd": hard_stop_loss_usd,
        "remaining_net_loss_usd_before_stop": round(max(0.0, hard_stop_loss_usd - max(0.0, -net_pnl)), 6),
    }


def _int_budget_value(budget: dict[str, Any], key: str, *, default: int) -> int:
    if key not in budget:
        return default
    try:
        return int(budget.get(key) or 0)
    except (TypeError, ValueError):
        return default


def load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def write_json_object(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(strict_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def latest_monitor_artifact(loop_dir: str | Path) -> Path | None:
    root = Path(loop_dir)
    candidates = sorted(
        [
            *root.glob("crypto_options_profile_signal_monitor_*.json"),
            *root.glob("crypto_options_micro_test_monitor_*.json"),
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def env_live_flags_summary() -> dict[str, bool]:
    return {
        "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE": os.getenv("JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE") == "1",
        "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED": os.getenv("JANUS_CRYPTO_OPTIONS_LIVE_APPROVED") == "1",
        "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK": os.getenv("JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK") == "1",
    }


def _normalized_order_side(value: Any) -> str:
    side = str(value or "BUY").strip().upper()
    if side not in {"BUY", "SELL"}:
        return "BUY"
    return side
