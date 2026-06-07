"""Price-memory and grid/scalp validation primitives for global portfolio slots.

These helpers are pure analysis surfaces. They parse observed Polymarket price
points, summarize volatility windows, and run an inert grid-cycle backtest.
They never prepare, submit, cancel, replace, or place orders.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT

PRICE_MEMORY_FEATURE_SCHEMA_VERSION = "portfolio_price_memory_features_v1"
PRICE_MEMORY_COLLECTION_SCHEMA_VERSION = "portfolio_price_memory_collection_v1"
GRID_BACKTEST_SCHEMA_VERSION = "portfolio_grid_backtest_v1"
GRID_VALIDATION_SCHEMA_VERSION = "portfolio_grid_scalp_validation_v1"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_PUBLIC_HEADERS = {
    "User-Agent": "Janus global portfolio price-memory collector (read-only)",
    "Accept": "application/json,text/plain,*/*",
}


@dataclass(frozen=True)
class PortfolioPricePoint:
    timestamp_utc: datetime
    price: Decimal


def build_price_memory_features(
    price_history: dict[str, Any] | list[Any],
    *,
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Build 1d/7d/30d volatility and trend features from price history."""

    generated_at = _coerce_utc(now_utc)
    points = _parse_price_points(price_history)
    latest_at = points[-1].timestamp_utc if points else generated_at
    latest_price = points[-1].price if points else None
    windows = {
        "1d": _window_features(points, latest_at=latest_at, days=1),
        "7d": _window_features(points, latest_at=latest_at, days=7),
        "30d": _window_features(points, latest_at=latest_at, days=30),
    }
    return {
        "schema_version": PRICE_MEMORY_FEATURE_SCHEMA_VERSION,
        "generated_at_utc": _iso(generated_at),
        "latest_point_at_utc": _iso(latest_at) if points else None,
        "point_count": len(points),
        "latest_price": _decimal_str(latest_price),
        "windows": windows,
        "source_caveats": _source_caveats(price_history, points),
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def simulate_grid_backtest(
    price_history: dict[str, Any] | list[Any],
    *,
    grid_step_price: Decimal | float | str = "0.01",
    leg_size: Decimal | float | str = "5",
    starting_inventory: Decimal | float | str | None = None,
    slippage_cents: Decimal | float | str = "0",
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Run a deterministic inert grid-cycle backtest over observed prices."""

    generated_at = _coerce_utc(now_utc)
    points = _parse_price_points(price_history)
    step = _positive_decimal(grid_step_price, default=Decimal("0.01"))
    size = _positive_decimal(leg_size, default=Decimal("5"))
    inventory = _positive_decimal(starting_inventory, default=size) if starting_inventory is not None else size
    slippage_price = (_decimal(slippage_cents) or Decimal("0")) / Decimal("100")

    events: list[dict[str, Any]] = []
    completed_cycles = 0
    gross_return = Decimal("0")
    if points:
        anchor = points[0].price
        open_sell_prices: list[Decimal] = []
        open_buy_prices: list[Decimal] = []
        for point in points[1:]:
            price = point.price
            while price >= anchor + step:
                trade_price = (anchor + step).quantize(Decimal("0.0001"))
                if open_buy_prices:
                    buy_price = open_buy_prices.pop(0)
                    cycle_return = (trade_price - buy_price) * size
                    gross_return += cycle_return
                    completed_cycles += 1
                    action = "sell_to_close_grid_buy"
                elif inventory >= size:
                    inventory -= size
                    open_sell_prices.append(trade_price)
                    cycle_return = Decimal("0")
                    action = "sell_existing_inventory_preview"
                else:
                    cycle_return = Decimal("0")
                    action = "sell_skipped_no_inventory"
                events.append(
                    _event_payload(
                        action=action,
                        point=point,
                        price=trade_price,
                        size=size,
                        cycle_return=cycle_return,
                    )
                )
                anchor = trade_price

            while price <= anchor - step:
                trade_price = (anchor - step).quantize(Decimal("0.0001"))
                if open_sell_prices:
                    sell_price = open_sell_prices.pop(0)
                    cycle_return = (sell_price - trade_price) * size
                    gross_return += cycle_return
                    completed_cycles += 1
                    inventory += size
                    action = "buy_to_close_grid_sell"
                else:
                    open_buy_prices.append(trade_price)
                    inventory += size
                    cycle_return = Decimal("0")
                    action = "buy_grid_leg_preview"
                events.append(
                    _event_payload(
                        action=action,
                        point=point,
                        price=trade_price,
                        size=size,
                        cycle_return=cycle_return,
                    )
                )
                anchor = trade_price

    estimated_cost = slippage_price * size * Decimal(len(events))
    net_return = gross_return - estimated_cost
    status = "positive_after_costs" if completed_cycles > 0 and net_return > 0 else "not_profitable"
    if not points:
        status = "blocked_missing_price_points"
    return {
        "schema_version": GRID_BACKTEST_SCHEMA_VERSION,
        "generated_at_utc": _iso(generated_at),
        "status": status,
        "grid_step_price": _decimal_str(step),
        "grid_step_cents": _decimal_str(step * Decimal("100")),
        "leg_size": _decimal_str(size),
        "starting_inventory": _decimal_str(_positive_decimal(starting_inventory, default=size) if starting_inventory is not None else size),
        "slippage_cents": _decimal_str(_decimal(slippage_cents) or Decimal("0")),
        "point_count": len(points),
        "event_count": len(events),
        "completed_cycle_count": completed_cycles,
        "gross_return_usd": _decimal_str(gross_return),
        "estimated_cost_usd": _decimal_str(estimated_cost),
        "net_return_usd": _decimal_str(net_return),
        "profitable": status == "positive_after_costs",
        "events": events,
        "source_caveats": _source_caveats(price_history, points),
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def build_grid_scalp_validation(
    price_history: dict[str, Any] | list[Any],
    *,
    premise_state: str = "unreviewed",
    spread_cents: Decimal | float | str | None = None,
    depth_usd: Decimal | float | str | None = None,
    days_to_resolution: int | None = None,
    grid_step_price: Decimal | float | str = "0.01",
    leg_size: Decimal | float | str = "5",
    slippage_cents: Decimal | float | str = "0",
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Return review fields for the portfolio grid eligibility gate."""

    features = build_price_memory_features(price_history, now_utc=now_utc)
    backtest = simulate_grid_backtest(
        price_history,
        grid_step_price=grid_step_price,
        leg_size=leg_size,
        slippage_cents=slippage_cents,
        now_utc=now_utc,
    )
    one_day_range = _window_range_cents(features, "1d")
    seven_day_range = _window_range_cents(features, "7d")
    thirty_day_range = _window_range_cents(features, "30d")
    profitable = bool(backtest.get("profitable"))
    recommended_profile = "blocked_missing_grid_gates"
    recommended_hours: int | None = None
    if profitable and one_day_range is not None and one_day_range >= Decimal("5"):
        recommended_profile = "1d_5c_sideways_positive_backtest"
        recommended_hours = 24
    elif profitable and seven_day_range is not None and seven_day_range >= Decimal("10"):
        recommended_profile = "3d_10c_weekly_sideways_positive_backtest"
        recommended_hours = 72
    elif profitable and thirty_day_range is not None and thirty_day_range >= Decimal("15"):
        recommended_profile = "7d_15c_monthly_sideways_positive_backtest"
        recommended_hours = 168

    return {
        "schema_version": GRID_VALIDATION_SCHEMA_VERSION,
        "generated_at_utc": features["generated_at_utc"],
        "status": "grid_validation_candidate" if recommended_hours is not None else "blocked_missing_grid_gates",
        "validator_profile": recommended_profile,
        "recommended_worker_duration_hours": recommended_hours,
        "grid_eligibility_review_fields": {
            "validator_profile": recommended_profile,
            "premise_state": premise_state,
            "one_day_range_cents": _decimal_float(one_day_range),
            "seven_day_range_cents": _decimal_float(seven_day_range),
            "thirty_day_range_cents": _decimal_float(thirty_day_range),
            "days_to_resolution": days_to_resolution,
            "spread_cents": _decimal_float(_decimal(spread_cents)),
            "depth_usd": _decimal_float(_decimal(depth_usd)),
            "one_day_backtest_positive": profitable and recommended_hours == 24,
            "three_day_backtest_positive": profitable and recommended_hours == 72,
            "seven_day_backtest_positive": profitable and recommended_hours == 168,
            "recommended_worker_duration_hours": recommended_hours,
            "backtest_json": backtest,
            "review_json": {"price_memory_features": features, "grid_backtest": backtest},
        },
        "price_memory_features": features,
        "grid_backtest_summary": backtest,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def fetch_clob_price_history(
    *,
    token_id: str,
    start_ts: int | float | None = None,
    end_ts: int | float | None = None,
    interval: str | None = None,
    fidelity: int | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Fetch public Polymarket CLOB price history for one outcome token."""

    query: dict[str, Any] = {"market": str(token_id)}
    if start_ts is not None:
        query["startTs"] = float(start_ts)
    if end_ts is not None:
        query["endTs"] = float(end_ts)
    if interval:
        query["interval"] = str(interval)
    if fidelity is not None:
        query["fidelity"] = int(fidelity)
    request = Request(f"{POLYMARKET_CLOB_BASE_URL}/prices-history?{urlencode(query)}", headers=POLYMARKET_PUBLIC_HEADERS)
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed public provider URL.
        import json

        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"history": []}


def build_price_memory_collection(
    market_rows: list[dict[str, Any]] | dict[str, Any],
    *,
    price_history_by_token: dict[str, Any] | None = None,
    fetch_missing: bool = False,
    lookback_days: int = 30,
    interval: str | None = None,
    fidelity: int | None = None,
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Collect price-memory features for slots, past sleeves, orders, and candidates."""

    generated_at = _coerce_utc(now_utc)
    rows = _collection_market_rows(market_rows)
    history_lookup = price_history_by_token or {}
    seen: set[str] = set()
    snapshots: list[dict[str, Any]] = []
    caveats: list[str] = []

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            caveats.append(f"market_row_{index}_not_object")
            continue
        token_id = _optional_text(_first_value(row, ("token_id", "asset_id", "asset", "clob_token_id", "outcome_token_id")))
        market_slug = _optional_text(_first_value(row, ("market_slug", "slug", "event_slug", "condition_slug")))
        identity = token_id or market_slug or _optional_text(_first_value(row, ("market_title", "title", "question"))) or f"row-{index}"
        identity_key = identity.lower()
        if identity_key in seen:
            caveats.append(f"duplicate_market_row_skipped:{identity}")
            continue
        seen.add(identity_key)

        if _is_excluded_scope_row(row):
            snapshots.append(_collection_skip_snapshot(row, status="ignored_scope_excluded", reason="non_global_portfolio_scope"))
            continue
        if not token_id:
            snapshots.append(_collection_skip_snapshot(row, status="blocked_missing_token_id", reason="token_id_missing"))
            continue

        row_caveats: list[str] = []
        history = _history_for_row(row, history_lookup)
        if history is None and fetch_missing:
            try:
                history = fetch_clob_price_history(
                    token_id=token_id,
                    start_ts=int((generated_at - timedelta(days=max(1, lookback_days))).timestamp()),
                    end_ts=int(generated_at.timestamp()),
                    interval=interval,
                    fidelity=fidelity,
                )
            except Exception as exc:  # pragma: no cover - network failure shape is provider-dependent.
                row_caveats.append(f"fetch_failed:{exc.__class__.__name__}")
                history = None
        if history is None:
            snapshots.append(
                _collection_skip_snapshot(
                    row,
                    status="blocked_missing_price_history",
                    reason="price_history_missing",
                    extra_caveats=row_caveats,
                )
            )
            continue

        features = build_price_memory_features(history, now_utc=generated_at)
        validation = build_grid_scalp_validation(
            history,
            premise_state=str(row.get("premise_state") or "unreviewed"),
            spread_cents=row.get("spread_cents") or _nested_value(row, "direct_orderbook", "spread_cents"),
            depth_usd=row.get("depth_usd") or _nested_value(row, "direct_orderbook", "depth_usd"),
            days_to_resolution=_int(row.get("days_to_resolution")),
            now_utc=generated_at,
        )
        feature_caveats = list(features.get("source_caveats") or [])
        status = "price_memory_featured" if not feature_caveats else "price_memory_featured_with_caveats"
        snapshots.append(
            {
                "status": status,
                "source": str(row.get("source") or row.get("slot_state") or "global_portfolio_price_memory"),
                "market_title": _optional_text(_first_value(row, ("market_title", "title", "question"))) or "Unknown market",
                "market_slug": market_slug,
                "token_id": token_id,
                "outcome": _optional_text(_first_value(row, ("outcome", "side", "name"))),
                "domain": _optional_text(_first_value(row, ("domain", "category", "group"))),
                "slot_state": _optional_text(_first_value(row, ("slot_state", "status"))) or "candidate",
                "current_price": _float(_first_value(row, ("current_price", "price", "cur_price", "proposed_price"))),
                "market_volume_usd": _float(_first_value(row, ("market_volume_usd", "volume_usd", "volume"))),
                "volume_24h_usd": _float(_first_value(row, ("volume_24h_usd", "volume_24h"))),
                "liquidity_usd": _float(_first_value(row, ("liquidity_usd", "liquidity"))),
                "feature_schema_version": features["schema_version"],
                "one_day_range_cents": _window_float(features, "1d", "range_cents"),
                "seven_day_range_cents": _window_float(features, "7d", "range_cents"),
                "thirty_day_range_cents": _window_float(features, "30d", "range_cents"),
                "one_day_trend_cents": _window_float(features, "1d", "trend_cents"),
                "seven_day_trend_cents": _window_float(features, "7d", "trend_cents"),
                "thirty_day_trend_cents": _window_float(features, "30d", "trend_cents"),
                "features_json": features,
                "grid_scalp_validation": validation,
                "source_caveats": sorted(set(row_caveats + feature_caveats)),
                "registry_json": dict(row),
            }
        )

    return {
        "schema_version": PRICE_MEMORY_COLLECTION_SCHEMA_VERSION,
        "generated_at_utc": _iso(generated_at),
        "market_row_count": len(rows),
        "snapshot_count": len(snapshots),
        "featured_count": sum(1 for item in snapshots if str(item.get("status", "")).startswith("price_memory_featured")),
        "blocked_count": sum(1 for item in snapshots if str(item.get("status", "")).startswith("blocked")),
        "ignored_count": sum(1 for item in snapshots if str(item.get("status", "")).startswith("ignored")),
        "snapshots": snapshots,
        "source_caveats": sorted(set(caveats)),
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def _parse_price_points(price_history: dict[str, Any] | list[Any]) -> list[PortfolioPricePoint]:
    rows = _history_rows(price_history)
    points: list[PortfolioPricePoint] = []
    fallback_start = datetime(1970, 1, 1, tzinfo=UTC)
    for index, row in enumerate(rows):
        if isinstance(row, (int, float, str, Decimal)):
            price = _decimal(row)
            timestamp = fallback_start + timedelta(seconds=index)
        elif isinstance(row, dict):
            price = _first_decimal(row, ("p", "price", "mid_price", "close", "value"))
            timestamp = _coerce_utc(
                row.get("t")
                or row.get("timestamp")
                or row.get("time")
                or row.get("captured_at")
                or row.get("created_at")
                or row.get("datetime")
                or (fallback_start + timedelta(seconds=index))
            )
        else:
            continue
        if price is None or price <= 0:
            continue
        points.append(PortfolioPricePoint(timestamp_utc=timestamp, price=price))
    return sorted(points, key=lambda item: item.timestamp_utc)


def _history_rows(price_history: dict[str, Any] | list[Any]) -> list[Any]:
    if isinstance(price_history, list):
        return price_history
    if not isinstance(price_history, dict):
        return []
    for key in ("history", "prices", "price_history", "rows", "items", "data"):
        value = price_history.get(key)
        if isinstance(value, list):
            return value
    return []


def _window_features(points: list[PortfolioPricePoint], *, latest_at: datetime, days: int) -> dict[str, Any]:
    start = latest_at - timedelta(days=days)
    window_points = [point for point in points if point.timestamp_utc >= start]
    if not window_points:
        return {
            "days": days,
            "sample_count": 0,
            "open_price": None,
            "close_price": None,
            "high_price": None,
            "low_price": None,
            "range_cents": None,
            "range_percent": None,
            "trend_cents": None,
            "midpoint_cross_count": 0,
        }
    prices = [point.price for point in window_points]
    open_price = prices[0]
    close_price = prices[-1]
    high = max(prices)
    low = min(prices)
    range_price = high - low
    trend = close_price - open_price
    midpoint = low + (range_price / Decimal("2"))
    midpoint_cross_count = _midpoint_cross_count(prices, midpoint=midpoint)
    range_percent = (range_price / open_price * Decimal("100")) if open_price > 0 else None
    return {
        "days": days,
        "sample_count": len(window_points),
        "open_price": _decimal_str(open_price),
        "close_price": _decimal_str(close_price),
        "high_price": _decimal_str(high),
        "low_price": _decimal_str(low),
        "range_cents": _decimal_str(range_price * Decimal("100")),
        "range_percent": _decimal_str(range_percent),
        "trend_cents": _decimal_str(trend * Decimal("100")),
        "midpoint_cross_count": midpoint_cross_count,
    }


def _midpoint_cross_count(prices: list[Decimal], *, midpoint: Decimal) -> int:
    previous_side: int | None = None
    crossings = 0
    for price in prices:
        side = 1 if price >= midpoint else -1
        if previous_side is not None and side != previous_side:
            crossings += 1
        previous_side = side
    return crossings


def _event_payload(
    *,
    action: str,
    point: PortfolioPricePoint,
    price: Decimal,
    size: Decimal,
    cycle_return: Decimal,
) -> dict[str, Any]:
    return {
        "action": action,
        "timestamp_utc": _iso(point.timestamp_utc),
        "limit_price": _decimal_str(price),
        "size": _decimal_str(size),
        "cycle_return_usd": _decimal_str(cycle_return),
        "order_preparation_allowed": False,
        "order_submission_allowed": False,
    }


def _source_caveats(price_history: dict[str, Any] | list[Any], points: list[PortfolioPricePoint]) -> list[str]:
    caveats: list[str] = []
    if not points:
        caveats.append("missing_price_history_points")
    if isinstance(price_history, dict):
        source_caveats = price_history.get("source_caveats")
        if isinstance(source_caveats, list):
            caveats.extend(str(item) for item in source_caveats if str(item).strip())
    return sorted(set(caveats))


def _window_range_cents(features: dict[str, Any], window: str) -> Decimal | None:
    windows = features.get("windows") if isinstance(features.get("windows"), dict) else {}
    payload = windows.get(window) if isinstance(windows.get(window), dict) else {}
    return _decimal(payload.get("range_cents"))


def _collection_market_rows(market_rows: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(market_rows, list):
        return [dict(item) for item in market_rows if isinstance(item, dict)]
    if not isinstance(market_rows, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in (
        "market_rows",
        "markets",
        "slots",
        "current_slots",
        "open_positions",
        "open_orders",
        "closed_positions",
        "past_markets",
        "candidate_rows",
        "candidates",
    ):
        value = market_rows.get(key)
        if isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, dict))
    if not rows and any(key in market_rows for key in ("token_id", "asset", "market_slug", "slug")):
        rows.append(dict(market_rows))
    return rows


def _collection_skip_snapshot(
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    extra_caveats: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "source": str(row.get("source") or "global_portfolio_price_memory"),
        "market_title": _optional_text(_first_value(row, ("market_title", "title", "question"))) or "Unknown market",
        "market_slug": _optional_text(_first_value(row, ("market_slug", "slug", "event_slug"))),
        "token_id": _optional_text(_first_value(row, ("token_id", "asset_id", "asset", "clob_token_id", "outcome_token_id"))),
        "outcome": _optional_text(_first_value(row, ("outcome", "side", "name"))),
        "source_caveats": sorted(set([reason] + list(extra_caveats or []))),
        "registry_json": dict(row),
    }


def _history_for_row(row: dict[str, Any], history_lookup: dict[str, Any]) -> dict[str, Any] | list[Any] | None:
    direct = row.get("price_history") or row.get("prices_history") or row.get("history")
    if isinstance(direct, (dict, list)):
        return direct
    token_id = _optional_text(_first_value(row, ("token_id", "asset_id", "asset", "clob_token_id", "outcome_token_id")))
    market_slug = _optional_text(_first_value(row, ("market_slug", "slug", "event_slug")))
    for key in (token_id, market_slug):
        if key and isinstance(history_lookup.get(key), (dict, list)):
            return history_lookup[key]
    tokens = history_lookup.get("tokens") if isinstance(history_lookup.get("tokens"), dict) else {}
    if token_id and isinstance(tokens.get(token_id), (dict, list)):
        return tokens[token_id]
    markets = history_lookup.get("markets") if isinstance(history_lookup.get("markets"), dict) else {}
    if market_slug and isinstance(markets.get(market_slug), (dict, list)):
        return markets[market_slug]
    return None


def _is_excluded_scope_row(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "").lower()
        for value in (
            row.get("market_title"),
            row.get("title"),
            row.get("question"),
            row.get("market_slug"),
            row.get("slug"),
            row.get("event_slug"),
            row.get("category"),
            row.get("domain"),
            row.get("source"),
            row.get("owner"),
            row.get("strategy_id"),
        )
    )
    padded = f" {text.replace('-', ' ')} "
    if any(token in padded for token in (" nba ", " wnba ", "nba finals", "wnba finals")):
        return True
    compact = re.sub(r"[\s_/-]+", "", text)
    crypto_terms = ("bitcoin", "btc", "ethereum", "eth")
    updown_terms = ("up or down", "up/down", "updown")
    timed_window = bool(re.search(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\s*[-–]\s*\d{1,2}:\d{2}", text))
    return (
        bool(re.search(r"\b(?:btc|eth)[-_ ]?updown[-_ ]?5m\b", text))
        or "updown5m" in compact
        or (
            any(term in text for term in updown_terms)
            and any(term in text for term in crypto_terms)
            and ("5m" in text or timed_window)
        )
        or "crypto-options-research" in text
        or "future-domain-research-agent" in text
    )


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _nested_value(row: dict[str, Any], parent: str, key: str) -> Any:
    value = row.get(parent)
    if isinstance(value, dict):
        return value.get(key)
    return None


def _window_float(features: dict[str, Any], window: str, key: str) -> float | None:
    windows = features.get("windows") if isinstance(features.get("windows"), dict) else {}
    payload = windows.get(window) if isinstance(windows.get(window), dict) else {}
    return _decimal_float(_decimal(payload.get(key)))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    return int(parsed)


def _float(value: Any) -> float | None:
    return _decimal_float(_decimal(value))


def _first_decimal(row: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = _decimal(row.get(key))
        if value is not None:
            return value
    return None


def _positive_decimal(value: Any, *, default: Decimal) -> Decimal:
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        return default
    return parsed


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        parsed = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _decimal_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _coerce_utc(value: datetime | str | int | float | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    text = str(value).strip()
    if not text:
        return datetime.now(UTC)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        numeric = _decimal(text)
        if numeric is not None:
            return _coerce_utc(float(numeric))
        return datetime.now(UTC)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "GRID_BACKTEST_SCHEMA_VERSION",
    "GRID_VALIDATION_SCHEMA_VERSION",
    "POLYMARKET_CLOB_BASE_URL",
    "PRICE_MEMORY_COLLECTION_SCHEMA_VERSION",
    "PRICE_MEMORY_FEATURE_SCHEMA_VERSION",
    "PortfolioPricePoint",
    "build_grid_scalp_validation",
    "build_price_memory_collection",
    "build_price_memory_features",
    "fetch_clob_price_history",
    "simulate_grid_backtest",
]
