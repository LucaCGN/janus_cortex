from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials, view_trades
from app.data.nodes.polymarket.crypto.history import fetch_current_order_book, normalize_order_book_snapshot
from app.data.nodes.polymarket.crypto.live_capture import LiveCaptureTarget, discover_live_crypto_updown_targets
from app.data.pipelines.crypto.options.live_micro_executor import inspect_polymarket_credentials

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT, CENTRAL_DB_PATH
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.reports.live_order_audit import audit_validation_orders_against_exchange
from crypto_options_app.reports.system_integrity import HealthBuildOptions, build_system_integrity_health
from crypto_options_app.config import CryptoOptionsAppConfig
from crypto_options_app.strategies.registry import get_strategy
from crypto_options_app.strategies.schema import StrategySpec
from crypto_options_app.trading.live_candidates import LiveMarketCandidateVerification, verify_live_market_candidate
from crypto_options_app.trading.live_preflight import LiveEnvironmentFlags
from crypto_options_app.workers.comparison_runner import CORE_FLOW_STRATEGY_IDS
from crypto_options_app.workers.live_minimal_validator import MinimalLiveStrategyBatchResult, run_minimal_supervised_live_validation_batch


DEFAULT_ARTIFACT_ROOT = CENTRAL_ARTIFACT_ROOT
DEFAULT_DB_PATH = CENTRAL_DB_PATH
LOCK_STALE_SECONDS = 7200


@dataclass(frozen=True)
class CoreFlowLiveRunConfig:
    run_id: str
    strategy_ids: tuple[str, ...] = CORE_FLOW_STRATEGY_IDS
    max_event_cycles: int = 3
    total_budget_cap_usd: float = 10.0
    max_wall_seconds: float = 1200.0
    poll_seconds: float = 10.0
    monitor_interval_seconds: float = 300.0
    min_seconds_remaining: float = 75.0
    symbols: tuple[str, ...] = ("BTC", "ETH")
    db_path: Path = DEFAULT_DB_PATH
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT
    operator: str = "codex-automation"
    reason: str = "Core Flow live structural validation"
    live_flags: LiveEnvironmentFlags = field(default_factory=lambda: LiveEnvironmentFlags(True, True, True))


@dataclass(frozen=True)
class CoreFlowEventResult:
    event_index: int
    event_slug: str | None
    status: str
    generated_at_utc: str
    verification_blockers: tuple[str, ...]
    runtime_blockers: tuple[str, ...]
    estimated_spent_usd: float
    artifact_json: str


@dataclass(frozen=True)
class CoreFlowLiveRunResult:
    run_id: str
    status: str
    generated_at_utc: str
    event_results: tuple[CoreFlowEventResult, ...]
    estimated_spent_usd: float
    blockers: tuple[str, ...]
    artifact_json: str
    health_snapshot_json: str | None
    order_audit_json: str | None
    manual_orders_avoided: bool = True


CandidateProvider = Callable[[set[str], CoreFlowLiveRunConfig], LiveMarketCandidateVerification]
Submitter = Callable[[dict[str, Any]], dict[str, Any]]


def run_core_flow_live_test(
    config: CoreFlowLiveRunConfig,
    *,
    candidate_provider: CandidateProvider | None = None,
    submitter: Submitter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CoreFlowLiveRunResult:
    """Run the guarded Core Flow live structural test through supervised runtime only."""

    started = time.monotonic()
    artifact_root = Path(config.artifact_root)
    validation_dir = artifact_root / "live-validation"
    report_dir = artifact_root / "reports"
    automation_dir = artifact_root / "automation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    automation_dir.mkdir(parents=True, exist_ok=True)
    initialize_schema(config.db_path)
    lock_path = automation_dir / "core_flow_live_test.lock"
    result_path = validation_dir / f"{config.run_id}.json"
    status_path = automation_dir / "core_flow_live_status.json"
    event_results: list[CoreFlowEventResult] = []
    all_strategy_rows: list[dict[str, Any]] = []
    processed_events: set[str] = set()
    blockers: list[str] = []
    estimated_spent = 0.0
    candidate_provider = candidate_provider or find_next_verified_candidate

    with _single_run_lock(lock_path, run_id=config.run_id):
        _write_json(
            status_path,
            {
                "schema_version": "crypto_options_core_flow_live_status_v1",
                "run_id": config.run_id,
                "status": "running",
                "started_at_utc": _utc_now(),
                "event_results": [],
                "estimated_spent_usd": 0.0,
                "manual_orders_avoided": True,
            },
        )
        last_monitor = 0.0
        while len(event_results) < config.max_event_cycles:
            if time.monotonic() - started > config.max_wall_seconds:
                blockers.append("core_flow_wall_clock_limit_reached")
                break
            if estimated_spent >= config.total_budget_cap_usd:
                blockers.append("core_flow_budget_cap_reached")
                break

            verification = candidate_provider(processed_events, config)
            if not verification.verified:
                if time.monotonic() - last_monitor >= config.monitor_interval_seconds:
                    last_monitor = time.monotonic()
                    _write_json(
                        status_path,
                        {
                            "schema_version": "crypto_options_core_flow_live_status_v1",
                            "run_id": config.run_id,
                            "status": "waiting_for_verified_candidate",
                            "generated_at_utc": _utc_now(),
                            "processed_event_count": len(event_results),
                            "verification_blockers": list(verification.blockers),
                            "estimated_spent_usd": round(estimated_spent, 6),
                            "manual_orders_avoided": True,
                        },
                    )
                sleep(max(1.0, config.poll_seconds))
                continue

            assert verification.candidate is not None
            event_slug = verification.candidate.event_slug or verification.candidate.event_key
            if event_slug in processed_events:
                sleep(max(1.0, config.poll_seconds))
                continue
            projected_budget = estimated_spent + _projected_minimal_event_cost(verification, strategy_count=len(config.strategy_ids))
            if projected_budget > config.total_budget_cap_usd + 1e-9:
                blockers.append("core_flow_budget_cap_would_be_exceeded")
                break

            event_index = len(event_results) + 1
            batch = run_minimal_supervised_live_validation_batch(
                run_id=f"{config.run_id}:event_{event_index}",
                candidate_verifications=(verification,),
                credentials_ready=True if submitter is not None else bool(inspect_polymarket_credentials().get("ready")),
                env_flags=config.live_flags,
                specs=tuple(get_strategy(strategy_id) for strategy_id in config.strategy_ids),
                operator=config.operator,
                reason=f"{config.reason}; event {event_index}/{config.max_event_cycles}",
                submitter=submitter,
                persist_db_path=config.db_path,
            )
            runtime_blockers = tuple(
                blocker
                for result in batch.results
                for blocker in result.blockers
                if _is_runtime_breaker(blocker)
            )
            strategy_rows = _strategy_rows_from_batch(batch)
            all_strategy_rows.extend(strategy_rows)
            event_spent = _estimated_spent_from_rows(strategy_rows)
            estimated_spent += event_spent
            event_artifact_path = validation_dir / f"{config.run_id}_event_{event_index}.json"
            _write_json(
                event_artifact_path,
                _event_payload(
                    config=config,
                    event_index=event_index,
                    verification=verification,
                    batch=batch,
                    strategy_rows=strategy_rows,
                    event_spent=event_spent,
                    cumulative_spent=estimated_spent,
                    runtime_blockers=runtime_blockers,
                ),
            )
            event_result = CoreFlowEventResult(
                event_index=event_index,
                event_slug=event_slug,
                status="validated" if not runtime_blockers else "stopped_on_breaker",
                generated_at_utc=_utc_now(),
                verification_blockers=verification.blockers,
                runtime_blockers=runtime_blockers,
                estimated_spent_usd=round(event_spent, 6),
                artifact_json=str(event_artifact_path),
            )
            event_results.append(event_result)
            processed_events.add(event_slug)
            _write_json(
                status_path,
                {
                    "schema_version": "crypto_options_core_flow_live_status_v1",
                    "run_id": config.run_id,
                    "status": event_result.status,
                    "generated_at_utc": event_result.generated_at_utc,
                    "event_results": [_event_result_dict(item) for item in event_results],
                    "estimated_spent_usd": round(estimated_spent, 6),
                    "manual_orders_avoided": True,
                },
            )
            if runtime_blockers:
                blockers.extend(runtime_blockers)
                break

        status = "validated" if len(event_results) >= config.max_event_cycles and not blockers else "blocked"
        health_path = report_dir / f"{config.run_id}_health_after.json"
        order_audit_path = report_dir / f"{config.run_id}_order_audit.json"
        latest_order_audit_path = report_dir / "live_order_integrity_audit_latest.json"
        audit_payload = _build_order_audit_payload(all_strategy_rows)
        _write_json(order_audit_path, audit_payload)
        _write_json(latest_order_audit_path, audit_payload)
        health_payload = build_system_integrity_health(
            CryptoOptionsAppConfig(db_path=config.db_path),
            options=HealthBuildOptions(artifact_root=artifact_root),
        )
        _write_json(health_path, health_payload)
        final_payload = {
            "schema_version": "crypto_options_core_flow_live_run_v1",
            "run_id": config.run_id,
            "status": status,
            "generated_at_utc": _utc_now(),
            "strategy_ids": list(config.strategy_ids),
            "max_event_cycles": config.max_event_cycles,
            "total_budget_cap_usd": config.total_budget_cap_usd,
            "estimated_spent_usd": round(estimated_spent, 6),
            "event_results": [_event_result_dict(item) for item in event_results],
            "strategy_rows": all_strategy_rows,
            "blockers": blockers,
            "health_snapshot_json": str(health_path),
            "order_audit_json": str(order_audit_path),
            "manual_orders_avoided": True,
        }
        _write_json(result_path, final_payload)
        _write_json(
            status_path,
            {
                "schema_version": "crypto_options_core_flow_live_status_v1",
                "run_id": config.run_id,
                "status": status,
                "generated_at_utc": final_payload["generated_at_utc"],
                "event_results": final_payload["event_results"],
                "estimated_spent_usd": final_payload["estimated_spent_usd"],
                "blockers": blockers,
                "artifact_json": str(result_path),
                "manual_orders_avoided": True,
            },
        )
    return CoreFlowLiveRunResult(
        run_id=config.run_id,
        status=status,
        generated_at_utc=final_payload["generated_at_utc"],
        event_results=tuple(event_results),
        estimated_spent_usd=round(estimated_spent, 6),
        blockers=tuple(blockers),
        artifact_json=str(result_path),
        health_snapshot_json=str(health_path),
        order_audit_json=str(order_audit_path),
    )


def find_next_verified_candidate(processed_events: set[str], config: CoreFlowLiveRunConfig) -> LiveMarketCandidateVerification:
    targets, _discovery = discover_live_crypto_updown_targets(
        symbols=list(config.symbols),
        cadence_minutes=5,
        lookback_minutes=2,
        lookahead_minutes=20,
    )
    now_utc = datetime.now(UTC)
    candidates: list[tuple[tuple[float, float, float, str], LiveMarketCandidateVerification]] = []
    blockers: list[str] = []
    for target in _event_targets_to_try(targets, processed_events=processed_events, now_utc=now_utc, min_seconds_remaining=config.min_seconds_remaining):
        row = _live_candidate_row_from_target(target)
        verification = verify_live_market_candidate(row, now_utc=now_utc, max_quote_age_seconds=15.0, max_spread=0.08)
        if not verification.verified or verification.candidate is None:
            blockers.extend(verification.blockers)
            continue
        ask = verification.candidate.best_ask
        score = (
            abs(float(ask) - 0.5),
            verification.candidate.spread,
            -verification.candidate.depth_top3_ask_size,
            verification.candidate.token_id,
        )
        candidates.append((score, verification))
    if not candidates:
        return LiveMarketCandidateVerification(False, None, tuple(sorted(set(blockers or ["no_verified_live_candidate"]))))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _event_targets_to_try(
    targets: list[LiveCaptureTarget],
    *,
    processed_events: set[str],
    now_utc: datetime,
    min_seconds_remaining: float,
) -> list[LiveCaptureTarget]:
    grouped: dict[str, list[LiveCaptureTarget]] = {}
    for target in targets:
        event_slug = target.event_slug or target.market_id
        if event_slug in processed_events:
            continue
        end_at = _parse_time(target.window_end_time)
        if end_at is None:
            continue
        if (end_at - now_utc).total_seconds() < min_seconds_remaining:
            continue
        grouped.setdefault(event_slug, []).append(target)
    scored: list[tuple[float, str, list[LiveCaptureTarget]]] = []
    for event_slug, event_targets in grouped.items():
        starts = [_parse_time(target.window_start_time) for target in event_targets]
        start = min((item for item in starts if item is not None), default=now_utc)
        # Prefer current events, then the nearest future event.
        score = 0.0 if start <= now_utc else (start - now_utc).total_seconds()
        scored.append((score, event_slug, event_targets))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [target for _score, _event_slug, event_targets in scored[:2] for target in event_targets]


def _live_candidate_row_from_target(target: LiveCaptureTarget) -> dict[str, Any]:
    observed_at = datetime.now(UTC)
    payload = fetch_current_order_book(target.token_id)
    frame = normalize_order_book_snapshot(
        payload,
        event_id=target.event_id,
        market_id=target.market_id,
        outcome=target.outcome,
        symbol=target.symbol,
    )
    if frame.empty:
        return {
            "token_id": target.token_id,
            "event_key": target.event_slug,
            "event_token_key": f"{target.event_slug}:{target.outcome.lower()}",
            "event_slug": target.event_slug,
            "outcome": target.outcome,
            "observed_at_utc": observed_at.isoformat(),
            "event_end_time_utc": target.window_end_time,
            "source": "polymarket_current_order_book_empty",
        }
    row = frame.iloc[0].to_dict()
    return {
        "token_id": target.token_id,
        "event_key": target.event_slug,
        "event_token_key": f"{target.event_slug}:{target.outcome.lower()}",
        "event_slug": target.event_slug,
        "outcome": target.outcome,
        "best_bid": row.get("best_bid"),
        "best_ask": row.get("best_ask"),
        "spread": row.get("spread"),
        "ask_size": row.get("ask_size"),
        "depth_top3_ask_size": row.get("depth_top3_ask_size"),
        "observed_at_utc": observed_at.isoformat(),
        "event_start_time_utc": target.window_start_time,
        "event_end_time_utc": target.window_end_time,
        "source": row.get("source") or "polymarket_current_order_book",
    }


def _strategy_rows_from_batch(batch: MinimalLiveStrategyBatchResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in batch.results:
        if result.runtime_report is None:
            rows.append(
                {
                    "strategy_id": result.run_id.rsplit(":", 1)[-1],
                    "status": result.status,
                    "blockers": list(result.blockers),
                    "live_submission_attempted": result.live_submission_attempted,
                }
            )
            continue
        for strategy_result in result.runtime_report.results:
            row = strategy_result.to_candidate_row()
            row["status"] = strategy_result.status
            row["side"] = "BUY"
            row["run_id"] = result.runtime_report.run_id
            rows.append(row)
    return rows


def _event_payload(
    *,
    config: CoreFlowLiveRunConfig,
    event_index: int,
    verification: LiveMarketCandidateVerification,
    batch: MinimalLiveStrategyBatchResult,
    strategy_rows: list[dict[str, Any]],
    event_spent: float,
    cumulative_spent: float,
    runtime_blockers: tuple[str, ...],
) -> dict[str, Any]:
    candidate = verification.candidate
    return {
        "schema_version": "crypto_options_core_flow_live_event_v1",
        "run_id": config.run_id,
        "event_index": event_index,
        "generated_at_utc": _utc_now(),
        "candidate": None if candidate is None else candidate.__dict__,
        "batch_statuses": [
            {
                "run_id": result.run_id,
                "status": result.status,
                "blockers": list(result.blockers),
                "live_submission_attempted": result.live_submission_attempted,
            }
            for result in batch.results
        ],
        "strategy_rows": strategy_rows,
        "estimated_spent_usd": round(event_spent, 6),
        "cumulative_spent_usd": round(cumulative_spent, 6),
        "runtime_blockers": list(runtime_blockers),
        "manual_orders_avoided": True,
    }


def _build_order_audit_payload(strategy_rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        trades = [trade.__dict__ for trade in view_trades(PolymarketCredentials.from_env())]
    except Exception as exc:  # noqa: BLE001
        return {
            "schema_version": "crypto_options_core_flow_order_audit_wrapper_v1",
            "status": "blocked",
            "error": f"{type(exc).__name__}:{exc}",
        }
    audit = audit_validation_orders_against_exchange(validation_rows=strategy_rows, exchange_trades=trades)
    return {
        "schema_version": "crypto_options_core_flow_order_audit_wrapper_v1",
        "generated_at_utc": _utc_now(),
        "status": audit.status,
        "audit": audit.to_dict(),
    }


def _projected_minimal_event_cost(verification: LiveMarketCandidateVerification, *, strategy_count: int) -> float:
    ask = 1.0
    if verification.candidate is not None:
        ask = float(verification.candidate.best_ask)
    # Match the supervised micro-executor's minimum marketable BUY notional
    # adjustment conservatively. The runtime scenario requests one share, but
    # the executor can raise share size so price * shares >= $1. Add a small
    # per-order cushion for JIT movement and taker-fee accounting so the run
    # does not exceed its hard budget cap on the final event.
    minimal_shares = max(1.0, _ceil_to_cents(1.0 / max(ask, 0.01)))
    per_strategy_cost = max(1.0, minimal_shares * ask) + 0.10
    return float(strategy_count) * per_strategy_cost


def _ceil_to_cents(value: float) -> float:
    return float(int(value * 100.0 + 0.999999999) / 100.0)


def _estimated_spent_from_rows(strategy_rows: list[dict[str, Any]]) -> float:
    spent = 0.0
    for row in strategy_rows:
        if row.get("status") not in {"executed", "live_structural_executed"}:
            continue
        shares = _optional_float(row.get("filled_shares"))
        price = _optional_float(row.get("fill_price"))
        if shares is not None and price is not None:
            spent += shares * price
    return spent


def _is_runtime_breaker(blocker: str) -> bool:
    return blocker in {
        "missing_lifecycle_coverage",
        "reconciliation_mismatch",
        "duplicate_cadence",
        "submit_error",
        "exchange_status_not_operational",
        "polymarket_status_unavailable",
        "polymarket_active_maintenance",
        "polymarket_active_incident",
        "credentials_access_failure",
        "executor_boundary_not_ready",
        "fill_evidence_missing",
        "submit_error_no_fill",
        "submit_error_fill_ambiguous",
        "rejected_no_fill",
        "rejected_fill_ambiguous",
    }


def _single_run_lock(path: Path, *, run_id: str):
    class _Lock:
        def __enter__(self) -> None:
            if path.exists():
                payload = _read_json(path)
                age = time.time() - path.stat().st_mtime
                pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
                if age < LOCK_STALE_SECONDS and _pid_alive(pid):
                    raise RuntimeError(f"duplicate_core_flow_live_test:pid={pid}")
            _write_json(path, {"run_id": run_id, "pid": os.getpid(), "created_at_utc": _utc_now()})

        def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
            path.unlink(missing_ok=True)

    return _Lock()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _event_result_dict(result: CoreFlowEventResult) -> dict[str, Any]:
    return {
        "event_index": result.event_index,
        "event_slug": result.event_slug,
        "status": result.status,
        "generated_at_utc": result.generated_at_utc,
        "verification_blockers": list(result.verification_blockers),
        "runtime_blockers": list(result.runtime_blockers),
        "estimated_spent_usd": result.estimated_spent_usd,
        "artifact_json": result.artifact_json,
    }


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
