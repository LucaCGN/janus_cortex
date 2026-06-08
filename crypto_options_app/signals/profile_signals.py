from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProfilePressureGateResult:
    executable: bool
    blockers: tuple[str, ...]
    support_weight: float
    conflict_weight: float
    max_signal_age_seconds: float | None
    signal_count: int


def evaluate_profile_pressure_gate(
    row: dict[str, Any],
    *,
    max_signal_age_seconds: float = 180.0,
    max_conflict_ratio: float = 0.34,
    min_support_weight: float = 1.0,
) -> ProfilePressureGateResult:
    """Classify a profile-pressure row as executable or observation-only."""

    signals = [signal for signal in row.get("supporting_signals") or [] if isinstance(signal, dict)]
    support_weight = _float(row.get("support_weight"))
    if support_weight <= 0:
        support_weight = float(len(signals))
    conflict_weight = _float(row.get("conflict_weight"))
    ages = [_float_or_none(signal.get("age_seconds")) for signal in signals]
    ages = [age for age in ages if age is not None]
    max_age = max(ages) if ages else _float_or_none(row.get("profile_age_seconds") or row.get("signal_age_seconds"))
    blockers: list[str] = []
    row_blockers = [str(blocker) for blocker in row.get("blockers") or [] if str(blocker).strip()]
    if row_blockers:
        blockers.append("profile_pressure_row_has_blockers")
    if not signals:
        blockers.append("profile_pressure_no_supporting_signals")
    if support_weight < min_support_weight:
        blockers.append("profile_pressure_support_too_low")
    conflict_ratio = conflict_weight / max(support_weight + conflict_weight, 1.0)
    if conflict_ratio > max_conflict_ratio:
        blockers.append("profile_pressure_conflict_too_high")
    if max_age is not None and max_age > max_signal_age_seconds:
        blockers.append("profile_pressure_stale")
    return ProfilePressureGateResult(
        executable=not blockers,
        blockers=tuple(blockers),
        support_weight=support_weight,
        conflict_weight=conflict_weight,
        max_signal_age_seconds=max_age,
        signal_count=len(signals),
    )


def split_observation_and_executable_profile_rows(
    rows: list[dict[str, Any]],
    *,
    max_signal_age_seconds: float = 180.0,
    max_conflict_ratio: float = 0.34,
    min_support_weight: float = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observation_rows: list[dict[str, Any]] = []
    executable_rows: list[dict[str, Any]] = []
    for row in rows:
        gate = evaluate_profile_pressure_gate(
            row,
            max_signal_age_seconds=max_signal_age_seconds,
            max_conflict_ratio=max_conflict_ratio,
            min_support_weight=min_support_weight,
        )
        decorated = dict(row)
        decorated["profile_pressure_gate"] = {
            "executable": gate.executable,
            "blockers": list(gate.blockers),
            "support_weight": gate.support_weight,
            "conflict_weight": gate.conflict_weight,
            "max_signal_age_seconds": gate.max_signal_age_seconds,
            "signal_count": gate.signal_count,
        }
        observation_rows.append(decorated)
        if gate.executable:
            executable_rows.append(decorated)
    return observation_rows, executable_rows


def profile_pressure_blockers(row: dict[str, Any]) -> tuple[str, ...]:
    gate = row.get("profile_pressure_gate")
    if not isinstance(gate, dict):
        return ()
    return tuple(str(blocker) for blocker in gate.get("blockers") or [])


def _float(value: Any) -> float:
    parsed = _float_or_none(value)
    return 0.0 if parsed is None else parsed


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
