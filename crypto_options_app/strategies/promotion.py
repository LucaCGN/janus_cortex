from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_options_app.api.routers.signals import _curate_signal_selection, _hydrate_signal_status
from crypto_options_app.db.connection import table_exists
from crypto_options_app.signals.validation.result_store import validation_status
from crypto_options_app.strategies.registry import all_strategy_specs


@dataclass(frozen=True)
class StrategyPromotionPolicy:
    min_historical_replay_passes: int = 1
    min_live_replay_passes: int = 1
    min_promotion_ready_signals: int = 1
    min_supervised_live_proofs: int = 1
    require_shadow_live_economics: bool = True
    min_shadow_live_pnl_usd: float = 0.0
    min_shadow_live_win_rate: float = 0.7
    recent_shadow_live_window_seconds: int = 3600
    min_recent_shadow_live_sample_count: int = 12
    min_recent_shadow_live_win_rate: float = 0.7
    min_recent_shadow_live_pnl_usd: float = 0.0
    max_live_vs_shadow_pnl_gap_usd: float = 2.0
    max_live_vs_shadow_win_rate_gap: float = 0.2
    live_budget_cap_usd: float = 100.0
    cash_balance_hard_stop_usd: float = 100.0
    auto_promotion_enabled: bool = True


def evaluate_and_persist_strategy_promotions(
    conn: sqlite3.Connection,
    *,
    policy: StrategyPromotionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or StrategyPromotionPolicy()
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    signal_gate = _signal_gate(conn, policy=policy)
    rows: list[dict[str, Any]] = []
    for spec in all_strategy_specs():
        evidence = _strategy_evidence(conn, spec.strategy_id, policy=policy, now_dt=now_dt)
        state, blockers, next_action = _promotion_decision(evidence, signal_gate=signal_gate, policy=policy)
        payload = {
            "strategy_id": spec.strategy_id,
            "strategy_version": spec.strategy_version,
            "strategy_family": spec.strategy_family,
            "promotion_state": state,
            "blockers": blockers,
            "next_action": next_action,
            "evidence": evidence,
            "signal_gate": signal_gate,
            "policy": {
                "min_historical_replay_passes": policy.min_historical_replay_passes,
                "min_live_replay_passes": policy.min_live_replay_passes,
                "min_promotion_ready_signals": policy.min_promotion_ready_signals,
                "min_supervised_live_proofs": policy.min_supervised_live_proofs,
                "require_shadow_live_economics": policy.require_shadow_live_economics,
                "min_shadow_live_pnl_usd": policy.min_shadow_live_pnl_usd,
                "min_shadow_live_win_rate": policy.min_shadow_live_win_rate,
                "recent_shadow_live_window_seconds": policy.recent_shadow_live_window_seconds,
                "min_recent_shadow_live_sample_count": policy.min_recent_shadow_live_sample_count,
                "min_recent_shadow_live_win_rate": policy.min_recent_shadow_live_win_rate,
                "min_recent_shadow_live_pnl_usd": policy.min_recent_shadow_live_pnl_usd,
                "max_live_vs_shadow_pnl_gap_usd": policy.max_live_vs_shadow_pnl_gap_usd,
                "max_live_vs_shadow_win_rate_gap": policy.max_live_vs_shadow_win_rate_gap,
                "live_budget_cap_usd": policy.live_budget_cap_usd,
                "cash_balance_hard_stop_usd": policy.cash_balance_hard_stop_usd,
                "auto_promotion_enabled": policy.auto_promotion_enabled,
            },
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
        conn.execute(
            """
            INSERT INTO strategy_promotion_state(
                promotion_key, strategy_id, strategy_version, promotion_state,
                updated_at_utc, evidence_json
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(promotion_key) DO UPDATE SET
                promotion_state=excluded.promotion_state,
                updated_at_utc=excluded.updated_at_utc,
                evidence_json=excluded.evidence_json
            """,
            (
                f"promotion:{spec.strategy_id}:{spec.strategy_version}",
                spec.strategy_id,
                spec.strategy_version,
                state,
                now,
                json.dumps(payload, sort_keys=True, default=str),
            ),
        )
        rows.append(payload | {"updated_at_utc": now})
    by_state: dict[str, int] = {}
    for row in rows:
        by_state[row["promotion_state"]] = by_state.get(row["promotion_state"], 0) + 1
    return {
        "schema_version": "crypto_options_strategy_promotion_summary_v1",
        "generated_at_utc": now,
        "strategy_count": len(rows),
        "by_promotion_state": dict(sorted(by_state.items())),
        "signal_gate": signal_gate,
        "policy": {
            "live_budget_cap_usd": policy.live_budget_cap_usd,
            "cash_balance_hard_stop_usd": policy.cash_balance_hard_stop_usd,
            "auto_promotion_enabled": policy.auto_promotion_enabled,
            "require_shadow_live_economics": policy.require_shadow_live_economics,
            "min_shadow_live_pnl_usd": policy.min_shadow_live_pnl_usd,
            "min_shadow_live_win_rate": policy.min_shadow_live_win_rate,
            "recent_shadow_live_window_seconds": policy.recent_shadow_live_window_seconds,
            "min_recent_shadow_live_sample_count": policy.min_recent_shadow_live_sample_count,
            "min_recent_shadow_live_win_rate": policy.min_recent_shadow_live_win_rate,
            "min_recent_shadow_live_pnl_usd": policy.min_recent_shadow_live_pnl_usd,
            "max_live_vs_shadow_pnl_gap_usd": policy.max_live_vs_shadow_pnl_gap_usd,
            "max_live_vs_shadow_win_rate_gap": policy.max_live_vs_shadow_win_rate_gap,
            "promotion_means": "automatic state transition only; live orders still require supervised runtime gates",
        },
        "strategies": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
    }


def promotion_state_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(conn, "strategy_promotion_state") or _promotion_row_count(conn) == 0:
        return evaluate_and_persist_strategy_promotions(conn)
    rows = []
    by_state: dict[str, int] = {}
    for row in conn.execute(
        """
        SELECT strategy_id, strategy_version, promotion_state, updated_at_utc, evidence_json
          FROM strategy_promotion_state
         ORDER BY
           CASE promotion_state
             WHEN 'LIVE_RUNNING' THEN 0
             WHEN 'LIVE_CANDIDATE' THEN 1
             WHEN 'SHADOW_READY' THEN 2
             WHEN 'BACKTEST_READY' THEN 3
             WHEN 'NEEDS_BACKTEST' THEN 4
             ELSE 5
           END,
           strategy_id
        """
    ).fetchall():
        payload = _json_load(row["evidence_json"], {})
        payload.setdefault("strategy_id", row["strategy_id"])
        payload.setdefault("strategy_version", row["strategy_version"])
        payload["promotion_state"] = row["promotion_state"]
        payload["updated_at_utc"] = row["updated_at_utc"]
        rows.append(payload)
        by_state[row["promotion_state"]] = by_state.get(row["promotion_state"], 0) + 1
    return {
        "schema_version": "crypto_options_strategy_promotion_summary_v1",
        "strategy_count": len(rows),
        "by_promotion_state": dict(sorted(by_state.items())),
        "strategies": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
    }


def _promotion_decision(
    evidence: dict[str, Any],
    *,
    signal_gate: dict[str, Any],
    policy: StrategyPromotionPolicy,
) -> tuple[str, list[str], str]:
    blockers: list[str] = []
    if evidence["historical_replay_pass_count"] < policy.min_historical_replay_passes:
        blockers.append("missing_historical_strategy_replay")
    if evidence["live_replay_pass_count"] < policy.min_live_replay_passes:
        blockers.append("missing_live_replay_shadow_evidence")
    if policy.require_shadow_live_economics:
        if evidence["live_replay_economic_sample_count"] < policy.min_live_replay_passes:
            blockers.append("missing_shadow_live_economic_evidence")
        elif evidence["live_replay_simulated_pnl_usd"] <= policy.min_shadow_live_pnl_usd:
            blockers.append("shadow_live_non_positive_pnl")
        elif (
            evidence["live_replay_win_rate"] is not None
            and evidence["live_replay_win_rate"] < policy.min_shadow_live_win_rate
        ):
            blockers.append("shadow_live_win_rate_below_floor")
        if evidence["recent_shadow_live_economic_sample_count"] <= 0:
            blockers.append("missing_recent_shadow_live_economic_evidence")
        elif evidence["recent_shadow_live_economic_sample_count"] < policy.min_recent_shadow_live_sample_count:
            blockers.append("recent_shadow_live_sample_below_floor")
        elif evidence["recent_shadow_live_distinct_event_count"] < policy.min_recent_shadow_live_sample_count:
            blockers.append("recent_shadow_live_distinct_event_below_floor")
        else:
            if evidence["recent_shadow_live_simulated_pnl_usd"] <= policy.min_recent_shadow_live_pnl_usd:
                blockers.append("recent_shadow_live_non_positive_pnl")
            if (
                evidence["recent_shadow_live_win_rate"] is None
                or evidence["recent_shadow_live_win_rate"] < policy.min_recent_shadow_live_win_rate
            ):
                blockers.append("recent_shadow_live_win_rate_below_70")
    if evidence["latest_lifecycle_audit_status"] not in {None, "passed"}:
        blockers.append("latest_lifecycle_not_passed")
    if evidence["latest_reconciliation_status"] not in {None, "reconciled"}:
        blockers.append("latest_reconciliation_not_reconciled")
    if signal_gate["promotion_ready_signal_count"] < policy.min_promotion_ready_signals:
        blockers.append("no_promotion_ready_signals")
    if signal_gate["strict_replay_required_count"] > 0:
        blockers.append("selected_signals_require_strict_replay")
    if evidence["supervised_live_blocked_count"] > 0:
        blockers.append("prior_supervised_live_blocker_present")
    if evidence["supervised_live_hard_stop_count"] > 0:
        blockers.append("supervised_live_hard_stop_triggered")
    if evidence["supervised_live_realized_pnl_usd"] < 0:
        blockers.append("supervised_live_negative_realized_pnl")
        if evidence["live_replay_economic_sample_count"] == 0:
            blockers.append("live_loss_without_shadow_economic_baseline")
        elif evidence["live_replay_simulated_pnl_usd"] > 0:
            blockers.append("live_loss_after_positive_shadow_requires_review")
    if (
        evidence["supervised_live_realized_pnl_usd"] < 0
        and evidence["live_replay_simulated_pnl_usd"] is not None
        and evidence["live_replay_simulated_pnl_usd"] > 0
        and evidence["live_vs_shadow_pnl_gap_usd"] is not None
        and evidence["live_vs_shadow_pnl_gap_usd"] > policy.max_live_vs_shadow_pnl_gap_usd
    ):
        blockers.append("live_shadow_actual_drift_exceeds_limit")
    if (
        evidence["supervised_live_win_rate"] is not None
        and evidence["recent_shadow_live_win_rate"] is not None
        and evidence["recent_shadow_live_economic_sample_count"] >= policy.min_recent_shadow_live_sample_count
        and evidence["recent_shadow_live_win_rate"] - evidence["supervised_live_win_rate"] > policy.max_live_vs_shadow_win_rate_gap
    ):
        blockers.append("live_shadow_win_rate_drift_exceeds_limit")

    if "supervised_live_negative_realized_pnl" in blockers:
        return "DEMOTED_TO_SHADOW", blockers, "Demote to shadow; compare shadow economics against live fills/slippage before another supervised live attempt."
    if any(
        blocker in blockers
        for blocker in (
            "prior_supervised_live_blocker_present",
            "supervised_live_hard_stop_triggered",
            "latest_lifecycle_not_passed",
            "latest_reconciliation_not_reconciled",
        )
    ):
        return "REVIEW_BLOCKED", blockers, "Stop live promotion; review mechanical blocker, ledger, and lifecycle evidence."
    if any(
        blocker in blockers
        for blocker in (
            "shadow_live_non_positive_pnl",
            "shadow_live_win_rate_below_floor",
            "recent_shadow_live_non_positive_pnl",
            "recent_shadow_live_win_rate_below_70",
            "live_shadow_actual_drift_exceeds_limit",
            "live_shadow_win_rate_drift_exceeds_limit",
        )
    ):
        return "SHADOW_REVIEW", blockers, "Keep this strategy in shadow only; revise signals/parameters before live promotion."
    if evidence["supervised_live_pass_count"] >= policy.min_supervised_live_proofs and not blockers:
        return "LIVE_RUNNING", [], "Continue supervised live with stop gates, budget scaling, and demotion on loss."
    if not blockers:
        return "LIVE_CANDIDATE", [], "Eligible for supervised live child run with scoped flags and budget cap."
    if all(
        blocker
        not in blockers
        for blocker in (
            "missing_historical_strategy_replay",
            "missing_live_replay_shadow_evidence",
            "latest_lifecycle_not_passed",
            "latest_reconciliation_not_reconciled",
        )
    ):
        return "SHADOW_READY", blockers, "Keep running shadow/live replay and resolve signal promotion blockers before live."
    if "missing_live_replay_shadow_evidence" in blockers and "missing_historical_strategy_replay" not in blockers:
        return "BACKTEST_READY", blockers, "Run strategy live replay/shadow for this strategy before live promotion review."
    return "NEEDS_BACKTEST", blockers, "Run historical replay, then live replay, before considering live promotion."


def _strategy_evidence(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    policy: StrategyPromotionPolicy,
    now_dt: datetime,
) -> dict[str, Any]:
    rows = []
    if table_exists(conn, "strategy_validation_runs"):
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT run_type, run_phase, run_status, lifecycle_audit_status,
                       reconciliation_status, evidence_json, started_at_utc,
                       completed_at_utc
                  FROM strategy_validation_runs
                 WHERE strategy_id=?
                 ORDER BY started_at_utc DESC, inserted_at_utc DESC
                """,
                (strategy_id,),
            ).fetchall()
        ]
    historical_pass_count = sum(
        1
        for row in rows
        if row["run_phase"] in {"historical_replay", "replay_validation"}
        and row["run_status"] == "completed"
        and row["lifecycle_audit_status"] == "passed"
        and row["reconciliation_status"] == "reconciled"
    )
    live_replay_pass_count = sum(
        1
        for row in rows
        if row["run_phase"] in {"live_replay", "live_shadow_test"}
        and row["run_type"] != "supervised_live"
        and row["run_status"] == "completed"
        and row["lifecycle_audit_status"] == "passed"
        and row["reconciliation_status"] == "reconciled"
    )
    live_replay_economics = []
    supervised_live_economics = []
    for row in rows:
        economics = _extract_shadow_economics(row.get("evidence_json"))
        if economics is None:
            continue
        economics["started_at_utc"] = row.get("started_at_utc")
        economics["completed_at_utc"] = row.get("completed_at_utc")
        if row["run_phase"] in {"live_replay", "live_shadow_test"} and row["run_type"] != "supervised_live" and row["run_status"] == "completed":
            live_replay_economics.append(economics)
        if row["run_type"] == "supervised_live" and row["run_status"] == "completed":
            supervised_live_economics.append(economics)
    recent_cutoff = now_dt - timedelta(seconds=max(1, int(policy.recent_shadow_live_window_seconds)))
    recent_shadow_live_economics = [
        economics
        for economics in live_replay_economics
        if (observed_at := _economics_observed_at(economics)) is not None and observed_at >= recent_cutoff
    ]
    live_aggregate = _aggregate_economics(live_replay_economics)
    recent_live_aggregate = _aggregate_economics(recent_shadow_live_economics)
    supervised_live_aggregate = _aggregate_economics(supervised_live_economics)
    live_replay_simulated_pnl_usd = live_aggregate["pnl_usd"]
    economic_win_count = live_aggregate["win_count"]
    economic_loss_count = live_aggregate["loss_count"]
    live_replay_win_rate = live_aggregate["win_rate"]
    supervised_live_pass_count = sum(
        1
        for row in rows
        if row["run_type"] == "supervised_live"
        and row["run_status"] == "completed"
        and row["lifecycle_audit_status"] == "passed"
        and row["reconciliation_status"] == "reconciled"
    )
    supervised_live_blocked_count = sum(1 for row in rows if row["run_type"] == "supervised_live" and row["run_status"] == "blocked")
    supervised_live_realized_pnl_usd = 0.0
    supervised_live_open_cost_usd = 0.0
    supervised_live_hard_stop_count = 0
    if table_exists(conn, "validation_budget_ledger"):
        ledger = conn.execute(
            """
            SELECT COALESCE(SUM(realized_pnl_usd), 0) AS realized_pnl_usd,
                   COALESCE(SUM(open_cost_usd), 0) AS open_cost_usd,
                   COALESCE(SUM(hard_stop_triggered), 0) AS hard_stop_count
              FROM validation_budget_ledger
             WHERE strategy_or_component_id=?
            """,
            (strategy_id,),
        ).fetchone()
        if ledger is not None:
            supervised_live_realized_pnl_usd = float(ledger["realized_pnl_usd"] or 0.0)
            supervised_live_open_cost_usd = float(ledger["open_cost_usd"] or 0.0)
            supervised_live_hard_stop_count = int(ledger["hard_stop_count"] or 0)
    live_vs_shadow_pnl_gap_usd = None
    if live_replay_economics and supervised_live_realized_pnl_usd < 0:
        live_vs_shadow_pnl_gap_usd = round(live_replay_simulated_pnl_usd - supervised_live_realized_pnl_usd, 8)
    latest = rows[0] if rows else {}
    latest_completed = next((row for row in rows if row.get("run_status") == "completed"), None) or {}
    return {
        "historical_replay_pass_count": historical_pass_count,
        "live_replay_pass_count": live_replay_pass_count,
        "live_replay_economic_sample_count": len(live_replay_economics),
        "live_replay_simulated_pnl_usd": live_replay_simulated_pnl_usd,
        "live_replay_win_count": economic_win_count,
        "live_replay_loss_count": economic_loss_count,
        "live_replay_win_rate": live_replay_win_rate,
        "recent_shadow_live_economic_sample_count": recent_live_aggregate["sample_count"],
        "recent_shadow_live_simulated_pnl_usd": recent_live_aggregate["pnl_usd"],
        "recent_shadow_live_win_count": recent_live_aggregate["win_count"],
        "recent_shadow_live_loss_count": recent_live_aggregate["loss_count"],
        "recent_shadow_live_win_rate": recent_live_aggregate["win_rate"],
        "recent_shadow_live_distinct_event_count": recent_live_aggregate["distinct_event_count"],
        "recent_shadow_live_distinct_token_count": recent_live_aggregate["distinct_token_count"],
        "recent_shadow_live_window_seconds": policy.recent_shadow_live_window_seconds,
        "supervised_live_pass_count": supervised_live_pass_count,
        "supervised_live_blocked_count": supervised_live_blocked_count,
        "supervised_live_realized_pnl_usd": supervised_live_realized_pnl_usd,
        "supervised_live_open_cost_usd": supervised_live_open_cost_usd,
        "supervised_live_hard_stop_count": supervised_live_hard_stop_count,
        "supervised_live_economic_sample_count": supervised_live_aggregate["sample_count"],
        "supervised_live_win_rate": supervised_live_aggregate["win_rate"],
        "live_vs_shadow_pnl_gap_usd": live_vs_shadow_pnl_gap_usd,
        "latest_run_type": latest.get("run_type"),
        "latest_run_phase": latest.get("run_phase"),
        "latest_run_status": latest.get("run_status"),
        "latest_lifecycle_audit_status": latest_completed.get("lifecycle_audit_status"),
        "latest_reconciliation_status": latest_completed.get("reconciliation_status"),
        "latest_started_at_utc": latest.get("started_at_utc"),
        "run_count": len(rows),
    }


def _signal_gate(conn: sqlite3.Connection, *, policy: StrategyPromotionPolicy) -> dict[str, Any]:
    try:
        status = validation_status(conn)
    except Exception as exc:  # pragma: no cover - defensive endpoint fallback.
        return {
            "promotion_ready_signal_count": 0,
            "strict_replay_required_count": 0,
            "signal_gate_status": "unavailable",
            "blockers": [f"signal_validation_status_unavailable:{type(exc).__name__}"],
        }
    by_promotion = status.get("by_promotion_state") or {}
    signals = status.get("signals") or []
    curated = _curate_signal_selection([_hydrate_signal_status(dict(row)) for row in signals])
    strict_replay_required_count = int((curated.get("action_state_counts") or {}).get("STRICT_REPLAY_REQUIRED", 0))
    promotion_ready_count = int(by_promotion.get("PROMOTION_READY") or 0)
    blockers = []
    if promotion_ready_count < policy.min_promotion_ready_signals:
        blockers.append("no_promotion_ready_signals")
    if strict_replay_required_count > 0:
        blockers.append("strict_replay_required_for_selected_signals")
    return {
        "promotion_ready_signal_count": promotion_ready_count,
        "strict_replay_required_count": strict_replay_required_count,
        "selected_signal_count": int(curated.get("selected_count") or 0),
        "signal_count": int(status.get("signal_count") or 0),
        "signal_gate_status": "passed" if not blockers else "blocked",
        "blockers": blockers,
    }


def _promotion_row_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM strategy_promotion_state").fetchone()[0])


def _json_load(value: Any, fallback: Any) -> Any:
    if value in {None, ""}:
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _extract_shadow_economics(evidence_json: Any) -> dict[str, Any] | None:
    evidence = _json_load(evidence_json, {})
    if not isinstance(evidence, dict):
        return None
    candidates: list[dict[str, Any]] = []
    for value in (
        evidence.get("economics"),
        evidence.get("shadow_economics"),
        evidence.get("result_economics"),
    ):
        if isinstance(value, dict):
            candidates.append(value)
    result = evidence.get("result")
    if isinstance(result, dict):
        for value in (
            result.get("economics"),
            result.get("shadow_economics"),
            result.get("result_economics"),
        ):
            if isinstance(value, dict):
                candidates.append(value)
        attribution = result.get("attribution")
        if isinstance(attribution, dict):
            signal_context = attribution.get("signal_context")
            if isinstance(signal_context, dict):
                candidates.append(signal_context)
    for candidate in candidates:
        source = str(candidate.get("source") or "")
        if source == "runtime_shadow_mark_to_observed_bid":
            continue
        if candidate.get("promotion_economics_ready") is False:
            continue
        result_payload = result if isinstance(result, dict) else {}
        pnl = _first_float(
            candidate.get("pnl_usd"),
            candidate.get("simulated_pnl_usd"),
            candidate.get("shadow_pnl_usd"),
            candidate.get("realized_pnl_usd"),
            candidate.get("expected_pnl_usd"),
        )
        if pnl is None:
            continue
        win_rate = _first_float(candidate.get("win_rate"), candidate.get("hit_rate"))
        sample_count = _first_int(
            candidate.get("sample_count"),
            candidate.get("event_count"),
            candidate.get("filled_event_count"),
            candidate.get("settled_event_count"),
            candidate.get("trade_count"),
            candidate.get("position_count"),
        )
        if sample_count is not None and sample_count <= 0:
            continue
        win_count = _first_float(candidate.get("win_count"), candidate.get("wins"))
        loss_count = _first_float(candidate.get("loss_count"), candidate.get("losses"))
        return {
            "pnl_usd": float(pnl),
            "win_rate": win_rate,
            "sample_count": max(1, int(sample_count or 1)),
            "win_count": win_count,
            "loss_count": loss_count,
            "source": source or "strategy_validation_evidence_json",
            "event_key": _first_text(candidate.get("event_key"), result_payload.get("event_key")),
            "event_slug": _first_text(candidate.get("event_slug"), result_payload.get("event_slug")),
            "event_token_key": _first_text(candidate.get("event_token_key"), result_payload.get("event_token_key")),
            "distinct_event_count": _first_int(candidate.get("distinct_event_count")),
            "distinct_token_count": _first_int(candidate.get("distinct_token_count")),
        }
    return None


def _aggregate_economics(economics_rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_usd = round(sum(float(item.get("pnl_usd") or 0.0) for item in economics_rows), 8)
    sample_count = sum(max(1, int(item.get("sample_count") or 1)) for item in economics_rows)
    event_keys: set[str] = set()
    token_keys: set[str] = set()
    explicit_distinct_event_count = 0
    explicit_distinct_token_count = 0
    win_count = 0.0
    loss_count = 0.0
    for item in economics_rows:
        samples = max(1, int(item.get("sample_count") or 1))
        event_key = _first_text(item.get("event_key"), item.get("event_slug"))
        token_key = _first_text(item.get("event_token_key"))
        if event_key:
            event_keys.add(event_key)
        else:
            explicit_distinct_event_count += max(1, int(item.get("distinct_event_count") or samples))
        if token_key:
            token_keys.add(token_key)
        else:
            explicit_distinct_token_count += max(1, int(item.get("distinct_token_count") or samples))
        explicit_wins = _first_float(item.get("win_count"))
        explicit_losses = _first_float(item.get("loss_count"))
        if explicit_wins is not None or explicit_losses is not None:
            win_count += max(0.0, float(explicit_wins or 0.0))
            loss_count += max(0.0, float(explicit_losses or 0.0))
            continue
        win_rate = _first_float(item.get("win_rate"))
        if win_rate is not None:
            bounded_win_rate = min(1.0, max(0.0, float(win_rate)))
            win_count += bounded_win_rate * samples
            loss_count += (1.0 - bounded_win_rate) * samples
            continue
        pnl = float(item.get("pnl_usd") or 0.0)
        if pnl > 0:
            win_count += samples
        elif pnl < 0:
            loss_count += samples
    win_rate = round(win_count / sample_count, 6) if sample_count else None
    return {
        "sample_count": int(sample_count),
        "pnl_usd": pnl_usd,
        "win_count": round(win_count, 6),
        "loss_count": round(loss_count, 6),
        "win_rate": win_rate,
        "distinct_event_count": len(event_keys) + explicit_distinct_event_count,
        "distinct_token_count": len(token_keys) + explicit_distinct_token_count,
    }


def _economics_observed_at(economics: dict[str, Any]) -> datetime | None:
    return _parse_datetime(economics.get("completed_at_utc")) or _parse_datetime(economics.get("started_at_utc"))


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None
