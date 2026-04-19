from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd

from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION


@dataclass(slots=True)
class ModelInputFrames:
    profiles_df: pd.DataFrame
    state_df: pd.DataFrame


def _query_df(connection: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _safe_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = _safe_datetime(value)
    if parsed is not None:
        return parsed.date()
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_model_input_frames(
    connection: Any,
    *,
    season: str,
    season_phase: str,
    analysis_version: str = ANALYSIS_VERSION,
) -> ModelInputFrames:
    profiles_df = _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_game_team_profiles
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC;
        """,
        (season, season_phase, analysis_version),
    )
    if not profiles_df.empty:
        for column in (
            "computed_at",
            "game_start_time",
            "first_inversion_at",
            "winner_stable_70_at",
            "winner_stable_80_at",
            "winner_stable_90_at",
            "winner_stable_95_at",
        ):
            if column in profiles_df.columns:
                profiles_df[column] = pd.to_datetime(profiles_df[column], errors="coerce", utc=True)
        if "game_date" in profiles_df.columns:
            profiles_df["game_date"] = pd.to_datetime(profiles_df["game_date"], errors="coerce").dt.date
        numeric_columns = [
            "opening_price",
            "closing_price",
            "pregame_price_min",
            "pregame_price_max",
            "pregame_price_range",
            "ingame_price_min",
            "ingame_price_max",
            "ingame_price_range",
            "total_price_min",
            "total_price_max",
            "total_swing",
            "max_favorable_excursion",
            "max_adverse_excursion",
            "inversion_count",
            "seconds_above_50c",
            "seconds_below_50c",
            "winner_stable_70_clock_elapsed_seconds",
            "winner_stable_80_clock_elapsed_seconds",
            "winner_stable_90_clock_elapsed_seconds",
            "winner_stable_95_clock_elapsed_seconds",
        ]
        for column in numeric_columns:
            if column in profiles_df.columns:
                profiles_df[column] = pd.to_numeric(profiles_df[column], errors="coerce")

    state_df = _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
        """,
        (season, season_phase, analysis_version),
    )
    if not state_df.empty:
        for column in ("computed_at", "event_at"):
            if column in state_df.columns:
                state_df[column] = pd.to_datetime(state_df[column], errors="coerce", utc=True)
        if "game_date" in state_df.columns:
            state_df["game_date"] = pd.to_datetime(state_df["game_date"], errors="coerce").dt.date
        numeric_columns = [
            "state_index",
            "event_index",
            "period",
            "clock_elapsed_seconds",
            "seconds_to_game_end",
            "score_for",
            "score_against",
            "score_diff",
            "points_scored",
            "delta_for",
            "delta_against",
            "lead_changes_so_far",
            "team_points_last_5_events",
            "opponent_points_last_5_events",
            "net_points_last_5_events",
            "opening_price",
            "team_price",
            "price_delta_from_open",
            "abs_price_delta_from_open",
            "gap_before_seconds",
            "gap_after_seconds",
            "mfe_from_state",
            "mae_from_state",
        ]
        for column in numeric_columns:
            if column in state_df.columns:
                state_df[column] = pd.to_numeric(state_df[column], errors="coerce")

    return ModelInputFrames(profiles_df=profiles_df, state_df=state_df)


def resolve_train_cutoff(game_dates: pd.Series | None, *, requested_cutoff: str | None = None) -> pd.Timestamp | None:
    if game_dates is None:
        return None
    clean_dates = pd.Series(pd.to_datetime(game_dates, errors="coerce")).dropna().sort_values().unique()
    if len(clean_dates) == 0:
        return None
    if requested_cutoff:
        parsed = pd.to_datetime(requested_cutoff, errors="coerce")
        if pd.notna(parsed):
            return pd.Timestamp(parsed)
    cutoff_index = max(0, int(len(clean_dates) * 0.75) - 1)
    return pd.Timestamp(clean_dates[cutoff_index])


def split_frame_by_cutoff(frame: pd.DataFrame, *, cutoff: pd.Timestamp | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return frame.copy(), frame.copy()
    if "game_date" not in frame.columns:
        return frame.copy(), frame.iloc[0:0].copy()
    if cutoff is None:
        train_mask = pd.Series(True, index=frame.index)
    else:
        game_dates = pd.to_datetime(frame["game_date"], errors="coerce")
        train_mask = game_dates.le(cutoff).fillna(False)
    train_df = frame.loc[train_mask].copy()
    validation_df = frame.loc[~train_mask].copy()
    return train_df, validation_df


def prepare_state_model_frame(state_df: pd.DataFrame) -> pd.DataFrame:
    if state_df.empty:
        return state_df.copy()
    work = state_df.copy()
    work["abs_score_diff"] = work["score_diff"].abs()
    work["market_favorite_flag"] = work["market_favorite_flag"].fillna(False).astype(int)
    work["team_led_flag"] = work["team_led_flag"].fillna(False).astype(int)
    work["scoreboard_control_mismatch_flag"] = work["scoreboard_control_mismatch_flag"].fillna(False).astype(int)
    work["period"] = pd.to_numeric(work["period"], errors="coerce")
    work["seconds_to_game_end"] = pd.to_numeric(work["seconds_to_game_end"], errors="coerce")
    work["team_price"] = pd.to_numeric(work["team_price"], errors="coerce")
    work["opening_price"] = pd.to_numeric(work["opening_price"], errors="coerce")
    work["abs_price_delta_from_open"] = pd.to_numeric(work["abs_price_delta_from_open"], errors="coerce")
    work["net_points_last_5_events"] = pd.to_numeric(work["net_points_last_5_events"], errors="coerce")
    work["game_date"] = pd.to_datetime(work["game_date"], errors="coerce")
    return work


def normalise_numeric_matrix(frame: pd.DataFrame, feature_columns: list[str]) -> tuple[np.ndarray, dict[str, list[float]]]:
    work = frame.copy()
    means: list[float] = []
    stds: list[float] = []
    for column in feature_columns:
        mean_value = float(work[column].mean())
        std_value = float(work[column].std(ddof=0))
        if std_value <= 0:
            std_value = 1.0
        work[column] = (work[column] - mean_value) / std_value
        means.append(mean_value)
        stds.append(std_value)
    x = work[feature_columns].to_numpy(dtype=float)
    x = np.concatenate([np.ones((x.shape[0], 1), dtype=float), x], axis=1)
    return x, {"means": means, "stds": stds}


def apply_numeric_normalisation(frame: pd.DataFrame, feature_columns: list[str], meta: dict[str, list[float]]) -> np.ndarray:
    work = frame.copy()
    means = meta.get("means") or []
    stds = meta.get("stds") or []
    for index, column in enumerate(feature_columns):
        mean_value = means[index] if index < len(means) else 0.0
        std_value = stds[index] if index < len(stds) and stds[index] > 0 else 1.0
        work[column] = (work[column] - mean_value) / std_value
    x = work[feature_columns].to_numpy(dtype=float)
    return np.concatenate([np.ones((x.shape[0], 1), dtype=float), x], axis=1)


def fit_logistic_regression(
    x_train: np.ndarray,
    y_train: np.ndarray,
    *,
    max_iter: int = 500,
    learning_rate: float = 0.05,
    l2_penalty: float = 1e-4,
) -> np.ndarray:
    coefficients = np.zeros(x_train.shape[1], dtype=float)
    for _ in range(max_iter):
        predictions = sigmoid(x_train @ coefficients)
        gradient = ((x_train.T @ (predictions - y_train)) / max(len(y_train), 1)) + (l2_penalty * coefficients)
        coefficients -= learning_rate * gradient
    return coefficients


def fit_ols(x_train: np.ndarray, y_train: np.ndarray, *, ridge: float = 1e-6) -> np.ndarray:
    lhs = (x_train.T @ x_train) + (ridge * np.eye(x_train.shape[1]))
    rhs = x_train.T @ y_train
    return np.linalg.solve(lhs, rhs)


def sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def brier_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean((y_true - y_pred) ** 2))


def log_loss(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    clipped = np.clip(y_pred, 1e-6, 1 - 1e-6)
    return float(-np.mean((y_true * np.log(clipped)) + ((1 - y_true) * np.log(1 - clipped))))


def auc_score(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
    positives = y_score[y_true == 1]
    negatives = y_score[y_true == 0]
    if len(positives) == 0 or len(negatives) == 0:
        return None
    wins = 0.0
    total = 0.0
    for positive in positives:
        wins += float(np.sum(positive > negatives))
        wins += 0.5 * float(np.sum(positive == negatives))
        total += float(len(negatives))
    return wins / max(total, 1.0)


def spearman_corr(y_true: Sequence[float], y_pred: Sequence[float]) -> float | None:
    if len(y_true) < 2 or len(y_pred) < 2:
        return None
    frame = pd.DataFrame({"y_true": list(y_true), "y_pred": list(y_pred)}).dropna()
    if len(frame) < 2:
        return None
    return float(frame["y_true"].rank().corr(frame["y_pred"].rank(), method="pearson"))


def naive_classification_comparison(y_true: np.ndarray, y_pred: np.ndarray, *, base_rate: float) -> dict[str, Any]:
    naive_scores = np.full(len(y_true), base_rate, dtype=float)
    return {
        "primary_metric": "brier",
        "model_value": brier_score(y_true, y_pred),
        "naive_value": brier_score(y_true, naive_scores),
        "delta": brier_score(y_true, naive_scores) - brier_score(y_true, y_pred),
        "better_than_naive": brier_score(y_true, y_pred) <= brier_score(y_true, naive_scores),
        "naive_base_rate": float(base_rate),
    }


def naive_regression_comparison(y_true: np.ndarray, y_pred: np.ndarray, *, train_mean: float) -> dict[str, Any]:
    naive = np.full(len(y_true), train_mean, dtype=float)
    return {
        "primary_metric": "rmse",
        "model_value": float(np.sqrt(np.mean((y_pred - y_true) ** 2))),
        "naive_value": float(np.sqrt(np.mean((naive - y_true) ** 2))),
        "delta": float(np.sqrt(np.mean((naive - y_true) ** 2))) - float(np.sqrt(np.mean((y_pred - y_true) ** 2))),
        "better_than_naive": float(np.sqrt(np.mean((y_pred - y_true) ** 2))) <= float(np.sqrt(np.mean((naive - y_true) ** 2))),
        "naive_mean": float(train_mean),
    }


__all__ = [
    "ModelInputFrames",
    "apply_numeric_normalisation",
    "auc_score",
    "brier_score",
    "fit_logistic_regression",
    "fit_ols",
    "load_model_input_frames",
    "log_loss",
    "naive_classification_comparison",
    "naive_regression_comparison",
    "normalise_numeric_matrix",
    "prepare_state_model_frame",
    "resolve_train_cutoff",
    "sigmoid",
    "split_frame_by_cutoff",
    "spearman_corr",
]
