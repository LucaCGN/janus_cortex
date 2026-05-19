from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PsycopgConnection
from psycopg2.extras import Json
from pydantic import ValidationError

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.guards import (
    OrderRiskLimits,
    enforce_order_rate_limit,
    enforce_order_risk_limits,
    load_order_risk_limits,
)
from app.api.models import (
    ManualOrderCancelRequest,
    ManualOrderCreateRequest,
    ManualOrderResponse,
    PortfolioDirectTradeBackfillRequest,
    PortfolioManagerActionLedgerRequest,
    PortfolioManagerOrderManagementRequest,
    PortfolioOrderStatusBackfillRequest,
    PortfolioPositionHistoryQuery,
    PortfolioPositionsQuery,
    PortfolioSummaryQuery,
    TradingAccountCreateRequest,
)
from app.modules.agentic.global_portfolio import GlobalPortfolioManagerActionPlan
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.polymarket.blockchain.manage_portfolio import (
    OrderSide,
    OrderType,
    PlaceOrderRequest,
    PolymarketCredentials,
    cancel_order,
    place_new_order,
    view_open_positions,
    view_orders,
    view_trades,
)


router = APIRouter(prefix="/v1/portfolio", tags=["portfolio"])

_PROVIDER_NAMESPACE = uuid.UUID("41395777-ed5f-474f-a5b7-c97567f5ca56")
_PORTFOLIO_SYNC_NAMESPACE = uuid.UUID("44ecb08d-f092-4a67-b542-c944bcf1c352")
_PORTFOLIO_MANAGER_APPROVED_EXECUTION_PATH = "janus_portfolio_order_management"
_PORTFOLIO_MANAGER_ADAPTER_NAME = "janus_portfolio_manager_order_management_v1"
_PORTFOLIO_MANAGER_RUNTIME_FLAG = "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED"


def _provider_uuid_for(code: str) -> str:
    return str(uuid.uuid5(_PROVIDER_NAMESPACE, code.strip().lower()))


def _portfolio_sync_uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_PORTFOLIO_SYNC_NAMESPACE, "|".join(parts)))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _portfolio_manager_runtime_activation_state() -> dict[str, Any]:
    enabled = _env_bool(_PORTFOLIO_MANAGER_RUNTIME_FLAG, False)
    return {
        "schema_version": "portfolio_manager_runtime_activation_v1",
        "runtime_flag": _PORTFOLIO_MANAGER_RUNTIME_FLAG,
        "enabled": enabled,
        "required_value": "true",
        "required_for_non_dry_run": True,
        "request_execution_approval_bypasses_runtime_flag": False,
        "dry_run_only_when_disabled": not enabled,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
    }


def _portfolio_manager_order_management_enabled_or_raise() -> None:
    if not _env_bool(_PORTFOLIO_MANAGER_RUNTIME_FLAG, False):
        raise HTTPException(
            status_code=403,
            detail=f"{_PORTFOLIO_MANAGER_RUNTIME_FLAG}=true is required when dry_run=false",
        )


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
_DIRECT_TRADE_ORDER_ID_FIELDS = ("taker_order_id", "maker_order_id", "order_id", "orderID", "external_order_id")
_DIRECT_ORDER_ID_FIELDS = ("id", "orderID", "orderId", "order_id", "external_order_id")


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


def _direct_trade_value(trade: Any, key: str) -> Any:
    if isinstance(trade, dict):
        return trade.get(key)
    return getattr(trade, key, None)


def _direct_trade_order_ids(trade: Any) -> set[str]:
    order_ids: set[str] = set()
    for key in _DIRECT_TRADE_ORDER_ID_FIELDS:
        value = _direct_trade_value(trade, key)
        if value is not None and str(value).strip():
            order_ids.add(str(value).strip().lower())
    return order_ids


def _direct_trade_id(trade: Any) -> str | None:
    for key in ("id", "trade_id", "external_trade_id", "hash", "tx_hash"):
        value = _direct_trade_value(trade, key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _direct_trade_identity(trade: Any) -> str:
    trade_id = _direct_trade_id(trade)
    if trade_id:
        return f"id:{trade_id.strip().lower()}"
    order_ids = sorted(_direct_trade_order_ids(trade))
    pieces = [
        "synthetic",
        ",".join(order_ids),
        str(_direct_trade_value(trade, "side") or "").strip().lower(),
        str(_direct_trade_value(trade, "price") or "").strip(),
        str(_direct_trade_value(trade, "size") or "").strip(),
        str(_direct_trade_value(trade, "fee") or "").strip(),
        str(
            _direct_trade_value(trade, "trade_time")
            or _direct_trade_value(trade, "created_at")
            or _direct_trade_value(trade, "timestamp")
            or ""
        ).strip(),
    ]
    return "|".join(pieces)


def _dedupe_direct_trade_rows(direct_trade_rows: list[Any] | None) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for trade in direct_trade_rows or []:
        identity = _direct_trade_identity(trade)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(trade)
    return unique


def _direct_item_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "model_dump") and callable(item.model_dump):
        return dict(item.model_dump())
    if hasattr(item, "dict") and callable(item.dict):
        return dict(item.dict())
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    return {"value": str(item)}


def _direct_order_external_id(order: Any) -> str | None:
    item = order if isinstance(order, dict) else _direct_item_to_dict(order)
    for key in _DIRECT_ORDER_ID_FIELDS:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _direct_order_value(order: Any, *keys: str) -> Any:
    item = order if isinstance(order, dict) else _direct_item_to_dict(order)
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _direct_order_timestamp(value: Any, *, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return default
    raw = str(value or "").strip()
    if not raw:
        return default
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return default
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _direct_order_status(order: Any) -> str:
    raw_status = str(_direct_order_value(order, "status") or "open").strip().lower()
    return {
        "live": "open",
        "opened": "open",
        "partial": "partially_filled",
    }.get(raw_status, raw_status or "open")


def _direct_order_side(order: Any) -> str:
    raw_side = str(_direct_order_value(order, "side") or "").strip().lower()
    return {"buy": "buy", "sell": "sell"}.get(raw_side, raw_side or "unknown")


def _load_outcome_pairs_by_token(connection: PsycopgConnection) -> dict[str, tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT token_id, outcome_id, market_id
            FROM catalog.outcomes
            WHERE token_id IS NOT NULL AND token_id <> '';
            """
        )
        return {str(token_id): (str(market_id), str(outcome_id)) for token_id, outcome_id, market_id in cursor.fetchall()}


def build_direct_open_order_mirror_plan(
    *,
    account_id: str,
    direct_open_orders: list[Any],
    token_to_pair: dict[str, tuple[str, str]],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    now = captured_at or datetime.now(timezone.utc)
    actions: list[dict[str, Any]] = []
    for order in direct_open_orders:
        item = _direct_item_to_dict(order)
        external_order_id = _direct_order_external_id(item)
        token_id = _direct_order_value(item, "token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId")
        token_text = str(token_id or "").strip()
        if not external_order_id:
            actions.append(
                {
                    "action": "review_required",
                    "reason": "missing_external_order_id",
                    "token_id": token_text or None,
                    "raw_direct_order": item,
                }
            )
            continue
        if not token_text or token_text not in token_to_pair:
            actions.append(
                {
                    "action": "review_required",
                    "reason": "missing_token_catalog_mapping",
                    "external_order_id": external_order_id,
                    "token_id": token_text or None,
                    "raw_direct_order": item,
                }
            )
            continue
        market_id, outcome_id = token_to_pair[token_text]
        price = _decimal_or_zero(_direct_order_value(item, "price"))
        size = _decimal_or_zero(_direct_order_value(item, "size", "original_size"))
        filled_size = _decimal_or_zero(_direct_order_value(item, "filled_size", "filledSize", "size_matched"))
        placed_at = _direct_order_timestamp(_direct_order_value(item, "created_at", "timestamp", "createdAt"), default=now)
        updated_at = _direct_order_timestamp(_direct_order_value(item, "updated_at", "updatedAt"), default=placed_at)
        order_id = _portfolio_sync_uuid_for("direct_open_order", account_id, external_order_id)
        actions.append(
            {
                "action": "upsert_order",
                "order_id": order_id,
                "account_id": account_id,
                "market_id": market_id,
                "outcome_id": outcome_id,
                "external_order_id": external_order_id,
                "client_order_id": None,
                "side": _direct_order_side(item),
                "order_type": "limit",
                "time_in_force": "gtc",
                "limit_price": price,
                "size": size,
                "status": _direct_order_status(item),
                "placed_at": placed_at,
                "updated_at": updated_at,
                "filled_size": filled_size,
                "filled_notional": filled_size * price,
                "token_id": token_text,
                "raw_direct_order": item,
            }
        )

    eligible = [action for action in actions if action.get("action") == "upsert_order"]
    review_required = [action for action in actions if action.get("action") == "review_required"]
    return {
        "direct_order_count": len(direct_open_orders),
        "eligible_upsert_count": len(eligible),
        "review_required_count": len(review_required),
        "actions": actions,
    }


def apply_direct_open_order_mirror_actions(
    connection: PsycopgConnection,
    *,
    actions: list[dict[str, Any]],
    reviewed_by: str | None = None,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    repo = JanusUpsertRepository(connection)
    applied: list[dict[str, Any]] = []
    for action in actions:
        if action.get("action") != "upsert_order":
            continue
        metadata = {
            "source": "direct_clob_open_order_mirror",
            "reviewed_by": reviewed_by,
            "reason": reason,
            "token_id": action.get("token_id"),
            "raw_direct_order": action.get("raw_direct_order") or {},
        }
        order_id = repo.upsert_order(
            order_id=str(action["order_id"]),
            account_id=str(action["account_id"]),
            market_id=str(action["market_id"]),
            outcome_id=str(action["outcome_id"]) if action.get("outcome_id") else None,
            side=str(action["side"]),
            order_type=str(action["order_type"]),
            status=str(action["status"]),
            placed_at=action["placed_at"],
            updated_at=action["updated_at"],
            external_order_id=str(action["external_order_id"]),
            client_order_id=action.get("client_order_id"),
            time_in_force=action.get("time_in_force"),
            limit_price=float(action["limit_price"]) if action.get("limit_price") is not None else None,
            size=float(action["size"]) if action.get("size") is not None else None,
            metadata_json=metadata,
        )
        event_inserted = repo.insert_order_event(
            order_event_id=_portfolio_sync_uuid_for(
                "direct_open_order_mirror_event",
                order_id,
                str(action.get("updated_at")),
                str(action.get("status")),
            ),
            order_id=order_id,
            event_time=action["updated_at"],
            event_type=f"direct_open_order_mirror_{action['status']}",
            filled_size_delta=float(action["filled_size"]) if action.get("filled_size") is not None else None,
            filled_notional_delta=float(action["filled_notional"]) if action.get("filled_notional") is not None else None,
            raw_json=metadata,
            ignore_duplicates=True,
        )
        applied.append(
            {
                "order_id": order_id,
                "external_order_id": action.get("external_order_id"),
                "applied": True,
                "order_event_inserted": event_inserted,
            }
        )
    return applied


def build_portfolio_manager_action_ledger_preview(
    *,
    action_plan: dict[str, Any],
    account_id: str | None = None,
) -> dict[str, Any]:
    try:
        plan = GlobalPortfolioManagerActionPlan.model_validate(action_plan)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=to_jsonable(exc.errors())) from exc

    source_plan = plan.model_dump(mode="json")
    ledger_record = to_jsonable(plan.ledger_record)
    ledger_id = _portfolio_sync_uuid_for(
        "portfolio_manager_action_ledger",
        str(account_id or ""),
        str(ledger_record.get("issue") or plan.issue),
        str(ledger_record.get("generated_at_utc") or source_plan.get("generated_at_utc") or ""),
        str(ledger_record.get("action") or plan.action),
        str(ledger_record.get("market_slug") or ""),
        str(ledger_record.get("token_id") or ""),
        str(ledger_record.get("status") or plan.status),
    )
    return {
        "ledger_id": ledger_id,
        "account_id": str(account_id) if account_id is not None else None,
        "issue": plan.issue,
        "schema_version": str(ledger_record.get("schema_version") or "global_portfolio_manager_action_ledger_v1"),
        "action": plan.action,
        "status": plan.status,
        "result": plan.gate_snapshot.result,
        "market_title": plan.gate_snapshot.market_title,
        "market_slug": plan.gate_snapshot.market_slug,
        "token_id": plan.gate_snapshot.token_id,
        "execution_authorized": plan.execution_authorized,
        "order_preparation_authorized": plan.order_preparation_authorized,
        "live_order_impact": plan.live_order_impact,
        "missing_gates": list(plan.gate_snapshot.missing_gates),
        "rejected_truth_sources": list(plan.gate_snapshot.rejected_truth_sources),
        "ledger_record": ledger_record,
        "source_plan": to_jsonable(source_plan),
        "ledger_write_only": True,
        "order_management_call_required": True,
        "no_execution_statement": plan.no_execution_statement,
        "side_effects": {
            "orders_placed": False,
            "orders_cancelled": False,
            "orders_replaced": False,
            "orders_submitted": False,
            "orders_prepared": False,
            "live_worker_started": False,
        },
    }


def build_portfolio_manager_order_management_preview(
    *,
    action_plan: dict[str, Any],
    account_id: str | None = None,
    requested_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        plan = GlobalPortfolioManagerActionPlan.model_validate(action_plan)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=to_jsonable(exc.errors())) from exc

    ledger_preview = build_portfolio_manager_action_ledger_preview(
        action_plan=action_plan,
        account_id=account_id,
    )
    requested_order_payload = dict(requested_order or {})
    gate_ready = plan.status == "ready_for_approved_order_management_call" and plan.gate_snapshot.execution_authorized
    status_value = "dry_run_order_management_preview" if gate_ready else "blocked_missing_execution_gates"
    next_step = (
        "Record the manager action ledger, then route the concrete order through a separately reviewed Janus order call."
        if gate_ready
        else "Resolve missing execution gates before any portfolio-manager order preparation."
    )

    return {
        "schema_version": "portfolio_manager_order_management_preview_v1",
        "account_id": str(account_id) if account_id is not None else None,
        "issue": plan.issue,
        "action": plan.action,
        "status": status_value,
        "runtime_activation": _portfolio_manager_runtime_activation_state(),
        "approved_order_management_call_available": True,
        "order_management_call_accepted": gate_ready,
        "concrete_adapter_proof": {
            "approved_execution_path": plan.gate_snapshot.approved_execution_path,
            "adapter_name": plan.gate_snapshot.adapter_name,
            "adapter_version": plan.gate_snapshot.adapter_version,
            "risk_budget_name": plan.gate_snapshot.risk_budget_name,
            "risk_budget": to_jsonable(plan.gate_snapshot.risk_budget),
            "minimum_order_proof": to_jsonable(plan.gate_snapshot.minimum_order_proof),
            "target_stop_rebuy_policy_detail": to_jsonable(plan.gate_snapshot.target_stop_rebuy_policy_detail),
            "kill_switch_clearance": to_jsonable(plan.gate_snapshot.kill_switch_clearance),
            "idempotency_key": plan.gate_snapshot.idempotency_key,
            "reconciliation_plan": to_jsonable(plan.gate_snapshot.reconciliation_plan),
        },
        "execution_authorized_by_gates": plan.execution_authorized,
        "order_preparation_authorized_by_gates": plan.order_preparation_authorized,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "live_order_impact": "read-only",
        "missing_gates": list(plan.gate_snapshot.missing_gates),
        "rejected_truth_sources": list(plan.gate_snapshot.rejected_truth_sources),
        "requested_order": to_jsonable(requested_order_payload),
        "manager_action_ledger": ledger_preview,
        "next_step": next_step,
        "no_execution_statement": plan.no_execution_statement,
        "side_effects": {
            "orders_placed": False,
            "orders_cancelled": False,
            "orders_replaced": False,
            "orders_submitted": False,
            "orders_prepared": False,
            "live_worker_started": False,
        },
    }


def _portfolio_manager_gate_ready_or_raise(plan: GlobalPortfolioManagerActionPlan) -> None:
    gate = plan.gate_snapshot
    if (
        plan.status != "ready_for_approved_order_management_call"
        or not plan.execution_authorized
        or not plan.order_preparation_authorized
        or not gate.execution_authorized
    ):
        missing = list(gate.missing_gates)
        detail = "portfolio-manager execution gates are not satisfied"
        if missing:
            detail = f"{detail}: {', '.join(missing)}"
        raise HTTPException(status_code=422, detail=detail)
    if gate.approved_execution_path != _PORTFOLIO_MANAGER_APPROVED_EXECUTION_PATH:
        raise HTTPException(status_code=422, detail="approved_execution_path is not the Janus portfolio order-management path")
    if gate.adapter_name != _PORTFOLIO_MANAGER_ADAPTER_NAME:
        raise HTTPException(status_code=422, detail="adapter_name is not the approved portfolio-manager adapter")


def _required_uuid_text(value: Any, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail=f"{field_name} is required")
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a UUID") from exc


def _required_decimal(value: Any, field_name: str) -> Decimal:
    if value is None:
        raise HTTPException(status_code=422, detail=f"{field_name} is required")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be numeric") from exc
    if not decimal_value.is_finite():
        raise HTTPException(status_code=422, detail=f"{field_name} must be finite")
    return decimal_value


def _optional_proof_decimal(proof: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        if proof.get(key) is not None:
            return _required_decimal(proof.get(key), f"minimum_order_proof.{key}")
    return None


def _assert_requested_decimal_matches_proof(
    *,
    field_name: str,
    requested_value: Decimal,
    proof_value: Decimal | None,
) -> None:
    if proof_value is None:
        return
    if requested_value.quantize(Decimal("0.000001")) != proof_value.quantize(Decimal("0.000001")):
        raise HTTPException(status_code=422, detail=f"requested_order.{field_name} does not match minimum_order_proof")


def _normalize_portfolio_manager_requested_order(
    *,
    plan: GlobalPortfolioManagerActionPlan,
    account_id: str | None,
    requested_order: dict[str, Any] | None,
) -> dict[str, Any]:
    if account_id is None:
        raise HTTPException(status_code=422, detail="account_id is required when dry_run=false")

    payload = dict(requested_order or {})
    proof = dict(plan.gate_snapshot.minimum_order_proof or {})
    side = str(payload.get("side") or proof.get("side") or "").strip().lower()
    if side not in {"buy", "sell"}:
        raise HTTPException(status_code=422, detail="requested_order.side must be buy or sell")
    proof_side = str(proof.get("side") or "").strip().lower()
    if proof_side and side != proof_side:
        raise HTTPException(status_code=422, detail="requested_order.side does not match minimum_order_proof")

    order_type = str(payload.get("order_type") or proof.get("order_type") or "limit").strip().lower()
    if order_type != "limit":
        raise HTTPException(status_code=422, detail="portfolio-manager live execution only supports limit orders")
    proof_order_type = str(proof.get("order_type") or "").strip().lower()
    if proof_order_type and proof_order_type != "limit":
        raise HTTPException(status_code=422, detail="minimum_order_proof.order_type must be limit")

    limit_price = _required_decimal(payload.get("limit_price", payload.get("price", proof.get("limit_price", proof.get("price")))), "requested_order.limit_price")
    if limit_price <= 0 or limit_price > 1:
        raise HTTPException(status_code=422, detail="requested_order.limit_price must be greater than 0 and at most 1")
    size = _required_decimal(payload.get("size", proof.get("size")), "requested_order.size")
    if size <= 0:
        raise HTTPException(status_code=422, detail="requested_order.size must be greater than 0")

    _assert_requested_decimal_matches_proof(
        field_name="limit_price",
        requested_value=limit_price,
        proof_value=_optional_proof_decimal(proof, "limit_price", "price"),
    )
    _assert_requested_decimal_matches_proof(
        field_name="size",
        requested_value=size,
        proof_value=_optional_proof_decimal(proof, "size"),
    )
    proof_notional = _optional_proof_decimal(proof, "notional_usd", "notional")
    requested_notional = (limit_price * size).quantize(Decimal("0.000001"))
    _assert_requested_decimal_matches_proof(
        field_name="notional_usd",
        requested_value=requested_notional,
        proof_value=proof_notional,
    )

    token_id = str(payload.get("token_id") or plan.gate_snapshot.token_id or "").strip()
    if not token_id:
        raise HTTPException(status_code=422, detail="requested_order.token_id is required")
    gate_token_id = str(plan.gate_snapshot.token_id or "").strip()
    if gate_token_id and token_id != gate_token_id:
        raise HTTPException(status_code=422, detail="requested_order.token_id does not match gate_snapshot.token_id")

    market_id = _required_uuid_text(payload.get("market_id"), "requested_order.market_id")
    outcome_id = None
    if payload.get("outcome_id") is not None:
        outcome_id = _required_uuid_text(payload.get("outcome_id"), "requested_order.outcome_id")
    time_in_force = str(payload.get("time_in_force") or "gtc").strip().lower() or "gtc"

    return {
        "account_id": account_id,
        "market_id": market_id,
        "outcome_id": outcome_id,
        "token_id": token_id,
        "side": side,
        "order_type": order_type,
        "time_in_force": time_in_force,
        "limit_price": limit_price,
        "size": size,
        "notional_usd": requested_notional,
    }


def _fetch_portfolio_manager_order_by_id(
    connection: PsycopgConnection,
    *,
    order_id: str,
) -> dict[str, Any] | None:
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
        return fetchone_dict(cursor)


def _build_portfolio_manager_runtime_risk_rate_evidence(
    *,
    normalized_order: dict[str, Any],
    limits: OrderRiskLimits,
    rate_limit_action: str,
    rate_limit_verdict: dict[str, Any],
) -> dict[str, Any]:
    size = _decimal_or_zero(normalized_order.get("size"))
    limit_price = _decimal_or_zero(normalized_order.get("limit_price"))
    notional = _decimal_or_zero(normalized_order.get("notional_usd"))
    return {
        "schema_version": "portfolio_manager_runtime_risk_rate_evidence_v1",
        "risk_limits_source": "app.api.guards.load_order_risk_limits",
        "risk_limits": {
            "max_order_size": limits.max_order_size,
            "min_limit_price": limits.min_limit_price,
            "max_limit_price": limits.max_limit_price,
            "max_notional": limits.max_notional,
            "max_ops_per_minute": limits.max_ops_per_minute,
        },
        "requested_order": {
            "side": normalized_order.get("side"),
            "order_type": normalized_order.get("order_type"),
            "time_in_force": normalized_order.get("time_in_force"),
            "limit_price": float(limit_price),
            "size": float(size),
            "notional_usd": float(notional),
        },
        "risk_checks": {
            "size_within_limit": float(size) <= float(limits.max_order_size),
            "limit_price_within_bounds": (
                float(limits.min_limit_price) <= float(limit_price) <= float(limits.max_limit_price)
            ),
            "notional_within_limit": float(notional) <= float(limits.max_notional),
            "limit_order_only": normalized_order.get("order_type") == "limit",
        },
        "rate_limit": {
            "action": rate_limit_action,
            "allowed": bool(rate_limit_verdict.get("allowed")),
            "count_in_window": int(rate_limit_verdict.get("count_in_window", 0)),
            "window_sec": int(rate_limit_verdict.get("window_sec", 60)),
            "max_ops": int(rate_limit_verdict.get("max_ops", limits.max_ops_per_minute)),
        },
    }


def _build_portfolio_manager_order_ledger_finalization(
    *,
    execution_status: str,
    order_id: str,
    external_order_id: str | None,
    event_type: str,
    execution_payload: dict[str, Any],
    side_effects: dict[str, Any],
) -> dict[str, Any]:
    if execution_status == "submitted":
        result = "execution_performed_via_approved_portfolio_manager_path"
    elif execution_status == "submit_confirmation_missing":
        result = "approved_portfolio_manager_path_submission_unconfirmed"
    else:
        result = "approved_portfolio_manager_path_submission_failed"
    proof = dict(execution_payload.get("concrete_adapter_proof") or {})
    return {
        "schema_version": "portfolio_manager_order_management_ledger_finalization_v1",
        "status": execution_status,
        "result": result,
        "order_id": order_id,
        "external_order_id": external_order_id,
        "event_type": event_type,
        "adapter_name": execution_payload.get("adapter_name"),
        "approved_execution_path": execution_payload.get("approved_execution_path"),
        "idempotency_key": execution_payload.get("idempotency_key"),
        "runtime_risk_rate_evidence": to_jsonable(execution_payload.get("runtime_risk_rate_evidence") or {}),
        "clob_response": to_jsonable(execution_payload.get("clob_response") or {}),
        "side_effects": to_jsonable(side_effects),
        "post_confirmation_reconciliation": {
            "required": True,
            "plan": to_jsonable(proof.get("reconciliation_plan")),
            "expected_external_order_id": external_order_id,
            "next_checks": [
                "direct_clob_open_order_or_fill_confirmation",
                "portfolio_order_lifecycle_reconciliation",
                "manager_action_ledger_status_review",
            ],
        },
        "transaction_preparation_attempted": False,
        "transaction_signing_attempted": False,
        "transaction_submission_attempted": False,
        "transaction_broadcast_attempted": False,
    }


def apply_portfolio_manager_order_management_order(
    connection: PsycopgConnection,
    *,
    action_plan: dict[str, Any],
    account_id: str,
    requested_order: dict[str, Any] | None,
    reviewed_by: str,
    reason: str,
) -> dict[str, Any]:
    try:
        plan = GlobalPortfolioManagerActionPlan.model_validate(action_plan)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=to_jsonable(exc.errors())) from exc

    _portfolio_manager_order_management_enabled_or_raise()
    _portfolio_manager_gate_ready_or_raise(plan)
    normalized_order = _normalize_portfolio_manager_requested_order(
        plan=plan,
        account_id=account_id,
        requested_order=requested_order,
    )

    idempotency_key = str(plan.gate_snapshot.idempotency_key or "").strip()
    if not idempotency_key:
        raise HTTPException(status_code=422, detail="gate_snapshot.idempotency_key is required")
    order_id = _portfolio_sync_uuid_for("portfolio_manager_order", account_id, idempotency_key)

    existing_order = _fetch_portfolio_manager_order_by_id(connection, order_id=order_id)
    if existing_order is not None:
        existing_status = str(existing_order.get("status") or "").strip()
        existing_external_order_id = str(existing_order.get("external_order_id") or "").strip() or None
        failed_statuses = {"submit_error", "failed", "rejected"}
        unconfirmed_statuses = {"", "submit_confirmation_missing", "pending_confirmation", "unknown"}
        if existing_status in failed_statuses:
            replay_status = "idempotency_replayed_failed"
            replay_result = "approved_portfolio_manager_path_submission_failed"
            event_type = "portfolio_manager_place_idempotency_replayed_failed"
            confirmed_external_order = False
        elif existing_external_order_id and existing_status not in unconfirmed_statuses:
            replay_status = "idempotency_replayed"
            replay_result = "execution_performed_via_approved_portfolio_manager_path"
            event_type = "portfolio_manager_place_idempotency_replayed"
            confirmed_external_order = True
        else:
            replay_status = "idempotency_replayed_unconfirmed"
            replay_result = "approved_portfolio_manager_path_submission_unconfirmed"
            event_type = "portfolio_manager_place_idempotency_replayed_unconfirmed"
            confirmed_external_order = False
        return {
            "schema_version": "portfolio_manager_order_management_execution_v1",
            "status": replay_status,
            "result": replay_result,
            "dry_run": False,
            "order_id": str(existing_order["order_id"]),
            "external_order_id": existing_external_order_id,
            "event_type": event_type,
            "ledger_applied": False,
            "execution_payload": {
                "schema_version": "portfolio_manager_order_management_idempotency_replay_v1",
                "idempotency_key": idempotency_key,
                "existing_order_status": existing_status or None,
                "existing_external_order_id": existing_external_order_id,
                "confirmed_external_order": confirmed_external_order,
                "external_order_id_required_for_confirmed_replay": True,
                "replay_result": replay_result,
                "post_confirmation_reconciliation": {
                    "required": not confirmed_external_order,
                    "expected_external_order_id": existing_external_order_id,
                    "next_checks": [
                        "direct_clob_open_order_or_fill_confirmation",
                        "portfolio_order_lifecycle_reconciliation",
                        "manager_action_ledger_status_review",
                    ],
                },
            },
            "side_effects": {
                "orders_placed": False,
                "orders_cancelled": False,
                "orders_replaced": False,
                "orders_submitted": False,
                "orders_prepared": False,
                "live_worker_started": False,
            },
        }

    limits = load_order_risk_limits()
    enforce_order_risk_limits(
        size=float(normalized_order["size"]),
        limit_price=float(normalized_order["limit_price"]),
        order_type=normalized_order["order_type"],
        limits=limits,
    )
    rate_limit_action = "portfolio_manager_place_order"
    rate_limit_verdict = enforce_order_rate_limit(
        account_id=UUID(account_id),
        action=rate_limit_action,
        max_ops_per_minute=limits.max_ops_per_minute,
    )
    runtime_risk_rate_evidence = _build_portfolio_manager_runtime_risk_rate_evidence(
        normalized_order=normalized_order,
        limits=limits,
        rate_limit_action=rate_limit_action,
        rate_limit_verdict=rate_limit_verdict,
    )

    _ensure_market_exists(connection, market_id=normalized_order["market_id"])
    resolved_outcome_id = _validate_market_outcome_relation(
        connection,
        market_id=normalized_order["market_id"],
        outcome_id=normalized_order["outcome_id"],
    )
    if resolved_outcome_id is None:
        raise HTTPException(status_code=422, detail="requested_order.outcome_id is required when dry_run=false")

    account = _fetch_account_wallet(connection, account_id=account_id)
    ledger_preview = build_portfolio_manager_action_ledger_preview(
        action_plan=action_plan,
        account_id=account_id,
    )
    ledger_applied = apply_portfolio_manager_action_ledger(
        connection,
        preview=ledger_preview,
        reviewed_by=reviewed_by,
        reason=reason,
    )

    repo = JanusUpsertRepository(connection)
    now = datetime.now(timezone.utc)
    creds = PolymarketCredentials.from_env()
    wallet = str(account.get("wallet_address") or "").strip()
    proxy_wallet = str(account.get("proxy_wallet_address") or "").strip()
    if wallet:
        creds.wallet_address = wallet
    if proxy_wallet:
        creds.funder_address = proxy_wallet
    elif wallet:
        creds.funder_address = wallet

    request = PlaceOrderRequest(
        market_id=normalized_order["market_id"],
        token_id=normalized_order["token_id"],
        side=OrderSide.BUY if normalized_order["side"] == "buy" else OrderSide.SELL,
        size=float(normalized_order["size"]),
        price=float(normalized_order["limit_price"]),
        order_type=OrderType.LIMIT,
    )
    execution_payload: dict[str, Any] = {
        "dry_run": False,
        "adapter_name": _PORTFOLIO_MANAGER_ADAPTER_NAME,
        "approved_execution_path": _PORTFOLIO_MANAGER_APPROVED_EXECUTION_PATH,
        "idempotency_key": idempotency_key,
        "reviewed_by": reviewed_by,
        "reason": reason,
        "requested_order": to_jsonable(normalized_order),
        "concrete_adapter_proof": {
            "approved_execution_path": plan.gate_snapshot.approved_execution_path,
            "adapter_name": plan.gate_snapshot.adapter_name,
            "adapter_version": plan.gate_snapshot.adapter_version,
            "risk_budget_name": plan.gate_snapshot.risk_budget_name,
            "risk_budget": to_jsonable(plan.gate_snapshot.risk_budget),
            "minimum_order_proof": to_jsonable(plan.gate_snapshot.minimum_order_proof),
            "target_stop_rebuy_policy_detail": to_jsonable(plan.gate_snapshot.target_stop_rebuy_policy_detail),
            "kill_switch_clearance": to_jsonable(plan.gate_snapshot.kill_switch_clearance),
            "reconciliation_plan": to_jsonable(plan.gate_snapshot.reconciliation_plan),
        },
        "runtime_risk_rate_evidence": runtime_risk_rate_evidence,
        "manager_action_ledger_id": ledger_applied.get("ledger_id"),
    }

    place_result = place_new_order(creds, request)
    execution_payload["clob_response"] = to_jsonable(place_result.raw)
    external_order_id = _extract_external_order_id(place_result.raw)
    if place_result.success and external_order_id:
        order_status = "submitted"
        event_type = "portfolio_manager_place_submitted"
    elif place_result.success:
        order_status = "submit_confirmation_missing"
        event_type = "portfolio_manager_place_confirmation_missing"
    else:
        order_status = "submit_error"
        event_type = "portfolio_manager_place_failed"
    side_effects = {
        "orders_placed": bool(place_result.success and external_order_id),
        "orders_cancelled": False,
        "orders_replaced": False,
        "orders_submitted": True,
        "orders_prepared": True,
        "live_worker_started": False,
    }
    ledger_finalization = _build_portfolio_manager_order_ledger_finalization(
        execution_status=order_status,
        order_id=order_id,
        external_order_id=external_order_id,
        event_type=event_type,
        execution_payload=execution_payload,
        side_effects=side_effects,
    )
    execution_payload["manager_action_ledger_finalization"] = ledger_finalization

    repo.upsert_order(
        order_id=order_id,
        account_id=account_id,
        market_id=normalized_order["market_id"],
        outcome_id=resolved_outcome_id,
        side=normalized_order["side"],
        order_type=normalized_order["order_type"],
        status=order_status,
        placed_at=now,
        updated_at=now,
        external_order_id=external_order_id,
        client_order_id=idempotency_key,
        time_in_force=normalized_order["time_in_force"],
        limit_price=float(normalized_order["limit_price"]),
        size=float(normalized_order["size"]),
        metadata_json={
            "portfolio_manager_idempotency_key": idempotency_key,
            "action_plan": to_jsonable(action_plan),
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
    ledger_finalized = finalize_portfolio_manager_action_ledger_execution(
        connection,
        ledger_id=str(ledger_applied.get("ledger_id") or ""),
        finalization=ledger_finalization,
    )

    return {
        "schema_version": "portfolio_manager_order_management_execution_v1",
        "status": order_status,
        "dry_run": False,
        "order_id": order_id,
        "external_order_id": external_order_id,
        "event_type": event_type,
        "ledger_applied": ledger_applied,
        "ledger_finalized": ledger_finalized,
        "execution_payload": execution_payload,
        "side_effects": side_effects,
    }


def apply_portfolio_manager_action_ledger(
    connection: PsycopgConnection,
    *,
    preview: dict[str, Any],
    reviewed_by: str | None,
    reason: str | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO portfolio.manager_action_ledger (
                ledger_id,
                account_id,
                issue,
                schema_version,
                action,
                status,
                result,
                market_title,
                market_slug,
                token_id,
                execution_authorized,
                order_preparation_authorized,
                live_order_impact,
                missing_gates,
                rejected_truth_sources,
                ledger_record,
                source_plan,
                reviewed_by,
                reason,
                dry_run,
                created_at,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ledger_id)
            DO UPDATE SET
                account_id = EXCLUDED.account_id,
                status = EXCLUDED.status,
                result = EXCLUDED.result,
                execution_authorized = EXCLUDED.execution_authorized,
                order_preparation_authorized = EXCLUDED.order_preparation_authorized,
                live_order_impact = EXCLUDED.live_order_impact,
                missing_gates = EXCLUDED.missing_gates,
                rejected_truth_sources = EXCLUDED.rejected_truth_sources,
                ledger_record = EXCLUDED.ledger_record,
                source_plan = EXCLUDED.source_plan,
                reviewed_by = EXCLUDED.reviewed_by,
                reason = EXCLUDED.reason,
                dry_run = EXCLUDED.dry_run,
                updated_at = EXCLUDED.updated_at
            RETURNING ledger_id;
            """,
            (
                preview["ledger_id"],
                preview.get("account_id"),
                preview["issue"],
                preview["schema_version"],
                preview["action"],
                preview["status"],
                preview["result"],
                preview.get("market_title"),
                preview.get("market_slug"),
                preview.get("token_id"),
                bool(preview["execution_authorized"]),
                bool(preview["order_preparation_authorized"]),
                preview["live_order_impact"],
                list(preview["missing_gates"]),
                list(preview["rejected_truth_sources"]),
                Json(to_jsonable(preview["ledger_record"])),
                Json(to_jsonable(preview["source_plan"])),
                reviewed_by,
                reason,
                False,
                now,
                now,
            ),
        )
        row = cursor.fetchone()
    return {
        "ledger_id": str(row[0] if isinstance(row, tuple) else row["ledger_id"]),
        "applied": True,
        "ledger_write_only": True,
        "orders_placed": False,
        "orders_cancelled": False,
        "orders_replaced": False,
        "orders_submitted": False,
        "orders_prepared": False,
    }


def finalize_portfolio_manager_action_ledger_execution(
    connection: PsycopgConnection,
    *,
    ledger_id: str,
    finalization: dict[str, Any],
) -> dict[str, Any]:
    normalized_ledger_id = str(ledger_id or "").strip()
    if not normalized_ledger_id:
        return {"ledger_id": None, "applied": False, "reason": "missing_ledger_id"}

    status_value = str(finalization.get("status") or "").strip() or "unknown"
    result_value = str(finalization.get("result") or "").strip() or "unknown"
    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE portfolio.manager_action_ledger
            SET
                status = %s,
                result = %s,
                live_order_impact = %s,
                ledger_record = jsonb_set(
                    ledger_record,
                    '{execution_confirmation}',
                    %s::jsonb,
                    true
                ),
                updated_at = %s
            WHERE ledger_id = %s
            RETURNING ledger_id;
            """,
            (
                status_value,
                result_value,
                "order-path",
                Json(to_jsonable(finalization)),
                now,
                normalized_ledger_id,
            ),
        )
        row = cursor.fetchone()
    if row is None:
        return {"ledger_id": normalized_ledger_id, "applied": False, "reason": "ledger_row_not_found"}
    return {
        "ledger_id": str(row[0] if isinstance(row, tuple) else row["ledger_id"]),
        "applied": True,
        "status": status_value,
        "result": result_value,
        "orders_placed": bool((finalization.get("side_effects") or {}).get("orders_placed")),
        "orders_submitted": bool((finalization.get("side_effects") or {}).get("orders_submitted")),
        "orders_prepared": bool((finalization.get("side_effects") or {}).get("orders_prepared")),
    }


def _credentials_for_account(account: dict[str, Any]) -> PolymarketCredentials:
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


def _fetch_direct_order_lifecycle_evidence(
    connection: PsycopgConnection,
    *,
    account_id: str,
) -> dict[str, Any]:
    account = _fetch_account_wallet(connection, account_id=account_id)
    creds = _credentials_for_account(account)
    if not creds.private_key:
        return {
            "enabled": True,
            "ok": False,
            "error": "clob_private_key_missing",
            "open_order_external_ids": [],
            "open_order_count": None,
            "open_position_count": None,
            "trade_count": None,
            "open_orders": [],
            "open_positions": [],
            "trades": [],
        }

    try:
        open_orders = view_orders(creds, open_only=True)
        open_positions = view_open_positions(creds, min_size=0.0)
        trades = view_trades(creds)
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "ok": False,
            "error": str(exc),
            "open_order_external_ids": [],
            "open_order_count": None,
            "open_position_count": None,
            "trade_count": None,
            "open_orders": [],
            "open_positions": [],
            "trades": [],
        }

    open_order_ids = [
        order_id
        for order_id in (_direct_order_external_id(order) for order in open_orders)
        if order_id is not None
    ]
    open_order_rows = [_direct_item_to_dict(order) for order in open_orders]
    open_position_rows = [_direct_item_to_dict(position) for position in open_positions]
    trade_rows = [_direct_item_to_dict(trade) for trade in trades]
    return {
        "enabled": True,
        "ok": True,
        "error": None,
        "open_order_external_ids": open_order_ids,
        "open_order_count": len(open_orders),
        "open_position_count": len(open_positions),
        "trade_count": len(trade_rows),
        "open_orders": open_order_rows,
        "open_positions": open_position_rows,
        "trades": trade_rows,
    }


def _trade_cashflow_for_order_side(
    *,
    order_side: Any,
    price: Any,
    size: Any,
    fee: Any = None,
) -> Decimal:
    side = str(order_side or "").strip().lower()
    notional = _decimal_or_zero(price) * _decimal_or_zero(size)
    fee_value = _decimal_or_zero(fee)
    if side == "sell":
        return notional - fee_value
    return -notional - fee_value


def _direct_trade_evidence_by_order_id(direct_trade_rows: list[Any] | None) -> dict[str, list[Any]]:
    evidence: dict[str, list[Any]] = {}
    for trade in _dedupe_direct_trade_rows(direct_trade_rows):
        for order_id in _direct_trade_order_ids(trade):
            evidence.setdefault(order_id, []).append(trade)
    return evidence


def _direct_trade_summary_for_order(
    row: dict[str, Any],
    direct_trades: list[Any],
) -> dict[str, Any]:
    fill_size = Decimal("0")
    cashflow_usd = Decimal("0")
    fee_usd = Decimal("0")
    trade_ids: list[str] = []
    for trade in direct_trades:
        size = _decimal_or_zero(_direct_trade_value(trade, "size"))
        price = _decimal_or_zero(_direct_trade_value(trade, "price"))
        fee = _decimal_or_zero(_direct_trade_value(trade, "fee"))
        fill_size += size
        fee_usd += fee
        cashflow_usd += _trade_cashflow_for_order_side(
            order_side=row.get("side"),
            price=price,
            size=size,
            fee=fee,
        )
        trade_id = _direct_trade_id(trade)
        if trade_id:
            trade_ids.append(trade_id)
    return {
        "direct_trade_count": len(direct_trades),
        "direct_trade_ids": trade_ids,
        "direct_fill_size": fill_size,
        "direct_cashflow_usd": cashflow_usd,
        "direct_fee_usd": fee_usd,
    }


def _effective_fill_summary(
    *,
    linked_fill_size: Decimal,
    linked_cashflow_usd: Decimal,
    linked_fee_usd: Decimal,
    direct_fill_size: Decimal,
    direct_cashflow_usd: Decimal,
    direct_fee_usd: Decimal,
) -> dict[str, Any]:
    if linked_fill_size > Decimal("0") and direct_fill_size > Decimal("0"):
        if direct_fill_size > linked_fill_size + Decimal("0.000001"):
            return {
                "fill_evidence_source": "direct_clob_trades",
                "effective_fill_size": direct_fill_size,
                "effective_cashflow_usd": direct_cashflow_usd,
                "effective_fee_usd": direct_fee_usd,
                "direct_local_fill_mismatch": True,
            }
        return {
            "fill_evidence_source": "local_and_direct_trades",
            "effective_fill_size": linked_fill_size,
            "effective_cashflow_usd": linked_cashflow_usd,
            "effective_fee_usd": linked_fee_usd,
            "direct_local_fill_mismatch": direct_fill_size + Decimal("0.000001") < linked_fill_size,
        }
    if linked_fill_size > Decimal("0"):
        return {
            "fill_evidence_source": "local_trades",
            "effective_fill_size": linked_fill_size,
            "effective_cashflow_usd": linked_cashflow_usd,
            "effective_fee_usd": linked_fee_usd,
            "direct_local_fill_mismatch": False,
        }
    if direct_fill_size > Decimal("0"):
        return {
            "fill_evidence_source": "direct_clob_trades",
            "effective_fill_size": direct_fill_size,
            "effective_cashflow_usd": direct_cashflow_usd,
            "effective_fee_usd": direct_fee_usd,
            "direct_local_fill_mismatch": False,
        }
    return {
        "fill_evidence_source": "none",
        "effective_fill_size": Decimal("0"),
        "effective_cashflow_usd": Decimal("0"),
        "effective_fee_usd": Decimal("0"),
        "direct_local_fill_mismatch": False,
    }


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
    linked_fill_size = _decimal_or_zero(row.get("effective_fill_size") or row.get("linked_fill_size"))
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
    direct_trade_rows: list[Any] | None = None,
) -> dict[str, Any]:
    direct_ids = {str(item or "").strip().lower() for item in direct_open_order_external_ids or [] if str(item or "").strip()}
    if direct_open_order_count is None and direct_ids:
        direct_open_order_count = len(direct_ids)
    raw_direct_trade_rows = list(direct_trade_rows or [])
    deduped_direct_trade_rows = _dedupe_direct_trade_rows(raw_direct_trade_rows)
    direct_duplicate_trade_count = max(0, len(raw_direct_trade_rows) - len(deduped_direct_trade_rows))
    direct_trade_evidence = _direct_trade_evidence_by_order_id(deduped_direct_trade_rows)

    lifecycle_counts: dict[str, int] = {}
    actor_summary: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    external_order_count = 0
    linked_order_count = 0
    linked_trade_count = 0
    unknown_lifecycle_count = 0
    direct_trade_matched_order_ids: set[str] = set()

    for row in rows:
        external_order_id = str(row.get("external_order_id") or "").strip()
        if external_order_id:
            external_order_count += 1
        linked_count = int(row.get("linked_trade_count") or 0)
        if linked_count:
            linked_order_count += 1
            linked_trade_count += linked_count
        linked_fill_size = _decimal_or_zero(row.get("linked_fill_size"))
        linked_cashflow_usd = _decimal_or_zero(row.get("linked_cashflow_usd"))
        linked_fee_usd = _decimal_or_zero(row.get("linked_fee_usd"))
        direct_summary = _direct_trade_summary_for_order(
            row,
            direct_trade_evidence.get(external_order_id.lower(), []),
        )
        if external_order_id and direct_summary["direct_trade_count"]:
            direct_trade_matched_order_ids.add(external_order_id.lower())
        fill_summary = _effective_fill_summary(
            linked_fill_size=linked_fill_size,
            linked_cashflow_usd=linked_cashflow_usd,
            linked_fee_usd=linked_fee_usd,
            direct_fill_size=direct_summary["direct_fill_size"],
            direct_cashflow_usd=direct_summary["direct_cashflow_usd"],
            direct_fee_usd=direct_summary["direct_fee_usd"],
        )
        row_for_status = {**row, "effective_fill_size": fill_summary["effective_fill_size"]}
        row_lifecycle = _order_lifecycle_status(
            row_for_status,
            direct_open_order_external_ids=direct_ids,
            direct_open_order_count=direct_open_order_count,
            direct_open_position_count=direct_open_position_count,
        )
        lifecycle_counts[row_lifecycle] = lifecycle_counts.get(row_lifecycle, 0) + 1
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
                "direct_trade_count": 0,
                "direct_fill_size": Decimal("0"),
                "direct_cashflow_usd": Decimal("0"),
                "direct_fee_usd": Decimal("0"),
                "effective_fill_size": Decimal("0"),
                "effective_cashflow_usd": Decimal("0"),
                "effective_fee_usd": Decimal("0"),
                "unknown_lifecycle_count": 0,
            },
        )
        actor_bucket["order_count"] += 1
        actor_bucket["linked_order_count"] += 1 if linked_count else 0
        actor_bucket["linked_trade_count"] += linked_count
        actor_bucket["linked_fill_size"] += linked_fill_size
        actor_bucket["linked_cashflow_usd"] += linked_cashflow_usd
        actor_bucket["linked_fee_usd"] += linked_fee_usd
        actor_bucket["direct_trade_count"] += direct_summary["direct_trade_count"]
        actor_bucket["direct_fill_size"] += direct_summary["direct_fill_size"]
        actor_bucket["direct_cashflow_usd"] += direct_summary["direct_cashflow_usd"]
        actor_bucket["direct_fee_usd"] += direct_summary["direct_fee_usd"]
        actor_bucket["effective_fill_size"] += fill_summary["effective_fill_size"]
        actor_bucket["effective_cashflow_usd"] += fill_summary["effective_cashflow_usd"]
        actor_bucket["effective_fee_usd"] += fill_summary["effective_fee_usd"]
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
                "direct_trade_count": direct_summary["direct_trade_count"],
                "direct_trade_ids": direct_summary["direct_trade_ids"],
                "direct_fill_size": direct_summary["direct_fill_size"],
                "direct_cashflow_usd": direct_summary["direct_cashflow_usd"],
                "direct_fee_usd": direct_summary["direct_fee_usd"],
                "fill_evidence_source": fill_summary["fill_evidence_source"],
                "effective_fill_size": fill_summary["effective_fill_size"],
                "effective_cashflow_usd": fill_summary["effective_cashflow_usd"],
                "effective_fee_usd": fill_summary["effective_fee_usd"],
                "direct_local_fill_mismatch": fill_summary["direct_local_fill_mismatch"],
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
            "trade_count": len(raw_direct_trade_rows),
            "deduped_trade_count": len(deduped_direct_trade_rows),
            "duplicate_trade_count": direct_duplicate_trade_count,
            "trade_matched_order_count": len(direct_trade_matched_order_ids),
            "trade_order_id_count": len(direct_trade_evidence),
            "direct_flat_snapshot": _direct_flat_snapshot_known(
                direct_open_order_count=direct_open_order_count,
                direct_open_position_count=direct_open_position_count,
            ),
        },
        "items": items,
    }


def _outcome_result_label(outcome_id: Any, *, final_winning_outcome_id: UUID | str | None) -> str:
    if final_winning_outcome_id is None:
        return "unknown"
    outcome_text = str(outcome_id or "").strip().lower()
    winner_text = str(final_winning_outcome_id or "").strip().lower()
    if not outcome_text:
        return "unknown"
    return "winning" if outcome_text == winner_text else "losing"


def _pnl_residual_status(
    residual_cashflow_usd: Decimal | None,
    *,
    tolerance: Decimal = Decimal("0.000001"),
) -> str:
    if residual_cashflow_usd is None:
        return "not_supplied"
    if abs(residual_cashflow_usd) <= tolerance:
        return "balanced"
    return "unexplained_residual"


def build_portfolio_pnl_attribution_report(
    report: dict[str, Any],
    *,
    opening_collateral_usd: Any = None,
    closing_collateral_usd: Any = None,
    final_winning_outcome_id: UUID | str | None = None,
) -> dict[str, Any]:
    actor_buckets: dict[str, dict[str, Any]] = {}
    known_cashflow_usd = Decimal("0")
    known_fee_usd = Decimal("0")
    lifecycle_unknown_count = int(report.get("unknown_lifecycle_count") or 0)

    for item in report.get("items", []):
        actor = str(item.get("actor_label") or "unknown")
        outcome_result = _outcome_result_label(item.get("outcome_id"), final_winning_outcome_id=final_winning_outcome_id)
        cashflow = _decimal_or_zero(item.get("effective_cashflow_usd"))
        fee = _decimal_or_zero(item.get("effective_fee_usd"))
        fill_size = _decimal_or_zero(item.get("effective_fill_size"))
        side = str(item.get("side") or "").strip().lower()
        bucket = actor_buckets.setdefault(
            actor,
            {
                "actor_label": actor,
                "order_count": 0,
                "trade_count": 0,
                "known_cashflow_usd": Decimal("0"),
                "known_fee_usd": Decimal("0"),
                "effective_fill_size": Decimal("0"),
                "buy_order_count": 0,
                "sell_order_count": 0,
                "winning_outcome_cashflow_usd": Decimal("0"),
                "losing_outcome_cashflow_usd": Decimal("0"),
                "unknown_outcome_cashflow_usd": Decimal("0"),
                "unknown_lifecycle_count": 0,
            },
        )
        bucket["order_count"] += 1
        bucket["trade_count"] += int(item.get("linked_trade_count") or item.get("direct_trade_count") or 0)
        bucket["known_cashflow_usd"] += cashflow
        bucket["known_fee_usd"] += fee
        bucket["effective_fill_size"] += fill_size
        if side == "buy":
            bucket["buy_order_count"] += 1
        elif side == "sell":
            bucket["sell_order_count"] += 1
        if outcome_result == "winning":
            bucket["winning_outcome_cashflow_usd"] += cashflow
        elif outcome_result == "losing":
            bucket["losing_outcome_cashflow_usd"] += cashflow
        else:
            bucket["unknown_outcome_cashflow_usd"] += cashflow
        if item.get("missing_evidence"):
            bucket["unknown_lifecycle_count"] += 1
        known_cashflow_usd += cashflow
        known_fee_usd += fee

    opening = _decimal_or_zero(opening_collateral_usd) if opening_collateral_usd is not None else None
    closing = _decimal_or_zero(closing_collateral_usd) if closing_collateral_usd is not None else None
    direct_collateral_delta_usd = closing - opening if opening is not None and closing is not None else None
    residual_cashflow_usd = (
        direct_collateral_delta_usd - known_cashflow_usd
        if direct_collateral_delta_usd is not None
        else None
    )
    residual_status = _pnl_residual_status(residual_cashflow_usd)
    direct_context = report.get("direct_context") or {}
    direct_final_flat = bool(direct_context.get("direct_flat_snapshot"))
    attribution_ready = lifecycle_unknown_count == 0 and direct_final_flat and residual_status in {"balanced", "not_supplied"}

    residual_bucket = None
    if residual_cashflow_usd is not None and residual_status != "balanced":
        residual_bucket = {
            "actor_label": "unknown_residual",
            "order_count": 0,
            "trade_count": 0,
            "known_cashflow_usd": residual_cashflow_usd,
            "known_fee_usd": Decimal("0"),
            "effective_fill_size": Decimal("0"),
            "buy_order_count": 0,
            "sell_order_count": 0,
            "winning_outcome_cashflow_usd": Decimal("0"),
            "losing_outcome_cashflow_usd": Decimal("0"),
            "unknown_outcome_cashflow_usd": residual_cashflow_usd,
            "unknown_lifecycle_count": 0,
            "residual_status": residual_status,
        }

    buckets = [actor_buckets[key] for key in sorted(actor_buckets)]
    if residual_bucket is not None:
        buckets.append(residual_bucket)

    return {
        "known_cashflow_usd": known_cashflow_usd,
        "known_fee_usd": known_fee_usd,
        "opening_collateral_usd": opening,
        "closing_collateral_usd": closing,
        "direct_collateral_delta_usd": direct_collateral_delta_usd,
        "residual_cashflow_usd": residual_cashflow_usd,
        "residual_status": residual_status,
        "direct_final_flat": direct_final_flat,
        "final_winning_outcome_id": str(final_winning_outcome_id) if final_winning_outcome_id is not None else None,
        "unknown_lifecycle_count": lifecycle_unknown_count,
        "pnl_attribution_ready": attribution_ready,
        "buckets": buckets,
    }


def _order_status_backfill_action(
    item: dict[str, Any],
    *,
    expire_direct_flat_open_orders: bool = False,
) -> dict[str, Any]:
    old_status = str(item.get("status") or "").strip().lower()
    lifecycle_status = str(item.get("lifecycle_status") or "").strip().lower()
    effective_fill_size = _decimal_or_zero(item.get("effective_fill_size"))
    requested_size = _decimal_or_zero(item.get("size"))
    fill_evidence_source = str(item.get("fill_evidence_source") or "none")
    base = {
        "order_id": item.get("order_id"),
        "external_order_id": item.get("external_order_id"),
        "actor_label": item.get("actor_label"),
        "old_status": old_status or None,
        "lifecycle_status": lifecycle_status or None,
        "target_status": None,
        "fill_evidence_source": fill_evidence_source,
        "effective_fill_size": effective_fill_size,
        "effective_cashflow_usd": _decimal_or_zero(item.get("effective_cashflow_usd")),
        "reason": None,
    }
    if lifecycle_status == "filled" and effective_fill_size > Decimal("0"):
        if requested_size > Decimal("0") and effective_fill_size + Decimal("0.000001") < requested_size:
            return {
                **base,
                "action": "review_required",
                "reason": "fill_size_below_requested_size",
            }
        if old_status == "filled":
            return {
                **base,
                "action": "no_update",
                "target_status": "filled",
                "reason": "status_already_matches_lifecycle",
            }
        if old_status in _OPEN_ORDER_STATUSES:
            return {
                **base,
                "action": "update_status",
                "target_status": "filled",
                "reason": "full_fill_evidence_available",
            }
        return {
            **base,
            "action": "review_required",
            "target_status": "filled",
            "reason": "non_open_status_requires_manual_review",
        }
    if lifecycle_status == "partially_filled":
        return {
            **base,
            "action": "review_required",
            "target_status": "partially_filled",
            "reason": "partial_fill_final_status_requires_manual_review",
        }
    if lifecycle_status in _UNKNOWN_LIFECYCLE_STATUSES:
        if (
            expire_direct_flat_open_orders
            and lifecycle_status == "direct_flat_status_unknown"
            and old_status in _OPEN_ORDER_STATUSES
        ):
            return {
                **base,
                "action": "update_status",
                "target_status": "expired",
                "reason": "reviewed_direct_flat_open_order_expiry",
            }
        return {
            **base,
            "action": "review_required",
            "reason": "missing_direct_fill_or_terminal_status_evidence",
        }
    return {
        **base,
        "action": "no_update",
        "target_status": lifecycle_status or None,
        "reason": "lifecycle_does_not_require_backfill",
    }


def build_order_status_backfill_plan(
    report: dict[str, Any],
    *,
    expire_direct_flat_open_orders: bool = False,
) -> dict[str, Any]:
    actions = [
        _order_status_backfill_action(
            item,
            expire_direct_flat_open_orders=expire_direct_flat_open_orders,
        )
        for item in report.get("items", [])
    ]
    action_counts: dict[str, int] = {}
    for action in actions:
        action_name = str(action.get("action") or "unknown")
        action_counts[action_name] = action_counts.get(action_name, 0) + 1
    return {
        "action_counts": dict(sorted(action_counts.items())),
        "eligible_update_count": action_counts.get("update_status", 0),
        "review_required_count": action_counts.get("review_required", 0),
        "actions": actions,
    }


def apply_order_status_backfill_actions(
    connection: PsycopgConnection,
    *,
    actions: list[dict[str, Any]],
    reviewed_by: str,
    reason: str,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    repo = JanusUpsertRepository(connection)
    applied: list[dict[str, Any]] = []
    for action in actions:
        if action.get("action") != "update_status":
            continue
        order_id = str(action.get("order_id") or "").strip()
        old_status = str(action.get("old_status") or "").strip()
        target_status = str(action.get("target_status") or "").strip()
        if not order_id or not old_status or not target_status:
            continue
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE portfolio.orders
                SET status = %s, updated_at = %s
                WHERE order_id = %s AND status = %s
                RETURNING order_id;
                """,
                (target_status, now, order_id, old_status),
            )
            updated = cursor.fetchone() is not None
        if updated:
            repo.insert_order_event(
                order_event_id=str(uuid4()),
                order_id=order_id,
                event_time=now,
                event_type="reconciliation_status_backfill_applied",
                filled_size_delta=None,
                filled_notional_delta=None,
                raw_json={
                    "reviewed_by": reviewed_by,
                    "reason": reason,
                    "old_status": old_status,
                    "target_status": target_status,
                    "lifecycle_status": action.get("lifecycle_status"),
                    "fill_evidence_source": action.get("fill_evidence_source"),
                    "effective_fill_size": to_jsonable(action.get("effective_fill_size")),
                    "effective_cashflow_usd": to_jsonable(action.get("effective_cashflow_usd")),
                    "external_order_id": action.get("external_order_id"),
                    "actor_label": action.get("actor_label"),
                },
                ignore_duplicates=True,
            )
        applied.append(
            {
                "order_id": order_id,
                "old_status": old_status,
                "target_status": target_status,
                "applied": updated,
            }
        )
    return applied


def _direct_trade_raw_value(trade: Any, *keys: str) -> Any:
    for key in keys:
        value = _direct_trade_value(trade, key)
        if value is not None and str(value).strip():
            return value
    return None


def _direct_trade_time(trade: Any, *, fallback: datetime) -> datetime:
    fallback_utc = fallback if fallback.tzinfo is not None else fallback.replace(tzinfo=timezone.utc)
    fallback_utc = fallback_utc.astimezone(timezone.utc)
    raw_value = _direct_trade_raw_value(trade, "trade_time", "timestamp", "createdAt", "created_at", "executed_at")
    if isinstance(raw_value, datetime):
        parsed = raw_value if raw_value.tzinfo is not None else raw_value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raw_text = str(raw_value or "").strip()
    if raw_text:
        try:
            numeric = Decimal(raw_text)
            if numeric > Decimal("100000000000"):
                numeric = numeric / Decimal("1000")
            if numeric > Decimal("0"):
                return datetime.fromtimestamp(float(numeric), tz=timezone.utc)
        except (InvalidOperation, ValueError, OverflowError):
            pass
        try:
            parsed = datetime.fromisoformat(raw_text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    trade_id = _direct_trade_id(trade) or str(_direct_trade_order_ids(trade))
    offset = uuid.uuid5(_PORTFOLIO_SYNC_NAMESPACE, f"direct_trade_time|{trade_id}").int % 1_000_000
    return fallback_utc + timedelta(microseconds=offset)


def _direct_trade_tx_hash(trade: Any) -> str | None:
    value = _direct_trade_raw_value(trade, "tx_hash", "transaction_hash", "transactionHash", "hash")
    return str(value).strip() if value is not None and str(value).strip() else None


def _direct_trade_liquidity_role(*, trade: Any, external_order_id: str) -> str | None:
    raw_role = _direct_trade_raw_value(trade, "liquidity_role", "liquidityRole")
    if raw_role is not None and str(raw_role).strip():
        return str(raw_role).strip().lower()
    normalized_external_id = str(external_order_id or "").strip().lower()
    if normalized_external_id:
        taker_order_id = str(_direct_trade_value(trade, "taker_order_id") or "").strip().lower()
        maker_order_id = str(_direct_trade_value(trade, "maker_order_id") or "").strip().lower()
        if normalized_external_id and normalized_external_id == taker_order_id:
            return "taker"
        if normalized_external_id and normalized_external_id == maker_order_id:
            return "maker"
    return None


def _portfolio_trade_id_for_backfill(action: dict[str, Any]) -> str:
    external_trade_id = str(action.get("external_trade_id") or "").strip()
    if external_trade_id:
        return _portfolio_sync_uuid_for("trade", external_trade_id)
    trade_time = action.get("trade_time")
    if isinstance(trade_time, datetime):
        trade_time_key = trade_time.astimezone(timezone.utc).isoformat(timespec="microseconds")
    else:
        trade_time_key = _normalized_trade_timestamp(trade_time)
    return _portfolio_sync_uuid_for(
        "trade_fallback",
        str(action.get("account_id") or ""),
        str(action.get("tx_hash") or ""),
        str(action.get("market_id") or ""),
        str(action.get("outcome_id") or ""),
        str(action.get("side") or "").strip().lower(),
        _normalized_trade_decimal(action.get("price")),
        _normalized_trade_decimal(action.get("size")),
        trade_time_key,
    )


def build_direct_trade_backfill_plan(
    report: dict[str, Any],
    *,
    direct_trade_rows: list[Any],
) -> dict[str, Any]:
    direct_trade_evidence = _direct_trade_evidence_by_order_id(direct_trade_rows)
    actions: list[dict[str, Any]] = []
    planned_trade_ids: set[str] = set()

    for item in report.get("items", []):
        external_order_id = str(item.get("external_order_id") or "").strip()
        if not external_order_id:
            continue
        direct_trades = direct_trade_evidence.get(external_order_id.lower(), [])
        if not direct_trades:
            continue
        linked_trade_count = int(item.get("linked_trade_count") or 0)
        order_id = str(item.get("order_id") or "").strip()
        account_id = str(item.get("account_id") or "").strip()
        market_id = str(item.get("market_id") or "").strip()
        side = str(item.get("side") or "").strip().lower()
        outcome_id = str(item.get("outcome_id") or "").strip() or None
        placed_at = item.get("placed_at")
        fallback_time = placed_at if isinstance(placed_at, datetime) else datetime.now(timezone.utc)

        for trade in direct_trades:
            external_trade_id = _direct_trade_id(trade)
            tx_hash = _direct_trade_tx_hash(trade)
            price = _decimal_or_zero(_direct_trade_value(trade, "price"))
            size = _decimal_or_zero(_direct_trade_value(trade, "size"))
            fee = _decimal_or_zero(_direct_trade_raw_value(trade, "fee", "fees"))
            trade_time = _direct_trade_time(trade, fallback=fallback_time)
            action: dict[str, Any] = {
                "action": "upsert_trade",
                "reason": "direct_clob_trade_missing_from_local_portfolio",
                "order_id": order_id or None,
                "external_order_id": external_order_id,
                "account_id": account_id or None,
                "market_id": market_id or None,
                "outcome_id": outcome_id,
                "event_slug": item.get("event_slug"),
                "actor_label": item.get("actor_label"),
                "side": side or None,
                "price": price,
                "size": size,
                "fee": fee,
                "fee_asset": str(_direct_trade_raw_value(trade, "fee_asset", "feeAsset") or "").strip() or None,
                "liquidity_role": _direct_trade_liquidity_role(trade=trade, external_order_id=external_order_id),
                "trade_time": trade_time,
                "external_trade_id": external_trade_id,
                "tx_hash": tx_hash,
                "cashflow_usd": _trade_cashflow_for_order_side(order_side=side, price=price, size=size, fee=fee),
                "raw_direct_trade": _direct_item_to_dict(trade),
            }
            action["trade_id"] = _portfolio_trade_id_for_backfill(action)
            trade_id = str(action["trade_id"])
            if trade_id in planned_trade_ids:
                action["action"] = "no_update"
                action["reason"] = "direct_trade_already_planned"
            elif linked_trade_count > 0 and not bool(item.get("direct_local_fill_mismatch")):
                action["action"] = "no_update"
                action["reason"] = "local_trade_already_linked"
            elif linked_trade_count > 0:
                action["action"] = "review_required"
                action["reason"] = "direct_local_fill_mismatch_requires_manual_review"
            elif not order_id or not account_id or not market_id or not side:
                action["action"] = "review_required"
                action["reason"] = "missing_local_order_trade_identity"
            elif size <= Decimal("0"):
                action["action"] = "review_required"
                action["reason"] = "direct_trade_size_missing"
            else:
                planned_trade_ids.add(trade_id)
            actions.append(action)

    action_counts: dict[str, int] = {}
    cashflow_usd = Decimal("0")
    for action in actions:
        action_name = str(action.get("action") or "unknown")
        action_counts[action_name] = action_counts.get(action_name, 0) + 1
        if action_name == "upsert_trade":
            cashflow_usd += _decimal_or_zero(action.get("cashflow_usd"))
    return {
        "action_counts": dict(sorted(action_counts.items())),
        "eligible_upsert_count": action_counts.get("upsert_trade", 0),
        "review_required_count": action_counts.get("review_required", 0),
        "planned_cashflow_usd": cashflow_usd,
        "actions": actions,
    }


def apply_direct_trade_backfill_actions(
    connection: PsycopgConnection,
    *,
    actions: list[dict[str, Any]],
    reviewed_by: str,
    reason: str,
) -> list[dict[str, Any]]:
    repo = JanusUpsertRepository(connection)
    applied: list[dict[str, Any]] = []
    for action in actions:
        if action.get("action") != "upsert_trade":
            continue
        order_id = str(action.get("order_id") or "").strip()
        trade_id = str(action.get("trade_id") or "").strip()
        if not order_id or not trade_id:
            continue
        trade_time = action.get("trade_time")
        if not isinstance(trade_time, datetime):
            trade_time = datetime.now(timezone.utc)
        raw_json = {
            "source": "reconciliation_direct_trade_backfill",
            "reviewed_by": reviewed_by,
            "reason": reason,
            "external_order_id": action.get("external_order_id"),
            "actor_label": action.get("actor_label"),
            "cashflow_usd": to_jsonable(action.get("cashflow_usd")),
            "raw_direct_trade": action.get("raw_direct_trade"),
        }
        repo.upsert_trade(
            trade_id=trade_id,
            account_id=str(action["account_id"]),
            order_id=order_id,
            market_id=str(action["market_id"]),
            outcome_id=str(action.get("outcome_id")) if action.get("outcome_id") else None,
            external_trade_id=str(action.get("external_trade_id") or "") or None,
            tx_hash=str(action.get("tx_hash") or "") or None,
            side=str(action["side"]),
            price=float(_decimal_or_zero(action.get("price"))),
            size=float(_decimal_or_zero(action.get("size"))),
            fee=float(_decimal_or_zero(action.get("fee"))),
            fee_asset=str(action.get("fee_asset") or "") or None,
            liquidity_role=str(action.get("liquidity_role") or "") or None,
            trade_time=trade_time,
            raw_json=raw_json,
        )
        event_inserted = repo.insert_order_event(
            order_event_id=_portfolio_sync_uuid_for("order_event", order_id, trade_id, "reconciliation_direct_trade_backfill"),
            order_id=order_id,
            event_time=trade_time,
            event_type=f"reconciliation_direct_trade_backfill:{trade_id}",
            filled_size_delta=float(_decimal_or_zero(action.get("size"))),
            filled_notional_delta=float(_decimal_or_zero(action.get("price")) * _decimal_or_zero(action.get("size"))),
            raw_json=raw_json,
            ignore_duplicates=True,
        )
        applied.append(
            {
                "order_id": order_id,
                "trade_id": trade_id,
                "external_trade_id": action.get("external_trade_id"),
                "applied": True,
                "order_event_inserted": event_inserted,
            }
        )
    return applied


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


def _fetch_order_lifecycle_reconciliation_rows(
    connection: PsycopgConnection,
    *,
    account_id: UUID | None = None,
    market_id: UUID | None = None,
    outcome_id: UUID | None = None,
    event_slug: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
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
        return fetchall_dicts(cursor)


def _resolve_order_lifecycle_direct_context(
    connection: PsycopgConnection,
    *,
    account_id: UUID | None,
    direct_open_order_external_id: list[str] | None,
    direct_open_order_count: int | None,
    direct_open_position_count: int | None,
    include_direct_clob_evidence: bool,
) -> dict[str, Any]:
    resolved_direct_open_order_external_ids = list(direct_open_order_external_id or [])
    resolved_direct_open_order_count = direct_open_order_count
    resolved_direct_open_position_count = direct_open_position_count
    direct_trade_rows: list[Any] = []
    direct_evidence: dict[str, Any] = {
        "enabled": include_direct_clob_evidence,
        "ok": None,
        "error": None,
    }
    if include_direct_clob_evidence:
        if account_id is None:
            direct_evidence = {
                "enabled": True,
                "ok": False,
                "error": "account_id_required",
                "open_order_count": None,
                "open_position_count": None,
                "trade_count": None,
            }
        else:
            direct_evidence = _fetch_direct_order_lifecycle_evidence(connection, account_id=str(account_id))
            if direct_evidence.get("ok"):
                direct_trade_rows = list(direct_evidence.get("trades") or [])
                seen_order_ids = {
                    str(item or "").strip().lower()
                    for item in resolved_direct_open_order_external_ids
                    if str(item or "").strip()
                }
                for order_id in direct_evidence.get("open_order_external_ids") or []:
                    normalized_order_id = str(order_id or "").strip()
                    if normalized_order_id and normalized_order_id.lower() not in seen_order_ids:
                        resolved_direct_open_order_external_ids.append(normalized_order_id)
                        seen_order_ids.add(normalized_order_id.lower())
                if resolved_direct_open_order_count is None:
                    resolved_direct_open_order_count = direct_evidence.get("open_order_count")
                if resolved_direct_open_position_count is None:
                    resolved_direct_open_position_count = direct_evidence.get("open_position_count")
    return {
        "direct_open_order_external_ids": resolved_direct_open_order_external_ids,
        "direct_open_order_count": resolved_direct_open_order_count,
        "direct_open_position_count": resolved_direct_open_position_count,
        "direct_trade_rows": direct_trade_rows,
        "direct_evidence": direct_evidence,
    }


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
    include_direct_clob_evidence: bool = Query(default=False),
    limit: int = Query(default=5000, ge=1, le=20000),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    rows = _fetch_order_lifecycle_reconciliation_rows(
        connection,
        account_id=account_id,
        market_id=market_id,
        outcome_id=outcome_id,
        event_slug=event_slug,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    direct_context = _resolve_order_lifecycle_direct_context(
        connection,
        account_id=account_id,
        direct_open_order_external_id=direct_open_order_external_id,
        direct_open_order_count=direct_open_order_count,
        direct_open_position_count=direct_open_position_count,
        include_direct_clob_evidence=include_direct_clob_evidence,
    )
    report = build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=direct_context["direct_open_order_external_ids"],
        direct_open_order_count=direct_context["direct_open_order_count"],
        direct_open_position_count=direct_context["direct_open_position_count"],
        direct_trade_rows=direct_context["direct_trade_rows"],
    )
    direct_evidence = direct_context["direct_evidence"]
    direct_evidence_summary = {key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}}
    return {
        "filters": {
            "account_id": str(account_id) if account_id is not None else None,
            "market_id": str(market_id) if market_id is not None else None,
            "outcome_id": str(outcome_id) if outcome_id is not None else None,
            "event_slug": event_slug,
            "start_time": start_time,
            "end_time": end_time,
            "include_direct_clob_evidence": include_direct_clob_evidence,
            "limit": limit,
        },
        "direct_evidence": to_jsonable(direct_evidence_summary),
        "reconciliation": to_jsonable(report),
    }


@router.get("/orders/reconciliation/pnl-attribution")
def reconcile_portfolio_order_pnl_attribution(
    account_id: UUID | None = Query(default=None),
    market_id: UUID | None = Query(default=None),
    outcome_id: UUID | None = Query(default=None),
    event_slug: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    direct_open_order_external_id: list[str] | None = Query(default=None),
    direct_open_order_count: int | None = Query(default=None, ge=0),
    direct_open_position_count: int | None = Query(default=None, ge=0),
    include_direct_clob_evidence: bool = Query(default=False),
    opening_collateral_usd: Decimal | None = Query(default=None),
    closing_collateral_usd: Decimal | None = Query(default=None),
    final_winning_outcome_id: UUID | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=20000),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    rows = _fetch_order_lifecycle_reconciliation_rows(
        connection,
        account_id=account_id,
        market_id=market_id,
        outcome_id=outcome_id,
        event_slug=event_slug,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    direct_context = _resolve_order_lifecycle_direct_context(
        connection,
        account_id=account_id,
        direct_open_order_external_id=direct_open_order_external_id,
        direct_open_order_count=direct_open_order_count,
        direct_open_position_count=direct_open_position_count,
        include_direct_clob_evidence=include_direct_clob_evidence,
    )
    report = build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=direct_context["direct_open_order_external_ids"],
        direct_open_order_count=direct_context["direct_open_order_count"],
        direct_open_position_count=direct_context["direct_open_position_count"],
        direct_trade_rows=direct_context["direct_trade_rows"],
    )
    attribution = build_portfolio_pnl_attribution_report(
        report,
        opening_collateral_usd=opening_collateral_usd,
        closing_collateral_usd=closing_collateral_usd,
        final_winning_outcome_id=final_winning_outcome_id,
    )
    direct_evidence = direct_context["direct_evidence"]
    direct_evidence_summary = {key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}}
    return {
        "filters": {
            "account_id": str(account_id) if account_id is not None else None,
            "market_id": str(market_id) if market_id is not None else None,
            "outcome_id": str(outcome_id) if outcome_id is not None else None,
            "event_slug": event_slug,
            "start_time": start_time,
            "end_time": end_time,
            "include_direct_clob_evidence": include_direct_clob_evidence,
            "opening_collateral_usd": opening_collateral_usd,
            "closing_collateral_usd": closing_collateral_usd,
            "final_winning_outcome_id": str(final_winning_outcome_id) if final_winning_outcome_id is not None else None,
            "limit": limit,
        },
        "direct_evidence": to_jsonable(direct_evidence_summary),
        "reconciliation": to_jsonable(report),
        "pnl_attribution": to_jsonable(attribution),
    }


@router.post("/orders/reconciliation/status-backfill")
def backfill_portfolio_order_statuses(
    payload: PortfolioOrderStatusBackfillRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    reviewed_by = str(payload.reviewed_by or "").strip()
    reason = str(payload.reason or "").strip()
    if not payload.dry_run and (not reviewed_by or not reason):
        raise HTTPException(status_code=422, detail="reviewed_by and reason are required when dry_run=false")

    rows = _fetch_order_lifecycle_reconciliation_rows(
        connection,
        account_id=payload.account_id,
        market_id=payload.market_id,
        outcome_id=payload.outcome_id,
        event_slug=payload.event_slug,
        start_time=payload.start_time,
        end_time=payload.end_time,
        limit=payload.limit,
    )
    direct_context = _resolve_order_lifecycle_direct_context(
        connection,
        account_id=payload.account_id,
        direct_open_order_external_id=payload.direct_open_order_external_id,
        direct_open_order_count=payload.direct_open_order_count,
        direct_open_position_count=payload.direct_open_position_count,
        include_direct_clob_evidence=payload.include_direct_clob_evidence,
    )
    report = build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=direct_context["direct_open_order_external_ids"],
        direct_open_order_count=direct_context["direct_open_order_count"],
        direct_open_position_count=direct_context["direct_open_position_count"],
        direct_trade_rows=direct_context["direct_trade_rows"],
    )
    plan = build_order_status_backfill_plan(
        report,
        expire_direct_flat_open_orders=payload.expire_direct_flat_open_orders,
    )
    applied: list[dict[str, Any]] = []
    if not payload.dry_run:
        applied = apply_order_status_backfill_actions(
            connection,
            actions=plan["actions"],
            reviewed_by=reviewed_by,
            reason=reason,
        )

    direct_evidence = direct_context["direct_evidence"]
    direct_evidence_summary = {key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}}
    return {
        "dry_run": payload.dry_run,
        "filters": {
            "account_id": str(payload.account_id),
            "market_id": str(payload.market_id) if payload.market_id is not None else None,
            "outcome_id": str(payload.outcome_id) if payload.outcome_id is not None else None,
            "event_slug": payload.event_slug,
            "start_time": payload.start_time,
            "end_time": payload.end_time,
            "include_direct_clob_evidence": payload.include_direct_clob_evidence,
            "expire_direct_flat_open_orders": payload.expire_direct_flat_open_orders,
            "limit": payload.limit,
        },
        "direct_evidence": to_jsonable(direct_evidence_summary),
        "reconciliation": to_jsonable(report),
        "status_backfill": to_jsonable(plan),
        "applied": to_jsonable(applied),
    }


@router.post("/orders/reconciliation/trade-backfill")
def backfill_portfolio_order_trades(
    payload: PortfolioDirectTradeBackfillRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    reviewed_by = str(payload.reviewed_by or "").strip()
    reason = str(payload.reason or "").strip()
    if not payload.dry_run and (not reviewed_by or not reason):
        raise HTTPException(status_code=422, detail="reviewed_by and reason are required when dry_run=false")

    rows = _fetch_order_lifecycle_reconciliation_rows(
        connection,
        account_id=payload.account_id,
        market_id=payload.market_id,
        outcome_id=payload.outcome_id,
        event_slug=payload.event_slug,
        start_time=payload.start_time,
        end_time=payload.end_time,
        limit=payload.limit,
    )
    direct_context = _resolve_order_lifecycle_direct_context(
        connection,
        account_id=payload.account_id,
        direct_open_order_external_id=payload.direct_open_order_external_id,
        direct_open_order_count=payload.direct_open_order_count,
        direct_open_position_count=payload.direct_open_position_count,
        include_direct_clob_evidence=payload.include_direct_clob_evidence,
    )
    report = build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=direct_context["direct_open_order_external_ids"],
        direct_open_order_count=direct_context["direct_open_order_count"],
        direct_open_position_count=direct_context["direct_open_position_count"],
        direct_trade_rows=direct_context["direct_trade_rows"],
    )
    plan = build_direct_trade_backfill_plan(
        report,
        direct_trade_rows=direct_context["direct_trade_rows"],
    )
    applied: list[dict[str, Any]] = []
    if not payload.dry_run:
        applied = apply_direct_trade_backfill_actions(
            connection,
            actions=plan["actions"],
            reviewed_by=reviewed_by,
            reason=reason,
        )

    direct_evidence = direct_context["direct_evidence"]
    direct_evidence_summary = {key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}}
    return {
        "dry_run": payload.dry_run,
        "filters": {
            "account_id": str(payload.account_id),
            "market_id": str(payload.market_id) if payload.market_id is not None else None,
            "outcome_id": str(payload.outcome_id) if payload.outcome_id is not None else None,
            "event_slug": payload.event_slug,
            "start_time": payload.start_time,
            "end_time": payload.end_time,
            "include_direct_clob_evidence": payload.include_direct_clob_evidence,
            "limit": payload.limit,
        },
        "direct_evidence": to_jsonable(direct_evidence_summary),
        "reconciliation": to_jsonable(report),
        "trade_backfill": to_jsonable(plan),
        "applied": to_jsonable(applied),
    }


@router.post("/orders/direct-open-mirror")
def mirror_direct_open_portfolio_orders(
    account_id: UUID = Query(...),
    dry_run: bool = Query(default=True),
    reviewed_by: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    reviewer = str(reviewed_by or "").strip()
    review_reason = str(reason or "").strip()
    if not dry_run and (not reviewer or not review_reason):
        raise HTTPException(status_code=422, detail="reviewed_by and reason are required when dry_run=false")

    direct_evidence = _fetch_direct_order_lifecycle_evidence(connection, account_id=str(account_id))
    direct_open_orders = list(direct_evidence.get("open_orders") or []) if direct_evidence.get("ok") else []
    token_to_pair = _load_outcome_pairs_by_token(connection)
    plan = build_direct_open_order_mirror_plan(
        account_id=str(account_id),
        direct_open_orders=direct_open_orders,
        token_to_pair=token_to_pair,
    )
    applied: list[dict[str, Any]] = []
    if not dry_run and direct_evidence.get("ok"):
        applied = apply_direct_open_order_mirror_actions(
            connection,
            actions=plan["actions"],
            reviewed_by=reviewer,
            reason=review_reason,
        )

    direct_evidence_summary = {key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}}
    return {
        "dry_run": dry_run,
        "status": "planned" if dry_run else "applied",
        "filters": {
            "account_id": str(account_id),
        },
        "direct_evidence": to_jsonable(direct_evidence_summary),
        "direct_open_order_mirror": to_jsonable(plan),
        "applied": to_jsonable(applied),
    }


@router.post("/manager/action-ledger")
def record_portfolio_manager_action_ledger(
    payload: PortfolioManagerActionLedgerRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    reviewer = str(payload.reviewed_by or "").strip()
    review_reason = str(payload.reason or "").strip()
    if not payload.dry_run and (not reviewer or not review_reason):
        raise HTTPException(status_code=422, detail="reviewed_by and reason are required when dry_run=false")

    preview = build_portfolio_manager_action_ledger_preview(
        action_plan=payload.action_plan,
        account_id=str(payload.account_id) if payload.account_id is not None else None,
    )
    applied: list[dict[str, Any]] = []
    if not payload.dry_run:
        applied.append(
            apply_portfolio_manager_action_ledger(
                connection,
                preview=preview,
                reviewed_by=reviewer,
                reason=review_reason,
            )
        )

    return {
        "dry_run": payload.dry_run,
        "status": "planned" if payload.dry_run else "applied",
        "live_order_impact": "read-only",
        "ledger_write_only": True,
        "no_order_side_effects": preview["side_effects"],
        "manager_action_ledger": to_jsonable(preview),
        "applied": to_jsonable(applied),
    }


@router.post("/manager/order-management")
def preview_portfolio_manager_order_management(
    payload: PortfolioManagerOrderManagementRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    if payload.dry_run:
        preview = build_portfolio_manager_order_management_preview(
            action_plan=payload.action_plan,
            account_id=str(payload.account_id) if payload.account_id is not None else None,
            requested_order=payload.requested_order,
        )
        return {
            "dry_run": True,
            "status": preview["status"],
            "live_order_impact": "read-only",
            "no_order_side_effects": preview["side_effects"],
            "order_management_preview": to_jsonable(preview),
        }

    reviewer = str(payload.reviewed_by or "").strip()
    review_reason = str(payload.reason or "").strip()
    if not reviewer or not review_reason:
        raise HTTPException(status_code=422, detail="reviewed_by and reason are required when dry_run=false")
    if not payload.execution_approved:
        raise HTTPException(
            status_code=403,
            detail="execution_approved=true is required when dry_run=false",
        )
    _portfolio_manager_order_management_enabled_or_raise()
    if payload.account_id is None:
        raise HTTPException(status_code=422, detail="account_id is required when dry_run=false")

    execution = apply_portfolio_manager_order_management_order(
        connection,
        action_plan=payload.action_plan,
        account_id=str(payload.account_id),
        requested_order=payload.requested_order,
        reviewed_by=reviewer,
        reason=review_reason,
    )
    return {
        "dry_run": False,
        "status": execution["status"],
        "live_order_impact": "order-path",
        "order_management_execution": to_jsonable(execution),
    }


@router.get("/manager/action-ledger")
def list_portfolio_manager_action_ledger(
    account_id: UUID | None = Query(default=None),
    issue: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        conditions.append("account_id = %s")
        params.append(str(account_id))
    if issue:
        conditions.append("issue = %s")
        params.append(issue)
    if status_filter:
        conditions.append("status = %s")
        params.append(status_filter)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                ledger_id,
                account_id,
                issue,
                schema_version,
                action,
                status,
                result,
                market_title,
                market_slug,
                token_id,
                execution_authorized,
                order_preparation_authorized,
                live_order_impact,
                missing_gates,
                rejected_truth_sources,
                ledger_record,
                source_plan,
                reviewed_by,
                reason,
                dry_run,
                created_at,
                updated_at
            FROM portfolio.manager_action_ledger
            {where_sql}
            ORDER BY created_at DESC, ledger_id DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {
        "filters": {
            "account_id": str(account_id) if account_id is not None else None,
            "issue": issue,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
        },
        "items": to_jsonable(rows),
        "count": len(rows),
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
