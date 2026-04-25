from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.backtests.replay import (
    run_postseason_execution_replay,
    write_replay_artifacts,
)
from app.data.pipelines.daily.nba.analysis.contracts import ReplayRunRequest


DEFAULT_ARTIFACTS_ROOT = Path(r"C:\code-personal\janus-local\janus_cortex\shared\artifacts\replay-engine-hf")
DEFAULT_REPORTS_ROOT = Path(r"C:\code-personal\janus-local\janus_cortex\shared\reports\replay-engine-hf")
DEFAULT_LIVE_RUN_IDS = ("live-2026-04-23-v1",)
REPLAY_ENGINE_LANE_ID = "replay-engine-hf"
LOCKED_BASELINE_SUBJECTS = (
    "controller_vnext_unified_v1 :: balanced",
    "controller_vnext_deterministic_v1 :: tight",
)
PRIORITY_HF_FAMILIES = (
    "quarter_open_reprice",
    "halftime_gap_fill",
    "micro_momentum_continuation",
    "lead_fragility",
    "panic_fade_fast",
)
PROMOTION_ORDER = {
    "live_probe": 0,
    "shadow_only": 1,
    "bench": 2,
    "locked_baseline": 3,
}
RANKING_COLUMNS = (
    "subject_name",
    "subject_type",
    "candidate_bucket",
    "priority_focus_flag",
    "standard_trade_count",
    "replay_trade_count",
    "standard_trades_per_game",
    "replay_trades_per_game",
    "replay_survival_rate",
    "stale_signal_count",
    "stale_signal_rate",
    "cadence_blocked_count",
    "cadence_blocked_rate",
    "replay_ending_bankroll",
    "replay_compounded_return",
    "replay_max_drawdown_pct",
    "replay_max_drawdown_amount",
    "replay_path_quality_score",
    "top_no_trade_reason",
    "live_trade_count",
    "live_test_recommendation",
    "probe_priority_rank",
    "replay_rank",
    "focus_rank",
)
QUOTE_SOURCE_COMPARISON_COLUMNS = (
    "subject_name",
    "subject_type",
    "proxy_replay_rank",
    "bidask_replay_rank",
    "rank_delta",
    "proxy_replay_trade_count",
    "bidask_replay_trade_count",
    "trade_count_delta",
    "proxy_replay_survival_rate",
    "bidask_replay_survival_rate",
    "survival_rate_delta",
    "proxy_replay_ending_bankroll",
    "bidask_replay_ending_bankroll",
    "bankroll_delta",
    "proxy_live_test_recommendation",
    "bidask_live_test_recommendation",
    "shortlist_changed_flag",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run execution-aware postseason replay analysis.")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument("--artifacts-root", default=str(DEFAULT_ARTIFACTS_ROOT))
    parser.add_argument("--reports-root", default=str(DEFAULT_REPORTS_ROOT))
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--signal-max-age-seconds", type=float, default=60.0)
    parser.add_argument("--quote-max-age-seconds", type=float, default=30.0)
    parser.add_argument("--max-spread-cents", type=float, default=2.0)
    parser.add_argument("--proxy-min-spread-cents", type=float, default=1.0)
    parser.add_argument("--proxy-max-spread-cents", type=float, default=6.0)
    parser.add_argument("--aggressive-exit-slippage-cents", type=float, default=1.0)
    parser.add_argument("--quote-source-mode", default="historical_bidask_l1")
    parser.add_argument("--quote-source-fallback-mode", default="cross_side_last_trade")
    parser.add_argument("--compare-against-quote-source", default="cross_side_last_trade")
    parser.add_argument("--live-run-id", action="append", default=list(DEFAULT_LIVE_RUN_IDS))
    return parser.parse_args()


def _published_at_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shared_root_from_reports(reports_root: Path) -> Path:
    return reports_root.parents[1]


def _as_markdown_num(value: Any, *, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _clean_bool(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"nan", "none"}


def _subject_stem(subject_name: str) -> str:
    return subject_name.replace(" ", "_").replace("::", "__").replace("/", "_")


def _numeric_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    work = frame.copy()
    for column in columns:
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    return work


def _candidate_bucket(subject_name: str, subject_type: str) -> str:
    if subject_type == "controller":
        return "locked_controller" if subject_name in LOCKED_BASELINE_SUBJECTS else "controller"
    if subject_name in PRIORITY_HF_FAMILIES:
        return "priority_hf_family"
    return "legacy_family"


def _build_ranking_frame(subject_summary_df: pd.DataFrame) -> pd.DataFrame:
    if subject_summary_df.empty:
        return pd.DataFrame(columns=RANKING_COLUMNS)

    numeric_columns = (
        "standard_trade_count",
        "replay_trade_count",
        "standard_trades_per_game",
        "replay_trades_per_game",
        "replay_survival_rate",
        "stale_signal_count",
        "stale_signal_rate",
        "cadence_blocked_count",
        "cadence_blocked_rate",
        "replay_ending_bankroll",
        "replay_compounded_return",
        "replay_max_drawdown_pct",
        "replay_max_drawdown_amount",
        "replay_path_quality_score",
        "live_trade_count",
    )
    work = _numeric_columns(subject_summary_df, numeric_columns)
    work["candidate_bucket"] = [
        _candidate_bucket(str(subject_name), str(subject_type))
        for subject_name, subject_type in zip(work["subject_name"], work["subject_type"], strict=False)
    ]
    work["priority_focus_flag"] = work["subject_name"].astype(str).isin(PRIORITY_HF_FAMILIES)

    controller_rows = work[work["subject_type"].astype(str) == "controller"].copy()
    best_controller_survival = float(controller_rows["replay_survival_rate"].max()) if not controller_rows.empty else 0.0
    best_controller_stale = float(controller_rows["stale_signal_rate"].min()) if not controller_rows.empty else 1.0
    best_controller_cadence = float(controller_rows["cadence_blocked_rate"].min()) if not controller_rows.empty else 1.0
    best_controller_path = (
        float(controller_rows["replay_path_quality_score"].max()) if not controller_rows.empty else float("-inf")
    )

    recommendations: list[str] = []
    for row in work.to_dict(orient="records"):
        subject_name = str(row.get("subject_name") or "")
        subject_type = str(row.get("subject_type") or "")
        standard_trade_count = int(row.get("standard_trade_count") or 0)
        replay_trade_count = int(row.get("replay_trade_count") or 0)
        replay_survival_rate = float(row.get("replay_survival_rate") or 0.0)
        stale_signal_rate = float(row.get("stale_signal_rate") if pd.notna(row.get("stale_signal_rate")) else 1.0)
        cadence_blocked_rate = float(
            row.get("cadence_blocked_rate") if pd.notna(row.get("cadence_blocked_rate")) else 1.0
        )
        replay_path_quality_score = float(row.get("replay_path_quality_score") or 0.0)
        if subject_type == "controller":
            recommendations.append("locked_baseline")
            continue
        if standard_trade_count <= 0 or replay_trade_count <= 0:
            recommendations.append("bench")
            continue
        if (
            subject_name in PRIORITY_HF_FAMILIES
            and replay_survival_rate >= best_controller_survival
            and stale_signal_rate <= best_controller_stale
            and cadence_blocked_rate <= best_controller_cadence
            and replay_path_quality_score >= best_controller_path
        ):
            recommendations.append("live_probe")
            continue
        if (
            subject_name in PRIORITY_HF_FAMILIES
            and replay_survival_rate >= 0.25
            and stale_signal_rate <= 0.50
            and cadence_blocked_rate <= 0.25
            and replay_path_quality_score >= best_controller_path
        ):
            recommendations.append("live_probe")
            continue
        if replay_survival_rate >= best_controller_survival and replay_path_quality_score >= 0.0:
            recommendations.append("shadow_only")
            continue
        if subject_name in PRIORITY_HF_FAMILIES and replay_survival_rate >= 0.15 and replay_path_quality_score >= 0.0:
            recommendations.append("shadow_only")
            continue
        recommendations.append("bench")
    work["live_test_recommendation"] = recommendations
    work["promotion_order"] = [
        PROMOTION_ORDER.get(str(value or ""), 99) for value in work["live_test_recommendation"].tolist()
    ]
    priority_index = {name: index for index, name in enumerate(PRIORITY_HF_FAMILIES)}
    work["priority_probe_order"] = [
        priority_index.get(str(subject_name), len(PRIORITY_HF_FAMILIES))
        for subject_name in work["subject_name"].tolist()
    ]

    work = work.sort_values(
        [
            "promotion_order",
            "replay_ending_bankroll",
            "replay_survival_rate",
            "replay_path_quality_score",
            "stale_signal_rate",
            "cadence_blocked_rate",
            "replay_trade_count",
            "priority_probe_order",
            "subject_name",
        ],
        ascending=[True, False, False, False, True, True, False, True, True],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
    work["replay_rank"] = range(1, len(work) + 1)
    work["probe_priority_rank"] = pd.NA
    live_probe_mask = work["live_test_recommendation"].astype(str) == "live_probe"
    if live_probe_mask.any():
        work.loc[live_probe_mask, "probe_priority_rank"] = list(range(1, int(live_probe_mask.sum()) + 1))

    work["focus_rank"] = pd.NA
    focus_mask = work["priority_focus_flag"].fillna(False).astype(bool)
    if focus_mask.any():
        focus_ranks = range(1, int(focus_mask.sum()) + 1)
        work.loc[focus_mask, "focus_rank"] = list(focus_ranks)

    return work[list(RANKING_COLUMNS)]


def _build_focus_family_rows(ranking_df: pd.DataFrame) -> list[dict[str, Any]]:
    if ranking_df.empty:
        return []
    focus_rows = ranking_df[ranking_df["priority_focus_flag"].fillna(False).astype(bool)].copy()
    if focus_rows.empty:
        return []
    return focus_rows.sort_values(
        [
            "probe_priority_rank",
            "replay_ending_bankroll",
            "replay_survival_rate",
            "stale_signal_rate",
        ],
        ascending=[True, False, False, True],
        kind="mergesort",
        na_position="last",
    ).to_dict(orient="records")


def _build_stale_window_rows(window_summary_df: pd.DataFrame) -> list[dict[str, Any]]:
    if window_summary_df.empty:
        return []
    work = _numeric_columns(
        window_summary_df,
        (
            "signal_count",
            "replay_trade_count",
            "replay_survival_rate",
            "stale_signal_count",
            "stale_signal_rate",
            "cadence_blocked_count",
            "cadence_blocked_rate",
        ),
    )
    work = work[
        (work["stale_signal_count"].fillna(0) > 0)
        | (work["cadence_blocked_count"].fillna(0) > 0)
    ].copy()
    if work.empty:
        return []
    return work.sort_values(
        [
            "stale_signal_count",
            "cadence_blocked_count",
            "stale_signal_rate",
            "subject_name",
            "period_label",
            "entry_window_label",
        ],
        ascending=[False, False, False, True, True, True],
        kind="mergesort",
        na_position="last",
    ).head(8).to_dict(orient="records")


def _build_quarter_focus_rows(quarter_summary_df: pd.DataFrame, focus_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if quarter_summary_df.empty or not focus_rows:
        return []
    focus_names = {str(row.get("subject_name")) for row in focus_rows[:3]}
    work = quarter_summary_df[quarter_summary_df["subject_name"].astype(str).isin(focus_names)].copy()
    if work.empty:
        return []
    work = _numeric_columns(
        work,
        (
            "standard_trade_count",
            "replay_trade_count",
            "replay_survival_rate",
            "stale_signal_count",
            "stale_signal_rate",
            "replay_avg_return_with_slippage",
        ),
    )
    return work.sort_values(
        ["subject_name", "replay_survival_rate", "replay_trade_count", "period_label"],
        ascending=[True, False, False, True],
        kind="mergesort",
        na_position="last",
    ).groupby("subject_name", sort=False).head(2).to_dict(orient="records")


def _build_blocker_focus_rows(blocker_summary_df: pd.DataFrame, focus_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if blocker_summary_df.empty or not focus_rows:
        return []
    focus_names = {str(row.get("subject_name")) for row in focus_rows[:4]}
    work = blocker_summary_df[blocker_summary_df["subject_name"].astype(str).isin(focus_names)].copy()
    if work.empty:
        return []
    work = _numeric_columns(work, ("signal_count",))
    return work.sort_values(
        [
            "subject_name",
            "signal_count",
            "period_label",
            "entry_window_label",
            "replay_blocker_class",
        ],
        ascending=[True, False, True, True, True],
        kind="mergesort",
        na_position="last",
    ).groupby("subject_name", sort=False).head(2).to_dict(orient="records")


def _build_promotion_table_frame(ranking_df: pd.DataFrame) -> pd.DataFrame:
    columns = (
        "subject_name",
        "subject_type",
        "live_test_recommendation",
        "probe_priority_rank",
        "replay_survival_rate",
        "replay_trades_per_game",
        "stale_signal_rate",
        "cadence_blocked_rate",
        "replay_ending_bankroll",
        "replay_max_drawdown_pct",
        "replay_path_quality_score",
        "top_no_trade_reason",
    )
    if ranking_df.empty:
        return pd.DataFrame(columns=columns)
    work = ranking_df.copy()
    work = work.sort_values(
        [
            "probe_priority_rank",
            "live_test_recommendation",
            "replay_ending_bankroll",
            "replay_survival_rate",
            "subject_name",
        ],
        ascending=[True, True, False, False, True],
        kind="mergesort",
        na_position="last",
    )
    return work[list(columns)].reset_index(drop=True)


def _live_probe_subjects(ranking_df: pd.DataFrame) -> list[str]:
    if ranking_df.empty:
        return []
    work = ranking_df[ranking_df["live_test_recommendation"].astype(str) == "live_probe"].copy()
    if work.empty:
        return []
    return sorted(work["subject_name"].astype(str).tolist())


def _shortlist_change_summary(
    *,
    bidask_ranking_df: pd.DataFrame,
    proxy_ranking_df: pd.DataFrame,
) -> dict[str, Any]:
    bidask_set = set(_live_probe_subjects(bidask_ranking_df))
    proxy_set = set(_live_probe_subjects(proxy_ranking_df))
    return {
        "changed_flag": bidask_set != proxy_set,
        "bidask_live_probe_subjects": sorted(bidask_set),
        "proxy_live_probe_subjects": sorted(proxy_set),
        "added_subjects": sorted(bidask_set - proxy_set),
        "removed_subjects": sorted(proxy_set - bidask_set),
    }


def _build_quote_source_comparison_frame(
    *,
    bidask_ranking_df: pd.DataFrame,
    proxy_ranking_df: pd.DataFrame,
) -> pd.DataFrame:
    if bidask_ranking_df.empty and proxy_ranking_df.empty:
        return pd.DataFrame(columns=QUOTE_SOURCE_COMPARISON_COLUMNS)
    left = proxy_ranking_df.copy().rename(
        columns={
            "replay_rank": "proxy_replay_rank",
            "replay_trade_count": "proxy_replay_trade_count",
            "replay_survival_rate": "proxy_replay_survival_rate",
            "replay_ending_bankroll": "proxy_replay_ending_bankroll",
            "live_test_recommendation": "proxy_live_test_recommendation",
        }
    )
    right = bidask_ranking_df.copy().rename(
        columns={
            "replay_rank": "bidask_replay_rank",
            "replay_trade_count": "bidask_replay_trade_count",
            "replay_survival_rate": "bidask_replay_survival_rate",
            "replay_ending_bankroll": "bidask_replay_ending_bankroll",
            "live_test_recommendation": "bidask_live_test_recommendation",
        }
    )
    columns = [
        "subject_name",
        "subject_type",
        "proxy_replay_rank",
        "proxy_replay_trade_count",
        "proxy_replay_survival_rate",
        "proxy_replay_ending_bankroll",
        "proxy_live_test_recommendation",
    ]
    left = left[columns] if not left.empty else pd.DataFrame(columns=columns)
    columns = [
        "subject_name",
        "subject_type",
        "bidask_replay_rank",
        "bidask_replay_trade_count",
        "bidask_replay_survival_rate",
        "bidask_replay_ending_bankroll",
        "bidask_live_test_recommendation",
    ]
    right = right[columns] if not right.empty else pd.DataFrame(columns=columns)
    work = left.merge(right, on=["subject_name", "subject_type"], how="outer")
    work = _numeric_columns(
        work,
        (
            "proxy_replay_rank",
            "bidask_replay_rank",
            "proxy_replay_trade_count",
            "bidask_replay_trade_count",
            "proxy_replay_survival_rate",
            "bidask_replay_survival_rate",
            "proxy_replay_ending_bankroll",
            "bidask_replay_ending_bankroll",
        ),
    )
    work["rank_delta"] = work["proxy_replay_rank"] - work["bidask_replay_rank"]
    work["trade_count_delta"] = work["bidask_replay_trade_count"] - work["proxy_replay_trade_count"]
    work["survival_rate_delta"] = work["bidask_replay_survival_rate"] - work["proxy_replay_survival_rate"]
    work["bankroll_delta"] = work["bidask_replay_ending_bankroll"] - work["proxy_replay_ending_bankroll"]
    work["shortlist_changed_flag"] = (
        work["proxy_live_test_recommendation"].astype(str) != work["bidask_live_test_recommendation"].astype(str)
    )
    work = work.sort_values(
        ["shortlist_changed_flag", "rank_delta", "bankroll_delta", "subject_name"],
        ascending=[False, False, False, True],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
    return work[list(QUOTE_SOURCE_COMPARISON_COLUMNS)]


def _render_bidask_change_report(
    *,
    payload: dict[str, Any],
    quote_coverage_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    shortlist_summary: dict[str, Any],
) -> str:
    direct_rows = int(quote_coverage_df["direct_bidask_quote_count"].sum()) if not quote_coverage_df.empty else 0
    synthetic_rows = int(quote_coverage_df["synthetic_quote_count"].sum()) if not quote_coverage_df.empty else 0
    avg_coverage = (
        float(pd.to_numeric(quote_coverage_df["coverage_ratio"], errors="coerce").fillna(0.0).mean())
        if not quote_coverage_df.empty
        else 0.0
    )
    lines = [
        "# Bid/Ask Replay Change",
        "",
        f"- quote source mode: `{payload.get('replay_contract', {}).get('quote_source_mode')}`",
        f"- quote fallback mode: `{payload.get('replay_contract', {}).get('quote_source_fallback_mode')}`",
        f"- direct historical bid/ask rows: `{direct_rows}`",
        f"- synthetic bid/ask rows: `{synthetic_rows}`",
        f"- average quote coverage ratio: `{avg_coverage:.4f}`",
        "",
        "## Live-Probe Shortlist Change",
        "",
    ]
    if shortlist_summary.get("changed_flag"):
        lines.append(f"- Changed: `True`")
        lines.append(f"- Added: `{', '.join(shortlist_summary.get('added_subjects') or []) or 'none'}`")
        lines.append(f"- Removed: `{', '.join(shortlist_summary.get('removed_subjects') or []) or 'none'}`")
    else:
        lines.append("- Changed: `False`")
        lines.append(
            f"- Bid/ask-aware shortlist remains: `{', '.join(shortlist_summary.get('bidask_live_probe_subjects') or []) or 'none'}`"
        )
    lines.extend(["", "## Top Ranking Deltas", ""])
    if comparison_df.empty:
        lines.append("- No comparison rows were produced.")
    else:
        for row in comparison_df.head(8).to_dict(orient='records'):
            lines.append(
                f"- `{row.get('subject_name')}`"
                f" | proxy rank `{row.get('proxy_replay_rank')}`"
                f" -> bid/ask rank `{row.get('bidask_replay_rank')}`"
                f" | trade delta `{row.get('trade_count_delta')}`"
                f" | bankroll delta `{_as_markdown_num(row.get('bankroll_delta'), digits=3)}`"
                f" | shortlist changed `{row.get('shortlist_changed_flag')}`"
            )
    return "\n".join(lines)


def _build_ranked_memo(
    *,
    payload: dict[str, Any],
    subject_summary_df: pd.DataFrame,
    divergence_df: pd.DataFrame,
    quarter_summary_df: pd.DataFrame,
    window_summary_df: pd.DataFrame,
    blocker_summary_df: pd.DataFrame,
    quote_coverage_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    shortlist_summary: dict[str, Any],
    ranking_df: pd.DataFrame,
    bidask_design_path: Path,
) -> str:
    focus_rows = _build_focus_family_rows(ranking_df)
    controller_rows = ranking_df[ranking_df["subject_type"].astype(str) == "controller"].copy()
    top_divergence_rows = divergence_df.head(10).to_dict(orient="records") if not divergence_df.empty else []
    stale_window_rows = _build_stale_window_rows(window_summary_df)
    quarter_focus_rows = _build_quarter_focus_rows(quarter_summary_df, focus_rows)
    blocker_focus_rows = _build_blocker_focus_rows(blocker_summary_df, focus_rows)
    live_probe_rows = [
        row for row in focus_rows if str(row.get("live_test_recommendation") or "") == "live_probe"
    ]
    shadow_rows = [
        row for row in focus_rows if str(row.get("live_test_recommendation") or "") == "shadow_only"
    ]

    verdict = "controllers_still_define_live_bar"
    if live_probe_rows:
        verdict = "replay_lane_has_live_probe_candidates"
    elif shadow_rows:
        verdict = "shadow_hf_probe_worth_running_but_not_live_ready"

    lines = [
        "# Ranked Memo",
        "",
        f"- season: `{payload.get('season')}`",
        f"- finished postseason games replayed: `{payload.get('finished_game_count')}`",
        f"- state-panel games: `{payload.get('state_panel_game_count')}`",
        f"- backfilled finished games: `{payload.get('derived_bundle_game_count')}`",
        f"- verdict: `{verdict}`",
        "",
        "## Controller Baseline",
        "",
    ]
    if controller_rows.empty:
        lines.append("- No controller rows were produced.")
    else:
        for row in controller_rows.to_dict(orient="records"):
            lines.append(
                f"- `{row['subject_name']}` replay bankroll `{_as_markdown_num(row.get('replay_ending_bankroll'))}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                f" | cadence blocked `{_as_markdown_num((row.get('cadence_blocked_rate') or 0.0) * 100.0)}%`"
                f" | standard trades `{row.get('standard_trade_count')}`"
                f" -> replay `{row.get('replay_trade_count')}`"
            )
    lines.extend(["", "## Bid/Ask-Aware Change", ""])
    if shortlist_summary.get("changed_flag"):
        lines.append("- Live-probe shortlist changed after bid/ask-aware replay.")
        lines.append(f"- Added: `{', '.join(shortlist_summary.get('added_subjects') or []) or 'none'}`")
        lines.append(f"- Removed: `{', '.join(shortlist_summary.get('removed_subjects') or []) or 'none'}`")
    else:
        lines.append("- Live-probe shortlist is unchanged after bid/ask-aware replay.")
        lines.append(
            f"- Shortlist remains `{', '.join(shortlist_summary.get('bidask_live_probe_subjects') or []) or 'none'}`"
        )
    if not quote_coverage_df.empty:
        lines.append(
            f"- Direct historical bid/ask rows `{int(quote_coverage_df['direct_bidask_quote_count'].sum())}`"
            f" | synthetic rows `{int(quote_coverage_df['synthetic_quote_count'].sum())}`"
            f" | average coverage `{float(pd.to_numeric(quote_coverage_df['coverage_ratio'], errors='coerce').fillna(0.0).mean()):.4f}`"
        )
    if not comparison_df.empty:
        top_row = comparison_df.iloc[0].to_dict()
        lines.append(
            f"- Largest ranking delta: `{top_row.get('subject_name')}`"
            f" | proxy rank `{top_row.get('proxy_replay_rank')}`"
            f" -> bid/ask rank `{top_row.get('bidask_replay_rank')}`"
            f" | bankroll delta `{_as_markdown_num(top_row.get('bankroll_delta'), digits=3)}`"
        )
    lines.extend(["", "## Live Promotion Table", ""])
    if not live_probe_rows and not shadow_rows:
        lines.append("- No replay-backed deterministic/HF candidates cleared promotion review.")
    else:
        for row in live_probe_rows + shadow_rows:
            lines.append(
                f"- `{row['subject_name']}` -> `{row.get('live_test_recommendation')}`"
                f" | probe rank `{row.get('probe_priority_rank') if pd.notna(row.get('probe_priority_rank')) else 'n/a'}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                f" | cadence `{_as_markdown_num((row.get('cadence_blocked_rate') or 0.0) * 100.0)}%`"
                f" | bankroll `{_as_markdown_num(row.get('replay_ending_bankroll'))}`"
            )
    lines.extend(["", "## Replay-Aware HF Ranking", ""])
    if not focus_rows:
        lines.append("- No priority higher-frequency families were produced.")
    else:
        for row in focus_rows:
            lines.append(
                f"- `#{row.get('focus_rank')}` `{row['subject_name']}`"
                f" | replay bankroll `{_as_markdown_num(row.get('replay_ending_bankroll'))}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                f" | cadence `{_as_markdown_num((row.get('cadence_blocked_rate') or 0.0) * 100.0)}%`"
                f" | replay trades/game `{_as_markdown_num(row.get('replay_trades_per_game') or 0.0, digits=3)}`"
                f" | live probe `{row.get('live_test_recommendation')}`"
            )
    lines.extend(["", "## Per-Quarter Read", ""])
    if not quarter_focus_rows:
        lines.append("- No quarter-level focus rows were produced.")
    else:
        for row in quarter_focus_rows:
            lines.append(
                f"- `{row.get('subject_name')}` `{row.get('period_label')}`"
                f" | replay `{row.get('replay_trade_count')}` / standard `{row.get('standard_trade_count')}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                f" | replay avg return `{_as_markdown_num(row.get('replay_avg_return_with_slippage'), digits=3)}`"
            )
    lines.extend(["", "## Main Divergence Causes", ""])
    if not top_divergence_rows:
        lines.append("- No divergence rows were produced.")
    else:
        for row in top_divergence_rows:
            lines.append(
                f"- `{row.get('subject_name')}` -> `{row.get('no_trade_reason')}` on `{row.get('signal_count')}` signals"
            )
    lines.extend(["", "## Stale Windows", ""])
    if not stale_window_rows:
        lines.append("- No stale-window rows were produced.")
    else:
        for row in stale_window_rows:
            lines.append(
                f"- `{row.get('subject_name')}` `{row.get('period_label')}` `{row.get('entry_window_label')}`"
                f" | stale `{row.get('stale_signal_count')}` / `{row.get('signal_count')}`"
                f" | cadence `{row.get('cadence_blocked_count')}` / `{row.get('signal_count')}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
            )
    lines.extend(["", "## Candidate Death Read", ""])
    if not blocker_focus_rows:
        lines.append("- No blocker rows were produced.")
    else:
        for row in blocker_focus_rows:
            lines.append(
                f"- `{row.get('subject_name')}` `{row.get('period_label')}` `{row.get('entry_window_label')}`"
                f" -> `{row.get('replay_blocker_class')}` / `{row.get('replay_blocker_detail')}`"
                f" on `{row.get('signal_count')}` signals"
            )
    lines.extend(["", "## Bid/Ask Next", ""])
    lines.append(
        f"- Historical bid/ask capture is the next realism layer: {bidask_design_path.as_posix()}"
    )
    lines.append("- Merge the replay runner, HF family refinements, lifecycle diagnostics, and benchmark submission manifest together. They form one compare-ready execution package.")
    lines.append("- Keep the locked controller pair unchanged as live baselines; any live testing from this lane should be a narrow probe, not a controller swap.")
    return "\n".join(lines)


def _render_bidask_capture_requirements(payload: dict[str, Any]) -> str:
    contract = payload.get("replay_contract") or {}
    return "\n".join(
        [
            "# Historical Bid/Ask Capture Requirements",
            "",
            f"- snapshot date: `{datetime.now(timezone.utc).date().isoformat()}`",
            f"- current replay proxy: `{contract.get('quote_proxy')}`",
            f"- current poll cadence assumption: `{contract.get('poll_interval_seconds')}` seconds",
            f"- current quote freshness gate: `{contract.get('quote_max_age_seconds')}` seconds",
            "",
            "## Why This Is Next",
            "",
            "- The replay runner is now execution-aware on cadence, freshness, spread gating, and realistic no-trade outcomes.",
            "- Historical best-bid and best-ask snapshots are still missing, so spread gating currently uses a conservative cross-side last-trade proxy.",
            "- That proxy is directionally useful, but it cannot fully reconstruct when a live order would have crossed, rested, or been rejected by a wider real spread.",
            "",
            "## Contract Summary",
            "",
            "- Capture every quote change when possible and preserve both venue and ingest timestamps.",
            "- Store append-only level-1 rows keyed by `game_id`, `market_id`, `outcome_id`, and `team_side`.",
            "- Replay must resolve real best bid, best ask, quote age, and spread without falling back to proxy math.",
            "",
            "## Design Pointer",
            "",
            "- Implementation-ready design: `historical_bidask_capture_design.md` in this same reports directory.",
        ]
    )


def _render_bidask_capture_design(payload: dict[str, Any]) -> str:
    contract = payload.get("replay_contract") or {}
    return "\n".join(
        [
            "# Historical Bid/Ask Capture Design",
            "",
            f"- snapshot date: `{datetime.now(timezone.utc).date().isoformat()}`",
            f"- current replay proxy: `{contract.get('quote_proxy')}`",
            f"- target lane owner: `{REPLAY_ENGINE_LANE_ID}` with executor/integration support",
            "",
            "## Goal",
            "",
            "- Replace the current cross-side last-trade proxy with true historical level-1 quote history so replay can compute side-specific executable entry and exit prices.",
            "- Keep the replay contract stable: the new layer should swap quote sourcing, not create a second replay engine.",
            "",
            "## Raw Capture Schema",
            "",
            "- Table: `market_quote_l1_history`.",
            "- Required columns: `season`, `season_phase`, `game_id`, `market_id`, `outcome_id`, `team_side`.",
            "- Quote columns: `best_bid_price`, `best_bid_size`, `best_ask_price`, `best_ask_size`, `last_trade_price`, `last_trade_size`.",
            "- Clock columns: `captured_at_utc`, `ingested_at_utc`, `source_sequence_id`, `source_latency_ms`.",
            "- Integrity columns: `capture_source`, `capture_status`, `raw_payload_json`.",
            "",
            "## Capture Loop",
            "",
            "- Subscribe to the venue quote feed for the selected market as soon as a game becomes live.",
            "- Write one row for every quote-change event; do not aggregate into bars before persistence.",
            "- If feed transport drops, emit a heartbeat gap row so replay can distinguish market silence from collector failure.",
            "- Persist both sides independently so replay can reconstruct the traded outcome and its opposite side at any cycle.",
            "",
            "## Storage Design",
            "",
            "- Partition by `season` and `game_date`.",
            "- Cluster by `(game_id, market_id, team_side, captured_at_utc)`.",
            "- Retain raw payload JSON for forensic checks, but the typed columns above must be queryable without JSON parsing.",
            "",
            "## Replay Integration",
            "",
            "- Add a quote-source adapter: `historical_bidask_l1`.",
            "- Keep the current replay request contract, but extend payload metadata with `quote_source_mode` and `quote_resolution_status`.",
            "- Replace proxy spread math inside the replay quote snapshot builder when historical rows exist for the game.",
            "- Fall back to the current proxy only when historical coverage is missing, and mark that fallback explicitly in artifacts.",
            "",
            "## Required Artifacts",
            "",
            "- Emit per-game quote coverage summaries: coverage ratio, average quote gap, maximum quote gap, and feed dropout spans.",
            "- Extend replay signal artifacts with `quote_source_mode`, `best_bid_size`, `best_ask_size`, and `transport_lag_ms`.",
            "- Extend blocker summaries so `quote_stale` can be split into market silence vs collector lag.",
            "",
            "## Acceptance Tests",
            "",
            "- At least 99% of replay polls for covered games must resolve to a quote row within the configured freshness window.",
            "- Spread gating must use captured best bid and best ask, not proxy reconstruction, whenever coverage exists.",
            "- Replay diagnostics must still explain each no-trade outcome with a traceable quote row or a traceable coverage gap.",
            "",
            "## Merge Plan",
            "",
            "1. Add raw quote capture table and writer.",
            "2. Add replay quote-source adapter with proxy fallback.",
            "3. Add quote coverage diagnostics and tests.",
            "4. Re-run postseason replay and republish ranking artifacts.",
        ]
    )


def _render_replay_contract(
    *,
    payload: dict[str, Any],
    ranking_df: pd.DataFrame,
    quote_coverage_df: pd.DataFrame,
    replay_output_dir: Path,
    reports_root: Path,
    submission_path: Path,
    bidask_report_path: Path,
) -> str:
    top_focus_rows = _build_focus_family_rows(ranking_df)[:3]
    return "\n".join(
        [
            "# Replay Contract",
            "",
            f"- owner lane: `{REPLAY_ENGINE_LANE_ID}`",
            "- maturity: `execution_replay_v1_3`",
            f"- snapshot date: `{datetime.now(timezone.utc).date().isoformat()}`",
            f"- season: `{payload.get('season')}`",
            f"- finished postseason games: `{payload.get('finished_game_count')}`",
            f"- canonical state-panel games: `{payload.get('state_panel_game_count')}`",
            f"- derived finished games: `{payload.get('derived_bundle_game_count')}`",
            "",
            "## Stable Semantics",
            "",
            "- The replay runner extends the shared backtest contract; it does not create a second incompatible engine.",
            "- Each standard backtest trade candidate becomes one replay signal that is re-evaluated under live-like polling, freshness, and spread constraints.",
            "- Replay artifacts now publish per-signal lifecycle fields, first-executable timestamps, cadence-vs-stale attribution, quarter summaries, and candidate birth/death tables.",
            "",
            "## Execution Gates",
            "",
            f"- poll cadence: `{payload.get('replay_contract', {}).get('poll_interval_seconds')}` seconds",
            f"- signal freshness window: `{payload.get('replay_contract', {}).get('signal_max_age_seconds')}` seconds",
            f"- quote freshness window: `{payload.get('replay_contract', {}).get('quote_max_age_seconds')}` seconds",
            f"- spread gate: `{payload.get('replay_contract', {}).get('max_spread_cents')}` cents",
            f"- quote source mode: `{payload.get('replay_contract', {}).get('quote_source_mode')}`",
            f"- quote source fallback: `{payload.get('replay_contract', {}).get('quote_source_fallback_mode')}`",
            f"- quote proxy until true historical bid/ask exists: `{payload.get('replay_contract', {}).get('quote_proxy')}`",
            "",
            "## Published Shared Outputs",
            "",
            f"- artifact root: `{replay_output_dir}`",
            f"- reports root: `{reports_root}`",
            f"- compare-ready submission: `{submission_path}`",
            f"- historical bid/ask spec: `{bidask_report_path}`",
            "",
            "## Current Read",
            "",
            "- Standard backtests still overstate executable activity relative to replay, and `signal_stale` remains the dominant divergence cause.",
            "- The locked controller pair stays unchanged as the live baseline reference while the replay lane owns deterministic/HF family invention.",
            (
                f"- Historical bid/ask sample rows `{int(quote_coverage_df['direct_bidask_quote_count'].sum()) if not quote_coverage_df.empty else 0}` direct"
                f" | `{int(quote_coverage_df['synthetic_quote_count'].sum()) if not quote_coverage_df.empty else 0}` synthetic"
            ),
            *[
                (
                    f"- focus family `{row.get('subject_name')}`"
                    f" | replay bankroll `{_as_markdown_num(row.get('replay_ending_bankroll'))}`"
                    f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                    f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                )
                for row in top_focus_rows
            ],
            "",
            "## Next Realism Layer",
            "",
            f"- Historical bid/ask capture design is defined in `{bidask_report_path}`.",
            "- Do not spend more controller-tuning cycles before true quote history exists; replay already shows the current controller pair compresses materially under execution-aware assumptions.",
        ]
    )


def _render_status(
    *,
    payload: dict[str, Any],
    ranking_df: pd.DataFrame,
    shortlist_summary: dict[str, Any],
    replay_output_dir: Path,
    reports_root: Path,
    submission_path: Path,
    tests_command: str,
) -> str:
    focus_rows = _build_focus_family_rows(ranking_df)
    return "\n".join(
        [
            "# Replay Lane Status",
            "",
            f"- branch: `codex/replay-engine-hf`",
            f"- updated at: `{_published_at_iso()}`",
            f"- season: `{payload.get('season')}`",
            f"- replay artifact root: `{replay_output_dir}`",
            f"- shared report root: `{reports_root}`",
            f"- benchmark submission: `{submission_path}`",
            f"- focused validation: `{tests_command}`",
            "",
            "## Current Findings",
            "",
            "- Replay remains materially stricter than the historical backtest because stale-signal rejection dominates the no-trade set.",
            "- The replay lane now owns deterministic/HF strategy invention, stale-window diagnostics, and compare-ready benchmark publication for non-ML / non-LLM candidates.",
            (
                "- Bid/ask-aware replay kept the live-probe shortlist unchanged."
                if not shortlist_summary.get("changed_flag")
                else f"- Bid/ask-aware replay changed the live-probe shortlist: added `{', '.join(shortlist_summary.get('added_subjects') or []) or 'none'}` removed `{', '.join(shortlist_summary.get('removed_subjects') or []) or 'none'}`."
            ),
            *[
                (
                    f"- `{row.get('subject_name')}`"
                    f" | live probe `{row.get('live_test_recommendation')}`"
                    f" | replay bankroll `{_as_markdown_num(row.get('replay_ending_bankroll'))}`"
                    f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                    f" | cadence `{_as_markdown_num((row.get('cadence_blocked_rate') or 0.0) * 100.0)}%`"
                )
                for row in focus_rows[:4]
            ],
            "",
            "## Merge Next",
            "",
            "- Merge the replay engine, HF family refinements, new replay diagnostics, and compare-ready submission manifest together.",
            "- Keep controller live routing unchanged; any immediate live testing should be a narrow replay-backed probe of the best HF family, not a baseline replacement.",
        ]
    )


def _render_live_probe_recommendations(ranking_df: pd.DataFrame, *, shortlist_summary: dict[str, Any]) -> str:
    live_probe_rows = ranking_df[ranking_df["live_test_recommendation"].astype(str) == "live_probe"].copy()
    shadow_rows = ranking_df[ranking_df["live_test_recommendation"].astype(str) == "shadow_only"].copy()
    bench_rows = ranking_df[ranking_df["live_test_recommendation"].astype(str) == "bench"].copy()
    lines = [
        "# Daily Live Validation Recommendations",
        "",
        (
            "- Live-probe shortlist changed after bid/ask-aware replay."
            if shortlist_summary.get("changed_flag")
            else "- Live-probe shortlist is unchanged after bid/ask-aware replay."
        ),
        "",
        "## Live Probe",
        "",
    ]
    if live_probe_rows.empty:
        lines.append("- No deterministic/HF family cleared live-probe promotion.")
    else:
        for row in live_probe_rows.sort_values(
            ["probe_priority_rank", "replay_ending_bankroll", "subject_name"],
            ascending=[True, False, True],
            kind="mergesort",
            na_position="last",
        ).to_dict(orient="records"):
            lines.append(
                f"- `#{int(row.get('probe_priority_rank') or 0)}` `{row.get('subject_name')}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                f" | cadence `{_as_markdown_num((row.get('cadence_blocked_rate') or 0.0) * 100.0)}%`"
                f" | bankroll `{_as_markdown_num(row.get('replay_ending_bankroll'))}`"
            )
    lines.extend(["", "## Shadow Only", ""])
    if shadow_rows.empty:
        lines.append("- No shadow-only families were produced.")
    else:
        for row in shadow_rows.sort_values(
            ["replay_ending_bankroll", "replay_survival_rate", "subject_name"],
            ascending=[False, False, True],
            kind="mergesort",
            na_position="last",
        ).to_dict(orient="records"):
            lines.append(
                f"- `{row.get('subject_name')}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
                f" | stale `{_as_markdown_num((row.get('stale_signal_rate') or 0.0) * 100.0)}%`"
                f" | cadence `{_as_markdown_num((row.get('cadence_blocked_rate') or 0.0) * 100.0)}%`"
            )
    lines.extend(["", "## Bench", ""])
    if bench_rows.empty:
        lines.append("- No bench rows were produced.")
    else:
        for row in bench_rows.sort_values(
            ["subject_name"],
            ascending=[True],
            kind="mergesort",
            na_position="last",
        ).head(6).to_dict(orient="records"):
            lines.append(
                f"- `{row.get('subject_name')}`"
                f" | top blocker `{row.get('top_no_trade_reason')}`"
                f" | survival `{_as_markdown_num((row.get('replay_survival_rate') or 0.0) * 100.0)}%`"
            )
    return "\n".join(lines)


def _build_benchmark_submission(
    *,
    payload: dict[str, Any],
    ranking_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    reports_root: Path,
    replay_output_dir: Path,
    ranking_artifacts: dict[str, str],
    bidask_report_path: Path,
    promotion_table_path: Path,
    live_probe_report_path: Path,
    bidask_change_report_path: Path,
    comparison_artifact_path: Path,
    shortlist_summary: dict[str, Any],
) -> dict[str, Any]:
    artifact_lookup = payload.get("artifacts") or {}
    comparison_lookup = (
        comparison_df.set_index("subject_name").to_dict(orient="index")
        if not comparison_df.empty and "subject_name" in comparison_df.columns
        else {}
    )
    subjects: list[dict[str, Any]] = []
    for row in ranking_df.to_dict(orient="records"):
        subject_name = str(row.get("subject_name") or "")
        if int(row.get("standard_trade_count") or 0) <= 0:
            continue
        stem = _subject_stem(subject_name)
        subject_type = str(row.get("subject_type") or "")
        candidate_kind = (
            "controller_baseline"
            if subject_type == "controller"
            else "hf_family" if subject_name in PRIORITY_HF_FAMILIES else "deterministic_family"
        )
        notes: list[str] = []
        if subject_name in LOCKED_BASELINE_SUBJECTS:
            notes.append("locked baseline controller")
        if subject_name in PRIORITY_HF_FAMILIES:
            notes.append("priority replay-aware HF focus family")
        if str(row.get("live_test_recommendation") or "") == "live_probe":
            notes.append("best current replay-backed candidate for a narrow live probe")
        comparison = comparison_lookup.get(subject_name) or {}
        subjects.append(
            {
                "candidate_id": subject_name,
                "display_name": subject_name,
                "candidate_kind": candidate_kind,
                "comparison_ready_flag": True,
                "metrics": {
                    "standard_trade_count": int(row.get("standard_trade_count") or 0),
                    "replay_trade_count": int(row.get("replay_trade_count") or 0),
                    "live_trade_count": int(row.get("live_trade_count") or 0),
                    "trade_gap": int((row.get("replay_trade_count") or 0) - (row.get("standard_trade_count") or 0)),
                    "execution_rate": row.get("replay_survival_rate"),
                    "replay_survival_rate": row.get("replay_survival_rate"),
                    "standard_trades_per_game": row.get("standard_trades_per_game"),
                    "replay_trades_per_game": row.get("replay_trades_per_game"),
                    "stale_signal_count": row.get("stale_signal_count"),
                    "stale_signal_rate": row.get("stale_signal_rate"),
                    "cadence_blocked_count": row.get("cadence_blocked_count"),
                    "cadence_blocked_rate": row.get("cadence_blocked_rate"),
                    "replay_ending_bankroll": row.get("replay_ending_bankroll"),
                    "replay_compounded_return": row.get("replay_compounded_return"),
                    "replay_max_drawdown_pct": row.get("replay_max_drawdown_pct"),
                    "replay_max_drawdown_amount": row.get("replay_max_drawdown_amount"),
                    "replay_path_quality_score": row.get("replay_path_quality_score"),
                    "top_no_trade_reason": row.get("top_no_trade_reason"),
                    "proxy_replay_rank": comparison.get("proxy_replay_rank"),
                    "bidask_replay_rank": comparison.get("bidask_replay_rank"),
                    "rank_delta_vs_proxy": comparison.get("rank_delta"),
                    "trade_count_delta_vs_proxy": comparison.get("trade_count_delta"),
                    "survival_rate_delta_vs_proxy": comparison.get("survival_rate_delta"),
                    "bankroll_delta_vs_proxy": comparison.get("bankroll_delta"),
                },
                "artifacts": {
                    "report_markdown": artifact_lookup.get("markdown"),
                    "replay_json": artifact_lookup.get("json"),
                    "standard_trades_csv": artifact_lookup.get(f"standard_{stem}_csv"),
                    "replay_trades_csv": artifact_lookup.get(f"replay_{stem}_csv"),
                    "signal_summary_csv": artifact_lookup.get("signal_summary_csv"),
                    "attempt_trace_csv": artifact_lookup.get("attempt_trace_csv"),
                    "quarter_summary_csv": artifact_lookup.get("quarter_summary_csv"),
                    "window_summary_csv": artifact_lookup.get("window_summary_csv"),
                    "candidate_lifecycle_csv": artifact_lookup.get("candidate_lifecycle_csv"),
                    "blocker_summary_csv": artifact_lookup.get("blocker_summary_csv"),
                    "historical_bidask_l1_csv": artifact_lookup.get("historical_bidask_l1_csv"),
                    "quote_coverage_summary_csv": artifact_lookup.get("quote_coverage_summary_csv"),
                    "game_gap_csv": artifact_lookup.get("game_gap_csv"),
                    "ranking_csv": ranking_artifacts.get("csv"),
                    "promotion_table_csv": str(promotion_table_path),
                    "live_probe_recommendations": str(live_probe_report_path),
                    "bidask_replay_change": str(bidask_change_report_path),
                    "quote_source_comparison_csv": str(comparison_artifact_path),
                    "historical_bidask_spec": str(bidask_report_path),
                },
                "notes": notes,
                "live_test_recommendation": row.get("live_test_recommendation"),
                "probe_priority_rank": int(row.get("probe_priority_rank")) if pd.notna(row.get("probe_priority_rank")) else None,
                "replay_rank": int(row.get("replay_rank") or 0),
                "focus_rank": int(row.get("focus_rank")) if pd.notna(row.get("focus_rank")) else None,
            }
        )

    return {
        "lane_id": REPLAY_ENGINE_LANE_ID,
        "lane_label": "Replay + deterministic/HF",
        "lane_type": "deterministic_hf",
        "published_at": _published_at_iso(),
        "comparison_scope": {
            "season": payload.get("season"),
            "phase_group": "play_in,playoffs",
            "shared_contract_ref": "replay_contract_current.md",
        },
        "replay_artifact_root": str(replay_output_dir),
        "promotion_table": str(promotion_table_path),
        "live_probe_recommendations": str(live_probe_report_path),
        "bidask_replay_change": str(bidask_change_report_path),
        "quote_source_comparison_csv": str(comparison_artifact_path),
        "historical_bidask_design": str(bidask_report_path),
        "live_probe_shortlist_changed": bool(shortlist_summary.get("changed_flag")),
        "live_probe_shortlist": shortlist_summary.get("bidask_live_probe_subjects") or [],
        "subjects": subjects,
    }


def main() -> None:
    args = _parse_args()
    artifacts_root = Path(args.artifacts_root).expanduser().resolve()
    reports_root = Path(args.reports_root).expanduser().resolve()
    replay_output_dir = artifacts_root / args.season / "postseason_execution_replay"
    replay_output_dir.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)
    shared_root = _shared_root_from_reports(reports_root)
    handoff_root = shared_root / "handoffs" / REPLAY_ENGINE_LANE_ID
    handoff_root.mkdir(parents=True, exist_ok=True)
    (shared_root / "benchmark_contract").mkdir(parents=True, exist_ok=True)

    request = ReplayRunRequest(
        season=args.season,
        season_phase="postseason_to_date",
        analysis_version=args.analysis_version,
        poll_interval_seconds=args.poll_interval_seconds,
        signal_max_age_seconds=args.signal_max_age_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
        max_spread_cents=args.max_spread_cents,
        proxy_min_spread_cents=args.proxy_min_spread_cents,
        proxy_max_spread_cents=args.proxy_max_spread_cents,
        aggressive_exit_slippage_cents=args.aggressive_exit_slippage_cents,
        quote_source_mode=args.quote_source_mode,
        quote_source_fallback_mode=args.quote_source_fallback_mode,
        include_live_run_ids=tuple(args.live_run_id),
    )
    proxy_request = replace(
        request,
        quote_source_mode=args.compare_against_quote_source,
        quote_source_fallback_mode="",
    )

    result = run_postseason_execution_replay(request=request, output_dir=replay_output_dir)
    payload = write_replay_artifacts(result, replay_output_dir)
    proxy_result = run_postseason_execution_replay(request=proxy_request, output_dir=replay_output_dir)

    subject_summary_df = result.benchmark_frames.get("subject_summary", pd.DataFrame())
    divergence_df = result.benchmark_frames.get("divergence_summary", pd.DataFrame())
    quarter_summary_df = result.benchmark_frames.get("quarter_summary", pd.DataFrame())
    window_summary_df = result.benchmark_frames.get("window_summary", pd.DataFrame())
    blocker_summary_df = result.benchmark_frames.get("blocker_summary", pd.DataFrame())
    quote_coverage_df = result.benchmark_frames.get("quote_coverage_summary", pd.DataFrame())
    ranking_df = _build_ranking_frame(subject_summary_df)
    proxy_ranking_df = _build_ranking_frame(proxy_result.benchmark_frames.get("subject_summary", pd.DataFrame()))
    shortlist_summary = _shortlist_change_summary(
        bidask_ranking_df=ranking_df,
        proxy_ranking_df=proxy_ranking_df,
    )
    comparison_df = _build_quote_source_comparison_frame(
        bidask_ranking_df=ranking_df,
        proxy_ranking_df=proxy_ranking_df,
    )
    ranking_artifacts = write_frame(replay_output_dir / "replay_candidate_ranking", ranking_df)
    payload.setdefault("artifacts", {}).update(
        {f"candidate_ranking_{key}": value for key, value in ranking_artifacts.items()}
    )
    promotion_df = _build_promotion_table_frame(ranking_df)
    promotion_artifacts = write_frame(replay_output_dir / "replay_promotion_table", promotion_df)
    payload.setdefault("artifacts", {}).update(
        {f"promotion_table_{key}": value for key, value in promotion_artifacts.items()}
    )
    comparison_artifacts = write_frame(replay_output_dir / "quote_source_comparison", comparison_df)
    payload.setdefault("artifacts", {}).update(
        {f"quote_source_comparison_{key}": value for key, value in comparison_artifacts.items()}
    )

    bidask_requirements_path = reports_root / "historical_bidask_capture_requirements.md"
    bidask_design_path = reports_root / "historical_bidask_capture_design.md"
    write_markdown(bidask_requirements_path, _render_bidask_capture_requirements(payload))
    write_markdown(bidask_design_path, _render_bidask_capture_design(payload))
    live_probe_report_path = reports_root / "daily_live_probe_recommendations.md"
    write_markdown(live_probe_report_path, _render_live_probe_recommendations(ranking_df, shortlist_summary=shortlist_summary))
    bidask_change_report_path = reports_root / "bidask_replay_change.md"
    write_markdown(
        bidask_change_report_path,
        _render_bidask_change_report(
            payload=payload,
            quote_coverage_df=quote_coverage_df,
            comparison_df=comparison_df,
            shortlist_summary=shortlist_summary,
        ),
    )

    submission_payload = _build_benchmark_submission(
        payload=payload,
        ranking_df=ranking_df,
        comparison_df=comparison_df,
        reports_root=reports_root,
        replay_output_dir=replay_output_dir,
        ranking_artifacts=ranking_artifacts,
        bidask_report_path=bidask_design_path,
        promotion_table_path=Path(str(promotion_artifacts.get("csv") or (replay_output_dir / "replay_promotion_table.csv"))),
        live_probe_report_path=live_probe_report_path,
        bidask_change_report_path=bidask_change_report_path,
        comparison_artifact_path=Path(str(comparison_artifacts.get("csv") or (replay_output_dir / "quote_source_comparison.csv"))),
        shortlist_summary=shortlist_summary,
    )
    submission_path = reports_root / "benchmark_submission.json"
    write_json(submission_path, submission_payload)

    memo_body = _build_ranked_memo(
        payload=payload,
        subject_summary_df=subject_summary_df,
        divergence_df=divergence_df,
        quarter_summary_df=quarter_summary_df,
        window_summary_df=window_summary_df,
        blocker_summary_df=blocker_summary_df,
        quote_coverage_df=quote_coverage_df,
        comparison_df=comparison_df,
        shortlist_summary=shortlist_summary,
        ranking_df=ranking_df,
        bidask_design_path=bidask_design_path,
    )
    memo_path = reports_root / "ranked_memo.md"
    write_markdown(memo_path, memo_body)

    contract_path = shared_root / "benchmark_contract" / "replay_contract_current.md"
    write_markdown(
        contract_path,
        _render_replay_contract(
            payload=payload,
            ranking_df=ranking_df,
            quote_coverage_df=quote_coverage_df,
            replay_output_dir=replay_output_dir,
            reports_root=reports_root,
            submission_path=submission_path,
            bidask_report_path=bidask_design_path,
        ),
    )

    tests_command = (
        "python -m pytest -q "
        "tests\\app\\data\\pipelines\\daily\\nba\\test_analysis_replay_pytest.py "
        "tests\\app\\data\\pipelines\\daily\\nba\\test_analysis_backtests_pytest.py"
    )
    status_path = handoff_root / "status.md"
    write_markdown(
        status_path,
        _render_status(
            payload=payload,
            ranking_df=ranking_df,
            shortlist_summary=shortlist_summary,
            replay_output_dir=replay_output_dir,
            reports_root=reports_root,
            submission_path=submission_path,
            tests_command=tests_command,
        ),
    )

    metadata = {
        "artifacts_dir": str(replay_output_dir),
        "reports_dir": str(reports_root),
        "replay_json": str(replay_output_dir / "replay_run.json"),
        "replay_markdown": str(replay_output_dir / "replay_run.md"),
        "ranked_memo": str(memo_path),
        "benchmark_submission": str(submission_path),
        "historical_bidask_capture_requirements": str(bidask_requirements_path),
        "historical_bidask_capture_design": str(bidask_design_path),
        "bidask_replay_change": str(bidask_change_report_path),
        "live_probe_recommendations": str(live_probe_report_path),
        "promotion_table_csv": str(promotion_artifacts.get("csv") or ""),
        "quote_source_comparison_csv": str(comparison_artifacts.get("csv") or ""),
        "replay_contract": str(contract_path),
        "handoff_status": str(status_path),
        "finished_game_count": payload.get("finished_game_count"),
        "state_panel_game_count": payload.get("state_panel_game_count"),
        "derived_bundle_game_count": payload.get("derived_bundle_game_count"),
    }
    write_json(reports_root / "run_metadata.json", metadata)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
