from __future__ import annotations

from typing import Any


def crypto_options_data_source_registry() -> list[dict[str, Any]]:
    """Document free/public sources the crypto-options service can consume.

    The registry is intentionally descriptive. It does not create API clients that can trade.
    """

    return [
        {
            "source_id": "polymarket_gamma_events",
            "category": "polymarket_metadata",
            "url": "https://gamma-api.polymarket.com/events",
            "documentation_url": "https://docs.polymarket.com/market-data/fetching-markets",
            "auth_required": False,
            "historical_use": "Discover event/market/outcome metadata, condition text, token ids, and windows.",
            "live_use": "Discover active recurring crypto events.",
            "granularity": "event/market",
            "implementation_status": "implemented_read_only",
            "caveats": ["Slug semantics must be validated against condition text and timestamps."],
        },
        {
            "source_id": "polymarket_clob_prices_history",
            "category": "polymarket_odds_history",
            "url": "https://clob.polymarket.com/prices-history",
            "documentation_url": "https://docs.polymarket.com/api-reference/markets/get-prices-history",
            "auth_required": False,
            "historical_use": "Outcome-token odds path and settlement-convergence research.",
            "live_use": "Not a live stream; use WebSocket for live book state.",
            "granularity": "minute-fidelity history by default",
            "implementation_status": "implemented_normalizer_and_public_fetcher",
            "caveats": ["Not BTC price.", "No queue position.", "Not enough for executable scalping alone."],
        },
        {
            "source_id": "polymarket_clob_websocket",
            "category": "polymarket_orderbook_stream",
            "url": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            "documentation_url": "https://docs.polymarket.com/developers/CLOB/websocket/market-channel",
            "auth_required": False,
            "historical_use": "Only available if Janus records snapshots and incremental updates.",
            "live_use": "Book, price_change, last_trade_price, and tick_size_change capture.",
            "granularity": "event-level stream",
            "implementation_status": "implemented_read_only_live_capture",
            "caveats": ["Must archive locally for replay.", "Still not an order execution module."],
        },
        {
            "source_id": "pmxt_polymarket_v2_archive",
            "category": "historical_orderbook_archive",
            "url": "https://archive.pmxt.dev/Polymarket/v2",
            "documentation_url": "https://archive.pmxt.dev/Polymarket/v2",
            "auth_required": False,
            "historical_use": "Free hourly Parquet snapshots for orderbook backtesting research.",
            "live_use": "None; historical archive.",
            "granularity": "hourly snapshot files",
            "implementation_status": "implemented_parquet_importer_and_event_hour_downloader",
            "caveats": ["External schema must be pinned before production replay.", "Snapshot cadence may miss intrasecond queue dynamics."],
        },
        {
            "source_id": "chainlink_btc_usd_data_stream",
            "category": "settlement_reference",
            "url": "https://data.chain.link/streams/btc-usd",
            "documentation_url": "https://docs.chain.link/data-streams",
            "auth_required": True,
            "historical_use": "Canonical boundary labels for BTC up/down when condition text names Chainlink.",
            "live_use": "Reference stream for timestamped benchmark price capture.",
            "granularity": "sub-second product, access-dependent",
            "implementation_status": "implemented_authenticated_rest_provider_and_decoded_csv_json_ingestion",
            "caveats": ["Credentials/access may be required.", "Boundary mapping must be empirically validated against resolved markets."],
        },
        {
            "source_id": "binance_spot_klines",
            "category": "exchange_candles",
            "url": "https://data-api.binance.vision/api/v3/klines",
            "documentation_url": "https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klinecandlestick-data",
            "auth_required": False,
            "historical_use": "BTCUSDT/BTCUSDC candles for technical indicators and explanatory features.",
            "live_use": "Public market-data polling; WebSocket needed for lower latency.",
            "granularity": "1s+ supported by Binance docs; 1m is first stable baseline",
            "implementation_status": "planned_provider",
            "caveats": ["Exchange feed is not the settlement label unless the market states it."],
        },
        {
            "source_id": "coinbase_public_product_candles",
            "category": "exchange_candles",
            "url": "https://api.coinbase.com/api/v3/brokerage/market/products/BTC-USD/candles",
            "documentation_url": "https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-candles",
            "auth_required": "public_endpoint_docs_show_bearer_header",
            "historical_use": "Independent BTC-USD candle cross-check and feature source.",
            "live_use": "Public product candles and market data.",
            "granularity": "ONE_MINUTE and higher",
            "implementation_status": "planned_provider",
            "caveats": ["Use as explanatory source, not settlement truth for Chainlink-resolved contracts."],
        },
        {
            "source_id": "kraken_ohlc_websocket",
            "category": "exchange_live_candles",
            "url": "wss://ws.kraken.com/v2",
            "documentation_url": "https://docs.kraken.com/api/docs/websocket-v2/ohlc/",
            "auth_required": False,
            "historical_use": "Limited; REST or archived stream needed for backfill.",
            "live_use": "OHLC updates generated on trade events.",
            "granularity": "streaming OHLC intervals",
            "implementation_status": "planned_collector",
            "caveats": ["Needs local archive before replay use."],
        },
        {
            "source_id": "coindesk_cryptocompare_histominute",
            "category": "aggregated_crypto_history",
            "url": "https://min-api.cryptocompare.com/data/v2/histominute",
            "documentation_url": "https://developers.coindesk.com/documentation/legacy/Historical/dataHistominute",
            "auth_required": "free_tier_key_may_be_required",
            "historical_use": "Fallback 1m OHLCV sanity checks when exchange backfill is unavailable.",
            "live_use": "Not preferred for live 5m execution research.",
            "granularity": "1m",
            "implementation_status": "planned_optional",
            "caveats": ["Aggregator latency and methodology differ from Chainlink and exchange feeds."],
        },
    ]


__all__ = ["crypto_options_data_source_registry"]
