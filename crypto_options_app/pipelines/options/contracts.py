from __future__ import annotations

from dataclasses import dataclass


CRYPTO_OPTIONS_RESEARCH_VERSION = "crypto_options_research_v0_1"
CRYPTO_OPTIONS_AUDIT_SCHEMA_VERSION = "crypto_options_data_audit_v1"
CRYPTO_OPTIONS_REPORT_SCHEMA_VERSION = "crypto_options_research_report_v1"

SUPPORTED_CRYPTO_SYMBOLS = ("BTC", "ETH", "SOL", "XRP")
SUPPORTED_POLYMARKET_EVENT_TYPES = (
    "short_interval_up_down",
    "hourly_daily_up_down",
    "above_below_price_at_time",
    "range_target",
    "hit_by_date",
    "other_recurring_crypto_contract",
)

CRYPTO_CANDLE_FIELDS = (
    "symbol",
    "exchange",
    "interval",
    "opened_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
)
POLYMARKET_CRYPTO_OUTCOME_FIELDS = (
    "event_id",
    "event_slug",
    "market_id",
    "market_slug",
    "event_type",
    "primary_symbol",
    "outcome",
    "token_id",
    "cadence_seconds",
    "window_start_time",
    "window_end_time",
    "settlement_threshold",
    "condition_text",
    "resolution_source",
    "resolution_source_url",
)
CLOB_OBSERVATION_FIELDS = (
    "event_id",
    "market_id",
    "token_id",
    "observed_at",
    "mid_price",
    "best_bid",
    "best_ask",
    "bid_size",
    "ask_size",
    "depth_top3_bid_size",
    "depth_top3_ask_size",
    "spread",
    "price_meaning",
)
REFERENCE_PRICE_FIELDS = (
    "symbol",
    "feed_id",
    "query_timestamp",
    "valid_from_timestamp",
    "observations_timestamp",
    "benchmark_price",
    "bid",
    "ask",
    "source",
)
EVENT_LABEL_FIELDS = (
    "event_id",
    "market_id",
    "primary_symbol",
    "window_start_time",
    "window_end_time",
    "reference_source",
    "reference_start_price",
    "reference_end_price",
    "resolved_direction",
    "resolved_outcome",
    "label_authority",
    "label_status",
    "label_blockers",
)
EVENT_STATE_PANEL_FIELDS = (
    "event_id",
    "market_id",
    "token_id",
    "observed_at",
    "primary_symbol",
    "event_type",
    "outcome",
    "polymarket_mid_price",
    "best_bid",
    "best_ask",
    "spread",
    "underlying_close",
    "settlement_threshold",
    "cadence_seconds",
    "resolution_source",
    "window_start_time",
    "window_end_time",
    "distance_to_threshold",
    "time_to_close_seconds",
    "resolved_outcome",
    "resolved_direction",
    "reference_start_price",
    "reference_end_price",
    "label_status",
    "label_authority",
)
BACKTEST_RESULT_FIELDS = (
    "strategy_id",
    "strategy_family",
    "status",
    "trade_count",
    "blockers",
    "summary",
    "metrics",
    "execution_boundary",
)


@dataclass(frozen=True)
class CryptoOptionsAuditThresholds:
    min_events_for_discovery: int = 1
    min_matched_outcomes_for_replay: int = 2
    min_clob_observations_for_scalping: int = 50
    min_underlying_candles_for_prediction: int = 50
    min_joined_panel_rows_for_hybrid: int = 50
    max_spread_for_fillability: float = 0.04


@dataclass(frozen=True)
class CryptoOptionsDataCounts:
    event_rows: int = 0
    distinct_events: int = 0
    distinct_markets: int = 0
    matched_outcomes: int = 0
    clob_observations: int = 0
    clob_rows_with_bid_ask: int = 0
    clob_rows_with_depth: int = 0
    reference_price_reports: int = 0
    reference_label_rows: int = 0
    reference_label_rows_complete: int = 0
    underlying_candles: int = 0
    candle_symbols: int = 0
    rows_with_settlement_threshold: int = 0
    rows_with_condition_text: int = 0
    rows_with_cadence: int = 0
    rows_with_resolution_source: int = 0
    short_interval_up_down_rows: int = 0
    joined_panel_rows: int = 0


def schema_contracts() -> dict[str, tuple[str, ...]]:
    return {
        "crypto_underlying_candles": CRYPTO_CANDLE_FIELDS,
        "polymarket_crypto_events_markets_outcomes": POLYMARKET_CRYPTO_OUTCOME_FIELDS,
        "polymarket_clob_observations": CLOB_OBSERVATION_FIELDS,
        "reference_price_reports": REFERENCE_PRICE_FIELDS,
        "event_labels": EVENT_LABEL_FIELDS,
        "joined_event_state_panel": EVENT_STATE_PANEL_FIELDS,
        "replay_backtest_result": BACKTEST_RESULT_FIELDS,
    }


__all__ = [
    "BACKTEST_RESULT_FIELDS",
    "CLOB_OBSERVATION_FIELDS",
    "CRYPTO_CANDLE_FIELDS",
    "CRYPTO_OPTIONS_AUDIT_SCHEMA_VERSION",
    "CRYPTO_OPTIONS_REPORT_SCHEMA_VERSION",
    "CRYPTO_OPTIONS_RESEARCH_VERSION",
    "CryptoOptionsAuditThresholds",
    "CryptoOptionsDataCounts",
    "EVENT_STATE_PANEL_FIELDS",
    "POLYMARKET_CRYPTO_OUTCOME_FIELDS",
    "REFERENCE_PRICE_FIELDS",
    "EVENT_LABEL_FIELDS",
    "SUPPORTED_CRYPTO_SYMBOLS",
    "SUPPORTED_POLYMARKET_EVENT_TYPES",
    "schema_contracts",
]
