from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
from app.data.pipelines.daily.wnba.analysis.ml_dataset import build_wnba_pbp_ml_feature_rows
from app.data.pipelines.daily.wnba.analysis.state_panel import build_wnba_state_panel
from app.data.pipelines.daily.nba.analysis.artifacts import write_json
from app.runtime.local_paths import resolve_shared_root


SCHEMA_VERSION = "wnba_polymarket_history_probe_v1"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def write_probe_artifact(payload: dict[str, Any], *, artifact_dir: str | None = None) -> str:
    """Persist a WNBA price-history probe for issue/backlog evidence."""

    day = datetime.now(timezone.utc).date().isoformat()
    root = Path(artifact_dir) if artifact_dir else resolve_shared_root() / "artifacts" / "wnba-price-history-probes" / day
    path = root / f"wnba_price_history_probe_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    artifact_payload = dict(payload)
    artifact_payload["artifact_path"] = str(path)
    return write_json(path, artifact_payload)


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


def _ranked_targets(targets_df: pd.DataFrame, *, max_targets: int) -> pd.DataFrame:
    if targets_df.empty:
        return targets_df
    scored = targets_df.copy()
    scored["_token_count"] = scored[["home_outcome_token_id", "away_outcome_token_id"]].notna().sum(axis=1)
    scored = scored.sort_values(["_token_count", "confidence"], ascending=[False, False], kind="mergesort")
    return scored.head(max(1, int(max_targets))).reset_index(drop=True)


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


def _build_target_backtest(
    *,
    target: pd.Series,
    outcome_rows: pd.DataFrame,
    games_df: pd.DataFrame,
    season: str,
    fidelity: int,
) -> dict[str, Any]:
    game_id_value = str(target["game_id"])
    try:
        price_history_df = _price_history_for_target(target, outcome_rows, fidelity=fidelity)
    except Exception as exc:  # noqa: BLE001 - public provider fetches should not crash the whole batch.
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "blockers": ["polymarket_price_history_fetch_error"],
            "error_text": repr(exc),
            "game_id": game_id_value,
            "event_slug": target.get("polymarket_event_slug"),
            "market_id": target.get("polymarket_market_id"),
            "price_history_rows": 0,
            "state_panel_rows": 0,
            "labeled_ml_rows": 0,
        }
    if price_history_df.empty:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "blockers": ["missing_polymarket_price_history_rows"],
            "game_id": game_id_value,
            "event_slug": target.get("polymarket_event_slug"),
            "market_id": target.get("polymarket_market_id"),
            "price_history_rows": 0,
            "state_panel_rows": 0,
            "labeled_ml_rows": 0,
        }

    pbp_payload = fetch_play_by_play_payload(game_id_value)
    pbp_df = normalize_play_by_play_payload(pbp_payload, game_id=game_id_value)
    game_row = games_df[games_df["game_id"].astype(str) == game_id_value]
    if pbp_df.empty or game_row.empty:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "blockers": ["missing_wnba_pbp_for_matched_closed_event"],
            "game_id": game_id_value,
            "event_slug": target.get("polymarket_event_slug"),
            "market_id": target.get("polymarket_market_id"),
            "price_history_rows": int(len(price_history_df)),
            "state_panel_rows": 0,
            "labeled_ml_rows": 0,
        }

    state_panel_df = build_wnba_state_panel(
        pbp_df,
        game=game_row.iloc[0].to_dict(),
        market_df=price_history_df,
    )
    feature_df = build_wnba_pbp_ml_feature_rows(state_panel_df)
    backtests = run_shadow_backtests_for_lanes(
        state_panel_df,
        season=season,
        season_phase=str(game_row.iloc[0].get("season_phase")),
    )
    lane_results = backtests.get("families") or {}
    shadow_trade_count = sum(int(result.get("trade_count") or 0) for result in lane_results.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "price_history_backtest_complete" if backtests.get("status") != "blocked" else "price_history_backtest_blocked",
        "blockers": list(backtests.get("blockers") or []),
        "game_id": game_id_value,
        "event_slug": target.get("polymarket_event_slug"),
        "market_id": target.get("polymarket_market_id"),
        "price_history_rows": int(len(price_history_df)),
        "play_by_play_rows": int(len(pbp_df)),
        "state_panel_rows": int(len(state_panel_df)),
        "ml_feature_rows": int(len(feature_df)),
        "labeled_ml_rows": int((feature_df["label_status"] == "labeled").sum()) if not feature_df.empty else 0,
        "distinct_labeled_games": int(feature_df[feature_df["label_status"] == "labeled"]["game_id"].nunique()) if not feature_df.empty else 0,
        "shadow_trade_count": shadow_trade_count,
        "backtests": {
            key: value
            for key, value in backtests.items()
            if key != "families"
        },
    }


def run_probe(*, season: str, event_limit: int, game_id: str | None, fidelity: int) -> dict[str, Any]:
    schedule_payload = fetch_season_schedule_payload()
    games_df, _teams_df = normalize_schedule_payload(schedule_payload, season=season)
    events = fetch_closed_wnba_moneyline_events(limit=event_limit)
    outcome_rows = normalize_wnba_moneyline_events(events)
    targets_df = match_wnba_moneyline_markets_to_schedule(outcome_rows, games_df)
    target = _selected_target(targets_df, game_id=game_id)
    if target is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "blockers": ["missing_matched_closed_wnba_moneyline_event"],
            "closed_events": len(events),
            "moneyline_outcome_rows": int(len(outcome_rows)),
            "matched_targets": int(len(targets_df)),
        }

    target_result = _build_target_backtest(
        target=target,
        outcome_rows=outcome_rows,
        games_df=games_df,
        season=season,
        fidelity=fidelity,
    )
    if target_result["status"] == "blocked":
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "blockers": target_result["blockers"],
            "target": target.to_dict(),
            "price_history_rows": target_result["price_history_rows"],
        }

    counts = WnbaDataCounts(
        season=season,
        schedule_games=int(len(games_df)),
        games_with_boxscore=0,
        games_with_play_by_play=1,
        play_by_play_rows=int(target_result.get("play_by_play_rows") or 0),
        market_link_count=1,
        polymarket_price_history_points=int(target_result["price_history_rows"]),
        games_with_polymarket_price_history=1,
        state_panel_rows=int(target_result["state_panel_rows"]),
        ml_feature_rows=int(target_result.get("ml_feature_rows") or 0),
        labeled_ml_feature_rows=int(target_result.get("labeled_ml_rows") or 0),
        distinct_ml_games=int(target_result.get("distinct_labeled_games") or 0),
    )
    audit = evaluate_wnba_data_sufficiency(counts)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": target_result["status"],
        "target": {
            "game_id": target_result["game_id"],
            "event_slug": target.get("polymarket_event_slug"),
            "market_id": target.get("polymarket_market_id"),
            "home_token": target.get("home_outcome_token_id"),
            "away_token": target.get("away_outcome_token_id"),
            "confidence": target.get("confidence"),
        },
        "closed_events": len(events),
        "moneyline_outcome_rows": int(len(outcome_rows)),
        "matched_targets": int(len(targets_df)),
        "price_history_rows": int(target_result["price_history_rows"]),
        "state_panel_rows": int(target_result["state_panel_rows"]),
        "backtests": target_result.get("backtests") or {},
        "ml_readiness": {
            "status": "ready_for_experiment"
            if int(target_result.get("labeled_ml_rows") or 0) >= 5000 and int(target_result.get("distinct_labeled_games") or 0) >= 40
            else "blocked",
            "feature_rows": int(target_result.get("ml_feature_rows") or 0),
            "labeled_rows": int(target_result.get("labeled_ml_rows") or 0),
            "distinct_games": int(target_result.get("distinct_labeled_games") or 0),
            "blockers": [
                blocker
                for blocker, blocked in [
                    ("insufficient_distinct_games_for_wnba_ml", int(target_result.get("distinct_labeled_games") or 0) < 40),
                    ("insufficient_labeled_rows_for_wnba_ml", int(target_result.get("labeled_ml_rows") or 0) < 5000),
                ]
                if blocked
            ],
        },
        "data_audit": audit,
    }


def run_batch_probe(*, season: str, event_limit: int, max_targets: int, fidelity: int) -> dict[str, Any]:
    schedule_payload = fetch_season_schedule_payload()
    games_df, _teams_df = normalize_schedule_payload(schedule_payload, season=season)
    events = fetch_closed_wnba_moneyline_events(limit=event_limit)
    outcome_rows = normalize_wnba_moneyline_events(events)
    targets_df = match_wnba_moneyline_markets_to_schedule(outcome_rows, games_df)
    ranked_targets = _ranked_targets(targets_df, max_targets=max_targets)
    if ranked_targets.empty:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "blockers": ["missing_matched_closed_wnba_moneyline_event"],
            "closed_events": len(events),
            "moneyline_outcome_rows": int(len(outcome_rows)),
            "matched_targets": int(len(targets_df)),
            "processed_targets": 0,
        }

    target_results = [
        _build_target_backtest(
            target=target,
            outcome_rows=outcome_rows,
            games_df=games_df,
            season=season,
            fidelity=fidelity,
        )
        for _, target in ranked_targets.iterrows()
    ]
    completed = [result for result in target_results if result["status"] == "price_history_backtest_complete"]
    blockers = sorted({blocker for result in target_results for blocker in result.get("blockers") or []})
    price_history_rows = sum(int(result.get("price_history_rows") or 0) for result in target_results)
    state_panel_rows = sum(int(result.get("state_panel_rows") or 0) for result in target_results)
    ml_feature_rows = sum(int(result.get("ml_feature_rows") or 0) for result in target_results)
    labeled_ml_rows = sum(int(result.get("labeled_ml_rows") or 0) for result in target_results)
    play_by_play_rows = sum(int(result.get("play_by_play_rows") or 0) for result in target_results)
    distinct_games = len({str(result.get("game_id")) for result in target_results if int(result.get("labeled_ml_rows") or 0) > 0})
    trade_count = sum(int(result.get("shadow_trade_count") or 0) for result in target_results)
    counts = WnbaDataCounts(
        season=season,
        schedule_games=int(len(games_df)),
        games_with_boxscore=0,
        games_with_play_by_play=len(completed),
        play_by_play_rows=play_by_play_rows,
        market_link_count=int(len(ranked_targets)),
        polymarket_price_history_points=price_history_rows,
        games_with_polymarket_price_history=len(completed),
        state_panel_rows=state_panel_rows,
        ml_feature_rows=ml_feature_rows,
        labeled_ml_feature_rows=labeled_ml_rows,
        distinct_ml_games=distinct_games,
    )
    audit = evaluate_wnba_data_sufficiency(counts)
    ml_readiness = {
        "status": "ready_for_experiment" if labeled_ml_rows >= 5000 and distinct_games >= 40 else "blocked",
        "feature_rows": ml_feature_rows,
        "labeled_rows": labeled_ml_rows,
        "distinct_games": distinct_games,
        "blockers": [
            blocker
            for blocker, blocked in [
                ("insufficient_distinct_games_for_wnba_ml", distinct_games < 40),
                ("insufficient_labeled_rows_for_wnba_ml", labeled_ml_rows < 5000),
            ]
            if blocked
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "batch_price_history_backtest_complete" if completed else "batch_price_history_backtest_blocked",
        "blockers": blockers,
        "closed_events": len(events),
        "moneyline_outcome_rows": int(len(outcome_rows)),
        "matched_targets": int(len(targets_df)),
        "processed_targets": int(len(target_results)),
        "completed_targets": int(len(completed)),
        "price_history_rows": price_history_rows,
        "state_panel_rows": state_panel_rows,
        "ml_feature_rows": ml_feature_rows,
        "labeled_ml_rows": labeled_ml_rows,
        "distinct_labeled_games": distinct_games,
        "shadow_trade_count": trade_count,
        "target_results": target_results,
        "ml_readiness": ml_readiness,
        "data_audit": audit,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a WNBA closed Polymarket price-history backtest probe.")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--event-limit", type=int, default=25)
    parser.add_argument("--max-targets", type=int, default=1)
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--fidelity", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-artifact", action="store_true")
    parser.add_argument("--artifact-dir", default=None)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.max_targets > 1 and not args.game_id:
        payload = run_batch_probe(
            season=args.season,
            event_limit=args.event_limit,
            max_targets=args.max_targets,
            fidelity=args.fidelity,
        )
    else:
        payload = run_probe(
            season=args.season,
            event_limit=args.event_limit,
            game_id=args.game_id,
            fidelity=args.fidelity,
        )
    if args.write_artifact:
        payload["artifact_path"] = write_probe_artifact(payload, artifact_dir=args.artifact_dir)
    if args.json:
        print(json.dumps(payload, default=_jsonable, indent=2, sort_keys=True))
    else:
        print(f"status={payload['status']}")
        print(f"closed_events={payload.get('closed_events')}")
        print(f"moneyline_outcome_rows={payload.get('moneyline_outcome_rows')}")
        print(f"matched_targets={payload.get('matched_targets')}")
        if "processed_targets" in payload:
            print(f"processed_targets={payload.get('processed_targets')}")
            print(f"completed_targets={payload.get('completed_targets')}")
        target = payload.get("target") or {}
        print(f"game_id={target.get('game_id')}")
        print(f"event_slug={target.get('event_slug')}")
        print(f"price_history_rows={payload.get('price_history_rows')}")
        print(f"state_panel_rows={payload.get('state_panel_rows')}")
        backtests = payload.get("backtests") or {}
        if backtests:
            print(f"backtest_status={backtests.get('status')} blockers={','.join(backtests.get('blockers') or [])}")
        else:
            print(f"shadow_trade_count={payload.get('shadow_trade_count')}")
        ml = payload.get("ml_readiness") or {}
        print(f"ml_status={ml.get('status')} blockers={','.join(ml.get('blockers') or [])}")
        audit = payload.get("data_audit") or {}
        print(f"data_status={audit.get('status')}")
        print(f"verdict={audit.get('verdict')}")
        if "artifact_path" in payload:
            print(f"artifact_path={payload.get('artifact_path')}")
    return 0 if not str(payload.get("status", "")).endswith("blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
