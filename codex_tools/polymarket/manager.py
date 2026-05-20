"""Portfolio-manager action planning for direct Polymarket evidence.

This module is intentionally non-executing. It converts direct account truth,
frontend catalog observations, and winning-profile notes into a required
portfolio-manager action selection. The selected action can be routed to an
approved order-management path later, but this module never prepares or submits
orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Any

from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT
from codex_tools.polymarket.grid_service import _classify_position

PORTFOLIO_MANAGER_ACTION_SCHEMA_VERSION = "polymarket_portfolio_manager_action_plan_v1"

_POSITIVE_TREND_WORDS = {"up", "uptrend", "positive", "bullish", "rising", "continuation"}
_NEGATIVE_TREND_WORDS = {"down", "downtrend", "negative", "bearish", "falling", "breaking_down"}
_SIDEWAYS_TREND_WORDS = {"sideways", "range", "rangebound", "oscillating", "band", "flat"}
_BROKEN_THESIS_WORDS = {"broken", "invalidated", "falsified", "failed", "abandoned", "thesis_broken"}
_CATALYST_CATEGORIES = {
    "geopolitics",
    "economics",
    "finance",
    "politics",
    "elections",
    "ai",
    "tech",
    "culture",
    "crypto",
    "sports",
    "basketball",
}
_LOW_PRICED_CATALYST_KEYWORDS = {
    "ai model",
    "best ai",
    "openai",
    "anthropic",
    "google",
    "gemini",
    "artificial intelligence",
    "benchmark",
    "model at the end",
}
_LOW_PRICED_CATALYST_PRICE_CEILING = Decimal("0.08")


@dataclass(frozen=True)
class PositionManagementDecision:
    market_slug: str
    title: str
    token_id: str
    side: str
    size: str
    average_price: str | None
    current_price: str | None
    pnl_percent: str | None
    trend_direction: str
    oscillation_band_percent: str | None
    category: str
    thesis_state: str
    target_state: str
    recommended_action: str
    proposed_micro_action: dict[str, Any]
    rationale: str


@dataclass(frozen=True)
class MarketCatalogCandidate:
    source: str
    category: str
    title: str
    market_slug: str
    outcome: str
    price: str | None
    volume: str | None
    trend_direction: str
    score: int
    proposed_micro_action: dict[str, Any]
    rationale: str


@dataclass(frozen=True)
class ProfileStudyCandidate:
    profile: str
    source_url: str | None
    category: str | None
    market_hint: str | None
    insight: str
    matched_catalog_title: str | None


@dataclass(frozen=True)
class PortfolioManagerActionPlan:
    schema_version: str
    status: str
    generated_at_utc: str
    action_required_each_run: bool
    action_requirement_satisfied: bool
    selected_action_type: str
    selected_action: dict[str, Any] | None
    existing_position_decision_count: int
    existing_position_decisions: list[dict[str, Any]]
    market_candidate_count: int
    market_candidates: list[dict[str, Any]]
    profile_candidate_count: int
    profile_candidates: list[dict[str, Any]]
    repeat_suppression: dict[str, Any]
    micro_trade_policy: dict[str, Any]
    execution_gate_status: str
    execution_blockers: list[str]
    browser_research_required: bool
    browser_research_pages: list[str]
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


def build_portfolio_manager_action_plan(
    direct_truth_snapshot: dict[str, Any],
    *,
    frontend_catalog_snapshot: dict[str, Any] | None = None,
    profile_studies: list[dict[str, Any]] | None = None,
    recent_action_history: dict[str, Any] | list[dict[str, Any]] | None = None,
    now_utc: datetime | str | None = None,
    require_action_each_run: bool = True,
    target_notional_usd: Decimal | float | str = Decimal("1"),
    max_initial_shares: Decimal | float | str = Decimal("5"),
    max_initial_notional_usd: Decimal | float | str = Decimal("5"),
    oscillation_grid_threshold_percent: Decimal | float | str = Decimal("3"),
) -> PortfolioManagerActionPlan:
    """Build the required active-management action plan for one pass."""

    generated_at = _coerce_utc(now_utc).isoformat().replace("+00:00", "Z")
    target_notional = _decimal(target_notional_usd) or Decimal("1")
    max_shares = _decimal(max_initial_shares) or Decimal("5")
    max_notional = _decimal(max_initial_notional_usd) or Decimal("5")
    oscillation_threshold = _decimal(oscillation_grid_threshold_percent) or Decimal("3")
    recent_fingerprints = _recent_action_fingerprints(recent_action_history)

    open_orders = list(direct_truth_snapshot.get("open_orders") or [])
    position_decisions = [
        _evaluate_position(
            position,
            open_orders=open_orders,
            target_notional_usd=target_notional,
            max_initial_shares=max_shares,
            max_initial_notional_usd=max_notional,
            oscillation_grid_threshold_percent=oscillation_threshold,
        )
        for position in direct_truth_snapshot.get("open_positions") or []
        if isinstance(position, dict)
    ]
    catalog_candidates = _build_catalog_candidates(
        frontend_catalog_snapshot or {},
        profile_studies=profile_studies or [],
        target_notional_usd=target_notional,
        max_initial_shares=max_shares,
        max_initial_notional_usd=max_notional,
    )
    profile_candidates = _build_profile_candidates(profile_studies or [], catalog_candidates)

    selected_type = "none"
    selected: dict[str, Any] | None = None

    existing_action = _select_existing_position_action(position_decisions, recent_fingerprints=recent_fingerprints)
    if existing_action is not None:
        selected_type = "manage_existing_position"
        selected = asdict(existing_action)
    elif catalog_candidates:
        catalog_action = _select_catalog_candidate(catalog_candidates, recent_fingerprints=recent_fingerprints)
        if catalog_action is not None:
            selected_type = "open_new_event_micro_position"
            selected = asdict(catalog_action)
        else:
            existing_hold = _select_existing_position_hold(position_decisions, recent_fingerprints=recent_fingerprints)
            if existing_hold is not None:
                selected_type = "manage_existing_position"
                selected = asdict(existing_hold)
    else:
        existing_hold = _select_existing_position_hold(position_decisions, recent_fingerprints=recent_fingerprints)
        if existing_hold is not None:
            selected_type = "manage_existing_position"
            selected = asdict(existing_hold)

    action_satisfied = selected is not None
    execution_blockers = [
        "execution still requires an approved Janus portfolio order-management or independent Polymarket path",
        "fresh direct CLOB/orderbook truth must be rechecked immediately before any non-dry-run call",
        "ledger/idempotency, risk-budget, target/stop/rebuy, kill-switch, approval, and reconciliation gates must pass",
    ]
    status = "required_action_selected_execution_gated" if action_satisfied else "blocked_no_required_action_candidate"
    if not require_action_each_run:
        status = "optional_action_plan_built" if action_satisfied else "no_candidate"

    return PortfolioManagerActionPlan(
        schema_version=PORTFOLIO_MANAGER_ACTION_SCHEMA_VERSION,
        status=status,
        generated_at_utc=generated_at,
        action_required_each_run=require_action_each_run,
        action_requirement_satisfied=action_satisfied,
        selected_action_type=selected_type,
        selected_action=selected,
        existing_position_decision_count=len(position_decisions),
        existing_position_decisions=[asdict(decision) for decision in position_decisions],
        market_candidate_count=len(catalog_candidates),
        market_candidates=[asdict(candidate) for candidate in catalog_candidates],
        profile_candidate_count=len(profile_candidates),
        profile_candidates=[asdict(candidate) for candidate in profile_candidates],
        repeat_suppression={
            "enabled": bool(recent_fingerprints),
            "recent_action_fingerprint_count": len(recent_fingerprints),
            "suppressed_existing_position_decision_count": sum(
                1 for decision in position_decisions if _is_recent_existing_position(decision, recent_fingerprints)
            ),
            "suppressed_market_candidate_count": sum(
                1 for candidate in catalog_candidates if _catalog_candidate_fingerprint(candidate) in recent_fingerprints
            ),
            "rule": "Do not repeat the same selected action when token/market/action/price/size evidence is unchanged; select the next safe candidate instead.",
        },
        micro_trade_policy={
            "target_notional_usd": str(target_notional),
            "max_initial_shares": str(max_shares),
            "max_initial_notional_usd": str(max_notional),
            "order_type": "limit_only",
            "rule": "Initial validation trades should target about $1, never exceed 5 shares or $5 notional without a new explicit policy.",
        },
        execution_gate_status="gated_no_order_preparation",
        execution_blockers=execution_blockers,
        browser_research_required=True,
        browser_research_pages=[
            "https://polymarket.com/",
            "https://polymarket.com/breaking",
            "https://polymarket.com/new",
            "https://polymarket.com/sports?live=true",
            "https://polymarket.com/politics",
            "https://polymarket.com/finance",
            "https://polymarket.com/geopolitics",
            "https://polymarket.com/economy",
        ],
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


def _evaluate_position(
    position: dict[str, Any],
    *,
    open_orders: list[dict[str, Any]],
    target_notional_usd: Decimal,
    max_initial_shares: Decimal,
    max_initial_notional_usd: Decimal,
    oscillation_grid_threshold_percent: Decimal,
) -> PositionManagementDecision:
    token_id = _token_id(position)
    title = _title(position)
    market_slug = _market_slug(position)
    side = _first_text(position, ("side", "outcome", "outcome_name")) or "position"
    category, covered = _classify_position(position)
    if covered:
        category = "covered_basketball"
    average_price = _first_decimal(position, ("average_price", "avg_price", "avgPrice", "entry_price"))
    current_price = _current_price(position)
    pnl_percent = _pnl_percent(position)
    trend = _trend_direction(position)
    oscillation = _oscillation_band_percent(position)
    target_state = _target_state(position, open_orders=open_orders, token_id=token_id)
    thesis_state = (
        "covered_basketball_defer_to_janus"
        if covered
        else _thesis_state(
            position,
            title=title,
            market_slug=market_slug,
            category=category,
            current_price=current_price,
        )
    )
    action, rationale = _recommend_position_action(
        thesis_state=thesis_state,
        pnl_percent=pnl_percent,
        trend_direction=trend,
        oscillation_band_percent=oscillation,
        target_state=target_state,
        oscillation_grid_threshold_percent=oscillation_grid_threshold_percent,
    )

    return PositionManagementDecision(
        market_slug=market_slug,
        title=title,
        token_id=token_id,
        side=side,
        size=str(_first_decimal(position, ("size", "quantity", "shares", "balance")) or ""),
        average_price=str(average_price) if average_price is not None else None,
        current_price=str(current_price) if current_price is not None else None,
        pnl_percent=str(pnl_percent) if pnl_percent is not None else None,
        trend_direction=trend,
        oscillation_band_percent=str(oscillation) if oscillation is not None else None,
        category=category,
        thesis_state=thesis_state,
        target_state=target_state,
        recommended_action=action,
        proposed_micro_action=_position_micro_action(
            action=action,
            position=position,
            current_price=current_price,
            target_notional_usd=target_notional_usd,
            max_initial_shares=max_initial_shares,
            max_initial_notional_usd=max_initial_notional_usd,
        ),
        rationale=rationale,
    )


def _recommend_position_action(
    *,
    thesis_state: str,
    pnl_percent: Decimal | None,
    trend_direction: str,
    oscillation_band_percent: Decimal | None,
    target_state: str,
    oscillation_grid_threshold_percent: Decimal,
) -> tuple[str, str]:
    if thesis_state == "covered_basketball_defer_to_janus":
        return (
            "defer_covered_basketball_to_janus",
            "Covered NBA/WNBA inventory belongs to the Janus trading Python system, not the Codex global portfolio lane.",
        )
    if thesis_state == "low_priced_catalyst_hold":
        return (
            "hold_low_priced_catalyst_option",
            "Position is a low-priced catalyst option with no direct thesis falsification; do not force a near-price target sell.",
        )

    if pnl_percent is None:
        if target_state in {"target_missing", "target_stale"}:
            return "set_or_refresh_target", "Existing position lacks usable PnL but needs target maintenance."
        return "hold_and_recheck", "Position lacks enough price context for a directional management action."

    positive = pnl_percent > 0
    negative = pnl_percent < 0
    heavy_oscillation = oscillation_band_percent is not None and oscillation_band_percent >= oscillation_grid_threshold_percent

    if positive and heavy_oscillation:
        return "close_win_and_convert_to_grid", "Positive position is oscillating inside a band; secure win and consider a 1c grid."
    if negative and trend_direction in _POSITIVE_TREND_WORDS:
        return "hold_negative_positive_thesis", "Position is red but thesis trend remains positive; wait for recovery."
    if positive and trend_direction in _POSITIVE_TREND_WORDS:
        return "hold_positive_uptrend", "Position is green and trend remains positive; keep monitoring."
    if positive and trend_direction in _NEGATIVE_TREND_WORDS:
        return "close_positive_downtrend", "Position is green but trend has turned down; secure gains."
    if negative and trend_direction in _NEGATIVE_TREND_WORDS:
        return "sell_loss_and_rebuy_lower_or_close", "Position is red in a downtrend; close or rebuy lower only if thesis remains strong."
    if target_state in {"target_missing", "target_stale"}:
        return "set_or_refresh_target", "Position needs target maintenance before it can be managed actively."
    return "hold_and_recheck", "No stronger active-management rule was triggered."


def _position_micro_action(
    *,
    action: str,
    position: dict[str, Any],
    current_price: Decimal | None,
    target_notional_usd: Decimal,
    max_initial_shares: Decimal,
    max_initial_notional_usd: Decimal,
) -> dict[str, Any]:
    if current_price is None:
        return {"action": "review_only", "reason": "current_price_missing"}
    if action in {"close_win_and_convert_to_grid", "close_positive_downtrend", "sell_loss_and_rebuy_lower_or_close"}:
        size = _first_decimal(position, ("size", "quantity", "shares", "balance")) or Decimal("0")
        return {
            "action": "sell_or_target_existing_position_via_approved_path",
            "limit_price": str(current_price.quantize(Decimal("0.01"))),
            "max_size": str(min(size, max_initial_shares)),
            "max_notional_usd": str(max_initial_notional_usd),
            "order_preparation_allowed": False,
        }
    if action == "set_or_refresh_target":
        average_price = _first_decimal(position, ("average_price", "avg_price", "avgPrice", "entry_price"))
        target_price, target_policy = _target_maintenance_limit_price(
            current_price=current_price,
            average_price=average_price,
        )
        return {
            "action": "set_one_cent_target_via_approved_path",
            "limit_price": str(target_price),
            "max_size": str(max_initial_shares),
            "target_notional_usd": str(target_notional_usd),
            "target_policy": target_policy,
            "average_price": str(average_price) if average_price is not None else None,
            "order_preparation_allowed": False,
        }
    if action == "hold_low_priced_catalyst_option":
        return {
            "action": "hold_catalyst_option_no_near_target",
            "target_policy": "do_not_place_mechanical_one_cent_sell_target",
            "optional_review": "Consider only a deliberate high-conviction partial target after fresh catalyst/news and orderbook review.",
            "falsification_trigger": "Reclassify if direct evidence shows thesis invalidation, liquidity failure, resolved-market state, or stronger opportunity-cost reason.",
            "order_preparation_allowed": False,
        }
    if action == "defer_covered_basketball_to_janus":
        return {
            "action": "defer_to_janus_covered_market_inventory",
            "order_preparation_allowed": False,
            "reason": "covered_basketball_scope",
        }
    return {"action": "monitor_existing_position", "order_preparation_allowed": False}


def _build_catalog_candidates(
    catalog: dict[str, Any],
    *,
    profile_studies: list[dict[str, Any]],
    target_notional_usd: Decimal,
    max_initial_shares: Decimal,
    max_initial_notional_usd: Decimal,
) -> list[MarketCatalogCandidate]:
    rows = _catalog_rows(catalog)
    profile_terms = _profile_terms(profile_studies)
    candidates: list[MarketCatalogCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _first_decimal(row, ("price", "current_price", "probability", "yes_price", "best_ask"))
        category = _category(row)
        source = _first_text(row, ("source", "catalog_source", "section")) or "frontend_catalog"
        title = _title(row)
        market_slug = _market_slug(row)
        outcome = _first_text(row, ("outcome", "side", "name")) or "Yes"
        trend = _trend_direction(row)
        score = _catalog_score(
            row,
            category=category,
            price=price,
            source=source,
            profile_terms=profile_terms,
        )
        if score <= 0:
            continue
        candidates.append(
            MarketCatalogCandidate(
                source=source,
                category=category,
                title=title,
                market_slug=market_slug,
                outcome=outcome,
                price=str(price) if price is not None else None,
                volume=str(_first_decimal(row, ("volume", "volume24hr", "volume_24h", "liquidity")) or ""),
                trend_direction=trend,
                score=score,
                proposed_micro_action=_catalog_micro_action(
                    price=price,
                    target_notional_usd=target_notional_usd,
                    max_initial_shares=max_initial_shares,
                    max_initial_notional_usd=max_initial_notional_usd,
                ),
                rationale="Frontend catalog candidate selected for bounded micro-position review.",
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _catalog_score(
    row: dict[str, Any],
    *,
    category: str,
    price: Decimal | None,
    source: str,
    profile_terms: set[str],
) -> int:
    score = 0
    source_l = source.lower()
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            _title(row),
            _market_slug(row),
            category,
            row.get("description"),
        )
    )
    if "live" in source_l:
        score += 4
    if "trending" in source_l or "breaking" in source_l:
        score += 3
    if "new" in source_l:
        score += 1
    if category in _CATALYST_CATEGORIES:
        score += 2
    if price is not None and Decimal("0.05") <= price <= Decimal("0.40"):
        score += 2
    if _trend_direction(row) in _POSITIVE_TREND_WORDS:
        score += 1
    if profile_terms and any(term in haystack for term in profile_terms):
        score += 2
    return score


def _catalog_micro_action(
    *,
    price: Decimal | None,
    target_notional_usd: Decimal,
    max_initial_shares: Decimal,
    max_initial_notional_usd: Decimal,
) -> dict[str, Any]:
    if price is None or price <= 0:
        return {"action": "research_only", "reason": "price_missing"}
    shares_for_target = (target_notional_usd / price).quantize(Decimal("0.01"), rounding=ROUND_UP)
    size = min(max_initial_shares, shares_for_target)
    notional = (size * price).quantize(Decimal("0.01"), rounding=ROUND_UP)
    if notional > max_initial_notional_usd:
        return {
            "action": "blocked_micro_size_exceeds_cap",
            "limit_price": str(price),
            "size": str(size),
            "notional_usd": str(notional),
            "max_initial_notional_usd": str(max_initial_notional_usd),
            "order_preparation_allowed": False,
        }
    return {
        "action": "open_new_micro_position_via_approved_path",
        "side": "BUY",
        "order_type": "limit",
        "limit_price": str(price.quantize(Decimal("0.01"))),
        "size": str(size),
        "notional_usd": str(notional),
        "order_preparation_allowed": False,
    }


def _build_profile_candidates(
    profile_studies: list[dict[str, Any]],
    catalog_candidates: list[MarketCatalogCandidate],
) -> list[ProfileStudyCandidate]:
    results: list[ProfileStudyCandidate] = []
    for profile in profile_studies:
        if not isinstance(profile, dict):
            continue
        category = _first_text(profile, ("category", "primary_category", "type"))
        market_hint = _first_text(profile, ("market_hint", "market", "focus", "tag"))
        matched = _match_profile_to_catalog(category, market_hint, catalog_candidates)
        results.append(
            ProfileStudyCandidate(
                profile=_first_text(profile, ("profile", "name", "handle")) or "unknown",
                source_url=_first_text(profile, ("source_url", "url")) or None,
                category=category or None,
                market_hint=market_hint or None,
                insight=_first_text(profile, ("insight", "lesson", "pattern"))
                or "Review profile returns and trade history for transferable market-structure lessons.",
                matched_catalog_title=matched,
            )
        )
    return results


def _match_profile_to_catalog(
    category: str,
    market_hint: str,
    catalog_candidates: list[MarketCatalogCandidate],
) -> str | None:
    terms = {item.lower() for item in (category, market_hint) if item}
    for candidate in catalog_candidates:
        haystack = " ".join((candidate.category, candidate.title, candidate.market_slug)).lower()
        if terms and any(term in haystack for term in terms):
            return candidate.title
    return None


def _select_existing_position_action(
    decisions: list[PositionManagementDecision],
    *,
    recent_fingerprints: set[str],
) -> PositionManagementDecision | None:
    priority = {
        "close_positive_downtrend": 100,
        "close_win_and_convert_to_grid": 95,
        "sell_loss_and_rebuy_lower_or_close": 90,
        "set_or_refresh_target": 80,
    }
    actionable = [
        decision
        for decision in decisions
        if priority.get(decision.recommended_action, 0) > 0
        and not _is_recent_existing_position(decision, recent_fingerprints)
    ]
    if not actionable:
        return None
    return sorted(actionable, key=lambda decision: priority.get(decision.recommended_action, 0), reverse=True)[0]


def _select_existing_position_hold(
    decisions: list[PositionManagementDecision],
    *,
    recent_fingerprints: set[str],
) -> PositionManagementDecision | None:
    priority = {
        "hold_low_priced_catalyst_option": 60,
        "hold_negative_positive_thesis": 50,
        "hold_positive_uptrend": 40,
        "hold_and_recheck": 10,
    }
    hold_decisions = [
        decision
        for decision in decisions
        if priority.get(decision.recommended_action, 0) > 0
        and not _is_recent_existing_position(decision, recent_fingerprints)
    ]
    if not hold_decisions:
        return None
    return sorted(hold_decisions, key=lambda decision: priority.get(decision.recommended_action, 0), reverse=True)[0]


def _select_catalog_candidate(
    candidates: list[MarketCatalogCandidate],
    *,
    recent_fingerprints: set[str],
) -> MarketCatalogCandidate | None:
    for candidate in candidates:
        if _catalog_candidate_fingerprint(candidate) not in recent_fingerprints:
            return candidate
    return None


def _target_maintenance_limit_price(
    *,
    current_price: Decimal,
    average_price: Decimal | None,
) -> tuple[Decimal, str]:
    if average_price is not None and current_price < average_price:
        return (
            _limit_price_cent_ceiling(min(average_price + Decimal("0.01"), Decimal("0.99"))),
            "recovery_target_one_cent_above_average_cost",
        )
    return (
        _limit_price_cent_ceiling(min(current_price + Decimal("0.01"), Decimal("0.99"))),
        "one_cent_above_current_mark",
    )


def _limit_price_cent_ceiling(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_UP)


def _recent_action_fingerprints(history: dict[str, Any] | list[dict[str, Any]] | None) -> set[str]:
    if history is None:
        return set()
    rows: list[dict[str, Any]] = []
    if isinstance(history, list):
        rows.extend(item for item in history if isinstance(item, dict))
    elif isinstance(history, dict):
        selected = history.get("selected_action")
        if isinstance(selected, dict):
            rows.append(selected)
        for key in ("recent_actions", "selected_actions", "actions"):
            value = history.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    fingerprints: set[str] = set()
    for row in rows:
        fingerprint = _action_mapping_fingerprint(row)
        if fingerprint:
            fingerprints.add(fingerprint)
        token_fingerprint = _action_mapping_token_fingerprint(row)
        if token_fingerprint:
            fingerprints.add(token_fingerprint)
    return fingerprints


def _action_mapping_fingerprint(action: dict[str, Any]) -> str | None:
    if any(action.get(key) for key in ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId")):
        token_id = _token_id(action)
        return "|".join(
            (
                "existing",
                token_id,
                _market_slug(action),
                _action_mapping_recommended_action(action),
                _action_mapping_target_state(action),
                _action_mapping_current_price(action),
                _first_text(action, ("average_price", "avg_price", "avgPrice", "entry_price")),
                _first_text(action, ("size", "quantity", "shares", "balance")),
            )
        )
    if action.get("market_slug") or action.get("title"):
        return "|".join(
            (
                "catalog",
                _market_slug(action),
                _first_text(action, ("outcome", "side", "name")),
                _first_text(action, ("price", "current_price", "probability", "yes_price", "best_ask")),
            )
        )
    return None


def _action_mapping_token_fingerprint(action: dict[str, Any]) -> str | None:
    if not any(action.get(key) for key in ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId")):
        return None
    return "|".join(
        (
            "existing_token",
            _token_id(action),
            _action_mapping_recommended_action(action),
            _action_mapping_target_state(action),
            _action_mapping_current_price(action),
            _first_text(action, ("average_price", "avg_price", "avgPrice", "entry_price")),
            _first_text(action, ("size", "quantity", "shares", "balance")),
        )
    )


def _action_mapping_target_state(action: dict[str, Any]) -> str:
    explicit = _first_text(action, ("target_state", "target_status"))
    if explicit:
        return explicit
    requested_order = action.get("requested_order")
    if isinstance(requested_order, dict) and str(requested_order.get("side") or "").lower() == "sell":
        return "target_missing"
    return ""


def _action_mapping_recommended_action(action: dict[str, Any]) -> str:
    explicit = _first_text(action, ("recommended_action", "action"))
    if explicit:
        return explicit
    requested_order = action.get("requested_order")
    requested_side = ""
    if isinstance(requested_order, dict):
        requested_side = str(requested_order.get("side") or "").lower()
    if _first_text(action, ("type",)) == "manage_existing_position" and requested_side == "sell":
        return "set_or_refresh_target"
    return _first_text(action, ("type",))


def _action_mapping_current_price(action: dict[str, Any]) -> str:
    explicit = _first_text(action, ("current_price", "cur_price", "curPrice", "market_price", "price"))
    if explicit:
        return explicit
    current_value = _decimal(action.get("current_value"))
    size = _decimal(action.get("size"))
    if current_value is not None and size is not None and size != 0:
        return str(current_value / size)
    return ""


def _existing_position_fingerprint(decision: PositionManagementDecision) -> str:
    return "|".join(
        (
            "existing",
            decision.token_id,
            decision.market_slug,
            decision.recommended_action,
            decision.target_state,
            decision.current_price or "",
            decision.average_price or "",
            decision.size,
        )
    )


def _existing_position_token_fingerprint(decision: PositionManagementDecision) -> str:
    return "|".join(
        (
            "existing_token",
            decision.token_id,
            decision.recommended_action,
            decision.target_state,
            decision.current_price or "",
            decision.average_price or "",
            decision.size,
        )
    )


def _is_recent_existing_position(decision: PositionManagementDecision, recent_fingerprints: set[str]) -> bool:
    return (
        _existing_position_fingerprint(decision) in recent_fingerprints
        or _existing_position_token_fingerprint(decision) in recent_fingerprints
    )


def _catalog_candidate_fingerprint(candidate: MarketCatalogCandidate) -> str:
    return "|".join(("catalog", candidate.market_slug, candidate.outcome, candidate.price or ""))


def _catalog_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("live_events", "trending_events", "breaking_events", "new_events", "markets", "items", "rows"):
        value = catalog.get(key)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    normalized = dict(row)
                    normalized.setdefault("source", key)
                    rows.append(normalized)
    return rows


def _profile_terms(profile_studies: list[dict[str, Any]]) -> set[str]:
    terms: set[str] = set()
    for profile in profile_studies:
        for key in ("category", "primary_category", "market_hint", "market", "focus", "tag"):
            value = str(profile.get(key) or "").strip().lower()
            if value:
                terms.add(value)
    return terms


def _thesis_state(
    position: dict[str, Any],
    *,
    title: str,
    market_slug: str,
    category: str,
    current_price: Decimal | None,
) -> str:
    explicit = _first_text(
        position,
        (
            "thesis_state",
            "thesis_status",
            "hypothesis_state",
            "hypothesis_status",
            "operator_thesis",
        ),
    )
    normalized = explicit.lower().replace("-", "_").replace(" ", "_")
    if normalized:
        if normalized in _BROKEN_THESIS_WORDS:
            return "thesis_broken"
        if "catalyst" in normalized and "hold" in normalized:
            return "low_priced_catalyst_hold"
        return normalized
    if _is_low_priced_catalyst_position(
        position,
        title=title,
        market_slug=market_slug,
        category=category,
        current_price=current_price,
    ):
        return "low_priced_catalyst_hold"
    return "unknown"


def _is_low_priced_catalyst_position(
    position: dict[str, Any],
    *,
    title: str,
    market_slug: str,
    category: str,
    current_price: Decimal | None,
) -> bool:
    if current_price is None or current_price > _LOW_PRICED_CATALYST_PRICE_CEILING:
        return False
    raw_tags = position.get("tags") or position.get("categories") or position.get("markets") or ""
    haystack = " ".join(
        (
            title,
            market_slug,
            category.replace("_", " "),
            str(raw_tags),
            _first_text(position, ("description", "notes", "resolution_source")),
        )
    ).lower()
    category_terms = {category.lower(), category.lower().replace("_", " ")}
    has_ai_category = bool(category_terms & {"ai", "tech", "ai models", "ai_models", "technology"})
    return has_ai_category or any(keyword in haystack for keyword in _LOW_PRICED_CATALYST_KEYWORDS)


def _target_state(position: dict[str, Any], *, open_orders: list[dict[str, Any]], token_id: str) -> str:
    explicit = _first_text(position, ("target_state", "target_status"))
    if explicit:
        return explicit
    if token_id and any(_token_id(order) == token_id for order in open_orders if isinstance(order, dict)):
        return "target_present"
    return "target_missing"


def _trend_direction(item: dict[str, Any]) -> str:
    raw = _first_text(item, ("trend_direction", "trend", "price_trend", "recent_trend", "direction")).lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in _POSITIVE_TREND_WORDS:
        return "uptrend"
    if normalized in _NEGATIVE_TREND_WORDS:
        return "downtrend"
    if normalized in _SIDEWAYS_TREND_WORDS:
        return "sideways"
    return normalized or "unknown"


def _oscillation_band_percent(item: dict[str, Any]) -> Decimal | None:
    return _first_decimal(
        item,
        (
            "oscillation_band_percent",
            "band_percent",
            "recent_range_percent",
            "absolute_move_percent",
            "volatility_percent",
        ),
    )


def _pnl_percent(position: dict[str, Any]) -> Decimal | None:
    explicit = _first_decimal(
        position,
        (
            "pnl_percent",
            "percent_pnl",
            "percentPnl",
            "totalPercentChange",
            "unrealized_pnl_percent",
            "percent_change",
        ),
    )
    if explicit is not None:
        return explicit
    average_price = _first_decimal(position, ("average_price", "avg_price", "avgPrice", "entry_price"))
    current_price = _current_price(position)
    if average_price is None or current_price is None or average_price == 0:
        return None
    return ((current_price - average_price) / average_price) * Decimal("100")


def _current_price(position: dict[str, Any]) -> Decimal | None:
    explicit = _first_decimal(position, ("current_price", "cur_price", "curPrice", "market_price", "price"))
    if explicit is not None:
        return explicit
    current_value = _first_decimal(position, ("current_value", "currentValue", "value"))
    size = _first_decimal(position, ("size", "quantity", "shares", "balance"))
    if current_value is None or size is None or size == 0:
        return None
    return current_value / size


def _category(row: dict[str, Any]) -> str:
    explicit = _first_text(row, ("category", "market_category", "tag", "domain")).lower()
    if explicit:
        return explicit.replace(" ", "_")
    classified, covered = _classify_position(row)
    return "covered_basketball" if covered else classified


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _first_decimal(item: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = _decimal(item.get(key))
        if value is not None:
            return value
    return None


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _token_id(item: dict[str, Any]) -> str:
    return _first_text(item, ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId"))


def _market_slug(item: dict[str, Any]) -> str:
    return _first_text(item, ("market_slug", "event_slug", "slug", "market"))


def _title(item: dict[str, Any]) -> str:
    return _first_text(item, ("title", "market_title", "event_title", "question", "name"))


def _coerce_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = [
    "PORTFOLIO_MANAGER_ACTION_SCHEMA_VERSION",
    "MarketCatalogCandidate",
    "PortfolioManagerActionPlan",
    "PositionManagementDecision",
    "ProfileStudyCandidate",
    "build_portfolio_manager_action_plan",
]
