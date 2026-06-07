from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def build_indicator_frame(
    candles_df: pd.DataFrame,
    *,
    settlement_threshold: float | None = None,
    close_time: Any | None = None,
    fast_window: int = 5,
    slow_window: int = 20,
    volatility_window: int = 10,
    rsi_window: int = 14,
) -> pd.DataFrame:
    """Add simple technical indicators over exchange-backed candles."""

    if candles_df.empty:
        return pd.DataFrame()
    required = {"symbol", "opened_at", "close"}
    if missing := sorted(required - set(candles_df.columns)):
        raise ValueError(f"candles_df missing required columns: {missing}")

    frame = candles_df.copy()
    frame["opened_at"] = pd.to_datetime(frame["opened_at"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["symbol", "opened_at", "close"]).sort_values(["symbol", "opened_at"], kind="mergesort")
    groups = frame.groupby("symbol", group_keys=False)
    frame["return_1"] = groups["close"].pct_change()
    frame["momentum_3"] = groups["close"].pct_change(periods=3)
    frame["volatility"] = groups["return_1"].rolling(volatility_window, min_periods=2).std().reset_index(level=0, drop=True)
    frame["ma_fast"] = groups["close"].rolling(fast_window, min_periods=1).mean().reset_index(level=0, drop=True)
    frame["ma_slow"] = groups["close"].rolling(slow_window, min_periods=1).mean().reset_index(level=0, drop=True)
    frame["rsi"] = groups["close"].transform(lambda series: _rsi(series, window=rsi_window))
    if settlement_threshold is not None:
        frame["settlement_threshold"] = float(settlement_threshold)
        frame["distance_to_threshold"] = frame["close"] - float(settlement_threshold)
    else:
        frame["settlement_threshold"] = None
        frame["distance_to_threshold"] = None
    if close_time is not None:
        close_at = pd.to_datetime(close_time, utc=True)
        frame["time_to_close_seconds"] = (close_at - frame["opened_at"]).dt.total_seconds()
    else:
        frame["time_to_close_seconds"] = None
    return frame.reset_index(drop=True)


def build_market_indicator_frame(
    candles_df: pd.DataFrame,
    *,
    settlement_threshold: float | None = None,
    close_time: Any | None = None,
    fast_ema_window: int = 5,
    slow_ema_window: int = 15,
    bollinger_window: int = 20,
    bollinger_std: float = 2.0,
    donchian_window: int = 30,
    vwap_window: int = 15,
    volume_z_window: int = 30,
    rsi_window: int = 14,
) -> pd.DataFrame:
    """Build the V4/V5 crypto underlying indicator frame from candles.

    The frame is exchange-backed and read-only.  It intentionally computes
    explainable components that can later be backtested independently:
    target-relative EMA momentum, volume-weighted pressure, and
    support/resistance band confluence.
    """

    if candles_df.empty:
        return pd.DataFrame()
    required = {"symbol", "opened_at", "close"}
    if missing := sorted(required - set(candles_df.columns)):
        raise ValueError(f"candles_df missing required columns: {missing}")

    frame = candles_df.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["opened_at"] = pd.to_datetime(frame["opened_at"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        if column not in frame.columns:
            frame[column] = frame["close"] if column != "volume" else 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["open"] = frame["open"].fillna(frame["close"])
    frame["high"] = frame["high"].fillna(frame["close"])
    frame["low"] = frame["low"].fillna(frame["close"])
    frame["volume"] = frame["volume"].fillna(0.0)
    frame = frame.dropna(subset=["symbol", "opened_at", "close"]).sort_values(["symbol", "opened_at"], kind="mergesort")
    if frame.empty:
        return pd.DataFrame()

    groups = frame.groupby("symbol", group_keys=False)
    frame["elapsed_seconds"] = groups["opened_at"].diff().dt.total_seconds().replace(0.0, np.nan)
    frame["return_1"] = groups["close"].pct_change()
    frame["log_return_1"] = groups["close"].transform(lambda series: np.log(series).diff())
    frame["return_per_second"] = frame["return_1"] / frame["elapsed_seconds"].fillna(60.0)
    frame["volatility_per_second_5m"] = (
        groups["return_per_second"].rolling(5, min_periods=2).std().reset_index(level=0, drop=True)
    )
    frame["volatility_per_second_1h"] = (
        groups["return_per_second"].rolling(60, min_periods=10).std().reset_index(level=0, drop=True)
    )
    frame["volatility_per_second_1d"] = (
        groups["return_per_second"].rolling(1440, min_periods=60).std().reset_index(level=0, drop=True)
    )
    frame["ema_fast"] = groups["close"].transform(lambda series: series.ewm(span=fast_ema_window, adjust=False).mean())
    frame["ema_slow"] = groups["close"].transform(lambda series: series.ewm(span=slow_ema_window, adjust=False).mean())
    frame["ema_spread"] = frame["ema_fast"] - frame["ema_slow"]
    frame["ema_fast_slope"] = groups["ema_fast"].diff(periods=3)
    frame["ema_slow_slope"] = groups["ema_slow"].diff(periods=3)
    frame["trend_15m"] = _trend_from_window(frame, groups, window=15)
    frame["trend_30m"] = _trend_from_window(frame, groups, window=30)
    frame["trend_1h"] = _trend_from_window(frame, groups, window=60)

    frame["bb_mid_20m"] = groups["close"].rolling(bollinger_window, min_periods=5).mean().reset_index(level=0, drop=True)
    frame["bb_std_20m"] = groups["close"].rolling(bollinger_window, min_periods=5).std().reset_index(level=0, drop=True)
    frame["bb_upper_20m"] = frame["bb_mid_20m"] + float(bollinger_std) * frame["bb_std_20m"]
    frame["bb_lower_20m"] = frame["bb_mid_20m"] - float(bollinger_std) * frame["bb_std_20m"]
    frame["bb_width_20m"] = (frame["bb_upper_20m"] - frame["bb_lower_20m"]) / frame["bb_mid_20m"].replace(0.0, np.nan)
    frame["donchian_high_30m"] = (
        groups["high"].rolling(donchian_window, min_periods=5).max().reset_index(level=0, drop=True)
    )
    frame["donchian_low_30m"] = (
        groups["low"].rolling(donchian_window, min_periods=5).min().reset_index(level=0, drop=True)
    )
    frame["nearest_resistance"] = frame[["bb_upper_20m", "donchian_high_30m"]].min(axis=1)
    frame["nearest_support"] = frame[["bb_lower_20m", "donchian_low_30m"]].max(axis=1)
    frame["distance_to_resistance"] = frame["nearest_resistance"] - frame["close"]
    frame["distance_to_support"] = frame["close"] - frame["nearest_support"]
    frame["band_event"] = _band_event(frame)
    frame["band_confidence"] = _band_confidence(frame)

    typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    pv = typical_price * frame["volume"]
    volume_sum = groups["volume"].rolling(vwap_window, min_periods=3).sum().reset_index(level=0, drop=True)
    pv_sum = pv.groupby(frame["symbol"], group_keys=False).rolling(vwap_window, min_periods=3).sum().reset_index(level=0, drop=True)
    frame["rolling_vwap_15m"] = pv_sum / volume_sum.replace(0.0, np.nan)
    frame["price_vs_vwap"] = frame["close"] - frame["rolling_vwap_15m"]
    volume_mean = groups["volume"].rolling(volume_z_window, min_periods=5).mean().reset_index(level=0, drop=True)
    volume_std = groups["volume"].rolling(volume_z_window, min_periods=5).std().reset_index(level=0, drop=True)
    frame["volume_zscore_30m"] = (frame["volume"] - volume_mean) / volume_std.replace(0.0, np.nan)
    candle_range = (frame["high"] - frame["low"]).replace(0.0, np.nan)
    frame["close_location_value"] = ((frame["close"] - frame["low"]) / candle_range).clip(0.0, 1.0).fillna(0.5)
    frame["pressure_direction"] = _pressure_direction(frame)
    frame["pressure_confidence"] = _pressure_confidence(frame)

    previous_close = groups["close"].shift(1)
    true_range = pd.concat(
        [
            (frame["high"] - frame["low"]).abs(),
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_14m"] = true_range.groupby(frame["symbol"], group_keys=False).rolling(14, min_periods=3).mean().reset_index(level=0, drop=True)
    frame["rsi"] = groups["close"].transform(lambda series: _rsi(series, window=rsi_window))

    if settlement_threshold is not None:
        threshold = float(settlement_threshold)
        frame["settlement_threshold"] = threshold
        frame["target_delta_abs"] = (frame["close"] - threshold).abs()
        frame["target_delta_signed_up"] = frame["close"] - threshold
        frame["target_delta_signed_down"] = threshold - frame["close"]
        frame["distance_threshold_to_resistance"] = frame["nearest_resistance"] - threshold
        frame["distance_threshold_to_support"] = threshold - frame["nearest_support"]
    else:
        frame["settlement_threshold"] = None
        frame["target_delta_abs"] = None
        frame["target_delta_signed_up"] = None
        frame["target_delta_signed_down"] = None
        frame["distance_threshold_to_resistance"] = None
        frame["distance_threshold_to_support"] = None
    if close_time is not None:
        close_at = pd.to_datetime(close_time, utc=True)
        frame["time_to_close_seconds"] = (close_at - frame["opened_at"]).dt.total_seconds()
    else:
        frame["time_to_close_seconds"] = None

    frame["momentum_direction"] = _momentum_direction(frame)
    frame["momentum_confidence"] = _momentum_confidence(frame)
    frame["history_rows_observed"] = groups.cumcount() + 1
    frame["history_minutes_observed"] = frame["history_rows_observed"].astype(float)
    frame["history_quality"] = np.select(
        [
            frame["history_rows_observed"] >= 60,
            frame["history_rows_observed"] >= 30,
            frame["history_rows_observed"] >= 15,
        ],
        ["full_initial", "indicator_ready", "momentum_only"],
        default="warming",
    )
    return frame.reset_index(drop=True)


def build_indicator_snapshots(
    candles_df: pd.DataFrame,
    *,
    settlement_threshold: float | None = None,
    close_time: Any | None = None,
    interval: str = "1m",
    source: str = "local_indicator_engine",
) -> list[dict[str, Any]]:
    """Return latest indicator snapshots for each symbol."""

    frame = build_market_indicator_frame(candles_df, settlement_threshold=settlement_threshold, close_time=close_time)
    if frame.empty:
        return []
    latest = frame.sort_values(["symbol", "opened_at"], kind="mergesort").groupby("symbol", as_index=False).tail(1)
    computed_at = pd.Timestamp.utcnow().isoformat()
    snapshots: list[dict[str, Any]] = []
    for row in latest.to_dict(orient="records"):
        symbol = str(row.get("symbol") or "").upper()
        candle_opened_at = row.get("opened_at")
        snapshots.extend(
            [
                {
                    "symbol": symbol,
                    "interval": interval,
                    "indicator_id": "target_relative_ema_momentum_v1",
                    "computed_at_utc": computed_at,
                    "candle_opened_at_utc": candle_opened_at,
                    "source": source,
                    "direction": row.get("momentum_direction"),
                    "confidence": row.get("momentum_confidence"),
                    "signal_value": row.get("ema_spread"),
                    "components_json": _components(
                        row,
                        [
                            "close",
                            "ema_fast",
                            "ema_slow",
                            "ema_spread",
                            "ema_fast_slope",
                            "ema_slow_slope",
                            "target_delta_abs",
                            "target_delta_signed_up",
                            "target_delta_signed_down",
                            "trend_15m",
                            "trend_30m",
                            "trend_1h",
                        ],
                    ),
                    "quality_flags_json": _quality_flags(row),
                },
                {
                    "symbol": symbol,
                    "interval": interval,
                    "indicator_id": "volume_weighted_pressure_v1",
                    "computed_at_utc": computed_at,
                    "candle_opened_at_utc": candle_opened_at,
                    "source": source,
                    "direction": row.get("pressure_direction"),
                    "confidence": row.get("pressure_confidence"),
                    "signal_value": row.get("price_vs_vwap"),
                    "components_json": _components(
                        row,
                        [
                            "close",
                            "rolling_vwap_15m",
                            "price_vs_vwap",
                            "volume_zscore_30m",
                            "close_location_value",
                        ],
                    ),
                    "quality_flags_json": _quality_flags(row),
                },
                {
                    "symbol": symbol,
                    "interval": interval,
                    "indicator_id": "support_resistance_band_confluence_v1",
                    "computed_at_utc": computed_at,
                    "candle_opened_at_utc": candle_opened_at,
                    "source": source,
                    "direction": row.get("band_event"),
                    "confidence": row.get("band_confidence"),
                    "signal_value": row.get("distance_to_resistance"),
                    "components_json": _components(
                        row,
                        [
                            "close",
                            "bb_mid_20m",
                            "bb_upper_20m",
                            "bb_lower_20m",
                            "bb_width_20m",
                            "donchian_high_30m",
                            "donchian_low_30m",
                            "nearest_resistance",
                            "nearest_support",
                            "distance_to_resistance",
                            "distance_to_support",
                            "distance_threshold_to_resistance",
                            "distance_threshold_to_support",
                        ],
                    ),
                    "quality_flags_json": _quality_flags(row),
                },
            ]
        )
    return snapshots


def build_event_indicator_context(
    candles_df: pd.DataFrame,
    *,
    event_id: str | None,
    market_slug: str | None,
    symbol: str,
    side: str,
    event_threshold_price: float,
    event_end_at_utc: Any | None,
    interval: str = "1m",
) -> dict[str, Any]:
    """Build the latest event-relative indicator context for one side."""

    frame = build_market_indicator_frame(
        candles_df,
        settlement_threshold=event_threshold_price,
        close_time=event_end_at_utc,
    )
    if frame.empty:
        return {
            "symbol": str(symbol).upper(),
            "side": side,
            "event_id": event_id,
            "market_slug": market_slug,
            "blocker_summary": {"blocked": True, "reason": "missing_candles"},
        }
    filtered = frame[frame["symbol"].astype(str).str.upper() == str(symbol).upper()]
    if filtered.empty:
        filtered = frame
    row = filtered.sort_values("opened_at", kind="mergesort").iloc[-1].to_dict()
    normalized_side = str(side or "").strip().lower()
    signed = row.get("target_delta_signed_up") if normalized_side.startswith("up") else row.get("target_delta_signed_down")
    indicator_summary = {
        "interval": interval,
        "momentum_direction": row.get("momentum_direction"),
        "momentum_confidence": _json_float(row.get("momentum_confidence")),
        "pressure_direction": row.get("pressure_direction"),
        "pressure_confidence": _json_float(row.get("pressure_confidence")),
        "band_event": row.get("band_event"),
        "band_confidence": _json_float(row.get("band_confidence")),
        "volatility_per_second_5m": _json_float(row.get("volatility_per_second_5m")),
        "volatility_per_second_1h": _json_float(row.get("volatility_per_second_1h")),
    }
    blockers = []
    if row.get("history_rows_observed", 0) < 15:
        blockers.append("indicator_history_warming")
    if event_end_at_utc is None:
        blockers.append("missing_event_end")
    return {
        "event_id": event_id,
        "market_slug": market_slug,
        "symbol": str(symbol).upper(),
        "side": side,
        "event_threshold_price": float(event_threshold_price),
        "event_end_at_utc": event_end_at_utc,
        "computed_at_utc": pd.Timestamp.utcnow().isoformat(),
        "underlying_price": _json_float(row.get("close")),
        "target_delta_abs": _json_float(row.get("target_delta_abs")),
        "target_delta_signed_for_side": _json_float(signed),
        "trend_15m": row.get("trend_15m"),
        "trend_30m": row.get("trend_30m"),
        "trend_1h": row.get("trend_1h"),
        "indicator_summary_json": indicator_summary,
        "blocker_summary_json": {"blocked": bool(blockers), "blockers": blockers},
    }


def _rsi(series: pd.Series, *, window: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(window, min_periods=1).mean()
    avg_loss = loss.rolling(window, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _trend_from_window(frame: pd.DataFrame, groups: Any, *, window: int) -> pd.Series:
    baseline = groups["close"].shift(window)
    delta = frame["close"] - baseline
    threshold = frame["close"].abs() * 0.0005
    return pd.Series(np.select([delta > threshold, delta < -threshold], ["up", "down"], default="sideways"), index=frame.index)


def _momentum_direction(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        np.select(
            [
                (frame["ema_fast"] > frame["ema_slow"]) & (frame["ema_fast_slope"] > 0),
                (frame["ema_fast"] < frame["ema_slow"]) & (frame["ema_fast_slope"] < 0),
            ],
            ["up", "down"],
            default="neutral",
        ),
        index=frame.index,
    )


def _momentum_confidence(frame: pd.DataFrame) -> pd.Series:
    scale = frame["atr_14m"].replace(0.0, np.nan).fillna(frame["close"].abs() * 0.001)
    spread_score = (frame["ema_spread"].abs() / scale).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    slope_score = (frame["ema_fast_slope"].abs() / scale).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return ((spread_score * 0.65 + slope_score * 0.35) / 2.0).clip(0.0, 1.0)


def _pressure_direction(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        np.select(
            [
                (frame["price_vs_vwap"] > 0) & (frame["close_location_value"] >= 0.58),
                (frame["price_vs_vwap"] < 0) & (frame["close_location_value"] <= 0.42),
            ],
            ["buy_pressure", "sell_pressure"],
            default="neutral",
        ),
        index=frame.index,
    )


def _pressure_confidence(frame: pd.DataFrame) -> pd.Series:
    vwap_scale = (frame["rolling_vwap_15m"].abs() * 0.001).replace(0.0, np.nan)
    price_score = (frame["price_vs_vwap"].abs() / vwap_scale).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    location_score = (frame["close_location_value"] - 0.5).abs() * 2.0
    volume_score = (frame["volume_zscore_30m"].fillna(0.0).clip(lower=0.0) / 3.0).clip(0.0, 1.0)
    return (price_score.clip(0.0, 1.0) * 0.45 + location_score.clip(0.0, 1.0) * 0.35 + volume_score * 0.20).clip(0.0, 1.0)


def _band_event(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        np.select(
            [
                frame["close"] > frame["bb_upper_20m"],
                frame["close"] < frame["bb_lower_20m"],
                (frame["high"] >= frame["bb_upper_20m"]) & (frame["close"] < frame["bb_upper_20m"]) & (frame["return_1"] < 0),
                (frame["low"] <= frame["bb_lower_20m"]) & (frame["close"] > frame["bb_lower_20m"]) & (frame["return_1"] > 0),
                (frame["distance_to_resistance"].abs() / frame["close"].abs()) <= 0.0015,
                (frame["distance_to_support"].abs() / frame["close"].abs()) <= 0.0015,
            ],
            ["breakout", "breakdown", "resistance_rejection", "support_bounce", "resistance_touch", "support_touch"],
            default="none",
        ),
        index=frame.index,
    )


def _band_confidence(frame: pd.DataFrame) -> pd.Series:
    width_score = (frame["bb_width_20m"].fillna(0.0).abs() / 0.01).clip(0.0, 1.0)
    distance = frame[["distance_to_resistance", "distance_to_support"]].abs().min(axis=1)
    distance_score = (1.0 - (distance / (frame["close"].abs() * 0.003).replace(0.0, np.nan))).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0.0).clip(0.0, 1.0)
    event_score = np.where(frame["band_event"] == "none", 0.0, 0.45)
    return (event_score + width_score * 0.25 + distance_score * 0.30).clip(0.0, 1.0)


def _components(row: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: _json_float(row.get(key)) if isinstance(row.get(key), (float, int, np.floating, np.integer)) else row.get(key) for key in keys}


def _quality_flags(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "history_quality": row.get("history_quality"),
        "history_rows_observed": int(row.get("history_rows_observed") or 0),
        "history_minutes_observed": _json_float(row.get("history_minutes_observed")),
    }


def _json_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "build_event_indicator_context",
    "build_indicator_frame",
    "build_indicator_snapshots",
    "build_market_indicator_frame",
]
