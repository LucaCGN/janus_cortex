from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


PERIOD_WEIGHTS = {
    "1h": 0.25,
    "1d": 0.25,
    "7d": 0.20,
    "30d": 0.15,
    "all_time": 0.15,
}


@dataclass(frozen=True)
class PeriodMetrics:
    period: str
    pnl_usd: float = 0.0
    win_rate: float | None = None
    return_pct: float | None = None
    trade_count: int = 0


@dataclass(frozen=True)
class GradeResult:
    grade: str
    score: float
    components: dict[str, Any]


@dataclass(frozen=True)
class UsageEligibility:
    usage_eligible: bool
    reason: str
    active_in_last_5m: bool
    active_most_last_hour: bool
    crypto_events_1h: int
    crypto_events_24h: int
    style_eligible: bool
    reconstruction_quality: float


def compute_global_grade(
    *,
    period_metrics: list[PeriodMetrics],
    bot_frequency_score: float,
    crypto_event_coverage: float,
    recent_crypto_share: float,
    style_quality: float,
    recent_activity_score: float | None = None,
) -> GradeResult:
    period_by_name = {metric.period: metric for metric in period_metrics}
    period_score = 0.0
    period_components: dict[str, Any] = {}
    for period, weight in PERIOD_WEIGHTS.items():
        metric = period_by_name.get(period, PeriodMetrics(period=period))
        pnl_component = _bounded(metric.pnl_usd / 1000.0, -1.0, 1.0)
        win_component = 0.0 if metric.win_rate is None else (metric.win_rate - 0.5) * 2.0
        return_component = 0.0 if metric.return_pct is None else _bounded(metric.return_pct / 100.0, -1.0, 1.0)
        component = (pnl_component * 0.35) + (win_component * 0.40) + (return_component * 0.25)
        period_score += weight * component
        period_components[period] = {
            "pnl_component": pnl_component,
            "win_component": win_component,
            "return_component": return_component,
            "weighted_component": weight * component,
        }

    quality_component = (
        _bounded(bot_frequency_score, 0.0, 1.0) * 0.20
        + _bounded(crypto_event_coverage, 0.0, 1.0) * 0.25
        + _bounded(recent_crypto_share, 0.0, 1.0) * 0.20
        + _bounded(style_quality, 0.0, 1.0) * 0.35
    )
    score = 50.0 + (period_score * 35.0) + (quality_component * 15.0)
    score = round(_bounded(score, 0.0, 100.0), 3)
    return GradeResult(
        grade=grade_from_score(score),
        score=score,
        components={
            "periods": period_components,
            "quality_component": quality_component,
            "recent_activity_score_ignored": recent_activity_score,
        },
    )


def grade_from_score(score: float) -> str:
    if score >= 95:
        return "S++"
    if score >= 90:
        return "S+"
    if score >= 80:
        return "S"
    if score >= 70:
        return "A"
    if score >= 60:
        return "B"
    if score >= 50:
        return "C"
    if score >= 40:
        return "D"
    if score >= 25:
        return "E"
    return "U"


def compute_usage_eligibility(
    *,
    last_activity_at_utc: datetime | None,
    now_utc: datetime,
    active_most_last_hour: bool,
    crypto_events_1h: int,
    crypto_events_24h: int,
    style_eligible: bool,
    grade: str,
    reconstruction_quality: float,
    minimum_grade: str = "S",
) -> UsageEligibility:
    active_in_last_5m = (
        last_activity_at_utc is not None
        and (now_utc.astimezone(UTC) - last_activity_at_utc.astimezone(UTC)).total_seconds() <= 300
    )
    if reconstruction_quality < 0.6:
        return UsageEligibility(
            False,
            "low_reconstruction_quality",
            active_in_last_5m,
            active_most_last_hour,
            crypto_events_1h,
            crypto_events_24h,
            style_eligible,
            reconstruction_quality,
        )
    if not style_eligible:
        reason = "style_not_eligible"
    elif not _grade_at_least(grade, minimum_grade):
        reason = "grade_below_minimum"
    elif not active_in_last_5m and not active_most_last_hour:
        reason = "not_recently_active"
    elif crypto_events_1h <= 0 or crypto_events_24h <= 0:
        reason = "insufficient_crypto_activity"
    else:
        reason = "eligible"
    return UsageEligibility(
        reason == "eligible",
        reason,
        active_in_last_5m,
        active_most_last_hour,
        crypto_events_1h,
        crypto_events_24h,
        style_eligible,
        reconstruction_quality,
    )


def _grade_at_least(grade: str, minimum_grade: str) -> bool:
    ranking = {"U": 0, "E": 1, "D": 2, "C": 3, "B": 4, "A": 5, "S": 6, "S+": 7, "S++": 8}
    return ranking.get(grade, -1) >= ranking.get(minimum_grade, 6)


def _bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
