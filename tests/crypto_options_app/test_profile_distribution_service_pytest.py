from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crypto_options_app.data_services.profile_distribution_service import (
    ProfileDistributionConfig,
    capture_top_profile_distributions_once,
)
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema


def test_top_profile_distribution_uses_grade_and_style_weighted_activity_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.sqlite"
    now = datetime(2026, 6, 4, 10, 2, 0, tzinfo=UTC)
    initialize_schema(db_path)
    with connect(db_path) as conn:
        _insert_event(conn, now=now)
        _insert_profile(conn, "profile-up", grade="S++", style="outcome_predictor")
        _insert_profile(conn, "profile-down", grade="S", style="hedger")
        _insert_profile(conn, "profile-ignored", grade="B", style="outcome_predictor")
        _insert_raw_activity(conn, "profile-up", "Up", price=0.4, shares=10, now=now)
        _insert_raw_activity(conn, "profile-down", "Down", price=0.6, shares=10, now=now)
        _insert_raw_activity(conn, "profile-ignored", "Down", price=0.9, shares=100, now=now)

    summary = capture_top_profile_distributions_once(
        config=ProfileDistributionConfig(
            db_path=db_path,
            active_profile_pool_path=tmp_path / "missing_pool.txt",
            symbols=("BTC",),
            allowed_grades=("S++", "S+", "S"),
        ),
        now_utc=now,
    )

    assert summary.status == "healthy"
    assert summary.snapshot_rows_inserted == 1
    assert summary.component_rows_inserted == 2
    distribution = summary.distributions[0]["distribution"]
    assert distribution["up"] == pytest.approx(7 / 13)
    assert distribution["down"] == pytest.approx(6 / 13)
    reconstructed = summary.distributions[0]["reconstructed_profile_prices"]
    assert reconstructed["up"] == pytest.approx(0.4)
    assert reconstructed["down"] == pytest.approx(0.6)
    assert summary.distributions[0]["pressure_delta"] == pytest.approx((7 / 13) - (6 / 13))
    with connect(db_path) as conn:
        readiness = conn.execute(
            "SELECT data_block, module_id, status FROM data_signal_readiness_snapshots"
        ).fetchone()
        assert dict(readiness) == {
            "data_block": "B",
            "module_id": "top_profiles_distribution",
            "status": "ready",
        }


def test_top_profile_distribution_falls_back_to_position_snapshots_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.sqlite"
    now = datetime(2026, 6, 4, 10, 2, 0, tzinfo=UTC)
    initialize_schema(db_path)
    with connect(db_path) as conn:
        _insert_event(conn, now=now)
        _insert_profile(conn, "profile-up", grade="S+", style="hedger")
        _insert_profile(conn, "profile-down", grade="S", style="hedger")
        _insert_position(conn, "profile-up", "Up", shares=5, cost=2.5, now=now)
        _insert_position(conn, "profile-down", "Down", shares=5, cost=1.25, now=now)

    summary = capture_top_profile_distributions_once(
        config=ProfileDistributionConfig(
            db_path=db_path,
            active_profile_pool_path=tmp_path / "missing_pool.txt",
            symbols=("BTC",),
        ),
        now_utc=now,
    )

    assert summary.status == "healthy"
    assert summary.distributions[0]["source_mode"] == "position_snapshot_fallback"
    assert summary.distributions[0]["distribution"]["up"] == pytest.approx(3 / 4.25)
    reconstructed = summary.distributions[0]["reconstructed_profile_prices"]
    assert reconstructed["up"] == pytest.approx(0.5)
    assert reconstructed["down"] == pytest.approx(0.25)
    assert reconstructed["pair_sum"] == pytest.approx(0.75)
    assert summary.component_rows_inserted == 2


def test_top_profile_distribution_external_fetchers_persist_rows_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.sqlite"
    now = datetime(2026, 6, 4, 10, 2, 0, tzinfo=UTC)
    initialize_schema(db_path)
    with connect(db_path) as conn:
        _insert_event(conn, now=now)
        _insert_profile(conn, "profile-up", wallet="0xabc", grade="S++", style="outcome_predictor")

    def activity_fetcher(wallet: str, limit: int, timeout: float):
        assert wallet == "0xabc"
        return [
            {
                "eventSlug": "btc-updown-5m-test",
                "conditionId": "condition-1",
                "outcome": "Up",
                "side": "BUY",
                "price": 0.5,
                "size": 2,
                "timestamp": int(now.timestamp()),
            }
        ]

    def position_fetcher(wallet: str, limit: int, timeout: float):
        return []

    summary = capture_top_profile_distributions_once(
        config=ProfileDistributionConfig(
            db_path=db_path,
            active_profile_pool_path=tmp_path / "missing_pool.txt",
            symbols=("BTC",),
            include_external_fetch=True,
        ),
        now_utc=now,
        activity_fetcher=activity_fetcher,
        position_fetcher=position_fetcher,
    )

    assert summary.status == "healthy"
    assert summary.component_rows_inserted == 1
    assert summary.distributions[0]["distribution"] == {"up": 1.0, "down": 0.0}
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile_raw_activity").fetchone()[0] == 1


def test_future_empty_pre_events_do_not_degrade_current_profile_distribution_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.sqlite"
    now = datetime(2026, 6, 4, 10, 2, 0, tzinfo=UTC)
    initialize_schema(db_path)
    with connect(db_path) as conn:
        _insert_event(conn, now=now)
        _insert_event(
            conn,
            now=now,
            event_key="future-event",
            event_slug="btc-updown-5m-future",
            condition_id="future-condition",
            start_delta=timedelta(minutes=8),
            end_delta=timedelta(minutes=13),
        )
        _insert_profile(conn, "profile-up", grade="S++", style="outcome_predictor")
        _insert_raw_activity(conn, "profile-up", "Up", price=0.4, shares=10, now=now)

    summary = capture_top_profile_distributions_once(
        config=ProfileDistributionConfig(
            db_path=db_path,
            active_profile_pool_path=tmp_path / "missing_pool.txt",
            symbols=("BTC",),
            lookahead_minutes=15,
        ),
        now_utc=now,
    )

    assert summary.status == "healthy"
    assert summary.event_count == 2
    assert [row["component_count"] for row in summary.distributions] == [1, 0]
    assert summary.distributions[1]["blockers"] == []
    assert summary.distributions[1]["coverage_warnings"] == [
        "no_profile_distribution_components",
        "no_up_down_distribution_weight",
    ]
    with connect(db_path) as conn:
        readiness = conn.execute(
            """
            SELECT status, blockers_json, payload_json
            FROM data_signal_readiness_snapshots
            WHERE data_block='B' AND module_id='top_profiles_distribution'
            """
        ).fetchone()
        assert readiness["status"] == "ready"
        assert readiness["blockers_json"] == "[]"
        assert "coverage_warnings" in readiness["payload_json"]


def test_actionable_live_event_without_profiles_degrades_profile_distribution_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.sqlite"
    now = datetime(2026, 6, 4, 10, 2, 0, tzinfo=UTC)
    initialize_schema(db_path)
    with connect(db_path) as conn:
        _insert_event(conn, now=now)
        _insert_profile(conn, "profile-up", grade="S++", style="outcome_predictor")

    summary = capture_top_profile_distributions_once(
        config=ProfileDistributionConfig(
            db_path=db_path,
            active_profile_pool_path=tmp_path / "missing_pool.txt",
            symbols=("BTC",),
        ),
        now_utc=now,
    )

    assert summary.status == "degraded"
    assert summary.blockers == ("no_profile_distribution_components", "no_up_down_distribution_weight")
    with connect(db_path) as conn:
        readiness = conn.execute(
            "SELECT status, blockers_json FROM data_signal_readiness_snapshots WHERE data_block='B'"
        ).fetchone()
        assert readiness["status"] == "degraded"
        assert "no_ready_profile_distribution" in readiness["blockers_json"]


def test_top_profile_distribution_rejects_live_flags_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE", "1")
    with pytest.raises(RuntimeError, match="data_service_live_flags_rejected"):
        capture_top_profile_distributions_once(
            config=ProfileDistributionConfig(db_path=tmp_path / "crypto.sqlite"),
            now_utc=datetime(2026, 6, 4, 10, 2, 0, tzinfo=UTC),
        )


def _insert_event(
    conn,
    *,
    now: datetime,
    event_key: str = "event-1",
    event_slug: str = "btc-updown-5m-test",
    condition_id: str = "condition-1",
    start_delta: timedelta = -timedelta(minutes=1),
    end_delta: timedelta = timedelta(minutes=4),
) -> None:
    conn.execute(
        """
        INSERT INTO events(
            event_key, event_slug, condition_id, market_slug, symbol, cadence_seconds,
            event_start_time_utc, event_end_time_utc, source_table,
            source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            event_key,
            event_slug,
            condition_id,
            event_slug,
            "BTC",
            300,
            (now + start_delta).isoformat(),
            (now + end_delta).isoformat(),
            "pytest",
            now.isoformat(),
            now.isoformat(),
        ),
    )


def _insert_profile(conn, profile_key: str, *, wallet: str | None = None, grade: str, style: str) -> None:
    now = datetime(2026, 6, 4, 10, 0, 0, tzinfo=UTC).isoformat()
    conn.execute(
        """
        INSERT INTO profiles(
            profile_key, normalized_ref, handle, proxy_wallet, profile_name,
            source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (profile_key, profile_key, profile_key, wallet, profile_key, now, now),
    )
    conn.execute(
        """
        INSERT INTO profile_grades(
            profile_key, evaluated_at_utc, grade, score, trading_style,
            trading_style_detail, source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (profile_key, now, grade, 99, style, style, now, now),
    )


def _insert_raw_activity(conn, profile_key: str, outcome: str, *, price: float, shares: float, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO profile_raw_activity(
            raw_activity_key, profile_key, event_key, event_slug, condition_id,
            market_slug, symbol, order_side, outcome_side, price, shares,
            notional_usd, activity_at_utc, observed_at_utc, source_table,
            source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?, ?, '{}', ?)
        """,
        (
            f"{profile_key}-{outcome}",
            profile_key,
            "event-1",
            "btc-updown-5m-test",
            "condition-1",
            "btc-updown-5m-test",
            "BTC",
            outcome,
            price,
            shares,
            price * shares,
            now.isoformat(),
            now.isoformat(),
            "pytest_batch_activity",
            now.isoformat(),
        ),
    )


def _insert_position(conn, profile_key: str, outcome: str, *, shares: float, cost: float, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO profile_event_positions(
            profile_event_position_key, profile_key, event_key, event_token_key,
            outcome, shares, cost_basis_usd, weighted_avg_price,
            source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            f"pos-{profile_key}-{outcome}",
            profile_key,
            "event-1",
            f"token-{outcome}",
            outcome,
            shares,
            cost,
            cost / shares,
            now.isoformat(),
            now.isoformat(),
        ),
    )
