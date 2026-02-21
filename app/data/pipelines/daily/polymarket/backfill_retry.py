from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    EventProbeConfig,
    build_today_nba_event_probes_from_scoreboard,
    run_polymarket_event_seed_pack,
)


_NAMESPACE = uuid.UUID("ec45c6a9-b45f-4db7-8af5-a7551265c18f")


@dataclass
class BackfillRetrySummary:
    sync_run_id: str | None
    status: str
    rows_read: int
    rows_written: int
    missing_today_before: int
    missing_today_after: int
    probes_run: int
    retry_probes_added: int
    ongoing_probes_run: int
    candles_upserted: int
    error_text: str | None = None


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _insert_sync_run(
    connection: Any,
    *,
    provider_id: str,
    module_id: str,
    lookback_hours: int,
    timeframe: str,
) -> str:
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
                "daily.polymarket.backfill_retry",
                "scheduled",
                "running",
                datetime.now(timezone.utc),
                Json({"lookback_hours": lookback_hours, "timeframe": timeframe}),
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


def _slug_from_probe(probe: EventProbeConfig) -> str:
    return probe.url.rstrip("/").split("/")[-1]


def _existing_slugs(connection: Any, slugs: list[str]) -> set[str]:
    if not slugs:
        return set()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT canonical_slug
            FROM catalog.events
            WHERE canonical_slug = ANY(%s);
            """,
            (slugs,),
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _coerce_timeframe_to_pandas_freq(timeframe: str) -> str:
    key = timeframe.strip().lower()
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "1h": "1h",
    }
    if key not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return mapping[key]


def _build_retry_probes(
    connection: Any,
    *,
    retry_lookback_hours: int,
) -> list[EventProbeConfig]:
    retry_since = datetime.now(timezone.utc) - timedelta(hours=max(retry_lookback_hours, 1))
    probes: list[EventProbeConfig] = []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT meta_json
            FROM core.sync_runs
            WHERE status IN ('error', 'partial_success')
              AND pipeline_name IN (
                'db_seed_pack.polymarket_event_probe',
                'daily.polymarket.sync_events',
                'daily.polymarket.sync_markets'
              )
              AND started_at >= %s
            ORDER BY started_at DESC
            LIMIT 25;
            """,
            (retry_since,),
        )
        rows = cursor.fetchall()

    seen_urls: set[str] = set()
    for (meta_json,) in rows:
        if not isinstance(meta_json, dict):
            continue
        event_urls = meta_json.get("event_urls")
        if not isinstance(event_urls, list):
            continue
        for url in event_urls:
            url_text = str(url or "").strip()
            if not url_text or url_text in seen_urls:
                continue
            seen_urls.add(url_text)
            probes.append(
                EventProbeConfig(
                    step_code=f"retry_{len(probes)+1}",
                    url=url_text,
                    event_type_code="sports_nba_game" if "/sports/nba/" in url_text else "general_event",
                    history_mode="rolling_recent",
                    history_market_selector="moneyline" if "/sports/nba/" in url_text else "primary",
                    history_interval="1m",
                    history_fidelity=10,
                    recent_lookback_days=2,
                    allow_snapshot_fallback=True,
                    stream_enabled="/sports/nba/" in url_text,
                    stream_sample_count=2,
                    stream_sample_interval_sec=0.5,
                    stream_max_outcomes=30,
                )
            )
    return probes


def _upsert_candles_from_ticks(
    *,
    repo: JanusUpsertRepository,
    connection: Any,
    timeframe: str,
    lookback_hours: int,
) -> tuple[int, int]:
    freq = _coerce_timeframe_to_pandas_freq(timeframe)
    rows_read = 0
    rows_written = 0
    since_ts = datetime.now(timezone.utc) - timedelta(hours=max(lookback_hours, 1))

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT outcome_id, ts, price, volume
            FROM market_data.outcome_price_ticks
            WHERE ts >= %s
              AND price IS NOT NULL
            ORDER BY outcome_id, ts;
            """,
            (since_ts,),
        )
        rows = cursor.fetchall()

    if not rows:
        return 0, 0

    df = pd.DataFrame(rows, columns=["outcome_id", "ts", "price", "volume"])
    rows_read = len(df)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["ts", "price"]).reset_index(drop=True)
    if df.empty:
        return rows_read, rows_written

    for outcome_id, frame in df.groupby("outcome_id"):
        work = frame.sort_values("ts").copy()
        work["open_time"] = work["ts"].dt.floor(freq)
        grouped = work.groupby("open_time", as_index=False).agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("volume", "sum"),
            samples=("price", "count"),
        )
        for _, row in grouped.iterrows():
            inserted = repo.upsert_outcome_price_candle(
                outcome_id=str(outcome_id),
                timeframe=timeframe,
                open_time=row["open_time"].to_pydatetime(),
                source="derived_tick_agg",
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if pd.notna(row["volume"]) else None,
                raw_json={"samples": int(row["samples"]), "timeframe": timeframe},
            )
            if inserted:
                rows_written += 1
    return rows_read, rows_written


def run_backfill_retry_sync(
    *,
    max_finished: int = 2,
    max_live: int = 2,
    max_upcoming: int = 2,
    include_upcoming: bool = True,
    stream_sample_count: int = 3,
    stream_sample_interval_sec: float = 1.0,
    stream_max_outcomes: int = 30,
    retry_failed: bool = True,
    retry_lookback_hours: int = 24,
    candle_timeframe: str = "1m",
    candle_lookback_hours: int = 24,
) -> BackfillRetrySummary:
    rows_read = 0
    rows_written = 0
    probes_run = 0
    retry_probes_added = 0
    ongoing_probes_run = 0
    candles_upserted = 0

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=_uuid_for("provider", "internal_backfill"),
            code="internal_backfill",
            name="Internal Backfill",
            category="internal",
            base_url="internal://backfill",
            auth_type="none",
        )
        module_id = repo.upsert_module(
            module_id=_uuid_for("module", "polymarket_backfill_retry"),
            code="polymarket_backfill_retry",
            name="Polymarket Backfill Retry",
            description="Backfill/retry orchestration for polymarket events and market-data candles",
            owner="janus",
        )
        sync_run_id = _insert_sync_run(
            connection,
            provider_id=provider_id,
            module_id=module_id,
            lookback_hours=candle_lookback_hours,
            timeframe=candle_timeframe,
        )
        connection.commit()

        try:
            selection = build_today_nba_event_probes_from_scoreboard(
                max_finished=max_finished,
                max_live=max_live,
                max_upcoming=max_upcoming,
                include_upcoming=include_upcoming,
                stream_sample_count=stream_sample_count,
                stream_sample_interval_sec=stream_sample_interval_sec,
                stream_max_outcomes=stream_max_outcomes,
            )
            today_probes = selection.all
            today_slugs = [_slug_from_probe(item) for item in today_probes]
            existing_before = _existing_slugs(connection, today_slugs)
            missing_today_before = len(set(today_slugs) - existing_before)

            missing_probes = [item for item in today_probes if _slug_from_probe(item) not in existing_before]
            live_probes = [item for item in today_probes if "today_live" in item.step_code]
            ongoing_probes_run = len(live_probes)
            probes: list[EventProbeConfig] = [*missing_probes, *live_probes]

            if retry_failed:
                retry_probes = _build_retry_probes(connection, retry_lookback_hours=retry_lookback_hours)
                retry_probes_added = len(retry_probes)
                probes.extend(retry_probes)

            dedup: dict[str, EventProbeConfig] = {}
            for probe in probes:
                dedup[probe.url] = probe
            probes = list(dedup.values())
            probes_run = len(probes)

            rows_read += len(today_probes)
            if probes:
                seed_summary = run_polymarket_event_seed_pack(probes, persist=True)
                rows_read += seed_summary.rows_read
                rows_written += seed_summary.rows_written

            existing_after = _existing_slugs(connection, today_slugs)
            missing_today_after = len(set(today_slugs) - existing_after)

            candle_rows_read, candle_rows_written = _upsert_candles_from_ticks(
                repo=repo,
                connection=connection,
                timeframe=candle_timeframe,
                lookback_hours=candle_lookback_hours,
            )
            rows_read += candle_rows_read
            rows_written += candle_rows_written
            candles_upserted = candle_rows_written

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
            )
            connection.commit()
            return BackfillRetrySummary(
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                missing_today_before=missing_today_before,
                missing_today_after=missing_today_after,
                probes_run=probes_run,
                retry_probes_added=retry_probes_added,
                ongoing_probes_run=ongoing_probes_run,
                candles_upserted=candles_upserted,
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
            return BackfillRetrySummary(
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                missing_today_before=0,
                missing_today_after=0,
                probes_run=probes_run,
                retry_probes_added=retry_probes_added,
                ongoing_probes_run=ongoing_probes_run,
                candles_upserted=candles_upserted,
                error_text=repr(exc),
            )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run polymarket backfill/retry orchestration.")
    parser.add_argument("--max-finished", type=int, default=2)
    parser.add_argument("--max-live", type=int, default=2)
    parser.add_argument("--max-upcoming", type=int, default=2)
    parser.add_argument("--include-upcoming", action="store_true")
    parser.add_argument("--stream-sample-count", type=int, default=3)
    parser.add_argument("--stream-sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--stream-max-outcomes", type=int, default=30)
    parser.add_argument("--no-retry-failed", action="store_true")
    parser.add_argument("--retry-lookback-hours", type=int, default=24)
    parser.add_argument("--candle-timeframe", default="1m")
    parser.add_argument("--candle-lookback-hours", type=int, default=24)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    summary = run_backfill_retry_sync(
        max_finished=args.max_finished,
        max_live=args.max_live,
        max_upcoming=args.max_upcoming,
        include_upcoming=args.include_upcoming,
        stream_sample_count=args.stream_sample_count,
        stream_sample_interval_sec=args.stream_sample_interval_sec,
        stream_max_outcomes=args.stream_max_outcomes,
        retry_failed=not args.no_retry_failed,
        retry_lookback_hours=args.retry_lookback_hours,
        candle_timeframe=args.candle_timeframe,
        candle_lookback_hours=args.candle_lookback_hours,
    )
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
    print(
        " | ".join(
            [
                f"missing_today_before={summary.missing_today_before}",
                f"missing_today_after={summary.missing_today_after}",
                f"probes_run={summary.probes_run}",
                f"retry_probes_added={summary.retry_probes_added}",
                f"ongoing_probes_run={summary.ongoing_probes_run}",
                f"candles_upserted={summary.candles_upserted}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
