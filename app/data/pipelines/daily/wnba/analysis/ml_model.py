from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.data.pipelines.daily.wnba.analysis.contracts import WNBA_FEATURE_VERSION


WNBA_SHORT_HORIZON_FEATURE_COLUMNS = (
    "period",
    "seconds_to_game_end",
    "score_diff",
    "recent_net_points",
    "team_price",
    "spread",
)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))


def _log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    clipped = np.clip(y_prob, 1e-6, 1.0 - 1e-6)
    return float(-(y_true * np.log(clipped) + (1.0 - y_true) * np.log(1.0 - clipped)).mean())


def _prepare_training_frame(feature_df: pd.DataFrame, *, target_column: str) -> pd.DataFrame:
    if feature_df.empty:
        return pd.DataFrame()
    required = {"game_id", "label_status", target_column, *WNBA_SHORT_HORIZON_FEATURE_COLUMNS}
    missing = required - set(feature_df.columns)
    if missing:
        raise ValueError(f"feature_df missing required columns: {sorted(missing)}")
    work = feature_df[feature_df["label_status"] == "labeled"].copy()
    if work.empty:
        return work
    work[target_column] = work[target_column].map(lambda value: bool(value) if value is not None and not pd.isna(value) else None)
    work = work[work[target_column].notna()].copy()
    for column in WNBA_SHORT_HORIZON_FEATURE_COLUMNS:
        work[column] = work[column].map(_safe_float)
    work = work.dropna(subset=list(WNBA_SHORT_HORIZON_FEATURE_COLUMNS)).reset_index(drop=True)
    return work


def train_wnba_short_horizon_reprice_model(
    feature_df: pd.DataFrame,
    *,
    target_column: str = "label_up_2c",
    feature_version: str = WNBA_FEATURE_VERSION,
    min_rows: int = 5000,
    min_distinct_games: int = 40,
    learning_rate: float = 0.08,
    max_iter: int = 350,
    l2_penalty: float = 0.001,
    trained_at: datetime | None = None,
) -> dict[str, Any]:
    """Train a small transparent baseline for WNBA short-horizon repricing.

    This intentionally returns a blocked status until labeled WNBA CLOB windows exist.
    It is a structural ML product, not a calibrated live model.
    """
    trained_at = trained_at or datetime.now(timezone.utc)
    prepared = _prepare_training_frame(feature_df, target_column=target_column)
    blockers: list[str] = []
    if prepared.empty:
        blockers.append("missing_labeled_wnba_clob_price_windows")
    if len(prepared) < min_rows:
        blockers.append("insufficient_labeled_rows_for_wnba_ml")
    distinct_games = int(prepared["game_id"].nunique()) if not prepared.empty else 0
    if distinct_games < min_distinct_games:
        blockers.append("insufficient_distinct_wnba_ml_games")
    if not prepared.empty and int(prepared[target_column].nunique()) < 2:
        blockers.append("single_class_wnba_ml_target")
    if blockers:
        return {
            "status": "blocked",
            "feature_version": feature_version,
            "target_column": target_column,
            "trained_at": trained_at,
            "training_rows": int(len(prepared)),
            "validation_rows": 0,
            "distinct_games": distinct_games,
            "blockers": blockers,
            "feature_columns": list(WNBA_SHORT_HORIZON_FEATURE_COLUMNS),
            "model_json": None,
            "metrics_json": {},
        }

    game_ids = sorted(str(value) for value in prepared["game_id"].dropna().unique())
    validation_count = max(1, int(round(len(game_ids) * 0.2))) if len(game_ids) > 1 else 0
    validation_games = set(game_ids[-validation_count:]) if validation_count else set()
    train_df = prepared[~prepared["game_id"].astype(str).isin(validation_games)].copy()
    validation_df = prepared[prepared["game_id"].astype(str).isin(validation_games)].copy()
    if train_df.empty or validation_df.empty:
        train_df = prepared.copy()
        validation_df = prepared.copy()

    x_train_raw = train_df[list(WNBA_SHORT_HORIZON_FEATURE_COLUMNS)].astype(float).to_numpy()
    y_train = train_df[target_column].astype(bool).astype(float).to_numpy()
    x_val_raw = validation_df[list(WNBA_SHORT_HORIZON_FEATURE_COLUMNS)].astype(float).to_numpy()
    y_val = validation_df[target_column].astype(bool).astype(float).to_numpy()

    means = x_train_raw.mean(axis=0)
    stds = x_train_raw.std(axis=0)
    stds = np.where(stds < 1e-9, 1.0, stds)
    x_train = (x_train_raw - means) / stds
    x_val = (x_val_raw - means) / stds
    x_train = np.column_stack([np.ones(len(x_train)), x_train])
    x_val = np.column_stack([np.ones(len(x_val)), x_val])

    weights = np.zeros(x_train.shape[1], dtype=float)
    for _ in range(max(1, int(max_iter))):
        probs = _sigmoid(x_train @ weights)
        gradient = (x_train.T @ (probs - y_train)) / len(y_train)
        gradient[1:] += l2_penalty * weights[1:]
        weights -= learning_rate * gradient

    train_prob = _sigmoid(x_train @ weights)
    validation_prob = _sigmoid(x_val @ weights)
    metrics = {
        "train_log_loss": _log_loss(y_train, train_prob),
        "validation_log_loss": _log_loss(y_val, validation_prob),
        "train_accuracy": float(((train_prob >= 0.5) == y_train.astype(bool)).mean()),
        "validation_accuracy": float(((validation_prob >= 0.5) == y_val.astype(bool)).mean()),
        "train_positive_rate": float(y_train.mean()),
        "validation_positive_rate": float(y_val.mean()),
    }
    coefficients = {
        column: float(value)
        for column, value in zip(WNBA_SHORT_HORIZON_FEATURE_COLUMNS, weights[1:], strict=True)
    }
    return {
        "status": "trained_baseline",
        "feature_version": feature_version,
        "target_column": target_column,
        "trained_at": trained_at,
        "training_rows": int(len(train_df)),
        "validation_rows": int(len(validation_df)),
        "distinct_games": distinct_games,
        "blockers": [],
        "feature_columns": list(WNBA_SHORT_HORIZON_FEATURE_COLUMNS),
        "model_json": {
            "model_type": "logistic_regression_gradient_descent",
            "intercept": float(weights[0]),
            "coefficients": coefficients,
            "feature_means": {
                column: float(value)
                for column, value in zip(WNBA_SHORT_HORIZON_FEATURE_COLUMNS, means, strict=True)
            },
            "feature_stds": {
                column: float(value)
                for column, value in zip(WNBA_SHORT_HORIZON_FEATURE_COLUMNS, stds, strict=True)
            },
            "max_iter": int(max_iter),
            "learning_rate": float(learning_rate),
            "l2_penalty": float(l2_penalty),
        },
        "metrics_json": metrics,
    }


__all__ = [
    "WNBA_SHORT_HORIZON_FEATURE_COLUMNS",
    "train_wnba_short_horizon_reprice_model",
]
