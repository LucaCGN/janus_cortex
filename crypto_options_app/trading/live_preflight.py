from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig, executor_boundary_ready
from crypto_options_app.trading.live_candidates import LiveMarketCandidateVerification


LIVE_EXECUTE_FLAG = "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE"
LIVE_APPROVED_FLAG = "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED"
LIVE_RISK_ACK_FLAG = "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK"
DEFAULT_VALIDATION_BUDGET_CAP_USD = 50.0
DEFAULT_CASH_BALANCE_HARD_STOP_USD = 100.0


@dataclass(frozen=True)
class LiveEnvironmentFlags:
    live_execute: bool
    execution_approved: bool
    risk_acknowledged: bool

    @property
    def ready(self) -> bool:
        return self.live_execute and self.execution_approved and self.risk_acknowledged

    @property
    def missing(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.live_execute:
            missing.append(LIVE_EXECUTE_FLAG)
        if not self.execution_approved:
            missing.append(LIVE_APPROVED_FLAG)
        if not self.risk_acknowledged:
            missing.append(LIVE_RISK_ACK_FLAG)
        return tuple(missing)


@dataclass(frozen=True)
class TrustedCashBalanceSnapshot:
    balance_usd: float
    source: str = "trusted_balance_source"


@dataclass(frozen=True)
class SupervisedLivePreflight:
    status: str
    blockers: tuple[str, ...]
    env_flags: LiveEnvironmentFlags
    credentials_ready: bool
    live_market_verified: bool
    executor_boundary_ready: bool
    supervised_executor_bound: bool
    live_submission_permitted: bool
    validation_budget_cap_usd: float
    validation_budget_spent_usd: float
    remaining_validation_budget_usd: float
    projected_order_notional_usd: float
    cash_balance_hard_stop_usd: float
    cash_balance_status: str
    cash_balance_before_usd: float | None
    cash_balance_after_projected_usd: float | None
    manual_orders_avoided: bool = True


def read_live_environment_flags(env: Mapping[str, str] | None = None) -> LiveEnvironmentFlags:
    values = env or os.environ
    return LiveEnvironmentFlags(
        live_execute=values.get(LIVE_EXECUTE_FLAG) == "1",
        execution_approved=values.get(LIVE_APPROVED_FLAG) == "1",
        risk_acknowledged=values.get(LIVE_RISK_ACK_FLAG) == "1",
    )


def evaluate_supervised_live_preflight(
    *,
    executor_boundary: ExecutorBoundaryConfig,
    candidate_verification: LiveMarketCandidateVerification | None,
    supervised_executor_bound: bool,
    credentials_ready: bool,
    live_cadence_count: int = 1,
    env_flags: LiveEnvironmentFlags | None = None,
    cash_balance_snapshot: TrustedCashBalanceSnapshot | None = None,
    validation_budget_spent_usd: float = 0.0,
    validation_budget_cap_usd: float = DEFAULT_VALIDATION_BUDGET_CAP_USD,
    cash_balance_hard_stop_usd: float = DEFAULT_CASH_BALANCE_HARD_STOP_USD,
    projected_order_notional_usd: float = 0.0,
) -> SupervisedLivePreflight:
    flags = env_flags or read_live_environment_flags()
    boundary_ready = executor_boundary_ready(executor_boundary)
    budget_cap = max(0.0, float(validation_budget_cap_usd))
    budget_spent = max(0.0, float(validation_budget_spent_usd))
    projected_notional = max(0.0, float(projected_order_notional_usd))
    remaining_budget = max(0.0, budget_cap - budget_spent)
    blockers: list[str] = []
    if live_cadence_count != 1:
        blockers.append("duplicate_cadence")
    if not boundary_ready:
        blockers.append("executor_boundary_not_ready")
    if not flags.ready:
        blockers.append("env_live_flags_missing")
        blockers.extend(f"env_flag_missing:{name}" for name in flags.missing)
    if not credentials_ready:
        blockers.append("credentials_access_failure")
    if not supervised_executor_bound:
        blockers.append("supervised_executor_binding_missing")
    if candidate_verification is None or not candidate_verification.verified:
        blockers.append("live_market_candidate_not_verified")
        if candidate_verification is not None:
            blockers.extend(candidate_verification.blockers)
    if remaining_budget <= 1e-9:
        blockers.append("validation_budget_cap_reached")
    elif projected_notional > remaining_budget + 1e-9:
        blockers.append("projected_validation_budget_cap_breach")
    cash_balance_before_usd: float | None = None
    cash_balance_after_projected_usd: float | None = None
    cash_balance_status = "cash_balance_unavailable"
    if cash_balance_snapshot is not None:
        cash_balance_before_usd = max(0.0, float(cash_balance_snapshot.balance_usd))
        cash_balance_after_projected_usd = max(0.0, cash_balance_before_usd - projected_notional)
        cash_balance_status = "trusted_balance_reported"
        if cash_balance_before_usd <= float(cash_balance_hard_stop_usd) + 1e-9:
            blockers.append("cash_balance_hard_stop_breach")
        elif cash_balance_after_projected_usd < float(cash_balance_hard_stop_usd) - 1e-9:
            blockers.append("projected_cash_balance_hard_stop_breach")
    return SupervisedLivePreflight(
        status="ready" if not blockers else "blocked",
        blockers=tuple(blockers),
        env_flags=flags,
        credentials_ready=credentials_ready,
        live_market_verified=bool(candidate_verification and candidate_verification.verified),
        executor_boundary_ready=boundary_ready,
        supervised_executor_bound=supervised_executor_bound,
        live_submission_permitted=not blockers,
        validation_budget_cap_usd=budget_cap,
        validation_budget_spent_usd=budget_spent,
        remaining_validation_budget_usd=remaining_budget,
        projected_order_notional_usd=projected_notional,
        cash_balance_hard_stop_usd=float(cash_balance_hard_stop_usd),
        cash_balance_status=cash_balance_status,
        cash_balance_before_usd=cash_balance_before_usd,
        cash_balance_after_projected_usd=cash_balance_after_projected_usd,
    )
