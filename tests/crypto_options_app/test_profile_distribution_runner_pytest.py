from __future__ import annotations

import argparse
import sqlite3
from types import SimpleNamespace

from crypto_options_app.scripts import run_crypto_options_profile_distribution_service as runner


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        db_path=None,
        active_profile_pool=None,
        symbols=["BTC", "ETH"],
        allowed_grades=["S++", "S+", "S"],
        max_profiles=120,
        lookback_minutes=5,
        lookahead_minutes=15,
        target_refresh_seconds=30,
        max_source_age_seconds=90,
        canonical_method="cost_weighted",
        include_external_fetch=False,
        external_activity_limit=100,
        external_position_limit=100,
        max_concurrency=8,
        timeout_seconds=5.0,
        loop=False,
        interval_seconds=30.0,
        state_path=None,
        json=True,
    )


def _summary(status: str = "healthy"):
    return SimpleNamespace(
        generated_at_utc="2026-06-06T00:00:00+00:00",
        status=status,
        db_path="crypto_options_app/data/crypto_options_data.sqlite",
        event_count=1,
        snapshot_rows_inserted=2,
        component_rows_inserted=3,
        readiness_rows_inserted=1,
        blockers=(),
        distributions=(),
        state={},
        orders_allowed=False,
        live_trading_authorized=False,
    )


def test_profile_distribution_runner_retries_sqlite_lock_pytest(monkeypatch) -> None:
    calls = {"count": 0}

    def flaky_capture(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return _summary()

    monkeypatch.setattr(runner, "capture_top_profile_distributions_once", flaky_capture)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    payload = runner.run_once(_args())

    assert calls["count"] == 3
    assert payload["status"] == "healthy"
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False


def test_profile_distribution_runner_does_not_retry_non_lock_operational_error_pytest(monkeypatch) -> None:
    calls = {"count": 0}

    def broken_capture(*_args, **_kwargs):
        calls["count"] += 1
        raise sqlite3.OperationalError("no such table: profile_distribution_snapshots")

    monkeypatch.setattr(runner, "capture_top_profile_distributions_once", broken_capture)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    try:
        runner.run_once(_args())
    except sqlite3.OperationalError as exc:
        assert "no such table" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected non-lock OperationalError to propagate")

    assert calls["count"] == 1
