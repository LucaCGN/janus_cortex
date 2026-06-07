from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.feeds.polymarket_status import clob_trading_available
from crypto_options_app.trading.intents import ExecutionIntent
from crypto_options_app.trading.orders import OrderState
from crypto_options_app.workers.runtime_adapter import RuntimeScenario


LegacySubmitter = Callable[[dict[str, Any]], dict[str, Any]]
TradeProvider = Callable[[], Iterable[Any]]


@dataclass(frozen=True)
class PolymarketSupervisedExecutorConfig:
    operator: str
    reason: str
    order_type: str = "FOK"
    execution_style: str = "limit"
    price_slippage_cents: float = 0.0
    max_position_cost_usd: float = 5.0
    min_order_notional_usd: float = 1.0
    status_provider: Callable[[], dict[str, Any]] | None = None
    allow_status_unavailable_with_verified_market: bool = False


def build_polymarket_supervised_executor(
    config: PolymarketSupervisedExecutorConfig,
    *,
    submitter: LegacySubmitter | None = None,
    trade_provider: TradeProvider | None = None,
) -> Callable[[ExecutionIntent, OrderState, RuntimeScenario], dict[str, Any]]:
    actual_submitter = submitter or _legacy_live_micro_submitter

    def execute(intent: ExecutionIntent, order: OrderState, scenario: RuntimeScenario) -> dict[str, Any]:
        order_request = build_polymarket_order_request(intent=intent, order=order, scenario=scenario, config=config)
        if config.status_provider is not None:
            status_report = config.status_provider()
            if not clob_trading_available(status_report):
                if _status_unavailable_but_market_verified(status_report, scenario=scenario, config=config):
                    order_request["status_fallback_used"] = True
                    order_request["status_fallback_reason"] = "polymarket_status_unavailable_with_fresh_verified_market"
                else:
                    return {
                        "success": False,
                        "status": "unfilled",
                        "blockers": ["exchange_status_not_operational", *(status_report.get("blockers") or [])],
                        "exchange_status": status_report,
                        "order_request": order_request,
                        "filled_shares": 0.0,
                        "remote_filled_shares": 0.0,
                        "fill_price": order_request["price"],
                    }
            else:
                order_request["status_fallback_used"] = False
        submission = actual_submitter(order_request)
        submission = reconcile_ambiguous_submission_from_recent_trades(
            submission,
            order_request=order_request,
            trade_provider=trade_provider,
        )
        return normalize_polymarket_submission(submission, fallback_order=order, fallback_price=scenario.limit_price)

    return execute


def _status_unavailable_but_market_verified(
    status_report: dict[str, Any],
    *,
    scenario: RuntimeScenario,
    config: PolymarketSupervisedExecutorConfig,
) -> bool:
    if not config.allow_status_unavailable_with_verified_market:
        return False
    blockers = set(str(blocker) for blocker in (status_report.get("blockers") or []))
    if not blockers or not blockers <= {"polymarket_status_unavailable", "polymarket_status_timeout"}:
        return False
    quote_age = 9999.0 if scenario.quote_age_seconds is None else float(scenario.quote_age_seconds)
    limit_price = 0.0 if scenario.limit_price is None else float(scenario.limit_price)
    fresh_verified_quote = (
        bool(str(scenario.token_id or "").strip())
        and quote_age <= 15.0
        and 0.0 < limit_price < 1.0
    )
    if not scenario.live_market_verified and not fresh_verified_quote:
        return False
    if not fresh_verified_quote:
        return False
    return True


def build_polymarket_order_request(
    *,
    intent: ExecutionIntent,
    order: OrderState,
    scenario: RuntimeScenario,
    config: PolymarketSupervisedExecutorConfig,
) -> dict[str, Any]:
    token_id = str(scenario.token_id or scenario.event_token_key or "").strip()
    price = float(order.limit_price if order.limit_price is not None else scenario.limit_price)
    size = float(order.requested_shares)
    return {
        "schema_version": "crypto_options_app_supervised_order_request_v1",
        "idempotency_key": order.order_key,
        "event_slug": scenario.event_slug or scenario.event_key,
        "token_id": token_id,
        "outcome": scenario.outcome,
        "symbol": _symbol_from_event_key(scenario.event_key),
        "side": order.side.upper(),
        "price": price,
        "observed_best_ask": price if order.side.upper() == "BUY" else None,
        "observed_best_bid": max(0.0, price - float(scenario.spread)) if order.side.upper() == "BUY" else price,
        "observed_execution_price": price,
        "price_slippage_cents": float(config.price_slippage_cents),
        "size": size,
        "order_type": str(config.order_type or "FOK").upper(),
        "execution_style": str(config.execution_style or "limit").lower(),
        "operator": config.operator,
        "reason": config.reason,
        "strategy_ids": [intent.strategy_id],
        "source_quote_at_utc": datetime.now(UTC).isoformat(),
        "max_position_cost_usd": float(config.max_position_cost_usd),
        "min_order_notional_usd": float(config.min_order_notional_usd),
        "app_intent_key": intent.intent_key,
        "app_order_key": order.order_key,
        "app_run_id": intent.run_id,
    }


def normalize_polymarket_submission(
    submission: dict[str, Any],
    *,
    fallback_order: OrderState,
    fallback_price: float,
) -> dict[str, Any]:
    raw = submission.get("raw") if isinstance(submission.get("raw"), dict) else {}
    remote_order = submission.get("remote_order") if isinstance(submission.get("remote_order"), dict) else {}
    execution_quality = submission.get("execution_quality") if isinstance(submission.get("execution_quality"), dict) else {}
    order_request = submission.get("order_request") if isinstance(submission.get("order_request"), dict) else {}
    exchange_order_id = (
        submission.get("exchange_order_id")
        or remote_order.get("id")
        or remote_order.get("orderID")
        or remote_order.get("orderId")
        or raw.get("orderID")
        or raw.get("orderId")
        or raw.get("id")
        or fallback_order.exchange_order_id
        or fallback_order.order_key
    )
    status = _runtime_order_status(submission)
    filled_shares = _optional_float(submission.get("filled_shares"))
    if filled_shares is None:
        filled_shares = (
            _optional_float(execution_quality.get("filled_shares"))
            or
            _optional_float(
                remote_order.get("filled_shares")
                or remote_order.get("filledSize")
                or remote_order.get("filled_size")
                or remote_order.get("size_matched")
                or remote_order.get("sizeMatched")
                or remote_order.get("matched_amount")
                or remote_order.get("matchedAmount")
            )
            or _optional_float(raw.get("filled_shares") or raw.get("filledSize") or raw.get("size_matched"))
            or 0.0
        )
    if filled_shares > 0.0 and status == "submitted":
        status = "filled"
    fill_price = _optional_float(
        submission.get("fill_price")
        or execution_quality.get("realized_price")
        or remote_order.get("average_price")
        or remote_order.get("avgPrice")
        or remote_order.get("price")
        or raw.get("fill_price")
        or raw.get("price")
    )
    if fill_price is None:
        fill_price = _optional_float(order_request.get("price")) or float(fallback_price)
    remote_filled = _optional_float(submission.get("remote_filled_shares"))
    if remote_filled is None:
        remote_filled = filled_shares
    if (
        status == "submitted"
        and float(filled_shares) <= 0.0
        and str(order_request.get("order_type") or "").upper() in {"FOK", "FAK"}
    ):
        status = "unfilled"
    return {
        "exchange_order_id": str(exchange_order_id),
        "status": status,
        "filled_shares": float(filled_shares),
        "remote_filled_shares": float(remote_filled),
        "fill_price": float(fill_price),
        "legacy_submission": submission,
        "blockers": list(submission.get("blockers") or []),
        "exchange_status": submission.get("exchange_status") if isinstance(submission.get("exchange_status"), dict) else None,
    }


def reconcile_ambiguous_submission_from_recent_trades(
    submission: dict[str, Any],
    *,
    order_request: dict[str, Any],
    trade_provider: TradeProvider | None = None,
) -> dict[str, Any]:
    if not _is_ambiguous_submit_error(submission):
        return submission
    provider = trade_provider or _default_recent_trade_provider
    try:
        trades = list(provider())
    except Exception as exc:  # noqa: BLE001 - reconciliation failure should remain observable.
        enriched = dict(submission)
        enriched["post_error_reconciliation_error"] = f"{type(exc).__name__}:{exc}"
        return enriched
    match = _find_recent_trade_for_order_request(trades, order_request=order_request)
    if match is None:
        return submission
    enriched = dict(submission)
    enriched["success"] = True
    enriched["status"] = "submitted"
    enriched["exchange_order_id"] = match["exchange_order_id"]
    enriched["filled_shares"] = match["size"]
    enriched["remote_filled_shares"] = match["size"]
    enriched["fill_price"] = match["price"]
    enriched["remote_order"] = {
        "id": match["exchange_order_id"],
        "status": "FILLED",
        "filledSize": match["size"],
        "price": match["price"],
    }
    enriched["execution_quality"] = {
        "schema_version": "crypto_options_execution_quality_v1",
        "side": match["side"],
        "realized_price": match["price"],
        "realized_price_source": "post_error_wallet_trade_reconciliation",
        "filled_shares": match["size"],
        "filled_notional_usd": None if match["price"] is None or match["size"] is None else round(float(match["price"]) * float(match["size"]), 8),
    }
    enriched["post_error_reconciliation"] = {
        "status": "filled_trade_found",
        "source": "wallet_trade_history",
        "trade": match,
    }
    return enriched


def _legacy_live_micro_submitter(order_request: dict[str, Any]) -> dict[str, Any]:
    from app.data.pipelines.crypto.options.live_micro_executor import submit_live_micro_order

    return submit_live_micro_order(order_request)


def _default_recent_trade_provider() -> Iterable[Any]:
    from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials, view_trades

    return view_trades(PolymarketCredentials.from_env())


def _runtime_order_status(submission: dict[str, Any]) -> str:
    remote_order = submission.get("remote_order") if isinstance(submission.get("remote_order"), dict) else {}
    remote_status = str(remote_order.get("status") or remote_order.get("orderStatus") or "").lower()
    if remote_status in {"filled", "matched"}:
        return "filled"
    if remote_status in {"partially_filled", "partial", "partially matched", "partially_matched"}:
        return "partially_filled"
    if remote_status in {"cancelled", "canceled"}:
        return "cancelled"
    if remote_status in {"expired"}:
        return "expired"
    if bool(submission.get("success")):
        return "submitted"
    status = str(submission.get("status") or "submit_error")
    if status in {"blocked_fak_no_match", "blocked_jit_quote", "blocked_exchange_status"}:
        return "unfilled"
    if status.startswith("blocked_"):
        return "submit_error"
    if status in {"submitted", "accepted", "partially_filled", "filled", "unfilled", "rejected", "submit_error"}:
        return status
    return "submit_error"


def _is_ambiguous_submit_error(submission: dict[str, Any]) -> bool:
    if bool(submission.get("success")):
        return False
    status = str(submission.get("status") or "").lower()
    error = str(submission.get("error") or "").lower()
    if "timeout" in error or "timed out" in error:
        return True
    return status == "submit_error" and any(fragment in error for fragment in ("read operation", "connection", "temporarily"))


def _find_recent_trade_for_order_request(trades: Iterable[Any], *, order_request: dict[str, Any]) -> dict[str, Any] | None:
    token_id = str(order_request.get("token_id") or "").strip()
    side = str(order_request.get("side") or "").strip().upper()
    limit_price = _optional_float(order_request.get("price"))
    requested_size = _optional_float(order_request.get("size"))
    if not token_id or side not in {"BUY", "SELL"}:
        return None
    for trade in trades:
        normalized = _normalize_trade_like(trade)
        if normalized["token_id"] != token_id or normalized["side"] != side:
            continue
        if limit_price is not None and normalized["price"] is not None:
            if side == "BUY" and float(normalized["price"]) > float(limit_price) + 0.011:
                continue
            if side == "SELL" and float(normalized["price"]) < float(limit_price) - 0.011:
                continue
        if requested_size is not None and normalized["size"] is not None:
            if float(normalized["size"]) > float(requested_size) + 1e-6:
                continue
        return normalized
    return None


def _normalize_trade_like(trade: Any) -> dict[str, Any]:
    if hasattr(trade, "__dict__"):
        raw = dict(trade.__dict__)
    elif isinstance(trade, dict):
        raw = trade
    else:
        raw = {}
    return {
        "trade_id": raw.get("id") or raw.get("trade_id"),
        "exchange_order_id": str(raw.get("taker_order_id") or raw.get("order_id") or raw.get("orderID") or ""),
        "token_id": str(raw.get("asset_id") or raw.get("token_id") or ""),
        "side": str(raw.get("side") or "").upper(),
        "price": _optional_float(raw.get("price")),
        "size": _optional_float(raw.get("size")),
        "market": raw.get("market"),
        "timestamp": raw.get("timestamp") or raw.get("match_time"),
        "transaction_hash": raw.get("transaction_hash"),
    }


def _symbol_from_event_key(event_key: str) -> str | None:
    lowered = str(event_key or "").lower()
    for symbol in ("btc", "eth", "sol", "xrp"):
        if lowered.startswith(f"{symbol}-") or f"-{symbol}-" in lowered:
            return symbol.upper()
    return None


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
