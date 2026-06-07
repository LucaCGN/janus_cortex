from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crypto_options_app.data_services.underlying_technical_observers import (
    IFCM_PERIOD_LABELS,
    TechnicalObserverConfig,
    TechnicalObserverSnapshot,
    capture_underlying_technical_observers_once,
)
from crypto_options_app.db.connection import connect


def _fake_snapshot(*, provider: str, symbol: str, interval: str) -> TechnicalObserverSnapshot:
    now = datetime.now(UTC).isoformat()
    components = (
        {
            "group": "moving_average",
            "name": f"{provider}:{symbol}:{interval}:ema10",
            "value": "100.0",
            "action": "BUY",
            "raw": {},
        },
        {
            "group": "oscillator",
            "name": f"{provider}:{symbol}:{interval}:rsi14",
            "value": "45.0",
            "action": "NEUTRAL",
            "raw": {},
        },
    )
    return TechnicalObserverSnapshot(
        provider=provider,
        symbol=symbol,
        interval=interval,
        source_url=f"https://example.test/{provider}/{symbol}/{interval}",
        request_started_at_utc=now,
        observed_at_utc=now,
        completed_at_utc=now,
        latency_ms=12,
        summary={"label": "Buy", "score": 1, "counts": {"BUY": 1, "SELL": 0, "NEUTRAL": 1, "ERROR": 0}},
        components=components,
        raw={"fixture": True},
    )


def test_underlying_technical_observers_write_snapshots_components_readiness_and_watermark(tmp_path) -> None:
    db_path = tmp_path / "technical_observers.sqlite"

    def fake_ifcm(symbol: str, period: str, _instrument_id: str, _group_id: str, _slug: str, _timeout: int):
        return _fake_snapshot(provider="ifcm", symbol=symbol, interval=IFCM_PERIOD_LABELS[period])

    def fake_tradersunion(symbol: str, _slug: str, _timeout: int):
        return _fake_snapshot(provider="tradersunion", symbol=symbol, interval="current")

    summary = capture_underlying_technical_observers_once(
        config=TechnicalObserverConfig(
            db_path=db_path,
            symbols=("BTC", "ETH"),
            ifcm_periods=("1", "5", "15"),
            include_ifcm=True,
            include_tradersunion=True,
        ),
        ifcm_fetcher=fake_ifcm,
        tradersunion_fetcher=fake_tradersunion,
    )

    assert summary.status == "healthy"
    assert summary.snapshot_rows_inserted == 8
    assert summary.component_rows_inserted == 16
    assert summary.readiness_rows_inserted == 2
    assert summary.orders_allowed is False
    assert summary.live_trading_authorized is False

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_technical_observer_snapshots").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM external_technical_observer_components").fetchone()[0] == 16
        readiness_rows = conn.execute(
            """
            SELECT symbol, status, blockers_json
            FROM data_signal_readiness_snapshots
            WHERE data_block='A' AND module_id='underlying_technical_observers'
            ORDER BY symbol
            """
        ).fetchall()
        assert [row["symbol"] for row in readiness_rows] == ["BTC", "ETH"]
        assert {row["status"] for row in readiness_rows} == {"ready"}
        watermark = conn.execute(
            """
            SELECT status, rows_observed, rows_inserted, source
            FROM data_service_watermarks
            WHERE module_id='underlying_technical_observers'
            """
        ).fetchone()
        assert watermark["status"] == "healthy"
        assert watermark["rows_observed"] == 8
        assert watermark["source"] == "ifcm,tradersunion"


def test_underlying_technical_observers_reject_live_flags(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE", "1")
    with pytest.raises(RuntimeError, match="data_service_live_flags_rejected"):
        capture_underlying_technical_observers_once(
            config=TechnicalObserverConfig(db_path=tmp_path / "technical_observers.sqlite")
        )
