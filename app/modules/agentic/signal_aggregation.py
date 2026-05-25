from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.contracts import LiveSignal


AggregationDecisionType = Literal["monitor_only", "blocked", "strategy_plan_revision", "order_intent_candidate"]

_AGGREGATION_NAMESPACE = uuid.UUID("f34cf0ec-7b87-49e6-80d3-502f9873effc")
_ACTIONABLE_TYPES = {"buy", "sell", "rebuy", "reduce"}
_BUY_TYPES = {"buy", "rebuy"}
_SELL_TYPES = {"sell", "reduce"}


class LiveSignalAggregationControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_signal_sources: list[str] = Field(default_factory=list)
    cooldown_seconds: float = Field(default=90.0, ge=0.0)
    max_signal_age_seconds: float = Field(default=300.0, ge=0.0)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    event_cap_usd: float | None = Field(default=None, ge=0.0)
    allow_inventory_adding: bool = False


class LiveSignalAggregationInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_order_count: int = Field(default=0, ge=0)
    open_position_count: int = Field(default=0, ge=0)
    pending_intent_count: int = Field(default=0, ge=0)
    unresolved_inventory: bool = False
    current_exposure_notional_usd: float = Field(default=0.0, ge=0.0)

    @property
    def has_buy_blocking_exposure(self) -> bool:
        return (
            self.unresolved_inventory
            or self.open_order_count > 0
            or self.open_position_count > 0
            or self.pending_intent_count > 0
        )


class LiveSignalBlockerArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(min_length=1)
    signal_ids: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


class LiveSignalOrderIntentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    signal_type: str = Field(min_length=1)
    side: str | None = None
    market_id: str | None = None
    outcome_id: str | None = None
    market_token_id: str | None = None
    requested_shares: float | None = Field(default=None, ge=0.0)
    requested_notional_usd: float | None = Field(default=None, ge=0.0)
    max_price: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    supporting_signal_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)


class LiveSignalAggregationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "live_signal_aggregation_decision_v1"
    decision_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision_type: AggregationDecisionType
    selected_signal_ids: list[str] = Field(default_factory=list)
    suppressed_signal_ids: list[str] = Field(default_factory=list)
    blocker_artifacts: list[LiveSignalBlockerArtifact] = Field(default_factory=list)
    order_intent_candidates: list[LiveSignalOrderIntentCandidate] = Field(default_factory=list)
    confirming_signal_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    stale_signal_count: int = Field(default=0, ge=0)
    duplicate_signal_count: int = Field(default=0, ge=0)
    evidence_paths: list[str] = Field(default_factory=list)


def aggregate_live_signals(
    signals: list[LiveSignal],
    *,
    event_id: str,
    inventory: LiveSignalAggregationInventory | None = None,
    control: LiveSignalAggregationControl | None = None,
    generated_at_utc: datetime | None = None,
) -> LiveSignalAggregationDecision:
    now = _as_utc(generated_at_utc or datetime.now(timezone.utc))
    inventory = inventory or LiveSignalAggregationInventory()
    control = control or LiveSignalAggregationControl()
    event_signals = [signal for signal in signals if signal.event_id == event_id]

    blockers: list[LiveSignalBlockerArtifact] = []
    selected: list[LiveSignal] = []
    suppressed: list[LiveSignal] = []
    duplicate_count = 0
    stale_count = 0
    seen_keys: dict[tuple[Any, ...], LiveSignal] = {}

    for signal in event_signals:
        if control.enabled_signal_sources and signal.source not in set(control.enabled_signal_sources):
            suppressed.append(signal)
            blockers.append(_blocker("signal_source_disabled", [signal], {"source": signal.source}))
            continue
        if _is_stale(signal, now=now, max_age_seconds=control.max_signal_age_seconds):
            stale_count += 1
            suppressed.append(signal)
            blockers.append(_blocker("stale_signal", [signal], {"max_signal_age_seconds": control.max_signal_age_seconds}))
            continue
        if control.min_confidence is not None and signal.confidence is not None and signal.confidence < control.min_confidence:
            suppressed.append(signal)
            blockers.append(
                _blocker(
                    "signal_confidence_below_minimum",
                    [signal],
                    {"minimum": control.min_confidence, "confidence": signal.confidence},
                )
            )
            continue
        key = _dedupe_key(signal)
        previous = seen_keys.get(key)
        if previous is not None and _seconds_between(signal.emitted_at_utc, previous.emitted_at_utc) <= control.cooldown_seconds:
            duplicate_count += 1
            suppressed.append(signal)
            blockers.append(
                _blocker(
                    "duplicate_signal_cooldown",
                    [signal, previous],
                    {"cooldown_seconds": control.cooldown_seconds},
                )
            )
            continue
        seen_keys[key] = signal
        selected.append(signal)

    selected_ids = [signal.signal_id or signal.stable_signal_id() for signal in selected]
    suppressed_ids = [signal.signal_id or signal.stable_signal_id() for signal in suppressed]
    actionable = [signal for signal in selected if signal.signal_type in _ACTIONABLE_TYPES]
    candidate_group = _candidate_group(actionable)
    explicit_blocks = [signal for signal in selected if signal.signal_type == "block"]
    for block_signal in explicit_blocks:
        local_to_other_sleeve = _is_local_block_signal(block_signal, candidate_group)
        blockers.append(
            _blocker(
                "block_signal_present",
                [block_signal],
                {
                    "scope": "local_sleeve" if local_to_other_sleeve else "global",
                    "candidate_blocking": not local_to_other_sleeve,
                },
            )
        )

    conflict_groups = _conflict_groups(actionable)
    for group in conflict_groups:
        blockers.append(_blocker("conflicting_actionable_signals", group, {"conflict_key": _conflict_key(group[0])}))

    if candidate_group and candidate_group[0].signal_type in _BUY_TYPES and inventory.has_buy_blocking_exposure and not control.allow_inventory_adding:
        blockers.append(
            _blocker(
                "duplicate_exposure_risk",
                candidate_group,
                inventory.model_dump(mode="json"),
            )
        )

    candidate = _order_candidate(candidate_group) if candidate_group else None
    if candidate is not None and control.event_cap_usd is not None:
        requested_notional = candidate.requested_notional_usd or 0.0
        if requested_notional <= 0.0 and candidate.requested_shares is not None and candidate.max_price is not None:
            requested_notional = candidate.requested_shares * candidate.max_price
        if inventory.current_exposure_notional_usd + requested_notional > control.event_cap_usd:
            blockers.append(
                _blocker(
                    "event_budget_exceeded",
                    candidate_group,
                    {
                        "event_cap_usd": control.event_cap_usd,
                        "current_exposure_notional_usd": inventory.current_exposure_notional_usd,
                        "requested_notional_usd": requested_notional,
                    },
                )
            )

    blocker_artifacts = _dedupe_blockers(blockers)
    order_candidates: list[LiveSignalOrderIntentCandidate] = []
    candidate_blockers = [blocker for blocker in blocker_artifacts if _candidate_blocking(blocker)]
    if candidate is not None and not candidate_blockers and not conflict_groups:
        order_candidates.append(candidate)

    if candidate_blockers:
        decision_type: AggregationDecisionType = "blocked"
    elif order_candidates:
        decision_type = "order_intent_candidate"
    elif blocker_artifacts:
        decision_type = "blocked"
    else:
        decision_type = "monitor_only"

    evidence_paths = _unique_strings([path for signal in selected for path in signal.evidence_paths])
    decision = LiveSignalAggregationDecision(
        decision_id=_stable_decision_id(event_id, selected_ids, suppressed_ids, blocker_artifacts, now),
        event_id=event_id,
        generated_at_utc=now,
        decision_type=decision_type,
        selected_signal_ids=selected_ids,
        suppressed_signal_ids=suppressed_ids,
        blocker_artifacts=blocker_artifacts,
        order_intent_candidates=order_candidates,
        confirming_signal_count=len(candidate_group),
        conflict_count=len(conflict_groups),
        stale_signal_count=stale_count,
        duplicate_signal_count=duplicate_count,
        evidence_paths=evidence_paths,
    )
    return decision


def _order_candidate(signals: list[LiveSignal]) -> LiveSignalOrderIntentCandidate | None:
    if not signals:
        return None
    strongest = sorted(signals, key=lambda signal: signal.confidence or 0.0, reverse=True)[0]
    risk = strongest.risk_request
    requested_shares = risk.requested_shares if risk is not None else None
    requested_notional_usd = risk.requested_notional_usd if risk is not None else None
    max_price = risk.max_price if risk is not None else None
    reason_codes = _unique_strings([reason for signal in signals for reason in signal.reason_codes])
    evidence_paths = _unique_strings([path for signal in signals for path in signal.evidence_paths])
    signal_ids = [signal.signal_id or signal.stable_signal_id() for signal in signals]
    return LiveSignalOrderIntentCandidate(
        event_id=strongest.event_id,
        signal_type=strongest.signal_type,
        side=strongest.side,
        market_id=strongest.market_id,
        outcome_id=strongest.outcome_id,
        market_token_id=strongest.market_token_id,
        requested_shares=requested_shares,
        requested_notional_usd=requested_notional_usd,
        max_price=max_price,
        confidence=strongest.confidence,
        supporting_signal_ids=signal_ids,
        reason_codes=reason_codes,
        evidence_paths=evidence_paths,
    )


def _candidate_group(actionable: list[LiveSignal]) -> list[LiveSignal]:
    if not actionable:
        return []
    groups: dict[tuple[Any, ...], list[LiveSignal]] = {}
    for signal in actionable:
        groups.setdefault(_action_key(signal), []).append(signal)
    return sorted(groups.values(), key=lambda group: (len(group), max(signal.confidence or 0.0 for signal in group)), reverse=True)[0]


def _is_local_block_signal(signal: LiveSignal, candidate_group: list[LiveSignal]) -> bool:
    if not candidate_group:
        return False
    payload = signal.payload if isinstance(signal.payload, dict) else {}
    scope = str(payload.get("aggregation_scope") or payload.get("scope") or "").strip().lower()
    if scope not in {"local", "sleeve", "strategy", "local_sleeve"}:
        return False
    block_sleeve = _signal_sleeve_id(signal)
    if not block_sleeve:
        return False
    candidate_sleeves = {_signal_sleeve_id(candidate) for candidate in candidate_group}
    candidate_sleeves.discard(None)
    return bool(candidate_sleeves) and block_sleeve not in candidate_sleeves


def _signal_sleeve_id(signal: LiveSignal) -> str | None:
    if signal.risk_request is not None and signal.risk_request.sleeve_id:
        return _norm(signal.risk_request.sleeve_id)
    payload = signal.payload if isinstance(signal.payload, dict) else {}
    return _norm(str(payload.get("sleeve_id") or payload.get("strategy_id") or ""))


def _candidate_blocking(blocker: LiveSignalBlockerArtifact) -> bool:
    return blocker.detail.get("candidate_blocking") is not False


def _conflict_groups(actionable: list[LiveSignal]) -> list[list[LiveSignal]]:
    if len(actionable) < 2:
        return []
    groups: dict[tuple[Any, ...], list[LiveSignal]] = {}
    for signal in actionable:
        groups.setdefault(_action_key(signal), []).append(signal)
    if len(groups) <= 1:
        return []
    buy_groups = [group for key, group in groups.items() if key[0] in _BUY_TYPES]
    sell_groups = [group for key, group in groups.items() if key[0] in _SELL_TYPES]
    if buy_groups and sell_groups:
        return [signal_group for signal_group in groups.values()]
    if len(buy_groups) > 1:
        return buy_groups
    if len(sell_groups) > 1:
        return sell_groups
    return []


def _action_key(signal: LiveSignal) -> tuple[Any, ...]:
    return (
        signal.signal_type,
        _norm(signal.side),
        signal.market_token_id,
        signal.outcome_id,
    )


def _conflict_key(signal: LiveSignal) -> str:
    return "|".join(str(part or "") for part in _action_key(signal))


def _dedupe_key(signal: LiveSignal) -> tuple[Any, ...]:
    band = signal.price_band
    return (
        signal.source,
        signal.event_id,
        signal.signal_type,
        _norm(signal.side),
        signal.market_token_id,
        signal.outcome_id,
        band.current_price if band is not None else None,
        band.lower_price if band is not None else None,
        band.upper_price if band is not None else None,
        signal.risk_request.sleeve_id if signal.risk_request is not None else None,
    )


def _is_stale(signal: LiveSignal, *, now: datetime, max_age_seconds: float) -> bool:
    if signal.freshness.stale:
        return True
    source_time = signal.freshness.source_timestamp_utc or signal.freshness.observed_at_utc or signal.emitted_at_utc
    return max_age_seconds > 0.0 and (now - _as_utc(source_time)).total_seconds() > max_age_seconds


def _blocker(reason_code: str, signals: list[LiveSignal], detail: dict[str, Any]) -> LiveSignalBlockerArtifact:
    return LiveSignalBlockerArtifact(
        reason_code=reason_code,
        signal_ids=[signal.signal_id or signal.stable_signal_id() for signal in signals],
        evidence_paths=_unique_strings([path for signal in signals for path in signal.evidence_paths]),
        detail=detail,
    )


def _dedupe_blockers(blockers: list[LiveSignalBlockerArtifact]) -> list[LiveSignalBlockerArtifact]:
    by_key: dict[tuple[str, tuple[str, ...]], LiveSignalBlockerArtifact] = {}
    for blocker in blockers:
        key = (blocker.reason_code, tuple(sorted(blocker.signal_ids)))
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = blocker
            continue
        existing.evidence_paths = _unique_strings([*existing.evidence_paths, *blocker.evidence_paths])
        existing.detail = {**existing.detail, **blocker.detail}
    return list(by_key.values())


def _stable_decision_id(
    event_id: str,
    selected_signal_ids: list[str],
    suppressed_signal_ids: list[str],
    blockers: list[LiveSignalBlockerArtifact],
    generated_at_utc: datetime,
) -> str:
    identity = "|".join(
        [
            event_id,
            generated_at_utc.isoformat(),
            ",".join(selected_signal_ids),
            ",".join(suppressed_signal_ids),
            ",".join(f"{blocker.reason_code}:{','.join(blocker.signal_ids)}" for blocker in blockers),
        ]
    )
    return f"lsigagg-{uuid.uuid5(_AGGREGATION_NAMESPACE, identity)}"


def _seconds_between(left: datetime, right: datetime) -> float:
    return abs((_as_utc(left) - _as_utc(right)).total_seconds())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _norm(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


__all__ = [
    "AggregationDecisionType",
    "LiveSignalAggregationControl",
    "LiveSignalAggregationDecision",
    "LiveSignalAggregationInventory",
    "LiveSignalBlockerArtifact",
    "LiveSignalOrderIntentCandidate",
    "aggregate_live_signals",
]
