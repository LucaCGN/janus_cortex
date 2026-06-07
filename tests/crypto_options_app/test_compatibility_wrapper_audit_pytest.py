from __future__ import annotations

from pathlib import Path

from crypto_options_app.reports.compatibility_wrapper_audit import build_compatibility_wrapper_audit
from crypto_options_app.reports.compatibility_wrapper_audit import render_compatibility_wrapper_audit_markdown


def test_compatibility_audit_blocks_active_referenced_wrappers(tmp_path: Path) -> None:
    repo = tmp_path
    active_script = repo / "crypto_options_app" / "scripts" / "run.py"
    active_script.parent.mkdir(parents=True)
    active_script.write_text(
        "from app.data.pipelines.crypto.options.live_review import build_live_review\n",
        encoding="utf-8",
    )
    wrapper = repo / "app" / "data" / "pipelines" / "crypto" / "options" / "live_review.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("def build_live_review():\n    return None\n", encoding="utf-8")
    batches = {
        "generated_at_utc": "2026-06-07T00:00:00+00:00",
        "batches": {
            "batch_4_crypto_compatibility_wrapper_cutover": {
                "entries": [
                    {
                        "status": "??",
                        "path": "app/data/pipelines/crypto/options/live_review.py",
                        "proposed_destination": "crypto_options_app/compatibility/app/data/pipelines/crypto/options/live_review.py",
                    }
                ]
            }
        },
    }

    report = build_compatibility_wrapper_audit(batches, repo_root=repo)

    wrapper_report = report["wrappers"][0]
    assert report["summary"]["wrapper_count"] == 1
    assert report["gates"]["automatic_wrapper_moves_allowed"] is False
    assert wrapper_report["active_reference_count"] == 1
    assert wrapper_report["recommended_decision"] == "keep_temporarily_cut_over_active_callers"


def test_compatibility_audit_replaces_script_wrapper_when_central_script_exists(tmp_path: Path) -> None:
    repo = tmp_path
    central_script = repo / "crypto_options_app" / "scripts" / "run_crypto_options_market_data.py"
    central_script.parent.mkdir(parents=True)
    central_script.write_text("def main():\n    return 0\n", encoding="utf-8")
    wrapper = repo / "codex_tool" / "run_crypto_options_market_data.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("from crypto_options_app.scripts.run_crypto_options_market_data import main\n", encoding="utf-8")
    batches = {
        "generated_at_utc": "2026-06-07T00:00:00+00:00",
        "batches": {
            "batch_4_crypto_compatibility_wrapper_cutover": {
                "entries": [{"status": "??", "path": "codex_tool/run_crypto_options_market_data.py"}]
            }
        },
    }

    report = build_compatibility_wrapper_audit(batches, repo_root=repo)

    wrapper_report = report["wrappers"][0]
    assert wrapper_report["matching_central_path"] == "crypto_options_app/scripts/run_crypto_options_market_data.py"
    assert wrapper_report["recommended_decision"] == "replace_with_crypto_options_app_script_entrypoint"


def test_compatibility_audit_markdown_includes_fixed_chat_gates(tmp_path: Path) -> None:
    report = build_compatibility_wrapper_audit(
        {"batches": {"batch_4_crypto_compatibility_wrapper_cutover": {"entries": []}}},
        repo_root=tmp_path,
    )

    markdown = render_compatibility_wrapper_audit_markdown(report)

    assert "frontend_fixed_chat_can_start_after_batch_3" in markdown
    assert "signal_strategy_fixed_chat_requires_batch_4_and_github_source_of_truth" in markdown
    assert "fixed_chat_frontend.md" in markdown
