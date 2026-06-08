from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_options_app.db.connection import table_exists


PIPELINE_PHASE_HISTORICAL_BACKTEST = "historical_backtest"
PIPELINE_PHASE_SHADOW_REPLAY = "shadow_replay"
PIPELINE_PHASE_RECENT_SHADOW_SAMPLE = "recent_shadow_sample"
PIPELINE_PHASE_LIVE_CANDIDATE = "live_candidate"
LEGACY_PIPELINE_PHASE_SUPERVISED_LIVE_CANDIDATE = "supervised_live_candidate"
# Backward-compatible import name; new queue rows must use `live_candidate`.
PIPELINE_PHASE_SUPERVISED_LIVE_CANDIDATE = PIPELINE_PHASE_LIVE_CANDIDATE

TERMINAL_QUEUE_STATUSES = {"done", "blocked", "retired"}
ACTIVE_QUEUE_STATUSES = {"queued", "running"}


def promotion_state_to_pipeline_phase(promotion_state: str | None) -> str | None:
    state = str(promotion_state or "").upper()
    if state == "NEEDS_BACKTEST":
        return PIPELINE_PHASE_HISTORICAL_BACKTEST
    if state == "BACKTEST_READY":
        return PIPELINE_PHASE_SHADOW_REPLAY
    if state == "SHADOW_READY":
        return PIPELINE_PHASE_RECENT_SHADOW_SAMPLE
    if state in {"LIVE_CANDIDATE", "LIVE_RUNNING"}:
        return PIPELINE_PHASE_LIVE_CANDIDATE
    return None


def pipeline_queue_key(strategy_id: str, strategy_version: str, phase: str) -> str:
    return f"{phase}:{strategy_id}:{strategy_version}"


def enqueue_initial_strategy_backtest(conn: Any, strategy_id: str, strategy_version: str, *, priority: int = 100) -> bool:
    return enqueue_strategy_pipeline_work(
        conn,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        phase=PIPELINE_PHASE_HISTORICAL_BACKTEST,
        priority=priority,
        reason="strategy_registered_enqueue_historical_backtest",
    )


def enqueue_strategy_pipeline_work(
    conn: Any,
    *,
    strategy_id: str,
    strategy_version: str,
    phase: str,
    reason: str,
    priority: int = 100,
    next_run_after_utc: str | None = None,
    requeue_terminal_done: bool = False,
    requeue_terminal_blocked: bool = False,
) -> bool:
    """Persist one phase item if it has not already reached a terminal state."""

    if not table_exists(conn, "strategy_pipeline_queue"):
        return False
    queue_key = pipeline_queue_key(strategy_id, strategy_version, phase)
    existing = conn.execute(
        """
        SELECT queue_status
        FROM strategy_pipeline_queue
        WHERE queue_key = ?
        LIMIT 1
        """,
        (queue_key,),
    ).fetchone()
    if existing is not None:
        status = str(existing["queue_status"])
        if status in TERMINAL_QUEUE_STATUSES:
            if (status == "done" and requeue_terminal_done) or (status == "blocked" and requeue_terminal_blocked):
                conn.execute(
                    """
                    UPDATE strategy_pipeline_queue
                       SET queue_status = 'queued',
                           owner = NULL,
                           claimed_at_utc = NULL,
                           claim_expires_at_utc = NULL,
                           priority = ?,
                           reason = ?,
                           next_run_after_utc = ?,
                           updated_at_utc = ?
                     WHERE queue_key = ?
                    """,
                    (int(priority), reason, next_run_after_utc, _utc_now(), queue_key),
                )
                return True
            return False
        conn.execute(
            """
            UPDATE strategy_pipeline_queue
               SET priority = ?,
                   reason = ?,
                   next_run_after_utc = COALESCE(?, next_run_after_utc),
                   updated_at_utc = ?
             WHERE queue_key = ?
            """,
            (int(priority), reason, next_run_after_utc, _utc_now(), queue_key),
        )
        return False

    now = _utc_now()
    conn.execute(
        """
        INSERT INTO strategy_pipeline_queue(
            queue_key, strategy_id, strategy_version, phase, queue_status,
            priority, reason, attempt_count, next_run_after_utc,
            last_result_json, created_at_utc, updated_at_utc
        )
        VALUES(?, ?, ?, ?, 'queued', ?, ?, 0, ?, '{}', ?, ?)
        ON CONFLICT(queue_key) DO NOTHING
        """,
        (
            queue_key,
            strategy_id,
            strategy_version,
            phase,
            int(priority),
            reason,
            next_run_after_utc,
            now,
            now,
        ),
    )
    return True


def claim_strategy_pipeline_work(
    conn: Any,
    *,
    owner: str,
    limit: int = 1,
    lease_seconds: int = 1800,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "strategy_pipeline_queue"):
        return []
    now = _utc_now()
    expires = (datetime.now(UTC) + timedelta(seconds=max(60, int(lease_seconds)))).isoformat()
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT queue_key, strategy_id, strategy_version, phase, queue_status,
                   priority, reason, attempt_count, owner, claimed_at_utc,
                   claim_expires_at_utc, next_run_after_utc, last_run_id,
                   last_result_json, created_at_utc, updated_at_utc
              FROM strategy_pipeline_queue
             WHERE (
                    queue_status = 'queued'
                    OR (
                        queue_status = 'running'
                        AND claim_expires_at_utc IS NOT NULL
                        AND claim_expires_at_utc < ?
                    )
                   )
               AND (next_run_after_utc IS NULL OR next_run_after_utc <= ?)
             ORDER BY
                   CASE phase
                     WHEN 'live_candidate' THEN 0
                     WHEN 'supervised_live_candidate' THEN 0
                     WHEN 'historical_backtest' THEN 1
                     WHEN 'shadow_replay' THEN 2
                     WHEN 'recent_shadow_sample' THEN 3
                     ELSE 4
                   END ASC,
                   CASE
                     WHEN phase = 'recent_shadow_sample' AND attempt_count > 0
                     THEN priority + 100
                     ELSE priority
                   END ASC,
                   created_at_utc ASC,
                   queue_key ASC
             LIMIT ?
            """,
            (now, now, max(1, int(limit))),
        ).fetchall()
    ]
    claimed: list[dict[str, Any]] = []
    for row in rows:
        conn.execute(
            """
            UPDATE strategy_pipeline_queue
               SET queue_status = 'running',
                   owner = ?,
                   claimed_at_utc = ?,
                   claim_expires_at_utc = ?,
                   attempt_count = attempt_count + 1,
                   updated_at_utc = ?
             WHERE queue_key = ?
               AND queue_status IN ('queued', 'running')
            """,
            (owner, now, expires, now, row["queue_key"]),
        )
        refreshed = conn.execute(
            """
            SELECT queue_key, strategy_id, strategy_version, phase, queue_status,
                   priority, reason, attempt_count, owner, claimed_at_utc,
                   claim_expires_at_utc, next_run_after_utc, last_run_id,
                   last_result_json, created_at_utc, updated_at_utc
              FROM strategy_pipeline_queue
             WHERE queue_key = ?
            """,
            (row["queue_key"],),
        ).fetchone()
        if refreshed is not None:
            claimed.append(dict(refreshed))
    return claimed


def complete_strategy_pipeline_work(
    conn: Any,
    queue_key: str,
    *,
    queue_status: str,
    result: dict[str, Any],
    run_id: str | None = None,
    next_run_after_seconds: int | None = None,
) -> None:
    status = str(queue_status)
    next_run_after_utc = None
    if next_run_after_seconds is not None:
        next_run_after_utc = (datetime.now(UTC) + timedelta(seconds=max(0, int(next_run_after_seconds)))).isoformat()
    conn.execute(
        """
        UPDATE strategy_pipeline_queue
           SET queue_status = ?,
               owner = NULL,
               claimed_at_utc = NULL,
               claim_expires_at_utc = NULL,
               next_run_after_utc = ?,
               last_run_id = ?,
               last_result_json = ?,
               updated_at_utc = ?
         WHERE queue_key = ?
        """,
        (
            status,
            next_run_after_utc,
            run_id,
            json.dumps(result, sort_keys=True, default=str),
            _utc_now(),
            queue_key,
        ),
    )


def pipeline_queue_summary(conn: Any, *, limit: int = 50) -> dict[str, Any]:
    if not table_exists(conn, "strategy_pipeline_queue"):
        return {
            "schema_version": "crypto_options_strategy_pipeline_queue_v1",
            "table_present": False,
            "by_status": {},
            "by_phase": {},
            "items": [],
        }
    status_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT queue_status, COUNT(*) AS item_count
              FROM strategy_pipeline_queue
             GROUP BY queue_status
            """
        ).fetchall()
    ]
    phase_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT phase, COUNT(*) AS item_count
              FROM strategy_pipeline_queue
             GROUP BY phase
            """
        ).fetchall()
    ]
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT queue_key, strategy_id, strategy_version, phase, queue_status,
                   priority, reason, attempt_count, owner, next_run_after_utc,
                   last_run_id, created_at_utc, updated_at_utc
              FROM strategy_pipeline_queue
             ORDER BY queue_status ASC, priority ASC, updated_at_utc DESC
             LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    ]
    by_status = {str(row["queue_status"]): int(row["item_count"]) for row in status_rows}
    by_phase = {str(row["phase"]): int(row["item_count"]) for row in phase_rows}
    active_status_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT queue_status, COUNT(*) AS item_count
              FROM strategy_pipeline_queue
             WHERE queue_status IN ('queued', 'running')
             GROUP BY queue_status
            """
        ).fetchall()
    ]
    active_phase_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT phase, COUNT(*) AS item_count
              FROM strategy_pipeline_queue
             WHERE queue_status IN ('queued', 'running')
             GROUP BY phase
            """
        ).fetchall()
    ]
    active_by_status = {str(row["queue_status"]): int(row["item_count"]) for row in active_status_rows}
    active_by_phase = {str(row["phase"]): int(row["item_count"]) for row in active_phase_rows}
    return {
        "schema_version": "crypto_options_strategy_pipeline_queue_v1",
        "table_present": True,
        "by_status": dict(sorted(by_status.items())),
        "by_phase": dict(sorted(by_phase.items())),
        "active_by_status": dict(sorted(active_by_status.items())),
        "active_by_phase": dict(sorted(active_by_phase.items())),
        "items": rows,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
