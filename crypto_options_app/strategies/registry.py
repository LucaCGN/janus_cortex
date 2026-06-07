from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect_read_only, default_db_path
from crypto_options_app.strategies.schema import StrategySpec, strategy_from_dict, validate_strategy_spec


STARTING_STRATEGY_IDS = (
    "s_tier_outcome_consensus_cashout_v1",
    "hedger_ratio_replication_v1",
    "grid_buyer_band_rebound_v1",
    "indicator_confirmed_outcome_v1",
    "buying_ahead_pre_event_v1",
)


def all_strategy_specs() -> tuple[StrategySpec, ...]:
    return _load_strategy_specs(default_db_path())


def strategy_registry() -> dict[str, StrategySpec]:
    return {spec.strategy_id: spec for spec in all_strategy_specs()}


def get_strategy(strategy_id: str) -> StrategySpec:
    return strategy_registry()[strategy_id]


def starting_strategy_ids() -> tuple[str, ...]:
    return STARTING_STRATEGY_IDS


def validate_registry() -> dict[str, tuple[str, ...]]:
    failures: dict[str, tuple[str, ...]] = {}
    for spec in all_strategy_specs():
        result = validate_strategy_spec(spec)
        if not result.valid:
            failures[spec.strategy_id] = result.errors
    return failures


@lru_cache(maxsize=8)
def _load_strategy_specs(db_path: Path) -> tuple[StrategySpec, ...]:
    if not db_path.exists() and not str(db_path).endswith("crypto_options_data.sqlite"):
        return ()
    with connect_read_only(db_path) as conn:
        rows = conn.execute(
            """
            SELECT strategy_id, strategy_version, enabled, spec_json
            FROM strategy_specs
            ORDER BY strategy_id, strategy_version
            """
        ).fetchall()

    specs: list[StrategySpec] = []
    seen: set[str] = set()
    for row in rows:
        payload = _load_spec_payload(dict(row))
        if not payload:
            continue
        strategy_id = str(payload.get("strategy_id") or row["strategy_id"])
        if strategy_id in seen:
            continue
        seen.add(strategy_id)
        specs.append(strategy_from_dict(payload))
    specs = _with_source_overlays(specs)
    return tuple(specs)


def _load_spec_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("spec_json")
    if not raw:
        return {}
    payload = json.loads(str(raw))
    payload["strategy_id"] = str(payload.get("strategy_id") or row.get("strategy_id"))
    payload["strategy_version"] = str(payload.get("strategy_version") or row.get("strategy_version"))
    payload["enabled"] = bool(row.get("enabled")) if "enabled" in row else bool(payload.get("enabled"))
    return payload


def _with_source_overlays(specs: list[StrategySpec]) -> list[StrategySpec]:
    by_id = {spec.strategy_id: spec for spec in specs}
    tail_base = by_id.get("master_hedge_grid_floor_tail_reversal_probe_v3")
    neutral_base = by_id.get("master_hedge_grid_floor_neutral_rebound_v3") or tail_base
    profile_splus_base = by_id.get("profile_splus_hedger_follow_hold_60s_v3")
    profile_floor_base = by_id.get("master_hedge_grid_floor_profile_follow_v1")
    overlays: list[StrategySpec] = []
    if tail_base is not None:
        overlays.extend(
            (
                _tail_floor_probe_v4(tail_base),
                _tail_floor_probe_v5(tail_base),
                _tail_floor_probe_v6(tail_base),
                _tail_floor_probe_v7(tail_base),
            )
        )
    if neutral_base is not None:
        overlays.append(_low_range_no_edge_control_v1(neutral_base))
        overlays.append(_master_hedge_grid_floor_seed_builder_neutral_v1(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v1(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v2(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v3(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v4(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v5(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v6(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v7(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v8(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v9(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v10(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v11(neutral_base))
        overlays.append(_master_hedge_grid_floor_paired_seed_builder_v12(neutral_base))
    if profile_floor_base is not None:
        overlays.append(_master_hedge_grid_floor_profile_follow_v2(profile_floor_base))
        overlays.append(_master_hedge_grid_floor_profile_follow_v3(profile_floor_base))
        overlays.append(_master_hedge_grid_floor_seed_builder_v1(profile_floor_base))
    if profile_splus_base is not None:
        overlays.append(_profile_splus_hedger_follow_hold_v4(profile_splus_base))
        overlays.append(_profile_splus_hedger_follow_hold_v5(profile_splus_base))
        overlays.append(_profile_splus_hedger_follow_hold_v6(profile_splus_base))
        overlays.append(_profile_splus_hedger_follow_hold_v7(profile_splus_base))
        overlays.append(_profile_splus_hedger_follow_hold_v8(profile_splus_base))
        overlays.append(_profile_splus_hedger_follow_hold_v9(profile_splus_base))
        overlays.append(_profile_splus_hedger_follow_hold_v10(profile_splus_base))
    for overlay in overlays:
        if overlay.strategy_id not in by_id:
            specs.append(overlay)
            by_id[overlay.strategy_id] = overlay
    return specs


def _tail_floor_probe_v4(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Profile-price-aware tail-touch hedge-floor sibling that keeps v3's floor-preserving mechanics and requires cleaner spread/forward-edge evidence.",
            "shadow_decision_mode": "tail_touch_profile_price_reference_only",
            "option_path_max_spread": 0.05,
            "option_path_min_forward_cashout_edge": 0.015,
            "option_entry_price_max": 0.16,
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_tail_reversal_probe_v4",
        strategy_version="v4",
        metadata=metadata,
    )


def _tail_floor_probe_v5(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Forward-edge-clean tail-touch hedge-floor sibling that keeps v4's profile-price and floor-preserving mechanics but only counts windows with stronger forward-bid support.",
            "shadow_decision_mode": "tail_touch_forward_edge_reference_only",
            "option_path_max_spread": 0.04,
            "option_path_min_forward_cashout_edge": 0.03,
            "option_entry_price_max": 0.14,
            "hedge_floor_min_grid_viability": 0.46,
            "hedge_floor_min_tail_reversal_probability": 0.24,
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_tail_reversal_probe_v5",
        strategy_version="v5",
        metadata=metadata,
    )


def _tail_floor_probe_v6(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Protected-surplus tail-touch hedge-floor sibling that keeps v5's clean forward-edge selector but disables tail optionality until protected surplus is actually funded.",
            "shadow_decision_mode": "tail_touch_protected_surplus_reference_only",
            "promotion_candidate_lane": False,
            "retired_from_promotion_lane": True,
            "superseded_by_strategy_id": "master_hedge_grid_floor_tail_reversal_probe_v5",
            "option_path_max_spread": 0.04,
            "option_path_min_forward_cashout_edge": 0.03,
            "option_entry_price_max": 0.14,
            "hedge_floor_min_grid_viability": 0.46,
            "hedge_floor_min_tail_reversal_probability": 0.24,
            "hedge_floor_tail_mode": "protected_only",
            "hedge_floor_require_surplus_for_tails": True,
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_tail_reversal_probe_v6",
        strategy_version="v6",
        metadata=metadata,
    )


def _tail_floor_probe_v7(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Subgroup-follow protected-surplus tail-touch hedge-floor sibling that keeps v6's clean forward-edge and protected-tail behavior but only executes when the S+ hedger subgroup is fresh and directionally coherent.",
            "shadow_decision_mode": "tail_touch_subgroup_follow_reference_only",
            "promotion_candidate_lane": False,
            "retired_from_promotion_lane": True,
            "superseded_by_strategy_id": "master_hedge_grid_floor_tail_reversal_probe_v6",
            "option_path_max_spread": 0.04,
            "option_path_min_forward_cashout_edge": 0.03,
            "option_entry_price_max": 0.14,
            "hedge_floor_min_grid_viability": 0.46,
            "hedge_floor_min_tail_reversal_probability": 0.24,
            "hedge_floor_tail_mode": "protected_only",
            "hedge_floor_require_surplus_for_tails": True,
            "profile_dependency_mode": "required",
            "profile_direction_mode": "follow",
            "profile_direction_threshold": 0.12,
            "profile_distribution_group_kind": "by_grade_style",
            "profile_distribution_group_label": "S+ / hedger",
            "profile_distribution_min_components": 2,
            "profile_reconstructed_pair_sum_min": 0.9,
            "profile_reconstructed_pair_sum_max": 1.1,
            "profile_distribution_max_source_age_seconds": 45,
            "profile_distribution_max_cost_share_gap": 0.22,
            "profile_distribution_max_cost_count_gap": 0.25,
            "profile_distribution_blocked_coverage_warnings": ["profile_distribution_source_stale"],
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_tail_reversal_probe_v7",
        strategy_version="v7",
        metadata=metadata,
    )


def _low_range_no_edge_control_v1(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Diagnostic no-edge control lane for low-range windows that keep failing rebound/forward-edge gates. This records baseline opportunity cost and must never be promoted to live.",
            "shadow_decision_mode": "low_range_no_edge_control",
            "promotion_candidate_lane": False,
            "diagnostic_control_lane": True,
            "retired_from_promotion_lane": True,
            "no_live_promotion": True,
            "option_path_required": True,
            "option_path_min_snapshot_count": 3,
            "option_path_min_level_crossing_count": None,
            "option_path_min_rebound_direction_flip_count": None,
            "option_path_min_strong_rebound_touch_count": None,
            "option_path_min_near_50c_sample_count": None,
            "option_path_min_extreme_sample_count": None,
            "option_path_min_trade_print_count": None,
            "option_path_min_avg_rolling_30s_range": None,
            "option_path_min_avg_rolling_60s_range": None,
            "option_path_min_max_rolling_60s_range": None,
            "option_path_min_abs_pair_depth_pressure": None,
            "option_path_max_pair_sum_range": 1,
            "option_path_max_spread": 0.06,
            "option_path_min_forward_cashout_edge": None,
            "option_entry_price_min": None,
            "option_entry_price_max": None,
            "low_range_control_max_avg_rolling_60s_range": 0.03,
            "low_range_control_max_forward_cashout_edge": 0.01,
            "hedge_floor_min_inversion_intensity": 0.0,
            "hedge_floor_min_grid_viability": 0.0,
            "hedge_floor_min_tail_reversal_probability": 0.0,
            "hedge_floor_min_current_floor": -10.0,
            "grid_price_band_required": False,
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_low_range_no_edge_control_v1",
        strategy_version="v1",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_seed_builder_neutral_v1(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "No-lookahead C-only seed-phase lane for the master hedge-grid template. It tests whether option-path churn, spread, and reconstructed pair prices can build the initial hedge floor before profile-follow logic is allowed to steer inventory.",
            "shadow_decision_mode": "seed_floor_builder_option_churn_no_lookahead",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.35,
            "option_entry_price_max": 0.62,
            "hedge_floor_mode": "seed_floor_builder",
            "hedge_floor_order_side_mode": "scenario_outcome",
            "hedge_floor_min_current_floor": -0.20,
            "hedge_floor_min_inversion_intensity": 0.52,
            "hedge_floor_min_grid_viability": 0.58,
            "hedge_floor_min_tail_reversal_probability": 0.18,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_seed_builder_neutral_v1",
        strategy_version="v1",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v1(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "No-lookahead C-only paired seed lane for the master hedge-grid template. It requires a high-inversion, low-spread window where buying equal shares of Up and Down can immediately create or preserve a non-negative guaranteed floor before any directional/profile steering is considered.",
            "shadow_decision_mode": "paired_seed_equal_share_floor_no_lookahead",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 0.99,
            "hedge_floor_paired_seed_min_floor": 0.01,
            "hedge_floor_min_current_floor": 0.01,
            "hedge_floor_min_inversion_intensity": 0.52,
            "hedge_floor_min_grid_viability": 0.58,
            "hedge_floor_min_tail_reversal_probability": 0.18,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "protected_only",
            "grid_price_band_required": True,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v1",
        strategy_version="v1",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v2(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Small-negative-floor paired seed lane for the master hedge-grid template. It models the realistic phase where both sides are seeded at a slight spread cost, but only when inversion intensity and grid viability are strong enough to justify harvesting toward a protected floor.",
            "shadow_decision_mode": "paired_seed_small_negative_floor_harvest_no_lookahead",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.04,
            "hedge_floor_paired_seed_min_floor": -0.08,
            "hedge_floor_min_current_floor": -0.08,
            "hedge_floor_min_inversion_intensity": 0.58,
            "hedge_floor_min_grid_viability": 0.62,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v2",
        strategy_version="v2",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v3(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Paired seed plus inversion scalp replay lane. It keeps the small-negative-floor paired seed allowance, then requires evidence that captured Up/Down pair-path movement can complete small extra buy/sell scalp cycles that move the seed toward a protected floor.",
            "shadow_decision_mode": "paired_seed_inversion_scalp_path_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.04,
            "hedge_floor_paired_seed_min_floor": -0.08,
            "hedge_floor_min_current_floor": -0.08,
            "hedge_floor_min_inversion_intensity": 0.58,
            "hedge_floor_min_grid_viability": 0.62,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.03,
            "paired_seed_scalp_target": 0.03,
            "paired_seed_scalp_notional_usd": 0.25,
            "paired_seed_scalp_max_open_per_side": 1,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v3",
        strategy_version="v3",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v4(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Conservative paired seed scalp lane. It waits for a deeper extra-lot discount and exits on a smaller rebound to reduce open-position drag while still testing the same volatility-harvest-to-floor mechanic.",
            "shadow_decision_mode": "paired_seed_deep_dip_quick_scalp_path_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.04,
            "hedge_floor_paired_seed_min_floor": -0.08,
            "hedge_floor_min_current_floor": -0.08,
            "hedge_floor_min_inversion_intensity": 0.58,
            "hedge_floor_min_grid_viability": 0.62,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.05,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.15,
            "paired_seed_scalp_max_open_per_side": 1,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v4",
        strategy_version="v4",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v5(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Closed-cycle paired seed scalp lane. It keeps the conservative V4 dip/target economics but only allows extra scalp entries early in the validation horizon and requires denser paired path evidence, so promotion samples represent closed volatility harvest into a positive hedge floor rather than open extra exposure.",
            "shadow_decision_mode": "paired_seed_closed_cycle_scalp_path_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.02,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.16,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.04,
            "hedge_floor_paired_seed_min_floor": -0.08,
            "hedge_floor_min_current_floor": -0.08,
            "hedge_floor_min_inversion_intensity": 0.60,
            "hedge_floor_min_grid_viability": 0.64,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.05,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.12,
            "paired_seed_scalp_max_open_per_side": 1,
            "paired_seed_scalp_min_path_snapshots": 2,
            "paired_seed_scalp_entry_window_fraction": 0.67,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v5",
        strategy_version="v5",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v6(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Entry-displacement sibling of V5. It keeps closed-cycle paired seed scalp economics, but only enters when Up/Down asks are meaningfully displaced from 50/50, because V5 evidence showed near-symmetric pair entries rarely created a complete extra scalp cycle or positive final floor.",
            "shadow_decision_mode": "paired_seed_closed_cycle_entry_gap_scalp_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["C"],
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.02,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.16,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.02,
            "hedge_floor_paired_seed_min_floor": -0.04,
            "hedge_floor_paired_seed_min_abs_entry_ask_gap": 0.05,
            "hedge_floor_paired_seed_max_abs_entry_ask_gap": 0.40,
            "hedge_floor_min_current_floor": -0.04,
            "hedge_floor_min_inversion_intensity": 0.60,
            "hedge_floor_min_grid_viability": 0.64,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.05,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.12,
            "paired_seed_scalp_max_open_per_side": 1,
            "paired_seed_scalp_min_path_snapshots": 2,
            "paired_seed_scalp_entry_window_fraction": 0.67,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("profile_dependency_mode", None)
    metadata.pop("profile_direction_threshold", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v6",
        strategy_version="v6",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v7(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Profile-coherent entry-displacement sibling of V6. It keeps closed-cycle paired seed scalp economics, but requires S+ hedger profile distribution context so concentrated option-path events cannot promote without aligned profile pressure and reconstructed profile pair sanity.",
            "shadow_decision_mode": "paired_seed_profile_coherent_entry_gap_scalp_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["B", "C"],
            "profile_dependency_mode": "required",
            "profile_direction_mode": "follow",
            "profile_direction_threshold": 0.12,
            "profile_distribution_group_kind": "by_grade_style",
            "profile_distribution_group_label": "S+ / hedger",
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "profile_distribution_max_top_profile_cost_share": 0.70,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.02,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.16,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.02,
            "hedge_floor_paired_seed_min_floor": -0.04,
            "hedge_floor_paired_seed_min_abs_entry_ask_gap": 0.05,
            "hedge_floor_paired_seed_max_abs_entry_ask_gap": 0.40,
            "hedge_floor_min_current_floor": -0.04,
            "hedge_floor_min_inversion_intensity": 0.60,
            "hedge_floor_min_grid_viability": 0.64,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.05,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.12,
            "paired_seed_scalp_max_open_per_side": 1,
            "paired_seed_scalp_min_path_snapshots": 2,
            "paired_seed_scalp_entry_window_fraction": 0.67,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    signal_inputs = dict(base.signal_inputs)
    signal_inputs["profile"] = ["band_rebound", "hedge_proportion", "outcome_expectation"]
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v7",
        strategy_version="v7",
        state="shadow_test",
        signal_inputs=signal_inputs,
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v8(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Profile-balanced sibling of V6. A paired seed/grid needs volatility more than a winner pick, so this variant requires aggregate top-profile pressure to stay close to 50/50 while option-path inversion and entry-displacement gates remain active.",
            "shadow_decision_mode": "paired_seed_profile_balanced_entry_gap_scalp_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["B", "C"],
            "profile_dependency_mode": "required",
            "profile_pressure_mode": "balanced_required",
            "profile_direction_threshold": 0.12,
            "profile_distribution_min_profile_count": 8,
            "profile_distribution_max_cost_share_gap": 0.50,
            "profile_distribution_max_cost_count_gap": 0.50,
            "profile_distribution_max_top_profile_cost_share": 0.75,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.02,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.16,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.02,
            "hedge_floor_paired_seed_min_floor": -0.04,
            "hedge_floor_paired_seed_min_abs_entry_ask_gap": 0.05,
            "hedge_floor_paired_seed_max_abs_entry_ask_gap": 0.40,
            "hedge_floor_min_current_floor": -0.04,
            "hedge_floor_min_inversion_intensity": 0.60,
            "hedge_floor_min_grid_viability": 0.64,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.05,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.12,
            "paired_seed_scalp_max_open_per_side": 1,
            "paired_seed_scalp_min_path_snapshots": 2,
            "paired_seed_scalp_entry_window_fraction": 0.67,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    signal_inputs = dict(base.signal_inputs)
    signal_inputs["profile"] = ["band_rebound", "hedge_proportion", "outcome_expectation"]
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v8",
        strategy_version="v8",
        state="shadow_test",
        signal_inputs=signal_inputs,
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v9(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "High-churn profile-bucket sibling of V6/V8. Profile balance is retained as confidence context instead of a hard gate; execution depends on no-lookahead option-path churn, near-50 activity, pair-depth pressure, and closed-cycle paired scalp economics.",
            "shadow_decision_mode": "paired_seed_profile_bucket_high_churn_scalp_no_lookahead_economics",
            "promotion_candidate_lane": True,
            "seed_phase_candidate_lane": True,
            "source_blocks": ["B", "C"],
            "profile_dependency_mode": "optional_confidence",
            "profile_pressure_mode": "balanced_required",
            "profile_direction_threshold": 0.12,
            "profile_distribution_min_profile_count": 8,
            "profile_distribution_max_cost_share_gap": 0.55,
            "profile_distribution_max_cost_count_gap": 0.55,
            "profile_distribution_max_top_profile_cost_share": 0.80,
            "option_path_required": True,
            "option_path_min_snapshot_count": 8,
            "option_path_min_avg_rolling_60s_range": 0.03,
            "option_path_min_rebound_direction_flip_count": 3,
            "option_path_min_level_crossing_count": 6,
            "option_path_min_near_50c_sample_count": 8,
            "option_path_min_abs_pair_depth_pressure": 0.08,
            "option_path_max_pair_sum_range": 0.12,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.12,
            "option_entry_price_max": 0.72,
            "hedge_floor_mode": "paired_seed_equal_share_floor",
            "hedge_floor_seed_allocation_mode": "equal_shares",
            "hedge_floor_paired_seed_required": True,
            "hedge_floor_paired_seed_max_pair_sum": 1.02,
            "hedge_floor_paired_seed_min_floor": -0.04,
            "hedge_floor_paired_seed_max_abs_entry_ask_gap": 0.40,
            "hedge_floor_min_current_floor": -0.04,
            "hedge_floor_min_inversion_intensity": 0.62,
            "hedge_floor_min_grid_viability": 0.66,
            "hedge_floor_min_tail_reversal_probability": 0.20,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "paired_seed_scalp_simulation_required": True,
            "paired_seed_scalp_buy_drop": 0.04,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.10,
            "paired_seed_scalp_max_open_per_side": 1,
            "paired_seed_scalp_min_path_snapshots": 3,
            "paired_seed_scalp_entry_window_fraction": 0.60,
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    metadata.pop("hedge_floor_paired_seed_min_abs_entry_ask_gap", None)
    signal_inputs = dict(base.signal_inputs)
    signal_inputs["profile"] = ["band_rebound", "hedge_proportion", "outcome_expectation"]
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v9",
        strategy_version="v9",
        state="shadow_test",
        signal_inputs=signal_inputs,
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v10(base: StrategySpec) -> StrategySpec:
    v9 = _master_hedge_grid_floor_paired_seed_builder_v9(base)
    metadata = dict(v9.metadata)
    metadata.update(
        {
            "version_notes": "Replay-granularity sibling of V9. Keeps the high-churn/profile-bucket selector and floor-preserving economics, but requires two paired path snapshots because the current captured disjoint replay rows usually have two usable paired snapshots rather than three.",
            "shadow_decision_mode": "paired_seed_profile_bucket_high_churn_two_snapshot_scalp_no_lookahead_economics",
            "paired_seed_scalp_min_path_snapshots": 2,
            "paired_seed_scalp_entry_window_fraction": 0.67,
        }
    )
    return replace(
        v9,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v10",
        strategy_version="v10",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v11(base: StrategySpec) -> StrategySpec:
    v10 = _master_hedge_grid_floor_paired_seed_builder_v10(base)
    metadata = dict(v10.metadata)
    metadata.update(
        {
            "version_notes": "Quality-gated V10 sibling. V10 showed completed cycles only when entry asks were meaningfully displaced and pair-depth pressure was strong; V11 requires that entry displacement and stronger absolute pair-depth pressure while retaining optional profile bucket context and closed-cycle floor gates.",
            "shadow_decision_mode": "paired_seed_profile_bucket_quality_entry_scalp_no_lookahead_economics",
            "hedge_floor_paired_seed_min_abs_entry_ask_gap": 0.12,
            "hedge_floor_paired_seed_max_abs_entry_ask_gap": 0.40,
            "option_path_min_abs_pair_depth_pressure": 0.12,
        }
    )
    return replace(
        v10,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v11",
        strategy_version="v11",
        metadata=metadata,
    )


def _master_hedge_grid_floor_paired_seed_builder_v12(base: StrategySpec) -> StrategySpec:
    v11 = _master_hedge_grid_floor_paired_seed_builder_v11(base)
    metadata = dict(v11.metadata)
    metadata.update(
        {
            "version_notes": "Closed-cycle positive-floor sibling of V11. This keeps V11's high-churn and quality-entry gates, but requires a no-lookahead paired-scalp path preflight to find at least one completed cycle, no stranded scalp inventory, and a positive guaranteed floor before a row can count as simulated strategy evidence.",
            "shadow_decision_mode": "paired_seed_closed_cycle_positive_floor_preflight_no_lookahead_economics",
            "paired_seed_scalp_preflight_required": True,
            "paired_seed_scalp_require_completed_cycle": True,
            "paired_seed_scalp_require_floor_positive": True,
            "paired_seed_scalp_require_no_open_positions": True,
            "paired_seed_scalp_min_floor_during_path": -0.04,
        }
    )
    return replace(
        v11,
        strategy_id="master_hedge_grid_floor_paired_seed_builder_v12",
        strategy_version="v12",
        metadata=metadata,
    )


def _master_hedge_grid_floor_profile_follow_v2(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "S+ hedger profile-follow hedge-grid sibling that tests the master template directly: use profile consensus for side selection, require option-path churn for volatility harvesting, and keep hedge-floor/order-preservation gates active before any promotion discussion.",
            "profile_direction_threshold": 0.18,
            "profile_distribution_group_kind": "by_grade_style",
            "profile_distribution_group_label": "S+ / hedger",
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "seed_both_then_preserve_floor",
            "hedge_floor_min_grid_viability": 0.46,
            "hedge_floor_min_tail_reversal_probability": 0.18,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "protected_only",
            "grid_price_band_required": True,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_profile_follow_v2",
        strategy_version="v2",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_profile_follow_v3(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Protected-floor S+ hedger profile-follow sibling. This keeps v2's profile and high-inversion requirements, but only treats a high-inversion window as tradable after the reconstructed seed state already has a positive guaranteed floor; this prevents 49c-51c churn entries from counting as promotion-quality floor-grid evidence.",
            "profile_direction_threshold": 0.18,
            "profile_distribution_group_kind": "by_grade_style",
            "profile_distribution_group_label": "S+ / hedger",
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.10,
            "option_entry_price_max": 0.70,
            "hedge_floor_mode": "protected_floor_follow",
            "hedge_floor_min_current_floor": 0.03,
            "hedge_floor_min_grid_viability": 0.46,
            "hedge_floor_min_tail_reversal_probability": 0.18,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "protected_only",
            "grid_price_band_required": True,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_profile_follow_v3",
        strategy_version="v3",
        state="shadow_test",
        metadata=metadata,
    )


def _master_hedge_grid_floor_seed_builder_v1(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "No-lookahead seed-phase lane for the master hedge-grid template. This is not a protected-floor follow lane: it allows slightly negative reconstructed floors only in high-inversion, low-spread, S+ hedger-coherent windows so replay can test whether scalping can build the first guaranteed floor before v3 takes over.",
            "shadow_decision_mode": "seed_floor_builder_no_lookahead",
            "profile_direction_threshold": 0.18,
            "profile_distribution_group_kind": "by_grade_style",
            "profile_distribution_group_label": "S+ / hedger",
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_min_rebound_direction_flip_count": 2,
            "option_path_min_level_crossing_count": 3,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.35,
            "option_entry_price_max": 0.62,
            "hedge_floor_mode": "seed_floor_builder",
            "hedge_floor_min_current_floor": -0.20,
            "hedge_floor_min_grid_viability": 0.58,
            "hedge_floor_min_tail_reversal_probability": 0.18,
            "hedge_floor_require_surplus_for_tails": True,
            "hedge_floor_tail_mode": "seed_only_no_tail",
            "grid_price_band_required": True,
            "seed_phase_candidate_lane": True,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    metadata.pop("option_path_min_forward_cashout_edge", None)
    return replace(
        base,
        strategy_id="master_hedge_grid_floor_seed_builder_v1",
        strategy_version="v1",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v4(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Strict S+ hedger follow lane that keeps v3's high-conviction profile threshold and adds replay-proven option-path activity, profile-coherence, and entry-price guards after extended shadow replay losses.",
            "profile_direction_threshold": 0.24,
            "profile_reconstructed_pair_sum_min": 0.9,
            "profile_reconstructed_pair_sum_max": 1.1,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.34,
            "profile_distribution_max_cost_count_gap": 0.33,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.05,
            "option_entry_price_max": 0.62,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v4",
        strategy_version="v4",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v5(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Moderate-entry S+ hedger follow sibling that keeps v4's high-conviction side alignment and option activity, blocks the observed losing 72c+ entry band, and widens profile reconstruction/noisy cost divergence tolerance so replay can produce economic samples instead of all-blocking.",
            "profile_direction_threshold": 0.24,
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.018,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.05,
            "option_entry_price_max": 0.7,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v5",
        strategy_version="v5",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v6(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Range-expanded S+ hedger follow sibling that keeps v5's no-lookahead profile side alignment and 70c entry ceiling, but lowers the option-path activity floor to test whether quieter profile-consensus windows can increase sample count without reopening the observed high-entry loss pattern.",
            "profile_direction_threshold": 0.24,
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 2,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "option_path_required": True,
            "option_path_min_snapshot_count": 6,
            "option_path_min_avg_rolling_60s_range": 0.01,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.05,
            "option_entry_price_max": 0.7,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v6",
        strategy_version="v6",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v7(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "High-confidence S+ hedger follow sibling for profile-consensus windows where v6 blocks mostly on the 70c entry ceiling. This tests the higher-entry band only when subgroup pressure is materially stronger and profile reconstruction is cleaner; it must pass strict replay/live-shadow before any live promotion.",
            "profile_direction_threshold": 0.4,
            "profile_reconstructed_pair_sum_min": 0.8,
            "profile_reconstructed_pair_sum_max": 1.1,
            "profile_distribution_min_components": 4,
            "profile_distribution_max_cost_share_gap": 0.32,
            "profile_distribution_max_cost_count_gap": 0.36,
            "profile_distribution_max_top_profile_cost_share": 0.58,
            "option_path_required": True,
            "option_path_min_snapshot_count": 8,
            "option_path_min_avg_rolling_60s_range": 0.005,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.5,
            "option_entry_price_max": 0.85,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v7",
        strategy_version="v7",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v8(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "High-entry S+ hedger follow sibling that keeps v7's stronger directional pressure and entry band but restores v6's cost/count divergence tolerance. It tests whether v7 was too narrow while still rejecting balanced or thin profile rows.",
            "profile_direction_threshold": 0.4,
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 4,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "profile_distribution_max_top_profile_cost_share": 0.70,
            "option_path_required": True,
            "option_path_min_snapshot_count": 8,
            "option_path_min_avg_rolling_60s_range": 0.005,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.5,
            "option_entry_price_max": 0.85,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v8",
        strategy_version="v8",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v9(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Liquidation-aware S+ hedger follow sibling. It keeps v8's executable B/C profile and path gates, but forward-mark profit is not promotion-ready unless immediate liquidation is non-negative and spread drag stays small.",
            "profile_direction_threshold": 0.4,
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 4,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "profile_distribution_max_top_profile_cost_share": 0.70,
            "option_path_required": True,
            "option_path_min_snapshot_count": 8,
            "option_path_min_avg_rolling_60s_range": 0.005,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.03,
            "option_entry_price_min": 0.5,
            "option_entry_price_max": 0.85,
            "shadow_economics_require_liquidation_non_negative": True,
            "shadow_economics_min_liquidation_pnl_usd": 0.0,
            "shadow_economics_max_spread_drag_usd": 0.03,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v9",
        strategy_version="v9",
        state="shadow_test",
        metadata=metadata,
    )


def _profile_splus_hedger_follow_hold_v10(base: StrategySpec) -> StrategySpec:
    metadata = dict(base.metadata)
    metadata.update(
        {
            "version_notes": "Bounded-spread-drag S+ hedger follow sibling. It preserves v9's B/C profile and path gates, but allows a tiny immediate bid-exit loss so spread cost can be paid only when 60s forward-mark edge is materially positive.",
            "profile_direction_threshold": 0.4,
            "profile_reconstructed_pair_sum_min": 0.75,
            "profile_reconstructed_pair_sum_max": 1.15,
            "profile_distribution_min_components": 4,
            "profile_distribution_max_cost_share_gap": 0.45,
            "profile_distribution_max_cost_count_gap": 0.48,
            "profile_distribution_max_top_profile_cost_share": 0.70,
            "option_path_required": True,
            "option_path_min_snapshot_count": 8,
            "option_path_min_avg_rolling_60s_range": 0.005,
            "option_path_max_pair_sum_range": 0.18,
            "option_path_max_spread": 0.02,
            "option_entry_price_min": 0.5,
            "option_entry_price_max": 0.80,
            "shadow_economics_min_liquidation_pnl_usd": -0.02,
            "shadow_economics_max_spread_drag_usd": 0.02,
            "promotion_candidate_lane": True,
            "source_blocks": ["B", "C"],
        }
    )
    return replace(
        base,
        strategy_id="profile_splus_hedger_follow_hold_60s_v10",
        strategy_version="v10",
        state="shadow_test",
        metadata=metadata,
    )
