from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.routers import portfolio as portfolio_router
from app.modules.agentic.global_portfolio import build_execution_gate_snapshot, build_manager_action_plan


ACCOUNT_ID = "56964015-5935-5035-bdab-b056c9277146"
MARKET_ID = "2ec1fbfd-2903-574e-82f9-a1d4b684ef44"
OUTCOME_ID = "11111111-1111-4111-8111-111111111111"
EVENT_SLUG = "nba-det-cle-2026-05-09"


def _global_portfolio_execution_proof_kwargs() -> dict[str, object]:
    return {
        "approved_execution_path": "janus_portfolio_order_management",
        "adapter_name": "janus_portfolio_manager_order_management_v1",
        "adapter_version": "preview-first",
        "risk_budget_name": "global-portfolio-existing-position-target-maintenance-v1",
        "risk_budget": {
            "name": "global-portfolio-existing-position-target-maintenance-v1",
            "scope": "global-portfolio",
            "max_notional_usd": 10.0,
            "used_notional_usd": 0.0,
            "action_notional_usd": 1.95,
        },
        "minimum_order_proof": {
            "side": "sell",
            "order_type": "limit",
            "price": 0.39,
            "size": 5.0,
            "notional_usd": 1.95,
            "min_size": 5.0,
            "min_buy_notional_usd": 1.0,
        },
        "target_stop_rebuy_policy": True,
        "target_stop_rebuy_policy_detail": {
            "policy_name": "existing-position-target-maintenance-v1",
            "target_policy": "place_or_replace_limit_sell_target_after_review",
            "target_price": 0.39,
            "stop_policy": "no autonomous stop; review deterioration manually",
            "rebuy_policy": "no autonomous rebuy; record rebuy-watch only after exit",
            "reason": "Existing operator/global position has no matching direct sell target.",
        },
        "kill_switch_clearance": {
            "clear": True,
            "source": "janus_status_and_live_strategy_worker_status",
            "checked_at_utc": "2026-05-18T13:20:00Z",
            "blocked_reasons": [],
        },
        "idempotency_key": "unit-test-existing-target-token-demo",
        "reconciliation_plan": {"target": "Janus portfolio action ledger then order reconciliation"},
    }


def _trade_row(
    trade_id: str,
    *,
    tx_hash: str,
    side: str,
    price: str,
    size: str,
    trade_time: str,
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "account_id": ACCOUNT_ID,
        "market_id": MARKET_ID,
        "outcome_id": OUTCOME_ID,
        "external_trade_id": None,
        "tx_hash": tx_hash,
        "side": side,
        "price": Decimal(price),
        "size": Decimal(size),
        "fee": Decimal("0"),
        "trade_time": datetime.fromisoformat(trade_time.replace("Z", "+00:00")),
        "event_slug": EVENT_SLUG,
    }


def _order_row(
    order_id: str,
    *,
    external_order_id: str | None,
    side: str,
    status: str,
    size: str,
    limit_price: str,
    metadata_json: dict[str, object] | None = None,
    linked_trade_count: int = 0,
    linked_fill_size: str = "0",
    linked_cashflow_usd: str = "0",
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "account_id": ACCOUNT_ID,
        "market_id": MARKET_ID,
        "outcome_id": OUTCOME_ID,
        "external_order_id": external_order_id,
        "client_order_id": None,
        "side": side,
        "order_type": "limit",
        "time_in_force": "gtc",
        "limit_price": Decimal(limit_price),
        "size": Decimal(size),
        "status": status,
        "placed_at": datetime(2026, 5, 10, 23, 51, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 10, 23, 54, tzinfo=timezone.utc),
        "metadata_json": metadata_json or {},
        "market_question": "Demo market",
        "outcome_label": "Demo",
        "token_id": "token-demo",
        "event_id": "event-demo",
        "event_slug": EVENT_SLUG,
        "linked_trade_count": linked_trade_count,
        "linked_fill_size": Decimal(linked_fill_size),
        "linked_cashflow_usd": Decimal(linked_cashflow_usd),
        "linked_fee_usd": Decimal("0"),
        "linked_trade_ids": ["trade-1"] if linked_trade_count else [],
    }


def test_trade_reconciliation_report_collapses_may_9_duplicate_groups_pytest() -> None:
    unique_rows = [
        _trade_row("buy-7", tx_hash="0xdetbuy7", side="buy", price="0.15", size="7", trade_time="2026-05-09T22:01:00Z"),
        _trade_row("buy-10", tx_hash="0xdetbuy10", side="buy", price="0.08", size="10", trade_time="2026-05-09T22:02:00Z"),
        _trade_row("sell-17", tx_hash="0xdetsell17", side="sell", price="0.14", size="17", trade_time="2026-05-09T22:03:00Z"),
    ]
    rows = unique_rows * 3

    report = portfolio_router.build_trade_reconciliation_report(rows)

    assert report["raw_count"] == 9
    assert report["unique_count"] == 3
    assert report["duplicate_count"] == 6
    assert report["duplicate_group_count"] == 3
    assert report["net_position_size"] == Decimal("0")
    assert report["flat_after_deduplication"] is True
    assert report["buy_notional_usd"] == Decimal("1.85")
    assert report["sell_notional_usd"] == Decimal("2.38")
    assert report["net_cashflow_usd"] == Decimal("0.53")


def test_trade_reconciliation_endpoint_returns_deduped_summary_pytest(monkeypatch) -> None:
    rows = [
        _trade_row("buy-a", tx_hash="0xdetbuy7", side="buy", price="0.15", size="7", trade_time="2026-05-09T22:01:00Z"),
        _trade_row("buy-b", tx_hash="0xdetbuy7", side="buy", price="0.150000", size="7.000000", trade_time="2026-05-09T22:01:00Z"),
        _trade_row("sell-a", tx_hash="0xdetsell7", side="sell", price="0.17", size="7", trade_time="2026-05-09T22:05:00Z"),
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return rows

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.get(
            "/v1/portfolio/trades/reconciliation",
            params={"account_id": ACCOUNT_ID, "event_slug": EVENT_SLUG},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    reconciliation = payload["reconciliation"]
    assert payload["filters"]["account_id"] == ACCOUNT_ID
    assert payload["filters"]["event_slug"] == EVENT_SLUG
    assert reconciliation["raw_count"] == 3
    assert reconciliation["unique_count"] == 2
    assert reconciliation["duplicate_count"] == 1
    assert reconciliation["flat_after_deduplication"] is True
    assert reconciliation["net_cashflow_usd"] == 0.14


def test_order_lifecycle_report_flags_direct_flat_unknown_and_actor_cashflow_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="submitted",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10", "strategy_family": "min-underdog-band-grid-v2"},
            linked_trade_count=1,
            linked_fill_size="5",
            linked_cashflow_usd="-1.55",
        ),
        _order_row(
            "manual-protect",
            external_order_id="0xsell",
            side="sell",
            status="submitted",
            size="5",
            limit_price="0.65",
            metadata_json={
                "reaction_type": "operator_intervention_target",
                "reaction_owner": "janus_internal_reactor_v0",
            },
        ),
    ]

    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
    )

    assert report["order_count"] == 2
    assert report["linked_order_count"] == 1
    assert report["unknown_lifecycle_count"] == 1
    assert report["pnl_attribution_ready"] is False
    assert report["lifecycle_status_counts"] == {"direct_flat_status_unknown": 1, "filled": 1}
    assert report["actor_summary"]["janus_strategy"]["linked_cashflow_usd"] == Decimal("-1.55")
    assert report["actor_summary"]["manual_target_exit"]["unknown_lifecycle_count"] == 1
    assert report["items"][1]["lifecycle_status"] == "direct_flat_status_unknown"


def test_order_lifecycle_report_uses_direct_trade_evidence_when_local_fill_missing_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="submitted",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10", "strategy_family": "min-underdog-band-grid-v2"},
        )
    ]

    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
        direct_trade_rows=[
            {
                "id": "clob-trade-1",
                "taker_order_id": "0xbuy",
                "price": "0.31",
                "size": "5",
            }
        ],
    )

    assert report["unknown_lifecycle_count"] == 0
    assert report["pnl_attribution_ready"] is True
    assert report["lifecycle_status_counts"] == {"filled": 1}
    assert report["direct_context"]["trade_count"] == 1
    assert report["direct_context"]["trade_matched_order_count"] == 1
    assert report["items"][0]["fill_evidence_source"] == "direct_clob_trades"
    assert report["items"][0]["direct_fill_size"] == Decimal("5")
    assert report["items"][0]["effective_cashflow_usd"] == Decimal("-1.55")
    assert report["actor_summary"]["janus_strategy"]["effective_cashflow_usd"] == Decimal("-1.55")


def test_order_lifecycle_report_dedupes_duplicate_direct_trade_rows_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="submitted",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10", "strategy_family": "min-underdog-band-grid-v2"},
        )
    ]

    direct_trade = {
        "id": "clob-trade-1",
        "taker_order_id": "0xbuy",
        "price": "0.31",
        "size": "5",
    }
    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
        direct_trade_rows=[direct_trade, dict(direct_trade)],
    )

    assert report["direct_context"]["trade_count"] == 2
    assert report["direct_context"]["deduped_trade_count"] == 1
    assert report["direct_context"]["duplicate_trade_count"] == 1
    assert report["items"][0]["direct_trade_count"] == 1
    assert report["items"][0]["direct_fill_size"] == Decimal("5")
    assert report["items"][0]["effective_cashflow_usd"] == Decimal("-1.55")
    assert report["actor_summary"]["janus_strategy"]["effective_cashflow_usd"] == Decimal("-1.55")


def test_order_status_backfill_plan_updates_only_full_fill_evidence_pytest() -> None:
    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        [
            _order_row(
                "janus-buy",
                external_order_id="0xbuy",
                side="buy",
                status="submitted",
                size="5",
                limit_price="0.31",
                metadata_json={"run_id": "live-2026-05-10"},
            ),
            _order_row(
                "manual-protect",
                external_order_id="0xsell",
                side="sell",
                status="submitted",
                size="5",
                limit_price="0.65",
                metadata_json={"reaction_type": "operator_intervention_target"},
            ),
        ],
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
        direct_trade_rows=[
            {
                "id": "clob-trade-1",
                "taker_order_id": "0xbuy",
                "price": "0.31",
                "size": "5",
            }
        ],
    )

    plan = portfolio_router.build_order_status_backfill_plan(report)

    assert plan["action_counts"] == {"review_required": 1, "update_status": 1}
    by_order = {action["order_id"]: action for action in plan["actions"]}
    assert by_order["janus-buy"]["action"] == "update_status"
    assert by_order["janus-buy"]["target_status"] == "filled"
    assert by_order["manual-protect"]["action"] == "review_required"
    assert by_order["manual-protect"]["reason"] == "missing_direct_fill_or_terminal_status_evidence"


def test_order_status_backfill_plan_can_expire_reviewed_direct_flat_open_rows_pytest() -> None:
    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        [
            _order_row(
                "janus-buy",
                external_order_id="0xbuy",
                side="buy",
                status="submitted",
                size="5",
                limit_price="0.30",
                metadata_json={"run_id": "live-2026-05-18"},
            ),
        ],
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
    )

    default_plan = portfolio_router.build_order_status_backfill_plan(report)
    expiry_plan = portfolio_router.build_order_status_backfill_plan(
        report,
        expire_direct_flat_open_orders=True,
    )

    assert default_plan["actions"][0]["action"] == "review_required"
    assert expiry_plan["action_counts"] == {"update_status": 1}
    assert expiry_plan["eligible_update_count"] == 1
    assert expiry_plan["actions"][0]["target_status"] == "expired"
    assert expiry_plan["actions"][0]["reason"] == "reviewed_direct_flat_open_order_expiry"


def test_order_lifecycle_reconciliation_endpoint_returns_direct_flat_unknown_pytest(monkeypatch) -> None:
    rows = [
        _order_row(
            "manual-protect",
            external_order_id="0xsell",
            side="sell",
            status="submitted",
            size="5",
            limit_price="0.65",
            metadata_json={"reaction_type": "operator_intervention_target"},
        )
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return rows

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.get(
            "/v1/portfolio/orders/reconciliation",
            params={
                "account_id": ACCOUNT_ID,
                "event_slug": EVENT_SLUG,
                "direct_open_order_count": 0,
                "direct_open_position_count": 0,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    reconciliation = payload["reconciliation"]
    assert payload["filters"]["event_slug"] == EVENT_SLUG
    assert reconciliation["direct_context"]["direct_flat_snapshot"] is True
    assert reconciliation["items"][0]["actor_label"] == "manual_target_exit"
    assert reconciliation["items"][0]["lifecycle_status"] == "direct_flat_status_unknown"


def test_order_lifecycle_reconciliation_endpoint_uses_direct_clob_trade_evidence_pytest(monkeypatch) -> None:
    rows = [
        _order_row(
            "manual-protect",
            external_order_id="0xsell",
            side="sell",
            status="submitted",
            size="5",
            limit_price="0.65",
            metadata_json={"reaction_type": "operator_intervention_target"},
        )
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return rows

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    def fake_direct_evidence(connection, *, account_id: str):
        return {
            "enabled": True,
            "ok": True,
            "error": None,
            "open_order_external_ids": [],
            "open_order_count": 0,
            "open_position_count": 0,
            "trade_count": 1,
            "trades": [
                {
                    "id": "clob-trade-1",
                    "maker_order_id": "0xsell",
                    "price": "0.65",
                    "size": "5",
                }
            ],
        }

    monkeypatch.setattr(portfolio_router, "_fetch_direct_order_lifecycle_evidence", fake_direct_evidence)

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.get(
            "/v1/portfolio/orders/reconciliation",
            params={
                "account_id": ACCOUNT_ID,
                "event_slug": EVENT_SLUG,
                "include_direct_clob_evidence": "true",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    reconciliation = payload["reconciliation"]
    assert payload["filters"]["include_direct_clob_evidence"] is True
    assert payload["direct_evidence"]["ok"] is True
    assert reconciliation["unknown_lifecycle_count"] == 0
    assert reconciliation["direct_context"]["trade_count"] == 1
    assert reconciliation["items"][0]["lifecycle_status"] == "filled"
    assert reconciliation["items"][0]["fill_evidence_source"] == "direct_clob_trades"
    assert reconciliation["items"][0]["effective_cashflow_usd"] == 3.25


def test_order_status_backfill_endpoint_dry_run_returns_reviewed_actions_pytest(monkeypatch) -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="submitted",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10"},
        )
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return rows

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    def fake_direct_evidence(connection, *, account_id: str):
        return {
            "enabled": True,
            "ok": True,
            "error": None,
            "open_order_external_ids": [],
            "open_order_count": 0,
            "open_position_count": 0,
            "trade_count": 1,
            "trades": [
                {
                    "id": "clob-trade-1",
                    "taker_order_id": "0xbuy",
                    "price": "0.31",
                    "size": "5",
                }
            ],
        }

    monkeypatch.setattr(portfolio_router, "_fetch_direct_order_lifecycle_evidence", fake_direct_evidence)

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/orders/reconciliation/status-backfill",
            json={
                "account_id": ACCOUNT_ID,
                "event_slug": EVENT_SLUG,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["status_backfill"]["eligible_update_count"] == 1
    assert payload["status_backfill"]["actions"][0]["action"] == "update_status"
    assert payload["status_backfill"]["actions"][0]["target_status"] == "filled"
    assert payload["applied"] == []


def test_direct_trade_backfill_plan_creates_missing_portfolio_trade_actions_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="submitted",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10"},
        )
    ]
    direct_trades = [
        {
            "id": "clob-trade-1",
            "taker_order_id": "0xbuy",
            "price": "0.31",
            "size": "5",
            "timestamp": 1_778_439_600,
        }
    ]
    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
        direct_trade_rows=direct_trades,
    )

    plan = portfolio_router.build_direct_trade_backfill_plan(report, direct_trade_rows=direct_trades)

    assert plan["eligible_upsert_count"] == 1
    assert plan["review_required_count"] == 0
    action = plan["actions"][0]
    assert action["action"] == "upsert_trade"
    assert action["order_id"] == "janus-buy"
    assert action["external_trade_id"] == "clob-trade-1"
    assert action["side"] == "buy"
    assert action["cashflow_usd"] == Decimal("-1.55")
    assert action["liquidity_role"] == "taker"


def test_pnl_attribution_report_splits_actor_cashflow_and_residual_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="filled",
            size="10",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10"},
            linked_trade_count=1,
            linked_fill_size="10",
            linked_cashflow_usd="-3.10",
        ),
        _order_row(
            "janus-target",
            external_order_id="0xsell",
            side="sell",
            status="filled",
            size="10",
            limit_price="0.39",
            metadata_json={"run_id": "live-2026-05-10"},
            linked_trade_count=1,
            linked_fill_size="10",
            linked_cashflow_usd="3.90",
        ),
    ]
    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
        direct_trade_rows=[],
    )

    attribution = portfolio_router.build_portfolio_pnl_attribution_report(
        report,
        opening_collateral_usd=Decimal("100.00"),
        closing_collateral_usd=Decimal("100.80"),
        final_winning_outcome_id=OUTCOME_ID,
    )

    buckets = {bucket["actor_label"]: bucket for bucket in attribution["buckets"]}
    assert attribution["known_cashflow_usd"] == Decimal("0.80")
    assert attribution["direct_collateral_delta_usd"] == Decimal("0.80")
    assert attribution["residual_cashflow_usd"] == Decimal("0.00")
    assert attribution["residual_status"] == "balanced"
    assert attribution["pnl_attribution_ready"] is True
    assert buckets["janus_strategy"]["known_cashflow_usd"] == Decimal("-3.10")
    assert buckets["janus_target_exit"]["known_cashflow_usd"] == Decimal("3.90")
    assert buckets["janus_strategy"]["winning_outcome_cashflow_usd"] == Decimal("-3.10")


def test_pnl_attribution_report_marks_unexplained_direct_residual_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="filled",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10"},
            linked_trade_count=1,
            linked_fill_size="5",
            linked_cashflow_usd="-1.55",
        )
    ]
    report = portfolio_router.build_order_lifecycle_reconciliation_report(
        rows,
        direct_open_order_external_ids=[],
        direct_open_order_count=0,
        direct_open_position_count=0,
        direct_trade_rows=[],
    )

    attribution = portfolio_router.build_portfolio_pnl_attribution_report(
        report,
        opening_collateral_usd=Decimal("100.00"),
        closing_collateral_usd=Decimal("99.00"),
        final_winning_outcome_id=OUTCOME_ID,
    )

    residual_bucket = attribution["buckets"][-1]
    assert attribution["known_cashflow_usd"] == Decimal("-1.55")
    assert attribution["direct_collateral_delta_usd"] == Decimal("-1.00")
    assert attribution["residual_cashflow_usd"] == Decimal("0.55")
    assert attribution["residual_status"] == "unexplained_residual"
    assert attribution["pnl_attribution_ready"] is False
    assert residual_bucket["actor_label"] == "unknown_residual"
    assert residual_bucket["known_cashflow_usd"] == Decimal("0.55")


def test_pnl_attribution_endpoint_returns_actor_buckets_pytest() -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="filled",
            size="10",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10"},
            linked_trade_count=1,
            linked_fill_size="10",
            linked_cashflow_usd="-3.10",
        ),
        _order_row(
            "janus-target",
            external_order_id="0xsell",
            side="sell",
            status="filled",
            size="10",
            limit_price="0.39",
            metadata_json={"run_id": "live-2026-05-10"},
            linked_trade_count=1,
            linked_fill_size="10",
            linked_cashflow_usd="3.90",
        ),
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return rows

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.get(
            "/v1/portfolio/orders/reconciliation/pnl-attribution",
            params={
                "account_id": ACCOUNT_ID,
                "event_slug": EVENT_SLUG,
                "direct_open_order_count": 0,
                "direct_open_position_count": 0,
                "opening_collateral_usd": "100.00",
                "closing_collateral_usd": "100.80",
                "final_winning_outcome_id": OUTCOME_ID,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    attribution = payload["pnl_attribution"]
    buckets = {bucket["actor_label"]: bucket for bucket in attribution["buckets"]}
    assert attribution["known_cashflow_usd"] == 0.8
    assert attribution["residual_status"] == "balanced"
    assert attribution["pnl_attribution_ready"] is True
    assert buckets["janus_strategy"]["known_cashflow_usd"] == -3.1
    assert buckets["janus_target_exit"]["known_cashflow_usd"] == 3.9


def test_direct_trade_backfill_endpoint_dry_run_returns_reviewed_trade_actions_pytest(monkeypatch) -> None:
    rows = [
        _order_row(
            "janus-buy",
            external_order_id="0xbuy",
            side="buy",
            status="submitted",
            size="5",
            limit_price="0.31",
            metadata_json={"run_id": "live-2026-05-10"},
        )
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return rows

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    def fake_direct_evidence(connection, *, account_id: str):
        return {
            "enabled": True,
            "ok": True,
            "error": None,
            "open_order_external_ids": [],
            "open_order_count": 0,
            "open_position_count": 0,
            "trade_count": 1,
            "trades": [
                {
                    "id": "clob-trade-1",
                    "taker_order_id": "0xbuy",
                    "price": "0.31",
                    "size": "5",
                    "timestamp": 1_778_439_600,
                }
            ],
        }

    monkeypatch.setattr(portfolio_router, "_fetch_direct_order_lifecycle_evidence", fake_direct_evidence)

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/orders/reconciliation/trade-backfill",
            json={
                "account_id": ACCOUNT_ID,
                "event_slug": EVENT_SLUG,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["trade_backfill"]["eligible_upsert_count"] == 1
    assert payload["trade_backfill"]["actions"][0]["action"] == "upsert_trade"
    assert payload["trade_backfill"]["actions"][0]["external_trade_id"] == "clob-trade-1"
    assert payload["applied"] == []


def test_direct_open_order_mirror_plan_maps_token_and_flags_missing_catalog_pytest() -> None:
    captured_at = datetime(2026, 5, 18, 9, 16, tzinfo=timezone.utc)

    plan = portfolio_router.build_direct_open_order_mirror_plan(
        account_id=ACCOUNT_ID,
        direct_open_orders=[
            {
                "id": "0xopen",
                "token_id": "token-demo",
                "side": "SELL",
                "price": "0.39",
                "size": "10",
                "filled_size": "2",
                "status": "LIVE",
                "created_at": 1_779_000_000,
            },
            {
                "id": "0xmissing",
                "token_id": "token-missing",
                "side": "BUY",
                "price": "0.12",
                "size": "5",
            },
        ],
        token_to_pair={"token-demo": (MARKET_ID, OUTCOME_ID)},
        captured_at=captured_at,
    )

    assert plan["direct_order_count"] == 2
    assert plan["eligible_upsert_count"] == 1
    assert plan["review_required_count"] == 1
    upsert = plan["actions"][0]
    assert upsert["action"] == "upsert_order"
    assert upsert["external_order_id"] == "0xopen"
    assert upsert["market_id"] == MARKET_ID
    assert upsert["outcome_id"] == OUTCOME_ID
    assert upsert["side"] == "sell"
    assert upsert["status"] == "open"
    assert upsert["updated_at"] == upsert["placed_at"]
    assert upsert["filled_notional"] == Decimal("0.78")
    assert plan["actions"][1]["reason"] == "missing_token_catalog_mapping"


def test_direct_open_order_mirror_endpoint_dry_run_returns_catalog_mapped_actions_pytest(monkeypatch) -> None:
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.query = query
            self.params = params

        def fetchall(self):
            return [("token-demo", OUTCOME_ID, MARKET_ID)]

    class FakeConnection:
        def cursor(self, *_, **__):
            return FakeCursor()

    def fake_db_connection():
        yield FakeConnection()

    def fake_direct_evidence(connection, *, account_id: str):
        return {
            "enabled": True,
            "ok": True,
            "error": None,
            "open_order_external_ids": ["0xopen"],
            "open_order_count": 1,
            "open_position_count": 1,
            "trade_count": 0,
            "open_orders": [
                {
                    "id": "0xopen",
                    "token_id": "token-demo",
                    "side": "SELL",
                    "price": "0.39",
                    "size": "10",
                    "status": "OPEN",
                }
            ],
            "trades": [],
        }

    monkeypatch.setattr(portfolio_router, "_fetch_direct_order_lifecycle_evidence", fake_direct_evidence)

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/orders/direct-open-mirror",
            params={"account_id": ACCOUNT_ID},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["direct_evidence"]["open_order_count"] == 1
    assert "open_orders" not in payload["direct_evidence"]
    assert payload["direct_open_order_mirror"]["eligible_upsert_count"] == 1
    assert payload["direct_open_order_mirror"]["actions"][0]["external_order_id"] == "0xopen"
    assert payload["applied"] == []


def test_apply_direct_open_order_mirror_actions_persists_order_and_event_pytest() -> None:
    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.connection.executed.append((query, params))
            if "INSERT INTO portfolio.orders" in query:
                self.row = (params[0],)
            elif "INSERT INTO portfolio.order_events" in query:
                self.row = (params[0],)
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self, *_, **__):
            return FakeCursor(self)

    connection = FakeConnection()
    order_id = "33333333-3333-4333-8333-333333333333"
    applied = portfolio_router.apply_direct_open_order_mirror_actions(
        connection,
        actions=[
            {
                "action": "upsert_order",
                "order_id": order_id,
                "account_id": ACCOUNT_ID,
                "market_id": MARKET_ID,
                "outcome_id": OUTCOME_ID,
                "external_order_id": "0xopen",
                "client_order_id": None,
                "side": "sell",
                "order_type": "limit",
                "time_in_force": "gtc",
                "limit_price": Decimal("0.39"),
                "size": Decimal("10"),
                "status": "open",
                "placed_at": datetime(2026, 5, 18, 9, 16, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 5, 18, 9, 16, tzinfo=timezone.utc),
                "filled_size": Decimal("0"),
                "filled_notional": Decimal("0"),
                "token_id": "token-demo",
                "raw_direct_order": {"id": "0xopen"},
            }
        ],
        reviewed_by="controller",
        reason="mirror direct open order evidence",
    )

    assert applied == [
        {
            "order_id": order_id,
            "external_order_id": "0xopen",
            "applied": True,
            "order_event_inserted": True,
        }
    ]
    assert any("INSERT INTO portfolio.orders" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.order_events" in query for query, _ in connection.executed)


def _manager_action_plan_fixture():
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
        **_global_portfolio_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api"],
        evidence={"direct_open_order_count": 1},
    )
    return build_manager_action_plan(
        gate_snapshot=snapshot,
        proposed_action={"target_state": "target_missing", "desired_state": "record_target_plan_only"},
        generated_at_utc="2026-05-18T13:18:00Z",
    )


def _ready_manager_action_plan_fixture():
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
        **_global_portfolio_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api", "portfolio_ledger"],
        evidence={"direct_open_order_count": 1},
    )
    return build_manager_action_plan(
        gate_snapshot=snapshot,
        proposed_action={"target_state": "target_missing", "desired_state": "review_target_order_preview"},
        management_plan=["Route through the approved portfolio manager order-management preview first."],
        generated_at_utc="2026-05-18T13:20:00Z",
    )


class _UnusedFakeConnection:
    pass


def _unused_fake_db_connection():
    yield _UnusedFakeConnection()


def test_portfolio_manager_action_ledger_endpoint_dry_run_records_no_order_side_effects_pytest() -> None:
    class FakeConnection:
        pass

    def fake_db_connection():
        yield FakeConnection()

    plan = _manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/action-ledger",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "dry_run": True,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["status"] == "planned"
    assert payload["ledger_write_only"] is True
    assert payload["no_order_side_effects"]["orders_placed"] is False
    assert payload["no_order_side_effects"]["orders_prepared"] is False
    ledger = payload["manager_action_ledger"]
    assert ledger["schema_version"] == "global_portfolio_manager_action_ledger_v1"
    assert ledger["status"] == "management_plan_only_execution_gate_missing"
    assert ledger["missing_gates"] == ["approved_order_management_path"]
    assert ledger["order_management_call_required"] is True
    assert ledger["side_effects"]["orders_submitted"] is False
    assert payload["applied"] == []


def test_portfolio_manager_action_ledger_apply_requires_review_metadata_pytest() -> None:
    class FakeConnection:
        pass

    def fake_db_connection():
        yield FakeConnection()

    plan = _manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/action-ledger",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "dry_run": False,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "reviewed_by and reason are required when dry_run=false"


def test_apply_portfolio_manager_action_ledger_persists_ledger_only_pytest() -> None:
    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.connection.executed.append((query, params))
            if "INSERT INTO portfolio.manager_action_ledger" in query:
                self.row = (params[0],)
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self, *_, **__):
            return FakeCursor(self)

    connection = FakeConnection()
    plan = _manager_action_plan_fixture()
    preview = portfolio_router.build_portfolio_manager_action_ledger_preview(
        action_plan=plan.model_dump(mode="json"),
        account_id=ACCOUNT_ID,
    )

    applied = portfolio_router.apply_portfolio_manager_action_ledger(
        connection,
        preview=preview,
        reviewed_by="controller",
        reason="record manager action plan without order side effects",
    )

    assert applied["ledger_id"] == preview["ledger_id"]
    assert applied["applied"] is True
    assert applied["ledger_write_only"] is True
    assert applied["orders_placed"] is False
    assert applied["orders_prepared"] is False
    assert any("INSERT INTO portfolio.manager_action_ledger" in query for query, _ in connection.executed)
    assert not any("place_new_order" in query for query, _ in connection.executed)


def test_portfolio_manager_order_management_blocks_missing_gates_pytest() -> None:
    plan = _manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = _unused_fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {"side": "sell", "limit_price": 0.39, "size": 5},
                "dry_run": True,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    preview = payload["order_management_preview"]
    assert payload["dry_run"] is True
    assert payload["status"] == "blocked_missing_execution_gates"
    assert payload["no_order_side_effects"]["orders_prepared"] is False
    assert preview["approved_order_management_call_available"] is True
    assert preview["order_management_call_accepted"] is False
    assert preview["missing_gates"] == ["approved_order_management_path"]
    assert preview["order_preparation_attempted"] is False
    assert preview["order_submission_attempted"] is False


def test_portfolio_manager_order_management_ready_plan_stays_preview_only_pytest() -> None:
    plan = _ready_manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = _unused_fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {"side": "sell", "limit_price": 0.39, "size": 5},
                "dry_run": True,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    preview = payload["order_management_preview"]
    assert payload["status"] == "dry_run_order_management_preview"
    assert payload["live_order_impact"] == "read-only"
    assert preview["order_management_call_accepted"] is True
    assert preview["execution_authorized_by_gates"] is True
    assert preview["order_preparation_authorized_by_gates"] is True
    assert preview["manager_action_ledger"]["missing_gates"] == []
    assert preview["requested_order"] == {"side": "sell", "limit_price": 0.39, "size": 5}
    assert preview["side_effects"]["orders_placed"] is False
    assert preview["side_effects"]["orders_prepared"] is False


def test_portfolio_manager_order_management_rejects_non_dry_run_pytest() -> None:
    plan = _ready_manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = _unused_fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {"side": "sell", "limit_price": 0.39, "size": 5},
                "dry_run": False,
                "reviewed_by": "controller",
                "reason": "attempt non-dry portfolio management",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "execution_approved=true" in response.json()["error"]["message"]


def test_portfolio_manager_order_management_live_path_requires_runtime_flag_pytest() -> None:
    plan = _ready_manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = _unused_fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {
                    "market_id": MARKET_ID,
                    "outcome_id": OUTCOME_ID,
                    "token_id": "token-demo",
                    "side": "sell",
                    "order_type": "limit",
                    "limit_price": 0.39,
                    "size": 5,
                },
                "dry_run": False,
                "execution_approved": True,
                "reviewed_by": "controller",
                "reason": "unit-test approved portfolio manager placement",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert (
        "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED=true is required"
        in response.json()["error"]["message"]
    )


def test_portfolio_manager_order_management_live_path_places_order_when_gate_proven_pytest(monkeypatch) -> None:
    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.connection.executed.append((query, params))
            if "INSERT INTO portfolio.manager_action_ledger" in query:
                self.row = (params[0],)
            elif "UPDATE portfolio.manager_action_ledger" in query:
                self.row = (params[-1],)
            elif "INSERT INTO portfolio.orders" in query:
                self.row = (params[0],)
            elif "INSERT INTO portfolio.order_events" in query:
                self.row = (params[0],)
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self, *_, **__):
            return FakeCursor(self)

    class FakePlaceResult:
        success = True
        raw = {"orderID": "0xpmorder", "status": "live"}

    connection = FakeConnection()
    placed = {}
    monkeypatch.setenv("JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED", "true")

    def fake_db_connection():
        yield connection

    def fake_place_new_order(creds, request):
        placed["creds"] = creds
        placed["request"] = request
        return FakePlaceResult()

    monkeypatch.setattr(portfolio_router, "_fetch_portfolio_manager_order_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(portfolio_router, "_ensure_market_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        portfolio_router,
        "_validate_market_outcome_relation",
        lambda *_args, **_kwargs: OUTCOME_ID,
    )
    monkeypatch.setattr(
        portfolio_router,
        "_fetch_account_wallet",
        lambda *_args, **_kwargs: {
            "account_id": ACCOUNT_ID,
            "wallet_address": "0x0000000000000000000000000000000000000001",
            "proxy_wallet_address": "0x0000000000000000000000000000000000000002",
            "account_label": "unit-test",
            "is_active": True,
        },
    )
    monkeypatch.setattr(portfolio_router, "place_new_order", fake_place_new_order)

    plan = _ready_manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {
                    "market_id": MARKET_ID,
                    "outcome_id": OUTCOME_ID,
                    "token_id": "token-demo",
                    "side": "sell",
                    "order_type": "limit",
                    "limit_price": 0.39,
                    "size": 5,
                },
                "dry_run": False,
                "execution_approved": True,
                "reviewed_by": "controller",
                "reason": "unit-test approved portfolio manager placement",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    execution = payload["order_management_execution"]
    runtime_evidence = execution["execution_payload"]["runtime_risk_rate_evidence"]
    ledger_finalization = execution["execution_payload"]["manager_action_ledger_finalization"]
    assert payload["dry_run"] is False
    assert payload["live_order_impact"] == "order-path"
    assert execution["status"] == "submitted"
    assert execution["external_order_id"] == "0xpmorder"
    assert execution["ledger_finalized"]["applied"] is True
    assert execution["ledger_finalized"]["result"] == "execution_performed_via_approved_portfolio_manager_path"
    assert execution["side_effects"]["orders_prepared"] is True
    assert execution["side_effects"]["orders_submitted"] is True
    assert execution["side_effects"]["orders_placed"] is True
    assert ledger_finalization["schema_version"] == "portfolio_manager_order_management_ledger_finalization_v1"
    assert ledger_finalization["status"] == "submitted"
    assert ledger_finalization["result"] == "execution_performed_via_approved_portfolio_manager_path"
    assert ledger_finalization["external_order_id"] == "0xpmorder"
    assert ledger_finalization["post_confirmation_reconciliation"]["required"] is True
    assert ledger_finalization["transaction_broadcast_attempted"] is False
    assert runtime_evidence["schema_version"] == "portfolio_manager_runtime_risk_rate_evidence_v1"
    assert runtime_evidence["risk_limits_source"] == "app.api.guards.load_order_risk_limits"
    assert runtime_evidence["risk_checks"] == {
        "size_within_limit": True,
        "limit_price_within_bounds": True,
        "notional_within_limit": True,
        "limit_order_only": True,
    }
    assert runtime_evidence["rate_limit"]["action"] == "portfolio_manager_place_order"
    assert runtime_evidence["rate_limit"]["allowed"] is True
    assert runtime_evidence["requested_order"]["notional_usd"] == 1.95
    assert placed["request"].token_id == "token-demo"
    assert placed["request"].side.value == "SELL"
    assert placed["request"].price == 0.39
    assert any("INSERT INTO portfolio.manager_action_ledger" in query for query, _ in connection.executed)
    assert any("UPDATE portfolio.manager_action_ledger" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.orders" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.order_events" in query for query, _ in connection.executed)


def test_portfolio_manager_order_management_requires_external_order_id_confirmation_pytest(monkeypatch) -> None:
    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.connection.executed.append((query, params))
            if "INSERT INTO portfolio.manager_action_ledger" in query:
                self.row = (params[0],)
            elif "UPDATE portfolio.manager_action_ledger" in query:
                self.row = (params[-1],)
            elif "INSERT INTO portfolio.orders" in query:
                self.row = (params[0],)
            elif "INSERT INTO portfolio.order_events" in query:
                self.row = (params[0],)
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self, *_, **__):
            return FakeCursor(self)

    class FakePlaceResult:
        success = True
        raw = {"status": "live"}

    connection = FakeConnection()
    monkeypatch.setenv("JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED", "true")

    def fake_db_connection():
        yield connection

    monkeypatch.setattr(portfolio_router, "_fetch_portfolio_manager_order_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(portfolio_router, "_ensure_market_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        portfolio_router,
        "_validate_market_outcome_relation",
        lambda *_args, **_kwargs: OUTCOME_ID,
    )
    monkeypatch.setattr(
        portfolio_router,
        "_fetch_account_wallet",
        lambda *_args, **_kwargs: {
            "account_id": ACCOUNT_ID,
            "wallet_address": "0x0000000000000000000000000000000000000001",
            "proxy_wallet_address": "0x0000000000000000000000000000000000000002",
            "account_label": "unit-test",
            "is_active": True,
        },
    )
    monkeypatch.setattr(portfolio_router, "place_new_order", lambda *_args, **_kwargs: FakePlaceResult())

    plan = _ready_manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {
                    "market_id": MARKET_ID,
                    "outcome_id": OUTCOME_ID,
                    "token_id": "token-demo",
                    "side": "sell",
                    "order_type": "limit",
                    "limit_price": 0.39,
                    "size": 5,
                },
                "dry_run": False,
                "execution_approved": True,
                "reviewed_by": "controller",
                "reason": "unit-test approved portfolio manager placement",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    execution = payload["order_management_execution"]
    ledger_finalization = execution["execution_payload"]["manager_action_ledger_finalization"]
    assert execution["status"] == "submit_confirmation_missing"
    assert execution["external_order_id"] is None
    assert execution["event_type"] == "portfolio_manager_place_confirmation_missing"
    assert execution["side_effects"]["orders_prepared"] is True
    assert execution["side_effects"]["orders_submitted"] is True
    assert execution["side_effects"]["orders_placed"] is False
    assert execution["ledger_finalized"]["result"] == "approved_portfolio_manager_path_submission_unconfirmed"
    assert ledger_finalization["result"] == "approved_portfolio_manager_path_submission_unconfirmed"
    assert ledger_finalization["post_confirmation_reconciliation"]["expected_external_order_id"] is None
    assert any("UPDATE portfolio.manager_action_ledger" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.orders" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.order_events" in query for query, _ in connection.executed)


def test_portfolio_manager_order_management_live_path_rejects_order_proof_mismatch_pytest(monkeypatch) -> None:
    monkeypatch.setenv("JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED", "true")
    plan = _ready_manager_action_plan_fixture()
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = _unused_fake_db_connection

    try:
        response = client.post(
            "/v1/portfolio/manager/order-management",
            json={
                "account_id": ACCOUNT_ID,
                "action_plan": plan.model_dump(mode="json"),
                "requested_order": {
                    "market_id": MARKET_ID,
                    "outcome_id": OUTCOME_ID,
                    "token_id": "token-demo",
                    "side": "sell",
                    "order_type": "limit",
                    "limit_price": 0.4,
                    "size": 5,
                },
                "dry_run": False,
                "execution_approved": True,
                "reviewed_by": "controller",
                "reason": "unit-test mismatch",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "does not match minimum_order_proof" in response.json()["error"]["message"]


def test_apply_direct_trade_backfill_actions_persists_trade_and_event_pytest() -> None:
    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.connection.executed.append((query, params))
            if "INSERT INTO portfolio.trades" in query:
                self.row = (params[0],)
            elif "INSERT INTO portfolio.order_events" in query:
                self.row = (params[0],)
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self, *_, **__):
            return FakeCursor(self)

    connection = FakeConnection()
    trade_id = "22222222-2222-4222-8222-222222222222"

    applied = portfolio_router.apply_direct_trade_backfill_actions(
        connection,
        actions=[
            {
                "action": "upsert_trade",
                "order_id": "janus-buy",
                "trade_id": trade_id,
                "account_id": ACCOUNT_ID,
                "market_id": MARKET_ID,
                "outcome_id": OUTCOME_ID,
                "side": "buy",
                "price": Decimal("0.31"),
                "size": Decimal("5"),
                "fee": Decimal("0"),
                "fee_asset": None,
                "liquidity_role": "taker",
                "trade_time": datetime(2026, 5, 10, 23, 52, tzinfo=timezone.utc),
                "external_trade_id": "clob-trade-1",
                "tx_hash": None,
                "external_order_id": "0xbuy",
                "actor_label": "janus_strategy",
                "cashflow_usd": Decimal("-1.55"),
                "raw_direct_trade": {"id": "clob-trade-1"},
            }
        ],
        reviewed_by="postgame-review",
        reason="direct CLOB fill evidence matched external order id",
    )

    assert applied == [
        {
            "order_id": "janus-buy",
            "trade_id": trade_id,
            "external_trade_id": "clob-trade-1",
            "applied": True,
            "order_event_inserted": True,
        }
    ]
    assert any("INSERT INTO portfolio.trades" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.order_events" in query for query, _ in connection.executed)


def test_apply_order_status_backfill_actions_updates_idempotently_pytest() -> None:
    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            self.connection.executed.append((query, params))
            if "UPDATE portfolio.orders" in query:
                self.row = {"order_id": params[2]}
            elif "INSERT INTO portfolio.order_events" in query:
                self.row = {"order_event_id": params[0]}
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self, *_, **__):
            return FakeCursor(self)

    connection = FakeConnection()

    applied = portfolio_router.apply_order_status_backfill_actions(
        connection,
        actions=[
            {
                "action": "update_status",
                "order_id": "janus-buy",
                "old_status": "submitted",
                "target_status": "filled",
                "lifecycle_status": "filled",
                "fill_evidence_source": "direct_clob_trades",
                "effective_fill_size": Decimal("5"),
                "effective_cashflow_usd": Decimal("-1.55"),
                "external_order_id": "0xbuy",
                "actor_label": "janus_strategy",
            },
            {"action": "review_required", "order_id": "manual-protect"},
        ],
        reviewed_by="postgame-review",
        reason="direct CLOB fill evidence matched external order id",
    )

    assert applied == [
        {
            "order_id": "janus-buy",
            "old_status": "submitted",
            "target_status": "filled",
            "applied": True,
        }
    ]
    assert any("UPDATE portfolio.orders" in query for query, _ in connection.executed)
    assert any("INSERT INTO portfolio.order_events" in query for query, _ in connection.executed)
