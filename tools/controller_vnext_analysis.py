from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.backtests.controller_vnext import (
    DEFAULT_VNEXT_PROFILE,
    DEFAULT_VNEXT_STOP_MAP,
    VNEXT_MASTER_CONTROLLER,
    VNEXT_UNIFIED_CONTROLLER,
    apply_stop_overlay,
    build_state_lookup,
    decorate_trade_frame_with_vnext_sizing,
)
from app.data.pipelines.daily.nba.analysis.backtests.engine import (
    build_backtest_result,
    load_analysis_backtest_state_panel_df,
)
from app.data.pipelines.daily.nba.analysis.backtests.llm_experiment import (
    _LLMBudgetState,
    _build_family_profiles,
    _load_llm_cache,
    _resolve_openai_client,
    _subset_backtest_result,
    build_llm_iteration_plan,
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
RELEVANT_FAMILIES = tuple(dict.fromkeys([*CORE_FAMILIES, *EXTRA_FAMILIES]))
TUNING_SAMPLE_SIZES = (10, 20, 50)
TUNING_ITERATIONS = 10
POSTSEASON_SLIPPAGE_SEEDS = tuple(BASE_RANDOM_SLIPPAGE_SEED + offset for offset in range(6))

MASTER_ROUTER_KWARGS = {
    "extra_selection_mode": "same_side_top1",
    "min_core_confidence_for_extras": 0.60,
}

CURRENT_LLM_LANE = {
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

VNEXT_LLM_LANE = {
    "lane_name": "llm_hybrid_vnext_meta_review_v1",
    "lane_group": "llm_vnext",
    "lane_mode": "llm_freedom",
    "llm_component_scope": "bc_freedom",
    "allowed_roles": ("core", "extra"),
    "prompt_profile": "compact_anchor",
    "reasoning_effort": "low",
    "include_rationale": False,
    "use_confidence_gate": False,
    "max_selected_candidates": 2,
    "max_core_candidates": 1,
    "max_extra_candidates": 1,
    "require_core_for_extra": True,
}

VARIANT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "variant_name": "master_current_v1",
        "controller_type": "master",
        "router_kwargs": {},
        "risk_profile_name": "baseline",
        "risk_kwargs": {},
        "stop_map": {},
        "sizing_profile": None,
    },
    {
        "variant_name": "unified_current_v1",
        "controller_type": "unified",
        "lane": CURRENT_LLM_LANE,
        "router_kwargs": {
            "weak_confidence_threshold": 0.60,
            "llm_accept_confidence": 0.60,
            "skip_weak_when_llm_empty": True,
            "skip_weak_when_llm_low_confidence": True,
        },
        "risk_profile_name": "baseline",
        "risk_kwargs": {},
        "stop_map": {},
        "sizing_profile": None,
    },
    {
        "variant_name": f"{VNEXT_MASTER_CONTROLLER} :: balanced",
        "controller_type": "master",
        "router_kwargs": {},
        "risk_profile_name": "balanced",
        "risk_kwargs": {
            "runup_throttle_peak_multiple": 1.60,
            "runup_throttle_fraction_scale": 0.75,
            "drawdown_throttle_threshold_pct": 0.20,
            "drawdown_throttle_fraction_scale": 0.70,
            "drawdown_new_entry_stop_pct": 0.38,
            "daily_loss_new_entry_stop_pct": 0.18,
        },
        "stop_map": DEFAULT_VNEXT_STOP_MAP,
        "sizing_profile": DEFAULT_VNEXT_PROFILE,
    },
    {
        "variant_name": f"{VNEXT_MASTER_CONTROLLER} :: tight",
        "controller_type": "master",
        "router_kwargs": {},
        "risk_profile_name": "tight",
        "risk_kwargs": {
            "runup_throttle_peak_multiple": 1.45,
            "runup_throttle_fraction_scale": 0.62,
            "drawdown_throttle_threshold_pct": 0.16,
            "drawdown_throttle_fraction_scale": 0.60,
            "drawdown_new_entry_stop_pct": 0.30,
            "daily_loss_new_entry_stop_pct": 0.15,
        },
        "stop_map": DEFAULT_VNEXT_STOP_MAP,
        "sizing_profile": {
            **DEFAULT_VNEXT_PROFILE,
            "family_caps": {
                **DEFAULT_VNEXT_PROFILE["family_caps"],
                "winner_definition": 0.28,
                "inversion": 0.20,
            },
            "sleeve_cap_fraction": 0.10,
        },
    },
    {
        "variant_name": f"{VNEXT_UNIFIED_CONTROLLER} :: balanced",
        "controller_type": "unified",
        "lane": VNEXT_LLM_LANE,
        "router_kwargs": {
            "weak_confidence_threshold": 0.64,
            "llm_accept_confidence": 0.60,
            "llm_review_min_confidence": 0.46,
            "skip_weak_when_llm_empty": True,
            "skip_weak_when_llm_low_confidence": True,
            "skip_below_review_min_confidence": True,
        },
        "risk_profile_name": "balanced",
        "risk_kwargs": {
            "runup_throttle_peak_multiple": 1.60,
            "runup_throttle_fraction_scale": 0.75,
            "drawdown_throttle_threshold_pct": 0.20,
            "drawdown_throttle_fraction_scale": 0.70,
            "drawdown_new_entry_stop_pct": 0.38,
            "daily_loss_new_entry_stop_pct": 0.18,
        },
        "stop_map": DEFAULT_VNEXT_STOP_MAP,
        "sizing_profile": DEFAULT_VNEXT_PROFILE,
    },
    {
        "variant_name": f"{VNEXT_UNIFIED_CONTROLLER} :: tight",
        "controller_type": "unified",
        "lane": VNEXT_LLM_LANE,
        "router_kwargs": {
            "weak_confidence_threshold": 0.62,
            "llm_accept_confidence": 0.62,
            "llm_review_min_confidence": 0.50,
            "skip_weak_when_llm_empty": True,
            "skip_weak_when_llm_low_confidence": True,
            "skip_below_review_min_confidence": True,
        },
        "risk_profile_name": "tight",
        "risk_kwargs": {
            "runup_throttle_peak_multiple": 1.45,
            "runup_throttle_fraction_scale": 0.62,
            "drawdown_throttle_threshold_pct": 0.16,
            "drawdown_throttle_fraction_scale": 0.60,
            "drawdown_new_entry_stop_pct": 0.30,
            "daily_loss_new_entry_stop_pct": 0.15,
        },
        "stop_map": DEFAULT_VNEXT_STOP_MAP,
        "sizing_profile": {
            **DEFAULT_VNEXT_PROFILE,
            "family_caps": {
                **DEFAULT_VNEXT_PROFILE["family_caps"],
                "winner_definition": 0.28,
                "inversion": 0.20,
            },
            "sleeve_cap_fraction": 0.10,
            "llm_review_min_confidence": 0.50,
            "weak_confidence_threshold": 0.62,
            "llm_accept_confidence": 0.62,
        },
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Controller vNext against current finalists.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument(
        "--output-dir",
        default=r"C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_controller_vnext",
    )
    parser.add_argument("--llm-model", default="gpt-5.4")
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
    risk_kwargs: dict[str, Any],
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
        **risk_kwargs,
    )


def _build_master_trades(
    sample_result: Any,
    *,
    sample_name: str,
    priors: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    trades_df, _ = build_master_router_trade_frame(
        sample_result,
        sample_name=sample_name,
        selection_sample_name="regular_full",
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        **MASTER_ROUTER_KWARGS,
    )
    return trades_df


def _build_unified_trades(
    sample_result: Any,
    *,
    sample_name: str,
    priors: dict[str, dict[str, Any]],
    family_profiles: dict[str, dict[str, Any]],
    request: BacktestRunRequest,
    client: Any,
    budget_state: _LLMBudgetState,
    cache_store: Any,
    lane: dict[str, Any],
    historical_team_context_lookup: dict[str, dict[str, Any]] | None,
    router_kwargs: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    trades_df, decisions_df, token_totals = build_unified_router_trade_frame(
        sample_result,
        sample_name=sample_name,
        selection_sample_name="regular_full",
        priors=priors,
        family_profiles=family_profiles,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        llm_lane=lane,
        request=request,
        client=client,
        budget_state=budget_state,
        cache_store=cache_store,
        historical_team_context_lookup=historical_team_context_lookup,
        **MASTER_ROUTER_KWARGS,
        **router_kwargs,
    )
    return trades_df, token_totals, decisions_df


def _apply_vnext_post_processing(
    trades_df: pd.DataFrame,
    *,
    state_lookup: dict[tuple[str, str], pd.DataFrame],
    stop_map: dict[str, float] | None,
    sizing_profile: dict[str, Any] | None,
) -> pd.DataFrame:
    work = apply_stop_overlay(trades_df, state_lookup=state_lookup, stop_map=stop_map)
    if sizing_profile:
        work = decorate_trade_frame_with_vnext_sizing(work, profile=sizing_profile)
    return work


def _detail_row(
    *,
    stage: str,
    sample_size: int,
    variant_name: str,
    risk_profile_name: str,
    summary: dict[str, Any],
    iteration_index: int | None = None,
    iteration_seed: int | None = None,
    slippage_seed: int | None = None,
    llm_cost_usd: float = 0.0,
    llm_call_count: int = 0,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "sample_size": sample_size,
        "variant_name": variant_name,
        "risk_profile_name": risk_profile_name,
        "iteration_index": iteration_index,
        "iteration_seed": iteration_seed,
        "slippage_seed": slippage_seed,
        "ending_bankroll": float(summary.get("ending_bankroll") or 0.0),
        "compounded_return": float(summary.get("compounded_return") or 0.0),
        "max_drawdown_pct": float(summary.get("max_drawdown_pct") or 0.0),
        "max_drawdown_amount": float(summary.get("max_drawdown_amount") or 0.0),
        "peak_bankroll": float(summary.get("peak_bankroll") or 0.0),
        "min_bankroll": float(summary.get("min_bankroll") or 0.0),
        "executed_trade_count": int(summary.get("executed_trade_count") or 0),
        "skipped_risk_guard_count": int(summary.get("skipped_risk_guard_count") or 0),
        "skipped_daily_loss_guard_count": int(summary.get("skipped_daily_loss_guard_count") or 0),
        "llm_estimated_cost_usd": float(llm_cost_usd),
        "llm_call_count": int(llm_call_count),
    }


def _aggregate_regular(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    grouped = (
        frame.groupby(["sample_size", "variant_name", "risk_profile_name"], dropna=False)
        .agg(
            iteration_count=("ending_bankroll", "count"),
            mean_ending_bankroll=("ending_bankroll", "mean"),
            median_ending_bankroll=("ending_bankroll", "median"),
            min_ending_bankroll=("ending_bankroll", "min"),
            max_ending_bankroll=("ending_bankroll", "max"),
            positive_rate=("ending_bankroll", lambda values: float((pd.Series(values) > STARTING_BANKROLL).mean())),
            mean_max_drawdown_pct=("max_drawdown_pct", "mean"),
            mean_max_drawdown_amount=("max_drawdown_amount", "mean"),
            mean_trade_count=("executed_trade_count", "mean"),
            mean_skipped_risk_guard_count=("skipped_risk_guard_count", "mean"),
            mean_skipped_daily_loss_guard_count=("skipped_daily_loss_guard_count", "mean"),
            total_llm_estimated_cost_usd=("llm_estimated_cost_usd", "sum"),
        )
        .reset_index()
    )
    grouped["tradeoff_score"] = (
        grouped["median_ending_bankroll"]
        * grouped["positive_rate"].clip(lower=0.0, upper=1.0)
        * (1.0 - grouped["mean_max_drawdown_pct"].clip(lower=0.0, upper=0.999999))
    )
    return grouped.sort_values(
        ["sample_size", "tradeoff_score", "median_ending_bankroll"],
        ascending=[True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def _aggregate_postseason(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    grouped = (
        frame.groupby(["variant_name", "risk_profile_name"], dropna=False)
        .agg(
            slippage_seed_count=("slippage_seed", "count"),
            mean_ending_bankroll=("ending_bankroll", "mean"),
            median_ending_bankroll=("ending_bankroll", "median"),
            min_ending_bankroll=("ending_bankroll", "min"),
            max_ending_bankroll=("ending_bankroll", "max"),
            mean_max_drawdown_pct=("max_drawdown_pct", "mean"),
            mean_max_drawdown_amount=("max_drawdown_amount", "mean"),
            mean_min_bankroll=("min_bankroll", "mean"),
            mean_trade_count=("executed_trade_count", "mean"),
            mean_skipped_risk_guard_count=("skipped_risk_guard_count", "mean"),
            mean_skipped_daily_loss_guard_count=("skipped_daily_loss_guard_count", "mean"),
            total_llm_estimated_cost_usd=("llm_estimated_cost_usd", "sum"),
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


def _build_overall_score(regular_aggregate_df: pd.DataFrame, postseason_df: pd.DataFrame) -> pd.DataFrame:
    if regular_aggregate_df.empty or postseason_df.empty:
        return pd.DataFrame()
    regular_scores = (
        regular_aggregate_df.groupby("variant_name", dropna=False)
        .agg(
            mean_regular_tradeoff=("tradeoff_score", "mean"),
            mean_regular_median_end=("median_ending_bankroll", "mean"),
            mean_regular_drawdown=("mean_max_drawdown_pct", "mean"),
        )
        .reset_index()
    )
    postseason_scores = postseason_df[[
        "variant_name",
        "tradeoff_score",
        "median_ending_bankroll",
        "mean_ending_bankroll",
        "mean_max_drawdown_pct",
        "mean_max_drawdown_amount",
    ]].rename(
        columns={
            "tradeoff_score": "postseason_tradeoff",
            "median_ending_bankroll": "postseason_median_end",
            "mean_ending_bankroll": "postseason_mean_end",
            "mean_max_drawdown_pct": "postseason_mean_drawdown",
            "mean_max_drawdown_amount": "postseason_mean_drawdown_amount",
        }
    )
    merged = regular_scores.merge(postseason_scores, on="variant_name", how="inner")
    merged["overall_score"] = (0.40 * merged["mean_regular_tradeoff"]) + (0.60 * merged["postseason_tradeoff"])
    return merged.sort_values(
        ["overall_score", "postseason_mean_end"],
        ascending=[False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def _write_report(
    output_dir: Path,
    *,
    regular_aggregate_df: pd.DataFrame,
    postseason_df: pd.DataFrame,
    overall_df: pd.DataFrame,
    llm_spend_usd: float,
) -> None:
    lines = [
        "# Controller vNext final tuning",
        "",
        "Focused benchmark for the actual live playoff controller. This pass compares current finalists against a narrow set of vNext controller candidates.",
        "",
        "## Contract",
        "",
        f"- Start bankroll: `{STARTING_BANKROLL}`",
        f"- Base position fraction floor: `{POSITION_SIZE_FRACTION}`",
        f"- Target exposure fraction: `{TARGET_EXPOSURE_FRACTION}`",
        f"- Max concurrent positions: `{MAX_CONCURRENT_POSITIONS}`",
        f"- Random adverse slippage: `0-{RANDOM_SLIPPAGE_MAX_CENTS}c`",
        f"- Regular sample sizes: `{', '.join(str(value) for value in TUNING_SAMPLE_SIZES)}` with `{TUNING_ITERATIONS}` windows each",
        f"- Postseason reference seeds: `{', '.join(str(seed) for seed in POSTSEASON_SLIPPAGE_SEEDS)}`",
        f"- Estimated LLM spend: `${llm_spend_usd:.4f}`",
        "",
        "## Overall ranking",
        "",
    ]
    if overall_df.empty:
        lines.append("- No overall rows were produced.")
    else:
        for _, row in overall_df.iterrows():
            lines.append(
                f"- `{row['variant_name']}`"
                f" overall `{row['overall_score']:.3f}`"
                f" | postseason median end `${row['postseason_median_end']:.2f}`"
                f" | postseason DD `{100.0 * row['postseason_mean_drawdown']:.2f}%` / `${row['postseason_mean_drawdown_amount']:.2f}`"
                f" | regular tradeoff `{row['mean_regular_tradeoff']:.3f}`"
            )
    lines.extend(["", "## Regular window summaries", ""])
    if regular_aggregate_df.empty:
        lines.append("- No regular-window rows were produced.")
    else:
        for sample_size in TUNING_SAMPLE_SIZES:
            lines.append(f"### {sample_size}-game windows")
            rows = regular_aggregate_df[regular_aggregate_df["sample_size"] == sample_size]
            for _, row in rows.iterrows():
                lines.append(
                    f"- `{row['variant_name']}`"
                    f" tradeoff `{row['tradeoff_score']:.3f}`"
                    f" | median end `${row['median_ending_bankroll']:.2f}`"
                    f" | positive `{100.0 * row['positive_rate']:.1f}%`"
                    f" | mean DD `{100.0 * row['mean_max_drawdown_pct']:.2f}%` / `${row['mean_max_drawdown_amount']:.2f}`"
                )
            lines.append("")
    lines.extend(["## Postseason final 20", ""])
    if postseason_df.empty:
        lines.append("- No postseason rows were produced.")
    else:
        for _, row in postseason_df.iterrows():
            lines.append(
                f"- `{row['variant_name']}`"
                f" tradeoff `{row['tradeoff_score']:.3f}`"
                f" | mean end `${row['mean_ending_bankroll']:.2f}`"
                f" | median `${row['median_ending_bankroll']:.2f}`"
                f" | DD `{100.0 * row['mean_max_drawdown_pct']:.2f}%` / `${row['mean_max_drawdown_amount']:.2f}`"
                f" | min bankroll `${row['mean_min_bankroll']:.2f}`"
            )
    (output_dir / "controller_vnext_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve() / args.season / "controller_vnext_dynamic80_s5_slip5"
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
    with managed_connection() as connection:
        team_profiles_df = _load_team_profiles_df(
            connection,
            season=args.season,
            analysis_version=args.analysis_version,
        )

    priors = build_master_router_selection_priors(regular_result, core_strategy_families=CORE_FAMILIES)
    family_profiles = _build_family_profiles(
        regular_result,
        registry=regular_result.strategy_registry,
        strategy_families=RELEVANT_FAMILIES,
        core_strategy_families=CORE_FAMILIES,
    )
    postseason_team_context_lookup = build_team_profile_context_lookup(team_profiles_df)
    client = _resolve_openai_client()
    cache_store = _load_llm_cache(output_dir / "controller_vnext_llm_cache.json")
    budget_state = _LLMBudgetState()

    regular_rows: list[dict[str, Any]] = []
    postseason_rows: list[dict[str, Any]] = []
    postseason_seed0_steps: list[pd.DataFrame] = []
    postseason_seed0_decisions: list[pd.DataFrame] = []

    regular_state_lookup = build_state_lookup(regular_result.state_df)
    postseason_state_lookup = build_state_lookup(postseason_result.state_df)

    for sample_size in TUNING_SAMPLE_SIZES:
        plan = build_llm_iteration_plan(
            regular_result,
            strategy_families=RELEVANT_FAMILIES,
            iteration_count=TUNING_ITERATIONS,
            games_per_iteration=sample_size,
            seeds=None,
            fallback_seed=BASE_RANDOM_SLIPPAGE_SEED + (sample_size * 100),
        )
        for item in plan:
            sampled_game_ids = tuple(str(game_id) for game_id in item["sampled_game_ids"])
            sampled_result = _subset_backtest_result(regular_result, sampled_game_ids)
            sampled_family_profiles = _build_family_profiles(
                regular_result,
                registry=regular_result.strategy_registry,
                strategy_families=RELEVANT_FAMILIES,
                core_strategy_families=CORE_FAMILIES,
            )
            for variant in VARIANT_SPECS:
                if variant["controller_type"] == "master":
                    trades_df = _build_master_trades(
                        sampled_result,
                        sample_name=str(item["sample_name"]),
                        priors=priors,
                    )
                    token_totals = {"llm_estimated_cost_usd": 0.0, "llm_call_count": 0}
                else:
                    trades_df, token_totals, _decisions_df = _build_unified_trades(
                        sampled_result,
                        sample_name=str(item["sample_name"]),
                        priors=priors,
                        family_profiles=sampled_family_profiles,
                        request=regular_request,
                        client=client,
                        budget_state=budget_state,
                        cache_store=cache_store,
                        lane=variant["lane"],
                        historical_team_context_lookup=None,
                        router_kwargs=dict(variant.get("router_kwargs") or {}),
                    )
                trades_df = _apply_vnext_post_processing(
                    trades_df,
                    state_lookup=regular_state_lookup,
                    stop_map=variant.get("stop_map"),
                    sizing_profile=variant.get("sizing_profile"),
                )
                summary, _steps_df = _simulate(
                    trades_df,
                    sample_name=str(item["sample_name"]),
                    strategy_family=str(variant["variant_name"]),
                    request=regular_request,
                    risk_kwargs=dict(variant.get("risk_kwargs") or {}),
                    random_slippage_seed=BASE_RANDOM_SLIPPAGE_SEED + int(item["iteration_seed"]),
                )
                regular_rows.append(
                    _detail_row(
                        stage="regular_window",
                        sample_size=sample_size,
                        variant_name=str(variant["variant_name"]),
                        risk_profile_name=str(variant["risk_profile_name"]),
                        summary=summary,
                        iteration_index=int(item["iteration_index"]),
                        iteration_seed=int(item["iteration_seed"]),
                        llm_cost_usd=float(token_totals.get("llm_estimated_cost_usd") or 0.0),
                        llm_call_count=int(token_totals.get("llm_call_count") or 0),
                    )
                )

    for slippage_seed in POSTSEASON_SLIPPAGE_SEEDS:
        for variant in VARIANT_SPECS:
            if variant["controller_type"] == "master":
                trades_df = _build_master_trades(
                    postseason_result,
                    sample_name="postseason_final_20",
                    priors=priors,
                )
                decisions_df = pd.DataFrame()
                token_totals = {"llm_estimated_cost_usd": 0.0, "llm_call_count": 0}
            else:
                trades_df, token_totals, decisions_df = _build_unified_trades(
                    postseason_result,
                    sample_name="postseason_final_20",
                    priors=priors,
                    family_profiles=family_profiles,
                    request=postseason_request,
                    client=client,
                    budget_state=budget_state,
                    cache_store=cache_store,
                    lane=variant["lane"],
                    historical_team_context_lookup=postseason_team_context_lookup,
                    router_kwargs=dict(variant.get("router_kwargs") or {}),
                )
            trades_df = _apply_vnext_post_processing(
                trades_df,
                state_lookup=postseason_state_lookup,
                stop_map=variant.get("stop_map"),
                sizing_profile=variant.get("sizing_profile"),
            )
            summary, steps_df = _simulate(
                trades_df,
                sample_name="postseason_final_20",
                strategy_family=str(variant["variant_name"]),
                request=postseason_request,
                risk_kwargs=dict(variant.get("risk_kwargs") or {}),
                random_slippage_seed=int(slippage_seed),
            )
            postseason_rows.append(
                _detail_row(
                    stage="postseason_reference",
                    sample_size=20,
                    variant_name=str(variant["variant_name"]),
                    risk_profile_name=str(variant["risk_profile_name"]),
                    summary=summary,
                    slippage_seed=int(slippage_seed),
                    llm_cost_usd=float(token_totals.get("llm_estimated_cost_usd") or 0.0),
                    llm_call_count=int(token_totals.get("llm_call_count") or 0),
                )
            )
            if slippage_seed == POSTSEASON_SLIPPAGE_SEEDS[0]:
                step_copy = steps_df.copy()
                step_copy["variant_name"] = str(variant["variant_name"])
                postseason_seed0_steps.append(step_copy)
                if not decisions_df.empty:
                    decision_copy = decisions_df.copy()
                    decision_copy["variant_name"] = str(variant["variant_name"])
                    postseason_seed0_decisions.append(decision_copy)

    regular_detail_df = pd.DataFrame(regular_rows)
    postseason_detail_df = pd.DataFrame(postseason_rows)
    regular_aggregate_df = _aggregate_regular(regular_detail_df)
    postseason_aggregate_df = _aggregate_postseason(postseason_detail_df)
    overall_df = _build_overall_score(regular_aggregate_df, postseason_aggregate_df)

    regular_detail_df.to_csv(output_dir / "controller_vnext_regular_detail.csv", index=False)
    regular_aggregate_df.to_csv(output_dir / "controller_vnext_regular_summary.csv", index=False)
    postseason_detail_df.to_csv(output_dir / "controller_vnext_postseason_detail.csv", index=False)
    postseason_aggregate_df.to_csv(output_dir / "controller_vnext_postseason_summary.csv", index=False)
    overall_df.to_csv(output_dir / "controller_vnext_overall_summary.csv", index=False)
    non_empty_seed0_steps = [frame for frame in postseason_seed0_steps if not frame.empty]
    non_empty_seed0_decisions = [frame for frame in postseason_seed0_decisions if not frame.empty]
    if non_empty_seed0_steps:
        pd.concat(non_empty_seed0_steps, ignore_index=True).to_csv(output_dir / "controller_vnext_postseason_seed0_steps.csv", index=False)
    if non_empty_seed0_decisions:
        pd.concat(non_empty_seed0_decisions, ignore_index=True).to_csv(output_dir / "controller_vnext_postseason_seed0_decisions.csv", index=False)

    _write_report(
        output_dir,
        regular_aggregate_df=regular_aggregate_df,
        postseason_df=postseason_aggregate_df,
        overall_df=overall_df,
        llm_spend_usd=float(budget_state.spent_usd),
    )
    metadata = {
        "season": args.season,
        "analysis_version": args.analysis_version,
        "llm_model": args.llm_model,
        "llm_budget_spent_usd": float(budget_state.spent_usd),
        "output_dir": str(output_dir),
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
