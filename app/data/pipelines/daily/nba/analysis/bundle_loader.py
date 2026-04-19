from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any

from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict


REGULATION_PERIOD_SECONDS = 12 * 60
OVERTIME_PERIOD_SECONDS = 5 * 60
PREFERRED_MARKET_TYPES = ("moneyline", "spreads", "first_half_spreads")
_CLOCK_PATTERN = re.compile(r"^PT(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$")


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_nba_clock_seconds_remaining(clock: str | None) -> float | None:
    if not clock:
        return None
    match = _CLOCK_PATTERN.match(str(clock).strip())
    if not match:
        return None
    minutes = float(match.group("minutes") or 0.0)
    seconds = float(match.group("seconds") or 0.0)
    return (minutes * 60.0) + seconds


def _period_duration_seconds(period: int | None) -> int:
    if period is None or period <= 4:
        return REGULATION_PERIOD_SECONDS
    return OVERTIME_PERIOD_SECONDS


def compute_elapsed_game_clock_seconds(period: int | None, clock: str | None) -> float | None:
    remaining = parse_nba_clock_seconds_remaining(clock)
    if period is None or remaining is None:
        return None
    elapsed = 0.0
    for current_period in range(1, period):
        elapsed += float(_period_duration_seconds(current_period))
    return elapsed + max(0.0, float(_period_duration_seconds(period)) - remaining)


def _format_duration(seconds: float | None) -> str:
    if seconds is None or math.isnan(seconds) or seconds < 0:
        return "n/a"
    rounded = int(round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _format_period_label(period: int | None) -> str:
    if period is None:
        return "UNK"
    if period <= 4:
        return f"Q{period}"
    return f"OT{period - 4}"


def _team_label_variants(team: dict[str, Any]) -> set[str]:
    team_city = str(team.get("team_city") or "").strip()
    team_name = str(team.get("team_name") or "").strip()
    team_slug = str(team.get("team_slug") or "").strip()
    normalized_team_name = _normalize_text(team_name)
    team_name_parts = normalized_team_name.split()
    variants = {
        _normalize_text(team_city),
        normalized_team_name,
        _normalize_text(team_slug),
        _normalize_text(f"{team_city} {team_name}"),
        _normalize_text(f"{team_name} {team_city}"),
    }
    if team_name_parts:
        variants.add(" ".join(team_name_parts[-1:]))
    if len(team_name_parts) >= 2:
        variants.add(" ".join(team_name_parts[-2:]))
    return {variant for variant in variants if variant}


def _resolve_team_side(
    *,
    game: dict[str, Any],
    team_tricode: str | None,
    fallback: str | None = None,
) -> str | None:
    tricode = str(team_tricode or "").strip().upper()
    if tricode and tricode == str(game.get("home_team_slug") or "").strip().upper():
        return "home"
    if tricode and tricode == str(game.get("away_team_slug") or "").strip().upper():
        return "away"
    return fallback


def _match_outcome_side(outcome_label: str | None, game: dict[str, Any]) -> str:
    label = _normalize_text(outcome_label)
    if not label:
        return "neutral"
    home_team = {
        "team_city": game.get("home_team_city"),
        "team_name": game.get("home_team_name"),
        "team_slug": game.get("home_team_slug"),
    }
    away_team = {
        "team_city": game.get("away_team_city"),
        "team_name": game.get("away_team_name"),
        "team_slug": game.get("away_team_slug"),
    }
    home_variants = _team_label_variants(home_team)
    away_variants = _team_label_variants(away_team)
    if label in home_variants or any(variant and variant in label for variant in home_variants):
        return "home"
    if label in away_variants or any(variant and variant in label for variant in away_variants):
        return "away"
    return "neutral"


def select_preferred_market_bundle(
    market_bundles: list[dict[str, Any]],
    requested_market_type: str = "moneyline",
    selected_market_id: str | None = None,
) -> dict[str, Any] | None:
    if not market_bundles:
        return None
    if selected_market_id:
        for bundle in market_bundles:
            if str(bundle.get("market_id")) == str(selected_market_id):
                return bundle

    def sort_key(bundle: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
        market_type = str(bundle.get("market_type") or "")
        requested_rank = 0 if market_type == requested_market_type else 1
        priced_rank = 0 if int(bundle.get("priced_outcomes_count") or 0) >= 2 else 1
        preferred_rank = (
            PREFERRED_MARKET_TYPES.index(market_type)
            if market_type in PREFERRED_MARKET_TYPES
            else len(PREFERRED_MARKET_TYPES)
        )
        total_tick_count = int(bundle.get("total_tick_count") or 0)
        priced_outcomes_count = int(bundle.get("priced_outcomes_count") or 0)
        question = str(bundle.get("question") or "")
        return (
            requested_rank,
            priced_rank,
            preferred_rank,
            -total_tick_count,
            -priced_outcomes_count,
            question,
        )

    return sorted(market_bundles, key=sort_key)[0]


def _load_game(connection: PsycopgConnection, *, game_id: str) -> dict[str, Any] | None:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                g.game_id,
                g.season,
                g.season_phase,
                g.season_phase_label,
                g.season_phase_sub_label,
                g.season_phase_subtype,
                g.series_text,
                g.series_game_number,
                g.game_date,
                g.game_start_time,
                g.game_status,
                g.game_status_text,
                g.period,
                g.game_clock,
                g.home_team_id,
                g.away_team_id,
                g.home_team_slug,
                g.away_team_slug,
                g.home_score,
                g.away_score,
                g.updated_at,
                ht.team_name AS home_team_name,
                ht.team_city AS home_team_city,
                at.team_name AS away_team_name,
                at.team_city AS away_team_city
            FROM nba.nba_games g
            LEFT JOIN nba.nba_teams ht ON ht.team_id = g.home_team_id
            LEFT JOIN nba.nba_teams at ON at.team_id = g.away_team_id
            WHERE g.game_id = %s
            LIMIT 1;
            """,
            (game_id,),
        )
        return fetchone_dict(cursor)


def _load_latest_feature_snapshot(
    connection: PsycopgConnection,
    *,
    game_id: str,
) -> dict[str, Any] | None:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                f.game_id,
                f.event_id,
                f.computed_at,
                f.feature_version,
                f.season,
                f.team_context_mode,
                f.pbp_event_count,
                f.lead_changes,
                f.home_largest_lead,
                f.away_largest_lead,
                f.home_losing_segments,
                f.away_losing_segments,
                f.home_led_and_lost,
                f.away_led_and_lost,
                f.covered_polymarket_game_flag,
                f.home_pre_game_price_min,
                f.home_pre_game_price_max,
                f.away_pre_game_price_min,
                f.away_pre_game_price_max,
                f.home_in_game_price_min,
                f.home_in_game_price_max,
                f.away_in_game_price_min,
                f.away_in_game_price_max,
                f.price_window_start,
                f.price_window_end,
                f.coverage_status,
                f.source_summary_json
            FROM nba.nba_game_feature_snapshots f
            WHERE f.game_id = %s
            ORDER BY f.computed_at DESC
            LIMIT 1;
            """,
            (game_id,),
        )
        return fetchone_dict(cursor)


def _load_market_rows(
    connection: PsycopgConnection,
    *,
    game_id: str,
    game: dict[str, Any],
) -> list[dict[str, Any]]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                l.nba_game_event_link_id,
                l.confidence,
                l.linked_by,
                l.linked_at,
                e.event_id,
                e.title AS event_title,
                e.canonical_slug,
                e.status AS event_status,
                e.start_time AS event_start_time,
                m.market_id,
                m.question,
                m.market_slug,
                m.market_type,
                m.settlement_status,
                m.created_at,
                o.outcome_id,
                o.outcome_label,
                o.outcome_index,
                o.token_id,
                count(t.ts)::int AS tick_count,
                count(*) FILTER (WHERE t.source = 'snapshot_fallback')::int AS fallback_tick_count,
                min(t.ts) AS first_tick_at,
                max(t.ts) AS last_tick_at,
                min(t.price) AS min_price,
                max(t.price) AS max_price,
                (
                    SELECT t2.ts
                    FROM market_data.outcome_price_ticks t2
                    WHERE t2.outcome_id = o.outcome_id
                    ORDER BY t2.ts DESC
                    LIMIT 1
                ) AS latest_tick_at,
                (
                    SELECT t3.price
                    FROM market_data.outcome_price_ticks t3
                    WHERE t3.outcome_id = o.outcome_id
                    ORDER BY t3.ts DESC
                    LIMIT 1
                ) AS latest_price
            FROM nba.nba_game_event_links l
            JOIN catalog.events e ON e.event_id = l.event_id
            JOIN catalog.markets m ON m.event_id = e.event_id
            JOIN catalog.outcomes o ON o.market_id = m.market_id
            LEFT JOIN market_data.outcome_price_ticks t ON t.outcome_id = o.outcome_id
            WHERE l.game_id = %s
            GROUP BY
                l.nba_game_event_link_id,
                l.confidence,
                l.linked_by,
                l.linked_at,
                e.event_id,
                e.title,
                e.canonical_slug,
                e.status,
                e.start_time,
                m.market_id,
                m.question,
                m.market_slug,
                m.market_type,
                m.settlement_status,
                m.created_at,
                o.outcome_id,
                o.outcome_label,
                o.outcome_index,
                o.token_id
            ORDER BY
                l.linked_at DESC,
                CASE
                    WHEN m.market_type = 'moneyline' THEN 0
                    WHEN m.market_type = 'spreads' THEN 1
                    WHEN m.market_type = 'first_half_spreads' THEN 2
                    ELSE 3
                END,
                m.question ASC,
                o.outcome_index ASC;
            """,
            (game_id,),
        )
        rows = fetchall_dicts(cursor)

    market_bundles: dict[str, dict[str, Any]] = {}
    for row in rows:
        market_id = str(row["market_id"])
        bundle = market_bundles.setdefault(
            market_id,
            {
                "market_id": row["market_id"],
                "question": row["question"],
                "market_slug": row["market_slug"],
                "market_type": row["market_type"],
                "settlement_status": row["settlement_status"],
                "created_at": row["created_at"],
                "event_id": row["event_id"],
                "event_title": row["event_title"],
                "canonical_slug": row["canonical_slug"],
                "event_status": row["event_status"],
                "event_start_time": row["event_start_time"],
                "link_confidence": row["confidence"],
                "linked_at": row["linked_at"],
                "outcomes": [],
                "total_tick_count": 0,
                "priced_outcomes_count": 0,
            },
        )

        outcome = {
            "outcome_id": row["outcome_id"],
            "outcome_label": row["outcome_label"],
            "outcome_index": row["outcome_index"],
            "token_id": row["token_id"],
            "side": _match_outcome_side(str(row.get("outcome_label") or ""), game),
            "tick_count": row["tick_count"],
            "fallback_tick_count": row["fallback_tick_count"],
            "first_tick_at": row["first_tick_at"],
            "last_tick_at": row["last_tick_at"],
            "min_price": row["min_price"],
            "max_price": row["max_price"],
            "latest_tick_at": row["latest_tick_at"],
            "latest_price": row["latest_price"],
        }
        bundle["outcomes"].append(outcome)
        bundle["total_tick_count"] += int(row.get("tick_count") or 0)
        if int(row.get("tick_count") or 0) > 0:
            bundle["priced_outcomes_count"] += 1

    ordered_bundles = list(market_bundles.values())
    ordered_bundles.sort(
        key=lambda bundle: (
            PREFERRED_MARKET_TYPES.index(bundle["market_type"])
            if bundle["market_type"] in PREFERRED_MARKET_TYPES
            else len(PREFERRED_MARKET_TYPES),
            bundle["question"] or "",
        )
    )
    return ordered_bundles


def _load_market_ticks(
    connection: PsycopgConnection,
    *,
    outcome_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not outcome_ids:
        return {}
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                outcome_id,
                ts,
                source,
                price,
                bid,
                ask,
                volume,
                liquidity
            FROM market_data.outcome_price_ticks
            WHERE outcome_id = ANY(%s::uuid[])
            ORDER BY outcome_id ASC, ts ASC;
            """,
            (outcome_ids,),
        )
        rows = fetchall_dicts(cursor)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["outcome_id"])].append(row)
    return grouped


def _load_play_by_play(
    connection: PsycopgConnection,
    *,
    game: dict[str, Any],
) -> dict[str, Any]:
    game_id = str(game["game_id"])
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                game_id,
                event_index,
                action_id,
                period,
                clock,
                description,
                home_score,
                away_score,
                is_score_change,
                payload_json
            FROM nba.nba_play_by_play
            WHERE game_id = %s
            ORDER BY event_index ASC;
            """,
            (game_id,),
        )
        rows = fetchall_dicts(cursor)

    previous_home = 0
    previous_away = 0
    items: list[dict[str, Any]] = []
    timed_events = 0
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    max_clock_elapsed = 0.0

    for row in rows:
        payload = row.get("payload_json") or {}
        raw_home_score = _safe_int(row.get("home_score"))
        raw_away_score = _safe_int(row.get("away_score"))
        home_score = previous_home if raw_home_score is None else raw_home_score
        away_score = previous_away if raw_away_score is None else raw_away_score
        delta_home = max(0, home_score - previous_home)
        delta_away = max(0, away_score - previous_away)
        previous_home = home_score
        previous_away = away_score

        time_actual = _parse_optional_datetime(payload.get("timeActual"))
        clock_elapsed = compute_elapsed_game_clock_seconds(
            _safe_int(row.get("period")),
            row.get("clock"),
        )
        if time_actual is None and game.get("game_start_time") and clock_elapsed is not None:
            game_start_time = _parse_optional_datetime(game.get("game_start_time"))
            if game_start_time is not None:
                time_actual = game_start_time + timedelta(seconds=clock_elapsed)

        if time_actual is not None:
            timed_events += 1
            first_event_at = time_actual if first_event_at is None else min(first_event_at, time_actual)
            last_event_at = time_actual if last_event_at is None else max(last_event_at, time_actual)
        if clock_elapsed is not None:
            max_clock_elapsed = max(max_clock_elapsed, clock_elapsed)

        scoring_side = None
        if delta_home > delta_away and delta_home > 0:
            scoring_side = "home"
        elif delta_away > delta_home and delta_away > 0:
            scoring_side = "away"

        actor_name = str(payload.get("playerNameI") or payload.get("playerName") or "").strip() or None
        team_tricode = str(payload.get("teamTricode") or "").strip().upper() or None
        team_side = _resolve_team_side(
            game=game,
            team_tricode=team_tricode,
            fallback=scoring_side,
        )
        points_total = _safe_int(payload.get("pointsTotal"))
        if points_total is None:
            points_total = max(delta_home, delta_away)
        assist_name = str(payload.get("assistPlayerNameInitial") or "").strip() or None
        sub_type = str(payload.get("subType") or "").strip() or None
        descriptor = str(payload.get("descriptor") or "").strip() or None
        shot_result = str(payload.get("shotResult") or "").strip() or None

        items.append(
            {
                "event_index": row["event_index"],
                "action_id": row["action_id"],
                "period": row["period"],
                "period_label": _format_period_label(_safe_int(row.get("period"))),
                "clock": row["clock"],
                "description": row["description"],
                "home_score": home_score,
                "away_score": away_score,
                "delta_home": delta_home,
                "delta_away": delta_away,
                "points_scored": max(delta_home, delta_away),
                "points_total": points_total,
                "is_score_change": bool(row.get("is_score_change") or delta_home or delta_away),
                "scoring_side": scoring_side,
                "team_side": team_side,
                "time_actual": time_actual,
                "action_type": payload.get("actionType"),
                "sub_type": sub_type,
                "descriptor": descriptor,
                "shot_result": shot_result,
                "shot_distance": _safe_float(payload.get("shotDistance")),
                "actor_name": actor_name,
                "actor_id": _safe_int(payload.get("personId")),
                "assist_name": assist_name,
                "team_tricode": team_tricode,
                "team_id": _safe_int(payload.get("teamId")),
                "x_legacy": _safe_float(payload.get("xLegacy")),
                "y_legacy": _safe_float(payload.get("yLegacy")),
                "clock_elapsed_seconds": clock_elapsed,
                "market_points": {},
            }
        )

    return {
        "items": items,
        "summary": {
            "event_count": len(items),
            "scoring_event_count": sum(1 for item in items if item["is_score_change"]),
            "timed_event_count": timed_events,
            "first_event_at": first_event_at,
            "last_event_at": last_event_at,
            "real_time_span": _format_duration((last_event_at - first_event_at).total_seconds())
            if first_event_at and last_event_at
            else "n/a",
            "clock_span": _format_duration(max_clock_elapsed),
            "max_clock_elapsed_seconds": max_clock_elapsed,
        },
    }


def _build_price_snapshots_for_events(
    pbp_items: list[dict[str, Any]],
    series_by_outcome: dict[str, list[dict[str, Any]]],
) -> None:
    for outcome_id, ticks in series_by_outcome.items():
        if not ticks:
            continue
        parsed_ticks = [
            {
                **tick,
                "_ts": _parse_optional_datetime(tick.get("ts")),
                "_price": _safe_float(tick.get("price")),
            }
            for tick in ticks
        ]
        parsed_ticks = [tick for tick in parsed_ticks if tick["_ts"] is not None and tick["_price"] is not None]
        tick_times = [tick["_ts"].timestamp() for tick in parsed_ticks]
        if not tick_times:
            continue

        for event in pbp_items:
            event_time = _parse_optional_datetime(event.get("time_actual"))
            if event_time is None:
                continue
            event_ts = event_time.timestamp()
            index = bisect_left(tick_times, event_ts)
            before_index = max(0, index - 1)
            after_index = min(index, len(parsed_ticks) - 1)
            before_tick = parsed_ticks[before_index]
            after_tick = parsed_ticks[after_index]

            if event_ts <= tick_times[0]:
                interpolated_price = before_tick["_price"]
                mode = "before_first_tick"
            elif event_ts >= tick_times[-1]:
                interpolated_price = parsed_ticks[-1]["_price"]
                mode = "after_last_tick"
            elif before_index == after_index or tick_times[after_index] == tick_times[before_index]:
                interpolated_price = before_tick["_price"]
                mode = "single_tick"
            else:
                span = tick_times[after_index] - tick_times[before_index]
                ratio = (event_ts - tick_times[before_index]) / span
                interpolated_price = before_tick["_price"] + (
                    (after_tick["_price"] - before_tick["_price"]) * ratio
                )
                mode = "between_ticks"

            event["market_points"][outcome_id] = {
                "price": interpolated_price,
                "mode": mode,
                "before_tick_at": before_tick["_ts"],
                "before_tick_price": before_tick["_price"],
                "before_tick_source": before_tick.get("source"),
                "after_tick_at": after_tick["_ts"],
                "after_tick_price": after_tick["_price"],
                "after_tick_source": after_tick.get("source"),
                "gap_before_seconds": max(0.0, event_ts - before_tick["_ts"].timestamp()),
                "gap_after_seconds": max(0.0, after_tick["_ts"].timestamp() - event_ts),
            }


def _build_market_summary(
    market_bundle: dict[str, Any],
    pbp_summary: dict[str, Any],
) -> dict[str, Any]:
    series = market_bundle.get("series") or []
    all_ticks = [
        _parse_optional_datetime(tick.get("ts"))
        for item in series
        for tick in item.get("ticks", [])
        if _parse_optional_datetime(tick.get("ts")) is not None
    ]
    full_price_start = min(all_ticks) if all_ticks else None
    full_price_end = max(all_ticks) if all_ticks else None
    first_event_at = _parse_optional_datetime(pbp_summary.get("first_event_at"))
    last_event_at = _parse_optional_datetime(pbp_summary.get("last_event_at"))
    chart_start = first_event_at or full_price_start
    chart_end = last_event_at or full_price_end

    pregame_seconds = None
    ingame_seconds = None
    postgame_seconds = None
    if full_price_start and full_price_end and first_event_at and last_event_at:
        pregame_seconds = max(0.0, (first_event_at - full_price_start).total_seconds())
        postgame_seconds = max(0.0, (full_price_end - last_event_at).total_seconds())
        overlap_start = max(full_price_start, first_event_at)
        overlap_end = min(full_price_end, last_event_at)
        ingame_seconds = max(0.0, (overlap_end - overlap_start).total_seconds())

    default_outcome_id = None
    for side in ("home", "away", "neutral"):
        for item in series:
            if item.get("side") == side and int(item.get("tick_count") or 0) > 0:
                default_outcome_id = item.get("outcome_id")
                break
        if default_outcome_id:
            break
    if default_outcome_id is None and series:
        default_outcome_id = series[0].get("outcome_id")

    return {
        "chart_start_at": chart_start,
        "chart_end_at": chart_end,
        "chart_span": _format_duration((chart_end - chart_start).total_seconds()) if chart_start and chart_end else "n/a",
        "pregame_span": _format_duration(pregame_seconds),
        "ingame_span": _format_duration(ingame_seconds),
        "postgame_span": _format_duration(postgame_seconds),
        "default_outcome_id": default_outcome_id,
        "price_domain": {"min": 0.0, "max": 1.0},
    }


__all__ = [
    "_build_market_summary",
    "_build_price_snapshots_for_events",
    "_load_game",
    "_load_latest_feature_snapshot",
    "_load_market_rows",
    "_load_market_ticks",
    "_load_play_by_play",
    "compute_elapsed_game_clock_seconds",
    "parse_nba_clock_seconds_remaining",
    "select_preferred_market_bundle",
]
