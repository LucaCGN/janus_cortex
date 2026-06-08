from __future__ import annotations

from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.indicators import build_indicator_frame


def build_event_state_panel(
    events_df: pd.DataFrame,
    clob_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    labels_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Join Polymarket odds/fillability observations to exchange-backed candle context."""

    if events_df.empty or clob_df.empty:
        return _empty_panel()
    events = events_df.copy()
    clob = clob_df.copy()
    candles = candles_df.copy() if not candles_df.empty else pd.DataFrame()
    labels = labels_df.copy() if labels_df is not None and not labels_df.empty else pd.DataFrame()

    if "token_id" not in events.columns or "token_id" not in clob.columns:
        return _empty_panel()
    events["token_id"] = events["token_id"].astype(str)
    clob["token_id"] = clob["token_id"].astype(str)
    clob["observed_at"] = pd.to_datetime(clob["observed_at"], utc=True, errors="coerce")
    joined = clob.merge(
        events[
            [
                column
                for column in (
                    "event_id",
                    "event_slug",
                    "market_id",
                    "market_slug",
                    "event_type",
                    "primary_symbol",
                    "outcome",
                    "token_id",
                    "settlement_threshold",
                    "cadence_seconds",
                    "condition_text",
                    "resolution_source",
                    "resolution_source_url",
                    "window_start_time",
                    "window_end_time",
                    "resolved_outcome",
                    "resolved_direction",
                    "end_time",
                    "closed_time",
                )
                if column in events.columns
            ]
        ],
        on="token_id",
        how="left",
        suffixes=("_clob", ""),
    )
    joined["primary_symbol"] = joined.get("primary_symbol").fillna(joined.get("symbol")) if "primary_symbol" in joined.columns else joined.get("symbol")
    joined["polymarket_mid_price"] = pd.to_numeric(joined.get("mid_price"), errors="coerce")
    joined["best_bid"] = pd.to_numeric(joined.get("best_bid"), errors="coerce")
    joined["best_ask"] = pd.to_numeric(joined.get("best_ask"), errors="coerce")
    if "spread" in joined.columns:
        joined["spread"] = pd.to_numeric(joined["spread"], errors="coerce")
    else:
        joined["spread"] = joined["best_ask"] - joined["best_bid"]
    joined["close_time"] = joined.apply(_row_close_time, axis=1)
    if not labels.empty:
        joined = _merge_labels(joined, labels)
    if "reference_start_price" in joined.columns:
        reference_start = pd.to_numeric(joined["reference_start_price"], errors="coerce")
        if "settlement_threshold" in joined.columns:
            joined["settlement_threshold"] = pd.to_numeric(joined["settlement_threshold"], errors="coerce").fillna(reference_start)
        else:
            joined["settlement_threshold"] = reference_start

    if not candles.empty:
        indicator_frames: list[pd.DataFrame] = []
        for (symbol, threshold, close_at), group in joined.dropna(subset=["primary_symbol"]).groupby(
            ["primary_symbol", "settlement_threshold", "close_time"],
            dropna=False,
        ):
            symbol_candles = candles[candles["symbol"].astype(str).str.upper() == str(symbol).upper()].copy()
            if symbol_candles.empty:
                continue
            indicator_frames.append(
                build_indicator_frame(
                    symbol_candles,
                    settlement_threshold=_safe_float(threshold),
                    close_time=close_at if pd.notna(close_at) else None,
                )
            )
        indicator_df = pd.concat(indicator_frames, ignore_index=True) if indicator_frames else pd.DataFrame()
        if not indicator_df.empty:
            joined = _merge_asof_by_symbol(joined, indicator_df)
        else:
            joined["underlying_close"] = None
    else:
        joined["underlying_close"] = None

    if "close" in joined.columns:
        joined["underlying_close"] = pd.to_numeric(joined["close"], errors="coerce")
    elif "underlying_close" not in joined.columns:
        joined["underlying_close"] = None
    if "distance_to_threshold" not in joined.columns:
        joined["distance_to_threshold"] = joined["underlying_close"] - pd.to_numeric(joined.get("settlement_threshold"), errors="coerce")
    if "time_to_close_seconds" not in joined.columns:
        close_at = pd.to_datetime(joined["close_time"], utc=True, errors="coerce")
        joined["time_to_close_seconds"] = (close_at - joined["observed_at"]).dt.total_seconds()

    columns = [
        "event_id",
        "event_slug",
        "market_id",
        "market_slug",
        "token_id",
        "observed_at",
        "primary_symbol",
        "event_type",
        "outcome",
        "polymarket_mid_price",
        "best_bid",
        "best_ask",
        "spread",
        "bid_size",
        "ask_size",
        "depth_top3_bid_size",
        "depth_top3_ask_size",
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
        "return_1",
        "momentum_3",
        "volatility",
        "ma_fast",
        "ma_slow",
        "rsi",
        "condition_text",
    ]
    for column in columns:
        if column not in joined.columns:
            joined[column] = None
    return joined[columns].sort_values(["token_id", "observed_at"], kind="mergesort").reset_index(drop=True)


def _merge_labels(joined: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    label_columns = [
        column
        for column in (
            "event_id",
            "market_id",
            "reference_start_price",
            "reference_end_price",
            "resolved_outcome",
            "resolved_direction",
            "label_status",
            "label_authority",
            "label_blockers",
            "label_version",
        )
        if column in labels.columns
    ]
    if "event_id" not in label_columns and "market_id" not in label_columns:
        return joined
    merge_keys = [key for key in ("event_id", "market_id") if key in joined.columns and key in labels.columns]
    if not merge_keys:
        return joined
    merged = joined.merge(labels[label_columns].drop_duplicates(subset=merge_keys), on=merge_keys, how="left", suffixes=("", "_label"))
    for column in ("resolved_outcome", "resolved_direction"):
        label_column = f"{column}_label"
        if label_column in merged.columns:
            if column in merged.columns:
                merged[column] = merged[column].fillna(merged[label_column])
            else:
                merged[column] = merged[label_column]
            merged = merged.drop(columns=[label_column])
    return merged


def _merge_asof_by_symbol(joined: pd.DataFrame, indicator_df: pd.DataFrame) -> pd.DataFrame:
    left = joined.copy().sort_values(["primary_symbol", "observed_at"], kind="mergesort")
    right = indicator_df.copy().sort_values(["symbol", "opened_at"], kind="mergesort")
    pieces: list[pd.DataFrame] = []
    for symbol, left_group in left.groupby("primary_symbol", dropna=False):
        right_group = right[right["symbol"].astype(str).str.upper() == str(symbol).upper()]
        if right_group.empty:
            pieces.append(left_group)
            continue
        pieces.append(
            pd.merge_asof(
                left_group.sort_values("observed_at"),
                right_group.sort_values("opened_at"),
                left_on="observed_at",
                right_on="opened_at",
                direction="backward",
            )
        )
    return pd.concat(pieces, ignore_index=True) if pieces else left


def _row_close_time(row: pd.Series) -> Any:
    return row.get("window_end_time") or row.get("closed_time") or row.get("end_time")


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_panel() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
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
            "bid_size",
            "ask_size",
            "depth_top3_bid_size",
            "depth_top3_ask_size",
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
        ]
    )


__all__ = ["build_event_state_panel"]
