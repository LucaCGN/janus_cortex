from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_options_app.api.routers.signals import _curate_signal_selection, _hydrate_signal_status
from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.connection import table_exists
from crypto_options_app.replay.calibration_report import calibrated_replay_evidence_for_strategy
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
    min_recent_shadow_live_sample_count: int = 3
    min_recent_shadow_live_distinct_event_count: int = 1
    min_recent_shadow_live_win_rate: float = 0.7
    min_recent_shadow_live_pnl_usd: float = 0.0
    max_recent_shadow_live_loss_streak: int = 3
    allow_aggregate_shadow_live_promotion: bool = False
    max_live_vs_shadow_pnl_gap_usd: float = 2.0
    max_live_vs_shadow_win_rate_gap: float = 0.2
    max_supervised_live_loss_usd: float = 10.0
    min_supervised_live_realized_pnl_usd: float = 0.0
    live_budget_cap_usd: float = 100.0
    cash_balance_hard_stop_usd: float = 100.0
    auto_promotion_enabled: bool = True


def promotion_policy_contract(policy: StrategyPromotionPolicy | None = None) -> dict[str, Any]:
    policy = policy or StrategyPromotionPolicy()
    return {
        "schema_version": "crypto_options_promotion_policy_contract_v1",
        "signals": {
            "promotable_state": "PROMOTION_READY",
            "terminal_review_states": ["PROMOTED", "REVIEW", "RETIRED", "BLOCKED", "NEEDS_VARIANT"],
            "not_promotable_labels": [
                "PASSED",
                "SELECTED",
                "STRUCTURAL_PASS",
                "STRUCTURAL_ALTERNATE",
                "STRICT_REPLAY_REQUIRED",
            ],
            "strict_replay_required_means_promotable": False,
            "required_engine_evidence": [
                "strict replay or live-shadow evidence is complete",
                "no selected signal has strict review blockers",
                "signal validation result state is PROMOTION_READY",
            ],
        },
        "strategies": {
            "states": [
                "NEEDS_BACKTEST",
                "BACKTEST_READY",
                "SHADOW_READY",
                "SHADOW_REVIEW",
                "REVIEW_BLOCKED",
                "LIVE_CANDIDATE",
                "LIVE_RUNNING",
                "DEMOTED_TO_SHADOW",
            ],
            "live_candidate_requirements": {
                "historical_replay_passes": policy.min_historical_replay_passes,
                "live_replay_passes": policy.min_live_replay_passes,
                "promotion_ready_signals": policy.min_promotion_ready_signals,
                "recent_distinct_economic_samples": policy.min_recent_shadow_live_sample_count,
                "recent_distinct_events": policy.min_recent_shadow_live_distinct_event_count,
                "recent_shadow_live_win_rate_gt": policy.min_recent_shadow_live_win_rate,
                "recent_shadow_live_pnl_usd_gt": policy.min_recent_shadow_live_pnl_usd,
                "lifecycle_audit_status": "passed",
                "reconciliation_status": "reconciled",
                "strict_signal_blockers": 0,
                "shadow_live_drift_within_tolerance": True,
            },
            "strategy_defined_criteria": {
                "supported": True,
                "source_fields": [
                    "metadata.promotion_policy",
                    "metadata.promotion_criteria",
                    "risk_gates.promotion_policy",
                    "live_pulse_requirements.promotion_policy",
                ],
                "allowed_override_fields": sorted(field.name for field in fields(StrategyPromotionPolicy)),
                "example": {
                    "allow_aggregate_shadow_live_promotion": True,
                    "min_recent_shadow_live_win_rate": 0.55,
                    "min_recent_shadow_live_pnl_usd": 5.0,
                    "max_recent_shadow_live_loss_streak": 3,
                    "max_supervised_live_loss_usd": 10.0,
                },
                "rule": "A strategy may lower win-rate floors only by defining stronger PnL, loss, lifecycle, reconciliation, and drift gates in its spec.",
            },
            "demotion_blockers": [
                "supervised_live_realized_pnl_below_loss_limit",
                "supervised_live_pnl_below_strategy_floor",
                "latest_lifecycle_not_passed",
                "latest_reconciliation_not_reconciled",
                "live_shadow_actual_drift_exceeds_limit",
                "live_shadow_win_rate_drift_exceeds_limit",
                "supervised_live_hard_stop_triggered",
            ],
            "budget_policy": {
                "live_budget_cap_usd": policy.live_budget_cap_usd,
                "cash_balance_hard_stop_usd": policy.cash_balance_hard_stop_usd,
                "max_supervised_live_loss_usd": policy.max_supervised_live_loss_usd,
                "min_supervised_live_realized_pnl_usd": policy.min_supervised_live_realized_pnl_usd,
                "scale_requires_repeated_positive_reconciled_live_evidence": True,
                "descale_on_loss_streak_or_negative_pnl_or_drift": True,
            },
        },
        "live_safety": {
            "chat_judgment_can_authorize_live": False,
            "automation_can_authorize_live": True,
            "automation_authority_scope": "promotion manager plus app executor/budget/risk/lifecycle/reconciliation/stop gates only",
            "orders_allowed_default": bool(DEFAULT_CONFIG.orders_allowed),
            "live_trading_authorized_default": bool(DEFAULT_CONFIG.live_trading_authorized),
            "requires_app_live_runtime": True,
            "requires_scoped_child_process_flags": True,
            "manual_orders_allowed": bool(DEFAULT_CONFIG.manual_orders_allowed),
            "manual_orders_avoided": True,
        },
    }


def evaluate_and_persist_strategy_promotions(
    conn: Any,
    *,
    policy: StrategyPromotionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or StrategyPromotionPolicy()
    policy_contract = promotion_policy_contract(policy)
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    signal_gate = _signal_gate(conn, policy=policy)
    rows: list[dict[str, Any]] = []
    for spec in all_strategy_specs():
        effective_policy, strategy_policy_overrides = _strategy_policy_for_spec(spec, base_policy=policy)
        evidence = _strategy_evidence(conn, spec.strategy_id, policy=effective_policy, now_dt=now_dt)
        state, blockers, next_action = _promotion_decision(evidence, signal_gate=signal_gate, policy=effective_policy)
        if state == "LIVE_CANDIDATE" and _strategy_excluded_from_initial_live_validation(spec):
            blockers = list(blockers) + ["initial_live_validation_strategy_style_excluded"]
            state = "SHADOW_REVIEW"
            next_action = "Keep in shadow/review for now; tail/underdog style is excluded from the initial live validation lane."
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
                "min_historical_replay_passes": effective_policy.min_historical_replay_passes,
                "min_live_replay_passes": effective_policy.min_live_replay_passes,
                "min_promotion_ready_signals": effective_policy.min_promotion_ready_signals,
                "min_supervised_live_proofs": effective_policy.min_supervised_live_proofs,
                "require_shadow_live_economics": effective_policy.require_shadow_live_economics,
                "min_shadow_live_pnl_usd": effective_policy.min_shadow_live_pnl_usd,
                "min_shadow_live_win_rate": effective_policy.min_shadow_live_win_rate,
                "recent_shadow_live_window_seconds": effective_policy.recent_shadow_live_window_seconds,
                "min_recent_shadow_live_sample_count": effective_policy.min_recent_shadow_live_sample_count,
                "min_recent_shadow_live_distinct_event_count": effective_policy.min_recent_shadow_live_distinct_event_count,
                "min_recent_shadow_live_win_rate": effective_policy.min_recent_shadow_live_win_rate,
                "min_recent_shadow_live_pnl_usd": effective_policy.min_recent_shadow_live_pnl_usd,
                "max_recent_shadow_live_loss_streak": effective_policy.max_recent_shadow_live_loss_streak,
                "allow_aggregate_shadow_live_promotion": effective_policy.allow_aggregate_shadow_live_promotion,
                "max_live_vs_shadow_pnl_gap_usd": effective_policy.max_live_vs_shadow_pnl_gap_usd,
                "max_live_vs_shadow_win_rate_gap": effective_policy.max_live_vs_shadow_win_rate_gap,
                "max_supervised_live_loss_usd": effective_policy.max_supervised_live_loss_usd,
                "min_supervised_live_realized_pnl_usd": effective_policy.min_supervised_live_realized_pnl_usd,
                "live_budget_cap_usd": effective_policy.live_budget_cap_usd,
                "cash_balance_hard_stop_usd": effective_policy.cash_balance_hard_stop_usd,
                "auto_promotion_enabled": effective_policy.auto_promotion_enabled,
            },
            "strategy_policy_overrides": strategy_policy_overrides,
            "policy_contract_schema_version": policy_contract["schema_version"],
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
            "min_recent_shadow_live_distinct_event_count": policy.min_recent_shadow_live_distinct_event_count,
            "min_recent_shadow_live_win_rate": policy.min_recent_shadow_live_win_rate,
            "min_recent_shadow_live_pnl_usd": policy.min_recent_shadow_live_pnl_usd,
            "max_recent_shadow_live_loss_streak": policy.max_recent_shadow_live_loss_streak,
            "max_live_vs_shadow_pnl_gap_usd": policy.max_live_vs_shadow_pnl_gap_usd,
            "max_live_vs_shadow_win_rate_gap": policy.max_live_vs_shadow_win_rate_gap,
            "max_supervised_live_loss_usd": policy.max_supervised_live_loss_usd,
            "min_supervised_live_realized_pnl_usd": policy.min_supervised_live_realized_pnl_usd,
            "promotion_means": "automatic state transition; live orders flow through strategy-defined app runtime gates",
        },
        "policy_contract": policy_contract,
        "strategies": rows,
        "orders_allowed": bool(DEFAULT_CONFIG.orders_allowed),
        "live_trading_authorized": bool(DEFAULT_CONFIG.live_trading_authorized),
        "manual_orders_allowed": bool(DEFAULT_CONFIG.manual_orders_allowed),
        "manual_orders_avoided": True,
    }


def promotion_state_summary(conn: Any) -> dict[str, Any]:
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
        "policy_contract": promotion_policy_contract(),
        "strategies": rows,
        "orders_allowed": bool(DEFAULT_CONFIG.orders_allowed),
        "live_trading_authorized": bool(DEFAULT_CONFIG.live_trading_authorized),
        "manual_orders_allowed": bool(DEFAULT_CONFIG.manual_orders_allowed),
        "manual_orders_avoided": True,
    }


def _strategy_policy_for_spec(spec: Any, *, base_policy: StrategyPromotionPolicy) -> tuple[StrategyPromotionPolicy, dict[str, Any]]:
    raw_overrides: dict[str, Any] = {}
    for source in (
        _mapping_path(getattr(spec, "metadata", {}), "promotion_policy"),
        _mapping_path(getattr(spec, "metadata", {}), "promotion_criteria"),
        _mapping_path(getattr(spec, "risk_gates", {}), "promotion_policy"),
        _mapping_path(getattr(spec, "live_pulse_requirements", {}), "promotion_policy"),
    ):
        raw_overrides.update(source)
    aliases = {
        "min_win_rate": "min_recent_shadow_live_win_rate",
        "min_recent_win_rate": "min_recent_shadow_live_win_rate",
        "min_pnl_usd": "min_recent_shadow_live_pnl_usd",
        "min_recent_pnl_usd": "min_recent_shadow_live_pnl_usd",
        "max_live_loss_usd": "max_supervised_live_loss_usd",
        "max_loss_streak": "max_recent_shadow_live_loss_streak",
    }
    for alias, target in aliases.items():
        if alias in raw_overrides and target not in raw_overrides:
            raw_overrides[target] = raw_overrides[alias]
    base_values = {field.name: getattr(base_policy, field.name) for field in fields(StrategyPromotionPolicy)}
    accepted: dict[str, Any] = {}
    for name, current in base_values.items():
        if name in raw_overrides:
            accepted[name] = _coerce_policy_value(raw_overrides[name], current)
    if not accepted:
        return base_policy, {}
    return replace(base_policy, **accepted), accepted


def _strategy_excluded_from_initial_live_validation(spec: Any) -> bool:
    metadata = getattr(spec, "metadata", {}) or {}
    if metadata.get("allow_initial_tail_underdog_live_validation") is True:
        return False
    strategy_id = str(getattr(spec, "strategy_id", "") or "").lower()
    style = " ".join(
        str(value).lower()
        for value in (
            strategy_id,
            metadata.get("strategy_family"),
            metadata.get("strategy_style"),
            metadata.get("entry_style"),
            metadata.get("version_notes"),
        )
        if value is not None
    )
    return "tail_reversal" in style or "underdog" in style or "tail touch" in style


def _mapping_path(payload: Any, key: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _coerce_policy_value(value: Any, current: Any) -> Any:
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(float(value))
    if isinstance(current, float):
        return float(value)
    return value


def _recent_win_rate_blocker(policy: StrategyPromotionPolicy) -> str:
    if abs(policy.min_recent_shadow_live_win_rate - 0.7) < 0.000001:
        return "recent_shadow_live_win_rate_below_70"
    return "recent_shadow_live_win_rate_below_strategy_floor"


def _promotion_decision(
    evidence: dict[str, Any],
    *,
    signal_gate: dict[str, Any],
    policy: StrategyPromotionPolicy,
) -> tuple[str, list[str], str]:
    blockers: list[str] = []
    if evidence["historical_replay_pass_count"] < policy.min_historical_replay_passes:
        blockers.append("missing_historical_strategy_replay")
        if evidence.get("latest_historical_replay_run_status") == "blocked":
            blockers.append("historical_strategy_replay_blocked")
    if evidence["live_replay_pass_count"] < policy.min_live_replay_passes:
        blockers.append("missing_live_replay_shadow_evidence")
        if evidence.get("latest_shadow_replay_run_status") == "blocked":
            blockers.append("shadow_live_replay_blocked")
    if policy.require_shadow_live_economics:
        if evidence["live_replay_economic_sample_count"] < policy.min_live_replay_passes:
            blockers.append("missing_shadow_live_economic_evidence")
        elif evidence["live_replay_simulated_pnl_usd"] <= policy.min_shadow_live_pnl_usd:
            blockers.append("shadow_live_non_positive_pnl")
        elif (
            evidence["live_replay_win_rate"] is not None
            and evidence["live_replay_win_rate"] <= policy.min_shadow_live_win_rate
        ):
            blockers.append("shadow_live_win_rate_below_floor")
        use_aggregate_shadow_as_recent = (
            bool(policy.allow_aggregate_shadow_live_promotion)
            and evidence["recent_shadow_live_economic_sample_count"] <= 0
            and evidence["live_replay_economic_sample_count"] > 0
        )
        if evidence["recent_shadow_live_economic_sample_count"] <= 0 and not use_aggregate_shadow_as_recent:
            blockers.append("missing_recent_shadow_live_economic_evidence")
        elif (
            evidence["recent_shadow_live_economic_sample_count"] < policy.min_recent_shadow_live_sample_count
            and not use_aggregate_shadow_as_recent
        ):
            blockers.append("recent_shadow_live_sample_below_floor")
        elif (
            evidence["recent_shadow_live_distinct_event_count"] < policy.min_recent_shadow_live_distinct_event_count
            and not use_aggregate_shadow_as_recent
        ):
            blockers.append("recent_shadow_live_distinct_event_below_floor")
        else:
            recent_pnl = (
                evidence["live_replay_simulated_pnl_usd"]
                if use_aggregate_shadow_as_recent
                else evidence["recent_shadow_live_simulated_pnl_usd"]
            )
            recent_win_rate = (
                evidence["live_replay_win_rate"]
                if use_aggregate_shadow_as_recent
                else evidence["recent_shadow_live_win_rate"]
            )
            recent_loss_streak = 0 if use_aggregate_shadow_as_recent else evidence["recent_shadow_live_loss_streak"]
            if recent_pnl <= policy.min_recent_shadow_live_pnl_usd:
                blockers.append("recent_shadow_live_non_positive_pnl")
            if recent_win_rate is None or recent_win_rate <= policy.min_recent_shadow_live_win_rate:
                blockers.append(_recent_win_rate_blocker(policy))
            if policy.max_recent_shadow_live_loss_streak > 0 and recent_loss_streak > policy.max_recent_shadow_live_loss_streak:
                blockers.append("recent_shadow_live_loss_streak_exceeds_strategy_limit")
    active_live_activity_count = int(evidence.get("active_live_position_count") or 0) + int(
        evidence.get("active_live_order_count") or 0
    )
    if evidence["latest_lifecycle_audit_status"] not in {None, "passed"} and active_live_activity_count <= 0:
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
    if (
        policy.max_recent_shadow_live_loss_streak > 0
        and evidence["supervised_live_loss_streak"] >= policy.max_recent_shadow_live_loss_streak
    ):
        blockers.append("supervised_live_loss_streak_exceeds_strategy_limit")
    if evidence["supervised_live_realized_pnl_usd"] <= -abs(policy.max_supervised_live_loss_usd):
        blockers.append("supervised_live_realized_pnl_below_loss_limit")
        if evidence["live_replay_economic_sample_count"] == 0:
            blockers.append("live_loss_without_shadow_economic_baseline")
        elif evidence["live_replay_simulated_pnl_usd"] > 0:
            blockers.append("live_loss_after_positive_shadow_requires_review")
    if (
        evidence["supervised_live_pass_count"] > 0
        and evidence["supervised_live_realized_pnl_usd"] < policy.min_supervised_live_realized_pnl_usd
    ):
        blockers.append("supervised_live_pnl_below_strategy_floor")
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
    calibrated_blockers = set(evidence.get("calibrated_replay_blockers") or [])
    if "calibrated_replay_non_positive_pnl" in calibrated_blockers:
        blockers.append("calibrated_replay_non_positive_pnl")
    if "calibrated_replay_live_loss_after_positive_simulation" in calibrated_blockers:
        blockers.append("calibrated_replay_live_loss_after_positive_simulation")
    if "account_incident_actual_negative_pnl" in calibrated_blockers:
        blockers.append("account_incident_actual_negative_pnl")

    hard_stop_blockers = {
        "supervised_live_realized_pnl_below_loss_limit",
        "prior_supervised_live_blocker_present",
        "supervised_live_hard_stop_triggered",
        "supervised_live_loss_streak_exceeds_strategy_limit",
        "latest_lifecycle_not_passed",
        "latest_reconciliation_not_reconciled",
        "historical_strategy_replay_blocked",
        "live_shadow_actual_drift_exceeds_limit",
        "live_shadow_win_rate_drift_exceeds_limit",
    }
    if active_live_activity_count > 0 and not any(blocker in blockers for blocker in hard_stop_blockers):
        return "LIVE_RUNNING", [], "Live order or position is active; continue app stop gates, reconciliation, and strategy-defined demotion checks."
    if "supervised_live_realized_pnl_below_loss_limit" in blockers or "supervised_live_loss_streak_exceeds_strategy_limit" in blockers:
        return "DEMOTED_TO_SHADOW", blockers, "Demote to shadow; compare shadow economics against live fills/slippage before another live attempt."
    if any(
        blocker in blockers
        for blocker in (
            "prior_supervised_live_blocker_present",
            "supervised_live_hard_stop_triggered",
            "supervised_live_loss_streak_exceeds_strategy_limit",
            "latest_lifecycle_not_passed",
            "latest_reconciliation_not_reconciled",
            "historical_strategy_replay_blocked",
        )
    ):
        return "REVIEW_BLOCKED", blockers, "Stop live promotion; review mechanical blocker, ledger, and lifecycle evidence."
    if any(
        blocker in blockers
        for blocker in (
            "shadow_live_non_positive_pnl",
            "shadow_live_win_rate_below_floor",
            "recent_shadow_live_non_positive_pnl",
            _recent_win_rate_blocker(policy),
            "recent_shadow_live_loss_streak_exceeds_strategy_limit",
            "live_shadow_actual_drift_exceeds_limit",
            "live_shadow_win_rate_drift_exceeds_limit",
            "calibrated_replay_non_positive_pnl",
            "calibrated_replay_live_loss_after_positive_simulation",
            "account_incident_actual_negative_pnl",
            "supervised_live_pnl_below_strategy_floor",
            "shadow_live_replay_blocked",
        )
    ):
        return "SHADOW_REVIEW", blockers, "Keep this strategy in shadow only; revise signals/parameters before live promotion."
    if evidence["supervised_live_pass_count"] >= policy.min_supervised_live_proofs and not blockers:
        return "LIVE_RUNNING", [], "Continue live with stop gates, budget scaling, and demotion on loss."
    if not blockers:
        return "LIVE_CANDIDATE", [], "Eligible for app-gated live execution with scoped flags and budget cap."
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
    conn: Any,
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
    supervised_live_loss_streak = 0
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
        supervised_live_loss_streak = _live_ledger_loss_streak(conn, strategy_id=strategy_id)
    active_live = active_live_position_summary(conn, strategy_id=strategy_id, now_dt=now_dt)
    active_orders = active_live_order_summary(conn, strategy_id=strategy_id, now_dt=now_dt)
    live_vs_shadow_pnl_gap_usd = None
    if live_replay_economics and supervised_live_realized_pnl_usd < 0:
        live_vs_shadow_pnl_gap_usd = round(live_replay_simulated_pnl_usd - supervised_live_realized_pnl_usd, 8)
    calibrated_replay = calibrated_replay_evidence_for_strategy(strategy_id) or {}
    latest = rows[0] if rows else {}
    latest_completed = next((row for row in rows if row.get("run_status") == "completed"), None) or {}
    latest_historical = next(
        (row for row in rows if row.get("run_phase") in {"historical_replay", "replay_validation"}),
        None,
    ) or {}
    latest_shadow_replay = next(
        (
            row
            for row in rows
            if row.get("run_phase") in {"live_replay", "live_shadow_test"}
            and row.get("run_type") != "supervised_live"
        ),
        None,
    ) or {}
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
        "recent_shadow_live_loss_streak": _current_economic_loss_streak(recent_shadow_live_economics),
        "recent_shadow_live_distinct_event_count": recent_live_aggregate["distinct_event_count"],
        "recent_shadow_live_distinct_token_count": recent_live_aggregate["distinct_token_count"],
        "recent_shadow_live_window_seconds": policy.recent_shadow_live_window_seconds,
        "supervised_live_pass_count": supervised_live_pass_count,
        "supervised_live_blocked_count": supervised_live_blocked_count,
        "supervised_live_realized_pnl_usd": supervised_live_realized_pnl_usd,
        "supervised_live_open_cost_usd": supervised_live_open_cost_usd,
        "supervised_live_hard_stop_count": supervised_live_hard_stop_count,
        "supervised_live_loss_streak": supervised_live_loss_streak,
        "supervised_live_economic_sample_count": supervised_live_aggregate["sample_count"],
        "supervised_live_win_rate": supervised_live_aggregate["win_rate"],
        "active_live_position_count": active_live["active_live_position_count"],
        "active_live_order_count": active_orders["active_live_order_count"],
        "active_live_open_cost_usd": active_live["active_live_open_cost_usd"],
        "active_live_latest_updated_at_utc": (
            max(
                (
                    item
                    for item in [
                        active_live["active_live_latest_updated_at_utc"],
                        active_orders["active_live_order_latest_updated_at_utc"],
                    ]
                    if item
                ),
                default=None,
            )
        ),
        "active_live_position_keys": active_live["active_live_position_keys"],
        "active_live_order_keys": active_orders["active_live_order_keys"],
        "live_vs_shadow_pnl_gap_usd": live_vs_shadow_pnl_gap_usd,
        "calibrated_replay": calibrated_replay,
        "calibrated_replay_scoring_ready": bool(calibrated_replay.get("scoring_ready")),
        "calibrated_replay_simulated_pnl_usd": calibrated_replay.get("simulated_pnl_usd"),
        "calibrated_replay_actual_resolved_pnl_usd": calibrated_replay.get("actual_resolved_pnl_usd"),
        "calibrated_replay_data_quality_score": calibrated_replay.get("data_quality_score"),
        "calibrated_replay_blockers": calibrated_replay.get("blockers", []),
        "latest_run_type": latest.get("run_type"),
        "latest_run_phase": latest.get("run_phase"),
        "latest_run_status": latest.get("run_status"),
        "latest_historical_replay_run_status": latest_historical.get("run_status"),
        "latest_historical_replay_started_at_utc": latest_historical.get("started_at_utc"),
        "latest_shadow_replay_run_status": latest_shadow_replay.get("run_status"),
        "latest_shadow_replay_started_at_utc": latest_shadow_replay.get("started_at_utc"),
        "latest_lifecycle_audit_status": latest_completed.get("lifecycle_audit_status"),
        "latest_reconciliation_status": latest_completed.get("reconciliation_status"),
        "latest_started_at_utc": latest.get("started_at_utc"),
        "run_count": len(rows),
    }


def active_live_order_summary(conn: Any, *, strategy_id: str | None = None, now_dt: datetime | None = None) -> dict[str, Any]:
    """Return active submitted live orders that have not produced a filled position yet."""

    if not table_exists(conn, "orders"):
        return {
            "active_live_order_count": 0,
            "active_live_order_latest_updated_at_utc": None,
            "active_live_order_keys": [],
            "active_live_order_strategies": [],
        }
    now_dt = now_dt or datetime.now(UTC)
    params: tuple[Any, ...] = ()
    strategy_filter = ""
    if strategy_id:
        strategy_filter = "AND i.strategy_id=?"
        params = (strategy_id,)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT o.order_key, o.exchange_order_id, o.status, o.submitted_at_utc,
                   o.updated_at_utc, i.strategy_id
              FROM orders o
              LEFT JOIN execution_intents i ON i.intent_key = o.intent_key
             WHERE o.submitted_at_utc IS NOT NULL
               AND LOWER(o.status) IN ('created', 'submitted', 'open', 'unfilled', 'partially_filled')
               {strategy_filter}
             ORDER BY o.updated_at_utc DESC
             LIMIT 500
            """,
            params,
        ).fetchall()
    ]
    active: list[dict[str, Any]] = []
    for row in rows:
        if _order_is_current(row, now_dt=now_dt):
            active.append(row)
    latest = max((_parse_utc(row.get("updated_at_utc")) for row in active), default=None)
    return {
        "active_live_order_count": len(active),
        "active_live_order_latest_updated_at_utc": None if latest is None else latest.isoformat(),
        "active_live_order_keys": [str(row.get("order_key") or "") for row in active[:20]],
        "active_live_order_strategies": sorted(
            {str(row.get("strategy_id") or "") for row in active if row.get("strategy_id")}
        ),
    }


def active_live_position_summary(conn: Any, *, strategy_id: str | None = None, now_dt: datetime | None = None) -> dict[str, Any]:
    """Return current live exposure from open positions, excluding stale closed-event residue."""

    if not table_exists(conn, "positions"):
        return {
            "active_live_position_count": 0,
            "active_live_strategy_count": 0,
            "active_live_open_cost_usd": 0.0,
            "active_live_latest_updated_at_utc": None,
            "active_live_position_keys": [],
            "active_live_strategies": [],
        }
    now_dt = now_dt or datetime.now(UTC)
    params: tuple[Any, ...] = ()
    strategy_filter = ""
    if strategy_id:
        strategy_filter = "AND p.strategy_id=?"
        params = (strategy_id,)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT p.position_key, p.strategy_id, p.event_token_key, p.cost_basis_usd,
                   p.status, p.opened_at_utc, p.updated_at_utc,
                   o.exchange_order_id, o.submitted_at_utc,
                   et.event_slug, e.event_start_time_utc, e.event_end_time_utc
              FROM positions p
              LEFT JOIN fills f ON p.position_key = ('position:' || f.fill_key)
              LEFT JOIN orders o ON o.order_key = f.order_key
              LEFT JOIN event_tokens et ON et.event_token_key = p.event_token_key
              LEFT JOIN events e ON e.event_key = et.event_key
             WHERE p.status='open' {strategy_filter}
             ORDER BY p.updated_at_utc DESC
             LIMIT 500
            """,
            params,
        ).fetchall()
    ]
    active: list[dict[str, Any]] = []
    for row in rows:
        if _position_is_current(row, now_dt=now_dt):
            active.append(row)
    latest = max((_parse_utc(row.get("updated_at_utc")) for row in active), default=None)
    strategies = sorted({str(row.get("strategy_id") or "") for row in active if row.get("strategy_id")})
    return {
        "active_live_position_count": len(active),
        "active_live_strategy_count": len(strategies),
        "active_live_open_cost_usd": round(sum(float(row.get("cost_basis_usd") or 0.0) for row in active), 8),
        "active_live_latest_updated_at_utc": None if latest is None else latest.isoformat(),
        "active_live_position_keys": [str(row.get("position_key") or "") for row in active[:20]],
        "active_live_strategies": strategies,
    }


def _live_ledger_loss_streak(conn: Any, *, strategy_id: str) -> int:
    rows = conn.execute(
        """
        SELECT realized_pnl_usd, completed_at_utc, updated_at_utc, inserted_at_utc
          FROM validation_budget_ledger
         WHERE strategy_or_component_id=?
           AND realized_pnl_usd IS NOT NULL
         ORDER BY COALESCE(completed_at_utc, updated_at_utc, inserted_at_utc) DESC
         LIMIT 20
        """,
        (strategy_id,),
    ).fetchall()
    streak = 0
    for row in rows:
        pnl = _first_float(row["realized_pnl_usd"])
        if pnl is None:
            continue
        if pnl < 0:
            streak += 1
            continue
        break
    return streak


def _position_is_current(row: dict[str, Any], *, now_dt: datetime) -> bool:
    if not _position_has_real_live_order(row):
        return False
    event_end = _parse_utc(row.get("event_end_time_utc")) or _event_end_from_slug(
        row.get("event_slug"), row.get("event_token_key")
    )
    if event_end is not None:
        return event_end >= now_dt - timedelta(minutes=5)
    updated_at = _parse_utc(row.get("updated_at_utc")) or _parse_utc(row.get("opened_at_utc"))
    if updated_at is None:
        return False
    return updated_at >= now_dt - timedelta(minutes=15)


def _position_has_real_live_order(row: dict[str, Any]) -> bool:
    exchange_order_id = str(row.get("exchange_order_id") or "")
    if not exchange_order_id or exchange_order_id.startswith(("shadow:", "dry_run:", "order:")):
        return False
    return _parse_utc(row.get("submitted_at_utc")) is not None


def _order_is_current(row: dict[str, Any], *, now_dt: datetime) -> bool:
    exchange_order_id = str(row.get("exchange_order_id") or "")
    if exchange_order_id.startswith(("shadow:", "dry_run:")):
        return False
    submitted_at = _parse_utc(row.get("submitted_at_utc"))
    if submitted_at is None:
        return False
    updated_at = _parse_utc(row.get("updated_at_utc")) or submitted_at
    return updated_at >= now_dt - timedelta(minutes=15)


def _event_end_from_slug(*values: Any) -> datetime | None:
    for value in values:
        text = str(value or "")
        match = re.search(r"updown-5m-(\d+)", text)
        if not match:
            continue
        try:
            return datetime.fromtimestamp(int(match.group(1)), tz=UTC) + timedelta(minutes=5)
        except (OSError, OverflowError, ValueError):
            continue
    return None


def _parse_utc(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _signal_gate(conn: Any, *, policy: StrategyPromotionPolicy) -> dict[str, Any]:
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


def _promotion_row_count(conn: Any) -> int:
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


def _current_economic_loss_streak(economics_rows: list[dict[str, Any]]) -> int:
    streak = 0
    for item in economics_rows:
        pnl = _first_float(item.get("pnl_usd"))
        win_rate = _first_float(item.get("win_rate"))
        win_count = _first_float(item.get("win_count"))
        loss_count = _first_float(item.get("loss_count"))
        is_loss = False
        if loss_count is not None and win_count is not None:
            is_loss = loss_count > 0 and win_count <= 0
        elif pnl is not None:
            is_loss = pnl < 0
        elif win_rate is not None:
            is_loss = win_rate <= 0.0
        if not is_loss:
            break
        streak += max(1, int(item.get("sample_count") or 1))
    return streak


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
