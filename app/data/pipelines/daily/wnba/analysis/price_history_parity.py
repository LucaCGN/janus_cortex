from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.pipelines.daily.wnba.analysis.backtests import run_shadow_backtests_for_lanes
from app.data.pipelines.daily.wnba.analysis.contracts import WNBA_ANALYSIS_VERSION, WnbaLaneSpec
from app.data.pipelines.daily.wnba.analysis.state_panel import build_wnba_state_panel


SCHEMA_VERSION = "wnba_price_history_replay_parity_v1"


def price_history_to_market_points(price_history_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize persisted price-history rows into state-panel market points."""

    if price_history_df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, source in price_history_df.iterrows():
        raw = source.to_dict()
        captured_at = raw.get("captured_at") or raw.get("price_at")
        price = _first_number(raw, "team_price", "mid_price", "price")
        if captured_at in (None, "") or price is None:
            continue
        rows.append(
            {
                "game_id": raw.get("game_id"),
                "team_side": raw.get("team_side"),
                "team_tricode": raw.get("team_tricode"),
                "captured_at": captured_at,
                "mid_price": price,
                "team_price": price,
                "best_bid": _first_number(raw, "best_bid"),
                "best_ask": _first_number(raw, "best_ask"),
                "spread": _first_number(raw, "spread"),
                "token_id": raw.get("token_id"),
                "market_id": raw.get("market_id"),
                "outcome_id": raw.get("outcome_id"),
                "event_slug": raw.get("event_slug"),
                "source": raw.get("source") or "wnba_polymarket_price_history",
            }
        )
    return pd.DataFrame(rows)


def build_wnba_price_history_state_panel(
    pbp_df: pd.DataFrame,
    *,
    game: dict[str, Any],
    price_history_df: pd.DataFrame,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
    computed_at: datetime | None = None,
) -> pd.DataFrame:
    market_df = price_history_to_market_points(price_history_df)
    return build_wnba_state_panel(
        pbp_df,
        game=game,
        market_df=market_df,
        analysis_version=analysis_version,
        computed_at=computed_at,
    )


def build_wnba_price_history_replay_pack(
    *,
    games: list[dict[str, Any]],
    pbp_df: pd.DataFrame,
    price_history_df: pd.DataFrame,
    lanes: tuple[WnbaLaneSpec, ...] | None = None,
    season: str | None = None,
    season_phase: str | None = None,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    """Run the WNBA sleeve replay pack from price-history-backed panels."""

    computed_at = computed_at or datetime.now(timezone.utc)
    event_results: list[dict[str, Any]] = []
    all_panels: list[pd.DataFrame] = []
    for game in games:
        game_id = str(game.get("game_id") or "").strip()
        if not game_id:
            continue
        game_pbp = pbp_df[pbp_df["game_id"].astype(str) == game_id] if "game_id" in pbp_df.columns else pd.DataFrame()
        game_prices = (
            price_history_df[price_history_df["game_id"].astype(str) == game_id]
            if "game_id" in price_history_df.columns
            else pd.DataFrame()
        )
        state_panel = build_wnba_price_history_state_panel(
            game_pbp,
            game=game,
            price_history_df=game_prices,
            analysis_version=analysis_version,
            computed_at=computed_at,
        )
        all_panels.append(state_panel)
        replay = run_shadow_backtests_for_lanes(
            state_panel,
            lanes=lanes,
            season=season,
            season_phase=season_phase or game.get("season_phase"),
            analysis_version=analysis_version,
        )
        event_results.append(
            {
                "game_id": game_id,
                "state_panel_rows": int(len(state_panel)),
                "price_history_rows": int(len(game_prices)),
                "backtest_eligible_rows": int(state_panel["backtest_eligible"].sum()) if "backtest_eligible" in state_panel.columns else 0,
                "replay": replay,
            }
        )
    combined = pd.concat([panel for panel in all_panels if not panel.empty], ignore_index=True) if all_panels else pd.DataFrame()
    blockers = sorted(
        {
            blocker
            for event in event_results
            for blocker in ((event.get("replay") or {}).get("blockers") or [])
        }
    )
    complete_count = sum(1 for event in event_results if (event.get("replay") or {}).get("status") == "shadow_complete")
    status = "ready" if complete_count else "blocked" if blockers else "no_trades"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "analysis_version": analysis_version,
        "season": season,
        "season_phase": season_phase,
        "game_count": len(event_results),
        "state_panel_rows": int(len(combined)),
        "price_history_rows": int(len(price_history_df)),
        "backtest_eligible_rows": int(combined["backtest_eligible"].sum()) if "backtest_eligible" in combined.columns else 0,
        "complete_event_count": complete_count,
        "blockers": blockers,
        "events": event_results,
        "source_confidence": "db_confirmed_or_artifact_confirmed",
        "execution_boundary": "backtest_only",
    }


def state_panel_db_records(state_panel_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return rows shaped for wnba.wnba_market_state_panels upsert code."""

    if state_panel_df.empty:
        return []
    records: list[dict[str, Any]] = []
    for _, row in state_panel_df.iterrows():
        item = row.to_dict()
        records.append(
            {
                "game_id": item.get("game_id"),
                "team_side": item.get("team_side"),
                "state_index": item.get("state_index"),
                "analysis_version": item.get("analysis_version"),
                "computed_at": item.get("computed_at"),
                "event_index": item.get("event_index"),
                "action_id": item.get("action_id"),
                "period": item.get("period"),
                "clock": item.get("clock"),
                "seconds_to_game_end": item.get("seconds_to_game_end"),
                "score_for": item.get("score_for"),
                "score_against": item.get("score_against"),
                "score_diff": item.get("score_diff"),
                "scoring_side": item.get("scoring_side"),
                "points_scored": item.get("points_scored"),
                "player_id": item.get("player_id"),
                "player_name": item.get("player_name"),
                "action_type": item.get("action_type"),
                "team_price": item.get("team_price"),
                "best_bid": item.get("best_bid"),
                "best_ask": item.get("best_ask"),
                "spread": item.get("spread"),
                "mid_price": item.get("mid_price"),
                "liquidity_context_json": {
                    "price_mode": item.get("price_mode"),
                    "market_age_seconds": item.get("market_age_seconds"),
                    "token_id": item.get("token_id"),
                    "market_id": item.get("market_id"),
                    "outcome_id": item.get("outcome_id"),
                },
                "player_context_json": {
                    "substitution_direction": item.get("substitution_direction"),
                    "substitution_person_id": item.get("substitution_person_id"),
                    "substitution_player_name": item.get("substitution_player_name"),
                },
                "raw_state_json": item.get("raw_state_json"),
            }
        )
    return records


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


__all__ = [
    "SCHEMA_VERSION",
    "build_wnba_price_history_replay_pack",
    "build_wnba_price_history_state_panel",
    "price_history_to_market_points",
    "state_panel_db_records",
]
