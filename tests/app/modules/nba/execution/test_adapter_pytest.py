from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.modules.nba.execution import adapter
from app.data.nodes.polymarket.blockchain.stream_orderbook import OrderbookSnapshot


class _FakeCursor:
    def __init__(self, connection: "_FakeCommitConnection") -> None:
        self.connection = connection
        self._row = None
        self._rows: list[dict[str, object]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.connection.queries.append((query, params))
        if "FROM portfolio.orders o" in query:
            self._rows = self.connection.order_rows
            self._row = None
        elif "FROM portfolio.orders" in query:
            self._row = self.connection.order_row
            self._rows = []
        elif "FROM portfolio.trades" in query:
            self._row = {"filled_size": self.connection.filled_size}
            self._rows = []
        else:
            self._row = None
            self._rows = []

    def fetchone(self) -> dict[str, object] | None:
        return self._row

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _FakeCommitConnection:
    def __init__(
        self,
        *,
        order_row: dict[str, object] | None = None,
        order_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.commit_count = 0
        self.queries: list[tuple[str, object]] = []
        self.order_row = order_row
        self.order_rows = order_rows or []
        self.filled_size = 0.0

    def cursor(self, *args, **kwargs) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1


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
    connection = _FakeCommitConnection()

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
    monkeypatch.setattr(adapter, "fetch_clob_collateral_status", lambda creds, required_notional_usd: {"ready": True})
    monkeypatch.setattr(
        adapter,
        "place_new_order",
        lambda creds, request: SimpleNamespace(success=True, raw={"id": "ext-order-1"}),
    )
    monkeypatch.setattr(adapter, "build_live_creds", lambda account: SimpleNamespace())

    with pytest.raises(RuntimeError, match="post-submit persistence failed"):
        adapter.create_live_order(
            connection,
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
    assert connection.commit_count == 1


def test_create_live_order_commits_submitted_live_order_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_calls: list[tuple[str, dict[str, object]]] = []
    connection = _FakeCommitConnection()

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
    monkeypatch.setattr(adapter, "fetch_clob_collateral_status", lambda creds, required_notional_usd: {"ready": True})
    monkeypatch.setattr(
        adapter,
        "place_new_order",
        lambda creds, request: SimpleNamespace(success=True, raw={"orderID": "ext-order-1"}),
    )

    result = adapter.create_live_order(
        connection,
        account={"account_id": "acct-live"},
        market_id="market-demo",
        outcome_id="outcome-demo",
        token_id="token-demo",
        side="buy",
        size=5.0,
        price=0.27,
        order_type="limit",
        metadata_json={"run_id": "live-2026-05-05-v1-live"},
        dry_run=False,
    )

    order_upserts = [payload for name, payload in repo_calls if name == "upsert_order"]
    events = [payload for name, payload in repo_calls if name == "insert_order_event"]
    assert result["status"] == "submitted"
    assert result["external_order_id"] == "ext-order-1"
    assert [order["status"] for order in order_upserts] == ["pending_submit", "submitted"]
    assert [event["event_type"] for event in events] == ["live_place_requested", "live_place_submitted"]
    assert connection.commit_count == 2


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


def test_cancel_live_order_commits_cancel_status_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_calls: list[tuple[str, dict[str, object]]] = []
    connection = _FakeCommitConnection(
        order_row={
            "order_id": "order-1",
            "external_order_id": "ext-order-1",
            "status": "submitted",
            "metadata_json": {},
        }
    )

    class _FakeRepo:
        def __init__(self, connection: object) -> None:
            self.connection = connection

        def insert_order_event(self, **kwargs) -> bool:
            repo_calls.append(("insert_order_event", kwargs))
            return True

    monkeypatch.setattr(adapter, "JanusUpsertRepository", _FakeRepo)
    monkeypatch.setattr(adapter, "build_live_creds", lambda account: SimpleNamespace())
    monkeypatch.setattr(
        adapter,
        "cancel_order",
        lambda creds, external_order_id: SimpleNamespace(success=True, raw={"canceled": external_order_id}),
    )

    result = adapter.cancel_live_order(
        connection,
        account={"account_id": "acct-live"},
        order_id="order-1",
        dry_run=False,
        reason="stale_signal",
    )

    assert result["status"] == "canceled"
    assert result["event_type"] == "live_cancel_submitted"
    assert repo_calls[0][1]["event_type"] == "live_cancel_submitted"
    assert connection.commit_count == 1


def test_reconcile_live_order_fills_links_clob_trade_to_run_order_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_calls: list[tuple[str, dict[str, object]]] = []
    connection = _FakeCommitConnection(
        order_rows=[
            {
                "order_id": "order-1",
                "account_id": "acct-live",
                "market_id": "market-demo",
                "outcome_id": "outcome-demo",
                "external_order_id": "0xabc",
                "side": "buy",
                "size": 5.0,
                "placed_at": datetime(2026, 5, 5, 23, 57, tzinfo=timezone.utc),
                "status": "submitted",
            }
        ]
    )

    class _FakeRepo:
        def __init__(self, connection: _FakeCommitConnection) -> None:
            self.connection = connection

        def upsert_trade(self, **kwargs) -> str:
            repo_calls.append(("upsert_trade", kwargs))
            self.connection.filled_size += float(kwargs["size"])
            return str(kwargs["trade_id"])

        def insert_order_event(self, **kwargs) -> bool:
            repo_calls.append(("insert_order_event", kwargs))
            return True

    monkeypatch.setattr(adapter, "JanusUpsertRepository", _FakeRepo)
    monkeypatch.setattr(adapter, "build_live_creds", lambda account: SimpleNamespace())
    monkeypatch.setattr(
        adapter,
        "view_trades",
        lambda creds: [
            SimpleNamespace(
                id="trade-1",
                taker_order_id="0xabc",
                maker_order_id="",
                side="BUY",
                asset_id="token-demo",
                size=5.0,
                price=0.27,
                timestamp=0,
            )
        ],
    )

    result = adapter.reconcile_live_order_fills(
        connection,
        account={"account_id": "acct-live"},
        run_id="live-2026-05-05-v1-live",
    )

    assert result == {"checked_orders": 1, "matched_trades": 1, "orders_filled": 1}
    assert repo_calls[0][0] == "upsert_trade"
    assert repo_calls[0][1]["order_id"] == "order-1"
    assert repo_calls[1][0] == "insert_order_event"
    assert str(repo_calls[1][1]["event_type"]).startswith("live_trade_fill:")
    update_queries = [query for query, _params in connection.queries if "UPDATE portfolio.orders" in query]
    assert update_queries
    assert connection.commit_count == 1


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
