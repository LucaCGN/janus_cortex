from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema


GammaFetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class SettlementRow:
    strategy_id: str
    event_key: str
    event_slug: str
    event_token_key: str
    outcome: str
    position_key: str
    order_key: str
    exchange_order_id: str | None
    shares: float
    fill_price: float
    cost_basis_usd: float
    resolved_outcome: str | None
    pnl_usd: float | None
    status: str


def reconcile_live_run_settlements(
    *,
    run_artifact_path: str | Path,
    db_path: str | Path | None = None,
    report_dir: str | Path = CENTRAL_ARTIFACT_ROOT / "reports",
    gamma_fetcher: GammaFetcher | None = None,
) -> dict[str, Any]:
    artifact_path = Path(run_artifact_path)
    payload = _read_json(artifact_path)
    run_id = str(payload.get("run_id") or artifact_path.stem)
    generated_at = datetime.now(UTC).isoformat()
    rows = _filled_rows(payload)
    gamma_fetcher = gamma_fetcher or fetch_gamma_event_by_slug
    resolved_by_slug: dict[str, dict[str, Any]] = {}
    settlement_rows: list[SettlementRow] = []
    blockers: list[str] = []

    for row in rows:
        event_slug = str(row.get("event_slug") or row.get("event_key") or "").strip()
        if not event_slug:
            blockers.append("filled_row_missing_event_slug")
            continue
        if event_slug not in resolved_by_slug:
            try:
                event_payload = gamma_fetcher(event_slug)
            except Exception as exc:  # noqa: BLE001 - report the provider error, do not crash the loop.
                event_payload = {"fetch_error": f"{type(exc).__name__}:{exc}"}
            resolved_by_slug[event_slug] = {
                "payload": event_payload,
                "resolved_outcome": resolve_outcome_from_gamma_payload(event_payload),
            }
        resolved = resolved_by_slug[event_slug]["resolved_outcome"]
        settlement_rows.append(_settlement_row_from_strategy_row(row, resolved_outcome=resolved))

    summary = _summarize_settlements(settlement_rows)
    if any(row.status == "unresolved" for row in settlement_rows):
        blockers.append("some_events_unresolved")
    db_counts = persist_settlement_performance(
        run_id=run_id,
        generated_at_utc=generated_at,
        settlement_rows=settlement_rows,
        event_payloads=resolved_by_slug,
        db_path=db_path,
    )
    report_payload = {
        "schema_version": "crypto_options_live_settlement_performance_v1",
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "source_artifact": str(artifact_path),
        "status": "settled" if not blockers else "partial",
        "blockers": sorted(set(blockers)),
        "summary": summary,
        "db_counts": db_counts,
        "manual_orders_avoided": True,
        "rows": [_settlement_row_payload(row) for row in settlement_rows],
    }
    report_root = Path(report_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{run_id}_settlement_performance.json"
    latest_path = report_root / "settlement_performance_latest.json"
    report_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    latest_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return report_payload | {"report_json": str(report_path)}


def fetch_gamma_event_by_slug(event_slug: str) -> dict[str, Any]:
    url = f"https://gamma-api.polymarket.com/events/slug/{event_slug}"
    request = urllib.request.Request(url, headers={"User-Agent": "janus-cortex-crypto-options-app/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed public Gamma API URL.
        return json.loads(response.read().decode("utf-8"))


def resolve_outcome_from_gamma_payload(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("eventMetadata") if isinstance(payload.get("eventMetadata"), dict) else {}
    final_price = _optional_float(metadata.get("finalPrice"))
    price_to_beat = _optional_float(metadata.get("priceToBeat"))
    if final_price is not None and price_to_beat is not None:
        return "Up" if final_price >= price_to_beat else "Down"

    markets = payload.get("markets")
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            outcomes = _json_list(market.get("outcomes"))
            outcome_prices = [_optional_float(value) for value in _json_list(market.get("outcomePrices"))]
            if outcomes and outcome_prices and len(outcomes) == len(outcome_prices):
                best_index, best_price = max(enumerate(outcome_prices), key=lambda item: -1.0 if item[1] is None else item[1])
                if best_price is not None and best_price >= 0.99:
                    return _normalize_outcome(str(outcomes[best_index])).title()
    return None


def persist_settlement_performance(
    *,
    run_id: str,
    generated_at_utc: str,
    settlement_rows: list[SettlementRow],
    event_payloads: dict[str, dict[str, Any]],
    db_path: str | Path | None = None,
) -> dict[str, int]:
    path = initialize_schema(db_path)
    counts = {"event_outcomes": 0, "settlements": 0, "positions": 0, "exit_plans": 0, "pnl_snapshots": 0}
    with connect(path) as conn:
        for event_slug, resolved_payload in event_payloads.items():
            resolved = resolved_payload.get("resolved_outcome")
            payload = resolved_payload.get("payload") if isinstance(resolved_payload.get("payload"), dict) else {}
            if not resolved:
                continue
            _persist_event_outcome(conn, event_key=event_slug, resolved_outcome=str(resolved), generated_at_utc=generated_at_utc, payload=payload)
            counts["event_outcomes"] += 1
            _persist_settlement(conn, event_key=event_slug, resolved_outcome=str(resolved), generated_at_utc=generated_at_utc, payload=payload)
            counts["settlements"] += 1
        for row in settlement_rows:
            if row.status != "settled":
                continue
            before = conn.total_changes
            conn.execute(
                """
                UPDATE positions
                SET status = ?, updated_at_utc = ?
                WHERE position_key = ?
                """,
                ("settled", generated_at_utc, row.position_key),
            )
            counts["positions"] += conn.total_changes - before
            before = conn.total_changes
            conn.execute(
                """
                UPDATE exit_plans
                SET status = ?, updated_at_utc = ?
                WHERE position_key = ?
                """,
                ("settled", generated_at_utc, row.position_key),
            )
            counts["exit_plans"] += conn.total_changes - before
        for strategy_id, snapshot in _strategy_snapshots(settlement_rows).items():
            key = f"pnl-snapshot:{run_id}:{strategy_id}:{generated_at_utc}"
            conn.execute(
                """
                INSERT INTO pnl_snapshots(
                    pnl_snapshot_key, strategy_id, run_id, computed_at_utc,
                    realized_pnl_usd, unrealized_pnl_usd, active_cost_usd, snapshot_json, inserted_at_utc
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pnl_snapshot_key) DO UPDATE SET
                    realized_pnl_usd=excluded.realized_pnl_usd,
                    unrealized_pnl_usd=excluded.unrealized_pnl_usd,
                    active_cost_usd=excluded.active_cost_usd,
                    snapshot_json=excluded.snapshot_json
                """,
                (
                    key,
                    strategy_id,
                    run_id,
                    generated_at_utc,
                    snapshot["realized_pnl_usd"],
                    snapshot["unrealized_pnl_usd"],
                    snapshot["active_cost_usd"],
                    json.dumps(snapshot, sort_keys=True),
                    generated_at_utc,
                ),
            )
            counts["pnl_snapshots"] += 1
    return counts


def _persist_event_outcome(
    conn: Any,
    *,
    event_key: str,
    resolved_outcome: str,
    generated_at_utc: str,
    payload: dict[str, Any],
) -> None:
    key = f"event-outcome:{event_key}"
    conn.execute(
        """
        INSERT INTO event_outcomes(event_outcome_key, event_key, resolved_outcome, resolved_at_utc, source_json, inserted_at_utc, updated_at_utc)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_outcome_key) DO UPDATE SET
            resolved_outcome=excluded.resolved_outcome,
            resolved_at_utc=excluded.resolved_at_utc,
            source_json=excluded.source_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        (key, event_key, resolved_outcome, generated_at_utc, json.dumps(payload, sort_keys=True), generated_at_utc, generated_at_utc),
    )


def _persist_settlement(
    conn: Any,
    *,
    event_key: str,
    resolved_outcome: str,
    generated_at_utc: str,
    payload: dict[str, Any],
) -> None:
    key = f"settlement:{event_key}"
    conn.execute(
        """
        INSERT INTO settlements(settlement_key, event_key, resolved_outcome, settled_at_utc, settlement_json, inserted_at_utc)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(settlement_key) DO UPDATE SET
            resolved_outcome=excluded.resolved_outcome,
            settled_at_utc=excluded.settled_at_utc,
            settlement_json=excluded.settlement_json
        """,
        (key, event_key, resolved_outcome, generated_at_utc, json.dumps(payload, sort_keys=True), generated_at_utc),
    )


def _filled_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("strategy_rows") if isinstance(payload.get("strategy_rows"), list) else []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("status") == "live_structural_executed"
        and row.get("order_status") == "filled"
        and _optional_float(row.get("filled_shares")) is not None
        and _optional_float(row.get("fill_price")) is not None
        and row.get("position_key")
    ]


def _settlement_row_from_strategy_row(row: dict[str, Any], *, resolved_outcome: str | None) -> SettlementRow:
    shares = float(_optional_float(row.get("filled_shares")) or 0.0)
    fill_price = float(_optional_float(row.get("fill_price")) or 0.0)
    cost = round(shares * fill_price, 8)
    outcome = _normalize_outcome(str(row.get("outcome") or ""))
    normalized_resolved = _normalize_outcome(resolved_outcome or "") if resolved_outcome else None
    if normalized_resolved is None:
        pnl = None
        status = "unresolved"
    else:
        payout = shares if outcome == normalized_resolved else 0.0
        pnl = round(payout - cost, 6)
        status = "settled"
    return SettlementRow(
        strategy_id=str(row.get("strategy_id") or "unknown_strategy"),
        event_key=str(row.get("event_key") or row.get("event_slug") or ""),
        event_slug=str(row.get("event_slug") or row.get("event_key") or ""),
        event_token_key=str(row.get("event_token_key") or ""),
        outcome=outcome.title() if outcome else "",
        position_key=str(row.get("position_key") or ""),
        order_key=str(row.get("order_key") or ""),
        exchange_order_id=str(row.get("exchange_order_id") or "") or None,
        shares=shares,
        fill_price=fill_price,
        cost_basis_usd=cost,
        resolved_outcome=None if normalized_resolved is None else normalized_resolved.title(),
        pnl_usd=pnl,
        status=status,
    )


def _summarize_settlements(rows: list[SettlementRow]) -> dict[str, Any]:
    settled = [row for row in rows if row.status == "settled" and row.pnl_usd is not None]
    by_strategy = _strategy_snapshots(rows)
    return {
        "filled_position_count": len(rows),
        "settled_position_count": len(settled),
        "unresolved_position_count": len(rows) - len(settled),
        "realized_pnl_usd": round(sum(row.pnl_usd or 0.0 for row in settled), 6),
        "wins": sum(1 for row in settled if (row.pnl_usd or 0.0) > 0),
        "losses": sum(1 for row in settled if (row.pnl_usd or 0.0) <= 0),
        "win_rate": None if not settled else round(sum(1 for row in settled if (row.pnl_usd or 0.0) > 0) / len(settled), 6),
        "by_strategy": by_strategy,
    }


def _strategy_snapshots(rows: list[SettlementRow]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[SettlementRow]] = defaultdict(list)
    for row in rows:
        grouped[row.strategy_id].append(row)
    snapshots: dict[str, dict[str, Any]] = {}
    for strategy_id, strategy_rows in grouped.items():
        settled = [row for row in strategy_rows if row.status == "settled" and row.pnl_usd is not None]
        unresolved = [row for row in strategy_rows if row.status != "settled"]
        wins = sum(1 for row in settled if (row.pnl_usd or 0.0) > 0)
        losses = sum(1 for row in settled if (row.pnl_usd or 0.0) <= 0)
        snapshots[strategy_id] = {
            "strategy_id": strategy_id,
            "filled_position_count": len(strategy_rows),
            "settled_position_count": len(settled),
            "unresolved_position_count": len(unresolved),
            "wins": wins,
            "losses": losses,
            "win_rate": None if not settled else round(wins / len(settled), 6),
            "realized_pnl_usd": round(sum(row.pnl_usd or 0.0 for row in settled), 6),
            "unrealized_pnl_usd": round(-sum(row.cost_basis_usd for row in unresolved), 6) if unresolved else 0.0,
            "active_cost_usd": round(sum(row.cost_basis_usd for row in unresolved), 6),
        }
    return snapshots


def _settlement_row_payload(row: SettlementRow) -> dict[str, Any]:
    return {
        "strategy_id": row.strategy_id,
        "event_key": row.event_key,
        "event_slug": row.event_slug,
        "event_token_key": row.event_token_key,
        "outcome": row.outcome,
        "position_key": row.position_key,
        "order_key": row.order_key,
        "exchange_order_id": row.exchange_order_id,
        "shares": row.shares,
        "fill_price": row.fill_price,
        "cost_basis_usd": row.cost_basis_usd,
        "resolved_outcome": row.resolved_outcome,
        "pnl_usd": row.pnl_usd,
        "status": row.status,
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_outcome(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"yes", "up"}:
        return "up"
    if normalized in {"no", "down"}:
        return "down"
    return normalized
