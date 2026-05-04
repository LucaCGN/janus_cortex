from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.guards import (
    enforce_order_rate_limit,
    enforce_order_risk_limits,
    load_order_risk_limits,
)
from app.api.models import (
    ManualOrderCancelRequest,
    ManualOrderCreateRequest,
    ManualOrderResponse,
    PortfolioPositionHistoryQuery,
    PortfolioPositionsQuery,
    PortfolioSummaryQuery,
    TradingAccountCreateRequest,
)
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.polymarket.blockchain.manage_portfolio import (
    OrderSide,
    OrderType,
    PlaceOrderRequest,
    PolymarketCredentials,
    cancel_order,
    place_new_order,
)


router = APIRouter(prefix="/v1/portfolio", tags=["portfolio"])

_PROVIDER_NAMESPACE = uuid.UUID("41395777-ed5f-474f-a5b7-c97567f5ca56")


def _provider_uuid_for(code: str) -> str:
    return str(uuid.uuid5(_PROVIDER_NAMESPACE, code.strip().lower()))


def _resolve_provider_id(
    connection: PsycopgConnection,
    *,
    provider_id: UUID | None,
    provider_code: str,
) -> str:
    if provider_id is not None:
        return str(provider_id)

    with cursor_dict(connection) as cursor:
        cursor.execute("SELECT provider_id FROM core.providers WHERE code = %s;", (provider_code,))
        row = fetchone_dict(cursor)
        if row is not None:
            return str(row["provider_id"])

    repo = JanusUpsertRepository(connection)
    return repo.upsert_provider(
        provider_id=_provider_uuid_for(provider_code),
        code=provider_code,
        name=provider_code.replace("_", " ").title(),
        category="prediction_market",
        base_url=None,
        auth_type="none",
        is_active=True,
    )


def _extract_external_order_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("id", "orderID", "orderId", "order_id"):
            value = payload.get(key)
            if value:
                return str(value)
        nested = payload.get("data")
        if isinstance(nested, dict):
            for key in ("id", "orderID", "orderId", "order_id"):
                value = nested.get(key)
                if value:
                    return str(value)
    return None


def _fetch_account_wallet(
    connection: PsycopgConnection,
    *,
    account_id: str,
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT account_id, wallet_address, proxy_wallet_address, account_label, is_active
            FROM portfolio.trading_accounts
            WHERE account_id = %s;
            """,
            (account_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="account_id not found")
    if not bool(row.get("is_active", True)):
        raise HTTPException(status_code=409, detail="account is not active")
    return row


def _validate_market_outcome_relation(
    connection: PsycopgConnection,
    *,
    market_id: str,
    outcome_id: str | None,
) -> str | None:
    if outcome_id is None:
        return None
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT outcome_id
            FROM catalog.outcomes
            WHERE outcome_id = %s AND market_id = %s
            LIMIT 1;
            """,
            (outcome_id, market_id),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=422, detail="outcome_id does not belong to market_id")
    return str(row["outcome_id"])


def _ensure_market_exists(connection: PsycopgConnection, *, market_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM catalog.markets WHERE market_id = %s;", (market_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="market_id not found")


@router.get("/accounts")
def list_portfolio_accounts(
    provider_code: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if provider_code:
        conditions.append("p.code = %s")
        params.append(provider_code)
    if is_active is not None:
        conditions.append("a.is_active = %s")
        params.append(is_active)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                a.account_id,
                a.provider_id,
                p.code AS provider_code,
                a.account_label,
                a.wallet_address,
                a.proxy_wallet_address,
                a.chain_id,
                a.is_active,
                a.created_at,
                a.updated_at
            FROM portfolio.trading_accounts a
            JOIN core.providers p ON p.provider_id = a.provider_id
            {where_sql}
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_portfolio_account(
    payload: TradingAccountCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    provider_id = _resolve_provider_id(
        connection,
        provider_id=payload.provider_id,
        provider_code=payload.provider_code.strip(),
    )
    repo = JanusUpsertRepository(connection)
    account_id = repo.upsert_trading_account(
        account_id=str(payload.account_id or uuid4()),
        provider_id=provider_id,
        account_label=payload.account_label.strip(),
        wallet_address=payload.wallet_address,
        proxy_wallet_address=payload.proxy_wallet_address,
        chain_id=payload.chain_id,
        is_active=payload.is_active,
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                a.account_id,
                a.provider_id,
                p.code AS provider_code,
                a.account_label,
                a.wallet_address,
                a.proxy_wallet_address,
                a.chain_id,
                a.is_active,
                a.created_at,
                a.updated_at
            FROM portfolio.trading_accounts a
            JOIN core.providers p ON p.provider_id = a.provider_id
            WHERE a.account_id = %s;
            """,
            (account_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="Trading account was not persisted.")
    return to_jsonable(row)


@router.get("/summary")
def get_portfolio_summary(
    account_id: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = PortfolioSummaryQuery(account_id=account_id, limit=limit)
    conditions: list[str] = []
    params: list[Any] = []
    if query.account_id is not None:
        conditions.append("a.account_id = %s")
        params.append(str(query.account_id))
    else:
        conditions.append("a.is_active = TRUE")
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(query.limit)

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            WITH latest_vals AS (
                SELECT DISTINCT ON (vs.account_id)
                    vs.account_id,
                    vs.captured_at,
                    vs.equity_usd,
                    vs.cash_usd,
                    vs.positions_value_usd,
                    vs.realized_pnl_usd,
                    vs.unrealized_pnl_usd
                FROM portfolio.valuation_snapshots vs
                ORDER BY vs.account_id, vs.captured_at DESC
            ),
            latest_positions AS (
                SELECT DISTINCT ON (ps.account_id, ps.outcome_id)
                    ps.account_id,
                    ps.outcome_id,
                    ps.current_value,
                    ps.unrealized_pnl,
                    ps.realized_pnl
                FROM portfolio.position_snapshots ps
                ORDER BY ps.account_id, ps.outcome_id, ps.captured_at DESC
            ),
            position_agg AS (
                SELECT
                    lp.account_id,
                    count(*)::int AS positions_count,
                    COALESCE(sum(lp.current_value), 0) AS positions_value_usd_calc,
                    COALESCE(sum(lp.unrealized_pnl), 0) AS unrealized_pnl_usd_calc,
                    COALESCE(sum(lp.realized_pnl), 0) AS realized_pnl_usd_calc
                FROM latest_positions lp
                GROUP BY lp.account_id
            )
            SELECT
                a.account_id,
                a.account_label,
                p.code AS provider_code,
                a.wallet_address,
                a.proxy_wallet_address,
                a.chain_id,
                a.is_active,
                lv.captured_at AS valuation_captured_at,
                COALESCE(lv.positions_value_usd, pa.positions_value_usd_calc, 0) AS positions_value_usd,
                COALESCE(lv.realized_pnl_usd, pa.realized_pnl_usd_calc, 0) AS realized_pnl_usd,
                COALESCE(lv.unrealized_pnl_usd, pa.unrealized_pnl_usd_calc, 0) AS unrealized_pnl_usd,
                lv.cash_usd,
                COALESCE(
                    lv.equity_usd,
                    COALESCE(lv.cash_usd, 0) + COALESCE(lv.positions_value_usd, pa.positions_value_usd_calc, 0)
                ) AS equity_usd,
                COALESCE(pa.positions_count, 0) AS positions_count
            FROM portfolio.trading_accounts a
            JOIN core.providers p ON p.provider_id = a.provider_id
            LEFT JOIN latest_vals lv ON lv.account_id = a.account_id
            LEFT JOIN position_agg pa ON pa.account_id = a.account_id
            {where_sql}
            ORDER BY COALESCE(lv.captured_at, a.updated_at, a.created_at) DESC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/positions")
def list_portfolio_positions(
    account_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    latest_only: bool = Query(default=True),
    source: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = PortfolioPositionsQuery(
        account_id=account_id,
        outcome_id=outcome_id,
        latest_only=latest_only,
        source=source,
        limit=limit,
        offset=offset,
    )
    conditions: list[str] = []
    params: list[Any] = []
    if query.account_id is not None:
        conditions.append("ps.account_id = %s")
        params.append(str(query.account_id))
    if query.outcome_id is not None:
        conditions.append("ps.outcome_id = %s")
        params.append(str(query.outcome_id))
    if query.source:
        conditions.append("ps.source = %s")
        params.append(query.source)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if query.latest_only:
        params.extend([query.limit, query.offset])
        with cursor_dict(connection) as cursor:
            cursor.execute(
                f"""
                SELECT
                    x.account_id,
                    x.outcome_id,
                    x.captured_at,
                    x.source,
                    x.size,
                    x.avg_price,
                    x.current_price,
                    x.current_value,
                    x.unrealized_pnl,
                    x.realized_pnl,
                    x.raw_json,
                    o.outcome_label,
                    o.outcome_index,
                    o.token_id,
                    m.market_id,
                    m.question AS market_question,
                    e.event_id,
                    e.canonical_slug AS event_slug
                FROM (
                    SELECT DISTINCT ON (ps.account_id, ps.outcome_id)
                        ps.account_id,
                        ps.outcome_id,
                        ps.captured_at,
                        ps.source,
                        ps.size,
                        ps.avg_price,
                        ps.current_price,
                        ps.current_value,
                        ps.unrealized_pnl,
                        ps.realized_pnl,
                        ps.raw_json
                    FROM portfolio.position_snapshots ps
                    {where_sql}
                    ORDER BY ps.account_id, ps.outcome_id, ps.captured_at DESC
                ) x
                JOIN catalog.outcomes o ON o.outcome_id = x.outcome_id
                JOIN catalog.markets m ON m.market_id = o.market_id
                JOIN catalog.events e ON e.event_id = m.event_id
                ORDER BY x.captured_at DESC
                LIMIT %s OFFSET %s;
                """,
                tuple(params),
            )
            rows = fetchall_dicts(cursor)
        return {"items": to_jsonable(rows), "count": len(rows)}

    return list_portfolio_positions_history(
        account_id=query.account_id,
        outcome_id=query.outcome_id,
        source=query.source,
        start_time=None,
        end_time=None,
        limit=query.limit,
        offset=query.offset,
        connection=connection,
    )


@router.get("/positions/history")
def list_portfolio_positions_history(
    account_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    source: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = PortfolioPositionHistoryQuery(
        account_id=account_id,
        outcome_id=outcome_id,
        source=source,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )
    conditions: list[str] = []
    params: list[Any] = []
    if query.account_id is not None:
        conditions.append("ps.account_id = %s")
        params.append(str(query.account_id))
    if query.outcome_id is not None:
        conditions.append("ps.outcome_id = %s")
        params.append(str(query.outcome_id))
    if query.source:
        conditions.append("ps.source = %s")
        params.append(query.source)
    if query.start_time is not None:
        conditions.append("ps.captured_at >= %s")
        params.append(query.start_time)
    if query.end_time is not None:
        conditions.append("ps.captured_at <= %s")
        params.append(query.end_time)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    params.extend([query.limit, query.offset])
    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                ps.account_id,
                ps.outcome_id,
                ps.captured_at,
                ps.source,
                ps.size,
                ps.avg_price,
                ps.current_price,
                ps.current_value,
                ps.unrealized_pnl,
                ps.realized_pnl,
                ps.raw_json,
                o.outcome_label,
                o.outcome_index,
                o.token_id,
                m.market_id,
                m.question AS market_question,
                e.event_id,
                e.canonical_slug AS event_slug
            FROM portfolio.position_snapshots ps
            JOIN catalog.outcomes o ON o.outcome_id = ps.outcome_id
            JOIN catalog.markets m ON m.market_id = o.market_id
            JOIN catalog.events e ON e.event_id = m.event_id
            {where_sql}
            ORDER BY ps.captured_at DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/orders")
def list_portfolio_orders(
    account_id: UUID | None = Query(default=None),
    market_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    side: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        conditions.append("o.account_id = %s")
        params.append(str(account_id))
    if market_id is not None:
        conditions.append("o.market_id = %s")
        params.append(str(market_id))
    if outcome_id is not None:
        conditions.append("o.outcome_id = %s")
        params.append(str(outcome_id))
    if status_filter:
        conditions.append("o.status = %s")
        params.append(status_filter)
    if side:
        conditions.append("o.side = %s")
        params.append(side)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                o.order_id,
                o.account_id,
                o.market_id,
                o.outcome_id,
                o.external_order_id,
                o.client_order_id,
                o.side,
                o.order_type,
                o.time_in_force,
                o.limit_price,
                o.size,
                o.status,
                o.placed_at,
                o.updated_at,
                o.metadata_json,
                m.question AS market_question,
                oc.outcome_label,
                e.event_id,
                e.canonical_slug AS event_slug
            FROM portfolio.orders o
            JOIN catalog.markets m ON m.market_id = o.market_id
            LEFT JOIN catalog.outcomes oc ON oc.outcome_id = o.outcome_id
            JOIN catalog.events e ON e.event_id = m.event_id
            {where_sql}
            ORDER BY o.updated_at DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/orders/{order_id}")
def get_portfolio_order(
    order_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                o.order_id,
                o.account_id,
                o.market_id,
                o.outcome_id,
                o.external_order_id,
                o.client_order_id,
                o.side,
                o.order_type,
                o.time_in_force,
                o.limit_price,
                o.size,
                o.status,
                o.placed_at,
                o.updated_at,
                o.metadata_json,
                m.question AS market_question,
                oc.outcome_label,
                e.event_id,
                e.canonical_slug AS event_slug
            FROM portfolio.orders o
            JOIN catalog.markets m ON m.market_id = o.market_id
            LEFT JOIN catalog.outcomes oc ON oc.outcome_id = o.outcome_id
            JOIN catalog.events e ON e.event_id = m.event_id
            WHERE o.order_id = %s;
            """,
            (str(order_id),),
        )
        order_row = fetchone_dict(cursor)
        if order_row is None:
            raise HTTPException(status_code=404, detail="order_id not found")

        cursor.execute(
            """
            SELECT
                order_event_id,
                order_id,
                event_time,
                event_type,
                filled_size_delta,
                filled_notional_delta,
                raw_json
            FROM portfolio.order_events
            WHERE order_id = %s
            ORDER BY event_time DESC;
            """,
            (str(order_id),),
        )
        events = fetchall_dicts(cursor)

        cursor.execute(
            """
            SELECT
                trade_id,
                account_id,
                order_id,
                market_id,
                outcome_id,
                external_trade_id,
                tx_hash,
                side,
                price,
                size,
                fee,
                fee_asset,
                liquidity_role,
                trade_time,
                raw_json
            FROM portfolio.trades
            WHERE order_id = %s
            ORDER BY trade_time DESC;
            """,
            (str(order_id),),
        )
        trades = fetchall_dicts(cursor)

    return {
        "order": to_jsonable(order_row),
        "events": to_jsonable(events),
        "trades": to_jsonable(trades),
    }


@router.get("/trades")
def list_portfolio_trades(
    account_id: UUID | None = Query(default=None),
    market_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    order_id: UUID | None = Query(default=None),
    side: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        conditions.append("t.account_id = %s")
        params.append(str(account_id))
    if market_id is not None:
        conditions.append("t.market_id = %s")
        params.append(str(market_id))
    if outcome_id is not None:
        conditions.append("t.outcome_id = %s")
        params.append(str(outcome_id))
    if order_id is not None:
        conditions.append("t.order_id = %s")
        params.append(str(order_id))
    if side:
        conditions.append("t.side = %s")
        params.append(side)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                t.trade_id,
                t.account_id,
                t.order_id,
                t.market_id,
                t.outcome_id,
                t.external_trade_id,
                t.tx_hash,
                t.side,
                t.price,
                t.size,
                t.fee,
                t.fee_asset,
                t.liquidity_role,
                t.trade_time,
                t.raw_json,
                m.question AS market_question,
                oc.outcome_label,
                e.event_id,
                e.canonical_slug AS event_slug
            FROM portfolio.trades t
            JOIN catalog.markets m ON m.market_id = t.market_id
            LEFT JOIN catalog.outcomes oc ON oc.outcome_id = t.outcome_id
            JOIN catalog.events e ON e.event_id = m.event_id
            {where_sql}
            ORDER BY t.trade_time DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/orders", response_model=ManualOrderResponse, status_code=status.HTTP_201_CREATED)
def create_manual_order(
    payload: ManualOrderCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> ManualOrderResponse:
    limits = load_order_risk_limits()
    enforce_order_risk_limits(
        size=payload.size,
        limit_price=payload.limit_price,
        order_type=payload.order_type,
        limits=limits,
    )
    enforce_order_rate_limit(
        account_id=payload.account_id,
        action="place_order",
        max_ops_per_minute=limits.max_ops_per_minute,
    )

    account = _fetch_account_wallet(connection, account_id=str(payload.account_id))
    _ensure_market_exists(connection, market_id=str(payload.market_id))
    resolved_outcome_id = _validate_market_outcome_relation(
        connection,
        market_id=str(payload.market_id),
        outcome_id=str(payload.outcome_id) if payload.outcome_id is not None else None,
    )

    repo = JanusUpsertRepository(connection)
    now = datetime.now(timezone.utc)
    order_id = str(uuid4())
    event_type = "manual_place_dry_run"
    order_status = "open"
    external_order_id: str | None = None
    execution_payload: dict[str, Any] = {"dry_run": payload.dry_run}

    if not payload.dry_run:
        if resolved_outcome_id is None:
            raise HTTPException(status_code=422, detail="outcome_id is required when dry_run=false")

        with cursor_dict(connection) as cursor:
            cursor.execute(
                "SELECT token_id FROM catalog.outcomes WHERE outcome_id = %s;",
                (resolved_outcome_id,),
            )
            outcome_row = fetchone_dict(cursor)
        token_id = str((outcome_row or {}).get("token_id") or "").strip()
        if not token_id:
            raise HTTPException(status_code=422, detail="outcome token_id is required for live order placement")

        creds = PolymarketCredentials.from_env()
        wallet = str(account.get("wallet_address") or "").strip()
        proxy_wallet = str(account.get("proxy_wallet_address") or "").strip()
        if wallet:
            creds.wallet_address = wallet
        if proxy_wallet:
            creds.funder_address = proxy_wallet
        elif wallet:
            creds.funder_address = wallet

        place_result = place_new_order(
            creds,
            PlaceOrderRequest(
                market_id=str(payload.market_id),
                token_id=token_id,
                side=OrderSide.BUY if payload.side == "buy" else OrderSide.SELL,
                size=float(payload.size or 0.0),
                price=float(payload.limit_price or 0.0),
                order_type=OrderType.LIMIT if payload.order_type == "limit" else OrderType.MARKET,
            ),
        )
        execution_payload["clob_response"] = to_jsonable(place_result.raw)
        external_order_id = _extract_external_order_id(place_result.raw)
        if place_result.success:
            order_status = "submitted"
            event_type = "manual_place_submitted"
        else:
            order_status = "submit_error"
            event_type = "manual_place_failed"

    repo.upsert_order(
        order_id=order_id,
        account_id=str(payload.account_id),
        market_id=str(payload.market_id),
        outcome_id=resolved_outcome_id,
        side=payload.side,
        order_type=payload.order_type,
        status=order_status,
        placed_at=now,
        updated_at=now,
        external_order_id=external_order_id,
        client_order_id=None,
        time_in_force=payload.time_in_force,
        limit_price=payload.limit_price,
        size=payload.size,
        metadata_json={
            "request_metadata": payload.metadata_json,
            "execution": execution_payload,
        },
    )
    repo.insert_order_event(
        order_event_id=str(uuid4()),
        order_id=order_id,
        event_time=now,
        event_type=event_type,
        filled_size_delta=None,
        filled_notional_delta=None,
        raw_json=execution_payload,
        ignore_duplicates=True,
    )
    return ManualOrderResponse(
        order_id=UUID(order_id),
        status=order_status,
        event_type=event_type,
        external_order_id=external_order_id,
        dry_run=payload.dry_run,
        summary={
            "account_id": str(payload.account_id),
            "market_id": str(payload.market_id),
            "outcome_id": resolved_outcome_id,
            "risk_limits": to_jsonable(limits.__dict__),
        },
    )


@router.delete("/orders/{order_id}", response_model=ManualOrderResponse)
def cancel_manual_order(
    order_id: UUID,
    payload: ManualOrderCancelRequest = Body(default_factory=ManualOrderCancelRequest),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> ManualOrderResponse:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT order_id, account_id, external_order_id, status, metadata_json
            FROM portfolio.orders
            WHERE order_id = %s;
            """,
            (str(order_id),),
        )
        order_row = fetchone_dict(cursor)
    if order_row is None:
        raise HTTPException(status_code=404, detail="order_id not found")

    account_uuid = UUID(str(order_row["account_id"]))
    if payload.account_id is not None and payload.account_id != account_uuid:
        raise HTTPException(status_code=403, detail="account_id does not match order owner")

    limits = load_order_risk_limits()
    enforce_order_rate_limit(
        account_id=account_uuid,
        action="cancel_order",
        max_ops_per_minute=limits.max_ops_per_minute,
    )

    order_status = "canceled"
    event_type = "manual_cancel_dry_run"
    execution_payload: dict[str, Any] = {
        "dry_run": payload.dry_run,
        "reason": payload.reason,
    }
    external_order_id = str(order_row.get("external_order_id") or "") or None

    if not payload.dry_run:
        if not external_order_id:
            raise HTTPException(status_code=422, detail="external_order_id missing for non-dry cancel")
        account = _fetch_account_wallet(connection, account_id=str(account_uuid))
        creds = PolymarketCredentials.from_env()
        wallet = str(account.get("wallet_address") or "").strip()
        proxy_wallet = str(account.get("proxy_wallet_address") or "").strip()
        if wallet:
            creds.wallet_address = wallet
        if proxy_wallet:
            creds.funder_address = proxy_wallet
        elif wallet:
            creds.funder_address = wallet

        cancel_result = cancel_order(creds, external_order_id)
        execution_payload["clob_response"] = to_jsonable(cancel_result.raw)
        if cancel_result.success:
            event_type = "manual_cancel_submitted"
            order_status = "canceled"
        else:
            event_type = "manual_cancel_failed"
            order_status = "cancel_error"

    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE portfolio.orders
            SET status = %s, updated_at = %s
            WHERE order_id = %s;
            """,
            (order_status, now, str(order_id)),
        )
    repo = JanusUpsertRepository(connection)
    repo.insert_order_event(
        order_event_id=str(uuid4()),
        order_id=str(order_id),
        event_time=now,
        event_type=event_type,
        filled_size_delta=None,
        filled_notional_delta=None,
        raw_json=execution_payload,
        ignore_duplicates=True,
    )
    return ManualOrderResponse(
        order_id=order_id,
        status=order_status,
        event_type=event_type,
        external_order_id=external_order_id,
        dry_run=payload.dry_run,
        summary={
            "account_id": str(account_uuid),
            "reason": payload.reason,
            "risk_limits": to_jsonable(limits.__dict__),
        },
    )
