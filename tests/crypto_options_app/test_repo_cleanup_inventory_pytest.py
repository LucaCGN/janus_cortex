from __future__ import annotations

from crypto_options_app.reports import repo_cleanup_batches
from crypto_options_app.reports import repo_baseline_staging
from crypto_options_app.reports import repo_cleanup_inventory


def test_classify_cleanup_path() -> None:
    assert repo_cleanup_inventory.classify_cleanup_path("crypto_options_app/main.py") == "crypto_active"
    assert repo_cleanup_inventory.classify_cleanup_path("tests/crypto_options_app/test_x.py") == "crypto_active"
    assert (
        repo_cleanup_inventory.classify_cleanup_path("codex_tool/run_crypto_options_market_data.py")
        == "crypto_compatibility_wrapper_candidate"
    )
    assert (
        repo_cleanup_inventory.classify_cleanup_path("app/docs/reference/postgame_evaluation_wnba.md")
        == "wnba_nba_reference_candidate"
    )
    assert repo_cleanup_inventory.classify_cleanup_path("app/modules/agentic/store.py") == "global_reference_candidate"
    assert repo_cleanup_inventory.classify_cleanup_path(".github/workflows/ci.yml") == "github_review_required"
    assert repo_cleanup_inventory.classify_cleanup_path("requirements.txt") == "root_config_review"


def test_build_inventory_from_status_rows() -> None:
    report = repo_cleanup_inventory.build_repo_cleanup_inventory(
        status_rows=[
            {"status": "M", "path": "crypto_options_app/config.py"},
            {"status": "M", "path": "app/modules/agentic/store.py"},
            {"status": "??", "path": "codex_tool/run_crypto_options_market_data.py"},
            {"status": "??", "path": "app/docs/reference/postgame_evaluation_2026_wnba.md"},
            {"status": "M", "path": "requirements.txt"},
        ]
    )

    assert report["schema_version"] == "crypto_options_repo_cleanup_inventory_v1"
    assert report["status"] == "degraded"
    assert report["summary"]["dirty_path_count"] == 5
    assert report["summary"]["active_crypto_count"] == 1
    assert report["summary"]["legacy_move_candidate_count"] == 2
    assert report["summary"]["crypto_compatibility_wrapper_candidate_count"] == 1
    assert report["gates"]["automatic_moves_allowed"] is False
    assert report["gates"]["fixed_chats_start_ready"] is False


def test_render_markdown_includes_gates() -> None:
    report = repo_cleanup_inventory.build_repo_cleanup_inventory(
        status_rows=[
            {"status": "M", "path": "app/modules/agentic/store.py"},
        ]
    )

    markdown = repo_cleanup_inventory.render_repo_cleanup_inventory_markdown(report)

    assert "Crypto Options Repo Cleanup Inventory" in markdown
    assert "Fixed chats start ready" in markdown
    assert "review_move_to_global_reference" in markdown


def test_cleanup_batches_group_inventory_entries() -> None:
    inventory = repo_cleanup_inventory.build_repo_cleanup_inventory(
        status_rows=[
            {"status": "M", "path": "crypto_options_app/config.py"},
            {"status": "M", "path": "app/modules/agentic/store.py"},
            {"status": "??", "path": "codex_tool/run_crypto_options_market_data.py"},
            {"status": "??", "path": "app/docs/reference/postgame_evaluation_2026_wnba.md"},
            {"status": "M", "path": "requirements.txt"},
            {"status": "??", "path": ".github/workflows/ci.yml"},
        ]
    )

    report = repo_cleanup_batches.build_repo_cleanup_batches(inventory)

    assert report["schema_version"] == "crypto_options_repo_cleanup_batches_v1"
    assert report["gates"]["automatic_moves_allowed"] is False
    assert report["batches"]["batch_0_active_crypto_baseline"]["entry_count"] == 1
    assert report["batches"]["batch_1_local_state_root_config_review"]["entry_count"] == 1
    assert report["batches"]["batch_2_wnba_nba_reference_move"]["entry_count"] == 1
    assert report["batches"]["batch_3_global_reference_move"]["entry_count"] == 1
    assert report["batches"]["batch_4_crypto_compatibility_wrapper_cutover"]["entry_count"] == 1
    assert report["batches"]["batch_5_github_source_of_truth_setup"]["entry_count"] == 1


def test_cleanup_batches_markdown_mentions_branch() -> None:
    inventory = repo_cleanup_inventory.build_repo_cleanup_inventory(
        status_rows=[
            {"status": "M", "path": "crypto_options_app/config.py"},
        ]
    )
    report = repo_cleanup_batches.build_repo_cleanup_batches(inventory)

    markdown = repo_cleanup_batches.render_repo_cleanup_batches_markdown(report)

    assert "codex/crypto-transition-control-plane" in markdown
    assert "No-Move Active Crypto Baseline" in markdown


def test_baseline_staging_classifies_source_and_artifacts() -> None:
    assert repo_baseline_staging.classify_baseline_path("crypto_options_app/api/app.py") == "stage_source"
    assert (
        repo_baseline_staging.classify_baseline_path("tests/crypto_options_app/test_app_pytest.py")
        == "stage_tests"
    )
    assert (
        repo_baseline_staging.classify_baseline_path("crypto_options_app/docs/reference/spec.md")
        == "stage_docs_specs"
    )
    assert (
        repo_baseline_staging.classify_baseline_path("crypto_options_app/artifacts/team_coordination/master_status.md")
        == "stage_coordination_artifacts"
    )
    assert (
        repo_baseline_staging.classify_baseline_path(
            "crypto_options_app/artifacts/reports/transition_readiness_latest.md"
        )
        == "stage_transition_reports"
    )
    assert (
        repo_baseline_staging.classify_baseline_path("crypto_options_app/artifacts/automation/status.json")
        == "hold_generated_artifact"
    )
    assert repo_baseline_staging.classify_baseline_path("crypto_options_app/data/x.sqlite") == "hold_data_file"


def test_baseline_staging_plan_splits_stage_and_hold() -> None:
    batches = {
        "generated_at_utc": "2026-06-07T00:00:00+00:00",
        "batches": {
            "batch_0_active_crypto_baseline": {
                "entries": [
                    {"status": "??", "path": "crypto_options_app/api/app.py"},
                    {"status": "??", "path": "tests/crypto_options_app/test_app_pytest.py"},
                    {"status": "??", "path": "crypto_options_app/artifacts/automation/status.json"},
                    {"status": "??", "path": "crypto_options_app/data/crypto_options_data.sqlite"},
                ]
            }
        },
    }

    report = repo_baseline_staging.build_repo_baseline_staging_plan(batches)

    assert report["schema_version"] == "crypto_options_repo_baseline_staging_v1"
    assert report["summary"]["stage_candidate_count"] == 2
    assert report["summary"]["hold_count"] == 2
    assert report["gates"]["automatic_stage_allowed"] is False
