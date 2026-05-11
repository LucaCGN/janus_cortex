from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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


def _decimal_or_zero(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _normalized_trade_decimal(value: Any) -> str:
    decimal_value = _decimal_or_zero(value).quantize(Decimal("0.000001"))
    return format(decimal_value.normalize(), "f")


def _normalized_trade_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return raw


def _portfolio_trade_reconciliation_key(row: dict[str, Any]) -> str:
    account_id = str(row.get("account_id") or "").strip()
    external_trade_id = str(row.get("external_trade_id") or "").strip()
    if external_trade_id:
        return "|".join(["external", account_id, external_trade_id])
    return "|".join(
        [
            "fallback",
            account_id,
            str(row.get("tx_hash") or "").strip(),
            str(row.get("market_id") or "").strip(),
            str(row.get("outcome_id") or "").strip(),
            str(row.get("side") or "").strip().lower(),
            _normalized_trade_decimal(row.get("price")),
            _normalized_trade_decimal(row.get("size")),
            _normalized_trade_timestamp(row.get("trade_time")),
        ]
    )


def _trade_signed_size(row: dict[str, Any]) -> Decimal:
    side = str(row.get("side") or "").strip().lower()
    size = _decimal_or_zero(row.get("size"))
    return -size if side == "sell" else size


def _trade_cashflow(row: dict[str, Any]) -> Decimal:
    side = str(row.get("side") or "").strip().lower()
    notional = _decimal_or_zero(row.get("price")) * _decimal_or_zero(row.get("size"))
    fee = _decimal_or_zero(row.get("fee"))
    if side == "sell":
        return notional - fee
    return -notional - fee


_OPEN_ORDER_STATUSES = {"open", "submitted", "working", "pending", "partially_filled", "partial", "pending_submit"}
_CANCELED_ORDER_STATUSES = {"canceled", "cancelled"}
_FAILED_ORDER_STATUSES = {"submit_error", "cancel_error", "rejected", "failed", "error"}
_EXPIRED_ORDER_STATUSES = {"expired"}
_UNKNOWN_LIFECYCLE_STATUSES = {
    "direct_flat_status_unknown",
    "direct_status_unknown",
    "local_open_without_external_id",
}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _metadata_text(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _nested_metadata_text(metadata: dict[str, Any], nested_key: str, *keys: str) -> str:
    nested = metadata.get(nested_key)
    if not isinstance(nested, dict):
        return ""
    return _metadata_text(nested, *keys)


def _order_actor_label(row: dict[str, Any]) -> str:
    metadata = _json_dict(row.get("metadata_json"))
    side = str(row.get("side") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    source = _metadata_text(metadata, "source", "run_id", "controller_source")
    source = source or _nested_metadata_text(metadata, "request_metadata", "source", "run_id")
    strategy_family = _metadata_text(metadata, "strategy_family", "source_strategy_family")
    strategy_family = strategy_family or _nested_metadata_text(metadata, "request_metadata", "strategy_family", "source_strategy_family")
    reaction_type = _metadata_text(metadata, "reaction_type", "reaction_owner")
    reason = _metadata_text(metadata, "reason")
    reason = reason or _nested_metadata_text(metadata, "request_metadata", "reason")
    combined = " ".join([source, strategy_family, reaction_type, reason, status]).lower()

    if "settlement" in combined or status == "settled":
        return "settlement"
    if "operator_intervention" in combined or "manual" in combined or "operator" in combined:
        return "manual_target_exit" if side == "sell" else "manual_operator"
    if strategy_family or source:
        return "janus_target_exit" if side == "sell" else "janus_strategy"
    if side == "sell":
        return "manual_target_exit"
    if side == "buy":
        return "manual_operator"
    return "unknown"


def _direct_flat_snapshot_known(
    *,
    direct_open_order_count: int | None,
    direct_open_position_count: int | None,
) -> bool:
    return direct_open_order_count == 0 and direct_open_position_count == 0


def _order_lifecycle_status(
    row: dict[str, Any],
    *,
    direct_open_order_external_ids: set[str],
    direct_open_order_count: int | None,
    direct_open_position_count: int | None,
) -> str:
    status = str(row.get("status") or "").strip().lower()
    external_order_id = str(row.get("external_order_id") or "").strip().lower()
    requested_size = _decimal_or_zero(row.get("size"))
    linked_fill_size = _decimal_or_zero(row.get("linked_fill_size"))
    if external_order_id and external_order_id in direct_open_order_external_ids:
        return "direct_live"
    if linked_fill_size > Decimal("0"):
        if requested_size > Decimal("0") and linked_fill_size + Decimal("0.000001") >= requested_size:
            return "filled"
        return "partially_filled"
    if status in _CANCELED_ORDER_STATUSES:
        return "canceled"
    if status in _EXPIRED_ORDER_STATUSES:
        return "expired"
    if status in _FAILED_ORDER_STATUSES:
        return status
    if status in _OPEN_ORDER_STATUSES:
        if not external_order_id:
            return "local_open_without_external_id"
        if _direct_flat_snapshot_known(
            direct_open_order_count=direct_open_order_count,
            direct_open_position_count=direct_open_position_count,
        ):
            return "direct_flat_status_unknown"
        return "direct_status_unknown"
    return status or "unknown"


def build_order_lifecycle_reconciliation_report(
    rows: list[dict[str, Any]],
    *,
    direct_open_order_external_ids: list[str] | None = None,
    direct_open_order_count: int | None = None,
    direct_open_position_count: int | None = None,
) -> dict[str, Any]:
    direct_ids = {str(item or "").strip().lower() for item in direct_open_order_external_ids or [] if str(item or "").strip()}
    if direct_open_order_count is None and direct_ids:
        direct_open_order_count = len(direct_ids)

    lifecycle_counts: dict[str, int] = {}
    actor_summary: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    external_order_count = 0
    linked_order_count = 0
    linked_trade_count = 0
    unknown_lifecycle_count = 0

    for row in rows:
        external_order_id = str(row.get("external_order_id") or "").strip()
        if external_order_id:
            external_order_count += 1
        row_lifecycle = _order_lifecycle_status(
            row,
            direct_open_order_external_ids=direct_ids,
            direct_open_order_count=direct_open_order_count,
            direct_open_position_count=direct_open_position_count,
        )
        lifecycle_counts[row_lifecycle] = lifecycle_counts.get(row_lifecycle, 0) + 1
        linked_count = int(row.get("linked_trade_count") or 0)
        if linked_count:
            linked_order_count += 1
            linked_trade_count += linked_count
        linked_fill_size = _decimal_or_zero(row.get("linked_fill_size"))
        linked_cashflow_usd = _decimal_or_zero(row.get("linked_cashflow_usd"))
        linked_fee_usd = _decimal_or_zero(row.get("linked_fee_usd"))
        actor = _order_actor_label(row)
        actor_bucket = actor_summary.setdefault(
            actor,
            {
                "order_count": 0,
                "linked_order_count": 0,
                "linked_trade_count": 0,
                "linked_fill_size": Decimal("0"),
                "linked_cashflow_usd": Decimal("0"),
                "linked_fee_usd": Decimal("0"),
                "unknown_lifecycle_count": 0,
            },
        )
        actor_bucket["order_count"] += 1
        actor_bucket["linked_order_count"] += 1 if linked_count else 0
        actor_bucket["linked_trade_count"] += linked_count
        actor_bucket["linked_fill_size"] += linked_fill_size
        actor_bucket["linked_cashflow_usd"] += linked_cashflow_usd
        actor_bucket["linked_fee_usd"] += linked_fee_usd
        if row_lifecycle in _UNKNOWN_LIFECYCLE_STATUSES:
            unknown_lifecycle_count += 1
            actor_bucket["unknown_lifecycle_count"] += 1

        items.append(
            {
                "order_id": row.get("order_id"),
                "account_id": row.get("account_id"),
                "market_id": row.get("market_id"),
                "outcome_id": row.get("outcome_id"),
                "event_slug": row.get("event_slug"),
                "external_order_id": external_order_id or None,
                "side": row.get("side"),
                "limit_price": row.get("limit_price"),
                "size": row.get("size"),
                "status": row.get("status"),
                "placed_at": row.get("placed_at"),
                "updated_at": row.get("updated_at"),
                "actor_label": actor,
                "lifecycle_status": row_lifecycle,
                "linked_trade_count": linked_count,
                "linked_trade_ids": row.get("linked_trade_ids") or [],
                "linked_fill_size": linked_fill_size,
                "linked_cashflow_usd": linked_cashflow_usd,
                "linked_fee_usd": linked_fee_usd,
                "missing_evidence": row_lifecycle in _UNKNOWN_LIFECYCLE_STATUSES,
            }
        )

    return {
        "order_count": len(rows),
        "external_order_count": external_order_count,
        "linked_order_count": linked_order_count,
        "linked_trade_count": linked_trade_count,
        "unknown_lifecycle_count": unknown_lifecycle_count,
        "pnl_attribution_ready": unknown_lifecycle_count == 0,
        "lifecycle_status_counts": dict(sorted(lifecycle_counts.items())),
        "actor_summary": {key: actor_summary[key] for key in sorted(actor_summary)},
        "direct_context": {
            "open_order_external_ids": sorted(direct_ids),
            "open_order_count": direct_open_order_count,
            "open_position_count": direct_open_position_count,
            "direct_flat_snapshot": _direct_flat_snapshot_known(
                direct_open_order_count=direct_open_order_count,
                direct_open_position_count=direct_open_position_count,
            ),
        },
        "items": items,
    }


def build_trade_reconciliation_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _portfolio_trade_reconciliation_key(row)
        group = groups.setdefault(
            key,
            {
                "dedupe_key": key,
                "raw_count": 0,
                "representative": row,
                "trade_ids": [],
            },
        )
        group["raw_count"] += 1
        if row.get("trade_id") is not None:
            group["trade_ids"].append(str(row.get("trade_id")))

    unique_rows = [group["representative"] for group in groups.values()]
    duplicate_groups = [group for group in groups.values() if int(group["raw_count"]) > 1]
    net_position_size = sum((_trade_signed_size(row) for row in unique_rows), Decimal("0"))
    net_cashflow_usd = sum((_trade_cashflow(row) for row in unique_rows), Decimal("0"))
    buy_notional_usd = sum(
        (
            _decimal_or_zero(row.get("price")) * _decimal_or_zero(row.get("size"))
            for row in unique_rows
            if str(row.get("side") or "").strip().lower() == "buy"
        ),
        Decimal("0"),
    )
    sell_notional_usd = sum(
        (
            _decimal_or_zero(row.get("price")) * _decimal_or_zero(row.get("size"))
            for row in unique_rows
            if str(row.get("side") or "").strip().lower() == "sell"
        ),
        Decimal("0"),
    )
    grouped_items = []
    for group in sorted(groups.values(), key=lambda item: str(item["dedupe_key"])):
        representative = group["representative"]
        grouped_items.append(
            {
                "dedupe_key": group["dedupe_key"],
                "raw_count": group["raw_count"],
                "duplicate_count": max(0, int(group["raw_count"]) - 1),
                "trade_ids": group["trade_ids"],
                "representative_trade_id": representative.get("trade_id"),
                "account_id": representative.get("account_id"),
                "market_id": representative.get("market_id"),
                "outcome_id": representative.get("outcome_id"),
                "event_slug": representative.get("event_slug"),
                "side": representative.get("side"),
                "price": representative.get("price"),
                "size": representative.get("size"),
                "trade_time": representative.get("trade_time"),
                "tx_hash": representative.get("tx_hash"),
                "external_trade_id": representative.get("external_trade_id"),
                "signed_size": _trade_signed_size(representative),
                "cashflow_usd": _trade_cashflow(representative),
            }
        )
    return {
        "raw_count": len(rows),
        "unique_count": len(unique_rows),
        "duplicate_count": max(0, len(rows) - len(unique_rows)),
        "duplicate_group_count": len(duplicate_groups),
        "net_position_size": net_position_size,
        "buy_notional_usd": buy_notional_usd,
        "sell_notional_usd": sell_notional_usd,
        "net_cashflow_usd": net_cashflow_usd,
        "flat_after_deduplication": net_position_size == Decimal("0"),
        "groups": grouped_items,
    }


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
    market_id: UUID | None = Query(default=None),
    latest_only: bool = Query(default=True),
    source: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    query = PortfolioPositionsQuery(
        account_id=account_id,
        outcome_id=outcome_id,
        market_id=market_id,
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
    if query.market_id is not None:
        conditions.append("ps.outcome_id IN (SELECT outcome_id FROM catalog.outcomes WHERE market_id = %s)")
        params.append(str(query.market_id))
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
    market_id: UUID | None = Query(default=None),
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
        market_id=market_id,
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
    if query.market_id is not None:
        conditions.append("ps.outcome_id IN (SELECT outcome_id FROM catalog.outcomes WHERE market_id = %s)")
        params.append(str(query.market_id))
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


@router.get("/orders/reconciliation")
def reconcile_portfolio_orders(
    account_id: UUID | None = Query(default=None),
    market_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    event_slug: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    direct_open_order_external_id: list[str] | None = Query(default=None),
    direct_open_order_count: int | None = Query(default=None, ge=0),
    direct_open_position_count: int | None = Query(default=None, ge=0),
    limit: int = Query(default=5000, ge=1, le=20000),
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
    if event_slug:
        conditions.append("e.canonical_slug = %s")
        params.append(event_slug)
    if start_time is not None:
        conditions.append("o.placed_at >= %s")
        params.append(start_time)
    if end_time is not None:
        conditions.append("o.placed_at <= %s")
        params.append(end_time)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

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
                oc.token_id,
                e.event_id,
                e.canonical_slug AS event_slug,
                COALESCE(tr.linked_trade_count, 0)::int AS linked_trade_count,
                COALESCE(tr.linked_fill_size, 0) AS linked_fill_size,
                COALESCE(tr.linked_cashflow_usd, 0) AS linked_cashflow_usd,
                COALESCE(tr.linked_fee_usd, 0) AS linked_fee_usd,
                COALESCE(tr.linked_trade_ids, ARRAY[]::text[]) AS linked_trade_ids
            FROM portfolio.orders o
            JOIN catalog.markets m ON m.market_id = o.market_id
            LEFT JOIN catalog.outcomes oc ON oc.outcome_id = o.outcome_id
            JOIN catalog.events e ON e.event_id = m.event_id
            LEFT JOIN LATERAL (
                SELECT
                    count(*)::int AS linked_trade_count,
                    COALESCE(sum(t.size), 0) AS linked_fill_size,
                    COALESCE(sum(COALESCE(t.fee, 0)), 0) AS linked_fee_usd,
                    COALESCE(
                        sum(
                            CASE
                                WHEN lower(COALESCE(t.side, '')) = 'sell'
                                    THEN COALESCE(t.price, 0) * COALESCE(t.size, 0) - COALESCE(t.fee, 0)
                                ELSE -(COALESCE(t.price, 0) * COALESCE(t.size, 0)) - COALESCE(t.fee, 0)
                            END
                        ),
                        0
                    ) AS linked_cashflow_usd,
                    array_agg(t.trade_id::text ORDER BY t.trade_time ASC, t.trade_id ASC) AS linked_trade_ids
                FROM portfolio.trades t
                WHERE t.order_id = o.order_id
            ) tr ON TRUE
            {where_sql}
            ORDER BY o.updated_at DESC, o.order_id ASC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    report = build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=direct_open_order_external_id,
        direct_open_order_count=direct_open_order_count,
        direct_open_position_count=direct_open_position_count,
    )
    return {
        "filters": {
            "account_id": str(account_id) if account_id is not None else None,
            "market_id": str(market_id) if market_id is not None else None,
            "outcome_id": str(outcome_id) if outcome_id is not None else None,
            "event_slug": event_slug,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        },
        "reconciliation": to_jsonable(report),
    }


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


@router.get("/trades/reconciliation")
def reconcile_portfolio_trades(
    account_id: UUID | None = Query(default=None),
    market_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    event_slug: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=20000),
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
    if event_slug:
        conditions.append("e.canonical_slug = %s")
        params.append(event_slug)
    if start_time is not None:
        conditions.append("t.trade_time >= %s")
        params.append(start_time)
    if end_time is not None:
        conditions.append("t.trade_time <= %s")
        params.append(end_time)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

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
            ORDER BY t.trade_time ASC, t.trade_id ASC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    report = build_trade_reconciliation_report(rows)
    return {
        "filters": {
            "account_id": str(account_id) if account_id is not None else None,
            "market_id": str(market_id) if market_id is not None else None,
            "outcome_id": str(outcome_id) if outcome_id is not None else None,
            "event_slug": event_slug,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        },
        "reconciliation": to_jsonable(report),
    }


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
