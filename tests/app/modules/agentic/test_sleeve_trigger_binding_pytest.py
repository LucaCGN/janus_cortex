from __future__ import annotations

from app.modules.agentic.sleeve_trigger_binding import (
    build_sleeve_trigger_binding_evidence,
    live_signals_from_sleeve_trigger_bindings,
)


EVENT_ID = "nba-okc-sas-2026-05-24"


def _plan() -> dict:
    return {
        "market_id": "market-okc-sas",
        "active_strategies": [
            {
                "strategy_id": "okc-q4-band-grid-v1",
                "family": "band_grid",
                "side": "Thunder",
                "sleeve_id": "okc-q4-grid",
                "sleeve_group": "okc",
                "sleeve_role": "grid_scalp",
                "entry_rules": {
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "size": 5,
                    "price": 0.03,
                },
            }
        ],
    }


def test_microcycle_sell_candidate_becomes_sleeve_bound_sell_signal_pytest() -> None:
    market_state = {
        "normalized_live_snapshot": {"evidence_paths": ["local/shared/artifacts/ops/live-monitor.json"]},
        "paired_microcycle": {
            "cycles": [
                {
                    "cycle_id": "cycle-okc",
                    "sleeve_id": "okc-q4-grid",
                    "sleeve_role": "grid_scalp",
                    "strategy_id": "okc-q4-band-grid-v1",
                    "token_id": "token-okc",
                    "outcome_id": "outcome-okc",
                    "outcome_label": "Thunder",
                    "configured_entry_shares": 5,
                    "configured_target_price": 0.04,
                    "status": "sell_candidate",
                    "next_action": "place_paired_sell",
                    "next_leg_candidate": True,
                    "reason_codes": ["filled_buy_requires_paired_sell"],
                    "sell_leg": {"shares": 5, "price": 0.04},
                }
            ]
        },
    }

    evidence = build_sleeve_trigger_binding_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        evaluation={"ok": True, "sleeve_states": []},
        market_state=market_state,
        min_size=5,
        source="pytest",
    )
    signals = live_signals_from_sleeve_trigger_bindings(evidence)

    assert evidence.microcycle_binding_count == 1
    assert evidence.actionable_binding_count == 1
    assert signals[0].signal_type == "sell"
    assert signals[0].side == "Thunder"
    assert signals[0].risk_request.sleeve_id == "okc-q4-grid"
    assert signals[0].risk_request.requested_shares == 5
    assert signals[0].risk_request.max_price == 0.04
    assert signals[0].payload["cycle_id"] == "cycle-okc"
    assert signals[0].payload["trigger_type"] == "paired_microcycle_next_leg"


def test_microcycle_open_sell_blocks_only_its_local_sleeve_pytest() -> None:
    market_state = {
        "paired_microcycle": {
            "cycles": [
                {
                    "cycle_id": "cycle-okc",
                    "sleeve_id": "okc-q4-grid",
                    "sleeve_role": "grid_scalp",
                    "strategy_id": "okc-q4-band-grid-v1",
                    "token_id": "token-okc",
                    "outcome_id": "outcome-okc",
                    "outcome_label": "Thunder",
                    "configured_entry_shares": 5,
                    "status": "sell_open_waiting",
                    "next_action": "wait_for_sell_fill",
                    "duplicate_buy_blocked": True,
                    "reason_codes": ["open_paired_sell_blocks_duplicate_buy"],
                    "sell_leg": {"shares": 5, "price": 0.04},
                }
            ]
        },
    }

    evidence = build_sleeve_trigger_binding_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        evaluation={"ok": True, "sleeve_states": []},
        market_state=market_state,
        min_size=5,
        source="pytest",
    )
    signals = live_signals_from_sleeve_trigger_bindings(evidence)

    assert evidence.blocker_binding_count == 1
    assert signals[0].signal_type == "block"
    assert signals[0].payload["aggregation_scope"] == "local_sleeve"
    assert signals[0].payload["cycle_id"] == "cycle-okc"


def test_microcycle_sell_fill_becomes_rebuy_signal_pytest() -> None:
    market_state = {
        "paired_microcycle": {
            "cycles": [
                {
                    "cycle_id": "cycle-okc",
                    "sleeve_id": "okc-q4-grid",
                    "sleeve_role": "grid_scalp",
                    "strategy_id": "okc-q4-band-grid-v1",
                    "token_id": "token-okc",
                    "outcome_id": "outcome-okc",
                    "outcome_label": "Thunder",
                    "configured_entry_shares": 5,
                    "status": "rebuy_candidate",
                    "next_action": "place_paired_rebuy",
                    "next_leg_candidate": True,
                    "reason_codes": ["sell_fill_allows_rebuy_review"],
                    "rebuy_leg": {"shares": 5, "price": 0.03},
                }
            ]
        },
    }

    evidence = build_sleeve_trigger_binding_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        evaluation={"ok": True, "sleeve_states": []},
        market_state=market_state,
        min_size=5,
        source="pytest",
    )
    signals = live_signals_from_sleeve_trigger_bindings(evidence)

    assert evidence.actionable_binding_count == 1
    assert signals[0].signal_type == "rebuy"
    assert signals[0].risk_request.max_price == 0.03
    assert "sell_fill_allows_rebuy_review" in signals[0].reason_codes

