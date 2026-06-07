from __future__ import annotations

from pathlib import Path

from crypto_options_app.data_services import underlying_market_price_capture as capture


def test_underlying_market_price_capture_writes_ticks_candles_and_readiness(tmp_path: Path, monkeypatch) -> None:
    def fake_get_json(url: str, *, timeout_seconds: int):
        if url.endswith("/ticker"):
            return {
                "price": "100.50",
                "bid": "100.40",
                "ask": "100.60",
                "time": "2026-06-06T15:00:00.000000Z",
            }
        return [[1780758000, 99.0, 101.0, 100.0, 100.5, 12.0]]

    monkeypatch.setattr(capture, "_get_json", fake_get_json)
    config = capture.UnderlyingMarketPriceConfig(db_path=tmp_path / "crypto_options.sqlite", symbols=("BTC",), candle_limit=1)

    summary = capture.capture_underlying_market_prices_once(config=config)

    assert summary.status == "healthy"
    assert summary.tick_rows_inserted == 1
    assert summary.candle_rows_inserted == 1
    assert summary.readiness_rows_inserted == 1
    assert summary.blockers == ()
