from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.data.databases.config import REPO_ROOT
from app.data.pipelines.daily.nba.analysis.artifacts import ensure_output_dir, write_json
from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS, BACKTEST_TRACE_STATE_COLUMNS
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE,
    MASTER_ROUTER_PORTFOLIO,
    build_master_router_selection_priors,
    build_master_router_trade_frame,
    score_master_router_candidate,
)
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    PORTFOLIO_SCOPE_ROUTED,
    PORTFOLIO_SCOPE_SINGLE_FAMILY,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult, StrategyDefinition
from app.data.pipelines.daily.nba.analysis.contracts import BacktestRunRequest


LLM_EXPERIMENT_ITERATION_COLUMNS = (
    "iteration_index",
    "iteration_seed",
    "sample_name",
    "sample_game_count",
    "sampled_game_ids_json",
)

LLM_EXPERIMENT_SUMMARY_COLUMNS = (
    "iteration_index",
    "iteration_seed",
    "sample_name",
    "lane_name",
    "lane_mode",
    "llm_component_scope",
    "lane_group",
    "prompt_profile",
    "reasoning_effort",
    "include_rationale",
    "use_confidence_gate",
    "starting_bankroll",
    "ending_bankroll",
    "compounded_return",
    "max_drawdown_pct",
    "executed_trade_count",
    "avg_executed_trade_return_with_slippage",
    "llm_call_count",
    "llm_cache_hit_count",
    "llm_input_tokens",
    "llm_cached_input_tokens",
    "llm_output_tokens",
    "llm_reasoning_tokens",
    "llm_estimated_cost_usd",
)

LLM_EXPERIMENT_LANE_SUMMARY_COLUMNS = (
    "lane_name",
    "lane_mode",
    "llm_component_scope",
    "lane_group",
    "prompt_profile",
    "reasoning_effort",
    "include_rationale",
    "use_confidence_gate",
    "iteration_count",
    "positive_iteration_count",
    "positive_iteration_rate",
    "mean_ending_bankroll",
    "median_ending_bankroll",
    "mean_compounded_return",
    "mean_max_drawdown_pct",
    "mean_executed_trade_count",
    "mean_llm_estimated_cost_usd",
    "total_llm_estimated_cost_usd",
    "total_llm_call_count",
    "total_llm_cache_hit_count",
)

LLM_EXPERIMENT_DECISION_COLUMNS = (
    "iteration_index",
    "iteration_seed",
    "sample_name",
    "lane_name",
    "lane_mode",
    "lane_group",
    "prompt_profile",
    "reasoning_effort",
    "include_rationale",
    "use_confidence_gate",
    "decision_stage",
    "game_id",
    "game_date",
    "opening_band",
    "available_candidate_count",
    "available_candidate_ids_json",
    "selected_candidate_count",
    "selected_candidate_ids_json",
    "selected_strategy_families_json",
    "decision_status",
    "cache_hit_flag",
    "llm_confidence",
    "rationale",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "error_text",
)

LLM_EXPERIMENT_SHOWDOWN_SUMMARY_COLUMNS = (
    "sample_name",
    "lane_name",
    "lane_mode",
    "lane_group",
    "prompt_profile",
    "reasoning_effort",
    "include_rationale",
    "use_confidence_gate",
    "starting_bankroll",
    "first_entry_at",
    "ending_bankroll",
    "compounded_return",
    "max_drawdown_pct",
    "executed_trade_count",
    "avg_executed_trade_return_with_slippage",
    "llm_call_count",
    "llm_cache_hit_count",
    "llm_input_tokens",
    "llm_cached_input_tokens",
    "llm_output_tokens",
    "llm_reasoning_tokens",
    "llm_estimated_cost_usd",
    "finalist_score",
)

LLM_EXPERIMENT_SHOWDOWN_DAILY_PATH_COLUMNS = (
    "sample_name",
    "lane_name",
    "lane_mode",
    "lane_group",
    "path_day_index",
    "path_date",
    "settled_trade_count_to_date",
    "ending_bankroll",
    "daily_pnl_amount",
    "compounded_return",
)

_LLM_MODEL_PRICING_PER_1M: dict[str, tuple[float, float, float]] = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
}
_LLM_MODEL_INPUT_PRICE_PER_1M = _LLM_MODEL_PRICING_PER_1M["gpt-5.4-mini"][0]
_LLM_MODEL_CACHED_INPUT_PRICE_PER_1M = _LLM_MODEL_PRICING_PER_1M["gpt-5.4-mini"][1]
_LLM_MODEL_OUTPUT_PRICE_PER_1M = _LLM_MODEL_PRICING_PER_1M["gpt-5.4-mini"][2]
_LLM_CACHE_FILENAME = "llm_router_cache.json"
_LLM_PROMPT_VERSION = "v2"
_LLM_TRACE_ROW_LIMIT = 4
_LLM_FALLBACK_CONFIDENCE = 0.51
_LLM_FINALIST_COUNT = 6
_LLM_SHOWDOWN_SAMPLE_NAME = "llm_showdown_fixed"
_FINAL_OPTION_SHOWDOWN_SAMPLE_NAME = "final_option_showdown"
_FINAL_OPTION_SHOWDOWN_LLM_LANE_NAME = "llm_hybrid_freedom_compact_v1"
_LLM_BASELINES = (
    "winner_definition",
    "inversion",
    "underdog_liftoff",
    "q1_repricing",
    "q4_clutch",
)
_LLM_LANES = (
    {
        "lane_name": "llm_hybrid_restrained_v1",
        "lane_group": "llm_variant",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "full",
        "reasoning_effort": "low",
        "include_rationale": True,
        "use_confidence_gate": False,
    },
    {
        "lane_name": "llm_hybrid_compact_v1",
        "lane_group": "llm_variant",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "low",
        "include_rationale": True,
        "use_confidence_gate": False,
    },
    {
        "lane_name": "llm_hybrid_compact_guarded_v1",
        "lane_group": "llm_variant",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "low",
        "include_rationale": True,
        "use_confidence_gate": True,
        "gate_min_top_confidence": 0.72,
        "gate_min_gap": 0.08,
    },
    {
        "lane_name": "llm_hybrid_compact_no_rationale_v1",
        "lane_group": "llm_variant",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": False,
    },
    {
        "lane_name": "llm_hybrid_compact_medium_v1",
        "lane_group": "llm_variant",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "medium",
        "include_rationale": True,
        "use_confidence_gate": False,
    },
    {
        "lane_name": "llm_hybrid_freedom_compact_v1",
        "lane_group": "llm_variant",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact",
        "reasoning_effort": "low",
        "include_rationale": True,
        "use_confidence_gate": False,
    },
)

_LLM_FULL_MODEL_TUNED_LANES = (
    {
        "lane_name": "llm_hybrid_full_anchor_none_v1",
        "lane_group": "llm_full_tuned",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor",
        "reasoning_effort": "none",
        "include_rationale": False,
        "use_confidence_gate": False,
    },
    {
        "lane_name": "llm_hybrid_full_anchor_low_v1",
        "lane_group": "llm_full_tuned",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor",
        "reasoning_effort": "low",
        "include_rationale": True,
        "use_confidence_gate": False,
    },
    {
        "lane_name": "llm_hybrid_full_anchor_guarded_low_v1",
        "lane_group": "llm_full_tuned",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": True,
        "gate_min_top_confidence": 0.66,
        "gate_min_gap": 0.05,
    },
    {
        "lane_name": "llm_hybrid_full_anchor_examples_low_v1",
        "lane_group": "llm_full_tuned",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
        "prompt_profile": "compact_anchor_examples",
        "reasoning_effort": "low",
        "include_rationale": False,
        "use_confidence_gate": False,
    },
)


class _LLMSelectionResponse(BaseModel):
    selected_candidate_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=120)


class _LLMSelectionNoRationaleResponse(BaseModel):
    selected_candidate_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass(slots=True)
class _LLMCacheStore:
    path: Path
    payload: dict[str, Any]


@dataclass(slots=True)
class _LLMBudgetState:
    spent_usd: float = 0.0


@dataclass(slots=True)
class _LLMCallResult:
    selected_candidate_ids: list[str]
    confidence: float
    rationale: str
    cache_hit: bool
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    estimated_cost_usd: float
    decision_status: str
    error_text: str | None = None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialise_scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _clean_trades_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    work = trades_df.copy()
    for column in ("entry_at", "exit_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column], errors="coerce", utc=True)
    for column in (
        "signal_strength",
        "gross_return_with_slippage",
        "entry_price",
        "exit_price",
        "entry_state_index",
        "hold_time_seconds",
    ):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    return work


def estimate_llm_usage_cost(
    *,
    model: str = "gpt-5.4-mini",
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    normalized_model = str(model or "").strip().lower()
    input_price, cached_input_price, output_price = _LLM_MODEL_PRICING_PER_1M.get(
        normalized_model,
        _LLM_MODEL_PRICING_PER_1M["gpt-5.4-mini"],
    )
    uncached_input_tokens = max(0, int(input_tokens) - int(cached_input_tokens))
    return (
        (uncached_input_tokens / 1_000_000.0) * input_price
        + (max(0, int(cached_input_tokens)) / 1_000_000.0) * cached_input_price
        + (max(0, int(output_tokens)) / 1_000_000.0) * output_price
    )


def _normalize_iteration_seeds(
    seeds: tuple[int, ...] | list[int] | None,
    *,
    iteration_count: int,
    fallback_seed: int,
) -> tuple[int, ...]:
    normalized: list[int] = []
    seen: set[int] = set()
    for seed in tuple(seeds or ()):
        resolved = int(seed)
        if resolved in seen:
            continue
        normalized.append(resolved)
        seen.add(resolved)
    next_seed = int(fallback_seed)
    while len(normalized) < max(1, int(iteration_count)):
        if next_seed not in seen:
            normalized.append(next_seed)
            seen.add(next_seed)
        next_seed += 997
    return tuple(normalized[: max(1, int(iteration_count))])


def build_llm_iteration_plan(
    evaluation_result: BacktestResult,
    *,
    strategy_families: tuple[str, ...] | list[str],
    iteration_count: int,
    games_per_iteration: int,
    seeds: tuple[int, ...] | list[int] | None,
    fallback_seed: int,
) -> list[dict[str, Any]]:
    state_df = evaluation_result.state_df.copy()
    if state_df.empty or "game_id" not in state_df.columns:
        return []

    candidate_game_ids: set[str] = set()
    for family in strategy_families:
        trades_df = _clean_trades_df(evaluation_result.trade_frames.get(str(family), pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)))
        if trades_df.empty:
            continue
        candidate_game_ids.update(str(game_id) for game_id in trades_df["game_id"].dropna().astype(str).tolist())

    games_df = (
        state_df[["game_id", "game_date"]]
        .drop_duplicates(subset=["game_id"])
        .assign(game_id=lambda frame: frame["game_id"].astype(str))
    )
    if candidate_game_ids:
        games_df = games_df[games_df["game_id"].isin(candidate_game_ids)]
    games_df = games_df.sort_values(["game_date", "game_id"], kind="mergesort", na_position="last").reset_index(drop=True)
    if games_df.empty:
        return []

    resolved_game_count = max(1, min(int(games_per_iteration), len(games_df)))
    resolved_seeds = _normalize_iteration_seeds(seeds, iteration_count=iteration_count, fallback_seed=fallback_seed)
    plan: list[dict[str, Any]] = []
    for iteration_index, iteration_seed in enumerate(resolved_seeds, start=1):
        rng = np.random.default_rng(int(iteration_seed))
        chosen_indices = np.sort(rng.choice(len(games_df), size=resolved_game_count, replace=False))
        sampled = games_df.iloc[chosen_indices].copy().sort_values(["game_date", "game_id"], kind="mergesort", na_position="last")
        sampled_game_ids = tuple(str(game_id) for game_id in sampled["game_id"].tolist())
        plan.append(
            {
                "iteration_index": iteration_index,
                "iteration_seed": int(iteration_seed),
                "sample_name": f"llm_random_holdout_iter_{iteration_index:02d}",
                "sample_game_count": len(sampled_game_ids),
                "sampled_game_ids": sampled_game_ids,
            }
        )
    return plan


def normalize_llm_selected_candidate_ids(
    selected_candidate_ids: list[str] | tuple[str, ...],
    available_candidates: list[dict[str, Any]],
    *,
    lane_mode: str,
    allowed_roles: tuple[str, ...] | list[str],
    max_selected_candidates: int | None = None,
    max_core_candidates: int | None = None,
    max_extra_candidates: int | None = None,
    require_core_for_extra: bool = False,
) -> list[str]:
    allowed_role_set = {str(role) for role in allowed_roles}
    available_lookup = {str(candidate["candidate_id"]): candidate for candidate in available_candidates}
    ordered: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_candidate_id in selected_candidate_ids:
        candidate_id = str(raw_candidate_id)
        if candidate_id in seen_ids or candidate_id not in available_lookup:
            continue
        candidate = available_lookup[candidate_id]
        if str(candidate.get("candidate_role")) not in allowed_role_set:
            continue
        ordered.append(candidate)
        seen_ids.add(candidate_id)

    if not ordered:
        return []

    selected_side = str(ordered[0].get("team_side") or "")
    ordered = [candidate for candidate in ordered if str(candidate.get("team_side") or "") == selected_side]

    if lane_mode == "llm_freedom":
        normalized: list[str] = []
        core_count = 0
        extra_count = 0
        resolved_max_selected = None if max_selected_candidates is None else max(0, int(max_selected_candidates))
        resolved_max_core = None if max_core_candidates is None else max(0, int(max_core_candidates))
        resolved_max_extra = None if max_extra_candidates is None else max(0, int(max_extra_candidates))
        for candidate in ordered:
            role = str(candidate.get("candidate_role") or "")
            if role == "core":
                if resolved_max_core is not None and core_count >= resolved_max_core:
                    continue
                normalized.append(str(candidate["candidate_id"]))
                core_count += 1
            elif role == "extra":
                if require_core_for_extra and core_count <= 0:
                    continue
                if resolved_max_extra is not None and extra_count >= resolved_max_extra:
                    continue
                normalized.append(str(candidate["candidate_id"]))
                extra_count += 1
            else:
                continue
            if resolved_max_selected is not None and len(normalized) >= resolved_max_selected:
                break
        return normalized

    keep_by_role: dict[str, str] = {}
    for candidate in ordered:
        role = str(candidate.get("candidate_role") or "")
        if role in keep_by_role:
            continue
        keep_by_role[role] = str(candidate["candidate_id"])
    normalized: list[str] = []
    for role in allowed_roles:
        candidate_id = keep_by_role.get(str(role))
        if candidate_id:
            normalized.append(candidate_id)
    return normalized


def _rough_prompt_cost(system_prompt: str, user_payload: dict[str, Any], *, model: str, include_rationale: bool) -> float:
    payload_text = json.dumps(user_payload, separators=(",", ":"), ensure_ascii=True)
    estimated_input_tokens = max(1, int((len(system_prompt) + len(payload_text)) / 4))
    estimated_output_tokens = 96 if include_rationale else 56
    return estimate_llm_usage_cost(
        model=model,
        input_tokens=estimated_input_tokens,
        output_tokens=estimated_output_tokens,
    )


def _resolve_output_dir(request: BacktestRunRequest) -> Path:
    return ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version) / "backtests"


def _load_llm_cache(path: Path) -> _LLMCacheStore:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return _LLMCacheStore(path=path, payload=payload)
        except Exception:
            pass
    return _LLMCacheStore(path=path, payload={})


def _persist_llm_cache(cache_store: _LLMCacheStore) -> None:
    write_json(cache_store.path, cache_store.payload)


def _resolve_openai_client() -> OpenAI | None:
    load_dotenv(REPO_ROOT / ".env", override=False)
    try:
        return OpenAI()
    except Exception:
        return None


def _usage_from_response(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached_input_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _build_state_lookup(state_df: pd.DataFrame) -> dict[tuple[str, str, int], dict[str, Any]]:
    if state_df.empty:
        return {}
    work = state_df.copy()
    work["game_id"] = work["game_id"].astype(str)
    work["team_side"] = work["team_side"].astype(str)
    work["state_index"] = pd.to_numeric(work["state_index"], errors="coerce").fillna(-1).astype(int)
    lookup: dict[tuple[str, str, int], dict[str, Any]] = {}
    for record in work.to_dict(orient="records"):
        key = (str(record.get("game_id")), str(record.get("team_side")), int(record.get("state_index") or -1))
        lookup[key] = record
    return lookup


def _build_trace_lookup(state_df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    if state_df.empty:
        return {}
    work = state_df.copy()
    work["game_id"] = work["game_id"].astype(str)
    work["team_side"] = work["team_side"].astype(str)
    work["state_index"] = pd.to_numeric(work["state_index"], errors="coerce").fillna(-1).astype(int)
    trace_columns = [column for column in BACKTEST_TRACE_STATE_COLUMNS if column in work.columns]
    traces: dict[tuple[str, str], pd.DataFrame] = {}
    for (game_id, team_side), group in work.groupby(["game_id", "team_side"], sort=False):
        traces[(str(game_id), str(team_side))] = group.sort_values("state_index", kind="mergesort")[trace_columns].reset_index(drop=True)
    return traces


def _compact_trace_strings(trace_df: pd.DataFrame, *, entry_state_index: int | None) -> list[str]:
    if trace_df.empty or entry_state_index is None:
        return []
    work = trace_df.copy()
    work["state_index"] = pd.to_numeric(work["state_index"], errors="coerce").fillna(-1).astype(int)
    relevant = work[work["state_index"] <= int(entry_state_index)].tail(_LLM_TRACE_ROW_LIMIT)
    trace_rows: list[str] = []
    for record in relevant.to_dict(orient="records"):
        period_label = str(record.get("period_label") or "")
        score_diff = _safe_float(record.get("score_diff"))
        team_price = _safe_float(record.get("team_price"))
        delta_from_open = _safe_float(record.get("price_delta_from_open"))
        net_points_last_5_events = _safe_float(record.get("net_points_last_5_events"))
        trace_rows.append(
            f"{period_label}|sd={score_diff if score_diff is not None else 'na'}|p={team_price if team_price is not None else 'na'}"
            f"|dfo={delta_from_open if delta_from_open is not None else 'na'}|m5={net_points_last_5_events if net_points_last_5_events is not None else 'na'}"
        )
    return trace_rows


def _build_context_lookup(
    trades_df: pd.DataFrame,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    if trades_df.empty:
        return {}, {}, {"trade_count": 0, "win_rate": None, "avg_return": None}, []

    context_df = (
        trades_df.groupby(["opening_band", "period_label", "context_bucket"], dropna=False)
        .agg(
            trade_count=("game_id", "count"),
            win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
            avg_return=("gross_return_with_slippage", "mean"),
        )
        .reset_index()
    )
    band_period_df = (
        trades_df.groupby(["opening_band", "period_label"], dropna=False)
        .agg(
            trade_count=("game_id", "count"),
            win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
            avg_return=("gross_return_with_slippage", "mean"),
        )
        .reset_index()
    )
    exact_lookup = {
        (str(row["opening_band"]), str(row["period_label"]), str(row["context_bucket"])): {
            "trade_count": int(row["trade_count"]),
            "win_rate": _safe_float(row["win_rate"]),
            "avg_return": _safe_float(row["avg_return"]),
        }
        for row in context_df.to_dict(orient="records")
    }
    band_period_lookup = {
        (str(row["opening_band"]), str(row["period_label"])): {
            "trade_count": int(row["trade_count"]),
            "win_rate": _safe_float(row["win_rate"]),
            "avg_return": _safe_float(row["avg_return"]),
        }
        for row in band_period_df.to_dict(orient="records")
    }
    overall = {
        "trade_count": int(len(trades_df)),
        "win_rate": float((trades_df["gross_return_with_slippage"] > 0).mean()),
        "avg_return": float(trades_df["gross_return_with_slippage"].mean()),
    }
    context_rows = context_df.sort_values(["avg_return", "trade_count"], ascending=[False, False]).to_dict(orient="records")
    return exact_lookup, band_period_lookup, overall, context_rows


def _build_family_profiles(
    selection_result: BacktestResult,
    *,
    registry: dict[str, StrategyDefinition],
    strategy_families: tuple[str, ...] | list[str],
    core_strategy_families: tuple[str, ...] | list[str],
) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for family in strategy_families:
        definition = registry.get(str(family))
        if definition is None:
            continue
        trades_df = _clean_trades_df(selection_result.trade_frames.get(str(family), pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)))
        if trades_df.empty:
            overall = {"trade_count": 0, "win_rate": None, "avg_return": None}
            context_rows: list[dict[str, Any]] = []
        else:
            context_df = (
                trades_df.groupby(["opening_band", "period_label", "context_bucket"], dropna=False)
                .agg(
                    trade_count=("game_id", "count"),
                    win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
                    avg_return=("gross_return_with_slippage", "mean"),
                )
                .reset_index()
            )
            overall = {
                "trade_count": int(len(trades_df)),
                "win_rate": float((trades_df["gross_return_with_slippage"] > 0).mean()),
                "avg_return": float(trades_df["gross_return_with_slippage"].mean()),
            }
            context_rows = context_df.to_dict(orient="records")
        sorted_best = sorted(
            context_rows,
            key=lambda row: (
                _safe_float(row.get("avg_return")) is not None,
                _safe_float(row.get("avg_return")) or float("-inf"),
                int(row.get("trade_count") or 0),
            ),
            reverse=True,
        )
        sorted_worst = sorted(
            context_rows,
            key=lambda row: (
                _safe_float(row.get("avg_return")) is not None,
                _safe_float(row.get("avg_return")) or float("inf"),
                -int(row.get("trade_count") or 0),
            ),
        )

        def _format_context(row: dict[str, Any]) -> str:
            return (
                f"{row.get('opening_band')}/{row.get('period_label')}/{row.get('context_bucket')}"
                f"|n={int(row.get('trade_count') or 0)}"
                f"|wr={round(_safe_float(row.get('win_rate')) or 0.0, 3)}"
                f"|ar={round(_safe_float(row.get('avg_return')) or 0.0, 3)}"
            )

        profiles[str(family)] = {
            "family": str(family),
            "candidate_role": "core" if str(family) in set(core_strategy_families) else "extra",
            "entry_rule": definition.entry_rule,
            "exit_rule": definition.exit_rule,
            "description": definition.description,
            "tags": list(definition.tags),
            "trade_count": int(overall["trade_count"]),
            "win_rate": round(_safe_float(overall.get("win_rate")) or 0.0, 6) if overall.get("win_rate") is not None else None,
            "avg_return": round(_safe_float(overall.get("avg_return")) or 0.0, 6) if overall.get("avg_return") is not None else None,
            "strong_contexts": [_format_context(row) for row in sorted_best[:2]],
            "weak_contexts": [_format_context(row) for row in sorted_worst[:2]],
        }
    return profiles


def _resolve_context_stats(prior: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    opening_band = str(record.get("opening_band") or "")
    period_label = str(record.get("period_label") or "")
    context_bucket = str(record.get("context_bucket") or "")
    exact_lookup = prior.get("exact_context_lookup") or {}
    band_period_lookup = prior.get("band_period_lookup") or {}
    return (
        exact_lookup.get((opening_band, period_label, context_bucket))
        or band_period_lookup.get((opening_band, period_label))
        or (prior.get("overall") or {"trade_count": 0, "win_rate": None, "avg_return": None})
    )


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _build_game_candidates(
    sample_result: BacktestResult,
    *,
    family_profiles: dict[str, dict[str, Any]],
    priors: dict[str, dict[str, Any]],
    core_strategy_families: tuple[str, ...] | list[str],
    extra_strategy_families: tuple[str, ...] | list[str],
) -> dict[str, list[dict[str, Any]]]:
    state_lookup = _build_state_lookup(sample_result.state_df)
    trace_lookup = _build_trace_lookup(sample_result.state_df)
    core_family_set = {str(family) for family in core_strategy_families}
    extra_family_set = {str(family) for family in extra_strategy_families}
    game_candidates: dict[str, list[dict[str, Any]]] = {}

    for family, profile in family_profiles.items():
        family_role = str(profile.get("candidate_role") or "")
        if family_role == "core" and family not in core_family_set:
            continue
        if family_role == "extra" and family not in extra_family_set:
            continue
        trades_df = _clean_trades_df(sample_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)))
        if trades_df.empty:
            continue
        work = trades_df.copy()
        work["game_id"] = work["game_id"].astype(str)
        work["team_side"] = work["team_side"].astype(str)
        if "entry_state_index" in work.columns:
            work["entry_state_index"] = pd.to_numeric(work["entry_state_index"], errors="coerce")
        if "signal_strength" in work.columns:
            work["signal_strength"] = pd.to_numeric(work["signal_strength"], errors="coerce")
        work = work.sort_values(
            ["game_id", "team_side", "signal_strength", "entry_at"],
            ascending=[True, True, False, True],
            kind="mergesort",
            na_position="last",
        ).drop_duplicates(subset=["game_id", "team_side"], keep="first")

        for record in work.to_dict(orient="records"):
            game_id = str(record.get("game_id"))
            team_side = str(record.get("team_side") or "")
            entry_state_index = int(record.get("entry_state_index")) if pd.notna(record.get("entry_state_index")) else None
            state_snapshot = state_lookup.get((game_id, team_side, int(entry_state_index or -1)), {})
            trace_rows = _compact_trace_strings(trace_lookup.get((game_id, team_side), pd.DataFrame()), entry_state_index=entry_state_index)
            context_stats = _resolve_context_stats(priors.get(family) or {}, record) if family in core_family_set else {
                "trade_count": profile.get("trade_count"),
                "win_rate": profile.get("win_rate"),
                "avg_return": profile.get("avg_return"),
            }
            if family in core_family_set:
                deterministic_confidence, components = score_master_router_candidate(record, family=family, priors=priors)
            else:
                deterministic_confidence = None
                components = {}
            entry_metadata = _parse_json_dict(record.get("entry_metadata_json"))
            candidate_id = f"{family}|{game_id}|{team_side}|{entry_state_index if entry_state_index is not None else 'na'}"
            avg_return = _safe_float(context_stats.get("avg_return")) if context_stats else None
            trade_count = int(context_stats.get("trade_count") or 0) if context_stats else 0
            signal_strength = _safe_float(record.get("signal_strength")) or 0.0
            rank_score = (
                (1000.0 * (deterministic_confidence or 0.0))
                + (100.0 * (avg_return or 0.0))
                + signal_strength
                + trade_count
            )
            game_candidates.setdefault(game_id, []).append(
                {
                    "candidate_id": candidate_id,
                    "game_id": game_id,
                    "game_date": _serialise_scalar(record.get("game_date") or state_snapshot.get("game_date")),
                    "candidate_role": family_role,
                    "strategy_family": family,
                    "team_side": team_side,
                    "team_slug": str(record.get("team_slug") or state_snapshot.get("team_slug") or ""),
                    "opponent_team_slug": str(record.get("opponent_team_slug") or state_snapshot.get("opponent_team_slug") or ""),
                    "opening_band": str(record.get("opening_band") or state_snapshot.get("opening_band") or ""),
                    "period_label": str(record.get("period_label") or state_snapshot.get("period_label") or ""),
                    "context_bucket": str(record.get("context_bucket") or state_snapshot.get("context_bucket") or ""),
                    "score_diff_bucket": str(record.get("score_diff_bucket") or state_snapshot.get("score_diff_bucket") or ""),
                    "entry_state_index": entry_state_index,
                    "entry_at": _serialise_scalar(record.get("entry_at")),
                    "entry_price": round(_safe_float(record.get("entry_price")) or 0.0, 6),
                    "signal_strength": round(signal_strength, 6),
                    "deterministic_confidence": round(deterministic_confidence, 6) if deterministic_confidence is not None else None,
                    "context_trade_count": trade_count,
                    "context_win_rate": round(_safe_float(context_stats.get("win_rate")) or 0.0, 6) if context_stats and context_stats.get("win_rate") is not None else None,
                    "context_avg_return": round(avg_return or 0.0, 6) if avg_return is not None else None,
                    "score_diff": round(_safe_float(state_snapshot.get("score_diff")) or 0.0, 6) if state_snapshot else None,
                    "price_delta_from_open": round(_safe_float(state_snapshot.get("price_delta_from_open")) or 0.0, 6) if state_snapshot else None,
                    "lead_changes_so_far": int(_safe_float(state_snapshot.get("lead_changes_so_far")) or 0) if state_snapshot else None,
                    "net_points_last_5_events": round(_safe_float(state_snapshot.get("net_points_last_5_events")) or 0.0, 6) if state_snapshot else None,
                    "seconds_to_game_end": round(_safe_float(state_snapshot.get("seconds_to_game_end")) or 0.0, 6) if state_snapshot else None,
                    "entry_metadata": {
                        key: entry_metadata[key]
                        for key in sorted(entry_metadata.keys())
                        if key in {"entry_threshold", "target_price", "stop_loss", "target_move", "min_momentum", "min_score_diff"}
                    },
                    "trace_rows": trace_rows,
                    "deterministic_components": components,
                    "rank_score": rank_score,
                    "trade_record": record,
                }
            )

    for game_id, candidates in game_candidates.items():
        candidates.sort(
            key=lambda candidate: (
                candidate.get("candidate_role") != "core",
                -float(candidate.get("rank_score") or 0.0),
                candidate.get("entry_at") or "",
                candidate.get("candidate_id") or "",
            )
        )
        game_candidates[game_id] = candidates
    return game_candidates


def _subset_backtest_result(result: BacktestResult, game_ids: tuple[str, ...] | list[str]) -> BacktestResult:
    game_id_set = {str(game_id) for game_id in game_ids}
    state_subset = result.state_df[result.state_df["game_id"].astype(str).isin(game_id_set)].copy()
    trade_frames = {
        family: _clean_trades_df(frame[frame["game_id"].astype(str).isin(game_id_set)].copy()) if not frame.empty else pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
        for family, frame in result.trade_frames.items()
    }
    payload = dict(result.payload)
    payload["games_considered"] = len(game_id_set)
    payload["state_rows_considered"] = int(len(state_subset))
    return BacktestResult(
        payload=payload,
        trade_frames=trade_frames,
        state_df=state_subset,
        strategy_registry=result.strategy_registry,
    )


def _build_llm_prompt_payload(
    *,
    lane_name: str,
    lane_mode: str,
    llm_component_scope: str,
    prompt_profile: str,
    use_confidence_gate: bool,
    game_id: str,
    opening_band: str,
    fallback_ids: list[str],
    available_candidates: list[dict[str, Any]],
    family_profiles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    families_in_scope = sorted({str(candidate["strategy_family"]) for candidate in available_candidates})
    profile_rows = []
    for family in families_in_scope:
        profile = family_profiles.get(family) or {}
        if prompt_profile == "compact":
            profile_rows.append(
                {
                    "fam": family,
                    "role": profile.get("candidate_role"),
                    "wr": profile.get("win_rate"),
                    "ar": profile.get("avg_return"),
                    "n": profile.get("trade_count"),
                    "tags": list(profile.get("tags") or [])[:3],
                }
            )
        else:
            profile_rows.append(
                {
                    "fam": family,
                    "role": profile.get("candidate_role"),
                    "entry": profile.get("entry_rule"),
                    "exit": profile.get("exit_rule"),
                    "wr": profile.get("win_rate"),
                    "ar": profile.get("avg_return"),
                    "n": profile.get("trade_count"),
                    "strong": profile.get("strong_contexts") or [],
                    "weak": profile.get("weak_contexts") or [],
                }
            )
    candidate_rows = []
    for candidate in available_candidates:
        base_row = {
            "id": candidate["candidate_id"],
            "role": candidate["candidate_role"],
            "fam": candidate["strategy_family"],
            "side": candidate["team_side"],
            "tm": candidate["team_slug"],
            "opp": candidate["opponent_team_slug"],
            "ob": candidate["opening_band"],
            "per": candidate["period_label"],
            "ctx": candidate["context_bucket"],
            "sd": candidate["score_diff"],
            "ep": candidate["entry_price"],
            "sig": candidate["signal_strength"],
            "det_conf": candidate["deterministic_confidence"],
            "ctx_ar": candidate["context_avg_return"],
            "ctx_wr": candidate["context_win_rate"],
            "ctx_n": candidate["context_trade_count"],
            "dfo": candidate["price_delta_from_open"],
            "lc": candidate["lead_changes_so_far"],
            "m5": candidate["net_points_last_5_events"],
        }
        if prompt_profile != "compact":
            base_row["trace"] = candidate["trace_rows"]
            base_row["meta"] = candidate["entry_metadata"]
        candidate_rows.append(base_row)
    sorted_candidates = sorted(
        available_candidates,
        key=lambda candidate: (
            -float(candidate.get("deterministic_confidence") or 0.0),
            -float(candidate.get("rank_score") or 0.0),
            str(candidate.get("candidate_id") or ""),
        ),
    )
    top_candidate = sorted_candidates[0] if sorted_candidates else {}
    second_candidate = sorted_candidates[1] if len(sorted_candidates) > 1 else {}
    top_confidence = _safe_float(top_candidate.get("deterministic_confidence")) or 0.0
    second_confidence = _safe_float(second_candidate.get("deterministic_confidence")) or 0.0
    candidate_by_id = {str(candidate.get("candidate_id") or ""): candidate for candidate in available_candidates}
    fallback_rows = []
    for candidate_id in fallback_ids:
        candidate = candidate_by_id.get(str(candidate_id))
        if candidate is None:
            continue
        fallback_rows.append(
            {
                "id": str(candidate.get("candidate_id") or ""),
                "fam": str(candidate.get("strategy_family") or ""),
                "role": str(candidate.get("candidate_role") or ""),
                "side": str(candidate.get("team_side") or ""),
                "det_conf": _safe_float(candidate.get("deterministic_confidence")),
                "ctx_ar": _safe_float(candidate.get("context_avg_return")),
                "ctx_wr": _safe_float(candidate.get("context_win_rate")),
                "ctx_n": int(candidate.get("context_trade_count") or 0),
                "sig": _safe_float(candidate.get("signal_strength")),
            }
        )
    router_hint = {
        "top_id": top_candidate.get("candidate_id"),
        "top_family": top_candidate.get("strategy_family"),
        "top_det_conf": round(top_confidence, 6),
        "gap_to_next": round(max(0.0, top_confidence - second_confidence), 6),
        "candidate_count": len(available_candidates),
        "confidence_gate_active": bool(use_confidence_gate),
        "default_ids": [str(candidate_id) for candidate_id in fallback_ids],
        "default_rows": fallback_rows,
    }
    payload = {
        "v": _LLM_PROMPT_VERSION,
        "lane": lane_name,
        "mode": lane_mode,
        "scope": llm_component_scope,
        "profile": prompt_profile,
        "game_id": game_id,
        "opening_band": opening_band,
        "router": router_hint,
        "profiles": profile_rows,
        "candidates": candidate_rows,
    }
    if prompt_profile in {"compact_anchor", "compact_anchor_examples"}:
        payload["selection_policy"] = {
            "task": "accept_or_override_router_default",
            "prefer_keep_default_when_top_det_conf_at_least": 0.58,
            "only_skip_when": [
                "all candidates are weak or contradictory",
                "the default selection has clearly adverse context",
                "a same-side replacement is materially stronger",
            ],
            "restrained_lane_target": "prefer one core selection; add an extra only on the same side with independent support",
        }
    return payload


def _build_llm_system_prompt(
    *,
    lane_mode: str,
    llm_component_scope: str,
    prompt_profile: str,
    include_rationale: bool,
    use_confidence_gate: bool,
    max_selected_candidates: int | None = None,
    max_core_candidates: int | None = None,
    max_extra_candidates: int | None = None,
    require_core_for_extra: bool = False,
) -> str:
    if lane_mode != "llm_freedom":
        constraint = "Select no more than one core and one extra candidate."
    else:
        constraint_parts = ["You may select multiple candidates only if they reinforce the same side."]
        if max_selected_candidates is not None:
            constraint_parts.append(f"Never select more than {max(0, int(max_selected_candidates))} total candidates.")
        if max_core_candidates is not None:
            constraint_parts.append(f"Never select more than {max(0, int(max_core_candidates))} core candidates.")
        if max_extra_candidates is not None:
            constraint_parts.append(f"Never select more than {max(0, int(max_extra_candidates))} extra candidates.")
        if require_core_for_extra:
            constraint_parts.append("Only select an extra candidate if you also selected a same-side core candidate.")
        constraint = " ".join(constraint_parts)
    profile_hint = (
        "Compact payload: rely on deterministic confidence, context stats, score state, and momentum fields."
        if prompt_profile in {"compact", "compact_anchor", "compact_anchor_examples"}
        else "Full payload: you may also use trace rows and entry metadata."
    )
    gate_hint = (
        "If the router leader is already strong, prefer keeping the deterministic leader instead of forcing a novelty override."
        if use_confidence_gate
        else "Only override the deterministic leader when the evidence clearly favors another candidate or a skip."
    )
    anchor_hint = (
        "This is an accept-or-override task. The router block contains the deterministic default selection. "
        "If router.default_ids is non-empty and the top deterministic confidence is at least 0.58, keep the default unless there is clear contrary evidence. "
        "Clear contrary evidence means the default has adverse context and weak state support, or another candidate on the same side is materially stronger. "
        "Avoid zero selections when a valid default exists and there is no clear contradiction."
        if prompt_profile in {"compact_anchor", "compact_anchor_examples"}
        else ""
    )
    example_hint = (
        "Example A: if the router default has positive context return, useful sample size, and no stronger same-side alternative, keep it. "
        "Example B: if the router default exists but another same-side candidate has much better confidence and context while the default is weak, override to that stronger candidate."
        if prompt_profile == "compact_anchor_examples"
        else ""
    )
    output_hint = (
        "Return only minified JSON with the selected candidate ids, a confidence score, and a rationale under 80 characters."
        if include_rationale
        else "Return only minified JSON with the selected candidate ids and a confidence score."
    )
    return (
        "You are evaluating NBA live-trading candidate strategies. "
        "Use only the JSON provided. Optimize for compounded bankroll growth under drawdown control. "
        "Prefer skip only when support is genuinely weak or contexts are contradictory. "
        "Never invent new strategy ids. Never select both sides of the same game. "
        f"Lane scope is {llm_component_scope}. {constraint} {profile_hint} {gate_hint} {anchor_hint} {example_hint} {output_hint}"
    )


def _select_fallback_candidate_ids(
    available_candidates: list[dict[str, Any]],
    *,
    lane_mode: str,
    allowed_roles: tuple[str, ...] | list[str],
) -> list[str]:
    if not available_candidates:
        return []
    allowed_role_set = {str(role) for role in allowed_roles}
    sorted_candidates = [
        candidate
        for candidate in available_candidates
        if str(candidate.get("candidate_role")) in allowed_role_set
    ]
    if not sorted_candidates:
        return []
    sorted_candidates.sort(
        key=lambda candidate: (
            -float(candidate.get("rank_score") or 0.0),
            candidate.get("entry_at") or "",
            candidate.get("candidate_id") or "",
        )
    )
    selected_side = str(sorted_candidates[0].get("team_side") or "")
    side_candidates = [candidate for candidate in sorted_candidates if str(candidate.get("team_side") or "") == selected_side]
    if lane_mode == "llm_freedom":
        return [
            str(candidate["candidate_id"])
            for candidate in side_candidates
            if float(candidate.get("rank_score") or 0.0) > 1.0
        ]

    selected: list[str] = []
    seen_roles: set[str] = set()
    for candidate in side_candidates:
        role = str(candidate.get("candidate_role") or "")
        if role in seen_roles:
            continue
        selected.append(str(candidate["candidate_id"]))
        seen_roles.add(role)
    return selected


def _confidence_gate_decision(
    available_candidates: list[dict[str, Any]],
    *,
    enabled: bool,
    min_top_confidence: float,
    min_gap: float,
) -> tuple[bool, float, float]:
    if not enabled or not available_candidates:
        return False, 0.0, 0.0
    sorted_candidates = sorted(
        available_candidates,
        key=lambda candidate: (
            -float(candidate.get("deterministic_confidence") or 0.0),
            -float(candidate.get("rank_score") or 0.0),
            str(candidate.get("candidate_id") or ""),
        ),
    )
    top_confidence = _safe_float(sorted_candidates[0].get("deterministic_confidence")) or 0.0
    second_confidence = _safe_float(sorted_candidates[1].get("deterministic_confidence")) or 0.0 if len(sorted_candidates) > 1 else 0.0
    gap = max(0.0, top_confidence - second_confidence)
    return top_confidence >= float(min_top_confidence) and gap >= float(min_gap), top_confidence, gap


def _maybe_call_llm(
    *,
    client: OpenAI | None,
    model: str,
    lane_name: str,
    lane_group: str,
    lane_mode: str,
    llm_component_scope: str,
    prompt_profile: str,
    include_rationale: bool,
    reasoning_effort: str,
    use_confidence_gate: bool,
    gate_min_top_confidence: float,
    gate_min_gap: float,
    allowed_roles: tuple[str, ...] | list[str],
    max_selected_candidates: int | None,
    max_core_candidates: int | None,
    max_extra_candidates: int | None,
    require_core_for_extra: bool,
    available_candidates: list[dict[str, Any]],
    family_profiles: dict[str, dict[str, Any]],
    max_budget_usd: float,
    budget_state: _LLMBudgetState,
    cache_store: _LLMCacheStore,
) -> _LLMCallResult:
    fallback_ids = _select_fallback_candidate_ids(available_candidates, lane_mode=lane_mode, allowed_roles=allowed_roles)
    fallback_ids = normalize_llm_selected_candidate_ids(
        fallback_ids,
        available_candidates,
        lane_mode=lane_mode,
        allowed_roles=allowed_roles,
        max_selected_candidates=max_selected_candidates,
        max_core_candidates=max_core_candidates,
        max_extra_candidates=max_extra_candidates,
        require_core_for_extra=require_core_for_extra,
    )
    if not available_candidates:
        return _LLMCallResult([], 0.0, "No candidates available.", False, 0, 0, 0, 0, 0.0, "no_candidates")

    gate_triggered, top_confidence, top_gap = _confidence_gate_decision(
        available_candidates,
        enabled=use_confidence_gate,
        min_top_confidence=gate_min_top_confidence,
        min_gap=gate_min_gap,
    )
    if gate_triggered:
        rationale = "" if not include_rationale else f"Gate kept router leader c={top_confidence:.2f} g={top_gap:.2f}"
        return _LLMCallResult(
            fallback_ids,
            top_confidence if fallback_ids else 0.0,
            rationale,
            False,
            0,
            0,
            0,
            0,
            0.0,
            "confidence_gate",
        )

    prompt_payload = _build_llm_prompt_payload(
        lane_name=lane_name,
        lane_mode=lane_mode,
        llm_component_scope=llm_component_scope,
        prompt_profile=prompt_profile,
        use_confidence_gate=use_confidence_gate,
        game_id=str(available_candidates[0].get("game_id") or ""),
        opening_band=str(available_candidates[0].get("opening_band") or ""),
        fallback_ids=fallback_ids,
        available_candidates=available_candidates,
        family_profiles=family_profiles,
    )
    system_prompt = _build_llm_system_prompt(
        lane_mode=lane_mode,
        llm_component_scope=llm_component_scope,
        prompt_profile=prompt_profile,
        include_rationale=include_rationale,
        use_confidence_gate=use_confidence_gate,
        max_selected_candidates=max_selected_candidates,
        max_core_candidates=max_core_candidates,
        max_extra_candidates=max_extra_candidates,
        require_core_for_extra=require_core_for_extra,
    )
    cache_key_payload = {
        "model": model,
        "prompt_version": _LLM_PROMPT_VERSION,
        "lane_name": lane_name,
        "lane_group": lane_group,
        "lane_mode": lane_mode,
        "llm_component_scope": llm_component_scope,
        "prompt_profile": prompt_profile,
        "include_rationale": include_rationale,
        "reasoning_effort": reasoning_effort,
        "use_confidence_gate": use_confidence_gate,
        "gate_min_top_confidence": gate_min_top_confidence,
        "gate_min_gap": gate_min_gap,
        "max_selected_candidates": max_selected_candidates,
        "max_core_candidates": max_core_candidates,
        "max_extra_candidates": max_extra_candidates,
        "require_core_for_extra": require_core_for_extra,
        "payload": prompt_payload,
    }
    cache_key = hashlib.sha256(json.dumps(cache_key_payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    cached = cache_store.payload.get(cache_key)
    if isinstance(cached, dict):
        cached_selection = normalize_llm_selected_candidate_ids(
            cached.get("selected_candidate_ids") or [],
            available_candidates,
            lane_mode=lane_mode,
            allowed_roles=allowed_roles,
            max_selected_candidates=max_selected_candidates,
            max_core_candidates=max_core_candidates,
            max_extra_candidates=max_extra_candidates,
            require_core_for_extra=require_core_for_extra,
        )
        return _LLMCallResult(
            cached_selection,
            float(cached.get("confidence") or 0.0),
            str(cached.get("rationale") or ""),
            True,
            0,
            0,
            0,
            0,
            0.0,
            "cache_hit",
        )

    estimated_cost = _rough_prompt_cost(
        system_prompt,
        prompt_payload,
        model=model,
        include_rationale=include_rationale,
    )
    if budget_state.spent_usd + estimated_cost > max_budget_usd:
        return _LLMCallResult(
            fallback_ids,
            _LLM_FALLBACK_CONFIDENCE if fallback_ids else 0.0,
            "Budget guard fallback to deterministic ranking.",
            False,
            0,
            0,
            0,
            0,
            0.0,
            "budget_guard",
        )
    if client is None:
        return _LLMCallResult(
            fallback_ids,
            _LLM_FALLBACK_CONFIDENCE if fallback_ids else 0.0,
            "OpenAI client unavailable; deterministic fallback.",
            False,
            0,
            0,
            0,
            0,
            0.0,
            "client_unavailable",
        )

    def _execute_parse(
        *,
        response_model: type[BaseModel],
        output_token_cap: int,
    ) -> tuple[BaseModel, dict[str, int], float]:
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt_payload, separators=(",", ":"), ensure_ascii=True)},
            ],
            text_format=response_model,
            reasoning={"effort": reasoning_effort},
            max_output_tokens=output_token_cap,
            store=False,
        )
        parsed = getattr(response, "output_parsed", None) or response_model()
        usage = _usage_from_response(response)
        actual_cost = estimate_llm_usage_cost(
            model=model,
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
        )
        return parsed, usage, actual_cost

    try:
        response_model: type[BaseModel] = _LLMSelectionResponse if include_rationale else _LLMSelectionNoRationaleResponse
        output_token_cap = 320 if include_rationale and lane_mode == "llm_freedom" else 240
        total_usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        total_cost = 0.0
        try:
            parsed, usage, actual_cost = _execute_parse(
                response_model=response_model,
                output_token_cap=output_token_cap,
            )
        except Exception:
            if not include_rationale:
                raise
            parsed, usage, actual_cost = _execute_parse(
                response_model=_LLMSelectionNoRationaleResponse,
                output_token_cap=160,
            )
        for key in total_usage:
            total_usage[key] += int(usage[key])
        total_cost += float(actual_cost)
        budget_state.spent_usd += total_cost
        normalized_ids = normalize_llm_selected_candidate_ids(
            parsed.selected_candidate_ids,
            available_candidates,
            lane_mode=lane_mode,
            allowed_roles=allowed_roles,
            max_selected_candidates=max_selected_candidates,
            max_core_candidates=max_core_candidates,
            max_extra_candidates=max_extra_candidates,
            require_core_for_extra=require_core_for_extra,
        )
        cache_store.payload[cache_key] = {
            "selected_candidate_ids": list(normalized_ids),
            "confidence": float(parsed.confidence),
            "rationale": str(getattr(parsed, "rationale", "") or ""),
        }
        _persist_llm_cache(cache_store)
        return _LLMCallResult(
            normalized_ids,
            float(parsed.confidence),
            str(getattr(parsed, "rationale", "") or ""),
            False,
            total_usage["input_tokens"],
            total_usage["cached_input_tokens"],
            total_usage["output_tokens"],
            total_usage["reasoning_tokens"],
            total_cost,
            "ok",
        )
    except Exception as exc:
        return _LLMCallResult(
            fallback_ids,
            _LLM_FALLBACK_CONFIDENCE if fallback_ids else 0.0,
            "LLM error fallback to deterministic ranking.",
            False,
            0,
            0,
            0,
            0,
            0.0,
            "error",
            str(exc),
        )


def _selected_trade_frame(selected_candidate_ids: list[str], candidate_lookup: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for candidate_id in selected_candidate_ids:
        candidate = candidate_lookup.get(str(candidate_id))
        if not candidate:
            continue
        record = dict(candidate["trade_record"])
        record["source_strategy_family"] = str(candidate["strategy_family"])
        record["llm_lane_candidate_id"] = str(candidate_id)
        rows.append(record)
    if not rows:
        return pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    frame = pd.DataFrame(rows)
    base_columns = list(BACKTEST_TRADE_COLUMNS)
    extra_columns = [column for column in frame.columns if column not in base_columns]
    return frame.reindex(columns=[*base_columns, *extra_columns])


def _portfolio_summary_row_to_frame(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "starting_bankroll": _safe_float(summary.get("starting_bankroll")) or 0.0,
        "first_entry_at": _serialise_scalar(summary.get("first_entry_at")),
        "ending_bankroll": _safe_float(summary.get("ending_bankroll")) or 0.0,
        "compounded_return": _safe_float(summary.get("compounded_return")) or 0.0,
        "max_drawdown_pct": _safe_float(summary.get("max_drawdown_pct")) or 0.0,
        "executed_trade_count": int(summary.get("executed_trade_count") or 0),
        "avg_executed_trade_return_with_slippage": _safe_float(summary.get("avg_executed_trade_return_with_slippage")) or 0.0,
    }


def _run_lane_portfolio(
    trades_df: pd.DataFrame,
    *,
    sample_name: str,
    lane_name: str,
    portfolio_scope: str,
    strategy_family_members: tuple[str, ...] | list[str],
    request: BacktestRunRequest,
) -> dict[str, Any]:
    summary, _steps_df = simulate_trade_portfolio(
        trades_df,
        sample_name=sample_name,
        strategy_family=lane_name,
        portfolio_scope=portfolio_scope,
        strategy_family_members=tuple(strategy_family_members),
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


def _run_lane_portfolio_with_steps(
    trades_df: pd.DataFrame,
    *,
    sample_name: str,
    lane_name: str,
    portfolio_scope: str,
    strategy_family_members: tuple[str, ...] | list[str],
    request: BacktestRunRequest,
) -> tuple[dict[str, Any], pd.DataFrame]:
    return simulate_trade_portfolio(
        trades_df,
        sample_name=sample_name,
        strategy_family=lane_name,
        portfolio_scope=portfolio_scope,
        strategy_family_members=tuple(strategy_family_members),
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


def _build_daily_path_frame(
    summary_df: pd.DataFrame,
    steps_df: pd.DataFrame,
    *,
    lane_lookup: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_DAILY_PATH_COLUMNS)

    step_work = steps_df.copy() if not steps_df.empty else pd.DataFrame(columns=steps_df.columns)
    if not step_work.empty:
        step_work["settled_at"] = pd.to_datetime(step_work["settled_at"], errors="coerce", utc=True)
        step_work["entry_at"] = pd.to_datetime(step_work["entry_at"], errors="coerce", utc=True)
        step_work["bankroll_after"] = pd.to_numeric(step_work["bankroll_after"], errors="coerce")

    rows: list[dict[str, Any]] = []
    for summary in summary_df.to_dict(orient="records"):
        lane_name = str(summary.get("lane_name") or summary.get("strategy_family") or "")
        lane_meta = lane_lookup.get(lane_name) or {}
        lane_steps = (
            step_work[
                (step_work["sample_name"] == summary.get("sample_name"))
                & (step_work["strategy_family"] == lane_name)
            ].copy()
            if not step_work.empty
            else pd.DataFrame(columns=step_work.columns)
        )
        lane_steps = lane_steps[
            (lane_steps["portfolio_action"] == "executed") & lane_steps["settled_at"].notna()
        ].copy() if not lane_steps.empty else lane_steps
        starting_bankroll = float(summary.get("starting_bankroll") or 0.0)
        first_entry_at = pd.to_datetime(summary.get("first_entry_at"), errors="coerce", utc=True)
        if pd.notna(first_entry_at):
            rows.append(
                {
                    "sample_name": summary.get("sample_name"),
                    "lane_name": lane_name,
                    "lane_mode": summary.get("lane_mode"),
                    "lane_group": lane_meta.get("lane_group"),
                    "path_day_index": 0,
                    "path_date": _serialise_scalar(first_entry_at.date()),
                    "settled_trade_count_to_date": 0,
                    "ending_bankroll": starting_bankroll,
                    "daily_pnl_amount": 0.0,
                    "compounded_return": 0.0,
                }
            )
        previous_bankroll = starting_bankroll
        for index, record in enumerate(lane_steps.sort_values(["settled_at", "trade_sequence"], kind="mergesort").to_dict(orient="records"), start=1):
            ending_bankroll = _safe_float(record.get("bankroll_after")) or previous_bankroll
            path_date = pd.to_datetime(record.get("settled_at"), errors="coerce", utc=True)
            rows.append(
                {
                    "sample_name": summary.get("sample_name"),
                    "lane_name": lane_name,
                    "lane_mode": summary.get("lane_mode"),
                    "lane_group": lane_meta.get("lane_group"),
                    "path_day_index": index,
                    "path_date": _serialise_scalar(path_date.date()) if pd.notna(path_date) else None,
                    "settled_trade_count_to_date": index,
                    "ending_bankroll": ending_bankroll,
                    "daily_pnl_amount": ending_bankroll - previous_bankroll,
                    "compounded_return": ((ending_bankroll / starting_bankroll) - 1.0) if starting_bankroll > 0 else 0.0,
                }
            )
            previous_bankroll = ending_bankroll
    return pd.DataFrame(rows, columns=LLM_EXPERIMENT_SHOWDOWN_DAILY_PATH_COLUMNS)


def _lane_group_for_name(lane_name: str) -> str:
    return "llm_variant" if str(lane_name).startswith("llm_") else "deterministic"


def _resolve_llm_lanes(model: str) -> tuple[dict[str, Any], ...]:
    normalized_model = str(model or "").strip().lower()
    if normalized_model == "gpt-5.4":
        return tuple([*_LLM_LANES, *_LLM_FULL_MODEL_TUNED_LANES])
    return tuple(_LLM_LANES)


def _lane_config_rows(model: str) -> list[dict[str, Any]]:
    llm_lanes = _resolve_llm_lanes(model)
    return [
        {
            "lane_name": str(lane["lane_name"]),
            "lane_group": str(lane.get("lane_group") or "llm_variant"),
            "lane_mode": str(lane["lane_mode"]),
            "llm_component_scope": str(lane["llm_component_scope"]),
            "prompt_profile": str(lane.get("prompt_profile") or "full"),
            "reasoning_effort": str(lane.get("reasoning_effort") or "low"),
            "include_rationale": bool(lane.get("include_rationale", True)),
            "use_confidence_gate": bool(lane.get("use_confidence_gate", False)),
        }
        for lane in llm_lanes
    ]


def _llm_finalist_score(
    *,
    mean_ending_bankroll: float | None,
    positive_iteration_rate: float | None,
    mean_max_drawdown_pct: float | None,
    mean_executed_trade_count: float | None,
) -> float:
    bankroll = max(1.0, float(mean_ending_bankroll or 0.0))
    positive_rate = float(positive_iteration_rate or 0.0)
    drawdown = max(0.0, float(mean_max_drawdown_pct or 0.0))
    trade_density = min(1.0, max(0.0, float(mean_executed_trade_count or 0.0) / 20.0))
    return float(np.log10(bankroll) + (positive_rate * 0.95) + (trade_density * 0.15) - (drawdown * 1.35))


def _rank_showdown_lanes(lane_summary_df: pd.DataFrame) -> list[dict[str, Any]]:
    if lane_summary_df.empty:
        return []
    ranked_rows: list[dict[str, Any]] = []
    for row in lane_summary_df.to_dict(orient="records"):
        finalist_score = _llm_finalist_score(
            mean_ending_bankroll=_safe_float(row.get("mean_ending_bankroll")),
            positive_iteration_rate=_safe_float(row.get("positive_iteration_rate")),
            mean_max_drawdown_pct=_safe_float(row.get("mean_max_drawdown_pct")),
            mean_executed_trade_count=_safe_float(row.get("mean_executed_trade_count")),
        )
        ranked_rows.append(
            {
                **row,
                "finalist_score": finalist_score,
                "lane_group": row.get("lane_group") or _lane_group_for_name(str(row.get("lane_name") or "")),
            }
        )
    ranked_rows.sort(
        key=lambda row: (
            float(row.get("finalist_score") or float("-inf")),
            float(row.get("mean_ending_bankroll") or float("-inf")),
            -float(row.get("mean_max_drawdown_pct") or 0.0),
            str(row.get("lane_name") or ""),
        ),
        reverse=True,
    )
    return ranked_rows


def _run_llm_lane_sample(
    *,
    iteration_index: int,
    iteration_seed: int,
    sample_name: str,
    sampled_game_ids: tuple[str, ...] | list[str],
    lane: dict[str, Any],
    game_candidates: dict[str, list[dict[str, Any]]],
    family_profiles: dict[str, dict[str, Any]],
    request: BacktestRunRequest,
    client: OpenAI | None,
    budget_state: _LLMBudgetState,
    cache_store: _LLMCacheStore,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    lane_name = str(lane["lane_name"])
    lane_group = str(lane.get("lane_group") or "llm_variant")
    lane_mode = str(lane["lane_mode"])
    llm_component_scope = str(lane["llm_component_scope"])
    allowed_roles = tuple(str(role) for role in lane["allowed_roles"])
    prompt_profile = str(lane.get("prompt_profile") or "full")
    include_rationale = bool(lane.get("include_rationale", True))
    reasoning_effort = str(lane.get("reasoning_effort") or "low")
    use_confidence_gate = bool(lane.get("use_confidence_gate", False))
    gate_min_top_confidence = float(lane.get("gate_min_top_confidence") or 0.0)
    gate_min_gap = float(lane.get("gate_min_gap") or 0.0)
    max_selected_candidates = (
        None
        if lane.get("max_selected_candidates") is None
        else int(lane.get("max_selected_candidates"))
    )
    max_core_candidates = None if lane.get("max_core_candidates") is None else int(lane.get("max_core_candidates"))
    max_extra_candidates = None if lane.get("max_extra_candidates") is None else int(lane.get("max_extra_candidates"))
    require_core_for_extra = bool(lane.get("require_core_for_extra", False))

    lane_selected_ids: list[str] = []
    lane_token_totals = {
        "llm_call_count": 0,
        "llm_cache_hit_count": 0,
        "llm_input_tokens": 0,
        "llm_cached_input_tokens": 0,
        "llm_output_tokens": 0,
        "llm_reasoning_tokens": 0,
        "llm_estimated_cost_usd": 0.0,
    }
    candidate_lookup = {
        str(candidate["candidate_id"]): candidate
        for game_id in sampled_game_ids
        for candidate in game_candidates.get(str(game_id), [])
    }
    decision_rows: list[dict[str, Any]] = []
    allowed_role_set = set(allowed_roles)

    for game_id in sampled_game_ids:
        all_candidates = game_candidates.get(str(game_id), [])
        available_candidates = [
            candidate
            for candidate in all_candidates
            if str(candidate.get("candidate_role")) in allowed_role_set
        ]
        call_result = _maybe_call_llm(
            client=client,
            model=request.llm_model,
            lane_name=lane_name,
            lane_group=lane_group,
            lane_mode=lane_mode,
            llm_component_scope=llm_component_scope,
            prompt_profile=prompt_profile,
            include_rationale=include_rationale,
            reasoning_effort=reasoning_effort,
            use_confidence_gate=use_confidence_gate,
            gate_min_top_confidence=gate_min_top_confidence,
            gate_min_gap=gate_min_gap,
            allowed_roles=allowed_roles,
            max_selected_candidates=max_selected_candidates,
            max_core_candidates=max_core_candidates,
            max_extra_candidates=max_extra_candidates,
            require_core_for_extra=require_core_for_extra,
            available_candidates=available_candidates,
            family_profiles=family_profiles,
            max_budget_usd=float(request.llm_max_budget_usd),
            budget_state=budget_state,
            cache_store=cache_store,
        )
        lane_selected_ids.extend(call_result.selected_candidate_ids)
        lane_token_totals["llm_call_count"] += 0 if call_result.decision_status in {"cache_hit", "no_candidates", "budget_guard", "client_unavailable", "error", "confidence_gate"} else 1
        lane_token_totals["llm_cache_hit_count"] += 1 if call_result.cache_hit else 0
        lane_token_totals["llm_input_tokens"] += int(call_result.input_tokens)
        lane_token_totals["llm_cached_input_tokens"] += int(call_result.cached_input_tokens)
        lane_token_totals["llm_output_tokens"] += int(call_result.output_tokens)
        lane_token_totals["llm_reasoning_tokens"] += int(call_result.reasoning_tokens)
        lane_token_totals["llm_estimated_cost_usd"] += float(call_result.estimated_cost_usd)
        decision_rows.append(
            {
                "iteration_index": iteration_index,
                "iteration_seed": iteration_seed,
                "sample_name": sample_name,
                "lane_name": lane_name,
                "lane_mode": lane_mode,
                "lane_group": lane_group,
                "prompt_profile": prompt_profile,
                "reasoning_effort": reasoning_effort,
                "include_rationale": include_rationale,
                "use_confidence_gate": use_confidence_gate,
                "max_selected_candidates": max_selected_candidates,
                "max_core_candidates": max_core_candidates,
                "max_extra_candidates": max_extra_candidates,
                "require_core_for_extra": require_core_for_extra,
                "decision_stage": llm_component_scope,
                "game_id": game_id,
                "game_date": _serialise_scalar((all_candidates[0].get("game_date") if all_candidates else None)),
                "opening_band": str(all_candidates[0].get("opening_band") or "") if all_candidates else None,
                "available_candidate_count": len(available_candidates),
                "available_candidate_ids_json": json.dumps([candidate["candidate_id"] for candidate in available_candidates], separators=(",", ":")),
                "selected_candidate_count": len(call_result.selected_candidate_ids),
                "selected_candidate_ids_json": json.dumps(call_result.selected_candidate_ids, separators=(",", ":")),
                "selected_strategy_families_json": json.dumps(
                    sorted({candidate_lookup[candidate_id]["strategy_family"] for candidate_id in call_result.selected_candidate_ids if candidate_id in candidate_lookup}),
                    separators=(",", ":"),
                ),
                "decision_status": call_result.decision_status,
                "cache_hit_flag": bool(call_result.cache_hit),
                "llm_confidence": float(call_result.confidence),
                "rationale": call_result.rationale,
                "input_tokens": int(call_result.input_tokens),
                "cached_input_tokens": int(call_result.cached_input_tokens),
                "output_tokens": int(call_result.output_tokens),
                "reasoning_tokens": int(call_result.reasoning_tokens),
                "estimated_cost_usd": float(call_result.estimated_cost_usd),
                "error_text": call_result.error_text,
            }
        )
    return _selected_trade_frame(lane_selected_ids, candidate_lookup), lane_token_totals, decision_rows


def build_llm_experiment_frames(
    split_results: dict[str, BacktestResult],
    request: BacktestRunRequest,
    *,
    registry: dict[str, StrategyDefinition],
    core_strategy_families: tuple[str, ...] | list[str],
    extra_strategy_families: tuple[str, ...] | list[str],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    selection_sample_name = DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE
    selection_result = split_results.get(selection_sample_name)
    evaluation_result = split_results.get("random_holdout")
    empty_frames = {
        "llm_experiment_iterations": pd.DataFrame(columns=LLM_EXPERIMENT_ITERATION_COLUMNS),
        "llm_experiment_summary": pd.DataFrame(columns=LLM_EXPERIMENT_SUMMARY_COLUMNS),
        "llm_experiment_lane_summary": pd.DataFrame(columns=LLM_EXPERIMENT_LANE_SUMMARY_COLUMNS),
        "llm_experiment_decisions": pd.DataFrame(columns=LLM_EXPERIMENT_DECISION_COLUMNS),
        "llm_experiment_showdown_summary": pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_SUMMARY_COLUMNS),
        "llm_experiment_showdown_daily_paths": pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_DAILY_PATH_COLUMNS),
        "llm_experiment_showdown_decisions": pd.DataFrame(columns=LLM_EXPERIMENT_DECISION_COLUMNS),
    }
    if not request.llm_enable:
        return {
            "enabled": False,
            "status": "disabled",
            "selection_sample_name": selection_sample_name,
        }, empty_frames
    if selection_result is None or evaluation_result is None:
        return {
            "enabled": bool(request.llm_enable),
            "status": "skipped_missing_split",
            "selection_sample_name": selection_sample_name,
        }, empty_frames

    relevant_families = tuple(
        family
        for family in dict.fromkeys([*core_strategy_families, *extra_strategy_families, *_LLM_BASELINES])
        if family in registry
    )
    if not relevant_families:
        return {
            "enabled": bool(request.llm_enable),
            "status": "skipped_no_relevant_families",
            "selection_sample_name": selection_sample_name,
        }, empty_frames

    priors = build_master_router_selection_priors(selection_result, core_strategy_families=tuple(core_strategy_families))
    family_profiles = _build_family_profiles(
        selection_result,
        registry=registry,
        strategy_families=relevant_families,
        core_strategy_families=tuple(core_strategy_families),
    )
    iteration_plan = build_llm_iteration_plan(
        evaluation_result,
        strategy_families=relevant_families,
        iteration_count=request.llm_iteration_count,
        games_per_iteration=request.llm_iteration_games,
        seeds=request.robustness_seeds,
        fallback_seed=request.holdout_seed,
    )

    output_dir = _resolve_output_dir(request)
    cache_store = _load_llm_cache(output_dir / _LLM_CACHE_FILENAME)
    client = _resolve_openai_client() if request.llm_enable else None
    budget_state = _LLMBudgetState()
    llm_lanes = _resolve_llm_lanes(request.llm_model)
    lane_catalog = _lane_config_rows(request.llm_model)
    lane_lookup = {row["lane_name"]: row for row in lane_catalog}
    lane_lookup[MASTER_ROUTER_PORTFOLIO] = {
        "lane_name": MASTER_ROUTER_PORTFOLIO,
        "lane_group": "deterministic",
        "lane_mode": "deterministic",
        "llm_component_scope": "deterministic_router",
        "prompt_profile": None,
        "reasoning_effort": None,
        "include_rationale": None,
        "use_confidence_gate": None,
    }
    for family in relevant_families:
        lane_lookup[str(family)] = {
            "lane_name": str(family),
            "lane_group": "deterministic",
            "lane_mode": "deterministic",
            "llm_component_scope": "single_family",
            "prompt_profile": None,
            "reasoning_effort": None,
            "include_rationale": None,
            "use_confidence_gate": None,
        }

    iteration_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []

    for plan in iteration_plan:
        iteration_index = int(plan["iteration_index"])
        iteration_seed = int(plan["iteration_seed"])
        sample_name = str(plan["sample_name"])
        sampled_game_ids = tuple(str(game_id) for game_id in plan["sampled_game_ids"])
        sampled_result = _subset_backtest_result(evaluation_result, sampled_game_ids)
        game_candidates = _build_game_candidates(
            sampled_result,
            family_profiles=family_profiles,
            priors=priors,
            core_strategy_families=tuple(core_strategy_families),
            extra_strategy_families=tuple(extra_strategy_families),
        )

        iteration_rows.append(
            {
                "iteration_index": iteration_index,
                "iteration_seed": iteration_seed,
                "sample_name": sample_name,
                "sample_game_count": len(sampled_game_ids),
                "sampled_game_ids_json": json.dumps(list(sampled_game_ids), separators=(",", ":")),
            }
        )

        master_router_trades_df, _master_router_decisions_df = build_master_router_trade_frame(
            sampled_result,
            sample_name=sample_name,
            selection_sample_name=selection_sample_name,
            priors=priors,
            core_strategy_families=tuple(core_strategy_families),
            extra_strategy_families=tuple(extra_strategy_families),
        )
        master_router_summary = _run_lane_portfolio(
            master_router_trades_df,
            sample_name=sample_name,
            lane_name=MASTER_ROUTER_PORTFOLIO,
            portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
            strategy_family_members=tuple([*core_strategy_families, *extra_strategy_families]),
            request=request,
        )
        summary_rows.append(
            {
                "iteration_index": iteration_index,
                "iteration_seed": iteration_seed,
                "sample_name": sample_name,
                "lane_name": MASTER_ROUTER_PORTFOLIO,
                "lane_group": "deterministic",
                "lane_mode": "deterministic",
                "llm_component_scope": "deterministic_router",
                "prompt_profile": None,
                "reasoning_effort": None,
                "include_rationale": None,
                "use_confidence_gate": None,
                **_portfolio_summary_row_to_frame(master_router_summary),
                "llm_call_count": 0,
                "llm_cache_hit_count": 0,
                "llm_input_tokens": 0,
                "llm_cached_input_tokens": 0,
                "llm_output_tokens": 0,
                "llm_reasoning_tokens": 0,
                "llm_estimated_cost_usd": 0.0,
            }
        )

        for family in relevant_families:
            trades_df = _clean_trades_df(sampled_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)))
            if trades_df.empty:
                continue
            baseline_summary = _run_lane_portfolio(
                trades_df,
                sample_name=sample_name,
                lane_name=family,
                portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY,
                strategy_family_members=(family,),
                request=request,
            )
            summary_rows.append(
                {
                    "iteration_index": iteration_index,
                    "iteration_seed": iteration_seed,
                    "sample_name": sample_name,
                    "lane_name": family,
                    "lane_group": "deterministic",
                    "lane_mode": "deterministic",
                    "llm_component_scope": "single_family",
                    "prompt_profile": None,
                    "reasoning_effort": None,
                    "include_rationale": None,
                    "use_confidence_gate": None,
                    **_portfolio_summary_row_to_frame(baseline_summary),
                    "llm_call_count": 0,
                    "llm_cache_hit_count": 0,
                    "llm_input_tokens": 0,
                    "llm_cached_input_tokens": 0,
                    "llm_output_tokens": 0,
                    "llm_reasoning_tokens": 0,
                    "llm_estimated_cost_usd": 0.0,
                }
            )

        for lane in llm_lanes:
            lane_name = str(lane["lane_name"])
            lane_mode = str(lane["lane_mode"])
            llm_component_scope = str(lane["llm_component_scope"])
            selected_trades_df, lane_token_totals, lane_decision_rows = _run_llm_lane_sample(
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
            decision_rows.extend(lane_decision_rows)
            lane_summary = _run_lane_portfolio(
                selected_trades_df,
                sample_name=sample_name,
                lane_name=lane_name,
                portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
                strategy_family_members=tuple([*core_strategy_families, *extra_strategy_families]),
                request=request,
            )
            summary_rows.append(
                {
                    "iteration_index": iteration_index,
                    "iteration_seed": iteration_seed,
                    "sample_name": sample_name,
                    "lane_name": lane_name,
                    "lane_mode": lane_mode,
                    "llm_component_scope": llm_component_scope,
                    "lane_group": str(lane.get("lane_group") or "llm_variant"),
                    "prompt_profile": lane.get("prompt_profile"),
                    "reasoning_effort": lane.get("reasoning_effort"),
                    "include_rationale": lane.get("include_rationale"),
                    "use_confidence_gate": lane.get("use_confidence_gate"),
                    **_portfolio_summary_row_to_frame(lane_summary),
                    **lane_token_totals,
                }
            )

    iteration_df = pd.DataFrame(iteration_rows, columns=LLM_EXPERIMENT_ITERATION_COLUMNS)
    summary_df = pd.DataFrame(summary_rows, columns=LLM_EXPERIMENT_SUMMARY_COLUMNS)
    decisions_df = pd.DataFrame(decision_rows, columns=LLM_EXPERIMENT_DECISION_COLUMNS)

    if summary_df.empty:
        lane_summary_df = pd.DataFrame(columns=LLM_EXPERIMENT_LANE_SUMMARY_COLUMNS)
    else:
        grouped = []
        group_cols = [
            "lane_name",
            "lane_mode",
            "llm_component_scope",
            "lane_group",
            "prompt_profile",
            "reasoning_effort",
            "include_rationale",
            "use_confidence_gate",
        ]
        for group_key, group in summary_df.groupby(group_cols, sort=False, dropna=False):
            (
                lane_name,
                lane_mode,
                llm_component_scope,
                lane_group,
                prompt_profile,
                reasoning_effort,
                include_rationale,
                use_confidence_gate,
            ) = group_key
            ending_bankroll = pd.to_numeric(group["ending_bankroll"], errors="coerce")
            starting_bankroll = pd.to_numeric(group["starting_bankroll"], errors="coerce")
            compounded_return = pd.to_numeric(group["compounded_return"], errors="coerce")
            max_drawdown_pct = pd.to_numeric(group["max_drawdown_pct"], errors="coerce")
            executed_trade_count = pd.to_numeric(group["executed_trade_count"], errors="coerce")
            llm_cost = pd.to_numeric(group["llm_estimated_cost_usd"], errors="coerce").fillna(0.0)
            grouped.append(
                {
                    "lane_name": lane_name,
                    "lane_mode": lane_mode,
                    "llm_component_scope": llm_component_scope,
                    "lane_group": lane_group,
                    "prompt_profile": prompt_profile,
                    "reasoning_effort": reasoning_effort,
                    "include_rationale": include_rationale,
                    "use_confidence_gate": use_confidence_gate,
                    "iteration_count": int(len(group)),
                    "positive_iteration_count": int((ending_bankroll > starting_bankroll).sum()),
                    "positive_iteration_rate": float((ending_bankroll > starting_bankroll).mean()),
                    "mean_ending_bankroll": float(ending_bankroll.mean()),
                    "median_ending_bankroll": float(ending_bankroll.median()),
                    "mean_compounded_return": float(compounded_return.mean()),
                    "mean_max_drawdown_pct": float(max_drawdown_pct.mean()),
                    "mean_executed_trade_count": float(executed_trade_count.mean()),
                    "mean_llm_estimated_cost_usd": float(llm_cost.mean()),
                    "total_llm_estimated_cost_usd": float(llm_cost.sum()),
                    "total_llm_call_count": int(pd.to_numeric(group["llm_call_count"], errors="coerce").fillna(0).sum()),
                    "total_llm_cache_hit_count": int(pd.to_numeric(group["llm_cache_hit_count"], errors="coerce").fillna(0).sum()),
                }
            )
        lane_summary_df = pd.DataFrame(grouped, columns=LLM_EXPERIMENT_LANE_SUMMARY_COLUMNS)

    ranked_lanes = _rank_showdown_lanes(lane_summary_df)
    finalist_rows = ranked_lanes[:_LLM_FINALIST_COUNT]
    finalist_names = [str(row.get("lane_name") or "") for row in finalist_rows]
    showdown_summary_rows: list[dict[str, Any]] = []
    showdown_steps_frames: list[pd.DataFrame] = []
    showdown_decision_rows: list[dict[str, Any]] = []
    showdown_summary_df = pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_SUMMARY_COLUMNS)
    showdown_daily_paths_df = pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_DAILY_PATH_COLUMNS)
    showdown_decisions_df = pd.DataFrame(columns=LLM_EXPERIMENT_DECISION_COLUMNS)
    showdown_seed = int(iteration_plan[0]["iteration_seed"]) if iteration_plan else int(request.holdout_seed)
    showdown_game_count = min(max(1, int(request.portfolio_game_limit)), 100)
    showdown_plan = build_llm_iteration_plan(
        evaluation_result,
        strategy_families=relevant_families,
        iteration_count=1,
        games_per_iteration=showdown_game_count,
        seeds=(showdown_seed,),
        fallback_seed=request.holdout_seed,
    )
    if finalist_names and showdown_plan:
        showdown_game_ids = tuple(str(game_id) for game_id in showdown_plan[0]["sampled_game_ids"])
        showdown_result = _subset_backtest_result(evaluation_result, showdown_game_ids)
        showdown_candidates = _build_game_candidates(
            showdown_result,
            family_profiles=family_profiles,
            priors=priors,
            core_strategy_families=tuple(core_strategy_families),
            extra_strategy_families=tuple(extra_strategy_families),
        )
        finalist_score_lookup = {str(row["lane_name"]): float(row.get("finalist_score") or 0.0) for row in finalist_rows}
        llm_lane_lookup = {str(lane["lane_name"]): lane for lane in llm_lanes}
        for lane_name in finalist_names:
            lane_meta = lane_lookup.get(lane_name) or {}
            if lane_name == MASTER_ROUTER_PORTFOLIO:
                trades_df, _ = build_master_router_trade_frame(
                    showdown_result,
                    sample_name=_LLM_SHOWDOWN_SAMPLE_NAME,
                    selection_sample_name=selection_sample_name,
                    priors=priors,
                    core_strategy_families=tuple(core_strategy_families),
                    extra_strategy_families=tuple(extra_strategy_families),
                )
                showdown_summary, showdown_steps = _run_lane_portfolio_with_steps(
                    trades_df,
                    sample_name=_LLM_SHOWDOWN_SAMPLE_NAME,
                    lane_name=lane_name,
                    portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
                    strategy_family_members=tuple([*core_strategy_families, *extra_strategy_families]),
                    request=request,
                )
                token_totals = {
                    "llm_call_count": 0,
                    "llm_cache_hit_count": 0,
                    "llm_input_tokens": 0,
                    "llm_cached_input_tokens": 0,
                    "llm_output_tokens": 0,
                    "llm_reasoning_tokens": 0,
                    "llm_estimated_cost_usd": 0.0,
                }
            elif lane_name in registry:
                trades_df = _clean_trades_df(
                    showdown_result.trade_frames.get(lane_name, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
                )
                showdown_summary, showdown_steps = _run_lane_portfolio_with_steps(
                    trades_df,
                    sample_name=_LLM_SHOWDOWN_SAMPLE_NAME,
                    lane_name=lane_name,
                    portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY,
                    strategy_family_members=(lane_name,),
                    request=request,
                )
                token_totals = {
                    "llm_call_count": 0,
                    "llm_cache_hit_count": 0,
                    "llm_input_tokens": 0,
                    "llm_cached_input_tokens": 0,
                    "llm_output_tokens": 0,
                    "llm_reasoning_tokens": 0,
                    "llm_estimated_cost_usd": 0.0,
                }
            else:
                lane = llm_lane_lookup.get(lane_name)
                if lane is None:
                    continue
                trades_df, token_totals, lane_decisions = _run_llm_lane_sample(
                    iteration_index=0,
                    iteration_seed=showdown_seed,
                    sample_name=_LLM_SHOWDOWN_SAMPLE_NAME,
                    sampled_game_ids=showdown_game_ids,
                    lane=lane,
                    game_candidates=showdown_candidates,
                    family_profiles=family_profiles,
                    request=request,
                    client=client,
                    budget_state=budget_state,
                    cache_store=cache_store,
                )
                showdown_decision_rows.extend(lane_decisions)
                showdown_summary, showdown_steps = _run_lane_portfolio_with_steps(
                    trades_df,
                    sample_name=_LLM_SHOWDOWN_SAMPLE_NAME,
                    lane_name=lane_name,
                    portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
                    strategy_family_members=tuple([*core_strategy_families, *extra_strategy_families]),
                    request=request,
                )
            showdown_summary_rows.append(
                {
                    "sample_name": _LLM_SHOWDOWN_SAMPLE_NAME,
                    "lane_name": lane_name,
                    "lane_mode": lane_meta.get("lane_mode"),
                    "lane_group": lane_meta.get("lane_group"),
                    "prompt_profile": lane_meta.get("prompt_profile"),
                    "reasoning_effort": lane_meta.get("reasoning_effort"),
                    "include_rationale": lane_meta.get("include_rationale"),
                    "use_confidence_gate": lane_meta.get("use_confidence_gate"),
                    **_portfolio_summary_row_to_frame(showdown_summary),
                    **token_totals,
                    "finalist_score": finalist_score_lookup.get(lane_name, 0.0),
                }
            )
            showdown_steps_frames.append(showdown_steps)
        showdown_summary_df = pd.DataFrame(showdown_summary_rows, columns=LLM_EXPERIMENT_SHOWDOWN_SUMMARY_COLUMNS)
        showdown_steps_df = (
            pd.concat(showdown_steps_frames, ignore_index=True)
            if showdown_steps_frames
            else pd.DataFrame()
        )
        showdown_daily_paths_df = _build_daily_path_frame(showdown_summary_df, showdown_steps_df, lane_lookup=lane_lookup)
        showdown_decisions_df = pd.DataFrame(showdown_decision_rows, columns=LLM_EXPERIMENT_DECISION_COLUMNS)

    total_cost = (
        float(pd.to_numeric(summary_df["llm_estimated_cost_usd"], errors="coerce").fillna(0.0).sum()) if not summary_df.empty else 0.0
    ) + (
        float(pd.to_numeric(showdown_summary_df["llm_estimated_cost_usd"], errors="coerce").fillna(0.0).sum())
        if not showdown_summary_df.empty
        else 0.0
    )
    payload = {
        "enabled": bool(request.llm_enable),
        "status": "ready" if request.llm_enable else "disabled",
        "selection_sample_name": selection_sample_name,
        "evaluation_sample_name": "random_holdout",
        "model": request.llm_model,
        "prompt_version": _LLM_PROMPT_VERSION,
        "iteration_game_count": int(request.llm_iteration_games),
        "iteration_count": int(request.llm_iteration_count),
        "max_budget_usd": float(request.llm_max_budget_usd),
        "lane_catalog": lane_catalog,
        "total_cost_usd": total_cost,
        "iterations": json.loads(iteration_df.to_json(orient="records")) if not iteration_df.empty else [],
        "lane_summary": finalist_rows if finalist_rows else (json.loads(lane_summary_df.to_json(orient="records")) if not lane_summary_df.empty else []),
        "showdown": {
            "sample_name": _LLM_SHOWDOWN_SAMPLE_NAME,
            "seed": showdown_seed,
            "game_count": int(len(showdown_plan[0]["sampled_game_ids"])) if showdown_plan else 0,
            "selection_rule": "top_6_by_log_bankroll_plus_positive_rate_minus_drawdown",
            "finalists": finalist_rows,
            "summary": json.loads(showdown_summary_df.to_json(orient="records")) if not showdown_summary_df.empty else [],
            "daily_paths": json.loads(showdown_daily_paths_df.to_json(orient="records")) if not showdown_daily_paths_df.empty else [],
        },
    }
    frames = {
        "llm_experiment_iterations": iteration_df,
        "llm_experiment_summary": summary_df,
        "llm_experiment_lane_summary": lane_summary_df,
        "llm_experiment_decisions": decisions_df,
        "llm_experiment_showdown_summary": showdown_summary_df,
        "llm_experiment_showdown_daily_paths": showdown_daily_paths_df,
        "llm_experiment_showdown_decisions": showdown_decisions_df,
    }
    return payload, frames


def build_final_option_showdown_frames(
    split_results: dict[str, BacktestResult],
    request: BacktestRunRequest,
    *,
    registry: dict[str, StrategyDefinition],
    core_strategy_families: tuple[str, ...] | list[str],
    extra_strategy_families: tuple[str, ...] | list[str],
    llm_models: tuple[str, ...] | list[str],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    empty_frames = {
        "final_option_showdown_summary": pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_SUMMARY_COLUMNS),
        "final_option_showdown_daily_paths": pd.DataFrame(columns=LLM_EXPERIMENT_SHOWDOWN_DAILY_PATH_COLUMNS),
        "final_option_showdown_decisions": pd.DataFrame(columns=LLM_EXPERIMENT_DECISION_COLUMNS),
    }
    sample_result = split_results.get("full_sample")
    selection_result = split_results.get(DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE) or sample_result
    if sample_result is None or selection_result is None:
        return {"status": "skipped_missing_full_sample"}, empty_frames

    core_members = tuple(str(family) for family in core_strategy_families if str(family) in registry)
    extra_members = tuple(str(family) for family in extra_strategy_families if str(family) in registry)
    relevant_families = tuple(dict.fromkeys([*core_members, *extra_members, "winner_definition"]))
    priors = build_master_router_selection_priors(selection_result, core_strategy_families=core_members)
    family_profiles = _build_family_profiles(
        selection_result,
        registry=registry,
        strategy_families=relevant_families,
        core_strategy_families=core_members,
    )
    game_candidates = _build_game_candidates(
        sample_result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=core_members,
        extra_strategy_families=extra_members,
    )
    ordered_games = (
        sample_result.state_df[["game_id", "game_date"]]
        .drop_duplicates(subset=["game_id"])
        .sort_values(["game_date", "game_id"], kind="mergesort", na_position="last")
        .reset_index(drop=True)
    )
    sampled_game_ids = tuple(str(game_id) for game_id in ordered_games["game_id"].tolist())
    showdown_rows: list[dict[str, Any]] = []
    showdown_steps_frames: list[pd.DataFrame] = []
    showdown_decision_rows: list[dict[str, Any]] = []

    winner_definition_trades_df = _clean_trades_df(
        sample_result.trade_frames.get("winner_definition", pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
    )
    winner_definition_summary, winner_definition_steps = _run_lane_portfolio_with_steps(
        winner_definition_trades_df,
        sample_name=_FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
        lane_name="winner_definition",
        portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY,
        strategy_family_members=("winner_definition",),
        request=request,
    )
    showdown_rows.append(
        {
            "sample_name": _FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
            "lane_name": "winner_definition",
            "lane_mode": "deterministic",
            "lane_group": "deterministic",
            "prompt_profile": None,
            "reasoning_effort": None,
            "include_rationale": None,
            "use_confidence_gate": None,
            **_portfolio_summary_row_to_frame(winner_definition_summary),
            "llm_call_count": 0,
            "llm_cache_hit_count": 0,
            "llm_input_tokens": 0,
            "llm_cached_input_tokens": 0,
            "llm_output_tokens": 0,
            "llm_reasoning_tokens": 0,
            "llm_estimated_cost_usd": 0.0,
            "finalist_score": None,
        }
    )
    showdown_steps_frames.append(winner_definition_steps)

    master_router_trades_df, _master_router_decisions_df = build_master_router_trade_frame(
        sample_result,
        sample_name=_FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
        selection_sample_name=DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE,
        priors=priors,
        core_strategy_families=core_members,
        extra_strategy_families=extra_members,
    )
    master_router_summary, master_router_steps = _run_lane_portfolio_with_steps(
        master_router_trades_df,
        sample_name=_FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
        lane_name=MASTER_ROUTER_PORTFOLIO,
        portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=tuple([*core_members, *extra_members]),
        request=request,
    )
    showdown_rows.append(
        {
            "sample_name": _FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
            "lane_name": MASTER_ROUTER_PORTFOLIO,
            "lane_mode": "deterministic",
            "lane_group": "deterministic",
            "prompt_profile": None,
            "reasoning_effort": None,
            "include_rationale": None,
            "use_confidence_gate": None,
            **_portfolio_summary_row_to_frame(master_router_summary),
            "llm_call_count": 0,
            "llm_cache_hit_count": 0,
            "llm_input_tokens": 0,
            "llm_cached_input_tokens": 0,
            "llm_output_tokens": 0,
            "llm_reasoning_tokens": 0,
            "llm_estimated_cost_usd": 0.0,
            "finalist_score": None,
        }
    )
    showdown_steps_frames.append(master_router_steps)

    resolved_models = tuple(dict.fromkeys(str(model).strip() for model in llm_models if str(model).strip()))
    client = _resolve_openai_client() if resolved_models else None
    cache_store = _load_llm_cache(_resolve_output_dir(request) / _LLM_CACHE_FILENAME)
    budget_state = _LLMBudgetState()
    for model in resolved_models:
        lane = next(
            (item for item in _resolve_llm_lanes(model) if str(item.get("lane_name")) == _FINAL_OPTION_SHOWDOWN_LLM_LANE_NAME),
            None,
        )
        if lane is None:
            continue
        model_request = replace(request, llm_model=model)
        lane_name = f"{model} :: {_FINAL_OPTION_SHOWDOWN_LLM_LANE_NAME}"
        trades_df, token_totals, lane_decisions = _run_llm_lane_sample(
            iteration_index=0,
            iteration_seed=int(request.holdout_seed),
            sample_name=_FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
            sampled_game_ids=sampled_game_ids,
            lane=lane,
            game_candidates=game_candidates,
            family_profiles=family_profiles,
            request=model_request,
            client=client,
            budget_state=budget_state,
            cache_store=cache_store,
        )
        for row in lane_decisions:
            row["lane_name"] = lane_name
        showdown_decision_rows.extend(lane_decisions)
        lane_summary, lane_steps = _run_lane_portfolio_with_steps(
            trades_df,
            sample_name=_FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
            lane_name=lane_name,
            portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
            strategy_family_members=tuple([*core_members, *extra_members]),
            request=model_request,
        )
        showdown_rows.append(
            {
                "sample_name": _FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
                "lane_name": lane_name,
                "lane_mode": lane.get("lane_mode"),
                "lane_group": model,
                "prompt_profile": lane.get("prompt_profile"),
                "reasoning_effort": lane.get("reasoning_effort"),
                "include_rationale": lane.get("include_rationale"),
                "use_confidence_gate": lane.get("use_confidence_gate"),
                **_portfolio_summary_row_to_frame(lane_summary),
                **token_totals,
                "finalist_score": None,
            }
        )
        showdown_steps_frames.append(lane_steps)

    showdown_summary_df = pd.DataFrame(showdown_rows, columns=LLM_EXPERIMENT_SHOWDOWN_SUMMARY_COLUMNS)
    showdown_steps_df = pd.concat(showdown_steps_frames, ignore_index=True) if showdown_steps_frames else pd.DataFrame()
    lane_lookup = {
        "winner_definition": {
            "lane_name": "winner_definition",
            "lane_mode": "deterministic",
            "lane_group": "deterministic",
        },
        MASTER_ROUTER_PORTFOLIO: {
            "lane_name": MASTER_ROUTER_PORTFOLIO,
            "lane_mode": "deterministic",
            "lane_group": "deterministic",
        },
        **{
            f"{model} :: {_FINAL_OPTION_SHOWDOWN_LLM_LANE_NAME}": {
                "lane_name": f"{model} :: {_FINAL_OPTION_SHOWDOWN_LLM_LANE_NAME}",
                "lane_mode": "llm_freedom",
                "lane_group": model,
            }
            for model in resolved_models
        },
    }
    showdown_daily_paths_df = _build_daily_path_frame(showdown_summary_df, showdown_steps_df, lane_lookup=lane_lookup)
    showdown_decisions_df = pd.DataFrame(showdown_decision_rows, columns=LLM_EXPERIMENT_DECISION_COLUMNS)
    payload = {
        "status": "ready",
        "sample_name": _FINAL_OPTION_SHOWDOWN_SAMPLE_NAME,
        "game_count": int(len(sampled_game_ids)),
        "selection_sample_name": DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE,
        "llm_lane_name": _FINAL_OPTION_SHOWDOWN_LLM_LANE_NAME,
        "models": list(resolved_models),
        "summary": json.loads(showdown_summary_df.to_json(orient="records")) if not showdown_summary_df.empty else [],
        "daily_paths": json.loads(showdown_daily_paths_df.to_json(orient="records")) if not showdown_daily_paths_df.empty else [],
    }
    return payload, {
        "final_option_showdown_summary": showdown_summary_df,
        "final_option_showdown_daily_paths": showdown_daily_paths_df,
        "final_option_showdown_decisions": showdown_decisions_df,
    }


__all__ = [
    "LLM_EXPERIMENT_DECISION_COLUMNS",
    "LLM_EXPERIMENT_ITERATION_COLUMNS",
    "LLM_EXPERIMENT_LANE_SUMMARY_COLUMNS",
    "LLM_EXPERIMENT_SUMMARY_COLUMNS",
    "build_final_option_showdown_frames",
    "build_llm_experiment_frames",
    "build_llm_iteration_plan",
    "estimate_llm_usage_cost",
    "normalize_llm_selected_candidate_ids",
]
