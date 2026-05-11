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


def test_strategy_plan_evaluator_blocks_sub_10c_underdog_as_manual_only_pytest() -> None:
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
    assert result.blockers[0]["reason"] == "ultra_low_underdog_manual_only"


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
