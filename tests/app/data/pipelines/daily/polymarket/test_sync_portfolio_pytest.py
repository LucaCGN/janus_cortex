from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.data.pipelines.daily.polymarket import sync_portfolio


ACCOUNT_ID = "56964015-5935-5035-bdab-b056c9277146"
DET_MARKET_ID = "2ec1fbfd-2903-574e-82f9-a1d4b684ef44"
DET_OUTCOME_ID = "det-outcome"
LAL_MARKET_ID = "02b29b5b-c017-5193-83eb-51c30d2c6e6b"
LAL_OUTCOME_ID = "lal-outcome"


def _trade_id(
    raw: dict[str, Any],
    *,
    market_id: str,
    outcome_id: str,
) -> str:
    trade_time = sync_portfolio._safe_dt(
        raw.get("timestamp"),
        default=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    return sync_portfolio._portfolio_trade_id(
        account_id=ACCOUNT_ID,
        market_id=market_id,
        outcome_id=outcome_id,
        external_trade_id=sync_portfolio._first_present(raw, ["id", "tradeID", "trade_id"]),
        tx_hash=sync_portfolio._first_present(raw, ["transactionHash", "txHash", "tx_hash"]),
        side=str(raw.get("side") or "buy").lower(),
        price=sync_portfolio._safe_float(raw.get("price")),
        size=sync_portfolio._safe_float(raw.get("size")),
        trade_time=trade_time,
    )


def _net_size(rows: list[dict[str, Any]], *, market_id: str, outcome_id: str) -> float:
    unique: dict[str, dict[str, Any]] = {}
    for raw in rows:
        unique[_trade_id(raw, market_id=market_id, outcome_id=outcome_id)] = raw
    total = 0.0
    for raw in unique.values():
        size = float(raw["size"])
        total += size if str(raw.get("side")).lower() == "buy" else -size
    return total


def test_fallback_trade_identity_is_stable_without_provider_trade_id_pytest() -> None:
    trade_time = datetime(2026, 5, 10, 1, 2, 3, tzinfo=timezone.utc)

    first_id = sync_portfolio._portfolio_trade_id(
        account_id=ACCOUNT_ID,
        market_id=DET_MARKET_ID,
        outcome_id=DET_OUTCOME_ID,
        external_trade_id=None,
        tx_hash="0xdetbuy7",
        side="BUY",
        price="0.150000",
        size="7.000000",
        trade_time=trade_time,
    )
    second_id = sync_portfolio._portfolio_trade_id(
        account_id=ACCOUNT_ID,
        market_id=DET_MARKET_ID,
        outcome_id=DET_OUTCOME_ID,
        external_trade_id=None,
        tx_hash="0xdetbuy7",
        side="buy",
        price=0.15,
        size=7,
        trade_time=trade_time,
    )

    assert first_id == second_id


def test_may_9_duplicate_fill_fixture_collapses_to_unique_exposure_pytest() -> None:
    det_unique = [
        {
            "transactionHash": "0xdetbuy7",
            "side": "BUY",
            "price": "0.15",
            "size": "7",
            "timestamp": "2026-05-09T22:01:00Z",
        },
        {
            "transactionHash": "0xdetbuy10",
            "side": "BUY",
            "price": "0.08",
            "size": "10",
            "timestamp": "2026-05-09T22:02:00Z",
        },
        {
            "transactionHash": "0xdetsell17",
            "side": "SELL",
            "price": "0.14",
            "size": "17",
            "timestamp": "2026-05-09T22:03:00Z",
        },
    ]
    lal_unique = [
        {
            "transactionHash": f"0xlalbuy{index}",
            "side": "BUY",
            "price": "0.01",
            "size": "80",
            "timestamp": f"2026-05-10T00:{index:02d}:00Z",
        }
        for index in range(1, 15)
    ]
    lal_unique.extend(
        [
            {
                "transactionHash": "0xlalbuy15",
                "side": "BUY",
                "price": "0.001",
                "size": "159.991647",
                "timestamp": "2026-05-10T00:15:00Z",
            },
            {
                "transactionHash": "0xlalsell1",
                "side": "SELL",
                "price": "0.07",
                "size": "16.66",
                "timestamp": "2026-05-10T00:16:00Z",
            },
        ]
    )

    det_raw = det_unique * 3
    lal_raw = lal_unique * 3

    det_ids = {_trade_id(raw, market_id=DET_MARKET_ID, outcome_id=DET_OUTCOME_ID) for raw in det_raw}
    lal_ids = {_trade_id(raw, market_id=LAL_MARKET_ID, outcome_id=LAL_OUTCOME_ID) for raw in lal_raw}

    assert len(det_raw) == 9
    assert len(det_ids) == 3
    assert _net_size(det_raw, market_id=DET_MARKET_ID, outcome_id=DET_OUTCOME_ID) == 0.0
    assert len(lal_raw) == 48
    assert len(lal_ids) == 16
    assert round(_net_size(lal_raw, market_id=LAL_MARKET_ID, outcome_id=LAL_OUTCOME_ID), 6) == 1263.331647
