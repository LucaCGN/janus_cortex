from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


V4_POLICY_SCHEMA_VERSION = "crypto_options_v4_policy_v1"

V4_HEDGER_REPLICATION_CANDIDATE_ID = "v4_hedger_replication_v1"
V4_OUTCOME_PREDICTION_CANDIDATE_ID = "v4_outcome_prediction_cashout_v1"
V4_DIVERGENCE_SCALPING_CANDIDATE_ID = "v4_divergence_scalping_v1"
V4_CANDIDATE_IDS = (
    V4_HEDGER_REPLICATION_CANDIDATE_ID,
    V4_OUTCOME_PREDICTION_CANDIDATE_ID,
    V4_DIVERGENCE_SCALPING_CANDIDATE_ID,
)
V4_LIVE_GRADES = ("S++", "S+", "S")
V4_A_FALLBACK_GRADES = ("A",)
V4_IGNORED_LIVE_GRADES = ("A", "B", "C", "D", "E", "U")
V4_HEDGER_STYLES = ("hedger", "grid_buyer")
V4_OUTCOME_STYLES = ("outcome_predictor",)


def grade_rank(grade: Any) -> int:
    return {"S++": 30, "S+": 20, "S": 10, "A": 5}.get(str(grade or "").upper(), 0)


def is_v4_live_grade(grade: Any) -> bool:
    return str(grade or "").upper() in set(V4_LIVE_GRADES)


def is_v4_a_fallback_grade(grade: Any) -> bool:
    return str(grade or "").upper() in set(V4_A_FALLBACK_GRADES)


def signal_profile_name(signal: dict[str, Any]) -> str:
    return str(signal.get("profile_name") or signal.get("name") or signal.get("proxy_wallet") or "").strip()


def signal_trading_style(signal: dict[str, Any]) -> str:
    return str(signal.get("profile_trading_style") or signal.get("trading_style") or "").strip().lower()


def signal_trading_style_detail(signal: dict[str, Any]) -> str:
    return str(signal.get("profile_trading_style_detail") or signal.get("trading_style_detail") or "").strip().lower()


def signal_is_hedger_grid(signal: dict[str, Any]) -> bool:
    style = signal_trading_style(signal)
    detail = signal_trading_style_detail(signal)
    return style == "hedger" or detail == "grid_buyer"


def signal_is_outcome_predictor(signal: dict[str, Any]) -> bool:
    return signal_trading_style(signal) == "outcome_predictor"


def eligible_v4_signals(row: dict[str, Any], *, style: str, allow_a_fallback: bool = False) -> list[dict[str, Any]]:
    output = []
    for signal in row.get("supporting_signals") or []:
        if not isinstance(signal, dict):
            continue
        grade_allowed = is_v4_live_grade(signal.get("profile_grade"))
        fallback_allowed = allow_a_fallback and is_v4_a_fallback_grade(signal.get("profile_grade"))
        if not grade_allowed and not fallback_allowed:
            continue
        if style == "hedger_grid" and not signal_is_hedger_grid(signal):
            continue
        if style == "outcome_predictor" and not signal_is_outcome_predictor(signal):
            continue
        cloned = dict(signal)
        if fallback_allowed and not grade_allowed:
            cloned["v4_grade_fallback"] = "A_when_no_s_tier_available"
        output.append(cloned)
    return output


def summarize_v4_support(row: dict[str, Any], *, style: str, allow_a_fallback: bool = False) -> dict[str, Any]:
    signals = eligible_v4_signals(row, style=style, allow_a_fallback=allow_a_fallback)
    profiles = sorted({signal_profile_name(signal) for signal in signals if signal_profile_name(signal)})
    return {
        "schema_version": "crypto_options_v4_profile_support_summary_v1",
        "style": style,
        "grade_fallback_active": bool(allow_a_fallback and signals),
        "signal_count": len(signals),
        "profile_count": len(profiles),
        "profiles": profiles,
        "grade_counts": _grade_counts(signals),
        "best_grade": _best_grade(signals),
        "best_score": max((_to_float(signal.get("profile_score")) or 0.0 for signal in signals), default=0.0),
    }


def build_v4_event_context(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    outcome = str(row.get("outcome") or row.get("effective_outcome") or "").strip()
    threshold = _to_float(row.get("settlement_threshold") or row.get("event_threshold_price") or row.get("reference_start_price"))
    underlying = _to_float(row.get("underlying_price") or row.get("underlying_close") or row.get("current_underlying_price"))
    remaining = _to_float(row.get("time_remaining_seconds") or row.get("time_to_close_seconds"))
    if remaining is None:
        event_end = _parse_time(row.get("window_end_time") or row.get("event_end_utc") or row.get("market_end_utc"))
        if event_end is not None:
            remaining = (event_end - now.astimezone(timezone.utc)).total_seconds()
    signed_delta = None
    if threshold is not None and underlying is not None:
        if _outcome_key(outcome) == "up":
            signed_delta = underlying - threshold
        elif _outcome_key(outcome) == "down":
            signed_delta = threshold - underlying
    blockers: list[str] = []
    if threshold is None:
        blockers.append("v4_event_context_threshold_missing")
    if underlying is None:
        blockers.append("v4_event_context_underlying_price_missing")
    if remaining is None:
        blockers.append("v4_event_context_time_remaining_missing")
    if _outcome_key(outcome) not in {"up", "down"}:
        blockers.append("v4_event_context_outcome_missing")
    return {
        "schema_version": "crypto_options_v4_event_context_v1",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "outcome": outcome,
        "underlying_price": underlying,
        "event_threshold_price": threshold,
        "target_delta_abs": abs(underlying - threshold) if threshold is not None and underlying is not None else None,
        "target_delta_signed_for_side": signed_delta,
        "target_delta_is_about": "underlying_price_vs_event_threshold",
        "time_remaining_seconds": remaining,
        "trend_15m": _trend_value(row.get("underlying_trend_15m")),
        "trend_30m": _trend_value(row.get("underlying_trend_30m")),
        "trend_1h": _trend_value(row.get("underlying_trend_1h")),
        "context_blockers": blockers,
    }


def evaluate_v4_event_context(row: dict[str, Any], *, strategy_id: str, now: datetime | None = None) -> dict[str, Any]:
    context = build_v4_event_context(row, now=now)
    blockers = list(context["context_blockers"])
    size_multiplier = 1.0
    price = _entry_price(row)
    remaining = _to_float(context.get("time_remaining_seconds"))
    signed_delta = _to_float(context.get("target_delta_signed_for_side"))
    side_adverse = signed_delta is not None and signed_delta < 0.0
    if strategy_id == V4_HEDGER_REPLICATION_CANDIDATE_ID:
        return {
            **context,
            "allowed": True,
            "blockers": [],
            "size_multiplier": 1.0,
            "context_mode": "informational_only_for_hedger_follow",
        }
    if blockers:
        return {**context, "allowed": False, "blockers": sorted(set(blockers)), "size_multiplier": 0.0}
    if strategy_id == V4_OUTCOME_PREDICTION_CANDIDATE_ID:
        if remaining is not None and remaining < 90.0 and side_adverse:
            blockers.append("v4_outcome_context_time_under_90s_with_adverse_delta")
    if strategy_id == V4_DIVERGENCE_SCALPING_CANDIDATE_ID:
        if remaining is not None and remaining < 150.0:
            blockers.append("v4_scalping_context_requires_more_than_half_event_remaining")
    if price is not None and remaining is not None and side_adverse:
        if price < 0.20 and remaining < 180.0:
            blockers.append("v4_context_low_price_under_20c_late_adverse_delta")
        elif price < 0.40 and remaining < 120.0:
            blockers.append("v4_context_low_price_under_40c_late_adverse_delta")
    trend = _trend_value(context.get("trend_15m"))
    if trend and signed_delta is not None and _trend_opposes_side(trend, context.get("outcome")):
        size_multiplier *= 0.70
    return {
        **context,
        "allowed": not blockers,
        "blockers": sorted(set(blockers)),
        "size_multiplier": round(max(0.0, min(1.0, size_multiplier)), 4),
    }


def dynamic_cashout_tier(win_rate: float | None, *, pnl_usd: float = 0.0, drawdown_usd: float = 0.0) -> dict[str, Any]:
    if win_rate is not None and win_rate >= 0.80 and pnl_usd > 0.0 and drawdown_usd <= 20.0:
        return {"tier": "optional_hold_with_lifecycle", "cashout_required": False, "target_profile": "hold_or_rescue"}
    if win_rate is not None and win_rate >= 0.65:
        return {"tier": "loose", "cashout_required": True, "target_profile": "loose_cashout"}
    if win_rate is not None and win_rate >= 0.50:
        return {"tier": "moderate", "cashout_required": True, "target_profile": "moderate_cashout"}
    return {"tier": "defensive", "cashout_required": True, "target_profile": "defensive_cashout"}


def hedger_last_minute_mode(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    context = build_v4_event_context(row, now=now)
    remaining = _to_float(context.get("time_remaining_seconds"))
    up_down_near_mid = _entry_price(row) is not None and 0.45 <= float(_entry_price(row) or 0.0) <= 0.55
    target_delta = _to_float(context.get("target_delta_abs"))
    if remaining is None:
        return {"mode": "blocked", "blockers": ["v4_hedger_time_remaining_missing"]}
    if remaining < 30.0:
        return {"mode": "protect_only_no_new_entries", "blockers": ["v4_hedger_no_new_entries_under_30s"]}
    if remaining < 75.0 and up_down_near_mid and target_delta is not None and target_delta <= 25.0:
        return {"mode": "passive_limit_only", "blockers": ["v4_hedger_last_minute_midpoint_latency_risk"]}
    if remaining < 75.0:
        return {"mode": "passive_limit_only", "blockers": []}
    return {"mode": "pulse_replication", "blockers": []}


def _grade_counts(signals: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in signals:
        grade = str(signal.get("profile_grade") or "").upper()
        if not grade:
            continue
        counts[grade] = counts.get(grade, 0) + 1
    return counts


def _best_grade(signals: list[dict[str, Any]]) -> str | None:
    grades = [str(signal.get("profile_grade") or "").upper() for signal in signals]
    grades = [grade for grade in grades if grade]
    return sorted(grades, key=grade_rank, reverse=True)[0] if grades else None


def _entry_price(row: dict[str, Any]) -> float | None:
    return _to_float(row.get("best_ask") or row.get("observed_execution_price") or row.get("price"))


def _trend_opposes_side(trend: str, outcome: Any) -> bool:
    key = _outcome_key(outcome)
    return (key == "up" and trend == "down") or (key == "down" and trend == "up")


def _trend_value(value: Any) -> str | None:
    lowered = str(value or "").strip().lower()
    return lowered if lowered in {"up", "down", "sideways"} else None


def _outcome_key(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in {"up", "yes", "above"}:
        return "up"
    if lowered in {"down", "no", "below"}:
        return "down"
    return lowered


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ""):
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


__all__ = [
    "V4_CANDIDATE_IDS",
    "V4_DIVERGENCE_SCALPING_CANDIDATE_ID",
    "V4_HEDGER_REPLICATION_CANDIDATE_ID",
    "V4_LIVE_GRADES",
    "V4_OUTCOME_PREDICTION_CANDIDATE_ID",
    "build_v4_event_context",
    "dynamic_cashout_tier",
    "eligible_v4_signals",
    "evaluate_v4_event_context",
    "grade_rank",
    "hedger_last_minute_mode",
    "is_v4_a_fallback_grade",
    "is_v4_live_grade",
    "signal_is_hedger_grid",
    "signal_is_outcome_predictor",
    "summarize_v4_support",
]
