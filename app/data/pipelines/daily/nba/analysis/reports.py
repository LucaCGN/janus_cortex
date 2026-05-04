from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import (
    ensure_output_dir,
    write_frame,
    write_json,
    write_markdown,
)
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
)
from app.data.pipelines.daily.nba.analysis.mart_aggregates import load_game_profiles_df
from app.data.pipelines.daily.nba.analysis.universe import build_analysis_universe_qa_summary


REPORT_SECTION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "teams_against_expectation",
        "title": "Teams Against Opening Expectation",
        "columns": ["team_slug", "sample_games", "avg_expectation_gap_abs"],
    },
    {
        "key": "highest_volatility_teams",
        "title": "Highest In-Game Volatility",
        "columns": ["team_slug", "sample_games", "avg_ingame_range", "avg_total_swing"],
    },
    {
        "key": "opening_band_swing_profiles",
        "title": "Opening-Band Swing Profiles",
        "columns": ["opening_band", "sample_games", "avg_total_swing", "avg_inversion_count", "win_rate"],
    },
    {
        "key": "most_inversions",
        "title": "Most Frequent Inversions",
        "columns": ["team_slug", "sample_games", "avg_inversion_count", "inversion_rate"],
    },
    {
        "key": "favorite_drawdown_exposure",
        "title": "Favorite Drawdown Exposure",
        "columns": ["team_slug", "sample_games", "avg_favorite_drawdown"],
    },
    {
        "key": "control_vs_confidence_gap",
        "title": "Scoreboard-Control Mismatch Leaders",
        "columns": ["team_slug", "sample_games", "control_confidence_mismatch_rate"],
    },
    {
        "key": "opening_expectation_drift",
        "title": "Opening Expectation Drift",
        "columns": ["team_slug", "sample_games", "opening_price_trend_slope"],
    },
    {
        "key": "stable_favorites",
        "title": "Stable Favorites",
        "columns": ["team_slug", "sample_games", "avg_total_swing", "avg_inversion_count"],
    },
    {
        "key": "high_reversion_contexts",
        "title": "Highest Reversion Contexts",
        "columns": ["context_bucket", "sample_states", "large_swing_rate", "inversion_rate"],
    },
    {
        "key": "winner_definition_thresholds",
        "title": "Winner Definition Thresholds",
        "columns": ["threshold_cents", "sample_states", "stable_rate", "avg_reopen_rate"],
    },
)


def _query_df(connection: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _mean_or_none(values: Sequence[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None and not pd.isna(value)]
    if not clean:
        return None
    return float(sum(clean) / len(clean))


def _coerce_notes_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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


def _load_team_season_profiles_df(connection: Any, *, season: str, season_phase: str, analysis_version: str) -> pd.DataFrame:
    return _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_team_season_profiles
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY team_slug ASC;
        """,
        (season, season_phase, analysis_version),
    )


def _load_opening_band_profiles_df(connection: Any, *, season: str, season_phase: str, analysis_version: str) -> pd.DataFrame:
    return _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_opening_band_profiles
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY opening_band ASC;
        """,
        (season, season_phase, analysis_version),
    )


def _load_winner_definition_profiles_df(connection: Any, *, season: str, season_phase: str, analysis_version: str) -> pd.DataFrame:
    return _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_winner_definition_profiles
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY threshold_cents ASC, context_bucket ASC;
        """,
        (season, season_phase, analysis_version),
    )


def _opening_band_sort_key(label: str | None) -> tuple[int, str]:
    raw = str(label or "")
    lower_raw, _, _ = raw.partition("-")
    try:
        lower_bound = int(lower_raw)
    except ValueError:
        lower_bound = 10**9
    return lower_bound, raw


def _build_report_universe_frame(profiles_df: pd.DataFrame) -> pd.DataFrame:
    if profiles_df.empty:
        return pd.DataFrame(
            columns=[
                "game_id",
                "game_date",
                "game_start_time",
                "home_team_slug",
                "away_team_slug",
                "coverage_status",
                "research_ready_flag",
            ]
        )

    work = profiles_df.copy()
    work["_notes_json"] = work["notes_json"].apply(_coerce_notes_json) if "notes_json" in work.columns else [{} for _ in range(len(work))]
    rows: list[dict[str, Any]] = []
    for game_id, group in work.groupby("game_id", sort=True):
        home_rows = group[group["team_side"] == "home"]
        away_rows = group[group["team_side"] == "away"]
        representative = group.iloc[0]
        home_row = home_rows.iloc[0] if not home_rows.empty else representative
        away_row = away_rows.iloc[0] if not away_rows.empty else representative
        rows.append(
            {
                "game_id": str(game_id),
                "game_date": representative.get("game_date"),
                "game_start_time": representative.get("game_start_time"),
                "home_team_slug": home_row.get("team_slug"),
                "away_team_slug": away_row.get("team_slug"),
                "coverage_status": representative.get("coverage_status"),
                "research_ready_flag": bool(representative.get("research_ready_flag")),
            }
        )
    return pd.DataFrame(rows)


def build_report_universe_summary(profiles_df: pd.DataFrame) -> dict[str, Any]:
    return build_analysis_universe_qa_summary(_build_report_universe_frame(profiles_df))


def build_descriptive_report_payload(
    *,
    profiles_df: pd.DataFrame,
    state_df: pd.DataFrame,
    team_profiles_df: pd.DataFrame,
    opening_band_df: pd.DataFrame,
    winner_definition_df: pd.DataFrame,
    completeness_report: dict[str, Any],
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
            "section_order": [spec["key"] for spec in REPORT_SECTION_SPECS],
        }

    research_profiles_df = profiles_df.loc[profiles_df["research_ready_flag"].fillna(False)].copy()

    expectation_gap_rows: list[dict[str, Any]] = []
    for team_slug, group in research_profiles_df.groupby("team_slug"):
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
    expectation_gap_rows.sort(key=lambda row: (row["avg_expectation_gap_abs"] is None, -(row["avg_expectation_gap_abs"] or 0.0), row["team_slug"]))

    stable_favorite_rows = []
    favorite_profiles = research_profiles_df[research_profiles_df["opening_price"].fillna(-1.0) >= 0.6].copy()
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
            row["team_slug"],
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
        context_rollup = context_rollup.sort_values(["large_swing_rate", "sample_states", "context_bucket"], ascending=[False, False, True])
        reversion_contexts = context_rollup.head(12).to_dict(orient="records")

    threshold_summary = []
    if not winner_definition_df.empty:
        threshold_rollup = (
            winner_definition_df.groupby("threshold_cents")
            .agg(
                sample_states=("sample_states", "sum"),
                stable_states=("stable_states", "sum"),
                avg_reopen_rate=("reopen_rate", "mean"),
            )
            .reset_index()
        )
        threshold_rollup["stable_rate"] = threshold_rollup["stable_states"] / threshold_rollup["sample_states"].clip(lower=1)
        threshold_summary = threshold_rollup.sort_values("threshold_cents").to_dict(orient="records")

    highest_volatility_teams = (
        team_profiles_df.sort_values(["avg_ingame_range", "avg_total_swing", "team_slug"], ascending=[False, False, True])
        .head(10)[["team_slug", "sample_games", "avg_ingame_range", "avg_total_swing"]]
        .to_dict(orient="records")
        if not team_profiles_df.empty
        else []
    )
    opening_band_swing_profiles = []
    if not opening_band_df.empty:
        opening_band_work = opening_band_df.copy()
        opening_band_work = opening_band_work.sort_values(
            by="opening_band",
            key=lambda series: series.map(lambda value: _opening_band_sort_key(value)[0]),
        )
        opening_band_swing_profiles = opening_band_work[
            ["opening_band", "sample_games", "avg_total_swing", "avg_inversion_count", "win_rate"]
        ].to_dict(orient="records")
    most_inversions = (
        team_profiles_df.sort_values(["avg_inversion_count", "inversion_rate", "team_slug"], ascending=[False, False, True])
        .head(10)[["team_slug", "sample_games", "avg_inversion_count", "inversion_rate"]]
        .to_dict(orient="records")
        if not team_profiles_df.empty
        else []
    )
    favorite_drawdown_exposure = (
        team_profiles_df.sort_values(["avg_favorite_drawdown", "team_slug"], ascending=[False, True])
        .head(10)[["team_slug", "sample_games", "avg_favorite_drawdown"]]
        .to_dict(orient="records")
        if not team_profiles_df.empty
        else []
    )
    control_vs_confidence_gap = (
        team_profiles_df.sort_values(["control_confidence_mismatch_rate", "team_slug"], ascending=[False, True])
        .head(10)[["team_slug", "sample_games", "control_confidence_mismatch_rate"]]
        .to_dict(orient="records")
        if not team_profiles_df.empty
        else []
    )
    opening_expectation_drift = []
    if not team_profiles_df.empty:
        drift_work = team_profiles_df.copy()
        drift_work["_opening_price_trend_abs"] = pd.to_numeric(drift_work["opening_price_trend_slope"], errors="coerce").abs()
        drift_work = drift_work.sort_values(
            ["_opening_price_trend_abs", "opening_price_trend_slope", "team_slug"],
            ascending=[False, False, True],
        )
        opening_expectation_drift = drift_work.head(10)[["team_slug", "sample_games", "opening_price_trend_slope"]].to_dict(orient="records")

    payload = {
        "season": season,
        "season_phase": season_phase,
        "analysis_version": analysis_version,
        "universe": completeness_report,
        "section_order": [spec["key"] for spec in REPORT_SECTION_SPECS],
        "teams_against_expectation": expectation_gap_rows[:10],
        "highest_volatility_teams": highest_volatility_teams,
        "opening_band_swing_profiles": opening_band_swing_profiles,
        "opening_bands_largest_swings": opening_band_swing_profiles,
        "most_inversions": most_inversions,
        "favorite_drawdown_exposure": favorite_drawdown_exposure,
        "control_vs_confidence_gap": control_vs_confidence_gap,
        "opening_expectation_drift": opening_expectation_drift,
        "stable_favorites": stable_favorite_rows[:10],
        "high_reversion_contexts": reversion_contexts,
        "winner_definition_thresholds": threshold_summary,
    }
    return payload


def render_analysis_report_markdown(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"# NBA Analysis Report\n\nError: `{payload['error']}`\n"

    lines = [
        "# NBA Analysis Report",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        f"- Research-ready games: `{payload['universe'].get('research_ready_games', 0)}` / `{payload['universe'].get('games_total', 0)}`",
        f"- Descriptive-only games: `{payload['universe'].get('descriptive_only_games', 0)}`",
        f"- Excluded games: `{payload['universe'].get('excluded_games', 0)}`",
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
                elif value is None:
                    formatted.append("")
                else:
                    formatted.append(str(value))
            lines.append("| " + " | ".join(formatted) + " |")

    for spec in REPORT_SECTION_SPECS:
        _append_table(spec["title"], payload.get(spec["key"], []), list(spec["columns"]))

    return "\n".join(lines) + "\n"


def write_descriptive_report_artifacts(output_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    json_path = output_dir / "analysis_report.json"
    artifacts: dict[str, Any] = {
        "json": write_json(json_path, payload),
        "markdown": write_markdown(output_dir / "analysis_report.md", render_analysis_report_markdown(payload)),
        "sections": {},
        "qa": {},
    }
    for spec in REPORT_SECTION_SPECS:
        frame = pd.DataFrame(payload.get(spec["key"], []), columns=list(spec["columns"]))
        artifacts["sections"][spec["key"]] = write_frame(output_dir / f"analysis_report_{spec['key']}", frame)

    universe = payload.get("universe") or {}
    artifacts["qa"]["coverage_by_date"] = write_frame(
        output_dir / "analysis_report_coverage_by_date",
        pd.DataFrame(universe.get("coverage_by_date", [])),
    )
    artifacts["qa"]["coverage_by_team"] = write_frame(
        output_dir / "analysis_report_coverage_by_team",
        pd.DataFrame(universe.get("coverage_by_team", [])),
    )
    artifacts["qa"]["unresolved_game_samples"] = write_frame(
        output_dir / "analysis_report_unresolved_game_samples",
        pd.DataFrame(universe.get("unresolved_game_samples", [])),
    )
    return artifacts


def build_analysis_report(
    *,
    season: str = DEFAULT_SEASON,
    season_phase: str = DEFAULT_SEASON_PHASE,
    analysis_version: str = ANALYSIS_VERSION,
    output_root: str | None = None,
) -> dict[str, Any]:
    output_dir = ensure_output_dir(output_root, season, season_phase, analysis_version)
    with managed_connection() as connection:
        profiles_df = load_game_profiles_df(connection, season=season, season_phase=season_phase, analysis_version=analysis_version)
        state_df = _load_state_panel_df(connection, season=season, season_phase=season_phase, analysis_version=analysis_version)
        team_profiles_df = _load_team_season_profiles_df(connection, season=season, season_phase=season_phase, analysis_version=analysis_version)
        opening_band_df = _load_opening_band_profiles_df(connection, season=season, season_phase=season_phase, analysis_version=analysis_version)
        winner_definition_df = _load_winner_definition_profiles_df(connection, season=season, season_phase=season_phase, analysis_version=analysis_version)
    completeness_report = build_report_universe_summary(profiles_df)
    payload = build_descriptive_report_payload(
        profiles_df=profiles_df,
        state_df=state_df,
        team_profiles_df=team_profiles_df,
        opening_band_df=opening_band_df,
        winner_definition_df=winner_definition_df,
        completeness_report=completeness_report,
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
    )
    payload["artifacts"] = write_descriptive_report_artifacts(output_dir, payload)
    write_json(Path(payload["artifacts"]["json"]), payload)
    return to_jsonable(payload)


__all__ = [
    "REPORT_SECTION_SPECS",
    "build_analysis_report",
    "build_descriptive_report_payload",
    "build_report_universe_summary",
    "render_analysis_report_markdown",
    "write_descriptive_report_artifacts",
]
