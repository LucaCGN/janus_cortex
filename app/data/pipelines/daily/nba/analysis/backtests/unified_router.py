from __future__ import annotations

import json
from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS
from app.data.pipelines.daily.nba.analysis.backtests.llm_experiment import (
    _LLMBudgetState,
    _build_game_candidates,
    _run_llm_lane_sample,
)
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    MASTER_ROUTER_DECISION_COLUMNS,
    MASTER_ROUTER_TRADE_COLUMNS,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.contracts import BacktestRunRequest


UNIFIED_ROUTER_PORTFOLIO = "unified_strategy_router_v1"
UNIFIED_ROUTER_DECISION_COLUMNS = (
    *MASTER_ROUTER_DECISION_COLUMNS,
    "llm_lane_name",
    "llm_evaluated_flag",
    "llm_decision_status",
    "llm_selected_candidate_ids_json",
    "llm_selected_strategy_families_json",
    "llm_confidence",
    "default_is_weak_flag",
    "final_source",
    "final_selection_reason",
    "final_selected_strategy_families_json",
    "final_selected_trade_count",
)
UNIFIED_ROUTER_TRADE_COLUMNS = (
    *MASTER_ROUTER_TRADE_COLUMNS,
    "unified_router_source",
    "unified_router_default_confidence",
    "unified_router_llm_confidence",
    "unified_router_llm_lane_name",
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _group_trade_frames_by_game_id(trades_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if trades_df.empty or "game_id" not in trades_df.columns:
        return {}
    work = trades_df.copy()
    work["game_id"] = work["game_id"].astype(str)
    grouped: dict[str, pd.DataFrame] = {}
    for game_id, game_df in work.groupby("game_id", sort=False):
        grouped[str(game_id)] = game_df.copy().reset_index(drop=True)
    return grouped


def _json_list_of_unique_families(trades_df: pd.DataFrame) -> str:
    if trades_df.empty or "source_strategy_family" not in trades_df.columns:
        return json.dumps([], sort_keys=True)
    families = sorted(
        {
            str(value)
            for value in trades_df["source_strategy_family"].dropna().astype(str).tolist()
            if str(value).strip()
        }
    )
    return json.dumps(families, sort_keys=True)


def _trade_signature_set(trades_df: pd.DataFrame) -> set[tuple[str, str, str, str]]:
    if trades_df.empty:
        return set()
    work = trades_df.copy()
    if "game_id" not in work.columns:
        work["game_id"] = ""
    if "team_side" not in work.columns:
        work["team_side"] = ""
    if "source_strategy_family" not in work.columns:
        work["source_strategy_family"] = ""
    if "entry_state_index" not in work.columns:
        work["entry_state_index"] = ""
    return {
        (
            str(row.get("game_id") or ""),
            str(row.get("team_side") or ""),
            str(row.get("source_strategy_family") or ""),
            str(row.get("entry_state_index") or ""),
        )
        for row in work.to_dict(orient="records")
    }


def resolve_unified_router_game_selection(
    *,
    deterministic_decision: dict[str, Any],
    llm_decision: dict[str, Any] | None,
    weak_confidence_threshold: float,
    llm_accept_confidence: float,
    skip_weak_when_llm_empty: bool = False,
    skip_weak_when_llm_low_confidence: bool = False,
) -> dict[str, Any]:
    default_confidence = _safe_float(deterministic_decision.get("selected_confidence"))
    default_has_core = bool(deterministic_decision.get("selected_core_family"))
    default_is_weak = (not default_has_core) or default_confidence is None or default_confidence < float(weak_confidence_threshold)

    if not default_is_weak:
        return {
            "default_is_weak_flag": False,
            "llm_evaluated_flag": False,
            "final_source": "deterministic_default",
            "final_selection_reason": "deterministic_confident",
        }

    llm_selected_count = int(llm_decision.get("selected_candidate_count") or 0) if llm_decision else 0
    llm_confidence = _safe_float(llm_decision.get("llm_confidence")) if llm_decision else None
    llm_evaluated_flag = llm_decision is not None
    llm_status = str(llm_decision.get("decision_status") or "") if llm_decision else ""
    llm_accepted = (
        llm_selected_count > 0
        and llm_confidence is not None
        and llm_confidence >= float(llm_accept_confidence)
    )
    if llm_accepted:
        return {
            "default_is_weak_flag": True,
            "llm_evaluated_flag": llm_evaluated_flag,
            "final_source": "llm_override",
            "final_selection_reason": "llm_override_on_weak_default",
        }

    if skip_weak_when_llm_empty and llm_selected_count <= 0:
        return {
            "default_is_weak_flag": True,
            "llm_evaluated_flag": llm_evaluated_flag,
            "final_source": "skip_weak_game",
            "final_selection_reason": "weak_default_llm_skip",
        }

    if skip_weak_when_llm_low_confidence and llm_evaluated_flag and llm_status not in {"client_unavailable", "budget_guard"}:
        return {
            "default_is_weak_flag": True,
            "llm_evaluated_flag": llm_evaluated_flag,
            "final_source": "skip_weak_game",
            "final_selection_reason": "weak_default_llm_low_confidence",
        }

    if default_has_core:
        return {
            "default_is_weak_flag": True,
            "llm_evaluated_flag": llm_evaluated_flag,
            "final_source": "deterministic_fallback",
            "final_selection_reason": "llm_not_stronger_than_weak_default",
        }

    return {
        "default_is_weak_flag": True,
        "llm_evaluated_flag": llm_evaluated_flag,
        "final_source": "skip_weak_game",
        "final_selection_reason": "no_default_and_no_llm_selection",
    }


def build_unified_router_trade_frame(
    sample_result: Any,
    *,
    sample_name: str,
    selection_sample_name: str,
    priors: dict[str, dict[str, Any]],
    family_profiles: dict[str, dict[str, Any]],
    core_strategy_families: tuple[str, ...] | list[str],
    extra_strategy_families: tuple[str, ...] | list[str],
    llm_lane: dict[str, Any],
    request: BacktestRunRequest,
    client: Any,
    budget_state: _LLMBudgetState,
    cache_store: Any,
    historical_team_context_lookup: dict[str, dict[str, Any]] | None = None,
    extra_selection_mode: str = "same_side_top1",
    min_selected_core_confidence: float = 0.0,
    min_core_confidence_for_extras: float = 0.60,
    weak_confidence_threshold: float = 0.60,
    llm_accept_confidence: float = 0.60,
    skip_weak_when_llm_empty: bool = False,
    skip_weak_when_llm_low_confidence: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    deterministic_trades_df, deterministic_decisions_df = build_master_router_trade_frame(
        sample_result,
        sample_name=sample_name,
        selection_sample_name=selection_sample_name,
        priors=priors,
        core_strategy_families=core_strategy_families,
        extra_strategy_families=extra_strategy_families,
        extra_selection_mode=extra_selection_mode,
        min_selected_core_confidence=min_selected_core_confidence,
        min_core_confidence_for_extras=min_core_confidence_for_extras,
    )
    deterministic_trade_lookup = _group_trade_frames_by_game_id(deterministic_trades_df)
    deterministic_decision_lookup = {
        str(record["game_id"]): record
        for record in deterministic_decisions_df.to_dict(orient="records")
    }
    ordered_game_ids = [str(record.get("game_id") or "") for record in deterministic_decisions_df.to_dict(orient="records")]

    weak_game_ids = [
        game_id
        for game_id in ordered_game_ids
        if resolve_unified_router_game_selection(
            deterministic_decision=deterministic_decision_lookup.get(game_id) or {},
            llm_decision=None,
            weak_confidence_threshold=weak_confidence_threshold,
            llm_accept_confidence=llm_accept_confidence,
            skip_weak_when_llm_empty=skip_weak_when_llm_empty,
            skip_weak_when_llm_low_confidence=skip_weak_when_llm_low_confidence,
        )["default_is_weak_flag"]
    ]

    llm_trades_df = pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    llm_decisions_lookup: dict[str, dict[str, Any]] = {}
    llm_token_totals = {
        "llm_call_count": 0,
        "llm_cache_hit_count": 0,
        "llm_input_tokens": 0,
        "llm_cached_input_tokens": 0,
        "llm_output_tokens": 0,
        "llm_reasoning_tokens": 0,
        "llm_estimated_cost_usd": 0.0,
    }
    if weak_game_ids:
        game_candidates = _build_game_candidates(
            sample_result,
            family_profiles=family_profiles,
            priors=priors,
            core_strategy_families=core_strategy_families,
            extra_strategy_families=extra_strategy_families,
            historical_team_context_lookup=historical_team_context_lookup,
        )
        llm_trades_df, llm_token_totals, llm_decision_rows = _run_llm_lane_sample(
            iteration_index=0,
            iteration_seed=int(request.holdout_seed),
            sample_name=sample_name,
            sampled_game_ids=tuple(weak_game_ids),
            lane=llm_lane,
            game_candidates=game_candidates,
            family_profiles=family_profiles,
            request=request,
            client=client,
            budget_state=budget_state,
            cache_store=cache_store,
        )
        llm_decisions_lookup = {
            str(record["game_id"]): record
            for record in llm_decision_rows
        }
    llm_trade_lookup = _group_trade_frames_by_game_id(llm_trades_df)

    unified_trade_records: list[dict[str, Any]] = []
    unified_decision_rows: list[dict[str, Any]] = []
    for game_id in ordered_game_ids:
        deterministic_decision = deterministic_decision_lookup.get(game_id) or {}
        llm_decision = llm_decisions_lookup.get(game_id)
        resolution = resolve_unified_router_game_selection(
            deterministic_decision=deterministic_decision,
            llm_decision=llm_decision,
            weak_confidence_threshold=weak_confidence_threshold,
            llm_accept_confidence=llm_accept_confidence,
            skip_weak_when_llm_empty=skip_weak_when_llm_empty,
            skip_weak_when_llm_low_confidence=skip_weak_when_llm_low_confidence,
        )

        final_source = str(resolution["final_source"])
        if final_source == "llm_override":
            chosen_trades_df = llm_trade_lookup.get(game_id, pd.DataFrame(columns=UNIFIED_ROUTER_TRADE_COLUMNS))
        elif final_source in {"deterministic_default", "deterministic_fallback"}:
            chosen_trades_df = deterministic_trade_lookup.get(game_id, pd.DataFrame(columns=UNIFIED_ROUTER_TRADE_COLUMNS))
        else:
            chosen_trades_df = pd.DataFrame(columns=UNIFIED_ROUTER_TRADE_COLUMNS)
        deterministic_game_trades_df = deterministic_trade_lookup.get(game_id, pd.DataFrame(columns=UNIFIED_ROUTER_TRADE_COLUMNS))
        if final_source == "llm_override" and _trade_signature_set(chosen_trades_df) == _trade_signature_set(deterministic_game_trades_df):
            final_source = "llm_confirmed_weak_default"
            resolution["final_selection_reason"] = "llm_confirmed_weak_default"

        default_confidence = _safe_float(deterministic_decision.get("selected_confidence"))
        llm_confidence = _safe_float(llm_decision.get("llm_confidence")) if llm_decision else None
        llm_selected_ids_json = json.dumps([], separators=(",", ":"))
        llm_selected_families_json = json.dumps([], separators=(",", ":"))
        llm_status = None
        if llm_decision:
            llm_selected_ids_json = str(llm_decision.get("selected_candidate_ids_json") or json.dumps([], separators=(",", ":")))
            llm_selected_families_json = str(llm_decision.get("selected_strategy_families_json") or json.dumps([], separators=(",", ":")))
            llm_status = llm_decision.get("decision_status")

        if not chosen_trades_df.empty:
            for record in chosen_trades_df.to_dict(orient="records"):
                decorated = dict(record)
                decorated["unified_router_source"] = final_source
                decorated["unified_router_default_confidence"] = default_confidence
                decorated["unified_router_llm_confidence"] = llm_confidence
                decorated["unified_router_llm_lane_name"] = str(llm_lane.get("lane_name") or "")
                unified_trade_records.append({column: decorated.get(column) for column in UNIFIED_ROUTER_TRADE_COLUMNS})

        unified_decision_rows.append(
            {
                **{column: deterministic_decision.get(column) for column in MASTER_ROUTER_DECISION_COLUMNS},
                "llm_lane_name": str(llm_lane.get("lane_name") or ""),
                "llm_evaluated_flag": bool(resolution["llm_evaluated_flag"]),
                "llm_decision_status": llm_status,
                "llm_selected_candidate_ids_json": llm_selected_ids_json,
                "llm_selected_strategy_families_json": llm_selected_families_json,
                "llm_confidence": llm_confidence,
                "default_is_weak_flag": bool(resolution["default_is_weak_flag"]),
                "final_source": final_source,
                "final_selection_reason": str(resolution["final_selection_reason"]),
                "final_selected_strategy_families_json": _json_list_of_unique_families(chosen_trades_df),
                "final_selected_trade_count": int(len(chosen_trades_df)),
            }
        )

    unified_trades_df = pd.DataFrame(unified_trade_records, columns=UNIFIED_ROUTER_TRADE_COLUMNS)
    unified_decisions_df = pd.DataFrame(unified_decision_rows, columns=UNIFIED_ROUTER_DECISION_COLUMNS)
    return unified_trades_df, unified_decisions_df, llm_token_totals


__all__ = [
    "UNIFIED_ROUTER_DECISION_COLUMNS",
    "UNIFIED_ROUTER_PORTFOLIO",
    "UNIFIED_ROUTER_TRADE_COLUMNS",
    "build_unified_router_trade_frame",
    "resolve_unified_router_game_selection",
]
