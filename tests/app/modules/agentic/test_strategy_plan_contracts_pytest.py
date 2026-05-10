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
