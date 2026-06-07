from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from crypto_options_app.pipelines.options.exit_execution_policy import build_exit_execution_decision
from crypto_options_app.pipelines.options.lane_system import build_five_lane_system_package
from crypto_options_app.pipelines.options.live_micro_executor import (
    load_json_object,
    normalize_live_execution_ledger,
    reconcile_live_execution_ledger,
    write_json_object,
)
from crypto_options_app.pipelines.options.promotion_policy import evaluate_global_discovery_cap, evaluate_promotion_state
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.pipelines.options.v2_candidates import (
    V3_SYSTEM_VALIDATION_CANDIDATE_ID,
    V4_CANDIDATE_IDS,
    V4_DIVERGENCE_SCALPING_CANDIDATE_ID,
    V4_HEDGER_REPLICATION_CANDIDATE_ID,
    V4_OUTCOME_PREDICTION_CANDIDATE_ID,
    build_dynamic_family_candidate_decisions,
    build_specialized_candidate_decisions,
    default_v2_candidate_configs,
)
from crypto_options_app.services.crypto_options.v3_policy import classify_quote_health
from crypto_options_app.services.crypto_options.v4_policy import (
    dynamic_cashout_tier,
    eligible_v4_signals,
    evaluate_v4_event_context,
    grade_rank,
    hedger_last_minute_mode,
    is_v4_a_fallback_grade,
    is_v4_live_grade,
    signal_is_hedger_grid,
    signal_is_outcome_predictor,
    signal_profile_name,
    summarize_v4_support,
)


V2_EXECUTION_PACKET_BUNDLE_SCHEMA_VERSION = "crypto_options_v2_execution_packet_bundle_v1"
V2_SERVICE_STATE_SCHEMA_VERSION = "crypto_options_v2_service_state_v1"
_CASHOUT_SHARE_COVERAGE_EPSILON = 0.01
_V3_FORWARD_TEST_ALLOWED_OBSERVED_FAILED_CHECKS = {
    "selected_backtest_variant_not_ready",
    "trade_count_below_target",
    "exploratory_in_sample_profile_filter_requires_forward_validation",
}
_V3_FORWARD_TEST_CONFLICT_PROBE_FAILED_CHECKS = {
    *_V3_FORWARD_TEST_ALLOWED_OBSERVED_FAILED_CHECKS,
    "aggregate_candidate_needs_more_validation",
    "aggregate_opposite_signal_conflict",
    "base_pnl_not_positive",
    "not_profitable_after_3c_adverse_slippage",
    "win_rate_below_target_and_no_economic_edge",
}
_V3_FORWARD_TEST_ULTRA_LOW_ASK_PROBE_FAILED_CHECKS = {
    *_V3_FORWARD_TEST_CONFLICT_PROBE_FAILED_CHECKS,
    "base_pnl_not_positive",
    "not_profitable_after_3c_adverse_slippage",
    "spread_above_max_or_missing",
    "win_rate_below_target_and_no_economic_edge",
}
_V3_SYSTEM_VALIDATION_ALLOWED_OBSERVED_FAILED_CHECKS = {
    *_V3_FORWARD_TEST_ULTRA_LOW_ASK_PROBE_FAILED_CHECKS,
    "quality_overlay_all_supporting_profiles_in_bad_profile_filter",
    "aggregate_insufficient_confirming_profiles",
    "aggregate_insufficient_weight",
}
_V3_SYSTEM_VALIDATION_FATAL_OBSERVED_FAILED_CHECKS = {
    "best_ask_missing",
    "quote_fetch_failed",
    "depth_top3_ask_size_below_min_or_missing",
    "time_remaining_outside_gate",
    "signal_to_ask_slippage_above_max_or_missing",
    "candidate_not_in_active_target_set",
}
_V3_SYSTEM_VALIDATION_REPLAY_BUCKET_CONDITIONAL_FAILED_CHECKS = {
    "max_loss_streak_above_3",
    "selected_backtest_variant_not_ready",
}


def build_v2_decision_set(
    monitor_payload: dict[str, Any],
    *,
    candidate_configs: list[dict[str, Any]],
    promotion_states: dict[str, dict[str, Any]] | None = None,
    candidate_ledgers: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build candidate decisions from the current profile monitor payload."""

    now = now or datetime.now(timezone.utc)
    promotion_states = promotion_states or {}
    five_lane_package = build_five_lane_system_package(monitor_payload, now=now)
    base_rows = {
        str(row.get("lane_id") or ""): row
        for row in ((five_lane_package.get("lane_decision_report") or {}).get("decisions") or [])
        if isinstance(row, dict)
    }
    dynamic = build_dynamic_family_candidate_decisions(
        base_rows.get("dynamic_multi_signal_event_manager") or {},
        candidate_configs,
        promotion_states=promotion_states,
    )
    specialized = build_specialized_candidate_decisions(
        candidate_configs,
        base_decisions={
            "ev_quality_overlay_limit_hold": base_rows.get("ev_quality_overlay_limit_hold") or {},
            "top_profile_consensus_market_follow": base_rows.get("top_profile_consensus_market_follow") or {},
            "inverse_profile_signal": _inverse_profile_signal(monitor_payload),
        },
        inverse_profile_samples=_inverse_profile_samples(monitor_payload),
    )
    validation = _build_v3_system_validation_decisions(
        monitor_payload,
        candidate_configs=candidate_configs,
        promotion_states=promotion_states,
    )
    v4 = _build_v4_decisions(
        monitor_payload,
        candidate_configs=candidate_configs,
        promotion_states=promotion_states,
        candidate_ledgers=candidate_ledgers or {},
        now=now,
    )
    decisions = [*(dynamic.get("decisions") or []), *(specialized.get("decisions") or []), *validation, *v4]
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v2_decision_set_v1",
            "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
            "five_lane_source_schema_version": five_lane_package.get("schema_version"),
            "candidate_count": len(candidate_configs),
            "decisions": decisions,
            "comparison_to_control": dynamic.get("comparison_to_control") or [],
            "safety_boundary": {
                "orders_allowed": False,
                "live_trading_authorized": False,
                "service_started": False,
            },
        }
    )


def _build_v3_system_validation_decisions(
    monitor_payload: dict[str, Any],
    *,
    candidate_configs: list[dict[str, Any]],
    promotion_states: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    config = next((row for row in candidate_configs if str(row.get("candidate_id") or "") == V3_SYSTEM_VALIDATION_CANDIDATE_ID), None)
    if not isinstance(config, dict):
        return []
    candidates = _validation_candidate_rows(monitor_payload)
    selected = _select_v3_system_validation_candidate(candidates, config=config)
    blockers: list[str] = []
    status = "accepted"
    if selected is None:
        selected = {}
        status = "blocked"
        blockers.append("no_v3_system_validation_candidate")
    else:
        selected = dict(selected)
        selected["v3_profile_support_summary"] = _validation_profile_support_summary(selected, config=config)
        selected["v3_bucket_label"] = _validation_bucket_label(_entry_price(selected))
        replay_gate = _v3_replay_bucket_gate(selected, (config.get("entry_policy") or {}))
        if replay_gate.get("enabled"):
            selected["v3_replay_bucket_gate"] = replay_gate
        low_probe_gate = _v3_s_plus_low_bucket_probe_gate(selected, (config.get("entry_policy") or {}))
        if low_probe_gate.get("enabled"):
            selected["v3_s_plus_low_bucket_probe_gate"] = low_probe_gate
        reduced_probe_gate = _v3_s_plus_reduced_bucket_probe_gate(selected, (config.get("entry_policy") or {}))
        if reduced_probe_gate.get("enabled"):
            selected["v3_s_plus_reduced_bucket_probe_gate"] = reduced_probe_gate
        high_probability_probe_gate = _v3_s_plus_high_probability_probe_gate(selected, (config.get("entry_policy") or {}))
        if high_probability_probe_gate.get("enabled"):
            selected["v3_s_plus_high_probability_probe_gate"] = high_probability_probe_gate
        selected["v3_size_multiplier"] = _validation_size_multiplier(selected, config=config)
        opposing = _select_v3_system_validation_opposing_candidate(selected, candidates, config=config)
        if opposing is not None:
            selected = dict(selected)
            selected["parallel_opposing_candidate"] = opposing
    return [
        strict_jsonable(
            {
                "candidate_id": V3_SYSTEM_VALIDATION_CANDIDATE_ID,
                "base_lane_lineage": config.get("base_lane_lineage"),
                "status": status,
                "planned_action": "enter_or_manage" if status == "accepted" else "none",
                "selected_candidate": selected,
                "entry_policy": config.get("entry_policy"),
                "exit_policy": config.get("exit_policy"),
                "promotion_state": promotion_states.get(V3_SYSTEM_VALIDATION_CANDIDATE_ID) or {},
                "proposed_ticket_notional_usd": (
                    promotion_states.get(V3_SYSTEM_VALIDATION_CANDIDATE_ID) or {}
                ).get("current_ticket_usd"),
                "policy_differences_vs_control": ["v3_single_system_validation_composite"],
                "blockers": sorted(set(blockers)),
                "ledger_annotations": {
                    "base_lane_lineage": "v3_system_validation_composite",
                    "validation_scope": [
                        "s_and_s_plus_follow_signals",
                        "e_d_u_inverse_signals",
                        "aggregate_profile_signal_support",
                        "same_direction_reentry_with_cashout_topup",
                        "opposite_side_parallel_signal",
                    ],
                },
                "orders_allowed": False,
                "live_trading_authorized": False,
            }
        )
    ]


def _build_v4_decisions(
    monitor_payload: dict[str, Any],
    *,
    candidate_configs: list[dict[str, Any]],
    promotion_states: dict[str, dict[str, Any]],
    candidate_ledgers: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    config_by_id = {str(row.get("candidate_id") or ""): row for row in candidate_configs if isinstance(row, dict)}
    if not (set(config_by_id) & set(V4_CANDIDATE_IDS)):
        return []
    rows = _v4_candidate_rows(monitor_payload)
    consensus = _v4_outcome_consensus(rows)
    decisions: list[dict[str, Any]] = []
    for candidate_id in V4_CANDIDATE_IDS:
        config = config_by_id.get(candidate_id)
        if not config:
            continue
        if candidate_id == V4_HEDGER_REPLICATION_CANDIDATE_ID:
            decisions.append(
                _v4_hedger_decision(
                    config,
                    rows,
                    ledger=candidate_ledgers.get(candidate_id) or {"entries": []},
                    promotion_state=promotion_states.get(candidate_id) or {},
                    now=now,
                )
            )
        elif candidate_id == V4_OUTCOME_PREDICTION_CANDIDATE_ID:
            decisions.append(
                _v4_outcome_prediction_decision(
                    config,
                    rows,
                    consensus=consensus,
                    promotion_state=promotion_states.get(candidate_id) or {},
                    now=now,
                )
            )
        elif candidate_id == V4_DIVERGENCE_SCALPING_CANDIDATE_ID:
            decisions.append(
                _v4_divergence_scalping_decision(
                    config,
                    rows,
                    consensus=consensus,
                    promotion_state=promotion_states.get(candidate_id) or {},
                    now=now,
                )
            )
    return decisions


def _v4_candidate_rows(monitor_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in _validation_candidate_rows(monitor_payload):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.setdefault("outcome", row.get("effective_outcome"))
        key = (str(row.get("event_slug") or ""), str(row.get("token_id") or ""), str(row.get("outcome") or ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _v4_hedger_decision(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    ledger: dict[str, Any],
    promotion_state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    candidate_id = V4_HEDGER_REPLICATION_CANDIDATE_ID
    blockers: list[str] = []
    hedger_rows = _v4_rows_with_style_support(rows, style="hedger_grid")
    hedger_rows = [row for row in hedger_rows if (row.get("v4_profile_support_summary") or {}).get("signal_count")]
    reference = _v4_best_reference_profile(_v4_executable_rows(hedger_rows)) or _v4_best_reference_profile(hedger_rows)
    selected: dict[str, Any] = {}
    if not reference:
        blockers.append("v4_no_active_s_tier_hedger_grid_profile")
    plan = _v4_hedger_inventory_plan(hedger_rows, ledger=ledger, profile_name=reference) if reference else None
    if plan is None:
        if reference:
            blockers.append("v4_hedger_inventory_plan_missing")
    else:
        selected = _v4_prepare_selected(plan["selected_row"], candidate_id=candidate_id, config=config, now=now)
        selected["v4_reference_profile"] = reference
        selected["v4_hedger_replication"] = plan["summary"]
        selected["v4_hedger_last_minute_mode"] = _v4_hedger_informational_mode(selected, now=now)
        entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
        if entry_policy.get("live_entries_enabled") is False:
            blockers.append(
                str(entry_policy.get("live_entries_disabled_reason") or "v4_hedger_live_entries_disabled")
            )
    status = "blocked" if blockers or not selected else "accepted"
    return _v4_decision_payload(
        candidate_id,
        config,
        status=status,
        selected=selected,
        promotion_state=promotion_state,
        blockers=blockers,
        ledger_annotations={
            "v4_strategy_family": "hedger_replication",
            "profile_style_rule": "hedger_grid_only",
            "reference_profile": reference,
        },
    )


def _v4_outcome_prediction_decision(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    consensus: dict[str, Any],
    promotion_state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    candidate_id = V4_OUTCOME_PREDICTION_CANDIDATE_ID
    blockers: list[str] = []
    selected: dict[str, Any] = {}
    if not consensus.get("accepted"):
        blockers.extend(consensus.get("blockers") or ["v4_outcome_consensus_missing"])
    else:
        selected = _v4_prepare_selected(consensus["selected"], candidate_id=candidate_id, config=config, now=now)
        context = evaluate_v4_event_context(selected, strategy_id=candidate_id, now=now)
        context = _v4_relaxed_context_for_probe(selected, context, candidate_id=candidate_id, config=config)
        selected["v4_event_context"] = context
        selected["v4_size_multiplier"] = context.get("size_multiplier")
        selected["v4_outcome_consensus"] = consensus.get("summary")
        selected["v4_dynamic_cashout_tier"] = dynamic_cashout_tier(
            _to_float(promotion_state.get("win_rate")),
            pnl_usd=_to_float(promotion_state.get("realized_pnl_usd")) or 0.0,
            drawdown_usd=_to_float(promotion_state.get("drawdown_from_peak_usd")) or 0.0,
        )
        blockers.extend(context.get("blockers") or [])
    status = "blocked" if blockers or not selected else "accepted"
    return _v4_decision_payload(
        candidate_id,
        config,
        status=status,
        selected=selected,
        promotion_state=promotion_state,
        blockers=blockers,
        ledger_annotations={
            "v4_strategy_family": "outcome_prediction",
            "profile_style_rule": "outcome_predictor_only",
            "cashout_policy": "dynamic_by_rolling_win_rate",
        },
    )


def _v4_divergence_scalping_decision(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    consensus: dict[str, Any],
    promotion_state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    candidate_id = V4_DIVERGENCE_SCALPING_CANDIDATE_ID
    blockers: list[str] = []
    selected: dict[str, Any] = {}
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    allow_consensus_probe = bool(entry_policy.get("allow_aggregate_scalping_with_consensus_probe"))
    allow_aggregate_probe = bool(entry_policy.get("allow_aggregate_action_scalping_probe"))
    if consensus.get("accepted") and not allow_consensus_probe:
        blockers.append("v4_scalping_consensus_present_routes_to_outcome_prediction")
    divergence = _v4_divergence_pair(rows)
    activation_reason = "elite_outcome_predictor_disagreement_without_consensus"
    if divergence is None and allow_aggregate_probe and not consensus.get("accepted"):
        divergence = _v4_aggregate_action_scalp_pair(rows)
        activation_reason = "aggregate_account_actions_20s_probe"
    if divergence is None:
        blockers.append("v4_scalping_requires_outcome_predictor_divergence")
    elif not blockers:
        selected = _v4_prepare_selected(divergence[0], candidate_id=candidate_id, config=config, now=now)
        opposing = _v4_prepare_selected(divergence[1], candidate_id=candidate_id, config=config, now=now)
        up_down_cost = (_entry_price(selected) or 0.0) + (_entry_price(opposing) or 0.0)
        max_cost = _to_float((config.get("entry_policy") or {}).get("max_combined_up_down_cost")) or 1.04
        if up_down_cost > max_cost + 1e-9:
            blockers.append(f"v4_scalping_combined_up_down_cost_above_max:{max_cost:g}")
        for leg in (selected, opposing):
            context = evaluate_v4_event_context(leg, strategy_id=candidate_id, now=now)
            context = _v4_relaxed_context_for_probe(leg, context, candidate_id=candidate_id, config=config)
            leg["v4_event_context"] = context
            leg["v4_size_multiplier"] = context.get("size_multiplier")
            blockers.extend(context.get("blockers") or [])
        selected["parallel_opposing_candidate"] = opposing
        selected["v4_divergence_summary"] = {
            "combined_up_down_cost": round(up_down_cost, 6),
            "max_combined_up_down_cost": max_cost,
            "activation_reason": activation_reason,
            "consensus_present": bool(consensus.get("accepted")),
        }
    status = "blocked" if blockers or not selected else "accepted"
    return _v4_decision_payload(
        candidate_id,
        config,
        status=status,
        selected=selected,
        promotion_state=promotion_state,
        blockers=blockers,
        ledger_annotations={
            "v4_strategy_family": "divergence_scalping",
            "profile_style_rule": "outcome_predictor_only",
            "cashout_policy": "mandatory_scalp_cashout",
        },
    )


def _v4_relaxed_context_for_probe(
    row: dict[str, Any],
    context: dict[str, Any],
    *,
    candidate_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    if not entry_policy.get("allow_missing_threshold_context_probe"):
        return context
    blockers = [str(item) for item in (context.get("blockers") or []) if item]
    if "v4_event_context_threshold_missing" not in blockers:
        return context
    hard_blockers = [item for item in blockers if item != "v4_event_context_threshold_missing"]
    if hard_blockers:
        return context
    if not row.get("token_id") or _entry_price(row) is None:
        return context
    remaining = _to_float(context.get("time_remaining_seconds"))
    minimum_remaining = (
        _to_float(entry_policy.get("missing_threshold_probe_min_time_remaining_seconds"))
        or _to_float(entry_policy.get("min_time_remaining_seconds"))
        or 90.0
    )
    if remaining is None or remaining < minimum_remaining:
        return context
    relaxed = dict(context)
    relaxed["allowed"] = True
    relaxed["blockers"] = []
    relaxed["context_mode"] = "threshold_missing_limited_probe"
    relaxed["context_probe_reason"] = "threshold_missing_but_quote_time_and_underlying_context_present"
    relaxed["size_multiplier"] = min(_to_float(context.get("size_multiplier")) or 1.0, 0.5)
    relaxed["context_blockers"] = [item for item in (context.get("context_blockers") or []) if item]
    relaxed["relaxed_context_blockers"] = ["v4_event_context_threshold_missing"]
    relaxed["context_probe_candidate_id"] = candidate_id
    return relaxed


def _v4_decision_payload(
    candidate_id: str,
    config: dict[str, Any],
    *,
    status: str,
    selected: dict[str, Any],
    promotion_state: dict[str, Any],
    blockers: list[str],
    ledger_annotations: dict[str, Any],
) -> dict[str, Any]:
    return strict_jsonable(
        {
            "candidate_id": candidate_id,
            "base_lane_lineage": config.get("base_lane_lineage"),
            "status": status,
            "planned_action": "enter_or_manage" if status == "accepted" else "none",
            "selected_candidate": selected,
            "entry_policy": config.get("entry_policy"),
            "exit_policy": config.get("exit_policy"),
            "promotion_state": promotion_state,
            "proposed_ticket_notional_usd": promotion_state.get("current_ticket_usd"),
            "policy_differences_vs_control": [candidate_id],
            "blockers": sorted(set(str(item) for item in blockers if item)),
            "ledger_annotations": ledger_annotations,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def _v4_row_with_style_support(row: dict[str, Any], *, style: str, allow_a_fallback: bool = False) -> dict[str, Any]:
    selected = dict(row)
    signals = eligible_v4_signals(selected, style=style, allow_a_fallback=allow_a_fallback)
    selected["supporting_signals"] = signals
    selected["supporting_profiles"] = sorted({signal_profile_name(signal) for signal in signals if signal_profile_name(signal)})
    selected["v4_profile_support_summary"] = summarize_v4_support(selected, style=style, allow_a_fallback=allow_a_fallback)
    selected["v4_ignored_live_grades"] = ["B", "C", "D", "E", "U"] if allow_a_fallback else ["A", "B", "C", "D", "E", "U"]
    selected["v4_a_fallback_active"] = bool(allow_a_fallback and signals)
    return selected


def _v4_rows_with_style_support(
    rows: list[dict[str, Any]],
    *,
    style: str,
    allow_a_fallback_when_no_live: bool = False,
) -> list[dict[str, Any]]:
    strict_rows = [_v4_row_with_style_support(row, style=style) for row in rows]
    if any((row.get("v4_profile_support_summary") or {}).get("signal_count") for row in strict_rows):
        return strict_rows
    if not allow_a_fallback_when_no_live:
        return strict_rows
    fallback_rows = [_v4_row_with_style_support(row, style=style, allow_a_fallback=True) for row in rows]
    if any((row.get("v4_profile_support_summary") or {}).get("signal_count") for row in fallback_rows):
        return fallback_rows
    return strict_rows


def _v4_signal_grade_allowed_after_filter(signal: dict[str, Any]) -> bool:
    if is_v4_live_grade(signal.get("profile_grade")):
        return True
    return bool(signal.get("v4_grade_fallback")) and is_v4_a_fallback_grade(signal.get("profile_grade"))


def _v4_best_reference_profile(rows: list[dict[str, Any]]) -> str | None:
    candidates: dict[str, tuple[int, float, float]] = {}
    for row in rows:
        for signal in row.get("supporting_signals") or []:
            if not isinstance(signal, dict):
                continue
            if not signal_is_hedger_grid(signal) or not _v4_signal_grade_allowed_after_filter(signal):
                continue
            name = signal_profile_name(signal)
            if not name:
                continue
            rank = grade_rank(signal.get("profile_grade"))
            score = _to_float(signal.get("profile_score")) or 0.0
            recency = -(_to_float(signal.get("age_seconds")) or 999999.0)
            current = candidates.get(name)
            value = (rank, score, recency)
            if current is None or value > current:
                candidates[name] = value
    if not candidates:
        return None
    return sorted(candidates, key=lambda name: candidates[name], reverse=True)[0]


def _v4_hedger_inventory_plan(
    rows: list[dict[str, Any]],
    *,
    ledger: dict[str, Any],
    profile_name: str | None,
) -> dict[str, Any] | None:
    wanted = str(profile_name or "").strip()
    if not wanted:
        return None
    event_states: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_slug = str(row.get("event_slug") or "")
        outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
        price = _entry_price(row)
        if not event_slug or outcome not in {"up", "down"} or price is None or not row.get("token_id"):
            continue
        signal_position = _v4_profile_signal_position(row, wanted)
        shares = _to_float(signal_position.get("shares")) or 0.0
        cost = _to_float(signal_position.get("cost_usd")) or 0.0
        if shares <= 0.0 and cost <= 0.0:
            continue
        if shares <= 0.0:
            shares = cost / max(0.01, float(price))
        if cost <= 0.0:
            cost = shares * float(price)
        event = event_states.setdefault(event_slug, _v4_empty_inventory_event(event_slug))
        side = event[outcome]
        side["shares"] += shares
        side["cost_usd"] += cost
        if side.get("row") is None or _v4_hedger_plan_row_sort_key(row) > _v4_hedger_plan_row_sort_key(side["row"]):
            side["row"] = row
    plans: list[dict[str, Any]] = []
    for event_slug, state in event_states.items():
        reference = _v4_reference_inventory_summary(state)
        if reference["total_shares"] <= 0.0:
            continue
        own = _v4_ledger_event_inventory(ledger, event_slug=event_slug)
        own_total = own["total_shares"]
        for outcome in ("up", "down"):
            side = state[outcome]
            row = side.get("row")
            if not isinstance(row, dict):
                continue
            if not _v4_hedger_row_has_usable_quote(row):
                continue
            reference_weight = float(reference[f"{outcome}_weight"] or 0.0)
            if reference_weight <= 0.0:
                continue
            own_weight = float(own[f"{outcome}_weight"] or 0.0) if own_total > 0.0 else 0.0
            ratio_gap = reference_weight if own_total <= 0.0 else reference_weight - own_weight
            if ratio_gap <= 0.02:
                continue
            plans.append(
                {
                    "row": row,
                    "event_slug": event_slug,
                    "outcome": outcome,
                    "ratio_gap": ratio_gap,
                    "reference": reference,
                    "own": own,
                    "sort_key": (
                        ratio_gap,
                        float(side["shares"]),
                        _to_float(row.get("time_remaining_seconds")) or 0.0,
                        -(_entry_price(row) or 0.0),
                    ),
                }
            )
    if not plans:
        return None
    best = sorted(plans, key=lambda item: item["sort_key"], reverse=True)[0]
    summary = {
        "schema_version": "crypto_options_v4_hedger_inventory_plan_v1",
        "reference_profile": wanted,
        "event_slug": best["event_slug"],
        "selected_outcome": "Up" if best["outcome"] == "up" else "Down",
        "replication_mode": "net_inventory_ratio_rebalance",
        "rebalance_action": "buy_underweight_side",
        "ratio_gap": round(float(best["ratio_gap"]), 6),
        "reference_position": best["reference"],
        "our_position": best["own"],
        "buy_only_rebalance": True,
        "sell_rebalance_implemented": False,
        "planner_note": "SELL inventory reduction is intentionally deferred until a dedicated lifecycle rule exists.",
    }
    return {"selected_row": best["row"], "summary": summary}


def _v4_hedger_plan_row_sort_key(row: dict[str, Any]) -> tuple[float, tuple[float, ...]]:
    return (1.0 if _v4_hedger_row_has_usable_quote(row) else 0.0, _v4_row_sort_key(row))


def _v4_hedger_row_has_usable_quote(row: dict[str, Any]) -> bool:
    if not row.get("token_id") or _entry_price(row) is None:
        return False
    if row.get("quote_error"):
        return False
    failed = {str(item) for item in (row.get("failed_checks") or []) if item}
    allowed = {
        "selected_backtest_variant_not_ready",
        "trade_count_below_target",
        "exploratory_in_sample_profile_filter_requires_forward_validation",
        "not_profitable_after_3c_adverse_slippage",
        "aggregate_candidate_needs_more_validation",
        "aggregate_opposite_signal_conflict",
    }
    if not failed.issubset(allowed):
        return False
    aggregate_blockers = {str(item) for item in (row.get("aggregate_blockers") or []) if item}
    if aggregate_blockers - {"opposite_signal_conflict"}:
        return False
    depth = _to_float(row.get("depth_top3_ask_size"))
    if depth is not None and depth < 5.0:
        return False
    return True


def _v4_empty_inventory_event(event_slug: str) -> dict[str, Any]:
    return {
        "event_slug": event_slug,
        "up": {"shares": 0.0, "cost_usd": 0.0, "row": None},
        "down": {"shares": 0.0, "cost_usd": 0.0, "row": None},
    }


def _v4_profile_signal_position(row: dict[str, Any], profile_name: str) -> dict[str, float]:
    wanted = str(profile_name or "").strip().lower()
    row_outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
    row_price = _entry_price(row) or 0.0
    shares = 0.0
    cost = 0.0
    for signal in row.get("supporting_signals") or []:
        if not isinstance(signal, dict):
            continue
        if wanted and signal_profile_name(signal).lower() != wanted:
            continue
        if not signal_is_hedger_grid(signal) or not _v4_signal_grade_allowed_after_filter(signal):
            continue
        signal_outcome = _outcome_key(signal.get("effective_outcome") or signal.get("outcome") or signal.get("raw_outcome"))
        if signal_outcome and row_outcome and signal_outcome != row_outcome:
            continue
        signal_shares = _to_float(signal.get("size") or signal.get("shares"))
        signal_cost = _to_float(signal.get("usdc_size") or signal.get("notional_usd") or signal.get("cost_usd"))
        signal_price = _to_float(signal.get("price") or signal.get("avg_price")) or row_price
        if signal_shares is None and signal_cost is not None and signal_price:
            signal_shares = signal_cost / max(0.01, signal_price)
        if signal_cost is None and signal_shares is not None:
            signal_cost = signal_shares * max(0.01, signal_price or row_price or 0.01)
        shares += signal_shares or 0.0
        cost += signal_cost or 0.0
    return {"shares": round(shares, 8), "cost_usd": round(cost, 8)}


def _v4_reference_inventory_summary(state: dict[str, Any]) -> dict[str, Any]:
    up_shares = float((state.get("up") or {}).get("shares") or 0.0)
    down_shares = float((state.get("down") or {}).get("shares") or 0.0)
    up_cost = float((state.get("up") or {}).get("cost_usd") or 0.0)
    down_cost = float((state.get("down") or {}).get("cost_usd") or 0.0)
    total_shares = up_shares + down_shares
    return {
        "up_shares": round(up_shares, 8),
        "down_shares": round(down_shares, 8),
        "up_cost_usd": round(up_cost, 8),
        "down_cost_usd": round(down_cost, 8),
        "up_weight": round(up_shares / total_shares, 6) if total_shares else None,
        "down_weight": round(down_shares / total_shares, 6) if total_shares else None,
        "up_weighted_price": round(up_cost / up_shares, 6) if up_shares else None,
        "down_weighted_price": round(down_cost / down_shares, 6) if down_shares else None,
        "total_shares": round(total_shares, 8),
    }


def _v4_ledger_event_inventory(ledger: dict[str, Any], *, event_slug: str) -> dict[str, Any]:
    up_shares = 0.0
    down_shares = 0.0
    up_cost = 0.0
    down_cost = 0.0
    for entry in ledger.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("side") or "BUY").upper() != "BUY":
            continue
        if str(entry.get("event_slug") or "") != event_slug:
            continue
        if entry.get("settlement_status") not in {"open_requires_reconciliation", "submitted_requires_reconciliation", None}:
            continue
        shares = _position_held_shares(entry) or _to_float(entry.get("size")) or 0.0
        price = _position_entry_price(entry) or _to_float(entry.get("price")) or 0.0
        outcome = _outcome_key(entry.get("outcome") or entry.get("effective_outcome"))
        if outcome == "up":
            up_shares += shares
            up_cost += shares * price
        elif outcome == "down":
            down_shares += shares
            down_cost += shares * price
    total_shares = up_shares + down_shares
    return {
        "up_shares": round(up_shares, 8),
        "down_shares": round(down_shares, 8),
        "up_cost_usd": round(up_cost, 8),
        "down_cost_usd": round(down_cost, 8),
        "up_weight": round(up_shares / total_shares, 6) if total_shares else None,
        "down_weight": round(down_shares / total_shares, 6) if total_shares else None,
        "up_weighted_price": round(up_cost / up_shares, 6) if up_shares else None,
        "down_weighted_price": round(down_cost / down_shares, 6) if down_shares else None,
        "total_shares": round(total_shares, 8),
    }


def _v4_best_paired_event_rows(rows: list[dict[str, Any]], *, profile_name: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    by_event: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if profile_name and not _v4_row_has_profile(row, profile_name):
            continue
        event_slug = str(row.get("event_slug") or "")
        outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
        if not event_slug or outcome not in {"up", "down"}:
            continue
        if _entry_price(row) is None or not row.get("token_id"):
            continue
        by_event.setdefault(event_slug, {})[outcome] = row
    pairs = [
        (sides["up"], sides["down"])
        for sides in by_event.values()
        if "up" in sides and "down" in sides
    ]
    if not pairs:
        return None
    return sorted(pairs, key=lambda pair: _v4_pair_sort_key(pair), reverse=True)[0]


def _v4_executable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("token_id") and _entry_price(row) is not None]


def _v4_best_single_hedger_follow_row(rows: list[dict[str, Any]], *, profile_name: str | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if profile_name and not _v4_row_has_profile(row, profile_name):
            continue
        outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
        if outcome not in {"up", "down"}:
            continue
        if _entry_price(row) is None or not row.get("token_id"):
            continue
        candidates.append(row)
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: _v4_row_sort_key(row), reverse=True)[0]


def _v4_hedger_aggregate(primary: dict[str, Any], opposing: dict[str, Any], *, reference_profile: str | None) -> dict[str, Any]:
    up = primary if _outcome_key(primary.get("outcome")) == "up" else opposing
    down = opposing if up is primary else primary
    up_signal_size = _v4_profile_signal_size(up, reference_profile)
    down_signal_size = _v4_profile_signal_size(down, reference_profile)
    total = up_signal_size + down_signal_size
    return {
        "schema_version": "crypto_options_v4_hedger_aggregate_v1",
        "reference_profile": reference_profile,
        "reference_up_signal_size": round(up_signal_size, 8),
        "reference_down_signal_size": round(down_signal_size, 8),
        "reference_up_weight": round(up_signal_size / total, 6) if total else None,
        "reference_down_weight": round(down_signal_size / total, 6) if total else None,
        "replication_mode": "paired_up_down_ratio_approximation",
    }


def _v4_hedger_single_follow_summary(row: dict[str, Any], *, reference_profile: str | None) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_v4_hedger_aggregate_v1",
        "reference_profile": reference_profile,
        "reference_signal_size": round(_v4_profile_signal_size(row, reference_profile), 8),
        "reference_outcome": row.get("outcome") or row.get("effective_outcome"),
        "paired_up_down_available": False,
        "replication_mode": "single_leg_hedger_signal_follow",
    }


def _v4_hedger_informational_mode(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    mode = dict(hedger_last_minute_mode(row, now=now))
    original_blockers = list(mode.get("blockers") or [])
    mode["informational_blockers"] = original_blockers
    mode["blockers"] = []
    mode["blocker_policy"] = "informational_only_for_hedger_follow"
    return mode


def _v4_outcome_consensus(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcome_rows = _v4_rows_with_style_support(rows, style="outcome_predictor")
    outcome_rows = [row for row in outcome_rows if (row.get("v4_profile_support_summary") or {}).get("signal_count")]
    if not outcome_rows:
        return {"accepted": False, "blockers": ["v4_no_active_s_tier_outcome_predictor_profile"]}
    decisions = _v4_latest_profile_outcomes(outcome_rows)
    total_profiles = len(decisions)
    if total_profiles < 3:
        return {"accepted": False, "blockers": ["v4_outcome_requires_at_least_3_active_profiles"], "summary": {"active_profile_count": total_profiles}}
    top_profiles = sorted(decisions, key=lambda name: decisions[name]["sort_key"], reverse=True)[: min(7, total_profiles)]
    counts: dict[str, int] = {"up": 0, "down": 0}
    best_by_outcome: dict[str, tuple[int, float]] = {"up": (0, 0.0), "down": (0, 0.0)}
    for profile in top_profiles:
        outcome = str(decisions[profile]["outcome"])
        counts[outcome] = counts.get(outcome, 0) + 1
        best_by_outcome[outcome] = max(best_by_outcome.get(outcome, (0, 0.0)), decisions[profile]["grade_score"])
    winning_outcome = sorted(("up", "down"), key=lambda key: (counts.get(key, 0), best_by_outcome.get(key, (0, 0.0))), reverse=True)[0]
    losing_outcome = "down" if winning_outcome == "up" else "up"
    required = _v4_consensus_required_count(len(top_profiles))
    blockers: list[str] = []
    if counts.get(winning_outcome, 0) < required:
        blockers.append(f"v4_outcome_consensus_below_required:{counts.get(winning_outcome, 0)}_of_{required}")
    if best_by_outcome.get(losing_outcome, (0, 0.0)) > best_by_outcome.get(winning_outcome, (0, 0.0)):
        blockers.append("v4_outcome_higher_tier_opposite_signal_present")
    selected = _v4_best_row_for_outcome(outcome_rows, winning_outcome)
    if selected is None:
        blockers.append("v4_outcome_consensus_row_missing")
    summary = {
        "active_profile_count": total_profiles,
        "top_profile_count": len(top_profiles),
        "required_agreement_count": required,
        "agreement_counts": counts,
        "winning_outcome": "Up" if winning_outcome == "up" else "Down",
        "top_profiles": top_profiles,
    }
    return {
        "accepted": not blockers and selected is not None,
        "selected": selected,
        "summary": summary,
        "blockers": blockers,
    }


def _v4_divergence_pair(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    outcome_rows = _v4_rows_with_style_support(rows, style="outcome_predictor")
    outcome_rows = [row for row in outcome_rows if (row.get("v4_profile_support_summary") or {}).get("signal_count")]
    decisions = _v4_latest_profile_outcomes(outcome_rows)
    if len(decisions) < 3:
        return None
    outcomes = {str(row.get("outcome")) for row in decisions.values()}
    if outcomes < {"up", "down"}:
        return None
    by_event: dict[str, dict[str, dict[str, Any]]] = {}
    for row in outcome_rows:
        event_slug = str(row.get("event_slug") or "")
        outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
        if event_slug and outcome in {"up", "down"} and row.get("token_id") and _entry_price(row) is not None:
            by_event.setdefault(event_slug, {})[outcome] = row
    pairs = [(sides["up"], sides["down"]) for sides in by_event.values() if "up" in sides and "down" in sides]
    if not pairs:
        return None
    return sorted(pairs, key=lambda pair: _v4_pair_sort_key(pair), reverse=True)[0]


def _v4_aggregate_action_scalp_pair(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    outcome_rows = _v4_rows_with_style_support(rows, style="outcome_predictor")
    outcome_rows = [row for row in outcome_rows if (row.get("v4_profile_support_summary") or {}).get("signal_count")]
    by_event: dict[str, dict[str, dict[str, Any]]] = {}
    for row in outcome_rows:
        event_slug = str(row.get("event_slug") or "")
        outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
        if event_slug and outcome in {"up", "down"} and row.get("token_id") and _entry_price(row) is not None:
            current = by_event.setdefault(event_slug, {}).get(outcome)
            if current is None or _v4_row_sort_key(row) > _v4_row_sort_key(current):
                by_event[event_slug][outcome] = row
    pairs = [(sides["up"], sides["down"]) for sides in by_event.values() if "up" in sides and "down" in sides]
    if not pairs:
        return None
    return sorted(pairs, key=lambda pair: _v4_pair_sort_key(pair), reverse=True)[0]


def _v4_prepare_selected(row: dict[str, Any], *, candidate_id: str, config: dict[str, Any], now: datetime) -> dict[str, Any]:
    selected = dict(row)
    selected["candidate_id"] = candidate_id
    selected["lane_id"] = candidate_id
    selected["v4_candidate_id"] = candidate_id
    selected["v4_bucket_label"] = _validation_bucket_label(_entry_price(selected))
    selected["v4_event_context"] = evaluate_v4_event_context(selected, strategy_id=candidate_id, now=now)
    selected["v4_entry_policy"] = {
        "allowed_live_grades": (config.get("entry_policy") or {}).get("allowed_live_grades"),
        "required_profile_styles": (config.get("entry_policy") or {}).get("required_profile_styles"),
        "ignored_live_grades": (config.get("entry_policy") or {}).get("ignored_live_grades"),
    }
    return selected


def _v4_latest_profile_outcomes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for row in rows:
        outcome = _outcome_key(row.get("outcome") or row.get("effective_outcome"))
        if outcome not in {"up", "down"}:
            continue
        for signal in row.get("supporting_signals") or []:
            if not isinstance(signal, dict) or not signal_is_outcome_predictor(signal) or not _v4_signal_grade_allowed_after_filter(signal):
                continue
            profile = signal_profile_name(signal)
            if not profile:
                continue
            grade_score = (grade_rank(signal.get("profile_grade")), _to_float(signal.get("profile_score")) or 0.0)
            sort_key = (grade_score[0], grade_score[1], -(_to_float(signal.get("age_seconds")) or 999999.0))
            current = decisions.get(profile)
            if current is None or sort_key > current["sort_key"]:
                decisions[profile] = {
                    "profile": profile,
                    "outcome": outcome,
                    "grade_score": grade_score,
                    "sort_key": sort_key,
                }
    return decisions


def _v4_consensus_required_count(profile_count: int) -> int:
    if profile_count >= 7:
        return 5
    if profile_count >= 5:
        return 4
    return max(3, profile_count - 1)


def _v4_best_row_for_outcome(rows: list[dict[str, Any]], outcome: str) -> dict[str, Any] | None:
    matching = [row for row in rows if _outcome_key(row.get("outcome") or row.get("effective_outcome")) == outcome and row.get("token_id") and _entry_price(row) is not None]
    if not matching:
        return None
    return sorted(matching, key=lambda row: _v4_row_sort_key(row), reverse=True)[0]


def _v4_row_has_profile(row: dict[str, Any], profile_name: str) -> bool:
    wanted = str(profile_name or "").strip().lower()
    return any(signal_profile_name(signal).lower() == wanted for signal in row.get("supporting_signals") or [] if isinstance(signal, dict))


def _v4_profile_signal_size(row: dict[str, Any], profile_name: str | None) -> float:
    wanted = str(profile_name or "").strip().lower()
    total = 0.0
    for signal in row.get("supporting_signals") or []:
        if not isinstance(signal, dict):
            continue
        if wanted and signal_profile_name(signal).lower() != wanted:
            continue
        total += _to_float(signal.get("size") or signal.get("usdc_size")) or 0.0
    return total


def _v4_pair_sort_key(pair: tuple[dict[str, Any], dict[str, Any]]) -> tuple[float, float, float]:
    return (
        min(_v4_row_sort_key(pair[0])[0], _v4_row_sort_key(pair[1])[0]),
        min(_to_float(pair[0].get("time_remaining_seconds")) or 0.0, _to_float(pair[1].get("time_remaining_seconds")) or 0.0),
        -((_entry_price(pair[0]) or 0.0) + (_entry_price(pair[1]) or 0.0)),
    )


def _v4_row_sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
    summary = row.get("v4_profile_support_summary") if isinstance(row.get("v4_profile_support_summary"), dict) else {}
    return (
        _to_float(summary.get("best_score")) or 0.0,
        _to_float(row.get("support_weight") or row.get("signal_weight")) or 0.0,
        -(_to_float(row.get("conflict_ratio")) or 0.0),
    )


def _outcome_key(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"up", "yes", "above"}:
        return "up"
    if normalized in {"down", "no", "below"}:
        return "down"
    return normalized


def _validation_candidate_rows(monitor_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    selected_variant = monitor_payload.get("selected_backtest_variant")
    for source, source_rows in (
        ("eligible_manual_candidates", monitor_payload.get("eligible_manual_candidates") or []),
        ("observed_candidates", monitor_payload.get("observed_candidates") or []),
    ):
        for raw in source_rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("outcome", row.get("effective_outcome"))
            row["v3_validation_source"] = source
            if isinstance(selected_variant, dict):
                row.setdefault("selected_backtest_variant", selected_variant)
            key = (str(row.get("event_slug") or ""), str(row.get("token_id") or ""), str(row.get("outcome") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    if rows:
        return rows
    report = monitor_payload.get("profile_signal_report") if isinstance(monitor_payload.get("profile_signal_report"), dict) else {}
    for raw in report.get("aggregated_candidates") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.setdefault("outcome", row.get("effective_outcome"))
        row["v3_validation_source"] = "profile_signal_report.aggregated_candidates"
        if isinstance(selected_variant, dict):
            row.setdefault("selected_backtest_variant", selected_variant)
        rows.append(row)
    return rows


def _select_v3_system_validation_candidate(rows: list[dict[str, Any]], *, config: dict[str, Any]) -> dict[str, Any] | None:
    eligible = [row for row in rows if _v3_system_validation_candidate_viable(row, config=config)]
    if not eligible:
        return None
    return sorted(
        eligible,
        key=lambda row: (
            len(_configured_entry_policy_blockers(row, config, check_size=False)),
            _v3_system_validation_sort_key(row),
        ),
    )[0]


def _select_v3_system_validation_opposing_candidate(
    selected: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    event = str(selected.get("event_slug") or "")
    outcome = str(selected.get("outcome") or "")
    opposite = _opposite_outcome(outcome)
    if not event or not opposite:
        return None
    opposing = [
        row
        for row in rows
        if str(row.get("event_slug") or "") == event
        and str(row.get("outcome") or row.get("effective_outcome") or "") == opposite
        and _v3_system_validation_candidate_viable(row, config=config)
    ]
    if not opposing:
        return None
    return sorted(
        opposing,
        key=lambda row: (
            len(_configured_entry_policy_blockers(row, config, check_size=False)),
            _v3_system_validation_sort_key(row),
        ),
    )[0]


def _v3_system_validation_candidate_viable(row: dict[str, Any], *, config: dict[str, Any]) -> bool:
    if not row.get("token_id"):
        return False
    price = _entry_price(row)
    if price is None:
        return False
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    if _price_in_configured_buckets(price, _effective_blocked_entry_buckets(row, entry_policy)):
        return False
    if _price_in_configured_buckets(price, entry_policy.get("quarantined_entry_buckets") or []):
        low_probe_gate = _v3_s_plus_low_bucket_probe_gate(row, entry_policy)
        if low_probe_gate.get("enabled") and not low_probe_gate.get("allowed"):
            return False
    if row.get("quote_error"):
        return False
    if not _v3_system_validation_failed_checks_allowed(row, config=config):
        return False
    aggregate_blockers = {str(item) for item in (row.get("aggregate_blockers") or row.get("blockers") or []) if item}
    if aggregate_blockers - {"opposite_signal_conflict", "insufficient_confirming_profiles", "insufficient_weight"}:
        return False
    depth = _to_float(row.get("depth_top3_ask_size"))
    if depth is not None and depth < 5.0:
        return False
    time_remaining = _to_float(row.get("time_remaining_seconds"))
    if time_remaining is not None and not (20.0 <= time_remaining <= 300.0):
        return False
    support_summary = _validation_profile_support_summary(row, config=config)
    if not support_summary["has_direct_signal"]:
        return False
    if support_summary["inverse_agrees_with_direct"]:
        return False
    if support_summary["a_disagrees"] and not support_summary["has_inverse_validator"]:
        return False
    support = _to_float(row.get("support_weight")) or _to_float(row.get("signal_weight")) or support_summary["direct_signal_count"]
    conflict = _to_float(row.get("conflict_weight")) or 0.0
    conflict_ratio = _to_float(row.get("conflict_ratio"))
    max_conflict = _to_float((config.get("conflict_policy") or {}).get("max_conflict_ratio")) or 1.25
    if support < 1.0:
        return False
    if conflict_ratio is not None and conflict_ratio > max_conflict and not _candidate_has_same_profile_dual_side(row):
        return False
    if conflict and support and conflict > support * max_conflict and not _candidate_has_same_profile_dual_side(row):
        return False
    return True


def _candidate_has_direct_validation_support(row: dict[str, Any]) -> bool:
    return _validation_profile_support_summary(row, config={})["has_direct_signal"]


def _validation_profile_support_summary(row: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    direct_grades = {str(item).upper() for item in (entry_policy.get("direct_signal_grades") or ["S", "S+", "S++"])}
    confirmation_grades = {str(item).upper() for item in (entry_policy.get("confirmation_grades") or ["A"])}
    inverse_grades = {str(item).upper() for item in (entry_policy.get("inverse_validator_grades") or ["E", "U"])}
    excluded_grades = {str(item).upper() for item in (entry_policy.get("excluded_signal_grades") or ["B", "C", "D"])}
    quarantined_profiles = _configured_signal_profile_keys(entry_policy.get("quarantined_signal_profiles") or [])
    max_signal_age_seconds = _to_float(entry_policy.get("max_signal_age_seconds"))
    signals = [item for item in row.get("supporting_signals") or [] if isinstance(item, dict)]
    direct_count = 0
    confirmation_count = 0
    inverse_count = 0
    excluded_count = 0
    stale_signal_count = 0
    quarantined_signal_count = 0
    a_disagrees = False
    inverse_agrees = False
    selected_outcome = str(row.get("outcome") or row.get("effective_outcome") or "").strip().lower()
    for signal in signals:
        if max_signal_age_seconds is not None:
            age_seconds = _to_float(signal.get("age_seconds"))
            if age_seconds is None or age_seconds > max_signal_age_seconds:
                stale_signal_count += 1
                continue
        signal_keys = {
            _profile_key(signal.get("profile_name") or signal.get("name")),
            _profile_key(signal.get("profile_id")),
            _profile_key(signal.get("profile_address") or signal.get("address")),
            _profile_key(signal.get("user") or signal.get("username")),
        }
        if quarantined_profiles and any(key in quarantined_profiles for key in signal_keys if key):
            quarantined_signal_count += 1
            continue
        grade = str(signal.get("profile_grade") or "").upper()
        polarity = str(signal.get("profile_polarity") or signal.get("polarity") or "").lower()
        effective_outcome = str(signal.get("effective_outcome") or signal.get("outcome") or "").strip().lower()
        raw_outcome = str(signal.get("raw_outcome") or signal.get("outcome") or effective_outcome).strip().lower()
        aligned = bool(selected_outcome and effective_outcome == selected_outcome)
        opposite = bool(selected_outcome and effective_outcome and effective_outcome != selected_outcome)
        raw_aligned = bool(selected_outcome and raw_outcome == selected_outcome)
        raw_opposite = bool(selected_outcome and raw_outcome and raw_outcome != selected_outcome)
        if grade in excluded_grades:
            excluded_count += 1
            continue
        if grade in direct_grades and aligned:
            direct_count += 1
            continue
        if grade in confirmation_grades:
            confirmation_count += 1
            if opposite:
                a_disagrees = True
            continue
        if grade in inverse_grades:
            if polarity == "inverse":
                if aligned or raw_opposite:
                    inverse_count += 1
                if raw_aligned:
                    inverse_agrees = True
                continue
            if aligned:
                inverse_agrees = True
            if opposite:
                inverse_count += 1
    direct_fallback = bool(row.get("single_trigger_profile_present")) and direct_count == 0 and not signals
    return {
        "has_direct_signal": bool(direct_count or direct_fallback),
        "direct_signal_count": direct_count + (1 if direct_fallback else 0),
        "confirmation_signal_count": confirmation_count,
        "inverse_validator_count": inverse_count,
        "excluded_signal_count": excluded_count,
        "stale_signal_count": stale_signal_count,
        "quarantined_signal_count": quarantined_signal_count,
        "a_disagrees": a_disagrees,
        "has_inverse_validator": inverse_count > 0,
        "inverse_agrees_with_direct": inverse_agrees,
    }


def _configured_signal_profile_keys(rows: list[Any]) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            key = _profile_key(row)
            if key:
                keys.add(key)
            continue
        if not isinstance(row, dict):
            continue
        for field in ("profile_name", "profile_id", "profile_address", "address", "user", "username"):
            key = _profile_key(row.get(field))
            if key:
                keys.add(key)
    return keys


def _profile_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _price_in_configured_buckets(price: float, buckets: list[Any]) -> bool:
    for bucket in buckets:
        parsed = _parse_price_bucket(bucket)
        if parsed is None:
            continue
        low, high = parsed
        if low <= float(price) < high:
            return True
    return False


def _effective_blocked_entry_buckets(selected: dict[str, Any], entry_policy: dict[str, Any]) -> list[Any]:
    buckets = list(entry_policy.get("blocked_entry_buckets") or [])
    price = _entry_price(selected)
    if price is None:
        return buckets
    replay_gate = _v3_replay_bucket_gate(selected, entry_policy)
    reduced_probe_gate = _v3_s_plus_reduced_bucket_probe_gate(selected, entry_policy)
    high_probability_probe_gate = _v3_s_plus_high_probability_probe_gate(selected, entry_policy)
    if not replay_gate.get("allowed") and not reduced_probe_gate.get("allowed") and not high_probability_probe_gate.get("allowed"):
        return buckets
    output: list[Any] = []
    for bucket in buckets:
        parsed = _parse_price_bucket(bucket)
        if parsed is None:
            output.append(bucket)
            continue
        low, high = parsed
        if low <= price < high:
            continue
        output.append(bucket)
    return output


def _v3_system_validation_failed_checks_allowed(row: dict[str, Any], *, config: dict[str, Any]) -> bool:
    failed = {str(item) for item in (row.get("failed_checks") or []) if item}
    if failed & _V3_SYSTEM_VALIDATION_FATAL_OBSERVED_FAILED_CHECKS:
        return False
    conditional = failed & _V3_SYSTEM_VALIDATION_REPLAY_BUCKET_CONDITIONAL_FAILED_CHECKS
    unconditional = failed - conditional
    if not unconditional.issubset(_V3_SYSTEM_VALIDATION_ALLOWED_OBSERVED_FAILED_CHECKS):
        return False
    if conditional:
        entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
        return bool(
            _v3_replay_bucket_gate(row, entry_policy).get("allowed")
            or _v3_s_plus_low_bucket_probe_gate(row, entry_policy).get("allowed")
            or _v3_s_plus_reduced_bucket_probe_gate(row, entry_policy).get("allowed")
            or _v3_s_plus_high_probability_probe_gate(row, entry_policy).get("allowed")
        )
    return True


def _v3_replay_bucket_gate(selected: dict[str, Any], entry_policy: dict[str, Any]) -> dict[str, Any]:
    settings = entry_policy.get("replay_bucket_readiness") if isinstance(entry_policy.get("replay_bucket_readiness"), dict) else {}
    if not settings.get("enabled"):
        return {"enabled": False, "allowed": False, "reason": "disabled"}
    price = _entry_price(selected)
    variant = selected.get("selected_backtest_variant") if isinstance(selected.get("selected_backtest_variant"), dict) else {}
    economics = variant.get("economics") if isinstance(variant.get("economics"), dict) else {}
    price_buckets = economics.get("price_buckets") if isinstance(economics.get("price_buckets"), list) else []
    matched = _matching_replay_price_bucket(price, price_buckets)
    if price is None:
        return {"enabled": True, "allowed": False, "reason": "entry_price_missing"}
    if not matched:
        return {"enabled": True, "allowed": False, "reason": "replay_bucket_missing", "entry_price": price}
    blockers: list[str] = []
    bucket_label = str(matched.get("bucket") or "")
    if _price_in_configured_buckets(price, entry_policy.get("blocked_entry_buckets") or []):
        allowed_blocked = list(settings.get("allow_blocked_entry_buckets") or [])
        if not _price_in_configured_buckets(price, allowed_blocked):
            blockers.append("replay_bucket_not_allowed_to_override_blocked_entry_bucket")
    trade_count = _to_int(matched.get("trade_count")) or 0
    min_trade_count = _to_int(settings.get("min_trade_count")) or 0
    if trade_count < min_trade_count:
        blockers.append("replay_bucket_trade_count_below_min")
    win_rate = _to_float(matched.get("win_rate"))
    min_win_rate = _to_float(settings.get("min_win_rate"))
    if min_win_rate is not None and (win_rate is None or win_rate + 1e-9 < min_win_rate):
        blockers.append("replay_bucket_win_rate_below_min")
    max_losses = _to_int(matched.get("max_sequential_losses")) or 0
    max_allowed_losses = _to_int(settings.get("max_sequential_losses"))
    if max_allowed_losses is not None and max_losses > max_allowed_losses:
        blockers.append("replay_bucket_loss_streak_above_max")
    return_sum = _to_float(matched.get("return_sum"))
    min_return_sum = _to_float(settings.get("min_return_sum"))
    if min_return_sum is not None and (return_sum is None or return_sum <= min_return_sum):
        blockers.append("replay_bucket_return_sum_below_min")
    return {
        "enabled": True,
        "allowed": not blockers,
        "bucket": bucket_label,
        "entry_price": price,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "max_sequential_losses": max_losses,
        "return_sum": return_sum,
        "blockers": blockers,
    }


def _v3_s_plus_low_bucket_probe_gate(selected: dict[str, Any], entry_policy: dict[str, Any]) -> dict[str, Any]:
    return _v3_s_plus_bucket_probe_gate(
        selected,
        entry_policy,
        settings_key="s_plus_low_bucket_split_probe",
        default_buckets=["0.05-0.20"],
        reason_prefix="s_plus_low_probe",
    )


def _v3_s_plus_reduced_bucket_probe_gate(selected: dict[str, Any], entry_policy: dict[str, Any]) -> dict[str, Any]:
    return _v3_s_plus_bucket_probe_gate(
        selected,
        entry_policy,
        settings_key="s_plus_reduced_bucket_probe",
        default_buckets=["0.25-0.50"],
        reason_prefix="s_plus_reduced_probe",
    )


def _v3_s_plus_high_probability_probe_gate(selected: dict[str, Any], entry_policy: dict[str, Any]) -> dict[str, Any]:
    return _v3_s_plus_bucket_probe_gate(
        selected,
        entry_policy,
        settings_key="s_plus_high_probability_probe",
        default_buckets=["0.80-0.95"],
        reason_prefix="s_plus_high_probability_probe",
    )


def _v3_s_plus_bucket_probe_gate(
    selected: dict[str, Any],
    entry_policy: dict[str, Any],
    *,
    settings_key: str,
    default_buckets: list[str],
    reason_prefix: str,
) -> dict[str, Any]:
    settings = entry_policy.get(settings_key) if isinstance(entry_policy.get(settings_key), dict) else {}
    reason = lambda suffix: f"{reason_prefix}_{suffix}"  # noqa: E731 - compact local reason formatter.
    if not settings.get("enabled"):
        return {"enabled": False, "allowed": False, "reason": "disabled"}
    failed = {str(item) for item in (selected.get("failed_checks") or []) if item}
    requires_replay_failure = bool(settings.get("requires_replay_conditional_failure", False))
    if requires_replay_failure and not (failed & _V3_SYSTEM_VALIDATION_REPLAY_BUCKET_CONDITIONAL_FAILED_CHECKS):
        return {"enabled": True, "allowed": False, "reason": reason("replay_conditional_failure_missing")}
    price = _entry_price(selected)
    if price is None:
        return {"enabled": True, "allowed": False, "reason": "entry_price_missing"}
    buckets = settings.get("entry_buckets") or default_buckets
    if not _price_in_configured_buckets(price, buckets):
        return {"enabled": True, "allowed": False, "reason": reason("entry_price_outside_probe_buckets"), "entry_price": price}
    blockers: list[str] = []
    summary = _validation_profile_support_summary(selected, config={"entry_policy": entry_policy})
    min_direct = _to_int(settings.get("min_direct_signals")) or 1
    if int(summary.get("direct_signal_count") or 0) < min_direct:
        blockers.append(reason("direct_signal_below_min"))
    min_support = _to_float(settings.get("min_support_weight"))
    support = _to_float(selected.get("support_weight") or selected.get("signal_weight")) or float(summary.get("direct_signal_count") or 0)
    if min_support is not None and support + 1e-9 < min_support:
        blockers.append(reason("support_weight_below_min"))
    if summary.get("inverse_agrees_with_direct"):
        blockers.append(reason("inverse_agrees_with_direct"))
    if settings.get("requires_inverse_validator") and not summary.get("has_inverse_validator"):
        blockers.append(reason("inverse_validator_required"))
    if summary.get("a_disagrees") and not summary.get("has_inverse_validator"):
        blockers.append(reason("a_disagrees_without_inverse_validation"))
    slippage = _to_float(selected.get("signal_to_ask_slippage_cents"))
    max_slippage = _to_float(settings.get("max_signal_to_ask_slippage_cents"))
    if max_slippage is not None and (slippage is None or slippage > max_slippage + 1e-9):
        blockers.append(reason("signal_to_ask_slippage_above_max"))
    depth = _to_float(selected.get("depth_top3_ask_size"))
    min_depth = _to_float(settings.get("min_depth_top3_ask_size"))
    if min_depth is not None and (depth is None or depth + 1e-9 < min_depth):
        blockers.append(reason("depth_below_min"))
    remaining = _to_float(selected.get("time_remaining_seconds"))
    min_time = _to_float(settings.get("min_time_remaining_seconds"))
    max_time = _to_float(settings.get("max_time_remaining_seconds"))
    if min_time is not None and (remaining is None or remaining + 1e-9 < min_time):
        blockers.append(reason("time_remaining_below_min"))
    if max_time is not None and remaining is not None and remaining > max_time + 1e-9:
        blockers.append(reason("time_remaining_above_max"))
    conflict_ratio = _to_float(selected.get("conflict_ratio"))
    max_conflict = _to_float(settings.get("max_conflict_ratio"))
    if conflict_ratio is not None and max_conflict is not None and conflict_ratio > max_conflict + 1e-9:
        blockers.append(reason("conflict_ratio_above_max"))
    conflict = _to_float(selected.get("conflict_weight")) or 0.0
    if max_conflict is not None and support and conflict > support * max_conflict + 1e-9:
        blockers.append(reason("conflict_weight_above_support"))
    return {
        "enabled": True,
        "allowed": not blockers,
        "entry_price": price,
        "direct_signal_count": summary.get("direct_signal_count"),
        "confirmation_signal_count": summary.get("confirmation_signal_count"),
        "inverse_validator_count": summary.get("inverse_validator_count"),
        "requires_inverse_validator": bool(settings.get("requires_inverse_validator")),
        "signal_to_ask_slippage_cents": slippage,
        "conflict_ratio": conflict_ratio,
        "support_weight": support,
        "conflict_weight": conflict,
        "settings_key": settings_key,
        "blockers": blockers,
    }


def _matching_replay_price_bucket(price: float | None, rows: list[Any]) -> dict[str, Any] | None:
    if price is None:
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = _parse_price_bucket(row.get("bucket"))
        if parsed is None:
            continue
        low, high = parsed
        if low <= float(price) < high:
            return row
    return None


def _candidate_has_same_profile_dual_side(row: dict[str, Any]) -> bool:
    if _to_float(row.get("same_profile_opposite_weight")):
        return True
    if int(row.get("same_profile_opposite_count") or 0) > 0:
        return True
    return bool(row.get("same_profile_dual_side_present"))


def _v3_system_validation_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float, float, str, str]:
    summary = _validation_profile_support_summary(row, config={})
    direct_bonus = 1.0 if summary["has_direct_signal"] else 0.0
    confirmation_bonus = float(summary["confirmation_signal_count"])
    inverse_bonus = float(summary["inverse_validator_count"])
    support = _to_float(row.get("support_weight")) or _to_float(row.get("signal_weight")) or 0.0
    conflict = _to_float(row.get("conflict_weight")) or 0.0
    price = _entry_price(row) or 0.99
    source_rank = 0.0 if str(row.get("v3_validation_source") or "") == "eligible_manual_candidates" else 1.0
    return (
        source_rank,
        -direct_bonus,
        -confirmation_bonus,
        -inverse_bonus,
        -support,
        conflict,
        f"{price:.6f}",
        str(row.get("token_id") or ""),
    )


def load_v2_candidate_ledgers(candidate_configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ledgers: dict[str, dict[str, Any]] = {}
    for config in candidate_configs:
        candidate_id = str(config.get("candidate_id") or "")
        path = Path(str(config.get("ledger_path") or ""))
        if path.exists():
            ledger = load_json_object(path)
        else:
            ledger = {"schema_version": "crypto_options_live_execution_ledger_v1", "lane_id": candidate_id, "entries": []}
        ledgers[candidate_id] = normalize_live_execution_ledger(ledger) | {"lane_id": candidate_id}
    return ledgers


def evaluate_v2_promotion_states(
    candidate_configs: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return {
        str(config.get("candidate_id") or ""): evaluate_promotion_state(
            ledgers.get(str(config.get("candidate_id") or "")) or {"entries": []},
            policy=config.get("promotion_policy"),
            generated_at=now,
        )
        for config in candidate_configs
    }


def build_v2_execution_packet_bundle(
    *,
    monitor_payload: dict[str, Any],
    candidate_configs: list[dict[str, Any]],
    candidate_decisions: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
    promotion_states: dict[str, dict[str, Any]],
    disabled_candidates: dict[str, Any] | None = None,
    execute_live: bool = False,
    execution_approved: bool = False,
    acknowledge_live_risk: bool = False,
    cashout_quote_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    disabled_candidates = disabled_candidates or {}
    decisions = {str(row.get("candidate_id") or ""): row for row in candidate_decisions if isinstance(row, dict)}
    packets: list[dict[str, Any]] = []
    global_cap = evaluate_global_discovery_cap(list(promotion_states.values()))
    for config in candidate_configs:
        candidate_id = str(config.get("candidate_id") or "")
        ledger = reconcile_live_execution_ledger(ledgers.get(candidate_id) or {"entries": []}, now=now)
        promotion_state = promotion_states.get(candidate_id) or {}
        decision = decisions.get(candidate_id) or {"candidate_id": candidate_id, "status": "blocked", "blockers": ["candidate_decision_missing"]}
        packet = _candidate_packet(
            config=config,
            decision=decision,
            ledger=ledger,
            promotion_state=promotion_state,
            monitor_payload=monitor_payload,
            disabled_candidates=disabled_candidates,
            global_cap=global_cap,
            execute_live=execute_live,
            execution_approved=execution_approved,
            acknowledge_live_risk=acknowledge_live_risk,
            cashout_quote_resolver=cashout_quote_resolver,
            now=now,
        )
        packets.append(packet)
        opposing = _parallel_opposing_decision(config, decision)
        if opposing is not None:
            packets.append(
                _candidate_packet(
                    config=config,
                    decision=opposing,
                    ledger=ledger,
                    promotion_state=promotion_state,
                    monitor_payload=monitor_payload,
                    disabled_candidates=disabled_candidates,
                    global_cap=global_cap,
                    execute_live=execute_live,
                    execution_approved=execution_approved,
                    acknowledge_live_risk=acknowledge_live_risk,
                    cashout_quote_resolver=cashout_quote_resolver,
                    now=now,
                    leg_id="opposing",
                )
            )
    return strict_jsonable(
        {
            "schema_version": V2_EXECUTION_PACKET_BUNDLE_SCHEMA_VERSION,
            "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
            "candidate_count": len(candidate_configs),
            "packet_count": len(packets),
            "ready_packet_count": sum(1 for packet in packets if packet.get("status") == "ready"),
            "execute_live_requested": bool(execute_live),
            "explicit_live_flags_complete": bool(execute_live and execution_approved and acknowledge_live_risk),
            "global_discovery_cap_state": global_cap,
            "packets": packets,
            "safety_boundary": {
                "orders_allowed": False,
                "live_submission_interface": "codex_tool/run_crypto_options_live_micro_executor.py",
                "manual_chat_orders_allowed": False,
            },
        }
    )


def write_v2_execution_packets(packet_bundle: dict[str, Any], *, output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    written_packets: list[dict[str, Any]] = []
    for packet in packet_bundle.get("packets") or []:
        if not isinstance(packet, dict):
            continue
        candidate_id = str(packet.get("candidate_id") or "")
        packet_id = str(packet.get("packet_id") or candidate_id)
        loop_dir = root / _safe_dir_name(packet_id)
        loop_dir.mkdir(parents=True, exist_ok=True)
        protocol_path = loop_dir / "protocol.json"
        monitor_path = loop_dir / "monitor.json"
        state_path = loop_dir / "manual_loop_state.json"
        ledger_path = Path(str(packet.get("ledger_path") or loop_dir / "live_execution_ledger.json"))
        if not ledger_path.is_absolute():
            ledger_path = (root / ledger_path).resolve()
        protocol_path.write_text(json.dumps(packet.get("protocol") or {}, indent=2, sort_keys=True), encoding="utf-8")
        monitor_path.write_text(json.dumps(packet.get("monitor") or {}, indent=2, sort_keys=True), encoding="utf-8")
        if not ledger_path.exists():
            write_json_object(packet.get("initial_live_ledger") or {}, ledger_path)
        state = {
            "schema_version": "crypto_options_v2_execution_loop_state_v1",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "candidate_id": candidate_id,
            "packet_id": packet_id,
            "leg_id": packet.get("leg_id"),
            "candidate_status": packet.get("status"),
            "packet_action": packet.get("packet_action"),
            "last_monitor_artifact": str(monitor_path),
            "protocol_artifact": str(protocol_path),
            "ledger_artifact": str(ledger_path),
            "executor_command": packet.get("executor_command"),
            "blockers": packet.get("blockers") or [],
        }
        selected_rows = (packet.get("monitor") or {}).get("eligible_manual_candidates") or []
        selected = selected_rows[0] if selected_rows and isinstance(selected_rows[0], dict) else {}
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        written_packets.append(
            {
                "candidate_id": candidate_id,
                "packet_id": packet_id,
                "leg_id": packet.get("leg_id"),
                "status": packet.get("status"),
                "packet_action": packet.get("packet_action"),
                "execution_side": packet.get("execution_side"),
                "execution_style": packet.get("execution_style"),
                "order_type": packet.get("order_type"),
                "price_slippage_cents": packet.get("price_slippage_cents"),
                "event_slug": selected.get("event_slug"),
                "token_id": selected.get("token_id"),
                "outcome": selected.get("outcome"),
                "selected_best_bid": selected.get("best_bid"),
                "selected_best_ask": selected.get("best_ask"),
                "loop_dir": str(loop_dir),
                "protocol": str(protocol_path),
                "monitor": str(monitor_path),
                "state": str(state_path),
                "ledger": str(ledger_path),
                "executor_command": packet.get("executor_command"),
                "blockers": packet.get("blockers") or [],
            }
        )
    output = {**packet_bundle, "output_root": str(root), "written_packets": written_packets}
    bundle_path = root / "v2_packets.json"
    write_json_object(output, bundle_path)
    output["artifact_json"] = str(bundle_path)
    return strict_jsonable(output)


def _candidate_packet(
    *,
    config: dict[str, Any],
    decision: dict[str, Any],
    ledger: dict[str, Any],
    promotion_state: dict[str, Any],
    monitor_payload: dict[str, Any],
    disabled_candidates: dict[str, Any],
    global_cap: dict[str, Any],
    execute_live: bool,
    execution_approved: bool,
    acknowledge_live_risk: bool,
    cashout_quote_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime,
    leg_id: str = "primary",
) -> dict[str, Any]:
    candidate_id = str(config.get("candidate_id") or "")
    packet_id = candidate_id if leg_id == "primary" else f"{candidate_id}__{leg_id}"
    decision_blockers = list(decision.get("blockers") or [])
    blockers: list[str] = []
    if _is_disabled(candidate_id, disabled_candidates):
        blockers.append("candidate_guard_disabled")
    if global_cap.get("global_cap_hit"):
        blockers.append("global_supervised_discovery_cap_hit")
    capabilities = config.get("capabilities") or {}
    if capabilities.get("cashout_managed"):
        active = _active_unarmed_cashout_position(ledger)
    elif capabilities.get("multi_entry_inventory_capable"):
        active = None
    else:
        active = _active_open_position(ledger)
    packet_action = "entry"
    selected = dict(decision.get("selected_candidate") or {})
    execution_side = "BUY"
    execution_style = _entry_execution_style(config)
    order_type = "FAK"
    price_slippage_cents = 3.0
    exit_decision = None
    cashout_quote = None
    if active and capabilities.get("cashout_managed"):
        packet_action = "cashout_exit"
        execution_side = "SELL"
        execution_style = "market" if (_position_held_shares(active) or 0.0) < 5.0 else "limit"
        if _has_submitted_exit_for_position(ledger, active):
            blockers.append("cashout_exit_already_submitted")
        quote = _quote_for_position(monitor_payload, active, quote_resolver=cashout_quote_resolver)
        cashout_quote = quote
        position = _position_from_open_entry(active, candidate_id=candidate_id)
        if _requires_undersized_partial_liquidation(active, config):
            position["force_market_exit_reason"] = "undersized_partial_fill_below_min_pairable"
        exit_decision = build_exit_execution_decision(
            position,
            quote,
            candidate_id=candidate_id,
            budget_state={"valid": not global_cap.get("global_cap_hit"), "reason": "v2_global_budget_state"},
            operator_reason=f"v2_cashout_exit_{candidate_id}",
            cashout_policy=(config.get("exit_policy") or {}).get("cashout_policy"),
            disabled_candidates=disabled_candidates,
            execute_live=execute_live,
            execution_approved=execution_approved,
            acknowledge_live_risk=acknowledge_live_risk,
            generated_at=now,
        )
        action = exit_decision.get("action") or {}
        action_type = str(action.get("action_type") or "")
        order_style = str(action.get("order_style") or "")
        if order_style == "limit_sell":
            order_type = "GTC"
            price_slippage_cents = 0.0
            execution_style = "limit"
        elif order_style == "market_sell_to_close":
            order_type = "FAK"
            execution_style = "market"
            price_slippage_cents = 3.0
        blockers.extend(exit_decision.get("dispatch_blockers") or [])
        if not exit_decision.get("dispatch_allowed") and not action.get("dispatch_ready"):
            if action_type == "worker_managed_market_sell_on_target" and action.get("target_reached") is False:
                blockers.append("cashout_target_not_reached")
            else:
                blockers.append("cashout_exit_dispatch_not_ready")
        selected = _selected_exit_candidate(active, quote, exit_decision, candidate_id=candidate_id)
    elif active:
        if promotion_state.get("disabled"):
            blockers.append(f"promotion_policy_disabled:{promotion_state.get('disabled_reason')}")
        blockers.append("open_position_requires_cashout_or_settlement_reconciliation")
    elif str(decision.get("status") or "") != "accepted":
        if promotion_state.get("disabled"):
            blockers.append(f"promotion_policy_disabled:{promotion_state.get('disabled_reason')}")
        blockers.extend(decision_blockers)
        blockers.append("candidate_decision_not_accepted")
    else:
        if promotion_state.get("disabled"):
            blockers.append(f"promotion_policy_disabled:{promotion_state.get('disabled_reason')}")
        blockers.extend(decision_blockers)
        executable = _matching_executable_entry_candidate(monitor_payload, selected, config=config)
        if executable is None:
            blockers.append("source_monitor_candidate_not_executable")
        else:
            merged_selected = _restore_v4_live_support_after_executable_bridge(
                {**selected, **executable},
                selected=selected,
                candidate_id=candidate_id,
            )
            selected = _selected_entry_candidate(
                merged_selected,
                decision=decision,
                promotion_state=promotion_state,
                candidate_id=candidate_id,
                config=config,
            )
            blockers.extend(_configured_entry_policy_blockers(selected, config, ledger=ledger))
            if capabilities.get("cashout_managed"):
                cashout_target = _cashout_entry_target(selected, config)
                selected["entry_cashout_target"] = cashout_target
                if cashout_target.get("target_price") is None:
                    blockers.append(f"cashout_entry_target_missing:{cashout_target.get('blocker') or 'unknown'}")
    status = "ready" if selected and not blockers else "blocked"
    protocol = _protocol_payload(
        candidate_id=candidate_id,
        config=config,
        selected=selected if status == "ready" else None,
        promotion_state=promotion_state,
        execution_side=execution_side,
        status=status,
        now=now,
    )
    monitor = _monitor_payload(
        candidate_id=candidate_id,
        selected=selected if status == "ready" else None,
        now=now,
    )
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v2_execution_packet_v1",
            "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
            "candidate_id": candidate_id,
            "packet_id": packet_id,
            "leg_id": leg_id,
            "status": status,
            "packet_action": packet_action,
            "blockers": sorted(set(str(item) for item in blockers if item)),
            "warnings": [],
            "execution_side": execution_side,
            "execution_style": execution_style,
            "order_type": order_type,
            "price_slippage_cents": price_slippage_cents,
            "protocol": protocol,
            "monitor": monitor,
            "ledger_path": config.get("ledger_path"),
            "initial_live_ledger": ledger,
            "promotion_state": promotion_state,
            "cashout_quote": cashout_quote,
            "exit_decision": exit_decision,
            "executor_command": _executor_command(
                candidate_id=candidate_id,
                execution_side=execution_side,
                execution_style=execution_style,
                order_type=order_type,
                price_slippage_cents=price_slippage_cents,
            ),
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def _restore_v4_live_support_after_executable_bridge(
    row: dict[str, Any],
    *,
    selected: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    if candidate_id == V4_HEDGER_REPLICATION_CANDIDATE_ID:
        style = "hedger_grid"
    elif candidate_id in {V4_OUTCOME_PREDICTION_CANDIDATE_ID, V4_DIVERGENCE_SCALPING_CANDIDATE_ID}:
        style = "outcome_predictor"
    else:
        return row
    restored = dict(row)
    filtered_signals = eligible_v4_signals(selected, style=style) or eligible_v4_signals(row, style=style)
    restored["supporting_signals"] = filtered_signals
    restored["supporting_profiles"] = sorted(
        {signal_profile_name(signal) for signal in filtered_signals if signal_profile_name(signal)}
    )
    restored["v4_profile_support_summary"] = summarize_v4_support(restored, style=style)
    restored["v4_ignored_live_grades"] = ["A", "B", "C", "D", "E", "U"]
    return restored


def _protocol_payload(
    *,
    candidate_id: str,
    config: dict[str, Any],
    selected: dict[str, Any] | None,
    promotion_state: dict[str, Any],
    execution_side: str,
    status: str,
    now: datetime,
) -> dict[str, Any]:
    ticket = float(promotion_state.get("current_ticket_usd") or (config.get("promotion_policy") or {}).get("starting_ticket_usd") or 2.0)
    dynamic_stop = float(promotion_state.get("dynamic_loss_stop_usd") or ticket * 3.0)
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    min_order_notional = _to_float(entry_policy.get("min_order_notional_usd"))
    gate: dict[str, Any] = {
        "strategy_id": f"v2_{candidate_id}",
        "candidate_status_required": "candidate_for_manual_review",
        "quote_age_seconds_max": 5.0,
        "time_remaining_seconds_min": 20.0,
        "time_remaining_seconds_max": 300.0,
    }
    if selected:
        gate.update(
            {
                "event_slug": selected.get("event_slug"),
                "outcome": selected.get("outcome"),
                "token_id": selected.get("token_id"),
            }
        )
        best_ask = _to_float(selected.get("best_ask") or selected.get("observed_execution_price") or selected.get("price"))
        best_bid = _to_float(selected.get("best_bid"))
        if best_ask is not None:
            max_jit_drift = _to_float(
                ((config.get("entry_policy") or {}).get("ev_liquidity_overlay") or {}).get("max_jit_price_drift_cents")
            )
            drift_cents = 3.0 if max_jit_drift is None else max(0.0, max_jit_drift)
            gate["best_ask_max"] = round(best_ask + (drift_cents / 100.0), 6)
        if best_bid is not None:
            gate["best_bid_min"] = round(max(0.01, best_bid - 0.03), 6)
        blocked_buckets = _effective_blocked_entry_buckets(selected, entry_policy)
        parsed_blocked = []
        for bucket in blocked_buckets:
            parsed = _parse_price_bucket(bucket)
            if parsed is None:
                continue
            low, high = parsed
            parsed_blocked.append({"min": low, "max": high})
            if best_ask is not None and best_ask < low:
                gate["best_ask_max"] = round(min(float(gate.get("best_ask_max") or low), low - 0.0001), 6)
        if parsed_blocked:
            gate["blocked_entry_buckets"] = parsed_blocked
        overlay = entry_policy.get("ev_liquidity_overlay") or {}
        configured_time_min = _time_to_expiry_min_seconds_for_selected(selected, entry_policy)
        if configured_time_min is not None:
            gate["time_remaining_seconds_min"] = max(float(gate.get("time_remaining_seconds_min") or 0.0), configured_time_min)
        max_spread = _to_float(overlay.get("max_spread"))
        min_depth = _to_float(overlay.get("min_depth_top3_ask_size"))
        if max_spread is not None:
            gate["spread_max"] = max_spread
        if min_depth is not None:
            gate["depth_top3_ask_size_min"] = min_depth
    budget: dict[str, Any] = {
        "max_position_cost_usd": ticket,
        "max_test_trades_before_review": 0,
        "hard_stop_full_losses": 0,
        "hard_stop_loss_usd": dynamic_stop,
        "global_supervised_discovery_cap_usd": (config.get("promotion_policy") or {}).get("global_supervised_discovery_cap_usd", 100.0),
    }
    capabilities = config.get("capabilities") if isinstance(config.get("capabilities"), dict) else {}
    if capabilities.get("multi_entry_inventory_capable"):
        budget["multi_entry_inventory_allowed"] = True
        budget["multi_entry_inventory_reason"] = "v4_hedger_net_inventory_rebalance"
    if min_order_notional is not None:
        budget["min_order_notional_usd"] = min_order_notional
    return {
        "schema_version": "crypto_options_v2_micro_test_protocol_v1",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "protocol_status": "approved_protocol_ready_for_separate_execution_design" if status == "ready" else "blocked",
        "strategy_id": f"v2_{candidate_id}",
        "candidate_id": candidate_id,
        "lane_id": candidate_id,
        "budget": budget,
        "entry_gates": [gate],
        "execution_side": execution_side,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _monitor_payload(*, candidate_id: str, selected: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if selected:
        row = dict(selected)
        row["strategy_id"] = f"v2_{candidate_id}"
        row["candidate_id"] = candidate_id
        row["lane_id"] = candidate_id
        row.setdefault("status", "candidate_for_manual_review")
        row.setdefault("quote_at_utc", row.get("observed_at") or now.astimezone(timezone.utc).isoformat())
        row.setdefault("monitor_quote_age_seconds", 0.0)
        rows.append(row)
    return {
        "schema_version": "crypto_options_v2_monitor_payload_v1",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "monitor_status": "eligible_manual_candidate_present" if rows else "blocked",
        "eligible_manual_candidate_count": len(rows),
        "eligible_manual_candidates": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _selected_entry_candidate(
    selected: dict[str, Any],
    *,
    decision: dict[str, Any],
    promotion_state: dict[str, Any],
    candidate_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = dict(selected)
    price = _entry_price(row) or 0.5
    ticket = _to_float(decision.get("proposed_ticket_notional_usd")) or _to_float(promotion_state.get("current_ticket_usd")) or 2.0
    entry_policy = (config or {}).get("entry_policy") if isinstance((config or {}).get("entry_policy"), dict) else {}
    if entry_policy.get("prefer_min_share_limit_orders"):
        existing_ticket = row.get("manual_execution_ticket") if isinstance(row.get("manual_execution_ticket"), dict) else {}
        target_shares = (
            _to_float(existing_ticket.get("shares"))
            or _to_float(entry_policy.get("min_pairable_filled_shares"))
            or 5.0
        )
        ticket = max(0.0, float(target_shares) * max(0.01, float(price)))
    multiplier = _to_float(row.get("v3_size_multiplier"))
    if multiplier is not None:
        ticket *= max(0.0, min(1.0, multiplier))
    v4_multiplier = _to_float(row.get("v4_size_multiplier"))
    if v4_multiplier is not None:
        ticket *= max(0.0, min(1.0, v4_multiplier))
    min_ticket = _to_float(((config or {}).get("entry_policy") or {}).get("min_order_notional_usd"))
    if min_ticket is not None:
        ticket = max(ticket, min_ticket)
    shares = max(0.0001, float(ticket) / max(0.01, float(price)))
    row["candidate_id"] = candidate_id
    row["lane_id"] = candidate_id
    row["source_signal_attribution"] = _source_signal_attribution(row, candidate_id=candidate_id, decision=decision)
    row["min_order_total_cost"] = round(float(ticket), 6)
    row["estimated_ticket_notional_usd"] = round(float(ticket), 6)
    row["manual_execution_ticket"] = {
        "shares": round(shares, 8),
        "observed_best_ask": price,
        "token_id": row.get("token_id"),
        "execution_side": "BUY",
    }
    return row


def _configured_entry_policy_blockers(
    selected: dict[str, Any],
    config: dict[str, Any],
    *,
    check_size: bool = True,
    ledger: dict[str, Any] | None = None,
) -> list[str]:
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    blockers: list[str] = []
    price = _entry_price(selected)
    summary = selected.get("v3_profile_support_summary") if isinstance(selected.get("v3_profile_support_summary"), dict) else {}
    if not summary:
        summary = _validation_profile_support_summary(selected, config=config)
    if price is None:
        blockers.append("entry_price_missing")
    else:
        v4_context = selected.get("v4_event_context") if isinstance(selected.get("v4_event_context"), dict) else {}
        blockers.extend(str(item) for item in v4_context.get("blockers") or [])
        for bucket in _effective_blocked_entry_buckets(selected, entry_policy):
            parsed = _parse_price_bucket(bucket)
            if parsed is None:
                continue
            low, high = parsed
            if low <= price < high:
                if _v3_s_plus_high_probability_probe_gate(selected, entry_policy).get("allowed"):
                    continue
                blockers.append(f"entry_price_bucket_blocked:{low:.2f}-{high:.2f}")
                break
        for bucket in entry_policy.get("quarantined_entry_buckets") or []:
            parsed = _parse_price_bucket(bucket)
            if parsed is None:
                continue
            low, high = parsed
            if low <= price < high:
                if _v3_s_plus_low_bucket_probe_gate(selected, entry_policy).get("allowed"):
                    continue
                blockers.append(f"entry_price_bucket_quarantined:{low:.2f}-{high:.2f}")
                break
        if _price_in_configured_buckets(price, entry_policy.get("reduced_confidence_entry_buckets") or []):
            bonereaper_style_dual_side = _candidate_has_same_profile_dual_side(selected)
            if (
                not bonereaper_style_dual_side
                and int(summary.get("confirmation_signal_count") or 0) <= 0
                and int(summary.get("inverse_validator_count") or 0) <= 0
            ):
                blockers.append("reduced_confidence_bucket_requires_a_or_inverse_validation")
        blockers.extend(_banded_router_selectivity_blockers(price, selected, summary, entry_policy))
        blockers.extend(_ev_liquidity_overlay_blockers(selected, entry_policy))
        blockers.extend(_symbol_outcome_gate_blockers(price, selected, summary, entry_policy))
        blockers.extend(_time_to_expiry_gate_blockers(price, selected, entry_policy))
    min_notional = _to_float(entry_policy.get("min_order_notional_usd"))
    notional = _to_float(selected.get("min_order_total_cost") or selected.get("estimated_ticket_notional_usd"))
    if check_size and min_notional is not None and notional is not None and notional + 1e-9 < min_notional:
        blockers.append("entry_notional_below_configured_min")
    min_shares = _to_float(entry_policy.get("min_pairable_filled_shares"))
    ticket = selected.get("manual_execution_ticket") if isinstance(selected.get("manual_execution_ticket"), dict) else {}
    shares = _to_float(ticket.get("shares") or selected.get("size"))
    if check_size and min_shares is not None and shares is not None and shares + 1e-9 < min_shares:
        blockers.append("entry_filled_shares_below_configured_min")
    blockers.extend(_active_exposure_throttle_blockers(selected, ledger, entry_policy))
    return sorted(set(blockers))


def _active_exposure_throttle_blockers(
    selected: dict[str, Any],
    ledger: dict[str, Any] | None,
    entry_policy: dict[str, Any],
) -> list[str]:
    throttle = entry_policy.get("active_exposure_throttle") if isinstance(entry_policy.get("active_exposure_throttle"), dict) else {}
    if not throttle or ledger is None:
        return []
    active_buys = [
        row
        for row in ledger.get("entries") or []
        if isinstance(row, dict)
        and str(row.get("side") or "BUY").upper() == "BUY"
        and row.get("status") == "submitted"
        and row.get("settlement_status") == "open_requires_reconciliation"
    ]
    blockers: list[str] = []
    total_limit = _to_int(throttle.get("max_open_entry_groups_total"))
    if total_limit is not None and len(active_buys) >= total_limit:
        blockers.append(f"active_exposure_total_limit:{total_limit}")
    symbol = str(selected.get("symbol") or "").strip()
    outcome = str(selected.get("outcome") or selected.get("effective_outcome") or "").strip()
    per_symbol_outcome = _to_int(throttle.get("max_open_entry_groups_per_symbol_outcome"))
    if per_symbol_outcome is not None and symbol and outcome:
        count = sum(
            1
            for row in active_buys
            if str(row.get("symbol") or "").strip() == symbol and str(row.get("outcome") or "").strip() == outcome
        )
        if count >= per_symbol_outcome:
            blockers.append(f"active_exposure_symbol_outcome_limit:{symbol}:{outcome}:{per_symbol_outcome}")
    event_slug = str(selected.get("event_slug") or "").strip()
    token_id = str(selected.get("token_id") or "").strip()
    per_event_token = _to_int(throttle.get("max_open_entry_groups_per_event_token_outcome"))
    if per_event_token is not None and (event_slug or token_id) and outcome:
        count = 0
        for row in active_buys:
            if event_slug and str(row.get("event_slug") or "").strip() != event_slug:
                continue
            if token_id and str(row.get("token_id") or "").strip() != token_id:
                continue
            if str(row.get("outcome") or "").strip() != outcome:
                continue
            count += 1
        if count >= per_event_token:
            blockers.append(f"active_exposure_event_token_outcome_limit:{per_event_token}")
    return blockers


def _banded_router_selectivity_blockers(
    price: float,
    selected: dict[str, Any],
    summary: dict[str, Any],
    entry_policy: dict[str, Any],
) -> list[str]:
    selectivity = entry_policy.get("banded_profile_router_selectivity") if isinstance(entry_policy.get("banded_profile_router_selectivity"), dict) else {}
    blockers: list[str] = []
    conflict_ratio = _to_float(selected.get("conflict_ratio"))
    validator_count = int(summary.get("confirmation_signal_count") or 0) + int(summary.get("inverse_validator_count") or 0)
    bonereaper_style_dual_side = _candidate_has_same_profile_dual_side(selected)
    s_plus_reduced_probe_allowed = _v3_s_plus_reduced_bucket_probe_gate(selected, entry_policy).get("allowed")
    if _price_in_configured_buckets(price, entry_policy.get("reduced_confidence_entry_buckets") or []):
        min_reduced_direct = _to_int(selectivity.get("reduced_bucket_min_direct_signals"))
        if min_reduced_direct is not None and int(summary.get("direct_signal_count") or 0) < min_reduced_direct:
            blockers.append("banded_router_reduced_bucket_requires_more_direct_signals")
        min_reduced_support = _to_float(selectivity.get("reduced_bucket_min_support_weight"))
        support = _to_float(selected.get("support_weight") or selected.get("signal_weight"))
        if min_reduced_support is not None and (support is None or support + 1e-9 < min_reduced_support):
            blockers.append("banded_router_reduced_bucket_support_below_min")
        if selectivity.get("reduced_bucket_requires_validator", False) and validator_count <= 0 and not bonereaper_style_dual_side:
            blockers.append("banded_router_reduced_bucket_requires_validator")
        max_reduced_conflict = _to_float(selectivity.get("reduced_bucket_max_conflict_ratio"))
        if (
            conflict_ratio is not None
            and max_reduced_conflict is not None
            and conflict_ratio > max_reduced_conflict
            and not s_plus_reduced_probe_allowed
        ):
            blockers.append("consensus_conflict_ratio_above_reduced_bucket_gate")
    if _price_in_configured_buckets(price, entry_policy.get("experimental_entry_buckets") or []):
        min_direct = int(selectivity.get("experimental_bucket_min_direct_signals") or 0)
        if min_direct > 0 and int(summary.get("direct_signal_count") or 0) < min_direct:
            blockers.append("banded_router_experimental_bucket_requires_two_direct_signals")
        if selectivity.get("experimental_bucket_requires_validator", False) and validator_count <= 0:
            blockers.append("banded_router_experimental_bucket_requires_validator")
        max_experimental_conflict = _to_float(selectivity.get("experimental_bucket_max_conflict_ratio"))
        if conflict_ratio is not None and max_experimental_conflict is not None and conflict_ratio > max_experimental_conflict:
            blockers.append("consensus_conflict_ratio_above_experimental_bucket_gate")
    return blockers


def _ev_liquidity_overlay_blockers(selected: dict[str, Any], entry_policy: dict[str, Any]) -> list[str]:
    overlay = entry_policy.get("ev_liquidity_overlay") if isinstance(entry_policy.get("ev_liquidity_overlay"), dict) else {}
    blockers: list[str] = []
    max_spread = _to_float(overlay.get("max_spread"))
    spread = _to_float(selected.get("spread"))
    if max_spread is not None and spread is not None and spread > max_spread + 1e-9:
        blockers.append("ev_liquidity_spread_above_max")
    min_depth = _to_float(overlay.get("min_depth_top3_ask_size"))
    depth = _to_float(selected.get("depth_top3_ask_size"))
    if min_depth is not None and depth is not None and depth + 1e-9 < min_depth:
        blockers.append("ev_liquidity_depth_top3_ask_below_min")
    max_slippage = _to_float(overlay.get("max_signal_to_ask_slippage_cents"))
    slippage = _to_float(selected.get("signal_to_ask_slippage_cents"))
    if max_slippage is not None and slippage is not None and slippage > max_slippage + 1e-9:
        blockers.append("ev_liquidity_signal_to_ask_slippage_above_max")
    min_time = _to_float(overlay.get("min_time_remaining_seconds"))
    max_time = _to_float(overlay.get("max_time_remaining_seconds"))
    remaining = _to_float(selected.get("time_remaining_seconds"))
    if remaining is not None:
        if min_time is not None and remaining + 1e-9 < min_time:
            blockers.append("ev_liquidity_time_remaining_below_min")
        if max_time is not None and remaining > max_time + 1e-9:
            blockers.append("ev_liquidity_time_remaining_above_max")
    return blockers


def _symbol_outcome_gate_blockers(
    price: float,
    selected: dict[str, Any],
    summary: dict[str, Any],
    entry_policy: dict[str, Any],
) -> list[str]:
    gates = entry_policy.get("symbol_outcome_gates") if isinstance(entry_policy.get("symbol_outcome_gates"), list) else []
    blockers: list[str] = []
    symbol = str(selected.get("symbol") or "").strip().upper()
    outcome = str(selected.get("outcome") or selected.get("effective_outcome") or "").strip().lower()
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        gate_symbol = str(gate.get("symbol") or "").strip().upper()
        gate_outcome = str(gate.get("outcome") or "").strip().lower()
        if gate_symbol and symbol != gate_symbol:
            continue
        if gate_outcome and outcome != gate_outcome:
            continue
        for bucket in gate.get("blocked_entry_buckets") or []:
            parsed = _parse_price_bucket(bucket)
            if parsed is None:
                continue
            low, high = parsed
            if low <= price < high:
                blockers.append(f"symbol_outcome_entry_bucket_blocked:{gate_symbol}:{gate_outcome}:{low:.2f}-{high:.2f}")
                break
        min_direct = _to_int(gate.get("min_direct_signals"))
        if min_direct is not None and int(summary.get("direct_signal_count") or 0) < min_direct:
            blockers.append(f"symbol_outcome_min_direct_signals:{gate_symbol}:{gate_outcome}:{min_direct}")
        if gate.get("requires_validator") and (
            int(summary.get("confirmation_signal_count") or 0) + int(summary.get("inverse_validator_count") or 0)
        ) <= 0:
            blockers.append(f"symbol_outcome_requires_validator:{gate_symbol}:{gate_outcome}")
        max_conflict = _to_float(gate.get("max_conflict_ratio"))
        if _v3_s_plus_reduced_bucket_probe_gate(selected, entry_policy).get("allowed"):
            max_conflict = _to_float(gate.get("s_plus_probe_max_conflict_ratio")) or max_conflict
        conflict_ratio = _to_float(selected.get("conflict_ratio"))
        if max_conflict is not None and conflict_ratio is not None and conflict_ratio > max_conflict:
            blockers.append(f"symbol_outcome_conflict_ratio_above_gate:{gate_symbol}:{gate_outcome}")
    return blockers


def _time_to_expiry_gate_blockers(price: float, selected: dict[str, Any], entry_policy: dict[str, Any]) -> list[str]:
    min_seconds = _time_to_expiry_min_seconds(price, entry_policy)
    if min_seconds is None:
        return []
    remaining = _to_float(selected.get("time_remaining_seconds"))
    if remaining is None:
        return []
    if remaining + 1e-9 < min_seconds:
        return [f"time_to_expiry_below_bucket_min:{min_seconds:g}s"]
    return []


def _time_to_expiry_min_seconds_for_selected(selected: dict[str, Any], entry_policy: dict[str, Any]) -> float | None:
    price = _entry_price(selected)
    if price is None:
        return None
    return _time_to_expiry_min_seconds(price, entry_policy)


def _time_to_expiry_min_seconds(price: float, entry_policy: dict[str, Any]) -> float | None:
    gates = entry_policy.get("time_to_expiry_gates") if isinstance(entry_policy.get("time_to_expiry_gates"), list) else []
    required: float | None = None
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        buckets = gate.get("entry_buckets") or []
        if not _price_in_configured_buckets(price, buckets):
            continue
        min_seconds = _to_float(gate.get("min_seconds"))
        if min_seconds is None:
            continue
        required = min_seconds if required is None else max(required, min_seconds)
    return required


def _cashout_entry_target(selected: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    entry_price = _entry_price(selected)
    if entry_price is None:
        return {"target_price": None, "bucket": None, "blocker": "entry_price_missing"}
    policy = (config.get("exit_policy") or {}).get("cashout_policy") or {}
    target_cap = _to_float(policy.get("target_price_cap")) or 0.99
    for bucket in policy.get("buckets") or []:
        if not isinstance(bucket, dict):
            continue
        low = _to_float(bucket.get("min_entry_price")) or _to_float(bucket.get("min_price"))
        high = _to_float(bucket.get("max_entry_price")) or _to_float(bucket.get("max_price"))
        multiple = _to_float(bucket.get("target_multiple"))
        if low is None or high is None or multiple is None:
            continue
        if float(low) <= float(entry_price) < float(high):
            return {
                "target_price": min(round(float(entry_price) * float(multiple), 4), target_cap),
                "bucket": bucket.get("bucket"),
                "target_multiple": multiple,
                "split_exit": bool(bucket.get("split_exit")),
                "stretch_target_price": bucket.get("stretch_target_price"),
                "blocker": None,
            }
    return {"target_price": None, "bucket": None, "blocker": "no_cashout_policy_for_entry_bucket"}


def _parallel_opposing_decision(config: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any] | None:
    capabilities = config.get("capabilities") or {}
    if not capabilities.get("parallel_conflict_capable"):
        return None
    if str(decision.get("status") or "") != "accepted":
        return None
    selected = decision.get("selected_candidate") if isinstance(decision.get("selected_candidate"), dict) else {}
    opposing = selected.get("parallel_opposing_candidate")
    if not isinstance(opposing, dict):
        return None
    cloned = deepcopy(decision)
    opposing = dict(opposing)
    opposing["v3_profile_support_summary"] = _validation_profile_support_summary(opposing, config=config)
    opposing["v3_bucket_label"] = _validation_bucket_label(_entry_price(opposing))
    opposing["v3_size_multiplier"] = _validation_size_multiplier(opposing, config=config)
    cloned["selected_candidate"] = opposing
    annotations = cloned.get("ledger_annotations") if isinstance(cloned.get("ledger_annotations"), dict) else {}
    annotations["parallel_conflict_leg"] = "opposing"
    cloned["ledger_annotations"] = annotations
    return cloned


def _selected_exit_candidate(
    open_entry: dict[str, Any],
    quote: dict[str, Any],
    exit_decision: dict[str, Any],
    *,
    candidate_id: str,
) -> dict[str, Any]:
    action = exit_decision.get("action") or {}
    shares = _to_float(action.get("sell_shares")) or _position_held_shares(open_entry) or 0.0
    limit_price = _to_float(action.get("limit_price"))
    best_bid = _to_float(quote.get("best_bid"))
    best_ask = _to_float(quote.get("best_ask"))
    return {
        "candidate_id": candidate_id,
        "lane_id": candidate_id,
        "event_slug": open_entry.get("event_slug"),
        "token_id": open_entry.get("token_id"),
        "symbol": open_entry.get("symbol"),
        "outcome": open_entry.get("outcome"),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "quote_error_type": quote.get("quote_error_type"),
        "quote_health": quote.get("quote_health"),
        "observed_execution_price": limit_price or best_bid,
        "cashout_limit_price": limit_price,
        "min_order_total_cost": 0.0,
        "manual_execution_ticket": {
            "shares": shares,
            "observed_best_bid": limit_price or best_bid,
            "token_id": open_entry.get("token_id"),
            "execution_side": "SELL",
        },
        "exit_decision": exit_decision,
    }


def _active_open_position(ledger: dict[str, Any]) -> dict[str, Any] | None:
    for entry in reversed(ledger.get("entries") or []):
        if isinstance(entry, dict) and entry.get("settlement_status") == "open_requires_reconciliation":
            return entry
    return None


def _active_unarmed_cashout_position(ledger: dict[str, Any]) -> dict[str, Any] | None:
    for entry in reversed(ledger.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("settlement_status") != "open_requires_reconciliation":
            continue
        coverage = _cashout_coverage_for_position(ledger, entry)
        if coverage["uncovered_shares"] <= _CASHOUT_SHARE_COVERAGE_EPSILON:
            continue
        active = dict(entry)
        active["cashout_open_buy_shares"] = coverage["open_buy_shares"]
        active["cashout_submitted_sell_shares"] = coverage["submitted_sell_shares"]
        active["cashout_uncovered_shares"] = coverage["uncovered_shares"]
        return active
    return None


def _has_submitted_exit_for_position(ledger: dict[str, Any], entry: dict[str, Any]) -> bool:
    return _cashout_coverage_for_position(ledger, entry)["uncovered_shares"] <= _CASHOUT_SHARE_COVERAGE_EPSILON


def _cashout_coverage_for_position(ledger: dict[str, Any], entry: dict[str, Any]) -> dict[str, float]:
    event_slug = str(entry.get("event_slug") or "")
    token_id = str(entry.get("token_id") or "")
    if not event_slug and not token_id:
        return {"open_buy_shares": 1.0, "submitted_sell_shares": 0.0, "uncovered_shares": 1.0}
    open_buy_shares = 0.0
    submitted_sell_shares = 0.0
    for row in ledger.get("entries") or []:
        if not isinstance(row, dict):
            continue
        if event_slug and str(row.get("event_slug") or "") != event_slug:
            continue
        if token_id and str(row.get("token_id") or "") != token_id:
            continue
        side = str(row.get("side") or "BUY").upper()
        if side == "BUY" and row.get("settlement_status") == "open_requires_reconciliation":
            open_buy_shares += _position_held_shares(row) or 1.0
        if (
            side == "SELL"
            and row.get("status") == "submitted"
            and row.get("settlement_status") == "exit_submitted_requires_reconciliation"
        ):
            submitted_sell_shares += _position_held_shares(row) or 1.0
    uncovered = max(0.0, open_buy_shares - submitted_sell_shares)
    if uncovered <= _CASHOUT_SHARE_COVERAGE_EPSILON:
        uncovered = 0.0
    return {
        "open_buy_shares": round(open_buy_shares, 8),
        "submitted_sell_shares": round(submitted_sell_shares, 8),
        "uncovered_shares": round(uncovered, 8),
    }


def _position_from_open_entry(entry: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    return {
        "position_id": entry.get("idempotency_key") or entry.get("submission_id") or f"{candidate_id}:{entry.get('event_slug')}:{entry.get('token_id')}",
        "candidate_id": candidate_id,
        "event_slug": entry.get("event_slug"),
        "token_id": entry.get("token_id"),
        "held_shares": _position_held_shares(entry),
        "original_open_buy_shares": entry.get("cashout_open_buy_shares") or _position_held_shares(entry),
        "submitted_sell_shares": entry.get("cashout_submitted_sell_shares"),
        "entry_price": _position_entry_price(entry),
        "opened_at_utc": entry.get("recorded_at_utc") or entry.get("submitted_at_utc"),
        "event_end_utc": entry.get("event_end_utc") or entry.get("market_end_utc"),
        "reconciliation_state": "reconciled",
    }


def _requires_undersized_partial_liquidation(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    if not entry_policy.get("liquidate_undersized_partial_fills"):
        return False
    min_pairable = _to_float(entry_policy.get("min_pairable_filled_shares")) or 5.0
    held_shares = _position_held_shares(entry) or 0.0
    return 0.0 < held_shares < min_pairable


def _position_held_shares(entry: dict[str, Any]) -> float | None:
    uncovered = _to_float(entry.get("cashout_uncovered_shares"))
    if uncovered is not None:
        return uncovered
    execution_quality = entry.get("execution_quality") if isinstance(entry.get("execution_quality"), dict) else {}
    return _to_float(execution_quality.get("filled_shares")) or _to_float(entry.get("size"))


def _position_entry_price(entry: dict[str, Any]) -> float | None:
    execution_quality = entry.get("execution_quality") if isinstance(entry.get("execution_quality"), dict) else {}
    return _to_float(execution_quality.get("realized_price")) or _to_float(entry.get("price"))


def _quote_for_position(
    monitor_payload: dict[str, Any],
    entry: dict[str, Any],
    *,
    quote_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    event_slug = str(entry.get("event_slug") or "")
    token_id = str(entry.get("token_id") or "")
    outcome = str(entry.get("outcome") or "")
    for row in _current_monitor_rows(monitor_payload):
        if token_id and str(row.get("token_id") or "") != token_id:
            continue
        if event_slug and str(row.get("event_slug") or "") != event_slug:
            continue
        if outcome and str(row.get("outcome") or "") != outcome:
            continue
        quote = {"best_bid": row.get("best_bid"), "best_ask": row.get("best_ask"), "source": "monitor_payload"}
        if quote.get("best_bid") is not None and quote.get("best_ask") is not None:
            return quote
        break
    if quote_resolver is not None:
        try:
            resolved = quote_resolver(entry)
        except Exception as exc:  # noqa: BLE001 - quote refresh failures should block exits, not crash the service.
            health = classify_quote_health(f"{type(exc).__name__}: {exc}")
            return {
                "best_bid": None,
                "best_ask": None,
                "source": "direct_quote_error",
                "error": f"{type(exc).__name__}: {exc}",
                "quote_error_type": health["quote_error_type"],
                "quote_health": health,
            }
        if isinstance(resolved, dict):
            if resolved.get("best_bid") is None or resolved.get("best_ask") is None or resolved.get("error") or resolved.get("reason"):
                health = classify_quote_health(resolved.get("error"), quote=resolved)
                resolved = {**resolved, "quote_error_type": health["quote_error_type"], "quote_health": health}
            return resolved
    return {"best_bid": None, "best_ask": None}


def _matching_executable_entry_candidate(
    monitor_payload: dict[str, Any],
    selected: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for row in monitor_payload.get("eligible_manual_candidates") or []:
        if not _monitor_candidate_matches_selected(row, selected):
            continue
        return dict(row)
    if _v3_forward_test_observed_candidate_bridge_enabled(config):
        for row in monitor_payload.get("observed_candidates") or []:
            if not _monitor_candidate_matches_selected(row, selected):
                continue
            bridge_row = dict(row)
            if selected.get("selected_backtest_variant") and not bridge_row.get("selected_backtest_variant"):
                bridge_row["selected_backtest_variant"] = selected.get("selected_backtest_variant")
            if selected.get("v3_replay_bucket_gate") and not bridge_row.get("v3_replay_bucket_gate"):
                bridge_row["v3_replay_bucket_gate"] = selected.get("v3_replay_bucket_gate")
            if not _observed_candidate_allowed_for_v3_forward_test(bridge_row, config=config):
                continue
            cloned = bridge_row
            cloned["v3_forward_test_observed_candidate_bridge"] = True
            cloned["v3_forward_test_allowed_failed_checks"] = sorted(
                str(item) for item in (bridge_row.get("failed_checks") or []) if item
            )
            return cloned
    return None


def _monitor_candidate_matches_selected(row: Any, selected: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    event_slug = str(selected.get("event_slug") or "")
    token_id = str(selected.get("token_id") or "")
    outcome = str(selected.get("outcome") or "")
    if token_id and str(row.get("token_id") or "") != token_id:
        return False
    if event_slug and str(row.get("event_slug") or "") != event_slug:
        return False
    if outcome and str(row.get("outcome") or "") != outcome:
        return False
    return True


def _v3_forward_test_observed_candidate_bridge_enabled(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    return (
        config.get("schema_version") == "crypto_options_v3_candidate_config_v1"
        or config.get("schema_version") == "crypto_options_v4_candidate_config_v1"
        or config.get("version_lineage") == "v3_final_five_live_test"
        or config.get("version_lineage") == "v4_three_lane_live_development"
    )


def _observed_candidate_allowed_for_v3_forward_test(row: Any, *, config: dict[str, Any] | None = None) -> bool:
    if not isinstance(row, dict):
        return False
    if not row.get("token_id"):
        return False
    if _entry_price(row) is None:
        return False
    if row.get("quote_error"):
        return False
    failed = {str(item) for item in (row.get("failed_checks") or []) if item}
    if _v4_hedger_observed_candidate_allowed(row, config=config, failed=failed):
        return True
    if not failed.issubset(_V3_FORWARD_TEST_ALLOWED_OBSERVED_FAILED_CHECKS):
        if _v3_system_validation_probe_allowed(row, config=config, failed=failed):
            return True
        if _v3_forward_test_ultra_low_ask_probe_allowed(row, config=config, failed=failed):
            return True
        return _v3_forward_test_conflict_probe_allowed(row, config=config, failed=failed)
    aggregate_blockers = {str(item) for item in (row.get("aggregate_blockers") or []) if item}
    if aggregate_blockers - _V3_FORWARD_TEST_ALLOWED_OBSERVED_FAILED_CHECKS:
        return False
    return True


def _v4_hedger_observed_candidate_allowed(
    row: dict[str, Any],
    *,
    config: dict[str, Any] | None,
    failed: set[str],
) -> bool:
    if str((config or {}).get("candidate_id") or "") != V4_HEDGER_REPLICATION_CANDIDATE_ID:
        return False
    allowed = {
        "selected_backtest_variant_not_ready",
        "trade_count_below_target",
        "exploratory_in_sample_profile_filter_requires_forward_validation",
        "not_profitable_after_3c_adverse_slippage",
        "aggregate_candidate_needs_more_validation",
        "aggregate_opposite_signal_conflict",
    }
    if not failed.issubset(allowed):
        return False
    aggregate_blockers = {str(item) for item in (row.get("aggregate_blockers") or []) if item}
    if aggregate_blockers - {"opposite_signal_conflict"}:
        return False
    depth = _to_float(row.get("depth_top3_ask_size"))
    if depth is not None and depth < 5.0:
        return False
    if _entry_price(row) is None or not row.get("token_id"):
        return False
    return bool((row.get("v4_profile_support_summary") or {}).get("signal_count") or eligible_v4_signals(row, style="hedger_grid"))


def _v3_system_validation_probe_allowed(
    row: dict[str, Any],
    *,
    config: dict[str, Any] | None,
    failed: set[str],
) -> bool:
    if str((config or {}).get("candidate_id") or "") != V3_SYSTEM_VALIDATION_CANDIDATE_ID:
        return False
    if failed & _V3_SYSTEM_VALIDATION_FATAL_OBSERVED_FAILED_CHECKS:
        return False
    if not _v3_system_validation_failed_checks_allowed(row, config=config or {}):
        return False
    return _v3_system_validation_candidate_viable(row, config=config or {})


def _v3_forward_test_conflict_probe_allowed(
    row: dict[str, Any],
    *,
    config: dict[str, Any] | None,
    failed: set[str],
) -> bool:
    if str((config or {}).get("candidate_id") or "") != "dynamic_parallel_conflict_cashout_v1":
        return False
    if not failed.issubset(_V3_FORWARD_TEST_CONFLICT_PROBE_FAILED_CHECKS):
        return False
    aggregate_blockers = {str(item) for item in (row.get("aggregate_blockers") or []) if item}
    if aggregate_blockers - {"opposite_signal_conflict"}:
        return False
    price = _entry_price(row)
    if price is None or 0.55 <= float(price) < 0.70:
        return False
    support = _to_float(row.get("support_weight"))
    conflict = _to_float(row.get("conflict_weight"))
    conflict_ratio = _to_float(row.get("conflict_ratio"))
    if support is None or conflict is None or conflict_ratio is None:
        return False
    return bool(float(support) >= max(25.0, float(conflict) * 1.05) and float(conflict_ratio) <= 0.95)


def _v3_forward_test_ultra_low_ask_probe_allowed(
    row: dict[str, Any],
    *,
    config: dict[str, Any] | None,
    failed: set[str],
) -> bool:
    if str((config or {}).get("candidate_id") or "") != "dynamic_bucketed_takeprofit_v1":
        return False
    if not failed.issubset(_V3_FORWARD_TEST_ULTRA_LOW_ASK_PROBE_FAILED_CHECKS):
        return False
    aggregate_blockers = {str(item) for item in (row.get("aggregate_blockers") or []) if item}
    if aggregate_blockers - {"opposite_signal_conflict"}:
        return False
    price = _entry_price(row)
    depth = _to_float(row.get("depth_top3_ask_size"))
    slippage = _to_float(row.get("signal_to_ask_slippage_cents"))
    time_remaining = _to_float(row.get("time_remaining_seconds"))
    conflict_ratio = _to_float(row.get("conflict_ratio"))
    support = _to_float(row.get("support_weight"))
    if price is None or not (0.01 <= float(price) <= 0.12):
        return False
    if depth is None or float(depth) < 5.0:
        return False
    if slippage is None or float(slippage) > 0.0:
        return False
    if time_remaining is None or not (20.0 <= float(time_remaining) <= 300.0):
        return False
    if support is None or float(support) < 25.0:
        return False
    if conflict_ratio is None or float(conflict_ratio) > 1.25:
        return False
    return True


def _current_monitor_rows(monitor_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key in ("eligible_manual_candidates", "observed_candidates"):
        rows.extend(row for row in monitor_payload.get(key) or [] if isinstance(row, dict))
    return rows


def _monitor_rows(monitor_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _current_monitor_rows(monitor_payload)
    report = monitor_payload.get("profile_signal_report") if isinstance(monitor_payload.get("profile_signal_report"), dict) else {}
    rows.extend(row for row in report.get("aggregated_candidates") or [] if isinstance(row, dict))
    return rows


def _inverse_profile_signal(monitor_payload: dict[str, Any]) -> dict[str, Any]:
    for row in _monitor_rows(monitor_payload):
        if row.get("inverse_signal_eligible") or row.get("source_profile_negative_edge_stable"):
            return dict(row)
    return {}


def _inverse_profile_samples(monitor_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    samples = monitor_payload.get("inverse_profile_samples")
    return samples if isinstance(samples, dict) else {}


def _entry_execution_style(config: dict[str, Any]) -> str:
    order_style = str((config.get("entry_policy") or {}).get("order_style") or "")
    selector = str((config.get("entry_policy") or {}).get("selector") or "")
    if "market" in order_style or selector in {
        "dynamic_event_manager",
        "top_profile_consensus",
        "v3_system_validation_composite",
        "v4_outcome_prediction_cashout",
        "v4_divergence_scalping",
    }:
        return "market"
    return "limit"


def _entry_price(row: dict[str, Any]) -> float | None:
    return _to_float(row.get("best_ask")) or _to_float(row.get("observed_execution_price")) or _to_float(row.get("price"))


def _executor_command(
    *,
    candidate_id: str,
    execution_side: str,
    execution_style: str,
    order_type: str,
    price_slippage_cents: float,
) -> str:
    return (
        "python codex_tool/run_crypto_options_live_micro_executor.py "
        f"--loop-dir <v2_loop_dir_for_{candidate_id}> "
        f"--order-type {order_type} "
        f"--execution-style {execution_style} "
        f"--execution-side {execution_side} "
        f"--price-slippage-cents {price_slippage_cents:g} "
        f"--operator codex-v2-{candidate_id} "
        f"--reason crypto_options_v2_supervised_{candidate_id} "
        "--execute-live --execution-approved --acknowledge-live-risk"
    )


def _is_disabled(candidate_id: str, disabled_candidates: dict[str, Any]) -> bool:
    row = disabled_candidates.get(candidate_id)
    if isinstance(row, dict):
        return bool(row.get("disabled", True))
    return bool(row)


def _safe_dir_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def _parse_price_bucket(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        low = _to_float(value.get("min_price") or value.get("min_entry_price") or value.get("low"))
        high = _to_float(value.get("max_price") or value.get("max_entry_price") or value.get("high"))
        return (low, high) if low is not None and high is not None else None
    parts = str(value or "").replace("c", "").split("-")
    if len(parts) != 2:
        return None
    low = _to_float(parts[0])
    high = _to_float(parts[1])
    if low is None or high is None:
        return None
    if low > 1.0 or high > 1.0:
        low /= 100.0
        high /= 100.0
    return low, high


def _validation_bucket_label(price: float | None) -> str | None:
    if price is None:
        return None
    for low, high in (
        (0.00, 0.05),
        (0.05, 0.10),
        (0.10, 0.15),
        (0.15, 0.20),
        (0.20, 0.25),
        (0.25, 0.30),
        (0.30, 0.35),
        (0.35, 0.40),
        (0.40, 0.45),
        (0.45, 0.50),
        (0.50, 0.55),
        (0.55, 0.60),
        (0.60, 0.70),
        (0.70, 0.80),
        (0.80, 1.00),
    ):
        if low <= float(price) < high:
            return f"{low:.2f}-{high:.2f}"
    return "out_of_policy_range"


def _validation_size_multiplier(row: dict[str, Any], *, config: dict[str, Any]) -> float:
    price = _entry_price(row)
    if price is None:
        return 0.0
    summary = _validation_profile_support_summary(row, config=config)
    if not summary["has_direct_signal"]:
        return 0.0
    multiplier = 1.0
    if 0.25 <= price < 0.50:
        multiplier *= 0.5
    if 0.50 <= price < 0.70:
        multiplier *= 0.35
    if summary["a_disagrees"]:
        multiplier *= 0.5
    if summary["confirmation_signal_count"] <= 0 and summary["inverse_validator_count"] <= 0:
        multiplier *= 0.75
    return round(max(0.0, min(1.0, multiplier)), 4)


def _source_signal_attribution(row: dict[str, Any], *, candidate_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    signals = [item for item in row.get("supporting_signals") or [] if isinstance(item, dict)]
    summarized_signals = []
    for signal in signals[:20]:
        summarized_signals.append(
            {
                "profile_name": signal.get("profile_name") or signal.get("name"),
                "profile_grade": signal.get("profile_grade"),
                "profile_score": signal.get("profile_score"),
                "profile_polarity": signal.get("profile_polarity") or signal.get("polarity"),
                "profile_trading_style": signal.get("profile_trading_style") or signal.get("trading_style"),
                "profile_trading_style_detail": signal.get("profile_trading_style_detail") or signal.get("trading_style_detail"),
                "profile_frequency_class": signal.get("profile_frequency_class") or signal.get("frequency_class"),
                "effective_outcome": signal.get("effective_outcome") or signal.get("outcome"),
                "raw_outcome": signal.get("raw_outcome") or signal.get("outcome"),
                "age_seconds": signal.get("age_seconds"),
            }
        )
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v3_source_signal_attribution_v1",
            "candidate_id": candidate_id,
            "decision_status": decision.get("status"),
            "validation_source": row.get("v3_validation_source"),
            "bucket": row.get("v3_bucket_label") or _validation_bucket_label(_entry_price(row)),
            "size_multiplier": row.get("v3_size_multiplier"),
            "v4_bucket": row.get("v4_bucket_label"),
            "v4_size_multiplier": row.get("v4_size_multiplier"),
            "v4_event_context": row.get("v4_event_context") or {},
            "v4_profile_support_summary": row.get("v4_profile_support_summary") or {},
            "profile_support_summary": row.get("v3_profile_support_summary") or {},
            "supporting_profiles": row.get("supporting_profiles") or [],
            "support_weight": row.get("support_weight") or row.get("signal_weight"),
            "conflict_weight": row.get("conflict_weight"),
            "conflict_ratio": row.get("conflict_ratio"),
            "same_profile_opposite_count": row.get("same_profile_opposite_count"),
            "signals": summarized_signals,
        }
    )


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


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


def _opposite_outcome(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"up", "yes", "above"}:
        return "Down"
    if normalized in {"down", "no", "below"}:
        return "Up"
    return ""


__all__ = [
    "V2_EXECUTION_PACKET_BUNDLE_SCHEMA_VERSION",
    "V2_SERVICE_STATE_SCHEMA_VERSION",
    "build_v2_decision_set",
    "build_v2_execution_packet_bundle",
    "default_v2_candidate_configs",
    "evaluate_v2_promotion_states",
    "load_v2_candidate_ledgers",
    "write_v2_execution_packets",
]
