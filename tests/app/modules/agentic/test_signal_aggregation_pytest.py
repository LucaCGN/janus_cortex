from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.modules.agentic.contracts import (
    LiveSignal,
    LiveSignalFreshness,
    LiveSignalPriceBand,
    LiveSignalRiskRequest,
)
from app.modules.agentic.signal_aggregation import (
    LiveSignalAggregationControl,
    LiveSignalAggregationInventory,
    aggregate_live_signals,
)
from app.modules.agentic.store import write_live_signal_aggregation_decision


BASE_TIME = datetime(2026, 5, 25, 4, 10, tzinfo=timezone.utc)
EVENT_ID = "nba-okc-sas-2026-05-24"


def _signal(
    *,
    source: str = "deterministic",
    signal_type: str = "buy",
    side: str = "Thunder",
    confidence: float = 0.74,
    emitted_offset_seconds: int = 0,
    stale: bool = False,
    token: str = "token-thunder",
    reason: str = "score_gap_inside_rule",
    payload: dict[str, object] | None = None,
) -> LiveSignal:
    emitted_at = BASE_TIME + timedelta(seconds=emitted_offset_seconds)
    return LiveSignal(
        event_id=EVENT_ID,
        market_id="market-okc-sas",
        outcome_id=f"outcome-{side.lower()}",
        market_token_id=token,
        source=source,
        signal_type=signal_type,
        side=side,
        emitted_at_utc=emitted_at,
        price_band=LiveSignalPriceBand(current_price=0.22, lower_price=0.19, upper_price=0.24),
        confidence=confidence,
        confidence_source=f"{source}_pytest",
        freshness=LiveSignalFreshness(source_timestamp_utc=emitted_at, latency_ms=220, stale=stale),
        reason_codes=[reason],
        risk_request=LiveSignalRiskRequest(sleeve_id="okc-grid", requested_shares=5, max_price=0.24),
        evidence_paths=[f"local/shared/artifacts/ops/{source}.json"],
        payload=payload or {},
    )


def test_aggregator_merges_confirming_buy_signals_into_one_candidate_pytest() -> None:
    decision = aggregate_live_signals(
        [
            _signal(source="deterministic", confidence=0.71),
            _signal(source="ml", confidence=0.82, reason="pbp_undervaluation"),
        ],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "order_intent_candidate"
    assert decision.confirming_signal_count == 2
    assert decision.conflict_count == 0
    assert len(decision.order_intent_candidates) == 1
    candidate = decision.order_intent_candidates[0]
    assert candidate.side == "Thunder"
    assert candidate.sleeve_id == "okc-grid"
    assert candidate.supporting_signal_ids == decision.selected_signal_ids
    assert candidate.reason_codes == ["score_gap_inside_rule", "pbp_undervaluation"]
    assert set(candidate.evidence_paths) == {
        "local/shared/artifacts/ops/deterministic.json",
        "local/shared/artifacts/ops/ml.json",
    }


def test_aggregator_blocks_conflicting_signals_pytest() -> None:
    decision = aggregate_live_signals(
        [
            _signal(source="deterministic", signal_type="buy", side="Thunder", token="token-thunder"),
            _signal(source="operator", signal_type="sell", side="Thunder", token="token-thunder", reason="operator_reduce"),
        ],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "blocked"
    assert decision.conflict_count == 1
    assert decision.order_intent_candidates == []
    assert {blocker.reason_code for blocker in decision.blocker_artifacts} == {"conflicting_actionable_signals"}


def test_aggregator_prioritizes_reduce_stop_over_rebuy_conflict_pytest() -> None:
    rebuy = _signal(
        signal_type="rebuy",
        side="Thunder",
        token="token-thunder",
        confidence=0.99,
        reason="sell_fill_allows_rebuy_review",
    )
    rebuy.payload = {"sleeve_id": "okc-grid", "trigger_source": "paired_microcycle"}
    reduce = _signal(
        signal_type="reduce",
        side="Thunder",
        token="token-thunder",
        confidence=0.91,
        reason="reduce_stop_triggered",
    )
    reduce.payload = {"sleeve_id": "okc-grid", "trigger_source": "reduce_stop_lifecycle"}

    decision = aggregate_live_signals(
        [rebuy, reduce],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "order_intent_candidate"
    assert decision.conflict_count == 1
    assert decision.blocker_artifacts == []
    assert decision.order_intent_candidates[0].signal_type == "reduce"
    assert decision.order_intent_candidates[0].trigger_source == "reduce_stop_lifecycle"


def test_aggregator_keeps_sleeve_local_blocker_from_suppressing_independent_candidate_pytest() -> None:
    blocked_sleeve = _signal(
        signal_type="block",
        side="Spurs",
        token="token-spurs",
        reason="score_gap_outside_range",
    )
    blocked_sleeve.risk_request = LiveSignalRiskRequest(sleeve_id="sas-grid", requested_shares=5, max_price=0.54)
    blocked_sleeve.payload = {"aggregation_scope": "sleeve", "sleeve_id": "sas-grid"}

    decision = aggregate_live_signals(
        [
            _signal(source="deterministic", signal_type="buy", side="Thunder", token="token-thunder"),
            blocked_sleeve,
        ],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "order_intent_candidate"
    assert len(decision.order_intent_candidates) == 1
    assert decision.order_intent_candidates[0].side == "Thunder"
    assert [(blocker.reason_code, blocker.detail["scope"], blocker.detail["candidate_blocking"]) for blocker in decision.blocker_artifacts] == [
        ("block_signal_present", "local_sleeve", False)
    ]


def test_aggregator_keeps_independent_sell_from_blocking_rebuy_candidate_pytest() -> None:
    rebuy = _signal(
        signal_type="rebuy",
        side="Cavaliers",
        token="token-cavaliers",
        confidence=0.78,
        reason="target_filled_rebuy_leg",
    )
    rebuy.risk_request = LiveSignalRiskRequest(sleeve_id="cavaliers-grid", requested_shares=5, max_price=0.24)
    rebuy.payload = {"sleeve_id": "cavaliers-grid", "trigger_source": "paired_microcycle"}
    sell = _signal(
        signal_type="sell",
        side="Knicks",
        token="token-knicks",
        confidence=0.72,
        reason="paired_sell_target",
    )
    sell.risk_request = LiveSignalRiskRequest(sleeve_id="knicks-grid", requested_shares=5, max_price=0.81)
    sell.payload = {"sleeve_id": "knicks-grid", "trigger_source": "paired_microcycle"}

    decision = aggregate_live_signals(
        [rebuy, sell],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "order_intent_candidate"
    assert decision.conflict_count == 0
    assert decision.order_intent_candidates[0].side == "Cavaliers"


def test_aggregator_keeps_local_block_local_without_candidate_pytest() -> None:
    blocked_sleeve = _signal(signal_type="block", side="Spurs", token="token-spurs")
    blocked_sleeve.risk_request = LiveSignalRiskRequest(sleeve_id="sas-grid", requested_shares=5, max_price=0.54)
    blocked_sleeve.payload = {"aggregation_scope": "local_sleeve", "sleeve_id": "sas-grid"}

    decision = aggregate_live_signals(
        [blocked_sleeve],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "blocked"
    assert decision.order_intent_candidates == []
    assert decision.blocker_artifacts[0].detail == {"scope": "local_sleeve", "candidate_blocking": False}


def test_aggregator_carries_trigger_and_cycle_metadata_to_candidate_pytest() -> None:
    signal = _signal()
    signal.payload = {
        "sleeve_group": "okc",
        "strategy_id": "okc-q4-band-grid-v1",
        "strategy_family": "band_grid",
        "cycle_id": "cycle-okc",
        "trigger_type": "paired_microcycle_next_leg",
        "trigger_source": "paired_microcycle",
        "lifecycle_policy": {"target_delta_cents": 1.0, "target_policy": "micro_target"},
        "game_scenario": {"scenario_level": "A"},
        "dynamic_risk_state": {"tail_risk_budget_usd": 1.0},
        "ml_confidence": {"confidence": 0.72, "model_status": "deterministic_fallback"},
    }

    decision = aggregate_live_signals(
        [signal],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    candidate = decision.order_intent_candidates[0]
    assert candidate.sleeve_id == "okc-grid"
    assert candidate.sleeve_group == "okc"
    assert candidate.strategy_id == "okc-q4-band-grid-v1"
    assert candidate.strategy_family == "band_grid"
    assert candidate.cycle_id == "cycle-okc"
    assert candidate.trigger_type == "paired_microcycle_next_leg"
    assert candidate.trigger_source == "paired_microcycle"
    assert candidate.lifecycle_policy["target_policy"] == "micro_target"
    assert candidate.game_scenario["scenario_level"] == "A"
    assert candidate.dynamic_risk_state["tail_risk_budget_usd"] == 1.0
    assert candidate.ml_confidence["confidence"] == 0.72


def test_aggregator_blocks_stale_signal_before_order_candidate_pytest() -> None:
    decision = aggregate_live_signals(
        [_signal(stale=True)],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "blocked"
    assert decision.stale_signal_count == 1
    assert decision.selected_signal_ids == []
    assert decision.order_intent_candidates == []
    assert decision.blocker_artifacts[0].reason_code == "stale_signal"


def test_aggregator_dedupes_buy_signals_and_blocks_existing_exposure_pytest() -> None:
    decision = aggregate_live_signals(
        [
            _signal(source="deterministic", emitted_offset_seconds=0),
            _signal(source="deterministic", emitted_offset_seconds=10),
        ],
        event_id=EVENT_ID,
        inventory=LiveSignalAggregationInventory(open_position_count=1, current_exposure_notional_usd=1.10),
        control=LiveSignalAggregationControl(cooldown_seconds=90),
        generated_at_utc=BASE_TIME + timedelta(seconds=15),
    )

    assert decision.decision_type == "blocked"
    assert decision.duplicate_signal_count == 1
    assert len(decision.selected_signal_ids) == 1
    assert len(decision.suppressed_signal_ids) == 1
    assert decision.order_intent_candidates == []
    assert {blocker.reason_code for blocker in decision.blocker_artifacts} == {
        "duplicate_signal_cooldown",
        "duplicate_exposure_risk",
    }


def test_aggregator_allows_explicit_sleeve_scoped_inventory_add_candidate_pytest() -> None:
    decision = aggregate_live_signals(
        [
            _signal(
                payload={
                    "position_limit_scope": "sleeve",
                    "allow_existing_position_add": True,
                }
            )
        ],
        event_id=EVENT_ID,
        inventory=LiveSignalAggregationInventory(open_position_count=1, current_exposure_notional_usd=1.10),
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    assert decision.decision_type == "order_intent_candidate"
    assert decision.order_intent_candidates[0].sleeve_id == "okc-grid"
    assert {blocker.reason_code for blocker in decision.blocker_artifacts} == set()


def test_aggregation_decision_artifact_writer_records_json_and_index_pytest(tmp_path) -> None:
    decision = aggregate_live_signals(
        [_signal()],
        event_id=EVENT_ID,
        generated_at_utc=BASE_TIME + timedelta(seconds=5),
    )

    result = write_live_signal_aggregation_decision(
        decision,
        day="2026-05-25",
        root=tmp_path,
        source="pytest",
    )

    assert result["status"] == "stored"
    payload = json.loads((tmp_path / "live-signal-aggregation" / "2026-05-25" / EVENT_ID / f"{decision.decision_id}.json").read_text(encoding="utf-8"))
    assert payload["decision_type"] == "order_intent_candidate"
    rows = [
        json.loads(line)
        for line in (tmp_path / "live-signal-aggregation" / "2026-05-25" / "aggregation_decisions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["decision_id"] == decision.decision_id
    assert rows[0]["order_intent_candidate_count"] == 1
