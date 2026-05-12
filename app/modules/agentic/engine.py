from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.contracts import OrderIntent, StrategyPlan, StrategyPlanEvaluationResult


MIN_ORDER_SIZE = 5.0
MIN_BUY_NOTIONAL_USD = 1.0
MIN_BUY_NOTIONAL_BUFFER_USD = 0.01
UNDERDOG_WATCH_ONLY_PRICE = 0.19
UNDERDOG_SUB_10C_PRICE = 0.10


def evaluate_strategy_plan(
    plan: StrategyPlan,
    *,
    market_state: dict[str, Any] | None = None,
    portfolio_state: dict[str, Any] | None = None,
    dry_run: bool = True,
    max_intents: int = 10,
) -> StrategyPlanEvaluationResult:
    market_state = dict(market_state or {})
    portfolio_state = dict(portfolio_state or {})
    blockers: list[dict[str, Any]] = []
    intents: list[OrderIntent] = []
    now = datetime.now(timezone.utc)

    if plan.valid_until_utc is not None and plan.valid_until_utc <= now:
        blockers.append({"scope": "plan", "reason": "plan_expired", "valid_until_utc": plan.valid_until_utc.isoformat()})
        return StrategyPlanEvaluationResult(
            event_id=plan.event_id,
            market_id=plan.market_id,
            blocked_count=len(blockers),
            blockers=blockers,
        )

    for strategy in plan.active_strategies:
        if len(intents) >= max_intents:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "max_intents_reached"})
            continue
        shadow_flags = dict(strategy.shadow_flags or {})
        if bool(shadow_flags.get("shadow_only")):
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "shadow_only"})
            continue
        rule_gate = _rules_blocker(
            strategy.entry_rules,
            market_state=market_state,
            portfolio_state=portfolio_state,
            strategy_id=strategy.strategy_id,
            strategy_max_positions=strategy.max_positions,
        )
        if rule_gate is not None:
            blockers.append({"strategy_id": strategy.strategy_id, **rule_gate})
            continue
        strategy_state = _strategy_market_state(strategy.entry_rules, market_state, strategy_id=strategy.strategy_id)
        order_payload = _extract_order_payload(strategy.entry_rules)
        order_payload = _resolve_dynamic_order_payload(order_payload, strategy_state=strategy_state)
        if isinstance(order_payload.get("dynamic_price_blocker"), dict):
            blockers.append({"strategy_id": strategy.strategy_id, **order_payload["dynamic_price_blocker"]})
            continue
        sizing_policy = _operator_sizing_policy(portfolio_state)
        required_order_fields = ["outcome_id", "token_id", "price"]
        if sizing_policy is None:
            required_order_fields.append("size")
        missing = [key for key in required_order_fields if order_payload.get(key) in {None, ""}]
        if missing:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "missing_order_fields", "missing": missing})
            continue
        side = str(order_payload.get("side") or "buy").lower()
        if side not in {"buy", "sell"}:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_side", "side": side})
            continue
        order_type = str(order_payload.get("order_type") or "limit").lower()
        if order_type not in {"limit", "market"}:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_order_type", "order_type": order_type})
            continue
        if order_type == "market":
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "market_orders_disabled"})
            continue
        price = _safe_float(order_payload.get("price"))
        if price is None or not 0.0 <= price <= 1.0:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_price", "price": order_payload.get("price")})
            continue
        size, sizing_metadata = _resolve_order_size(
            order_payload,
            strategy_budget_usd=strategy.budget_usd,
            portfolio_state=portfolio_state,
            side=side,
            price=price,
        )
        if size is None or size <= 0.0:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_size", "size": order_payload.get("size")})
            continue
        min_size = _safe_float(sizing_metadata.get("min_size")) or MIN_ORDER_SIZE
        if size < min_size:
            blockers.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "reason": "minimum_size_not_met",
                    "size": size,
                    "minimum_size": min_size,
                }
            )
            continue
        notional = price * size
        min_buy_notional = _safe_float(sizing_metadata.get("min_buy_notional_usd")) or MIN_BUY_NOTIONAL_USD
        if side == "buy" and notional < min_buy_notional:
            blockers.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "reason": "minimum_buy_notional_not_met",
                    "minimum_buy_notional_usd": min_buy_notional,
                    "required_notional_usd": round(notional, 6),
                }
            )
            continue
        max_buy_notional = _safe_float(sizing_metadata.get("max_buy_notional_usd"))
        if side == "buy" and max_buy_notional is not None and notional > max_buy_notional + 1e-9:
            blockers.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "reason": "operator_sizing_notional_exceeded",
                    "max_buy_notional_usd": max_buy_notional,
                    "required_notional_usd": round(notional, 6),
                }
            )
            continue
        if side == "buy" and sizing_policy is None and strategy.budget_usd > 0 and notional > strategy.budget_usd + 1e-9:
            blockers.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "reason": "budget_exceeded",
                    "budget_usd": strategy.budget_usd,
                    "required_notional_usd": round(notional, 6),
                }
            )
            continue
        ultra_low_blocker = _ultra_low_underdog_blocker(
            strategy,
            order_side=side,
            order_price=price,
            market_state=_strategy_market_state(strategy.entry_rules, market_state, strategy_id=strategy.strategy_id),
        )
        if ultra_low_blocker is not None:
            blockers.append({"strategy_id": strategy.strategy_id, **ultra_low_blocker})
            continue
        resolved_exit_rules = _resolve_exit_rules(strategy.exit_rules, entry_price=price)
        intents.append(
            OrderIntent(
                intent_id=f"{plan.event_id}|{strategy.strategy_id}|{len(intents) + 1}",
                event_id=plan.event_id,
                market_id=str(order_payload.get("market_id") or plan.market_id),
                outcome_id=str(order_payload["outcome_id"]),
                token_id=str(order_payload["token_id"]),
                strategy_id=strategy.strategy_id,
                strategy_family=strategy.family,
                side=side,  # type: ignore[arg-type]
                order_type=order_type,  # type: ignore[arg-type]
                price=price,
                size=size,
                time_in_force=str(order_payload.get("time_in_force") or "gtc"),
                dry_run=dry_run,
                reason=str(order_payload.get("reason") or "strategy_plan_entry"),
                metadata={
                    "plan_owner": plan.plan_owner,
                    "schema_version": plan.schema_version,
                    "context_summary": plan.context_summary,
                    "entry_rules": order_payload,
                    "original_entry_rules": strategy.entry_rules,
                    "exit_rules": resolved_exit_rules,
                    "stop_rules": strategy.stop_rules,
                    "hedge_rules": strategy.hedge_rules,
                    "revision_triggers": strategy.revision_triggers,
                    "explainability": plan.explainability,
                    "required_notional_usd": round(notional, 6),
                    "sizing_policy": sizing_metadata,
                },
            )
        )

    return StrategyPlanEvaluationResult(
        event_id=plan.event_id,
        market_id=plan.market_id,
        intent_count=len(intents),
        blocked_count=len(blockers),
        intents=intents,
        blockers=blockers,
    )


def _extract_order_payload(entry_rules: dict[str, Any]) -> dict[str, Any]:
    nested = entry_rules.get("order_intent")
    if isinstance(nested, dict):
        return {**entry_rules, **nested}
    nested = entry_rules.get("order")
    if isinstance(nested, dict):
        return {**entry_rules, **nested}
    return dict(entry_rules)


def _resolve_dynamic_order_payload(order_payload: dict[str, Any], *, strategy_state: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(order_payload)
    policy = str(
        resolved.get("price_policy")
        or resolved.get("limit_price_policy")
        or resolved.get("price_mode")
        or ""
    ).strip().lower()
    if not policy:
        return resolved
    side = str(resolved.get("side") or "buy").strip().lower()
    dynamic_price = _dynamic_order_price(policy, side=side, strategy_state=strategy_state)
    if dynamic_price is None:
        return resolved
    max_price = _safe_float(resolved.get("max_price") or resolved.get("max_entry_price"))
    min_price = _safe_float(resolved.get("min_price") or resolved.get("min_entry_price"))
    if max_price is not None and dynamic_price > max_price:
        resolved["dynamic_price_blocker"] = {
            "reason": "dynamic_price_above_max",
            "price": dynamic_price,
            "max_price": max_price,
            "price_policy": policy,
        }
        return resolved
    if min_price is not None and dynamic_price < min_price:
        resolved["dynamic_price_blocker"] = {
            "reason": "dynamic_price_below_min",
            "price": dynamic_price,
            "min_price": min_price,
            "price_policy": policy,
        }
        return resolved
    resolved["price"] = round(dynamic_price, 4)
    resolved["resolved_price_policy"] = policy
    resolved["static_price"] = order_payload.get("price")
    return resolved


def _dynamic_order_price(policy: str, *, side: str, strategy_state: dict[str, Any]) -> float | None:
    best_bid = _safe_float(strategy_state.get("best_bid"))
    best_ask = _safe_float(strategy_state.get("best_ask"))
    current_price = _first_float(strategy_state, ("price", "team_price", "current_price"))
    if policy in {"current_ask", "best_ask", "cross_current_ask", "take_ask"}:
        return best_ask
    if policy in {"current_bid", "best_bid", "maker_bid"}:
        return best_bid
    if policy in {"current_mid", "mid", "mid_price"}:
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        return current_price
    if policy in {"current_price", "market_state_price", "in_band_current_price"}:
        return current_price if current_price is not None else (best_ask if side == "buy" else best_bid)
    return None


def _resolve_order_size(
    order_payload: dict[str, Any],
    *,
    strategy_budget_usd: float,
    portfolio_state: dict[str, Any],
    side: str,
    price: float,
) -> tuple[float | None, dict[str, Any]]:
    policy = _operator_sizing_policy(portfolio_state)
    if policy is None:
        min_size = _safe_float(order_payload.get("min_size") or order_payload.get("minimum_size")) or MIN_ORDER_SIZE
        min_buy_notional = (
            _safe_float(order_payload.get("min_buy_notional_usd") or order_payload.get("minimum_buy_notional_usd"))
            or MIN_BUY_NOTIONAL_USD
        )
        return _safe_float(order_payload.get("size")), {
            "source": "strategy_plan",
            "mode": "plan_size",
            "min_size": min_size,
            "min_buy_notional_usd": min_buy_notional,
            "strategy_budget_usd": strategy_budget_usd,
        }

    min_size = _safe_float(policy.get("min_size") or policy.get("minimum_size")) or MIN_ORDER_SIZE
    min_buy_notional = _safe_float(policy.get("min_buy_notional_usd") or policy.get("minimum_buy_notional_usd")) or MIN_BUY_NOTIONAL_USD
    min_buy_notional_buffer = _safe_float(
        policy.get("min_buy_notional_buffer_usd")
        or policy.get("notional_safety_buffer_usd")
        or policy.get("notional_buffer_usd")
    )
    if min_buy_notional_buffer is None:
        min_buy_notional_buffer = MIN_BUY_NOTIONAL_BUFFER_USD
    min_buy_notional_buffer = max(0.0, min_buy_notional_buffer)
    effective_min_buy_notional = min_buy_notional + min_buy_notional_buffer
    metadata = {
        "source": "operator_policy",
        "mode": policy.get("mode") or "operator_minimum_order",
        "min_size": min_size,
        "min_buy_notional_usd": min_buy_notional,
        "min_buy_notional_buffer_usd": min_buy_notional_buffer,
        "effective_min_buy_notional_usd": effective_min_buy_notional,
        "max_buy_notional_usd": _safe_float(policy.get("max_buy_notional_usd") or policy.get("max_notional_usd")),
        "llm_requested_size": _safe_float(order_payload.get("size")),
        "llm_strategy_budget_usd": strategy_budget_usd,
    }
    if side != "buy":
        return _safe_float(order_payload.get("size")), metadata
    if price <= 0.0:
        return None, metadata
    minimum_notional_size = effective_min_buy_notional / price
    precision = int(_safe_float(policy.get("share_precision")) or 3)
    factor = 10**max(0, precision)
    size = max(min_size, math.ceil(minimum_notional_size * factor) / factor)
    return size, metadata


def _operator_sizing_policy(portfolio_state: dict[str, Any]) -> dict[str, Any] | None:
    policy = portfolio_state.get("operator_sizing_policy") or portfolio_state.get("sizing_policy")
    if not isinstance(policy, dict):
        return None
    mode = str(policy.get("mode") or "operator_minimum_order").strip().lower()
    if mode in {"strategy_plan", "plan_size", "llm"}:
        return None
    return dict(policy)


def _resolve_exit_rules(exit_rules: dict[str, Any], *, entry_price: float) -> dict[str, Any]:
    resolved = dict(exit_rules or {})
    target_price = _safe_float(resolved.get("target_price"))
    dynamic_target = _scaled_micro_grid_target_price(entry_price, resolved)
    if target_price is None and dynamic_target is not None:
        resolved["target_price"] = dynamic_target
    if dynamic_target is not None:
        resolved["resolved_target_price"] = dynamic_target
        resolved["resolved_target_policy"] = "max_min_cents_or_return_fraction"
    return resolved


def _scaled_micro_grid_target_price(entry_price: float, rules: dict[str, Any]) -> float | None:
    if entry_price <= 0.0:
        return None
    target_policy = str(rules.get("target_policy") or rules.get("target_mode") or "").strip().lower()
    min_target_cents = _safe_float(
        rules.get("min_target_cents")
        or rules.get("minimum_target_cents")
        or rules.get("target_floor_cents")
    )
    target_return_fraction = _safe_float(
        rules.get("target_return_fraction")
        or rules.get("target_gain_fraction")
        or rules.get("target_return")
    )
    target_return_percent = _safe_float(rules.get("target_return_percent") or rules.get("target_gain_percent"))
    if target_return_fraction is None and target_return_percent is not None:
        target_return_fraction = target_return_percent / 100.0
    if (
        target_policy not in {"micro_grid_scaled", "scaled_micro_grid", "price_scaled_micro_grid"}
        and min_target_cents is None
        and target_return_fraction is None
    ):
        return None
    min_move = (min_target_cents or 0.0) / 100.0
    fraction_move = entry_price * (target_return_fraction or 0.0)
    target_move = max(min_move, fraction_move)
    if target_move <= 0.0:
        return None
    return round(min(0.95, max(0.01, entry_price + target_move)), 4)


def _ultra_low_underdog_blocker(
    strategy: Any,
    *,
    order_side: str,
    order_price: float,
    market_state: dict[str, Any],
) -> dict[str, Any] | None:
    if order_side != "buy" or not _is_underdog_strategy(strategy):
        return None
    guardrail_price = _min_present(
        order_price,
        _safe_float(market_state.get("price")),
        _safe_float(market_state.get("team_price")),
    )
    if guardrail_price is None or guardrail_price >= UNDERDOG_WATCH_ONLY_PRICE:
        return None

    base = {
        "guardrail": "ultra_low_underdog",
        "price": round(guardrail_price, 6),
        "watch_only_threshold": UNDERDOG_WATCH_ONLY_PRICE,
        "sub_10c_threshold": UNDERDOG_SUB_10C_PRICE,
    }

    entry_rules = dict(strategy.entry_rules or {})
    missing: list[str] = []
    if not _truthy_any(entry_rules, ("allow_ultra_low_underdog", "allow_underdog_below_19c")):
        missing.append("allow_ultra_low_underdog")
    if guardrail_price < UNDERDOG_SUB_10C_PRICE and not _truthy_any(
        entry_rules,
        (
            "allow_sub_10c_underdog_grid",
            "allow_ultra_low_grid",
            "allow_0_5c_to_5c_grid",
        ),
    ):
        missing.append("allow_sub_10c_underdog_grid")

    max_scoreboard_age = _safe_float(
        entry_rules.get("max_scoreboard_age_seconds") or entry_rules.get("max_live_scoreboard_age_seconds")
    )
    scoreboard_age = _safe_float(
        market_state.get("scoreboard_age_seconds")
        if "scoreboard_age_seconds" in market_state
        else market_state.get("scoreboard_age")
    )
    if max_scoreboard_age is None or scoreboard_age is None or scoreboard_age > max_scoreboard_age:
        missing.append("fresh_scoreboard")

    max_score_gap = _safe_float(
        entry_rules.get("max_abs_score_gap")
        or entry_rules.get("max_close_score_gap")
        or entry_rules.get("max_trailing_score_gap")
    )
    score_gap = _safe_float(market_state.get("score_gap") if "score_gap" in market_state else market_state.get("score_diff"))
    if max_score_gap is None or score_gap is None or abs(score_gap) > max_score_gap:
        missing.append("score_gap_constraint")

    if not _has_target_policy(strategy.exit_rules, entry_rules):
        missing.append("target_policy")
    if not _has_stop_policy(strategy.stop_rules, entry_rules):
        missing.append("stop_policy")
    if missing:
        return {
            **base,
            "reason": "ultra_low_underdog_guardrail",
            "missing_requirements": missing,
            "score_gap": score_gap,
            "max_abs_score_gap": max_score_gap,
            "scoreboard_age_seconds": scoreboard_age,
            "max_scoreboard_age_seconds": max_scoreboard_age,
        }
    return None


def _is_underdog_strategy(strategy: Any) -> bool:
    family = str(getattr(strategy, "family", "") or "").lower()
    side = str(getattr(strategy, "side", "") or "").lower()
    return "underdog" in family or side in {"underdog", "dog", "away_underdog", "home_underdog"}


def _truthy_any(values: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str):
            if value.strip().lower() in {"1", "true", "yes", "y"}:
                return True
            continue
        if bool(value):
            return True
    return False


def _has_target_policy(exit_rules: dict[str, Any], entry_rules: dict[str, Any]) -> bool:
    for values in (exit_rules, entry_rules):
        for key in (
            "target_price",
            "target_cents",
            "target_gain",
            "min_exit_price",
            "min_target_cents",
            "minimum_target_cents",
            "target_floor_cents",
            "target_return_fraction",
            "target_gain_fraction",
            "target_return_percent",
            "target_gain_percent",
        ):
            if _safe_float(values.get(key)) is not None:
                return True
        for key in ("targets_cents", "target_prices"):
            value = values.get(key)
            if isinstance(value, (list, tuple)) and value:
                return True
    return False


def _has_stop_policy(stop_rules: dict[str, Any], entry_rules: dict[str, Any]) -> bool:
    for values in (stop_rules, entry_rules):
        for key in ("stop_price", "max_loss_cents", "max_adverse_cents", "stop_loss", "exit_threshold"):
            if _safe_float(values.get(key)) is not None:
                return True
    return False


def _min_present(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _rules_blocker(
    entry_rules: dict[str, Any],
    *,
    market_state: dict[str, Any],
    portfolio_state: dict[str, Any],
    strategy_id: str | None = None,
    strategy_max_positions: int | None = None,
) -> dict[str, Any] | None:
    strategy_state = _strategy_market_state(entry_rules, market_state, strategy_id=strategy_id)

    max_orderbook_age = _safe_float(entry_rules.get("max_orderbook_age_seconds"))
    orderbook_age = _safe_float(strategy_state.get("orderbook_age_seconds"))
    if max_orderbook_age is not None and orderbook_age is None:
        return {"reason": "orderbook_freshness_required", "max_orderbook_age_seconds": max_orderbook_age}
    if max_orderbook_age is not None and orderbook_age is not None and orderbook_age > max_orderbook_age:
        return {"reason": "orderbook_stale", "orderbook_age_seconds": orderbook_age, "max_orderbook_age_seconds": max_orderbook_age}

    max_scoreboard_age = _safe_float(entry_rules.get("max_scoreboard_age_seconds") or entry_rules.get("max_live_scoreboard_age_seconds"))
    scoreboard_age = _first_float(strategy_state, ("scoreboard_age_seconds", "scoreboard_age"))
    if max_scoreboard_age is not None and scoreboard_age is None:
        return {"reason": "scoreboard_freshness_required", "max_scoreboard_age_seconds": max_scoreboard_age}
    if max_scoreboard_age is not None and scoreboard_age is not None and scoreboard_age > max_scoreboard_age:
        return {
            "reason": "scoreboard_stale",
            "scoreboard_age_seconds": scoreboard_age,
            "max_scoreboard_age_seconds": max_scoreboard_age,
        }

    max_spread_cents = _safe_float(entry_rules.get("max_spread_cents"))
    spread_cents = _spread_cents(strategy_state)
    if max_spread_cents is not None and spread_cents is None:
        return {"reason": "orderbook_spread_required", "max_spread_cents": max_spread_cents}
    if max_spread_cents is not None and spread_cents is not None and spread_cents > max_spread_cents:
        return {"reason": "orderbook_spread_too_wide", "spread_cents": spread_cents, "max_spread_cents": max_spread_cents}

    max_score_gap = _safe_float(entry_rules.get("max_abs_score_gap"))
    score_gap = _first_float(strategy_state, ("score_gap", "score_diff"))
    if max_score_gap is not None and score_gap is None:
        return {"reason": "score_gap_required", "max_abs_score_gap": max_score_gap}
    if max_score_gap is not None and score_gap is not None and abs(score_gap) > max_score_gap:
        return {"reason": "score_gap_outside_rule", "score_gap": score_gap, "max_abs_score_gap": max_score_gap}

    period_blocker = _period_blocker(entry_rules, strategy_state)
    if period_blocker is not None:
        return period_blocker

    clock_blocker = _clock_blocker(entry_rules, strategy_state)
    if clock_blocker is not None:
        return clock_blocker

    player_status_blocker = _player_status_shock_blocker(entry_rules, strategy_state)
    if player_status_blocker is not None:
        return player_status_blocker

    price_band = entry_rules.get("price_band") or entry_rules.get("price_range")
    current_price = _first_float(strategy_state, ("price", "team_price", "current_price"))
    if isinstance(price_band, (list, tuple)) and len(price_band) == 2 and current_price is None:
        return {"reason": "price_state_required", "price_band": list(price_band)}
    if isinstance(price_band, (list, tuple)) and len(price_band) == 2 and current_price is not None:
        low = _safe_float(price_band[0])
        high = _safe_float(price_band[1])
        if low is not None and high is not None and not low <= current_price <= high:
            return {"reason": "price_band_not_met", "price": current_price, "price_band": [low, high]}

    if bool(portfolio_state.get("pending_intents_unavailable")):
        return {
            "reason": "pending_intent_state_unavailable",
            "source": portfolio_state.get("pending_intent_source"),
            "error": portfolio_state.get("pending_intents_error"),
        }

    explicit_position_cap = _safe_float(entry_rules.get("max_open_positions"))
    pending_intents = _pending_intent_exposure(
        portfolio_state,
        entry_rules=entry_rules,
        strategy_id=strategy_id,
    )
    has_exposure_state = (
        "open_positions" in portfolio_state
        or "open_orders" in portfolio_state
        or _has_pending_intent_state(portfolio_state)
    )
    max_open_positions = None
    if explicit_position_cap is not None or has_exposure_state:
        max_open_positions = _min_present(explicit_position_cap, _safe_float(strategy_max_positions))
    open_positions = _safe_float(portfolio_state.get("open_positions"))
    open_orders = _safe_float(portfolio_state.get("open_orders")) or 0.0
    direct_unresolved_exposure = (open_positions or 0.0) + open_orders
    unresolved_exposure = direct_unresolved_exposure + pending_intents
    if explicit_position_cap is not None and open_positions is None:
        return {"reason": "position_state_required", "max_open_positions": max_open_positions}
    if max_open_positions is not None and unresolved_exposure >= max_open_positions:
        reason = "position_limit_reached"
        if pending_intents > 0.0 and direct_unresolved_exposure < max_open_positions:
            reason = "pending_intent_limit_reached"
        return {
            "reason": reason,
            "open_positions": open_positions,
            "open_orders": open_orders,
            "pending_intents": pending_intents,
            "direct_unresolved_exposure": direct_unresolved_exposure,
            "unresolved_exposure": unresolved_exposure,
            "max_open_positions": max_open_positions,
        }

    return None


def _period_blocker(entry_rules: dict[str, Any], strategy_state: dict[str, Any]) -> dict[str, Any] | None:
    min_period = _safe_float(entry_rules.get("min_period"))
    max_period = _safe_float(entry_rules.get("max_period"))
    allowed_periods_raw = entry_rules.get("allowed_periods") or entry_rules.get("periods")
    allowed_periods: set[int] = set()
    if isinstance(allowed_periods_raw, (list, tuple, set)):
        for value in allowed_periods_raw:
            parsed = _safe_float(value)
            if parsed is not None:
                allowed_periods.add(int(parsed))
    if min_period is None and max_period is None and not allowed_periods:
        return None

    period = _first_float(strategy_state, ("period", "game_period", "quarter"))
    if period is None:
        return {
            "reason": "period_required",
            "min_period": min_period,
            "max_period": max_period,
            "allowed_periods": sorted(allowed_periods) if allowed_periods else None,
        }
    period_int = int(period)
    if allowed_periods and period_int not in allowed_periods:
        return {"reason": "period_outside_rule", "period": period_int, "allowed_periods": sorted(allowed_periods)}
    if min_period is not None and period < min_period:
        return {"reason": "period_outside_rule", "period": period_int, "min_period": int(min_period)}
    if max_period is not None and period > max_period:
        return {"reason": "period_outside_rule", "period": period_int, "max_period": int(max_period)}
    return None


def _clock_blocker(entry_rules: dict[str, Any], strategy_state: dict[str, Any]) -> dict[str, Any] | None:
    min_remaining = _safe_float(
        entry_rules.get("min_clock_remaining_seconds")
        or entry_rules.get("min_game_clock_remaining_seconds")
        or entry_rules.get("no_new_entry_after_clock_seconds")
    )
    max_remaining = _safe_float(entry_rules.get("max_clock_remaining_seconds") or entry_rules.get("max_game_clock_remaining_seconds"))
    if min_remaining is None and max_remaining is None:
        return None

    clock = (
        strategy_state.get("game_clock")
        or strategy_state.get("clock")
        or strategy_state.get("clock_remaining")
        or strategy_state.get("time_remaining")
    )
    seconds_remaining = _clock_remaining_seconds(clock)
    if seconds_remaining is None:
        return {
            "reason": "clock_required",
            "min_clock_remaining_seconds": min_remaining,
            "max_clock_remaining_seconds": max_remaining,
        }
    if min_remaining is not None and seconds_remaining < min_remaining:
        return {
            "reason": "clock_inside_no_entry_window",
            "clock": clock,
            "clock_remaining_seconds": seconds_remaining,
            "min_clock_remaining_seconds": min_remaining,
        }
    if max_remaining is not None and seconds_remaining > max_remaining:
        return {
            "reason": "clock_outside_rule",
            "clock": clock,
            "clock_remaining_seconds": seconds_remaining,
            "max_clock_remaining_seconds": max_remaining,
        }
    return None


def _player_status_shock_blocker(entry_rules: dict[str, Any], strategy_state: dict[str, Any]) -> dict[str, Any] | None:
    order_side = str(entry_rules.get("side") or "buy").strip().lower()
    if order_side != "buy":
        return None
    if _truthy_any(
        entry_rules,
        (
            "allow_player_status_shock",
            "allow_after_player_status_shock",
            "player_status_shock_reviewed",
            "fresh_strategy_plan_after_player_status_shock",
        ),
    ):
        return None

    shocks = _revision_required_player_status_shocks(strategy_state)
    explicit_count = _safe_float(strategy_state.get("player_status_shock_count"))
    if not shocks and not explicit_count:
        return None

    shock_tags = sorted(
        {
            str(tag)
            for shock in shocks
            for tag in (shock.get("tags") or shock.get("shock_tags") or [])
            if str(tag).strip()
        }
    )
    player_names = sorted({str(shock.get("player_name")) for shock in shocks if str(shock.get("player_name") or "").strip()})
    event_indexes = [
        shock.get("event_index")
        for shock in shocks
        if shock.get("event_index") not in (None, "")
    ]
    return {
        "reason": "player_status_shock_revision_required",
        "shock_count": len(shocks) if shocks else int(explicit_count or 0),
        "shock_tags": shock_tags,
        "player_names": player_names,
        "event_indexes": event_indexes,
        "requires_strategy_plan_revision": True,
        "message": "Player-status shock detected from play-by-play; autonomous buys require a fresh StrategyPlanJSON revision.",
    }


def _revision_required_player_status_shocks(strategy_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        strategy_state.get("player_status_shocks")
        or strategy_state.get("player_status_shock_events")
        or strategy_state.get("player_status_shock")
    )
    if isinstance(raw, dict):
        values = [raw]
    elif isinstance(raw, list):
        values = [item for item in raw if isinstance(item, dict)]
    else:
        values = []
    return [item for item in values if item.get("requires_strategy_plan_revision") is not False]


def _has_pending_intent_state(portfolio_state: dict[str, Any]) -> bool:
    if "pending_intent_orders" in portfolio_state:
        return True
    for key in ("pending_intents", "pending_buy_intents", "pending_sell_intents", "pending_orders"):
        if key in portfolio_state:
            return True
    return False


def _pending_intent_exposure(
    portfolio_state: dict[str, Any],
    *,
    entry_rules: dict[str, Any],
    strategy_id: str | None,
) -> float:
    order_side = str(entry_rules.get("side") or "buy").strip().lower()
    pending_orders = portfolio_state.get("pending_intent_orders")
    if isinstance(pending_orders, list):
        return float(
            sum(
                1
                for order in pending_orders
                if isinstance(order, dict)
                and _pending_intent_order_matches(order, entry_rules=entry_rules, strategy_id=strategy_id, order_side=order_side)
            )
        )

    side_specific = _safe_float(portfolio_state.get(f"pending_{order_side}_intents"))
    if side_specific is not None:
        return side_specific

    pending_side = str(portfolio_state.get("pending_intents_side") or "").strip().lower()
    if pending_side and pending_side != order_side:
        return 0.0
    return (
        _safe_float(portfolio_state.get("pending_intents"))
        or _safe_float(portfolio_state.get("pending_orders"))
        or 0.0
    )


def _pending_intent_order_matches(
    order: dict[str, Any],
    *,
    entry_rules: dict[str, Any],
    strategy_id: str | None,
    order_side: str,
) -> bool:
    pending_side = str(order.get("side") or order.get("order_side") or "").strip().lower()
    if pending_side and pending_side != order_side:
        return False

    entry_outcome_id = str(entry_rules.get("outcome_id") or "").strip()
    entry_token_id = str(entry_rules.get("token_id") or "").strip()
    pending_strategy_id = str(order.get("strategy_id") or "").strip()
    pending_outcome_id = str(order.get("outcome_id") or "").strip()
    pending_token_id = str(order.get("token_id") or "").strip()

    if pending_strategy_id and strategy_id and pending_strategy_id == strategy_id:
        return True
    if pending_outcome_id and entry_outcome_id and pending_outcome_id == entry_outcome_id:
        return True
    if pending_token_id and entry_token_id and pending_token_id == entry_token_id:
        return True
    return not (pending_strategy_id or pending_outcome_id or pending_token_id)


def _strategy_market_state(
    entry_rules: dict[str, Any],
    market_state: dict[str, Any],
    *,
    strategy_id: str | None,
) -> dict[str, Any]:
    state = dict(market_state)
    outcome_id = str(entry_rules.get("outcome_id") or "")
    token_id = str(entry_rules.get("token_id") or "")
    _merge_nested_state(state, market_state, ("strategy_states", "strategy_market_states"), strategy_id)
    _merge_nested_state(state, market_state, ("outcome_states", "outcome_market_states", "outcomes"), outcome_id)
    _merge_nested_state(state, market_state, ("token_states", "token_market_states", "tokens"), token_id)
    _merge_scalar_state(state, market_state, ("outcome_prices", "prices_by_outcome"), outcome_id, "price")
    _merge_scalar_state(state, market_state, ("token_prices", "prices_by_token"), token_id, "price")
    return state


def _merge_nested_state(
    state: dict[str, Any],
    market_state: dict[str, Any],
    buckets: tuple[str, ...],
    key: str | None,
) -> None:
    if not key:
        return
    for bucket in buckets:
        values = market_state.get(bucket)
        if not isinstance(values, dict):
            continue
        selected = values.get(key)
        if isinstance(selected, dict):
            state.update(selected)


def _merge_scalar_state(
    state: dict[str, Any],
    market_state: dict[str, Any],
    buckets: tuple[str, ...],
    key: str | None,
    target_key: str,
) -> None:
    if not key:
        return
    for bucket in buckets:
        values = market_state.get(bucket)
        if not isinstance(values, dict):
            continue
        selected = values.get(key)
        if selected is not None and not isinstance(selected, dict):
            state[target_key] = selected


def _spread_cents(market_state: dict[str, Any]) -> float | None:
    explicit = _safe_float(market_state.get("spread_cents"))
    if explicit is not None:
        return explicit
    spread = _safe_float(market_state.get("spread"))
    if spread is not None:
        return spread * 100.0 if spread <= 1.0 else spread
    bid = _safe_float(market_state.get("best_bid"))
    ask = _safe_float(market_state.get("best_ask"))
    if bid is None or ask is None:
        return None
    return max(0.0, ask - bid) * 100.0


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(values: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in values:
            parsed = _safe_float(values.get(key))
            if parsed is not None:
                return parsed
    return None


def _clock_remaining_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))

    text = str(value).strip().upper()
    if not text:
        return None
    parsed = _safe_float(text)
    if parsed is not None:
        return max(0.0, parsed)

    if text.startswith("PT"):
        body = text[2:]
        minutes = 0.0
        seconds = 0.0
        if "M" in body:
            minute_part, body = body.split("M", 1)
            minutes = _safe_float(minute_part) or 0.0
        if "S" in body:
            second_part = body.split("S", 1)[0]
            seconds = _safe_float(second_part) or 0.0
        return max(0.0, minutes * 60.0 + seconds)

    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            minutes = _safe_float(parts[0])
            seconds = _safe_float(parts[1])
            if minutes is not None and seconds is not None:
                return max(0.0, minutes * 60.0 + seconds)

    return None


__all__ = ["MIN_BUY_NOTIONAL_USD", "MIN_ORDER_SIZE", "evaluate_strategy_plan"]
