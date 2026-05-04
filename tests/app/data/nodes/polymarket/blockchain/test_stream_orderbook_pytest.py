from __future__ import annotations

import logging

import httpx
import pytest
from py_clob_client_v2.exceptions import PolyApiException

from app.data.nodes.polymarket.blockchain import stream_orderbook
from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials


def _test_creds() -> PolymarketCredentials:
    return PolymarketCredentials(
        wallet_address="",
        private_key=None,
        clob_host="https://clob.polymarket.com",
        chain_id=137,
    )


def test_fetch_orderbook_missing_orderbook_is_rate_limited_pytest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _MissingOrderbookClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_order_book(self, token_id: str) -> object:
            response = httpx.Response(404, json={"error": "No orderbook exists for the requested token id"})
            raise PolyApiException(resp=response)

    stream_orderbook._ORDERBOOK_WARNING_LOG_STATE.clear()

    with caplog.at_level(logging.DEBUG):
        first = stream_orderbook.fetch_orderbook(
            creds=_test_creds(),
            token_id="token-404",
            market_id="market-404",
            client_factory=_MissingOrderbookClient,
        )
        second = stream_orderbook.fetch_orderbook(
            creds=_test_creds(),
            token_id="token-404",
            market_id="market-404",
            client_factory=_MissingOrderbookClient,
        )

    assert first.token_id == "token-404"
    assert first.bids == []
    assert first.asks == []
    assert second.token_id == "token-404"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    debugs = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert "missing orderbook" in warnings[0].message
    assert any("suppressed" in record.message for record in debugs)


def test_fetch_orderbook_unexpected_error_still_raises_pytest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _BrokenClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_order_book(self, token_id: str) -> object:
            raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="boom"):
        stream_orderbook.fetch_orderbook(
            creds=_test_creds(),
            token_id="token-broken",
            market_id="market-broken",
            client_factory=_BrokenClient,
        )

    assert any("failed token_id=token-broken" in record.message for record in caplog.records)


def test_fetch_orderbook_parses_v2_dict_response_pytest() -> None:
    class _V2DictClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_order_book(self, token_id: str) -> dict[str, object]:
            return {
                "bids": [{"price": "0.41", "size": "7"}, {"price": "0.42", "size": "5"}],
                "asks": [{"price": "0.6", "size": "5"}, {"price": "0.59", "size": "9"}],
                "min_order_size": "5",
                "tick_size": "0.001",
            }

    snapshot = stream_orderbook.fetch_orderbook(
        creds=_test_creds(),
        token_id="token-v2",
        market_id="market-v2",
        client_factory=_V2DictClient,
    )

    assert snapshot.bids[0].price == 0.42
    assert snapshot.asks[0].price == 0.59
    assert snapshot.min_order_size == 5.0
    assert snapshot.tick_size == 0.001
