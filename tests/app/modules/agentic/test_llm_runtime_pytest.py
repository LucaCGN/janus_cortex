from __future__ import annotations

import json
from types import SimpleNamespace

from app.modules.agentic.contracts import LLMRevisionRequest, LLMRuntimeTrigger
from app.modules.agentic.llm_runtime import (
    FRONTIER_MODEL,
    MINI_MODEL,
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
    assert prompt_contract["required_json_output_schema"]["revised_strategy_plan"] == "StrategyPlanJSON or null"
    assert prompt_contract["authority_boundaries"][0] == "The LLM never calls order endpoints."


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
    assert status["items"][0]["llm_revision_adoption"]["blocker"] == "response_skipped_or_unavailable"


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
    assert adoption["status"] == "adoptable_review_required"
    assert adoption["review_required"] is True
    assert adoption["blocker"] is None
    assert adoption["trace_artifact_path"] == str(artifact_path.resolve())
    assert adoption["adoption_endpoint"] == "/v1/events/nba-det-cle-2026-05-11/llm-revision/adopt"
    assert adoption["required_review_fields"] == ["reviewed_by", "review_reason"]
