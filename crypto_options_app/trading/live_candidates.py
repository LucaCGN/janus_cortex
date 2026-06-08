from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.workers.runtime_adapter import RuntimeScenario


@dataclass(frozen=True)
class VerifiedLiveMarketCandidate:
    event_key: str
    event_token_key: str
    token_id: str
    event_slug: str | None
    outcome: str | None
    best_bid: float | None
    best_ask: float
    spread: float
    ask_size: float
    depth_top3_ask_size: float
    observed_at_utc: datetime
    quote_age_seconds: float
    source: str
    time_remaining_seconds: float | None = None


@dataclass(frozen=True)
class LiveMarketCandidateVerification:
    verified: bool
    candidate: VerifiedLiveMarketCandidate | None
    blockers: tuple[str, ...]


def verify_live_market_candidate(
    row: dict[str, Any],
    *,
    now_utc: datetime | None = None,
    max_quote_age_seconds: float = 5.0,
    max_spread: float = 0.08,
    min_ask_size: float = 1.0,
    min_depth_top3_ask_size: float = 1.0,
) -> LiveMarketCandidateVerification:
    now_utc = (now_utc or datetime.now(UTC)).astimezone(UTC)
    blockers: list[str] = []
    token_id = _text(row.get("token_id") or row.get("asset_id") or row.get("clob_token_id"))
    event_key = _text(row.get("event_key") or row.get("event_id") or row.get("event_slug"))
    event_token_key = _text(row.get("event_token_key") or token_id)
    best_bid = _optional_float(row.get("best_bid"))
    best_ask = _optional_float(row.get("best_ask"))
    ask_size = _optional_float(row.get("ask_size"))
    depth_top3_ask_size = _optional_float(row.get("depth_top3_ask_size"))
    observed_at = _parse_datetime(row.get("observed_at_utc") or row.get("observed_at") or row.get("system_received_at_utc"))
    event_end_at = _parse_datetime(row.get("event_end_time_utc") or row.get("window_end_time") or row.get("end_time_utc"))
    quote_age = None if observed_at is None else max(0.0, (now_utc - observed_at).total_seconds())
    time_remaining = None if event_end_at is None else max(0.0, (event_end_at - now_utc).total_seconds())
    spread = _optional_float(row.get("spread"))
    if spread is None and best_bid is not None and best_ask is not None:
        spread = max(0.0, best_ask - best_bid)

    if not token_id:
        blockers.append("missing_token_id")
    if not event_key:
        blockers.append("missing_event_key")
    if best_ask is None:
        blockers.append("missing_best_ask")
    elif not 0.0 < best_ask < 1.0:
        blockers.append("invalid_best_ask")
    if observed_at is None or quote_age is None:
        blockers.append("missing_quote_timestamp")
    elif quote_age > max_quote_age_seconds:
        blockers.append("stale_quote")
    if spread is None:
        blockers.append("missing_spread")
    elif spread > max_spread:
        blockers.append("spread_too_wide")
    if ask_size is None:
        blockers.append("missing_ask_size")
    elif ask_size < min_ask_size:
        blockers.append("insufficient_ask_size")
    if depth_top3_ask_size is None:
        blockers.append("missing_depth_top3_ask_size")
    elif depth_top3_ask_size < min_depth_top3_ask_size:
        blockers.append("insufficient_depth_top3_ask_size")

    if blockers:
        return LiveMarketCandidateVerification(False, None, tuple(blockers))
    assert token_id is not None
    assert event_key is not None
    assert event_token_key is not None
    assert best_ask is not None
    assert spread is not None
    assert ask_size is not None
    assert depth_top3_ask_size is not None
    assert observed_at is not None
    assert quote_age is not None
    return LiveMarketCandidateVerification(
        True,
        VerifiedLiveMarketCandidate(
            event_key=event_key,
            event_token_key=event_token_key,
            token_id=token_id,
            event_slug=_text(row.get("event_slug")),
            outcome=_text(row.get("outcome")),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            ask_size=ask_size,
            depth_top3_ask_size=depth_top3_ask_size,
            observed_at_utc=observed_at,
            quote_age_seconds=quote_age,
            source=str(row.get("source") or "polymarket_current_order_book"),
            time_remaining_seconds=time_remaining,
        ),
        (),
    )


def scenario_from_verified_candidate(
    candidate: VerifiedLiveMarketCandidate,
    *,
    shares: float = 1.0,
    signal_age_seconds: float = 1.0,
    profile_age_seconds: float = 20.0,
    expected_slippage: float = 0.01,
    time_remaining_seconds: float = 300.0,
    signal_context: dict[str, Any] | None = None,
) -> RuntimeScenario:
    return RuntimeScenario(
        event_key=candidate.event_key,
        event_token_key=candidate.event_token_key,
        token_id=candidate.token_id,
        event_slug=candidate.event_slug,
        outcome=candidate.outcome,
        side="BUY",
        shares=shares,
        limit_price=candidate.best_ask,
        quote_age_seconds=candidate.quote_age_seconds,
        signal_age_seconds=signal_age_seconds,
        profile_age_seconds=profile_age_seconds,
        spread=candidate.spread,
        expected_slippage=expected_slippage,
        liquidity_depth=candidate.depth_top3_ask_size,
        time_remaining_seconds=(
            candidate.time_remaining_seconds
            if candidate.time_remaining_seconds is not None
            else time_remaining_seconds
        ),
        live_market_verified=True,
        signal_context=signal_context or {},
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
