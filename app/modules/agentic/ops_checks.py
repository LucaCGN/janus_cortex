from __future__ import annotations

from typing import Any

from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import to_jsonable
from app.data.nodes.polymarket.blockchain.manage_portfolio import view_open_positions, view_orders
from app.modules.agentic.repository import get_agentic_database_status
from app.modules.nba.execution.adapter import (
    build_live_creds,
    fetch_account_summary,
    fetch_clob_collateral_status,
    list_latest_positions,
    resolve_trading_account,
)


PORTFOLIO_MIRROR_NON_BLOCKING_REASONS = {
    "portfolio_mirror_missing_summary",
    "portfolio_mirror_cash_mismatch",
    "portfolio_mirror_equity_mismatch",
    "portfolio_mirror_position_mismatch",
}


def build_integrity_snapshot(
    connection: PsycopgConnection,
    *,
    account_id: str | None = None,
    required_notional_usd: float = 1.0,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "database": get_agentic_database_status(),
        "account": None,
        "direct_clob": {"ok": False, "error": "not_checked"},
        "portfolio_mirror": {"ok": False, "error": "not_checked"},
        "ready_for_live_minimum_orders": False,
        "blockers": [],
    }
    try:
        account = resolve_trading_account(connection, account_id=account_id)
        snapshot["account"] = {
            "account_id": str(account.get("account_id") or ""),
            "provider_code": str(account.get("provider_code") or ""),
            "wallet_address": str(account.get("wallet_address") or ""),
            "proxy_wallet_address": str(account.get("proxy_wallet_address") or ""),
            "chain_id": account.get("chain_id"),
            "is_active": bool(account.get("is_active", True)),
        }
    except Exception as exc:  # noqa: BLE001
        snapshot["blockers"].append({"reason": "account_resolution_failed", "error": str(exc)})
        return snapshot

    creds = build_live_creds(account)
    collateral = fetch_clob_collateral_status(creds, required_notional_usd=required_notional_usd)
    direct_orders = _safe_direct_orders(creds)
    direct_positions = _safe_direct_positions(creds)
    snapshot["direct_clob"] = {
        "ok": bool(collateral.get("ready")),
        "collateral": collateral,
        "open_order_count": len(direct_orders.get("orders") or []),
        "open_orders": direct_orders,
        "open_positions": direct_positions,
    }

    account_id_value = str(account["account_id"])
    mirror_summary = fetch_account_summary(connection, account_id=account_id_value)
    mirror_positions = list_latest_positions(connection, account_id=account_id_value)
    mirror_authority = classify_portfolio_mirror_authority(
        collateral=collateral,
        direct_positions=direct_positions,
        mirror_summary=mirror_summary,
        mirror_positions=mirror_positions,
    )
    snapshot["portfolio_mirror"] = {
        "ok": not mirror_authority["quarantined"],
        "status": mirror_authority["status"],
        "authoritative_for_live": mirror_authority["authoritative_for_live"],
        "quarantine_reasons": mirror_authority["quarantine_reasons"],
        "summary": to_jsonable(mirror_summary),
        "position_count": len(mirror_positions),
        "positions_preview": to_jsonable(mirror_positions[:20]),
    }

    if not bool(snapshot["database"].get("ok")):
        snapshot["blockers"].append({"reason": "agentic_database_not_ready", "database": snapshot["database"]})
    if not bool(collateral.get("ready")):
        snapshot["blockers"].append({"reason": "clob_collateral_not_ready", "collateral": collateral})
    if direct_orders.get("ok") is False:
        snapshot["blockers"].append({"reason": "direct_open_order_check_failed", "error": direct_orders.get("error")})
    if direct_positions.get("ok") is False:
        snapshot["blockers"].append({"reason": "direct_open_position_check_failed", "error": direct_positions.get("error")})
    for reason in mirror_authority["quarantine_reasons"]:
        blocker = {
            **reason,
            "severity": "non_blocking_for_live_if_direct_clob_ready",
            "mirror_authoritative_for_live": False,
        }
        snapshot["blockers"].append(blocker)

    hard_blockers = [
        blocker
        for blocker in snapshot["blockers"]
        if blocker.get("reason") not in PORTFOLIO_MIRROR_NON_BLOCKING_REASONS
    ]
    snapshot["ready_for_live_minimum_orders"] = bool(collateral.get("ready")) and not hard_blockers
    return snapshot


def classify_portfolio_mirror_authority(
    *,
    collateral: dict[str, Any] | None,
    direct_positions: dict[str, Any] | None,
    mirror_summary: dict[str, Any] | None,
    mirror_positions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    direct_balance_usd = _float_or_none((collateral or {}).get("balance_usd"))
    direct_position_count = len((direct_positions or {}).get("positions") or [])
    mirror_cash_usd = _float_or_none((mirror_summary or {}).get("cash_usd"))
    mirror_equity_usd = _float_or_none((mirror_summary or {}).get("equity_usd"))
    mirror_position_count = len(mirror_positions or [])
    reasons: list[dict[str, Any]] = []
    if not mirror_summary:
        reasons.append(
            {
                "reason": "portfolio_mirror_missing_summary",
                "direct_clob_balance_usd": direct_balance_usd,
            }
        )
    if mirror_cash_usd is not None and direct_balance_usd is not None and mirror_cash_usd <= 0.0 < direct_balance_usd:
        reasons.append(
            {
                "reason": "portfolio_mirror_cash_mismatch",
                "mirror_cash_usd": mirror_cash_usd,
                "direct_clob_balance_usd": direct_balance_usd,
            }
        )
    if mirror_equity_usd is not None and direct_balance_usd is not None and mirror_equity_usd <= 0.0 < direct_balance_usd:
        reasons.append(
            {
                "reason": "portfolio_mirror_equity_mismatch",
                "mirror_equity_usd": mirror_equity_usd,
                "direct_clob_balance_usd": direct_balance_usd,
            }
        )
    if (direct_positions or {}).get("ok") is True and mirror_position_count > 0 and direct_position_count == 0:
        reasons.append(
            {
                "reason": "portfolio_mirror_position_mismatch",
                "mirror_position_count": mirror_position_count,
                "direct_clob_position_count": direct_position_count,
            }
        )
    quarantined = bool(reasons)
    return {
        "status": "quarantined" if quarantined else "authoritative",
        "authoritative_for_live": not quarantined,
        "quarantined": quarantined,
        "quarantine_reasons": reasons,
    }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_direct_orders(creds: Any) -> dict[str, Any]:
    try:
        orders = view_orders(creds, open_only=True)
        return {"ok": True, "orders": [to_jsonable(getattr(order, "__dict__", order)) for order in orders]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "orders": []}


def _safe_direct_positions(creds: Any) -> dict[str, Any]:
    try:
        positions = view_open_positions(creds, min_size=0.0)
        return {"ok": True, "positions": [to_jsonable(getattr(position, "__dict__", position)) for position in positions]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "positions": []}


__all__ = ["build_integrity_snapshot", "classify_portfolio_mirror_authority"]
