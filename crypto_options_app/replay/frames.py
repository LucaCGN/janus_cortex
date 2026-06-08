from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ReplayFrame:
    replay_frame_key: str
    event_key: str
    event_token_key: str
    replay_timestamp_utc: datetime
    source_observed_at_utc: datetime
    decision_at_utc: datetime
    market_state: dict[str, Any]
    underlying_state: dict[str, Any]
    indicator_state: dict[str, Any] = field(default_factory=dict)
    profile_state: dict[str, Any] = field(default_factory=dict)
    buying_ahead: bool = False


@dataclass(frozen=True)
class ReplayFrameResult:
    frame: ReplayFrame | None
    blockers: tuple[str, ...] = ()


def build_replay_frame(
    *,
    event_key: str,
    event_token_key: str,
    decision_at_utc: datetime,
    price_ticks: Iterable[Mapping[str, Any]],
    underlying_ticks: Iterable[Mapping[str, Any]],
    indicator_snapshots: Iterable[Mapping[str, Any]] = (),
    profile_signals: Iterable[Mapping[str, Any]] = (),
    buying_ahead_rows: Iterable[Mapping[str, Any]] = (),
) -> ReplayFrameResult:
    decision_at = decision_at_utc.astimezone(UTC)
    price = _latest_at_or_before(price_ticks, decision_at, "system_received_at_utc")
    underlying = _latest_at_or_before(underlying_ticks, decision_at, "observed_at_utc")
    if price is None:
        return ReplayFrameResult(None, ("missing_contemporaneous_polymarket_price",))
    if underlying is None:
        return ReplayFrameResult(None, ("missing_contemporaneous_underlying_price",))

    indicators = [
        dict(row)
        for row in indicator_snapshots
        if _parse_datetime(row.get("computed_at_utc")) <= decision_at
    ]
    signals = [
        dict(row)
        for row in profile_signals
        if _parse_datetime(row.get("evaluated_at_utc") or row.get("computed_at_utc")) <= decision_at
    ]
    buying_ahead = any(_parse_datetime(row.get("activity_at_utc")) <= decision_at for row in buying_ahead_rows)
    source_observed = max(
        _parse_datetime(price["system_received_at_utc"]),
        _parse_datetime(underlying["observed_at_utc"]),
    )
    frame_key = _stable_key("replay_frame", event_token_key, decision_at.isoformat())
    frame = ReplayFrame(
        replay_frame_key=frame_key,
        event_key=event_key,
        event_token_key=event_token_key,
        replay_timestamp_utc=decision_at,
        source_observed_at_utc=source_observed,
        decision_at_utc=decision_at,
        market_state=dict(price),
        underlying_state=dict(underlying),
        indicator_state={"snapshots": indicators},
        profile_state={"signals": signals},
        buying_ahead=buying_ahead,
    )
    return ReplayFrameResult(frame, ())


def assert_no_lookahead(frame: ReplayFrame) -> None:
    decision_at = frame.decision_at_utc.astimezone(UTC)
    timestamps = [
        _parse_datetime(frame.market_state.get("system_received_at_utc")),
        _parse_datetime(frame.underlying_state.get("observed_at_utc")),
    ]
    timestamps.extend(_parse_datetime(row["computed_at_utc"]) for row in frame.indicator_state.get("snapshots", []))
    timestamps.extend(
        _parse_datetime(row.get("evaluated_at_utc") or row.get("computed_at_utc"))
        for row in frame.profile_state.get("signals", [])
    )
    if any(timestamp > decision_at for timestamp in timestamps):
        raise ValueError("replay frame contains lookahead data")


def insert_replay_frame(conn: Any, *, replay_dataset_key: str | None, frame: ReplayFrame) -> None:
    assert_no_lookahead(frame)
    conn.execute(
        """
        INSERT INTO replay_frames(
            replay_frame_key, replay_dataset_key, event_key, event_token_key,
            replay_timestamp_utc, source_observed_at_utc, decision_at_utc, frame_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(replay_frame_key) DO NOTHING
        """,
        (
            frame.replay_frame_key,
            replay_dataset_key,
            frame.event_key,
            frame.event_token_key,
            frame.replay_timestamp_utc.isoformat(),
            frame.source_observed_at_utc.isoformat(),
            frame.decision_at_utc.isoformat(),
            json.dumps(
                {
                    "market_state": frame.market_state,
                    "underlying_state": frame.underlying_state,
                    "indicator_state": frame.indicator_state,
                    "profile_state": frame.profile_state,
                    "buying_ahead": frame.buying_ahead,
                },
                sort_keys=True,
                default=str,
            ),
            datetime.now(UTC).isoformat(),
        ),
    )


def build_replay_frame_from_price_path_db(
    conn: Any,
    *,
    event_token_key: str,
    decision_at_utc: datetime,
    require_underlying: bool = False,
) -> ReplayFrameResult:
    decision_at = decision_at_utc.astimezone(UTC)
    price_row = conn.execute(
        """
        SELECT *
        FROM polymarket_price_ticks
        WHERE event_token_key = ?
          AND system_received_at_utc <= ?
        ORDER BY system_received_at_utc DESC
        LIMIT 1
        """,
        (event_token_key, decision_at.isoformat()),
    ).fetchone()
    if price_row is None:
        return ReplayFrameResult(None, ("missing_contemporaneous_polymarket_price",))
    price = dict(price_row)
    symbol = _symbol_for_event_token(conn, event_token_key=event_token_key, fallback=price.get("event_slug"))
    underlying_row = None
    if symbol:
        underlying_row = conn.execute(
            """
            SELECT *
            FROM underlying_price_ticks
            WHERE symbol = ?
              AND observed_at_utc <= ?
            ORDER BY observed_at_utc DESC
            LIMIT 1
            """,
            (symbol, decision_at.isoformat()),
        ).fetchone()
    if underlying_row is None and require_underlying:
        return ReplayFrameResult(None, ("missing_contemporaneous_underlying_price",))
    if underlying_row is None:
        underlying = {
            "symbol": symbol,
            "source": "option_price_path_without_underlying",
            "observed_at_utc": price["system_received_at_utc"],
            "price": None,
        }
    else:
        underlying = dict(underlying_row)
    indicators = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM indicator_snapshots
            WHERE (symbol = ? OR ? IS NULL)
              AND computed_at_utc <= ?
            ORDER BY computed_at_utc DESC
            LIMIT 50
            """,
            (symbol, symbol, decision_at.isoformat()),
        ).fetchall()
    ]
    result = build_replay_frame(
        event_key=str(price.get("event_key") or event_token_key),
        event_token_key=event_token_key,
        decision_at_utc=decision_at,
        price_ticks=[price],
        underlying_ticks=[underlying],
        indicator_snapshots=indicators,
    )
    return result


def _symbol_for_event_token(conn: Any, *, event_token_key: str, fallback: Any = None) -> str | None:
    row = conn.execute("SELECT symbol FROM event_tokens WHERE event_token_key = ? LIMIT 1", (event_token_key,)).fetchone()
    if row is not None and row["symbol"]:
        return str(row["symbol"]).upper()
    slug = str(fallback or event_token_key).lower()
    for symbol in ("btc", "eth", "sol", "xrp"):
        if slug.startswith(f"{symbol}-") or f"-{symbol}-" in slug:
            return symbol.upper()
    return None


def data_coverage_blocker(candidate_id: str, missing: Iterable[str]) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "status": "blocked",
        "blocker_type": "data_coverage",
        "missing": tuple(missing),
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _latest_at_or_before(rows: Iterable[Mapping[str, Any]], decision_at: datetime, timestamp_key: str) -> Mapping[str, Any] | None:
    candidates = [row for row in rows if row.get(timestamp_key) and _parse_datetime(row[timestamp_key]) <= decision_at]
    if not candidates:
        return None
    return max(candidates, key=lambda row: _parse_datetime(row[timestamp_key]))


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
