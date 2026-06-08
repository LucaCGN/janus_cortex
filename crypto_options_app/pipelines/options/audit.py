from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.contracts import (
    CRYPTO_OPTIONS_AUDIT_SCHEMA_VERSION,
    CryptoOptionsAuditThresholds,
    CryptoOptionsDataCounts,
    SUPPORTED_POLYMARKET_EVENT_TYPES,
    schema_contracts,
)


def evaluate_crypto_options_data_sufficiency(
    events_df: pd.DataFrame,
    clob_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    *,
    reference_df: pd.DataFrame | None = None,
    labels_df: pd.DataFrame | None = None,
    panel_df: pd.DataFrame | None = None,
    thresholds: CryptoOptionsAuditThresholds | None = None,
    audited_at: datetime | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or CryptoOptionsAuditThresholds()
    audited_at = audited_at or datetime.now(timezone.utc)
    contracts = schema_contracts()
    reference_df = reference_df if reference_df is not None else pd.DataFrame(columns=contracts["reference_price_reports"])
    labels_df = labels_df if labels_df is not None else pd.DataFrame(columns=contracts["event_labels"])
    panel_df = panel_df if panel_df is not None else pd.DataFrame()
    counts = _counts(events_df, clob_df, candles_df, reference_df, labels_df, panel_df)
    missing_fields = _missing_fields(events_df, clob_df, candles_df, reference_df, labels_df, panel_df)
    blockers = _global_blockers(counts, thresholds, missing_fields)
    event_type_rows = _event_type_summary(events_df)
    strategy_validity = _strategy_validity(counts, thresholds)
    polymarket_only = _polymarket_only_underlying_verdict(counts)
    status = _status(strategy_validity, blockers)
    return {
        "schema_version": CRYPTO_OPTIONS_AUDIT_SCHEMA_VERSION,
        "audited_at_utc": audited_at.isoformat(),
        "status": status,
        "counts": asdict(counts),
        "thresholds": asdict(thresholds),
        "available_events": event_type_rows,
        "matched_outcomes": int(counts.matched_outcomes),
        "available_clob_history": {
            "observations": int(counts.clob_observations),
            "rows_with_bid_ask": int(counts.clob_rows_with_bid_ask),
            "rows_with_depth": int(counts.clob_rows_with_depth),
            "odds_only": int(counts.clob_rows_with_bid_ask) == 0,
            "has_executable_quotes": int(counts.clob_rows_with_bid_ask) > 0,
        },
        "available_reference_labels": {
            "reference_price_reports": int(counts.reference_price_reports),
            "label_rows": int(counts.reference_label_rows),
            "complete_label_rows": int(counts.reference_label_rows_complete),
            "required_for_short_interval_up_down": True,
        },
        "available_underlying_candles": {
            "candles": int(counts.underlying_candles),
            "symbols": int(counts.candle_symbols),
            "required_source": "external_exchange_candles_or_trades",
        },
        "missing_fields": missing_fields,
        "strategy_validity": strategy_validity,
        "polymarket_only_underlying_price_reconstruction": polymarket_only,
        "blockers": blockers,
        "schema_contracts": schema_contracts(),
        "execution_boundary": "research_audit_only",
        "live_trading_authorized": False,
    }


def _counts(
    events_df: pd.DataFrame,
    clob_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    panel_df: pd.DataFrame,
) -> CryptoOptionsDataCounts:
    event_rows = int(len(events_df))
    distinct_events = _nunique(events_df, "event_id")
    distinct_markets = _nunique(events_df, "market_id")
    matched_outcomes = int(events_df["token_id"].dropna().astype(str).ne("").sum()) if "token_id" in events_df.columns else 0
    clob_observations = int(len(clob_df))
    if {"best_bid", "best_ask"}.issubset(clob_df.columns):
        clob_rows_with_bid_ask = int(clob_df[["best_bid", "best_ask"]].dropna().shape[0])
    else:
        clob_rows_with_bid_ask = 0
    if {"depth_top3_bid_size", "depth_top3_ask_size"}.issubset(clob_df.columns):
        clob_rows_with_depth = int(clob_df[["depth_top3_bid_size", "depth_top3_ask_size"]].dropna(how="all").shape[0])
    else:
        clob_rows_with_depth = 0
    reference_price_reports = int(len(reference_df))
    reference_label_rows = int(len(labels_df))
    reference_label_rows_complete = int((labels_df["label_status"].astype(str) == "complete").sum()) if "label_status" in labels_df.columns else 0
    underlying_candles = int(len(candles_df))
    candle_symbols = _nunique(candles_df, "symbol")
    rows_with_settlement_threshold = int(events_df["settlement_threshold"].dropna().shape[0]) if "settlement_threshold" in events_df.columns else 0
    rows_with_condition_text = int(events_df["condition_text"].dropna().astype(str).str.strip().ne("").sum()) if "condition_text" in events_df.columns else 0
    rows_with_cadence = int(events_df["cadence_seconds"].dropna().shape[0]) if "cadence_seconds" in events_df.columns else 0
    rows_with_resolution_source = (
        int(events_df["resolution_source"].dropna().astype(str).str.strip().ne("").sum()) if "resolution_source" in events_df.columns else 0
    )
    short_interval_up_down_rows = (
        int((events_df["event_type"].astype(str) == "short_interval_up_down").sum()) if "event_type" in events_df.columns else 0
    )
    joined_panel_rows = int(len(panel_df))
    return CryptoOptionsDataCounts(
        event_rows=event_rows,
        distinct_events=distinct_events,
        distinct_markets=distinct_markets,
        matched_outcomes=matched_outcomes,
        clob_observations=clob_observations,
        clob_rows_with_bid_ask=clob_rows_with_bid_ask,
        clob_rows_with_depth=clob_rows_with_depth,
        reference_price_reports=reference_price_reports,
        reference_label_rows=reference_label_rows,
        reference_label_rows_complete=reference_label_rows_complete,
        underlying_candles=underlying_candles,
        candle_symbols=candle_symbols,
        rows_with_settlement_threshold=rows_with_settlement_threshold,
        rows_with_condition_text=rows_with_condition_text,
        rows_with_cadence=rows_with_cadence,
        rows_with_resolution_source=rows_with_resolution_source,
        short_interval_up_down_rows=short_interval_up_down_rows,
        joined_panel_rows=joined_panel_rows,
    )


def _missing_fields(
    events_df: pd.DataFrame,
    clob_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    panel_df: pd.DataFrame,
) -> dict[str, list[str]]:
    contracts = schema_contracts()
    return {
        "polymarket_crypto_events_markets_outcomes": sorted(set(contracts["polymarket_crypto_events_markets_outcomes"]) - set(events_df.columns)),
        "polymarket_clob_observations": sorted(set(contracts["polymarket_clob_observations"]) - set(clob_df.columns)),
        "reference_price_reports": sorted(set(contracts["reference_price_reports"]) - set(reference_df.columns)),
        "event_labels": sorted(set(contracts["event_labels"]) - set(labels_df.columns)),
        "crypto_underlying_candles": sorted(set(contracts["crypto_underlying_candles"]) - set(candles_df.columns)),
        "joined_event_state_panel": sorted(set(contracts["joined_event_state_panel"]) - set(panel_df.columns)),
    }


def _global_blockers(
    counts: CryptoOptionsDataCounts,
    thresholds: CryptoOptionsAuditThresholds,
    missing_fields: dict[str, list[str]],
) -> list[str]:
    blockers: list[str] = []
    if counts.distinct_events < thresholds.min_events_for_discovery:
        blockers.append("missing_polymarket_crypto_events")
    if counts.matched_outcomes < thresholds.min_matched_outcomes_for_replay:
        blockers.append("missing_polymarket_crypto_outcome_tokens")
    if counts.clob_observations < thresholds.min_clob_observations_for_scalping:
        blockers.append("missing_polymarket_clob_history")
    if counts.underlying_candles < thresholds.min_underlying_candles_for_prediction:
        blockers.append("missing_underlying_exchange_candles")
    if counts.rows_with_settlement_threshold == 0 and counts.short_interval_up_down_rows == 0:
        blockers.append("missing_settlement_thresholds")
    if counts.short_interval_up_down_rows > 0 and counts.reference_label_rows_complete == 0:
        blockers.append("missing_reference_window_prices_for_up_down")
    if counts.rows_with_condition_text == 0:
        blockers.append("missing_condition_text")
    if counts.rows_with_cadence == 0:
        blockers.append("missing_contract_cadence")
    if counts.rows_with_resolution_source == 0:
        blockers.append("missing_resolution_source")
    if counts.underlying_candles == 0 and counts.clob_observations > 0:
        blockers.append("polymarket_odds_cannot_reconstruct_underlying_price")
    for schema_name, fields in missing_fields.items():
        if fields:
            blockers.append(f"missing_fields:{schema_name}")
    return sorted(set(blockers))


def _strategy_validity(counts: CryptoOptionsDataCounts, thresholds: CryptoOptionsAuditThresholds) -> dict[str, dict[str, Any]]:
    directional_blockers = []
    if counts.underlying_candles < thresholds.min_underlying_candles_for_prediction:
        directional_blockers.append("missing_underlying_exchange_candles")
    if counts.rows_with_settlement_threshold == 0 and counts.short_interval_up_down_rows == 0:
        directional_blockers.append("missing_settlement_thresholds")
    if counts.short_interval_up_down_rows > 0 and counts.reference_label_rows_complete == 0:
        directional_blockers.append("missing_reference_window_prices_for_up_down")
    scalping_blockers = []
    if counts.clob_observations < thresholds.min_clob_observations_for_scalping:
        scalping_blockers.append("missing_polymarket_clob_history")
    if counts.clob_rows_with_bid_ask == 0:
        scalping_blockers.append("missing_bid_ask_depth")
    hybrid_blockers = sorted(set(directional_blockers + scalping_blockers))
    baseline_blockers = []
    if counts.matched_outcomes == 0:
        baseline_blockers.append("missing_outcomes")
    return {
        "directional_prediction": {
            "valid": not directional_blockers,
            "blockers": directional_blockers,
            "requires": ["exchange_candles", "settlement_thresholds", "no_lookahead_labels"],
        },
        "microstructure_scalping": {
            "valid": not scalping_blockers,
            "blockers": scalping_blockers,
            "requires": ["Polymarket CLOB bid/ask history", "depth", "queue/fillability stress"],
        },
        "hybrid": {
            "valid": not hybrid_blockers,
            "blockers": hybrid_blockers,
            "requires": ["directional signal", "CLOB bid/ask history", "entry_exit_policy"],
        },
        "no_trade_baseline": {
            "valid": not baseline_blockers,
            "blockers": baseline_blockers,
            "requires": ["event/outcome inventory"],
        },
    }


def _polymarket_only_underlying_verdict(counts: CryptoOptionsDataCounts) -> dict[str, Any]:
    return {
        "answer": "no",
        "evidence": (
            "Polymarket price history is an odds/fillability path for outcome tokens. It can show market sentiment, "
            "spread, and entry/exit feasibility, but it is not a BTC/ETH/SOL/XRP spot candle source. "
            f"Current audit saw {counts.underlying_candles} exchange-backed underlying candle rows."
        ),
        "usable_for": ["odds_path", "fillability", "spread", "settlement_convergence"],
        "not_usable_for": ["underlying_spot_candles", "technical_indicators_without_exchange_data"],
    }


def _event_type_summary(events_df: pd.DataFrame) -> list[dict[str, Any]]:
    if events_df.empty or "event_type" not in events_df.columns:
        return [{"event_type": event_type, "event_count": 0, "market_count": 0, "outcome_count": 0} for event_type in SUPPORTED_POLYMARKET_EVENT_TYPES]
    rows: list[dict[str, Any]] = []
    for event_type in SUPPORTED_POLYMARKET_EVENT_TYPES:
        subset = events_df[events_df["event_type"].astype(str) == event_type]
        rows.append(
            {
                "event_type": event_type,
                "event_count": _nunique(subset, "event_id"),
                "market_count": _nunique(subset, "market_id"),
                "outcome_count": int(len(subset)),
            }
        )
    return rows


def _status(strategy_validity: dict[str, dict[str, Any]], blockers: list[str]) -> str:
    if strategy_validity["hybrid"]["valid"]:
        return "hybrid_backtest_ready"
    if strategy_validity["directional_prediction"]["valid"] or strategy_validity["microstructure_scalping"]["valid"]:
        return "partial_backtest_ready"
    if blockers:
        return "blocked"
    return "research_ready"


def _nunique(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    return int(frame[column].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())


__all__ = ["evaluate_crypto_options_data_sufficiency"]
