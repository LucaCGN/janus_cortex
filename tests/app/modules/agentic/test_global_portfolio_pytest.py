from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.agentic.global_portfolio import (
    GlobalPortfolioWatchlistEntry,
    build_execution_gate_snapshot,
    build_manager_action_plan,
    build_watchlist_artifact,
    load_watchlist_source,
    render_execution_gate_report,
    render_manager_action_plan,
    render_watchlist_report,
)
from tools.build_global_portfolio_watchlist import build_from_source, write_outputs


def _execution_proof_kwargs() -> dict[str, object]:
    return {
        "approved_execution_path": "janus_portfolio_order_management",
        "adapter_name": "janus_portfolio_manager_order_management_v1",
        "adapter_version": "preview-first",
        "risk_budget_name": "global-portfolio-existing-position-target-maintenance-v1",
        "risk_budget": {
            "name": "global-portfolio-existing-position-target-maintenance-v1",
            "scope": "global-portfolio",
            "max_notional_usd": 10.0,
            "used_notional_usd": 0.0,
            "action_notional_usd": 1.95,
        },
        "minimum_order_proof": {
            "side": "sell",
            "order_type": "limit",
            "price": 0.39,
            "size": 5.0,
            "notional_usd": 1.95,
            "min_size": 5.0,
            "min_buy_notional_usd": 1.0,
        },
        "target_stop_rebuy_policy": True,
        "target_stop_rebuy_policy_detail": {
            "policy_name": "existing-position-target-maintenance-v1",
            "target_policy": "place_or_replace_limit_sell_target_after_review",
            "target_price": 0.39,
            "stop_policy": "no autonomous stop; review deterioration manually",
            "rebuy_policy": "no autonomous rebuy; record rebuy-watch only after exit",
            "reason": "Existing operator/global position has no matching direct sell target.",
        },
        "kill_switch_clearance": {
            "clear": True,
            "source": "janus_status_and_live_strategy_worker_status",
            "checked_at_utc": "2026-05-18T13:00:00Z",
            "blocked_reasons": [],
        },
        "idempotency_key": "unit-test-existing-target-token-123",
        "reconciliation_plan": {"target": "Janus portfolio action ledger then order reconciliation"},
    }


def test_execution_gate_snapshot_reports_issue59_concrete_proof_gaps_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        market_title="Will OpenAI have the best AI model at the end of June 2026?",
        market_slug="will-openai-have-the-best-ai-model-at-the-end-of-june-2026",
        token_id="token-openai",
        approved_execution_path="janus_portfolio_order_management",
        adapter_name="janus_portfolio_manager_order_management_v1",
        adapter_version="repo_current_runtime_preview",
        risk_budget={},
        minimum_order_proof={},
        target_stop_rebuy_policy_detail={},
        kill_switch_clearance={},
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=False,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=False,
        minimum_order_compliance=False,
        target_stop_rebuy_policy=False,
        kill_switch_clear=False,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob_account_snapshot", "janus_api_order_management_preview", "repo_contracts"],
    )

    diagnostics = snapshot.proof_diagnostics

    assert diagnostics["schema_version"] == "global_portfolio_execution_gate_diagnostics_v1"
    assert diagnostics["proof_bundle_complete"] is False
    assert diagnostics["next_missing_gate"] == "market_token_order_state_resolved"
    assert diagnostics["gates"]["approved_order_management_path"]["passed"] is True
    assert diagnostics["gates"]["portfolio_ledger_path"]["missing_fields"] == [
        "idempotency_key",
        "reconciliation_plan",
    ]
    assert "risk_budget_name" in diagnostics["gates"]["separate_risk_budget"]["missing_fields"]
    assert "minimum_order_proof.side" in diagnostics["gates"]["minimum_order_compliance"]["missing_fields"]
    assert (
        "target_stop_rebuy_policy_detail.target_price"
        in diagnostics["gates"]["target_stop_rebuy_policy"]["missing_fields"]
    )
    assert "kill_switch_clearance.source" in diagnostics["gates"]["kill_switch_clear"]["missing_fields"]


def test_watchlist_entry_rejects_execution_authority_pytest() -> None:
    with pytest.raises(ValueError, match="cannot authorize execution"):
        GlobalPortfolioWatchlistEntry(
            watch_id="bad-row",
            market_title="Unauthorized test market",
            group="operator-manual",
            execution_authorized=True,
        )

    with pytest.raises(ValueError, match="cannot authorize order preparation"):
        GlobalPortfolioWatchlistEntry(
            watch_id="bad-row",
            market_title="Unauthorized test market",
            group="operator-manual",
            order_preparation_authorized=True,
        )


def test_build_watchlist_artifact_adds_review_questions_and_summary_pytest() -> None:
    artifact = build_watchlist_artifact(
        [
            {
                "market_title": "Long-term finals winner",
                "market_slug": "nba-finals-winner-2026",
                "outcome": "Thunder",
                "group": "operator-manual",
                "target_state": "target_stale",
                "risk_bucket": "operator-manual",
                "source_evidence": ["direct_clob_position_snapshot"],
            },
            {
                "market_title": "BTC up or down May 18",
                "market_slug": "btc-updown-2026-05-18",
                "group": "future-domain-candidate",
                "target_state": "target_unknown",
                "source_evidence": ["profile_study_hypothesis"],
            },
        ],
        source_caveats=["fixture_only"],
        generated_at_utc="2026-05-18T11:15:00Z",
    )

    assert artifact.schema_version == "global_portfolio_watchlist_v1"
    assert artifact.execution_authorized is False
    assert artifact.order_preparation_authorized is False
    assert artifact.summary["entry_count"] == 2
    assert artifact.summary["needs_operator_review_count"] == 1
    assert artifact.summary["policy_flags"] == {
        "future_domain_watch_only": 1,
        "operator_manual_review": 1,
        "target_stale": 1,
    }
    assert artifact.summary["target_policy"]["target_uncovered_or_stale_rows"] == 1
    assert artifact.entries[0].source_actor == "operator"
    assert artifact.entries[0].operator_review_questions == ["target requires operator review before any action"]
    assert artifact.entries[1].risk_bucket == "future-domain"


def test_load_watchlist_source_accepts_object_and_list_pytest() -> None:
    entries, caveats = load_watchlist_source(
        {
            "source_caveats": ["direct_access_missing"],
            "entries": [{"market_title": "Culture market", "group": "watch-only"}],
        }
    )

    assert entries == [{"market_title": "Culture market", "group": "watch-only"}]
    assert caveats == ["direct_access_missing"]
    list_entries, list_caveats = load_watchlist_source([{"market_title": "Macro market", "group": "watch-only"}])
    assert list_entries == [{"market_title": "Macro market", "group": "watch-only"}]
    assert list_caveats == []


def test_tool_writes_schema_artifact_and_report_pytest(tmp_path: Path) -> None:
    source = tmp_path / "watchlist_source.json"
    source.write_text(
        json.dumps(
            {
                "source_caveats": ["fixture_not_direct_clob_truth"],
                "entries": [
                    {
                        "market_title": "Global watch market",
                        "market_slug": "global-watch-market",
                        "outcome": "Yes",
                        "group": "watch-only",
                        "target_state": "target_missing",
                        "source_evidence": ["operator_watch_note"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    artifact = build_from_source(source_json=source, generated_at_utc="2026-05-18T11:15:00Z")
    paths = write_outputs(
        artifact,
        output_dir=tmp_path / "artifacts",
        report_path=tmp_path / "global_portfolio_watchlist_schema_2026-05-18.md",
    )

    artifact_path = Path(paths["artifact_path"])
    report_path = Path(paths["report_path"])
    assert artifact_path.exists()
    assert report_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["no_execution_statement"] == "No execution is authorized by this artifact."
    assert payload["summary"]["target_states"] == {"target_missing": 1}
    assert payload["summary"]["policy_flags"] == {"target_missing": 1}
    report = report_path.read_text(encoding="utf-8")
    assert "No orders were placed" in report
    assert "Direct CLOB/account truth remains required" in report
    assert "Target Policy Review" in report
    assert "review_only_no_execution" in report


def test_render_watchlist_report_handles_empty_schema_pytest() -> None:
    artifact = build_watchlist_artifact([], generated_at_utc="2026-05-18T11:15:00Z")

    report = render_watchlist_report(artifact)

    assert "| none | watch-only | No source rows supplied |" in report
    assert "No execution is authorized by this artifact." in report


def test_artifact_flags_paired_yes_no_exposure_pytest() -> None:
    artifact = build_watchlist_artifact(
        [
            {
                "market_title": "Will Team A win the title?",
                "market_slug": "team-a-title-2026",
                "outcome": "Yes",
                "side": "yes",
                "group": "operator-manual",
                "target_state": "target_present",
                "current_target": {"side": "sell", "limit_price": 0.5},
                "source_evidence": ["direct_clob_position_snapshot"],
            },
            {
                "market_title": "Will Team A win the title?",
                "market_slug": "team-a-title-2026",
                "outcome": "No",
                "side": "no",
                "group": "operator-manual",
                "target_state": "target_missing",
                "source_evidence": ["direct_clob_position_snapshot"],
            },
        ],
        generated_at_utc="2026-05-18T11:15:00Z",
    )

    assert artifact.summary["target_policy"]["paired_exposure_rows"] == 2
    assert artifact.summary["policy_flags"]["paired_yes_no_exposure"] == 2
    assert "paired_yes_no_exposure" in artifact.entries[0].policy_flags
    assert "target_present" in artifact.entries[0].policy_flags
    assert "target_missing" in artifact.entries[1].policy_flags
    assert artifact.entries[0].operator_review_questions == [
        "Resolve paired Yes/No exposure before interpreting directional thesis."
    ]


def test_execution_gate_snapshot_blocks_missing_portfolio_manager_path_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        **_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api"],
    )

    assert snapshot.result == "management_plan_only_execution_gate_missing"
    assert snapshot.execution_authorized is False
    assert snapshot.order_preparation_authorized is False
    assert snapshot.live_order_impact == "read-only"
    assert snapshot.missing_gates == ["approved_order_management_path"]

    report = render_execution_gate_report(snapshot)
    assert "management_plan_only_execution_gate_missing" in report
    assert "approved Janus portfolio order-management path" in report
    assert "does not place, cancel, replace, submit, prepare, or authorize" in report


def test_execution_gate_snapshot_rejects_non_authoritative_truth_sources_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="trend_entry",
        **_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "screenshot", "stale_mirror"],
    )

    assert snapshot.result == "management_plan_only_execution_gate_missing"
    assert snapshot.execution_authorized is False
    assert snapshot.missing_gates == ["non_runtime_truth_rejected"]
    assert snapshot.rejected_truth_sources == ["screenshot", "stale_mirror"]


def test_execution_gate_snapshot_requires_named_adapter_budget_killswitch_and_policy_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        target_stop_rebuy_policy=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api", "portfolio_ledger"],
    )

    assert snapshot.result == "management_plan_only_execution_gate_missing"
    assert snapshot.execution_authorized is False
    assert snapshot.missing_gates == [
        "approved_order_management_path",
        "portfolio_ledger_path",
        "separate_risk_budget",
        "minimum_order_compliance",
        "target_stop_rebuy_policy",
        "kill_switch_clear",
    ]


def test_execution_gate_snapshot_requires_idempotency_and_reconciliation_plan_pytest() -> None:
    proof = _execution_proof_kwargs()
    proof["idempotency_key"] = ""
    proof["reconciliation_plan"] = {}

    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        **proof,
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api", "portfolio_ledger"],
    )

    assert snapshot.result == "management_plan_only_execution_gate_missing"
    assert snapshot.execution_authorized is False
    assert snapshot.order_preparation_authorized is False
    assert snapshot.missing_gates == ["portfolio_ledger_path"]


def test_execution_gate_snapshot_rejects_market_order_exception_proof_pytest() -> None:
    proof = _execution_proof_kwargs()
    proof["minimum_order_proof"] = {
        "side": "sell",
        "order_type": "market",
        "size": 5.0,
        "notional_usd": 1.95,
        "min_size": 5.0,
        "market_order_exception_approved": True,
    }
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        **proof,
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api", "portfolio_ledger"],
    )

    assert snapshot.result == "management_plan_only_execution_gate_missing"
    assert snapshot.execution_authorized is False
    assert snapshot.order_preparation_authorized is False
    assert snapshot.missing_gates == ["minimum_order_compliance"]


def test_execution_gate_snapshot_satisfies_only_when_all_gates_true_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_close",
        **_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api", "portfolio_ledger"],
    )

    assert snapshot.result == "execution_gates_satisfied"
    assert snapshot.execution_authorized is True
    assert snapshot.order_preparation_authorized is True
    assert snapshot.live_order_impact == "order-path"
    assert snapshot.missing_gates == []


def test_manager_action_plan_records_missing_gates_without_order_preparation_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_replace",
        market_title="NBA title winner",
        market_slug="nba-title-winner-2026",
        token_id="token-123",
        **_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api"],
        evidence={"direct_open_order_count": 1},
    )

    plan = build_manager_action_plan(
        gate_snapshot=snapshot,
        proposed_action={"target_state": "target_stale", "desired_state": "replace_target_after_review"},
        generated_at_utc="2026-05-18T13:00:00Z",
    )

    assert plan.status == "management_plan_only_execution_gate_missing"
    assert plan.execution_authorized is False
    assert plan.order_preparation_authorized is False
    assert plan.live_order_impact == "read-only"
    assert plan.ledger_record["schema_version"] == "global_portfolio_manager_action_ledger_v1"
    assert plan.ledger_record["missing_gates"] == ["approved_order_management_path"]
    assert plan.ledger_record["proposed_action"] == {
        "target_state": "target_stale",
        "desired_state": "replace_target_after_review",
    }
    assert plan.operator_review_questions == ["Which missing gate should be implemented or validated next?"]

    report = render_manager_action_plan(plan)
    assert "management_plan_only_execution_gate_missing" in report
    assert "No orders were placed, cancelled, replaced, submitted, prepared, authorized, or executed" in report
    assert "approved Janus portfolio order-management path" in report


def test_manager_action_plan_is_ready_only_after_gate_snapshot_succeeds_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        market_title="NBA title winner",
        market_slug="nba-title-winner-2026",
        token_id="token-123",
        **_execution_proof_kwargs(),
        direct_clob_truth_fresh=True,
        market_token_order_state_resolved=True,
        approved_order_management_path=True,
        portfolio_ledger_path=True,
        separate_risk_budget=True,
        minimum_order_compliance=True,
        kill_switch_clear=True,
        non_runtime_truth_rejected=True,
        truth_sources=["direct_clob", "janus_api", "portfolio_ledger"],
    )

    plan = build_manager_action_plan(
        gate_snapshot=snapshot,
        management_plan=["Submit only through the approved portfolio manager order path."],
        generated_at_utc="2026-05-18T13:00:00Z",
    )

    assert plan.status == "ready_for_approved_order_management_call"
    assert plan.execution_authorized is True
    assert plan.order_preparation_authorized is True
    assert plan.live_order_impact == "order-path"
    assert plan.ledger_record["missing_gates"] == []

    report = render_manager_action_plan(plan)
    assert "ready_for_approved_order_management_call" in report
    assert "A separate approved order-management call is still required" in report
