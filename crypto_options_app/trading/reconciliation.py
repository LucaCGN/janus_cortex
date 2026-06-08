from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_options_app.trading.fills import FillState
from crypto_options_app.trading.orders import OrderState


@dataclass(frozen=True)
class ReconciliationResult:
    reconciled: bool
    status: str
    blockers: tuple[str, ...]
    evidence: dict[str, Any]


def reconcile_order(
    *,
    local_order: OrderState,
    local_fills: list[FillState],
    remote_order: dict[str, Any],
    dust_tolerance: float = 1e-6,
) -> ReconciliationResult:
    blockers: list[str] = []
    if remote_order.get("exchange_order_id") and local_order.exchange_order_id:
        if remote_order["exchange_order_id"] != local_order.exchange_order_id:
            blockers.append("exchange_order_id_mismatch")
    elif local_order.status in {"submitted", "accepted", "partially_filled", "filled"}:
        blockers.append("remote_order_missing")

    local_filled = sum(fill.filled_shares for fill in local_fills)
    remote_filled = float(remote_order.get("filled_shares") or 0.0)
    if abs(local_filled - remote_filled) > dust_tolerance:
        blockers.append("filled_size_mismatch")
    remote_status = str(remote_order.get("status", "unknown"))
    if remote_status == "rejected" and local_order.status not in {"rejected", "submit_error"}:
        blockers.append("remote_rejected_local_not_terminal")
    return ReconciliationResult(
        reconciled=not blockers,
        status="reconciled" if not blockers else "reconciliation_failed",
        blockers=tuple(blockers),
        evidence={"local_filled": local_filled, "remote_filled": remote_filled, "remote_status": remote_status},
    )


def reload_active_state(conn: Any) -> dict[str, list[dict[str, Any]]]:
    open_orders = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM orders WHERE status NOT IN ('filled', 'unfilled', 'expired', 'cancelled', 'rejected', 'submit_error', 'reconciliation_failed')"
        )
    ]
    open_positions = [dict(row) for row in conn.execute("SELECT * FROM positions WHERE status='open'")]
    active_exit_plans = [dict(row) for row in conn.execute("SELECT * FROM exit_plans WHERE status IN ('active', 'covered', 'settlement_tracking')")]
    return {"open_orders": open_orders, "open_positions": open_positions, "active_exit_plans": active_exit_plans}
