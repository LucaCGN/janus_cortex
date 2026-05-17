from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def classify_basketball_regime(snapshot: dict[str, Any]) -> dict[str, Any]:
    period = _safe_int(snapshot.get("period")) or 0
    clock_seconds = _safe_float(snapshot.get("clock_seconds_remaining"))
    score_gap = abs(_safe_float(snapshot.get("score_gap")) or _safe_float(snapshot.get("score_margin")) or 0.0)
    underdog_price = _safe_float(snapshot.get("underdog_price"))
    favorite_price = _safe_float(snapshot.get("favorite_price"))
    previous_underdog_price = _safe_float(snapshot.get("previous_underdog_price"))
    previous_favorite_price = _safe_float(snapshot.get("previous_favorite_price"))
    starters_active = bool(snapshot.get("starters_active", True))
    feed_stale = bool(snapshot.get("feed_stale") or snapshot.get("stale_feed"))
    final_state = bool(snapshot.get("final") or snapshot.get("game_final"))
    garbage_signal = bool(snapshot.get("garbage_time") or snapshot.get("bench_emptying") or snapshot.get("market_settling"))
    price_flip = bool(snapshot.get("price_flip") or snapshot.get("leadership_switch"))
    star_shock = bool(snapshot.get("star_injury") or snapshot.get("star_ejection") or snapshot.get("star_foul_trouble"))
    recent_run = abs(_safe_float(snapshot.get("recent_run_margin")) or 0.0)

    labels: list[str] = []
    evidence: list[dict[str, Any]] = []
    impact = "shadow"
    scenario = "U"
    confidence = 0.55

    if feed_stale:
        labels.append("stale_or_inconsistent_feed")
        evidence.append({"reason": "feed_stale"})
        return _classifier_result("D", labels, 0.75, evidence, "block", ["feed_recovers_and_reconciles"])
    if final_state:
        labels.append("final_state")
        evidence.append({"reason": "game_final"})
        return _classifier_result("D", labels, 0.95, evidence, "shutdown", ["market_reopens_with_unsettled_inventory"])
    if garbage_signal or (score_gap >= 18 and (period >= 4 or (clock_seconds is not None and clock_seconds <= 420))):
        labels.append("garbage_time_or_falling_knife")
        evidence.append({"reason": "large_gap_or_bench_emptying", "score_gap": score_gap, "period": period})
        return _classifier_result("D", labels, 0.86, evidence, "shutdown", ["score_gap_contracts_and_starters_return"])

    if period >= 5:
        labels.append("overtime")
        labels.append("clutch_close_game" if score_gap <= 5 else "ot_gap")
        evidence.append({"reason": "period_5_plus", "period": period, "score_gap": score_gap})
        scenario = "S" if score_gap <= 5 else "B"
        impact = "allow_tight_lifecycle"
        confidence = 0.8
    elif price_flip or _crossed_expectation(previous_underdog_price, underdog_price, previous_favorite_price, favorite_price):
        labels.append("full_expectation_inversion")
        evidence.append({"reason": "price_or_leadership_flip"})
        scenario = "S"
        impact = "allow_medium_or_profit_funded_high"
        confidence = 0.82
    elif period >= 4 and score_gap <= 6 and starters_active:
        labels.append("clutch_close_game")
        evidence.append({"reason": "late_close_starters_active", "period": period, "score_gap": score_gap})
        scenario = "A"
        impact = "allow_hold_hedge_add_down_micro_grid"
        confidence = 0.84
    elif score_gap <= 5:
        labels.append("close_game_stable_oscillation")
        evidence.append({"reason": "small_score_gap", "score_gap": score_gap})
        scenario = "A"
        impact = "allow_micro_grid"
        confidence = 0.78
    elif _slow_underdog_descent(snapshot, underdog_price, previous_underdog_price):
        labels.append("slow_underdog_descent_with_spikes")
        evidence.append({"reason": "underdog_downtrend_with_rebound_room", "underdog_price": underdog_price})
        scenario = "B"
        impact = "selective_scalp_only"
        confidence = 0.66
    elif _favorite_floor_rebound(snapshot, favorite_price, previous_favorite_price):
        labels.append("favorite_floor_rebound")
        evidence.append({"reason": "favorite_temporarily_below_floor", "favorite_price": favorite_price})
        scenario = "C"
        impact = "support_lane"
        confidence = 0.7
    elif recent_run >= 8:
        labels.append("star_or_team_run")
        evidence.append({"reason": "recent_run", "recent_run_margin": recent_run})
        scenario = "B"
        impact = "shadow_or_selective_trend_pickup"
        confidence = 0.62

    if star_shock:
        labels.append("star_status_shock")
        evidence.append({"reason": "injury_ejection_or_foul_trouble"})
        impact = "llm_or_codex_review_required"
        confidence = max(confidence, 0.78)
        if scenario == "U":
            scenario = "S"

    if scenario == "U":
        labels.append("unclassified")
        evidence.append({"reason": "no_positive_profile_detected"})
        scenario = "D"
        impact = "block_until_instrumented"
        confidence = 0.62

    invalidation = ["feed_stale", "garbage_time", "score_gap_breaks_profile", "orderbook_stale"]
    return _classifier_result(scenario, labels, confidence, evidence, impact, invalidation)


def tag_basketball_pbp_events(events: list[dict[str, Any]], *, player_roles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    roles = {str(key).lower(): value for key, value in dict(player_roles or {}).items()}
    tagged: list[dict[str, Any]] = []
    previous_margin: float | None = None
    for index, event in enumerate(events):
        description = str(event.get("description") or event.get("text") or "").lower()
        event_type = _pbp_event_type(description)
        player = str(event.get("player") or event.get("athlete") or event.get("player_name") or "").strip()
        margin = _safe_float(event.get("score_margin") or event.get("score_gap"))
        score_delta = None if margin is None or previous_margin is None else round(margin - previous_margin, 3)
        if margin is not None:
            previous_margin = margin
        tags = [event_type]
        if "timeout" in description:
            tags.append("timeout")
        if "substitution" in description or "enters" in description:
            tags.append("substitution")
        if score_delta is not None and abs(score_delta) >= 5:
            tags.append("run")
        role = roles.get(player.lower(), "unknown") if player else "unknown"
        if role in {"star_creator", "defensive_anchor", "bench_scorer"}:
            tags.append("star_event" if role != "bench_scorer" else "bench_event")
        tagged.append(
            {
                "event_index": event.get("event_index", index),
                "period": _safe_int(event.get("period")),
                "clock": event.get("clock"),
                "clock_seconds_remaining": _safe_float(event.get("clock_seconds_remaining")),
                "event_type": event_type,
                "tags": sorted(set(tags)),
                "player": player or None,
                "player_role": role,
                "score_margin": margin,
                "score_delta": score_delta,
                "raw": event,
            }
        )
    return tagged


def build_price_impact_windows(
    tagged_events: list[dict[str, Any]],
    orderbook_ticks: list[dict[str, Any]],
    *,
    before_seconds: float = 60.0,
    after_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    parsed_ticks = [(tick, _parse_datetime(tick.get("captured_at_utc") or tick.get("captured_at"))) for tick in orderbook_ticks]
    for event in tagged_events:
        event_time = _parse_datetime(event.get("timestamp_utc") or event.get("time_utc") or (event.get("raw") or {}).get("timestamp_utc"))
        before_prices: list[float] = []
        after_prices: list[float] = []
        if event_time is not None:
            for tick, tick_time in parsed_ticks:
                if tick_time is None:
                    continue
                delta = (tick_time - event_time).total_seconds()
                price = _safe_float(tick.get("mid_price"))
                if price is None:
                    continue
                if -before_seconds <= delta < 0:
                    before_prices.append(price)
                elif 0 <= delta <= after_seconds:
                    after_prices.append(price)
        before_mid = sum(before_prices) / len(before_prices) if before_prices else None
        after_mid = sum(after_prices) / len(after_prices) if after_prices else None
        windows.append(
            {
                "event_index": event.get("event_index"),
                "event_type": event.get("event_type"),
                "before_mid": round(before_mid, 6) if before_mid is not None else None,
                "after_mid": round(after_mid, 6) if after_mid is not None else None,
                "price_impact": round(after_mid - before_mid, 6) if before_mid is not None and after_mid is not None else None,
                "before_tick_count": len(before_prices),
                "after_tick_count": len(after_prices),
                "fillability_proxy": "needs_depth" if not before_prices or not after_prices else "sampled",
            }
        )
    return windows


def generate_strategy_sleeve_candidates(
    classification: dict[str, Any],
    *,
    market_state: dict[str, Any] | None = None,
    existing_sleeves: list[dict[str, Any]] | None = None,
    live_authority: bool = False,
) -> dict[str, Any]:
    scenario = str(classification.get("scenario_level") or "D")
    labels = set(classification.get("regime_labels") or [])
    existing_ids = {str(item.get("sleeve_id")) for item in existing_sleeves or [] if isinstance(item, dict)}
    candidates: list[dict[str, Any]] = []
    if scenario == "A":
        candidates.append(_sleeve("close-game-micro-grid", "price_stability_micro_grid", "low", live_authority))
    if scenario == "S" or "overtime" in labels:
        candidates.append(_sleeve("expectation-inversion", "inversion", "medium", live_authority))
        candidates.append(_sleeve("ot-rebound", "ot_rebound", "medium", live_authority))
    if scenario == "B":
        candidates.append(_sleeve("underdog-rebound", "underdog_rebound", "medium", live_authority))
    if scenario == "C":
        candidates.append(_sleeve("favorite-floor", "favorite_floor_rebound", "low", live_authority))
    if scenario == "D":
        candidates.append(_sleeve("shutdown", "shutdown", "none", False, state="disabled"))
    conflicts = [
        {"reason": "duplicate_sleeve", "sleeve_id": item["sleeve_id"]}
        for item in candidates
        if item["sleeve_id"] in existing_ids
    ]
    return {
        "schema_version": "strategy_sleeve_candidates_v1",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "conflicts": conflicts,
        "dependency_graph": {
            "nodes": [item["sleeve_id"] for item in candidates],
            "edges": _sleeve_edges(candidates),
            "portfolio_coordination_required": len(candidates) > 1,
        },
        "market_state": market_state or {},
    }


def build_profit_ratcheted_risk_state(
    *,
    portfolio_value: float,
    realized_event_pnl: float,
    realized_day_pnl: float,
    open_unrealized_pnl: float = 0.0,
    unresolved_inventory: bool = False,
    scenario_level: str = "A",
    confidence: float = 0.7,
    liquidity_score: float = 1.0,
    latency_penalty: float = 0.0,
) -> dict[str, Any]:
    realized_profit = max(0.0, realized_event_pnl + realized_day_pnl)
    realized_return = realized_profit / max(portfolio_value, 0.000001)
    ladder = _risk_ladder(realized_return)
    low, medium, high = ladder["split"]
    tail_budget = 0.0 if scenario_level in {"D", "U"} else realized_profit * ladder["max_profit_risk"] * high
    if unresolved_inventory:
        tail_budget = 0.0
    trade_score = max(0.0, confidence) * max(0.0, liquidity_score) - max(0.0, latency_penalty)
    if scenario_level in {"D", "U"} or unresolved_inventory:
        trade_score -= 1.0
    return {
        "schema_version": "profit_ratcheted_risk_state_v1",
        "portfolio_value": round(portfolio_value, 6),
        "realized_event_pnl": round(realized_event_pnl, 6),
        "realized_day_pnl": round(realized_day_pnl, 6),
        "open_unrealized_pnl": round(open_unrealized_pnl, 6),
        "unrealized_profit_unlocks_risk": False,
        "realized_return": round(realized_return, 6),
        "ladder": ladder["name"],
        "max_base_portfolio_risk_usd": round(portfolio_value * ladder["max_base_risk"], 6),
        "max_realized_profit_risk_usd": round(realized_profit * ladder["max_profit_risk"], 6),
        "sleeve_budgets": {
            "low": round(realized_profit * ladder["max_profit_risk"] * low, 6),
            "medium": round(realized_profit * ladder["max_profit_risk"] * medium, 6),
            "high": round(realized_profit * ladder["max_profit_risk"] * high, 6),
        },
        "tail_risk_budget_usd": round(tail_budget, 6),
        "base_bankroll_protected": True,
        "trade_score": round(trade_score, 6),
        "blocked": scenario_level in {"D", "U"} or unresolved_inventory,
    }


def classify_virtual_dead_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    period = _safe_int(snapshot.get("period")) or 0
    clock_seconds = _safe_float(snapshot.get("clock_seconds_remaining")) or 0.0
    score_gap = abs(_safe_float(snapshot.get("score_gap")) or 0.0)
    feed_unsafe = bool(snapshot.get("feed_stale") or snapshot.get("unsafe_truth"))
    final_state = bool(snapshot.get("final") or snapshot.get("game_final"))
    bench_emptying = bool(snapshot.get("bench_emptying") or snapshot.get("starters_pulled"))
    garbage = bool(snapshot.get("garbage_time"))
    virtual_dead = final_state or feed_unsafe or garbage or bench_emptying or (period >= 4 and clock_seconds <= 90 and score_gap >= 10)
    reasons: list[str] = []
    if final_state:
        reasons.append("final")
    if feed_unsafe:
        reasons.append("unsafe_truth")
    if garbage:
        reasons.append("garbage_time")
    if bench_emptying:
        reasons.append("bench_emptying")
    if period >= 4 and clock_seconds <= 90 and score_gap >= 10:
        reasons.append("late_severe_deficit")
    return {
        "schema_version": "virtual_dead_state_v1",
        "virtual_dead": virtual_dead,
        "reasons": reasons,
        "loss_exit_allowed": virtual_dead,
        "must_compare_before_loss_exit": ["hold", "lower_target", "hedge", "add_down", "close"] if not virtual_dead else [],
    }


def evaluate_wnba_minimal_live_readiness(evidence: dict[str, Any]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    linked_games = _safe_int(evidence.get("linked_games"))
    passive_ticks = _safe_int(evidence.get("passive_orderbook_ticks"))
    fillability_samples = _safe_int(evidence.get("fillability_samples"))
    safety_controls_ready = bool(evidence.get("core_safety_controls_ready"))
    direct_clob_clean = bool(evidence.get("direct_clob_clean"))
    if linked_games < _safe_int(evidence.get("min_linked_games") or 3):
        blockers.append({"reason": "insufficient_linked_games", "linked_games": linked_games})
    if passive_ticks < _safe_int(evidence.get("min_passive_orderbook_ticks") or 100):
        blockers.append({"reason": "insufficient_passive_orderbook_ticks", "passive_orderbook_ticks": passive_ticks})
    if fillability_samples < _safe_int(evidence.get("min_fillability_samples") or 20):
        blockers.append({"reason": "insufficient_fillability_samples", "fillability_samples": fillability_samples})
    if not safety_controls_ready:
        blockers.append({"reason": "core_safety_controls_not_ready"})
    if not direct_clob_clean:
        blockers.append({"reason": "direct_clob_not_clean"})
    return {
        "schema_version": "wnba_minimal_live_readiness_v1",
        "status": "ready_for_minimum_size_operator_review" if not blockers else "blocked",
        "live_money_allowed": False,
        "minimum_size_test_requires_operator_approval": True,
        "blockers": blockers,
        "shared_basketball_contract": True,
        "league_specific_calibration_required": True,
    }


def _classifier_result(
    scenario: str,
    labels: list[str],
    confidence: float,
    evidence: list[dict[str, Any]],
    impact: str,
    invalidation: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "basketball_regime_classifier_v1",
        "scenario_level": scenario,
        "regime_labels": sorted(set(labels)),
        "confidence": round(confidence, 3),
        "evidence": evidence,
        "trading_impact": impact,
        "invalidation_conditions": invalidation,
    }


def _pbp_event_type(description: str) -> str:
    if "turnover" in description:
        return "turnover"
    if "foul" in description:
        return "foul"
    if "substitution" in description or "enters" in description:
        return "substitution"
    if "timeout" in description:
        return "timeout"
    if "3-pt" in description or "three" in description or "jump shot" in description or "layup" in description:
        return "shot"
    return "other"


def _crossed_expectation(
    previous_underdog_price: float | None,
    underdog_price: float | None,
    previous_favorite_price: float | None,
    favorite_price: float | None,
) -> bool:
    if previous_underdog_price is not None and underdog_price is not None:
        if previous_underdog_price < 0.5 <= underdog_price:
            return True
    if previous_favorite_price is not None and favorite_price is not None:
        if previous_favorite_price >= 0.5 > favorite_price:
            return True
    return False


def _slow_underdog_descent(snapshot: dict[str, Any], price: float | None, previous_price: float | None) -> bool:
    has_spikes = bool(snapshot.get("underdog_spikes") or snapshot.get("rebound_spikes"))
    time_live = (_safe_float(snapshot.get("clock_seconds_remaining")) or 0.0) >= 120
    if price is None:
        return False
    downtrend = previous_price is not None and previous_price > price
    return downtrend and has_spikes and time_live and 0.08 <= price <= 0.35


def _favorite_floor_rebound(snapshot: dict[str, Any], price: float | None, previous_price: float | None) -> bool:
    floor = _safe_float(snapshot.get("favorite_floor_price")) or 0.62
    fundamentals_intact = bool(snapshot.get("favorite_fundamentals_intact", True))
    if price is None:
        return False
    return fundamentals_intact and price <= floor and (previous_price is None or previous_price > price)


def _sleeve(sleeve_id: str, family: str, risk: str, live_authority: bool, *, state: str | None = None) -> dict[str, Any]:
    return {
        "sleeve_id": sleeve_id,
        "family": family,
        "risk_sleeve": risk,
        "state": state or ("candidate" if live_authority else "shadow_only"),
        "live_authority_required": not live_authority,
    }


def _sleeve_edges(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    ids = [item["sleeve_id"] for item in candidates]
    return [{"from": ids[index], "to": ids[index + 1], "relation": "portfolio_cap_shared"} for index in range(len(ids) - 1)]


def _risk_ladder(realized_return: float) -> dict[str, Any]:
    if realized_return < 0.20:
        return {"name": "0-20", "max_base_risk": 0.03, "max_profit_risk": 0.10, "split": (0.85, 0.15, 0.0)}
    if realized_return < 0.50:
        return {"name": "20-50", "max_base_risk": 0.05, "max_profit_risk": 0.25, "split": (0.70, 0.25, 0.05)}
    if realized_return < 1.00:
        return {"name": "50-100", "max_base_risk": 0.08, "max_profit_risk": 0.40, "split": (0.50, 0.35, 0.15)}
    return {"name": ">100", "max_base_risk": 0.10, "max_profit_risk": 0.50, "split": (0.40, 0.35, 0.25)}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "build_price_impact_windows",
    "build_profit_ratcheted_risk_state",
    "classify_basketball_regime",
    "classify_virtual_dead_state",
    "evaluate_wnba_minimal_live_readiness",
    "generate_strategy_sleeve_candidates",
    "tag_basketball_pbp_events",
]
