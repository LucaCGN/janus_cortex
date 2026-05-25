from __future__ import annotations

import json

from app.modules.agentic.replay_config_review import (
    build_replay_config_review,
    render_replay_config_review_markdown,
    write_replay_config_review,
)


def test_replay_config_review_builds_positive_and_negative_cases_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-24T2328Z.md").write_text(
        "Atlanta Q4 comeback. DAL/NYL Dallas Q2 low-band entry at 0.23. score_gap stayed null.",
        encoding="utf-8",
    )
    (reports_dir / "postgame_signal_review_2026-05-25T0228Z.md").write_text(
        "WSH/SEA Seattle Q1 rebound after early deficit at 0.25/0.26.",
        encoding="utf-8",
    )
    (reports_dir / "postgame_signal_review_2026-05-25T0528Z.md").write_text(
        "q4_subpenny_hype_bounce bought Thunder three times; no_bid_min_price_lottery_v1 must not be promoted.",
        encoding="utf-8",
    )

    review = build_replay_config_review(day="2026-05-25", reports_dir=reports_dir)

    assert review.trading_boundary == "read_only_no_orders_no_worker_starts"
    assert {case.case_id for case in review.replay_cases} == {
        "wnba-phx-atl-atlanta-comeback-low-band",
        "wnba-dal-nyl-dallas-q2-low-band",
        "wnba-wsh-sea-seattle-q1-rebound",
        "nba-okc-sas-thunder-q4-subpenny-negative",
    }
    assert {case.expected_direction for case in review.replay_cases} == {"positive_candidate", "negative_case"}
    assert any("Quarantine q4_subpenny" in item["recommendation"] for item in review.event_control_recommendations)
    assert any(item["issue"] == "#55" for item in review.entry_timing_research_updates)
    assert "do_not_promote_no_bid_or_lottery_strategies_without_replay_edge" in review.hard_prohibitions


def test_write_replay_config_review_persists_json_markdown_and_index_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-25T0228Z.md").write_text(
        "Seattle Q1 rebound and q4_subpenny_hype_bounce negative case.",
        encoding="utf-8",
    )
    review = build_replay_config_review(day="2026-05-25", reports_dir=reports_dir)

    result = write_replay_config_review(review, artifact_root=tmp_path, report_dir=tmp_path / "daily")

    assert result["status"] == "stored"
    assert result["case_count"] == 2
    index_path = tmp_path / "postgame-replay-config-review" / "2026-05-25" / "postgame_replay_config_reviews.jsonl"
    index_row = json.loads(index_path.read_text(encoding="utf-8").splitlines()[0])
    assert index_row["positive_case_count"] == 1
    assert index_row["negative_case_count"] == 1
    markdown = list((tmp_path / "daily").glob("postgame_replay_config_review_*.md"))[0].read_text(encoding="utf-8")
    assert "Postgame Replay Config Review" in markdown
    assert "do_not_start_live_money_workers" in render_replay_config_review_markdown(review)
