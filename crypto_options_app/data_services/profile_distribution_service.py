from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from crypto_options_app.config import CENTRAL_ACTIVE_PROFILE_POOL, CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.workers.feed_worker import FeedWorkerConfig, write_watermark


LIVE_FLAG_NAMES = (
    "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE",
    "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED",
    "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK",
)

DATA_API_BASE_URL = "https://data-api.polymarket.com"
DEFAULT_ALLOWED_GRADES = ("S++", "S+", "S")
GRADE_WEIGHTS = {"S++": 1.4, "S+": 1.2, "S": 1.0, "A": 0.65}
STYLE_WEIGHTS = {
    "outcome_predictor": 1.25,
    "one_side_buy_hold": 1.25,
    "buy_and_hold": 1.25,
    "hedger": 1.0,
    "grid_buyer": 0.95,
    "scalping_trader": 0.35,
    "unknown": 0.5,
}


@dataclass(frozen=True)
class ProfileDistributionConfig:
    db_path: Path = CENTRAL_DB_PATH
    active_profile_pool_path: Path = CENTRAL_ACTIVE_PROFILE_POOL
    symbols: tuple[str, ...] = ("BTC", "ETH")
    allowed_grades: tuple[str, ...] = DEFAULT_ALLOWED_GRADES
    max_profiles: int = 120
    lookback_minutes: int = 5
    lookahead_minutes: int = 15
    target_refresh_seconds: int = 30
    max_source_age_seconds: int = 90
    live_signal_start_seconds: int = 30
    live_signal_end_seconds: int = 270
    canonical_method: str = "cost_weighted"
    module_id: str = "top_profiles_distribution"
    include_external_fetch: bool = False
    external_activity_limit: int = 100
    external_position_limit: int = 100
    max_concurrency: int = 8
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ProfileDistributionSummary:
    generated_at_utc: str
    status: str
    db_path: str
    event_count: int
    snapshot_rows_inserted: int
    component_rows_inserted: int
    readiness_rows_inserted: int
    blockers: tuple[str, ...] = ()
    distributions: tuple[dict[str, Any], ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False


ExternalProfileFetcher = Callable[[str, int, float], dict[str, Any] | list[Any]]


def capture_top_profile_distributions_once(
    *,
    config: ProfileDistributionConfig,
    now_utc: datetime | None = None,
    activity_fetcher: ExternalProfileFetcher | None = None,
    position_fetcher: ExternalProfileFetcher | None = None,
) -> ProfileDistributionSummary:
    _reject_live_env_flags()
    if not should_use_postgres_runtime(config.db_path):
        initialize_schema(config.db_path)
    now = _as_utc(now_utc or datetime.now(UTC))
    generated_at = now.isoformat()
    blockers: list[str] = []

    with connect(config.db_path) as conn:
        if not getattr(conn, "is_postgres", False):
            create_schema(conn)
        profiles = _load_eligible_profiles(conn, config=config)
        events = _load_relevant_events(conn, config=config, now_utc=now)
        if not profiles:
            blockers.append("no_eligible_top_profiles")
        if not events:
            blockers.append("no_relevant_crypto_events")
        if config.include_external_fetch and profiles:
            external_errors = _refresh_external_profile_rows(
                conn,
                profiles=profiles,
                config=config,
                activity_fetcher=activity_fetcher,
                position_fetcher=position_fetcher,
            )
            blockers.extend(external_errors)

        snapshots: list[dict[str, Any]] = []
        component_rows: list[dict[str, Any]] = []
        for event in events:
            snapshot, components = _build_distribution_snapshot(
                conn,
                event=event,
                profiles=profiles,
                config=config,
                computed_at_utc=now,
            )
            snapshots.append(snapshot)
            component_rows.extend(components)

        snapshot_inserted = 0
        component_inserted = 0
        for snapshot in snapshots:
            before = conn.total_changes
            _insert_distribution_snapshot(conn, snapshot)
            if conn.total_changes > before:
                snapshot_inserted += 1
        for component in component_rows:
            before = conn.total_changes
            _insert_distribution_component(conn, component)
            if conn.total_changes > before:
                component_inserted += 1
        readiness_rows = _write_block_b_readiness(
            conn,
            snapshots=snapshots,
            generated_at_utc=now,
            target_refresh_seconds=config.target_refresh_seconds,
            base_blockers=blockers,
        )
        snapshot_blockers = sorted(
            {str(blocker) for row in snapshots for blocker in _critical_snapshot_blockers(row)}
        )
        all_blockers = blockers + snapshot_blockers
        status = "failed" if not snapshots else "degraded" if all_blockers else "healthy"
        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id=config.module_id,
            status=status,
            last_run_at_utc=generated_at,
            rows_observed=len(events),
            rows_inserted=snapshot_inserted + component_inserted + readiness_rows,
            error_count=len(all_blockers),
            source="profile_distribution_hybrid",
            state={
                "symbols": list(config.symbols),
                "allowed_grades": list(config.allowed_grades),
                "target_refresh_seconds": config.target_refresh_seconds,
                "eligible_profile_count": len(profiles),
                "event_count": len(events),
                "snapshot_rows_inserted": snapshot_inserted,
                "component_rows_inserted": component_inserted,
                "readiness_rows_inserted": readiness_rows,
                "source_modes": sorted({str(row.get("source_mode")) for row in snapshots}),
                "blockers": all_blockers[:30],
                "orders_allowed": False,
                "live_trading_authorized": False,
            },
        )

    return ProfileDistributionSummary(
        generated_at_utc=generated_at,
        status=status,
        db_path=str(config.db_path),
        event_count=len(events),
        snapshot_rows_inserted=snapshot_inserted,
        component_rows_inserted=component_inserted,
        readiness_rows_inserted=readiness_rows,
        blockers=tuple(all_blockers),
        distributions=tuple(_public_snapshot(row) for row in snapshots),
        state={"eligible_profile_count": len(profiles), "source_modes": sorted({str(row.get("source_mode")) for row in snapshots})},
    )


def capture_top_profile_distributions_once_sync(
    *,
    config: ProfileDistributionConfig,
    now_utc: datetime | None = None,
    activity_fetcher: ExternalProfileFetcher | None = None,
    position_fetcher: ExternalProfileFetcher | None = None,
) -> ProfileDistributionSummary:
    return capture_top_profile_distributions_once(
        config=config,
        now_utc=now_utc,
        activity_fetcher=activity_fetcher,
        position_fetcher=position_fetcher,
    )


def feed_worker_config(config: ProfileDistributionConfig) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id=config.module_id,
        data_type="profile_distribution_signal",
        provider="polymarket_profiles",
        transport="hybrid",
        max_concurrency=config.max_concurrency,
        stale_after_seconds=config.target_refresh_seconds * 2,
        read_only=True,
        metadata={
            "writes": [
                "profile_distribution_snapshots",
                "profile_distribution_components",
                "data_signal_readiness_snapshots",
                "data_service_watermarks",
            ],
            "orders_allowed": False,
            "canonical_signal": "top_profiles_distribution",
        },
    )


def fetch_data_api_profile_activity(wallet_address: str, limit: int, timeout_seconds: float) -> dict[str, Any] | list[Any]:
    return _fetch_data_api("activity", wallet_address, limit, timeout_seconds)


def fetch_data_api_profile_positions(wallet_address: str, limit: int, timeout_seconds: float) -> dict[str, Any] | list[Any]:
    return _fetch_data_api("positions", wallet_address, limit, timeout_seconds)


def _refresh_external_profile_rows(
    conn: Any,
    *,
    profiles: list[dict[str, Any]],
    config: ProfileDistributionConfig,
    activity_fetcher: ExternalProfileFetcher | None,
    position_fetcher: ExternalProfileFetcher | None,
) -> list[str]:
    activity_fetcher = activity_fetcher or fetch_data_api_profile_activity
    position_fetcher = position_fetcher or fetch_data_api_profile_positions
    errors: list[str] = []
    tasks: list[tuple[dict[str, Any], str, ExternalProfileFetcher, int, str]] = []
    for profile in profiles[: max(0, config.max_profiles)]:
        wallet = profile.get("proxy_wallet") or profile.get("address")
        if not wallet:
            continue
        for source_name, fetcher, limit in (
            ("data_api_activity", activity_fetcher, config.external_activity_limit),
            ("data_api_positions", position_fetcher, config.external_position_limit),
        ):
            tasks.append((profile, str(wallet), fetcher, int(limit), source_name))
    if not tasks:
        return errors
    with ThreadPoolExecutor(max_workers=max(1, int(config.max_concurrency))) as executor:
        future_map = {
            executor.submit(fetcher, wallet, limit, float(config.timeout_seconds)): (profile, wallet, source_name)
            for profile, wallet, fetcher, limit, source_name in tasks
        }
        for future in as_completed(future_map):
            profile, wallet, source_name = future_map[future]
            try:
                payload = future.result()
            except Exception as exc:  # noqa: BLE001 - provider errors become readiness blockers.
                errors.append(f"{source_name}:{wallet}:{type(exc).__name__}:{exc}")
                continue
            if source_name == "data_api_activity":
                _persist_external_activity_rows(conn, profile=profile, payload=payload)
            else:
                _persist_external_position_rows(conn, profile=profile, payload=payload)
    return errors


def _build_distribution_snapshot(
    conn: Any,
    *,
    event: dict[str, Any],
    profiles: list[dict[str, Any]],
    config: ProfileDistributionConfig,
    computed_at_utc: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event_key = str(event.get("event_key") or "")
    event_slug = str(event.get("event_slug") or "")
    condition_id = str(event.get("condition_id") or "")
    profile_by_key = {str(profile["profile_key"]): profile for profile in profiles}
    profile_keys = tuple(profile_by_key)
    raw_rows = _select_profile_activity_rows(conn, profile_keys=profile_keys, event=event)
    order_rows = [] if raw_rows else _select_profile_order_rows(conn, profile_keys=profile_keys, event=event)
    position_rows = [] if raw_rows or order_rows else _select_profile_position_rows(conn, profile_keys=profile_keys, event=event)
    source_mode = _source_mode(raw_rows=raw_rows, order_rows=order_rows, position_rows=position_rows)
    rows = raw_rows or order_rows or position_rows
    contribution_map: dict[tuple[str, str], dict[str, Any]] = {}
    latest_source_at: datetime | None = None
    for row in rows:
        profile_key = str(row.get("profile_key") or "")
        profile = profile_by_key.get(profile_key)
        if profile is None:
            continue
        outcome = _normalize_outcome(row.get("outcome") or row.get("outcome_side"))
        if outcome not in {"Up", "Down"}:
            continue
        side = str(row.get("order_side") or "BUY").upper()
        row_source_mode = "position_snapshot_fallback" if row.get("_source") == "position" else source_mode
        shares = _float(row.get("shares"))
        notional = _float(row.get("notional_usd") or row.get("cost_basis_usd"))
        price = _float(row.get("price") or row.get("weighted_avg_price"))
        if notional <= 0 and shares > 0 and price > 0:
            notional = shares * price
        sign = -1.0 if side == "SELL" and row.get("_source") != "position" else 1.0
        key = (profile_key, outcome)
        acc = contribution_map.setdefault(
            key,
            {
                "profile": profile,
                "outcome": outcome,
                "source_modes": set(),
                "net_shares": 0.0,
                "net_notional_usd": 0.0,
                "cost_basis_usd": 0.0,
                "row_count": 0,
                "latest_source_at_utc": None,
            },
        )
        acc["source_modes"].add(row_source_mode)
        acc["net_shares"] += sign * shares
        acc["net_notional_usd"] += sign * notional
        acc["cost_basis_usd"] += max(0.0, notional) if row.get("_source") == "position" else max(0.0, sign * notional)
        acc["row_count"] += 1
        source_at = _parse_utc(row.get("activity_at_utc") or row.get("updated_at_utc") or row.get("inserted_at_utc"))
        if source_at and (latest_source_at is None or source_at > latest_source_at):
            latest_source_at = source_at
        if source_at and (acc["latest_source_at_utc"] is None or source_at.isoformat() > acc["latest_source_at_utc"]):
            acc["latest_source_at_utc"] = source_at.isoformat()

    components: list[dict[str, Any]] = []
    totals = {
        "Up": {"share": 0.0, "cost": 0.0, "count": 0.0},
        "Down": {"share": 0.0, "cost": 0.0, "count": 0.0},
    }
    for acc in contribution_map.values():
        if acc["net_shares"] <= 0 and acc["net_notional_usd"] <= 0 and acc["cost_basis_usd"] <= 0:
            continue
        profile = acc["profile"]
        grade_weight = GRADE_WEIGHTS.get(str(profile.get("grade") or ""), 0.0)
        style_weight = _style_weight(profile)
        final_weight = grade_weight * style_weight
        outcome = str(acc["outcome"])
        share_contribution = max(0.0, float(acc["net_shares"])) * final_weight
        cost_contribution = max(0.0, float(acc["cost_basis_usd"] or acc["net_notional_usd"])) * final_weight
        count_contribution = final_weight
        totals[outcome]["share"] += share_contribution
        totals[outcome]["cost"] += cost_contribution
        totals[outcome]["count"] += count_contribution
        component = {
            "distribution_component_key": _stable_key("profile_distribution_component", event_key, event_slug, profile["profile_key"], outcome, computed_at_utc.isoformat()),
            "profile_key": profile["profile_key"],
            "handle": profile.get("handle") or profile.get("profile_name"),
            "grade": profile.get("grade"),
            "trading_style": profile.get("trading_style") or profile.get("trading_style_detail"),
            "source_mode": "hybrid" if len(acc["source_modes"]) > 1 else next(iter(acc["source_modes"]), source_mode),
            "outcome": outcome,
            "net_shares": round(float(acc["net_shares"]), 8),
            "net_notional_usd": round(float(acc["net_notional_usd"]), 8),
            "cost_basis_usd": round(float(acc["cost_basis_usd"]), 8),
            "grade_weight": grade_weight,
            "style_weight": style_weight,
            "final_weight": final_weight,
            "contribution_json": {
                "share_contribution": share_contribution,
                "cost_contribution": cost_contribution,
                "count_contribution": count_contribution,
                "row_count": acc["row_count"],
                "latest_source_at_utc": acc["latest_source_at_utc"],
            },
        }
        components.append(component)

    distributions = {
        "shares_weighted": _ratio(totals["Up"]["share"], totals["Down"]["share"]),
        "cost_weighted": _ratio(totals["Up"]["cost"], totals["Down"]["cost"]),
        "profile_count_weighted": _ratio(totals["Up"]["count"], totals["Down"]["count"]),
    }
    reconstructed_prices = _reconstructed_weighted_prices(totals)
    canonical_method = _canonical_method(config.canonical_method, distributions)
    canonical = distributions[canonical_method]
    pressure_delta = round(float(canonical["up"]) - float(canonical["down"]), 8)
    phase, phase_detail = _event_phase(event, computed_at_utc, config=config)
    blockers: list[str] = []
    coverage_warnings: list[str] = []
    blocker_target = blockers if phase == "live" and phase_detail.get("live_window_status") == "actionable" else coverage_warnings
    if not components:
        blocker_target.append("no_profile_distribution_components")
    if canonical["total_weight"] <= 0:
        blocker_target.append("no_up_down_distribution_weight")
    source_age = None
    if latest_source_at is not None:
        source_age = max(0.0, (computed_at_utc - latest_source_at).total_seconds())
        if source_age > config.max_source_age_seconds:
            blocker_target.append("profile_distribution_source_stale")
    elif components:
        blocker_target.append("profile_distribution_missing_source_timestamp")
    snapshot_key = _stable_key("profile_distribution_snapshot", event_key, event_slug, computed_at_utc.isoformat())
    snapshot = {
        "distribution_snapshot_key": snapshot_key,
        "event_key": event.get("event_key"),
        "event_slug": event.get("event_slug"),
        "symbol": event.get("symbol"),
        "phase": phase,
        "computed_at_utc": computed_at_utc.isoformat(),
        "event_start_time_utc": event.get("event_start_time_utc"),
        "event_end_time_utc": event.get("event_end_time_utc"),
        "source_mode": source_mode,
        "canonical_method": canonical_method,
        "profile_count": len({component["profile_key"] for component in components}),
        "component_count": len(components),
        "up_weight": canonical["up_weight"],
        "down_weight": canonical["down_weight"],
        "up_share_weight": totals["Up"]["share"],
        "down_share_weight": totals["Down"]["share"],
        "up_cost_weight": totals["Up"]["cost"],
        "down_cost_weight": totals["Down"]["cost"],
        "up_count_weight": totals["Up"]["count"],
        "down_count_weight": totals["Down"]["count"],
        "distribution_json": {
            "top_profiles_distribution": {"up": canonical["up"], "down": canonical["down"]},
            "pressure_delta": pressure_delta,
            "pressure_delta_abs": round(abs(pressure_delta), 8),
            "pressure_interpretation": "hedged_profile_inventory_delta_not_directional_probability",
            "reconstructed_profile_prices": reconstructed_prices,
            "canonical_method": canonical_method,
            "variants": distributions,
            "phase_detail": phase_detail,
            "latest_source_at_utc": latest_source_at.isoformat() if latest_source_at else None,
            "source_age_seconds": source_age,
            "allowed_grades": list(config.allowed_grades),
            "coverage_warnings": sorted(set(coverage_warnings)),
        },
        "source_json": {
            "event_key": event_key,
            "event_slug": event_slug,
            "condition_id": condition_id,
            "raw_activity_rows": len(raw_rows),
            "event_order_rows": len(order_rows),
            "position_rows": len(position_rows),
            "computed_by": "profile_distribution_service",
        },
        "blockers": blockers,
        "inserted_at_utc": computed_at_utc.isoformat(),
    }
    for component in components:
        component["distribution_snapshot_key"] = snapshot_key
        component["inserted_at_utc"] = computed_at_utc.isoformat()
    return snapshot, components


def _load_eligible_profiles(conn: Any, *, config: ProfileDistributionConfig) -> list[dict[str, Any]]:
    allowed = tuple(dict.fromkeys(str(grade).upper() for grade in config.allowed_grades))
    if not allowed:
        return []
    pool = _read_active_profile_pool(config.active_profile_pool_path)
    placeholders = ",".join("?" for _ in allowed)
    rows = conn.execute(
        f"""
        SELECT p.profile_key, p.normalized_ref, p.handle, p.proxy_wallet, p.profile_name,
               COALESCE(p.proxy_wallet, r.address) AS address,
               g.grade, g.score, g.trading_style, g.trading_style_detail,
               g.evaluated_at_utc, p.latest_activity_utc
        FROM profile_grades g
        JOIN profiles p ON p.profile_key = g.profile_key
        LEFT JOIN profile_refs r ON r.profile_key = p.profile_key
        WHERE UPPER(g.grade) IN ({placeholders})
        ORDER BY
            CASE UPPER(g.grade)
                WHEN 'S++' THEN 0
                WHEN 'S+' THEN 1
                WHEN 'S' THEN 2
                WHEN 'A' THEN 3
                ELSE 4
            END,
            COALESCE(g.score, 0) DESC
        """,
        allowed,
    ).fetchall()
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        item = dict(row)
        refs = {
            str(value).strip().lower()
            for value in (
                item.get("profile_key"),
                item.get("normalized_ref"),
                item.get("handle"),
                item.get("proxy_wallet"),
                item.get("address"),
                item.get("profile_name"),
            )
            if value
        }
        if pool and not refs.intersection(pool):
            continue
        profile_key = str(item["profile_key"])
        if profile_key in seen:
            continue
        seen.add(profile_key)
        profiles.append(item)
        if len(profiles) >= config.max_profiles:
            break
    if not profiles and pool:
        return _load_eligible_profiles(conn, config=ProfileDistributionConfig(
            db_path=config.db_path,
            active_profile_pool_path=Path("__missing_pool__"),
            symbols=config.symbols,
            allowed_grades=config.allowed_grades,
            max_profiles=config.max_profiles,
            lookback_minutes=config.lookback_minutes,
            lookahead_minutes=config.lookahead_minutes,
            target_refresh_seconds=config.target_refresh_seconds,
            max_source_age_seconds=config.max_source_age_seconds,
            live_signal_start_seconds=config.live_signal_start_seconds,
            live_signal_end_seconds=config.live_signal_end_seconds,
            canonical_method=config.canonical_method,
            module_id=config.module_id,
            include_external_fetch=config.include_external_fetch,
            external_activity_limit=config.external_activity_limit,
            external_position_limit=config.external_position_limit,
            max_concurrency=config.max_concurrency,
            timeout_seconds=config.timeout_seconds,
        ))
    return profiles


def _load_relevant_events(conn: Any, *, config: ProfileDistributionConfig, now_utc: datetime) -> list[dict[str, Any]]:
    lower = (now_utc - timedelta(minutes=max(0, config.lookback_minutes))).isoformat()
    upper = (now_utc + timedelta(minutes=max(0, config.lookahead_minutes))).isoformat()
    symbols = tuple(dict.fromkeys(symbol.upper() for symbol in config.symbols))
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"""
        SELECT event_key, event_slug, condition_id, market_id, market_slug, symbol,
               event_start_time_utc, event_end_time_utc
        FROM events
        WHERE UPPER(symbol) IN ({placeholders})
          AND (event_end_time_utc IS NULL OR event_end_time_utc >= ?)
          AND (event_start_time_utc IS NULL OR event_start_time_utc <= ?)
        ORDER BY event_start_time_utc, symbol
        LIMIT 24
        """,
        (*symbols, lower, upper),
    ).fetchall()
    return [dict(row) for row in rows]


def _select_profile_activity_rows(conn: Any, *, profile_keys: tuple[str, ...], event: dict[str, Any]) -> list[dict[str, Any]]:
    if not profile_keys:
        return []
    profile_placeholders = ",".join("?" for _ in profile_keys)
    event_refs = [event.get("event_key"), event.get("event_slug"), event.get("condition_id"), event.get("market_slug")]
    event_refs = [str(ref) for ref in event_refs if ref]
    if not event_refs:
        return []
    event_placeholders = ",".join("?" for _ in event_refs)
    rows = conn.execute(
        f"""
        SELECT profile_key, event_key, event_slug, condition_id, market_slug, order_side,
               outcome_side AS outcome, price, shares, notional_usd, activity_at_utc,
               observed_at_utc, inserted_at_utc, source_table, source_json,
               'activity' AS _source
        FROM profile_raw_activity
        WHERE profile_key IN ({profile_placeholders})
          AND (
              event_key IN ({event_placeholders})
              OR event_slug IN ({event_placeholders})
              OR condition_id IN ({event_placeholders})
              OR market_slug IN ({event_placeholders})
          )
        """,
        (*profile_keys, *event_refs, *event_refs, *event_refs, *event_refs),
    ).fetchall()
    return [dict(row) for row in rows]


def _select_profile_order_rows(conn: Any, *, profile_keys: tuple[str, ...], event: dict[str, Any]) -> list[dict[str, Any]]:
    if not profile_keys:
        return []
    profile_placeholders = ",".join("?" for _ in profile_keys)
    event_refs = [event.get("event_key"), event.get("condition_id")]
    event_refs = [str(ref) for ref in event_refs if ref]
    if not event_refs:
        return []
    event_placeholders = ",".join("?" for _ in event_refs)
    rows = conn.execute(
        f"""
        SELECT profile_key, event_key, event_token_key, order_side, outcome,
               price, shares, notional_usd, activity_at_utc, inserted_at_utc,
               source_json, 'event_order' AS _source
        FROM profile_event_orders
        WHERE profile_key IN ({profile_placeholders})
          AND event_key IN ({event_placeholders})
        """,
        (*profile_keys, *event_refs),
    ).fetchall()
    return [dict(row) for row in rows]


def _select_profile_position_rows(conn: Any, *, profile_keys: tuple[str, ...], event: dict[str, Any]) -> list[dict[str, Any]]:
    if not profile_keys:
        return []
    profile_placeholders = ",".join("?" for _ in profile_keys)
    event_refs = [event.get("event_key"), event.get("condition_id")]
    event_refs = [str(ref) for ref in event_refs if ref]
    if not event_refs:
        return []
    event_placeholders = ",".join("?" for _ in event_refs)
    rows = conn.execute(
        f"""
        SELECT profile_key, event_key, event_token_key, outcome,
               'BUY' AS order_side, weighted_avg_price AS price, shares,
               cost_basis_usd, cost_basis_usd AS notional_usd,
               updated_at_utc, inserted_at_utc, source_json, 'position' AS _source
        FROM profile_event_positions
        WHERE profile_key IN ({profile_placeholders})
          AND event_key IN ({event_placeholders})
        """,
        (*profile_keys, *event_refs),
    ).fetchall()
    return [dict(row) for row in rows]


def _insert_distribution_snapshot(conn: Any, snapshot: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO profile_distribution_snapshots(
            distribution_snapshot_key, event_key, event_slug, symbol, phase,
            computed_at_utc, event_start_time_utc, event_end_time_utc,
            source_mode, canonical_method, profile_count, component_count,
            up_weight, down_weight, up_share_weight, down_share_weight,
            up_cost_weight, down_cost_weight, up_count_weight, down_count_weight,
            distribution_json, source_json, blockers_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(distribution_snapshot_key) DO UPDATE SET
            source_mode=excluded.source_mode,
            canonical_method=excluded.canonical_method,
            profile_count=excluded.profile_count,
            component_count=excluded.component_count,
            up_weight=excluded.up_weight,
            down_weight=excluded.down_weight,
            up_share_weight=excluded.up_share_weight,
            down_share_weight=excluded.down_share_weight,
            up_cost_weight=excluded.up_cost_weight,
            down_cost_weight=excluded.down_cost_weight,
            up_count_weight=excluded.up_count_weight,
            down_count_weight=excluded.down_count_weight,
            distribution_json=excluded.distribution_json,
            source_json=excluded.source_json,
            blockers_json=excluded.blockers_json
        """,
        (
            snapshot["distribution_snapshot_key"],
            snapshot.get("event_key"),
            snapshot.get("event_slug"),
            snapshot.get("symbol"),
            snapshot["phase"],
            snapshot["computed_at_utc"],
            snapshot.get("event_start_time_utc"),
            snapshot.get("event_end_time_utc"),
            snapshot["source_mode"],
            snapshot["canonical_method"],
            int(snapshot["profile_count"]),
            int(snapshot["component_count"]),
            float(snapshot["up_weight"]),
            float(snapshot["down_weight"]),
            float(snapshot["up_share_weight"]),
            float(snapshot["down_share_weight"]),
            float(snapshot["up_cost_weight"]),
            float(snapshot["down_cost_weight"]),
            float(snapshot["up_count_weight"]),
            float(snapshot["down_count_weight"]),
            json.dumps(snapshot["distribution_json"], sort_keys=True, default=str),
            json.dumps(snapshot["source_json"], sort_keys=True, default=str),
            json.dumps(snapshot["blockers"], sort_keys=True, default=str),
            snapshot["inserted_at_utc"],
        ),
    )


def _insert_distribution_component(conn: Any, component: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO profile_distribution_components(
            distribution_component_key, distribution_snapshot_key, profile_key,
            handle, grade, trading_style, source_mode, outcome, net_shares,
            net_notional_usd, cost_basis_usd, grade_weight, style_weight,
            final_weight, contribution_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(distribution_component_key) DO UPDATE SET
            source_mode=excluded.source_mode,
            net_shares=excluded.net_shares,
            net_notional_usd=excluded.net_notional_usd,
            cost_basis_usd=excluded.cost_basis_usd,
            grade_weight=excluded.grade_weight,
            style_weight=excluded.style_weight,
            final_weight=excluded.final_weight,
            contribution_json=excluded.contribution_json
        """,
        (
            component["distribution_component_key"],
            component["distribution_snapshot_key"],
            component["profile_key"],
            component.get("handle"),
            component.get("grade"),
            component.get("trading_style"),
            component["source_mode"],
            component["outcome"],
            float(component["net_shares"]),
            float(component["net_notional_usd"]),
            float(component["cost_basis_usd"]),
            float(component["grade_weight"]),
            float(component["style_weight"]),
            float(component["final_weight"]),
            json.dumps(component["contribution_json"], sort_keys=True, default=str),
            component["inserted_at_utc"],
        ),
    )


def _write_block_b_readiness(
    conn: Any,
    *,
    snapshots: list[dict[str, Any]],
    generated_at_utc: datetime,
    target_refresh_seconds: int,
    base_blockers: list[str],
) -> int:
    inserted = 0
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        by_symbol.setdefault(str(snapshot.get("symbol") or "unknown"), []).append(snapshot)
    if not by_symbol:
        by_symbol["unknown"] = []
    for symbol, rows in by_symbol.items():
        ready_rows = [
            row
            for row in rows
            if int(row.get("component_count") or 0) > 0 and not _critical_snapshot_blockers(row)
        ]
        latest_source_at = _latest_snapshot_source_at(rows)
        source_age = None
        if latest_source_at is not None:
            source_age = max(0.0, (generated_at_utc - latest_source_at).total_seconds())
        blockers = list(base_blockers)
        blockers.extend(sorted({str(blocker) for row in rows for blocker in _critical_snapshot_blockers(row)}))
        coverage_warnings = sorted(
            {
                str(blocker)
                for row in rows
                for blocker in (
                    list(row.get("blockers", []))
                    + list((row.get("distribution_json") or {}).get("coverage_warnings", []))
                )
            }
        )
        if not ready_rows and any(_snapshot_requires_ready_distribution(row) for row in rows):
            blockers.append("no_ready_profile_distribution")
        status = "ready" if not blockers else "degraded" if rows else "missing"
        payload = {
            "snapshot_count": len(rows),
            "ready_snapshot_count": len(ready_rows),
            "actionable_snapshot_count": len([row for row in rows if _snapshot_requires_ready_distribution(row)]),
            "coverage_warnings": coverage_warnings,
            "top_profiles_distribution": (ready_rows[-1]["distribution_json"].get("top_profiles_distribution") if ready_rows else None),
            "pressure_delta": (ready_rows[-1]["distribution_json"].get("pressure_delta") if ready_rows else None),
            "reconstructed_profile_prices": (
                ready_rows[-1]["distribution_json"].get("reconstructed_profile_prices") if ready_rows else None
            ),
            "source_modes": sorted({str(row.get("source_mode")) for row in rows}),
            "profile_counts": [int(row.get("profile_count") or 0) for row in rows],
            "component_counts": [int(row.get("component_count") or 0) for row in rows],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
        conn.execute(
            """
            INSERT INTO data_signal_readiness_snapshots(
                readiness_key, data_block, module_id, symbol, generated_at_utc,
                target_refresh_seconds, status, latest_source_at_utc,
                source_age_seconds, payload_json, blockers_json, inserted_at_utc
            )
            VALUES (?, 'B', 'top_profiles_distribution', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _stable_key("block_b_readiness", symbol, generated_at_utc.isoformat()),
                symbol,
                generated_at_utc.isoformat(),
                int(target_refresh_seconds),
                status,
                latest_source_at.isoformat() if latest_source_at else None,
                source_age,
                json.dumps(payload, sort_keys=True, default=str),
                json.dumps(sorted(set(blockers)), sort_keys=True, default=str),
                generated_at_utc.isoformat(),
            ),
        )
        inserted += 1
    return inserted


def _snapshot_requires_ready_distribution(snapshot: dict[str, Any]) -> bool:
    phase = str(snapshot.get("phase") or "")
    phase_detail = snapshot.get("distribution_json", {}).get("phase_detail", {})
    live_window_status = str(phase_detail.get("live_window_status") or "")
    return phase == "live" and live_window_status == "actionable"


def _critical_snapshot_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers = [str(blocker) for blocker in snapshot.get("blockers", [])]
    if not blockers:
        return []
    if _snapshot_requires_ready_distribution(snapshot):
        return blockers
    return []


def _persist_external_activity_rows(conn: Any, *, profile: dict[str, Any], payload: dict[str, Any] | list[Any]) -> None:
    for raw in _iter_payload_rows(payload):
        token_id = _first(raw, "asset", "token_id", "tokenId", "asset_id")
        event_slug = _first(raw, "eventSlug", "event_slug", "slug")
        condition_id = _first(raw, "conditionId", "condition_id")
        outcome = _first(raw, "outcome", "outcome_side")
        price = _float(_first(raw, "price", "avgPrice"))
        shares = _float(_first(raw, "size", "shares", "quantity"))
        side = str(_first(raw, "side", "order_side") or "BUY").upper()
        timestamp = _timestamp_to_iso(_first(raw, "timestamp", "createdAt", "created_at"))
        key = _stable_key("external_profile_activity", profile["profile_key"], event_slug, token_id, outcome, side, price, shares, timestamp)
        conn.execute(
            """
            INSERT INTO profile_raw_activity(
                raw_activity_key, profile_key, event_key, event_slug, condition_id,
                market_slug, symbol, order_side, outcome_side, token_id, price,
                shares, notional_usd, activity_at_utc, observed_at_utc,
                source_table, source_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_activity_key) DO NOTHING
            """,
            (
                key,
                profile["profile_key"],
                condition_id,
                event_slug,
                condition_id,
                _first(raw, "slug", "marketSlug", "market_slug"),
                _symbol_from_slug(str(event_slug or "")),
                side,
                outcome,
                token_id,
                price,
                shares,
                price * shares if price and shares else _float(_first(raw, "notional", "notional_usd")),
                timestamp,
                datetime.now(UTC).isoformat(),
                "polymarket_data_api_activity",
                json.dumps(raw, sort_keys=True, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )


def _persist_external_position_rows(conn: Any, *, profile: dict[str, Any], payload: dict[str, Any] | list[Any]) -> None:
    for raw in _iter_payload_rows(payload):
        token_id = _first(raw, "asset", "token_id", "tokenId", "asset_id")
        event_slug = _first(raw, "eventSlug", "event_slug", "slug")
        condition_id = _first(raw, "conditionId", "condition_id")
        outcome = _first(raw, "outcome", "outcome_side")
        shares = _float(_first(raw, "size", "shares", "quantity", "currentShares"))
        avg_price = _float(_first(raw, "avgPrice", "averagePrice", "price", "weighted_avg_price"))
        cost_basis = _float(_first(raw, "costBasis", "cost_basis", "cost_basis_usd"))
        if cost_basis <= 0 and shares > 0 and avg_price > 0:
            cost_basis = shares * avg_price
        if not event_slug and not condition_id:
            continue
        event_key = str(condition_id or event_slug)
        key = _stable_key("external_profile_position", profile["profile_key"], event_key, token_id, outcome)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO profile_event_positions(
                profile_event_position_key, profile_key, event_key, event_token_key,
                outcome, shares, cost_basis_usd, weighted_avg_price,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_event_position_key) DO UPDATE SET
                shares=excluded.shares,
                cost_basis_usd=excluded.cost_basis_usd,
                weighted_avg_price=excluded.weighted_avg_price,
                source_json=excluded.source_json,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                key,
                profile["profile_key"],
                event_key,
                token_id,
                outcome,
                shares,
                cost_basis,
                avg_price,
                json.dumps(raw, sort_keys=True, default=str),
                now,
                now,
            ),
        )


def _fetch_data_api(endpoint: str, wallet_address: str, limit: int, timeout_seconds: float) -> dict[str, Any] | list[Any]:
    params = urllib.parse.urlencode({"user": wallet_address, "limit": max(1, int(limit)), "offset": 0})
    url = f"{DATA_API_BASE_URL.rstrip('/')}/{endpoint}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "crypto-options-app-readonly-profile-service/0.1"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - official Polymarket data host.
        return json.loads(response.read().decode("utf-8", "replace"))


def _read_active_profile_pool(path: Path) -> set[str]:
    if not path.exists():
        return set()
    refs: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value:
            refs.add(value.lower())
    return refs


def _source_mode(*, raw_rows: list[dict[str, Any]], order_rows: list[dict[str, Any]], position_rows: list[dict[str, Any]]) -> str:
    modes: set[str] = set()
    if raw_rows:
        source_tables = {str(row.get("source_table") or "").lower() for row in raw_rows}
        if any("websocket" in source or "real_time" in source for source in source_tables):
            modes.add("stream_orders")
        else:
            modes.add("batch_activity")
    if order_rows:
        modes.add("event_order_reconstruction")
    if position_rows:
        modes.add("position_snapshot_fallback")
    if not modes:
        return "no_profile_source"
    return "hybrid" if len(modes) > 1 else next(iter(modes))


def _canonical_method(requested: str, distributions: dict[str, dict[str, float]]) -> str:
    requested = requested if requested in distributions else "cost_weighted"
    if distributions[requested]["total_weight"] > 0:
        return requested
    for fallback in ("cost_weighted", "shares_weighted", "profile_count_weighted"):
        if distributions[fallback]["total_weight"] > 0:
            return fallback
    return requested


def _ratio(up_weight: float, down_weight: float) -> dict[str, float]:
    total = max(0.0, float(up_weight)) + max(0.0, float(down_weight))
    if total <= 0:
        return {"up": 0.0, "down": 0.0, "up_weight": 0.0, "down_weight": 0.0, "total_weight": 0.0}
    return {
        "up": round(max(0.0, float(up_weight)) / total, 8),
        "down": round(max(0.0, float(down_weight)) / total, 8),
        "up_weight": round(max(0.0, float(up_weight)), 8),
        "down_weight": round(max(0.0, float(down_weight)), 8),
        "total_weight": round(total, 8),
    }


def _reconstructed_weighted_prices(totals: dict[str, dict[str, float]]) -> dict[str, Any]:
    up_price = _weighted_price(totals["Up"]["cost"], totals["Up"]["share"])
    down_price = _weighted_price(totals["Down"]["cost"], totals["Down"]["share"])
    pair_sum = None if up_price is None or down_price is None else round(up_price + down_price, 8)
    return {
        "method": "sum(weighted_cost_basis_usd) / sum(weighted_net_shares)",
        "up": up_price,
        "down": down_price,
        "up_down_price_delta": None if up_price is None or down_price is None else round(up_price - down_price, 8),
        "pair_sum": pair_sum,
        "pair_sum_deviation_from_one": None if pair_sum is None else round(pair_sum - 1.0, 8),
        "up_weighted_cost": round(max(0.0, float(totals["Up"]["cost"])), 8),
        "down_weighted_cost": round(max(0.0, float(totals["Down"]["cost"])), 8),
        "up_weighted_shares": round(max(0.0, float(totals["Up"]["share"])), 8),
        "down_weighted_shares": round(max(0.0, float(totals["Down"]["share"])), 8),
    }


def _weighted_price(weighted_cost: float, weighted_shares: float) -> float | None:
    weighted_shares = max(0.0, float(weighted_shares))
    if weighted_shares <= 0:
        return None
    return round(max(0.0, float(weighted_cost)) / weighted_shares, 8)


def _event_phase(event: dict[str, Any], now: datetime, *, config: ProfileDistributionConfig) -> tuple[str, dict[str, Any]]:
    start = _parse_utc(event.get("event_start_time_utc"))
    end = _parse_utc(event.get("event_end_time_utc"))
    detail: dict[str, Any] = {}
    if start:
        detail["seconds_to_start"] = (start - now).total_seconds()
        detail["seconds_elapsed"] = (now - start).total_seconds()
    if end:
        detail["seconds_remaining"] = (end - now).total_seconds()
    if start and now < start:
        phase = "pre"
    elif end and now > end:
        phase = "post"
    elif start and end and start <= now <= end:
        phase = "live"
        elapsed = (now - start).total_seconds()
        if elapsed < config.live_signal_start_seconds:
            detail["live_window_status"] = "too_early"
        elif elapsed > config.live_signal_end_seconds:
            detail["live_window_status"] = "final_minute_or_late"
        else:
            detail["live_window_status"] = "actionable"
    else:
        phase = "unknown"
    return phase, detail


def _latest_snapshot_source_at(snapshots: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for snapshot in snapshots:
        value = (snapshot.get("distribution_json") or {}).get("latest_source_at_utc")
        parsed = _parse_utc(value)
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _style_weight(profile: dict[str, Any]) -> float:
    for key in ("trading_style_detail", "trading_style"):
        value = str(profile.get(key) or "").strip().lower()
        if value in STYLE_WEIGHTS:
            return STYLE_WEIGHTS[value]
    return STYLE_WEIGHTS["unknown"]


def _normalize_outcome(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"up", "yes", "above", "higher"}:
        return "Up"
    if text in {"down", "no", "below", "lower"}:
        return "Down"
    return None


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    distribution_json = snapshot.get("distribution_json") or {}
    return {
        "event_key": snapshot.get("event_key"),
        "event_slug": snapshot.get("event_slug"),
        "symbol": snapshot.get("symbol"),
        "phase": snapshot.get("phase"),
        "source_mode": snapshot.get("source_mode"),
        "canonical_method": snapshot.get("canonical_method"),
        "profile_count": snapshot.get("profile_count"),
        "component_count": snapshot.get("component_count"),
        "distribution": distribution_json.get("top_profiles_distribution"),
        "pressure_delta": distribution_json.get("pressure_delta"),
        "pressure_delta_abs": distribution_json.get("pressure_delta_abs"),
        "pressure_interpretation": distribution_json.get("pressure_interpretation"),
        "reconstructed_profile_prices": distribution_json.get("reconstructed_profile_prices"),
        "variants": distribution_json.get("variants"),
        "coverage_warnings": distribution_json.get("coverage_warnings", []),
        "blockers": snapshot.get("blockers") or [],
    }


def _iter_payload_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "activity", "positions", "trades"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload] if payload else []


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _timestamp_to_iso(value: Any) -> str | None:
    parsed = _parse_utc(value)
    return parsed.isoformat() if parsed else None


def _symbol_from_slug(slug: str) -> str | None:
    lower = slug.lower()
    if lower.startswith("btc-") or "bitcoin" in lower:
        return "BTC"
    if lower.startswith("eth-") or "ethereum" in lower:
        return "ETH"
    return None


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stable_key(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reject_live_env_flags() -> None:
    enabled = [name for name in LIVE_FLAG_NAMES if str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}]
    if enabled:
        raise RuntimeError(f"data_service_live_flags_rejected:{','.join(enabled)}")


__all__ = [
    "DATA_API_BASE_URL",
    "ProfileDistributionConfig",
    "ProfileDistributionSummary",
    "capture_top_profile_distributions_once",
    "capture_top_profile_distributions_once_sync",
    "feed_worker_config",
    "fetch_data_api_profile_activity",
    "fetch_data_api_profile_positions",
]
