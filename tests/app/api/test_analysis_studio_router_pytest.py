from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.routers.analysis_studio as analysis_studio_router
from app.api.main import create_app


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
    _write_csv(
        version_dir / "nba_analysis_game_team_profiles.csv",
        [
            {
                "game_id": "game_001",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "ATL",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": version,
                "game_date": "2025-10-22",
                "game_start_time": "2025-10-22T23:30:00Z",
                "coverage_status": "covered_pre_and_ingame",
                "research_ready_flag": True,
                "price_path_reconciled_flag": True,
                "final_winner_flag": True,
                "opening_price": 0.67,
                "closing_price": 0.72,
                "opening_band": "favorite_60_70",
                "total_swing": 0.18,
                "inversion_count": 1,
                "max_favorable_excursion": 0.22,
                "max_adverse_excursion": -0.09,
                "winner_stable_80_clock_elapsed_seconds": 2320,
            },
            {
                "game_id": "game_001",
                "team_side": "away",
                "team_slug": "ATL",
                "opponent_team_slug": "BOS",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": version,
                "game_date": "2025-10-22",
                "game_start_time": "2025-10-22T23:30:00Z",
                "coverage_status": "covered_pre_and_ingame",
                "research_ready_flag": True,
                "price_path_reconciled_flag": True,
                "final_winner_flag": False,
                "opening_price": 0.33,
                "closing_price": 0.28,
                "opening_band": "underdog_30_40",
                "total_swing": 0.17,
                "inversion_count": 1,
                "max_favorable_excursion": 0.09,
                "max_adverse_excursion": -0.22,
                "winner_stable_80_clock_elapsed_seconds": None,
            },
            {
                "game_id": "game_002",
                "team_side": "home",
                "team_slug": "NYK",
                "opponent_team_slug": "MIA",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": version,
                "game_date": "2025-10-23",
                "game_start_time": "2025-10-23T23:30:00Z",
                "coverage_status": "no_history",
                "research_ready_flag": False,
                "price_path_reconciled_flag": False,
                "final_winner_flag": True,
                "opening_price": 0.58,
                "closing_price": 0.61,
                "opening_band": "favorite_50_60",
                "total_swing": 0.08,
                "inversion_count": 0,
                "max_favorable_excursion": 0.12,
                "max_adverse_excursion": -0.06,
                "winner_stable_80_clock_elapsed_seconds": 2400,
            },
            {
                "game_id": "game_002",
                "team_side": "away",
                "team_slug": "MIA",
                "opponent_team_slug": "NYK",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": version,
                "game_date": "2025-10-23",
                "game_start_time": "2025-10-23T23:30:00Z",
                "coverage_status": "no_history",
                "research_ready_flag": False,
                "price_path_reconciled_flag": False,
                "final_winner_flag": False,
                "opening_price": 0.42,
                "closing_price": 0.39,
                "opening_band": "underdog_40_50",
                "total_swing": 0.09,
                "inversion_count": 0,
                "max_favorable_excursion": 0.06,
                "max_adverse_excursion": -0.11,
                "winner_stable_80_clock_elapsed_seconds": None,
            },
            {
                "game_id": "game_003",
                "team_side": "home",
                "team_slug": "OKC",
                "opponent_team_slug": "LAL",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": version,
                "game_date": "2025-10-24",
                "game_start_time": "2025-10-24T23:30:00Z",
                "coverage_status": "covered_partial",
                "research_ready_flag": False,
                "price_path_reconciled_flag": False,
                "final_winner_flag": True,
                "opening_price": 0.74,
                "closing_price": 0.83,
                "opening_band": "favorite_70_80",
                "total_swing": 0.2,
                "inversion_count": 0,
                "max_favorable_excursion": 0.27,
                "max_adverse_excursion": -0.08,
                "winner_stable_80_clock_elapsed_seconds": 2190,
            },
            {
                "game_id": "game_003",
                "team_side": "away",
                "team_slug": "LAL",
                "opponent_team_slug": "OKC",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": version,
                "game_date": "2025-10-24",
                "game_start_time": "2025-10-24T23:30:00Z",
                "coverage_status": "covered_partial",
                "research_ready_flag": False,
                "price_path_reconciled_flag": False,
                "final_winner_flag": False,
                "opening_price": 0.26,
                "closing_price": 0.17,
                "opening_band": "underdog_20_30",
                "total_swing": 0.21,
                "inversion_count": 0,
                "max_favorable_excursion": 0.08,
                "max_adverse_excursion": -0.29,
                "winner_stable_80_clock_elapsed_seconds": None,
            },
        ],
    )
    _write_csv(
        version_dir / "nba_analysis_state_panel.csv",
        [
            {
                "game_id": "game_001",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "ATL",
                "state_index": 1,
                "event_at": "2025-10-22T23:31:00Z",
                "period": 1,
                "clock": "11:10",
                "score_for": 4,
                "score_against": 2,
                "score_diff": 2,
                "context_bucket": "small_lead",
                "team_price": 0.63,
                "price_delta_from_open": -0.04,
                "mfe_from_state": 0.14,
                "mae_from_state": -0.06,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
                "winner_stable_80_after_state_flag": False,
            },
            {
                "game_id": "game_001",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "ATL",
                "state_index": 2,
                "event_at": "2025-10-22T23:52:00Z",
                "period": 2,
                "clock": "04:40",
                "score_for": 39,
                "score_against": 37,
                "score_diff": 2,
                "context_bucket": "coin_flip",
                "team_price": 0.55,
                "price_delta_from_open": -0.12,
                "mfe_from_state": 0.18,
                "mae_from_state": -0.08,
                "large_swing_next_12_states_flag": True,
                "crossed_50c_next_12_states_flag": True,
                "winner_stable_80_after_state_flag": False,
            },
            {
                "game_id": "game_001",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "ATL",
                "state_index": 3,
                "event_at": "2025-10-23T00:58:00Z",
                "period": 4,
                "clock": "01:05",
                "score_for": 111,
                "score_against": 103,
                "score_diff": 8,
                "context_bucket": "closing_control",
                "team_price": 0.86,
                "price_delta_from_open": 0.19,
                "mfe_from_state": 0.02,
                "mae_from_state": -0.01,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
                "winner_stable_80_after_state_flag": True,
            },
            {
                "game_id": "game_001",
                "team_side": "away",
                "team_slug": "ATL",
                "opponent_team_slug": "BOS",
                "state_index": 1,
                "event_at": "2025-10-22T23:31:00Z",
                "period": 1,
                "clock": "11:10",
                "score_for": 2,
                "score_against": 4,
                "score_diff": -2,
                "context_bucket": "small_deficit",
                "team_price": 0.37,
                "price_delta_from_open": 0.04,
                "mfe_from_state": 0.06,
                "mae_from_state": -0.14,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
                "winner_stable_80_after_state_flag": False,
            },
            {
                "game_id": "game_001",
                "team_side": "away",
                "team_slug": "ATL",
                "opponent_team_slug": "BOS",
                "state_index": 2,
                "event_at": "2025-10-22T23:52:00Z",
                "period": 2,
                "clock": "04:40",
                "score_for": 37,
                "score_against": 39,
                "score_diff": -2,
                "context_bucket": "coin_flip",
                "team_price": 0.45,
                "price_delta_from_open": 0.12,
                "mfe_from_state": 0.08,
                "mae_from_state": -0.18,
                "large_swing_next_12_states_flag": True,
                "crossed_50c_next_12_states_flag": True,
                "winner_stable_80_after_state_flag": False,
            },
            {
                "game_id": "game_001",
                "team_side": "away",
                "team_slug": "ATL",
                "opponent_team_slug": "BOS",
                "state_index": 3,
                "event_at": "2025-10-23T00:58:00Z",
                "period": 4,
                "clock": "01:05",
                "score_for": 103,
                "score_against": 111,
                "score_diff": -8,
                "context_bucket": "late_chase",
                "team_price": 0.14,
                "price_delta_from_open": -0.19,
                "mfe_from_state": 0.01,
                "mae_from_state": -0.02,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
                "winner_stable_80_after_state_flag": False,
            },
            {
                "game_id": "game_002",
                "team_side": "home",
                "team_slug": "NYK",
                "opponent_team_slug": "MIA",
                "state_index": 1,
                "event_at": "2025-10-23T23:50:00Z",
                "period": 2,
                "clock": "05:00",
                "score_for": 46,
                "score_against": 39,
                "score_diff": 7,
                "context_bucket": "control",
                "team_price": 0.59,
                "price_delta_from_open": 0.01,
                "mfe_from_state": 0.05,
                "mae_from_state": -0.03,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
                "winner_stable_80_after_state_flag": False,
            },
            {
                "game_id": "game_002",
                "team_side": "away",
                "team_slug": "MIA",
                "opponent_team_slug": "NYK",
                "state_index": 1,
                "event_at": "2025-10-23T23:50:00Z",
                "period": 2,
                "clock": "05:00",
                "score_for": 39,
                "score_against": 46,
                "score_diff": -7,
                "context_bucket": "trail",
                "team_price": 0.41,
                "price_delta_from_open": -0.01,
                "mfe_from_state": 0.03,
                "mae_from_state": -0.05,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
                "winner_stable_80_after_state_flag": False,
            },
        ],
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
    assert "Inspect finished games and bounded state windows" in response.text
    assert "/analysis-studio/static/analysis_studio.js" in response.text


def test_analysis_studio_static_asset_route_serves_javascript_pytest() -> None:
    client = TestClient(create_app())
    response = client.get("/analysis-studio/static/analysis_studio.js")
    assert response.status_code == 200
    assert "loadGameExplorer" in response.text


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


def test_analysis_studio_games_route_lists_filtered_finished_games_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path)
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/games",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
            "team_slug": "ATL",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_games"] == 1
    assert payload["items"][0]["game_id"] == "game_001"
    assert payload["items"][0]["research_ready_game_flag"] is True
    assert payload["items"][0]["home"]["team_slug"] == "BOS"
    assert payload["items"][0]["away"]["team_slug"] == "ATL"

    filtered = client.get(
        "/v1/analysis/studio/games",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
            "coverage_status": "no_history",
        },
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total_games"] == 1
    assert filtered_payload["items"][0]["game_id"] == "game_002"
    assert filtered_payload["items"][0]["research_ready_game_flag"] is False


def test_analysis_studio_game_detail_route_returns_profiles_and_state_windows_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path)
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/games/game_001",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
            "state_limit": 12,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["game"]["matchup"] == "ATL @ BOS"
    assert payload["profiles"]["home"]["team_slug"] == "BOS"
    assert payload["profiles"]["away"]["team_slug"] == "ATL"
    assert payload["state_panel"]["home"]["summary"]["state_count"] == 3
    assert payload["state_panel"]["away"]["summary"]["state_count"] == 3
    assert payload["state_panel"]["home"]["rows"][1]["context_bucket"] == "coin_flip"
    assert payload["state_panel"]["away"]["rows"][2]["team_price"] == 0.14


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
