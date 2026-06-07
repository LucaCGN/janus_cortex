from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from crypto_options_app.trading.positions import PositionState


ALLOWED_COVERAGE_TYPES = {
    "active_cashout_order",
    "split_exit_plan",
    "hedge_managed_inventory_plan",
    "hold_to_settlement_policy",
    "late_rescue_policy",
    "reconciled_settlement_state",
}


@dataclass(frozen=True)
class ExitPlanState:
    exit_plan_key: str
    position_key: str
    coverage_type: str
    status: str
    created_at_utc: datetime


@dataclass(frozen=True)
class ExitOrderState:
    exit_order_key: str
    exit_plan_key: str
    parent_position_key: str
    order_key: str | None
    status: str


def create_exit_plan(position: PositionState, *, coverage_type: str, status: str = "active") -> ExitPlanState:
    if coverage_type not in ALLOWED_COVERAGE_TYPES:
        raise ValueError(f"unsupported coverage type: {coverage_type}")
    return ExitPlanState(
        exit_plan_key=f"exit_plan:{position.position_key}:{coverage_type}",
        position_key=position.position_key,
        coverage_type=coverage_type,
        status=status,
        created_at_utc=datetime.now(UTC),
    )


def create_exit_order(plan: ExitPlanState, *, order_key: str | None, status: str = "created") -> ExitOrderState:
    if not plan.position_key:
        raise ValueError("exit order requires parent position link")
    return ExitOrderState(
        exit_order_key=f"exit_order:{plan.exit_plan_key}:{order_key or 'pending'}",
        exit_plan_key=plan.exit_plan_key,
        parent_position_key=plan.position_key,
        order_key=order_key,
        status=status,
    )


def validate_lifecycle_coverage(positions: list[PositionState], exit_plans: list[ExitPlanState]) -> tuple[str, ...]:
    active_plan_positions = {plan.position_key for plan in exit_plans if plan.status in {"active", "covered", "settlement_tracking"}}
    missing = [position.position_key for position in positions if position.status == "open" and position.position_key not in active_plan_positions]
    return tuple(f"missing_lifecycle_coverage:{position_key}" for position_key in missing)


def validate_duplicate_exit_orders(exit_orders: list[ExitOrderState]) -> tuple[str, ...]:
    active_counts: dict[str, int] = {}
    for exit_order in exit_orders:
        if exit_order.status in {"created", "submitted", "accepted", "partially_filled"}:
            active_counts[exit_order.exit_plan_key] = active_counts.get(exit_order.exit_plan_key, 0) + 1
    return tuple(f"duplicate_exit_order:{plan_key}" for plan_key, count in active_counts.items() if count > 1)
