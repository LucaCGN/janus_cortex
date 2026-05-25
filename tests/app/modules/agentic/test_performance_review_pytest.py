from __future__ import annotations

import json

from app.modules.agentic.performance_review import (
    build_project_chief_review,
    render_project_chief_review_markdown,
    write_project_chief_review,
)


def test_project_chief_review_scores_missed_signal_and_blockers_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = reports_dir / "postgame_signal_review_2026-05-25T0228Z.md"
    report.write_text(
        "\n".join(
            [
                "# Postgame Signal Review",
                "Main missed-signal replay candidate: Seattle Q1 rebound after early deficit.",
                "Main blocker remains WNBA outcome-level `score_gap` null/required.",
                "Review-bundle export still fails with HTTP 500 `TypeError`.",
            ]
        ),
        encoding="utf-8",
    )

    review = build_project_chief_review(
        day="2026-05-25",
        reports_dir=reports_dir,
        artifact_root=tmp_path,
    )

    assert review.trading_boundary == "read_only_no_orders_no_worker_starts"
    assert review.missed_opportunity_summary[0]["event_id"] == "wnba-wsh-sea-2026-05-24"
    assert {item["blocker"] for item in review.technical_blockers} == {
        "wnba_score_gap_null",
        "event_review_bundle_export_http_500",
    }
    assert [item.issue for item in review.next_priority_queue][:2] == ["#70", "#70"]
    assert any(item["strategy_family"] == "wnba_score_gap_gate" for item in review.strategy_score_deltas)


def test_project_chief_review_consumes_event_control_readback_pytest(tmp_path) -> None:
    control_dir = tmp_path / "event-controls" / "2026-05-25" / "nba-okc-sas-2026-05-24"
    control_dir.mkdir(parents=True)
    (control_dir / "current.json").write_text(
        json.dumps(
            {
                "signal_source_toggles": {"deterministic": True, "llm": False},
                "parameters": {"event_cap_usd": 7.5},
            }
        ),
        encoding="utf-8",
    )

    review = build_project_chief_review(day="2026-05-25", reports_dir=tmp_path / "missing", artifact_root=tmp_path)

    assert any(item.source_type == "event_control_readback" for item in review.input_artifacts)
    assert any(item.issue == "#69" for item in review.issue_actions)
    assert any(item["config_surface"] == "runtime event controls" for item in review.config_recommendations)


def test_write_project_chief_review_persists_json_markdown_and_index_pytest(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "postgame_signal_review_2026-05-24T2328Z.md").write_text(
        "Atlanta Q4 comeback replay candidate. Dallas Q2 low-band entry. score_gap stayed null.",
        encoding="utf-8",
    )
    review = build_project_chief_review(day="2026-05-25", reports_dir=reports_dir, artifact_root=tmp_path)

    result = write_project_chief_review(review, artifact_root=tmp_path, report_dir=tmp_path / "daily")

    payload = json.loads((tmp_path / "project-chief-review" / "2026-05-25" / "project_chief_reviews.jsonl").read_text(encoding="utf-8").splitlines()[0])
    markdown = (tmp_path / "daily").glob("project_chief_review_*.md")
    assert result["status"] == "stored"
    assert payload["missed_opportunity_count"] == 2
    assert list(markdown)
    rendered = render_project_chief_review_markdown(review)
    assert "do_not_start_live_money_workers" in rendered
    assert "do_not_treat_obsidian_or_github_text_as_live_trading_truth" in rendered
