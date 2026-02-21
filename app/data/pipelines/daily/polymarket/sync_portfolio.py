from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.polymarket.blockchain.manage_portfolio import (
    PolymarketCredentials,
    _extract_base_address,
)
from app.data.nodes.polymarket.gamma.gamma_client import PolymarketDataClient


_NAMESPACE = uuid.UUID("44ecb08d-f092-4a67-b542-c944bcf1c352")


@dataclass
class PortfolioMirrorSummary:
    sync_run_id: str | None
    status: str
    wallet_address: str
    rows_read: int
    rows_written: int
    positions_written: int
    orders_written: int
    order_events_written: int
    trades_written: int
    unresolved_positions: int
    unresolved_orders: int
    unresolved_trades: int
    error_text: str | None = None


@dataclass
class _ResolutionMaps:
    token_to_pair: dict[str, tuple[str, str]]
    condition_to_market: dict[str, str]
    external_market_to_market: dict[str, str]
    market_to_first_outcome: dict[str, str]


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get("data")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_dt(value: Any, *, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return default or datetime.now(timezone.utc)


def _first_present(raw: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = raw.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return None


def _insert_sync_run(connection: Any, *, provider_id: str, module_id: str, wallet_address: str) -> str:
    sync_run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.sync_runs (
                sync_run_id, provider_id, module_id, pipeline_name, run_type, status, started_at, meta_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                sync_run_id,
                provider_id,
                module_id,
                "daily.polymarket.sync_portfolio",
                "scheduled",
                "running",
                now,
                Json({"wallet_address": wallet_address}),
            ),
        )
    return sync_run_id


def _update_sync_run(
    connection: Any,
    *,
    sync_run_id: str,
    status: str,
    rows_read: int,
    rows_written: int,
    error_text: str | None = None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE core.sync_runs
            SET status = %s,
                ended_at = %s,
                rows_read = %s,
                rows_written = %s,
                error_text = %s
            WHERE sync_run_id = %s;
            """,
            (status, datetime.now(timezone.utc), rows_read, rows_written, error_text, sync_run_id),
        )


def _insert_raw_payload(
    connection: Any,
    *,
    sync_run_id: str,
    provider_id: str,
    endpoint: str,
    external_id: str,
    payload: Any,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.raw_payloads (
                raw_payload_id, sync_run_id, provider_id, endpoint, external_id, fetched_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                str(uuid.uuid4()),
                sync_run_id,
                provider_id,
                endpoint,
                external_id,
                datetime.now(timezone.utc),
                Json(payload),
            ),
        )


def _load_resolution_maps(connection: Any) -> _ResolutionMaps:
    token_to_pair: dict[str, tuple[str, str]] = {}
    condition_to_market: dict[str, str] = {}
    external_market_to_market: dict[str, str] = {}
    market_to_first_outcome: dict[str, str] = {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT token_id, outcome_id, market_id
            FROM catalog.outcomes
            WHERE token_id IS NOT NULL AND token_id <> '';
            """
        )
        for token_id, outcome_id, market_id in cursor.fetchall():
            token_to_pair[str(token_id)] = (str(market_id), str(outcome_id))

        cursor.execute(
            """
            SELECT external_condition_id, market_id
            FROM catalog.market_external_refs
            WHERE external_condition_id IS NOT NULL AND external_condition_id <> '';
            """
        )
        for condition_id, market_id in cursor.fetchall():
            condition_to_market[str(condition_id)] = str(market_id)

        cursor.execute(
            """
            SELECT external_market_id, market_id
            FROM catalog.market_external_refs
            WHERE external_market_id IS NOT NULL AND external_market_id <> '';
            """
        )
        for external_market_id, market_id in cursor.fetchall():
            external_market_to_market[str(external_market_id)] = str(market_id)

        cursor.execute(
            """
            SELECT market_id, outcome_id
            FROM catalog.outcomes
            ORDER BY market_id, outcome_index;
            """
        )
        for market_id, outcome_id in cursor.fetchall():
            market_key = str(market_id)
            if market_key not in market_to_first_outcome:
                market_to_first_outcome[market_key] = str(outcome_id)

    return _ResolutionMaps(
        token_to_pair=token_to_pair,
        condition_to_market=condition_to_market,
        external_market_to_market=external_market_to_market,
        market_to_first_outcome=market_to_first_outcome,
    )


def _resolve_market_outcome(raw: dict[str, Any], maps: _ResolutionMaps) -> tuple[str | None, str | None]:
    token = _first_present(
        raw,
        ["asset", "asset_id", "token_id", "tokenId", "outcomeTokenId", "clobTokenId"],
    )
    if token and token in maps.token_to_pair:
        market_id, outcome_id = maps.token_to_pair[token]
        return market_id, outcome_id

    condition_id = _first_present(raw, ["conditionId", "condition_id", "condition"])
    if condition_id and condition_id in maps.condition_to_market:
        market_id = maps.condition_to_market[condition_id]
        return market_id, maps.market_to_first_outcome.get(market_id)

    external_market_id = _first_present(raw, ["market", "marketId", "market_id"])
    if external_market_id and external_market_id in maps.external_market_to_market:
        market_id = maps.external_market_to_market[external_market_id]
        return market_id, maps.market_to_first_outcome.get(market_id)

    return None, None


def _fetch_data_api_payload(
    *,
    data_client: PolymarketDataClient,
    wallet_address: str,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    orders_merged: dict[str, dict[str, Any]] = {}
    for status in ("OPEN", "FILLED", "CANCELED"):
        try:
            rows = _as_list(data_client.get_orders(user=wallet_address, limit=limit, status=status))
        except Exception:
            rows = []
        for row in rows:
            key = str(row.get("id") or row.get("orderID") or f"{status}:{len(orders_merged)}")
            orders_merged[key] = row

    try:
        open_positions = _as_list(data_client.get_positions(user=wallet_address, size_threshold=0.0, limit=limit))
    except Exception:
        open_positions = []
    try:
        closed_positions = _as_list(data_client.get_closed_positions(user=wallet_address, limit=limit))
    except Exception:
        closed_positions = []
    try:
        trades = _as_list(data_client.get_trades(user=wallet_address, limit=limit, taker_only=False))
    except Exception:
        trades = []

    return {
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "orders": list(orders_merged.values()),
        "trades": trades,
    }


def run_portfolio_mirror_sync(
    *,
    wallet_address: str,
    limit: int = 250,
    payload_override: dict[str, list[dict[str, Any]]] | None = None,
    data_client: PolymarketDataClient | None = None,
) -> PortfolioMirrorSummary:
    client = data_client or PolymarketDataClient()
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    positions_written = 0
    orders_written = 0
    order_events_written = 0
    trades_written = 0
    unresolved_positions = 0
    unresolved_orders = 0
    unresolved_trades = 0

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=_uuid_for("provider", "polymarket_data_api"),
            code="polymarket_data_api",
            name="Polymarket Data API",
            category="prediction_market",
            base_url="https://data-api.polymarket.com",
            auth_type="none",
        )
        module_id = repo.upsert_module(
            module_id=_uuid_for("module", "polymarket_portfolio_mirror"),
            code="polymarket_portfolio_mirror",
            name="Polymarket Portfolio Mirror",
            description="Data API portfolio mirror ingestion pipeline",
            owner="janus",
        )
        account_id = repo.upsert_trading_account(
            account_id=_uuid_for("account", wallet_address),
            provider_id=provider_id,
            account_label=f"wallet:{wallet_address[:10]}",
            wallet_address=wallet_address,
            proxy_wallet_address=wallet_address,
            chain_id=137,
            is_active=True,
        )
        sync_run_id = _insert_sync_run(
            connection,
            provider_id=provider_id,
            module_id=module_id,
            wallet_address=wallet_address,
        )
        connection.commit()

        try:
            payload = payload_override or _fetch_data_api_payload(
                data_client=client,
                wallet_address=wallet_address,
                limit=limit,
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/open-positions",
                external_id=wallet_address,
                payload=payload.get("open_positions", []),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/closed-positions",
                external_id=wallet_address,
                payload=payload.get("closed_positions", []),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/orders",
                external_id=wallet_address,
                payload=payload.get("orders", []),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/trades",
                external_id=wallet_address,
                payload=payload.get("trades", []),
            )
            maps = _load_resolution_maps(connection)

            for raw in payload.get("open_positions", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if outcome_id is None:
                    unresolved_positions += 1
                    continue
                size = _safe_float(raw.get("size"))
                current_value = _safe_float(raw.get("currentValue"))
                current_price = (
                    (current_value / size)
                    if current_value is not None and size is not None and abs(size) > 0
                    else _safe_float(raw.get("currentPrice"))
                )
                inserted = repo.insert_position_snapshot(
                    account_id=account_id,
                    outcome_id=outcome_id,
                    captured_at=now,
                    source="data_api_open_position",
                    size=size,
                    avg_price=_safe_float(raw.get("avgPrice")),
                    current_price=current_price,
                    current_value=current_value,
                    unrealized_pnl=_safe_float(raw.get("cashPnl")),
                    realized_pnl=None,
                    raw_json=raw,
                    ignore_duplicates=True,
                )
                if inserted:
                    positions_written += 1
                    rows_written += 1

            for raw in payload.get("closed_positions", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if outcome_id is None:
                    unresolved_positions += 1
                    continue
                inserted = repo.insert_position_snapshot(
                    account_id=account_id,
                    outcome_id=outcome_id,
                    captured_at=now,
                    source="data_api_closed_position",
                    size=_safe_float(raw.get("size")),
                    avg_price=_safe_float(raw.get("avgPrice")),
                    current_price=None,
                    current_value=None,
                    unrealized_pnl=None,
                    realized_pnl=_safe_float(raw.get("realizedPnl")),
                    raw_json=raw,
                    ignore_duplicates=True,
                )
                if inserted:
                    positions_written += 1
                    rows_written += 1

            for raw in payload.get("orders", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if market_id is None:
                    unresolved_orders += 1
                    continue
                external_order_id = _first_present(raw, ["id", "orderID", "order_id"])
                order_id = _uuid_for("order", external_order_id or str(uuid.uuid4()))
                placed_at = _safe_dt(raw.get("createdAt") or raw.get("created_at") or raw.get("timestamp"), default=now)
                updated_at = _safe_dt(raw.get("updatedAt") or raw.get("updated_at"), default=now)
                side = str(raw.get("side") or "buy").lower()
                order_type = str(raw.get("type") or raw.get("orderType") or "limit").lower()
                status = str(raw.get("status") or "open").lower()
                repo.upsert_order(
                    order_id=order_id,
                    account_id=account_id,
                    market_id=market_id,
                    outcome_id=outcome_id,
                    side=side,
                    order_type=order_type,
                    status=status,
                    placed_at=placed_at,
                    updated_at=updated_at,
                    external_order_id=external_order_id,
                    client_order_id=_first_present(raw, ["clientOrderId", "client_order_id"]),
                    time_in_force=_first_present(raw, ["timeInForce", "time_in_force"]),
                    limit_price=_safe_float(raw.get("price")),
                    size=_safe_float(raw.get("size")),
                    metadata_json=raw,
                )
                orders_written += 1
                rows_written += 1

                inserted_event = repo.insert_order_event(
                    order_event_id=_uuid_for("order_event", order_id, updated_at.isoformat(), status),
                    order_id=order_id,
                    event_time=updated_at,
                    event_type=f"mirror_status_{status}",
                    filled_size_delta=_safe_float(raw.get("filledSize") or raw.get("filled_size")),
                    filled_notional_delta=_safe_float(raw.get("filledNotional") or raw.get("filled_notional")),
                    raw_json=raw,
                    ignore_duplicates=True,
                )
                if inserted_event:
                    order_events_written += 1
                    rows_written += 1

            for raw in payload.get("trades", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if market_id is None:
                    unresolved_trades += 1
                    continue
                external_trade_id = _first_present(raw, ["id", "tradeID", "trade_id"])
                trade_id = _uuid_for("trade", external_trade_id or str(uuid.uuid4()))
                trade_time = _safe_dt(raw.get("timestamp") or raw.get("createdAt"), default=now)
                repo.upsert_trade(
                    trade_id=trade_id,
                    account_id=account_id,
                    order_id=_uuid_for("order", _first_present(raw, ["orderID", "orderId", "order_id"]) or "none"),
                    market_id=market_id,
                    outcome_id=outcome_id,
                    external_trade_id=external_trade_id,
                    tx_hash=_first_present(raw, ["transactionHash", "txHash", "tx_hash"]),
                    side=str(raw.get("side") or "buy").lower(),
                    price=_safe_float(raw.get("price")),
                    size=_safe_float(raw.get("size")),
                    fee=_safe_float(raw.get("fee")),
                    fee_asset=_first_present(raw, ["feeAsset", "fee_asset"]),
                    liquidity_role=_first_present(raw, ["liquidityRole", "liquidity_role"]),
                    trade_time=trade_time,
                    raw_json=raw,
                )
                trades_written += 1
                rows_written += 1

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=None,
            )
            connection.commit()
            return PortfolioMirrorSummary(
                sync_run_id=sync_run_id,
                status="success",
                wallet_address=wallet_address,
                rows_read=rows_read,
                rows_written=rows_written,
                positions_written=positions_written,
                orders_written=orders_written,
                order_events_written=order_events_written,
                trades_written=trades_written,
                unresolved_positions=unresolved_positions,
                unresolved_orders=unresolved_orders,
                unresolved_trades=unresolved_trades,
            )
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=repr(exc),
            )
            connection.commit()
            return PortfolioMirrorSummary(
                sync_run_id=sync_run_id,
                status="error",
                wallet_address=wallet_address,
                rows_read=rows_read,
                rows_written=rows_written,
                positions_written=positions_written,
                orders_written=orders_written,
                order_events_written=order_events_written,
                trades_written=trades_written,
                unresolved_positions=unresolved_positions,
                unresolved_orders=unresolved_orders,
                unresolved_trades=unresolved_trades,
                error_text=repr(exc),
            )


def _resolve_wallet_address(explicit_wallet: str | None) -> str | None:
    if explicit_wallet:
        wallet = _extract_base_address(explicit_wallet) or explicit_wallet
        return wallet
    creds = PolymarketCredentials.from_env()
    if creds.wallet_address:
        return creds.wallet_address
    return None


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run polymarket portfolio mirror ingestion.")
    parser.add_argument("--wallet", default=None, help="0x wallet address (base) to mirror.")
    parser.add_argument("--limit", type=int, default=250)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    wallet_address = _resolve_wallet_address(args.wallet)
    if not wallet_address:
        print("Wallet address is required (set --wallet or POLYMARKET_PROXY_WALLET/POLYMARKET_PRIMARY_WALLET).")
        return 2

    summary = run_portfolio_mirror_sync(wallet_address=wallet_address, limit=args.limit)
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} wallet={summary.wallet_address}")
    print(
        " | ".join(
            [
                f"rows_read={summary.rows_read}",
                f"rows_written={summary.rows_written}",
                f"positions_written={summary.positions_written}",
                f"orders_written={summary.orders_written}",
                f"order_events_written={summary.order_events_written}",
                f"trades_written={summary.trades_written}",
                f"unresolved_positions={summary.unresolved_positions}",
                f"unresolved_orders={summary.unresolved_orders}",
                f"unresolved_trades={summary.unresolved_trades}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
