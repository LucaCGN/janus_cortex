from __future__ import annotations

from app.modules.agentic.basketball_logic import (
    build_price_impact_windows,
    build_profit_ratcheted_risk_state,
    classify_basketball_regime,
    classify_virtual_dead_state,
    evaluate_wnba_minimal_live_readiness,
    generate_strategy_sleeve_candidates,
    tag_basketball_pbp_events,
)


def test_basketball_classifier_covers_core_scenarios_pytest() -> None:
    close_game = classify_basketball_regime({"period": 4, "score_gap": 3, "starters_active": True})
    assert close_game["scenario_level"] == "A"
    assert "clutch_close_game" in close_game["regime_labels"]
    assert close_game["trading_impact"] == "allow_hold_hedge_add_down_micro_grid"

    inversion = classify_basketball_regime(
        {"period": 4, "score_gap": 2, "previous_underdog_price": 0.42, "underdog_price": 0.57}
    )
    assert inversion["scenario_level"] == "S"
    assert "full_expectation_inversion" in inversion["regime_labels"]

    blowout = classify_basketball_regime({"period": 3, "score_gap": 24, "bench_emptying": True})
    assert blowout["scenario_level"] == "D"
    assert blowout["trading_impact"] == "shutdown"

    ot = classify_basketball_regime({"period": 5, "score_gap": 1})
    assert ot["scenario_level"] == "S"
    assert "overtime" in ot["regime_labels"]


def test_pbp_tagger_and_price_windows_emit_shared_basketball_features_pytest() -> None:
    tagged = tag_basketball_pbp_events(
        [
            {
                "event_index": 1,
                "timestamp_utc": "2026-05-17T18:00:00Z",
                "period": 4,
                "description": "Paige makes 3-pt jump shot",
                "player": "Paige",
                "score_margin": -4,
            },
            {
                "event_index": 2,
                "timestamp_utc": "2026-05-17T18:00:20Z",
                "period": 4,
                "description": "Turnover by Dallas",
                "score_margin": -1,
            },
        ],
        player_roles={"Paige": "star_creator"},
    )
    assert tagged[0]["event_type"] == "shot"
    assert "star_event" in tagged[0]["tags"]
    assert tagged[1]["event_type"] == "turnover"

    windows = build_price_impact_windows(
        tagged,
        [
            {"captured_at_utc": "2026-05-17T17:59:40Z", "mid_price": 0.12},
            {"captured_at_utc": "2026-05-17T18:00:30Z", "mid_price": 0.18},
        ],
    )
    assert windows[0]["before_tick_count"] == 1
    assert windows[0]["after_tick_count"] == 1
    assert windows[0]["price_impact"] == 0.06


def test_sleeve_generation_conflicts_and_shadow_fallback_pytest() -> None:
    classification = classify_basketball_regime({"period": 4, "score_gap": 2, "starters_active": True})
    generated = generate_strategy_sleeve_candidates(
        classification,
        existing_sleeves=[{"sleeve_id": "close-game-micro-grid"}],
        live_authority=False,
    )

    assert generated["candidate_count"] == 1
    assert generated["candidates"][0]["state"] == "shadow_only"
    assert generated["conflicts"][0]["reason"] == "duplicate_sleeve"
    assert generated["dependency_graph"]["portfolio_coordination_required"] is False


def test_profit_ratcheted_risk_state_blocks_tail_from_unrealized_and_unresolved_inventory_pytest() -> None:
    state = build_profit_ratcheted_risk_state(
        portfolio_value=100.0,
        realized_event_pnl=5.0,
        realized_day_pnl=5.0,
        open_unrealized_pnl=50.0,
        unresolved_inventory=True,
        scenario_level="S",
        confidence=0.9,
    )

    assert state["ladder"] == "0-20"
    assert state["unrealized_profit_unlocks_risk"] is False
    assert state["tail_risk_budget_usd"] == 0.0
    assert state["blocked"] is True

    up_large = build_profit_ratcheted_risk_state(
        portfolio_value=100.0,
        realized_event_pnl=80.0,
        realized_day_pnl=40.0,
        scenario_level="S",
        confidence=0.9,
    )
    assert up_large["ladder"] == ">100"
    assert up_large["tail_risk_budget_usd"] > 0


def test_virtual_dead_policy_and_wnba_readiness_gate_pytest() -> None:
    live_close = classify_virtual_dead_state({"period": 4, "clock_seconds_remaining": 42, "score_gap": 4})
    assert live_close["virtual_dead"] is False
    assert "hold" in live_close["must_compare_before_loss_exit"]

    garbage = classify_virtual_dead_state({"period": 4, "clock_seconds_remaining": 40, "score_gap": 14})
    assert garbage["virtual_dead"] is True
    assert "late_severe_deficit" in garbage["reasons"]

    blocked = evaluate_wnba_minimal_live_readiness(
        {
            "linked_games": 1,
            "passive_orderbook_ticks": 30,
            "fillability_samples": 5,
            "core_safety_controls_ready": True,
            "direct_clob_clean": True,
        }
    )
    assert blocked["status"] == "blocked"
    assert blocked["live_money_allowed"] is False

    ready = evaluate_wnba_minimal_live_readiness(
        {
            "linked_games": 3,
            "passive_orderbook_ticks": 100,
            "fillability_samples": 20,
            "core_safety_controls_ready": True,
            "direct_clob_clean": True,
        }
    )
    assert ready["status"] == "ready_for_minimum_size_operator_review"
    assert ready["minimum_size_test_requires_operator_approval"] is True
