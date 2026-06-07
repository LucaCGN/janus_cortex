from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.data_nodes.polymarket_crypto.history import fetch_current_order_book, normalize_order_book_snapshot
from crypto_options_app.data_nodes.polymarket_crypto.live_capture import LiveCaptureTarget, build_underlying_context, discover_live_crypto_updown_targets
from crypto_options_app.pipelines.options.profile_signals import (
    ProfileSignalConfig,
    build_profile_signal_report,
    refresh_profile_signal_report_from_cache,
)
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_PROFILE_SIGNAL_MONITOR_SCHEMA_VERSION = "crypto_options_profile_signal_monitor_v1"


def build_profile_signal_monitor_tick(
    *,
    strategy_id: str = "btc_eth_5m_mid_high",
    symbols: list[str] | None = None,
    profile_refs: list[str] | None = None,
    obsidian_vault: str | Path | None = None,
    now_utc: datetime | None = None,
    max_workers: int = 8,
    page_limit: int = 200,
    max_signal_to_ask_slippage_cents: float = 10.0,
    max_spread: float = 0.03,
    min_depth_top3_ask_size: float = 5.0,
    min_time_remaining_seconds: float = 20.0,
    max_time_remaining_seconds: float = 300.0,
    include_top_holders: bool = False,
    include_scraped_profiles: bool = False,
    scrape_profile_limit: int = 100,
    include_active_profile_pool: bool = True,
    active_profile_pool_path: str | Path | None = None,
    active_profile_pool_limit: int = 120,
    profile_report_cache: dict[str, Any] | None = None,
    include_underlying_context: bool = False,
    underlying_context_fetcher: Any | None = None,
) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    symbols = [symbol.upper() for symbol in (symbols or ["BTC", "ETH"])]
    targets, discovery = discover_live_crypto_updown_targets(
        symbols=symbols,
        cadence_minutes=5,
        lookback_minutes=5,
        lookahead_minutes=10,
        now=now_utc,
    )
    current_targets = _current_targets(targets, now_utc=now_utc)
    active_slugs = sorted({target.event_slug for target in current_targets if target.event_slug})
    active_conditions = sorted({str(target.condition_id) for target in current_targets if target.condition_id})
    if profile_report_cache is not None:
        report = refresh_profile_signal_report_from_cache(
            profile_report_cache,
            active_event_slugs=active_slugs,
            active_condition_ids=active_conditions,
            now_utc=now_utc,
            config=ProfileSignalConfig(),
            page_limit=page_limit,
            max_workers=max_workers,
        )
    else:
        report = build_profile_signal_report(
            profile_refs=list(profile_refs or []),
            active_event_slugs=active_slugs,
            active_condition_ids=active_conditions,
            obsidian_vault=obsidian_vault,
            include_top_holders=include_top_holders,
            include_active_profile_pool=include_active_profile_pool,
            active_profile_pool_path=active_profile_pool_path,
            active_profile_pool_limit=active_profile_pool_limit,
            include_scraped_profiles=include_scraped_profiles,
            scrape_profile_limit=scrape_profile_limit,
            page_limit=page_limit,
            max_workers=max_workers,
            now_utc=now_utc,
            config=ProfileSignalConfig(),
        )
    selected_variant = _selected_backtest_variant(report, strategy_id)
    backtest_failed_checks = _backtest_failed_checks_for_live_monitor(selected_variant, strategy_id=strategy_id)
    eligible: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    target_by_key = {(target.event_slug, target.outcome): target for target in current_targets}
    underlying_contexts = _underlying_contexts(
        symbols,
        enabled=include_underlying_context,
        fetcher=underlying_context_fetcher,
    )
    for candidate in report.get("aggregated_candidates") or []:
        if not _candidate_matches_strategy(candidate, strategy_id):
            continue
        target = target_by_key.get((str(candidate.get("event_slug") or ""), str(candidate.get("effective_outcome") or "")))
        observed_row = _quote_candidate(
            candidate,
            target=target,
            now_utc=now_utc,
            strategy_id=strategy_id,
            max_signal_to_ask_slippage_cents=max_signal_to_ask_slippage_cents,
            max_spread=max_spread,
            min_depth_top3_ask_size=min_depth_top3_ask_size,
            min_time_remaining_seconds=min_time_remaining_seconds,
            max_time_remaining_seconds=max_time_remaining_seconds,
            inherited_failed_checks=backtest_failed_checks,
            selected_variant=selected_variant,
            underlying_context=underlying_contexts.get(str(target.symbol).upper()) if target else None,
        )
        observed.append(observed_row)
        if not observed_row.get("failed_checks"):
            eligible.append(observed_row)
    monitor_status = "eligible_manual_candidate_present" if eligible else "blocked"
    payload = {
        "schema_version": CRYPTO_OPTIONS_PROFILE_SIGNAL_MONITOR_SCHEMA_VERSION,
        "monitor_status": monitor_status,
        "generated_at_utc": now_utc.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "strategy_id": strategy_id,
        "active_event_slugs": active_slugs,
        "target_count": len(current_targets),
        "profile_signal_report": report,
        "selected_backtest_variant": selected_variant,
        "discovery": discovery,
        "observed_candidate_count": len(observed),
        "eligible_manual_candidate_count": len(eligible),
        "observed_candidates": observed,
        "eligible_manual_candidates": eligible,
        "gates": {
            "max_signal_to_ask_slippage_cents": max_signal_to_ask_slippage_cents,
            "max_spread": max_spread,
            "min_depth_top3_ask_size": min_depth_top3_ask_size,
            "min_time_remaining_seconds": min_time_remaining_seconds,
            "max_time_remaining_seconds": max_time_remaining_seconds,
        },
        "orders_allowed": False,
        "live_trading_authorized": False,
        "boundary": "Profile monitor is read-only candidate generation; live submission is isolated in the live micro-executor.",
    }
    return strict_jsonable(payload)


def build_profile_signal_protocol(*, strategy_id: str = "btc_eth_5m_mid_high", budget_cap_usd: float = 50.0) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_profile_signal_micro_test_protocol_v1",
        "protocol_status": "approved_protocol_ready_for_separate_execution_design",
        "strategy_id": f"profile_signal_{strategy_id}",
        "budget": {
            "max_total_budget_usd": float(budget_cap_usd),
            "max_position_cost_usd": 5.0,
            "min_order_size_shares": 5.0,
            "max_position_shares": 5.0,
            "max_test_trades_before_review": 12,
            "hard_stop_full_losses": 12,
            "hard_stop_loss_usd": min(5.0, float(budget_cap_usd)),
            "global_budget_cap_usd": float(budget_cap_usd),
        },
        "entry_gates": [
            {
                "strategy_id": f"profile_signal_{strategy_id}",
                "candidate_status_required": "candidate_for_manual_review",
                "spread_max": 0.03,
                "depth_top3_ask_size_min": 5.0,
                "quote_age_seconds_max": 5.0,
                "time_remaining_seconds_min": 20.0,
                "time_remaining_seconds_max": 300.0,
            }
        ],
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def write_profile_signal_monitor_artifacts(
    payload: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    protocol_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    root = Path(output_dir) if output_dir else _default_monitor_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    monitor_path = root / f"crypto_options_profile_signal_monitor_{stamp}.json"
    protocol_path = root / "profile_signal_micro_test_protocol.json"
    state_path = root / "manual_loop_state.json"
    monitor_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    protocol = protocol_payload or build_profile_signal_protocol(strategy_id=str(payload.get("strategy_id") or "btc_eth_5m_mid_high"))
    protocol_path.write_text(json.dumps(strict_jsonable(protocol), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    state = {
        "schema_version": "crypto_options_profile_signal_loop_state_v1",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "last_monitor_artifact": str(monitor_path),
        "protocol_artifact": str(protocol_path),
        "monitor_status": payload.get("monitor_status"),
        "eligible_manual_candidate_count": payload.get("eligible_manual_candidate_count"),
    }
    state_path.write_text(json.dumps(strict_jsonable(state), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"monitor": str(monitor_path), "protocol": str(protocol_path), "state": str(state_path)}


def _quote_candidate(
    candidate: dict[str, Any],
    *,
    target: LiveCaptureTarget | None,
    now_utc: datetime,
    strategy_id: str,
    max_signal_to_ask_slippage_cents: float,
    max_spread: float,
    min_depth_top3_ask_size: float,
    min_time_remaining_seconds: float,
    max_time_remaining_seconds: float,
    inherited_failed_checks: list[str],
    selected_variant: dict[str, Any],
    underlying_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed: list[str] = list(dict.fromkeys(inherited_failed_checks))
    if candidate.get("status") and candidate.get("status") != "profile_signal_candidate":
        failed.append("aggregate_candidate_needs_more_validation")
    for blocker in candidate.get("blockers") or []:
        failed.append(f"aggregate_{blocker}")
    if target is None:
        failed.append("candidate_not_in_active_target_set")
        return _candidate_base(candidate, target=None, now_utc=now_utc, strategy_id=strategy_id, failed=failed, underlying_context=underlying_context)
    quote = _fetch_quote(target)
    signal_price = _candidate_signal_price(candidate)
    best_ask = _float(quote.get("best_ask"))
    spread = _float(quote.get("spread"))
    depth = _float(quote.get("depth_top3_ask_size"))
    time_remaining = _seconds_until(target.window_end_time, now_utc=now_utc)
    slippage_cents = (best_ask - signal_price) * 100.0 if best_ask is not None and signal_price is not None else None
    if best_ask is None:
        failed.append("best_ask_missing")
    if quote.get("quote_error"):
        failed.append("quote_fetch_failed")
    if spread is None or spread > max_spread:
        failed.append("spread_above_max_or_missing")
    if depth is None or depth < min_depth_top3_ask_size:
        failed.append("depth_top3_ask_size_below_min_or_missing")
    if time_remaining is None or time_remaining < min_time_remaining_seconds or time_remaining > max_time_remaining_seconds:
        failed.append("time_remaining_outside_gate")
    if slippage_cents is None or slippage_cents > max_signal_to_ask_slippage_cents:
        failed.append("signal_to_ask_slippage_above_max_or_missing")
    if strategy_id == "btc_eth_5m_quality_favorite":
        if best_ask is None or best_ask < 0.55:
            failed.append("quality_favorite_best_ask_below_55c")
        if best_ask is not None and best_ask > 0.90:
            failed.append("quality_favorite_best_ask_above_90c")
        if _float(candidate.get("conflict_ratio")) is None or float(candidate.get("conflict_ratio") or 0.0) > 0.25:
            failed.append("quality_favorite_conflict_ratio_above_25pct")
        if not _candidate_has_supporting_grade(candidate, {"S", "A"}):
            failed.append("quality_favorite_missing_s_or_a_support")
    if strategy_id == "btc_eth_5m_mid_high_quality_overlay":
        bad_profiles = {str(item) for item in selected_variant.get("bad_profiles") or []}
        supporting_profiles = {str(item) for item in candidate.get("supporting_profiles") or [] if item}
        if supporting_profiles and supporting_profiles.issubset(bad_profiles):
            failed.append("quality_overlay_all_supporting_profiles_in_bad_profile_filter")
    base = _candidate_base(candidate, target=target, now_utc=now_utc, strategy_id=strategy_id, failed=failed, underlying_context=underlying_context)
    base.update(
        {
            "quote_at_utc": now_utc.isoformat(),
            "monitor_quote_age_seconds": 0.0,
            "time_remaining_seconds": time_remaining,
            "best_bid": quote.get("best_bid"),
            "best_ask": best_ask,
            "spread": spread,
            "ask_size": quote.get("ask_size"),
            "quote_error": quote.get("quote_error"),
            "depth_top3_ask_size": depth,
            "signal_reference_price": signal_price,
            "signal_to_ask_slippage_cents": slippage_cents,
            "min_order_total_cost": _min_order_total_cost(best_ask),
            "manual_execution_ticket": _manual_ticket(base, best_ask=best_ask),
        }
    )
    base["manual_execution_ticket"] = _manual_ticket(base, best_ask=best_ask)
    return base


def _candidate_base(
    candidate: dict[str, Any],
    *,
    target: LiveCaptureTarget | None,
    now_utc: datetime,
    strategy_id: str,
    failed: list[str],
    underlying_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = underlying_context or {}
    return {
        "strategy_id": f"profile_signal_{strategy_id}",
        "status": "candidate_for_manual_review",
        "event_slug": candidate.get("event_slug"),
        "symbol": target.symbol if target else _symbol_from_event_slug(str(candidate.get("event_slug") or "")),
        "outcome": candidate.get("effective_outcome"),
        "token_id": target.token_id if target else None,
        "window_start_time": target.window_start_time if target else None,
        "window_end_time": target.window_end_time if target else None,
        "settlement_threshold": target.settlement_threshold if target else None,
        "event_threshold_price": target.settlement_threshold if target else None,
        "underlying_price": context.get("underlying_price"),
        "underlying_trend_15m": context.get("underlying_trend_15m"),
        "underlying_trend_30m": context.get("underlying_trend_30m"),
        "underlying_trend_1h": context.get("underlying_trend_1h"),
        "underlying_context": context,
        "quote_at_utc": now_utc.isoformat(),
        "support_weight": candidate.get("support_weight"),
        "conflict_weight": candidate.get("conflict_weight"),
        "conflict_ratio": candidate.get("conflict_ratio"),
        "aggregate_candidate_status": candidate.get("status"),
        "aggregate_blockers": candidate.get("blockers") or [],
        "single_trigger_profile_present": candidate.get("single_trigger_profile_present"),
        "supporting_profiles": candidate.get("supporting_profiles") or [],
        "supporting_signals": candidate.get("supporting_signals") or [],
        "failed_checks": failed,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _underlying_contexts(
    symbols: list[str],
    *,
    enabled: bool,
    fetcher: Any | None = None,
) -> dict[str, dict[str, Any]]:
    if not enabled:
        return {}
    fetch = fetcher or build_underlying_context
    contexts: dict[str, dict[str, Any]] = {}
    for symbol in sorted({str(item).upper() for item in symbols if item}):
        try:
            context = fetch(symbol)
        except Exception as exc:  # noqa: BLE001 - context failures should block V4 entries, not stop monitor generation.
            context = {
                "schema_version": "crypto_options_underlying_context_v1",
                "symbol": symbol,
                "underlying_price": None,
                "source": "underlying_context_fetch_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        if isinstance(context, dict):
            contexts[symbol] = context
    return contexts


def _manual_ticket(row: dict[str, Any], *, best_ask: float | None) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_manual_execution_ticket_v1",
        "ticket_type": "profile_signal_micro_test",
        "event_slug": row.get("event_slug"),
        "strategy_id": row.get("strategy_id"),
        "symbol": row.get("symbol"),
        "outcome": row.get("outcome"),
        "token_id": row.get("token_id"),
        "shares": 5.0,
        "observed_best_ask": best_ask,
        "estimated_total_cost_usd": _min_order_total_cost(best_ask),
        "max_total_cost_usd": 5.0,
        "quote_at_utc": row.get("quote_at_utc"),
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _fetch_quote(target: LiveCaptureTarget) -> dict[str, Any]:
    try:
        payload = fetch_current_order_book(target.token_id)
    except Exception as exc:  # noqa: BLE001 - quote fetch failures should block candidates, not stop the watcher.
        return {
            "quote_error": f"{type(exc).__name__}: {exc}",
            "quote_provider": "polymarket_current_order_book_profile_signal_monitor",
        }
    frame = normalize_order_book_snapshot(
        payload,
        event_id=target.event_id,
        market_id=target.market_id,
        outcome=target.outcome,
        symbol=target.symbol,
        source="polymarket_current_order_book_profile_signal_monitor",
    )
    return frame.iloc[0].to_dict() if not frame.empty else {}


def _candidate_matches_strategy(candidate: dict[str, Any], strategy_id: str) -> bool:
    slug = str(candidate.get("event_slug") or "")
    price = _candidate_signal_price(candidate)
    if strategy_id == "btc_5m_only":
        return slug.startswith("btc-updown-5m")
    if strategy_id == "btc_eth_5m_mid_high":
        return (slug.startswith("btc-updown-5m") or slug.startswith("eth-updown-5m")) and price is not None and 0.10 <= price <= 0.99
    if strategy_id == "btc_eth_5m_mid_high_quality_overlay":
        return (slug.startswith("btc-updown-5m") or slug.startswith("eth-updown-5m")) and price is not None and 0.45 <= price <= 0.99
    if strategy_id == "btc_eth_5m_quality_favorite":
        return (slug.startswith("btc-updown-5m") or slug.startswith("eth-updown-5m")) and price is not None and 0.55 <= price <= 0.90
    return True


def _candidate_has_supporting_grade(candidate: dict[str, Any], grades: set[str]) -> bool:
    return any(str(signal.get("profile_grade") or "") in grades for signal in candidate.get("supporting_signals") or [])


def _selected_backtest_variant(report: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    variants = ((report.get("backtest") or {}).get("strategy_variants") or [])
    for variant in variants:
        if variant.get("strategy_id") == strategy_id:
            return variant
    return {"strategy_id": strategy_id, "live_readiness": {"status": "not_ready", "failed_gates": ["variant_missing"]}}


def _backtest_failed_checks_for_live_monitor(selected_variant: dict[str, Any], *, strategy_id: str) -> list[str]:
    readiness = selected_variant.get("live_readiness") or {}
    if readiness.get("status") == "ready_for_supervised_micro_test":
        return []
    failed = [str(item) for item in readiness.get("failed_gates") or []]
    if strategy_id == "btc_eth_5m_mid_high_quality_overlay" and _quality_overlay_forward_validation_allowed(selected_variant, failed):
        return []
    return ["selected_backtest_variant_not_ready", *failed]


def _quality_overlay_forward_validation_allowed(selected_variant: dict[str, Any], failed_gates: list[str]) -> bool:
    allowed_failures = {"trade_count_below_target", "exploratory_in_sample_profile_filter_requires_forward_validation"}
    if any(item not in allowed_failures for item in failed_gates):
        return False
    metrics = selected_variant.get("trade_metrics") or {}
    economics = selected_variant.get("economics") or {}
    sensitivity = {
        int(row.get("adverse_slippage_cents")): row
        for row in (economics.get("slippage_sensitivity") or [])
        if row.get("adverse_slippage_cents") is not None
    }
    slippage_3c = sensitivity.get(3) or {}
    return bool(
        int(metrics.get("trade_count") or 0) >= 6
        and float(metrics.get("win_rate") or 0.0) >= 0.75
        and float(metrics.get("return_sum") or 0.0) > 0.0
        and float(metrics.get("max_sequential_losses") or 0) <= 2
        and float(slippage_3c.get("return_sum") or 0.0) > 0.0
    )


def _candidate_signal_price(candidate: dict[str, Any]) -> float | None:
    prices = [
        _float(signal.get("price"))
        for signal in candidate.get("supporting_signals") or []
        if _float(signal.get("price")) is not None
    ]
    if not prices:
        return None
    return sum(prices) / len(prices)


def _current_targets(targets: list[LiveCaptureTarget], *, now_utc: datetime) -> list[LiveCaptureTarget]:
    rows: list[LiveCaptureTarget] = []
    for target in targets:
        start = _parse_time(target.window_start_time)
        end = _parse_time(target.window_end_time)
        if start is None or end is None:
            continue
        if start <= now_utc < end:
            rows.append(target)
    return rows


def _seconds_until(value: str, *, now_utc: datetime) -> float | None:
    parsed = _parse_time(value)
    return (parsed - now_utc).total_seconds() if parsed else None


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _min_order_total_cost(best_ask: float | None) -> float | None:
    if best_ask is None:
        return None
    fee = 0.07 * float(best_ask) * (1.0 - float(best_ask))
    return 5.0 * (float(best_ask) + fee)


def _symbol_from_event_slug(slug: str) -> str | None:
    prefix = slug.split("-", 1)[0].upper()
    return prefix if prefix in {"BTC", "ETH", "SOL", "XRP"} else None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_monitor_root() -> Path:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return resolve_shared_root() / "artifacts" / "crypto-options-research" / "profile-signal-loop" / day / stamp


__all__ = [
    "CRYPTO_OPTIONS_PROFILE_SIGNAL_MONITOR_SCHEMA_VERSION",
    "build_profile_signal_monitor_tick",
    "build_profile_signal_protocol",
    "write_profile_signal_monitor_artifacts",
]
