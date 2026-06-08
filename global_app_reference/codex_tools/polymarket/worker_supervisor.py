"""Supervisor planning surfaces for global portfolio strategy workers.

The supervisor is intentionally non-executing. It normalizes worker configs,
summarizes current worker state, and can build a gated order-leg plan that
points at the approved `portfolio-manager-order` path without invoking it.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT

STRATEGY_WORKER_SPEC_SCHEMA_VERSION = "portfolio_strategy_worker_spec_v1"
STRATEGY_WORKER_STATUS_SCHEMA_VERSION = "portfolio_strategy_worker_status_v1"
STRATEGY_WORKER_LEG_PLAN_SCHEMA_VERSION = "portfolio_strategy_worker_leg_plan_v1"
STRATEGY_WORKER_TICK_SCHEMA_VERSION = "portfolio_strategy_worker_tick_v1"
ALLOWED_STRATEGY_WORKER_STATES = {
    "planned",
    "dry_run_active",
    "paused",
    "blocked",
    "expired",
    "ready_for_order_review",
}


@dataclass(frozen=True)
class PortfolioStrategyWorkerSpec:
    schema_version: str
    worker_id: str
    strategy_mode: str
    enabled: bool
    dry_run: bool
    market_slug: str | None
    token_id: str | None
    outcome_side: str | None
    premise_state: str
    grid_step_price: str
    review_after_hours: int | None
    review_after_at: str | None
    max_notional_usd: str | None
    heartbeat_path: str | None
    ledger_path: str | None
    state: str
    candidate: dict[str, Any]
    order_preparation_allowed: bool
    order_submission_allowed: bool

    def safe_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate_keys"] = sorted(self.candidate.keys())
        payload.pop("candidate", None)
        return payload


def build_strategy_worker_spec(payload: dict[str, Any]) -> PortfolioStrategyWorkerSpec:
    candidate = _dict(payload.get("candidate") or payload.get("position") or payload.get("slot") or payload)
    token_id = _optional_text(payload.get("token_id") or candidate.get("token_id") or candidate.get("asset"))
    market_slug = _optional_text(payload.get("market_slug") or candidate.get("market_slug") or candidate.get("slug"))
    worker_id = _optional_text(payload.get("worker_id")) or _stable_worker_id(
        strategy_mode=str(payload.get("strategy_mode") or "sideways_grid"),
        market_slug=market_slug,
        token_id=token_id,
    )
    grid_step = _positive_decimal(payload.get("grid_step_price") or payload.get("grid_step") or "0.01", default=Decimal("0.01"))
    return PortfolioStrategyWorkerSpec(
        schema_version=str(payload.get("schema_version") or STRATEGY_WORKER_SPEC_SCHEMA_VERSION),
        worker_id=worker_id,
        strategy_mode=str(payload.get("strategy_mode") or payload.get("mode") or "sideways_grid"),
        enabled=_bool(payload.get("enabled"), default=False),
        dry_run=_bool(payload.get("dry_run"), default=True),
        market_slug=market_slug,
        token_id=token_id,
        outcome_side=_optional_text(payload.get("outcome_side") or candidate.get("outcome") or candidate.get("side")),
        premise_state=str(payload.get("premise_state") or candidate.get("premise_state") or "unreviewed"),
        grid_step_price=_decimal_str(grid_step) or "0.01",
        review_after_hours=_int(payload.get("review_after_hours")),
        review_after_at=_optional_text(payload.get("review_after_at") or payload.get("review_after_at_utc")),
        max_notional_usd=_decimal_str(_decimal(payload.get("max_notional_usd"))),
        heartbeat_path=_optional_text(payload.get("heartbeat_path")),
        ledger_path=_optional_text(payload.get("ledger_path")),
        state=_worker_state(payload.get("state")),
        candidate=candidate,
        order_preparation_allowed=False,
        order_submission_allowed=False,
    )


def build_strategy_worker_status(
    worker_configs: list[dict[str, Any]] | dict[str, Any],
    *,
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    generated_at = _coerce_utc(now_utc)
    rows = worker_configs if isinstance(worker_configs, list) else worker_configs.get("workers", [])
    if isinstance(rows, dict):
        rows = list(rows.values())
    specs = [build_strategy_worker_spec(row) for row in rows if isinstance(row, dict)]
    active_count = sum(1 for spec in specs if spec.enabled and spec.dry_run and spec.state not in {"paused", "blocked", "expired"})
    blocked_count = sum(1 for spec in specs if spec.enabled and not spec.dry_run)
    expired_count = sum(1 for spec in specs if _worker_expired(spec, now_utc=generated_at))
    return {
        "schema_version": STRATEGY_WORKER_STATUS_SCHEMA_VERSION,
        "generated_at_utc": _iso(generated_at),
        "status": "ok" if blocked_count == 0 else "blocked_non_dry_run_worker_present",
        "worker_count": len(specs),
        "active_dry_run_worker_count": active_count,
        "blocked_worker_count": blocked_count,
        "expired_worker_count": expired_count,
        "workers": [spec.safe_dict() for spec in specs],
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def build_strategy_worker_tick(
    worker_config: dict[str, Any] | PortfolioStrategyWorkerSpec,
    *,
    market_snapshot: dict[str, Any] | None = None,
    action_plan: dict[str, Any] | None = None,
    requested_order: dict[str, Any] | None = None,
    api_root: str = "http://127.0.0.1:8010",
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Build one durable dry-run worker tick payload without invoking orders."""

    spec = worker_config if isinstance(worker_config, PortfolioStrategyWorkerSpec) else build_strategy_worker_spec(worker_config)
    generated_at = _coerce_utc(now_utc)
    blockers: list[str] = []
    if not spec.enabled:
        blockers.append("worker_disabled")
    if not spec.dry_run:
        blockers.append("non_dry_run_worker_not_supported")
    if spec.state in {"paused", "blocked", "expired"}:
        blockers.append(f"worker_state_{spec.state}")
    if _worker_expired(spec, now_utc=generated_at):
        blockers.append("review_window_expired")
    if not spec.token_id:
        blockers.append("token_id_missing")

    leg_plan = build_strategy_worker_leg_plan(
        spec,
        action_plan=action_plan,
        requested_order=requested_order,
        api_root=api_root,
        now_utc=generated_at,
    )
    leg_ready = leg_plan["status"] == "ready_for_portfolio_manager_order_review"
    if leg_ready and not blockers:
        status = "ready_for_order_review"
        next_state = "ready_for_order_review"
    elif blockers:
        status = "blocked"
        next_state = "blocked" if "review_window_expired" not in blockers else "expired"
    else:
        status = "dry_run_tick_recorded"
        next_state = "dry_run_active"

    idempotency_key = _tick_idempotency_key(spec, generated_at=generated_at)
    heartbeat = {
        "schema_version": "portfolio_strategy_worker_heartbeat_v1",
        "worker_id": spec.worker_id,
        "updated_at_utc": _iso(generated_at),
        "status": status,
        "state": next_state,
        "strategy_mode": spec.strategy_mode,
        "market_slug": spec.market_slug,
        "token_id": spec.token_id,
        "dry_run": spec.dry_run,
        "blockers": blockers,
        "leg_plan_status": leg_plan["status"],
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }
    ledger_event = {
        "schema_version": "portfolio_strategy_worker_ledger_event_v1",
        "worker_id": spec.worker_id,
        "event_type": "dry_run_tick",
        "event_status": status,
        "idempotency_key": idempotency_key,
        "recorded_at_utc": _iso(generated_at),
        "previous_state": spec.state,
        "next_state": next_state,
        "market_snapshot": market_snapshot or {},
        "leg_plan": leg_plan,
        "blockers": blockers,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }
    return {
        "schema_version": STRATEGY_WORKER_TICK_SCHEMA_VERSION,
        "generated_at_utc": _iso(generated_at),
        "status": status,
        "worker_id": spec.worker_id,
        "previous_state": spec.state,
        "next_state": next_state,
        "blockers": blockers,
        "heartbeat_json": heartbeat,
        "ledger_event": ledger_event,
        "strategy_worker_leg_plan": leg_plan,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def build_strategy_worker_leg_plan(
    worker_config: dict[str, Any] | PortfolioStrategyWorkerSpec,
    *,
    action_plan: dict[str, Any] | None = None,
    requested_order: dict[str, Any] | None = None,
    api_root: str = "http://127.0.0.1:8010",
    now_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Build an inert order-leg handoff plan for approved order path review."""

    spec = worker_config if isinstance(worker_config, PortfolioStrategyWorkerSpec) else build_strategy_worker_spec(worker_config)
    generated_at = _coerce_utc(now_utc)
    blockers: list[str] = []
    if not spec.enabled:
        blockers.append("worker_disabled")
    if not spec.dry_run:
        blockers.append("non_dry_run_worker_not_supported_by_supervisor")
    if not spec.token_id:
        blockers.append("token_id_missing")
    if not action_plan:
        blockers.append("manager_action_plan_missing")
    if not requested_order:
        blockers.append("requested_order_missing")
    review_after_at = _iso(generated_at + timedelta(hours=spec.review_after_hours)) if spec.review_after_hours else None
    command = None
    if not blockers:
        command = [
            "python",
            "-m",
            "codex_tools.polymarket.cli",
            "portfolio-manager-order",
            "--api-root",
            api_root,
            "--action-plan-json",
            "<path-to-reviewed-action-plan.json>",
            "--requested-order-json",
            "<path-to-reviewed-requested-order.json>",
        ]
    return {
        "schema_version": STRATEGY_WORKER_LEG_PLAN_SCHEMA_VERSION,
        "generated_at_utc": _iso(generated_at),
        "status": "ready_for_portfolio_manager_order_review" if not blockers else "blocked_missing_worker_order_gates",
        "worker_id": spec.worker_id,
        "strategy_mode": spec.strategy_mode,
        "market_slug": spec.market_slug,
        "token_id": spec.token_id,
        "premise_state": spec.premise_state,
        "review_after_at_utc": review_after_at,
        "grid_step_price": spec.grid_step_price,
        "max_notional_usd": spec.max_notional_usd,
        "blockers": blockers,
        "portfolio_manager_order_command": command,
        "action_plan": action_plan or {},
        "requested_order": requested_order or {},
        "worker_config": spec.safe_dict(),
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
    }


def _stable_worker_id(*, strategy_mode: str, market_slug: str | None, token_id: str | None) -> str:
    source = "|".join([strategy_mode, market_slug or "", token_id or ""])
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"portfolio-worker-{digest}"


def _tick_idempotency_key(spec: PortfolioStrategyWorkerSpec, *, generated_at: datetime) -> str:
    bucket = generated_at.replace(second=0, microsecond=0)
    source = "|".join([spec.worker_id, spec.strategy_mode, spec.token_id or "", _iso(bucket)])
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"portfolio-worker-tick-{digest}"


def _worker_state(value: Any) -> str:
    state = str(value or "planned").strip().lower()
    return state if state in ALLOWED_STRATEGY_WORKER_STATES else "planned"


def _worker_expired(spec: PortfolioStrategyWorkerSpec, *, now_utc: datetime) -> bool:
    if spec.state == "expired":
        return True
    if not spec.review_after_at:
        return False
    return _coerce_utc(spec.review_after_at) <= now_utc


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _int(value: Any) -> int | None:
    decimal = _decimal(value)
    if decimal is None:
        return None
    return int(decimal)


def _positive_decimal(value: Any, *, default: Decimal) -> Decimal:
    decimal = _decimal(value)
    if decimal is None or decimal <= 0:
        return default
    return decimal


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        parsed = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _coerce_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "ALLOWED_STRATEGY_WORKER_STATES",
    "STRATEGY_WORKER_LEG_PLAN_SCHEMA_VERSION",
    "STRATEGY_WORKER_SPEC_SCHEMA_VERSION",
    "STRATEGY_WORKER_STATUS_SCHEMA_VERSION",
    "STRATEGY_WORKER_TICK_SCHEMA_VERSION",
    "PortfolioStrategyWorkerSpec",
    "build_strategy_worker_leg_plan",
    "build_strategy_worker_spec",
    "build_strategy_worker_status",
    "build_strategy_worker_tick",
]
