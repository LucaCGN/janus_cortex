from __future__ import annotations

from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.statistical_signals import (
    STATISTICAL_COMPONENT_IDS,
    StatisticalComponentRunner,
    make_statistical_signal,
    run_statistical_signal_backtest,
)


COMPONENT_1_ID = "stat_vwap_ema_trend_confluence"
COMPONENT_2_ID = "stat_volume_profile_acceptance_rejection"
COMPONENT_3_ID = "stat_support_resistance_bounce_decay"
COMPONENT_4_ID = "stat_false_breakout_reversal_filter"
COMPONENT_6_ID = "stat_volatility_regime_filter"
COMPONENT_8_ID = "stat_settlement_threshold_distance_model"


def registered_statistical_component_runners(component_ids: tuple[str, ...] | None = None) -> dict[str, StatisticalComponentRunner]:
    wanted = set(component_ids or STATISTICAL_COMPONENT_IDS)
    runners: dict[str, StatisticalComponentRunner] = {}
    if COMPONENT_1_ID in wanted:
        runners[COMPONENT_1_ID] = run_vwap_ema_trend_confluence
    if COMPONENT_2_ID in wanted:
        runners[COMPONENT_2_ID] = run_volume_profile_acceptance_rejection
    if COMPONENT_3_ID in wanted:
        runners[COMPONENT_3_ID] = run_support_resistance_bounce_decay
    if COMPONENT_4_ID in wanted:
        runners[COMPONENT_4_ID] = run_false_breakout_reversal_filter
    if COMPONENT_6_ID in wanted:
        runners[COMPONENT_6_ID] = run_volatility_regime_filter
    if COMPONENT_8_ID in wanted:
        runners[COMPONENT_8_ID] = run_settlement_threshold_distance_model
    return runners


def run_registered_statistical_component_backtests(
    panel_df: pd.DataFrame,
    *,
    component_ids: tuple[str, ...] = STATISTICAL_COMPONENT_IDS,
    allow_midpoint_entries: bool = False,
) -> dict[str, Any]:
    return run_statistical_signal_backtest(
        panel_df,
        component_ids=component_ids,
        component_runners=registered_statistical_component_runners(component_ids),
        allow_midpoint_entries=allow_midpoint_entries,
    )


def run_vwap_ema_trend_confluence(frame: pd.DataFrame) -> list[dict[str, Any]]:
    component_id = COMPONENT_1_ID
    required = ("vwap", "ma_fast", "ma_slow", "momentum_3", "distance_to_threshold", "underlying_close")
    blockers = _missing_required(frame, required)
    if blockers:
        return [_blocked_signal(component_id, blockers, required_data_quality=["vwap", "ema_trend", "momentum", "threshold_distance"])]
    work = frame.copy()
    for column in required:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_trend_side"] = work.apply(_vwap_ema_side, axis=1)
    work = work[work["_trend_side"].isin({"up", "down"})].copy()
    return _first_matching_side_signals(
        component_id,
        work,
        side_column="_trend_side",
        feature_builder=lambda row: {
            "underlying_close": row.get("underlying_close"),
            "vwap": row.get("vwap"),
            "ma_fast": row.get("ma_fast"),
            "ma_slow": row.get("ma_slow"),
            "momentum_3": row.get("momentum_3"),
            "distance_to_threshold": row.get("distance_to_threshold"),
            "trend_alignment": row.get("_trend_side"),
            "statistical_only": True,
            "profile_signal_confirmation_used": False,
        },
        confidence_builder=lambda row: _bounded_confidence(
            0.54
            + min(abs(_to_float(row.get("momentum_3")) or 0.0) * 25.0, 0.14)
            + min(abs(_to_float(row.get("distance_to_threshold")) or 0.0) / max(abs(_to_float(row.get("underlying_close")) or 1.0), 1.0) * 15.0, 0.12)
            + (0.08 if _same_direction(row.get("underlying_close"), row.get("vwap"), row.get("_trend_side")) else 0.0)
        ),
        required_data_quality=["vwap", "ema_trend", "momentum", "threshold_distance", "bid_ask", "settlement_label"],
    )


def run_volume_profile_acceptance_rejection(frame: pd.DataFrame) -> list[dict[str, Any]]:
    component_id = COMPONENT_2_ID
    required = ("vwap", "volume", "volume_profile_poc", "profile_value_area_high", "profile_value_area_low", "underlying_close")
    blockers = _missing_required(frame, required)
    if blockers:
        return [_blocked_signal(component_id, blockers, required_data_quality=["volume", "vwap", "volume_profile"])]
    work = frame.copy()
    for column in required + ("volume_ma",):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_profile_side"] = work.apply(_volume_profile_side, axis=1)
    work = work[work["_profile_side"].isin({"up", "down"})].copy()
    return _first_matching_side_signals(
        component_id,
        work,
        side_column="_profile_side",
        feature_builder=lambda row: {
            "underlying_close": row.get("underlying_close"),
            "vwap": row.get("vwap"),
            "volume": row.get("volume"),
            "volume_ma": row.get("volume_ma"),
            "volume_profile_poc": row.get("volume_profile_poc"),
            "profile_value_area_high": row.get("profile_value_area_high"),
            "profile_value_area_low": row.get("profile_value_area_low"),
            "profile_acceptance_side": row.get("_profile_side"),
            "statistical_only": True,
            "profile_signal_confirmation_used": False,
        },
        confidence_builder=lambda row: _bounded_confidence(
            0.52
            + min(_volume_ratio(row) * 0.08, 0.16)
            + min(abs((_to_float(row.get("underlying_close")) or 0.0) - (_to_float(row.get("volume_profile_poc")) or 0.0)) / max(abs(_to_float(row.get("underlying_close")) or 1.0), 1.0) * 20.0, 0.14)
        ),
        required_data_quality=["volume", "vwap", "volume_profile", "bid_ask", "settlement_label"],
    )


def run_support_resistance_bounce_decay(frame: pd.DataFrame) -> list[dict[str, Any]]:
    component_id = COMPONENT_3_ID
    work = _with_deterministic_levels(frame)
    required = ("underlying_close", "rolling_support", "rolling_resistance", "time_to_close_seconds", "rejection_strength")
    blockers = _missing_required(work, required)
    if blockers:
        return [_blocked_signal(component_id, blockers + ["insufficient_candle_granularity_for_support_resistance"], required_data_quality=["support_resistance_levels", "rejection_strength"])]
    for column in required + ("prior_support_touches", "prior_resistance_touches", "volatility"):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_bounce_side"] = work.apply(_support_resistance_bounce_side, axis=1)
    work = work[work["_bounce_side"].isin({"up", "down"})].copy()
    return _first_matching_side_signals(
        component_id,
        work,
        side_column="_bounce_side",
        feature_builder=lambda row: {
            "underlying_close": row.get("underlying_close"),
            "rolling_support": row.get("rolling_support"),
            "rolling_resistance": row.get("rolling_resistance"),
            "prior_support_touches": row.get("prior_support_touches"),
            "prior_resistance_touches": row.get("prior_resistance_touches"),
            "rejection_strength": row.get("rejection_strength"),
            "time_to_close_seconds": row.get("time_to_close_seconds"),
            "level_distance": _level_distance(row),
            "statistical_only": True,
            "profile_signal_confirmation_used": False,
        },
        confidence_builder=lambda row: _bounded_confidence(
            0.51
            + min(abs(_to_float(row.get("rejection_strength")) or 0.0) * 0.16, 0.18)
            + min((_touch_count_for_side(row) or 0) * 0.04, 0.16)
            + (0.06 if 45 <= (_to_float(row.get("time_to_close_seconds")) or 0.0) <= 240 else 0.0)
        ),
        required_data_quality=["support_resistance_levels", "rejection_strength", "time_remaining", "bid_ask", "settlement_label"],
    )


def run_false_breakout_reversal_filter(frame: pd.DataFrame) -> list[dict[str, Any]]:
    component_id = COMPONENT_4_ID
    required = ("opening_range_high", "opening_range_low", "underlying_close", "time_to_close_seconds", "volatility")
    blockers = _missing_required(frame, required)
    if blockers:
        return [_blocked_signal(component_id, blockers + ["missing_opening_range_path_fields"], required_data_quality=["opening_range", "breakout_path", "volatility"])]
    work = frame.copy()
    for column in required + ("prior_underlying_close",):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_false_break_side"] = work.apply(_false_breakout_side, axis=1)
    work = work[work["_false_break_side"].isin({"up", "down"})].copy()
    return _first_matching_side_signals(
        component_id,
        work,
        side_column="_false_break_side",
        feature_builder=lambda row: {
            "underlying_close": row.get("underlying_close"),
            "prior_underlying_close": row.get("prior_underlying_close"),
            "opening_range_high": row.get("opening_range_high"),
            "opening_range_low": row.get("opening_range_low"),
            "broke_above_opening_range": bool(row.get("broke_above_opening_range")),
            "broke_below_opening_range": bool(row.get("broke_below_opening_range")),
            "returned_inside_opening_range": bool(row.get("returned_inside_opening_range")),
            "time_to_close_seconds": row.get("time_to_close_seconds"),
            "volatility": row.get("volatility"),
            "statistical_only": True,
            "profile_signal_confirmation_used": False,
        },
        confidence_builder=lambda row: _bounded_confidence(
            0.53
            + (0.12 if bool(row.get("returned_inside_opening_range")) else 0.0)
            + min(abs((_to_float(row.get("underlying_close")) or 0.0) - (_to_float(row.get("prior_underlying_close")) or _to_float(row.get("underlying_close")) or 0.0)) / max(abs(_to_float(row.get("underlying_close")) or 1.0), 1.0) * 25.0, 0.12)
            + (0.06 if (_to_float(row.get("time_to_close_seconds")) or 0.0) >= 45 else 0.0)
        ),
        required_data_quality=["opening_range", "breakout_path", "volatility", "bid_ask", "settlement_label"],
    )


def run_volatility_regime_filter(frame: pd.DataFrame) -> list[dict[str, Any]]:
    component_id = COMPONENT_6_ID
    required = ("volatility", "spread", "time_to_close_seconds", "distance_to_threshold", "momentum_3")
    blockers = _missing_required(frame, required)
    if blockers:
        return [_blocked_signal(component_id, blockers, required_data_quality=["volatility", "spread", "time_remaining"])]
    work = frame.copy()
    for column in required + ("range_expansion", "odds_instability"):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    unstable_blockers = _volatility_regime_blockers(work)
    if unstable_blockers:
        return [_blocked_signal(component_id, unstable_blockers, required_data_quality=["volatility_regime", "odds_stability", "spread"])]
    work["_volatility_side"] = work.apply(_directional_context_side, axis=1)
    work = work[work["_volatility_side"].isin({"up", "down"})].copy()
    return _first_matching_side_signals(
        component_id,
        work,
        side_column="_volatility_side",
        feature_builder=lambda row: {
            "regime_label": _volatility_regime_label(row),
            "volatility": row.get("volatility"),
            "range_expansion": row.get("range_expansion"),
            "odds_instability": row.get("odds_instability"),
            "spread": row.get("spread"),
            "time_to_close_seconds": row.get("time_to_close_seconds"),
            "recommended_action": "confirm_or_normal_size",
            "statistical_only": True,
            "profile_signal_confirmation_used": False,
        },
        confidence_builder=lambda row: _bounded_confidence(
            0.56
            + (0.08 if _volatility_regime_label(row) == "low_stable" else 0.03)
            + min(abs(_to_float(row.get("momentum_3")) or 0.0) * 20.0, 0.08)
        ),
        required_data_quality=["volatility_regime", "odds_stability", "spread", "bid_ask", "settlement_label"],
    )


def run_settlement_threshold_distance_model(frame: pd.DataFrame) -> list[dict[str, Any]]:
    component_id = COMPONENT_8_ID
    required = ("settlement_threshold", "underlying_close", "distance_to_threshold", "time_to_close_seconds", "volatility", "momentum_3")
    blockers = _missing_required(frame, required)
    if "settlement_threshold_stale" in frame.columns and frame["settlement_threshold_stale"].fillna(False).astype(bool).any():
        blockers.append("stale_settlement_threshold")
    if blockers:
        return [_blocked_signal(component_id, blockers, required_data_quality=["threshold", "distance", "volatility", "label"])]
    work = frame.copy()
    for column in required:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_distance_z"] = work.apply(_threshold_distance_z, axis=1)
    if (work["_distance_z"].abs() < 0.25).any():
        return [_blocked_signal(component_id, ["near_threshold_uncertainty"], required_data_quality=["threshold", "distance", "volatility", "label"])]
    work["_distance_side"] = work.apply(_threshold_distance_side, axis=1)
    work = work[work["_distance_side"].isin({"up", "down"})].copy()
    return _first_matching_side_signals(
        component_id,
        work,
        side_column="_distance_side",
        feature_builder=lambda row: {
            "settlement_threshold": row.get("settlement_threshold"),
            "underlying_close": row.get("underlying_close"),
            "distance_to_threshold": row.get("distance_to_threshold"),
            "time_to_close_seconds": row.get("time_to_close_seconds"),
            "volatility": row.get("volatility"),
            "momentum_3": row.get("momentum_3"),
            "volatility_scaled_distance": row.get("_distance_z"),
            "statistical_only": True,
            "profile_signal_confirmation_used": False,
        },
        confidence_builder=lambda row: _bounded_confidence(0.52 + min(abs(_to_float(row.get("_distance_z")) or 0.0) * 0.08, 0.30)),
        required_data_quality=["threshold", "distance", "volatility", "bid_ask", "settlement_label"],
    )


def _first_matching_side_signals(
    component_id: str,
    frame: pd.DataFrame,
    *,
    side_column: str,
    feature_builder: Any,
    confidence_builder: Any,
    required_data_quality: list[str],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if frame.empty:
        return signals
    work = frame.copy()
    work["_outcome_norm"] = work["outcome"].apply(_normalize_side)
    selected = work[work["_outcome_norm"] == work[side_column]].copy()
    if selected.empty:
        return signals
    selected = selected.sort_values("observed_at", kind="mergesort").groupby("market_id", sort=False).head(1)
    for _, row in selected.iterrows():
        signals.append(
            make_statistical_signal(
                component_id,
                row,
                side=str(row[side_column]),
                confidence=confidence_builder(row),
                feature_values=feature_builder(row),
                required_data_quality=required_data_quality,
            )
        )
    return signals


def _vwap_ema_side(row: pd.Series) -> str | None:
    close = _to_float(row.get("underlying_close"))
    vwap = _to_float(row.get("vwap"))
    ma_fast = _to_float(row.get("ma_fast"))
    ma_slow = _to_float(row.get("ma_slow"))
    momentum = _to_float(row.get("momentum_3"))
    distance = _to_float(row.get("distance_to_threshold"))
    if None in (close, vwap, ma_fast, ma_slow, momentum, distance):
        return None
    if close > vwap and ma_fast > ma_slow and momentum > 0 and distance > 0:
        return "up"
    if close < vwap and ma_fast < ma_slow and momentum < 0 and distance < 0:
        return "down"
    return None


def _volume_profile_side(row: pd.Series) -> str | None:
    close = _to_float(row.get("underlying_close"))
    vwap = _to_float(row.get("vwap"))
    value_high = _to_float(row.get("profile_value_area_high"))
    value_low = _to_float(row.get("profile_value_area_low"))
    volume = _to_float(row.get("volume"))
    volume_ma = _to_float(row.get("volume_ma")) or volume
    if None in (close, vwap, value_high, value_low, volume, volume_ma):
        return None
    if volume < volume_ma:
        return None
    if close > value_high and close > vwap:
        return "up"
    if close < value_low and close < vwap:
        return "down"
    return None


def _with_deterministic_levels(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "rolling_support" in work.columns and "rolling_resistance" in work.columns:
        return work
    if not {"underlying_low", "underlying_high"}.issubset(work.columns):
        return work
    work = work.sort_values(["market_id", "observed_at"], kind="mergesort").copy()
    work["underlying_low"] = pd.to_numeric(work["underlying_low"], errors="coerce")
    work["underlying_high"] = pd.to_numeric(work["underlying_high"], errors="coerce")
    groups = work.groupby("market_id", group_keys=False)
    work["rolling_support"] = groups["underlying_low"].rolling(5, min_periods=1).min().reset_index(level=0, drop=True)
    work["rolling_resistance"] = groups["underlying_high"].rolling(5, min_periods=1).max().reset_index(level=0, drop=True)
    return work.sort_index()


def _support_resistance_bounce_side(row: pd.Series) -> str | None:
    close = _to_float(row.get("underlying_close"))
    support = _to_float(row.get("rolling_support"))
    resistance = _to_float(row.get("rolling_resistance"))
    rejection = _to_float(row.get("rejection_strength"))
    time_remaining = _to_float(row.get("time_to_close_seconds"))
    if None in (close, support, resistance, rejection, time_remaining):
        return None
    tolerance = max(abs(close) * 0.0015, abs(_to_float(row.get("volatility")) or 0.0) * abs(close) * 2.0, 0.5)
    if 30 <= time_remaining <= 270 and abs(close - support) <= tolerance and rejection > 0 and (_to_float(row.get("prior_support_touches")) or 0.0) >= 2:
        return "up"
    if 30 <= time_remaining <= 270 and abs(close - resistance) <= tolerance and rejection < 0 and (_to_float(row.get("prior_resistance_touches")) or 0.0) >= 2:
        return "down"
    return None


def _false_breakout_side(row: pd.Series) -> str | None:
    close = _to_float(row.get("underlying_close"))
    high = _to_float(row.get("opening_range_high"))
    low = _to_float(row.get("opening_range_low"))
    time_remaining = _to_float(row.get("time_to_close_seconds"))
    if None in (close, high, low, time_remaining) or time_remaining < 30:
        return None
    broke_above = bool(row.get("broke_above_opening_range"))
    broke_below = bool(row.get("broke_below_opening_range"))
    returned_inside = bool(row.get("returned_inside_opening_range")) or low < close < high
    if broke_above and returned_inside and close < high:
        return "down"
    if broke_below and returned_inside and close > low:
        return "up"
    return None


def _volatility_regime_blockers(frame: pd.DataFrame) -> list[str]:
    blockers: list[str] = []
    if (pd.to_numeric(frame["volatility"], errors="coerce") >= 0.0025).any():
        blockers.append("volatility_regime_block:high_volatility")
    if "range_expansion" in frame.columns and (pd.to_numeric(frame["range_expansion"], errors="coerce") >= 0.0100).any():
        blockers.append("volatility_regime_block:range_expansion")
    if "odds_instability" in frame.columns and (pd.to_numeric(frame["odds_instability"], errors="coerce") >= 0.0800).any():
        blockers.append("volatility_regime_block:odds_instability")
    if (pd.to_numeric(frame["spread"], errors="coerce") >= 0.0600).any():
        blockers.append("volatility_regime_block:wide_spread")
    if (pd.to_numeric(frame["time_to_close_seconds"], errors="coerce") < 20).any():
        blockers.append("volatility_regime_block:late_event_reversal_risk")
    return sorted(set(blockers))


def _volatility_regime_label(row: pd.Series) -> str:
    volatility = _to_float(row.get("volatility")) or 0.0
    range_expansion = _to_float(row.get("range_expansion")) or 0.0
    odds_instability = _to_float(row.get("odds_instability")) or 0.0
    if volatility < 0.0007 and range_expansion < 0.003 and odds_instability < 0.025:
        return "low_stable"
    if volatility < 0.0015 and range_expansion < 0.007 and odds_instability < 0.050:
        return "medium_stable"
    return "elevated_watch"


def _directional_context_side(row: pd.Series) -> str | None:
    distance = _to_float(row.get("distance_to_threshold"))
    momentum = _to_float(row.get("momentum_3"))
    if distance is None and momentum is None:
        return None
    if distance is not None and distance != 0:
        return "up" if distance > 0 else "down"
    return "up" if (momentum or 0.0) >= 0 else "down"


def _threshold_distance_z(row: pd.Series) -> float | None:
    close = abs(_to_float(row.get("underlying_close")) or 0.0)
    distance = _to_float(row.get("distance_to_threshold"))
    volatility = abs(_to_float(row.get("volatility")) or 0.0)
    time_remaining = max(_to_float(row.get("time_to_close_seconds")) or 1.0, 1.0)
    if distance is None or close <= 0 or volatility <= 0:
        return None
    dollar_sigma = max(close * volatility * (time_remaining / 60.0) ** 0.5, 1.0)
    return distance / dollar_sigma


def _threshold_distance_side(row: pd.Series) -> str | None:
    distance_z = _to_float(row.get("_distance_z"))
    momentum = _to_float(row.get("momentum_3")) or 0.0
    if distance_z is None or abs(distance_z) < 0.25:
        return None
    if distance_z > 0:
        return "up" if momentum >= -0.003 else None
    return "down" if momentum <= 0.003 else None


def _level_distance(row: pd.Series) -> float | None:
    side = _normalize_side(row.get("_bounce_side"))
    close = _to_float(row.get("underlying_close"))
    if close is None:
        return None
    if side == "up":
        support = _to_float(row.get("rolling_support"))
        return None if support is None else close - support
    if side == "down":
        resistance = _to_float(row.get("rolling_resistance"))
        return None if resistance is None else resistance - close
    return None


def _touch_count_for_side(row: pd.Series) -> float | None:
    side = _normalize_side(row.get("_bounce_side"))
    if side == "up":
        return _to_float(row.get("prior_support_touches"))
    if side == "down":
        return _to_float(row.get("prior_resistance_touches"))
    return None


def _blocked_signal(component_id: str, blockers: list[str], *, required_data_quality: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_statistical_signal_v1",
        "component_id": component_id,
        "side": None,
        "confidence": 0.0,
        "feature_values": {},
        "entry_window": {},
        "required_data_quality": required_data_quality,
        "blockers": sorted(set(blockers)),
        "replay_timestamp_utc": None,
        "source_row_index": None,
    }


def _missing_required(frame: pd.DataFrame, required: tuple[str, ...]) -> list[str]:
    return [f"missing_component_field:{field}" for field in required if field not in frame.columns or not frame[field].notna().any()]


def _volume_ratio(row: pd.Series) -> float:
    volume = _to_float(row.get("volume")) or 0.0
    baseline = _to_float(row.get("volume_ma")) or volume or 1.0
    return max(volume / baseline - 1.0, 0.0)


def _same_direction(close: Any, anchor: Any, side: Any) -> bool:
    close_value = _to_float(close)
    anchor_value = _to_float(anchor)
    normalized_side = _normalize_side(side)
    if close_value is None or anchor_value is None:
        return False
    return (normalized_side == "up" and close_value > anchor_value) or (normalized_side == "down" and close_value < anchor_value)


def _bounded_confidence(value: float) -> float:
    return max(0.0, min(float(value), 0.95))


def _normalize_side(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"yes", "true", "1", "above", "up"}:
        return "up"
    if normalized in {"no", "false", "0", "below", "down"}:
        return "down"
    return normalized


def _to_float(value: Any) -> float | None:
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


__all__ = [
    "COMPONENT_1_ID",
    "COMPONENT_2_ID",
    "COMPONENT_3_ID",
    "COMPONENT_4_ID",
    "COMPONENT_6_ID",
    "COMPONENT_8_ID",
    "registered_statistical_component_runners",
    "run_registered_statistical_component_backtests",
    "run_false_breakout_reversal_filter",
    "run_settlement_threshold_distance_model",
    "run_support_resistance_bounce_decay",
    "run_volatility_regime_filter",
    "run_volume_profile_acceptance_rejection",
    "run_vwap_ema_trend_confluence",
]
