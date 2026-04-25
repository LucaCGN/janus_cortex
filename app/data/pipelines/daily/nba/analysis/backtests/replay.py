from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from app.api.db import cursor_dict, fetchall_dicts, to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.backtests.controller_vnext import (
    DEFAULT_VNEXT_PROFILE,
    DEFAULT_VNEXT_STOP_MAP,
    apply_stop_overlay,
    build_state_lookup,
    decorate_trade_frame_with_vnext_sizing,
)
from app.data.pipelines.daily.nba.analysis.backtests.engine import (
    BACKTEST_TRADE_COLUMNS,
    build_backtest_result,
    load_analysis_backtest_state_panel_df,
)
from app.data.pipelines.daily.nba.analysis.backtests.historical_bidask import (
    HISTORICAL_BIDASK_L1_COLUMNS,
    QUOTE_COVERAGE_SUMMARY_COLUMNS,
    build_historical_bidask_samples,
    build_proxy_quote_fields_from_cross_side_ticks,
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
    PORTFOLIO_SCOPE_SINGLE_FAMILY,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.backtests.registry import REPLAY_HF_STRATEGY_GROUP, build_strategy_registry
from app.data.pipelines.daily.nba.analysis.backtests.specs import ReplayRunResult
from app.data.pipelines.daily.nba.analysis.backtests.unified_router import build_unified_router_trade_frame
from app.data.pipelines.daily.nba.analysis.consumer_adapters import load_analysis_consumer_bundle
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisConsumerRequest, ReplayRunRequest, RESEARCH_READY_STATUSES
from app.data.pipelines.daily.nba.analysis.mart_game_profiles import derive_game_rows, load_analysis_bundle
from app.data.pipelines.daily.nba.analysis.mart_state_panel import build_state_rows_for_side
from app.modules.nba.execution.contracts import LIVE_FALLBACK_CONTROLLER, LIVE_PRIMARY_CONTROLLER


REPLAY_SIGNAL_SUMMARY_COLUMNS = (
    "subject_name",
    "subject_type",
    "game_id",
    "team_side",
    "signal_id",
    "strategy_family",
    "period_label",
    "opening_band",
    "score_diff_bucket",
    "context_bucket",
    "entry_state_index",
    "exit_state_index",
    "signal_entry_at",
    "signal_exit_at",
    "signal_entry_price",
    "signal_exit_price",
    "signal_window_seconds",
    "period_elapsed_seconds",
    "entry_window_label",
    "executed_flag",
    "no_trade_reason",
    "terminal_no_trade_reason",
    "dominant_retry_reason",
    "attempt_count",
    "first_attempt_at",
    "last_attempt_at",
    "first_visible_at",
    "first_executable_event_at",
    "first_executable_poll_at",
    "stale_at",
    "time_to_first_executable_event_seconds",
    "time_to_first_executable_poll_seconds",
    "time_to_stale_seconds",
    "cadence_gap_seconds",
    "event_level_opportunity_flag",
    "poll_level_opportunity_flag",
    "cadence_blocker_flag",
    "stale_before_first_executable_flag",
    "replay_blocker_class",
    "replay_blocker_detail",
    "cadence_vs_stale_blocker",
    "max_signal_age_seconds",
    "max_quote_age_seconds",
    "entry_fill_at",
    "exit_fill_at",
    "entry_fill_price",
    "exit_fill_price",
    "fill_delay_seconds",
    "exit_delay_seconds",
    "quote_proxy",
    "quote_source_mode",
    "quote_resolution_status",
    "capture_source",
    "best_bid_size",
    "best_ask_size",
    "transport_lag_ms",
    "state_source",
)

REPLAY_ATTEMPT_TRACE_COLUMNS = (
    "subject_name",
    "subject_type",
    "game_id",
    "signal_id",
    "period_label",
    "entry_window_label",
    "attempt_stage",
    "cycle_at",
    "attempt_index",
    "result",
    "reason",
    "entry_state_index",
    "latest_state_index",
    "latest_state_at",
    "signal_age_seconds",
    "quote_time",
    "quote_age_seconds",
    "best_bid",
    "best_ask",
    "best_bid_size",
    "best_ask_size",
    "spread_cents",
    "quote_source_mode",
    "quote_resolution_status",
    "capture_source",
    "transport_lag_ms",
    "state_source",
)

REPLAY_SUBJECT_SUMMARY_COLUMNS = (
    "subject_name",
    "subject_type",
    "standard_trade_count",
    "replay_trade_count",
    "trade_gap",
    "standard_traded_game_count",
    "replay_traded_game_count",
    "standard_trades_per_game",
    "replay_trades_per_game",
    "execution_rate",
    "replay_survival_rate",
    "standard_avg_return_with_slippage",
    "replay_avg_return_with_slippage",
    "replay_no_trade_count",
    "stale_signal_count",
    "stale_signal_rate",
    "cadence_blocked_count",
    "cadence_blocked_rate",
    "top_no_trade_reason",
    "standard_ending_bankroll",
    "standard_compounded_return",
    "standard_max_drawdown_pct",
    "standard_max_drawdown_amount",
    "replay_ending_bankroll",
    "replay_compounded_return",
    "replay_max_drawdown_pct",
    "replay_max_drawdown_amount",
    "replay_path_quality_score",
    "live_trade_count",
    "live_vs_standard_gap_trade_rate",
)

REPLAY_GAME_GAP_COLUMNS = (
    "subject_name",
    "subject_type",
    "game_id",
    "state_source",
    "standard_trade_count",
    "replay_trade_count",
    "trade_gap",
    "top_no_trade_reason",
)

REPLAY_DIVERGENCE_COLUMNS = (
    "subject_name",
    "subject_type",
    "no_trade_reason",
    "signal_count",
)

REPLAY_PORTFOLIO_COLUMNS = (
    "subject_name",
    "subject_type",
    "mode",
    "ending_bankroll",
    "compounded_return",
    "max_drawdown_pct",
    "max_drawdown_amount",
    "executed_trade_count",
)

REPLAY_QUARTER_SUMMARY_COLUMNS = (
    "subject_name",
    "subject_type",
    "period_label",
    "standard_trade_count",
    "replay_trade_count",
    "standard_avg_return_with_slippage",
    "replay_avg_return_with_slippage",
    "replay_survival_rate",
    "stale_signal_count",
    "stale_signal_rate",
)

REPLAY_WINDOW_SUMMARY_COLUMNS = (
    "subject_name",
    "subject_type",
    "period_label",
    "entry_window_label",
    "signal_count",
    "replay_trade_count",
    "replay_survival_rate",
    "stale_signal_count",
    "stale_signal_rate",
    "cadence_blocked_count",
    "cadence_blocked_rate",
    "avg_fill_delay_seconds",
    "avg_signal_window_seconds",
)

REPLAY_CANDIDATE_LIFECYCLE_COLUMNS = (
    "subject_name",
    "subject_type",
    "game_id",
    "signal_id",
    "strategy_family",
    "period_label",
    "entry_window_label",
    "lifecycle_status",
    "birth_at",
    "death_at",
    "birth_to_death_seconds",
    "first_visible_at",
    "first_executable_event_at",
    "first_executable_poll_at",
    "stale_at",
    "time_to_first_executable_event_seconds",
    "time_to_first_executable_poll_seconds",
    "time_to_stale_seconds",
    "cadence_gap_seconds",
    "event_level_opportunity_flag",
    "poll_level_opportunity_flag",
    "cadence_blocker_flag",
    "stale_before_first_executable_flag",
    "replay_blocker_class",
    "replay_blocker_detail",
    "cadence_vs_stale_blocker",
    "signal_window_seconds",
    "attempt_count",
    "dominant_retry_reason",
    "terminal_no_trade_reason",
    "final_no_trade_reason",
    "max_signal_age_seconds",
    "max_quote_age_seconds",
    "state_source",
)

REPLAY_BLOCKER_SUMMARY_COLUMNS = (
    "subject_name",
    "subject_type",
    "period_label",
    "entry_window_label",
    "replay_blocker_class",
    "replay_blocker_detail",
    "cadence_vs_stale_blocker",
    "signal_count",
)

REPLAY_HISTORICAL_BIDASK_COLUMNS = HISTORICAL_BIDASK_L1_COLUMNS
REPLAY_QUOTE_COVERAGE_COLUMNS = QUOTE_COVERAGE_SUMMARY_COLUMNS

CONTROLLER_SUBJECTS = (
    {
        "subject_name": LIVE_FALLBACK_CONTROLLER,
        "subject_type": "controller",
        "controller_mode": "deterministic",
    },
    {
        "subject_name": LIVE_PRIMARY_CONTROLLER,
        "subject_type": "controller",
        "controller_mode": "unified",
    },
)

_LIVE_MASTER_ROUTER_KWARGS = {
    "extra_selection_mode": "same_side_top1",
    "min_core_confidence_for_extras": 0.60,
}
_LIVE_UNIFIED_KWARGS = {
    "extra_selection_mode": "same_side_top1",
    "min_core_confidence_for_extras": 0.60,
    "weak_confidence_threshold": 0.64,
    "llm_accept_confidence": 0.60,
    "llm_review_min_confidence": 0.46,
    "skip_weak_when_llm_empty": True,
    "skip_weak_when_llm_low_confidence": True,
    "skip_below_review_min_confidence": True,
}
_LIVE_UNIFIED_LLM_LANE = {
    "lane_name": "llm_hybrid_vnext_meta_review_v1",
    "lane_group": "live_controller",
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


@dataclass(slots=True)
class ControllerContext:
    selection_sample_name: str
    priors: dict[str, dict[str, Any]]
    family_profiles: dict[str, dict[str, Any]]
    historical_team_context_lookup: dict[str, dict[str, Any]]
    llm_client: Any
    llm_cache_store: Any
    llm_budget_state: _LLMBudgetState


@dataclass(slots=True)
class ReplayGameContext:
    game_id: str
    season_phase: str
    game: dict[str, Any]
    bundle: dict[str, Any]
    state_df: pd.DataFrame
    state_source: str
    coverage_status: str
    classification: str
    anchor_at: datetime
    end_at: datetime
    historical_bidask_df: pd.DataFrame | None = None
    quote_coverage: dict[str, Any] | None = None


@dataclass(slots=True)
class ReplaySubject:
    subject_name: str
    subject_type: str
    standard_frame: pd.DataFrame


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, *, default: int = -1) -> int:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clamp(value: float, *, low: float, high: float) -> float:
    return max(low, min(high, value))


def _json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=True, separators=(",", ":"))


def _signal_id_for_trade(record: dict[str, Any]) -> str:
    return (
        f"{str(record.get('subject_name') or record.get('source_strategy_family') or record.get('strategy_family') or '')}"
        f"|{str(record.get('game_id') or '')}"
        f"|{str(record.get('team_side') or '')}"
        f"|{int(record.get('entry_state_index') or 0)}"
    )


def _extract_stop_price(record: dict[str, Any]) -> float | None:
    metadata = record.get("entry_metadata_json")
    payload = metadata if isinstance(metadata, dict) else {}
    if isinstance(metadata, str) and metadata:
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
    for key in ("stop_price", "exit_threshold"):
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _build_state_lookup_by_game(state_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if state_df.empty:
        return {}
    work = state_df.copy()
    work["game_id"] = work["game_id"].astype(str)
    work["state_index"] = pd.to_numeric(work["state_index"], errors="coerce")
    work["event_at"] = pd.to_datetime(work["event_at"], errors="coerce", utc=True)
    return {
        str(game_id): group.sort_values(["event_at", "team_side", "state_index"], kind="mergesort").reset_index(drop=True)
        for game_id, group in work.groupby("game_id", sort=False)
    }


def _resolve_signal_state_row(context: ReplayGameContext | None, record: dict[str, Any]) -> dict[str, Any]:
    if context is None or context.state_df.empty:
        return {}
    team_side = str(record.get("team_side") or "")
    entry_state_index = _safe_int(record.get("entry_state_index"))
    if entry_state_index < 0:
        return {}
    frame = context.state_df.copy()
    frame["team_side"] = frame["team_side"].astype(str)
    frame["state_index"] = pd.to_numeric(frame["state_index"], errors="coerce")
    matched = frame[
        (frame["team_side"] == team_side)
        & (frame["state_index"] == entry_state_index)
    ]
    if matched.empty:
        return {}
    return matched.iloc[0].to_dict()


def _period_elapsed_seconds_for_signal(context: ReplayGameContext | None, record: dict[str, Any]) -> float | None:
    if context is None or context.state_df.empty:
        return None
    signal_row = _resolve_signal_state_row(context, record)
    if not signal_row:
        return None
    team_side = str(signal_row.get("team_side") or record.get("team_side") or "")
    period_label = str(signal_row.get("period_label") or record.get("period_label") or "")
    clock_elapsed_seconds = _safe_float(signal_row.get("clock_elapsed_seconds"))
    if not team_side or not period_label or clock_elapsed_seconds is None:
        return None
    period_rows = context.state_df[
        (context.state_df["team_side"].astype(str) == team_side)
        & (context.state_df["period_label"].astype(str) == period_label)
    ].copy()
    if period_rows.empty:
        return None
    period_rows["clock_elapsed_seconds"] = pd.to_numeric(period_rows["clock_elapsed_seconds"], errors="coerce")
    start_clock = _safe_float(period_rows["clock_elapsed_seconds"].min())
    if start_clock is None:
        return None
    return max(0.0, clock_elapsed_seconds - start_clock)


def _entry_window_label(period_elapsed_seconds: float | None) -> str:
    if period_elapsed_seconds is None:
        return "unknown"
    if period_elapsed_seconds <= 60.0:
        return "opening_0_60"
    if period_elapsed_seconds <= 180.0:
        return "opening_60_180"
    if period_elapsed_seconds <= 360.0:
        return "early_180_360"
    if period_elapsed_seconds <= 720.0:
        return "mid_360_720"
    return "late_720_plus"


def _signal_context_fields(
    context: ReplayGameContext | None,
    record: dict[str, Any],
) -> dict[str, Any]:
    signal_entry_at = _parse_datetime(record.get("entry_at"))
    signal_exit_at = _parse_datetime(record.get("exit_at"))
    signal_window_seconds = (
        max(0.0, (signal_exit_at - signal_entry_at).total_seconds())
        if signal_entry_at is not None and signal_exit_at is not None
        else None
    )
    period_elapsed_seconds = _period_elapsed_seconds_for_signal(context, record)
    return {
        "period_label": str(record.get("period_label") or ""),
        "opening_band": str(record.get("opening_band") or ""),
        "score_diff_bucket": str(record.get("score_diff_bucket") or ""),
        "context_bucket": str(record.get("context_bucket") or ""),
        "signal_window_seconds": signal_window_seconds,
        "period_elapsed_seconds": period_elapsed_seconds,
        "entry_window_label": _entry_window_label(period_elapsed_seconds),
        "state_source": context.state_source if context is not None else "missing",
    }


def _attempt_lifecycle_fields(attempt_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not attempt_rows:
        return {
            "attempt_count": 0,
            "first_attempt_at": None,
            "last_attempt_at": None,
            "max_signal_age_seconds": None,
            "max_quote_age_seconds": None,
            "dominant_retry_reason": None,
        }
    retry_reasons = Counter(
        str(row.get("reason") or "")
        for row in attempt_rows
        if str(row.get("result") or "") == "retry" and str(row.get("reason") or "")
    )
    signal_ages = [_safe_float(row.get("signal_age_seconds")) for row in attempt_rows]
    quote_ages = [_safe_float(row.get("quote_age_seconds")) for row in attempt_rows]
    return {
        "attempt_count": int(len(attempt_rows)),
        "first_attempt_at": attempt_rows[0].get("cycle_at"),
        "last_attempt_at": attempt_rows[-1].get("cycle_at"),
        "max_signal_age_seconds": max((value for value in signal_ages if value is not None), default=None),
        "max_quote_age_seconds": max((value for value in quote_ages if value is not None), default=None),
        "dominant_retry_reason": retry_reasons.most_common(1)[0][0] if retry_reasons else None,
    }


def _attempt_quote_fields(attempt_rows: list[dict[str, Any]]) -> dict[str, Any]:
    entry_rows = [row for row in attempt_rows if str(row.get("attempt_stage") or "") == "entry"]
    if not entry_rows:
        return {
            "quote_source_mode": None,
            "quote_resolution_status": None,
            "capture_source": None,
            "best_bid_size": None,
            "best_ask_size": None,
            "transport_lag_ms": None,
        }
    selected = next((row for row in entry_rows if str(row.get("result") or "") == "filled"), entry_rows[-1])
    return {
        "quote_source_mode": selected.get("quote_source_mode"),
        "quote_resolution_status": selected.get("quote_resolution_status"),
        "capture_source": selected.get("capture_source"),
        "best_bid_size": selected.get("best_bid_size"),
        "best_ask_size": selected.get("best_ask_size"),
        "transport_lag_ms": selected.get("transport_lag_ms"),
    }


def _first_poll_fill_at(attempt_rows: list[dict[str, Any]]) -> datetime | None:
    for row in attempt_rows:
        if str(row.get("attempt_stage") or "") != "entry":
            continue
        if str(row.get("result") or "") == "filled":
            return _parse_datetime(row.get("cycle_at"))
    return None


def _first_visible_at(context: ReplayGameContext | None, record: dict[str, Any]) -> datetime | None:
    entry_at = _parse_datetime(record.get("entry_at"))
    if context is None or context.state_df.empty:
        return entry_at
    team_side = str(record.get("team_side") or "")
    entry_state_index = _safe_int(record.get("entry_state_index"))
    if not team_side or entry_state_index < 0:
        return entry_at
    state_rows = context.state_df[
        (context.state_df["team_side"].astype(str) == team_side)
        & (pd.to_numeric(context.state_df["state_index"], errors="coerce") >= entry_state_index)
    ].copy()
    if state_rows.empty:
        return entry_at
    state_rows["event_at"] = pd.to_datetime(state_rows["event_at"], errors="coerce", utc=True)
    state_rows = state_rows.dropna(subset=["event_at"]).sort_values(["event_at", "state_index"], kind="mergesort")
    if state_rows.empty:
        return entry_at
    return _parse_datetime(state_rows.iloc[0]["event_at"]) or entry_at


def _signal_stale_at(
    context: ReplayGameContext | None,
    record: dict[str, Any],
    *,
    request: ReplayRunRequest,
) -> datetime | None:
    entry_at = _parse_datetime(record.get("entry_at"))
    exit_at = _parse_datetime(record.get("exit_at"))
    if context is None or context.state_df.empty or entry_at is None or exit_at is None:
        return None
    team_side = str(record.get("team_side") or "")
    entry_state_index = _safe_int(record.get("entry_state_index"))
    if not team_side or entry_state_index < 0:
        return None
    state_rows = context.state_df[
        (context.state_df["team_side"].astype(str) == team_side)
        & (pd.to_numeric(context.state_df["state_index"], errors="coerce") > entry_state_index)
    ].copy()
    if state_rows.empty:
        return None
    state_rows["event_at"] = pd.to_datetime(state_rows["event_at"], errors="coerce", utc=True)
    state_rows = state_rows.dropna(subset=["event_at"]).sort_values(["event_at", "state_index"], kind="mergesort")
    for row in state_rows.to_dict(orient="records"):
        event_at = _parse_datetime(row.get("event_at"))
        if event_at is None or event_at >= exit_at:
            break
        signal_age_seconds = max(0.0, (event_at - entry_at).total_seconds())
        if signal_age_seconds > float(request.signal_max_age_seconds):
            return event_at
    return None


def _signal_candidate_times(
    context: ReplayGameContext | None,
    record: dict[str, Any],
    *,
    tick_lookup: dict[str, pd.DataFrame],
) -> list[datetime]:
    entry_at = _parse_datetime(record.get("entry_at"))
    exit_at = _parse_datetime(record.get("exit_at"))
    if context is None or entry_at is None or exit_at is None or entry_at >= exit_at:
        return []
    team_side = str(record.get("team_side") or "")
    opposite_side = "away" if team_side == "home" else "home"
    timestamps: set[datetime] = {entry_at}

    if not context.state_df.empty and team_side:
        state_rows = context.state_df[context.state_df["team_side"].astype(str) == team_side].copy()
        if not state_rows.empty:
            state_rows["event_at"] = pd.to_datetime(state_rows["event_at"], errors="coerce", utc=True)
            for value in state_rows["event_at"].tolist():
                event_at = _parse_datetime(value)
                if event_at is not None and entry_at <= event_at < exit_at:
                    timestamps.add(event_at)

    for side in (team_side, opposite_side):
        frame = tick_lookup.get(side, pd.DataFrame())
        if frame.empty:
            continue
        for value in frame["ts"].tolist():
            tick_at = _parse_datetime(value)
            if tick_at is not None and entry_at <= tick_at < exit_at:
                timestamps.add(tick_at)

    return sorted(timestamps)


def _blocker_class_for_signal(
    *,
    executed_flag: bool,
    no_trade_reason: str | None,
    terminal_no_trade_reason: str | None,
    cadence_blocker_flag: bool,
) -> tuple[str, str | None, str]:
    if executed_flag:
        return "executed", None, "executed"
    if cadence_blocker_flag:
        detail = no_trade_reason or terminal_no_trade_reason or "polling_cadence_miss"
        return "polling_cadence", detail, "polling_cadence"

    detail = str(no_trade_reason or terminal_no_trade_reason or "unknown")
    reason = str(terminal_no_trade_reason or no_trade_reason or "")
    if reason == "signal_stale" or detail == "signal_stale":
        return "signal_freshness", detail, "stale_gate"
    if detail == "quote_stale":
        return "quote_freshness", detail, "other"
    if detail == "spread_too_wide":
        return "spread_gate", detail, "other"
    if detail == "quote_unavailable":
        return "quote_coverage", detail, "other"
    if reason in {"entry_after_exit_signal", "game_finished_before_submit"}:
        return "submission_window", detail, "other"
    if reason in {"missing_game_context", "invalid_signal_timestamps"}:
        return "data_context", detail, "other"
    if reason == "game_position_exists":
        return "position_overlap", detail, "other"
    return "other", detail, "other"


def _build_signal_diagnostic_fields(
    *,
    context: ReplayGameContext | None,
    record: dict[str, Any],
    request: ReplayRunRequest,
    subject_name: str,
    subject_type: str,
    signal_id: str,
    executed_flag: bool,
    no_trade_reason: str | None,
    terminal_no_trade_reason: str | None,
    attempt_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    birth_at = _parse_datetime(record.get("entry_at"))
    first_visible_at = _first_visible_at(context, record)
    first_executable_poll_at = _first_poll_fill_at(attempt_rows)
    stale_at = _signal_stale_at(context, record, request=request)
    first_executable_event_at = None

    if context is not None and birth_at is not None and _parse_datetime(record.get("exit_at")) is not None:
        tick_lookup = _build_tick_lookup(context.bundle)
        signal_context = _signal_context_fields(context, record)
        for candidate_at in _signal_candidate_times(context, record, tick_lookup=tick_lookup):
            attempt_row = _evaluate_entry_attempt(
                context,
                record,
                request=request,
                subject_name=subject_name,
                subject_type=subject_type,
                signal_id=signal_id,
                cycle_at=candidate_at,
                attempt_index=-1,
                signal_context=signal_context,
                tick_lookup=tick_lookup,
            )
            if str(attempt_row.get("result") or "") == "filled":
                first_executable_event_at = candidate_at
                break
            if str(attempt_row.get("reason") or "") in {"signal_stale", "entry_after_exit_signal"}:
                break

    time_to_first_event = (
        max(0.0, (first_executable_event_at - birth_at).total_seconds())
        if birth_at is not None and first_executable_event_at is not None
        else None
    )
    time_to_first_poll = (
        max(0.0, (first_executable_poll_at - birth_at).total_seconds())
        if birth_at is not None and first_executable_poll_at is not None
        else None
    )
    time_to_stale = (
        max(0.0, (stale_at - birth_at).total_seconds())
        if birth_at is not None and stale_at is not None
        else None
    )
    cadence_gap_seconds = None
    if first_executable_event_at is not None and context is not None:
        next_poll_at = _next_poll_at_or_after(
            context.anchor_at,
            first_executable_event_at,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        cadence_gap_seconds = max(0.0, (next_poll_at - first_executable_event_at).total_seconds())

    cadence_blocker_flag = bool(
        not executed_flag
        and first_executable_event_at is not None
        and first_executable_poll_at is None
    )
    stale_before_first_executable_flag = bool(
        stale_at is not None
        and (first_executable_event_at is None or stale_at <= first_executable_event_at)
    )
    blocker_class, blocker_detail, cadence_vs_stale = _blocker_class_for_signal(
        executed_flag=executed_flag,
        no_trade_reason=no_trade_reason,
        terminal_no_trade_reason=terminal_no_trade_reason,
        cadence_blocker_flag=cadence_blocker_flag,
    )
    return {
        "first_visible_at": first_visible_at,
        "first_executable_event_at": first_executable_event_at,
        "first_executable_poll_at": first_executable_poll_at,
        "stale_at": stale_at,
        "time_to_first_executable_event_seconds": time_to_first_event,
        "time_to_first_executable_poll_seconds": time_to_first_poll,
        "time_to_stale_seconds": time_to_stale,
        "cadence_gap_seconds": cadence_gap_seconds,
        "event_level_opportunity_flag": first_executable_event_at is not None,
        "poll_level_opportunity_flag": first_executable_poll_at is not None,
        "cadence_blocker_flag": cadence_blocker_flag,
        "stale_before_first_executable_flag": stale_before_first_executable_flag,
        "replay_blocker_class": blocker_class,
        "replay_blocker_detail": blocker_detail,
        "cadence_vs_stale_blocker": cadence_vs_stale,
    }


def _build_signal_summary_row(
    *,
    context: ReplayGameContext | None,
    record: dict[str, Any],
    subject_name: str,
    subject_type: str,
    request: ReplayRunRequest,
    signal_id: str,
    executed_flag: bool,
    no_trade_reason: str | None,
    terminal_no_trade_reason: str | None,
    attempt_rows: list[dict[str, Any]],
    entry_fill_at: datetime | None,
    exit_fill_at: datetime | None,
    entry_fill_price: float | None,
    exit_fill_price: float | None,
    fill_delay_seconds: float | None,
    exit_delay_seconds: float | None,
) -> dict[str, Any]:
    signal_context = _signal_context_fields(context, record)
    lifecycle_fields = _attempt_lifecycle_fields(attempt_rows)
    quote_fields = _attempt_quote_fields(attempt_rows)
    diagnostic_fields = _build_signal_diagnostic_fields(
        context=context,
        record=record,
        request=request,
        subject_name=subject_name,
        subject_type=subject_type,
        signal_id=signal_id,
        executed_flag=executed_flag,
        no_trade_reason=no_trade_reason,
        terminal_no_trade_reason=terminal_no_trade_reason,
        attempt_rows=attempt_rows,
    )
    return {
        "subject_name": subject_name,
        "subject_type": subject_type,
        "game_id": str(record.get("game_id") or ""),
        "team_side": str(record.get("team_side") or ""),
        "signal_id": signal_id,
        "strategy_family": str(record.get("source_strategy_family") or record.get("strategy_family") or ""),
        **{key: signal_context[key] for key in ("period_label", "opening_band", "score_diff_bucket", "context_bucket")},
        "entry_state_index": _safe_int(record.get("entry_state_index")),
        "exit_state_index": _safe_int(record.get("exit_state_index")),
        "signal_entry_at": _parse_datetime(record.get("entry_at")),
        "signal_exit_at": _parse_datetime(record.get("exit_at")),
        "signal_entry_price": _safe_float(record.get("entry_price")),
        "signal_exit_price": _safe_float(record.get("exit_price")),
        "signal_window_seconds": signal_context.get("signal_window_seconds"),
        "period_elapsed_seconds": signal_context.get("period_elapsed_seconds"),
        "entry_window_label": signal_context.get("entry_window_label"),
        "executed_flag": executed_flag,
        "no_trade_reason": no_trade_reason,
        "terminal_no_trade_reason": terminal_no_trade_reason,
        "dominant_retry_reason": lifecycle_fields.get("dominant_retry_reason"),
        "attempt_count": lifecycle_fields.get("attempt_count"),
        "first_attempt_at": lifecycle_fields.get("first_attempt_at"),
        "last_attempt_at": lifecycle_fields.get("last_attempt_at"),
        "first_visible_at": diagnostic_fields.get("first_visible_at"),
        "first_executable_event_at": diagnostic_fields.get("first_executable_event_at"),
        "first_executable_poll_at": diagnostic_fields.get("first_executable_poll_at"),
        "stale_at": diagnostic_fields.get("stale_at"),
        "time_to_first_executable_event_seconds": diagnostic_fields.get("time_to_first_executable_event_seconds"),
        "time_to_first_executable_poll_seconds": diagnostic_fields.get("time_to_first_executable_poll_seconds"),
        "time_to_stale_seconds": diagnostic_fields.get("time_to_stale_seconds"),
        "cadence_gap_seconds": diagnostic_fields.get("cadence_gap_seconds"),
        "event_level_opportunity_flag": diagnostic_fields.get("event_level_opportunity_flag"),
        "poll_level_opportunity_flag": diagnostic_fields.get("poll_level_opportunity_flag"),
        "cadence_blocker_flag": diagnostic_fields.get("cadence_blocker_flag"),
        "stale_before_first_executable_flag": diagnostic_fields.get("stale_before_first_executable_flag"),
        "replay_blocker_class": diagnostic_fields.get("replay_blocker_class"),
        "replay_blocker_detail": diagnostic_fields.get("replay_blocker_detail"),
        "cadence_vs_stale_blocker": diagnostic_fields.get("cadence_vs_stale_blocker"),
        "max_signal_age_seconds": lifecycle_fields.get("max_signal_age_seconds"),
        "max_quote_age_seconds": lifecycle_fields.get("max_quote_age_seconds"),
        "entry_fill_at": entry_fill_at,
        "exit_fill_at": exit_fill_at,
        "entry_fill_price": entry_fill_price,
        "exit_fill_price": exit_fill_price,
        "fill_delay_seconds": fill_delay_seconds,
        "exit_delay_seconds": exit_delay_seconds,
        "quote_proxy": request.quote_proxy,
        "quote_source_mode": quote_fields.get("quote_source_mode") or str(request.quote_source_mode or request.quote_proxy),
        "quote_resolution_status": quote_fields.get("quote_resolution_status"),
        "capture_source": quote_fields.get("capture_source"),
        "best_bid_size": quote_fields.get("best_bid_size"),
        "best_ask_size": quote_fields.get("best_ask_size"),
        "transport_lag_ms": quote_fields.get("transport_lag_ms"),
        "state_source": signal_context.get("state_source"),
    }


def _infer_bundle_coverage_status(bundle: dict[str, Any]) -> tuple[str, str]:
    feature_snapshot = bundle.get("feature_snapshot") or {}
    coverage_status = str(feature_snapshot.get("coverage_status") or "").strip()

    selected_market = bundle.get("selected_market") or {}
    series = selected_market.get("series") or []
    side_map = {
        str(item.get("side") or ""): item
        for item in series
        if str(item.get("side") or "") in {"home", "away"}
    }
    has_both_sides = all(side in side_map for side in ("home", "away"))
    has_ticks_for_both = has_both_sides and all(bool((side_map[side].get("ticks") or [])) for side in ("home", "away"))
    play_by_play = bundle.get("play_by_play") or {}
    timed_items = [
        item
        for item in (play_by_play.get("items") or [])
        if _parse_datetime(item.get("time_actual")) is not None
    ]

    inferred_coverage_status = "missing_feature_snapshot"
    inferred_classification = "descriptive_only"
    if has_ticks_for_both and timed_items:
        inferred_coverage_status = "covered_partial"
        inferred_classification = "research_ready"
    elif has_ticks_for_both and series:
        inferred_coverage_status = "pregame_only"
    elif series:
        inferred_coverage_status = "covered_partial"

    if coverage_status in RESEARCH_READY_STATUSES:
        return coverage_status, "research_ready"
    if inferred_classification == "research_ready":
        return inferred_coverage_status, inferred_classification
    if coverage_status and coverage_status not in {"missing_feature_snapshot", "no_matching_event", "debug_only"}:
        return coverage_status, "research_ready" if coverage_status in RESEARCH_READY_STATUSES else "descriptive_only"
    return inferred_coverage_status, inferred_classification


def _load_regular_season_trade_frames() -> tuple[dict[str, pd.DataFrame], str]:
    bundle = load_analysis_consumer_bundle(
        AnalysisConsumerRequest(
            season="2025-26",
            season_phase="regular_season",
            analysis_version="v1_0_1",
        )
    )
    artifacts = bundle.backtest_payload.get("artifacts") or {}
    families = [*DEFAULT_MASTER_ROUTER_CORE_FAMILIES, *DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES]
    trade_frames = {
        family: pd.read_csv(str(artifacts[f"{family}_csv"]))
        for family in families
        if artifacts.get(f"{family}_csv")
    }
    return trade_frames, "regular_season_full_sample"


def _load_team_profile_context_lookup() -> dict[str, dict[str, Any]]:
    with managed_connection() as connection:
        with cursor_dict(connection) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM nba.nba_analysis_team_season_profiles
                WHERE season = %s AND season_phase = %s AND analysis_version = %s
                ORDER BY computed_at DESC;
                """,
                ("2025-26", "regular_season", "v1_0_1"),
            )
            rows = fetchall_dicts(cursor)
    return build_team_profile_context_lookup(pd.DataFrame(rows))


def build_controller_context(cache_path: Path) -> ControllerContext:
    trade_frames, selection_sample_name = _load_regular_season_trade_frames()
    selection_result = SimpleNamespace(trade_frames=trade_frames)
    registry = build_strategy_registry()
    priors = build_master_router_selection_priors(
        selection_result,
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    )
    family_profiles = _build_family_profiles(
        selection_result,
        registry=registry,
        strategy_families=[*DEFAULT_MASTER_ROUTER_CORE_FAMILIES, *DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES],
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    )
    return ControllerContext(
        selection_sample_name=selection_sample_name,
        priors=priors,
        family_profiles=family_profiles,
        historical_team_context_lookup=_load_team_profile_context_lookup(),
        llm_client=_resolve_openai_client(),
        llm_cache_store=_load_llm_cache(cache_path),
        llm_budget_state=_LLMBudgetState(spent_usd=0.0),
    )


def _query_finished_postseason_games(connection: Any, *, season: str) -> list[dict[str, Any]]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                game_id,
                season_phase,
                game_date,
                game_start_time,
                game_status,
                game_status_text,
                home_team_slug,
                away_team_slug
            FROM nba.nba_games
            WHERE season = %s
              AND season_phase IN ('play_in', 'playoffs')
              AND game_status = 3
            ORDER BY game_date ASC, game_id ASC;
            """,
            (season,),
        )
        return fetchall_dicts(cursor)


def load_finished_postseason_replay_contexts(
    *,
    season: str,
    analysis_version: str,
) -> tuple[dict[str, ReplayGameContext], pd.DataFrame, pd.DataFrame]:
    with managed_connection() as connection:
        finished_games = _query_finished_postseason_games(connection, season=season)
        canonical_state_df = load_analysis_backtest_state_panel_df(
            connection,
            season=season,
            season_phase="playoffs",
            season_phases=("play_in", "playoffs"),
            analysis_version=analysis_version,
        )
        state_lookup = _build_state_lookup_by_game(canonical_state_df)

        contexts: dict[str, ReplayGameContext] = {}
        state_frames: list[pd.DataFrame] = []
        manifest_rows: list[dict[str, Any]] = []

        for game_row in finished_games:
            game_id = str(game_row["game_id"])
            bundle = load_analysis_bundle(connection, game_id=game_id)
            if bundle is None:
                manifest_rows.append(
                    {
                        "game_id": game_id,
                        "season_phase": game_row.get("season_phase"),
                        "game_date": game_row.get("game_date"),
                        "state_source": "missing_bundle",
                        "coverage_status": "bundle_missing",
                        "classification": "descriptive_only",
                        "state_row_count": 0,
                    }
                )
                continue
            coverage_status, classification = _infer_bundle_coverage_status(bundle)
            if game_id in state_lookup:
                game_state_df = state_lookup[game_id].copy()
                state_source = "state_panel"
            else:
                universe_row = pd.Series(
                    {
                        "season": str(bundle["game"].get("season") or season),
                        "season_phase": str(bundle["game"].get("season_phase") or game_row.get("season_phase") or "playoffs"),
                        "coverage_status": coverage_status,
                        "classification": classification,
                        "research_ready_flag": classification == "research_ready",
                    }
                )
                _, state_rows, _ = derive_game_rows(
                    universe_row=universe_row,
                    bundle=bundle,
                    analysis_version=analysis_version,
                    computed_at=datetime.now(timezone.utc),
                    build_state_rows_for_side=build_state_rows_for_side,
                )
                game_state_df = pd.DataFrame(state_rows)
                state_source = "derived_bundle"
            if not game_state_df.empty:
                game_state_df = game_state_df.sort_values(["event_at", "team_side", "state_index"], kind="mergesort").reset_index(drop=True)
                state_frames.append(game_state_df)
            all_times = [
                timestamp
                for timestamp in [
                    _parse_datetime(bundle["game"].get("game_start_time")),
                    _parse_datetime(bundle.get("play_by_play", {}).get("summary", {}).get("first_event_at")),
                    _parse_datetime(bundle.get("play_by_play", {}).get("summary", {}).get("last_event_at")),
                ]
                if timestamp is not None
            ]
            selected_market = bundle.get("selected_market") or {}
            for item in selected_market.get("series") or []:
                for tick in item.get("ticks") or []:
                    ts = _parse_datetime(tick.get("ts"))
                    if ts is not None:
                        all_times.append(ts)
            if not all_times:
                manifest_rows.append(
                    {
                        "game_id": game_id,
                        "season_phase": game_row.get("season_phase"),
                        "game_date": game_row.get("game_date"),
                        "state_source": state_source,
                        "coverage_status": coverage_status,
                        "classification": classification,
                        "state_row_count": int(len(game_state_df)),
                    }
                )
                continue
            anchor_at = min(all_times)
            end_at = max(all_times)
            contexts[game_id] = ReplayGameContext(
                game_id=game_id,
                season_phase=str(game_row.get("season_phase") or bundle["game"].get("season_phase") or "playoffs"),
                game=bundle["game"],
                bundle=bundle,
                state_df=game_state_df,
                state_source=state_source,
                coverage_status=coverage_status,
                classification=classification,
                anchor_at=anchor_at,
                end_at=end_at,
            )
            manifest_rows.append(
                {
                    "game_id": game_id,
                    "season_phase": game_row.get("season_phase"),
                    "game_date": game_row.get("game_date"),
                    "state_source": state_source,
                    "coverage_status": coverage_status,
                    "classification": classification,
                    "state_row_count": int(len(game_state_df)),
                }
            )

    combined_state_df = pd.concat(state_frames, ignore_index=True) if state_frames else pd.DataFrame()
    manifest_df = pd.DataFrame(manifest_rows).sort_values(["game_date", "game_id"], kind="mergesort").reset_index(drop=True)
    return contexts, combined_state_df, manifest_df


def _build_tick_lookup(bundle: dict[str, Any]) -> dict[str, pd.DataFrame]:
    selected_market = bundle.get("selected_market") or {}
    lookup: dict[str, pd.DataFrame] = {}
    for item in selected_market.get("series") or []:
        side = str(item.get("side") or "")
        if side not in {"home", "away"}:
            continue
        rows: list[dict[str, Any]] = []
        for tick in item.get("ticks") or []:
            ts = _parse_datetime(tick.get("ts"))
            price = _safe_float(tick.get("price"))
            if ts is None or price is None:
                continue
            rows.append({"ts": ts, "price": float(price)})
        lookup[side] = pd.DataFrame(rows).sort_values("ts", kind="mergesort").reset_index(drop=True) if rows else pd.DataFrame(columns=["ts", "price"])
    return lookup


def _latest_tick_before(frame: pd.DataFrame, *, cycle_at: datetime) -> dict[str, Any] | None:
    if frame.empty:
        return None
    work = frame[frame["ts"] <= cycle_at]
    if work.empty:
        return None
    return work.iloc[-1].to_dict()


def _latest_historical_bidask_row_before(
    frame: pd.DataFrame,
    *,
    team_side: str,
    cycle_at: datetime,
) -> dict[str, Any] | None:
    if frame.empty:
        return None
    work = frame[
        (frame["team_side"].astype(str) == str(team_side))
        & (pd.to_datetime(frame["captured_at_utc"], errors="coerce", utc=True) <= cycle_at)
    ]
    if work.empty:
        return None
    return work.sort_values(["captured_at_utc", "source_sequence_id"], kind="mergesort").iloc[-1].to_dict()


def _build_proxy_quote_snapshot(
    context: ReplayGameContext,
    *,
    team_side: str,
    cycle_at: datetime,
    request: ReplayRunRequest,
    tick_lookup: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    tick_lookup = tick_lookup or _build_tick_lookup(context.bundle)
    own_tick = _latest_tick_before(tick_lookup.get(team_side, pd.DataFrame()), cycle_at=cycle_at)
    opposite_side = "away" if team_side == "home" else "home"
    opposite_tick = _latest_tick_before(tick_lookup.get(opposite_side, pd.DataFrame()), cycle_at=cycle_at)
    if own_tick is None or opposite_tick is None:
        return {
            "quote_proxy": request.quote_proxy,
            "quote_source_mode": str(request.quote_source_mode or request.quote_proxy),
            "quote_resolution_status": "quote_unavailable",
            "capture_source": request.quote_proxy,
            "available": False,
            "reason": "quote_unavailable",
            "quote_time": None,
            "quote_age_seconds": None,
            "best_bid": None,
            "best_ask": None,
            "best_bid_size": None,
            "best_ask_size": None,
            "spread_cents": None,
            "transport_lag_ms": None,
        }
    own_ts = _parse_datetime(own_tick.get("ts"))
    opposite_ts = _parse_datetime(opposite_tick.get("ts"))
    proxy_fields = build_proxy_quote_fields_from_cross_side_ticks(
        own_price=float(own_tick["price"]),
        opposite_price=float(opposite_tick["price"]),
        own_ts=own_ts or cycle_at,
        opposite_ts=opposite_ts or cycle_at,
        proxy_min_spread_cents=float(request.proxy_min_spread_cents),
        proxy_max_spread_cents=float(request.proxy_max_spread_cents),
    )
    quote_time = _parse_datetime(proxy_fields.get("captured_at_utc"))
    quote_age_seconds = max(0.0, (cycle_at - quote_time).total_seconds()) if quote_time is not None else None
    return {
        "quote_proxy": request.quote_proxy,
        "quote_source_mode": str(request.quote_source_mode or request.quote_proxy),
        "quote_resolution_status": "proxy_from_cross_side_ticks",
        "capture_source": request.quote_proxy,
        "available": True,
        "reason": None,
        "quote_time": quote_time,
        "quote_age_seconds": quote_age_seconds,
        "best_bid": proxy_fields.get("best_bid_price"),
        "best_ask": proxy_fields.get("best_ask_price"),
        "best_bid_size": None,
        "best_ask_size": None,
        "spread_cents": proxy_fields.get("spread_cents"),
        "transport_lag_ms": proxy_fields.get("source_latency_ms"),
    }


def _build_historical_bidask_quote_snapshot(
    context: ReplayGameContext,
    *,
    team_side: str,
    cycle_at: datetime,
    request: ReplayRunRequest,
) -> dict[str, Any]:
    frame = context.historical_bidask_df if isinstance(context.historical_bidask_df, pd.DataFrame) else pd.DataFrame()
    row = _latest_historical_bidask_row_before(frame, team_side=team_side, cycle_at=cycle_at)
    if row is None:
        return {
            "quote_proxy": request.quote_proxy,
            "quote_source_mode": "historical_bidask_l1",
            "quote_resolution_status": "missing_historical_bidask",
            "capture_source": "historical_bidask_l1",
            "available": False,
            "reason": "quote_unavailable",
            "quote_time": None,
            "quote_age_seconds": None,
            "best_bid": None,
            "best_ask": None,
            "best_bid_size": None,
            "best_ask_size": None,
            "spread_cents": None,
            "transport_lag_ms": None,
        }
    best_bid = _safe_float(row.get("best_bid_price"))
    best_ask = _safe_float(row.get("best_ask_price"))
    captured_at = _parse_datetime(row.get("captured_at_utc"))
    transport_lag_ms = _safe_float(row.get("source_latency_ms")) or 0.0
    quote_time = (
        captured_at - timedelta(milliseconds=transport_lag_ms)
        if captured_at is not None
        else None
    )
    quote_age_seconds = max(0.0, (cycle_at - quote_time).total_seconds()) if quote_time is not None else None
    if best_bid is None or best_ask is None:
        return {
            "quote_proxy": request.quote_proxy,
            "quote_source_mode": "historical_bidask_l1",
            "quote_resolution_status": str(row.get("quote_resolution_status") or row.get("capture_status") or "unresolved"),
            "capture_source": str(row.get("capture_source") or "historical_bidask_l1"),
            "available": False,
            "reason": "quote_unavailable",
            "quote_time": quote_time,
            "quote_age_seconds": quote_age_seconds,
            "best_bid": None,
            "best_ask": None,
            "best_bid_size": _safe_float(row.get("best_bid_size")),
            "best_ask_size": _safe_float(row.get("best_ask_size")),
            "spread_cents": None,
            "transport_lag_ms": transport_lag_ms,
        }
    return {
        "quote_proxy": request.quote_proxy,
        "quote_source_mode": "historical_bidask_l1",
        "quote_resolution_status": str(row.get("quote_resolution_status") or row.get("capture_status") or "historical_bidask_l1"),
        "capture_source": str(row.get("capture_source") or "historical_bidask_l1"),
        "available": True,
        "reason": None,
        "quote_time": quote_time,
        "quote_age_seconds": quote_age_seconds,
        "best_bid": float(best_bid),
        "best_ask": float(best_ask),
        "best_bid_size": _safe_float(row.get("best_bid_size")),
        "best_ask_size": _safe_float(row.get("best_ask_size")),
        "spread_cents": round(max(0.0, float(best_ask) - float(best_bid)) * 100.0, 4),
        "transport_lag_ms": transport_lag_ms,
    }


def _build_quote_snapshot(
    context: ReplayGameContext,
    *,
    team_side: str,
    cycle_at: datetime,
    request: ReplayRunRequest,
    tick_lookup: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    quote_source_mode = str(request.quote_source_mode or request.quote_proxy).strip() or request.quote_proxy
    if quote_source_mode == "historical_bidask_l1":
        historical_quote = _build_historical_bidask_quote_snapshot(
            context,
            team_side=team_side,
            cycle_at=cycle_at,
            request=request,
        )
        if historical_quote.get("available"):
            return historical_quote
        if str(request.quote_source_fallback_mode or "").strip() == request.quote_proxy:
            proxy_quote = _build_proxy_quote_snapshot(
                context,
                team_side=team_side,
                cycle_at=cycle_at,
                request=request,
                tick_lookup=tick_lookup,
            )
            proxy_quote["quote_source_mode"] = "historical_bidask_l1"
            proxy_quote["quote_resolution_status"] = (
                f"fallback_proxy_{historical_quote.get('quote_resolution_status') or historical_quote.get('reason') or 'missing'}"
            )
            proxy_quote["capture_source"] = "fallback_cross_side_last_trade"
            return proxy_quote
        return historical_quote
    return _build_proxy_quote_snapshot(
        context,
        team_side=team_side,
        cycle_at=cycle_at,
        request=request,
        tick_lookup=tick_lookup,
    )


def _next_poll_at_or_after(anchor_at: datetime, target_at: datetime, *, poll_interval_seconds: float) -> datetime:
    if target_at <= anchor_at:
        return anchor_at
    elapsed = max(0.0, (target_at - anchor_at).total_seconds())
    steps = int(math.ceil(elapsed / max(poll_interval_seconds, 1e-6) - 1e-9))
    return anchor_at + timedelta(seconds=steps * poll_interval_seconds)


def _latest_state_before(state_df: pd.DataFrame, *, team_side: str, cycle_at: datetime) -> dict[str, Any] | None:
    if state_df.empty:
        return None
    work = state_df[
        (state_df["team_side"].astype(str) == str(team_side))
        & (pd.to_datetime(state_df["event_at"], errors="coerce", utc=True) <= cycle_at)
    ]
    if work.empty:
        return None
    return work.sort_values(["event_at", "state_index"], kind="mergesort").iloc[-1].to_dict()


def _evaluate_entry_attempt(
    context: ReplayGameContext,
    record: dict[str, Any],
    *,
    request: ReplayRunRequest,
    subject_name: str,
    subject_type: str,
    signal_id: str,
    cycle_at: datetime,
    attempt_index: int,
    signal_context: dict[str, Any],
    tick_lookup: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    team_side = str(record.get("team_side") or "")
    entry_at = _parse_datetime(record.get("entry_at")) or cycle_at
    exit_at = _parse_datetime(record.get("exit_at")) or cycle_at
    entry_state_index = _safe_int(record.get("entry_state_index"))
    exit_state_index = _safe_int(record.get("exit_state_index"), default=entry_state_index)
    latest_state = _latest_state_before(context.state_df, team_side=team_side, cycle_at=cycle_at)
    latest_state_index = _safe_int((latest_state or {}).get("state_index"))
    latest_state_at = _parse_datetime((latest_state or {}).get("event_at"))
    state_signal_age = (
        max(0.0, (latest_state_at - entry_at).total_seconds())
        if latest_state_at is not None and latest_state_index > entry_state_index
        else 0.0
    )
    quote = _build_quote_snapshot(
        context,
        team_side=team_side,
        cycle_at=cycle_at,
        request=request,
        tick_lookup=tick_lookup,
    )
    result = "retry"
    reason = "waiting_for_signal"
    if latest_state is None or latest_state_index < entry_state_index:
        reason = "signal_not_visible_yet"
    elif latest_state_index >= exit_state_index or cycle_at >= exit_at:
        reason = "entry_after_exit_signal"
        result = "no_trade"
    elif latest_state_index > entry_state_index and state_signal_age > float(request.signal_max_age_seconds):
        reason = "signal_stale"
        result = "no_trade"
    elif not quote.get("available"):
        reason = "quote_unavailable"
    elif (quote.get("quote_age_seconds") or 0.0) > float(request.quote_max_age_seconds):
        reason = "quote_stale"
    elif (quote.get("spread_cents") or 0.0) > float(request.max_spread_cents):
        reason = "spread_too_wide"
    else:
        result = "filled"
        reason = "entry_filled"

    return {
        "subject_name": subject_name,
        "subject_type": subject_type,
        "game_id": str(record.get("game_id") or ""),
        "signal_id": signal_id,
        "period_label": signal_context.get("period_label"),
        "entry_window_label": signal_context.get("entry_window_label"),
        "attempt_stage": "entry",
        "cycle_at": cycle_at,
        "attempt_index": attempt_index,
        "result": result,
        "reason": reason,
        "entry_state_index": entry_state_index,
        "latest_state_index": latest_state_index,
        "latest_state_at": latest_state_at,
        "signal_age_seconds": state_signal_age,
        "quote_time": quote.get("quote_time"),
        "quote_age_seconds": quote.get("quote_age_seconds"),
        "best_bid": quote.get("best_bid"),
        "best_ask": quote.get("best_ask"),
        "best_bid_size": quote.get("best_bid_size"),
        "best_ask_size": quote.get("best_ask_size"),
        "spread_cents": quote.get("spread_cents"),
        "quote_source_mode": quote.get("quote_source_mode"),
        "quote_resolution_status": quote.get("quote_resolution_status"),
        "capture_source": quote.get("capture_source"),
        "transport_lag_ms": quote.get("transport_lag_ms"),
        "state_source": context.state_source,
    }


def _replay_exit(
    context: ReplayGameContext,
    record: dict[str, Any],
    *,
    fill_at: datetime,
    fill_price: float,
    request: ReplayRunRequest,
    attempt_rows: list[dict[str, Any]],
) -> tuple[datetime, float, float, str]:
    exit_at = _parse_datetime(record.get("exit_at")) or fill_at
    cycle_at = _next_poll_at_or_after(context.anchor_at, max(fill_at, exit_at), poll_interval_seconds=request.poll_interval_seconds)
    end_limit = context.end_at + timedelta(seconds=request.poll_interval_seconds * 3.0)
    stop_price = _extract_stop_price(record)
    aggressive_flag = bool(
        (stop_price is not None and _safe_float(record.get("exit_price")) is not None and float(record.get("exit_price")) <= float(stop_price))
        or "overlay_stop" in str(record.get("exit_rule") or "")
    )
    attempt_index = 0
    while cycle_at <= end_limit:
        quote = _build_quote_snapshot(context, team_side=str(record.get("team_side") or ""), cycle_at=cycle_at, request=request)
        attempt_rows.append(
            {
                "subject_name": str(record.get("subject_name") or ""),
                "subject_type": str(record.get("subject_type") or ""),
                "game_id": str(record.get("game_id") or ""),
                "signal_id": _signal_id_for_trade(record),
                "attempt_stage": "exit",
                "cycle_at": cycle_at,
                "attempt_index": attempt_index,
                "result": "retry" if not quote.get("available") or (quote.get("quote_age_seconds") or 0.0) > request.quote_max_age_seconds else "filled",
                "reason": "quote_unavailable"
                if not quote.get("available")
                else "quote_stale"
                if (quote.get("quote_age_seconds") or 0.0) > request.quote_max_age_seconds
                else "exit_filled",
                "entry_state_index": record.get("entry_state_index"),
                "latest_state_index": record.get("exit_state_index"),
                "quote_time": quote.get("quote_time"),
                "quote_age_seconds": quote.get("quote_age_seconds"),
                "best_bid": quote.get("best_bid"),
                "best_ask": quote.get("best_ask"),
                "best_bid_size": quote.get("best_bid_size"),
                "best_ask_size": quote.get("best_ask_size"),
                "spread_cents": quote.get("spread_cents"),
                "quote_source_mode": quote.get("quote_source_mode"),
                "quote_resolution_status": quote.get("quote_resolution_status"),
                "capture_source": quote.get("capture_source"),
                "transport_lag_ms": quote.get("transport_lag_ms"),
            }
        )
        if quote.get("available") and (quote.get("quote_age_seconds") or 0.0) <= request.quote_max_age_seconds:
            best_bid = float(quote.get("best_bid") or 0.0)
            if aggressive_flag:
                exit_price = max(0.01, best_bid - (float(request.aggressive_exit_slippage_cents) / 100.0))
            else:
                exit_price = best_bid
            return cycle_at, float(exit_price), max(0.0, (cycle_at - exit_at).total_seconds()), "quote_exit"
        cycle_at += timedelta(seconds=request.poll_interval_seconds)
        attempt_index += 1
    fallback_exit_price = _safe_float(record.get("exit_price")) or fill_price
    return end_limit, float(fallback_exit_price), max(0.0, (end_limit - exit_at).total_seconds()), "final_settlement_fallback"


def _build_replay_trade_row(
    record: dict[str, Any],
    *,
    fill_at: datetime,
    fill_price: float,
    exit_fill_at: datetime,
    exit_fill_price: float,
    request: ReplayRunRequest,
    exit_fill_mode: str,
) -> dict[str, Any]:
    entry_exec = max(1e-6, float(fill_price))
    exit_exec = max(0.0, float(exit_fill_price))
    gross_return = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
    signal_entry_at = _parse_datetime(record.get("entry_at"))
    signal_exit_at = _parse_datetime(record.get("exit_at"))
    return {
        **record,
        "entry_at": fill_at,
        "exit_at": exit_fill_at,
        "entry_price": float(fill_price),
        "exit_price": float(exit_fill_price),
        "gross_return": gross_return,
        "gross_return_with_slippage": gross_return,
        "hold_time_seconds": max(0.0, (exit_fill_at - fill_at).total_seconds()),
        "slippage_cents": 0,
        "engine_mode": "execution_replay",
        "execution_profile_version": "replay_v1",
        "signal_entry_at": signal_entry_at,
        "signal_exit_at": signal_exit_at,
        "signal_entry_price": _safe_float(record.get("entry_price")),
        "signal_exit_price": _safe_float(record.get("exit_price")),
        "signal_age_seconds_at_submit": max(0.0, (fill_at - (signal_entry_at or fill_at)).total_seconds()),
        "quote_age_seconds_at_submit": None,
        "spread_cents_at_submit": None,
        "submission_window_label": "signal_fresh",
        "replay_trade_status": "executed",
        "skip_reason": None,
        "no_trade_reason": None,
        "exit_fill_mode": exit_fill_mode,
    }


def _replay_single_signal(
    context: ReplayGameContext,
    record: dict[str, Any],
    *,
    request: ReplayRunRequest,
    subject_name: str,
    subject_type: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    attempt_rows: list[dict[str, Any]] = []
    entry_at = _parse_datetime(record.get("entry_at"))
    exit_at = _parse_datetime(record.get("exit_at"))
    entry_state_index = _safe_int(record.get("entry_state_index"))
    exit_state_index = _safe_int(record.get("exit_state_index"), default=entry_state_index)
    signal_id = _signal_id_for_trade({**record, "subject_name": subject_name})
    if entry_at is None or exit_at is None:
        summary_row = _build_signal_summary_row(
            context=context,
            record={**record, "exit_state_index": exit_state_index},
            subject_name=subject_name,
            subject_type=subject_type,
            request=request,
            signal_id=signal_id,
            executed_flag=False,
            no_trade_reason="invalid_signal_timestamps",
            terminal_no_trade_reason="invalid_signal_timestamps",
            attempt_rows=attempt_rows,
            entry_fill_at=None,
            exit_fill_at=None,
            entry_fill_price=None,
            exit_fill_price=None,
            fill_delay_seconds=None,
            exit_delay_seconds=None,
        )
        return summary_row, attempt_rows, None

    cycle_at = _next_poll_at_or_after(context.anchor_at, entry_at, poll_interval_seconds=request.poll_interval_seconds)
    attempt_index = 0
    last_retry_reason: str | None = None
    signal_context = _signal_context_fields(context, record)
    tick_lookup = _build_tick_lookup(context.bundle)
    while cycle_at <= context.end_at + timedelta(seconds=request.poll_interval_seconds):
        attempt_row = _evaluate_entry_attempt(
            context,
            record,
            request=request,
            subject_name=subject_name,
            subject_type=subject_type,
            signal_id=signal_id,
            cycle_at=cycle_at,
            attempt_index=attempt_index,
            signal_context=signal_context,
            tick_lookup=tick_lookup,
        )
        result = str(attempt_row.get("result") or "")
        reason = str(attempt_row.get("reason") or "")
        if result == "retry" and reason in {"quote_unavailable", "quote_stale", "spread_too_wide"}:
            last_retry_reason = reason

        attempt_rows.append(attempt_row)
        if result == "filled":
            fill_price = float(attempt_row.get("best_ask") or _safe_float(record.get("entry_price")) or 0.0)
            exit_fill_at, exit_fill_price, exit_delay_seconds, exit_fill_mode = _replay_exit(
                context,
                {**record, "subject_name": subject_name, "subject_type": subject_type},
                fill_at=cycle_at,
                fill_price=fill_price,
                request=request,
                attempt_rows=attempt_rows,
            )
            trade_row = _build_replay_trade_row(
                {**record, "subject_name": subject_name, "subject_type": subject_type},
                fill_at=cycle_at,
                fill_price=fill_price,
                exit_fill_at=exit_fill_at,
                exit_fill_price=exit_fill_price,
                request=request,
                exit_fill_mode=exit_fill_mode,
            )
            trade_row["quote_age_seconds_at_submit"] = attempt_row.get("quote_age_seconds")
            trade_row["spread_cents_at_submit"] = attempt_row.get("spread_cents")
            summary_row = _build_signal_summary_row(
                context=context,
                record={**record, "exit_state_index": exit_state_index},
                subject_name=subject_name,
                subject_type=subject_type,
                request=request,
                signal_id=signal_id,
                executed_flag=True,
                no_trade_reason=None,
                terminal_no_trade_reason=None,
                attempt_rows=attempt_rows,
                entry_fill_at=cycle_at,
                exit_fill_at=exit_fill_at,
                entry_fill_price=fill_price,
                exit_fill_price=exit_fill_price,
                fill_delay_seconds=max(0.0, (cycle_at - entry_at).total_seconds()),
                exit_delay_seconds=exit_delay_seconds,
            )
            return summary_row, attempt_rows, trade_row
        if result == "no_trade":
            final_reason = last_retry_reason if reason == "entry_after_exit_signal" and last_retry_reason else reason
            summary_row = _build_signal_summary_row(
                context=context,
                record={**record, "exit_state_index": exit_state_index},
                subject_name=subject_name,
                subject_type=subject_type,
                request=request,
                signal_id=signal_id,
                executed_flag=False,
                no_trade_reason=final_reason,
                terminal_no_trade_reason=reason,
                attempt_rows=attempt_rows,
                entry_fill_at=None,
                exit_fill_at=None,
                entry_fill_price=None,
                exit_fill_price=None,
                fill_delay_seconds=None,
                exit_delay_seconds=None,
            )
            return summary_row, attempt_rows, None
        cycle_at += timedelta(seconds=request.poll_interval_seconds)
        attempt_index += 1

    summary_row = _build_signal_summary_row(
        context=context,
        record={**record, "exit_state_index": exit_state_index},
        subject_name=subject_name,
        subject_type=subject_type,
        request=request,
        signal_id=signal_id,
        executed_flag=False,
        no_trade_reason=last_retry_reason or "game_finished_before_submit",
        terminal_no_trade_reason="game_finished_before_submit",
        attempt_rows=attempt_rows,
        entry_fill_at=None,
        exit_fill_at=None,
        entry_fill_price=None,
        exit_fill_price=None,
        fill_delay_seconds=None,
        exit_delay_seconds=None,
    )
    return summary_row, attempt_rows, None


def simulate_replay_trade_frames(
    subjects: list[ReplaySubject],
    *,
    contexts: dict[str, ReplayGameContext],
    request: ReplayRunRequest,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    replay_frames: dict[str, pd.DataFrame] = {}
    signal_rows: list[dict[str, Any]] = []
    attempt_rows: list[dict[str, Any]] = []
    for subject in subjects:
        executed_rows: list[dict[str, Any]] = []
        subject_frame = subject.standard_frame.copy()
        if subject_frame.empty:
            replay_frames[subject.subject_name] = pd.DataFrame(columns=[*BACKTEST_TRADE_COLUMNS, "engine_mode"])
            continue
        ordered = subject_frame.sort_values(["game_id", "entry_at", "entry_state_index"], kind="mergesort").reset_index(drop=True)
        for game_id, game_rows in ordered.groupby("game_id", sort=False):
            context = contexts.get(str(game_id))
            if context is None:
                for record in game_rows.to_dict(orient="records"):
                    signal_id = _signal_id_for_trade({**record, "subject_name": subject.subject_name})
                    signal_rows.append(
                        _build_signal_summary_row(
                            context=None,
                            record=record,
                            subject_name=subject.subject_name,
                            subject_type=subject.subject_type,
                            request=request,
                            signal_id=signal_id,
                            executed_flag=False,
                            no_trade_reason="missing_game_context",
                            terminal_no_trade_reason="missing_game_context",
                            attempt_rows=[],
                            entry_fill_at=None,
                            exit_fill_at=None,
                            entry_fill_price=None,
                            exit_fill_price=None,
                            fill_delay_seconds=None,
                            exit_delay_seconds=None,
                        )
                    )
                continue
            game_has_executed_trade = False
            for record in game_rows.to_dict(orient="records"):
                if game_has_executed_trade:
                    signal_id = _signal_id_for_trade({**record, "subject_name": subject.subject_name})
                    signal_rows.append(
                        _build_signal_summary_row(
                            context=context,
                            record=record,
                            subject_name=subject.subject_name,
                            subject_type=subject.subject_type,
                            request=request,
                            signal_id=signal_id,
                            executed_flag=False,
                            no_trade_reason="game_position_exists",
                            terminal_no_trade_reason="game_position_exists",
                            attempt_rows=[],
                            entry_fill_at=None,
                            exit_fill_at=None,
                            entry_fill_price=None,
                            exit_fill_price=None,
                            fill_delay_seconds=None,
                            exit_delay_seconds=None,
                        )
                    )
                    continue
                summary_row, candidate_attempts, trade_row = _replay_single_signal(
                    context,
                    {**record, "subject_name": subject.subject_name, "subject_type": subject.subject_type},
                    request=request,
                    subject_name=subject.subject_name,
                    subject_type=subject.subject_type,
                )
                signal_rows.append(summary_row)
                attempt_rows.extend(candidate_attempts)
                if trade_row is not None:
                    executed_rows.append(trade_row)
                    game_has_executed_trade = True
        replay_frames[subject.subject_name] = pd.DataFrame(
            executed_rows,
            columns=[*BACKTEST_TRADE_COLUMNS, "engine_mode", "execution_profile_version", "signal_entry_at", "signal_exit_at", "signal_entry_price", "signal_exit_price", "signal_age_seconds_at_submit", "quote_age_seconds_at_submit", "spread_cents_at_submit", "submission_window_label", "replay_trade_status", "skip_reason", "no_trade_reason", "exit_fill_mode", "subject_name", "subject_type"],
        )
    signal_summary_df = pd.DataFrame(signal_rows, columns=REPLAY_SIGNAL_SUMMARY_COLUMNS)
    attempt_trace_df = pd.DataFrame(attempt_rows, columns=REPLAY_ATTEMPT_TRACE_COLUMNS)
    return replay_frames, signal_summary_df, attempt_trace_df


def _build_subjects(
    *,
    combined_state_df: pd.DataFrame,
    request: ReplayRunRequest,
    output_dir: Path,
) -> tuple[list[ReplaySubject], dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, Any]]:
    request_payload = asdict(request)
    extended_request = ReplayRunRequest(
        **{
            **request_payload,
            "season_phase": "postseason_to_date",
            "season_phases": ("play_in", "playoffs"),
            "strategy_group": REPLAY_HF_STRATEGY_GROUP,
        }
    )
    extended_result = build_backtest_result(combined_state_df, extended_request)

    controller_context = build_controller_context(output_dir / "replay_llm_cache.json")
    controller_request = ReplayRunRequest(
        **{
            **request_payload,
            "season_phase": "postseason_to_date",
            "season_phases": ("play_in", "playoffs"),
            "strategy_group": "default",
        }
    )
    controller_result = build_backtest_result(combined_state_df, controller_request)
    controller_state_lookup = build_state_lookup(controller_result.state_df)

    deterministic_trades, deterministic_decisions = build_master_router_trade_frame(
        controller_result,
        sample_name="postseason_to_date",
        selection_sample_name=controller_context.selection_sample_name,
        priors=controller_context.priors,
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
        extra_strategy_families=DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
        **_LIVE_MASTER_ROUTER_KWARGS,
    )
    deterministic_trades = decorate_trade_frame_with_vnext_sizing(
        apply_stop_overlay(deterministic_trades, state_lookup=controller_state_lookup, stop_map=DEFAULT_VNEXT_STOP_MAP),
        profile=DEFAULT_VNEXT_PROFILE,
    )

    unified_trades, unified_decisions, unified_token_totals = build_unified_router_trade_frame(
        controller_result,
        sample_name="postseason_to_date",
        selection_sample_name=controller_context.selection_sample_name,
        priors=controller_context.priors,
        family_profiles=controller_context.family_profiles,
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
        extra_strategy_families=DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
        llm_lane=_LIVE_UNIFIED_LLM_LANE,
        request=controller_request,
        client=controller_context.llm_client,
        budget_state=controller_context.llm_budget_state,
        cache_store=controller_context.llm_cache_store,
        historical_team_context_lookup=controller_context.historical_team_context_lookup,
        **_LIVE_UNIFIED_KWARGS,
    )
    unified_trades = decorate_trade_frame_with_vnext_sizing(
        apply_stop_overlay(unified_trades, state_lookup=controller_state_lookup, stop_map=DEFAULT_VNEXT_STOP_MAP),
        profile=DEFAULT_VNEXT_PROFILE,
    )

    standard_trade_frames: dict[str, pd.DataFrame] = {
        family: frame.copy()
        for family, frame in extended_result.trade_frames.items()
    }
    standard_trade_frames[LIVE_FALLBACK_CONTROLLER] = deterministic_trades.copy()
    standard_trade_frames[LIVE_PRIMARY_CONTROLLER] = unified_trades.copy()
    standard_decision_frames = {
        LIVE_FALLBACK_CONTROLLER: deterministic_decisions.copy(),
        LIVE_PRIMARY_CONTROLLER: unified_decisions.copy(),
    }
    subjects = [
        ReplaySubject(subject_name=family, subject_type="family", standard_frame=frame.copy())
        for family, frame in standard_trade_frames.items()
        if family not in {LIVE_FALLBACK_CONTROLLER, LIVE_PRIMARY_CONTROLLER}
    ]
    subjects.extend(
        [
            ReplaySubject(subject_name=LIVE_FALLBACK_CONTROLLER, subject_type="controller", standard_frame=deterministic_trades.copy()),
            ReplaySubject(subject_name=LIVE_PRIMARY_CONTROLLER, subject_type="controller", standard_frame=unified_trades.copy()),
        ]
    )
    metadata = {
        "llm_client_available": bool(controller_context.llm_client),
        "llm_spent_usd": float(controller_context.llm_budget_state.spent_usd),
        "unified_token_totals": to_jsonable(unified_token_totals),
    }
    return subjects, standard_trade_frames, standard_decision_frames, metadata


def _portfolio_summary_for_frames(
    frames: dict[str, pd.DataFrame],
    *,
    request: ReplayRunRequest,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for subject_name, frame in frames.items():
        subject_type = "controller" if subject_name in {LIVE_PRIMARY_CONTROLLER, LIVE_FALLBACK_CONTROLLER} else "family"
        scope = PORTFOLIO_SCOPE_ROUTED if subject_type == "controller" else PORTFOLIO_SCOPE_SINGLE_FAMILY
        summary, _ = simulate_trade_portfolio(
            frame,
            sample_name="replay_subject",
            strategy_family=subject_name,
            portfolio_scope=scope,
            strategy_family_members=(subject_name,),
            initial_bankroll=request.portfolio_initial_bankroll,
            position_size_fraction=request.portfolio_position_size_fraction,
            game_limit=request.portfolio_game_limit,
            min_order_dollars=request.portfolio_min_order_dollars,
            min_shares=request.portfolio_min_shares,
            max_concurrent_positions=request.portfolio_max_concurrent_positions,
            concurrency_mode=request.portfolio_concurrency_mode,
            sizing_mode=request.portfolio_sizing_mode,
            target_exposure_fraction=request.portfolio_target_exposure_fraction,
            random_slippage_max_cents=0,
            random_slippage_seed=request.portfolio_random_slippage_seed,
        )
        rows.append(
            {
                "subject_name": subject_name,
                "subject_type": subject_type,
                "ending_bankroll": summary.get("ending_bankroll"),
                "compounded_return": summary.get("compounded_return"),
                "max_drawdown_pct": summary.get("max_drawdown_pct"),
                "max_drawdown_amount": summary.get("max_drawdown_amount"),
                "executed_trade_count": summary.get("executed_trade_count"),
            }
        )
    return pd.DataFrame(rows, columns=[column for column in REPLAY_PORTFOLIO_COLUMNS if column != "mode"])


def _load_live_run_summary(*, run_ids: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    live_root = Path(r"C:\code-personal\janus-local\janus_cortex\tracks\live-controller")
    if not live_root.exists():
        return pd.DataFrame(columns=["run_id", "subject_name", "game_id", "live_trade_count", "entry_submitted_count", "position_opened_count"])
    for run_id in run_ids:
        matches = list(live_root.glob(f"*\\{run_id}"))
        if not matches:
            continue
        run_root = matches[0]
        try:
            run_config = json.loads((run_root / "run_config.json").read_text(encoding="utf-8"))
        except Exception:
            run_config = {}
        controller_name = str(run_config.get("controller_name") or LIVE_PRIMARY_CONTROLLER)
        game_ids = [str(value) for value in run_config.get("game_ids") or []]
        submitted = 0
        opened = 0
        if (run_root / "executor_events.jsonl").exists():
            for line in (run_root / "executor_events.jsonl").read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                title = str(payload.get("title") or "")
                if title == "Entry submitted":
                    submitted += 1
                if title == "Position opened":
                    opened += 1
        for game_id in game_ids:
            rows.append(
                {
                    "run_id": run_id,
                    "subject_name": controller_name,
                    "game_id": game_id,
                    "live_trade_count": 0,
                    "entry_submitted_count": submitted,
                    "position_opened_count": opened,
                }
            )
    return pd.DataFrame(rows)


def _subject_summary_frame(
    *,
    standard_trade_frames: dict[str, pd.DataFrame],
    replay_trade_frames: dict[str, pd.DataFrame],
    signal_summary_df: pd.DataFrame,
    standard_portfolio_df: pd.DataFrame,
    replay_portfolio_df: pd.DataFrame,
    live_summary_df: pd.DataFrame,
    games_replayed: int,
) -> pd.DataFrame:
    live_lookup = (
        live_summary_df.groupby("subject_name", dropna=False)["live_trade_count"].sum().to_dict()
        if not live_summary_df.empty
        else {}
    )
    no_trade_lookup = {}
    stale_lookup = {}
    cadence_lookup = {}
    if not signal_summary_df.empty:
        no_trade_lookup = (
            signal_summary_df[signal_summary_df["executed_flag"] == False]
            .groupby(["subject_name", "no_trade_reason"], dropna=False)
            .size()
            .reset_index(name="signal_count")
            .sort_values(["subject_name", "signal_count"], ascending=[True, False], kind="mergesort")
            .drop_duplicates(subset=["subject_name"])
            .set_index("subject_name")
            .to_dict(orient="index")
        )
        stale_lookup = (
            signal_summary_df[signal_summary_df["no_trade_reason"].astype(str) == "signal_stale"]
            .groupby("subject_name", dropna=False)
            .size()
            .to_dict()
        )
        cadence_lookup = (
            signal_summary_df[signal_summary_df["cadence_blocker_flag"].fillna(False).astype(bool)]
            .groupby("subject_name", dropna=False)
            .size()
            .to_dict()
        )
    standard_portfolio_lookup = standard_portfolio_df.set_index("subject_name").to_dict(orient="index") if not standard_portfolio_df.empty else {}
    replay_portfolio_lookup = replay_portfolio_df.set_index("subject_name").to_dict(orient="index") if not replay_portfolio_df.empty else {}
    rows: list[dict[str, Any]] = []
    for subject_name, standard_frame in standard_trade_frames.items():
        replay_frame = replay_trade_frames.get(subject_name, pd.DataFrame())
        subject_type = "controller" if subject_name in {LIVE_PRIMARY_CONTROLLER, LIVE_FALLBACK_CONTROLLER} else "family"
        no_trade = no_trade_lookup.get(subject_name) or {}
        standard_trade_count = int(len(standard_frame))
        replay_trade_count = int(len(replay_frame))
        stale_signal_count = int(stale_lookup.get(subject_name) or 0)
        cadence_blocked_count = int(cadence_lookup.get(subject_name) or 0)
        standard_portfolio = standard_portfolio_lookup.get(subject_name) or {}
        replay_portfolio = replay_portfolio_lookup.get(subject_name) or {}
        replay_compounded_return = replay_portfolio.get("compounded_return")
        replay_max_drawdown_pct = replay_portfolio.get("max_drawdown_pct")
        rows.append(
            {
                "subject_name": subject_name,
                "subject_type": subject_type,
                "standard_trade_count": standard_trade_count,
                "replay_trade_count": replay_trade_count,
                "trade_gap": int(replay_trade_count - standard_trade_count),
                "standard_traded_game_count": int(standard_frame["game_id"].astype(str).nunique()) if not standard_frame.empty else 0,
                "replay_traded_game_count": int(replay_frame["game_id"].astype(str).nunique()) if not replay_frame.empty else 0,
                "standard_trades_per_game": float(standard_trade_count / games_replayed) if games_replayed else None,
                "replay_trades_per_game": float(replay_trade_count / games_replayed) if games_replayed else None,
                "execution_rate": float(replay_trade_count / standard_trade_count) if standard_trade_count else None,
                "replay_survival_rate": float(replay_trade_count / standard_trade_count) if standard_trade_count else None,
                "standard_avg_return_with_slippage": float(standard_frame["gross_return_with_slippage"].mean()) if not standard_frame.empty else None,
                "replay_avg_return_with_slippage": float(replay_frame["gross_return_with_slippage"].mean()) if not replay_frame.empty else None,
                "replay_no_trade_count": int(
                    signal_summary_df[
                        (signal_summary_df["subject_name"].astype(str) == subject_name)
                        & (signal_summary_df["executed_flag"] == False)
                    ].shape[0]
                )
                if not signal_summary_df.empty
                else 0,
                "stale_signal_count": stale_signal_count,
                "stale_signal_rate": float(stale_signal_count / standard_trade_count) if standard_trade_count else None,
                "cadence_blocked_count": cadence_blocked_count,
                "cadence_blocked_rate": (
                    float(cadence_blocked_count / standard_trade_count) if standard_trade_count else None
                ),
                "top_no_trade_reason": no_trade.get("no_trade_reason"),
                "standard_ending_bankroll": standard_portfolio.get("ending_bankroll"),
                "standard_compounded_return": standard_portfolio.get("compounded_return"),
                "standard_max_drawdown_pct": standard_portfolio.get("max_drawdown_pct"),
                "standard_max_drawdown_amount": standard_portfolio.get("max_drawdown_amount"),
                "replay_ending_bankroll": replay_portfolio.get("ending_bankroll"),
                "replay_compounded_return": replay_compounded_return,
                "replay_max_drawdown_pct": replay_max_drawdown_pct,
                "replay_max_drawdown_amount": replay_portfolio.get("max_drawdown_amount"),
                "replay_path_quality_score": (
                    float((replay_compounded_return or 0.0) - (replay_max_drawdown_pct or 0.0))
                    if replay_compounded_return is not None or replay_max_drawdown_pct is not None
                    else None
                ),
                "live_trade_count": int(live_lookup.get(subject_name) or 0),
                "live_vs_standard_gap_trade_rate": (
                    float((standard_trade_count - int(live_lookup.get(subject_name) or 0)) / standard_trade_count)
                    if standard_trade_count
                    else None
                ),
            }
        )
    return pd.DataFrame(rows, columns=REPLAY_SUBJECT_SUMMARY_COLUMNS)


def _game_gap_frame(
    *,
    standard_trade_frames: dict[str, pd.DataFrame],
    replay_trade_frames: dict[str, pd.DataFrame],
    signal_summary_df: pd.DataFrame,
    manifest_df: pd.DataFrame,
) -> pd.DataFrame:
    manifest_lookup = manifest_df.set_index("game_id").to_dict(orient="index") if not manifest_df.empty else {}
    game_ids = sorted({str(game_id) for game_id in manifest_df["game_id"].tolist()}) if not manifest_df.empty else []
    rows: list[dict[str, Any]] = []
    for subject_name, standard_frame in standard_trade_frames.items():
        replay_frame = replay_trade_frames.get(subject_name, pd.DataFrame())
        subject_type = "controller" if subject_name in {LIVE_PRIMARY_CONTROLLER, LIVE_FALLBACK_CONTROLLER} else "family"
        for game_id in game_ids:
            standard_count = int((standard_frame["game_id"].astype(str) == game_id).sum()) if not standard_frame.empty else 0
            replay_count = int((replay_frame["game_id"].astype(str) == game_id).sum()) if not replay_frame.empty else 0
            signal_slice = signal_summary_df[
                (signal_summary_df["subject_name"].astype(str) == subject_name)
                & (signal_summary_df["game_id"].astype(str) == game_id)
                & (signal_summary_df["executed_flag"] == False)
            ] if not signal_summary_df.empty else pd.DataFrame()
            top_reason = None
            if not signal_slice.empty:
                reason_counts = signal_slice["no_trade_reason"].value_counts(dropna=False)
                top_reason = str(reason_counts.index[0]) if not reason_counts.empty else None
            rows.append(
                {
                    "subject_name": subject_name,
                    "subject_type": subject_type,
                    "game_id": game_id,
                    "state_source": (manifest_lookup.get(game_id) or {}).get("state_source"),
                    "standard_trade_count": standard_count,
                    "replay_trade_count": replay_count,
                    "trade_gap": replay_count - standard_count,
                    "top_no_trade_reason": top_reason,
                }
            )
    return pd.DataFrame(rows, columns=REPLAY_GAME_GAP_COLUMNS)


def _divergence_frame(signal_summary_df: pd.DataFrame) -> pd.DataFrame:
    if signal_summary_df.empty:
        return pd.DataFrame(columns=REPLAY_DIVERGENCE_COLUMNS)
    rows = (
        signal_summary_df[signal_summary_df["executed_flag"] == False]
        .groupby(["subject_name", "subject_type", "no_trade_reason"], dropna=False)
        .size()
        .reset_index(name="signal_count")
        .sort_values(["signal_count", "subject_name"], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )
    return rows[list(REPLAY_DIVERGENCE_COLUMNS)]


def _quarter_summary_frame(
    *,
    standard_trade_frames: dict[str, pd.DataFrame],
    replay_trade_frames: dict[str, pd.DataFrame],
    signal_summary_df: pd.DataFrame,
) -> pd.DataFrame:
    quarter_rows: list[dict[str, Any]] = []
    periods = ("Q1", "Q2", "Q3", "Q4", "OT1", "OT2")
    for subject_name, standard_frame in standard_trade_frames.items():
        replay_frame = replay_trade_frames.get(subject_name, pd.DataFrame())
        subject_type = "controller" if subject_name in {LIVE_PRIMARY_CONTROLLER, LIVE_FALLBACK_CONTROLLER} else "family"
        for period_label in periods:
            standard_slice = (
                standard_frame[standard_frame["period_label"].astype(str) == period_label]
                if not standard_frame.empty and "period_label" in standard_frame.columns
                else pd.DataFrame()
            )
            replay_slice = (
                replay_frame[replay_frame["period_label"].astype(str) == period_label]
                if not replay_frame.empty and "period_label" in replay_frame.columns
                else pd.DataFrame()
            )
            signal_slice = (
                signal_summary_df[
                    (signal_summary_df["subject_name"].astype(str) == subject_name)
                    & (signal_summary_df["period_label"].astype(str) == period_label)
                ]
                if not signal_summary_df.empty
                else pd.DataFrame()
            )
            signal_count = int(len(signal_slice)) if not signal_slice.empty else int(len(standard_slice))
            stale_signal_count = int(
                signal_slice[signal_slice["no_trade_reason"].astype(str) == "signal_stale"].shape[0]
            ) if not signal_slice.empty else 0
            if signal_count == 0 and replay_slice.empty and standard_slice.empty:
                continue
            quarter_rows.append(
                {
                    "subject_name": subject_name,
                    "subject_type": subject_type,
                    "period_label": period_label,
                    "standard_trade_count": int(len(standard_slice)),
                    "replay_trade_count": int(len(replay_slice)),
                    "standard_avg_return_with_slippage": (
                        float(standard_slice["gross_return_with_slippage"].mean()) if not standard_slice.empty else None
                    ),
                    "replay_avg_return_with_slippage": (
                        float(replay_slice["gross_return_with_slippage"].mean()) if not replay_slice.empty else None
                    ),
                    "replay_survival_rate": float(len(replay_slice) / signal_count) if signal_count else None,
                    "stale_signal_count": stale_signal_count,
                    "stale_signal_rate": float(stale_signal_count / signal_count) if signal_count else None,
                }
            )
    return pd.DataFrame(quarter_rows, columns=REPLAY_QUARTER_SUMMARY_COLUMNS)


def _window_summary_frame(signal_summary_df: pd.DataFrame) -> pd.DataFrame:
    if signal_summary_df.empty:
        return pd.DataFrame(columns=REPLAY_WINDOW_SUMMARY_COLUMNS)
    work = signal_summary_df.copy()
    work["fill_delay_seconds"] = pd.to_numeric(work["fill_delay_seconds"], errors="coerce")
    work["signal_window_seconds"] = pd.to_numeric(work["signal_window_seconds"], errors="coerce")
    rows: list[dict[str, Any]] = []
    grouped = work.groupby(["subject_name", "subject_type", "period_label", "entry_window_label"], dropna=False)
    for (subject_name, subject_type, period_label, entry_window_label), group in grouped:
        signal_count = int(len(group))
        replay_trade_count = int(group["executed_flag"].fillna(False).astype(bool).sum())
        stale_signal_count = int((group["no_trade_reason"].astype(str) == "signal_stale").sum())
        cadence_blocked_count = int(group["cadence_blocker_flag"].fillna(False).astype(bool).sum())
        rows.append(
            {
                "subject_name": subject_name,
                "subject_type": subject_type,
                "period_label": period_label,
                "entry_window_label": entry_window_label,
                "signal_count": signal_count,
                "replay_trade_count": replay_trade_count,
                "replay_survival_rate": float(replay_trade_count / signal_count) if signal_count else None,
                "stale_signal_count": stale_signal_count,
                "stale_signal_rate": float(stale_signal_count / signal_count) if signal_count else None,
                "cadence_blocked_count": cadence_blocked_count,
                "cadence_blocked_rate": float(cadence_blocked_count / signal_count) if signal_count else None,
                "avg_fill_delay_seconds": float(group["fill_delay_seconds"].mean()) if group["fill_delay_seconds"].notna().any() else None,
                "avg_signal_window_seconds": (
                    float(group["signal_window_seconds"].mean()) if group["signal_window_seconds"].notna().any() else None
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            str(row.get("subject_name") or ""),
            str(row.get("period_label") or ""),
            str(row.get("entry_window_label") or ""),
        )
    )
    return pd.DataFrame(rows, columns=REPLAY_WINDOW_SUMMARY_COLUMNS)


def _candidate_lifecycle_frame(signal_summary_df: pd.DataFrame) -> pd.DataFrame:
    if signal_summary_df.empty:
        return pd.DataFrame(columns=REPLAY_CANDIDATE_LIFECYCLE_COLUMNS)
    rows: list[dict[str, Any]] = []
    for record in signal_summary_df.to_dict(orient="records"):
        birth_at = _parse_datetime(record.get("signal_entry_at"))
        death_at = (
            _parse_datetime(record.get("entry_fill_at"))
            if bool(record.get("executed_flag"))
            else _parse_datetime(record.get("last_attempt_at")) or _parse_datetime(record.get("signal_exit_at"))
        )
        birth_to_death_seconds = (
            max(0.0, (death_at - birth_at).total_seconds())
            if birth_at is not None and death_at is not None
            else None
        )
        rows.append(
            {
                "subject_name": record.get("subject_name"),
                "subject_type": record.get("subject_type"),
                "game_id": record.get("game_id"),
                "signal_id": record.get("signal_id"),
                "strategy_family": record.get("strategy_family"),
                "period_label": record.get("period_label"),
                "entry_window_label": record.get("entry_window_label"),
                "lifecycle_status": "executed" if bool(record.get("executed_flag")) else "dead",
                "birth_at": birth_at,
                "death_at": death_at,
                "birth_to_death_seconds": birth_to_death_seconds,
                "first_visible_at": record.get("first_visible_at"),
                "first_executable_event_at": record.get("first_executable_event_at"),
                "first_executable_poll_at": record.get("first_executable_poll_at"),
                "stale_at": record.get("stale_at"),
                "time_to_first_executable_event_seconds": record.get("time_to_first_executable_event_seconds"),
                "time_to_first_executable_poll_seconds": record.get("time_to_first_executable_poll_seconds"),
                "time_to_stale_seconds": record.get("time_to_stale_seconds"),
                "cadence_gap_seconds": record.get("cadence_gap_seconds"),
                "event_level_opportunity_flag": record.get("event_level_opportunity_flag"),
                "poll_level_opportunity_flag": record.get("poll_level_opportunity_flag"),
                "cadence_blocker_flag": record.get("cadence_blocker_flag"),
                "stale_before_first_executable_flag": record.get("stale_before_first_executable_flag"),
                "replay_blocker_class": record.get("replay_blocker_class"),
                "replay_blocker_detail": record.get("replay_blocker_detail"),
                "cadence_vs_stale_blocker": record.get("cadence_vs_stale_blocker"),
                "signal_window_seconds": record.get("signal_window_seconds"),
                "attempt_count": record.get("attempt_count"),
                "dominant_retry_reason": record.get("dominant_retry_reason"),
                "terminal_no_trade_reason": record.get("terminal_no_trade_reason"),
                "final_no_trade_reason": record.get("no_trade_reason"),
                "max_signal_age_seconds": record.get("max_signal_age_seconds"),
                "max_quote_age_seconds": record.get("max_quote_age_seconds"),
                "state_source": record.get("state_source"),
            }
        )
    return pd.DataFrame(rows, columns=REPLAY_CANDIDATE_LIFECYCLE_COLUMNS)


def _blocker_summary_frame(signal_summary_df: pd.DataFrame) -> pd.DataFrame:
    if signal_summary_df.empty:
        return pd.DataFrame(columns=REPLAY_BLOCKER_SUMMARY_COLUMNS)
    work = signal_summary_df[signal_summary_df["replay_blocker_class"].astype(str) != "executed"].copy()
    if work.empty:
        return pd.DataFrame(columns=REPLAY_BLOCKER_SUMMARY_COLUMNS)
    rows = (
        work.groupby(
            [
                "subject_name",
                "subject_type",
                "period_label",
                "entry_window_label",
                "replay_blocker_class",
                "replay_blocker_detail",
                "cadence_vs_stale_blocker",
            ],
            dropna=False,
        )
        .size()
        .reset_index(name="signal_count")
        .sort_values(
            [
                "subject_name",
                "signal_count",
                "period_label",
                "entry_window_label",
                "replay_blocker_class",
            ],
            ascending=[True, False, True, True, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    return rows[list(REPLAY_BLOCKER_SUMMARY_COLUMNS)]


def _render_replay_markdown(payload: dict[str, Any]) -> str:
    benchmark = payload.get("benchmark") or {}
    subject_rows = benchmark.get("subject_summary") or []
    divergence_rows = benchmark.get("divergence_summary") or []
    live_rows = benchmark.get("live_summary") or []
    quote_coverage_rows = benchmark.get("quote_coverage_summary") or []
    lines = [
        "# Postseason Replay Comparison",
        "",
        f"- Season: `{payload.get('season')}`",
        f"- Analysis version: `{payload.get('analysis_version')}`",
        f"- Finished games replayed: `{payload.get('finished_game_count')}`",
        f"- Poll cadence: `{payload.get('replay_contract', {}).get('poll_interval_seconds')}` seconds",
        f"- Signal freshness window: `{payload.get('replay_contract', {}).get('signal_max_age_seconds')}` seconds",
        f"- Quote freshness window: `{payload.get('replay_contract', {}).get('quote_max_age_seconds')}` seconds",
        f"- Spread gate: `{payload.get('replay_contract', {}).get('max_spread_cents')}` cents",
        f"- Quote source mode: `{payload.get('replay_contract', {}).get('quote_source_mode')}`",
        f"- Quote source fallback: `{payload.get('replay_contract', {}).get('quote_source_fallback_mode')}`",
        f"- Quote proxy: `{payload.get('replay_contract', {}).get('quote_proxy')}`",
        "",
        "## Subject Summary",
        "",
    ]
    if not subject_rows:
        lines.append("- No subject rows were produced.")
    else:
        for row in subject_rows:
            lines.append(
                f"- `{row.get('subject_name')}`"
                f" standard `{row.get('standard_trade_count')}`"
                f" -> replay `{row.get('replay_trade_count')}`"
                f" | gap `{row.get('trade_gap')}`"
                f" | survival `{row.get('replay_survival_rate')}`"
                f" | stale rate `{row.get('stale_signal_rate')}`"
                f" | replay bankroll `{row.get('replay_ending_bankroll')}`"
            )
    lines.extend(["", "## Quote Coverage", ""])
    if not quote_coverage_rows:
        lines.append("- No quote coverage rows were produced.")
    else:
        direct_rows = sum(int(row.get("direct_bidask_quote_count") or 0) for row in quote_coverage_rows)
        synthetic_rows = sum(int(row.get("synthetic_quote_count") or 0) for row in quote_coverage_rows)
        avg_coverage = (
            sum(float(row.get("coverage_ratio") or 0.0) for row in quote_coverage_rows) / len(quote_coverage_rows)
            if quote_coverage_rows
            else 0.0
        )
        lines.append(
            f"- Direct bid/ask rows `{direct_rows}` | synthetic rows `{synthetic_rows}` | average coverage `{avg_coverage:.4f}`"
        )
    lines.extend(["", "## Main Divergence Causes", ""])
    if not divergence_rows:
        lines.append("- No divergence rows were produced.")
    else:
        for row in divergence_rows[:20]:
            lines.append(
                f"- `{row.get('subject_name')}` -> `{row.get('no_trade_reason')}` on `{row.get('signal_count')}` signals"
            )
    lines.extend(["", "## Live Comparison", ""])
    if not live_rows:
        lines.append("- No live-run comparison rows were available.")
    else:
        for row in live_rows:
            lines.append(
                f"- run `{row.get('run_id')}` subject `{row.get('subject_name')}` game `{row.get('game_id')}` live trades `{row.get('live_trade_count')}`"
            )
    return "\n".join(lines)


def write_replay_artifacts(result: ReplayRunResult, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = result.payload
    payload["artifacts"] = {}

    for subject_name, frame in result.standard_trade_frames.items():
        stem = subject_name.replace(" ", "_").replace("::", "__").replace("/", "_")
        payload["artifacts"].update(
            {f"standard_{stem}_{key}": value for key, value in write_frame(output_dir / f"standard_{stem}", frame).items()}
        )
    for subject_name, frame in result.replay_trade_frames.items():
        stem = subject_name.replace(" ", "_").replace("::", "__").replace("/", "_")
        payload["artifacts"].update(
            {f"replay_{stem}_{key}": value for key, value in write_frame(output_dir / f"replay_{stem}", frame).items()}
        )
    artifact_stems = {
        "game_manifest": "replay_game_manifest",
        "subject_summary": "replay_subject_summary",
        "game_gap": "replay_game_gap",
        "divergence_summary": "replay_divergence_summary",
        "signal_summary": "replay_signal_summary",
        "attempt_trace": "replay_attempt_trace",
        "portfolio_summary": "replay_portfolio_summary",
        "quarter_summary": "replay_quarter_summary",
        "window_summary": "replay_window_summary",
        "candidate_lifecycle": "replay_candidate_lifecycle",
        "blocker_summary": "replay_blocker_summary",
        "historical_bidask_l1": "historical_bidask_l1",
        "quote_coverage_summary": "quote_coverage_summary",
        "live_summary": "replay_live_summary",
    }
    for frame_name, frame in result.benchmark_frames.items():
        stem = artifact_stems.get(frame_name)
        if not stem:
            continue
        payload["artifacts"].update(
            {f"{frame_name}_{key}": value for key, value in write_frame(output_dir / stem, frame).items()}
        )
    payload["artifacts"]["json"] = write_json(output_dir / "replay_run.json", payload)
    payload["artifacts"]["markdown"] = write_markdown(output_dir / "replay_run.md", _render_replay_markdown(payload))
    return to_jsonable(payload)


def run_postseason_execution_replay(
    *,
    request: ReplayRunRequest,
    output_dir: Path,
) -> ReplayRunResult:
    contexts, combined_state_df, manifest_df = load_finished_postseason_replay_contexts(
        season=request.season,
        analysis_version=request.analysis_version,
    )
    historical_bidask_by_game, historical_bidask_df, quote_coverage_df = build_historical_bidask_samples(
        contexts=contexts,
        season=request.season,
        request=request,
    )
    coverage_lookup = quote_coverage_df.set_index("game_id").to_dict(orient="index") if not quote_coverage_df.empty else {}
    for game_id, context in contexts.items():
        context.historical_bidask_df = historical_bidask_by_game.get(game_id, pd.DataFrame(columns=REPLAY_HISTORICAL_BIDASK_COLUMNS))
        context.quote_coverage = coverage_lookup.get(game_id)
    subjects, standard_trade_frames, standard_decision_frames, controller_meta = _build_subjects(
        combined_state_df=combined_state_df,
        request=request,
        output_dir=output_dir,
    )
    replay_trade_frames, signal_summary_df, attempt_trace_df = simulate_replay_trade_frames(
        subjects,
        contexts=contexts,
        request=request,
    )
    standard_portfolio_df = _portfolio_summary_for_frames(standard_trade_frames, request=request)
    standard_portfolio_df["mode"] = "standard"
    replay_portfolio_df = _portfolio_summary_for_frames(replay_trade_frames, request=request)
    replay_portfolio_df["mode"] = "replay"
    live_summary_df = _load_live_run_summary(run_ids=tuple(request.include_live_run_ids or ()))
    subject_summary_df = _subject_summary_frame(
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        signal_summary_df=signal_summary_df,
        standard_portfolio_df=standard_portfolio_df,
        replay_portfolio_df=replay_portfolio_df,
        live_summary_df=live_summary_df,
        games_replayed=len(contexts),
    )
    game_gap_df = _game_gap_frame(
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        signal_summary_df=signal_summary_df,
        manifest_df=manifest_df,
    )
    divergence_df = _divergence_frame(signal_summary_df)
    quarter_summary_df = _quarter_summary_frame(
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        signal_summary_df=signal_summary_df,
    )
    window_summary_df = _window_summary_frame(signal_summary_df)
    candidate_lifecycle_df = _candidate_lifecycle_frame(signal_summary_df)
    blocker_summary_df = _blocker_summary_frame(signal_summary_df)
    portfolio_summary_df = pd.concat([standard_portfolio_df, replay_portfolio_df], ignore_index=True, sort=False)

    payload = {
        "season": request.season,
        "season_phase": "postseason_to_date",
        "analysis_version": request.analysis_version,
        "finished_game_count": int(len(contexts)),
        "state_panel_game_count": int((manifest_df["state_source"] == "state_panel").sum()) if not manifest_df.empty else 0,
        "derived_bundle_game_count": int((manifest_df["state_source"] == "derived_bundle").sum()) if not manifest_df.empty else 0,
        "replay_contract": {
            "poll_interval_seconds": request.poll_interval_seconds,
            "signal_max_age_seconds": request.signal_max_age_seconds,
            "quote_max_age_seconds": request.quote_max_age_seconds,
            "max_spread_cents": request.max_spread_cents,
            "proxy_min_spread_cents": request.proxy_min_spread_cents,
            "proxy_max_spread_cents": request.proxy_max_spread_cents,
            "quote_source_mode": request.quote_source_mode,
            "quote_source_fallback_mode": request.quote_source_fallback_mode,
            "quote_proxy": request.quote_proxy,
        },
        "controller_meta": controller_meta,
        "benchmark": {
            "game_manifest": to_jsonable(manifest_df.to_dict(orient="records")),
            "subject_summary": to_jsonable(subject_summary_df.to_dict(orient="records")),
            "game_gap": to_jsonable(game_gap_df.to_dict(orient="records")),
            "divergence_summary": to_jsonable(divergence_df.to_dict(orient="records")),
            "signal_summary": to_jsonable(signal_summary_df.to_dict(orient="records")),
            "quarter_summary": to_jsonable(quarter_summary_df.to_dict(orient="records")),
            "window_summary": to_jsonable(window_summary_df.to_dict(orient="records")),
            "candidate_lifecycle": to_jsonable(candidate_lifecycle_df.to_dict(orient="records")),
            "blocker_summary": to_jsonable(blocker_summary_df.to_dict(orient="records")),
            "historical_bidask_l1": to_jsonable(historical_bidask_df.to_dict(orient="records")),
            "quote_coverage_summary": to_jsonable(quote_coverage_df.to_dict(orient="records")),
            "portfolio_summary": to_jsonable(portfolio_summary_df.to_dict(orient="records")),
            "live_summary": to_jsonable(live_summary_df.to_dict(orient="records")),
            "standard_controller_decisions": {
                subject: to_jsonable(frame.to_dict(orient="records"))
                for subject, frame in standard_decision_frames.items()
            },
        },
    }
    return ReplayRunResult(
        payload=payload,
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        benchmark_frames={
            "game_manifest": manifest_df,
            "subject_summary": subject_summary_df,
            "game_gap": game_gap_df,
            "divergence_summary": divergence_df,
            "signal_summary": signal_summary_df,
            "attempt_trace": attempt_trace_df,
            "portfolio_summary": portfolio_summary_df[list(REPLAY_PORTFOLIO_COLUMNS)],
            "quarter_summary": quarter_summary_df,
            "window_summary": window_summary_df,
            "candidate_lifecycle": candidate_lifecycle_df,
            "blocker_summary": blocker_summary_df,
            "historical_bidask_l1": historical_bidask_df,
            "quote_coverage_summary": quote_coverage_df,
            "live_summary": live_summary_df,
        },
    )


__all__ = [
    "REPLAY_ATTEMPT_TRACE_COLUMNS",
    "REPLAY_BLOCKER_SUMMARY_COLUMNS",
    "REPLAY_DIVERGENCE_COLUMNS",
    "REPLAY_GAME_GAP_COLUMNS",
    "REPLAY_CANDIDATE_LIFECYCLE_COLUMNS",
    "REPLAY_HISTORICAL_BIDASK_COLUMNS",
    "REPLAY_PORTFOLIO_COLUMNS",
    "REPLAY_QUOTE_COVERAGE_COLUMNS",
    "REPLAY_QUARTER_SUMMARY_COLUMNS",
    "REPLAY_SIGNAL_SUMMARY_COLUMNS",
    "REPLAY_SUBJECT_SUMMARY_COLUMNS",
    "REPLAY_WINDOW_SUMMARY_COLUMNS",
    "ReplayGameContext",
    "build_controller_context",
    "load_finished_postseason_replay_contexts",
    "run_postseason_execution_replay",
    "write_replay_artifacts",
]
