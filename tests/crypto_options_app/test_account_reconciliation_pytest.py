from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.trading.account_reconciliation import (
    AccountPositionSettlement,
    account_position_settlements_from_local_event_resolution,
    _validation_run_id_from_order_json,
    apply_account_position_settlements,
    cancel_underpriced_paired_exit_orders,
    reconcile_pending_exchange_orders,
)


def test_account_settlement_writeback_closes_losing_zero_value_position_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "account-reconciliation.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=8.35)

        summary = apply_account_position_settlements(
            conn,
            [
                AccountPositionSettlement(
                    validation_run_id="validation-live-1",
                    strategy_id="profile_splus_hedger_follow_hold_60s_v8",
                    event_token_key="btc-updown-5m-1780914600:down",
                    event_slug="btc-updown-5m-1780914600",
                    outcome="Down",
                    resolved_outcome="Up",
                    shares=17.5,
                    cost_basis_usd=8.35,
                    realized_pnl_usd=-8.35,
                    settled_at_utc="2026-06-08T10:35:00+00:00",
                    source="polymarket_data_api_positions_lost_zero_value",
                )
            ],
        )

        ledger = conn.execute(
            """
            SELECT realized_pnl_usd, open_cost_usd, hard_stop_triggered,
                   stop_reason, reconciliation_status
              FROM validation_budget_ledger
             WHERE validation_run_id='validation-live-1'
            """
        ).fetchone()
        position = conn.execute("SELECT status FROM positions WHERE position_key='position-live-1'").fetchone()

    assert summary["updated_ledger_count"] == 1
    assert summary["closed_position_count"] == 1
    assert ledger["realized_pnl_usd"] == -8.35
    assert ledger["open_cost_usd"] == 0.0
    assert ledger["hard_stop_triggered"] == 0
    assert ledger["reconciliation_status"] == "reconciled"
    assert position["status"] == "settled"


def test_account_settlement_writeback_triggers_strategy_loss_stop_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "account-reconciliation-stop.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=12.0)

        apply_account_position_settlements(
            conn,
            [
                AccountPositionSettlement(
                    validation_run_id="validation-live-1",
                    strategy_id="profile_splus_hedger_follow_hold_60s_v8",
                    event_token_key="btc-updown-5m-1780914600:down",
                    outcome="Down",
                    shares=25.0,
                    cost_basis_usd=12.0,
                    realized_pnl_usd=-12.0,
                    settled_at_utc="2026-06-08T10:35:00+00:00",
                )
            ],
            default_max_loss_usd=10.0,
        )

        ledger = conn.execute(
            """
            SELECT hard_stop_triggered, stop_reason
              FROM validation_budget_ledger
             WHERE validation_run_id='validation-live-1'
            """
        ).fetchone()

    assert ledger["hard_stop_triggered"] == 1
    assert "account_authoritative_loss_stop" in ledger["stop_reason"]


def test_portfolio_mapper_converts_lost_zero_value_position_to_settlement_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "account-reconciliation-portfolio-map.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=8.35)
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, inserted_at_utc, updated_at_utc
            )
            VALUES('event-1', 'btc-updown-5m-1780914600',
                   '2026-06-08T10:30:00+00:00', '2026-06-08T10:30:00+00:00')
            """,
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug,
                inserted_at_utc, updated_at_utc
            )
            VALUES('btc-updown-5m-1780914600:down', 'event-1', 'token-1', 'Down',
                   'btc-updown-5m-1780914600',
                   '2026-06-08T10:30:00+00:00', '2026-06-08T10:30:00+00:00')
            """,
        )

        from crypto_options_app.trading.account_reconciliation import account_position_settlements_from_portfolio

        settlements = account_position_settlements_from_portfolio(
            conn,
            open_positions=[
                SimpleNamespace(
                    event_slug="btc-updown-5m-1780914600",
                    outcome="Down",
                    current_value=0.0,
                    cash_pnl=-8.35,
                    end_date="2026-06-08T10:35:00+00:00",
                    asset="asset-1",
                )
            ],
            closed_positions=[],
            now_dt=datetime.fromisoformat("2026-06-08T10:36:00+00:00"),
        )

    assert len(settlements) == 1
    assert settlements[0].strategy_id == "profile_splus_hedger_follow_hold_60s_v8"
    assert settlements[0].event_token_key == "btc-updown-5m-1780914600:down"
    assert settlements[0].realized_pnl_usd == -8.35
    assert settlements[0].source == "polymarket_data_api_open_position"


def test_portfolio_mapper_matches_slug_shaped_event_token_without_event_token_row_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "account-reconciliation-slug-shaped-token.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=7.905)
        conn.execute("DELETE FROM positions WHERE position_key='position-live-1'")
        conn.execute(
            """
            INSERT INTO positions(
                position_key, strategy_id, event_token_key, shares,
                cost_basis_usd, status, opened_at_utc, updated_at_utc
            )
            VALUES('position:fill:order:abc123:15.5:0.51',
                   'profile_splus_hedger_follow_hold_60s_v16',
                   'btc-updown-5m-1780946700:down', 15.5, 7.905,
                   'open', '2026-06-08T19:25:07+00:00', '2026-06-08T19:25:07+00:00')
            """
        )

        from crypto_options_app.trading.account_reconciliation import account_position_settlements_from_portfolio

        settlements = account_position_settlements_from_portfolio(
            conn,
            open_positions=[
                SimpleNamespace(
                    slug="btc-updown-5m-1780946700",
                    event_slug="parent-event-not-used-for-local-token-key",
                    outcome="Down",
                    current_value=0.0,
                    cash_pnl=-7.905,
                    end_date="2026-06-08T19:35:00+00:00",
                    asset="asset-live-loss",
                )
            ],
            closed_positions=[],
            now_dt=datetime.fromisoformat("2026-06-08T19:40:00+00:00"),
        )

    assert len(settlements) == 1
    assert settlements[0].strategy_id == "profile_splus_hedger_follow_hold_60s_v16"
    assert settlements[0].event_token_key == "btc-updown-5m-1780946700:down"
    assert settlements[0].realized_pnl_usd == -7.905


def test_local_event_resolution_fallback_settles_omitted_short_market_position_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "account-reconciliation-local-resolution-fallback.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=7.905)
        conn.execute("DELETE FROM positions WHERE position_key='position-live-1'")
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, event_start_time_utc, event_end_time_utc,
                inserted_at_utc, updated_at_utc
            )
            VALUES('570261', 'btc-updown-5m-1780946700',
                   '2026-06-08T19:25:00+00:00', '2026-06-08T19:30:00+00:00',
                   '2026-06-08T19:20:00+00:00', '2026-06-08T19:30:02+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO positions(
                position_key, strategy_id, event_token_key, shares,
                cost_basis_usd, status, opened_at_utc, updated_at_utc
            )
            VALUES('position:fill:order:abc123:15.5:0.51',
                   'profile_splus_hedger_follow_hold_60s_v16',
                   'btc-updown-5m-1780946700:down', 15.5, 7.905,
                   'open', '2026-06-08T19:25:07+00:00', '2026-06-08T19:25:07+00:00')
            """
        )

        settlements = account_position_settlements_from_local_event_resolution(
            conn,
            event_outcome_resolver=lambda slug: "Up",
            now_dt=datetime.fromisoformat("2026-06-08T19:32:00+00:00"),
        )

    assert len(settlements) == 1
    assert settlements[0].strategy_id == "profile_splus_hedger_follow_hold_60s_v16"
    assert settlements[0].event_token_key == "btc-updown-5m-1780946700:down"
    assert settlements[0].resolved_outcome == "Up"
    assert settlements[0].realized_pnl_usd == -7.905


def test_portfolio_mapper_allocates_closed_position_pnl_across_local_positions_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "account-reconciliation-portfolio-closed-map.sqlite")
    now = "2026-06-08T10:30:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, inserted_at_utc, updated_at_utc
            )
            VALUES('event-1', 'eth-updown-5m-1780915200',
                   '2026-06-08T10:40:00+00:00', '2026-06-08T10:40:00+00:00')
            """,
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug,
                inserted_at_utc, updated_at_utc
            )
            VALUES('eth-updown-5m-1780915200:up', 'event-1', 'token-1', 'Up',
                   'eth-updown-5m-1780915200',
                   '2026-06-08T10:40:00+00:00', '2026-06-08T10:40:00+00:00')
            """,
        )
        for position_key, strategy_id, cost_basis in (
            ("position-live-1", "strategy_a", 4.0),
            ("position-live-2", "strategy_b", 6.0),
        ):
            conn.execute(
                """
                INSERT INTO positions(
                    position_key, strategy_id, event_token_key, shares,
                    cost_basis_usd, status, opened_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'eth-updown-5m-1780915200:up', ?, ?, 'open', ?, ?)
                """,
                (position_key, strategy_id, cost_basis * 2, cost_basis, now, now),
            )

        from crypto_options_app.trading.account_reconciliation import account_position_settlements_from_portfolio

        settlements = account_position_settlements_from_portfolio(
            conn,
            open_positions=[],
            closed_positions=[
                SimpleNamespace(
                    event_slug="eth-updown-5m-1780915200",
                    outcome="Up",
                    realized_pnl=10.0,
                    end_date="2026-06-08T10:45:00+00:00",
                    asset="asset-closed-1",
                )
            ],
            now_dt=datetime.fromisoformat("2026-06-08T10:46:00+00:00"),
        )

    by_strategy = {settlement.strategy_id: settlement.realized_pnl_usd for settlement in settlements}
    assert by_strategy == {"strategy_a": 4.0, "strategy_b": 6.0}


def test_validation_run_id_parser_preserves_strategy_scoped_live_run_id_pytest() -> None:
    strategy_id = "master_hedge_grid_floor_tail_reversal_probe_v5"
    payload = {
        "candidate_key": (
            "candidate:strategy-pipeline-live-candidate-20260608T161732Z:"
            f"{strategy_id}:{strategy_id}:eth-updown-5m-1780935300:down"
        ),
        "attribution": {
            "order_source_payload": {
                "live_executor_response": {
                    "legacy_submission": {
                        "order_request": {
                            "app_run_id": f"strategy-pipeline-live-candidate-20260608T161732Z:{strategy_id}"
                        }
                    }
                }
            }
        },
    }

    assert _validation_run_id_from_order_json(payload, strategy_id) == (
        "validation-run:strategy-pipeline-live-candidate-20260608T161732Z:"
        f"{strategy_id}:{strategy_id}"
    )


def test_validation_run_id_parser_falls_back_to_right_split_candidate_key_pytest() -> None:
    strategy_id = "master_hedge_grid_floor_tail_reversal_probe_v5"
    payload = {
        "candidate_key": (
            "candidate:strategy-pipeline-live-candidate-20260608T161732Z:"
            f"{strategy_id}:{strategy_id}:eth-updown-5m-1780935300:down"
        )
    }

    assert _validation_run_id_from_order_json(payload, strategy_id) == (
        "validation-run:strategy-pipeline-live-candidate-20260608T161732Z:"
        f"{strategy_id}:{strategy_id}"
    )


def test_pending_order_reconciliation_closes_matched_paired_exit_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "pending-paired-exit.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=5.0)
        conn.execute(
            "UPDATE positions SET shares=10.0, cost_basis_usd=5.0 WHERE position_key='position-live-1'"
        )
        _insert_submitted_paired_exit(conn)

        summary = reconcile_pending_exchange_orders(
            conn,
            open_orders=[],
            trades=[
                SimpleNamespace(
                    size=10.0,
                    price=0.51,
                    timestamp=1780941600,
                    taker_order_id="",
                    maker_order_id="0xpairedexit",
                )
            ],
            now_dt=datetime.fromisoformat("2026-06-08T18:01:00+00:00"),
        )

        order = conn.execute("SELECT status FROM orders WHERE order_key='order-paired-exit'").fetchone()
        fill = conn.execute(
            "SELECT filled_size, filled_price FROM fills WHERE order_key='order-paired-exit'"
        ).fetchone()
        position = conn.execute("SELECT status FROM positions WHERE position_key='position-live-1'").fetchone()
        exit_order = conn.execute("SELECT status FROM exit_orders WHERE order_key='order-paired-exit'").fetchone()
        ledger = conn.execute(
            """
            SELECT realized_pnl_usd, open_cost_usd, reconciliation_status
              FROM validation_budget_ledger
             WHERE validation_run_id='validation-live-1'
            """
        ).fetchone()

    assert summary["filled_order_count"] == 1
    assert summary["updated_ledger_count"] == 1
    assert order["status"] == "filled"
    assert fill["filled_size"] == 10.0
    assert fill["filled_price"] == 0.51
    assert exit_order["status"] == "filled"
    assert position["status"] == "settled"
    assert ledger["realized_pnl_usd"] == 0.1
    assert ledger["open_cost_usd"] == 0.0
    assert ledger["reconciliation_status"] == "reconciled"


def test_underpriced_paired_exit_guard_cancels_exit_below_effective_basis_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "underpriced-paired-exit.sqlite")
    cancelled_order_ids: list[str] = []
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=5.0)
        conn.execute(
            "UPDATE positions SET shares=10.0, cost_basis_usd=5.0 WHERE position_key='position-live-1'"
        )
        _insert_entry_for_underpriced_paired_exit(conn)
        _insert_submitted_paired_exit(conn)

        summary = cancel_underpriced_paired_exit_orders(
            conn,
            open_orders=[SimpleNamespace(id="0xpairedexit")],
            cancel_order_fn=lambda order_id: cancelled_order_ids.append(order_id) or SimpleNamespace(success=True, raw="ok"),
        )

        order = conn.execute("SELECT status, order_json FROM orders WHERE order_key='order-paired-exit'").fetchone()
        exit_order = conn.execute("SELECT status FROM exit_orders WHERE order_key='order-paired-exit'").fetchone()
        ledger = conn.execute(
            """
            SELECT stop_reason, reconciliation_status, ledger_json
              FROM validation_budget_ledger
             WHERE validation_run_id='validation-live-1'
            """
        ).fetchone()

    assert summary["checked_order_count"] == 1
    assert summary["unsafe_order_count"] == 1
    assert summary["cancelled_order_count"] == 1
    assert cancelled_order_ids == ["0xpairedexit"]
    assert order["status"] == "cancelled"
    assert exit_order["status"] == "cancelled"
    assert "underpriced_paired_exit_cancelled" in ledger["stop_reason"]
    assert ledger["reconciliation_status"] == "pending_safe_exit_replacement"
    assert "paired_exit_safety_guard" in order["order_json"]
    assert "required_exit_price" in ledger["ledger_json"]


def test_pending_order_reconciliation_expires_stale_submitted_without_trade_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "pending-stale-order.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=5.0)
        conn.execute(
            """
            INSERT INTO execution_intents(
                intent_key, candidate_key, strategy_id, event_token_key,
                intent_type, order_type, side, status, intent_json, created_at_utc
            )
            VALUES('intent-stale', 'candidate-stale', 'profile_splus_hedger_follow_hold_60s_v8',
                   'btc-updown-5m-1780914600:down', 'entry', 'limit_buy',
                   'BUY', 'created', '{}', '2026-06-08T17:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO orders(order_key, intent_key, exchange_order_id, status, order_json, submitted_at_utc, updated_at_utc)
            VALUES('order-stale', 'intent-stale', '0xstaleorder', 'submitted', '{}',
                   '2026-06-08T17:00:00+00:00', '2026-06-08T17:00:00+00:00')
            """
        )

        summary = reconcile_pending_exchange_orders(
            conn,
            open_orders=[],
            trades=[],
            now_dt=datetime.fromisoformat("2026-06-08T17:10:01+00:00"),
            stale_after_seconds=300,
        )

        order = conn.execute("SELECT status FROM orders WHERE order_key='order-stale'").fetchone()
        ledger = conn.execute(
            "SELECT realized_pnl_usd, open_cost_usd FROM validation_budget_ledger WHERE validation_run_id='validation-live-1'"
        ).fetchone()

    assert summary["expired_order_count"] == 1
    assert summary["filled_order_count"] == 0
    assert order["status"] == "expired"
    assert ledger["realized_pnl_usd"] == 0.0
    assert ledger["open_cost_usd"] == 5.0


def test_pending_entry_expiry_reverses_optimistic_local_fill_and_position_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "pending-entry-expiry-reversal.sqlite")
    with connect(db_path) as conn:
        _insert_live_ledger_and_position(conn, realized_pnl=0.0, open_cost=7.9)
        conn.execute("DELETE FROM positions WHERE position_key='position-live-1'")
        order_payload = {
            "attribution": {
                "order_source_payload": {
                    "live_executor_response": {
                        "legacy_submission": {
                            "order_request": {"app_run_id": "validation-live-1"}
                        }
                    }
                }
            }
        }
        conn.execute(
            """
            INSERT INTO execution_intents(
                intent_key, candidate_key, strategy_id, event_token_key,
                intent_type, order_type, side, status, intent_json, created_at_utc
            )
            VALUES('intent-entry-stale', 'candidate-entry-stale', 'profile_splus_hedger_follow_hold_60s_v8',
                   'btc-updown-5m-1780914600:down', 'entry', 'limit_buy',
                   'BUY', 'created', '{}', '2026-06-08T17:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO orders(order_key, intent_key, exchange_order_id, status, order_json, submitted_at_utc, updated_at_utc)
            VALUES('order-entry-stale', 'intent-entry-stale', '0xentrystale', 'submitted', ?,
                   '2026-06-08T17:00:00+00:00', '2026-06-08T17:00:00+00:00')
            """,
            (json_dumps(order_payload),),
        )
        conn.execute(
            """
            INSERT INTO fills(fill_key, order_key, event_token_key, filled_size, filled_price, filled_at_utc, source_json, inserted_at_utc)
            VALUES('fill:order-entry-stale:15.5:0.51', 'order-entry-stale',
                   'btc-updown-5m-1780914600:down', 15.5, 0.51,
                   '2026-06-08T17:00:00+00:00', '{}', '2026-06-08T17:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO positions(
                position_key, strategy_id, event_token_key, shares,
                cost_basis_usd, status, opened_at_utc, updated_at_utc
            )
            VALUES('position:fill:order-entry-stale:15.5:0.51',
                   'profile_splus_hedger_follow_hold_60s_v8',
                   'btc-updown-5m-1780914600:down', 15.5, 7.905,
                   'open', '2026-06-08T17:00:00+00:00', '2026-06-08T17:00:00+00:00')
            """
        )

        summary = reconcile_pending_exchange_orders(
            conn,
            open_orders=[],
            trades=[],
            now_dt=datetime.fromisoformat("2026-06-08T17:10:01+00:00"),
            stale_after_seconds=300,
        )

        order = conn.execute("SELECT status FROM orders WHERE order_key='order-entry-stale'").fetchone()
        position = conn.execute(
            "SELECT status FROM positions WHERE position_key='position:fill:order-entry-stale:15.5:0.51'"
        ).fetchone()
        ledger = conn.execute(
            """
            SELECT notional_filled_usd, realized_pnl_usd, open_cost_usd,
                   remaining_validation_budget_usd, stop_reason
              FROM validation_budget_ledger
             WHERE validation_run_id='validation-live-1'
            """
        ).fetchone()

    assert summary["expired_order_count"] == 1
    assert summary["closed_position_count"] == 1
    assert summary["updated_ledger_count"] == 1
    assert order["status"] == "expired"
    assert position["status"] == "expired_unfilled"
    assert ledger["notional_filled_usd"] == 0.0
    assert ledger["realized_pnl_usd"] == 0.0
    assert ledger["open_cost_usd"] == 0.0
    assert ledger["remaining_validation_budget_usd"] == 30.0
    assert "entry_order_expired_without_exchange_fill" in ledger["stop_reason"]


def _insert_live_ledger_and_position(conn, *, realized_pnl: float, open_cost: float) -> None:
    now = "2026-06-08T10:30:00+00:00"
    conn.execute(
        """
        INSERT INTO validation_budget_ledger(
            validation_budget_ledger_key, validation_run_id, strategy_or_component_id,
            started_at_utc, completed_at_utc, budget_cap_usd,
            notional_submitted_usd, notional_filled_usd, realized_pnl_usd,
            open_cost_usd, remaining_validation_budget_usd,
            cash_balance_before_usd, cash_balance_after_usd, cash_balance_status,
            hard_stop_triggered, stop_reason, lifecycle_audit_status,
            reconciliation_status, ledger_json, inserted_at_utc, updated_at_utc
        )
        VALUES('ledger-live-1', 'validation-live-1', 'profile_splus_hedger_follow_hold_60s_v8',
               ?, ?, 30, ?, ?, ?, ?, 30, 100, 92, 'trusted_balance_reported',
               0, NULL, 'passed', 'pending_account_settlement',
               '{"promotion_policy":{"max_supervised_live_loss_usd":10}}', ?, ?)
        """,
        (now, now, open_cost, open_cost, realized_pnl, open_cost, now, now),
    )
    conn.execute(
        """
        INSERT INTO positions(
            position_key, strategy_id, event_token_key, shares,
            cost_basis_usd, status, opened_at_utc, updated_at_utc
        )
        VALUES('position-live-1', 'profile_splus_hedger_follow_hold_60s_v8',
               'btc-updown-5m-1780914600:down', 17.5, ?, 'open', ?, ?)
        """,
        (open_cost, now, now),
    )


def _insert_submitted_paired_exit(conn) -> None:
    now = "2026-06-08T17:58:00+00:00"
    order_payload = {
        "limit_price": 0.51,
        "attribution": {
            "order_source_payload": {
                "live_executor_response": {
                    "legacy_submission": {
                        "order_request": {"app_run_id": "validation-live-1"}
                    }
                }
            }
        }
    }
    conn.execute(
        """
        INSERT INTO execution_intents(
            intent_key, candidate_key, strategy_id, event_token_key,
            intent_type, order_type, side, status, intent_json, created_at_utc
        )
        VALUES('intent-paired-exit', 'candidate-live-1', 'profile_splus_hedger_follow_hold_60s_v8',
               'btc-updown-5m-1780914600:down', 'paired_exit', 'limit_sell',
               'SELL', 'created', ?, ?)
        """,
        (json_dumps({"limit_price": 0.51}), now),
    )
    conn.execute(
        """
        INSERT INTO orders(order_key, intent_key, exchange_order_id, status, order_json, submitted_at_utc, updated_at_utc)
        VALUES('order-paired-exit', 'intent-paired-exit', '0xpairedexit', 'submitted', ?, ?, ?)
        """,
        (json_dumps(order_payload), now, now),
    )
    conn.execute(
        """
        INSERT INTO exit_plans(exit_plan_key, position_key, coverage_type, status, plan_json, created_at_utc, updated_at_utc)
        VALUES('exit-plan-live-1', 'position-live-1', 'paired_exit', 'active', '{}', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO exit_orders(exit_order_key, exit_plan_key, order_key, status, source_json, inserted_at_utc)
        VALUES('exit-order-live-1', 'exit-plan-live-1', 'order-paired-exit', 'submitted', '{}', ?)
        """,
        (now,),
    )


def _insert_entry_for_underpriced_paired_exit(conn) -> None:
    now = "2026-06-08T17:57:00+00:00"
    order_payload = {
        "attribution": {
            "order_source_payload": {
                "live_executor_response": {
                    "legacy_submission": {
                        "order_request": {
                            "app_run_id": "validation-live-1",
                            "price": 0.515,
                            "estimated_total_cost_usd": 5.324843,
                            "size": 10.0,
                        },
                        "execution_quality": {
                            "submitted_limit_price": 0.515,
                            "realized_price": 0.5,
                            "filled_shares": 10.0,
                        },
                        "jit_quote": {"submitted_limit_price": 0.515},
                    }
                }
            }
        }
    }
    conn.execute(
        """
        INSERT INTO execution_intents(
            intent_key, candidate_key, strategy_id, event_token_key,
            intent_type, order_type, side, status, intent_json, created_at_utc
        )
        VALUES('intent-entry-live-1', 'candidate-live-1', 'profile_splus_hedger_follow_hold_60s_v8',
               'btc-updown-5m-1780914600:down', 'entry', 'limit_buy',
               'BUY', 'created', '{}', ?)
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO orders(order_key, intent_key, exchange_order_id, status, order_json, submitted_at_utc, updated_at_utc)
        VALUES('order-entry-live-1', 'intent-entry-live-1', '0xentrylive1', 'filled', ?, ?, ?)
        """,
        (json_dumps(order_payload), now, now),
    )
    conn.execute(
        """
        INSERT INTO fills(fill_key, order_key, event_token_key, filled_size, filled_price, filled_at_utc, source_json, inserted_at_utc)
        VALUES('fill:order-entry-live-1:10.0:0.5', 'order-entry-live-1',
               'btc-updown-5m-1780914600:down', 10.0, 0.5,
               ?, '{}', ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        UPDATE positions
           SET position_key='position:fill:order-entry-live-1:10.0:0.5'
         WHERE position_key='position-live-1'
        """
    )


def json_dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, sort_keys=True)
