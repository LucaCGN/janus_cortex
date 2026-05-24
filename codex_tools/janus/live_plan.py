"""Build event-specific Janus live StrategyPlanJSON payloads.

This module is intentionally order-path neutral: it imports/maps catalog truth,
builds StrategyPlanJSON, optionally submits the plan, and never calls execute.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.modules.agentic.contracts import ActiveStrategy, StrategyPlan
from codex_tools.janus.client import api_json, base_parser, exit_for_response
from codex_tools.janus.strategy import submit_strategy_plan

LIVE_PLAN_BOOTSTRAP_SCHEMA_VERSION = "live_strategy_plan_bootstrap_v1"
EVENT_IMPORT_URL_PATH = "/v1/events/import-url"
EVENT_MARKETS_PATH_TEMPLATE = "/v1/events/{event_id}/markets"
MARKET_OUTCOMES_PATH_TEMPLATE = "/v1/markets/{market_id}/outcomes"


def build_event_import_payload(
    *,
    event_url: str,
    league: str,
    stream_enabled: bool = False,
    stream_sample_count: int = 3,
    stream_sample_interval_sec: float = 1.0,
) -> dict[str, Any]:
    """Return an import-url payload tuned for live moneyline planning."""
    normalized_league = league.strip().lower()
    history_mode = "game_period" if normalized_league == "nba" else "rolling_recent"
    return {
        "url": event_url,
        "history_mode": history_mode,
        "history_market_selector": "moneyline",
        "history_interval": "1m",
        "history_fidelity": 10,
        "recent_lookback_days": 7,
        "allow_snapshot_fallback": True,
        "stream_enabled": stream_enabled,
        "stream_sample_count": stream_sample_count,
        "stream_sample_interval_sec": stream_sample_interval_sec,
        "stream_max_outcomes": 30,
    }


def build_live_strategy_plan_from_catalog(
    *,
    event_id: str,
    event_url: str,
    league: str,
    catalog_event: dict[str, Any],
    markets: list[dict[str, Any]],
    outcomes: list[dict[str, Any]] | None = None,
    mode: str = "selected_outcome",
    outcome_label: str | None = None,
    total_shares: float = 10.0,
    grid_leg_shares: float = 5.0,
    max_buy_notional_usd: float = 10.0,
    min_entry_price: float = 0.03,
    max_entry_price: float = 0.45,
    max_spread_cents: float = 2.0,
    max_scoreboard_age_seconds: float = 45.0,
    max_orderbook_age_seconds: float = 45.0,
    max_abs_score_gap: float = 18.0,
    min_clock_remaining_seconds: float = 60.0,
    valid_minutes: float = 960.0,
    generated_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build a validated executable StrategyPlan payload from catalog rows."""
    generated_at = generated_at_utc or datetime.now(timezone.utc)
    market = select_moneyline_market(markets)
    market_id = str(market["market_id"])
    selected_outcomes = select_plan_outcomes(outcomes or [], mode=mode, outcome_label=outcome_label)
    strategies: list[ActiveStrategy] = []

    if _normalize_mode(mode) == "responsive_both_sides":
        for item in selected_outcomes:
            strategies.append(
                _build_strategy(
                    event_id=event_id,
                    market_id=market_id,
                    outcome=item,
                    shares=grid_leg_shares,
                    budget_usd=max_buy_notional_usd,
                    family="price_stability_micro_grid",
                    sleeve_role="grid_scalp",
                    min_entry_price=min_entry_price,
                    max_entry_price=max_entry_price,
                    max_spread_cents=max_spread_cents,
                    max_scoreboard_age_seconds=max_scoreboard_age_seconds,
                    max_orderbook_age_seconds=max_orderbook_age_seconds,
                    max_abs_score_gap=max_abs_score_gap,
                    min_clock_remaining_seconds=min_clock_remaining_seconds,
                    target_cents=1.0,
                    target_return_fraction=0.10,
                    max_adverse_cents=3.0,
                )
            )
    else:
        selected = selected_outcomes[0]
        grid_shares = min(grid_leg_shares, total_shares)
        strategies.append(
            _build_strategy(
                event_id=event_id,
                market_id=market_id,
                outcome=selected,
                shares=grid_shares,
                budget_usd=max_buy_notional_usd,
                family="price_stability_micro_grid",
                sleeve_role="grid_scalp",
                min_entry_price=min_entry_price,
                max_entry_price=max_entry_price,
                max_spread_cents=max_spread_cents,
                max_scoreboard_age_seconds=max_scoreboard_age_seconds,
                max_orderbook_age_seconds=max_orderbook_age_seconds,
                max_abs_score_gap=max_abs_score_gap,
                min_clock_remaining_seconds=min_clock_remaining_seconds,
                target_cents=1.0,
                target_return_fraction=0.10,
                max_adverse_cents=3.0,
            )
        )
        core_shares = total_shares - grid_shares
        if core_shares >= 5.0:
            strategies.append(
                _build_strategy(
                    event_id=event_id,
                    market_id=market_id,
                    outcome=selected,
                    shares=core_shares,
                    budget_usd=max_buy_notional_usd,
                    family="core_hold_live_validation",
                    sleeve_role="core_hold",
                    min_entry_price=min_entry_price,
                    max_entry_price=max_entry_price,
                    max_spread_cents=max_spread_cents,
                    max_scoreboard_age_seconds=max_scoreboard_age_seconds,
                    max_orderbook_age_seconds=max_orderbook_age_seconds,
                    max_abs_score_gap=max_abs_score_gap,
                    min_clock_remaining_seconds=min_clock_remaining_seconds,
                    target_cents=3.0,
                    target_return_fraction=0.25,
                    max_adverse_cents=5.0,
                )
            )

    plan = StrategyPlan(
        event_id=event_id,
        market_id=market_id,
        generated_at_utc=generated_at,
        valid_until_utc=generated_at + timedelta(minutes=valid_minutes),
        plan_owner="system",
        context_summary={
            "schema_version": LIVE_PLAN_BOOTSTRAP_SCHEMA_VERSION,
            "league": league.strip().lower(),
            "event_url": event_url,
            "catalog_event_id": catalog_event.get("event_id"),
            "catalog_event_slug": catalog_event.get("canonical_slug"),
            "catalog_event_title": catalog_event.get("title"),
            "planning_mode": _normalize_mode(mode),
            "max_event_notional_usd": max_buy_notional_usd,
            "minimum_parallel_sleeve": "5-share grid plus 5-share core when total_shares >= 10",
            "execution_boundary": "plan-only; Janus evaluate/execute/live-worker gates own all orders",
        },
        active_strategies=strategies,
        trigger_conditions=[
            {
                "type": "fresh_live_state_required",
                "max_scoreboard_age_seconds": max_scoreboard_age_seconds,
                "max_orderbook_age_seconds": max_orderbook_age_seconds,
            },
            {
                "type": "event_budget_cap",
                "max_buy_notional_usd": max_buy_notional_usd,
                "grid_leg_shares": grid_leg_shares,
                "total_shares": total_shares,
            },
        ],
        explainability={
            "rationale": (
                "Controlled live test plan for NBA/WNBA covered-market execution: "
                "buy only when direct CLOB, live feed, worker, kill-switch, and risk gates are green."
            ),
            "strategy_roles": [item.sleeve_role for item in strategies],
        },
    )
    return plan.model_dump(mode="json")


def import_catalog_event(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    return api_json(api_root, "POST", EVENT_IMPORT_URL_PATH, payload)


def load_event_markets(api_root: str, catalog_event_id: str) -> list[dict[str, Any]]:
    response = api_json(api_root, "GET", EVENT_MARKETS_PATH_TEMPLATE.format(event_id=catalog_event_id))
    if response.get("ok") is False:
        raise RuntimeError(json.dumps(response, sort_keys=True))
    return [item for item in response.get("items", []) if isinstance(item, dict)]


def load_market_outcomes(api_root: str, market_id: str) -> list[dict[str, Any]]:
    response = api_json(api_root, "GET", MARKET_OUTCOMES_PATH_TEMPLATE.format(market_id=market_id))
    if response.get("ok") is False:
        raise RuntimeError(json.dumps(response, sort_keys=True))
    return [item for item in response.get("items", []) if isinstance(item, dict)]


def build_live_strategy_plan_with_api(args: Namespace) -> dict[str, Any]:
    import_payload = build_event_import_payload(
        event_url=args.event_url,
        league=args.league,
        stream_enabled=args.stream_enabled,
        stream_sample_count=args.stream_sample_count,
        stream_sample_interval_sec=args.stream_sample_interval_sec,
    )
    imported = import_catalog_event(args.api_root, import_payload)
    if imported.get("ok") is False:
        return imported
    catalog_event = imported.get("event") if isinstance(imported.get("event"), dict) else {}
    catalog_event_id = str(catalog_event.get("event_id") or "").strip()
    if not catalog_event_id:
        return {"ok": False, "reason": "catalog_event_missing_after_import", "import": imported}
    markets = load_event_markets(args.api_root, catalog_event_id)
    market = select_moneyline_market(markets)
    outcomes = load_market_outcomes(args.api_root, str(market["market_id"]))
    plan = build_live_strategy_plan_from_catalog(
        event_id=args.event_id,
        event_url=args.event_url,
        league=args.league,
        catalog_event=catalog_event,
        markets=markets,
        outcomes=outcomes,
        mode=args.mode,
        outcome_label=args.outcome_label,
        total_shares=args.total_shares,
        grid_leg_shares=args.grid_leg_shares,
        max_buy_notional_usd=args.max_buy_notional_usd,
        min_entry_price=args.min_entry_price,
        max_entry_price=args.max_entry_price,
        max_spread_cents=args.max_spread_cents,
        max_scoreboard_age_seconds=args.max_scoreboard_age_seconds,
        max_orderbook_age_seconds=args.max_orderbook_age_seconds,
        max_abs_score_gap=args.max_abs_score_gap,
        min_clock_remaining_seconds=args.min_clock_remaining_seconds,
        valid_minutes=args.valid_minutes,
    )
    if args.output_path:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    submission = None
    if args.submit:
        submission = submit_strategy_plan(args.api_root, args.event_id, plan)
    return {
        "ok": True,
        "schema_version": LIVE_PLAN_BOOTSTRAP_SCHEMA_VERSION,
        "event_id": args.event_id,
        "catalog_event_id": catalog_event_id,
        "market_id": plan["market_id"],
        "strategy_count": len(plan.get("active_strategies") or []),
        "output_path": args.output_path,
        "submitted": bool(args.submit),
        "submission": submission,
        "plan": plan,
    }


def select_moneyline_market(markets: list[dict[str, Any]]) -> dict[str, Any]:
    if not markets:
        raise ValueError("catalog event has no markets")
    for market in markets:
        market_type = str(market.get("market_type") or "").strip().lower()
        question = str(market.get("question") or "").strip().lower()
        metadata = market.get("metadata_json") if isinstance(market.get("metadata_json"), dict) else {}
        if market_type == "moneyline" or "moneyline" in market_type:
            return market
        if metadata.get("history_market_selector") == "moneyline":
            return market
        if " vs " in question or " v. " in question:
            return market
    return markets[0]


def select_plan_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    mode: str,
    outcome_label: str | None,
) -> list[dict[str, Any]]:
    if not outcomes:
        raise ValueError("moneyline market has no outcomes")
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "responsive_both_sides":
        return outcomes[:2]
    if not outcome_label:
        raise ValueError("outcome_label is required unless mode=responsive_both_sides")
    wanted = _normalize_label(outcome_label)
    for outcome in outcomes:
        label = _normalize_label(outcome.get("outcome_label"))
        if wanted == label or wanted in label or label in wanted:
            return [outcome]
    raise ValueError(f"outcome_label not found: {outcome_label}")


def build_live_plan_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-id", required=True, help="Janus runtime event id, not catalog UUID.")
    parser.add_argument("--event-url", required=True, help="Polymarket event URL.")
    parser.add_argument("--league", required=True, choices=["nba", "wnba"])
    parser.add_argument("--mode", default="selected_outcome", choices=["selected_outcome", "responsive_both_sides"])
    parser.add_argument("--outcome-label", default=None)
    parser.add_argument("--total-shares", type=float, default=10.0)
    parser.add_argument("--grid-leg-shares", type=float, default=5.0)
    parser.add_argument("--max-buy-notional-usd", type=float, default=10.0)
    parser.add_argument("--min-entry-price", type=float, default=0.03)
    parser.add_argument("--max-entry-price", type=float, default=0.45)
    parser.add_argument("--max-spread-cents", type=float, default=2.0)
    parser.add_argument("--max-scoreboard-age-seconds", type=float, default=45.0)
    parser.add_argument("--max-orderbook-age-seconds", type=float, default=45.0)
    parser.add_argument("--max-abs-score-gap", type=float, default=18.0)
    parser.add_argument("--min-clock-remaining-seconds", type=float, default=60.0)
    parser.add_argument("--valid-minutes", type=float, default=960.0)
    parser.add_argument("--stream-enabled", action="store_true")
    parser.add_argument("--stream-sample-count", type=int, default=3)
    parser.add_argument("--stream-sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--output-path", default=None)
    parser.add_argument("--submit", action="store_true")
    return parser


def main_for_live_plan(description: str) -> None:
    args = build_live_plan_parser(description).parse_args()
    exit_for_response(build_live_strategy_plan_with_api(args))


def _build_strategy(
    *,
    event_id: str,
    market_id: str,
    outcome: dict[str, Any],
    shares: float,
    budget_usd: float,
    family: str,
    sleeve_role: str,
    min_entry_price: float,
    max_entry_price: float,
    max_spread_cents: float,
    max_scoreboard_age_seconds: float,
    max_orderbook_age_seconds: float,
    max_abs_score_gap: float,
    min_clock_remaining_seconds: float,
    target_cents: float,
    target_return_fraction: float,
    max_adverse_cents: float,
) -> ActiveStrategy:
    label = str(outcome.get("outcome_label") or "outcome").strip()
    slug = _slug(label)
    return ActiveStrategy(
        strategy_id=f"{event_id}-{slug}-{sleeve_role}",
        family=family,
        side=label,
        sleeve_id=f"{event_id}-{slug}-{sleeve_role}",
        sleeve_group=f"{event_id}-parallel-live-test",
        sleeve_role=sleeve_role,
        budget_usd=budget_usd,
        max_positions=1,
        entry_rules={
            "market_id": market_id,
            "outcome_id": str(outcome["outcome_id"]),
            "outcome_label": label,
            "token_id": str(outcome["token_id"]),
            "side": "buy",
            "order_type": "limit",
            "time_in_force": "gtc",
            "size": shares,
            "size_policy": "plan_size",
            "respect_plan_size": True,
            "price_policy": "current_ask",
            "min_price": min_entry_price,
            "max_price": max_entry_price,
            "price_band": [min_entry_price, max_entry_price],
            "max_spread_cents": max_spread_cents,
            "max_scoreboard_age_seconds": max_scoreboard_age_seconds,
            "max_orderbook_age_seconds": max_orderbook_age_seconds,
            "max_abs_score_gap": max_abs_score_gap,
            "min_clock_remaining_seconds": min_clock_remaining_seconds,
            "allow_ultra_low_underdog": True,
            "allow_sub_10c_underdog_grid": True,
            "reason": f"{sleeve_role}_controlled_live_test",
        },
        exit_rules={
            "target_required": True,
            "target_policy": "micro_grid_scaled",
            "min_target_cents": target_cents,
            "target_return_fraction": target_return_fraction,
        },
        stop_rules={"max_adverse_cents": max_adverse_cents},
        revision_triggers=[
            {"type": "quarter_boundary_review"},
            {"type": "score_gap_break", "max_abs_score_gap": max_abs_score_gap},
            {"type": "order_fill_reconciliation_required"},
        ],
        shadow_flags={"shadow_only": False, "entry_disabled": False, "must_not_place_orders": False},
    )


def _normalize_mode(mode: str) -> str:
    return str(mode or "selected_outcome").strip().lower().replace("-", "_")


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace(".", "").split())


def _slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text or "outcome"
