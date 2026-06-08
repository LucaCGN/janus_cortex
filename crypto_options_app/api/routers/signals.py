from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.frontend import render_frontend_index
from crypto_options_app.signals.validation.models import model_to_dict
from crypto_options_app.signals.validation.registry import (
    get_signal_spec,
    list_signal_specs,
    signal_catalog_summary,
)
from crypto_options_app.signals.validation.result_store import (
    create_review_request,
    queue_status,
    sync_signal_catalog,
    validation_results,
    validation_status,
)


router = APIRouter(prefix="/v1/crypto-options-app/signals", tags=["crypto-options-app-signals"])


class SignalReviewPayload(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    requested_by: str = "signal-backtest-dashboard"


@router.get("/catalog")
def crypto_options_signal_catalog() -> dict:
    """Return read-only signal hypotheses for validation and web UI rendering."""

    return signal_catalog_summary()


@router.get("/catalog/{signal_id}")
def crypto_options_signal_catalog_item(signal_id: str) -> dict:
    """Return one read-only signal hypothesis."""

    spec = get_signal_spec(signal_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    payload = model_to_dict(spec)
    payload["structural_blockers"] = list(spec.structural_blockers)
    return payload


@router.get("/backtests", response_class=HTMLResponse)
def crypto_options_signal_backtests_dashboard() -> HTMLResponse:
    """Render the read-only signal validation lab dashboard."""

    return HTMLResponse(render_frontend_index())


@router.get("/validation/status")
def crypto_options_signal_validation_status(
    request: Request,
    include_signals: bool = Query(default=True),
    signal_limit: int | None = Query(default=None, ge=1, le=500),
) -> dict:
    """Return compact read-only validation status for all signal variants."""

    with _connect_request_db(request) as conn:
        _sync_signal_catalog_if_needed(conn, enqueue=True)
        return validation_status(conn, include_signals=include_signals, signal_limit=signal_limit)


@router.get("/selection")
def crypto_options_signal_selection(request: Request) -> dict:
    """Return curated read-only signal building blocks for strategy-design review."""

    with _connect_request_db(request) as conn:
        _sync_signal_catalog_if_needed(conn, enqueue=True)
        status = validation_status(conn)

    signals = [_hydrate_signal_status(dict(row)) for row in status.get("signals", [])]
    curated = _curate_signal_selection(signals)
    return {
        "schema_version": "crypto_options_signal_selection_v1",
        "generated_at_utc": status.get("generated_at_utc"),
        "signal_count": len(curated["signals"]),
        "orders_allowed": False,
        "live_trading_authorized": False,
        **curated,
    }


@router.get("/validation/results")
def crypto_options_signal_validation_results(request: Request, limit: int = 100) -> dict:
    """Return recent read-only signal validation phase results."""

    with _connect_request_db(request) as conn:
        _sync_signal_catalog_if_needed(conn, enqueue=True)
        return validation_results(conn, limit=limit)


@router.get("/replay/backtests")
def crypto_options_signal_replay_backtests(request: Request, limit: int = 1000) -> dict:
    """Return historical signal replay/backtest evidence without live-shadow rows."""

    with _connect_request_db(request) as conn:
        _sync_signal_catalog_if_needed(conn, enqueue=True)
        return _signal_replay_summary(
            conn,
            phases=("last_week_backtest", "last_month_backtest", "random_sample_backtest"),
            mode="historical_backtest",
            limit=limit,
        )


@router.get("/replay/live")
def crypto_options_signal_live_replay(request: Request, limit: int = 1000) -> dict:
    """Return read-only live-shadow signal replay evidence separate from historical backtests."""

    with _connect_request_db(request) as conn:
        _sync_signal_catalog_if_needed(conn, enqueue=True)
        return _signal_replay_summary(conn, phases=("live_shadow_test",), mode="live_replay", limit=limit)


@router.get("/validation/queue")
def crypto_options_signal_validation_queue(request: Request) -> dict:
    """Return queue ownership and phase status for signal validation workers."""

    with _connect_request_db(request) as conn:
        _sync_signal_catalog_if_needed(conn, enqueue=True)
        return queue_status(conn)


@router.post("/validation/request-review")
def crypto_options_signal_validation_request_review(request: Request, payload: SignalReviewPayload) -> dict:
    """Persist an operator review request for the 15-minute design reviewer."""

    with _connect_request_db(request) as conn:
        try:
            request_row = create_review_request(conn, message=payload.message, requested_by=payload.requested_by)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "request": request_row}


def _signal_replay_summary(conn, phases: tuple[str, ...], *, mode: str, limit: int) -> dict:
    results = validation_results(conn, limit=max(int(limit), 1)).get("results", [])
    phase_set = set(phases)
    filtered = [dict(row) for row in results if str(row.get("phase") or "") in phase_set]
    latest_rows = _latest_signal_phase_rows(filtered)

    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    phase_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    total_samples = 0
    passed_count = 0
    hit_rate_sum = 0.0
    hit_rate_count = 0

    for row in latest_rows[: max(int(limit), 1)]:
        status = str(row.get("status") or "")
        phase = str(row.get("phase") or "")
        status_counts[status.upper()] = status_counts.get(status.upper(), 0) + 1
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        if status.lower() == "passed":
            passed_count += 1
        sample_count = int(row.get("sample_count") or 0)
        total_samples += sample_count
        hit_rate = _first_float(row.get("hit_rate"))
        if hit_rate is not None:
            hit_rate_sum += hit_rate
            hit_rate_count += 1
        for blocker in row.get("blockers") or []:
            blocker_key = str(blocker)
            blocker_counts[blocker_key] = blocker_counts.get(blocker_key, 0) + 1
        rows.append(
            {
                "signal_id": row.get("signal_id"),
                "version": row.get("version") or "v1",
                "phase": phase,
                "status": row.get("status"),
                "evaluated_at_utc": row.get("evaluated_at_utc"),
                "sample_count": sample_count,
                "hit_rate": hit_rate,
                "average_forward_return": _first_float(row.get("average_forward_return")),
                "blockers": row.get("blockers") or [],
                "metrics": row.get("metrics") or {},
            }
        )

    return {
        "schema_version": "crypto_options_signal_replay_summary_v1",
        "mode": mode,
        "phases": list(phases),
        "orders_allowed": False,
        "live_trading_authorized": False,
        "result_count": len(rows),
        "passed_count": passed_count,
        "total_samples": total_samples,
        "average_hit_rate": (hit_rate_sum / hit_rate_count) if hit_rate_count else None,
        "status_counts": dict(sorted(status_counts.items())),
        "phase_counts": dict(sorted(phase_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))),
        "results": rows,
    }


def _latest_signal_phase_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("signal_id") or ""), str(row.get("version") or "v1"), str(row.get("phase") or ""))
        current = latest.get(key)
        if current is None or str(row.get("evaluated_at_utc") or "") >= str(current.get("evaluated_at_utc") or ""):
            latest[key] = row
    return sorted(latest.values(), key=lambda item: str(item.get("evaluated_at_utc") or ""), reverse=True)


def _hydrate_signal_status(row: dict) -> dict:
    signal_id = str(row.get("signal_id") or "")
    spec = get_signal_spec(signal_id)
    hydrated = dict(row)
    if spec is not None:
        spec_payload = model_to_dict(spec)
        for key, value in spec_payload.items():
            hydrated.setdefault(key, value)
        hydrated["description"] = spec_payload.get("description")
        hydrated["win_criteria"] = spec_payload.get("win_criteria")
        hydrated["validation_target"] = spec_payload.get("validation_target")
        hydrated["signal_payload_example"] = spec_payload.get("signal_payload_example", {})
        hydrated["required_data_blocks"] = list(spec.required_data_blocks)
    hydrated.setdefault("source_blocks", hydrated.get("required_data_blocks") or [])
    hydrated.setdefault("sources", [])
    return hydrated


def _curate_signal_selection(signals: list[dict]) -> dict:
    quotas = {
        "outcome_prediction": 14,
        "hedge_ratio": 8,
        "side_start": 6,
        "buy_rebound": 5,
        "cashout_rebuy": 5,
        "grid_spacing": 5,
        "support_resistance": 5,
        "trend_regime": 4,
        "liquidity_depth": 4,
        "stale_order_review": 4,
        "final_minute": 4,
        "grid_type": 4,
        "grid_count": 3,
        "latency_quality": 3,
    }
    by_type: dict[str, list[dict]] = {}
    for row in signals:
        by_type.setdefault(str(row.get("signal_type") or row.get("type") or "unknown"), []).append(row)
    superseded_ids = _superseded_signal_ids(signals)

    selected_ids: set[str] = set()
    for signal_type, rows in by_type.items():
        quota = quotas.get(signal_type, 3)
        eligible = [
            row
            for row in rows
            if str(row.get("promotion_state")) in {"STRUCTURAL_PASS", "PROMOTION_READY"}
            and str(row.get("signal_id") or "") not in superseded_ids
        ]
        ranked = sorted(eligible, key=_signal_selection_score, reverse=True)
        selected_ids.update(str(row.get("signal_id")) for row in ranked[:quota])

    curated_rows: list[dict] = []
    coverage = {"A": 0, "B": 0, "C": 0}
    action_state_counts: dict[str, int] = {}
    selected_count = 0
    review_count = 0
    revision_candidate_count = 0
    discarded_count = 0
    for row in sorted(signals, key=lambda item: (_selection_sort_key(item, selected_ids), str(item.get("signal_id") or ""))):
        signal_id = str(row.get("signal_id") or "")
        promotion_state = str(row.get("promotion_state") or "")
        tier = "structural"
        if signal_id in superseded_ids:
            tier = "discarded"
            discarded_count += 1
        elif signal_id in selected_ids:
            tier = "selected"
            selected_count += 1
            for block in row.get("source_blocks") or row.get("required_data_blocks") or []:
                if block in coverage:
                    coverage[block] += 1
        elif promotion_state == "NEEDS_V2_REVIEW":
            revision_candidate_count += 1
            if _is_discarded_directional_signal(row):
                tier = "discarded"
                discarded_count += 1
            else:
                tier = "revision_candidate"
        elif promotion_state not in {"STRUCTURAL_PASS", "PROMOTION_READY"}:
            tier = "review"
            review_count += 1

        action = _signal_action_plan(row, tier=tier, selected=signal_id in selected_ids, superseded=signal_id in superseded_ids)
        action_state_counts[action["action_state"]] = action_state_counts.get(action["action_state"], 0) + 1
        curated_rows.append(
            row
            | {
                "selection_tier": tier,
                "selection_reason": _selection_reason(row, selected=signal_id in selected_ids),
                "selection_score": round(_signal_selection_score(row), 4),
                "action_state": action["action_state"],
                "action_owner": action["action_owner"],
                "next_action": action["next_action"],
                "selection_next_action": action["next_action"],
                "next_action_detail": action["next_action_detail"],
                "orders_allowed": False,
                "live_trading_authorized": False,
            }
        )

    return {
        "selected_count": selected_count,
        "review_count": review_count,
        "revision_candidate_count": revision_candidate_count,
        "discarded_count": discarded_count,
        "action_state_counts": action_state_counts,
        "coverage": coverage,
        "signals": curated_rows,
        "selection_policy": {
            "selected_meaning": "strategy-design building block, not live-trading approval",
            "promotion_required_before_live_strategy_use": True,
            "max_versions_target": "V5 unless a type has no viable coverage",
            "discarded_rule": "weak crypto-only directional rows with below-baseline hit rate or negative forward return leave the primary set",
            "review_label": "NEEDS REVISION; the internal DB state may still be NEEDS_V2_REVIEW for compatibility",
            "action_states": {
                "STRICT_REPLAY_REQUIRED": "selected building block that still needs strict no-lookahead replay before strategy dependency",
                "STRUCTURAL_ALTERNATE": "structural pass retained as fallback or coverage evidence",
                "REVISION_REQUIRED": "needs a concrete stricter variant or conversion before reuse",
                "RETIRED_FROM_PRIMARY": "removed from primary directional candidate set",
                "VALIDATION_REQUIRED": "missing or incomplete validation phase",
            },
            "quotas_by_signal_type": quotas,
        },
    }


def _selection_sort_key(row: dict, selected_ids: set[str]) -> tuple[int, int, int]:
    signal_id = str(row.get("signal_id") or "")
    promotion_state = str(row.get("promotion_state") or "")
    if signal_id in selected_ids:
        tier = 0
    elif promotion_state == "STRUCTURAL_PASS":
        tier = 1
    elif promotion_state == "NEEDS_V2_REVIEW":
        tier = 2
    else:
        tier = 3
    impact_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    impact = impact_order.get(str(row.get("impact_if_degraded") or "low"), 4)
    return (tier, impact, -int(row.get("distinct_event_count") or 0))


def _signal_selection_score(row: dict) -> float:
    hit_rate = _first_float(row.get("live_shadow_hit_rate"), row.get("latest_phase_hit_rate"), row.get("latest_hit_rate"))
    average_forward_return = _first_float(
        row.get("live_shadow_average_forward_return"),
        row.get("latest_phase_average_forward_return"),
        row.get("latest_average_forward_return"),
    )
    source_blocks = set(row.get("source_blocks") or row.get("required_data_blocks") or [])
    sources = set(row.get("sources") or [])
    impact_bonus = {"critical": 0.18, "high": 0.12, "medium": 0.06, "low": 0.02}.get(str(row.get("impact_if_degraded") or "low"), 0.0)
    block_bonus = 0.03 * len(source_blocks)
    profile_bonus = 0.05 if "B" in source_blocks or "profiles" in sources else 0.0
    option_bonus = 0.04 if "C" in source_blocks or "optionprice" in sources else 0.0
    sample_bonus = min(int(row.get("distinct_event_count") or 0), 100) / 1000.0
    forward_bonus = max(min(average_forward_return or 0.0, 0.1), -0.1)
    crypto_directional_penalty = 0.08 if _is_weak_crypto_directional_shape(row) else 0.0
    return (hit_rate or 0.0) + impact_bonus + block_bonus + profile_bonus + option_bonus + sample_bonus + forward_bonus - crypto_directional_penalty


def _selection_reason(row: dict, *, selected: bool) -> str:
    if selected:
        return "top ranked structural candidate for this signal type"
    if row.get("superseded_by_signal_id"):
        return "superseded by a stronger revision and removed from the primary set"
    if row.get("promotion_state") == "NEEDS_V2_REVIEW":
        return "below strict hit/return threshold or needs stronger revision"
    if row.get("promotion_state") == "STRUCTURAL_PASS":
        return "structural pass; lower ranked than selected candidates"
    return "validation incomplete or blocked"


def _signal_action_plan(row: dict, *, tier: str, selected: bool, superseded: bool = False) -> dict[str, str]:
    signal_type = str(row.get("signal_type") or row.get("type") or "unknown")
    variant = str(row.get("variant") or "")
    promotion_state = str(row.get("promotion_state") or "")
    queue_status = str(row.get("queue_status") or row.get("status") or "")
    source_blocks = set(row.get("source_blocks") or row.get("required_data_blocks") or [])
    strict_reasons = [str(reason) for reason in row.get("strict_review_reasons") or row.get("strict_review") or []]
    block_label = "/".join(sorted(source_blocks)) or "unknown source blocks"
    superseded_by_signal_id = str(row.get("superseded_by_signal_id") or "").strip()

    if tier == "discarded":
        if superseded and superseded_by_signal_id:
            return {
                "action_state": "RETIRED_FROM_PRIMARY",
                "action_owner": "signal-design-reviewer",
                "next_action": "Retire from the primary candidate set; keep only as historical baseline until the superseding revision fails strict replay or exposes a coverage gap.",
                "next_action_detail": f"{signal_type}/{variant} is superseded by {superseded_by_signal_id}. Do not route it into new strategy dependencies while the newer revision remains intact.",
            }
        return {
            "action_state": "RETIRED_FROM_PRIMARY",
            "action_owner": "signal-design-reviewer",
            "next_action": "Retire from primary directional set; do not create more crypto-only directional versions. Revive only as an avoid gate or A+B/A+C confluence variant with strict baseline evidence.",
            "next_action_detail": f"{signal_type}/{variant} is a weak A-only directional shape. Keep its history for negative evidence, but do not route it into strategy components unless a non-directional or confirmed variant is created.",
        }

    if promotion_state not in {"STRUCTURAL_PASS", "PROMOTION_READY", "NEEDS_V2_REVIEW"}:
        return {
            "action_state": "VALIDATION_REQUIRED",
            "action_owner": "signal-validator-worker",
            "next_action": "Run the queued validation phases for this revision before selection, retirement, or strategy dependency.",
            "next_action_detail": f"{signal_type}/{variant} is {queue_status or promotion_state}; validate last-week, last-month, random-sample, and live-shadow phases before it can be compared against the selected set.",
        }

    if tier in {"review", "revision_candidate"}:
        if signal_type == "support_resistance" and "ifcm_pivot_distance" in variant:
            return {
                "action_state": "REVISION_REQUIRED",
                "action_owner": "signal-design-reviewer",
                "next_action": "Create or run the V4 pivot-distance revision with option-path confirmation, rebound/reclaim evidence, and positive forward-return filtering.",
                "next_action_detail": "The IFCM pivot row may be useful as a grid reference, but not as a standalone directional signal. Use option Up/Down path confirmation before it can influence rebound, spacing, or side-start logic.",
            }
        return {
            "action_state": "REVISION_REQUIRED",
            "action_owner": "signal-design-reviewer",
            "next_action": "Design a stricter revision up to V5 or convert this row into a non-directional gate before reuse.",
            "next_action_detail": f"{signal_type}/{variant} needs revised criteria because the current evidence is not strategy-grade.",
        }

    if tier == "structural":
        return {
            "action_state": "STRUCTURAL_ALTERNATE",
            "action_owner": "signal-reviewer",
            "next_action": "Keep as fallback structural evidence; do not promote until selected peers fail, coverage gaps remain, or strict replay proves this variant materially better.",
            "next_action_detail": f"{signal_type}/{variant} passed structurally but ranked below the selected set. Schedule strict replay only after the selected queue is reviewed.",
        }

    if selected:
        needs_strict_replay = bool(row.get("needs_strict_replay")) or bool(strict_reasons)
        if promotion_state == "PROMOTION_READY" and not needs_strict_replay:
            return {
                "action_state": "PROMOTION_READY",
                "action_owner": "strategy-designer",
                "next_action": "Use as an eligible strategy dependency; keep monitoring live-shadow drift and do not promote a strategy unless its own shadow/live-replay economics pass policy.",
                "next_action_detail": f"{signal_type}/{variant} is selected from {block_label} and has already passed the strict signal promotion criteria. Strategy dependency is allowed for read-only replay and shadow tests.",
            }
        if "B" in source_blocks:
            next_action = "Run strict profile-position replay with disjoint events; compare distribution-vs-settlement and reject stale or low-coverage profile rows before strategy dependency."
        elif "C" in source_blocks:
            next_action = "Run strict option-path replay on captured Up/Down frames with fillability, spread, slippage, and forward-return baseline checks before strategy dependency."
        elif "A" in source_blocks:
            next_action = "Run strict crypto-indicator replay against naive baseline; use only as confirm/avoid input until B or C confirmation proves directional value."
        else:
            next_action = "Run strict no-lookahead replay before strategy dependency."
        if strict_reasons:
            next_action += " Address strict-review flags: " + ", ".join(strict_reasons[:3]) + "."
        return {
            "action_state": "STRICT_REPLAY_REQUIRED",
            "action_owner": "strict-replay-worker",
            "next_action": next_action,
            "next_action_detail": f"{signal_type}/{variant} is selected for strategy-design review from {block_label}, but remains read-only until strict replay and live-shadow promotion criteria pass.",
        }

    return {
        "action_state": "VALIDATION_REQUIRED",
        "action_owner": "signal-validator-worker",
        "next_action": "Complete the missing validation phase before selection or retirement.",
        "next_action_detail": f"{signal_type}/{variant} has incomplete validation evidence.",
    }


def _is_discarded_directional_signal(row: dict) -> bool:
    if str(row.get("promotion_state") or "") != "NEEDS_V2_REVIEW":
        return False
    if not _is_weak_crypto_directional_shape(row):
        return False
    hit_rate = _first_float(row.get("live_shadow_hit_rate"), row.get("latest_phase_hit_rate"), row.get("latest_hit_rate"))
    average_forward_return = _first_float(row.get("live_shadow_average_forward_return"), row.get("latest_phase_average_forward_return"), row.get("latest_average_forward_return"))
    min_hit_rate = _first_float(row.get("promotion_min_hit_rate")) or 0.55
    return (hit_rate is not None and hit_rate < min_hit_rate) or (average_forward_return is not None and average_forward_return < 0)


def _is_weak_crypto_directional_shape(row: dict) -> bool:
    source_blocks = set(row.get("source_blocks") or row.get("required_data_blocks") or [])
    signal_type = str(row.get("signal_type") or row.get("type") or "")
    return source_blocks == {"A"} and signal_type in {"outcome_prediction", "side_start", "trend_regime"}


def _first_float(*values) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _superseded_signal_ids(signals: list[dict]) -> set[str]:
    superseded_ids: set[str] = set()
    superseding_by_superseded: dict[str, list[dict]] = {}
    for row in signals:
        supersedes_signal_id = str(row.get("supersedes_signal_id") or "").strip()
        if not supersedes_signal_id:
            continue
        superseded_ids.add(supersedes_signal_id)
        superseding_by_superseded.setdefault(supersedes_signal_id, []).append(row)
    for row in signals:
        signal_id = str(row.get("signal_id") or "").strip()
        if signal_id in superseded_ids:
            row["superseded_by_signal_id"] = _find_terminal_superseding_signal_id(superseding_by_superseded, signal_id)
    return superseded_ids


def _find_terminal_superseding_signal_id(superseding_by_superseded: dict[str, list[dict]], superseded_signal_id: str) -> str | None:
    current = superseded_signal_id
    seen = {superseded_signal_id}
    terminal: str | None = None
    while True:
        candidates = [row for row in superseding_by_superseded.get(current, []) if str(row.get("signal_id") or "").strip()]
        if not candidates:
            return terminal
        next_row = sorted(candidates, key=lambda row: (_signal_selection_score(row), str(row.get("signal_id") or "")), reverse=True)[0]
        next_signal_id = str(next_row.get("signal_id") or "").strip()
        if next_signal_id in seen:
            return terminal or next_signal_id
        terminal = next_signal_id
        current = next_signal_id
        seen.add(next_signal_id)


def _connect_request_db(request: Request):
    config = getattr(request.app.state, "crypto_options_config", DEFAULT_CONFIG)
    db_path = Path(getattr(config, "db_path", DEFAULT_CONFIG.db_path))
    if not db_path.exists():
        initialize_schema(db_path)
    return connect(db_path)


def _sync_signal_catalog_if_needed(conn, *, enqueue: bool) -> bool:
    row = conn.execute("SELECT COUNT(*) AS count FROM signal_specs").fetchone()
    current_count = int(row["count"] or 0) if row is not None else 0
    if current_count != len(list_signal_specs()):
        sync_signal_catalog(conn, enqueue=enqueue)
        return True
    return False
