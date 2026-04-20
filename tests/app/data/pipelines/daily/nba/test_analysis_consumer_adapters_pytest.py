from __future__ import annotations

import json
from pathlib import Path

from app.data.pipelines.daily.nba.analysis.consumer_adapters import (
    build_analysis_consumer_snapshot,
    load_analysis_consumer_bundle,
    load_analysis_consumer_snapshot,
    resolve_analysis_consumer_paths,
)
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisConsumerRequest
from app.data.pipelines.daily.nba.analysis.reports import REPORT_SECTION_SPECS


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _build_report_payload(*, version: str, json_path: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "season": "2025-26",
        "season_phase": "regular_season",
        "analysis_version": version,
        "universe": {
            "games_total": 1224,
            "research_ready_games": 1209,
            "descriptive_only_games": 15,
            "excluded_games": 0,
            "coverage_status_counts": {"covered_pre_and_ingame": 1209, "covered_partial": 10, "no_matching_event": 5},
        },
        "section_order": [spec["key"] for spec in REPORT_SECTION_SPECS],
        "artifacts": {
            "json": str(json_path),
            "markdown": str(json_path.with_suffix(".md")),
        },
    }
    for spec in REPORT_SECTION_SPECS:
        payload[spec["key"]] = []
    payload["teams_against_expectation"] = [
        {"team_slug": "ATL", "sample_games": 82, "avg_expectation_gap_abs": 0.19},
        {"team_slug": "LAL", "sample_games": 82, "avg_expectation_gap_abs": 0.17},
    ]
    payload["highest_volatility_teams"] = [
        {"team_slug": "ATL", "sample_games": 82, "avg_ingame_range": 0.33, "avg_total_swing": 0.41}
    ]
    return payload


def _build_backtest_payload(*, version: str, json_path: Path, experiment_id: str) -> dict[str, object]:
    return {
        "season": "2025-26",
        "season_phase": "regular_season",
        "analysis_version": version,
        "families": {
            "reversion": {"trade_count": 60, "avg_gross_return_with_slippage": 0.081},
            "inversion": {"trade_count": 45, "avg_gross_return_with_slippage": 0.042},
        },
        "benchmark": {
            "contract_version": "v1",
            "minimum_trade_count": 20,
            "split_summary": [
                {"sample_name": "full_sample", "games_considered": 1224},
                {"sample_name": "random_holdout", "games_considered": 122},
            ],
            "family_summary": [
                {
                    "sample_name": "full_sample",
                    "strategy_family": "reversion",
                    "entry_rule": "favorite_drawdown_buy_10c",
                    "exit_rule": "reclaim_open_minus_2c_or_end",
                    "description": "Favorite drawdown reversion",
                    "comparator_group": "favorite_reversion",
                    "trade_count": 60,
                    "meets_min_trade_count_flag": True,
                    "win_rate": 0.58,
                    "avg_gross_return": 0.09,
                    "median_gross_return": 0.07,
                    "avg_gross_return_with_slippage": 0.081,
                    "avg_hold_time_seconds": 420.0,
                    "avg_mfe_after_entry": 0.13,
                    "avg_mae_after_entry": 0.06,
                    "delta_vs_no_trade_avg_gross_return_with_slippage": 0.081,
                    "delta_vs_winner_prediction_hold_to_end_avg_gross_return_with_slippage": 0.018,
                },
                {
                    "sample_name": "full_sample",
                    "strategy_family": "inversion",
                    "entry_rule": "first_cross_above_50c",
                    "exit_rule": "break_back_below_50c_or_end",
                    "description": "Underdog inversion continuation",
                    "comparator_group": "underdog_continuation",
                    "trade_count": 45,
                    "meets_min_trade_count_flag": True,
                    "win_rate": 0.54,
                    "avg_gross_return": 0.05,
                    "median_gross_return": 0.04,
                    "avg_gross_return_with_slippage": 0.042,
                    "avg_hold_time_seconds": 390.0,
                    "avg_mfe_after_entry": 0.10,
                    "avg_mae_after_entry": 0.05,
                    "delta_vs_no_trade_avg_gross_return_with_slippage": 0.042,
                    "delta_vs_winner_prediction_hold_to_end_avg_gross_return_with_slippage": -0.006,
                },
            ],
            "candidate_freeze": [
                {"strategy_family": "reversion", "candidate_label": "keep", "label_reason": "positive_on_full_time_and_holdout"},
                {"strategy_family": "inversion", "candidate_label": "experimental", "label_reason": "mixed_benchmark_signal"},
            ],
            "comparators": [
                {"name": "no_trade", "description": "Zero-return baseline."},
                {"name": "winner_prediction_hold_to_end", "description": "Buy and hold."},
            ],
            "comparator_summary": [
                {"sample_name": "full_sample", "strategy_family": "reversion", "comparator_name": "no_trade", "avg_gross_return_with_slippage": 0.0},
                {"sample_name": "full_sample", "strategy_family": "reversion", "comparator_name": "winner_prediction_hold_to_end", "avg_gross_return_with_slippage": 0.063},
            ],
            "context_rankings": [
                {
                    "sample_name": "full_sample",
                    "strategy_family": "reversion",
                    "ranking_side": "best",
                    "rank": 1,
                    "period_label": "Q4",
                    "opening_band": "70-80",
                    "context_bucket": "Q4|trail_1_4",
                    "trade_count": 12,
                    "win_rate": 0.67,
                    "avg_gross_return_with_slippage": 0.11,
                    "avg_hold_time_seconds": 390.0,
                }
            ],
        },
        "experiment": {"experiment_id": experiment_id},
        "artifacts": {
            "json": str(json_path),
            "markdown": str(json_path.with_suffix(".md")),
            "benchmark_family_summary_csv": str(json_path.parent / "benchmark_family_summary.csv"),
        },
    }


def _build_model_payload(*, version: str, json_path: Path) -> dict[str, object]:
    return {
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
                        "naive_comparison": {"better_than_naive": True},
                    }
                },
            },
            "volatility_inversion": {
                "status": "success",
                "model_family": "logistic_regression_baseline",
                "train_rows": 4000,
                "validation_rows": 1200,
                "metrics": {"brier": 0.14, "auc": 0.72},
                "naive_comparison": {"better_than_naive": True, "primary_metric": "brier"},
            },
        },
        "artifacts": {
            "json": str(json_path),
            "markdown": str(json_path.with_suffix(".md")),
            "tracks": {"volatility_inversion": {"validation_csv": str(json_path.parent / "volatility_validation.csv")}},
        },
    }


def _write_consumer_fixture(root: Path, *, version: str, experiment_id: str = "exp-123") -> None:
    version_dir = root / "2025-26" / "regular_season" / version
    report_json = version_dir / "analysis_report.json"
    backtest_json = version_dir / "backtests" / "run_analysis_backtests.json"
    model_json = version_dir / "models" / "train_analysis_baselines.json"
    _write_json(report_json, _build_report_payload(version=version, json_path=report_json))
    _write_json(backtest_json, _build_backtest_payload(version=version, json_path=backtest_json, experiment_id=experiment_id))
    _write_json(model_json, _build_model_payload(version=version, json_path=model_json))


def test_consumer_adapter_resolves_latest_version_and_builds_snapshot_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path, version="v1_0_4", experiment_id="exp-old")
    _write_consumer_fixture(tmp_path, version="v1_2_0", experiment_id="exp-new")

    request = AnalysisConsumerRequest(
        season="2025-26",
        season_phase="regular_season",
        output_root=str(tmp_path),
    )
    resolved = resolve_analysis_consumer_paths(request)
    assert resolved["output_dir"].endswith("v1_2_0")

    bundle = load_analysis_consumer_bundle(request)
    snapshot = build_analysis_consumer_snapshot(bundle)

    assert bundle.analysis_version == "v1_2_0"
    assert snapshot["benchmark"]["contract_version"] == "v1"
    assert snapshot["benchmark"]["experiment"]["experiment_id"] == "exp-new"
    assert snapshot["benchmark"]["strategy_rankings"][0]["strategy_family"] == "reversion"
    assert snapshot["benchmark"]["strategy_rankings"][0]["candidate_label"] == "keep"
    assert snapshot["report"]["sections"][0]["key"] == REPORT_SECTION_SPECS[0]["key"]
    assert snapshot["report"]["sections"][0]["row_count"] == 2
    assert snapshot["models"]["tracks"][0]["track_name"] == "trade_window_quality"
    assert snapshot["artifacts"]["backtests"]["json"].endswith("run_analysis_backtests.json")


def test_consumer_adapter_load_snapshot_rejects_experiment_mismatch_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path, version="v1_2_0", experiment_id="exp-expected")

    request = AnalysisConsumerRequest(
        season="2025-26",
        season_phase="regular_season",
        analysis_version="v1_2_0",
        backtest_experiment_id="exp-other",
        output_root=str(tmp_path),
    )

    try:
        load_analysis_consumer_snapshot(request)
    except ValueError as exc:
        assert "Backtest experiment mismatch" in str(exc)
    else:
        raise AssertionError("Expected ValueError for mismatched experiment id")
