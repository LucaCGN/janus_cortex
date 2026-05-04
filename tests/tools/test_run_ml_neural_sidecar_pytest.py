from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS
from tools.run_ml_neural_sidecar import (
    CALIBRATED_SUBJECT_ID,
    RAW_SUBJECT_ID,
    NeuralSidecarRequest,
    run_neural_sidecar,
)


def _trade_payload(
    *,
    game_id: str,
    game_date: str,
    strategy_family: str,
    entry_state_index: int,
    return_value: float,
) -> dict[str, object]:
    return {
        "season": "2025-26",
        "season_phase": "regular_season" if game_id.startswith("002") else "playoffs",
        "analysis_version": "v1_0_1",
        "strategy_family": strategy_family,
        "entry_rule": "entry",
        "exit_rule": "exit",
        "game_id": game_id,
        "team_side": "home",
        "team_slug": "HOME",
        "opponent_team_slug": "AWAY",
        "opening_band": "40-50",
        "period_label": "Q4",
        "score_diff_bucket": "lead_1_4",
        "context_bucket": "Q4|lead_1_4",
        "context_tags_json": "{}",
        "entry_metadata_json": "{}",
        "signal_strength": 0.8,
        "entry_state_index": entry_state_index,
        "exit_state_index": entry_state_index + 1,
        "entry_at": f"{game_date}T00:10:00Z",
        "exit_at": f"{game_date}T00:11:00Z",
        "entry_price": 0.52,
        "exit_price": 0.58,
        "gross_return": return_value,
        "gross_return_with_slippage": return_value,
        "max_favorable_excursion_after_entry": 0.06,
        "max_adverse_excursion_after_entry": 0.01,
        "hold_time_seconds": 60.0,
        "slippage_cents": 0,
    }


def _candidate_row(
    *,
    evaluation_slice: str,
    game_id: str,
    game_date: str,
    strategy_family: str,
    entry_state_index: int,
    positive: bool,
    executed: bool,
    score: float,
) -> dict[str, object]:
    signal_id = f"{strategy_family}|{game_id}|home|{entry_state_index}"
    standard_trade = _trade_payload(
        game_id=game_id,
        game_date=game_date,
        strategy_family=strategy_family,
        entry_state_index=entry_state_index,
        return_value=0.10 if positive else -0.05,
    )
    replay_trade = _trade_payload(
        game_id=game_id,
        game_date=game_date,
        strategy_family=strategy_family,
        entry_state_index=entry_state_index,
        return_value=0.12 if positive else -0.08,
    )
    row: dict[str, object] = {
        "signal_id": signal_id,
        "underlying_candidate_id": signal_id,
        "subject_name": strategy_family,
        "subject_type": "family",
        "candidate_kind": "replay_family",
        "strategy_family": strategy_family,
        "focus_family_flag": strategy_family == "inversion",
        "season_phase": "regular_season" if evaluation_slice == "training_history" else "playoffs",
        "replay_artifact_name": "unit",
        "game_id": game_id,
        "game_date": game_date,
        "team_side": "home",
        "team_slug": "HOME",
        "opponent_team_slug": "AWAY",
        "opening_band": "40-50",
        "period_label": "Q4",
        "score_diff_bucket": "lead_1_4",
        "context_bucket": "Q4|lead_1_4",
        "entry_state_index": entry_state_index,
        "exit_state_index": entry_state_index + 1,
        "signal_entry_at": f"{game_date}T00:10:00Z",
        "signal_exit_at": f"{game_date}T00:11:00Z",
        "entry_price": 0.52,
        "signal_strength": score,
        "raw_confidence": score,
        "historical_context_trade_count": 10,
        "historical_context_win_rate": 0.55,
        "historical_context_avg_return": 0.02,
        "historical_family_trade_count": 20,
        "historical_family_win_rate": 0.58,
        "historical_family_avg_return": 0.03,
        "state_seconds_to_game_end": 120.0,
        "state_score_diff": 3,
        "state_lead_changes_so_far": 2,
        "state_net_points_last_5_events": 5.0,
        "state_abs_price_delta_from_open": 0.11,
        "state_gap_before_seconds": 4.0,
        "state_gap_after_seconds": 5.0,
        "first_attempt_signal_age_seconds": 5.0,
        "first_attempt_quote_age_seconds": 8.0,
        "first_attempt_spread_cents": 1.0,
        "first_attempt_state_lag": 0,
        "heuristic_rank_score": score,
        "heuristic_execute_score": score,
        "label_replay_executed_flag": executed,
        "label_replay_positive_flag": positive,
        "label_replay_return": 0.12 if positive else -0.08,
        "label_replay_value": 0.12 if positive else -0.08,
        "no_trade_reason": None if executed else "signal_stale",
        "evaluation_slice": evaluation_slice,
    }
    for column in BACKTEST_TRADE_COLUMNS:
        row[f"standard_{column}"] = standard_trade[column]
        row[f"replay_{column}"] = replay_trade[column] if executed else None
    return row


def test_run_neural_sidecar_writes_shadow_artifacts_pytest(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared"
    source_root = shared_root / "artifacts" / "ml-trading-lane" / "2025-26" / "expanded_regular_replay_ml_v1"
    source_root.mkdir(parents=True, exist_ok=True)
    rows = [
        _candidate_row(evaluation_slice="training_history", game_id="0022500001", game_date="2025-10-22", strategy_family="inversion", entry_state_index=1, positive=False, executed=False, score=0.2),
        _candidate_row(evaluation_slice="training_history", game_id="0022500002", game_date="2025-10-23", strategy_family="winner_definition", entry_state_index=2, positive=True, executed=True, score=0.8),
        _candidate_row(evaluation_slice="training_history", game_id="0022500003", game_date="2025-10-24", strategy_family="inversion", entry_state_index=3, positive=False, executed=False, score=0.3),
        _candidate_row(evaluation_slice="training_history", game_id="0022500004", game_date="2025-10-25", strategy_family="winner_definition", entry_state_index=4, positive=True, executed=True, score=0.7),
        _candidate_row(evaluation_slice="training_history", game_id="0022500005", game_date="2025-10-26", strategy_family="inversion", entry_state_index=5, positive=False, executed=False, score=0.4),
        _candidate_row(evaluation_slice="training_history", game_id="0022500006", game_date="2025-10-27", strategy_family="winner_definition", entry_state_index=6, positive=True, executed=True, score=0.9),
        _candidate_row(evaluation_slice="postseason_holdout", game_id="0042500101", game_date="2026-04-20", strategy_family="inversion", entry_state_index=10, positive=True, executed=True, score=0.85),
        _candidate_row(evaluation_slice="postseason_holdout", game_id="0042500101", game_date="2026-04-20", strategy_family="winner_definition", entry_state_index=11, positive=False, executed=False, score=0.15),
        _candidate_row(evaluation_slice="postseason_holdout", game_id="0042500102", game_date="2026-04-21", strategy_family="inversion", entry_state_index=20, positive=False, executed=False, score=0.25),
        _candidate_row(evaluation_slice="postseason_holdout", game_id="0042500102", game_date="2026-04-21", strategy_family="winner_definition", entry_state_index=21, positive=True, executed=True, score=0.75),
    ]
    pd.DataFrame(rows).to_csv(source_root / "all_candidates.csv", index=False)
    report_root = shared_root / "reports" / "ml-trading-lane"
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / "benchmark_submission.json").write_text(
        json.dumps({"lane_id": "ml-trading", "subjects": []}),
        encoding="utf-8",
    )

    payload = run_neural_sidecar(
        NeuralSidecarRequest(
            shared_root=str(shared_root),
            epochs=5,
            hidden_units=4,
            learning_rate=0.01,
        )
    )

    artifact_root = Path(payload["artifact_root"])
    assert (artifact_root / "feature_schema.json").exists()
    assert (artifact_root / "neural_predictions.csv").exists()
    assert (artifact_root / "shadow_payload_calibrated.csv").exists()
    submission = json.loads((report_root / "benchmark_submission.json").read_text(encoding="utf-8"))
    assert {subject["candidate_id"] for subject in submission["subjects"]} == {
        RAW_SUBJECT_ID,
        CALIBRATED_SUBJECT_ID,
    }
    assert submission["neural_shadow_support"]["promotion"] == "shadow_only"
    assert submission["neural_shadow_support"]["live_routing_change"] is False
    report = (report_root / "neural_sidecar_report.md").read_text(encoding="utf-8")
    assert "shadow-only" in report.lower()
