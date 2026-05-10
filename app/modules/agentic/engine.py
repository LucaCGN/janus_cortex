from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.contracts import OrderIntent, StrategyPlan, StrategyPlanEvaluationResult


MIN_ORDER_SIZE = 5.0
MIN_BUY_NOTIONAL_USD = 1.0
UNDERDOG_WATCH_ONLY_PRICE = 0.19
UNDERDOG_MANUAL_ONLY_PRICE = 0.10


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
        rule_gate = _rules_blocker(strategy.entry_rules, market_state=market_state, portfolio_state=portfolio_state)
        if rule_gate is not None:
            blockers.append({"strategy_id": strategy.strategy_id, **rule_gate})
            continue
        order_payload = _extract_order_payload(strategy.entry_rules)
        missing = [key for key in ("outcome_id", "token_id", "price", "size") if order_payload.get(key) in {None, ""}]
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
        size = _safe_float(order_payload.get("size"))
        if price is None or not 0.0 <= price <= 1.0:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_price", "price": order_payload.get("price")})
            continue
        if size is None or size <= 0.0:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_size", "size": order_payload.get("size")})
            continue
        min_size = _safe_float(order_payload.get("min_size") or order_payload.get("minimum_size")) or MIN_ORDER_SIZE
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
        min_buy_notional = (
            _safe_float(order_payload.get("min_buy_notional_usd") or order_payload.get("minimum_buy_notional_usd"))
            or MIN_BUY_NOTIONAL_USD
        )
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
        if side == "buy" and strategy.budget_usd > 0 and notional > strategy.budget_usd + 1e-9:
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
            market_state=market_state,
        )
        if ultra_low_blocker is not None:
            blockers.append({"strategy_id": strategy.strategy_id, **ultra_low_blocker})
            continue
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
                    "entry_rules": strategy.entry_rules,
                    "exit_rules": strategy.exit_rules,
                    "stop_rules": strategy.stop_rules,
                    "hedge_rules": strategy.hedge_rules,
                    "revision_triggers": strategy.revision_triggers,
                    "explainability": plan.explainability,
                    "required_notional_usd": round(notional, 6),
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
        "manual_only_threshold": UNDERDOG_MANUAL_ONLY_PRICE,
    }
    if guardrail_price < UNDERDOG_MANUAL_ONLY_PRICE:
        return {
            **base,
            "reason": "ultra_low_underdog_manual_only",
            "message": "Underdog buys below 10c require manual-only adoption and cannot compile autonomous order intents.",
        }

    entry_rules = dict(strategy.entry_rules or {})
    missing: list[str] = []
    if not _truthy_any(entry_rules, ("allow_ultra_low_underdog", "allow_underdog_below_19c")):
        missing.append("allow_ultra_low_underdog")

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
        for key in ("target_price", "target_cents", "target_gain", "min_exit_price"):
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
) -> dict[str, Any] | None:
    max_orderbook_age = _safe_float(entry_rules.get("max_orderbook_age_seconds"))
    orderbook_age = _safe_float(market_state.get("orderbook_age_seconds"))
    if max_orderbook_age is not None and orderbook_age is not None and orderbook_age > max_orderbook_age:
        return {"reason": "orderbook_stale", "orderbook_age_seconds": orderbook_age, "max_orderbook_age_seconds": max_orderbook_age}

    max_score_gap = _safe_float(entry_rules.get("max_abs_score_gap"))
    score_gap = _safe_float(market_state.get("score_gap"))
    if max_score_gap is not None and score_gap is not None and abs(score_gap) > max_score_gap:
        return {"reason": "score_gap_outside_rule", "score_gap": score_gap, "max_abs_score_gap": max_score_gap}

    price_band = entry_rules.get("price_band") or entry_rules.get("price_range")
    current_price = _safe_float(market_state.get("price"))
    if isinstance(price_band, (list, tuple)) and len(price_band) == 2 and current_price is not None:
        low = _safe_float(price_band[0])
        high = _safe_float(price_band[1])
        if low is not None and high is not None and not low <= current_price <= high:
            return {"reason": "price_band_not_met", "price": current_price, "price_band": [low, high]}

    max_open_positions = _safe_float(entry_rules.get("max_open_positions"))
    open_positions = _safe_float(portfolio_state.get("open_positions"))
    if max_open_positions is not None and open_positions is not None and open_positions >= max_open_positions:
        return {"reason": "position_limit_reached", "open_positions": open_positions, "max_open_positions": max_open_positions}

    return None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["MIN_BUY_NOTIONAL_USD", "MIN_ORDER_SIZE", "evaluate_strategy_plan"]
