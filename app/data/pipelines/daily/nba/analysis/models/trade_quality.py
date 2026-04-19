from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.data.pipelines.daily.nba.analysis.artifacts import write_frame
from app.data.pipelines.daily.nba.analysis.models.features import (
    apply_numeric_normalisation,
    fit_ols,
    naive_regression_comparison,
    normalise_numeric_matrix,
    prepare_state_model_frame,
    spearman_corr,
    split_frame_by_cutoff,
)


TRADE_QUALITY_FEATURE_COLUMNS = [
    "opening_price",
    "team_price",
    "abs_price_delta_from_open",
    "abs_score_diff",
    "seconds_to_game_end",
    "period",
    "market_favorite_flag",
    "team_led_flag",
    "scoreboard_control_mismatch_flag",
    "net_points_last_5_events",
]


def _format_coefficients(coefficients: np.ndarray, feature_columns: list[str]) -> list[dict[str, Any]]:
    return [{"feature": "intercept", "value": float(coefficients[0])}] + [
        {"feature": feature, "value": float(coefficients[index + 1])} for index, feature in enumerate(feature_columns)
    ]


def _validation_frame(
    validation_df: pd.DataFrame,
    *,
    feature_columns: list[str],
    coefficients: np.ndarray,
    normalisation: dict[str, list[float]],
    target_column: str,
    train_mean: float,
) -> pd.DataFrame:
    x_validation = apply_numeric_normalisation(validation_df[feature_columns], feature_columns, normalisation)
    predictions = x_validation @ coefficients
    frame = validation_df[
        [
            column
            for column in ("game_id", "team_side", "game_date", "state_index", target_column)
            if column in validation_df.columns
        ]
    ].copy()
    frame["target"] = validation_df[target_column].to_numpy(dtype=float)
    frame["prediction"] = predictions
    frame["naive_prediction"] = float(train_mean)
    frame["residual"] = frame["target"] - frame["prediction"]
    return frame


def _train_single_target(
    *,
    train_df: pd.DataFrame,
    validation_df: pd.DataFrame,
    target_column: str,
    output_dir: Path,
    feature_columns: list[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    if train_df.empty or validation_df.empty:
        return {"status": "insufficient_data", "target": target_column}, {}
    x_train, normalisation = normalise_numeric_matrix(train_df[feature_columns], feature_columns)
    x_validation = apply_numeric_normalisation(validation_df[feature_columns], feature_columns, normalisation)
    y_train = train_df[target_column].to_numpy(dtype=float)
    y_validation = validation_df[target_column].to_numpy(dtype=float)
    coefficients = fit_ols(x_train, y_train)
    predictions = x_validation @ coefficients
    train_mean = float(np.mean(y_train))
    validation_frame = _validation_frame(
        validation_df,
        feature_columns=feature_columns,
        coefficients=coefficients,
        normalisation=normalisation,
        target_column=target_column,
        train_mean=train_mean,
    )
    coefficients_frame = pd.DataFrame(_format_coefficients(coefficients, feature_columns))
    artifacts = {
        f"{target_column}_coefficients_csv": write_frame(output_dir / f"{target_column}_coefficients", coefficients_frame)["csv"],
        f"{target_column}_validation_csv": write_frame(output_dir / f"{target_column}_validation", validation_frame)["csv"],
    }
    return (
        {
            "rmse": float(np.sqrt(np.mean((predictions - y_validation) ** 2))),
            "mae": float(np.mean(np.abs(predictions - y_validation))),
            "rank_corr": spearman_corr(y_validation.tolist(), predictions.tolist()),
            "naive_rmse": float(np.sqrt(np.mean((np.full(len(y_validation), train_mean, dtype=float) - y_validation) ** 2))),
            "naive_mae": float(np.mean(np.abs(np.full(len(y_validation), train_mean, dtype=float) - y_validation))),
            "naive_comparison": naive_regression_comparison(y_validation, predictions, train_mean=train_mean),
            "coefficients": _format_coefficients(coefficients, feature_columns),
        },
        artifacts,
    )


def run_trade_quality_baseline(
    state_df: pd.DataFrame,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    train_cutoff: pd.Timestamp | None,
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    work = prepare_state_model_frame(state_df)
    work = work.dropna(subset=TRADE_QUALITY_FEATURE_COLUMNS + ["mfe_from_state", "mae_from_state", "game_date"]).copy()
    if len(work) < 20:
        return (
            {
                "status": "insufficient_data",
                "reason": "need at least 20 scored rows to fit both regression targets",
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

    targets: dict[str, Any] = {}
    artifacts: dict[str, str] = {}
    for target_column in ("mfe_from_state", "mae_from_state"):
        target_payload, target_artifacts = _train_single_target(
            train_df=train_df,
            validation_df=validation_df,
            target_column=target_column,
            output_dir=output_dir,
            feature_columns=TRADE_QUALITY_FEATURE_COLUMNS,
        )
        targets[target_column] = target_payload
        artifacts.update(target_artifacts)

    return (
        {
            "status": "success",
            "model_family": "ols_regression_baseline",
            "train_rows": int(len(train_df)),
            "validation_rows": int(len(validation_df)),
            "targets": targets,
        },
        artifacts,
    )


__all__ = [
    "TRADE_QUALITY_FEATURE_COLUMNS",
    "run_trade_quality_baseline",
]
