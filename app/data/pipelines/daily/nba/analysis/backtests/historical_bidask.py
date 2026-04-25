from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


HISTORICAL_BIDASK_L1_COLUMNS = (
    "season",
    "season_phase",
    "game_id",
    "game_date",
    "market_id",
    "outcome_id",
    "team_side",
    "captured_at_utc",
    "ingested_at_utc",
    "source_sequence_id",
    "source_latency_ms",
    "best_bid_price",
    "best_bid_size",
    "best_ask_price",
    "best_ask_size",
    "last_trade_price",
    "last_trade_size",
    "capture_source",
    "capture_status",
    "quote_source_mode",
    "quote_resolution_status",
    "raw_payload_json",
)

QUOTE_COVERAGE_SUMMARY_COLUMNS = (
    "game_id",
    "game_date",
    "season_phase",
    "state_source",
    "quote_source_mode",
    "quote_row_count",
    "home_quote_row_count",
    "away_quote_row_count",
    "direct_bidask_quote_count",
    "synthetic_quote_count",
    "unresolved_quote_count",
    "first_quote_at",
    "last_quote_at",
    "avg_quote_gap_seconds",
    "max_quote_gap_seconds",
    "dropout_span_count",
    "max_dropout_span_seconds",
    "replay_poll_count",
    "covered_poll_count",
    "coverage_ratio",
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clamp(value: float, *, low: float, high: float) -> float:
    return max(low, min(high, value))


def _tick_frame(series_item: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for tick in series_item.get("ticks") or []:
        ts = _parse_datetime(tick.get("ts"))
        price = _safe_float(tick.get("price"))
        if ts is None or price is None:
            continue
        rows.append(
            {
                "ts": ts,
                "price": float(price),
                "bid": _safe_float(tick.get("bid")),
                "ask": _safe_float(tick.get("ask")),
                "liquidity": _safe_float(tick.get("liquidity")),
                "volume": _safe_float(tick.get("volume")),
                "source": str(tick.get("source") or ""),
                "outcome_id": str(tick.get("outcome_id") or series_item.get("outcome_id") or ""),
                "raw_tick": tick,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["ts", "price", "bid", "ask", "liquidity", "volume", "source", "outcome_id", "raw_tick"])
    return pd.DataFrame(rows).sort_values("ts", kind="mergesort").reset_index(drop=True)


def _latest_tick_before(frame: pd.DataFrame, *, cycle_at: datetime) -> dict[str, Any] | None:
    if frame.empty:
        return None
    work = frame[frame["ts"] <= cycle_at]
    if work.empty:
        return None
    return work.iloc[-1].to_dict()


def build_proxy_quote_fields_from_cross_side_ticks(
    *,
    own_price: float,
    opposite_price: float,
    own_ts: datetime,
    opposite_ts: datetime,
    proxy_min_spread_cents: float,
    proxy_max_spread_cents: float,
) -> dict[str, Any]:
    overround_cents = max(0.0, ((float(own_price) + float(opposite_price)) - 1.0) * 100.0)
    timestamp_skew_seconds = abs((own_ts - opposite_ts).total_seconds())
    skew_component_cents = min(float(proxy_max_spread_cents), timestamp_skew_seconds / 20.0)
    spread_cents = min(
        float(proxy_max_spread_cents),
        max(float(proxy_min_spread_cents), overround_cents, skew_component_cents),
    )
    half_spread = spread_cents / 200.0
    best_ask = _clamp(float(own_price) + half_spread, low=0.01, high=0.99)
    best_bid = _clamp(float(own_price) - half_spread, low=0.01, high=0.99)
    if best_bid >= best_ask:
        midpoint = _clamp(float(own_price), low=0.02, high=0.98)
        best_bid = _clamp(midpoint - 0.005, low=0.01, high=0.98)
        best_ask = _clamp(midpoint + 0.005, low=0.02, high=0.99)
    quote_time = min(own_ts, opposite_ts)
    ingest_time = max(own_ts, opposite_ts)
    return {
        "captured_at_utc": quote_time,
        "ingested_at_utc": ingest_time,
        "source_latency_ms": max(0.0, (ingest_time - quote_time).total_seconds() * 1000.0),
        "best_bid_price": round(best_bid, 6),
        "best_ask_price": round(best_ask, 6),
        "spread_cents": round(spread_cents, 4),
        "timestamp_skew_seconds": round(timestamp_skew_seconds, 6),
    }


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"))


def _game_date_for_context(context: Any) -> str | None:
    game_start_time = _parse_datetime((context.game or {}).get("game_start_time"))
    if game_start_time is not None:
        return game_start_time.date().isoformat()
    if context.state_df is not None and not context.state_df.empty and "game_date" in context.state_df.columns:
        value = context.state_df.iloc[0]["game_date"]
        if value is not None and str(value) != "NaT":
            return str(value)
    return None


def build_historical_bidask_l1_frame(
    context: Any,
    *,
    season: str,
    request: Any,
) -> pd.DataFrame:
    selected_market = context.bundle.get("selected_market") or {}
    market_id = str(selected_market.get("market_id") or "")
    side_series = {
        str(item.get("side") or ""): item
        for item in selected_market.get("series") or []
        if str(item.get("side") or "") in {"home", "away"}
    }
    frames = {side: _tick_frame(item) for side, item in side_series.items()}
    rows: list[dict[str, Any]] = []
    for side in ("home", "away"):
        frame = frames.get(side, pd.DataFrame())
        opposite_side = "away" if side == "home" else "home"
        opposite_frame = frames.get(opposite_side, pd.DataFrame())
        if frame.empty:
            continue
        sequence_id = 0
        for tick in frame.to_dict(orient="records"):
            own_ts = _parse_datetime(tick.get("ts"))
            own_price = _safe_float(tick.get("price"))
            if own_ts is None or own_price is None:
                continue
            best_bid = _safe_float(tick.get("bid"))
            best_ask = _safe_float(tick.get("ask"))
            liquidity = _safe_float(tick.get("liquidity"))
            volume = _safe_float(tick.get("volume"))
            row: dict[str, Any] = {
                "season": season,
                "season_phase": str(getattr(context, "season_phase", "") or ""),
                "game_id": str(getattr(context, "game_id", "") or ""),
                "game_date": _game_date_for_context(context),
                "market_id": market_id,
                "outcome_id": str(tick.get("outcome_id") or ""),
                "team_side": side,
                "captured_at_utc": own_ts,
                "ingested_at_utc": own_ts,
                "source_sequence_id": sequence_id,
                "source_latency_ms": 0.0,
                "best_bid_price": best_bid,
                "best_bid_size": None,
                "best_ask_price": best_ask,
                "best_ask_size": None,
                "last_trade_price": own_price,
                "last_trade_size": volume,
                "capture_source": str(tick.get("source") or ""),
                "capture_status": "direct_bidask" if best_bid is not None and best_ask is not None else "unresolved",
                "quote_source_mode": "historical_bidask_l1",
                "quote_resolution_status": "direct_bidask" if best_bid is not None and best_ask is not None else "unresolved",
                "raw_payload_json": _json_payload(tick.get("raw_tick") or {}),
            }
            if best_bid is not None and best_ask is not None:
                rows.append(row)
                sequence_id += 1
                continue

            opposite_tick = _latest_tick_before(opposite_frame, cycle_at=own_ts)
            opposite_ts = _parse_datetime((opposite_tick or {}).get("ts"))
            opposite_price = _safe_float((opposite_tick or {}).get("price"))
            if opposite_ts is None or opposite_price is None:
                row["capture_source"] = "synthetic_bidask_missing_opposite_side"
                row["capture_status"] = "missing_opposite_side"
                row["quote_resolution_status"] = "missing_opposite_side"
                rows.append(row)
                sequence_id += 1
                continue

            proxy_fields = build_proxy_quote_fields_from_cross_side_ticks(
                own_price=float(own_price),
                opposite_price=float(opposite_price),
                own_ts=own_ts,
                opposite_ts=opposite_ts,
                proxy_min_spread_cents=float(request.proxy_min_spread_cents),
                proxy_max_spread_cents=float(request.proxy_max_spread_cents),
            )
            rows.append(
                {
                    **row,
                    "captured_at_utc": own_ts,
                    "ingested_at_utc": own_ts,
                    "source_latency_ms": proxy_fields["source_latency_ms"],
                    "best_bid_price": proxy_fields["best_bid_price"],
                    "best_ask_price": proxy_fields["best_ask_price"],
                    "best_bid_size": liquidity,
                    "best_ask_size": liquidity,
                    "capture_source": "synthetic_bidask_from_cross_side_ticks",
                    "capture_status": "synthetic_from_cross_side_ticks",
                    "quote_resolution_status": "synthetic_from_cross_side_ticks",
                    "raw_payload_json": _json_payload(
                        {
                            "own_tick": tick.get("raw_tick"),
                            "opposite_tick": opposite_tick.get("raw_tick") if isinstance(opposite_tick, dict) else {},
                            "oldest_component_at_utc": proxy_fields["captured_at_utc"],
                            "spread_cents": proxy_fields["spread_cents"],
                            "timestamp_skew_seconds": proxy_fields["timestamp_skew_seconds"],
                        }
                    ),
                }
            )
            sequence_id += 1

    if not rows:
        return pd.DataFrame(columns=HISTORICAL_BIDASK_L1_COLUMNS)
    frame = pd.DataFrame(rows, columns=HISTORICAL_BIDASK_L1_COLUMNS)
    return frame.sort_values(["team_side", "captured_at_utc", "source_sequence_id"], kind="mergesort").reset_index(drop=True)


def _coverage_row(context: Any, frame: pd.DataFrame, *, request: Any) -> dict[str, Any]:
    empty_row = {
        "game_id": str(getattr(context, "game_id", "") or ""),
        "game_date": _game_date_for_context(context),
        "season_phase": str(getattr(context, "season_phase", "") or ""),
        "state_source": str(getattr(context, "state_source", "") or ""),
        "quote_source_mode": "historical_bidask_l1",
        "quote_row_count": 0,
        "home_quote_row_count": 0,
        "away_quote_row_count": 0,
        "direct_bidask_quote_count": 0,
        "synthetic_quote_count": 0,
        "unresolved_quote_count": 0,
        "first_quote_at": None,
        "last_quote_at": None,
        "avg_quote_gap_seconds": None,
        "max_quote_gap_seconds": None,
        "dropout_span_count": 0,
        "max_dropout_span_seconds": None,
        "replay_poll_count": 0,
        "covered_poll_count": 0,
        "coverage_ratio": None,
    }
    if frame.empty:
        return empty_row

    quote_times = pd.to_datetime(frame["captured_at_utc"], errors="coerce", utc=True).dropna().sort_values()
    gaps = quote_times.diff().dropna().dt.total_seconds().tolist()
    side_counts = frame["team_side"].astype(str).value_counts(dropna=False).to_dict()
    capture_status = frame["capture_status"].astype(str)

    replay_poll_count = 0
    covered_poll_count = 0
    side_frames = {
        side: frame[frame["team_side"].astype(str) == side].copy()
        for side in ("home", "away")
    }
    for side_frame in side_frames.values():
        if not side_frame.empty:
            side_frame["captured_at_utc"] = pd.to_datetime(side_frame["captured_at_utc"], errors="coerce", utc=True)
            side_frame.sort_values(["captured_at_utc", "source_sequence_id"], kind="mergesort", inplace=True)
    cycle_at = getattr(context, "anchor_at", None)
    end_at = getattr(context, "end_at", None)
    if isinstance(cycle_at, datetime) and isinstance(end_at, datetime):
        while cycle_at <= end_at + timedelta(seconds=float(request.poll_interval_seconds)):
            for side, side_frame in side_frames.items():
                replay_poll_count += 1
                if side_frame.empty:
                    continue
                visible = side_frame[side_frame["captured_at_utc"] <= cycle_at]
                if visible.empty:
                    continue
                row = visible.iloc[-1]
                captured_at = _parse_datetime(row.get("captured_at_utc"))
                transport_lag_ms = _safe_float(row.get("source_latency_ms")) or 0.0
                effective_quote_at = (
                    captured_at - timedelta(milliseconds=transport_lag_ms)
                    if captured_at is not None
                    else None
                )
                quote_age_seconds = (
                    max(0.0, (cycle_at - effective_quote_at).total_seconds())
                    if effective_quote_at is not None
                    else None
                )
                if (
                    _safe_float(row.get("best_bid_price")) is not None
                    and _safe_float(row.get("best_ask_price")) is not None
                    and quote_age_seconds is not None
                    and quote_age_seconds <= float(request.quote_max_age_seconds)
                ):
                    covered_poll_count += 1
            cycle_at += timedelta(seconds=float(request.poll_interval_seconds))

    return {
        **empty_row,
        "quote_row_count": int(len(frame)),
        "home_quote_row_count": int(side_counts.get("home") or 0),
        "away_quote_row_count": int(side_counts.get("away") or 0),
        "direct_bidask_quote_count": int((capture_status == "direct_bidask").sum()),
        "synthetic_quote_count": int((capture_status == "synthetic_from_cross_side_ticks").sum()),
        "unresolved_quote_count": int((capture_status == "missing_opposite_side").sum()),
        "first_quote_at": quote_times.iloc[0] if not quote_times.empty else None,
        "last_quote_at": quote_times.iloc[-1] if not quote_times.empty else None,
        "avg_quote_gap_seconds": float(sum(gaps) / len(gaps)) if gaps else None,
        "max_quote_gap_seconds": float(max(gaps)) if gaps else None,
        "dropout_span_count": int(sum(1 for gap in gaps if gap > float(request.quote_max_age_seconds))),
        "max_dropout_span_seconds": float(max((gap for gap in gaps if gap > float(request.quote_max_age_seconds)), default=0.0)) if gaps else None,
        "replay_poll_count": int(replay_poll_count),
        "covered_poll_count": int(covered_poll_count),
        "coverage_ratio": float(covered_poll_count / replay_poll_count) if replay_poll_count else None,
    }


def build_historical_bidask_samples(
    *,
    contexts: dict[str, Any],
    season: str,
    request: Any,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    frames_by_game: dict[str, pd.DataFrame] = {}
    combined_frames: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, Any]] = []
    for game_id, context in contexts.items():
        frame = build_historical_bidask_l1_frame(context, season=season, request=request)
        frames_by_game[game_id] = frame
        if not frame.empty:
            combined_frames.append(frame)
        coverage_rows.append(_coverage_row(context, frame, request=request))
    combined_df = pd.concat(combined_frames, ignore_index=True) if combined_frames else pd.DataFrame(columns=HISTORICAL_BIDASK_L1_COLUMNS)
    coverage_df = pd.DataFrame(coverage_rows, columns=QUOTE_COVERAGE_SUMMARY_COLUMNS)
    return frames_by_game, combined_df, coverage_df


__all__ = [
    "HISTORICAL_BIDASK_L1_COLUMNS",
    "QUOTE_COVERAGE_SUMMARY_COLUMNS",
    "build_historical_bidask_samples",
    "build_proxy_quote_fields_from_cross_side_ticks",
]
