from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.modules.agentic.contracts import (
    MarketOrderbookTickRequest,
    MarketTradeObservationRequest,
    MarketWatchSessionRequest,
    OperatorInterventionRequest,
    ReplayFromWatchSessionRequest,
    StrategyPlan,
    StrategyPlanEvaluationResult,
    WatchlistEvent,
)


def try_persist_strategy_plan(plan: StrategyPlan) -> dict[str, Any]:
    strategy_plan_version_id = str(uuid.uuid4())
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
                        strategy_plan_version_id,
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
        return {"ok": True, "strategy_plan_version_id": strategy_plan_version_id}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def try_persist_strategy_decisions(
    result: StrategyPlanEvaluationResult,
    *,
    source: str,
    execute_requested: bool,
    account_id: str | None = None,
) -> dict[str, Any]:
    rows: list[tuple[Any, ...]] = []
    decided_at = result.evaluated_at_utc
    raw_base = {
        "source": source,
        "execute_requested": execute_requested,
        "account_id": account_id,
        "event_id": result.event_id,
        "market_id": result.market_id,
    }
    for intent in result.intents:
        rows.append(
            (
                str(uuid.uuid4()),
                result.event_id,
                decided_at,
                intent.strategy_id,
                "order_intent",
                Json(to_jsonable(intent.model_dump(mode="json"))),
                None,
                None,
                None,
                None,
                Json(to_jsonable({**raw_base, "intent_id": intent.intent_id})),
            )
        )
    for blocker in result.blockers:
        strategy_id = str(blocker.get("strategy_id") or "") or None
        rows.append(
            (
                str(uuid.uuid4()),
                result.event_id,
                decided_at,
                strategy_id,
                "blocker",
                None,
                Json(to_jsonable(blocker)),
                None,
                None,
                None,
                Json(to_jsonable(raw_base)),
            )
        )
    for executed_order in result.executed_orders:
        intent_id = str(executed_order.get("intent_id") or "")
        strategy_id = _strategy_id_from_intent_id(intent_id)
        rows.append(
            (
                str(uuid.uuid4()),
                result.event_id,
                decided_at,
                strategy_id,
                "executed_order",
                None,
                None,
                Json(to_jsonable(executed_order)),
                None,
                None,
                Json(to_jsonable({**raw_base, "intent_id": intent_id})),
            )
        )
    if not rows:
        rows.append(
            (
                str(uuid.uuid4()),
                result.event_id,
                decided_at,
                None,
                "no_decision",
                None,
                None,
                None,
                None,
                None,
                Json(to_jsonable({**raw_base, "reason": "no_intents_or_blockers"})),
            )
        )
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO agentic.strategy_decisions (
                        strategy_decision_id,
                        event_key,
                        decided_at,
                        strategy_id,
                        decision_type,
                        order_intent_json,
                        blockers_json,
                        fill_json,
                        exit_json,
                        hedge_json,
                        raw_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    rows,
                )
            connection.commit()
        return {"ok": True, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "row_count": 0}


def get_agentic_database_status() -> dict[str, Any]:
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'agentic'
                    ORDER BY table_name;
                    """
                )
                table_names = [str(row[0]) for row in cursor.fetchall()]
                cursor.execute("SELECT count(*) FROM agentic.strategy_plan_versions;")
                strategy_plan_count = int(cursor.fetchone()[0])
                cursor.execute("SELECT count(*) FROM agentic.strategy_decisions;")
                strategy_decision_count = int(cursor.fetchone()[0])
                cursor.execute("SELECT count(*) FROM agentic.operator_interventions;")
                operator_intervention_count = int(cursor.fetchone()[0])
        expected = {
            "market_events",
            "market_outcomes",
            "market_orderbook_ticks",
            "market_trades",
            "market_watch_sessions",
            "operator_interventions",
            "strategy_plan_versions",
            "strategy_decisions",
            "replay_sessions",
        }
        return {
            "ok": expected.issubset(set(table_names)),
            "schema": "agentic",
            "tables": table_names,
            "missing_tables": sorted(expected.difference(table_names)),
            "strategy_plan_count": strategy_plan_count,
            "strategy_decision_count": strategy_decision_count,
            "operator_intervention_count": operator_intervention_count,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "schema": "agentic", "error": str(exc)}


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


def try_persist_watch_session(payload: MarketWatchSessionRequest) -> dict[str, Any]:
    watch_session_id = payload.watch_session_id or str(uuid.uuid4())
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agentic.market_watch_sessions (
                        watch_session_id,
                        event_key,
                        category,
                        started_at,
                        cadence_ms,
                        passive_only,
                        reason,
                        metadata_json
                    )
                    VALUES (%s, %s, %s, now(), %s, %s, %s, %s)
                    ON CONFLICT (watch_session_id)
                    DO UPDATE SET
                        ended_at = NULL,
                        cadence_ms = EXCLUDED.cadence_ms,
                        passive_only = EXCLUDED.passive_only,
                        reason = EXCLUDED.reason,
                        metadata_json = EXCLUDED.metadata_json;
                    """,
                    (
                        watch_session_id,
                        payload.event_key,
                        payload.category,
                        payload.cadence_ms,
                        payload.passive_only,
                        payload.reason,
                        Json(to_jsonable(payload.metadata)),
                    ),
                )
            connection.commit()
        return {"ok": True, "watch_session_id": watch_session_id}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "watch_session_id": watch_session_id}


def try_persist_orderbook_ticks(payload: MarketOrderbookTickRequest) -> dict[str, Any]:
    rows = [
        (
            str(uuid.uuid4()),
            tick.event_key,
            tick.market_id,
            tick.outcome_id,
            tick.token_id,
            tick.captured_at_utc,
            tick.source_timestamp_utc,
            tick.best_bid,
            tick.best_ask,
            tick.spread,
            tick.mid_price,
            tick.bid_depth,
            tick.ask_depth,
            tick.source_latency_ms,
            tick.ingest_latency_ms,
            Json(to_jsonable(tick.levels)),
            Json(to_jsonable({**tick.raw, "source": payload.source})),
        )
        for tick in payload.ticks
    ]
    if not rows:
        return {"ok": True, "row_count": 0}
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO agentic.market_orderbook_ticks (
                        market_orderbook_tick_id,
                        event_key,
                        market_id,
                        outcome_id,
                        token_id,
                        captured_at,
                        source_timestamp,
                        best_bid,
                        best_ask,
                        spread,
                        mid_price,
                        bid_depth,
                        ask_depth,
                        source_latency_ms,
                        ingest_latency_ms,
                        levels_json,
                        raw_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    rows,
                )
            connection.commit()
        return {"ok": True, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "row_count": 0}


def try_persist_market_trades(payload: MarketTradeObservationRequest) -> dict[str, Any]:
    rows = [
        (
            str(uuid.uuid4()),
            trade.event_key,
            trade.market_id,
            trade.outcome_id,
            trade.token_id,
            trade.external_trade_id,
            trade.trade_time_utc,
            trade.observed_at_utc,
            trade.side,
            trade.price,
            trade.size,
            trade.source_latency_ms,
            Json(to_jsonable({**trade.raw, "source": payload.source})),
        )
        for trade in payload.trades
    ]
    if not rows:
        return {"ok": True, "row_count": 0}
    try:
        with managed_connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO agentic.market_trades (
                        market_trade_id,
                        event_key,
                        market_id,
                        outcome_id,
                        token_id,
                        external_trade_id,
                        trade_time,
                        observed_at,
                        side,
                        price,
                        size,
                        source_latency_ms,
                        raw_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    rows,
                )
            connection.commit()
        return {"ok": True, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "row_count": 0}


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
    "get_agentic_database_status",
    "try_persist_operator_intervention",
    "try_persist_replay_request",
    "try_persist_market_trades",
    "try_persist_orderbook_ticks",
    "try_persist_strategy_decisions",
    "try_persist_strategy_plan",
    "try_persist_watch_session",
    "try_persist_watchlist_event",
]


def _strategy_id_from_intent_id(intent_id: str) -> str | None:
    parts = intent_id.split("|")
    if len(parts) >= 2:
        return parts[1] or None
    return None
