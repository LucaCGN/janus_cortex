from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import create_schema
from crypto_options_app.trading.executor_boundary import (
    ExecutorBoundaryConfig,
    executor_boundary_ready,
    reject_manual_order_instruction,
)
from crypto_options_app.trading.exits import (
    create_exit_order,
    create_exit_plan,
    validate_duplicate_exit_orders,
    validate_lifecycle_coverage,
)
from crypto_options_app.trading.fills import record_fill
from crypto_options_app.trading.intents import IntentBook, create_execution_intent
from crypto_options_app.trading.orders import create_order_from_intent, order_counts_as_trade, transition_order
from crypto_options_app.trading.positions import create_position_from_buy_fill
from crypto_options_app.trading.reconciliation import reconcile_order, reload_active_state


def test_intent_idempotency_and_market_limit_buy_sell_creation_pytest() -> None:
    decision = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    order_types = ("market_buy", "limit_buy", "market_sell", "limit_sell")
    intents = [
        create_execution_intent(
            candidate_key="candidate-1",
            strategy_id="strategy-1",
            run_id="run-1",
            event_key="event-1",
            event_token_key="event-1:up",
            side="BUY" if "buy" in order_type else "SELL",
            intent_type="entry" if "buy" in order_type else "exit",
            order_type=order_type,
            shares=5.0,
            limit_price=0.5 if order_type.startswith("limit") else None,
            decision_at_utc=decision,
            source_attribution=("source-a",),
        )
        for order_type in order_types
    ]
    duplicate = create_execution_intent(
        candidate_key="candidate-1",
        strategy_id="strategy-1",
        run_id="run-1",
        event_key="event-1",
        event_token_key="event-1:up",
        side="BUY",
        intent_type="entry",
        order_type="market_buy",
        shares=5.0,
        decision_at_utc=decision,
        source_attribution=("source-a",),
    )
    book = IntentBook()

    assert {intent.order_type for intent in intents} == set(order_types)
    assert book.add(intents[0]) is True
    assert book.add(duplicate) is False
    assert intents[0].intent_key == duplicate.intent_key
    assert all(intent.orders_allowed is False for intent in intents)


def test_partial_fill_unfilled_and_reconciled_position_behavior_pytest() -> None:
    intent = _buy_intent()
    order = create_order_from_intent(intent, exchange_order_id="remote-1")
    partial_order = transition_order(order, status="partially_filled")
    unfilled_order = transition_order(order, status="unfilled")
    pending_fill = record_fill(partial_order, filled_shares=2.0, fill_price=0.4, reconciled=False)
    reconciled_fill = record_fill(partial_order, filled_shares=2.0, fill_price=0.4, reconciled=True)

    assert order_counts_as_trade(partial_order) is True
    assert order_counts_as_trade(unfilled_order) is False
    assert create_position_from_buy_fill(pending_fill) is None
    position = create_position_from_buy_fill(reconciled_fill)
    assert position is not None
    assert position.shares == 2.0
    assert position.cost_basis_usd == 0.8


def test_lifecycle_coverage_and_duplicate_exit_checks_pytest() -> None:
    position = create_position_from_buy_fill(record_fill(transition_order(create_order_from_intent(_buy_intent()), status="filled"), filled_shares=5, fill_price=0.5, reconciled=True))
    assert position is not None

    assert validate_lifecycle_coverage([position], []) == (f"missing_lifecycle_coverage:{position.position_key}",)
    plan = create_exit_plan(position, coverage_type="active_cashout_order")
    exit_a = create_exit_order(plan, order_key="order-exit-a")
    exit_b = create_exit_order(plan, order_key="order-exit-b")

    assert validate_lifecycle_coverage([position], [plan]) == ()
    assert validate_duplicate_exit_orders([exit_a, exit_b]) == (f"duplicate_exit_order:{plan.exit_plan_key}",)


def test_reconciliation_mismatch_and_restart_reload_pytest(tmp_path: Path) -> None:
    order = transition_order(create_order_from_intent(_buy_intent(), exchange_order_id="remote-1"), status="partially_filled")
    fill = record_fill(order, filled_shares=2.0, fill_price=0.5, reconciled=True)
    mismatch = reconcile_order(local_order=order, local_fills=[fill], remote_order={"exchange_order_id": "remote-1", "filled_shares": 1.0, "status": "partially_filled"})
    match = reconcile_order(local_order=order, local_fills=[fill], remote_order={"exchange_order_id": "remote-1", "filled_shares": 2.0, "status": "partially_filled"})

    assert mismatch.reconciled is False
    assert mismatch.blockers == ("filled_size_mismatch",)
    assert match.reconciled is True

    db_path = tmp_path / "restart.sqlite"
    with connect(db_path) as conn:
        create_schema(conn)
        conn.execute(
            """
            INSERT INTO orders(order_key, intent_key, exchange_order_id, status, order_json, updated_at_utc)
            VALUES('order-1', 'intent-1', 'remote-1', 'submitted', '{}', '2026-06-03T01:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO positions(position_key, strategy_id, event_token_key, shares, cost_basis_usd, status, opened_at_utc, updated_at_utc)
            VALUES('position-1', 'strategy-1', 'event-1:up', 2.0, 1.0, 'open', '2026-06-03T01:00:00+00:00', '2026-06-03T01:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO exit_plans(exit_plan_key, position_key, coverage_type, status, plan_json, created_at_utc, updated_at_utc)
            VALUES('exit-1', 'position-1', 'hold_to_settlement_policy', 'active', '{}', '2026-06-03T01:00:00+00:00', '2026-06-03T01:00:00+00:00')
            """
        )
        state = reload_active_state(conn)

    assert len(state["open_orders"]) == 1
    assert len(state["open_positions"]) == 1
    assert len(state["active_exit_plans"]) == 1


def test_executor_boundary_requires_all_gates_and_rejects_manual_order_phrasing_pytest() -> None:
    blocked = ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=True,
    )
    ready = ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=True,
        live_risk_acknowledged=True,
    )

    assert executor_boundary_ready(blocked) is False
    assert executor_boundary_ready(ready) is True
    assert reject_manual_order_instruction("please place order now") is True
    assert reject_manual_order_instruction("record a structural test result") is False


def _buy_intent():
    return create_execution_intent(
        candidate_key="candidate-1",
        strategy_id="strategy-1",
        run_id="run-1",
        event_key="event-1",
        event_token_key="event-1:up",
        side="BUY",
        intent_type="entry",
        order_type="market_buy",
        shares=5.0,
        decision_at_utc=datetime(2026, 6, 3, 1, 0, tzinfo=UTC),
        source_attribution=("source-a",),
    )
