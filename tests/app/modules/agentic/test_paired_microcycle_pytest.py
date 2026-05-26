from __future__ import annotations

from app.modules.agentic.paired_microcycle import (
    build_paired_microcycle_evidence,
    build_paired_microcycle_readback_score,
    write_paired_microcycle_readback_score,
)


EVENT_ID = "nba-okc-sas-2026-05-24"


def _plan() -> dict:
    return {
        "event_id": EVENT_ID,
        "market_id": "market-okc-sas",
        "active_strategies": [
            {
                "strategy_id": "okc-grid",
                "family": "price_stability_micro_grid",
                "side": "Thunder",
                "sleeve_id": "okc-grid",
                "sleeve_role": "grid_scalp",
                "entry_rules": {
                    "market_id": "market-okc-sas",
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "size": 5,
                    "price": 0.04,
                },
                "exit_rules": {
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 5,
                },
            }
        ],
    }


def test_paired_microcycle_buy_fill_creates_sell_candidate_pytest() -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {"id": "trade-buy", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.04, "order_id": "janus-buy-1"},
                ]
            }
        },
        target_management={
            "sleeves": [
                {
                    "sleeve_id": "okc-grid",
                    "target_status": "target_missing",
                    "weighted_basis_price": 0.04,
                    "target_price": 0.09,
                }
            ]
        },
    )

    cycle = evidence.cycles[0]
    assert evidence.next_leg_candidate_count == 1
    assert cycle.status == "sell_candidate"
    assert cycle.next_action == "place_paired_sell"
    assert cycle.buy_leg.status == "filled"
    assert cycle.sell_leg is not None
    assert cycle.sell_leg.status == "missing"
    assert cycle.configured_target_price == 0.09
    assert cycle.reason_codes == ["filled_buy_requires_paired_sell"]


def test_paired_microcycle_open_sell_blocks_duplicate_buy_pytest() -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "open_orders": {
                "orders": [
                    {"id": "target-sell", "asset": "token-okc", "side": "sell", "status": "open", "size": 5, "price": 0.09},
                ]
            },
            "current_token_trades": {
                "trades": [
                    {"id": "trade-buy", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.04, "order_id": "janus-buy-1"},
                ]
            },
        },
    )

    cycle = evidence.cycles[0]
    assert evidence.duplicate_buy_block_count == 1
    assert cycle.status == "sell_open_waiting"
    assert cycle.next_action == "wait_for_sell_fill"
    assert cycle.duplicate_buy_blocked is True
    assert cycle.sell_leg is not None
    assert cycle.sell_leg.status == "open"
    assert cycle.reason_codes == ["open_paired_sell_blocks_duplicate_buy"]


def test_paired_microcycle_sell_fill_creates_rebuy_candidate_pytest() -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1", "janus-sell-1"},
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {
                        "id": "trade-buy",
                        "asset": "token-okc",
                        "side": "buy",
                        "size": 5,
                        "price": 0.04,
                        "order_id": "janus-buy-1",
                        "timestamp_utc": "2026-05-25T01:00:00Z",
                    },
                    {
                        "id": "trade-sell",
                        "asset": "token-okc",
                        "side": "sell",
                        "size": 5,
                        "price": 0.09,
                        "order_id": "janus-sell-1",
                        "timestamp_utc": "2026-05-25T01:03:30Z",
                    },
                ]
            },
        },
        event_risk_budget={"budget_status": "within_budget", "remaining_notional_usd": 10.0},
    )

    cycle = evidence.cycles[0]
    assert cycle.status == "rebuy_candidate"
    assert cycle.next_action == "place_paired_rebuy"
    assert cycle.sell_leg is not None
    assert cycle.sell_leg.status == "filled"
    assert cycle.rebuy_leg is not None
    assert cycle.rebuy_leg.status == "waiting"
    assert cycle.reason_codes == ["sell_fill_allows_rebuy_review"]

    score = build_paired_microcycle_readback_score(evidence, final_prices_by_token={"token-okc": 0.0})
    row = score.scores[0]
    assert score.realized_cycle_count == 1
    assert score.missed_transition_count == 1
    assert score.latency_scored_count == 1
    assert score.net_realized_pnl_usd == 0.25
    assert row.realized_pnl_usd == 0.25
    assert row.final_mark_pnl_usd is None
    assert row.latency_seconds == 210.0
    assert row.fillability_status == "rebuy_review_ready"
    assert row.missed_transition_codes == ["rebuy_review_pending_after_sell_fill"]


def test_paired_microcycle_manual_fill_import_and_stale_target_replacement_pytest() -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-other-order"},
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {"id": "operator-buy", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.05, "order_id": "manual-buy-1"},
                ]
            },
        },
        target_management={
            "sleeves": [
                {
                    "sleeve_id": "okc-grid",
                    "target_status": "target_stale",
                    "weighted_basis_price": 0.05,
                    "target_price": 0.10,
                }
            ]
        },
    )

    cycle = evidence.cycles[0]
    assert evidence.manual_fill_import_count == 1
    assert cycle.status == "sell_stale_replace"
    assert cycle.next_action == "replace_paired_sell"
    assert cycle.manual_fill_imported is True
    assert cycle.buy_leg.actor == "operator"
    assert cycle.sell_leg is not None
    assert cycle.sell_leg.status == "stale"
    assert cycle.reason_codes == ["paired_sell_target_stale", "manual_or_unknown_fill_imported"]


def test_paired_microcycle_manual_imported_sleeve_is_first_class_pytest() -> None:
    plan = {
        "event_id": EVENT_ID,
        "market_id": "market-okc-sas",
        "active_strategies": [
            {
                "strategy_id": "okc-operator-import",
                "family": "manual_imported_position_management",
                "side": "Thunder",
                "sleeve_id": "okc-operator-import",
                "sleeve_role": "manual_imported",
                "entry_rules": {
                    "market_id": "market-okc-sas",
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "size": 5,
                    "price": 0.04,
                },
                "exit_rules": {
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 1,
                },
            }
        ],
    }

    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=plan,
        known_external_order_ids=set(),
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {"id": "operator-buy", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.04, "order_id": "operator-buy-1"},
                ]
            },
        },
        target_management={
            "sleeves": [
                {
                    "sleeve_id": "okc-operator-import",
                    "target_status": "target_missing",
                    "weighted_basis_price": 0.04,
                    "target_price": 0.05,
                }
            ]
        },
    )

    cycle = evidence.cycles[0]
    assert evidence.manual_fill_import_count == 1
    assert evidence.next_leg_candidate_count == 1
    assert cycle.sleeve_role == "manual_imported"
    assert cycle.status == "sell_candidate"
    assert cycle.next_action == "place_paired_sell"
    assert cycle.manual_fill_imported is True
    assert cycle.buy_leg.actor == "operator"
    assert cycle.reason_codes == ["filled_buy_requires_paired_sell", "manual_or_unknown_fill_imported"]


def test_paired_microcycle_blocks_rebuy_when_event_budget_exhausted_pytest() -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1", "janus-sell-1"},
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {"id": "trade-buy", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.04, "order_id": "janus-buy-1"},
                    {"id": "trade-sell", "asset": "token-okc", "side": "sell", "size": 5, "price": 0.09, "order_id": "janus-sell-1"},
                ]
            },
        },
        event_risk_budget={"budget_status": "exhausted", "remaining_notional_usd": 0.0},
    )

    cycle = evidence.cycles[0]
    assert evidence.budget_block_count == 1
    assert cycle.status == "rebuy_blocked"
    assert cycle.next_action == "blocked"
    assert cycle.rebuy_leg is not None
    assert cycle.rebuy_leg.status == "blocked"
    assert cycle.reason_codes == ["event_budget_exhausted"]


def test_paired_microcycle_readback_scores_unfilled_sell_and_final_mark_pytest() -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "open_orders": {
                "orders": [
                    {
                        "id": "target-sell",
                        "asset": "token-okc",
                        "side": "sell",
                        "status": "open",
                        "size": 5,
                        "price": 0.09,
                    },
                ]
            },
            "current_token_trades": {
                "trades": [
                    {
                        "id": "trade-buy",
                        "asset": "token-okc",
                        "side": "buy",
                        "size": 5,
                        "price": 0.04,
                        "order_id": "janus-buy-1",
                    },
                ]
            },
        },
    )

    score = build_paired_microcycle_readback_score(evidence, final_prices_by_token={"token-okc": 0.0})
    row = score.scores[0]
    assert score.realized_cycle_count == 0
    assert score.missed_transition_count == 1
    assert score.fillability_block_count == 1
    assert score.net_realized_pnl_usd == 0.0
    assert score.net_final_mark_pnl_usd == -0.2
    assert row.fillability_status == "paired_sell_open_unfilled"
    assert row.open_sell_shares == 5
    assert row.final_mark_pnl_usd == -0.2
    assert row.missed_transition_codes == [
        "paired_sell_unfilled_before_readback",
        "duplicate_buy_blocked_while_cycle_unresolved",
    ]


def test_paired_microcycle_readback_score_persists_json_markdown_and_index_pytest(tmp_path) -> None:
    evidence = build_paired_microcycle_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {
                        "id": "trade-buy",
                        "asset": "token-okc",
                        "side": "buy",
                        "size": 5,
                        "price": 0.04,
                        "order_id": "janus-buy-1",
                    },
                ]
            }
        },
    )
    score = build_paired_microcycle_readback_score(evidence, final_prices_by_token={"token-okc": 0.0})

    result = write_paired_microcycle_readback_score(
        score,
        day="2026-05-25",
        artifact_root=tmp_path / "artifacts",
        report_dir=tmp_path / "reports",
    )

    json_path = tmp_path / "artifacts" / "sports-live-paired-microcycle" / "2026-05-25"
    assert result["status"] == "stored"
    assert (json_path / "paired_microcycle_readback_scores.jsonl").exists()
    markdown = (tmp_path / "reports").glob("paired_microcycle_readback_score_*.md")
    text = next(markdown).read_text(encoding="utf-8")
    assert "Paired Microcycle Readback Score" in text
    assert "paired_sell_missing_after_buy_fill" in text
    assert "no order, cancel, replace" in text
