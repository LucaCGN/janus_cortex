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
TUNING_ITERATIONS = 12
POSTSEASON_SLIPPAGE_SEEDS = tuple(BASE_RANDOM_SLIPPAGE_SEED + offset for offset in range(6))

MASTER_FINALIST = {
    "variant_name": "master_strategy_router_same_side_top1_conf60_v1",
    "router_kwargs": {
        "extra_selection_mode": "same_side_top1",
        "min_core_confidence_for_extras": 0.60,
    },
}

LLM_BASELINE = {
    "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_v1",
    "lane_name": "llm_hybrid_freedom_compact_v1",
    "lane_group": "llm_finalist",
    "lane_mode": "llm_freedom",
    "llm_component_scope": "bc_freedom",
    "allowed_roles": ("core", "extra"),
    "prompt_profile": "compact",
    "reasoning_effort": "low",
    "include_rationale": False,
    "use_confidence_gate": False,
}

LLM_CONTEXT_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        **LLM_BASELINE,
        "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_risk_v1",
        "lane_name": "llm_hybrid_freedom_compact_risk_v1",
    },
    {
        **LLM_BASELINE,
        "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_postseason_context_v1",
        "lane_name": "llm_hybrid_freedom_compact_postseason_context_v1",
    },
    {
        **LLM_BASELINE,
        "variant_name": "gpt-5.4 :: llm_hybrid_freedom_anchor_postseason_context_v1",
        "lane_name": "llm_hybrid_freedom_anchor_postseason_context_v1",
        "prompt_profile": "compact_anchor",
        "max_selected_candidates": 2,
        "max_core_candidates": 1,
        "max_extra_candidates": 1,
        "require_core_for_extra": True,
    },
)

RISK_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "risk_profile_name": "baseline",
        "portfolio_kwargs": {},
    },
    {
        "risk_profile_name": "drawdown_only_v1",
        "portfolio_kwargs": {
            "drawdown_throttle_threshold_pct": 0.18,
            "drawdown_throttle_fraction_scale": 0.60,
            "drawdown_new_entry_stop_pct": 0.42,
        },
    },
    {
        "risk_profile_name": "light_peak_drawdown_v1",
        "portfolio_kwargs": {
            "runup_throttle_peak_multiple": 1.75,
            "runup_throttle_fraction_scale": 0.75,
            "drawdown_throttle_threshold_pct": 0.18,
            "drawdown_throttle_fraction_scale": 0.65,
            "drawdown_new_entry_stop_pct": 0.45,
        },
    },
    {
        "risk_profile_name": "balanced_peak_drawdown_v1",
        "portfolio_kwargs": {
            "runup_throttle_peak_multiple": 1.50,
            "runup_throttle_fraction_scale": 0.65,
            "drawdown_throttle_threshold_pct": 0.15,
            "drawdown_throttle_fraction_scale": 0.55,
            "drawdown_new_entry_stop_pct": 0.38,
        },
    },
    {
        "risk_profile_name": "tight_peak_drawdown_v1",
        "portfolio_kwargs": {
            "runup_throttle_peak_multiple": 1.35,
            "runup_throttle_fraction_scale": 0.55,
            "drawdown_throttle_threshold_pct": 0.12,
            "drawdown_throttle_fraction_scale": 0.45,
            "drawdown_new_entry_stop_pct": 0.30,
        },
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune live-readiness tradeoffs for deterministic vs LLM finalists.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument(
        "--output-dir",
        default=r"C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_live_readiness",
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


def _simulate_with_risk(
    trades_df: pd.DataFrame,
    *,
    sample_name: str,
    strategy_family: str,
    request: BacktestRunRequest,
    risk_profile: dict[str, Any],
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
        **dict(risk_profile.get("portfolio_kwargs") or {}),
    )


def _run_master_variant(
    *,
    sample_result: Any,
    request: BacktestRunRequest,
    sample_name: str,
    priors: dict[str, dict[str, Any]],
    risk_profile: dict[str, Any],
    random_slippage_seed: int,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    trades_df, decisions_df = build_master_router_trade_frame(
        sample_result,
        sample_name=sample_name,
        selection_sample_name="regular_full",
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        **dict(MASTER_FINALIST["router_kwargs"]),
    )
    summary, steps_df = _simulate_with_risk(
        trades_df,
        sample_name=sample_name,
        strategy_family=str(MASTER_FINALIST["variant_name"]),
        request=request,
        risk_profile=risk_profile,
        random_slippage_seed=random_slippage_seed,
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
    risk_profile: dict[str, Any],
    random_slippage_seed: int,
    client: Any,
    budget_state: _LLMBudgetState,
    cache_store: Any,
    iteration_seed: int,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    trades_df, token_totals, lane_decisions = _run_llm_lane_sample(
        iteration_index=0,
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
    summary, steps_df = _simulate_with_risk(
        trades_df,
        sample_name=sample_name,
        strategy_family=str(lane["variant_name"]),
        request=request,
        risk_profile=risk_profile,
        random_slippage_seed=random_slippage_seed,
    )
    return summary, steps_df, lane_decisions, token_totals


def _summary_record(
    *,
    stage: str,
    sample_size: int,
    variant_name: str,
    risk_profile_name: str,
    summary: dict[str, Any],
    iteration_index: int | None = None,
    iteration_seed: int | None = None,
    slippage_seed: int | None = None,
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
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
        "avg_executed_trade_return_with_slippage": float(summary.get("avg_executed_trade_return_with_slippage") or 0.0),
        "skipped_risk_guard_count": int(summary.get("skipped_risk_guard_count") or 0),
    }
    record.update(extra_metrics or {})
    return record


def _aggregate_regular(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    grouped = (
        frame.groupby(["sample_size", "risk_profile_name"], dropna=False)
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
        )
        .reset_index()
    )
    grouped["tradeoff_score"] = (
        grouped["median_ending_bankroll"]
        * grouped["positive_rate"].clip(lower=0.0, upper=1.0)
        * (1.0 - grouped["mean_max_drawdown_pct"].clip(lower=0.0, upper=0.999999))
    )
    return grouped.sort_values(
        ["sample_size", "tradeoff_score", "mean_ending_bankroll"],
        ascending=[True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def _rank_risk_profiles(aggregate_df: pd.DataFrame) -> pd.DataFrame:
    if aggregate_df.empty:
        return pd.DataFrame()
    return (
        aggregate_df.groupby("risk_profile_name", dropna=False)
        .agg(
            sample_sizes=("sample_size", lambda values: ",".join(str(int(value)) for value in sorted(set(values)))),
            mean_tradeoff_score=("tradeoff_score", "mean"),
            mean_median_ending_bankroll=("median_ending_bankroll", "mean"),
            mean_mean_ending_bankroll=("mean_ending_bankroll", "mean"),
            mean_drawdown_pct=("mean_max_drawdown_pct", "mean"),
            mean_drawdown_amount=("mean_max_drawdown_amount", "mean"),
            mean_positive_rate=("positive_rate", "mean"),
            mean_trade_count=("mean_trade_count", "mean"),
            mean_skipped_risk_guard_count=("mean_skipped_risk_guard_count", "mean"),
        )
        .reset_index()
        .sort_values(
            ["mean_tradeoff_score", "mean_median_ending_bankroll"],
            ascending=[False, False],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )


def _pick_risk_profile(ranked_df: pd.DataFrame) -> str:
    if ranked_df.empty:
        return "baseline"
    return str(ranked_df.iloc[0]["risk_profile_name"])


def _find_risk_profile(name: str) -> dict[str, Any]:
    return next(profile for profile in RISK_PROFILES if str(profile["risk_profile_name"]) == name)


def _build_postseason_variant_rows(postseason_df: pd.DataFrame) -> pd.DataFrame:
    if postseason_df.empty:
        return postseason_df
    grouped = (
        postseason_df.groupby("variant_name", dropna=False)
        .agg(
            slippage_seed_count=("slippage_seed", "count"),
            mean_ending_bankroll=("ending_bankroll", "mean"),
            median_ending_bankroll=("ending_bankroll", "median"),
            min_ending_bankroll=("ending_bankroll", "min"),
            max_ending_bankroll=("ending_bankroll", "max"),
            mean_compounded_return=("compounded_return", "mean"),
            mean_max_drawdown_pct=("max_drawdown_pct", "mean"),
            mean_max_drawdown_amount=("max_drawdown_amount", "mean"),
            mean_peak_bankroll=("peak_bankroll", "mean"),
            mean_min_bankroll=("min_bankroll", "mean"),
            mean_trade_count=("executed_trade_count", "mean"),
            mean_skipped_risk_guard_count=("skipped_risk_guard_count", "mean"),
            mean_llm_cost_usd=("llm_estimated_cost_usd", "mean"),
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


def _write_report(
    output_dir: Path,
    *,
    risk_ranked_df: pd.DataFrame,
    postseason_seed_df: pd.DataFrame,
    postseason_variant_df: pd.DataFrame,
    selected_risk_profile_name: str,
    llm_budget_spent: float,
) -> None:
    lines = [
        "# Final Live Readiness Analysis",
        "",
        "## Contract",
        "",
        f"- Start bankroll: `{STARTING_BANKROLL}`",
        f"- Base position fraction floor: `{POSITION_SIZE_FRACTION}`",
        f"- Dynamic target exposure fraction: `{TARGET_EXPOSURE_FRACTION}`",
        f"- Max concurrent positions: `{MAX_CONCURRENT_POSITIONS}`",
        f"- Random adverse slippage max cents: `{RANDOM_SLIPPAGE_MAX_CENTS}`",
        f"- Postseason reference seeds: `{', '.join(str(seed) for seed in POSTSEASON_SLIPPAGE_SEEDS)}`",
        "",
        "## Selected Portfolio Guard Profile",
        "",
        f"- Selected risk profile: `{selected_risk_profile_name}`",
        f"- Estimated LLM spend during analysis: `${llm_budget_spent:.4f}`",
        "",
        "## Regular-Season Risk Tuning Summary",
        "",
    ]
    if risk_ranked_df.empty:
        lines.append("- No regular-season risk profile rows were produced.")
    else:
        for _, row in risk_ranked_df.iterrows():
            lines.append(
                f"- `{row['risk_profile_name']}`"
                f" tradeoff `{row['mean_tradeoff_score']:.4f}`"
                f" | median end `{row['mean_median_ending_bankroll']:.2f}`"
                f" | mean DD `{100.0 * row['mean_drawdown_pct']:.2f}%` / `${row['mean_drawdown_amount']:.2f}`"
                f" | positive `{100.0 * row['mean_positive_rate']:.1f}%`"
            )
    lines.extend(["", "## Postseason Final 20 Across Slippage Seeds", ""])
    if postseason_variant_df.empty:
        lines.append("- No postseason comparison rows were produced.")
    else:
        for _, row in postseason_variant_df.iterrows():
            lines.append(
                f"- `{row['variant_name']}`"
                f" mean end `{row['mean_ending_bankroll']:.2f}`"
                f" | median end `{row['median_ending_bankroll']:.2f}`"
                f" | range `${row['min_ending_bankroll']:.2f}`-`${row['max_ending_bankroll']:.2f}`"
                f" | mean DD `{100.0 * row['mean_max_drawdown_pct']:.2f}%` / `${row['mean_max_drawdown_amount']:.2f}`"
                f" | mean min bankroll `{row['mean_min_bankroll']:.2f}`"
                f" | tradeoff `{row['tradeoff_score']:.2f}`"
            )
    lines.extend(["", "## Per-Seed Postseason Rows", ""])
    if postseason_seed_df.empty:
        lines.append("- No per-seed postseason rows were produced.")
    else:
        for _, row in postseason_seed_df.sort_values(["variant_name", "slippage_seed"], kind="mergesort").iterrows():
            lines.append(
                f"- `{row['variant_name']}` seed `{int(row['slippage_seed'])}`"
                f" -> end `{row['ending_bankroll']:.2f}`"
                f" | DD `{100.0 * row['max_drawdown_pct']:.2f}%` / `${row['max_drawdown_amount']:.2f}`"
                f" | min bankroll `{row['min_bankroll']:.2f}`"
                f" | trades `{int(row['executed_trade_count'])}`"
            )
    (output_dir / "final_live_readiness_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir) / args.season / "final_live_readiness_dynamic80_s5_slip5"
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
    postseason_game_candidates_baseline = _build_game_candidates(
        postseason_result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
    )
    postseason_game_candidates_context = _build_game_candidates(
        postseason_result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        historical_team_context_lookup=postseason_team_context_lookup,
    )

    regular_tuning_rows: list[dict[str, Any]] = []
    for sample_size in TUNING_SAMPLE_SIZES:
        plan = build_llm_iteration_plan(
            regular_result,
            strategy_families=RELEVANT_FAMILIES,
            iteration_count=TUNING_ITERATIONS,
            games_per_iteration=sample_size,
            seeds=None,
            fallback_seed=BASE_RANDOM_SLIPPAGE_SEED + (10 * sample_size),
        )
        for item in plan:
            sampled_game_ids = tuple(str(game_id) for game_id in item["sampled_game_ids"])
            sampled_result = _subset_backtest_result(regular_result, sampled_game_ids)
            for risk_profile in RISK_PROFILES:
                summary, _steps_df, _decisions_df = _run_master_variant(
                    sample_result=sampled_result,
                    request=regular_request,
                    sample_name=str(item["sample_name"]),
                    priors=priors,
                    risk_profile=risk_profile,
                    random_slippage_seed=BASE_RANDOM_SLIPPAGE_SEED + int(item["iteration_seed"]),
                )
                regular_tuning_rows.append(
                    _summary_record(
                        stage="regular_risk_tuning",
                        sample_size=sample_size,
                        variant_name=str(MASTER_FINALIST["variant_name"]),
                        risk_profile_name=str(risk_profile["risk_profile_name"]),
                        summary=summary,
                        iteration_index=int(item["iteration_index"]),
                        iteration_seed=int(item["iteration_seed"]),
                    )
                )

    regular_tuning_df = pd.DataFrame(regular_tuning_rows)
    regular_tuning_df.to_csv(output_dir / "regular_risk_tuning_detail.csv", index=False)
    regular_risk_aggregate_df = _aggregate_regular(regular_tuning_df)
    regular_risk_aggregate_df.to_csv(output_dir / "regular_risk_tuning_summary.csv", index=False)
    regular_risk_ranked_df = _rank_risk_profiles(regular_risk_aggregate_df)
    regular_risk_ranked_df.to_csv(output_dir / "regular_risk_profiles_ranked.csv", index=False)
    selected_risk_profile_name = _pick_risk_profile(regular_risk_ranked_df)
    selected_risk_profile = _find_risk_profile(selected_risk_profile_name)

    client = _resolve_openai_client()
    cache_store = _load_llm_cache(output_dir / "llm_live_readiness_cache.json")
    budget_state = _LLMBudgetState()

    ordered_postseason_game_ids = tuple(
        str(game_id)
        for game_id in (
            postseason_result.state_df[["game_id", "game_date"]]
            .drop_duplicates(subset=["game_id"])
            .sort_values(["game_date", "game_id"], kind="mergesort", na_position="last")["game_id"]
            .tolist()
        )
    )

    postseason_rows: list[dict[str, Any]] = []
    postseason_steps: list[pd.DataFrame] = []
    postseason_decisions: list[pd.DataFrame] = []

    deterministic_variants = (
        ("master_strategy_router_same_side_top1_conf60_v1 :: current", RISK_PROFILES[0]),
        ("master_strategy_router_same_side_top1_conf60_v1 :: improved", selected_risk_profile),
    )
    for variant_name, risk_profile in deterministic_variants:
        for slippage_seed in POSTSEASON_SLIPPAGE_SEEDS:
            summary, steps_df, decisions_df = _run_master_variant(
                sample_result=postseason_result,
                request=postseason_request,
                sample_name="postseason_final_20",
                priors=priors,
                risk_profile=risk_profile,
                random_slippage_seed=int(slippage_seed),
            )
            summary["strategy_family"] = variant_name
            postseason_rows.append(
                _summary_record(
                    stage="postseason_reference",
                    sample_size=20,
                    variant_name=variant_name,
                    risk_profile_name=str(risk_profile["risk_profile_name"]),
                    summary=summary,
                    slippage_seed=int(slippage_seed),
                    extra_metrics={"llm_estimated_cost_usd": 0.0},
                )
            )
            if slippage_seed == POSTSEASON_SLIPPAGE_SEEDS[0]:
                steps_copy = steps_df.copy()
                steps_copy["variant_name"] = variant_name
                steps_copy["risk_profile_name"] = risk_profile["risk_profile_name"]
                decisions_copy = decisions_df.copy()
                decisions_copy["variant_name"] = variant_name
                decisions_copy["risk_profile_name"] = risk_profile["risk_profile_name"]
                postseason_steps.append(steps_copy)
                postseason_decisions.append(decisions_copy)

    llm_variants = (
        {
            **LLM_BASELINE,
            "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_v1 :: current",
        },
        {
            **LLM_CONTEXT_VARIANTS[0],
            "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_v1 :: improved_risk_only",
        },
        {
            **LLM_CONTEXT_VARIANTS[1],
            "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_v1 :: improved_postseason_context",
        },
        {
            **LLM_CONTEXT_VARIANTS[2],
            "variant_name": "gpt-5.4 :: llm_hybrid_freedom_compact_v1 :: improved_postseason_anchor",
        },
    )
    for lane in llm_variants:
        lane_uses_context = "postseason_context" in str(lane["variant_name"])
        lane_risk_profile = selected_risk_profile if "current" not in str(lane["variant_name"]) else RISK_PROFILES[0]
        candidate_source = postseason_game_candidates_context if lane_uses_context else postseason_game_candidates_baseline
        for slippage_seed in POSTSEASON_SLIPPAGE_SEEDS:
            summary, steps_df, decisions_rows, token_totals = _run_llm_variant(
                sampled_game_ids=ordered_postseason_game_ids,
                request=postseason_request,
                sample_name="postseason_final_20",
                lane=lane,
                game_candidates=candidate_source,
                family_profiles=family_profiles,
                risk_profile=lane_risk_profile,
                random_slippage_seed=int(slippage_seed),
                client=client,
                budget_state=budget_state,
                cache_store=cache_store,
                iteration_seed=int(slippage_seed),
            )
            summary["strategy_family"] = str(lane["variant_name"])
            postseason_rows.append(
                _summary_record(
                    stage="postseason_reference",
                    sample_size=20,
                    variant_name=str(lane["variant_name"]),
                    risk_profile_name=str(lane_risk_profile["risk_profile_name"]),
                    summary=summary,
                    slippage_seed=int(slippage_seed),
                    extra_metrics=token_totals,
                )
            )
            if slippage_seed == POSTSEASON_SLIPPAGE_SEEDS[0]:
                steps_copy = steps_df.copy()
                steps_copy["variant_name"] = str(lane["variant_name"])
                steps_copy["risk_profile_name"] = lane_risk_profile["risk_profile_name"]
                decisions_copy = pd.DataFrame(decisions_rows)
                decisions_copy["variant_name"] = str(lane["variant_name"])
                decisions_copy["risk_profile_name"] = lane_risk_profile["risk_profile_name"]
                postseason_steps.append(steps_copy)
                postseason_decisions.append(decisions_copy)

    postseason_seed_df = pd.DataFrame(postseason_rows)
    postseason_seed_df.to_csv(output_dir / "postseason_seed_reference_detail.csv", index=False)
    postseason_variant_df = _build_postseason_variant_rows(postseason_seed_df)
    postseason_variant_df.to_csv(output_dir / "postseason_variant_summary.csv", index=False)
    non_empty_steps = [frame for frame in postseason_steps if not frame.empty]
    non_empty_decisions = [frame for frame in postseason_decisions if not frame.empty]
    if non_empty_steps:
        pd.concat(non_empty_steps, ignore_index=True).to_csv(output_dir / "postseason_reference_steps_seed0.csv", index=False)
    if non_empty_decisions:
        pd.concat(non_empty_decisions, ignore_index=True).to_csv(output_dir / "postseason_reference_decisions_seed0.csv", index=False)

    _write_report(
        output_dir,
        risk_ranked_df=regular_risk_ranked_df,
        postseason_seed_df=postseason_seed_df,
        postseason_variant_df=postseason_variant_df,
        selected_risk_profile_name=selected_risk_profile_name,
        llm_budget_spent=float(budget_state.spent_usd),
    )
    metadata = {
        "season": args.season,
        "analysis_version": args.analysis_version,
        "llm_model": args.llm_model,
        "selected_risk_profile": selected_risk_profile_name,
        "llm_budget_spent_usd": float(budget_state.spent_usd),
        "output_dir": str(output_dir),
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
