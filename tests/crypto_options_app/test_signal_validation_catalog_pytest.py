from __future__ import annotations

from crypto_options_app.signals.validation.registry import get_signal_spec, list_signal_specs, signal_catalog_summary


def test_signal_validation_catalog_has_structured_master_family_first_batch_pytest() -> None:
    specs = list_signal_specs()

    assert len(specs) >= 20
    assert {spec.family for spec in specs} == {"master_hedge_grid_scalping"}
    assert {"outcome_prediction", "side_start", "grid_spacing", "hedge_ratio", "cashout_rebuy"}.issubset(
        {spec.signal_type for spec in specs}
    )
    assert all(not spec.structural_blockers() for spec in specs)


def test_signal_validation_catalog_covers_data_blocks_and_purposes_pytest() -> None:
    specs = list_signal_specs()
    blocks = {block for spec in specs for block in spec.required_data_blocks}
    purposes = {spec.purpose for spec in specs}

    assert blocks == {"A", "B", "C"}
    assert {"strategy_parameter", "logic_gate_trigger", "refreshing_stat", "price_reference"}.issubset(purposes)


def test_signal_validation_catalog_lookup_and_summary_are_read_only_pytest() -> None:
    summary = signal_catalog_summary()
    signal_id = summary["signals"][0]["signal_id"]

    assert get_signal_spec(signal_id) is not None
    assert get_signal_spec("missing") is None
    assert summary["orders_allowed"] is False
    assert summary["live_trading_authorized"] is False


def test_signal_validation_catalog_includes_ifcm_support_resistance_v4_revisions_pytest() -> None:
    variants = {
        spec.variant
        for spec in list_signal_specs()
        if spec.signal_type == "support_resistance" and spec.version == "v4"
    }

    assert "ifcm_pivot_distance_option_reclaim_positive_return_filter" in variants
    assert "ifcm_pivot_distance_grid_reference_forward_return_guard" in variants


def test_signal_validation_catalog_includes_crypto_option_confluence_v2_to_v5_pytest() -> None:
    lookup = {(spec.signal_type, spec.variant, spec.version): spec for spec in list_signal_specs()}

    expected = {
        ("outcome_prediction", "multiframe_conflict_option_path_alignment", "v2"),
        ("side_start", "observer_conflict_neutral_start_gate", "v3"),
        ("buy_rebound", "pivot_cluster_path_rebound_reclaim", "v4"),
        ("latency_quality", "observer_path_pair_sum_avoid_trade_gate", "v5"),
        ("support_resistance", "pivot_distance_pair_sum_reference", "v5"),
        ("grid_spacing", "crypto_option_path_volatility_reference", "v5"),
        ("trend_regime", "observer_path_conflict_reference", "v5"),
        ("side_start", "observer_pair_sum_stable_start_reference", "v5"),
    }

    assert expected.issubset(set(lookup))
    for key in expected:
        assert lookup[key].required_data_blocks == ("A", "C")


def test_signal_validation_catalog_includes_profile_consensus_revision_waves_pytest() -> None:
    lookup = {(spec.signal_type, spec.variant, spec.version): spec for spec in list_signal_specs()}

    expected = {
        ("outcome_prediction", "splus_hedger_coherent_consensus", "v4"),
        ("outcome_prediction", "outcome_predictor_coherent_consensus", "v4"),
        ("hedge_ratio", "cost_share_alignment_reference", "v5"),
        ("latency_quality", "profile_distribution_pair_sum_guard", "v5"),
        ("latency_quality", "profile_distribution_grade_style_balance_guard", "v5"),
    }

    assert expected.issubset(set(lookup))
    for key in expected:
        assert lookup[key].required_data_blocks == ("B",)


def test_signal_validation_catalog_includes_option_liquidity_microstructure_v5_building_blocks_pytest() -> None:
    lookup = {(spec.signal_type, spec.variant, spec.version): spec for spec in list_signal_specs()}

    expected = {
        ("liquidity_depth", "spread_top3_friction_guard", "v5"),
        ("liquidity_depth", "pair_depth_pressure_fillability_guard", "v5"),
        ("liquidity_depth", "top3_pair_depth_pressure_reference", "v5"),
        ("latency_quality", "source_latency_trade_print_guard", "v5"),
        ("grid_spacing", "rolling_range_spread_reference", "v5"),
        ("trend_regime", "near50c_churn_imbalance_reference", "v5"),
        ("side_start", "entry_price_band_depth_reference", "v5"),
        ("cashout_rebuy", "forward_mark_cashout_viability", "v5"),
    }

    assert expected.issubset(set(lookup))
    for key in expected:
        assert lookup[key].required_data_blocks == ("C",)


def test_signal_validation_catalog_includes_hedge_floor_tail_touch_revision_wave_pytest() -> None:
    lookup = {(spec.signal_type, spec.variant, spec.version): spec for spec in list_signal_specs()}

    expected = {
        ("inversion_intensity", "tail_touch_rebound_flip_density", "v2"): ("C",),
        ("inversion_intensity", "forward_cashout_edge_clean_flip_density", "v3"): ("C",),
        ("tail_reversal_probability", "tail_touch_microstructure_context", "v2"): ("B", "C"),
        ("grid_viability", "tail_touch_spread_depth_budget", "v2"): ("C",),
        ("hedge_floor_state", "scenario_outcome_confidence_floor", "v2"): ("B", "C"),
        ("floor_preserving_order_gate", "scenario_outcome_floor_preserve", "v2"): ("C",),
        ("surplus_tail_budget", "subgroup_confidence_capped_surplus", "v2"): ("B", "C"),
        ("tail_reversal_probability", "tail_touch_subgroup_alignment_context", "v3"): ("B", "C"),
        ("tail_reversal_probability", "tail_touch_profile_price_context", "v4"): ("B", "C"),
        ("surplus_tail_budget", "tail_touch_subgroup_alignment_cap", "v3"): ("B", "C"),
        ("surplus_tail_budget", "tail_touch_profile_price_cap", "v4"): ("B", "C"),
        ("hedge_floor_state", "profile_price_confidence_floor", "v3"): ("B", "C"),
        ("grid_viability", "forward_cashout_edge_budget", "v3"): ("C",),
        ("floor_preserving_order_gate", "forward_cashout_edge_floor_preserve", "v3"): ("C",),
    }

    assert expected.keys() <= set(lookup)
    for key, blocks in expected.items():
        assert lookup[key].required_data_blocks == blocks
        assert lookup[key].signal_payload_example["orders_allowed"] is False
        if key[2] in {"v2", "v3"}:
            assert lookup[key].signal_payload_example["empirical_tail_touch_required"] is True

    assert lookup[("inversion_intensity", "tail_touch_rebound_flip_density", "v2")].event_phase_relevance == "both"
    assert lookup[("grid_viability", "tail_touch_spread_depth_budget", "v2")].purpose == "logic_gate_trigger"
    assert lookup[("hedge_floor_state", "scenario_outcome_confidence_floor", "v2")].signal_payload_example[
        "hedge_floor_revision_wave"
    ] == "scenario_outcome_confidence_floor"
    assert lookup[("floor_preserving_order_gate", "scenario_outcome_floor_preserve", "v2")].signal_payload_example[
        "hedge_floor_revision_wave"
    ] == "scenario_outcome_floor_preserve"
    assert lookup[("inversion_intensity", "tail_touch_rebound_flip_density", "v2")].supersedes_signal_id == (
        "master_hedge_grid_scalping_inversion_intensity_optionprice_path_crossing_flip_density_v1"
    )
    assert lookup[("inversion_intensity", "forward_cashout_edge_clean_flip_density", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_inversion_intensity_optionprice_tail_touch_rebound_flip_density_v2"
    )
    assert lookup[("tail_reversal_probability", "tail_touch_subgroup_alignment_context", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_microstructure_context_v2"
    )
    assert lookup[("tail_reversal_probability", "tail_touch_profile_price_context", "v4")].supersedes_signal_id == (
        "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3"
    )
    assert lookup[("surplus_tail_budget", "tail_touch_subgroup_alignment_cap", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_surplus_tail_budget_profiles_optionprice_subgroup_confidence_capped_surplus_v2"
    )
    assert lookup[("surplus_tail_budget", "tail_touch_profile_price_cap", "v4")].supersedes_signal_id == (
        "master_hedge_grid_scalping_surplus_tail_budget_profiles_optionprice_tail_touch_subgroup_alignment_cap_v3"
    )
    assert lookup[("hedge_floor_state", "profile_price_confidence_floor", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_hedge_floor_state_profiles_optionprice_scenario_outcome_confidence_floor_v2"
    )
    assert lookup[("grid_viability", "forward_cashout_edge_budget", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_grid_viability_optionprice_tail_touch_spread_depth_budget_v2"
    )
    assert lookup[("floor_preserving_order_gate", "forward_cashout_edge_floor_preserve", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_floor_preserving_order_gate_optionprice_scenario_outcome_floor_preserve_v2"
    )


def test_signal_validation_catalog_includes_b_profile_subgroup_revision_wave_pytest() -> None:
    lookup = {(spec.signal_type, spec.variant, spec.version): spec for spec in list_signal_specs()}

    expected = {
        ("outcome_prediction", "splus_hedger_subgroup_coherent_consensus", "v5"),
        ("latency_quality", "splus_hedger_subgroup_component_guard", "v5"),
        ("outcome_prediction", "splusplus_hedger_subgroup_coherent_consensus", "v5"),
        ("latency_quality", "splusplus_hedger_subgroup_depth_freshness_guard", "v5"),
        ("outcome_prediction", "outcome_predictor_subgroup_coherent_consensus", "v5"),
        ("latency_quality", "outcome_predictor_subgroup_component_guard", "v5"),
        ("latency_quality", "tail_touch_subgroup_conflict_guard", "v5"),
        ("profile_subgroup_distribution", "grade_style_side_tilt_reference", "v1"),
        ("profile_subgroup_distribution", "method_agreement_concentration_reference", "v2"),
        ("profile_subgroup_distribution", "profile_price_tail_context_reference", "v3"),
        ("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4"),
        ("latency_quality", "subgroup_method_agreement_guard", "v5"),
    }

    assert expected.issubset(set(lookup))
    bc_expected = {
        ("latency_quality", "tail_touch_subgroup_conflict_guard", "v5"),
        ("profile_subgroup_distribution", "grade_style_side_tilt_reference", "v1"),
        ("profile_subgroup_distribution", "method_agreement_concentration_reference", "v2"),
        ("profile_subgroup_distribution", "profile_price_tail_context_reference", "v3"),
        ("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4"),
        ("latency_quality", "subgroup_method_agreement_guard", "v5"),
    }
    for key in expected - bc_expected:
        assert lookup[key].required_data_blocks == ("B",)
        assert lookup[key].signal_payload_example["requires_component_breakdown"] is True
        assert lookup[key].signal_payload_example["orders_allowed"] is False
    assert lookup[("latency_quality", "tail_touch_subgroup_conflict_guard", "v5")].required_data_blocks == ("B", "C")
    assert lookup[("profile_subgroup_distribution", "grade_style_side_tilt_reference", "v1")].required_data_blocks == ("B", "C")
    assert lookup[("profile_subgroup_distribution", "method_agreement_concentration_reference", "v2")].required_data_blocks == ("B", "C")
    assert lookup[("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4")].required_data_blocks == ("B", "C")
    assert lookup[("latency_quality", "subgroup_method_agreement_guard", "v5")].required_data_blocks == ("B", "C")
    assert lookup[("latency_quality", "tail_touch_subgroup_conflict_guard", "v5")].event_phase_relevance == "live"
    assert lookup[("latency_quality", "tail_touch_subgroup_conflict_guard", "v5")].signal_payload_example[
        "empirical_tail_touch_required"
    ] is True
    assert lookup[("profile_subgroup_distribution", "grade_style_side_tilt_reference", "v1")].event_phase_relevance == "both"
    assert lookup[("profile_subgroup_distribution", "grade_style_side_tilt_reference", "v1")].signal_payload_example[
        "requires_option_context"
    ] is True
    assert lookup[("profile_subgroup_distribution", "method_agreement_concentration_reference", "v2")].signal_payload_example[
        "requires_method_agreement"
    ] is True
    assert lookup[("profile_subgroup_distribution", "method_agreement_concentration_reference", "v2")].supersedes_signal_id == (
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_grade_style_side_tilt_reference_v1"
    )
    assert lookup[("profile_subgroup_distribution", "profile_price_tail_context_reference", "v3")].signal_payload_example[
        "requires_profile_price_context"
    ] is True
    assert lookup[("profile_subgroup_distribution", "profile_price_tail_context_reference", "v3")].signal_payload_example[
        "empirical_tail_touch_required"
    ] is True
    assert lookup[("profile_subgroup_distribution", "profile_price_tail_context_reference", "v3")].supersedes_signal_id == (
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2"
    )
    assert lookup[("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4")].signal_payload_example[
        "requires_execution_context"
    ] is True
    assert lookup[("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4")].signal_payload_example[
        "empirical_tail_touch_required"
    ] is True
    assert lookup[("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4")].supersedes_signal_id == (
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3"
    )
    assert lookup[("latency_quality", "subgroup_method_agreement_guard", "v5")].signal_payload_example[
        "requires_method_agreement"
    ] is True
    parent_signal_id = (
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_grade_style_side_tilt_reference_v1"
    )
    for key in expected - {
        ("profile_subgroup_distribution", "grade_style_side_tilt_reference", "v1"),
        ("profile_subgroup_distribution", "method_agreement_concentration_reference", "v2"),
        ("profile_subgroup_distribution", "profile_price_tail_context_reference", "v3"),
        ("profile_subgroup_distribution", "profile_price_execution_context_reference", "v4"),
        ("latency_quality", "subgroup_method_agreement_guard", "v5"),
    }:
        assert lookup[key].parent_signal_id == parent_signal_id
    assert lookup[("latency_quality", "subgroup_method_agreement_guard", "v5")].parent_signal_id == (
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2"
    )
    assert lookup[("outcome_prediction", "splus_hedger_subgroup_coherent_consensus", "v5")].supersedes_signal_id == (
        "master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_coherent_consensus_v4"
    )
    assert lookup[("outcome_prediction", "outcome_predictor_subgroup_coherent_consensus", "v5")].supersedes_signal_id == (
        "master_hedge_grid_scalping_outcome_prediction_profiles_outcome_predictor_coherent_consensus_v4"
    )
