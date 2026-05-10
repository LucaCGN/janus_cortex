from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import uuid

from app.modules.agentic.contracts import ReplayFromWatchSessionRequest
from app.modules.agentic import repository


def test_readable_watch_session_keys_map_to_stable_uuid_pytest() -> None:
    key = "watch-nba-nyk-phi-2026-05-10"

    first = repository._coerce_uuid(key, namespace="watch-session")  # noqa: SLF001
    second = repository._coerce_uuid(key, namespace="watch-session")  # noqa: SLF001

    assert first == second
    assert str(uuid.UUID(first)) == first


def test_uuid_watch_session_ids_are_preserved_pytest() -> None:
    value = str(uuid.uuid4())

    assert repository._coerce_uuid(value, namespace="watch-session") == value  # noqa: SLF001


def test_build_replay_source_summary_counts_ticks_trades_and_decisions_pytest() -> None:
    base = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
    summary = repository._build_replay_source_summary(  # noqa: SLF001
        watch_session={
            "watch_session_id": "watch-uuid",
            "event_key": "nba-event",
            "category": "nba",
            "started_at": base,
            "ended_at": None,
            "cadence_ms": 3000,
            "passive_only": True,
            "reason": "pytest",
        },
        tick_rows=[
            {
                "event_key": "nba-event",
                "market_id": "market-1",
                "outcome_id": "out-away",
                "token_id": "token-away",
                "captured_at": base,
                "source_latency_ms": 100,
                "ingest_latency_ms": 20,
            },
            {
                "event_key": "nba-event",
                "market_id": "market-1",
                "outcome_id": "out-away",
                "token_id": "token-away",
                "captured_at": base + timedelta(seconds=3),
                "source_latency_ms": 200,
                "ingest_latency_ms": 30,
            },
        ],
        trade_rows=[
            {
                "event_key": "nba-event",
                "market_id": "market-1",
                "outcome_id": "out-away",
                "token_id": "token-away",
                "trade_time": base + timedelta(seconds=2),
                "source_latency_ms": 150,
            }
        ],
        decision_rows=[{"decision_type": "blocker", "strategy_id": "guardrail-1"}],
    )

    assert summary["source_tick_count"] == 2
    assert summary["source_trade_count"] == 1
    assert summary["event_keys"] == ["nba-event"]
    assert summary["tick_cadence"]["avg_interval_seconds"] == 3.0
    assert summary["tick_cadence"]["gap_over_cadence_count"] == 0
    assert summary["orderbook_source_latency_ms"] == {"avg": 150.0, "max": 200.0}
    assert summary["controller_decision_comparison"]["status"] == "decisions_available"
    assert summary["controller_decision_comparison"]["decision_types"] == {"blocker": 1}


def test_try_persist_replay_request_materializes_watch_session_sources_pytest(monkeypatch) -> None:
    base = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
    watch_session_key = "watch-nba-event"
    watch_session_uuid = repository._coerce_uuid(watch_session_key, namespace="watch-session")  # noqa: SLF001

    class FakeCursor:
        def __init__(self) -> None:
            self.query_index = 0
            self.insert_params = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params=None) -> None:
            if "INSERT INTO agentic.replay_sessions" in query:
                self.insert_params = params
            else:
                self.query_index += 1

        def fetchone(self):
            return {
                "watch_session_id": watch_session_uuid,
                "event_key": "nba-event",
                "category": "nba",
                "started_at": base,
                "ended_at": base + timedelta(minutes=1),
                "cadence_ms": 3000,
                "passive_only": True,
                "reason": "pytest",
                "metadata_json": {"watch_session_key": watch_session_key},
            }

        def fetchall(self):
            if self.query_index == 2:
                return [
                    {
                        "market_orderbook_tick_id": "tick-1",
                        "event_key": "nba-event",
                        "market_id": "market-1",
                        "outcome_id": "out-away",
                        "token_id": "token-away",
                        "captured_at": base,
                        "source_timestamp": base,
                        "best_bid": 0.28,
                        "best_ask": 0.3,
                        "source_latency_ms": 25,
                        "ingest_latency_ms": 10,
                        "raw_json": {"watch_session_key": watch_session_key},
                    },
                    {
                        "market_orderbook_tick_id": "tick-2",
                        "event_key": "nba-event",
                        "market_id": "market-1",
                        "outcome_id": "out-home",
                        "token_id": "token-home",
                        "captured_at": base + timedelta(seconds=3),
                        "source_timestamp": base + timedelta(seconds=3),
                        "best_bid": 0.7,
                        "best_ask": 0.72,
                        "source_latency_ms": 35,
                        "ingest_latency_ms": 12,
                        "raw_json": {"watch_session_key": watch_session_key},
                    },
                ]
            if self.query_index == 3:
                return [
                    {
                        "market_trade_id": "trade-1",
                        "event_key": "nba-event",
                        "market_id": "market-1",
                        "outcome_id": "out-away",
                        "token_id": "token-away",
                        "trade_time": base + timedelta(seconds=4),
                        "observed_at": base + timedelta(seconds=5),
                        "price": 0.29,
                        "size": 5,
                        "source_latency_ms": 100,
                        "raw_json": {"watch_session_key": watch_session_key},
                    }
                ]
            if self.query_index == 4:
                return [{"strategy_decision_id": "decision-1", "event_key": "nba-event", "decision_type": "blocker"}]
            return []

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_obj = FakeCursor()
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self, *_, **__):
            return self.cursor_obj

        def commit(self) -> None:
            self.committed = True

    fake_connection = FakeConnection()

    @contextmanager
    def fake_managed_connection():
        yield fake_connection

    monkeypatch.setattr(repository, "managed_connection", fake_managed_connection)

    result = repository.try_persist_replay_request(
        ReplayFromWatchSessionRequest(watch_session_id=watch_session_key, notes="pytest"),
        output_root="local/replay.json",
    )

    assert result["ok"] is True
    assert result["watch_session_id"] == watch_session_uuid
    assert result["source_tick_count"] == 2
    assert result["source_trade_count"] == 1
    assert result["latency_summary"]["controller_decision_comparison"]["decision_count"] == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_obj.insert_params[4:6] == (2, 1)
