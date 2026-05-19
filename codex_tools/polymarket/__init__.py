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
from codex_tools.polymarket.preview import (
    PREVIEW_SCHEMA_VERSION,
    PolymarketFallbackPreview,
    build_fallback_preview,
)
from codex_tools.polymarket.safety import (
    DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS,
    DEFAULT_MIN_BUY_NOTIONAL_USD,
    DEFAULT_MIN_ORDER_SIZE,
    PolymarketSafetyCheck,
    build_polymarket_safety_gate_snapshot,
    evaluate_direct_truth_freshness,
    evaluate_kill_switch,
    evaluate_minimum_order_policy,
    evaluate_risk_budget,
    evaluate_target_stop_rebuy_policy,
)
from codex_tools.polymarket.account import (
    ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
    PolymarketAccountReadSnapshot,
    read_account_snapshot,
)
from codex_tools.polymarket.grid_service import (
    GRID_SERVICE_SCHEMA_VERSION,
    PolymarketGridCandidate,
    PolymarketGridServicePreview,
    build_grid_service_preview,
)

__all__ = [
    "ACCOUNT_SNAPSHOT_SCHEMA_VERSION",
    "DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS",
    "DEFAULT_MIN_BUY_NOTIONAL_USD",
    "DEFAULT_MIN_ORDER_SIZE",
    "GRID_SERVICE_SCHEMA_VERSION",
    "PREVIEW_SCHEMA_VERSION",
    "PolymarketAccountReadSnapshot",
    "PolymarketExecutionGateSnapshot",
    "PolymarketFallbackDecision",
    "PolymarketFallbackIntent",
    "PolymarketFallbackLedgerWrite",
    "PolymarketFallbackPreview",
    "PolymarketGridCandidate",
    "PolymarketGridServicePreview",
    "PolymarketSafetyCheck",
    "build_fallback_decision",
    "build_fallback_preview",
    "build_grid_service_preview",
    "build_ledger_entry",
    "build_polymarket_safety_gate_snapshot",
    "default_fallback_ledger_root",
    "derive_idempotency_key",
    "evaluate_direct_truth_freshness",
    "evaluate_kill_switch",
    "evaluate_minimum_order_policy",
    "evaluate_risk_budget",
    "evaluate_target_stop_rebuy_policy",
    "read_account_snapshot",
    "write_fallback_decision_ledger",
]
