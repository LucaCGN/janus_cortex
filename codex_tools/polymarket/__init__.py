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

__all__ = [
    "PolymarketExecutionGateSnapshot",
    "PolymarketFallbackDecision",
    "PolymarketFallbackIntent",
    "build_fallback_decision",
    "derive_idempotency_key",
]
