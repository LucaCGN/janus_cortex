from __future__ import annotations

from app.modules.agentic.prewindow_readiness import build_prewindow_sleeve_readiness


def test_prewindow_readiness_promotes_grid_and_flags_ultra_low_pytest() -> None:
    report = build_prewindow_sleeve_readiness(
        event_id="nba-test",
        strategy_plan={
            "active_strategies": [
                {
                    "strategy_id": "grid",
                    "sleeve_id": "grid",
                    "sleeve_role": "grid_scalp",
                    "family": "price_stability_micro_grid",
                },
                {
                    "strategy_id": "ultra",
                    "sleeve_id": "ultra",
                    "sleeve_role": "ultra_low_rebound",
                    "family": "ultra_low_underdog_decimal_grid",
                },
            ]
        },
        backtest_review={
            "schema_version": "sleeve_deep_backtest_review_v1",
            "sleeve_rankings": [
                {
                    "live_sleeve_role": "grid_scalp",
                    "avg_return_with_slippage": 0.012,
                    "min_order_pnl_total": 4.25,
                    "trade_count": 100,
                },
                {
                    "live_sleeve_role": "ultra_low_rebound",
                    "avg_return_with_slippage": -0.8,
                    "min_order_pnl_total": -40.0,
                    "trade_count": 80,
                },
            ],
            "recommendations": [
                {
                    "area": "sleeve_promotion_candidates",
                    "top_rows": [
                        {
                            "live_sleeve_role": "grid_scalp",
                            "strategy_family": "q4_clutch",
                            "avg_return_with_slippage": 0.2,
                        }
                    ],
                }
            ],
        },
        source="pytest",
    )

    assert report["status"] == "YELLOW"
    assert report["readiness_status_counts"]["supported_for_live_validation"] == 1
    assert report["readiness_status_counts"]["experimental_guardrails_required"] == 1
    assert report["sleeves"][0]["readiness_status"] == "supported_for_live_validation"
    assert report["sleeves"][1]["readiness_status"] == "experimental_guardrails_required"
    assert any(warning["code"] == "ultra_low_naive_replay_negative" for warning in report["warnings"])


def test_prewindow_readiness_reports_wNBA_parity_blocker_pytest() -> None:
    report = build_prewindow_sleeve_readiness(
        event_id="wnba-test",
        strategy_plan={"active_strategies": []},
        backtest_review={"wnba_blockers": [{"blocker": "missing_wnba_market_state_panel_or_price_history"}]},
        source="pytest",
    )

    assert any(warning["code"] == "wnba_replay_parity_blocked" for warning in report["warnings"])
