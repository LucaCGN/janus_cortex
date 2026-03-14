from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.data.nodes.nba.live.play_by_play import (
    PlayByPlayRequest,
    compute_lead_change_summary,
    fetch_play_by_play_df,
)
from app.data.nodes.nba.schedule.season_schedule import fetch_season_schedule_df
from app.data.nodes.polymarket.gamma.nba.markets_moneyline_node import (
    NBAMoneylineMarketsRequest,
    fetch_nba_moneyline_df,
)
from app.data.nodes.polymarket.gamma.nba.odds_history_node import (
    NBAOddsHistoryRequest,
    fetch_clob_prices_history,
)


@dataclass
class _GamePbpAuditResult:
    game_id: str
    has_data: bool
    row_count: int
    summary: dict[str, Any] | None
    home_team_slug: str | None
    away_team_slug: str | None
    home_score: int | None
    away_score: int | None


def _expected_slug(row: pd.Series) -> str:
    return (
        "nba-"
        + str(row.get("away_team_slug") or "").strip().lower()
        + "-"
        + str(row.get("home_team_slug") or "").strip().lower()
        + "-"
        + str(row.get("game_date") or "").strip()
    )


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _audit_single_game(row: pd.Series) -> _GamePbpAuditResult:
    game_id = str(row.get("game_id") or "")
    df = fetch_play_by_play_df(PlayByPlayRequest(game_id=game_id))
    if df.empty:
        return _GamePbpAuditResult(
            game_id=game_id,
            has_data=False,
            row_count=0,
            summary=None,
            home_team_slug=str(row.get("home_team_slug") or "") or None,
            away_team_slug=str(row.get("away_team_slug") or "") or None,
            home_score=_safe_int(row.get("home_score")),
            away_score=_safe_int(row.get("away_score")),
        )
    return _GamePbpAuditResult(
        game_id=game_id,
        has_data=True,
        row_count=len(df),
        summary=compute_lead_change_summary(df),
        home_team_slug=str(row.get("home_team_slug") or "") or None,
        away_team_slug=str(row.get("away_team_slug") or "") or None,
        home_score=_safe_int(row.get("home_score")),
        away_score=_safe_int(row.get("away_score")),
    )


def _collect_moneyline_season_df(
    *,
    start_dt: datetime,
    end_dt: datetime,
    window_days: int,
    page_size: int,
    max_pages: int,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cursor = start_dt
    while cursor <= end_dt:
        window_end = min(cursor + timedelta(days=window_days - 1), end_dt)
        req = NBAMoneylineMarketsRequest(
            only_open=False,
            start_date_min=cursor,
            start_date_max=window_end,
            page_size=page_size,
            max_pages=max_pages,
            use_events_fallback=True,
        )
        df = fetch_nba_moneyline_df(req=req)
        if not df.empty:
            frames.append(df)
        cursor = window_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["market_id", "outcome"])


def _top_team_rows(
    team_stats: dict[str, dict[str, float]],
    key: str,
    *,
    limit: int = 10,
    min_games: int = 10,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for team_slug, stats in team_stats.items():
        games = int(stats.get("games_with_pbp") or 0)
        if games < min_games:
            continue
        rows.append(
            {
                "team": team_slug,
                "games_with_pbp": games,
                key: round(float(stats.get(key) or 0.0), 3),
                "wins": int(stats.get("wins") or 0),
                "losses": int(stats.get("losses") or 0),
            }
        )
    rows.sort(key=lambda item: item[key], reverse=True)
    return rows[:limit]


def build_season_strategy_audit(
    *,
    season: str = "2025-26",
    pbp_max_workers: int = 6,
    pbp_game_limit: int | None = None,
    moneyline_window_days: int = 14,
    moneyline_page_size: int = 100,
    moneyline_max_pages: int = 30,
    history_sample_events_per_month: int = 3,
) -> dict[str, Any]:
    schedule_df = fetch_season_schedule_df(season).copy()
    if schedule_df.empty:
        return {"season": season, "error": "schedule_unavailable"}

    schedule_df["expected_slug"] = schedule_df.apply(_expected_slug, axis=1)
    schedule_df["game_start_time"] = pd.to_datetime(schedule_df["game_start_time"], errors="coerce", utc=True)

    finished_df = schedule_df[schedule_df["game_status"] == 3].copy().reset_index(drop=True)
    if pbp_game_limit is not None and pbp_game_limit > 0:
        finished_df = finished_df.head(pbp_game_limit).reset_index(drop=True)

    pbp_results: list[_GamePbpAuditResult] = []
    with ThreadPoolExecutor(max_workers=max(1, pbp_max_workers)) as executor:
        futures = [executor.submit(_audit_single_game, row) for _, row in finished_df.iterrows()]
        for future in as_completed(futures):
            pbp_results.append(future.result())

    pbp_results.sort(key=lambda item: item.game_id)
    games_with_pbp = [item for item in pbp_results if item.has_data and item.summary is not None]
    team_stats: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for item in games_with_pbp:
        summary = item.summary or {}
        home_team = item.home_team_slug or "UNKNOWN_HOME"
        away_team = item.away_team_slug or "UNKNOWN_AWAY"
        home_score = item.home_score or 0
        away_score = item.away_score or 0
        home_won = home_score > away_score
        away_won = away_score > home_score

        team_stats[home_team]["games_with_pbp"] += 1
        team_stats[home_team]["lead_changes_total"] += float(summary.get("lead_changes") or 0)
        team_stats[home_team]["losing_segments_total"] += float(summary.get("away_lead_segments") or 0)
        team_stats[home_team]["lead_events_while_trailing_total"] += float(summary.get("away_lead_events") or 0)
        team_stats[home_team]["wins"] += 1 if home_won else 0
        team_stats[home_team]["losses"] += 1 if away_won else 0
        if away_won:
            team_stats[home_team]["largest_lead_in_losses_total"] += float(summary.get("home_largest_lead") or 0)
            team_stats[home_team]["losses_after_leading"] += 1 if float(summary.get("home_largest_lead") or 0) > 0 else 0

        team_stats[away_team]["games_with_pbp"] += 1
        team_stats[away_team]["lead_changes_total"] += float(summary.get("lead_changes") or 0)
        team_stats[away_team]["losing_segments_total"] += float(summary.get("home_lead_segments") or 0)
        team_stats[away_team]["lead_events_while_trailing_total"] += float(summary.get("home_lead_events") or 0)
        team_stats[away_team]["wins"] += 1 if away_won else 0
        team_stats[away_team]["losses"] += 1 if home_won else 0
        if home_won:
            team_stats[away_team]["largest_lead_in_losses_total"] += float(summary.get("away_largest_lead") or 0)
            team_stats[away_team]["losses_after_leading"] += 1 if float(summary.get("away_largest_lead") or 0) > 0 else 0

    for stats in team_stats.values():
        games = max(int(stats.get("games_with_pbp") or 0), 1)
        losses = max(int(stats.get("losses") or 0), 1)
        stats["avg_lead_changes"] = float(stats.get("lead_changes_total") or 0.0) / games
        stats["avg_losing_segments"] = float(stats.get("losing_segments_total") or 0.0) / games
        stats["avg_trailing_events"] = float(stats.get("lead_events_while_trailing_total") or 0.0) / games
        stats["avg_largest_lead_in_losses"] = float(stats.get("largest_lead_in_losses_total") or 0.0) / losses

    now = datetime.now(timezone.utc)
    season_start = datetime(2025, 10, 1, tzinfo=timezone.utc)
    moneyline_df = _collect_moneyline_season_df(
        start_dt=season_start,
        end_dt=now,
        window_days=moneyline_window_days,
        page_size=moneyline_page_size,
        max_pages=moneyline_max_pages,
    )
    moneyline_event_slugs = set(moneyline_df.get("event_slug", pd.Series(dtype=str)).dropna().astype(str)) if not moneyline_df.empty else set()
    covered_finished_games = int(finished_df["expected_slug"].isin(moneyline_event_slugs).sum())

    history_sample_summary = {
        "sample_outcomes": 0,
        "sample_events": 0,
        "outcomes_with_points": 0,
        "outcomes_with_pre_and_ingame": 0,
        "by_month": [],
        "schedule_anchor_required": True,
    }
    if not moneyline_df.empty:
        history_df = moneyline_df.copy()
        history_df = history_df[history_df["event_slug"].notna() & history_df["token_id"].notna()].copy()
        schedule_start_map = (
            schedule_df.dropna(subset=["game_start_time"])
            .drop_duplicates(subset=["expected_slug"])
            .set_index("expected_slug")["game_start_time"]
            .to_dict()
        )
        history_df["schedule_start"] = history_df["event_slug"].map(schedule_start_map)
        history_df = history_df.dropna(subset=["schedule_start"]).copy()
        if not history_df.empty:
            history_df["month"] = pd.to_datetime(history_df["schedule_start"], utc=True).dt.strftime("%Y-%m")
            sample_frames: list[pd.DataFrame] = []
            for _, month_df in history_df.groupby("month"):
                event_slugs = list(dict.fromkeys(month_df["event_slug"].astype(str)))[: max(1, history_sample_events_per_month)]
                sample_frames.append(month_df[month_df["event_slug"].isin(event_slugs)])
            sample_df = pd.concat(sample_frames, ignore_index=True) if sample_frames else pd.DataFrame()
            if not sample_df.empty:
                sample_rows: list[dict[str, Any]] = []
                for _, row in sample_df.iterrows():
                    game_start = pd.to_datetime(row["schedule_start"], utc=True).to_pydatetime()
                    history_req = NBAOddsHistoryRequest(
                        start_date_min=game_start - timedelta(hours=12),
                        start_date_max=game_start + timedelta(hours=6),
                        interval="1m",
                        fidelity=10,
                        allow_snapshot_fallback=False,
                        retries=1,
                        request_timeout_sec=10.0,
                    )
                    points = fetch_clob_prices_history(str(row["token_id"]), history_req)
                    pre_count = sum(1 for point in points if point["ts"] < game_start)
                    ingame_count = sum(1 for point in points if game_start <= point["ts"] <= game_start + timedelta(hours=5))
                    sample_rows.append(
                        {
                            "month": row["month"],
                            "event_slug": row["event_slug"],
                            "outcome": row["outcome"],
                            "point_count": len(points),
                            "has_points": len(points) > 0,
                            "has_pre": pre_count > 0,
                            "has_ingame": ingame_count > 0,
                            "has_both": pre_count > 0 and ingame_count > 0,
                        }
                    )
                sample_out = pd.DataFrame(sample_rows)
                month_summary = (
                    sample_out.groupby("month")
                    .agg(
                        outcomes=("event_slug", "count"),
                        with_points=("has_points", "sum"),
                        with_both=("has_both", "sum"),
                    )
                    .reset_index()
                )
                history_sample_summary = {
                    "sample_outcomes": int(len(sample_out)),
                    "sample_events": int(sample_out["event_slug"].nunique()),
                    "outcomes_with_points": int(sample_out["has_points"].sum()),
                    "outcomes_with_pre_and_ingame": int(sample_out["has_both"].sum()),
                    "by_month": month_summary.to_dict(orient="records"),
                    "schedule_anchor_required": True,
                }

    lakers_stats = dict(team_stats.get("LAL") or {})
    hornets_stats = dict(team_stats.get("CHA") or {})

    return {
        "season": season,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schedule": {
            "games_total": int(len(schedule_df)),
            "finished_games_total": int(len(finished_df)),
        },
        "play_by_play": {
            "games_checked": int(len(pbp_results)),
            "games_with_pbp": int(len(games_with_pbp)),
            "coverage_pct": round((len(games_with_pbp) / max(len(pbp_results), 1)) * 100.0, 2),
            "avg_rows_per_game": round(sum(item.row_count for item in games_with_pbp) / max(len(games_with_pbp), 1), 2),
            "top_avg_lead_changes": _top_team_rows(team_stats, "avg_lead_changes"),
            "top_avg_losing_segments": _top_team_rows(team_stats, "avg_losing_segments"),
            "top_avg_largest_lead_in_losses": _top_team_rows(team_stats, "avg_largest_lead_in_losses"),
            "focus_teams": {
                "LAL": lakers_stats,
                "CHA": hornets_stats,
            },
        },
        "moneyline": {
            "unique_event_slugs": int(len(moneyline_event_slugs)),
            "covered_finished_games": covered_finished_games,
            "finished_game_coverage_pct": round((covered_finished_games / max(len(finished_df), 1)) * 100.0, 2),
            "history_sample": history_sample_summary,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Season-wide NBA strategy data feasibility audit.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--pbp-max-workers", type=int, default=6)
    parser.add_argument("--pbp-game-limit", type=int, default=None)
    parser.add_argument("--moneyline-window-days", type=int, default=14)
    parser.add_argument("--moneyline-page-size", type=int, default=100)
    parser.add_argument("--moneyline-max-pages", type=int, default=30)
    parser.add_argument("--history-sample-events-per-month", type=int, default=3)
    args = parser.parse_args()

    summary = build_season_strategy_audit(
        season=args.season,
        pbp_max_workers=args.pbp_max_workers,
        pbp_game_limit=args.pbp_game_limit,
        moneyline_window_days=args.moneyline_window_days,
        moneyline_page_size=args.moneyline_page_size,
        moneyline_max_pages=args.moneyline_max_pages,
        history_sample_events_per_month=args.history_sample_events_per_month,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
