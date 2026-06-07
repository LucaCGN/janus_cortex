from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.agentic.global_portfolio import (
    GlobalPortfolioWatchlistEntry,
    build_20_slot_board,
    build_deep_pass_plan,
    build_execution_gate_snapshot,
    build_global_portfolio_dynamic_budget,
    build_grid_eligibility_review,
    build_manager_action_plan,
    build_top_holder_scan,
    build_watchlist_artifact,
    load_watchlist_source,
    render_execution_gate_report,
    render_manager_action_plan,
    render_watchlist_report,
    score_portfolio_candidates,
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


def test_execution_gate_snapshot_requires_market_token_identity_for_resolved_state_pytest() -> None:
    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        market_title="Global target market",
        market_slug="global-target-market",
        token_id=None,
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

    diagnostics = snapshot.proof_diagnostics

    assert snapshot.execution_authorized is False
    assert snapshot.missing_gates == ["market_token_order_state_resolved"]
    assert diagnostics["next_missing_gate"] == "market_token_order_state_resolved"
    assert diagnostics["gates"]["market_token_order_state_resolved"]["passed"] is False
    assert diagnostics["gates"]["market_token_order_state_resolved"]["missing_fields"] == ["token_id"]


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
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
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
        market_title="Global trend market",
        market_slug="global-trend-market",
        token_id="token-demo",
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
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
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
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
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


def test_execution_gate_snapshot_allows_low_notional_limit_order_when_share_minimum_passes_pytest() -> None:
    proof = _execution_proof_kwargs()
    proof["risk_budget"] = {
        **dict(proof["risk_budget"]),
        "action_notional_usd": 0.5,
    }
    proof["minimum_order_proof"] = {
        "side": "buy",
        "order_type": "limit",
        "price": 0.1,
        "size": 5.0,
        "notional_usd": 0.5,
        "min_size": 5.0,
        "min_buy_notional_usd": 1.0,
    }

    snapshot = build_execution_gate_snapshot(
        action="existing_position_target",
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
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

    minimum_gate = snapshot.proof_diagnostics["gates"]["minimum_order_compliance"]
    minimum_policy = minimum_gate["minimum_order_policy"]

    assert snapshot.result == "execution_gates_satisfied"
    assert snapshot.execution_authorized is True
    assert snapshot.missing_gates == []
    assert minimum_gate["passed"] is True
    assert "minimum_order_proof_buy_notional_below_minimum" not in minimum_gate["blockers"]
    assert minimum_policy["schema_version"] == "polymarket_order_type_minimum_policy_v1"
    assert minimum_policy["order_type_minimum_rule"] == "limit_min_shares"
    assert minimum_policy["share_minimum_applies"] is True
    assert minimum_policy["notional_minimum_applies"] is False
    assert minimum_policy["required_min_size"] == 5.0
    assert minimum_policy["required_market_notional_usd"] is None
    assert minimum_policy["minimum_rule_status"] == "passed"


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
        market_title="Global target market",
        market_slug="global-target-market",
        token_id="token-demo",
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
        market_title="Global close market",
        market_slug="global-close-market",
        token_id="token-demo",
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


def _twenty_slot_direct_truth_fixture() -> dict[str, object]:
    positions = [
        {
            "market_title": f"Global market {index}",
            "market_slug": f"global-market-{index}",
            "outcome": "Yes",
            "token_id": f"token-global-{index}",
            "size": "5",
            "current_price": "0.40",
            "current_value_usd": "2.00",
            "category": "geopolitics" if index < 4 else "tech",
            "source_actor": "operator",
        }
        for index in range(1, 8)
    ]
    positions.append(
        {
            "market_title": "Will the Oklahoma City Thunder win the 2026 NBA Finals?",
            "market_slug": "nba-finals-2026-okc",
            "outcome": "Yes",
            "token_id": "token-covered-nba",
            "size": "2",
            "current_price": "0.50",
            "current_value_usd": "1.00",
            "category": "nba",
        }
    )
    return {
        "account_id": "account-1",
        "equity_usd": "113.76",
        "cash_usd": "99.66",
        "open_positions": positions,
        "open_orders": [
            {
                "market_title": "Unfilled target sell order should not count",
                "market_slug": "sell-target-market",
                "token_id": "token-sell-target",
                "side": "sell",
                "status": "open",
                "limit_price": "0.60",
                "size": "5",
            }
        ],
    }


def test_twenty_slot_board_counts_global_positions_and_excludes_covered_basketball_pytest() -> None:
    board = build_20_slot_board(_twenty_slot_direct_truth_fixture(), generated_at_utc="2026-05-21T12:00:00Z")

    assert board.schema_version == "global_portfolio_20_slot_board_v1"
    assert board.managed_slot_count == 7
    assert board.empty_slot_count == 13
    assert board.filled_position_slot_count == 7
    assert board.approved_resting_entry_slot_count == 0
    assert board.covered_market_ignored_count == 1
    assert board.budget.effective_sleeve_cap_usd == 50.0
    assert board.budget.codex_sleeve_usage_usd == 14.0
    assert all(slot.risk_cap_usd <= 5.0 for slot in board.slots)
    assert board.side_effects["orders_prepared"] is False


def test_global_portfolio_dynamic_budget_adds_confirmed_realized_pnl_pytest() -> None:
    board = build_20_slot_board(
        {
            "account_id": "account-1",
            "equity_usd": "200",
            "open_positions": [],
            "closed_positions": [
                {
                    "market_title": "Global resolved winner",
                    "market_slug": "global-resolved-winner",
                    "realized_pnl_usd": "2.50",
                }
            ],
        }
    )

    assert board.budget.base_sleeve_cap_usd == 50.0
    assert board.budget.realized_global_pnl_usd == 2.5
    assert board.budget.dynamic_sleeve_cap_usd == 52.5
    assert board.budget.effective_sleeve_cap_usd == 52.5


def test_global_portfolio_dynamic_budget_subtracts_losses_and_ignores_unrealized_pytest() -> None:
    board = build_20_slot_board(
        {
            "account_id": "account-1",
            "equity_usd": "200",
            "open_positions": [
                {
                    "market_title": "Unrealized winner",
                    "market_slug": "unrealized-winner",
                    "token_id": "token-open",
                    "size": "5",
                    "current_price": "0.40",
                    "cashPnl": "99.00",
                }
            ],
            "closed_positions": [
                {
                    "market_title": "Global realized loser",
                    "market_slug": "global-realized-loser",
                    "realized_pnl_usd": "-3.00",
                }
            ],
        }
    )

    assert board.budget.realized_global_pnl_usd == -3.0
    assert board.budget.dynamic_sleeve_cap_usd == 47.0
    assert board.budget.effective_sleeve_cap_usd == 47.0


def test_global_portfolio_dynamic_budget_excludes_sports_and_crypto_options_pytest() -> None:
    budget = build_global_portfolio_dynamic_budget(
        {
            "closed_positions": [
                {
                    "market_title": "Will the New York Liberty win tonight?",
                    "category": "wnba",
                    "realized_pnl_usd": "10.00",
                },
                {
                    "market_title": "Bitcoin Up or Down - May 31, 8:30AM-8:35AM ET",
                    "market_slug": "btc-updown-5m-1780230600",
                    "realized_pnl_usd": "5.00",
                },
                {
                    "market_title": "Global resolved loser",
                    "market_slug": "global-resolved-loser",
                    "realized_pnl_usd": "-1.25",
                },
            ]
        }
    )

    assert float(budget["realized_global_pnl_usd"]) == -1.25
    assert float(budget["dynamic_sleeve_cap_usd"]) == 48.75
    assert budget["excluded_realized_pnl_row_count"] == 2


def test_global_portfolio_dynamic_budget_still_respects_equity_cap_pytest() -> None:
    board = build_20_slot_board(
        {
            "equity_usd": "20",
            "open_positions": [],
            "closed_positions": [{"market_title": "Global winner", "realized_pnl_usd": "100"}],
        }
    )

    assert board.budget.dynamic_sleeve_cap_usd == 150.0
    assert board.budget.effective_sleeve_cap_usd == 10.0


def test_twenty_slot_board_excludes_external_crypto_options_research_rows_pytest() -> None:
    board = build_20_slot_board(
        {
            "account_id": "account-1",
            "equity_usd": "100",
            "open_positions": [
                {
                    "market_title": "Bitcoin Up or Down - May 31, 8:30AM-8:35AM ET",
                    "market_slug": "btc-updown-5m-1780230600",
                    "outcome": "Up",
                    "token_id": "token-btc-updown",
                    "size": "5",
                    "avg_price": "0.40",
                    "current_value_usd": "2.00",
                    "category": "crypto",
                },
                {
                    "market_title": "Will Bitcoin reach $100,000 by December 31, 2026?",
                    "market_slug": "will-bitcoin-reach-100000-by-december-31-2026",
                    "outcome": "Yes",
                    "token_id": "token-bitcoin-long",
                    "size": "5",
                    "avg_price": "0.40",
                    "current_value_usd": "2.00",
                    "category": "crypto",
                },
            ],
            "open_orders": [
                {
                    "market_title": "Ethereum Up or Down - May 31, 8:30AM-8:35AM ET",
                    "market_slug": "eth-updown-5m-1780230600",
                    "outcome": "Down",
                    "token_id": "token-eth-updown",
                    "side": "buy",
                    "status": "open",
                    "limit_price": "0.32",
                    "size": "5",
                    "approved_resting_entry": True,
                }
            ],
        }
    )

    assert board.managed_slot_count == 1
    assert board.empty_slot_count == 19
    assert board.covered_market_ignored_count == 2
    assert board.slots[0].market_slug == "will-bitcoin-reach-100000-by-december-31-2026"
    assert {row["ignore_reason"] for row in board.ignored_covered_market_rows} == {"external_research_scope_excluded"}


def test_twenty_slot_board_counts_approved_resting_entry_orders_pytest() -> None:
    direct_truth = {
        "equity_usd": "20",
        "open_positions": [],
        "open_orders": [
            {
                "market_title": "Approved entry market",
                "market_slug": "approved-entry-market",
                "outcome": "Yes",
                "token_id": "token-entry",
                "side": "buy",
                "status": "open",
                "limit_price": "0.20",
                "size": "5",
                "approved_resting_entry": True,
            }
        ],
    }

    board = build_20_slot_board(direct_truth)

    assert board.managed_slot_count == 1
    assert board.empty_slot_count == 19
    assert board.approved_resting_entry_slot_count == 1
    assert board.slots[0].slot_status == "pending_entry"
    assert board.budget.effective_sleeve_cap_usd == 10.0


def test_twenty_slot_board_counts_local_approved_entry_orders_when_direct_rows_lack_metadata_pytest() -> None:
    direct_truth = {
        "equity_usd": "100",
        "open_positions": [],
        "open_orders": [
            {
                "market_title": "Direct CLOB target without local slot metadata",
                "market_slug": "direct-target",
                "token_id": "token-target",
                "side": "sell",
                "status": "open",
                "limit_price": "0.60",
                "size": "5",
            }
        ],
        "local_open_orders": [
            {
                "market_title": "Approved local entry market",
                "market_slug": "approved-local-entry",
                "token_id": "token-entry-local",
                "side": "buy",
                "status": "submitted",
                "limit_price": "0.57",
                "size": "5",
                "metadata_json": {
                    "approved_resting_entry": True,
                    "counts_as_managed_slot": True,
                    "slot_role": "portfolio_manager_20_slot_entry",
                },
            }
        ],
    }

    board = build_20_slot_board(direct_truth)

    assert board.managed_slot_count == 1
    assert board.empty_slot_count == 19
    assert board.approved_resting_entry_slot_count == 1
    assert board.slots[0].market_slug == "approved-local-entry"


def test_twenty_slot_board_ignores_stale_local_entry_when_direct_open_order_absent_pytest() -> None:
    direct_truth = {
        "equity_usd": "100",
        "open_positions": [],
        "open_orders": [
            {
                "id": "0xlive",
                "token_id": "token-live",
                "side": "BUY",
                "status": "LIVE",
                "price": "0.20",
                "size": "5",
            }
        ],
        "local_open_orders": [
            {
                "order_id": "local-live",
                "external_order_id": "0xlive",
                "market_title": "Live approved entry",
                "market_slug": "live-approved-entry",
                "token_id": "token-live",
                "side": "buy",
                "status": "submitted",
                "limit_price": "0.20",
                "size": "5",
                "metadata_json": {
                    "approved_resting_entry": True,
                    "counts_as_managed_slot": True,
                    "slot_role": "portfolio_manager_20_slot_entry",
                },
            },
            {
                "order_id": "local-stale",
                "external_order_id": "0xstale",
                "market_title": "Stale approved entry",
                "market_slug": "stale-approved-entry",
                "token_id": "token-stale",
                "side": "buy",
                "status": "submitted",
                "limit_price": "0.30",
                "size": "5",
                "metadata_json": {
                    "approved_resting_entry": True,
                    "counts_as_managed_slot": True,
                    "slot_role": "portfolio_manager_20_slot_entry",
                },
            },
        ],
    }

    board = build_20_slot_board(direct_truth)

    assert board.managed_slot_count == 1
    assert board.slots[0].market_slug == "live-approved-entry"
    assert board.budget.codex_sleeve_usage_usd == 1.0


def test_candidate_scoring_rejects_known_blockers_and_promotes_mapped_liquid_candidate_pytest() -> None:
    board = build_20_slot_board(_twenty_slot_direct_truth_fixture())
    queue = score_portfolio_candidates(
        [
            {
                "source": "profile_active_position",
                "market_title": "Profile only market",
                "market_slug": "profile-only-market",
                "profile_signal": {"profile": "winner"},
                "category": "culture",
            },
            {
                "source": "frontend",
                "market_title": "Mapped liquid geopolitics market",
                "market_slug": "mapped-liquid-market",
                "token_id": "token-candidate",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": 1, "depth_usd": 25},
                "proposed_notional_usd": "2.50",
                "category": "macro",
                "confidence": "medium",
                "horizon": "medium",
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "source": "frontend",
                "market_title": "Illiquid market",
                "market_slug": "illiquid-market",
                "token_id": "token-illiquid",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": 5, "depth_usd": 2},
                "category": "finance",
            },
            {
                "source": "frontend",
                "market_title": "Will the Indiana Fever win tonight?",
                "market_slug": "wnba-fever-tonight",
                "token_id": "token-covered",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": 1, "depth_usd": 30},
                "category": "wnba",
            },
        ],
        board,
    )

    by_slug = {candidate.market_slug: candidate for candidate in queue.candidates}
    assert queue.ready_count == 1
    assert by_slug["mapped-liquid-market"].status == "ready_for_order_proof"
    assert "profile_only_without_direct_edge" in by_slug["profile-only-market"].rejection_reasons
    assert "janus_catalog_token_mapping_missing" in by_slug["profile-only-market"].rejection_reasons
    assert "illiquid_or_wide_spread" in by_slug["illiquid-market"].rejection_reasons
    assert "covered_market_excluded" in by_slug["wnba-fever-tonight"].rejection_reasons


def test_candidate_scoring_rejects_external_crypto_options_research_rows_pytest() -> None:
    board = build_20_slot_board({"account_id": "acct", "equity_usd": "100", "open_positions": [], "open_orders": []})
    queue = score_portfolio_candidates(
        [
            {
                "source": "future-domain-research-agent",
                "market_title": "Bitcoin Up or Down - May 31, 8:30AM-8:35AM ET",
                "market_slug": "btc-updown-5m-1780230600",
                "token_id": "token-btc-updown",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.5", "depth_usd": "100"},
                "proposed_price": "0.40",
                "proposed_notional_usd": "2.00",
                "expected_return_cents": "4",
                "category": "crypto",
                "end_date": "2026-05-31T13:35:00Z",
            },
            {
                "source": "frontend",
                "market_title": "Mapped liquid culture market",
                "market_slug": "mapped-liquid-culture-market",
                "token_id": "token-culture",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "1", "depth_usd": "50"},
                "proposed_price": "0.35",
                "proposed_notional_usd": "2.50",
                "expected_return_cents": "5",
                "category": "culture",
                "end_date": "2026-12-31T00:00:00Z",
            },
        ],
        board,
        generated_at_utc="2026-05-31T12:00:00Z",
    )

    by_slug = {candidate.market_slug: candidate for candidate in queue.candidates}
    assert by_slug["btc-updown-5m-1780230600"].status == "rejected"
    assert "external_research_scope_excluded" in by_slug["btc-updown-5m-1780230600"].rejection_reasons
    assert by_slug["mapped-liquid-culture-market"].status == "ready_for_order_proof"


def test_candidate_scoring_marks_scale_limited_micro_trade_without_blocking_validation_pytest() -> None:
    board = build_20_slot_board(_twenty_slot_direct_truth_fixture())
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Micro viable but not scalable market",
                "market_slug": "micro-not-scalable",
                "token_id": "token-micro-scale-limited",
                "janus_catalog_mapped": True,
                "strategy_style": "quick_trade",
                "direct_orderbook": {
                    "spread_cents": "1",
                    "depth_usd": "40",
                    "liquidity_capacity_usd": "40",
                    "price_impact_1000_usd_cents": "5",
                },
                "proposed_price": "0.20",
                "proposed_notional_usd": "5",
                "expected_return_cents": "10",
                "expected_hold_days": 7,
                "category": "finance",
                "end_date": "2026-12-31T00:00:00Z",
            }
        ],
        board,
    )

    candidate = queue.candidates[0]
    assert candidate.status == "ready_for_order_proof"
    assert candidate.sizing_tier == "micro_only"
    assert "scale_limited_quick_trade" in candidate.risk_return_flags
    assert candidate.slippage_to_edge_ratio == 0.05
    assert candidate.sizing_guidance["current_notional_is_scale_proof"] is False


def test_candidate_scoring_expands_low_price_buy_to_minimum_notional_pytest() -> None:
    board = build_20_slot_board({"account_id": "acct", "equity_usd": "100", "open_positions": [], "open_orders": []})
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Low price liquid candidate",
                "market_slug": "low-price-liquid-candidate",
                "token_id": "token-low-price",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.1", "depth_usd": "1000"},
                "proposed_price": "0.008",
                "proposed_size": "5",
                "proposed_notional_usd": "0.04",
                "expected_return_cents": "2",
                "category": "politics",
                "end_date": "2028-11-07T00:00:00Z",
            }
        ],
        board,
        generated_at_utc="2026-05-31T12:00:00Z",
    )

    candidate = queue.candidates[0]
    assert candidate.status == "ready_for_order_proof"
    assert candidate.proposed_notional_usd == 1.0
    assert candidate.proposed_size == 125.0


def test_candidate_scoring_rewards_high_volume_low_price_cashout_candidates_pytest() -> None:
    board = build_20_slot_board({"account_id": "acct", "equity_usd": "100", "open_positions": [], "open_orders": []})
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "High volume low price rebound",
                "market_slug": "high-volume-low-price-rebound",
                "token_id": "token-high-volume",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.5", "depth_usd": "500", "liquidity_capacity_usd": "500"},
                "proposed_price": "0.08",
                "proposed_notional_usd": "2.50",
                "market_volume_usd": "150000",
                "volume_24h_usd": "125000",
                "expected_return_cents": "4",
                "expected_hold_days": 3,
                "price_history_features": {"windows": {"1d": {"range_cents": "7"}}},
                "grid_backtest_summary": {"status": "positive_after_costs", "profitable": True},
                "category": "politics",
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "source": "frontend",
                "market_title": "Thin high priced long hold",
                "market_slug": "thin-high-priced-long-hold",
                "token_id": "token-thin-high",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.5", "depth_usd": "500", "liquidity_capacity_usd": "500"},
                "proposed_price": "0.82",
                "proposed_notional_usd": "4.10",
                "market_volume_usd": "5000",
                "expected_return_cents": "4",
                "expected_hold_days": 30,
                "category": "culture",
                "end_date": "2026-12-31T00:00:00Z",
            },
        ],
        board,
        generated_at_utc="2026-05-31T12:00:00Z",
    )

    first, second = queue.candidates
    assert first.market_slug == "high-volume-low-price-rebound"
    assert first.market_volume_usd == 150000.0
    assert first.price_bucket == "5c_to_15c"
    assert first.low_price_score > second.low_price_score
    assert first.cashout_score > second.cashout_score
    assert first.grid_backtest_summary["status"] == "positive_after_costs"


def test_candidate_scoring_rejects_expired_market_even_when_budget_available_pytest() -> None:
    board = build_20_slot_board(_twenty_slot_direct_truth_fixture())
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Expired Iran deadline market",
                "market_slug": "expired-iran-deadline",
                "token_id": "token-expired",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "1", "depth_usd": "1000", "liquidity_capacity_usd": "1000"},
                "proposed_price": "0.06",
                "proposed_notional_usd": "0.30",
                "expected_return_cents": "8",
                "expected_hold_days": 1,
                "category": "geopolitics",
                "end_date": "2026-05-26T00:00:00Z",
            }
        ],
        board,
        generated_at_utc="2026-05-26T12:13:00Z",
    )

    candidate = queue.candidates[0]
    assert candidate.status == "rejected"
    assert "market_expired_or_closed" in candidate.rejection_reasons


def test_candidate_scoring_reserves_concentration_family_across_same_batch_pytest() -> None:
    board = build_20_slot_board({"account_id": "acct", "open_positions": [], "open_orders": []})
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "MicroStrategy sells any Bitcoin by June 30, 2026?",
                "market_slug": "microstrategy-sells-any-bitcoin-by-june-30-2026",
                "token_id": "token-mstr-jun-yes",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.3", "depth_usd": "500"},
                "proposed_price": "0.73",
                "proposed_notional_usd": "3.65",
                "expected_return_cents": "4",
                "category": "crypto",
                "end_date": "2026-06-30T00:00:00Z",
            },
            {
                "source": "frontend",
                "market_title": "MicroStrategy sells any Bitcoin by May 31, 2026?",
                "market_slug": "microstrategy-sells-any-bitcoin-by-may-31-2026",
                "token_id": "token-mstr-may-no",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.7", "depth_usd": "500"},
                "proposed_price": "0.85",
                "proposed_notional_usd": "4.25",
                "expected_return_cents": "4",
                "category": "crypto",
                "end_date": "2026-05-31T00:00:00Z",
            },
        ],
        board,
        generated_at_utc="2026-05-30T21:00:00Z",
    )

    first, second = queue.candidates
    assert first.status == "ready_for_order_proof"
    assert second.status == "rejected"
    assert "concentration_conflict" in second.rejection_reasons


def test_candidate_scoring_does_not_treat_generic_category_as_family_conflict_pytest() -> None:
    board = build_20_slot_board({"account_id": "acct", "equity_usd": "100", "open_positions": [], "open_orders": []})
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Will Graham Platner win the 2028 Democratic presidential nomination?",
                "market_slug": "will-graham-platner-win-the-2028-democratic-presidential-nomination",
                "token_id": "token-platner",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.1", "depth_usd": "1000"},
                "proposed_price": "0.008",
                "proposed_notional_usd": "0.04",
                "expected_return_cents": "2",
                "category": "uncategorized",
                "end_date": "2028-11-07T00:00:00Z",
            },
            {
                "source": "frontend",
                "market_title": "No one announced as next James Bond?",
                "market_slug": "no-one-announced-as-next-james-bond",
                "token_id": "token-bond",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.5", "depth_usd": "1000"},
                "proposed_price": "0.82",
                "proposed_notional_usd": "4.10",
                "expected_return_cents": "4",
                "category": "uncategorized",
                "end_date": "2026-12-31T00:00:00Z",
            },
        ],
        board,
        generated_at_utc="2026-05-31T12:00:00Z",
    )

    assert [candidate.status for candidate in queue.candidates] == [
        "ready_for_order_proof",
        "ready_for_order_proof",
    ]


def test_candidate_scoring_rejects_duplicate_existing_market_slug_pytest() -> None:
    board = build_20_slot_board(
        {
            "account_id": "acct",
            "equity_usd": "100",
            "open_positions": [
                {
                    "market_title": "Will the U.S. invade Cuba in 2026?",
                    "market_slug": "will-the-us-invade-cuba-in-2026",
                    "outcome": "Yes",
                    "token_id": "token-cuba",
                    "size": "5",
                    "current_value_usd": "1.25",
                }
            ],
            "open_orders": [],
        }
    )
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Will the U.S. invade Cuba in 2026?",
                "market_slug": "will-the-us-invade-cuba-in-2026",
                "token_id": "token-cuba",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "1", "depth_usd": "1000"},
                "proposed_price": "0.25",
                "proposed_notional_usd": "1.25",
                "expected_return_cents": "3",
                "end_date": "2026-12-31T00:00:00Z",
            }
        ],
        board,
        generated_at_utc="2026-05-31T12:00:00Z",
    )

    candidate = queue.candidates[0]
    assert candidate.status == "rejected"
    assert "duplicate_existing_slot" in candidate.rejection_reasons


def test_candidate_scoring_rejects_same_event_family_across_distinct_markets_pytest() -> None:
    board = build_20_slot_board({"account_id": "acct", "equity_usd": "100", "open_positions": [], "open_orders": []})
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Will Graham Platner win the 2028 Democratic presidential nomination?",
                "market_slug": "will-graham-platner-win-the-2028-democratic-presidential-nomination",
                "token_id": "token-platner",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.1", "depth_usd": "1000"},
                "proposed_price": "0.008",
                "proposed_notional_usd": "1",
                "expected_return_cents": "2",
                "end_date": "2028-11-07T00:00:00Z",
            },
            {
                "source": "frontend",
                "market_title": "Will Barack Obama win the 2028 Democratic presidential nomination?",
                "market_slug": "will-barack-obama-win-the-2028-democratic-presidential-nomination",
                "token_id": "token-obama",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.1", "depth_usd": "1000"},
                "proposed_price": "0.008",
                "proposed_notional_usd": "1",
                "expected_return_cents": "2",
                "end_date": "2028-11-07T00:00:00Z",
            },
        ],
        board,
        generated_at_utc="2026-05-31T12:00:00Z",
    )

    first, second = queue.candidates
    assert first.status == "ready_for_order_proof"
    assert second.status == "rejected"
    assert "concentration_conflict" in second.rejection_reasons


def test_candidate_scoring_rejects_microstrategy_when_existing_bitcoin_family_slot_exists_pytest() -> None:
    board = build_20_slot_board(
        {
            "account_id": "acct",
            "open_positions": [
                {
                    "title": "Will Bitcoin reach $100,000 by December 31, 2026?",
                    "slug": "will-bitcoin-reach-100000-by-december-31-2026",
                    "asset": "token-bitcoin",
                    "outcome": "Yes",
                    "size": "5",
                    "avg_price": "0.40",
                    "current_value": "2.00",
                }
            ],
            "open_orders": [],
        }
    )
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "MicroStrategy sells any Bitcoin by June 30, 2026?",
                "market_slug": "microstrategy-sells-any-bitcoin-by-june-30-2026",
                "token_id": "token-mstr-jun-yes",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": "0.3", "depth_usd": "500"},
                "proposed_price": "0.73",
                "proposed_notional_usd": "3.65",
                "expected_return_cents": "4",
                "category": "crypto",
                "end_date": "2026-06-30T00:00:00Z",
            }
        ],
        board,
        generated_at_utc="2026-05-30T21:00:00Z",
    )

    candidate = queue.candidates[0]
    assert candidate.status == "rejected"
    assert "concentration_conflict" in candidate.rejection_reasons


def test_candidate_scoring_rejects_quick_trade_when_slippage_consumes_edge_pytest() -> None:
    board = build_20_slot_board(_twenty_slot_direct_truth_fixture())
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Too tight quick edge",
                "market_slug": "too-tight-quick-edge",
                "token_id": "token-tight-edge",
                "janus_catalog_mapped": True,
                "strategy_style": "quick_trade",
                "direct_orderbook": {"spread_cents": "1", "depth_usd": "100", "liquidity_capacity_usd": "500"},
                "estimated_entry_slippage_cents": "1.2",
                "proposed_price": "0.30",
                "proposed_notional_usd": "2.50",
                "expected_return_cents": "2",
                "expected_hold_days": 3,
                "category": "finance",
                "end_date": "2026-12-31T00:00:00Z",
            }
        ],
        board,
    )

    candidate = queue.candidates[0]
    assert candidate.status == "rejected"
    assert "slippage_consumes_expected_edge" in candidate.rejection_reasons
    assert "quick_trade_edge_too_small_after_slippage" in candidate.rejection_reasons


def test_candidate_scoring_rewards_payoff_velocity_over_slow_capital_drag_pytest() -> None:
    board = build_20_slot_board(_twenty_slot_direct_truth_fixture())
    queue = score_portfolio_candidates(
        [
            {
                "source": "frontend",
                "market_title": "Slow long thesis",
                "market_slug": "slow-long-thesis",
                "token_id": "token-slow-long",
                "janus_catalog_mapped": True,
                "strategy_style": "long_thesis",
                "direct_orderbook": {"spread_cents": "0.5", "depth_usd": "500", "liquidity_capacity_usd": "500"},
                "proposed_price": "0.20",
                "proposed_notional_usd": "2.50",
                "expected_return_cents": "10",
                "expected_hold_days": 180,
                "category": "culture",
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "source": "frontend",
                "market_title": "Fast asymmetric swing",
                "market_slug": "fast-asymmetric-swing",
                "token_id": "token-fast-swing",
                "janus_catalog_mapped": True,
                "strategy_style": "quick_trade",
                "direct_orderbook": {"spread_cents": "0.5", "depth_usd": "500", "liquidity_capacity_usd": "500"},
                "proposed_price": "0.20",
                "proposed_notional_usd": "2.50",
                "expected_return_cents": "4",
                "expected_hold_days": 7,
                "category": "economy",
                "end_date": "2026-12-31T00:00:00Z",
            },
        ],
        board,
    )

    assert queue.candidates[0].market_slug == "fast-asymmetric-swing"
    assert queue.candidates[0].payoff_velocity_score > queue.candidates[1].payoff_velocity_score


def test_top_holder_scan_promotes_high_profit_profiles_on_yes_and_no_sides_pytest() -> None:
    scan = build_top_holder_scan(
        market_title="US-Iran nuclear deal before 2027?",
        market_slug="us-iran-nuclear-deal-before-2027",
        yes_holders=[{"username": "ScottyNooo", "profit_loss_usd": "680590", "shares": "23.2"}],
        no_holders=[{"username": "ImJustKen", "profit_loss_usd": "531838", "shares": "12.1"}],
        min_profit_usd="100000",
        generated_at_utc="2026-05-21T12:00:00Z",
    )

    assert scan.yes_holders_seen == 1
    assert scan.no_holders_seen == 1
    assert scan.high_profit_profile_count == 2
    assert {profile["side"] for profile in scan.high_profit_profiles} == {"yes", "no"}


def test_grid_eligibility_requires_30_day_movement_window_and_explicit_service_approval_pytest() -> None:
    eligible = build_grid_eligibility_review(
        market_title="Oscillating geopolitics market",
        thirty_day_range_percent="12",
        days_to_resolution=45,
        stable_thesis=True,
        spread_cents="1",
        depth_usd="20",
        explicit_service_spawn_approval=True,
    )
    blocked = build_grid_eligibility_review(
        market_title="Too quiet market",
        thirty_day_range_percent="8",
        days_to_resolution=20,
        stable_thesis=True,
        spread_cents="1",
        depth_usd="20",
        explicit_service_spawn_approval=False,
    )

    assert eligible.status == "eligible_for_service_spawn_proof"
    assert eligible.eligible is True
    assert blocked.status == "blocked_missing_grid_gates"
    assert "thirty_day_range_below_10_percent" in blocked.blockers
    assert "resolution_window_below_30_days" in blocked.blockers
    assert "explicit_service_spawn_approval_missing" in blocked.blockers


def test_grid_eligibility_accepts_price_memory_window_backtest_validator_pytest() -> None:
    review = build_grid_eligibility_review(
        market_title="Sideways high volume rebound",
        market_slug="sideways-high-volume-rebound",
        token_id="token-sideways",
        premise_state="sideways_valid",
        one_day_range_cents="6",
        seven_day_range_cents="8",
        thirty_day_range_cents="12",
        days_to_resolution=2,
        stable_thesis=True,
        spread_cents="1",
        depth_usd="50",
        one_day_backtest_positive=True,
        explicit_service_spawn_approval=True,
        backtest_json={"status": "positive_after_costs", "completed_cycle_count": 2},
    )

    assert review.status == "eligible_for_service_spawn_proof"
    assert review.eligible is True
    assert review.validator_profile == "1d_5c_sideways_positive_backtest"
    assert review.recommended_worker_duration_hours == 24
    assert review.blockers == []


def test_deep_pass_reports_slot_deficit_and_selected_bounded_candidate_pytest() -> None:
    plan = build_deep_pass_plan(
        _twenty_slot_direct_truth_fixture(),
        candidate_rows=[
            {
                "source": "frontend",
                "market_title": "Mapped liquid AI market",
                "market_slug": "mapped-liquid-ai-market",
                "token_id": "token-ai-candidate",
                "janus_catalog_mapped": True,
                "direct_orderbook": {"spread_cents": 1, "depth_usd": 40},
                "proposed_notional_usd": "2.50",
                "category": "ai",
                "horizon": "medium",
                "end_date": "2026-12-31T00:00:00Z",
            }
        ],
        generated_at_utc="2026-05-21T12:00:00Z",
    )

    assert plan.status == "slot_deficit_candidate_ready"
    assert plan.board.empty_slot_count == 13
    assert plan.selected_candidate is not None
    assert plan.selected_candidate.market_slug == "mapped-liquid-ai-market"
    assert plan.required_order_path == "portfolio-manager-order"
    assert plan.side_effects["orders_placed"] is False
