"""Read-only crypto market data adapters for research lanes."""

from crypto_options_app.data_nodes.crypto.candles import (
    CryptoCandleSourceUnavailable,
    CsvCryptoCandleProvider,
    NoConfiguredCryptoCandleProvider,
    fetch_binance_candles_for_events,
    normalize_candle_records,
)
from crypto_options_app.data_nodes.crypto.reference import (
    ChainlinkDataStreamsRestProvider,
    CryptoReferenceSourceUnavailable,
    fetch_chainlink_reference_reports_for_events,
    normalize_reference_price_reports,
    reference_reports_from_candles,
)

__all__ = [
    "ChainlinkDataStreamsRestProvider",
    "CryptoCandleSourceUnavailable",
    "CryptoReferenceSourceUnavailable",
    "CsvCryptoCandleProvider",
    "NoConfiguredCryptoCandleProvider",
    "fetch_binance_candles_for_events",
    "fetch_chainlink_reference_reports_for_events",
    "normalize_candle_records",
    "normalize_reference_price_reports",
    "reference_reports_from_candles",
]
