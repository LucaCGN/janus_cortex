from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IndicatorDefinition:
    indicator_id: str
    name: str
    description: str
    config: dict[str, Any]


DEFAULT_INDICATORS: tuple[IndicatorDefinition, ...] = (
    IndicatorDefinition(
        "target_relative_ema_momentum_v1",
        "Target-relative EMA momentum",
        "Compares short and long EMA direction against event target-relative price.",
        {"short_span": 5, "long_span": 9, "min_samples": 9},
    ),
    IndicatorDefinition(
        "volume_weighted_pressure_v1",
        "Volume-weighted pressure",
        "Estimates directional pressure from signed price deltas weighted by observed volume.",
        {"min_samples": 3},
    ),
    IndicatorDefinition(
        "support_resistance_band_confluence_v1",
        "Support/resistance band confluence",
        "Uses Bollinger-style bands to flag price near local support or resistance relative to target.",
        {"window": 20, "stdevs": 2},
    ),
    IndicatorDefinition(
        "volatility_per_second_5m",
        "Volatility per second 5m",
        "Computes absolute price movement per second over the latest 5-minute sample.",
        {"window_seconds": 300},
    ),
    IndicatorDefinition(
        "volatility_per_second_1h",
        "Volatility per second 1h",
        "Computes absolute price movement per second over the latest 1-hour sample.",
        {"window_seconds": 3600},
    ),
    IndicatorDefinition(
        "volatility_per_second_1d",
        "Volatility per second 1d",
        "Computes absolute price movement per second over the latest 1-day sample.",
        {"window_seconds": 86400},
    ),
    IndicatorDefinition(
        "option_updown_pair_divergence_v1",
        "Option Up/Down pair divergence",
        "Compares executable Up and Down option prices for the same event.",
        {"source_table": "polymarket_updown_pair_snapshots"},
    ),
    IndicatorDefinition(
        "option_orderbook_depth_pressure_v1",
        "Option order-book depth pressure",
        "Estimates directional pressure from paired Up/Down visible depth.",
        {"source_table": "polymarket_order_book_levels", "depth_window": "top3"},
    ),
    IndicatorDefinition(
        "option_pair_volatility_per_second_5m_v1",
        "Option pair volatility per second 5m",
        "Computes paired Up/Down option price movement per second over the latest 5-minute sample.",
        {"window_seconds": 300},
    ),
    IndicatorDefinition(
        "pre_event_option_price_drift_15m_v1",
        "Pre-event option price drift 15m",
        "Measures Up/Down price drift in the 15-minute pre-event capture window.",
        {"window_seconds": 900},
    ),
)


def indicator_definition_map() -> dict[str, IndicatorDefinition]:
    return {definition.indicator_id: definition for definition in DEFAULT_INDICATORS}
