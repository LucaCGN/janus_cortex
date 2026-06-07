from __future__ import annotations

from crypto_options_app.strategies.revision_scout import (
    recommend_next_lanes,
    summarize_signal_coverage,
    summarize_strategy_promotion,
)


def test_revision_scout_identifies_profile_repair_and_option_context_lanes_pytest() -> None:
    signal_rows = [
        {"promotion_state": "PROMOTION_READY", "signal_type": "outcome_prediction", "source_blocks": ["A"]},
        {"promotion_state": "PROMOTION_READY", "signal_type": "support_resistance", "source_blocks": ["C"]},
        {"promotion_state": "PROMOTION_READY", "signal_type": "liquidity_depth", "source_blocks": ["C"]},
        {"promotion_state": "PROMOTION_READY", "signal_type": "stale_order_review", "source_blocks": ["C"]},
        {"promotion_state": "NEEDS_V2_REVIEW", "signal_type": "outcome_prediction", "source_blocks": ["B"]},
        {"promotion_state": "NEEDS_V2_REVIEW", "signal_type": "hedge_ratio", "source_blocks": ["B"]},
    ]
    strategy_summary = {
        "strategies_clearing_live_policy": 0,
    }

    coverage = summarize_signal_coverage(signal_rows)
    lanes = recommend_next_lanes(
        promotion_ready_counts=coverage["promotion_ready_by_type_source"],
        revision_counts=coverage["revision_by_type_source"],
        strategy_summary=strategy_summary,
    )
    lane_ids = [lane["lane_id"] for lane in lanes]

    assert coverage["promotion_ready_by_type_source"]["outcome_prediction|A"] == 1
    assert coverage["revision_by_type_source"]["outcome_prediction|B"] == 1
    assert "repair_profile_distribution_executable_signal" in lane_ids
    assert "crypto_outcome_option_context_control" in lane_ids
    assert "option_liquidity_micro_scalp_shadow" in lane_ids
    assert "retire_or_revision_existing_live_lanes" in lane_ids


def test_revision_scout_strategy_summary_marks_policy_clearing_rows_pytest() -> None:
    rows = [
        {
            "strategy_id": "candidate_good",
            "promotion_state": "SHADOW_READY",
            "evidence_json": """
            {
              "evidence": {
                "recent_shadow_live_economic_sample_count": 12,
                "recent_shadow_live_win_rate": 0.75,
                "recent_shadow_live_simulated_pnl_usd": 0.42
              },
              "blockers": [],
              "next_action": "promote after review"
            }
            """,
        },
        {
            "strategy_id": "candidate_bad",
            "promotion_state": "SHADOW_REVIEW",
            "evidence_json": """
            {
              "evidence": {
                "recent_shadow_live_economic_sample_count": 12,
                "recent_shadow_live_win_rate": 0.25,
                "recent_shadow_live_simulated_pnl_usd": -0.12
              },
              "blockers": ["recent_shadow_live_win_rate_below_70"],
              "next_action": "revise"
            }
            """,
        },
    ]

    summary = summarize_strategy_promotion(rows)

    assert summary["strategy_count"] == 2
    assert summary["strategies_with_recent_economics"] == 2
    assert summary["strategies_clearing_live_policy"] == 1
    by_id = {row["strategy_id"]: row for row in summary["strategies"]}
    assert by_id["candidate_good"]["clears_live_policy"] is True
    assert by_id["candidate_bad"]["clears_live_policy"] is False
