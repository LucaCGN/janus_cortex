from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.engine import build_backtest_result  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.registry import REPLAY_HF_STRATEGY_GROUP  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.replay import load_finished_replay_contexts  # noqa: E402
from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, BacktestRunRequest  # noqa: E402
from app.runtime.local_paths import resolve_shared_root  # noqa: E402


DEFAULT_SHARED_ROOT = resolve_shared_root()
DEFAULT_FAMILIES = ("underdog_range_scalp", "favorite_floor_rebound")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a fast standard backtest artifact for selected strategy families from finished replay contexts."
    )
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--season-phase", default="regular_season")
    parser.add_argument("--season-phase-member", action="append", default=[])
    parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    parser.add_argument("--artifact-name", default="grid_regular_standard_backtest_v1")
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument("--strategy-group", default=REPLAY_HF_STRATEGY_GROUP)
    parser.add_argument("--slippage-cents", type=int, default=1)
    return parser.parse_args()


def _subject_stem(subject_name: str) -> str:
    return str(subject_name).replace(" ", "_").replace("::", "__").replace("/", "_")


def main() -> None:
    args = _parse_args()
    shared_root = Path(args.shared_root).expanduser().resolve()
    output_dir = shared_root / "artifacts" / "replay-engine-hf" / args.season / args.artifact_name
    output_dir.mkdir(parents=True, exist_ok=True)
    season_phases = tuple(
        str(value).strip()
        for value in (args.season_phase_member or [args.season_phase])
        if str(value).strip()
    )
    contexts, state_df, manifest_df = load_finished_replay_contexts(
        season=args.season,
        analysis_version=args.analysis_version,
        season_phase=args.season_phase,
        season_phases=season_phases,
    )
    families = tuple(args.family or DEFAULT_FAMILIES)
    subject_rows: list[dict[str, object]] = []
    for family in families:
        request = BacktestRunRequest(
            season=args.season,
            season_phase=args.season_phase,
            season_phases=season_phases,
            strategy_family=family,
            strategy_group=args.strategy_group,
            slippage_cents=int(args.slippage_cents),
            analysis_version=args.analysis_version,
        )
        result = build_backtest_result(state_df, request)
        frame = result.trade_frames.get(family, pd.DataFrame()).copy()
        frame.to_csv(output_dir / f"standard_{_subject_stem(family)}.csv", index=False)
        if not frame.empty:
            returns = pd.to_numeric(frame["gross_return_with_slippage"], errors="coerce")
            subject_rows.append(
                {
                    "subject_name": family,
                    "subject_type": "family",
                    "standard_trade_count": int(len(frame)),
                    "standard_traded_game_count": int(frame["game_id"].astype(str).nunique()),
                    "standard_avg_return_with_slippage": float(returns.mean()) if not returns.dropna().empty else None,
                    "standard_win_rate": float((returns > 0).mean()) if not returns.dropna().empty else None,
                }
            )
        else:
            subject_rows.append(
                {
                    "subject_name": family,
                    "subject_type": "family",
                    "standard_trade_count": 0,
                    "standard_traded_game_count": 0,
                    "standard_avg_return_with_slippage": None,
                    "standard_win_rate": None,
                }
            )
    manifest_df.to_csv(output_dir / "replay_game_manifest.csv", index=False)
    subject_df = pd.DataFrame(subject_rows)
    subject_df.to_csv(output_dir / "standard_subject_summary.csv", index=False)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": args.season,
        "season_phase": args.season_phase,
        "season_phases": list(season_phases),
        "analysis_version": args.analysis_version,
        "artifact_root": str(output_dir),
        "finished_game_count": int(len(contexts)),
        "state_rows": int(len(state_df)),
        "families": subject_rows,
        "contract": {
            "mode": "standard_state_panel_screen",
            "strategy_group": args.strategy_group,
            "slippage_cents": int(args.slippage_cents),
            "replay_poll_simulation": False,
            "orderbook_reconciliation": False,
        },
    }
    (output_dir / "run_summary.json").write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(to_jsonable(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
