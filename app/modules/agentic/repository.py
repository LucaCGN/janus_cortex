from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.modules.agentic.contracts import (
    OperatorInterventionRequest,
    ReplayFromWatchSessionRequest,
    StrategyPlan,
    WatchlistEvent,
)


def try_persist_strategy_plan(plan: StrategyPlan) -> dict[str, Any]:
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agentic.strategy_plan_versions (
                        strategy_plan_version_id,
                        event_key,
                        market_id,
                        schema_version,
                        plan_owner,
                        generated_at,
                        valid_until,
                        active_strategy_count,
                        plan_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        str(uuid.uuid4()),
                        plan.event_id,
                        plan.market_id,
                        plan.schema_version,
                        plan.plan_owner,
                        plan.generated_at_utc,
                        plan.valid_until_utc,
                        len(plan.active_strategies),
                        Json(to_jsonable(plan.model_dump(mode="json"))),
                    ),
                )
            connection.commit()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def try_persist_watchlist_event(event: WatchlistEvent, *, source: str) -> dict[str, Any]:
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agentic.market_events (
                        market_event_id,
                        event_key,
                        category,
                        provider,
                        title,
                        status,
                        source_urls_json,
                        metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_key)
                    DO UPDATE SET
                        category = EXCLUDED.category,
                        title = EXCLUDED.title,
                        source_urls_json = EXCLUDED.source_urls_json,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now();
                    """,
                    (
                        str(uuid.uuid4()),
                        event.event_key,
                        event.category,
                        "polymarket",
                        event.title,
                        "watchlisted",
                        Json(to_jsonable(event.source_urls)),
                        Json(
                            to_jsonable(
                                {
                                    "source": source,
                                    "market_id": event.market_id,
                                    "notes": event.notes,
                                    "passive_only": event.passive_only,
                                }
                            )
                        ),
                    ),
                )
            connection.commit()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def try_persist_operator_intervention(payload: OperatorInterventionRequest) -> dict[str, Any]:
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agentic.operator_interventions (
                        operator_intervention_id,
                        event_key,
                        market_id,
                        account_id,
                        detected_at,
                        action,
                        external_order_ids_json,
                        reconciliation_action,
                        status,
                        notes,
                        raw_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        str(uuid.uuid4()),
                        payload.event_id,
                        payload.market_id,
                        payload.account_id,
                        datetime.now(timezone.utc),
                        payload.action,
                        Json(to_jsonable(payload.external_order_ids)),
                        payload.action,
                        "recorded",
                        payload.notes,
                        Json(to_jsonable(payload.model_dump(mode="json"))),
                    ),
                )
            connection.commit()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def try_persist_replay_request(payload: ReplayFromWatchSessionRequest, *, output_root: str | None = None) -> dict[str, Any]:
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agentic.replay_sessions (
                        replay_session_id,
                        watch_session_id,
                        event_key,
                        output_name,
                        replay_config_json,
                        output_root
                    )
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (
                        str(uuid.uuid4()),
                        payload.watch_session_id,
                        payload.event_key,
                        payload.output_name,
                        Json(to_jsonable(payload.model_dump(mode="json"))),
                        output_root,
                    ),
                )
            connection.commit()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


__all__ = [
    "try_persist_operator_intervention",
    "try_persist_replay_request",
    "try_persist_strategy_plan",
    "try_persist_watchlist_event",
]
