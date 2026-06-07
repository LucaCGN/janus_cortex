from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies.registry import get_strategy
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.runtime_adapter import RuntimeScenario, SupervisedRuntimeConfig, validate_all_strategy_scenarios
from crypto_options_app.workers.strategy_live_replay import (
    DEFAULT_STRATEGY_IDS,
    _load_live_replay_scenarios,
    _persist_runtime_validation_report_with_retry,
    _scenario_payload,
)


@dataclass(frozen=True)
class StrategyBacktestReplayConfig:
    db_path: Path = CENTRAL_DB_PATH
    run_id: str | None = None
    strategy_ids: tuple[str, ...] = DEFAULT_STRATEGY_IDS
    max_trades_per_strategy: int = 1
    validation_budget_cap_usd: float = 50.0
    forward_mark_horizon_seconds: float = 60.0
    max_scenarios: int = 1
    scenario_selector: str = "fixture"


def run_strategy_backtest_replay(config: StrategyBacktestReplayConfig | None = None) -> dict[str, Any]:
    config = config or StrategyBacktestReplayConfig()
    db_path = _initialize_schema_with_retry(config.db_path)
    run_id = config.run_id or f"strategy-backtest-replay-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    scenarios = _load_historical_replay_scenarios(
        db_path,
        forward_mark_horizon_seconds=config.forward_mark_horizon_seconds,
        max_scenarios=config.max_scenarios,
        scenario_selector=config.scenario_selector,
        strategy_ids=config.strategy_ids,
    )
    specs = tuple(get_strategy(strategy_id) for strategy_id in config.strategy_ids)
    reports = []
    scenario_payloads: list[dict[str, Any]] = []
    scenario_statuses: list[dict[str, Any]] = []
    aggregate_counts: dict[str, int] = {}
    for index, (scenario, scenario_source) in enumerate(scenarios, start=1):
        scenario_run_id = run_id if len(scenarios) == 1 else f"{run_id}-scenario-{index}"
        report = validate_all_strategy_scenarios(
            SupervisedRuntimeConfig(
                run_id=scenario_run_id,
                mode="dry_run",
                executor_boundary=_dry_run_boundary(),
                max_trades_per_strategy=max(1, int(config.max_trades_per_strategy)),
                allow_live_submission=False,
                live_environment_approved=False,
                credentials_ready=False,
                validation_budget_cap_usd=float(config.validation_budget_cap_usd),
                cash_balance_status="not_required_for_read_only_backtest",
            ),
            scenario=scenario,
            specs=specs,
        )
        reports.append(report)
        counts = _persist_runtime_validation_report_with_retry(report, db_path)
        for key, value in counts.items():
            aggregate_counts[key] = aggregate_counts.get(key, 0) + int(value)
        scenario_payloads.append(_scenario_payload(scenario, source=scenario_source, run_id=scenario_run_id))
        scenario_statuses.append(
            {
                "run_id": scenario_run_id,
                "scenario_source": scenario_source,
                "event_token_key": scenario.event_token_key,
                "passed_count": report.passed_count,
                "blocked_count": report.blocked_count,
                "statuses": {result.strategy_id: result.status for result in report.results},
                "blockers": {result.strategy_id: list(result.blockers) for result in report.results},
            }
        )
    first_scenario, first_source = scenarios[0]
    total_results = tuple(result for report in reports for result in report.results)
    return {
        "schema_version": "crypto_options_strategy_backtest_replay_run_v1",
        "run_id": run_id,
        "mode": "historical_backtest",
        "runtime_mode": "dry_run",
        "scenario_source": first_source,
        "scenario": _scenario_payload(first_scenario, source=first_source, run_id=run_id),
        "scenario_count": len(scenarios),
        "scenarios": scenario_payloads,
        "strategy_ids": list(config.strategy_ids),
        "result_count": len(total_results),
        "passed_count": sum(report.passed_count for report in reports),
        "blocked_count": sum(report.blocked_count for report in reports),
        "statuses": {result.strategy_id: result.status for result in total_results},
        "blockers": {result.strategy_id: list(result.blockers) for result in total_results},
        "scenario_statuses": scenario_statuses,
        "db_counts": aggregate_counts,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": all(report.manual_orders_avoided for report in reports),
    }


def _load_historical_replay_scenarios(
    db_path: Path,
    *,
    forward_mark_horizon_seconds: float,
    max_scenarios: int,
    scenario_selector: str,
    strategy_ids: tuple[str, ...],
) -> tuple[tuple[RuntimeScenario, str], ...]:
    selector = str(scenario_selector or "fixture").strip().lower().replace("-", "_")
    if selector == "fixture":
        return ((_historical_fixture_scenario(), "historical_fixture"),)
    return _load_live_replay_scenarios(
        db_path,
        forward_mark_horizon_seconds=forward_mark_horizon_seconds,
        max_scenarios=max_scenarios,
        scenario_selector=selector,
        strategy_ids=strategy_ids,
    )


def _historical_fixture_scenario() -> RuntimeScenario:
    return RuntimeScenario(
        event_key="historical-replay-fixture-event",
        event_token_key="historical-replay-fixture-event:up",
        token_id="historical-replay-fixture-token",
        event_slug="historical-replay-fixture-event",
        outcome="Up",
        shares=1.0,
        limit_price=0.51,
        spread=0.01,
        liquidity_depth=20.0,
        time_remaining_seconds=240.0,
        signal_context={
            "best_bid": 0.50,
            "best_ask": 0.51,
            "target_up_ratio": 0.62,
            "profile_distribution_ready": True,
            "profile_distribution": {
                "profile_distribution_up_ratio": 0.62,
                "profile_distribution_down_ratio": 0.38,
                "blockers": [],
                "component_breakdown": {},
            },
            "option_path_ready": True,
            "option_path": {
                "snapshot_count": 9,
                "level_crossing_count": 6,
                "rebound_direction_flip_count": 3,
                "near_50c_sample_count": 5,
                "strong_rebound_touch_count": 3,
                "avg_rolling_60s_range": 0.07,
                "max_rolling_60s_range": 0.10,
                "pair_sum_range": 0.04,
                "blockers": [],
            },
            "source": "strategy_backtest_replay_fixture",
        },
    )


def _initialize_schema_with_retry(db_path: Path, *, attempts: int = 4, delay_seconds: float = 2.0) -> Path:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            return initialize_schema(db_path)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            last_error = exc
            time.sleep(max(0.1, float(delay_seconds)))
    if last_error is not None:
        raise last_error
    return initialize_schema(db_path)


def _dry_run_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=False,
    )
