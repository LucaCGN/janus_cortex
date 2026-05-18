"""Local ledger persistence for inert Polymarket fallback decisions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.runtime.local_paths import resolve_shared_root
from codex_tools.polymarket.execution_gate import (
    NO_EXECUTION_STATEMENT,
    PolymarketFallbackDecision,
)

LEDGER_SCHEMA_VERSION = "polymarket_fallback_ledger_entry_v1"
LEDGER_FILE_NAME = "fallback_decisions.jsonl"


@dataclass(frozen=True)
class PolymarketFallbackLedgerWrite:
    schema_version: str
    status: str
    ledger_path: str
    ledger_id: str
    idempotency_key: str
    created: bool
    no_execution_statement: str


def default_fallback_ledger_root() -> Path:
    return (
        resolve_shared_root()
        / "artifacts"
        / "codex-tools"
        / "polymarket"
        / "fallback-ledger"
    )


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _existing_ledger_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()

    ledger_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ledger_id = entry.get("ledger_id") or entry.get("decision", {}).get("ledger_id")
        if isinstance(ledger_id, str):
            ledger_ids.add(ledger_id)
    return ledger_ids


def build_ledger_entry(
    decision: PolymarketFallbackDecision,
    *,
    written_at_utc: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _coerce_utc(written_at_utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "written_at_utc": timestamp,
        "ledger_id": decision.ledger_id,
        "idempotency_key": decision.idempotency_key,
        "status": decision.status,
        "order_submission_attempted": decision.order_submission_attempted,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
        "decision": asdict(decision),
    }


def write_fallback_decision_ledger(
    decision: PolymarketFallbackDecision,
    *,
    ledger_root: Path | None = None,
    written_at_utc: datetime | None = None,
) -> PolymarketFallbackLedgerWrite:
    timestamp = _coerce_utc(written_at_utc)
    root = ledger_root if ledger_root is not None else default_fallback_ledger_root()
    ledger_dir = Path(root) / timestamp.date().isoformat()
    ledger_path = ledger_dir / LEDGER_FILE_NAME

    ledger_dir.mkdir(parents=True, exist_ok=True)
    if decision.ledger_id in _existing_ledger_ids(ledger_path):
        return PolymarketFallbackLedgerWrite(
            schema_version="polymarket_fallback_ledger_write_v1",
            status="already_recorded",
            ledger_path=str(ledger_path),
            ledger_id=decision.ledger_id,
            idempotency_key=decision.idempotency_key,
            created=False,
            no_execution_statement=NO_EXECUTION_STATEMENT,
        )

    entry = build_ledger_entry(decision, written_at_utc=timestamp)
    with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")))
        handle.write("\n")

    return PolymarketFallbackLedgerWrite(
        schema_version="polymarket_fallback_ledger_write_v1",
        status="written",
        ledger_path=str(ledger_path),
        ledger_id=decision.ledger_id,
        idempotency_key=decision.idempotency_key,
        created=True,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


__all__ = [
    "LEDGER_FILE_NAME",
    "LEDGER_SCHEMA_VERSION",
    "PolymarketFallbackLedgerWrite",
    "build_ledger_entry",
    "default_fallback_ledger_root",
    "write_fallback_decision_ledger",
]
