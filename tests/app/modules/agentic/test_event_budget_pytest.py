from __future__ import annotations

from app.modules.agentic.event_budget import (
    EventRiskBudgetPolicy,
    SleeveTransitionRequest,
    derive_event_risk_budget,
    evaluate_event_sleeve_transitions,
)


EVENT_ID = "nba-okc-sas-2026-05-24"


def test_event_budget_uses_portfolio_cash_and_absolute_caps_pytest() -> None:
    snapshot = derive_event_risk_budget(
        event_id=EVENT_ID,
        portfolio_value_usd=250.0,
        available_cash_usd=60.0,
        current_position_notional_usd=3.0,
        open_order_notional_usd=2.0,
        pending_intent_notional_usd=1.0,
        policy=EventRiskBudgetPolicy(event_cap_pct=0.10, cash_cap_pct=0.20, absolute_event_cap_usd=10.0),
    )

    assert snapshot.portfolio_cap_usd == 25.0
    assert snapshot.cash_cap_usd == 12.0
    assert snapshot.event_cap_usd == 10.0
    assert snapshot.used_notional_usd == 6.0
    assert snapshot.remaining_notional_usd == 4.0
    assert snapshot.budget_status == "within_budget"
    assert snapshot.blocker_codes == []


def test_event_budget_reports_exhausted_and_over_limit_pytest() -> None:
    exhausted = derive_event_risk_budget(
        event_id=EVENT_ID,
        portfolio_value_usd=100.0,
        available_cash_usd=50.0,
        current_position_notional_usd=10.0,
    )
    over_limit = derive_event_risk_budget(
        event_id=EVENT_ID,
        portfolio_value_usd=100.0,
        available_cash_usd=50.0,
        current_position_notional_usd=10.01,
    )

    assert exhausted.budget_status == "exhausted"
    assert exhausted.blocker_codes == ["event_budget_exhausted"]
    assert over_limit.budget_status == "over_budget"
    assert over_limit.blocker_codes == ["event_budget_over_limit"]


def test_sleeve_transitions_support_grid_core_rebuy_reduce_and_monitor_pytest() -> None:
    budget = derive_event_risk_budget(
        event_id=EVENT_ID,
        portfolio_value_usd=200.0,
        available_cash_usd=100.0,
        current_position_notional_usd=0.0,
    )
    bundle = evaluate_event_sleeve_transitions(
        event_id=EVENT_ID,
        budget=budget,
        sleeves=[
            SleeveTransitionRequest(
                sleeve_id="okc-grid",
                sleeve_role="grid_scalp",
                action="buy",
                side="Thunder",
                requested_shares=5,
                max_price=0.22,
            ),
            SleeveTransitionRequest(
                sleeve_id="okc-core",
                sleeve_role="core_hold",
                action="buy",
                side="Thunder",
                requested_shares=5,
                max_price=0.22,
            ),
            SleeveTransitionRequest(
                sleeve_id="okc-rebuy",
                sleeve_role="rebuy",
                action="rebuy",
                side="Thunder",
                requested_shares=5,
                max_price=0.18,
                existing_position_shares=5,
                target_coverage_shares=5,
            ),
            SleeveTransitionRequest(
                sleeve_id="okc-reduce",
                sleeve_role="reduce_stop",
                action="reduce",
                side="Thunder",
                existing_position_shares=5,
                target_coverage_shares=0,
            ),
            SleeveTransitionRequest(
                sleeve_id="okc-monitor",
                sleeve_role="monitor_only",
                action="monitor",
                side="Thunder",
            ),
        ],
    )

    decisions = {decision.sleeve_id: decision for decision in bundle.decisions}
    assert bundle.has_order_candidates is True
    assert decisions["okc-grid"].status == "intent_candidate"
    assert decisions["okc-grid"].remaining_notional_usd_after == 8.9
    assert decisions["okc-core"].status == "intent_candidate"
    assert decisions["okc-core"].remaining_notional_usd_after == 7.8
    assert decisions["okc-rebuy"].status == "intent_candidate"
    assert decisions["okc-rebuy"].remaining_notional_usd_after == 6.9
    assert decisions["okc-reduce"].status == "intent_candidate"
    assert decisions["okc-reduce"].requested_notional_usd == 0.0
    assert decisions["okc-monitor"].status == "monitor_only"


def test_sleeve_transitions_block_duplicate_exposure_and_budget_overflow_pytest() -> None:
    budget = derive_event_risk_budget(
        event_id=EVENT_ID,
        portfolio_value_usd=100.0,
        available_cash_usd=50.0,
        current_position_notional_usd=8.0,
    )
    bundle = evaluate_event_sleeve_transitions(
        event_id=EVENT_ID,
        budget=budget,
        sleeves=[
            SleeveTransitionRequest(
                sleeve_id="okc-grid",
                sleeve_role="grid_scalp",
                action="buy",
                requested_shares=5,
                max_price=0.50,
                existing_position_shares=5,
            ),
            SleeveTransitionRequest(
                sleeve_id="okc-rebuy",
                sleeve_role="rebuy",
                action="rebuy",
                requested_shares=5,
                max_price=0.10,
                existing_position_shares=5,
                target_coverage_shares=0,
            ),
        ],
    )

    decisions = {decision.sleeve_id: decision for decision in bundle.decisions}
    assert decisions["okc-grid"].status == "blocked"
    assert decisions["okc-grid"].reason_codes == ["duplicate_open_position", "event_budget_exceeded"]
    assert decisions["okc-rebuy"].status == "blocked"
    assert decisions["okc-rebuy"].reason_codes == ["rebuy_requires_existing_position_covered"]
