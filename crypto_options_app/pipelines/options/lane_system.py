from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_LANE_SYSTEM_SCHEMA_VERSION = "crypto_options_five_lane_system_v1"
PROFILE_GRADE_CACHE_SCHEMA_VERSION = "crypto_options_profile_grade_cache_v1"
LANE_LEDGER_SCHEMA_VERSION = "crypto_options_lane_ledger_v1"
LANE_EXECUTION_PACKET_SCHEMA_VERSION = "crypto_options_lane_execution_packet_v1"


@dataclass(frozen=True)
class LaneBudgetPolicy:
    lane_loss_stop_usd: float = 10.0
    global_loss_stop_usd: float = 50.0
    max_open_exposure_per_lane_usd: float = 6.0
    max_ticket_notional_usd: float = 2.0
    positive_pnl_ticket_step_usd: float = 3.0
    positive_pnl_ticket_notional_usd: float = 3.0
    min_settled_trades_before_discard: int = 8


@dataclass(frozen=True)
class LaneConfig:
    lane_id: str
    description: str
    signal_sources: tuple[str, ...]
    order_style: str
    entry_rules: dict[str, Any]
    exit_rules: dict[str, Any] = field(default_factory=dict)
    budget_policy: LaneBudgetPolicy = field(default_factory=LaneBudgetPolicy)
    sizing_policy: dict[str, Any] = field(default_factory=dict)
    promotion_rules: dict[str, Any] = field(default_factory=dict)


def default_five_lane_configs() -> list[dict[str, Any]]:
    """Return the decision-complete five-lane configuration set."""

    base_budget = LaneBudgetPolicy()
    common_promotion = {
        "primary_metric": "net_realized_pnl_usd",
        "discard_if_lane_loss_stop_hit": True,
        "discard_if_negative_ev_after_settled_trades": 8,
        "promote_if_positive_and_beats_controls": True,
        "production_unsupervised_allowed": False,
    }
    common_sizing = {
        "initial_max_ticket_notional_usd": 2.0,
        "profit_unlocked_max_ticket_notional_usd": 3.0,
        "profit_unlock_threshold_usd": 3.0,
        "max_position_shares_reference": 5.0,
    }
    modular_probe = {
        "enabled": True,
        "ignore_monitor_failed_checks": [
            "selected_backtest_variant_not_ready",
            "win_rate_below_target_and_no_economic_edge",
            "base_pnl_not_positive",
            "not_profitable_after_3c_adverse_slippage",
        ],
        "promotion_requires_warnings_resolved": True,
    }
    lanes = [
        LaneConfig(
            lane_id="single_best_profile_market_follow",
            description="Control lane: follow the best active S-grade profile; fallback to Bonereaper if no higher grade is active.",
            signal_sources=("profile_signal",),
            order_style="FAK_market_buy_hold_to_settlement",
            entry_rules={
                "selector": "best_single_profile_first_signal",
                "preferred_fallback_profile": "bonereaper",
                "allowed_grades": ["S"],
                "max_signal_age_seconds": 60,
                "modular_probe_max_signal_age_seconds": 300,
                "max_entries_per_event": 1,
                "modular_probe": modular_probe,
            },
            budget_policy=base_budget,
            sizing_policy=common_sizing,
            promotion_rules=common_promotion,
        ),
        LaneConfig(
            lane_id="top_profile_consensus_market_follow",
            description="Control/aggregation lane: one S+ profile or two A/S profiles agreeing within 30 seconds.",
            signal_sources=("profile_signal",),
            order_style="FAK_market_buy_hold_to_settlement",
            entry_rules={
                "selector": "top_profile_consensus",
                "single_trigger_grades": ["S"],
                "confirming_grades": ["S", "A"],
                "min_confirming_profiles": 2,
                "confirmation_window_seconds": 30,
                "max_signal_age_seconds": 90,
                "max_entries_per_event": 1,
                "modular_probe": modular_probe,
            },
            budget_policy=base_budget,
            sizing_policy=common_sizing,
            promotion_rules=common_promotion,
        ),
        LaneConfig(
            lane_id="ev_quality_overlay_limit_hold",
            description="Main lane: profile-weighted EV, price bucket EV, signal age, and conflict penalties with JIT marketable limits.",
            signal_sources=("profile_signal", "price_bucket_ev"),
            order_style="FAK_marketable_limit_buy_hold_to_settlement",
            entry_rules={
                "selector": "positive_ev_quality_overlay",
                "min_expected_value_per_share": 0.015,
                "max_conflict_ratio": 0.25,
                "max_signal_age_seconds": 120,
                "late_event_seconds": 180,
                "late_event_min_expected_value_per_share": 0.04,
                "modular_probe_max_conflict_ratio": 0.75,
                "slippage_cents": 3,
                "max_entries_per_event": 1,
                "modular_probe": modular_probe,
            },
            budget_policy=base_budget,
            sizing_policy=common_sizing,
            promotion_rules=common_promotion,
        ),
        LaneConfig(
            lane_id="dynamic_multi_signal_event_manager",
            description="Dynamic lane: allow add, exit, or flip when later profile evidence dominates the current position.",
            signal_sources=("profile_signal", "profile_conflict", "open_position_state"),
            order_style="FAK_market_buy_or_sell_event_manager",
            entry_rules={
                "selector": "dynamic_event_manager",
                "min_expected_value_per_share": 0.02,
                "confidence_margin_to_add": 0.12,
                "confidence_margin_to_exit": 0.10,
                "confidence_margin_to_flip": 0.18,
                "max_actions_per_event": 2,
                "max_open_outcomes_per_event": 1,
                "modular_probe": modular_probe,
            },
            exit_rules={
                "allow_sell_to_close": True,
                "allow_flip_after_close": True,
                "sell_requires_matching_open_position": True,
            },
            budget_policy=base_budget,
            sizing_policy=common_sizing,
            promotion_rules=common_promotion,
        ),
        LaneConfig(
            lane_id="proprietary_algo_profile_confirmed",
            description="Hybrid lane: deterministic BTC/ETH signal requires profile confirmation or no strong opposition.",
            signal_sources=("deterministic_algo", "profile_signal"),
            order_style="FAK_marketable_limit_buy_hold_to_settlement",
            entry_rules={
                "selector": "algo_primary_profile_confirmed",
                "required_algo_signal": True,
                "confirmation_grades": ["S", "A", "B"],
                "allow_no_strong_opposition": True,
                "allow_orderbook_favorite_proxy_for_modular_probe": True,
                "strong_opposition_grades": ["S", "A"],
                "min_expected_value_per_share": 0.015,
                "max_entries_per_event": 1,
                "modular_probe": modular_probe,
            },
            budget_policy=base_budget,
            sizing_policy=common_sizing,
            promotion_rules=common_promotion,
        ),
    ]
    return strict_jsonable([_lane_to_dict(lane) for lane in lanes])


def build_order_sizing_matrix(*, generated_at: datetime | None = None) -> dict[str, Any]:
    """Document the order-size premise we must satisfy before live lane tests."""

    generated_at = generated_at or datetime.now(timezone.utc)
    rows = [
        {
            "order_case": "market_buy_FAK_or_FOK",
            "execution_style": "market",
            "side": "BUY",
            "order_type": "FAK/FOK",
            "amount_semantics": "dollar_amount",
            "min_practical_order": ">= 1.00 USDC notional",
            "price_semantics": "worst acceptable execution price / slippage bound",
            "lane_use": "single_best_profile_market_follow, top_profile_consensus_market_follow",
            "status": "docs_confirmed_live_guarded",
        },
        {
            "order_case": "market_sell_FAK_or_FOK",
            "execution_style": "market",
            "side": "SELL",
            "order_type": "FAK/FOK",
            "amount_semantics": "share_amount",
            "min_practical_order": "requires held conditional-token shares",
            "price_semantics": "worst acceptable execution price / slippage bound",
            "lane_use": "dynamic_multi_signal_event_manager",
            "status": "docs_confirmed_live_guarded",
        },
        {
            "order_case": "marketable_limit_buy_FAK",
            "execution_style": "limit",
            "side": "BUY",
            "order_type": "FAK",
            "amount_semantics": "share_size_at_limit_price",
            "min_practical_order": "CLOB amount precision plus >= 1.00 USDC marketable notional guard",
            "price_semantics": "limit price derived from JIT best ask plus slippage cents",
            "lane_use": "ev_quality_overlay_limit_hold, proprietary_algo_profile_confirmed",
            "status": "live_observed_precision_patch_required",
        },
        {
            "order_case": "resting_limit_buy_GTC_or_GTD",
            "execution_style": "limit",
            "side": "BUY",
            "order_type": "GTC/GTD",
            "amount_semantics": "share_size_at_limit_price",
            "min_practical_order": "5-share UX/API premise requires empirical confirmation before live use",
            "price_semantics": "resting bid limit price",
            "lane_use": "not used in v1 live lanes",
            "status": "research_only_until_empirically_validated",
        },
        {
            "order_case": "sell_to_close_FAK",
            "execution_style": "market_or_limit",
            "side": "SELL",
            "order_type": "FAK",
            "amount_semantics": "held share size",
            "min_practical_order": "cannot exceed reconciled held shares",
            "price_semantics": "JIT best bid minus slippage cents for marketable limit",
            "lane_use": "dynamic_multi_signal_event_manager",
            "status": "requires_matching_open_position",
        },
    ]
    return {
        "schema_version": "crypto_options_order_sizing_matrix_v1",
        "generated_at_utc": generated_at.isoformat(),
        "source_urls": [
            "https://docs.polymarket.com/developers/CLOB/orders/create-order",
            "https://docs.polymarket.com/trading/clients/l2",
        ],
        "source_notes": [
            "Polymarket market BUY amount is treated as notional dollars in the CLOB client.",
            "Polymarket market SELL amount is treated as shares in the CLOB client.",
            "The overnight live test observed buy limit precision rejects and sub-dollar marketable-buy rejects; both are now guarded.",
        ],
        "rows": rows,
    }


def build_profile_grade_cache(profile_report: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Create a latency-friendly cache of static and current-event profile quality."""

    now = now or _parse_time(profile_report.get("generated_at_utc")) or datetime.now(timezone.utc)
    active_slugs = {str(item) for item in profile_report.get("active_event_slugs") or [] if item}
    attribution = _profile_backtest_attribution(profile_report)
    profiles = []
    for snapshot in profile_report.get("profiles") or []:
        if not isinstance(snapshot, dict):
            continue
        identity = _profile_identity(snapshot)
        signals = [row for row in snapshot.get("signals") or [] if isinstance(row, dict)]
        active_signals = [
            signal
            for signal in signals
            if not active_slugs or str(signal.get("event_slug") or signal.get("market_slug") or "") in active_slugs
        ]
        timing = _timing_stats((snapshot.get("historical_signals") or []) + active_signals)
        base_score = _to_float(snapshot.get("score")) or 0.0
        live_modifier = _live_grade_modifier(active_signals, timing=timing)
        dynamic_score = max(0.0, min(100.0, base_score + live_modifier))
        row = {
            "profile_key": identity["key"],
            "profile_name": identity["name"],
            "proxy_wallet": identity["wallet"],
            "base_grade": snapshot.get("grade"),
            "base_score": round(base_score, 3),
            "live_modifier": round(live_modifier, 3),
            "dynamic_score": round(dynamic_score, 3),
            "dynamic_grade": _grade_from_score(dynamic_score),
            "polarity": snapshot.get("polarity"),
            "active_event_status": "active" if active_signals else "inactive",
            "active_signal_count": len(active_signals),
            "timing_stats": timing,
            "ev_stats": attribution.get(identity["key"]) or _empty_attribution(),
            "inverse_signal_eligible": _inverse_signal_eligible(snapshot, attribution.get(identity["key"])),
            "grade_reasons": snapshot.get("grade_reasons") or [],
        }
        profiles.append(row)
    return strict_jsonable(
        {
            "schema_version": PROFILE_GRADE_CACHE_SCHEMA_VERSION,
            "generated_at_utc": now.isoformat(),
            "profile_count": len(profiles),
            "active_profile_count": sum(1 for row in profiles if row["active_event_status"] == "active"),
            "profiles": sorted(profiles, key=lambda row: (row["dynamic_score"], row["profile_name"] or ""), reverse=True),
        }
    )


def build_research_premise_report(profile_report: dict[str, Any], grade_cache: dict[str, Any]) -> dict[str, Any]:
    trades = _backtest_trades(profile_report)
    return strict_jsonable(
        {
            "schema_version": "crypto_options_research_premise_report_v1",
            "time_of_entry": _time_of_entry_report(trades),
            "price_bucket_ev": _price_bucket_ev_report(trades),
            "signal_aging": _signal_aging_report(),
            "profile_followability": _profile_followability_report(grade_cache),
            "late_event_risk": _late_event_risk_report(trades),
            "shadow_boundary": "Shadow/backtest output is research evidence only; live performance requires real submitted-order ledgers.",
        }
    )


def build_lane_decision_report(
    profile_report: dict[str, Any],
    *,
    grade_cache: dict[str, Any] | None = None,
    lane_configs: list[dict[str, Any]] | None = None,
    lane_ledgers: dict[str, dict[str, Any]] | None = None,
    algo_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _parse_time(profile_report.get("generated_at_utc")) or datetime.now(timezone.utc)
    grade_cache = grade_cache or build_profile_grade_cache(profile_report, now=now)
    lane_configs = lane_configs or default_five_lane_configs()
    lane_ledgers = lane_ledgers or {}
    candidates = _current_candidate_rows(profile_report)
    decisions = [
        _lane_decision(
            lane,
            candidates=candidates,
            active_signals=_active_signal_rows(profile_report),
            grade_cache=grade_cache,
            ledger=lane_ledgers.get(str(lane.get("lane_id") or "")),
            algo_signals=algo_signals or [],
            source_monitor_status=profile_report.get("monitor_status"),
            now=now,
        )
        for lane in lane_configs
    ]
    return strict_jsonable(
        {
            "schema_version": "crypto_options_five_lane_decision_report_v1",
            "generated_at_utc": now.isoformat(),
            "lane_count": len(lane_configs),
            "candidate_count": len(candidates),
            "decisions": decisions,
            "boundary": {
                "orders_allowed": False,
                "description": "Lane decisions are readiness artifacts only; live submission remains isolated behind explicit executor flags.",
            },
        }
    )


def normalize_lane_ledger(payload: dict[str, Any] | None, *, lane_id: str) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("schema_version") == LANE_LEDGER_SCHEMA_VERSION:
        ledger = dict(payload)
        ledger.setdefault("entries", [])
        return ledger
    return {
        "schema_version": LANE_LEDGER_SCHEMA_VERSION,
        "lane_id": lane_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "entries": [],
    }


def append_lane_decision(ledger: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    lane_id = str(ledger.get("lane_id") or decision.get("lane_id") or "")
    updated = normalize_lane_ledger(ledger, lane_id=lane_id)
    entries = [dict(row) for row in updated.get("entries") or [] if isinstance(row, dict)]
    decision_id = str(decision.get("decision_id") or "")
    if decision_id and any(str(row.get("decision_id") or "") == decision_id for row in entries):
        return updated
    entries.append(dict(decision))
    updated["entries"] = entries
    updated["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return updated


def lane_risk_state(ledger: dict[str, Any], budget_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    budget_policy = budget_policy or {}
    entries = [row for row in ledger.get("entries") or [] if isinstance(row, dict)]
    submitted = [row for row in entries if row.get("status") in {"submitted", "settled"}]
    realized = [row for row in entries if _to_float(row.get("realized_pnl_net_usd")) is not None]
    net = sum(float(row.get("realized_pnl_net_usd") or 0.0) for row in realized)
    open_exposure = sum(float(row.get("estimated_total_cost_usd") or 0.0) for row in submitted if row.get("settlement_status") == "open")
    stop = float(budget_policy.get("lane_loss_stop_usd") or 10.0)
    max_open = float(budget_policy.get("max_open_exposure_per_lane_usd") or 6.0)
    ticket_cap = float(budget_policy.get("max_ticket_notional_usd") or 2.0)
    if net >= float(budget_policy.get("positive_pnl_ticket_step_usd") or 3.0):
        ticket_cap = float(budget_policy.get("positive_pnl_ticket_notional_usd") or 3.0)
    return {
        "entry_count": len(entries),
        "submitted_count": len(submitted),
        "settled_count": len(realized),
        "net_realized_pnl_usd": round(net, 6),
        "relative_realized_loss_usd": round(max(0.0, -net), 6),
        "lane_loss_stop_usd": stop,
        "lane_loss_stop_hit": max(0.0, -net) >= stop,
        "open_exposure_usd": round(open_exposure, 6),
        "max_open_exposure_per_lane_usd": max_open,
        "open_exposure_available_usd": round(max(0.0, max_open - open_exposure), 6),
        "current_max_ticket_notional_usd": ticket_cap,
    }


def build_five_lane_comparison(
    lane_ledgers: dict[str, dict[str, Any]],
    *,
    lane_configs: list[dict[str, Any]] | None = None,
    global_loss_stop_usd: float = 50.0,
) -> dict[str, Any]:
    lane_configs = lane_configs or default_five_lane_configs()
    rows = []
    for lane in lane_configs:
        lane_id = str(lane.get("lane_id") or "")
        ledger = normalize_lane_ledger(lane_ledgers.get(lane_id), lane_id=lane_id)
        entries = [row for row in ledger.get("entries") or [] if isinstance(row, dict)]
        settled = [row for row in entries if _to_float(row.get("realized_pnl_net_usd")) is not None]
        metrics = compute_trade_metrics(pd.DataFrame(_metric_rows(settled)))
        risk = lane_risk_state(ledger, lane.get("budget_policy") or {})
        rows.append(
            {
                "lane_id": lane_id,
                "order_style": lane.get("order_style"),
                "status": _lane_status(metrics, risk, lane),
                "risk_state": risk,
                "trade_metrics": metrics,
                "latency_slippage": _latency_slippage_summary(settled),
                "promotion_decision": _promotion_decision(metrics, risk),
            }
        )
    global_net = sum(float((row.get("risk_state") or {}).get("net_realized_pnl_usd") or 0.0) for row in rows)
    return strict_jsonable(
        {
            "schema_version": "crypto_options_five_lane_comparison_v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "global_loss_stop_usd": float(global_loss_stop_usd),
            "global_net_realized_pnl_usd": round(global_net, 6),
            "global_loss_stop_hit": max(0.0, -global_net) >= float(global_loss_stop_usd),
            "lanes": rows,
            "best_lane": _best_comparison_lane(rows),
        }
    )


def build_unified_signal_router_design(
    lane_comparison: dict[str, Any],
    *,
    lane_decision_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lanes = [row for row in lane_comparison.get("lanes") or [] if isinstance(row, dict)]
    promoted = [row for row in lanes if (row.get("promotion_decision") or {}).get("decision") == "promote_candidate"]
    controls = {row.get("lane_id"): row for row in lanes if str(row.get("lane_id") or "").startswith(("single_", "top_"))}
    return strict_jsonable(
        {
            "schema_version": "crypto_options_unified_signal_router_design_v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "router_status": "design_ready_needs_supervised_validation" if promoted else "not_ready_no_promoted_lane",
            "selected_modules": [row.get("lane_id") for row in promoted],
            "benchmark_controls": sorted(controls),
            "allocation_policy": {
                "score": "expected_value_per_share * grade_confidence * drawdown_multiplier",
                "max_global_loss_usd": lane_comparison.get("global_loss_stop_usd"),
                "production_unsupervised_allowed": False,
            },
            "hard_gates": [
                "current_executable_quote_required",
                "signal_age_must_pass_lane_threshold",
                "global_loss_stop_blocks_all_lanes",
                "open_position_must_reconcile_before_new_hold_to_settlement_entry",
                "manual_review_required_before_unsupervised_production",
            ],
            "latest_decisions": (lane_decision_report or {}).get("decisions") or [],
            "production_blockers": []
            if promoted
            else ["no_lane_promoted_from_supervised_live_comparison"],
        }
    )


def build_five_lane_system_package(
    profile_report: dict[str, Any],
    *,
    lane_ledgers: dict[str, dict[str, Any]] | None = None,
    algo_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    source_payload = profile_report
    nested_report = source_payload.get("profile_signal_report") if isinstance(source_payload.get("profile_signal_report"), dict) else None
    research_report = nested_report or source_payload
    now = now or _parse_time(source_payload.get("generated_at_utc")) or _parse_time(research_report.get("generated_at_utc")) or datetime.now(timezone.utc)
    lane_configs = default_five_lane_configs()
    grade_cache = build_profile_grade_cache(research_report, now=now)
    premise_report = build_research_premise_report(research_report, grade_cache)
    order_matrix = build_order_sizing_matrix(generated_at=now)
    decisions = build_lane_decision_report(
        source_payload,
        grade_cache=grade_cache,
        lane_configs=lane_configs,
        lane_ledgers=lane_ledgers or {},
        algo_signals=algo_signals or [],
        now=now,
    )
    comparison = build_five_lane_comparison(lane_ledgers or {}, lane_configs=lane_configs)
    router = build_unified_signal_router_design(comparison, lane_decision_report=decisions)
    execution_plan = build_parallel_lane_execution_plan(decisions, lane_configs=lane_configs)
    return strict_jsonable(
        {
            "schema_version": CRYPTO_OPTIONS_LANE_SYSTEM_SCHEMA_VERSION,
            "generated_at_utc": now.isoformat(),
            "phase": "phase1_research_and_lane_readiness",
            "order_sizing_matrix": order_matrix,
            "profile_grade_cache": grade_cache,
            "research_premise_report": premise_report,
            "lane_configs": lane_configs,
            "lane_decision_report": decisions,
            "phase2_execution_plan": execution_plan,
            "lane_comparison": comparison,
            "unified_signal_router_design": router,
            "boundary": {
                "orders_allowed": False,
                "live_execution_requires": "codex_tool/run_crypto_options_live_micro_executor.py explicit live flags and per-lane ledger gates",
                "production_unsupervised_allowed": False,
            },
        }
    )


def build_parallel_lane_execution_plan(
    lane_decision_report: dict[str, Any],
    *,
    lane_configs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    lane_configs = lane_configs or default_five_lane_configs()
    configs = {str(row.get("lane_id") or ""): row for row in lane_configs}
    lanes = []
    for decision in lane_decision_report.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        lane_id = str(decision.get("lane_id") or "")
        config = configs.get(lane_id) or {}
        selected = decision.get("selected_candidate") or {}
        budget = config.get("budget_policy") or {}
        order_style = str(config.get("order_style") or "")
        execution_style = "market" if "market_buy" in order_style or "market_buy_or_sell" in order_style else "limit"
        status = "ready_for_supervised_live_tick" if decision.get("status") == "accepted" else "blocked"
        blockers = list(decision.get("blockers") or [])
        if status == "ready_for_supervised_live_tick" and not selected.get("token_id"):
            status = "blocked"
            blockers.append("selected_candidate_missing_token_id")
        if status == "ready_for_supervised_live_tick" and not selected.get("event_slug"):
            status = "blocked"
            blockers.append("selected_candidate_missing_event_slug")
        protocol_budget = {
            "max_position_cost_usd": float((decision.get("risk_state") or {}).get("current_max_ticket_notional_usd") or 2.0),
            "max_test_trades_before_review": 60,
            "hard_stop_loss_usd": float(budget.get("lane_loss_stop_usd") or 10.0),
            "hard_stop_full_losses": 60,
            "global_loss_stop_usd": float(budget.get("global_loss_stop_usd") or 50.0),
            "max_open_exposure_per_lane_usd": float(budget.get("max_open_exposure_per_lane_usd") or 6.0),
        }
        lanes.append(
            {
                "lane_id": lane_id,
                "status": status,
                "blockers": blockers,
                "warnings": list(decision.get("warnings") or []),
                "order_style": order_style,
                "execution_style": execution_style,
                "order_type": "FAK",
                "execution_side": "BUY",
                "price_slippage_cents": 3,
                "protocol_status": "approved_protocol_ready_for_separate_execution_design",
                "protocol_budget": protocol_budget,
                "selected_candidate": selected if status == "ready_for_supervised_live_tick" else None,
                "executor_command_template": _executor_command_template(
                    lane_id=lane_id,
                    execution_style=execution_style,
                    price_slippage_cents=3,
                ),
            }
        )
    return {
        "schema_version": "crypto_options_parallel_lane_execution_plan_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "live_orders_allowed_by_this_artifact": False,
        "requires_explicit_live_flags": True,
        "lanes": lanes,
        "ready_lane_count": sum(1 for row in lanes if row["status"] == "ready_for_supervised_live_tick"),
    }


def build_lane_execution_packets(five_lane_package: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Build per-lane execution packets consumable by the isolated live executor."""

    now = now or _parse_time(five_lane_package.get("generated_at_utc")) or datetime.now(timezone.utc)
    plan_rows = {
        str(row.get("lane_id") or ""): row
        for row in ((five_lane_package.get("phase2_execution_plan") or {}).get("lanes") or [])
        if isinstance(row, dict)
    }
    decisions = {
        str(row.get("lane_id") or ""): row
        for row in ((five_lane_package.get("lane_decision_report") or {}).get("decisions") or [])
        if isinstance(row, dict)
    }
    packets = []
    for lane_id, plan in sorted(plan_rows.items()):
        decision = decisions.get(lane_id) or {}
        status = "ready" if plan.get("status") == "ready_for_supervised_live_tick" else "blocked"
        selected = plan.get("selected_candidate") or decision.get("selected_candidate") or None
        blockers = list(dict.fromkeys([*(plan.get("blockers") or []), *(decision.get("blockers") or [])]))
        warnings = list(dict.fromkeys([*(plan.get("warnings") or []), *(decision.get("warnings") or [])]))
        strategy_id = f"issue47_{lane_id}"
        protocol = _lane_protocol_payload(
            lane_id=lane_id,
            strategy_id=strategy_id,
            plan=plan,
            selected=selected if isinstance(selected, dict) else None,
            now=now,
        )
        monitor = _lane_monitor_payload(
            lane_id=lane_id,
            strategy_id=strategy_id,
            selected=selected if status == "ready" and isinstance(selected, dict) else None,
            decision_id=decision.get("decision_id"),
            now=now,
        )
        ledger = {
            "schema_version": "crypto_options_live_execution_ledger_v1",
            "lane_id": lane_id,
            "created_at_utc": now.isoformat(),
            "entries": [],
        }
        packets.append(
            {
                "schema_version": LANE_EXECUTION_PACKET_SCHEMA_VERSION,
                "generated_at_utc": now.isoformat(),
                "lane_id": lane_id,
                "status": status,
                "blockers": blockers,
                "warnings": warnings,
                "decision_id": decision.get("decision_id"),
                "protocol": protocol,
                "monitor": monitor,
                "initial_live_ledger": ledger,
                "executor_command": plan.get("executor_command_template"),
            }
        )
    return strict_jsonable(
        {
            "schema_version": "crypto_options_lane_execution_packets_v1",
            "generated_at_utc": now.isoformat(),
            "packet_count": len(packets),
            "ready_packet_count": sum(1 for packet in packets if packet.get("status") == "ready"),
            "packets": packets,
            "boundary": {
                "orders_allowed": False,
                "live_submission_interface": "codex_tool/run_crypto_options_live_micro_executor.py",
            },
        }
    )


def write_lane_execution_packets(
    five_lane_package: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write per-lane loop directories for the approved isolated executor."""

    packet_bundle = build_lane_execution_packets(five_lane_package, now=now)
    generated = _parse_time(packet_bundle.get("generated_at_utc")) or datetime.now(timezone.utc)
    day = generated.strftime("%Y-%m-%d")
    root = (
        Path(output_dir)
        if output_dir
        else resolve_shared_root() / "artifacts" / "crypto-options-research" / "five-lane-live" / day
    )
    root.mkdir(parents=True, exist_ok=True)
    written_packets = []
    for packet in packet_bundle.get("packets") or []:
        if not isinstance(packet, dict):
            continue
        lane_id = str(packet.get("lane_id") or "")
        lane_dir = root / _lane_loop_dir_name(lane_id)
        lane_dir.mkdir(parents=True, exist_ok=True)
        protocol_path = lane_dir / "protocol.json"
        monitor_path = lane_dir / "monitor.json"
        state_path = lane_dir / "manual_loop_state.json"
        ledger_path = lane_dir / "live_execution_ledger.json"
        protocol_path.write_text(json.dumps(packet.get("protocol") or {}, indent=2, sort_keys=True), encoding="utf-8")
        monitor_path.write_text(json.dumps(packet.get("monitor") or {}, indent=2, sort_keys=True), encoding="utf-8")
        if not ledger_path.exists():
            ledger_path.write_text(json.dumps(packet.get("initial_live_ledger") or {}, indent=2, sort_keys=True), encoding="utf-8")
        state = {
            "schema_version": "crypto_options_lane_execution_loop_state_v1",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "lane_id": lane_id,
            "lane_status": packet.get("status"),
            "last_monitor_artifact": str(monitor_path),
            "protocol_artifact": str(protocol_path),
            "ledger_artifact": str(ledger_path),
            "monitor_status": (packet.get("monitor") or {}).get("monitor_status"),
            "eligible_manual_candidate_count": (packet.get("monitor") or {}).get("eligible_manual_candidate_count"),
            "executor_command": packet.get("executor_command"),
            "blockers": packet.get("blockers") or [],
            "warnings": packet.get("warnings") or [],
        }
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        written_packets.append(
            {
                "lane_id": lane_id,
                "status": packet.get("status"),
                "loop_dir": str(lane_dir),
                "protocol": str(protocol_path),
                "monitor": str(monitor_path),
                "state": str(state_path),
                "ledger": str(ledger_path),
                "executor_command": packet.get("executor_command"),
                "blockers": packet.get("blockers") or [],
                "warnings": packet.get("warnings") or [],
            }
        )
    bundle_path = root / "packets.json"
    output = {
        **packet_bundle,
        "output_root": str(root),
        "written_packets": written_packets,
    }
    bundle_path.write_text(json.dumps(strict_jsonable(output), indent=2, sort_keys=True), encoding="utf-8")
    output["artifact_json"] = str(bundle_path)
    return strict_jsonable(output)


def write_five_lane_system_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    generated = _parse_time(payload.get("generated_at_utc")) or datetime.now(timezone.utc)
    day = generated.strftime("%Y-%m-%d")
    stamp = generated.strftime("%Y%m%dT%H%M%SZ")
    root = (
        Path(output_dir)
        if output_dir
        else resolve_shared_root() / "artifacts" / "crypto-options-research" / "five-lane-system" / day
    )
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_five_lane_system_{stamp}.json"
    md_path = root / f"crypto_options_five_lane_system_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_five_lane_system_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_five_lane_system_markdown(payload: dict[str, Any]) -> str:
    comparison = payload.get("lane_comparison") or {}
    lines = [
        "# Crypto Options Five-Lane System",
        "",
        f"- Generated: `{payload.get('generated_at_utc')}`",
        f"- Phase: `{payload.get('phase')}`",
        f"- Global comparison PnL: `{comparison.get('global_net_realized_pnl_usd')}`",
        f"- Router status: `{(payload.get('unified_signal_router_design') or {}).get('router_status')}`",
        "",
        "## Lane Readiness",
        "",
    ]
    decisions = ((payload.get("lane_decision_report") or {}).get("decisions") or [])
    for row in decisions:
        lines.append(
            "- `{lane}` status=`{status}` ev=`{ev}` action=`{action}` blockers=`{blockers}`".format(
                lane=row.get("lane_id"),
                status=row.get("status"),
                ev=_fmt(row.get("expected_value_per_share")),
                action=row.get("planned_action"),
                blockers=",".join(row.get("blockers") or []),
            )
        )
    lines.extend(["", "## Research Premises", ""])
    premise = payload.get("research_premise_report") or {}
    lines.append(f"- Time buckets: `{len((premise.get('time_of_entry') or {}).get('buckets') or [])}`")
    lines.append(f"- Price buckets: `{len((premise.get('price_bucket_ev') or {}).get('buckets') or [])}`")
    lines.append(f"- Followability profiles: `{(premise.get('profile_followability') or {}).get('profile_count')}`")
    lines.extend(["", "## Boundary", "", (payload.get("boundary") or {}).get("live_execution_requires", "")])
    return "\n".join(lines).rstrip() + "\n"


def _lane_decision(
    lane: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    active_signals: list[dict[str, Any]],
    grade_cache: dict[str, Any],
    ledger: dict[str, Any] | None,
    algo_signals: list[dict[str, Any]],
    now: datetime,
    source_monitor_status: Any = None,
) -> dict[str, Any]:
    lane_id = str(lane.get("lane_id") or "")
    risk = lane_risk_state(normalize_lane_ledger(ledger, lane_id=lane_id), lane.get("budget_policy") or {})
    blockers: list[str] = []
    warnings: list[str] = []
    if risk["lane_loss_stop_hit"]:
        blockers.append("lane_loss_stop_hit")
    if risk["open_exposure_available_usd"] <= 0.0:
        blockers.append("lane_open_exposure_cap_reached")
    selected = _select_lane_candidate(lane, candidates, active_signals=active_signals, grade_cache=grade_cache, algo_signals=algo_signals)
    if selected is None:
        blockers.append("no_lane_candidate")
        ev = None
    else:
        selected = _enrich_candidate_from_current_rows(selected, candidates)
        monitor_assessment = _candidate_monitor_assessment(selected, lane=lane, source_monitor_status=source_monitor_status)
        blockers.extend(monitor_assessment["blockers"])
        warnings.extend(monitor_assessment["warnings"])
        ev = estimate_candidate_expected_value(selected, lane=lane, grade_cache=grade_cache, now=now)
        max_age = _to_float((lane.get("entry_rules") or {}).get("max_signal_age_seconds"))
        modular_age = _to_float((lane.get("entry_rules") or {}).get("modular_probe_max_signal_age_seconds"))
        modular_enabled = bool(((lane.get("entry_rules") or {}).get("modular_probe") or {}).get("enabled"))
        active_max_age = modular_age if modular_enabled and modular_age is not None else max_age
        if active_max_age is not None and _candidate_signal_age(selected, now=now) > active_max_age:
            blockers.append("signal_age_above_lane_max")
        late_second = _to_float((lane.get("entry_rules") or {}).get("late_event_seconds"))
        late_min_ev = _to_float((lane.get("entry_rules") or {}).get("late_event_min_expected_value_per_share"))
        if (
            late_second is not None
            and late_min_ev is not None
            and ev["event_second"] is not None
            and float(ev["event_second"]) >= late_second
            and ev["expected_value_per_share"] < late_min_ev
        ):
            if modular_enabled:
                warnings.append("modular_probe_allows_late_event_expected_value_below_lane_min")
            else:
                blockers.append("late_event_expected_value_below_lane_min")
        if ev["expected_value_per_share"] < float((lane.get("entry_rules") or {}).get("min_expected_value_per_share") or -999.0):
            if modular_enabled:
                warnings.append("modular_probe_allows_expected_value_below_lane_min")
            else:
                blockers.append("expected_value_below_lane_min")
        max_conflict = _to_float((lane.get("entry_rules") or {}).get("max_conflict_ratio"))
        modular_conflict = _to_float((lane.get("entry_rules") or {}).get("modular_probe_max_conflict_ratio"))
        active_conflict = modular_conflict if modular_enabled and modular_conflict is not None else max_conflict
        if active_conflict is not None and (_to_float(selected.get("conflict_ratio")) or 0.0) > active_conflict:
            blockers.append("conflict_ratio_above_lane_max")
        ev = _apply_lane_ticket_sizing(ev, selected=selected, risk=risk, lane=lane)
        if ev["estimated_ticket_notional_usd"] > risk["current_max_ticket_notional_usd"] + 1e-9:
            blockers.append("ticket_notional_above_lane_cap")
        if ev["estimated_ticket_notional_usd"] < 1.0 - 1e-9:
            blockers.append("ticket_notional_below_exchange_minimum")
        if not selected.get("token_id"):
            blockers.append("selected_candidate_missing_token_id")
        selected = {**selected, "ev": ev}
    status = "accepted" if selected is not None and not blockers else "blocked"
    planned_action = _planned_action(lane, selected)
    return {
        "decision_id": _decision_id(lane_id, selected, now),
        "lane_id": lane_id,
        "generated_at_utc": now.isoformat(),
        "status": status,
        "planned_action": planned_action if status == "accepted" else "none",
        "order_style": lane.get("order_style"),
        "selected_candidate": selected,
        "expected_value_per_share": (selected.get("ev") or {}).get("expected_value_per_share") if selected else None,
        "estimated_ticket_notional_usd": (selected.get("ev") or {}).get("estimated_ticket_notional_usd") if selected else None,
        "risk_state": risk,
        "blockers": blockers,
        "warnings": warnings,
        "orders_allowed": False,
    }


def estimate_candidate_expected_value(
    candidate: dict[str, Any],
    *,
    lane: dict[str, Any],
    grade_cache: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    ticket = candidate.get("manual_execution_ticket") if isinstance(candidate.get("manual_execution_ticket"), dict) else {}
    price_source = (
        "best_ask"
        if _to_float(candidate.get("best_ask")) is not None
        else "manual_execution_ticket.observed_best_ask"
        if _to_float(ticket.get("observed_best_ask")) is not None
        else "observed_execution_price"
        if _to_float(candidate.get("observed_execution_price")) is not None
        else "price"
    )
    price = min(
        0.99,
        max(
            0.01,
            _to_float(candidate.get("best_ask"))
            or _to_float(ticket.get("observed_best_ask"))
            or _to_float(candidate.get("observed_execution_price"))
            or _to_float(candidate.get("price"))
            or 0.5,
        ),
    )
    fee = _taker_fee_per_share(price)
    confidence = _candidate_confidence(candidate, grade_cache=grade_cache)
    signal_age = _candidate_signal_age(candidate, now=now)
    latency_penalty = min(0.08, max(0.0, signal_age - 30.0) / 300.0 * 0.08)
    conflict_penalty = min(0.12, (_to_float(candidate.get("conflict_ratio")) or 0.0) * 0.18)
    entry_second = _candidate_event_second(candidate)
    late_penalty = 0.03 if entry_second is not None and entry_second >= 180 and confidence < 0.68 else 0.0
    payout_after_fee = 1.0 - price - fee
    entry_cost = price + fee
    expected_value = confidence * payout_after_fee - (1.0 - confidence) * entry_cost - latency_penalty - conflict_penalty - late_penalty
    return {
        "schema_version": "crypto_options_candidate_ev_v1",
        "price": round(price, 6),
        "fee_per_share": round(fee, 6),
        "confidence": round(confidence, 6),
        "payout_after_fee": round(payout_after_fee, 6),
        "entry_cost": round(entry_cost, 6),
        "entry_price_source": price_source,
        "latency_penalty": round(latency_penalty, 6),
        "conflict_penalty": round(conflict_penalty, 6),
        "late_event_penalty": round(late_penalty, 6),
        "expected_value_per_share": round(expected_value, 6),
        "estimated_ticket_notional_usd": round(5.0 * price, 6),
        "event_second": entry_second,
        "lane_id": lane.get("lane_id"),
    }


def _apply_lane_ticket_sizing(
    ev: dict[str, Any],
    *,
    selected: dict[str, Any],
    risk: dict[str, Any],
    lane: dict[str, Any],
) -> dict[str, Any]:
    """Convert per-share EV into a lane-sized ticket under the active cap."""

    price = _to_float(ev.get("price")) or _to_float(selected.get("best_ask") or selected.get("price")) or 0.5
    entry_cost = _to_float(ev.get("entry_cost")) or price + _taker_fee_per_share(price)
    cap = _to_float(risk.get("current_max_ticket_notional_usd")) or _to_float((lane.get("budget_policy") or {}).get("max_ticket_notional_usd")) or 2.0
    max_reference_shares = _to_float((lane.get("sizing_policy") or {}).get("max_position_shares_reference")) or 5.0
    absolute_share_cap = _to_float((lane.get("sizing_policy") or {}).get("absolute_max_position_shares")) or 250.0
    requested_shares = _to_float(((selected.get("manual_execution_ticket") or {}) if isinstance(selected.get("manual_execution_ticket"), dict) else {}).get("shares"))
    target_shares = min(max_reference_shares, requested_shares or max_reference_shares, max(0.0, cap / max(entry_cost, 0.01)))
    target_shares = math.floor(target_shares * 100.0) / 100.0
    sizing_mode = "cap_notional_before_share_target"
    estimated_net_cost = target_shares * entry_cost
    estimated_gross_cost = target_shares * price
    min_notional = 1.0
    if estimated_net_cost < min_notional - 1e-9 and cap >= min_notional:
        min_shares = math.ceil((min_notional / max(entry_cost, 0.01)) * 100.0) / 100.0
        max_affordable_shares = max(0.0, cap / max(entry_cost, 0.01))
        if min_shares <= max_affordable_shares + 1e-9 and min_shares <= absolute_share_cap + 1e-9:
            target_shares = min_shares
            estimated_net_cost = target_shares * entry_cost
            estimated_gross_cost = target_shares * price
            sizing_mode = "cheap_contract_expanded_to_exchange_min_notional"
    updated = dict(ev)
    updated.update(
        {
            "current_ticket_cap_usd": round(cap, 6),
            "target_shares": round(target_shares, 6),
            "estimated_ticket_gross_usd": round(estimated_gross_cost, 6),
            "estimated_ticket_notional_usd": round(estimated_net_cost, 6),
            "min_order_notional_usd": min_notional,
            "sizing_mode": sizing_mode,
        }
    )
    return updated


def _select_lane_candidate(
    lane: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    active_signals: list[dict[str, Any]],
    grade_cache: dict[str, Any],
    algo_signals: list[dict[str, Any]],
) -> dict[str, Any] | None:
    selector = str((lane.get("entry_rules") or {}).get("selector") or "")
    if selector == "best_single_profile_first_signal":
        return _single_best_profile_candidate(active_signals, grade_cache=grade_cache)
    if selector == "top_profile_consensus":
        return _top_consensus_candidate(candidates, lane=lane)
    if selector == "positive_ev_quality_overlay":
        return _best_ev_candidate(candidates, lane=lane, grade_cache=grade_cache)
    if selector == "dynamic_event_manager":
        return _best_ev_candidate(candidates, lane=lane, grade_cache=grade_cache)
    if selector == "algo_primary_profile_confirmed":
        return _algo_confirmed_candidate(candidates, algo_signals=algo_signals, lane=lane)
    return None


def _enrich_candidate_from_current_rows(selected: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    event = str(selected.get("event_slug") or "")
    outcome = str(selected.get("outcome") or "")
    for candidate in candidates:
        if str(candidate.get("event_slug") or "") == event and str(candidate.get("outcome") or "") == outcome:
            merged = dict(candidate)
            merged.update({key: value for key, value in selected.items() if value is not None})
            merged.setdefault("token_id", candidate.get("token_id"))
            return merged
    return selected


def _single_best_profile_candidate(active_signals: list[dict[str, Any]], *, grade_cache: dict[str, Any]) -> dict[str, Any] | None:
    profiles = {row["profile_name"]: row for row in grade_cache.get("profiles") or [] if row.get("profile_name")}
    eligible = []
    for signal in active_signals:
        profile = profiles.get(str(signal.get("profile_name") or ""))
        if not profile:
            continue
        if str(profile.get("dynamic_grade") or profile.get("base_grade") or "") != "S":
            continue
        eligible.append(_candidate_from_signal(signal, support_profile=profile))
    if not eligible:
        fallback = [signal for signal in active_signals if str(signal.get("profile_name") or "").lower() == "bonereaper"]
        eligible = [_candidate_from_signal(signal) for signal in fallback]
    return sorted(eligible, key=lambda row: (-(row.get("profile_score") or 0), row.get("signal_at_utc") or ""), reverse=False)[0] if eligible else None


def _top_consensus_candidate(candidates: list[dict[str, Any]], *, lane: dict[str, Any]) -> dict[str, Any] | None:
    rules = lane.get("entry_rules") or {}
    wanted = set(rules.get("confirming_grades") or ["S", "A"])
    min_profiles = int(rules.get("min_confirming_profiles") or 2)
    eligible = []
    for candidate in candidates:
        signals = [row for row in candidate.get("supporting_signals") or [] if isinstance(row, dict)]
        supporting = {str(row.get("profile_name") or row.get("proxy_wallet") or "") for row in signals if str(row.get("profile_grade") or "") in wanted}
        single = any(str(row.get("profile_grade") or "") == "S" for row in signals)
        if single or len(supporting) >= min_profiles:
            eligible.append(candidate)
    return _sort_candidate_rows(eligible)[0] if eligible else None


def _best_ev_candidate(candidates: list[dict[str, Any]], *, lane: dict[str, Any], grade_cache: dict[str, Any]) -> dict[str, Any] | None:
    if not candidates:
        return None
    scored = [
        (estimate_candidate_expected_value(candidate, lane=lane, grade_cache=grade_cache), candidate)
        for candidate in candidates
    ]
    scored = [row for row in scored if row[0]["expected_value_per_share"] > 0.0]
    if scored:
        return sorted(scored, key=lambda item: item[0]["expected_value_per_share"], reverse=True)[0][1]
    if bool(((lane.get("entry_rules") or {}).get("modular_probe") or {}).get("enabled")):
        all_scored = [
            (estimate_candidate_expected_value(candidate, lane=lane, grade_cache=grade_cache), candidate)
            for candidate in candidates
        ]
        return sorted(all_scored, key=lambda item: item[0]["expected_value_per_share"], reverse=True)[0][1] if all_scored else None
    return None


def _algo_confirmed_candidate(
    candidates: list[dict[str, Any]],
    *,
    algo_signals: list[dict[str, Any]],
    lane: dict[str, Any],
) -> dict[str, Any] | None:
    if not algo_signals and bool((lane.get("entry_rules") or {}).get("allow_orderbook_favorite_proxy_for_modular_probe")):
        algo_signals = _orderbook_favorite_algo_signals(candidates)
    if not algo_signals:
        return None
    confirmed = []
    for algo in algo_signals:
        event = str(algo.get("event_slug") or "")
        outcome = str(algo.get("outcome") or "")
        for candidate in candidates:
            if str(candidate.get("event_slug") or "") == event and str(candidate.get("outcome") or "") == outcome:
                merged = dict(candidate)
                merged["algo_signal"] = algo
                confirmed.append(merged)
    return _sort_candidate_rows(confirmed)[0] if confirmed else None


def _orderbook_favorite_algo_signals(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_event: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        event = str(candidate.get("event_slug") or "")
        if event:
            by_event.setdefault(event, []).append(candidate)
    signals = []
    for event, rows in by_event.items():
        priced = [
            row
            for row in rows
            if _to_float(row.get("best_ask") or row.get("observed_execution_price") or row.get("price")) is not None
        ]
        if not priced:
            continue
        selected = max(priced, key=lambda row: _to_float(row.get("best_ask") or row.get("observed_execution_price") or row.get("price")) or 0.0)
        signals.append(
            {
                "strategy_id": "orderbook_favorite_proxy_modular_probe",
                "event_slug": event,
                "outcome": selected.get("outcome"),
                "source": "orderbook_favorite_proxy",
                "modular_probe_only": True,
            }
        )
    return signals


def _current_candidate_rows(profile_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in (profile_report.get("eligible_manual_candidates") or profile_report.get("observed_candidates") or [])
        if isinstance(row, dict)
    ]
    if not rows and isinstance(profile_report.get("profile_signal_report"), dict):
        nested = profile_report["profile_signal_report"]
        rows = [dict(row) for row in nested.get("aggregated_candidates") or [] if isinstance(row, dict)]
    if not rows:
        rows = [dict(row) for row in profile_report.get("aggregated_candidates") or [] if isinstance(row, dict)]
    for row in rows:
        row.setdefault("price", row.get("signal_reference_price") or row.get("observed_execution_price") or row.get("best_ask"))
        row.setdefault("observed_execution_price", row.get("best_ask") or row.get("signal_reference_price") or row.get("price"))
        row.setdefault("outcome", row.get("effective_outcome"))
        ticket = row.get("manual_execution_ticket") if isinstance(row.get("manual_execution_ticket"), dict) else {}
        if ticket.get("token_id"):
            row.setdefault("token_id", ticket.get("token_id"))
    return rows


def _active_signal_rows(profile_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in profile_report.get("active_signals") or [] if isinstance(row, dict)]
    if rows:
        return rows
    nested = profile_report.get("profile_signal_report") if isinstance(profile_report.get("profile_signal_report"), dict) else {}
    return [dict(row) for row in nested.get("active_signals") or [] if isinstance(row, dict)]


def _candidate_from_signal(signal: dict[str, Any], support_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_signal = signal.get("raw_signal") if isinstance(signal.get("raw_signal"), dict) else {}
    return {
        "event_slug": signal.get("event_slug"),
        "market_slug": signal.get("market_slug"),
        "symbol": signal.get("symbol"),
        "outcome": signal.get("effective_outcome"),
        "token_id": signal.get("token_id") or raw_signal.get("asset"),
        "condition_id": signal.get("condition_id") or raw_signal.get("conditionId"),
        "price": signal.get("price"),
        "observed_execution_price": signal.get("price"),
        "signal_at_utc": signal.get("signal_at_utc"),
        "supporting_profiles": [signal.get("profile_name") or signal.get("proxy_wallet")],
        "supporting_signals": [signal],
        "profile_score": (support_profile or {}).get("dynamic_score") or signal.get("profile_score"),
        "conflict_ratio": 0.0,
        "selection_source": "active_signal_direct",
    }


def _candidate_monitor_assessment(
    candidate: dict[str, Any],
    *,
    lane: dict[str, Any],
    source_monitor_status: Any = None,
) -> dict[str, list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    failed = [str(item) for item in candidate.get("failed_checks") or [] if item]
    modular = ((lane.get("entry_rules") or {}).get("modular_probe") or {})
    ignored = set(str(item) for item in modular.get("ignore_monitor_failed_checks") or [])
    fatal_failed = {
        "monitor_quote_stale",
        "best_ask_missing",
        "quote_fetch_failed",
        "spread_above_max",
        "spread_above_max_or_missing",
        "depth_top3_ask_size_below_min",
        "depth_top3_ask_size_below_min_or_missing",
        "time_remaining_below_min",
        "time_remaining_above_max",
        "time_remaining_outside_gate",
        "ticket_cost_above_budget",
        "signal_to_ask_slippage_above_max_or_missing",
        "candidate_not_in_active_target_set",
    }
    for item in failed:
        if item in ignored:
            warnings.append(f"modular_probe_ignores_{item}")
            continue
        if item == "spread_above_max_or_missing" and _has_executable_buy_quote(candidate):
            warnings.append("spread_missing_but_executable_ask_present")
            continue
        if item in fatal_failed:
            blockers.append(f"candidate_failed_{item}")
    if _to_float(candidate.get("best_ask")) is None:
        blockers.append("candidate_failed_best_ask_missing")
    if _to_float(candidate.get("spread")) is None and not _has_executable_buy_quote(candidate):
        blockers.append("candidate_failed_spread_above_max_or_missing")
    if _to_float(candidate.get("depth_top3_ask_size")) is None:
        blockers.append("candidate_failed_depth_top3_ask_size_below_min_or_missing")
    if _to_float(candidate.get("signal_to_ask_slippage_cents")) is None:
        blockers.append("candidate_failed_signal_to_ask_slippage_above_max_or_missing")
    if any(item in {"aggregate_opposite_signal_conflict", "opposite_signal_conflict"} for item in failed):
        warnings.append("candidate_has_opposite_signal_conflict")
    if source_monitor_status and str(source_monitor_status) != "eligible_manual_candidate_present" and not blockers and not warnings:
        if bool(modular.get("enabled")) and candidate.get("selection_source") == "active_signal_direct":
            warnings.append("modular_probe_uses_active_signal_without_monitor_eligibility")
        else:
            blockers.append("source_monitor_not_eligible")
    return {"blockers": list(dict.fromkeys(blockers)), "warnings": list(dict.fromkeys(warnings))}


def _has_executable_buy_quote(candidate: dict[str, Any]) -> bool:
    return (
        _to_float(candidate.get("best_ask")) is not None
        and _to_float(candidate.get("depth_top3_ask_size")) is not None
        and _to_float(candidate.get("signal_to_ask_slippage_cents")) is not None
    )


def _profile_backtest_attribution(profile_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for trade in _backtest_trades(profile_report):
        profiles = [str(item) for item in trade.get("supporting_profiles") or [] if item]
        if not profiles:
            continue
        share = float(trade.get("pnl_net") or 0.0) / len(profiles)
        for profile in profiles:
            row = stats.setdefault(profile, {"trade_count": 0, "wins": 0, "pnl_net": 0.0, "prices": []})
            row["trade_count"] += 1
            row["wins"] += 1 if trade.get("win") else 0
            row["pnl_net"] += share
            if _to_float(trade.get("price")) is not None:
                row["prices"].append(float(trade.get("price")))
    output: dict[str, dict[str, Any]] = {}
    for key, row in stats.items():
        trade_count = int(row["trade_count"])
        prices = row.get("prices") or []
        output[key] = {
            "trade_count": trade_count,
            "win_rate": round(float(row["wins"]) / trade_count, 6) if trade_count else None,
            "pnl_net": round(float(row["pnl_net"]), 6),
            "avg_signal_price": round(sum(prices) / len(prices), 6) if prices else None,
        }
    return output


def _backtest_trades(profile_report: dict[str, Any]) -> list[dict[str, Any]]:
    backtest = profile_report.get("backtest") or {}
    rows = [row for row in backtest.get("trades") or [] if isinstance(row, dict)]
    if rows:
        return rows
    best = backtest.get("best_variant") or {}
    return [row for row in best.get("trades") or [] if isinstance(row, dict)]


def _time_of_entry_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for trade in trades:
        second = _candidate_event_second(trade)
        if second is None:
            continue
        rows.append({**trade, "event_second": second})
    buckets = []
    for label, low, high in _time_buckets():
        subset = [row for row in rows if low <= int(row["event_second"]) < high]
        if subset:
            metrics = compute_trade_metrics(pd.DataFrame(_metric_rows(subset)))
            buckets.append({"bucket": label, "start_second": low, "end_second": high, "trade_metrics": metrics})
    return {"sample_count": len(rows), "buckets": buckets}


def _price_bucket_ev_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = []
    for label, low, high in _price_buckets():
        subset = [row for row in trades if low <= (_to_float(row.get("price")) or -1.0) < high]
        if subset:
            metrics = compute_trade_metrics(pd.DataFrame(_metric_rows(subset)))
            avg_price = sum(float(row.get("price") or 0.0) for row in subset) / len(subset)
            buckets.append(
                {
                    "bucket": label,
                    "low": low,
                    "high": high,
                    "avg_price": round(avg_price, 6),
                    "trade_metrics": metrics,
                    "weighted_win_rate_comment": "Evaluate against breakeven price plus fee, not raw 70% target.",
                }
            )
    return {"sample_count": len(trades), "buckets": buckets}


def _signal_aging_report() -> dict[str, Any]:
    return {
        "scenarios": [
            {"delay_seconds": item, "status": "requires_live_quote_or_capture_replay"}
            for item in (0, 15, 30, 60, 120, 240)
        ],
        "note": "Signal aging cannot be validated from settlement-only history; it must be paired with captured executable quotes.",
    }


def _profile_followability_report(grade_cache: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for profile in grade_cache.get("profiles") or []:
        timing = profile.get("timing_stats") or {}
        ev = profile.get("ev_stats") or {}
        rows.append(
            {
                "profile_key": profile.get("profile_key"),
                "profile_name": profile.get("profile_name"),
                "dynamic_grade": profile.get("dynamic_grade"),
                "active_event_status": profile.get("active_event_status"),
                "historical_signal_count": timing.get("signal_count"),
                "median_event_second": timing.get("median_event_second"),
                "attributed_trade_count": ev.get("trade_count"),
                "attributed_pnl_net": ev.get("pnl_net"),
            }
        )
    return {"profile_count": len(rows), "profiles": rows}


def _late_event_risk_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    late = [row for row in trades if (_candidate_event_second(row) or -1) >= 180]
    early = [row for row in trades if (_candidate_event_second(row) or 999) < 180]
    return {
        "late_event_second_threshold": 180,
        "early_metrics": compute_trade_metrics(pd.DataFrame(_metric_rows(early))) if early else {},
        "late_metrics": compute_trade_metrics(pd.DataFrame(_metric_rows(late))) if late else {},
        "gate": "Late low-confidence favorite entries require positive EV and a higher EV threshold.",
    }


def _timing_stats(signals: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = []
    for signal in signals:
        second = _candidate_event_second(signal)
        if second is not None:
            seconds.append(second)
    if not seconds:
        return {"signal_count": 0, "median_event_second": None, "early_0_30_share": None, "late_180_300_share": None}
    ordered = sorted(seconds)
    return {
        "signal_count": len(seconds),
        "avg_event_second": round(sum(seconds) / len(seconds), 3),
        "median_event_second": ordered[len(ordered) // 2],
        "early_0_30_share": round(sum(1 for value in seconds if value < 30) / len(seconds), 6),
        "late_180_300_share": round(sum(1 for value in seconds if value >= 180) / len(seconds), 6),
    }


def _live_grade_modifier(active_signals: list[dict[str, Any]], *, timing: dict[str, Any]) -> float:
    if not active_signals:
        return 0.0
    modifier = min(6.0, len(active_signals) * 1.5)
    median_second = _to_float(timing.get("median_event_second"))
    if median_second is not None:
        if median_second <= 30:
            modifier += 2.0
        elif median_second >= 180:
            modifier -= 2.0
    prices = [_to_float(row.get("price")) for row in active_signals if _to_float(row.get("price")) is not None]
    if prices and sum(1 for price in prices if price >= 0.70) == len(prices):
        modifier -= 1.5
    return modifier


def _inverse_signal_eligible(snapshot: dict[str, Any], attribution: dict[str, Any] | None) -> bool:
    if snapshot.get("polarity") == "inverse" and snapshot.get("grade") in {"D", "E"}:
        return True
    if not attribution:
        return False
    return int(attribution.get("trade_count") or 0) >= 5 and float(attribution.get("pnl_net") or 0.0) < 0.0


def _candidate_confidence(candidate: dict[str, Any], *, grade_cache: dict[str, Any]) -> float:
    grade_base = {"S": 0.72, "A": 0.66, "B": 0.59, "C": 0.53, "D": 0.48, "E": 0.44, "U": 0.50}
    grades = []
    for signal in candidate.get("supporting_signals") or []:
        if isinstance(signal, dict):
            grades.append(str(signal.get("profile_grade") or "U"))
    if not grades:
        grades = [str(candidate.get("best_supporting_grade") or "U")]
    confidence = sum(grade_base.get(grade, 0.5) for grade in grades) / len(grades)
    support_count = len(set(candidate.get("supporting_profiles") or []))
    confidence += min(0.07, max(0, support_count - 1) * 0.025)
    confidence -= min(0.08, (_to_float(candidate.get("conflict_ratio")) or 0.0) * 0.12)
    return max(0.35, min(0.82, confidence))


def _candidate_signal_age(candidate: dict[str, Any], *, now: datetime) -> float:
    ages = []
    for signal in candidate.get("supporting_signals") or []:
        age = _to_float(signal.get("age_seconds")) if isinstance(signal, dict) else None
        if age is not None:
            ages.append(age)
    if ages:
        return min(ages)
    ts = _parse_time(candidate.get("signal_at_utc"))
    return max(0.0, (now - ts).total_seconds()) if ts else 999.0


def _candidate_event_second(candidate: dict[str, Any]) -> int | None:
    event_slug = str(candidate.get("event_slug") or candidate.get("market_slug") or "")
    match = None
    import re

    found = re.findall(r"(\d{10})", event_slug)
    if found:
        match = int(found[-1])
    if match is None:
        return None
    signal_ts = _parse_time(candidate.get("signal_at_utc") or candidate.get("observed_at"))
    if signal_ts is None:
        for signal in candidate.get("supporting_signals") or []:
            if isinstance(signal, dict):
                signal_ts = _parse_time(signal.get("signal_at_utc"))
                if signal_ts:
                    break
    if signal_ts is None:
        return None
    return int(max(0, min(299, signal_ts.timestamp() - match)))


def _metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        output.append(
            {
                "pnl_net": _to_float(row.get("pnl_net") or row.get("realized_pnl_net_usd")) or 0.0,
                "return": _to_float(row.get("pnl_net") or row.get("realized_pnl_net_usd")) or 0.0,
                "win": bool(row.get("win") if "win" in row else row.get("settlement_win")),
                "sample_id": row.get("event_slug") or row.get("sample_id"),
            }
        )
    return output


def _latency_slippage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [_to_float(((row.get("submission") or {}).get("latency") or {}).get("submit_total_ms")) for row in rows]
    slips = [_to_float(((row.get("execution_quality") or {}).get("signal_to_realized_slippage_cents"))) for row in rows]
    latencies = [value for value in latencies if value is not None]
    slips = [value for value in slips if value is not None]
    return {
        "avg_submit_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "avg_signal_to_realized_slippage_cents": round(sum(slips) / len(slips), 3) if slips else None,
    }


def _lane_status(metrics: dict[str, Any], risk: dict[str, Any], lane: dict[str, Any]) -> str:
    if risk.get("lane_loss_stop_hit"):
        return "discarded_loss_stop"
    min_trades = int(((lane.get("budget_policy") or {}).get("min_settled_trades_before_discard")) or 8)
    if int(metrics.get("trade_count") or 0) >= min_trades and float(metrics.get("return_sum") or 0.0) < 0.0:
        return "discarded_negative_ev"
    if float(metrics.get("return_sum") or 0.0) > 0.0:
        return "promotion_candidate"
    return "collecting_evidence"


def _promotion_decision(metrics: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    if risk.get("lane_loss_stop_hit"):
        return {"decision": "discard", "reason": "lane_loss_stop_hit"}
    if int(metrics.get("trade_count") or 0) >= 8 and float(metrics.get("return_sum") or 0.0) < 0.0:
        return {"decision": "discard", "reason": "negative_pnl_after_min_sample"}
    if float(metrics.get("return_sum") or 0.0) > 0.0 and int(metrics.get("max_sequential_losses") or 0) <= 3:
        return {"decision": "promote_candidate", "reason": "positive_pnl_without_uncontrolled_loss_streak"}
    return {"decision": "continue_supervised_testing", "reason": "insufficient_evidence"}


def _best_comparison_lane(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda row: (
            float(((row.get("trade_metrics") or {}).get("return_sum")) or 0.0),
            float(((row.get("trade_metrics") or {}).get("win_rate")) or 0.0),
        ),
        reverse=True,
    )[0]


def _sort_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -float(row.get("signal_weight") or row.get("profile_score") or 0.0),
            float(row.get("conflict_ratio") or 0.0),
            float(row.get("price") or row.get("observed_execution_price") or 0.99),
        ),
    )


def _planned_action(lane: dict[str, Any], selected: dict[str, Any] | None) -> str:
    if selected is None:
        return "none"
    if str((lane.get("entry_rules") or {}).get("selector") or "") == "dynamic_event_manager":
        return "enter_or_manage"
    return "enter_hold_to_settlement"


def _decision_id(lane_id: str, selected: dict[str, Any] | None, now: datetime) -> str:
    if not selected:
        return f"{lane_id}:{now.isoformat()}:none"
    return ":".join(
        [
            lane_id,
            str(selected.get("event_slug") or ""),
            str(selected.get("outcome") or ""),
            str(selected.get("token_id") or ""),
            now.strftime("%Y%m%dT%H%M%SZ"),
        ]
    )


def _executor_command_template(*, lane_id: str, execution_style: str, price_slippage_cents: int) -> str:
    return (
        "python codex_tool/run_crypto_options_live_micro_executor.py "
        f"--loop-dir <lane_loop_dir_for_{lane_id}> "
        "--order-type FAK "
        f"--execution-style {execution_style} "
        "--execution-side BUY "
        f"--price-slippage-cents {price_slippage_cents} "
        f"--operator codex-{lane_id} "
        f"--reason issue47_{lane_id}_supervised_10usd_lane_test "
        "--execute-live --execution-approved --acknowledge-live-risk"
    )


def _lane_loop_dir_name(lane_id: str) -> str:
    aliases = {
        "single_best_profile_market_follow": "lane1_single_best",
        "top_profile_consensus_market_follow": "lane2_consensus",
        "ev_quality_overlay_limit_hold": "lane3_ev_overlay",
        "dynamic_multi_signal_event_manager": "lane4_dynamic",
        "proprietary_algo_profile_confirmed": "lane5_algo_confirmed",
    }
    return aliases.get(lane_id, lane_id[:48])


def _lane_protocol_payload(
    *,
    lane_id: str,
    strategy_id: str,
    plan: dict[str, Any],
    selected: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    budget = dict(plan.get("protocol_budget") or {})
    gate: dict[str, Any] = {
        "strategy_id": strategy_id,
        "candidate_status_required": "candidate_for_manual_review",
        "quote_age_seconds_max": 5.0,
        "time_remaining_seconds_min": 20.0,
        "time_remaining_seconds_max": 300.0,
    }
    if selected:
        best_ask = _to_float(selected.get("best_ask") or selected.get("observed_execution_price") or selected.get("price"))
        best_bid = _to_float(selected.get("best_bid"))
        spread = _to_float(selected.get("spread"))
        depth = _to_float(selected.get("depth_top3_ask_size"))
        if best_ask is not None:
            gate["best_ask_max"] = round(best_ask + float(plan.get("price_slippage_cents") or 0.0) / 100.0, 6)
        if best_bid is not None:
            gate["best_bid_min"] = round(max(0.01, best_bid - float(plan.get("price_slippage_cents") or 0.0) / 100.0), 6)
        if spread is not None:
            gate["spread_max"] = max(0.03, round(spread, 6))
        if depth is not None:
            gate["depth_top3_ask_size_min"] = min(5.0, max(0.0, round(depth, 6)))
        gate.update(
            {
                "event_slug": selected.get("event_slug"),
                "outcome": selected.get("outcome"),
                "token_id": selected.get("token_id"),
            }
        )
    return {
        "schema_version": "crypto_options_lane_micro_test_protocol_v1",
        "generated_at_utc": now.isoformat(),
        "protocol_status": plan.get("protocol_status") or "blocked",
        "strategy_id": strategy_id,
        "lane_id": lane_id,
        "budget": budget,
        "entry_gates": [gate],
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _lane_monitor_payload(
    *,
    lane_id: str,
    strategy_id: str,
    selected: dict[str, Any] | None,
    now: datetime,
    decision_id: Any = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if selected:
        row = dict(selected)
        ev = row.get("ev") if isinstance(row.get("ev"), dict) else {}
        row["strategy_id"] = strategy_id
        row["lane_id"] = lane_id
        row["lane_decision_id"] = selected.get("lane_decision_id") or selected.get("decision_id") or decision_id
        row.setdefault("status", "candidate_for_manual_review")
        row.setdefault("quote_at_utc", row.get("observed_at") or now.isoformat())
        row.setdefault("monitor_quote_age_seconds", 0.0)
        row["min_order_total_cost"] = ev.get("estimated_ticket_notional_usd") or row.get("estimated_ticket_notional_usd") or row.get("min_order_total_cost")
        ticket = row.get("manual_execution_ticket") if isinstance(row.get("manual_execution_ticket"), dict) else {}
        target_shares = _to_float(ev.get("target_shares"))
        if not ticket:
            price = _to_float(row.get("best_ask") or row.get("observed_execution_price") or row.get("price")) or 0.5
            ticket = {
                "shares": target_shares or 5.0,
                "observed_best_ask": price,
                "token_id": row.get("token_id"),
            }
        elif target_shares is not None:
            ticket = {**ticket, "shares": target_shares}
        row["manual_execution_ticket"] = ticket
        rows.append(row)
    status = "eligible_manual_candidate_present" if rows else "blocked"
    return {
        "schema_version": "crypto_options_lane_monitor_v1",
        "generated_at_utc": now.isoformat(),
        "monitor_status": status,
        "eligible_manual_candidate_count": len(rows),
        "eligible_manual_candidates": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _profile_identity(snapshot: dict[str, Any]) -> dict[str, str | None]:
    profile = snapshot.get("profile") or {}
    wallet = str(profile.get("proxy_wallet") or "").strip().lower() or None
    name = str(profile.get("name") or "").strip() or None
    key = wallet or name or str((snapshot.get("profile_ref") or {}).get("normalized_ref") or "unknown")
    return {"key": key, "name": name, "wallet": wallet}


def _empty_attribution() -> dict[str, Any]:
    return {"trade_count": 0, "win_rate": None, "pnl_net": 0.0, "avg_signal_price": None}


def _price_buckets() -> list[tuple[str, float, float]]:
    return [
        ("0.10-0.25", 0.10, 0.25),
        ("0.25-0.40", 0.25, 0.40),
        ("0.40-0.55", 0.40, 0.55),
        ("0.55-0.70", 0.55, 0.70),
        ("0.70-0.90", 0.70, 0.90),
    ]


def _time_buckets() -> list[tuple[str, int, int]]:
    return [
        ("0-30s", 0, 30),
        ("30-60s", 30, 60),
        ("60-120s", 60, 120),
        ("120-180s", 120, 180),
        ("180-240s", 180, 240),
        ("240-300s", 240, 300),
    ]


def _grade_from_score(score: float) -> str:
    if score >= 82.0:
        return "S"
    if score >= 70.0:
        return "A"
    if score >= 56.0:
        return "B"
    if score >= 42.0:
        return "C"
    if score >= 25.0:
        return "D"
    return "E"


def _taker_fee_per_share(price: float) -> float:
    return 0.07 * float(price) * (1.0 - float(price))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    return f"{number:.4f}"


def _lane_to_dict(lane: LaneConfig) -> dict[str, Any]:
    payload = asdict(lane)
    payload["signal_sources"] = list(lane.signal_sources)
    return payload


__all__ = [
    "CRYPTO_OPTIONS_LANE_SYSTEM_SCHEMA_VERSION",
    "LANE_LEDGER_SCHEMA_VERSION",
    "LANE_EXECUTION_PACKET_SCHEMA_VERSION",
    "LaneBudgetPolicy",
    "LaneConfig",
    "append_lane_decision",
    "build_five_lane_comparison",
    "build_five_lane_system_package",
    "build_lane_execution_packets",
    "build_lane_decision_report",
    "build_order_sizing_matrix",
    "build_parallel_lane_execution_plan",
    "build_profile_grade_cache",
    "build_research_premise_report",
    "build_unified_signal_router_design",
    "default_five_lane_configs",
    "estimate_candidate_expected_value",
    "lane_risk_state",
    "normalize_lane_ledger",
    "render_five_lane_system_markdown",
    "write_lane_execution_packets",
    "write_five_lane_system_artifacts",
]
