from __future__ import annotations

from typing import Any

import pandas as pd


def build_event_labels(
    events_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    *,
    boundary_policy: str = "first_report_at_or_after_boundary",
    max_boundary_lag_seconds: float = 15.0,
    prefer_polymarket_settlement: bool = True,
) -> pd.DataFrame:
    """Build settlement labels from decoded reference reports and event windows."""

    columns = [
        "event_id",
        "market_id",
        "primary_symbol",
        "window_start_time",
        "window_end_time",
        "reference_source",
        "reference_start_price",
        "reference_end_price",
        "reference_start_timestamp",
        "reference_end_timestamp",
        "reference_start_lag_seconds",
        "reference_end_lag_seconds",
        "resolved_direction",
        "resolved_outcome",
        "label_authority",
        "label_status",
        "label_blockers",
        "label_version",
    ]
    if events_df.empty:
        return pd.DataFrame(columns=columns)
    event_keys = _event_keys(events_df)
    if reference_df.empty:
        rows = []
        for _, event in event_keys.iterrows():
            rows.append(_blocked_label(event, ["missing_reference_price_reports"], boundary_policy=boundary_policy))
        labels = pd.DataFrame(rows, columns=columns)
        return _apply_polymarket_settlement_labels(labels, events_df, columns) if prefer_polymarket_settlement else labels

    reference = reference_df.copy()
    reference["symbol"] = reference["symbol"].astype(str).str.upper()
    reference["reference_timestamp"] = pd.to_datetime(reference["reference_timestamp"], utc=True, errors="coerce")
    reference["benchmark_price"] = pd.to_numeric(reference["benchmark_price"], errors="coerce")
    reference = reference.dropna(subset=["symbol", "reference_timestamp", "benchmark_price"]).sort_values(
        ["symbol", "reference_timestamp"],
        kind="mergesort",
    )

    rows: list[dict[str, Any]] = []
    for _, event in event_keys.iterrows():
        symbol = str(event.get("primary_symbol") or "BTC").upper()
        start_at = pd.to_datetime(event.get("window_start_time"), utc=True, errors="coerce")
        end_at = pd.to_datetime(event.get("window_end_time"), utc=True, errors="coerce")
        blockers: list[str] = []
        if pd.isna(start_at) or pd.isna(end_at):
            rows.append(_blocked_label(event, ["missing_event_window_times"], boundary_policy=boundary_policy))
            continue
        symbol_reports = reference[reference["symbol"] == symbol]
        start_report = _select_boundary_report(symbol_reports, start_at, policy=boundary_policy)
        end_report = _select_boundary_report(symbol_reports, end_at, policy=boundary_policy)
        if start_report is None:
            blockers.append("missing_start_boundary_reference_report")
        if end_report is None:
            blockers.append("missing_end_boundary_reference_report")
        start_lag = _lag_seconds(start_report, start_at) if start_report is not None else None
        end_lag = _lag_seconds(end_report, end_at) if end_report is not None else None
        if start_lag is not None and abs(start_lag) > float(max_boundary_lag_seconds):
            blockers.append("start_boundary_reference_lag_exceeds_limit")
        if end_lag is not None and abs(end_lag) > float(max_boundary_lag_seconds):
            blockers.append("end_boundary_reference_lag_exceeds_limit")
        if blockers:
            rows.append(_blocked_label(event, blockers, boundary_policy=boundary_policy))
            continue
        start_price = float(start_report["benchmark_price"])
        end_price = float(end_report["benchmark_price"])
        resolved_direction = "up" if end_price >= start_price else "down"
        rows.append(
            {
                "event_id": event.get("event_id"),
                "market_id": event.get("market_id"),
                "primary_symbol": symbol,
                "window_start_time": start_at,
                "window_end_time": end_at,
                "reference_source": _reference_source(start_report, end_report),
                "reference_start_price": start_price,
                "reference_end_price": end_price,
                "reference_start_timestamp": start_report["reference_timestamp"],
                "reference_end_timestamp": end_report["reference_timestamp"],
                "reference_start_lag_seconds": start_lag,
                "reference_end_lag_seconds": end_lag,
                "resolved_direction": resolved_direction,
                "resolved_outcome": "Up" if resolved_direction == "up" else "Down",
                "label_authority": _label_authority(_reference_source(start_report, end_report)),
                "label_status": "complete",
                "label_blockers": [],
                "label_version": f"crypto_options_label_v1:{boundary_policy}:max_lag_{max_boundary_lag_seconds:g}s",
            }
        )
    labels = pd.DataFrame(rows, columns=columns)
    return _apply_polymarket_settlement_labels(labels, events_df, columns) if prefer_polymarket_settlement else labels


def _apply_polymarket_settlement_labels(labels_df: pd.DataFrame, events_df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    settled = _settled_event_outcomes(events_df)
    if settled.empty:
        return labels_df
    labels = labels_df.copy()
    for _, settled_row in settled.iterrows():
        mask = pd.Series([True] * len(labels), index=labels.index)
        for key in ("event_id", "market_id"):
            if key in labels.columns and key in settled_row.index:
                mask &= labels[key].astype(str) == str(settled_row.get(key))
        if labels.empty or not bool(mask.any()):
            base = {column: None for column in columns}
            for column in ("event_id", "market_id", "primary_symbol", "window_start_time", "window_end_time"):
                base[column] = settled_row.get(column)
            labels = pd.concat([labels, pd.DataFrame([base], columns=columns)], ignore_index=True)
            mask = pd.Series([False] * len(labels), index=labels.index)
            mask.iloc[-1] = True
        labels.loc[mask, "resolved_direction"] = settled_row["resolved_direction"]
        labels.loc[mask, "resolved_outcome"] = settled_row["resolved_outcome"]
        labels.loc[mask, "label_authority"] = "polymarket_settled_outcome"
        labels.loc[mask, "label_status"] = "complete"
        labels.loc[mask, "label_blockers"] = pd.Series([[] for _ in range(int(mask.sum()))], index=labels.index[mask], dtype=object)
        labels.loc[mask, "reference_source"] = labels.loc[mask, "reference_source"].fillna(
            settled_row.get("resolution_source") or "polymarket_settled_outcome"
        )
        labels.loc[mask, "label_version"] = "crypto_options_label_v2:polymarket_settled_outcome_price"
    return labels[columns].sort_values(["event_id", "market_id"], kind="mergesort").reset_index(drop=True)


def _settled_event_outcomes(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty or "outcome_price" not in events_df.columns or "outcome" not in events_df.columns:
        return pd.DataFrame()
    frame = events_df.copy()
    frame["outcome_price"] = pd.to_numeric(frame["outcome_price"], errors="coerce")
    key_columns = [
        column
        for column in ("event_id", "market_id", "primary_symbol", "window_start_time", "window_end_time", "resolution_source")
        if column in frame.columns
    ]
    rows: list[dict[str, Any]] = []
    for _, group in frame.dropna(subset=["outcome_price"]).groupby(["event_id", "market_id"], dropna=False, sort=False):
        max_price = group["outcome_price"].max()
        min_price = group["outcome_price"].min()
        winners = group[group["outcome_price"] == max_price]
        if len(winners) != 1 or max_price < 0.99 or min_price > 0.01:
            continue
        winner = winners.iloc[0]
        direction = _normalize_direction(winner.get("outcome"))
        if direction not in {"up", "down"}:
            continue
        row = {column: winner.get(column) for column in key_columns}
        row.update(
            {
                "resolved_direction": direction,
                "resolved_outcome": "Up" if direction == "up" else "Down",
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _event_keys(events_df: pd.DataFrame) -> pd.DataFrame:
    frame = events_df.copy()
    for column in ("window_start_time", "window_end_time"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    key_columns = [
        column
        for column in ("event_id", "market_id", "primary_symbol", "window_start_time", "window_end_time", "resolution_source")
        if column in frame.columns
    ]
    if not key_columns:
        return pd.DataFrame()
    return frame[key_columns].drop_duplicates(subset=[column for column in key_columns if column != "resolution_source"]).reset_index(drop=True)


def _select_boundary_report(reports: pd.DataFrame, boundary: pd.Timestamp, *, policy: str) -> pd.Series | None:
    if reports.empty:
        return None
    if policy == "nearest_report":
        distances = (reports["reference_timestamp"] - boundary).abs()
        if distances.empty:
            return None
        return reports.loc[distances.idxmin()]
    if policy == "last_report_at_or_before_boundary":
        candidates = reports[reports["reference_timestamp"] <= boundary]
        return candidates.iloc[-1] if not candidates.empty else None
    candidates = reports[reports["reference_timestamp"] >= boundary]
    return candidates.iloc[0] if not candidates.empty else None


def _blocked_label(event: pd.Series, blockers: list[str], *, boundary_policy: str) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "market_id": event.get("market_id"),
        "primary_symbol": event.get("primary_symbol"),
        "window_start_time": event.get("window_start_time"),
        "window_end_time": event.get("window_end_time"),
        "reference_source": event.get("resolution_source"),
        "reference_start_price": None,
        "reference_end_price": None,
        "reference_start_timestamp": None,
        "reference_end_timestamp": None,
        "reference_start_lag_seconds": None,
        "reference_end_lag_seconds": None,
        "resolved_direction": None,
        "resolved_outcome": None,
        "label_authority": _label_authority(event.get("resolution_source")),
        "label_status": "blocked",
        "label_blockers": sorted(set(blockers)),
        "label_version": f"crypto_options_label_v1:{boundary_policy}",
    }


def _lag_seconds(row: pd.Series, boundary: pd.Timestamp) -> float:
    return float((row["reference_timestamp"] - boundary).total_seconds())


def _reference_source(start_report: pd.Series, end_report: pd.Series) -> str:
    sources = {str(start_report.get("source") or ""), str(end_report.get("source") or "")}
    sources.discard("")
    return ",".join(sorted(sources)) or "decoded_reference_price_report"


def _label_authority(source: Any) -> str:
    normalized = str(source or "").lower()
    if "chainlink" in normalized:
        return "canonical_candidate"
    if "pyth" in normalized or "dia" in normalized:
        return "oracle_proxy"
    if "exchange" in normalized or "binance" in normalized or "coinbase" in normalized or "kraken" in normalized:
        return "exchange_proxy"
    if "proxy" in normalized:
        return "proxy"
    return "unknown"


def _normalize_direction(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"up", "yes", "above", "true", "1"}:
        return "up"
    if normalized in {"down", "no", "below", "false", "0"}:
        return "down"
    return normalized


__all__ = ["build_event_labels"]
