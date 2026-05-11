from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.routers import portfolio as portfolio_router


ACCOUNT_ID = "56964015-5935-5035-bdab-b056c9277146"
MARKET_ID = "2ec1fbfd-2903-574e-82f9-a1d4b684ef44"
OUTCOME_ID = "11111111-1111-4111-8111-111111111111"
EVENT_SLUG = "nba-det-cle-2026-05-09"


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
