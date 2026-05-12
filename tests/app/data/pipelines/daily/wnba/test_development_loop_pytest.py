from __future__ import annotations

from app.data.pipelines.daily.wnba.analysis.development_loop import evaluate_wnba_standard_development_loop
from tools.run_wnba_analysis_bundle import build_wnba_analysis_bundle


def test_wnba_development_loop_allows_passive_shadow_fixture_pytest() -> None:
    analysis_payload = build_wnba_analysis_bundle(season="2026", use_fixture=True)

    payload = evaluate_wnba_standard_development_loop(analysis_payload=analysis_payload)

    assert payload["status"] == "ready_for_standard_loop_passive_shadow"
    assert payload["standard_loop_allowed"] is True
    assert payload["orders_allowed"] is False
    assert payload["live_trading_allowed"] is False
    assert payload["minimum_before_standard_loop"] == []
    assert "missing_passive_wnba_clob_tick_trade_capture_for_full_microstructure_replay" in payload["calibrated_or_live_blockers"]
    assert "placing_orders" in payload["disallowed_scopes"]


def test_wnba_development_loop_recognizes_finished_price_history_probe_pytest() -> None:
    analysis_payload = build_wnba_analysis_bundle(season="2026", use_fixture=True)
    price_payload = {
        "status": "price_history_backtest_complete",
        "price_history_rows": 1200,
        "state_panel_rows": 900,
        "ml_readiness": {
            "status": "blocked",
            "blockers": ["insufficient_distinct_games_for_wnba_ml"],
        },
    }

    payload = evaluate_wnba_standard_development_loop(
        analysis_payload=analysis_payload,
        price_history_payload=price_payload,
        migrations_applied=False,
    )

    assert payload["status"] == "ready_for_standard_loop_price_history_shadow"
    assert payload["price_history_probe_ready"] is True
    task_statuses = {task["id"]: task["status"] for task in payload["next_tasks"]}
    assert task_statuses["backfill_closed_wnba_polymarket_price_history"] == "ready"
    assert task_statuses["run_wnba_price_history_shadow_backtests"] == "ready"
    assert "wnba_migrations_not_applied_to_safe_db" in payload["calibrated_or_live_blockers"]


def test_wnba_development_loop_blocks_when_structural_readiness_fails_pytest() -> None:
    analysis_payload = {
        "data_audit": {"status": "blocked", "blockers": ["missing_wnba_state_panel_rows"]},
        "integration_readiness": {
            "status": "blocked",
            "passive_shadow_ready": False,
            "orders_allowed": False,
            "structural_blockers": ["missing_wnba_lane_signal_rows"],
            "calibration_blockers": [],
        },
        "historical_backfill": {"status": "ready"},
        "ml_training": {"blockers": []},
    }

    payload = evaluate_wnba_standard_development_loop(analysis_payload=analysis_payload)

    assert payload["status"] == "blocked"
    assert payload["standard_loop_allowed"] is False
    assert "missing_wnba_lane_signal_rows" in payload["minimum_before_standard_loop"]
    assert "missing_wnba_state_panel_rows" in payload["minimum_before_standard_loop"]
