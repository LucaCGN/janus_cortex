from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.agentic.global_portfolio import (
    GlobalPortfolioWatchlistEntry,
    build_watchlist_artifact,
    load_watchlist_source,
    render_watchlist_report,
)
from tools.build_global_portfolio_watchlist import build_from_source, write_outputs


def test_watchlist_entry_rejects_execution_authority_pytest() -> None:
    with pytest.raises(ValueError, match="cannot authorize execution"):
        GlobalPortfolioWatchlistEntry(
            watch_id="bad-row",
            market_title="Unauthorized test market",
            group="operator-manual",
            execution_authorized=True,
        )

    with pytest.raises(ValueError, match="cannot authorize order preparation"):
        GlobalPortfolioWatchlistEntry(
            watch_id="bad-row",
            market_title="Unauthorized test market",
            group="operator-manual",
            order_preparation_authorized=True,
        )


def test_build_watchlist_artifact_adds_review_questions_and_summary_pytest() -> None:
    artifact = build_watchlist_artifact(
        [
            {
                "market_title": "Long-term finals winner",
                "market_slug": "nba-finals-winner-2026",
                "outcome": "Thunder",
                "group": "operator-manual",
                "target_state": "target_stale",
                "risk_bucket": "operator-manual",
                "source_evidence": ["direct_clob_position_snapshot"],
            },
            {
                "market_title": "BTC up or down May 18",
                "market_slug": "btc-updown-2026-05-18",
                "group": "future-domain-candidate",
                "target_state": "target_unknown",
                "source_evidence": ["profile_study_hypothesis"],
            },
        ],
        source_caveats=["fixture_only"],
        generated_at_utc="2026-05-18T11:15:00Z",
    )

    assert artifact.schema_version == "global_portfolio_watchlist_v1"
    assert artifact.execution_authorized is False
    assert artifact.order_preparation_authorized is False
    assert artifact.summary["entry_count"] == 2
    assert artifact.summary["needs_operator_review_count"] == 1
    assert artifact.summary["policy_flags"] == {
        "future_domain_watch_only": 1,
        "operator_manual_review": 1,
        "target_stale": 1,
    }
    assert artifact.summary["target_policy"]["target_uncovered_or_stale_rows"] == 1
    assert artifact.entries[0].source_actor == "operator"
    assert artifact.entries[0].operator_review_questions == ["target requires operator review before any action"]
    assert artifact.entries[1].risk_bucket == "future-domain"


def test_load_watchlist_source_accepts_object_and_list_pytest() -> None:
    entries, caveats = load_watchlist_source(
        {
            "source_caveats": ["direct_access_missing"],
            "entries": [{"market_title": "Culture market", "group": "watch-only"}],
        }
    )

    assert entries == [{"market_title": "Culture market", "group": "watch-only"}]
    assert caveats == ["direct_access_missing"]
    list_entries, list_caveats = load_watchlist_source([{"market_title": "Macro market", "group": "watch-only"}])
    assert list_entries == [{"market_title": "Macro market", "group": "watch-only"}]
    assert list_caveats == []


def test_tool_writes_schema_artifact_and_report_pytest(tmp_path: Path) -> None:
    source = tmp_path / "watchlist_source.json"
    source.write_text(
        json.dumps(
            {
                "source_caveats": ["fixture_not_direct_clob_truth"],
                "entries": [
                    {
                        "market_title": "Global watch market",
                        "market_slug": "global-watch-market",
                        "outcome": "Yes",
                        "group": "watch-only",
                        "target_state": "target_missing",
                        "source_evidence": ["operator_watch_note"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    artifact = build_from_source(source_json=source, generated_at_utc="2026-05-18T11:15:00Z")
    paths = write_outputs(
        artifact,
        output_dir=tmp_path / "artifacts",
        report_path=tmp_path / "global_portfolio_watchlist_schema_2026-05-18.md",
    )

    artifact_path = Path(paths["artifact_path"])
    report_path = Path(paths["report_path"])
    assert artifact_path.exists()
    assert report_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["no_execution_statement"] == "No execution is authorized by this artifact."
    assert payload["summary"]["target_states"] == {"target_missing": 1}
    assert payload["summary"]["policy_flags"] == {"target_missing": 1}
    report = report_path.read_text(encoding="utf-8")
    assert "No orders were placed" in report
    assert "Direct CLOB/account truth remains required" in report
    assert "Target Policy Review" in report
    assert "review_only_no_execution" in report


def test_render_watchlist_report_handles_empty_schema_pytest() -> None:
    artifact = build_watchlist_artifact([], generated_at_utc="2026-05-18T11:15:00Z")

    report = render_watchlist_report(artifact)

    assert "| none | watch-only | No source rows supplied |" in report
    assert "No execution is authorized by this artifact." in report


def test_artifact_flags_paired_yes_no_exposure_pytest() -> None:
    artifact = build_watchlist_artifact(
        [
            {
                "market_title": "Will Team A win the title?",
                "market_slug": "team-a-title-2026",
                "outcome": "Yes",
                "side": "yes",
                "group": "operator-manual",
                "target_state": "target_present",
                "current_target": {"side": "sell", "limit_price": 0.5},
                "source_evidence": ["direct_clob_position_snapshot"],
            },
            {
                "market_title": "Will Team A win the title?",
                "market_slug": "team-a-title-2026",
                "outcome": "No",
                "side": "no",
                "group": "operator-manual",
                "target_state": "target_missing",
                "source_evidence": ["direct_clob_position_snapshot"],
            },
        ],
        generated_at_utc="2026-05-18T11:15:00Z",
    )

    assert artifact.summary["target_policy"]["paired_exposure_rows"] == 2
    assert artifact.summary["policy_flags"]["paired_yes_no_exposure"] == 2
    assert "paired_yes_no_exposure" in artifact.entries[0].policy_flags
    assert "target_present" in artifact.entries[0].policy_flags
    assert "target_missing" in artifact.entries[1].policy_flags
    assert artifact.entries[0].operator_review_questions == [
        "Resolve paired Yes/No exposure before interpreting directional thesis."
    ]
