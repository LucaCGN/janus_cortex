from __future__ import annotations

from app.modules.agentic.live_game_context import (
    build_live_game_context_evidence,
    live_signals_from_live_game_context,
)


def _plan() -> dict:
    return {
        "event_id": "nba-sas-okc-2026-05-27",
        "market_id": "market-sas-okc",
        "active_strategies": [
            {
                "strategy_id": "sas-grid",
                "family": "price_stability_micro_grid",
                "side": "Spurs",
                "sleeve_id": "sas-grid",
                "sleeve_group": "sas",
                "sleeve_role": "grid_scalp",
                "entry_rules": {
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "outcome_label": "Spurs",
                    "side": "buy",
                },
            },
            {
                "strategy_id": "okc-grid",
                "family": "price_stability_micro_grid",
                "side": "Thunder",
                "sleeve_id": "okc-grid",
                "sleeve_group": "okc",
                "sleeve_role": "grid_scalp",
                "entry_rules": {
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "outcome_label": "Thunder",
                    "side": "buy",
                },
            },
        ],
    }


def test_live_game_context_records_scenario_ml_confidence_and_no_signal_without_realized_budget_pytest() -> None:
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-27",
        plan=_plan(),
        market_state={
            "normalized_live_snapshot": {"game": {"period": 4, "clock": "05:30", "home_score": 91, "away_score": 88}},
            "token_states": {
                "token-sas": {"price": 0.44, "best_ask": 0.45, "spread_cents": 1, "score_gap": 3},
                "token-okc": {"price": 0.56, "best_ask": 0.57, "spread_cents": 1, "score_gap": -3},
            },
            "pbp_annotation": {
                "model_tier": "deterministic_fallback",
                "intended_model": "gpt-5.4-nano",
                "tags": [
                    {
                        "tag_type": "score_run",
                        "confidence": 0.72,
                        "sleeve_relevance": ["grid_scalp"],
                        "evidence": {"swing": 10},
                    }
                ],
            },
            "paired_microcycle": {"cycles": []},
        },
        portfolio_state={},
        direct_clob={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        max_buy_notional_usd=10.0,
        min_buy_notional_usd=1.0,
    )

    assert evidence["schema_version"] == "live_game_context_evidence_v1"
    assert evidence["game_scenario"]["scenario_level"] in {"A", "B"}
    assert evidence["ml_confidence_by_sleeve"]["sas-grid"]["model_status"] == "deterministic_fallback"
    assert evidence["ml_confidence_by_sleeve"]["sas-grid"]["executable"] is False
    assert evidence["opportunistic_signal_candidates"][0]["status"] == "blocked"
    assert evidence["opportunistic_signal_candidates"][0]["reason_codes"][0] == "realized_profit_opportunistic_budget_below_minimum"
    assert live_signals_from_live_game_context(evidence) == []


def test_live_game_context_emits_standalone_opportunistic_signal_when_profit_budget_funds_it_pytest() -> None:
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-27",
        plan=_plan(),
        market_state={
            "normalized_live_snapshot": {"game": {"period": 4, "clock": "04:20", "home_score": 91, "away_score": 88}},
            "token_states": {
                "token-sas": {"price": 0.04, "best_ask": 0.04, "spread_cents": 1, "score_gap": -9},
                "token-okc": {"price": 0.96, "best_ask": 0.97, "spread_cents": 1, "score_gap": 9},
            },
            "pbp_annotation": {
                "tags": [
                    {
                        "tag_type": "score_run",
                        "confidence": 0.72,
                        "sleeve_relevance": ["ultra_low_rebound"],
                        "evidence": {"swing": 10},
                    }
                ]
            },
            "paired_microcycle": {
                "cycles": [
                        {
                            "buy_leg": {"status": "filled", "shares": 500, "price": 0.04},
                            "sell_leg": {"status": "filled", "shares": 500, "price": 0.06},
                        }
                ]
            },
        },
        portfolio_state={},
        direct_clob={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        max_buy_notional_usd=10.0,
        min_buy_notional_usd=1.0,
    )

    candidates = evidence["opportunistic_signal_candidates"]
    assert candidates[0]["status"] == "signal_candidate"
    assert candidates[0]["side"] == "Spurs"
    assert candidates[0]["requested_notional_usd"] >= 1.0
    assert candidates[0]["lifecycle_policy"]["target_delta_cents"] == 1.0

    signals = live_signals_from_live_game_context(evidence)
    assert len(signals) == 1
    assert signals[0].signal_type == "buy"
    assert signals[0].risk_request is not None
    assert signals[0].risk_request.requested_notional_usd >= 1.0
    assert signals[0].payload["standalone_signal"] is True
    assert signals[0].payload["lifecycle_policy"]["target_policy"] == "profit_ratcheted_tail_micro_target"
