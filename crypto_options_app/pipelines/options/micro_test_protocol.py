from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_MICRO_TEST_PROTOCOL_SCHEMA_VERSION = "crypto_options_micro_test_protocol_v1"
PROFILE_MICRO_TEST_STRATEGY_IDS = (
    "profile_late_btc_deep_60_120_validation",
    "profile_mid_fade_60s_validation",
    "profile_mid_fade_lower_mid_validation",
)


def build_micro_test_protocol(
    aggregate_payload: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    max_budget_usd: float = 20.0,
    min_order_size: float = 5.0,
    user_approval_recorded: bool = False,
    allowed_strategy_ids: list[str] | tuple[str, ...] | None = None,
    max_test_trades_before_review: int = 5,
    hard_stop_full_losses: int = 2,
    hard_stop_loss_usd: float = 3.0,
    max_position_cost_usd: float = 5.0,
) -> dict[str, Any]:
    """Build a read-only protocol for a possible tiny live test.

    This function never authorizes execution. It converts research evidence into
    a checklist that must be explicitly approved before any separate live step.
    """

    generated_at = generated_at or datetime.now(timezone.utc)
    allowed_strategy_ids = [str(item) for item in allowed_strategy_ids or ["late_convergence_min_size"]]
    if allowed_strategy_ids != ["late_convergence_min_size"]:
        return _build_multi_strategy_micro_test_protocol(
            aggregate_payload,
            generated_at=generated_at,
            max_budget_usd=max_budget_usd,
            min_order_size=min_order_size,
            user_approval_recorded=user_approval_recorded,
            allowed_strategy_ids=allowed_strategy_ids,
            max_test_trades_before_review=max_test_trades_before_review,
            hard_stop_full_losses=hard_stop_full_losses,
            hard_stop_loss_usd=hard_stop_loss_usd,
            max_position_cost_usd=max_position_cost_usd,
        )

    late = _strategy_metrics(aggregate_payload, "late_convergence_min_size")
    cheap = _strategy_metrics(aggregate_payload, "cheap_tail_min_size")
    late_gate = late.get("minimum_live_test_gate_report") or {}
    cheap_gate = cheap.get("minimum_live_test_gate_report") or {}

    blockers: list[str] = []
    warnings: list[str] = []
    if not late:
        blockers.append("missing_late_convergence_aggregate_metrics")
    elif not late_gate.get("ready"):
        blockers.append("late_convergence_aggregate_gate_not_ready")
    if not cheap:
        warnings.append("missing_cheap_tail_aggregate_metrics")
    elif cheap_gate.get("ready"):
        blockers.append("cheap_tail_unexpectedly_ready_manual_review_required")
    if not user_approval_recorded:
        blockers.append("explicit_user_approval_missing")

    protocol_status = "ready_for_user_approval" if blockers == ["explicit_user_approval_missing"] else "blocked"
    if user_approval_recorded and not blockers:
        protocol_status = "approved_protocol_ready_for_separate_execution_design"

    payload = {
        "schema_version": CRYPTO_OPTIONS_MICRO_TEST_PROTOCOL_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "source_aggregate_schema_version": aggregate_payload.get("schema_version"),
        "source_generated_at_utc": aggregate_payload.get("generated_at_utc"),
        "source_capture_dirs": aggregate_payload.get("capture_dirs") or [],
        "source_data_counts": aggregate_payload.get("data_counts") or {},
        "protocol_status": protocol_status,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "user_approval_recorded": bool(user_approval_recorded),
        "allowed_strategy": "late_convergence_min_size",
        "rejected_strategies": [
            {
                "strategy_id": "cheap_tail_min_size",
                "reason": "aggregate gate failed; zero-win profile in current evidence",
                "failed_gates": cheap_gate.get("failed_gates") or [],
            }
        ],
        "budget": {
            "max_total_budget_usd": float(max_budget_usd),
            "min_order_size_shares": float(min_order_size),
            "max_position_shares": float(min_order_size),
            "max_open_positions": 1,
            "max_orders_per_5m_window": 1,
            "max_simultaneous_markets": 1,
            "no_ladders": True,
            "no_position_averaging": True,
            "no_overlapping_windows": True,
            "hard_stop_full_losses": int(hard_stop_full_losses),
            "hard_stop_loss_usd": float(hard_stop_loss_usd),
            "max_position_cost_usd": float(max_position_cost_usd),
            "preferred_stop_full_losses": 1,
            "max_test_trades_before_review": int(max_test_trades_before_review),
        },
        "entry_gate": {
            "strategy_id": "late_convergence_min_size",
            "requires_current_decision_review_candidate": True,
            "candidate_status_required": "candidate_for_manual_review",
            "outcome_quote_meaning": "Polymarket executable ask, not BTC or ETH underlying price",
            "time_remaining_seconds_min": 30,
            "time_remaining_seconds_max": 120,
            "best_ask_min": 0.90,
            "best_ask_max": 0.98,
            "spread_max": 0.02,
            "depth_top3_ask_size_min": 100.0,
            "top_ask_size_min": float(min_order_size),
            "quote_age_seconds_max": 5.0,
            "underlying_proxy_age_seconds_max": 5.0,
            "settlement_window_known": True,
            "manual_quote_reconciliation_required": True,
        },
        "pre_trade_reconciliation": [
            "Regenerate a live decision review no more than 5 seconds before considering entry.",
            "Confirm the candidate event slug, token outcome, best ask, spread, top ask size, and top-3 ask depth still match the protocol gate.",
            "Confirm total cost for exactly 5 shares including taker fee is within remaining test budget.",
            f"Confirm total ticket cost is at or below ${float(max_position_cost_usd):.2f}.",
            "Confirm no other crypto-options micro-test position is open.",
            "Confirm the market window has at least 30 seconds and at most 120 seconds remaining.",
            "Confirm the candidate is late_convergence_min_size and not cheap_tail_min_size.",
        ],
        "kill_switches": [
            "Stop after one full-loss trade for review unless user explicitly approves continuing to the second-loss hard stop.",
            "Stop immediately after two full-loss trades.",
            f"Stop immediately once net realized loss reaches ${float(hard_stop_loss_usd):.2f}.",
            "Stop if a regenerated decision review shows quote_age_seconds above 5 seconds.",
            "Stop if spread exceeds 2c or top-3 ask depth drops below 100 shares.",
            "Stop if any order is partially filled, rejected, delayed, or reconciles at a worse price than the candidate ask.",
            "Stop if settlement reconciliation disagrees with the expected Polymarket outcome path.",
        ],
        "post_trade_review": [
            "Record event slug, side, token id, order timestamp, observed ask, fill price, filled shares, fee, and total cost.",
            "Record settlement outcome and net PnL per trade.",
            "Compare live fill price against the candidate review best_ask.",
            "Do not open the next position until the previous position has resolved and been reviewed.",
        ],
        "evidence_summary": _evidence_summary(late),
        "blockers": blockers,
        "warnings": warnings,
        "next_required_action": _next_required_action(protocol_status),
    }
    return strict_jsonable(payload)


def _build_multi_strategy_micro_test_protocol(
    aggregate_payload: dict[str, Any],
    *,
    generated_at: datetime,
    max_budget_usd: float,
    min_order_size: float,
    user_approval_recorded: bool,
    allowed_strategy_ids: list[str],
    max_test_trades_before_review: int,
    hard_stop_full_losses: int,
    hard_stop_loss_usd: float,
    max_position_cost_usd: float,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = ["profile_strategy_micro_test_is_experimental_manual_only"]
    strategy_rows = [_strategy_metrics(aggregate_payload, strategy_id) for strategy_id in allowed_strategy_ids]
    missing = [strategy_id for strategy_id, row in zip(allowed_strategy_ids, strategy_rows, strict=True) if not row]
    if missing:
        blockers.extend([f"missing_strategy_metrics:{strategy_id}" for strategy_id in missing])
    for strategy_id, row in zip(allowed_strategy_ids, strategy_rows, strict=True):
        gate = row.get("minimum_live_test_gate_report") or {}
        if gate.get("ready") is not True:
            warnings.append(f"strategy_not_gate_ready:{strategy_id}")
    if not user_approval_recorded:
        blockers.append("explicit_user_approval_missing")

    protocol_status = "blocked"
    if user_approval_recorded and not blockers:
        protocol_status = "approved_protocol_ready_for_separate_execution_design"
    elif blockers == ["explicit_user_approval_missing"]:
        protocol_status = "ready_for_user_approval"

    budget = {
        "max_total_budget_usd": float(max_budget_usd),
        "min_order_size_shares": float(min_order_size),
        "max_position_shares": float(min_order_size),
        "max_open_positions": 1,
        "max_orders_per_5m_window": 1,
        "max_simultaneous_markets": 1,
        "no_ladders": True,
        "no_position_averaging": True,
        "no_overlapping_windows": True,
        "hard_stop_full_losses": int(hard_stop_full_losses),
        "hard_stop_loss_usd": float(hard_stop_loss_usd),
        "max_position_cost_usd": float(max_position_cost_usd),
        "preferred_stop_full_losses": 1,
        "max_test_trades_before_review": int(max_test_trades_before_review),
    }
    entry_gates = [_profile_entry_gate(strategy_id, min_order_size=min_order_size) for strategy_id in allowed_strategy_ids]
    payload = {
        "schema_version": CRYPTO_OPTIONS_MICRO_TEST_PROTOCOL_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "source_aggregate_schema_version": aggregate_payload.get("schema_version"),
        "source_generated_at_utc": aggregate_payload.get("generated_at_utc"),
        "source_capture_dirs": aggregate_payload.get("capture_dirs") or [],
        "source_data_counts": aggregate_payload.get("data_counts") or {},
        "protocol_status": protocol_status,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "user_approval_recorded": bool(user_approval_recorded),
        "allowed_strategy": allowed_strategy_ids[0] if allowed_strategy_ids else None,
        "allowed_strategies": allowed_strategy_ids,
        "entry_gate": entry_gates[0] if entry_gates else {},
        "entry_gates": entry_gates,
        "budget": budget,
        "rejected_strategies": [],
        "pre_trade_reconciliation": [
            "Regenerate a live decision review no more than 5 seconds before considering entry.",
            "Confirm the candidate strategy, event slug, token outcome, best ask, spread, top ask size, and top-3 ask depth still match that strategy gate.",
            "Confirm total cost for exactly 5 shares including taker fee is within remaining test budget.",
            f"Confirm total ticket cost is at or below ${float(max_position_cost_usd):.2f}.",
            "Confirm no other crypto-options micro-test position is open.",
            "Confirm the manual test ledger has fewer than 20 trades and fewer than 3 settled full losses.",
            f"Confirm the manual test ledger net realized loss is below ${float(hard_stop_loss_usd):.2f}.",
            "Treat all monitor artifacts as manual inspection signals, not order instructions.",
        ],
        "kill_switches": [
            "Stop immediately when the manual ledger reaches 20 total test trades.",
            f"Stop immediately when the manual ledger reaches {int(hard_stop_full_losses)} settled full losses.",
            f"Stop immediately when net realized loss reaches ${float(hard_stop_loss_usd):.2f}.",
            "Stop if a regenerated decision review shows quote_age_seconds above 5 seconds.",
            "Stop if the strategy-specific spread, depth, ask, time-remaining, or symbol gate fails.",
            "Stop if any order is partially filled, rejected, delayed, or reconciles at a worse price than the candidate ask.",
            "Stop if settlement reconciliation disagrees with the expected Polymarket outcome path.",
        ],
        "post_trade_review": [
            "Record event slug, side, token id, order timestamp, observed ask, fill price, filled shares, fee, and total cost in the manual ledger.",
            "Record settlement outcome and net PnL per trade before the next manual trade.",
            "Compare live fill price against the candidate review best_ask.",
            "Do not open the next position until the previous position has resolved and been reviewed.",
        ],
        "evidence_summaries": {
            strategy_id: _evidence_summary(row)
            for strategy_id, row in zip(allowed_strategy_ids, strategy_rows, strict=True)
            if row
        },
        "evidence_summary": _evidence_summary(strategy_rows[0]) if strategy_rows and strategy_rows[0] else {},
        "blockers": blockers,
        "warnings": warnings,
        "next_required_action": _next_required_action(protocol_status),
    }
    return strict_jsonable(payload)


def _profile_entry_gate(strategy_id: str, *, min_order_size: float) -> dict[str, Any]:
    base = {
        "strategy_id": strategy_id,
        "requires_current_decision_review_candidate": True,
        "candidate_status_required": "candidate_for_manual_review",
        "outcome_quote_meaning": "Polymarket executable ask, not BTC or ETH underlying price",
        "top_ask_size_min": float(min_order_size),
        "quote_age_seconds_max": 5.0,
        "underlying_proxy_age_seconds_max": 5.0,
        "manual_quote_reconciliation_required": True,
    }
    if strategy_id == "profile_late_btc_deep_60_120_validation":
        return {
            **base,
            "symbol_required": "BTC",
            "time_remaining_seconds_min": 60,
            "time_remaining_seconds_max": 120,
            "best_ask_min": 0.20,
            "best_ask_max": 0.98,
            "spread_min": 0.01,
            "spread_max": 0.02,
            "depth_top3_ask_size_min": 100.0,
        }
    if strategy_id == "profile_mid_fade_lower_mid_validation":
        return {
            **base,
            "reference_ladder_band_required": "lower_mid_ladder",
            "time_remaining_seconds_min": 20,
            "time_remaining_seconds_max": 300,
            "best_ask_min": 0.40,
            "best_ask_max": 0.55,
            "spread_max": 0.02,
            "depth_top3_ask_size_min": 20.0,
        }
    if strategy_id == "profile_mid_fade_60s_validation":
        return {
            **base,
            "time_remaining_seconds_min": 20,
            "time_remaining_seconds_max": 300,
            "best_ask_min": 0.40,
            "best_ask_max": 0.70,
            "spread_max": 0.02,
            "depth_top3_ask_size_min": 20.0,
        }
    if strategy_id == "profile_convex_tail_depth_20_100_validation":
        return {
            **base,
            "reference_ladder_band_required": "cheap_convex_ladder",
            "time_remaining_seconds_min": 20,
            "time_remaining_seconds_max": 900,
            "best_ask_min": 0.20,
            "best_ask_max": 0.40,
            "spread_max": 0.02,
            "depth_top3_ask_size_min": 20.0,
            "depth_top3_ask_size_max": 100.0,
        }
    return base


def write_micro_test_protocol_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_dir) if output_dir else resolve_shared_root() / "artifacts" / "crypto-options-research" / day
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_micro_test_protocol_{stamp}.json"
    md_path = root / f"crypto_options_micro_test_protocol_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_micro_test_protocol_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_micro_test_protocol_markdown(payload: dict[str, Any]) -> str:
    budget = payload.get("budget") or {}
    entry = payload.get("entry_gate") or {}
    evidence = payload.get("evidence_summary") or {}
    lines = [
        "# Crypto Options $20 Micro-Test Protocol",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Protocol status: `{payload.get('protocol_status')}`",
        f"- Live trading authorized: `{payload.get('live_trading_authorized')}`",
        f"- Orders allowed: `{payload.get('orders_allowed')}`",
        f"- Allowed strategy: `{payload.get('allowed_strategy')}`",
        f"- Allowed strategies: `{payload.get('allowed_strategies')}`",
        f"- Max budget: `${budget.get('max_total_budget_usd')}`",
        f"- Max position: `{budget.get('max_position_shares')}` shares",
        f"- Max ticket cost: `${budget.get('max_position_cost_usd')}`",
        f"- Net loss hard stop: `${budget.get('hard_stop_loss_usd')}`",
        "",
        "## Evidence",
        "",
        f"- Deduped settled trades: `{evidence.get('deduped_trade_count')}`",
        f"- Win rate: `{evidence.get('win_rate')}`",
        f"- Return sum: `{evidence.get('return_sum')}`",
        f"- Simulated final budget: `{evidence.get('final_budget')}`",
        f"- Max loss streak: `{evidence.get('max_sequential_losses')}`",
        "",
        "## Entry Gate",
        "",
    ]
    if payload.get("entry_gates"):
        for gate in payload.get("entry_gates") or []:
            lines.append(f"- Strategy `{gate.get('strategy_id')}`")
            for key, value in gate.items():
                if key != "strategy_id":
                    lines.append(f"  - `{key}`: `{value}`")
    else:
        for key, value in entry.items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Pre-Trade Reconciliation", ""])
    lines.extend([f"- {item}" for item in payload.get("pre_trade_reconciliation") or []])
    lines.extend(["", "## Kill Switches", ""])
    lines.extend([f"- {item}" for item in payload.get("kill_switches") or []])
    if payload.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend([f"- `{blocker}`" for blocker in payload.get("blockers") or []])
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This protocol is a read-only approval artifact. It does not place, cancel, sign, broadcast, redeem, route, or authorize orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def _strategy_metrics(payload: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    for row in (payload.get("rolling_shadow_metrics") or {}).get("strategies") or []:
        if row.get("strategy_id") == strategy_id:
            return row
    return {}


def _evidence_summary(strategy: dict[str, Any]) -> dict[str, Any]:
    metrics = strategy.get("trade_metrics") or {}
    budget = strategy.get("budget_simulation") or {}
    gate = strategy.get("minimum_live_test_gate_report") or {}
    return {
        "deduped_trade_count": strategy.get("deduped_trade_count"),
        "win_rate": metrics.get("win_rate"),
        "return_sum": metrics.get("return_sum"),
        "return_avg": metrics.get("return_avg"),
        "max_sequential_losses": metrics.get("max_sequential_losses"),
        "final_budget": budget.get("final_budget"),
        "min_budget": budget.get("min_budget"),
        "failed_gates": gate.get("failed_gates") or [],
    }


def _next_required_action(status: str) -> str:
    if status == "ready_for_user_approval":
        return "user_must_explicitly_approve_separate_20usd_micro_test_before_any_execution_design"
    if status == "approved_protocol_ready_for_separate_execution_design":
        return "build_separate_min_size_execution_design_with_manual_controls_and_no_global_portfolio_wiring"
    return "resolve_protocol_blockers_before_any_live_test"


__all__ = [
    "PROFILE_MICRO_TEST_STRATEGY_IDS",
    "build_micro_test_protocol",
    "render_micro_test_protocol_markdown",
    "write_micro_test_protocol_artifacts",
]
