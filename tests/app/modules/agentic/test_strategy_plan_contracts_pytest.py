from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.agentic.contracts import ActiveStrategy, StrategyPlan
from app.modules.agentic.engine import evaluate_strategy_plan


REPO_ROOT = Path(__file__).resolve().parents[4]
WNBA_2026_05_24_PLAN_PATHS = [
    REPO_ROOT / "local/shared/artifacts/strategy-plans/2026-05-24/wnba-phx-atl-2026-05-24/current.json",
    REPO_ROOT / "local/shared/artifacts/strategy-plans/2026-05-24/wnba-dal-nyl-2026-05-24/current.json",
    REPO_ROOT / "local/shared/artifacts/strategy-plans/2026-05-24/wnba-wsh-sea-2026-05-24/current.json",
]


def test_strategy_plan_accepts_multi_strategy_executable_json_pytest() -> None:
    generated_at = datetime.now(timezone.utc)
    plan = StrategyPlan(
        event_id="0042500212",
        market_id="market-1",
        generated_at_utc=generated_at,
        valid_until_utc=generated_at + timedelta(minutes=10),
        plan_owner="janus_internal_llm",
        context_summary={"market_thesis": "close game supports grid and rebound legs"},
        active_strategies=[
            ActiveStrategy(
                strategy_id="grid-phi-1",
                family="resistance_band_rebound_grid",
                side="PHI",
                budget_usd=3.0,
                max_positions=3,
                entry_rules={"price_bands": [[0.19, 0.35]], "score_gap_max": 10},
                exit_rules={"targets_cents": [4, 7, 10]},
                stop_rules={"max_adverse_cents": 5},
                hedge_rules={"opposite_side_allowed": True},
                revision_triggers=[{"type": "quarter_end"}, {"type": "human_intervention"}],
            ),
            ActiveStrategy(
                strategy_id="winner-nyk-1",
                family="winner_definition",
                side="NYK",
                budget_usd=2.0,
                max_positions=1,
            ),
        ],
        trigger_conditions=[{"type": "price_band", "min": 0.19, "max": 0.35}],
        portfolio_reconciliation=[{"action": "protect", "reason": "manual position detected"}],
        explainability={"invalidates": ["orderbook_stale", "score_gap_expands"]},
    )

    payload = plan.model_dump(mode="json")

    assert payload["schema_version"] == "strategy_plan_v1"
    assert len(payload["active_strategies"]) == 2
    assert payload["active_strategies"][0]["family"] == "resistance_band_rebound_grid"


def test_strategy_plan_rejects_duplicate_strategy_ids_pytest() -> None:
    with pytest.raises(ValidationError, match="strategy_id values must be unique"):
        StrategyPlan(
            event_id="event-1",
            market_id="market-1",
            active_strategies=[
                ActiveStrategy(strategy_id="dup", family="grid", side="A"),
                ActiveStrategy(strategy_id="dup", family="winner", side="B"),
            ],
        )


def test_strategy_plan_evaluator_compiles_valid_order_intent_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="grid-1",
                family="resistance_band_rebound_grid",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "side": "buy",
                    "price": 0.2,
                    "size": 5,
                    "price_band": [0.15, 0.25],
                    "max_orderbook_age_seconds": 3,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(plan, market_state={"price": 0.2, "orderbook_age_seconds": 1}, dry_run=True)

    assert result.intent_count == 1
    assert result.blocked_count == 0
    assert result.intents[0].price == 0.2
    assert result.intents[0].metadata["required_notional_usd"] == 1.0


def test_strategy_plan_position_limit_is_token_scoped_for_parallel_sleeves_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="favorite-grid",
                family="price_stability_micro_grid",
                side="Favorite",
                budget_usd=5.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-fav",
                    "token_id": "token-fav",
                    "side": "buy",
                    "price": 0.8,
                    "size": 5,
                    "price_band": [0.01, 0.95],
                },
            ),
            ActiveStrategy(
                strategy_id="dog-grid",
                family="price_stability_micro_grid",
                side="Underdog",
                budget_usd=5.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-dog",
                    "token_id": "token-dog",
                    "side": "buy",
                    "price": 0.2,
                    "size": 5,
                    "price_band": [0.01, 0.95],
                },
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"token_states": {"token-fav": {"price": 0.8}, "token-dog": {"price": 0.2}}},
        portfolio_state={
            "open_positions": 1,
            "open_orders": 0,
            "event_scoped_direct_clob": {
                "open_positions": {"positions": [{"asset": "token-fav", "size": 5}]},
                "open_orders": {"orders": []},
            },
        },
        dry_run=True,
    )

    assert result.intent_count == 1
    assert result.intents[0].strategy_id == "dog-grid"
    assert any(blocker["strategy_id"] == "favorite-grid" and blocker["reason"] == "position_limit_reached" for blocker in result.blockers)


def test_strategy_plan_allows_explicit_sleeve_scoped_add_with_existing_position_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="spurs-grid",
                family="price_stability_micro_grid",
                side="Spurs",
                budget_usd=5.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-spurs",
                    "token_id": "token-spurs",
                    "side": "buy",
                    "price": 0.04,
                    "size": 25,
                    "price_band": [0.01, 0.05],
                },
            ),
            ActiveStrategy(
                strategy_id="spurs-ultra-low",
                family="ultra_low_underdog_decimal_grid",
                side="Spurs",
                budget_usd=2.0,
                max_positions=1,
                sleeve_role="ultra_low_rebound",
                entry_rules={
                    "outcome_id": "outcome-spurs",
                    "token_id": "token-spurs",
                    "side": "buy",
                    "price": 0.04,
                    "size": 25,
                    "price_band": [0.003, 0.05],
                    "position_limit_scope": "sleeve",
                    "allow_existing_position_add": True,
                    "allow_ultra_low_underdog": True,
                    "allow_sub_10c_underdog_grid": True,
                    "min_clock_remaining_seconds": 30,
                    "max_scoreboard_age_seconds": 45,
                    "max_abs_score_gap": 35,
                },
                exit_rules={"target_required": True, "target_policy": "micro_grid_scaled", "min_target_cents": 0.3},
                stop_rules={"max_adverse_cents": 2},
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "token_states": {"token-spurs": {"price": 0.04, "clock_remaining": 300, "score_gap": -18, "scoreboard_age_seconds": 3}},
        },
        portfolio_state={
            "event_scoped_direct_clob": {
                "open_positions": {"positions": [{"asset": "token-spurs", "size": 5}]},
                "open_orders": {"orders": []},
            },
            "pending_intent_orders": [],
        },
        dry_run=True,
    )

    assert [intent.strategy_id for intent in result.intents] == ["spurs-ultra-low"]
    assert any(blocker["strategy_id"] == "spurs-grid" and blocker["reason"] == "position_limit_reached" for blocker in result.blockers)
    assert not any(
        blocker["strategy_id"] == "spurs-ultra-low" and blocker["reason"] == "position_limit_reached"
        for blocker in result.blockers
    )


def test_strategy_plan_keeps_pending_intent_blocker_for_sleeve_scoped_add_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="spurs-ultra-low",
                family="ultra_low_underdog_decimal_grid",
                side="Spurs",
                budget_usd=2.0,
                max_positions=1,
                sleeve_role="ultra_low_rebound",
                entry_rules={
                    "outcome_id": "outcome-spurs",
                    "token_id": "token-spurs",
                    "side": "buy",
                    "price": 0.04,
                    "size": 25,
                    "price_band": [0.003, 0.05],
                    "position_limit_scope": "sleeve",
                    "allow_existing_position_add": True,
                    "allow_ultra_low_underdog": True,
                    "allow_sub_10c_underdog_grid": True,
                    "min_clock_remaining_seconds": 30,
                    "max_scoreboard_age_seconds": 45,
                    "max_abs_score_gap": 35,
                },
                exit_rules={"target_required": True, "target_policy": "micro_grid_scaled", "min_target_cents": 0.3},
                stop_rules={"max_adverse_cents": 2},
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "token_states": {"token-spurs": {"price": 0.04, "clock_remaining": 300, "score_gap": -18, "scoreboard_age_seconds": 3}},
        },
        portfolio_state={
            "event_scoped_direct_clob": {
                "open_positions": {"positions": [{"asset": "token-spurs", "size": 5}]},
                "open_orders": {"orders": []},
            },
            "pending_intent_orders": [{"strategy_id": "spurs-ultra-low", "side": "buy", "market_token_id": "token-spurs"}],
        },
        dry_run=True,
    )

    assert result.intent_count == 0
    assert any(blocker["strategy_id"] == "spurs-ultra-low" and blocker["reason"] == "pending_intent_limit_reached" for blocker in result.blockers)


def test_strategy_plan_evaluator_promotes_live_aggregation_candidate_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-1",
                            "signal_type": "sell",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-q4-grid",
                            "sleeve_group": "okc",
                            "sleeve_role": "grid_scalp",
                            "strategy_id": "okc-q4-band-grid-v1",
                            "strategy_family": "band_grid",
                            "cycle_id": "cycle-okc",
                            "trigger_type": "paired_microcycle_next_leg",
                            "trigger_source": "paired_microcycle",
                            "requested_shares": 5,
                            "max_price": 0.04,
                            "reason_codes": ["filled_buy_requires_paired_sell"],
                        }
                    ]
                }
            }
        },
        dry_run=True,
    )

    assert result.intent_count == 1
    assert result.intents[0].side == "sell"
    assert result.intents[0].price == 0.04
    assert result.intents[0].size == 5
    assert result.intents[0].sleeve_id == "okc-q4-grid"
    assert result.intents[0].metadata["source"] == "live_signal_aggregation"
    assert result.intents[0].metadata["cycle_id"] == "cycle-okc"
    assert result.intents[0].metadata["trigger_type"] == "paired_microcycle_next_leg"
    assert result.intents[0].metadata["paired_lifecycle"]["cycle_id"] == "cycle-okc"
    assert "filled_buy_requires_paired_sell" in result.intents[0].metadata["paired_lifecycle"]["reason_codes"]


def test_strategy_plan_evaluator_requires_lifecycle_policy_for_aggregation_buy_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-1",
                            "signal_type": "buy",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-grid",
                            "strategy_id": "grid-1",
                            "strategy_family": "resistance_band_rebound_grid",
                            "requested_shares": 5,
                            "max_price": 0.2,
                        }
                    ]
                }
            }
        },
        dry_run=True,
    )

    assert result.intent_count == 0
    assert result.blocked_count == 1
    assert result.blockers[0]["reason"] == "paired_lifecycle_policy_required_for_buy"


def test_strategy_plan_evaluator_promotes_aggregation_buy_with_exit_policy_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="grid-1",
                family="manual_imported_micro_grid",
                side="Thunder",
                sleeve_id="okc-grid",
                sleeve_role="grid_scalp",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "side": "monitor",
                    "price": 0.2,
                    "size": 5,
                },
                exit_rules={"target_price": 0.24},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-1",
                            "signal_type": "buy",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-grid",
                            "strategy_id": "grid-1",
                            "strategy_family": "manual_imported_micro_grid",
                            "requested_shares": 5,
                            "max_price": 0.2,
                            "reason_codes": ["strategy_plan_intent_created"],
                        }
                    ]
                }
            }
        },
        dry_run=True,
    )

    assert result.intent_count == 1
    assert result.intents[0].side == "buy"
    assert result.intents[0].metadata["paired_lifecycle"]["declared_exit_policy"]["target_price"] == 0.24


def test_strategy_plan_evaluator_blocks_rebuy_without_sell_fill_evidence_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-1",
                            "signal_type": "rebuy",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-grid",
                            "strategy_id": "okc-grid",
                            "cycle_id": "cycle-okc",
                            "trigger_type": "paired_microcycle_next_leg",
                            "trigger_source": "paired_microcycle",
                            "requested_shares": 5,
                            "max_price": 0.2,
                            "reason_codes": ["manual_rebuy_review"],
                        }
                    ]
                }
            }
        },
        dry_run=True,
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "rebuy_requires_sell_fill_evidence"


def test_strategy_plan_evaluator_dedupes_live_aggregation_candidate_matching_plan_intent_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="grid-1",
                family="resistance_band_rebound_grid",
                side="Thunder",
                sleeve_id="okc-grid",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "side": "buy",
                    "price": 0.2,
                    "size": 5,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-1",
                            "signal_type": "buy",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-grid",
                            "strategy_id": "grid-1",
                            "strategy_family": "resistance_band_rebound_grid",
                            "requested_shares": 5,
                            "max_price": 0.2,
                        }
                    ]
                }
            }
        },
        dry_run=True,
    )

    assert result.intent_count == 1
    assert result.intents[0].reason == "strategy_plan_entry"
    assert result.blocked_count == 1
    assert result.blockers[0]["reason"] == "aggregation_candidate_duplicate_intent"


def test_strategy_plan_evaluator_blocks_stale_orderbook_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="grid-1",
                family="resistance_band_rebound_grid",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "price": 0.2,
                    "size": 5,
                    "max_orderbook_age_seconds": 3,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(plan, market_state={"orderbook_age_seconds": 4}, dry_run=True)

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "orderbook_stale"


def test_strategy_plan_evaluator_uses_scoreboard_capture_age_before_score_stall_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-sas-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="okc-ultra-low-adddown",
                family="ultra_low_underdog_add_down_grid",
                side="Thunder",
                budget_usd=3.0,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "side": "buy",
                    "price": 0.2,
                    "size": 5,
                    "price_band": [0.19, 0.21],
                    "max_scoreboard_age_seconds": 45,
                    "max_abs_score_gap": 25,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 1},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.2,
            "score_gap": -20,
            "scoreboard_age_seconds": 145,
            "scoreboard_captured_age_seconds": 4,
        },
    )

    assert result.intent_count == 1
    assert result.blocked_count == 0


def test_strategy_plan_evaluator_blocks_market_orders_and_too_small_buys_pytest() -> None:
    market_plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="market-order",
                family="grid",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "side": "buy",
                    "order_type": "market",
                    "price": 0.2,
                    "size": 5,
                },
            )
        ],
    )
    tiny_buy_plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="tiny-buy",
                family="grid",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "side": "buy",
                    "price": 0.1,
                    "size": 5,
                },
            )
        ],
    )

    market_result = evaluate_strategy_plan(market_plan)
    tiny_buy_result = evaluate_strategy_plan(tiny_buy_plan)

    assert market_result.intent_count == 0
    assert market_result.blockers[0]["reason"] == "market_orders_disabled"
    assert tiny_buy_result.intent_count == 0
    assert tiny_buy_result.blockers[0]["reason"] == "minimum_buy_notional_not_met"


def test_strategy_plan_evaluator_allows_low_notional_sell_targets_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-1",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="target-sell",
                family="bracket_exit",
                side="underdog",
                budget_usd=0.0,
                entry_rules={
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "side": "sell",
                    "price": 0.02,
                    "size": 5,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(plan)

    assert result.intent_count == 1
    assert result.intents[0].side == "sell"


def test_strategy_plan_evaluator_blocks_ultra_low_underdog_without_fresh_context_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-lal-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-low",
                family="underdog_range_scalp",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "price": 0.12,
                    "size": 10,
                    "price_band": [0.1, 0.18],
                },
                exit_rules={"target_price": 0.22},
                stop_rules={"stop_price": 0.08},
            )
        ],
    )

    result = evaluate_strategy_plan(plan, market_state={"price": 0.12})

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "ultra_low_underdog_guardrail"
    assert result.blockers[0]["missing_requirements"] == [
        "allow_ultra_low_underdog",
        "fresh_scoreboard",
        "score_gap_constraint",
    ]


def test_strategy_plan_evaluator_blocks_sub_10c_underdog_without_grid_opt_in_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-lal-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-below-10c",
                family="underdog_range_scalp",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "price": 0.08,
                    "size": 15,
                    "price_band": [0.01, 0.09],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 6,
                },
                exit_rules={"target_price": 0.18},
                stop_rules={"stop_price": 0.04},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.08, "score_gap": -3, "scoreboard_age_seconds": 1},
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "ultra_low_underdog_guardrail"
    assert result.blockers[0]["missing_requirements"] == ["allow_sub_10c_underdog_grid"]


def test_strategy_plan_evaluator_resolves_scaled_micro_grid_target_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-det-cle",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="det-micro-grid",
                family="underdog_micro_grid_reprice",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-det",
                    "token_id": "token-det",
                    "side": "buy",
                    "price": 0.20,
                    "size": 5,
                    "price_band": [0.19, 0.21],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 6,
                },
                exit_rules={
                    "target_required": True,
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 1,
                    "target_return_fraction": 0.10,
                },
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.20, "score_gap": -2, "scoreboard_age_seconds": 1},
    )

    assert result.intent_count == 1
    assert result.intents[0].metadata["exit_rules"]["target_price"] == 0.22
    assert result.intents[0].metadata["exit_rules"]["resolved_target_policy"] == "max_min_cents_or_return_fraction"


def test_strategy_plan_evaluator_resolves_subcent_scaled_micro_grid_target_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-okc-sas",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="okc-q4-subpenny-hype-bounce",
                family="ultra_low_underdog_decimal_grid",
                side="Thunder",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "side": "buy",
                    "price": 0.004,
                    "size": 253,
                    "price_band": [0.003, 0.014],
                    "allow_ultra_low_underdog": True,
                    "allow_sub_10c_underdog_grid": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 35,
                },
                exit_rules={
                    "target_required": True,
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 0.3,
                    "target_return_fraction": 0.50,
                    "min_target_price": 0.001,
                    "target_tick_size": 0.001,
                },
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.004, "score_gap": -20, "scoreboard_age_seconds": 1},
    )

    assert result.intent_count == 1
    assert result.intents[0].metadata["exit_rules"]["target_price"] == 0.007
    assert result.intents[0].metadata["exit_rules"]["resolved_target_policy"] == "max_min_cents_or_return_fraction"


def test_strategy_plan_evaluator_scales_ultra_low_aggregation_buy_to_min_notional_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-okc-sas",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="okc-q4-subpenny-hype-bounce",
                family="ultra_low_underdog_decimal_grid",
                side="Thunder",
                sleeve_id="okc-ultra-low",
                sleeve_role="ultra_low_rebound",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "price": 0.004,
                    "price_band": [0.003, 0.014],
                    "allow_ultra_low_underdog": True,
                    "allow_sub_10c_underdog_grid": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 35,
                    "sizing_mode": "minimum_notional",
                    "min_buy_notional_usd": 1.0,
                },
                exit_rules={
                    "target_required": True,
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 0.3,
                    "target_return_fraction": 0.50,
                    "min_target_price": 0.001,
                    "target_tick_size": 0.001,
                },
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.004,
            "score_gap": -20,
            "scoreboard_age_seconds": 1,
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-okc-sas",
                            "signal_type": "buy",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-ultra-low",
                            "sleeve_role": "ultra_low_rebound",
                            "strategy_id": "okc-q4-subpenny-hype-bounce",
                            "strategy_family": "ultra_low_underdog_decimal_grid",
                            "requested_shares": 5,
                            "max_price": 0.004,
                            "reason_codes": ["ultra_low_rebound_signal"],
                        }
                    ]
                }
            },
        },
    )

    assert result.intent_count == 1
    assert result.intents[0].size == 250
    assert result.intents[0].metadata["required_notional_usd"] == 1.0
    assert result.intents[0].metadata["sizing_policy"]["mode"] == "minimum_notional_ultra_low_sleeve"
    assert result.intents[0].metadata["sizing_policy"]["requested_size"] == 5


def test_strategy_plan_evaluator_blocks_ultra_low_aggregation_without_local_opt_in_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-okc-sas",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="okc-q4-subpenny-hype-bounce",
                family="ultra_low_underdog_decimal_grid",
                side="Thunder",
                sleeve_id="okc-ultra-low",
                sleeve_role="ultra_low_rebound",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "price": 0.004,
                    "sizing_mode": "minimum_notional",
                    "min_buy_notional_usd": 1.0,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 0.3},
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.004,
            "score_gap": -20,
            "scoreboard_age_seconds": 1,
            "live_signal_aggregation": {
                "decision": {
                    "order_intent_candidates": [
                        {
                            "event_id": "event-okc-sas",
                            "signal_type": "buy",
                            "side": "Thunder",
                            "market_id": "market-1",
                            "outcome_id": "outcome-okc",
                            "market_token_id": "token-okc",
                            "sleeve_id": "okc-ultra-low",
                            "sleeve_role": "ultra_low_rebound",
                            "strategy_id": "okc-q4-subpenny-hype-bounce",
                            "strategy_family": "ultra_low_underdog_decimal_grid",
                            "requested_shares": 5,
                            "max_price": 0.004,
                        }
                    ]
                }
            },
        },
    )

    assert result.intent_count == 0
    assert any(blocker["reason"] == "ultra_low_underdog_guardrail" for blocker in result.blockers)


def test_strategy_plan_evaluator_uses_current_ask_price_policy_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-lal-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-live-micro-grid",
                family="price_stability_micro_grid",
                side="Lakers",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "size": 5,
                    "price_policy": "current_ask",
                    "max_price": 0.24,
                    "price_band": [0.10, 0.24],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 6,
                },
                exit_rules={
                    "target_required": True,
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 1,
                    "target_return_fraction": 0.10,
                },
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.21,
            "best_bid": 0.20,
            "best_ask": 0.21,
            "score_gap": 1,
            "scoreboard_age_seconds": 1,
        },
    )

    assert result.intent_count == 1
    assert result.intents[0].price == 0.21
    assert result.intents[0].metadata["entry_rules"]["resolved_price_policy"] == "current_ask"
    assert result.intents[0].metadata["exit_rules"]["target_price"] == 0.231


def test_wnba_controlled_entry_fires_after_matching_grid_spread_blocker_pytest() -> None:
    plan = StrategyPlan(
        event_id="wnba-dal-nyl-2026-05-24",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="dallas-grid",
                family="price_stability_micro_grid",
                side="Dallas",
                budget_usd=10.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-dal",
                    "token_id": "token-dal",
                    "side": "buy",
                    "size": 5,
                    "price_policy": "current_ask",
                    "max_price": 0.45,
                    "price_band": [0.03, 0.45],
                    "max_spread_cents": 2,
                    "max_scoreboard_age_seconds": 45,
                    "max_orderbook_age_seconds": 45,
                    "max_abs_score_gap": 18,
                    "min_clock_remaining_seconds": 60,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 1},
                stop_rules={"max_adverse_cents": 3},
            ),
            ActiveStrategy(
                strategy_id="dallas-controlled-fill",
                family="wnba_controlled_min_size_entry_v1",
                side="Dallas",
                budget_usd=10.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-dal",
                    "token_id": "token-dal",
                    "side": "buy",
                    "size": 5,
                    "price_policy": "current_ask",
                    "max_price": 0.45,
                    "price_band": [0.03, 0.45],
                    "max_spread_cents": 6,
                    "max_scoreboard_age_seconds": 45,
                    "max_orderbook_age_seconds": 45,
                    "max_abs_score_gap": 18,
                    "min_clock_remaining_seconds": 60,
                    "controlled_entry_requires_grid_spread_blocker": True,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 2},
                stop_rules={"max_adverse_cents": 4},
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "best_bid": 0.18,
            "best_ask": 0.22,
            "price": 0.22,
            "spread_cents": 4,
            "score_gap": 2,
            "period": 2,
            "clock": "PT05M00.00S",
            "scoreboard_age_seconds": 3,
            "orderbook_age_seconds": 2,
        },
        portfolio_state={"open_positions": 0, "open_orders": 0, "pending_intents": 0},
    )

    assert result.intent_count == 1
    assert result.intents[0].strategy_id == "dallas-controlled-fill"
    assert result.intents[0].strategy_family == "wnba_controlled_min_size_entry_v1"
    assert result.intents[0].price == 0.22
    assert result.blockers[0]["strategy_id"] == "dallas-grid"
    assert result.blockers[0]["reason"] == "orderbook_spread_too_wide"


def test_wnba_controlled_entry_caps_event_to_one_candidate_pytest() -> None:
    plan = StrategyPlan(
        event_id="wnba-dal-nyl-2026-05-24",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="dallas-grid",
                family="price_stability_micro_grid",
                side="Dallas",
                entry_rules={
                    "outcome_id": "outcome-dal",
                    "token_id": "token-dal",
                    "side": "buy",
                    "size": 5,
                    "price": 0.22,
                    "max_spread_cents": 2,
                },
            ),
            ActiveStrategy(
                strategy_id="dallas-controlled-fill",
                family="wnba_controlled_min_size_entry_v1",
                side="Dallas",
                budget_usd=10.0,
                entry_rules={
                    "outcome_id": "outcome-dal",
                    "token_id": "token-dal",
                    "side": "buy",
                    "size": 5,
                    "price": 0.22,
                    "max_spread_cents": 6,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 2},
                stop_rules={"max_adverse_cents": 4},
            ),
            ActiveStrategy(
                strategy_id="new-york-grid",
                family="price_stability_micro_grid",
                side="New York",
                entry_rules={
                    "outcome_id": "outcome-nyl",
                    "token_id": "token-nyl",
                    "side": "buy",
                    "size": 5,
                    "price": 0.22,
                    "max_spread_cents": 2,
                },
            ),
            ActiveStrategy(
                strategy_id="new-york-controlled-fill",
                family="wnba_controlled_min_size_entry_v1",
                side="New York",
                budget_usd=10.0,
                entry_rules={
                    "outcome_id": "outcome-nyl",
                    "token_id": "token-nyl",
                    "side": "buy",
                    "size": 5,
                    "price": 0.22,
                    "max_spread_cents": 6,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 2},
                stop_rules={"max_adverse_cents": 4},
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"spread_cents": 4},
        portfolio_state={"open_positions": 0, "open_orders": 0, "pending_intents": 0},
    )

    assert result.intent_count == 1
    assert result.intents[0].strategy_id == "dallas-controlled-fill"
    assert any(blocker["reason"] == "controlled_entry_event_limit_reached" for blocker in result.blockers)


def test_wnba_controlled_entry_augments_three_2026_05_24_plans_pytest() -> None:
    for path in WNBA_2026_05_24_PLAN_PATHS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["valid_until_utc"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        grid = payload["active_strategies"][0]
        controlled = {
            **grid,
            "strategy_id": f"{grid['strategy_id']}-controlled-fill",
            "family": "wnba_controlled_min_size_entry_v1",
            "sleeve_id": f"{grid['sleeve_id']}-controlled-fill",
            "sleeve_role": "controlled_fill",
            "entry_rules": {
                **grid["entry_rules"],
                "max_spread_cents": 6.0,
                "controlled_entry_requires_grid_spread_blocker": True,
                "reason": "controlled_fill_test_after_grid_spread_block",
            },
            "exit_rules": {"target_policy": "micro_grid_scaled", "min_target_cents": 2.0, "target_return_fraction": 0.10},
            "stop_rules": {"max_adverse_cents": 4.0},
        }
        payload["active_strategies"] = [grid, controlled]
        plan = StrategyPlan.model_validate(payload)

        result = evaluate_strategy_plan(
            plan,
            market_state={
                "best_bid": 0.18,
                "best_ask": 0.22,
                "price": 0.22,
                "spread_cents": 4.0,
                "score_gap": 2,
                "period": 2,
                "clock": "PT05M00.00S",
                "scoreboard_age_seconds": 3,
                "orderbook_age_seconds": 2,
            },
            portfolio_state={"open_positions": 0, "open_orders": 0, "pending_intents": 0},
        )

        assert result.intent_count == 1, path
        assert result.intents[0].strategy_family == "wnba_controlled_min_size_entry_v1"
        assert result.blockers[0]["reason"] == "orderbook_spread_too_wide"


def test_strategy_plan_evaluator_blocks_dynamic_price_above_max_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-lal-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-live-micro-grid",
                family="price_stability_micro_grid",
                side="Lakers",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "size": 5,
                    "price_policy": "current_ask",
                    "max_price": 0.24,
                    "price_band": [0.10, 0.30],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 6,
                },
                exit_rules={"target_price": 0.27},
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.25,
            "best_bid": 0.24,
            "best_ask": 0.25,
            "score_gap": 1,
            "scoreboard_age_seconds": 1,
        },
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "dynamic_price_above_max"


def test_operator_minimum_buy_size_uses_notional_buffer_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-lal-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-live-micro-grid",
                family="price_stability_micro_grid",
                side="Lakers",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "price_policy": "current_ask",
                    "max_price": 0.35,
                    "price_band": [0.10, 0.35],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 8,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "target_return_fraction": 0.10},
                stop_rules={"max_adverse_cents": 3},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.18,
            "best_bid": 0.17,
            "best_ask": 0.18,
            "score_gap": -3,
            "scoreboard_age_seconds": 1,
        },
        portfolio_state={
            "operator_sizing_policy": {
                "mode": "operator_minimum_order",
                "min_size": 5,
                "min_buy_notional_usd": 1,
                "share_precision": 3,
            }
        },
    )

    assert result.intent_count == 1
    assert result.intents[0].size == 5.612
    assert result.intents[0].metadata["required_notional_usd"] == 1.01016
    assert result.intents[0].metadata["sizing_policy"]["effective_min_buy_notional_usd"] == 1.01


def test_operator_policy_can_respect_plan_size_with_notional_cap_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-sas-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="sas-core-hold",
                family="core_hold_live_validation",
                side="Spurs",
                budget_usd=10.0,
                entry_rules={
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "size": 10,
                    "size_policy": "plan_size",
                    "price_policy": "current_ask",
                    "max_price": 0.45,
                    "price_band": [0.05, 0.45],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 12,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 1},
                stop_rules={"max_adverse_cents": 4},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.31,
            "best_bid": 0.30,
            "best_ask": 0.31,
            "score_gap": -4,
            "scoreboard_age_seconds": 1,
        },
        portfolio_state={
            "operator_sizing_policy": {
                "mode": "operator_minimum_order",
                "min_size": 5,
                "min_buy_notional_usd": 1,
                "max_buy_notional_usd": 10,
                "share_precision": 3,
            }
        },
    )

    assert result.intent_count == 1
    assert result.intents[0].size == 10
    assert result.intents[0].metadata["required_notional_usd"] == 3.1
    assert result.intents[0].metadata["sizing_policy"]["respect_plan_size"] is True


def test_operator_policy_blocks_plan_size_above_buy_notional_cap_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-sas-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="sas-oversized-core",
                family="core_hold_live_validation",
                side="Spurs",
                budget_usd=20.0,
                entry_rules={
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "size": 25,
                    "size_policy": "plan_size",
                    "price_policy": "current_ask",
                    "max_price": 0.45,
                    "price_band": [0.05, 0.45],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 12,
                },
                exit_rules={"target_policy": "micro_grid_scaled", "min_target_cents": 1},
                stop_rules={"max_adverse_cents": 4},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.41,
            "best_bid": 0.40,
            "best_ask": 0.41,
            "score_gap": -4,
            "scoreboard_age_seconds": 1,
        },
        portfolio_state={
            "operator_sizing_policy": {
                "mode": "operator_minimum_order",
                "min_size": 5,
                "min_buy_notional_usd": 1,
                "max_buy_notional_usd": 10,
                "share_precision": 3,
            }
        },
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "operator_sizing_notional_exceeded"
    assert result.blockers[0]["required_notional_usd"] == 10.25


def test_strategy_plan_evaluator_allows_sub_10c_explicit_micro_grid_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-det-cle",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="det-sub-10c-grid",
                family="underdog_micro_grid_reprice",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-det",
                    "token_id": "token-det",
                    "side": "buy",
                    "price": 0.05,
                    "size": 20,
                    "price_band": [0.005, 0.10],
                    "allow_ultra_low_underdog": True,
                    "allow_sub_10c_underdog_grid": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 16,
                },
                exit_rules={
                    "target_required": True,
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 1,
                    "target_return_fraction": 0.10,
                },
                stop_rules={"max_adverse_cents": 2},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.05,
            "score_gap": -12,
            "scoreboard_age_seconds": 145,
            "scoreboard_captured_age_seconds": 4,
        },
    )

    assert result.intent_count == 1
    assert result.intents[0].metadata["exit_rules"]["target_price"] == 0.06


def test_strategy_plan_evaluator_allows_explicit_low_underdog_with_protection_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-det-cle",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="det-low-guarded",
                family="underdog_range_scalp",
                side="underdog",
                budget_usd=2.0,
                entry_rules={
                    "outcome_id": "outcome-det",
                    "token_id": "token-det",
                    "side": "buy",
                    "price": 0.15,
                    "size": 10,
                    "price_band": [0.1, 0.18],
                    "allow_ultra_low_underdog": True,
                    "max_scoreboard_age_seconds": 5,
                    "max_abs_score_gap": 6,
                },
                exit_rules={"target_price": 0.27},
                stop_rules={"stop_price": 0.11},
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.15, "score_gap": -2, "scoreboard_age_seconds": 1},
    )

    assert result.intent_count == 1
    assert result.blocked_count == 0
    assert result.intents[0].price == 0.15


def test_strategy_plan_evaluator_blocks_pending_buy_intent_before_direct_mirror_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-det-cle",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="det-grid-1",
                family="resistance_band_rebound_grid",
                side="underdog",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-det",
                    "token_id": "token-det",
                    "side": "buy",
                    "price": 0.22,
                    "size": 5,
                    "price_band": [0.2, 0.25],
                },
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.22},
        portfolio_state={
            "open_positions": 0,
            "open_orders": 0,
            "pending_intent_orders": [
                {
                    "strategy_id": "det-grid-1",
                    "outcome_id": "outcome-det",
                    "side": "buy",
                    "status": "submitted",
                }
            ],
        },
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "pending_intent_limit_reached"
    assert result.blockers[0]["pending_intents"] == 1.0
    assert result.blockers[0]["direct_unresolved_exposure"] == 0.0
    assert result.blockers[0]["unresolved_exposure"] == 1.0


def test_strategy_plan_evaluator_blocks_buy_after_player_status_shock_pytest() -> None:
    plan = StrategyPlan(
        event_id="nba-sas-min-2026-05-10",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="sas-favorite-floor-rebound-v2",
                family="favorite_floor_rebound",
                side="Spurs",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "price": 0.18,
                    "size": 6,
                    "price_band": [0.1, 0.2],
                    "requires_wembanyama_available": True,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.18,
            "player_status_shocks": [
                {
                    "player_name": "Victor Wembanyama",
                    "event_index": 179,
                    "tags": ["flagrant_type_2", "ejection", "feed_status_conflict"],
                    "requires_strategy_plan_revision": True,
                }
            ],
            "player_status_shock_count": 1,
        },
        portfolio_state={"open_positions": 0, "open_orders": 0},
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "player_status_shock_revision_required"
    assert result.blockers[0]["shock_count"] == 1
    assert result.blockers[0]["shock_tags"] == ["ejection", "feed_status_conflict", "flagrant_type_2"]
    assert result.blockers[0]["player_names"] == ["Victor Wembanyama"]
    assert result.blockers[0]["requires_strategy_plan_revision"] is True


def test_strategy_plan_evaluator_allows_explicit_post_shock_revision_pytest() -> None:
    plan = StrategyPlan(
        event_id="nba-sas-min-2026-05-10",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="min-post-shock-reopen-v1",
                family="resistance_band_rebound_grid",
                side="Timberwolves",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-min",
                    "token_id": "token-min",
                    "side": "buy",
                    "price": 0.34,
                    "size": 5,
                    "price_band": [0.3, 0.4],
                    "fresh_strategy_plan_after_player_status_shock": True,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "price": 0.34,
            "player_status_shocks": [
                {
                    "player_name": "Victor Wembanyama",
                    "tags": ["ejection"],
                    "requires_strategy_plan_revision": True,
                }
            ],
            "player_status_shock_count": 1,
        },
        portfolio_state={"open_positions": 0, "open_orders": 0},
    )

    assert result.intent_count == 1
    assert result.blocked_count == 0


def test_strategy_plan_evaluator_blocks_outside_period_window_pytest() -> None:
    plan = StrategyPlan(
        event_id="nba-okc-lal-2026-05-11",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-q1-q2-momentum-scalp-v1",
                family="underdog_momentum_scalp",
                side="Lakers",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "price": 0.2,
                    "size": 5,
                    "price_band": [0.17, 0.25],
                    "max_period": 2,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.2, "period": 3},
        portfolio_state={"open_positions": 0, "open_orders": 0},
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "period_outside_rule"
    assert result.blockers[0]["period"] == 3
    assert result.blockers[0]["max_period"] == 2


def test_strategy_plan_evaluator_blocks_inside_no_entry_clock_window_pytest() -> None:
    plan = StrategyPlan(
        event_id="nba-okc-lal-2026-05-11",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="okc-q4-continuation-v1",
                family="favorite_continuation_micro_grid",
                side="Thunder",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "side": "buy",
                    "price": 0.72,
                    "size": 5,
                    "price_band": [0.7, 0.82],
                    "min_period": 3,
                    "max_period": 4,
                    "min_clock_remaining_seconds": 120,
                },
            )
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={"price": 0.72, "period": 4, "game_clock": "PT00M32.80S"},
        portfolio_state={"open_positions": 0, "open_orders": 0},
    )

    assert result.intent_count == 0
    assert result.blockers[0]["reason"] == "clock_inside_no_entry_window"
    assert result.blockers[0]["clock_remaining_seconds"] == 32.8
    assert result.blockers[0]["min_clock_remaining_seconds"] == 120


def test_strategy_plan_evaluator_reports_sleeve_states_and_garbage_time_blocks_one_sleeve_pytest() -> None:
    plan = StrategyPlan(
        event_id="nba-okc-lal-2026-05-11",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="lal-late-default-grid-v1",
                family="close_game_micro_grid",
                side="Lakers",
                sleeve_id="lal-late-default",
                sleeve_group="lal",
                sleeve_role="standard_entry",
                budget_usd=5.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-lal",
                    "token_id": "token-lal",
                    "side": "buy",
                    "price": 0.22,
                    "size": 5,
                    "price_band": [0.2, 0.25],
                },
            ),
            ActiveStrategy(
                strategy_id="okc-reviewed-q4-clutch-v1",
                family="q4_clutch_micro_grid",
                side="Thunder",
                sleeve_id="okc-q4-clutch",
                sleeve_group="okc",
                sleeve_role="reviewed_q4_clutch",
                budget_usd=5.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "side": "buy",
                    "price": 0.72,
                    "size": 5,
                    "price_band": [0.7, 0.82],
                    "allow_garbage_time_entry": True,
                },
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "garbage_time": True,
            "strategy_states": {
                "lal-late-default-grid-v1": {"price": 0.22},
                "okc-reviewed-q4-clutch-v1": {"price": 0.72},
            },
        },
        portfolio_state={"open_positions": 0, "open_orders": 0},
    )

    assert result.intent_count == 1
    assert result.blocked_count == 1
    assert result.blockers[0]["reason"] == "garbage_time_no_new_entry"
    assert result.blockers[0]["sleeve_id"] == "lal-late-default"
    assert result.intents[0].strategy_id == "okc-reviewed-q4-clutch-v1"
    assert result.intents[0].sleeve_id == "okc-q4-clutch"
    assert result.intents[0].metadata["sleeve"]["sleeve_role"] == "reviewed_q4_clutch"
    assert [
        (state["sleeve_id"], state["status"], state["blocker_reasons"], state["intent_count"])
        for state in result.sleeve_states
    ] == [
        ("lal-late-default", "blocked", ["garbage_time_no_new_entry"], 0),
        ("okc-q4-clutch", "intent_created", [], 1),
    ]


def test_strategy_score_gap_gate_blocks_only_that_sleeve_pytest() -> None:
    plan = StrategyPlan(
        event_id="event-sas-okc",
        market_id="market-1",
        active_strategies=[
            ActiveStrategy(
                strategy_id="sas-close-game-only",
                family="score_gap_sensitive_grid",
                side="Spurs",
                sleeve_id="sas-close-gap",
                sleeve_group="sas",
                sleeve_role="score_gap_sensitive",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "price": 0.24,
                    "size": 5,
                    "price_band": [0.2, 0.3],
                    "max_abs_score_gap": 4,
                },
            ),
            ActiveStrategy(
                strategy_id="sas-band-grid",
                family="price_stability_micro_grid",
                side="Spurs",
                sleeve_id="sas-band-grid",
                sleeve_group="sas",
                sleeve_role="price_band_only",
                budget_usd=2.0,
                max_positions=1,
                entry_rules={
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "price": 0.24,
                    "size": 5,
                    "price_band": [0.2, 0.3],
                },
            ),
        ],
    )

    result = evaluate_strategy_plan(
        plan,
        market_state={
            "score_gap": 8,
            "strategy_states": {
                "sas-close-game-only": {"price": 0.24},
                "sas-band-grid": {"price": 0.24},
            },
        },
        portfolio_state={"open_positions": 0, "open_orders": 0},
    )

    assert result.intent_count == 1
    assert result.blocked_count == 1
    assert result.blockers[0]["reason"] == "score_gap_outside_rule"
    assert result.blockers[0]["sleeve_id"] == "sas-close-gap"
    assert result.intents[0].strategy_id == "sas-band-grid"
