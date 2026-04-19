from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from psycopg2.extras import Json, execute_values

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import (
    ensure_output_dir as _ensure_output_dir,
    write_frame as _write_frame,
    write_json as _write_json,
    write_markdown as _write_markdown,
)
from app.data.pipelines.daily.nba.analysis.mart_aggregates import (
    build_opening_band_profile_rows as _build_opening_band_profile_rows,
    build_team_season_profile_rows as _build_team_season_profile_rows,
    load_game_profiles_df as _load_game_profiles_df,
)
from app.data.pipelines.daily.nba.analysis.cli import (
    build_parser as _analysis_build_parser,
    run_cli as _analysis_run_cli,
)
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_REVERSION_DRAWDOWN,
    DEFAULT_REVERSION_EXIT_BUFFER,
    DEFAULT_REVERSION_OPEN_THRESHOLD,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    DEFAULT_WINNER_DEFINITION_BREAK,
    DEFAULT_WINNER_DEFINITION_ENTRY,
    DESCRIPTIVE_ONLY_STATUSES,
    GAME_PROFILE_COLUMNS,
    OPENING_BAND_PROFILE_COLUMNS,
    OVERTIME_PERIOD_SECONDS,
    REGULATION_PERIOD_SECONDS,
    RESEARCH_READY_STATUSES,
    STATE_PANEL_COLUMNS,
    TEAM_SEASON_PROFILE_COLUMNS,
    WINNER_DEFINITION_PROFILE_COLUMNS,
    AnalysisMartBuildRequest,
    AnalysisUniverseRequest,
    BacktestRunRequest,
    ModelRunRequest,
)
from app.data.pipelines.daily.nba.analysis.mart_game_profiles import (
    derive_game_rows as _derive_game_rows,
    load_analysis_bundle as _load_analysis_bundle,
    opening_band_for_price as _opening_band_for_price,
)
from app.data.pipelines.daily.nba.analysis.mart_state_panel import (
    build_state_rows_for_side as _build_state_rows_for_side,
    build_winner_definition_profile_rows as _build_winner_definition_profile_rows,
)
from app.data.pipelines.daily.nba.analysis.reports import (
    build_analysis_report as _build_analysis_report_impl,
)
from app.data.pipelines.daily.nba.analysis.universe import (
    build_analysis_universe_qa_summary as _build_analysis_universe_qa_summary,
    load_analysis_universe as _load_analysis_universe_bundle,
)


def _query_df(connection: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


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


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"true", "1", "yes", "y"}:
        return True
    if raw in {"false", "0", "no", "n"}:
        return False
    return None


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


def _json_value(value: Any) -> Json | None:
    if value is None:
        return None
    if isinstance(value, Json):
        return value
    if isinstance(value, (dict, list)):
        return Json(to_jsonable(value))
    return value


def _estimate_midpoint(min_value: float | None, max_value: float | None) -> float | None:
    if min_value is not None and max_value is not None:
        return (min_value + max_value) / 2.0
    return min_value if min_value is not None else max_value


def _value_range(min_value: float | None, max_value: float | None) -> float | None:
    if min_value is None or max_value is None:
        return None
    return max_value - min_value


def _mean_or_none(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None and not pd.isna(value)]
    if not clean:
        return None
    return float(sum(clean) / len(clean))


def _linear_slope(values: Sequence[float | None]) -> float | None:
    clean = [(index, float(value)) for index, value in enumerate(values) if value is not None]
    if len(clean) < 2:
        return None
    x = np.array([item[0] for item in clean], dtype=float)
    y = np.array([item[1] for item in clean], dtype=float)
    x_mean = float(x.mean())
    denom = float(((x - x_mean) ** 2).sum())
    if denom == 0.0:
        return 0.0
    return float(((x - x_mean) * (y - float(y.mean()))).sum() / denom)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_logistic_regression(
    x_train: np.ndarray,
    y_train: np.ndarray,
    *,
    max_iter: int = 500,
    learning_rate: float = 0.05,
    l2_penalty: float = 1e-4,
) -> np.ndarray:
    coefficients = np.zeros(x_train.shape[1], dtype=float)
    for _ in range(max_iter):
        predictions = _sigmoid(x_train @ coefficients)
        gradient = ((x_train.T @ (predictions - y_train)) / max(len(y_train), 1)) + (l2_penalty * coefficients)
        coefficients -= learning_rate * gradient
    return coefficients


def _fit_ols(x_train: np.ndarray, y_train: np.ndarray, *, ridge: float = 1e-6) -> np.ndarray:
    lhs = (x_train.T @ x_train) + (ridge * np.eye(x_train.shape[1]))
    rhs = x_train.T @ y_train
    return np.linalg.solve(lhs, rhs)


def _brier_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean((y_true - y_pred) ** 2))


def _log_loss(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    clipped = np.clip(y_pred, 1e-6, 1 - 1e-6)
    return float(-np.mean((y_true * np.log(clipped)) + ((1 - y_true) * np.log(1 - clipped))))


def _auc_score(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
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


def _spearman_corr(y_true: Sequence[float], y_pred: Sequence[float]) -> float | None:
    if len(y_true) < 2 or len(y_pred) < 2:
        return None
    frame = pd.DataFrame({"y_true": list(y_true), "y_pred": list(y_pred)}).dropna()
    if len(frame) < 2:
        return None
    return float(frame["y_true"].rank().corr(frame["y_pred"].rank(), method="pearson"))


def _normalise_numeric_matrix(frame: pd.DataFrame, feature_columns: list[str]) -> tuple[np.ndarray, dict[str, list[float]]]:
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


def _apply_numeric_normalisation(frame: pd.DataFrame, feature_columns: list[str], meta: dict[str, list[float]]) -> np.ndarray:
    work = frame.copy()
    means = meta.get("means") or []
    stds = meta.get("stds") or []
    for index, column in enumerate(feature_columns):
        mean_value = means[index] if index < len(means) else 0.0
        std_value = stds[index] if index < len(stds) and stds[index] > 0 else 1.0
        work[column] = (work[column] - mean_value) / std_value
    x = work[feature_columns].to_numpy(dtype=float)
    return np.concatenate([np.ones((x.shape[0], 1), dtype=float), x], axis=1)


def _format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.1f}%"


def _format_num(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:.{digits}f}"


def _price_band_label(price: float | None) -> str | None:
    label, _ = _opening_band_for_price(price)
    return label


def _score_diff_bucket(score_diff: int | None) -> str:
    if score_diff is None:
        return "unknown"
    if score_diff <= -15:
        return "trail_15_plus"
    if score_diff <= -10:
        return "trail_10_14"
    if score_diff <= -5:
        return "trail_5_9"
    if score_diff <= -1:
        return "trail_1_4"
    if score_diff == 0:
        return "tied"
    if score_diff <= 4:
        return "lead_1_4"
    if score_diff <= 9:
        return "lead_5_9"
    if score_diff <= 14:
        return "lead_10_14"
    return "lead_15_plus"


def _period_duration_seconds(period: int | None) -> int:
    if period is None or period <= 4:
        return REGULATION_PERIOD_SECONDS
    return OVERTIME_PERIOD_SECONDS


def _total_game_duration_seconds(max_period: int | None) -> float | None:
    if max_period is None:
        return None
    total = 0.0
    for period in range(1, max_period + 1):
        total += float(_period_duration_seconds(period))
    return total


def _prepare_sql_rows(columns: Sequence[str], rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    prepared: list[tuple[Any, ...]] = []
    for row in rows:
        prepared.append(tuple(_json_value(row.get(column)) for column in columns))
    return prepared


def _delete_game_rows(connection: Any, *, game_id: str, analysis_version: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM nba.nba_analysis_state_panel WHERE game_id = %s AND analysis_version = %s;",
            (game_id, analysis_version),
        )
        cursor.execute(
            "DELETE FROM nba.nba_analysis_game_team_profiles WHERE game_id = %s AND analysis_version = %s;",
            (game_id, analysis_version),
        )


def _delete_season_rows(
    connection: Any,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_state_panel
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_game_team_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_team_season_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_opening_band_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_winner_definition_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )


def _delete_aggregate_rows(
    connection: Any,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_team_season_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_opening_band_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        cursor.execute(
            """
            DELETE FROM nba.nba_analysis_winner_definition_profiles
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )


def _bulk_insert_rows(connection: Any, *, table: str, columns: Sequence[str], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s;"
    values = _prepare_sql_rows(columns, rows)
    with connection.cursor() as cursor:
        execute_values(cursor, sql, values, page_size=200)
    return len(rows)


def load_analysis_universe(connection: Any, request: AnalysisUniverseRequest) -> pd.DataFrame:
    universe = _load_analysis_universe_bundle(connection, request)
    return universe.selected_frame(request.coverage_filter)


def _build_completeness_report(universe_df: pd.DataFrame) -> dict[str, Any]:
    return _build_analysis_universe_qa_summary(universe_df)


def _load_state_panel_df(connection: Any, *, season: str, season_phase: str, analysis_version: str) -> pd.DataFrame:
    frame = _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
        """,
        (season, season_phase, analysis_version),
    )
    if frame.empty:
        return frame
    for column in ("computed_at", "event_at"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    if "game_date" in frame.columns:
        frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce").dt.date
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
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def build_analysis_mart(request: AnalysisMartBuildRequest) -> dict[str, Any]:
    universe_request = AnalysisUniverseRequest(
        season=request.season,
        season_phase=request.season_phase,
        coverage_filter="all",
        analysis_version=request.analysis_version,
    )
    computed_at = datetime.now(timezone.utc)
    output_dir = _ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version)

    with managed_connection() as connection:
        universe_bundle = _load_analysis_universe_bundle(connection, universe_request)
        universe_df = universe_bundle.full_universe.copy()
        if request.game_ids:
            universe_df = universe_df[universe_df["game_id"].astype(str).isin([str(game_id) for game_id in request.game_ids])].reset_index(drop=True)
        completeness_report = _build_analysis_universe_qa_summary(universe_df)
        if request.rebuild:
            _delete_season_rows(
                connection,
                season=request.season,
                season_phase=request.season_phase,
                analysis_version=request.analysis_version,
            )
            connection.commit()

        game_profiles_written = 0
        state_rows_written = 0
        game_qas: list[dict[str, Any]] = []
        for _, universe_row in universe_df.iterrows():
            bundle = _load_analysis_bundle(connection, game_id=str(universe_row["game_id"]))
            if bundle is None:
                continue
            game_rows, state_rows, qa = _derive_game_rows(
                universe_row=universe_row,
                bundle=bundle,
                analysis_version=request.analysis_version,
                computed_at=computed_at,
                build_state_rows_for_side=_build_state_rows_for_side,
            )
            universe_classification = str(universe_row.get("classification") or "excluded")
            universe_reason = str(universe_row.get("classification_reason") or universe_classification)
            for game_row in game_rows:
                game_row["notes_json"] = {
                    **(game_row.get("notes_json") or {}),
                    "universe_classification": universe_classification,
                    "universe_classification_reason": universe_reason,
                }
            _delete_game_rows(connection, game_id=str(universe_row["game_id"]), analysis_version=request.analysis_version)
            game_profiles_written += _bulk_insert_rows(
                connection,
                table="nba.nba_analysis_game_team_profiles",
                columns=GAME_PROFILE_COLUMNS,
                rows=game_rows,
            )
            state_rows_written += _bulk_insert_rows(
                connection,
                table="nba.nba_analysis_state_panel",
                columns=STATE_PANEL_COLUMNS,
                rows=state_rows,
            )
            game_qas.append(
                {
                    **qa,
                    "classification": universe_classification,
                    "classification_reason": universe_reason,
                    "canonical_research_ready_flag": universe_classification == "research_ready",
                    "descriptive_only_flag": universe_classification == "descriptive_only",
                    "excluded_flag": universe_classification == "excluded",
                }
            )
        connection.commit()

        profiles_df = _load_game_profiles_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
        state_df = _load_state_panel_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
        _delete_aggregate_rows(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
        team_profile_rows = _build_team_season_profile_rows(
            profiles_df,
            state_df,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
            computed_at=computed_at,
        )
        opening_band_rows = _build_opening_band_profile_rows(
            profiles_df,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
            computed_at=computed_at,
        )
        winner_definition_rows = _build_winner_definition_profile_rows(
            state_df,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
            computed_at=computed_at,
        )
        team_profiles_written = _bulk_insert_rows(
            connection,
            table="nba.nba_analysis_team_season_profiles",
            columns=TEAM_SEASON_PROFILE_COLUMNS,
            rows=team_profile_rows,
        )
        opening_band_profiles_written = _bulk_insert_rows(
            connection,
            table="nba.nba_analysis_opening_band_profiles",
            columns=OPENING_BAND_PROFILE_COLUMNS,
            rows=opening_band_rows,
        )
        winner_definition_profiles_written = _bulk_insert_rows(
            connection,
            table="nba.nba_analysis_winner_definition_profiles",
            columns=WINNER_DEFINITION_PROFILE_COLUMNS,
            rows=winner_definition_rows,
        )
        connection.commit()

    artifacts = {
        "universe_json": _write_json(output_dir / "analysis_universe.json", completeness_report),
        "game_qa_json": _write_json(output_dir / "analysis_game_qa.json", game_qas),
    }
    artifacts.update({f"game_profiles_{key}": value for key, value in _write_frame(output_dir / "nba_analysis_game_team_profiles", profiles_df).items()})
    artifacts.update({f"state_panel_{key}": value for key, value in _write_frame(output_dir / "nba_analysis_state_panel", state_df).items()})
    artifacts.update(
        {
            f"team_profiles_{key}": value
            for key, value in _write_frame(output_dir / "nba_analysis_team_season_profiles", pd.DataFrame(team_profile_rows)).items()
        }
    )
    artifacts.update(
        {
            f"opening_band_profiles_{key}": value
            for key, value in _write_frame(output_dir / "nba_analysis_opening_band_profiles", pd.DataFrame(opening_band_rows)).items()
        }
    )
    artifacts.update(
        {
            f"winner_definition_profiles_{key}": value
            for key, value in _write_frame(output_dir / "nba_analysis_winner_definition_profiles", pd.DataFrame(winner_definition_rows)).items()
        }
    )

    summary = {
        "season": request.season,
        "season_phase": request.season_phase,
        "analysis_version": request.analysis_version,
        "computed_at": computed_at,
        "games_considered": int(len(universe_df)),
        "research_ready_games": int(completeness_report.get("research_ready_games", 0)),
        "descriptive_only_games": int(completeness_report.get("descriptive_only_games", 0)),
        "excluded_games": int(completeness_report.get("excluded_games", 0)),
        "game_team_profiles_written": game_profiles_written,
        "state_rows_written": state_rows_written,
        "team_season_profiles_written": team_profiles_written,
        "opening_band_profiles_written": opening_band_profiles_written,
        "winner_definition_profiles_written": winner_definition_profiles_written,
        "classification_counts": completeness_report.get("classification_counts", {}),
        "coverage_status_counts": completeness_report.get("coverage_status_counts", {}),
        "undercovered_teams": completeness_report.get("undercovered_teams", []),
        "undercovered_dates": completeness_report.get("undercovered_dates", []),
        "artifacts": artifacts,
    }
    _write_json(output_dir / "build_analysis_mart_summary.json", summary)
    return to_jsonable(summary)


def _build_descriptive_report_payload(
    profiles_df: pd.DataFrame,
    state_df: pd.DataFrame,
    team_profiles_df: pd.DataFrame,
    opening_band_df: pd.DataFrame,
    winner_definition_df: pd.DataFrame,
    completeness_report: dict[str, Any],
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
) -> dict[str, Any]:
    if profiles_df.empty:
        return {
            "season": season,
            "season_phase": season_phase,
            "analysis_version": analysis_version,
            "error": "analysis_mart_empty",
        }
    expectation_gap_rows: list[dict[str, Any]] = []
    for team_slug, group in profiles_df.groupby("team_slug"):
        gaps = [
            abs((1.0 if bool(final_winner) else 0.0) - float(opening_price))
            for final_winner, opening_price in zip(group["final_winner_flag"].fillna(False), group["opening_price"])
            if opening_price is not None and not pd.isna(opening_price)
        ]
        expectation_gap_rows.append(
            {
                "team_slug": team_slug,
                "sample_games": int(len(group)),
                "avg_expectation_gap_abs": _mean_or_none(gaps),
            }
        )
    expectation_gap_rows.sort(key=lambda row: (row["avg_expectation_gap_abs"] is None, -(row["avg_expectation_gap_abs"] or 0.0)))

    stable_favorite_rows = []
    favorite_profiles = profiles_df[profiles_df["opening_price"].fillna(-1.0) >= 0.6].copy()
    for team_slug, group in favorite_profiles.groupby("team_slug"):
        stable_favorite_rows.append(
            {
                "team_slug": team_slug,
                "sample_games": int(len(group)),
                "avg_total_swing": _mean_or_none(group["total_swing"].tolist()),
                "avg_inversion_count": _mean_or_none(group["inversion_count"].tolist()),
            }
        )
    stable_favorite_rows.sort(
        key=lambda row: (
            row["avg_total_swing"] is None,
            row["avg_total_swing"] if row["avg_total_swing"] is not None else math.inf,
            row["avg_inversion_count"] if row["avg_inversion_count"] is not None else math.inf,
        )
    )

    reversion_contexts = []
    if not state_df.empty:
        context_rollup = (
            state_df.groupby("context_bucket")
            .agg(
                sample_states=("game_id", "count"),
                large_swing_rate=("large_swing_next_12_states_flag", "mean"),
                inversion_rate=("crossed_50c_next_12_states_flag", "mean"),
            )
            .reset_index()
        )
        context_rollup = context_rollup.sort_values(["large_swing_rate", "sample_states"], ascending=[False, False])
        reversion_contexts = context_rollup.head(12).to_dict(orient="records")

    threshold_summary = []
    if not winner_definition_df.empty:
        threshold_rollup = (
            winner_definition_df.groupby("threshold_cents")
            .agg(
                sample_states=("sample_states", "sum"),
                stable_rate=("stable_states", "sum"),
                avg_reopen_rate=("reopen_rate", "mean"),
            )
            .reset_index()
        )
        threshold_rollup["stable_rate"] = threshold_rollup["stable_rate"] / threshold_rollup["sample_states"].clip(lower=1)
        threshold_summary = threshold_rollup.sort_values("threshold_cents").to_dict(orient="records")

    return {
        "season": season,
        "season_phase": season_phase,
        "analysis_version": analysis_version,
        "universe": completeness_report,
        "teams_against_expectation": expectation_gap_rows[:10],
        "highest_volatility_teams": team_profiles_df.sort_values("avg_ingame_range", ascending=False).head(10)[["team_slug", "sample_games", "avg_ingame_range", "avg_total_swing"]].to_dict(orient="records") if not team_profiles_df.empty else [],
        "opening_bands_largest_swings": opening_band_df.sort_values("avg_total_swing", ascending=False).head(10).to_dict(orient="records") if not opening_band_df.empty else [],
        "most_inversions": team_profiles_df.sort_values("avg_inversion_count", ascending=False).head(10)[["team_slug", "sample_games", "avg_inversion_count", "inversion_rate"]].to_dict(orient="records") if not team_profiles_df.empty else [],
        "stable_favorites": stable_favorite_rows[:10],
        "favorite_drawdown_exposure": team_profiles_df.sort_values("avg_favorite_drawdown", ascending=False).head(10)[["team_slug", "sample_games", "avg_favorite_drawdown"]].to_dict(orient="records") if not team_profiles_df.empty else [],
        "control_vs_confidence_gap": team_profiles_df.sort_values("control_confidence_mismatch_rate", ascending=False).head(10)[["team_slug", "sample_games", "control_confidence_mismatch_rate"]].to_dict(orient="records") if not team_profiles_df.empty else [],
        "opening_expectation_drift": team_profiles_df.sort_values("opening_price_trend_slope", ascending=False).head(10)[["team_slug", "sample_games", "opening_price_trend_slope"]].to_dict(orient="records") if not team_profiles_df.empty else [],
        "high_reversion_contexts": reversion_contexts,
        "winner_definition_thresholds": threshold_summary,
    }


def _render_analysis_report_markdown(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"# NBA Analysis Report\n\nError: `{payload['error']}`\n"
    lines = [
        "# NBA Analysis Report",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        f"- Research-ready games: `{payload['universe'].get('research_ready_games', 0)}` / `{payload['universe'].get('games_total', 0)}`",
        "",
        "## Coverage",
        "",
        "| Status | Games |",
        "| --- | ---: |",
    ]
    for status, count in sorted((payload["universe"].get("coverage_status_counts") or {}).items()):
        lines.append(f"| {status} | {count} |")

    def _append_table(title: str, rows: list[dict[str, Any]], keys: list[str]) -> None:
        lines.extend(["", f"## {title}", ""])
        if not rows:
            lines.append("No rows.")
            return
        lines.append("| " + " | ".join(keys) + " |")
        lines.append("| " + " | ".join(["---"] * len(keys)) + " |")
        for row in rows:
            formatted = []
            for key in keys:
                value = row.get(key)
                if isinstance(value, float):
                    formatted.append(f"{value:.4f}")
                else:
                    formatted.append(str(value))
            lines.append("| " + " | ".join(formatted) + " |")

    _append_table(
        "Teams Against Opening Expectation",
        payload.get("teams_against_expectation", []),
        ["team_slug", "sample_games", "avg_expectation_gap_abs"],
    )
    _append_table(
        "Highest In-Game Volatility",
        payload.get("highest_volatility_teams", []),
        ["team_slug", "sample_games", "avg_ingame_range", "avg_total_swing"],
    )
    _append_table(
        "Opening Bands With Largest Swings",
        payload.get("opening_bands_largest_swings", []),
        ["opening_band", "sample_games", "avg_total_swing", "avg_inversion_count"],
    )
    _append_table(
        "Most Frequent Inversions",
        payload.get("most_inversions", []),
        ["team_slug", "sample_games", "avg_inversion_count", "inversion_rate"],
    )
    _append_table(
        "Reversion Contexts",
        payload.get("high_reversion_contexts", []),
        ["context_bucket", "sample_states", "large_swing_rate", "inversion_rate"],
    )
    _append_table(
        "Winner Definition Thresholds",
        payload.get("winner_definition_thresholds", []),
        ["threshold_cents", "sample_states", "stable_rate", "avg_reopen_rate"],
    )
    return "\n".join(lines) + "\n"


def build_analysis_report(
    *,
    season: str = DEFAULT_SEASON,
    season_phase: str = DEFAULT_SEASON_PHASE,
    analysis_version: str = ANALYSIS_VERSION,
    output_root: str | None = None,
) -> dict[str, Any]:
    return _build_analysis_report_impl(
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
        output_root=output_root,
    )


def _trade_row(
    group: pd.DataFrame,
    *,
    entry_index: int,
    exit_index: int,
    strategy_family: str,
    entry_rule: str,
    exit_rule: str,
    slippage_cents: int,
) -> dict[str, Any]:
    entry = group.iloc[entry_index]
    exit_row = group.iloc[exit_index]
    entry_price = float(entry["team_price"])
    exit_price = float(exit_row["team_price"])
    mfe_after_entry = float(group.iloc[entry_index:]["team_price"].max() - entry_price)
    mae_after_entry = float(entry_price - group.iloc[entry_index:]["team_price"].min())
    gross_return = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    slippage = slippage_cents / 100.0
    entry_exec = min(0.999999, entry_price + slippage)
    exit_exec = max(0.0, exit_price - slippage)
    net_return = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
    entry_at = pd.to_datetime(entry["event_at"], utc=True)
    exit_at = pd.to_datetime(exit_row["event_at"], utc=True)
    return {
        "season": entry["season"],
        "season_phase": entry["season_phase"],
        "analysis_version": entry["analysis_version"],
        "strategy_family": strategy_family,
        "entry_rule": entry_rule,
        "exit_rule": exit_rule,
        "game_id": entry["game_id"],
        "team_side": entry["team_side"],
        "team_slug": entry["team_slug"],
        "opponent_team_slug": entry["opponent_team_slug"],
        "opening_band": entry["opening_band"],
        "period_label": entry["period_label"],
        "score_diff_bucket": entry["score_diff_bucket"],
        "context_bucket": entry["context_bucket"],
        "entry_state_index": int(entry["state_index"]),
        "exit_state_index": int(exit_row["state_index"]),
        "entry_at": entry_at,
        "exit_at": exit_at,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return": gross_return,
        "gross_return_with_slippage": net_return,
        "max_favorable_excursion_after_entry": mfe_after_entry,
        "max_adverse_excursion_after_entry": mae_after_entry,
        "hold_time_seconds": max(0.0, (exit_at - entry_at).total_seconds()),
        "slippage_cents": slippage_cents,
    }


def _simulate_reversion_trades(state_df: pd.DataFrame, slippage_cents: int) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for (_, _), group in state_df.groupby(["game_id", "team_side"]):
        ordered = group.sort_values("state_index").reset_index(drop=True)
        opening_price = _safe_float(ordered.iloc[0]["opening_price"])
        if opening_price is None or opening_price < DEFAULT_REVERSION_OPEN_THRESHOLD:
            continue
        trigger = ordered[ordered["team_price"] <= opening_price - DEFAULT_REVERSION_DRAWDOWN]
        if trigger.empty:
            continue
        entry_index = int(trigger.index[0])
        target_exit_price = opening_price - DEFAULT_REVERSION_EXIT_BUFFER
        future = ordered.iloc[entry_index + 1 :].copy()
        exit_candidates = future[future["team_price"] >= target_exit_price]
        exit_index = int(exit_candidates.index[0]) if not exit_candidates.empty else int(len(ordered) - 1)
        trades.append(
            _trade_row(
                ordered,
                entry_index=entry_index,
                exit_index=exit_index,
                strategy_family="reversion",
                entry_rule="favorite_drawdown_buy_10c",
                exit_rule="reclaim_open_minus_2c_or_end",
                slippage_cents=slippage_cents,
            )
        )
    return trades


def _simulate_inversion_trades(state_df: pd.DataFrame, slippage_cents: int) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for (_, _), group in state_df.groupby(["game_id", "team_side"]):
        ordered = group.sort_values("state_index").reset_index(drop=True)
        opening_price = _safe_float(ordered.iloc[0]["opening_price"])
        if opening_price is None or opening_price >= 0.5:
            continue
        previous_price = opening_price
        entry_index = None
        for index, price in enumerate(ordered["team_price"].tolist()):
            if index == 0:
                previous_price = price
                continue
            if previous_price < 0.5 and price >= 0.5:
                entry_index = index
                break
            previous_price = price
        if entry_index is None:
            continue
        future = ordered.iloc[entry_index + 1 :].copy()
        exit_candidates = future[future["team_price"] < 0.5]
        exit_index = int(exit_candidates.index[0]) if not exit_candidates.empty else int(len(ordered) - 1)
        trades.append(
            _trade_row(
                ordered,
                entry_index=entry_index,
                exit_index=exit_index,
                strategy_family="inversion",
                entry_rule="first_cross_above_50c",
                exit_rule="break_back_below_50c_or_end",
                slippage_cents=slippage_cents,
            )
        )
    return trades


def _simulate_winner_definition_trades(state_df: pd.DataFrame, slippage_cents: int) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for (_, _), group in state_df.groupby(["game_id", "team_side"]):
        ordered = group.sort_values("state_index").reset_index(drop=True)
        trigger = ordered[ordered["team_price"] >= DEFAULT_WINNER_DEFINITION_ENTRY]
        if trigger.empty:
            continue
        entry_index = int(trigger.index[0])
        future = ordered.iloc[entry_index + 1 :].copy()
        exit_candidates = future[future["team_price"] < DEFAULT_WINNER_DEFINITION_BREAK]
        exit_index = int(exit_candidates.index[0]) if not exit_candidates.empty else int(len(ordered) - 1)
        trades.append(
            _trade_row(
                ordered,
                entry_index=entry_index,
                exit_index=exit_index,
                strategy_family="winner_definition",
                entry_rule="reach_80c",
                exit_rule="break_75c_or_end",
                slippage_cents=slippage_cents,
            )
        )
    return trades


def _summarize_trades(trades_df: pd.DataFrame) -> dict[str, Any]:
    if trades_df.empty:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_gross_return": None,
            "median_gross_return": None,
            "avg_gross_return_with_slippage": None,
            "avg_hold_time_seconds": None,
        }
    return {
        "trade_count": int(len(trades_df)),
        "win_rate": float((trades_df["gross_return"] > 0).mean()),
        "avg_gross_return": float(trades_df["gross_return"].mean()),
        "median_gross_return": float(trades_df["gross_return"].median()),
        "avg_gross_return_with_slippage": float(trades_df["gross_return_with_slippage"].mean()),
        "avg_hold_time_seconds": float(trades_df["hold_time_seconds"].mean()),
        "avg_mfe_after_entry": float(trades_df["max_favorable_excursion_after_entry"].mean()),
        "avg_mae_after_entry": float(trades_df["max_adverse_excursion_after_entry"].mean()),
    }


def _render_backtest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# NBA Analysis Backtests",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        "",
    ]
    for family, summary in (payload.get("families") or {}).items():
        lines.extend(
            [
                f"## {family}",
                "",
                f"- Trade count: `{summary.get('trade_count')}`",
                f"- Win rate: `{_format_pct(summary.get('win_rate'))}`",
                f"- Average gross return: `{_format_num(summary.get('avg_gross_return'))}`",
                f"- Average gross return with slippage: `{_format_num(summary.get('avg_gross_return_with_slippage'))}`",
                f"- Average hold time seconds: `{_format_num(summary.get('avg_hold_time_seconds'))}`",
                "",
            ]
        )
    return "\n".join(lines)


def run_analysis_backtests(request: BacktestRunRequest) -> dict[str, Any]:
    output_dir = _ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version) / "backtests"
    output_dir.mkdir(parents=True, exist_ok=True)
    with managed_connection() as connection:
        state_df = _load_state_panel_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
    if state_df.empty:
        payload = {
            "season": request.season,
            "season_phase": request.season_phase,
            "analysis_version": request.analysis_version,
            "families": {},
            "error": "state_panel_empty",
        }
        payload["artifacts"] = {"json": _write_json(output_dir / "run_analysis_backtests.json", payload)}
        return payload

    families_to_run = (
        [request.strategy_family]
        if request.strategy_family != "all"
        else ["reversion", "inversion", "winner_definition"]
    )
    family_summaries: dict[str, Any] = {}
    family_trades: dict[str, pd.DataFrame] = {}
    for family in families_to_run:
        if family == "reversion":
            trades = _simulate_reversion_trades(state_df, request.slippage_cents)
        elif family == "inversion":
            trades = _simulate_inversion_trades(state_df, request.slippage_cents)
        elif family == "winner_definition":
            trades = _simulate_winner_definition_trades(state_df, request.slippage_cents)
        else:
            continue
        trades_df = pd.DataFrame(trades)
        family_trades[family] = trades_df
        family_summaries[family] = _summarize_trades(trades_df)
    payload = {
        "season": request.season,
        "season_phase": request.season_phase,
        "analysis_version": request.analysis_version,
        "slippage_cents": request.slippage_cents,
        "families": family_summaries,
        "artifacts": {},
    }
    payload["artifacts"]["json"] = _write_json(output_dir / "run_analysis_backtests.json", payload)
    payload["artifacts"]["markdown"] = _write_markdown(output_dir / "run_analysis_backtests.md", _render_backtest_markdown(payload))
    for family, trades_df in family_trades.items():
        payload["artifacts"].update(
            {f"{family}_{key}": value for key, value in _write_frame(output_dir / f"{family}_trades", trades_df).items()}
        )
    return to_jsonable(payload)


def _prepare_state_model_frame(state_df: pd.DataFrame) -> pd.DataFrame:
    if state_df.empty:
        return state_df
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


def _resolve_train_cutoff(game_dates: pd.Series, requested_cutoff: str | None) -> pd.Timestamp | None:
    clean_dates = pd.Series(pd.to_datetime(game_dates, errors="coerce")).dropna().sort_values().unique()
    if len(clean_dates) == 0:
        return None
    if requested_cutoff:
        parsed = pd.to_datetime(requested_cutoff, errors="coerce")
        if pd.notna(parsed):
            return pd.Timestamp(parsed)
    cutoff_index = max(0, int(len(clean_dates) * 0.75) - 1)
    return pd.Timestamp(clean_dates[cutoff_index])


def _train_volatility_inversion_baseline(frame: pd.DataFrame, train_cutoff: pd.Timestamp | None) -> dict[str, Any]:
    feature_columns = [
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
    work = frame.dropna(subset=feature_columns + ["crossed_50c_next_12_states_flag", "game_date"]).copy()
    if work.empty or work["crossed_50c_next_12_states_flag"].nunique() < 2:
        return {"status": "insufficient_data"}
    train_mask = work["game_date"] <= train_cutoff if train_cutoff is not None else pd.Series(True, index=work.index)
    train_df = work[train_mask].copy()
    validation_df = work[~train_mask].copy()
    if train_df.empty or validation_df.empty or train_df["crossed_50c_next_12_states_flag"].nunique() < 2:
        return {"status": "insufficient_data"}
    x_train, normalisation = _normalise_numeric_matrix(train_df[feature_columns], feature_columns)
    y_train = train_df["crossed_50c_next_12_states_flag"].astype(int).to_numpy(dtype=float)
    x_validation = _apply_numeric_normalisation(validation_df[feature_columns], feature_columns, normalisation)
    y_validation = validation_df["crossed_50c_next_12_states_flag"].astype(int).to_numpy(dtype=float)
    coefficients = _fit_logistic_regression(x_train, y_train)
    validation_scores = _sigmoid(x_validation @ coefficients)
    naive_rate = float(y_train.mean())
    naive_scores = np.full(len(y_validation), naive_rate, dtype=float)
    return {
        "status": "success",
        "model_family": "logistic_regression_baseline",
        "target": "crossed_50c_next_12_states_flag",
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(validation_df)),
        "metrics": {
            "brier": _brier_score(y_validation, validation_scores),
            "log_loss": _log_loss(y_validation, validation_scores),
            "auc": _auc_score(y_validation, validation_scores),
            "naive_brier": _brier_score(y_validation, naive_scores),
            "naive_log_loss": _log_loss(y_validation, naive_scores),
        },
        "coefficients": [{"feature": "intercept", "value": float(coefficients[0])}]
        + [{"feature": feature, "value": float(coefficients[index + 1])} for index, feature in enumerate(feature_columns)],
    }


def _train_trade_window_quality_baseline(frame: pd.DataFrame, train_cutoff: pd.Timestamp | None) -> dict[str, Any]:
    feature_columns = [
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
    work = frame.dropna(subset=feature_columns + ["mfe_from_state", "mae_from_state", "game_date"]).copy()
    if len(work) < 20:
        return {"status": "insufficient_data"}
    train_mask = work["game_date"] <= train_cutoff if train_cutoff is not None else pd.Series(True, index=work.index)
    train_df = work[train_mask].copy()
    validation_df = work[~train_mask].copy()
    if train_df.empty or validation_df.empty:
        return {"status": "insufficient_data"}
    x_train, normalisation = _normalise_numeric_matrix(train_df[feature_columns], feature_columns)
    x_validation = _apply_numeric_normalisation(validation_df[feature_columns], feature_columns, normalisation)
    targets = {}
    for target in ("mfe_from_state", "mae_from_state"):
        y_train = train_df[target].to_numpy(dtype=float)
        y_validation = validation_df[target].to_numpy(dtype=float)
        coefficients = _fit_ols(x_train, y_train)
        predictions = x_validation @ coefficients
        naive_prediction = np.full(len(y_validation), float(np.mean(y_train)), dtype=float)
        targets[target] = {
            "rmse": float(np.sqrt(np.mean((predictions - y_validation) ** 2))),
            "mae": float(np.mean(np.abs(predictions - y_validation))),
            "rank_corr": _spearman_corr(y_validation.tolist(), predictions.tolist()),
            "naive_rmse": float(np.sqrt(np.mean((naive_prediction - y_validation) ** 2))),
            "naive_mae": float(np.mean(np.abs(naive_prediction - y_validation))),
            "coefficients": [{"feature": "intercept", "value": float(coefficients[0])}]
            + [{"feature": feature, "value": float(coefficients[index + 1])} for index, feature in enumerate(feature_columns)],
        }
    return {
        "status": "success",
        "model_family": "ols_regression_baseline",
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(validation_df)),
        "targets": targets,
    }


def _train_winner_definition_timing_baseline(
    frame: pd.DataFrame,
    profiles_df: pd.DataFrame,
    train_cutoff: pd.Timestamp | None,
) -> dict[str, Any]:
    if frame.empty or profiles_df.empty:
        return {"status": "insufficient_data"}
    winners = profiles_df[
        (profiles_df["final_winner_flag"] == True) & profiles_df["winner_stable_80_clock_elapsed_seconds"].notna()
    ][["game_id", "team_side", "winner_stable_80_clock_elapsed_seconds"]].copy()
    if winners.empty:
        return {"status": "insufficient_data"}
    work = frame.merge(winners, on=["game_id", "team_side"], how="inner")
    work = work.dropna(subset=["clock_elapsed_seconds", "game_date"])
    if work.empty:
        return {"status": "insufficient_data"}
    work["time_to_stable_seconds"] = (
        pd.to_numeric(work["winner_stable_80_clock_elapsed_seconds"], errors="coerce")
        - pd.to_numeric(work["clock_elapsed_seconds"], errors="coerce")
    ).clip(lower=0.0)
    work = work.dropna(subset=["time_to_stable_seconds"])
    if len(work) < 20:
        return {"status": "insufficient_data"}
    train_mask = work["game_date"] <= train_cutoff if train_cutoff is not None else pd.Series(True, index=work.index)
    train_df = work[train_mask].copy()
    validation_df = work[~train_mask].copy()
    if train_df.empty or validation_df.empty:
        return {"status": "insufficient_data"}
    group_columns = ["period_label", "score_diff_bucket", "opening_band"]
    hazard_table = (
        train_df.groupby(group_columns)
        .agg(
            sample_states=("game_id", "count"),
            avg_time_to_stable_seconds=("time_to_stable_seconds", "mean"),
        )
        .reset_index()
    )
    default_mean = float(train_df["time_to_stable_seconds"].mean())
    validation_scored = validation_df.merge(hazard_table, on=group_columns, how="left")
    validation_scored["predicted_time_to_stable_seconds"] = validation_scored["avg_time_to_stable_seconds"].fillna(default_mean)
    y_true = validation_scored["time_to_stable_seconds"].to_numpy(dtype=float)
    y_pred = validation_scored["predicted_time_to_stable_seconds"].to_numpy(dtype=float)
    naive = np.full(len(y_true), default_mean, dtype=float)
    return {
        "status": "success",
        "model_family": "grouped_hazard_proxy",
        "target": "time_to_stable_80_seconds",
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(validation_df)),
        "metrics": {
            "rmse": float(np.sqrt(np.mean((y_pred - y_true) ** 2))),
            "mae": float(np.mean(np.abs(y_pred - y_true))),
            "rank_corr": _spearman_corr(y_true.tolist(), y_pred.tolist()),
            "naive_rmse": float(np.sqrt(np.mean((naive - y_true) ** 2))),
            "naive_mae": float(np.mean(np.abs(naive - y_true))),
        },
        "hazard_like_table": hazard_table.sort_values(["sample_states", "avg_time_to_stable_seconds"], ascending=[False, True]).head(50).to_dict(orient="records"),
    }


def _render_model_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# NBA Analysis Baselines",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        f"- Train cutoff: `{payload.get('train_cutoff')}`",
        "",
    ]
    for track_name, track_payload in (payload.get("tracks") or {}).items():
        lines.extend([f"## {track_name}", ""])
        if track_payload.get("status") != "success":
            lines.append(f"- Status: `{track_payload.get('status', 'unknown')}`")
            lines.append("")
            continue
        lines.append(f"- Model family: `{track_payload.get('model_family')}`")
        lines.append(f"- Train rows: `{track_payload.get('train_rows')}`")
        lines.append(f"- Validation rows: `{track_payload.get('validation_rows')}`")
        metrics = track_payload.get("metrics") or {}
        if metrics:
            for key, value in metrics.items():
                lines.append(f"- {key}: `{_format_num(value)}`")
        if track_name == "trade_window_quality":
            for target, target_payload in (track_payload.get("targets") or {}).items():
                lines.append(f"- {target} rmse: `{_format_num(target_payload.get('rmse'))}`")
                lines.append(f"- {target} mae: `{_format_num(target_payload.get('mae'))}`")
        lines.append("")
    return "\n".join(lines)


def train_analysis_baselines(request: ModelRunRequest) -> dict[str, Any]:
    output_dir = _ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version) / "models"
    output_dir.mkdir(parents=True, exist_ok=True)
    with managed_connection() as connection:
        profiles_df = _load_game_profiles_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
        state_df = _load_state_panel_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
    state_model_df = _prepare_state_model_frame(state_df)
    train_cutoff = _resolve_train_cutoff(state_model_df["game_date"] if not state_model_df.empty else pd.Series(dtype="datetime64[ns]"), request.train_cutoff)
    tracks: dict[str, Any] = {}
    requested_tracks = (
        [request.target_family]
        if request.target_family != "all"
        else ["volatility_inversion", "trade_window_quality", "winner_definition_timing"]
    )
    if "volatility_inversion" in requested_tracks:
        tracks["volatility_inversion"] = _train_volatility_inversion_baseline(state_model_df, train_cutoff)
    if "trade_window_quality" in requested_tracks:
        tracks["trade_window_quality"] = _train_trade_window_quality_baseline(state_model_df, train_cutoff)
    if "winner_definition_timing" in requested_tracks:
        tracks["winner_definition_timing"] = _train_winner_definition_timing_baseline(state_model_df, profiles_df, train_cutoff)

    payload = {
        "season": request.season,
        "season_phase": request.season_phase,
        "analysis_version": request.analysis_version,
        "feature_set_version": request.feature_set_version,
        "train_cutoff": train_cutoff.isoformat() if train_cutoff is not None else None,
        "validation_window": request.validation_window,
        "tracks": tracks,
        "artifacts": {},
    }
    payload["artifacts"]["json"] = _write_json(output_dir / "train_analysis_baselines.json", payload)
    payload["artifacts"]["markdown"] = _write_markdown(output_dir / "train_analysis_baselines.md", _render_model_markdown(payload))
    return to_jsonable(payload)


def _build_parser():
    return _analysis_build_parser()


def main() -> int:
    return _analysis_run_cli(
        build_analysis_mart=build_analysis_mart,
        build_analysis_report=build_analysis_report,
        run_analysis_backtests=run_analysis_backtests,
        train_analysis_baselines=train_analysis_baselines,
    )


if __name__ == "__main__":
    raise SystemExit(main())
