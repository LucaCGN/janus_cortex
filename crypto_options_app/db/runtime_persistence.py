from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies.registry import get_strategy
from crypto_options_app.workers.runtime_adapter import RuntimeStrategyResult, RuntimeValidationReport


def persist_runtime_validation_report(report: RuntimeValidationReport, db_path: str | Path | None = None) -> dict[str, int]:
    path = initialize_schema(db_path)
    counts = {
        "strategy_validation_runs": 0,
        "validation_budget_ledger": 0,
        "strategy_candidates": 0,
        "execution_intents": 0,
        "orders": 0,
        "fills": 0,
        "positions": 0,
        "exit_plans": 0,
        "exit_orders": 0,
        "run_reports": 0,
    }
    with connect(path) as conn:
        running_validation_budget_spent_usd = max(0.0, float(report.validation_budget_spent_usd))
        for result in report.results:
            _persist_strategy_validation_run(
                conn,
                report,
                result,
                counts,
                validation_budget_spent_usd=running_validation_budget_spent_usd,
            )
            running_validation_budget_spent_usd += _submitted_notional_usd(result)
            _persist_result(conn, report, result, counts)
        _persist_run_report(conn, report, counts)
    return counts


def _persist_strategy_validation_run(
    conn: sqlite3.Connection,
    report: RuntimeValidationReport,
    result: RuntimeStrategyResult,
    counts: dict[str, int],
    *,
    validation_budget_spent_usd: float,
) -> None:
    now = datetime.now(UTC).isoformat()
    validation_run_id = f"validation-run:{report.run_id}:{result.strategy_id}"
    lifecycle_audit_status = "passed" if result.lifecycle_covered else "missing_lifecycle_coverage"
    reconciliation_status = result.reconciliation_status or "reconciliation_unavailable"
    submitted_notional = _submitted_notional_usd(result)
    filled_notional = _filled_notional_usd(result)
    run_status = "completed" if not result.blockers else "blocked"
    budget_ledger_required = 1 if report.mode == "supervised_live" else 0
    budget_cap_usd = max(0.0, float(report.validation_budget_cap_usd))
    cash_balance_status = str(report.cash_balance_status or "cash_balance_unavailable")
    stop_reason = ",".join(result.blockers) if result.blockers else None
    hard_stop_triggered = int(
        any(
            blocker in {"missing_lifecycle_coverage", "reconciliation_mismatch", "duplicate_cadence"}
            for blocker in result.blockers
        )
    )
    remaining_validation_budget_usd = max(0.0, budget_cap_usd - validation_budget_spent_usd - submitted_notional)
    cash_balance_before_usd = report.cash_balance_before_usd
    cash_balance_after_usd = (
        None
        if cash_balance_before_usd is None
        else max(0.0, float(cash_balance_before_usd) - submitted_notional)
    )
    if cash_balance_before_usd is not None:
        if float(cash_balance_before_usd) <= float(report.cash_balance_hard_stop_usd) + 1e-9:
            hard_stop_triggered = 1
            stop_reason = stop_reason or "cash_balance_hard_stop_breach"
        elif cash_balance_after_usd is not None and cash_balance_after_usd < float(report.cash_balance_hard_stop_usd) - 1e-9:
            hard_stop_triggered = 1
            stop_reason = stop_reason or "projected_cash_balance_hard_stop_breach"
    signal_dependencies = tuple(str(source) for source in result.attribution.get("sources", ()) if source)
    scoped_live_flags = {
        "mode": report.mode,
        "orders_allowed": result.orders_allowed,
        "live_trading_authorized": result.live_trading_authorized,
        "manual_orders_avoided": report.manual_orders_avoided,
    }
    evidence = {
        "run_report_summary": report.run_report.summary,
        "result": _result_payload(result),
    }
    strategy_version = get_strategy(result.strategy_id).strategy_version
    conn.execute(
        """
        INSERT INTO strategy_validation_runs(
            strategy_validation_run_key, strategy_id, strategy_version, run_type,
            run_phase, run_status, run_id, validation_run_id,
            signal_dependencies_json, scoped_live_flags_json,
            max_notional_usd, max_events, max_trades, max_wall_time_seconds,
            stop_rules_json, lifecycle_audit_status, reconciliation_status,
            budget_ledger_required, cash_balance_status, evidence_json,
            started_at_utc, completed_at_utc, inserted_at_utc
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_validation_run_key) DO UPDATE SET
            run_status=excluded.run_status,
            validation_run_id=excluded.validation_run_id,
            signal_dependencies_json=excluded.signal_dependencies_json,
            scoped_live_flags_json=excluded.scoped_live_flags_json,
            max_notional_usd=excluded.max_notional_usd,
            max_events=excluded.max_events,
            max_trades=excluded.max_trades,
            max_wall_time_seconds=excluded.max_wall_time_seconds,
            stop_rules_json=excluded.stop_rules_json,
            lifecycle_audit_status=excluded.lifecycle_audit_status,
            reconciliation_status=excluded.reconciliation_status,
            budget_ledger_required=excluded.budget_ledger_required,
            cash_balance_status=excluded.cash_balance_status,
            evidence_json=excluded.evidence_json,
            completed_at_utc=excluded.completed_at_utc
        """,
        (
            f"strategy-validation-run:{report.run_id}:{result.strategy_id}",
            result.strategy_id,
            strategy_version,
            report.mode,
            _run_phase_for_mode(report.mode),
            run_status,
            report.run_id,
            validation_run_id,
            json.dumps(list(signal_dependencies), sort_keys=True),
            json.dumps(scoped_live_flags, sort_keys=True),
            submitted_notional or filled_notional,
            report.run_report.summary.get("max_events"),
            report.run_report.summary.get("max_trades_per_strategy"),
            report.run_report.summary.get("max_wall_time_seconds"),
            json.dumps({"stop_gates": list(report.run_report.stop_gates)}, sort_keys=True),
            lifecycle_audit_status,
            reconciliation_status,
            budget_ledger_required,
            cash_balance_status,
            json.dumps(evidence, sort_keys=True, default=str),
            report.generated_at_utc.isoformat(),
            report.generated_at_utc.isoformat(),
            now,
        ),
    )
    counts["strategy_validation_runs"] += 1

    if budget_ledger_required:
        conn.execute(
            """
            INSERT INTO validation_budget_ledger(
                validation_budget_ledger_key, validation_run_id, strategy_or_component_id,
                started_at_utc, completed_at_utc, budget_cap_usd, notional_submitted_usd,
                notional_filled_usd, realized_pnl_usd, open_cost_usd,
                remaining_validation_budget_usd, cash_balance_before_usd,
                cash_balance_after_usd, cash_balance_status, hard_stop_triggered,
                stop_reason, lifecycle_audit_status, reconciliation_status,
                ledger_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(validation_budget_ledger_key) DO UPDATE SET
                completed_at_utc=excluded.completed_at_utc,
                notional_submitted_usd=excluded.notional_submitted_usd,
                notional_filled_usd=excluded.notional_filled_usd,
                realized_pnl_usd=excluded.realized_pnl_usd,
                open_cost_usd=excluded.open_cost_usd,
                remaining_validation_budget_usd=excluded.remaining_validation_budget_usd,
                cash_balance_before_usd=excluded.cash_balance_before_usd,
                cash_balance_after_usd=excluded.cash_balance_after_usd,
                cash_balance_status=excluded.cash_balance_status,
                hard_stop_triggered=excluded.hard_stop_triggered,
                stop_reason=excluded.stop_reason,
                lifecycle_audit_status=excluded.lifecycle_audit_status,
                reconciliation_status=excluded.reconciliation_status,
                ledger_json=excluded.ledger_json,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                f"validation-budget-ledger:{validation_run_id}",
                validation_run_id,
                result.strategy_id,
                report.generated_at_utc.isoformat(),
                report.generated_at_utc.isoformat(),
                budget_cap_usd,
                submitted_notional,
                filled_notional,
                0.0,
                filled_notional,
                remaining_validation_budget_usd,
                cash_balance_before_usd,
                cash_balance_after_usd,
                cash_balance_status,
                hard_stop_triggered,
                stop_reason,
                lifecycle_audit_status,
                reconciliation_status,
                json.dumps(
                    {
                        "run_id": report.run_id,
                        "mode": report.mode,
                        "manual_orders_avoided": report.manual_orders_avoided,
                        "stop_gates": list(report.run_report.stop_gates),
                    },
                    sort_keys=True,
                ),
                now,
                now,
            ),
        )
        counts["validation_budget_ledger"] += 1


def _run_phase_for_mode(mode: str) -> str:
    if mode == "dry_run":
        return "historical_replay"
    if mode == "shadow":
        return "live_replay"
    if mode == "supervised_live":
        return "supervised_live"
    return "unknown_runtime_phase"


def _persist_result(
    conn: sqlite3.Connection,
    report: RuntimeValidationReport,
    result: RuntimeStrategyResult,
    counts: dict[str, int],
) -> None:
    now = datetime.now(UTC).isoformat()
    candidate_key = result.candidate_key or f"candidate:{report.run_id}:{result.strategy_id}:blocked"
    event_token_key = result.event_token_key
    conn.execute(
        """
        INSERT INTO strategy_candidates(
            candidate_key, strategy_id, strategy_version, event_key, event_token_key,
            candidate_side, decision_at_utc, status, blocker_json, candidate_json, inserted_at_utc
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_key) DO UPDATE SET
            status=excluded.status,
            blocker_json=excluded.blocker_json,
            candidate_json=excluded.candidate_json
        """,
        (
            candidate_key,
            result.strategy_id,
            "v1",
            result.event_key,
            event_token_key,
            "BUY",
            report.generated_at_utc.isoformat(),
            _candidate_status(result),
            json.dumps(list(result.blockers), sort_keys=True),
            json.dumps(_result_payload(result), sort_keys=True, default=str),
            now,
        ),
    )
    counts["strategy_candidates"] += 1

    if result.intent_key:
        conn.execute(
            """
            INSERT INTO execution_intents(
                intent_key, candidate_key, strategy_id, event_token_key, intent_type,
                order_type, side, status, intent_json, created_at_utc
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(intent_key) DO UPDATE SET
                status=excluded.status,
                intent_json=excluded.intent_json
            """,
            (
                result.intent_key,
                candidate_key,
                result.strategy_id,
                event_token_key,
                "entry",
                str(result.attribution.get("order_type") or "limit_buy"),
                "BUY",
                "created",
                json.dumps(_result_payload(result), sort_keys=True, default=str),
                report.generated_at_utc.isoformat(),
            ),
        )
        counts["execution_intents"] += 1

    if result.order_key:
        conn.execute(
            """
            INSERT INTO orders(order_key, intent_key, exchange_order_id, status, order_json, submitted_at_utc, updated_at_utc)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_key) DO UPDATE SET
                exchange_order_id=excluded.exchange_order_id,
                status=excluded.status,
                order_json=excluded.order_json,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                result.order_key,
                result.intent_key,
                result.exchange_order_id,
                result.order_status or ("blocked" if result.blockers else "unknown"),
                json.dumps(_result_payload(result), sort_keys=True, default=str),
                report.generated_at_utc.isoformat() if result.live_submission_attempted else None,
                now,
            ),
        )
        counts["orders"] += 1

    if result.order_key and result.filled_shares and result.fill_price is not None:
        fill_key = f"fill:{result.order_key}:{result.filled_shares}:{result.fill_price}"
        conn.execute(
            """
            INSERT INTO fills(fill_key, order_key, event_token_key, filled_size, filled_price, filled_at_utc, source_json, inserted_at_utc)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fill_key) DO UPDATE SET
                source_json=excluded.source_json
            """,
            (
                fill_key,
                result.order_key,
                event_token_key,
                float(result.filled_shares),
                float(result.fill_price),
                report.generated_at_utc.isoformat(),
                json.dumps(_result_payload(result), sort_keys=True, default=str),
                now,
            ),
        )
        counts["fills"] += 1

    if result.position_key and result.filled_shares and result.fill_price is not None:
        conn.execute(
            """
            INSERT INTO positions(position_key, strategy_id, event_token_key, shares, cost_basis_usd, status, opened_at_utc, updated_at_utc)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(position_key) DO UPDATE SET
                shares=excluded.shares,
                cost_basis_usd=excluded.cost_basis_usd,
                status=excluded.status,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                result.position_key,
                result.strategy_id,
                event_token_key or "",
                float(result.filled_shares),
                round(float(result.filled_shares) * float(result.fill_price), 8),
                "open",
                report.generated_at_utc.isoformat(),
                now,
            ),
        )
        counts["positions"] += 1

    if result.position_key and result.lifecycle_covered:
        exit_plan_key = f"exit-plan:{result.position_key}"
        conn.execute(
            """
            INSERT INTO exit_plans(exit_plan_key, position_key, coverage_type, status, plan_json, created_at_utc, updated_at_utc)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exit_plan_key) DO UPDATE SET
                status=excluded.status,
                plan_json=excluded.plan_json,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                exit_plan_key,
                result.position_key,
                str(result.attribution.get("coverage_type") or "managed_exit_or_settlement"),
                "active",
                json.dumps(_result_payload(result), sort_keys=True, default=str),
                report.generated_at_utc.isoformat(),
                now,
            ),
        )
        counts["exit_plans"] += 1
        managed_plans = result.attribution.get("managed_runtime_plans")
        if isinstance(managed_plans, list):
            for index, plan in enumerate(managed_plans):
                if not isinstance(plan, dict):
                    continue
                intent_type = str(plan.get("intent_type") or f"managed_{index}")
                exit_order_key = f"exit-order:{exit_plan_key}:{intent_type}:{index}"
                conn.execute(
                    """
                    INSERT INTO exit_orders(exit_order_key, exit_plan_key, order_key, status, source_json, inserted_at_utc)
                    VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(exit_order_key) DO UPDATE SET
                        status=excluded.status,
                        source_json=excluded.source_json
                    """,
                    (
                        exit_order_key,
                        exit_plan_key,
                        None,
                        "planned",
                        json.dumps(plan, sort_keys=True, default=str),
                        now,
                    ),
                )
                counts["exit_orders"] += 1


def _persist_run_report(conn: sqlite3.Connection, report: RuntimeValidationReport, counts: dict[str, int]) -> None:
    key = f"run-report:{report.run_id}:{report.generated_at_utc.isoformat()}"
    payload = {
        "run_id": report.run_id,
        "mode": report.mode,
        "generated_at_utc": report.generated_at_utc.isoformat(),
        "passed_count": report.passed_count,
        "blocked_count": report.blocked_count,
        "manual_orders_avoided": report.manual_orders_avoided,
        "results": [_result_payload(result) for result in report.results],
    }
    conn.execute(
        """
        INSERT INTO run_reports(run_report_key, run_id, generated_at_utc, report_json)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(run_report_key) DO UPDATE SET report_json=excluded.report_json
        """,
        (key, report.run_id, report.generated_at_utc.isoformat(), json.dumps(payload, sort_keys=True, default=str)),
    )
    counts["run_reports"] += 1


def _candidate_status(result: RuntimeStrategyResult) -> str:
    if result.blockers:
        return "blocked"
    if result.status in {"simulated_executed", "live_structural_executed"}:
        return "executed"
    if result.status == "structural_passed":
        return "ready"
    return result.status


def _result_payload(result: RuntimeStrategyResult) -> dict[str, Any]:
    economics = _result_economics(result)
    return {
        "strategy_id": result.strategy_id,
        "status": result.status,
        "blockers": list(result.blockers),
        "event_key": result.event_key,
        "event_token_key": result.event_token_key,
        "token_id": result.token_id,
        "event_slug": result.event_slug,
        "outcome": result.outcome,
        "candidate_key": result.candidate_key,
        "intent_key": result.intent_key,
        "order_key": result.order_key,
        "exchange_order_id": result.exchange_order_id,
        "order_status": result.order_status,
        "filled_shares": result.filled_shares,
        "fill_price": result.fill_price,
        "remote_filled_shares": result.remote_filled_shares,
        "position_key": result.position_key,
        "lifecycle_covered": result.lifecycle_covered,
        "reconciliation_status": result.reconciliation_status,
        "reconciliation_evidence": result.reconciliation_evidence,
        "orders_allowed": result.orders_allowed,
        "live_trading_authorized": result.live_trading_authorized,
        "live_submission_attempted": result.live_submission_attempted,
        "attribution": result.attribution,
        "economics": economics,
        "shadow_economics": economics,
    }


def _submitted_notional_usd(result: RuntimeStrategyResult) -> float:
    if result.filled_shares and result.fill_price is not None:
        return round(float(result.filled_shares) * float(result.fill_price), 8)
    return 0.0


def _filled_notional_usd(result: RuntimeStrategyResult) -> float:
    if result.order_status == "filled" and result.filled_shares and result.fill_price is not None:
        return round(float(result.filled_shares) * float(result.fill_price), 8)
    return 0.0


def _result_economics(result: RuntimeStrategyResult) -> dict[str, Any]:
    shares = _optional_float(result.filled_shares)
    fill_price = _optional_float(result.fill_price)
    if shares is None or shares <= 0.0 or fill_price is None:
        return {
            "source": "runtime_validation_no_fill",
            "sample_count": 0,
            "pnl_usd": 0.0,
            "simulated_pnl_usd": 0.0,
            "win_rate": None,
            "cost_basis_usd": 0.0,
            "mark_price": None,
            "blockers": ["no_filled_shadow_position"],
        }
    signal_context = result.attribution.get("signal_context")
    if not isinstance(signal_context, dict):
        signal_context = {}
    strategy_decision = result.attribution.get("strategy_decision")
    if _is_paired_seed_decision(strategy_decision):
        paired_economics = _paired_seed_result_economics(
            result,
            signal_context=signal_context,
            strategy_decision=strategy_decision,
            shares=shares,
            fill_price=fill_price,
        )
        if paired_economics is not None:
            return paired_economics
    liquidation_mark_price = _first_float(
        signal_context.get("mark_price"),
        signal_context.get("current_bid"),
        signal_context.get("best_bid"),
        signal_context.get("mid_price"),
    )
    mark_source = "signal_context_mark_or_bid"
    if liquidation_mark_price is None:
        liquidation_mark_price = fill_price
        mark_source = "entry_price_fallback"
    cost_basis = round(shares * fill_price, 8)
    liquidation_mark_value = round(shares * float(liquidation_mark_price), 8)
    liquidation_pnl = round(liquidation_mark_value - cost_basis, 8)
    spread_drag_usd = round(max(0.0, fill_price - float(liquidation_mark_price)) * shares, 8)
    forward_mark_price = _first_float(
        signal_context.get("forward_mark_price"),
        signal_context.get("forward_exit_price"),
        signal_context.get("simulated_exit_price"),
        signal_context.get("settlement_price"),
        signal_context.get("exit_price"),
    )
    if forward_mark_price is None:
        return {
            "source": "runtime_shadow_entry_fill_needs_forward_mark",
            "mark_source": mark_source,
            "sample_count": 0,
            "pnl_usd": 0.0,
            "simulated_pnl_usd": 0.0,
            "win_rate": None,
            "win_count": 0,
            "loss_count": 0,
            "cost_basis_usd": cost_basis,
            "mark_price": None,
            "fill_price": round(fill_price, 8),
            "filled_shares": round(shares, 8),
            "promotion_economics_ready": False,
            "blockers": ["forward_price_path_required_for_promotion_pnl"],
            "liquidation_mark_price": round(float(liquidation_mark_price), 8),
            "liquidation_mark_value_usd": liquidation_mark_value,
            "liquidation_pnl_usd": liquidation_pnl,
            "spread_drag_usd": spread_drag_usd,
        }
    mark_value = round(shares * float(forward_mark_price), 8)
    pnl = round(mark_value - cost_basis, 8)
    economics_blockers: list[str] = []
    min_liquidation_pnl = None
    max_spread_drag = None
    if isinstance(strategy_decision, dict):
        min_liquidation_pnl = _optional_float(strategy_decision.get("shadow_economics_min_liquidation_pnl_usd"))
        if bool(strategy_decision.get("shadow_economics_require_liquidation_non_negative")):
            min_liquidation_pnl = max(0.0, min_liquidation_pnl or 0.0)
        max_spread_drag = _optional_float(strategy_decision.get("shadow_economics_max_spread_drag_usd"))
    if min_liquidation_pnl is not None and liquidation_pnl < min_liquidation_pnl:
        economics_blockers.append("liquidation_pnl_below_shadow_gate")
    if max_spread_drag is not None and spread_drag_usd > max_spread_drag:
        economics_blockers.append("spread_drag_above_shadow_gate")
    return {
        "source": "runtime_shadow_forward_mark",
        "mark_source": mark_source,
        "sample_count": 1,
        "pnl_usd": pnl,
        "simulated_pnl_usd": pnl,
        "win_rate": 1.0 if pnl > 0 else 0.0 if pnl < 0 else 0.5,
        "win_count": 1 if pnl > 0 else 0,
        "loss_count": 1 if pnl < 0 else 0,
        "cost_basis_usd": cost_basis,
        "mark_value_usd": mark_value,
        "mark_price": round(float(forward_mark_price), 8),
        "fill_price": round(fill_price, 8),
        "filled_shares": round(shares, 8),
        "promotion_economics_ready": not economics_blockers,
        "blockers": economics_blockers,
        "liquidation_mark_price": round(float(liquidation_mark_price), 8),
        "liquidation_mark_value_usd": liquidation_mark_value,
        "liquidation_pnl_usd": liquidation_pnl,
        "spread_drag_usd": spread_drag_usd,
    }


def _is_paired_seed_decision(strategy_decision: object) -> bool:
    if not isinstance(strategy_decision, dict):
        return False
    seed_mode = str(strategy_decision.get("hedge_floor_seed_allocation_mode") or "").strip().lower()
    return seed_mode == "equal_shares" or isinstance(strategy_decision.get("paired_seed_entry"), dict)


def _paired_seed_result_economics(
    result: RuntimeStrategyResult,
    *,
    signal_context: dict[str, Any],
    strategy_decision: object,
    shares: float,
    fill_price: float,
) -> dict[str, Any] | None:
    if not isinstance(strategy_decision, dict):
        return None
    paired_entry = strategy_decision.get("paired_seed_entry")
    if not isinstance(paired_entry, dict):
        paired_entry = {}
    outcome_is_up = str(result.outcome or "").strip().lower() == "up"
    up_entry_ask = _first_float(signal_context.get("paired_entry_up_ask"), paired_entry.get("up_price"))
    down_entry_ask = _first_float(signal_context.get("paired_entry_down_ask"), paired_entry.get("down_price"))
    if up_entry_ask is None and outcome_is_up:
        up_entry_ask = fill_price
    if down_entry_ask is None and not outcome_is_up:
        down_entry_ask = fill_price
    opposite_ask = _first_float(signal_context.get("paired_opposite_ask"), signal_context.get("paired_down_ask"))
    if down_entry_ask is None and outcome_is_up:
        down_entry_ask = opposite_ask
    if up_entry_ask is None and not outcome_is_up:
        up_entry_ask = opposite_ask
    pair_sum = _first_float(signal_context.get("paired_entry_pair_sum"), paired_entry.get("pair_sum"))
    if pair_sum is None and up_entry_ask is not None and down_entry_ask is not None:
        pair_sum = up_entry_ask + down_entry_ask
    if pair_sum is None or pair_sum <= 0.0:
        return None
    equal_shares = _first_float(paired_entry.get("equal_shares"), signal_context.get("paired_equal_shares"))
    if equal_shares is None or equal_shares <= 0.0:
        equal_shares = shares
    pair_cost = round(equal_shares * pair_sum, 8)
    guaranteed_floor_value = round(equal_shares, 8)
    guaranteed_floor_pnl = round(guaranteed_floor_value - pair_cost, 8)
    forward_up_bid = _first_float(signal_context.get("paired_forward_up_bid"))
    forward_down_bid = _first_float(signal_context.get("paired_forward_down_bid"))
    paired_forward_mark_available = forward_up_bid is not None and forward_down_bid is not None
    paired_forward_mark_value = None
    paired_forward_mark_pnl = None
    if paired_forward_mark_available:
        paired_forward_mark_value = round(equal_shares * (float(forward_up_bid) + float(forward_down_bid)), 8)
        paired_forward_mark_pnl = round(paired_forward_mark_value - pair_cost, 8)
    liquidation_mark_price = _first_float(
        signal_context.get("mark_price"),
        signal_context.get("current_bid"),
        signal_context.get("best_bid"),
        signal_context.get("mid_price"),
        fill_price,
    )
    liquidation_mark_value = round(shares * float(liquidation_mark_price), 8)
    one_leg_cost_basis = round(shares * fill_price, 8)
    liquidation_pnl = round(liquidation_mark_value - one_leg_cost_basis, 8)
    if bool(strategy_decision.get("paired_seed_scalp_simulation_required")):
        return _paired_seed_scalp_result_economics(
            signal_context=signal_context,
            strategy_decision=strategy_decision,
            equal_shares=equal_shares,
            pair_cost=pair_cost,
            guaranteed_floor_pnl=guaranteed_floor_pnl,
            up_entry_ask=up_entry_ask,
            down_entry_ask=down_entry_ask,
            paired_forward_mark_available=paired_forward_mark_available,
            forward_up_bid=forward_up_bid,
            forward_down_bid=forward_down_bid,
            liquidation_mark_price=liquidation_mark_price,
            liquidation_mark_value=liquidation_mark_value,
            liquidation_pnl=liquidation_pnl,
            one_leg_cost_basis=one_leg_cost_basis,
            shares=shares,
            fill_price=fill_price,
        )
    blockers: list[str] = []
    if not bool(signal_context.get("paired_market_snapshot_ready")):
        blockers.append("paired_market_snapshot_required_for_promotion")
    if not paired_forward_mark_available:
        blockers.append("paired_forward_pair_mark_required_for_promotion")
    if guaranteed_floor_pnl <= 0.0:
        blockers.append("paired_seed_floor_not_positive")
    promotion_economics_ready = not blockers
    return {
        "source": (
            "runtime_shadow_paired_seed_floor_with_forward_pair_mark"
            if paired_forward_mark_available
            else "runtime_shadow_paired_seed_floor_needs_forward_pair_mark"
        ),
        "sample_count": 1,
        "pnl_usd": guaranteed_floor_pnl,
        "simulated_pnl_usd": guaranteed_floor_pnl,
        "win_rate": 1.0 if guaranteed_floor_pnl > 0.0 else 0.0 if guaranteed_floor_pnl < 0.0 else 0.5,
        "win_count": 1 if guaranteed_floor_pnl > 0.0 else 0,
        "loss_count": 1 if guaranteed_floor_pnl < 0.0 else 0,
        "promotion_economics_ready": promotion_economics_ready,
        "blockers": blockers,
        "cost_basis_usd": pair_cost,
        "paired_entry_up_ask": None if up_entry_ask is None else round(float(up_entry_ask), 8),
        "paired_entry_down_ask": None if down_entry_ask is None else round(float(down_entry_ask), 8),
        "paired_entry_pair_sum": round(float(pair_sum), 8),
        "paired_equal_shares": round(float(equal_shares), 8),
        "guaranteed_floor_value_usd": guaranteed_floor_value,
        "guaranteed_floor_pnl_usd": guaranteed_floor_pnl,
        "paired_forward_mark_available": paired_forward_mark_available,
        "paired_forward_up_bid": None if forward_up_bid is None else round(float(forward_up_bid), 8),
        "paired_forward_down_bid": None if forward_down_bid is None else round(float(forward_down_bid), 8),
        "paired_forward_mark_value_usd": paired_forward_mark_value,
        "paired_forward_mark_pnl_usd": paired_forward_mark_pnl,
        "fill_price": round(fill_price, 8),
        "filled_shares": round(shares, 8),
        "liquidation_mark_price": round(float(liquidation_mark_price), 8),
        "liquidation_mark_value_usd": liquidation_mark_value,
        "liquidation_pnl_usd": liquidation_pnl,
        "spread_drag_usd": round(max(0.0, fill_price - float(liquidation_mark_price)) * shares, 8),
    }


def _paired_seed_scalp_result_economics(
    *,
    signal_context: dict[str, Any],
    strategy_decision: dict[str, Any],
    equal_shares: float,
    pair_cost: float,
    guaranteed_floor_pnl: float,
    up_entry_ask: float | None,
    down_entry_ask: float | None,
    paired_forward_mark_available: bool,
    forward_up_bid: float | None,
    forward_down_bid: float | None,
    liquidation_mark_price: float,
    liquidation_mark_value: float,
    liquidation_pnl: float,
    one_leg_cost_basis: float,
    shares: float,
    fill_price: float,
) -> dict[str, Any]:
    snapshots = signal_context.get("paired_path_snapshots")
    if not isinstance(snapshots, list):
        snapshots = []
    buy_drop = max(0.0, _first_float(strategy_decision.get("paired_seed_scalp_buy_drop")) or 0.03)
    scalp_target = max(0.0, _first_float(strategy_decision.get("paired_seed_scalp_target")) or 0.03)
    scalp_notional = max(0.01, _first_float(strategy_decision.get("paired_seed_scalp_notional_usd")) or 0.25)
    max_open_per_side = max(1, int(_first_float(strategy_decision.get("paired_seed_scalp_max_open_per_side")) or 1))
    min_path_snapshots = max(2, int(_first_float(strategy_decision.get("paired_seed_scalp_min_path_snapshots")) or 2))
    entry_window_fraction = _first_float(strategy_decision.get("paired_seed_scalp_entry_window_fraction"))
    if entry_window_fraction is None:
        entry_window_fraction = 1.0
    entry_window_fraction = max(0.0, min(1.0, float(entry_window_fraction)))
    buy_cutoff_index = max(1, int(len(snapshots) * entry_window_fraction)) if snapshots else 0
    realized_cash = -float(pair_cost)
    side_shares = {"up": float(equal_shares), "down": float(equal_shares)}
    entry_ask = {"up": up_entry_ask, "down": down_entry_ask}
    open_positions: dict[str, list[dict[str, float]]] = {"up": [], "down": []}
    completed_cycles: list[dict[str, Any]] = []
    min_floor = min(realized_cash + side_shares["up"], realized_cash + side_shares["down"])

    for snapshot_index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            continue
        for side in ("up", "down"):
            bid = _first_float(snapshot.get(f"{side}_bid"))
            if bid is None:
                continue
            retained: list[dict[str, float]] = []
            for position in open_positions[side]:
                target_price = float(position["price"]) + scalp_target
                if bid >= target_price:
                    sold_shares = float(position["shares"])
                    realized_cash += sold_shares * bid
                    side_shares[side] -= sold_shares
                    completed_cycles.append(
                        {
                            "side": side,
                            "buy_price": round(float(position["price"]), 8),
                            "sell_price": round(float(bid), 8),
                            "shares": round(sold_shares, 8),
                            "profit_usd": round(sold_shares * (bid - float(position["price"])), 8),
                            "timestamp_utc": snapshot.get("timestamp_utc"),
                        }
                    )
                else:
                    retained.append(position)
            open_positions[side] = retained
        can_open_new_scalp = snapshot_index < buy_cutoff_index
        for side in ("up", "down"):
            ask = _first_float(snapshot.get(f"{side}_ask"))
            side_entry = entry_ask.get(side)
            if ask is None or side_entry is None:
                continue
            if not can_open_new_scalp:
                continue
            if len(open_positions[side]) >= max_open_per_side:
                continue
            if ask <= float(side_entry) - buy_drop:
                bought_shares = scalp_notional / max(float(ask), 0.01)
                realized_cash -= scalp_notional
                side_shares[side] += bought_shares
                open_positions[side].append({"price": float(ask), "shares": bought_shares})
        min_floor = min(min_floor, realized_cash + side_shares["up"], realized_cash + side_shares["down"])

    final_payout_if_up = realized_cash + side_shares["up"]
    final_payout_if_down = realized_cash + side_shares["down"]
    final_floor = min(final_payout_if_up, final_payout_if_down)
    completed_profit = round(sum(float(item["profit_usd"]) for item in completed_cycles), 8)
    open_count = sum(len(items) for items in open_positions.values())
    blockers: list[str] = []
    if not bool(signal_context.get("paired_market_snapshot_ready")):
        blockers.append("paired_market_snapshot_required_for_promotion")
    if not paired_forward_mark_available:
        blockers.append("paired_forward_pair_mark_required_for_promotion")
    if len(snapshots) < min_path_snapshots:
        blockers.append("paired_scalp_path_required_for_promotion")
    if not completed_cycles:
        blockers.append("paired_scalp_no_completed_cycles")
    if final_floor <= 0.0:
        blockers.append("paired_scalp_floor_not_positive")
    if open_count:
        blockers.append("paired_scalp_open_positions_remain")
    promotion_economics_ready = not blockers
    return {
        "source": "runtime_shadow_paired_seed_scalp_path",
        "sample_count": 1,
        "pnl_usd": round(final_floor, 8),
        "simulated_pnl_usd": round(final_floor, 8),
        "win_rate": 1.0 if final_floor > 0.0 else 0.0 if final_floor < 0.0 else 0.5,
        "win_count": 1 if final_floor > 0.0 else 0,
        "loss_count": 1 if final_floor < 0.0 else 0,
        "promotion_economics_ready": promotion_economics_ready,
        "blockers": blockers,
        "cost_basis_usd": round(float(pair_cost), 8),
        "paired_entry_up_ask": None if up_entry_ask is None else round(float(up_entry_ask), 8),
        "paired_entry_down_ask": None if down_entry_ask is None else round(float(down_entry_ask), 8),
        "paired_equal_shares": round(float(equal_shares), 8),
        "initial_guaranteed_floor_pnl_usd": round(float(guaranteed_floor_pnl), 8),
        "guaranteed_floor_pnl_usd": round(final_floor, 8),
        "payout_if_up_after_scalp": round(final_payout_if_up, 8),
        "payout_if_down_after_scalp": round(final_payout_if_down, 8),
        "min_floor_during_path_usd": round(float(min_floor), 8),
        "completed_scalp_cycle_count": len(completed_cycles),
        "completed_scalp_profit_usd": completed_profit,
        "open_scalp_position_count": open_count,
        "paired_scalp_snapshot_count": len(snapshots),
        "paired_seed_scalp_min_path_snapshots": min_path_snapshots,
        "paired_seed_scalp_entry_window_fraction": round(float(entry_window_fraction), 8),
        "paired_seed_scalp_buy_cutoff_index": buy_cutoff_index,
        "paired_seed_scalp_buy_drop": round(float(buy_drop), 8),
        "paired_seed_scalp_target": round(float(scalp_target), 8),
        "paired_seed_scalp_notional_usd": round(float(scalp_notional), 8),
        "paired_forward_mark_available": paired_forward_mark_available,
        "paired_forward_up_bid": None if forward_up_bid is None else round(float(forward_up_bid), 8),
        "paired_forward_down_bid": None if forward_down_bid is None else round(float(forward_down_bid), 8),
        "completed_scalp_cycles": completed_cycles[:10],
        "fill_price": round(fill_price, 8),
        "filled_shares": round(shares, 8),
        "liquidation_mark_price": round(float(liquidation_mark_price), 8),
        "liquidation_mark_value_usd": liquidation_mark_value,
        "liquidation_pnl_usd": liquidation_pnl,
        "one_leg_cost_basis_usd": one_leg_cost_basis,
        "spread_drag_usd": round(max(0.0, fill_price - float(liquidation_mark_price)) * shares, 8),
    }


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: object) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None
