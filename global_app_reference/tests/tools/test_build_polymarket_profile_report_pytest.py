from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path


def load_profile_report_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "tools" / "build_polymarket_profile_report.py"
    spec = importlib.util.spec_from_file_location("build_polymarket_profile_report", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_summary(handle: str, *, win_rate: float = 60.0, all_pnl: float = 1000.0) -> dict:
    return {
        "profile": {
            "handle": handle,
            "input_url": f"https://polymarket.com/@{handle}",
            "wallet": "0x0000000000000000000000000000000000000000",
        },
        "specialty": "Crypto" if handle in {"pbot-6", "baloneigh"} else "Geopolitics",
        "closed_stats": {"win_rate": win_rate},
        "period_rows": [{"period": "ALL", "pnl": all_pnl}],
        "position_value": 123.0,
        "closed_capped": False,
        "trades_capped": True,
        "open": {"positions": 1, "capped": False},
    }


def test_latest_position_and_trade_rows_are_compact_and_source_tagged():
    module = load_profile_report_module()

    positions = [
        {
            "title": "Bitcoin Up or Down - May 28, 1PM ET",
            "marketSlug": "btc-updown-5m-1780000000",
            "outcome": "Up",
            "size": "5",
            "avgPrice": "0.44",
            "curPrice": "0.61",
            "currentValue": "3.05",
            "cashPnl": "0.85",
            "asset": "token-a",
        },
        {
            "title": "Tiny row",
            "marketSlug": "tiny-row",
            "outcome": "No",
            "size": "1",
            "currentValue": "0.01",
        },
    ]
    trades = [
        {
            "title": "Will no Fed rate cuts happen in 2026?",
            "eventSlug": "will-no-fed-rate-cuts-happen-in-2026",
            "outcome": "Yes",
            "side": "BUY",
            "size": "5",
            "price": "0.61",
            "timestamp": "1780000000",
            "asset": "token-b",
        }
    ]

    latest_positions = module.latest_position_rows(positions, limit=1)
    latest_trades = module.latest_trade_rows(trades, limit=1)

    assert latest_positions[0]["title"] == "Bitcoin Up or Down - May 28, 1PM ET"
    assert latest_positions[0]["source_confidence"] == [
        "profile_observed",
        "api_limited",
        "unresolved_open_pnl",
    ]
    assert latest_trades[0]["trade_side"] == "BUY"
    assert latest_trades[0]["observed_time_utc"].endswith("UTC")
    assert "profile_observed" in latest_trades[0]["source_confidence"]


def test_profile_hypotheses_preserve_not_live_authorized_status_and_caveats():
    module = load_profile_report_module()
    summaries = [
        sample_summary("aenews2", win_rate=57.0, all_pnl=1_900_000.0),
        sample_summary("classified", win_rate=60.0, all_pnl=611_000.0),
        sample_summary("pbot-6", win_rate=49.8, all_pnl=112_000.0),
        sample_summary("baloneigh", win_rate=49.1, all_pnl=82_000.0),
        sample_summary("ImJustKen", win_rate=62.0, all_pnl=3_100_000.0),
    ]

    payload = module.derive_benchmark_hypotheses(
        summaries,
        datetime(2026, 5, 28, 23, 59, tzinfo=UTC),
    )

    assert payload["schema_version"] == "profile_benchmark_hypotheses_v1"
    assert len(payload["hypotheses"]) == 5
    for hypothesis in payload["hypotheses"]:
        assert hypothesis["status"] == "not_live_authorized"
        assert hypothesis["promotion_path"] == ["idea", "research", "shadow", "min-size test"]
        assert hypothesis["data_requirements"]
        assert hypothesis["backtest_requirements"]

    rendered = module.render_hypotheses_report(payload)
    assert "do not authorize live trading" in rendered
    assert "PROFILE-HYP-CRYPTO-UPDOWN-MICROSTRUCTURE" in rendered
