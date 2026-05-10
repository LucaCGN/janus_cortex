from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (  # noqa: E402
    PORTFOLIO_SCOPE_ROUTED,
    PORTFOLIO_SCOPE_SINGLE_FAMILY,
    simulate_trade_portfolio,
)
from app.runtime.local_paths import resolve_shared_root  # noqa: E402


DEFAULT_SHARED_ROOT = resolve_shared_root()
DEFAULT_SEASON = "2025-26"
DEFAULT_SAMPLE_SIZES = (10, 50, 100)
DEFAULT_SEEDS = (1107, 2113, 3251, 4421, 5573)
DEFAULT_INITIAL_BANKROLL = 10.0
DEFAULT_POSITION_SIZE_FRACTION = 0.10
DEFAULT_MIN_ORDER_DOLLARS = 1.0
DEFAULT_MIN_SHARES = 5.0
DEFAULT_MAX_CONCURRENT_POSITIONS = 2
BACKBONE_FAMILIES = (
    "underdog_range_scalp",
    "favorite_floor_rebound",
    "panic_fade_fast",
    "q4_clutch",
    "q1_repricing",
    "underdog_liftoff",
    "inversion",
    "quarter_open_reprice",
    "micro_momentum_continuation",
    "lead_fragility",
    "halftime_gap_fill",
    "winner_definition",
)
ORDERBOOK_REQUIRED_FAMILIES = (
    "ultra_low_orderbook_grid",
    "inventory_aware_bracket_manager",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample replay artifacts across random game slices and summarize strategy backbone candidates."
    )
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument(
        "--artifact-name",
        default="full_regular_execution_replay_v2_grid",
        help="Replay artifact under shared/artifacts/replay-engine-hf/<season>.",
    )
    parser.add_argument(
        "--fallback-artifact-name",
        default="full_regular_execution_replay_v1",
        help="Artifact used if --artifact-name does not exist.",
    )
    parser.add_argument(
        "--supplement-artifact-name",
        action="append",
        default=[],
        help="Additional replay artifact under shared/artifacts/replay-engine-hf/<season>; later artifacts override duplicate subject frames.",
    )
    parser.add_argument("--output-name", default="backbone_sample_analysis_v1")
    parser.add_argument("--sample-size", action="append", type=int, default=[])
    parser.add_argument("--seed", action="append", type=int, default=[])
    parser.add_argument("--initial-bankroll", type=float, default=DEFAULT_INITIAL_BANKROLL)
    parser.add_argument("--position-size-fraction", type=float, default=DEFAULT_POSITION_SIZE_FRACTION)
    parser.add_argument("--min-order-dollars", type=float, default=DEFAULT_MIN_ORDER_DOLLARS)
    parser.add_argument("--min-shares", type=float, default=DEFAULT_MIN_SHARES)
    parser.add_argument("--max-concurrent-positions", type=int, default=DEFAULT_MAX_CONCURRENT_POSITIONS)
    return parser.parse_args()


def _artifact_root(shared_root: Path, season: str, artifact_name: str, fallback_artifact_name: str) -> Path:
    requested = shared_root / "artifacts" / "replay-engine-hf" / season / artifact_name
    if requested.exists():
        return requested
    fallback = shared_root / "artifacts" / "replay-engine-hf" / season / fallback_artifact_name
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"No replay artifact found at {requested} or {fallback}")


def _subject_stem(subject_name: str) -> str:
    return str(subject_name).replace(" ", "_").replace("::", "__").replace("/", "_")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _load_subject_names(artifact_roots: list[Path]) -> list[str]:
    names: set[str] = set()
    for artifact_root in artifact_roots:
        summary = _read_csv(artifact_root / "replay_subject_summary.csv")
        if not summary.empty and "subject_name" in summary.columns:
            names.update(str(value) for value in summary["subject_name"].dropna().tolist())
            continue
        for path in list(artifact_root.glob("replay_*.csv")) + list(artifact_root.glob("standard_*.csv")):
            name = path.stem.removeprefix("replay_").removeprefix("standard_")
            if name in {
                "attempt_trace",
                "blocker_summary",
                "candidate_lifecycle",
                "candidate_ranking",
                "divergence_summary",
                "game_gap",
                "game_manifest",
                "live_summary",
                "portfolio_summary",
                "promotion_table",
                "quarter_summary",
                "run",
                "signal_summary",
                "slate_expectation",
                "subject_summary",
                "window_summary",
            }:
                continue
            names.add(name)
    return sorted(names)


def _load_trade_frames(artifact_roots: list[Path], subject_names: list[str]) -> dict[tuple[str, str], pd.DataFrame]:
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for subject_name in subject_names:
        stem = _subject_stem(subject_name)
        for mode in ("standard", "replay"):
            frame = pd.DataFrame()
            for artifact_root in artifact_roots:
                candidate = _read_csv(artifact_root / f"{mode}_{stem}.csv")
                if not candidate.empty:
                    frame = candidate
            if not frame.empty and "game_id" in frame.columns:
                frame = frame.copy()
                frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
                for column in ("entry_at", "exit_at"):
                    if column in frame.columns:
                        frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
            frames[(mode, subject_name)] = frame
    return frames


def _portfolio_scope(subject_name: str) -> str:
    return PORTFOLIO_SCOPE_ROUTED if "::" in subject_name else PORTFOLIO_SCOPE_SINGLE_FAMILY


def _sample_summary(
    frame: pd.DataFrame,
    *,
    subject_name: str,
    mode: str,
    sample_size: int,
    seed: int,
    game_ids: set[str],
    initial_bankroll: float,
    position_size_fraction: float,
    min_order_dollars: float,
    min_shares: float,
    max_concurrent_positions: int,
) -> dict[str, Any]:
    if frame.empty:
        sample = frame.copy()
    else:
        sample = frame[frame["game_id"].astype(str).str.zfill(10).isin(game_ids)].copy()
    summary, steps = simulate_trade_portfolio(
        sample,
        sample_name=f"{mode}_{sample_size}_{seed}",
        strategy_family=subject_name,
        portfolio_scope=_portfolio_scope(subject_name),
        strategy_family_members=(subject_name,),
        initial_bankroll=initial_bankroll,
        position_size_fraction=position_size_fraction,
        game_limit=None,
        min_order_dollars=min_order_dollars,
        min_shares=min_shares,
        max_concurrent_positions=max_concurrent_positions,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="static",
        target_exposure_fraction=0.80,
        random_slippage_max_cents=0,
        random_slippage_seed=seed,
    )
    executed = steps[steps["portfolio_action"] == "executed"].copy() if not steps.empty else pd.DataFrame()
    executed_returns = pd.to_numeric(executed.get("gross_return_with_slippage"), errors="coerce") if not executed.empty else pd.Series(dtype=float)
    return {
        "subject_name": subject_name,
        "subject_type": "controller" if "::" in subject_name else "family",
        "mode": mode,
        "sample_size": int(sample_size),
        "seed": int(seed),
        "sample_game_count": int(len(game_ids)),
        "signal_trade_count": int(len(sample)),
        "executed_trade_count": int(summary.get("executed_trade_count") or 0),
        "ending_bankroll": _safe_float(summary.get("ending_bankroll")),
        "total_pnl_amount": _safe_float(summary.get("total_pnl_amount")),
        "compounded_return": _safe_float(summary.get("compounded_return")),
        "max_drawdown_pct": _safe_float(summary.get("max_drawdown_pct")),
        "avg_executed_trade_return": _safe_float(summary.get("avg_executed_trade_return_with_slippage")),
        "executed_win_rate": float((executed_returns > 0).mean()) if not executed_returns.empty else None,
        "skipped_min_order_count": int(summary.get("skipped_min_order_count") or 0),
        "skipped_concurrency_count": int(summary.get("skipped_concurrency_count") or 0),
    }


def _aggregate_sample_results(sample_df: pd.DataFrame, *, initial_bankroll: float) -> pd.DataFrame:
    if sample_df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    grouped = sample_df.groupby(["subject_name", "subject_type", "mode", "sample_size"], dropna=False)
    for (subject_name, subject_type, mode, sample_size), group in grouped:
        ending = pd.to_numeric(group["ending_bankroll"], errors="coerce").dropna()
        executed = pd.to_numeric(group["executed_trade_count"], errors="coerce").fillna(0)
        drawdown = pd.to_numeric(group["max_drawdown_pct"], errors="coerce").dropna()
        rows.append(
            {
                "subject_name": subject_name,
                "subject_type": subject_type,
                "mode": mode,
                "sample_size": int(sample_size),
                "sample_count": int(len(group)),
                "mean_ending_bankroll": float(ending.mean()) if not ending.empty else None,
                "median_ending_bankroll": float(ending.median()) if not ending.empty else None,
                "min_ending_bankroll": float(ending.min()) if not ending.empty else None,
                "p10_ending_bankroll": float(ending.quantile(0.10)) if len(ending) else None,
                "p90_ending_bankroll": float(ending.quantile(0.90)) if len(ending) else None,
                "positive_sample_rate": float((ending > initial_bankroll).mean()) if not ending.empty else None,
                "mean_executed_trade_count": float(executed.mean()) if len(executed) else 0.0,
                "mean_max_drawdown_pct": float(drawdown.mean()) if not drawdown.empty else None,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["mode", "sample_size", "median_ending_bankroll", "mean_ending_bankroll"],
        ascending=[True, True, False, False],
        kind="mergesort",
    )


def _recommend_backbone(aggregate_df: pd.DataFrame, subject_names: list[str], *, initial_bankroll: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    replay_100 = aggregate_df[(aggregate_df["mode"] == "replay") & (aggregate_df["sample_size"] == 100)].copy()
    replay_50 = aggregate_df[(aggregate_df["mode"] == "replay") & (aggregate_df["sample_size"] == 50)].copy()
    standard_100 = aggregate_df[(aggregate_df["mode"] == "standard") & (aggregate_df["sample_size"] == 100)].copy()
    standard_50 = aggregate_df[(aggregate_df["mode"] == "standard") & (aggregate_df["sample_size"] == 50)].copy()
    lookup_100 = {str(row["subject_name"]): row for row in replay_100.to_dict(orient="records")}
    lookup_50 = {str(row["subject_name"]): row for row in replay_50.to_dict(orient="records")}
    standard_lookup_100 = {str(row["subject_name"]): row for row in standard_100.to_dict(orient="records")}
    standard_lookup_50 = {str(row["subject_name"]): row for row in standard_50.to_dict(orient="records")}
    for family in BACKBONE_FAMILIES:
        replay_row_100 = lookup_100.get(family) or {}
        standard_row_100 = standard_lookup_100.get(family) or {}
        replay_trades_100 = _safe_float(replay_row_100.get("mean_executed_trade_count")) or 0.0
        standard_trades_100 = _safe_float(standard_row_100.get("mean_executed_trade_count")) or 0.0
        evidence_mode = "replay" if replay_trades_100 > 0 or standard_trades_100 <= 0 else "standard"
        row_100 = (replay_row_100 if evidence_mode == "replay" else standard_row_100) or {}
        row_50 = (lookup_50.get(family) if evidence_mode == "replay" else standard_lookup_50.get(family)) or {}
        present = family in subject_names
        median_100 = _safe_float(row_100.get("median_ending_bankroll"))
        positive_100 = _safe_float(row_100.get("positive_sample_rate"))
        mean_trades_100 = _safe_float(row_100.get("mean_executed_trade_count")) or 0.0
        median_50 = _safe_float(row_50.get("median_ending_bankroll"))
        if not present:
            role = "needs_replay_artifact"
            reason = "family_not_present_in_selected_replay_artifact"
        elif evidence_mode == "standard" and ((median_100 or 0.0) <= initial_bankroll or (positive_100 or 0.0) < 0.50):
            role = "rework_required"
            reason = "current_rule_loses_on_standard_samples_before_replay_validation"
        elif evidence_mode == "standard":
            role = "standard_screen_only"
            reason = "profitable_standard_screen_but_needs_replay_poll_orderbook_validation"
        elif mean_trades_100 <= 0:
            role = "bench_only"
            reason = "no_replay_executions_in_100_game_samples"
        elif (median_100 or 0.0) > initial_bankroll and (positive_100 or 0.0) >= 0.55 and (median_50 or 0.0) >= initial_bankroll:
            role = "backbone_candidate"
            reason = "profitable_median_replay_samples_with_positive_sample_rate"
        elif (median_100 or 0.0) > initial_bankroll or (positive_100 or 0.0) >= 0.45:
            role = "shadow_backbone_candidate"
            reason = "useful_but_unstable_replay_sample_profile"
        else:
            role = "bench_or_context_only"
            reason = "replay_samples_do_not_clear_bankroll_stability"
        rows.append(
            {
                "strategy_family": family,
                "present_in_artifact": bool(present),
                "recommended_role": role,
                "reason": reason,
                "evidence_mode": evidence_mode if present else None,
                "sample_100_median_bankroll": median_100,
                "sample_100_positive_rate": positive_100,
                "sample_100_mean_executed_trades": mean_trades_100,
                "sample_50_median_bankroll": median_50,
            }
        )
    for family in ORDERBOOK_REQUIRED_FAMILIES:
        rows.append(
            {
                "strategy_family": family,
                "present_in_artifact": False,
                "recommended_role": "engine_requirement",
                "reason": "requires_orderbook_tick_replay_and_inventory_bracket_simulation",
                "evidence_mode": None,
                "sample_100_median_bankroll": None,
                "sample_100_positive_rate": None,
                "sample_100_mean_executed_trades": None,
                "sample_50_median_bankroll": None,
            }
        )
    return pd.DataFrame(rows)


def _format_float(value: Any, digits: int = 3) -> str:
    resolved = _safe_float(value)
    if resolved is None or math.isnan(resolved):
        return ""
    return f"{resolved:.{digits}f}"


def _render_markdown(
    *,
    artifact_roots: list[Path],
    output_dir: Path,
    manifest_df: pd.DataFrame,
    aggregate_df: pd.DataFrame,
    recommendation_df: pd.DataFrame,
    initial_bankroll: float,
) -> str:
    lines = [
        "# Strategy Backbone Sample Analysis",
        "",
        f"- Generated at `{datetime.now(timezone.utc).isoformat()}`.",
        f"- Source artifacts: `{', '.join(str(root) for root in artifact_roots)}`.",
        f"- Games in manifest: `{manifest_df['game_id'].nunique() if not manifest_df.empty else 0}`.",
        f"- Portfolio assumptions: `${initial_bankroll:.2f}` bankroll, `$1.00` minimum order, `5` share minimum, `2` concurrent positions.",
        "",
        "## Interpretation Guardrails",
        "",
        "- These results use the replay artifact's state-panel/poll simulation, not a true 1-3s CLOB tick stream.",
        "- `standard` evidence means the strategy fired on the historical state panel but has not cleared replay-poll/orderbook execution.",
        "- Orderbook-grid families need a new replay mode with direct CLOB ticks, grouped brackets, and inventory reconciliation before promotion.",
        "- The sample tables are useful for ranking backbone families, not for claiming production profitability.",
        "",
        "## Replay 100-Game Sample Leaders",
        "",
        "| Subject | Median | Mean | P10 | P90 | Positive Rate | Mean Trades |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    top = aggregate_df[(aggregate_df["mode"] == "replay") & (aggregate_df["sample_size"] == 100)].head(12)
    for row in top.to_dict(orient="records"):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.get('subject_name')}`",
                    _format_float(row.get("median_ending_bankroll")),
                    _format_float(row.get("mean_ending_bankroll")),
                    _format_float(row.get("p10_ending_bankroll")),
                    _format_float(row.get("p90_ending_bankroll")),
                    _format_float(row.get("positive_sample_rate"), 2),
                    _format_float(row.get("mean_executed_trade_count"), 2),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Standard 100-Game Sample Screens",
            "",
            "| Subject | Median | Mean | P10 | P90 | Positive Rate | Mean Trades |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    top_standard = aggregate_df[(aggregate_df["mode"] == "standard") & (aggregate_df["sample_size"] == 100)].head(14)
    for row in top_standard.to_dict(orient="records"):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.get('subject_name')}`",
                    _format_float(row.get("median_ending_bankroll")),
                    _format_float(row.get("mean_ending_bankroll")),
                    _format_float(row.get("p10_ending_bankroll")),
                    _format_float(row.get("p90_ending_bankroll")),
                    _format_float(row.get("positive_sample_rate"), 2),
                    _format_float(row.get("mean_executed_trade_count"), 2),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Backbone Recommendation",
            "",
            "| Strategy | Role | Evidence | 100-Game Median | Positive Rate | Mean Trades | Reason |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in recommendation_df.to_dict(orient="records"):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.get('strategy_family')}`",
                    f"`{row.get('recommended_role')}`",
                    f"`{row.get('evidence_mode') or ''}`",
                    _format_float(row.get("sample_100_median_bankroll")),
                    _format_float(row.get("sample_100_positive_rate"), 2),
                    _format_float(row.get("sample_100_mean_executed_trades"), 2),
                    str(row.get("reason") or ""),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- Sample rows: `{output_dir / 'strategy_sample_results.csv'}`",
            f"- Aggregates: `{output_dir / 'strategy_sample_aggregate.csv'}`",
            f"- Backbone recommendation: `{output_dir / 'strategy_backbone_recommendation.csv'}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    shared_root = Path(args.shared_root).expanduser().resolve()
    artifact_root = _artifact_root(shared_root, args.season, args.artifact_name, args.fallback_artifact_name)
    artifact_roots = [artifact_root]
    for artifact_name in args.supplement_artifact_name:
        supplement = shared_root / "artifacts" / "replay-engine-hf" / args.season / str(artifact_name).strip()
        if supplement.exists():
            artifact_roots.append(supplement)
    output_dir = shared_root / "artifacts" / "replay-engine-hf" / args.season / args.output_name
    report_dir = shared_root / "reports" / "replay-engine-hf"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    manifest_df = _read_csv(artifact_root / "replay_game_manifest.csv")
    if manifest_df.empty or "game_id" not in manifest_df.columns:
        raise ValueError(f"Replay game manifest is missing or empty: {artifact_root / 'replay_game_manifest.csv'}")
    manifest_df = manifest_df.copy()
    manifest_df["game_id"] = manifest_df["game_id"].astype(str).str.zfill(10)
    all_game_ids = sorted(manifest_df["game_id"].dropna().unique().tolist())
    subject_names = _load_subject_names(artifact_roots)
    frames = _load_trade_frames(artifact_roots, subject_names)
    sample_sizes = tuple(args.sample_size or DEFAULT_SAMPLE_SIZES)
    seeds = tuple(args.seed or DEFAULT_SEEDS)

    sample_rows: list[dict[str, Any]] = []
    for sample_size in sample_sizes:
        resolved_size = min(int(sample_size), len(all_game_ids))
        for seed in seeds:
            sample_games = set(pd.Series(all_game_ids).sample(n=resolved_size, random_state=int(seed)).tolist())
            for subject_name in subject_names:
                for mode in ("standard", "replay"):
                    sample_rows.append(
                        _sample_summary(
                            frames.get((mode, subject_name), pd.DataFrame()),
                            subject_name=subject_name,
                            mode=mode,
                            sample_size=resolved_size,
                            seed=int(seed),
                            game_ids=sample_games,
                            initial_bankroll=float(args.initial_bankroll),
                            position_size_fraction=float(args.position_size_fraction),
                            min_order_dollars=float(args.min_order_dollars),
                            min_shares=float(args.min_shares),
                            max_concurrent_positions=int(args.max_concurrent_positions),
                        )
                    )
    sample_df = pd.DataFrame(sample_rows)
    aggregate_df = _aggregate_sample_results(sample_df, initial_bankroll=float(args.initial_bankroll))
    recommendation_df = _recommend_backbone(aggregate_df, subject_names, initial_bankroll=float(args.initial_bankroll))

    sample_df.to_csv(output_dir / "strategy_sample_results.csv", index=False)
    aggregate_df.to_csv(output_dir / "strategy_sample_aggregate.csv", index=False)
    recommendation_df.to_csv(output_dir / "strategy_backbone_recommendation.csv", index=False)
    manifest_df.to_csv(output_dir / "source_game_manifest.csv", index=False)
    _write_json(
        output_dir / "run_summary.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_artifacts": [str(root) for root in artifact_roots],
            "output_dir": str(output_dir),
            "sample_sizes": list(sample_sizes),
            "seeds": list(seeds),
            "game_count": len(all_game_ids),
            "subject_count": len(subject_names),
            "portfolio": {
                "initial_bankroll": float(args.initial_bankroll),
                "position_size_fraction": float(args.position_size_fraction),
                "min_order_dollars": float(args.min_order_dollars),
                "min_shares": float(args.min_shares),
                "max_concurrent_positions": int(args.max_concurrent_positions),
            },
        },
    )
    markdown = _render_markdown(
        artifact_roots=artifact_roots,
        output_dir=output_dir,
        manifest_df=manifest_df,
        aggregate_df=aggregate_df,
        recommendation_df=recommendation_df,
        initial_bankroll=float(args.initial_bankroll),
    )
    (report_dir / "strategy_backbone_sample_analysis.md").write_text(markdown, encoding="utf-8")
    print(
        json.dumps(
            {
                "source_artifacts": [str(root) for root in artifact_roots],
                "output_dir": str(output_dir),
                "report": str(report_dir / "strategy_backbone_sample_analysis.md"),
                "game_count": len(all_game_ids),
                "subject_count": len(subject_names),
                "sample_rows": len(sample_df),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
