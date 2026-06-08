from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_options_app.trading.live_candidates import scenario_from_verified_candidate, verify_live_market_candidate


def test_live_candidate_verifier_accepts_fresh_executable_quote_pytest() -> None:
    now = datetime(2026, 6, 3, 2, 45, tzinfo=UTC)

    result = verify_live_market_candidate(
        {
            "event_id": "event-1",
            "event_slug": "btc-updown-5m-1770000000",
            "token_id": "token-up",
            "outcome": "Up",
            "best_bid": 0.49,
            "best_ask": 0.51,
            "ask_size": 20,
            "depth_top3_ask_size": 50,
            "observed_at_utc": (now - timedelta(seconds=2)).isoformat(),
            "source": "polymarket_current_order_book",
        },
        now_utc=now,
    )

    assert result.verified is True
    assert result.blockers == ()
    assert result.candidate is not None
    scenario = scenario_from_verified_candidate(result.candidate, shares=1.0)
    assert scenario.event_key == "event-1"
    assert scenario.event_token_key == "token-up"
    assert scenario.limit_price == 0.51
    assert scenario.live_market_verified is True


def test_live_candidate_verifier_blocks_stale_or_wide_quotes_pytest() -> None:
    now = datetime(2026, 6, 3, 2, 45, tzinfo=UTC)

    stale = verify_live_market_candidate(
        {
            "event_id": "event-1",
            "token_id": "token-up",
            "best_bid": 0.40,
            "best_ask": 0.60,
            "ask_size": 20,
            "depth_top3_ask_size": 50,
            "observed_at_utc": (now - timedelta(seconds=20)).isoformat(),
        },
        now_utc=now,
    )

    assert stale.verified is False
    assert "stale_quote" in stale.blockers
    assert "spread_too_wide" in stale.blockers


def test_live_candidate_verifier_blocks_missing_identity_or_liquidity_pytest() -> None:
    now = datetime(2026, 6, 3, 2, 45, tzinfo=UTC)

    missing = verify_live_market_candidate(
        {
            "best_ask": 0.5,
            "ask_size": 0.5,
            "depth_top3_ask_size": 0.5,
            "observed_at_utc": now.isoformat(),
        },
        now_utc=now,
    )

    assert missing.verified is False
    assert "missing_token_id" in missing.blockers
    assert "missing_event_key" in missing.blockers
    assert "insufficient_ask_size" in missing.blockers
    assert "insufficient_depth_top3_ask_size" in missing.blockers
