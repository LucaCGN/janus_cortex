"""Pure planning primitives for a future global portfolio grid worker.

The helpers in this module are inert. They translate a reviewed grid candidate
into a deterministic ladder preview that a later dry-run worker tick can record,
but they never prepare, sign, submit, cancel, replace, or place orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any

from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT

GRID_WORKER_CONFIG_SCHEMA_VERSION = "polymarket_grid_worker_config_v1"
GRID_WORKER_STATUS_SCHEMA_VERSION = "polymarket_grid_worker_status_v1"
GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION = "polymarket_grid_worker_dry_run_tick_v1"
GRID_WORKER_HEARTBEAT_SCHEMA_VERSION = "polymarket_grid_worker_heartbeat_v1"
GRID_WORKER_LADDER_SCHEMA_VERSION = "polymarket_grid_worker_ladder_plan_v1"
GRID_WORKER_LEDGER_ENTRY_SCHEMA_VERSION = "polymarket_grid_worker_ledger_entry_v1"


@dataclass(frozen=True)
class PolymarketGridWorkerConfig:
    schema_version: str
    worker_id: str
    enabled: bool
    dry_run: bool
    source: str
    grid_step_cents: int
    grid_step_price: str
    price_tick: str
    min_volatility_band_percent: str
    max_ladder_legs_per_side: int
    heartbeat_path: str | None
    ledger_path: str | None
    state: str
    candidates: list[dict[str, Any]]
    order_preparation_allowed: bool
    order_submission_allowed: bool

    def safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "worker_id": self.worker_id,
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "source": self.source,
            "grid_step_cents": self.grid_step_cents,
            "grid_step_price": self.grid_step_price,
            "price_tick": self.price_tick,
            "min_volatility_band_percent": self.min_volatility_band_percent,
            "max_ladder_legs_per_side": self.max_ladder_legs_per_side,
            "heartbeat_path": self.heartbeat_path,
            "ledger_path": self.ledger_path,
            "state": self.state,
            "candidate_count": len(self.candidates),
            "order_preparation_allowed": self.order_preparation_allowed,
            "order_submission_allowed": self.order_submission_allowed,
        }


@dataclass(frozen=True)
class PolymarketGridWorkerLadderLeg:
    leg_id: str
    sequence: int
    action: str
    side: str
    limit_price: str
    trigger_price: str
    size: str
    order_preparation_allowed: bool
    order_submission_allowed: bool
    rationale: str


@dataclass(frozen=True)
class PolymarketGridWorkerLadderPlan:
    schema_version: str
    status: str
    generated_at_utc: str
    market_slug: str
    title: str
    token_id: str
    outcome_side: str
    anchor_price: str | None
    lower_band_price: str | None
    upper_band_price: str | None
    volatility_band_percent: str | None
    min_volatility_band_percent: str
    grid_step_cents: int
    grid_step_price: str
    price_tick: str
    max_ladder_legs_per_side: int
    leg_count: int
    legs: list[dict[str, Any]]
    missing_inputs: list[str]
    blockers: list[dict[str, Any]]
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


@dataclass(frozen=True)
class PolymarketGridWorkerStatus:
    schema_version: str
    status: str
    generated_at_utc: str
    worker_id: str
    enabled: bool
    dry_run: bool
    candidate_count: int
    heartbeat_path: str | None
    last_tick_status: str | None
    config: dict[str, Any]
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


@dataclass(frozen=True)
class PolymarketGridWorkerDryRunTick:
    schema_version: str
    status: str
    ok: bool
    tick_id: str
    idempotency_key: str
    replay_suppressed: bool
    started_at_utc: str
    finished_at_utc: str
    worker_id: str
    trigger: str
    execution_boundary: str
    previous_state: str
    next_state: str
    state_transition: dict[str, Any]
    candidate_count: int
    plan_count: int
    ready_plan_count: int
    blocked_plan_count: int
    plans: list[dict[str, Any]]
    blockers: list[dict[str, Any]]
    heartbeat_write: dict[str, Any]
    ledger_read: dict[str, Any]
    ledger_write: dict[str, Any]
    config: dict[str, Any]
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


def load_grid_worker_config(config_path: str | Path) -> PolymarketGridWorkerConfig:
    """Load a durable dry-run grid-worker config from JSON."""

    payload = _read_json_object(Path(config_path))
    return build_grid_worker_config(payload)


def build_grid_worker_config(payload: dict[str, Any]) -> PolymarketGridWorkerConfig:
    """Normalize a JSON-like worker config into an inert worker config object."""

    min_band = _decimal(payload.get("min_volatility_band_percent")) or Decimal("10")
    grid_step_price = _grid_step_price(payload)
    price_tick = _positive_decimal(payload.get("price_tick") or payload.get("tick_size") or grid_step_price, default=Decimal("0.01"))
    return PolymarketGridWorkerConfig(
        schema_version=str(payload.get("schema_version") or GRID_WORKER_CONFIG_SCHEMA_VERSION),
        worker_id=str(payload.get("worker_id") or "global-portfolio-grid-worker").strip()
        or "global-portfolio-grid-worker",
        enabled=_bool(payload.get("enabled"), default=False),
        dry_run=_bool(payload.get("dry_run"), default=True),
        source=str(payload.get("source") or "janus-grid-worker-devloop").strip() or "janus-grid-worker-devloop",
        grid_step_cents=max(1, int((grid_step_price * Decimal("100")).to_integral_value(rounding=ROUND_UP))),
        grid_step_price=str(_quantize_to_tick(grid_step_price, tick=price_tick, rounding=ROUND_UP)),
        price_tick=str(price_tick),
        min_volatility_band_percent=str(min_band),
        max_ladder_legs_per_side=max(0, int(_decimal(payload.get("max_ladder_legs_per_side")) or Decimal("3"))),
        heartbeat_path=_optional_text(payload.get("heartbeat_path")),
        ledger_path=_optional_text(
            payload.get("ledger_path") or payload.get("dry_run_ledger_path") or payload.get("state_ledger_path")
        ),
        state=str(payload.get("state") or "idle").strip() or "idle",
        candidates=_candidate_rows(payload),
        order_preparation_allowed=False,
        order_submission_allowed=False,
    )


def build_grid_worker_status(
    config: PolymarketGridWorkerConfig | dict[str, Any],
    *,
    last_tick: PolymarketGridWorkerDryRunTick | dict[str, Any] | None = None,
    now_utc: datetime | str | None = None,
) -> PolymarketGridWorkerStatus:
    """Return a dry-run status payload without writing heartbeat or ledger state."""

    worker_config = _coerce_worker_config(config)
    generated_at = _coerce_utc(now_utc).isoformat().replace("+00:00", "Z")
    status = _status_for_config(worker_config)
    tick_payload = asdict(last_tick) if isinstance(last_tick, PolymarketGridWorkerDryRunTick) else last_tick
    return PolymarketGridWorkerStatus(
        schema_version=GRID_WORKER_STATUS_SCHEMA_VERSION,
        status=status,
        generated_at_utc=generated_at,
        worker_id=worker_config.worker_id,
        enabled=worker_config.enabled,
        dry_run=worker_config.dry_run,
        candidate_count=len(worker_config.candidates),
        heartbeat_path=worker_config.heartbeat_path,
        last_tick_status=str(tick_payload.get("status")) if isinstance(tick_payload, dict) and tick_payload.get("status") else None,
        config=worker_config.safe_dict(),
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


def run_grid_worker_dry_run_tick(
    config: PolymarketGridWorkerConfig | dict[str, Any],
    *,
    now_utc: datetime | str | None = None,
    trigger: str = "manual_dry_run_tick",
    write_heartbeat: bool = True,
) -> PolymarketGridWorkerDryRunTick:
    """Run one inert dry-run worker tick and optionally write a heartbeat file."""

    worker_config = _coerce_worker_config(config)
    started_at = _coerce_utc(now_utc)
    started = started_at.isoformat().replace("+00:00", "Z")
    idempotency_key = _dry_run_idempotency_key(worker_config, trigger=trigger)
    ledger_entries, ledger_read = _read_grid_worker_ledger(
        worker_config.ledger_path,
        worker_id=worker_config.worker_id,
        idempotency_key=idempotency_key,
    )
    replay_entry = ledger_read.pop("_replay_entry", None)
    previous_state = _ledger_previous_state(ledger_entries, worker_config=worker_config)
    blockers = _tick_blockers(worker_config)
    plans: list[dict[str, Any]] = []

    if ledger_read["status"] == "error":
        blockers.append(
            {
                "reason": "ledger_read_failed",
                "path": ledger_read.get("path"),
                "error": ledger_read.get("error"),
            }
        )

    replay_suppressed = isinstance(replay_entry, dict)
    if replay_suppressed:
        blockers.append(
            {
                "reason": "idempotent_replay_suppressed",
                "idempotency_key": idempotency_key,
                "replay_of_tick_id": replay_entry.get("tick_id"),
            }
        )
        status = "dry_run_tick_replayed"
        summary = replay_entry.get("tick_summary") if isinstance(replay_entry.get("tick_summary"), dict) else replay_entry
        ready_count = _safe_int(summary.get("ready_plan_count"))
        blocked_count = _safe_int(summary.get("blocked_plan_count"))
        plan_count = _safe_int(summary.get("plan_count"))
        next_state = str(summary.get("next_state") or previous_state)
        ok = bool(summary.get("ok", True))
    elif not blockers:
        for candidate in worker_config.candidates:
            plan = build_grid_worker_ladder_plan(
                candidate,
                now_utc=started_at,
                grid_step_cents=worker_config.grid_step_cents,
                grid_step_price=worker_config.grid_step_price,
                price_tick=worker_config.price_tick,
                min_volatility_band_percent=worker_config.min_volatility_band_percent,
                max_ladder_legs_per_side=worker_config.max_ladder_legs_per_side,
            )
            plans.append(asdict(plan))
        status = _tick_status(worker_config=worker_config, blockers=blockers, plans=plans)
        ready_count = sum(1 for plan in plans if plan.get("status") == "ladder_plan_ready")
        blocked_count = sum(1 for plan in plans if plan.get("status") != "ladder_plan_ready")
        plan_count = len(plans)
        next_state = _next_state(status)
        ok = not any(blocker["reason"] in {"non_dry_run_not_supported", "ledger_read_failed"} for blocker in blockers)
    else:
        status = _tick_status(worker_config=worker_config, blockers=blockers, plans=plans)
        ready_count = 0
        blocked_count = 0
        plan_count = 0
        next_state = _next_state(status)
        ok = not any(blocker["reason"] in {"non_dry_run_not_supported", "ledger_read_failed"} for blocker in blockers)

    tick_id = _tick_id(
        {
            "worker_id": worker_config.worker_id,
            "started_at_utc": started,
            "idempotency_key": idempotency_key,
            "status": status,
            "config": worker_config.safe_dict(),
            "replay_suppressed": replay_suppressed,
        }
    )
    finished = started
    ledger_write = _write_grid_worker_ledger_entry(
        config=worker_config,
        idempotency_key=idempotency_key,
        tick_id=tick_id,
        recorded_at_utc=finished,
        trigger=trigger,
        status=status,
        ok=ok,
        previous_state=previous_state,
        next_state=next_state,
        candidate_count=len(worker_config.candidates),
        plan_count=plan_count,
        ready_plan_count=ready_count,
        blocked_plan_count=blocked_count,
        replay_suppressed=replay_suppressed,
        replay_of_tick_id=replay_entry.get("tick_id") if isinstance(replay_entry, dict) else None,
    )
    heartbeat_write = _heartbeat_write_result(
        config=worker_config,
        write_heartbeat=write_heartbeat,
        tick_payload={
            "schema_version": GRID_WORKER_HEARTBEAT_SCHEMA_VERSION,
            "updated_at_utc": finished,
            "status": status,
            "ok": ok,
            "tick_id": tick_id,
            "idempotency_key": idempotency_key,
            "replay_suppressed": replay_suppressed,
            "worker_id": worker_config.worker_id,
            "candidate_count": len(worker_config.candidates),
            "plan_count": plan_count,
            "ready_plan_count": ready_count,
            "blocked_plan_count": blocked_count,
            "last_tick_started_at_utc": started,
            "last_tick_finished_at_utc": finished,
            "source": worker_config.source,
            "execution_boundary": "dry_run_preview_only",
            "ledger_path": worker_config.ledger_path,
            "order_preparation_attempted": False,
            "order_submission_attempted": False,
            "no_execution_statement": NO_EXECUTION_STATEMENT,
        },
    )
    return PolymarketGridWorkerDryRunTick(
        schema_version=GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION,
        status=status,
        ok=ok,
        tick_id=tick_id,
        idempotency_key=idempotency_key,
        replay_suppressed=replay_suppressed,
        started_at_utc=started,
        finished_at_utc=finished,
        worker_id=worker_config.worker_id,
        trigger=trigger,
        execution_boundary="dry_run_preview_only",
        previous_state=previous_state,
        next_state=next_state,
        state_transition={
            "idempotency_key": idempotency_key,
            "from": previous_state,
            "to": next_state,
            "changed": previous_state != next_state,
        },
        candidate_count=len(worker_config.candidates),
        plan_count=plan_count,
        ready_plan_count=ready_count,
        blocked_plan_count=blocked_count,
        plans=plans,
        blockers=blockers,
        heartbeat_write=heartbeat_write,
        ledger_read=ledger_read,
        ledger_write=ledger_write,
        config=worker_config.safe_dict(),
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


def build_grid_worker_ladder_plan(
    candidate: dict[str, Any],
    *,
    now_utc: datetime | str | None = None,
    grid_step_cents: int | Decimal | float | str = 1,
    grid_step_price: Decimal | float | str | None = None,
    price_tick: Decimal | float | str | None = None,
    min_volatility_band_percent: Decimal | float | str = Decimal("10"),
    max_ladder_legs_per_side: int = 3,
    lower_band_price: Decimal | float | str | None = None,
    upper_band_price: Decimal | float | str | None = None,
    leg_size: Decimal | float | str | None = None,
) -> PolymarketGridWorkerLadderPlan:
    """Build an inert sell/rebuy ladder preview from one reviewed candidate."""

    generated_at = _coerce_utc(now_utc).isoformat().replace("+00:00", "Z")
    step_price = _positive_decimal(
        grid_step_price if grid_step_price is not None else Decimal(str(grid_step_cents)) / Decimal("100"),
        default=Decimal("0.01"),
    )
    tick = _positive_decimal(price_tick, default=step_price)
    step_price = _quantize_to_tick(step_price, tick=tick, rounding=ROUND_UP)
    step_cents = max(1, int((step_price * Decimal("100")).to_integral_value(rounding=ROUND_UP)))
    max_legs = max(0, int(max_ladder_legs_per_side))
    min_band = _decimal(min_volatility_band_percent) or Decimal("10")

    market_slug = _first_text(candidate, ("market_slug", "event_slug", "slug", "market"))
    title = _first_text(candidate, ("title", "market_title", "event_title", "question", "name"))
    token_id = _first_text(candidate, ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId"))
    outcome_side = _first_text(candidate, ("side", "outcome", "outcome_name")) or "position"
    anchor = _first_decimal(candidate, ("current_price", "cur_price", "curPrice", "market_price", "price"))
    size = _decimal(leg_size) or _first_decimal(candidate, ("size", "quantity", "shares", "balance"))
    explicit_lower = _decimal(lower_band_price) or _first_decimal(candidate, ("lower_band_price", "band_low", "recent_low_price"))
    explicit_upper = _decimal(upper_band_price) or _first_decimal(candidate, ("upper_band_price", "band_high", "recent_high_price"))
    volatility = _volatility_band_percent(candidate, anchor=anchor, lower=explicit_lower, upper=explicit_upper)

    missing = _missing_inputs(token_id=token_id, anchor=anchor, size=size, volatility=volatility)
    blockers: list[dict[str, Any]] = []
    lower: Decimal | None = None
    upper: Decimal | None = None

    if not missing and anchor is not None and volatility is not None:
        lower, upper = _resolve_band(anchor=anchor, volatility=volatility, lower=explicit_lower, upper=explicit_upper)
        if volatility < min_band:
            blockers.append(
                {
                    "reason": "volatility_band_below_grid_threshold",
                    "volatility_band_percent": str(volatility),
                    "min_volatility_band_percent": str(min_band),
                }
            )
        if lower is None or upper is None or lower >= upper:
            blockers.append(
                {
                    "reason": "invalid_price_band",
                    "lower_band_price": str(lower) if lower is not None else None,
                    "upper_band_price": str(upper) if upper is not None else None,
                }
            )
        elif not lower <= anchor <= upper:
            blockers.append(
                {
                    "reason": "anchor_price_outside_band",
                    "anchor_price": str(anchor),
                    "lower_band_price": str(lower),
                    "upper_band_price": str(upper),
                }
            )

    legs: list[PolymarketGridWorkerLadderLeg] = []
    if not missing and not blockers and anchor is not None and lower is not None and upper is not None and size is not None:
        legs = _build_ladder_legs(
            token_id=token_id,
            anchor=anchor,
            lower=lower,
            upper=upper,
            size=size,
            grid_step_price=step_price,
            price_tick=tick,
            max_ladder_legs_per_side=max_legs,
        )
        if not legs:
            blockers.append(
                {
                    "reason": "price_band_too_narrow_for_grid_step",
                    "grid_step_cents": step_cents,
                    "grid_step_price": str(step_price),
                    "price_tick": str(tick),
                    "anchor_price": str(anchor),
                    "lower_band_price": str(lower),
                    "upper_band_price": str(upper),
                }
            )

    if missing:
        status = "blocked_missing_ladder_inputs"
    elif blockers:
        status = "blocked_ladder_plan"
    else:
        status = "ladder_plan_ready"

    return PolymarketGridWorkerLadderPlan(
        schema_version=GRID_WORKER_LADDER_SCHEMA_VERSION,
        status=status,
        generated_at_utc=generated_at,
        market_slug=market_slug,
        title=title,
        token_id=token_id,
        outcome_side=outcome_side,
        anchor_price=str(_quantize_price(anchor, tick=tick)) if anchor is not None else None,
        lower_band_price=str(_quantize_price(lower, tick=tick, rounding=ROUND_DOWN)) if lower is not None else None,
        upper_band_price=str(_quantize_price(upper, tick=tick, rounding=ROUND_UP)) if upper is not None else None,
        volatility_band_percent=str(volatility) if volatility is not None else None,
        min_volatility_band_percent=str(min_band),
        grid_step_cents=step_cents,
        grid_step_price=str(step_price),
        price_tick=str(tick),
        max_ladder_legs_per_side=max_legs,
        leg_count=len(legs),
        legs=[asdict(leg) for leg in legs],
        missing_inputs=missing,
        blockers=blockers,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


def _build_ladder_legs(
    *,
    token_id: str,
    anchor: Decimal,
    lower: Decimal,
    upper: Decimal,
    size: Decimal,
    grid_step_price: Decimal,
    price_tick: Decimal,
    max_ladder_legs_per_side: int,
) -> list[PolymarketGridWorkerLadderLeg]:
    step = grid_step_price
    legs: list[PolymarketGridWorkerLadderLeg] = []
    sequence = 1
    for level in range(1, max_ladder_legs_per_side + 1):
        sell_price = _quantize_price(anchor + (step * level), tick=price_tick, rounding=ROUND_UP)
        if sell_price <= upper and sell_price <= Decimal("0.99"):
            legs.append(
                PolymarketGridWorkerLadderLeg(
                    leg_id=f"{token_id}|sell|{sell_price}",
                    sequence=sequence,
                    action="sell_existing_position_preview",
                    side="sell",
                    limit_price=str(sell_price),
                    trigger_price=str(sell_price),
                    size=str(size),
                    order_preparation_allowed=False,
                    order_submission_allowed=False,
                    rationale="Preview a sell leg above the current anchor; execution remains gated elsewhere.",
                )
            )
            sequence += 1

        rebuy_price = _quantize_price(anchor - (step * level), tick=price_tick, rounding=ROUND_DOWN)
        if rebuy_price >= lower and rebuy_price >= Decimal("0.01"):
            legs.append(
                PolymarketGridWorkerLadderLeg(
                    leg_id=f"{token_id}|buy|{rebuy_price}",
                    sequence=sequence,
                    action="rebuy_after_sell_preview",
                    side="buy",
                    limit_price=str(rebuy_price),
                    trigger_price=str(rebuy_price),
                    size=str(size),
                    order_preparation_allowed=False,
                    order_submission_allowed=False,
                    rationale="Preview a rebuy leg below the current anchor; execution remains gated elsewhere.",
                )
            )
            sequence += 1
    return legs


def _missing_inputs(
    *,
    token_id: str,
    anchor: Decimal | None,
    size: Decimal | None,
    volatility: Decimal | None,
) -> list[str]:
    missing: list[str] = []
    if not token_id:
        missing.append("token_id")
    if anchor is None:
        missing.append("current_price")
    if size is None or size <= 0:
        missing.append("size")
    if volatility is None:
        missing.append("volatility_band_percent")
    return missing


def _resolve_band(
    *,
    anchor: Decimal,
    volatility: Decimal,
    lower: Decimal | None,
    upper: Decimal | None,
) -> tuple[Decimal, Decimal]:
    if lower is not None and upper is not None:
        return lower, upper
    half_width = anchor * (volatility / Decimal("100")) / Decimal("2")
    resolved_lower = lower if lower is not None else max(Decimal("0.01"), anchor - half_width)
    resolved_upper = upper if upper is not None else min(Decimal("0.99"), anchor + half_width)
    return resolved_lower, resolved_upper


def _volatility_band_percent(
    candidate: dict[str, Any],
    *,
    anchor: Decimal | None,
    lower: Decimal | None,
    upper: Decimal | None,
) -> Decimal | None:
    explicit = _first_decimal(
        candidate,
        (
            "volatility_band_percent",
            "oscillation_band_percent",
            "recent_range_percent",
            "absolute_move_percent",
            "volatility_percent",
        ),
    )
    if explicit is not None:
        return explicit
    if anchor is None or anchor == 0 or lower is None or upper is None:
        return None
    return abs((upper - lower) / anchor) * Decimal("100")


def _coerce_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_decimal(item: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = _decimal(item.get(key))
        if value is not None:
            return value
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _positive_decimal(value: Any, *, default: Decimal) -> Decimal:
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        return default
    return parsed


def _grid_step_price(payload: dict[str, Any]) -> Decimal:
    explicit = _decimal(payload.get("grid_step_price") or payload.get("grid_step") or payload.get("step_price"))
    if explicit is not None and explicit > 0:
        return explicit
    cents = _decimal(payload.get("grid_step_cents"))
    if cents is not None and cents > 0:
        return cents / Decimal("100")
    return Decimal("0.01")


def _quantize_to_tick(value: Decimal, *, tick: Decimal, rounding: str = ROUND_DOWN) -> Decimal:
    tick = tick if tick > 0 else Decimal("0.01")
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _quantize_price(value: Decimal | None, *, tick: Decimal = Decimal("0.01"), rounding: str = ROUND_DOWN) -> Decimal:
    if value is None:
        return Decimal("0.00")
    bounded = min(max(value, Decimal("0.01")), Decimal("0.99"))
    quantized = _quantize_to_tick(bounded, tick=tick, rounding=rounding)
    return min(max(quantized, Decimal("0.01")), Decimal("0.99"))


def _coerce_worker_config(config: PolymarketGridWorkerConfig | dict[str, Any]) -> PolymarketGridWorkerConfig:
    if isinstance(config, PolymarketGridWorkerConfig):
        return config
    return build_grid_worker_config(config)


def _read_json_object(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            payload = json.loads(raw.decode(encoding))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    else:
        raise ValueError(f"could not decode JSON file: {path}")
    if not isinstance(payload, dict):
        raise ValueError(f"grid worker config must contain a JSON object: {path}")
    return payload


def _bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candidates", "selected_candidates", "candidate_rows", "positions"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
    return []


def _status_for_config(config: PolymarketGridWorkerConfig) -> str:
    if not config.enabled:
        return "disabled"
    if not config.dry_run:
        return "blocked_non_dry_run_not_supported"
    if not config.candidates:
        return "idle_no_candidates"
    return "dry_run_ready"


def _tick_blockers(config: PolymarketGridWorkerConfig) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not config.enabled:
        blockers.append({"reason": "worker_disabled"})
    if not config.dry_run:
        blockers.append(
            {
                "reason": "non_dry_run_not_supported",
                "detail": "This worker slice is dry-run only and cannot prepare or submit orders.",
            }
        )
    if not config.candidates:
        blockers.append({"reason": "no_grid_candidates"})
    return blockers


def _tick_status(
    *,
    worker_config: PolymarketGridWorkerConfig,
    blockers: list[dict[str, Any]],
    plans: list[dict[str, Any]],
) -> str:
    reasons = {blocker["reason"] for blocker in blockers}
    if "ledger_read_failed" in reasons:
        return "blocked_ledger_read_failed"
    if "non_dry_run_not_supported" in reasons:
        return "blocked_non_dry_run_not_supported"
    if "worker_disabled" in reasons:
        return "disabled"
    if "no_grid_candidates" in reasons:
        return "idle_no_candidates"
    if not plans and worker_config.candidates:
        return "blocked_no_ladder_plans"
    return "dry_run_tick_completed"


def _next_state(status: str) -> str:
    if status == "dry_run_tick_completed":
        return "dry_run_completed"
    if status == "dry_run_tick_replayed":
        return "dry_run_completed"
    if status == "idle_no_candidates":
        return "idle"
    if status == "disabled":
        return "disabled"
    if status == "blocked_non_dry_run_not_supported":
        return "blocked"
    return "blocked"


def _tick_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dry_run_idempotency_key(config: PolymarketGridWorkerConfig, *, trigger: str) -> str:
    payload = {
        "schema_version": GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION,
        "worker_id": config.worker_id,
        "source": config.source,
        "trigger": trigger,
        "enabled": config.enabled,
        "dry_run": config.dry_run,
        "execution_boundary": "dry_run_preview_only",
        "grid_step_cents": config.grid_step_cents,
        "grid_step_price": config.grid_step_price,
        "price_tick": config.price_tick,
        "min_volatility_band_percent": config.min_volatility_band_percent,
        "max_ladder_legs_per_side": config.max_ladder_legs_per_side,
        "candidates": config.candidates,
    }
    return _tick_id(payload)


def _read_grid_worker_ledger(
    ledger_path: str | None,
    *,
    worker_id: str,
    idempotency_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not ledger_path:
        return [], {"status": "skipped", "reason": "ledger_path_missing", "path": None, "entry_count": 0}

    path = Path(ledger_path)
    if not path.exists():
        return [], {"status": "missing", "path": str(path), "entry_count": 0, "matched_idempotency_key": False}

    entries: list[dict[str, Any]] = []
    replay_entry: dict[str, Any] | None = None
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"ledger line {line_number} is not a JSON object")
            if str(payload.get("worker_id") or "") != worker_id:
                continue
            entries.append(payload)
            if payload.get("idempotency_key") == idempotency_key and replay_entry is None:
                replay_entry = payload
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], {"status": "error", "path": str(path), "entry_count": 0, "error": str(exc)}

    latest_state = _ledger_previous_state(entries, worker_config=None)
    result = {
        "status": "read",
        "path": str(path),
        "entry_count": len(entries),
        "latest_state": latest_state,
        "matched_idempotency_key": replay_entry is not None,
    }
    if replay_entry is not None:
        result["_replay_entry"] = replay_entry
        result["replay_of_tick_id"] = replay_entry.get("tick_id")
    return entries, result


def _ledger_previous_state(
    entries: list[dict[str, Any]],
    *,
    worker_config: PolymarketGridWorkerConfig | None,
) -> str:
    for entry in reversed(entries):
        summary = entry.get("tick_summary") if isinstance(entry.get("tick_summary"), dict) else entry
        state = summary.get("next_state") if isinstance(summary, dict) else None
        if state is not None and str(state).strip():
            return str(state).strip()
    if worker_config is None:
        return ""
    return worker_config.state


def _write_grid_worker_ledger_entry(
    *,
    config: PolymarketGridWorkerConfig,
    idempotency_key: str,
    tick_id: str,
    recorded_at_utc: str,
    trigger: str,
    status: str,
    ok: bool,
    previous_state: str,
    next_state: str,
    candidate_count: int,
    plan_count: int,
    ready_plan_count: int,
    blocked_plan_count: int,
    replay_suppressed: bool,
    replay_of_tick_id: Any,
) -> dict[str, Any]:
    if replay_suppressed:
        return {
            "status": "skipped",
            "reason": "idempotency_key_already_recorded",
            "path": config.ledger_path,
            "idempotency_key": idempotency_key,
            "replay_of_tick_id": replay_of_tick_id,
        }
    if not config.ledger_path:
        return {"status": "skipped", "reason": "ledger_path_missing", "path": None}

    entry = {
        "schema_version": GRID_WORKER_LEDGER_ENTRY_SCHEMA_VERSION,
        "worker_id": config.worker_id,
        "idempotency_key": idempotency_key,
        "tick_id": tick_id,
        "recorded_at_utc": recorded_at_utc,
        "trigger": trigger,
        "execution_boundary": "dry_run_preview_only",
        "status": status,
        "ok": ok,
        "previous_state": previous_state,
        "next_state": next_state,
        "candidate_count": candidate_count,
        "plan_count": plan_count,
        "ready_plan_count": ready_plan_count,
        "blocked_plan_count": blocked_plan_count,
        "replay_suppressed": False,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_EXECUTION_STATEMENT,
        "tick_summary": {
            "status": status,
            "ok": ok,
            "tick_id": tick_id,
            "idempotency_key": idempotency_key,
            "previous_state": previous_state,
            "next_state": next_state,
            "candidate_count": candidate_count,
            "plan_count": plan_count,
            "ready_plan_count": ready_plan_count,
            "blocked_plan_count": blocked_plan_count,
        },
    }
    path = Path(config.ledger_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str) + "\n")
    except OSError as exc:
        return {"status": "error", "path": str(path), "error": str(exc), "idempotency_key": idempotency_key}
    return {"status": "written", "path": str(path), "idempotency_key": idempotency_key, "tick_id": tick_id}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _heartbeat_write_result(
    *,
    config: PolymarketGridWorkerConfig,
    write_heartbeat: bool,
    tick_payload: dict[str, Any],
) -> dict[str, Any]:
    if not write_heartbeat:
        return {"status": "skipped", "reason": "heartbeat_write_disabled", "path": config.heartbeat_path}
    if not config.heartbeat_path:
        return {"status": "skipped", "reason": "heartbeat_path_missing", "path": None}
    path = Path(config.heartbeat_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tick_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "written", "path": str(path)}


__all__ = [
    "GRID_WORKER_CONFIG_SCHEMA_VERSION",
    "GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION",
    "GRID_WORKER_HEARTBEAT_SCHEMA_VERSION",
    "GRID_WORKER_LADDER_SCHEMA_VERSION",
    "GRID_WORKER_LEDGER_ENTRY_SCHEMA_VERSION",
    "GRID_WORKER_STATUS_SCHEMA_VERSION",
    "PolymarketGridWorkerConfig",
    "PolymarketGridWorkerDryRunTick",
    "PolymarketGridWorkerStatus",
    "PolymarketGridWorkerLadderLeg",
    "PolymarketGridWorkerLadderPlan",
    "build_grid_worker_config",
    "build_grid_worker_ladder_plan",
    "build_grid_worker_status",
    "load_grid_worker_config",
    "run_grid_worker_dry_run_tick",
]
