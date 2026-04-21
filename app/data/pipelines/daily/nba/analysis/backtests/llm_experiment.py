from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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

_LLM_MODEL_INPUT_PRICE_PER_1M = 0.75
_LLM_MODEL_CACHED_INPUT_PRICE_PER_1M = 0.075
_LLM_MODEL_OUTPUT_PRICE_PER_1M = 4.50
_LLM_CACHE_FILENAME = "llm_router_cache.json"
_LLM_PROMPT_VERSION = "v1"
_LLM_TRACE_ROW_LIMIT = 4
_LLM_FALLBACK_CONFIDENCE = 0.51
_LLM_BASELINES = (
    "winner_definition",
    "inversion",
    "underdog_liftoff",
    "favorite_panic_fade_v1",
    "q1_repricing",
    "halftime_q3_repricing_v1",
    "q4_clutch",
)
_LLM_LANES = (
    {
        "lane_name": "llm_strategy_eval_v1",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "b_only",
        "allowed_roles": ("core",),
    },
    {
        "lane_name": "llm_ingame_eval_v1",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "c_only",
        "allowed_roles": ("extra",),
    },
    {
        "lane_name": "llm_hybrid_restrained_v1",
        "lane_mode": "llm_restrained",
        "llm_component_scope": "bc_restrained",
        "allowed_roles": ("core", "extra"),
    },
    {
        "lane_name": "llm_hybrid_freedom_v1",
        "lane_mode": "llm_freedom",
        "llm_component_scope": "bc_freedom",
        "allowed_roles": ("core", "extra"),
    },
)


class _LLMSelectionResponse(BaseModel):
    selected_candidate_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=120)


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
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    uncached_input_tokens = max(0, int(input_tokens) - int(cached_input_tokens))
    return (
        (uncached_input_tokens / 1_000_000.0) * _LLM_MODEL_INPUT_PRICE_PER_1M
        + (max(0, int(cached_input_tokens)) / 1_000_000.0) * _LLM_MODEL_CACHED_INPUT_PRICE_PER_1M
        + (max(0, int(output_tokens)) / 1_000_000.0) * _LLM_MODEL_OUTPUT_PRICE_PER_1M
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
        return [str(candidate["candidate_id"]) for candidate in ordered]

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


def _rough_prompt_cost(system_prompt: str, user_payload: dict[str, Any]) -> float:
    payload_text = json.dumps(user_payload, separators=(",", ":"), ensure_ascii=True)
    estimated_input_tokens = max(1, int((len(system_prompt) + len(payload_text)) / 4))
    return estimate_llm_usage_cost(input_tokens=estimated_input_tokens, output_tokens=96)


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
    game_id: str,
    opening_band: str,
    available_candidates: list[dict[str, Any]],
    family_profiles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    families_in_scope = sorted({str(candidate["strategy_family"]) for candidate in available_candidates})
    profile_rows = []
    for family in families_in_scope:
        profile = family_profiles.get(family) or {}
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
        candidate_rows.append(
            {
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
                "trace": candidate["trace_rows"],
                "meta": candidate["entry_metadata"],
            }
        )
    return {
        "v": _LLM_PROMPT_VERSION,
        "lane": lane_name,
        "mode": lane_mode,
        "scope": llm_component_scope,
        "game_id": game_id,
        "opening_band": opening_band,
        "profiles": profile_rows,
        "candidates": candidate_rows,
    }


def _build_llm_system_prompt(*, lane_mode: str, llm_component_scope: str) -> str:
    constraint = "Select no more than one core and one extra candidate." if lane_mode != "llm_freedom" else "You may select multiple candidates if they reinforce the same side."
    return (
        "You are evaluating NBA live-trading candidate strategies. "
        "Use only the JSON provided. Optimize for compounded bankroll growth under drawdown control. "
        "Prefer skip when support is weak or contexts are contradictory. "
        "Never invent new strategy ids. Never select both sides of the same game. "
        f"Lane scope is {llm_component_scope}. {constraint} "
        "Return only minified JSON with the selected candidate ids, a confidence score, and a rationale under 80 characters."
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


def _maybe_call_llm(
    *,
    client: OpenAI | None,
    model: str,
    lane_name: str,
    lane_mode: str,
    llm_component_scope: str,
    allowed_roles: tuple[str, ...] | list[str],
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
    )
    if not available_candidates:
        return _LLMCallResult([], 0.0, "No candidates available.", False, 0, 0, 0, 0, 0.0, "no_candidates")

    prompt_payload = _build_llm_prompt_payload(
        lane_name=lane_name,
        lane_mode=lane_mode,
        llm_component_scope=llm_component_scope,
        game_id=str(available_candidates[0].get("game_id") or ""),
        opening_band=str(available_candidates[0].get("opening_band") or ""),
        available_candidates=available_candidates,
        family_profiles=family_profiles,
    )
    system_prompt = _build_llm_system_prompt(lane_mode=lane_mode, llm_component_scope=llm_component_scope)
    cache_key_payload = {
        "model": model,
        "prompt_version": _LLM_PROMPT_VERSION,
        "lane_name": lane_name,
        "lane_mode": lane_mode,
        "llm_component_scope": llm_component_scope,
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

    estimated_cost = _rough_prompt_cost(system_prompt, prompt_payload)
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

    try:
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt_payload, separators=(",", ":"), ensure_ascii=True)},
            ],
            text_format=_LLMSelectionResponse,
            reasoning={"effort": "low"},
            max_output_tokens=240,
            store=False,
        )
        parsed = getattr(response, "output_parsed", None) or _LLMSelectionResponse()
        usage = _usage_from_response(response)
        actual_cost = estimate_llm_usage_cost(
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
        )
        budget_state.spent_usd += actual_cost
        normalized_ids = normalize_llm_selected_candidate_ids(
            parsed.selected_candidate_ids,
            available_candidates,
            lane_mode=lane_mode,
            allowed_roles=allowed_roles,
        )
        cache_store.payload[cache_key] = {
            "selected_candidate_ids": list(normalized_ids),
            "confidence": float(parsed.confidence),
            "rationale": str(parsed.rationale),
        }
        _persist_llm_cache(cache_store)
        return _LLMCallResult(
            normalized_ids,
            float(parsed.confidence),
            str(parsed.rationale),
            False,
            usage["input_tokens"],
            usage["cached_input_tokens"],
            usage["output_tokens"],
            usage["reasoning_tokens"],
            actual_cost,
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
    )
    return summary


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
                "lane_mode": "deterministic",
                "llm_component_scope": "deterministic_router",
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
                    "lane_mode": "deterministic",
                    "llm_component_scope": "single_family",
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

        for lane in _LLM_LANES:
            lane_name = str(lane["lane_name"])
            lane_mode = str(lane["lane_mode"])
            llm_component_scope = str(lane["llm_component_scope"])
            allowed_roles = tuple(str(role) for role in lane["allowed_roles"])
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
                for candidate in game_candidates.get(game_id, [])
            }

            for game_id in sampled_game_ids:
                all_candidates = game_candidates.get(game_id, [])
                available_candidates = [
                    candidate
                    for candidate in all_candidates
                    if str(candidate.get("candidate_role")) in set(allowed_roles)
                ]
                call_result = _maybe_call_llm(
                    client=client,
                    model=request.llm_model,
                    lane_name=lane_name,
                    lane_mode=lane_mode,
                    llm_component_scope=llm_component_scope,
                    allowed_roles=allowed_roles,
                    available_candidates=available_candidates,
                    family_profiles=family_profiles,
                    max_budget_usd=float(request.llm_max_budget_usd),
                    budget_state=budget_state,
                    cache_store=cache_store,
                )
                lane_selected_ids.extend(call_result.selected_candidate_ids)
                lane_token_totals["llm_call_count"] += 0 if call_result.decision_status in {"cache_hit", "no_candidates", "budget_guard", "client_unavailable", "error"} else 1
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

            selected_trades_df = _selected_trade_frame(lane_selected_ids, candidate_lookup)
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
        for (lane_name, lane_mode, llm_component_scope), group in summary_df.groupby(["lane_name", "lane_mode", "llm_component_scope"], sort=False, dropna=False):
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
        "total_cost_usd": float(pd.to_numeric(summary_df["llm_estimated_cost_usd"], errors="coerce").fillna(0.0).sum()) if not summary_df.empty else 0.0,
        "iterations": json.loads(iteration_df.to_json(orient="records")) if not iteration_df.empty else [],
        "lane_summary": json.loads(lane_summary_df.to_json(orient="records")) if not lane_summary_df.empty else [],
    }
    frames = {
        "llm_experiment_iterations": iteration_df,
        "llm_experiment_summary": summary_df,
        "llm_experiment_lane_summary": lane_summary_df,
        "llm_experiment_decisions": decisions_df,
    }
    return payload, frames


__all__ = [
    "LLM_EXPERIMENT_DECISION_COLUMNS",
    "LLM_EXPERIMENT_ITERATION_COLUMNS",
    "LLM_EXPERIMENT_LANE_SUMMARY_COLUMNS",
    "LLM_EXPERIMENT_SUMMARY_COLUMNS",
    "build_llm_experiment_frames",
    "build_llm_iteration_plan",
    "estimate_llm_usage_cost",
    "normalize_llm_selected_candidate_ids",
]
