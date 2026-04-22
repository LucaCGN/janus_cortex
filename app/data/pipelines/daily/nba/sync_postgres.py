from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
from nba_api.live.nba.endpoints import scoreboard as nba_live_scoreboard
from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.nba.live.live_stats import fetch_live_scoreboard
from app.data.nodes.nba.live.play_by_play import PlayByPlayRequest, fetch_play_by_play_df
from app.data.nodes.nba.schedule.season_schedule import fetch_season_schedule_df


_NAMESPACE = uuid.UUID("90cd793c-b12d-430a-8fd6-ddfd26f1258f")


@dataclass
class NbaSyncSummary:
    sync_run_id: str | None
    status: str
    rows_read: int
    rows_written: int
    teams_upserted: int
    games_upserted: int
    missing_today_detected: int
    missing_today_inserted: int
    ongoing_games: int
    live_snapshots_written: int
    play_by_play_rows_written: int
    error_text: str | None = None


@dataclass
class NbaLiveGameSyncSummary:
    sync_run_id: str | None
    status: str
    game_id: str
    rows_read: int
    rows_written: int
    live_snapshots_written: int
    play_by_play_rows_written: int
    error_text: str | None = None


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_positive_int(value: Any) -> int | None:
    parsed = _safe_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _safe_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def _season_phase_from_game_id(game_id: str | None) -> str | None:
    prefix = str(game_id or "").strip()[:3]
    return {
        "001": "preseason",
        "002": "regular_season",
        "003": "all_star",
        "004": "playoffs",
        "005": "play_in",
        "006": "nba_cup",
    }.get(prefix)


def _extract_scoreboard_games() -> list[dict[str, Any]]:
    try:
        board = nba_live_scoreboard.ScoreBoard()
        data = board.games.get_dict()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


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
                "daily.nba.sync_postgres",
                "scheduled",
                "running",
                datetime.now(timezone.utc),
                Json({"source": "nba_live + schedule"}),
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
                Json(payload),
            ),
        )


def _update_game_from_live_payload(
    connection: Any,
    *,
    game_id: str,
    payload: dict[str, Any],
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE nba.nba_games
            SET game_status = COALESCE(%s, game_status),
                game_status_text = COALESCE(%s, game_status_text),
                period = COALESCE(%s, period),
                game_clock = COALESCE(%s, game_clock),
                home_score = COALESCE(%s, home_score),
                away_score = COALESCE(%s, away_score),
                updated_at = %s
            WHERE game_id = %s;
            """,
            (
                _safe_int(payload.get("game_status")),
                str(payload.get("game_status_text") or "") or None,
                _safe_int(payload.get("period")),
                str(payload.get("game_clock") or "") or None,
                _safe_int(payload.get("home_score")),
                _safe_int(payload.get("visitor_score")),
                datetime.now(timezone.utc),
                game_id,
            ),
        )


def _upsert_schedule_games(
    *,
    repo: JanusUpsertRepository,
    schedule_df: pd.DataFrame,
    start_date: date,
    end_date: date,
    season: str,
) -> tuple[int, int]:
    teams_upserted = 0
    games_upserted = 0
    work = schedule_df.copy()
    if work.empty:
        return teams_upserted, games_upserted

    dates = pd.to_datetime(work["game_date"], errors="coerce").dt.date
    work = work[(dates >= start_date) & (dates <= end_date)].reset_index(drop=True)
    if work.empty:
        return teams_upserted, games_upserted

    for _, row in work.iterrows():
        home_team_id = _safe_positive_int(row.get("home_team_id"))
        away_team_id = _safe_positive_int(row.get("away_team_id"))
        home_slug = str(row.get("home_team_slug") or "").upper()
        away_slug = str(row.get("away_team_slug") or "").upper()
        if home_team_id:
            repo.upsert_nba_team(
                team_id=home_team_id,
                team_slug=home_slug or f"T{home_team_id}",
                team_name=str(row.get("home_team_name") or home_slug or home_team_id),
                team_city=str(row.get("home_team_city") or ""),
            )
            teams_upserted += 1
        if away_team_id:
            repo.upsert_nba_team(
                team_id=away_team_id,
                team_slug=away_slug or f"T{away_team_id}",
                team_name=str(row.get("away_team_name") or away_slug or away_team_id),
                team_city=str(row.get("away_team_city") or ""),
            )
            teams_upserted += 1

        game_date = _safe_date(row.get("game_date"))
        game_start_time = _safe_dt(row.get("game_start_time"))
        repo.upsert_nba_game(
            game_id=str(row.get("game_id")),
            season=season,
            season_phase=_season_phase_from_game_id(str(row.get("game_id"))),
            game_date=game_date,
            game_start_time=game_start_time,
            game_status=_safe_int(row.get("game_status")),
            game_status_text=str(row.get("game_status_text") or ""),
            period=_safe_int(row.get("period")),
            game_clock=str(row.get("game_clock") or ""),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_team_slug=home_slug or None,
            away_team_slug=away_slug or None,
            home_score=_safe_int(row.get("home_score")),
            away_score=_safe_int(row.get("away_score")),
            updated_at=_safe_dt(row.get("updated_at")) or datetime.now(timezone.utc),
        )
        games_upserted += 1
    return teams_upserted, games_upserted


def _upsert_scoreboard_games(
    *,
    repo: JanusUpsertRepository,
    scoreboard_games: list[dict[str, Any]],
    season: str,
) -> tuple[int, int, list[str], list[str]]:
    teams_upserted = 0
    games_upserted = 0
    ongoing_game_ids: list[str] = []
    scoreboard_game_ids: list[str] = []
    for game in scoreboard_games:
        game_id = str(game.get("gameId") or "").strip()
        if not game_id:
            continue
        scoreboard_game_ids.append(game_id)
        status = _safe_int(game.get("gameStatus"))
        if status == 2:
            ongoing_game_ids.append(game_id)

        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}
        home_team_id = _safe_positive_int(home.get("teamId"))
        away_team_id = _safe_positive_int(away.get("teamId"))
        home_slug = str(home.get("teamTricode") or "").upper()
        away_slug = str(away.get("teamTricode") or "").upper()

        if home_team_id:
            repo.upsert_nba_team(
                team_id=home_team_id,
                team_slug=home_slug or f"T{home_team_id}",
                team_name=str(home.get("teamName") or home_slug or home_team_id),
                team_city=str(home.get("teamCity") or ""),
            )
            teams_upserted += 1
        if away_team_id:
            repo.upsert_nba_team(
                team_id=away_team_id,
                team_slug=away_slug or f"T{away_team_id}",
                team_name=str(away.get("teamName") or away_slug or away_team_id),
                team_city=str(away.get("teamCity") or ""),
            )
            teams_upserted += 1

        game_date = _safe_date(game.get("gameEt"))
        game_start_time = _safe_dt(game.get("gameTimeUTC"))
        repo.upsert_nba_game(
            game_id=game_id,
            season=season,
            season_phase=_season_phase_from_game_id(game_id),
            game_date=game_date,
            game_start_time=game_start_time,
            game_status=status,
            game_status_text=str(game.get("gameStatusText") or ""),
            period=_safe_int(game.get("period")),
            game_clock=str(game.get("gameClock") or ""),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_team_slug=home_slug or None,
            away_team_slug=away_slug or None,
            home_score=_safe_int(home.get("score")),
            away_score=_safe_int(away.get("score")),
            updated_at=datetime.now(timezone.utc),
        )
        games_upserted += 1
    return teams_upserted, games_upserted, ongoing_game_ids, scoreboard_game_ids


def _detect_missing_today(
    *,
    schedule_df: pd.DataFrame,
    scoreboard_games: list[dict[str, Any]],
) -> list[str]:
    scoreboard_ids = {str(item.get("gameId")) for item in scoreboard_games if item.get("gameId")}
    if not scoreboard_ids:
        return []

    scoreboard_dates = {
        _safe_date(item.get("gameEt"))
        for item in scoreboard_games
        if _safe_date(item.get("gameEt")) is not None
    }
    schedule_ids: set[str] = set()
    if not schedule_df.empty:
        work = schedule_df.copy()
        dates = pd.to_datetime(work["game_date"], errors="coerce").dt.date
        mask = dates.isin(scoreboard_dates)
        for value in work[mask]["game_id"].tolist():
            schedule_ids.add(str(value))
    missing = sorted(scoreboard_ids - schedule_ids)
    return missing


def run_nba_metadata_sync(
    *,
    season: str = "2025-26",
    schedule_window_days: int = 2,
    include_play_by_play: bool = True,
    include_live_snapshots: bool = True,
) -> NbaSyncSummary:
    rows_read = 0
    rows_written = 0
    teams_upserted = 0
    games_upserted = 0
    live_snapshots_written = 0
    play_by_play_rows_written = 0

    schedule_df = fetch_season_schedule_df(season=season)
    scoreboard_games = _extract_scoreboard_games()
    rows_read += len(schedule_df) + len(scoreboard_games)
    missing_today = _detect_missing_today(schedule_df=schedule_df, scoreboard_games=scoreboard_games)

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=_uuid_for("provider", "nba_live_api"),
            code="nba_live_api",
            name="NBA Live API",
            category="sports_data",
            base_url="https://cdn.nba.com",
            auth_type="none",
        )
        module_id = repo.upsert_module(
            module_id=_uuid_for("module", "nba_metadata_sync"),
            code="nba_metadata_sync",
            name="NBA Metadata Sync",
            description="NBA schedule/live metadata ingestion to postgres",
            owner="janus",
        )
        sync_run_id = _insert_sync_run(connection, provider_id=provider_id, module_id=module_id)
        connection.commit()

        try:
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/nba/schedule/season",
                payload=schedule_df.to_dict(orient="records"),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/nba/scoreboard/today",
                payload=scoreboard_games,
            )

            today = datetime.now(timezone.utc).date()
            start_date = today - timedelta(days=max(schedule_window_days, 0))
            end_date = today + timedelta(days=max(schedule_window_days, 0))
            team_count, game_count = _upsert_schedule_games(
                repo=repo,
                schedule_df=schedule_df,
                start_date=start_date,
                end_date=end_date,
                season=season,
            )
            teams_upserted += team_count
            games_upserted += game_count
            rows_written += team_count + game_count

            team_count, game_count, ongoing_game_ids, scoreboard_game_ids = _upsert_scoreboard_games(
                repo=repo,
                scoreboard_games=scoreboard_games,
                season=season,
            )
            teams_upserted += team_count
            games_upserted += game_count
            rows_written += team_count + game_count

            missing_today_inserted = 0
            missing_set = set(missing_today)
            for game_id in scoreboard_game_ids:
                if game_id in missing_set:
                    missing_today_inserted += 1

            if include_live_snapshots:
                for game_id in ongoing_game_ids:
                    payload = fetch_live_scoreboard(game_id)
                    rows_read += 1
                    if not payload:
                        continue
                    inserted = repo.insert_nba_live_game_snapshot(
                        game_id=game_id,
                        captured_at=datetime.now(timezone.utc),
                        period=_safe_int(payload.get("period")),
                        clock=str(payload.get("game_clock") or ""),
                        home_score=_safe_int(payload.get("home_score")),
                        away_score=_safe_int(payload.get("visitor_score")),
                        payload_json=payload,
                        ignore_duplicates=True,
                    )
                    if inserted:
                        live_snapshots_written += 1
                        rows_written += 1

            if include_play_by_play:
                for game_id in ongoing_game_ids:
                    pbp_df = fetch_play_by_play_df(PlayByPlayRequest(game_id=game_id))
                    rows_read += len(pbp_df)
                    if pbp_df.empty:
                        continue
                    for _, row in pbp_df.iterrows():
                        inserted = repo.upsert_nba_play_by_play_event(
                            game_id=game_id,
                            event_index=int(row.get("event_index")),
                            action_id=str(row.get("action_id")) if row.get("action_id") is not None else None,
                            period=_safe_int(row.get("period")),
                            clock=str(row.get("clock") or ""),
                            description=str(row.get("description") or ""),
                            home_score=_safe_int(row.get("score_home")),
                            away_score=_safe_int(row.get("score_away")),
                            is_score_change=bool(row.get("is_score_change")),
                            payload_json=row.get("raw") if isinstance(row.get("raw"), dict) else None,
                        )
                        if inserted:
                            play_by_play_rows_written += 1
                            rows_written += 1

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=None,
            )
            connection.commit()
            return NbaSyncSummary(
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                teams_upserted=teams_upserted,
                games_upserted=games_upserted,
                missing_today_detected=len(missing_today),
                missing_today_inserted=missing_today_inserted,
                ongoing_games=len(ongoing_game_ids),
                live_snapshots_written=live_snapshots_written,
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
            return NbaSyncSummary(
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                teams_upserted=teams_upserted,
                games_upserted=games_upserted,
                missing_today_detected=len(missing_today),
                missing_today_inserted=0,
                ongoing_games=0,
                live_snapshots_written=live_snapshots_written,
                play_by_play_rows_written=play_by_play_rows_written,
                error_text=repr(exc),
            )


def run_nba_live_game_sync(
    *,
    game_id: str,
    include_live_snapshots: bool = True,
    include_play_by_play: bool = True,
) -> NbaLiveGameSyncSummary:
    rows_read = 0
    rows_written = 0
    live_snapshots_written = 0
    play_by_play_rows_written = 0

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=_uuid_for("provider", "nba_live_api"),
            code="nba_live_api",
            name="NBA Live API",
            category="sports_data",
            base_url="https://cdn.nba.com",
            auth_type="none",
        )
        module_id = repo.upsert_module(
            module_id=_uuid_for("module", "nba_live_game_sync"),
            code="nba_live_game_sync",
            name="NBA Live Game Sync",
            description="Game-scoped NBA live snapshot and play-by-play ingestion",
            owner="janus",
        )
        sync_run_id = _insert_sync_run(connection, provider_id=provider_id, module_id=module_id)
        connection.commit()

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM nba.nba_games WHERE game_id = %s;", (game_id,))
                if cursor.fetchone() is None:
                    raise ValueError(f"game_id not found in nba.nba_games: {game_id}")

            if include_live_snapshots:
                live_payload = fetch_live_scoreboard(game_id)
                rows_read += 1
                _insert_raw_payload(
                    connection,
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    endpoint=f"/nba/live/{game_id}/boxscore",
                    payload=live_payload,
                )
                if live_payload:
                    inserted = repo.insert_nba_live_game_snapshot(
                        game_id=game_id,
                        captured_at=datetime.now(timezone.utc),
                        period=_safe_int(live_payload.get("period")),
                        clock=str(live_payload.get("game_clock") or ""),
                        home_score=_safe_int(live_payload.get("home_score")),
                        away_score=_safe_int(live_payload.get("visitor_score")),
                        payload_json=live_payload,
                        ignore_duplicates=True,
                    )
                    if inserted:
                        live_snapshots_written += 1
                        rows_written += 1
                    _update_game_from_live_payload(connection, game_id=game_id, payload=live_payload)

            if include_play_by_play:
                pbp_df = fetch_play_by_play_df(PlayByPlayRequest(game_id=game_id))
                rows_read += len(pbp_df)
                _insert_raw_payload(
                    connection,
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    endpoint=f"/nba/live/{game_id}/play-by-play",
                    payload=pbp_df.to_dict(orient="records"),
                )
                if not pbp_df.empty:
                    for _, row in pbp_df.iterrows():
                        inserted = repo.upsert_nba_play_by_play_event(
                            game_id=game_id,
                            event_index=int(row.get("event_index")),
                            action_id=str(row.get("action_id")) if row.get("action_id") is not None else None,
                            period=_safe_int(row.get("period")),
                            clock=str(row.get("clock") or ""),
                            description=str(row.get("description") or ""),
                            home_score=_safe_int(row.get("score_home")),
                            away_score=_safe_int(row.get("score_away")),
                            is_score_change=bool(row.get("is_score_change")),
                            payload_json=row.get("raw") if isinstance(row.get("raw"), dict) else None,
                        )
                        if inserted:
                            play_by_play_rows_written += 1
                            rows_written += 1

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=None,
            )
            connection.commit()
            return NbaLiveGameSyncSummary(
                sync_run_id=sync_run_id,
                status="success",
                game_id=game_id,
                rows_read=rows_read,
                rows_written=rows_written,
                live_snapshots_written=live_snapshots_written,
                play_by_play_rows_written=play_by_play_rows_written,
                error_text=None,
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
            return NbaLiveGameSyncSummary(
                sync_run_id=sync_run_id,
                status="error",
                game_id=game_id,
                rows_read=rows_read,
                rows_written=rows_written,
                live_snapshots_written=live_snapshots_written,
                play_by_play_rows_written=play_by_play_rows_written,
                error_text=repr(exc),
            )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NBA postgres ingestion sync.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--schedule-window-days", type=int, default=2)
    parser.add_argument("--skip-live-snapshots", action="store_true")
    parser.add_argument("--skip-play-by-play", action="store_true")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    summary = run_nba_metadata_sync(
        season=args.season,
        schedule_window_days=args.schedule_window_days,
        include_live_snapshots=not args.skip_live_snapshots,
        include_play_by_play=not args.skip_play_by_play,
    )
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
    print(
        " | ".join(
            [
                f"teams_upserted={summary.teams_upserted}",
                f"games_upserted={summary.games_upserted}",
                f"missing_today_detected={summary.missing_today_detected}",
                f"missing_today_inserted={summary.missing_today_inserted}",
                f"ongoing_games={summary.ongoing_games}",
                f"live_snapshots_written={summary.live_snapshots_written}",
                f"play_by_play_rows_written={summary.play_by_play_rows_written}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
