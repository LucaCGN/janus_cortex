from __future__ import annotations

from app.modules.agentic.target_management import build_target_management_evidence


EVENT_ID = "nba-okc-sas-2026-05-24"


def _plan() -> dict:
    return {
        "event_id": EVENT_ID,
        "market_id": "market-okc-sas",
        "active_strategies": [
            {
                "strategy_id": "okc-grid",
                "family": "price_stability_micro_grid",
                "side": "Thunder",
                "sleeve_id": "okc-grid",
                "sleeve_role": "grid_scalp",
                "entry_rules": {
                    "market_id": "market-okc-sas",
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "size": 5,
                },
                "exit_rules": {
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 5,
                    "target_tick_size": 0.01,
                },
            },
            {
                "strategy_id": "okc-core",
                "family": "core_hold_live_validation",
                "side": "Thunder",
                "sleeve_id": "okc-core",
                "sleeve_role": "core_hold",
                "entry_rules": {
                    "market_id": "market-okc-sas",
                    "outcome_id": "outcome-okc",
                    "token_id": "token-okc",
                    "size": 5,
                },
                "exit_rules": {
                    "target_policy": "micro_grid_scaled",
                    "min_target_cents": 6,
                    "target_tick_size": 0.01,
                },
            },
        ],
    }


def test_target_management_allocates_janus_and_operator_lots_to_sleeves_pytest() -> None:
    evidence = build_target_management_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "open_positions": {
                "positions": [
                    {"asset": "token-okc", "size": 10, "avg_price": 0.05, "outcome": "Thunder"},
                ]
            },
            "open_orders": {
                "orders": [
                    {"id": "target-grid", "asset": "token-okc", "side": "sell", "status": "open", "size": 5, "price": 0.10},
                    {"id": "target-core", "asset": "token-okc", "side": "sell", "status": "open", "size": 5, "price": 0.12},
                ]
            },
            "direct_trades": {
                "trades": [
                    {"id": "trade-1", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.04, "order_id": "janus-buy-1"},
                    {"id": "trade-2", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.06, "order_id": "operator-buy-1"},
                ]
            },
        },
    )

    by_sleeve = {row.sleeve_id: row for row in evidence.sleeves}
    assert evidence.target_covered_count == 2
    assert evidence.replacement_recommendation_count == 0
    assert by_sleeve["okc-grid"].weighted_basis_price == 0.04
    assert by_sleeve["okc-grid"].lot_actor_counts == {"janus": 1}
    assert by_sleeve["okc-grid"].target_status == "target_covered"
    assert by_sleeve["okc-core"].weighted_basis_price == 0.06
    assert by_sleeve["okc-core"].lot_actor_counts == {"operator": 1}
    assert by_sleeve["okc-core"].target_price == 0.12


def test_target_management_flags_stale_and_missing_sleeve_targets_pytest() -> None:
    evidence = build_target_management_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "open_positions": {
                "positions": [
                    {"asset": "token-okc", "size": 10, "avg_price": 0.05, "outcome": "Thunder"},
                ]
            },
            "open_orders": {
                "orders": [
                    {"id": "stale-grid-target", "asset": "token-okc", "side": "sell", "status": "open", "size": 5, "price": 0.06},
                ]
            },
            "direct_trades": {
                "trades": [
                    {"id": "trade-1", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.04, "order_id": "janus-buy-1"},
                    {"id": "trade-2", "asset": "token-okc", "side": "buy", "size": 5, "price": 0.06, "order_id": "operator-buy-1"},
                ]
            },
        },
    )

    by_sleeve = {row.sleeve_id: row for row in evidence.sleeves}
    assert evidence.target_stale_count == 1
    assert evidence.target_missing_count == 1
    assert evidence.replacement_recommendation_count == 2
    assert by_sleeve["okc-grid"].target_status == "target_stale"
    assert by_sleeve["okc-grid"].reason_codes == [
        "target_price_below_current_lot_basis_policy",
        "replace_or_place_target_order_recommended",
    ]
    assert by_sleeve["okc-core"].target_status == "target_missing"
    assert by_sleeve["okc-core"].target_coverage_shares == 0.0


def test_target_management_ignores_public_current_token_trade_tape_pytest() -> None:
    evidence = build_target_management_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "open_positions": {
                "positions": [
                    {"asset": "token-okc", "size": 10, "avg_price": 0.05, "outcome": "Thunder"},
                ]
            },
            "open_orders": {
                "orders": [
                    {"id": "target-grid", "asset": "token-okc", "side": "sell", "status": "open", "size": 5, "price": 0.10},
                    {"id": "target-core", "asset": "token-okc", "side": "sell", "status": "open", "size": 5, "price": 0.12},
                ]
            },
            "current_token_trades": {
                "trades": [
                    {
                        "id": "public-market-print",
                        "asset": "token-okc",
                        "side": "buy",
                        "size": 4060,
                        "price": 0.46,
                        "order_id": "not-our-order",
                    },
                ]
            },
        },
    )

    assert len(evidence.sleeves) == 2
    assert not any(row.sleeve_role == "unassigned_excess" for row in evidence.sleeves)
    assert sum(row.allocated_shares for row in evidence.sleeves) == 10.0
    assert {row.weighted_basis_price for row in evidence.sleeves} == {0.05}


def test_target_management_caps_known_market_observations_to_account_position_pytest() -> None:
    evidence = build_target_management_evidence(
        event_id=EVENT_ID,
        plan=_plan(),
        known_external_order_ids={"janus-buy-1"},
        direct_clob={
            "open_positions": {
                "positions": [
                    {"asset": "token-okc", "size": 10, "avg_price": 0.04, "outcome": "Thunder"},
                ]
            },
            "current_token_trades": {
                "trades": [
                    {
                        "id": "known-public-market-print",
                        "asset": "token-okc",
                        "side": "buy",
                        "size": 4060,
                        "price": 0.04,
                        "order_id": "janus-buy-1",
                    },
                ]
            },
        },
    )

    assert len(evidence.sleeves) == 2
    assert not any(row.sleeve_role == "unassigned_excess" for row in evidence.sleeves)
    assert sum(row.allocated_shares for row in evidence.sleeves) == 10.0
