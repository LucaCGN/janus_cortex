from __future__ import annotations

from crypto_options_app.signals.validation.registry import get_signal_spec
from crypto_options_app.strategies.hedge_floor import (
    crypto_tail_distance_bucket,
    floor_preserving_order_gate,
    grid_viability,
    hedge_floor_state,
    profile_tail_context_bucket,
    profile_tail_price_context_bucket,
    surplus_tail_budget,
    tail_reversal_probability,
)
from crypto_options_app.strategies.registry import get_strategy, validate_registry
from crypto_options_app.workers.runtime_adapter import RuntimeScenario, _strategy_shadow_scenario


def test_hedge_floor_state_tracks_positive_floor_and_surplus() -> None:
    state = hedge_floor_state(realized_cash=0.35, up_shares=0.90, down_shares=0.70, protected_floor=0.50)
    assert round(state.payout_if_up, 8) == 1.25
    assert round(state.payout_if_down, 8) == 1.05
    assert round(state.guaranteed_floor, 8) == 1.05
    assert round(surplus_tail_budget(state), 8) == 0.55


def test_floor_preserving_order_gate_rejects_floor_damage_after_protection() -> None:
    state = hedge_floor_state(realized_cash=0.10, up_shares=0.75, down_shares=0.72, protected_floor=0.80)
    allowed, change = floor_preserving_order_gate(state, side="up", price=0.85, shares=0.50)
    assert allowed is False
    assert change.preserves_floor is False
    assert change.weaker_side_improved is False


def test_grid_viability_prefers_inversion_heavy_paths() -> None:
    path = {
        "level_crossing_count": 7,
        "rebound_direction_flip_count": 4,
        "near_50c_sample_count": 6,
        "strong_rebound_touch_count": 3,
        "avg_rolling_60s_range": 0.07,
        "max_rolling_60s_range": 0.11,
        "pair_sum_range": 0.05,
    }
    viability = grid_viability(path, spread=0.015, liquidity_depth=22.0)
    assert viability.viable is True
    assert viability.inversion_intensity > 0.6
    assert 0.01 <= viability.recommended_spacing <= 0.12


def test_tail_reversal_probability_uses_touch_bucket_and_state_scores() -> None:
    probability = tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=45.0,
        inversion_score=0.8,
        volatility_score=0.7,
    )
    assert 0.30 <= probability <= 0.70


def test_tail_reversal_probability_prefers_microstructure_context() -> None:
    probability = tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        depth_pressure_score=0.3,
        tables={
            "microstructure_context_probabilities": {
                "touched_5c": {
                    "60_180": {
                        "violent": {
                            "up_supportive": 0.94,
                        }
                    }
                }
            }
        },
    )
    assert probability == 0.94


def test_tail_reversal_probability_prefers_execution_context() -> None:
    probability = tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        spread=0.06,
        liquidity_depth=24.0,
        depth_pressure_score=0.3,
        tables={
            "execution_context_probabilities": {
                "touched_5c": {
                    "60_180": {
                        "violent": {
                            "wide|medium|moderate": 0.81,
                        }
                    }
                }
            },
            "microstructure_context_probabilities": {
                "touched_5c": {
                    "60_180": {
                        "violent": {
                            "up_supportive": 0.42,
                        }
                    }
                }
            },
        },
    )
    assert probability == 0.81


def test_tail_reversal_probability_prefers_crypto_distance_context() -> None:
    probability = tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        spread=0.06,
        liquidity_depth=24.0,
        crypto_distance_bucket="far_up",
        depth_pressure_score=0.3,
        tables={
            "crypto_distance_context_probabilities": {
                "touched_5c": {
                    "60_180": {
                        "violent": {
                            "far_up": 0.86,
                        }
                    }
                }
            },
            "execution_context_probabilities": {
                "touched_5c": {
                    "60_180": {
                        "violent": {
                            "wide|medium|moderate": 0.81,
                        }
                    }
                }
            },
        },
    )
    assert probability == 0.86


def test_profile_tail_context_bucket_tracks_subgroup_tilt_and_prices() -> None:
    bucket = profile_tail_context_bucket(
        {
            "up_reconstructed_profile_price": 0.76,
            "down_reconstructed_profile_price": 0.24,
            "component_breakdown": {
                "by_grade_style": {
                    "S+ / hedger": {
                        "up_pressure_ratio": 0.7,
                        "down_pressure_ratio": 0.3,
                        "pressure_delta": 0.4,
                    }
                }
            },
        }
    )
    assert bucket == "splus_hedger|up|up"


def test_profile_tail_price_context_bucket_tracks_subgroup_tilt_and_price_distance() -> None:
    bucket = profile_tail_price_context_bucket(
        {
            "up_reconstructed_profile_price": 0.76,
            "down_reconstructed_profile_price": 0.24,
            "component_breakdown": {
                "by_grade_style": {
                    "S+ / hedger": {
                        "up_pressure_ratio": 0.7,
                        "down_pressure_ratio": 0.3,
                        "pressure_delta": 0.4,
                    }
                }
            },
        }
    )
    assert bucket == "splus_hedger|up|up_extreme"


def test_crypto_tail_distance_bucket_tracks_average_observer_distance() -> None:
    bucket = crypto_tail_distance_bucket(
        {
            "symbol": "BTC",
            "summaries": [
                {"summary_score": 0.61},
                {"summary_score": 0.55},
            ],
        }
    )
    assert bucket == "far_up"


def test_registry_exposes_hedge_floor_strategy_variants_and_signal_specs() -> None:
    failures = validate_registry()
    assert "master_hedge_grid_floor_profile_follow_v1" not in failures
    assert "master_hedge_grid_floor_neutral_rebound_v1" not in failures
    assert "master_hedge_grid_floor_neutral_rebound_v2" not in failures
    assert "master_hedge_grid_floor_neutral_rebound_v3" not in failures
    assert "master_hedge_grid_floor_tail_reversal_probe_v3" not in failures
    assert "master_hedge_grid_floor_tail_reversal_probe_v5" not in failures
    assert "master_hedge_grid_floor_tail_reversal_probe_v6" not in failures
    assert "master_hedge_grid_floor_low_range_no_edge_control_v1" not in failures
    assert "profile_splusplus_hedger_follow_coherent_scalp_v1" not in failures
    assert "profile_splusplus_hedger_follow_concentration_probe_scalp_v1" not in failures
    assert get_strategy("master_hedge_grid_floor_profile_follow_v1").metadata["hedge_floor_mode"] == "seed_both_then_preserve_floor"
    assert get_strategy("master_hedge_grid_floor_neutral_rebound_v1").metadata["hedge_floor_mode"] == "seed_both_then_preserve_floor"
    assert get_strategy("master_hedge_grid_floor_neutral_rebound_v2").metadata["hedge_floor_order_side_mode"] == "scenario_outcome"
    assert get_strategy("master_hedge_grid_floor_neutral_rebound_v3").metadata["option_path_min_forward_cashout_edge"] == 0.02
    assert get_strategy("master_hedge_grid_floor_tail_reversal_probe_v3").metadata["grid_price_band_required"] is False
    assert get_strategy("master_hedge_grid_floor_tail_reversal_probe_v5").metadata["option_path_min_forward_cashout_edge"] == 0.03
    assert get_strategy("master_hedge_grid_floor_tail_reversal_probe_v6").metadata["hedge_floor_require_surplus_for_tails"] is True
    assert get_strategy("master_hedge_grid_floor_low_range_no_edge_control_v1").metadata["diagnostic_control_lane"] is True
    assert get_strategy("master_hedge_grid_floor_low_range_no_edge_control_v1").metadata["no_live_promotion"] is True
    assert get_strategy("profile_splusplus_hedger_follow_coherent_scalp_v1").metadata["profile_distribution_min_components"] == 4
    assert get_strategy("profile_splusplus_hedger_follow_concentration_probe_scalp_v1").metadata["diagnostic_control_lane"] is True
    assert get_signal_spec(
        "master_hedge_grid_scalping_inversion_intensity_optionprice_path_crossing_flip_density_v1"
    ) is not None
    assert get_signal_spec(
        "master_hedge_grid_scalping_floor_preserving_order_gate_optionprice_weaker_side_improve_or_preserve_v1"
    ) is not None


def test_runtime_shadow_decision_surfaces_hedge_floor_metrics() -> None:
    spec = get_strategy("master_hedge_grid_floor_profile_follow_v1").with_enabled(True)
    scenario = RuntimeScenario(
        event_key="event-1",
        event_token_key="event-1:up",
        event_slug="btc-updown-5m-test",
        outcome="Up",
        shares=1.0,
        limit_price=0.46,
        spread=0.015,
        liquidity_depth=25.0,
        time_remaining_seconds=75.0,
        signal_context={
            "best_bid": 0.45,
            "best_ask": 0.46,
            "target_up_ratio": 0.64,
            "profile_distribution_ready": True,
            "profile_distribution": {
                "profile_distribution_up_ratio": 0.64,
                "profile_distribution_down_ratio": 0.36,
                "up_reconstructed_profile_price": 0.74,
                "down_reconstructed_profile_price": 0.24,
                "component_breakdown": {
                    "by_grade_style": {
                        "S+ / hedger": {
                            "up_pressure_ratio": 0.7,
                            "down_pressure_ratio": 0.3,
                            "pressure_delta": 0.4,
                        }
                    }
                },
            },
            "option_path_ready": True,
            "option_path": {
                "snapshot_count": 8,
                "level_crossing_count": 6,
                "rebound_direction_flip_count": 3,
                "near_50c_sample_count": 4,
                "strong_rebound_touch_count": 3,
                "avg_rolling_60s_range": 0.07,
                "max_rolling_60s_range": 0.10,
                "pair_sum_range": 0.04,
                "tail_comeback_table": {
                    "profile_price_execution_context_probabilities": {
                        "touched_5c": {
                            "60_180": {
                                "violent": {
                                    "splus_hedger|up|up_extreme": {
                                        "tight|medium|low": 0.91,
                                    }
                                }
                            }
                        }
                    },
                    "profile_price_context_probabilities": {
                        "touched_5c": {
                            "60_180": {
                                "violent": {
                                    "splus_hedger|up|up_extreme": 0.79,
                                }
                            }
                        }
                    },
                    "execution_context_probabilities": {
                        "touched_5c": {
                            "60_180": {
                                "active": {
                                    "tight|medium|low": 0.68,
                                }
                            }
                        }
                    },
                    "profile_context_probabilities": {
                        "touched_5c": {
                            "60_180": {
                                "violent": {
                                    "splus_hedger|up|up": 0.73,
                                }
                            }
                        }
                    }
                },
            },
        },
    )
    adjusted, decision, blockers = _strategy_shadow_scenario(spec, scenario)
    assert adjusted.signal_context["strategy_shadow_decision"]["hedge_floor_state"]["guaranteed_floor"] <= 0.1
    assert "inversion_intensity" in decision
    assert "grid_viability" in decision
    assert "surplus_tail_budget" in decision
    assert "floor_preserving_order_gate" in decision
    assert decision["tail_reversal_probability"] == 0.91
    assert decision["tail_reversal_context"]["profile_price_context_bucket"] == "splus_hedger|up|up_extreme"
    assert (
        decision["tail_reversal_context"]["profile_price_execution_context_bucket"]
        == "splus_hedger|up|up_extreme||tight|medium|low"
    )
    assert blockers == ()


def test_low_range_no_edge_control_stays_diagnostic_and_non_promotable() -> None:
    spec = get_strategy("master_hedge_grid_floor_low_range_no_edge_control_v1").with_enabled(True)
    scenario = RuntimeScenario(
        event_key="event-2",
        event_token_key="event-2:up",
        event_slug="btc-updown-5m-low-range",
        outcome="Up",
        shares=1.0,
        limit_price=0.45,
        spread=0.02,
        liquidity_depth=15.0,
        time_remaining_seconds=90.0,
        signal_context={
            "best_bid": 0.44,
            "best_ask": 0.45,
            "forward_best_bid": 0.455,
            "option_path_ready": True,
            "option_path": {
                "snapshot_count": 6,
                "level_crossing_count": 0,
                "rebound_direction_flip_count": 0,
                "near_50c_sample_count": 1,
                "strong_rebound_touch_count": 0,
                "avg_rolling_60s_range": 0.012,
                "max_rolling_60s_range": 0.018,
                "pair_sum_range": 0.02,
            },
        },
    )
    _adjusted, decision, blockers = _strategy_shadow_scenario(spec, scenario)
    assert decision["shadow_decision_mode"] == "low_range_no_edge_control"
    assert decision["diagnostic_control_lane"] is True
    assert decision["no_live_promotion"] is True
    assert round(decision["low_range_control"]["forward_cashout_edge"], 8) == 0.005
    assert "diagnostic_no_live_promotion_control_lane" in blockers
