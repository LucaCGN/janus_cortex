from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.basketball_logic import (
    build_profit_ratcheted_risk_state,
    classify_basketball_regime,
    generate_strategy_sleeve_candidates,
)
from app.modules.agentic.contracts import (
    LiveSignal,
    LiveSignalFreshness,
    LiveSignalPriceBand,
    LiveSignalRiskRequest,
)


SCHEMA_VERSION = "live_game_context_evidence_v1"
OPPORTUNISTIC_SIGNAL_SCHEMA_VERSION = "live_game_opportunistic_signal_v1"


def build_live_game_context_evidence(
    *,
    event_id: str,
    plan: dict[str, Any],
    market_state: dict[str, Any],
    portfolio_state: dict[str, Any],
    direct_clob: dict[str, Any],
    max_buy_notional_usd: float | None = None,
    min_buy_notional_usd: float = 1.0,
) -> dict[str, Any]:
    """Build event-level scenario/risk/context evidence for live aggregation.

    This is not an execution module. It turns already-normalized live tick
    evidence into explicit context that the aggregator and postgame review can
    consume without requiring an LLM call.
    """

    now = datetime.now(timezone.utc)
    outcomes = _outcome_contexts(plan=plan, market_state=market_state)
    snapshot = _classification_snapshot(market_state=market_state, outcomes=outcomes)
    classification = classify_basketball_regime(snapshot)
    paired_pnl = _paired_microcycle_pnl(market_state.get("paired_microcycle"))
    risk_state = build_profit_ratcheted_risk_state(
        portfolio_value=_portfolio_value_for_risk(max_buy_notional_usd),
        realized_event_pnl=paired_pnl["net_realized_pnl_usd"],
        realized_day_pnl=_realized_day_pnl(portfolio_state),
        open_unrealized_pnl=paired_pnl["net_open_mark_pnl_usd"],
        unresolved_inventory=_unresolved_inventory(portfolio_state=portfolio_state, direct_clob=direct_clob),
        scenario_level=str(classification.get("scenario_level") or "D"),
        confidence=float(classification.get("confidence") or 0.0),
        liquidity_score=_liquidity_score(outcomes),
        latency_penalty=_latency_penalty(snapshot),
    )
    ml_confidence = _ml_confidence_by_sleeve(plan=plan, market_state=market_state, classification=classification)
    sleeve_candidates = generate_strategy_sleeve_candidates(
        classification,
        market_state={"outcomes": outcomes, "snapshot": snapshot},
        existing_sleeves=_existing_sleeves(plan),
        live_authority=False,
    )
    opportunistic = _opportunistic_signal_candidates(
        event_id=event_id,
        plan=plan,
        outcomes=outcomes,
        classification=classification,
        risk_state=risk_state,
        ml_confidence_by_sleeve=ml_confidence,
        min_buy_notional_usd=min_buy_notional_usd,
        max_buy_notional_usd=max_buy_notional_usd,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "generated_at_utc": now.isoformat(),
        "execution_boundary": "evidence_only",
        "game_scenario": classification,
        "classification_snapshot": snapshot,
        "outcome_contexts": outcomes,
        "sleeve_candidate_review": sleeve_candidates,
        "ml_confidence_by_sleeve": ml_confidence,
        "dynamic_risk_state": risk_state,
        "opportunistic_signal_candidates": opportunistic,
        "notes": [
            "Pregame priors and PBP annotations are context only.",
            "Standalone opportunistic candidates require realized-profit budget and paired lifecycle policy.",
        ],
    }


def live_signals_from_live_game_context(evidence: dict[str, Any] | None) -> list[LiveSignal]:
    if not isinstance(evidence, dict):
        return []
    event_id = str(evidence.get("event_id") or "").strip()
    if not event_id:
        return []
    signals: list[LiveSignal] = []
    now = _parse_datetime(evidence.get("generated_at_utc")) or datetime.now(timezone.utc)
    for candidate in evidence.get("opportunistic_signal_candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("status") != "signal_candidate":
            continue
        signals.append(
            LiveSignal(
                event_id=event_id,
                market_id=_clean(candidate.get("market_id")),
                outcome_id=_clean(candidate.get("outcome_id")),
                market_token_id=_clean(candidate.get("market_token_id")),
                source="deterministic",
                signal_type="buy",
                side=_clean(candidate.get("side")),
                emitted_at_utc=now,
                price_band=LiveSignalPriceBand(
                    current_price=_float(candidate.get("current_price")),
                    target_price=_float(candidate.get("max_price")),
                    band_role="opportunistic_profit_ratcheted_entry",
                ),
                confidence=_float(candidate.get("confidence")),
                confidence_source="live_game_context:scenario_profit_risk",
                freshness=LiveSignalFreshness(source_timestamp_utc=now, stale=False),
                reason_codes=[str(reason) for reason in candidate.get("reason_codes") or []],
                risk_request=LiveSignalRiskRequest(
                    sleeve_id=_clean(candidate.get("sleeve_id")),
                    sleeve_role=_clean(candidate.get("sleeve_role")),
                    requested_notional_usd=_float(candidate.get("requested_notional_usd")),
                    requested_shares=_float(candidate.get("requested_shares")),
                    max_price=_float(candidate.get("max_price")),
                ),
                evidence_paths=[str(path) for path in evidence.get("evidence_paths") or []],
                payload={
                    "schema_version": OPPORTUNISTIC_SIGNAL_SCHEMA_VERSION,
                    "strategy_id": _clean(candidate.get("strategy_id")),
                    "strategy_family": _clean(candidate.get("strategy_family")),
                    "sleeve_id": _clean(candidate.get("sleeve_id")),
                    "sleeve_group": _clean(candidate.get("sleeve_group")),
                    "sleeve_role": _clean(candidate.get("sleeve_role")),
                    "trigger_type": "profit_ratcheted_opportunistic_entry",
                    "trigger_source": "live_game_context",
                    "position_limit_scope": "local_sleeve",
                    "allow_existing_position_add": True,
                    "allow_inventory_adding": True,
                    "standalone_signal": True,
                    "lifecycle_policy": candidate.get("lifecycle_policy") or {},
                    "game_scenario": evidence.get("game_scenario") or {},
                    "dynamic_risk_state": evidence.get("dynamic_risk_state") or {},
                    "ml_confidence": candidate.get("ml_confidence") or {},
                },
            )
        )
    return signals


def _classification_snapshot(*, market_state: dict[str, Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = market_state.get("normalized_live_snapshot") if isinstance(market_state.get("normalized_live_snapshot"), dict) else {}
    game = normalized.get("game") if isinstance(normalized.get("game"), dict) else {}
    live_state = market_state.get("live_state") if isinstance(market_state.get("live_state"), dict) else {}
    latest = live_state.get("latest_snapshot") if isinstance(live_state.get("latest_snapshot"), dict) else {}
    underdog = _underdog(outcomes)
    favorite = _favorite(outcomes)
    score_gap = _score_gap_for_outcomes(outcomes)
    scoreboard_age = _min_number([outcome.get("scoreboard_age_seconds") for outcome in outcomes])
    orderbook_age = _min_number([outcome.get("orderbook_age_seconds") for outcome in outcomes])
    clock_seconds = _clock_seconds(_first_non_empty(game.get("clock"), game.get("game_clock"), latest.get("clock")))
    return {
        "period": _int(_first_non_empty(game.get("period"), latest.get("period"))),
        "clock_seconds_remaining": clock_seconds,
        "score_gap": score_gap,
        "underdog_price": underdog.get("price") if underdog else None,
        "favorite_price": favorite.get("price") if favorite else None,
        "feed_stale": _is_stale(scoreboard_age) or _is_stale(orderbook_age),
        "final": _is_final(_first_non_empty(game.get("game_status_text"), game.get("status"), latest.get("status"))),
        "garbage_time": bool(market_state.get("garbage_time")),
        "price_flip": bool(market_state.get("price_flip")),
        "leadership_switch": bool(market_state.get("leadership_switch")),
        "star_injury": _player_shock_contains(market_state, "injury"),
        "star_ejection": _player_shock_contains(market_state, "ejection"),
        "star_foul_trouble": _player_shock_contains(market_state, "foul"),
        "recent_run_margin": _recent_run_margin(market_state),
        "scoreboard_age_seconds": scoreboard_age,
        "orderbook_age_seconds": orderbook_age,
    }


def _outcome_contexts(*, plan: dict[str, Any], market_state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        strategy_id = str(strategy.get("strategy_id") or "").strip()
        outcome_id = _clean(entry_rules.get("outcome_id"))
        token_id = _clean(entry_rules.get("token_id") or entry_rules.get("asset_id"))
        state = _strategy_market_state(
            market_state=market_state,
            outcome_id=outcome_id,
            token_id=token_id,
            strategy_id=strategy_id,
        )
        rows.append(
            {
                "strategy_id": strategy_id,
                "market_id": _clean(entry_rules.get("market_id") or plan.get("market_id")),
                "outcome_id": outcome_id,
                "market_token_id": token_id,
                "side": _clean(strategy.get("side") or entry_rules.get("outcome_label")),
                "sleeve_id": _clean(strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id),
                "sleeve_group": _clean(strategy.get("sleeve_group")),
                "sleeve_role": _clean(strategy.get("sleeve_role") or entry_rules.get("sleeve_role")),
                "strategy_family": _clean(strategy.get("family")),
                "price": _first_float({**state, **entry_rules}, ("price", "current_price", "best_ask", "best_bid", "max_price")),
                "best_bid": _first_float(state, ("best_bid",)),
                "best_ask": _first_float(state, ("best_ask",)),
                "spread_cents": _first_float(state, ("spread_cents",)),
                "score_gap": _first_float(state, ("score_gap",)),
                "period": _int(state.get("period")),
                "game_clock": _clean(state.get("game_clock")),
                "scoreboard_age_seconds": _first_float(state, ("scoreboard_age_seconds",)),
                "orderbook_age_seconds": _first_float(state, ("orderbook_age_seconds",)),
            }
        )
    return rows


def _strategy_market_state(
    *,
    market_state: dict[str, Any],
    outcome_id: str | None,
    token_id: str | None,
    strategy_id: str | None,
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for bucket, key in (
        ("strategy_states", strategy_id),
        ("strategy_market_states", strategy_id),
        ("outcome_states", outcome_id),
        ("outcome_market_states", outcome_id),
        ("token_states", token_id),
        ("token_market_states", token_id),
    ):
        values = market_state.get(bucket)
        if isinstance(values, dict) and key and isinstance(values.get(key), dict):
            state.update(values[key])
    return state


def _ml_confidence_by_sleeve(
    *,
    plan: dict[str, Any],
    market_state: dict[str, Any],
    classification: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    pbp = market_state.get("pbp_annotation") if isinstance(market_state.get("pbp_annotation"), dict) else {}
    tags = [tag for tag in pbp.get("tags") or [] if isinstance(tag, dict)]
    tag_relevance = {
        _norm(role)
        for tag in tags
        for role in (tag.get("sleeve_relevance") or [])
        if str(role or "").strip()
    }
    result: dict[str, dict[str, Any]] = {}
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        sleeve_id = _clean(strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy.get("strategy_id"))
        if not sleeve_id:
            continue
        role = _clean(strategy.get("sleeve_role") or entry_rules.get("sleeve_role")) or ""
        family = _clean(strategy.get("family")) or ""
        role_key = _norm(role)
        family_key = _norm(family)
        relevant = role_key in tag_relevance or any(part in tag_relevance for part in family_key.split("_"))
        scenario_confidence = float(classification.get("confidence") or 0.0)
        tag_confidence = max((_float(tag.get("confidence")) or 0.0 for tag in tags), default=0.0)
        confidence = max(0.45, scenario_confidence * (1.0 if relevant else 0.75), tag_confidence if relevant else 0.0)
        result[sleeve_id] = {
            "schema_version": "sleeve_ml_confidence_v1",
            "sleeve_id": sleeve_id,
            "sleeve_role": role or None,
            "strategy_family": family or None,
            "confidence": round(min(confidence, 0.95), 3),
            "confidence_source": "scenario_classifier+pbp_annotation",
            "model_status": pbp.get("model_tier") or "deterministic_fallback",
            "intended_model": pbp.get("intended_model"),
            "executable": False,
            "reason_codes": _unique(
                [
                    "pbp_relevant_to_sleeve" if relevant else "scenario_only_confidence",
                    f"scenario_{classification.get('scenario_level') or 'U'}",
                ]
            ),
        }
    return result


def _opportunistic_signal_candidates(
    *,
    event_id: str,
    plan: dict[str, Any],
    outcomes: list[dict[str, Any]],
    classification: dict[str, Any],
    risk_state: dict[str, Any],
    ml_confidence_by_sleeve: dict[str, dict[str, Any]],
    min_buy_notional_usd: float,
    max_buy_notional_usd: float | None,
) -> list[dict[str, Any]]:
    scenario = str(classification.get("scenario_level") or "D")
    underdog = _underdog(outcomes)
    tail_budget = _float(risk_state.get("tail_risk_budget_usd")) or 0.0
    profit_risk_budget = _float(risk_state.get("max_realized_profit_risk_usd")) or 0.0
    if scenario in {"D", "U"}:
        return [_blocked_opportunistic(event_id, "scenario_blocks_opportunistic_entry", scenario, tail_budget)]
    if underdog is None:
        return [_blocked_opportunistic(event_id, "underdog_outcome_required", scenario, tail_budget)]
    price = _float(underdog.get("best_ask") or underdog.get("price"))
    if price is None or price <= 0.0:
        return [_blocked_opportunistic(event_id, "fresh_underdog_ask_required", scenario, tail_budget)]
    available_budget = max(tail_budget, profit_risk_budget) if scenario == "S" or price < 0.10 else profit_risk_budget
    if available_budget < min_buy_notional_usd:
        return [_blocked_opportunistic(event_id, "realized_profit_opportunistic_budget_below_minimum", scenario, available_budget)]
    max_notional = min(available_budget, max_buy_notional_usd or available_budget)
    requested_notional = max(min_buy_notional_usd, min(max_notional, available_budget))
    shares = requested_notional / price
    side_slug = _slug(underdog.get("side") or "underdog")
    sleeve_id = f"{side_slug}-opportunistic-tail"
    return [
        {
            "schema_version": OPPORTUNISTIC_SIGNAL_SCHEMA_VERSION,
            "event_id": event_id,
            "status": "signal_candidate",
            "strategy_id": f"{event_id}-{sleeve_id}",
            "strategy_family": "opportunistic_profit_ratcheted_entry",
            "sleeve_id": sleeve_id,
            "sleeve_group": side_slug,
            "sleeve_role": "opportunistic_tail_rebound",
            "side": underdog.get("side"),
            "market_id": underdog.get("market_id") or plan.get("market_id"),
            "outcome_id": underdog.get("outcome_id"),
            "market_token_id": underdog.get("market_token_id"),
            "current_price": price,
            "max_price": price,
            "requested_notional_usd": round(requested_notional, 6),
            "requested_shares": round(shares, 6),
            "confidence": round(min(float(classification.get("confidence") or 0.0), 0.85), 3),
            "ml_confidence": _closest_ml_confidence(underdog, ml_confidence_by_sleeve),
            "lifecycle_policy": {
                "required": True,
                "target_delta_cents": 1.0 if price < 0.10 else 2.0,
                "target_policy": "profit_ratcheted_tail_micro_target",
                "stop_policy": "tail_budget_limited_no_add_without_new_profit",
                "max_loss_usd": round(requested_notional, 6),
                "rebuy_requires_fresh_review": True,
            },
            "reason_codes": [
                "profit_ratcheted_opportunistic_budget_available",
                f"scenario_{scenario}",
                "standalone_opportunistic_entry_detected",
            ],
        }
    ]


def _blocked_opportunistic(event_id: str, reason: str, scenario: str, available_budget: float) -> dict[str, Any]:
    return {
        "schema_version": OPPORTUNISTIC_SIGNAL_SCHEMA_VERSION,
        "event_id": event_id,
        "status": "blocked",
        "reason_codes": [reason, f"scenario_{scenario}"],
        "available_opportunistic_budget_usd": round(available_budget, 6),
    }


def _existing_sleeves(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        rows.append(
            {
                "sleeve_id": strategy.get("sleeve_id") or strategy.get("strategy_id"),
                "sleeve_role": strategy.get("sleeve_role"),
                "family": strategy.get("family"),
            }
        )
    return rows


def _paired_microcycle_pnl(value: Any) -> dict[str, float]:
    evidence = value if isinstance(value, dict) else {}
    realized = 0.0
    open_mark = 0.0
    for cycle in evidence.get("cycles") or []:
        if not isinstance(cycle, dict):
            continue
        buy = cycle.get("buy_leg") if isinstance(cycle.get("buy_leg"), dict) else {}
        sell = cycle.get("sell_leg") if isinstance(cycle.get("sell_leg"), dict) else {}
        buy_price = _float(buy.get("price"))
        sell_price = _float(sell.get("price"))
        buy_shares = _float(buy.get("shares")) or 0.0
        sell_shares = _float(sell.get("shares")) or 0.0
        sell_status = str(sell.get("status") or "").strip().lower()
        if buy_price is not None and sell_price is not None and sell_status == "filled":
            realized += min(buy_shares, sell_shares) * (sell_price - buy_price)
        elif buy_price is not None and buy_shares > 0.0:
            open_mark -= buy_shares * buy_price
    return {"net_realized_pnl_usd": round(realized, 6), "net_open_mark_pnl_usd": round(open_mark, 6)}


def _portfolio_value_for_risk(max_buy_notional_usd: float | None) -> float:
    cap = max_buy_notional_usd if max_buy_notional_usd is not None and max_buy_notional_usd > 0 else 10.0
    return cap / 0.10


def _realized_day_pnl(portfolio_state: dict[str, Any]) -> float:
    for key in ("realized_day_pnl_usd", "day_realized_pnl_usd", "daily_realized_pnl_usd"):
        value = _float(portfolio_state.get(key))
        if value is not None:
            return value
    return 0.0


def _unresolved_inventory(*, portfolio_state: dict[str, Any], direct_clob: dict[str, Any]) -> bool:
    proof = portfolio_state.get("current_event_inventory_proof")
    if isinstance(proof, dict) and proof.get("unresolved_inventory_present"):
        return True
    return bool(_orders(direct_clob) or _positions(direct_clob))


def _orders(direct_clob: dict[str, Any]) -> list[dict[str, Any]]:
    orders = (direct_clob.get("open_orders") or {}).get("orders") if isinstance(direct_clob, dict) else None
    return [row for row in orders or [] if isinstance(row, dict)]


def _positions(direct_clob: dict[str, Any]) -> list[dict[str, Any]]:
    positions = (direct_clob.get("open_positions") or {}).get("positions") if isinstance(direct_clob, dict) else None
    return [row for row in positions or [] if isinstance(row, dict)]


def _liquidity_score(outcomes: list[dict[str, Any]]) -> float:
    spreads = [_float(row.get("spread_cents")) for row in outcomes]
    spreads = [value for value in spreads if value is not None]
    if not spreads:
        return 0.5
    spread = min(spreads)
    if spread <= 1.0:
        return 1.0
    if spread <= 3.0:
        return 0.8
    if spread <= 6.0:
        return 0.55
    return 0.25


def _latency_penalty(snapshot: dict[str, Any]) -> float:
    ages = [
        _float(snapshot.get("scoreboard_age_seconds")),
        _float(snapshot.get("orderbook_age_seconds")),
    ]
    max_age = max([age for age in ages if age is not None], default=0.0)
    if max_age <= 5:
        return 0.0
    if max_age <= 45:
        return 0.1
    return 0.4


def _is_stale(value: float | None) -> bool:
    return value is not None and value > 120.0


def _is_final(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"final", "final/ot"} or "final" in text


def _player_shock_contains(market_state: dict[str, Any], needle: str) -> bool:
    for shock in market_state.get("player_status_shocks") or []:
        if not isinstance(shock, dict):
            continue
        text = " ".join(str(item) for item in shock.get("tags") or []).lower()
        if needle in text:
            return True
    return False


def _recent_run_margin(market_state: dict[str, Any]) -> float | None:
    pbp = market_state.get("pbp_annotation") if isinstance(market_state.get("pbp_annotation"), dict) else {}
    for tag in pbp.get("tags") or []:
        if not isinstance(tag, dict) or tag.get("tag_type") != "score_run":
            continue
        evidence = tag.get("evidence") if isinstance(tag.get("evidence"), dict) else {}
        swing = _float(evidence.get("swing"))
        if swing is not None:
            return swing
    return None


def _score_gap_for_outcomes(outcomes: list[dict[str, Any]]) -> float | None:
    gaps = [_float(row.get("score_gap")) for row in outcomes if _float(row.get("score_gap")) is not None]
    if not gaps:
        return None
    return max(gaps, key=abs)


def _underdog(outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [row for row in outcomes if _float(row.get("price")) is not None]
    return min(priced, key=lambda row: float(row.get("price"))) if priced else None


def _favorite(outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [row for row in outcomes if _float(row.get("price")) is not None]
    return max(priced, key=lambda row: float(row.get("price"))) if priced else None


def _closest_ml_confidence(outcome: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sleeve_id = _clean(outcome.get("sleeve_id"))
    if sleeve_id and sleeve_id in rows:
        return rows[sleeve_id]
    return {
        "schema_version": "sleeve_ml_confidence_v1",
        "confidence": None,
        "model_status": "not_matched_to_sleeve",
        "executable": False,
    }


def _min_number(values: list[Any]) -> float | None:
    parsed = [_float(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return min(parsed) if parsed else None


def _first_float(values: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float(values.get(key))
        if value is not None:
            return value
    return None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _clock_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if ":" in text:
        try:
            minutes, seconds = text.split(":", 1)
            return float(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    if text.startswith("PT"):
        import re

        match = re.search(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
        if match:
            return float(match.group(1) or 0) * 60 + float(match.group(2) or 0)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _slug(value: Any) -> str:
    text = _norm(value)
    keep = [char if char.isalnum() else "_" for char in text]
    return "_".join(part for part in "".join(keep).split("_") if part) or "unknown"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


__all__ = [
    "OPPORTUNISTIC_SIGNAL_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_live_game_context_evidence",
    "live_signals_from_live_game_context",
]
