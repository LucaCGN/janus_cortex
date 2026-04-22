from __future__ import annotations

from typing import Any

import pandas as pd


VNEXT_UNIFIED_CONTROLLER = "controller_vnext_unified_v1"
VNEXT_MASTER_CONTROLLER = "controller_vnext_deterministic_v1"
DEFAULT_VNEXT_STOP_MAP = {"winner_definition": 0.05}
DEFAULT_VNEXT_PROFILE = {
    "family_caps": {
        "winner_definition": 0.30,
        "inversion": 0.22,
        "underdog_liftoff": 0.18,
        "q1_repricing": 0.10,
        "q4_clutch": 0.12,
    },
    "family_scales": {
        "winner_definition": 1.00,
        "inversion": 0.84,
        "underdog_liftoff": 0.72,
        "q1_repricing": 0.45,
        "q4_clutch": 0.55,
    },
    "source_scales": {
        "deterministic_default": 1.00,
        "deterministic_fallback": 0.82,
        "llm_override": 0.86,
        "llm_confirmed_weak_default": 0.92,
        "skip_weak_game": 0.0,
        "core_selected": 1.00,
        "extra_sleeve": 0.58,
    },
    "sleeve_cap_fraction": 0.12,
    "llm_review_min_confidence": 0.46,
    "weak_confidence_threshold": 0.64,
    "llm_accept_confidence": 0.60,
}


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_state_lookup(state_df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    if state_df.empty:
        return {}
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


def apply_stop_overlay(
    trades_df: pd.DataFrame,
    *,
    state_lookup: dict[tuple[str, str], pd.DataFrame],
    stop_map: dict[str, float] | None,
) -> pd.DataFrame:
    if trades_df.empty or not stop_map:
        return trades_df.copy()

    overlay_rows: list[dict[str, Any]] = []
    for record in trades_df.to_dict(orient="records"):
        family = str(record.get("source_strategy_family") or record.get("strategy_family") or "")
        stop_amount = _safe_float((stop_map or {}).get(family))
        if stop_amount is None:
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
            (group["state_index"] > entry_state_index)
            & (group["state_index"] <= exit_state_index)
        ].copy()
        stop_price = max(0.01, float(record.get("entry_price") or 0.0) - float(stop_amount))
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
        updated["exit_rule"] = f"{updated.get('exit_rule') or ''} + overlay_stop_{int(round(stop_amount * 100))}c".strip()
        overlay_rows.append(updated)
    return pd.DataFrame(overlay_rows, columns=trades_df.columns)


def _confidence_bucket_scale(confidence: float | None) -> float:
    resolved = _safe_float(confidence)
    if resolved is None:
        return 0.60
    if resolved >= 0.74:
        return 1.00
    if resolved >= 0.68:
        return 0.92
    if resolved >= 0.62:
        return 0.84
    if resolved >= 0.56:
        return 0.72
    return 0.58


def decorate_trade_frame_with_vnext_sizing(
    trades_df: pd.DataFrame,
    *,
    profile: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if trades_df.empty:
        work = trades_df.copy()
        work["position_fraction_scale_override"] = pd.Series(dtype=float)
        work["position_fraction_cap_override"] = pd.Series(dtype=float)
        return work

    resolved_profile = DEFAULT_VNEXT_PROFILE | dict(profile or {})
    family_caps = dict(DEFAULT_VNEXT_PROFILE["family_caps"]) | dict(resolved_profile.get("family_caps") or {})
    family_scales = dict(DEFAULT_VNEXT_PROFILE["family_scales"]) | dict(resolved_profile.get("family_scales") or {})
    source_scales = dict(DEFAULT_VNEXT_PROFILE["source_scales"]) | dict(resolved_profile.get("source_scales") or {})
    sleeve_cap_fraction = max(0.0, min(1.0, _safe_float(resolved_profile.get("sleeve_cap_fraction")) or 0.12))

    rows: list[dict[str, Any]] = []
    for record in trades_df.to_dict(orient="records"):
        updated = dict(record)
        family = str(updated.get("source_strategy_family") or updated.get("strategy_family") or "")
        router_source = str(updated.get("unified_router_source") or updated.get("master_router_role") or "")
        default_confidence = _safe_float(updated.get("unified_router_default_confidence"))
        llm_confidence = _safe_float(updated.get("unified_router_llm_confidence"))
        master_confidence = _safe_float(updated.get("master_router_confidence"))
        confidence = llm_confidence if router_source == "llm_override" and llm_confidence is not None else (
            default_confidence if default_confidence is not None else master_confidence
        )
        role_scale = float(source_scales.get(router_source, 1.0 if router_source != "extra_sleeve" else 0.58))
        family_scale = float(family_scales.get(family, 0.70))
        confidence_scale = _confidence_bucket_scale(confidence)
        scale_override = max(0.0, min(1.0, role_scale * family_scale * confidence_scale))
        cap_override = max(0.0, min(1.0, float(family_caps.get(family, 0.18))))
        if router_source == "extra_sleeve":
            cap_override = min(cap_override, sleeve_cap_fraction)
        updated["position_fraction_scale_override"] = scale_override
        updated["position_fraction_cap_override"] = cap_override
        rows.append(updated)
    return pd.DataFrame(rows, columns=[*trades_df.columns, "position_fraction_scale_override", "position_fraction_cap_override"])


__all__ = [
    "DEFAULT_VNEXT_PROFILE",
    "DEFAULT_VNEXT_STOP_MAP",
    "VNEXT_MASTER_CONTROLLER",
    "VNEXT_UNIFIED_CONTROLLER",
    "apply_stop_overlay",
    "build_state_lookup",
    "decorate_trade_frame_with_vnext_sizing",
]
