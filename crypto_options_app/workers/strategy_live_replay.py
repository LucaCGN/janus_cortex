from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect, table_exists
from crypto_options_app.db.dialect import POSTGRES_DIALECT, SQLITE_DIALECT, DatabaseDialect
from crypto_options_app.db.runtime_persistence import persist_runtime_validation_report
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies.registry import get_strategy
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.runtime_adapter import RuntimeScenario, SupervisedRuntimeConfig, validate_all_strategy_scenarios


DEFAULT_STRATEGY_IDS = (
    "profile_hedge_scalping_v1",
    "event_context_profile_confirmed_v1",
    "hedger_ratio_replication_v4",
    "profile_hedge_scalping_v4",
    "grid_band_rebound_v3",
)


@dataclass(frozen=True)
class StrategyLiveReplayConfig:
    db_path: Path = CENTRAL_DB_PATH
    run_id: str | None = None
    strategy_ids: tuple[str, ...] = DEFAULT_STRATEGY_IDS
    max_trades_per_strategy: int = 1
    validation_budget_cap_usd: float = 50.0
    forward_mark_horizon_seconds: float = 60.0
    max_scenarios: int = 1
    scenario_selector: str = "latest"


def _dialect_for_connection(conn) -> DatabaseDialect:
    return POSTGRES_DIALECT if bool(getattr(conn, "is_postgres", False)) else SQLITE_DIALECT


def run_strategy_live_replay(config: StrategyLiveReplayConfig | None = None) -> dict[str, Any]:
    config = config or StrategyLiveReplayConfig()
    db_path = _initialize_schema_with_retry(config.db_path)
    run_id = config.run_id or f"strategy-live-replay-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    scenarios = _load_live_replay_scenarios(
        db_path,
        forward_mark_horizon_seconds=config.forward_mark_horizon_seconds,
        max_scenarios=config.max_scenarios,
        scenario_selector=config.scenario_selector,
        strategy_ids=config.strategy_ids,
    )
    if not scenarios:
        return {
            "schema_version": "crypto_options_strategy_live_replay_run_v1",
            "run_id": run_id,
            "mode": "live_replay",
            "runtime_mode": "shadow",
            "scenario_source": _normalize_scenario_selector(config.scenario_selector),
            "scenario": None,
            "scenario_count": 0,
            "scenarios": [],
            "strategy_ids": list(config.strategy_ids),
            "result_count": 0,
            "passed_count": 0,
            "blocked_count": 0,
            "statuses": {},
            "blockers": {"scenario_selector": ["no_matching_scenarios"]},
            "scenario_statuses": [],
            "db_counts": {},
            "orders_allowed": False,
            "live_trading_authorized": False,
            "manual_orders_avoided": True,
        }
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
                mode="shadow",
                executor_boundary=_shadow_boundary(),
                max_trades_per_strategy=max(1, int(config.max_trades_per_strategy)),
                allow_live_submission=False,
                live_environment_approved=False,
                credentials_ready=False,
                validation_budget_cap_usd=float(config.validation_budget_cap_usd),
                cash_balance_status="not_required_for_read_only_shadow",
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
                "event_key": scenario.event_key,
                "event_slug": scenario.event_slug,
                "event_token_key": scenario.event_token_key,
                "outcome": scenario.outcome,
                "passed_count": report.passed_count,
                "blocked_count": report.blocked_count,
                "statuses": {result.strategy_id: result.status for result in report.results},
                "blockers": {result.strategy_id: list(result.blockers) for result in report.results},
            }
        )
    first_scenario, first_source = scenarios[0]
    total_results = tuple(result for report in reports for result in report.results)
    return {
        "schema_version": "crypto_options_strategy_live_replay_run_v1",
        "run_id": run_id,
        "mode": "live_replay",
        "runtime_mode": "shadow",
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


def scout_strategy_live_replay_candidates(config: StrategyLiveReplayConfig | None = None) -> dict[str, Any]:
    """Return replay candidate windows without running strategy validation.

    This keeps the controller from repeatedly executing full shadow/live-replay
    batches just to learn whether strict tail, inversion, or profile windows are
    available. It may refresh selector candidate caches, but it never writes
    strategy validation runs or authorizes orders.
    """

    config = config or StrategyLiveReplayConfig()
    db_path = _initialize_schema_with_retry(config.db_path)
    generated_at_utc = datetime.now(UTC)
    run_id = config.run_id or f"strategy-live-replay-scout-{generated_at_utc.strftime('%Y%m%dT%H%M%SZ')}"
    selector = _normalize_scenario_selector(config.scenario_selector)
    scenarios = _load_live_replay_scenarios(
        db_path,
        forward_mark_horizon_seconds=config.forward_mark_horizon_seconds,
        max_scenarios=config.max_scenarios,
        scenario_selector=selector,
        strategy_ids=config.strategy_ids,
    )
    scenario_payloads = [
        _scenario_payload(scenario, source=scenario_source, run_id=f"{run_id}-candidate-{index}")
        for index, (scenario, scenario_source) in enumerate(scenarios, start=1)
    ]
    distinct_event_keys = sorted(
        {
            str(row.get("event_key") or "")
            for row in scenario_payloads
            if str(row.get("event_key") or "").strip()
        }
    )
    distinct_event_token_keys = sorted(
        {
            str(row.get("event_token_key") or "")
            for row in scenario_payloads
            if str(row.get("event_token_key") or "").strip()
        }
    )
    scenario_sources = sorted(
        {
            str(row.get("source") or "")
            for row in scenario_payloads
            if str(row.get("source") or "").strip()
        }
    )
    return {
        "schema_version": "crypto_options_strategy_live_replay_candidate_scout_v1",
        "run_id": run_id,
        "generated_at_utc": generated_at_utc.isoformat(),
        "mode": "live_replay_candidate_scout",
        "runtime_mode": "shadow_read_only",
        "scenario_selector": selector,
        "scenario_sources": scenario_sources,
        "strategy_ids": list(config.strategy_ids),
        "forward_mark_horizon_seconds": float(config.forward_mark_horizon_seconds),
        "requested_max_scenarios": max(1, int(config.max_scenarios)),
        "scenario_count": len(scenario_payloads),
        "distinct_event_count": len(distinct_event_keys),
        "distinct_event_token_count": len(distinct_event_token_keys),
        "distinct_event_keys": distinct_event_keys[:50],
        "distinct_event_token_keys": distinct_event_token_keys[:50],
        "scenarios": scenario_payloads,
        "blockers": [] if scenario_payloads else ["no_matching_scenarios"],
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
    }


def _load_live_replay_scenarios(
    db_path: Path,
    *,
    forward_mark_horizon_seconds: float = 60.0,
    max_scenarios: int = 1,
    scenario_selector: str = "latest",
    strategy_ids: tuple[str, ...] = DEFAULT_STRATEGY_IDS,
) -> tuple[tuple[RuntimeScenario, str], ...]:
    requested = max(1, int(max_scenarios))
    selector = _normalize_scenario_selector(scenario_selector)
    with connect(db_path) as conn:
        if selector in {"tail_touch", "tail_touch_forward_edge_clean"}:
            if selector == "tail_touch_forward_edge_clean":
                cached_tail_touch_ticks = _cached_tail_touch_candidate_rows(
                    conn,
                    selector=selector,
                    strategy_ids=strategy_ids,
                    horizon_seconds=forward_mark_horizon_seconds,
                    limit=requested,
                )
                if cached_tail_touch_ticks is not None:
                    if not cached_tail_touch_ticks:
                        return ()
                    return tuple(
                        (
                            _scenario_from_price_tick(row, conn),
                            "polymarket_price_ticks_forward_mark_tail_touch_forward_edge_clean_cached",
                        )
                        for row in cached_tail_touch_ticks
                    )
            tail_touch_ticks = _tail_touch_price_ticks_with_forward_marks(
                conn,
                horizon_seconds=forward_mark_horizon_seconds,
                limit=requested if selector == "tail_touch" else max(requested * 3, requested + 6),
            )
            if tail_touch_ticks:
                if selector == "tail_touch_forward_edge_clean":
                    tail_touch_ticks = _filter_tail_touch_rows_by_forward_edge(
                        tail_touch_ticks,
                        strategy_ids=strategy_ids,
                        limit=requested,
                    )
                    _persist_tail_touch_candidate_cache(
                        conn,
                        selector=selector,
                        strategy_ids=strategy_ids,
                        horizon_seconds=forward_mark_horizon_seconds,
                        rows=tail_touch_ticks,
                    )
                if tail_touch_ticks:
                    source = (
                        "polymarket_price_ticks_forward_mark_tail_touch_forward_edge_clean"
                        if selector == "tail_touch_forward_edge_clean"
                        else "polymarket_price_ticks_forward_mark_tail_touch"
                    )
                    return tuple(
                        (_scenario_from_price_tick(row, conn), source)
                        for row in tail_touch_ticks
                    )
            if selector == "tail_touch_forward_edge_clean":
                _persist_tail_touch_candidate_cache(
                    conn,
                    selector=selector,
                    strategy_ids=strategy_ids,
                    horizon_seconds=forward_mark_horizon_seconds,
                    rows=(),
                )
                return ()
        if selector == "low_range_no_edge":
            low_range_ticks = _low_range_no_edge_price_ticks_with_forward_marks(
                conn,
                horizon_seconds=forward_mark_horizon_seconds,
                limit=requested,
            )
            _persist_tail_touch_candidate_cache(
                conn,
                selector=selector,
                strategy_ids=strategy_ids,
                horizon_seconds=forward_mark_horizon_seconds,
                rows=low_range_ticks,
            )
            if low_range_ticks:
                return tuple(
                    (_scenario_from_price_tick(row, conn), "polymarket_price_ticks_forward_mark_low_range_no_edge")
                    for row in low_range_ticks
                )
            return ()
        if selector in {
            "high_inversion",
            "high_inversion_disjoint",
            "recent_high_inversion_disjoint",
            "hedge_grid_ready",
            "hedge_grid_closed_cycle_ready",
        }:
            closed_cycle_ready = selector == "hedge_grid_closed_cycle_ready"
            hedge_grid_source = selector in {"hedge_grid_ready", "hedge_grid_closed_cycle_ready"}
            expanded_request = min(max(requested * 4, requested), 24) if closed_cycle_ready else requested
            high_inversion_ticks = _high_inversion_price_ticks_with_forward_marks(
                conn,
                horizon_seconds=forward_mark_horizon_seconds,
                limit=expanded_request,
                max_per_event=2 if selector in {"high_inversion_disjoint", "recent_high_inversion_disjoint", "hedge_grid_ready", "hedge_grid_closed_cycle_ready"} else None,
                recent_first=selector in {"recent_high_inversion_disjoint", "hedge_grid_ready", "hedge_grid_closed_cycle_ready"},
                hedge_grid_ready=hedge_grid_source,
            )
            if closed_cycle_ready:
                high_inversion_ticks = _hedge_grid_closed_cycle_ready_rows(
                    conn,
                    high_inversion_ticks,
                    limit=requested,
                    strategy_ids=strategy_ids,
                )
            if high_inversion_ticks:
                source = (
                    "polymarket_price_ticks_forward_mark_hedge_grid_closed_cycle_ready"
                    if selector == "hedge_grid_closed_cycle_ready"
                    else (
                        "polymarket_price_ticks_forward_mark_hedge_grid_ready"
                        if selector == "hedge_grid_ready"
                        else (
                            "polymarket_price_ticks_forward_mark_recent_high_inversion"
                            if selector == "recent_high_inversion_disjoint"
                            else "polymarket_price_ticks_forward_mark_high_inversion"
                        )
                    )
                )
                return tuple(
                    (_scenario_from_price_tick(row, conn), source)
                    for row in high_inversion_ticks
                )
            return ()
        if selector in {"profile_group", "profile_group_quality"}:
            group_ticks = _profile_group_price_ticks_with_forward_marks(
                conn,
                horizon_seconds=forward_mark_horizon_seconds,
                limit=requested,
                strategy_ids=strategy_ids,
                quality_filter=selector == "profile_group_quality",
            )
            if group_ticks:
                source = (
                    "polymarket_price_ticks_forward_mark_profile_group_quality"
                    if selector == "profile_group_quality"
                    else "polymarket_price_ticks_forward_mark_profile_group"
                )
                return tuple(
                    (_scenario_from_price_tick(row, conn), source)
                    for row in group_ticks
                )
            if selector == "profile_group_quality":
                return ()
        if selector in {"profile_preferred", "profile_opposed"}:
            preferred_ticks = _profile_preferred_price_ticks_with_forward_marks(
                conn,
                horizon_seconds=forward_mark_horizon_seconds,
                limit=requested,
                strategy_ids=strategy_ids,
                forced_direction_mode="contrarian" if selector == "profile_opposed" else "follow",
            )
            if preferred_ticks:
                source = (
                    "polymarket_price_ticks_forward_mark_profile_opposed"
                    if selector == "profile_opposed"
                    else "polymarket_price_ticks_forward_mark_profile_preferred"
                )
                return tuple(
                    (_scenario_from_price_tick(row, conn), source)
                    for row in preferred_ticks
                )
        if requested == 1:
            scenario = _load_live_replay_scenario_from_connection(
                conn,
                forward_mark_horizon_seconds=forward_mark_horizon_seconds,
            )
            return (scenario,)
        forward_price_ticks = _latest_price_ticks_with_forward_marks(
            conn,
            horizon_seconds=forward_mark_horizon_seconds,
            limit=requested,
        )
        if forward_price_ticks:
            return tuple(
                (_scenario_from_price_tick(row, conn), "polymarket_price_ticks_forward_mark")
                for row in forward_price_ticks
            )
        scenario = _load_live_replay_scenario_from_connection(
            conn,
            forward_mark_horizon_seconds=forward_mark_horizon_seconds,
        )
        return (scenario,)


def _normalize_scenario_selector(value: str | None) -> str:
    selector = str(value or "latest").strip().lower().replace("-", "_")
    allowed = {
        "latest",
        "profile_preferred",
        "profile_opposed",
        "profile_group",
        "profile_group_quality",
        "high_inversion",
        "high_inversion_disjoint",
        "recent_high_inversion_disjoint",
        "hedge_grid_ready",
        "hedge_grid_closed_cycle_ready",
        "tail_touch",
        "tail_touch_forward_edge_clean",
        "low_range_no_edge",
    }
    return selector if selector in allowed else "latest"


def _load_live_replay_scenario(db_path: Path, *, forward_mark_horizon_seconds: float = 60.0) -> tuple[RuntimeScenario, str]:
    with connect(db_path) as conn:
        return _load_live_replay_scenario_from_connection(
            conn,
            forward_mark_horizon_seconds=forward_mark_horizon_seconds,
        )


def _load_live_replay_scenario_from_connection(
    conn,
    *,
    forward_mark_horizon_seconds: float = 60.0,
) -> tuple[RuntimeScenario, str]:
    replay_frame = _latest_replay_frame(conn)
    if replay_frame is not None:
        return _scenario_from_replay_frame(replay_frame), "replay_frames"
    forward_price_tick = _latest_price_tick_with_forward_mark(
        conn,
        horizon_seconds=forward_mark_horizon_seconds,
    )
    if forward_price_tick is not None:
        return _scenario_from_price_tick(forward_price_tick, conn), "polymarket_price_ticks_forward_mark"
    price_tick = _latest_price_tick(conn)
    if price_tick is not None:
        return _scenario_from_price_tick(price_tick, conn), "polymarket_price_ticks"
    return _fixture_scenario(), "fixture"


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


def _persist_runtime_validation_report_with_retry(
    report,
    db_path: Path,
    *,
    attempts: int = 6,
    delay_seconds: float = 1.5,
) -> dict[str, int]:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            return persist_runtime_validation_report(report, db_path)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            last_error = exc
            time.sleep(max(0.1, float(delay_seconds)))
    if last_error is not None:
        raise last_error
    return persist_runtime_validation_report(report, db_path)


def _latest_replay_frame(conn) -> dict[str, Any] | None:
    if not table_exists(conn, "replay_frames"):
        return None
    row = conn.execute(
        """
        SELECT event_key, event_token_key, replay_timestamp_utc, source_observed_at_utc,
               decision_at_utc, frame_json
          FROM replay_frames
         ORDER BY replay_timestamp_utc DESC, inserted_at_utc DESC
         LIMIT 1
        """
    ).fetchone()
    return None if row is None else dict(row)


def _latest_price_tick(conn) -> dict[str, Any] | None:
    if not table_exists(conn, "polymarket_price_ticks"):
        return None
    row = conn.execute(
        """
        SELECT event_key, event_token_key, token_id, event_slug, outcome,
               system_received_at_utc, best_bid, best_ask, spread,
               depth_top3_bid_size, depth_top3_ask_size, mid_price
          FROM polymarket_price_ticks
         WHERE event_token_key IS NOT NULL
         ORDER BY system_received_at_utc DESC, system_inserted_at_utc DESC
         LIMIT 1
        """
    ).fetchone()
    return None if row is None else dict(row)


def _latest_price_tick_with_forward_mark(
    conn,
    *,
    horizon_seconds: float,
    tolerance_seconds: float = 15.0,
) -> dict[str, Any] | None:
    if not table_exists(conn, "polymarket_price_ticks"):
        return None
    horizon_seconds = max(1.0, float(horizon_seconds))
    tolerance_seconds = max(1.0, float(tolerance_seconds))
    candidate_limit = _forward_mark_candidate_limit(1)
    dialect = _dialect_for_connection(conn)
    forward_delta_seconds = dialect.seconds_between("f.system_received_at_utc", "p.system_received_at_utc")
    row = conn.execute(
        f"""
        WITH candidate_p AS (
            SELECT
                p.event_key,
                p.event_token_key,
                p.token_id,
                p.event_slug,
                p.outcome,
                p.system_received_at_utc,
                p.best_bid,
                p.best_ask,
                p.spread,
                p.depth_top3_bid_size,
                p.depth_top3_ask_size,
                p.mid_price,
                EXISTS(
                    SELECT 1
                      FROM profile_distribution_snapshots s
                     WHERE s.event_key = p.event_key
                ) AS profile_context_available,
                EXISTS(
                    SELECT 1
                      FROM polymarket_event_path_stats eps
                     WHERE eps.event_key = p.event_key
                ) AS option_path_context_available
              FROM polymarket_price_ticks p
             WHERE p.event_token_key IS NOT NULL
               AND p.best_ask IS NOT NULL
               AND COALESCE(p.best_ask, p.mid_price) > 0
             ORDER BY p.system_received_at_utc DESC
             LIMIT ?
        )
        SELECT
            p.event_key,
            p.event_token_key,
            p.token_id,
            p.event_slug,
            p.outcome,
            p.system_received_at_utc,
            p.best_bid,
            p.best_ask,
            p.spread,
            p.depth_top3_bid_size,
            p.depth_top3_ask_size,
            p.mid_price,
            f.best_bid AS forward_best_bid,
            f.best_ask AS forward_best_ask,
            f.mid_price AS forward_mid_price,
            f.system_received_at_utc AS forward_mark_at_utc,
            {forward_delta_seconds} AS forward_horizon_seconds,
            p.profile_context_available,
            p.option_path_context_available
          FROM candidate_p p
          JOIN polymarket_price_ticks f
            ON f.event_token_key = p.event_token_key
           AND f.system_received_at_utc > p.system_received_at_utc
           AND f.system_received_at_utc >= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
           AND f.system_received_at_utc <= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
         WHERE COALESCE(f.best_bid, f.mid_price) IS NOT NULL
           AND {forward_delta_seconds} BETWEEN ? AND ?
         ORDER BY option_path_context_available DESC,
                  profile_context_available DESC,
                  p.system_received_at_utc DESC,
                  ABS({forward_delta_seconds} - ?)
         LIMIT 1
        """,
        (
            candidate_limit,
            f"+{max(1.0, horizon_seconds - tolerance_seconds):.6f} seconds",
            f"+{horizon_seconds + tolerance_seconds:.6f} seconds",
            max(1.0, horizon_seconds - tolerance_seconds),
            horizon_seconds + tolerance_seconds,
            horizon_seconds,
        ),
    ).fetchone()
    return None if row is None else dict(row)


def _latest_price_ticks_with_forward_marks(
    conn,
    *,
    horizon_seconds: float,
    limit: int,
    tolerance_seconds: float = 15.0,
) -> tuple[dict[str, Any], ...]:
    if not table_exists(conn, "polymarket_price_ticks"):
        return ()
    horizon_seconds = max(1.0, float(horizon_seconds))
    tolerance_seconds = max(1.0, float(tolerance_seconds))
    requested = max(1, int(limit))
    candidate_limit = _forward_mark_candidate_limit(requested)
    dialect = _dialect_for_connection(conn)
    forward_delta_seconds = dialect.seconds_between("f.system_received_at_utc", "p.system_received_at_utc")
    rows = conn.execute(
        f"""
        WITH candidate_p AS (
            SELECT
                p.event_key,
                p.event_token_key,
                p.token_id,
                p.event_slug,
                p.outcome,
                p.system_received_at_utc,
                p.best_bid,
                p.best_ask,
                p.spread,
                p.depth_top3_bid_size,
                p.depth_top3_ask_size,
                p.mid_price,
                EXISTS(
                    SELECT 1
                      FROM profile_distribution_snapshots s
                     WHERE s.event_key = p.event_key
                ) AS profile_context_available,
                EXISTS(
                    SELECT 1
                      FROM polymarket_event_path_stats eps
                     WHERE eps.event_key = p.event_key
                ) AS option_path_context_available
              FROM polymarket_price_ticks p
             WHERE p.event_token_key IS NOT NULL
               AND p.best_ask IS NOT NULL
               AND COALESCE(p.best_ask, p.mid_price) > 0
             ORDER BY p.system_received_at_utc DESC
             LIMIT ?
        )
        SELECT
            p.event_key,
            p.event_token_key,
            p.token_id,
            p.event_slug,
            p.outcome,
            p.system_received_at_utc,
            p.best_bid,
            p.best_ask,
            p.spread,
            p.depth_top3_bid_size,
            p.depth_top3_ask_size,
            p.mid_price,
            f.best_bid AS forward_best_bid,
            f.best_ask AS forward_best_ask,
            f.mid_price AS forward_mid_price,
            f.system_received_at_utc AS forward_mark_at_utc,
            {forward_delta_seconds} AS forward_horizon_seconds,
            p.profile_context_available,
            p.option_path_context_available
          FROM candidate_p p
          JOIN polymarket_price_ticks f
            ON f.event_token_key = p.event_token_key
           AND f.system_received_at_utc > p.system_received_at_utc
           AND f.system_received_at_utc >= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
           AND f.system_received_at_utc <= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
         WHERE COALESCE(f.best_bid, f.mid_price) IS NOT NULL
           AND {forward_delta_seconds} BETWEEN ? AND ?
         ORDER BY option_path_context_available DESC,
                  profile_context_available DESC,
                  p.system_received_at_utc DESC,
                  ABS({forward_delta_seconds} - ?)
         LIMIT ?
        """,
        (
            candidate_limit,
            f"+{max(1.0, horizon_seconds - tolerance_seconds):.6f} seconds",
            f"+{horizon_seconds + tolerance_seconds:.6f} seconds",
            max(1.0, horizon_seconds - tolerance_seconds),
            horizon_seconds + tolerance_seconds,
            horizon_seconds,
            requested * 3,
        ),
    ).fetchall()
    unique_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        payload = dict(row)
        key = (str(payload.get("event_token_key")), str(payload.get("system_received_at_utc")))
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(payload)
        if len(unique_rows) >= requested:
            break
    return tuple(unique_rows)


def _profile_preferred_price_ticks_with_forward_marks(
    conn,
    *,
    horizon_seconds: float,
    limit: int,
    strategy_ids: tuple[str, ...],
    forced_direction_mode: str | None = None,
) -> tuple[dict[str, Any], ...]:
    requested = max(1, int(limit))
    candidate_rows = _latest_price_ticks_with_forward_marks(
        conn,
        horizon_seconds=horizon_seconds,
        limit=max(24, requested * 12),
    )
    if not candidate_rows:
        return ()
    direction_mode, threshold = _profile_selector_mode_and_threshold(strategy_ids)
    forced_mode = str(forced_direction_mode or "").strip().lower()
    if forced_mode in {"follow", "contrarian"}:
        direction_mode = forced_mode
    selected: list[dict[str, Any]] = []
    for row in candidate_rows:
        profile = _profile_distribution_signal_context(
            conn,
            event_key=str(row.get("event_key") or ""),
            event_slug=str(row.get("event_slug") or ""),
        )
        desired_up = _desired_up_from_profile_context(profile, threshold=threshold)
        if desired_up is None:
            continue
        if direction_mode == "contrarian":
            desired_up = not desired_up
        if desired_up == _is_up_outcome(row.get("outcome")):
            selected.append(row)
        if len(selected) >= requested:
            break
    return tuple(selected)


def _profile_group_price_ticks_with_forward_marks(
    conn,
    *,
    horizon_seconds: float,
    limit: int,
    strategy_ids: tuple[str, ...],
    quality_filter: bool = False,
) -> tuple[dict[str, Any], ...]:
    selectors = _profile_group_selectors(strategy_ids)
    if not selectors:
        return ()
    requested = max(1, int(limit))
    candidate_floor = 96 if quality_filter else 24
    candidate_multiplier = 32 if quality_filter else 8
    preferences = _profile_group_event_preferences(
        conn,
        selectors=selectors,
        limit=max(candidate_floor, requested * candidate_multiplier),
    )
    selected = _profile_group_price_ticks_from_preferences(
        conn,
        horizon_seconds=horizon_seconds,
        preferences=preferences,
        limit=max(96, requested * 12) if quality_filter else requested,
    )
    if quality_filter:
        selected = _profile_group_quality_rows(
            conn,
            selected,
            limit=requested,
        )
    return tuple(selected)


def _profile_group_quality_rows(
    conn,
    rows: tuple[dict[str, Any], ...],
    *,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    """Keep only profile-group rows likely to pass cheap runtime gates.

    This selector is intentionally conservative and scalar-only. It mirrors the
    blockers that made V9/V10 profile-follow replay waste most rows:
    high entry price, wide spread, weak option-path context, high top-profile
    concentration, and stale/missing profile context. Full lifecycle and
    economics still run in the replay worker.
    """

    requested = max(1, int(limit))
    selected: list[dict[str, Any]] = []
    for row in rows:
        matched_strategy_ids = tuple(
            str(strategy_id)
            for strategy_id in (row.get("profile_group_selector_matched_strategy_ids") or ())
            if str(strategy_id).strip()
        )
        if not matched_strategy_ids:
            continue
        for strategy_id in matched_strategy_ids:
            blockers, metrics = _profile_group_quality_blockers(conn, row, strategy_id=strategy_id)
            if blockers:
                continue
            payload = dict(row)
            payload["replay_profile_group_quality_ready"] = 1
            payload["replay_profile_group_quality_strategy_id"] = strategy_id
            payload["replay_profile_group_quality_metrics"] = metrics
            selected.append(payload)
            break
        if len(selected) >= requested:
            break
    return tuple(selected)


def _profile_group_quality_blockers(conn, row: dict[str, Any], *, strategy_id: str) -> tuple[tuple[str, ...], dict[str, Any]]:
    try:
        spec = get_strategy(strategy_id)
    except KeyError:
        return ("strategy_spec_missing",), {}
    blockers: list[str] = []
    metrics: dict[str, Any] = {"strategy_id": strategy_id}

    entry_price = _optional_price(row.get("best_ask"))
    entry_min = _optional_price(spec.metadata.get("option_entry_price_min"))
    entry_max = _optional_price(spec.metadata.get("option_entry_price_max"))
    metrics["entry_price"] = entry_price
    metrics["entry_price_min"] = entry_min
    metrics["entry_price_max"] = entry_max
    if entry_min is not None and (entry_price is None or entry_price < entry_min):
        blockers.append("option_entry_price_below_band")
    if entry_max is not None and (entry_price is None or entry_price > entry_max):
        blockers.append("option_entry_price_above_band")

    spread = _optional_price(row.get("spread"))
    max_spread = _optional_price(spec.metadata.get("option_path_max_spread"))
    metrics["spread"] = spread
    metrics["max_spread"] = max_spread
    if max_spread is not None and (spread is None or spread > max_spread):
        blockers.append("option_spread_too_wide")

    event_key = str(row.get("event_key") or "")
    event_slug = str(row.get("event_slug") or "")
    profile = _profile_distribution_signal_context(conn, event_key=event_key, event_slug=event_slug)

    max_shadow_spread_drag = _optional_price(spec.metadata.get("shadow_economics_max_spread_drag_usd"))
    if max_shadow_spread_drag is not None:
        best_bid = _optional_price(row.get("best_bid"))
        target_up_ratio = _optional_price(row.get("target_up_ratio"))
        if target_up_ratio is None:
            target_up_ratio = _optional_price(profile.get("profile_distribution_up_ratio"))
        ratio_distance = abs((target_up_ratio if target_up_ratio is not None else 0.5) - 0.5)
        estimated_shares = round(0.6 + min(0.8, ratio_distance * 2.0), 8)
        estimated_spread_drag = (
            None
            if entry_price is None or best_bid is None
            else round(max(0.0, entry_price - best_bid) * estimated_shares, 8)
        )
        metrics["estimated_shares"] = estimated_shares
        metrics["estimated_spread_drag_usd"] = estimated_spread_drag
        metrics["max_shadow_spread_drag_usd"] = max_shadow_spread_drag
        if estimated_spread_drag is None or estimated_spread_drag > max_shadow_spread_drag:
            blockers.append("spread_drag_above_shadow_gate")

    metrics["profile_distribution_ready"] = bool(profile.get("profile_distribution_ready"))
    selector_tuple = _profile_group_selectors((strategy_id,))
    latest_desired_up = _desired_up_from_profile_group(profile, selector_tuple[0]) if selector_tuple else None
    metrics["latest_profile_desired_up"] = latest_desired_up
    if latest_desired_up is None:
        blockers.append("profile_distribution_pressure_too_balanced")
    elif latest_desired_up != _is_up_outcome(row.get("outcome")):
        blockers.append("profile_distribution_side_mismatch")
    if spec.metadata.get("profile_distribution_max_top_profile_cost_share") is not None:
        top_share = _optional_price(profile.get("top_profile_cost_share"))
        max_top_share = _optional_price(spec.metadata.get("profile_distribution_max_top_profile_cost_share"))
        metrics["top_profile_cost_share"] = top_share
        metrics["max_top_profile_cost_share"] = max_top_share
        if top_share is None or (max_top_share is not None and top_share > max_top_share):
            blockers.append("profile_distribution_top_profile_concentration_high")
    max_source_age = _optional_price(spec.metadata.get("profile_distribution_max_source_age_seconds"))
    if max_source_age is not None:
        source_age = _optional_price(profile.get("source_age_seconds"))
        metrics["profile_source_age_seconds"] = source_age
        metrics["profile_max_source_age_seconds"] = max_source_age
        if source_age is None or source_age > max_source_age:
            blockers.append("profile_distribution_source_age_high")

    if spec.metadata.get("option_path_required"):
        path = _option_path_signal_context(conn, event_key=event_key, event_slug=event_slug)
        metrics["option_path_ready"] = bool(path.get("option_path_ready"))
        if not path.get("option_path_ready"):
            blockers.append("option_path_context_missing")
        min_snapshots = _optional_price(spec.metadata.get("option_path_min_snapshot_count"))
        if min_snapshots is not None:
            snapshot_count = _optional_price(path.get("snapshot_count"))
            metrics["option_path_snapshot_count"] = snapshot_count
            metrics["option_path_min_snapshot_count"] = min_snapshots
            if snapshot_count is None or snapshot_count < min_snapshots:
                blockers.append("option_path_snapshot_count_low")
        min_range = _optional_price(spec.metadata.get("option_path_min_avg_rolling_60s_range"))
        if min_range is not None:
            avg_range = _optional_price(path.get("avg_rolling_60s_range"))
            metrics["avg_rolling_60s_range"] = avg_range
            metrics["min_avg_rolling_60s_range"] = min_range
            if avg_range is None or avg_range < min_range:
                blockers.append("option_path_avg_rolling_60s_range_low")
        max_pair_sum_range = _optional_price(spec.metadata.get("option_path_max_pair_sum_range"))
        if max_pair_sum_range is not None:
            pair_sum_range = _optional_price(path.get("pair_sum_range"))
            metrics["pair_sum_range"] = pair_sum_range
            metrics["max_pair_sum_range"] = max_pair_sum_range
            if pair_sum_range is None or pair_sum_range > max_pair_sum_range:
                blockers.append("option_path_pair_sum_range_high")

    return tuple(blockers), metrics


def _profile_group_event_preferences(
    conn,
    *,
    selectors: tuple[dict[str, Any], ...],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    if (
        not selectors
        or not table_exists(conn, "profile_distribution_snapshots")
        or not table_exists(conn, "profile_distribution_components")
    ):
        return ()
    condition_sql, params = _profile_group_component_filter(selectors)
    if not condition_sql:
        return ()
    rows = conn.execute(
        f"""
        SELECT
            s.distribution_snapshot_key,
            s.event_key,
            s.event_slug,
            s.computed_at_utc,
            COALESCE(NULLIF(c.grade, ''), 'unknown') AS grade,
            COALESCE(NULLIF(c.trading_style, ''), 'unknown') AS trading_style,
            COALESCE(NULLIF(c.outcome, ''), 'unknown') AS outcome,
            COUNT(*) AS component_count,
            COUNT(DISTINCT c.profile_key) AS profile_count,
            SUM(c.net_shares * c.final_weight) AS weighted_shares,
            SUM(c.cost_basis_usd * c.final_weight) AS weighted_cost
          FROM profile_distribution_snapshots s
          JOIN profile_distribution_components c
            ON c.distribution_snapshot_key = s.distribution_snapshot_key
         WHERE ({condition_sql})
           AND s.event_key IS NOT NULL
         GROUP BY s.distribution_snapshot_key, s.event_key, s.event_slug,
                  s.computed_at_utc, grade, trading_style, outcome
         ORDER BY s.computed_at_utc DESC
         LIMIT ?
        """,
        (*params, max(100, int(limit) * 24)),
    ).fetchall()
    selector_map = {
        (str(selector["group_kind"]), str(selector["group_label"])): selector
        for selector in selectors
    }
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        payload = dict(row)
        outcome = _normalize_profile_outcome(payload.get("outcome"))
        if outcome not in {"up", "down"}:
            continue
        grade = str(payload.get("grade") or "unknown")
        style = str(payload.get("trading_style") or "unknown")
        labels = (
            ("by_grade", grade),
            ("by_style", style),
            ("by_grade_style", f"{grade} / {style}"),
        )
        for group_kind, group_label in labels:
            if (group_kind, group_label) not in selector_map:
                continue
            key = (
                str(payload.get("distribution_snapshot_key") or ""),
                str(payload.get("event_key") or ""),
                str(payload.get("event_slug") or ""),
                group_kind,
                group_label,
            )
            target = grouped.setdefault(
                key,
                {
                    "distribution_snapshot_key": payload.get("distribution_snapshot_key"),
                    "event_key": payload.get("event_key"),
                    "event_slug": payload.get("event_slug"),
                    "computed_at_utc": payload.get("computed_at_utc"),
                    "group_kind": group_kind,
                    "group_label": group_label,
                    "sides": {},
                },
            )
            _accumulate_breakdown_bucket(target["sides"], outcome, payload)
    preferences: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    ordered = sorted(grouped.values(), key=lambda item: str(item.get("computed_at_utc") or ""), reverse=True)
    for item in ordered:
        selector = selector_map.get((str(item.get("group_kind")), str(item.get("group_label"))))
        if selector is None:
            continue
        group_row = _breakdown_row(str(item.get("group_label") or ""), item["sides"])
        desired_up = _desired_up_from_profile_group_row(group_row, selector)
        if desired_up is None:
            continue
        outcome = "Up" if desired_up else "Down"
        dedupe_key = (str(item.get("event_key") or ""), outcome, str(selector["strategy_id"]))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        preferences.append(
            {
                "event_key": item.get("event_key"),
                "event_slug": item.get("event_slug"),
                "outcome": outcome,
                "strategy_id": selector["strategy_id"],
                "group_kind": selector["group_kind"],
                "group_label": selector["group_label"],
                "group": group_row,
                "computed_at_utc": item.get("computed_at_utc"),
            }
        )
        if len(preferences) >= max(1, int(limit)):
            break
    return tuple(preferences)


def _profile_group_component_filter(selectors: tuple[dict[str, Any], ...]) -> tuple[str, tuple[Any, ...]]:
    conditions: list[str] = []
    params: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for selector in selectors:
        group_kind = str(selector.get("group_kind") or "")
        group_label = str(selector.get("group_label") or "")
        if not group_kind or not group_label:
            continue
        key = (group_kind, group_label)
        if key in seen:
            continue
        seen.add(key)
        if group_kind == "by_grade_style":
            grade, style = _split_grade_style_label(group_label)
            conditions.append(
                "(COALESCE(NULLIF(c.grade, ''), 'unknown') = ? "
                "AND COALESCE(NULLIF(c.trading_style, ''), 'unknown') = ?)"
            )
            params.extend((grade, style))
        elif group_kind == "by_grade":
            conditions.append("COALESCE(NULLIF(c.grade, ''), 'unknown') = ?")
            params.append(group_label)
        elif group_kind == "by_style":
            conditions.append("COALESCE(NULLIF(c.trading_style, ''), 'unknown') = ?")
            params.append(group_label)
    return " OR ".join(conditions), tuple(params)


def _split_grade_style_label(label: str) -> tuple[str, str]:
    if " / " in label:
        grade, style = label.split(" / ", 1)
        return grade.strip() or "unknown", style.strip() or "unknown"
    return label.strip() or "unknown", "unknown"


def _profile_group_price_ticks_from_preferences(
    conn,
    *,
    horizon_seconds: float,
    preferences: tuple[dict[str, Any], ...],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for preference in preferences:
        row = _price_tick_with_forward_mark_for_event_outcome(
            conn,
            event_key=str(preference.get("event_key") or ""),
            event_slug=str(preference.get("event_slug") or ""),
            outcome=str(preference.get("outcome") or ""),
            horizon_seconds=horizon_seconds,
        )
        if row is None:
            continue
        key = (str(row.get("event_token_key")), str(row.get("system_received_at_utc")))
        if key in seen:
            continue
        seen.add(key)
        payload = dict(row)
        payload["profile_group_selector_matched_strategy_ids"] = [str(preference.get("strategy_id") or "")]
        payload["profile_group_selector"] = {
            "group_kind": preference.get("group_kind"),
            "group_label": preference.get("group_label"),
            "computed_at_utc": preference.get("computed_at_utc"),
            "group": preference.get("group"),
        }
        selected.append(payload)
        if len(selected) >= max(1, int(limit)):
            break
    return tuple(selected)


def _price_tick_with_forward_mark_for_event_outcome(
    conn,
    *,
    event_key: str,
    event_slug: str,
    outcome: str,
    horizon_seconds: float,
    tolerance_seconds: float = 15.0,
) -> dict[str, Any] | None:
    if not table_exists(conn, "polymarket_price_ticks"):
        return None
    event_key = str(event_key or "")
    event_slug = str(event_slug or "")
    if not event_key and not event_slug:
        return None
    horizon_seconds = max(1.0, float(horizon_seconds))
    tolerance_seconds = max(1.0, float(tolerance_seconds))
    outcome_values = _outcome_sql_values(outcome)
    dialect = _dialect_for_connection(conn)
    forward_delta_seconds = dialect.seconds_between("f.system_received_at_utc", "p.system_received_at_utc")
    row = conn.execute(
        f"""
        WITH candidate_p AS (
            SELECT
                p.event_key,
                p.event_token_key,
                p.token_id,
                p.event_slug,
                p.outcome,
                p.system_received_at_utc,
                p.best_bid,
                p.best_ask,
                p.spread,
                p.depth_top3_bid_size,
                p.depth_top3_ask_size,
                p.mid_price,
                1 AS profile_context_available,
                EXISTS(
                    SELECT 1
                      FROM polymarket_event_path_stats eps
                     WHERE eps.event_key = p.event_key
                ) AS option_path_context_available
              FROM polymarket_price_ticks p
             WHERE ((? != '' AND p.event_key = ?) OR (? != '' AND p.event_slug = ?))
               AND LOWER(COALESCE(p.outcome, '')) IN ({",".join("?" for _ in outcome_values)})
               AND p.event_token_key IS NOT NULL
               AND p.best_ask IS NOT NULL
               AND COALESCE(p.best_ask, p.mid_price) > 0
             ORDER BY p.system_received_at_utc DESC
             LIMIT 240
        )
        SELECT
            p.event_key,
            p.event_token_key,
            p.token_id,
            p.event_slug,
            p.outcome,
            p.system_received_at_utc,
            p.best_bid,
            p.best_ask,
            p.spread,
            p.depth_top3_bid_size,
            p.depth_top3_ask_size,
            p.mid_price,
            f.best_bid AS forward_best_bid,
            f.best_ask AS forward_best_ask,
            f.mid_price AS forward_mid_price,
            f.system_received_at_utc AS forward_mark_at_utc,
            {forward_delta_seconds} AS forward_horizon_seconds,
            p.profile_context_available,
            p.option_path_context_available
          FROM candidate_p p
          JOIN polymarket_price_ticks f
            ON f.event_token_key = p.event_token_key
           AND f.system_received_at_utc > p.system_received_at_utc
           AND f.system_received_at_utc >= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
           AND f.system_received_at_utc <= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
         WHERE COALESCE(f.best_bid, f.mid_price) IS NOT NULL
           AND {forward_delta_seconds} BETWEEN ? AND ?
         ORDER BY p.system_received_at_utc DESC,
                  ABS({forward_delta_seconds} - ?)
         LIMIT 1
        """,
        (
            event_key,
            event_key,
            event_slug,
            event_slug,
            *outcome_values,
            f"+{max(1.0, horizon_seconds - tolerance_seconds):.6f} seconds",
            f"+{horizon_seconds + tolerance_seconds:.6f} seconds",
            max(1.0, horizon_seconds - tolerance_seconds),
            horizon_seconds + tolerance_seconds,
            horizon_seconds,
        ),
    ).fetchone()
    return None if row is None else dict(row)


def _outcome_sql_values(outcome: str) -> tuple[str, ...]:
    if _is_up_outcome(outcome):
        return ("up", "yes", "above", "higher")
    return ("down", "no", "below", "lower")


def _profile_group_selectors(strategy_ids: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    selectors: list[dict[str, Any]] = []
    for strategy_id in strategy_ids:
        try:
            spec = get_strategy(strategy_id)
        except KeyError:
            continue
        group_kind = str(spec.metadata.get("profile_distribution_group_kind") or "").strip()
        group_label = str(spec.metadata.get("profile_distribution_group_label") or "").strip()
        if not group_kind or not group_label:
            continue
        mode = str(spec.metadata.get("profile_direction_mode") or "follow").strip().lower()
        threshold = _optional_price(spec.metadata.get("profile_direction_threshold"))
        selectors.append(
            {
                "strategy_id": strategy_id,
                "group_kind": group_kind,
                "group_label": group_label,
                "direction_mode": "contrarian" if mode == "contrarian" else "follow",
                "threshold": threshold if threshold is not None else 0.08,
                "min_components": int(spec.metadata.get("profile_distribution_min_components") or 1),
                "pair_sum_min": _optional_price(spec.metadata.get("profile_reconstructed_pair_sum_min")),
                "pair_sum_max": _optional_price(spec.metadata.get("profile_reconstructed_pair_sum_max")),
            }
        )
    return tuple(selectors)


def _desired_up_from_profile_group(profile: dict[str, Any], selector: dict[str, Any]) -> bool | None:
    if not profile.get("profile_distribution_ready"):
        return None
    breakdown = profile.get("component_breakdown") if isinstance(profile.get("component_breakdown"), dict) else {}
    group_kind = str(selector.get("group_kind") or "")
    group_label = str(selector.get("group_label") or "")
    group = breakdown.get(group_kind) if isinstance(breakdown.get(group_kind), dict) else {}
    row = group.get(group_label) if isinstance(group.get(group_label), dict) else None
    if not isinstance(row, dict):
        return None
    return _desired_up_from_profile_group_row(row, selector)


def _desired_up_from_profile_group_row(row: dict[str, Any], selector: dict[str, Any]) -> bool | None:
    if int(row.get("component_count") or 0) < int(selector.get("min_components") or 1):
        return None
    pair_sum = _optional_price(row.get("reconstructed_profile_pair_sum"))
    pair_min = _optional_price(selector.get("pair_sum_min"))
    pair_max = _optional_price(selector.get("pair_sum_max"))
    if pair_min is not None and (pair_sum is None or pair_sum < pair_min):
        return None
    if pair_max is not None and (pair_sum is None or pair_sum > pair_max):
        return None
    desired_up = _desired_up_from_ratio(
        _optional_price(row.get("up_pressure_ratio")),
        threshold=float(selector.get("threshold") or 0.08),
    )
    if desired_up is not None and selector.get("direction_mode") == "contrarian":
        desired_up = not desired_up
    return desired_up


def _profile_selector_mode_and_threshold(strategy_ids: tuple[str, ...]) -> tuple[str, float]:
    for strategy_id in strategy_ids:
        try:
            spec = get_strategy(strategy_id)
        except KeyError:
            continue
        if not tuple(spec.signal_inputs.get("profile", ())):
            continue
        mode = str(spec.metadata.get("profile_direction_mode") or "follow").strip().lower()
        threshold = _optional_price(spec.metadata.get("profile_direction_threshold"))
        return ("contrarian" if mode == "contrarian" else "follow", threshold if threshold is not None else 0.08)
    return "follow", 0.08


def _desired_up_from_profile_context(profile: dict[str, Any], *, threshold: float) -> bool | None:
    if not profile.get("profile_distribution_ready"):
        return None
    up_ratio = _optional_price(profile.get("profile_distribution_up_ratio"))
    return _desired_up_from_ratio(up_ratio, threshold=threshold)


def _desired_up_from_ratio(up_ratio: float | None, *, threshold: float) -> bool | None:
    if up_ratio is None:
        return None
    threshold = max(0.0, float(threshold))
    if up_ratio >= 0.5 + threshold:
        return True
    if up_ratio <= 0.5 - threshold:
        return False
    return None


def _is_up_outcome(value: Any) -> bool:
    return str(value or "").strip().lower() in {"up", "yes", "above", "higher"}


def _low_range_no_edge_price_ticks_with_forward_marks(
    conn,
    *,
    horizon_seconds: float,
    limit: int,
    max_avg_rolling_60s_range: float = 0.03,
    max_forward_cashout_edge: float = 0.01,
) -> tuple[dict[str, Any], ...]:
    requested = max(1, int(limit))
    candidate_rows = _latest_price_ticks_with_forward_marks(
        conn,
        horizon_seconds=horizon_seconds,
        limit=max(24, requested * 12),
    )
    selected: list[dict[str, Any]] = []
    for row in candidate_rows:
        option_path = _option_path_signal_context(
            conn,
            event_key=str(row.get("event_key") or ""),
            event_slug=str(row.get("event_slug") or ""),
        )
        if not option_path.get("option_path_ready"):
            continue
        avg_range = _optional_price(option_path.get("avg_rolling_60s_range"))
        if avg_range is None or avg_range > max_avg_rolling_60s_range:
            continue
        best_ask = _optional_price(row.get("best_ask"))
        forward_best_bid = _optional_price(row.get("forward_best_bid"))
        if best_ask is None or forward_best_bid is None:
            continue
        forward_edge = forward_best_bid - best_ask
        if forward_edge > max_forward_cashout_edge:
            continue
        payload = dict(row)
        payload["replay_avg_rolling_60s_range"] = avg_range
        payload["replay_forward_cashout_edge"] = forward_edge
        payload["replay_rebound_flips"] = option_path.get("rebound_direction_flip_count")
        payload["replay_strong_rebounds"] = option_path.get("strong_rebound_touch_count")
        payload["replay_level_crossings"] = option_path.get("level_crossing_count")
        selected.append(payload)
        if len(selected) >= requested:
            break
    return tuple(selected)


def _high_inversion_price_ticks_with_forward_marks(
    conn,
    *,
    horizon_seconds: float,
    limit: int,
    tolerance_seconds: float = 15.0,
    max_per_event: int | None = None,
    recent_first: bool = False,
    hedge_grid_ready: bool = False,
) -> tuple[dict[str, Any], ...]:
    if not table_exists(conn, "polymarket_price_ticks") or not table_exists(conn, "polymarket_event_path_stats"):
        return ()
    horizon_seconds = max(1.0, float(horizon_seconds))
    tolerance_seconds = max(1.0, float(tolerance_seconds))
    requested = max(1, int(limit))
    per_event_cap = max(1, int(max_per_event)) if max_per_event is not None else None
    candidate_limit = _forward_mark_candidate_limit(requested)
    dialect = _dialect_for_connection(conn)
    forward_delta_seconds = dialect.seconds_between("f.system_received_at_utc", "p.system_received_at_utc")
    score_sql = """
                    COALESCE(eps.rebound_direction_flip_count, 0) * 10.0
                  + COALESCE(eps.strong_rebound_touch_count, 0) * 8.0
                  + COALESCE(eps.level_crossing_count, 0) * 3.0
                  + COALESCE(eps.near_50c_sample_count, 0) * 0.5
                  + COALESCE(eps.avg_rolling_60s_range, 0) * 100.0
                  + COALESCE(eps.max_rolling_60s_range, 0) * 75.0
    """
    candidate_order_by = (
        f"p.system_received_at_utc DESC, ({score_sql}) DESC"
        if recent_first
        else f"({score_sql}) DESC, p.system_received_at_utc DESC"
    )
    final_score_sql = """
                p.replay_rebound_flips * 10.0
              + p.replay_strong_rebounds * 8.0
              + p.replay_level_crossings * 3.0
              + p.replay_near_50c_samples * 0.5
              + p.replay_avg_rolling_60s_range * 100.0
              + p.replay_max_rolling_60s_range * 75.0
    """
    hedge_grid_where_sql = ""
    if hedge_grid_ready:
        hedge_grid_where_sql = """
               AND COALESCE(eps.rebound_direction_flip_count, 0) >= 3
               AND COALESCE(eps.level_crossing_count, 0) >= 6
               AND COALESCE(eps.near_50c_sample_count, 0) >= 8
               AND COALESCE(eps.avg_rolling_60s_range, 0) >= 0.03
               AND ABS(COALESCE(eps.avg_pair_depth_pressure, 0)) >= 0.12
               AND COALESCE(eps.pair_sum_range, 999) <= 0.12
               AND COALESCE(p.spread, 999) <= 0.03
               AND COALESCE(p.best_ask, p.mid_price) BETWEEN 0.12 AND 0.72
        """
    final_order_by = (
        f"p.system_received_at_utc DESC, ({final_score_sql}) DESC, ABS({forward_delta_seconds} - ?)"
        if recent_first
        else f"({final_score_sql}) DESC, ABS({forward_delta_seconds} - ?), p.system_received_at_utc DESC"
    )
    rows = conn.execute(
        f"""
        WITH candidate_p AS (
            SELECT
                p.event_key,
                p.event_token_key,
                p.token_id,
                p.event_slug,
                p.outcome,
                p.system_received_at_utc,
                p.best_bid,
                p.best_ask,
                p.spread,
                p.depth_top3_bid_size,
                p.depth_top3_ask_size,
                p.mid_price,
                1 AS option_path_context_available,
                EXISTS(
                    SELECT 1
                      FROM profile_distribution_snapshots s
                     WHERE s.event_key = p.event_key
                ) AS profile_context_available,
                COALESCE(eps.rebound_direction_flip_count, 0) AS replay_rebound_flips,
                COALESCE(eps.strong_rebound_touch_count, 0) AS replay_strong_rebounds,
                COALESCE(eps.level_crossing_count, 0) AS replay_level_crossings,
                COALESCE(eps.near_50c_sample_count, 0) AS replay_near_50c_samples,
                COALESCE(eps.avg_rolling_60s_range, 0) AS replay_avg_rolling_60s_range,
                COALESCE(eps.max_rolling_60s_range, 0) AS replay_max_rolling_60s_range,
                COALESCE(eps.pair_sum_range, NULL) AS replay_pair_sum_range,
                COALESCE(eps.avg_pair_depth_pressure, NULL) AS replay_avg_pair_depth_pressure
              FROM polymarket_price_ticks p
              JOIN polymarket_event_path_stats eps
                ON eps.event_key = p.event_key
             WHERE p.event_token_key IS NOT NULL
               AND p.best_ask IS NOT NULL
               AND COALESCE(p.best_ask, p.mid_price) > 0
               {hedge_grid_where_sql}
             ORDER BY {candidate_order_by}
             LIMIT ?
        )
        SELECT
            p.event_key,
            p.event_token_key,
            p.token_id,
            p.event_slug,
            p.outcome,
            p.system_received_at_utc,
            p.best_bid,
            p.best_ask,
            p.spread,
            p.depth_top3_bid_size,
            p.depth_top3_ask_size,
            p.mid_price,
            f.best_bid AS forward_best_bid,
            f.best_ask AS forward_best_ask,
            f.mid_price AS forward_mid_price,
            f.system_received_at_utc AS forward_mark_at_utc,
            {forward_delta_seconds} AS forward_horizon_seconds,
            p.profile_context_available,
            p.option_path_context_available,
            p.replay_rebound_flips,
            p.replay_strong_rebounds,
            p.replay_level_crossings,
            p.replay_near_50c_samples,
            p.replay_avg_rolling_60s_range,
            p.replay_max_rolling_60s_range,
            p.replay_pair_sum_range,
            p.replay_avg_pair_depth_pressure
          FROM candidate_p p
          JOIN polymarket_price_ticks f
            ON f.event_token_key = p.event_token_key
           AND f.system_received_at_utc > p.system_received_at_utc
           AND f.system_received_at_utc >= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
           AND f.system_received_at_utc <= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
         WHERE COALESCE(f.best_bid, f.mid_price) IS NOT NULL
           AND {forward_delta_seconds} BETWEEN ? AND ?
         ORDER BY {final_order_by}
         LIMIT ?
        """,
        (
            candidate_limit,
            f"+{max(1.0, horizon_seconds - tolerance_seconds):.6f} seconds",
            f"+{horizon_seconds + tolerance_seconds:.6f} seconds",
            max(1.0, horizon_seconds - tolerance_seconds),
            horizon_seconds + tolerance_seconds,
            horizon_seconds,
            min(candidate_limit, requested * (12 if per_event_cap is not None else 3)),
        ),
    ).fetchall()
    unique_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    event_counts: dict[str, int] = {}
    for row in rows:
        payload = dict(row)
        key = (str(payload.get("event_token_key")), str(payload.get("system_received_at_utc")))
        if key in seen:
            continue
        event_key = str(payload.get("event_key") or payload.get("event_slug") or "")
        if per_event_cap is not None and event_counts.get(event_key, 0) >= per_event_cap:
            continue
        seen.add(key)
        event_counts[event_key] = event_counts.get(event_key, 0) + 1
        unique_rows.append(payload)
        if len(unique_rows) >= requested:
            break
    return tuple(unique_rows)


def _hedge_grid_closed_cycle_ready_rows(
    conn,
    rows: tuple[dict[str, Any], ...],
    *,
    limit: int,
    strategy_ids: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    requested = max(1, int(limit))
    spec_metadata = _closed_cycle_strategy_metadata(strategy_ids)
    selected: list[dict[str, Any]] = []
    for row in rows:
        scenario = _scenario_from_price_tick(row, conn)
        preflight = _closed_cycle_preflight_for_scenario(scenario, spec_metadata)
        if preflight["blockers"]:
            continue
        payload = dict(row)
        payload["replay_closed_cycle_count"] = preflight["completed_scalp_cycle_count"]
        payload["replay_closed_cycle_floor"] = preflight["guaranteed_floor_pnl_usd"]
        payload["replay_closed_cycle_profit"] = preflight["completed_scalp_profit_usd"]
        selected.append(payload)
        if len(selected) >= requested:
            break
    return tuple(selected)


def _closed_cycle_strategy_metadata(strategy_ids: tuple[str, ...]) -> dict[str, Any]:
    for strategy_id in strategy_ids:
        try:
            spec = get_strategy(str(strategy_id))
        except KeyError:
            continue
        if bool(spec.metadata.get("paired_seed_scalp_simulation_required")):
            return dict(spec.metadata)
    try:
        return dict(get_strategy("master_hedge_grid_floor_paired_seed_builder_v12").metadata)
    except KeyError:
        return {
            "paired_seed_scalp_buy_drop": 0.04,
            "paired_seed_scalp_target": 0.02,
            "paired_seed_scalp_notional_usd": 0.10,
            "paired_seed_scalp_max_open_per_side": 1,
            "paired_seed_scalp_min_path_snapshots": 2,
            "paired_seed_scalp_entry_window_fraction": 0.67,
            "paired_seed_scalp_min_floor_during_path": -0.04,
        }


def _closed_cycle_preflight_for_scenario(scenario: RuntimeScenario, metadata: dict[str, Any]) -> dict[str, Any]:
    context = scenario.signal_context if isinstance(scenario.signal_context, dict) else {}
    snapshots = context.get("paired_path_snapshots")
    if not isinstance(snapshots, list):
        snapshots = []
    up_entry_ask = _optional_price(context.get("paired_entry_up_ask"))
    down_entry_ask = _optional_price(context.get("paired_entry_down_ask"))
    pair_sum = _optional_price(context.get("paired_entry_pair_sum"))
    if pair_sum is None and up_entry_ask is not None and down_entry_ask is not None:
        pair_sum = up_entry_ask + down_entry_ask
    if pair_sum is None or pair_sum <= 0.0:
        return {"blockers": ["paired_scalp_closed_cycle_pair_sum_missing"]}

    seed_budget = max(0.5, _metadata_price(metadata, "hedge_floor_seed_budget_usd", 2.0))
    equal_shares = seed_budget / max(pair_sum, 0.01)
    realized_cash = -seed_budget
    side_shares = {"up": equal_shares, "down": equal_shares}
    entry_ask = {"up": up_entry_ask, "down": down_entry_ask}
    buy_drop = max(0.0, _metadata_price(metadata, "paired_seed_scalp_buy_drop", 0.03))
    scalp_target = max(0.0, _metadata_price(metadata, "paired_seed_scalp_target", 0.03))
    scalp_notional = max(0.01, _metadata_price(metadata, "paired_seed_scalp_notional_usd", 0.25))
    max_open_per_side = max(1, int(_metadata_price(metadata, "paired_seed_scalp_max_open_per_side", 1)))
    min_path_snapshots = max(2, int(_metadata_price(metadata, "paired_seed_scalp_min_path_snapshots", 2)))
    entry_window_fraction = max(0.0, min(1.0, _metadata_price(metadata, "paired_seed_scalp_entry_window_fraction", 1.0)))
    buy_cutoff_index = max(1, int(len(snapshots) * entry_window_fraction)) if snapshots else 0
    open_positions: dict[str, list[dict[str, float]]] = {"up": [], "down": []}
    completed_cycles = 0
    completed_profit = 0.0
    min_floor = min(realized_cash + side_shares["up"], realized_cash + side_shares["down"])

    for snapshot_index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            continue
        for side in ("up", "down"):
            bid = _optional_price(snapshot.get(f"{side}_bid"))
            if bid is None:
                continue
            retained: list[dict[str, float]] = []
            for position in open_positions[side]:
                buy_price = float(position["price"])
                if bid >= buy_price + scalp_target:
                    sold_shares = float(position["shares"])
                    realized_cash += sold_shares * bid
                    side_shares[side] -= sold_shares
                    completed_cycles += 1
                    completed_profit += sold_shares * (bid - buy_price)
                else:
                    retained.append(position)
            open_positions[side] = retained

        if snapshot_index < buy_cutoff_index:
            for side in ("up", "down"):
                ask = _optional_price(snapshot.get(f"{side}_ask"))
                side_entry = entry_ask.get(side)
                if ask is None or side_entry is None:
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
    open_count = sum(len(items) for items in open_positions.values())
    blockers: list[str] = []
    if len(snapshots) < min_path_snapshots:
        blockers.append("paired_scalp_closed_cycle_path_density_low")
    if completed_cycles <= 0:
        blockers.append("paired_scalp_closed_cycle_no_completed_cycles")
    if final_floor <= 0.0:
        blockers.append("paired_scalp_closed_cycle_floor_not_positive")
    if open_count > 0:
        blockers.append("paired_scalp_closed_cycle_open_positions_remain")
    min_floor_required = _optional_price(metadata.get("paired_seed_scalp_min_floor_during_path"))
    if min_floor_required is not None and min_floor < min_floor_required:
        blockers.append("paired_scalp_closed_cycle_min_floor_too_low")
    return {
        "snapshot_count": len(snapshots),
        "completed_scalp_cycle_count": completed_cycles,
        "completed_scalp_profit_usd": round(float(completed_profit), 8),
        "open_scalp_position_count": open_count,
        "guaranteed_floor_pnl_usd": round(float(final_floor), 8),
        "min_floor_during_path_usd": round(float(min_floor), 8),
        "buy_cutoff_index": buy_cutoff_index,
        "blockers": blockers,
    }


def _metadata_price(metadata: dict[str, Any], key: str, default: float) -> float:
    parsed = _optional_price(metadata.get(key))
    return default if parsed is None else float(parsed)


def _tail_touch_price_ticks_with_forward_marks(
    conn,
    *,
    horizon_seconds: float,
    limit: int,
    tolerance_seconds: float = 15.0,
) -> tuple[dict[str, Any], ...]:
    if not table_exists(conn, "polymarket_price_ticks") or not table_exists(conn, "polymarket_event_path_stats"):
        return ()
    horizon_seconds = max(1.0, float(horizon_seconds))
    tolerance_seconds = max(1.0, float(tolerance_seconds))
    requested = max(1, int(limit))
    candidate_limit = _forward_mark_candidate_limit(requested)
    dialect = _dialect_for_connection(conn)
    forward_delta_seconds = dialect.seconds_between("f.system_received_at_utc", "p.system_received_at_utc")
    event_elapsed_seconds = dialect.seconds_between("p.system_received_at_utc", "eps.event_start_time_utc")
    def json_scalar(path: str) -> str:
        return f"json_extract(eps.tail_comeback_table_json, '{path}')"

    def json_number(path: str) -> str:
        return f"COALESCE(CAST(NULLIF(CAST({json_scalar(path)} AS TEXT), '') AS REAL), 0.0)"

    def json_touch_number(path: str) -> str:
        scalar_text = f"LOWER(COALESCE(CAST({json_scalar(path)} AS TEXT), '0'))"
        return (
            f"(CASE WHEN {scalar_text} IN ('true', '1') THEN 1.0 "
            f"WHEN {scalar_text} IN ('false', '0') THEN 0.0 "
            f"ELSE COALESCE(CAST(NULLIF(CAST({json_scalar(path)} AS TEXT), '') AS REAL), 0.0) END)"
        )

    def max_of_three(first: str, second: str, third: str) -> str:
        return (
            f"(CASE WHEN {first} >= {second} AND {first} >= {third} THEN {first} "
            f"WHEN {second} >= {third} THEN {second} ELSE {third} END)"
        )

    up_1c_touched = json_touch_number("$.sides.up.touched_1c.touched")
    up_5c_touched = json_touch_number("$.sides.up.touched_5c.touched")
    up_10c_touched = json_touch_number("$.sides.up.touched_10c.touched")
    down_1c_touched = json_touch_number("$.sides.down.touched_1c.touched")
    down_5c_touched = json_touch_number("$.sides.down.touched_5c.touched")
    down_10c_touched = json_touch_number("$.sides.down.touched_10c.touched")
    up_1c_max_after = json_number("$.sides.up.touched_1c.max_after_touch")
    up_5c_max_after = json_number("$.sides.up.touched_5c.max_after_touch")
    up_10c_max_after = json_number("$.sides.up.touched_10c.max_after_touch")
    down_1c_max_after = json_number("$.sides.down.touched_1c.max_after_touch")
    down_5c_max_after = json_number("$.sides.down.touched_5c.max_after_touch")
    down_10c_max_after = json_number("$.sides.down.touched_10c.max_after_touch")
    candidate_1c_max_after = (
        f"(CASE WHEN lower(p.outcome) = 'up' THEN {up_1c_max_after} ELSE {down_1c_max_after} END)"
    )
    candidate_5c_max_after = (
        f"(CASE WHEN lower(p.outcome) = 'up' THEN {up_5c_max_after} ELSE {down_5c_max_after} END)"
    )
    candidate_10c_max_after = (
        f"(CASE WHEN lower(p.outcome) = 'up' THEN {up_10c_max_after} ELSE {down_10c_max_after} END)"
    )
    tail_max_after = max_of_three(
        candidate_1c_max_after,
        candidate_5c_max_after,
        candidate_10c_max_after,
    )
    selected_tail_max_after = max_of_three(
        "p.replay_tail_1c_max_after",
        "p.replay_tail_5c_max_after",
        "p.replay_tail_10c_max_after",
    )
    rows = conn.execute(
        f"""
        WITH candidate_p AS (
            SELECT
                p.event_key,
                p.event_token_key,
                p.token_id,
                p.event_slug,
                p.outcome,
                p.system_received_at_utc,
                p.best_bid,
                p.best_ask,
                p.spread,
                p.depth_top3_bid_size,
                p.depth_top3_ask_size,
                p.mid_price,
                1 AS option_path_context_available,
                EXISTS(
                    SELECT 1
                      FROM profile_distribution_snapshots s
                     WHERE s.event_key = p.event_key
                ) AS profile_context_available,
                CASE
                    WHEN lower(p.outcome) = 'up' THEN
                        {up_1c_touched}
                      + {up_5c_touched}
                      + {up_10c_touched}
                    ELSE
                        {down_1c_touched}
                      + {down_5c_touched}
                      + {down_10c_touched}
                END AS replay_tail_touch_count,
                COALESCE(eps.strong_rebound_touch_count, 0) AS replay_strong_rebounds,
                CASE
                    WHEN lower(p.outcome) = 'up' THEN
                        {up_1c_max_after}
                    ELSE
                        {down_1c_max_after}
                END AS replay_tail_1c_max_after,
                CASE
                    WHEN lower(p.outcome) = 'up' THEN
                        {up_5c_max_after}
                    ELSE
                        {down_5c_max_after}
                END AS replay_tail_5c_max_after,
                CASE
                    WHEN lower(p.outcome) = 'up' THEN
                        {up_10c_max_after}
                    ELSE
                        {down_10c_max_after}
                END AS replay_tail_10c_max_after,
                {event_elapsed_seconds} AS event_elapsed_seconds,
                CASE
                    WHEN eps.event_end_time_utc IS NULL THEN NULL
                    ELSE {dialect.seconds_between("eps.event_end_time_utc", "p.system_received_at_utc")}
                END AS time_remaining_seconds
              FROM polymarket_price_ticks p
              JOIN polymarket_event_path_stats eps
                ON eps.event_key = p.event_key
             WHERE p.event_token_key IS NOT NULL
               AND p.best_ask IS NOT NULL
               AND COALESCE(p.best_ask, p.mid_price) BETWEEN 0.01 AND 0.16
               AND eps.tail_comeback_table_json IS NOT NULL
               AND eps.tail_comeback_table_json != '{{}}'
               AND (
                    (lower(p.outcome) = 'up' AND (
                        {up_1c_touched} = 1
                     OR {up_5c_touched} = 1
                     OR {up_10c_touched} = 1
                    ))
                 OR (lower(p.outcome) = 'down' AND (
                        {down_1c_touched} = 1
                     OR {down_5c_touched} = 1
                     OR {down_10c_touched} = 1
                    ))
               )
             ORDER BY replay_tail_touch_count DESC,
                      {tail_max_after} DESC,
                      p.system_received_at_utc DESC
             LIMIT ?
        )
        SELECT
            p.event_key,
            p.event_token_key,
            p.token_id,
            p.event_slug,
            p.outcome,
            p.system_received_at_utc,
            p.best_bid,
            p.best_ask,
            p.spread,
            p.depth_top3_bid_size,
            p.depth_top3_ask_size,
            p.mid_price,
            f.best_bid AS forward_best_bid,
            f.best_ask AS forward_best_ask,
            f.mid_price AS forward_mid_price,
            f.system_received_at_utc AS forward_mark_at_utc,
            {forward_delta_seconds} AS forward_horizon_seconds,
            p.profile_context_available,
            p.option_path_context_available,
            p.replay_tail_touch_count,
            p.replay_strong_rebounds,
            p.replay_tail_1c_max_after,
            p.replay_tail_5c_max_after,
            p.replay_tail_10c_max_after,
            p.event_elapsed_seconds,
            p.time_remaining_seconds
          FROM candidate_p p
          JOIN polymarket_price_ticks f
            ON f.event_token_key = p.event_token_key
           AND f.system_received_at_utc > p.system_received_at_utc
           AND f.system_received_at_utc >= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
           AND f.system_received_at_utc <= strftime('%Y-%m-%dT%H:%M:%f+00:00', p.system_received_at_utc, ?)
         WHERE COALESCE(f.best_bid, f.mid_price) IS NOT NULL
           AND {forward_delta_seconds} BETWEEN ? AND ?
         ORDER BY p.replay_tail_touch_count DESC,
                  {selected_tail_max_after} DESC,
                  ABS({forward_delta_seconds} - ?),
                  p.system_received_at_utc DESC
         LIMIT ?
        """,
        (
            candidate_limit,
            f"+{max(1.0, horizon_seconds - tolerance_seconds):.6f} seconds",
            f"+{horizon_seconds + tolerance_seconds:.6f} seconds",
            max(1.0, horizon_seconds - tolerance_seconds),
            horizon_seconds + tolerance_seconds,
            horizon_seconds,
            requested * 3,
        ),
    ).fetchall()
    unique_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        payload = dict(row)
        key = (str(payload.get("event_token_key")), str(payload.get("system_received_at_utc")))
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(payload)
        if len(unique_rows) >= requested:
            break
    return tuple(unique_rows)


def _filter_tail_touch_rows_by_forward_edge(
    rows: tuple[dict[str, Any], ...],
    *,
    strategy_ids: tuple[str, ...],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    threshold = _tail_touch_forward_edge_threshold(strategy_ids)
    min_strong_rebounds = _tail_touch_min_strong_rebounds(strategy_ids)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        best_ask = _optional_price(row.get("best_ask"))
        forward_best_bid = _optional_price(row.get("forward_best_bid"))
        if best_ask is None or forward_best_bid is None:
            continue
        if (forward_best_bid - best_ask) + 1e-9 < threshold:
            continue
        strong_rebounds = _optional_price(row.get("replay_strong_rebounds")) or 0.0
        if strong_rebounds + 1e-9 < min_strong_rebounds:
            continue
        filtered.append(row)
        if len(filtered) >= max(1, int(limit)):
            break
    return tuple(filtered)


def _cached_tail_touch_candidate_rows(
    conn,
    *,
    selector: str,
    strategy_ids: tuple[str, ...],
    horizon_seconds: float,
    limit: int,
    max_age_seconds: float = 300.0,
) -> tuple[dict[str, Any], ...] | None:
    if not (
        table_exists(conn, "strategy_replay_candidate_cache_runs")
        and table_exists(conn, "strategy_replay_candidate_scenarios")
    ):
        return None
    signature = _strategy_signature(strategy_ids)
    row = conn.execute(
        """
        SELECT candidate_cache_run_key, generated_at_utc, candidate_count, status
        FROM strategy_replay_candidate_cache_runs
        WHERE selector = ?
          AND strategy_signature = ?
          AND ABS(forward_mark_horizon_seconds - ?) < 0.001
        ORDER BY generated_at_utc DESC
        LIMIT 1
        """,
        (selector, signature, float(horizon_seconds)),
    ).fetchone()
    if row is None:
        return None
    generated_at = _parse_utc_timestamp(dict(row).get("generated_at_utc"))
    if generated_at is None:
        return None
    age_seconds = (datetime.now(UTC) - generated_at).total_seconds()
    if age_seconds > max(1.0, float(max_age_seconds)):
        return None
    if int(dict(row).get("candidate_count") or 0) <= 0:
        return ()
    rows = conn.execute(
        """
        SELECT
            event_key,
            event_token_key,
            token_id,
            event_slug,
            outcome,
            system_received_at_utc,
            best_bid,
            best_ask,
            spread,
            depth_top3_bid_size,
            depth_top3_ask_size,
            mid_price,
            forward_best_bid,
            forward_best_ask,
            forward_mid_price,
            forward_mark_at_utc,
            forward_horizon_seconds,
            profile_context_available,
            option_path_context_available,
            replay_tail_touch_count,
            replay_strong_rebounds,
            replay_tail_1c_max_after,
            replay_tail_5c_max_after,
            replay_tail_10c_max_after,
            event_elapsed_seconds,
            time_remaining_seconds
        FROM strategy_replay_candidate_scenarios
        WHERE candidate_cache_run_key = ?
        ORDER BY score DESC, system_received_at_utc DESC
        LIMIT ?
        """,
        (dict(row)["candidate_cache_run_key"], max(1, int(limit))),
    ).fetchall()
    return tuple(dict(candidate) for candidate in rows)


def _persist_tail_touch_candidate_cache(
    conn,
    *,
    selector: str,
    strategy_ids: tuple[str, ...],
    horizon_seconds: float,
    rows: tuple[dict[str, Any], ...],
) -> None:
    if not (
        table_exists(conn, "strategy_replay_candidate_cache_runs")
        and table_exists(conn, "strategy_replay_candidate_scenarios")
    ):
        return
    signature = _strategy_signature(strategy_ids)
    generated_at = datetime.now(UTC).isoformat()
    run_key = f"{selector}:{signature}:{float(horizon_seconds):.3f}:{generated_at}"
    status = "ok" if rows else "empty"
    conn.execute(
        """
        INSERT INTO strategy_replay_candidate_cache_runs(
            candidate_cache_run_key, selector, strategy_signature,
            forward_mark_horizon_seconds, generated_at_utc, candidate_count,
            status, source_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_key,
            selector,
            signature,
            float(horizon_seconds),
            generated_at,
            len(rows),
            status,
            json.dumps(
                {
                    "schema_version": "crypto_options_replay_candidate_cache_run_v1",
                    "strategy_ids": list(strategy_ids),
                    "selector": selector,
                },
                sort_keys=True,
            ),
        ),
    )
    for index, row in enumerate(rows, start=1):
        conn.execute(
            """
            INSERT INTO strategy_replay_candidate_scenarios(
                candidate_key, candidate_cache_run_key, selector, strategy_signature,
                event_key, event_token_key, token_id, event_slug, outcome,
                system_received_at_utc, best_bid, best_ask, spread,
                depth_top3_bid_size, depth_top3_ask_size, mid_price,
                forward_best_bid, forward_best_ask, forward_mid_price,
                forward_mark_at_utc, forward_horizon_seconds,
                profile_context_available, option_path_context_available,
                replay_tail_touch_count, replay_strong_rebounds,
                replay_tail_1c_max_after, replay_tail_5c_max_after,
                replay_tail_10c_max_after, event_elapsed_seconds,
                time_remaining_seconds, score, source_json, inserted_at_utc
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{run_key}:{index}",
                run_key,
                selector,
                signature,
                row.get("event_key"),
                row.get("event_token_key"),
                row.get("token_id"),
                row.get("event_slug"),
                row.get("outcome"),
                row.get("system_received_at_utc"),
                row.get("best_bid"),
                row.get("best_ask"),
                row.get("spread"),
                row.get("depth_top3_bid_size"),
                row.get("depth_top3_ask_size"),
                row.get("mid_price"),
                row.get("forward_best_bid"),
                row.get("forward_best_ask"),
                row.get("forward_mid_price"),
                row.get("forward_mark_at_utc"),
                row.get("forward_horizon_seconds"),
                1 if row.get("profile_context_available") else 0,
                1 if row.get("option_path_context_available") else 0,
                row.get("replay_tail_touch_count"),
                row.get("replay_strong_rebounds"),
                row.get("replay_tail_1c_max_after"),
                row.get("replay_tail_5c_max_after"),
                row.get("replay_tail_10c_max_after"),
                row.get("event_elapsed_seconds"),
                row.get("time_remaining_seconds"),
                _tail_touch_candidate_score(row),
                json.dumps({"source": selector}, sort_keys=True),
                generated_at,
            ),
        )


def _strategy_signature(strategy_ids: tuple[str, ...]) -> str:
    return ",".join(sorted(str(strategy_id) for strategy_id in strategy_ids))


def _tail_touch_candidate_score(row: dict[str, Any]) -> float:
    touch_count = _optional_price(row.get("replay_tail_touch_count")) or 0.0
    strong_rebounds = _optional_price(row.get("replay_strong_rebounds")) or 0.0
    best_ask = _optional_price(row.get("best_ask")) or 0.0
    forward_best_bid = _optional_price(row.get("forward_best_bid")) or 0.0
    forward_edge = max(0.0, forward_best_bid - best_ask)
    return touch_count * 10.0 + strong_rebounds * 8.0 + forward_edge * 100.0


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _tail_touch_forward_edge_threshold(strategy_ids: tuple[str, ...]) -> float:
    thresholds: list[float] = []
    for strategy_id in strategy_ids:
        try:
            spec = get_strategy(str(strategy_id))
        except KeyError:
            continue
        threshold = _optional_price(spec.metadata.get("option_path_min_forward_cashout_edge"))
        if threshold is not None:
            thresholds.append(float(threshold))
    return max(thresholds) if thresholds else 0.03


def _tail_touch_min_strong_rebounds(strategy_ids: tuple[str, ...]) -> float:
    thresholds: list[float] = []
    for strategy_id in strategy_ids:
        try:
            spec = get_strategy(str(strategy_id))
        except KeyError:
            continue
        threshold = _optional_price(spec.metadata.get("option_path_min_strong_rebound_touch_count"))
        if threshold is not None:
            thresholds.append(float(threshold))
    return max(thresholds) if thresholds else 0.0


def _forward_mark_candidate_limit(requested: int) -> int:
    # Keep the forward-mark self-join bounded against the continuously growing
    # price-tick table while leaving enough recent rows for 60s+ horizons.
    requested = max(1, int(requested))
    return max(500, min(10_000, requested * 250))


def _scenario_from_replay_frame(row: dict[str, Any]) -> RuntimeScenario:
    frame = _json_load(row.get("frame_json"), {})
    market_state = frame.get("market_state") if isinstance(frame.get("market_state"), dict) else {}
    best_ask = _coerce_price(market_state.get("best_ask"), default=0.51)
    best_bid = _coerce_price(market_state.get("best_bid"), default=max(0.0, best_ask - 0.01))
    spread = _coerce_price(market_state.get("spread"), default=max(0.0, best_ask - best_bid))
    return RuntimeScenario(
        event_key=str(row.get("event_key") or frame.get("event_key") or "live-replay-event"),
        event_token_key=str(row.get("event_token_key") or frame.get("event_token_key") or "live-replay-event:up"),
        token_id=str(frame.get("token_id") or "live-replay-token"),
        event_slug=str(frame.get("event_slug") or row.get("event_key") or "live-replay-event"),
        outcome=str(frame.get("outcome") or "Up"),
        shares=1.0,
        limit_price=best_ask,
        spread=spread,
        liquidity_depth=_coerce_price(market_state.get("depth_top3_ask_size"), default=20.0),
        time_remaining_seconds=_coerce_price(frame.get("time_remaining_seconds"), default=240.0),
        signal_context={
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": _coerce_price(market_state.get("mid_price"), default=(best_bid + best_ask) / 2.0),
            "depth_top3_bid_size": _coerce_price(market_state.get("depth_top3_bid_size"), default=20.0),
            "depth_top3_ask_size": _coerce_price(market_state.get("depth_top3_ask_size"), default=20.0),
            "system_received_at_utc": market_state.get("system_received_at_utc"),
            "target_up_ratio": _coerce_price(frame.get("target_up_ratio"), default=0.62),
            "source": "strategy_live_replay_from_replay_frame",
        },
    )


def _scenario_from_price_tick(row: dict[str, Any], conn=None) -> RuntimeScenario:
    best_ask = _coerce_price(row.get("best_ask"), default=_coerce_price(row.get("mid_price"), default=0.51))
    best_bid = _coerce_price(row.get("best_bid"), default=max(0.0, best_ask - 0.01))
    spread = _coerce_price(row.get("spread"), default=max(0.0, best_ask - best_bid))
    forward_mark_price = _optional_price(row.get("forward_best_bid"))
    if forward_mark_price is None:
        forward_mark_price = _optional_price(row.get("forward_mid_price"))
    signal_context = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": _optional_price(row.get("mid_price")),
        "depth_top3_bid_size": _optional_price(row.get("depth_top3_bid_size")),
        "depth_top3_ask_size": _optional_price(row.get("depth_top3_ask_size")),
        "system_received_at_utc": row.get("system_received_at_utc"),
        "forward_mark_price": forward_mark_price,
        "forward_best_bid": _optional_price(row.get("forward_best_bid")),
        "forward_best_ask": _optional_price(row.get("forward_best_ask")),
        "forward_mid_price": _optional_price(row.get("forward_mid_price")),
        "forward_mark_at_utc": row.get("forward_mark_at_utc"),
        "forward_mark_horizon_seconds": _optional_price(row.get("forward_horizon_seconds")),
        "replay_tail_touch_count": _optional_price(row.get("replay_tail_touch_count")),
        "replay_strong_rebounds": _optional_price(row.get("replay_strong_rebounds")),
        "replay_tail_1c_max_after": _optional_price(row.get("replay_tail_1c_max_after")),
        "replay_tail_5c_max_after": _optional_price(row.get("replay_tail_5c_max_after")),
        "replay_tail_10c_max_after": _optional_price(row.get("replay_tail_10c_max_after")),
        "event_elapsed_seconds": _optional_price(row.get("event_elapsed_seconds")),
        "target_up_ratio": 0.62,
        "source": "strategy_live_replay_from_price_tick",
    }
    if conn is not None:
        signal_context.update(_source_context_for_price_tick(conn, row, default_target_up_ratio=0.62))
    return RuntimeScenario(
        event_key=str(row.get("event_key") or "live-replay-event"),
        event_token_key=str(row.get("event_token_key") or "live-replay-event:up"),
        token_id=str(row.get("token_id") or "live-replay-token"),
        event_slug=str(row.get("event_slug") or row.get("event_key") or "live-replay-event"),
        outcome=str(row.get("outcome") or "Up"),
        shares=1.0,
        limit_price=best_ask,
        spread=spread,
        liquidity_depth=_coerce_price(row.get("depth_top3_ask_size"), default=20.0),
        time_remaining_seconds=_coerce_price(row.get("time_remaining_seconds"), default=240.0),
        signal_context=signal_context,
    )


def _source_context_for_price_tick(conn, row: dict[str, Any], *, default_target_up_ratio: float) -> dict[str, Any]:
    event_key = str(row.get("event_key") or "")
    event_slug = str(row.get("event_slug") or "")
    symbol = str(row.get("event_slug") or "").split("-", 1)[0].upper()
    if not symbol or symbol == str(row.get("event_slug") or "").upper():
        symbol = "BTC" if "btc" in event_slug.lower() else "ETH" if "eth" in event_slug.lower() else ""
    profile_context = _profile_distribution_signal_context(conn, event_key=event_key, event_slug=event_slug)
    crypto_context = _crypto_observer_signal_context(conn, symbol=symbol)
    option_path_context = _option_path_signal_context(conn, event_key=event_key, event_slug=event_slug)
    pair_context = _paired_market_signal_context(conn, row)
    target_up_ratio = profile_context.get("profile_distribution_up_ratio")
    if target_up_ratio is None:
        target_up_ratio = default_target_up_ratio
    pair_sum_hint = profile_context.get("reconstructed_profile_pair_sum")
    paired_opposite_ask = pair_context.get("paired_opposite_ask")
    outcome_is_up = str(row.get("outcome") or "").strip().lower() == "up"
    if pair_context.get("paired_entry_pair_sum") is not None:
        pair_sum_hint = pair_context.get("paired_entry_pair_sum")
    if paired_opposite_ask is None:
        paired_opposite_ask = (
            profile_context.get("down_reconstructed_profile_price")
            if outcome_is_up
            else profile_context.get("up_reconstructed_profile_price")
        )
    payload = {
        "target_up_ratio": target_up_ratio,
        "pair_sum_hint": pair_sum_hint,
        "paired_opposite_ask": paired_opposite_ask,
        "paired_down_ask": paired_opposite_ask,
        "profile_distribution_ready": bool(profile_context.get("profile_distribution_ready")),
        "profile_distribution": profile_context,
        "crypto_observer_ready": bool(crypto_context.get("crypto_observer_ready")),
        "crypto_observer": crypto_context,
        "option_path_ready": bool(option_path_context.get("option_path_ready")),
        "option_path": option_path_context,
        "abc_context_version": "strategy_live_replay_abc_context_v2",
    }
    payload.update(pair_context)
    return payload


def _paired_market_signal_context(conn, row: dict[str, Any]) -> dict[str, Any]:
    if not table_exists(conn, "polymarket_updown_pair_snapshots"):
        return {"paired_market_snapshot_ready": False, "paired_forward_snapshot_ready": False}
    event_key = str(row.get("event_key") or "")
    event_slug = str(row.get("event_slug") or "")
    current_snapshot = _nearest_pair_snapshot(
        conn,
        event_key=event_key,
        event_slug=event_slug,
        timestamp=row.get("system_received_at_utc"),
    )
    forward_snapshot = _nearest_pair_snapshot(
        conn,
        event_key=event_key,
        event_slug=event_slug,
        timestamp=row.get("forward_mark_at_utc"),
    )
    outcome_is_up = str(row.get("outcome") or "").strip().lower() == "up"
    payload: dict[str, Any] = {
        "paired_market_snapshot_ready": current_snapshot is not None,
        "paired_forward_snapshot_ready": forward_snapshot is not None,
    }
    if current_snapshot is not None:
        up_ask = _optional_price(current_snapshot.get("up_best_ask"))
        down_ask = _optional_price(current_snapshot.get("down_best_ask"))
        up_bid = _optional_price(current_snapshot.get("up_best_bid"))
        down_bid = _optional_price(current_snapshot.get("down_best_bid"))
        payload.update(
            {
                "paired_entry_snapshot_at_utc": current_snapshot.get("bucket_timestamp_utc"),
                "paired_entry_up_bid": up_bid,
                "paired_entry_up_ask": up_ask,
                "paired_entry_down_bid": down_bid,
                "paired_entry_down_ask": down_ask,
                "paired_entry_pair_sum": None if up_ask is None or down_ask is None else up_ask + down_ask,
                "paired_entry_pair_bid_sum": None if up_bid is None or down_bid is None else up_bid + down_bid,
                "paired_opposite_ask": down_ask if outcome_is_up else up_ask,
            }
        )
    if forward_snapshot is not None:
        forward_up_bid = _optional_price(forward_snapshot.get("up_best_bid"))
        forward_down_bid = _optional_price(forward_snapshot.get("down_best_bid"))
        payload.update(
            {
                "paired_forward_snapshot_at_utc": forward_snapshot.get("bucket_timestamp_utc"),
                "paired_forward_up_bid": forward_up_bid,
                "paired_forward_up_ask": _optional_price(forward_snapshot.get("up_best_ask")),
                "paired_forward_down_bid": forward_down_bid,
                "paired_forward_down_ask": _optional_price(forward_snapshot.get("down_best_ask")),
                "paired_forward_pair_bid_sum": (
                    None if forward_up_bid is None or forward_down_bid is None else forward_up_bid + forward_down_bid
                ),
            }
        )
    payload.update(
        _paired_scalp_path_signal_context(
            conn,
            event_key=event_key,
            event_slug=event_slug,
            start_timestamp=row.get("system_received_at_utc"),
            end_timestamp=row.get("forward_mark_at_utc"),
        )
    )
    return payload


def _paired_scalp_path_signal_context(
    conn,
    *,
    event_key: str,
    event_slug: str,
    start_timestamp: Any,
    end_timestamp: Any,
) -> dict[str, Any]:
    if not start_timestamp or not end_timestamp:
        return {"paired_scalp_path_ready": False, "paired_path_snapshots": []}
    rows = conn.execute(
        """
        SELECT bucket_timestamp_utc, up_best_bid, up_best_ask, down_best_bid, down_best_ask
          FROM polymarket_updown_pair_snapshots
         WHERE ((? != '' AND event_key = ?) OR (? != '' AND event_slug = ?))
           AND bucket_timestamp_utc > ?
           AND bucket_timestamp_utc <= ?
         ORDER BY bucket_timestamp_utc ASC
         LIMIT 240
        """,
        (event_key, event_key, event_slug, event_slug, str(start_timestamp), str(end_timestamp)),
    ).fetchall()
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        snapshots.append(
            {
                "timestamp_utc": item.get("bucket_timestamp_utc"),
                "up_bid": _optional_price(item.get("up_best_bid")),
                "up_ask": _optional_price(item.get("up_best_ask")),
                "down_bid": _optional_price(item.get("down_best_bid")),
                "down_ask": _optional_price(item.get("down_best_ask")),
            }
        )
    up_bids = [float(item["up_bid"]) for item in snapshots if item.get("up_bid") is not None]
    up_asks = [float(item["up_ask"]) for item in snapshots if item.get("up_ask") is not None]
    down_bids = [float(item["down_bid"]) for item in snapshots if item.get("down_bid") is not None]
    down_asks = [float(item["down_ask"]) for item in snapshots if item.get("down_ask") is not None]
    return {
        "paired_scalp_path_ready": len(snapshots) >= 2,
        "paired_path_snapshot_count": len(snapshots),
        "paired_path_min_up_ask": min(up_asks) if up_asks else None,
        "paired_path_max_up_bid": max(up_bids) if up_bids else None,
        "paired_path_min_down_ask": min(down_asks) if down_asks else None,
        "paired_path_max_down_bid": max(down_bids) if down_bids else None,
        "paired_path_snapshots": snapshots,
    }


def _nearest_pair_snapshot(conn, *, event_key: str, event_slug: str, timestamp: Any) -> dict[str, Any] | None:
    params: tuple[Any, ...]
    if timestamp:
        rows = conn.execute(
            """
            SELECT *
              FROM polymarket_updown_pair_snapshots
             WHERE ((? != '' AND event_key = ?) OR (? != '' AND event_slug = ?))
               AND bucket_timestamp_utc <= ?
             ORDER BY bucket_timestamp_utc DESC
             LIMIT 1
            """,
            (event_key, event_key, event_slug, event_slug, str(timestamp)),
        ).fetchall()
        if rows:
            return dict(rows[0])
    rows = conn.execute(
        """
        SELECT *
          FROM polymarket_updown_pair_snapshots
         WHERE (? != '' AND event_key = ?) OR (? != '' AND event_slug = ?)
         ORDER BY bucket_timestamp_utc DESC
         LIMIT 1
        """,
        (event_key, event_key, event_slug, event_slug),
    ).fetchall()
    return dict(rows[0]) if rows else None


def _profile_distribution_signal_context(conn, *, event_key: str, event_slug: str) -> dict[str, Any]:
    if not table_exists(conn, "profile_distribution_snapshots"):
        return {"profile_distribution_ready": False, "blockers": ["profile_distribution_table_missing"]}
    row = conn.execute(
        """
        SELECT distribution_snapshot_key, event_key, event_slug, computed_at_utc, source_mode,
               canonical_method, profile_count, component_count, up_weight, down_weight,
               up_share_weight, down_share_weight, up_cost_weight, down_cost_weight,
               distribution_json, source_json, blockers_json
          FROM profile_distribution_snapshots
         WHERE (? != '' AND event_key = ?)
            OR (? != '' AND event_slug = ?)
         ORDER BY computed_at_utc DESC
         LIMIT 1
        """,
        (event_key, event_key, event_slug, event_slug),
    ).fetchone()
    if row is None:
        return {"profile_distribution_ready": False, "blockers": ["profile_distribution_missing_for_event"]}
    payload = dict(row)
    distribution = _json_load(payload.get("distribution_json"), {})
    source = _json_load(payload.get("source_json"), {})
    blockers = _json_load(payload.get("blockers_json"), [])
    variants = distribution.get("variants") if isinstance(distribution.get("variants"), dict) else {}
    canonical = variants.get(str(payload.get("canonical_method") or "cost_weighted"))
    if not isinstance(canonical, dict):
        canonical = distribution.get("top_profiles_distribution") if isinstance(distribution.get("top_profiles_distribution"), dict) else {}
    up_ratio = _optional_price(canonical.get("up"))
    down_ratio = _optional_price(canonical.get("down"))
    prices = distribution.get("reconstructed_profile_prices") if isinstance(distribution.get("reconstructed_profile_prices"), dict) else {}
    coverage_warnings = [
        str(item).strip()
        for item in (distribution.get("coverage_warnings") if isinstance(distribution.get("coverage_warnings"), list) else [])
        if str(item).strip()
    ]
    cost_variant = variants.get("cost_weighted") if isinstance(variants.get("cost_weighted"), dict) else {}
    shares_variant = variants.get("shares_weighted") if isinstance(variants.get("shares_weighted"), dict) else {}
    count_variant = variants.get("profile_count_weighted") if isinstance(variants.get("profile_count_weighted"), dict) else {}
    cost_up_ratio = _optional_price(cost_variant.get("up"))
    shares_up_ratio = _optional_price(shares_variant.get("up"))
    count_up_ratio = _optional_price(count_variant.get("up"))
    breakdown = _profile_distribution_component_breakdown(
        conn,
        distribution_snapshot_key=str(payload.get("distribution_snapshot_key") or ""),
    )
    return {
        "profile_distribution_ready": not blockers and up_ratio is not None and down_ratio is not None,
        "distribution_snapshot_key": payload.get("distribution_snapshot_key"),
        "computed_at_utc": payload.get("computed_at_utc"),
        "source_mode": payload.get("source_mode"),
        "canonical_method": payload.get("canonical_method"),
        "profile_count": payload.get("profile_count"),
        "component_count": payload.get("component_count"),
        "profile_distribution_up_ratio": up_ratio,
        "profile_distribution_down_ratio": down_ratio,
        "pressure_delta": _optional_price(distribution.get("pressure_delta")),
        "source_age_seconds": _optional_price(distribution.get("source_age_seconds")),
        "target_refresh_seconds": _optional_price(distribution.get("target_refresh_seconds")) or 30.0,
        "latest_source_at_utc": distribution.get("latest_source_at_utc"),
        "up_reconstructed_profile_price": _optional_price(prices.get("up")),
        "down_reconstructed_profile_price": _optional_price(prices.get("down")),
        "reconstructed_profile_pair_sum": _optional_price(prices.get("pair_sum")),
        "coverage_warnings": coverage_warnings,
        "cost_weighted_up_ratio": cost_up_ratio,
        "shares_weighted_up_ratio": shares_up_ratio,
        "profile_count_weighted_up_ratio": count_up_ratio,
        "cost_vs_shares_up_gap_abs": None if cost_up_ratio is None or shares_up_ratio is None else round(abs(cost_up_ratio - shares_up_ratio), 8),
        "cost_vs_profile_count_up_gap_abs": None if cost_up_ratio is None or count_up_ratio is None else round(abs(cost_up_ratio - count_up_ratio), 8),
        "top_profile_cost_share": _profile_distribution_top_cost_share(
            conn,
            distribution_snapshot_key=str(payload.get("distribution_snapshot_key") or ""),
        ),
        "raw_activity_rows": source.get("raw_activity_rows"),
        "position_rows": source.get("position_rows"),
        "event_order_rows": source.get("event_order_rows"),
        "blockers": blockers,
        "distribution_variants": variants,
        "component_breakdown": breakdown,
    }


def _profile_distribution_component_breakdown(conn, *, distribution_snapshot_key: str) -> dict[str, Any]:
    if not distribution_snapshot_key or not table_exists(conn, "profile_distribution_components"):
        return {"by_grade": {}, "by_style": {}, "by_grade_style": {}}
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(grade, ''), 'unknown') AS grade,
            COALESCE(NULLIF(trading_style, ''), 'unknown') AS trading_style,
            COALESCE(NULLIF(outcome, ''), 'unknown') AS outcome,
            COUNT(*) AS component_count,
            COUNT(DISTINCT profile_key) AS profile_count,
            SUM(net_shares * final_weight) AS weighted_shares,
            SUM(cost_basis_usd * final_weight) AS weighted_cost
          FROM profile_distribution_components
         WHERE distribution_snapshot_key = ?
         GROUP BY grade, trading_style, outcome
        """,
        (distribution_snapshot_key,),
    ).fetchall()
    raw: dict[str, dict[str, dict[str, Any]]] = {"by_grade": {}, "by_style": {}, "by_grade_style": {}}
    for row in rows:
        payload = dict(row)
        outcome = _normalize_profile_outcome(payload.get("outcome"))
        if outcome not in {"up", "down"}:
            continue
        grade = str(payload.get("grade") or "unknown")
        style = str(payload.get("trading_style") or "unknown")
        _accumulate_breakdown_bucket(raw["by_grade"].setdefault(grade, {}), outcome, payload)
        _accumulate_breakdown_bucket(raw["by_style"].setdefault(style, {}), outcome, payload)
        _accumulate_breakdown_bucket(raw["by_grade_style"].setdefault(f"{grade} / {style}", {}), outcome, payload)
    return {
        group: {
            label: _breakdown_row(label, sides)
            for label, sides in sorted(labels.items())
        }
        for group, labels in raw.items()
    }


def _profile_distribution_top_cost_share(conn, *, distribution_snapshot_key: str) -> float | None:
    if not distribution_snapshot_key or not table_exists(conn, "profile_distribution_components"):
        return None
    rows = conn.execute(
        """
        SELECT profile_key, SUM(cost_basis_usd * final_weight) AS weighted_cost
          FROM profile_distribution_components
         WHERE distribution_snapshot_key = ?
         GROUP BY profile_key
        """,
        (distribution_snapshot_key,),
    ).fetchall()
    costs = [
        _optional_price(row["weighted_cost"])
        for row in rows
        if _optional_price(row["weighted_cost"]) is not None
    ]
    if not costs:
        return None
    total_cost = sum(costs)
    if total_cost <= 0:
        return None
    return round(max(costs) / total_cost, 8)


def _normalize_profile_outcome(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("up") or text in {"yes", "long"}:
        return "up"
    if text.startswith("down") or text in {"no", "short"}:
        return "down"
    return "unknown"


def _accumulate_breakdown_bucket(target: dict[str, Any], outcome: str, row: dict[str, Any]) -> None:
    bucket = target.setdefault(
        outcome,
        {
            "component_count": 0,
            "profile_count": 0,
            "weighted_shares": 0.0,
            "weighted_cost": 0.0,
        },
    )
    bucket["component_count"] += int(row.get("component_count") or 0)
    bucket["profile_count"] += int(row.get("profile_count") or 0)
    bucket["weighted_shares"] += float(row.get("weighted_shares") or 0.0)
    bucket["weighted_cost"] += float(row.get("weighted_cost") or 0.0)


def _breakdown_row(label: str, sides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    up = sides.get("up", {})
    down = sides.get("down", {})
    up_cost = float(up.get("weighted_cost") or 0.0)
    down_cost = float(down.get("weighted_cost") or 0.0)
    up_shares = float(up.get("weighted_shares") or 0.0)
    down_shares = float(down.get("weighted_shares") or 0.0)
    total_cost = up_cost + down_cost
    up_ratio = up_cost / total_cost if total_cost > 0 else None
    down_ratio = down_cost / total_cost if total_cost > 0 else None
    up_price = _weighted_profile_price(up_cost, up_shares)
    down_price = _weighted_profile_price(down_cost, down_shares)
    return {
        "label": label,
        "component_count": int(up.get("component_count") or 0) + int(down.get("component_count") or 0),
        "profile_count": int(up.get("profile_count") or 0) + int(down.get("profile_count") or 0),
        "up_pressure_ratio": up_ratio,
        "down_pressure_ratio": down_ratio,
        "pressure_delta": None if up_ratio is None or down_ratio is None else up_ratio - down_ratio,
        "up_reconstructed_profile_price": up_price,
        "down_reconstructed_profile_price": down_price,
        "reconstructed_profile_pair_sum": None if up_price is None or down_price is None else up_price + down_price,
    }


def _weighted_profile_price(cost: float, shares: float) -> float | None:
    if abs(shares) <= 1e-9:
        return None
    return cost / abs(shares)


def _crypto_observer_signal_context(conn, *, symbol: str) -> dict[str, Any]:
    if not symbol or not table_exists(conn, "external_technical_observer_snapshots"):
        return {"crypto_observer_ready": False, "blockers": ["crypto_observer_missing_symbol_or_table"]}
    rows = conn.execute(
        """
        SELECT provider, symbol, interval, completed_at_utc, summary_label,
               summary_score, buy_count, sell_count, neutral_count, component_count
          FROM v_crypto_options_app_latest_external_technical_observers
         WHERE symbol = ?
         ORDER BY completed_at_utc DESC
         LIMIT 20
        """,
        (symbol,),
    ).fetchall()
    summaries = [dict(row) for row in rows]
    return {
        "crypto_observer_ready": bool(summaries),
        "symbol": symbol,
        "summary_count": len(summaries),
        "intervals": sorted({str(row.get("interval")) for row in summaries if row.get("interval")}),
        "latest_completed_at_utc": summaries[0].get("completed_at_utc") if summaries else None,
        "summaries": summaries[:8],
        "blockers": [] if summaries else ["crypto_observer_summary_missing"],
    }


def _option_path_signal_context(conn, *, event_key: str, event_slug: str) -> dict[str, Any]:
    if not table_exists(conn, "polymarket_event_path_stats"):
        return {"option_path_ready": False, "blockers": ["option_path_stats_table_missing"]}
    row = conn.execute(
        """
        SELECT event_path_stats_key, event_key, event_slug, symbol, computed_at_utc,
               snapshot_count, up_first_price, up_last_price, up_min_price,
               up_max_price, up_range, up_abs_move_sum, up_abs_move_per_minute,
               up_stddev, path_direction, path_efficiency,
               tail_comeback_table_json,
               time_to_first_extreme_seconds, avg_swing_distance,
               max_swing_distance, avg_rolling_30s_range, max_rolling_30s_range,
               avg_rolling_60s_range, max_rolling_60s_range,
               level_crossing_count, near_50c_sample_count, extreme_sample_count,
               rebound_direction_flip_count, strong_rebound_touch_count,
               pair_sum_range, avg_pair_depth_pressure, avg_source_latency_ms,
               max_source_latency_ms, trade_print_count
          FROM polymarket_event_path_stats
         WHERE (? != '' AND event_key = ?)
            OR (? != '' AND event_slug = ?)
         ORDER BY computed_at_utc DESC
         LIMIT 1
        """,
        (event_key, event_key, event_slug, event_slug),
    ).fetchone()
    if row is None:
        return {"option_path_ready": False, "blockers": ["option_path_stats_missing_for_event"]}
    payload = dict(row)
    snapshot_count = int(payload.get("snapshot_count") or 0)
    blockers = []
    if snapshot_count <= 0:
        blockers.append("option_path_stats_empty")
    return {
        "option_path_ready": not blockers,
        "event_path_stats_key": payload.get("event_path_stats_key"),
        "event_key": payload.get("event_key"),
        "event_slug": payload.get("event_slug"),
        "symbol": payload.get("symbol"),
        "computed_at_utc": payload.get("computed_at_utc"),
        "snapshot_count": snapshot_count,
        "up_first_price": _optional_price(payload.get("up_first_price")),
        "up_last_price": _optional_price(payload.get("up_last_price")),
        "up_min_price": _optional_price(payload.get("up_min_price")),
        "up_max_price": _optional_price(payload.get("up_max_price")),
        "up_range": _optional_price(payload.get("up_range")),
        "up_abs_move_sum": _optional_price(payload.get("up_abs_move_sum")),
        "up_abs_move_per_minute": _optional_price(payload.get("up_abs_move_per_minute")),
        "up_stddev": _optional_price(payload.get("up_stddev")),
        "path_direction": payload.get("path_direction"),
        "path_efficiency": _optional_price(payload.get("path_efficiency")),
        "tail_comeback_table": _json_load(payload.get("tail_comeback_table_json"), {}),
        "time_to_first_extreme_seconds": _optional_price(payload.get("time_to_first_extreme_seconds")),
        "avg_swing_distance": _optional_price(payload.get("avg_swing_distance")),
        "max_swing_distance": _optional_price(payload.get("max_swing_distance")),
        "avg_rolling_30s_range": _optional_price(payload.get("avg_rolling_30s_range")),
        "max_rolling_30s_range": _optional_price(payload.get("max_rolling_30s_range")),
        "avg_rolling_60s_range": _optional_price(payload.get("avg_rolling_60s_range")),
        "max_rolling_60s_range": _optional_price(payload.get("max_rolling_60s_range")),
        "level_crossing_count": int(payload.get("level_crossing_count") or 0),
        "near_50c_sample_count": int(payload.get("near_50c_sample_count") or 0),
        "extreme_sample_count": int(payload.get("extreme_sample_count") or 0),
        "rebound_direction_flip_count": int(payload.get("rebound_direction_flip_count") or 0),
        "strong_rebound_touch_count": int(payload.get("strong_rebound_touch_count") or 0),
        "pair_sum_range": _optional_price(payload.get("pair_sum_range")),
        "avg_pair_depth_pressure": _optional_price(payload.get("avg_pair_depth_pressure")),
        "avg_source_latency_ms": _optional_price(payload.get("avg_source_latency_ms")),
        "max_source_latency_ms": _optional_price(payload.get("max_source_latency_ms")),
        "trade_print_count": int(payload.get("trade_print_count") or 0),
        "blockers": blockers,
    }


def _scenario_payload(scenario: RuntimeScenario, *, source: str, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source": source,
        "event_key": scenario.event_key,
        "event_token_key": scenario.event_token_key,
        "event_slug": scenario.event_slug,
        "outcome": scenario.outcome,
        "limit_price": scenario.limit_price,
        "spread": scenario.spread,
        "time_remaining_seconds": scenario.time_remaining_seconds,
        "forward_mark_price": scenario.signal_context.get("forward_mark_price"),
        "forward_mark_at_utc": scenario.signal_context.get("forward_mark_at_utc"),
        "forward_mark_horizon_seconds": scenario.signal_context.get("forward_mark_horizon_seconds"),
        "target_up_ratio": scenario.signal_context.get("target_up_ratio"),
        "profile_distribution_ready": scenario.signal_context.get("profile_distribution_ready"),
        "crypto_observer_ready": scenario.signal_context.get("crypto_observer_ready"),
        "option_path_ready": scenario.signal_context.get("option_path_ready"),
    }


def _fixture_scenario() -> RuntimeScenario:
    return RuntimeScenario(
        event_key="fixture-live-replay-event",
        event_token_key="fixture-live-replay-event:up",
        token_id="fixture-live-replay-token",
        event_slug="fixture-live-replay-event",
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
                "profile_distribution_ready": True,
                "profile_distribution_up_ratio": 0.62,
                "profile_distribution_down_ratio": 0.38,
                "blockers": [],
            },
            "crypto_observer_ready": True,
            "crypto_observer": {
                "crypto_observer_ready": True,
                "summary_count": 1,
                "summaries": [{"interval": "1m", "summary_label": "Buy", "summary_score": 0.2}],
                "blockers": [],
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
            "source": "strategy_live_replay_fixture",
        },
    )


def _shadow_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=False,
    )


def _json_load(value: Any, fallback: Any) -> Any:
    if value in {None, ""}:
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _coerce_price(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _optional_price(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
