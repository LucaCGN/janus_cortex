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
WNBA_CONTROLLED_ENTRY_FAMILY = "wnba_controlled_min_size_entry_v1"
WNBA_CONTROLLED_ENTRY_MAX_INTENTS = 1
GRID_SPREAD_BLOCKER_REASONS = {"orderbook_spread_required", "orderbook_spread_too_wide"}


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
    strategies_by_id = {strategy.strategy_id: strategy for strategy in plan.active_strategies}
    controlled_entry_intents = 0
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
        controlled_entry_blocker = _wnba_controlled_entry_blocker(
            strategy,
            blockers=blockers,
            strategies_by_id=strategies_by_id,
            controlled_entry_intents=controlled_entry_intents,
        )
        if controlled_entry_blocker is not None:
            blockers.append({"strategy_id": strategy.strategy_id, **controlled_entry_blocker})
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
        sleeve_metadata = _strategy_sleeve_metadata(strategy)
        intents.append(
            OrderIntent(
                intent_id=f"{plan.event_id}|{strategy.strategy_id}|{len(intents) + 1}",
                event_id=plan.event_id,
                market_id=str(order_payload.get("market_id") or plan.market_id),
                outcome_id=str(order_payload["outcome_id"]),
                token_id=str(order_payload["token_id"]),
                strategy_id=strategy.strategy_id,
                strategy_family=strategy.family,
                sleeve_id=sleeve_metadata["sleeve_id"],
                sleeve_group=sleeve_metadata.get("sleeve_group"),
                sleeve_role=sleeve_metadata.get("sleeve_role"),
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
                    "sleeve": sleeve_metadata,
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
        if _is_wnba_controlled_entry_strategy(strategy):
            controlled_entry_intents += 1

    aggregation_result = _order_intents_from_live_signal_aggregation(
        plan,
        market_state=market_state,
        portfolio_state=portfolio_state,
        dry_run=dry_run,
        existing_intents=intents,
        max_remaining=max(0, max_intents - len(intents)),
    )
    intents.extend(aggregation_result["intents"])
    blockers.extend(aggregation_result["blockers"])

    blockers = _attach_sleeve_metadata(plan, blockers)
    return StrategyPlanEvaluationResult(
        event_id=plan.event_id,
        market_id=plan.market_id,
        intent_count=len(intents),
        blocked_count=len(blockers),
        intents=intents,
        blockers=blockers,
        sleeve_states=_build_sleeve_states(plan, intents=intents, blockers=blockers),
    )


def _order_intents_from_live_signal_aggregation(
    plan: StrategyPlan,
    *,
    market_state: dict[str, Any],
    portfolio_state: dict[str, Any],
    dry_run: bool,
    existing_intents: list[OrderIntent],
    max_remaining: int,
) -> dict[str, list[Any]]:
    decision = _live_signal_aggregation_decision(market_state)
    candidates = decision.get("order_intent_candidates")
    if not isinstance(candidates, list) or not candidates:
        return {"intents": [], "blockers": []}

    intents: list[OrderIntent] = []
    blockers: list[dict[str, Any]] = []
    seen_keys = {_intent_dedupe_key(intent) for intent in existing_intents}
    sizing_policy = _operator_sizing_policy(portfolio_state) or {}
    min_size = _safe_float(sizing_policy.get("min_size") or sizing_policy.get("minimum_size")) or MIN_ORDER_SIZE
    min_buy_notional = _safe_float(sizing_policy.get("min_buy_notional_usd") or sizing_policy.get("minimum_buy_notional_usd")) or MIN_BUY_NOTIONAL_USD
    max_buy_notional = _safe_float(sizing_policy.get("max_buy_notional_usd") or sizing_policy.get("max_notional_usd"))
    strategies_by_id = {strategy.strategy_id: strategy for strategy in plan.active_strategies}

    for index, candidate in enumerate(candidates):
        if len(intents) >= max_remaining:
            blockers.append({"scope": "live_signal_aggregation", "reason": "max_intents_reached", "candidate_index": index})
            continue
        if not isinstance(candidate, dict):
            blockers.append({"scope": "live_signal_aggregation", "reason": "invalid_candidate_shape", "candidate_index": index})
            continue

        side = _candidate_order_side(candidate)
        token_id = _clean_text(candidate.get("market_token_id") or candidate.get("token_id"))
        outcome_id = _clean_text(candidate.get("outcome_id"))
        price = _safe_float(candidate.get("max_price") or candidate.get("price"))
        strategy_id = _clean_text(candidate.get("strategy_id")) or f"live-signal-aggregation-{index + 1}"
        sleeve_id = _clean_text(candidate.get("sleeve_id")) or strategy_id
        strategy = strategies_by_id.get(strategy_id)
        size, size_metadata = _candidate_size(
            candidate,
            price=price,
            side=side,
            strategy=strategy,
            min_buy_notional_usd=min_buy_notional,
        )
        dedupe_key = (token_id, side, sleeve_id, _clean_text(candidate.get("cycle_id")))
        if dedupe_key in seen_keys:
            blockers.append(
                {
                    "scope": "live_signal_aggregation",
                    "reason": "aggregation_candidate_duplicate_intent",
                    "strategy_id": strategy_id,
                    "sleeve_id": sleeve_id,
                    "token_id": token_id,
                    "side": side,
                }
            )
            continue

        missing = [
            field
            for field, value in (
                ("market_token_id", token_id),
                ("outcome_id", outcome_id),
                ("side", side),
                ("price", price),
                ("size", size),
            )
            if value in {None, ""}
        ]
        if missing:
            blockers.append(
                {
                    "scope": "live_signal_aggregation",
                    "reason": "aggregation_candidate_missing_order_fields",
                    "strategy_id": strategy_id,
                    "missing": missing,
                }
            )
            continue
        if side not in {"buy", "sell"}:
            blockers.append({"scope": "live_signal_aggregation", "reason": "invalid_side", "strategy_id": strategy_id, "side": side})
            continue
        if price is None or not 0.0 <= price <= 1.0:
            blockers.append({"scope": "live_signal_aggregation", "reason": "invalid_price", "strategy_id": strategy_id, "price": price})
            continue
        if size is None or size <= 0.0:
            blockers.append({"scope": "live_signal_aggregation", "reason": "invalid_size", "strategy_id": strategy_id, "size": size})
            continue
        if size < min_size:
            blockers.append(
                {
                    "scope": "live_signal_aggregation",
                    "reason": "minimum_size_not_met",
                    "strategy_id": strategy_id,
                    "size": size,
                    "minimum_size": min_size,
                }
            )
            continue

        notional = price * size
        if side == "buy" and notional < min_buy_notional:
            blockers.append(
                {
                    "scope": "live_signal_aggregation",
                    "reason": "minimum_buy_notional_not_met",
                    "strategy_id": strategy_id,
                    "minimum_buy_notional_usd": min_buy_notional,
                    "required_notional_usd": round(notional, 6),
                }
            )
            continue
        if side == "buy" and max_buy_notional is not None and notional > max_buy_notional + 1e-9:
            blockers.append(
                {
                    "scope": "live_signal_aggregation",
                    "reason": "operator_sizing_notional_exceeded",
                    "strategy_id": strategy_id,
                    "max_buy_notional_usd": max_buy_notional,
                    "required_notional_usd": round(notional, 6),
                }
            )
            continue
        if side == "buy" and strategy is not None and strategy.budget_usd > 0 and notional > strategy.budget_usd + 1e-9:
            blockers.append(
                {
                    "scope": "live_signal_aggregation",
                    "reason": "budget_exceeded",
                    "strategy_id": strategy_id,
                    "sleeve_id": sleeve_id,
                    "budget_usd": strategy.budget_usd,
                    "required_notional_usd": round(notional, 6),
                }
            )
            continue
        if strategy is not None:
            ultra_low_blocker = _ultra_low_underdog_blocker(
                strategy,
                order_side=side,
                order_price=price,
                market_state=_strategy_market_state(strategy.entry_rules, market_state, strategy_id=strategy.strategy_id),
            )
            if ultra_low_blocker is not None:
                blockers.append(
                    {
                        "scope": "live_signal_aggregation",
                        "strategy_id": strategy_id,
                        "sleeve_id": sleeve_id,
                        **ultra_low_blocker,
                    }
                )
                continue
        lifecycle_blocker = _aggregation_candidate_lifecycle_blocker(
            candidate,
            side=side,
            strategy=strategy,
        )
        if lifecycle_blocker is not None:
            blockers.append(lifecycle_blocker)
            continue

        intent = OrderIntent(
            intent_id=f"{plan.event_id}|{strategy_id}|aggregation|{len(existing_intents) + len(intents) + 1}",
            event_id=plan.event_id,
            market_id=_clean_text(candidate.get("market_id")) or plan.market_id,
            outcome_id=str(outcome_id),
            token_id=str(token_id),
            strategy_id=str(strategy_id),
            strategy_family=_clean_text(candidate.get("strategy_family")) or "live_signal_aggregation",
            sleeve_id=sleeve_id,
            sleeve_group=_clean_text(candidate.get("sleeve_group")),
            sleeve_role=_clean_text(candidate.get("sleeve_role")),
            side=side,  # type: ignore[arg-type]
            order_type="limit",
            price=price,
            size=size,
            time_in_force="gtc",
            dry_run=dry_run,
            reason=_aggregation_reason(candidate),
            metadata={
                "source": "live_signal_aggregation",
                "aggregation_candidate": candidate,
                "required_notional_usd": round(notional, 6),
                "sizing_policy": {
                    "source": "live_signal_aggregation",
                    "min_size": min_size,
                    "min_buy_notional_usd": min_buy_notional,
                    "max_buy_notional_usd": max_buy_notional,
                    **size_metadata,
                },
                "sleeve": {
                    "sleeve_id": sleeve_id,
                    "sleeve_group": _clean_text(candidate.get("sleeve_group")),
                    "sleeve_role": _clean_text(candidate.get("sleeve_role")),
                },
                "cycle_id": _clean_text(candidate.get("cycle_id")),
                "trigger_type": _clean_text(candidate.get("trigger_type")),
                "trigger_source": _clean_text(candidate.get("trigger_source")),
                "paired_lifecycle": _aggregation_candidate_lifecycle_metadata(
                    candidate,
                    side=side,
                    strategy=strategy,
                ),
            },
        )
        seen_keys.add(dedupe_key)
        intents.append(intent)
    return {"intents": intents, "blockers": blockers}


def _live_signal_aggregation_decision(market_state: dict[str, Any]) -> dict[str, Any]:
    aggregation = market_state.get("live_signal_aggregation")
    if not isinstance(aggregation, dict):
        return {}
    decision = aggregation.get("decision")
    return decision if isinstance(decision, dict) else {}


def _candidate_order_side(candidate: dict[str, Any]) -> str | None:
    signal_type = str(candidate.get("signal_type") or "").strip().lower()
    explicit_side = str(candidate.get("order_side") or candidate.get("side_action") or "").strip().lower()
    if explicit_side in {"buy", "sell"}:
        return explicit_side
    if signal_type in {"buy", "rebuy"}:
        return "buy"
    if signal_type in {"sell", "reduce"}:
        return "sell"
    return None


def _candidate_size(
    candidate: dict[str, Any],
    *,
    price: float | None,
    side: str | None,
    strategy: Any | None,
    min_buy_notional_usd: float,
) -> tuple[float | None, dict[str, Any]]:
    size = _safe_float(candidate.get("requested_shares") or candidate.get("size"))
    source = "requested_shares" if size is not None else None
    notional = _safe_float(candidate.get("requested_notional_usd") or candidate.get("notional_usd"))
    if size is None and notional is not None and price is not None and price > 0.0:
        size = notional / price
        source = "requested_notional_usd"
    if (
        side == "buy"
        and price is not None
        and price > 0.0
        and _aggregation_min_notional_sizing_enabled(candidate, strategy)
    ):
        target_notional = _aggregation_min_buy_notional(strategy, fallback=min_buy_notional_usd)
        precision = _aggregation_share_precision(strategy)
        min_notional_size = _ceil_to_precision(target_notional / price, precision)
        if size is None or size * price < target_notional:
            return min_notional_size, {
                "mode": "minimum_notional_ultra_low_sleeve",
                "requested_size": size,
                "resolved_size_source": "minimum_buy_notional",
                "target_notional_usd": round(target_notional, 6),
                "share_precision": precision,
            }
    return size, {
        "mode": "candidate_size",
        "requested_size": size,
        "resolved_size_source": source,
    }


def _aggregation_min_notional_sizing_enabled(candidate: dict[str, Any], strategy: Any | None) -> bool:
    explicit_mode = str(
        candidate.get("sizing_mode")
        or candidate.get("size_policy")
        or candidate.get("sizing_policy")
        or ""
    ).strip().lower()
    if explicit_mode in {"minimum_notional", "min_notional", "min_buy_notional", "operator_minimum_order"}:
        return True
    if strategy is None:
        return False
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    strategy_mode = str(
        entry_rules.get("sizing_mode")
        or entry_rules.get("size_policy")
        or entry_rules.get("sizing_policy")
        or ""
    ).strip().lower()
    if strategy_mode in {"minimum_notional", "min_notional", "min_buy_notional", "operator_minimum_order"}:
        return True
    if _truthy_any(entry_rules, ("size_to_min_buy_notional", "scale_to_min_buy_notional")):
        return True
    return _is_ultra_low_sleeve_strategy(strategy)


def _aggregation_min_buy_notional(strategy: Any | None, *, fallback: float) -> float:
    if strategy is None:
        return fallback
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    value = _safe_float(entry_rules.get("min_buy_notional_usd") or entry_rules.get("minimum_buy_notional_usd"))
    return value if value is not None and value > 0.0 else fallback


def _aggregation_share_precision(strategy: Any | None) -> int:
    if strategy is None:
        return 3
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    precision = _safe_float(entry_rules.get("share_precision") or entry_rules.get("size_precision"))
    if precision is None:
        return 3
    return max(0, min(6, int(precision)))


def _ceil_to_precision(value: float, precision: int) -> float:
    factor = 10**precision
    return math.ceil(value * factor - 1e-12) / factor


def _aggregation_candidate_lifecycle_blocker(
    candidate: dict[str, Any],
    *,
    side: str,
    strategy: Any | None,
) -> dict[str, Any] | None:
    signal_type = str(candidate.get("signal_type") or "").strip().lower()
    trigger_source = _clean_text(candidate.get("trigger_source"))
    reason_codes = {str(reason) for reason in candidate.get("reason_codes") or []}
    strategy_id = _clean_text(candidate.get("strategy_id"))
    sleeve_id = _clean_text(candidate.get("sleeve_id"))
    if trigger_source == "paired_microcycle":
        if not _clean_text(candidate.get("cycle_id")):
            return {
                "scope": "live_signal_aggregation",
                "reason": "paired_microcycle_cycle_id_required",
                "strategy_id": strategy_id,
                "sleeve_id": sleeve_id,
            }
        if signal_type == "rebuy" and "sell_fill_allows_rebuy_review" not in reason_codes:
            return {
                "scope": "live_signal_aggregation",
                "reason": "rebuy_requires_sell_fill_evidence",
                "strategy_id": strategy_id,
                "sleeve_id": sleeve_id,
            }
        if side == "sell" and not (
            {"filled_buy_requires_paired_sell", "paired_sell_target_stale"} & reason_codes
        ):
            return {
                "scope": "live_signal_aggregation",
                "reason": "sell_requires_buy_fill_or_stale_target_evidence",
                "strategy_id": strategy_id,
                "sleeve_id": sleeve_id,
            }
    if (
        side == "buy"
        and signal_type != "rebuy"
        and not _strategy_declares_buy_lifecycle(strategy)
        and not _candidate_declares_buy_lifecycle(candidate)
    ):
        return {
            "scope": "live_signal_aggregation",
            "reason": "paired_lifecycle_policy_required_for_buy",
            "strategy_id": strategy_id,
            "sleeve_id": sleeve_id,
        }
    return None


def _strategy_declares_buy_lifecycle(strategy: Any | None) -> bool:
    if strategy is None:
        return False
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    exit_rules = dict(getattr(strategy, "exit_rules", {}) or {})
    stop_rules = dict(getattr(strategy, "stop_rules", {}) or {})
    shadow_flags = dict(getattr(strategy, "shadow_flags", {}) or {})
    return any(
        value not in {None, "", False}
        for value in (
            exit_rules.get("target_price"),
            exit_rules.get("target_delta_cents"),
            exit_rules.get("target_policy"),
            exit_rules.get("min_target_cents"),
            exit_rules.get("target_return_fraction"),
            entry_rules.get("target_price"),
            entry_rules.get("target_delta_cents"),
            stop_rules.get("stop_price"),
            stop_rules.get("max_loss_cents"),
            shadow_flags.get("core_hold_reason"),
            shadow_flags.get("hold_reason"),
        )
    )


def _candidate_declares_buy_lifecycle(candidate: dict[str, Any]) -> bool:
    lifecycle = candidate.get("lifecycle_policy") if isinstance(candidate.get("lifecycle_policy"), dict) else {}
    exit_policy = candidate.get("exit_policy") if isinstance(candidate.get("exit_policy"), dict) else {}
    stop_policy = candidate.get("stop_policy") if isinstance(candidate.get("stop_policy"), dict) else {}
    return any(
        value not in {None, "", False}
        for value in (
            lifecycle.get("target_price"),
            lifecycle.get("target_delta_cents"),
            lifecycle.get("target_policy"),
            lifecycle.get("target_return_fraction"),
            lifecycle.get("stop_price"),
            lifecycle.get("max_loss_cents"),
            lifecycle.get("max_loss_usd"),
            lifecycle.get("hold_reason"),
            exit_policy.get("target_price"),
            exit_policy.get("target_delta_cents"),
            stop_policy.get("stop_price"),
            stop_policy.get("max_loss_cents"),
            candidate.get("hold_reason"),
        )
    )


def _aggregation_candidate_lifecycle_metadata(
    candidate: dict[str, Any],
    *,
    side: str,
    strategy: Any | None,
) -> dict[str, Any]:
    trigger_source = _clean_text(candidate.get("trigger_source"))
    reason_codes = [str(reason) for reason in candidate.get("reason_codes") or []]
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {}) if strategy is not None else {}
    exit_rules = dict(getattr(strategy, "exit_rules", {}) or {}) if strategy is not None else {}
    stop_rules = dict(getattr(strategy, "stop_rules", {}) or {}) if strategy is not None else {}
    shadow_flags = dict(getattr(strategy, "shadow_flags", {}) or {}) if strategy is not None else {}
    lifecycle = candidate.get("lifecycle_policy") if isinstance(candidate.get("lifecycle_policy"), dict) else {}
    return {
        "required": side == "buy" or trigger_source == "paired_microcycle",
        "trigger_source": trigger_source,
        "cycle_id": _clean_text(candidate.get("cycle_id")),
        "reason_codes": reason_codes,
        "declared_exit_policy": {
            "target_price": exit_rules.get("target_price") or entry_rules.get("target_price") or lifecycle.get("target_price"),
            "target_delta_cents": (
                exit_rules.get("target_delta_cents")
                or entry_rules.get("target_delta_cents")
                or lifecycle.get("target_delta_cents")
            ),
            "target_policy": exit_rules.get("target_policy") or lifecycle.get("target_policy"),
        },
        "declared_stop_policy": {
            "stop_price": stop_rules.get("stop_price") or lifecycle.get("stop_price"),
            "max_loss_cents": stop_rules.get("max_loss_cents") or lifecycle.get("max_loss_cents"),
            "max_loss_usd": lifecycle.get("max_loss_usd"),
        },
        "hold_reason": shadow_flags.get("core_hold_reason") or shadow_flags.get("hold_reason") or lifecycle.get("hold_reason"),
        "standalone_lifecycle_policy": lifecycle,
    }


def _aggregation_reason(candidate: dict[str, Any]) -> str:
    reasons = candidate.get("reason_codes")
    if isinstance(reasons, list):
        for reason in reasons:
            text = _clean_text(reason)
            if text:
                return f"live_signal_aggregation:{text}"
    signal_type = _clean_text(candidate.get("signal_type")) or "intent"
    return f"live_signal_aggregation:{signal_type}"


def _intent_dedupe_key(intent: OrderIntent) -> tuple[str | None, str | None, str | None, str | None]:
    cycle_id = None
    if isinstance(intent.metadata, dict):
        cycle_id = _clean_text(intent.metadata.get("cycle_id"))
    return (_clean_text(intent.token_id), _clean_text(intent.side), _clean_text(intent.sleeve_id), cycle_id)


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _wnba_controlled_entry_blocker(
    strategy: Any,
    *,
    blockers: list[dict[str, Any]],
    strategies_by_id: dict[str, Any],
    controlled_entry_intents: int,
) -> dict[str, Any] | None:
    if not _is_wnba_controlled_entry_strategy(strategy):
        return None
    if controlled_entry_intents >= WNBA_CONTROLLED_ENTRY_MAX_INTENTS:
        return {
            "reason": "controlled_entry_event_limit_reached",
            "family": WNBA_CONTROLLED_ENTRY_FAMILY,
            "max_controlled_entry_intents": WNBA_CONTROLLED_ENTRY_MAX_INTENTS,
        }
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    requires_grid_spread_blocker = entry_rules.get("requires_grid_spread_blocker")
    if requires_grid_spread_blocker is None:
        requires_grid_spread_blocker = entry_rules.get("controlled_entry_requires_grid_spread_blocker")
    if requires_grid_spread_blocker is None:
        requires_grid_spread_blocker = True
    if bool(requires_grid_spread_blocker) and not _has_matching_grid_spread_blocker(
        blockers,
        strategies_by_id=strategies_by_id,
        entry_rules=entry_rules,
    ):
        return {
            "reason": "controlled_entry_requires_grid_spread_blocker",
            "required_blockers": sorted(GRID_SPREAD_BLOCKER_REASONS),
            "fallback_family": "price_stability_micro_grid",
        }
    if not _has_target_policy(dict(getattr(strategy, "exit_rules", {}) or {}), entry_rules):
        return {"reason": "controlled_entry_target_policy_required"}
    if not _has_stop_policy(dict(getattr(strategy, "stop_rules", {}) or {}), entry_rules):
        return {"reason": "controlled_entry_stop_policy_required"}
    return None


def _is_wnba_controlled_entry_strategy(strategy: Any) -> bool:
    return str(getattr(strategy, "family", "") or "").strip().lower() == WNBA_CONTROLLED_ENTRY_FAMILY


def _has_matching_grid_spread_blocker(
    blockers: list[dict[str, Any]],
    *,
    strategies_by_id: dict[str, Any],
    entry_rules: dict[str, Any],
) -> bool:
    token_id = str(entry_rules.get("token_id") or "").strip()
    outcome_id = str(entry_rules.get("outcome_id") or "").strip()
    for blocker in blockers:
        if str(blocker.get("reason") or "") not in GRID_SPREAD_BLOCKER_REASONS:
            continue
        blocked_strategy = strategies_by_id.get(str(blocker.get("strategy_id") or ""))
        if blocked_strategy is None:
            continue
        if str(getattr(blocked_strategy, "family", "") or "").strip().lower() != "price_stability_micro_grid":
            continue
        blocked_rules = dict(getattr(blocked_strategy, "entry_rules", {}) or {})
        blocked_token = str(blocked_rules.get("token_id") or "").strip()
        blocked_outcome = str(blocked_rules.get("outcome_id") or "").strip()
        if token_id and blocked_token and token_id == blocked_token:
            return True
        if outcome_id and blocked_outcome and outcome_id == blocked_outcome:
            return True
    return False


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
    requested_size = _safe_float(order_payload.get("size"))
    requested_mode = str(
        order_payload.get("sizing_mode")
        or order_payload.get("size_policy")
        or order_payload.get("sizing_policy")
        or ""
    ).strip().lower()
    respect_plan_size = bool(order_payload.get("respect_plan_size")) or requested_mode in {
        "plan_size",
        "strategy_plan_size",
        "fixed_shares",
        "fixed_size",
    }
    metadata = {
        "source": "operator_policy",
        "mode": policy.get("mode") or "operator_minimum_order",
        "min_size": min_size,
        "min_buy_notional_usd": min_buy_notional,
        "min_buy_notional_buffer_usd": min_buy_notional_buffer,
        "effective_min_buy_notional_usd": effective_min_buy_notional,
        "max_buy_notional_usd": _safe_float(policy.get("max_buy_notional_usd") or policy.get("max_notional_usd")),
        "llm_requested_size": requested_size,
        "respect_plan_size": respect_plan_size,
        "llm_strategy_budget_usd": strategy_budget_usd,
    }
    if side != "buy":
        return _safe_float(order_payload.get("size")), metadata
    if price <= 0.0:
        return None, metadata
    if respect_plan_size and requested_size is not None:
        return requested_size, metadata
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
    floor = _target_floor_price(rules) or 0.01
    raw_target = min(0.95, max(floor, entry_price + target_move))
    tick_size = _target_tick_size(rules)
    if tick_size is not None:
        decimals = max(0, min(6, len(f"{tick_size:.8f}".rstrip("0").split(".")[-1])))
        return round(math.ceil((raw_target - 1e-12) / tick_size) * tick_size, decimals)
    return round(raw_target, 4)


def _target_tick_size(rules: dict[str, Any]) -> float | None:
    tick = _safe_float(
        rules.get("target_tick_size")
        or rules.get("tick_size")
        or rules.get("price_tick_size")
        or rules.get("min_price_increment")
    )
    if tick is None or tick <= 0.0:
        return None
    return tick


def _target_floor_price(rules: dict[str, Any]) -> float | None:
    floor = _safe_float(
        rules.get("min_target_price")
        or rules.get("target_min_price")
        or rules.get("target_floor_price")
        or rules.get("minimum_target_price")
    )
    if floor is None or floor <= 0.0:
        return None
    return floor


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
    scoreboard_age = _first_float(
        market_state,
        (
            "scoreboard_captured_age_seconds",
            "scoreboard_snapshot_age_seconds",
            "scoreboard_capture_age_seconds",
            "scoreboard_age_seconds",
            "scoreboard_age",
        ),
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


def _is_ultra_low_sleeve_strategy(strategy: Any) -> bool:
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            getattr(strategy, "family", ""),
            getattr(strategy, "sleeve_role", ""),
            getattr(strategy, "sleeve_id", ""),
            getattr(strategy, "strategy_id", ""),
            entry_rules.get("sleeve_role"),
        )
    )
    return any(marker in haystack for marker in ("ultra_low", "ultralow", "subpenny", "decimal_grid"))


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
    scoreboard_age = _first_float(
        strategy_state,
        (
            "scoreboard_captured_age_seconds",
            "scoreboard_snapshot_age_seconds",
            "scoreboard_capture_age_seconds",
            "scoreboard_age_seconds",
            "scoreboard_age",
        ),
    )
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

    garbage_time_blocker = _garbage_time_blocker(entry_rules, strategy_state)
    if garbage_time_blocker is not None:
        return garbage_time_blocker

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
    scoped_exposure = _strategy_scoped_unresolved_exposure(
        portfolio_state,
        entry_rules=entry_rules,
    )
    if scoped_exposure is not None:
        open_positions = scoped_exposure["open_positions"]
        open_orders = scoped_exposure["open_orders"]
    else:
        open_positions = _safe_float(portfolio_state.get("open_positions"))
        open_orders = _safe_float(portfolio_state.get("open_orders")) or 0.0
    direct_unresolved_exposure = (open_positions or 0.0) + open_orders
    unresolved_exposure = direct_unresolved_exposure + pending_intents
    if explicit_position_cap is not None and open_positions is None:
        return {"reason": "position_state_required", "max_open_positions": max_open_positions}
    if max_open_positions is not None and unresolved_exposure >= max_open_positions:
        if _allows_parallel_sleeve_add(entry_rules):
            if pending_intents <= 0.0:
                return None
            return {
                "reason": "pending_intent_limit_reached",
                "open_positions": open_positions,
                "open_orders": open_orders,
                "pending_intents": pending_intents,
                "direct_unresolved_exposure": direct_unresolved_exposure,
                "unresolved_exposure": unresolved_exposure,
                "max_open_positions": max_open_positions,
                "position_limit_scope": entry_rules.get("position_limit_scope"),
            }
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


def _allows_parallel_sleeve_add(entry_rules: dict[str, Any]) -> bool:
    scope = str(entry_rules.get("position_limit_scope") or "").strip().lower()
    if scope in {"sleeve", "cycle", "parallel_sleeve", "local_sleeve"}:
        return True
    for key in (
        "allow_existing_position_add",
        "allow_existing_inventory_add",
        "allow_same_side_position_add",
        "allow_inventory_adding",
    ):
        if _truthy_rule(entry_rules.get(key)):
            return True
    return False


def _truthy_rule(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def _garbage_time_blocker(entry_rules: dict[str, Any], strategy_state: dict[str, Any]) -> dict[str, Any] | None:
    order_side = str(entry_rules.get("side") or "buy").strip().lower()
    if order_side != "buy":
        return None
    if _truthy_any(
        entry_rules,
        (
            "allow_garbage_time",
            "allow_garbage_time_entry",
            "garbage_time_reviewed",
            "q4_clutch_reviewed",
            "fresh_strategy_plan_after_garbage_time",
        ),
    ):
        return None
    raw_state = (
        strategy_state.get("garbage_time")
        or strategy_state.get("garbage_time_state")
        or strategy_state.get("is_garbage_time")
    )
    if not _truthy_any({"garbage_time": raw_state}, ("garbage_time",)):
        return None
    return {
        "reason": "garbage_time_no_new_entry",
        "garbage_time": raw_state,
        "requires_strategy_plan_revision": True,
        "message": "Garbage-time state detected; autonomous buys require a fresh or explicitly reviewed StrategyPlanJSON sleeve.",
    }


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


def _strategy_scoped_unresolved_exposure(
    portfolio_state: dict[str, Any],
    *,
    entry_rules: dict[str, Any],
) -> dict[str, float] | None:
    direct_clob = portfolio_state.get("event_scoped_direct_clob") or portfolio_state.get("direct_clob_truth")
    if not isinstance(direct_clob, dict):
        return None

    token_id = str(entry_rules.get("token_id") or entry_rules.get("asset_id") or "").strip()
    outcome_id = str(entry_rules.get("outcome_id") or "").strip()
    if not token_id and not outcome_id:
        return None

    open_positions = 0.0
    positions = ((direct_clob.get("open_positions") or {}).get("positions") or []) if isinstance(direct_clob.get("open_positions"), dict) else []
    for position in positions:
        if isinstance(position, dict) and _direct_position_matches(position, token_id=token_id, outcome_id=outcome_id):
            open_positions += 1.0

    open_orders = 0.0
    orders = ((direct_clob.get("open_orders") or {}).get("orders") or []) if isinstance(direct_clob.get("open_orders"), dict) else []
    for order in orders:
        if isinstance(order, dict) and _direct_order_matches(order, token_id=token_id, outcome_id=outcome_id):
            open_orders += 1.0

    return {"open_positions": open_positions, "open_orders": open_orders}


def _direct_position_matches(position: dict[str, Any], *, token_id: str, outcome_id: str) -> bool:
    position_token = str(
        position.get("asset")
        or position.get("asset_id")
        or position.get("token_id")
        or position.get("market_token_id")
        or ""
    ).strip()
    position_outcome = str(position.get("outcome_id") or "").strip()
    return bool((token_id and position_token == token_id) or (outcome_id and position_outcome == outcome_id))


def _direct_order_matches(order: dict[str, Any], *, token_id: str, outcome_id: str) -> bool:
    order_token = str(
        order.get("asset_id")
        or order.get("token_id")
        or order.get("asset")
        or order.get("market_token_id")
        or ""
    ).strip()
    order_outcome = str(order.get("outcome_id") or "").strip()
    return bool((token_id and order_token == token_id) or (outcome_id and order_outcome == outcome_id))


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


def _strategy_sleeve_metadata(strategy: Any) -> dict[str, Any]:
    entry_rules = dict(getattr(strategy, "entry_rules", {}) or {})
    strategy_id = str(getattr(strategy, "strategy_id", "") or "").strip()
    sleeve_id = str(getattr(strategy, "sleeve_id", None) or entry_rules.get("sleeve_id") or strategy_id).strip()
    sleeve_group = str(getattr(strategy, "sleeve_group", None) or entry_rules.get("sleeve_group") or "").strip()
    sleeve_role = str(getattr(strategy, "sleeve_role", None) or entry_rules.get("sleeve_role") or "").strip()
    metadata: dict[str, Any] = {
        "sleeve_id": sleeve_id or strategy_id,
        "sleeve_side": str(getattr(strategy, "side", "") or ""),
    }
    if sleeve_group:
        metadata["sleeve_group"] = sleeve_group
    if sleeve_role:
        metadata["sleeve_role"] = sleeve_role
    return metadata


def _attach_sleeve_metadata(plan: StrategyPlan, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sleeve_by_strategy = {
        strategy.strategy_id: _strategy_sleeve_metadata(strategy)
        for strategy in plan.active_strategies
    }
    enriched: list[dict[str, Any]] = []
    for blocker in blockers:
        strategy_id = str(blocker.get("strategy_id") or "")
        sleeve = sleeve_by_strategy.get(strategy_id)
        enriched.append({**sleeve, **blocker} if sleeve else dict(blocker))
    return enriched


def _build_sleeve_states(
    plan: StrategyPlan,
    *,
    intents: list[OrderIntent],
    blockers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    intents_by_strategy: dict[str, list[OrderIntent]] = {}
    for intent in intents:
        intents_by_strategy.setdefault(intent.strategy_id, []).append(intent)
    blockers_by_strategy: dict[str, list[dict[str, Any]]] = {}
    for blocker in blockers:
        strategy_id = str(blocker.get("strategy_id") or "")
        if strategy_id:
            blockers_by_strategy.setdefault(strategy_id, []).append(blocker)

    states: list[dict[str, Any]] = []
    for strategy in plan.active_strategies:
        strategy_intents = intents_by_strategy.get(strategy.strategy_id, [])
        strategy_blockers = blockers_by_strategy.get(strategy.strategy_id, [])
        status = "eligible"
        if strategy_intents:
            status = "intent_created"
        elif strategy_blockers:
            status = "blocked"
        sleeve = _strategy_sleeve_metadata(strategy)
        states.append(
            {
                **sleeve,
                "strategy_id": strategy.strategy_id,
                "strategy_family": strategy.family,
                "status": status,
                "intent_count": len(strategy_intents),
                "blocker_count": len(strategy_blockers),
                "intent_ids": [intent.intent_id for intent in strategy_intents],
                "blocker_reasons": [str(blocker.get("reason") or "") for blocker in strategy_blockers],
            }
        )
    return states


__all__ = ["MIN_BUY_NOTIONAL_USD", "MIN_ORDER_SIZE", "evaluate_strategy_plan"]
