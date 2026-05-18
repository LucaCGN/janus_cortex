from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
from psycopg2.extras import Json

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.wnba.balldontlie.client import describe_historical_backfill_readiness
from app.data.nodes.wnba.live.live_stats import (
    fetch_boxscore_payload,
    fetch_todays_scoreboard_payload,
    normalize_boxscore_payload,
    normalize_scoreboard_payload,
)
from app.data.nodes.wnba.live.play_by_play import fetch_play_by_play_payload, normalize_play_by_play_payload
from app.data.nodes.wnba.schedule.season_schedule import fetch_season_schedule_payload, normalize_schedule_payload


_NAMESPACE = uuid.UUID("1aa54384-0c54-4b54-a53a-b34822337f18")


@dataclass
class WnbaSyncSummary:
    sync_run_id: str | None
    status: str
    rows_read: int
    rows_written: int
    teams_upserted: int
    games_upserted: int
    scoreboard_games: int
    context_games: int
    live_snapshots_written: int
    team_boxscore_rows_written: int
    player_boxscore_rows_written: int
    play_by_play_rows_written: int
    error_text: str | None = None


@dataclass
class WnbaLiveGameSyncSummary:
    sync_run_id: str | None
    status: str
    game_id: str
    rows_read: int
    rows_written: int
    live_snapshots_written: int
    team_boxscore_rows_written: int
    player_boxscore_rows_written: int
    play_by_play_rows_written: int
    error_text: str | None = None


@dataclass
class WnbaGameContextSyncResult:
    rows_read: int = 0
    rows_written: int = 0
    live_snapshots_written: int = 0
    team_boxscore_rows_written: int = 0
    player_boxscore_rows_written: int = 0
    play_by_play_rows_written: int = 0


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(part) for part in parts)))


def _json_or_none(value: Any) -> Json | None:
    if value is None:
        return None
    return Json(to_jsonable(value))


def _scalar(value: Any) -> Any | None:
    if isinstance(value, (dict, list)):
        return value
    if value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


def _safe_int(value: Any) -> int | None:
    value = _scalar(value)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    value = _scalar(value)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool | None:
    value = _scalar(value)
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    return None


def _safe_date(value: Any) -> date | None:
    value = _scalar(value)
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def _safe_dt(value: Any) -> datetime | None:
    value = _scalar(value)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _insert_sync_run(connection: Any, *, provider_id: str, module_id: str) -> str:
    sync_run_id = str(uuid.uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.sync_runs (
                sync_run_id, provider_id, module_id, pipeline_name, run_type, status, started_at, meta_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                sync_run_id,
                provider_id,
                module_id,
                "daily.wnba.sync_postgres",
                "scheduled",
                "running",
                datetime.now(timezone.utc),
                Json({"source": "wnba_cdn"}),
            ),
        )
    return sync_run_id


def _update_sync_run(
    connection: Any,
    *,
    sync_run_id: str,
    status: str,
    rows_read: int,
    rows_written: int,
    error_text: str | None = None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE core.sync_runs
            SET status = %s,
                ended_at = %s,
                rows_read = %s,
                rows_written = %s,
                error_text = %s
            WHERE sync_run_id = %s;
            """,
            (status, datetime.now(timezone.utc), rows_read, rows_written, error_text, sync_run_id),
        )


def _insert_raw_payload(
    connection: Any,
    *,
    sync_run_id: str,
    provider_id: str,
    endpoint: str,
    payload: Any,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.raw_payloads (
                raw_payload_id, sync_run_id, provider_id, endpoint, external_id, fetched_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                str(uuid.uuid4()),
                sync_run_id,
                provider_id,
                endpoint,
                endpoint,
                datetime.now(timezone.utc),
                Json(to_jsonable(payload)),
            ),
        )


def _provider_and_module(connection: Any) -> tuple[str, str]:
    repo = JanusUpsertRepository(connection)
    provider_id = repo.upsert_provider(
        provider_id=_uuid_for("provider", "wnba_cdn"),
        code="wnba_cdn",
        name="WNBA CDN",
        category="sports_data",
        base_url="https://cdn.wnba.com",
        auth_type="none",
    )
    module_id = repo.upsert_module(
        module_id=_uuid_for("module", "wnba_metadata_sync"),
        code="wnba_metadata_sync",
        name="WNBA Metadata Sync",
        description="WNBA schedule/live/boxscore/play-by-play ingestion to postgres",
        owner="janus",
    )
    return provider_id, module_id


def _upsert_wnba_team(connection: Any, row: dict[str, Any]) -> bool:
    team_id = _safe_int(row.get("team_id"))
    if team_id is None or team_id <= 0:
        return False
    team_name = str(_scalar(row.get("team_name")) or _scalar(row.get("team_tricode")) or team_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_teams (
                team_id, team_slug, team_tricode, team_name, team_city, source, fetched_at, raw_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (team_id)
            DO UPDATE SET
                team_slug = COALESCE(EXCLUDED.team_slug, wnba.wnba_teams.team_slug),
                team_tricode = COALESCE(EXCLUDED.team_tricode, wnba.wnba_teams.team_tricode),
                team_name = EXCLUDED.team_name,
                team_city = COALESCE(EXCLUDED.team_city, wnba.wnba_teams.team_city),
                source = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at,
                raw_payload_json = COALESCE(EXCLUDED.raw_payload_json, wnba.wnba_teams.raw_payload_json),
                updated_at = now()
            RETURNING team_id;
            """,
            (
                team_id,
                _scalar(row.get("team_slug")),
                _scalar(row.get("team_tricode")),
                team_name,
                _scalar(row.get("team_city")),
                str(_scalar(row.get("source")) or "wnba_cdn"),
                _safe_dt(row.get("fetched_at")) or datetime.now(timezone.utc),
                _json_or_none(_scalar(row.get("raw"))),
            ),
        )
        return cursor.fetchone() is not None


def _upsert_wnba_player(connection: Any, row: dict[str, Any]) -> bool:
    player_id = _safe_int(row.get("player_id"))
    if player_id is None or player_id <= 0:
        return False
    player_name = str(_scalar(row.get("player_name")) or player_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_players (
                player_id, player_name, first_name, family_name, team_id, team_tricode,
                jersey_num, position, status, source, fetched_at, raw_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id)
            DO UPDATE SET
                player_name = EXCLUDED.player_name,
                first_name = COALESCE(EXCLUDED.first_name, wnba.wnba_players.first_name),
                family_name = COALESCE(EXCLUDED.family_name, wnba.wnba_players.family_name),
                team_id = COALESCE(EXCLUDED.team_id, wnba.wnba_players.team_id),
                team_tricode = COALESCE(EXCLUDED.team_tricode, wnba.wnba_players.team_tricode),
                jersey_num = COALESCE(EXCLUDED.jersey_num, wnba.wnba_players.jersey_num),
                position = COALESCE(EXCLUDED.position, wnba.wnba_players.position),
                status = COALESCE(EXCLUDED.status, wnba.wnba_players.status),
                source = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at,
                raw_payload_json = COALESCE(EXCLUDED.raw_payload_json, wnba.wnba_players.raw_payload_json),
                updated_at = now()
            RETURNING player_id;
            """,
            (
                player_id,
                player_name,
                _scalar(row.get("first_name")),
                _scalar(row.get("family_name")),
                _safe_int(row.get("team_id")),
                _scalar(row.get("team_tricode")),
                _scalar(row.get("jersey_num")),
                _scalar(row.get("position")),
                _scalar(row.get("status")),
                str(_scalar(row.get("source")) or "wnba_cdn"),
                _safe_dt(row.get("fetched_at")) or datetime.now(timezone.utc),
                _json_or_none(_scalar(row.get("raw"))),
            ),
        )
        return cursor.fetchone() is not None


def _upsert_wnba_game(connection: Any, row: dict[str, Any]) -> bool:
    game_id = str(_scalar(row.get("game_id")) or "").strip()
    if not game_id:
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_games (
                game_id, season, league_id, game_code, game_date, game_start_time,
                game_status, game_status_text, period, game_clock, season_phase,
                season_phase_label, season_phase_sub_label, season_phase_subtype,
                series_text, series_game_number, home_team_id, away_team_id,
                home_team_tricode, away_team_tricode, home_team_slug, away_team_slug,
                home_score, away_score, arena_name, arena_city, arena_state,
                is_neutral, postponed_status, source, fetched_at, raw_payload_json, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (game_id)
            DO UPDATE SET
                season = EXCLUDED.season,
                league_id = EXCLUDED.league_id,
                game_code = COALESCE(EXCLUDED.game_code, wnba.wnba_games.game_code),
                game_date = COALESCE(EXCLUDED.game_date, wnba.wnba_games.game_date),
                game_start_time = COALESCE(EXCLUDED.game_start_time, wnba.wnba_games.game_start_time),
                game_status = COALESCE(EXCLUDED.game_status, wnba.wnba_games.game_status),
                game_status_text = COALESCE(EXCLUDED.game_status_text, wnba.wnba_games.game_status_text),
                period = COALESCE(EXCLUDED.period, wnba.wnba_games.period),
                game_clock = COALESCE(EXCLUDED.game_clock, wnba.wnba_games.game_clock),
                season_phase = COALESCE(EXCLUDED.season_phase, wnba.wnba_games.season_phase),
                season_phase_label = COALESCE(EXCLUDED.season_phase_label, wnba.wnba_games.season_phase_label),
                season_phase_sub_label = COALESCE(EXCLUDED.season_phase_sub_label, wnba.wnba_games.season_phase_sub_label),
                season_phase_subtype = COALESCE(EXCLUDED.season_phase_subtype, wnba.wnba_games.season_phase_subtype),
                series_text = COALESCE(EXCLUDED.series_text, wnba.wnba_games.series_text),
                series_game_number = COALESCE(EXCLUDED.series_game_number, wnba.wnba_games.series_game_number),
                home_team_id = COALESCE(EXCLUDED.home_team_id, wnba.wnba_games.home_team_id),
                away_team_id = COALESCE(EXCLUDED.away_team_id, wnba.wnba_games.away_team_id),
                home_team_tricode = COALESCE(EXCLUDED.home_team_tricode, wnba.wnba_games.home_team_tricode),
                away_team_tricode = COALESCE(EXCLUDED.away_team_tricode, wnba.wnba_games.away_team_tricode),
                home_team_slug = COALESCE(EXCLUDED.home_team_slug, wnba.wnba_games.home_team_slug),
                away_team_slug = COALESCE(EXCLUDED.away_team_slug, wnba.wnba_games.away_team_slug),
                home_score = COALESCE(EXCLUDED.home_score, wnba.wnba_games.home_score),
                away_score = COALESCE(EXCLUDED.away_score, wnba.wnba_games.away_score),
                source = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at,
                raw_payload_json = COALESCE(EXCLUDED.raw_payload_json, wnba.wnba_games.raw_payload_json),
                updated_at = EXCLUDED.updated_at
            RETURNING game_id;
            """,
            (
                game_id,
                str(_scalar(row.get("season")) or "2026"),
                str(_scalar(row.get("league_id")) or "10"),
                _scalar(row.get("game_code")),
                _safe_date(row.get("game_date")),
                _safe_dt(row.get("game_start_time")),
                _safe_int(row.get("game_status")),
                _scalar(row.get("game_status_text")),
                _safe_int(row.get("period")),
                _scalar(row.get("game_clock")),
                str(_scalar(row.get("season_phase")) or "regular_season"),
                _scalar(row.get("season_phase_label")),
                _scalar(row.get("season_phase_sub_label")),
                _scalar(row.get("season_phase_subtype")),
                _scalar(row.get("series_text")),
                _scalar(row.get("series_game_number")),
                _safe_int(row.get("home_team_id")),
                _safe_int(row.get("away_team_id")),
                _scalar(row.get("home_team_tricode")),
                _scalar(row.get("away_team_tricode")),
                _scalar(row.get("home_team_slug")),
                _scalar(row.get("away_team_slug")),
                _safe_int(row.get("home_score")),
                _safe_int(row.get("away_score")),
                _scalar(row.get("arena_name")),
                _scalar(row.get("arena_city")),
                _scalar(row.get("arena_state")),
                _safe_bool(row.get("is_neutral")),
                _scalar(row.get("postponed_status")),
                str(_scalar(row.get("source")) or "wnba_cdn"),
                _safe_dt(row.get("fetched_at")) or datetime.now(timezone.utc),
                _json_or_none(_scalar(row.get("raw"))),
                datetime.now(timezone.utc),
            ),
        )
        return cursor.fetchone() is not None


def _insert_wnba_live_snapshot(
    connection: Any,
    *,
    snapshot: dict[str, Any],
    captured_at: datetime,
    raw_payload: dict[str, Any],
) -> bool:
    game_id = str(snapshot.get("game_id") or "").strip()
    if not game_id:
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_live_game_snapshots (
                game_id, captured_at, source, fetched_at, game_status, game_status_text,
                period, clock, home_team_id, away_team_id, home_team_tricode,
                away_team_tricode, home_score, away_score, normalized_json, raw_payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (game_id, captured_at, source) DO NOTHING
            RETURNING game_id;
            """,
            (
                game_id,
                captured_at,
                str(snapshot.get("source") or "wnba_cdn_boxscore"),
                _safe_dt(snapshot.get("fetched_at")) or captured_at,
                _safe_int(snapshot.get("game_status")),
                _scalar(snapshot.get("game_status_text")),
                _safe_int(snapshot.get("period")),
                _scalar(snapshot.get("clock")),
                _safe_int(snapshot.get("home_team_id")),
                _safe_int(snapshot.get("away_team_id")),
                _scalar(snapshot.get("home_team_tricode")),
                _scalar(snapshot.get("away_team_tricode")),
                _safe_int(snapshot.get("home_score")),
                _safe_int(snapshot.get("away_score")),
                _json_or_none({k: v for k, v in snapshot.items() if k != "raw"}),
                _json_or_none(raw_payload),
            ),
        )
        return cursor.fetchone() is not None


def _insert_wnba_team_boxscore(connection: Any, row: dict[str, Any], *, captured_at: datetime) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_team_boxscore_snapshots (
                game_id, team_id, captured_at, source, fetched_at, team_side, team_tricode,
                period, clock, minutes, points, rebounds, assists, steals, blocks,
                turnovers, fgm, fga, fg3m, fg3a, ftm, fta, plus_minus, stats_json, raw_payload_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (game_id, team_id, captured_at, source) DO NOTHING
            RETURNING game_id;
            """,
            (
                _scalar(row.get("game_id")),
                _safe_int(row.get("team_id")),
                captured_at,
                str(_scalar(row.get("source")) or "wnba_cdn_boxscore"),
                _safe_dt(row.get("fetched_at")) or captured_at,
                _scalar(row.get("team_side")),
                _scalar(row.get("team_tricode")),
                _safe_int(row.get("period")),
                _scalar(row.get("clock")),
                _scalar(row.get("minutes")),
                _safe_int(row.get("points")),
                _safe_int(row.get("rebounds")),
                _safe_int(row.get("assists")),
                _safe_int(row.get("steals")),
                _safe_int(row.get("blocks")),
                _safe_int(row.get("turnovers")),
                _safe_int(row.get("fgm")),
                _safe_int(row.get("fga")),
                _safe_int(row.get("fg3m")),
                _safe_int(row.get("fg3a")),
                _safe_int(row.get("ftm")),
                _safe_int(row.get("fta")),
                _safe_float(row.get("plus_minus")),
                _json_or_none(_scalar(row.get("stats_json"))),
                _json_or_none(_scalar(row.get("raw"))),
            ),
        )
        return cursor.fetchone() is not None


def _insert_wnba_player_boxscore(connection: Any, row: dict[str, Any], *, captured_at: datetime) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_player_boxscore_snapshots (
                game_id, player_id, team_id, captured_at, source, fetched_at, team_side,
                team_tricode, player_name, first_name, family_name, jersey_num, position,
                status, starter, oncourt, played, order_no, minutes, minutes_calculated,
                points, rebounds, assists, steals, blocks, turnovers, fgm, fga, fg3m,
                fg3a, ftm, fta, fouls_personal, plus_minus, stats_json, raw_player_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (game_id, player_id, captured_at, source) DO NOTHING
            RETURNING game_id;
            """,
            (
                _scalar(row.get("game_id")),
                _safe_int(row.get("player_id")),
                _safe_int(row.get("team_id")),
                captured_at,
                str(_scalar(row.get("source")) or "wnba_cdn_boxscore"),
                _safe_dt(row.get("fetched_at")) or captured_at,
                _scalar(row.get("team_side")),
                _scalar(row.get("team_tricode")),
                _scalar(row.get("player_name")),
                _scalar(row.get("first_name")),
                _scalar(row.get("family_name")),
                _scalar(row.get("jersey_num")),
                _scalar(row.get("position")),
                _scalar(row.get("status")),
                _safe_bool(row.get("starter")),
                _safe_bool(row.get("oncourt")),
                _safe_bool(row.get("played")),
                _safe_int(row.get("order_no")),
                _scalar(row.get("minutes")),
                _scalar(row.get("minutes_calculated")),
                _safe_int(row.get("points")),
                _safe_int(row.get("rebounds")),
                _safe_int(row.get("assists")),
                _safe_int(row.get("steals")),
                _safe_int(row.get("blocks")),
                _safe_int(row.get("turnovers")),
                _safe_int(row.get("fgm")),
                _safe_int(row.get("fga")),
                _safe_int(row.get("fg3m")),
                _safe_int(row.get("fg3a")),
                _safe_int(row.get("ftm")),
                _safe_int(row.get("fta")),
                _safe_int(row.get("fouls_personal")),
                _safe_float(row.get("plus_minus")),
                _json_or_none(_scalar(row.get("stats_json"))),
                _json_or_none(_scalar(row.get("raw"))),
            ),
        )
        return cursor.fetchone() is not None


def _upsert_wnba_play_by_play_event(connection: Any, row: dict[str, Any]) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO wnba.wnba_play_by_play (
                game_id, event_index, action_id, action_number, order_number, period,
                period_type, clock, time_actual, team_id, team_tricode, person_id,
                player_name, action_type, sub_type, description, home_score, away_score,
                points_home, points_away, is_score_change, scoring_team_id,
                scoring_team_tricode, substitution_direction, substitution_person_id,
                substitution_player_name, qualifiers_json, source, fetched_at, raw_payload_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (game_id, event_index)
            DO UPDATE SET
                action_id = EXCLUDED.action_id,
                action_number = EXCLUDED.action_number,
                order_number = EXCLUDED.order_number,
                period = EXCLUDED.period,
                period_type = EXCLUDED.period_type,
                clock = EXCLUDED.clock,
                time_actual = EXCLUDED.time_actual,
                team_id = EXCLUDED.team_id,
                team_tricode = EXCLUDED.team_tricode,
                person_id = EXCLUDED.person_id,
                player_name = EXCLUDED.player_name,
                action_type = EXCLUDED.action_type,
                sub_type = EXCLUDED.sub_type,
                description = EXCLUDED.description,
                home_score = EXCLUDED.home_score,
                away_score = EXCLUDED.away_score,
                points_home = EXCLUDED.points_home,
                points_away = EXCLUDED.points_away,
                is_score_change = EXCLUDED.is_score_change,
                scoring_team_id = EXCLUDED.scoring_team_id,
                scoring_team_tricode = EXCLUDED.scoring_team_tricode,
                substitution_direction = EXCLUDED.substitution_direction,
                substitution_person_id = EXCLUDED.substitution_person_id,
                substitution_player_name = EXCLUDED.substitution_player_name,
                qualifiers_json = EXCLUDED.qualifiers_json,
                source = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at,
                raw_payload_json = EXCLUDED.raw_payload_json
            RETURNING game_id;
            """,
            (
                _scalar(row.get("game_id")),
                _safe_int(row.get("event_index")),
                _scalar(row.get("action_id")),
                _safe_int(row.get("action_number")),
                _safe_int(row.get("order_number")),
                _safe_int(row.get("period")),
                _scalar(row.get("period_type")),
                _scalar(row.get("clock")),
                _safe_dt(row.get("time_actual")),
                _safe_int(row.get("team_id")),
                _scalar(row.get("team_tricode")),
                _safe_int(row.get("person_id")),
                _scalar(row.get("player_name")),
                _scalar(row.get("action_type")),
                _scalar(row.get("sub_type")),
                _scalar(row.get("description")),
                _safe_int(row.get("home_score")),
                _safe_int(row.get("away_score")),
                _safe_int(row.get("points_home")),
                _safe_int(row.get("points_away")),
                _safe_bool(row.get("is_score_change")),
                _safe_int(row.get("scoring_team_id")),
                _scalar(row.get("scoring_team_tricode")),
                _scalar(row.get("substitution_direction")),
                _safe_int(row.get("substitution_person_id")),
                _scalar(row.get("substitution_player_name")),
                _json_or_none(_scalar(row.get("qualifiers"))),
                str(_scalar(row.get("source")) or "wnba_cdn_play_by_play"),
                _safe_dt(row.get("fetched_at")) or datetime.now(timezone.utc),
                _json_or_none(_scalar(row.get("raw"))),
            ),
        )
        return cursor.fetchone() is not None


def _context_game_ids(scoreboard_df: pd.DataFrame, *, include_final_context: bool, final_context_game_limit: int) -> list[str]:
    if scoreboard_df.empty:
        return []
    live_ids = scoreboard_df[scoreboard_df["game_status"] == 2]["game_id"].astype(str).tolist()
    final_ids = scoreboard_df[scoreboard_df["game_status"] == 3]["game_id"].astype(str).tolist()
    selected = [*live_ids]
    if include_final_context:
        selected.extend(final_ids[: max(final_context_game_limit, 0)])
    out: list[str] = []
    seen: set[str] = set()
    for game_id in selected:
        if game_id and game_id not in seen:
            seen.add(game_id)
            out.append(game_id)
    return out


def _sync_wnba_game_live_context(
    *,
    connection: Any,
    sync_run_id: str,
    provider_id: str,
    game_id: str,
    season: str,
    include_live_snapshots: bool,
    include_boxscore: bool,
    include_play_by_play: bool,
) -> WnbaGameContextSyncResult:
    result = WnbaGameContextSyncResult()
    captured_at = datetime.now(timezone.utc)

    if include_live_snapshots or include_boxscore:
        boxscore_payload = fetch_boxscore_payload(game_id)
        result.rows_read += 1
        _insert_raw_payload(
            connection,
            sync_run_id=sync_run_id,
            provider_id=provider_id,
            endpoint=f"/wnba/live/{game_id}/boxscore",
            payload=boxscore_payload,
        )
        frames = normalize_boxscore_payload(boxscore_payload, fetched_at=captured_at)
        snapshot = frames.snapshot
        raw_game = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        for side in ("home", "away"):
            team = raw_game.get(f"{side}Team") if isinstance(raw_game.get(f"{side}Team"), dict) else {}
            if team:
                if _upsert_wnba_team(
                    connection,
                    {
                        "team_id": team.get("teamId"),
                        "team_tricode": team.get("teamTricode"),
                        "team_name": team.get("teamName"),
                        "team_city": team.get("teamCity"),
                        "source": "wnba_cdn_boxscore",
                        "fetched_at": captured_at,
                        "raw": team,
                    },
                ):
                    result.rows_written += 1
        if snapshot:
            game_row = {
                "game_id": game_id,
                "season": season,
                "league_id": "10",
                "game_status": snapshot.get("game_status"),
                "game_status_text": snapshot.get("game_status_text"),
                "period": snapshot.get("period"),
                "game_clock": snapshot.get("clock"),
                "home_team_id": snapshot.get("home_team_id"),
                "away_team_id": snapshot.get("away_team_id"),
                "home_team_tricode": snapshot.get("home_team_tricode"),
                "away_team_tricode": snapshot.get("away_team_tricode"),
                "home_score": snapshot.get("home_score"),
                "away_score": snapshot.get("away_score"),
                "source": "wnba_cdn_boxscore",
                "fetched_at": captured_at,
                "raw": raw_game,
            }
            _upsert_wnba_game(connection, game_row)
            if include_live_snapshots and _insert_wnba_live_snapshot(
                connection,
                snapshot=snapshot,
                captured_at=captured_at,
                raw_payload=boxscore_payload,
            ):
                result.live_snapshots_written += 1
                result.rows_written += 1

        if include_boxscore:
            for _, row in frames.teams.iterrows():
                if _insert_wnba_team_boxscore(connection, row.to_dict(), captured_at=captured_at):
                    result.team_boxscore_rows_written += 1
                    result.rows_written += 1
            for _, row in frames.players.iterrows():
                player_row = row.to_dict()
                _upsert_wnba_player(connection, player_row)
                if _insert_wnba_player_boxscore(connection, player_row, captured_at=captured_at):
                    result.player_boxscore_rows_written += 1
                    result.rows_written += 1

    if include_play_by_play:
        pbp_payload = fetch_play_by_play_payload(game_id)
        pbp_df = normalize_play_by_play_payload(pbp_payload, game_id=game_id, fetched_at=captured_at)
        result.rows_read += len(pbp_df)
        _insert_raw_payload(
            connection,
            sync_run_id=sync_run_id,
            provider_id=provider_id,
            endpoint=f"/wnba/live/{game_id}/play-by-play",
            payload=pbp_payload,
        )
        for _, row in pbp_df.iterrows():
            if _upsert_wnba_play_by_play_event(connection, row.to_dict()):
                result.play_by_play_rows_written += 1
                result.rows_written += 1

    return result


def run_wnba_current_season_sync(
    *,
    season: str = "2026",
    schedule_window_days: int | None = None,
    include_live_snapshots: bool = True,
    include_boxscore: bool = True,
    include_play_by_play: bool = True,
    include_final_context: bool = True,
    final_context_game_limit: int = 4,
) -> WnbaSyncSummary:
    rows_read = 0
    rows_written = 0
    teams_upserted = 0
    games_upserted = 0
    live_snapshots_written = 0
    team_boxscore_rows_written = 0
    player_boxscore_rows_written = 0
    play_by_play_rows_written = 0

    schedule_payload = fetch_season_schedule_payload()
    schedule_df, teams_df = normalize_schedule_payload(schedule_payload, season=season)
    rows_read += len(schedule_df) + len(teams_df)

    scoreboard_payload = fetch_todays_scoreboard_payload()
    scoreboard_df = normalize_scoreboard_payload(scoreboard_payload)
    rows_read += len(scoreboard_df)

    if schedule_window_days is not None and not schedule_df.empty:
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=max(schedule_window_days, 0))
        end_date = today + timedelta(days=max(schedule_window_days, 0))
        game_dates = pd.to_datetime(schedule_df["game_date"], errors="coerce").dt.date
        schedule_df = schedule_df[(game_dates >= start_date) & (game_dates <= end_date)].reset_index(drop=True)

    with managed_connection() as connection:
        provider_id, module_id = _provider_and_module(connection)
        sync_run_id = _insert_sync_run(connection, provider_id=provider_id, module_id=module_id)
        connection.commit()

        try:
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/wnba/schedule/season",
                payload=schedule_payload,
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/wnba/scoreboard/today",
                payload=scoreboard_payload,
            )

            for _, row in teams_df.iterrows():
                if _upsert_wnba_team(connection, row.to_dict()):
                    teams_upserted += 1
                    rows_written += 1
            for _, row in schedule_df.iterrows():
                if _upsert_wnba_game(connection, row.to_dict()):
                    games_upserted += 1
                    rows_written += 1

            for _, row in scoreboard_df.iterrows():
                raw_game = _scalar(row.get("raw"))
                if isinstance(raw_game, dict):
                    for side in ("home", "away"):
                        team = raw_game.get(f"{side}Team") if isinstance(raw_game.get(f"{side}Team"), dict) else {}
                        if team:
                            if _upsert_wnba_team(
                                connection,
                                {
                                    "team_id": team.get("teamId"),
                                    "team_tricode": team.get("teamTricode"),
                                    "team_name": team.get("teamName") or team.get("teamTricode"),
                                    "team_city": team.get("teamCity"),
                                    "source": "wnba_cdn_scoreboard",
                                    "fetched_at": row.get("fetched_at"),
                                    "raw": team,
                                },
                            ):
                                teams_upserted += 1
                                rows_written += 1
                game_row = row.to_dict()
                game_row["season"] = season
                game_row["game_clock"] = game_row.get("game_clock")
                if _upsert_wnba_game(connection, game_row):
                    games_upserted += 1
                    rows_written += 1

            context_game_ids = _context_game_ids(
                scoreboard_df,
                include_final_context=include_final_context,
                final_context_game_limit=final_context_game_limit,
            )
            for game_id in context_game_ids:
                context_result = _sync_wnba_game_live_context(
                    connection=connection,
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    game_id=game_id,
                    season=season,
                    include_live_snapshots=include_live_snapshots,
                    include_boxscore=include_boxscore,
                    include_play_by_play=include_play_by_play,
                )
                rows_read += context_result.rows_read
                rows_written += context_result.rows_written
                live_snapshots_written += context_result.live_snapshots_written
                team_boxscore_rows_written += context_result.team_boxscore_rows_written
                player_boxscore_rows_written += context_result.player_boxscore_rows_written
                play_by_play_rows_written += context_result.play_by_play_rows_written

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
            )
            connection.commit()
            return WnbaSyncSummary(
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                teams_upserted=teams_upserted,
                games_upserted=games_upserted,
                scoreboard_games=len(scoreboard_df),
                context_games=len(context_game_ids),
                live_snapshots_written=live_snapshots_written,
                team_boxscore_rows_written=team_boxscore_rows_written,
                player_boxscore_rows_written=player_boxscore_rows_written,
                play_by_play_rows_written=play_by_play_rows_written,
            )
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=repr(exc),
            )
            connection.commit()
            return WnbaSyncSummary(
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                teams_upserted=teams_upserted,
                games_upserted=games_upserted,
                scoreboard_games=len(scoreboard_df),
                context_games=0,
                live_snapshots_written=live_snapshots_written,
                team_boxscore_rows_written=team_boxscore_rows_written,
                player_boxscore_rows_written=player_boxscore_rows_written,
                play_by_play_rows_written=play_by_play_rows_written,
                error_text=repr(exc),
            )


def run_wnba_live_game_sync(
    *,
    game_id: str,
    season: str = "2026",
    include_live_snapshots: bool = True,
    include_boxscore: bool = True,
    include_play_by_play: bool = True,
) -> WnbaLiveGameSyncSummary:
    rows_read = 0
    rows_written = 0

    with managed_connection() as connection:
        provider_id, module_id = _provider_and_module(connection)
        sync_run_id = _insert_sync_run(connection, provider_id=provider_id, module_id=module_id)
        connection.commit()

        try:
            context_result = _sync_wnba_game_live_context(
                connection=connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                game_id=game_id,
                season=season,
                include_live_snapshots=include_live_snapshots,
                include_boxscore=include_boxscore,
                include_play_by_play=include_play_by_play,
            )
            rows_read += context_result.rows_read
            rows_written += context_result.rows_written
            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
            )
            connection.commit()
            return WnbaLiveGameSyncSummary(
                sync_run_id=sync_run_id,
                status="success",
                game_id=game_id,
                rows_read=rows_read,
                rows_written=rows_written,
                live_snapshots_written=context_result.live_snapshots_written,
                team_boxscore_rows_written=context_result.team_boxscore_rows_written,
                player_boxscore_rows_written=context_result.player_boxscore_rows_written,
                play_by_play_rows_written=context_result.play_by_play_rows_written,
            )
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=repr(exc),
            )
            connection.commit()
            return WnbaLiveGameSyncSummary(
                sync_run_id=sync_run_id,
                status="error",
                game_id=game_id,
                rows_read=rows_read,
                rows_written=rows_written,
                live_snapshots_written=0,
                team_boxscore_rows_written=0,
                player_boxscore_rows_written=0,
                play_by_play_rows_written=0,
                error_text=repr(exc),
            )


def record_wnba_historical_backfill_readiness(*, season: str = "2025") -> dict[str, Any]:
    readiness = describe_historical_backfill_readiness(season=season)
    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    with managed_connection() as connection:
        for blocker in blockers:
            blocker_id = _uuid_for("wnba_backfill_blocker", season, str(blocker.get("code")))
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO wnba.wnba_backfill_blockers (
                        blocker_id, season, source, requirement, status, details_json
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (blocker_id)
                    DO UPDATE SET
                        requirement = EXCLUDED.requirement,
                        status = EXCLUDED.status,
                        detected_at = now(),
                        details_json = EXCLUDED.details_json,
                        resolved_at = NULL;
                    """,
                    (
                        blocker_id,
                        str(season),
                        str(blocker.get("source") or "balldontlie_wnba"),
                        str(blocker.get("requirement") or blocker.get("code")),
                        "blocked",
                        Json(to_jsonable(blocker)),
                    ),
                )
        connection.commit()
    return readiness


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WNBA postgres ingestion sync.")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--schedule-window-days", type=int, default=None)
    parser.add_argument("--game-id", default=None, help="Run a game-scoped live sync instead of season sync.")
    parser.add_argument("--skip-live-snapshots", action="store_true")
    parser.add_argument("--skip-boxscore", action="store_true")
    parser.add_argument("--skip-play-by-play", action="store_true")
    parser.add_argument("--skip-final-context", action="store_true")
    parser.add_argument("--final-context-game-limit", type=int, default=4)
    parser.add_argument("--record-last-season-backfill-readiness", action="store_true")
    parser.add_argument("--last-season", default="2025")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.record_last_season_backfill_readiness:
        readiness = record_wnba_historical_backfill_readiness(season=args.last_season)
        print(f"last_season_backfill_status={readiness['status']}")
        for blocker in readiness.get("blockers", []):
            print(f"blocker={blocker.get('code')} requirement={blocker.get('requirement')}")

    if args.game_id:
        summary = run_wnba_live_game_sync(
            game_id=args.game_id,
            season=args.season,
            include_live_snapshots=not args.skip_live_snapshots,
            include_boxscore=not args.skip_boxscore,
            include_play_by_play=not args.skip_play_by_play,
        )
    else:
        summary = run_wnba_current_season_sync(
            season=args.season,
            schedule_window_days=args.schedule_window_days,
            include_live_snapshots=not args.skip_live_snapshots,
            include_boxscore=not args.skip_boxscore,
            include_play_by_play=not args.skip_play_by_play,
            include_final_context=not args.skip_final_context,
            final_context_game_limit=args.final_context_game_limit,
        )

    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
    print(
        " | ".join(
            [
                f"live_snapshots_written={summary.live_snapshots_written}",
                f"team_boxscore_rows_written={summary.team_boxscore_rows_written}",
                f"player_boxscore_rows_written={summary.player_boxscore_rows_written}",
                f"play_by_play_rows_written={summary.play_by_play_rows_written}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
