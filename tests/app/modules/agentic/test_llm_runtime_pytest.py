from __future__ import annotations

import json
from types import SimpleNamespace

from app.modules.agentic.contracts import LLMRevisionRequest, LLMRuntimeTrigger
from app.modules.agentic.llm_runtime import (
    FRONTIER_MODEL,
    MINI_MODEL,
    _build_live_revision_system_prompt,
    build_current_event_inventory_proof,
    build_llm_prompt_contract,
    build_llm_revision_request,
    build_llm_runtime_trace,
    detect_llm_runtime_triggers,
    load_latest_llm_runtime_status,
    process_llm_runtime_trace,
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


def test_quarter_end_reviewed_by_current_plan_does_not_require_revision_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-det-cle-2026-05-11",
        current_plan={"generated_at_utc": "2026-05-12T02:06:40Z"},
        live_state={
            "recent_play_by_play": [
                {
                    "event_index": 398,
                    "period": 3,
                    "clock": "PT00M00.00S",
                    "description": "Period End",
                    "payload_json": {"edited": "2026-05-12T02:00:51Z", "subType": "end"},
                }
            ]
        },
        source="pytest",
    )

    assert [trigger.trigger_type for trigger in triggers] == ["quarter_end"]
    assert triggers[0].requires_revision is False
    assert triggers[0].current_plan_stale_reason is None
    assert triggers[0].evidence["reviewed_by_current_plan"] is True


def test_quarter_end_review_marker_suppresses_same_period_later_snapshot_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-okc-lal-2026-05-11",
        current_plan={
            "generated_at_utc": "2026-05-12T00:10:00Z",
            "context_summary": {
                "q1_quarter_end_reviewed_utc": "2026-05-12T00:20:00Z",
                "q1_quarter_end_reviewed": "Operator reviewed Q1 and kept the micro-grid active.",
            },
        },
        live_state={
            "period": 1,
            "clock": "0:00",
            "latest_snapshot": {
                "period": 1,
                "game_clock": "PT00M00.00S",
                "captured_at_utc": "2026-05-12T00:21:00Z",
            },
        },
        source="pytest",
    )

    assert [trigger.trigger_type for trigger in triggers] == ["quarter_end"]
    assert triggers[0].requires_revision is False
    assert triggers[0].current_plan_stale_reason is None
    assert triggers[0].reason == "Quarter boundary reviewed by current StrategyPlanJSON."
    assert triggers[0].evidence["quarter_end_reviewed_by_plan"] is True


def test_fresh_q3_plan_revision_trigger_fires_once_pytest() -> None:
    plan = {
        "active_strategies": [
            {
                "strategy_id": "halftime-watch",
                "revision_triggers": [{"type": "fresh_q3_state_after_halftime"}],
            }
        ]
    }
    live_state = {"latest_snapshot": {"period": 3, "clock": "PT07M30.00S", "home_score": 64, "away_score": 59}}

    triggers = detect_llm_runtime_triggers(
        event_id="nba-cle-det-2026-05-13",
        current_plan=plan,
        live_state=live_state,
        source="pytest",
    )
    reviewed_triggers = detect_llm_runtime_triggers(
        event_id="nba-cle-det-2026-05-13",
        current_plan={
            **plan,
            "explainability": {"fresh_q3_state_after_halftime_reviewed_utc": "2026-05-14T01:45:00Z"},
        },
        live_state=live_state,
        source="pytest",
    )

    assert [trigger.trigger_type for trigger in triggers] == ["strategy_plan_revision_trigger"]
    assert triggers[0].requires_revision is True
    assert triggers[0].evidence["semantic_trigger_type"] == "fresh_q3_state_after_halftime"
    assert triggers[0].evidence["seconds_remaining"] == 450
    assert reviewed_triggers == []


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


def test_critical_trigger_downgrades_frontier_when_budget_or_min_size_context_blocks_pytest() -> None:
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
        portfolio_state={"open_positions": 1, "open_exposure_usd": 2.0},
    )

    decision = route_llm_model(
        triggers,
        portfolio_state={"open_positions": 1, "open_exposure_usd": 2.0},
        budget_state={
            "warning": True,
            "available_openai_budget_usd": 0.25,
            "frontier_min_available_budget_usd": 1.0,
        },
        bankroll_state={
            "minimum_size_testing": True,
            "open_exposure_usd": 2.0,
            "frontier_min_exposure_usd": 10.0,
        },
    )

    assert decision.selected_model == MINI_MODEL
    assert decision.selected_tier == "mini"
    assert decision.fallback_alias == FRONTIER_MODEL
    assert "frontier_downgraded_by_budget_warning" in decision.critical_reasons
    assert "frontier_blocked_by_available_openai_budget" in decision.critical_reasons
    assert "frontier_downgraded_min_size_low_exposure" in decision.critical_reasons


def test_position_adverse_move_routes_to_frontier_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-okc-lal-2026-05-11",
        operator_interventions=[
            {
                "action": "position_adverse_move",
                "token_id": "token-lal",
                "avg_price": 0.19,
                "current_exit_bid": 0.10,
                "triggered_rules": [{"rule": "max_adverse_cents", "adverse_cents": 9.0}],
            }
        ],
        portfolio_state={"open_positions": 1},
    )

    decision = route_llm_model(triggers, portfolio_state={"open_positions": 1})

    assert [trigger.trigger_type for trigger in triggers] == ["position_adverse_move"]
    assert triggers[0].requires_revision is True
    assert decision.selected_model == FRONTIER_MODEL
    assert "position_adverse_move" in decision.critical_reasons
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


def test_price_flip_and_leadership_switch_emit_frontier_triggers_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-okc-lal-2026-05-11",
        live_state={
            "leadership_switch": {
                "previous_leader": "LAL",
                "current_leader": "OKC",
                "home_score": 88,
                "away_score": 87,
            },
        },
        orderbook_state={
            "price_flip": {
                "token_id": "token-okc",
                "previous_mid": 0.48,
                "current_mid": 0.53,
                "crossed": "0.50",
            }
        },
    )

    decision = route_llm_model(triggers)

    assert [trigger.trigger_type for trigger in triggers] == ["price_flip", "leadership_switch"]
    assert decision.selected_model == FRONTIER_MODEL
    assert "price_flip" in decision.critical_reasons
    assert "leadership_switch" in decision.critical_reasons


def test_recent_run_and_garbage_time_emit_runtime_triggers_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-okc-lal-2026-05-11",
        live_state={
            "recent_run": {
                "team": "LAL",
                "points_for": 10,
                "points_against": 0,
                "window_seconds": 120,
            },
            "period": 4,
            "clock": "1:35",
            "score_gap": 18,
        },
    )

    decision = route_llm_model(triggers, live_state={"period": 4, "clock": "1:35", "score_gap": 18})

    assert [trigger.trigger_type for trigger in triggers] == ["recent_run", "garbage_time"]
    assert triggers[0].severity == "routine"
    assert triggers[1].severity == "critical"
    assert triggers[1].evidence["computed"] is True
    assert decision.selected_model == FRONTIER_MODEL
    assert "garbage_time" in decision.critical_reasons


def test_score_gap_break_emits_runtime_trigger_from_plan_rules_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-okc-lal-2026-05-11",
        current_plan={
            "active_strategies": [
                {
                    "strategy_id": "lal-close-game-micro-grid",
                    "family": "micro_grid_reprice_v1",
                    "side": "Lakers",
                    "sleeve_id": "lal-q4-close",
                    "entry_rules": {"max_abs_score_gap": 8},
                }
            ]
        },
        live_state={"period": 3, "clock": "4:12", "score_gap": 12},
    )

    routine_decision = route_llm_model(triggers)
    exposure_decision = route_llm_model(triggers, portfolio_state={"open_positions": 1})

    assert [trigger.trigger_type for trigger in triggers] == ["score_gap_break"]
    assert triggers[0].severity == "routine"
    assert triggers[0].evidence["computed"] is True
    assert triggers[0].evidence["score_gap"] == 12.0
    assert triggers[0].evidence["broken_strategy_count"] == 1
    assert triggers[0].evidence["broken_strategies"][0]["strategy_id"] == "lal-close-game-micro-grid"
    assert routine_decision.selected_model == MINI_MODEL
    assert exposure_decision.selected_model == FRONTIER_MODEL
    assert "open_exposure" in exposure_decision.critical_reasons


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


def test_target_lifecycle_events_emit_specific_runtime_triggers_pytest() -> None:
    triggers = detect_llm_runtime_triggers(
        event_id="nba-sas-min-2026-05-10",
        portfolio_state={
            "recent_order_events": [
                {
                    "order_id": "target-fill-1",
                    "event_type": "fill",
                    "status": "filled",
                    "order_role": "protective_target",
                },
                {
                    "order_id": "target-cancel-1",
                    "event_type": "cancel",
                    "status": "canceled",
                    "metadata": {"purpose": "target_exit"},
                },
                {
                    "order_id": "target-error-1",
                    "event_type": "submit_error",
                    "status": "submit_error",
                    "order_role": "protective_target",
                    "error": "CLOB balance/allowance does not cover target placement",
                },
            ],
            "missing_protection": True,
        },
    )

    decision = route_llm_model(triggers, portfolio_state={"missing_protection": True})

    assert [trigger.trigger_type for trigger in triggers] == [
        "target_fill",
        "target_cancel",
        "target_placement_failed",
    ]
    assert triggers[0].severity == "routine"
    assert triggers[1].severity == "critical"
    assert triggers[2].severity == "critical"
    assert decision.selected_model == FRONTIER_MODEL
    assert "target_placement_failed" in decision.critical_reasons
    assert "missing_protection_or_stop_hedge" in decision.critical_reasons


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
    assert "current_event_inventory_proof" in request.prompt_contract["required_input_sections"]
    assert request.direct_clob_truth["current_event_inventory_proof"]["status"] == "included"
    assert request.portfolio_state["current_event_inventory_proof"]["open_order_count"] == 0
    assert prompt_contract["required_json_output_schema"]["revised_strategy_plan"] == "StrategyPlanJSON or null"
    assert prompt_contract["authority_boundaries"][0] == "The LLM never calls order endpoints."
    assert "Marketable loss exits are reserved for virtual-dead states" in prompt_contract["experiment_policy"]
    assert "loss_exit_requires_virtual_dead=true" in _build_live_revision_system_prompt(request)


def test_current_event_inventory_proof_summarizes_direct_scope_pytest() -> None:
    proof = build_current_event_inventory_proof(
        direct_clob_truth={
            "event_token_ids": ["token-a", "token-b"],
            "open_orders": {"orders": [{"id": "order-1"}], "event_scoped": True},
            "open_positions": {"positions": [{"asset": "token-a"}], "event_scoped": True},
        },
        portfolio_state={
            "pending_intents": 1,
            "submitted_orders": [{"id": "submitted-1"}],
            "direct_clob_trade_observation_count": 2,
            "pending_intent_source": "portfolio_orders",
        },
    )

    assert proof["status"] == "included"
    assert proof["event_scoped"] is True
    assert proof["event_token_count"] == 2
    assert proof["open_order_count"] == 1
    assert proof["open_position_count"] == 1
    assert proof["pending_intent_count"] == 1
    assert proof["submitted_order_count"] == 1
    assert proof["unresolved_inventory_count"] == 4
    assert proof["unresolved_inventory_present"] is True


def test_quarter_end_runtime_trace_persists_to_artifact_pytest(tmp_path) -> None:
    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
        source="pytest",
    )

    trace, persistence = process_llm_runtime_trace(
        trace,
        dispatch_enabled=False,
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert trace.status == "skipped_unavailable"
    assert trace.revision_response is not None
    assert trace.revision_response.skipped_reason == "dispatch_disabled"
    assert persistence["status"] == "persisted"
    payload = json.loads((tmp_path / "llm-runtime" / "2026-05-11").glob("*.json").__next__().read_text())
    assert payload["event_id"] == "nba-det-cle-2026-05-11"
    assert payload["trigger_types"] == ["quarter_end"]
    assert payload["model_routing_decision"]["selected_model"] == MINI_MODEL
    assert payload["prompt_payload"]["event_id"] == "nba-det-cle-2026-05-11"
    assert payload["response"]["status"] == "skipped_unavailable"


def test_missing_openai_key_records_skipped_unavailable_pytest(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JANUS_TEST_OPENAI_KEY", raising=False)
    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
    )

    trace, persistence = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
        api_key_env="JANUS_TEST_OPENAI_KEY",
    )

    assert persistence["ok"] is True
    assert trace.status == "skipped_unavailable"
    assert trace.revision_response is not None
    assert trace.revision_response.skipped_reason == "janus_test_openai_key_missing"
    assert trace.revision_response.trace_metadata["openai_call_attempted"] is False


def test_reviewed_runtime_trigger_does_not_call_openai_pytest(tmp_path) -> None:
    class FailingResponses:
        def parse(self, **kwargs):  # pragma: no cover - called only on regression
            raise AssertionError("reviewed triggers must not call OpenAI")

    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        current_plan={
            "generated_at_utc": "2026-05-12T00:10:00Z",
            "explainability": {"q2_quarter_end_reviewed_utc": "2026-05-12T01:00:00Z"},
        },
        live_state={
            "recent_play_by_play": [
                {
                    "event_index": 248,
                    "period": 2,
                    "clock": "PT00M00.00S",
                    "description": "Period End",
                    "payload_json": {"edited": "2026-05-12T00:55:00Z", "subType": "end"},
                }
            ]
        },
    )

    trace, persistence = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FailingResponses()),
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert persistence["status"] == "persisted"
    assert trace.status == "detected_only"
    assert trace.revision_response is not None
    assert trace.revision_response.skipped_reason == "no_revision_required"
    assert trace.revision_response.trace_metadata["openai_call_attempted"] is False


def test_valid_mocked_llm_response_is_schema_validated_and_stored_pytest(tmp_path) -> None:
    class FakeResponses:
        def parse(self, **kwargs):
            request_id = json.loads(kwargs["input"][1]["content"])["request_id"]
            text_format = kwargs["text_format"]
            return SimpleNamespace(
                output_parsed=text_format(
                    schema_version="llm_revision_response_v1",
                    request_id=request_id,
                    status="reconciled",
                    selected_model=kwargs["model"],
                    revised_strategy_plan_json=None,
                    reconciliation_actions_json=json.dumps(["no_new_entry"]),
                    blocked_actions_json="[]",
                    confidence=0.73,
                    skipped_reason=None,
                    trace_metadata_json=json.dumps({"mock": True}),
                ),
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            )

    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
    )

    trace, persistence = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FakeResponses()),
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert trace.status == "response_recorded"
    assert trace.revision_response is not None
    assert trace.revision_response.status == "response_recorded"
    assert trace.revision_response.trace_metadata["raw_llm_status"] == "reconciled"
    assert trace.revision_response.confidence == 0.73
    assert trace.revision_response.reconciliation_actions == [{"action": "no_new_entry"}]
    assert trace.revision_response.trace_metadata["openai_call_attempted"] is True
    assert trace.revision_response.trace_metadata["order_endpoint_call_allowed"] is False
    assert persistence["response_status"] == "response_recorded"
    payload = json.loads((tmp_path / "llm-runtime" / "2026-05-11").glob("*.json").__next__().read_text())
    assert payload["response"]["trace_metadata"]["usage"]["input_tokens"] == 11
    telemetry = payload["llm_runtime_telemetry"]
    assert telemetry["controls_version"].startswith("llm_runtime_safety_controls_")
    assert telemetry["selected_model"] == MINI_MODEL
    assert telemetry["usage"]["output_tokens"] == 7
    assert telemetry["last_call_estimated_cost_usd"] > 0
    assert telemetry["event_usage_after"]["total_tokens"] == 18


def test_repeated_trigger_hash_dedup_skips_second_openai_call_pytest(tmp_path) -> None:
    class FakeResponses:
        def parse(self, **kwargs):
            request_id = json.loads(kwargs["input"][1]["content"])["request_id"]
            text_format = kwargs["text_format"]
            return SimpleNamespace(
                output_parsed=text_format(
                    request_id=request_id,
                    status="response_recorded",
                    selected_model=kwargs["model"],
                    revised_strategy_plan_json=None,
                    reconciliation_actions_json="[]",
                    blocked_actions_json="[]",
                    confidence=0.5,
                    skipped_reason=None,
                    trace_metadata_json="{}",
                ),
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    class FailingResponses:
        def parse(self, **kwargs):  # pragma: no cover - called only on regression
            raise AssertionError("duplicate trigger must not call OpenAI")

    artifact_root = tmp_path / "llm-runtime"
    first_trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
    )
    process_llm_runtime_trace(
        first_trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FakeResponses()),
        artifact_root=artifact_root,
        session_date="2026-05-11",
    )

    second_trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
    )
    second_trace, _ = process_llm_runtime_trace(
        second_trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FailingResponses()),
        artifact_root=artifact_root,
        session_date="2026-05-11",
    )

    assert second_trace.status == "skipped_unavailable"
    assert second_trace.revision_response is not None
    assert second_trace.revision_response.skipped_reason == "repeated_trigger_hash_dedup"
    assert second_trace.revision_response.trace_metadata["openai_call_attempted"] is False


def test_event_budget_exceeded_skips_openai_call_pytest(tmp_path, monkeypatch) -> None:
    class FailingResponses:
        def parse(self, **kwargs):  # pragma: no cover - called only on regression
            raise AssertionError("budget-exceeded event must not call OpenAI")

    monkeypatch.setenv("JANUS_LLM_EVENT_TOKEN_BUDGET", "1")
    day_root = tmp_path / "llm-runtime" / "2026-05-11"
    day_root.mkdir(parents=True)
    (day_root / "prior.json").write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "nba-det-cle-2026-05-11",
                "trace_id": "prior",
                "status": "response_recorded",
                "response_status": "response_recorded",
                "trigger_count": 1,
                "trigger_types": ["quarter_end"],
                "trigger_list": [{"trigger_id": "other-trigger", "trigger_type": "quarter_end"}],
                "selected_model": MINI_MODEL,
                "response": {
                    "status": "response_recorded",
                    "selected_model": MINI_MODEL,
                    "trace_metadata": {
                        "openai_call_attempted": True,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 2, "clock": "0:00"},
    )
    trace, _ = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FailingResponses()),
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert trace.status == "skipped_unavailable"
    assert trace.revision_response is not None
    assert trace.revision_response.skipped_reason == "llm_event_budget_exceeded"
    assert trace.revision_response.trace_metadata["llm_budget_status"]["hard_block"] is True


def test_final_flat_shutdown_skips_openai_call_pytest(tmp_path) -> None:
    class FailingResponses:
        def parse(self, **kwargs):  # pragma: no cover - called only on regression
            raise AssertionError("final flat event must not call OpenAI")

    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 4, "clock": "0:00"},
        direct_clob_truth={"open_orders": {"orders": []}, "open_positions": {"positions": []}},
        portfolio_state={"open_orders": 0, "open_positions": 0},
    )
    request = trace.revision_request
    assert request is not None
    request = request.model_copy(
        update={
            "scoreboard_pbp_summary": {**request.scoreboard_pbp_summary, "status": "final"},
            "direct_clob_truth": {"event_scope_flat": True, "open_orders": {"orders": []}, "open_positions": {"positions": []}},
            "portfolio_state": {"event_scope_flat": True, "open_orders": 0, "open_positions": 0},
        },
        deep=True,
    )
    trace = trace.model_copy(update={"revision_request": request}, deep=True)

    trace, _ = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FailingResponses()),
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert trace.status == "skipped_unavailable"
    assert trace.revision_response is not None
    assert trace.revision_response.skipped_reason == "event_final_flat_shutdown"
    shutdown = trace.revision_response.trace_metadata["llm_final_flat_shutdown"]
    assert shutdown["active"] is True
    assert shutdown["event_final"] is True
    assert shutdown["event_scope_flat"] is True


def test_single_object_reconciliation_action_is_tolerated_pytest(tmp_path) -> None:
    class FakeResponses:
        def parse(self, **kwargs):
            request_id = json.loads(kwargs["input"][1]["content"])["request_id"]
            text_format = kwargs["text_format"]
            return SimpleNamespace(
                output_parsed=text_format(
                    schema_version="llm_revision_response_v1",
                    request_id=request_id,
                    status="response_recorded",
                    selected_model=kwargs["model"],
                    revised_strategy_plan_json=None,
                    reconciliation_actions_json=json.dumps({"action": "target", "limit_price": 0.39}),
                    blocked_actions_json=json.dumps({"reason": "no_new_entry"}),
                    confidence=0.7,
                    skipped_reason=None,
                    trace_metadata_json="{}",
                ),
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            )

    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 3, "clock": "0:00", "score_gap": -3},
    )

    trace, _ = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FakeResponses()),
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert trace.status == "response_recorded"
    assert trace.revision_response is not None
    assert trace.revision_response.reconciliation_actions == [{"action": "target", "limit_price": 0.39}]
    assert trace.revision_response.blocked_actions == [{"reason": "no_new_entry"}]


def test_invalid_mocked_llm_response_fails_closed_pytest(tmp_path) -> None:
    class FakeResponses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed={"status": "response_recorded"}, usage=SimpleNamespace())

    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
    )

    trace, persistence = process_llm_runtime_trace(
        trace,
        dispatch_enabled=True,
        client=SimpleNamespace(responses=FakeResponses()),
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    assert persistence["status"] == "persisted"
    assert trace.status == "skipped_unavailable"
    assert trace.revision_response is not None
    assert trace.revision_response.skipped_reason.startswith("response_schema_validation_failed:")
    assert trace.revision_response.trace_metadata["order_endpoint_call_allowed"] is False


def test_latest_llm_runtime_status_loads_persisted_event_trace_pytest(tmp_path) -> None:
    trace = build_llm_runtime_trace(
        event_id="nba-det-cle-2026-05-11",
        market_id="market-1",
        session_date="2026-05-11",
        live_state={"period": 1, "clock": "0:00"},
    )
    process_llm_runtime_trace(
        trace,
        dispatch_enabled=False,
        artifact_root=tmp_path / "llm-runtime",
        session_date="2026-05-11",
    )

    status = load_latest_llm_runtime_status(
        session_date="2026-05-11",
        event_ids=["nba-det-cle-2026-05-11"],
        artifact_root=tmp_path / "llm-runtime",
    )

    assert status["status"] == "recorded"
    assert status["items"][0]["event_id"] == "nba-det-cle-2026-05-11"
    assert status["items"][0]["response_status"] == "skipped_unavailable"
    assert status["items"][0]["trigger_types"] == ["quarter_end"]
    assert status["items"][0]["adoption_status"] == "not_adoptable"
    assert status["codex_strategy_required_count"] == 1
    assert status["items"][0]["codex_fallback_state"]["status"] == "codex_strategy_required"
    assert status["items"][0]["codex_fallback_state"]["reason_code"] == "dispatch_disabled"
    assert status["items"][0]["codex_fallback_state"]["internal_llm_unavailable"] is True
    assert status["items"][0]["codex_fallback_state"]["codex_strategy_required"] is True
    assert status["items"][0]["llm_runtime_state"]["internal_llm_unavailable"] is True
    assert status["items"][0]["llm_runtime_state"]["codex_strategy_required"] is True
    assert status["items"][0]["codex_fallback_state"]["must_use_janus_validators"] is True
    assert status["items"][0]["llm_revision_adoption"]["blocker"] == "response_skipped_or_unavailable"
    assert status["safety_controls"]["model_tier_policy"]["frontier"]["default_allowed"] is False


def test_latest_llm_runtime_status_exposes_budget_blocked_state_pytest(tmp_path) -> None:
    day_root = tmp_path / "llm-runtime" / "2026-05-11"
    day_root.mkdir(parents=True)
    (day_root / "trace.json").write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "nba-det-cle-2026-05-11",
                "trace_id": "trace-budget-blocked",
                "status": "skipped_unavailable",
                "response_status": "skipped_unavailable",
                "trigger_count": 1,
                "trigger_types": ["quarter_end"],
                "selected_model": MINI_MODEL,
                "model_routing_decision": {"selected_model": MINI_MODEL, "selected_tier": "mini"},
                "response": {
                    "request_id": "request-budget-blocked",
                    "status": "skipped_unavailable",
                    "selected_model": MINI_MODEL,
                    "skipped_reason": "llm_event_budget_exceeded",
                    "trace_metadata": {
                        "openai_call_attempted": False,
                        "order_endpoint_call_allowed": False,
                    },
                },
                "persisted_at_utc": "2026-05-11T20:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    status = load_latest_llm_runtime_status(
        session_date="2026-05-11",
        event_ids=["nba-det-cle-2026-05-11"],
        artifact_root=tmp_path / "llm-runtime",
    )

    item = status["items"][0]
    assert item["codex_fallback_state"]["status"] == "codex_strategy_required"
    assert item["codex_fallback_state"]["reason_code"] == "llm_event_budget_exceeded"
    assert item["codex_fallback_state"]["budget_blocked"] is True
    assert item["llm_runtime_state"]["budget_blocked"] is True
    assert item["llm_runtime_state"]["codex_strategy_required"] is True
    assert item["llm_runtime_state"]["order_endpoint_call_allowed"] is False


def test_latest_llm_runtime_status_marks_valid_response_adoptable_pytest(tmp_path) -> None:
    day_root = tmp_path / "llm-runtime" / "2026-05-11"
    day_root.mkdir(parents=True)
    artifact_path = day_root / "trace.json"
    artifact_path.write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "nba-det-cle-2026-05-11",
                "trace_id": "trace-1",
                "status": "response_recorded",
                "response_status": "response_recorded",
                "trigger_count": 1,
                "trigger_types": ["quarter_end"],
                "selected_model": "gpt-5.5",
                "model_routing_decision": {"selected_model": "gpt-5.5", "selected_tier": "frontier"},
                "response": {
                    "request_id": "request-1",
                    "status": "response_recorded",
                    "selected_model": "gpt-5.5",
                    "revised_strategy_plan": {"event_id": "nba-det-cle-2026-05-11"},
                    "reconciliation_actions": [],
                    "blocked_actions": [],
                    "confidence": 0.74,
                    "skipped_reason": None,
                },
                "persisted_at_utc": "2026-05-11T20:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    status = load_latest_llm_runtime_status(
        session_date="2026-05-11",
        event_ids=["nba-det-cle-2026-05-11"],
        artifact_root=tmp_path / "llm-runtime",
    )

    adoption = status["items"][0]["llm_revision_adoption"]
    assert status["items"][0]["adoption_status"] == "adoptable_review_required"
    assert status["items"][0]["codex_fallback_state"]["status"] == "review_recorded_revision"
    assert status["items"][0]["codex_fallback_state"]["review_required"] is True
    assert adoption["status"] == "adoptable_review_required"
    assert adoption["review_required"] is True
    assert adoption["blocker"] is None
    assert adoption["trace_artifact_path"] == str(artifact_path.resolve())
    assert adoption["adoption_endpoint"] == "/v1/events/nba-det-cle-2026-05-11/llm-revision/adopt"
    assert adoption["required_review_fields"] == ["reviewed_by", "review_reason"]
