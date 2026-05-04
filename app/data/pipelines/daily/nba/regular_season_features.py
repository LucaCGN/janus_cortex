from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.nba.live.play_by_play import (
    PlayByPlayRequest,
    compute_lead_change_summary,
    fetch_play_by_play_df,
)
from app.data.nodes.nba.schedule.season_schedule import fetch_season_schedule_df
from app.data.nodes.polymarket.gamma.nba.markets_moneyline_node import (
    NBAMoneylineMarketsRequest,
    fetch_nba_moneyline_df,
)
from app.data.nodes.polymarket.gamma.nba.odds_history_node import (
    NBAOddsHistoryRequest,
    fetch_clob_prices_history,
)


FEATURE_VERSION = "v0_8_1"
TEAM_CONTEXT_MODE = "full_game"
PRE_GAME_LOOKBACK_HOURS = 12
IN_GAME_WINDOW_HOURS = 6
_NAMESPACE = uuid.UUID("8e4eab1a-4124-49d6-8ecf-2c9e255f0d7b")


@dataclass
class OddsAuditRow:
    season: str
    game_id: str
    event_id: str | None
    market_id: str | None
    outcome_id: str | None
    coverage_scope: str
    coverage_status: str
    history_points: int | None
    fallback_points: int | None
    window_start: datetime | None
    window_end: datetime | None
    issue_code: str | None
    details_json: dict[str, Any]


@dataclass
class GameOddsSummary:
    event_id: str | None
    coverage_status: str
    covered_polymarket_game_flag: bool
    home_pre_game_price_min: float | None
    home_pre_game_price_max: float | None
    away_pre_game_price_min: float | None
    away_pre_game_price_max: float | None
    home_in_game_price_min: float | None
    home_in_game_price_max: float | None
    away_in_game_price_min: float | None
    away_in_game_price_max: float | None
    price_window_start: datetime | None
    price_window_end: datetime | None
    audit_rows: list[OddsAuditRow]
    source_summary_json: dict[str, Any]


@dataclass
class RegularSeasonFeatureMaterializationSummary:
    season: str
    games_considered: int
    feature_snapshots_written: int
    pbp_backfilled_games: int
    pbp_backfilled_rows: int
    covered_polymarket_games: int
    odds_audit_rows_written: int
    coverage_status_counts: dict[str, int]


@dataclass
class TeamFeatureRollupSummary:
    season: str
    rollups_written: int
    classified_inconsistent_winning_teams: int
    classified_resilient_underdogs: int
    classified_high_lead_change_profiles: int


@dataclass
class RegularSeasonRefreshSummary:
    sync_run_id: str | None
    status: str
    season: str
    rows_read: int
    rows_written: int
    metadata_games_upserted: int
    metadata_teams_upserted: int
    games_considered: int
    feature_snapshots_written: int
    pbp_backfilled_games: int
    pbp_backfilled_rows: int
    covered_polymarket_games: int
    odds_audit_rows_written: int
    rollups_written: int
    coverage_status_counts: dict[str, int]
    qa_report: dict[str, Any]
    error_text: str | None = None


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _query_df(connection: Any, query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_positive_int(value: Any) -> int | None:
    parsed = _safe_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value is None:
        return None
    raw = str(value).strip()
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


def _expected_slug_for_game(row: pd.Series | dict[str, Any]) -> str:
    away = str(row.get("away_team_slug") or "").strip().lower()
    home = str(row.get("home_team_slug") or "").strip().lower()
    game_date = _safe_date(row.get("game_date"))
    if game_date is None:
        game_date = date.today()
    return f"nba-{away}-{home}-{game_date.isoformat()}"


def _normalize_schedule_df(schedule_df: pd.DataFrame, season: str) -> pd.DataFrame:
    work = schedule_df.copy()
    if work.empty:
        return work
    work["season"] = season
    work["game_date"] = pd.to_datetime(work["game_date"], errors="coerce").dt.date
    work["game_start_time"] = pd.to_datetime(work["game_start_time"], errors="coerce", utc=True)
    work["expected_slug"] = work.apply(_expected_slug_for_game, axis=1)
    return work


def _upsert_season_schedule(
    connection: Any,
    *,
    season: str,
    schedule_df: pd.DataFrame,
) -> tuple[int, int]:
    repo = JanusUpsertRepository(connection)
    teams_upserted = 0
    games_upserted = 0
    if schedule_df.empty:
        return teams_upserted, games_upserted

    for _, row in schedule_df.iterrows():
        home_team_id = _safe_positive_int(row.get("home_team_id"))
        away_team_id = _safe_positive_int(row.get("away_team_id"))
        home_slug = str(row.get("home_team_slug") or "").upper().strip()
        away_slug = str(row.get("away_team_slug") or "").upper().strip()

        if home_team_id and home_slug:
            repo.upsert_nba_team(
                team_id=home_team_id,
                team_slug=home_slug,
                team_name=str(row.get("home_team_name") or home_slug),
                team_city=str(row.get("home_team_city") or ""),
            )
            teams_upserted += 1
        if away_team_id and away_slug:
            repo.upsert_nba_team(
                team_id=away_team_id,
                team_slug=away_slug,
                team_name=str(row.get("away_team_name") or away_slug),
                team_city=str(row.get("away_team_city") or ""),
            )
            teams_upserted += 1

        repo.upsert_nba_game(
            game_id=str(row.get("game_id")),
            season=season,
            game_date=_safe_date(row.get("game_date")),
            game_start_time=_safe_dt(row.get("game_start_time")),
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
            updated_at=datetime.now(timezone.utc),
        )
        games_upserted += 1

    return teams_upserted, games_upserted


def _fetch_games_df(
    connection: Any,
    *,
    season: str,
    game_ids: list[str] | None = None,
    only_finished: bool = False,
    max_games: int | None = None,
) -> pd.DataFrame:
    query = """
        SELECT
            g.game_id,
            g.season,
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
            g.updated_at
        FROM nba.nba_games g
        WHERE g.season = %s
    """
    params: list[Any] = [season]
    if game_ids:
        query += " AND g.game_id = ANY(%s)"
        params.append(game_ids)
    if only_finished:
        query += " AND g.game_status = 3"
    query += """
        ORDER BY
            CASE WHEN g.game_status = 2 THEN 0
                 WHEN g.game_status = 1 THEN 1
                 WHEN g.game_status = 3 THEN 2
                 ELSE 3 END,
            g.game_date DESC NULLS LAST,
            g.game_start_time DESC NULLS LAST
    """
    if max_games is not None and max_games > 0:
        query += " LIMIT %s"
        params.append(max_games)
    games_df = _query_df(connection, query, tuple(params))
    if games_df.empty:
        return games_df
    games_df["game_date"] = pd.to_datetime(games_df["game_date"], errors="coerce").dt.date
    games_df["game_start_time"] = pd.to_datetime(games_df["game_start_time"], errors="coerce", utc=True)
    games_df["expected_slug"] = games_df.apply(_expected_slug_for_game, axis=1)
    return games_df


def _fetch_db_play_by_play_df(connection: Any, game_id: str) -> pd.DataFrame:
    return _query_df(
        connection,
        """
        SELECT
            game_id,
            event_index,
            action_id,
            period,
            clock,
            description,
            home_score AS score_home,
            away_score AS score_away,
            is_score_change,
            payload_json AS raw
        FROM nba.nba_play_by_play
        WHERE game_id = %s
        ORDER BY event_index ASC;
        """,
        (game_id,),
    )


def _load_catalog_outcome_lookup(connection: Any) -> tuple[dict[str, str], dict[str, dict[str, str | None]]]:
    rows = _query_df(
        connection,
        """
        SELECT
            e.canonical_slug,
            e.event_id,
            m.market_id,
            mer.external_market_id,
            o.outcome_id,
            o.token_id,
            lower(o.outcome_label) AS outcome_label_lower
        FROM catalog.events e
        LEFT JOIN catalog.markets m ON m.event_id = e.event_id
        LEFT JOIN catalog.market_external_refs mer ON mer.market_id = m.market_id
        LEFT JOIN catalog.outcomes o ON o.market_id = m.market_id;
        """,
    )
    event_ids_by_slug: dict[str, str] = {}
    outcome_lookup: dict[str, dict[str, str | None]] = {}
    if rows.empty:
        return event_ids_by_slug, outcome_lookup

    for _, row in rows.iterrows():
        canonical_slug = str(row.get("canonical_slug") or "").strip()
        event_id = str(row.get("event_id") or "").strip()
        if canonical_slug and event_id:
            event_ids_by_slug[canonical_slug] = event_id

        token_id = str(row.get("token_id") or "").strip()
        external_market_id = str(row.get("external_market_id") or "").strip()
        outcome_label_lower = str(row.get("outcome_label_lower") or "").strip()
        if canonical_slug or external_market_id or token_id or outcome_label_lower:
            outcome_lookup["|".join([canonical_slug, external_market_id, token_id, outcome_label_lower])] = {
                "event_id": event_id or None,
                "market_id": str(row.get("market_id") or "").strip() or None,
                "outcome_id": str(row.get("outcome_id") or "").strip() or None,
            }
    return event_ids_by_slug, outcome_lookup


def _resolve_catalog_refs(
    *,
    expected_slug: str,
    moneyline_row: pd.Series,
    event_ids_by_slug: dict[str, str],
    outcome_lookup: dict[str, dict[str, str | None]],
) -> dict[str, str | None]:
    row_event_id = str(moneyline_row.get("event_id") or "").strip() or None
    row_market_id = str(moneyline_row.get("market_uuid") or "").strip() or None
    row_outcome_id = str(moneyline_row.get("outcome_id") or "").strip() or None
    event_id = event_ids_by_slug.get(expected_slug) or row_event_id
    external_market_id = str(moneyline_row.get("market_id") or "").strip()
    token_id = str(moneyline_row.get("token_id") or "").strip()
    outcome_label_lower = str(moneyline_row.get("outcome") or "").strip().lower()
    refs = dict(outcome_lookup.get("|".join([expected_slug, external_market_id, token_id, outcome_label_lower])) or {})
    if not refs:
        refs = {"event_id": event_id, "market_id": row_market_id, "outcome_id": row_outcome_id}
    elif refs.get("event_id") is None:
        refs["event_id"] = event_id
    if refs.get("market_id") is None and row_market_id:
        refs["market_id"] = row_market_id
    if refs.get("outcome_id") is None and row_outcome_id:
        refs["outcome_id"] = row_outcome_id
    return refs


def _parse_matchup_question(question: Any) -> tuple[str | None, str | None]:
    raw = str(question or "").strip()
    if not raw or ":" in raw or " vs. " not in raw:
        return None, None
    away_name, home_name = [part.strip() for part in raw.split(" vs. ", maxsplit=1)]
    if not away_name or not home_name:
        return None, None
    return away_name, home_name


def _catalog_moneyline_row_to_record(row: pd.Series) -> dict[str, Any]:
    event_slug = str(row.get("event_slug") or "").strip().lower()
    outcome_label = str(row.get("outcome") or "").strip()
    away_name, home_name = _parse_matchup_question(row.get("question"))
    slug_parts = event_slug.split("-")
    away_abbr = slug_parts[1].upper() if len(slug_parts) >= 5 else None
    home_abbr = slug_parts[2].upper() if len(slug_parts) >= 5 else None

    team_abbr: str | None = None
    if away_name and outcome_label.lower() == away_name.lower():
        team_abbr = away_abbr
    elif home_name and outcome_label.lower() == home_name.lower():
        team_abbr = home_abbr
    elif len(outcome_label) <= 4:
        team_abbr = outcome_label.upper()

    return {
        "event_slug": event_slug,
        "market_id": str(row.get("external_market_id") or row.get("market_id") or "").strip() or None,
        "outcome": outcome_label,
        "token_id": str(row.get("token_id") or "").strip() or None,
        "team_abbr": team_abbr,
        "last_price": _safe_float(row.get("last_price")),
        "ingestion_source": "catalog_fallback",
    }


def _collect_catalog_moneyline_df(connection: Any, *, expected_slugs: list[str]) -> pd.DataFrame:
    normalized_slugs = sorted({str(slug or "").strip().lower() for slug in expected_slugs if str(slug or "").strip()})
    if not normalized_slugs:
        return pd.DataFrame()

    rows = _query_df(
        connection,
        """
        WITH latest_market_snapshots AS (
            SELECT DISTINCT ON (market_id)
                market_id,
                captured_at,
                last_price
            FROM catalog.market_state_snapshots
            ORDER BY market_id, captured_at DESC
        )
        SELECT
            e.canonical_slug AS event_slug,
            m.market_id,
            mer.external_market_id,
            m.question,
            m.market_type,
            o.outcome_label AS outcome,
            o.token_id,
            s.last_price
        FROM catalog.events e
        JOIN catalog.markets m ON m.event_id = e.event_id
        LEFT JOIN catalog.market_external_refs mer ON mer.market_id = m.market_id
        JOIN catalog.outcomes o ON o.market_id = m.market_id
        LEFT JOIN latest_market_snapshots s ON s.market_id = m.market_id
        WHERE e.canonical_slug = ANY(%s)
          AND lower(COALESCE(m.market_type, '')) = 'moneyline';
        """,
        (normalized_slugs,),
    )
    if rows.empty:
        return rows

    records = [_catalog_moneyline_row_to_record(row) for _, row in rows.iterrows()]
    out = pd.DataFrame(records)
    if out.empty:
        return out
    out["event_slug"] = out["event_slug"].astype(str).str.lower()
    return out.drop_duplicates(subset=["event_slug", "market_id", "outcome", "token_id"])


def _collect_linked_game_moneyline_df(connection: Any, *, games_df: pd.DataFrame) -> pd.DataFrame:
    if games_df.empty or "game_id" not in games_df.columns:
        return pd.DataFrame()

    game_ids = sorted({str(value).strip() for value in games_df["game_id"].dropna().tolist() if str(value).strip()})
    if not game_ids:
        return pd.DataFrame()

    latest_tick_query = """
        WITH latest_ticks AS (
            SELECT DISTINCT ON (outcome_id)
                outcome_id,
                ts,
                price
            FROM market_data.outcome_price_ticks
            ORDER BY outcome_id, ts DESC
        )
        SELECT
            g.game_id,
            g.game_date,
            g.away_team_slug,
            g.home_team_slug,
            away.team_name AS away_team_name,
            home.team_name AS home_team_name,
            e.event_id,
            e.canonical_slug AS actual_event_slug,
            m.market_id AS market_uuid,
            mer.external_market_id,
            m.question,
            m.market_type,
            o.outcome_id,
            o.outcome_label AS outcome,
            o.token_id,
            lt.price AS last_price
        FROM nba.nba_game_event_links l
        JOIN nba.nba_games g ON g.game_id = l.game_id
        JOIN catalog.events e ON e.event_id = l.event_id
        JOIN catalog.markets m ON m.event_id = e.event_id
        LEFT JOIN catalog.market_external_refs mer ON mer.market_id = m.market_id
        JOIN catalog.outcomes o ON o.market_id = m.market_id
        LEFT JOIN nba.nba_teams away ON away.team_id = g.away_team_id
        LEFT JOIN nba.nba_teams home ON home.team_id = g.home_team_id
        LEFT JOIN latest_ticks lt ON lt.outcome_id = o.outcome_id
        WHERE g.game_id = ANY(%s)
          AND lower(COALESCE(m.market_type, '')) = 'moneyline';
    """
    rows = _query_df(connection, latest_tick_query, (game_ids,))
    if rows.empty:
        return rows

    expected_by_game = {
        str(row.get("game_id")): _expected_slug_for_game(row)
        for _, row in games_df.iterrows()
    }
    records: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        game_id = str(row.get("game_id") or "").strip()
        expected_slug = expected_by_game.get(game_id, "")
        outcome = str(row.get("outcome") or "").strip()
        outcome_key = outcome.lower()
        away_slug = str(row.get("away_team_slug") or "").strip().upper()
        home_slug = str(row.get("home_team_slug") or "").strip().upper()
        away_name = str(row.get("away_team_name") or "").strip().lower()
        home_name = str(row.get("home_team_name") or "").strip().lower()
        team_abbr: str | None = None
        if outcome_key and outcome_key in {away_slug.lower(), away_name}:
            team_abbr = away_slug
        elif outcome_key and outcome_key in {home_slug.lower(), home_name}:
            team_abbr = home_slug
        records.append(
            {
                "event_slug": expected_slug,
                "actual_event_slug": str(row.get("actual_event_slug") or "").strip().lower() or None,
                "event_id": str(row.get("event_id") or "").strip() or None,
                "market_uuid": str(row.get("market_uuid") or "").strip() or None,
                "outcome_id": str(row.get("outcome_id") or "").strip() or None,
                "market_id": str(row.get("external_market_id") or row.get("market_uuid") or "").strip() or None,
                "outcome": outcome,
                "token_id": str(row.get("token_id") or "").strip() or None,
                "team_abbr": team_abbr,
                "last_price": _safe_float(row.get("last_price")),
                "ingestion_source": "linked_catalog_fallback",
            }
        )

    out = pd.DataFrame(records)
    if out.empty:
        return out
    out["event_slug"] = out["event_slug"].astype(str).str.lower()
    return out.drop_duplicates(subset=["event_slug", "market_id", "outcome", "token_id"])


def _collect_moneyline_season_df(
    connection: Any,
    *,
    start_dt: datetime,
    end_dt: datetime,
    window_days: int,
    page_size: int,
    max_pages: int,
    expected_slugs: list[str] | None = None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cursor = start_dt
    while cursor <= end_dt:
        window_end = min(cursor + timedelta(days=max(window_days, 1) - 1), end_dt)
        req = NBAMoneylineMarketsRequest(
            only_open=False,
            start_date_min=cursor,
            start_date_max=window_end,
            page_size=page_size,
            max_pages=max_pages,
            use_events_fallback=True,
        )
        frame = fetch_nba_moneyline_df(req=req)
        if not frame.empty:
            frames.append(frame)
        cursor = window_end + timedelta(days=1)

    catalog_fallback_df = _collect_catalog_moneyline_df(connection, expected_slugs=expected_slugs or [])
    if not catalog_fallback_df.empty:
        frames.append(catalog_fallback_df)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out["event_slug"] = out["event_slug"].astype(str).str.lower()
    return out.drop_duplicates(subset=["event_slug", "market_id", "outcome", "token_id"])


def _price_range(points: list[float]) -> tuple[float | None, float | None]:
    if not points:
        return None, None
    return min(points), max(points)


def _fallback_game_start(row: pd.Series) -> datetime:
    game_start = _safe_dt(row.get("game_start_time"))
    if game_start is not None:
        return game_start
    game_date = _safe_date(row.get("game_date"))
    if game_date is not None:
        return datetime.combine(game_date, time(0, 0), tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _best_coverage_status(statuses: list[str]) -> str:
    ranking = {
        "covered_pre_and_ingame": 5,
        "covered_partial": 4,
        "snapshot_only": 3,
        "pregame_only": 2,
        "no_history": 1,
        "missing_token": 0,
        "no_matching_event": -1,
    }
    if not statuses:
        return "no_matching_event"
    return max(statuses, key=lambda item: ranking.get(item, -99))


def _summarize_game_odds(
    *,
    season: str,
    game_row: pd.Series,
    moneyline_df: pd.DataFrame,
    event_ids_by_slug: dict[str, str],
    outcome_lookup: dict[str, dict[str, str | None]],
) -> GameOddsSummary:
    expected_slug = str(game_row.get("expected_slug") or "")
    game_moneyline = moneyline_df[moneyline_df["event_slug"] == expected_slug].copy() if not moneyline_df.empty else pd.DataFrame()
    game_start = _fallback_game_start(game_row)
    window_start = game_start - timedelta(hours=PRE_GAME_LOOKBACK_HOURS)
    window_end = game_start + timedelta(hours=IN_GAME_WINDOW_HOURS)

    if game_moneyline.empty:
        event_id = event_ids_by_slug.get(expected_slug)
        audit_row = OddsAuditRow(
            season=season,
            game_id=str(game_row["game_id"]),
            event_id=event_id,
            market_id=None,
            outcome_id=None,
            coverage_scope="game",
            coverage_status="no_matching_event",
            history_points=0,
            fallback_points=0,
            window_start=window_start,
            window_end=window_end,
            issue_code="missing_moneyline_mapping",
            details_json={"expected_slug": expected_slug},
        )
        return GameOddsSummary(
            event_id=event_id,
            coverage_status="no_matching_event",
            covered_polymarket_game_flag=False,
            home_pre_game_price_min=None,
            home_pre_game_price_max=None,
            away_pre_game_price_min=None,
            away_pre_game_price_max=None,
            home_in_game_price_min=None,
            home_in_game_price_max=None,
            away_in_game_price_min=None,
            away_in_game_price_max=None,
            price_window_start=window_start,
            price_window_end=window_end,
            audit_rows=[audit_row],
            source_summary_json={
                "expected_slug": expected_slug,
                "matched_markets": 0,
                "matched_outcomes": 0,
            },
        )

    home_slug = str(game_row.get("home_team_slug") or "").upper().strip()
    away_slug = str(game_row.get("away_team_slug") or "").upper().strip()
    per_side: dict[str, dict[str, float | None]] = {
        "home": {"pre_min": None, "pre_max": None, "in_min": None, "in_max": None},
        "away": {"pre_min": None, "pre_max": None, "in_min": None, "in_max": None},
    }
    audit_rows: list[OddsAuditRow] = []
    statuses: list[str] = []
    matched_event_id: str | None = None

    for _, market_row in game_moneyline.iterrows():
        refs = _resolve_catalog_refs(
            expected_slug=expected_slug,
            moneyline_row=market_row,
            event_ids_by_slug=event_ids_by_slug,
            outcome_lookup=outcome_lookup,
        )
        matched_event_id = matched_event_id or refs.get("event_id")
        token_id = str(market_row.get("token_id") or "").strip()
        side_slug = str(market_row.get("team_abbr") or market_row.get("outcome") or "").upper().strip()

        details_json: dict[str, Any] = {
            "expected_slug": expected_slug,
            "event_slug": str(market_row.get("event_slug") or ""),
            "outcome": str(market_row.get("outcome") or ""),
            "team_abbr": str(market_row.get("team_abbr") or ""),
            "token_id": token_id or None,
        }

        pre_prices: list[float] = []
        in_prices: list[float] = []
        fallback_points = 0
        history_points = 0
        issue_code: str | None = None
        if not token_id:
            coverage_status = "missing_token"
            issue_code = "missing_token"
        else:
            req = NBAOddsHistoryRequest(
                start_date_min=window_start,
                start_date_max=window_end,
                interval="1m",
                fidelity=10,
                allow_snapshot_fallback=False,
                retries=1,
                request_timeout_sec=10.0,
            )
            points = fetch_clob_prices_history(token_id=token_id, req=req)
            history_points = len(points)
            pre_prices = [float(item["price"]) for item in points if item["ts"] < game_start]
            in_prices = [float(item["price"]) for item in points if game_start <= item["ts"] <= window_end]
            fallback_price = _safe_float(market_row.get("last_price"))

            if pre_prices and in_prices:
                coverage_status = "covered_pre_and_ingame"
            elif pre_prices:
                coverage_status = "pregame_only"
                issue_code = "missing_ingame_history"
            elif history_points > 0:
                coverage_status = "covered_partial"
                issue_code = "partial_window_history"
            elif fallback_price is not None:
                coverage_status = "snapshot_only"
                fallback_points = 1
            else:
                coverage_status = "no_history"
                issue_code = "empty_history_window"

            if coverage_status == "snapshot_only" and fallback_price is not None:
                if side_slug == home_slug:
                    per_side["home"]["pre_min"] = fallback_price
                    per_side["home"]["pre_max"] = fallback_price
                if side_slug == away_slug:
                    per_side["away"]["pre_min"] = fallback_price
                    per_side["away"]["pre_max"] = fallback_price

        pre_min, pre_max = _price_range(pre_prices)
        in_min, in_max = _price_range(in_prices)
        if side_slug == home_slug:
            per_side["home"].update({"pre_min": pre_min, "pre_max": pre_max, "in_min": in_min, "in_max": in_max})
        elif side_slug == away_slug:
            per_side["away"].update({"pre_min": pre_min, "pre_max": pre_max, "in_min": in_min, "in_max": in_max})

        audit_rows.append(
            OddsAuditRow(
                season=season,
                game_id=str(game_row["game_id"]),
                event_id=refs.get("event_id"),
                market_id=refs.get("market_id"),
                outcome_id=refs.get("outcome_id"),
                coverage_scope="outcome_window",
                coverage_status=coverage_status,
                history_points=history_points,
                fallback_points=fallback_points,
                window_start=window_start,
                window_end=window_end,
                issue_code=issue_code,
                details_json=details_json,
            )
        )
        statuses.append(coverage_status)

    coverage_status = _best_coverage_status(statuses)
    return GameOddsSummary(
        event_id=matched_event_id,
        coverage_status=coverage_status,
        covered_polymarket_game_flag=coverage_status != "no_matching_event",
        home_pre_game_price_min=_safe_float(per_side["home"]["pre_min"]),
        home_pre_game_price_max=_safe_float(per_side["home"]["pre_max"]),
        away_pre_game_price_min=_safe_float(per_side["away"]["pre_min"]),
        away_pre_game_price_max=_safe_float(per_side["away"]["pre_max"]),
        home_in_game_price_min=_safe_float(per_side["home"]["in_min"]),
        home_in_game_price_max=_safe_float(per_side["home"]["in_max"]),
        away_in_game_price_min=_safe_float(per_side["away"]["in_min"]),
        away_in_game_price_max=_safe_float(per_side["away"]["in_max"]),
        price_window_start=window_start,
        price_window_end=window_end,
        audit_rows=audit_rows,
        source_summary_json={
            "expected_slug": expected_slug,
            "matched_markets": int(game_moneyline["market_id"].nunique()),
            "matched_outcomes": int(len(game_moneyline)),
            "moneyline_source_variants": sorted({str(item) for item in game_moneyline["ingestion_source"].dropna().tolist()}),
        },
    )


def _persist_pbp_if_needed(
    connection: Any,
    *,
    game_id: str,
    repo: JanusUpsertRepository,
) -> tuple[pd.DataFrame, int]:
    pbp_df = _fetch_db_play_by_play_df(connection, game_id)
    if not pbp_df.empty:
        return pbp_df, 0

    fetched_df = fetch_play_by_play_df(PlayByPlayRequest(game_id=game_id))
    written = 0
    if fetched_df.empty:
        return fetched_df, written

    for _, row in fetched_df.iterrows():
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
            written += 1
    return fetched_df, written


def _build_game_feature_payload(
    *,
    season: str,
    game_row: pd.Series,
    pbp_df: pd.DataFrame,
    odds_summary: GameOddsSummary,
) -> dict[str, Any]:
    if pbp_df.empty:
        lead_summary = {
            "lead_changes": 0,
            "home_largest_lead": 0,
            "away_largest_lead": 0,
            "away_lead_segments": 0,
            "home_lead_segments": 0,
        }
    else:
        lead_summary = compute_lead_change_summary(pbp_df)

    home_score = _safe_int(game_row.get("home_score")) or 0
    away_score = _safe_int(game_row.get("away_score")) or 0
    home_won = home_score > away_score
    away_won = away_score > home_score

    return {
        "game_id": str(game_row["game_id"]),
        "computed_at": datetime.now(timezone.utc),
        "feature_version": FEATURE_VERSION,
        "season": season,
        "team_context_mode": TEAM_CONTEXT_MODE,
        "event_id": odds_summary.event_id,
        "pbp_event_count": int(len(pbp_df)),
        "lead_changes": int(lead_summary.get("lead_changes") or 0),
        "home_largest_lead": int(lead_summary.get("home_largest_lead") or 0),
        "away_largest_lead": int(lead_summary.get("away_largest_lead") or 0),
        "home_losing_segments": int(lead_summary.get("away_lead_segments") or 0),
        "away_losing_segments": int(lead_summary.get("home_lead_segments") or 0),
        "home_led_and_lost": bool((lead_summary.get("home_largest_lead") or 0) > 0 and away_won) if home_won != away_won else None,
        "away_led_and_lost": bool((lead_summary.get("away_largest_lead") or 0) > 0 and home_won) if home_won != away_won else None,
        "covered_polymarket_game_flag": odds_summary.covered_polymarket_game_flag,
        "home_pre_game_price_min": odds_summary.home_pre_game_price_min,
        "home_pre_game_price_max": odds_summary.home_pre_game_price_max,
        "away_pre_game_price_min": odds_summary.away_pre_game_price_min,
        "away_pre_game_price_max": odds_summary.away_pre_game_price_max,
        "home_in_game_price_min": odds_summary.home_in_game_price_min,
        "home_in_game_price_max": odds_summary.home_in_game_price_max,
        "away_in_game_price_min": odds_summary.away_in_game_price_min,
        "away_in_game_price_max": odds_summary.away_in_game_price_max,
        "price_window_start": odds_summary.price_window_start,
        "price_window_end": odds_summary.price_window_end,
        "coverage_status": odds_summary.coverage_status,
        "source_summary_json": {
            **odds_summary.source_summary_json,
            "expected_slug": str(game_row.get("expected_slug") or ""),
            "game_status": _safe_int(game_row.get("game_status")),
            "home_team_slug": str(game_row.get("home_team_slug") or ""),
            "away_team_slug": str(game_row.get("away_team_slug") or ""),
        },
    }


def materialize_regular_season_features(
    connection: Any,
    *,
    season: str,
    games_df: pd.DataFrame,
    include_odds_fetch: bool = True,
    moneyline_window_days: int = 14,
    moneyline_page_size: int = 100,
    moneyline_max_pages: int = 30,
) -> RegularSeasonFeatureMaterializationSummary:
    if games_df.empty:
        return RegularSeasonFeatureMaterializationSummary(
            season=season,
            games_considered=0,
            feature_snapshots_written=0,
            pbp_backfilled_games=0,
            pbp_backfilled_rows=0,
            covered_polymarket_games=0,
            odds_audit_rows_written=0,
            coverage_status_counts={},
        )

    repo = JanusUpsertRepository(connection)
    event_ids_by_slug, outcome_lookup = _load_catalog_outcome_lookup(connection)

    moneyline_df = pd.DataFrame()
    if include_odds_fetch:
        game_start_min = min(_fallback_game_start(row) for _, row in games_df.iterrows()) - timedelta(days=1)
        game_start_max = max(_fallback_game_start(row) for _, row in games_df.iterrows()) + timedelta(days=1)
        moneyline_df = _collect_moneyline_season_df(
            connection,
            start_dt=game_start_min,
            end_dt=game_start_max,
            window_days=moneyline_window_days,
            page_size=moneyline_page_size,
            max_pages=moneyline_max_pages,
            expected_slugs=games_df["expected_slug"].dropna().astype(str).tolist(),
        )
    linked_moneyline_df = _collect_linked_game_moneyline_df(connection, games_df=games_df)
    if not linked_moneyline_df.empty:
        moneyline_df = (
            linked_moneyline_df
            if moneyline_df.empty
            else pd.concat([moneyline_df, linked_moneyline_df], ignore_index=True, sort=False)
        )
        moneyline_df["event_slug"] = moneyline_df["event_slug"].astype(str).str.lower()
        moneyline_df = moneyline_df.drop_duplicates(subset=["event_slug", "market_id", "outcome", "token_id"])

    feature_snapshots_written = 0
    pbp_backfilled_games = 0
    pbp_backfilled_rows = 0
    covered_polymarket_games = 0
    odds_audit_rows_written = 0
    coverage_status_counts: dict[str, int] = {}

    for _, game_row in games_df.iterrows():
        game_id = str(game_row["game_id"])
        pbp_df, written = _persist_pbp_if_needed(connection, game_id=game_id, repo=repo)
        if written > 0:
            pbp_backfilled_games += 1
            pbp_backfilled_rows += written

        odds_summary = _summarize_game_odds(
            season=season,
            game_row=game_row,
            moneyline_df=moneyline_df,
            event_ids_by_slug=event_ids_by_slug,
            outcome_lookup=outcome_lookup,
        )

        _ = repo.delete_nba_odds_coverage_audits_for_game(season=season, game_id=game_id)
        for audit_row in odds_summary.audit_rows:
            inserted = repo.insert_nba_odds_coverage_audit(
                odds_coverage_audit_id=str(uuid.uuid4()),
                season=audit_row.season,
                game_id=audit_row.game_id,
                event_id=audit_row.event_id,
                market_id=audit_row.market_id,
                outcome_id=audit_row.outcome_id,
                audited_at=datetime.now(timezone.utc),
                coverage_scope=audit_row.coverage_scope,
                coverage_status=audit_row.coverage_status,
                history_points=audit_row.history_points,
                fallback_points=audit_row.fallback_points,
                window_start=audit_row.window_start,
                window_end=audit_row.window_end,
                issue_code=audit_row.issue_code,
                details_json=audit_row.details_json,
            )
            if inserted:
                odds_audit_rows_written += 1

        inserted_feature = repo.upsert_nba_game_feature_snapshot(**_build_game_feature_payload(
            season=season,
            game_row=game_row,
            pbp_df=pbp_df,
            odds_summary=odds_summary,
        ))
        if inserted_feature:
            feature_snapshots_written += 1

        coverage_status_counts[odds_summary.coverage_status] = coverage_status_counts.get(odds_summary.coverage_status, 0) + 1
        if odds_summary.covered_polymarket_game_flag:
            covered_polymarket_games += 1

    return RegularSeasonFeatureMaterializationSummary(
        season=season,
        games_considered=int(len(games_df)),
        feature_snapshots_written=feature_snapshots_written,
        pbp_backfilled_games=pbp_backfilled_games,
        pbp_backfilled_rows=pbp_backfilled_rows,
        covered_polymarket_games=covered_polymarket_games,
        odds_audit_rows_written=odds_audit_rows_written,
        coverage_status_counts=coverage_status_counts,
    )


def _estimate_pre_game_price(min_value: float | None, max_value: float | None) -> float | None:
    if min_value is not None and max_value is not None:
        return (min_value + max_value) / 2.0
    return min_value if min_value is not None else max_value


def _estimate_in_game_range(min_value: float | None, max_value: float | None) -> float | None:
    if min_value is None or max_value is None:
        return None
    return max_value - min_value


def materialize_team_feature_rollups(
    connection: Any,
    *,
    season: str,
) -> TeamFeatureRollupSummary:
    latest_df = _query_df(
        connection,
        """
        SELECT DISTINCT ON (f.game_id)
            f.game_id,
            f.computed_at,
            f.feature_version,
            f.season,
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
            f.coverage_status,
            g.home_team_id,
            g.away_team_id,
            g.home_team_slug,
            g.away_team_slug,
            g.home_score,
            g.away_score,
            g.game_status
        FROM nba.nba_game_feature_snapshots f
        JOIN nba.nba_games g ON g.game_id = f.game_id
        WHERE f.season = %s
        ORDER BY f.game_id, f.computed_at DESC;
        """,
        (season,),
    )
    if latest_df.empty:
        return TeamFeatureRollupSummary(
            season=season,
            rollups_written=0,
            classified_inconsistent_winning_teams=0,
            classified_resilient_underdogs=0,
            classified_high_lead_change_profiles=0,
        )

    repo = JanusUpsertRepository(connection)
    team_buckets: dict[int, list[dict[str, Any]]] = {}

    for _, row in latest_df.iterrows():
        home_team_id = _safe_int(row.get("home_team_id"))
        away_team_id = _safe_int(row.get("away_team_id"))
        if not home_team_id or not away_team_id:
            continue

        home_pre = _estimate_pre_game_price(_safe_float(row.get("home_pre_game_price_min")), _safe_float(row.get("home_pre_game_price_max")))
        away_pre = _estimate_pre_game_price(_safe_float(row.get("away_pre_game_price_min")), _safe_float(row.get("away_pre_game_price_max")))
        home_range = _estimate_in_game_range(_safe_float(row.get("home_in_game_price_min")), _safe_float(row.get("home_in_game_price_max")))
        away_range = _estimate_in_game_range(_safe_float(row.get("away_in_game_price_min")), _safe_float(row.get("away_in_game_price_max")))
        home_score = _safe_int(row.get("home_score")) or 0
        away_score = _safe_int(row.get("away_score")) or 0
        game_finished = (_safe_int(row.get("game_status")) or 0) == 3

        if home_pre is not None and away_pre is not None:
            home_role = "favorite" if home_pre >= away_pre else "underdog"
            away_role = "favorite" if away_pre > home_pre else "underdog"
        else:
            home_role = "unknown"
            away_role = "unknown"

        team_buckets.setdefault(home_team_id, []).append(
            {
                "won": game_finished and home_score > away_score,
                "lost": game_finished and away_score > home_score,
                "lead_changes": _safe_int(row.get("lead_changes")) or 0,
                "losing_segments": _safe_int(row.get("home_losing_segments")) or 0,
                "largest_lead_in_losses": (_safe_int(row.get("home_largest_lead")) or 0) if home_score < away_score else None,
                "loss_after_leading": bool(row.get("home_led_and_lost")),
                "covered": bool(row.get("covered_polymarket_game_flag")),
                "role": home_role,
                "in_game_range": home_range,
            }
        )
        team_buckets.setdefault(away_team_id, []).append(
            {
                "won": game_finished and away_score > home_score,
                "lost": game_finished and home_score > away_score,
                "lead_changes": _safe_int(row.get("lead_changes")) or 0,
                "losing_segments": _safe_int(row.get("away_losing_segments")) or 0,
                "largest_lead_in_losses": (_safe_int(row.get("away_largest_lead")) or 0) if away_score < home_score else None,
                "loss_after_leading": bool(row.get("away_led_and_lost")),
                "covered": bool(row.get("covered_polymarket_game_flag")),
                "role": away_role,
                "in_game_range": away_range,
            }
        )

    rollups_written = 0
    inconsistent_count = 0
    resilient_count = 0
    high_lead_count = 0
    now = datetime.now(timezone.utc)

    for team_id, rows in team_buckets.items():
        sample_games = len(rows)
        covered_games = sum(1 for item in rows if item["covered"])
        wins = sum(1 for item in rows if item["won"])
        losses = sum(1 for item in rows if item["lost"])
        avg_lead_changes = sum(float(item["lead_changes"]) for item in rows) / max(sample_games, 1)
        avg_losing_segments = sum(float(item["losing_segments"]) for item in rows) / max(sample_games, 1)
        loss_leads = [float(item["largest_lead_in_losses"]) for item in rows if item["largest_lead_in_losses"] is not None]
        avg_largest_lead_in_losses = sum(loss_leads) / max(len(loss_leads), 1) if loss_leads else 0.0
        losses_after_leading = sum(1 for item in rows if item["loss_after_leading"])

        underdog_rows = [item for item in rows if item["role"] == "underdog" and item["covered"]]
        favorite_rows = [item for item in rows if item["role"] == "favorite" and item["covered"]]
        underdog_ranges = [float(item["in_game_range"]) for item in underdog_rows if item["in_game_range"] is not None]
        favorite_ranges = [float(item["in_game_range"]) for item in favorite_rows if item["in_game_range"] is not None]
        avg_underdog_range = sum(underdog_ranges) / max(len(underdog_ranges), 1) if underdog_ranges else 0.0
        avg_favorite_range = sum(favorite_ranges) / max(len(favorite_ranges), 1) if favorite_ranges else 0.0

        tags: list[str] = []
        if wins > losses and avg_losing_segments >= 3.0 and losses_after_leading >= 5:
            tags.append("inconsistent_winning_team")
        if losses >= wins and avg_largest_lead_in_losses >= 5.0 and avg_underdog_range >= 0.08:
            tags.append("resilient_underdog")
        if avg_lead_changes >= 6.0:
            tags.append("high_lead_change_profile")

        inconsistent_count += 1 if "inconsistent_winning_team" in tags else 0
        resilient_count += 1 if "resilient_underdog" in tags else 0
        high_lead_count += 1 if "high_lead_change_profile" in tags else 0

        inserted = repo.upsert_nba_team_feature_rollup(
            team_id=team_id,
            season=season,
            computed_at=now,
            feature_version=FEATURE_VERSION,
            sample_games=sample_games,
            covered_games=covered_games,
            wins=wins,
            losses=losses,
            avg_lead_changes=avg_lead_changes,
            avg_losing_segments=avg_losing_segments,
            avg_largest_lead_in_losses=avg_largest_lead_in_losses,
            losses_after_leading=losses_after_leading,
            underdog_games_with_coverage=len(underdog_rows),
            favorite_games_with_coverage=len(favorite_rows),
            avg_underdog_in_game_range=avg_underdog_range,
            avg_favorite_in_game_range=avg_favorite_range,
            classification_tags_json={"tags": tags},
            notes_json={
                "coverage_pct": round((covered_games / max(sample_games, 1)) * 100.0, 2),
                "feature_version": FEATURE_VERSION,
            },
        )
        if inserted:
            rollups_written += 1

    return TeamFeatureRollupSummary(
        season=season,
        rollups_written=rollups_written,
        classified_inconsistent_winning_teams=inconsistent_count,
        classified_resilient_underdogs=resilient_count,
        classified_high_lead_change_profiles=high_lead_count,
    )


def build_regular_season_dataset_report(connection: Any, *, season: str) -> dict[str, Any]:
    report = {"season": season, "generated_at": datetime.now(timezone.utc).isoformat()}

    schedule_df = _query_df(
        connection,
        "SELECT game_id, game_status, game_date FROM nba.nba_games WHERE season = %s;",
        (season,),
    )
    feature_df = _query_df(
        connection,
        """
        SELECT DISTINCT ON (game_id)
            game_id,
            coverage_status,
            covered_polymarket_game_flag,
            pbp_event_count,
            computed_at
        FROM nba.nba_game_feature_snapshots
        WHERE season = %s
        ORDER BY game_id, computed_at DESC;
        """,
        (season,),
    )
    audit_df = _query_df(
        connection,
        "SELECT game_id, coverage_status, issue_code, history_points, fallback_points, audited_at FROM nba.nba_odds_coverage_audits WHERE season = %s;",
        (season,),
    )
    rollup_df = _query_df(
        connection,
        """
        SELECT DISTINCT ON (team_id)
            team_id,
            classification_tags_json
        FROM nba.nba_team_feature_rollups
        WHERE season = %s
        ORDER BY team_id, computed_at DESC;
        """,
        (season,),
    )

    games_total = int(len(schedule_df))
    finished_games = int((pd.to_numeric(schedule_df.get("game_status"), errors="coerce").fillna(0) == 3).sum()) if not schedule_df.empty else 0
    feature_games = int(len(feature_df))
    covered_games = int(feature_df["covered_polymarket_game_flag"].fillna(False).sum()) if not feature_df.empty else 0
    pbp_missing_games = int((pd.to_numeric(feature_df.get("pbp_event_count"), errors="coerce").fillna(0) == 0).sum()) if not feature_df.empty else 0

    coverage_counts = feature_df["coverage_status"].fillna("unknown").value_counts().to_dict() if not feature_df.empty else {}
    audit_counts = audit_df["coverage_status"].fillna("unknown").value_counts().to_dict() if not audit_df.empty else {}
    issue_counts = audit_df["issue_code"].fillna("none").value_counts().to_dict() if not audit_df.empty else {}

    tagged_teams = {"inconsistent_winning_team": 0, "resilient_underdog": 0, "high_lead_change_profile": 0}
    if not rollup_df.empty:
        for payload in rollup_df["classification_tags_json"].tolist():
            tags = payload.get("tags") if isinstance(payload, dict) else []
            for tag in tags or []:
                if tag in tagged_teams:
                    tagged_teams[tag] += 1

    report["schedule"] = {"games_total": games_total, "finished_games": finished_games}
    report["features"] = {
        "feature_games": feature_games,
        "covered_polymarket_games": covered_games,
        "coverage_status_counts": coverage_counts,
        "pbp_missing_games": pbp_missing_games,
    }
    report["odds_coverage"] = {
        "audit_rows": int(len(audit_df)),
        "audit_status_counts": audit_counts,
        "issue_counts": issue_counts,
    }
    report["teams"] = tagged_teams
    report["gap_samples"] = (
        feature_df[feature_df["coverage_status"].isin(["no_matching_event", "no_history", "missing_token"])].head(20).to_dict(orient="records")
        if not feature_df.empty
        else []
    )
    return report


def run_nba_regular_season_refresh(
    *,
    season: str = "2025-26",
    refresh_metadata: bool = True,
    game_ids: list[str] | None = None,
    max_games: int | None = None,
    only_finished: bool = False,
    include_odds_fetch: bool = True,
    build_rollups: bool = True,
    moneyline_window_days: int = 14,
    moneyline_page_size: int = 100,
    moneyline_max_pages: int = 30,
) -> RegularSeasonRefreshSummary:
    rows_read = 0
    rows_written = 0
    metadata_games_upserted = 0
    metadata_teams_upserted = 0

    with managed_connection() as connection:
        try:
            if refresh_metadata:
                schedule_df = _normalize_schedule_df(fetch_season_schedule_df(season=season), season)
                rows_read += int(len(schedule_df))
                teams_upserted, games_upserted = _upsert_season_schedule(connection, season=season, schedule_df=schedule_df)
                metadata_teams_upserted += teams_upserted
                metadata_games_upserted += games_upserted
                rows_written += teams_upserted + games_upserted

            games_df = _fetch_games_df(
                connection,
                season=season,
                game_ids=game_ids,
                only_finished=only_finished,
                max_games=max_games,
            )
            rows_read += int(len(games_df))

            feature_summary = materialize_regular_season_features(
                connection,
                season=season,
                games_df=games_df,
                include_odds_fetch=include_odds_fetch,
                moneyline_window_days=moneyline_window_days,
                moneyline_page_size=moneyline_page_size,
                moneyline_max_pages=moneyline_max_pages,
            )
            rows_written += feature_summary.feature_snapshots_written + feature_summary.odds_audit_rows_written + feature_summary.pbp_backfilled_rows

            rollup_summary = TeamFeatureRollupSummary(
                season=season,
                rollups_written=0,
                classified_inconsistent_winning_teams=0,
                classified_resilient_underdogs=0,
                classified_high_lead_change_profiles=0,
            )
            if build_rollups:
                rollup_summary = materialize_team_feature_rollups(connection, season=season)
                rows_written += rollup_summary.rollups_written

            qa_report = build_regular_season_dataset_report(connection, season=season)
            return RegularSeasonRefreshSummary(
                sync_run_id=None,
                status="success",
                season=season,
                rows_read=rows_read,
                rows_written=rows_written,
                metadata_games_upserted=metadata_games_upserted,
                metadata_teams_upserted=metadata_teams_upserted,
                games_considered=feature_summary.games_considered,
                feature_snapshots_written=feature_summary.feature_snapshots_written,
                pbp_backfilled_games=feature_summary.pbp_backfilled_games,
                pbp_backfilled_rows=feature_summary.pbp_backfilled_rows,
                covered_polymarket_games=feature_summary.covered_polymarket_games,
                odds_audit_rows_written=feature_summary.odds_audit_rows_written,
                rollups_written=rollup_summary.rollups_written,
                coverage_status_counts=feature_summary.coverage_status_counts,
                qa_report=qa_report,
                error_text=None,
            )
        except Exception as exc:  # noqa: BLE001
            return RegularSeasonRefreshSummary(
                sync_run_id=None,
                status="error",
                season=season,
                rows_read=rows_read,
                rows_written=rows_written,
                metadata_games_upserted=metadata_games_upserted,
                metadata_teams_upserted=metadata_teams_upserted,
                games_considered=0,
                feature_snapshots_written=0,
                pbp_backfilled_games=0,
                pbp_backfilled_rows=0,
                covered_polymarket_games=0,
                odds_audit_rows_written=0,
                rollups_written=0,
                coverage_status_counts={},
                qa_report={},
                error_text=repr(exc),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NBA regular-season feature refresh.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--skip-metadata-refresh", action="store_true")
    parser.add_argument("--only-finished", action="store_true")
    parser.add_argument("--skip-odds-fetch", action="store_true")
    parser.add_argument("--skip-rollups", action="store_true")
    parser.add_argument("--max-games", type=int, default=None)
    parser.add_argument("--game-id", dest="game_ids", action="append")
    parser.add_argument("--moneyline-window-days", type=int, default=14)
    parser.add_argument("--moneyline-page-size", type=int, default=100)
    parser.add_argument("--moneyline-max-pages", type=int, default=30)
    args = parser.parse_args()

    summary = run_nba_regular_season_refresh(
        season=args.season,
        refresh_metadata=not args.skip_metadata_refresh,
        game_ids=args.game_ids,
        max_games=args.max_games,
        only_finished=args.only_finished,
        include_odds_fetch=not args.skip_odds_fetch,
        build_rollups=not args.skip_rollups,
        moneyline_window_days=args.moneyline_window_days,
        moneyline_page_size=args.moneyline_page_size,
        moneyline_max_pages=args.moneyline_max_pages,
    )
    print(json.dumps(asdict(summary), indent=2, sort_keys=True, default=str))
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
