from __future__ import annotations

import json
from pathlib import Path

from crypto_options_app.reports.live_dashboard import (
    _audit_summary,
    _strategy_state,
    build_live_dashboard_state,
    create_stop_for_review_request,
    render_live_dashboard_html,
)


def test_live_dashboard_marks_no_signal_rows_as_waiting_pytest() -> None:
    rows = [
        {
            "strategy_id": "indicator_confirmed_outcome_v1",
            "status": "blocked",
            "blockers": ["no_indicator_confirmed_outcome_signal"],
        },
        {
            "strategy_id": "indicator_confirmed_outcome_v1",
            "status": "blocked",
            "blockers": ["no_indicator_confirmed_outcome_signal"],
        },
    ]

    strategies = _strategy_state(rows, {})

    assert strategies[0]["status"] == "waiting"
    assert strategies[0]["blocked_rows"] == 2


def test_live_dashboard_marks_valid_fallback_gate_as_gated_pytest() -> None:
    rows = [
        {
            "strategy_id": "a_fallback_outcome_probe_v1",
            "status": "blocked",
            "blockers": ["s_tier_source_present"],
        }
    ]

    strategies = _strategy_state(rows, {})

    assert strategies[0]["status"] == "gated"


def test_live_dashboard_keeps_mechanical_blockers_visible_pytest() -> None:
    rows = [
        {
            "strategy_id": "event_context_outcome_v1",
            "status": "blocked",
            "blockers": ["missing_lifecycle_coverage"],
        }
    ]

    strategies = _strategy_state(rows, {})

    assert strategies[0]["status"] == "blocked"


def test_live_dashboard_hides_profile_quote_diagnostics_after_live_fill_pytest() -> None:
    rows = [
        {
            "strategy_id": "profile_hedge_scalping_v3",
            "status": "live_structural_executed",
            "blockers": [],
        },
        {
            "strategy_id": "profile_hedge_scalping_v3",
            "status": "blocked",
            "blockers": ["missing_best_ask", "missing_spread", "missing_ask_size", "missing_depth_top3_ask_size"],
        },
    ]

    strategies = _strategy_state(rows, {})

    assert strategies[0]["status"] == "live"
    assert strategies[0]["top_blockers"] == []


def test_live_dashboard_keeps_profile_quote_diagnostics_when_lane_never_filled_pytest() -> None:
    rows = [
        {
            "strategy_id": "profile_hedge_scalping_v3",
            "status": "blocked",
            "blockers": ["missing_best_ask", "missing_spread"],
        }
    ]

    strategies = _strategy_state(rows, {})

    assert strategies[0]["status"] == "blocked"
    assert strategies[0]["top_blockers"] == [("missing_best_ask", 1), ("missing_spread", 1)]


def test_live_dashboard_reports_active_run_pending_final_audit_pytest() -> None:
    rows = [
        {
            "strategy_id": "profile_hedge_scalping_v1",
            "status": "live_structural_executed",
            "exchange_order_id": "0xabc",
            "order_status": "filled",
        },
        {
            "strategy_id": "event_context_outcome_v1",
            "status": "live_structural_unfilled",
            "exchange_order_id": "0xdef",
            "order_status": "unfilled",
        },
    ]

    audit = _audit_summary({}, strategy_rows=rows, active_run_id="signal-live-test")

    assert audit["status"] == "active_run_pending_final_audit"
    assert audit["recorded_successful_buy_count"] == 1
    assert audit["blockers"] == []


def test_live_dashboard_prefers_final_run_status_over_stale_active_state_pytest(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    automation_dir = root / "automation"
    validation_dir = root / "live-validation"
    reports_dir = root / "reports"
    automation_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    run_id = "signal-live-test"
    (automation_dir / "signal_live_active_process.json").write_text(
        json.dumps({"run_id": run_id, "status": "running"}),
        encoding="utf-8-sig",
    )
    (validation_dir / f"{run_id}.json").write_text(
        json.dumps({"status": "validated", "strategy_rows": [], "event_results": []}),
        encoding="utf-8",
    )
    (reports_dir / f"{run_id}_order_audit.json").write_text(
        json.dumps({"status": "matched", "audit": {"status": "matched"}}),
        encoding="utf-8",
    )

    state = build_live_dashboard_state(artifact_root=root)

    assert state["active_run"]["status"] == "validated"
    assert state["active_run"]["run_id"] == run_id
    assert state["active_run"]["status_source"] == "run_artifact"
    assert state["active_run"]["audit_status"] == "matched"


def test_live_dashboard_promotes_finished_validated_run_to_validated_pytest(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    automation_dir = root / "automation"
    validation_dir = root / "live-validation"
    reports_dir = root / "reports"
    automation_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    run_id = "signal-live-finished"
    (automation_dir / "signal_live_active_process.json").write_text(
        json.dumps({"run_id": run_id, "status": "finished"}),
        encoding="utf-8-sig",
    )
    (validation_dir / f"{run_id}.json").write_text(
        json.dumps({"status": "validated", "strategy_rows": [], "event_results": []}),
        encoding="utf-8",
    )
    (reports_dir / f"{run_id}_order_audit.json").write_text(
        json.dumps({"status": "matched", "audit": {"status": "matched"}}),
        encoding="utf-8",
    )

    state = build_live_dashboard_state(artifact_root=root)

    assert state["active_run"]["status"] == "validated"
    assert state["active_run"]["status_source"] == "run_artifact"
    assert state["active_run"]["audit_status"] == "matched"


def test_live_dashboard_promotes_stale_validated_matched_run_to_validated_pytest(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    automation_dir = root / "automation"
    validation_dir = root / "live-validation"
    reports_dir = root / "reports"
    automation_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    run_id = "signal-live-stale-but-complete"
    (automation_dir / "signal_live_active_process.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "stale",
                "stale_reason": "audited_completed_run_pid_not_alive",
                "stale_marked_at_utc": "2026-06-03T21:25:05Z",
            }
        ),
        encoding="utf-8-sig",
    )
    (validation_dir / f"{run_id}.json").write_text(
        json.dumps({"status": "validated", "strategy_rows": [], "event_results": []}),
        encoding="utf-8",
    )
    (reports_dir / f"{run_id}_order_audit.json").write_text(
        json.dumps({"status": "matched", "audit": {"status": "matched"}}),
        encoding="utf-8",
    )

    state = build_live_dashboard_state(artifact_root=root)

    assert state["active_run"]["status"] == "validated"
    assert state["active_run"]["status_source"] == "run_artifact"
    assert state["active_run"]["audit_status"] == "matched"
    assert "stale_reason" not in state["active_run"]


def test_live_dashboard_uses_live_status_when_active_process_file_is_missing_pytest(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    automation_dir = root / "automation"
    validation_dir = root / "live-validation"
    reports_dir = root / "reports"
    automation_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    run_id = "signal-live-fallback"
    (automation_dir / "signal_live_status.json").write_text(
        json.dumps({"run_id": run_id, "status": "validated", "event_results": []}),
        encoding="utf-8",
    )
    (validation_dir / f"{run_id}.json").write_text(
        json.dumps({"status": "validated", "strategy_rows": [], "event_results": []}),
        encoding="utf-8",
    )
    (reports_dir / f"{run_id}_order_audit.json").write_text(
        json.dumps({"status": "matched", "audit": {"status": "matched"}}),
        encoding="utf-8",
    )

    state = build_live_dashboard_state(artifact_root=root)

    assert state["active_run"]["run_id"] == run_id
    assert state["active_run"]["stage"] == "latest completed run"
    assert state["active_run"]["status"] == "validated"
    assert state["audit"]["status"] == "matched"


def test_live_dashboard_exposes_run_start_and_event_window_count_pytest(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    automation_dir = root / "automation"
    validation_dir = root / "live-validation"
    reports_dir = root / "reports"
    automation_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    run_id = "signal-live-windows"
    (automation_dir / "signal_live_active_process.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "running",
                "started_at_utc": "2026-06-03T23:37:04.000000+00:00",
            }
        ),
        encoding="utf-8-sig",
    )
    (automation_dir / "signal_live_status.json").write_text(
        json.dumps({"run_id": run_id, "status": "running", "event_results": []}),
        encoding="utf-8",
    )
    (validation_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "status": "running",
                "event_cycle_counts": {
                    "btc-updown-5m-1780529700": 1,
                    "eth-updown-5m-1780530000": 1,
                },
                "event_results": [
                    {"event_index": 1, "event_slug": "btc-updown-5m-1780529700"},
                    {"event_index": 2, "event_slug": "eth-updown-5m-1780530000"},
                ],
                "strategy_rows": [],
            }
        ),
        encoding="utf-8",
    )
    (validation_dir / f"{run_id}_event_1.json").write_text(
        json.dumps({"event_index": 1, "event_slug": "btc-updown-5m-1780529700", "strategy_rows": []}),
        encoding="utf-8",
    )

    state = build_live_dashboard_state(artifact_root=root)

    assert state["totals"]["run_started_at_utc"] == "2026-06-03T23:37:04.000000+00:00"
    assert state["totals"]["event_window_count"] == 2
    assert state["totals"]["elapsed_5m_window_count"] is not None
    assert state["event_artifacts"][0]["event_window_start_utc"] is not None


def test_live_dashboard_exposes_open_cost_for_unresolved_positions_pytest() -> None:
    rows = [
        {
            "strategy_id": "event_context_outcome_v2",
            "status": "live_structural_executed",
        }
    ]
    settlement = {
        "summary": {
            "by_strategy": {
                "event_context_outcome_v2": {
                    "filled_position_count": 7,
                    "settled_position_count": 0,
                    "unresolved_position_count": 7,
                    "active_cost_usd": 7.28,
                    "realized_pnl_usd": 0.0,
                    "win_rate": None,
                }
            }
        }
    }

    strategies = _strategy_state(rows, settlement)

    assert strategies[0]["realized_pnl_usd"] == 0.0
    assert strategies[0]["active_cost_usd"] == 7.28
    assert strategies[0]["unresolved_position_count"] == 7


def test_live_dashboard_creates_stop_for_review_request_pytest(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    automation_dir = root / "automation"
    automation_dir.mkdir(parents=True)
    (automation_dir / "signal_live_active_process.json").write_text(
        json.dumps({"run_id": "signal-live-test", "stage": "rotation-test", "status": "running"}),
        encoding="utf-8",
    )

    request = create_stop_for_review_request("pause after current event and inspect blockers", artifact_root=root)
    state = build_live_dashboard_state(artifact_root=root)

    assert request["status"] == "pending"
    assert request["run_id"] == "signal-live-test"
    assert request["operator_message"] == "pause after current event and inspect blockers"
    assert (automation_dir / "stop_for_review_request.json").exists()
    assert state["review_request"]["status"] == "pending"


def test_live_dashboard_html_contains_stop_for_review_controls_pytest() -> None:
    html = render_live_dashboard_html()

    assert "Stop for review" in html
    assert "/v1/crypto-options-app/dashboard/stop-for-review" in html
    assert "openCost" in html
