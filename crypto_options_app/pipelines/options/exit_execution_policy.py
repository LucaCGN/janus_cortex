from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from crypto_options_app.pipelines.options.cashout_simulator import default_cashout_policy
from crypto_options_app.pipelines.options.reporting import strict_jsonable


EXIT_EXECUTION_POLICY_SCHEMA_VERSION = "crypto_options_exit_execution_policy_v1"
EXIT_EXECUTION_DECISION_SCHEMA_VERSION = "crypto_options_exit_execution_decision_v1"


def build_exit_execution_decision(
    position: dict[str, Any],
    quote: dict[str, Any],
    *,
    candidate_id: str | None,
    budget_state: dict[str, Any] | None,
    operator_reason: str | None,
    cashout_policy: dict[str, Any] | None = None,
    disabled_candidates: dict[str, Any] | None = None,
    execute_live: bool = False,
    execution_approved: bool = False,
    acknowledge_live_risk: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    cashout_policy = cashout_policy or default_cashout_policy()
    disabled_candidates = disabled_candidates or {}
    budget_state = budget_state or {"valid": False, "reason": "budget_state_missing"}
    target = _target_from_position(position, cashout_policy)
    blockers = _exit_blockers(
        position=position,
        quote=quote,
        candidate_id=candidate_id,
        budget_state=budget_state,
        operator_reason=operator_reason,
        target=target,
        disabled_candidates=disabled_candidates,
        execute_live=execute_live,
        execution_approved=execution_approved,
        acknowledge_live_risk=acknowledge_live_risk,
    )
    shares = _to_float(position.get("held_shares")) or 0.0
    observed_bid = _to_float(quote.get("best_bid"))
    action = _exit_action(
        position=position,
        shares=shares,
        observed_bid=observed_bid,
        target=target,
        generated_at=generated_at,
    )
    dispatch_allowed = bool(not blockers and action["dispatch_ready"])
    ledger_entry = {
        "schema_version": "crypto_options_exit_ledger_entry_v1",
        "candidate_id": candidate_id,
        "position_id": position.get("position_id"),
        "event_slug": position.get("event_slug"),
        "token_id": position.get("token_id"),
        "target_price": target.get("target_price"),
        "target_source_bucket": target.get("bucket"),
        "observed_bid": observed_bid,
        "observed_ask": _to_float(quote.get("best_ask")),
        "submitted_order_id": None,
        "filled_size": 0.0,
        "realized_pnl": None,
        "blocker": blockers[0] if blockers else None,
        "reconciliation_state": position.get("reconciliation_state"),
    }
    return strict_jsonable(
        {
            "schema_version": EXIT_EXECUTION_DECISION_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "policy": {
                "schema_version": EXIT_EXECUTION_POLICY_SCHEMA_VERSION,
                "paired_limit_min_shares": 5.0,
                "worker_managed_below_shares": 5.0,
                "cashout_policy_id": cashout_policy.get("policy_id"),
            },
            "candidate_id": candidate_id,
            "position_id": position.get("position_id"),
            "target": target,
            "action": action,
            "dispatch_allowed": dispatch_allowed,
            "orders_allowed": dispatch_allowed,
            "dispatch_blockers": blockers,
            "max_sell_shares": shares,
            "ledger_entry": ledger_entry,
            "safety_boundary": {
                "manual_chat_exit_allowed": False,
                "orders_submitted_by_policy": False,
                "requires_isolated_executor": True,
            },
        }
    )


def _exit_blockers(
    *,
    position: dict[str, Any],
    quote: dict[str, Any],
    candidate_id: str | None,
    budget_state: dict[str, Any],
    operator_reason: str | None,
    target: dict[str, Any],
    disabled_candidates: dict[str, Any],
    execute_live: bool,
    execution_approved: bool,
    acknowledge_live_risk: bool,
) -> list[str]:
    blockers: list[str] = []
    if not (execute_live and execution_approved and acknowledge_live_risk):
        blockers.append("explicit_live_exit_flags_missing")
    if not candidate_id:
        blockers.append("candidate_id_missing")
    if candidate_id and _is_disabled(candidate_id, disabled_candidates):
        blockers.append("candidate_disabled")
    if not operator_reason:
        blockers.append("operator_reason_missing")
    if not budget_state.get("valid"):
        blockers.append(f"budget_state_invalid:{budget_state.get('reason') or 'unknown'}")
    if not position.get("position_id"):
        blockers.append("position_id_missing")
    if str(position.get("reconciliation_state") or "").lower() != "reconciled":
        blockers.append("position_not_reconciled")
    if (_to_float(position.get("held_shares")) or 0.0) <= 0:
        blockers.append("held_shares_missing_or_zero")
    if _to_float(quote.get("best_bid")) is None or _to_float(quote.get("best_ask")) is None:
        blockers.append("target_quote_missing_bid_or_ask")
    if target.get("target_price") is None:
        blockers.append(target.get("blocker") or "cashout_target_missing")
    return sorted(set(blockers))


def _target_from_position(position: dict[str, Any], cashout_policy: dict[str, Any]) -> dict[str, Any]:
    entry_price = _to_float(position.get("entry_price"))
    if entry_price is None:
        return {"target_price": None, "bucket": None, "blocker": "entry_price_missing"}
    target_cap = _to_float(cashout_policy.get("target_price_cap")) or 0.99
    for bucket in cashout_policy.get("buckets") or []:
        low = _to_float(bucket.get("min_entry_price")) or _to_float(bucket.get("min_price"))
        high = _to_float(bucket.get("max_entry_price")) or _to_float(bucket.get("max_price"))
        multiple = _to_float(bucket.get("target_multiple"))
        if low is None or high is None or multiple is None:
            continue
        if low <= entry_price < high:
            return {
                "target_price": min(round(entry_price * multiple, 4), target_cap),
                "bucket": bucket.get("bucket"),
                "target_multiple": multiple,
                "split_exit": bool(bucket.get("split_exit")),
                "stretch_target_price": bucket.get("stretch_target_price"),
                "blocker": None,
            }
    return {"target_price": None, "bucket": None, "blocker": "no_cashout_policy_for_entry_bucket"}


def _exit_action(*, position: dict[str, Any], shares: float, observed_bid: float | None, target: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    force_reason = str(position.get("force_market_exit_reason") or "")
    if force_reason:
        return {
            "action_type": "immediate_market_sell_forced_exit",
            "order_style": "market_sell_to_close",
            "sell_shares": shares,
            "limit_price": None,
            "target_reached": observed_bid is not None,
            "dispatch_ready": observed_bid is not None,
            "force_market_exit_reason": force_reason,
        }
    target_price = _to_float(target.get("target_price"))
    rescue = _late_exit_rescue_action(position=position, shares=shares, observed_bid=observed_bid, target_price=target_price, generated_at=generated_at)
    if rescue is not None:
        return rescue
    split = _split_exit_action(position=position, shares=shares, observed_bid=observed_bid, target=target)
    if split is not None:
        return split
    if shares >= 5.0:
        return {
            "action_type": "paired_limit_sell_take_profit",
            "order_style": "limit_sell",
            "sell_shares": shares,
            "limit_price": target_price,
            "dispatch_ready": target_price is not None,
        }
    target_reached = bool(target_price is not None and observed_bid is not None and observed_bid >= target_price)
    return {
        "action_type": "worker_managed_market_sell_on_target",
        "order_style": "market_sell_to_close",
        "sell_shares": shares,
        "limit_price": None,
        "target_reached": target_reached,
        "dispatch_ready": target_reached,
    }


def _split_exit_action(*, position: dict[str, Any], shares: float, observed_bid: float | None, target: dict[str, Any]) -> dict[str, Any] | None:
    entry_price = _to_float(position.get("entry_price"))
    if entry_price is None or entry_price >= 0.20 or not target.get("split_exit"):
        return None
    original_shares = _to_float(position.get("original_open_buy_shares")) or shares
    submitted_sell_shares = _to_float(position.get("submitted_sell_shares")) or 0.0
    remaining_sell_shares = max(0.0, float(original_shares) - float(submitted_sell_shares))
    if remaining_sell_shares <= 0.01:
        return {
            "action_type": "split_exit_already_fully_covered",
            "order_style": "none",
            "sell_shares": 0.0,
            "limit_price": None,
            "target_reached": False,
            "dispatch_ready": False,
            "split_exit": {
                "leg": "already_covered",
                "original_open_buy_shares": original_shares,
                "submitted_sell_shares": submitted_sell_shares,
                "remaining_sell_shares": round(remaining_sell_shares, 8),
            },
        }
    if original_shares < 10.0 or shares < 5.0:
        return None
    if submitted_sell_shares <= 0.01:
        recovery_shares = max(5.0, round(original_shares / 2.0, 8))
        recovery_shares = min(shares, remaining_sell_shares, recovery_shares)
        recovery_target = min(0.99, max(entry_price * 2.0, _to_float(target.get("target_price")) or entry_price * 2.0))
        return {
            "action_type": "split_limit_sell_recovery_leg",
            "order_style": "limit_sell",
            "sell_shares": round(recovery_shares, 8),
            "limit_price": round(recovery_target, 4),
            "target_reached": bool(observed_bid is not None and observed_bid >= recovery_target),
            "dispatch_ready": True,
            "split_exit": {
                "leg": "recovery",
                "original_open_buy_shares": original_shares,
                "submitted_sell_shares": submitted_sell_shares,
                "remaining_sell_shares": round(remaining_sell_shares, 8),
            },
        }
    stretch_target = _to_float(target.get("stretch_target_price")) or _to_float(target.get("target_price"))
    stretch_shares = min(shares, remaining_sell_shares)
    return {
        "action_type": "split_limit_sell_stretch_leg",
        "order_style": "limit_sell",
        "sell_shares": round(stretch_shares, 8),
        "limit_price": round(min(0.99, stretch_target), 4) if stretch_target is not None else None,
        "target_reached": bool(stretch_target is not None and observed_bid is not None and observed_bid >= stretch_target),
        "dispatch_ready": stretch_target is not None,
        "split_exit": {
            "leg": "stretch",
            "original_open_buy_shares": original_shares,
            "submitted_sell_shares": submitted_sell_shares,
            "remaining_sell_shares": round(remaining_sell_shares, 8),
        },
    }


def _late_exit_rescue_action(
    *,
    position: dict[str, Any],
    shares: float,
    observed_bid: float | None,
    target_price: float | None,
    generated_at: datetime,
) -> dict[str, Any] | None:
    entry_price = _to_float(position.get("entry_price"))
    if entry_price is None or entry_price < 0.05:
        return None
    opened_at = _parse_dt(position.get("opened_at_utc"))
    event_end = _parse_dt(position.get("event_end_utc"))
    if opened_at is None or event_end is None:
        return None
    seconds_to_end = (event_end - generated_at).total_seconds()
    if not (0 <= seconds_to_end < 60):
        return None
    if (event_end - opened_at).total_seconds() < 60:
        return None
    if observed_bid is None or target_price is None or target_price - observed_bid <= 0.50:
        return None
    if observed_bid < 0.05 and shares >= 5.0:
        return {
            "action_type": "late_rescue_limit_sell_5c",
            "order_style": "limit_sell",
            "sell_shares": shares,
            "limit_price": 0.05,
            "dispatch_ready": True,
            "late_exit_rescue": {"seconds_to_end": round(seconds_to_end, 3), "prior_target_price": target_price, "observed_bid": observed_bid},
        }
    return {
        "action_type": "late_rescue_market_sell",
        "order_style": "market_sell_to_close",
        "sell_shares": shares,
        "limit_price": None,
        "dispatch_ready": True,
        "late_exit_rescue": {"seconds_to_end": round(seconds_to_end, 3), "prior_target_price": target_price, "observed_bid": observed_bid},
    }


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_disabled(candidate_id: str, disabled_candidates: dict[str, Any]) -> bool:
    row = disabled_candidates.get(candidate_id)
    if isinstance(row, dict):
        return bool(row.get("disabled", True))
    return bool(row)


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
    "EXIT_EXECUTION_DECISION_SCHEMA_VERSION",
    "EXIT_EXECUTION_POLICY_SCHEMA_VERSION",
    "build_exit_execution_decision",
]
