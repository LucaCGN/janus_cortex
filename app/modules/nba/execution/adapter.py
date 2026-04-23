from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.polymarket.blockchain.manage_portfolio import (
    OrderSide,
    OrderType,
    PlaceOrderRequest,
    PolymarketCredentials,
    cancel_order,
    place_new_order,
)
from app.data.nodes.polymarket.blockchain.stream_orderbook import fetch_orderbook
from app.data.pipelines.daily.polymarket.sync_portfolio import run_portfolio_mirror_sync


POLYMARKET_PROVIDER_CODE = "polymarket"
_PROVIDER_NAMESPACE = uuid.UUID("41395777-ed5f-474f-a5b7-c97567f5ca56")
_ACCOUNT_NAMESPACE = uuid.UUID("0b28a9b6-a9a7-4f05-a4f9-9925434fd1e0")
OPEN_ORDER_STATUSES = {"open", "submitted", "working", "pending"}


def _provider_uuid_for(code: str) -> str:
    return str(uuid.uuid5(_PROVIDER_NAMESPACE, code.strip().lower()))


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


def _resolve_provider_id(connection: PsycopgConnection, *, provider_code: str) -> str:
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
        base_url="https://polymarket.com",
        auth_type="wallet",
        is_active=True,
    )


def _account_id_for_wallet(wallet_address: str, proxy_wallet_address: str | None) -> str:
    key = f"{wallet_address.strip().lower()}::{str(proxy_wallet_address or '').strip().lower()}"
    return str(uuid.uuid5(_ACCOUNT_NAMESPACE, key))


def _fetch_account(connection: PsycopgConnection, *, account_id: str) -> dict[str, Any] | None:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                a.account_id,
                a.account_label,
                a.wallet_address,
                a.proxy_wallet_address,
                a.chain_id,
                a.is_active,
                p.code AS provider_code
            FROM portfolio.trading_accounts a
            JOIN core.providers p ON p.provider_id = a.provider_id
            WHERE a.account_id = %s
            LIMIT 1;
            """,
            (account_id,),
        )
        row = fetchone_dict(cursor)
    return row


def resolve_trading_account(
    connection: PsycopgConnection,
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    if account_id:
        row = _fetch_account(connection, account_id=account_id)
        if row is None:
            raise ValueError(f"Trading account not found: {account_id}")
        if not bool(row.get("is_active", True)):
            raise ValueError(f"Trading account is inactive: {account_id}")
        return row

    creds = PolymarketCredentials.from_env()
    wallet_address = str(creds.wallet_address or "").strip()
    if not wallet_address:
        raise ValueError("Polymarket wallet address is not configured in env")
    proxy_wallet_address = str(creds.funder_address or "").strip() or None
    resolved_account_id = _account_id_for_wallet(wallet_address, proxy_wallet_address)

    existing = _fetch_account(connection, account_id=resolved_account_id)
    if existing is not None:
        return existing

    provider_id = _resolve_provider_id(connection, provider_code=POLYMARKET_PROVIDER_CODE)
    repo = JanusUpsertRepository(connection)
    repo.upsert_trading_account(
        account_id=resolved_account_id,
        provider_id=provider_id,
        account_label="Polymarket Live",
        wallet_address=wallet_address,
        proxy_wallet_address=proxy_wallet_address,
        chain_id=int(creds.chain_id or 137),
        is_active=True,
    )
    created = _fetch_account(connection, account_id=resolved_account_id)
    if created is None:
        raise RuntimeError("Failed to provision default Polymarket trading account")
    return created


def build_live_creds(account: dict[str, Any]) -> PolymarketCredentials:
    creds = PolymarketCredentials.from_env()
    wallet = str(account.get("wallet_address") or "").strip()
    proxy_wallet = str(account.get("proxy_wallet_address") or "").strip()
    if wallet:
        creds.wallet_address = wallet
    if proxy_wallet:
        creds.funder_address = proxy_wallet
    elif wallet:
        creds.funder_address = wallet
    return creds


def mirror_account_state(connection: PsycopgConnection, *, account: dict[str, Any]) -> dict[str, Any]:
    wallet = str(account.get("wallet_address") or "").strip()
    if not wallet:
        return {"mirrored": False, "reason": "wallet_missing"}
    summary = run_portfolio_mirror_sync(wallet_address=wallet)
    return to_jsonable(summary.__dict__ if hasattr(summary, "__dict__") else summary)


def resolve_minimum_order_size(price: float) -> float:
    safe_price = max(0.01, float(price))
    return round(max(5.0, 1.0 / safe_price), 4)


def fetch_latest_orderbook_summary(
    *,
    creds: PolymarketCredentials,
    market_id: str,
    token_id: str,
) -> dict[str, Any]:
    snapshot = fetch_orderbook(creds=creds, token_id=token_id, market_id=market_id)
    best_bid = snapshot.bids[0].price if snapshot.bids else None
    best_ask = snapshot.asks[0].price if snapshot.asks else None
    spread_cents = None
    if best_bid is not None and best_ask is not None:
        spread_cents = round((float(best_ask) - float(best_bid)) * 100.0, 4)
    return {
        "market_id": market_id,
        "token_id": token_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_cents": spread_cents,
        "bid_size": snapshot.bids[0].size if snapshot.bids else None,
        "ask_size": snapshot.asks[0].size if snapshot.asks else None,
        "timestamp": snapshot.timestamp.isoformat(),
    }


def create_live_order(
    connection: PsycopgConnection,
    *,
    account: dict[str, Any],
    market_id: str,
    outcome_id: str,
    token_id: str,
    side: str,
    size: float,
    price: float,
    order_type: str,
    metadata_json: dict[str, Any],
    dry_run: bool,
    time_in_force: str = "gtc",
) -> dict[str, Any]:
    repo = JanusUpsertRepository(connection)
    now = datetime.now(timezone.utc)
    order_id = str(uuid.uuid4())
    external_order_id: str | None = None
    event_type = "live_place_dry_run"
    order_status = "open"
    execution_payload: dict[str, Any] = {
        "dry_run": dry_run,
        "request": {
            "side": side,
            "size": float(size),
            "price": float(price),
            "order_type": order_type,
        },
    }

    if not dry_run:
        creds = build_live_creds(account)
        place_result = place_new_order(
            creds,
            PlaceOrderRequest(
                market_id=str(market_id),
                token_id=str(token_id),
                side=OrderSide.BUY if str(side).lower() == "buy" else OrderSide.SELL,
                size=float(size),
                price=float(price),
                order_type=OrderType.LIMIT if str(order_type).lower() == "limit" else OrderType.MARKET,
            ),
        )
        execution_payload["clob_response"] = to_jsonable(place_result.raw)
        external_order_id = _extract_external_order_id(place_result.raw)
        if place_result.success:
            order_status = "submitted"
            event_type = "live_place_submitted"
        else:
            order_status = "submit_error"
            event_type = "live_place_failed"

    merged_metadata = dict(metadata_json)
    merged_metadata["execution"] = execution_payload
    repo.upsert_order(
        order_id=order_id,
        account_id=str(account["account_id"]),
        market_id=str(market_id),
        outcome_id=str(outcome_id),
        side=str(side).lower(),
        order_type=str(order_type).lower(),
        status=order_status,
        placed_at=now,
        updated_at=now,
        external_order_id=external_order_id,
        client_order_id=None,
        time_in_force=time_in_force,
        limit_price=float(price),
        size=float(size),
        metadata_json=merged_metadata,
    )
    repo.insert_order_event(
        order_event_id=str(uuid.uuid4()),
        order_id=order_id,
        event_time=now,
        event_type=event_type,
        filled_size_delta=None,
        filled_notional_delta=None,
        raw_json=execution_payload,
        ignore_duplicates=True,
    )
    return {
        "order_id": order_id,
        "external_order_id": external_order_id,
        "status": order_status,
        "event_type": event_type,
        "metadata_json": merged_metadata,
    }


def cancel_live_order(
    connection: PsycopgConnection,
    *,
    account: dict[str, Any],
    order_id: str,
    dry_run: bool,
    reason: str,
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT order_id, external_order_id, status, metadata_json
            FROM portfolio.orders
            WHERE order_id = %s
            LIMIT 1;
            """,
            (order_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise ValueError(f"Order not found: {order_id}")

    execution_payload: dict[str, Any] = {"dry_run": dry_run, "reason": reason}
    event_type = "live_cancel_dry_run"
    order_status = "canceled"
    external_order_id = str(row.get("external_order_id") or "").strip() or None

    if not dry_run:
        if not external_order_id:
            raise ValueError("Cannot cancel live order without external_order_id")
        creds = build_live_creds(account)
        cancel_result = cancel_order(creds, external_order_id)
        execution_payload["clob_response"] = to_jsonable(cancel_result.raw)
        if cancel_result.success:
            event_type = "live_cancel_submitted"
            order_status = "canceled"
        else:
            event_type = "live_cancel_failed"
            order_status = "cancel_error"

    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE portfolio.orders
            SET status = %s, updated_at = %s
            WHERE order_id = %s;
            """,
            (order_status, now, order_id),
        )
    repo = JanusUpsertRepository(connection)
    repo.insert_order_event(
        order_event_id=str(uuid.uuid4()),
        order_id=order_id,
        event_time=now,
        event_type=event_type,
        filled_size_delta=None,
        filled_notional_delta=None,
        raw_json=execution_payload,
        ignore_duplicates=True,
    )
    return {
        "order_id": order_id,
        "external_order_id": external_order_id,
        "status": order_status,
        "event_type": event_type,
    }


def list_run_orders(
    connection: PsycopgConnection,
    *,
    run_id: str,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [run_id]
    account_filter = ""
    if account_id:
        account_filter = "AND o.account_id = %s"
        params.append(account_id)
    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                o.order_id,
                o.account_id,
                o.market_id,
                o.outcome_id,
                o.external_order_id,
                o.side,
                o.order_type,
                o.limit_price,
                o.size,
                o.status,
                o.placed_at,
                o.updated_at,
                o.metadata_json,
                m.question AS market_question,
                oc.outcome_label
            FROM portfolio.orders o
            JOIN catalog.markets m ON m.market_id = o.market_id
            LEFT JOIN catalog.outcomes oc ON oc.outcome_id = o.outcome_id
            WHERE o.metadata_json->>'run_id' = %s
            {account_filter}
            ORDER BY o.updated_at DESC;
            """,
            tuple(params),
        )
        return fetchall_dicts(cursor)


def list_run_trades(
    connection: PsycopgConnection,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                t.trade_id,
                t.order_id,
                t.account_id,
                t.market_id,
                t.outcome_id,
                t.external_trade_id,
                t.side,
                t.price,
                t.size,
                t.fee,
                t.liquidity_role,
                t.trade_time,
                o.metadata_json
            FROM portfolio.trades t
            JOIN portfolio.orders o ON o.order_id = t.order_id
            WHERE o.metadata_json->>'run_id' = %s
            ORDER BY t.trade_time DESC;
            """,
            (run_id,),
        )
        return fetchall_dicts(cursor)


def list_latest_positions(
    connection: PsycopgConnection,
    *,
    account_id: str,
) -> list[dict[str, Any]]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                x.account_id,
                x.outcome_id,
                x.captured_at,
                x.size,
                x.avg_price,
                x.current_price,
                x.current_value,
                x.unrealized_pnl,
                x.realized_pnl,
                oc.outcome_label,
                oc.token_id,
                m.market_id,
                m.question AS market_question
            FROM (
                SELECT DISTINCT ON (ps.account_id, ps.outcome_id)
                    ps.account_id,
                    ps.outcome_id,
                    ps.captured_at,
                    ps.size,
                    ps.avg_price,
                    ps.current_price,
                    ps.current_value,
                    ps.unrealized_pnl,
                    ps.realized_pnl
                FROM portfolio.position_snapshots ps
                WHERE ps.account_id = %s
                ORDER BY ps.account_id, ps.outcome_id, ps.captured_at DESC
            ) x
            JOIN catalog.outcomes oc ON oc.outcome_id = x.outcome_id
            JOIN catalog.markets m ON m.market_id = oc.market_id
            WHERE COALESCE(x.size, 0) <> 0
            ORDER BY x.captured_at DESC;
            """,
            (account_id,),
        )
        return fetchall_dicts(cursor)


def list_active_run_signatures(
    connection: PsycopgConnection,
    *,
    run_id: str,
    execution_profile_version: str,
) -> set[tuple[str, str, str]]:
    rows = list_run_orders(connection, run_id=run_id)
    signatures: set[tuple[str, str, str]] = set()
    for row in rows:
        status = str(row.get("status") or "").lower()
        if status not in OPEN_ORDER_STATUSES:
            continue
        metadata = row.get("metadata_json") or {}
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("execution_profile_version") or "") != str(execution_profile_version):
            continue
        game_id = str(metadata.get("game_id") or "")
        outcome_id = str(metadata.get("outcome_id") or "")
        side = str(row.get("side") or "")
        if game_id and outcome_id and side:
            signatures.add((game_id, outcome_id, side))
    return signatures


def fetch_account_summary(connection: PsycopgConnection, *, account_id: str) -> dict[str, Any] | None:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
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
                WHERE vs.account_id = %s
                ORDER BY vs.account_id, vs.captured_at DESC
            )
            SELECT *
            FROM latest_vals
            LIMIT 1;
            """,
            (account_id,),
        )
        return fetchone_dict(cursor)


__all__ = [
    "OPEN_ORDER_STATUSES",
    "build_live_creds",
    "cancel_live_order",
    "create_live_order",
    "fetch_account_summary",
    "fetch_latest_orderbook_summary",
    "list_active_run_signatures",
    "list_latest_positions",
    "list_run_orders",
    "list_run_trades",
    "mirror_account_state",
    "resolve_minimum_order_size",
    "resolve_trading_account",
]
