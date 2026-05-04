from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class StakesAssessment:
    stakes_score: float
    stakes_deterministic_score: float
    stakes_llm_score: float | None
    stakes_bucket: str
    stakes_reason: str


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _clip_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _bucket_for_score(score: float) -> str:
    if score >= 90.0:
        return "critical"
    if score >= 70.0:
        return "high"
    if score >= 40.0:
        return "medium"
    if score > 0.0:
        return "low"
    return "none"


def _regular_season_score(*, game_date: date | None, context: dict[str, Any]) -> tuple[float, str]:
    if bool(context.get("position_mathematically_locked_flag")):
        return 0.0, "regular_season_position_locked"
    if bool(context.get("playin_or_playoff_spot_implication_flag")):
        return 100.0, "regular_season_playin_or_playoff_spot_implication"
    if bool(context.get("homecourt_implication_flag")):
        return 85.0, "regular_season_homecourt_implication"
    if bool(context.get("draft_position_implication_flag")):
        return 20.0, "regular_season_draft_position_implication"

    if game_date is None:
        return 45.0, "regular_season_default_unknown_date"
    season_month = game_date.month
    if season_month in {3, 4}:
        return 60.0, "regular_season_late_calendar_default"
    if season_month in {10, 11}:
        return 35.0, "regular_season_early_calendar_default"
    return 45.0, "regular_season_mid_calendar_default"


def evaluate_game_stakes(
    *,
    season_phase: str,
    game_date: Any = None,
    home_opening_price: float | None = None,
    away_opening_price: float | None = None,
    llm_stakes_score: float | None = None,
    context: dict[str, Any] | None = None,
) -> StakesAssessment:
    """Return a 0-100 game stakes score for routing and research features.

    The deterministic score is always available. LLM matchup scoring can be
    layered in later, but remains optional so mart/replay jobs stay offline-safe.
    """
    phase = str(season_phase or "").strip().lower()
    context_payload = dict(context or {})
    resolved_date = _safe_date(game_date)

    if phase in {"preseason", "pre_season"}:
        deterministic_score = 0.0
        reason = "preseason"
    elif phase == "play_in":
        deterministic_score = 100.0
        reason = "play_in_elimination_stakes"
    elif phase == "playoffs":
        home_price = _safe_float(home_opening_price)
        away_price = _safe_float(away_opening_price)
        usable_price = home_price if home_price is not None else away_price
        if usable_price is None:
            deterministic_score = 85.0
            reason = "postseason_default_no_opening_price"
        else:
            balance = 1.0 - min(abs(float(usable_price) - 0.5) / 0.5, 1.0)
            deterministic_score = 75.0 + (20.0 * balance)
            reason = "postseason_homecourt_matchup_balance"
        if bool(context_payload.get("series_elimination_game_flag")):
            deterministic_score = max(deterministic_score, 100.0)
            reason = "postseason_elimination_game"
    elif phase == "regular_season":
        deterministic_score, reason = _regular_season_score(game_date=resolved_date, context=context_payload)
    else:
        deterministic_score = 40.0
        reason = f"unknown_phase:{phase or 'missing'}"

    deterministic_score = _clip_score(deterministic_score)
    llm_score = _safe_float(llm_stakes_score)
    if llm_score is not None:
        llm_score = _clip_score(llm_score)
        final_score = _clip_score((0.70 * deterministic_score) + (0.30 * llm_score))
        reason = f"{reason}|llm_blend_30pct"
    else:
        final_score = deterministic_score

    return StakesAssessment(
        stakes_score=round(final_score, 2),
        stakes_deterministic_score=round(deterministic_score, 2),
        stakes_llm_score=round(llm_score, 2) if llm_score is not None else None,
        stakes_bucket=_bucket_for_score(final_score),
        stakes_reason=reason,
    )


__all__ = ["StakesAssessment", "evaluate_game_stakes"]
