from __future__ import annotations

import json

from app.modules.agentic.replay_config_review import (
    build_no_bid_min_price_lottery_study,
    build_replay_config_review,
    build_replay_fixture_backtest,
    render_no_bid_min_price_lottery_study_markdown,
    render_replay_fixture_backtest_markdown,
    render_replay_config_review_markdown,
    write_no_bid_min_price_lottery_study,
    write_replay_fixture_backtest,
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


def test_replay_fixture_backtest_scores_cases_without_live_promotion_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-25T0528Z.md").write_text(
        "Atlanta comeback. Dallas Q2 low-band. Seattle Q1 rebound. q4_subpenny_hype_bounce Thunder loss.",
        encoding="utf-8",
    )
    review = build_replay_config_review(day="2026-05-25", reports_dir=reports_dir)

    backtest = build_replay_fixture_backtest(review)

    assert backtest.trading_boundary == "read_only_no_orders_no_worker_starts"
    assert backtest.source_case_count == 4
    assert backtest.summary["eligible_positive_case_count"] == 3
    assert backtest.summary["quarantined_case_ids"] == ["nba-okc-sas-thunder-q4-subpenny-negative"]
    assert backtest.summary["live_promotion_allowed"] is False
    thunder = next(result for result in backtest.results if result.case_id == "nba-okc-sas-thunder-q4-subpenny-negative")
    assert thunder.final_score_pnl_usd < 0
    assert "duplicate_intent_cooldown_required" in thunder.blockers
    assert thunder.recommendation == "quarantine_until_independent_replay_proves_edge"
    assert all(
        result.recommendation == "eligible_for_entry_timing_matrix_not_live_promotion"
        for result in backtest.results
        if result.expected_direction == "positive_candidate"
    )


def test_write_replay_fixture_backtest_persists_json_markdown_and_index_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-25T0528Z.md").write_text(
        "Seattle Q1 rebound and q4_subpenny_hype_bounce negative case.",
        encoding="utf-8",
    )
    backtest = build_replay_fixture_backtest(build_replay_config_review(day="2026-05-25", reports_dir=reports_dir))

    result = write_replay_fixture_backtest(backtest, artifact_root=tmp_path, report_dir=tmp_path / "daily")

    assert result["status"] == "stored"
    assert result["result_count"] == 2
    index_path = tmp_path / "postgame-replay-config-review" / "2026-05-25" / "postgame_replay_fixture_backtests.jsonl"
    index_row = json.loads(index_path.read_text(encoding="utf-8").splitlines()[0])
    assert index_row["quarantined_case_count"] == 1
    markdown = list((tmp_path / "daily").glob("postgame_replay_fixture_backtest_*.md"))[0].read_text(
        encoding="utf-8"
    )
    assert "Postgame Replay Fixture Backtest" in markdown
    assert "do_not_start_live_money_workers" in render_replay_fixture_backtest_markdown(backtest)


def test_no_bid_min_price_lottery_study_keeps_hype_rebound_quarantined_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-25T0528Z.md").write_text(
        "q4_subpenny_hype_bounce bought Thunder three times near min price. "
        "The human-observed 2c-4c rebound was attractive, but Thunder lost and no_bid_min_price_lottery_v1 must not be promoted.",
        encoding="utf-8",
    )
    backtest = build_replay_fixture_backtest(build_replay_config_review(day="2026-05-25", reports_dir=reports_dir))

    study = build_no_bid_min_price_lottery_study(backtest)

    assert study.schema_version == "no_bid_min_price_lottery_study_v1"
    assert study.trading_boundary == "read_only_replay_no_runtime_mutation"
    assert study.summary["human_observed_rebound_count"] == 1
    assert study.summary["reproducible_positive_case_count"] == 0
    assert study.summary["quarantined_case_ids"] == ["nba-okc-sas-thunder-q4-subpenny-negative"]
    assert study.summary["runtime_mutation_allowed"] is False
    assert study.summary["live_promotion_allowed"] is False
    case = study.cases[0]
    assert case.entry_fillability_status == "entry_fillable_from_observed_direct_clob"
    assert case.exit_fillability_status == "target_exit_unproven_no_live_reconciled_fill"
    assert case.human_observed_rebound_status == "hype_rebound_observed_but_exit_unproven"
    assert case.reproducible_edge_status == "quarantine_disabled"
    assert "independent_positive_replay_missing" in case.blockers
    assert "target_fill_observed_as_theoretical_not_reconciled_exit" in case.blockers
    assert case.event_control_recommendation["recommended_signal_toggles"]["no_bid_min_price_lottery_v1"] is False
    assert case.event_control_recommendation["runtime_mutation_allowed"] is False
    assert "do_not_update_event_control_current_json_from_this_artifact" in study.hard_prohibitions


def test_write_no_bid_min_price_lottery_study_persists_json_markdown_and_index_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-25T0528Z.md").write_text(
        "q4_subpenny_hype_bounce Thunder loss with no_bid_min_price_lottery_v1 quarantine.",
        encoding="utf-8",
    )
    backtest = build_replay_fixture_backtest(build_replay_config_review(day="2026-05-25", reports_dir=reports_dir))
    study = build_no_bid_min_price_lottery_study(backtest)

    result = write_no_bid_min_price_lottery_study(study, artifact_root=tmp_path, report_dir=tmp_path / "daily")

    assert result["status"] == "stored"
    assert result["study_case_count"] == 1
    index_path = (
        tmp_path
        / "postgame-replay-config-review"
        / "2026-05-25"
        / "no_bid_min_price_lottery_studies.jsonl"
    )
    index_row = json.loads(index_path.read_text(encoding="utf-8").splitlines()[0])
    assert index_row["reproducible_positive_case_count"] == 0
    assert index_row["quarantined_case_count"] == 1
    markdown = list((tmp_path / "daily").glob("no_bid_min_price_lottery_study_*.md"))[0].read_text(
        encoding="utf-8"
    )
    assert "No-Bid Min-Price Lottery Study" in markdown
    assert "quarantine_disabled" in markdown
    assert "do_not_start_live_money_workers" in render_no_bid_min_price_lottery_study_markdown(study)
