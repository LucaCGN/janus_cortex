from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.db.connection import count_rows, table_exists
from crypto_options_app.strategies.readiness import evaluate_pulse_readiness, evaluate_replay_readiness
from crypto_options_app.strategies.registry import all_strategy_specs, get_strategy
from crypto_options_app.strategies.schema import StrategySpec


def sync_strategy_registry(conn: sqlite3.Connection, *, force: bool = False) -> dict[str, int]:
    now = datetime.now(UTC).isoformat()
    specs = all_strategy_specs()
    if not force and _strategy_registry_current(conn, expected_count=len(specs)):
        return {
            "strategy_specs": 0,
            "strategy_versions": 0,
            "strategy_readiness": 0,
        }
    counts = {
        "strategy_specs": 0,
        "strategy_versions": 0,
        "strategy_readiness": 0,
    }
    for spec in specs:
        _upsert_strategy_spec(conn, spec, now=now)
        counts["strategy_specs"] += 1
        _upsert_strategy_version(conn, spec, now=now)
        counts["strategy_versions"] += 1
        _upsert_strategy_readiness(conn, spec, now=now)
        counts["strategy_readiness"] += 2
    return counts


def _strategy_registry_current(conn: sqlite3.Connection, *, expected_count: int) -> bool:
    if not table_exists(conn, "strategy_specs") or not table_exists(conn, "strategy_versions"):
        return False
    try:
        spec_count = int(conn.execute("SELECT COUNT(*) FROM strategy_specs").fetchone()[0])
        version_count = int(conn.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0])
    except sqlite3.OperationalError:
        return False
    return spec_count == expected_count and version_count == expected_count


def strategy_catalog_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    sync_strategy_registry(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT strategy_id, strategy_version, enabled, spec_json, inserted_at_utc, updated_at_utc
            FROM strategy_specs
            ORDER BY strategy_id, strategy_version
            """
        ).fetchall()
    ]
    strategies = []
    by_family: dict[str, int] = {}
    enabled_count = 0
    for row in rows:
        spec = strategy_from_db_row(row)
        strategies.append(
            _strategy_payload(
                spec,
                inserted_at_utc=row.get("inserted_at_utc"),
                updated_at_utc=row.get("updated_at_utc"),
            )
        )
        by_family[spec.strategy_family] = by_family.get(spec.strategy_family, 0) + 1
        enabled_count += int(spec.enabled)
    return {
        "schema_version": "crypto_options_strategy_catalog_v1",
        "strategy_count": len(strategies),
        "enabled_count": enabled_count,
        "disabled_count": len(strategies) - enabled_count,
        "starting_strategy_ids": list(_starting_strategy_ids()),
        "by_family": dict(sorted(by_family.items())),
        "strategies": strategies,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def strategy_readiness_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    sync_strategy_registry(conn)
    readiness_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT readiness_key, strategy_id, strategy_version, readiness_state,
                   evaluated_at_utc, evidence_json
            FROM strategy_readiness
            ORDER BY strategy_id, strategy_version, readiness_key
            """
        ).fetchall()
    ]
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    by_type: dict[str, dict[str, int]] = {}
    for row in readiness_rows:
        evidence = _load_json(row.get("evidence_json"), default={})
        readiness_type = str(evidence.get("readiness_type") or "unknown")
        key = (str(row["strategy_id"]), str(row["strategy_version"]))
        payload = grouped.setdefault(
            key,
            {
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "replay": None,
                "pulse": None,
            },
        )
        readiness_payload = {
            "readiness_key": row["readiness_key"],
            "readiness_state": row["readiness_state"],
            "evaluated_at_utc": row["evaluated_at_utc"],
            "blockers": list(evidence.get("blockers") or []),
            "evidence": evidence,
        }
        payload[readiness_type] = readiness_payload
        state_counts = by_type.setdefault(readiness_type, {})
        state = str(row["readiness_state"])
        state_counts[state] = state_counts.get(state, 0) + 1

    strategies: list[dict[str, Any]] = []
    for strategy_id, strategy_version in sorted(grouped):
        spec = get_strategy(strategy_id)
        record = grouped[(strategy_id, strategy_version)]
        replay_state = ((record.get("replay") or {}).get("readiness_state")) or "missing"
        pulse_state = ((record.get("pulse") or {}).get("readiness_state")) or "missing"
        strategies.append(
            {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "strategy_family": spec.strategy_family,
                "state": spec.state,
                "enabled": spec.enabled,
                "starting_candidate": strategy_id in _starting_strategy_ids(),
                "replay": record.get("replay"),
                "pulse": record.get("pulse"),
                "orders_allowed": False,
                "live_trading_authorized": False,
                "summary_state": "replay_ready" if replay_state == "replay_ready" and pulse_state != "pulse_ready" else pulse_state,
            }
        )
    return {
        "schema_version": "crypto_options_strategy_readiness_v1",
        "strategy_count": len(strategies),
        "by_readiness_type": {name: dict(sorted(states.items())) for name, states in sorted(by_type.items())},
        "strategies": strategies,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def strategy_validation_lab_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    sync_strategy_registry(conn)
    strategy_validation_run_count = count_rows(conn, "strategy_validation_runs") if table_exists(conn, "strategy_validation_runs") else 0
    validation_budget_ledger_count = count_rows(conn, "validation_budget_ledger") if table_exists(conn, "validation_budget_ledger") else 0
    supervised_live_run_count = 0
    supervised_live_reconciled_count = 0
    supervised_live_lifecycle_pass_count = 0
    supervised_live_trusted_balance_count = 0
    supervised_live_cash_balance_unavailable_count = 0
    supervised_live_scoped_live_flag_count = 0
    ledger_required_run_without_entry_count = 0
    latest_strategy_validation_run = None
    latest_validation_budget_entry = None

    if table_exists(conn, "strategy_validation_runs"):
        supervised_live_run_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM strategy_validation_runs WHERE lower(run_type) = 'supervised_live'"
            ).fetchone()[0]
        )
        latest_strategy_validation_run = conn.execute(
            """
            SELECT strategy_id, strategy_version, run_type, run_phase, run_status,
                   run_id, validation_run_id, max_notional_usd, max_events,
                   max_trades, max_wall_time_seconds, lifecycle_audit_status,
                   reconciliation_status, budget_ledger_required, scoped_live_flags_json,
                   cash_balance_status, started_at_utc, completed_at_utc
            FROM strategy_validation_runs
            ORDER BY started_at_utc DESC, inserted_at_utc DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_strategy_validation_run is not None:
            latest_strategy_validation_run = dict(latest_strategy_validation_run)
            latest_strategy_validation_run["scoped_live_flags"] = _load_json(
                latest_strategy_validation_run.pop("scoped_live_flags_json", None),
                default={},
            )

        supervised_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT reconciliation_status, lifecycle_audit_status, cash_balance_status,
                       scoped_live_flags_json
                FROM strategy_validation_runs
                WHERE lower(run_type) = 'supervised_live'
                """
            ).fetchall()
        ]
        supervised_live_reconciled_count = sum(
            1 for row in supervised_rows if str(row.get("reconciliation_status") or "").lower() == "reconciled"
        )
        supervised_live_lifecycle_pass_count = sum(
            1 for row in supervised_rows if str(row.get("lifecycle_audit_status") or "").lower() == "passed"
        )
        supervised_live_trusted_balance_count = sum(
            1 for row in supervised_rows if str(row.get("cash_balance_status") or "").lower() == "trusted_balance_reported"
        )
        supervised_live_cash_balance_unavailable_count = sum(
            1 for row in supervised_rows if str(row.get("cash_balance_status") or "").lower() == "cash_balance_unavailable"
        )
        supervised_live_scoped_live_flag_count = sum(
            1
            for row in supervised_rows
            if (
                lambda flags: bool(flags.get("orders_allowed")) and bool(flags.get("live_trading_authorized"))
            )(_load_json(row.get("scoped_live_flags_json"), default={}))
        )

    if table_exists(conn, "validation_budget_ledger"):
        latest_validation_budget_entry = conn.execute(
            """
            SELECT validation_run_id, strategy_or_component_id, started_at_utc,
                   completed_at_utc, budget_cap_usd, notional_submitted_usd,
                   notional_filled_usd, realized_pnl_usd, open_cost_usd,
                   remaining_validation_budget_usd, cash_balance_before_usd,
                   cash_balance_after_usd, cash_balance_status,
                   hard_stop_triggered, stop_reason, lifecycle_audit_status,
                   reconciliation_status, updated_at_utc
            FROM validation_budget_ledger
            ORDER BY updated_at_utc DESC, inserted_at_utc DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_validation_budget_entry is not None:
            latest_validation_budget_entry = dict(latest_validation_budget_entry)

    if table_exists(conn, "strategy_validation_runs") and table_exists(conn, "validation_budget_ledger"):
        ledger_required_run_without_entry_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM strategy_validation_runs r
                LEFT JOIN validation_budget_ledger l
                  ON l.validation_run_id = r.validation_run_id
                WHERE r.budget_ledger_required = 1
                  AND (
                    r.validation_run_id IS NULL
                    OR trim(r.validation_run_id) = ''
                    OR l.validation_run_id IS NULL
                  )
                """
            ).fetchone()[0]
        )

    return {
        "schema_version": "crypto_options_strategy_validation_lab_v1",
        "strategy_count": len(all_strategy_specs()),
        "strategy_validation_run_count": strategy_validation_run_count,
        "supervised_live_run_count": supervised_live_run_count,
        "supervised_live_reconciled_count": supervised_live_reconciled_count,
        "supervised_live_lifecycle_pass_count": supervised_live_lifecycle_pass_count,
        "supervised_live_trusted_balance_count": supervised_live_trusted_balance_count,
        "supervised_live_cash_balance_unavailable_count": supervised_live_cash_balance_unavailable_count,
        "supervised_live_scoped_live_flag_count": supervised_live_scoped_live_flag_count,
        "repeatable_supervised_live_proof_ready": (
            supervised_live_run_count >= 2
            and supervised_live_reconciled_count == supervised_live_run_count
            and supervised_live_lifecycle_pass_count == supervised_live_run_count
            and supervised_live_scoped_live_flag_count == supervised_live_run_count
        ),
        "validation_budget_ledger_count": validation_budget_ledger_count,
        "ledger_required_run_without_entry_count": ledger_required_run_without_entry_count,
        "budget_cap_usd": 50.0,
        "cash_balance_hard_stop_usd": 100.0,
        "cash_balance_status": (
            latest_validation_budget_entry.get("cash_balance_status")
            if isinstance(latest_validation_budget_entry, dict)
            else "cash_balance_unavailable"
        ),
        "latest_strategy_validation_run": latest_strategy_validation_run,
        "latest_validation_budget_entry": latest_validation_budget_entry,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def strategy_replay_summary(conn: sqlite3.Connection, *, replay_mode: str, limit: int = 100) -> dict[str, Any]:
    sync_strategy_registry(conn)
    phase_filter = {
        "historical_backtest": {"historical_replay", "replay_validation"},
        "live_replay": {"live_replay", "live_shadow_test"},
    }.get(replay_mode, {replay_mode})
    rows: list[dict[str, Any]] = []
    if table_exists(conn, "strategy_validation_runs"):
        placeholders = ",".join("?" for _ in phase_filter)
        rows = [
            _strategy_replay_row_payload(dict(row))
            for row in conn.execute(
                f"""
                SELECT strategy_id, strategy_version, run_type, run_phase, run_status,
                       run_id, validation_run_id, signal_dependencies_json,
                       scoped_live_flags_json, max_notional_usd, max_events,
                       max_trades, max_wall_time_seconds, lifecycle_audit_status,
                       reconciliation_status, budget_ledger_required,
                       cash_balance_status, evidence_json, started_at_utc,
                       completed_at_utc, inserted_at_utc
                  FROM strategy_validation_runs
                 WHERE run_phase IN ({placeholders})
                   AND lower(run_type) != 'supervised_live'
                 ORDER BY started_at_utc DESC, inserted_at_utc DESC
                 LIMIT ?
                """,
                (*sorted(phase_filter), max(1, int(limit))),
            ).fetchall()
        ]
    by_status: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    lifecycle_pass_count = 0
    reconciled_count = 0
    for row in rows:
        status = str(row.get("run_status") or "unknown")
        phase = str(row.get("run_phase") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_phase[phase] = by_phase.get(phase, 0) + 1
        if str(row.get("lifecycle_audit_status") or "").lower() == "passed":
            lifecycle_pass_count += 1
        if str(row.get("reconciliation_status") or "").lower() == "reconciled":
            reconciled_count += 1
    return {
        "schema_version": "crypto_options_strategy_replay_summary_v1",
        "mode": replay_mode,
        "phase_filter": sorted(phase_filter),
        "strategy_run_count": len(rows),
        "strategy_count": len({str(row.get("strategy_id") or "") for row in rows if row.get("strategy_id")}),
        "by_status": dict(sorted(by_status.items())),
        "by_phase": dict(sorted(by_phase.items())),
        "lifecycle_pass_count": lifecycle_pass_count,
        "reconciled_count": reconciled_count,
        "rows": rows,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
    }


def strategy_from_db_row(row: dict[str, Any]) -> StrategySpec:
    spec_payload = _load_json(row.get("spec_json"), default={})
    return StrategySpec(**spec_payload)


def _upsert_strategy_spec(conn: sqlite3.Connection, spec: StrategySpec, *, now: str) -> None:
    spec_json = json.dumps(asdict(spec), sort_keys=True)
    conn.execute(
        """
        INSERT INTO strategy_specs(
            strategy_id, strategy_version, enabled, spec_json, inserted_at_utc, updated_at_utc
        )
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_id, strategy_version) DO UPDATE SET
            enabled=excluded.enabled,
            spec_json=excluded.spec_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        (spec.strategy_id, spec.strategy_version, int(spec.enabled), spec_json, now, now),
    )


def _upsert_strategy_version(conn: sqlite3.Connection, spec: StrategySpec, *, now: str) -> None:
    version_key = f"{spec.strategy_id}:{spec.strategy_version}"
    metadata = dict(spec.metadata)
    metadata.setdefault("strategy_family", spec.strategy_family)
    conn.execute(
        """
        INSERT INTO strategy_versions(
            strategy_version_key, strategy_id, strategy_version, status, created_at_utc, metadata_json
        )
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_version_key) DO UPDATE SET
            status=excluded.status,
            metadata_json=excluded.metadata_json
        """,
        (version_key, spec.strategy_id, spec.strategy_version, spec.state, now, json.dumps(metadata, sort_keys=True)),
    )


def _upsert_strategy_readiness(conn: sqlite3.Connection, spec: StrategySpec, *, now: str) -> None:
    replay = evaluate_replay_readiness(spec)
    pulse = evaluate_pulse_readiness(spec, replay_rejected=replay.readiness_state != "replay_ready", executor_boundary_configured=False)
    for readiness_type, result in (("replay", replay), ("pulse", pulse)):
        evidence = dict(result.evidence)
        evidence["readiness_type"] = readiness_type
        evidence["blockers"] = list(result.blockers)
        conn.execute(
            """
            INSERT INTO strategy_readiness(
                readiness_key, strategy_id, strategy_version, readiness_state, evaluated_at_utc, evidence_json
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(readiness_key) DO UPDATE SET
                readiness_state=excluded.readiness_state,
                evaluated_at_utc=excluded.evaluated_at_utc,
                evidence_json=excluded.evidence_json
            """,
            (
                f"{readiness_type}:{spec.strategy_id}:{spec.strategy_version}",
                spec.strategy_id,
                spec.strategy_version,
                result.readiness_state,
                now,
                json.dumps(evidence, sort_keys=True),
            ),
        )


def _strategy_payload(spec: StrategySpec, *, inserted_at_utc: str | None, updated_at_utc: str | None) -> dict[str, Any]:
    payload = asdict(spec)
    payload["starting_candidate"] = spec.strategy_id in _starting_strategy_ids()
    payload["inserted_at_utc"] = inserted_at_utc
    payload["updated_at_utc"] = updated_at_utc
    payload["orders_allowed"] = False
    payload["live_trading_authorized"] = False
    return payload


def _strategy_replay_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    scoped_flags = _load_json(row.pop("scoped_live_flags_json", None), default={})
    evidence = _load_json(row.pop("evidence_json", None), default={})
    signal_dependencies = _load_json(row.pop("signal_dependencies_json", None), default=[])
    result = evidence.get("result") if isinstance(evidence, dict) else {}
    attribution = result.get("attribution") if isinstance(result, dict) else {}
    return {
        **row,
        "signal_dependencies": signal_dependencies,
        "scoped_live_flags": scoped_flags,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
        "result_status": result.get("status") if isinstance(result, dict) else None,
        "result_blockers": list(result.get("blockers") or []) if isinstance(result, dict) else [],
        "candidate_key": result.get("candidate_key") if isinstance(result, dict) else None,
        "intent_key": result.get("intent_key") if isinstance(result, dict) else None,
        "order_key": result.get("order_key") if isinstance(result, dict) else None,
        "position_key": result.get("position_key") if isinstance(result, dict) else None,
        "managed_runtime_plan_count": (
            attribution.get("managed_runtime_plan_count")
            if isinstance(attribution, dict)
            else None
        ),
    }


def _load_json(value: Any, *, default: Any) -> Any:
    if value in {None, ""}:
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _starting_strategy_ids() -> tuple[str, ...]:
    from crypto_options_app.strategies.registry import starting_strategy_ids

    return starting_strategy_ids()
