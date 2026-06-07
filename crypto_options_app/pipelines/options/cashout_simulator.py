from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.pipelines.options.price_path_trace import build_price_path_trace_report_from_paths
from crypto_options_app.pipelines.options.reporting import strict_jsonable


CASHOUT_POLICY_SCHEMA_VERSION = "crypto_options_cashout_policy_v1"
CASHOUT_SIMULATION_SCHEMA_VERSION = "crypto_options_cashout_simulation_v1"

DEFAULT_CASHOUT_BUCKETS = (
    {"bucket": "0.01-0.05", "min_price": 0.01, "max_price": 0.05, "target_multiple": 4.0},
    {"bucket": "0.05-0.10", "min_price": 0.05, "max_price": 0.10, "target_multiple": 3.0},
    {"bucket": "0.10-0.25", "min_price": 0.10, "max_price": 0.25, "target_multiple": 2.0},
    {"bucket": "0.25-0.45", "min_price": 0.25, "max_price": 0.45, "target_multiple": 1.8},
    {"bucket": "0.45-0.55", "min_price": 0.45, "max_price": 0.55, "target_multiple": 1.5},
    {"bucket": "0.55-0.70", "min_price": 0.55, "max_price": 0.70, "target_multiple": 1.5},
    {"bucket": "0.70-0.85", "min_price": 0.70, "max_price": 0.85, "target_multiple": 1.15},
)


def default_cashout_policy() -> dict[str, Any]:
    return strict_jsonable(
        {
            "schema_version": CASHOUT_POLICY_SCHEMA_VERSION,
            "policy_id": "cashout_policy_v1",
            "target_price_cap": 0.99,
            "fee_model": "not_modelled_in_v1_simulation",
            "buckets": list(DEFAULT_CASHOUT_BUCKETS),
        }
    )


def simulate_bucketed_cashout(
    trace_report: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    policy = policy or default_cashout_policy()
    generated_at = generated_at or datetime.now(timezone.utc)
    rows = [_simulate_trace(row, policy=policy) for row in trace_report.get("traces") or []]
    scoreable = [row for row in rows if row.get("simulated_pnl_net_usd") is not None]
    frame = pd.DataFrame(scoreable)
    metrics = compute_trade_metrics(frame, sample_column="lane_id") if scoreable else compute_trade_metrics(pd.DataFrame())
    by_lane = _group_summary(scoreable, "lane_id")
    by_bucket = _group_summary(scoreable, "bucket")
    prevented_losses = [
        row
        for row in scoreable
        if row.get("exit_reason") == "cashout_target_hit"
        and _to_float(row.get("hold_to_settlement_pnl_net_usd")) is not None
        and (_to_float(row.get("hold_to_settlement_pnl_net_usd")) or 0.0) < 0.0
        and (_to_float(row.get("simulated_pnl_net_usd")) or 0.0) > 0.0
    ]
    reduced_winners = [
        row
        for row in scoreable
        if row.get("exit_reason") == "cashout_target_hit"
        and _to_float(row.get("hold_to_settlement_pnl_net_usd")) is not None
        and (_to_float(row.get("hold_to_settlement_pnl_net_usd")) or 0.0) > (_to_float(row.get("simulated_pnl_net_usd")) or 0.0)
    ]
    return strict_jsonable(
        {
            "schema_version": CASHOUT_SIMULATION_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "policy": policy,
            "trace_report_schema_version": trace_report.get("schema_version"),
            "trace_count": len(trace_report.get("traces") or []),
            "scoreable_count": len(scoreable),
            "cashout_hit_count": sum(1 for row in scoreable if row.get("exit_reason") == "cashout_target_hit"),
            "fallback_hold_count": sum(1 for row in scoreable if row.get("exit_reason") == "hold_to_settlement_fallback"),
            "no_policy_count": sum(1 for row in rows if row.get("exit_reason") == "no_cashout_policy_for_entry_bucket"),
            "prevented_loss_count": len(prevented_losses),
            "reduced_winner_count": len(reduced_winners),
            "metrics": metrics,
            "by_lane": by_lane,
            "by_bucket": by_bucket,
            "prevented_losses": prevented_losses[:25],
            "reduced_winners": reduced_winners[:25],
            "rows": rows,
            "safety_boundary": {
                "orders_allowed": False,
                "live_trading_authorized": False,
                "scope": "read_only_cashout_simulation",
            },
        }
    )


def simulate_bucketed_cashout_from_paths(
    *,
    ledger_paths: list[str | Path],
    monitor_paths: list[str | Path],
    policy: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    trace_report = build_price_path_trace_report_from_paths(ledger_paths=ledger_paths, monitor_paths=monitor_paths)
    return simulate_bucketed_cashout(trace_report, policy=policy, generated_at=generated_at)


def write_cashout_simulation_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    root = Path(output_dir) if output_dir is not None else Path.cwd()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = root / f"crypto_options_cashout_simulation_{stamp}.json"
    md_path = root / f"crypto_options_cashout_simulation_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_cashout_simulation_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_cashout_simulation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Bucketed Cashout Simulation",
        "",
        f"- Generated: `{payload.get('generated_at_utc')}`",
        f"- Scoreable positions: `{payload.get('scoreable_count')}`",
        f"- Cashout hits: `{payload.get('cashout_hit_count')}`",
        f"- Hold fallback: `{payload.get('fallback_hold_count')}`",
        f"- Prevented losses: `{payload.get('prevented_loss_count')}`",
        f"- Reduced winners: `{payload.get('reduced_winner_count')}`",
        "",
        "## By Bucket",
        "",
        "| Bucket | Trades | Cashout Hits | Sim PnL | Hold PnL | Max Drawdown |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload.get("by_bucket") or []:
        lines.append(
            f"| {row.get('group')} | {row.get('trade_count')} | {row.get('cashout_hit_count')} | {row.get('simulated_pnl_sum')} | {row.get('hold_pnl_sum')} | {row.get('max_drawdown')} |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Read-only simulation.",
            "- No order placement, cancellation, signing, broadcasting, redemption, routing, recommendation, or authorization.",
        ]
    )
    return "\n".join(lines)


def _simulate_trace(trace: dict[str, Any], *, policy: dict[str, Any]) -> dict[str, Any]:
    entry_price = _to_float(trace.get("entry_price"))
    shares = _to_float(trace.get("shares"))
    hold_pnl = _to_float(trace.get("realized_pnl_net_usd"))
    bucket = _bucket_for_entry(entry_price, policy)
    if entry_price is None or shares is None:
        return _row(trace, bucket=None, exit_reason="missing_entry_price_or_shares", simulated_pnl=None, hold_pnl=hold_pnl)
    if bucket is None:
        return _row(trace, bucket=None, exit_reason="no_cashout_policy_for_entry_bucket", simulated_pnl=hold_pnl, hold_pnl=hold_pnl)
    target_price = min(float(policy.get("target_price_cap", 0.99)), entry_price * float(bucket["target_multiple"]))
    hit = _first_target_hit(trace, target_price=target_price)
    if hit is None:
        return _row(
            trace,
            bucket=bucket,
            exit_reason="hold_to_settlement_fallback",
            simulated_pnl=hold_pnl,
            hold_pnl=hold_pnl,
            target_price=target_price,
        )
    exit_price = _to_float(hit.get("exit_price"))
    simulated_pnl = ((exit_price - entry_price) * shares) if exit_price is not None else None
    return _row(
        trace,
        bucket=bucket,
        exit_reason="cashout_target_hit",
        simulated_pnl=simulated_pnl,
        hold_pnl=hold_pnl,
        target_price=target_price,
        exit_price=exit_price,
        exit_at_utc=hit.get("observed_at_utc"),
    )


def _row(
    trace: dict[str, Any],
    *,
    bucket: dict[str, Any] | None,
    exit_reason: str,
    simulated_pnl: float | None,
    hold_pnl: float | None,
    target_price: float | None = None,
    exit_price: float | None = None,
    exit_at_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "lane_id": trace.get("lane_id"),
        "event_slug": trace.get("event_slug"),
        "symbol": trace.get("symbol"),
        "outcome": trace.get("outcome"),
        "token_id": trace.get("token_id"),
        "entry_price": trace.get("entry_price"),
        "shares": trace.get("shares"),
        "bucket": bucket.get("bucket") if bucket else None,
        "target_multiple": bucket.get("target_multiple") if bucket else None,
        "target_price": target_price,
        "exit_reason": exit_reason,
        "exit_price": exit_price,
        "exit_at_utc": exit_at_utc,
        "simulated_pnl_net_usd": simulated_pnl,
        "hold_to_settlement_pnl_net_usd": hold_pnl,
        "pnl_delta_vs_hold_usd": (simulated_pnl - hold_pnl) if simulated_pnl is not None and hold_pnl is not None else None,
        "win": simulated_pnl is not None and simulated_pnl > 0,
        "pnl_net": simulated_pnl,
    }


def _bucket_for_entry(entry_price: float | None, policy: dict[str, Any]) -> dict[str, Any] | None:
    if entry_price is None:
        return None
    for bucket in policy.get("buckets") or []:
        min_price = _to_float(bucket.get("min_price"))
        max_price = _to_float(bucket.get("max_price"))
        if min_price is None or max_price is None:
            continue
        if entry_price >= min_price and entry_price < max_price:
            return bucket
    return None


def _first_target_hit(trace: dict[str, Any], *, target_price: float) -> dict[str, Any] | None:
    for row in (trace.get("path") or trace.get("path_sample") or []):
        exit_price = _to_float(row.get("exit_price"))
        if exit_price is not None and exit_price >= target_price:
            return row
    return None


def _group_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        group = str(row.get(key) or "unknown")
        groups.setdefault(group, []).append(row)
    output: list[dict[str, Any]] = []
    for group, group_rows in sorted(groups.items()):
        frame = pd.DataFrame(group_rows)
        metrics = compute_trade_metrics(frame, sample_column="event_slug")
        output.append(
            {
                "group": group,
                "trade_count": len(group_rows),
                "cashout_hit_count": sum(1 for row in group_rows if row.get("exit_reason") == "cashout_target_hit"),
                "simulated_pnl_sum": _sum(row.get("simulated_pnl_net_usd") for row in group_rows),
                "hold_pnl_sum": _sum(row.get("hold_to_settlement_pnl_net_usd") for row in group_rows),
                "max_drawdown": metrics.get("max_drawdown"),
                "win_rate": metrics.get("win_rate"),
            }
        )
    return output


def _sum(values: Any) -> float:
    total = 0.0
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            total += parsed
    return total


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


__all__ = [
    "CASHOUT_POLICY_SCHEMA_VERSION",
    "CASHOUT_SIMULATION_SCHEMA_VERSION",
    "default_cashout_policy",
    "render_cashout_simulation_markdown",
    "simulate_bucketed_cashout",
    "simulate_bucketed_cashout_from_paths",
    "write_cashout_simulation_artifacts",
]
