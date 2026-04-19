from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import pandas as pd

from app.data.pipelines.daily.nba.analysis.contracts import (
    DESCRIPTIVE_ONLY_STATUSES,
    RESEARCH_READY_STATUSES,
    AnalysisUniverseRequest,
)


DESCRIPTIVE_VISIBLE_STATUSES = frozenset({*DESCRIPTIVE_ONLY_STATUSES, "no_history"})
EXCLUDED_VISIBLE_STATUSES = frozenset({"missing_feature_snapshot", "missing_token", "snapshot_only"})
DEFAULT_UNRESOLVED_SAMPLE_LIMIT = 12


@dataclass(slots=True)
class AnalysisUniverseResult:
    full_universe: pd.DataFrame
    research_ready: pd.DataFrame
    descriptive_only: pd.DataFrame
    qa_summary: dict[str, Any]

    def selected_frame(self, coverage_filter: str = "all") -> pd.DataFrame:
        if coverage_filter == "research_ready":
            return self.research_ready.copy()
        if coverage_filter == "descriptive_only":
            return self.descriptive_only.copy()
        if coverage_filter == "excluded":
            return self.full_universe.loc[self.full_universe["classification"] == "excluded"].reset_index(drop=True).copy()
        return self.full_universe.copy()


def _query_df(connection: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _coerce_bool(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool)


def _series_or_default(frame: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def _ordered_count_dict(series: pd.Series) -> dict[str, int]:
    counts = series.fillna("unknown").astype(str).value_counts()
    return {str(index): int(counts.loc[index]) for index in sorted(counts.index.tolist())}


def _serialise_scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            return str(value)
    return value


def _apply_classification(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if work.empty:
        return work

    work["coverage_status"] = _series_or_default(work, "coverage_status", "missing_feature_snapshot").fillna(
        "missing_feature_snapshot"
    ).astype(str)
    work["linked_event_count"] = pd.to_numeric(_series_or_default(work, "linked_event_count", 0), errors="coerce").fillna(
        0
    ).astype(int)
    work["timed_pbp_event_count"] = pd.to_numeric(
        _series_or_default(work, "timed_pbp_event_count", 0), errors="coerce"
    ).fillna(0).astype(int)
    work["covered_polymarket_game_flag"] = _coerce_bool(_series_or_default(work, "covered_polymarket_game_flag", False))
    work["feature_computed_at"] = pd.to_datetime(_series_or_default(work, "feature_computed_at", None), errors="coerce", utc=True)
    work["game_start_time"] = pd.to_datetime(_series_or_default(work, "game_start_time", None), errors="coerce", utc=True)
    work["price_window_start"] = pd.to_datetime(_series_or_default(work, "price_window_start", None), errors="coerce", utc=True)
    work["price_window_end"] = pd.to_datetime(_series_or_default(work, "price_window_end", None), errors="coerce", utc=True)
    work["game_date"] = pd.to_datetime(_series_or_default(work, "game_date", None), errors="coerce").dt.date

    pre_columns = [
        "home_pre_game_price_min",
        "home_pre_game_price_max",
        "away_pre_game_price_min",
        "away_pre_game_price_max",
    ]
    ingame_columns = [
        "home_in_game_price_min",
        "home_in_game_price_max",
        "away_in_game_price_min",
        "away_in_game_price_max",
    ]
    required_nullable_columns = ["feature_event_id", *pre_columns, *ingame_columns]
    for column in required_nullable_columns:
        if column not in work.columns:
            work[column] = pd.NA

    snapshot_pregame_market = work[pre_columns].notna().all(axis=1)
    snapshot_ingame_market = work[ingame_columns].notna().all(axis=1)
    snapshot_market_extrema_present = work[[*pre_columns, *ingame_columns]].notna().any(axis=1)
    coverage_implies_market_path = work["coverage_status"].isin(RESEARCH_READY_STATUSES)

    work["has_feature_snapshot"] = work["feature_computed_at"].notna()
    work["has_linked_event"] = work["feature_event_id"].notna() | (work["linked_event_count"] > 0)
    work["has_timed_pbp"] = work["timed_pbp_event_count"] > 0
    work["market_path_inferred_from_coverage_status"] = coverage_implies_market_path & ~snapshot_market_extrema_present
    work["has_bilateral_pregame_market"] = snapshot_pregame_market | work["market_path_inferred_from_coverage_status"]
    work["has_bilateral_ingame_market"] = snapshot_ingame_market | work["market_path_inferred_from_coverage_status"]
    work["has_bilateral_market_path"] = work["has_bilateral_pregame_market"] & work["has_bilateral_ingame_market"]
    if "research_ready_flag" in work.columns:
        work["research_ready_flag"] = _coerce_bool(work["research_ready_flag"])
    else:
        work["research_ready_flag"] = (
            work["coverage_status"].isin(RESEARCH_READY_STATUSES)
            & work["covered_polymarket_game_flag"]
            & work["has_linked_event"]
            & work["has_timed_pbp"]
            & work["has_bilateral_market_path"]
        )

    def classify_row(row: pd.Series) -> pd.Series:
        coverage_status = str(row.get("coverage_status") or "missing_feature_snapshot")
        if bool(row.get("research_ready_flag")):
            return pd.Series({"classification": "research_ready", "classification_reason": coverage_status})
        if coverage_status in DESCRIPTIVE_VISIBLE_STATUSES:
            return pd.Series({"classification": "descriptive_only", "classification_reason": coverage_status})
        if coverage_status in RESEARCH_READY_STATUSES:
            if not bool(row.get("covered_polymarket_game_flag")):
                reason = "covered_pre_and_ingame_without_polymarket_flag"
            elif not bool(row.get("has_linked_event")):
                reason = "covered_pre_and_ingame_without_linked_event"
            elif not bool(row.get("has_timed_pbp")):
                reason = "covered_pre_and_ingame_without_timed_pbp"
            elif not bool(row.get("has_bilateral_pregame_market")):
                reason = "covered_pre_and_ingame_without_bilateral_pregame_market"
            elif not bool(row.get("has_bilateral_ingame_market")):
                reason = "covered_pre_and_ingame_without_bilateral_ingame_market"
            else:
                reason = "covered_pre_and_ingame_failed_research_gate"
            return pd.Series({"classification": "excluded", "classification_reason": reason})
        if coverage_status in EXCLUDED_VISIBLE_STATUSES:
            return pd.Series({"classification": "excluded", "classification_reason": coverage_status})
        return pd.Series({"classification": "excluded", "classification_reason": f"unsupported_coverage_status:{coverage_status}"})

    classifications = work.apply(classify_row, axis=1)
    work["classification"] = classifications["classification"]
    work["classification_reason"] = classifications["classification_reason"]
    work["descriptive_only_flag"] = work["classification"].eq("descriptive_only")
    work["excluded_flag"] = work["classification"].eq("excluded")
    work["research_bucket"] = work["classification"]
    return work.reset_index(drop=True)


def build_analysis_universe_qa_summary(
    universe_df: pd.DataFrame,
    *,
    unresolved_sample_limit: int = DEFAULT_UNRESOLVED_SAMPLE_LIMIT,
) -> dict[str, Any]:
    frame = _apply_classification(universe_df)
    if frame.empty:
        return {
            "games_total": 0,
            "research_ready_games": 0,
            "descriptive_only_games": 0,
            "excluded_games": 0,
            "classification_counts": {},
            "coverage_status_counts": {},
            "coverage_by_date": [],
            "coverage_by_team": [],
            "unresolved_game_samples": [],
            "undercovered_teams": [],
            "undercovered_dates": [],
        }

    unresolved = frame.loc[frame["classification"] != "research_ready"].copy()
    classification_counts = _ordered_count_dict(frame["classification"])
    coverage_counts = _ordered_count_dict(frame["coverage_status"])

    coverage_by_date: list[dict[str, Any]] = []
    if "game_date" in frame.columns:
        grouped = frame.groupby("game_date", dropna=False, sort=True)
        for game_date, group in grouped:
            coverage_by_date.append(
                {
                    "game_date": None if pd.isna(game_date) else str(game_date),
                    "games": int(group["game_id"].astype(str).nunique()) if "game_id" in group.columns else int(len(group)),
                    "research_ready_games": int(group["classification"].eq("research_ready").sum()),
                    "descriptive_only_games": int(group["classification"].eq("descriptive_only").sum()),
                    "excluded_games": int(group["classification"].eq("excluded").sum()),
                    "coverage_status_counts": _ordered_count_dict(group["coverage_status"]),
                }
            )

    coverage_by_team: list[dict[str, Any]] = []
    if not unresolved.empty:
        team_rows: list[pd.DataFrame] = []
        team_columns = [column for column in ("home_team_slug", "away_team_slug") if column in unresolved.columns]
        for column in team_columns:
            partial = unresolved[["coverage_status", "classification", column]].copy()
            partial = partial.rename(columns={column: "team_slug"})
            team_rows.append(partial)
        if team_rows:
            teams_frame = pd.concat(team_rows, ignore_index=True).dropna(subset=["team_slug"])
            teams_frame["team_slug"] = teams_frame["team_slug"].astype(str)
            grouped = teams_frame.groupby("team_slug", sort=True)
            for team_slug, group in grouped:
                coverage_by_team.append(
                    {
                        "team_slug": str(team_slug),
                        "games": int(len(group)),
                        "coverage_status_counts": _ordered_count_dict(group["coverage_status"]),
                        "classification_counts": _ordered_count_dict(group["classification"]),
                    }
                )
            coverage_by_team.sort(key=lambda item: (-item["games"], item["team_slug"]))

    sample_columns = [
        column
        for column in (
            "game_id",
            "game_date",
            "game_start_time",
            "home_team_slug",
            "away_team_slug",
            "coverage_status",
            "classification",
            "classification_reason",
            "linked_event_count",
            "timed_pbp_event_count",
            "has_linked_event",
            "has_timed_pbp",
            "has_bilateral_market_path",
        )
        if column in unresolved.columns
    ]
    unresolved_game_samples = []
    if sample_columns:
        sample_frame = unresolved.sort_values(
            by=[column for column in ("game_date", "game_start_time", "game_id") if column in unresolved.columns],
            ascending=True,
            na_position="last",
        ).head(unresolved_sample_limit)
        for row in sample_frame[sample_columns].to_dict(orient="records"):
            unresolved_game_samples.append({key: _serialise_scalar(value) for key, value in row.items()})

    return {
        "games_total": int(frame["game_id"].astype(str).nunique()) if "game_id" in frame.columns else int(len(frame)),
        "research_ready_games": int(frame["classification"].eq("research_ready").sum()),
        "descriptive_only_games": int(frame["classification"].eq("descriptive_only").sum()),
        "excluded_games": int(frame["classification"].eq("excluded").sum()),
        "classification_counts": classification_counts,
        "coverage_status_counts": coverage_counts,
        "coverage_by_date": coverage_by_date,
        "coverage_by_team": coverage_by_team,
        "unresolved_game_samples": unresolved_game_samples,
        "undercovered_teams": coverage_by_team[:DEFAULT_UNRESOLVED_SAMPLE_LIMIT],
        "undercovered_dates": [
            {
                "game_date": item["game_date"],
                "games": item["descriptive_only_games"] + item["excluded_games"],
            }
            for item in coverage_by_date
            if (item["descriptive_only_games"] + item["excluded_games"]) > 0
        ][:DEFAULT_UNRESOLVED_SAMPLE_LIMIT],
    }


def load_analysis_universe(connection: Any, request: AnalysisUniverseRequest) -> AnalysisUniverseResult:
    frame = _query_df(
        connection,
        """
        WITH latest_features AS (
            SELECT DISTINCT ON (f.game_id)
                f.game_id,
                f.event_id,
                f.computed_at,
                f.feature_version,
                f.pbp_event_count,
                f.lead_changes,
                f.coverage_status,
                f.covered_polymarket_game_flag,
                f.home_pre_game_price_min,
                f.home_pre_game_price_max,
                f.away_pre_game_price_min,
                f.away_pre_game_price_max,
                f.home_in_game_price_min,
                f.home_in_game_price_max,
                f.away_in_game_price_min,
                f.away_in_game_price_max,
                f.price_window_start,
                f.price_window_end
            FROM nba.nba_game_feature_snapshots f
            WHERE f.season = %s
            ORDER BY f.game_id, f.computed_at DESC
        ),
        link_rollup AS (
            SELECT
                l.game_id,
                count(*)::int AS linked_event_count
            FROM nba.nba_game_event_links l
            GROUP BY l.game_id
        ),
        pbp_rollup AS (
            SELECT
                p.game_id,
                count(*)::int AS pbp_row_count,
                count(*) FILTER (
                    WHERE COALESCE(NULLIF(p.payload_json ->> 'timeActual', ''), '') <> ''
                       OR (
                            p.period IS NOT NULL
                            AND COALESCE(NULLIF(p.clock, ''), '') <> ''
                            AND g.game_start_time IS NOT NULL
                       )
                )::int AS timed_pbp_event_count
            FROM nba.nba_play_by_play p
            JOIN nba.nba_games g ON g.game_id = p.game_id
            GROUP BY p.game_id
        )
        SELECT
            g.game_id,
            g.season,
            g.season_phase,
            g.game_date,
            g.game_start_time,
            g.game_status,
            g.game_status_text,
            g.home_team_id,
            g.away_team_id,
            g.home_team_slug,
            g.away_team_slug,
            g.home_score,
            g.away_score,
            lf.event_id AS feature_event_id,
            lf.computed_at AS feature_computed_at,
            lf.feature_version,
            lf.pbp_event_count,
            COALESCE(pbp.timed_pbp_event_count, 0) AS timed_pbp_event_count,
            COALESCE(pbp.pbp_row_count, 0) AS pbp_row_count,
            lf.lead_changes,
            COALESCE(lf.coverage_status, 'missing_feature_snapshot') AS coverage_status,
            COALESCE(lf.covered_polymarket_game_flag, FALSE) AS covered_polymarket_game_flag,
            lf.home_pre_game_price_min,
            lf.home_pre_game_price_max,
            lf.away_pre_game_price_min,
            lf.away_pre_game_price_max,
            lf.home_in_game_price_min,
            lf.home_in_game_price_max,
            lf.away_in_game_price_min,
            lf.away_in_game_price_max,
            lf.price_window_start,
            lf.price_window_end,
            COALESCE(lr.linked_event_count, 0) AS linked_event_count
        FROM nba.nba_games g
        LEFT JOIN latest_features lf ON lf.game_id = g.game_id
        LEFT JOIN link_rollup lr ON lr.game_id = g.game_id
        LEFT JOIN pbp_rollup pbp ON pbp.game_id = g.game_id
        WHERE g.season = %s
          AND g.season_phase = %s
          AND g.game_status = 3
        ORDER BY g.game_date ASC NULLS LAST, g.game_start_time ASC NULLS LAST, g.game_id ASC;
        """,
        (request.season, request.season, request.season_phase),
    )
    classified = _apply_classification(frame)
    research_ready = classified.loc[classified["classification"] == "research_ready"].reset_index(drop=True)
    descriptive_only = classified.loc[classified["classification"] == "descriptive_only"].reset_index(drop=True)
    qa_summary = build_analysis_universe_qa_summary(classified)
    return AnalysisUniverseResult(
        full_universe=classified,
        research_ready=research_ready,
        descriptive_only=descriptive_only,
        qa_summary=qa_summary,
    )


__all__ = [
    "AnalysisUniverseResult",
    "DESCRIPTIVE_VISIBLE_STATUSES",
    "EXCLUDED_VISIBLE_STATUSES",
    "build_analysis_universe_qa_summary",
    "load_analysis_universe",
]
