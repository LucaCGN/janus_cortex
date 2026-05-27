from __future__ import annotations

from datetime import datetime, timezone

from app.modules.agentic.contracts import StrategyPlan
from codex_tools.janus.live_plan import (
    build_event_import_payload,
    build_live_strategy_plan_from_catalog,
    select_moneyline_market,
    select_plan_outcomes,
)


CATALOG_EVENT = {
    "event_id": "11111111-1111-1111-1111-111111111111",
    "canonical_slug": "spurs-vs-thunder",
    "title": "Spurs vs. Thunder",
}

MONEYLINE_MARKET = {
    "market_id": "22222222-2222-2222-2222-222222222222",
    "market_type": "moneyline",
    "question": "Spurs vs. Thunder",
}

OUTCOMES = [
    {
        "outcome_id": "33333333-3333-3333-3333-333333333333",
        "outcome_label": "Spurs",
        "token_id": "token-sas",
    },
    {
        "outcome_id": "44444444-4444-4444-4444-444444444444",
        "outcome_label": "Thunder",
        "token_id": "token-okc",
    },
]


def test_selected_outcome_live_plan_splits_grid_and_core_sleeves_pytest() -> None:
    plan = build_live_strategy_plan_from_catalog(
        event_id="nba-sas-okc-2026-05-24",
        event_url="https://polymarket.com/event/spurs-vs-thunder",
        league="nba",
        catalog_event=CATALOG_EVENT,
        markets=[MONEYLINE_MARKET],
        outcomes=OUTCOMES,
        outcome_label="Spurs",
        total_shares=10,
        grid_leg_shares=5,
        max_buy_notional_usd=10,
        generated_at_utc=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    validated = StrategyPlan.model_validate(plan)

    assert validated.plan_owner == "system"
    assert validated.market_id == MONEYLINE_MARKET["market_id"]
    assert [item.sleeve_role for item in validated.active_strategies] == ["grid_scalp", "core_hold"]
    assert [item.entry_rules["size"] for item in validated.active_strategies] == [5, 5]
    assert all(item.entry_rules["size_policy"] == "plan_size" for item in validated.active_strategies)
    assert all(item.shadow_flags["must_not_place_orders"] is False for item in validated.active_strategies)


def test_responsive_both_sides_live_plan_builds_one_grid_sleeve_per_outcome_pytest() -> None:
    plan = build_live_strategy_plan_from_catalog(
        event_id="wnba-con-ind-2026-05-24",
        event_url="https://polymarket.com/event/connecticut-sun-vs-indiana-fever",
        league="wnba",
        catalog_event=CATALOG_EVENT,
        markets=[MONEYLINE_MARKET],
        outcomes=OUTCOMES,
        mode="responsive_both_sides",
        grid_leg_shares=5,
        max_buy_notional_usd=10,
        generated_at_utc=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    validated = StrategyPlan.model_validate(plan)

    assert validated.context_summary["planning_mode"] == "responsive_both_sides"
    assert validated.context_summary["wnba_controlled_entry_fallback"] is True
    assert len(validated.active_strategies) == 6
    assert {item.entry_rules["outcome_label"] for item in validated.active_strategies} == {"Spurs", "Thunder"}
    grid_strategies = [item for item in validated.active_strategies if item.family == "price_stability_micro_grid"]
    core_strategies = [item for item in validated.active_strategies if item.family == "core_hold_live_validation"]
    controlled_strategies = [
        item for item in validated.active_strategies if item.family == "wnba_controlled_min_size_entry_v1"
    ]
    assert len(grid_strategies) == 2
    assert len(core_strategies) == 2
    assert len(controlled_strategies) == 2
    assert all(item.entry_rules["max_spread_cents"] == 2.0 for item in grid_strategies)
    assert all(item.sleeve_role == "core_hold" for item in core_strategies)
    assert all(item.entry_rules["max_spread_cents"] == 6.0 for item in controlled_strategies)
    assert all(item.entry_rules["controlled_entry_requires_grid_spread_blocker"] is True for item in controlled_strategies)


def test_responsive_both_sides_nba_plan_can_include_core_and_ultra_low_sleeves_pytest() -> None:
    plan = build_live_strategy_plan_from_catalog(
        event_id="nba-sas-okc-2026-05-26",
        event_url="https://polymarket.com/event/nba-sas-okc-2026-05-26",
        league="nba",
        catalog_event=CATALOG_EVENT,
        markets=[MONEYLINE_MARKET],
        outcomes=OUTCOMES,
        mode="responsive_both_sides",
        total_shares=10,
        grid_leg_shares=5,
        max_buy_notional_usd=10,
        include_ultra_low_rebound=True,
        generated_at_utc=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
    )

    validated = StrategyPlan.model_validate(plan)

    assert validated.context_summary["planning_mode"] == "responsive_both_sides"
    assert validated.context_summary["core_hold_enabled"] is True
    assert validated.context_summary["ultra_low_rebound_enabled"] is True
    assert len(validated.active_strategies) == 6
    assert {item.sleeve_role for item in validated.active_strategies} == {
        "grid_scalp",
        "core_hold",
        "ultra_low_rebound",
    }
    ultra_low_strategies = [
        item for item in validated.active_strategies if item.family == "ultra_low_underdog_decimal_grid"
    ]
    assert len(ultra_low_strategies) == 2
    assert all(item.budget_usd == 2.0 for item in ultra_low_strategies)
    assert all(item.entry_rules["price_band"] == [0.003, 0.05] for item in ultra_low_strategies)
    assert all(item.entry_rules["sizing_mode"] == "minimum_notional" for item in ultra_low_strategies)
    assert all(item.entry_rules["position_limit_scope"] == "sleeve" for item in ultra_low_strategies)
    assert all(item.entry_rules["allow_existing_position_add"] is True for item in ultra_low_strategies)
    assert all(item.exit_rules["target_tick_size"] == 0.001 for item in ultra_low_strategies)


def test_wnba_event_import_payload_uses_moneyline_catalog_probe_pytest() -> None:
    payload = build_event_import_payload(
        event_url="https://polymarket.com/event/connecticut-sun-vs-indiana-fever",
        league="wnba",
    )

    assert payload["history_market_selector"] == "moneyline"
    assert payload["history_mode"] == "rolling_recent"
    assert payload["allow_snapshot_fallback"] is True


def test_selectors_accept_loose_moneyline_and_outcome_labels_pytest() -> None:
    market = select_moneyline_market([{"market_id": "primary", "market_type": "primary"}, MONEYLINE_MARKET])
    selected = select_plan_outcomes(OUTCOMES, mode="selected_outcome", outcome_label="sas spurs")

    assert market["market_id"] == MONEYLINE_MARKET["market_id"]
    assert selected[0]["outcome_label"] == "Spurs"
