from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.contracts import (
    LiveSignal,
    LiveSignalFreshness,
    LiveSignalPriceBand,
    LiveSignalRiskRequest,
)


SCHEMA_VERSION = "sports_live_reduce_stop_lifecycle_evidence_v1"

_EXIT_STATES = {
    "stop_triggered",
    "q4_endgame_loss_mode",
    "adverse_thesis_failed",
    "stale_target_exit",
    "near_final_loss_cleanup",
}
_FINAL_STATES = {"final_cleanup"}


def build_reduce_stop_lifecycle_evidence(
    *,
    event_id: str,
    plan: dict[str, Any],
    market_state: dict[str, Any],
    portfolio_state: dict[str, Any],
    direct_clob: dict[str, Any],
    min_size: float = 5.0,
) -> dict[str, Any]:
    """Build deterministic reduce/stop lifecycle evidence.

    This module is deliberately evidence-only. It can emit reduce candidates for
    the normal Janus StrategyPlan/live-worker/order-management gates, but it
    never submits orders directly.
    """

    now = datetime.now(timezone.utc)
    scenario = _game_scenario(market_state)
    snapshot = _classification_snapshot(market_state)
    final_state = _final_state(scenario=scenario, snapshot=snapshot, market_state=market_state)
    q4_loss_mode = _q4_loss_mode(scenario=scenario, snapshot=snapshot)
    target_rows = _target_rows(portfolio_state=portfolio_state, market_state=market_state)
    direct_positions = _direct_positions_by_token(direct_clob)
    rows: list[dict[str, Any]] = []

    for strategy in _strategies(plan):
        token_id = strategy["token_id"]
        if not token_id:
            continue
        target = target_rows.get(strategy["sleeve_id"]) or target_rows.get(token_id) or {}
        position = direct_positions.get(token_id) or {}
        shares = _first_float(
            target,
            ("allocated_shares", "shares", "position_size", "size"),
        )
        if shares is None:
            shares = _first_float(position, ("shares", "size"))
        shares = shares or 0.0
        basis = _first_float(target, ("weighted_basis_price", "basis_price", "avg_price", "average_price"))
        if basis is None:
            basis = _first_float(position, ("price", "avg_price", "average_price"))
        current_price = _current_exit_price(market_state=market_state, token_id=token_id, outcome_id=strategy["outcome_id"])
        target_status = str(target.get("target_status") or "").strip().lower()
        target_price = _first_float(target, ("target_price", "target", "exit_price"))
        stop_threshold = _stop_threshold(strategy=strategy, basis=basis)
        near_final_loss_cleanup = _near_final_loss_cleanup(
            snapshot=snapshot,
            scenario=scenario,
            current_price=current_price,
            basis=basis,
        )
        adverse = _adverse_thesis_failed(
            current_price=current_price,
            basis=basis,
            stop_threshold=stop_threshold,
            scenario=scenario,
            q4_loss_mode=q4_loss_mode,
        )
        state, reason_codes = _state_and_reasons(
            shares=shares,
            min_size=min_size,
            current_price=current_price,
            stop_threshold=stop_threshold,
            target_status=target_status,
            final_state=final_state,
            near_final_loss_cleanup=near_final_loss_cleanup,
            q4_loss_mode=q4_loss_mode,
            adverse=adverse,
        )
        reduce_signal = state in _EXIT_STATES and shares >= min_size and current_price is not None and not final_state
        rows.append(
            {
                "event_id": event_id,
                "sleeve_id": strategy["sleeve_id"],
                "sleeve_role": strategy["sleeve_role"],
                "sleeve_group": strategy["sleeve_group"],
                "strategy_id": strategy["strategy_id"],
                "strategy_family": strategy["strategy_family"],
                "outcome_id": strategy["outcome_id"],
                "outcome_label": strategy["outcome_label"],
                "market_token_id": token_id,
                "shares": round(shares, 6),
                "basis_price": _round_price(basis),
                "current_exit_price": _round_price(current_price),
                "target_price": _round_price(target_price),
                "target_status": target_status or None,
                "stop_threshold_price": _round_price(stop_threshold),
                "state": state,
                "active_reduce_signal": reduce_signal,
                "rebuy_allowed": not adverse and not final_state,
                "reason_codes": reason_codes,
                "source_confidence": "account_confirmed" if position else "runtime_artifact",
                "lifecycle_policy": {
                    "stop_rules": strategy["stop_rules"],
                    "exit_rules": strategy["exit_rules"],
                    "q4_endgame_loss_mode": q4_loss_mode,
                    "final_state_cleanup_only": final_state,
                    "near_final_loss_cleanup": near_final_loss_cleanup,
                    "adverse_thesis_failed": adverse,
                },
            }
        )

    active_rows = [row for row in rows if row["active_reduce_signal"]]
    final_rows = [row for row in rows if row["state"] in _FINAL_STATES]
    near_final_rows = [row for row in rows if row["state"] == "near_final_loss_cleanup"]
    adverse_rows = [row for row in rows if row["state"] == "adverse_thesis_failed"]
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "generated_at_utc": now.isoformat(),
        "execution_boundary": "evidence_only",
        "source_confidence": "runtime_artifact",
        "game_scenario": scenario,
        "classification_snapshot": snapshot,
        "row_count": len(rows),
        "active_reduce_signal_count": len(active_rows),
        "final_cleanup_count": len(final_rows),
        "near_final_loss_cleanup_count": len(near_final_rows),
        "adverse_thesis_failure_count": len(adverse_rows),
        "rows": rows,
        "notes": [
            "Reduce/stop lifecycle is deterministic app-owned evidence.",
            "Settled final-state rows are cleanup/reconciliation evidence and do not emit new sell targets.",
            "Near-final live losing positions may emit Janus-gated reduce candidates while a CLOB exit is still valid.",
            "All reduce signals still require Janus StrategyPlan/live-worker/order-management gates.",
        ],
    }


def live_signals_from_reduce_stop_lifecycle(evidence: dict[str, Any] | None) -> list[LiveSignal]:
    if not isinstance(evidence, dict):
        return []
    event_id = str(evidence.get("event_id") or "").strip()
    if not event_id:
        return []
    now = _parse_datetime(evidence.get("generated_at_utc")) or datetime.now(timezone.utc)
    signals: list[LiveSignal] = []
    for row in evidence.get("rows") or []:
        if not isinstance(row, dict) or row.get("active_reduce_signal") is not True:
            continue
        token_id = _clean(row.get("market_token_id"))
        outcome_id = _clean(row.get("outcome_id"))
        price = _float(row.get("current_exit_price"))
        shares = _float(row.get("shares"))
        if not token_id or not outcome_id or price is None or shares is None or shares <= 0.0:
            continue
        signals.append(
            LiveSignal(
                event_id=event_id,
                market_id=_clean(row.get("market_id")),
                outcome_id=outcome_id,
                market_token_id=token_id,
                source="deterministic",
                signal_type="reduce",
                side=_clean(row.get("outcome_label")),
                emitted_at_utc=now,
                price_band=LiveSignalPriceBand(
                    current_price=price,
                    target_price=price,
                    band_role="reduce_stop_lifecycle_exit",
                ),
                confidence=0.91,
                confidence_source="reduce_stop_lifecycle",
                freshness=LiveSignalFreshness(source_timestamp_utc=now, stale=False),
                reason_codes=[str(reason) for reason in row.get("reason_codes") or []],
                risk_request=LiveSignalRiskRequest(
                    sleeve_id=_clean(row.get("sleeve_id")),
                    sleeve_role=_clean(row.get("sleeve_role")),
                    requested_shares=shares,
                    max_price=price,
                ),
                payload={
                    "schema_version": "reduce_stop_lifecycle_signal_v1",
                    "strategy_id": _clean(row.get("strategy_id")),
                    "strategy_family": _clean(row.get("strategy_family")),
                    "sleeve_id": _clean(row.get("sleeve_id")),
                    "sleeve_group": _clean(row.get("sleeve_group")),
                    "sleeve_role": _clean(row.get("sleeve_role")),
                    "cycle_id": _clean(row.get("sleeve_id")),
                    "trigger_type": _clean(row.get("state")) or "reduce_stop_lifecycle",
                    "trigger_source": "reduce_stop_lifecycle",
                    "lifecycle_policy": row.get("lifecycle_policy") if isinstance(row.get("lifecycle_policy"), dict) else {},
                    "game_scenario": evidence.get("game_scenario") if isinstance(evidence.get("game_scenario"), dict) else {},
                    "reduce_stop_lifecycle_row": row,
                },
            )
        )
    return signals


def _strategies(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        strategy_id = str(strategy.get("strategy_id") or "").strip()
        token_id = str(entry_rules.get("token_id") or entry_rules.get("asset_id") or "").strip()
        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_family": str(strategy.get("family") or "").strip() or "unknown",
                "sleeve_id": str(strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id).strip()
                or strategy_id,
                "sleeve_group": _clean(strategy.get("sleeve_group") or entry_rules.get("sleeve_group")),
                "sleeve_role": _clean(strategy.get("sleeve_role") or entry_rules.get("sleeve_role")),
                "outcome_id": str(entry_rules.get("outcome_id") or "").strip() or None,
                "outcome_label": str(strategy.get("side") or entry_rules.get("outcome_label") or "").strip() or None,
                "token_id": token_id,
                "entry_rules": entry_rules,
                "exit_rules": strategy.get("exit_rules") if isinstance(strategy.get("exit_rules"), dict) else {},
                "stop_rules": strategy.get("stop_rules") if isinstance(strategy.get("stop_rules"), dict) else {},
            }
        )
    return rows


def _target_rows(*, portfolio_state: dict[str, Any], market_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence = portfolio_state.get("target_management")
    if not isinstance(evidence, dict):
        evidence = market_state.get("target_management")
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(evidence, dict):
        return rows
    for row in evidence.get("sleeves") or []:
        if not isinstance(row, dict):
            continue
        sleeve_id = str(row.get("sleeve_id") or "").strip()
        token_id = str(row.get("token_id") or row.get("market_token_id") or "").strip()
        if sleeve_id:
            rows[sleeve_id] = row
        if token_id and token_id not in rows:
            rows[token_id] = row
    return rows


def _direct_positions_by_token(direct_clob: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for position in ((direct_clob.get("open_positions") or {}).get("positions") or []):
        if not isinstance(position, dict):
            continue
        token_id = str(position.get("asset") or position.get("asset_id") or position.get("token_id") or "").strip()
        if not token_id:
            continue
        rows[token_id] = {
            "shares": _first_float(position, ("size", "shares", "balance")),
            "price": _first_float(position, ("avg_price", "average_price", "price")),
            "outcome_label": position.get("outcome") or position.get("outcome_label"),
        }
    return rows


def _current_exit_price(*, market_state: dict[str, Any], token_id: str | None, outcome_id: str | None) -> float | None:
    state: dict[str, Any] = {}
    for bucket, key in (
        ("token_states", token_id),
        ("token_market_states", token_id),
        ("outcome_states", outcome_id),
        ("outcome_market_states", outcome_id),
    ):
        values = market_state.get(bucket)
        if isinstance(values, dict) and key and isinstance(values.get(key), dict):
            state.update(values[key])
    return _first_float(state, ("best_bid", "bid", "current_bid", "price", "current_price", "mid_price"))


def _stop_threshold(*, strategy: dict[str, Any], basis: float | None) -> float | None:
    stop_rules = strategy["stop_rules"]
    entry_rules = strategy["entry_rules"]
    explicit = _first_float(stop_rules, ("stop_price", "exit_threshold", "stop_loss"))
    if explicit is None:
        explicit = _first_float(entry_rules, ("stop_price", "exit_threshold", "stop_loss"))
    if explicit is not None:
        return _normalize_price(explicit)
    if basis is None:
        return None
    adverse_cents = _first_float(stop_rules, ("max_adverse_cents", "max_loss_cents"))
    if adverse_cents is None:
        adverse_cents = _first_float(entry_rules, ("max_adverse_cents", "max_loss_cents"))
    if adverse_cents is None:
        return None
    return _normalize_price(basis - adverse_cents / 100.0)


def _state_and_reasons(
    *,
    shares: float,
    min_size: float,
    current_price: float | None,
    stop_threshold: float | None,
    target_status: str,
    final_state: bool,
    near_final_loss_cleanup: bool,
    q4_loss_mode: bool,
    adverse: bool,
) -> tuple[str, list[str]]:
    if shares <= 1e-9:
        return "no_position", ["no_direct_position_for_sleeve"]
    if final_state:
        return "final_cleanup", ["final_cleanup_required", "no_new_targets_after_final"]
    if shares < min_size:
        return "below_minimum_reduce_size", ["position_below_minimum_reduce_size"]
    if near_final_loss_cleanup:
        return "near_final_loss_cleanup", [
            "near_final_loss_cleanup",
            "rebuy_blocked_adverse_thesis_failed",
        ]
    if current_price is not None and stop_threshold is not None and current_price <= stop_threshold + 1e-9:
        return "stop_triggered", ["reduce_stop_triggered", "rebuy_blocked_adverse_thesis_failed"]
    if q4_loss_mode and adverse:
        return "q4_endgame_loss_mode", ["q4_endgame_loss_mode", "rebuy_blocked_adverse_thesis_failed"]
    if adverse:
        return "adverse_thesis_failed", ["adverse_thesis_failed", "rebuy_blocked_adverse_thesis_failed"]
    if target_status in {"target_missing", "target_stale"}:
        return "stale_target_exit", ["target_uncovered_reduce_review"]
    return "normal_target_management", ["normal_target_management"]


def _near_final_loss_cleanup(
    *,
    snapshot: dict[str, Any],
    scenario: dict[str, Any],
    current_price: float | None,
    basis: float | None,
) -> bool:
    period = _safe_int(snapshot.get("period") or snapshot.get("quarter")) or 0
    clock_seconds = _first_float(snapshot, ("clock_seconds_remaining", "remaining_seconds", "game_clock_seconds"))
    labels = {str(label).lower() for label in scenario.get("labels") or []}
    if period < 4:
        return False
    if clock_seconds is not None and clock_seconds > 180:
        return False
    if current_price is None:
        return False
    if basis is not None and current_price < basis:
        return True
    return current_price <= 0.05 or "garbage_time_or_falling_knife" in labels


def _adverse_thesis_failed(
    *,
    current_price: float | None,
    basis: float | None,
    stop_threshold: float | None,
    scenario: dict[str, Any],
    q4_loss_mode: bool,
) -> bool:
    labels = {str(label).lower() for label in scenario.get("labels") or []}
    if "adverse_thesis_failed" in labels or "garbage_time_or_falling_knife" in labels or "final_state" in labels:
        return True
    if current_price is not None and stop_threshold is not None and current_price <= stop_threshold + 1e-9:
        return True
    if q4_loss_mode and current_price is not None and basis is not None and current_price < basis:
        return True
    return False


def _game_scenario(market_state: dict[str, Any]) -> dict[str, Any]:
    context = market_state.get("live_game_context") if isinstance(market_state.get("live_game_context"), dict) else {}
    scenario = context.get("game_scenario") if isinstance(context.get("game_scenario"), dict) else {}
    return scenario


def _classification_snapshot(market_state: dict[str, Any]) -> dict[str, Any]:
    context = market_state.get("live_game_context") if isinstance(market_state.get("live_game_context"), dict) else {}
    snapshot = context.get("classification_snapshot") if isinstance(context.get("classification_snapshot"), dict) else {}
    if snapshot:
        return snapshot
    game = market_state.get("game") if isinstance(market_state.get("game"), dict) else {}
    live_state = market_state.get("live_state") if isinstance(market_state.get("live_state"), dict) else {}
    return {**game, **live_state}


def _final_state(*, scenario: dict[str, Any], snapshot: dict[str, Any], market_state: dict[str, Any]) -> bool:
    labels = {str(label).lower() for label in scenario.get("labels") or []}
    if "final_state" in labels:
        return True
    values = [snapshot, market_state.get("game") if isinstance(market_state.get("game"), dict) else {}]
    for value in values:
        if bool(value.get("final") or value.get("game_final") or value.get("is_final")):
            return True
        status_text = str(value.get("status") or value.get("game_status") or "").strip().lower()
        if status_text in {"final", "closed", "completed", "ended"}:
            return True
    return False


def _q4_loss_mode(*, scenario: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    period = _safe_int(snapshot.get("period") or snapshot.get("quarter")) or 0
    clock_seconds = _first_float(snapshot, ("clock_seconds_remaining", "remaining_seconds", "game_clock_seconds"))
    score_gap = abs(_first_float(snapshot, ("score_gap", "score_margin", "score_diff")) or 0.0)
    labels = {str(label).lower() for label in scenario.get("labels") or []}
    if "garbage_time_or_falling_knife" in labels:
        return True
    if period >= 4 and score_gap >= 8:
        return True
    if period >= 4 and clock_seconds is not None and clock_seconds <= 360 and score_gap >= 5:
        return True
    return False


def _first_float(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float(mapping.get(key))
        if value is not None:
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_price(value: float) -> float:
    return min(0.99, max(0.01, round(float(value), 4)))


def _round_price(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "SCHEMA_VERSION",
    "build_reduce_stop_lifecycle_evidence",
    "live_signals_from_reduce_stop_lifecycle",
]
