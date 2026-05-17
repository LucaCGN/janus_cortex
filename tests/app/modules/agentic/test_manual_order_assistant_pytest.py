from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.agentic.contracts import ManualClobOrderAssistantRequest
from app.modules.agentic.manual_order_assistant import build_manual_clob_order_assistant_review


NOW = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)


def _request(**overrides):
    payload = {
        "account_id": "account-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
        "token_id": "token-1",
        "side": "buy",
        "order_type": "limit",
        "limit_price": 0.001,
        "size": 100.0,
        "max_price": 0.001,
        "max_notional_usd": 0.10,
        "max_spread_cents": 3.0,
        "actor": "codex",
        "reason": "tail-risk low-price preview from realized-profit sleeve",
    }
    payload.update(overrides)
    return ManualClobOrderAssistantRequest(**payload)


def _outcome(**overrides):
    payload = {
        "event_id": "event-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
        "token_id": "token-1",
        "label": "Demo",
    }
    payload.update(overrides)
    return payload


def _book(**overrides):
    payload = {
        "event_id": "event-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
        "token_id": "token-1",
        "captured_at_utc": NOW.isoformat(),
        "best_bid": 0.001,
        "best_ask": 0.002,
        "spread_cents": 0.1,
        "bid_depth": 500,
        "ask_depth": 500,
    }
    payload.update(overrides)
    return payload


def test_manual_order_assistant_approves_low_price_preview_with_caps_pytest() -> None:
    review = build_manual_clob_order_assistant_review(
        _request(),
        event_id="event-1",
        matched_outcome=_outcome(),
        orderbook=_book(),
        inventory={"open_orders": [], "pending_intents": [], "unresolved_inventory_present": False},
        now_utc=NOW,
    )

    assert review["status"] == "preview_ready"
    assert review["approved"] is True
    assert review["order_payload"]["notional_usd"] == 0.1
    assert review["metadata"]["origin_actor"] == "codex_assisted"
    assert review["metadata"]["guardrails"]["max_notional_usd"] == 0.1


def test_manual_order_assistant_blocks_max_notional_stale_wrong_event_duplicate_pytest() -> None:
    review = build_manual_clob_order_assistant_review(
        _request(limit_price=0.02, max_price=0.03, max_notional_usd=1.0),
        event_id="event-1",
        matched_outcome=_outcome(event_id="event-2"),
        orderbook=_book(captured_at_utc="2026-05-17T17:59:00+00:00"),
        inventory={
            "open_orders": [{"outcome_id": "outcome-1", "side": "buy", "status": "submitted"}],
            "pending_intents": [],
            "unresolved_inventory_present": True,
        },
        now_utc=NOW,
    )

    reasons = {item["reason"] for item in review["blockers"]}
    assert review["status"] == "blocked"
    assert "wrong_event" in reasons
    assert "max_notional_exceeded" in reasons
    assert "stale_orderbook" in reasons
    assert "unresolved_current_event_inventory" in reasons
    assert "duplicate_open_order" in reasons


def test_manual_order_assistant_market_orders_disabled_except_sell_profit_exception_pytest() -> None:
    blocked = build_manual_clob_order_assistant_review(
        _request(order_type="market", limit_price=None, max_price=None, max_notional_usd=1.0),
        event_id="event-1",
        matched_outcome=_outcome(),
        orderbook=_book(),
        inventory={"open_orders": [], "pending_intents": [], "unresolved_inventory_present": False},
        now_utc=NOW,
    )
    assert {item["reason"] for item in blocked["blockers"]} == {"market_orders_disabled"}

    allowed = build_manual_clob_order_assistant_review(
        _request(
            side="sell",
            order_type="market",
            limit_price=None,
            max_price=None,
            max_notional_usd=1.0,
            allow_market_urgent_profit_capture=True,
            urgent_profit_capture_reason="profit spike likely to mean revert",
        ),
        event_id="event-1",
        matched_outcome=_outcome(),
        orderbook=_book(),
        inventory={"open_orders": [], "pending_intents": [], "unresolved_inventory_present": False},
        now_utc=NOW,
    )
    assert allowed["approved"] is True


def test_manual_order_assistant_rejects_buy_limit_above_max_price_at_schema_pytest() -> None:
    with pytest.raises(ValidationError):
        _request(limit_price=0.02, max_price=0.01)
