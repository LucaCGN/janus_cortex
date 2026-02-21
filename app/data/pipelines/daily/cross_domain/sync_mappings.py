from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository


_NAMESPACE = uuid.UUID("b07a7012-cd6a-45e8-b6b2-a0c7f0ac132a")


@dataclass
class MappingSyncSummary:
    sync_run_id: str | None
    status: str
    rows_read: int
    rows_written: int
    games_considered: int
    links_written: int
    events_scored: int
    error_text: str | None = None


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _insert_sync_run(connection: Any, *, provider_id: str, module_id: str, lookback_days: int, lookahead_days: int) -> str:
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
                "daily.cross_domain.sync_mappings",
                "scheduled",
                "running",
                datetime.now(timezone.utc),
                Json({"lookback_days": lookback_days, "lookahead_days": lookahead_days}),
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


def _build_expected_slug(*, away_slug: str | None, home_slug: str | None, game_date: Any) -> str | None:
    away = str(away_slug or "").strip().lower()
    home = str(home_slug or "").strip().lower()
    date_text = str(game_date or "").strip()
    if len(date_text) >= 10:
        date_text = date_text[:10]
    if not away or not home or len(date_text) != 10:
        return None
    return f"nba-{away}-{home}-{date_text}"


def _compute_event_score_fields(
    *,
    market_count: int,
    outcome_count: int,
    outcomes_with_token: int,
    has_recent_ticks: bool,
    last_tick_age_min: float | None,
    has_link: bool,
) -> tuple[float, float, float, bool, list[str]]:
    missing_fields: list[str] = []
    coverage = 0.0
    if market_count > 0:
        coverage += 35.0
    else:
        missing_fields.append("markets")
    if outcome_count >= 2:
        coverage += 25.0
    else:
        missing_fields.append("outcomes")
    if has_recent_ticks:
        coverage += 20.0
    else:
        missing_fields.append("recent_ticks")
    if has_link:
        coverage += 20.0
    else:
        missing_fields.append("nba_link")
    coverage = min(100.0, coverage)

    token_ratio = (outcomes_with_token / outcome_count) if outcome_count > 0 else 0.0
    quality = min(100.0, 50.0 + token_ratio * 50.0)

    if last_tick_age_min is None:
        latency = 0.0
    else:
        latency = max(0.0, 100.0 - min(100.0, last_tick_age_min / 5.0))

    eligible = coverage >= 70.0 and quality >= 60.0 and latency >= 20.0
    return coverage, quality, latency, eligible, missing_fields


def run_cross_domain_mapping_sync(*, lookback_days: int = 3, lookahead_days: int = 2) -> MappingSyncSummary:
    rows_read = 0
    rows_written = 0
    links_written = 0
    events_scored = 0
    games_considered = 0
    now = datetime.now(timezone.utc)

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=_uuid_for("provider", "internal_mapping"),
            code="internal_mapping",
            name="Internal Mapping",
            category="internal",
            base_url="internal://mapping",
            auth_type="none",
        )
        module_id = repo.upsert_module(
            module_id=_uuid_for("module", "cross_domain_mapping_sync"),
            code="cross_domain_mapping_sync",
            name="Cross-domain Mapping Sync",
            description="Sync nba games to catalog events and score information quality",
            owner="janus",
        )
        sync_run_id = _insert_sync_run(
            connection,
            provider_id=provider_id,
            module_id=module_id,
            lookback_days=lookback_days,
            lookahead_days=lookahead_days,
        )
        connection.commit()

        try:
            start_date = (now - timedelta(days=max(lookback_days, 0))).date()
            end_date = (now + timedelta(days=max(lookahead_days, 0))).date()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT game_id, game_date, away_team_slug, home_team_slug
                    FROM nba.nba_games
                    WHERE game_date BETWEEN %s AND %s;
                    """,
                    (start_date, end_date),
                )
                games = cursor.fetchall()

            games_considered = len(games)
            rows_read += games_considered

            linked_event_ids: set[str] = set()
            with connection.cursor() as cursor:
                for game_id, game_date, away_slug, home_slug in games:
                    expected_slug = _build_expected_slug(
                        away_slug=away_slug,
                        home_slug=home_slug,
                        game_date=game_date,
                    )
                    if not expected_slug:
                        continue
                    cursor.execute(
                        """
                        SELECT event_id
                        FROM catalog.events
                        WHERE canonical_slug = %s
                        LIMIT 1;
                        """,
                        (expected_slug,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                    event_id = str(row[0])
                    linked_event_ids.add(event_id)
                    link_id = repo.upsert_nba_game_event_link(
                        nba_game_event_link_id=_uuid_for("nba_game_event_link", str(game_id), event_id),
                        game_id=str(game_id),
                        event_id=event_id,
                        confidence=1.0,
                        linked_by="slug_exact",
                        linked_at=now,
                    )
                    if link_id:
                        links_written += 1
                        rows_written += 1

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT e.event_id, e.information_profile_id
                    FROM catalog.events e
                    WHERE e.event_id = ANY(%s::uuid[]);
                    """,
                    (list(linked_event_ids),),
                )
                events = cursor.fetchall()

            for event_id, information_profile_id in events:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT count(*)::int
                        FROM catalog.markets
                        WHERE event_id = %s;
                        """,
                        (str(event_id),),
                    )
                    market_count = int(cursor.fetchone()[0])
                    cursor.execute(
                        """
                        SELECT
                            count(*)::int,
                            count(*) FILTER (WHERE token_id IS NOT NULL AND token_id <> '')::int
                        FROM catalog.outcomes o
                        JOIN catalog.markets m ON m.market_id = o.market_id
                        WHERE m.event_id = %s;
                        """,
                        (str(event_id),),
                    )
                    outcome_count, outcomes_with_token = cursor.fetchone()
                    cursor.execute(
                        """
                        SELECT max(t.ts)
                        FROM market_data.outcome_price_ticks t
                        JOIN catalog.outcomes o ON o.outcome_id = t.outcome_id
                        JOIN catalog.markets m ON m.market_id = o.market_id
                        WHERE m.event_id = %s;
                        """,
                        (str(event_id),),
                    )
                    last_tick_ts = cursor.fetchone()[0]
                    cursor.execute(
                        """
                        SELECT count(*)::int
                        FROM nba.nba_game_event_links
                        WHERE event_id = %s;
                        """,
                        (str(event_id),),
                    )
                    has_link = int(cursor.fetchone()[0]) > 0

                last_tick_age_min = None
                has_recent_ticks = False
                if last_tick_ts is not None:
                    delta = now - last_tick_ts
                    last_tick_age_min = delta.total_seconds() / 60.0
                    has_recent_ticks = delta <= timedelta(hours=24)

                coverage, quality, latency, eligible, missing_fields = _compute_event_score_fields(
                    market_count=market_count,
                    outcome_count=int(outcome_count),
                    outcomes_with_token=int(outcomes_with_token),
                    has_recent_ticks=has_recent_ticks,
                    last_tick_age_min=last_tick_age_min,
                    has_link=has_link,
                )
                inserted = repo.insert_event_information_score(
                    event_id=str(event_id),
                    scored_at=now,
                    information_profile_id=str(information_profile_id) if information_profile_id else None,
                    coverage_score=coverage,
                    quality_score=quality,
                    latency_score=latency,
                    is_trade_eligible=eligible,
                    missing_fields_json=missing_fields,
                    ignore_duplicates=True,
                )
                if inserted:
                    events_scored += 1
                    rows_written += 1
                rows_read += 1

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
            )
            connection.commit()
            return MappingSyncSummary(
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                games_considered=games_considered,
                links_written=links_written,
                events_scored=events_scored,
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
            return MappingSyncSummary(
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                games_considered=games_considered,
                links_written=links_written,
                events_scored=events_scored,
                error_text=repr(exc),
            )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run cross-domain NBA/Polymarket mapping sync.")
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--lookahead-days", type=int, default=2)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    summary = run_cross_domain_mapping_sync(
        lookback_days=args.lookback_days,
        lookahead_days=args.lookahead_days,
    )
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
    print(
        " | ".join(
            [
                f"games_considered={summary.games_considered}",
                f"links_written={summary.links_written}",
                f"events_scored={summary.events_scored}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
