from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect
from crypto_options_app.db.errors import is_database_full_error
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.signals.validation.models import (
    SignalCandidateSpec,
    SignalObservation,
    SignalQueueItem,
    SignalValidationPhaseResult,
    model_to_dict,
)
from crypto_options_app.signals.validation.registry import list_signal_specs


PHASE_ORDER = ("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test")
ACTIVE_QUEUE_STATES = ("OWNED", "RUNNING")
CLAIMABLE_QUEUE_STATES = ("QUEUED", "FAILED", "BLOCKED")
TERMINAL_QUEUE_STATES = ("PASSED", "RETIRED", "PROMOTED")
PROMOTION_MIN_SAMPLE_COUNT = 90
PROMOTION_MIN_DISTINCT_EVENTS = 20
PROMOTION_MIN_HIT_RATE_BY_IMPACT = {
    "critical": 0.62,
    "high": 0.58,
    "medium": 0.55,
    "low": 0.52,
}


def sync_signal_catalog(conn: Any, *, enqueue: bool = True, now_utc: datetime | None = None) -> dict[str, int]:
    create_schema(conn)
    now = _as_utc(now_utc or datetime.now(UTC)).isoformat()
    spec_count = 0
    version_count = 0
    queue_count = 0
    for spec in list_signal_specs():
        _upsert_signal_spec(conn, spec, now)
        spec_count += 1
        _upsert_signal_version(conn, spec, now)
        version_count += 1
        if enqueue:
            before = conn.total_changes
            _ensure_queue_item(conn, spec, PHASE_ORDER[0], now)
            if conn.total_changes > before:
                queue_count += 1
    return {
        "signal_specs": spec_count,
        "signal_versions": version_count,
        "queue_items_inserted": queue_count,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def sync_signal_catalog_db(db_path: str | Path | None = None, *, enqueue: bool = True) -> dict[str, int]:
    path = initialize_schema(db_path)
    with connect(path) as conn:
        return sync_signal_catalog(conn, enqueue=enqueue)


def expire_stale_owned_queue_items(conn: Any, *, now_utc: datetime | None = None) -> int:
    now = _as_utc(now_utc or datetime.now(UTC)).isoformat()
    before = conn.total_changes
    conn.execute(
        """
        UPDATE signal_queue_items
           SET status='QUEUED',
               owner_id=NULL,
               owned_at_utc=NULL,
               owner_expires_at_utc=NULL,
               updated_at_utc=?
         WHERE status IN ('OWNED', 'RUNNING')
           AND owner_expires_at_utc IS NOT NULL
           AND owner_expires_at_utc < ?
        """,
        (now, now),
    )
    return conn.total_changes - before


def claim_next_queue_item(
    conn: Any,
    *,
    owner_id: str,
    ttl_minutes: int = 10,
    now_utc: datetime | None = None,
) -> SignalQueueItem | None:
    now = _as_utc(now_utc or datetime.now(UTC))
    expire_stale_owned_queue_items(conn, now_utc=now)
    row = conn.execute(
        """
        SELECT q.*
          FROM signal_queue_items q
          JOIN signal_specs s
            ON s.signal_id = q.signal_id
         WHERE q.status IN ('QUEUED', 'FAILED', 'BLOCKED')
         ORDER BY
              CASE q.phase
                WHEN 'last_week_backtest' THEN 0
                WHEN 'last_month_backtest' THEN 1
                WHEN 'random_sampling_backtest' THEN 2
                WHEN 'live_shadow_test' THEN 3
                ELSE 9
              END,
              CASE s.impact_if_degraded
                WHEN 'critical' THEN 0
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                ELSE 3
              END,
              q.priority ASC,
              q.attempt_count ASC,
              q.inserted_at_utc ASC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    expires = now + timedelta(minutes=max(1, int(ttl_minutes)))
    conn.execute(
        """
        UPDATE signal_queue_items
           SET status='OWNED',
               owner_id=?,
               owned_at_utc=?,
               owner_expires_at_utc=?,
               attempt_count=attempt_count + 1,
               updated_at_utc=?
         WHERE queue_item_key=?
           AND status IN ('QUEUED', 'FAILED', 'BLOCKED')
        """,
        (owner_id, now.isoformat(), expires.isoformat(), now.isoformat(), row["queue_item_key"]),
    )
    claimed = conn.execute(
        "SELECT * FROM signal_queue_items WHERE queue_item_key=?",
        (row["queue_item_key"],),
    ).fetchone()
    return _queue_item_from_row(claimed) if claimed else None


def mark_queue_item_running(conn: Any, *, queue_item_key: str, now_utc: datetime | None = None) -> None:
    now = _as_utc(now_utc or datetime.now(UTC)).isoformat()
    conn.execute(
        "UPDATE signal_queue_items SET status='RUNNING', updated_at_utc=? WHERE queue_item_key=?",
        (now, queue_item_key),
    )


def start_validation_run(
    conn: Any,
    *,
    queue_item: SignalQueueItem,
    owner_id: str,
    now_utc: datetime | None = None,
) -> str:
    now = _as_utc(now_utc or datetime.now(UTC)).isoformat()
    run_key = _stable_key("signal_validation_run", queue_item.queue_item_key, owner_id, now)
    conn.execute(
        """
        INSERT INTO signal_validation_runs(
            validation_run_key, queue_item_key, signal_id, version, phase,
            owner_id, started_at_utc, status, summary_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'running', '{}', ?)
        """,
        (
            run_key,
            queue_item.queue_item_key,
            queue_item.signal_id,
            queue_item.version,
            queue_item.phase,
            owner_id,
            now,
            now,
        ),
    )
    mark_queue_item_running(conn, queue_item_key=queue_item.queue_item_key, now_utc=now_utc)
    return run_key


def finish_validation_run(
    conn: Any,
    *,
    validation_run_key: str,
    phase_result: SignalValidationPhaseResult,
    observations: tuple[SignalObservation, ...] = (),
    now_utc: datetime | None = None,
) -> None:
    now = _as_utc(now_utc or datetime.now(UTC)).isoformat()
    run = conn.execute(
        "SELECT * FROM signal_validation_runs WHERE validation_run_key=?",
        (validation_run_key,),
    ).fetchone()
    queue_item_key = run["queue_item_key"] if run else None
    version = run["version"] if run else "v1"
    _insert_phase_result(
        conn,
        validation_run_key=validation_run_key,
        queue_item_key=queue_item_key,
        version=version,
        result=phase_result,
        now=now,
    )
    for observation in observations:
        _insert_observation(conn, validation_run_key=validation_run_key, observation=observation, now=now)
    summary = {
        "sample_count": phase_result.sample_count,
        "hit_rate": phase_result.hit_rate,
        "average_forward_return": phase_result.average_forward_return,
        "blockers": list(phase_result.blockers),
        "observation_count": len(observations),
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
    conn.execute(
        """
        UPDATE signal_validation_runs
           SET status=?, completed_at_utc=?, summary_json=?
         WHERE validation_run_key=?
        """,
        (phase_result.status, now, json.dumps(summary, sort_keys=True, default=str), validation_run_key),
    )
    if queue_item_key:
        _advance_queue_after_result(conn, queue_item_key=queue_item_key, result=phase_result, now=now)


def validation_status(
    conn: Any,
    *,
    include_signals: bool = True,
    signal_limit: int | None = None,
) -> dict[str, Any]:
    _create_schema_if_missing(conn)
    generated_at_utc = datetime.now(UTC).isoformat()
    spec_rows = {
        str(row["signal_id"]): dict(row)
        for row in conn.execute(
            """
            SELECT signal_id, signal_type, sources_json, required_data_blocks_json
              FROM signal_specs
            """
        ).fetchall()
    }
    raw_rows = [
        _decode_status_row(dict(row), spec_rows.get(str(row["signal_id"])))
        for row in conn.execute(
            """
            SELECT *
              FROM v_crypto_options_app_signal_validation_status
             ORDER BY
                CASE impact_if_degraded
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                signal_type,
                signal_id
            """
        ).fetchall()
    ]
    rows = _current_signal_status_rows(raw_rows)
    display_rows = rows
    if signal_limit is not None:
        display_rows = display_rows[: max(1, int(signal_limit))]
    if include_signals:
        strict_reviews = _strict_promotion_reviews(conn, display_rows)
    else:
        strict_reviews = {}
    for row in display_rows:
        key = (str(row.get("signal_id") or ""), str(row.get("version") or "v1"))
        row.update(strict_reviews.get(key, _strict_promotion_review_from_rows(row, {}, _empty_diversity_summary(), {})))
    by_status: dict[str, int] = {}
    by_promotion_state: dict[str, int] = {}
    for row in rows:
        status = str(row.get("queue_status") or "missing")
        by_status[status] = by_status.get(status, 0) + 1
        promotion_state = str(row.get("promotion_state") or "UNKNOWN")
        by_promotion_state[promotion_state] = by_promotion_state.get(promotion_state, 0) + 1
    review_queue = _review_request_summary(conn)
    integrity = _signal_history_integrity_summary(conn)
    return {
        "schema_version": "crypto_options_signal_validation_status_v1",
        "generated_at_utc": generated_at_utc,
        "signal_count": len(rows),
        "raw_queue_row_count": len(raw_rows),
        "by_queue_status": by_status,
        "by_promotion_state": by_promotion_state,
        "review_queue": review_queue,
        "integrity": integrity,
        "signals_included": bool(include_signals),
        "signal_limit": signal_limit,
        "returned_signal_count": len(display_rows) if include_signals else 0,
        "signals": display_rows if include_signals else [],
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def queue_status(conn: Any) -> dict[str, Any]:
    _create_schema_if_missing(conn)
    rows = [dict(row) for row in conn.execute("SELECT * FROM signal_queue_items ORDER BY priority, inserted_at_utc").fetchall()]
    return {
        "schema_version": "crypto_options_signal_validation_queue_v1",
        "queue_count": len(rows),
        "queue": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def validation_results(conn: Any, *, limit: int = 100) -> dict[str, Any]:
    _create_schema_if_missing(conn)
    rows = [
        _decode_result_row(dict(row))
        for row in conn.execute(
            """
            SELECT *
              FROM signal_validation_results
             ORDER BY evaluated_at_utc DESC
             LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    ]
    for row in rows:
        row["diversity"] = _observation_diversity_summary(
            conn,
            signal_id=str(row.get("signal_id") or ""),
            version=str(row.get("version") or "v1"),
            phase=str(row.get("phase") or ""),
        )
    return {
        "schema_version": "crypto_options_signal_validation_results_v1",
        "result_count": len(rows),
        "results": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def create_review_request(conn: Any, *, message: str, requested_by: str = "dashboard") -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    payload = {
        "message": str(message).strip(),
        "requested_by": requested_by,
        "status": "pending",
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
    if not payload["message"]:
        raise ValueError("message_required")
    key = _stable_key("signal_review_request", requested_by, now, payload["message"])
    conn.execute(
        """
        INSERT INTO signal_artifacts(signal_artifact_key, artifact_type, generated_at_utc, artifact_json, inserted_at_utc)
        VALUES (?, 'review_request', ?, ?, ?)
        """,
        (key, now, json.dumps(payload, sort_keys=True, default=str), now),
    )
    return {"signal_artifact_key": key, "generated_at_utc": now, **payload}


def _create_schema_if_missing(conn: Any) -> None:
    """Avoid taking a write lock on read-only status paths after bootstrap."""

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_specs'"
    ).fetchone()
    if row is None:
        create_schema(conn)


def _upsert_signal_spec(conn: Any, spec: SignalCandidateSpec, now: str) -> None:
    payload = model_to_dict(spec)
    conn.execute(
        """
        INSERT INTO signal_specs(
            signal_id, family, signal_type, sources_json, variant, version,
            filename, purpose, event_phase_relevance, refresh_rate_seconds,
            time_frames_relevant_json, required_data_blocks_json,
            validation_target, win_criteria, sample_unit, impact_if_degraded,
            description, signal_payload_example_json, spec_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id) DO UPDATE SET
            family=excluded.family,
            signal_type=excluded.signal_type,
            sources_json=excluded.sources_json,
            variant=excluded.variant,
            version=excluded.version,
            filename=excluded.filename,
            purpose=excluded.purpose,
            event_phase_relevance=excluded.event_phase_relevance,
            refresh_rate_seconds=excluded.refresh_rate_seconds,
            time_frames_relevant_json=excluded.time_frames_relevant_json,
            required_data_blocks_json=excluded.required_data_blocks_json,
            validation_target=excluded.validation_target,
            win_criteria=excluded.win_criteria,
            sample_unit=excluded.sample_unit,
            impact_if_degraded=excluded.impact_if_degraded,
            description=excluded.description,
            signal_payload_example_json=excluded.signal_payload_example_json,
            spec_json=excluded.spec_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            spec.signal_id,
            spec.family,
            spec.signal_type,
            json.dumps(list(spec.sources), sort_keys=True),
            spec.variant,
            spec.version,
            spec.filename,
            spec.purpose,
            spec.event_phase_relevance,
            spec.refresh_rate_seconds,
            json.dumps(list(spec.time_frames_relevant), sort_keys=True),
            json.dumps(list(spec.required_data_blocks), sort_keys=True),
            spec.validation_target,
            spec.win_criteria,
            spec.sample_unit,
            spec.impact_if_degraded,
            spec.description,
            json.dumps(spec.signal_payload_example, sort_keys=True, default=str),
            json.dumps(payload, sort_keys=True, default=str),
            now,
            now,
        ),
    )


def _upsert_signal_version(conn: Any, spec: SignalCandidateSpec, now: str) -> None:
    key = _stable_key("signal_version", spec.signal_id, spec.version)
    conn.execute(
        """
        INSERT INTO signal_versions(
            signal_version_key, signal_id, version, status, parent_signal_id,
            supersedes_signal_id, metadata_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, 'active', ?, ?, '{}', ?, ?)
        ON CONFLICT(signal_id, version) DO UPDATE SET
            status=excluded.status,
            parent_signal_id=excluded.parent_signal_id,
            supersedes_signal_id=excluded.supersedes_signal_id,
            updated_at_utc=excluded.updated_at_utc
        """,
        (key, spec.signal_id, spec.version, spec.parent_signal_id, spec.supersedes_signal_id, now, now),
    )


def _ensure_queue_item(conn: Any, spec: SignalCandidateSpec, phase: str, now: str) -> None:
    key = _stable_key("signal_queue", spec.signal_id, spec.version, phase)
    priority = {"critical": 0, "high": 25, "medium": 50, "low": 75}.get(spec.impact_if_degraded, 100)
    conn.execute(
        """
        INSERT INTO signal_queue_items(
            queue_item_key, signal_id, version, phase, status, priority,
            parent_signal_id, supersedes_signal_id, queue_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, '{}', ?, ?)
        ON CONFLICT(queue_item_key) DO NOTHING
        """,
        (
            key,
            spec.signal_id,
            spec.version,
            phase,
            priority,
            spec.parent_signal_id,
            spec.supersedes_signal_id,
            now,
            now,
        ),
    )


def _insert_phase_result(
    conn: Any,
    *,
    validation_run_key: str,
    queue_item_key: str | None,
    version: str,
    result: SignalValidationPhaseResult,
    now: str,
) -> None:
    key = _stable_key("signal_validation_result", validation_run_key, result.signal_id, result.phase, now)
    conn.execute(
        """
        INSERT INTO signal_validation_results(
            validation_result_key, validation_run_key, queue_item_key, signal_id,
            version, phase, status, evaluated_at_utc, sample_count, hit_rate,
            average_forward_return, blockers_json, metrics_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            validation_run_key,
            queue_item_key,
            result.signal_id,
            version,
            result.phase,
            result.status,
            result.evaluated_at_utc or now,
            int(result.sample_count),
            result.hit_rate,
            result.average_forward_return,
            json.dumps(list(result.blockers), sort_keys=True, default=str),
            json.dumps(result.metrics, sort_keys=True, default=str),
            now,
        ),
    )


def _insert_observation(conn: Any, *, validation_run_key: str, observation: SignalObservation, now: str) -> None:
    key = _stable_key("signal_observation", validation_run_key, observation.signal_id, observation.event_token_key, observation.decision_at_utc)
    conn.execute(
        """
        INSERT INTO signal_observations(
            observation_key, validation_run_key, signal_id, version, phase,
            event_key, event_token_key, decision_at_utc, emitted_signal,
            observed_value, expected_direction, outcome_direction, hit,
            payload_json, blockers_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            validation_run_key,
            observation.signal_id,
            observation.version,
            observation.phase,
            observation.event_key,
            observation.event_token_key,
            observation.decision_at_utc,
            1 if observation.emitted_signal else 0,
            observation.observed_value,
            observation.expected_direction,
            observation.outcome_direction,
            None if observation.hit is None else 1 if observation.hit else 0,
            json.dumps(observation.payload, sort_keys=True, default=str),
            json.dumps(list(observation.blockers), sort_keys=True, default=str),
            now,
        ),
    )


def _advance_queue_after_result(conn: Any, *, queue_item_key: str, result: SignalValidationPhaseResult, now: str) -> None:
    queue_row = conn.execute("SELECT * FROM signal_queue_items WHERE queue_item_key=?", (queue_item_key,)).fetchone()
    if queue_row is None:
        return
    next_status = {
        "passed": "PASSED",
        "failed": "FAILED",
        "blocked": "BLOCKED",
        "running": "RUNNING",
        "not_started": "QUEUED",
    }.get(result.status, "BLOCKED")
    conn.execute(
        """
        UPDATE signal_queue_items
           SET status=?,
               owner_id=NULL,
               owned_at_utc=NULL,
               owner_expires_at_utc=NULL,
               last_error=?,
               updated_at_utc=?
         WHERE queue_item_key=?
        """,
        (
            next_status,
            ";".join(result.blockers) if result.blockers else None,
            now,
            queue_item_key,
        ),
    )
    if result.status == "passed":
        next_phase = _next_phase(str(queue_row["phase"]))
        if next_phase is not None:
            spec = conn.execute("SELECT * FROM signal_specs WHERE signal_id=?", (queue_row["signal_id"],)).fetchone()
            priority = int(queue_row["priority"])
            key = _stable_key("signal_queue", queue_row["signal_id"], queue_row["version"], next_phase)
            conn.execute(
                """
                INSERT INTO signal_queue_items(
                    queue_item_key, signal_id, version, phase, status, priority,
                    queue_json, inserted_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)
                ON CONFLICT(queue_item_key) DO NOTHING
                """,
                (
                    key,
                    queue_row["signal_id"],
                    queue_row["version"],
                    next_phase,
                    priority,
                    json.dumps({"queued_after_phase": queue_row["phase"], "impact_if_degraded": spec["impact_if_degraded"] if spec else None}, sort_keys=True),
                    now,
                    now,
                ),
            )


def _next_phase(phase: str) -> str | None:
    try:
        index = PHASE_ORDER.index(phase)
    except ValueError:
        return None
    next_index = index + 1
    if next_index >= len(PHASE_ORDER):
        return None
    return PHASE_ORDER[next_index]


def _queue_item_from_row(row: Any) -> SignalQueueItem:
    return SignalQueueItem(
        queue_item_key=row["queue_item_key"],
        signal_id=row["signal_id"],
        version=row["version"],
        phase=row["phase"],
        status=row["status"],
        priority=int(row["priority"]),
        owner_id=row["owner_id"],
        owned_at_utc=row["owned_at_utc"],
        owner_expires_at_utc=row["owner_expires_at_utc"],
        attempt_count=int(row["attempt_count"]),
        parent_signal_id=row["parent_signal_id"],
        supersedes_signal_id=row["supersedes_signal_id"],
        last_error=row["last_error"],
    )


def _decode_status_row(row: dict[str, Any], spec_row: dict[str, Any] | None = None) -> dict[str, Any]:
    row["latest_blockers"] = _json_load(row.pop("latest_blockers_json", None), [])
    sources = _json_load(spec_row.get("sources_json"), []) if spec_row else []
    required_data_blocks = _json_load(spec_row.get("required_data_blocks_json"), []) if spec_row else []
    row["type"] = row.get("signal_type")
    row["phase"] = row.get("latest_result_phase") or row.get("queue_phase")
    row["sources"] = sources
    row["source_blocks"] = required_data_blocks
    return row


def _decode_result_row(row: dict[str, Any]) -> dict[str, Any]:
    row["blockers"] = _json_load(row.pop("blockers_json", None), [])
    row["metrics"] = _json_load(row.pop("metrics_json", None), {})
    return row


def _current_signal_status_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        signal_id = str(row.get("signal_id") or "")
        if not signal_id:
            continue
        if signal_id not in selected or _status_selection_key(row) < _status_selection_key(selected[signal_id]):
            selected[signal_id] = row
    return list(selected.values())


def _strict_promotion_review(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Return the stricter strategy-promotion state for a structurally passed signal.

    Queue status remains a worker/lifecycle state. This review state is the one
    strategy design should trust before using a signal as a live component.
    """

    signal_id = str(row.get("signal_id") or "")
    version = str(row.get("version") or "v1")
    return _strict_promotion_review_from_rows(
        row,
        _latest_phase_results(conn, signal_id=signal_id, version=version),
        _observation_diversity_summary(conn, signal_id=signal_id, version=version),
        {
            phase: _observation_diversity_summary(conn, signal_id=signal_id, version=version, phase=phase)
            for phase in PHASE_ORDER
        },
    )


def _strict_promotion_reviews(conn: Any, rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Bulk strict-promotion review for the dashboard/status path."""

    keys = {
        (str(row.get("signal_id") or ""), str(row.get("version") or "v1"))
        for row in rows
        if row.get("signal_id")
    }
    if not keys:
        return {}

    phase_results: dict[tuple[str, str], dict[str, dict[str, Any]]] = {key: {} for key in keys}
    signal_ids = sorted({signal_id for signal_id, _version in keys})
    placeholders = ",".join("?" for _ in signal_ids)
    for raw in conn.execute(
        f"""
        SELECT signal_id, version, phase, status, evaluated_at_utc, sample_count,
               hit_rate, average_forward_return, blockers_json, metrics_json
          FROM signal_validation_results
         WHERE signal_id IN ({placeholders})
         ORDER BY evaluated_at_utc DESC
        """,
        tuple(signal_ids),
    ).fetchall():
        result = dict(raw)
        key = (str(result.get("signal_id") or ""), str(result.get("version") or "v1"))
        phase = str(result.get("phase") or "")
        if key in phase_results and phase in PHASE_ORDER and phase not in phase_results[key]:
            phase_results[key][phase] = result

    diversity_all = _bulk_observation_diversity_summary(conn, keys)
    diversity_by_phase = _bulk_observation_diversity_summary(conn, keys, by_phase=True)
    return {
        key: _strict_promotion_review_from_rows(
            row,
            phase_results.get(key, {}),
            diversity_all.get(key, _empty_diversity_summary()),
            {
                phase: diversity_by_phase.get((*key, phase), _empty_diversity_summary())
                for phase in PHASE_ORDER
            },
        )
        for row in rows
        for key in [(str(row.get("signal_id") or ""), str(row.get("version") or "v1"))]
        if key in keys
    }


def _strict_promotion_review_from_rows(
    row: dict[str, Any],
    phase_rows: dict[str, dict[str, Any]],
    diversity_summary: dict[str, Any],
    diversity_by_phase: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    signal_id = str(row.get("signal_id") or "")
    version = str(row.get("version") or "v1")
    impact = str(row.get("impact_if_degraded") or "medium")
    requires_positive_forward_return = _requires_positive_forward_return(row)
    min_hit_rate = PROMOTION_MIN_HIT_RATE_BY_IMPACT.get(impact, PROMOTION_MIN_HIT_RATE_BY_IMPACT["medium"])
    promotion_reasons: list[str] = []
    v2_reasons: list[str] = []
    strict_replay_reasons: list[str] = []
    phase_hit_rates: dict[str, float | None] = {}
    phase_samples: dict[str, int] = {}
    phase_forward_returns: dict[str, float | None] = {}
    phase_sources: dict[str, dict[str, int]] = {}

    for phase in PHASE_ORDER:
        result = phase_rows.get(phase)
        if result is None:
            promotion_reasons.append(f"{phase}_missing")
            continue
        if result["status"] != "passed":
            promotion_reasons.append(f"{phase}_not_passed")
        sample_count = int(result["sample_count"] or 0)
        hit_rate = result["hit_rate"]
        average_forward_return = result["average_forward_return"]
        blockers = _json_load(result["blockers_json"], [])
        metrics = _json_load(result["metrics_json"], {})
        frame_sources = metrics.get("frame_sources") if isinstance(metrics, dict) else {}
        if not isinstance(frame_sources, dict):
            frame_sources = {}
        phase_hit_rates[phase] = None if hit_rate is None else float(hit_rate)
        phase_samples[phase] = sample_count
        phase_forward_returns[phase] = None if average_forward_return is None else float(average_forward_return)
        phase_sources[phase] = {str(key): int(value) for key, value in frame_sources.items()}
        if sample_count < PROMOTION_MIN_SAMPLE_COUNT:
            strict_replay_reasons.append(f"{phase}_sample_count_below_{PROMOTION_MIN_SAMPLE_COUNT}")
        if blockers:
            promotion_reasons.extend(f"{phase}_blocker_{blocker}" for blocker in blockers)
        if bool(metrics.get("structural_validation_only")):
            strict_replay_reasons.append(f"{phase}_structural_validation_only")
        if "abc_tables_synthetic" in frame_sources:
            strict_replay_reasons.append(f"{phase}_uses_abc_tables_synthetic")
        if hit_rate is None:
            v2_reasons.append(f"{phase}_hit_rate_missing")
        elif float(hit_rate) < min_hit_rate:
            v2_reasons.append(f"{phase}_hit_rate_below_{min_hit_rate:.2f}")
        elif float(hit_rate) >= 0.98 and bool(metrics.get("structural_validation_only")):
            strict_replay_reasons.append(f"{phase}_near_perfect_structural_hit_rate_needs_baseline")
        if requires_positive_forward_return and average_forward_return is not None and float(average_forward_return) < 0:
            v2_reasons.append(f"{phase}_negative_average_forward_return")

    distinct_event_count = int(diversity_summary["distinct_event_count"])
    distinct_token_count = int(diversity_summary["distinct_token_count"])
    if distinct_event_count < PROMOTION_MIN_DISTINCT_EVENTS:
        strict_replay_reasons.append(f"distinct_event_count_below_{PROMOTION_MIN_DISTINCT_EVENTS}")

    if promotion_reasons:
        promotion_state = "INCOMPLETE"
    elif v2_reasons:
        promotion_state = "NEEDS_V2_REVIEW"
    elif strict_replay_reasons:
        promotion_state = "STRUCTURAL_PASS"
    else:
        promotion_state = "PROMOTION_READY"

    review_reasons = tuple(dict.fromkeys([*promotion_reasons, *v2_reasons, *strict_replay_reasons]))
    latest_phase = str(row.get("queue_phase") or "")
    latest_phase_diversity = (
        diversity_by_phase.get(latest_phase, _empty_diversity_summary())
        if latest_phase in phase_rows
        else _empty_diversity_summary()
    )
    live_shadow_diversity = (
        diversity_by_phase.get("live_shadow_test", _empty_diversity_summary())
        if "live_shadow_test" in phase_rows
        else _empty_diversity_summary()
    )
    return {
        "promotion_state": promotion_state,
        "promotion_ready": promotion_state == "PROMOTION_READY",
        "needs_stricter_variant": bool(v2_reasons),
        "needs_strict_replay": bool(strict_replay_reasons),
        "strict_review_reasons": list(review_reasons),
        "promotion_min_hit_rate": min_hit_rate,
        "promotion_min_sample_count": PROMOTION_MIN_SAMPLE_COUNT,
        "promotion_min_distinct_events": PROMOTION_MIN_DISTINCT_EVENTS,
        "distinct_event_count": distinct_event_count,
        "distinct_token_count": distinct_token_count,
        "distinct_symbol_count": int(diversity_summary["distinct_symbol_count"]),
        "distinct_window_count": int(diversity_summary["distinct_window_count"]),
        "observation_decision_span": diversity_summary["decision_span"],
        "phase_hit_rates": phase_hit_rates,
        "phase_sample_counts": phase_samples,
        "phase_average_forward_returns": phase_forward_returns,
        "requires_positive_forward_return": requires_positive_forward_return,
        "phase_frame_sources": phase_sources,
        "latest_phase_hit_rate": phase_hit_rates.get(latest_phase),
        "latest_phase_sample_count": phase_samples.get(latest_phase),
        "latest_phase_average_forward_return": phase_forward_returns.get(latest_phase),
        "latest_phase_frame_sources": phase_sources.get(latest_phase, {}),
        "latest_phase_diversity": latest_phase_diversity,
        "live_shadow_hit_rate": phase_hit_rates.get("live_shadow_test"),
        "live_shadow_sample_count": phase_samples.get("live_shadow_test"),
        "live_shadow_average_forward_return": phase_forward_returns.get("live_shadow_test"),
        "live_shadow_frame_sources": phase_sources.get("live_shadow_test", {}),
        "live_shadow_diversity": live_shadow_diversity,
        "phase_diversity": {
            phase: diversity_by_phase.get(phase, _empty_diversity_summary())
            for phase in PHASE_ORDER
            if phase in phase_rows
        },
    }


def _requires_positive_forward_return(row: dict[str, Any]) -> bool:
    """Return whether strict review should treat forward return as a promotion gate.

    Outcome and entry/exit signals must justify themselves with positive
    forward-return evidence. Non-directional reference, freshness, liquidity,
    and strategy-parameter signals are useful building blocks even when they do
    not directly predict the next price path. Those still need samples,
    diversity, and hit-rate evidence, but they should not be blocked solely for
    negative directional return.
    """

    signal_type = str(row.get("signal_type") or row.get("type") or "").strip()
    purpose = str(row.get("purpose") or "").strip()
    variant = str(row.get("variant") or "").lower()
    if purpose == "price_reference":
        return False
    if signal_type in {
        "grid_spacing",
        "grid_count",
        "grid_type",
        "liquidity_depth",
        "latency_quality",
        "stale_order_review",
        "support_resistance",
    }:
        return False
    if signal_type == "hedge_ratio" and purpose in {"refreshing_stat", "strategy_parameter"}:
        return False
    if purpose == "logic_gate_trigger":
        return True
    if any(
        marker in variant
        for marker in (
            "reference",
            "freshness",
            "latency",
            "depth",
            "spacing",
            "range",
            "guard",
            "selector",
            "avoid",
            "non_directional",
            "distribution_delta",
            "reconstructed_profile_price",
        )
    ):
        return False
    return True


def _latest_phase_results(conn: Any, *, signal_id: str, version: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT *
          FROM signal_validation_results
         WHERE signal_id=?
           AND version=?
         ORDER BY evaluated_at_utc DESC
        """,
        (signal_id, version),
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        phase = str(row.get("phase") or "")
        if phase in PHASE_ORDER and phase not in selected:
            selected[phase] = row
    return selected


def _distinct_observation_count(conn: Any, *, signal_id: str, version: str, column: str) -> int:
    if column not in {"event_key", "event_token_key"}:
        raise ValueError("unsupported_distinct_observation_column")
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT {column}) AS count
          FROM signal_observations
         WHERE signal_id=?
           AND version=?
           AND {column} IS NOT NULL
           AND {column} != ''
        """,
        (signal_id, version),
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def _observation_diversity_summary(
    conn: Any,
    *,
    signal_id: str,
    version: str,
    phase: str | None = None,
) -> dict[str, Any]:
    where_clauses = ["so.signal_id=?", "so.version=?"]
    params: list[Any] = [signal_id, version]
    if phase:
        where_clauses.append("so.phase=?")
        params.append(phase)
    row = conn.execute(
        f"""
        SELECT
            COUNT(DISTINCT CASE WHEN so.event_key IS NOT NULL AND so.event_key != '' THEN so.event_key END) AS distinct_event_count,
            COUNT(DISTINCT CASE WHEN so.event_token_key IS NOT NULL AND so.event_token_key != '' THEN so.event_token_key END) AS distinct_token_count,
            COUNT(DISTINCT CASE
                WHEN et.symbol IS NOT NULL AND et.symbol != '' THEN et.symbol
                WHEN ev.symbol IS NOT NULL AND ev.symbol != '' THEN ev.symbol
            END) AS distinct_symbol_count,
            COUNT(DISTINCT COALESCE(
                ev.event_start_time_utc,
                json_extract(ev.source_json, '$.window_start_time'),
                substr(so.decision_at_utc, 1, 16)
            )) AS distinct_window_count,
            MIN(so.decision_at_utc) AS earliest_decision_at_utc,
            MAX(so.decision_at_utc) AS latest_decision_at_utc
          FROM signal_observations so
          LEFT JOIN event_tokens et
            ON et.event_token_key = so.event_token_key
          LEFT JOIN events ev
            ON ev.event_key = COALESCE(et.event_key, so.event_key)
         WHERE {" AND ".join(where_clauses)}
        """,
        tuple(params),
    ).fetchone()
    earliest = row["earliest_decision_at_utc"] if row else None
    latest = row["latest_decision_at_utc"] if row else None
    return {
        "distinct_event_count": int(row["distinct_event_count"] or 0) if row else 0,
        "distinct_token_count": int(row["distinct_token_count"] or 0) if row else 0,
        "distinct_symbol_count": int(row["distinct_symbol_count"] or 0) if row else 0,
        "distinct_window_count": int(row["distinct_window_count"] or 0) if row else 0,
        "decision_span": {
            "earliest_decision_at_utc": earliest,
            "latest_decision_at_utc": latest,
        },
    }


def _bulk_observation_diversity_summary(
    conn: Any,
    keys: set[tuple[str, str]],
    *,
    by_phase: bool = False,
) -> dict[tuple[str, ...], dict[str, Any]]:
    if not keys:
        return {}
    signal_ids = sorted({signal_id for signal_id, _version in keys})
    placeholders = ",".join("?" for _ in signal_ids)
    phase_column = ", so.phase AS phase" if by_phase else ""
    phase_group = ", so.phase" if by_phase else ""
    summaries: dict[tuple[str, ...], dict[str, Any]] = {}
    for raw in conn.execute(
        f"""
        SELECT
            so.signal_id,
            so.version
            {phase_column},
            COUNT(DISTINCT CASE WHEN so.event_key IS NOT NULL AND so.event_key != '' THEN so.event_key END) AS distinct_event_count,
            COUNT(DISTINCT CASE WHEN so.event_token_key IS NOT NULL AND so.event_token_key != '' THEN so.event_token_key END) AS distinct_token_count,
            0 AS distinct_symbol_count,
            COUNT(DISTINCT substr(so.decision_at_utc, 1, 16)) AS distinct_window_count,
            MIN(so.decision_at_utc) AS earliest_decision_at_utc,
            MAX(so.decision_at_utc) AS latest_decision_at_utc
          FROM signal_observations so
         WHERE so.signal_id IN ({placeholders})
         GROUP BY so.signal_id, so.version{phase_group}
        """,
        tuple(signal_ids),
    ).fetchall():
        row = dict(raw)
        base_key = (str(row.get("signal_id") or ""), str(row.get("version") or "v1"))
        if base_key not in keys:
            continue
        if by_phase:
            summaries[(*base_key, str(row.get("phase") or ""))] = _diversity_summary_from_row(row)
        else:
            summaries[base_key] = _diversity_summary_from_row(row)
    # Symbol diversity is display/context metadata, not a promotion gate.
    # On the live 10GB+ SQLite bridge this join can spill to temp storage while
    # data services are writing; keep status endpoints available and let the
    # stricter Postgres/read-model path own exact symbol diversity later.
    try:
        symbol_counts = _bulk_observation_symbol_counts(conn, keys, by_phase=by_phase)
    except Exception as exc:
        if not is_database_full_error(exc):
            raise
        symbol_counts = {}
    for key, symbol_count in symbol_counts.items():
        if key in summaries:
            summaries[key]["distinct_symbol_count"] = symbol_count
    return summaries


def _bulk_observation_symbol_counts(
    conn: Any,
    keys: set[tuple[str, str]],
    *,
    by_phase: bool = False,
) -> dict[tuple[str, ...], int]:
    if not keys:
        return {}
    signal_ids = sorted({signal_id for signal_id, _version in keys})
    placeholders = ",".join("?" for _ in signal_ids)
    phase_select = ", so.phase" if by_phase else ""
    phase_column = ", phase" if by_phase else ""
    phase_group = ", oe.phase" if by_phase else ""
    counts: dict[tuple[str, ...], int] = {}
    for raw in conn.execute(
        f"""
        WITH observed_events AS (
            SELECT DISTINCT so.signal_id, so.version{phase_select}, so.event_key, so.event_token_key
              FROM signal_observations so
             WHERE so.signal_id IN ({placeholders})
        )
        SELECT
            oe.signal_id,
            oe.version
            {phase_column},
            COUNT(DISTINCT COALESCE(NULLIF(et.symbol, ''), NULLIF(ev.symbol, ''))) AS distinct_symbol_count
          FROM observed_events oe
          LEFT JOIN event_tokens et
            ON et.event_token_key = oe.event_token_key
          LEFT JOIN events ev
            ON ev.event_key = COALESCE(et.event_key, oe.event_key)
         GROUP BY oe.signal_id, oe.version{phase_group}
        """,
        tuple(signal_ids),
    ).fetchall():
        row = dict(raw)
        base_key = (str(row.get("signal_id") or ""), str(row.get("version") or "v1"))
        if base_key not in keys:
            continue
        if by_phase:
            counts[(*base_key, str(row.get("phase") or ""))] = int(row.get("distinct_symbol_count") or 0)
        else:
            counts[base_key] = int(row.get("distinct_symbol_count") or 0)
    return counts


def _diversity_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "distinct_event_count": int(row.get("distinct_event_count") or 0),
        "distinct_token_count": int(row.get("distinct_token_count") or 0),
        "distinct_symbol_count": int(row.get("distinct_symbol_count") or 0),
        "distinct_window_count": int(row.get("distinct_window_count") or 0),
        "decision_span": {
            "earliest_decision_at_utc": row.get("earliest_decision_at_utc"),
            "latest_decision_at_utc": row.get("latest_decision_at_utc"),
        },
    }


def _empty_diversity_summary() -> dict[str, Any]:
    return {
        "distinct_event_count": 0,
        "distinct_token_count": 0,
        "distinct_symbol_count": 0,
        "distinct_window_count": 0,
        "decision_span": {
            "earliest_decision_at_utc": None,
            "latest_decision_at_utc": None,
        },
    }


def _review_request_summary(conn: Any, *, limit: int = 5) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT signal_artifact_key, generated_at_utc, artifact_json
              FROM signal_artifacts
             WHERE artifact_type='review_request'
             ORDER BY generated_at_utc DESC, inserted_at_utc DESC
            """
        ).fetchall()
    ]
    requests: list[dict[str, Any]] = []
    pending_requests: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_load(row.get("artifact_json"), {})
        request = {
            "signal_artifact_key": row.get("signal_artifact_key"),
            "generated_at_utc": row.get("generated_at_utc"),
            "status": payload.get("status") or "unknown",
            "requested_by": payload.get("requested_by"),
            "message": payload.get("message"),
        }
        requests.append(request)
        if request["status"] == "pending":
            pending_requests.append(request)
    return {
        "total_review_request_count": len(requests),
        "pending_review_request_count": len(pending_requests),
        "latest_pending_review_requests": pending_requests[: max(1, int(limit))],
    }


def _signal_history_integrity_summary(conn: Any, *, orphan_limit: int = 5) -> dict[str, Any]:
    orphaned_run_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM signal_validation_runs r
            LEFT JOIN signal_validation_results vr
              ON vr.validation_run_key = r.validation_run_key
            WHERE vr.validation_run_key IS NULL
            """
        ).fetchone()[0]
    )
    latest_orphaned_runs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.validation_run_key, r.signal_id, r.version, r.phase, r.status, r.started_at_utc
            FROM signal_validation_runs r
            LEFT JOIN signal_validation_results vr
              ON vr.validation_run_key = r.validation_run_key
            WHERE vr.validation_run_key IS NULL
            ORDER BY r.started_at_utc ASC, r.validation_run_key ASC
            LIMIT ?
            """,
            (max(0, orphan_limit),),
        ).fetchall()
    ]
    return {
        "signal_history_trust_state": "needs_reconciliation" if orphaned_run_count > 0 else "consistent",
        "orphaned_run_count": orphaned_run_count,
        "latest_orphaned_runs": latest_orphaned_runs,
    }


def _status_selection_key(row: dict[str, Any]) -> tuple[int, int, int]:
    status = str(row.get("queue_status") or "")
    phase = str(row.get("queue_phase") or "")
    phase_rank = {
        "last_week_backtest": 0,
        "last_month_backtest": 1,
        "random_sampling_backtest": 2,
        "live_shadow_test": 3,
    }.get(phase, 9)
    if status in {"RUNNING", "OWNED", "QUEUED", "FAILED", "BLOCKED"}:
        status_rank = {"RUNNING": 0, "OWNED": 1, "QUEUED": 2, "FAILED": 3, "BLOCKED": 4}.get(status, 8)
        return (0, phase_rank, status_rank)
    return (1, -phase_rank, 0)


def _json_load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
