from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class PriceTick:
    symbol: str
    observed_at_utc: datetime
    price: float
    volume: float = 0.0


@dataclass(frozen=True)
class OptionPairTick:
    symbol: str
    event_key: str
    observed_at_utc: datetime
    up_price: float
    down_price: float
    up_ask_depth: float = 0.0
    down_ask_depth: float = 0.0
    up_bid_depth: float = 0.0
    down_bid_depth: float = 0.0


@dataclass(frozen=True)
class IndicatorSnapshot:
    indicator_id: str
    symbol: str
    interval: str
    computed_at_utc: datetime
    direction: str
    confidence: float
    signal_value: float
    components: dict[str, Any] = field(default_factory=dict)
    quality_flags: dict[str, Any] = field(default_factory=dict)


def compute_indicator_snapshots(
    *,
    symbol: str,
    ticks: list[PriceTick],
    event_threshold_price: float | None = None,
    computed_at_utc: datetime | None = None,
) -> list[IndicatorSnapshot]:
    if not ticks:
        return []
    ordered = sorted(ticks, key=lambda tick: tick.observed_at_utc)
    computed_at = computed_at_utc or ordered[-1].observed_at_utc
    snapshots = [
        compute_target_relative_ema_momentum(
            symbol=symbol,
            ticks=ordered,
            event_threshold_price=event_threshold_price,
            computed_at_utc=computed_at,
        ),
        compute_volume_weighted_pressure(symbol=symbol, ticks=ordered, computed_at_utc=computed_at),
        compute_support_resistance_band_confluence(
            symbol=symbol,
            ticks=ordered,
            event_threshold_price=event_threshold_price,
            computed_at_utc=computed_at,
        ),
    ]
    for indicator_id, window_seconds in (
        ("volatility_per_second_5m", 300),
        ("volatility_per_second_1h", 3600),
        ("volatility_per_second_1d", 86400),
    ):
        snapshots.append(
            compute_volatility_per_second(
                symbol=symbol,
                ticks=ordered,
                computed_at_utc=computed_at,
                indicator_id=indicator_id,
                window_seconds=window_seconds,
            )
        )
    return snapshots


def compute_option_pair_indicator_snapshots(
    *,
    symbol: str,
    ticks: list[OptionPairTick],
    computed_at_utc: datetime | None = None,
) -> list[IndicatorSnapshot]:
    if not ticks:
        return []
    ordered = sorted(ticks, key=lambda tick: tick.observed_at_utc)
    computed_at = computed_at_utc or ordered[-1].observed_at_utc
    return [
        compute_option_pair_divergence(symbol=symbol, ticks=ordered, computed_at_utc=computed_at),
        compute_option_depth_pressure(symbol=symbol, ticks=ordered, computed_at_utc=computed_at),
        compute_option_price_volatility(symbol=symbol, ticks=ordered, computed_at_utc=computed_at, window_seconds=300),
        compute_pre_event_price_drift(symbol=symbol, ticks=ordered, computed_at_utc=computed_at, window_seconds=900),
    ]


def compute_option_pair_divergence(
    *,
    symbol: str,
    ticks: list[OptionPairTick],
    computed_at_utc: datetime,
) -> IndicatorSnapshot:
    latest = ticks[-1]
    value = latest.up_price - latest.down_price
    return IndicatorSnapshot(
        "option_updown_pair_divergence_v1",
        symbol.upper(),
        "event_path",
        computed_at_utc,
        _direction(value, neutral_band=0.02),
        _confidence(abs(value), scale=0.30),
        value,
        {"up_price": latest.up_price, "down_price": latest.down_price, "event_key": latest.event_key},
        {"min_samples_met": True},
    )


def compute_option_depth_pressure(
    *,
    symbol: str,
    ticks: list[OptionPairTick],
    computed_at_utc: datetime,
) -> IndicatorSnapshot:
    latest = ticks[-1]
    up_depth = max(latest.up_bid_depth, 0.0) + max(latest.down_ask_depth, 0.0)
    down_depth = max(latest.down_bid_depth, 0.0) + max(latest.up_ask_depth, 0.0)
    total = up_depth + down_depth
    value = 0.0 if total <= 0 else (up_depth - down_depth) / total
    return IndicatorSnapshot(
        "option_orderbook_depth_pressure_v1",
        symbol.upper(),
        "event_path",
        computed_at_utc,
        _direction(value, neutral_band=0.05),
        _confidence(abs(value), scale=0.50),
        value,
        {"up_depth": up_depth, "down_depth": down_depth},
        {"min_samples_met": True, "has_depth": total > 0},
    )


def compute_option_price_volatility(
    *,
    symbol: str,
    ticks: list[OptionPairTick],
    computed_at_utc: datetime,
    window_seconds: int,
) -> IndicatorSnapshot:
    cutoff = computed_at_utc - timedelta(seconds=window_seconds)
    sample = [tick for tick in ticks if tick.observed_at_utc >= cutoff]
    if len(sample) < 2:
        return IndicatorSnapshot(
            "option_pair_volatility_per_second_5m_v1",
            symbol.upper(),
            f"{window_seconds}s",
            computed_at_utc,
            "sideways",
            0.0,
            0.0,
            {"sample_count": len(sample), "window_seconds": window_seconds},
            {"min_samples_met": False},
        )
    movement = sum(
        abs(current.up_price - previous.up_price) + abs(current.down_price - previous.down_price)
        for previous, current in zip(sample, sample[1:], strict=False)
    )
    elapsed = max((sample[-1].observed_at_utc - sample[0].observed_at_utc).total_seconds(), 1.0)
    value = movement / elapsed
    return IndicatorSnapshot(
        "option_pair_volatility_per_second_5m_v1",
        symbol.upper(),
        f"{window_seconds}s",
        computed_at_utc,
        _direction((sample[-1].up_price - sample[0].up_price) - (sample[-1].down_price - sample[0].down_price), neutral_band=0.01),
        _confidence(value, scale=0.01),
        value,
        {"sample_count": len(sample), "movement": movement, "elapsed_seconds": elapsed},
        {"min_samples_met": True},
    )


def compute_pre_event_price_drift(
    *,
    symbol: str,
    ticks: list[OptionPairTick],
    computed_at_utc: datetime,
    window_seconds: int,
) -> IndicatorSnapshot:
    cutoff = computed_at_utc - timedelta(seconds=window_seconds)
    sample = [tick for tick in ticks if tick.observed_at_utc >= cutoff]
    if len(sample) < 2:
        drift = 0.0
        min_samples_met = False
    else:
        drift = (sample[-1].up_price - sample[0].up_price) - (sample[-1].down_price - sample[0].down_price)
        min_samples_met = True
    return IndicatorSnapshot(
        "pre_event_option_price_drift_15m_v1",
        symbol.upper(),
        f"{window_seconds}s",
        computed_at_utc,
        _direction(drift, neutral_band=0.02),
        _confidence(abs(drift), scale=0.25),
        drift,
        {"sample_count": len(sample), "window_seconds": window_seconds},
        {"min_samples_met": min_samples_met},
    )


def compute_target_relative_ema_momentum(
    *,
    symbol: str,
    ticks: list[PriceTick],
    event_threshold_price: float | None,
    computed_at_utc: datetime,
    short_span: int = 5,
    long_span: int = 9,
) -> IndicatorSnapshot:
    prices = [tick.price for tick in ticks]
    short_ema = ema(prices, short_span)
    long_ema = ema(prices, long_span)
    latest = prices[-1]
    momentum = short_ema - long_ema
    target_delta = None if event_threshold_price is None else latest - event_threshold_price
    signal_value = momentum if target_delta is None else momentum + (target_delta / max(abs(event_threshold_price), 1.0))
    direction = _direction(signal_value, neutral_band=0.00001)
    confidence = _confidence(abs(signal_value), scale=0.001)
    return IndicatorSnapshot(
        "target_relative_ema_momentum_v1",
        symbol,
        "5m",
        computed_at_utc,
        direction,
        confidence,
        signal_value,
        {"short_ema": short_ema, "long_ema": long_ema, "latest_price": latest, "target_delta": target_delta},
        {"min_samples_met": len(prices) >= long_span},
    )


def compute_volume_weighted_pressure(
    *,
    symbol: str,
    ticks: list[PriceTick],
    computed_at_utc: datetime,
) -> IndicatorSnapshot:
    pressure = 0.0
    total_weight = 0.0
    for previous, current in zip(ticks, ticks[1:], strict=False):
        weight = current.volume if current.volume > 0 else 1.0
        pressure += math.copysign(weight * abs(current.price - previous.price), current.price - previous.price)
        total_weight += weight
    signal_value = pressure / total_weight if total_weight else 0.0
    return IndicatorSnapshot(
        "volume_weighted_pressure_v1",
        symbol,
        "5m",
        computed_at_utc,
        _direction(signal_value, neutral_band=0.00001),
        _confidence(abs(signal_value), scale=0.001),
        signal_value,
        {"pressure": pressure, "total_weight": total_weight},
        {"min_samples_met": len(ticks) >= 3},
    )


def compute_support_resistance_band_confluence(
    *,
    symbol: str,
    ticks: list[PriceTick],
    event_threshold_price: float | None,
    computed_at_utc: datetime,
    window: int = 20,
    stdevs: float = 2.0,
) -> IndicatorSnapshot:
    prices = [tick.price for tick in ticks[-window:]]
    latest = prices[-1]
    mean = sum(prices) / len(prices)
    variance = sum((price - mean) ** 2 for price in prices) / len(prices)
    stdev = math.sqrt(variance)
    support = mean - stdevs * stdev
    resistance = mean + stdevs * stdev
    if latest >= resistance:
        direction = "down"
        signal_value = resistance - latest
    elif latest <= support:
        direction = "up"
        signal_value = support - latest
    else:
        target_delta = 0.0 if event_threshold_price is None else latest - event_threshold_price
        direction = _direction(target_delta, neutral_band=max(stdev, 1.0) * 0.05)
        signal_value = target_delta
    distance_to_band = min(abs(latest - support), abs(latest - resistance))
    confidence = 1.0 - min(1.0, distance_to_band / max(stdev * stdevs, 1.0))
    return IndicatorSnapshot(
        "support_resistance_band_confluence_v1",
        symbol,
        "5m",
        computed_at_utc,
        direction,
        round(confidence, 6),
        signal_value,
        {"mean": mean, "support": support, "resistance": resistance, "latest_price": latest},
        {"min_samples_met": len(prices) >= min(window, 3)},
    )


def compute_volatility_per_second(
    *,
    symbol: str,
    ticks: list[PriceTick],
    computed_at_utc: datetime,
    indicator_id: str,
    window_seconds: int,
) -> IndicatorSnapshot:
    cutoff = computed_at_utc - timedelta(seconds=window_seconds)
    sample = [tick for tick in ticks if tick.observed_at_utc >= cutoff]
    if len(sample) < 2:
        return IndicatorSnapshot(
            indicator_id,
            symbol,
            f"{window_seconds}s",
            computed_at_utc,
            "sideways",
            0.0,
            0.0,
            {"sample_count": len(sample), "window_seconds": window_seconds},
            {"min_samples_met": False},
        )
    movement = sum(abs(current.price - previous.price) for previous, current in zip(sample, sample[1:], strict=False))
    elapsed = max((sample[-1].observed_at_utc - sample[0].observed_at_utc).total_seconds(), 1.0)
    volatility = movement / elapsed
    direction = _direction(sample[-1].price - sample[0].price, neutral_band=0.00001)
    return IndicatorSnapshot(
        indicator_id,
        symbol,
        f"{window_seconds}s",
        computed_at_utc,
        direction,
        _confidence(volatility, scale=0.5),
        volatility,
        {"sample_count": len(sample), "movement": movement, "elapsed_seconds": elapsed},
        {"min_samples_met": True},
    )


def ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    current = values[0]
    for value in values[1:]:
        current = alpha * value + (1.0 - alpha) * current
    return current


def price_tick_from_mapping(row: dict[str, Any]) -> PriceTick:
    observed = row.get("observed_at_utc")
    if isinstance(observed, datetime):
        observed_at = observed
    else:
        observed_at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return PriceTick(
        symbol=str(row["symbol"]),
        observed_at_utc=observed_at.astimezone(UTC),
        price=float(row["price"]),
        volume=float(row.get("volume") or 0.0),
    )


def _direction(value: float, *, neutral_band: float) -> str:
    if value > neutral_band:
        return "up"
    if value < -neutral_band:
        return "down"
    return "sideways"


def _confidence(value: float, *, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return round(min(1.0, value / scale), 6)
