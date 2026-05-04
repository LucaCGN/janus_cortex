from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.modules.nba.execution import adapter
from app.data.nodes.polymarket.blockchain.stream_orderbook import OrderbookSnapshot


def test_resolve_entry_order_size_uses_minimum_shares_even_above_old_one_dollar_target_pytest() -> None:
    result = adapter.resolve_entry_order_size(
        0.81,
        min_order_size=5.0,
        target_notional_usd=1.0,
    )

    assert result["size"] == 5.0
    assert result["blocked_reason"] is None
    assert result["target_notional_usd"] == 1.0
    assert result["required_notional_usd"] == 4.05
    assert result["min_order_size"] == 5.0
    assert result["sizing_mode"] == "minimum_shares"


def test_resolve_entry_order_size_uses_five_share_floor_when_market_reports_lower_minimum_pytest() -> None:
    result = adapter.resolve_entry_order_size(
        0.4,
        min_order_size=1.0,
        target_notional_usd=1.0,
    )

    assert result["blocked_reason"] is None
    assert result["size"] == 5.0
    assert result["required_notional_usd"] == 2.0
    assert result["target_notional_usd"] == 1.0
    assert result["sizing_mode"] == "minimum_shares"


def test_mirror_account_state_forwards_live_account_identity_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_mirror_sync(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="success", rows_written=3)

    monkeypatch.setattr(adapter, "run_portfolio_mirror_sync", _fake_mirror_sync)

    result = adapter.mirror_account_state(
        object(),
        account={
            "account_id": "acct-live",
            "account_label": "Polymarket Live",
            "wallet_address": "0xwallet",
            "proxy_wallet_address": "0xproxy",
            "chain_id": 137,
        },
    )

    assert captured == {
        "wallet_address": "0xwallet",
        "account_id": "acct-live",
        "account_label": "Polymarket Live",
        "proxy_wallet_address": "0xproxy",
        "chain_id": 137,
    }
    assert result["status"] == "success"
    assert result["rows_written"] == 3


def test_create_live_order_dry_run_sanitizes_metadata_and_persists_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_calls: list[tuple[str, dict[str, object]]] = []

    class _FakeRepo:
        def __init__(self, connection: object) -> None:
            self.connection = connection

        def upsert_order(self, **kwargs) -> str:
            repo_calls.append(("upsert_order", kwargs))
            return str(kwargs["order_id"])

        def insert_order_event(self, **kwargs) -> bool:
            repo_calls.append(("insert_order_event", kwargs))
            return True

    monkeypatch.setattr(adapter, "JanusUpsertRepository", _FakeRepo)

    result = adapter.create_live_order(
        object(),
        account={"account_id": "acct-live"},
        market_id="market-demo",
        outcome_id="outcome-demo",
        token_id="token-demo",
        side="buy",
        size=2.5,
        price=0.4,
        order_type="limit",
        metadata_json={
            "run_id": "live-2026-04-26-v1-dryrun",
            "signal_timestamp": pd.Timestamp("2026-04-26T10:00:00Z"),
        },
        dry_run=True,
    )

    order_upserts = [payload for name, payload in repo_calls if name == "upsert_order"]
    events = [payload for name, payload in repo_calls if name == "insert_order_event"]
    assert len(order_upserts) == 2
    assert len(events) == 1
    assert order_upserts[0]["status"] == "open"
    assert order_upserts[0]["metadata_json"]["signal_timestamp"] == "2026-04-26T10:00:00+00:00"
    assert order_upserts[0]["metadata_json"]["execution"]["dry_run"] is True
    assert events[0]["event_type"] == "live_place_dry_run"
    assert result["status"] == "open"
    assert result["metadata_json"]["signal_timestamp"] == "2026-04-26T10:00:00+00:00"


def test_create_live_order_persists_initial_row_before_submit_update_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_calls: list[tuple[str, dict[str, object]]] = []

    class _FakeRepo:
        def __init__(self, connection: object) -> None:
            self.connection = connection
            self._upsert_count = 0

        def upsert_order(self, **kwargs) -> str:
            self._upsert_count += 1
            repo_calls.append(("upsert_order", kwargs))
            if self._upsert_count == 2:
                raise RuntimeError("post-submit persistence failed")
            return str(kwargs["order_id"])

        def insert_order_event(self, **kwargs) -> bool:
            repo_calls.append(("insert_order_event", kwargs))
            return True

    monkeypatch.setattr(adapter, "JanusUpsertRepository", _FakeRepo)
    monkeypatch.setattr(
        adapter,
        "place_new_order",
        lambda creds, request: SimpleNamespace(success=True, raw={"id": "ext-order-1"}),
    )
    monkeypatch.setattr(adapter, "build_live_creds", lambda account: SimpleNamespace())

    with pytest.raises(RuntimeError, match="post-submit persistence failed"):
        adapter.create_live_order(
            object(),
            account={"account_id": "acct-live"},
            market_id="market-demo",
            outcome_id="outcome-demo",
            token_id="token-demo",
            side="buy",
            size=2.5,
            price=0.4,
            order_type="limit",
            metadata_json={"run_id": "live-2026-04-26-v1-live"},
            dry_run=False,
        )

    order_upserts = [payload for name, payload in repo_calls if name == "upsert_order"]
    events = [payload for name, payload in repo_calls if name == "insert_order_event"]
    assert len(order_upserts) == 2
    assert order_upserts[0]["status"] == "pending_submit"
    assert events[0]["event_type"] == "live_place_requested"


def test_create_live_order_blocks_live_buy_when_clob_collateral_not_ready_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_calls: list[tuple[str, dict[str, object]]] = []
    place_calls: list[object] = []

    class _FakeRepo:
        def __init__(self, connection: object) -> None:
            self.connection = connection

        def upsert_order(self, **kwargs) -> str:
            repo_calls.append(("upsert_order", kwargs))
            return str(kwargs["order_id"])

        def insert_order_event(self, **kwargs) -> bool:
            repo_calls.append(("insert_order_event", kwargs))
            return True

    monkeypatch.setattr(adapter, "JanusUpsertRepository", _FakeRepo)
    monkeypatch.setattr(adapter, "build_live_creds", lambda account: SimpleNamespace())
    monkeypatch.setattr(
        adapter,
        "fetch_clob_collateral_status",
        lambda creds, required_notional_usd: {
            "ready": False,
            "reason": "clob_collateral_balance_too_low",
            "required_notional_usd": required_notional_usd,
            "balance_usd": 0.0,
            "max_allowance_usd": 0.0,
        },
    )
    monkeypatch.setattr(adapter, "place_new_order", lambda creds, request: place_calls.append(request))

    result = adapter.create_live_order(
        object(),
        account={"account_id": "acct-live"},
        market_id="market-demo",
        outcome_id="outcome-demo",
        token_id="token-demo",
        side="buy",
        size=5.0,
        price=0.5,
        order_type="limit",
        metadata_json={"run_id": "live-2026-04-29-v1-live"},
        dry_run=False,
    )

    order_upserts = [payload for name, payload in repo_calls if name == "upsert_order"]
    events = [payload for name, payload in repo_calls if name == "insert_order_event"]
    assert place_calls == []
    assert result["status"] == "submit_error"
    assert result["event_type"] == "live_place_blocked"
    assert result["metadata_json"]["execution"]["blocked_reason"] == "clob_collateral_unavailable"
    assert result["metadata_json"]["execution"]["clob_collateral"]["reason"] == "clob_collateral_balance_too_low"
    assert order_upserts[0]["status"] == "pending_submit"
    assert order_upserts[1]["status"] == "submit_error"
    assert [event["event_type"] for event in events] == ["live_place_requested", "live_place_blocked"]


def test_fetch_latest_orderbook_summary_caches_missing_orderbook_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_fetch_orderbook(*, creds, token_id: str, market_id: str):
        calls.append((market_id, token_id))
        return OrderbookSnapshot(
            market_id=market_id,
            token_id=token_id,
            bids=[],
            asks=[],
            min_order_size=None,
            tick_size=None,
        )

    adapter._MISSING_ORDERBOOK_CACHE.clear()
    monkeypatch.setattr(adapter, "fetch_orderbook", _fake_fetch_orderbook)

    first = adapter.fetch_latest_orderbook_summary(
        creds=SimpleNamespace(),
        market_id="market-missing",
        token_id="token-missing",
    )
    second = adapter.fetch_latest_orderbook_summary(
        creds=SimpleNamespace(),
        market_id="market-missing",
        token_id="token-missing",
    )

    assert calls == [("market-missing", "token-missing")]
    assert first["quote_status"] == "live_orderbook"
    assert second["quote_status"] == "missing_orderbook_cached"
    assert second["best_bid"] is None
    assert second["best_ask"] is None
