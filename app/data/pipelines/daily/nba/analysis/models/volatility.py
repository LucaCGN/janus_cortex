from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.data.pipelines.daily.nba.analysis.artifacts import write_frame
from app.data.pipelines.daily.nba.analysis.models.features import (
    apply_numeric_normalisation,
    auc_score,
    brier_score,
    fit_logistic_regression,
    log_loss,
    naive_classification_comparison,
    normalise_numeric_matrix,
    prepare_state_model_frame,
    sigmoid,
    split_frame_by_cutoff,
)


VOLATILITY_FEATURE_COLUMNS = [
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


def _build_validation_frame(
    validation_df: pd.DataFrame,
    *,
    feature_columns: list[str],
    coefficients: np.ndarray,
    normalisation: dict[str, list[float]],
    target_column: str,
    train_base_rate: float,
) -> pd.DataFrame:
    x_validation = apply_numeric_normalisation(validation_df[feature_columns], feature_columns, normalisation)
    validation_scores = sigmoid(x_validation @ coefficients)
    frame = validation_df[
        [
            column
            for column in ("game_id", "team_side", "game_date", "state_index", target_column)
            if column in validation_df.columns
        ]
    ].copy()
    frame["target"] = validation_df[target_column].astype(int).to_numpy(dtype=int)
    frame["prediction"] = validation_scores
    frame["naive_prediction"] = float(train_base_rate)
    frame["residual"] = frame["target"] - frame["prediction"]
    return frame


def run_volatility_inversion_baseline(
    state_df: pd.DataFrame,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    train_cutoff: pd.Timestamp | None,
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    work = prepare_state_model_frame(state_df)
    work = work.dropna(subset=VOLATILITY_FEATURE_COLUMNS + ["crossed_50c_next_12_states_flag", "game_date"]).copy()
    if work.empty or work["crossed_50c_next_12_states_flag"].nunique() < 2:
        return (
            {
                "status": "insufficient_data",
                "target": "crossed_50c_next_12_states_flag",
                "reason": "need both classes and enough rows to train/validate",
                "train_rows": int(len(work)),
                "validation_rows": 0,
            },
            {},
        )

    train_df, validation_df = split_frame_by_cutoff(work, cutoff=train_cutoff)
    if train_df.empty or validation_df.empty or train_df["crossed_50c_next_12_states_flag"].nunique() < 2:
        return (
            {
                "status": "insufficient_data",
                "target": "crossed_50c_next_12_states_flag",
                "reason": "time split or class balance was too thin",
                "train_rows": int(len(train_df)),
                "validation_rows": int(len(validation_df)),
            },
            {},
        )

    x_train, normalisation = normalise_numeric_matrix(train_df[VOLATILITY_FEATURE_COLUMNS], VOLATILITY_FEATURE_COLUMNS)
    y_train = train_df["crossed_50c_next_12_states_flag"].astype(int).to_numpy(dtype=float)
    x_validation = apply_numeric_normalisation(validation_df[VOLATILITY_FEATURE_COLUMNS], VOLATILITY_FEATURE_COLUMNS, normalisation)
    y_validation = validation_df["crossed_50c_next_12_states_flag"].astype(int).to_numpy(dtype=float)
    coefficients = fit_logistic_regression(x_train, y_train)
    validation_scores = sigmoid(x_validation @ coefficients)
    base_rate = float(y_train.mean())

    validation_frame = _build_validation_frame(
        validation_df,
        feature_columns=VOLATILITY_FEATURE_COLUMNS,
        coefficients=coefficients,
        normalisation=normalisation,
        target_column="crossed_50c_next_12_states_flag",
        train_base_rate=base_rate,
    )
    coefficients_frame = pd.DataFrame(_format_coefficients(coefficients, VOLATILITY_FEATURE_COLUMNS))
    artifacts = {
        "coefficients_csv": write_frame(output_dir / "volatility_inversion_coefficients", coefficients_frame)["csv"],
        "validation_csv": write_frame(output_dir / "volatility_inversion_validation", validation_frame)["csv"],
    }
    payload = {
        "status": "success",
        "model_family": "logistic_regression_baseline",
        "target": "crossed_50c_next_12_states_flag",
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(validation_df)),
        "metrics": {
            "brier": brier_score(y_validation, validation_scores),
            "log_loss": log_loss(y_validation, validation_scores),
            "auc": auc_score(y_validation, validation_scores),
            "naive_brier": brier_score(y_validation, np.full(len(y_validation), base_rate, dtype=float)),
            "naive_log_loss": log_loss(y_validation, np.full(len(y_validation), base_rate, dtype=float)),
        },
        "naive_comparison": naive_classification_comparison(y_validation, validation_scores, base_rate=base_rate),
        "coefficients": _format_coefficients(coefficients, VOLATILITY_FEATURE_COLUMNS),
    }
    return payload, artifacts


__all__ = [
    "VOLATILITY_FEATURE_COLUMNS",
    "run_volatility_inversion_baseline",
]
