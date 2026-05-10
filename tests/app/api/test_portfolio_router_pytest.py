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
