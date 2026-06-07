from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    budget_usd: float = 50.0
    max_active_cost_usd: float = 50.0
    max_event_exposure_usd: float = 20.0
    max_same_side_exposure_usd: float = 15.0
    max_correlated_exposure_usd: float = 30.0
    max_spread: float = 0.08
    max_quote_age_seconds: float = 5.0
    max_profile_age_seconds: float = 300.0
    max_signal_age_seconds: float = 60.0
    max_slippage: float = 0.04
    min_liquidity_depth: float = 1.0
    allow_final_minute_entries: bool = False


@dataclass(frozen=True)
class CandidateRiskContext:
    notional_usd: float
    active_cost_usd: float
    event_exposure_usd: float
    same_side_exposure_usd: float
    correlated_exposure_usd: float
    spread: float
    quote_age_seconds: float
    profile_age_seconds: float
    signal_age_seconds: float
    expected_slippage: float
    liquidity_depth: float
    time_remaining_seconds: float
    has_event_token_key: bool = True
    has_lifecycle_coverage: bool = True


@dataclass(frozen=True)
class RiskGateResult:
    passed: bool
    blockers: tuple[str, ...]
    details: dict[str, float | bool]
    orders_allowed: bool = False
    live_trading_authorized: bool = False


def evaluate_risk_gates(context: CandidateRiskContext, limits: RiskLimits) -> RiskGateResult:
    blockers: list[str] = []
    if not context.has_event_token_key:
        blockers.append("missing_event_token_key")
    if not context.has_lifecycle_coverage:
        blockers.append("missing_lifecycle_coverage")
    if context.active_cost_usd + context.notional_usd > limits.max_active_cost_usd:
        blockers.append("active_cost_cap")
    if context.event_exposure_usd + context.notional_usd > limits.max_event_exposure_usd:
        blockers.append("event_exposure_cap")
    if context.same_side_exposure_usd + context.notional_usd > limits.max_same_side_exposure_usd:
        blockers.append("same_side_exposure_cap")
    if context.correlated_exposure_usd + context.notional_usd > limits.max_correlated_exposure_usd:
        blockers.append("correlated_exposure_cap")
    if context.spread > limits.max_spread:
        blockers.append("spread_too_wide")
    if context.quote_age_seconds > limits.max_quote_age_seconds:
        blockers.append("stale_quote")
    if context.profile_age_seconds > limits.max_profile_age_seconds:
        blockers.append("stale_profile_data")
    if context.signal_age_seconds > limits.max_signal_age_seconds:
        blockers.append("stale_signal_data")
    if context.expected_slippage > limits.max_slippage:
        blockers.append("slippage_too_high")
    if context.liquidity_depth < limits.min_liquidity_depth:
        blockers.append("insufficient_liquidity")
    if context.time_remaining_seconds < 60 and not limits.allow_final_minute_entries:
        blockers.append("final_minute_entry_block")
    return RiskGateResult(
        passed=not blockers,
        blockers=tuple(blockers),
        details={
            "notional_usd": context.notional_usd,
            "active_cost_usd": context.active_cost_usd,
            "event_exposure_usd": context.event_exposure_usd,
            "same_side_exposure_usd": context.same_side_exposure_usd,
            "correlated_exposure_usd": context.correlated_exposure_usd,
            "spread": context.spread,
            "quote_age_seconds": context.quote_age_seconds,
            "signal_age_seconds": context.signal_age_seconds,
            "time_remaining_seconds": context.time_remaining_seconds,
        },
    )
