from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.pipelines.options.live_micro_executor import load_json_object
from crypto_options_app.pipelines.options.reporting import strict_jsonable


PRICE_PATH_TRACE_SCHEMA_VERSION = "crypto_options_price_path_trace_v1"
PRICE_PATH_TRACE_REPORT_SCHEMA_VERSION = "crypto_options_price_path_trace_report_v1"
DEFAULT_TRACE_MULTIPLES = (2.0, 3.0, 4.0)


def build_price_path_trace_report(
    *,
    ledgers: list[dict[str, Any]] | dict[str, dict[str, Any]],
    monitor_payloads: list[dict[str, Any]],
    generated_at: datetime | None = None,
    target_multiples: tuple[float, ...] = DEFAULT_TRACE_MULTIPLES,
) -> dict[str, Any]:
    """Build per-position option-token price movement traces.

    This reconstructs Polymarket outcome-token price paths only. It does not infer
    BTC/ETH underlying movement from option odds.
    """

    generated_at = generated_at or datetime.now(timezone.utc)
    ledger_list = list(ledgers.values()) if isinstance(ledgers, dict) else list(ledgers)
    observations = _collect_monitor_observations(monitor_payloads)
    traces = [
        _trace_entry(entry, observations=observations, target_multiples=target_multiples)
        for ledger in ledger_list
        for entry in (ledger.get("entries") or [])
        if _is_traceable_position(entry)
    ]
    covered = [row for row in traces if row["coverage"]["covered"]]
    missing_reasons: dict[str, int] = {}
    for row in traces:
        reason = row["coverage"].get("missing_coverage_reason")
        if reason:
            missing_reasons[str(reason)] = missing_reasons.get(str(reason), 0) + 1

    target_counts = {
        f"{_target_key(multiple)}x": sum(1 for row in covered if row["target_hits"].get(_target_key(multiple), {}).get("hit"))
        for multiple in target_multiples
    }
    losers_with_targets = [
        {
            "lane_id": row["lane_id"],
            "event_slug": row["event_slug"],
            "outcome": row["outcome"],
            "entry_price": row["entry_price"],
            "realized_pnl_net_usd": row["realized_pnl_net_usd"],
            "max_favorable_exit_price": row["mfe"]["max_favorable_exit_price"],
            "max_favorable_multiple": row["mfe"]["max_favorable_multiple"],
            "hit_targets": [key for key, hit in row["target_hits"].items() if hit.get("hit")],
        }
        for row in covered
        if _to_float(row.get("realized_pnl_net_usd")) is not None
        and (_to_float(row.get("realized_pnl_net_usd")) or 0.0) < 0.0
        and any(hit.get("hit") for hit in row["target_hits"].values())
    ]

    return strict_jsonable(
        {
            "schema_version": PRICE_PATH_TRACE_REPORT_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "trace_count": len(traces),
            "covered_count": len(covered),
            "coverage_rate": (len(covered) / len(traces)) if traces else None,
            "missing_coverage_reasons": missing_reasons,
            "target_hit_counts": target_counts,
            "losers_with_target_hits": losers_with_targets,
            "traces": traces,
            "safety_boundary": {
                "orders_allowed": False,
                "live_trading_authorized": False,
                "scope": "read_only_trace_reconstruction",
            },
        }
    )


def build_price_path_trace_report_from_paths(
    *,
    ledger_paths: list[str | Path],
    monitor_paths: list[str | Path],
    generated_at: datetime | None = None,
    target_multiples: tuple[float, ...] = DEFAULT_TRACE_MULTIPLES,
) -> dict[str, Any]:
    """Load ledger and monitor JSON files and build a trace report."""

    ledgers = [load_json_object(path) for path in ledger_paths]
    monitors = [load_json_object(path) for path in monitor_paths]
    return build_price_path_trace_report(
        ledgers=ledgers,
        monitor_payloads=monitors,
        generated_at=generated_at,
        target_multiples=target_multiples,
    )


def write_price_path_trace_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    root = Path(output_dir) if output_dir is not None else Path.cwd()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = root / f"crypto_options_price_path_trace_{stamp}.json"
    md_path = root / f"crypto_options_price_path_trace_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_price_path_trace_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_price_path_trace_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Price Path Trace",
        "",
        f"- Generated: `{payload.get('generated_at_utc')}`",
        f"- Traces: `{payload.get('trace_count')}`",
        f"- Covered: `{payload.get('covered_count')}`",
        f"- Coverage rate: `{payload.get('coverage_rate')}`",
        f"- Target hits: `{payload.get('target_hit_counts')}`",
        "",
        "## Missing Coverage",
        "",
        "```json",
        json.dumps(strict_jsonable(payload.get("missing_coverage_reasons") or {}), indent=2, sort_keys=True),
        "```",
        "",
        "## Losers With Target Hits",
        "",
        "| Lane | Event | Outcome | Entry | MFE Exit | Multiple | PnL | Targets |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("losers_with_target_hits") or []:
        lines.append(
            "| {lane} | {event} | {outcome} | {entry} | {mfe} | {multiple} | {pnl} | {targets} |".format(
                lane=row.get("lane_id"),
                event=row.get("event_slug"),
                outcome=row.get("outcome"),
                entry=row.get("entry_price"),
                mfe=row.get("max_favorable_exit_price"),
                multiple=row.get("max_favorable_multiple"),
                pnl=row.get("realized_pnl_net_usd"),
                targets=", ".join(row.get("hit_targets") or []),
            )
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Read-only trace reconstruction.",
            "- No order placement, cancellation, signing, broadcasting, redemption, routing, recommendation, or authorization.",
        ]
    )
    return "\n".join(lines)


def _is_traceable_position(entry: dict[str, Any]) -> bool:
    if str(entry.get("status") or "").lower() != "submitted":
        return False
    if str(entry.get("side") or "").upper() != "BUY":
        return False
    return bool(entry.get("event_slug") and entry.get("token_id"))


def _trace_entry(
    entry: dict[str, Any],
    *,
    observations: dict[tuple[str, str], list[dict[str, Any]]],
    target_multiples: tuple[float, ...],
) -> dict[str, Any]:
    event_slug = str(entry.get("event_slug") or "")
    token_id = str(entry.get("token_id") or "")
    entry_time = _entry_time(entry)
    event_end = _event_end_time(event_slug)
    entry_price = _entry_price(entry)
    path = [
        row
        for row in observations.get((event_slug, token_id), [])
        if entry_time is None or row.get("observed_at") is None or row["observed_at"] >= entry_time
    ]
    if event_end is not None:
        path = [row for row in path if row.get("observed_at") is None or row["observed_at"] <= event_end]
    coverage = _coverage(path, entry_price=entry_price)
    mfe = _mfe(path, entry_time=entry_time, entry_price=entry_price)
    mae = _mae(path, entry_time=entry_time, entry_price=entry_price)
    return strict_jsonable(
        {
            "schema_version": PRICE_PATH_TRACE_SCHEMA_VERSION,
            "lane_id": entry.get("lane_id"),
            "event_slug": event_slug,
            "symbol": entry.get("symbol"),
            "outcome": entry.get("outcome"),
            "token_id": token_id,
            "entry_at_utc": entry_time.astimezone(timezone.utc).isoformat() if entry_time else None,
            "event_end_at_utc": event_end.astimezone(timezone.utc).isoformat() if event_end else None,
            "entry_price": entry_price,
            "entry_price_source": _entry_price_source(entry),
            "shares": _filled_shares(entry),
            "estimated_total_cost_usd": _to_float(entry.get("estimated_total_cost_usd")),
            "realized_pnl_net_usd": _to_float(entry.get("realized_pnl_net_usd")),
            "settlement_status": entry.get("settlement_status"),
            "coverage": coverage,
            "mfe": mfe,
            "mae": mae,
            "target_hits": {
                _target_key(multiple): _target_hit(path, entry_time=entry_time, entry_price=entry_price, multiple=multiple)
                for multiple in target_multiples
            },
            "path": _path_rows(path),
            "path_sample": _path_sample(path),
        }
    )


def _collect_monitor_observations(monitor_payloads: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for payload in monitor_payloads:
        for candidate in (payload.get("observed_candidates") or []) + (payload.get("eligible_manual_candidates") or []):
            event_slug = str(candidate.get("event_slug") or "")
            token_id = str(candidate.get("token_id") or "")
            if not event_slug or not token_id:
                continue
            observed_at = _parse_time(candidate.get("quote_at_utc") or payload.get("generated_at_utc"))
            best_bid = _to_float(candidate.get("best_bid"))
            best_ask = _to_float(candidate.get("best_ask"))
            mid = _mid(best_bid, best_ask)
            exit_price = best_bid if best_bid is not None else mid
            if observed_at is None or exit_price is None:
                continue
            rows.setdefault((event_slug, token_id), []).append(
                {
                    "observed_at": observed_at,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "mid": mid,
                    "exit_price": exit_price,
                    "spread": _to_float(candidate.get("spread")),
                    "source": "monitor_candidate",
                }
            )
    for key, values in rows.items():
        dedup: dict[str, dict[str, Any]] = {}
        for value in values:
            dedup[value["observed_at"].isoformat()] = value
        rows[key] = sorted(dedup.values(), key=lambda row: row["observed_at"])
    return rows


def _coverage(path: list[dict[str, Any]], *, entry_price: float | None) -> dict[str, Any]:
    if entry_price is None:
        return {"covered": False, "observation_count": len(path), "missing_coverage_reason": "entry_price_missing"}
    if not path:
        return {"covered": False, "observation_count": 0, "missing_coverage_reason": "no_matching_monitor_observations"}
    return {"covered": True, "observation_count": len(path), "missing_coverage_reason": None}


def _mfe(path: list[dict[str, Any]], *, entry_time: datetime | None, entry_price: float | None) -> dict[str, Any]:
    if entry_price is None or not path:
        return {
            "max_favorable_exit_price": None,
            "max_favorable_delta": None,
            "max_favorable_multiple": None,
            "time_to_mfe_seconds": None,
        }
    best = max(path, key=lambda row: _to_float(row.get("exit_price")) or -1.0)
    best_price = _to_float(best.get("exit_price"))
    return {
        "max_favorable_exit_price": best_price,
        "max_favorable_delta": (best_price - entry_price) if best_price is not None else None,
        "max_favorable_multiple": (best_price / entry_price) if best_price is not None and entry_price > 0 else None,
        "time_to_mfe_seconds": _seconds_between(entry_time, best.get("observed_at")),
    }


def _mae(path: list[dict[str, Any]], *, entry_time: datetime | None, entry_price: float | None) -> dict[str, Any]:
    if entry_price is None or not path:
        return {
            "max_adverse_exit_price": None,
            "max_adverse_delta": None,
            "max_adverse_multiple": None,
            "time_to_mae_seconds": None,
        }
    worst = min(path, key=lambda row: _to_float(row.get("exit_price")) or 2.0)
    worst_price = _to_float(worst.get("exit_price"))
    return {
        "max_adverse_exit_price": worst_price,
        "max_adverse_delta": (worst_price - entry_price) if worst_price is not None else None,
        "max_adverse_multiple": (worst_price / entry_price) if worst_price is not None and entry_price > 0 else None,
        "time_to_mae_seconds": _seconds_between(entry_time, worst.get("observed_at")),
    }


def _target_hit(
    path: list[dict[str, Any]],
    *,
    entry_time: datetime | None,
    entry_price: float | None,
    multiple: float,
) -> dict[str, Any]:
    if entry_price is None or entry_price <= 0:
        return {"hit": False, "target_price": None, "hit_at_utc": None, "time_to_hit_seconds": None}
    target_price = min(0.99, entry_price * float(multiple))
    for row in path:
        exit_price = _to_float(row.get("exit_price"))
        if exit_price is not None and exit_price >= target_price:
            observed_at = row.get("observed_at")
            return {
                "hit": True,
                "target_price": target_price,
                "hit_at_utc": observed_at.astimezone(timezone.utc).isoformat() if observed_at else None,
                "time_to_hit_seconds": _seconds_between(entry_time, observed_at),
                "hit_exit_price": exit_price,
            }
    return {"hit": False, "target_price": target_price, "hit_at_utc": None, "time_to_hit_seconds": None}


def _path_sample(path: list[dict[str, Any]], *, limit: int = 25) -> list[dict[str, Any]]:
    sample = path[:limit]
    if len(path) > limit:
        sample = path[: max(1, limit // 2)] + path[-max(1, limit // 2) :]
    return [
        _path_row(row)
        for row in sample
    ]


def _path_rows(path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_path_row(row) for row in path]


def _path_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at_utc": row["observed_at"].astimezone(timezone.utc).isoformat(),
        "best_bid": row.get("best_bid"),
        "best_ask": row.get("best_ask"),
        "mid": row.get("mid"),
        "exit_price": row.get("exit_price"),
        "spread": row.get("spread"),
        "source": row.get("source"),
    }


def _entry_time(entry: dict[str, Any]) -> datetime | None:
    order_request = ((entry.get("submission") or {}).get("order_request") or {})
    return _parse_time(order_request.get("source_quote_at_utc") or entry.get("recorded_at_utc"))


def _entry_price(entry: dict[str, Any]) -> float | None:
    quality = entry.get("execution_quality") or ((entry.get("submission") or {}).get("execution_quality") or {})
    order_request = ((entry.get("submission") or {}).get("order_request") or {})
    for value in (
        quality.get("realized_price"),
        order_request.get("jit_best_ask"),
        order_request.get("observed_execution_price"),
        entry.get("price"),
    ):
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _entry_price_source(entry: dict[str, Any]) -> str | None:
    quality = entry.get("execution_quality") or ((entry.get("submission") or {}).get("execution_quality") or {})
    order_request = ((entry.get("submission") or {}).get("order_request") or {})
    if _to_float(quality.get("realized_price")) is not None:
        return "execution_quality.realized_price"
    if _to_float(order_request.get("jit_best_ask")) is not None:
        return "submission.order_request.jit_best_ask"
    if _to_float(order_request.get("observed_execution_price")) is not None:
        return "submission.order_request.observed_execution_price"
    if _to_float(entry.get("price")) is not None:
        return "entry.price"
    return None


def _filled_shares(entry: dict[str, Any]) -> float | None:
    quality = entry.get("execution_quality") or ((entry.get("submission") or {}).get("execution_quality") or {})
    for value in (quality.get("filled_shares"), entry.get("settled_size"), entry.get("size")):
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_end_time(event_slug: str) -> datetime | None:
    parts = str(event_slug or "").split("-")
    if len(parts) < 4:
        return None
    if parts[-2] != "5m":
        return None
    try:
        start = datetime.fromtimestamp(int(parts[-1]), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return start + timedelta(minutes=5)


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


def _mid(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2.0
    return best_bid if best_bid is not None else best_ask


def _seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _target_key(multiple: float) -> str:
    return str(float(multiple)).rstrip("0").rstrip(".")


__all__ = [
    "DEFAULT_TRACE_MULTIPLES",
    "PRICE_PATH_TRACE_REPORT_SCHEMA_VERSION",
    "PRICE_PATH_TRACE_SCHEMA_VERSION",
    "build_price_path_trace_report",
    "build_price_path_trace_report_from_paths",
    "render_price_path_trace_markdown",
    "write_price_path_trace_artifacts",
]
