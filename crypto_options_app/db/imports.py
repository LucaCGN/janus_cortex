from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect, connect_read_only
from crypto_options_app.db.schema import create_schema


def import_legacy_shards(
    *,
    target_db_path: str | Path,
    profile_db_path: str | Path | None = None,
    market_db_path: str | Path | None = None,
) -> dict[str, Any]:
    with connect(target_db_path) as target:
        create_schema(target)
        profile_counts = import_legacy_profile_shard(target, profile_db_path) if profile_db_path else {}
        market_counts = import_legacy_market_shard(target, market_db_path) if market_db_path else {}
        return {
            "schema_version": "crypto_options_app_legacy_import_v1",
            "target_db_path": str(target_db_path),
            "profile": profile_counts,
            "market": market_counts,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }


def import_legacy_profile_shard(target: sqlite3.Connection, profile_db_path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect_read_only(profile_db_path) as source:
        now = _now()
        if _table_exists(source, "profile_universe"):
            for row in source.execute("SELECT * FROM profile_universe"):
                profile_key = _value(row, "profile_key")
                if not profile_key:
                    continue
                _upsert(
                    target,
                    "profiles",
                    {
                        "profile_key": profile_key,
                        "normalized_ref": _value(row, "normalized_ref"),
                        "handle": _value(row, "handle"),
                        "proxy_wallet": _value(row, "proxy_wallet"),
                        "profile_name": _value(row, "profile_name"),
                        "first_seen_at_utc": _value(row, "first_seen_at_utc", now),
                        "last_seen_at_utc": _value(row, "last_seen_at_utc", now),
                        "latest_activity_utc": _value(row, "latest_activity_utc"),
                        "source_table": "profile_universe",
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("profile_key",),
                )
            counts["profiles"] = _count(target, "profiles")

        if _table_exists(source, "profile_refs"):
            for row in source.execute("SELECT * FROM profile_refs"):
                ref_key = _value(row, "normalized_ref") or _stable_key("profile_ref", _json_row(row))
                _upsert(
                    target,
                    "profile_refs",
                    {
                        "ref_key": ref_key,
                        "profile_key": _value(row, "profile_key"),
                        "normalized_ref": _value(row, "normalized_ref"),
                        "raw_ref": _value(row, "raw_ref"),
                        "handle": _value(row, "handle"),
                        "address": _value(row, "address"),
                        "source": _value(row, "source"),
                        "first_seen_at_utc": _value(row, "first_seen_at_utc", now),
                        "last_seen_at_utc": _value(row, "last_seen_at_utc", now),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("ref_key",),
                )
            counts["profile_refs"] = _count(target, "profile_refs")

        if _table_exists(source, "profile_fetch_runs"):
            for row in source.execute("SELECT * FROM profile_fetch_runs"):
                _upsert(
                    target,
                    "profile_fetch_runs",
                    {
                        "fetch_run_id": _value(row, "fetch_run_id") or _stable_key("fetch_run", _json_row(row)),
                        "started_at_utc": _value(row, "started_at_utc", now),
                        "completed_at_utc": _value(row, "completed_at_utc"),
                        "status": _value(row, "status", "unknown"),
                        "requested_ref_count": _int(_value(row, "requested_ref_count")),
                        "fetched_profile_count": _int(_value(row, "fetched_profile_count")),
                        "failed_profile_count": _int(_value(row, "failed_profile_count")),
                        "max_concurrency": _int(_value(row, "max_concurrency")),
                        "page_limit": _int(_value(row, "page_limit")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                    },
                    pk=("fetch_run_id",),
                )
            counts["profile_fetch_runs"] = _count(target, "profile_fetch_runs")

        if _table_exists(source, "profile_raw_activity"):
            for row in source.execute("SELECT * FROM profile_raw_activity"):
                raw_key = _value(row, "raw_activity_key") or _stable_key("raw_activity", _json_row(row))
                event_key = _event_key(row)
                event_token_key = _ensure_profile_event_token(target, row, now)
                _upsert(
                    target,
                    "profile_raw_activity",
                    {
                        "raw_activity_key": raw_key,
                        "profile_key": _value(row, "profile_key"),
                        "event_key": event_key,
                        "event_slug": _value(row, "event_slug"),
                        "condition_id": _value(row, "condition_id"),
                        "market_slug": _value(row, "market_slug"),
                        "symbol": _value(row, "symbol"),
                        "order_side": _value(row, "order_side"),
                        "outcome_side": _value(row, "outcome_side"),
                        "token_id": _value(row, "token_id"),
                        "price": _float(_value(row, "price")),
                        "shares": _float(_value(row, "shares")),
                        "notional_usd": _float(_value(row, "notional_usd")),
                        "activity_at_utc": _value(row, "activity_at_utc"),
                        "observed_at_utc": _value(row, "observed_at_utc", now),
                        "event_start_time_utc": _value(row, "event_start_time_utc"),
                        "event_end_time_utc": _value(row, "event_end_time_utc"),
                        "seconds_before_event_start": _float(_value(row, "seconds_before_event_start")),
                        "buying_ahead": _int(_value(row, "buying_ahead")),
                        "active_during_event": _int(_value(row, "active_during_event")),
                        "source_table": "profile_raw_activity",
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                    },
                    pk=("raw_activity_key",),
                )
                _upsert(
                    target,
                    "profile_event_orders",
                    {
                        "profile_event_order_key": raw_key,
                        "profile_key": _value(row, "profile_key"),
                        "event_key": event_key,
                        "event_token_key": event_token_key,
                        "raw_activity_key": raw_key,
                        "order_side": _value(row, "order_side"),
                        "outcome": _value(row, "outcome_side"),
                        "price": _float(_value(row, "price")),
                        "shares": _float(_value(row, "shares")),
                        "notional_usd": _float(_value(row, "notional_usd")),
                        "activity_at_utc": _value(row, "activity_at_utc"),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                    },
                    pk=("profile_event_order_key",),
                )
            counts["profile_raw_activity"] = _count(target, "profile_raw_activity")
            counts["profile_event_orders"] = _count(target, "profile_event_orders")

        if _table_exists(source, "profile_grade_current"):
            for row in source.execute("SELECT * FROM profile_grade_current"):
                profile_key = _value(row, "profile_key")
                if not profile_key:
                    continue
                _upsert(
                    target,
                    "profile_grades",
                    {
                        "profile_key": profile_key,
                        "evaluated_at_utc": _value(row, "evaluated_at_utc", now),
                        "grade": _value(row, "grade", "U"),
                        "score": _float(_value(row, "score")),
                        "trading_style": _value(row, "trading_style"),
                        "trading_style_detail": _value(row, "trading_style_detail"),
                        "frequency_class": _value(row, "frequency_class"),
                        "source_table": "profile_grade_current",
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("profile_key",),
                )
            counts["profile_grades"] = _count(target, "profile_grades")

        if _table_exists(source, "profile_grade_history"):
            for row in source.execute("SELECT * FROM profile_grade_history"):
                key = _stable_key("grade_history", _value(row, "profile_key"), _value(row, "evaluated_at_utc"), _value(row, "grade"), _json_row(row))
                _upsert(
                    target,
                    "profile_grade_history",
                    {
                        "grade_history_key": key,
                        "profile_key": _value(row, "profile_key"),
                        "evaluated_at_utc": _value(row, "evaluated_at_utc", now),
                        "grade": _value(row, "grade", "U"),
                        "score": _float(_value(row, "score")),
                        "source_table": "profile_grade_history",
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                    },
                    pk=("grade_history_key",),
                )
            counts["profile_grade_history"] = _count(target, "profile_grade_history")

        if _table_exists(source, "profile_period_performance"):
            for row in source.execute("SELECT * FROM profile_period_performance"):
                key = _stable_key("period", _value(row, "profile_key"), _value(row, "period"), _value(row, "evaluated_at_utc"))
                _upsert(
                    target,
                    "profile_period_performance",
                    {
                        "period_performance_key": key,
                        "profile_key": _value(row, "profile_key"),
                        "period": _value(row, "period", "unknown"),
                        "evaluated_at_utc": _value(row, "evaluated_at_utc"),
                        "pnl_usd": _float(_value(row, "pnl_usd")),
                        "closed_win_rate": _float(_value(row, "closed_win_rate")),
                        "closed_return_pct": _float(_value(row, "closed_return_pct")),
                        "reconstructed_event_win_rate": _float(_value(row, "reconstructed_event_win_rate")),
                        "reconstructed_event_return_pct": _float(_value(row, "reconstructed_event_return_pct")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                    },
                    pk=("period_performance_key",),
                )
            counts["profile_period_performance"] = _count(target, "profile_period_performance")

        if _table_exists(source, "profile_signal_generator_scores"):
            for row in source.execute("SELECT * FROM profile_signal_generator_scores"):
                key = _stable_key("generator", _value(row, "profile_key"), _value(row, "generator_id"), _value(row, "evaluated_at_utc"), _json_row(row))
                _upsert(
                    target,
                    "profile_generator_scores",
                    {
                        "generator_score_key": key,
                        "profile_key": _value(row, "profile_key"),
                        "generator_id": _value(row, "generator_id", "unknown"),
                        "evaluated_at_utc": _value(row, "evaluated_at_utc", now),
                        "account_type": _value(row, "account_type"),
                        "grade": _value(row, "grade"),
                        "score": _float(_value(row, "score"), 0.0),
                        "status": _value(row, "status", "unknown"),
                        "can_emit_live": _int(_value(row, "can_emit_live")),
                        "usage_eligible": _int(_value(row, "usage_eligible")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("generator_score_key",),
                )
            counts["profile_generator_scores"] = _count(target, "profile_generator_scores")

        if _table_exists(source, "profile_event_reconstructions"):
            for row in source.execute("SELECT * FROM profile_event_reconstructions"):
                event_key = _value(row, "event_key") or _value(row, "event_slug") or "unknown_event"
                key = _stable_key("reconstruction", _value(row, "profile_key"), event_key, _value(row, "evaluated_at_utc"), _json_row(row))
                _upsert(
                    target,
                    "profile_event_reconstructions",
                    {
                        "reconstruction_key": key,
                        "profile_key": _value(row, "profile_key"),
                        "event_key": event_key,
                        "event_slug": _value(row, "event_slug"),
                        "evaluated_at_utc": _value(row, "evaluated_at_utc"),
                        "event_style": _value(row, "event_style"),
                        "buy_count": _int(_value(row, "buy_count")),
                        "sell_count": _int(_value(row, "sell_count")),
                        "up_open_shares": _float(_value(row, "up_open_shares")),
                        "down_open_shares": _float(_value(row, "down_open_shares")),
                        "realized_pnl_usd": _float(_value(row, "realized_pnl_usd")),
                        "event_pnl_usd": _float(_value(row, "event_pnl_usd")),
                        "event_effective_win": _int(_value(row, "event_effective_win")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("reconstruction_key",),
                )
            counts["profile_event_reconstructions"] = _count(target, "profile_event_reconstructions")

        if _table_exists(source, "profile_event_timing_links"):
            for row in source.execute("SELECT * FROM profile_event_timing_links WHERE buying_ahead=1"):
                event_key = _event_key(row)
                key = _stable_key("buying_ahead", _value(row, "profile_key"), event_key, _value(row, "raw_activity_key"))
                _upsert(
                    target,
                    "profile_buying_ahead",
                    {
                        "buying_ahead_key": key,
                        "profile_key": _value(row, "profile_key"),
                        "event_key": event_key,
                        "raw_activity_key": _value(row, "raw_activity_key"),
                        "token_id": _value(row, "token_id"),
                        "outcome_side": _value(row, "outcome_side"),
                        "activity_at_utc": _value(row, "activity_at_utc"),
                        "event_start_time_utc": _value(row, "event_start_time_utc"),
                        "seconds_before_event_start": _float(_value(row, "seconds_before_event_start")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                    },
                    pk=("buying_ahead_key",),
                )
            counts["profile_buying_ahead"] = _count(target, "profile_buying_ahead")

    target.commit()
    return counts


def import_legacy_market_shard(target: sqlite3.Connection, market_db_path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect_read_only(market_db_path) as source:
        now = _now()
        token_lookup: dict[str, str] = {}
        if _table_exists(source, "polymarket_crypto_event_universe"):
            for row in source.execute("SELECT * FROM polymarket_crypto_event_universe"):
                event_key = _event_key(row)
                event_token_key = _value(row, "event_token_key") or _event_token_key_from_row(row)
                token_id = _value(row, "token_id")
                if token_id and event_token_key:
                    token_lookup[token_id] = event_token_key
                _upsert(
                    target,
                    "events",
                    {
                        "event_key": event_key,
                        "event_slug": _value(row, "event_slug"),
                        "condition_id": _value(row, "condition_id"),
                        "market_id": _value(row, "market_id"),
                        "market_slug": _value(row, "market_slug"),
                        "symbol": _value(row, "symbol"),
                        "cadence_seconds": _int(_value(row, "cadence_seconds")),
                        "event_start_time_utc": _value(row, "event_start_time_utc"),
                        "event_end_time_utc": _value(row, "event_end_time_utc"),
                        "settlement_threshold": _float(_value(row, "settlement_threshold")),
                        "source_table": "polymarket_crypto_event_universe",
                        "source_json": _json_row(row),
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("event_key",),
                )
                if token_id and event_token_key:
                    _upsert(
                        target,
                        "event_tokens",
                        {
                            "event_token_key": event_token_key,
                            "event_key": event_key,
                            "token_id": token_id,
                            "outcome": _value(row, "outcome", "Unknown"),
                            "condition_id": _value(row, "condition_id"),
                            "event_slug": _value(row, "event_slug"),
                            "market_id": _value(row, "market_id"),
                            "symbol": _value(row, "symbol"),
                            "active": _int(_value(row, "active"), 1),
                            "closed": _int(_value(row, "closed")),
                            "source_table": "polymarket_crypto_event_universe",
                            "source_json": _json_row(row),
                            "inserted_at_utc": now,
                            "updated_at_utc": now,
                        },
                        pk=("event_token_key",),
                    )
            counts["events"] = _count(target, "events")
            counts["event_tokens"] = _count(target, "event_tokens")

        if _table_exists(source, "polymarket_event_price_ticks"):
            for row in source.execute("SELECT * FROM polymarket_event_price_ticks"):
                token_id = _value(row, "token_id")
                event_token_key = token_lookup.get(str(token_id)) if token_id else None
                _upsert(
                    target,
                    "polymarket_price_ticks",
                    {
                        "price_tick_key": _value(row, "event_price_tick_key") or _stable_key("price", _json_row(row)),
                        "event_token_key": event_token_key,
                        "event_key": _event_key(row),
                        "token_id": token_id,
                        "event_slug": _value(row, "event_slug"),
                        "outcome": _value(row, "outcome"),
                        "chart_timestamp_utc": _value(row, "chart_timestamp_utc"),
                        "system_received_at_utc": _value(row, "system_received_at_utc", now),
                        "system_inserted_at_utc": _value(row, "system_inserted_at_utc", now),
                        "source_latency_ms": _float(_value(row, "source_latency_ms")),
                        "insert_latency_ms": _float(_value(row, "insert_latency_ms")),
                        "mid_price": _float(_value(row, "mid_price")),
                        "best_bid": _float(_value(row, "best_bid")),
                        "best_ask": _float(_value(row, "best_ask")),
                        "spread": _float(_value(row, "spread")),
                        "depth_top3_bid_size": _float(_value(row, "depth_top3_bid_size")),
                        "depth_top3_ask_size": _float(_value(row, "depth_top3_ask_size")),
                        "trade_price": _float(_value(row, "trade_price")),
                        "trade_size": _float(_value(row, "trade_size")),
                        "source_json": _json_row(row),
                    },
                    pk=("price_tick_key",),
                )
            counts["polymarket_price_ticks"] = _count(target, "polymarket_price_ticks")

        if _table_exists(source, "crypto_price_ticks"):
            for row in source.execute("SELECT * FROM crypto_price_ticks"):
                _upsert(
                    target,
                    "underlying_price_ticks",
                    {
                        "tick_key": _value(row, "tick_key") or _stable_key("tick", _json_row(row)),
                        "symbol": _value(row, "symbol"),
                        "source": _value(row, "source", "unknown"),
                        "observed_at_utc": _value(row, "observed_at_utc", now),
                        "exchange_timestamp_utc": _value(row, "exchange_timestamp_utc"),
                        "price": _float(_value(row, "price"), 0.0),
                        "bid": _float(_value(row, "bid")),
                        "ask": _float(_value(row, "ask")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": _value(row, "inserted_at_utc", now),
                    },
                    pk=("tick_key",),
                )
            counts["underlying_price_ticks"] = _count(target, "underlying_price_ticks")

        if _table_exists(source, "crypto_candles"):
            for row in source.execute("SELECT * FROM crypto_candles"):
                _upsert(
                    target,
                    "underlying_candles",
                    {
                        "candle_key": _value(row, "candle_key") or _stable_key("candle", _json_row(row)),
                        "symbol": _value(row, "symbol"),
                        "exchange": _value(row, "exchange", "unknown"),
                        "interval": _value(row, "interval", "unknown"),
                        "opened_at_utc": _value(row, "opened_at_utc", now),
                        "closed_at_utc": _value(row, "closed_at_utc"),
                        "open": _float(_value(row, "open"), 0.0),
                        "high": _float(_value(row, "high"), 0.0),
                        "low": _float(_value(row, "low"), 0.0),
                        "close": _float(_value(row, "close"), 0.0),
                        "volume": _float(_value(row, "volume")),
                        "source_json": _json_row(row),
                        "inserted_at_utc": _value(row, "inserted_at_utc", now),
                    },
                    pk=("candle_key",),
                )
            counts["underlying_candles"] = _count(target, "underlying_candles")

        if _table_exists(source, "crypto_indicator_snapshots"):
            for row in source.execute("SELECT * FROM crypto_indicator_snapshots"):
                indicator_id = _value(row, "indicator_id", "unknown")
                _upsert(
                    target,
                    "indicator_definitions",
                    {
                        "indicator_id": indicator_id,
                        "name": indicator_id,
                        "description": None,
                        "config_json": "{}",
                        "inserted_at_utc": now,
                        "updated_at_utc": now,
                    },
                    pk=("indicator_id",),
                )
                _upsert(
                    target,
                    "indicator_snapshots",
                    {
                        "snapshot_key": _value(row, "snapshot_key") or _stable_key("indicator", _json_row(row)),
                        "symbol": _value(row, "symbol"),
                        "interval": _value(row, "interval", "unknown"),
                        "indicator_id": indicator_id,
                        "computed_at_utc": _value(row, "computed_at_utc", now),
                        "direction": _value(row, "direction"),
                        "confidence": _float(_value(row, "confidence")),
                        "signal_value": _float(_value(row, "signal_value")),
                        "components_json": _value(row, "components_json", "{}"),
                        "quality_flags_json": _value(row, "quality_flags_json", "{}"),
                        "source_json": _json_row(row),
                        "inserted_at_utc": _value(row, "inserted_at_utc", now),
                    },
                    pk=("snapshot_key",),
                )
            counts["indicator_definitions"] = _count(target, "indicator_definitions")
            counts["indicator_snapshots"] = _count(target, "indicator_snapshots")

        if _table_exists(source, "crypto_event_indicator_context"):
            for row in source.execute("SELECT * FROM crypto_event_indicator_context"):
                _upsert(
                    target,
                    "event_indicator_context",
                    {
                        "context_key": _value(row, "context_key") or _stable_key("context", _json_row(row)),
                        "event_key": _value(row, "event_id") or _value(row, "market_slug"),
                        "event_token_key": None,
                        "symbol": _value(row, "symbol"),
                        "side": _value(row, "side"),
                        "event_threshold_price": _float(_value(row, "event_threshold_price")),
                        "computed_at_utc": _value(row, "computed_at_utc", now),
                        "underlying_price": _float(_value(row, "underlying_price")),
                        "target_delta_abs": _float(_value(row, "target_delta_abs")),
                        "target_delta_signed_for_side": _float(_value(row, "target_delta_signed_for_side")),
                        "indicator_summary_json": _value(row, "indicator_summary_json", "{}"),
                        "blocker_summary_json": _value(row, "blocker_summary_json", "{}"),
                        "inserted_at_utc": _value(row, "inserted_at_utc", now),
                    },
                    pk=("context_key",),
                )
            counts["event_indicator_context"] = _count(target, "event_indicator_context")

        if _table_exists(source, "crypto_market_data_watermarks"):
            for row in source.execute("SELECT * FROM crypto_market_data_watermarks"):
                _upsert(
                    target,
                    "data_service_watermarks",
                    {
                        "service_name": _value(row, "service_name"),
                        "module_id": "market_data",
                        "last_run_at_utc": _value(row, "last_run_at_utc"),
                        "status": _value(row, "status", "unknown"),
                        "source": "legacy_market_shard",
                        "rows_observed": 0,
                        "rows_inserted": 0,
                        "error_count": 0,
                        "state_json": _value(row, "state_json", "{}"),
                        "updated_at_utc": now,
                    },
                    pk=("service_name", "module_id"),
                )
            counts["data_service_watermarks"] = _count(target, "data_service_watermarks")

    target.commit()
    return counts


def run_import_parity_checks(
    *,
    target_db_path: str | Path,
    profile_db_path: str | Path | None = None,
    market_db_path: str | Path | None = None,
) -> dict[str, Any]:
    with connect(target_db_path) as target:
        profile_source = connect_read_only(profile_db_path) if profile_db_path else None
        market_source = connect_read_only(market_db_path) if market_db_path else None
        try:
            checks = {
                "profile_count": _count_check(profile_source, "profile_universe", target, "profiles"),
                "raw_activity_count": _count_check(profile_source, "profile_raw_activity", target, "profile_raw_activity"),
                "event_token_count": _count_check(market_source, "polymarket_crypto_event_universe", target, "event_tokens"),
                "latest_price_rows": _latest_price_check(market_source, target),
                "generator_scores": _count_check(profile_source, "profile_signal_generator_scores", target, "profile_generator_scores"),
                "buying_ahead_duplicates": _buying_ahead_duplicate_check(target),
                "legacy_source_table_collisions": _legacy_collision_check(profile_source, market_source),
            }
            passed = all(check["passed"] for check in checks.values())
            return {
                "schema_version": "crypto_options_app_import_parity_v1",
                "passed": passed,
                "checks": checks,
                "orders_allowed": False,
                "live_trading_authorized": False,
            }
        finally:
            if profile_source is not None:
                profile_source.close()
            if market_source is not None:
                market_source.close()


def _count_check(source: sqlite3.Connection | None, source_table: str, target: sqlite3.Connection, target_table: str) -> dict[str, Any]:
    source_count = _count(source, source_table) if source is not None and _table_exists(source, source_table) else 0
    target_count = _count(target, target_table)
    return {"source": source_count, "target": target_count, "passed": target_count >= source_count}


def _latest_price_check(source: sqlite3.Connection | None, target: sqlite3.Connection) -> dict[str, Any]:
    source_count = _count(source, "polymarket_event_price_ticks") if source is not None and _table_exists(source, "polymarket_event_price_ticks") else 0
    target_latest = target.execute("SELECT COUNT(*) AS c FROM v_crypto_options_app_latest_polymarket_prices").fetchone()["c"]
    return {"source": source_count, "target_latest": int(target_latest), "passed": source_count == 0 or int(target_latest) > 0}


def _buying_ahead_duplicate_check(target: sqlite3.Connection) -> dict[str, Any]:
    duplicate_count = target.execute(
        """
        SELECT COUNT(*) AS c
        FROM (
            SELECT profile_key, event_key, raw_activity_key, COUNT(*) AS row_count
            FROM profile_buying_ahead
            GROUP BY profile_key, event_key, raw_activity_key
            HAVING row_count > 1
        )
        """
    ).fetchone()["c"]
    return {"duplicates": int(duplicate_count), "passed": int(duplicate_count) == 0}


def _legacy_collision_check(profile_source: sqlite3.Connection | None, market_source: sqlite3.Connection | None) -> dict[str, Any]:
    profile_tables = _source_tables(profile_source)
    market_tables = _source_tables(market_source)
    collisions = sorted(profile_tables.intersection(market_tables))
    return {"collisions": collisions, "passed": not collisions}


def _ensure_profile_event_token(target: sqlite3.Connection, row: sqlite3.Row, now: str) -> str | None:
    token_id = _value(row, "token_id")
    if not token_id:
        return None
    event_key = _event_key(row)
    event_token_key = _event_token_key_from_row(row)
    if not event_token_key:
        return None
    _upsert(
        target,
        "events",
        {
            "event_key": event_key,
            "event_slug": _value(row, "event_slug"),
            "condition_id": _value(row, "condition_id"),
            "market_id": _value(row, "market_id"),
            "market_slug": _value(row, "market_slug"),
            "symbol": _value(row, "symbol"),
            "cadence_seconds": _int(_value(row, "cadence_seconds")),
            "event_start_time_utc": _value(row, "event_start_time_utc"),
            "event_end_time_utc": _value(row, "event_end_time_utc"),
            "settlement_threshold": _float(_value(row, "settlement_threshold")),
            "source_table": "profile_raw_activity",
            "source_json": _json_row(row),
            "inserted_at_utc": now,
            "updated_at_utc": now,
        },
        pk=("event_key",),
    )
    _upsert(
        target,
        "event_tokens",
        {
            "event_token_key": event_token_key,
            "event_key": event_key,
            "token_id": token_id,
            "outcome": _value(row, "outcome_side", "Unknown"),
            "condition_id": _value(row, "condition_id"),
            "event_slug": _value(row, "event_slug"),
            "market_id": _value(row, "market_id"),
            "symbol": _value(row, "symbol"),
            "active": 1,
            "closed": 0,
            "source_table": "profile_raw_activity",
            "source_json": _json_row(row),
            "inserted_at_utc": now,
            "updated_at_utc": now,
        },
        pk=("event_token_key",),
    )
    return event_token_key


def _source_tables(conn: sqlite3.Connection | None) -> set[str]:
    if conn is None:
        return set()
    return {str(row["name"]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _upsert(conn: sqlite3.Connection, table: str, values: dict[str, Any], *, pk: tuple[str, ...]) -> None:
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    update_columns = [column for column in columns if column not in pk]
    if update_columns:
        updates = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
        conflict = f" ON CONFLICT({', '.join(pk)}) DO UPDATE SET {updates}"
    else:
        conflict = f" ON CONFLICT({', '.join(pk)}) DO NOTHING"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}){conflict}",
        [values[column] for column in columns],
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection | None, table_name: str) -> int:
    if conn is None or not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"])


def _value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() and row[key] is not None else default


def _json_row(row: sqlite3.Row) -> str:
    return json.dumps({key: row[key] for key in row.keys()}, sort_keys=True, default=str)


def _event_key(row: sqlite3.Row) -> str:
    return str(_value(row, "condition_id") or _value(row, "event_slug") or _value(row, "event_id") or _stable_key("event", _json_row(row)))


def _event_token_key_from_row(row: sqlite3.Row) -> str | None:
    token_id = _value(row, "token_id")
    if not token_id:
        return None
    return str(_value(row, "event_token_key") or _stable_key(_event_key(row), token_id))


def _stable_key(*parts: Any) -> str:
    import hashlib

    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
