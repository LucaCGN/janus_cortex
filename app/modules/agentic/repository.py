from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.data.databases.postgres import managed_connection
from app.modules.agentic.contracts import (
    MarketOrderbookTickRequest,
    MarketTradeObservation,
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
    watch_session_key = payload.watch_session_id or str(uuid.uuid4())
    watch_session_id = _coerce_uuid(watch_session_key, namespace="watch-session")
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
                        Json(to_jsonable({**payload.metadata, "watch_session_key": watch_session_key})),
                    ),
                )
            connection.commit()
        return {"ok": True, "watch_session_id": watch_session_id, "watch_session_key": watch_session_key}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "watch_session_id": watch_session_id, "watch_session_key": watch_session_key}


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


_MARKET_TRADE_NAMESPACE = uuid.UUID("56bbac1f-6d4a-4a79-b11b-549ac4d4d40a")


def _market_trade_observation_id(trade: MarketTradeObservation) -> str:
    identity = "|".join(
        [
            str(trade.event_key or ""),
            str(trade.market_id or ""),
            str(trade.outcome_id or ""),
            str(trade.token_id or ""),
            str(trade.external_trade_id or ""),
            trade.trade_time_utc.isoformat(),
            str(trade.side or ""),
            str(trade.price or ""),
            str(trade.size or ""),
        ]
    )
    return str(uuid.uuid5(_MARKET_TRADE_NAMESPACE, identity))


def try_persist_market_trades(payload: MarketTradeObservationRequest) -> dict[str, Any]:
    rows = [
        (
            _market_trade_observation_id(trade),
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_trade_id)
                    DO UPDATE SET
                        event_key = EXCLUDED.event_key,
                        market_id = EXCLUDED.market_id,
                        outcome_id = EXCLUDED.outcome_id,
                        token_id = EXCLUDED.token_id,
                        external_trade_id = EXCLUDED.external_trade_id,
                        trade_time = EXCLUDED.trade_time,
                        observed_at = EXCLUDED.observed_at,
                        side = EXCLUDED.side,
                        price = EXCLUDED.price,
                        size = EXCLUDED.size,
                        source_latency_ms = EXCLUDED.source_latency_ms,
                        raw_json = EXCLUDED.raw_json;
                    """,
                    rows,
                )
            connection.commit()
        return {"ok": True, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "row_count": 0}


def _has_text(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


_OPERATOR_ADOPTION_ACTIONS = {"adopt", "reject", "protect", "target", "hedge"}


def _operator_intervention_metadata_gate(payload: OperatorInterventionRequest) -> dict[str, Any]:
    metadata_required = payload.action in _OPERATOR_ADOPTION_ACTIONS
    missing_fields: list[str] = []
    external_reference_count = len(payload.external_order_ids) + len(payload.external_trade_ids)

    if metadata_required:
        if external_reference_count == 0:
            missing_fields.append("external_order_ids_or_external_trade_ids")
        if not _has_text(payload.strategy_family) and not _has_text(payload.manual_reason):
            missing_fields.append("strategy_family_or_manual_reason")
        for field_name in ("target_status", "stop_status", "hedge_status", "expected_close_path"):
            if not _has_text(getattr(payload, field_name)):
                missing_fields.append(field_name)
        if payload.final_pnl_usd is None:
            missing_fields.append("final_pnl_usd")

    adoption_class = "strategy_family" if _has_text(payload.strategy_family) else "manual_only"
    if not _has_text(payload.strategy_family) and not _has_text(payload.manual_reason):
        adoption_class = "unclassified"

    status = "recorded"
    if metadata_required:
        status = "metadata_complete" if not missing_fields else "metadata_incomplete"

    return {
        "status": status,
        "metadata_required": metadata_required,
        "metadata_complete": not missing_fields,
        "missing_metadata_fields": missing_fields,
        "adoption_class": adoption_class,
        "external_reference_count": external_reference_count,
        "strategy_family": payload.strategy_family,
        "manual_reason": payload.manual_reason,
        "target_status": payload.target_status,
        "stop_status": payload.stop_status,
        "hedge_status": payload.hedge_status,
        "protective_order_status": payload.protective_order_status,
        "expected_close_path": payload.expected_close_path,
        "final_pnl_usd": payload.final_pnl_usd,
    }


def _operator_intervention_metadata_summary(metadata_gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata_status": metadata_gate["status"],
        "metadata_required": metadata_gate["metadata_required"],
        "metadata_complete": metadata_gate["metadata_complete"],
        "missing_metadata_fields": metadata_gate["missing_metadata_fields"],
        "adoption_class": metadata_gate["adoption_class"],
    }


def try_persist_operator_intervention(payload: OperatorInterventionRequest) -> dict[str, Any]:
    metadata_gate = _operator_intervention_metadata_gate(payload)
    payload_json = payload.model_dump(mode="json")
    raw_json = {**payload_json, "adoption_metadata": metadata_gate}
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
                        metadata_gate["status"],
                        payload.notes,
                        Json(to_jsonable(raw_json)),
                    ),
                )
            connection.commit()
        return {"ok": True, **_operator_intervention_metadata_summary(metadata_gate)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), **_operator_intervention_metadata_summary(metadata_gate)}


def try_persist_replay_request(payload: ReplayFromWatchSessionRequest, *, output_root: str | None = None) -> dict[str, Any]:
    watch_session_id = _coerce_uuid(payload.watch_session_id, namespace="watch-session")
    replay_session_id = str(uuid.uuid4())
    try:
        with managed_connection() as connection:
            with cursor_dict(connection) as cursor:
                cursor.execute(
                    """
                    SELECT
                        watch_session_id::text AS watch_session_id,
                        event_key,
                        category,
                        started_at,
                        ended_at,
                        cadence_ms,
                        passive_only,
                        reason,
                        metadata_json
                    FROM agentic.market_watch_sessions
                    WHERE watch_session_id = %s;
                    """,
                    (watch_session_id,),
                )
                watch_session = fetchone_dict(cursor)
                if watch_session is None:
                    return {
                        "ok": False,
                        "error": "watch_session_not_found",
                        "watch_session_id": watch_session_id,
                        "watch_session_key": payload.watch_session_id,
                    }
                event_key = str(payload.event_key or watch_session.get("event_key") or "").strip()
                if payload.event_key and watch_session.get("event_key") and payload.event_key != watch_session.get("event_key"):
                    return {
                        "ok": False,
                        "error": "event_key_mismatch",
                        "watch_session_id": watch_session_id,
                        "watch_session_key": payload.watch_session_id,
                        "event_key": event_key,
                    }
                started_at = _parse_replay_timestamp(watch_session.get("started_at"))
                ended_at = _parse_replay_timestamp(watch_session.get("ended_at"))
                cursor.execute(
                    """
                    SELECT
                        market_orderbook_tick_id::text AS market_orderbook_tick_id,
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
                    FROM agentic.market_orderbook_ticks
                    WHERE event_key = %s
                      AND (%s::timestamptz IS NULL OR captured_at >= %s)
                      AND (%s::timestamptz IS NULL OR captured_at <= %s)
                    ORDER BY captured_at ASC;
                    """,
                    (event_key, started_at, started_at, ended_at, ended_at),
                )
                tick_rows = _filter_rows_for_watch_session(
                    fetchall_dicts(cursor),
                    watch_session_key=payload.watch_session_id,
                    watch_session_id=watch_session_id,
                )
                if not tick_rows:
                    return {
                        "ok": False,
                        "error": "replay_source_empty",
                        "watch_session_id": watch_session_id,
                        "watch_session_key": payload.watch_session_id,
                        "event_key": event_key,
                        "source_tick_count": 0,
                        "source_trade_count": 0,
                    }
                cursor.execute(
                    """
                    SELECT
                        market_trade_id::text AS market_trade_id,
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
                    FROM agentic.market_trades
                    WHERE event_key = %s
                      AND (%s::timestamptz IS NULL OR trade_time >= %s)
                      AND (%s::timestamptz IS NULL OR trade_time <= %s)
                    ORDER BY trade_time ASC;
                    """,
                    (event_key, started_at, started_at, ended_at, ended_at),
                )
                trade_rows = _filter_rows_for_watch_session(
                    fetchall_dicts(cursor),
                    watch_session_key=payload.watch_session_id,
                    watch_session_id=watch_session_id,
                )
                cursor.execute(
                    """
                    SELECT
                        strategy_decision_id::text AS strategy_decision_id,
                        event_key,
                        decided_at,
                        strategy_id,
                        decision_type,
                        blockers_json,
                        order_intent_json,
                        raw_json
                    FROM agentic.strategy_decisions
                    WHERE event_key = %s
                      AND (%s::timestamptz IS NULL OR decided_at >= %s)
                      AND (%s::timestamptz IS NULL OR decided_at <= %s)
                    ORDER BY decided_at ASC;
                    """,
                    (event_key, started_at, started_at, ended_at, ended_at),
                )
                decision_rows = fetchall_dicts(cursor)
                latency_summary = _build_replay_source_summary(
                    watch_session=watch_session,
                    tick_rows=tick_rows,
                    trade_rows=trade_rows,
                    decision_rows=decision_rows,
                )
                output_name = payload.output_name or _default_replay_output_name(event_key)
                replay_config = {
                    **payload.model_dump(mode="json"),
                    "watch_session_key": payload.watch_session_id,
                    "resolved_event_key": event_key,
                    "source": "watch_session",
                    "source_tick_count": len(tick_rows),
                    "source_trade_count": len(trade_rows),
                    "controller_decision_comparison": latency_summary.get("controller_decision_comparison", {}),
                }
                cursor.execute(
                    """
                    INSERT INTO agentic.replay_sessions (
                        replay_session_id,
                        watch_session_id,
                        event_key,
                        output_name,
                        source_tick_count,
                        source_trade_count,
                        latency_summary_json,
                        replay_config_json,
                        output_root
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        replay_session_id,
                        watch_session_id,
                        event_key,
                        output_name,
                        len(tick_rows),
                        len(trade_rows),
                        Json(to_jsonable(latency_summary)),
                        Json(to_jsonable(replay_config)),
                        output_root,
                    ),
                )
            connection.commit()
        return {
            "ok": True,
            "replay_session_id": replay_session_id,
            "watch_session_id": watch_session_id,
            "watch_session_key": payload.watch_session_id,
            "event_key": event_key,
            "output_name": output_name,
            "source_tick_count": len(tick_rows),
            "source_trade_count": len(trade_rows),
            "latency_summary": latency_summary,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "watch_session_id": watch_session_id, "watch_session_key": payload.watch_session_id}


def _default_replay_output_name(event_key: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(event_key or "").strip())
    safe = safe.strip("-")[:120] or "unknown"
    return f"replay-{safe}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_replay_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _filter_rows_for_watch_session(
    rows: list[dict[str, Any]],
    *,
    watch_session_key: str,
    watch_session_id: str,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for row in rows:
        raw = _json_object(row.get("raw_json"))
        if raw.get("watch_session_key") == watch_session_key or str(raw.get("watch_session_id") or "") == watch_session_id:
            matched.append(row)
    return matched or rows


def _window_summary(rows: list[dict[str, Any]], timestamp_key: str) -> dict[str, Any]:
    timestamps = [_parse_replay_timestamp(row.get(timestamp_key)) for row in rows]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    if not timestamps:
        return {"start": None, "end": None}
    return {"start": min(timestamps).isoformat(), "end": max(timestamps).isoformat()}


def _numeric_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [_float_or_none(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return {"avg": None, "max": None}
    return {
        "avg": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
    }


def _sorted_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    values = {str(row.get(key) or "").strip() for row in rows}
    return sorted(value for value in values if value)


def _tick_cadence_summary(rows: list[dict[str, Any]], cadence_ms: Any) -> dict[str, Any]:
    cadence_seconds = _float_or_none(cadence_ms)
    cadence_seconds = cadence_seconds / 1000.0 if cadence_seconds is not None else None
    grouped: dict[tuple[str, str, str], list[datetime]] = {}
    for row in rows:
        timestamp = _parse_replay_timestamp(row.get("captured_at"))
        if timestamp is None:
            continue
        key = (
            str(row.get("market_id") or ""),
            str(row.get("outcome_id") or ""),
            str(row.get("token_id") or ""),
        )
        grouped.setdefault(key, []).append(timestamp)
    intervals: list[float] = []
    for timestamps in grouped.values():
        ordered = sorted(timestamps)
        intervals.extend((right - left).total_seconds() for left, right in zip(ordered, ordered[1:]))
    if not intervals:
        return {
            "stream_count": len(grouped),
            "avg_interval_seconds": None,
            "max_gap_seconds": None,
            "gap_over_cadence_count": 0,
            "cadence_ms": cadence_ms,
        }
    threshold = cadence_seconds * 2.0 if cadence_seconds is not None else None
    return {
        "stream_count": len(grouped),
        "avg_interval_seconds": round(sum(intervals) / len(intervals), 3),
        "max_gap_seconds": round(max(intervals), 3),
        "gap_over_cadence_count": len([interval for interval in intervals if threshold is not None and interval > threshold]),
        "cadence_ms": cadence_ms,
    }


def _decision_comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_types = Counter(str(row.get("decision_type") or "unknown") for row in rows)
    return {
        "status": "decisions_available" if rows else "missing_controller_decisions",
        "decision_count": len(rows),
        "decision_types": dict(sorted(decision_types.items())),
        "strategy_ids": _sorted_values(rows, "strategy_id"),
    }


def _build_replay_source_summary(
    *,
    watch_session: dict[str, Any],
    tick_rows: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "watch_session": {
            "watch_session_id": watch_session.get("watch_session_id"),
            "event_key": watch_session.get("event_key"),
            "category": watch_session.get("category"),
            "started_at": to_jsonable(watch_session.get("started_at")),
            "ended_at": to_jsonable(watch_session.get("ended_at")),
            "cadence_ms": watch_session.get("cadence_ms"),
            "passive_only": watch_session.get("passive_only"),
            "reason": watch_session.get("reason"),
        },
        "event_keys": _sorted_values(tick_rows + trade_rows, "event_key"),
        "market_ids": _sorted_values(tick_rows + trade_rows, "market_id"),
        "outcome_ids": _sorted_values(tick_rows + trade_rows, "outcome_id"),
        "token_ids": _sorted_values(tick_rows + trade_rows, "token_id"),
        "source_tick_count": len(tick_rows),
        "source_trade_count": len(trade_rows),
        "tick_window": _window_summary(tick_rows, "captured_at"),
        "trade_window": _window_summary(trade_rows, "trade_time"),
        "orderbook_source_latency_ms": _numeric_summary(tick_rows, "source_latency_ms"),
        "orderbook_ingest_latency_ms": _numeric_summary(tick_rows, "ingest_latency_ms"),
        "trade_source_latency_ms": _numeric_summary(trade_rows, "source_latency_ms"),
        "tick_cadence": _tick_cadence_summary(tick_rows, watch_session.get("cadence_ms")),
        "controller_decision_comparison": _decision_comparison_summary(decision_rows),
    }


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


def _coerce_uuid(value: str, *, namespace: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"janus:{namespace}:{value}"))
