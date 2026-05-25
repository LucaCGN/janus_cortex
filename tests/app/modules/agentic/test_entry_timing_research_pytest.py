from __future__ import annotations

import json

from app.modules.agentic.entry_timing_research import (
    build_entry_timing_matrix,
    build_entry_timing_matrix_from_fixture_backtest,
    render_entry_timing_matrix_markdown,
    write_entry_timing_matrix,
)


def _fixture_backtest_payload() -> dict:
    return {
        "schema_version": "postgame_replay_fixture_backtest_v1",
        "session_date": "2026-05-25",
        "results": [
            {
                "case_id": "wnba-phx-atl-atlanta-comeback-low-band",
                "event_id": "wnba-phx-atl-2026-05-24",
                "league": "WNBA",
                "side": "Atlanta",
                "expected_direction": "positive_candidate",
                "fillability_passed": True,
                "score_gap_available": True,
                "duplicate_cooldown_passed": True,
                "target_fill_pnl_usd": 0.65,
                "final_score_pnl_usd": 3.4,
                "recommendation": "eligible_for_entry_timing_matrix_not_live_promotion",
                "blockers": [],
                "evidence_note": "Atlanta comeback fixture.",
            },
            {
                "case_id": "wnba-dal-nyl-dallas-q2-low-band",
                "event_id": "wnba-dal-nyl-2026-05-24",
                "league": "WNBA",
                "side": "Dallas",
                "expected_direction": "positive_candidate",
                "fillability_passed": True,
                "score_gap_available": True,
                "duplicate_cooldown_passed": True,
                "target_fill_pnl_usd": 1.1,
                "final_score_pnl_usd": 3.85,
                "recommendation": "eligible_for_entry_timing_matrix_not_live_promotion",
                "blockers": [],
                "evidence_note": "Dallas Q2 low-band fixture.",
            },
            {
                "case_id": "wnba-wsh-sea-seattle-q1-rebound",
                "event_id": "wnba-wsh-sea-2026-05-24",
                "league": "WNBA",
                "side": "Seattle",
                "expected_direction": "positive_candidate",
                "fillability_passed": True,
                "score_gap_available": True,
                "duplicate_cooldown_passed": True,
                "target_fill_pnl_usd": 0.95,
                "final_score_pnl_usd": 3.7,
                "recommendation": "eligible_for_entry_timing_matrix_not_live_promotion",
                "blockers": [],
                "evidence_note": "Seattle Q1 rebound fixture.",
            },
            {
                "case_id": "nba-okc-sas-thunder-q4-subpenny-negative",
                "event_id": "nba-okc-sas-2026-05-24",
                "league": "NBA",
                "side": "Thunder",
                "expected_direction": "negative_case",
                "fillability_passed": True,
                "score_gap_available": True,
                "duplicate_cooldown_passed": False,
                "target_fill_pnl_usd": 3.03,
                "final_score_pnl_usd": -3.03,
                "recommendation": "quarantine_until_independent_replay_proves_edge",
                "blockers": ["duplicate_intent_cooldown_required", "final_score_negative_edge"],
                "evidence_note": "Thunder Q4 subpenny fixture.",
            },
        ],
    }


def test_entry_timing_matrix_builds_rows_without_live_promotion_pytest() -> None:
    matrix = build_entry_timing_matrix_from_fixture_backtest(
        _fixture_backtest_payload(),
        source_path="fixture.json",
    )

    assert matrix.trading_boundary == "read_only_research_no_live_promotion"
    assert matrix.acceptance_progress["eligible_case_count"] == 3
    assert matrix.acceptance_progress["live_promotion_allowed"] is False
    assert matrix.acceptance_progress["includes_side_by_side_policy_windows"] is True
    assert matrix.acceptance_progress["side_by_side_policy_count"] == 4
    assert matrix.acceptance_progress["separates_return_fill_missed_entry_adverse_selection_and_expiry"] is True
    assert {row.timing_policy for row in matrix.rows} >= {
        "pregame_resting_limit_order",
        "first_live_window_after_event_start",
        "post_q1_plus_market_stability_confirmation",
        "late_game_min_price_add",
    }
    thunder = next(row for row in matrix.rows if row.source_case_id == "nba-okc-sas-thunder-q4-subpenny-negative")
    assert thunder.recommendation == "negative_bucket_quarantine"
    assert "final_score_negative_edge" in thunder.blockers
    pregame = next(row for row in matrix.rows if row.timing_policy == "pregame_resting_limit_order")
    assert "event_start_expiry_not_scored" in pregame.blockers
    assert "do_not_start_live_money_workers" in matrix.hard_prohibitions
    side_by_side = {summary.timing_policy: summary for summary in matrix.side_by_side_policy_summaries}
    assert side_by_side["pregame_resting_limit_order"].cancelled_or_expired_count == 4
    assert side_by_side["pregame_resting_limit_order"].missed_entry_count == 3
    assert side_by_side["pregame_resting_limit_order"].avoided_loss_usd == 3.03
    assert side_by_side["first_live_window_after_event_start"].filled_case_count == 4
    assert side_by_side["first_live_window_after_event_start"].adverse_selection_count == 1
    assert side_by_side["post_q1_plus_market_stability_confirmation"].filled_case_count == 0


def test_entry_timing_matrix_reads_latest_fixture_and_persists_outputs_pytest(tmp_path) -> None:
    root = tmp_path / "artifacts"
    fixture_root = root / "postgame-replay-config-review" / "2026-05-25"
    fixture_root.mkdir(parents=True)
    fixture_path = fixture_root / "postgame_replay_fixture_backtest_20260525T055152Z.json"
    fixture_path.write_text(json.dumps(_fixture_backtest_payload()), encoding="utf-8")

    matrix = build_entry_timing_matrix(day="2026-05-25", artifact_root=root)
    result = write_entry_timing_matrix(matrix, artifact_root=root, report_dir=tmp_path / "reports")

    assert result["status"] == "stored"
    assert result["row_count"] == 6
    index_path = root / "entry-timing-research" / "2026-05-25" / "entry_timing_matrices.jsonl"
    index_row = json.loads(index_path.read_text(encoding="utf-8").splitlines()[0])
    assert index_row["eligible_case_count"] == 3
    assert index_row["side_by_side_result_count"] == 16
    markdown = list((tmp_path / "reports").glob("entry_timing_matrix_*.md"))[0].read_text(encoding="utf-8")
    assert "Entry Timing Matrix" in markdown
    assert "Side-By-Side Policy Summaries" in markdown
    assert "post_q1_plus_market_stability_confirmation" in render_entry_timing_matrix_markdown(matrix)


def test_entry_timing_matrix_uses_live_worker_price_path_replay_pytest(tmp_path) -> None:
    ticks_path = tmp_path / "ticks.jsonl"
    _write_live_worker_ticks(ticks_path)

    matrix = build_entry_timing_matrix_from_fixture_backtest(
        _fixture_backtest_payload(),
        source_path="fixture.json",
        live_worker_ticks_path=ticks_path,
    )

    assert matrix.acceptance_progress["includes_real_price_path_replay"] is True
    assert matrix.acceptance_progress["price_path_replay_case_count"] == 4
    assert matrix.acceptance_progress["price_path_entry_fill_case_count"] == 4
    assert matrix.acceptance_progress["price_path_stability_fill_case_count"] == 3
    assert matrix.acceptance_progress["includes_order_lifecycle_replay"] is True
    seattle = next(replay for replay in matrix.price_path_replays if replay.case_id == "wnba-wsh-sea-seattle-q1-rebound")
    assert seattle.entry_fill_observed is True
    assert seattle.stability_fill_observed is False
    assert "stability_entry_window_not_seen_in_real_price_path" in seattle.blockers
    thunder = next(replay for replay in matrix.price_path_replays if replay.case_id == "nba-okc-sas-thunder-q4-subpenny-negative")
    assert thunder.event_start_expired_order_count == 3
    assert thunder.order_lifecycle_status == "event_start_expired_orders_observed"
    post_q1 = [
        result
        for result in matrix.side_by_side_results
        if result.timing_policy == "post_q1_entry" and result.case_id == "wnba-phx-atl-atlanta-comeback-low-band"
    ][0]
    assert post_q1.evaluation_basis == "price_path_replay_at_entry_price"
    assert "post_q1_price_path_proxy_only" not in post_q1.blockers
    stability = {summary.timing_policy: summary for summary in matrix.side_by_side_policy_summaries}
    assert stability["post_q1_plus_market_stability_confirmation"].filled_case_count == 3


def _write_live_worker_ticks(path) -> None:
    strategies = {
        "wnba-phx-atl-2026-05-24": ("atl-id", "Atlanta Dream"),
        "wnba-dal-nyl-2026-05-24": ("dal-id", "Dallas Wings"),
        "wnba-wsh-sea-2026-05-24": ("sea-id", "Seattle Storm"),
        "nba-okc-sas-2026-05-24": ("okc-id", "Thunder"),
    }
    prices = [
        {
            "wnba-phx-atl-2026-05-24": 0.31,
            "wnba-dal-nyl-2026-05-24": 0.23,
            "wnba-wsh-sea-2026-05-24": 0.26,
            "nba-okc-sas-2026-05-24": 0.005,
        },
        {
            "wnba-phx-atl-2026-05-24": 0.30,
            "wnba-dal-nyl-2026-05-24": 0.22,
            "wnba-wsh-sea-2026-05-24": 0.26,
            "nba-okc-sas-2026-05-24": 0.005,
        },
        {
            "wnba-phx-atl-2026-05-24": 0.29,
            "wnba-dal-nyl-2026-05-24": 0.21,
            "wnba-wsh-sea-2026-05-24": 0.27,
            "nba-okc-sas-2026-05-24": 0.005,
        },
    ]
    lines = []
    for index, tick_prices in enumerate(prices):
        events = []
        for event_id, ask in tick_prices.items():
            outcome_id, label = strategies[event_id]
            events.append(
                {
                    "event_id": event_id,
                    "llm_runtime_trace": {
                        "revision_request": {
                            "current_plan": {
                                "active_strategies": [
                                    {
                                        "side": label,
                                        "entry_rules": {
                                            "outcome_id": outcome_id,
                                            "outcome_label": label,
                                        },
                                    }
                                ]
                            }
                        }
                    },
                    "orderbook_results": {
                        outcome_id: {
                            "best_bid": round(ask - 0.01, 4),
                            "best_ask": ask,
                            "spread": 0.01,
                        }
                    },
                    "portfolio_state": {
                        "event_start_expired_order_count": 3 if event_id == "nba-okc-sas-2026-05-24" else 0
                    },
                }
            )
        lines.append(
            json.dumps(
                {
                    "finished_at_utc": f"2026-05-25T00:0{index}:00+00:00",
                    "stdout": {"events": events},
                }
            )
        )
    path.write_text("\n".join(lines), encoding="utf-8")
