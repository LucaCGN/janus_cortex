from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    AnalysisMartBuildRequest,
    BacktestRunRequest,
    ModelRunRequest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline NBA odds analysis mart, reports, backtests, and baselines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mart_parser = subparsers.add_parser("build_analysis_mart")
    mart_parser.add_argument("--season", default=DEFAULT_SEASON)
    mart_parser.add_argument("--season-phase", default=DEFAULT_SEASON_PHASE)
    mart_parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    mart_parser.add_argument("--rebuild", action="store_true")
    mart_parser.add_argument("--game-id", dest="game_ids", action="append")
    mart_parser.add_argument("--output-root", default=None)

    report_parser = subparsers.add_parser("build_analysis_report")
    report_parser.add_argument("--season", default=DEFAULT_SEASON)
    report_parser.add_argument("--season-phase", default=DEFAULT_SEASON_PHASE)
    report_parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    report_parser.add_argument("--output-root", default=None)

    backtest_parser = subparsers.add_parser("run_analysis_backtests")
    backtest_parser.add_argument("--season", default=DEFAULT_SEASON)
    backtest_parser.add_argument("--season-phase", default=DEFAULT_SEASON_PHASE)
    backtest_parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    backtest_parser.add_argument("--strategy-family", default="all")
    backtest_parser.add_argument("--entry-rule", default=None)
    backtest_parser.add_argument("--exit-rule", default=None)
    backtest_parser.add_argument("--slippage-cents", type=int, default=0)
    backtest_parser.add_argument("--output-root", default=None)

    model_parser = subparsers.add_parser("train_analysis_baselines")
    model_parser.add_argument("--season", default=DEFAULT_SEASON)
    model_parser.add_argument("--season-phase", default=DEFAULT_SEASON_PHASE)
    model_parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    model_parser.add_argument("--target-family", default="all")
    model_parser.add_argument("--train-cutoff", default=None)
    model_parser.add_argument("--validation-window", default=None)
    model_parser.add_argument("--feature-set-version", default=ANALYSIS_VERSION)
    model_parser.add_argument("--output-root", default=None)
    return parser


def dispatch_command(
    args: argparse.Namespace,
    *,
    build_analysis_mart: Callable[[AnalysisMartBuildRequest], dict[str, Any]],
    build_analysis_report: Callable[..., dict[str, Any]],
    run_analysis_backtests: Callable[[BacktestRunRequest], dict[str, Any]],
    train_analysis_baselines: Callable[[ModelRunRequest], dict[str, Any]],
) -> dict[str, Any]:
    if args.command == "build_analysis_mart":
        return build_analysis_mart(
            AnalysisMartBuildRequest(
                season=args.season,
                season_phase=args.season_phase,
                rebuild=bool(args.rebuild),
                game_ids=args.game_ids,
                analysis_version=args.analysis_version,
                output_root=args.output_root,
            )
        )
    if args.command == "build_analysis_report":
        return build_analysis_report(
            season=args.season,
            season_phase=args.season_phase,
            analysis_version=args.analysis_version,
            output_root=args.output_root,
        )
    if args.command == "run_analysis_backtests":
        return run_analysis_backtests(
            BacktestRunRequest(
                season=args.season,
                season_phase=args.season_phase,
                strategy_family=args.strategy_family,
                entry_rule=args.entry_rule,
                exit_rule=args.exit_rule,
                slippage_cents=args.slippage_cents,
                analysis_version=args.analysis_version,
                output_root=args.output_root,
            )
        )
    return train_analysis_baselines(
        ModelRunRequest(
            season=args.season,
            season_phase=args.season_phase,
            target_family=args.target_family,
            train_cutoff=args.train_cutoff,
            validation_window=args.validation_window,
            feature_set_version=args.feature_set_version,
            analysis_version=args.analysis_version,
            output_root=args.output_root,
        )
    )


def run_cli(
    *,
    build_analysis_mart: Callable[[AnalysisMartBuildRequest], dict[str, Any]],
    build_analysis_report: Callable[..., dict[str, Any]],
    run_analysis_backtests: Callable[[BacktestRunRequest], dict[str, Any]],
    train_analysis_baselines: Callable[[ModelRunRequest], dict[str, Any]],
) -> int:
    parser = build_parser()
    args = parser.parse_args()
    summary = dispatch_command(
        args,
        build_analysis_mart=build_analysis_mart,
        build_analysis_report=build_analysis_report,
        run_analysis_backtests=run_analysis_backtests,
        train_analysis_baselines=train_analysis_baselines,
    )
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0
