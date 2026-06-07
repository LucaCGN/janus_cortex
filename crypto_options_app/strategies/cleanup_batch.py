from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect
from crypto_options_app.signals.validation.result_store import validation_status
from crypto_options_app.strategies.promotion import promotion_policy_contract
from crypto_options_app.strategies.revision_scout import build_strategy_revision_scout


CLEANUP_BATCH_SCHEMA_VERSION = "crypto_options_signal_strategy_cleanup_batch_v1"

TERMINAL_PROMOTED_STATES = {"PROMOTED", "LIVE_CANDIDATE", "LIVE_RUNNING"}
TERMINAL_RETIRED_STATES = {"RETIRED", "RETIRED_FROM_PRIMARY"}
BLOCKED_STATES = {"BLOCKED", "REVIEW_BLOCKED"}
REVISION_STATES = {"NEEDS_V2_REVIEW", "REVISION_REQUIRED", "SHADOW_REVIEW", "DEMOTED_TO_SHADOW"}

CLASSIFICATION_PRIORITY = {
    "BLOCKED": 0,
    "NEEDS_VARIANT": 1,
    "STRICT_REPLAY_REQUIRED": 2,
    "SHADOW_REQUIRED": 3,
    "REVIEW": 4,
    "PROMOTED": 5,
    "RETIRED": 6,
}


def build_signal_strategy_cleanup_batch(
    conn: Any,
    *,
    max_signals: int = 24,
    max_strategies: int = 12,
) -> dict[str, Any]:
    """Build a bounded read-only cleanup packet for fixed chats and queue workers."""

    contract = promotion_policy_contract()
    signal_status = validation_status(conn)
    scout = build_strategy_revision_scout(conn)
    signal_rows = [classify_signal_cleanup_row(row, policy_contract=contract) for row in signal_status.get("signals") or []]
    strategy_rows = [
        classify_strategy_cleanup_row(row, policy_contract=contract)
        for row in (scout.get("strategy_summary") or {}).get("strategies", [])
    ]
    selected_signals = _bounded_rows(signal_rows, max_items=max_signals)
    selected_strategies = _bounded_rows(strategy_rows, max_items=max_strategies)
    return {
        "schema_version": CLEANUP_BATCH_SCHEMA_VERSION,
        "policy_contract_schema_version": contract["schema_version"],
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
        "limits": {
            "max_signals": max(0, int(max_signals)),
            "max_strategies": max(0, int(max_strategies)),
        },
        "summary": {
            "signal_total": len(signal_rows),
            "strategy_total": len(strategy_rows),
            "signal_classification_counts": _classification_counts(signal_rows),
            "strategy_classification_counts": _classification_counts(strategy_rows),
            "recommended_next_lanes": scout.get("recommended_next_lanes") or [],
        },
        "signals": selected_signals,
        "strategies": selected_strategies,
    }


def classify_signal_cleanup_row(row: dict[str, Any], *, policy_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = policy_contract or promotion_policy_contract()
    signal_policy = contract.get("signals") if isinstance(contract.get("signals"), dict) else {}
    promotable_state = str(signal_policy.get("promotable_state") or "PROMOTION_READY")
    not_promotable_labels = {str(label) for label in signal_policy.get("not_promotable_labels") or ()}
    promotion_state = str(row.get("promotion_state") or "UNKNOWN")
    queue_status = str(row.get("queue_status") or row.get("status") or "")
    strict_reviews = _list(row.get("strict_review_reasons") or row.get("strict_review"))
    blockers = _list(row.get("structural_blockers") or row.get("blockers"))
    action_state = str(row.get("action_state") or "")

    if promotion_state in TERMINAL_RETIRED_STATES:
        classification = "RETIRED"
        action = "Keep retired unless a new source gap justifies a fresh variant."
    elif promotion_state in BLOCKED_STATES or queue_status in {"FAILED", "BLOCKED"}:
        classification = "BLOCKED"
        action = "Resolve explicit blocker before any replay or strategy dependency."
    elif promotion_state in not_promotable_labels or action_state == "STRICT_REPLAY_REQUIRED":
        classification = "STRICT_REPLAY_REQUIRED"
        action = "Run strict replay/live-shadow before this signal can feed strategy promotion."
    elif promotion_state == promotable_state and not strict_reviews and not blockers:
        classification = "PROMOTED"
        action = "Eligible as a signal building block; strategy promotion still requires economic proof."
    elif promotion_state in REVISION_STATES:
        classification = "NEEDS_VARIANT"
        action = "Retire or create a V2-V5 only if it fixes a concrete blocker or source gap."
    else:
        classification = "REVIEW"
        action = "Inspect evidence and assign promote, retire, block, or variant decision."

    return {
        "kind": "signal",
        "cleanup_classification": classification,
        "priority": CLASSIFICATION_PRIORITY[classification],
        "signal_id": row.get("signal_id") or row.get("id"),
        "signal_type": row.get("signal_type") or row.get("type"),
        "variant": row.get("variant"),
        "version": row.get("version") or "v1",
        "source_blocks": row.get("source_blocks") or row.get("required_data_blocks") or [],
        "promotion_state": promotion_state,
        "queue_status": queue_status or None,
        "action_state": action_state or None,
        "strict_review_reasons": strict_reviews,
        "blockers": blockers,
        "next_action": row.get("next_action") or row.get("selection_next_action") or action,
        "cleanup_next_action": action,
    }


def classify_strategy_cleanup_row(row: dict[str, Any], *, policy_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = policy_contract or promotion_policy_contract()
    requirements = ((contract.get("strategies") or {}).get("live_candidate_requirements") or {})
    promotion_state = str(row.get("promotion_state") or "UNKNOWN")
    blockers = _list(row.get("blockers"))
    recent_samples = int(row.get("recent_samples") or 0)
    recent_win_rate = _float_or_none(row.get("recent_win_rate"))
    recent_pnl = float(row.get("recent_pnl") or 0.0)
    sample_threshold = int(requirements.get("recent_distinct_economic_samples") or 12)
    win_rate_threshold = float(requirements.get("recent_shadow_live_win_rate_gt") or 0.70)
    pnl_threshold = float(requirements.get("recent_shadow_live_pnl_usd_gt") or 0.0)
    clears_policy = bool(row.get("clears_live_policy"))

    if promotion_state in TERMINAL_PROMOTED_STATES and clears_policy and not blockers:
        classification = "PROMOTED"
        action = "Promotion-manager eligible; live still requires supervised runtime authority."
    elif promotion_state in BLOCKED_STATES or _has_hard_strategy_blocker(blockers):
        classification = "BLOCKED"
        action = "Stop promotion path until lifecycle, reconciliation, or drift blocker is fixed."
    elif promotion_state in REVISION_STATES:
        classification = "NEEDS_VARIANT"
        action = "Revise or retire; do not rerun unchanged failed strategy lanes."
    elif recent_samples < sample_threshold or recent_win_rate is None or recent_win_rate <= win_rate_threshold or recent_pnl <= pnl_threshold:
        classification = "SHADOW_REQUIRED"
        action = "Accumulate policy-compliant shadow/live-replay economics before live consideration."
    else:
        classification = "REVIEW"
        action = "Evidence is near policy; inspect blockers, event diversity, and drift before queueing next step."

    return {
        "kind": "strategy",
        "cleanup_classification": classification,
        "priority": CLASSIFICATION_PRIORITY[classification],
        "strategy_id": row.get("strategy_id"),
        "promotion_state": promotion_state,
        "recent_samples": recent_samples,
        "recent_win_rate": recent_win_rate,
        "recent_pnl": recent_pnl,
        "clears_live_policy": clears_policy,
        "blockers": blockers,
        "next_action": row.get("next_action") or action,
        "cleanup_next_action": action,
    }


def write_signal_strategy_cleanup_batch_report(payload: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Signal And Strategy Cleanup Batch",
        "",
        f"Generated UTC: `{payload['generated_at_utc']}`",
        f"Policy contract: `{payload.get('policy_contract_schema_version')}`",
        "",
        "## Summary",
        "",
        f"- Signal rows: `{payload['summary']['signal_total']}`",
        f"- Strategy rows: `{payload['summary']['strategy_total']}`",
        f"- Signal classifications: `{json.dumps(payload['summary']['signal_classification_counts'], sort_keys=True)}`",
        f"- Strategy classifications: `{json.dumps(payload['summary']['strategy_classification_counts'], sort_keys=True)}`",
        "",
        "## Signal Batch",
        "",
    ]
    lines.extend(_markdown_rows(payload.get("signals") or [], id_key="signal_id"))
    lines.extend(["", "## Strategy Batch", ""])
    lines.extend(_markdown_rows(payload.get("strategies") or [], id_key="strategy_id"))
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This batch is read-only.",
            "- It does not authorize orders or live trading.",
            "- `PROMOTED` here means cleanup classification only; live promotion still requires the promotion manager and supervised runtime gates.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_signal_strategy_cleanup_batch_db(
    db_path: str | Path | None = None,
    *,
    max_signals: int = 24,
    max_strategies: int = 12,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        return build_signal_strategy_cleanup_batch(conn, max_signals=max_signals, max_strategies=max_strategies)


def _bounded_rows(rows: list[dict[str, Any]], *, max_items: int) -> list[dict[str, Any]]:
    if max_items <= 0:
        return []
    return sorted(rows, key=lambda row: (int(row.get("priority") or 99), str(row.get("signal_id") or row.get("strategy_id") or "")))[:max_items]


def _classification_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("cleanup_classification") or "UNKNOWN") for row in rows)
    return dict(sorted(counts.items()))


def _markdown_rows(rows: list[dict[str, Any]], *, id_key: str) -> list[str]:
    if not rows:
        return ["No rows in this bounded batch."]
    lines = ["| Classification | Id | State | Blockers | Next action |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        blockers = ", ".join(str(item) for item in row.get("blockers") or row.get("strict_review_reasons") or []) or "none"
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    row.get("cleanup_classification"),
                    row.get(id_key),
                    row.get("promotion_state"),
                    blockers,
                    row.get("cleanup_next_action") or row.get("next_action"),
                )
            )
            + " |"
        )
    return lines


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/")


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in {None, ""}]
    if isinstance(value, tuple):
        return [str(item) for item in value if item not in {None, ""}]
    if value is None or value == "":
        return []
    return [str(value)]


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_hard_strategy_blocker(blockers: list[str]) -> bool:
    hard_fragments = (
        "lifecycle",
        "reconciliation",
        "drift",
        "hard_stop",
        "negative_realized_pnl",
    )
    return any(any(fragment in blocker for fragment in hard_fragments) for blocker in blockers)
