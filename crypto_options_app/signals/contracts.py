from __future__ import annotations


GENERATOR_OUTCOME_EXPECTATION = "outcome_expectation"
GENERATOR_HEDGE_PROPORTION = "hedge_proportion"
GENERATOR_BAND_REBOUND = "band_rebound"
GENERATOR_VOLATILITY_LIQUIDITY = "volatility_liquidity"
GENERATOR_BUYING_AHEAD = "buying_ahead"

GENERATOR_IDS = {
    GENERATOR_OUTCOME_EXPECTATION,
    GENERATOR_HEDGE_PROPORTION,
    GENERATOR_BAND_REBOUND,
    GENERATOR_VOLATILITY_LIQUIDITY,
    GENERATOR_BUYING_AHEAD,
}

ACCOUNT_TYPES = {"outcome_predictor", "hedger", "grid_buyer", "scalping_trader", "unknown"}
