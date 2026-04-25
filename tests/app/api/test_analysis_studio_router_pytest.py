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


def _write_json_list(path: Path, payload: list[dict[str, object]]) -> None:
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
    backtests_dir = version_dir / "backtests"
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
        backtests_dir / "run_analysis_backtests.json",
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
                        "win_rate": 0.61,
                        "avg_gross_return": 0.094,
                        "avg_gross_return_with_slippage": 0.081,
                        "avg_hold_time_seconds": 420,
                        "avg_mfe_after_entry": 0.137,
                        "avg_mae_after_entry": -0.071,
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
                "comparator_summary": [
                    {
                        "strategy_family": "reversion",
                        "comparator_family": "favorite_hold",
                        "avg_gross_return_with_slippage_diff": 0.017,
                    }
                ],
                "context_rankings": [
                    {
                        "strategy_family": "reversion",
                        "context_bucket": "Q2|trail_1_4",
                        "trade_count": 11,
                        "avg_gross_return_with_slippage": 0.129,
                    }
                ],
            },
            "artifacts": {
                "json": str(backtests_dir / "run_analysis_backtests.json"),
                "reversion_best_trades_csv": str(backtests_dir / "reversion_best_trades.csv"),
                "reversion_worst_trades_csv": str(backtests_dir / "reversion_worst_trades.csv"),
                "reversion_context_summary_csv": str(backtests_dir / "reversion_context_summary.csv"),
                "reversion_trade_traces_json": str(backtests_dir / "reversion_trade_traces.json"),
            },
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
        backtests_dir / "reversion_best_trades.csv",
        [
            {
                "game_id": "game_001",
                "team_slug": "ATL",
                "entry_price": 0.33,
                "exit_price": 0.48,
                "gross_return_with_slippage": 0.143,
                "hold_time_seconds": 510,
            },
            {
                "game_id": "game_003",
                "team_slug": "LAL",
                "entry_price": 0.26,
                "exit_price": 0.38,
                "gross_return_with_slippage": 0.112,
                "hold_time_seconds": 430,
            },
        ],
    )
    _write_csv(
        backtests_dir / "reversion_worst_trades.csv",
        [
            {
                "game_id": "game_002",
                "team_slug": "MIA",
                "entry_price": 0.42,
                "exit_price": 0.34,
                "gross_return_with_slippage": -0.082,
                "hold_time_seconds": 390,
            }
        ],
    )
    _write_csv(
        backtests_dir / "reversion_context_summary.csv",
        [
            {
                "context_bucket": "Q2|trail_1_4",
                "trade_count": 11,
                "win_rate": 0.73,
                "avg_gross_return_with_slippage": 0.129,
            },
            {
                "context_bucket": "Q3|coin_flip",
                "trade_count": 8,
                "win_rate": 0.5,
                "avg_gross_return_with_slippage": 0.042,
            },
        ],
    )
    _write_json_list(
        backtests_dir / "reversion_trade_traces.json",
        [
            {
                "game_id": "game_001",
                "team_slug": "ATL",
                "entry_context_bucket": "Q2|trail_1_4",
                "entry_price": 0.33,
                "exit_price": 0.48,
                "gross_return_with_slippage": 0.143,
                "states": [
                    {"period": 2, "clock": "04:40", "team_price": 0.58, "score_diff": -2},
                    {"period": 3, "clock": "08:05", "team_price": 0.51, "score_diff": -1},
                ],
            }
        ],
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


def _write_shared_benchmark_fixture(shared_root: Path) -> None:
    replay_root = shared_root / "artifacts" / "replay-engine-hf" / "2025-26" / "postseason_execution_replay"
    _write_json(
        replay_root / "replay_run.json",
        {
            "season": "2025-26",
            "season_phase": "play_in,playoffs",
            "analysis_version": "v1_0_1",
            "finished_game_count": 25,
            "state_panel_game_count": 22,
            "derived_bundle_game_count": 3,
            "replay_contract": {"maturity": "stub_v0"},
        },
    )
    _write_csv(
        replay_root / "replay_subject_summary.csv",
        [
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "standard_trade_count": 17,
                "replay_trade_count": 4,
                "trade_gap": -13,
                "execution_rate": 0.2352941176,
                "standard_avg_return_with_slippage": 0.179136,
                "replay_avg_return_with_slippage": 0.213698,
                "replay_no_trade_count": 13,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 19.378395,
                "replay_ending_bankroll": 11.96,
                "live_trade_count": 0,
            },
            {
                "subject_name": "controller_vnext_deterministic_v1 :: tight",
                "subject_type": "controller",
                "standard_trade_count": 24,
                "replay_trade_count": 5,
                "trade_gap": -19,
                "execution_rate": 0.2083333333,
                "standard_avg_return_with_slippage": 0.14462,
                "replay_avg_return_with_slippage": 0.14169,
                "replay_no_trade_count": 19,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 21.068925,
                "replay_ending_bankroll": 11.36,
                "live_trade_count": 0,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "standard_trade_count": 6,
                "replay_trade_count": 3,
                "trade_gap": -3,
                "execution_rate": 0.5,
                "standard_avg_return_with_slippage": 0.2539607,
                "replay_avg_return_with_slippage": 0.6561728,
                "replay_no_trade_count": 3,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 13.7749572,
                "replay_ending_bankroll": 14.9265111,
                "live_trade_count": 0,
            },
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "standard_trade_count": 2,
                "replay_trade_count": 1,
                "trade_gap": -1,
                "execution_rate": 0.5,
                "standard_avg_return_with_slippage": 0.117413,
                "replay_avg_return_with_slippage": 0.66,
                "replay_no_trade_count": 1,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 10.66619,
                "replay_ending_bankroll": 11.65,
                "live_trade_count": 0,
            },
        ],
    )
    _write_csv(
        replay_root / "replay_portfolio_summary.csv",
        [
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "mode": "standard",
                "ending_bankroll": 19.378395,
                "compounded_return": 0.9378395,
                "max_drawdown_pct": 0.41,
                "max_drawdown_amount": 7.95,
                "executed_trade_count": 17,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "mode": "replay",
                "ending_bankroll": 11.96,
                "compounded_return": 0.196,
                "max_drawdown_pct": 0.22,
                "max_drawdown_amount": 2.2,
                "executed_trade_count": 4,
            },
            {
                "subject_name": "controller_vnext_deterministic_v1 :: tight",
                "subject_type": "controller",
                "mode": "standard",
                "ending_bankroll": 21.068925,
                "compounded_return": 1.1068925,
                "max_drawdown_pct": 0.46,
                "max_drawdown_amount": 9.68,
                "executed_trade_count": 24,
            },
            {
                "subject_name": "controller_vnext_deterministic_v1 :: tight",
                "subject_type": "controller",
                "mode": "replay",
                "ending_bankroll": 11.36,
                "compounded_return": 0.136,
                "max_drawdown_pct": 0.29,
                "max_drawdown_amount": 2.9,
                "executed_trade_count": 5,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 13.7749572,
                "compounded_return": 0.3774957,
                "max_drawdown_pct": 0.08,
                "max_drawdown_amount": 0.82,
                "executed_trade_count": 6,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 14.9265111,
                "compounded_return": 0.4926511,
                "max_drawdown_pct": 0.12,
                "max_drawdown_amount": 1.2,
                "executed_trade_count": 3,
            },
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 10.66619,
                "compounded_return": 0.066619,
                "max_drawdown_pct": 0.05,
                "max_drawdown_amount": 0.5,
                "executed_trade_count": 2,
            },
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 11.65,
                "compounded_return": 0.165,
                "max_drawdown_pct": 0.07,
                "max_drawdown_amount": 0.7,
                "executed_trade_count": 1,
            },
        ],
    )
    _write_csv(
        replay_root / "replay_divergence_summary.csv",
        [
            {
                "subject_name": "controller_vnext_deterministic_v1 :: tight",
                "subject_type": "controller",
                "no_trade_reason": "signal_stale",
                "signal_count": 19,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "no_trade_reason": "signal_stale",
                "signal_count": 13,
            },
        ],
    )
    replay_signal_rows: list[dict[str, object]] = []

    def _append_signal_rows(
        *,
        subject_name: str,
        subject_type: str,
        executed_count: int,
        stale_count: int,
        other_blocked_count: int = 0,
    ) -> None:
        for index in range(executed_count):
            replay_signal_rows.append(
                {
                    "subject_name": subject_name,
                    "subject_type": subject_type,
                    "game_id": f"{subject_name[:8]}-game",
                    "signal_id": f"{subject_name}-exec-{index}",
                    "executed_flag": True,
                    "no_trade_reason": "",
                }
            )
        for index in range(stale_count):
            replay_signal_rows.append(
                {
                    "subject_name": subject_name,
                    "subject_type": subject_type,
                    "game_id": f"{subject_name[:8]}-game",
                    "signal_id": f"{subject_name}-stale-{index}",
                    "executed_flag": False,
                    "no_trade_reason": "signal_stale",
                }
            )
        for index in range(other_blocked_count):
            replay_signal_rows.append(
                {
                    "subject_name": subject_name,
                    "subject_type": subject_type,
                    "game_id": f"{subject_name[:8]}-game",
                    "signal_id": f"{subject_name}-other-{index}",
                    "executed_flag": False,
                    "no_trade_reason": "entry_after_exit_signal",
                }
            )

    _append_signal_rows(
        subject_name="controller_vnext_unified_v1 :: balanced",
        subject_type="controller",
        executed_count=4,
        stale_count=13,
    )
    _append_signal_rows(
        subject_name="controller_vnext_deterministic_v1 :: tight",
        subject_type="controller",
        executed_count=5,
        stale_count=19,
    )
    _append_signal_rows(
        subject_name="inversion",
        subject_type="family",
        executed_count=3,
        stale_count=2,
        other_blocked_count=1,
    )
    _append_signal_rows(
        subject_name="quarter_open_reprice",
        subject_type="family",
        executed_count=1,
        stale_count=1,
    )
    _write_csv(replay_root / "replay_signal_summary.csv", replay_signal_rows)
    _write_csv(
        replay_root / "replay_attempt_trace.csv",
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500133",
                "signal_id": "inversion-stale-0",
                "attempt_stage": "entry",
                "cycle_at": "2026-04-24T18:05:00+00:00",
                "attempt_index": 1,
                "result": "blocked",
                "reason": "signal_stale",
            }
        ],
    )
    _write_csv(
        replay_root / "replay_live_summary.csv",
        [
            {
                "run_id": "live-2026-04-23-v1",
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "game_id": "0042500123",
                "live_trade_count": 0,
                "entry_submitted_count": 0,
                "position_opened_count": 0,
            }
        ],
    )
    _write_csv(
        replay_root / "replay_game_gap.csv",
        [
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "game_id": "0042500123",
                "state_source": "state_panel",
                "standard_trade_count": 2,
                "replay_trade_count": 0,
                "trade_gap": -2,
                "top_no_trade_reason": "signal_stale",
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500133",
                "state_source": "state_panel",
                "standard_trade_count": 1,
                "replay_trade_count": 0,
                "trade_gap": -1,
                "top_no_trade_reason": "entry_after_exit_signal",
            },
        ],
    )
    _write_json(
        shared_root / "reports" / "replay-engine-hf" / "run_metadata.json",
        {
            "artifacts_dir": str(replay_root),
            "reports_dir": str(shared_root / "reports" / "replay-engine-hf"),
            "replay_json": str(replay_root / "replay_run.json"),
            "replay_markdown": str(replay_root / "replay_run.md"),
            "ranked_memo": str(shared_root / "reports" / "replay-engine-hf" / "ranked_memo.md"),
            "finished_game_count": 25,
            "state_panel_game_count": 22,
            "derived_bundle_game_count": 3,
        },
    )
    (shared_root / "reports" / "replay-engine-hf" / "ranked_memo.md").parent.mkdir(parents=True, exist_ok=True)
    (shared_root / "reports" / "replay-engine-hf" / "ranked_memo.md").write_text(
        "# Ranked Memo\n- strongest current replay result: `inversion`\n",
        encoding="utf-8",
    )
    (shared_root / "benchmark_contract").mkdir(parents=True, exist_ok=True)
    (shared_root / "benchmark_contract" / "replay_contract_current.md").write_text(
        "# Replay Contract Current\n\n"
        "## Status\n"
        "- owner lane: `replay-engine-hf`\n"
        "- snapshot date: `2026-04-24`\n"
        "- maturity: `stub_v0`\n",
        encoding="utf-8",
    )
    (shared_root / "benchmark_contract" / "unified_benchmark_contract_current.md").write_text(
        "# Unified Benchmark Contract Current\n\n"
        "## Status\n"
        "- owner lane: `benchmark-integration`\n"
        "- snapshot date: `2026-04-24`\n"
        "- schema version: `integration_v1`\n",
        encoding="utf-8",
    )
    (shared_root / "handoffs" / "replay-engine-hf").mkdir(parents=True, exist_ok=True)
    (shared_root / "handoffs" / "replay-engine-hf" / "status.md").write_text(
        "# Replay Engine HF Status\n\n- timestamp: `2026-04-24`\n- role: `realism baseline + HF invention`\n",
        encoding="utf-8",
    )
    (shared_root / "handoffs" / "ml-trading-lane").mkdir(parents=True, exist_ok=True)
    (shared_root / "handoffs" / "ml-trading-lane" / "status.md").write_text(
        "# ML Trading Status\n\n- timestamp: `2026-04-24`\n- role: `sidecar ranking and calibration`\n",
        encoding="utf-8",
    )
    (shared_root / "handoffs" / "llm-strategy-lane").mkdir(parents=True, exist_ok=True)
    (shared_root / "handoffs" / "llm-strategy-lane" / "status.md").write_text(
        "# LLM Strategy Status\n\n- timestamp: `2026-04-24`\n- role: `select/gate/compile`\n",
        encoding="utf-8",
    )

    replay_contract_ref = str(shared_root / "benchmark_contract" / "replay_contract_current.md")
    benchmark_contract_ref = str(shared_root / "benchmark_contract" / "unified_benchmark_contract_current.md")

    def _result_views(
        *,
        standard_trade_count: int,
        standard_ending_bankroll: float,
        standard_avg_return_with_slippage: float,
        standard_compounded_return: float,
        standard_max_drawdown_pct: float,
        standard_max_drawdown_amount: float,
        replay_trade_count: int,
        replay_ending_bankroll: float,
        replay_avg_return_with_slippage: float,
        replay_compounded_return: float,
        replay_max_drawdown_pct: float,
        replay_max_drawdown_amount: float,
        replay_no_trade_count: int,
        execution_rate: float,
        live_observed_flag: bool = False,
        live_trade_count: int = 0,
    ) -> dict[str, object]:
        return {
            "standard_backtest": {
                "trade_count": standard_trade_count,
                "ending_bankroll": standard_ending_bankroll,
                "avg_return_with_slippage": standard_avg_return_with_slippage,
                "compounded_return": standard_compounded_return,
                "max_drawdown_pct": standard_max_drawdown_pct,
                "max_drawdown_amount": standard_max_drawdown_amount,
            },
            "replay_result": {
                "trade_count": replay_trade_count,
                "ending_bankroll": replay_ending_bankroll,
                "avg_return_with_slippage": replay_avg_return_with_slippage,
                "compounded_return": replay_compounded_return,
                "max_drawdown_pct": replay_max_drawdown_pct,
                "max_drawdown_amount": replay_max_drawdown_amount,
                "no_trade_count": replay_no_trade_count,
                "execution_rate": execution_rate,
            },
            "live_observed": {
                "live_observed_flag": live_observed_flag,
                "trade_count": live_trade_count,
            },
        }

    def _replay_realism(
        *,
        standard_trade_count: int,
        replay_trade_count: int,
        execution_rate: float,
        stale_signal_suppressed_count: int,
        blocked_signal_count: int,
        top_no_trade_reason: str,
    ) -> dict[str, object]:
        trade_gap = replay_trade_count - standard_trade_count
        return {
            "trade_gap": trade_gap,
            "execution_rate": execution_rate,
            "realism_gap_trade_rate": abs(trade_gap) / standard_trade_count if standard_trade_count else None,
            "blocked_signal_count": blocked_signal_count,
            "stale_signal_suppressed_count": stale_signal_suppressed_count,
            "stale_signal_suppression_rate": (
                stale_signal_suppressed_count / standard_trade_count if standard_trade_count else None
            ),
            "stale_signal_share_of_blocked_signals": (
                stale_signal_suppressed_count / blocked_signal_count if blocked_signal_count else None
            ),
            "top_no_trade_reason": top_no_trade_reason,
        }

    _write_json(
        shared_root / "reports" / "replay-engine-hf" / "benchmark_submission.json",
        {
            "lane_id": "replay-engine-hf",
            "lane_label": "Replay + deterministic/HF",
            "lane_type": "deterministic_hf",
            "published_at": "2026-04-24T22:45:00+00:00",
            "comparison_scope": {
                "season": "2025-26",
                "phase_group": "play_in,playoffs",
                "replay_contract_ref": replay_contract_ref,
                "benchmark_contract_ref": benchmark_contract_ref,
            },
            "subjects": [
                {
                    "candidate_id": "controller_vnext_unified_v1 :: balanced",
                    "display_name": "controller_vnext_unified_v1 :: balanced",
                    "candidate_kind": "baseline_controller",
                    "subject_type": "controller",
                    "publication_state": "published",
                    "live_test_recommendation": "locked_baseline",
                    "replay_rank": 11,
                    "result_views": _result_views(
                        standard_trade_count=17,
                        standard_ending_bankroll=19.378395,
                        standard_avg_return_with_slippage=0.179136,
                        standard_compounded_return=0.9378395,
                        standard_max_drawdown_pct=0.41,
                        standard_max_drawdown_amount=7.95,
                        replay_trade_count=4,
                        replay_ending_bankroll=11.96,
                        replay_avg_return_with_slippage=0.213698,
                        replay_compounded_return=0.196,
                        replay_max_drawdown_pct=0.22,
                        replay_max_drawdown_amount=2.2,
                        replay_no_trade_count=13,
                        execution_rate=0.2352941176,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=17,
                        replay_trade_count=4,
                        execution_rate=0.2352941176,
                        stale_signal_suppressed_count=13,
                        blocked_signal_count=13,
                        top_no_trade_reason="signal_stale",
                    ),
                    "trace_artifacts": {"replay_signal_summary_csv": "replay_signal_summary.csv"},
                },
                {
                    "candidate_id": "controller_vnext_deterministic_v1 :: tight",
                    "display_name": "controller_vnext_deterministic_v1 :: tight",
                    "candidate_kind": "baseline_controller",
                    "subject_type": "controller",
                    "publication_state": "published",
                    "live_test_recommendation": "locked_baseline",
                    "replay_rank": 12,
                    "result_views": _result_views(
                        standard_trade_count=24,
                        standard_ending_bankroll=21.068925,
                        standard_avg_return_with_slippage=0.14462,
                        standard_compounded_return=1.1068925,
                        standard_max_drawdown_pct=0.46,
                        standard_max_drawdown_amount=9.68,
                        replay_trade_count=5,
                        replay_ending_bankroll=11.36,
                        replay_avg_return_with_slippage=0.14169,
                        replay_compounded_return=0.136,
                        replay_max_drawdown_pct=0.29,
                        replay_max_drawdown_amount=2.9,
                        replay_no_trade_count=19,
                        execution_rate=0.2083333333,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=24,
                        replay_trade_count=5,
                        execution_rate=0.2083333333,
                        stale_signal_suppressed_count=19,
                        blocked_signal_count=19,
                        top_no_trade_reason="signal_stale",
                    ),
                    "trace_artifacts": {"replay_signal_summary_csv": "replay_signal_summary.csv"},
                },
                {
                    "candidate_id": "inversion",
                    "display_name": "inversion",
                    "candidate_kind": "deterministic_family",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "comparison_ready_flag": True,
                    "live_test_recommendation": "shadow_only",
                    "replay_rank": 3,
                    "result_views": _result_views(
                        standard_trade_count=6,
                        standard_ending_bankroll=13.7749572,
                        standard_avg_return_with_slippage=0.2539607,
                        standard_compounded_return=0.3774957,
                        standard_max_drawdown_pct=0.08,
                        standard_max_drawdown_amount=0.82,
                        replay_trade_count=3,
                        replay_ending_bankroll=14.9265111,
                        replay_avg_return_with_slippage=0.6561728,
                        replay_compounded_return=0.4926511,
                        replay_max_drawdown_pct=0.12,
                        replay_max_drawdown_amount=1.2,
                        replay_no_trade_count=3,
                        execution_rate=0.5,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=6,
                        replay_trade_count=3,
                        execution_rate=0.5,
                        stale_signal_suppressed_count=2,
                        blocked_signal_count=3,
                        top_no_trade_reason="signal_stale",
                    ),
                    "trace_artifacts": {"attempt_trace_csv": "replay_attempt_trace.csv"},
                },
                {
                    "candidate_id": "quarter_open_reprice",
                    "display_name": "quarter_open_reprice",
                    "candidate_kind": "hf_family",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "comparison_ready_flag": True,
                    "focus_rank": 1,
                    "replay_rank": 1,
                    "live_test_recommendation": "live_probe",
                    "result_views": _result_views(
                        standard_trade_count=2,
                        standard_ending_bankroll=10.66619,
                        standard_avg_return_with_slippage=0.117413,
                        standard_compounded_return=0.066619,
                        standard_max_drawdown_pct=0.05,
                        standard_max_drawdown_amount=0.5,
                        replay_trade_count=1,
                        replay_ending_bankroll=11.65,
                        replay_avg_return_with_slippage=0.66,
                        replay_compounded_return=0.165,
                        replay_max_drawdown_pct=0.07,
                        replay_max_drawdown_amount=0.7,
                        replay_no_trade_count=1,
                        execution_rate=0.5,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=2,
                        replay_trade_count=1,
                        execution_rate=0.5,
                        stale_signal_suppressed_count=1,
                        blocked_signal_count=1,
                        top_no_trade_reason="signal_stale",
                    ),
                    "trace_artifacts": {"decision_trace_json": "quarter_open_reprice_trace.json"},
                },
                {
                    "candidate_id": "micro_momentum_continuation",
                    "display_name": "micro_momentum_continuation",
                    "candidate_kind": "hf_family",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "comparison_ready_flag": True,
                    "focus_rank": 2,
                    "replay_rank": 2,
                    "live_test_recommendation": "live_probe",
                    "result_views": _result_views(
                        standard_trade_count=1,
                        standard_ending_bankroll=10.3,
                        standard_avg_return_with_slippage=0.03,
                        standard_compounded_return=0.03,
                        standard_max_drawdown_pct=0.02,
                        standard_max_drawdown_amount=0.2,
                        replay_trade_count=1,
                        replay_ending_bankroll=11.65,
                        replay_avg_return_with_slippage=0.165,
                        replay_compounded_return=0.165,
                        replay_max_drawdown_pct=0.04,
                        replay_max_drawdown_amount=0.4,
                        replay_no_trade_count=0,
                        execution_rate=1.0,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=1,
                        replay_trade_count=1,
                        execution_rate=1.0,
                        stale_signal_suppressed_count=0,
                        blocked_signal_count=0,
                        top_no_trade_reason="",
                    ),
                    "trace_artifacts": {"decision_trace_json": "micro_momentum_continuation_trace.json"},
                },
                {
                    "candidate_id": "lead_fragility",
                    "display_name": "lead_fragility",
                    "candidate_kind": "hf_family",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "comparison_ready_flag": True,
                    "focus_rank": 3,
                    "replay_rank": 4,
                    "live_test_recommendation": "shadow_only",
                    "result_views": _result_views(
                        standard_trade_count=1,
                        standard_ending_bankroll=10.15,
                        standard_avg_return_with_slippage=0.015,
                        standard_compounded_return=0.015,
                        standard_max_drawdown_pct=0.02,
                        standard_max_drawdown_amount=0.2,
                        replay_trade_count=1,
                        replay_ending_bankroll=10.8,
                        replay_avg_return_with_slippage=0.08,
                        replay_compounded_return=0.08,
                        replay_max_drawdown_pct=0.05,
                        replay_max_drawdown_amount=0.5,
                        replay_no_trade_count=0,
                        execution_rate=1.0,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=1,
                        replay_trade_count=1,
                        execution_rate=1.0,
                        stale_signal_suppressed_count=0,
                        blocked_signal_count=0,
                        top_no_trade_reason="",
                    ),
                    "trace_artifacts": {"decision_trace_json": "lead_fragility_trace.json"},
                },
                {
                    "candidate_id": "winner_definition",
                    "display_name": "winner_definition",
                    "candidate_kind": "deterministic_family",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "comparison_ready_flag": True,
                    "replay_rank": 10,
                    "live_test_recommendation": "bench",
                    "result_views": _result_views(
                        standard_trade_count=7,
                        standard_ending_bankroll=9.6,
                        standard_avg_return_with_slippage=-0.02,
                        standard_compounded_return=-0.04,
                        standard_max_drawdown_pct=0.22,
                        standard_max_drawdown_amount=2.2,
                        replay_trade_count=1,
                        replay_ending_bankroll=8.9,
                        replay_avg_return_with_slippage=-0.11,
                        replay_compounded_return=-0.11,
                        replay_max_drawdown_pct=0.31,
                        replay_max_drawdown_amount=3.1,
                        replay_no_trade_count=6,
                        execution_rate=0.1428571429,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=7,
                        replay_trade_count=1,
                        execution_rate=0.1428571429,
                        stale_signal_suppressed_count=6,
                        blocked_signal_count=6,
                        top_no_trade_reason="signal_stale",
                    ),
                    "trace_artifacts": {"decision_trace_json": "winner_definition_trace.json"},
                },
            ],
        },
    )
    _write_json(
        shared_root / "reports" / "ml-trading-lane" / "benchmark_submission.json",
        {
            "lane_id": "ml-trading",
            "lane_label": "ML trading lane",
            "lane_type": "ml",
            "published_at": "2026-04-24T23:00:00+00:00",
            "comparison_scope": {
                "season": "2025-26",
                "phase_group": "play_in,playoffs",
                "replay_contract_ref": replay_contract_ref,
                "benchmark_contract_ref": benchmark_contract_ref,
            },
            "subjects": [
                {
                    "candidate_id": "ml_controller_focus_calibrator_v2",
                    "display_name": "ml_controller_focus_calibrator_v2",
                    "candidate_kind": "ml_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "result_views": _result_views(
                        standard_trade_count=4,
                        standard_ending_bankroll=13.6,
                        standard_avg_return_with_slippage=0.19,
                        standard_compounded_return=0.36,
                        standard_max_drawdown_pct=0.11,
                        standard_max_drawdown_amount=1.1,
                        replay_trade_count=2,
                        replay_ending_bankroll=18.17,
                        replay_avg_return_with_slippage=0.408,
                        replay_compounded_return=0.817,
                        replay_max_drawdown_pct=0.07,
                        replay_max_drawdown_amount=0.7,
                        replay_no_trade_count=2,
                        execution_rate=0.5,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=4,
                        replay_trade_count=2,
                        execution_rate=0.5,
                        stale_signal_suppressed_count=2,
                        blocked_signal_count=2,
                        top_no_trade_reason="signal_stale",
                    ),
                    "notes": ["Calibration is the strongest immediate ML contribution."],
                    "trace_artifacts": {"decision_trace_json": "ml_controller_focus_calibrator_v2_trace.json"},
                },
                {
                    "candidate_id": "ml_sidecar_union_v2",
                    "display_name": "ml_sidecar_union_v2",
                    "candidate_kind": "ml_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "result_views": _result_views(
                        standard_trade_count=5,
                        standard_ending_bankroll=13.8,
                        standard_avg_return_with_slippage=0.18,
                        standard_compounded_return=0.38,
                        standard_max_drawdown_pct=0.13,
                        standard_max_drawdown_amount=1.3,
                        replay_trade_count=2,
                        replay_ending_bankroll=18.17,
                        replay_avg_return_with_slippage=0.4,
                        replay_compounded_return=0.817,
                        replay_max_drawdown_pct=0.08,
                        replay_max_drawdown_amount=0.8,
                        replay_no_trade_count=3,
                        execution_rate=0.4,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=5,
                        replay_trade_count=2,
                        execution_rate=0.4,
                        stale_signal_suppressed_count=3,
                        blocked_signal_count=3,
                        top_no_trade_reason="signal_stale",
                    ),
                    "notes": ["Deterministic routing plus replay-aware ML calibration remains preferred."],
                    "trace_artifacts": {"decision_trace_json": "ml_sidecar_union_v2_trace.json"},
                },
            ],
        },
    )
    _write_json(
        shared_root / "reports" / "llm-strategy-lane" / "benchmark_submission.json",
        {
            "lane_id": "llm-strategy",
            "lane_label": "LLM strategy",
            "lane_type": "llm",
            "published_at": "2026-04-24T23:10:00+00:00",
            "comparison_scope": {
                "season": "2025-26",
                "phase_group": "play_in,playoffs",
                "replay_contract_ref": replay_contract_ref,
                "benchmark_contract_ref": benchmark_contract_ref,
            },
            "subjects": [
                {
                    "candidate_id": "llm_template_compiler_v1",
                    "display_name": "llm_template_compiler_v1",
                    "candidate_kind": "llm_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "live_observed_flag": False,
                    "result_views": _result_views(
                        standard_trade_count=4,
                        standard_ending_bankroll=10.99,
                        standard_avg_return_with_slippage=0.0342,
                        standard_compounded_return=0.0995,
                        standard_max_drawdown_pct=0.0,
                        standard_max_drawdown_amount=0.0,
                        replay_trade_count=4,
                        replay_ending_bankroll=35.7781,
                        replay_avg_return_with_slippage=0.5047,
                        replay_compounded_return=2.5778,
                        replay_max_drawdown_pct=0.0,
                        replay_max_drawdown_amount=0.0,
                        replay_no_trade_count=0,
                        execution_rate=1.0,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=4,
                        replay_trade_count=4,
                        execution_rate=1.0,
                        stale_signal_suppressed_count=0,
                        blocked_signal_count=0,
                        top_no_trade_reason="",
                    ),
                    "notes": ["Preferred LLM role is constrained select/gate/compile."],
                    "trace_artifacts": {"decision_trace_json": "llm_template_compiler_v1_trace.json"},
                },
                {
                    "candidate_id": "llm_state_reactor_v1",
                    "display_name": "llm_state_reactor_v1",
                    "candidate_kind": "llm_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "live_observed_flag": False,
                    "result_views": _result_views(
                        standard_trade_count=3,
                        standard_ending_bankroll=11.04,
                        standard_avg_return_with_slippage=0.0477,
                        standard_compounded_return=0.1042,
                        standard_max_drawdown_pct=0.0,
                        standard_max_drawdown_amount=0.0,
                        replay_trade_count=3,
                        replay_ending_bankroll=17.1407,
                        replay_avg_return_with_slippage=0.2696,
                        replay_compounded_return=0.7141,
                        replay_max_drawdown_pct=0.0,
                        replay_max_drawdown_amount=0.0,
                        replay_no_trade_count=0,
                        execution_rate=1.0,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=3,
                        replay_trade_count=3,
                        execution_rate=1.0,
                        stale_signal_suppressed_count=0,
                        blocked_signal_count=0,
                        top_no_trade_reason="",
                    ),
                    "trace_artifacts": {"decision_trace_json": "llm_state_reactor_v1_trace.json"},
                },
                {
                    "candidate_id": "llm_selector_replay_guard_v1",
                    "display_name": "llm_selector_replay_guard_v1",
                    "candidate_kind": "llm_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "live_observed_flag": False,
                    "result_views": _result_views(
                        standard_trade_count=2,
                        standard_ending_bankroll=9.92,
                        standard_avg_return_with_slippage=0.0006,
                        standard_compounded_return=-0.0083,
                        standard_max_drawdown_pct=0.0,
                        standard_max_drawdown_amount=0.0,
                        replay_trade_count=2,
                        replay_ending_bankroll=15.778,
                        replay_avg_return_with_slippage=0.3504,
                        replay_compounded_return=0.5778,
                        replay_max_drawdown_pct=0.0,
                        replay_max_drawdown_amount=0.0,
                        replay_no_trade_count=0,
                        execution_rate=1.0,
                    ),
                    "replay_realism": _replay_realism(
                        standard_trade_count=2,
                        replay_trade_count=2,
                        execution_rate=1.0,
                        stale_signal_suppressed_count=0,
                        blocked_signal_count=0,
                        top_no_trade_reason="",
                    ),
                    "trace_artifacts": {"decision_trace_json": "llm_selector_replay_guard_v1_trace.json"},
                },
            ],
        },
    )
    _write_json(
        shared_root / "artifacts" / "daily-live-validation" / "2025-04-24" / "session_summary.json",
        {
            "session_date": "2025-04-24",
            "status": "live_running",
            "snapshot_published_at": "2025-04-24T21:48:07-03:00",
            "control": {
                "primary_controller": "controller_vnext_unified_v1 :: balanced",
                "fallback_controller": "controller_vnext_deterministic_v1 :: tight",
                "mode": "live",
            },
            "planned_probes": [
                {
                    "candidate_id": "quarter_open_reprice",
                    "target_mode": "probe",
                    "today_execution": "shadow",
                    "compare_ready_state": "compare_ready_dashboard_yes",
                    "executor_supported": False,
                    "reason": "standalone probe routing is not implemented in live executor v1",
                },
                {
                    "candidate_id": "micro_momentum_continuation",
                    "target_mode": "probe",
                    "today_execution": "shadow",
                    "compare_ready_state": "compare_ready_dashboard_yes",
                    "executor_supported": False,
                    "reason": "standalone probe routing is not implemented in live executor v1",
                },
            ],
            "shadow_set": [
                {
                    "candidate_id": "inversion",
                    "today_execution": "shadow",
                    "compare_ready_state": "compare_ready_dashboard_yes",
                },
                {
                    "candidate_id": "ML-calibrated controller sidecar",
                    "today_execution": "shadow",
                    "compare_ready_state": "compare_ready_ml_submission_yes",
                },
                {
                    "candidate_id": "llm_template_compiler_v1",
                    "today_execution": "shadow",
                    "compare_ready_state": "submission_published_dashboard_refreshed",
                },
            ],
            "bench_set": [
                "lead_fragility",
                "panic_fade_fast",
                "halftime_gap_fill",
                "ml_controller_gate_v1",
                "ml_ranker_gate_v1",
                "unconstrained_llm_generation",
            ],
            "harness_capabilities": {
                "live_executor_version": "v1",
                "supports_locked_controller_pair": True,
                "supports_standalone_probe_candidates": False,
                "supports_ml_sidecar_live_routing": False,
                "supports_llm_sidecar_live_routing": False,
            },
            "current_live_truth": {
                "run_status": "running",
                "cycle_count": 3,
                "open_orders": 0,
                "open_positions": 0,
            },
        },
    )
    _write_csv(
        shared_root / "artifacts" / "daily-live-validation" / "2025-04-24" / "live_vs_replay_comparison.csv",
        [
            {
                "session_date": "2025-04-24",
                "run_id": "live-2025-04-24-v1-live",
                "game_id": "0042500113",
                "matchup": "BOS at PHI",
                "candidate_id": "controller_vnext_unified_v1 :: balanced",
                "candidate_kind": "control",
                "source_lane": "daily-live-validation",
                "target_mode": "control",
                "today_execution": "live",
                "benchmark_compare_ready_state": "control_locked_baseline",
                "replay_expected_trade": "pending_replay_extract",
                "replay_expected_reason": "awaiting_replay_slate_extract",
                "replay_signal_id": "",
                "replay_signal_timestamp": "",
                "live_selected_trade": True,
                "live_selected_signal_id": "winner_definition|0042500113|away|130",
                "live_decision_source": "llm_confirmed_weak_default",
                "live_attempted_entry": False,
                "live_order_id": "",
                "live_filled": False,
                "live_trade_id": "",
                "no_trade_bucket": "stale_signal",
                "no_trade_sub_reason": "entry_signal_stale",
                "route_lane": "daily-live-validation",
                "best_bid": 0.67,
                "best_ask": 0.68,
                "spread_cents": 1.0,
                "notes": "selected trade existed but was stale before live entry",
            }
        ],
    )
    (shared_root / "reports" / "daily-live-validation").mkdir(parents=True, exist_ok=True)
    (shared_root / "reports" / "daily-live-validation" / "postgame_report_2025-04-24.md").write_text(
        "# Daily Live Validation\n",
        encoding="utf-8",
    )


def test_analysis_studio_index_route_serves_html_pytest() -> None:
    client = TestClient(create_app())
    response = client.get("/analysis-studio")
    assert response.status_code == 200
    assert "Janus Cortex Unified Benchmark Dashboard" in response.text
    assert "One comparison layer for the locked baselines" in response.text
    assert "Who can compare globally right now" in response.text
    assert "Current promoted stack" in response.text
    assert "Today’s live slate posture" in response.text
    assert "/analysis-studio/static/analysis_studio.js" in response.text


def test_analysis_studio_static_asset_route_serves_javascript_pytest() -> None:
    client = TestClient(create_app())
    response = client.get("/analysis-studio/static/analysis_studio.js")
    assert response.status_code == 200
    assert "loadUnifiedBenchmarkDashboard" in response.text
    assert "renderLaneStatuses" in response.text
    assert "renderLaneRankings" in response.text
    assert "renderCompareReadyRanking" in response.text
    assert "renderMergeRecommendations" in response.text
    assert "renderResultModes" in response.text
    assert "renderDailyLiveValidation" in response.text
    assert "renderPromotedStack" in response.text
    assert "Live state" in response.text


def test_analysis_studio_benchmark_dashboard_route_loads_shared_snapshot_pytest(tmp_path: Path) -> None:
    _write_shared_benchmark_fixture(tmp_path / "shared")
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/benchmark-dashboard",
        params={
            "season": "2025-26",
            "shared_root": str(tmp_path / "shared"),
            "finalist_limit": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "integration_v1"
    assert payload["replay_contract"]["replay_contract_maturity"] == "stub_v0"
    assert payload["daily_live_validation"]["session_date"] == "2025-04-24"
    assert payload["summary"]["published_candidate_count"] == 12
    assert payload["summary"]["criteria_ready_candidate_count"] == 12
    assert payload["summary"]["compare_ready_candidate_count"] == 12
    assert payload["summary"]["live_ready_candidate_count"] == 2
    assert payload["summary"]["live_probe_candidate_count"] == 2
    assert payload["summary"]["shadow_only_candidate_count"] == 7
    assert payload["summary"]["bench_only_candidate_count"] == 1
    assert payload["summary"]["replay_compare_ready_challenger_count"] == 5
    assert payload["lane_statuses"][0]["lane_id"] == "locked-baselines"
    assert payload["lane_statuses"][0]["lane_bucket"] == "live_ready"
    assert payload["lane_statuses"][1]["lane_id"] == "replay-engine-hf"
    assert payload["lane_statuses"][1]["lane_bucket"] == "mixed"
    assert payload["lane_statuses"][2]["lane_id"] == "ml-trading"
    assert payload["lane_statuses"][2]["compare_ready_flag"] is True
    assert payload["lane_statuses"][2]["lane_bucket"] == "shadow_only"
    assert payload["lane_statuses"][3]["lane_id"] == "llm-strategy"
    assert payload["lane_statuses"][3]["lane_bucket"] == "shadow_only"
    assert {row["lane_id"] for row in payload["compare_ready_lane_rankings"]} == {
        "locked-baselines",
        "replay-engine-hf",
        "ml-trading",
        "llm-strategy",
    }
    assert payload["baseline_controllers"][0]["baseline_locked_flag"] is True
    assert payload["baseline_controllers"][0]["promotion_bucket"] == "live_ready"
    assert payload["baseline_controllers"][0]["live_observed_result"]["live_observed_flag"] is True
    assert payload["baseline_controllers"][0]["stale_signal_suppressed_count"] == 13
    assert payload["deterministic_hf_compare_ready"][0]["candidate_id"] == "inversion"
    assert {
        row["candidate_id"] for row in payload["deterministic_hf_compare_ready"]
    } == {"inversion", "quarter_open_reprice", "micro_momentum_continuation", "lead_fragility", "winner_definition"}
    assert {row["candidate_id"] for row in payload["deterministic_hf_live_probe"]} == {
        "quarter_open_reprice",
        "micro_momentum_continuation",
    }
    assert payload["deterministic_hf_shadow_only"][0]["candidate_id"] == "inversion"
    assert payload["deterministic_hf_bench_only"][0]["candidate_id"] == "winner_definition"
    assert payload["deterministic_hf_pending"] == []
    assert payload["ml_candidates"][0]["candidate_id"] == "ml_controller_focus_calibrator_v2"
    assert payload["ml_candidates"][0]["comparison_ready_flag"] is True
    assert payload["ml_candidates"][0]["promotion_bucket"] == "shadow_only"
    assert payload["ml_candidates"][0]["live_observed_result"]["live_observed_flag"] is False
    assert payload["llm_candidates"][0]["candidate_id"] == "llm_template_compiler_v1"
    assert payload["llm_candidates"][0]["promotion_bucket"] == "shadow_only"
    assert payload["compare_ready_ranking"][0]["candidate_id"] == "llm_template_compiler_v1"
    assert payload["live_ready_ranking"][0]["candidate_id"] == "controller_vnext_unified_v1 :: balanced"
    assert {row["candidate_id"] for row in payload["live_probe_ranking"]} == {
        "quarter_open_reprice",
        "micro_momentum_continuation",
    }
    assert payload["shadow_only_candidates"][0]["lane_id"] == "llm-strategy"
    assert payload["bench_only_candidates"][0]["candidate_id"] == "winner_definition"
    assert payload["merge_recommendation"]["merge_now"][0]["lane_id"] == "replay-engine-hf"
    assert payload["merge_recommendation"]["merge_now"][1]["lane_id"] == "ml-trading"
    assert payload["merge_recommendation"]["wait"][0]["lane_id"] == "llm-strategy"
    assert {row["candidate_id"] for row in payload["current_promoted_stack"]["live_probe"]} == {
        "quarter_open_reprice",
        "micro_momentum_continuation",
    }
    assert "quarter_open_reprice" in payload["current_promoted_stack"]["operator_note"]
    assert payload["finalists"][0]["candidate_id"] == "controller_vnext_unified_v1 :: balanced"
    assert payload["finalists"][2]["candidate_id"] == "llm_template_compiler_v1"
    assert payload["finalists"][3]["candidate_id"] == "ml_controller_focus_calibrator_v2"
    assert payload["compare_ready_criteria"]["version"] == "compare_ready_v1"
    assert payload["submission_examples"]["schema_version"] == "submission_example_v1"
    assert payload["result_modes"][1]["id"] == "replay_result"
    assert payload["divergence_summary"][0]["signal_count"] == 19


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


def test_analysis_studio_backtests_route_lists_strategy_families_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path)
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/backtests",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_version"] == "v1_2_0"
    assert payload["families"][0]["strategy_family"] == "reversion"
    assert payload["families"][0]["summary"]["candidate_label"] == "keep"
    assert payload["families"][0]["artifact_paths"]["trade_traces_json"].endswith("reversion_trade_traces.json")


def test_analysis_studio_backtest_family_route_returns_bounded_detail_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path)
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/backtests/reversion",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
            "trade_limit": 1,
            "context_limit": 1,
            "trace_limit": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_family"] == "reversion"
    assert payload["summary"]["candidate_label"] == "keep"
    assert len(payload["best_trades"]) == 1
    assert payload["best_trades"][0]["game_id"] == "game_001"
    assert len(payload["worst_trades"]) == 1
    assert payload["context_summary"][0]["context_bucket"] == "Q2|trail_1_4"
    assert len(payload["trade_traces"]) == 1
    assert payload["trade_traces"][0]["states"][0]["team_price"] == 0.58


def test_analysis_studio_backtest_family_route_maps_unknown_family_to_404_pytest(tmp_path: Path) -> None:
    _write_consumer_fixture(tmp_path)
    client = TestClient(create_app())

    response = client.get(
        "/v1/analysis/studio/backtests/volatility_scalp",
        params={
            "season": "2025-26",
            "season_phase": "regular_season",
            "output_root": str(tmp_path),
        },
    )

    assert response.status_code == 404
    assert "Unknown strategy_family" in response.json()["error"]["message"]


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
