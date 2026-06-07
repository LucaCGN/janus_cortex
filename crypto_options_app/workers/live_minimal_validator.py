from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.db.runtime_persistence import persist_runtime_validation_report
from crypto_options_app.feeds.polymarket_status import build_cached_polymarket_status_provider
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.trading.live_candidates import LiveMarketCandidateVerification, scenario_from_verified_candidate
from crypto_options_app.trading.live_preflight import (
    LiveEnvironmentFlags,
    SupervisedLivePreflight,
    TrustedCashBalanceSnapshot,
    evaluate_supervised_live_preflight,
    read_live_environment_flags,
)
from crypto_options_app.trading.polymarket_supervised_executor import (
    PolymarketSupervisedExecutorConfig,
    build_polymarket_supervised_executor,
)
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.strategies.schema import StrategySpec
from crypto_options_app.workers.runtime_adapter import RuntimeValidationReport, SupervisedRuntimeConfig, validate_all_strategy_scenarios


@dataclass(frozen=True)
class MinimalLiveValidationResult:
    run_id: str
    generated_at_utc: datetime
    status: str
    preflight: SupervisedLivePreflight
    runtime_report: RuntimeValidationReport | None
    live_submission_attempted: bool
    manual_orders_avoided: bool = True

    @property
    def blockers(self) -> tuple[str, ...]:
        if self.preflight.blockers:
            return self.preflight.blockers
        if self.runtime_report is None:
            return ()
        return tuple(blocker for result in self.runtime_report.results for blocker in result.blockers)


@dataclass(frozen=True)
class MinimalLiveStrategyBatchResult:
    run_id: str
    generated_at_utc: datetime
    results: tuple[MinimalLiveValidationResult, ...]
    manual_orders_avoided: bool = True

    @property
    def live_submission_attempted(self) -> bool:
        return any(result.live_submission_attempted for result in self.results)

    @property
    def blockers_by_strategy(self) -> dict[str, tuple[str, ...]]:
        output: dict[str, tuple[str, ...]] = {}
        for result in self.results:
            if result.runtime_report is None:
                output[result.run_id] = result.blockers
                continue
            for strategy_result in result.runtime_report.results:
                output[strategy_result.strategy_id] = strategy_result.blockers
        return output


def run_minimal_supervised_live_validation(
    *,
    run_id: str,
    candidate_verification: LiveMarketCandidateVerification | None,
    credentials_ready: bool,
    env_flags: LiveEnvironmentFlags | None = None,
    operator: str = "codex-automation",
    reason: str = "minimal structural strategy validation",
    submitter: Any | None = None,
    persist_db_path: str | Path | None = None,
    cash_balance_snapshot: TrustedCashBalanceSnapshot | None = None,
    validation_budget_spent_usd: float = 0.0,
    validation_budget_cap_usd: float = 50.0,
    cash_balance_hard_stop_usd: float = 100.0,
) -> MinimalLiveValidationResult:
    flags = env_flags or read_live_environment_flags()
    scenario = (
        scenario_from_verified_candidate(candidate_verification.candidate)
        if candidate_verification is not None and candidate_verification.verified and candidate_verification.candidate is not None
        else None
    )
    projected_order_notional_usd = (
        len(all_strategy_specs()) * float(scenario.shares) * float(scenario.limit_price)
        if scenario is not None
        else 0.0
    )
    status_provider = None if submitter is not None else build_cached_polymarket_status_provider(fresh_seconds=60.0, ttl_seconds=600.0)
    executor_boundary = ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=flags.execution_approved,
        live_risk_acknowledged=flags.risk_acknowledged,
    )
    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(
            operator=operator,
            reason=reason,
            status_provider=status_provider,
        ),
        submitter=submitter,
    )
    preflight = evaluate_supervised_live_preflight(
        executor_boundary=executor_boundary,
        candidate_verification=candidate_verification,
        supervised_executor_bound=True,
        credentials_ready=credentials_ready,
        live_cadence_count=1,
        env_flags=flags,
        cash_balance_snapshot=cash_balance_snapshot,
        validation_budget_spent_usd=validation_budget_spent_usd,
        validation_budget_cap_usd=validation_budget_cap_usd,
        cash_balance_hard_stop_usd=cash_balance_hard_stop_usd,
        projected_order_notional_usd=projected_order_notional_usd,
    )
    if preflight.blockers:
        return MinimalLiveValidationResult(
            run_id=run_id,
            generated_at_utc=datetime.now(UTC),
            status="blocked",
            preflight=preflight,
            runtime_report=None,
            live_submission_attempted=False,
        )

    assert scenario is not None
    runtime_report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id=run_id,
            mode="supervised_live",
            executor_boundary=executor_boundary,
            max_trades_per_strategy=3,
            minimal_sizing=True,
            allow_live_submission=flags.live_execute,
            live_environment_approved=flags.ready,
            credentials_ready=credentials_ready,
            supervised_executor=executor,
            validation_budget_cap_usd=validation_budget_cap_usd,
            validation_budget_spent_usd=validation_budget_spent_usd,
            cash_balance_before_usd=preflight.cash_balance_before_usd,
            cash_balance_hard_stop_usd=cash_balance_hard_stop_usd,
            cash_balance_status=preflight.cash_balance_status,
        ),
        scenario=scenario,
    )
    if persist_db_path is not None:
        persist_runtime_validation_report(runtime_report, persist_db_path)
    blockers = tuple(blocker for result in runtime_report.results for blocker in result.blockers)
    return MinimalLiveValidationResult(
        run_id=run_id,
        generated_at_utc=datetime.now(UTC),
        status="live_structural_executed" if not blockers else "blocked",
        preflight=preflight,
        runtime_report=runtime_report,
        live_submission_attempted=bool(runtime_report.run_report.summary.get("live_submission_attempted")),
    )


def run_minimal_supervised_live_validation_batch(
    *,
    run_id: str,
    candidate_verifications: tuple[LiveMarketCandidateVerification, ...],
    credentials_ready: bool,
    env_flags: LiveEnvironmentFlags | None = None,
    specs: tuple[StrategySpec, ...] | None = None,
    operator: str = "codex-automation",
    reason: str = "minimal structural strategy validation",
    submitter: Any | None = None,
    persist_db_path: str | Path | None = None,
    cash_balance_snapshot: TrustedCashBalanceSnapshot | None = None,
    validation_budget_spent_usd: float = 0.0,
    validation_budget_cap_usd: float = 50.0,
    cash_balance_hard_stop_usd: float = 100.0,
) -> MinimalLiveStrategyBatchResult:
    specs = specs or all_strategy_specs()
    verified_candidates = tuple(verification for verification in candidate_verifications if verification.verified)
    results: list[MinimalLiveValidationResult] = []
    status_provider = None if submitter is not None else build_cached_polymarket_status_provider(fresh_seconds=60.0, ttl_seconds=600.0)
    if not verified_candidates:
        blocked = run_minimal_supervised_live_validation(
            run_id=f"{run_id}:no_verified_candidate",
            candidate_verification=None,
            credentials_ready=credentials_ready,
            env_flags=env_flags,
            operator=operator,
            reason=reason,
            submitter=submitter,
            persist_db_path=persist_db_path,
        )
        return MinimalLiveStrategyBatchResult(run_id=run_id, generated_at_utc=datetime.now(UTC), results=(blocked,))

    for index, spec in enumerate(specs):
        verification = verified_candidates[index % len(verified_candidates)]
        assert verification.candidate is not None
        scenario = scenario_from_verified_candidate(verification.candidate)
        flags = env_flags or read_live_environment_flags()
        executor_boundary = ExecutorBoundaryConfig(
            supervised_runtime_gate=True,
            ledger_gate=True,
            risk_gate=True,
            reconciliation_gate=True,
            execution_approved=flags.execution_approved,
            live_risk_acknowledged=flags.risk_acknowledged,
        )
        executor = build_polymarket_supervised_executor(
            PolymarketSupervisedExecutorConfig(
                operator=operator,
                reason=reason,
                status_provider=status_provider,
            ),
            submitter=submitter,
        )
        preflight = evaluate_supervised_live_preflight(
            executor_boundary=executor_boundary,
            candidate_verification=verification,
            supervised_executor_bound=True,
            credentials_ready=credentials_ready,
            live_cadence_count=1,
            env_flags=flags,
            cash_balance_snapshot=cash_balance_snapshot,
            validation_budget_spent_usd=validation_budget_spent_usd,
            validation_budget_cap_usd=validation_budget_cap_usd,
            cash_balance_hard_stop_usd=cash_balance_hard_stop_usd,
            projected_order_notional_usd=float(scenario.shares) * float(scenario.limit_price),
        )
        if preflight.blockers:
            results.append(
                MinimalLiveValidationResult(
                    run_id=f"{run_id}:{spec.strategy_id}",
                    generated_at_utc=datetime.now(UTC),
                    status="blocked",
                    preflight=preflight,
                    runtime_report=None,
                    live_submission_attempted=False,
                )
            )
            continue
        runtime_report = validate_all_strategy_scenarios(
            SupervisedRuntimeConfig(
                run_id=f"{run_id}:{spec.strategy_id}",
                mode="supervised_live",
                executor_boundary=executor_boundary,
                max_trades_per_strategy=1,
                minimal_sizing=True,
                allow_live_submission=flags.live_execute,
                live_environment_approved=flags.ready,
                credentials_ready=credentials_ready,
                supervised_executor=executor,
                validation_budget_cap_usd=validation_budget_cap_usd,
                validation_budget_spent_usd=validation_budget_spent_usd,
                cash_balance_before_usd=preflight.cash_balance_before_usd,
                cash_balance_hard_stop_usd=cash_balance_hard_stop_usd,
                cash_balance_status=preflight.cash_balance_status,
            ),
            scenario=scenario,
            specs=(spec,),
        )
        if persist_db_path is not None:
            persist_runtime_validation_report(runtime_report, persist_db_path)
        blockers = tuple(blocker for strategy_result in runtime_report.results for blocker in strategy_result.blockers)
        results.append(
            MinimalLiveValidationResult(
                run_id=f"{run_id}:{spec.strategy_id}",
                generated_at_utc=datetime.now(UTC),
                status="live_structural_executed" if not blockers else "blocked",
                preflight=preflight,
                runtime_report=runtime_report,
                live_submission_attempted=bool(runtime_report.run_report.summary.get("live_submission_attempted")),
            )
        )
    return MinimalLiveStrategyBatchResult(run_id=run_id, generated_at_utc=datetime.now(UTC), results=tuple(results))
