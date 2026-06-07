from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.pipelines.options.cashout_simulator import default_cashout_policy
from crypto_options_app.pipelines.options.promotion_policy import default_promotion_policy
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.services.crypto_options.v4_policy import (
    V4_CANDIDATE_IDS,
    V4_DIVERGENCE_SCALPING_CANDIDATE_ID,
    V4_HEDGER_REPLICATION_CANDIDATE_ID,
    V4_OUTCOME_PREDICTION_CANDIDATE_ID,
)


V2_CANDIDATE_CONFIG_SCHEMA_VERSION = "crypto_options_v2_candidate_config_v1"
V2_CANDIDATE_PACKET_SCHEMA_VERSION = "crypto_options_v2_candidate_packet_v1"

V2_CANDIDATE_IDS = (
    "control_dynamic_hold_v1",
    "dynamic_bucketed_takeprofit_v1",
    "dynamic_dynamic_sizing_v1",
    "dynamic_parallel_conflict_cashout_v1",
    "ev_overlay_cashout_parallel_v1",
    "banded_profile_cashout_router_v1",
    "consensus_conflict_filter_cashout_v2",
    "inverse_profile_scalp_cashout_v1",
)
V2_DYNAMIC_FAMILY_CANDIDATE_IDS = V2_CANDIDATE_IDS[:4]
V2_SPECIALIZED_CANDIDATE_IDS = V2_CANDIDATE_IDS[4:]
V3_FINAL_CANDIDATE_IDS = (
    "banded_profile_cashout_router_v1",
    "dynamic_parallel_conflict_cashout_v1",
    "dynamic_bucketed_takeprofit_v1",
    "ev_overlay_cashout_parallel_v1",
    "consensus_conflict_filter_cashout_v2",
)
V3_SYSTEM_VALIDATION_CANDIDATE_ID = "v3_system_validation_cashout_v1"
V3_SYSTEM_VALIDATION_CANDIDATE_IDS = (V3_SYSTEM_VALIDATION_CANDIDATE_ID,)


def default_v2_candidate_configs(*, ledger_root: str | Path | None = None) -> list[dict[str, Any]]:
    ledger_root_path = Path(ledger_root) if ledger_root is not None else Path("v2_candidate_ledgers")
    cashout_policy = default_cashout_policy()
    promotion_policy = default_promotion_policy()
    base_exposure = {
        "starting_max_ticket_notional_usd": 2.0,
        "global_supervised_discovery_cap_usd": 100.0,
        "max_open_exposure_per_candidate_usd": 6.0,
        "live_requires_explicit_flags": True,
    }
    rows = [
        _candidate(
            "control_dynamic_hold_v1",
            lineage="dynamic_multi_signal_event_manager",
            entry_policy={"selector": "dynamic_event_manager", "hold_to_settlement": True},
            exit_policy={"mode": "hold_to_settlement"},
            capabilities={"hold_to_settle": True},
            statistical_hooks=[],
        ),
        _candidate(
            "dynamic_bucketed_takeprofit_v1",
            lineage="dynamic_multi_signal_event_manager",
            entry_policy={"selector": "dynamic_event_manager", "cashout_required": True},
            exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True},
            statistical_hooks=[],
        ),
        _candidate(
            "dynamic_dynamic_sizing_v1",
            lineage="dynamic_multi_signal_event_manager",
            entry_policy={"selector": "dynamic_event_manager", "hold_to_settlement": True},
            exit_policy={"mode": "hold_to_settlement"},
            capabilities={"hold_to_settle": True, "dynamic_sizing": True},
            statistical_hooks=[],
        ),
        _candidate(
            "dynamic_parallel_conflict_cashout_v1",
            lineage="dynamic_multi_signal_event_manager",
            entry_policy={"selector": "dynamic_event_manager", "allow_parallel_conflict": True, "cashout_required": True},
            exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True, "parallel_conflict_capable": True},
            statistical_hooks=[],
        ),
        _candidate(
            "ev_overlay_cashout_parallel_v1",
            lineage="ev_quality_overlay_limit_hold",
            entry_policy={"selector": "positive_ev_quality_overlay", "allow_parallel_conflict": True, "cashout_required": True},
            exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True, "parallel_conflict_capable": True},
            statistical_hooks=["stat_volatility_regime_filter", "stat_settlement_threshold_distance_model"],
        ),
        _candidate(
            "banded_profile_cashout_router_v1",
            lineage="ev_quality_overlay_limit_hold",
            entry_policy={"selector": "price_band_profile_router", "cashout_required": True},
            exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True},
            statistical_hooks=["stat_volatility_regime_filter"],
            price_band_reporting_metadata={
                "bands": ["0.05-0.10", "0.10-0.25", "0.25-0.40", "0.40-0.55", "0.55-0.70", "0.70-0.90"],
                "modular_band_reporting": True,
            },
        ),
        _candidate(
            "consensus_conflict_filter_cashout_v2",
            lineage="top_profile_consensus_market_follow",
            entry_policy={"selector": "top_profile_consensus", "cashout_required": True},
            exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True},
            statistical_hooks=["stat_settlement_threshold_distance_model"],
            conflict_policy={"max_conflict_ratio": 0.20, "require_consensus_window_seconds": 30},
        ),
        _candidate(
            "inverse_profile_scalp_cashout_v1",
            lineage="profile_inverse_research",
            entry_policy={"selector": "inverse_bad_profile_scalp", "cashout_required": True, "hold_to_settlement_allowed": False},
            exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True},
            statistical_hooks=["stat_volatility_regime_filter", "stat_settlement_threshold_distance_model"],
        ),
    ]
    for row in rows:
        row["ledger_path"] = str(ledger_root_path / f"candidate_ledger_{row['candidate_id']}.json")
        row["promotion_policy"] = promotion_policy
        row["exposure_policy"] = dict(base_exposure)
        row["risk_state"] = {
            "disabled": False,
            "submitted_trade_count": 0,
            "realized_pnl_usd": 0.0,
            "current_order_size_usd": promotion_policy["starting_ticket_usd"],
            "dynamic_loss_stop_usd": promotion_policy["starting_ticket_usd"] * promotion_policy["loss_stop_order_size_multiple"],
        }
        row["validation_blockers"] = validate_v2_candidate_config(row)
    return strict_jsonable(rows)


def default_v3_candidate_configs(*, ledger_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Return the five approved V3 live-test components with pairable sizing gates."""

    by_id = {row.get("candidate_id"): row for row in default_v2_candidate_configs(ledger_root=ledger_root)}
    rows = [by_id[component_id] for component_id in V3_FINAL_CANDIDATE_IDS if component_id in by_id]
    for row in rows:
        row["schema_version"] = "crypto_options_v3_candidate_config_v1"
        row["version_lineage"] = "v3_final_five_live_test"
        entry_policy = dict(row.get("entry_policy") or {})
        entry_policy.update(
            {
                "min_order_notional_usd": 5.0,
                "min_pairable_filled_shares": 5.0,
                "blocked_entry_buckets": ["0.00-0.05", "0.25-0.45", "0.55-0.70", "0.70-1.00"],
                "quarantined_entry_buckets": ["0.05-0.25"],
                "reduced_confidence_entry_buckets": ["0.25-0.50"],
                "experimental_entry_buckets": ["0.50-0.55"],
                "active_exposure_throttle": {
                    "max_open_entry_groups_total": 2,
                    "max_open_entry_groups_per_symbol_outcome": 1,
                    "max_open_entry_groups_per_event_token_outcome": 1,
                },
                "cashout_requires_pairable_entry": True,
            }
        )
        row["entry_policy"] = entry_policy
        promotion_policy = dict(row.get("promotion_policy") or {})
        promotion_policy.update(
            {
                "policy_id": "v3_component_budget_policy",
                "starting_ticket_usd": 5.0,
                "max_ticket_usd": 50.0,
                "global_supervised_discovery_cap_usd": 250.0,
                "component_budget_usd": 50.0,
                "min_order_notional_usd": 5.0,
                "order_fraction_of_current_budget": 0.10,
                "hard_stop_loss_usd": 50.0,
                "tiered_loss_stops": [
                    {"loss_usd": 15.0, "win_rate_below": 0.20},
                    {"loss_usd": 25.0, "win_rate_below": 0.40},
                    {"loss_usd": 40.0, "win_rate_below": 0.50},
                ],
            }
        )
        row["promotion_policy"] = promotion_policy
        exposure_policy = dict(row.get("exposure_policy") or {})
        exposure_policy.update(
            {
                "starting_max_ticket_notional_usd": 5.0,
                "component_budget_usd": 50.0,
                "global_supervised_discovery_cap_usd": 250.0,
                "min_order_notional_usd": 5.0,
                "min_pairable_filled_shares": 5.0,
            }
        )
        row["exposure_policy"] = exposure_policy
        row["validation_blockers"] = validate_v2_candidate_config(row)
    return strict_jsonable(rows)


def default_v3_validation_candidate_configs(*, ledger_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Return the single V3 validation component used before the five-lane parallel run."""

    ledger_root_path = Path(ledger_root) if ledger_root is not None else Path("v3_candidate_ledgers")
    cashout_policy = _v3_validation_cashout_policy()
    row = _candidate(
        V3_SYSTEM_VALIDATION_CANDIDATE_ID,
        lineage="v3_system_validation_composite",
        entry_policy={
            "selector": "v3_system_validation_composite",
            "cashout_required": True,
            "allow_same_direction_reentry": True,
            "allow_opposite_side_entries": True,
            "allow_parallel_conflict": True,
            "allow_inverse_profile_support": False,
            "allow_unknown_inverse_probe": False,
            "direct_signal_grades": ["S++", "S+"],
            "confirmation_grades": ["S", "A"],
            "inverse_validator_grades": ["E", "U"],
            "excluded_signal_grades": ["B", "C", "D"],
            "quarantined_signal_profiles": [
                {
                    "profile_name": "0xd9013df863C1Ba932780857B020DFdEAcedf8E14-1774337630675",
                    "reason": "v3_ci_cd_validation_direct_signal_4_loss_zero_win_probe",
                },
                {
                    "profile_name": "0xe9076a87c5ed90ef16e6fe6529c943baeca0cff6",
                    "reason": "v3_ci_cd_validation_inverse_validator_4_loss_zero_win_probe",
                },
            ],
            "require_direct_signal_grade": True,
            "block_when_inverse_agrees_with_direct": True,
            "block_when_a_disagrees_without_inverse_validation": True,
            "max_signal_age_seconds": 60.0,
            "min_order_notional_usd": 5.0,
            "min_pairable_filled_shares": 5.0,
            "liquidate_undersized_partial_fills": True,
            "blocked_entry_buckets": ["0.00-0.05", "0.25-0.45", "0.55-0.70", "0.70-1.00"],
            "quarantined_entry_buckets": ["0.05-0.25"],
            "reduced_confidence_entry_buckets": ["0.25-0.50"],
            "experimental_entry_buckets": ["0.50-0.55"],
            "replay_bucket_readiness": {
                "enabled": True,
                "allow_blocked_entry_buckets": ["0.55-0.70"],
                "min_trade_count": 5,
                "min_win_rate": 0.70,
                "max_sequential_losses": 2,
                "min_return_sum": 0.0,
            },
            "s_plus_low_bucket_split_probe": {
                "enabled": True,
                "entry_buckets": ["0.05-0.25"],
                "requires_replay_conditional_failure": False,
                "min_direct_signals": 1,
                "requires_inverse_validator": True,
                "min_support_weight": 18.0,
                "max_signal_to_ask_slippage_cents": 0.0,
                "max_conflict_ratio": 0.35,
                "min_depth_top3_ask_size": 10.0,
                "min_time_remaining_seconds": 20.0,
                "max_time_remaining_seconds": 300.0,
            },
            "s_plus_reduced_bucket_probe": {
                "enabled": False,
                "entry_buckets": ["0.25-0.50"],
                "requires_replay_conditional_failure": True,
                "min_direct_signals": 1,
                "min_support_weight": 18.0,
                "max_signal_to_ask_slippage_cents": 0.0,
                "max_conflict_ratio": 0.85,
                "min_depth_top3_ask_size": 10.0,
                "min_time_remaining_seconds": 90.0,
                "max_time_remaining_seconds": 300.0,
            },
            "s_plus_high_probability_probe": {
                "enabled": True,
                "entry_buckets": ["0.80-0.95"],
                "requires_replay_conditional_failure": False,
                "min_direct_signals": 1,
                "requires_inverse_validator": True,
                "min_support_weight": 20.0,
                "max_signal_to_ask_slippage_cents": 0.0,
                "max_conflict_ratio": 0.50,
                "min_depth_top3_ask_size": 10.0,
                "min_time_remaining_seconds": 90.0,
                "max_time_remaining_seconds": 300.0,
            },
            "active_exposure_throttle": {
                "max_open_entry_groups_total": 2,
                "max_open_entry_groups_per_symbol_outcome": 1,
                "max_open_entry_groups_per_event_token_outcome": 1,
            },
            "symbol_outcome_gates": [
                {
                    "symbol": "ETH",
                    "outcome": "Up",
                    "blocked_entry_buckets": ["0.05-0.25"],
                    "min_direct_signals": 2,
                    "requires_validator": True,
                    "max_conflict_ratio": 0.25,
                    "s_plus_probe_max_conflict_ratio": 0.85,
                }
            ],
            "time_to_expiry_gates": [
                {"entry_buckets": ["0.25-0.50"], "min_seconds": 60.0},
                {"entry_buckets": ["0.50-0.55"], "min_seconds": 90.0},
            ],
            "banded_profile_router_selectivity": {
                "reduced_bucket_min_direct_signals": 1,
                "reduced_bucket_min_support_weight": 18.0,
                "reduced_bucket_requires_validator": True,
                "reduced_bucket_max_conflict_ratio": 0.75,
                "experimental_bucket_min_direct_signals": 1,
                "experimental_bucket_requires_validator": True,
                "experimental_bucket_max_conflict_ratio": 0.35,
            },
            "ev_liquidity_overlay": {
                "max_spread": 0.02,
                "min_depth_top3_ask_size": 10.0,
                "max_signal_to_ask_slippage_cents": 2.0,
                "max_jit_price_drift_cents": 2.0,
            },
            "cashout_requires_pairable_entry": True,
        },
        exit_policy={"mode": "bucketed_takeprofit", "cashout_policy": cashout_policy},
        capabilities={
            "cashout_managed": True,
            "parallel_conflict_capable": True,
            "same_direction_reentry_capable": True,
            "opposite_side_entry_capable": True,
            "composite_profile_signal_capable": True,
        },
        statistical_hooks=[
            "profile_grade_signal_router",
            "profile_inverse_signal_router",
            "aggregate_profile_signal_router",
            "cashout_pairing_validation",
        ],
        conflict_policy={
            "allow_parallel_conflict": True,
            "max_conflict_ratio": 1.25,
            "same_profile_dual_side_allowed": True,
            "support_dominance_min_ratio": 1.0,
        },
        price_band_reporting_metadata={
            "bands": [bucket["bucket"] for bucket in _v3_validation_cashout_policy()["buckets"]],
            "validation_lane": True,
            "bucket_width": "0.05_or_tighter_low_bands",
        },
    )
    row["schema_version"] = "crypto_options_v3_candidate_config_v1"
    row["version_lineage"] = "v3_single_system_validation_live_test"
    row["ledger_path"] = str(ledger_root_path / f"candidate_ledger_{row['candidate_id']}.json")
    row["promotion_policy"] = {
        **default_promotion_policy(),
        "policy_id": "v3_single_validation_component_budget_policy",
        "starting_ticket_usd": 5.0,
        "max_ticket_usd": 50.0,
        "global_supervised_discovery_cap_usd": 50.0,
        "component_budget_usd": 50.0,
        "min_order_notional_usd": 5.0,
        "order_fraction_of_current_budget": 0.10,
        "hard_stop_loss_usd": 50.0,
        "max_submitted_entry_groups": 100,
        "quality_floor_min_closed_trades": 30,
        "quality_floor_win_rate_below": 0.45,
        "profit_giveback_min_peak_pnl_usd": 25.0,
        "profit_giveback_min_drawdown_usd": 20.0,
        "profit_giveback_fraction_of_peak": 0.35,
        "tiered_loss_stops": [
            {"loss_usd": 15.0, "win_rate_below": 0.20},
            {"loss_usd": 25.0, "win_rate_below": 0.40},
            {"loss_usd": 40.0, "win_rate_below": 0.50},
        ],
    }
    row["exposure_policy"] = {
        "starting_max_ticket_notional_usd": 5.0,
        "component_budget_usd": 50.0,
        "global_supervised_discovery_cap_usd": 50.0,
        "max_open_exposure_per_candidate_usd": 50.0,
        "min_order_notional_usd": 5.0,
        "min_pairable_filled_shares": 5.0,
        "max_correlated_entry_orders": 3,
        "live_requires_explicit_flags": True,
    }
    row["risk_state"] = {
        "disabled": False,
        "submitted_trade_count": 0,
        "realized_pnl_usd": 0.0,
        "current_order_size_usd": 5.0,
        "dynamic_loss_stop_usd": 50.0,
    }
    row["validation_blockers"] = validate_v2_candidate_config(row)
    return strict_jsonable([row])


def default_v4_candidate_configs(*, ledger_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Return the three isolated V4 strategy lanes."""

    ledger_root_path = Path(ledger_root) if ledger_root is not None else Path("v4_candidate_ledgers")
    cashout_policy = _v4_cashout_policy()
    rows = [
        _candidate(
            V4_HEDGER_REPLICATION_CANDIDATE_ID,
            lineage="v4_hedger_grid_inventory_replication",
            entry_policy={
                "selector": "v4_hedger_replication",
                "order_style": "limit_inventory_rebalance",
                "allowed_live_grades": ["S++", "S+", "S"],
                "fallback_live_grades_when_no_s_tier": [],
                "required_profile_styles": ["hedger", "grid_buyer"],
                "ignored_live_grades": ["A", "B", "C", "D", "E", "U"],
                "live_entries_enabled": False,
                "live_entries_disabled_reason": "v4_hedger_live_entries_disabled_pending_sell_rebalance",
                "min_order_notional_usd": 0.0,
                "min_pairable_filled_shares": 5.0,
                "prefer_min_share_limit_orders": True,
                "pulse_seconds": 20.0,
                "stop_chasing_seconds": 75.0,
                "no_new_entries_seconds": 30.0,
                "deployable_budget_usd": 50.0,
                "reserve_budget_usd": 20.0,
                "active_exposure_throttle": {
                    "max_open_entry_groups_total": 50,
                    "max_open_entry_groups_per_symbol_outcome": 25,
                    "max_open_entry_groups_per_event_token_outcome": 25,
                },
                "cashout_required": False,
            },
            exit_policy={"mode": "inventory_protection_or_settlement", "cashout_policy": {}},
            capabilities={
                "hold_to_settle": True,
                "hedger_replication_capable": True,
                "multi_entry_inventory_capable": True,
                "parallel_conflict_capable": True,
            },
            statistical_hooks=["v4_profile_style_router", "v4_hedger_inventory_ratio"],
            conflict_policy={
                "allow_parallel_conflict": True,
                "max_conflict_ratio": 1.0,
                "same_profile_dual_side_allowed": True,
            },
        ),
        _candidate(
            V4_OUTCOME_PREDICTION_CANDIDATE_ID,
            lineage="v4_elite_outcome_predictor_consensus",
            entry_policy={
                "selector": "v4_outcome_prediction_cashout",
                "order_style": "market_outcome_prediction",
                "allowed_live_grades": ["S++", "S+", "S"],
                "fallback_live_grades_when_no_s_tier": [],
                "required_profile_styles": ["outcome_predictor"],
                "ignored_live_grades": ["A", "B", "C", "D", "E", "U"],
                "min_order_notional_usd": 5.0,
                "min_pairable_filled_shares": 5.0,
                "min_active_profiles": 3,
                "top_pool_size": 7,
                "top_7_required_agreement": 5,
                "top_5_required_agreement": 4,
                "context_required": True,
                "allow_missing_threshold_context_probe": False,
                "missing_threshold_probe_min_time_remaining_seconds": 90.0,
                "dynamic_cashout": True,
                "cashout_optional_win_rate": 0.80,
                "active_exposure_throttle": {
                    "max_open_entry_groups_total": 2,
                    "max_open_entry_groups_per_symbol_outcome": 1,
                    "max_open_entry_groups_per_event_token_outcome": 1,
                },
                "cashout_required": True,
            },
            exit_policy={"mode": "dynamic_cashout_or_lifecycle_hold", "cashout_policy": cashout_policy},
            capabilities={"cashout_managed": True, "dynamic_sizing": True, "v4_outcome_prediction_capable": True},
            statistical_hooks=["v4_profile_style_router", "v4_event_context_gate", "v4_dynamic_cashout"],
        ),
        _candidate(
            V4_DIVERGENCE_SCALPING_CANDIDATE_ID,
            lineage="v4_elite_outcome_predictor_divergence_scalping",
            entry_policy={
                "selector": "v4_divergence_scalping",
                "order_style": "market_or_limit_divergence_scalp",
                "allowed_live_grades": ["S++", "S+", "S"],
                "fallback_live_grades_when_no_s_tier": [],
                "required_profile_styles": ["outcome_predictor"],
                "ignored_live_grades": ["A", "B", "C", "D", "E", "U"],
                "min_active_profiles": 3,
                "min_time_remaining_seconds": 150.0,
                "max_combined_up_down_cost": 1.04,
                "context_required": True,
                "allow_missing_threshold_context_probe": False,
                "missing_threshold_probe_min_time_remaining_seconds": 150.0,
                "allow_aggregate_scalping_with_consensus_probe": False,
                "allow_aggregate_action_scalping_probe": False,
                "cashout_required": True,
                "active_exposure_throttle": {
                    "max_open_entry_groups_total": 2,
                    "max_open_entry_groups_per_symbol_outcome": 1,
                    "max_open_entry_groups_per_event_token_outcome": 1,
                },
            },
            exit_policy={"mode": "mandatory_scalp_cashout", "cashout_policy": cashout_policy},
            capabilities={
                "cashout_managed": True,
                "dynamic_sizing": True,
                "parallel_conflict_capable": True,
                "v4_divergence_scalping_capable": True,
            },
            statistical_hooks=["v4_profile_style_router", "v4_event_context_gate", "v4_divergence_router"],
            conflict_policy={
                "allow_parallel_conflict": True,
                "max_conflict_ratio": 1.0,
                "same_profile_dual_side_allowed": False,
            },
        ),
    ]
    for row in rows:
        row["schema_version"] = "crypto_options_v4_candidate_config_v1"
        row["version_lineage"] = "v4_three_lane_live_development"
        row["ledger_path"] = str(ledger_root_path / f"candidate_ledger_{row['candidate_id']}.json")
        component_budget = 70.0 if row["candidate_id"] == V4_HEDGER_REPLICATION_CANDIDATE_ID else 50.0
        row["promotion_policy"] = {
            **default_promotion_policy(),
            "policy_id": "v4_dynamic_budget_policy",
            "starting_ticket_usd": 5.0,
            "max_ticket_usd": component_budget,
            "component_budget_usd": component_budget,
            "global_supervised_discovery_cap_usd": 170.0,
            "min_order_notional_usd": 0.0 if row["candidate_id"] == V4_HEDGER_REPLICATION_CANDIDATE_ID else 5.0,
            "order_fraction_of_current_budget": 0.10,
            "hard_stop_loss_usd": 50.0,
            "max_submitted_entry_groups": 100,
            "quality_floor_min_closed_trades": 30,
            "quality_floor_win_rate_below": 0.45,
            "profit_giveback_min_peak_pnl_usd": 25.0,
            "profit_giveback_min_drawdown_usd": 20.0,
            "profit_giveback_fraction_of_peak": 0.35,
            "stop_loss_tiers": [
                [15.0, 0.20],
                [25.0, 0.40],
                [40.0, 0.50],
            ],
        }
        row["exposure_policy"] = {
            "starting_max_ticket_notional_usd": 5.0,
            "component_budget_usd": component_budget,
            "global_supervised_discovery_cap_usd": 170.0,
            "max_open_exposure_per_candidate_usd": component_budget,
            "min_order_notional_usd": 0.0 if row["candidate_id"] == V4_HEDGER_REPLICATION_CANDIDATE_ID else 5.0,
            "min_pairable_filled_shares": 5.0,
            "max_correlated_entry_orders": 3,
            "live_requires_explicit_flags": True,
        }
        row["risk_state"] = {
            "disabled": False,
            "submitted_trade_count": 0,
            "realized_pnl_usd": 0.0,
            "current_order_size_usd": 5.0,
            "dynamic_loss_stop_usd": 50.0,
        }
        row["validation_blockers"] = validate_v2_candidate_config(row)
    return strict_jsonable(rows)


def _v3_validation_cashout_policy() -> dict[str, Any]:
    policy = default_cashout_policy()
    policy["policy_id"] = "v3_validation_tight_bucket_split_cashout_policy"
    policy["buckets"] = [
        {"bucket": "0.00-0.05", "min_price": 0.00, "max_price": 0.05, "target_multiple": 2.0, "normal_entries_blocked": True},
        {"bucket": "0.05-0.10", "min_price": 0.05, "max_price": 0.10, "target_multiple": 1.45, "split_exit": True, "stretch_target_price": 0.35},
        {"bucket": "0.10-0.15", "min_price": 0.10, "max_price": 0.15, "target_multiple": 1.35, "split_exit": True, "stretch_target_price": 0.30},
        {"bucket": "0.15-0.20", "min_price": 0.15, "max_price": 0.20, "target_multiple": 1.25, "split_exit": True, "stretch_target_price": 0.30},
        {"bucket": "0.20-0.25", "min_price": 0.20, "max_price": 0.25, "target_multiple": 1.20, "split_exit": True, "stretch_target_price": 0.35},
        {"bucket": "0.25-0.30", "min_price": 0.25, "max_price": 0.30, "target_multiple": 1.35, "requires_strong_confirmation": True},
        {"bucket": "0.30-0.35", "min_price": 0.30, "max_price": 0.35, "target_multiple": 1.30, "requires_strong_confirmation": True},
        {"bucket": "0.35-0.40", "min_price": 0.35, "max_price": 0.40, "target_multiple": 1.25, "requires_strong_confirmation": True},
        {"bucket": "0.40-0.45", "min_price": 0.40, "max_price": 0.45, "target_multiple": 1.20, "requires_strong_confirmation": True},
        {"bucket": "0.45-0.50", "min_price": 0.45, "max_price": 0.50, "target_multiple": 1.15, "requires_strong_confirmation": True},
        {"bucket": "0.50-0.55", "min_price": 0.50, "max_price": 0.55, "target_multiple": 1.10, "requires_strong_confirmation": True},
        {"bucket": "0.55-0.60", "min_price": 0.55, "max_price": 0.60, "target_multiple": 1.08, "experimental": True},
        {"bucket": "0.60-0.70", "min_price": 0.60, "max_price": 0.70, "target_multiple": 1.06, "experimental": True},
        {"bucket": "0.70-0.80", "min_price": 0.70, "max_price": 0.80, "target_multiple": 1.04, "normal_entries_blocked": True},
        {"bucket": "0.80-1.00", "min_price": 0.80, "max_price": 1.00, "target_multiple": 1.02, "normal_entries_blocked": True},
    ]
    return policy


def _v4_cashout_policy() -> dict[str, Any]:
    policy = default_cashout_policy()
    policy["policy_id"] = "v4_dynamic_cashout_policy"
    policy["dynamic_by_win_rate"] = True
    policy["buckets"] = [
        {"bucket": "0.00-0.20", "min_price": 0.00, "max_price": 0.20, "target_multiple": 1.20, "target_profile": "defensive_cashout"},
        {"bucket": "0.20-0.40", "min_price": 0.20, "max_price": 0.40, "target_multiple": 1.15, "target_profile": "moderate_cashout"},
        {"bucket": "0.40-0.60", "min_price": 0.40, "max_price": 0.60, "target_multiple": 1.10, "target_profile": "moderate_cashout"},
        {"bucket": "0.60-0.80", "min_price": 0.60, "max_price": 0.80, "target_multiple": 1.06, "target_profile": "loose_cashout"},
        {"bucket": "0.80-1.00", "min_price": 0.80, "max_price": 1.00, "target_multiple": 1.03, "target_profile": "loose_cashout"},
    ]
    return policy


def build_v2_candidate_packets(
    candidate_configs: list[dict[str, Any]],
    *,
    candidate_payloads: dict[str, dict[str, Any]] | None = None,
    disabled_candidates: dict[str, Any] | None = None,
    budget_state: dict[str, Any] | None = None,
    execute_live: bool = False,
    execution_approved: bool = False,
    acknowledge_live_risk: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    candidate_payloads = candidate_payloads or {}
    disabled_candidates = disabled_candidates or {}
    budget_state = budget_state or {"valid": False, "reason": "budget_state_missing"}
    packets: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for config in candidate_configs:
        candidate_id = str(config.get("candidate_id") or "")
        if _is_disabled(config, disabled_candidates):
            excluded.append({"candidate_id": candidate_id, "reason": "candidate_disabled"})
            continue
        blockers = validate_v2_candidate_config(config)
        live_flags_ok = bool(execute_live and execution_approved and acknowledge_live_risk)
        budget_ok = bool(budget_state.get("valid"))
        dispatch_allowed = bool(live_flags_ok and budget_ok and not blockers)
        packets.append(
            strict_jsonable(
                {
                    "schema_version": V2_CANDIDATE_PACKET_SCHEMA_VERSION,
                    "candidate_id": candidate_id,
                    "packet_type": candidate_id,
                    "base_lane_lineage": config.get("base_lane_lineage"),
                    "ledger_path": config.get("ledger_path"),
                    "candidate_payload": candidate_payloads.get(candidate_id) or {},
                    "cashout_policy": (config.get("exit_policy") or {}).get("cashout_policy"),
                    "promotion_policy": config.get("promotion_policy"),
                    "conflict_policy": config.get("conflict_policy"),
                    "exposure_policy": config.get("exposure_policy"),
                    "statistical_confirmation_hooks": config.get("statistical_confirmation_hooks") or [],
                    "price_band_reporting_metadata": config.get("price_band_reporting_metadata") or {},
                    "capabilities": config.get("capabilities") or {},
                    "dispatch_allowed": dispatch_allowed,
                    "orders_allowed": dispatch_allowed,
                    "dispatch_blockers": _dispatch_blockers(
                        config_blockers=blockers,
                        live_flags_ok=live_flags_ok,
                        budget_ok=budget_ok,
                        budget_state=budget_state,
                    ),
                    "live_flags": {
                        "execute_live": bool(execute_live),
                        "execution_approved": bool(execution_approved),
                        "acknowledge_live_risk": bool(acknowledge_live_risk),
                    },
                    "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
                }
            )
        )
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v2_candidate_packet_set_v1",
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "execution_boundary": "packet_build_only",
            "live_trading_authorized": False,
            "candidate_count": len(candidate_configs),
            "packet_count": len(packets),
            "excluded_candidates": excluded,
            "packets": packets,
            "safety_boundary": {
                "service_started": False,
                "orders_submitted": False,
                "requires_explicit_live_flags_for_dispatch": True,
            },
        }
    )


def build_dynamic_family_candidate_decisions(
    dynamic_control_decision: dict[str, Any],
    candidate_configs: list[dict[str, Any]],
    *,
    promotion_states: dict[str, dict[str, Any]] | None = None,
    event_exposure_usd: dict[str, float] | None = None,
) -> dict[str, Any]:
    promotion_states = promotion_states or {}
    event_exposure_usd = event_exposure_usd or {}
    config_by_id = {str(row.get("candidate_id")): row for row in candidate_configs}
    decisions = [
        _dynamic_family_decision(
            candidate_id,
            config_by_id[candidate_id],
            dynamic_control_decision,
            promotion_state=promotion_states.get(candidate_id) or {},
            event_exposure_usd=event_exposure_usd,
        )
        for candidate_id in V2_DYNAMIC_FAMILY_CANDIDATE_IDS
        if candidate_id in config_by_id
    ]
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v2_dynamic_family_decisions_v1",
            "execution_boundary": "decision_build_only",
            "live_trading_authorized": False,
            "control_candidate_id": "control_dynamic_hold_v1",
            "decisions": decisions,
            "comparison_to_control": compare_dynamic_family_to_control(decisions),
            "safety_boundary": {"orders_allowed": False, "service_started": False, "orders_submitted": False},
        }
    )


def build_specialized_candidate_decisions(
    candidate_configs: list[dict[str, Any]],
    *,
    base_decisions: dict[str, dict[str, Any]],
    inverse_profile_samples: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    inverse_profile_samples = inverse_profile_samples or {}
    config_by_id = {str(row.get("candidate_id")): row for row in candidate_configs}
    decisions = [
        _specialized_candidate_decision(candidate_id, config_by_id[candidate_id], base_decisions, inverse_profile_samples=inverse_profile_samples)
        for candidate_id in V2_SPECIALIZED_CANDIDATE_IDS
        if candidate_id in config_by_id
    ]
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v2_specialized_candidate_decisions_v1",
            "execution_boundary": "decision_build_only",
            "live_trading_authorized": False,
            "decisions": decisions,
            "safety_boundary": {"orders_allowed": False, "service_started": False, "orders_submitted": False},
        }
    )


def compare_dynamic_family_to_control(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    control = next((row for row in decisions if row.get("candidate_id") == "control_dynamic_hold_v1"), None)
    control_candidate = control.get("selected_candidate") if control else {}
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        selected = decision.get("selected_candidate") or {}
        rows.append(
            {
                "candidate_id": decision.get("candidate_id"),
                "status": decision.get("status"),
                "policy_differences_vs_control": decision.get("policy_differences_vs_control") or [],
                "same_event_as_control": selected.get("event_slug") == control_candidate.get("event_slug"),
                "same_primary_outcome_as_control": selected.get("outcome") == control_candidate.get("outcome"),
                "ticket_notional_delta_vs_control": _to_float(decision.get("proposed_ticket_notional_usd")) - (_to_float(control.get("proposed_ticket_notional_usd")) if control else 0.0),
                "blockers": decision.get("blockers") or [],
            }
        )
    return strict_jsonable(rows)


def validate_v2_candidate_config(config: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for field in ("candidate_id", "base_lane_lineage", "entry_policy", "exit_policy", "promotion_policy", "conflict_policy", "exposure_policy", "ledger_path", "capabilities"):
        if config.get(field) in (None, "", {}, []):
            blockers.append(f"missing_candidate_field:{field}")
    capabilities = config.get("capabilities") or {}
    exit_policy = config.get("exit_policy") or {}
    if capabilities.get("cashout_managed") and not exit_policy.get("cashout_policy"):
        blockers.append("missing_cashout_policy_for_cashout_candidate")
    promotion_policy = config.get("promotion_policy") or {}
    for field in ("starting_ticket_usd", "loss_stop_order_size_multiple", "global_supervised_discovery_cap_usd"):
        if promotion_policy.get(field) is None:
            blockers.append(f"missing_promotion_policy_field:{field}")
    conflict_policy = config.get("conflict_policy") or {}
    if capabilities.get("parallel_conflict_capable") and "allow_parallel_conflict" not in conflict_policy:
        blockers.append("missing_parallel_conflict_policy")
    return sorted(set(blockers))


def _dynamic_family_decision(
    candidate_id: str,
    config: dict[str, Any],
    dynamic_control_decision: dict[str, Any],
    *,
    promotion_state: dict[str, Any],
    event_exposure_usd: dict[str, float],
) -> dict[str, Any]:
    selected = deepcopy(dynamic_control_decision.get("selected_candidate") or {})
    status = str(dynamic_control_decision.get("status") or "blocked")
    blockers = list(dynamic_control_decision.get("blockers") or [])
    policy_differences = _dynamic_policy_differences(candidate_id)
    proposed_ticket = _base_ticket_notional(selected)
    if candidate_id == "dynamic_dynamic_sizing_v1":
        proposed_ticket = _to_float(promotion_state.get("current_ticket_usd")) or proposed_ticket
    ledger_annotations: dict[str, Any] = {
        "base_lane_lineage": "dynamic_multi_signal_event_manager",
        "policy_differences_vs_control": policy_differences,
    }
    if candidate_id == "dynamic_parallel_conflict_cashout_v1":
        selected, parallel_blockers, annotations = _parallel_conflict_selection(config, selected, proposed_ticket, event_exposure_usd)
        blockers.extend(parallel_blockers)
        ledger_annotations.update(annotations)
        if parallel_blockers:
            status = "blocked"
    return strict_jsonable(
        {
            "candidate_id": candidate_id,
            "base_lane_lineage": config.get("base_lane_lineage"),
            "status": status,
            "planned_action": dynamic_control_decision.get("planned_action"),
            "selected_candidate": selected,
            "entry_policy": config.get("entry_policy"),
            "exit_policy": config.get("exit_policy"),
            "promotion_state": promotion_state,
            "proposed_ticket_notional_usd": proposed_ticket,
            "policy_differences_vs_control": policy_differences,
            "blockers": sorted(set(blockers)),
            "ledger_annotations": ledger_annotations,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def _specialized_candidate_decision(
    candidate_id: str,
    config: dict[str, Any],
    base_decisions: dict[str, dict[str, Any]],
    *,
    inverse_profile_samples: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if candidate_id == "ev_overlay_cashout_parallel_v1":
        return _ev_overlay_candidate_decision(candidate_id, config, base_decisions.get("ev_quality_overlay_limit_hold") or {})
    if candidate_id == "banded_profile_cashout_router_v1":
        return _banded_candidate_decision(candidate_id, config, base_decisions.get("ev_quality_overlay_limit_hold") or {})
    if candidate_id == "consensus_conflict_filter_cashout_v2":
        return _consensus_candidate_decision(candidate_id, config, base_decisions.get("top_profile_consensus_market_follow") or {})
    if candidate_id == "inverse_profile_scalp_cashout_v1":
        return _inverse_candidate_decision(candidate_id, config, base_decisions.get("inverse_profile_signal") or {}, inverse_profile_samples)
    return _base_specialized_decision(candidate_id, config, {}, status="blocked", blockers=["unknown_specialized_candidate"])


def _ev_overlay_candidate_decision(candidate_id: str, config: dict[str, Any], base_decision: dict[str, Any]) -> dict[str, Any]:
    selected = deepcopy(base_decision.get("selected_candidate") or {})
    attribution = {
        "direction_component": selected.get("outcome"),
        "entry_price_component": selected.get("best_ask") or selected.get("price") or selected.get("observed_execution_price"),
        "conflict_component": {
            "allow_parallel_conflict": True,
            "conflict_ratio": selected.get("conflict_ratio"),
        },
        "cashout_component": "cashout_policy_v1",
    }
    return _base_specialized_decision(candidate_id, config, selected, status=str(base_decision.get("status") or "blocked"), attribution=attribution)


def _banded_candidate_decision(candidate_id: str, config: dict[str, Any], base_decision: dict[str, Any]) -> dict[str, Any]:
    selected = deepcopy(base_decision.get("selected_candidate") or {})
    price = _candidate_entry_price(selected)
    band = _price_band(price)
    selected["price_band"] = band
    return _base_specialized_decision(
        candidate_id,
        config,
        selected,
        status=str(base_decision.get("status") or "blocked"),
        reporting_identity={
            "candidate_is_single_router": True,
            "active_band": band,
            "band_group": _band_group(band),
            "sub_metric_dimensions": ["low_band", "mid_band", "high_band"],
        },
    )


def _consensus_candidate_decision(candidate_id: str, config: dict[str, Any], base_decision: dict[str, Any]) -> dict[str, Any]:
    selected = deepcopy(base_decision.get("selected_candidate") or {})
    blockers = list(base_decision.get("blockers") or [])
    max_conflict = _to_float((config.get("conflict_policy") or {}).get("max_conflict_ratio")) or 0.20
    conflict_ratio = _to_float(selected.get("conflict_ratio")) or 0.0
    if conflict_ratio > max_conflict:
        blockers.append("consensus_conflict_cap_exceeded")
    if bool(selected.get("ev_overlay_strong_disagreement")):
        blockers.append("ev_overlay_strong_disagreement")
    status = "blocked" if blockers else str(base_decision.get("status") or "blocked")
    return _base_specialized_decision(candidate_id, config, selected, status=status, blockers=blockers)


def _inverse_candidate_decision(
    candidate_id: str,
    config: dict[str, Any],
    inverse_signal: dict[str, Any],
    inverse_profile_samples: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    profile_key = str(inverse_signal.get("profile_key") or inverse_signal.get("profile_name") or "")
    sample = inverse_profile_samples.get(profile_key) or {}
    blockers: list[str] = []
    if int(sample.get("sample_size") or 0) < 8:
        blockers.append("inverse_profile_sample_too_small")
    if not bool(sample.get("negative_edge_stable")):
        blockers.append("inverse_profile_negative_edge_not_stable")
    if (_to_float(sample.get("win_rate")) or 1.0) > 0.40:
        blockers.append("inverse_profile_win_rate_not_bad_enough")
    selected = deepcopy(inverse_signal)
    selected["source_profile_key"] = profile_key
    selected["outcome"] = _opposite_outcome(selected.get("outcome"))
    selected["inverse_profile_sample"] = sample
    status = "blocked" if blockers else "accepted"
    return _base_specialized_decision(candidate_id, config, selected, status=status, blockers=blockers)


def _base_specialized_decision(
    candidate_id: str,
    config: dict[str, Any],
    selected: dict[str, Any],
    *,
    status: str,
    blockers: list[str] | None = None,
    attribution: dict[str, Any] | None = None,
    reporting_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return strict_jsonable(
        {
            "candidate_id": candidate_id,
            "base_lane_lineage": config.get("base_lane_lineage"),
            "status": status,
            "selected_candidate": selected,
            "entry_policy": config.get("entry_policy"),
            "exit_policy": config.get("exit_policy"),
            "ledger_path": config.get("ledger_path"),
            "blockers": sorted(set(blockers or [])),
            "reporting_identity": reporting_identity
            or {
                "candidate_id": candidate_id,
                "price_band_reporting_metadata": config.get("price_band_reporting_metadata") or {},
            },
            "performance_attribution": attribution or {},
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def _parallel_conflict_selection(
    config: dict[str, Any],
    selected: dict[str, Any],
    proposed_ticket: float,
    event_exposure_usd: dict[str, float],
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    event_slug = str(selected.get("event_slug") or selected.get("market_slug") or selected.get("event_id") or "")
    opposing = selected.get("opposing_candidate") or selected.get("conflicting_candidate")
    if not isinstance(opposing, dict):
        return selected, [], {"parallel_conflict_mode": "single_side_only_no_opposing_candidate"}
    opposing_ticket = _base_ticket_notional(opposing)
    existing_exposure = _to_float(event_exposure_usd.get(event_slug)) or 0.0
    max_exposure = _to_float((config.get("exposure_policy") or {}).get("max_open_exposure_per_candidate_usd")) or 0.0
    total = existing_exposure + proposed_ticket + opposing_ticket
    annotations = {
        "parallel_conflict_mode": "both_sides_profile_supported",
        "both_side_rationale": {
            "primary_outcome": selected.get("outcome"),
            "opposing_outcome": opposing.get("outcome"),
            "primary_profiles": selected.get("supporting_profiles") or selected.get("supporting_signals") or [],
            "opposing_profiles": opposing.get("supporting_profiles") or opposing.get("supporting_signals") or [],
        },
        "event_exposure_after_entry_usd": total,
        "max_open_exposure_per_candidate_usd": max_exposure,
    }
    if max_exposure and total > max_exposure:
        return selected, ["parallel_conflict_exposure_cap_exceeded"], annotations
    selected = deepcopy(selected)
    selected["parallel_opposing_candidate"] = opposing
    selected["parallel_conflict_total_ticket_usd"] = proposed_ticket + opposing_ticket
    return selected, [], annotations


def _dynamic_policy_differences(candidate_id: str) -> list[str]:
    return {
        "control_dynamic_hold_v1": [],
        "dynamic_bucketed_takeprofit_v1": ["cashout_policy_v1"],
        "dynamic_dynamic_sizing_v1": ["promotion_policy_v1_dynamic_sizing"],
        "dynamic_parallel_conflict_cashout_v1": ["cashout_policy_v1", "parallel_conflict_policy"],
    }.get(candidate_id, [])


def _base_ticket_notional(candidate: dict[str, Any]) -> float:
    return (
        _to_float(candidate.get("estimated_ticket_notional_usd"))
        or _to_float(candidate.get("ticket_notional_usd"))
        or _to_float(candidate.get("min_order_total_cost"))
        or 2.0
    )


def _candidate_entry_price(candidate: dict[str, Any]) -> float | None:
    return _to_float(candidate.get("best_ask")) or _to_float(candidate.get("price")) or _to_float(candidate.get("observed_execution_price"))


def _price_band(price: float | None) -> str | None:
    if price is None:
        return None
    for low, high in ((0.05, 0.10), (0.10, 0.25), (0.25, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 0.90)):
        if low <= price < high:
            return f"{low:.2f}-{high:.2f}"
    return "out_of_policy_range"


def _band_group(band: str | None) -> str | None:
    if band in {"0.05-0.10", "0.10-0.25"}:
        return "low_band"
    if band in {"0.25-0.40", "0.40-0.55"}:
        return "mid_band"
    if band in {"0.55-0.70", "0.70-0.90"}:
        return "high_band"
    return None


def _opposite_outcome(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"up", "yes", "above"}:
        return "Down"
    if normalized in {"down", "no", "below"}:
        return "Up"
    return str(value or "")


def _candidate(
    candidate_id: str,
    *,
    lineage: str,
    entry_policy: dict[str, Any],
    exit_policy: dict[str, Any],
    capabilities: dict[str, Any],
    statistical_hooks: list[str],
    conflict_policy: dict[str, Any] | None = None,
    price_band_reporting_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_capabilities = {
        "hold_to_settle": False,
        "cashout_managed": False,
        "dynamic_sizing": False,
        "parallel_conflict_capable": False,
    }
    full_capabilities.update(capabilities)
    return {
        "schema_version": V2_CANDIDATE_CONFIG_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "enabled": True,
        "base_lane_lineage": lineage,
        "entry_policy": entry_policy,
        "exit_policy": exit_policy,
        "conflict_policy": conflict_policy or {
            "allow_parallel_conflict": bool(full_capabilities["parallel_conflict_capable"]),
            "max_conflict_ratio": 0.35 if full_capabilities["parallel_conflict_capable"] else 0.20,
        },
        "statistical_confirmation_hooks": statistical_hooks,
        "price_band_reporting_metadata": price_band_reporting_metadata or {},
        "capabilities": full_capabilities,
    }


def _is_disabled(config: dict[str, Any], disabled_candidates: dict[str, Any]) -> bool:
    candidate_id = str(config.get("candidate_id") or "")
    if config.get("enabled") is False:
        return True
    disabled_row = disabled_candidates.get(candidate_id)
    if isinstance(disabled_row, dict):
        return bool(disabled_row.get("disabled", True))
    return bool(disabled_row)


def _dispatch_blockers(
    *,
    config_blockers: list[str],
    live_flags_ok: bool,
    budget_ok: bool,
    budget_state: dict[str, Any],
) -> list[str]:
    blockers = list(config_blockers)
    if not live_flags_ok:
        blockers.append("explicit_live_flags_missing")
    if not budget_ok:
        blockers.append(f"budget_state_invalid:{budget_state.get('reason') or 'unknown'}")
    return sorted(set(blockers))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


__all__ = [
    "V2_CANDIDATE_CONFIG_SCHEMA_VERSION",
    "V2_CANDIDATE_IDS",
    "V2_CANDIDATE_PACKET_SCHEMA_VERSION",
    "V2_DYNAMIC_FAMILY_CANDIDATE_IDS",
    "V2_SPECIALIZED_CANDIDATE_IDS",
    "V3_FINAL_CANDIDATE_IDS",
    "V3_SYSTEM_VALIDATION_CANDIDATE_ID",
    "V3_SYSTEM_VALIDATION_CANDIDATE_IDS",
    "V4_CANDIDATE_IDS",
    "V4_DIVERGENCE_SCALPING_CANDIDATE_ID",
    "V4_HEDGER_REPLICATION_CANDIDATE_ID",
    "V4_OUTCOME_PREDICTION_CANDIDATE_ID",
    "build_v2_candidate_packets",
    "build_dynamic_family_candidate_decisions",
    "build_specialized_candidate_decisions",
    "compare_dynamic_family_to_control",
    "default_v2_candidate_configs",
    "default_v3_candidate_configs",
    "default_v3_validation_candidate_configs",
    "default_v4_candidate_configs",
    "validate_v2_candidate_config",
]
