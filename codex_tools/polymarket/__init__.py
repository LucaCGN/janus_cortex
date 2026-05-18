"""Direct Polymarket fallback planning surface.

This package is intentionally non-executing in its first slice. It can build
gate decisions and deterministic ledger records, but it does not submit,
cancel, replace, or prepare orders.
"""

from codex_tools.polymarket.execution_gate import (
    PolymarketExecutionGateSnapshot,
    PolymarketFallbackDecision,
    PolymarketFallbackIntent,
    build_fallback_decision,
    derive_idempotency_key,
)
from codex_tools.polymarket.ledger import (
    PolymarketFallbackLedgerWrite,
    build_ledger_entry,
    default_fallback_ledger_root,
    write_fallback_decision_ledger,
)

__all__ = [
    "PolymarketExecutionGateSnapshot",
    "PolymarketFallbackDecision",
    "PolymarketFallbackIntent",
    "PolymarketFallbackLedgerWrite",
    "build_fallback_decision",
    "build_ledger_entry",
    "default_fallback_ledger_root",
    "derive_idempotency_key",
    "write_fallback_decision_ledger",
]
