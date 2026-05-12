from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.nodes.wnba.balldontlie.client import describe_historical_backfill_readiness
from app.data.nodes.wnba.live.live_stats import fetch_boxscore_payload, normalize_boxscore_payload
from app.data.nodes.wnba.live.play_by_play import fetch_play_by_play_payload, normalize_play_by_play_payload
from app.data.nodes.wnba.schedule.season_schedule import fetch_season_schedule_payload, normalize_schedule_payload
from app.data.pipelines.daily.wnba.analysis.data_sufficiency import (
    WnbaDataCounts,
    evaluate_wnba_data_sufficiency,
)
from app.data.pipelines.daily.wnba.analysis.ml_dataset import (
    build_wnba_pbp_ml_feature_rows,
    summarize_ml_training_readiness,
)
from app.data.pipelines.daily.wnba.analysis.state_panel import build_wnba_state_panel


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe WNBA lane/ML/backtest data sufficiency.")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--sample-game-id", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    schedule_payload = fetch_season_schedule_payload()
    games_df, _teams_df = normalize_schedule_payload(schedule_payload, season=args.season)
    sample_game_id = args.sample_game_id
    if sample_game_id is None and not games_df.empty:
        final_games = games_df[games_df["game_status"] == 3]
        sample_game_id = str((final_games if not final_games.empty else games_df).iloc[0]["game_id"])

    games_with_boxscore = 0
    games_with_pbp = 0
    player_boxscore_rows = 0
    pbp_rows = 0
    state_panel_rows = 0
    ml_feature_rows = 0
    labeled_ml_feature_rows = 0
    distinct_ml_games = 0
    sample_state_status = "not_sampled"

    if sample_game_id:
        boxscore_payload = fetch_boxscore_payload(sample_game_id)
        frames = normalize_boxscore_payload(boxscore_payload)
        games_with_boxscore = 1 if frames.snapshot else 0
        player_boxscore_rows = int(len(frames.players))

        pbp_payload = fetch_play_by_play_payload(sample_game_id)
        pbp_df = normalize_play_by_play_payload(pbp_payload, game_id=sample_game_id)
        games_with_pbp = 1 if not pbp_df.empty else 0
        pbp_rows = int(len(pbp_df))
        game_row = games_df[games_df["game_id"].astype(str) == str(sample_game_id)]
        if not pbp_df.empty and not game_row.empty:
            state_panel_df = build_wnba_state_panel(pbp_df, game=game_row.iloc[0].to_dict())
            feature_df = build_wnba_pbp_ml_feature_rows(state_panel_df)
            state_panel_rows = int(len(state_panel_df))
            ml_feature_rows = int(len(feature_df))
            labeled_ml_feature_rows = int((feature_df["label_status"] == "labeled").sum()) if not feature_df.empty else 0
            distinct_ml_games = int(feature_df[feature_df["label_status"] == "labeled"]["game_id"].nunique()) if labeled_ml_feature_rows else 0
            sample_state_status = "state_panel_proxy_only_no_clob" if state_panel_rows else "state_panel_empty"

    counts = WnbaDataCounts(
        season=args.season,
        schedule_games=int(len(games_df)),
        games_with_boxscore=games_with_boxscore,
        games_with_play_by_play=games_with_pbp,
        play_by_play_rows=pbp_rows,
        player_boxscore_rows=player_boxscore_rows,
        market_link_count=0,
        clob_tick_count=0,
        clob_trade_count=0,
        state_panel_rows=state_panel_rows,
        ml_feature_rows=ml_feature_rows,
        labeled_ml_feature_rows=labeled_ml_feature_rows,
        distinct_ml_games=distinct_ml_games,
    )
    audit = evaluate_wnba_data_sufficiency(counts)
    balldontlie = describe_historical_backfill_readiness(season=str(int(args.season) - 1) if args.season.isdigit() else "last")
    ml_summary = summarize_ml_training_readiness(feature_df) if sample_game_id and "feature_df" in locals() else {}
    payload = {
        "audit": audit,
        "sample_game_id": sample_game_id,
        "sample_state_status": sample_state_status,
        "ml_sample_summary": ml_summary,
        "historical_backfill": balldontlie,
    }

    if args.json:
        print(json.dumps(payload, default=_jsonable, indent=2, sort_keys=True))
    else:
        print(f"season={args.season}")
        print(f"sample_game_id={sample_game_id}")
        print(f"verdict={audit['verdict']}")
        print(f"status={audit['status']}")
        print(f"schedule_games={counts.schedule_games}")
        print(f"sample_pbp_rows={counts.play_by_play_rows}")
        print(f"sample_state_panel_rows={counts.state_panel_rows}")
        print(f"sample_ml_feature_rows={counts.ml_feature_rows}")
        print(f"labeled_ml_feature_rows={counts.labeled_ml_feature_rows}")
        for lane in audit["lane_readiness"]:
            print(f"lane={lane['lane_id']} status={lane['status']} blockers={','.join(lane['blockers'])}")
        print(f"ml_status={audit['ml_readiness']['status']} blockers={','.join(audit['ml_readiness']['blockers'])}")
        print(f"historical_backfill_status={balldontlie['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
