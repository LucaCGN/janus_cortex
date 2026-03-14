from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.models import OrderbookHistoryQuery, OutcomeCandlesQuery, OutcomeTicksQuery


router = APIRouter(prefix="/v1", tags=["market-data"])


def _append_time_filters(
    *,
    conditions: list[str],
    params: list[Any],
    column_name: str,
    start_time: datetime | None,
    end_time: datetime | None,
) -> None:
    if start_time is not None:
        conditions.append(f"{column_name} >= %s")
        params.append(start_time)
    if end_time is not None:
        conditions.append(f"{column_name} <= %s")
        params.append(end_time)


@router.get("/outcomes/{outcome_id}/prices/ticks")
def get_outcome_price_ticks(
    outcome_id: UUID,
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    asc: bool = Query(default=False),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = OutcomeTicksQuery(
        start_time=start_time,
        end_time=end_time,
        source=source,
        limit=limit,
    )
    conditions = ["outcome_id = %s"]
    params: list[Any] = [str(outcome_id)]
    _append_time_filters(
        conditions=conditions,
        params=params,
        column_name="ts",
        start_time=query.start_time,
        end_time=query.end_time,
    )
    if query.source:
        conditions.append("source = %s")
        params.append(query.source)

    order_direction = "ASC" if asc else "DESC"
    params.append(query.limit)
    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT outcome_id, ts, source, price, bid, ask, volume, liquidity, raw_json
            FROM market_data.outcome_price_ticks
            WHERE {' AND '.join(conditions)}
            ORDER BY ts {order_direction}
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/outcomes/{outcome_id}/prices/candles")
def get_outcome_price_candles(
    outcome_id: UUID,
    timeframe: str = Query(default="1m"),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    asc: bool = Query(default=False),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = OutcomeCandlesQuery(
        timeframe=timeframe,
        start_time=start_time,
        end_time=end_time,
        source=source,
        limit=limit,
    )
    conditions = ["outcome_id = %s", "timeframe = %s"]
    params: list[Any] = [str(outcome_id), query.timeframe]
    _append_time_filters(
        conditions=conditions,
        params=params,
        column_name="open_time",
        start_time=query.start_time,
        end_time=query.end_time,
    )
    if query.source:
        conditions.append("source = %s")
        params.append(query.source)

    order_direction = "ASC" if asc else "DESC"
    params.append(query.limit)
    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                outcome_id, timeframe, open_time, source, open, high, low, close, volume, raw_json
            FROM market_data.outcome_price_candles
            WHERE {' AND '.join(conditions)}
            ORDER BY open_time {order_direction}
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/outcomes/{outcome_id}/orderbook/latest")
def get_outcome_orderbook_latest(
    outcome_id: UUID,
    levels_per_side: int = Query(default=10, ge=1, le=100),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                orderbook_snapshot_id,
                outcome_id,
                captured_at,
                best_bid,
                best_ask,
                spread,
                mid_price,
                bid_depth,
                ask_depth,
                raw_json
            FROM market_data.orderbook_snapshots
            WHERE outcome_id = %s
            ORDER BY captured_at DESC
            LIMIT 1;
            """,
            (str(outcome_id),),
        )
        snapshot = fetchone_dict(cursor)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No orderbook snapshot found for outcome_id")

        cursor.execute(
            """
            SELECT side, level_no, price, size, order_count
            FROM (
                SELECT
                    side,
                    level_no,
                    price,
                    size,
                    order_count,
                    row_number() OVER (PARTITION BY side ORDER BY level_no ASC) AS rn
                FROM market_data.orderbook_levels
                WHERE orderbook_snapshot_id = %s
            ) ranked
            WHERE rn <= %s
            ORDER BY side ASC, level_no ASC;
            """,
            (snapshot["orderbook_snapshot_id"], levels_per_side),
        )
        levels = fetchall_dicts(cursor)

    bids = [row for row in levels if str(row.get("side")) == "bid"]
    asks = [row for row in levels if str(row.get("side")) == "ask"]
    return {
        "snapshot": to_jsonable(snapshot),
        "bids": to_jsonable(bids),
        "asks": to_jsonable(asks),
        "levels_count": len(levels),
    }


@router.get("/outcomes/{outcome_id}/orderbook/history")
def get_outcome_orderbook_history(
    outcome_id: UUID,
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    include_levels: bool = Query(default=True),
    levels_per_side: int = Query(default=10, ge=1, le=100),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = OrderbookHistoryQuery(
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        include_levels=include_levels,
        levels_per_side=levels_per_side,
    )
    conditions = ["outcome_id = %s"]
    params: list[Any] = [str(outcome_id)]
    _append_time_filters(
        conditions=conditions,
        params=params,
        column_name="captured_at",
        start_time=query.start_time,
        end_time=query.end_time,
    )
    params.append(query.limit)

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                orderbook_snapshot_id,
                outcome_id,
                captured_at,
                best_bid,
                best_ask,
                spread,
                mid_price,
                bid_depth,
                ask_depth,
                raw_json
            FROM market_data.orderbook_snapshots
            WHERE {' AND '.join(conditions)}
            ORDER BY captured_at DESC
            LIMIT %s;
            """,
            tuple(params),
        )
        snapshots = fetchall_dicts(cursor)

        if not query.include_levels or not snapshots:
            return {"items": to_jsonable(snapshots), "count": len(snapshots)}

        snapshot_ids = [str(item["orderbook_snapshot_id"]) for item in snapshots]
        cursor.execute(
            """
            SELECT orderbook_snapshot_id, side, level_no, price, size, order_count
            FROM (
                SELECT
                    orderbook_snapshot_id,
                    side,
                    level_no,
                    price,
                    size,
                    order_count,
                    row_number() OVER (
                        PARTITION BY orderbook_snapshot_id, side
                        ORDER BY level_no ASC
                    ) AS rn
                FROM market_data.orderbook_levels
                WHERE orderbook_snapshot_id = ANY(%s::uuid[])
            ) ranked
            WHERE rn <= %s
            ORDER BY orderbook_snapshot_id ASC, side ASC, level_no ASC;
            """,
            (snapshot_ids, query.levels_per_side),
        )
        levels = fetchall_dicts(cursor)

    levels_by_snapshot: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in levels:
        sid = str(row["orderbook_snapshot_id"])
        bucket = levels_by_snapshot.setdefault(sid, {"bids": [], "asks": []})
        if str(row.get("side")) == "bid":
            bucket["bids"].append(row)
        else:
            bucket["asks"].append(row)

    items: list[dict[str, Any]] = []
    for snapshot in snapshots:
        sid = str(snapshot["orderbook_snapshot_id"])
        bundle = levels_by_snapshot.get(sid, {"bids": [], "asks": []})
        items.append(
            {
                **snapshot,
                "bids": bundle["bids"],
                "asks": bundle["asks"],
            }
        )
    return {"items": to_jsonable(items), "count": len(items)}


@router.get("/markets/{market_id}/state/latest")
def get_market_state_latest(
    market_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                market_state_snapshot_id,
                market_id,
                sync_run_id,
                captured_at,
                last_price,
                volume,
                liquidity,
                best_bid,
                best_ask,
                mid_price,
                market_status,
                raw_json
            FROM catalog.market_state_snapshots
            WHERE market_id = %s
            ORDER BY captured_at DESC
            LIMIT 1;
            """,
            (str(market_id),),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="No market state snapshot found for market_id")
    return to_jsonable(row)


@router.get("/events/{event_id}/odds/latest")
def get_event_latest_odds(
    event_id: UUID,
    priced_only: bool = Query(default=False),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                m.market_id,
                m.question,
                m.market_slug,
                o.outcome_id,
                o.outcome_index,
                o.outcome_label,
                o.token_id,
                lt.ts AS latest_ts,
                lt.price AS latest_price,
                lt.bid AS latest_bid,
                lt.ask AS latest_ask,
                lt.source AS latest_source
            FROM catalog.markets m
            JOIN catalog.outcomes o ON o.market_id = m.market_id
            LEFT JOIN LATERAL (
                SELECT ts, price, bid, ask, source
                FROM market_data.outcome_price_ticks
                WHERE outcome_id = o.outcome_id
                ORDER BY ts DESC
                LIMIT 1
            ) lt ON TRUE
            WHERE m.event_id = %s
            ORDER BY m.created_at ASC, o.outcome_index ASC;
            """,
            (str(event_id),),
        )
        rows = fetchall_dicts(cursor)
    if not rows:
        raise HTTPException(status_code=404, detail="No outcomes found for event_id")
    if priced_only:
        rows = [row for row in rows if row.get("latest_ts") is not None]
    return {"items": to_jsonable(rows), "count": len(rows)}
