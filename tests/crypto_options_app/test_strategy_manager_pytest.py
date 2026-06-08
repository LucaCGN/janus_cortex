from __future__ import annotations

import pytest

from crypto_options_app.strategies.candidates import build_structural_candidate
from crypto_options_app.strategies.readiness import (
    evaluate_pulse_readiness,
    evaluate_replay_readiness,
    transition_strategy_state,
)
from crypto_options_app.strategies.registry import (
    all_strategy_specs,
    get_strategy,
    starting_strategy_ids,
    strategy_registry,
    validate_registry,
)
from crypto_options_app.strategies.schema import StrategySpec, validate_strategy_spec


EXPECTED_STRATEGIES = {
    "s_tier_outcome_consensus_cashout_v1",
    "hedger_ratio_replication_v1",
    "hedger_ratio_replication_v2",
    "hedger_ratio_replication_v3",
    "grid_buyer_band_rebound_v1",
    "grid_buyer_band_rebound_v2",
    "indicator_confirmed_outcome_v1",
    "buying_ahead_pre_event_v1",
    "s_tier_outcome_hold_to_settlement_v1",
    "a_fallback_outcome_probe_v1",
    "profile_hedge_scalping_v1",
    "profile_hedge_scalping_v2",
    "profile_hedge_scalping_v3",
    "event_context_outcome_v1",
    "event_context_outcome_v2",
    "event_context_profile_confirmed_v1",
    "volatility_spread_scalping_probe_v1",
    "hedger_ratio_replication_v4",
    "profile_hedge_scalping_v4",
    "grid_band_rebound_v3",
    "master_hedge_grid_floor_profile_follow_v1",
    "master_hedge_grid_floor_profile_follow_v2",
    "master_hedge_grid_floor_profile_follow_v3",
    "master_hedge_grid_floor_seed_builder_v1",
    "master_hedge_grid_floor_seed_builder_neutral_v1",
    "master_hedge_grid_floor_paired_seed_builder_v1",
    "master_hedge_grid_floor_paired_seed_builder_v2",
    "master_hedge_grid_floor_paired_seed_builder_v3",
    "master_hedge_grid_floor_paired_seed_builder_v4",
    "master_hedge_grid_floor_paired_seed_builder_v5",
    "master_hedge_grid_floor_paired_seed_builder_v6",
    "master_hedge_grid_floor_paired_seed_builder_v7",
    "master_hedge_grid_floor_paired_seed_builder_v8",
    "master_hedge_grid_floor_paired_seed_builder_v9",
    "master_hedge_grid_floor_paired_seed_builder_v10",
    "master_hedge_grid_floor_paired_seed_builder_v11",
    "master_hedge_grid_floor_paired_seed_builder_v12",
    "master_hedge_grid_floor_neutral_rebound_v1",
    "master_hedge_grid_floor_neutral_rebound_v2",
    "master_hedge_grid_floor_neutral_rebound_v3",
    "crypto_direction_option_context_hold_60s_v1",
    "profile_distribution_consensus_hold_60s_v1",
    "profile_distribution_contrarian_hold_60s_v1",
    "profile_distribution_contrarian_scalp_v1",
    "profile_distribution_extreme_reversal_grid_v1",
    "crypto_observer_contrarian_option_hold_60s_v1",
    "profile_splusplus_hedger_follow_hold_60s_v1",
    "profile_splusplus_hedger_follow_coherent_hold_60s_v2",
    "profile_splusplus_hedger_follow_coherent_hold_60s_v3",
    "profile_splusplus_hedger_fade_hold_60s_v1",
    "profile_splus_hedger_follow_hold_60s_v1",
    "profile_splus_hedger_follow_hold_60s_v2",
    "profile_splus_hedger_follow_hold_60s_v3",
    "profile_splus_hedger_follow_hold_60s_v4",
    "profile_splus_hedger_follow_hold_60s_v5",
    "profile_splus_hedger_follow_hold_60s_v6",
    "profile_splus_hedger_follow_hold_60s_v7",
    "profile_splus_hedger_follow_hold_60s_v8",
    "profile_splus_hedger_follow_hold_60s_v9",
    "profile_splus_hedger_follow_hold_60s_v10",
    "profile_splus_hedger_follow_coherent_hold_60s_v1",
    "profile_splus_hedger_follow_near50_hold_60s_v1",
    "profile_splus_hedger_follow_path_active_hold_60s_v1",
    "profile_splus_hedger_follow_rebound_hold_60s_v1",
    "profile_splus_hedger_fade_hold_60s_v1",
    "profile_unknown_style_fade_hold_60s_v1",
    "profile_outcome_predictor_follow_hold_60s_v1",
    "profile_outcome_predictor_follow_hold_60s_v2",
    "profile_outcome_predictor_follow_hold_60s_v3",
    "profile_outcome_predictor_follow_coherent_hold_60s_v4",
    "profile_outcome_predictor_follow_coherent_hold_60s_v5",
    "profile_outcome_predictor_fade_hold_60s_v1",
    "profile_splus_hedger_follow_scalp_v1",
    "profile_splus_hedger_follow_scalp_v2",
    "profile_splus_hedger_follow_coherent_scalp_v1",
    "profile_splus_hedger_follow_path_active_scalp_v1",
    "profile_splusplus_hedger_follow_scalp_v1",
    "profile_splusplus_hedger_follow_coherent_scalp_v1",
    "profile_splusplus_hedger_follow_concentration_probe_scalp_v1",
    "profile_splusplus_hedger_fade_scalp_v1",
    "profile_splus_hedger_fade_scalp_v1",
    "profile_splusplus_extreme_reversal_grid_v1",
    "profile_splus_hedger_follow_grid_v2",
    "profile_splus_hedger_follow_coherent_grid_v4",
    "profile_splus_hedger_follow_rebound_grid_v3",
    "profile_splus_unknown_reversal_grid_v1",
    "crypto_observer_follow_option_scalp_v1",
    "crypto_observer_fade_option_scalp_v1",
    "crypto_option_multiframe_alignment_hold_60s_v2",
    "crypto_option_conflict_neutral_start_hold_60s_v3",
    "crypto_pivot_rebound_reclaim_hold_60s_v4",
    "crypto_option_avoid_trade_gate_v5",
    "crypto_option_pair_sum_reference_hold_60s_v5",
    "crypto_option_regime_reference_scalp_v5",
    "option_tail_reversal_hold_60s_v1",
    "option_tail_reversal_hold_60s_v2",
    "option_tail_reversal_hold_60s_v3",
    "option_tail_reversal_scalp_v3",
    "profile_option_tail_reversal_contrarian_hold_60s_v1",
    "profile_option_tail_reversal_contrarian_hold_60s_v2",
    "option_low_band_rebound_hold_60s_v1",
    "option_liquidity_micro_scalp_v1",
    "option_liquidity_micro_scalp_v2",
    "option_liquidity_micro_scalp_v3",
    "option_liquidity_micro_scalp_v4",
    "option_liquidity_micro_scalp_v5",
    "master_hedge_grid_floor_tail_reversal_probe_v2",
    "master_hedge_grid_floor_tail_reversal_probe_v3",
    "master_hedge_grid_floor_tail_reversal_probe_v4",
    "master_hedge_grid_floor_tail_reversal_probe_v5",
    "master_hedge_grid_floor_tail_reversal_probe_v6",
    "master_hedge_grid_floor_tail_reversal_probe_v7",
    "master_hedge_grid_floor_low_range_no_edge_control_v1",
}


def test_strategy_registry_contains_all_10_candidates_and_starting_5_pytest() -> None:
    registry = strategy_registry()

    assert set(registry) == EXPECTED_STRATEGIES
    assert starting_strategy_ids() == (
        "s_tier_outcome_consensus_cashout_v1",
        "hedger_ratio_replication_v1",
        "grid_buyer_band_rebound_v1",
        "indicator_confirmed_outcome_v1",
        "buying_ahead_pre_event_v1",
    )
    assert validate_registry() == {}
    assert all(spec.enabled is False for spec in all_strategy_specs())


def test_kept_low_volume_lanes_have_v2_variants_pytest() -> None:
    assert get_strategy("hedger_ratio_replication_v2").strategy_version == "v2"
    assert get_strategy("profile_hedge_scalping_v2").strategy_version == "v2"
    assert get_strategy("event_context_outcome_v2").strategy_version == "v2"
    assert get_strategy("grid_buyer_band_rebound_v2").strategy_version == "v2"
    assert get_strategy("hedger_ratio_replication_v2").metadata["volume_adjustment"] == "profile_quote_fallback_to_verified_event_context"
    assert get_strategy("profile_hedge_scalping_v2").metadata["volume_adjustment"] == "profile_quote_fallback_to_verified_event_context"
    assert get_strategy("event_context_outcome_v2").metadata["volume_adjustment"] == "FAK_order_type_with_wider_structural_slippage"
    assert get_strategy("grid_buyer_band_rebound_v2").metadata["volume_adjustment"] == "cross_event_grid_pressure_carry_forward"


def test_rotation4_profile_pressure_carry_forward_variants_pytest() -> None:
    assert get_strategy("hedger_ratio_replication_v3").strategy_version == "v3"
    assert get_strategy("profile_hedge_scalping_v3").strategy_version == "v3"
    assert get_strategy("event_context_profile_confirmed_v1").metadata["volume_adjustment"] == "event_context_quote_profile_pressure_confirmation"
    assert get_strategy("hedger_ratio_replication_v3").metadata["volume_adjustment"] == "cross_event_profile_pressure_carry_forward"
    assert get_strategy("profile_hedge_scalping_v3").metadata["volume_adjustment"] == "cross_event_profile_pressure_carry_forward"


def test_crypto_option_confluence_shadow_variants_are_read_only_candidates_pytest() -> None:
    assert get_strategy("crypto_option_multiframe_alignment_hold_60s_v2").strategy_version == "v2"
    assert get_strategy("crypto_option_conflict_neutral_start_hold_60s_v3").metadata["shadow_decision_mode"] == "neutral_start_or_skip"
    assert get_strategy("crypto_pivot_rebound_reclaim_hold_60s_v4").metadata["option_band_mode"] == "pivot_rebound_reclaim"
    assert get_strategy("crypto_option_avoid_trade_gate_v5").metadata["freshness_blocker_sensitive"] is True
    assert get_strategy("crypto_option_avoid_trade_gate_v5").metadata["shadow_decision_mode"] == "avoid_trade_gate"
    assert get_strategy("crypto_option_pair_sum_reference_hold_60s_v5").metadata["pair_sum_stability_required"] is True
    assert get_strategy("crypto_option_pair_sum_reference_hold_60s_v5").metadata["shadow_decision_mode"] == "reference_follow_or_neutral"
    assert get_strategy("crypto_option_regime_reference_scalp_v5").metadata["pair_sum_stability_required"] is True
    assert get_strategy("crypto_option_regime_reference_scalp_v5").metadata["shadow_decision_mode"] == "regime_reference_only"
    assert get_strategy("option_liquidity_micro_scalp_v2").metadata["option_path_min_forward_cashout_edge"] == 0.01
    assert get_strategy("option_liquidity_micro_scalp_v3").metadata["option_path_min_near_50c_sample_count"] == 3
    assert get_strategy("option_liquidity_micro_scalp_v4").metadata["option_path_min_trade_print_count"] == 1
    assert get_strategy("option_liquidity_micro_scalp_v5").metadata["shadow_decision_mode"] == "friction_reference_only"
    neutral_seed = get_strategy("master_hedge_grid_floor_seed_builder_neutral_v1")
    paired_seed = get_strategy("master_hedge_grid_floor_paired_seed_builder_v1")
    paired_seed_v2 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v2")
    paired_seed_v3 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v3")
    paired_seed_v4 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v4")
    paired_seed_v5 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v5")
    paired_seed_v6 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v6")
    paired_seed_v7 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v7")
    paired_seed_v8 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v8")
    paired_seed_v9 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v9")
    paired_seed_v10 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v10")
    paired_seed_v11 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v11")
    paired_seed_v12 = get_strategy("master_hedge_grid_floor_paired_seed_builder_v12")
    assert neutral_seed.strategy_version == "v1"
    assert neutral_seed.metadata["shadow_decision_mode"] == "seed_floor_builder_option_churn_no_lookahead"
    assert neutral_seed.metadata["source_blocks"] == ["C"]
    assert neutral_seed.metadata["hedge_floor_order_side_mode"] == "scenario_outcome"
    assert "option_path_min_forward_cashout_edge" not in neutral_seed.metadata
    assert paired_seed.strategy_version == "v1"
    assert paired_seed.metadata["shadow_decision_mode"] == "paired_seed_equal_share_floor_no_lookahead"
    assert paired_seed.metadata["source_blocks"] == ["C"]
    assert paired_seed.metadata["hedge_floor_seed_allocation_mode"] == "equal_shares"
    assert paired_seed.metadata["hedge_floor_paired_seed_required"] is True
    assert paired_seed.metadata["hedge_floor_paired_seed_max_pair_sum"] == 0.99
    assert paired_seed.metadata["hedge_floor_paired_seed_min_floor"] == 0.01
    assert "option_path_min_forward_cashout_edge" not in paired_seed.metadata
    assert paired_seed_v2.strategy_version == "v2"
    assert paired_seed_v2.metadata["shadow_decision_mode"] == "paired_seed_small_negative_floor_harvest_no_lookahead"
    assert paired_seed_v2.metadata["hedge_floor_paired_seed_max_pair_sum"] == 1.04
    assert paired_seed_v2.metadata["hedge_floor_paired_seed_min_floor"] == -0.08
    assert paired_seed_v2.metadata["hedge_floor_min_current_floor"] == -0.08
    assert paired_seed_v2.metadata["hedge_floor_min_inversion_intensity"] > paired_seed.metadata["hedge_floor_min_inversion_intensity"]
    assert paired_seed_v3.strategy_version == "v3"
    assert paired_seed_v3.metadata["shadow_decision_mode"] == "paired_seed_inversion_scalp_path_no_lookahead_economics"
    assert paired_seed_v3.metadata["paired_seed_scalp_simulation_required"] is True
    assert paired_seed_v3.metadata["paired_seed_scalp_buy_drop"] == 0.03
    assert paired_seed_v3.metadata["paired_seed_scalp_target"] == 0.03
    assert paired_seed_v4.strategy_version == "v4"
    assert paired_seed_v4.metadata["shadow_decision_mode"] == "paired_seed_deep_dip_quick_scalp_path_no_lookahead_economics"
    assert paired_seed_v4.metadata["paired_seed_scalp_buy_drop"] == 0.05
    assert paired_seed_v4.metadata["paired_seed_scalp_target"] == 0.02
    assert paired_seed_v4.metadata["paired_seed_scalp_notional_usd"] < paired_seed_v3.metadata["paired_seed_scalp_notional_usd"]
    assert paired_seed_v5.strategy_version == "v5"
    assert paired_seed_v5.metadata["shadow_decision_mode"] == "paired_seed_closed_cycle_scalp_path_no_lookahead_economics"
    assert paired_seed_v5.metadata["paired_seed_scalp_min_path_snapshots"] == 2
    assert paired_seed_v5.metadata["paired_seed_scalp_entry_window_fraction"] == 0.67
    assert paired_seed_v5.metadata["paired_seed_scalp_notional_usd"] < paired_seed_v4.metadata["paired_seed_scalp_notional_usd"]
    assert paired_seed_v6.strategy_version == "v6"
    assert paired_seed_v6.metadata["shadow_decision_mode"] == "paired_seed_closed_cycle_entry_gap_scalp_no_lookahead_economics"
    assert paired_seed_v6.metadata["hedge_floor_paired_seed_min_abs_entry_ask_gap"] == 0.05
    assert paired_seed_v6.metadata["hedge_floor_paired_seed_max_abs_entry_ask_gap"] == 0.40
    assert paired_seed_v6.metadata["hedge_floor_paired_seed_max_pair_sum"] < paired_seed_v5.metadata["hedge_floor_paired_seed_max_pair_sum"]
    assert paired_seed_v7.strategy_version == "v7"
    assert paired_seed_v7.metadata["shadow_decision_mode"] == "paired_seed_profile_coherent_entry_gap_scalp_no_lookahead_economics"
    assert paired_seed_v7.metadata["source_blocks"] == ["B", "C"]
    assert paired_seed_v7.signal_inputs["profile"] == ["band_rebound", "hedge_proportion", "outcome_expectation"]
    assert paired_seed_v7.metadata["profile_dependency_mode"] == "required"
    assert paired_seed_v7.metadata["profile_distribution_group_label"] == "S+ / hedger"
    assert paired_seed_v7.metadata["hedge_floor_paired_seed_max_pair_sum"] == paired_seed_v6.metadata["hedge_floor_paired_seed_max_pair_sum"]
    assert paired_seed_v8.strategy_version == "v8"
    assert paired_seed_v8.metadata["shadow_decision_mode"] == "paired_seed_profile_balanced_entry_gap_scalp_no_lookahead_economics"
    assert paired_seed_v8.metadata["profile_pressure_mode"] == "balanced_required"
    assert paired_seed_v8.metadata["source_blocks"] == ["B", "C"]
    assert paired_seed_v8.signal_inputs["profile"] == paired_seed_v7.signal_inputs["profile"]
    assert paired_seed_v9.strategy_version == "v9"
    assert paired_seed_v9.metadata["shadow_decision_mode"] == "paired_seed_profile_bucket_high_churn_scalp_no_lookahead_economics"
    assert paired_seed_v9.metadata["profile_dependency_mode"] == "optional_confidence"
    assert paired_seed_v9.metadata["profile_pressure_mode"] == "balanced_required"
    assert paired_seed_v9.metadata["option_path_min_level_crossing_count"] > paired_seed_v8.metadata["option_path_min_level_crossing_count"]
    assert paired_seed_v9.metadata["option_path_min_near_50c_sample_count"] == 8
    assert paired_seed_v9.metadata["paired_seed_scalp_min_path_snapshots"] == 3
    assert "hedge_floor_paired_seed_min_abs_entry_ask_gap" not in paired_seed_v9.metadata
    assert paired_seed_v10.strategy_version == "v10"
    assert paired_seed_v10.metadata["shadow_decision_mode"] == "paired_seed_profile_bucket_high_churn_two_snapshot_scalp_no_lookahead_economics"
    assert paired_seed_v10.metadata["profile_dependency_mode"] == paired_seed_v9.metadata["profile_dependency_mode"]
    assert paired_seed_v10.metadata["option_path_min_level_crossing_count"] == paired_seed_v9.metadata["option_path_min_level_crossing_count"]
    assert paired_seed_v10.metadata["paired_seed_scalp_min_path_snapshots"] == 2
    assert paired_seed_v10.metadata["paired_seed_scalp_entry_window_fraction"] > paired_seed_v9.metadata["paired_seed_scalp_entry_window_fraction"]
    assert paired_seed_v11.strategy_version == "v11"
    assert paired_seed_v11.metadata["shadow_decision_mode"] == "paired_seed_profile_bucket_quality_entry_scalp_no_lookahead_economics"
    assert paired_seed_v11.metadata["hedge_floor_paired_seed_min_abs_entry_ask_gap"] == 0.12
    assert paired_seed_v11.metadata["option_path_min_abs_pair_depth_pressure"] > paired_seed_v10.metadata["option_path_min_abs_pair_depth_pressure"]
    assert paired_seed_v11.metadata["paired_seed_scalp_min_path_snapshots"] == paired_seed_v10.metadata["paired_seed_scalp_min_path_snapshots"]
    assert paired_seed_v12.strategy_version == "v12"
    assert paired_seed_v12.metadata["shadow_decision_mode"] == "paired_seed_closed_cycle_positive_floor_preflight_no_lookahead_economics"
    assert paired_seed_v12.metadata["paired_seed_scalp_preflight_required"] is True
    assert paired_seed_v12.metadata["paired_seed_scalp_require_completed_cycle"] is True
    assert paired_seed_v12.metadata["paired_seed_scalp_require_floor_positive"] is True
    assert paired_seed_v12.metadata["paired_seed_scalp_require_no_open_positions"] is True


def test_profile_consensus_coherent_variants_use_executable_b_profile_guards_pytest() -> None:
    splusplus_hold = get_strategy("profile_splusplus_hedger_follow_coherent_hold_60s_v2")
    splusplus_hold_v3 = get_strategy("profile_splusplus_hedger_follow_coherent_hold_60s_v3")
    predictor_hold = get_strategy("profile_outcome_predictor_follow_coherent_hold_60s_v4")
    predictor_hold_v5 = get_strategy("profile_outcome_predictor_follow_coherent_hold_60s_v5")
    splus_grid = get_strategy("profile_splus_hedger_follow_coherent_grid_v4")
    splus_hold_v4 = get_strategy("profile_splus_hedger_follow_hold_60s_v4")
    splus_hold_v5 = get_strategy("profile_splus_hedger_follow_hold_60s_v5")
    splus_hold_v6 = get_strategy("profile_splus_hedger_follow_hold_60s_v6")
    splus_hold_v7 = get_strategy("profile_splus_hedger_follow_hold_60s_v7")
    splus_hold_v8 = get_strategy("profile_splus_hedger_follow_hold_60s_v8")
    splus_hold_v9 = get_strategy("profile_splus_hedger_follow_hold_60s_v9")
    splus_hold_v10 = get_strategy("profile_splus_hedger_follow_hold_60s_v10")
    profile_floor_v2 = get_strategy("master_hedge_grid_floor_profile_follow_v2")
    profile_floor_v3 = get_strategy("master_hedge_grid_floor_profile_follow_v3")
    seed_builder = get_strategy("master_hedge_grid_floor_seed_builder_v1")

    assert splusplus_hold.strategy_version == "v2"
    assert splusplus_hold.metadata["profile_distribution_max_top_profile_cost_share"] == 0.58
    assert splusplus_hold_v3.strategy_version == "v3"
    assert splusplus_hold_v3.metadata["profile_distribution_min_components"] == 4
    assert predictor_hold.strategy_version == "v4"
    assert predictor_hold.metadata["profile_distribution_group_label"] == "outcome_predictor"
    assert predictor_hold.metadata["profile_distribution_max_cost_share_gap"] == 0.20
    assert predictor_hold_v5.strategy_version == "v5"
    assert predictor_hold_v5.metadata["profile_distribution_min_components"] == 2
    assert splus_grid.strategy_version == "v4"
    assert splus_grid.metadata["option_path_min_level_crossing_count"] == 1
    assert splus_grid.metadata["profile_distribution_blocked_coverage_warnings"] == ["profile_distribution_source_stale"]
    assert splus_hold_v4.strategy_version == "v4"
    assert splus_hold_v4.metadata["profile_distribution_max_cost_share_gap"] == 0.34
    assert splus_hold_v4.metadata["option_path_min_avg_rolling_60s_range"] == 0.018
    assert splus_hold_v4.metadata["option_entry_price_max"] == 0.62
    assert splus_hold_v5.strategy_version == "v5"
    assert splus_hold_v5.metadata["profile_reconstructed_pair_sum_min"] == 0.75
    assert splus_hold_v5.metadata["profile_distribution_max_cost_share_gap"] == 0.45
    assert splus_hold_v5.metadata["profile_distribution_max_cost_count_gap"] == 0.48
    assert splus_hold_v5.metadata["option_entry_price_max"] == 0.7
    assert splus_hold_v6.strategy_version == "v6"
    assert splus_hold_v6.metadata["option_path_min_avg_rolling_60s_range"] == 0.01
    assert splus_hold_v6.metadata["option_entry_price_max"] == 0.7
    assert splus_hold_v7.strategy_version == "v7"
    assert splus_hold_v7.metadata["profile_direction_threshold"] == 0.4
    assert splus_hold_v7.metadata["profile_distribution_min_components"] == 4
    assert splus_hold_v7.metadata["option_entry_price_min"] == 0.5
    assert splus_hold_v7.metadata["option_entry_price_max"] == 0.85
    assert splus_hold_v8.strategy_version == "v8"
    assert splus_hold_v8.metadata["profile_direction_threshold"] == 0.4
    assert splus_hold_v8.metadata["profile_distribution_max_cost_count_gap"] == 0.48
    assert splus_hold_v8.metadata["profile_distribution_max_top_profile_cost_share"] == 0.70
    assert splus_hold_v8.metadata["option_entry_price_max"] == 0.85
    assert splus_hold_v9.strategy_version == "v9"
    assert splus_hold_v9.metadata["shadow_economics_require_liquidation_non_negative"] is True
    assert splus_hold_v9.metadata["shadow_economics_min_liquidation_pnl_usd"] == 0.0
    assert splus_hold_v9.metadata["shadow_economics_max_spread_drag_usd"] == 0.03
    assert splus_hold_v9.metadata["option_entry_price_max"] == splus_hold_v8.metadata["option_entry_price_max"]
    assert splus_hold_v10.strategy_version == "v10"
    assert "shadow_economics_require_liquidation_non_negative" not in splus_hold_v10.metadata
    assert splus_hold_v10.metadata["shadow_economics_min_liquidation_pnl_usd"] == -0.02
    assert splus_hold_v10.metadata["shadow_economics_max_spread_drag_usd"] == 0.02
    assert splus_hold_v10.metadata["option_entry_price_max"] < splus_hold_v9.metadata["option_entry_price_max"]
    assert profile_floor_v2.strategy_version == "v2"
    assert profile_floor_v2.metadata["profile_distribution_group_label"] == "S+ / hedger"
    assert profile_floor_v2.metadata["hedge_floor_mode"] == "seed_both_then_preserve_floor"
    assert profile_floor_v2.metadata["hedge_floor_tail_mode"] == "protected_only"
    assert profile_floor_v2.metadata["option_path_min_rebound_direction_flip_count"] == 2
    assert profile_floor_v3.strategy_version == "v3"
    assert profile_floor_v3.metadata["hedge_floor_mode"] == "protected_floor_follow"
    assert profile_floor_v3.metadata["hedge_floor_min_current_floor"] == 0.03
    assert profile_floor_v3.metadata["profile_distribution_group_label"] == "S+ / hedger"
    assert seed_builder.strategy_version == "v1"
    assert seed_builder.metadata["shadow_decision_mode"] == "seed_floor_builder_no_lookahead"
    assert seed_builder.metadata["hedge_floor_mode"] == "seed_floor_builder"
    assert seed_builder.metadata["hedge_floor_min_current_floor"] == -0.20
    assert seed_builder.metadata["seed_phase_candidate_lane"] is True
    assert "option_path_min_forward_cashout_edge" not in seed_builder.metadata


def test_retired_baseline_and_tail_touch_floor_variants_are_flagged_pytest() -> None:
    retired_predictor = get_strategy("profile_outcome_predictor_follow_hold_60s_v1")
    tail_touch_floor = get_strategy("master_hedge_grid_floor_tail_reversal_probe_v3")
    tail_touch_floor_v4 = get_strategy("master_hedge_grid_floor_tail_reversal_probe_v4")
    tail_touch_floor_v5 = get_strategy("master_hedge_grid_floor_tail_reversal_probe_v5")
    tail_touch_floor_v6 = get_strategy("master_hedge_grid_floor_tail_reversal_probe_v6")
    tail_touch_floor_v7 = get_strategy("master_hedge_grid_floor_tail_reversal_probe_v7")
    high_inversion_floor = get_strategy("master_hedge_grid_floor_neutral_rebound_v2")
    friction_gated_high_inversion_floor = get_strategy("master_hedge_grid_floor_neutral_rebound_v3")

    assert retired_predictor.metadata["promotion_candidate_lane"] is False
    assert retired_predictor.metadata["retired_from_promotion_lane"] is True
    assert retired_predictor.metadata["superseded_by_strategy_id"] == "profile_outcome_predictor_follow_coherent_hold_60s_v5"
    assert tail_touch_floor.metadata["shadow_decision_mode"] == "tail_touch_floor_reference_only"
    assert tail_touch_floor.metadata["profile_dependency_mode"] == "confidence_modifier"
    assert tail_touch_floor.metadata["source_blocks"] == ["B", "C"]
    assert tail_touch_floor_v4.strategy_version == "v4"
    assert tail_touch_floor_v4.metadata["shadow_decision_mode"] == "tail_touch_profile_price_reference_only"
    assert tail_touch_floor_v4.metadata["option_path_max_spread"] == 0.05
    assert tail_touch_floor_v4.metadata["option_path_min_forward_cashout_edge"] == 0.015
    assert tail_touch_floor_v5.strategy_version == "v5"
    assert tail_touch_floor_v5.metadata["shadow_decision_mode"] == "tail_touch_forward_edge_reference_only"
    assert tail_touch_floor_v5.metadata["option_path_max_spread"] == 0.04
    assert tail_touch_floor_v5.metadata["option_path_min_forward_cashout_edge"] == 0.03
    assert tail_touch_floor_v6.strategy_version == "v6"
    assert tail_touch_floor_v6.metadata["shadow_decision_mode"] == "tail_touch_protected_surplus_reference_only"
    assert tail_touch_floor_v6.metadata["hedge_floor_tail_mode"] == "protected_only"
    assert tail_touch_floor_v6.metadata["hedge_floor_require_surplus_for_tails"] is True
    assert tail_touch_floor_v6.metadata["promotion_candidate_lane"] is False
    assert tail_touch_floor_v7.strategy_version == "v7"
    assert tail_touch_floor_v7.metadata["shadow_decision_mode"] == "tail_touch_subgroup_follow_reference_only"
    assert tail_touch_floor_v7.metadata["profile_dependency_mode"] == "required"
    assert tail_touch_floor_v7.metadata["profile_distribution_group_label"] == "S+ / hedger"
    assert tail_touch_floor_v7.metadata["profile_distribution_min_components"] == 2
    assert tail_touch_floor_v7.metadata["hedge_floor_require_surplus_for_tails"] is True
    assert tail_touch_floor_v7.metadata["promotion_candidate_lane"] is False
    assert high_inversion_floor.strategy_version == "v2"
    assert high_inversion_floor.metadata["shadow_decision_mode"] == "high_inversion_floor_reference_only"
    assert high_inversion_floor.metadata["hedge_floor_order_side_mode"] == "scenario_outcome"
    assert friction_gated_high_inversion_floor.strategy_version == "v3"
    assert friction_gated_high_inversion_floor.metadata["shadow_decision_mode"] == "high_inversion_friction_reference_only"
    assert friction_gated_high_inversion_floor.metadata["option_path_max_spread"] == 0.02
    assert friction_gated_high_inversion_floor.metadata["option_path_min_forward_cashout_edge"] == 0.02


def test_strategy_schema_validation_rejects_unknown_signal_and_missing_lifecycle_pytest() -> None:
    valid = get_strategy("s_tier_outcome_consensus_cashout_v1")
    assert validate_strategy_spec(valid).valid is True

    invalid_signal = _replace(valid, signal_inputs={"profile": ["not_a_signal"], "event": [], "indicator": []})
    signal_result = validate_strategy_spec(invalid_signal)
    assert signal_result.valid is False
    assert "unknown_profile_signal:not_a_signal" in signal_result.errors

    missing_lifecycle = _replace(valid, exit_rules={})
    lifecycle_result = validate_strategy_spec(missing_lifecycle)
    assert lifecycle_result.valid is False
    assert "exit_lifecycle_coverage_missing" in lifecycle_result.errors


def test_strategy_state_transitions_require_evidence_pytest() -> None:
    assert transition_strategy_state(
        current_state="draft",
        next_state="validated",
        evidence={"tests": "schema_validation"},
    ) == "validated"
    with pytest.raises(ValueError, match="require evidence"):
        transition_strategy_state(current_state="draft", next_state="validated", evidence={})


def test_replay_and_pulse_readiness_return_structured_blockers_pytest() -> None:
    spec = get_strategy("indicator_confirmed_outcome_v1")

    replay_ready = evaluate_replay_readiness(spec)
    replay_blocked = evaluate_replay_readiness(spec, data_blockers=("missing_indicator_snapshots",))
    pulse_blocked = evaluate_pulse_readiness(spec, replay_rejected=False, executor_boundary_configured=False)

    assert replay_ready.readiness_state == "replay_ready"
    assert replay_ready.orders_allowed is False
    assert replay_blocked.readiness_state == "blocked"
    assert replay_blocked.blockers == ("missing_indicator_snapshots",)
    assert pulse_blocked.readiness_state == "blocked"
    assert "executor_boundary_not_configured" in pulse_blocked.blockers


def test_disabled_strategy_emits_no_structural_candidate_or_intent_pytest() -> None:
    disabled = get_strategy("s_tier_outcome_consensus_cashout_v1")
    enabled = disabled.with_enabled(True)

    disabled_record = build_structural_candidate(
        disabled,
        event_key="event-1",
        event_token_key="event-1:up",
        side="Up",
    )
    enabled_record = build_structural_candidate(
        enabled,
        event_key="event-1",
        event_token_key="event-1:up",
        side="Up",
    )
    missing_identity = build_structural_candidate(
        enabled,
        event_key="event-1",
        event_token_key=None,
        side="Up",
    )

    assert disabled_record.status == "disabled"
    assert disabled_record.candidate is None
    assert disabled_record.orders_allowed is False
    assert enabled_record.status == "candidate_ready"
    assert enabled_record.candidate is not None
    assert enabled_record.candidate["requires_executor_boundary"] is True
    assert enabled_record.orders_allowed is False
    assert missing_identity.status == "blocked"
    assert missing_identity.blockers == ("missing_event_token_key",)


def _replace(spec: StrategySpec, **kwargs) -> StrategySpec:
    payload = spec.__dict__.copy()
    payload.update(kwargs)
    return StrategySpec(**payload)
