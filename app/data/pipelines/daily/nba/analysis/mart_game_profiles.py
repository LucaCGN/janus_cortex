from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Callable

import pandas as pd

from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_OPENING_BAND_SIZE,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    DEFAULT_WINNER_THRESHOLDS,
    RESEARCH_READY_STATUSES,
)
from app.data.pipelines.daily.nba.analysis.bundle_loader import (
    _build_market_summary,
    _build_price_snapshots_for_events,
    _load_game,
    _load_latest_feature_snapshot,
    _load_market_rows,
    _load_market_ticks,
    _load_play_by_play,
    select_preferred_market_bundle,
)


BuildStateRowsForSide = Callable[..., list[dict[str, Any]]]


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = _safe_datetime(value)
    if parsed is not None:
        return parsed.date()
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _normalize_text(value: Any) -> str:
    raw = "".join(character.lower() if str(character).isalnum() else " " for character in str(value or ""))
    return " ".join(raw.split())


def _team_label_variants(game: dict[str, Any], team_side: str) -> set[str]:
    city = str(game.get(f"{team_side}_team_city") or "").strip()
    name = str(game.get(f"{team_side}_team_name") or "").strip()
    slug = str(game.get(f"{team_side}_team_slug") or "").strip()
    normalized_name = _normalize_text(name)
    name_parts = normalized_name.split()
    variants = {
        _normalize_text(city),
        normalized_name,
        _normalize_text(slug),
        _normalize_text(f"{city} {name}"),
        _normalize_text(f"{name} {city}"),
    }
    if name_parts:
        variants.add(" ".join(name_parts[-1:]))
    if len(name_parts) >= 2:
        variants.add(" ".join(name_parts[-2:]))
    return {variant for variant in variants if variant}


def _resolve_outcome_side(
    *,
    game: dict[str, Any],
    outcome_label: Any,
    fallback: str | None = None,
) -> str | None:
    label = _normalize_text(outcome_label)
    if not label:
        return fallback

    home_variants = _team_label_variants(game, "home")
    away_variants = _team_label_variants(game, "away")
    home_exact = label in home_variants
    away_exact = label in away_variants
    if home_exact and not away_exact:
        return "home"
    if away_exact and not home_exact:
        return "away"

    for side, variants in (("home", home_variants), ("away", away_variants)):
        for variant in variants:
            if len(variant) < 4 or " " not in variant:
                continue
            if label.startswith(f"{variant} ") or label.endswith(f" {variant}") or f" {variant} " in f" {label} ":
                return side
    return fallback


def _normalize_selected_market_payload(selected_market: dict[str, Any] | None, *, game: dict[str, Any]) -> dict[str, Any] | None:
    if selected_market is None:
        return None
    series = []
    for series_item in selected_market.get("series", []):
        fallback = str(series_item.get("side") or "").strip() or None
        resolved_side = _resolve_outcome_side(
            game=game,
            outcome_label=series_item.get("outcome_label"),
            fallback=fallback,
        )
        series.append({**series_item, "side": resolved_side})
    return {**selected_market, "series": series}


def _estimate_midpoint(min_value: float | None, max_value: float | None) -> float | None:
    if min_value is not None and max_value is not None:
        return (min_value + max_value) / 2.0
    return min_value if min_value is not None else max_value


def _value_range(min_value: float | None, max_value: float | None) -> float | None:
    if min_value is None or max_value is None:
        return None
    return max_value - min_value


def opening_band_for_price(price: float | None) -> tuple[str | None, int | None]:
    if price is None or pd.isna(price):
        return None, None
    cents = min(99, max(0, int(math.floor(float(price) * 100.0 + 1e-9))))
    lower = (cents // DEFAULT_OPENING_BAND_SIZE) * DEFAULT_OPENING_BAND_SIZE
    upper = lower + DEFAULT_OPENING_BAND_SIZE
    if upper > 100:
        upper = 100
    return f"{lower}-{upper}", int(lower // DEFAULT_OPENING_BAND_SIZE)


def _parse_tick_series(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for tick in ticks:
        ts = _safe_datetime(tick.get("ts"))
        price = _safe_float(tick.get("price"))
        if ts is None or price is None:
            continue
        parsed.append({**tick, "_ts": ts, "_price": price})
    parsed.sort(key=lambda item: item["_ts"])
    return parsed


def _derive_tick_series_metrics(
    ticks: list[dict[str, Any]],
    *,
    first_event_at: datetime | None,
    last_event_at: datetime | None,
) -> dict[str, Any]:
    parsed = _parse_tick_series(ticks)
    if not parsed:
        return {
            "series_point_count": 0,
            "opening_price": None,
            "closing_price": None,
            "opening_band": None,
            "opening_band_rank": None,
            "pregame_price_min": None,
            "pregame_price_max": None,
            "pregame_price_range": None,
            "ingame_price_min": None,
            "ingame_price_max": None,
            "ingame_price_range": None,
            "total_price_min": None,
            "total_price_max": None,
            "total_swing": None,
            "max_favorable_excursion": None,
            "max_adverse_excursion": None,
        }
    prices = [item["_price"] for item in parsed]
    opening_price = float(prices[0])
    closing_price = float(prices[-1])
    pregame_prices = [item["_price"] for item in parsed if first_event_at is not None and item["_ts"] < first_event_at]
    ingame_prices = [
        item["_price"]
        for item in parsed
        if first_event_at is not None and last_event_at is not None and first_event_at <= item["_ts"] <= last_event_at
    ]
    opening_band, opening_band_rank = opening_band_for_price(opening_price)
    total_min = float(min(prices))
    total_max = float(max(prices))
    return {
        "series_point_count": len(parsed),
        "opening_price": opening_price,
        "closing_price": closing_price,
        "opening_band": opening_band,
        "opening_band_rank": opening_band_rank,
        "pregame_price_min": float(min(pregame_prices)) if pregame_prices else None,
        "pregame_price_max": float(max(pregame_prices)) if pregame_prices else None,
        "pregame_price_range": _value_range(
            float(min(pregame_prices)) if pregame_prices else None,
            float(max(pregame_prices)) if pregame_prices else None,
        ),
        "ingame_price_min": float(min(ingame_prices)) if ingame_prices else None,
        "ingame_price_max": float(max(ingame_prices)) if ingame_prices else None,
        "ingame_price_range": _value_range(
            float(min(ingame_prices)) if ingame_prices else None,
            float(max(ingame_prices)) if ingame_prices else None,
        ),
        "total_price_min": total_min,
        "total_price_max": total_max,
        "total_swing": total_max - total_min,
        "max_favorable_excursion": max(total_max - opening_price, 0.0),
        "max_adverse_excursion": max(opening_price - total_min, 0.0),
    }


def _fallback_profile_metrics_from_feature_snapshot(feature_snapshot: dict[str, Any] | None, team_side: str) -> dict[str, Any]:
    payload = feature_snapshot or {}
    prefix = "home" if team_side == "home" else "away"
    pre_min = _safe_float(payload.get(f"{prefix}_pre_game_price_min"))
    pre_max = _safe_float(payload.get(f"{prefix}_pre_game_price_max"))
    ingame_min = _safe_float(payload.get(f"{prefix}_in_game_price_min"))
    ingame_max = _safe_float(payload.get(f"{prefix}_in_game_price_max"))
    opening_price = _estimate_midpoint(pre_min, pre_max)
    opening_band, opening_band_rank = opening_band_for_price(opening_price)
    all_values = [value for value in (pre_min, pre_max, ingame_min, ingame_max) if value is not None]
    total_min = min(all_values) if all_values else None
    total_max = max(all_values) if all_values else None
    return {
        "series_point_count": 0,
        "opening_price": opening_price,
        "closing_price": None,
        "opening_band": opening_band,
        "opening_band_rank": opening_band_rank,
        "pregame_price_min": pre_min,
        "pregame_price_max": pre_max,
        "pregame_price_range": _value_range(pre_min, pre_max),
        "ingame_price_min": ingame_min,
        "ingame_price_max": ingame_max,
        "ingame_price_range": _value_range(ingame_min, ingame_max),
        "total_price_min": total_min,
        "total_price_max": total_max,
        "total_swing": _value_range(total_min, total_max),
        "max_favorable_excursion": max((total_max or opening_price or 0.0) - (opening_price or 0.0), 0.0)
        if opening_price is not None and total_max is not None
        else None,
        "max_adverse_excursion": max((opening_price or 0.0) - (total_min or opening_price or 0.0), 0.0)
        if opening_price is not None and total_min is not None
        else None,
    }


def _find_stable_index(prices: list[float], threshold: float) -> int | None:
    if not prices:
        return None
    running_min = math.inf
    stable_index: int | None = None
    for index in range(len(prices) - 1, -1, -1):
        running_min = min(running_min, prices[index])
        if prices[index] >= threshold and running_min >= threshold:
            stable_index = index
    return stable_index


def _compute_time_above_below_fifty(prices: list[float], event_times: list[datetime]) -> tuple[float | None, float | None]:
    if len(prices) < 2 or len(event_times) < 2:
        return None, None
    seconds_above = 0.0
    seconds_below = 0.0
    for index in range(len(prices) - 1):
        duration = max(0.0, (event_times[index + 1] - event_times[index]).total_seconds())
        if prices[index] >= 0.5:
            seconds_above += duration
        else:
            seconds_below += duration
    return seconds_above, seconds_below


def load_analysis_bundle(connection: Any, *, game_id: str) -> dict[str, Any] | None:
    game = _load_game(connection, game_id=game_id)
    if game is None:
        return None
    feature_snapshot = _load_latest_feature_snapshot(connection, game_id=game_id)
    play_by_play = _load_play_by_play(connection, game=game)
    market_bundles = _load_market_rows(connection, game_id=game_id, game=game)
    selected_market = select_preferred_market_bundle(market_bundles, requested_market_type="moneyline")
    selected_market_payload: dict[str, Any] | None = None
    if selected_market is not None:
        outcome_ids = [str(item["outcome_id"]) for item in selected_market["outcomes"] if item.get("outcome_id")]
        ticks_by_outcome = _load_market_ticks(connection, outcome_ids=outcome_ids)
        series = []
        for outcome in selected_market["outcomes"]:
            outcome_id = str(outcome["outcome_id"])
            series.append(
                {
                    **outcome,
                    "outcome_id": outcome_id,
                    "ticks": ticks_by_outcome.get(outcome_id, []),
                }
            )
        selected_market_payload = {key: value for key, value in selected_market.items() if key != "outcomes"}
        selected_market_payload["series"] = series
        selected_market_payload.update(
            _build_market_summary(
                {"series": series, **selected_market_payload},
                play_by_play["summary"],
            )
        )
        _build_price_snapshots_for_events(
            play_by_play["items"],
            {str(item["outcome_id"]): item["ticks"] for item in series if item.get("outcome_id")},
        )
        selected_market_payload = _normalize_selected_market_payload(selected_market_payload, game=game)
    return {
        "game": game,
        "feature_snapshot": feature_snapshot,
        "play_by_play": play_by_play,
        "selected_market": selected_market_payload,
    }


def derive_game_rows(
    *,
    universe_row: pd.Series,
    bundle: dict[str, Any],
    analysis_version: str,
    computed_at: datetime,
    build_state_rows_for_side: BuildStateRowsForSide,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    game = bundle["game"]
    feature_snapshot = bundle.get("feature_snapshot") or {}
    play_by_play = bundle["play_by_play"]
    selected_market = _normalize_selected_market_payload(bundle.get("selected_market"), game=game)
    coverage_status = str(universe_row.get("coverage_status") or "missing_feature_snapshot")
    timed_items = [item for item in play_by_play["items"] if _safe_datetime(item.get("time_actual")) is not None]
    first_event_at = _safe_datetime(play_by_play["summary"].get("first_event_at"))
    last_event_at = _safe_datetime(play_by_play["summary"].get("last_event_at"))
    canonical_classification = str(universe_row.get("classification") or "").strip()
    if canonical_classification:
        canonical_research_ready_flag = canonical_classification == "research_ready"
    elif "research_ready_flag" in universe_row.index:
        canonical_research_ready_flag = bool(universe_row.get("research_ready_flag"))
    else:
        canonical_research_ready_flag = coverage_status in RESEARCH_READY_STATUSES

    outcome_by_side: dict[str, dict[str, Any]] = {}
    selected_event_id = None
    selected_market_id = None
    if selected_market is not None:
        selected_event_id = str(selected_market.get("event_id")) if selected_market.get("event_id") else None
        selected_market_id = str(selected_market.get("market_id")) if selected_market.get("market_id") else None
        for series_item in selected_market.get("series", []):
            side = str(series_item.get("side") or "")
            if side in {"home", "away"} and side not in outcome_by_side:
                outcome_by_side[side] = series_item

    home_score = _safe_int(game.get("home_score")) or 0
    away_score = _safe_int(game.get("away_score")) or 0
    winner_side = "home" if home_score > away_score else "away" if away_score > home_score else None
    state_rows_all: list[dict[str, Any]] = []
    game_rows: dict[str, dict[str, Any]] = {}

    for team_side in ("home", "away"):
        outcome_meta = outcome_by_side.get(team_side)
        tick_metrics = (
            _derive_tick_series_metrics(
                outcome_meta.get("ticks", []),
                first_event_at=first_event_at,
                last_event_at=last_event_at,
            )
            if outcome_meta is not None
            else {}
        )
        fallback_metrics = _fallback_profile_metrics_from_feature_snapshot(feature_snapshot, team_side)
        metrics = tick_metrics if tick_metrics.get("opening_price") is not None else fallback_metrics
        team_id = _safe_int(game.get(f"{team_side}_team_id"))
        team_slug = str(game.get(f"{team_side}_team_slug") or "").upper() or None
        opposite = "away" if team_side == "home" else "home"
        opponent_team_id = _safe_int(game.get(f"{opposite}_team_id"))
        opponent_team_slug = str(game.get(f"{opposite}_team_slug") or "").upper() or None
        final_winner_flag = winner_side == team_side if winner_side is not None else False
        side_state_rows: list[dict[str, Any]] = []
        if canonical_research_ready_flag and outcome_meta is not None and outcome_meta.get("ticks") and timed_items:
            side_state_rows = build_state_rows_for_side(
                game=game,
                timed_items=timed_items,
                team_side=team_side,
                team_id=team_id,
                team_slug=team_slug,
                opponent_team_id=opponent_team_id,
                opponent_team_slug=opponent_team_slug,
                outcome_id=str(outcome_meta["outcome_id"]),
                event_id=selected_event_id or (str(feature_snapshot.get("event_id")) if feature_snapshot.get("event_id") else None),
                market_id=selected_market_id,
                opening_price=metrics.get("opening_price"),
                opening_band=metrics.get("opening_band"),
                final_winner_flag=final_winner_flag,
                season=str(universe_row.get("season") or DEFAULT_SEASON),
                season_phase=str(universe_row.get("season_phase") or DEFAULT_SEASON_PHASE),
                analysis_version=analysis_version,
                computed_at=computed_at,
            )
            state_rows_all.extend(side_state_rows)

        prices = [float(row["team_price"]) for row in side_state_rows if row.get("team_price") is not None]
        event_times = [row["event_at"] for row in side_state_rows if row.get("event_at") is not None]
        inversion_count = None
        first_inversion_at = None
        seconds_above_50c = None
        seconds_below_50c = None
        winner_stable_at: dict[int, datetime | None] = {threshold: None for threshold in DEFAULT_WINNER_THRESHOLDS}
        winner_stable_clock: dict[int, float | None] = {threshold: None for threshold in DEFAULT_WINNER_THRESHOLDS}
        if prices:
            previous_favorite = prices[0] >= 0.5
            inversion_count = 0
            for price_index, price in enumerate(prices[1:], start=1):
                favorite = price >= 0.5
                if favorite != previous_favorite:
                    inversion_count += 1
                    if first_inversion_at is None:
                        first_inversion_at = event_times[price_index]
                previous_favorite = favorite
            seconds_above_50c, seconds_below_50c = _compute_time_above_below_fifty(prices, event_times)
            if final_winner_flag:
                for threshold in DEFAULT_WINNER_THRESHOLDS:
                    stable_index = _find_stable_index(prices, threshold / 100.0)
                    if stable_index is not None:
                        winner_stable_at[threshold] = event_times[stable_index]
                        winner_stable_clock[threshold] = _safe_float(side_state_rows[stable_index].get("clock_elapsed_seconds"))

        notes_json = {
            "profile_source": "tick_series" if tick_metrics.get("opening_price") is not None else "feature_snapshot_fallback",
            "series_point_count": tick_metrics.get("series_point_count", 0),
            "timed_event_count": len(timed_items),
            "selected_market_type": selected_market.get("market_type") if selected_market else None,
            "selected_market_question": selected_market.get("question") if selected_market else None,
        }
        game_rows[team_side] = {
            "game_id": str(game["game_id"]),
            "team_side": team_side,
            "team_id": team_id,
            "team_slug": team_slug,
            "opponent_team_id": opponent_team_id,
            "opponent_team_slug": opponent_team_slug,
            "event_id": selected_event_id or (str(feature_snapshot.get("event_id")) if feature_snapshot.get("event_id") else None),
            "market_id": selected_market_id,
            "outcome_id": str(outcome_meta["outcome_id"]) if outcome_meta else None,
            "season": str(universe_row.get("season") or DEFAULT_SEASON),
            "season_phase": str(universe_row.get("season_phase") or DEFAULT_SEASON_PHASE),
            "analysis_version": analysis_version,
            "computed_at": computed_at,
            "game_date": _safe_date(game.get("game_date")),
            "game_start_time": _safe_datetime(game.get("game_start_time")),
            "coverage_status": coverage_status,
            "research_ready_flag": False,
            "price_path_reconciled_flag": False,
            "final_winner_flag": final_winner_flag,
            "opening_price": metrics.get("opening_price"),
            "closing_price": metrics.get("closing_price"),
            "opening_band": metrics.get("opening_band"),
            "opening_band_rank": metrics.get("opening_band_rank"),
            "pregame_price_min": metrics.get("pregame_price_min"),
            "pregame_price_max": metrics.get("pregame_price_max"),
            "pregame_price_range": metrics.get("pregame_price_range"),
            "ingame_price_min": metrics.get("ingame_price_min"),
            "ingame_price_max": metrics.get("ingame_price_max"),
            "ingame_price_range": metrics.get("ingame_price_range"),
            "total_price_min": metrics.get("total_price_min"),
            "total_price_max": metrics.get("total_price_max"),
            "total_swing": metrics.get("total_swing"),
            "max_favorable_excursion": metrics.get("max_favorable_excursion"),
            "max_adverse_excursion": metrics.get("max_adverse_excursion"),
            "inversion_count": inversion_count,
            "first_inversion_at": first_inversion_at,
            "seconds_above_50c": seconds_above_50c,
            "seconds_below_50c": seconds_below_50c,
            "winner_stable_70_at": winner_stable_at[70],
            "winner_stable_80_at": winner_stable_at[80],
            "winner_stable_90_at": winner_stable_at[90],
            "winner_stable_95_at": winner_stable_at[95],
            "winner_stable_70_clock_elapsed_seconds": winner_stable_clock[70],
            "winner_stable_80_clock_elapsed_seconds": winner_stable_clock[80],
            "winner_stable_90_clock_elapsed_seconds": winner_stable_clock[90],
            "winner_stable_95_clock_elapsed_seconds": winner_stable_clock[95],
            "notes_json": notes_json,
        }

    state_row_counts = {team_side: int(sum(1 for row in state_rows_all if row["team_side"] == team_side)) for team_side in ("home", "away")}
    market_outcomes_complete_flag = all(side in outcome_by_side for side in ("home", "away"))
    opening_prices_complete_flag = all(game_rows[side].get("opening_price") is not None for side in ("home", "away"))
    state_rows_complete_flag = len(timed_items) > 0 and all(
        state_row_counts[side] == len(timed_items) for side in ("home", "away")
    )
    research_ready_contract_drift_flag = bool(
        canonical_research_ready_flag and not (market_outcomes_complete_flag and opening_prices_complete_flag and state_rows_complete_flag)
    )
    home_closing = _safe_float(game_rows["home"].get("closing_price"))
    away_closing = _safe_float(game_rows["away"].get("closing_price"))
    price_path_reconciled_flag = bool(
        canonical_research_ready_flag
        and market_outcomes_complete_flag
        and opening_prices_complete_flag
        and state_rows_complete_flag
        and winner_side is not None
        and home_closing is not None
        and away_closing is not None
        and ((winner_side == "home" and home_closing >= away_closing) or (winner_side == "away" and away_closing >= home_closing))
    )
    for team_side in ("home", "away"):
        game_rows[team_side]["research_ready_flag"] = canonical_research_ready_flag
        game_rows[team_side]["price_path_reconciled_flag"] = price_path_reconciled_flag
        game_rows[team_side]["notes_json"] = {
            **(game_rows[team_side].get("notes_json") or {}),
            "state_row_count": state_row_counts[team_side],
            "canonical_research_ready_flag": canonical_research_ready_flag,
            "market_outcomes_complete_flag": market_outcomes_complete_flag,
            "opening_prices_complete_flag": opening_prices_complete_flag,
            "state_rows_complete_flag": state_rows_complete_flag,
            "research_ready_contract_drift_flag": research_ready_contract_drift_flag,
            "price_path_reconciled_flag": price_path_reconciled_flag,
        }
    qa = {
        "game_id": str(game["game_id"]),
        "coverage_status": coverage_status,
        "research_ready_flag": canonical_research_ready_flag,
        "price_path_reconciled_flag": price_path_reconciled_flag,
        "market_outcomes_complete_flag": market_outcomes_complete_flag,
        "opening_prices_complete_flag": opening_prices_complete_flag,
        "state_rows_complete_flag": state_rows_complete_flag,
        "research_ready_contract_drift_flag": research_ready_contract_drift_flag,
        "timed_event_count": len(timed_items),
        "selected_market_id": selected_market_id,
        "selected_event_id": selected_event_id,
        "available_outcome_sides": sorted(outcome_by_side.keys()),
    }
    return [game_rows["home"], game_rows["away"]], state_rows_all, qa


__all__ = [
    "derive_game_rows",
    "load_analysis_bundle",
    "opening_band_for_price",
]
