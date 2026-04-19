from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.data.pipelines.daily.nba.analysis.artifacts import write_frame
from app.data.pipelines.daily.nba.analysis.contracts import DEFAULT_WINNER_THRESHOLDS
from app.data.pipelines.daily.nba.analysis.models.features import naive_regression_comparison, prepare_state_model_frame, split_frame_by_cutoff


WINNER_TIMING_GROUP_COLUMNS = ["period_label", "score_diff_bucket", "opening_band"]


def _validation_frame(
    validation_df: pd.DataFrame,
    *,
    predictions: np.ndarray,
    train_mean: float,
) -> pd.DataFrame:
    frame = validation_df[
        [
            column
            for column in ("game_id", "team_side", "game_date", "state_index", "period_label", "score_diff_bucket", "opening_band")
            if column in validation_df.columns
        ]
    ].copy()
    frame["target"] = validation_df["time_to_stable_seconds"].to_numpy(dtype=float)
    frame["prediction"] = predictions
    frame["naive_prediction"] = float(train_mean)
    frame["residual"] = frame["target"] - frame["prediction"]
    return frame


def _build_winner_training_frame(profiles_df: pd.DataFrame, state_df: pd.DataFrame) -> pd.DataFrame:
    if profiles_df.empty or state_df.empty:
        return pd.DataFrame()
    winners = profiles_df[
        (profiles_df["final_winner_flag"] == True) & profiles_df["winner_stable_80_clock_elapsed_seconds"].notna()
    ][["game_id", "team_side", "winner_stable_80_clock_elapsed_seconds"]].copy()
    if winners.empty:
        return pd.DataFrame()
    work = state_df.merge(winners, on=["game_id", "team_side"], how="inner")
    work = work.dropna(subset=["clock_elapsed_seconds", "game_date"]).copy()
    if work.empty:
        return pd.DataFrame()
    work["time_to_stable_seconds"] = (
        pd.to_numeric(work["winner_stable_80_clock_elapsed_seconds"], errors="coerce")
        - pd.to_numeric(work["clock_elapsed_seconds"], errors="coerce")
    ).clip(lower=0.0)
    work = work.dropna(subset=["time_to_stable_seconds"]).copy()
    return work


def run_winner_definition_timing_baseline(
    *,
    profiles_df: pd.DataFrame,
    state_df: pd.DataFrame,
    season: str,
    season_phase: str,
    analysis_version: str,
    train_cutoff: pd.Timestamp | None,
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    work = _build_winner_training_frame(profiles_df, state_df)
    if len(work) < 20:
        return (
            {
                "status": "insufficient_data",
                "reason": "need at least 20 winner-labeled states for grouped hazard proxy",
                "train_rows": int(len(work)),
                "validation_rows": 0,
            },
            {},
        )
    train_df, validation_df = split_frame_by_cutoff(work, cutoff=train_cutoff)
    if train_df.empty or validation_df.empty:
        return (
            {
                "status": "insufficient_data",
                "reason": "time split produced empty train or validation partition",
                "train_rows": int(len(train_df)),
                "validation_rows": int(len(validation_df)),
            },
            {},
        )

    hazard_table = (
        train_df.groupby(WINNER_TIMING_GROUP_COLUMNS)
        .agg(
            sample_states=("game_id", "count"),
            avg_time_to_stable_seconds=("time_to_stable_seconds", "mean"),
        )
        .reset_index()
    )
    default_mean = float(train_df["time_to_stable_seconds"].mean())
    validation_scored = validation_df.merge(hazard_table, on=WINNER_TIMING_GROUP_COLUMNS, how="left")
    validation_scored["predicted_time_to_stable_seconds"] = validation_scored["avg_time_to_stable_seconds"].fillna(default_mean)
    y_true = validation_scored["time_to_stable_seconds"].to_numpy(dtype=float)
    y_pred = validation_scored["predicted_time_to_stable_seconds"].to_numpy(dtype=float)
    validation_frame = _validation_frame(validation_scored, predictions=y_pred, train_mean=default_mean)
    artifacts = {
        "hazard_table_csv": write_frame(output_dir / "winner_definition_timing_hazard_table", hazard_table)["csv"],
        "validation_csv": write_frame(output_dir / "winner_definition_timing_validation", validation_frame)["csv"],
    }
    return (
        {
            "status": "success",
            "model_family": "grouped_hazard_proxy",
            "target": "time_to_stable_80_seconds",
            "train_rows": int(len(train_df)),
            "validation_rows": int(len(validation_df)),
            "metrics": {
                "rmse": float(np.sqrt(np.mean((y_pred - y_true) ** 2))),
                "mae": float(np.mean(np.abs(y_pred - y_true))),
                "rank_corr": float(pd.Series(y_true).rank().corr(pd.Series(y_pred).rank(), method="pearson"))
                if len(y_true) >= 2
                else None,
                "naive_rmse": float(np.sqrt(np.mean((np.full(len(y_true), default_mean, dtype=float) - y_true) ** 2))),
                "naive_mae": float(np.mean(np.abs(np.full(len(y_true), default_mean, dtype=float) - y_true))),
            },
            "naive_comparison": naive_regression_comparison(y_true, y_pred, train_mean=default_mean),
            "hazard_like_table": hazard_table.sort_values(["sample_states", "avg_time_to_stable_seconds"], ascending=[False, True]).head(50).to_dict(orient="records"),
            "winner_thresholds": list(DEFAULT_WINNER_THRESHOLDS),
        },
        artifacts,
    )


__all__ = [
    "WINNER_TIMING_GROUP_COLUMNS",
    "run_winner_definition_timing_baseline",
]
