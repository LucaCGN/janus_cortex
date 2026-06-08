from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies import promotion
from crypto_options_app.strategies.promotion import (
    StrategyPromotionPolicy,
    evaluate_and_persist_strategy_promotions,
    promotion_policy_contract,
    promotion_state_summary,
)
from crypto_options_app.workers.strategy_backtest_replay import StrategyBacktestReplayConfig, run_strategy_backtest_replay
from crypto_options_app.workers.strategy_live_replay import StrategyLiveReplayConfig, run_strategy_live_replay


def test_strategy_promotion_manager_blocks_live_until_signal_gate_is_clean_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion.sqlite")
    strategy_ids = ("profile_hedge_scalping_v1", "hedger_ratio_replication_v4")

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-promotion-backtest",
            strategy_ids=strategy_ids,
            max_trades_per_strategy=1,
        )
    )
    run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-promotion-live-replay",
            strategy_ids=strategy_ids,
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        summary = evaluate_and_persist_strategy_promotions(conn)
        persisted_count = count_rows(conn, "strategy_promotion_state")

    assert summary["schema_version"] == "crypto_options_strategy_promotion_summary_v1"
    assert summary["orders_allowed"] is True
    assert summary["live_trading_authorized"] is True
    assert summary["manual_orders_allowed"] is False
    assert summary["manual_orders_avoided"] is True
    assert persisted_count == summary["strategy_count"]
    assert summary["signal_gate"]["promotion_ready_signal_count"] == 0

    by_id = {row["strategy_id"]: row for row in summary["strategies"]}
    for strategy_id in strategy_ids:
        row = by_id[strategy_id]
        assert row["promotion_state"] == "SHADOW_READY"
        assert row["evidence"]["historical_replay_pass_count"] == 1
        assert row["evidence"]["live_replay_pass_count"] == 1
        assert row["evidence"]["live_replay_economic_sample_count"] == 0
        assert row["evidence"]["recent_shadow_live_economic_sample_count"] == 0
        assert "missing_shadow_live_economic_evidence" in row["blockers"]
        assert "missing_recent_shadow_live_economic_evidence" in row["blockers"]
        assert "no_promotion_ready_signals" in row["blockers"]
        assert row["orders_allowed"] is False
        assert row["live_trading_authorized"] is False

    untouched = by_id["grid_band_rebound_v3"]
    assert untouched["promotion_state"] == "NEEDS_BACKTEST"
    assert "missing_historical_strategy_replay" in untouched["blockers"]


def test_strategy_promotion_signal_gate_allows_selected_promotion_ready_candidates_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-ready-signal.sqlite")

    monkeypatch.setattr(
        promotion,
        "validation_status",
        lambda conn: {
            "signal_count": 1,
            "by_promotion_state": {"PROMOTION_READY": 1},
            "signals": [
                {
                    "signal_id": "pytest_selected_signal",
                    "promotion_state": "PROMOTION_READY",
                    "signal_type": "support_resistance",
                    "source_blocks": ["C"],
                    "sources": ["optionprice"],
                    "impact_if_degraded": "critical",
                    "distinct_event_count": 120,
                    "strict_review_reasons": [],
                }
            ],
        },
    )

    with connect(db_path) as conn:
        summary = evaluate_and_persist_strategy_promotions(conn)

    assert summary["signal_gate"]["promotion_ready_signal_count"] == 1
    assert summary["signal_gate"]["selected_signal_count"] == 1
    assert summary["signal_gate"]["strict_replay_required_count"] == 0
    assert summary["signal_gate"]["signal_gate_status"] == "passed"
    by_id = {row["strategy_id"]: row for row in summary["strategies"]}
    assert "selected_signals_require_strict_replay" not in by_id["profile_hedge_scalping_v1"]["blockers"]


def test_strategy_promotion_signal_gate_blocks_selected_strict_replay_candidates_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-strict-replay.sqlite")

    monkeypatch.setattr(
        promotion,
        "validation_status",
        lambda conn: {
            "signal_count": 1,
            "by_promotion_state": {"STRUCTURAL_PASS": 1},
            "signals": [
                {
                    "signal_id": "pytest_selected_signal",
                    "promotion_state": "STRUCTURAL_PASS",
                    "signal_type": "support_resistance",
                    "source_blocks": ["C"],
                    "sources": ["optionprice"],
                    "impact_if_degraded": "critical",
                    "distinct_event_count": 120,
                    "needs_strict_replay": True,
                    "strict_review_reasons": ["strict_pair_snapshot_replay_required"],
                }
            ],
        },
    )

    with connect(db_path) as conn:
        summary = evaluate_and_persist_strategy_promotions(conn)

    assert summary["signal_gate"]["promotion_ready_signal_count"] == 0
    assert summary["signal_gate"]["selected_signal_count"] == 1
    assert summary["signal_gate"]["strict_replay_required_count"] == 1
    assert summary["signal_gate"]["signal_gate_status"] == "blocked"
    assert "strict_replay_required_for_selected_signals" in summary["signal_gate"]["blockers"]
    by_id = {row["strategy_id"]: row for row in summary["strategies"]}
    assert "selected_signals_require_strict_replay" in by_id["profile_hedge_scalping_v1"]["blockers"]


def test_strategy_promotion_signal_gate_does_not_treat_passed_as_promotable_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-passed-label.sqlite")

    monkeypatch.setattr(
        promotion,
        "validation_status",
        lambda conn: {
            "signal_count": 1,
            "by_promotion_state": {"PASSED": 1},
            "signals": [
                {
                    "signal_id": "pytest_passed_is_not_promoted",
                    "promotion_state": "PASSED",
                    "status": "PASSED",
                    "queue_status": "PASSED",
                    "signal_type": "support_resistance",
                    "source_blocks": ["C"],
                    "sources": ["optionprice"],
                    "impact_if_degraded": "critical",
                    "distinct_event_count": 120,
                    "strict_review_reasons": [],
                }
            ],
        },
    )

    with connect(db_path) as conn:
        summary = evaluate_and_persist_strategy_promotions(conn)

    assert summary["signal_gate"]["promotion_ready_signal_count"] == 0
    assert summary["signal_gate"]["signal_gate_status"] == "blocked"
    assert "no_promotion_ready_signals" in summary["signal_gate"]["blockers"]
    by_id = {row["strategy_id"]: row for row in summary["strategies"]}
    assert "no_promotion_ready_signals" in by_id["profile_hedge_scalping_v1"]["blockers"]


def test_strategy_promotion_summary_renders_policy_contract_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-policy-contract.sqlite")

    with connect(db_path) as conn:
        refreshed = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )
        cached = promotion_state_summary(conn)

    contract = refreshed["policy_contract"]
    assert contract["schema_version"] == "crypto_options_promotion_policy_contract_v1"
    assert "PASSED" in contract["signals"]["not_promotable_labels"]
    assert contract["signals"]["strict_replay_required_means_promotable"] is False
    assert contract["strategies"]["live_candidate_requirements"]["recent_distinct_economic_samples"] == 3
    assert contract["strategies"]["live_candidate_requirements"]["recent_shadow_live_win_rate_gt"] == 0.7
    assert contract["strategies"]["live_candidate_requirements"]["strict_signal_blockers"] == 0
    assert contract["strategies"]["strategy_defined_criteria"]["supported"] is True
    assert contract["live_safety"]["chat_judgment_can_authorize_live"] is False
    assert contract["live_safety"]["automation_can_authorize_live"] is True
    assert contract["live_safety"]["manual_orders_allowed"] is False
    assert cached["policy_contract"]["schema_version"] == contract["schema_version"]


def test_promotion_policy_contract_reflects_custom_thresholds_pytest() -> None:
    contract = promotion_policy_contract(
        StrategyPromotionPolicy(
            min_recent_shadow_live_sample_count=24,
            min_recent_shadow_live_win_rate=0.8,
            live_budget_cap_usd=25.0,
        )
    )

    assert contract["strategies"]["live_candidate_requirements"]["recent_distinct_economic_samples"] == 24
    assert contract["strategies"]["live_candidate_requirements"]["recent_shadow_live_win_rate_gt"] == 0.8
    assert contract["strategies"]["budget_policy"]["live_budget_cap_usd"] == 25.0


def test_strategy_policy_override_supports_low_win_rate_high_pnl_path_pytest() -> None:
    spec = SimpleNamespace(
        metadata={
            "promotion_policy": {
                "min_recent_shadow_live_win_rate": 0.55,
                "min_recent_shadow_live_pnl_usd": 5.0,
                "max_loss_streak": 3,
                "max_live_loss_usd": 1.0,
            }
        },
        risk_gates={},
        live_pulse_requirements={},
    )

    effective, overrides = promotion._strategy_policy_for_spec(spec, base_policy=StrategyPromotionPolicy())

    assert effective.min_recent_shadow_live_win_rate == 0.55
    assert effective.min_recent_shadow_live_pnl_usd == 5.0
    assert effective.max_recent_shadow_live_loss_streak == 3
    assert effective.max_supervised_live_loss_usd == 1.0
    assert overrides == {
        "max_recent_shadow_live_loss_streak": 3,
        "max_supervised_live_loss_usd": 1.0,
        "min_recent_shadow_live_pnl_usd": 5.0,
        "min_recent_shadow_live_win_rate": 0.55,
    }


def test_strategy_promotion_demotes_losing_supervised_live_evidence_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-demotion.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-promotion-demotion-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )
    run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-promotion-demotion-shadow",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        now = "2026-06-05T18:00:00+00:00"
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version,
                run_type, run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES(?, ?, 'v1', 'supervised_live', 'supervised_live', 'completed',
                   'pytest-live-loss', 'pytest-live-loss-validation', '[]',
                   '{"orders_allowed": true, "live_trading_authorized": true}',
                   10, 1, 1, 60, '{}', 'passed', 'reconciled', 1,
                   'trusted_balance_reported', '{}', ?, ?, ?)
            """,
            ("pytest-supervised-loss-row", strategy_id, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO validation_budget_ledger(
                validation_budget_ledger_key, validation_run_id, strategy_or_component_id,
                started_at_utc, completed_at_utc, budget_cap_usd,
                notional_submitted_usd, notional_filled_usd, realized_pnl_usd,
                open_cost_usd, remaining_validation_budget_usd,
                cash_balance_before_usd, cash_balance_after_usd, cash_balance_status,
                hard_stop_triggered, stop_reason, lifecycle_audit_status,
                reconciliation_status, ledger_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, 'pytest-live-loss-validation', ?, ?, ?, 10,
               12, 12, -11.25, 0, 0, 206.27, 195.02,
                   'trusted_balance_reported', 0, NULL, 'passed',
                   'reconciled', '{}', ?, ?)
            """,
            ("pytest-supervised-loss-ledger", strategy_id, now, now, now, now),
        )
        summary = evaluate_and_persist_strategy_promotions(conn)

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "DEMOTED_TO_SHADOW"
    assert "supervised_live_realized_pnl_below_loss_limit" in row["blockers"]
    assert row["evidence"]["supervised_live_realized_pnl_usd"] == -11.25
    assert row["orders_allowed"] is False
    assert row["live_trading_authorized"] is False


def test_strategy_promotion_demotes_three_live_losses_from_strategy_criteria_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-live-loss-streak.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-loss-streak-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )
    run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-loss-streak-shadow",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        for index, pnl in enumerate((-0.25, -0.50, -0.75), start=1):
            now = f"2026-06-05T18:0{index}:00+00:00"
            conn.execute(
                """
                INSERT INTO validation_budget_ledger(
                    validation_budget_ledger_key, validation_run_id, strategy_or_component_id,
                    started_at_utc, completed_at_utc, budget_cap_usd,
                    notional_submitted_usd, notional_filled_usd, realized_pnl_usd,
                    open_cost_usd, remaining_validation_budget_usd,
                    cash_balance_before_usd, cash_balance_after_usd, cash_balance_status,
                    hard_stop_triggered, stop_reason, lifecycle_audit_status,
                    reconciliation_status, ledger_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, ?, ?, 30,
                       2, 2, ?, 0, 28, 206.27, 205.52,
                       'trusted_balance_reported', 0, NULL, 'passed',
                       'reconciled', '{}', ?, ?)
                """,
                (f"pytest-live-loss-ledger-{index}", f"pytest-live-loss-validation-{index}", strategy_id, now, now, pnl, now, now),
            )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(
                min_promotion_ready_signals=0,
                max_supervised_live_loss_usd=10.0,
                max_recent_shadow_live_loss_streak=3,
            ),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "DEMOTED_TO_SHADOW"
    assert row["evidence"]["supervised_live_loss_streak"] == 3
    assert "supervised_live_loss_streak_exceeds_strategy_limit" in row["blockers"]
    assert "supervised_live_realized_pnl_below_loss_limit" not in row["blockers"]


def test_strategy_promotion_ignores_legacy_immediate_bid_shadow_economics_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-legacy-bid-shadow.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-legacy-bid-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-legacy-bid-shadow-row",
            strategy_id=strategy_id,
            pnl_usd=-0.01,
            win_rate=0.0,
            sample_count=1,
            source="runtime_shadow_mark_to_observed_bid",
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["evidence"]["live_replay_pass_count"] == 1
    assert row["evidence"]["live_replay_economic_sample_count"] == 0
    assert row["evidence"]["recent_shadow_live_economic_sample_count"] == 0
    assert "missing_shadow_live_economic_evidence" in row["blockers"]
    assert "shadow_live_non_positive_pnl" not in row["blockers"]


def test_strategy_promotion_flags_live_loss_after_positive_shadow_economics_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-live-shadow-drift.sqlite")
    strategy_id = "profile_hedge_scalping_v1"
    now = "2026-06-05T19:30:00+00:00"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-promotion-drift-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version,
                run_type, run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES(?, ?, 'v1', 'shadow', 'live_replay', 'completed',
                   'pytest-positive-shadow', 'pytest-positive-shadow-validation',
                   '[]', '{"orders_allowed": false, "live_trading_authorized": false}',
                   10, 1, 1, 60, '{}', 'passed', 'reconciled', 0,
                   'not_required_for_read_only_shadow', ?, ?, ?, ?)
            """,
            (
                "pytest-positive-shadow-row",
                strategy_id,
                '{"result":{"economics":{"simulated_pnl_usd":2.25,"win_rate":1.0,"source":"pytest_shadow_fixture"}}}',
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version,
                run_type, run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES(?, ?, 'v1', 'supervised_live', 'supervised_live', 'completed',
                   'pytest-negative-live', 'pytest-negative-live-validation',
                   '[]', '{"orders_allowed": true, "live_trading_authorized": true}',
                   10, 1, 1, 60, '{}', 'passed', 'reconciled', 1,
                   'trusted_balance_reported', '{}', ?, ?, ?)
            """,
            ("pytest-negative-live-row", strategy_id, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO validation_budget_ledger(
                validation_budget_ledger_key, validation_run_id, strategy_or_component_id,
                started_at_utc, completed_at_utc, budget_cap_usd,
                notional_submitted_usd, notional_filled_usd, realized_pnl_usd,
                open_cost_usd, remaining_validation_budget_usd,
                cash_balance_before_usd, cash_balance_after_usd, cash_balance_status,
                hard_stop_triggered, stop_reason, lifecycle_audit_status,
                reconciliation_status, ledger_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, 'pytest-negative-live-validation', ?, ?, ?, 10,
               12, 12, -11.25, 0, 0, 206.27, 195.02,
                   'trusted_balance_reported', 0, NULL, 'passed',
                   'reconciled', '{}', ?, ?)
            """,
            ("pytest-negative-live-ledger", strategy_id, now, now, now, now),
        )
        summary = evaluate_and_persist_strategy_promotions(conn)

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "DEMOTED_TO_SHADOW"
    assert row["evidence"]["live_replay_economic_sample_count"] == 1
    assert row["evidence"]["live_replay_simulated_pnl_usd"] == 2.25
    assert row["evidence"]["live_replay_win_rate"] == 1.0
    assert row["evidence"]["supervised_live_realized_pnl_usd"] == -11.25
    assert row["evidence"]["live_vs_shadow_pnl_gap_usd"] == 13.5
    assert "live_loss_after_positive_shadow_requires_review" in row["blockers"]
    assert "live_shadow_actual_drift_exceeds_limit" in row["blockers"]


def test_strategy_promotion_requires_three_recent_shadow_samples_and_70_percent_win_rate_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-recent-shadow.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-recent-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-recent-shadow-under-sampled",
            strategy_id=strategy_id,
            pnl_usd=2.0,
            win_rate=1.0,
            sample_count=2,
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_READY"
    assert row["evidence"]["recent_shadow_live_economic_sample_count"] == 2
    assert row["evidence"]["recent_shadow_live_win_rate"] == 1.0
    assert "recent_shadow_live_sample_below_floor" in row["blockers"]
    assert row["orders_allowed"] is False
    assert row["live_trading_authorized"] is False


def test_strategy_promotion_blocks_recent_shadow_loss_streak_from_strategy_criteria_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-loss-streak.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-loss-streak-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        for index in range(4):
            _insert_shadow_economics_row(
                conn,
                row_key=f"pytest-loss-streak-row-{index}",
                strategy_id=strategy_id,
                pnl_usd=-0.01,
                win_rate=0.0,
                sample_count=1,
                event_key=f"loss-event-{index}",
            )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(
                min_promotion_ready_signals=0,
                min_shadow_live_pnl_usd=-10.0,
                min_shadow_live_win_rate=-0.1,
                min_recent_shadow_live_sample_count=4,
                min_recent_shadow_live_win_rate=-0.1,
                min_recent_shadow_live_pnl_usd=-10.0,
                max_recent_shadow_live_loss_streak=3,
            ),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_REVIEW"
    assert row["evidence"]["recent_shadow_live_loss_streak"] == 4
    assert "recent_shadow_live_loss_streak_exceeds_strategy_limit" in row["blockers"]


def test_strategy_promotion_ignores_later_blocked_shadow_row_for_mechanical_review_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-latest-blocked-shadow.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-latest-blocked-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-latest-blocked-shadow-completed-row",
            strategy_id=strategy_id,
            pnl_usd=2.0,
            win_rate=1.0,
            sample_count=2,
        )
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version,
                run_type, run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES(?, ?, 'v1', 'shadow', 'live_replay', 'blocked',
                   'pytest-latest-blocked-shadow-run', 'pytest-latest-blocked-shadow-validation',
                   '[]', '{"orders_allowed": false, "live_trading_authorized": false}',
                   20, 1, 1, 3600, '{}', 'missing_lifecycle_coverage', 'reconciliation_unavailable', 0,
                   'not_required_for_read_only_shadow', '{}', ?, ?, ?)
            """,
            (
                "pytest-latest-blocked-shadow-row",
                strategy_id,
                "2099-06-06T08:30:00+00:00",
                "2099-06-06T08:30:00+00:00",
                "2099-06-06T08:30:00+00:00",
            ),
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_READY"
    assert row["evidence"]["latest_run_status"] == "blocked"
    assert row["evidence"]["latest_lifecycle_audit_status"] == "passed"
    assert row["evidence"]["latest_reconciliation_status"] == "reconciled"
    assert "recent_shadow_live_sample_below_floor" in row["blockers"]
    assert "latest_lifecycle_not_passed" not in row["blockers"]
    assert "latest_reconciliation_not_reconciled" not in row["blockers"]


def test_strategy_promotion_routes_weak_recent_shadow_to_review_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-weak-shadow.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-weak-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-weak-shadow-row",
            strategy_id=strategy_id,
            pnl_usd=1.0,
            win_rate=0.69,
            sample_count=3,
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_REVIEW"
    assert row["evidence"]["recent_shadow_live_economic_sample_count"] == 3
    assert row["evidence"]["recent_shadow_live_win_rate"] == 0.69
    assert "recent_shadow_live_win_rate_below_70" in row["blockers"]


def test_strategy_promotion_requires_win_rate_strictly_above_70_percent_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-exact-70-shadow.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-exact-70-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-exact-70-shadow-row",
            strategy_id=strategy_id,
            pnl_usd=1.0,
            win_rate=0.7,
            sample_count=3,
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_REVIEW"
    assert row["evidence"]["recent_shadow_live_win_rate"] == 0.7
    assert "recent_shadow_live_win_rate_below_70" in row["blockers"]


def test_strategy_promotion_blocks_repeated_shadow_event_samples_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-repeat-shadow-event.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-repeat-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-repeat-shadow-row",
            strategy_id=strategy_id,
            pnl_usd=3.0,
            win_rate=0.75,
            sample_count=3,
            event_key="same-event-replayed-3-times",
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(
                min_promotion_ready_signals=0,
                min_recent_shadow_live_distinct_event_count=3,
            ),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_READY"
    assert row["evidence"]["recent_shadow_live_economic_sample_count"] == 3
    assert row["evidence"]["recent_shadow_live_distinct_event_count"] == 1
    assert "recent_shadow_live_distinct_event_below_floor" in row["blockers"]
    assert row["orders_allowed"] is False
    assert row["live_trading_authorized"] is False


def test_strategy_promotion_allows_live_candidate_only_after_strict_recent_shadow_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-strict-shadow-pass.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-strict-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-strict-shadow-row",
            strategy_id=strategy_id,
            pnl_usd=3.0,
            win_rate=0.75,
            sample_count=3,
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "LIVE_CANDIDATE"
    assert row["blockers"] == []
    assert row["evidence"]["recent_shadow_live_economic_sample_count"] == 3
    assert row["evidence"]["recent_shadow_live_win_rate"] == 0.75
    assert row["evidence"]["recent_shadow_live_simulated_pnl_usd"] == 3.0
    assert row["orders_allowed"] is False
    assert row["live_trading_authorized"] is False


def test_strategy_promotion_routes_tail_underdog_initial_live_candidate_to_shadow_review_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-tail-initial-live-block.sqlite")
    spec = SimpleNamespace(
        strategy_id="option_tail_reversal_hold_60s_v3",
        strategy_version="v3",
        strategy_family="hybrid",
        metadata={"version_notes": "tail reversal underdog hold lane"},
        risk_gates={},
        live_pulse_requirements={},
    )
    candidate_evidence = {
        "historical_replay_pass_count": 1,
        "latest_historical_replay_run_status": "completed",
        "live_replay_pass_count": 3,
        "latest_shadow_replay_run_status": "completed",
        "live_replay_economic_sample_count": 3,
        "live_replay_simulated_pnl_usd": 5.0,
        "live_replay_win_rate": 1.0,
        "recent_shadow_live_economic_sample_count": 3,
        "recent_shadow_live_distinct_event_count": 3,
        "recent_shadow_live_distinct_token_count": 3,
        "recent_shadow_live_simulated_pnl_usd": 3.0,
        "recent_shadow_live_win_rate": 1.0,
        "recent_shadow_live_loss_streak": 0,
        "latest_lifecycle_audit_status": "passed",
        "latest_reconciliation_status": "reconciled",
        "active_live_position_count": 0,
        "active_live_order_count": 0,
        "supervised_live_blocked_count": 0,
        "supervised_live_hard_stop_count": 0,
        "supervised_live_loss_streak": 0,
        "supervised_live_realized_pnl_usd": 0.0,
        "supervised_live_pass_count": 0,
        "supervised_live_win_rate": None,
        "live_vs_shadow_pnl_gap_usd": None,
        "calibrated_replay_blockers": [],
    }

    monkeypatch.setattr(promotion, "all_strategy_specs", lambda: (spec,))
    monkeypatch.setattr(promotion, "_strategy_evidence", lambda *_args, **_kwargs: candidate_evidence)
    monkeypatch.setattr(
        promotion,
        "validation_status",
        lambda _conn: {
            "signal_count": 1,
            "by_promotion_state": {"PROMOTION_READY": 1},
            "signals": [
                {
                    "signal_id": "pytest_promotion_ready_signal",
                    "promotion_state": "PROMOTION_READY",
                    "strict_review_reasons": [],
                }
            ],
        },
    )

    with connect(db_path) as conn:
        summary = evaluate_and_persist_strategy_promotions(conn)

    row = summary["strategies"][0]
    assert row["promotion_state"] == "SHADOW_REVIEW"
    assert "initial_live_validation_strategy_style_excluded" in row["blockers"]
    assert "tail/underdog style is excluded" in row["next_action"]


def test_strategy_promotion_routes_blocked_historical_replay_to_review_blocked_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-blocked-historical.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version,
                run_type, run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES('pytest-blocked-historical-row', ?, 'v1',
                   'backtest', 'historical_replay', 'blocked',
                   'pytest-blocked-historical-run', 'pytest-blocked-historical-validation',
                   '[]', '{"orders_allowed": false, "live_trading_authorized": false}',
                   20, 1, 1, 3600, '{}', 'missing_lifecycle_coverage',
                   'reconciliation_unavailable', 0, 'not_required_for_backtest',
                   '{}', '2099-06-06T08:30:00+00:00',
                   '2099-06-06T08:30:00+00:00',
                   '2099-06-06T08:30:00+00:00')
            """,
            (strategy_id,),
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "REVIEW_BLOCKED"
    assert row["evidence"]["latest_historical_replay_run_status"] == "blocked"
    assert "historical_strategy_replay_blocked" in row["blockers"]


def test_strategy_promotion_routes_blocked_shadow_replay_to_shadow_review_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-blocked-shadow.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-blocked-shadow-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version,
                run_type, run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES('pytest-blocked-shadow-row', ?, 'v1',
                   'shadow', 'live_replay', 'blocked',
                   'pytest-blocked-shadow-run', 'pytest-blocked-shadow-validation',
                   '[]', '{"orders_allowed": false, "live_trading_authorized": false}',
                   20, 1, 1, 3600, '{}', 'missing_lifecycle_coverage',
                   'reconciliation_unavailable', 0, 'not_required_for_read_only_shadow',
                   '{}', '2099-06-06T08:30:00+00:00',
                   '2099-06-06T08:30:00+00:00',
                   '2099-06-06T08:30:00+00:00')
            """,
            (strategy_id,),
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_REVIEW"
    assert row["evidence"]["historical_replay_pass_count"] == 1
    assert row["evidence"]["latest_shadow_replay_run_status"] == "blocked"
    assert "shadow_live_replay_blocked" in row["blockers"]


def test_strategy_promotion_blocks_scoring_ready_negative_calibrated_replay_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-promotion-calibrated-replay.sqlite")
    strategy_id = "profile_hedge_scalping_v1"

    run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-calibrated-backtest",
            strategy_ids=(strategy_id,),
            max_trades_per_strategy=1,
        )
    )
    with connect(db_path) as conn:
        _insert_shadow_economics_row(
            conn,
            row_key="pytest-calibrated-positive-shadow",
            strategy_id=strategy_id,
            pnl_usd=3.0,
            win_rate=1.0,
            sample_count=3,
        )
        monkeypatch.setattr(
            promotion,
            "calibrated_replay_evidence_for_strategy",
            lambda requested_strategy_id: {
                "schema_version": "crypto_options_calibrated_replay_strategy_evidence_v1",
                "decisions": 3,
                "simulated_filled": 3,
                "simulated_pnl_usd": -2.25,
                "actual_resolved_pnl_usd": -2.25,
                "data_quality_score": 0.9,
                "scoring_ready": True,
                "blockers": ["calibrated_replay_non_positive_pnl"],
            }
            if requested_strategy_id == strategy_id
            else None,
        )
        summary = evaluate_and_persist_strategy_promotions(
            conn,
            policy=StrategyPromotionPolicy(min_promotion_ready_signals=0),
        )

    row = {item["strategy_id"]: item for item in summary["strategies"]}[strategy_id]
    assert row["promotion_state"] == "SHADOW_REVIEW"
    assert row["evidence"]["calibrated_replay_scoring_ready"] is True
    assert row["evidence"]["calibrated_replay_simulated_pnl_usd"] == -2.25
    assert "calibrated_replay_non_positive_pnl" in row["blockers"]


def _insert_shadow_economics_row(
    conn,
    *,
    row_key: str,
    strategy_id: str,
    pnl_usd: float,
    win_rate: float,
    sample_count: int,
    source: str = "pytest_recent_shadow_fixture",
    event_key: str | None = None,
) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    evidence = {
        "result": {
            "economics": {
                "simulated_pnl_usd": pnl_usd,
                "win_rate": win_rate,
                "sample_count": sample_count,
                "source": source,
                "event_key": event_key,
            }
        }
    }
    conn.execute(
        """
        INSERT INTO strategy_validation_runs(
            strategy_validation_run_key, strategy_id, strategy_version,
            run_type, run_phase, run_status, run_id, validation_run_id,
            signal_dependencies_json, scoped_live_flags_json,
            max_notional_usd, max_events, max_trades, max_wall_time_seconds,
            stop_rules_json, lifecycle_audit_status, reconciliation_status,
            budget_ledger_required, cash_balance_status, evidence_json,
            started_at_utc, completed_at_utc, inserted_at_utc
        )
        VALUES(?, ?, 'v1', 'shadow', 'live_replay', 'completed',
               ?, ?, '[]',
               '{"orders_allowed": false, "live_trading_authorized": false}',
               20, ?, ?, 3600, '{}', 'passed', 'reconciled', 0,
               'not_required_for_read_only_shadow', ?, ?, ?, ?)
        """,
        (
            row_key,
            strategy_id,
            f"{row_key}-run",
            f"{row_key}-validation",
            sample_count,
            sample_count,
            json.dumps(evidence),
            now,
            now,
            now,
        ),
    )
