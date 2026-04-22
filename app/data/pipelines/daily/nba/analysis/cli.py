from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_BACKTEST_HOLDOUT_RATIO,
    DEFAULT_BACKTEST_HOLDOUT_SEED,
    DEFAULT_BACKTEST_MIN_TRADE_COUNT,
    DEFAULT_BACKTEST_LLM_ITERATION_COUNT,
    DEFAULT_BACKTEST_LLM_ITERATION_GAMES,
    DEFAULT_BACKTEST_LLM_MAX_BUDGET_USD,
    DEFAULT_BACKTEST_LLM_MODEL,
    DEFAULT_BACKTEST_PORTFOLIO_CONCURRENCY_MODE,
    DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT,
    DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL,
    DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS,
    DEFAULT_BACKTEST_PORTFOLIO_MIN_ORDER_DOLLARS,
    DEFAULT_BACKTEST_PORTFOLIO_MIN_SHARES,
    DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION,
    DEFAULT_BACKTEST_PORTFOLIO_SIZING_MODE,
    DEFAULT_BACKTEST_PORTFOLIO_TARGET_EXPOSURE_FRACTION,
    DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_MAX_CENTS,
    DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_SEED,
    DEFAULT_BACKTEST_ROBUSTNESS_SEEDS,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    AnalysisMartBuildRequest,
    BacktestRunRequest,
    ModelRunRequest,
)


def _parse_int_csv(value: str | None, *, fallback: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return fallback
    parsed: list[int] = []
    for chunk in str(value).split(","):
        cleaned = chunk.strip()
        if not cleaned:
            continue
        try:
            parsed.append(int(cleaned))
        except ValueError:
            continue
    return tuple(parsed) if parsed else fallback


def _parse_str_csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    parsed: list[str] = []
    for chunk in str(value).split(","):
        cleaned = chunk.strip()
        if not cleaned:
            continue
        parsed.append(cleaned)
    return tuple(parsed)


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
    backtest_parser.add_argument("--season-phases", default=None)
    backtest_parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    backtest_parser.add_argument("--strategy-family", default="all")
    backtest_parser.add_argument("--entry-rule", default=None)
    backtest_parser.add_argument("--exit-rule", default=None)
    backtest_parser.add_argument("--slippage-cents", type=int, default=0)
    backtest_parser.add_argument("--train-cutoff", default=None)
    backtest_parser.add_argument("--holdout-ratio", type=float, default=DEFAULT_BACKTEST_HOLDOUT_RATIO)
    backtest_parser.add_argument("--holdout-seed", type=int, default=DEFAULT_BACKTEST_HOLDOUT_SEED)
    backtest_parser.add_argument(
        "--robustness-seeds",
        default=",".join(str(seed) for seed in DEFAULT_BACKTEST_ROBUSTNESS_SEEDS),
    )
    backtest_parser.add_argument("--min-trade-count", type=int, default=DEFAULT_BACKTEST_MIN_TRADE_COUNT)
    backtest_parser.add_argument("--portfolio-initial-bankroll", type=float, default=DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL)
    backtest_parser.add_argument(
        "--portfolio-position-size-fraction",
        type=float,
        default=DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION,
    )
    backtest_parser.add_argument("--portfolio-game-limit", type=int, default=DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT)
    backtest_parser.add_argument("--portfolio-min-order-dollars", type=float, default=DEFAULT_BACKTEST_PORTFOLIO_MIN_ORDER_DOLLARS)
    backtest_parser.add_argument("--portfolio-min-shares", type=float, default=DEFAULT_BACKTEST_PORTFOLIO_MIN_SHARES)
    backtest_parser.add_argument(
        "--portfolio-max-concurrent-positions",
        type=int,
        default=DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS,
    )
    backtest_parser.add_argument(
        "--portfolio-concurrency-mode",
        default=DEFAULT_BACKTEST_PORTFOLIO_CONCURRENCY_MODE,
    )
    backtest_parser.add_argument(
        "--portfolio-sizing-mode",
        default=DEFAULT_BACKTEST_PORTFOLIO_SIZING_MODE,
    )
    backtest_parser.add_argument(
        "--portfolio-target-exposure-fraction",
        type=float,
        default=DEFAULT_BACKTEST_PORTFOLIO_TARGET_EXPOSURE_FRACTION,
    )
    backtest_parser.add_argument(
        "--portfolio-random-slippage-max-cents",
        type=int,
        default=DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_MAX_CENTS,
    )
    backtest_parser.add_argument(
        "--portfolio-random-slippage-seed",
        type=int,
        default=DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_SEED,
    )
    backtest_parser.add_argument("--llm-enable", action="store_true")
    backtest_parser.add_argument("--llm-model", default=DEFAULT_BACKTEST_LLM_MODEL)
    backtest_parser.add_argument("--llm-compare-models", default=None)
    backtest_parser.add_argument("--llm-iteration-games", type=int, default=DEFAULT_BACKTEST_LLM_ITERATION_GAMES)
    backtest_parser.add_argument("--llm-iteration-count", type=int, default=DEFAULT_BACKTEST_LLM_ITERATION_COUNT)
    backtest_parser.add_argument("--llm-max-budget-usd", type=float, default=DEFAULT_BACKTEST_LLM_MAX_BUDGET_USD)
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
                season_phases=_parse_str_csv(args.season_phases) or None,
                strategy_family=args.strategy_family,
                entry_rule=args.entry_rule,
                exit_rule=args.exit_rule,
                slippage_cents=args.slippage_cents,
                train_cutoff=args.train_cutoff,
                holdout_ratio=args.holdout_ratio,
                holdout_seed=args.holdout_seed,
                robustness_seeds=_parse_int_csv(
                    args.robustness_seeds,
                    fallback=DEFAULT_BACKTEST_ROBUSTNESS_SEEDS,
                ),
                min_trade_count=args.min_trade_count,
                portfolio_initial_bankroll=args.portfolio_initial_bankroll,
                portfolio_position_size_fraction=args.portfolio_position_size_fraction,
                portfolio_game_limit=args.portfolio_game_limit,
                portfolio_min_order_dollars=args.portfolio_min_order_dollars,
                portfolio_min_shares=args.portfolio_min_shares,
                portfolio_max_concurrent_positions=args.portfolio_max_concurrent_positions,
                portfolio_concurrency_mode=args.portfolio_concurrency_mode,
                portfolio_sizing_mode=args.portfolio_sizing_mode,
                portfolio_target_exposure_fraction=args.portfolio_target_exposure_fraction,
                portfolio_random_slippage_max_cents=args.portfolio_random_slippage_max_cents,
                portfolio_random_slippage_seed=args.portfolio_random_slippage_seed,
                llm_enable=bool(args.llm_enable),
                llm_model=args.llm_model,
                llm_compare_models=_parse_str_csv(args.llm_compare_models),
                llm_iteration_games=args.llm_iteration_games,
                llm_iteration_count=args.llm_iteration_count,
                llm_max_budget_usd=args.llm_max_budget_usd,
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
