from __future__ import annotations

from crypto_options_app.reports.compatibility_wrapper_decisions import (
    build_compatibility_wrapper_decision_plan,
    render_compatibility_wrapper_decision_plan_markdown,
)


def test_decision_plan_blocks_when_active_imports_remain() -> None:
    audit = {
        "generated_at_utc": "2026-06-07T00:00:00+00:00",
        "wrappers": [
            {
                "path": "app/data/pipelines/crypto/options/live_review.py",
                "family": "legacy_options_pipeline",
                "recommended_decision": "keep_temporarily_cut_over_active_callers",
                "active_reference_count": 1,
                "reference_count": 1,
            }
        ],
    }

    report = build_compatibility_wrapper_decision_plan(audit)

    assert report["status"] == "blocked"
    assert report["gates"]["active_import_blockers_cleared"] is False
    assert report["gates"]["github_issue_creation_can_start_after_commit"] is False
    assert report["decisions"][0]["bucket"] == "active_import_blocker"


def test_decision_plan_allows_github_gate_after_active_import_cutover() -> None:
    audit = {
        "generated_at_utc": "2026-06-07T00:00:00+00:00",
        "wrappers": [
            {
                "path": "codex_tool/run_crypto_options_market_data.py",
                "family": "legacy_cli_script_wrapper",
                "recommended_decision": "replace_with_crypto_options_app_script_entrypoint",
                "active_reference_count": 0,
                "reference_count": 0,
            },
            {
                "path": "tests/app/services/crypto_options/test_service_pytest.py",
                "family": "legacy_crypto_test_wrapper",
                "recommended_decision": "migrate_or_drop_duplicate_test_after_active_suite_mapping",
                "active_reference_count": 0,
                "reference_count": 0,
            },
        ],
    }

    report = build_compatibility_wrapper_decision_plan(audit)
    markdown = render_compatibility_wrapper_decision_plan_markdown(report)

    assert report["status"] == "review"
    assert report["gates"]["active_import_blockers_cleared"] is True
    assert report["gates"]["github_issue_creation_can_start_after_commit"] is True
    assert report["gates"]["signal_strategy_fixed_chat_ready"] is False
    assert report["summary"]["bucket_counts"] == {
        "replace_cli_wrapper_with_central_entrypoint": 1,
        "review_legacy_duplicate_tests": 1,
    }
    assert "GitHub milestones/issues" in markdown
