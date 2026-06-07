from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.connection import connect_read_only
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime
from crypto_options_app.db.postgres_shadow import load_postgres_shadow_parity_report
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.reports.live_dashboard import (
    build_live_dashboard_state,
    create_stop_for_review_request,
    render_live_dashboard_html,
)
from crypto_options_app.reports.system_integrity import _status_file_readiness_rows


router = APIRouter(prefix="/v1/crypto-options-app/dashboard", tags=["crypto-options-app-dashboard"])


class StopForReviewPayload(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.get("", response_class=HTMLResponse)
def crypto_options_live_dashboard() -> HTMLResponse:
    """Render the read-only live strategy dashboard."""

    return HTMLResponse(render_live_dashboard_html())


@router.get("/state")
def crypto_options_live_dashboard_state() -> dict:
    """Return the read-only live strategy dashboard state."""

    return build_live_dashboard_state()


@router.get("/control-center-state")
def crypto_options_control_center_state(request: Request) -> dict[str, Any]:
    """Return compact read-only state for the main command-center frontend."""

    config = getattr(request.app.state, "crypto_options_config", DEFAULT_CONFIG)
    db_path = Path(getattr(config, "db_path", DEFAULT_CONFIG.db_path))
    artifact_root = Path(getattr(config, "artifact_root", DEFAULT_CONFIG.artifact_root))
    if not db_path.exists():
        initialize_schema(db_path)
    live_state = build_live_dashboard_state(artifact_root=artifact_root)
    force_refresh = str(request.query_params.get("refresh") or "").lower() in {"1", "true", "yes"}
    if not force_refresh:
        cached = _fresh_control_center_cache(artifact_root=artifact_root, live_state=live_state, max_age_seconds=60.0)
        if cached is not None:
            return cached
    if not force_refresh:
        stale_cached = _control_center_cache_fallback(artifact_root=artifact_root, live_state=live_state)
        if stale_cached is not None:
            return stale_cached
    try:
        with _connect_dashboard_read_only(db_path) as conn:
            db_connection_is_postgres = bool(getattr(conn, "is_postgres", False))
            _preflight_dashboard_read(conn)
            portfolio = _portfolio_state(conn, live_state)
            positions = _position_rows(conn)
            orders = _order_rows(conn)
            history = _history_rows(conn)
            events = _event_intelligence_rows(conn)
            profiles = _profile_distribution_rows(conn)
            crypto = _crypto_indicator_rows(conn)
            modules = _merge_source_module_rows(_module_rows(conn), crypto=crypto, profiles=profiles, events=events)
            modules = _overlay_status_file_modules(modules, artifact_root=artifact_root)
            replay_candidate_cache = _replay_candidate_cache_summary(conn, artifact_root=artifact_root)
    except Exception as exc:  # noqa: BLE001 - dashboard must stay online during DB writer contention.
        result = _empty_control_center_state(db_path=db_path, live_state=live_state)
        result["db_read_status"] = "blocked"
        result["db_read_error"] = f"{type(exc).__name__}:{exc}"
        result = _with_control_center_cache(result, artifact_root=artifact_root)
        result["db_read_status"] = "blocked"
        result["db_read_error"] = f"{type(exc).__name__}:{exc}"
        return result

    result = _base_control_center_state(
        db_path=db_path,
        live_state=live_state,
        portfolio=portfolio,
        positions=positions,
        orders=orders,
        history=history,
        events=events,
        profiles=profiles,
        crypto=crypto,
        modules=modules,
        replay_candidate_cache=replay_candidate_cache,
    )
    result["db_read_status"] = "ok"
    result["database_backend"] = str(getattr(config, "database_backend", "unknown"))
    result["db_connection_is_postgres"] = db_connection_is_postgres
    result["postgres_runtime_expected"] = should_use_postgres_runtime(db_path)
    result = _with_control_center_cache(result, artifact_root=artifact_root)
    return result


@router.post("/stop-for-review")
def crypto_options_stop_for_review(payload: StopForReviewPayload) -> dict:
    """Persist a dashboard review request for the automation controller."""

    try:
        request = create_stop_for_review_request(payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "request": request}


def _portfolio_state(conn, live_state: dict[str, Any]) -> dict[str, Any]:
    live_totals = live_state.get("totals") or {}
    ledger_rows = _fetch_all(
        conn,
        """
        SELECT validation_run_id, strategy_or_component_id, started_at_utc, completed_at_utc,
               budget_cap_usd, notional_submitted_usd, notional_filled_usd, realized_pnl_usd,
               open_cost_usd, remaining_validation_budget_usd, cash_balance_before_usd,
               cash_balance_after_usd, cash_balance_status, hard_stop_triggered, stop_reason,
               lifecycle_audit_status, reconciliation_status
        FROM validation_budget_ledger
        ORDER BY rowid DESC
        LIMIT 40
        """,
    )
    latest_balance = next(
        (
            row
            for row in ledger_rows
            if row.get("cash_balance_after_usd") is not None or row.get("cash_balance_before_usd") is not None
        ),
        {},
    )
    total_filled_usd = sum(_safe_float(row.get("notional_filled_usd")) for row in ledger_rows)
    total_realized_pnl_usd = sum(_safe_float(row.get("realized_pnl_usd")) for row in ledger_rows)
    total_open_cost_usd = sum(_safe_float(row.get("open_cost_usd")) for row in ledger_rows)
    return {
        "recorded_cash_balance_usd": _coalesce_float(
            latest_balance.get("cash_balance_after_usd"),
            latest_balance.get("cash_balance_before_usd"),
        ),
        "cash_balance_status": latest_balance.get("cash_balance_status") or "cash_balance_unavailable",
        "cash_balance_updated_at_utc": latest_balance.get("updated_at_utc"),
        "validation_notional_filled_usd": _coalesce_float(
            total_filled_usd,
            live_totals.get("estimated_spent_usd"),
        ),
        "validation_realized_pnl_usd": _coalesce_float(
            live_totals.get("realized_pnl_usd"),
            total_realized_pnl_usd,
        ),
        "validation_open_cost_usd": _coalesce_float(
            live_totals.get("active_cost_usd"),
            total_open_cost_usd,
        ),
        "filled_position_count": live_totals.get("filled_position_count"),
        "settled_position_count": live_totals.get("settled_position_count"),
        "unresolved_position_count": live_totals.get("unresolved_position_count"),
        "ledger_row_count": len(ledger_rows),
        "ledger_rows": ledger_rows,
    }


def _position_rows(conn, *, limit: int = 120) -> list[dict[str, Any]]:
    return _fetch_all(
        conn,
        """
        SELECT p.position_key, p.strategy_id, p.event_token_key, p.shares, p.cost_basis_usd,
               p.status, p.opened_at_utc, p.updated_at_utc,
               et.outcome, et.event_slug, et.symbol, et.token_id,
               e.event_start_time_utc, e.event_end_time_utc
        FROM positions p
        LEFT JOIN event_tokens et ON et.event_token_key = p.event_token_key
        LEFT JOIN events e ON e.event_key = et.event_key
        ORDER BY p.updated_at_utc DESC
        LIMIT ?
        """,
        (limit,),
    )


def _order_rows(conn, *, limit: int = 140) -> list[dict[str, Any]]:
    return _fetch_all(
        conn,
        """
        SELECT o.order_key, o.exchange_order_id, o.status AS order_status, o.submitted_at_utc,
               o.updated_at_utc, i.intent_key, i.strategy_id, i.event_token_key, i.intent_type,
               i.order_type, i.side, i.status AS intent_status,
               et.outcome, et.event_slug, et.symbol, et.token_id
        FROM orders o
        LEFT JOIN execution_intents i ON i.intent_key = o.intent_key
        LEFT JOIN event_tokens et ON et.event_token_key = i.event_token_key
        ORDER BY COALESCE(o.submitted_at_utc, o.updated_at_utc) DESC
        LIMIT ?
        """,
        (limit,),
    )


def _history_rows(conn, *, limit: int = 160) -> list[dict[str, Any]]:
    fills = _fetch_all(
        conn,
        """
        SELECT f.fill_key, f.order_key, f.event_token_key, f.filled_size, f.filled_price,
               f.filled_at_utc, f.inserted_at_utc, o.exchange_order_id, o.status AS order_status,
               i.strategy_id, i.intent_type, i.order_type, i.side,
               et.outcome, et.event_slug, et.symbol
        FROM fills f
        LEFT JOIN orders o ON o.order_key = f.order_key
        LEFT JOIN execution_intents i ON i.intent_key = o.intent_key
        LEFT JOIN event_tokens et ON et.event_token_key = f.event_token_key
        ORDER BY COALESCE(f.filled_at_utc, f.inserted_at_utc) DESC
        LIMIT ?
        """,
        (limit,),
    )
    settlements = _fetch_all(
        conn,
        """
        SELECT settlement_key, event_key, resolved_outcome, settled_at_utc,
               inserted_at_utc, settlement_json
        FROM settlements
        ORDER BY COALESCE(settled_at_utc, inserted_at_utc) DESC
        LIMIT 60
        """,
    )
    rows = [
        row
        | {
            "activity_type": "fill",
            "notional_usd": _safe_float(row.get("filled_size")) * _safe_float(row.get("filled_price")),
        }
        for row in fills
    ]
    rows.extend(row | {"activity_type": "settlement", "notional_usd": None} for row in settlements)
    return sorted(
        rows,
        key=lambda item: str(item.get("filled_at_utc") or item.get("settled_at_utc") or item.get("inserted_at_utc") or ""),
        reverse=True,
    )[:limit]


def _event_intelligence_rows(conn, *, limit: int = 80) -> list[dict[str, Any]]:
    rows = _fetch_all(
        conn,
        """
        SELECT event_path_stats_key, event_key, event_slug, symbol, event_start_time_utc,
               event_end_time_utc, computed_at_utc, first_snapshot_at_utc, last_snapshot_at_utc,
               snapshot_count, up_first_price, up_last_price, up_min_price, up_max_price,
               up_range, up_abs_move_sum, up_abs_move_per_minute, up_stddev,
               event_price_points_json, pre_event_price_points_json, level_first_touch_seconds_json,
               path_direction, path_efficiency, time_to_first_extreme_seconds, avg_swing_distance,
               max_swing_distance, avg_rolling_30s_range, max_rolling_30s_range,
               avg_rolling_60s_range, max_rolling_60s_range, level_crossing_count,
               level_crossings_json, price_bucket_counts_json, near_50c_sample_count,
               extreme_sample_count, rebound_direction_flip_count, strong_rebound_touch_count,
               pair_sum_range, avg_pair_depth_pressure, avg_source_latency_ms, max_source_latency_ms,
               trade_print_count
        FROM polymarket_event_path_stats
        ORDER BY computed_at_utc DESC
        LIMIT ?
        """,
        (limit,),
    )
    for row in rows:
        for key in (
            "event_price_points_json",
            "pre_event_price_points_json",
            "level_first_touch_seconds_json",
            "level_crossings_json",
            "price_bucket_counts_json",
        ):
            row[key.replace("_json", "")] = _decode_json(row.pop(key, "{}"), fallback={})
        row["avg_source_latency_ms"] = _non_negative_latency_ms(row.get("avg_source_latency_ms"))
        row["max_source_latency_ms"] = _non_negative_latency_ms(row.get("max_source_latency_ms"))
    return rows


def _profile_distribution_rows(conn, *, limit: int = 80) -> list[dict[str, Any]]:
    rows = _fetch_all(
        conn,
        """
        SELECT distribution_snapshot_key, event_key, event_slug, symbol, phase, computed_at_utc,
               event_start_time_utc, event_end_time_utc, source_mode, canonical_method,
               profile_count, component_count, up_weight, down_weight, up_share_weight,
               down_share_weight, up_cost_weight, down_cost_weight, up_count_weight,
               down_count_weight, distribution_json, source_json, blockers_json
        FROM v_crypto_options_app_latest_profile_distributions
        ORDER BY computed_at_utc DESC
        LIMIT ?
        """,
        (limit,),
    )
    breakdowns = _profile_distribution_component_breakdowns(
        conn,
        [str(row["distribution_snapshot_key"]) for row in rows if row.get("distribution_snapshot_key")],
    )
    for row in rows:
        row["distribution"] = _decode_json(row.pop("distribution_json", "{}"), fallback={})
        row["source"] = _decode_json(row.pop("source_json", "{}"), fallback={})
        row["blockers"] = _decode_json(row.pop("blockers_json", "[]"), fallback=[])
        row.update(_profile_distribution_summary_metrics(row))
        row["component_breakdown"] = breakdowns.get(str(row.get("distribution_snapshot_key")), _empty_profile_distribution_breakdown())
    return rows


def _profile_distribution_component_breakdowns(
    conn,
    snapshot_keys: list[str],
) -> dict[str, dict[str, Any]]:
    if not snapshot_keys:
        return {}
    placeholders = ",".join("?" for _ in snapshot_keys)
    rows = _fetch_all(
        conn,
        f"""
        SELECT distribution_snapshot_key,
               COALESCE(NULLIF(grade, ''), 'unknown') AS grade,
               COALESCE(NULLIF(trading_style, ''), 'unknown') AS trading_style,
               COALESCE(NULLIF(outcome, ''), 'unknown') AS outcome,
               COUNT(*) AS component_count,
               COUNT(DISTINCT profile_key) AS profile_count,
               SUM(net_shares * final_weight) AS weighted_shares,
               SUM(cost_basis_usd * final_weight) AS weighted_cost,
               SUM(net_notional_usd * final_weight) AS weighted_notional,
               SUM(final_weight) AS contribution_weight
          FROM profile_distribution_components
         WHERE distribution_snapshot_key IN ({placeholders})
         GROUP BY distribution_snapshot_key, grade, trading_style, outcome
        """,
        tuple(snapshot_keys),
    )
    raw: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for row in rows:
        snapshot_key = str(row.get("distribution_snapshot_key") or "")
        outcome = _normalize_profile_outcome(row.get("outcome"))
        if outcome not in {"up", "down"}:
            continue
        grade = str(row.get("grade") or "unknown")
        style = str(row.get("trading_style") or "unknown")
        _accumulate_profile_component(raw.setdefault(snapshot_key, {}).setdefault("by_grade", {}).setdefault(grade, {}), outcome, row)
        _accumulate_profile_component(raw.setdefault(snapshot_key, {}).setdefault("by_style", {}).setdefault(style, {}), outcome, row)
        _accumulate_profile_component(raw.setdefault(snapshot_key, {}).setdefault("by_grade_style", {}).setdefault(f"{grade} / {style}", {}), outcome, row)
    return {snapshot_key: _finalize_profile_distribution_breakdown(groups) for snapshot_key, groups in raw.items()}


def _normalize_profile_outcome(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("up") or text in {"yes", "long"}:
        return "up"
    if text.startswith("down") or text in {"no", "short"}:
        return "down"
    return "unknown"


def _accumulate_profile_component(target: dict[str, Any], outcome: str, row: dict[str, Any]) -> None:
    bucket = target.setdefault(
        outcome,
        {
            "component_count": 0,
            "profile_count": 0,
            "weighted_shares": 0.0,
            "weighted_cost": 0.0,
            "weighted_notional": 0.0,
            "contribution_weight": 0.0,
        },
    )
    bucket["component_count"] += int(row.get("component_count") or 0)
    bucket["profile_count"] += int(row.get("profile_count") or 0)
    bucket["weighted_shares"] += _safe_float(row.get("weighted_shares"))
    bucket["weighted_cost"] += _safe_float(row.get("weighted_cost"))
    bucket["weighted_notional"] += _safe_float(row.get("weighted_notional"))
    bucket["contribution_weight"] += _safe_float(row.get("contribution_weight"))


def _finalize_profile_distribution_breakdown(groups: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    finalized: dict[str, Any] = {}
    for group_name, buckets in groups.items():
        rows: list[dict[str, Any]] = []
        for label, sides in buckets.items():
            row = _profile_breakdown_row(label, sides)
            rows.append(row)
        rows.sort(key=lambda item: item.get("total_weighted_cost") or 0.0, reverse=True)
        finalized[group_name] = rows[:12]
    return finalized | {"schema_version": "crypto_options_profile_distribution_component_breakdown_v1"}


def _profile_breakdown_row(label: str, sides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    up = sides.get("up", {})
    down = sides.get("down", {})
    up_cost = _safe_float(up.get("weighted_cost"))
    down_cost = _safe_float(down.get("weighted_cost"))
    up_shares = _safe_float(up.get("weighted_shares"))
    down_shares = _safe_float(down.get("weighted_shares"))
    total_cost = up_cost + down_cost
    up_ratio = up_cost / total_cost if total_cost > 0 else None
    down_ratio = down_cost / total_cost if total_cost > 0 else None
    up_price = _weighted_profile_price(up_cost, up_shares)
    down_price = _weighted_profile_price(down_cost, down_shares)
    pair_sum = None if up_price is None or down_price is None else up_price + down_price
    return {
        "label": label,
        "component_count": int(up.get("component_count") or 0) + int(down.get("component_count") or 0),
        "profile_count": int(up.get("profile_count") or 0) + int(down.get("profile_count") or 0),
        "up_cost_weight": round(up_cost, 8),
        "down_cost_weight": round(down_cost, 8),
        "total_weighted_cost": round(total_cost, 8),
        "up_pressure_ratio": None if up_ratio is None else round(up_ratio, 8),
        "down_pressure_ratio": None if down_ratio is None else round(down_ratio, 8),
        "pressure_delta": None if up_ratio is None or down_ratio is None else round(up_ratio - down_ratio, 8),
        "up_reconstructed_profile_price": None if up_price is None else round(up_price, 8),
        "down_reconstructed_profile_price": None if down_price is None else round(down_price, 8),
        "reconstructed_profile_pair_sum": None if pair_sum is None else round(pair_sum, 8),
    }


def _empty_profile_distribution_breakdown() -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_profile_distribution_component_breakdown_v1",
        "by_grade": [],
        "by_style": [],
        "by_grade_style": [],
    }


def _crypto_indicator_rows(conn, *, limit: int = 120) -> dict[str, list[dict[str, Any]]]:
    technicals = _fetch_all(
        conn,
        """
        SELECT provider, symbol, interval, source_url, observed_at_utc, completed_at_utc,
               latency_ms, summary_label, summary_score, buy_count, sell_count,
               neutral_count, error_count, component_count
        FROM v_crypto_options_app_latest_external_technical_observers
        ORDER BY completed_at_utc DESC
        LIMIT ?
        """,
        (limit,),
    )
    prices = _fetch_all(
        conn,
        """
        SELECT symbol, source, observed_at_utc, exchange_timestamp_utc, price, bid, ask
        FROM v_crypto_options_app_latest_underlying_prices
        ORDER BY observed_at_utc DESC
        LIMIT 24
        """,
    )
    snapshots = _fetch_all(
        conn,
        """
        SELECT symbol, interval, indicator_id, computed_at_utc, direction, confidence,
               signal_value, quality_flags_json
        FROM indicator_snapshots
        ORDER BY computed_at_utc DESC
        LIMIT 80
        """,
    )
    for row in snapshots:
        row["quality_flags"] = _decode_json(row.pop("quality_flags_json", "{}"), fallback={})
    return {"technicals": technicals, "latest_prices": prices, "indicator_snapshots": snapshots}


def _module_rows(conn) -> list[dict[str, Any]]:
    rows = _fetch_all(
        conn,
        """
        SELECT data_block, module_id, symbol, generated_at_utc, status, freshness_seconds,
               blocker_json, metrics_json
        FROM v_crypto_options_app_latest_data_signal_readiness
        ORDER BY data_block, module_id, COALESCE(symbol, '')
        """,
    )
    for row in rows:
        row["blockers"] = _decode_json(row.pop("blocker_json", "[]"), fallback=[])
        row["metrics"] = _decode_json(row.pop("metrics_json", "{}"), fallback={})
    return rows


def _merge_source_module_rows(
    modules: list[dict[str, Any]],
    *,
    crypto: dict[str, list[dict[str, Any]]],
    profiles: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve readiness rows and synthesize A/B/C cards from visible data if needed."""

    merged = list(modules)
    present_blocks = {str(row.get("data_block") or "") for row in merged}
    generated_at = datetime.now(UTC).isoformat()
    if "A" not in present_blocks:
        latest_prices = crypto.get("latest_prices") or []
        technicals = crypto.get("technicals") or []
        snapshots = crypto.get("indicator_snapshots") or []
        total_rows = len(latest_prices) + len(technicals) + len(snapshots)
        if total_rows:
            blockers = _fallback_block_a_blockers(
                latest_prices=latest_prices,
                technicals=technicals,
                snapshots=snapshots,
            )
            stale_price_rows = _count_stale_rows(
                latest_prices,
                "observed_at_utc",
                "exchange_timestamp_utc",
                threshold_seconds=30.0,
            )
            stale_technical_rows = _count_stale_rows(
                technicals,
                "completed_at_utc",
                "observed_at_utc",
                threshold_seconds=30.0,
            )
            stale_snapshot_rows = _count_stale_rows(
                snapshots,
                "computed_at_utc",
                threshold_seconds=120.0,
            )
            merged.append(
                {
                    "data_block": "A",
                    "module_id": "crypto_indicator_rows",
                    "symbol": _summary_symbol([*latest_prices, *technicals, *snapshots]),
                    "generated_at_utc": generated_at,
                    "status": "ready" if not blockers else "degraded",
                    "freshness_seconds": None,
                    "blockers": blockers,
                    "metrics": {
                        "latest_price_rows": len(latest_prices),
                        "technical_observer_rows": len(technicals),
                        "indicator_snapshot_rows": len(snapshots),
                        "stale_latest_price_rows": stale_price_rows,
                        "stale_technical_observer_rows": stale_technical_rows,
                        "stale_indicator_snapshot_rows": stale_snapshot_rows,
                        "source": "control_center_payload_fallback",
                    },
                }
            )
    if "B" not in present_blocks and profiles:
        current_profiles = _current_profile_rows(profiles)
        actionable_profiles = _actionable_profile_rows(current_profiles)
        hard_blocker_count = sum(1 for row in actionable_profiles if row.get("blockers"))
        warning_count = sum(1 for row in actionable_profiles if row.get("coverage_warnings"))
        stale_count = sum(1 for row in actionable_profiles if _profile_row_is_stale(row))
        all_actionable_stale = bool(actionable_profiles) and stale_count >= len(actionable_profiles)
        blockers: list[str] = []
        if hard_blocker_count:
            blockers.append("profile_distribution_rows_have_blockers")
        if all_actionable_stale:
            blockers.append("profile_distribution_rows_stale")
        merged.append(
            {
                "data_block": "B",
                "module_id": "top_profiles_distribution_rows",
                "symbol": _summary_symbol(actionable_profiles or current_profiles),
                "generated_at_utc": generated_at,
                "status": "ready" if not blockers else "degraded",
                "freshness_seconds": None,
                "blockers": blockers,
                "metrics": {
                    "distribution_snapshot_rows": len(profiles),
                    "current_distribution_snapshot_rows": len(current_profiles),
                    "actionable_distribution_snapshot_rows": len(actionable_profiles),
                    "rows_with_blockers": hard_blocker_count,
                    "rows_stale": stale_count,
                    "rows_with_coverage_warnings": warning_count,
                    "source": "control_center_payload_fallback",
                },
            }
        )
    if "C" not in present_blocks and events:
        blockers = _fallback_block_c_blockers(events)
        stale_event_rows = _count_stale_rows(events, "last_snapshot_at_utc", "computed_at_utc", threshold_seconds=30.0)
        merged.append(
            {
                "data_block": "C",
                "module_id": "polymarket_event_path_rows",
                "symbol": _summary_symbol(events),
                "generated_at_utc": generated_at,
                "status": "ready" if not blockers else "degraded",
                "freshness_seconds": None,
                "blockers": blockers,
                "metrics": {
                    "event_path_rows": len(events),
                    "rows_stale": stale_event_rows,
                    "source": "control_center_payload_fallback",
                },
            }
        )
    return merged


def _actionable_profile_rows(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actionable = [
        row
        for row in profiles
        if str(row.get("phase") or "").lower() in {"pre", "live"}
    ]
    return actionable or profiles


def _current_profile_rows(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timestamps = [_parse_utc(row.get("computed_at_utc")) for row in profiles]
    latest = max((value for value in timestamps if value is not None), default=None)
    if latest is None:
        return profiles
    cutoff = latest.timestamp() - 120.0
    current: list[dict[str, Any]] = []
    for row, timestamp in zip(profiles, timestamps, strict=False):
        if timestamp is None or timestamp.timestamp() >= cutoff:
            current.append(row)
    return current or profiles


def _fallback_block_a_blockers(
    *,
    latest_prices: list[dict[str, Any]],
    technicals: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if not latest_prices:
        blockers.append("missing_underlying_price_rows")
    if not technicals:
        blockers.append("missing_technical_observer_rows")
    if not snapshots:
        blockers.append("missing_indicator_snapshot_rows")
    if _count_stale_rows(latest_prices, "observed_at_utc", "exchange_timestamp_utc", threshold_seconds=30.0):
        blockers.append("stale_underlying_price_rows")
    if _count_stale_rows(technicals, "completed_at_utc", "observed_at_utc", threshold_seconds=30.0):
        blockers.append("stale_technical_observer_rows")
    if _count_stale_rows(snapshots, "computed_at_utc", threshold_seconds=120.0):
        blockers.append("stale_indicator_snapshot_rows")
    return blockers


def _fallback_block_c_blockers(events: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    if any(int(row.get("snapshot_count") or 0) <= 0 for row in events):
        blockers.append("event_path_rows_missing_snapshot_count")
    if _count_stale_rows(events, "last_snapshot_at_utc", "computed_at_utc", threshold_seconds=30.0):
        blockers.append("stale_event_path_rows")
    return blockers


def _profile_row_is_stale(row: dict[str, Any]) -> bool:
    source_age = _coalesce_float(row.get("source_age_seconds"))
    refresh_target = _coalesce_float(row.get("target_refresh_seconds"), 30.0) or 30.0
    if source_age is not None:
        return source_age > refresh_target
    computed_at = _parse_utc(row.get("computed_at_utc"))
    if computed_at is None:
        return False
    return max(0.0, (datetime.now(UTC) - computed_at).total_seconds()) > refresh_target


def _rows_stale(rows: list[dict[str, Any]], *keys: str, threshold_seconds: float) -> bool:
    if not rows:
        return False
    latest = _latest_row_timestamp(rows, *keys)
    if latest is None:
        return True
    return max(0.0, (datetime.now(UTC) - latest).total_seconds()) > threshold_seconds


def _count_stale_rows(rows: list[dict[str, Any]], *keys: str, threshold_seconds: float) -> int:
    if not rows:
        return 0
    now = datetime.now(UTC)
    stale_count = 0
    for row in rows:
        latest = _latest_row_timestamp([row], *keys)
        if latest is None or max(0.0, (now - latest).total_seconds()) > threshold_seconds:
            stale_count += 1
    return stale_count


def _latest_row_timestamp(rows: list[dict[str, Any]], *keys: str) -> datetime | None:
    latest: datetime | None = None
    for row in rows:
        for key in keys:
            parsed = _parse_utc(row.get(key))
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
    return latest


def _summary_symbol(rows: list[dict[str, Any]]) -> str | None:
    symbols: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol.lower() == "unknown" or symbol in symbols:
            continue
        symbols.append(symbol)
        if len(symbols) >= 4:
            break
    if not symbols:
        return None
    return "/".join(symbols)


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _profile_distribution_summary_metrics(row: dict[str, Any]) -> dict[str, Any]:
    distribution = row.get("distribution") if isinstance(row.get("distribution"), dict) else {}
    nested_prices = distribution.get("reconstructed_profile_prices")
    prices = nested_prices if isinstance(nested_prices, dict) else {}
    up_price = _coalesce_float(
        prices.get("up"),
        _weighted_profile_price(row.get("up_cost_weight"), row.get("up_share_weight")),
    )
    down_price = _coalesce_float(
        prices.get("down"),
        _weighted_profile_price(row.get("down_cost_weight"), row.get("down_share_weight")),
    )
    up_weight = _safe_float(row.get("up_weight"))
    down_weight = _safe_float(row.get("down_weight"))
    total_weight = up_weight + down_weight
    up_ratio = up_weight / total_weight if total_weight > 0 else None
    down_ratio = down_weight / total_weight if total_weight > 0 else None
    pressure_delta = _coalesce_float(distribution.get("pressure_delta"))
    if pressure_delta is None and up_ratio is not None and down_ratio is not None:
        pressure_delta = up_ratio - down_ratio
    pair_sum = None if up_price is None or down_price is None else up_price + down_price
    return {
        "up_pressure_ratio": up_ratio,
        "down_pressure_ratio": down_ratio,
        "pressure_delta": None if pressure_delta is None else round(float(pressure_delta), 8),
        "pressure_delta_abs": None if pressure_delta is None else round(abs(float(pressure_delta)), 8),
        "pressure_interpretation": distribution.get("pressure_interpretation")
        or "hedged_profile_inventory_delta_not_directional_probability",
        "up_reconstructed_profile_price": None if up_price is None else round(float(up_price), 8),
        "down_reconstructed_profile_price": None if down_price is None else round(float(down_price), 8),
        "reconstructed_profile_price_delta": None if up_price is None or down_price is None else round(float(up_price) - float(down_price), 8),
        "reconstructed_profile_pair_sum": None if pair_sum is None else round(float(pair_sum), 8),
        "reconstructed_profile_price_method": prices.get("method")
        or "sum(weighted_cost_basis_usd) / sum(weighted_net_shares)",
        "source_age_seconds": _coalesce_float(distribution.get("source_age_seconds")),
        "target_refresh_seconds": _coalesce_float(distribution.get("target_refresh_seconds"), 30.0),
        "latest_source_at_utc": distribution.get("latest_source_at_utc"),
        "coverage_warnings": distribution.get("coverage_warnings") if isinstance(distribution.get("coverage_warnings"), list) else [],
        "distribution_variants": distribution.get("variants") if isinstance(distribution.get("variants"), dict) else {},
    }


def _weighted_profile_price(weighted_cost: Any, weighted_shares: Any) -> float | None:
    shares = _safe_float(weighted_shares)
    if shares <= 0:
        return None
    return _safe_float(weighted_cost) / shares


def _replay_candidate_cache_summary(conn, *, artifact_root: Path) -> dict[str, Any]:
    latest_scout = _latest_replay_candidate_scout_summary(artifact_root=artifact_root)
    selector_scouts = _latest_replay_candidate_selector_scout_summaries(artifact_root=artifact_root)
    if latest_scout is None and selector_scouts:
        latest_scout = max(
            selector_scouts.values(),
            key=lambda item: _parse_utc(item.get("latest_generated_at_utc")) or datetime.min.replace(tzinfo=UTC),
        )
    run_rows = _fetch_all(
        conn,
        """
        SELECT selector, strategy_signature, forward_mark_horizon_seconds,
               generated_at_utc, candidate_count, status
        FROM strategy_replay_candidate_cache_runs
        ORDER BY generated_at_utc DESC
        LIMIT 20
        """,
    )
    if not run_rows:
        return _merge_replay_candidate_cache_with_scout(
            {
                "status": "missing",
                "run_count": 0,
                "candidate_count": 0,
                "latest_generated_at_utc": None,
                "latest_age_seconds": None,
                "selectors": {},
                "blockers": ["replay_candidate_cache_empty"],
            },
            latest_scout,
            selector_scouts=selector_scouts,
        )
    latest_at = _latest_row_timestamp(run_rows, "generated_at_utc")
    latest_age = None if latest_at is None else max(0.0, (datetime.now(UTC) - latest_at).total_seconds())
    selectors: dict[str, dict[str, Any]] = {}
    total_candidates = 0
    for row in run_rows:
        selector = str(row.get("selector") or "unknown")
        candidate_count = int(_safe_float(row.get("candidate_count")))
        total_candidates += candidate_count
        bucket = selectors.setdefault(
            selector,
            {
                "run_count": 0,
                "candidate_count": 0,
                "latest_generated_at_utc": None,
                "latest_status": None,
            },
        )
        bucket["run_count"] += 1
        bucket["candidate_count"] += candidate_count
        if bucket["latest_generated_at_utc"] is None:
            bucket["latest_generated_at_utc"] = row.get("generated_at_utc")
            bucket["latest_status"] = row.get("status")
    blockers: list[str] = []
    if latest_age is None:
        blockers.append("replay_candidate_cache_timestamp_missing")
    elif latest_age > 900.0:
        blockers.append("replay_candidate_cache_stale")
    if total_candidates <= 0:
        blockers.append("replay_candidate_cache_has_no_candidates")
    return _merge_replay_candidate_cache_with_scout(
        {
            "status": "ready" if not blockers else "degraded",
            "run_count": len(run_rows),
            "candidate_count": total_candidates,
            "latest_generated_at_utc": None if latest_at is None else latest_at.isoformat(),
            "latest_age_seconds": latest_age,
            "selectors": selectors,
            "blockers": blockers,
        },
        latest_scout,
        selector_scouts=selector_scouts,
    )


def _latest_replay_candidate_scout_summary(*, artifact_root: Path) -> dict[str, Any] | None:
    report_path = artifact_root / "reports" / "strategy_replay_candidate_scout_latest.json"
    if not report_path.exists():
        return None
    return _replay_candidate_scout_summary_from_path(report_path)


def _latest_replay_candidate_selector_scout_summaries(*, artifact_root: Path) -> dict[str, dict[str, Any]]:
    reports_root = artifact_root / "reports"
    if not reports_root.exists():
        return {}
    summaries: dict[str, dict[str, Any]] = {}
    for report_path in sorted(reports_root.glob("strategy_replay_candidate_scout_*_latest.json")):
        if report_path.name in {
            "strategy_replay_candidate_scout_latest.json",
            "strategy_replay_candidate_scout_batch_latest.json",
        }:
            continue
        summary = _replay_candidate_scout_summary_from_path(report_path)
        selector = str(summary.get("scenario_selector") or "").strip()
        if selector:
            summaries[selector] = summary
    return summaries


def _replay_candidate_scout_summary_from_path(report_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - dashboard should degrade, not fail.
        return {
            "status": "degraded",
            "path": str(report_path),
            "candidate_count": 0,
            "latest_generated_at_utc": None,
            "latest_age_seconds": None,
            "scenario_selector": None,
            "blockers": [f"replay_candidate_scout_read_error:{type(exc).__name__}"],
            "scenarios": [],
        }
    generated_at = _parse_utc(payload.get("generated_at_utc"))
    latest_age = None if generated_at is None else max(0.0, (datetime.now(UTC) - generated_at).total_seconds())
    candidate_count = int(_safe_float(payload.get("scenario_count")))
    blockers = [str(item) for item in payload.get("blockers") or []]
    if generated_at is None:
        blockers.append("replay_candidate_scout_timestamp_missing")
    elif latest_age is not None and latest_age > 900.0:
        blockers.append("replay_candidate_scout_stale")
    if candidate_count <= 0:
        blockers.append("replay_candidate_scout_has_no_candidates")
    compact_scenarios: list[dict[str, Any]] = []
    for scenario in payload.get("scenarios") or []:
        if not isinstance(scenario, dict):
            continue
        compact_scenarios.append(
            {
                "event_key": scenario.get("event_key"),
                "event_slug": scenario.get("event_slug"),
                "event_token_key": scenario.get("event_token_key"),
                "outcome": scenario.get("outcome"),
                "source": scenario.get("source"),
                "limit_price": scenario.get("limit_price"),
                "forward_mark_price": scenario.get("forward_mark_price"),
                "time_remaining_seconds": scenario.get("time_remaining_seconds"),
            }
        )
        if len(compact_scenarios) >= 5:
            break
    return {
        "status": "ready" if not blockers else "degraded",
        "path": str(report_path),
        "schema_version": payload.get("schema_version"),
        "run_id": payload.get("run_id"),
        "scenario_selector": payload.get("scenario_selector"),
        "candidate_count": candidate_count,
        "distinct_event_count": int(_safe_float(payload.get("distinct_event_count"))),
        "distinct_event_token_count": int(_safe_float(payload.get("distinct_event_token_count"))),
        "latest_generated_at_utc": None if generated_at is None else generated_at.isoformat(),
        "latest_age_seconds": latest_age,
        "blockers": blockers,
        "scenarios": compact_scenarios,
        "orders_allowed": bool(payload.get("orders_allowed")),
        "live_trading_authorized": bool(payload.get("live_trading_authorized")),
        "manual_orders_avoided": bool(payload.get("manual_orders_avoided", True)),
    }


def _merge_replay_candidate_cache_with_scout(
    cache: dict[str, Any],
    latest_scout: dict[str, Any] | None,
    *,
    selector_scouts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    merged = dict(cache)
    selector_scouts = selector_scouts or {}
    merged["latest_scout"] = latest_scout
    merged["selector_scouts"] = selector_scouts
    if latest_scout is None and not selector_scouts:
        return merged
    scout_rows = list(selector_scouts.values())
    if latest_scout is not None:
        latest_selector = str(latest_scout.get("scenario_selector") or "").strip()
        if latest_selector not in selector_scouts:
            scout_rows.append(latest_scout)
    scout_candidate_count = sum(int(_safe_float(row.get("candidate_count"))) for row in scout_rows)
    selectors = dict(merged.get("selectors") or {})
    for scout in scout_rows:
        selector = str(scout.get("scenario_selector") or "").strip()
        if selector and selector not in selectors:
            selectors[selector] = {
                "run_count": 1,
                "candidate_count": int(_safe_float(scout.get("candidate_count"))),
                "latest_generated_at_utc": scout.get("latest_generated_at_utc"),
                "latest_status": scout.get("status"),
                "source": "latest_scout_artifact",
            }
    merged["selectors"] = selectors
    if int(_safe_float(merged.get("candidate_count"))) <= 0 and scout_candidate_count > 0:
        merged["candidate_count"] = scout_candidate_count
    if latest_scout is not None and not merged.get("latest_generated_at_utc") and latest_scout.get("latest_generated_at_utc"):
        merged["latest_generated_at_utc"] = latest_scout.get("latest_generated_at_utc")
        merged["latest_age_seconds"] = latest_scout.get("latest_age_seconds")
    blockers = [str(item) for item in merged.get("blockers") or []]
    if any(scout.get("status") == "ready" for scout in scout_rows):
        blockers = [
            blocker
            for blocker in blockers
            if blocker
            not in {
                "replay_candidate_cache_empty",
                "replay_candidate_cache_has_no_candidates",
                "replay_candidate_cache_stale",
                "replay_candidate_cache_timestamp_missing",
            }
        ]
    else:
        for scout in scout_rows:
            blockers.extend(str(item) for item in scout.get("blockers") or [])
    merged["blockers"] = sorted(set(blockers))
    merged["status"] = "ready" if not merged["blockers"] else "degraded"
    return merged


def _connect_dashboard_read_only(db_path: str | Path):
    """Open the runtime dashboard read connection through the central DB adapter."""

    conn = connect_read_only(db_path)
    if getattr(conn, "is_postgres", False):
        try:
            conn.execute("SET statement_timeout TO 3000")
            conn.execute("SET lock_timeout TO 1000")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    return conn


def _preflight_dashboard_read(conn) -> None:
    conn.execute("SELECT 1").fetchone()


def _base_control_center_state(
    *,
    db_path: Path,
    live_state: dict[str, Any],
    portfolio: dict[str, Any],
    positions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    crypto: dict[str, Any],
    modules: list[dict[str, Any]],
    replay_candidate_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module_blocks = _module_block_summaries(modules)
    return {
        "schema_version": "crypto_options_control_center_state_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "db_path": str(db_path),
        "portfolio": portfolio,
        "positions": positions,
        "orders": orders,
        "history": history,
        "events": events,
        "profile_distributions": profiles,
        "crypto_indicators": crypto,
        "replay_candidate_cache": replay_candidate_cache or _default_replay_candidate_cache(),
        "modules": modules,
        "module_blocks": module_blocks,
        "live_dashboard": {
            "active_run": live_state.get("active_run") or {},
            "audit": live_state.get("audit") or {},
            "totals": live_state.get("totals") or {},
            "review_request": live_state.get("review_request"),
        },
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
    }


def _empty_control_center_state(*, db_path: Path, live_state: dict[str, Any]) -> dict[str, Any]:
    return _base_control_center_state(
        db_path=db_path,
        live_state=live_state,
        portfolio={},
        positions=[],
        orders=[],
        history=[],
        events=[],
        profiles=[],
        crypto={"latest_prices": [], "technicals": [], "indicator_snapshots": []},
        modules=[],
        replay_candidate_cache=None,
    )


def _with_control_center_cache(result: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    cache_path = artifact_root / "reports" / "control_center_state_cache.json"
    has_live_rows = any(
        result.get(key)
        for key in ("positions", "orders", "history", "events", "profile_distributions", "modules", "replay_candidate_cache")
    )
    if has_live_rows:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_payload = result | {
                "cache_status": "fresh",
                "cached_at_utc": datetime.now(UTC).isoformat(),
            }
            cache_path.write_text(json.dumps(cache_payload, default=str), encoding="utf-8")
        except OSError:
            pass
        return result | {"cache_status": "fresh"}
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return result | {"cache_status": "empty"}
    cached["cache_status"] = "stale_fallback"
    cached["generated_at_utc"] = result.get("generated_at_utc")
    cached["live_dashboard"] = result.get("live_dashboard") or cached.get("live_dashboard") or {}
    cached["orders_allowed"] = False
    cached["live_trading_authorized"] = False
    cached["manual_orders_avoided"] = True
    cached["modules"] = _merge_source_module_rows(
        cached.get("modules") or [],
        crypto=cached.get("crypto_indicators") or {},
        profiles=cached.get("profile_distributions") or [],
        events=cached.get("events") or [],
    )
    cached["modules"] = _overlay_status_file_modules(cached["modules"], artifact_root=artifact_root)
    cached["module_blocks"] = _module_block_summaries(cached["modules"])
    cached.setdefault("replay_candidate_cache", _default_replay_candidate_cache("replay_candidate_cache_cached_payload_missing"))
    return cached


def _fresh_control_center_cache(
    *,
    artifact_root: Path,
    live_state: dict[str, Any],
    max_age_seconds: float,
) -> dict[str, Any] | None:
    cache_path = artifact_root / "reports" / "control_center_state_cache.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    cached_at = _parse_utc(cached.get("cached_at_utc"))
    if cached_at is None:
        cached_at = _parse_utc(cached.get("generated_at_utc"))
    if cached_at is None:
        return None
    cache_age_seconds = max(0.0, (datetime.now(UTC) - cached_at).total_seconds())
    if cache_age_seconds > max_age_seconds:
        return None
    cached["cache_status"] = "fresh_cache"
    cached["cache_age_seconds"] = cache_age_seconds
    cached["generated_at_utc"] = datetime.now(UTC).isoformat()
    cached["live_dashboard"] = {
        "active_run": live_state.get("active_run") or {},
        "audit": live_state.get("audit") or {},
        "totals": live_state.get("totals") or {},
        "review_request": live_state.get("review_request"),
    }
    cached["orders_allowed"] = False
    cached["live_trading_authorized"] = False
    cached["manual_orders_avoided"] = True
    cached["modules"] = _hydrate_cached_source_modules(cached)
    cached["modules"] = _overlay_status_file_modules(cached["modules"], artifact_root=artifact_root)
    cached["module_blocks"] = _module_block_summaries(cached["modules"])
    cached.setdefault("replay_candidate_cache", _default_replay_candidate_cache("replay_candidate_cache_cached_payload_missing"))
    cached["db_read_status"] = "fresh_cache"
    return cached


def _control_center_cache_fallback(
    *,
    artifact_root: Path,
    live_state: dict[str, Any],
) -> dict[str, Any] | None:
    cache_path = artifact_root / "reports" / "control_center_state_cache.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    cached_at = _parse_utc(cached.get("cached_at_utc"))
    if cached_at is None:
        cached_at = _parse_utc(cached.get("generated_at_utc"))
    cache_age_seconds = None
    if cached_at is not None:
        cache_age_seconds = max(0.0, (datetime.now(UTC) - cached_at).total_seconds())
    cached["cache_status"] = "expired_fallback"
    cached["cache_age_seconds"] = cache_age_seconds
    cached["generated_at_utc"] = datetime.now(UTC).isoformat()
    cached["live_dashboard"] = {
        "active_run": live_state.get("active_run") or {},
        "audit": live_state.get("audit") or {},
        "totals": live_state.get("totals") or {},
        "review_request": live_state.get("review_request"),
    }
    cached["orders_allowed"] = False
    cached["live_trading_authorized"] = False
    cached["manual_orders_avoided"] = True
    cached["modules"] = _hydrate_cached_source_modules(cached)
    cached["modules"] = _overlay_status_file_modules(
        cached["modules"],
        artifact_root=artifact_root,
        prefer_status_over_degraded=False,
    )
    cached["module_blocks"] = _module_block_summaries(cached["modules"])
    cached.setdefault("replay_candidate_cache", _default_replay_candidate_cache("replay_candidate_cache_cached_payload_missing"))
    shadow_state = _postgres_shadow_read_state(artifact_root=artifact_root)
    if shadow_state and shadow_state.get("ready"):
        cached["sqlite_read_status"] = "expired_fallback"
        cached["cache_status"] = "postgres_shadow_fallback"
        cached["db_read_status"] = "postgres_shadow_fallback"
        cached["postgres_shadow_read_available"] = True
    else:
        cached["db_read_status"] = "expired_fallback"
        if shadow_state:
            cached["postgres_shadow_read_available"] = False
            cached["postgres_shadow_parity_status"] = shadow_state.get("status")
            cached["postgres_shadow_runtime_cutover_allowed"] = shadow_state.get("runtime_read_cutover_allowed")
            cached["postgres_shadow_parity_completed_at_utc"] = shadow_state.get("completed_at_utc")
            cached["postgres_shadow_blockers"] = shadow_state.get("blockers") or []
            cached["postgres_shadow_lagging_tables"] = shadow_state.get("lagging_tables") or []
    blockers = cached.get("readiness_blockers")
    if not isinstance(blockers, list):
        blockers = []
    if cached.get("db_read_status") == "postgres_shadow_fallback":
        blockers = [blocker for blocker in blockers if blocker != "canonical_db_report_cache_expired"]
    elif "canonical_db_report_cache_expired" not in blockers:
        blockers.append("canonical_db_report_cache_expired")
    cached["readiness_blockers"] = blockers
    return cached


def _default_replay_candidate_cache(
    blocker: str = "replay_candidate_cache_not_checked",
) -> dict[str, Any]:
    return {
        "status": "missing",
        "run_count": 0,
        "candidate_count": 0,
        "latest_generated_at_utc": None,
        "latest_age_seconds": None,
        "selectors": {},
        "latest_scout": None,
        "selector_scouts": {},
        "blockers": [blocker],
    }


def _hydrate_cached_source_modules(cached: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild A/B/C readiness rows from cached visible payloads before overlays."""

    return _merge_source_module_rows(
        cached.get("modules") or [],
        crypto=cached.get("crypto_indicators") or {},
        profiles=cached.get("profile_distributions") or [],
        events=cached.get("events") or [],
    )


def _postgres_shadow_read_ready(*, artifact_root: Path) -> bool:
    state = _postgres_shadow_read_state(artifact_root=artifact_root)
    return bool(state and state.get("ready"))


def _postgres_shadow_read_state(*, artifact_root: Path) -> dict[str, Any] | None:
    report = load_postgres_shadow_parity_report(artifact_root=artifact_root)
    if not isinstance(report, dict):
        return None
    return {
        "ready": (
            report.get("status") == "ok"
            and report.get("runtime_read_cutover_allowed") is True
            and not report.get("blockers")
        ),
        "status": report.get("status"),
        "completed_at_utc": report.get("completed_at_utc"),
        "runtime_read_cutover_allowed": bool(report.get("runtime_read_cutover_allowed")),
        "blockers": list(report.get("blockers") or [])[:12],
        "lagging_tables": _compact_postgres_shadow_lagging_tables(report),
    }


def _compact_postgres_shadow_lagging_tables(report: dict[str, Any]) -> list[dict[str, Any]]:
    lagging: list[dict[str, Any]] = []
    for table in report.get("tables") or []:
        if table.get("status") == "ok":
            continue
        lagging.append(
            {
                "table": table.get("table"),
                "blockers": list(table.get("blockers") or [])[:6],
                "row_count_delta": table.get("row_count_delta"),
                "source_latest_timestamp": table.get("source_latest_timestamp"),
                "target_latest_timestamp": table.get("target_latest_timestamp"),
            }
        )
        if len(lagging) >= 6:
            break
    return lagging


def _overlay_status_file_modules(
    modules: list[dict[str, Any]],
    *,
    artifact_root: Path,
    prefer_status_over_degraded: bool = True,
) -> list[dict[str, Any]]:
    status_rows = _status_file_readiness_rows(artifact_root=artifact_root)
    if not status_rows:
        return modules
    merged = list(modules)
    existing_blocks = {str(row.get("data_block") or "") for row in modules}
    for row in status_rows:
        block = str(row.get("data_block") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        module_id = str(row.get("module_id") or block or "unknown")
        if block in existing_blocks:
            module_id = f"{module_id}_status_file"
        merged.append(
            {
                "data_block": block,
                "module_id": module_id,
                "symbol": row.get("symbol"),
                "generated_at_utc": row.get("generated_at_utc"),
                "status": row.get("status"),
                "freshness_seconds": row.get("source_age_seconds"),
                "blockers": row.get("blockers") or [],
                "metrics": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
                "source": "service_status_file",
                "prefer_over_degraded": prefer_status_over_degraded,
            }
        )
    return merged


def _module_block_summaries(modules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        block: _module_block_summary(block, modules)
        for block in ("A", "B", "C")
    }


def _module_block_summary(block: str, modules: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in modules if str(row.get("data_block") or "") == block]
    if not rows:
        return {
            "data_block": block,
            "status": "missing",
            "detail": "no readiness row",
            "module_count": 0,
            "blocker_count": 0,
            "blockers": [],
        }
    preferred_rows = _preferred_module_rows(rows)
    statuses = [str(row.get("status") or "unknown").lower() for row in preferred_rows]
    blockers = [str(item) for row in preferred_rows for item in (row.get("blockers") or [])]
    status = "ready"
    if any(value in {"failed", "unsafe"} for value in statuses):
        status = "failed"
    elif any(value in {"degraded", "stale", "missing", "unknown"} for value in statuses):
        status = "degraded"
    elif not all(value in {"healthy", "ready", "complete"} for value in statuses):
        status = "unknown"
    symbols = [
        str(row.get("symbol") or "")
        for row in preferred_rows
        if str(row.get("symbol") or "").strip() and str(row.get("symbol") or "").lower() != "unknown"
    ]
    symbols = symbols[:4]
    symbol_text = f" ({'/'.join(symbols)})" if symbols else ""
    blocker_text = f"; blockers: {', '.join(blockers[:3])}" if blockers else ""
    return {
        "data_block": block,
        "status": status,
        "detail": f"{len(rows)} module{'s' if len(rows) != 1 else ''}{symbol_text}{blocker_text}",
        "module_count": len(rows),
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


def _preferred_module_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    non_status_rows = [row for row in rows if str(row.get("source") or "") != "service_status_file"]
    non_status_has_hard_state = any(
        str(row.get("status") or "").lower() in {"degraded", "failed", "unsafe"}
        for row in non_status_rows
    )
    fresh_status_rows = [
        row
        for row in rows
        if str(row.get("source") or "") == "service_status_file"
        and _service_status_file_row_is_fresh(row)
    ]
    if (
        non_status_has_hard_state
        and not any(row.get("prefer_over_degraded") is not False for row in fresh_status_rows)
    ):
        return non_status_rows
    if fresh_status_rows:
        return fresh_status_rows
    return non_status_rows or rows


def _service_status_file_row_is_fresh(row: dict[str, Any]) -> bool:
    source_age = _coalesce_float(row.get("source_age_seconds"), row.get("freshness_seconds"))
    target_refresh = _coalesce_float(row.get("target_refresh_seconds"), 30.0) or 30.0
    if source_age is None:
        generated_at = _parse_utc(row.get("generated_at_utc"))
        if generated_at is None:
            return False
        source_age = max(0.0, (datetime.now(UTC) - generated_at).total_seconds())
    return source_age <= max(120.0, target_refresh * 4.0)


def _fetch_all(conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        if getattr(conn, "is_postgres", False):
            try:
                conn.rollback()
            except Exception:
                pass
        return []


def _fetch_one(conn, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = _fetch_all(conn, sql, params)
    return rows[0] if rows else {}


def _decode_json(value: Any, *, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _coalesce_float(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _safe_float(value: Any) -> float:
    resolved = _coalesce_float(value)
    return 0.0 if resolved is None else resolved


def _non_negative_latency_ms(value: Any) -> float | None:
    latency = _coalesce_float(value)
    if latency is None:
        return None
    return max(0.0, latency)
