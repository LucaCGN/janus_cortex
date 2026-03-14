from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.polymarket.blockchain.manage_portfolio import (
    PolymarketCredentials,
    _extract_base_address,
)


@dataclass
class ClosedPositionConsolidationSummary:
    status: str
    wallet_address: str
    accounts_processed: int
    rows_read: int
    rows_written: int
    consolidated_positions_written: int
    valuation_snapshots_written: int
    stale_conclusion_candidates: int
    stale_conclusion_samples: list[dict[str, Any]] = field(default_factory=list)
    error_text: str | None = None


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def _parse_wallet(raw_value: str | None) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    if value.startswith("0x") and len(value) >= 42:
        return value[:42]
    return None


def consolidate_closed_positions_for_wallet(
    *,
    wallet_address: str,
    stale_sample_limit: int = 20,
) -> ClosedPositionConsolidationSummary:
    wallet = _parse_wallet(wallet_address)
    if wallet is None:
        return ClosedPositionConsolidationSummary(
            status="error",
            wallet_address=str(wallet_address),
            accounts_processed=0,
            rows_read=0,
            rows_written=0,
            consolidated_positions_written=0,
            valuation_snapshots_written=0,
            stale_conclusion_candidates=0,
            error_text="invalid wallet_address",
        )

    rows_read = 0
    rows_written = 0
    consolidated_positions_written = 0
    valuation_snapshots_written = 0
    stale_conclusion_candidates = 0
    stale_samples: list[dict[str, Any]] = []

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        now = datetime.now(timezone.utc)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT account_id
                    FROM portfolio.trading_accounts
                    WHERE lower(COALESCE(wallet_address, '')) = lower(%s)
                       OR lower(COALESCE(proxy_wallet_address, '')) = lower(%s)
                    ORDER BY created_at DESC;
                    """,
                    (wallet, wallet),
                )
                account_ids = [str(row[0]) for row in cursor.fetchall()]

            if not account_ids:
                return ClosedPositionConsolidationSummary(
                    status="success",
                    wallet_address=wallet,
                    accounts_processed=0,
                    rows_read=0,
                    rows_written=0,
                    consolidated_positions_written=0,
                    valuation_snapshots_written=0,
                    stale_conclusion_candidates=0,
                    stale_conclusion_samples=[],
                )

            # Stale-conclusion scan from all account exposures (orders/trades/positions), not only closed positions.
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH exposure AS (
                        SELECT o.account_id, o.market_id, o.outcome_id
                        FROM portfolio.orders o
                        WHERE o.account_id = ANY(%s::uuid[])
                        UNION
                        SELECT t.account_id, t.market_id, t.outcome_id
                        FROM portfolio.trades t
                        WHERE t.account_id = ANY(%s::uuid[])
                        UNION
                        SELECT ps.account_id, co.market_id, ps.outcome_id
                        FROM portfolio.position_snapshots ps
                        JOIN catalog.outcomes co ON co.outcome_id = ps.outcome_id
                        WHERE ps.account_id = ANY(%s::uuid[])
                    )
                    SELECT DISTINCT ON (
                        ex.account_id,
                        ex.market_id,
                        COALESCE(ex.outcome_id::text, '')
                    )
                        ex.account_id,
                        e.event_id,
                        e.canonical_slug,
                        e.status AS event_status,
                        e.end_time,
                        m.market_id,
                        m.question AS market_question,
                        m.settlement_status,
                        m.close_time,
                        ex.outcome_id,
                        oc.outcome_label
                    FROM exposure ex
                    JOIN catalog.markets m ON m.market_id = ex.market_id
                    JOIN catalog.events e ON e.event_id = m.event_id
                    LEFT JOIN catalog.outcomes oc ON oc.outcome_id = ex.outcome_id
                    ORDER BY
                        ex.account_id,
                        ex.market_id,
                        COALESCE(ex.outcome_id::text, '');
                    """,
                    (account_ids, account_ids, account_ids),
                )
                exposure_rows = cursor.fetchall()

            for (
                account_id,
                event_id,
                event_slug,
                event_status,
                event_end_time,
                market_id,
                market_question,
                settlement_status,
                market_close_time,
                outcome_id,
                outcome_label,
            ) in exposure_rows:
                rows_read += 1
                event_open = str(event_status or "").lower() == "open"
                market_open = str(settlement_status or "").lower() == "open"
                event_past_end = isinstance(event_end_time, datetime) and event_end_time <= now
                market_past_close = isinstance(market_close_time, datetime) and market_close_time <= now
                if not ((event_open and event_past_end) or (market_open and market_past_close)):
                    continue

                stale_conclusion_candidates += 1
                if len(stale_samples) >= max(stale_sample_limit, 0):
                    continue
                stale_samples.append(
                    {
                        "account_id": str(account_id),
                        "event_id": str(event_id),
                        "event_slug": str(event_slug or ""),
                        "event_status": str(event_status or ""),
                        "event_end_time": event_end_time,
                        "market_id": str(market_id),
                        "market_question": str(market_question or ""),
                        "settlement_status": str(settlement_status or ""),
                        "market_close_time": market_close_time,
                        "outcome_id": str(outcome_id) if outcome_id is not None else None,
                        "outcome_label": str(outcome_label or ""),
                    }
                )

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT ON (ps.account_id, ps.outcome_id)
                        ps.account_id,
                        ps.outcome_id,
                        ps.captured_at,
                        ps.avg_price,
                        ps.realized_pnl,
                        o.market_id,
                        o.outcome_label,
                        m.question AS market_question,
                        m.settlement_status,
                        m.close_time,
                        e.event_id,
                        e.canonical_slug,
                        e.status AS event_status,
                        e.end_time
                    FROM portfolio.position_snapshots ps
                    JOIN catalog.outcomes o ON o.outcome_id = ps.outcome_id
                    JOIN catalog.markets m ON m.market_id = o.market_id
                    JOIN catalog.events e ON e.event_id = m.event_id
                    WHERE ps.account_id = ANY(%s::uuid[])
                      AND ps.source = 'data_api_closed_position'
                    ORDER BY ps.account_id, ps.outcome_id, ps.captured_at DESC;
                    """,
                    (account_ids,),
                )
                closed_rows = cursor.fetchall()

            for (
                account_id,
                outcome_id,
                captured_at,
                avg_price,
                realized_pnl,
                market_id,
                outcome_label,
                market_question,
                settlement_status,
                market_close_time,
                event_id,
                event_slug,
                event_status,
                event_end_time,
            ) in closed_rows:
                rows_read += 1

                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT source, captured_at, realized_pnl, size
                        FROM portfolio.position_snapshots
                        WHERE account_id = %s
                          AND outcome_id = %s
                        ORDER BY captured_at DESC
                        LIMIT 1;
                        """,
                        (str(account_id), str(outcome_id)),
                    )
                    latest = cursor.fetchone()

                if latest is not None:
                    latest_source, _, latest_realized_pnl, latest_size = latest
                    latest_source_text = str(latest_source or "")
                    if latest_source_text == "consolidated_closed_position":
                        same_realized = _to_decimal(latest_realized_pnl) == _to_decimal(realized_pnl)
                        size_zero = _to_decimal(latest_size) == Decimal("0")
                        if same_realized and size_zero:
                            continue

                inserted = repo.insert_position_snapshot(
                    account_id=str(account_id),
                    outcome_id=str(outcome_id),
                    captured_at=now,
                    source="consolidated_closed_position",
                    size=0.0,
                    avg_price=float(avg_price) if avg_price is not None else None,
                    current_price=0.0,
                    current_value=0.0,
                    unrealized_pnl=0.0,
                    realized_pnl=float(realized_pnl) if realized_pnl is not None else None,
                    raw_json={
                        "consolidated_from_source": "data_api_closed_position",
                        "source_captured_at": captured_at.isoformat() if isinstance(captured_at, datetime) else None,
                        "event_slug": event_slug,
                        "event_status": event_status,
                        "market_question": market_question,
                        "market_settlement_status": settlement_status,
                    },
                    ignore_duplicates=True,
                )
                if inserted:
                    consolidated_positions_written += 1
                    rows_written += 1

            # Account-level valuation snapshot consolidation from latest position states.
            for account_id in account_ids:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT DISTINCT ON (ps.outcome_id)
                            ps.current_value,
                            ps.realized_pnl,
                            ps.unrealized_pnl
                        FROM portfolio.position_snapshots ps
                        WHERE ps.account_id = %s
                        ORDER BY ps.outcome_id, ps.captured_at DESC;
                        """,
                        (account_id,),
                    )
                    latest_positions = cursor.fetchall()

                    positions_value_usd = sum(_to_decimal(row[0]) for row in latest_positions)
                    realized_pnl_usd = sum(_to_decimal(row[1]) for row in latest_positions)
                    unrealized_pnl_usd = sum(_to_decimal(row[2]) for row in latest_positions)
                    equity_usd = positions_value_usd

                    cursor.execute(
                        """
                        INSERT INTO portfolio.valuation_snapshots (
                            account_id,
                            captured_at,
                            equity_usd,
                            cash_usd,
                            positions_value_usd,
                            realized_pnl_usd,
                            unrealized_pnl_usd,
                            raw_json
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (account_id, captured_at)
                        DO UPDATE SET
                            equity_usd = EXCLUDED.equity_usd,
                            cash_usd = EXCLUDED.cash_usd,
                            positions_value_usd = EXCLUDED.positions_value_usd,
                            realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                            unrealized_pnl_usd = EXCLUDED.unrealized_pnl_usd,
                            raw_json = EXCLUDED.raw_json;
                        """,
                        (
                            account_id,
                            now,
                            equity_usd,
                            None,
                            positions_value_usd,
                            realized_pnl_usd,
                            unrealized_pnl_usd,
                            Json(
                                {
                                    "source": "closed_position_consolidation",
                                    "wallet_address": wallet,
                                    "generated_at": now.isoformat(),
                                }
                            ),
                        ),
                    )
                valuation_snapshots_written += 1
                rows_written += 1

            return ClosedPositionConsolidationSummary(
                status="success",
                wallet_address=wallet,
                accounts_processed=len(account_ids),
                rows_read=rows_read,
                rows_written=rows_written,
                consolidated_positions_written=consolidated_positions_written,
                valuation_snapshots_written=valuation_snapshots_written,
                stale_conclusion_candidates=stale_conclusion_candidates,
                stale_conclusion_samples=stale_samples,
                error_text=None,
            )
        except Exception as exc:  # noqa: BLE001
            return ClosedPositionConsolidationSummary(
                status="error",
                wallet_address=wallet,
                accounts_processed=0,
                rows_read=rows_read,
                rows_written=rows_written,
                consolidated_positions_written=consolidated_positions_written,
                valuation_snapshots_written=valuation_snapshots_written,
                stale_conclusion_candidates=stale_conclusion_candidates,
                stale_conclusion_samples=stale_samples,
                error_text=repr(exc),
            )


def _resolve_wallet(explicit_wallet: str | None) -> str | None:
    if explicit_wallet:
        return _extract_base_address(explicit_wallet) or _parse_wallet(explicit_wallet)
    creds = PolymarketCredentials.from_env()
    candidates = (
        creds.proxy_wallet_raw,
        creds.wallet_address,
        creds.primary_wallet_raw,
        creds.funder_address,
    )
    for item in candidates:
        wallet = _parse_wallet(item)
        if wallet:
            return wallet
    return None


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consolidate closed positions and detect stale conclusions.")
    parser.add_argument("--wallet", default=None, help="Wallet address to consolidate.")
    parser.add_argument("--stale-sample-limit", type=int, default=20)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    wallet = _resolve_wallet(args.wallet)
    if wallet is None:
        print("Wallet address is required (set --wallet or POLYMARKET_PROXY_WALLET/POLYMARKET_PRIMARY_WALLET).")
        return 2

    summary = consolidate_closed_positions_for_wallet(
        wallet_address=wallet,
        stale_sample_limit=int(args.stale_sample_limit),
    )
    print(f"status={summary.status} wallet={summary.wallet_address}")
    print(
        " | ".join(
            [
                f"accounts_processed={summary.accounts_processed}",
                f"rows_read={summary.rows_read}",
                f"rows_written={summary.rows_written}",
                f"consolidated_positions_written={summary.consolidated_positions_written}",
                f"valuation_snapshots_written={summary.valuation_snapshots_written}",
                f"stale_conclusion_candidates={summary.stale_conclusion_candidates}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
