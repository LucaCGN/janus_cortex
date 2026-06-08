from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from crypto_options_app.pipelines.options.reporting import strict_jsonable


V2_LIVE_PROTOCOL_SCHEMA_VERSION = "crypto_options_v2_supervised_live_run_protocol_v1"
V2_REQUIRED_ISSUES = (94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107)


def build_v2_supervised_live_run_protocol(
    *,
    prerequisite_statuses: dict[int, str] | None = None,
    operator_approval_captured: bool = False,
    run_until_user_stop: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    prerequisite_statuses = prerequisite_statuses or {}
    incomplete = [issue for issue in V2_REQUIRED_ISSUES if prerequisite_statuses.get(issue) != "completed"]
    return strict_jsonable(
        {
            "schema_version": V2_LIVE_PROTOCOL_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "parent_issue": 93,
            "historical_seed_issue": 47,
            "prerequisites": {
                "required_issues": list(V2_REQUIRED_ISSUES),
                "statuses": {str(key): value for key, value in prerequisite_statuses.items()},
                "incomplete_issues": incomplete,
                "parent_93_must_reference_completed_prerequisites": True,
            },
            "launch_checklist": _launch_checklist(),
            "service_cadence": {
                "live_cadence_owner": "continuous_janus_service_only",
                "heartbeat_role": "guard_and_report_only",
                "heartbeat_must_not_generate_or_submit_live_orders": True,
            },
            "test_duration_policy": {
                "fixed_trade_count_cap": None,
                "run_until_user_stop": bool(run_until_user_stop),
                "continue_while_profitable_candidates_remain": bool(run_until_user_stop),
                "stop_for_breakage_or_safety": True,
                "stop_when_no_profitable_candidates_remain": True,
                "operator_can_stop_any_time": True,
            },
            "stop_rules": {
                "per_candidate_dynamic_stop": "promotion_policy_v1 dynamic 3x current order size stop",
                "global_supervised_discovery_cap_usd": 100.0,
                "disabled_candidates_do_not_restart": True,
                "notify_immediately": ["unexpected_traceback", "credentials_missing", "submit_error", "global_cap_risk"],
            },
            "demotion_rules": {
                "policy": "promotion_policy_v1 demotes rather than disables when a candidate has a prior promotion streak, a current loss streak, positive realized PnL, and win rate still at or above threshold.",
                "default_loss_streak": 2,
                "default_size_multiplier": 0.50,
                "demotion_floor_ticket_usd": 2.0,
                "purpose": "Keep profitable candidates alive while reducing drawdown pressure after reversals.",
            },
            "promotion_rules": {
                "must_beat_or_diversify_control": "Promote only if PnL, hit rate, drawdown, cashout capture, and chronological stability beat control or add distinct profitable behavior.",
                "control_candidate_id": "control_dynamic_hold_v1",
                "requires_final_report_review": True,
            },
            "final_report": {
                "must_compare": ["8_live_candidates", "statistical_components_1_2_3_4_6_8", "cashout_simulation", "price_path_trace"],
                "recommendation_values": ["promote", "continue_supervised_testing", "discard_or_rebuild"],
            },
            "safety_boundary": {
                "manual_chat_order_placement_allowed": False,
                "manual_chat_order_cancellation_allowed": False,
                "manual_chat_order_signing_allowed": False,
                "manual_chat_order_broadcasting_allowed": False,
                "manual_chat_order_redemption_allowed": False,
                "manual_chat_order_routing_allowed": False,
                "manual_chat_recommendation_or_authorization_allowed": False,
                "unsupervised_production_authority": False,
                "supervised_testing_only": True,
                "live_flags_allowed_without_user_final_validation": False,
                "operator_approval_captured": bool(operator_approval_captured),
            },
            "ready_for_user_validation": not incomplete,
            "ready_for_supervised_launch": not incomplete and bool(operator_approval_captured),
        }
    )


def dry_run_v2_launch_rehearsal(
    *,
    protocol: dict[str, Any],
    report: dict[str, Any] | None,
    packet_set: dict[str, Any] | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if protocol.get("prerequisites", {}).get("incomplete_issues"):
        blockers.append("incomplete_github_prerequisites")
    if not report:
        blockers.append("missing_v2_comparison_report")
    elif not _report_has_required_sections(report):
        blockers.append("v2_report_missing_required_sections")
    if not packet_set:
        blockers.append("missing_v2_candidate_packet_set")
    else:
        live_packets = [packet for packet in packet_set.get("packets") or [] if packet.get("dispatch_allowed")]
        if live_packets:
            blockers.append("dry_run_packet_set_has_dispatch_allowed_packets")
    return strict_jsonable(
        {
            "schema_version": "crypto_options_v2_launch_rehearsal_v1",
            "status": "dry_run_passed" if not blockers else "blocked",
            "blockers": sorted(set(blockers)),
            "report_generation_verified": bool(report and _report_has_required_sections(report)),
            "guard_behavior_verified": bool(packet_set and not [packet for packet in packet_set.get("packets") or [] if packet.get("dispatch_allowed")]),
            "live_flags_allowed": False,
            "requires_user_final_validation": True,
            "safety_statement": "Dry-run rehearsal cannot start live trading; it only verifies artifacts and guard behavior.",
        }
    )


def _launch_checklist() -> list[dict[str, Any]]:
    return [
        {"item": "data_sources_ready", "requires": ["profile_signals", "price_path_trace", "statistical_features"]},
        {"item": "settlement_labels_ready", "requires": ["event_labels", "no_stale_thresholds"]},
        {"item": "price_trace_coverage_ready", "requires": ["price_path_trace_v1", "mfe_mae_fields"]},
        {"item": "cashout_simulator_ready", "requires": ["cashout_policy_v1", "bucketed_replay"]},
        {"item": "candidate_configs_ready", "requires": ["8_candidate_configs", "isolated_ledgers"]},
        {"item": "ledger_paths_ready", "requires": ["no_cross_candidate_contamination"]},
        {"item": "open_positions_reconciled", "requires": ["no_stale_open_positions"]},
        {"item": "credentials_readiness_checked", "requires": ["credentials_present", "no_submit_from_chat"]},
        {"item": "operator_approval_captured", "requires": ["explicit_user_validation_before_live_flags"]},
    ]


def _report_has_required_sections(report: dict[str, Any]) -> bool:
    sections = report.get("sections") or {}
    required = {
        "global_scoreboard",
        "per_candidate_scoreboard",
        "statistical_component_attribution",
        "cashout_capture",
        "divergent_decision_comparison",
        "cross_candidate_entry_price_comparison",
        "promotion_demotion_state",
        "safety_statement",
    }
    return required.issubset(sections)


__all__ = [
    "V2_LIVE_PROTOCOL_SCHEMA_VERSION",
    "V2_REQUIRED_ISSUES",
    "build_v2_supervised_live_run_protocol",
    "dry_run_v2_launch_rehearsal",
]
