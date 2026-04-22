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
    _load_llm_cache,
    _resolve_openai_client,
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
POSTSEASON_SLIPPAGE_SEEDS = tuple(BASE_RANDOM_SLIPPAGE_SEED + offset for offset in range(6))
CORE_FAMILIES = tuple(DEFAULT_MASTER_ROUTER_CORE_FAMILIES)
EXTRA_FAMILIES = tuple(DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES)

MASTER_VARIANT_NAME = "master_strategy_router_same_side_top1_conf60_v1"
UNIFIED_VARIANT_NAME = "unified_router_llm_meta_conf60_llm60_skip_v1"

STOP_VARIANTS: tuple[dict[str, Any], ...] = (
    {"variant_name": "baseline", "stop_map": {}},
    {"variant_name": "inv_stop_8c", "stop_map": {"inversion": 0.08}},
    {"variant_name": "inv_stop_6c", "stop_map": {"inversion": 0.06}},
    {"variant_name": "inv_stop_5c", "stop_map": {"inversion": 0.05}},
    {"variant_name": "wd_stop_6c", "stop_map": {"winner_definition": 0.06}},
    {"variant_name": "wd_stop_5c", "stop_map": {"winner_definition": 0.05}},
    {"variant_name": "inv8_wd6", "stop_map": {"inversion": 0.08, "winner_definition": 0.06}},
    {"variant_name": "inv6_wd5", "stop_map": {"inversion": 0.06, "winner_definition": 0.05}},
)

LLM_LANE = {
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyse stop-loss overlays on routed NBA finalists.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument(
        "--output-dir",
        default=r"C:\code-personal\janus-local\janus_cortex\archives\output\nba_analysis_stop_overlay",
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


def _make_request(
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    output_root: str,
    llm_model: str,
    llm_max_budget_usd: float,
    season_phases: tuple[str, ...] | None = None,
    random_slippage_seed: int = BASE_RANDOM_SLIPPAGE_SEED,
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
        portfolio_random_slippage_seed=random_slippage_seed,
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


def _build_state_lookup(state_df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    work = state_df.copy()
    work["game_id"] = work["game_id"].astype(str)
    work["team_side"] = work["team_side"].astype(str)
    work["state_index"] = pd.to_numeric(work["state_index"], errors="coerce")
    work["team_price"] = pd.to_numeric(work["team_price"], errors="coerce")
    work["event_at"] = pd.to_datetime(work["event_at"], errors="coerce", utc=True)
    return {
        (str(game_id), str(team_side)): group.sort_values("state_index", kind="mergesort").reset_index(drop=True)
        for (game_id, team_side), group in work.groupby(["game_id", "team_side"], sort=False)
    }


def _apply_stop_overlay(
    trades_df: pd.DataFrame,
    *,
    state_lookup: dict[tuple[str, str], pd.DataFrame],
    stop_map: dict[str, float],
) -> pd.DataFrame:
    if trades_df.empty or not stop_map:
        return trades_df.copy()
    overlay_rows: list[dict[str, Any]] = []
    for record in trades_df.to_dict(orient="records"):
        family = str(record.get("source_strategy_family") or record.get("strategy_family") or "")
        stop_cents = stop_map.get(family)
        if stop_cents is None:
            overlay_rows.append(record)
            continue
        key = (str(record.get("game_id") or ""), str(record.get("team_side") or ""))
        group = state_lookup.get(key)
        if group is None:
            overlay_rows.append(record)
            continue
        entry_state_index = int(record.get("entry_state_index") or 0)
        exit_state_index = int(record.get("exit_state_index") or entry_state_index)
        future = group[
            (group["state_index"] > entry_state_index) & (group["state_index"] <= exit_state_index)
        ].copy()
        stop_price = max(0.01, float(record.get("entry_price") or 0.0) - float(stop_cents))
        stop_hit = future[future["team_price"] <= stop_price]
        if stop_hit.empty:
            overlay_rows.append(record)
            continue
        stop_row = stop_hit.iloc[0]
        updated = dict(record)
        entry_price = float(updated.get("entry_price") or 0.0)
        exit_price = float(stop_row["team_price"])
        slippage = max(0.0, int(updated.get("slippage_cents") or 0) / 100.0)
        entry_exec = min(0.999999, entry_price + slippage)
        exit_exec = max(0.0, exit_price - slippage)
        updated["exit_state_index"] = int(stop_row["state_index"])
        updated["exit_at"] = pd.to_datetime(stop_row["event_at"], utc=True)
        updated["exit_price"] = exit_price
        updated["gross_return"] = ((exit_price - entry_price) / entry_price) if entry_price > 0 else 0.0
        updated["gross_return_with_slippage"] = ((exit_exec - entry_exec) / entry_exec) if entry_exec > 0 else 0.0
        updated["hold_time_seconds"] = (
            pd.to_datetime(updated["exit_at"], utc=True) - pd.to_datetime(updated["entry_at"], utc=True)
        ).total_seconds()
        updated["exit_rule"] = f"{updated.get('exit_rule') or ''} + overlay_stop_{int(round(stop_cents * 100))}c".strip()
        overlay_rows.append(updated)
    return pd.DataFrame(overlay_rows, columns=trades_df.columns)


def _simulate_controller(
    trades_df: pd.DataFrame,
    *,
    request: BacktestRunRequest,
    strategy_family: str,
) -> dict[str, Any]:
    summary, _ = simulate_trade_portfolio(
        trades_df,
        sample_name="postseason_final_20",
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
        random_slippage_seed=request.portfolio_random_slippage_seed,
    )
    return summary


def _aggregate(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby(["controller_name", "variant_name"], dropna=False)
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
        )
        .reset_index()
        .sort_values(["controller_name", "mean_ending_bankroll"], ascending=[True, False], kind="mergesort")
        .reset_index(drop=True)
    )


def _write_report(output_dir: Path, aggregate_df: pd.DataFrame) -> None:
    lines = [
        "# Stop overlay analysis",
        "",
        "Trade-level overlay stops tested on postseason finalists. These overlays sit on top of the existing family exits.",
        "",
    ]
    for controller_name, controller_df in aggregate_df.groupby("controller_name", sort=False):
        lines.append(f"## {controller_name}")
        lines.append("")
        for record in controller_df.to_dict(orient="records"):
            lines.extend(
                [
                    f"### {record['variant_name']}",
                    f"- mean ending bankroll: `${record['mean_ending_bankroll']:.2f}`",
                    f"- median ending bankroll: `${record['median_ending_bankroll']:.2f}`",
                    f"- mean max drawdown: `{record['mean_max_drawdown_pct']:.2%}` / `${record['mean_max_drawdown_amount']:.2f}`",
                    f"- mean minimum bankroll: `${record['mean_min_bankroll']:.2f}`",
                    f"- mean trade count: `{record['mean_trade_count']:.2f}`",
                    "",
                ]
            )
    (output_dir / "stop_overlay_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve() / args.season / "postseason_stop_overlay"
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
        team_profiles_df = _query_df(
            connection,
            """
            SELECT *
            FROM nba.nba_analysis_team_season_profiles
            WHERE season = %s AND season_phase = 'regular_season' AND analysis_version = %s
            ORDER BY team_slug ASC;
            """,
            (args.season, args.analysis_version),
        )

    regular_result = _load_result(regular_request)
    postseason_result = _load_result(postseason_request)
    state_lookup = _build_state_lookup(postseason_result.state_df)
    priors = build_master_router_selection_priors(regular_result, core_strategy_families=CORE_FAMILIES)

    master_trades_df, _ = build_master_router_trade_frame(
        postseason_result,
        sample_name="postseason_final_20",
        selection_sample_name="regular_full",
        priors=priors,
        core_strategy_families=CORE_FAMILIES,
        extra_strategy_families=EXTRA_FAMILIES,
        extra_selection_mode="same_side_top1",
        min_core_confidence_for_extras=0.60,
    )

    family_profiles = _build_family_profiles(
        regular_result,
        registry=regular_result.strategy_registry,
        strategy_families=tuple(dict.fromkeys([*CORE_FAMILIES, *EXTRA_FAMILIES])),
        core_strategy_families=CORE_FAMILIES,
    )
    client = _resolve_openai_client()
    cache_store = _load_llm_cache(output_dir / "llm_router_cache.json")
    budget_state = _LLMBudgetState()
    historical_context_lookup = build_team_profile_context_lookup(team_profiles_df)
    unified_trades_df, _unified_decisions_df, _ = build_unified_router_trade_frame(
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
        historical_team_context_lookup=historical_context_lookup,
        extra_selection_mode="same_side_top1",
        min_core_confidence_for_extras=0.60,
        weak_confidence_threshold=0.60,
        llm_accept_confidence=0.60,
        skip_weak_when_llm_empty=True,
        skip_weak_when_llm_low_confidence=True,
    )

    controller_frames = {
        MASTER_VARIANT_NAME: master_trades_df,
        UNIFIED_VARIANT_NAME: unified_trades_df,
    }
    summary_rows: list[dict[str, Any]] = []
    for controller_name, base_frame in controller_frames.items():
        for variant in STOP_VARIANTS:
            variant_name = str(variant["variant_name"])
            stop_map = dict(variant.get("stop_map") or {})
            overlay_frame = _apply_stop_overlay(base_frame, state_lookup=state_lookup, stop_map=stop_map)
            for slippage_seed in POSTSEASON_SLIPPAGE_SEEDS:
                request = _make_request(
                    season=args.season,
                    season_phase="postseason_final_20",
                    season_phases=("play_in", "playoffs"),
                    analysis_version=args.analysis_version,
                    output_root=str(output_dir),
                    llm_model=args.llm_model,
                    llm_max_budget_usd=args.llm_budget_usd,
                    random_slippage_seed=slippage_seed,
                )
                summary = _simulate_controller(
                    overlay_frame,
                    request=request,
                    strategy_family=f"{controller_name} :: {variant_name}",
                )
                summary_rows.append(
                    {
                        "controller_name": controller_name,
                        "variant_name": variant_name,
                        "slippage_seed": int(slippage_seed),
                        "ending_bankroll": float(summary.get("ending_bankroll") or 0.0),
                        "compounded_return": float(summary.get("compounded_return") or 0.0),
                        "max_drawdown_pct": float(summary.get("max_drawdown_pct") or 0.0),
                        "max_drawdown_amount": float(summary.get("max_drawdown_amount") or 0.0),
                        "min_bankroll": float(summary.get("min_bankroll") or 0.0),
                        "executed_trade_count": int(summary.get("executed_trade_count") or 0),
                    }
                )

    detail_df = pd.DataFrame(summary_rows)
    aggregate_df = _aggregate(detail_df)
    detail_df.to_csv(output_dir / "stop_overlay_seed_summary.csv", index=False)
    aggregate_df.to_csv(output_dir / "stop_overlay_aggregate_summary.csv", index=False)
    _write_report(output_dir, aggregate_df)
    (output_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "season": args.season,
                "analysis_version": args.analysis_version,
                "llm_model": args.llm_model,
                "output_dir": str(output_dir),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
