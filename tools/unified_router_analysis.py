from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.backtests.engine import (
    build_backtest_result,
    load_analysis_backtest_state_panel_df,
)
from app.data.pipelines.daily.nba.analysis.backtests.llm_experiment import (
    _LLMBudgetState,
    _build_family_profiles,
    _build_game_candidates,
    _load_llm_cache,
    _resolve_openai_client,
    _run_llm_lane_sample,
    build_team_profile_context_lookup,
)
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    build_master_router_selection_priors,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    PORTFOLIO_SCOPE_ROUTED,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.backtests.unified_router import (
    build_unified_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.contracts import BacktestRunRequest


STARTING_BANKROLL = 10.0
POSITION_SIZE_FRACTION = 0.20
TARGET_EXPOSURE_FRACTION = 0.80
RANDOM_SLIPPAGE_MAX_CENTS = 5
BASE_RANDOM_SLIPPAGE_SEED = 20260422
MAX_CONCURRENT_POSITIONS = 5
CONCURRENCY_MODE = "shared_cash_equal_split"
SIZING_MODE = "dynamic_concurrent_games"
MIN_ORDER_DOLLARS = 1.0
MIN_SHARES = 5.0
CORE_FAMILIES = tuple(DEFAULT_MASTER_ROUTER_CORE_FAMILIES)
EXTRA_FAMILIES = tuple(DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES)
POSTSEASON_SLIPPAGE_SEEDS = tuple(BASE_RANDOM_SLIPPAGE_SEED + offset for offset in range(6))

MASTER_VARIANT_NAME = "master_strategy_router_same_side_top1_conf60_v1"
MASTER_ROUTER_KWARGS = {
    "extra_selection_mode": "same_side_top1",
    "min_core_confidence_for_extras": 0.60,
}
LLM_LANE = {
    "variant_name": "gpt-5.4-mini :: llm_hybrid_freedom_compact_v1 :: improved_postseason_context",
    "lane_name": "llm_hybrid_freedom_compact_postseason_context_v1",
    "lane_group": "llm_finalist",
    "lane_mode": "llm_freedom",
    "llm_component_scope": "bc_freedom",
    "allowed_roles": ("core", "extra"),
    "prompt_profile": "compact",
    "reasoning_effort": "low",
    "include_rationale": False,
    "use_confidence_gate": False,
}
UNIFIED_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "variant_name": "unified_router_llm_meta_conf60_llm60_v1",
        "weak_confidence_threshold": 0.60,
        "llm_accept_confidence": 0.60,
        "skip_weak_when_llm_empty": False,
        "skip_weak_when_llm_low_confidence": False,
    },
    {
        "variant_name": "unified_router_llm_meta_conf60_llm60_skip_v1",
        "weak_confidence_threshold": 0.60,
        "llm_accept_confidence": 0.60,
        "skip_weak_when_llm_empty": True,
        "skip_weak_when_llm_low_confidence": True,
    },
    {
        "variant_name": "unified_router_llm_meta_conf55_llm60_v1",
        "weak_confidence_threshold": 0.55,
        "llm_accept_confidence": 0.60,
        "skip_weak_when_llm_empty": False,
        "skip_weak_when_llm_low_confidence": False,
    },
    {
        "variant_name": "unified_router_llm_meta_conf60_llm55_v1",
        "weak_confidence_threshold": 0.60,
        "llm_accept_confidence": 0.55,
        "skip_weak_when_llm_empty": False,
        "skip_weak_when_llm_low_confidence": False,
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a unified deterministic-plus-LLM router on the postseason reference.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument(
        "--output-dir",
        default=r"C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_unified_router",
    )
    parser.add_argument("--llm-model", default="gpt-5.4-mini")
    parser.add_argument("--llm-budget-usd", type=float, default=10.0)
    return parser.parse_args()


def _query_df(connection: Any, query: str, params: tuple[Any, ...]) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


def _load_team_profiles_df(connection: Any, *, season: str, analysis_version: str) -> pd.DataFrame:
    return _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_team_season_profiles
        WHERE season = %s AND season_phase = 'regular_season' AND analysis_version = %s
        ORDER BY team_slug ASC;
        """,
        (season, analysis_version),
    )


def _make_request(
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    output_root: str,
    llm_model: str,
    llm_max_budget_usd: float,
    season_phases: tuple[str, ...] | None = None,
) -> BacktestRunRequest:
    return BacktestRunRequest(
        season=season,
        season_phase=season_phase,
        season_phases=season_phases,
        strategy_family="all",
        slippage_cents=0,
        portfolio_initial_bankroll=STARTING_BANKROLL,
        portfolio_position_size_fraction=POSITION_SIZE_FRACTION,
        portfolio_game_limit=None,
        portfolio_min_order_dollars=MIN_ORDER_DOLLARS,
        portfolio_min_shares=MIN_SHARES,
        portfolio_max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
        portfolio_concurrency_mode=CONCURRENCY_MODE,
        portfolio_sizing_mode=SIZING_MODE,
        portfolio_target_exposure_fraction=TARGET_EXPOSURE_FRACTION,
        portfolio_random_slippage_max_cents=RANDOM_SLIPPAGE_MAX_CENTS,
        portfolio_random_slippage_seed=BASE_RANDOM_SLIPPAGE_SEED,
        llm_enable=True,
        llm_model=llm_model,
        llm_max_budget_usd=llm_max_budget_usd,
        output_root=output_root,
        analysis_version=analysis_version,
    )


def _load_result(request: BacktestRunRequest) -> Any:
    with managed_connection() as connection:
        state_df = load_analysis_backtest_state_panel_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            season_phases=request.season_phases,
            analysis_version=request.analysis_version,
        )
    return build_backtest_result(state_df, request)


def _simulate(
    trades_df: pd.DataFrame,
    *,
    sample_name: str,
    strategy_family: str,
    request: BacktestRunRequest,
    random_slippage_seed: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    return simulate_trade_portfolio(
        trades_df,
        sample_name=sample_name,
        strategy_family=strategy_family,
        portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=tuple([*CORE_FAMILIES, *EXTRA_FAMILIES]),
        initial_bankroll=request.portfolio_initial_bankroll,
        position_size_fraction=request.portfolio_position_size_fraction,
        game_limit=request.portfolio_game_limit,
        min_order_dollars=request.portfolio_min_order_dollars,
        min_shares=request.portfolio_min_shares,
        max_concurrent_positions=request.portfolio_max_concurrent_positions,
        concurrency_mode=request.portfolio_concurrency_mode,
        sizing_mode=request.portfolio_sizing_mode,
        target_exposure_fraction=request.portfolio_target_exposure_fraction,
        random_slippage_max_cents=request.portfolio_random_slippage_max_cents,
        random_slippage_seed=random_slippage_seed,
    )


def _summary_row(
    *,
    variant_name: str,
    slippage_seed: int,
    summary: dict[str, Any],
    llm_cost_usd: float = 0.0,
    llm_call_count: int = 0,
) -> dict[str, Any]:
    return {
        "variant_name": variant_name,
        "slippage_seed": int(slippage_seed),
        "ending_bankroll": float(summary.get("ending_bankroll") or 0.0),
        "compounded_return": float(summary.get("compounded_return") or 0.0),
        "max_drawdown_pct": float(summary.get("max_drawdown_pct") or 0.0),
        "max_drawdown_amount": float(summary.get("max_drawdown_amount") or 0.0),
        "min_bankroll": float(summary.get("min_bankroll") or 0.0),
        "peak_bankroll": float(summary.get("peak_bankroll") or 0.0),
        "executed_trade_count": int(summary.get("executed_trade_count") or 0),
        "llm_estimated_cost_usd": float(llm_cost_usd),
        "llm_call_count": int(llm_call_count),
    }


def _aggregate(summary_df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        summary_df.groupby("variant_name", dropna=False)
        .agg(
            slippage_seed_count=("slippage_seed", "count"),
            mean_ending_bankroll=("ending_bankroll", "mean"),
            median_ending_bankroll=("ending_bankroll", "median"),
            min_ending_bankroll=("ending_bankroll", "min"),
            max_ending_bankroll=("ending_bankroll", "max"),
            mean_compounded_return=("compounded_return", "mean"),
            mean_max_drawdown_pct=("max_drawdown_pct", "mean"),
            mean_max_drawdown_amount=("max_drawdown_amount", "mean"),
            mean_min_bankroll=("min_bankroll", "mean"),
            mean_trade_count=("executed_trade_count", "mean"),
            total_llm_estimated_cost_usd=("llm_estimated_cost_usd", "sum"),
            total_llm_call_count=("llm_call_count", "sum"),
        )
        .reset_index()
    )
    grouped["tradeoff_score"] = (
        grouped["median_ending_bankroll"]
        * (1.0 - grouped["mean_max_drawdown_pct"].clip(lower=0.0, upper=0.999999))
    )
    return grouped.sort_values(
        ["tradeoff_score", "mean_ending_bankroll"],
        ascending=[False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def _write_report(output_dir: Path, aggregate_df: pd.DataFrame) -> None:
    lines = [
        "# Unified router analysis",
        "",
        "Postseason reference: 6 play-in + 14 playoff games, dynamic 80% exposure, shared concurrency, 0-5c adverse slippage across 6 seeds.",
        "",
        "## Ranked variants",
        "",
    ]
    for record in aggregate_df.to_dict(orient="records"):
        lines.extend(
            [
                f"### {record['variant_name']}",
                f"- mean ending bankroll: `${record['mean_ending_bankroll']:.2f}`",
                f"- median ending bankroll: `${record['median_ending_bankroll']:.2f}`",
                f"- mean max drawdown: `{record['mean_max_drawdown_pct']:.2%}` / `${record['mean_max_drawdown_amount']:.2f}`",
                f"- mean minimum bankroll: `${record['mean_min_bankroll']:.2f}`",
                f"- mean trade count: `{record['mean_trade_count']:.2f}`",
                f"- total LLM cost: `${record['total_llm_estimated_cost_usd']:.4f}` across `{int(record['total_llm_call_count'])}` calls",
                "",
            ]
        )
    (output_dir / "unified_router_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve() / args.season / "postseason_unified_router"
    output_dir.mkdir(parents=True, exist_ok=True)

    regular_request = _make_request(
        season=args.season,
        season_phase="regular_season",
        analysis_version=args.analysis_version,
        output_root=str(output_dir),
        llm_model=args.llm_model,
        llm_max_budget_usd=args.llm_budget_usd,
    )
    postseason_request = _make_request(
        season=args.season,
        season_phase="postseason_final_20",
        season_phases=("play_in", "playoffs"),
        analysis_version=args.analysis_version,
        output_root=str(output_dir),
        llm_model=args.llm_model,
        llm_max_budget_usd=args.llm_budget_usd,
    )

    with managed_connection() as connection:
        team_profiles_df = _load_team_profiles_df(
            connection,
            season=args.season,
            analysis_version=args.analysis_version,
        )

    regular_result = _load_result(regular_request)
    postseason_result = _load_result(postseason_request)
    priors = build_master_router_selection_priors(regular_result, core_strategy_families=CORE_FAMILIES)
    family_profiles = _build_family_profiles(
        regular_result,
        registry=regular_result.strategy_registry,
        strategy_families=tuple(dict.fromkeys([*CORE_FAMILIES, *EXTRA_FAMILIES])),
        core_strategy_families=CORE_FAMILIES,
    )
    historical_team_context_lookup = build_team_profile_context_lookup(team_profiles_df)

    client = _resolve_openai_client()
    cache_store = _load_llm_cache(output_dir / "llm_router_cache.json")
    budget_state = _LLMBudgetState()
    summary_rows: list[dict[str, Any]] = []

    for slippage_seed in POSTSEASON_SLIPPAGE_SEEDS:
        master_trades_df, _ = build_master_router_trade_frame(
            postseason_result,
            sample_name="postseason_final_20",
            selection_sample_name="regular_full",
            priors=priors,
            core_strategy_families=CORE_FAMILIES,
            extra_strategy_families=EXTRA_FAMILIES,
            **MASTER_ROUTER_KWARGS,
        )
        master_summary, _ = _simulate(
            master_trades_df,
            sample_name="postseason_final_20",
            strategy_family=MASTER_VARIANT_NAME,
            request=postseason_request,
            random_slippage_seed=slippage_seed,
        )
        summary_rows.append(_summary_row(variant_name=MASTER_VARIANT_NAME, slippage_seed=slippage_seed, summary=master_summary))

    # The full LLM/unified loop is assembled below to avoid rebuilding candidates per seed.
    game_candidates = _build_game_candidates(
        postseason_result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        historical_team_context_lookup=historical_team_context_lookup,
    )
    sampled_game_ids = tuple(postseason_result.state_df["game_id"].drop_duplicates().astype(str).tolist())

    # Re-run the base comparisons cleanly now that candidates are built once.
    summary_rows = [row for row in summary_rows if row["variant_name"] == MASTER_VARIANT_NAME]
    for slippage_seed in POSTSEASON_SLIPPAGE_SEEDS:
        llm_trades_df, llm_token_totals, _ = _run_llm_lane_sample(
            iteration_index=0,
            iteration_seed=slippage_seed,
            sample_name="postseason_final_20",
            sampled_game_ids=sampled_game_ids,
            lane=LLM_LANE,
            game_candidates=game_candidates,
            family_profiles=family_profiles,
            request=postseason_request,
            client=client,
            budget_state=budget_state,
            cache_store=cache_store,
        )
        llm_summary, _ = _simulate(
            llm_trades_df,
            sample_name="postseason_final_20",
            strategy_family=str(LLM_LANE["variant_name"]),
            request=postseason_request,
            random_slippage_seed=slippage_seed,
        )
        summary_rows.append(
            _summary_row(
                variant_name=str(LLM_LANE["variant_name"]),
                slippage_seed=slippage_seed,
                summary=llm_summary,
                llm_cost_usd=float(llm_token_totals.get("llm_estimated_cost_usd") or 0.0),
                llm_call_count=int(llm_token_totals.get("llm_call_count") or 0),
            )
        )

        for variant in UNIFIED_VARIANTS:
            unified_trades_df, _unified_decisions_df, token_totals = build_unified_router_trade_frame(
                postseason_result,
                sample_name="postseason_final_20",
                selection_sample_name="regular_full",
                priors=priors,
                family_profiles=family_profiles,
                core_strategy_families=CORE_FAMILIES,
                extra_strategy_families=EXTRA_FAMILIES,
                llm_lane=LLM_LANE,
                request=postseason_request,
                client=client,
                budget_state=budget_state,
                cache_store=cache_store,
                historical_team_context_lookup=historical_team_context_lookup,
                **MASTER_ROUTER_KWARGS,
                weak_confidence_threshold=float(variant["weak_confidence_threshold"]),
                llm_accept_confidence=float(variant["llm_accept_confidence"]),
                skip_weak_when_llm_empty=bool(variant.get("skip_weak_when_llm_empty", False)),
                skip_weak_when_llm_low_confidence=bool(variant.get("skip_weak_when_llm_low_confidence", False)),
            )
            unified_summary, _ = _simulate(
                unified_trades_df,
                sample_name="postseason_final_20",
                strategy_family=str(variant["variant_name"]),
                request=postseason_request,
                random_slippage_seed=slippage_seed,
            )
            summary_rows.append(
                _summary_row(
                    variant_name=str(variant["variant_name"]),
                    slippage_seed=slippage_seed,
                    summary=unified_summary,
                    llm_cost_usd=float(token_totals.get("llm_estimated_cost_usd") or 0.0),
                    llm_call_count=int(token_totals.get("llm_call_count") or 0),
                )
            )

    summary_df = pd.DataFrame(summary_rows)
    aggregate_df = _aggregate(summary_df)
    summary_df.to_csv(output_dir / "unified_router_seed_summary.csv", index=False)
    aggregate_df.to_csv(output_dir / "unified_router_aggregate_summary.csv", index=False)
    _write_report(output_dir, aggregate_df)
    metadata = {
        "season": args.season,
        "analysis_version": args.analysis_version,
        "llm_model": args.llm_model,
        "llm_budget_usd": float(args.llm_budget_usd),
        "postseason_slippage_seeds": list(POSTSEASON_SLIPPAGE_SEEDS),
        "output_dir": str(output_dir),
        "total_llm_spend_usd": float(summary_df["llm_estimated_cost_usd"].sum()) if not summary_df.empty else 0.0,
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
