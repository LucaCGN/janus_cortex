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
                "token-sas": {"price": 0.44, "best_ask": 0.45, "spread_cents": 1, "score_gap": 3, "scoreboard_age_seconds": 1},
                "token-okc": {"price": 0.56, "best_ask": 0.57, "spread_cents": 1, "score_gap": -3, "scoreboard_age_seconds": 1},
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
                "token-sas": {"price": 0.04, "best_ask": 0.04, "spread_cents": 1, "score_gap": -9, "scoreboard_age_seconds": 1},
                "token-okc": {"price": 0.96, "best_ask": 0.97, "spread_cents": 1, "score_gap": 9, "scoreboard_age_seconds": 1},
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


def test_live_game_context_blocks_opportunistic_signal_without_scoreboard_context_pytest() -> None:
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-27",
        plan=_plan(),
        market_state={
            "normalized_live_snapshot": {"game": {"status": "scheduled"}},
            "token_states": {
                "token-sas": {"price": 0.04, "best_ask": 0.04, "spread_cents": 1},
                "token-okc": {"price": 0.96, "best_ask": 0.97, "spread_cents": 1},
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
    assert candidates[0]["status"] == "blocked"
    assert candidates[0]["reason_codes"][0] == "scoreboard_freshness_required"
    assert live_signals_from_live_game_context(evidence) == []


def test_live_game_context_promotes_pbp_player_catalyst_into_snapshot_pytest() -> None:
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-30",
        plan=_plan(),
        market_state={
            "normalized_live_snapshot": {"game": {"period": 3, "clock": "03:45", "home_score": 71, "away_score": 68}},
            "token_states": {
                "token-sas": {"price": 0.61, "best_ask": 0.62, "spread_cents": 1, "score_gap": 3, "scoreboard_age_seconds": 1},
                "token-okc": {"price": 0.39, "best_ask": 0.40, "spread_cents": 1, "score_gap": -3, "scoreboard_age_seconds": 1},
            },
            "pbp_annotation": {
                "model_tier": "deterministic_fallback",
                "tags": [
                    {
                        "tag_type": "player_foul_trouble_watch",
                        "severity": "elevated",
                        "confidence": 0.72,
                        "sleeve_relevance": ["rebound_snipe", "grid_scalp"],
                        "reason": "Victor Wembanyama personal foul, his 5th foul.",
                        "evidence": {"matched_rows": ["victor wembanyama personal foul, his 5th foul"]},
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

    assert evidence["classification_snapshot"]["star_foul_trouble"] is True
    assert evidence["ml_confidence_by_sleeve"]["sas-grid"]["model_status"] == "deterministic_fallback"


def test_live_game_context_emits_rebound_snipe_candidate_from_reviewed_sleeve_and_catalyst_pytest() -> None:
    plan = {
        "event_id": "nba-sas-okc-2026-05-30",
        "market_id": "market-sas-okc",
        "active_strategies": [
            {
                "strategy_id": "okc-rebound-snipe",
                "family": "catalyst_rebound_snipe",
                "side": "Thunder",
                "sleeve_id": "okc-35c-rebound",
                "sleeve_group": "okc",
                "sleeve_role": "rebound_snipe",
                "entry_rules": {
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "outcome_label": "Thunder",
                    "side": "buy",
                    "size": 5,
                },
            },
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
                    "size": 5,
                },
            },
        ],
    }
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-30",
        plan=plan,
        market_state={
            "normalized_live_snapshot": {"game": {"period": 3, "clock": "04:40", "home_score": 72, "away_score": 65}},
            "token_states": {
                "token-okc": {"price": 0.35, "best_ask": 0.35, "spread_cents": 1, "score_gap": -7, "scoreboard_age_seconds": 1},
                "token-sas": {"price": 0.65, "best_ask": 0.66, "spread_cents": 1, "score_gap": 7, "scoreboard_age_seconds": 1},
            },
            "pbp_annotation": {
                "model_tier": "gpt-5.4-nano",
                "tags": [
                    {
                        "tag_type": "player_foul_trouble_watch",
                        "confidence": 0.8,
                        "sleeve_relevance": ["rebound_snipe"],
                        "reason": "Victor Wembanyama personal foul, his 5th foul.",
                    },
                    {
                        "tag_type": "score_run",
                        "confidence": 0.7,
                        "sleeve_relevance": ["rebound_snipe"],
                        "evidence": {"swing": 8},
                    },
                ],
            },
            "manual_interference_rebase": {
                "schema_version": "manual_interference_rebase_v1",
                "status": "required",
            },
            "paired_microcycle": {"cycles": []},
        },
        portfolio_state={
            "operator_sizing_policy": {"mode": "fixed_min_shares", "min_size": 5, "min_buy_notional_usd": 0},
            "manual_interference_rebase": {
                "schema_version": "manual_interference_rebase_v1",
                "status": "required",
            },
        },
        direct_clob={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        max_buy_notional_usd=20.0,
        min_buy_notional_usd=0.0,
    )

    candidate = evidence["rebound_snipe_signal_candidates"][0]
    assert candidate["status"] == "signal_candidate"
    assert candidate["sleeve_role"] == "rebound_snipe"
    assert candidate["requested_shares"] == 5
    assert candidate["requested_notional_usd"] == 1.75
    assert candidate["manual_rebase_status"] == "required"
    assert "star_foul_trouble" in candidate["reason_codes"]
    assert "large_recent_score_run" in candidate["reason_codes"]
    assert "low_price_close_game_dislocation" in candidate["reason_codes"]
    assert "manual_interference_rebase_active" in candidate["reason_codes"]

    signals = live_signals_from_live_game_context(evidence)
    rebound = [signal for signal in signals if signal.payload.get("trigger_type") == "rebound_snipe_catalyst_entry"]
    assert len(rebound) == 1
    assert rebound[0].risk_request is not None
    assert rebound[0].risk_request.requested_shares == 5
    assert rebound[0].payload["lifecycle_policy"]["target_policy"] == "rebound_snipe_ladder_target"


def test_live_game_context_blocks_rebound_snipe_without_reviewed_sleeve_pytest() -> None:
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-30",
        plan=_plan(),
        market_state={
            "normalized_live_snapshot": {"game": {"period": 3, "clock": "04:40", "home_score": 72, "away_score": 65}},
            "token_states": {
                "token-okc": {"price": 0.35, "best_ask": 0.35, "spread_cents": 1, "score_gap": -7, "scoreboard_age_seconds": 1},
                "token-sas": {"price": 0.65, "best_ask": 0.66, "spread_cents": 1, "score_gap": 7, "scoreboard_age_seconds": 1},
            },
            "pbp_annotation": {
                "tags": [
                    {
                        "tag_type": "player_foul_trouble_watch",
                        "confidence": 0.8,
                        "sleeve_relevance": ["rebound_snipe"],
                        "reason": "Victor Wembanyama personal foul, his 5th foul.",
                    }
                ],
            },
            "paired_microcycle": {"cycles": []},
        },
        portfolio_state={},
        direct_clob={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        max_buy_notional_usd=20.0,
        min_buy_notional_usd=0.0,
    )

    candidate = evidence["rebound_snipe_signal_candidates"][0]
    assert candidate["status"] == "blocked"
    assert candidate["reason_codes"][0] == "reviewed_rebound_snipe_sleeve_required"
    assert [
        signal for signal in live_signals_from_live_game_context(evidence) if signal.payload.get("trigger_type") == "rebound_snipe_catalyst_entry"
    ] == []


def test_live_game_context_blocks_rebound_snipe_without_fresh_scoreboard_pytest() -> None:
    plan = _plan()
    plan["active_strategies"].append(
        {
            "strategy_id": "okc-rebound-snipe",
            "family": "catalyst_rebound_snipe",
            "side": "Thunder",
            "sleeve_id": "okc-35c-rebound",
            "sleeve_group": "okc",
            "sleeve_role": "rebound_snipe",
            "entry_rules": {
                "outcome_id": "outcome-okc",
                "token_id": "token-okc",
                "outcome_label": "Thunder",
                "side": "buy",
                "size": 5,
            },
        }
    )
    evidence = build_live_game_context_evidence(
        event_id="nba-sas-okc-2026-05-30",
        plan=plan,
        market_state={
            "normalized_live_snapshot": {"game": {"period": 3, "clock": "04:40", "home_score": 72, "away_score": 65}},
            "token_states": {
                "token-okc": {"price": 0.35, "best_ask": 0.35, "spread_cents": 1, "score_gap": -7, "scoreboard_age_seconds": 180},
                "token-sas": {"price": 0.65, "best_ask": 0.66, "spread_cents": 1, "score_gap": 7, "scoreboard_age_seconds": 180},
            },
            "pbp_annotation": {
                "tags": [
                    {
                        "tag_type": "player_foul_trouble_watch",
                        "confidence": 0.8,
                        "sleeve_relevance": ["rebound_snipe"],
                        "reason": "Victor Wembanyama personal foul, his 5th foul.",
                    }
                ],
            },
            "paired_microcycle": {"cycles": []},
        },
        portfolio_state={},
        direct_clob={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        max_buy_notional_usd=20.0,
        min_buy_notional_usd=0.0,
    )

    candidate = evidence["rebound_snipe_signal_candidates"][0]
    assert candidate["status"] == "blocked"
    assert candidate["reason_codes"][0] == "scoreboard_freshness_required"
    assert [
        signal for signal in live_signals_from_live_game_context(evidence) if signal.payload.get("trigger_type") == "rebound_snipe_catalyst_entry"
    ] == []
