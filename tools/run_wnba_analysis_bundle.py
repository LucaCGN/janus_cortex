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

from app.data.nodes.wnba.balldontlie.client import describe_historical_backfill_readiness
from app.data.nodes.wnba.live.live_stats import fetch_boxscore_payload, normalize_boxscore_payload
from app.data.nodes.wnba.live.play_by_play import fetch_play_by_play_payload, normalize_play_by_play_payload
from app.data.nodes.wnba.schedule.season_schedule import fetch_season_schedule_payload, normalize_schedule_payload
from app.data.pipelines.daily.wnba.analysis.backtests import run_shadow_backtests_for_lanes
from app.data.pipelines.daily.wnba.analysis.contracts import WNBA_DEFAULT_SEASON_PHASE
from app.data.pipelines.daily.wnba.analysis.data_sufficiency import (
    WnbaDataCounts,
    evaluate_wnba_data_sufficiency,
)
from app.data.pipelines.daily.wnba.analysis.deterministic_lanes import build_wnba_lane_signal_rows
from app.data.pipelines.daily.wnba.analysis.integration_readiness import evaluate_wnba_integration_readiness
from app.data.pipelines.daily.wnba.analysis.ml_dataset import build_wnba_pbp_ml_feature_rows
from app.data.pipelines.daily.wnba.analysis.ml_model import train_wnba_short_horizon_reprice_model
from app.data.pipelines.daily.wnba.analysis.state_panel import build_wnba_state_panel


FIXTURE_PATH = REPO_ROOT / "tests" / "app" / "data" / "nodes" / "wnba" / "fixtures" / "wnba_cdn_samples.json"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _load_fixture_payloads() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    samples = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return samples["schedule"], samples["boxscore"], samples["play_by_play"]


def _select_sample_game_id(games_df: Any, requested_game_id: str | None) -> str | None:
    if requested_game_id:
        return requested_game_id
    if games_df.empty:
        return None
    final_games = games_df[games_df["game_status"] == 3]
    selected = final_games if not final_games.empty else games_df
    return str(selected.iloc[0]["game_id"])


def build_wnba_analysis_bundle(
    *,
    season: str,
    sample_game_id: str | None = None,
    use_fixture: bool = False,
) -> dict[str, Any]:
    if use_fixture:
        schedule_payload, boxscore_payload, pbp_payload = _load_fixture_payloads()
    else:
        schedule_payload = fetch_season_schedule_payload()
        boxscore_payload = None
        pbp_payload = None

    games_df, _teams_df = normalize_schedule_payload(schedule_payload, season=season)
    resolved_game_id = _select_sample_game_id(games_df, sample_game_id)

    games_with_boxscore = 0
    games_with_pbp = 0
    player_boxscore_rows = 0
    pbp_rows = 0
    state_panel_rows = 0
    ml_feature_rows = 0
    labeled_ml_feature_rows = 0
    distinct_ml_games = 0
    lane_signal_df = None
    backtest_bundle: dict[str, Any] = {}
    ml_result: dict[str, Any] = {}
    state_panel_df = None
    feature_df = None

    if resolved_game_id:
        if boxscore_payload is None:
            boxscore_payload = fetch_boxscore_payload(resolved_game_id)
        boxscore_frames = normalize_boxscore_payload(boxscore_payload)
        games_with_boxscore = 1 if boxscore_frames.snapshot else 0
        player_boxscore_rows = int(len(boxscore_frames.players))

        if pbp_payload is None:
            pbp_payload = fetch_play_by_play_payload(resolved_game_id)
        pbp_df = normalize_play_by_play_payload(pbp_payload, game_id=resolved_game_id)
        games_with_pbp = 1 if not pbp_df.empty else 0
        pbp_rows = int(len(pbp_df))

        game_row = games_df[games_df["game_id"].astype(str) == str(resolved_game_id)]
        if not pbp_df.empty and not game_row.empty:
            state_panel_df = build_wnba_state_panel(pbp_df, game=game_row.iloc[0].to_dict())
            feature_df = build_wnba_pbp_ml_feature_rows(state_panel_df)
            lane_signal_df = build_wnba_lane_signal_rows(state_panel_df, include_no_signal=True)
            backtest_bundle = run_shadow_backtests_for_lanes(
                state_panel_df,
                season=season,
                season_phase=WNBA_DEFAULT_SEASON_PHASE,
            )
            ml_result = train_wnba_short_horizon_reprice_model(feature_df)
            state_panel_rows = int(len(state_panel_df))
            ml_feature_rows = int(len(feature_df))
            labeled_ml_feature_rows = int((feature_df["label_status"] == "labeled").sum()) if not feature_df.empty else 0
            distinct_ml_games = (
                int(feature_df[feature_df["label_status"] == "labeled"]["game_id"].nunique())
                if labeled_ml_feature_rows
                else 0
            )

    counts = WnbaDataCounts(
        season=season,
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
    data_audit = evaluate_wnba_data_sufficiency(counts)
    historical_backfill = describe_historical_backfill_readiness(
        season=str(int(season) - 1) if str(season).isdigit() else "last"
    )
    empty_state_panel = pd.DataFrame()
    empty_features = pd.DataFrame()
    lane_signal_df = lane_signal_df if lane_signal_df is not None else build_wnba_lane_signal_rows(empty_state_panel)
    if not backtest_bundle:
        backtest_bundle = run_shadow_backtests_for_lanes(state_panel_df if state_panel_df is not None else empty_state_panel)
    if not ml_result:
        ml_result = train_wnba_short_horizon_reprice_model(feature_df if feature_df is not None else empty_features)
    integration = evaluate_wnba_integration_readiness(
        data_audit=data_audit,
        lane_signal_df=lane_signal_df,
        backtest_bundle=backtest_bundle,
        ml_training_result=ml_result,
        historical_backfill=historical_backfill,
    )

    return {
        "season": season,
        "sample_game_id": resolved_game_id,
        "source_mode": "fixture" if use_fixture else "wnba_cdn",
        "data_audit": data_audit,
        "lane_signal_summary": {
            "rows": int(len(lane_signal_df)),
            "entry_candidates": int((lane_signal_df["signal_status"] == "entry_candidate").sum()) if not lane_signal_df.empty else 0,
            "blocked": int((lane_signal_df["signal_status"] == "blocked").sum()) if not lane_signal_df.empty else 0,
        },
        "backtests": {
            key: value
            for key, value in backtest_bundle.items()
            if key != "families"
        },
        "ml_training": ml_result,
        "historical_backfill": historical_backfill,
        "integration_readiness": integration,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WNBA structural analysis, lane, backtest, and ML readiness bundle.")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--sample-game-id", default=None)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    payload = build_wnba_analysis_bundle(
        season=args.season,
        sample_game_id=args.sample_game_id,
        use_fixture=args.fixture,
    )
    if args.json:
        print(json.dumps(payload, default=_jsonable, indent=2, sort_keys=True))
    else:
        readiness = payload["integration_readiness"]
        audit = payload["data_audit"]
        print(f"season={payload['season']}")
        print(f"sample_game_id={payload['sample_game_id']}")
        print(f"source_mode={payload['source_mode']}")
        print(f"data_status={audit['status']}")
        print(f"integration_status={readiness['status']}")
        print(f"passive_shadow_ready={readiness['passive_shadow_ready']}")
        print(f"calibrated_backtesting_ready={readiness['calibrated_backtesting_ready']}")
        print(f"lane_signal_rows={payload['lane_signal_summary']['rows']}")
        print(f"lane_entry_candidates={payload['lane_signal_summary']['entry_candidates']}")
        print(f"lane_blocked_signals={payload['lane_signal_summary']['blocked']}")
        print(f"backtest_status={payload['backtests']['status']} blockers={','.join(payload['backtests']['blockers'])}")
        print(f"ml_status={payload['ml_training']['status']} blockers={','.join(payload['ml_training']['blockers'])}")
        print(f"historical_backfill_status={payload['historical_backfill']['status']}")
        print(f"verdict={readiness['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
