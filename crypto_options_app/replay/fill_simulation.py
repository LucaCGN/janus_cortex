from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_options_app.replay.frames import ReplayFrame


@dataclass(frozen=True)
class ReplayOrder:
    order_type: str
    side: str
    shares: float
    decision_at_utc: datetime
    limit_price: float | None = None
    max_quote_age_seconds: float = 5.0


@dataclass(frozen=True)
class FillSimulationResult:
    fillability_status: str
    order_type: str
    side: str
    requested_shares: float
    filled_shares: float
    fill_price: float | None
    slippage: float
    blockers: tuple[str, ...] = ()
    simulation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PathFillSimulationConfig:
    latency_ms: float = 0.0
    ttl_seconds: float = 5.0


def simulate_fill(order: ReplayOrder, frame: ReplayFrame) -> FillSimulationResult:
    market = frame.market_state
    quote_time = _parse_datetime(market.get("system_received_at_utc"))
    quote_age = (order.decision_at_utc.astimezone(UTC) - quote_time).total_seconds()
    if quote_age > order.max_quote_age_seconds:
        return _blocked(order, "stale_quote", {"quote_age_seconds": quote_age})
    best_bid = _optional_float(market.get("best_bid"))
    best_ask = _optional_float(market.get("best_ask"))
    mid_price = _optional_float(market.get("mid_price"))
    bid_depth = _optional_float(market.get("depth_top3_bid_size")) or 0.0
    ask_depth = _optional_float(market.get("depth_top3_ask_size")) or 0.0
    if best_bid is None or best_ask is None or mid_price is None:
        return _blocked(order, "missing_executable_quote", {"quote_age_seconds": quote_age})

    side = order.side.upper()
    order_type = order.order_type.upper()
    if side == "BUY":
        executable_price = best_ask
        available_shares = ask_depth
        limit_crossed = order.limit_price is not None and order.limit_price >= best_ask
        slippage = executable_price - mid_price
    elif side == "SELL":
        executable_price = best_bid
        available_shares = bid_depth
        limit_crossed = order.limit_price is not None and order.limit_price <= best_bid
        slippage = mid_price - executable_price
    else:
        return _blocked(order, "unsupported_side", {})

    if order_type == "LIMIT" and not limit_crossed:
        return FillSimulationResult(
            "unfilled",
            order_type,
            side,
            order.shares,
            0.0,
            None,
            0.0,
            ("limit_not_crossed",),
            {"quote_age_seconds": quote_age},
        )
    if order_type not in {"MARKET", "LIMIT"}:
        return _blocked(order, "unsupported_order_type", {})
    if available_shares <= 0:
        return FillSimulationResult(
            "unfilled",
            order_type,
            side,
            order.shares,
            0.0,
            None,
            0.0,
            ("no_visible_depth",),
            {"quote_age_seconds": quote_age},
        )
    filled = min(order.shares, available_shares)
    status = "filled" if filled >= order.shares else "partial"
    return FillSimulationResult(
        status,
        order_type,
        side,
        order.shares,
        filled,
        executable_price,
        slippage,
        (),
        {"quote_age_seconds": quote_age, "available_shares": available_shares},
    )


def simulate_fill_path(
    order: ReplayOrder,
    frames: list[ReplayFrame] | tuple[ReplayFrame, ...],
    *,
    config: PathFillSimulationConfig | None = None,
) -> FillSimulationResult:
    """Simulate execution against the post-decision quote path.

    This is intentionally separate from ``simulate_fill`` so existing callers
    keep their snapshot semantics while backtest calibration can model live-like
    latency, TTL, missed limits, and partial fills.
    """

    path_config = config or PathFillSimulationConfig()
    decision_at = order.decision_at_utc.astimezone(UTC)
    submit_at = decision_at + timedelta(milliseconds=float(path_config.latency_ms))
    expires_at = submit_at + timedelta(seconds=float(path_config.ttl_seconds))
    sorted_frames = sorted(frames, key=_frame_quote_time)
    frames_examined = 0
    saw_post_latency_quote = False
    saw_depth = False
    saw_executable_quote = False
    last_blocker = "no_post_latency_quote"

    for frame in sorted_frames:
        quote_time = _frame_quote_time(frame)
        if quote_time < submit_at:
            continue
        if quote_time > expires_at:
            break
        saw_post_latency_quote = True
        frames_examined += 1
        market = frame.market_state
        best_bid = _optional_float(market.get("best_bid"))
        best_ask = _optional_float(market.get("best_ask"))
        mid_price = _optional_float(market.get("mid_price"))
        bid_depth = _optional_float(market.get("depth_top3_bid_size")) or 0.0
        ask_depth = _optional_float(market.get("depth_top3_ask_size")) or 0.0
        if best_bid is None or best_ask is None or mid_price is None:
            last_blocker = "missing_executable_quote"
            continue

        side = order.side.upper()
        order_type = order.order_type.upper()
        if side == "BUY":
            executable_price = best_ask
            available_shares = ask_depth
            limit_crossed = order.limit_price is not None and order.limit_price >= best_ask
            slippage = executable_price - mid_price
        elif side == "SELL":
            executable_price = best_bid
            available_shares = bid_depth
            limit_crossed = order.limit_price is not None and order.limit_price <= best_bid
            slippage = mid_price - executable_price
        else:
            return _blocked(order, "unsupported_side", {})

        if order_type not in {"MARKET", "LIMIT"}:
            return _blocked(order, "unsupported_order_type", {})
        saw_executable_quote = True
        if order_type == "LIMIT" and not limit_crossed:
            last_blocker = "limit_not_crossed_during_ttl"
            continue
        if available_shares <= 0:
            last_blocker = "no_visible_depth"
            continue
        saw_depth = True
        filled = min(order.shares, available_shares)
        status = "filled" if filled >= order.shares else "partial"
        return FillSimulationResult(
            status,
            order_type,
            side,
            order.shares,
            filled,
            executable_price,
            slippage,
            (),
            {
                "latency_ms": path_config.latency_ms,
                "ttl_seconds": path_config.ttl_seconds,
                "submit_at_utc": submit_at.isoformat(),
                "expires_at_utc": expires_at.isoformat(),
                "frame_quote_at_utc": quote_time.isoformat(),
                "quote_after_submit_seconds": (quote_time - submit_at).total_seconds(),
                "frames_examined": frames_examined,
                "available_shares": available_shares,
            },
        )

    if not saw_post_latency_quote:
        blockers = ("no_post_latency_quote",)
    elif not saw_executable_quote:
        blockers = (last_blocker,)
    elif not saw_depth and last_blocker == "no_visible_depth":
        blockers = ("no_visible_depth_during_ttl",)
    else:
        blockers = (last_blocker,)
    return FillSimulationResult(
        "unfilled",
        order.order_type.upper(),
        order.side.upper(),
        order.shares,
        0.0,
        None,
        0.0,
        blockers,
        {
            "latency_ms": path_config.latency_ms,
            "ttl_seconds": path_config.ttl_seconds,
            "submit_at_utc": submit_at.isoformat(),
            "expires_at_utc": expires_at.isoformat(),
            "frames_examined": frames_examined,
        },
    )


def _blocked(order: ReplayOrder, reason: str, simulation: dict[str, Any]) -> FillSimulationResult:
    return FillSimulationResult(
        "blocked",
        order.order_type.upper(),
        order.side.upper(),
        order.shares,
        0.0,
        None,
        0.0,
        (reason,),
        simulation,
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _frame_quote_time(frame: ReplayFrame) -> datetime:
    value = frame.market_state.get("system_received_at_utc") or frame.replay_timestamp_utc
    return _parse_datetime(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
