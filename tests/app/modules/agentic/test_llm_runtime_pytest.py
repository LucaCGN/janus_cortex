from __future__ import annotations

from app.modules.agentic.contracts import LLMRevisionRequest, LLMRuntimeTrigger
from app.modules.agentic.llm_runtime import (
    FRONTIER_MODEL,
    MINI_MODEL,
    build_llm_prompt_contract,
    build_llm_revision_request,
    detect_llm_runtime_triggers,
    route_llm_model,
)


def test_quarter_end_emits_runtime_trigger_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-det-cle-2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
        source="pytest",
    )

    assert [trigger.trigger_type for trigger in triggers] == ["quarter_end"]
    assert triggers[0].source == "pytest"
    assert triggers[0].current_plan_stale_reason == "app_owned_live_revision_trigger_detected"


def test_manual_operator_exposure_routes_to_frontier_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-sas-min-2026-05-10",
        operator_interventions=[
            {
                "action": "adopt_operator_position",
                "token_id": "token-sas",
                "position_size": 5,
                "current_position": {"avg_price": 0.6},
            }
        ],
        portfolio_state={"open_positions": 1},
    )

    decision = route_llm_model(triggers, portfolio_state={"open_positions": 1})

    assert [trigger.trigger_type for trigger in triggers] == ["manual_operator_position"]
    assert decision.selected_model == FRONTIER_MODEL
    assert decision.selected_tier == "frontier"
    assert "manual_operator_position" in decision.critical_reasons
    assert "open_exposure" in decision.critical_reasons


def test_player_status_shock_routes_to_frontier_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-sas-min-2026-05-10",
        pbp_shocks=[
            {
                "player_name": "Victor Wembanyama",
                "tags": ["ejection", "flagrant_type_2"],
                "requires_strategy_plan_revision": True,
            }
        ],
    )

    decision = route_llm_model(triggers)

    assert [trigger.trigger_type for trigger in triggers] == ["player_status_shock"]
    assert decision.selected_model == FRONTIER_MODEL
    assert "player_status_shock" in decision.critical_reasons


def test_order_submitted_and_fill_emit_lifecycle_triggers_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-det-cle-2026-05-11",
        portfolio_state={
            "pending_intent_orders": [
                {"order_id": "local-1", "status": "submitted", "side": "buy"},
            ],
            "recent_order_events": [
                {"order_id": "local-1", "event_type": "fill", "status": "filled"},
            ],
        },
    )

    assert [trigger.trigger_type for trigger in triggers] == ["janus_order_submitted", "order_fill"]


def test_no_position_no_shock_state_emits_no_trigger_and_mini_route_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-det-cle-2026-05-11",
        live_state={"period": 1, "clock": "9:42"},
        portfolio_state={"open_positions": 0, "open_orders": 0, "pending_intents": 0},
    )
    decision = route_llm_model(triggers, portfolio_state={"open_positions": 0, "open_orders": 0})

    assert triggers == []
    assert decision.selected_model == MINI_MODEL
    assert decision.trigger_ids == []


def test_ml_pbp_undervaluation_schema_emits_placeholder_trigger_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-okc-lal-2026-05-11",
        ml_pbp_evidence={
            "valuation_signal": "undervalued",
            "outcome_id": "outcome-lal",
            "token_id": "token-lal",
            "pbp_tags": ["star_sub_out", "score_gap_closing"],
            "edge_probability": 0.58,
        },
    )

    assert len(triggers) == 1
    trigger = triggers[0]
    assert isinstance(trigger, LLMRuntimeTrigger)
    assert trigger.trigger_type == "ml_pbp_undervaluation"
    assert trigger.evidence["edge_probability"] == 0.58


def test_prompt_contract_and_revision_request_include_safety_sections_pytest() -> None:
    trigger = detect_llm_runtime_triggers(
        event_id="nba-det-cle-2026-05-11",
        live_state={"quarter_end": True, "period": 2},
    )[0]
    routing = route_llm_model([trigger])
    request = build_llm_revision_request(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        triggers=[trigger],
        model_routing=routing,
        current_plan={
            "event_id": "nba-det-cle-2026-05-11",
            "market_id": "market-1",
            "active_strategies": [{"strategy_id": "det-grid", "family": "grid", "side": "Pistons"}],
        },
        direct_clob_truth={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        portfolio_state={"operator_sizing_policy": {"mode": "operator_minimum_order", "min_size": 5}},
    )
    prompt_contract = build_llm_prompt_contract()

    assert isinstance(request, LLMRevisionRequest)
    assert request.prompt_contract["safety_rule"] == "No order endpoint calls are allowed from LLM output or prompt tools."
    assert "direct_clob_truth" in request.prompt_contract["required_input_sections"]
    assert "operator_sizing_policy" in request.prompt_contract["required_input_sections"]
    assert prompt_contract["required_json_output_schema"]["revised_strategy_plan"] == "StrategyPlanJSON or null"
    assert prompt_contract["authority_boundaries"][0] == "The LLM never calls order endpoints."
