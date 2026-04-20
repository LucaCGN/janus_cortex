from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.routers.analysis_studio as analysis_studio_router
from app.api.main import create_app


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_consumer_fixture(root: Path) -> None:
    version = "v1_2_0"
    version_dir = root / "2025-26" / "regular_season" / version
    _write_json(
        version_dir / "analysis_report.json",
        {
            "season": "2025-26",
            "season_phase": "regular_season",
            "analysis_version": version,
            "universe": {
                "games_total": 1224,
                "research_ready_games": 1198,
                "descriptive_only_games": 26,
                "excluded_games": 0,
                "coverage_status_counts": {
                    "covered_pre_and_ingame": 1198,
                    "covered_partial": 13,
                    "no_history": 10,
                    "no_matching_event": 2,
                    "pregame_only": 1,
                },
            },
            "section_order": ["teams_against_expectation"],
            "teams_against_expectation": [
                {"team_slug": "ATL", "sample_games": 82, "avg_expectation_gap_abs": 0.19}
            ],
            "artifacts": {"json": str(version_dir / "analysis_report.json")},
        },
    )
    _write_json(
        version_dir / "backtests" / "run_analysis_backtests.json",
        {
            "season": "2025-26",
            "season_phase": "regular_season",
            "analysis_version": version,
            "experiment": {"experiment_id": "exp-frontend"},
            "benchmark": {
                "contract_version": "v1",
                "minimum_trade_count": 20,
                "family_summary": [
                    {
                        "sample_name": "full_sample",
                        "strategy_family": "reversion",
                        "entry_rule": "favorite_drawdown_buy_10c",
                        "trade_count": 60,
                        "avg_gross_return_with_slippage": 0.081,
                    }
                ],
                "candidate_freeze": [
                    {
                        "strategy_family": "reversion",
                        "candidate_label": "keep",
                        "label_reason": "positive_on_full_time_and_holdout",
                    }
                ],
                "split_summary": [{"sample_name": "full_sample", "games_considered": 1224}],
                "comparators": [],
                "comparator_summary": [],
                "context_rankings": [],
            },
            "artifacts": {"json": str(version_dir / "backtests" / "run_analysis_backtests.json")},
        },
    )
    _write_json(
        version_dir / "models" / "train_analysis_baselines.json",
        {
            "season": "2025-26",
            "season_phase": "regular_season",
            "analysis_version": version,
            "feature_set_version": version,
            "train_cutoff": "2026-03-10T00:00:00",
            "validation_window": None,
            "tracks": {
                "trade_window_quality": {
                    "status": "success",
                    "model_family": "ols_regression_baseline",
                    "train_rows": 4000,
                    "validation_rows": 1200,
                    "targets": {
                        "mfe_from_state": {
                            "rmse": 0.12,
                            "mae": 0.08,
                            "rank_corr": 0.36,
                        }
                    },
                }
            },
            "artifacts": {"json": str(version_dir / "models" / "train_analysis_baselines.json")},
        },
    )


def _write_validation_summary(local_root: Path) -> None:
    summary_root = local_root / "archives" / "output" / "nba_analysis_validation" / "20260420_020000"
    _write_json(
        summary_root / "validation_summary.json",
        {
            "target": "disposable",
            "season": "2025-26",
            "season_phase": "regular_season",
            "analysis_version": "v1_2_0",
            "all_commands_ok": True,
            "output_root": str(summary_root),
            "commands": [
                {"name": "analysis_pytest_sweep", "ok": True, "exit_code": 0, "duration_seconds": 5.0}
            ],
            "parsed_outputs": {
                "collect_validation_snapshot": {
                    "database_target": {"name": "disposable"},
                    "consumer_snapshot": {
                        "benchmark_contract_version": "v1",
                        "benchmark_experiment_id": "exp-frontend",
                        "model_track_count": 1,
                        "output_dir": str(summary_root / "2025-26" / "regular_season" / "v1_2_0"),
                        "report_section_count": 1,
                    },
                    "universe": {
                        "games_total": 1224,
                        "research_ready_games": 1198,
                        "descriptive_only_games": 26,
                        "excluded_games": 0,
                    },
                }
            },
        },
    )
    (summary_root / "validation_summary.md").write_text("# validation", encoding="utf-8")


def test_analysis_studio_index_route_serves_html_pytest() -> None:
    client = TestClient(create_app())
    response = client.get("/analysis-studio")
    assert response.status_code == 200
    assert "Janus Cortex Analysis Studio" in response.text
    assert "Launch guarded local analysis commands" in response.text
    assert "/analysis-studio/static/analysis_studio.js" in response.text


def test_analysis_studio_snapshot_route_loads_consumer_snapshot_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path)
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/snapshot",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_version"] == "v1_2_0"
    assert payload["benchmark"]["strategy_rankings"][0]["strategy_family"] == "reversion"
    assert payload["report"]["sections"][0]["key"] == "teams_against_expectation"


def test_analysis_studio_snapshot_route_maps_missing_snapshot_to_404_pytest(tmp_path: Path) -> None:
    client = TestClient(create_app())
    response = client.get(
        "/v1/analysis/studio/snapshot",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
        },
    )

    assert response.status_code == 404
    assert "No analysis output versions found" in response.json()["error"]["message"]


def test_analysis_studio_control_route_reports_versions_and_latest_validation_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(tmp_path))
    analysis_studio_router._RUN_REGISTRY.clear()
    _write_consumer_fixture(tmp_path / "archives" / "output" / "nba_analysis")
    _write_validation_summary(tmp_path)

    client = TestClient(create_app())
    response = client.get(
        "/v1/analysis/studio/control",
        params={"season": "2025-26", "season_phase": "regular_season"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available_analysis_versions"] == ["v1_2_0"]
    assert payload["latest_validation"]["run_label"] == "20260420_020000"
    assert payload["latest_validation"]["consumer_snapshot"]["benchmark_experiment_id"] == "exp-frontend"
    assert payload["latest_analysis_output_dir"].endswith("v1_2_0")


def test_analysis_studio_run_route_queues_record_and_lists_it_pytest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(tmp_path))
    analysis_studio_router._RUN_REGISTRY.clear()

    def _fake_launch(record: dict[str, object], request: analysis_studio_router.AnalysisStudioRunRequest) -> None:
        analysis_studio_router._update_run_record(
            str(record["run_id"]),
            status="running",
            started_at="2026-04-20T00:00:00+00:00",
            pid=4321,
            command=["python", "-m", "fake"],
            output_root=str(tmp_path / "run_output"),
        )

    monkeypatch.setattr(analysis_studio_router, "_launch_analysis_studio_run", _fake_launch)
    client = TestClient(create_app())

    create_response = client.post(
        "/v1/analysis/studio/runs",
        json={
            "action": "build_analysis_report",
            "season": "2025-26",
            "season_phase": "regular_season",
            "analysis_version": "v1_2_0",
            "validation_target": "disposable",
            "rebuild": False,
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["status"] == "running"
    assert created["action"] == "build_analysis_report"

    list_response = client.get("/v1/analysis/studio/runs")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["run_id"] == created["run_id"]

    detail_response = client.get(f"/v1/analysis/studio/runs/{created['run_id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["pid"] == 4321
