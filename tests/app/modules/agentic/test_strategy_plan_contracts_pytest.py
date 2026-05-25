from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.modules.agentic.contracts import ActiveStrategy, StrategyPlan
from app.modules.agentic.engine import evaluate_strategy_plan


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
        market_state={"price": 0.05, "score_gap": -12, "scoreboard_age_seconds": 1},
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
