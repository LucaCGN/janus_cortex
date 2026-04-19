from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd


def _query_df(connection: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


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


def _team_row_sort_key(row: dict[str, Any]) -> tuple[str, int]:
    team_slug = str(row.get("team_slug") or "")
    team_id = row.get("team_id")
    if team_id is None or pd.isna(team_id):
        return team_slug, -1
    return team_slug, int(team_id)


def _opening_band_sort_key(label: str | None) -> tuple[int, str]:
    raw = str(label or "")
    lower_raw, _, _ = raw.partition("-")
    try:
        lower_bound = int(lower_raw)
    except ValueError:
        lower_bound = 10**9
    return lower_bound, raw


def _research_ready_subset(profiles_df: pd.DataFrame) -> pd.DataFrame:
    if profiles_df.empty:
        return profiles_df.copy()
    if "research_ready_flag" not in profiles_df.columns:
        return profiles_df.copy()
    return profiles_df.loc[profiles_df["research_ready_flag"].fillna(False)].copy()


def load_game_profiles_df(connection: Any, *, season: str, season_phase: str, analysis_version: str) -> pd.DataFrame:
    frame = _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_game_team_profiles
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC;
        """,
        (season, season_phase, analysis_version),
    )
    if frame.empty:
        return frame
    for column in (
        "computed_at",
        "game_start_time",
        "first_inversion_at",
        "winner_stable_70_at",
        "winner_stable_80_at",
        "winner_stable_90_at",
        "winner_stable_95_at",
    ):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    if "game_date" in frame.columns:
        frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce").dt.date
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
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _rolling_window_payload(group: pd.DataFrame, *, window: int) -> dict[str, Any]:
    if group.empty:
        return {"window": window, "latest": None, "history": []}
    ordered = group.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    history: list[dict[str, Any]] = []
    for end_index in range(len(ordered)):
        window_df = ordered.iloc[max(0, end_index - window + 1) : end_index + 1]
        history.append(
            {
                "through_game_id": str(window_df.iloc[-1]["game_id"]),
                "through_game_date": str(window_df.iloc[-1]["game_date"]),
                "window_sample_games": int(len(window_df)),
                "avg_opening_price": _mean_or_none(window_df["opening_price"].tolist()),
                "avg_total_swing": _mean_or_none(window_df["total_swing"].tolist()),
                "avg_inversion_count": _mean_or_none(window_df["inversion_count"].tolist()),
            }
        )
    return {
        "window": window,
        "latest": history[-1] if history else None,
        "history": history[-10:],
    }


def build_team_season_profile_rows(
    profiles_df: pd.DataFrame,
    state_df: pd.DataFrame,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    work = _research_ready_subset(profiles_df)
    if work.empty:
        return []
    rows: list[dict[str, Any]] = []
    mismatch_rates = (
        state_df.groupby("team_id")["scoreboard_control_mismatch_flag"].mean().to_dict()
        if not state_df.empty and "team_id" in state_df.columns
        else {}
    )
    for team_id, group in work.groupby("team_id"):
        team_group = group.sort_values(["game_date", "game_id"]).reset_index(drop=True)
        final_wins = team_group[team_group["final_winner_flag"] == True]
        favorite_group = team_group[team_group["opening_price"].fillna(-1) >= 0.5]
        underdog_group = team_group[team_group["opening_price"].fillna(2) < 0.5]
        rows.append(
            {
                "team_id": int(team_id) if team_id is not None and not pd.isna(team_id) else None,
                "team_slug": str(team_group.iloc[0]["team_slug"]) if len(team_group) else None,
                "season": season,
                "season_phase": season_phase,
                "analysis_version": analysis_version,
                "computed_at": computed_at,
                "sample_games": int(len(team_group)),
                "research_ready_games": int(team_group["research_ready_flag"].fillna(False).sum()),
                "wins": int(team_group["final_winner_flag"].fillna(False).sum()),
                "losses": int((~team_group["final_winner_flag"].fillna(False)).sum()),
                "favorite_games": int(len(favorite_group)),
                "underdog_games": int(len(underdog_group)),
                "avg_opening_price": _mean_or_none(team_group["opening_price"].tolist()),
                "avg_closing_price": _mean_or_none(team_group["closing_price"].tolist()),
                "avg_pregame_range": _mean_or_none(team_group["pregame_price_range"].tolist()),
                "avg_ingame_range": _mean_or_none(team_group["ingame_price_range"].tolist()),
                "avg_total_swing": _mean_or_none(team_group["total_swing"].tolist()),
                "avg_max_favorable_excursion": _mean_or_none(team_group["max_favorable_excursion"].tolist()),
                "avg_max_adverse_excursion": _mean_or_none(team_group["max_adverse_excursion"].tolist()),
                "avg_inversion_count": _mean_or_none(team_group["inversion_count"].tolist()),
                "games_with_inversion": int((pd.to_numeric(team_group["inversion_count"], errors="coerce").fillna(0) > 0).sum()),
                "inversion_rate": float((pd.to_numeric(team_group["inversion_count"], errors="coerce").fillna(0) > 0).mean())
                if len(team_group)
                else None,
                "avg_seconds_above_50c": _mean_or_none(team_group["seconds_above_50c"].tolist()),
                "avg_seconds_below_50c": _mean_or_none(team_group["seconds_below_50c"].tolist()),
                "avg_favorite_drawdown": _mean_or_none(favorite_group["max_adverse_excursion"].tolist()),
                "avg_underdog_spike": _mean_or_none(underdog_group["max_favorable_excursion"].tolist()),
                "control_confidence_mismatch_rate": mismatch_rates.get(team_id),
                "opening_price_trend_slope": _linear_slope(team_group["opening_price"].tolist()),
                "winner_stable_70_rate": float(final_wins["winner_stable_70_clock_elapsed_seconds"].notna().mean()) if len(final_wins) else None,
                "winner_stable_80_rate": float(final_wins["winner_stable_80_clock_elapsed_seconds"].notna().mean()) if len(final_wins) else None,
                "winner_stable_90_rate": float(final_wins["winner_stable_90_clock_elapsed_seconds"].notna().mean()) if len(final_wins) else None,
                "winner_stable_95_rate": float(final_wins["winner_stable_95_clock_elapsed_seconds"].notna().mean()) if len(final_wins) else None,
                "avg_winner_stable_70_clock_elapsed_seconds": _mean_or_none(final_wins["winner_stable_70_clock_elapsed_seconds"].tolist()),
                "avg_winner_stable_80_clock_elapsed_seconds": _mean_or_none(final_wins["winner_stable_80_clock_elapsed_seconds"].tolist()),
                "avg_winner_stable_90_clock_elapsed_seconds": _mean_or_none(final_wins["winner_stable_90_clock_elapsed_seconds"].tolist()),
                "avg_winner_stable_95_clock_elapsed_seconds": _mean_or_none(final_wins["winner_stable_95_clock_elapsed_seconds"].tolist()),
                "rolling_10_json": _rolling_window_payload(team_group, window=10),
                "rolling_20_json": _rolling_window_payload(team_group, window=20),
                "notes_json": {
                    "research_ready_games": int(team_group["research_ready_flag"].fillna(False).sum()),
                    "opening_expectation_gap_abs_avg": _mean_or_none(
                        [
                            abs((1.0 if bool(winner) else 0.0) - float(open_price))
                            for winner, open_price in zip(
                                team_group["final_winner_flag"].fillna(False).tolist(),
                                team_group["opening_price"].tolist(),
                            )
                            if open_price is not None and not pd.isna(open_price)
                        ]
                    ),
                },
            }
        )
    rows.sort(key=_team_row_sort_key)
    return rows


def build_opening_band_profile_rows(
    profiles_df: pd.DataFrame,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    work = _research_ready_subset(profiles_df).dropna(subset=["opening_band"]).copy()
    if work.empty:
        return []
    rows: list[dict[str, Any]] = []
    for opening_band, group in work.groupby("opening_band"):
        winners = group[group["final_winner_flag"] == True]
        rows.append(
            {
                "season": season,
                "season_phase": season_phase,
                "opening_band": str(opening_band),
                "analysis_version": analysis_version,
                "computed_at": computed_at,
                "sample_games": int(len(group)),
                "win_rate": float(group["final_winner_flag"].fillna(False).mean()),
                "avg_opening_price": _mean_or_none(group["opening_price"].tolist()),
                "avg_closing_price": _mean_or_none(group["closing_price"].tolist()),
                "avg_ingame_range": _mean_or_none(group["ingame_price_range"].tolist()),
                "avg_total_swing": _mean_or_none(group["total_swing"].tolist()),
                "avg_max_favorable_excursion": _mean_or_none(group["max_favorable_excursion"].tolist()),
                "avg_max_adverse_excursion": _mean_or_none(group["max_adverse_excursion"].tolist()),
                "avg_inversion_count": _mean_or_none(group["inversion_count"].tolist()),
                "inversion_rate": float((pd.to_numeric(group["inversion_count"], errors="coerce").fillna(0) > 0).mean()),
                "winner_stable_70_rate": float(winners["winner_stable_70_clock_elapsed_seconds"].notna().mean()) if len(winners) else None,
                "winner_stable_80_rate": float(winners["winner_stable_80_clock_elapsed_seconds"].notna().mean()) if len(winners) else None,
                "winner_stable_90_rate": float(winners["winner_stable_90_clock_elapsed_seconds"].notna().mean()) if len(winners) else None,
                "winner_stable_95_rate": float(winners["winner_stable_95_clock_elapsed_seconds"].notna().mean()) if len(winners) else None,
                "notes_json": {
                    "research_ready_games": int(group["research_ready_flag"].fillna(False).sum()),
                },
            }
        )
    rows.sort(key=lambda row: _opening_band_sort_key(row.get("opening_band")))
    return rows


__all__ = [
    "build_opening_band_profile_rows",
    "build_team_season_profile_rows",
    "load_game_profiles_df",
]
