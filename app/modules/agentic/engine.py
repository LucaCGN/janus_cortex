from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.contracts import OrderIntent, StrategyPlan, StrategyPlanEvaluationResult


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
        price = _safe_float(order_payload.get("price"))
        size = _safe_float(order_payload.get("size"))
        if price is None or not 0.0 <= price <= 1.0:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_price", "price": order_payload.get("price")})
            continue
        if size is None or size <= 0.0:
            blockers.append({"strategy_id": strategy.strategy_id, "reason": "invalid_size", "size": order_payload.get("size")})
            continue
        notional = price * size
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


__all__ = ["evaluate_strategy_plan"]
