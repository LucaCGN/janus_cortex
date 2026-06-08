from __future__ import annotations

import pytest

from crypto_options_app.strategies.hedge_floor import (
    floor_preserving_order_gate,
    grid_spacing_from_inversion_stats,
    grid_viability,
    hedge_floor_state,
    paired_seed_entry_projection,
    protected_floor_improvement,
    rebound_enough_to_cashout,
    surplus_tail_budget,
    tail_reversal_probability,
)


def test_hedge_floor_math_preserves_and_improves_weaker_side_pytest() -> None:
    state = hedge_floor_state(
        realized_cash=-0.6,
        up_shares=1.0,
        down_shares=0.7,
        protected_floor=0.05,
    )

    allowed, change = floor_preserving_order_gate(state, side="down", price=0.20, shares=0.25)
    improvement = protected_floor_improvement(state, side="down", price=0.20, shares=0.25)

    assert state.guaranteed_floor == pytest.approx(0.1)
    assert state.weaker_side == "down"
    assert allowed is True
    assert change.weaker_side_improved is True
    assert change.preserves_floor is True
    assert improvement.guaranteed_floor_after == pytest.approx(0.3)
    assert surplus_tail_budget(state) == pytest.approx(0.05)


def test_hedge_floor_gate_rejects_order_that_breaks_protected_floor_pytest() -> None:
    state = hedge_floor_state(
        realized_cash=-0.4,
        up_shares=1.0,
        down_shares=0.9,
        protected_floor=0.45,
    )

    allowed, change = floor_preserving_order_gate(state, side="up", price=0.90, shares=0.60)

    assert state.guaranteed_floor == pytest.approx(0.5)
    assert allowed is False
    assert change.weaker_side_improved is False
    assert change.preserves_floor is False
    assert change.guaranteed_floor_after < state.protected_floor


def test_paired_seed_entry_projection_uses_equal_shares_to_create_floor_pytest() -> None:
    projection = paired_seed_entry_projection(up_price=0.45, down_price=0.50, budget_usd=2.0)

    assert projection.pair_sum == pytest.approx(0.95)
    assert projection.equal_shares == pytest.approx(2.0 / 0.95)
    assert projection.state.up_shares == pytest.approx(projection.state.down_shares)
    assert projection.state.payout_if_up == pytest.approx(projection.state.payout_if_down)
    assert projection.guaranteed_floor == pytest.approx((2.0 / 0.95) - 2.0)
    assert projection.floor_margin_per_share == pytest.approx(0.05)
    assert projection.floor_margin_ratio > 0.0


def test_paired_seed_entry_projection_exposes_negative_floor_when_pair_is_expensive_pytest() -> None:
    projection = paired_seed_entry_projection(up_price=0.51, down_price=0.50, budget_usd=2.0)

    assert projection.pair_sum == pytest.approx(1.01)
    assert projection.guaranteed_floor < 0.0
    assert projection.floor_margin_per_share == pytest.approx(-0.01)


def test_grid_viability_and_tail_reversal_capture_inversion_heavy_paths_pytest() -> None:
    path = {
        "level_crossing_count": 8,
        "rebound_direction_flip_count": 5,
        "near_50c_sample_count": 7,
        "strong_rebound_touch_count": 4,
        "avg_rolling_60s_range": 0.06,
        "max_rolling_60s_range": 0.12,
        "pair_sum_range": 0.03,
    }

    viability = grid_viability(path, spread=0.02, liquidity_depth=40.0)
    spacing = grid_spacing_from_inversion_stats(
        inversion_score=viability.inversion_intensity,
        avg_rolling_60s_range=path["avg_rolling_60s_range"],
        spread=0.02,
    )
    tail_probability = tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=90.0,
        inversion_score=viability.inversion_intensity,
        volatility_score=1.0,
    )

    assert viability.viable is True
    assert viability.inversion_intensity > 0.8
    assert viability.viability_score > 0.7
    assert viability.recommended_spacing == spacing
    assert rebound_enough_to_cashout(path, spacing=spacing, spread=0.02) is True
    assert 0.01 <= spacing <= 0.12
    assert tail_probability > 0.4


def test_tail_reversal_probability_uses_empirical_comeback_table_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.82,
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.0,
        tables=table,
    ) == pytest.approx(0.82)


def test_tail_reversal_probability_prefers_time_and_volatility_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.35,
            }
        },
        "context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": 0.91,
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        tables=table,
    ) == pytest.approx(0.91)


def test_tail_reversal_probability_prefers_microstructure_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.35,
            }
        },
        "context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": 0.91,
                }
            }
        },
        "microstructure_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "up_supportive": 0.97,
                    }
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        depth_pressure_score=0.35,
        tables=table,
    ) == pytest.approx(0.97)


def test_tail_reversal_probability_prefers_profile_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.35,
            }
        },
        "context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": 0.91,
                }
            }
        },
        "profile_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up": 0.98,
                    }
                }
            }
        },
        "microstructure_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "up_supportive": 0.97,
                    }
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        depth_pressure_score=0.35,
        profile_context_bucket="splus_hedger|up|up",
        tables=table,
    ) == pytest.approx(0.98)


def test_tail_reversal_probability_prefers_profile_price_context_fallback_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.35,
            }
        },
        "context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": 0.91,
                }
            }
        },
        "profile_price_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up_extreme": 0.99,
                    }
                }
            }
        },
        "profile_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up": 0.98,
                    }
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        profile_price_context_bucket="splus_hedger|up|up_extreme",
        profile_context_bucket="splus_hedger|up|up",
        tables=table,
    ) == pytest.approx(0.99)


def test_tail_reversal_probability_prefers_profile_price_crypto_distance_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "profile_price_crypto_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up_extreme": {
                            "far_up": 0.995,
                        }
                    }
                }
            }
        },
        "profile_price_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up_extreme": 0.99,
                    }
                }
            }
        },
        "crypto_distance_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "far_up": 0.89,
                    }
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        crypto_distance_bucket="far_up",
        profile_price_context_bucket="splus_hedger|up|up_extreme",
        tables=table,
    ) == pytest.approx(0.995)


def test_tail_reversal_probability_prefers_profile_price_execution_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "profile_price_execution_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up_extreme": {
                            "wide|medium|moderate": 0.993,
                        }
                    }
                }
            }
        },
        "profile_price_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "splus_hedger|up|up_extreme": 0.99,
                    }
                }
            }
        },
        "execution_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "wide|medium|moderate": 0.89,
                    }
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        spread=0.06,
        liquidity_depth=24.0,
        profile_price_context_bucket="splus_hedger|up|up_extreme",
        tables=table,
    ) == pytest.approx(0.993)


def test_tail_reversal_probability_prefers_crypto_distance_execution_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "crypto_distance_execution_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "far_up": {
                            "wide|medium|moderate": 0.992,
                        }
                    }
                }
            }
        },
        "crypto_distance_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "far_up": 0.89,
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
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        spread=0.06,
        liquidity_depth=24.0,
        crypto_distance_bucket="far_up",
        tables=table,
    ) == pytest.approx(0.992)


def test_tail_reversal_probability_prefers_crypto_distance_context_fallback_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.35,
            }
        },
        "context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": 0.91,
                }
            }
        },
        "crypto_distance_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "far_up": 0.89,
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
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        spread=0.06,
        liquidity_depth=24.0,
        crypto_distance_bucket="far_up",
        tables=table,
    ) == pytest.approx(0.89)


def test_tail_reversal_probability_prefers_crypto_context_pytest() -> None:
    table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {
            "touched_5c": {
                "60_180": 0.35,
            }
        },
        "context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": 0.91,
                }
            }
        },
        "crypto_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "btc|far_up": 0.96,
                    }
                }
            }
        },
        "execution_context_probabilities": {
            "touched_5c": {
                "60_180": {
                    "violent": {
                        "tight|deep|low": 0.89,
                    }
                }
            }
        },
    }

    assert tail_reversal_probability(
        touch_price_cents=5,
        time_remaining_seconds=120.0,
        inversion_score=0.0,
        volatility_score=0.9,
        spread=0.01,
        liquidity_depth=60.0,
        crypto_context_bucket="btc|far_up",
        tables=table,
    ) == pytest.approx(0.96)
