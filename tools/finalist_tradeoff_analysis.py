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
    _subset_backtest_result,
    build_llm_iteration_plan,
)
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    MASTER_ROUTER_PORTFOLIO,
    build_master_router_selection_priors,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    PORTFOLIO_SCOPE_ROUTED,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.contracts import BacktestRunRequest


STARTING_BANKROLL = 10.0
POSITION_SIZE_FRACTION = 0.20
TARGET_EXPOSURE_FRACTION = 0.80
RANDOM_SLIPPAGE_MAX_CENTS = 5
RANDOM_SLIPPAGE_SEED = 20260422
MAX_CONCURRENT_POSITIONS = 5
CONCURRENCY_MODE = "shared_cash_equal_split"
SIZING_MODE = "dynamic_concurrent_games"
MIN_ORDER_DOLLARS = 1.0
MIN_SHARES = 5.0
CORE_FAMILIES = tuple(DEFAULT_MASTER_ROUTER_CORE_FAMILIES)
EXTRA_FAMILIES = tuple(DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES)
RELEVANT_FAMILIES = tuple(dict.fromkeys([*CORE_FAMILIES, *EXTRA_FAMILIES]))

TUNING_SAMPLE_SIZES = (10, 20, 50)
TUNING_ITERATIONS = 8
CONFIRMATION_ITERATIONS = 20


MASTER_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "variant_name": MASTER_ROUTER_PORTFOLIO,
        "lane_group": "master_router",
        "router_kwargs": {},
    },
    {
        "variant_name": "master_strategy_router_core_only_v1",
        "lane_group": "master_router",
        "router_kwargs": {
            "extra_selection_mode": "none",
        },
    },
    {
        "variant_name": "master_strategy_router_same_side_extras_v1",
        "lane_group": "master_router",
        "router_kwargs": {
            "extra_selection_mode": "same_side",
        },
    },
    {
        "variant_name": "master_strategy_router_same_side_top1_conf60_v1",
        "lane_group": "master_router",
        "router_kwargs": {
            "extra_selection_mode": "same_side_top1",
            "min_core_confidence_for_extras": 0.60,
        },
    },
    {
        "variant_name": "master_strategy_router_guarded_v1",
        "lane_group": "master_router",
        "router_kwargs": {
            "extra_selection_mode": "same_side_top1",
            "min_selected_core_confidence": 0.58,
            "min_core_confidence_for_extras": 0.58,
        },
    },
)

LLM_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "variant_name": "gpt-5.4-mini :: llm_hybrid_freedom_compact_v1",
        "lane_name": "llm_hybrid_freedom_compact_v1",
        "lane_group": "llm_finalist",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "low",
        "include_rationale": True,
        "use_confidence_gate": False,
    },
    {
        "variant_name": "gpt-5.4-mini :: llm_hybrid_freedom_compact_cap1_v1",
        "lane_name": "llm_hybrid_freedom_compact_cap1_v1",
        "lane_group": "llm_tuned",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": False,
        "max_selected_candidates": 1,
        "max_core_candidates": 1,
        "max_extra_candidates": 0,
    },
    {
        "variant_name": "gpt-5.4-mini :: llm_hybrid_freedom_anchor_cap1_guarded_v1",
        "lane_name": "llm_hybrid_freedom_anchor_cap1_guarded_v1",
        "lane_group": "llm_tuned",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": True,
        "gate_min_top_confidence": 0.62,
        "gate_min_gap": 0.05,
        "max_selected_candidates": 1,
        "max_core_candidates": 1,
        "max_extra_candidates": 0,
    },
    {
        "variant_name": "gpt-5.4-mini :: llm_hybrid_freedom_anchor_cap2_guarded_v1",
        "lane_name": "llm_hybrid_freedom_anchor_cap2_guarded_v1",
        "lane_group": "llm_tuned",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": True,
        "gate_min_top_confidence": 0.62,
        "gate_min_gap": 0.05,
        "max_selected_candidates": 2,
        "max_core_candidates": 1,
        "max_extra_candidates": 1,
        "require_core_for_extra": True,
    },
    {
        "variant_name": "gpt-5.4-mini :: llm_hybrid_freedom_anchor_soft_cap2_v1",
        "lane_name": "llm_hybrid_freedom_anchor_soft_cap2_v1",
        "lane_group": "llm_tuned",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": True,
        "gate_min_top_confidence": 0.58,
        "gate_min_gap": 0.04,
        "max_selected_candidates": 2,
        "max_core_candidates": 1,
        "max_extra_candidates": 1,
        "require_core_for_extra": True,
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune finalist router tradeoffs against drawdown/path spikes.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument(
        "--output-dir",
        default=r"C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_finalist_tradeoff",
    )
    parser.add_argument("--llm-model", default="gpt-5.4-mini")
    parser.add_argument("--llm-budget-usd", type=float, default=9.0)
    parser.add_argument("--tuning-iterations", type=int, default=TUNING_ITERATIONS)
    parser.add_argument("--confirmation-iterations", type=int, default=CONFIRMATION_ITERATIONS)
    return parser.parse_args()


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
        analysis_version=analysis_version,
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
        portfolio_random_slippage_seed=RANDOM_SLIPPAGE_SEED,
        llm_enable=True,
        llm_model=llm_model,
        llm_max_budget_usd=llm_max_budget_usd,
        output_root=output_root,
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


def _run_master_variant(
    *,
    sample_result: Any,
    request: BacktestRunRequest,
    sample_name: str,
    selection_sample_name: str,
    priors: dict[str, dict[str, Any]],
    variant: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    combined_trades_df, decisions_df = build_master_router_trade_frame(
        sample_result,
        sample_name=sample_name,
        selection_sample_name=selection_sample_name,
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        **dict(variant.get("router_kwargs") or {}),
    )
    summary, steps_df = simulate_trade_portfolio(
        combined_trades_df,
        sample_name=sample_name,
        strategy_family=str(variant["variant_name"]),
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
        random_slippage_seed=request.portfolio_random_slippage_seed,
    )
    return summary, steps_df, decisions_df


def _run_llm_variant(
    *,
    sampled_game_ids: tuple[str, ...] | list[str],
    request: BacktestRunRequest,
    sample_name: str,
    lane: dict[str, Any],
    game_candidates: dict[str, list[dict[str, Any]]],
    family_profiles: dict[str, dict[str, Any]],
    client: Any,
    budget_state: _LLMBudgetState,
    cache_store: Any,
    iteration_index: int,
    iteration_seed: int,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    trades_df, token_totals, lane_decisions = _run_llm_lane_sample(
        iteration_index=iteration_index,
        iteration_seed=iteration_seed,
        sample_name=sample_name,
        sampled_game_ids=sampled_game_ids,
        lane=lane,
        game_candidates=game_candidates,
        family_profiles=family_profiles,
        request=request,
        client=client,
        budget_state=budget_state,
        cache_store=cache_store,
    )
    summary, steps_df = simulate_trade_portfolio(
        trades_df,
        sample_name=sample_name,
        strategy_family=str(lane["variant_name"]),
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
        random_slippage_seed=request.portfolio_random_slippage_seed,
    )
    return summary, steps_df, lane_decisions, token_totals


def _summary_record(
    *,
    stage: str,
    sample_size: int,
    iteration_index: int | None,
    iteration_seed: int | None,
    variant_name: str,
    lane_group: str,
    summary: dict[str, Any],
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "stage": stage,
        "sample_size": sample_size,
        "iteration_index": iteration_index,
        "iteration_seed": iteration_seed,
        "variant_name": variant_name,
        "lane_group": lane_group,
        "ending_bankroll": float(summary.get("ending_bankroll") or 0.0),
        "compounded_return": float(summary.get("compounded_return") or 0.0),
        "max_drawdown_pct": float(summary.get("max_drawdown_pct") or 0.0),
        "max_drawdown_amount": float(summary.get("max_drawdown_amount") or 0.0),
        "peak_bankroll": float(summary.get("peak_bankroll") or 0.0),
        "executed_trade_count": int(summary.get("executed_trade_count") or 0),
        "avg_executed_trade_return_with_slippage": float(summary.get("avg_executed_trade_return_with_slippage") or 0.0),
        "max_concurrent_positions_observed": int(summary.get("max_concurrent_positions_observed") or 0),
    }
    record.update(extra_metrics or {})
    return record


def _aggregate_iterations(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    grouped = (
        frame.groupby(["stage", "sample_size", "variant_name", "lane_group"], dropna=False)
        .agg(
            iteration_count=("ending_bankroll", "count"),
            mean_ending_bankroll=("ending_bankroll", "mean"),
            median_ending_bankroll=("ending_bankroll", "median"),
            min_ending_bankroll=("ending_bankroll", "min"),
            max_ending_bankroll=("ending_bankroll", "max"),
            positive_rate=("ending_bankroll", lambda values: float((pd.Series(values) > STARTING_BANKROLL).mean())),
            mean_compounded_return=("compounded_return", "mean"),
            mean_max_drawdown_pct=("max_drawdown_pct", "mean"),
            p90_max_drawdown_pct=("max_drawdown_pct", lambda values: float(pd.Series(values).quantile(0.90))),
            mean_max_drawdown_amount=("max_drawdown_amount", "mean"),
            mean_peak_bankroll=("peak_bankroll", "mean"),
            mean_trade_count=("executed_trade_count", "mean"),
            mean_llm_cost_usd=("llm_estimated_cost_usd", "mean"),
        )
        .reset_index()
    )
    grouped["tradeoff_score"] = (
        grouped["median_ending_bankroll"]
        * (1.0 - grouped["mean_max_drawdown_pct"].clip(lower=0.0, upper=0.999999))
        * grouped["positive_rate"].clip(lower=0.0, upper=1.0)
    )
    return grouped.sort_values(
        ["stage", "sample_size", "tradeoff_score", "mean_ending_bankroll"],
        ascending=[True, True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def _rank_variants(aggregate_df: pd.DataFrame) -> pd.DataFrame:
    if aggregate_df.empty:
        return pd.DataFrame()
    return (
        aggregate_df.groupby(["variant_name", "lane_group"], dropna=False)
        .agg(
            sample_sizes=("sample_size", lambda values: ",".join(str(int(value)) for value in sorted(set(values)))),
            mean_tradeoff_score=("tradeoff_score", "mean"),
            mean_median_ending_bankroll=("median_ending_bankroll", "mean"),
            mean_mean_ending_bankroll=("mean_ending_bankroll", "mean"),
            mean_drawdown_pct=("mean_max_drawdown_pct", "mean"),
            p90_drawdown_pct=("p90_max_drawdown_pct", "mean"),
            mean_drawdown_amount=("mean_max_drawdown_amount", "mean"),
            mean_positive_rate=("positive_rate", "mean"),
            mean_trade_count=("mean_trade_count", "mean"),
            mean_llm_cost_usd=("mean_llm_cost_usd", "mean"),
        )
        .reset_index()
        .sort_values(
            ["lane_group", "mean_tradeoff_score", "mean_median_ending_bankroll"],
            ascending=[True, False, False],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )


def _pick_best_variants(ranked_df: pd.DataFrame) -> tuple[str, str]:
    if ranked_df.empty:
        return MASTER_ROUTER_PORTFOLIO, "gpt-5.4-mini :: llm_hybrid_freedom_compact_v1"
    master_row = ranked_df[ranked_df["lane_group"] == "master_router"].head(1)
    llm_row = ranked_df[ranked_df["lane_group"].str.startswith("llm")].head(1)
    best_master = str(master_row.iloc[0]["variant_name"]) if not master_row.empty else MASTER_ROUTER_PORTFOLIO
    best_llm = str(llm_row.iloc[0]["variant_name"]) if not llm_row.empty else "gpt-5.4-mini :: llm_hybrid_freedom_compact_v1"
    return best_master, best_llm


def _find_master_variant(name: str) -> dict[str, Any]:
    return next(item for item in MASTER_VARIANTS if str(item["variant_name"]) == name)


def _find_llm_variant(name: str) -> dict[str, Any]:
    return next(item for item in LLM_VARIANTS if str(item["variant_name"]) == name)


def _write_report(
    output_dir: Path,
    *,
    ranked_df: pd.DataFrame,
    confirmation_df: pd.DataFrame,
    postseason_df: pd.DataFrame,
    best_master_name: str,
    best_llm_name: str,
    llm_budget_spent: float,
) -> None:
    lines = [
        "# Finalist Tradeoff Analysis",
        "",
        "## Contract",
        "",
        f"- Start bankroll: `{STARTING_BANKROLL}`",
        f"- Base position fraction floor: `{POSITION_SIZE_FRACTION}`",
        f"- Dynamic target exposure fraction: `{TARGET_EXPOSURE_FRACTION}`",
        f"- Max concurrent positions: `{MAX_CONCURRENT_POSITIONS}`",
        f"- Random adverse slippage max cents: `{RANDOM_SLIPPAGE_MAX_CENTS}`",
        "",
        "## Selected Tuned Variants",
        "",
        f"- Best master router variant: `{best_master_name}`",
        f"- Best LLM freedom variant: `{best_llm_name}`",
        f"- Estimated LLM spend during this analysis: `${llm_budget_spent:.4f}`",
        "",
        "## Ranked Tuning Summary",
        "",
    ]
    if ranked_df.empty:
        lines.append("- No ranked tuning rows were produced.")
    else:
        for _, row in ranked_df.iterrows():
            lines.append(
                f"- `{row['variant_name']}` [{row['lane_group']}]"
                f" tradeoff `{row['mean_tradeoff_score']:.4f}`"
                f" | median end `{row['mean_median_ending_bankroll']:.2f}`"
                f" | mean DD `{100.0 * row['mean_drawdown_pct']:.2f}%`"
                f" | mean DD amount `${row['mean_drawdown_amount']:.2f}`"
                f" | positive `{100.0 * row['mean_positive_rate']:.1f}%`"
            )
    lines.extend(["", "## Confirmation Samples", ""])
    if confirmation_df.empty:
        lines.append("- No confirmation rows were produced.")
    else:
        for sample_size in sorted(set(int(value) for value in confirmation_df["sample_size"].tolist())):
            lines.append(f"### {sample_size}-Game Samples")
            sample_df = confirmation_df[confirmation_df["sample_size"] == sample_size].copy()
            for _, row in sample_df.iterrows():
                lines.append(
                    f"- `{row['variant_name']}`"
                    f" median end `{row['median_ending_bankroll']:.2f}`"
                    f" | mean end `{row['mean_ending_bankroll']:.2f}`"
                    f" | DD `{100.0 * row['mean_max_drawdown_pct']:.2f}%`"
                    f" / `${row['mean_max_drawdown_amount']:.2f}`"
                    f" | positive `{100.0 * row['positive_rate']:.1f}%`"
                )
            lines.append("")
    lines.extend(["## Postseason Final 20 Reference", ""])
    if postseason_df.empty:
        lines.append("- No postseason reference rows were produced.")
    else:
        for _, row in postseason_df.iterrows():
            lines.append(
                f"- `{row['variant_name']}`"
                f" end `{row['ending_bankroll']:.2f}`"
                f" | return `{100.0 * row['compounded_return']:.2f}%`"
                f" | DD `{100.0 * row['max_drawdown_pct']:.2f}%`"
                f" / `${row['max_drawdown_amount']:.2f}`"
                f" | trades `{int(row['executed_trade_count'])}`"
            )
    (output_dir / "finalist_tradeoff_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir) / args.season / "finalist_tradeoff_dynamic80_s5_slip5"
    output_dir.mkdir(parents=True, exist_ok=True)

    regular_request = _make_request(
        season=args.season,
        season_phase="regular_season",
        analysis_version=args.analysis_version,
        output_root=str(output_dir),
        llm_model=args.llm_model,
        llm_max_budget_usd=float(args.llm_budget_usd),
    )
    postseason_request = _make_request(
        season=args.season,
        season_phase="postseason_final_20",
        season_phases=("play_in", "playoffs"),
        analysis_version=args.analysis_version,
        output_root=str(output_dir),
        llm_model=args.llm_model,
        llm_max_budget_usd=float(args.llm_budget_usd),
    )

    regular_result = _load_result(regular_request)
    postseason_result = _load_result(postseason_request)

    priors = build_master_router_selection_priors(regular_result, core_strategy_families=CORE_FAMILIES)
    family_profiles = _build_family_profiles(
        regular_result,
        registry=regular_result.strategy_registry,
        strategy_families=RELEVANT_FAMILIES,
        core_strategy_families=CORE_FAMILIES,
    )
    regular_game_candidates = _build_game_candidates(
        regular_result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
    )
    postseason_game_candidates = _build_game_candidates(
        postseason_result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
    )

    client = _resolve_openai_client()
    cache_store = _load_llm_cache(output_dir / "llm_finalist_tradeoff_cache.json")
    budget_state = _LLMBudgetState()

    tuning_rows: list[dict[str, Any]] = []
    for sample_size in TUNING_SAMPLE_SIZES:
        plan = build_llm_iteration_plan(
            regular_result,
            strategy_families=RELEVANT_FAMILIES,
            iteration_count=int(args.tuning_iterations),
            games_per_iteration=sample_size,
            seeds=None,
            fallback_seed=RANDOM_SLIPPAGE_SEED + sample_size,
        )
        for item in plan:
            sampled_game_ids = tuple(str(game_id) for game_id in item["sampled_game_ids"])
            sampled_result = _subset_backtest_result(regular_result, sampled_game_ids)
            for variant in MASTER_VARIANTS:
                summary, _steps_df, _decisions_df = _run_master_variant(
                    sample_result=sampled_result,
                    request=regular_request,
                    sample_name=str(item["sample_name"]),
                    selection_sample_name="regular_full",
                    priors=priors,
                    variant=variant,
                )
                tuning_rows.append(
                    _summary_record(
                        stage="tuning",
                        sample_size=sample_size,
                        iteration_index=int(item["iteration_index"]),
                        iteration_seed=int(item["iteration_seed"]),
                        variant_name=str(variant["variant_name"]),
                        lane_group=str(variant["lane_group"]),
                        summary=summary,
                        extra_metrics={"llm_estimated_cost_usd": 0.0},
                    )
                )
            for lane in LLM_VARIANTS:
                summary, _steps_df, _decisions, token_totals = _run_llm_variant(
                    sampled_game_ids=sampled_game_ids,
                    request=regular_request,
                    sample_name=str(item["sample_name"]),
                    lane=lane,
                    game_candidates=regular_game_candidates,
                    family_profiles=family_profiles,
                    client=client,
                    budget_state=budget_state,
                    cache_store=cache_store,
                    iteration_index=int(item["iteration_index"]),
                    iteration_seed=int(item["iteration_seed"]),
                )
                tuning_rows.append(
                    _summary_record(
                        stage="tuning",
                        sample_size=sample_size,
                        iteration_index=int(item["iteration_index"]),
                        iteration_seed=int(item["iteration_seed"]),
                        variant_name=str(lane["variant_name"]),
                        lane_group=str(lane["lane_group"]),
                        summary=summary,
                        extra_metrics=token_totals,
                    )
                )

    tuning_df = pd.DataFrame(tuning_rows)
    tuning_df.to_csv(output_dir / "tuning_iteration_results.csv", index=False)
    tuning_aggregate_df = _aggregate_iterations(tuning_df)
    tuning_aggregate_df.to_csv(output_dir / "tuning_aggregate_summary.csv", index=False)
    ranked_df = _rank_variants(tuning_aggregate_df)
    ranked_df.to_csv(output_dir / "tuning_ranked_variants.csv", index=False)

    best_master_name, best_llm_name = _pick_best_variants(ranked_df)
    confirmation_master_variants = [
        _find_master_variant(name)
        for name in dict.fromkeys([MASTER_ROUTER_PORTFOLIO, best_master_name]).keys()
    ]
    confirmation_llm_variants = [
        _find_llm_variant(name)
        for name in dict.fromkeys(["gpt-5.4-mini :: llm_hybrid_freedom_compact_v1", best_llm_name]).keys()
    ]

    confirmation_rows: list[dict[str, Any]] = []
    for sample_size in TUNING_SAMPLE_SIZES:
        plan = build_llm_iteration_plan(
            regular_result,
            strategy_families=RELEVANT_FAMILIES,
            iteration_count=int(args.confirmation_iterations),
            games_per_iteration=sample_size,
            seeds=None,
            fallback_seed=(RANDOM_SLIPPAGE_SEED + 1000 + sample_size),
        )
        for item in plan:
            sampled_game_ids = tuple(str(game_id) for game_id in item["sampled_game_ids"])
            sampled_result = _subset_backtest_result(regular_result, sampled_game_ids)
            for variant in confirmation_master_variants:
                summary, _steps_df, _decisions_df = _run_master_variant(
                    sample_result=sampled_result,
                    request=regular_request,
                    sample_name=str(item["sample_name"]),
                    selection_sample_name="regular_full",
                    priors=priors,
                    variant=variant,
                )
                confirmation_rows.append(
                    _summary_record(
                        stage="confirmation",
                        sample_size=sample_size,
                        iteration_index=int(item["iteration_index"]),
                        iteration_seed=int(item["iteration_seed"]),
                        variant_name=str(variant["variant_name"]),
                        lane_group=str(variant["lane_group"]),
                        summary=summary,
                        extra_metrics={"llm_estimated_cost_usd": 0.0},
                    )
                )
            for lane in confirmation_llm_variants:
                summary, _steps_df, _decisions, token_totals = _run_llm_variant(
                    sampled_game_ids=sampled_game_ids,
                    request=regular_request,
                    sample_name=str(item["sample_name"]),
                    lane=lane,
                    game_candidates=regular_game_candidates,
                    family_profiles=family_profiles,
                    client=client,
                    budget_state=budget_state,
                    cache_store=cache_store,
                    iteration_index=int(item["iteration_index"]),
                    iteration_seed=int(item["iteration_seed"]),
                )
                confirmation_rows.append(
                    _summary_record(
                        stage="confirmation",
                        sample_size=sample_size,
                        iteration_index=int(item["iteration_index"]),
                        iteration_seed=int(item["iteration_seed"]),
                        variant_name=str(lane["variant_name"]),
                        lane_group=str(lane["lane_group"]),
                        summary=summary,
                        extra_metrics=token_totals,
                    )
                )

    confirmation_df = pd.DataFrame(confirmation_rows)
    confirmation_df.to_csv(output_dir / "confirmation_iteration_results.csv", index=False)
    confirmation_aggregate_df = _aggregate_iterations(confirmation_df)
    confirmation_aggregate_df.to_csv(output_dir / "confirmation_aggregate_summary.csv", index=False)

    postseason_rows: list[dict[str, Any]] = []
    for variant in confirmation_master_variants:
        summary, steps_df, decisions_df = _run_master_variant(
            sample_result=postseason_result,
            request=postseason_request,
            sample_name="postseason_final_20",
            selection_sample_name="regular_full",
            priors=priors,
            variant=variant,
        )
        postseason_rows.append(
            _summary_record(
                stage="postseason_reference",
                sample_size=20,
                iteration_index=None,
                iteration_seed=None,
                variant_name=str(variant["variant_name"]),
                lane_group=str(variant["lane_group"]),
                summary=summary,
                extra_metrics={"llm_estimated_cost_usd": 0.0},
            )
        )
        steps_df.to_csv(output_dir / f"{variant['variant_name']}_postseason_steps.csv", index=False)
        decisions_df.to_csv(output_dir / f"{variant['variant_name']}_postseason_decisions.csv", index=False)

    ordered_postseason_game_ids = tuple(
        str(game_id)
        for game_id in (
            postseason_result.state_df[["game_id", "game_date"]]
            .drop_duplicates(subset=["game_id"])
            .sort_values(["game_date", "game_id"], kind="mergesort", na_position="last")["game_id"]
            .tolist()
        )
    )
    for lane in confirmation_llm_variants:
        summary, steps_df, decisions, token_totals = _run_llm_variant(
            sampled_game_ids=ordered_postseason_game_ids,
            request=postseason_request,
            sample_name="postseason_final_20",
            lane=lane,
            game_candidates=postseason_game_candidates,
            family_profiles=family_profiles,
            client=client,
            budget_state=budget_state,
            cache_store=cache_store,
            iteration_index=0,
            iteration_seed=RANDOM_SLIPPAGE_SEED,
        )
        postseason_rows.append(
            _summary_record(
                stage="postseason_reference",
                sample_size=20,
                iteration_index=None,
                iteration_seed=None,
                variant_name=str(lane["variant_name"]),
                lane_group=str(lane["lane_group"]),
                summary=summary,
                extra_metrics=token_totals,
            )
        )
        steps_df.to_csv(output_dir / f"{lane['lane_name']}_postseason_steps.csv", index=False)
        pd.DataFrame(decisions).to_csv(output_dir / f"{lane['lane_name']}_postseason_decisions.csv", index=False)

    postseason_df = pd.DataFrame(postseason_rows)
    postseason_df.to_csv(output_dir / "postseason_reference_summary.csv", index=False)

    _write_report(
        output_dir,
        ranked_df=ranked_df,
        confirmation_df=confirmation_aggregate_df,
        postseason_df=postseason_df,
        best_master_name=best_master_name,
        best_llm_name=best_llm_name,
        llm_budget_spent=float(budget_state.spent_usd),
    )
    metadata = {
        "season": args.season,
        "analysis_version": args.analysis_version,
        "llm_model": args.llm_model,
        "llm_budget_spent_usd": float(budget_state.spent_usd),
        "best_master_variant": best_master_name,
        "best_llm_variant": best_llm_name,
        "output_dir": str(output_dir),
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
