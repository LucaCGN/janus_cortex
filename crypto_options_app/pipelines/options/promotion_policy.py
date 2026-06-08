from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from crypto_options_app.pipelines.options.reporting import strict_jsonable


PROMOTION_POLICY_SCHEMA_VERSION = "crypto_options_promotion_policy_v1"
PROMOTION_STATE_SCHEMA_VERSION = "crypto_options_promotion_state_v1"


def default_promotion_policy() -> dict[str, Any]:
    return {
        "schema_version": PROMOTION_POLICY_SCHEMA_VERSION,
        "policy_id": "promotion_policy_v1",
        "starting_ticket_usd": 2.0,
        "loss_stop_order_size_multiple": 3.0,
        "promotion_win_streak": 3,
        "profit_leverage_coefficient": 0.75,
        "max_promotion_step_usd": 3.0,
        "max_ticket_usd": 10.0,
        "demotion_loss_streak": 2,
        "demotion_size_multiplier": 0.50,
        "demotion_floor_ticket_usd": 2.0,
        "post_streak_min_win_rate": 0.60,
        "global_supervised_discovery_cap_usd": 100.0,
    }


def evaluate_promotion_state(
    ledger_or_entries: dict[str, Any] | list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate candidate promotion, dynamic sizing, and stop state from settled results."""

    policy = policy or default_promotion_policy()
    generated_at = generated_at or datetime.now(timezone.utc)
    entries = list(ledger_or_entries.get("entries") or []) if isinstance(ledger_or_entries, dict) else list(ledger_or_entries)
    settled = [entry for entry in entries if _is_settled(entry)]
    settled.sort(key=lambda entry: str(entry.get("recorded_at_utc") or entry.get("settlement_reconciled_at_utc") or ""))
    realized_pnl = _sum(entry.get("realized_pnl_net_usd") for entry in settled)
    wins = [entry for entry in settled if _pnl(entry) > 0.0]
    losses = [entry for entry in settled if _pnl(entry) < 0.0]
    win_rate = (len(wins) / len(settled)) if settled else None
    current_loss_streak = _tail_streak(settled, want_win=False)
    current_win_streak = _tail_streak(settled, want_win=True)
    max_win_streak = _max_streak(settled, want_win=True)
    max_loss_streak = _max_streak(settled, want_win=False)
    has_prior_win_streak = _has_prior_streak(settled, want_win=True, length=int(policy["promotion_win_streak"]))
    pre_demotion_ticket = _promoted_ticket_usd(realized_pnl, has_prior_win_streak=has_prior_win_streak, policy=policy)
    demotion_reason = _demotion_reason(
        realized_pnl=realized_pnl,
        current_loss_streak=current_loss_streak,
        has_prior_win_streak=has_prior_win_streak,
        win_rate=win_rate,
        pre_demotion_ticket=pre_demotion_ticket,
        policy=policy,
    )
    current_ticket = _apply_demotion(pre_demotion_ticket, policy=policy) if demotion_reason else pre_demotion_ticket
    dynamic_loss_stop = current_ticket * float(policy["loss_stop_order_size_multiple"])
    disabled_reason = _disabled_reason(
        realized_pnl=realized_pnl,
        dynamic_loss_stop=dynamic_loss_stop,
        current_loss_streak=current_loss_streak,
        has_prior_win_streak=has_prior_win_streak,
        win_rate=win_rate,
        policy=policy,
    )
    return strict_jsonable(
        {
            "schema_version": PROMOTION_STATE_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "policy": policy,
            "settled_count": len(settled),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "realized_pnl_usd": realized_pnl,
            "current_win_streak": current_win_streak,
            "current_loss_streak": current_loss_streak,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "has_prior_promotion_streak": has_prior_win_streak,
            "current_ticket_usd": current_ticket,
            "pre_demotion_ticket_usd": pre_demotion_ticket,
            "dynamic_loss_stop_usd": dynamic_loss_stop,
            "distance_to_dynamic_loss_stop_usd": realized_pnl + dynamic_loss_stop,
            "promotion_unlocked": pre_demotion_ticket > float(policy["starting_ticket_usd"]),
            "current_ticket_promoted": current_ticket > float(policy["starting_ticket_usd"]),
            "demotion_active": demotion_reason is not None,
            "demotion_reason": demotion_reason,
            "disabled": disabled_reason is not None,
            "disabled_reason": disabled_reason,
            "safety_boundary": {
                "orders_allowed": False,
                "live_trading_authorized": False,
                "scope": "read_only_promotion_policy_evaluation",
            },
        }
    )


def evaluate_global_discovery_cap(candidate_states: list[dict[str, Any]], *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or default_promotion_policy()
    realized = _sum(state.get("realized_pnl_usd") for state in candidate_states)
    cap = float(policy["global_supervised_discovery_cap_usd"])
    return strict_jsonable(
        {
            "schema_version": "crypto_options_global_discovery_cap_state_v1",
            "realized_pnl_usd": realized,
            "global_supervised_discovery_cap_usd": cap,
            "global_cap_hit": realized <= -cap,
            "distance_to_global_cap_usd": realized + cap,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def _promoted_ticket_usd(realized_pnl: float, *, has_prior_win_streak: bool, policy: dict[str, Any]) -> float:
    starting = float(policy["starting_ticket_usd"])
    if not has_prior_win_streak or realized_pnl <= 0:
        return starting
    reserved_drawdown = starting * float(policy["loss_stop_order_size_multiple"])
    available_profit = max(0.0, realized_pnl - reserved_drawdown)
    increase = min(float(policy["max_promotion_step_usd"]), available_profit * float(policy["profit_leverage_coefficient"]))
    return min(float(policy["max_ticket_usd"]), starting + increase)


def _demotion_reason(
    *,
    realized_pnl: float,
    current_loss_streak: int,
    has_prior_win_streak: bool,
    win_rate: float | None,
    pre_demotion_ticket: float,
    policy: dict[str, Any],
) -> str | None:
    if pre_demotion_ticket <= float(policy["starting_ticket_usd"]):
        return None
    if not has_prior_win_streak or realized_pnl <= 0.0 or win_rate is None:
        return None
    if current_loss_streak < int(policy["demotion_loss_streak"]):
        return None
    if win_rate < float(policy["post_streak_min_win_rate"]):
        return None
    return "post_promotion_loss_streak_positive_edge_reduce_size"


def _apply_demotion(pre_demotion_ticket: float, *, policy: dict[str, Any]) -> float:
    floor = float(policy["demotion_floor_ticket_usd"])
    reduced = pre_demotion_ticket * float(policy["demotion_size_multiplier"])
    return max(floor, min(pre_demotion_ticket, reduced))


def _disabled_reason(
    *,
    realized_pnl: float,
    dynamic_loss_stop: float,
    current_loss_streak: int,
    has_prior_win_streak: bool,
    win_rate: float | None,
    policy: dict[str, Any],
) -> str | None:
    if realized_pnl <= -dynamic_loss_stop:
        return "dynamic_loss_stop_hit"
    if current_loss_streak >= 3 and not has_prior_win_streak:
        return "three_losses_without_prior_promotion_streak"
    if (
        current_loss_streak >= 3
        and has_prior_win_streak
        and win_rate is not None
        and win_rate < float(policy["post_streak_min_win_rate"])
        and realized_pnl < 0.0
    ):
        return "post_streak_three_losses_negative_pnl_under_win_rate"
    return None


def _is_settled(entry: dict[str, Any]) -> bool:
    if entry.get("realized_pnl_net_usd") is None:
        return False
    status = str(entry.get("settlement_status") or entry.get("status") or "").lower()
    return status in {"settled", "submitted"} or bool(entry.get("settlement_win") is not None)


def _pnl(entry: dict[str, Any]) -> float:
    value = _to_float(entry.get("realized_pnl_net_usd"))
    return value if value is not None else 0.0


def _tail_streak(entries: list[dict[str, Any]], *, want_win: bool) -> int:
    count = 0
    for entry in reversed(entries):
        is_win = _pnl(entry) > 0.0
        is_loss = _pnl(entry) < 0.0
        if (want_win and is_win) or (not want_win and is_loss):
            count += 1
            continue
        break
    return count


def _max_streak(entries: list[dict[str, Any]], *, want_win: bool) -> int:
    best = 0
    current = 0
    for entry in entries:
        is_win = _pnl(entry) > 0.0
        is_loss = _pnl(entry) < 0.0
        if (want_win and is_win) or (not want_win and is_loss):
            current += 1
            best = max(best, current)
            continue
        current = 0
    return best


def _has_prior_streak(entries: list[dict[str, Any]], *, want_win: bool, length: int) -> bool:
    return _max_streak(entries, want_win=want_win) >= length


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
    "PROMOTION_POLICY_SCHEMA_VERSION",
    "PROMOTION_STATE_SCHEMA_VERSION",
    "default_promotion_policy",
    "evaluate_global_discovery_cap",
    "evaluate_promotion_state",
]
