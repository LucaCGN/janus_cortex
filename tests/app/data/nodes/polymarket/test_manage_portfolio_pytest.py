from __future__ import annotations

from app.data.nodes.polymarket.blockchain import manage_portfolio
from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials


def test_cancel_order_wraps_order_id_in_v2_payload_pytest(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        def set_api_creds(self, api_creds) -> None:
            captured["api_creds"] = api_creds

        def cancel_order(self, payload):
            captured["payload"] = payload
            return {"canceled": [payload.orderID], "not_canceled": {}}

    monkeypatch.setattr(manage_portfolio, "ClobClient", _FakeClient)

    result = manage_portfolio.cancel_order(
        PolymarketCredentials(
            wallet_address="0x0000000000000000000000000000000000000001",
            private_key="0xabc",
            api_key="key",
            secret="secret",
            passphrase="pass",
            funder_address="0x0000000000000000000000000000000000000001",
            chain_id=137,
            signature_type=1,
        ),
        "0xorder",
    )

    assert result.success is True
    assert captured["payload"].orderID == "0xorder"
    assert result.raw == {"canceled": ["0xorder"], "not_canceled": {}}


def test_view_orders_keeps_live_status_and_original_size_pytest(monkeypatch) -> None:
    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        def set_api_creds(self, api_creds) -> None:
            pass

        def get_open_orders(self):
            return [
                {
                    "id": "0xlive",
                    "market": "0xcondition",
                    "asset_id": "123",
                    "side": "SELL",
                    "original_size": "5",
                    "size_matched": "0",
                    "price": "0.31",
                    "status": "LIVE",
                    "created_at": 1778118832,
                },
                {
                    "id": "0xmatched",
                    "market": "0xcondition",
                    "asset_id": "123",
                    "side": "BUY",
                    "original_size": "5",
                    "size_matched": "5",
                    "price": "0.22",
                    "status": "MATCHED",
                    "created_at": 1778118565,
                },
            ]

    monkeypatch.setattr(manage_portfolio, "ClobClient", _FakeClient)

    orders = manage_portfolio.view_orders(
        PolymarketCredentials(
            wallet_address="0x0000000000000000000000000000000000000001",
            private_key="0xabc",
            api_key="key",
            secret="secret",
            passphrase="pass",
            funder_address="0x0000000000000000000000000000000000000001",
            chain_id=137,
            signature_type=1,
        ),
        open_only=True,
    )

    assert [order.id for order in orders] == ["0xlive"]
    assert orders[0].status == "LIVE"
    assert orders[0].size == 5.0
