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
