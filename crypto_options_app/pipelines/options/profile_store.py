from __future__ import annotations

"""SQLite store for crypto options profile universe, grades, and signals.

This module is intentionally separate from the Janus application database.  It
stores the profile signal system's own source-of-truth tables so trading code can
read stable grades from SQLite while signal streamers continue to update recent
signals independently.
"""

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.pipelines.options.profile_signals import (
    ProfileSignalConfig,
    build_profile_signal_generator_scores,
    grade_profile,
    load_active_crypto_profile_pool_refs,
    reconstruct_profile_events_from_activity,
)


PROFILE_STORE_SCHEMA_VERSION = "crypto_options_profile_store_v1"
PROFILE_GRADING_POLICY_VERSION = "crypto_options_profile_grading_v4_90_splus_elite_spp_v2"


def default_profile_store_path() -> Path:
    return (
        Path("local")
        / "shared"
        / "artifacts"
        / "crypto-options-research"
        / "profile-store"
        / "crypto_options_profiles.sqlite"
    )


def connect_profile_store(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_profile_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def initialize_profile_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else default_profile_store_path()
    conn = connect_profile_store(path)
    try:
        _create_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def ingest_profile_signal_artifact(
    artifact_path: str | Path,
    *,
    db_path: str | Path | None = None,
    profile_pool_path: str | Path | None = None,
    config: ProfileSignalConfig | None = None,
    fetch_run_id: str | None = None,
) -> dict[str, Any]:
    artifact = Path(artifact_path)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    report = payload.get("profile_signal_report") if isinstance(payload.get("profile_signal_report"), dict) else payload
    return ingest_profile_signal_report(
        report,
        db_path=db_path,
        source_artifact=str(artifact),
        profile_pool_path=profile_pool_path,
        config=config,
        fetch_run_id=fetch_run_id,
    )


def ingest_profile_signal_report(
    report: dict[str, Any],
    *,
    db_path: str | Path | None = None,
    source_artifact: str | None = None,
    profile_pool_path: str | Path | None = None,
    config: ProfileSignalConfig | None = None,
    fetch_run_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a profile signal report into the profile store.

    Profile universe rows are inserted for both fetched snapshots and all refs in
    the configured active pool.  Grades are recomputed from stored metrics using
    the current grading policy so old artifacts can be migrated when the grading
    model changes.
    """

    config = config or ProfileSignalConfig()
    source_artifact = str(source_artifact or "")
    generated_at = _text(report.get("generated_at_utc")) or _now()
    profiles = [row for row in report.get("profiles") or [] if isinstance(row, dict)]
    active_signals = [row for row in report.get("active_signals") or [] if isinstance(row, dict)]
    registry_refs = [row for row in report.get("profile_registry") or [] if isinstance(row, dict)]
    active_pool_refs = _load_pool_refs(profile_pool_path)
    signals_by_profile_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in active_signals:
        profile_key = _profile_key_from_signal(signal)
        if profile_key:
            signals_by_profile_key[profile_key].append(signal)

    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        run_id = _stable_key(source_artifact or generated_at)
        conn.execute(
            """
            INSERT INTO profile_refresh_runs (
                run_id, source_artifact, generated_at_utc, ingested_at_utc, schema_version,
                grading_policy_version, profile_seed_count, profile_snapshot_count,
                active_signal_count, candidate_count, status, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                ingested_at_utc=excluded.ingested_at_utc,
                profile_seed_count=excluded.profile_seed_count,
                profile_snapshot_count=excluded.profile_snapshot_count,
                active_signal_count=excluded.active_signal_count,
                candidate_count=excluded.candidate_count,
                status=excluded.status,
                summary_json=excluded.summary_json
            """,
            (
                run_id,
                source_artifact,
                generated_at,
                _now(),
                PROFILE_STORE_SCHEMA_VERSION,
                PROFILE_GRADING_POLICY_VERSION,
                int(report.get("profile_seed_count") or len(registry_refs)),
                len(profiles),
                int(report.get("active_signal_count") or len(active_signals)),
                int(report.get("candidate_count") or len(report.get("aggregated_candidates") or [])),
                "complete",
                _json(
                    {
                        "config": report.get("config"),
                        "blockers": report.get("blockers"),
                        "source_artifact": source_artifact,
                    }
                ),
            ),
        )

        ref_count = 0
        for ref in registry_refs + active_pool_refs:
            if _upsert_profile_ref(conn, ref, generated_at=generated_at):
                ref_count += 1

        profile_count = 0
        grade_count = 0
        raw_activity_count = 0
        generator_score_count = 0
        period_performance_count = 0
        event_reconstruction_count = 0
        for snapshot in profiles:
            profile_key = _profile_key_from_snapshot(snapshot)
            snapshot = _snapshot_with_fallback_reconstructions(
                snapshot,
                signals_by_profile_key.get(profile_key) or [],
                generated_at=generated_at,
            )
            seen_raw_activity_keys: set[str] = set()
            _upsert_profile_universe(conn, profile_key, snapshot, generated_at=generated_at)
            _upsert_snapshot_refs(conn, profile_key, snapshot, generated_at=generated_at)
            raw_activity_count += _upsert_raw_activity_rows(
                conn,
                profile_key,
                snapshot,
                generated_at=generated_at,
                source_artifact=source_artifact,
                fetch_run_id=fetch_run_id,
                seen_keys=seen_raw_activity_keys,
            )
            raw_activity_count += _upsert_active_signal_raw_rows(
                conn,
                profile_key,
                signals_by_profile_key.get(profile_key) or [],
                generated_at=generated_at,
                source_artifact=source_artifact,
                fetch_run_id=fetch_run_id,
                seen_keys=seen_raw_activity_keys,
            )
            grade_payload = _grade_payload(snapshot, config=config)
            _upsert_grade(conn, profile_key, snapshot, grade_payload, generated_at=generated_at, source_artifact=source_artifact)
            period_performance_count += _upsert_period_performance(
                conn,
                profile_key,
                snapshot,
                generated_at=generated_at,
                source_artifact=source_artifact,
            )
            event_reconstruction_count += _upsert_event_reconstructions(
                conn,
                profile_key,
                snapshot,
                generated_at=generated_at,
                source_artifact=source_artifact,
            )
            generator_score_count += _upsert_signal_generator_scores(
                conn,
                profile_key,
                snapshot,
                grade_payload,
                generated_at=generated_at,
                source_artifact=source_artifact,
                config=config,
            )
            _upsert_bot_daily(conn, profile_key, snapshot, grade_payload, generated_at=generated_at, source_artifact=source_artifact)
            profile_count += 1
            grade_count += 1

        signal_count = 0
        for signal in active_signals:
            profile_key = _profile_key_from_signal(signal)
            if profile_key:
                _upsert_signal(conn, profile_key, signal, source_artifact=source_artifact)
                signal_count += 1

        conn.execute(
            """
            INSERT INTO service_watermarks(service_name, last_run_at_utc, status, state_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                last_run_at_utc=excluded.last_run_at_utc,
                status=excluded.status,
                state_json=excluded.state_json
            """,
            (
                "profile_store_ingest",
                _now(),
                "complete",
                _json(
                    {
                        "source_artifact": source_artifact,
                        "profiles": profile_count,
                        "raw_activity_rows": raw_activity_count,
                        "grades": grade_count,
                        "generator_scores": generator_score_count,
                        "period_performance_rows": period_performance_count,
                        "event_reconstructions": event_reconstruction_count,
                        "signals": signal_count,
                        "refs_seen": ref_count,
                    }
                ),
            ),
        )

        conn.commit()
    finally:
        conn.close()

    return {
        "schema_version": PROFILE_STORE_SCHEMA_VERSION,
        "db_path": str(Path(db_path) if db_path else default_profile_store_path()),
        "source_artifact": source_artifact,
        "fetch_run_id": fetch_run_id,
        "generated_at_utc": generated_at,
        "profile_refs_seen": ref_count,
        "profiles_upserted": profile_count,
        "raw_activity_upserted": raw_activity_count,
        "grades_upserted": grade_count,
        "generator_scores_upserted": generator_score_count,
        "period_performance_upserted": period_performance_count,
        "event_reconstructions_upserted": event_reconstruction_count,
        "signals_upserted": signal_count,
    }


def profile_store_summary(db_path: str | Path | None = None) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        conn.commit()
        return {
            "schema_version": PROFILE_STORE_SCHEMA_VERSION,
            "db_path": str(Path(db_path) if db_path else default_profile_store_path()),
            "profiles": _scalar(conn, "SELECT COUNT(*) FROM profile_universe"),
            "profile_refs": _scalar(conn, "SELECT COUNT(*) FROM profile_refs"),
            "raw_activity_rows": _scalar(conn, "SELECT COUNT(*) FROM profile_raw_activity"),
            "fetch_runs": _scalar(conn, "SELECT COUNT(*) FROM profile_fetch_runs"),
            "current_grades": _scalar(conn, "SELECT COUNT(*) FROM profile_grade_current"),
            "generator_scores": _scalar(conn, "SELECT COUNT(*) FROM profile_signal_generator_scores"),
            "period_performance_rows": _scalar(conn, "SELECT COUNT(*) FROM profile_period_performance"),
            "event_reconstructions": _scalar(conn, "SELECT COUNT(*) FROM profile_event_reconstructions"),
            "signals": _scalar(conn, "SELECT COUNT(*) FROM profile_signal_events"),
            "daily_bot_rows": _scalar(conn, "SELECT COUNT(*) FROM profile_bot_daily"),
            "grade_counts": _rows(conn, "SELECT grade, COUNT(*) AS count FROM profile_grade_current GROUP BY grade ORDER BY count DESC"),
            "style_counts": _rows(conn, "SELECT trading_style, COUNT(*) AS count FROM profile_grade_current GROUP BY trading_style ORDER BY count DESC"),
            "live_route_counts": _rows(
                conn,
                """
                SELECT live_route, COUNT(*) AS count
                FROM profile_grade_current
                GROUP BY live_route
                ORDER BY count DESC
                """,
            ),
            "fresh_current_grades": _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM profile_grade_current
                WHERE evaluated_at_utc=(SELECT MAX(generated_at_utc) FROM profile_refresh_runs WHERE status='complete')
                """,
            ),
            "fresh_live_route_counts": _rows(
                conn,
                """
                SELECT live_route, COUNT(*) AS count
                FROM v_crypto_options_fresh_live_profile_grades
                GROUP BY live_route
                ORDER BY count DESC
                """,
            ),
            "fresh_generator_score_counts": _rows(
                conn,
                """
                SELECT generator_id, status, COUNT(*) AS count, SUM(can_emit_live) AS live_enabled_count,
                       SUM(usage_eligible) AS usage_eligible_count,
                       ROUND(AVG(score), 3) AS avg_score
                FROM v_crypto_options_fresh_profile_signal_generator_scores
                GROUP BY generator_id, status
                ORDER BY generator_id, status
                """,
            ),
            "top_hedgers": _rows(
                conn,
                """
                SELECT profile_name, grade, score, trading_style_detail, frequency_class,
                       active_crypto_last_5m, active_crypto_most_of_last_hour,
                       daily_pnl_usd, weekly_pnl_usd, monthly_pnl_usd, all_pnl_usd
                FROM v_crypto_options_fresh_live_profile_grades
                WHERE live_route='hedger'
                ORDER BY grade_rank DESC, score DESC, daily_pnl_usd DESC
                LIMIT 10
                """,
            ),
            "top_outcome_predictors": _rows(
                conn,
                """
                SELECT profile_name, grade, score, trading_style_detail, frequency_class,
                       active_crypto_last_5m, active_crypto_most_of_last_hour,
                       daily_pnl_usd, weekly_pnl_usd, monthly_pnl_usd, all_pnl_usd
                FROM v_crypto_options_fresh_live_profile_grades
                WHERE live_route='outcome_predictor'
                ORDER BY grade_rank DESC, score DESC, daily_pnl_usd DESC
                LIMIT 10
                """,
            ),
        }
    finally:
        conn.close()


def record_profile_fetch_run(
    *,
    fetch_run_id: str,
    started_at_utc: str,
    completed_at_utc: str | None,
    status: str,
    requested_ref_count: int,
    fetched_profile_count: int,
    failed_profile_count: int,
    max_concurrency: int,
    page_limit: int | None,
    summary: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        conn.execute(
            """
            INSERT INTO profile_fetch_runs (
                fetch_run_id, started_at_utc, completed_at_utc, status,
                requested_ref_count, fetched_profile_count, failed_profile_count,
                max_concurrency, page_limit, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fetch_run_id) DO UPDATE SET
                completed_at_utc=excluded.completed_at_utc,
                status=excluded.status,
                requested_ref_count=excluded.requested_ref_count,
                fetched_profile_count=excluded.fetched_profile_count,
                failed_profile_count=excluded.failed_profile_count,
                max_concurrency=excluded.max_concurrency,
                page_limit=excluded.page_limit,
                summary_json=excluded.summary_json
            """,
            (
                fetch_run_id,
                started_at_utc,
                completed_at_utc,
                status,
                int(requested_ref_count),
                int(fetched_profile_count),
                int(failed_profile_count),
                int(max_concurrency),
                page_limit,
                _json(summary or {}),
            ),
        )
        conn.commit()
        return {
            "schema_version": "crypto_options_profile_fetch_run_record_v1",
            "db_path": str(Path(db_path) if db_path else default_profile_store_path()),
            "fetch_run_id": fetch_run_id,
            "status": status,
            "requested_ref_count": int(requested_ref_count),
            "fetched_profile_count": int(fetched_profile_count),
            "failed_profile_count": int(failed_profile_count),
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def profile_store_fresh_profiles(db_path: str | Path | None = None, *, limit: int = 200) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        rows = _rows(
            conn,
            f"""
            SELECT *
            FROM v_crypto_options_fresh_live_profile_grades
            ORDER BY grade_rank DESC, score DESC
            LIMIT {max(1, min(int(limit), 1000))}
            """,
        )
        return {
            "schema_version": "crypto_options_profile_store_fresh_profiles_v1",
            "count": len(rows),
            "items": rows,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def profile_store_fresh_generator_scores(
    db_path: str | Path | None = None,
    *,
    generator_id: str | None = None,
    usage_eligible: bool | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        clauses: list[str] = []
        params: list[Any] = []
        if generator_id:
            clauses.append("generator_id=?")
            params.append(generator_id)
        if usage_eligible is not None:
            clauses.append("usage_eligible=?")
            params.append(1 if usage_eligible else 0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = _decode_json_columns(
            [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT *
                    FROM v_crypto_options_fresh_profile_signal_generator_scores
                    {where}
                    ORDER BY score DESC, profile_name
                    LIMIT {max(1, min(int(limit), 1000))}
                    """,
                    params,
                ).fetchall()
            ],
            ["usage_blockers_json", "components_json", "requirements_json", "metrics_json"],
        )
        return {
            "schema_version": "crypto_options_profile_store_fresh_generator_scores_v1",
            "count": len(rows),
            "items": rows,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def profile_store_fresh_period_performance(
    db_path: str | Path | None = None,
    *,
    period: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        params: list[Any] = []
        where = """
        WHERE evaluated_at_utc=(SELECT MAX(generated_at_utc) FROM profile_refresh_runs WHERE status='complete')
        """
        if period:
            where += " AND period=?"
            params.append(period)
        rows = _decode_json_columns(
            [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT *
                    FROM profile_period_performance
                    {where}
                    ORDER BY period, profile_key
                    LIMIT {max(1, min(int(limit), 1000))}
                    """,
                    params,
                ).fetchall()
            ],
            ["metrics_json"],
        )
        return {
            "schema_version": "crypto_options_profile_store_fresh_period_performance_v1",
            "count": len(rows),
            "items": rows,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def profile_store_fresh_event_reconstructions(
    db_path: str | Path | None = None,
    *,
    event_style: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        params: list[Any] = []
        where = """
        WHERE evaluated_at_utc=(SELECT MAX(generated_at_utc) FROM profile_refresh_runs WHERE status='complete')
        """
        if event_style:
            where += " AND event_style=?"
            params.append(event_style)
        rows = _decode_json_columns(
            [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT *
                    FROM profile_event_reconstructions
                    {where}
                    ORDER BY last_signal_at_utc DESC, profile_key
                    LIMIT {max(1, min(int(limit), 1000))}
                    """,
                    params,
                ).fetchall()
            ],
            ["quality_flags_json", "summary_json"],
        )
        return {
            "schema_version": "crypto_options_profile_store_fresh_event_reconstructions_v1",
            "count": len(rows),
            "items": rows,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def refresh_profile_event_timing_links(
    event_rows: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    clear_existing: bool = False,
) -> dict[str, Any]:
    """Link stored profile activity to known event windows and flag buying ahead."""

    normalized_events = [_normalize_event_timing_row(row) for row in event_rows]
    normalized_events = [row for row in normalized_events if row]
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        if clear_existing:
            conn.execute("DELETE FROM profile_event_timing_links")
            conn.execute(
                """
                UPDATE profile_raw_activity
                SET event_start_time_utc=NULL,
                    event_end_time_utc=NULL,
                    seconds_before_event_start=NULL,
                    buying_ahead=0,
                    active_during_event=0
                """
            )
        linked = 0
        buying_ahead = 0
        active_during = 0
        seen_link_keys: set[str] = set()
        for event in normalized_events:
            conditions = []
            params: list[Any] = []
            if event.get("event_slug"):
                conditions.append("event_slug = ?")
                params.append(event["event_slug"])
            if event.get("condition_id"):
                conditions.append("condition_id = ?")
                params.append(event["condition_id"])
            if event.get("token_id"):
                conditions.append("token_id = ?")
                params.append(event["token_id"])
            if not conditions:
                continue
            rows = conn.execute(
                f"""
                SELECT raw_activity_key, profile_key, activity_at_utc, order_side,
                       event_slug, condition_id, token_id, outcome_side
                FROM profile_raw_activity
                WHERE ({' OR '.join(conditions)})
                  AND activity_at_utc IS NOT NULL
                """,
                tuple(params),
            ).fetchall()
            for row in rows:
                timing = _profile_event_timing(row, event)
                if timing["link_key"] in seen_link_keys:
                    continue
                seen_link_keys.add(timing["link_key"])
                conn.execute(
                    """
                    INSERT INTO profile_event_timing_links (
                        link_key, profile_key, raw_activity_key, event_slug, condition_id,
                        token_id, outcome_side, activity_at_utc, order_side,
                        event_start_time_utc, event_end_time_utc, seconds_before_event_start,
                        buying_ahead, active_during_event, source, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(link_key) DO UPDATE SET
                        event_start_time_utc=excluded.event_start_time_utc,
                        event_end_time_utc=excluded.event_end_time_utc,
                        seconds_before_event_start=excluded.seconds_before_event_start,
                        buying_ahead=excluded.buying_ahead,
                        active_during_event=excluded.active_during_event,
                        updated_at_utc=excluded.updated_at_utc
                    """,
                    (
                        timing["link_key"],
                        timing["profile_key"],
                        timing["raw_activity_key"],
                        timing.get("event_slug"),
                        timing.get("condition_id"),
                        timing.get("token_id"),
                        timing.get("outcome_side"),
                        timing.get("activity_at_utc"),
                        timing.get("order_side"),
                        timing.get("event_start_time_utc"),
                        timing.get("event_end_time_utc"),
                        timing.get("seconds_before_event_start"),
                        timing["buying_ahead"],
                        timing["active_during_event"],
                        "polymarket_event_universe_link",
                        _now(),
                    ),
                )
                conn.execute(
                    """
                    UPDATE profile_raw_activity
                    SET event_start_time_utc=?,
                        event_end_time_utc=?,
                        seconds_before_event_start=?,
                        buying_ahead=?,
                        active_during_event=?
                    WHERE raw_activity_key=?
                    """,
                    (
                        timing.get("event_start_time_utc"),
                        timing.get("event_end_time_utc"),
                        timing.get("seconds_before_event_start"),
                        timing["buying_ahead"],
                        timing["active_during_event"],
                        timing["raw_activity_key"],
                    ),
                )
                linked += 1
                buying_ahead += int(timing["buying_ahead"])
                active_during += int(timing["active_during_event"])
        conn.execute(
            """
            INSERT INTO service_watermarks(service_name, last_run_at_utc, status, state_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                last_run_at_utc=excluded.last_run_at_utc,
                status=excluded.status,
                state_json=excluded.state_json
            """,
            (
                "profile_event_timing_links",
                _now(),
                "complete",
                _json({"event_rows": len(normalized_events), "linked": linked, "buying_ahead": buying_ahead}),
            ),
        )
        conn.commit()
        return {
            "schema_version": "crypto_options_profile_event_timing_links_v1",
            "event_rows": len(normalized_events),
            "linked_activity_rows": linked,
            "buying_ahead_rows": buying_ahead,
            "active_during_event_rows": active_during,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def profile_store_buying_ahead_profiles(
    *,
    limit: int = 200,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = connect_profile_store(db_path)
    try:
        _create_schema(conn)
        rows = _decode_json_columns(
            [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM v_crypto_options_profile_buying_ahead
                    ORDER BY first_buying_ahead_at_utc DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 1000)),),
                ).fetchall()
            ],
            [],
        )
        return {
            "schema_version": "crypto_options_profile_buying_ahead_v1",
            "count": len(rows),
            "items": rows,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS profile_universe (
            profile_key TEXT PRIMARY KEY,
            normalized_ref TEXT,
            handle TEXT,
            proxy_wallet TEXT,
            profile_name TEXT,
            pseudonym TEXT,
            verified_badge INTEGER,
            profile_created_at_utc TEXT,
            sources_json TEXT NOT NULL DEFAULT '[]',
            first_seen_at_utc TEXT NOT NULL,
            last_seen_at_utc TEXT NOT NULL,
            latest_snapshot_at_utc TEXT,
            latest_activity_utc TEXT,
            latest_grade TEXT,
            latest_score REAL,
            latest_trading_style TEXT,
            latest_trading_style_detail TEXT,
            latest_frequency_class TEXT,
            active_crypto_last_5m INTEGER NOT NULL DEFAULT 0,
            active_crypto_most_of_last_hour INTEGER NOT NULL DEFAULT 0,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_refs (
            normalized_ref TEXT PRIMARY KEY,
            profile_key TEXT,
            raw_ref TEXT,
            handle TEXT,
            address TEXT,
            source TEXT,
            first_seen_at_utc TEXT NOT NULL,
            last_seen_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE TABLE IF NOT EXISTS profile_refresh_runs (
            run_id TEXT PRIMARY KEY,
            source_artifact TEXT,
            generated_at_utc TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            grading_policy_version TEXT NOT NULL,
            profile_seed_count INTEGER NOT NULL DEFAULT 0,
            profile_snapshot_count INTEGER NOT NULL DEFAULT 0,
            active_signal_count INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_fetch_runs (
            fetch_run_id TEXT PRIMARY KEY,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            requested_ref_count INTEGER NOT NULL DEFAULT 0,
            fetched_profile_count INTEGER NOT NULL DEFAULT 0,
            failed_profile_count INTEGER NOT NULL DEFAULT 0,
            max_concurrency INTEGER NOT NULL DEFAULT 1,
            page_limit INTEGER,
            summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_raw_activity (
            raw_activity_key TEXT PRIMARY KEY,
            profile_key TEXT NOT NULL,
            fetch_run_id TEXT,
            source_artifact TEXT,
            source_type TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            activity_at_utc TEXT,
            event_slug TEXT,
            condition_id TEXT,
            market_slug TEXT,
            symbol TEXT,
            cadence TEXT,
            order_side TEXT,
            outcome_side TEXT,
            token_id TEXT,
            price REAL,
            shares REAL,
            notional_usd REAL,
            transaction_hash TEXT,
            event_start_time_utc TEXT,
            event_end_time_utc TEXT,
            seconds_before_event_start REAL,
            buying_ahead INTEGER NOT NULL DEFAULT 0,
            active_during_event INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL,
            inserted_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE INDEX IF NOT EXISTS ix_profile_raw_activity_profile_time
        ON profile_raw_activity(profile_key, activity_at_utc);

        CREATE INDEX IF NOT EXISTS ix_profile_raw_activity_event
        ON profile_raw_activity(event_slug, condition_id, outcome_side);

        CREATE TABLE IF NOT EXISTS profile_grade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_key TEXT NOT NULL,
            evaluated_at_utc TEXT NOT NULL,
            source_artifact TEXT,
            grading_policy_version TEXT NOT NULL,
            grade TEXT NOT NULL,
            score REAL,
            grade_rank INTEGER NOT NULL DEFAULT 0,
            polarity TEXT,
            live_route TEXT,
            trading_style TEXT,
            trading_style_detail TEXT,
            frequency_class TEXT,
            active_crypto_last_5m INTEGER NOT NULL DEFAULT 0,
            active_crypto_most_of_last_hour INTEGER NOT NULL DEFAULT 0,
            crypto_event_count_1h INTEGER NOT NULL DEFAULT 0,
            crypto_event_count_24h INTEGER NOT NULL DEFAULT 0,
            crypto_signal_count_1h INTEGER NOT NULL DEFAULT 0,
            daily_pnl_usd REAL,
            weekly_pnl_usd REAL,
            monthly_pnl_usd REAL,
            quarterly_pnl_usd REAL,
            all_pnl_usd REAL,
            closed_win_rate REAL,
            closed_return_pct REAL,
            closed_wins INTEGER NOT NULL DEFAULT 0,
            closed_losses INTEGER NOT NULL DEFAULT 0,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            grade_reasons_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_profile_grade_history
        ON profile_grade_history(profile_key, evaluated_at_utc, grading_policy_version, COALESCE(source_artifact, ''));

        CREATE TABLE IF NOT EXISTS profile_grade_current (
            profile_key TEXT PRIMARY KEY,
            evaluated_at_utc TEXT NOT NULL,
            source_artifact TEXT,
            grading_policy_version TEXT NOT NULL,
            profile_name TEXT,
            proxy_wallet TEXT,
            grade TEXT NOT NULL,
            score REAL,
            grade_rank INTEGER NOT NULL DEFAULT 0,
            polarity TEXT,
            live_route TEXT,
            trading_style TEXT,
            trading_style_detail TEXT,
            frequency_class TEXT,
            active_crypto_last_5m INTEGER NOT NULL DEFAULT 0,
            active_crypto_most_of_last_hour INTEGER NOT NULL DEFAULT 0,
            crypto_event_count_1h INTEGER NOT NULL DEFAULT 0,
            crypto_event_count_24h INTEGER NOT NULL DEFAULT 0,
            crypto_signal_count_1h INTEGER NOT NULL DEFAULT 0,
            daily_pnl_usd REAL,
            weekly_pnl_usd REAL,
            monthly_pnl_usd REAL,
            quarterly_pnl_usd REAL,
            all_pnl_usd REAL,
            closed_win_rate REAL,
            closed_return_pct REAL,
            closed_wins INTEGER NOT NULL DEFAULT 0,
            closed_losses INTEGER NOT NULL DEFAULT 0,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            grade_reasons_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE TABLE IF NOT EXISTS profile_signal_generator_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_key TEXT NOT NULL,
            evaluated_at_utc TEXT NOT NULL,
            source_artifact TEXT,
            grading_policy_version TEXT NOT NULL,
            generator_id TEXT NOT NULL,
            account_type TEXT,
            grade TEXT,
            score REAL NOT NULL,
            status TEXT NOT NULL,
            signal_role TEXT,
            can_emit_live INTEGER NOT NULL DEFAULT 0,
            usage_eligible INTEGER NOT NULL DEFAULT 0,
            usage_blockers_json TEXT NOT NULL DEFAULT '[]',
            components_json TEXT NOT NULL,
            requirements_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE INDEX IF NOT EXISTS ix_profile_signal_generator_scores_fresh
        ON profile_signal_generator_scores(evaluated_at_utc, generator_id, status, score DESC);

        CREATE INDEX IF NOT EXISTS ix_profile_signal_generator_scores_profile
        ON profile_signal_generator_scores(profile_key, evaluated_at_utc);

        CREATE TABLE IF NOT EXISTS profile_period_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_key TEXT NOT NULL,
            evaluated_at_utc TEXT NOT NULL,
            source_artifact TEXT,
            grading_policy_version TEXT NOT NULL,
            period TEXT NOT NULL,
            pnl_usd REAL,
            closed_win_rate REAL,
            closed_return_pct REAL,
            closed_wins INTEGER NOT NULL DEFAULT 0,
            closed_losses INTEGER NOT NULL DEFAULT 0,
            closed_count INTEGER NOT NULL DEFAULT 0,
            reconstructed_event_win_rate REAL,
            reconstructed_event_return_pct REAL,
            reconstructed_event_wins INTEGER NOT NULL DEFAULT 0,
            reconstructed_event_losses INTEGER NOT NULL DEFAULT 0,
            reconstructed_event_count INTEGER NOT NULL DEFAULT 0,
            source_quality TEXT,
            metrics_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE INDEX IF NOT EXISTS ix_profile_period_performance_fresh
        ON profile_period_performance(evaluated_at_utc, period, profile_key);

        CREATE TABLE IF NOT EXISTS profile_event_reconstructions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_key TEXT NOT NULL,
            evaluated_at_utc TEXT NOT NULL,
            source_artifact TEXT,
            event_key TEXT NOT NULL,
            event_slug TEXT,
            first_signal_at_utc TEXT,
            last_signal_at_utc TEXT,
            event_style TEXT,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            up_open_shares REAL,
            down_open_shares REAL,
            up_cost_basis_usd REAL,
            down_cost_basis_usd REAL,
            realized_pnl_usd REAL,
            event_pnl_usd REAL,
            event_pnl_known INTEGER NOT NULL DEFAULT 0,
            event_effective_win INTEGER,
            quality_flags_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE INDEX IF NOT EXISTS ix_profile_event_reconstructions_fresh
        ON profile_event_reconstructions(evaluated_at_utc, event_style, profile_key);

        CREATE TABLE IF NOT EXISTS profile_event_timing_links (
            link_key TEXT PRIMARY KEY,
            profile_key TEXT NOT NULL,
            raw_activity_key TEXT NOT NULL,
            event_slug TEXT,
            condition_id TEXT,
            token_id TEXT,
            outcome_side TEXT,
            activity_at_utc TEXT,
            order_side TEXT,
            event_start_time_utc TEXT,
            event_end_time_utc TEXT,
            seconds_before_event_start REAL,
            buying_ahead INTEGER NOT NULL DEFAULT 0,
            active_during_event INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );
        CREATE INDEX IF NOT EXISTS ix_profile_event_timing_links_profile
        ON profile_event_timing_links(profile_key, event_start_time_utc, buying_ahead);

        CREATE TABLE IF NOT EXISTS profile_bot_daily (
            profile_key TEXT NOT NULL,
            day_utc TEXT NOT NULL,
            is_crypto_bot INTEGER NOT NULL,
            trading_style TEXT,
            trading_style_detail TEXT,
            frequency_class TEXT,
            crypto_event_count_24h INTEGER NOT NULL DEFAULT 0,
            crypto_signal_count_24h INTEGER NOT NULL DEFAULT 0,
            avg_crypto_trades_per_event_24h REAL,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            active_crypto_most_of_last_hour INTEGER NOT NULL DEFAULT 0,
            daily_pnl_usd REAL,
            grade TEXT,
            score REAL,
            source_artifact TEXT,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY(profile_key, day_utc),
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE TABLE IF NOT EXISTS profile_signal_events (
            signal_key TEXT PRIMARY KEY,
            profile_key TEXT NOT NULL,
            signal_at_utc TEXT,
            event_slug TEXT,
            condition_id TEXT,
            market_slug TEXT,
            symbol TEXT,
            cadence TEXT,
            action_side TEXT,
            raw_outcome TEXT,
            effective_outcome TEXT,
            price REAL,
            size REAL,
            usdc_size REAL,
            transaction_hash TEXT,
            profile_grade TEXT,
            profile_score REAL,
            profile_trading_style TEXT,
            profile_trading_style_detail TEXT,
            profile_frequency_class TEXT,
            source_artifact TEXT,
            raw_json TEXT NOT NULL,
            inserted_at_utc TEXT NOT NULL,
            FOREIGN KEY(profile_key) REFERENCES profile_universe(profile_key)
        );

        CREATE TABLE IF NOT EXISTS service_watermarks (
            service_name TEXT PRIMARY KEY,
            last_run_at_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            state_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_profile_grade_current_route
        ON profile_grade_current(live_route, grade_rank DESC, score DESC);

        CREATE INDEX IF NOT EXISTS ix_profile_signal_events_time
        ON profile_signal_events(signal_at_utc, event_slug);

        CREATE VIEW IF NOT EXISTS v_crypto_options_live_profile_grades AS
        SELECT
            u.profile_key,
            COALESCE(g.profile_name, u.profile_name) AS profile_name,
            COALESCE(g.proxy_wallet, u.proxy_wallet) AS proxy_wallet,
            g.grade,
            g.score,
            g.grade_rank,
            g.live_route,
            g.trading_style,
            g.trading_style_detail,
            g.frequency_class,
            g.active_crypto_last_5m,
            g.active_crypto_most_of_last_hour,
            g.crypto_event_count_1h,
            g.crypto_event_count_24h,
            g.crypto_signal_count_1h,
            g.daily_pnl_usd,
            g.weekly_pnl_usd,
            g.monthly_pnl_usd,
            g.quarterly_pnl_usd,
            g.all_pnl_usd,
            g.closed_win_rate,
            g.closed_return_pct,
            g.buy_count,
            g.sell_count,
            g.evaluated_at_utc
        FROM profile_grade_current g
        JOIN profile_universe u ON u.profile_key = g.profile_key
        WHERE g.grade IN ('S++', 'S+', 'S');

        CREATE VIEW IF NOT EXISTS v_crypto_options_fresh_live_profile_grades AS
        SELECT *
        FROM v_crypto_options_live_profile_grades
        WHERE evaluated_at_utc = (
            SELECT MAX(generated_at_utc)
            FROM profile_refresh_runs
            WHERE status='complete'
        );

        DROP VIEW IF EXISTS v_crypto_options_fresh_profile_signal_generator_scores;

        CREATE VIEW v_crypto_options_fresh_profile_signal_generator_scores AS
        SELECT
            s.profile_key,
            COALESCE(g.profile_name, u.profile_name) AS profile_name,
            COALESCE(g.proxy_wallet, u.proxy_wallet) AS proxy_wallet,
            s.generator_id,
            s.account_type,
            s.grade,
            s.score,
            s.status,
            s.signal_role,
            s.can_emit_live,
            s.usage_eligible,
            s.usage_blockers_json,
            s.components_json,
            s.requirements_json,
            s.metrics_json,
            g.trading_style,
            g.trading_style_detail,
            g.frequency_class,
            g.active_crypto_last_5m,
            g.active_crypto_most_of_last_hour,
            g.crypto_event_count_1h,
            g.crypto_event_count_24h,
            g.crypto_signal_count_1h,
            g.daily_pnl_usd,
            g.weekly_pnl_usd,
            g.monthly_pnl_usd,
            g.all_pnl_usd,
            s.evaluated_at_utc
        FROM profile_signal_generator_scores s
        JOIN profile_universe u ON u.profile_key = s.profile_key
        LEFT JOIN profile_grade_current g ON g.profile_key = s.profile_key
        WHERE s.evaluated_at_utc = (
            SELECT MAX(generated_at_utc)
            FROM profile_refresh_runs
            WHERE status='complete'
        );

        DROP VIEW IF EXISTS v_crypto_options_profile_buying_ahead;

        CREATE VIEW v_crypto_options_profile_buying_ahead AS
        SELECT
            l.profile_key,
            COALESCE(g.profile_name, u.profile_name) AS profile_name,
            COALESCE(g.proxy_wallet, u.proxy_wallet) AS proxy_wallet,
            g.grade,
            g.score,
            g.trading_style,
            g.trading_style_detail,
            g.frequency_class,
            COUNT(*) AS buying_ahead_rows,
            COUNT(DISTINCT l.event_slug) AS buying_ahead_events,
            MIN(l.activity_at_utc) AS first_buying_ahead_at_utc,
            MAX(l.activity_at_utc) AS last_buying_ahead_at_utc,
            MIN(l.seconds_before_event_start) AS min_seconds_before_event_start,
            MAX(l.seconds_before_event_start) AS max_seconds_before_event_start
        FROM profile_event_timing_links l
        JOIN profile_universe u ON u.profile_key = l.profile_key
        LEFT JOIN profile_grade_current g ON g.profile_key = l.profile_key
        WHERE l.buying_ahead = 1
        GROUP BY
            l.profile_key,
            COALESCE(g.profile_name, u.profile_name),
            COALESCE(g.proxy_wallet, u.proxy_wallet),
            g.grade,
            g.score,
            g.trading_style,
            g.trading_style_detail,
            g.frequency_class;
        """
    )
    _ensure_profile_activity_timing_columns(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_profile_raw_activity_buying_ahead
        ON profile_raw_activity(buying_ahead, event_start_time_utc, profile_key)
        """
    )
    _ensure_table_column(conn, "profile_signal_generator_scores", "usage_eligible", "INTEGER NOT NULL DEFAULT 0")
    _ensure_table_column(conn, "profile_signal_generator_scores", "usage_blockers_json", "TEXT NOT NULL DEFAULT '[]'")


def _ensure_profile_activity_timing_columns(conn: sqlite3.Connection) -> None:
    _ensure_table_column(conn, "profile_raw_activity", "event_start_time_utc", "TEXT")
    _ensure_table_column(conn, "profile_raw_activity", "event_end_time_utc", "TEXT")
    _ensure_table_column(conn, "profile_raw_activity", "seconds_before_event_start", "REAL")
    _ensure_table_column(conn, "profile_raw_activity", "buying_ahead", "INTEGER NOT NULL DEFAULT 0")
    _ensure_table_column(conn, "profile_raw_activity", "active_during_event", "INTEGER NOT NULL DEFAULT 0")


def _ensure_table_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _upsert_profile_ref(conn: sqlite3.Connection, ref: dict[str, Any], *, generated_at: str) -> bool:
    normalized = _text(ref.get("normalized_ref"))
    if not normalized:
        return False
    profile_key = _text(ref.get("profile_key")) or _profile_key_from_ref(ref)
    if profile_key and not ref.get("profile_key"):
        _ensure_unresolved_profile(conn, profile_key, ref, generated_at=generated_at)
    conn.execute(
        """
        INSERT INTO profile_refs(normalized_ref, profile_key, raw_ref, handle, address, source, first_seen_at_utc, last_seen_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_ref) DO UPDATE SET
            profile_key=COALESCE(excluded.profile_key, profile_refs.profile_key),
            raw_ref=excluded.raw_ref,
            handle=excluded.handle,
            address=excluded.address,
            source=_merge_json_text(profile_refs.source, excluded.source),
            last_seen_at_utc=excluded.last_seen_at_utc
        """,
        (
            normalized,
            profile_key,
            _text(ref.get("raw_ref")),
            _text(ref.get("handle")),
            _text(ref.get("address")),
            _text(ref.get("source")),
            generated_at,
            generated_at,
        ),
    )
    return True


def _upsert_profile_universe(conn: sqlite3.Connection, profile_key: str, snapshot: dict[str, Any], *, generated_at: str) -> None:
    profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    profile_ref = snapshot.get("profile_ref") if isinstance(snapshot.get("profile_ref"), dict) else {}
    sources = _sources_from_ref(profile_ref)
    conn.execute(
        """
        INSERT INTO profile_universe (
            profile_key, normalized_ref, handle, proxy_wallet, profile_name, pseudonym,
            verified_badge, profile_created_at_utc, sources_json, first_seen_at_utc,
            last_seen_at_utc, latest_snapshot_at_utc, latest_activity_utc,
            latest_grade, latest_score, latest_trading_style, latest_trading_style_detail,
            latest_frequency_class, active_crypto_last_5m, active_crypto_most_of_last_hour,
            updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_key) DO UPDATE SET
            normalized_ref=COALESCE(excluded.normalized_ref, profile_universe.normalized_ref),
            handle=COALESCE(excluded.handle, profile_universe.handle),
            proxy_wallet=COALESCE(excluded.proxy_wallet, profile_universe.proxy_wallet),
            profile_name=COALESCE(excluded.profile_name, profile_universe.profile_name),
            pseudonym=COALESCE(excluded.pseudonym, profile_universe.pseudonym),
            verified_badge=COALESCE(excluded.verified_badge, profile_universe.verified_badge),
            profile_created_at_utc=COALESCE(excluded.profile_created_at_utc, profile_universe.profile_created_at_utc),
            sources_json=excluded.sources_json,
            last_seen_at_utc=excluded.last_seen_at_utc,
            latest_snapshot_at_utc=excluded.latest_snapshot_at_utc,
            latest_activity_utc=excluded.latest_activity_utc,
            latest_grade=excluded.latest_grade,
            latest_score=excluded.latest_score,
            latest_trading_style=excluded.latest_trading_style,
            latest_trading_style_detail=excluded.latest_trading_style_detail,
            latest_frequency_class=excluded.latest_frequency_class,
            active_crypto_last_5m=excluded.active_crypto_last_5m,
            active_crypto_most_of_last_hour=excluded.active_crypto_most_of_last_hour,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            profile_key,
            _text(profile_ref.get("normalized_ref")),
            _text(profile_ref.get("handle")),
            _text(profile.get("proxy_wallet")),
            _text(profile.get("name")),
            _text(profile.get("pseudonym")),
            _bool(profile.get("verified_badge")),
            _text(profile.get("created_at")),
            _json(sources),
            generated_at,
            generated_at,
            generated_at,
            _text(metrics.get("latest_activity_utc")),
            _text(snapshot.get("grade")),
            _float(snapshot.get("score")),
            _text(snapshot.get("trading_style")),
            _text(snapshot.get("trading_style_detail")),
            _text(snapshot.get("frequency_class")),
            _bool(metrics.get("active_crypto_in_last_5m")),
            _bool(metrics.get("active_crypto_most_of_last_hour")),
            _now(),
        ),
    )


def _ensure_unresolved_profile(conn: sqlite3.Connection, profile_key: str, ref: dict[str, Any], *, generated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO profile_universe(profile_key, normalized_ref, handle, proxy_wallet, profile_name, sources_json, first_seen_at_utc, last_seen_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_key) DO UPDATE SET
            last_seen_at_utc=excluded.last_seen_at_utc,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            profile_key,
            _text(ref.get("normalized_ref")),
            _text(ref.get("handle")),
            _text(ref.get("address")),
            _text(ref.get("handle") or ref.get("address")),
            _json(_sources_from_ref(ref)),
            generated_at,
            generated_at,
            _now(),
        ),
    )


def _upsert_snapshot_refs(conn: sqlite3.Connection, profile_key: str, snapshot: dict[str, Any], *, generated_at: str) -> None:
    ref = snapshot.get("profile_ref")
    if not isinstance(ref, dict):
        return
    ref = dict(ref)
    unresolved_key = _profile_key_from_ref(ref)
    ref["profile_key"] = profile_key
    _upsert_profile_ref(conn, ref, generated_at=generated_at)
    normalized = _text(ref.get("normalized_ref"))
    if normalized:
        conn.execute("UPDATE profile_refs SET profile_key=? WHERE normalized_ref=?", (profile_key, normalized))
    if unresolved_key and unresolved_key != profile_key:
        conn.execute(
            """
            DELETE FROM profile_universe
            WHERE profile_key=?
              AND NOT EXISTS (SELECT 1 FROM profile_refs WHERE profile_key=?)
              AND NOT EXISTS (SELECT 1 FROM profile_grade_current WHERE profile_key=?)
              AND NOT EXISTS (SELECT 1 FROM profile_signal_events WHERE profile_key=?)
            """,
            (unresolved_key, unresolved_key, unresolved_key, unresolved_key),
        )


def _snapshot_with_fallback_reconstructions(
    snapshot: dict[str, Any],
    active_signals: list[dict[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    if metrics.get("event_reconstructions") or not active_signals:
        return snapshot
    generated_dt = _parse_iso_datetime(generated_at) or datetime.now(timezone.utc)
    events = reconstruct_profile_events_from_activity(active_signals, now_utc=generated_dt)
    if not events:
        return snapshot
    updated = dict(snapshot)
    updated_metrics = dict(metrics)
    updated_metrics["event_reconstructions"] = events
    updated_metrics["event_reconstruction_source"] = "active_signals_fallback"
    updated_metrics["reconstructed_event_count"] = len(events)
    _merge_fallback_reconstructed_period_metrics(updated_metrics, events, generated_at=generated_at)
    updated["metrics"] = updated_metrics
    return updated


def _merge_fallback_reconstructed_period_metrics(metrics: dict[str, Any], events: list[dict[str, Any]], *, generated_at: str) -> None:
    period_metrics = dict(metrics.get("period_metrics") if isinstance(metrics.get("period_metrics"), dict) else {})
    generated_dt = _parse_iso_datetime(generated_at) or datetime.now(timezone.utc)
    periods: dict[str, Any] = {
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "all_time": None,
    }
    for period, delta in periods.items():
        start = generated_dt - delta if delta is not None else None
        filtered = []
        for event in events:
            event_ts = _parse_iso_datetime(event.get("last_signal_at_utc"))
            if start is None or (event_ts is not None and event_ts >= start):
                filtered.append(event)
        closed = [event for event in filtered if event.get("event_pnl_known") and _float(event.get("event_pnl_usd")) is not None]
        wins = [event for event in closed if (_float(event.get("event_pnl_usd")) or 0.0) > 0]
        losses = [event for event in closed if (_float(event.get("event_pnl_usd")) or 0.0) < 0]
        total_pnl = sum(_float(event.get("event_pnl_usd")) or 0.0 for event in closed)
        total_cost = sum(_float(event.get("total_buy_notional_usd")) or 0.0 for event in closed)
        row = dict(period_metrics.get(period) if isinstance(period_metrics.get(period), dict) else {})
        row["reconstructed_event_wins"] = len(wins)
        row["reconstructed_event_losses"] = len(losses)
        row["reconstructed_event_outcomes"] = len(wins) + len(losses)
        row["reconstructed_event_win_rate"] = len(wins) / len(closed) if closed else None
        row["reconstructed_event_return_pct"] = total_pnl / total_cost * 100.0 if total_cost else None
        row["reconstructed_event_profit_usd"] = total_pnl
        period_metrics[period] = row
    metrics["period_metrics"] = period_metrics
    all_closed = [event for event in events if event.get("event_pnl_known") and _float(event.get("event_pnl_usd")) is not None]
    all_wins = [event for event in all_closed if (_float(event.get("event_pnl_usd")) or 0.0) > 0]
    all_losses = [event for event in all_closed if (_float(event.get("event_pnl_usd")) or 0.0) < 0]
    all_cost = sum(_float(event.get("total_buy_notional_usd")) or 0.0 for event in all_closed)
    all_pnl = sum(_float(event.get("event_pnl_usd")) or 0.0 for event in all_closed)
    metrics["reconstructed_closed_wins"] = len(all_wins)
    metrics["reconstructed_closed_losses"] = len(all_losses)
    metrics["reconstructed_closed_win_rate"] = len(all_wins) / len(all_closed) if all_closed else None
    metrics["reconstructed_closed_return_pct"] = all_pnl / all_cost * 100.0 if all_cost else None
    metrics["reconstructed_closed_profit_usd"] = all_pnl


def _upsert_grade(
    conn: sqlite3.Connection,
    profile_key: str,
    snapshot: dict[str, Any],
    grade_payload: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
) -> None:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
    values = _grade_values(profile_key, snapshot, grade_payload, generated_at=generated_at, source_artifact=source_artifact)
    conn.execute(
        """
        INSERT OR IGNORE INTO profile_grade_history (
            profile_key, evaluated_at_utc, source_artifact, grading_policy_version,
            grade, score, grade_rank, polarity, live_route, trading_style,
            trading_style_detail, frequency_class, active_crypto_last_5m,
            active_crypto_most_of_last_hour, crypto_event_count_1h, crypto_event_count_24h,
            crypto_signal_count_1h, daily_pnl_usd, weekly_pnl_usd, monthly_pnl_usd,
            quarterly_pnl_usd, all_pnl_usd, closed_win_rate, closed_return_pct,
            closed_wins, closed_losses, buy_count, sell_count, grade_reasons_json,
            metrics_json, snapshot_json, created_at_utc
        ) VALUES (
            :profile_key, :evaluated_at_utc, :source_artifact, :grading_policy_version,
            :grade, :score, :grade_rank, :polarity, :live_route, :trading_style,
            :trading_style_detail, :frequency_class, :active_crypto_last_5m,
            :active_crypto_most_of_last_hour, :crypto_event_count_1h, :crypto_event_count_24h,
            :crypto_signal_count_1h, :daily_pnl_usd, :weekly_pnl_usd, :monthly_pnl_usd,
            :quarterly_pnl_usd, :all_pnl_usd, :closed_win_rate, :closed_return_pct,
            :closed_wins, :closed_losses, :buy_count, :sell_count, :grade_reasons_json,
            :metrics_json, :snapshot_json, :created_at_utc
        )
        """,
        values,
    )
    conn.execute(
        """
        INSERT INTO profile_grade_current (
            profile_key, evaluated_at_utc, source_artifact, grading_policy_version,
            profile_name, proxy_wallet, grade, score, grade_rank, polarity,
            live_route, trading_style, trading_style_detail, frequency_class,
            active_crypto_last_5m, active_crypto_most_of_last_hour,
            crypto_event_count_1h, crypto_event_count_24h, crypto_signal_count_1h,
            daily_pnl_usd, weekly_pnl_usd, monthly_pnl_usd, quarterly_pnl_usd,
            all_pnl_usd, closed_win_rate, closed_return_pct, closed_wins,
            closed_losses, buy_count, sell_count, grade_reasons_json,
            metrics_json, updated_at_utc
        ) VALUES (
            :profile_key, :evaluated_at_utc, :source_artifact, :grading_policy_version,
            :profile_name, :proxy_wallet, :grade, :score, :grade_rank, :polarity,
            :live_route, :trading_style, :trading_style_detail, :frequency_class,
            :active_crypto_last_5m, :active_crypto_most_of_last_hour,
            :crypto_event_count_1h, :crypto_event_count_24h, :crypto_signal_count_1h,
            :daily_pnl_usd, :weekly_pnl_usd, :monthly_pnl_usd, :quarterly_pnl_usd,
            :all_pnl_usd, :closed_win_rate, :closed_return_pct, :closed_wins,
            :closed_losses, :buy_count, :sell_count, :grade_reasons_json,
            :metrics_json, :created_at_utc
        )
        ON CONFLICT(profile_key) DO UPDATE SET
            evaluated_at_utc=excluded.evaluated_at_utc,
            source_artifact=excluded.source_artifact,
            grading_policy_version=excluded.grading_policy_version,
            profile_name=excluded.profile_name,
            proxy_wallet=excluded.proxy_wallet,
            grade=excluded.grade,
            score=excluded.score,
            grade_rank=excluded.grade_rank,
            polarity=excluded.polarity,
            live_route=excluded.live_route,
            trading_style=excluded.trading_style,
            trading_style_detail=excluded.trading_style_detail,
            frequency_class=excluded.frequency_class,
            active_crypto_last_5m=excluded.active_crypto_last_5m,
            active_crypto_most_of_last_hour=excluded.active_crypto_most_of_last_hour,
            crypto_event_count_1h=excluded.crypto_event_count_1h,
            crypto_event_count_24h=excluded.crypto_event_count_24h,
            crypto_signal_count_1h=excluded.crypto_signal_count_1h,
            daily_pnl_usd=excluded.daily_pnl_usd,
            weekly_pnl_usd=excluded.weekly_pnl_usd,
            monthly_pnl_usd=excluded.monthly_pnl_usd,
            quarterly_pnl_usd=excluded.quarterly_pnl_usd,
            all_pnl_usd=excluded.all_pnl_usd,
            closed_win_rate=excluded.closed_win_rate,
            closed_return_pct=excluded.closed_return_pct,
            closed_wins=excluded.closed_wins,
            closed_losses=excluded.closed_losses,
            buy_count=excluded.buy_count,
            sell_count=excluded.sell_count,
            grade_reasons_json=excluded.grade_reasons_json,
            metrics_json=excluded.metrics_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        values | {"profile_name": _text(profile.get("name")), "proxy_wallet": _text(profile.get("proxy_wallet"))},
    )
    metrics_update = {
        "latest_grade": values["grade"],
        "latest_score": values["score"],
        "latest_trading_style": values["trading_style"],
        "latest_trading_style_detail": values["trading_style_detail"],
        "latest_frequency_class": values["frequency_class"],
        "active_crypto_last_5m": values["active_crypto_last_5m"],
        "active_crypto_most_of_last_hour": values["active_crypto_most_of_last_hour"],
        "latest_activity_utc": _text(metrics.get("latest_activity_utc")),
        "updated_at_utc": _now(),
        "profile_key": profile_key,
    }
    conn.execute(
        """
        UPDATE profile_universe SET
            latest_grade=:latest_grade,
            latest_score=:latest_score,
            latest_trading_style=:latest_trading_style,
            latest_trading_style_detail=:latest_trading_style_detail,
            latest_frequency_class=:latest_frequency_class,
            active_crypto_last_5m=:active_crypto_last_5m,
            active_crypto_most_of_last_hour=:active_crypto_most_of_last_hour,
            latest_activity_utc=:latest_activity_utc,
            updated_at_utc=:updated_at_utc
        WHERE profile_key=:profile_key
        """,
        metrics_update,
    )


def _upsert_raw_activity_rows(
    conn: sqlite3.Connection,
    profile_key: str,
    snapshot: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
    fetch_run_id: str | None = None,
    seen_keys: set[str] | None = None,
) -> int:
    source_rows = snapshot.get("source_rows") if isinstance(snapshot.get("source_rows"), dict) else {}
    count = 0
    for source_type in ("activity", "trades"):
        rows = source_rows.get(source_type) if isinstance(source_rows.get(source_type), list) else []
        for row in rows:
            if isinstance(row, dict) and _upsert_raw_activity_row(
                conn,
                profile_key,
                row,
                source_type=source_type,
                generated_at=generated_at,
                source_artifact=source_artifact,
                fetch_run_id=fetch_run_id,
                seen_keys=seen_keys,
            ):
                count += 1
    return count


def _upsert_active_signal_raw_rows(
    conn: sqlite3.Connection,
    profile_key: str,
    signals: list[dict[str, Any]],
    *,
    generated_at: str,
    source_artifact: str,
    fetch_run_id: str | None = None,
    seen_keys: set[str] | None = None,
) -> int:
    count = 0
    for signal in signals:
        if _upsert_raw_activity_row(
            conn,
            profile_key,
            signal,
            source_type="active_signal",
            generated_at=generated_at,
            source_artifact=source_artifact,
            fetch_run_id=fetch_run_id,
            seen_keys=seen_keys,
        ):
            count += 1
    return count


def _upsert_raw_activity_row(
    conn: sqlite3.Connection,
    profile_key: str,
    row: dict[str, Any],
    *,
    source_type: str,
    generated_at: str,
    source_artifact: str,
    fetch_run_id: str | None = None,
    seen_keys: set[str] | None = None,
) -> bool:
    activity_at = _activity_timestamp(row)
    event_slug = _text(row.get("eventSlug") or row.get("event_slug") or row.get("slug") or row.get("market_slug"))
    condition_id = _text(row.get("conditionId") or row.get("condition_id"))
    order_side = _order_side(row)
    outcome_side = _outcome_side(row)
    price = _float(row.get("price"))
    shares = _float(row.get("size") or row.get("shares"))
    notional = _float(row.get("usdcSize") or row.get("usdc_size") or row.get("notional_usd"))
    if notional is None and price is not None and shares is not None:
        notional = price * shares
    raw_signal = row.get("raw_signal") if isinstance(row.get("raw_signal"), dict) else {}
    tx_hash = _text(
        raw_signal.get("transactionHash")
        or raw_signal.get("transaction_hash")
        or row.get("transactionHash")
        or row.get("transaction_hash")
    )
    raw_key = _raw_activity_key(
        profile_key=profile_key,
        source_type=source_type,
        tx_hash=tx_hash,
        event_slug=event_slug,
        condition_id=condition_id,
        order_side=order_side,
        outcome_side=outcome_side,
        activity_at=activity_at,
        price=price,
        shares=shares,
        row=row,
    )
    if not raw_key:
        return False
    if seen_keys is not None:
        if raw_key in seen_keys:
            return False
        seen_keys.add(raw_key)
    conn.execute(
        """
        INSERT INTO profile_raw_activity (
            raw_activity_key, profile_key, fetch_run_id, source_artifact, source_type,
            observed_at_utc, activity_at_utc, event_slug, condition_id, market_slug,
            symbol, cadence, order_side, outcome_side, token_id, price, shares,
            notional_usd, transaction_hash, raw_json, inserted_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_activity_key) DO UPDATE SET
            fetch_run_id=COALESCE(excluded.fetch_run_id, profile_raw_activity.fetch_run_id),
            source_artifact=excluded.source_artifact,
            raw_json=excluded.raw_json,
            inserted_at_utc=excluded.inserted_at_utc
        """,
        (
            raw_key,
            profile_key,
            fetch_run_id,
            source_artifact,
            source_type,
            generated_at,
            activity_at,
            event_slug,
            condition_id,
            _text(row.get("market_slug")),
            _text(row.get("symbol")),
            _text(row.get("cadence")),
            order_side,
            outcome_side,
            _text(row.get("tokenId") or row.get("token_id") or row.get("asset")),
            price,
            shares,
            notional,
            tx_hash,
            _json(row),
            _now(),
        ),
    )
    return True


def _raw_activity_key(
    *,
    profile_key: str,
    source_type: str,
    tx_hash: str | None,
    event_slug: str | None,
    condition_id: str | None,
    order_side: str | None,
    outcome_side: str | None,
    activity_at: str | None,
    price: float | None,
    shares: float | None,
    row: dict[str, Any],
) -> str:
    if tx_hash:
        return f"raw:{profile_key}:{tx_hash.lower()}"
    parts = [
        profile_key,
        source_type,
        condition_id or event_slug or "",
        order_side or "",
        outcome_side or "",
        activity_at or "",
        "" if price is None else f"{price:.8f}",
        "" if shares is None else f"{shares:.8f}",
        _json(row.get("raw_signal") if isinstance(row.get("raw_signal"), dict) else {}),
    ]
    return "raw:" + _stable_key("|".join(parts))


def _activity_timestamp(row: dict[str, Any]) -> str | None:
    value = row.get("timestamp") or row.get("signal_at_utc") or row.get("createdAt") or row.get("created_at")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) >= 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    parsed = _parse_iso_datetime(value)
    return parsed.isoformat() if parsed else _text(value)


def _order_side(row: dict[str, Any]) -> str | None:
    side = str(row.get("side") or row.get("action_side") or row.get("type") or "").upper()
    return side if side in {"BUY", "SELL"} else None


def _outcome_side(row: dict[str, Any]) -> str | None:
    outcome = str(row.get("outcome") or row.get("effective_outcome") or row.get("raw_outcome") or "").strip().lower()
    if outcome in {"up", "yes"}:
        return "Up"
    if outcome in {"down", "no"}:
        return "Down"
    return None


def _upsert_period_performance(
    conn: sqlite3.Connection,
    profile_key: str,
    snapshot: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
) -> int:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    period_metrics = dict(metrics.get("period_metrics") if isinstance(metrics.get("period_metrics"), dict) else {})
    if "all_time" not in period_metrics:
        closed_wins = int(metrics.get("closed_wins") or 0)
        closed_losses = int(metrics.get("closed_losses") or 0)
        reconstructed_wins = int(metrics.get("reconstructed_closed_wins") or 0)
        reconstructed_losses = int(metrics.get("reconstructed_closed_losses") or 0)
        period_metrics["all_time"] = {
            "pnl_usd": _float(metrics.get("all_pnl_usd")),
            "closed_wins": closed_wins,
            "closed_losses": closed_losses,
            "closed_outcomes": closed_wins + closed_losses,
            "closed_win_rate": _float(metrics.get("closed_win_rate")),
            "closed_return_pct": _float(metrics.get("closed_return_pct")),
            "closed_profit_usd": _float(metrics.get("closed_profit_usd")),
            "reconstructed_event_wins": reconstructed_wins,
            "reconstructed_event_losses": reconstructed_losses,
            "reconstructed_event_outcomes": reconstructed_wins + reconstructed_losses,
            "reconstructed_event_win_rate": _float(metrics.get("reconstructed_closed_win_rate")),
            "reconstructed_event_return_pct": _float(metrics.get("reconstructed_closed_return_pct")),
            "reconstructed_event_profit_usd": _float(metrics.get("reconstructed_closed_profit_usd")),
        }
    conn.execute(
        """
        DELETE FROM profile_period_performance
        WHERE profile_key=?
          AND evaluated_at_utc=?
          AND grading_policy_version=?
          AND COALESCE(source_artifact, '')=COALESCE(?, '')
        """,
        (profile_key, generated_at, PROFILE_GRADING_POLICY_VERSION, source_artifact),
    )
    count = 0
    for period, row in period_metrics.items():
        if not isinstance(row, dict):
            continue
        closed_count = int(row.get("closed_outcomes") or 0)
        reconstructed_count = int(row.get("reconstructed_event_outcomes") or 0)
        if closed_count and reconstructed_count:
            source_quality = "mixed"
        elif reconstructed_count:
            source_quality = "reconstructed"
        elif closed_count:
            source_quality = "raw_closed_positions"
        else:
            source_quality = "pnl_only"
        conn.execute(
            """
            INSERT INTO profile_period_performance (
                profile_key, evaluated_at_utc, source_artifact, grading_policy_version,
                period, pnl_usd, closed_win_rate, closed_return_pct, closed_wins,
                closed_losses, closed_count, reconstructed_event_win_rate,
                reconstructed_event_return_pct, reconstructed_event_wins,
                reconstructed_event_losses, reconstructed_event_count, source_quality,
                metrics_json, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_key,
                generated_at,
                source_artifact,
                PROFILE_GRADING_POLICY_VERSION,
                _text(period),
                _float(row.get("pnl_usd")),
                _float(row.get("closed_win_rate")),
                _float(row.get("closed_return_pct")),
                int(row.get("closed_wins") or 0),
                int(row.get("closed_losses") or 0),
                closed_count,
                _float(row.get("reconstructed_event_win_rate")),
                _float(row.get("reconstructed_event_return_pct")),
                int(row.get("reconstructed_event_wins") or 0),
                int(row.get("reconstructed_event_losses") or 0),
                reconstructed_count,
                source_quality,
                _json(row),
                _now(),
            ),
        )
        count += 1
    return count


def _upsert_event_reconstructions(
    conn: sqlite3.Connection,
    profile_key: str,
    snapshot: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
) -> int:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    events = metrics.get("event_reconstructions") if isinstance(metrics.get("event_reconstructions"), list) else []
    conn.execute(
        """
        DELETE FROM profile_event_reconstructions
        WHERE profile_key=?
          AND evaluated_at_utc=?
          AND COALESCE(source_artifact, '')=COALESCE(?, '')
        """,
        (profile_key, generated_at, source_artifact),
    )
    count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_key = _text(event.get("event_key"))
        if not event_key:
            continue
        conn.execute(
            """
            INSERT INTO profile_event_reconstructions (
                profile_key, evaluated_at_utc, source_artifact, event_key,
                event_slug, first_signal_at_utc, last_signal_at_utc, event_style,
                buy_count, sell_count, up_open_shares, down_open_shares,
                up_cost_basis_usd, down_cost_basis_usd, realized_pnl_usd,
                event_pnl_usd, event_pnl_known, event_effective_win,
                quality_flags_json, summary_json, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_key,
                generated_at,
                source_artifact,
                event_key,
                _text(event.get("event_slug")),
                _text(event.get("first_signal_at_utc")),
                _text(event.get("last_signal_at_utc")),
                _text(event.get("event_style")),
                int(event.get("buy_count") or 0),
                int(event.get("sell_count") or 0),
                _float(event.get("up_open_shares")),
                _float(event.get("down_open_shares")),
                _float(event.get("up_cost_basis_usd")),
                _float(event.get("down_cost_basis_usd")),
                _float(event.get("realized_pnl_usd")),
                _float(event.get("event_pnl_usd")),
                _bool(event.get("event_pnl_known")),
                None if event.get("event_effective_win") is None else _bool(event.get("event_effective_win")),
                _json(event.get("quality_flags") or {}),
                _json(event),
                _now(),
            ),
        )
        count += 1
    return count


def _upsert_signal_generator_scores(
    conn: sqlite3.Connection,
    profile_key: str,
    snapshot: dict[str, Any],
    grade_payload: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
    config: ProfileSignalConfig,
) -> int:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    rows = build_profile_signal_generator_scores(metrics, grade_payload=grade_payload, config=config)
    conn.execute(
        """
        DELETE FROM profile_signal_generator_scores
        WHERE profile_key=?
          AND evaluated_at_utc=?
          AND grading_policy_version=?
          AND COALESCE(source_artifact, '')=COALESCE(?, '')
        """,
        (profile_key, generated_at, PROFILE_GRADING_POLICY_VERSION, source_artifact),
    )
    for row in rows:
        conn.execute(
            """
            INSERT INTO profile_signal_generator_scores (
                profile_key, evaluated_at_utc, source_artifact, grading_policy_version,
                generator_id, account_type, grade, score, status, signal_role,
                can_emit_live, usage_eligible, usage_blockers_json,
                components_json, requirements_json, metrics_json,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_key,
                generated_at,
                source_artifact,
                PROFILE_GRADING_POLICY_VERSION,
                _text(row.get("generator_id")),
                _text(row.get("account_type")),
                _text(grade_payload.get("grade")),
                _float(row.get("score")) or 0.0,
                _text(row.get("status")) or "research_only",
                _text(row.get("signal_role")),
                _bool(row.get("can_emit_live")),
                _bool(row.get("usage_eligible")),
                _json(row.get("usage_blockers") or []),
                _json(row.get("components") or {}),
                _json(row.get("requirements") or []),
                _json(metrics),
                _now(),
            ),
        )
    return len(rows)


def _upsert_bot_daily(
    conn: sqlite3.Connection,
    profile_key: str,
    snapshot: dict[str, Any],
    grade_payload: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
) -> None:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    classification = metrics.get("profile_classification") if isinstance(metrics.get("profile_classification"), dict) else {}
    day = generated_at[:10]
    is_bot = bool(metrics.get("bot_like") and metrics.get("crypto_event_count_24h"))
    conn.execute(
        """
        INSERT INTO profile_bot_daily (
            profile_key, day_utc, is_crypto_bot, trading_style, trading_style_detail,
            frequency_class, crypto_event_count_24h, crypto_signal_count_24h,
            avg_crypto_trades_per_event_24h, buy_count, sell_count,
            active_crypto_most_of_last_hour, daily_pnl_usd, grade, score,
            source_artifact, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_key, day_utc) DO UPDATE SET
            is_crypto_bot=excluded.is_crypto_bot,
            trading_style=excluded.trading_style,
            trading_style_detail=excluded.trading_style_detail,
            frequency_class=excluded.frequency_class,
            crypto_event_count_24h=excluded.crypto_event_count_24h,
            crypto_signal_count_24h=excluded.crypto_signal_count_24h,
            avg_crypto_trades_per_event_24h=excluded.avg_crypto_trades_per_event_24h,
            buy_count=excluded.buy_count,
            sell_count=excluded.sell_count,
            active_crypto_most_of_last_hour=excluded.active_crypto_most_of_last_hour,
            daily_pnl_usd=excluded.daily_pnl_usd,
            grade=excluded.grade,
            score=excluded.score,
            source_artifact=excluded.source_artifact,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            profile_key,
            day,
            _bool(is_bot),
            _text(snapshot.get("trading_style")),
            _text(snapshot.get("trading_style_detail")),
            _text(snapshot.get("frequency_class")),
            int(metrics.get("crypto_event_count_24h") or 0),
            int(classification.get("crypto_signal_count_24h") or 0),
            _float(classification.get("avg_crypto_trades_per_event_24h")),
            int(metrics.get("buy_count") or 0),
            int(metrics.get("sell_count") or 0),
            _bool(metrics.get("active_crypto_most_of_last_hour")),
            _float(metrics.get("daily_pnl_usd")),
            _text(grade_payload.get("grade")),
            _float(grade_payload.get("score")),
            source_artifact,
            _now(),
        ),
    )


def _upsert_signal(conn: sqlite3.Connection, profile_key: str, signal: dict[str, Any], *, source_artifact: str) -> None:
    signal_key = _signal_key(signal)
    conn.execute(
        """
        INSERT INTO profile_signal_events (
            signal_key, profile_key, signal_at_utc, event_slug, condition_id,
            market_slug, symbol, cadence, action_side, raw_outcome,
            effective_outcome, price, size, usdc_size, transaction_hash,
            profile_grade, profile_score, profile_trading_style,
            profile_trading_style_detail, profile_frequency_class,
            source_artifact, raw_json, inserted_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_key) DO UPDATE SET
            profile_grade=excluded.profile_grade,
            profile_score=excluded.profile_score,
            profile_trading_style=excluded.profile_trading_style,
            profile_trading_style_detail=excluded.profile_trading_style_detail,
            profile_frequency_class=excluded.profile_frequency_class,
            source_artifact=excluded.source_artifact,
            raw_json=excluded.raw_json
        """,
        (
            signal_key,
            profile_key,
            _text(signal.get("signal_at_utc")),
            _text(signal.get("event_slug")),
            _text(signal.get("condition_id")),
            _text(signal.get("market_slug")),
            _text(signal.get("symbol")),
            _text(signal.get("cadence")),
            _text(signal.get("action_side")),
            _text(signal.get("raw_outcome")),
            _text(signal.get("effective_outcome")),
            _float(signal.get("price")),
            _float(signal.get("size")),
            _float(signal.get("usdc_size")),
            _text((signal.get("raw_signal") or {}).get("transactionHash") if isinstance(signal.get("raw_signal"), dict) else signal.get("transaction_hash")),
            _text(signal.get("profile_grade")),
            _float(signal.get("profile_score")),
            _text(signal.get("profile_trading_style")),
            _text(signal.get("profile_trading_style_detail")),
            _text(signal.get("profile_frequency_class")),
            source_artifact,
            _json(signal),
            _now(),
        ),
    )


def _grade_payload(snapshot: dict[str, Any], *, config: ProfileSignalConfig) -> dict[str, Any]:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    if metrics:
        return grade_profile(metrics, config=config)
    return {
        "grade": _text(snapshot.get("grade")) or "U",
        "score": _float(snapshot.get("score")) or 0.0,
        "polarity": _text(snapshot.get("polarity")) or "ignore",
        "reasons": list(snapshot.get("grade_reasons") or []),
    }


def _grade_values(
    profile_key: str,
    snapshot: dict[str, Any],
    grade_payload: dict[str, Any],
    *,
    generated_at: str,
    source_artifact: str,
) -> dict[str, Any]:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    grade = _text(grade_payload.get("grade")) or "U"
    style = _text(snapshot.get("trading_style") or metrics.get("trading_style"))
    detail = _text(snapshot.get("trading_style_detail") or metrics.get("trading_style_detail"))
    return {
        "profile_key": profile_key,
        "evaluated_at_utc": generated_at,
        "source_artifact": source_artifact,
        "grading_policy_version": PROFILE_GRADING_POLICY_VERSION,
        "grade": grade,
        "score": _float(grade_payload.get("score")),
        "grade_rank": _grade_rank(grade),
        "polarity": _text(grade_payload.get("polarity")),
        "live_route": _live_route(grade, style, detail),
        "trading_style": style,
        "trading_style_detail": detail,
        "frequency_class": _text(snapshot.get("frequency_class") or metrics.get("frequency_class")),
        "active_crypto_last_5m": _bool(metrics.get("active_crypto_in_last_5m")),
        "active_crypto_most_of_last_hour": _bool(metrics.get("active_crypto_most_of_last_hour")),
        "crypto_event_count_1h": int(metrics.get("crypto_event_count_1h") or 0),
        "crypto_event_count_24h": int(metrics.get("crypto_event_count_24h") or 0),
        "crypto_signal_count_1h": int(metrics.get("crypto_signal_count_1h") or 0),
        "daily_pnl_usd": _float(metrics.get("daily_pnl_usd")),
        "weekly_pnl_usd": _float(metrics.get("weekly_pnl_usd")),
        "monthly_pnl_usd": _float(metrics.get("monthly_pnl_usd")),
        "quarterly_pnl_usd": _float(metrics.get("quarterly_pnl_usd")),
        "all_pnl_usd": _float(metrics.get("all_pnl_usd")),
        "closed_win_rate": _float(metrics.get("closed_win_rate")),
        "closed_return_pct": _float(metrics.get("closed_return_pct")),
        "closed_wins": int(metrics.get("closed_wins") or 0),
        "closed_losses": int(metrics.get("closed_losses") or 0),
        "buy_count": int(metrics.get("buy_count") or 0),
        "sell_count": int(metrics.get("sell_count") or 0),
        "grade_reasons_json": _json(grade_payload.get("reasons") or snapshot.get("grade_reasons") or []),
        "metrics_json": _json(metrics),
        "snapshot_json": _json(snapshot),
        "created_at_utc": _now(),
    }


def _profile_key_from_snapshot(snapshot: dict[str, Any]) -> str:
    profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
    wallet = _text(profile.get("proxy_wallet") or profile.get("proxyWallet"))
    if wallet:
        return f"wallet:{wallet.lower()}"
    ref = snapshot.get("profile_ref") if isinstance(snapshot.get("profile_ref"), dict) else {}
    return _profile_key_from_ref(ref) or f"snapshot:{_stable_key(_json(snapshot))}"


def _profile_key_from_signal(signal: dict[str, Any]) -> str | None:
    wallet = _text(signal.get("proxy_wallet") or signal.get("profile_address"))
    if wallet:
        return f"wallet:{wallet.lower()}"
    name = _text(signal.get("profile_name"))
    if name:
        return f"handle:{name.lower()}"
    return None


def _profile_key_from_ref(ref: dict[str, Any]) -> str | None:
    address = _text(ref.get("address"))
    if address:
        return f"wallet:{address.lower()}"
    handle = _text(ref.get("handle"))
    if handle:
        return f"handle:{handle.lower()}"
    normalized = _text(ref.get("normalized_ref"))
    return normalized


def _sources_from_ref(ref: dict[str, Any]) -> list[str]:
    source = _text(ref.get("source"))
    if not source:
        return []
    return [item.strip() for item in source.split(",") if item.strip()]


def _load_pool_refs(profile_pool_path: str | Path | None) -> list[dict[str, Any]]:
    if not profile_pool_path:
        return []
    return load_active_crypto_profile_pool_refs(profile_pool_path, limit=0, enabled=True)


def _signal_key(signal: dict[str, Any]) -> str:
    raw = signal.get("raw_signal") if isinstance(signal.get("raw_signal"), dict) else {}
    tx = _text(raw.get("transactionHash") or signal.get("transaction_hash"))
    if tx:
        return f"tx:{tx.lower()}"
    parts = [
        _text(signal.get("profile_name")),
        _text(signal.get("proxy_wallet")),
        _text(signal.get("event_slug")),
        _text(signal.get("effective_outcome")),
        _text(signal.get("action_side")),
        _text(signal.get("signal_at_utc")),
        str(_float(signal.get("price"))),
        str(_float(signal.get("size"))),
    ]
    return "sig:" + _stable_key("|".join(parts))


def _live_route(grade: str, style: str | None, detail: str | None) -> str | None:
    if grade not in {"S", "S+", "S++"}:
        return None
    style = (style or "").lower()
    detail = (detail or "").lower()
    if style == "hedger" or detail == "grid_buyer":
        return "hedger"
    if style == "outcome_predictor":
        return "outcome_predictor"
    return None


def _grade_rank(grade: str | None) -> int:
    return {"S++": 30, "S+": 20, "S": 10, "A": 5, "B": 2, "C": 1, "D": 0, "E": -1, "U": -2}.get(str(grade or "").upper(), -3)


def _decode_json_columns(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    for row in rows:
        for column in columns:
            value = row.get(column)
            if not isinstance(value, str):
                continue
            try:
                row[column] = json.loads(value)
            except json.JSONDecodeError:
                row[column] = value
    return rows


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, default=str)


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:32]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _normalize_event_timing_row(row: dict[str, Any]) -> dict[str, Any] | None:
    event_start = _parse_iso_datetime(row.get("event_start_time_utc") or row.get("window_start_time") or row.get("start_time"))
    event_end = _parse_iso_datetime(row.get("event_end_time_utc") or row.get("window_end_time") or row.get("end_time"))
    if event_start is None or event_end is None:
        return None
    event_slug = _text(row.get("event_slug") or row.get("market_slug"))
    condition_id = _text(row.get("condition_id"))
    token_id = _text(row.get("token_id") or row.get("asset_id"))
    if not event_slug and not condition_id and not token_id:
        return None
    return {
        "event_slug": event_slug,
        "condition_id": condition_id,
        "token_id": token_id,
        "outcome_side": _text(row.get("outcome") or row.get("outcome_side") or row.get("raw_outcome")),
        "event_start_time_utc": event_start.isoformat(),
        "event_end_time_utc": event_end.isoformat(),
    }


def _profile_event_timing(row: sqlite3.Row, event: dict[str, Any]) -> dict[str, Any]:
    activity_at = _parse_iso_datetime(row["activity_at_utc"])
    event_start = _parse_iso_datetime(event.get("event_start_time_utc"))
    event_end = _parse_iso_datetime(event.get("event_end_time_utc"))
    seconds_before_start = None
    buying_ahead = 0
    active_during = 0
    if activity_at is not None and event_start is not None:
        seconds_before_start = (event_start - activity_at).total_seconds()
        buying_ahead = 1 if seconds_before_start > 0 and str(row["order_side"] or "").upper() == "BUY" else 0
    if activity_at is not None and event_start is not None and event_end is not None:
        active_during = 1 if event_start <= activity_at <= event_end else 0
    raw_key = str(row["raw_activity_key"])
    return {
        "link_key": _stable_key("|".join(["profile_event_timing", raw_key, str(event.get("event_start_time_utc"))])),
        "profile_key": row["profile_key"],
        "raw_activity_key": raw_key,
        "event_slug": event.get("event_slug") or row["event_slug"],
        "condition_id": event.get("condition_id") or row["condition_id"],
        "token_id": event.get("token_id") or row["token_id"],
        "outcome_side": event.get("outcome_side") or row["outcome_side"],
        "activity_at_utc": row["activity_at_utc"],
        "order_side": row["order_side"],
        "event_start_time_utc": event.get("event_start_time_utc"),
        "event_end_time_utc": event.get("event_end_time_utc"),
        "seconds_before_event_start": seconds_before_start,
        "buying_ahead": buying_ahead,
        "active_during_event": active_during,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _install_sqlite_functions(conn: sqlite3.Connection) -> None:
    conn.create_function("COALESCE_JSON", 2, lambda a, b: a or b)


sqlite3.Connection.create_function  # keep static analyzers aware of sqlite UDF support


def _merge_source_text(existing: str | None, incoming: str | None) -> str | None:
    parts = []
    for value in (existing, incoming):
        for item in str(value or "").split(","):
            item = item.strip()
            if item and item not in parts:
                parts.append(item)
    return ",".join(parts) if parts else None


def _register_functions(conn: sqlite3.Connection) -> None:
    conn.create_function("_merge_json_text", 2, _merge_source_text)


# Registering functions has to happen after connect but before schema SQL uses
# ON CONFLICT expressions that call the function.
_original_connect_profile_store = connect_profile_store


def connect_profile_store(db_path: str | Path | None = None) -> sqlite3.Connection:  # type: ignore[no-redef]
    conn = _original_connect_profile_store(db_path)
    _register_functions(conn)
    return conn


__all__ = [
    "PROFILE_GRADING_POLICY_VERSION",
    "PROFILE_STORE_SCHEMA_VERSION",
    "connect_profile_store",
    "default_profile_store_path",
    "ingest_profile_signal_artifact",
    "ingest_profile_signal_report",
    "initialize_profile_store",
    "profile_store_fresh_event_reconstructions",
    "profile_store_fresh_generator_scores",
    "profile_store_fresh_period_performance",
    "profile_store_fresh_profiles",
    "profile_store_buying_ahead_profiles",
    "profile_store_summary",
    "record_profile_fetch_run",
    "refresh_profile_event_timing_links",
]
