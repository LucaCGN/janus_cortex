from __future__ import annotations

from app.modules.agentic.reduce_stop_lifecycle import (
    build_reduce_stop_lifecycle_evidence,
    live_signals_from_reduce_stop_lifecycle,
)


def _plan() -> dict:
    return {
        "event_id": "wnba-test-2026-05-27",
        "market_id": "market-1",
        "active_strategies": [
            {
                "strategy_id": "sky-grid",
                "family": "price_stability_micro_grid",
                "side": "Chicago Sky",
                "sleeve_id": "sky-grid",
                "sleeve_role": "grid_scalp",
                "entry_rules": {
                    "outcome_id": "outcome-sky",
                    "token_id": "token-sky",
                    "size": 5,
                    "price": 0.65,
                },
                "exit_rules": {"min_target_cents": 2.0},
                "stop_rules": {"max_adverse_cents": 3.0},
            }
        ],
    }


def _portfolio_state() -> dict:
    return {
        "target_management": {
            "sleeves": [
                {
                    "sleeve_id": "sky-grid",
                    "sleeve_role": "grid_scalp",
                    "strategy_id": "sky-grid",
                    "token_id": "token-sky",
                    "outcome_id": "outcome-sky",
                    "allocated_shares": 5.0,
                    "weighted_basis_price": 0.65,
                    "target_price": 0.67,
                    "target_status": "target_missing",
                }
            ]
        }
    }


def _direct_clob() -> dict:
    return {
        "open_positions": {
            "positions": [
                {
                    "asset": "token-sky",
                    "size": 5.0,
                    "avg_price": 0.65,
                    "outcome": "Chicago Sky",
                }
            ]
        }
    }


def _portfolio_state_with_target_status(target_status: str) -> dict:
    state = _portfolio_state()
    state["target_management"]["sleeves"][0]["target_status"] = target_status
    return state


def test_reduce_stop_lifecycle_emits_stop_reduce_signal_pytest() -> None:
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.61}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "B", "labels": ["slow_underdog_descent_with_spikes"]},
                "classification_snapshot": {"period": 3, "clock_seconds_remaining": 480, "score_gap": 5},
            },
        },
        portfolio_state=_portfolio_state(),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "stop_triggered"
    assert row["active_reduce_signal"] is True
    assert "reduce_stop_triggered" in row["reason_codes"]

    signals = live_signals_from_reduce_stop_lifecycle(evidence)
    assert len(signals) == 1
    assert signals[0].signal_type == "reduce"
    assert signals[0].risk_request.requested_shares == 5.0
    assert signals[0].payload["trigger_source"] == "reduce_stop_lifecycle"


def test_reduce_stop_lifecycle_stop_with_covered_target_does_not_emit_second_sell_pytest() -> None:
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.61}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "B", "labels": ["slow_underdog_descent_with_spikes"]},
                "classification_snapshot": {"period": 3, "clock_seconds_remaining": 480, "score_gap": 5},
            },
        },
        portfolio_state=_portfolio_state_with_target_status("target_covered"),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "covered_target_exit_review"
    assert row["active_reduce_signal"] is False
    assert row["rebuy_allowed"] is False
    assert "existing_target_covers_position" in row["reason_codes"]
    assert live_signals_from_reduce_stop_lifecycle(evidence) == []


def test_reduce_stop_lifecycle_stop_with_missing_target_still_emits_reduce_pytest() -> None:
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.61}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "B", "labels": ["slow_underdog_descent_with_spikes"]},
                "classification_snapshot": {"period": 3, "clock_seconds_remaining": 480, "score_gap": 5},
            },
        },
        portfolio_state=_portfolio_state_with_target_status("target_missing"),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "stop_triggered"
    assert row["active_reduce_signal"] is True
    assert live_signals_from_reduce_stop_lifecycle(evidence)[0].signal_type == "reduce"


def test_reduce_stop_lifecycle_classifies_q4_loss_mode_pytest() -> None:
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.63}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "D", "labels": ["garbage_time_or_falling_knife"]},
                "classification_snapshot": {"period": 4, "clock_seconds_remaining": 300, "score_gap": 12},
            },
        },
        portfolio_state=_portfolio_state(),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "q4_endgame_loss_mode"
    assert row["active_reduce_signal"] is True
    assert row["rebuy_allowed"] is False
    assert "rebuy_blocked_adverse_thesis_failed" in row["reason_codes"]


def test_reduce_stop_lifecycle_final_cleanup_does_not_emit_sell_signal_pytest() -> None:
    direct_clob = _direct_clob()
    direct_clob["open_orders"] = {
        "orders": [
            {
                "id": "target-order",
                "asset_id": "token-sky",
                "side": "sell",
                "price": 0.67,
            }
        ]
    }
    direct_clob["documented_residual_positions"] = [
        {
            "position": {
                "asset": "token-sky",
                "size": 5.0,
                "settlement_residual": {"resolved_market": {"resolved": True}},
            },
            "classification": {"residual_type": "zero_value_residual"},
        }
    ]
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.01}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "D", "labels": ["final_state"]},
                "classification_snapshot": {"period": 4, "clock_seconds_remaining": 0, "score_gap": 7, "final": True},
            },
        },
        portfolio_state=_portfolio_state(),
        direct_clob=direct_clob,
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "final_cleanup"
    assert row["active_reduce_signal"] is False
    assert row["rebuy_allowed"] is False
    cleanup = row["final_cleanup_reconciliation"]
    assert cleanup["status"] == "cleanup_reconciliation_required"
    assert cleanup["clob_exit_valid"] is False
    assert cleanup["order_submission_allowed"] is False
    assert cleanup["redemption_authority"] == "reviewed_settlement_path_only"
    assert cleanup["reason_codes"] == [
        "final_settled_direct_position_reconciliation_required",
        "final_settled_open_order_reconciliation_required",
        "final_settled_local_target_reconciliation_required",
        "final_settled_documented_residual_only",
    ]
    assert evidence["final_cleanup_reason_counts"] == {
        "final_settled_direct_position_reconciliation_required": 1,
        "final_settled_documented_residual_only": 1,
        "final_settled_local_target_reconciliation_required": 1,
        "final_settled_open_order_reconciliation_required": 1,
    }
    assert live_signals_from_reduce_stop_lifecycle(evidence) == []


def test_reduce_stop_lifecycle_near_final_losing_position_emits_reduce_signal_pytest() -> None:
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.03}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "D", "labels": ["late_adverse_position"]},
                "classification_snapshot": {"period": 4, "clock_seconds_remaining": 90, "score_gap": 9},
            },
        },
        portfolio_state=_portfolio_state(),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "near_final_loss_cleanup"
    assert row["active_reduce_signal"] is True
    assert row["rebuy_allowed"] is False
    assert evidence["near_final_loss_cleanup_count"] == 1
    signals = live_signals_from_reduce_stop_lifecycle(evidence)
    assert len(signals) == 1
    assert signals[0].signal_type == "reduce"
    assert signals[0].payload["trigger_type"] == "near_final_loss_cleanup"


def test_reduce_stop_lifecycle_stale_target_with_direct_inventory_emits_reduce_signal_pytest() -> None:
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=_plan(),
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.64}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "B", "labels": ["normal_live"]},
                "classification_snapshot": {"period": 2, "clock_seconds_remaining": 480, "score_gap": 2},
            },
        },
        portfolio_state=_portfolio_state(),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "stale_target_exit"
    assert row["active_reduce_signal"] is True
    assert live_signals_from_reduce_stop_lifecycle(evidence)[0].reason_codes == ["target_uncovered_reduce_review"]


def test_reduce_stop_lifecycle_adverse_thesis_failure_blocks_rebuy_pytest() -> None:
    plan = _plan()
    plan["active_strategies"][0]["stop_rules"] = {}
    evidence = build_reduce_stop_lifecycle_evidence(
        event_id="wnba-test-2026-05-27",
        plan=plan,
        market_state={
            "token_states": {"token-sky": {"best_bid": 0.64}},
            "live_game_context": {
                "game_scenario": {"scenario_level": "C", "labels": ["adverse_thesis_failed"]},
                "classification_snapshot": {"period": 3, "clock_seconds_remaining": 480, "score_gap": 4},
            },
        },
        portfolio_state=_portfolio_state(),
        direct_clob=_direct_clob(),
        min_size=5,
    )

    row = evidence["rows"][0]
    assert row["state"] == "adverse_thesis_failed"
    assert row["active_reduce_signal"] is True
    assert row["rebuy_allowed"] is False
    assert "rebuy_blocked_adverse_thesis_failed" in row["reason_codes"]
