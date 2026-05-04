from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS
from app.data.pipelines.daily.nba.analysis.ml_trading_lane import (
    MLTradingLaneRequest,
    build_expanding_predictions,
    build_ml_feature_schema,
    build_ml_shadow_payload_schema,
    build_replay_candidate_dataset,
    run_ml_trading_lane,
)


def _standard_trade_row(
    *,
    subject_name: str,
    strategy_family: str,
    game_id: str,
    entry_at: str,
    entry_state_index: int,
    team_side: str = "home",
    signal_strength: float = 0.8,
    raw_confidence: float | None = None,
) -> dict[str, object]:
    row = {
        "season": "2025-26",
        "season_phase": "playoffs",
        "analysis_version": "v1_0_1",
        "strategy_family": strategy_family,
        "entry_rule": "entry",
        "exit_rule": "exit",
        "game_id": game_id,
        "team_side": team_side,
        "team_slug": "HOME",
        "opponent_team_slug": "AWAY",
        "opening_band": "40-50",
        "period_label": "Q4",
        "score_diff_bucket": "lead_1_4",
        "context_bucket": "Q4|lead_1_4",
        "context_tags_json": "{}",
        "entry_metadata_json": "{}",
        "signal_strength": signal_strength,
        "entry_state_index": entry_state_index,
        "exit_state_index": entry_state_index + 1,
        "entry_at": entry_at,
        "exit_at": entry_at,
        "entry_price": 0.52,
        "exit_price": 0.58,
        "gross_return": 0.10,
        "gross_return_with_slippage": 0.10,
        "max_favorable_excursion_after_entry": 0.06,
        "max_adverse_excursion_after_entry": 0.01,
        "hold_time_seconds": 60.0,
        "slippage_cents": 0,
        "subject_name": subject_name,
        "subject_type": "controller" if "::" in subject_name else "family",
    }
    if raw_confidence is not None:
        row["unified_router_default_confidence"] = raw_confidence
    return row


def _replay_trade_row(
    *,
    subject_name: str,
    strategy_family: str,
    game_id: str,
    entry_at: str,
    entry_state_index: int,
    team_side: str = "home",
    replay_return: float = 0.10,
) -> dict[str, object]:
    row = _standard_trade_row(
        subject_name=subject_name,
        strategy_family=strategy_family,
        game_id=game_id,
        entry_at=entry_at,
        entry_state_index=entry_state_index,
        team_side=team_side,
        signal_strength=0.8,
    )
    row["gross_return_with_slippage"] = replay_return
    row["engine_mode"] = "execution_replay"
    row["execution_profile_version"] = "replay_v1"
    row["signal_age_seconds_at_submit"] = 4.0
    row["quote_age_seconds_at_submit"] = 8.0
    row["spread_cents_at_submit"] = 1.0
    row["submission_window_label"] = "signal_fresh"
    row["replay_trade_status"] = "executed"
    row["subject_name"] = subject_name
    row["subject_type"] = "controller" if "::" in subject_name else "family"
    return row


def test_build_replay_candidate_dataset_joins_execution_and_history_pytest() -> None:
    signal_summary_df = pd.DataFrame(
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500101",
                "team_side": "home",
                "strategy_family": "inversion",
                "entry_state_index": 10,
                "exit_state_index": 11,
                "signal_entry_at": "2026-04-20T00:10:00Z",
                "signal_exit_at": "2026-04-20T00:11:00Z",
                "signal_entry_price": 0.52,
                "signal_exit_price": 0.58,
                "executed_flag": True,
                "no_trade_reason": None,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "game_id": "0042500102",
                "team_side": "home",
                "strategy_family": "winner_definition",
                "entry_state_index": 20,
                "exit_state_index": 21,
                "signal_entry_at": "2026-04-21T00:10:00Z",
                "signal_exit_at": "2026-04-21T00:11:00Z",
                "signal_entry_price": 0.62,
                "signal_exit_price": 0.68,
                "executed_flag": False,
                "no_trade_reason": "signal_stale",
            },
        ]
    )
    attempt_trace_df = pd.DataFrame(
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500101",
                "signal_id": "inversion|0042500101|home|10",
                "attempt_stage": "entry",
                "cycle_at": "2026-04-20T00:10:06Z",
                "attempt_index": 0,
                "result": "filled",
                "reason": "entry_filled",
                "entry_state_index": 10,
                "latest_state_index": 10,
                "quote_time": "2026-04-20T00:10:00Z",
                "quote_age_seconds": 6.0,
                "best_bid": 0.51,
                "best_ask": 0.52,
                "spread_cents": 1.0,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "game_id": "0042500102",
                "signal_id": "controller_vnext_unified_v1 :: balanced|0042500102|home|20",
                "attempt_stage": "entry",
                "cycle_at": "2026-04-21T00:11:15Z",
                "attempt_index": 0,
                "result": "no_trade",
                "reason": "signal_stale",
                "entry_state_index": 20,
                "latest_state_index": 24,
                "quote_time": "2026-04-21T00:10:30Z",
                "quote_age_seconds": 45.0,
                "best_bid": 0.60,
                "best_ask": 0.62,
                "spread_cents": 2.0,
            },
        ]
    )
    standard_frames = {
        "inversion": pd.DataFrame(
            [_standard_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0042500101", entry_at="2026-04-20T00:10:00Z", entry_state_index=10)]
        ),
        "controller_vnext_unified_v1 :: balanced": pd.DataFrame(
            [
                _standard_trade_row(
                    subject_name="controller_vnext_unified_v1 :: balanced",
                    strategy_family="winner_definition",
                    game_id="0042500102",
                    entry_at="2026-04-21T00:10:00Z",
                    entry_state_index=20,
                    raw_confidence=0.71,
                )
            ]
        ),
    }
    replay_frames = {
        "inversion": pd.DataFrame(
            [_replay_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0042500101", entry_at="2026-04-20T00:10:00Z", entry_state_index=10)]
        ),
        "controller_vnext_unified_v1 :: balanced": pd.DataFrame(),
    }
    state_panel_df = pd.DataFrame(
        [
            {
                "game_id": "0042500101",
                "team_side": "home",
                "state_index": 10,
                "period": 4,
                "clock_elapsed_seconds": 1800.0,
                "seconds_to_game_end": 120.0,
                "score_diff": 3,
                "lead_changes_so_far": 2,
                "team_led_flag": True,
                "team_trailed_flag": False,
                "market_favorite_flag": True,
                "scoreboard_control_mismatch_flag": False,
                "team_price": 0.52,
                "price_delta_from_open": 0.12,
                "abs_price_delta_from_open": 0.12,
                "net_points_last_5_events": 5.0,
                "gap_before_seconds": 4.0,
                "gap_after_seconds": 5.0,
                "event_at": "2026-04-20T00:10:00Z",
            },
            {
                "game_id": "0042500102",
                "team_side": "home",
                "state_index": 20,
                "period": 4,
                "clock_elapsed_seconds": 1750.0,
                "seconds_to_game_end": 140.0,
                "score_diff": 2,
                "lead_changes_so_far": 1,
                "team_led_flag": True,
                "team_trailed_flag": False,
                "market_favorite_flag": True,
                "scoreboard_control_mismatch_flag": False,
                "team_price": 0.62,
                "price_delta_from_open": 0.10,
                "abs_price_delta_from_open": 0.10,
                "net_points_last_5_events": 4.0,
                "gap_before_seconds": 4.0,
                "gap_after_seconds": 5.0,
                "event_at": "2026-04-21T00:10:00Z",
            },
        ]
    )
    historical_context_df = pd.DataFrame(
        [
            {
                "strategy_family": "inversion",
                "opening_band": "40-50",
                "period_label": "Q4",
                "context_bucket": "Q4|lead_1_4",
                "historical_context_trade_count": 12,
                "historical_context_win_rate": 0.67,
                "historical_context_avg_return": 0.08,
            },
            {
                "strategy_family": "winner_definition",
                "opening_band": "40-50",
                "period_label": "Q4",
                "context_bucket": "Q4|lead_1_4",
                "historical_context_trade_count": 20,
                "historical_context_win_rate": 0.72,
                "historical_context_avg_return": 0.06,
            },
        ]
    )
    historical_family_df = pd.DataFrame(
        [
            {
                "strategy_family": "inversion",
                "historical_family_trade_count": 30,
                "historical_family_win_rate": 0.60,
                "historical_family_avg_return": 0.05,
            },
            {
                "strategy_family": "winner_definition",
                "historical_family_trade_count": 50,
                "historical_family_win_rate": 0.66,
                "historical_family_avg_return": 0.04,
            },
        ]
    )

    dataset_df = build_replay_candidate_dataset(
        signal_summary_df=signal_summary_df,
        attempt_trace_df=attempt_trace_df,
        standard_frames=standard_frames,
        replay_frames=replay_frames,
        state_panel_df=state_panel_df,
        historical_context_df=historical_context_df,
        historical_family_df=historical_family_df,
    )

    assert len(dataset_df) == 2
    assert "standard_gross_return_with_slippage" in dataset_df.columns
    assert "replay_gross_return_with_slippage" in dataset_df.columns
    inversion_row = dataset_df.loc[dataset_df["subject_name"] == "inversion"].iloc[0]
    controller_row = dataset_df.loc[
        dataset_df["subject_name"] == "controller_vnext_unified_v1 :: balanced"
    ].iloc[0]
    assert bool(inversion_row["label_replay_executed_flag"]) is True
    assert inversion_row["historical_context_trade_count"] == 12
    assert inversion_row["first_attempt_signal_age_seconds"] == 6.0
    assert inversion_row["state_seconds_to_game_end"] == 120.0
    assert bool(controller_row["label_replay_executed_flag"]) is False
    assert controller_row["raw_confidence"] == 0.71
    assert controller_row["first_attempt_state_lag"] == 4
    assert controller_row["heuristic_execute_score"] < inversion_row["heuristic_execute_score"]


def test_build_expanding_predictions_respects_warmup_dates_pytest() -> None:
    frame = pd.DataFrame(
        [
            {"game_id": "G1", "game_date": "2026-04-15", "signal_strength": 0.2, "strategy_family": "inversion", "heuristic_rank_score": 0.2, "label_replay_positive_flag": 0},
            {"game_id": "G2", "game_date": "2026-04-16", "signal_strength": 0.3, "strategy_family": "inversion", "heuristic_rank_score": 0.3, "label_replay_positive_flag": 0},
            {"game_id": "G3", "game_date": "2026-04-17", "signal_strength": 0.8, "strategy_family": "inversion", "heuristic_rank_score": 0.8, "label_replay_positive_flag": 1},
            {"game_id": "G4", "game_date": "2026-04-18", "signal_strength": 0.9, "strategy_family": "winner_definition", "heuristic_rank_score": 0.9, "label_replay_positive_flag": 1},
        ]
    )

    predictions_df = build_expanding_predictions(
        frame,
        target_column="label_replay_positive_flag",
        numeric_columns=["signal_strength"],
        categorical_columns=["strategy_family"],
        fallback_column="heuristic_rank_score",
        warmup_dates=2,
    )

    assert list(predictions_df["prediction_mode"].iloc[:2]) == ["cold_start_fallback", "cold_start_fallback"]
    assert predictions_df["train_row_count"].iloc[2] == 2
    assert predictions_df["train_row_count"].iloc[3] == 3
    assert all(0.0 <= value <= 1.0 for value in predictions_df["prediction_score"].tolist())


def test_run_ml_trading_lane_end_to_end_with_temp_roots_pytest(tmp_path: Path, monkeypatch) -> None:
    shared_root = tmp_path / "shared"
    replay_root = shared_root / "artifacts" / "replay-engine-hf" / "2025-26" / "postseason_execution_replay"
    replay_root.mkdir(parents=True, exist_ok=True)
    (shared_root / "benchmark_contract").mkdir(parents=True, exist_ok=True)
    (shared_root / "benchmark_contract" / "replay_contract_current.md").write_text(
        "# Replay Contract Current\n- maturity: `stub_v0`\n",
        encoding="utf-8",
    )

    signal_summary_df = pd.DataFrame(
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500101",
                "team_side": "home",
                "signal_id": "inversion|0042500101|home|10",
                "strategy_family": "inversion",
                "entry_state_index": 10,
                "exit_state_index": 11,
                "signal_entry_at": "2026-04-15T00:10:00Z",
                "signal_exit_at": "2026-04-15T00:11:00Z",
                "signal_entry_price": 0.52,
                "signal_exit_price": 0.58,
                "executed_flag": True,
                "no_trade_reason": None,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "game_id": "0042500102",
                "team_side": "home",
                "signal_id": "controller_vnext_unified_v1 :: balanced|0042500102|home|20",
                "strategy_family": "winner_definition",
                "entry_state_index": 20,
                "exit_state_index": 21,
                "signal_entry_at": "2026-04-16T00:10:00Z",
                "signal_exit_at": "2026-04-16T00:11:00Z",
                "signal_entry_price": 0.62,
                "signal_exit_price": 0.68,
                "executed_flag": False,
                "no_trade_reason": "signal_stale",
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500103",
                "team_side": "home",
                "signal_id": "inversion|0042500103|home|30",
                "strategy_family": "inversion",
                "entry_state_index": 30,
                "exit_state_index": 31,
                "signal_entry_at": "2026-04-17T00:10:00Z",
                "signal_exit_at": "2026-04-17T00:11:00Z",
                "signal_entry_price": 0.51,
                "signal_exit_price": 0.60,
                "executed_flag": True,
                "no_trade_reason": None,
            },
        ]
    )
    signal_summary_df.to_csv(replay_root / "replay_signal_summary.csv", index=False)
    pd.DataFrame(
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500101",
                "signal_id": "inversion|0042500101|home|10",
                "attempt_stage": "entry",
                "cycle_at": "2026-04-15T00:10:06Z",
                "attempt_index": 0,
                "result": "filled",
                "reason": "entry_filled",
                "entry_state_index": 10,
                "latest_state_index": 10,
                "quote_time": "2026-04-15T00:10:00Z",
                "quote_age_seconds": 6.0,
                "best_bid": 0.51,
                "best_ask": 0.52,
                "spread_cents": 1.0,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "game_id": "0042500102",
                "signal_id": "controller_vnext_unified_v1 :: balanced|0042500102|home|20",
                "attempt_stage": "entry",
                "cycle_at": "2026-04-16T00:11:15Z",
                "attempt_index": 0,
                "result": "no_trade",
                "reason": "signal_stale",
                "entry_state_index": 20,
                "latest_state_index": 24,
                "quote_time": "2026-04-16T00:10:30Z",
                "quote_age_seconds": 45.0,
                "best_bid": 0.60,
                "best_ask": 0.62,
                "spread_cents": 2.0,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "0042500103",
                "signal_id": "inversion|0042500103|home|30",
                "attempt_stage": "entry",
                "cycle_at": "2026-04-17T00:10:05Z",
                "attempt_index": 0,
                "result": "filled",
                "reason": "entry_filled",
                "entry_state_index": 30,
                "latest_state_index": 30,
                "quote_time": "2026-04-17T00:10:00Z",
                "quote_age_seconds": 5.0,
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread_cents": 1.0,
            },
        ]
    ).to_csv(replay_root / "replay_attempt_trace.csv", index=False)
    pd.DataFrame(
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "standard_trade_count": 2,
                "replay_trade_count": 2,
                "trade_gap": 0,
                "execution_rate": 1.0,
                "standard_avg_return_with_slippage": 0.10,
                "replay_avg_return_with_slippage": 0.10,
                "replay_no_trade_count": 0,
                "top_no_trade_reason": None,
                "standard_ending_bankroll": 11.0,
                "replay_ending_bankroll": 11.0,
                "live_trade_count": 0,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "standard_trade_count": 1,
                "replay_trade_count": 0,
                "trade_gap": -1,
                "execution_rate": 0.0,
                "standard_avg_return_with_slippage": 0.05,
                "replay_avg_return_with_slippage": None,
                "replay_no_trade_count": 1,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 10.5,
                "replay_ending_bankroll": 10.0,
                "live_trade_count": 0,
            },
        ]
    ).to_csv(replay_root / "replay_subject_summary.csv", index=False)
    pd.DataFrame(
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 11.0,
                "compounded_return": 0.10,
                "max_drawdown_pct": 0.0,
                "max_drawdown_amount": 0.0,
                "executed_trade_count": 2,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 11.0,
                "compounded_return": 0.10,
                "max_drawdown_pct": 0.0,
                "max_drawdown_amount": 0.0,
                "executed_trade_count": 2,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "mode": "standard",
                "ending_bankroll": 10.5,
                "compounded_return": 0.05,
                "max_drawdown_pct": 0.0,
                "max_drawdown_amount": 0.0,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "controller_vnext_unified_v1 :: balanced",
                "subject_type": "controller",
                "mode": "replay",
                "ending_bankroll": 10.0,
                "compounded_return": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_amount": 0.0,
                "executed_trade_count": 0,
            },
        ]
    ).to_csv(replay_root / "replay_portfolio_summary.csv", index=False)
    pd.DataFrame(
        [_standard_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0042500101", entry_at="2026-04-15T00:10:00Z", entry_state_index=10),
         _standard_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0042500103", entry_at="2026-04-17T00:10:00Z", entry_state_index=30)]
    ).to_csv(replay_root / "standard_inversion.csv", index=False)
    pd.DataFrame(
        [_replay_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0042500101", entry_at="2026-04-15T00:10:00Z", entry_state_index=10),
         _replay_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0042500103", entry_at="2026-04-17T00:10:00Z", entry_state_index=30)]
    ).to_csv(replay_root / "replay_inversion.csv", index=False)
    pd.DataFrame(
        [
            _standard_trade_row(
                subject_name="controller_vnext_unified_v1 :: balanced",
                strategy_family="winner_definition",
                game_id="0042500102",
                entry_at="2026-04-16T00:10:00Z",
                entry_state_index=20,
                raw_confidence=0.72,
            )
        ]
    ).to_csv(replay_root / "standard_controller_vnext_unified_v1____balanced.csv", index=False)
    pd.DataFrame(columns=[*BACKTEST_TRADE_COLUMNS, "subject_name", "subject_type"]).to_csv(
        replay_root / "replay_controller_vnext_unified_v1____balanced.csv",
        index=False,
    )

    postseason_state_panel_df = pd.DataFrame(
        [
            {
                "game_id": "0042500101",
                "team_side": "home",
                "state_index": 10,
                "period": 4,
                "clock_elapsed_seconds": 1800.0,
                "seconds_to_game_end": 120.0,
                "score_diff": 3,
                "lead_changes_so_far": 2,
                "team_led_flag": True,
                "team_trailed_flag": False,
                "market_favorite_flag": True,
                "scoreboard_control_mismatch_flag": False,
                "team_price": 0.52,
                "price_delta_from_open": 0.12,
                "abs_price_delta_from_open": 0.12,
                "net_points_last_5_events": 5.0,
                "gap_before_seconds": 4.0,
                "gap_after_seconds": 5.0,
                "event_at": "2026-04-15T00:10:00Z",
            },
            {
                "game_id": "0042500102",
                "team_side": "home",
                "state_index": 20,
                "period": 4,
                "clock_elapsed_seconds": 1750.0,
                "seconds_to_game_end": 140.0,
                "score_diff": 2,
                "lead_changes_so_far": 1,
                "team_led_flag": True,
                "team_trailed_flag": False,
                "market_favorite_flag": True,
                "scoreboard_control_mismatch_flag": False,
                "team_price": 0.62,
                "price_delta_from_open": 0.10,
                "abs_price_delta_from_open": 0.10,
                "net_points_last_5_events": 4.0,
                "gap_before_seconds": 4.0,
                "gap_after_seconds": 5.0,
                "event_at": "2026-04-16T00:10:00Z",
            },
            {
                "game_id": "0042500103",
                "team_side": "home",
                "state_index": 30,
                "period": 4,
                "clock_elapsed_seconds": 1700.0,
                "seconds_to_game_end": 160.0,
                "score_diff": 4,
                "lead_changes_so_far": 2,
                "team_led_flag": True,
                "team_trailed_flag": False,
                "market_favorite_flag": True,
                "scoreboard_control_mismatch_flag": False,
                "team_price": 0.51,
                "price_delta_from_open": 0.11,
                "abs_price_delta_from_open": 0.11,
                "net_points_last_5_events": 6.0,
                "gap_before_seconds": 4.0,
                "gap_after_seconds": 5.0,
                "event_at": "2026-04-17T00:10:00Z",
            },
        ]
    )
    monkeypatch.setattr(
        "app.data.pipelines.daily.nba.analysis.ml_trading_lane._load_postseason_state_panel",
        lambda output_root, analysis_version: postseason_state_panel_df,
    )
    monkeypatch.setattr(
        "app.data.pipelines.daily.nba.analysis.ml_trading_lane._load_regular_season_trade_frames",
        lambda output_root: {
            "inversion": pd.DataFrame(
                [
                    _standard_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0022500001", entry_at="2026-02-01T00:10:00Z", entry_state_index=5),
                    _standard_trade_row(subject_name="inversion", strategy_family="inversion", game_id="0022500002", entry_at="2026-02-02T00:10:00Z", entry_state_index=6),
                ]
            ),
            "winner_definition": pd.DataFrame(
                [
                    _standard_trade_row(subject_name="winner_definition", strategy_family="winner_definition", game_id="0022500003", entry_at="2026-02-03T00:10:00Z", entry_state_index=7),
                ]
            ),
        },
    )
    monkeypatch.setattr(
        "app.data.pipelines.daily.nba.analysis.ml_trading_lane.build_unified_benchmark_dashboard",
        lambda request: {
            "ml_candidates": [
                {"candidate_id": "ml_focus_family_reranker_v2"},
                {"candidate_id": "ml_controller_focus_calibrator_v2"},
                {"candidate_id": "ml_sidecar_union_v2"},
            ]
        },
    )

    payload = run_ml_trading_lane(
        MLTradingLaneRequest(
            season="2025-26",
            shared_root=str(shared_root),
            analysis_output_root=str(tmp_path / "analysis_output"),
            warmup_dates=1,
            gate_threshold=0.25,
        )
    )

    submission_path = Path(payload["reports"]["benchmark_submission_json"])
    feature_schema_path = Path(payload["artifacts"]["feature_schema_json"])
    shadow_schema_path = Path(payload["artifacts"]["shadow_payload_schema_json"])
    shadow_sample_path = Path(payload["artifacts"]["shadow_payload_sample_json"])
    live_shadow_sample_path = Path(payload["artifacts"]["live_shadow_payload_sample_json"])
    shadow_comparison_path = Path(payload["artifacts"]["shadow_variant_comparison_csv"])
    live_shadow_summary_path = Path(payload["artifacts"]["live_shadow_variant_summary_csv"])
    daily_handoff_path = Path(payload["reports"]["daily_live_validation_shared_handoff_markdown"])
    assert submission_path.exists()
    assert feature_schema_path.exists()
    assert shadow_schema_path.exists()
    assert shadow_sample_path.exists()
    assert live_shadow_sample_path.exists()
    assert shadow_comparison_path.exists()
    assert live_shadow_summary_path.exists()
    assert daily_handoff_path.exists()
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    shadow_sample = json.loads(shadow_sample_path.read_text(encoding="utf-8"))
    assert submission["lane_id"] == "ml-trading"
    assert len(submission["subjects"]) == 3
    assert submission["shadow_operational_support"]["schema_version"] == "ml_shadow_payload_v2"
    assert {row["candidate_id"] for row in submission["subjects"]} == {
        "ml_focus_family_reranker_v2",
        "ml_controller_focus_calibrator_v2",
        "ml_sidecar_union_v2",
    }
    assert shadow_sample["schema_version"] == "ml_shadow_payload_v2"
    assert shadow_sample["required_live_fields"] == [
        "sidecar_probability",
        "calibrated_confidence",
        "calibrated_execution_likelihood",
        "focus_family_flag",
        "feed_fresh_flag",
        "orderbook_available_flag",
        "min_required_notional_usd",
        "budget_affordable_flag",
    ]
    focus_subject = next(row for row in submission["subjects"] if row["candidate_id"] == "ml_focus_family_reranker_v2")
    focus_standard_df = pd.read_csv(focus_subject["artifacts"]["standard_trades_csv"])
    focus_replay_df = pd.read_csv(focus_subject["artifacts"]["replay_trades_csv"])
    calibrator_shadow_df = pd.read_csv(payload["artifacts"]["shadow_payload_calibrator_only_csv"])
    assert "game_id" in focus_standard_df.columns
    assert "gross_return_with_slippage" in focus_replay_df.columns
    assert {
        "sidecar_probability",
        "calibrated_confidence",
        "calibrated_execution_likelihood",
        "focus_family_flag",
        "feed_fresh_flag",
        "orderbook_available_flag",
        "min_required_notional_usd",
        "budget_affordable_flag",
    }.issubset(set(calibrator_shadow_df.columns))
    live_shadow_summary_df = pd.read_csv(live_shadow_summary_path)
    assert "replay_profitable_but_live_unexecutable_count" in live_shadow_summary_df.columns
    assert len(focus_standard_df) >= 1
    assert any(row["candidate_id"] == "ml_focus_family_reranker_v2" for row in payload["dashboard_ingest_preview"])
    assert "first operational role" in daily_handoff_path.read_text(encoding="utf-8").lower()


def test_build_ml_feature_schema_has_expected_groups_pytest() -> None:
    schema = build_ml_feature_schema()
    assert schema["schema_version"] == "ml_lane_v2"
    assert "ranking_numeric" in schema["feature_groups"]
    assert "gate_numeric" in schema["feature_groups"]
    assert any(row["column"] == "label_replay_executed_flag" for row in schema["columns"])


def test_build_ml_shadow_payload_schema_has_required_fields_pytest() -> None:
    schema = build_ml_shadow_payload_schema()
    assert schema["schema_version"] == "ml_shadow_payload_v2"
    assert schema["required_live_fields"] == [
        "sidecar_probability",
        "calibrated_confidence",
        "calibrated_execution_likelihood",
        "focus_family_flag",
        "feed_fresh_flag",
        "orderbook_available_flag",
        "min_required_notional_usd",
        "budget_affordable_flag",
    ]
    assert any(row["column"] == "shadow_selected_flag" for row in schema["columns"])
