from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.nodes.wnba.live.play_by_play import fetch_play_by_play_payload, normalize_play_by_play_payload
from app.data.nodes.wnba.polymarket.history import (
    event_time_bounds,
    fetch_closed_wnba_moneyline_events,
    fetch_token_price_history,
    normalize_token_price_history,
    normalize_wnba_moneyline_events,
)
from app.data.nodes.wnba.polymarket.moneyline import match_wnba_moneyline_markets_to_schedule
from app.data.nodes.wnba.schedule.season_schedule import fetch_season_schedule_payload, normalize_schedule_payload
from app.data.pipelines.daily.wnba.analysis.backtests import run_shadow_backtests_for_lanes
from app.data.pipelines.daily.wnba.analysis.data_sufficiency import WnbaDataCounts, evaluate_wnba_data_sufficiency
from app.data.pipelines.daily.wnba.analysis.ml_dataset import build_wnba_pbp_ml_feature_rows, summarize_ml_training_readiness
from app.data.pipelines.daily.wnba.analysis.state_panel import build_wnba_state_panel


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _selected_target(targets_df: pd.DataFrame, *, game_id: str | None = None) -> pd.Series | None:
    if targets_df.empty:
        return None
    work = targets_df
    if game_id:
        filtered = work[work["game_id"].astype(str) == str(game_id)]
        if not filtered.empty:
            work = filtered
    scored = work.copy()
    scored["_token_count"] = scored[["home_outcome_token_id", "away_outcome_token_id"]].notna().sum(axis=1)
    scored = scored.sort_values(["_token_count", "confidence"], ascending=[False, False], kind="mergesort")
    return scored.iloc[0]


def _price_history_for_target(target: pd.Series, outcome_rows: pd.DataFrame, *, fidelity: int) -> pd.DataFrame:
    market_rows = outcome_rows[
        (outcome_rows["market_id"].astype(str) == str(target["polymarket_market_id"]))
        & (outcome_rows["event_slug"].astype(str) == str(target["polymarket_event_slug"]))
    ].copy()
    if market_rows.empty:
        return pd.DataFrame()
    rows: list[pd.DataFrame] = []
    for _, outcome in market_rows.iterrows():
        token_id = str(outcome.get("token_id") or "")
        if not token_id:
            continue
        team_side = None
        team_tricode = None
        if token_id == str(target.get("home_outcome_token_id")):
            team_side = "home"
            team_tricode = (target.get("matching_json") or {}).get("home_team_tricode")
        elif token_id == str(target.get("away_outcome_token_id")):
            team_side = "away"
            team_tricode = (target.get("matching_json") or {}).get("away_team_tricode")
        start_ts, end_ts = event_time_bounds(outcome, pregame_hours=12, postgame_hours=2)
        payload = fetch_token_price_history(token_id, fidelity=fidelity, start_ts=start_ts, end_ts=end_ts)
        frame = normalize_token_price_history(
            payload,
            token_id=token_id,
            game_id=str(target["game_id"]),
            team_side=team_side,
            team_tricode=team_tricode,
            outcome=str(outcome.get("outcome") or ""),
            event_slug=str(target["polymarket_event_slug"]),
            market_id=str(target["polymarket_market_id"]),
        )
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def run_probe(*, season: str, event_limit: int, game_id: str | None, fidelity: int) -> dict[str, Any]:
    schedule_payload = fetch_season_schedule_payload()
    games_df, _teams_df = normalize_schedule_payload(schedule_payload, season=season)
    events = fetch_closed_wnba_moneyline_events(limit=event_limit)
    outcome_rows = normalize_wnba_moneyline_events(events)
    targets_df = match_wnba_moneyline_markets_to_schedule(outcome_rows, games_df)
    target = _selected_target(targets_df, game_id=game_id)
    if target is None:
        return {
            "status": "blocked",
            "blockers": ["missing_matched_closed_wnba_moneyline_event"],
            "closed_events": len(events),
            "moneyline_outcome_rows": int(len(outcome_rows)),
            "matched_targets": int(len(targets_df)),
        }

    price_history_df = _price_history_for_target(target, outcome_rows, fidelity=fidelity)
    if price_history_df.empty:
        return {
            "status": "blocked",
            "blockers": ["missing_polymarket_price_history_rows"],
            "target": target.to_dict(),
        }

    game_id_value = str(target["game_id"])
    pbp_payload = fetch_play_by_play_payload(game_id_value)
    pbp_df = normalize_play_by_play_payload(pbp_payload, game_id=game_id_value)
    game_row = games_df[games_df["game_id"].astype(str) == game_id_value]
    if pbp_df.empty or game_row.empty:
        return {
            "status": "blocked",
            "blockers": ["missing_wnba_pbp_for_matched_closed_event"],
            "target": target.to_dict(),
            "price_history_rows": int(len(price_history_df)),
        }

    state_panel_df = build_wnba_state_panel(
        pbp_df,
        game=game_row.iloc[0].to_dict(),
        market_df=price_history_df,
    )
    feature_df = build_wnba_pbp_ml_feature_rows(state_panel_df)
    backtests = run_shadow_backtests_for_lanes(state_panel_df, season=season, season_phase=str(game_row.iloc[0].get("season_phase")))
    counts = WnbaDataCounts(
        season=season,
        schedule_games=int(len(games_df)),
        games_with_boxscore=0,
        games_with_play_by_play=1,
        play_by_play_rows=int(len(pbp_df)),
        market_link_count=1,
        polymarket_price_history_points=int(len(price_history_df)),
        games_with_polymarket_price_history=1,
        state_panel_rows=int(len(state_panel_df)),
        ml_feature_rows=int(len(feature_df)),
        labeled_ml_feature_rows=int((feature_df["label_status"] == "labeled").sum()) if not feature_df.empty else 0,
        distinct_ml_games=int(feature_df[feature_df["label_status"] == "labeled"]["game_id"].nunique()) if not feature_df.empty else 0,
    )
    audit = evaluate_wnba_data_sufficiency(counts)
    return {
        "status": "price_history_backtest_complete" if backtests.get("status") != "blocked" else "price_history_backtest_blocked",
        "target": {
            "game_id": game_id_value,
            "event_slug": target.get("polymarket_event_slug"),
            "market_id": target.get("polymarket_market_id"),
            "home_token": target.get("home_outcome_token_id"),
            "away_token": target.get("away_outcome_token_id"),
            "confidence": target.get("confidence"),
        },
        "closed_events": len(events),
        "moneyline_outcome_rows": int(len(outcome_rows)),
        "matched_targets": int(len(targets_df)),
        "price_history_rows": int(len(price_history_df)),
        "state_panel_rows": int(len(state_panel_df)),
        "backtests": {
            key: value
            for key, value in backtests.items()
            if key != "families"
        },
        "ml_readiness": summarize_ml_training_readiness(feature_df),
        "data_audit": audit,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a WNBA closed Polymarket price-history backtest probe.")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--event-limit", type=int, default=25)
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--fidelity", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    payload = run_probe(
        season=args.season,
        event_limit=args.event_limit,
        game_id=args.game_id,
        fidelity=args.fidelity,
    )
    if args.json:
        print(json.dumps(payload, default=_jsonable, indent=2, sort_keys=True))
    else:
        print(f"status={payload['status']}")
        print(f"closed_events={payload.get('closed_events')}")
        print(f"moneyline_outcome_rows={payload.get('moneyline_outcome_rows')}")
        print(f"matched_targets={payload.get('matched_targets')}")
        target = payload.get("target") or {}
        print(f"game_id={target.get('game_id')}")
        print(f"event_slug={target.get('event_slug')}")
        print(f"price_history_rows={payload.get('price_history_rows')}")
        print(f"state_panel_rows={payload.get('state_panel_rows')}")
        backtests = payload.get("backtests") or {}
        print(f"backtest_status={backtests.get('status')} blockers={','.join(backtests.get('blockers') or [])}")
        ml = payload.get("ml_readiness") or {}
        print(f"ml_status={ml.get('status')} blockers={','.join(ml.get('blockers') or [])}")
        audit = payload.get("data_audit") or {}
        print(f"data_status={audit.get('status')}")
        print(f"verdict={audit.get('verdict')}")
    return 0 if not str(payload.get("status", "")).endswith("blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
