from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from crypto_options_app.data_nodes.polymarket_crypto.accounts import (
    fetch_top_holders_for_markets,
    fetch_user_activity,
    fetch_user_closed_positions,
    fetch_user_pnl_history,
    fetch_user_positions,
    fetch_user_trades,
    resolve_polymarket_profile,
    scrape_market_profile_refs,
)
from crypto_options_app.data_nodes.polymarket_crypto.markets import fetch_gamma_event_by_slug
from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_PROFILE_SIGNAL_SCHEMA_VERSION = "crypto_options_profile_signal_report_v1"

USER_REQUESTED_PROFILE_REFS = [
    "@nagi777",
    "@0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82-1772569391020",
    "@junkman999",
    "@drfc4eybh7i8",
    "@0xedEE942D17893FACCF866b72670D5F3ca2bb7139",
    "@0x6982049c65e98606f65a0ce71fdb9b61296da165-1777135114945",
    "@prostoludoman",
    "@0xb55fa1296e6ec55d0ce53d93b9237389f11764d4-1777575277609",
    "@0x4705408c791455edffc66782d4cea30c17afd03e",
    "@nndrekop",
    "@ohanism",
    "@nihiiism",
    "@uyhtfvbnd",
    "@btc5mscour",
    "@x1x1x1",
    "@0x50f7",
    "profile/0xe9076a87c5ed90ef16e6fe6529c943baeca0cff6",
    "@pbot-3",
    "profile/0xbF337426aa856996B8bb79B238345Dd1A0276bF7",
    "@edgebot",
    "@bonereaper",
]

USER_CURATED_VISIBLE_TOP_HOLDER_REFS = [
    "@marketing101",
    "@Peaceful-Quadrant",
    "@hot-garbage",
    "@ZhengYing999",
    "@Agile-Spacing",
    "@gastatd",
    "@Savvvv",
]

OBSIDIAN_CRYPTO_PROFILE_HINTS = {
    "0xb55fa1296e6ec55d0ce53d93b9237389f11764d4-1777575277609",
    "baloneigh",
    "mikeaddon",
    "pbot-6",
    "predictfolio",
    "wuhuuuuuuli",
}

DISCOVERED_TOP_HOLDER_WINNER_PROFILE_REFS = [
    "0x3c58ef422754ff22c7e806336feba0064d8b776b",
    "0xba016b05c84c9f073e5c9059d247d37cea4b8535",
    "0x6fdc687773d4ba8753ea406f4eb2a403051a953f",
    "0x48ac40fc545cf327edd5365435c3a9f385614a7e",
    "0xd9013df863c1ba932780857b020dfdeacedf8e14",
]

DISCOVERED_TOP_HOLDER_LOSER_PROFILE_REFS = [
    "0x18954a8003ebbbdaa4d9190cd2505382a5b902fb",
    "0xa832e7afb91c8083d42474faaf05f9abfaf8abf8",
    "0xd594c3f395c3955b8b06aa5fb428b514bbf29d1b",
    "0x18e3861378b15f5a4ab5812ce006c7e8adfe1463",
    "0x77586afad036c798693ef241780a3a48d22a42a9",
]

DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT = 120


@dataclass(frozen=True)
class ProfileSignalConfig:
    """Tunable profile grading and signal aggregation policy."""

    recent_signal_window_seconds: int = 300
    bot_trade_window_seconds: int = 900
    min_bot_trades_per_15m: int = 8
    min_recent_crypto_share: float = 0.40
    profile_style_window_seconds: int = 86_400
    profile_frequency_window_seconds: int = 3_600
    expected_crypto_events_per_hour: int = 12
    s_plus_plus_min_score: float = 100.0
    s_plus_plus_min_crypto_events_1h: int = 6
    s_plus_plus_min_crypto_events_24h: int = 6
    s_plus_plus_min_daily_pnl_usd: float = 1_000.0
    s_plus_plus_min_weekly_pnl_usd: float = 5_000.0
    s_plus_plus_min_monthly_pnl_usd: float = 20_000.0
    pnl_scale_hourly: float = 3_000.0
    pnl_scale_three_hour: float = 6_000.0
    pnl_scale_six_hour: float = 8_000.0
    pnl_scale_twelve_hour: float = 9_000.0
    grade_thresholds: dict[str, float] = field(
        default_factory=lambda: {"S+": 90.0, "S": 82.0, "A": 70.0, "B": 56.0, "C": 42.0, "D": 25.0}
    )
    grade_weights: dict[str, float] = field(
        default_factory=lambda: {
            "S++": 4.25,
            "S+": 3.5,
            "S": 3.0,
            "A": 1.6,
            "B": 0.0,
            "C": 0.0,
            "D": 0.0,
            "E": 1.2,
            "U": 0.35,
        }
    )
    inverse_grades: tuple[str, ...] = ("E", "U")
    single_trigger_grades: tuple[str, ...] = ("S", "S+", "S++")
    confirmation_grades: tuple[str, ...] = ("A",)
    ignored_signal_grades: tuple[str, ...] = ("B", "C", "D")
    allow_confirmation_only_candidates: bool = False
    allow_inverse_only_candidates: bool = False
    dedupe_latest_signal_per_profile_event: bool = True
    split_side_max_conflict_ratio: float = 1.25
    aggregate_trigger_weight: float = 3.0
    aggregate_min_profiles: int = 2
    max_conflict_ratio: float = 0.50
    pnl_scale_daily: float = 10_000.0
    pnl_scale_weekly: float = 35_000.0
    pnl_scale_monthly: float = 75_000.0
    pnl_scale_quarterly: float = 200_000.0
    pnl_scale_all: float = 250_000.0
    s_grade_min_daily_pnl_usd: float = 0.0
    s_grade_min_weekly_pnl_usd: float = 0.0
    s_grade_min_monthly_pnl_usd: float = 0.0
    s_grade_min_closed_win_rate: float = 0.55
    s_grade_min_closed_outcomes: int = 5
    s_plus_min_daily_pnl_usd: float = 0.0
    s_plus_min_weekly_pnl_usd: float = 0.0
    s_plus_min_monthly_pnl_usd: float = 0.0
    s_plus_min_quarterly_pnl_usd: float = 0.0
    s_plus_min_all_pnl_usd: float = 0.0
    s_plus_min_closed_win_rate: float = 0.70
    s_plus_min_closed_outcomes: int = 10
    s_plus_max_closed_loss_streak: int = 3
    backtest_lookback_seconds: int = 3600
    backtest_target_trade_count: int = 12
    backtest_target_win_rate: float = 0.70
    backtest_min_supervised_win_rate: float = 0.55
    backtest_min_edge_over_breakeven: float = 0.02
    backtest_supervised_slippage_cents: int = 3
    control_profile_count: int = 2


def build_profile_signal_report(
    profile_refs: list[str] | None = None,
    *,
    active_event_slugs: list[str] | None = None,
    active_condition_ids: list[str] | None = None,
    obsidian_vault: str | Path | None = None,
    include_obsidian_profiles: bool = True,
    include_default_profiles: bool = True,
    include_active_profile_pool: bool = False,
    active_profile_pool_path: str | Path | None = None,
    active_profile_pool_limit: int = DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT,
    include_top_holders: bool = False,
    top_holder_limit: int = 20,
    include_scraped_profiles: bool = False,
    scrape_profile_limit: int = 100,
    activity_pages: int = 1,
    positions_pages: int = 1,
    trades_pages: int = 1,
    closed_pages: int = 1,
    page_limit: int = 500,
    max_workers: int = 8,
    now_utc: datetime | None = None,
    config: ProfileSignalConfig | None = None,
    profile_source_fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    top_holder_fetcher: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    scrape_profile_fetcher: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    outcome_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a read-only profile grading and active-signal aggregation report."""

    now_utc = _coerce_datetime(now_utc) or datetime.now(timezone.utc)
    config = config or ProfileSignalConfig()
    seeds = build_profile_registry(
        profile_refs,
        obsidian_vault=obsidian_vault,
        include_obsidian_profiles=include_obsidian_profiles,
        include_default_profiles=include_default_profiles,
        include_active_profile_pool=include_active_profile_pool,
        active_profile_pool_path=active_profile_pool_path,
        active_profile_pool_limit=active_profile_pool_limit,
    )
    active_profile_pool = load_active_crypto_profile_pool_refs(
        active_profile_pool_path,
        limit=active_profile_pool_limit,
        enabled=include_active_profile_pool,
    )
    top_holder_discovery = _top_holder_discovery(
        active_condition_ids or [],
        enabled=include_top_holders,
        limit=top_holder_limit,
        fetcher=top_holder_fetcher,
    )
    scraped_profile_discovery = _scraped_profile_discovery(
        active_event_slugs or [],
        enabled=include_scraped_profiles,
        limit=scrape_profile_limit,
        fetcher=scrape_profile_fetcher,
    )
    seeds = _dedupe_registry(seeds + top_holder_discovery["profile_refs"] + scraped_profile_discovery["profile_refs"])
    snapshots, blockers = _fetch_profile_snapshots(
        seeds,
        now_utc=now_utc,
        config=config,
        profile_source_fetcher=profile_source_fetcher,
        activity_pages=activity_pages,
        positions_pages=positions_pages,
        trades_pages=trades_pages,
        closed_pages=closed_pages,
        page_limit=page_limit,
        max_workers=max_workers,
    )
    snapshots, dedupe_blockers = _dedupe_profile_snapshots_by_identity(snapshots)
    blockers.extend(dedupe_blockers)

    active_signals = _active_signals(
        snapshots,
        active_event_slugs=active_event_slugs or [],
        now_utc=now_utc,
        config=config,
    )
    candidates = aggregate_profile_signals(active_signals, config=config)
    backtest = build_profile_signal_backtest(
        snapshots,
        now_utc=now_utc,
        config=config,
        outcome_resolver=outcome_resolver,
    )
    payload = {
        "schema_version": CRYPTO_OPTIONS_PROFILE_SIGNAL_SCHEMA_VERSION,
        "generated_at_utc": now_utc.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "profile_seed_count": len(seeds),
        "profile_fetch_max_workers": max_workers,
        "profile_snapshot_count": len(snapshots),
        "active_signal_count": len(active_signals),
        "candidate_count": len(candidates),
        "active_event_slugs": sorted(set(active_event_slugs or [])),
        "active_condition_ids": sorted(set(active_condition_ids or [])),
        "config": asdict(config),
        "profile_registry": seeds,
        "active_profile_pool": active_profile_pool,
        "top_holder_discovery": top_holder_discovery,
        "scraped_profile_discovery": scraped_profile_discovery,
        "profiles": snapshots,
        "active_signals": active_signals,
        "aggregated_candidates": candidates,
        "backtest": backtest,
        "blockers": blockers,
        "boundary": {
            "orders_allowed": False,
            "live_trading_authorized": False,
            "description": (
                "Profile signals are research and candidate-generation evidence only. "
                "They do not place, cancel, sign, broadcast, redeem, route, recommend, or authorize orders."
            ),
        },
    }
    return strict_jsonable(payload)


def refresh_profile_signal_report_from_cache(
    profile_report: dict[str, Any],
    *,
    active_event_slugs: list[str] | None = None,
    active_condition_ids: list[str] | None = None,
    now_utc: datetime | None = None,
    config: ProfileSignalConfig | None = None,
    refresh_activity: bool = True,
    activity_pages: int = 1,
    page_limit: int = 50,
    max_workers: int = 8,
    activity_fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Reuse cached profile snapshots and recompute only current-event signals."""

    now_utc = _coerce_datetime(now_utc) or datetime.now(timezone.utc)
    config = config or _profile_signal_config_from_payload(profile_report.get("config"))
    snapshots = [dict(row) for row in profile_report.get("profiles") or [] if isinstance(row, dict)]
    refresh_blockers: list[str] = []
    if refresh_activity and snapshots:
        snapshots, refresh_blockers = _refresh_cached_profile_snapshot_signals(
            snapshots,
            now_utc=now_utc,
            config=config,
            activity_pages=activity_pages,
            page_limit=page_limit,
            max_workers=max_workers,
            activity_fetcher=activity_fetcher,
        )
    active_signals = _active_signals(
        snapshots,
        active_event_slugs=active_event_slugs or [],
        now_utc=now_utc,
        config=config,
    )
    candidates = aggregate_profile_signals(active_signals, config=config)
    payload = dict(profile_report)
    payload.update(
        {
            "schema_version": CRYPTO_OPTIONS_PROFILE_SIGNAL_SCHEMA_VERSION,
            "generated_at_utc": now_utc.isoformat(),
            "profile_snapshot_source": "cached_profile_signal_report",
            "profile_snapshot_source_generated_at_utc": profile_report.get("generated_at_utc"),
            "profile_fetch_max_workers": 0,
            "profile_snapshot_count": len(snapshots),
            "active_signal_count": len(active_signals),
            "candidate_count": len(candidates),
            "active_event_slugs": sorted(set(active_event_slugs or [])),
            "active_condition_ids": sorted(set(active_condition_ids or [])),
            "config": asdict(config),
            "profiles": snapshots,
            "active_signals": active_signals,
            "aggregated_candidates": candidates,
        }
    )
    payload["blockers"] = list(profile_report.get("blockers") or []) + refresh_blockers
    return strict_jsonable(payload)


def _refresh_cached_profile_snapshot_signals(
    snapshots: list[dict[str, Any]],
    *,
    now_utc: datetime,
    config: ProfileSignalConfig,
    activity_pages: int,
    page_limit: int,
    max_workers: int,
    activity_fetcher: Callable[[str], list[dict[str, Any]]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    workers = max(1, min(int(max_workers), max(len(snapshots), 1)))
    blockers: list[str] = []
    refreshed_by_index: dict[int, dict[str, Any]] = {}

    def refresh_one(index: int, snapshot: dict[str, Any]) -> tuple[int, dict[str, Any], str | None]:
        profile = snapshot.get("profile") or {}
        wallet = str(profile.get("proxy_wallet") or profile.get("proxyWallet") or "").strip()
        if not wallet:
            return index, snapshot, f"profile_activity_refresh_missing_wallet:{profile.get('name') or index}"
        try:
            activity = (
                activity_fetcher(wallet)
                if activity_fetcher
                else fetch_user_activity(wallet, pages=activity_pages, limit=page_limit)
            )
        except Exception as exc:  # noqa: BLE001 - one stale profile should not stop the live tick.
            return index, snapshot, f"profile_activity_refresh_failed:{wallet}:{type(exc).__name__}"
        grade = {
            "grade": snapshot.get("grade") or "U",
            "score": snapshot.get("score") or 0.0,
            "polarity": snapshot.get("polarity") or "follow",
        }
        live_profile = {
            "name": profile.get("name"),
            "proxyWallet": wallet,
        }
        profile_metrics = _cached_profile_metrics_for_refresh(snapshot)
        updated = dict(snapshot)
        updated["signals"] = extract_recent_profile_signals(
            [row for row in activity or [] if isinstance(row, dict)],
            profile=live_profile,
            grade=grade,
            now_utc=now_utc,
            config=config,
            profile_metrics=profile_metrics,
        )
        updated["signal_refresh"] = {
            "status": "fresh_activity",
            "refreshed_at_utc": now_utc.isoformat(),
            "activity_row_count": len(activity or []),
            "page_limit": int(page_limit),
        }
        return index, updated, None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(refresh_one, index, snapshot) for index, snapshot in enumerate(snapshots)]
        for future in as_completed(futures):
            index, snapshot, blocker = future.result()
            refreshed_by_index[index] = snapshot
            if blocker:
                blockers.append(blocker)
    return [refreshed_by_index.get(index, snapshot) for index, snapshot in enumerate(snapshots)], blockers


def _cached_profile_metrics_for_refresh(snapshot: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(snapshot.get("metrics") or {})
    classification = snapshot.get("profile_classification")
    if isinstance(classification, dict):
        metrics["profile_classification"] = dict(classification)
    for key in ("trading_style", "trading_style_detail", "frequency_class"):
        if snapshot.get(key) is not None:
            metrics[key] = snapshot.get(key)
    return metrics


def _profile_signal_config_from_payload(payload: Any) -> ProfileSignalConfig:
    if not isinstance(payload, dict):
        return ProfileSignalConfig()
    defaults = ProfileSignalConfig()
    values: dict[str, Any] = {}
    for config_field in fields(ProfileSignalConfig):
        if config_field.name not in payload:
            continue
        value = payload[config_field.name]
        default_value = getattr(defaults, config_field.name)
        if isinstance(default_value, tuple) and isinstance(value, list):
            value = tuple(value)
        values[config_field.name] = value
    try:
        return ProfileSignalConfig(**values)
    except TypeError:
        return ProfileSignalConfig()


def build_profile_registry(
    profile_refs: list[str] | None = None,
    *,
    obsidian_vault: str | Path | None = None,
    include_obsidian_profiles: bool = True,
    include_default_profiles: bool = True,
    include_active_profile_pool: bool = False,
    active_profile_pool_path: str | Path | None = None,
    active_profile_pool_limit: int = DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if include_default_profiles:
        refs.extend(normalize_profile_ref(ref, source="user_default_seed") for ref in USER_REQUESTED_PROFILE_REFS)
        refs.extend(
            normalize_profile_ref(ref, source="user_curated_visible_top_holder_seed")
            for ref in USER_CURATED_VISIBLE_TOP_HOLDER_REFS
        )
        refs.extend(
            normalize_profile_ref(ref, source="top_holder_monthly_winner_seed")
            for ref in DISCOVERED_TOP_HOLDER_WINNER_PROFILE_REFS
        )
        refs.extend(
            normalize_profile_ref(ref, source="top_holder_monthly_loser_seed")
            for ref in DISCOVERED_TOP_HOLDER_LOSER_PROFILE_REFS
        )
    refs.extend(
        load_active_crypto_profile_pool_refs(
            active_profile_pool_path,
            limit=active_profile_pool_limit,
            enabled=include_active_profile_pool,
        )
    )
    refs.extend(normalize_profile_ref(ref, source="user_supplied_seed") for ref in (profile_refs or []))
    if include_obsidian_profiles:
        refs.extend(load_obsidian_crypto_profile_refs(obsidian_vault))
    return _dedupe_registry(refs)


def default_active_crypto_profile_pool_path() -> Path:
    return resolve_shared_root() / "artifacts" / "crypto-options-research" / "profile-pool" / "active_crypto_profile_pool.txt"


def load_active_crypto_profile_pool_refs(
    path: str | Path | None = None,
    *,
    limit: int = DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    pool_path = Path(path).expanduser() if path else default_active_crypto_profile_pool_path()
    if not pool_path.exists():
        return []
    refs: list[dict[str, Any]] = []
    for line in pool_path.read_text(encoding="utf-8", errors="replace").splitlines():
        cleaned = _clean_active_profile_pool_line(line)
        if cleaned is None:
            continue
        refs.append(normalize_profile_ref(cleaned, source=f"active_crypto_profile_pool:{pool_path.name}"))
        if limit > 0 and len(refs) >= limit:
            break
    return _dedupe_registry(refs)


def _clean_active_profile_pool_line(value: str) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned or cleaned.startswith("#"):
        return None
    cleaned = re.sub(r"^https?://(?:www\.)?polymarket\.com/", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.split("?", 1)[0].strip().strip("/")
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    if "..." in cleaned:
        return None
    if cleaned.lower().startswith("profile/"):
        candidate = cleaned.split("/", 1)[1]
    else:
        candidate = cleaned
    if _is_wallet(candidate):
        return candidate
    if re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", candidate):
        return candidate
    return None


def _fetch_profile_snapshots(
    seeds: list[dict[str, Any]],
    *,
    now_utc: datetime,
    config: ProfileSignalConfig,
    profile_source_fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None,
    activity_pages: int,
    positions_pages: int,
    trades_pages: int,
    closed_pages: int,
    page_limit: int,
    max_workers: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    snapshots_by_ref: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []

    def load(seed: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
        key = str(seed.get("normalized_ref") or seed.get("raw_ref") or "")
        try:
            source = (
                profile_source_fetcher(seed)
                if profile_source_fetcher
                else fetch_profile_signal_source(
                    seed,
                    activity_pages=activity_pages,
                    positions_pages=positions_pages,
                    trades_pages=trades_pages,
                    closed_pages=closed_pages,
                    page_limit=page_limit,
                )
            )
            return key, build_profile_snapshot(seed, source, now_utc=now_utc, config=config), None
        except Exception as exc:  # noqa: BLE001 - one bad public profile should not block the full report.
            blocked = {
                "profile_ref": seed,
                "status": "blocked",
                "blockers": [f"{type(exc).__name__}:{exc}"],
                "grade": "U",
                "score": None,
                "polarity": "ignore",
                "signals": [],
                "historical_signals": [],
            }
            return key, blocked, f"profile_fetch_failed:{seed.get('normalized_ref')}:{type(exc).__name__}:{exc}"

    workers = max(1, min(int(max_workers), max(len(seeds), 1)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(load, seed): seed for seed in seeds}
        for future in as_completed(futures):
            key, snapshot, blocker = future.result()
            snapshots_by_ref[key] = snapshot
            if blocker:
                blockers.append(blocker)
    return [snapshots_by_ref[str(seed.get("normalized_ref") or seed.get("raw_ref") or "")] for seed in seeds], blockers


def normalize_profile_ref(value: str | dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    if isinstance(value, dict):
        raw = str(value.get("raw_ref") or value.get("handle") or value.get("address") or value.get("profile") or "").strip()
        source = str(value.get("source") or source)
    else:
        raw = str(value or "").strip()
    cleaned = raw.strip()
    cleaned = re.sub(r"^https?://(?:www\.)?polymarket\.com/", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.split("?", 1)[0].strip("/")
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    if cleaned.lower().startswith("profile/"):
        cleaned = cleaned.split("/", 1)[1]
    if _is_wallet(cleaned):
        address = cleaned.lower()
        handle = None
        key = f"address:{address}"
    else:
        address = None
        handle = cleaned
        key = f"handle:{cleaned.lower()}"
    return {
        "raw_ref": raw,
        "normalized_ref": key,
        "handle": handle,
        "address": address,
        "source": source,
    }


def load_obsidian_crypto_profile_refs(obsidian_vault: str | Path | None) -> list[dict[str, Any]]:
    if not obsidian_vault:
        return [normalize_profile_ref(ref, source="obsidian_seed_hint") for ref in sorted(OBSIDIAN_CRYPTO_PROFILE_HINTS)]
    root = Path(obsidian_vault)
    study_dir = root / "40_Profile_Studies"
    if not study_dir.exists():
        return [normalize_profile_ref(ref, source="obsidian_seed_hint") for ref in sorted(OBSIDIAN_CRYPTO_PROFILE_HINTS)]
    refs: list[dict[str, Any]] = []
    for path in study_dir.glob("Profile - *.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"\b(crypto|bitcoin|btc|ethereum|eth|#47|up/down)\b", text, flags=re.IGNORECASE):
            continue
        matches = re.findall(r"https?://(?:www\.)?polymarket\.com/@([A-Za-z0-9_.\-]+)", text)
        if matches:
            refs.extend(normalize_profile_ref(match, source=f"obsidian:{path.name}") for match in matches)
            continue
        name = path.stem.removeprefix("Profile - ").strip()
        if name:
            refs.append(normalize_profile_ref(name, source=f"obsidian:{path.name}"))
    return _dedupe_registry(refs)


def fetch_profile_signal_source(
    seed: dict[str, Any],
    *,
    activity_pages: int = 1,
    positions_pages: int = 1,
    trades_pages: int = 1,
    closed_pages: int = 1,
    page_limit: int = 500,
) -> dict[str, Any]:
    handle = seed.get("handle")
    address = seed.get("address")
    profile = resolve_polymarket_profile(handle=handle, address=address)
    wallet = str(profile.get("proxyWallet") or address or "").strip()
    if not wallet:
        raise ValueError("missing_proxy_wallet")
    return {
        "profile": profile,
        "activity": fetch_user_activity(wallet, pages=activity_pages, limit=page_limit),
        "positions": fetch_user_positions(wallet, pages=positions_pages, limit=page_limit),
        "trades": fetch_user_trades(wallet, pages=trades_pages, limit=page_limit),
        "closed_positions": fetch_user_closed_positions(wallet, pages=closed_pages, limit=page_limit),
        "pnl_recent": fetch_user_pnl_history(wallet, interval="1m", fidelity="1h"),
        "pnl_all": fetch_user_pnl_history(wallet, interval="all", fidelity="1d"),
    }


def build_profile_snapshot(
    seed: dict[str, Any],
    source: dict[str, Any],
    *,
    now_utc: datetime,
    config: ProfileSignalConfig,
) -> dict[str, Any]:
    profile = source.get("profile") or {}
    activity = [row for row in source.get("activity") or [] if isinstance(row, dict)]
    positions = [row for row in source.get("positions") or [] if isinstance(row, dict)]
    trades = [row for row in source.get("trades") or activity if isinstance(row, dict)]
    closed_positions = [row for row in source.get("closed_positions") or [] if isinstance(row, dict)]
    metrics = compute_profile_metrics(
        activity=activity,
        positions=positions,
        trades=trades,
        closed_positions=closed_positions,
        pnl_recent=source.get("pnl_recent") or [],
        pnl_all=source.get("pnl_all") or [],
        now_utc=now_utc,
        config=config,
    )
    grade = grade_profile(metrics, config=config)
    signals = extract_recent_profile_signals(
        activity,
        profile=profile,
        grade=grade,
        profile_metrics=metrics,
        now_utc=now_utc,
        config=config,
    )
    historical_signals = extract_profile_signals(
        activity,
        profile=profile,
        grade=grade,
        profile_metrics=metrics,
        now_utc=now_utc,
        config=config,
        start_utc=now_utc - timedelta(seconds=max(config.backtest_lookback_seconds, config.recent_signal_window_seconds)),
    )
    return {
        "profile_ref": seed,
        "status": "complete",
        "profile": {
            "name": profile.get("name") or seed.get("handle") or seed.get("address"),
            "pseudonym": profile.get("pseudonym"),
            "proxy_wallet": profile.get("proxyWallet") or seed.get("address"),
            "created_at": profile.get("createdAt"),
            "verified_badge": bool(profile.get("verifiedBadge")) if profile.get("verifiedBadge") is not None else None,
        },
        "score": grade["score"],
        "grade": grade["grade"],
        "polarity": grade["polarity"],
        "grade_reasons": grade["reasons"],
        "metrics": metrics,
        "source_rows": {
            "activity": strict_jsonable(activity),
            "trades": strict_jsonable(trades),
            "positions": strict_jsonable(positions),
            "closed_positions": strict_jsonable(closed_positions),
            "pnl_recent": strict_jsonable(source.get("pnl_recent") or []),
            "pnl_all": strict_jsonable(source.get("pnl_all") or []),
        },
        "profile_classification": metrics.get("profile_classification") or {},
        "trading_style": metrics.get("trading_style"),
        "trading_style_detail": metrics.get("trading_style_detail"),
        "frequency_class": metrics.get("frequency_class"),
        "signals": signals,
        "historical_signals": historical_signals,
    }


def compute_profile_metrics(
    *,
    activity: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
    pnl_recent: list[dict[str, Any]],
    pnl_all: list[dict[str, Any]],
    now_utc: datetime,
    config: ProfileSignalConfig,
) -> dict[str, Any]:
    recent_signal_start = now_utc - timedelta(seconds=config.recent_signal_window_seconds)
    bot_start = now_utc - timedelta(seconds=config.bot_trade_window_seconds)
    activity_rows = [_activity_view(row) for row in activity]
    trade_rows = [_activity_view(row) for row in trades]
    closed_rows = [_closed_view(row) for row in closed_positions]
    recent_activity = [row for row in activity_rows if row["timestamp"] and row["timestamp"] >= recent_signal_start]
    bot_window_activity = [row for row in activity_rows if row["timestamp"] and row["timestamp"] >= bot_start]
    crypto_rows = [row for row in activity_rows if _is_crypto_updown(row)]
    recent_crypto_rows = [row for row in recent_activity if _is_crypto_updown(row)]
    reconstructed_events = _reconstruct_profile_events(crypto_rows, now_utc=now_utc)
    closed_stats = _closed_stats(closed_rows)
    reconstructed_closed_stats = _reconstructed_event_stats(reconstructed_events)
    effective_closed_stats = closed_stats if (closed_stats["wins"] + closed_stats["losses"]) else reconstructed_closed_stats
    pnl = _pnl_metrics(pnl_recent, pnl_all, now_utc=now_utc)
    period_metrics = _profile_period_metrics(
        pnl_recent=pnl_recent,
        pnl_all=pnl_all,
        closed_rows=closed_rows,
        reconstructed_events=reconstructed_events,
        now_utc=now_utc,
    )
    profile_classification = _classify_profile_trading_behavior(
        crypto_rows,
        reconstructed_events=reconstructed_events,
        now_utc=now_utc,
        config=config,
    )
    position_value = sum(_to_float(row.get("currentValue") or row.get("current_value")) or 0.0 for row in positions)
    position_pnl = sum(_to_float(row.get("cashPnl") or row.get("cash_pnl")) or 0.0 for row in positions)
    trades_per_15m = len(bot_window_activity) / max(config.bot_trade_window_seconds / 900.0, 1e-9)
    recent_crypto_share = len(recent_crypto_rows) / len(recent_activity) if recent_activity else 0.0
    bot_frequency_score = min(1.0, len(bot_window_activity) / max(config.min_bot_trades_per_15m, 1))
    return {
        "activity_rows": len(activity_rows),
        "trade_rows": len(trade_rows),
        "closed_position_rows": len(closed_rows),
        "open_position_rows": len(positions),
        "latest_activity_utc": _latest_iso(activity_rows),
        "recent_activity_count_5m": len(recent_activity),
        "recent_crypto_activity_count_5m": len(recent_crypto_rows),
        "crypto_signal_count_1h": profile_classification["crypto_signal_count_1h"],
        "crypto_event_count_1h": profile_classification["crypto_event_count_1h"],
        "crypto_event_count_24h": profile_classification["crypto_event_count_24h"],
        "crypto_event_coverage_1h": profile_classification["crypto_event_coverage_1h"],
        "bot_window_activity_count": len(bot_window_activity),
        "trades_per_15m": trades_per_15m,
        "bot_frequency_score": bot_frequency_score,
        "crypto_activity_share": len(crypto_rows) / len(activity_rows) if activity_rows else 0.0,
        "recent_crypto_share": recent_crypto_share,
        "buy_count": sum(1 for row in activity_rows if row["side"] == "BUY"),
        "sell_count": sum(1 for row in activity_rows if row["side"] == "SELL"),
        "near_certain_buy_count": sum(1 for row in activity_rows if row["side"] == "BUY" and row["price"] is not None and row["price"] >= 0.90),
        "longshot_buy_count": sum(1 for row in activity_rows if row["side"] == "BUY" and row["price"] is not None and row["price"] <= 0.10),
        "position_value_usd": position_value,
        "visible_open_cash_pnl_usd": position_pnl,
        "daily_pnl_usd": pnl["daily_pnl_usd"],
        "weekly_pnl_usd": pnl["weekly_pnl_usd"],
        "monthly_pnl_usd": pnl["monthly_pnl_usd"],
        "quarterly_pnl_usd": pnl["quarterly_pnl_usd"],
        "all_pnl_usd": pnl["all_pnl_usd"],
        "period_metrics": period_metrics,
        "period_pnl_usd": _period_field_map(period_metrics, "pnl_usd"),
        "period_closed_win_rate": _period_field_map(period_metrics, "closed_win_rate"),
        "period_closed_return_pct": _period_field_map(period_metrics, "closed_return_pct"),
        "period_reconstructed_event_win_rate": _period_field_map(period_metrics, "reconstructed_event_win_rate"),
        "period_reconstructed_event_return_pct": _period_field_map(period_metrics, "reconstructed_event_return_pct"),
        "closed_win_rate": effective_closed_stats["win_rate"],
        "closed_return_pct": effective_closed_stats["return_pct"],
        "closed_profit_usd": effective_closed_stats["realized_pnl"],
        "closed_wins": effective_closed_stats["wins"],
        "closed_losses": effective_closed_stats["losses"],
        "closed_stats_source": "closed_positions" if (closed_stats["wins"] + closed_stats["losses"]) else "event_reconstruction",
        "current_closed_streak": closed_stats["current_streak"],
        "max_closed_loss_streak": closed_stats["max_loss_streak"],
        "avg_closed_loss_streak": closed_stats["avg_loss_streak"],
        "event_reconstructions": reconstructed_events,
        "reconstructed_event_count": len(reconstructed_events),
        "reconstructed_closed_win_rate": reconstructed_closed_stats["win_rate"],
        "reconstructed_closed_return_pct": reconstructed_closed_stats["return_pct"],
        "reconstructed_closed_profit_usd": reconstructed_closed_stats["realized_pnl"],
        "reconstructed_closed_wins": reconstructed_closed_stats["wins"],
        "reconstructed_closed_losses": reconstructed_closed_stats["losses"],
        "bot_like": len(bot_window_activity) >= config.min_bot_trades_per_15m,
        "active_in_last_5m": len(recent_activity) > 0,
        "active_crypto_in_last_5m": len(recent_crypto_rows) > 0,
        "active_crypto_most_of_last_hour": profile_classification["active_crypto_most_of_last_hour"],
        "profile_classification": profile_classification,
        "trading_style": profile_classification["trading_style"],
        "trading_style_detail": profile_classification["trading_style_detail"],
        "frequency_class": profile_classification["frequency_class"],
    }


def reconstruct_profile_events_from_activity(activity: list[dict[str, Any]], *, now_utc: datetime) -> list[dict[str, Any]]:
    activity_rows = [_activity_view(row) for row in activity]
    crypto_rows = [row for row in activity_rows if _is_crypto_updown(row)]
    return _reconstruct_profile_events(crypto_rows, now_utc=now_utc)


def grade_profile(metrics: dict[str, Any], *, config: ProfileSignalConfig) -> dict[str, Any]:
    score = 50.0
    reasons: list[str] = []

    daily = _to_float(metrics.get("daily_pnl_usd")) or 0.0
    weekly = _to_float(metrics.get("weekly_pnl_usd")) or 0.0
    monthly = _to_float(metrics.get("monthly_pnl_usd")) or 0.0
    quarterly = _to_float(metrics.get("quarterly_pnl_usd")) or 0.0
    all_pnl = _to_float(metrics.get("all_pnl_usd")) or 0.0
    period_metrics = metrics.get("period_metrics") if isinstance(metrics.get("period_metrics"), dict) else {}
    hourly = _period_value(period_metrics, "1h", "pnl_usd")
    three_hour = _period_value(period_metrics, "3h", "pnl_usd")
    six_hour = _period_value(period_metrics, "6h", "pnl_usd")
    twelve_hour = _period_value(period_metrics, "12h", "pnl_usd")
    score += 20.0 * math.tanh(daily / max(config.pnl_scale_daily, 1.0))
    score += 12.0 * math.tanh(hourly / max(config.pnl_scale_hourly, 1.0))
    score += 6.0 * math.tanh(three_hour / max(config.pnl_scale_three_hour, 1.0))
    score += 5.0 * math.tanh(six_hour / max(config.pnl_scale_six_hour, 1.0))
    score += 4.0 * math.tanh(twelve_hour / max(config.pnl_scale_twelve_hour, 1.0))
    score += 10.0 * math.tanh(weekly / max(config.pnl_scale_weekly, 1.0))
    score += 7.0 * math.tanh(monthly / max(config.pnl_scale_monthly, 1.0))
    score += 5.0 * math.tanh(quarterly / max(config.pnl_scale_quarterly, 1.0))
    score += 5.0 * math.tanh(all_pnl / max(config.pnl_scale_all, 1.0))
    for label, value in (("hourly_pnl", hourly), ("three_hour_pnl", three_hour), ("six_hour_pnl", six_hour), ("twelve_hour_pnl", twelve_hour)):
        if value:
            reasons.append(f"{label}={value:.2f}")
    if daily:
        reasons.append(f"daily_pnl={daily:.2f}")
    if weekly:
        reasons.append(f"weekly_pnl={weekly:.2f}")
    if monthly:
        reasons.append(f"monthly_pnl={monthly:.2f}")
    if quarterly:
        reasons.append(f"quarterly_pnl={quarterly:.2f}")
    if all_pnl:
        reasons.append(f"all_pnl={all_pnl:.2f}")

    win_rate = _to_float(metrics.get("closed_win_rate"))
    if win_rate is not None:
        score += max(-12.0, min(12.0, (win_rate - 0.50) * 30.0))
        reasons.append(f"closed_win_rate={win_rate:.3f}")

    return_pct = _to_float(metrics.get("closed_return_pct"))
    if return_pct is not None:
        score += 8.0 * math.tanh(return_pct / 75.0)
        reasons.append(f"closed_return_pct={return_pct:.2f}")

    for period, win_weight, return_weight in (
        ("1h", 5.0, 3.0),
        ("3h", 3.5, 2.0),
        ("6h", 3.0, 1.5),
        ("12h", 2.5, 1.0),
        ("1d", 4.0, 2.0),
        ("7d", 3.0, 1.5),
        ("30d", 2.0, 1.0),
        ("all_time", 1.5, 1.0),
    ):
        period_win_rate = _period_value(period_metrics, period, "closed_win_rate", default=None)
        period_return_pct = _period_value(period_metrics, period, "closed_return_pct", default=None)
        period_outcomes = int(_period_value(period_metrics, period, "closed_outcomes") or 0)
        if period_outcomes <= 0:
            continue
        if period_win_rate is not None:
            score += max(-win_weight, min(win_weight, (period_win_rate - 0.50) * win_weight * 2.0))
            reasons.append(f"{period}_closed_win_rate={period_win_rate:.3f}")
        if period_return_pct is not None:
            score += return_weight * math.tanh(period_return_pct / 75.0)
            reasons.append(f"{period}_closed_return_pct={period_return_pct:.2f}")

    bot_score = _to_float(metrics.get("bot_frequency_score")) or 0.0
    score += 10.0 * bot_score
    if metrics.get("bot_like"):
        reasons.append(f"bot_like_activity={metrics.get('bot_window_activity_count')}")

    recent_crypto_share = _to_float(metrics.get("recent_crypto_share")) or 0.0
    score += 6.0 * min(1.0, recent_crypto_share / max(config.min_recent_crypto_share, 1e-9))
    if metrics.get("active_crypto_in_last_5m"):
        reasons.append("active_crypto_last_5m")
    if metrics.get("active_crypto_most_of_last_hour"):
        reasons.append(f"active_crypto_events_1h={int(metrics.get('crypto_event_count_1h') or 0)}")

    if all_pnl <= -250_000 or monthly <= -100_000:
        score = min(score, 18.0)
        reasons.append("large_negative_profile_pnl_forces_inverse_grade")
    elif daily <= -25_000 and monthly < 0:
        score = min(score, 24.0)
        reasons.append("large_negative_daily_and_monthly_pnl")
    elif monthly <= -10_000 and all_pnl < 0:
        score = min(score, 36.0)
        reasons.append("negative_monthly_and_all_time_pnl_caps_grade")
    elif monthly <= -1_000 and all_pnl < 0 and daily < 0:
        score = min(score, 41.0)
        reasons.append("negative_recent_profile_pnl_caps_grade")
    elif monthly < 0 and all_pnl < 0:
        score = min(score, 69.0)
        reasons.append("negative_monthly_profile_pnl_prevents_a_grade")

    s_grade_blockers = _s_grade_blockers(
        metrics,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
        win_rate=win_rate,
        config=config,
    )
    s_threshold = float(config.grade_thresholds.get("S", 82.0))
    if score >= s_threshold and s_grade_blockers:
        score = min(score, max(0.0, s_threshold - 0.001))
        reasons.extend(s_grade_blockers)
    s_plus_threshold = float(config.grade_thresholds.get("S+", 96.0))
    s_plus_blockers = _s_plus_grade_blockers(
        metrics,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
        quarterly=quarterly,
        all_pnl=all_pnl,
        win_rate=win_rate,
        config=config,
    )
    if score >= s_plus_threshold and s_plus_blockers:
        score = min(score, max(s_threshold, s_plus_threshold - 0.001))
        reasons.extend(s_plus_blockers)

    score = max(0.0, min(100.0, score))
    grade = _grade_from_score(score, config.grade_thresholds)
    if grade == "S+" and _s_plus_plus_reliability_passes(metrics, score=score, config=config):
        grade = "S++"
        reasons.append("s_plus_plus_reliable_crypto_activity")
    polarity = "inverse" if grade in config.inverse_grades else "follow"
    if not metrics.get("bot_like"):
        reasons.append("frequency_below_bot_threshold")
    if not metrics.get("active_crypto_in_last_5m"):
        reasons.append("not_active_crypto_last_5m")
    return {"score": round(score, 3), "grade": grade, "polarity": polarity, "reasons": reasons}


def build_profile_signal_generator_scores(
    metrics: dict[str, Any],
    *,
    grade_payload: dict[str, Any] | None = None,
    config: ProfileSignalConfig | None = None,
) -> list[dict[str, Any]]:
    """Score which signal generators a profile can feed.

    The global profile grade answers "is this account good?". These generator
    scores answer "what kind of signal can we safely extract from it?".
    """

    config = config or ProfileSignalConfig()
    grade_payload = grade_payload or {}
    classification = metrics.get("profile_classification") if isinstance(metrics.get("profile_classification"), dict) else {}
    account_type = str(metrics.get("trading_style_detail") or metrics.get("trading_style") or "unknown")
    grade = str(grade_payload.get("grade") or "")
    quality = _unit_interval((_to_float(grade_payload.get("score")) or _to_float(metrics.get("score")) or 0.0) / 100.0)
    usage_eligible = bool(metrics.get("active_crypto_most_of_last_hour"))
    usage_blockers = [] if usage_eligible else ["profile_not_active_most_of_last_hour"]
    event_count_1h = int(metrics.get("crypto_event_count_1h") or 0)
    signal_count_1h = int(metrics.get("crypto_signal_count_1h") or 0)
    event_count_24h = max(int(metrics.get("crypto_event_count_24h") or 0), 1)
    event_style_counts = classification.get("event_style_counts_24h") if isinstance(classification.get("event_style_counts_24h"), dict) else {}
    outcome_events = int(event_style_counts.get("outcome_predictor") or 0)
    both_side_events = int(classification.get("both_side_event_count_24h") or 0)
    buy_sell_events = int(classification.get("buy_sell_event_count_24h") or 0)
    avg_trades_per_event = _to_float(classification.get("avg_crypto_trades_per_event_24h")) or 0.0
    buy_count = int(metrics.get("buy_count") or 0)
    sell_count = int(metrics.get("sell_count") or 0)
    action_count = max(buy_count + sell_count, 1)

    event_coverage = _unit_interval(event_count_1h / max(float(config.expected_crypto_events_per_hour), 1.0))
    signal_density = _unit_interval(signal_count_1h / 120.0)
    high_pulse_density = _unit_interval(avg_trades_per_event / 12.0)
    directional_purity = _unit_interval(outcome_events / event_count_24h)
    both_side_ratio = _unit_interval(both_side_events / event_count_24h)
    buy_sell_ratio = _unit_interval(buy_sell_events / event_count_24h)
    buy_sell_balance = _unit_interval(1.0 - abs(buy_count - sell_count) / action_count)
    low_scalp_noise = _unit_interval(1.0 - buy_sell_ratio)
    pnl_quality = _pnl_quality_from_metrics(metrics)
    reconstructed_quality = _reconstructed_event_quality_from_metrics(metrics)
    s_tier_quality = 1.0 if grade in {"S", "S+", "S++"} else 0.0

    rows: list[dict[str, Any]] = []

    if account_type == "outcome_predictor":
        components = {
            "quality": quality,
            "directional_purity": directional_purity,
            "event_coverage": event_coverage,
            "pnl_quality": pnl_quality,
            "reconstructed_event_quality": reconstructed_quality["quality"],
            "reconstructed_event_sample": reconstructed_quality["sample_quality"],
            "low_scalp_noise": low_scalp_noise,
        }
        score = _weighted_score(
            components,
            {
                "quality": 0.25,
                "directional_purity": 0.25,
                "event_coverage": 0.15,
                "pnl_quality": 0.15,
                "reconstructed_event_quality": 0.08,
                "reconstructed_event_sample": 0.02,
                "low_scalp_noise": 0.05,
            },
        )
        rows.append(
            _generator_score_row(
                "outcome_expectation",
                account_type=account_type,
                score=score,
                status="ready" if s_tier_quality and score >= 70.0 else "research_only",
                signal_role="direct_outcome_expectation",
                can_emit_live=True,
                usage_eligible=usage_eligible,
                usage_blockers=usage_blockers,
                components=components,
                requirements=["profile_style_outcome_predictor", "s_tier_grade"],
            )
        )

    if account_type == "hedger":
        hedge_components = {
            "quality": quality,
            "both_side_ratio": both_side_ratio,
            "event_coverage": event_coverage,
            "pulse_density": high_pulse_density,
            "pnl_quality": pnl_quality,
            "reconstructed_event_quality": reconstructed_quality["quality"],
            "reconstructed_event_sample": reconstructed_quality["sample_quality"],
        }
        hedge_score = _weighted_score(
            hedge_components,
            {
                "quality": 0.20,
                "both_side_ratio": 0.25,
                "event_coverage": 0.15,
                "pulse_density": 0.20,
                "pnl_quality": 0.10,
                "reconstructed_event_quality": 0.08,
                "reconstructed_event_sample": 0.02,
            },
        )
        rows.append(
            _generator_score_row(
                "hedge_proportion",
                account_type=account_type,
                score=hedge_score,
                status="requires_event_position_reconstruction",
                signal_role="yes_no_aggregate_position_ratio",
                can_emit_live=True,
                usage_eligible=usage_eligible,
                usage_blockers=usage_blockers,
                components=hedge_components,
                requirements=["event_level_order_reconstruction", "live_yes_no_position_aggregation"],
            )
        )
        outcome_components = {
            "quality": quality,
            "both_side_ratio": both_side_ratio,
            "event_coverage": event_coverage,
            "pnl_quality": pnl_quality,
            "reconstructed_event_quality": reconstructed_quality["quality"],
            "reconstructed_event_sample": reconstructed_quality["sample_quality"],
        }
        outcome_score = _weighted_score(
            outcome_components,
            {
                "quality": 0.20,
                "both_side_ratio": 0.20,
                "event_coverage": 0.20,
                "pnl_quality": 0.25,
                "reconstructed_event_quality": 0.12,
                "reconstructed_event_sample": 0.03,
            },
        )
        rows.append(
            _generator_score_row(
                "outcome_expectation",
                account_type=account_type,
                score=outcome_score,
                status="requires_event_position_reconstruction",
                signal_role="aggregate_position_skew_as_outcome_expectation",
                can_emit_live=True,
                usage_eligible=usage_eligible,
                usage_blockers=usage_blockers,
                components=outcome_components,
                requirements=["event_level_order_reconstruction", "aggregate_position_skew"],
            )
        )

    if account_type == "grid_buyer":
        band_components = {
            "quality": quality,
            "both_side_ratio": both_side_ratio,
            "signal_density": signal_density,
            "pulse_density": high_pulse_density,
            "pnl_quality": pnl_quality,
            "reconstructed_event_quality": reconstructed_quality["quality"],
            "reconstructed_event_sample": reconstructed_quality["sample_quality"],
        }
        band_score = _weighted_score(
            band_components,
            {
                "quality": 0.20,
                "both_side_ratio": 0.25,
                "signal_density": 0.20,
                "pulse_density": 0.15,
                "pnl_quality": 0.10,
                "reconstructed_event_quality": 0.08,
                "reconstructed_event_sample": 0.02,
            },
        )
        rows.append(
            _generator_score_row(
                "band_rebound",
                account_type=account_type,
                score=band_score,
                status="research_ready" if s_tier_quality and band_score >= 70.0 else "research_only",
                signal_role="option_price_rebound_resistance_band_confirmation",
                can_emit_live=True,
                usage_eligible=usage_eligible,
                usage_blockers=usage_blockers,
                components=band_components,
                requirements=["event_order_clusters_by_option_price", "future_rebound_validation"],
            )
        )
        hedge_components = {
            "quality": quality,
            "both_side_ratio": both_side_ratio,
            "signal_density": signal_density,
            "pulse_density": high_pulse_density,
            "pnl_quality": pnl_quality,
            "reconstructed_event_quality": reconstructed_quality["quality"],
            "reconstructed_event_sample": reconstructed_quality["sample_quality"],
        }
        hedge_score = _weighted_score(
            hedge_components,
            {
                "quality": 0.15,
                "both_side_ratio": 0.30,
                "signal_density": 0.15,
                "pulse_density": 0.20,
                "pnl_quality": 0.10,
                "reconstructed_event_quality": 0.08,
                "reconstructed_event_sample": 0.02,
            },
        )
        rows.append(
            _generator_score_row(
                "hedge_proportion",
                account_type=account_type,
                score=hedge_score,
                status="requires_event_position_reconstruction",
                signal_role="grid_inventory_yes_no_ratio_reference",
                can_emit_live=True,
                usage_eligible=usage_eligible,
                usage_blockers=usage_blockers,
                components=hedge_components,
                requirements=["event_level_order_reconstruction", "live_yes_no_position_aggregation"],
            )
        )

    if account_type == "scalping_trader":
        components = {
            "quality": quality,
            "buy_sell_event_ratio": buy_sell_ratio,
            "buy_sell_balance": buy_sell_balance,
            "signal_density": signal_density,
            "event_coverage": event_coverage,
            "reconstructed_event_quality": reconstructed_quality["quality"],
            "reconstructed_event_sample": reconstructed_quality["sample_quality"],
        }
        score = _weighted_score(
            components,
            {
                "quality": 0.20,
                "buy_sell_event_ratio": 0.25,
                "buy_sell_balance": 0.20,
                "signal_density": 0.15,
                "event_coverage": 0.10,
                "reconstructed_event_quality": 0.08,
                "reconstructed_event_sample": 0.02,
            },
        )
        rows.append(
            _generator_score_row(
                "volatility_liquidity",
                account_type=account_type,
                score=score,
                status="research_ready" if score >= 70.0 else "research_only",
                signal_role="short_horizon_oscillation_and_liquidity_metadata",
                can_emit_live=True,
                usage_eligible=usage_eligible,
                usage_blockers=usage_blockers,
                components=components,
                requirements=["spread_capture_backtest", "event_order_lifecycle_reconstruction"],
            )
        )

    return rows


def _generator_score_row(
    generator_id: str,
    *,
    account_type: str,
    score: float,
    status: str,
    signal_role: str,
    can_emit_live: bool,
    usage_eligible: bool,
    usage_blockers: list[str],
    components: dict[str, float],
    requirements: list[str],
) -> dict[str, Any]:
    return {
        "generator_id": generator_id,
        "account_type": account_type,
        "score": round(max(0.0, min(100.0, score)), 3),
        "status": status,
        "signal_role": signal_role,
        "can_emit_live": bool(can_emit_live),
        "usage_eligible": bool(usage_eligible),
        "usage_blockers": usage_blockers,
        "components": {key: round(float(value), 4) for key, value in components.items()},
        "requirements": requirements,
    }


def _weighted_score(components: dict[str, float], weights: dict[str, float]) -> float:
    total_weight = sum(max(0.0, float(weight)) for weight in weights.values())
    if total_weight <= 0:
        return 0.0
    return 100.0 * sum(_unit_interval(components.get(key, 0.0)) * float(weight) for key, weight in weights.items()) / total_weight


def _unit_interval(value: float | int | None) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


def _pnl_quality_from_metrics(metrics: dict[str, Any]) -> float:
    daily = _to_float(metrics.get("daily_pnl_usd")) or 0.0
    weekly = _to_float(metrics.get("weekly_pnl_usd")) or 0.0
    monthly = _to_float(metrics.get("monthly_pnl_usd")) or 0.0
    return _unit_interval(
        (
            (math.tanh(daily / 1_000.0) + 1.0) / 2.0
            + (math.tanh(weekly / 5_000.0) + 1.0) / 2.0
            + (math.tanh(monthly / 20_000.0) + 1.0) / 2.0
        )
        / 3.0
    )


def _reconstructed_event_quality_from_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    period_metrics = metrics.get("period_metrics") if isinstance(metrics.get("period_metrics"), dict) else {}
    win_rate = _to_float(metrics.get("reconstructed_closed_win_rate"))
    return_pct = _to_float(metrics.get("reconstructed_closed_return_pct"))
    event_count = int(metrics.get("reconstructed_closed_wins") or 0) + int(metrics.get("reconstructed_closed_losses") or 0)
    all_time = period_metrics.get("all_time") if isinstance(period_metrics.get("all_time"), dict) else {}
    if win_rate is None:
        win_rate = _to_float(all_time.get("reconstructed_event_win_rate"))
    if return_pct is None:
        return_pct = _to_float(all_time.get("reconstructed_event_return_pct"))
    if not event_count:
        event_count = int(all_time.get("reconstructed_event_outcomes") or 0)
    win_quality = _unit_interval(win_rate if win_rate is not None else 0.5)
    return_quality = _unit_interval((math.tanh((return_pct or 0.0) / 50.0) + 1.0) / 2.0)
    sample_quality = _unit_interval(event_count / 20.0)
    quality = _unit_interval((0.55 * win_quality) + (0.30 * return_quality) + (0.15 * sample_quality))
    return {
        "quality": quality,
        "win_quality": win_quality,
        "return_quality": return_quality,
        "sample_quality": sample_quality,
    }


def extract_recent_profile_signals(
    activity: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    grade: dict[str, Any],
    now_utc: datetime,
    config: ProfileSignalConfig,
    profile_metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    start = now_utc - timedelta(seconds=config.recent_signal_window_seconds)
    return extract_profile_signals(
        activity,
        profile=profile,
        grade=grade,
        profile_metrics=profile_metrics,
        now_utc=now_utc,
        config=config,
        start_utc=start,
    )


def extract_profile_signals(
    activity: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    grade: dict[str, Any],
    now_utc: datetime,
    config: ProfileSignalConfig,
    start_utc: datetime | None = None,
    profile_metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    profile_metrics = profile_metrics or {}
    classification = profile_metrics.get("profile_classification") if isinstance(profile_metrics.get("profile_classification"), dict) else {}
    for record in activity:
        row = _activity_view(record)
        if row["timestamp"] is None:
            continue
        if start_utc is not None and row["timestamp"] < start_utc:
            continue
        if row["timestamp"] > now_utc:
            continue
        if row["type"] not in {"TRADE", "BUY", "SELL"} and not row["side"]:
            continue
        if not _is_crypto_updown(row):
            continue
        effective = _effective_outcome(row["outcome"], row["side"], grade["polarity"])
        if not effective:
            continue
        rows.append(
            {
                "profile_name": profile.get("name"),
                "proxy_wallet": profile.get("proxyWallet"),
                "profile_grade": grade["grade"],
                "profile_score": grade["score"],
                "profile_polarity": grade["polarity"],
                "profile_trading_style": profile_metrics.get("trading_style") or classification.get("trading_style"),
                "profile_trading_style_detail": profile_metrics.get("trading_style_detail") or classification.get("trading_style_detail"),
                "profile_frequency_class": profile_metrics.get("frequency_class") or classification.get("frequency_class"),
                "signal_at_utc": row["timestamp"].isoformat(),
                "age_seconds": max(0.0, (now_utc - row["timestamp"]).total_seconds()),
                "event_slug": row["event_slug"] or row["slug"],
                "market_slug": row["slug"],
                "condition_id": row["condition_id"],
                "symbol": row["symbol"],
                "cadence": row["cadence"],
                "action_side": row["side"],
                "raw_outcome": row["outcome"],
                "effective_outcome": effective,
                "price": row["price"],
                "size": row["size"],
                "usdc_size": row["usdc_size"],
                "raw_signal": record,
            }
        )
    return sorted(rows, key=lambda item: item["signal_at_utc"], reverse=True)


def build_profile_signal_backtest(
    snapshots: list[dict[str, Any]],
    *,
    now_utc: datetime,
    config: ProfileSignalConfig,
    outcome_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replay the aggregation rules over the previous backtest window.

    This is a signal-quality backtest. It measures whether copied/aggregated
    profile directions matched settlement, using profile-reported trade prices
    as a conservative proxy for entry. It does not claim our local order would
    have filled at the same price.
    """

    start = now_utc - timedelta(seconds=config.backtest_lookback_seconds)
    all_signals: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for signal in snapshot.get("historical_signals") or []:
            ts = _parse_timestamp(signal.get("signal_at_utc"))
            if ts is not None and start <= ts <= now_utc:
                all_signals.append(dict(signal))
    if not all_signals:
        return {
            "schema_version": "crypto_options_profile_signal_backtest_v1",
            "status": "blocked",
            "blockers": ["no_historical_profile_signals_in_window"],
            "lookback_seconds": config.backtest_lookback_seconds,
            "trade_metrics": compute_trade_metrics(pd.DataFrame()),
            "trades": [],
        }

    resolver = outcome_resolver or resolve_crypto_updown_outcome
    outcome_cache: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    decisions = _replay_profile_signal_decisions(all_signals, start_utc=start, now_utc=now_utc, config=config)
    trade_rows: list[dict[str, Any]] = []
    for decision in decisions:
        event_slug = str(decision.get("event_slug") or "")
        if event_slug not in outcome_cache:
            outcome_cache[event_slug] = resolver(event_slug)
        resolved = outcome_cache[event_slug]
        if resolved.get("status") != "resolved":
            blockers.append(f"unresolved_event:{event_slug}:{resolved.get('status')}")
            continue
        outcome = str(decision.get("effective_outcome") or "")
        price = _decision_reference_price(decision)
        if price is None:
            blockers.append(f"missing_signal_price:{event_slug}:{outcome}")
            continue
        win = outcome == resolved.get("resolved_outcome")
        fee = _taker_fee_per_share(price)
        pnl = 5.0 * ((1.0 - price - fee) if win else -(price + fee))
        trade_rows.append(
            {
                "decision_at_utc": decision.get("decision_at_utc"),
                "event_slug": event_slug,
                "effective_outcome": outcome,
                "resolved_outcome": resolved.get("resolved_outcome"),
                "win": win,
                "price": price,
                "fee_per_share": fee,
                "pnl_net": pnl,
                "support_weight": decision.get("support_weight"),
                "conflict_weight": decision.get("conflict_weight"),
                "conflict_ratio": decision.get("conflict_ratio"),
                "single_trigger_profile_present": decision.get("single_trigger_profile_present"),
                "supporting_profile_count": decision.get("supporting_profile_count"),
                "supporting_profiles": decision.get("supporting_profiles"),
                "supporting_grade_counts": _supporting_grade_counts(decision),
                "best_supporting_grade": _best_supporting_grade(decision),
                "candidate_status": decision.get("status"),
                "sample_id": _sample_bucket(decision.get("decision_at_utc")),
            }
        )
    control_profiles = _select_control_profiles(snapshots, limit=config.control_profile_count)
    control_trade_rows = _profile_control_trade_rows(
        control_profiles,
        resolver=resolver,
        outcome_cache=outcome_cache,
        blockers=blockers,
        config=config,
    )
    metrics = compute_trade_metrics(pd.DataFrame(trade_rows), sample_column="sample_id")
    variants = _profile_signal_strategy_variants(
        trade_rows,
        control_trade_rows=control_trade_rows,
        control_profiles=control_profiles,
        config=config,
    )
    best_variant = _best_variant(variants)
    passed = bool(
        metrics.get("trade_count", 0) >= config.backtest_target_trade_count
        and (metrics.get("win_rate") or 0.0) >= config.backtest_target_win_rate
    )
    variant_passed = bool(best_variant and best_variant.get("target_passed"))
    status = "target_passed" if passed else "target_not_met"
    if not passed and variant_passed:
        status = "target_passed_exploratory_variant"
    if blockers and trade_rows:
        status = f"{status}_partial"
    elif blockers and not trade_rows:
        status = "blocked"
    return strict_jsonable(
        {
            "schema_version": "crypto_options_profile_signal_backtest_v1",
            "status": status,
            "lookback_seconds": config.backtest_lookback_seconds,
            "window_start_utc": start.isoformat(),
            "window_end_utc": now_utc.isoformat(),
            "target_trade_count": config.backtest_target_trade_count,
            "target_win_rate": config.backtest_target_win_rate,
            "historical_signal_count": len(all_signals),
            "decision_count": len(decisions),
            "settled_trade_count": len(trade_rows),
            "trade_metrics": metrics,
            "strategy_variants": variants,
            "best_variant": best_variant,
            "control_profiles": [_control_profile_summary(row) for row in control_profiles],
            "control_trade_count": len(control_trade_rows),
            "control_trades": control_trade_rows,
            "blockers": sorted(set(blockers)),
            "trades": trade_rows,
        }
    )


def resolve_crypto_updown_outcome(event_slug: str) -> dict[str, Any]:
    try:
        event = fetch_gamma_event_by_slug(event_slug)
    except Exception as exc:  # noqa: BLE001 - caller needs structured unresolved reason.
        return {"status": "fetch_error", "event_slug": event_slug, "error": f"{type(exc).__name__}:{exc}"}
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    for market in markets:
        outcomes = _json_list(market.get("outcomes"))
        prices = [_to_float(item) for item in _json_list(market.get("outcomePrices"))]
        if len(outcomes) >= 2 and len(prices) >= len(outcomes):
            for outcome, price in zip(outcomes, prices, strict=False):
                if price is not None and price >= 0.999:
                    return {
                        "status": "resolved",
                        "event_slug": event_slug,
                        "resolved_outcome": _normalize_outcome(outcome),
                        "source": "gamma_event_outcomePrices",
                    }
    return {"status": "unresolved", "event_slug": event_slug, "source": "gamma_event_outcomePrices"}


def aggregate_profile_signals(signals: list[dict[str, Any]], *, config: ProfileSignalConfig) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    ignored_grades = {str(grade).upper() for grade in config.ignored_signal_grades}
    filtered_signals = []
    for signal in signals:
        grade = str(signal.get("profile_grade") or "U").upper()
        if grade in ignored_grades:
            continue
        filtered_signals.append(signal)
    if config.dedupe_latest_signal_per_profile_event:
        filtered_signals = _latest_signal_per_profile_event(filtered_signals)
    for signal in filtered_signals:
        event_slug = str(signal.get("event_slug") or "")
        outcome = str(signal.get("effective_outcome") or "")
        if event_slug and outcome:
            grouped[(event_slug, outcome)].append(signal)
    candidates: list[dict[str, Any]] = []
    for (event_slug, outcome), rows in grouped.items():
        opposite = _opposite_outcome(outcome)
        conflict_rows = grouped.get((event_slug, opposite), []) if opposite else []
        support_weight = sum(_signal_weight(row, config) for row in rows)
        conflict_weight = sum(_signal_weight(row, config) for row in conflict_rows)
        support_profiles = sorted({str(row.get("profile_name") or row.get("proxy_wallet")) for row in rows})
        support_profile_set = set(support_profiles)
        conflict_profile_set = {str(row.get("profile_name") or row.get("proxy_wallet")) for row in conflict_rows}
        same_profile_conflicts = sorted(profile for profile in support_profile_set & conflict_profile_set if profile)
        same_profile_conflict_rows = [
            row
            for row in conflict_rows
            if str(row.get("profile_name") or row.get("proxy_wallet")) in same_profile_conflicts
        ]
        grade_counts = _profile_signal_grade_counts(rows)
        conflict_grade_counts = _profile_signal_grade_counts(conflict_rows)
        single_trigger = any(str(row.get("profile_grade")) in config.single_trigger_grades for row in rows)
        direct_support_profiles = {
            str(row.get("profile_name") or row.get("proxy_wallet"))
            for row in rows
            if str(row.get("profile_grade")) in config.single_trigger_grades
        }
        direct_conflict_profiles = {
            str(row.get("profile_name") or row.get("proxy_wallet"))
            for row in conflict_rows
            if str(row.get("profile_grade")) in config.single_trigger_grades
        }
        direct_support_profiles.discard("")
        direct_conflict_profiles.discard("")
        direct_strong_split_allowed = bool(
            direct_support_profiles
            and direct_conflict_profiles
            and direct_support_profiles.isdisjoint(direct_conflict_profiles)
            and not same_profile_conflicts
        )
        support_grades = {str(row.get("profile_grade") or "U").upper() for row in rows}
        confirmation_only = bool(support_grades) and support_grades <= {str(grade).upper() for grade in config.confirmation_grades}
        inverse_only = bool(support_grades) and support_grades <= {str(grade).upper() for grade in config.inverse_grades}
        conflict_ratio = conflict_weight / support_weight if support_weight else 0.0
        blockers: list[str] = []
        if conflict_ratio > config.max_conflict_ratio:
            if direct_strong_split_allowed and conflict_ratio <= config.split_side_max_conflict_ratio:
                pass
            else:
                blockers.append("opposite_signal_conflict")
        if same_profile_conflicts:
            blockers.append("same_profile_dual_side_conflict")
        if not single_trigger:
            allowed_without_direct = (
                (config.allow_confirmation_only_candidates and confirmation_only)
                or (config.allow_inverse_only_candidates and inverse_only)
            )
            if not allowed_without_direct:
                blockers.append("missing_direct_s_signal")
        if not single_trigger and len(support_profiles) < config.aggregate_min_profiles:
            blockers.append("insufficient_confirming_profiles")
        if not single_trigger and support_weight < config.aggregate_trigger_weight:
            blockers.append("insufficient_weight")
        status = "profile_signal_candidate" if not blockers else "needs_more_validation"
        candidates.append(
            {
                "event_slug": event_slug,
                "effective_outcome": outcome,
                "candidate_action": "BUY",
                "status": status,
                "support_weight": round(support_weight, 3),
                "conflict_weight": round(conflict_weight, 3),
                "conflict_ratio": round(conflict_ratio, 3),
                "single_trigger_profile_present": bool(single_trigger),
                "supporting_profile_count": len(support_profiles),
                "supporting_profiles": support_profiles,
                "supporting_grade_counts": grade_counts,
                "conflicting_grade_counts": conflict_grade_counts,
                "same_profile_dual_side_present": bool(same_profile_conflicts),
                "same_profile_opposite_count": len(same_profile_conflicts),
                "same_profile_opposite_profiles": same_profile_conflicts,
                "same_profile_opposite_weight": round(sum(_signal_weight(row, config) for row in same_profile_conflict_rows), 3),
                "supporting_signals": sorted(rows, key=lambda item: item.get("age_seconds") or 0.0),
                "conflicting_signals": sorted(conflict_rows, key=lambda item: item.get("age_seconds") or 0.0),
                "blockers": blockers,
                "orders_allowed": False,
            }
        )
    return sorted(
        candidates,
        key=lambda row: (
            0 if row["status"] == "profile_signal_candidate" else 1,
            -row["support_weight"],
            row["conflict_weight"],
        ),
    )


def _latest_signal_per_profile_event(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], tuple[tuple[float, int], dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for index, signal in enumerate(signals):
        event_slug = str(signal.get("event_slug") or "")
        profile = str(signal.get("profile_name") or signal.get("proxy_wallet") or "")
        if not event_slug or not profile:
            passthrough.append(signal)
            continue
        key = (event_slug, profile)
        sort_key = (_signal_timestamp_sort_value(signal), index)
        current = latest.get(key)
        if current is None or sort_key >= current[0]:
            latest[key] = (sort_key, signal)
    return [row for _, row in sorted(latest.values(), key=lambda item: item[0], reverse=True)] + passthrough


def _signal_timestamp_sort_value(signal: dict[str, Any]) -> float:
    parsed = _parse_timestamp(signal.get("signal_at_utc"))
    if parsed is not None:
        return parsed.timestamp()
    age_seconds = _to_float(signal.get("age_seconds"))
    if age_seconds is not None:
        return -age_seconds
    return 0.0


def _profile_signal_grade_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        grade = str(row.get("profile_grade") or "U").upper()
        counts[grade] = counts.get(grade, 0) + 1
    return dict(sorted(counts.items()))


def _replay_profile_signal_decisions(
    signals: list[dict[str, Any]],
    *,
    start_utc: datetime,
    now_utc: datetime,
    config: ProfileSignalConfig,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    tick = _floor_time(start_utc, seconds=300) + timedelta(seconds=300)
    while tick <= now_utc:
        window_start = tick - timedelta(seconds=config.recent_signal_window_seconds)
        window = [
            signal
            for signal in signals
            if (ts := _parse_timestamp(signal.get("signal_at_utc"))) is not None and window_start <= ts <= tick
        ]
        for candidate in aggregate_profile_signals(window, config=config):
            if candidate.get("status") != "profile_signal_candidate":
                continue
            key = (str(candidate.get("event_slug")), str(candidate.get("effective_outcome")))
            if key in seen:
                continue
            seen.add(key)
            decisions.append({**candidate, "decision_at_utc": tick.isoformat()})
        tick += timedelta(seconds=300)
    return decisions


def _select_control_profiles(snapshots: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if snapshot.get("status") != "complete":
            continue
        if snapshot.get("polarity") != "follow":
            continue
        signals = [
            signal
            for signal in snapshot.get("historical_signals") or []
            if _event_slug_starts(signal, ("btc-updown-5m", "eth-updown-5m"))
        ]
        if not signals:
            continue
        metrics = snapshot.get("metrics") or {}
        daily = _to_float(metrics.get("daily_pnl_usd")) or 0.0
        weekly = _to_float(metrics.get("weekly_pnl_usd")) or 0.0
        monthly = _to_float(metrics.get("monthly_pnl_usd")) or 0.0
        all_time = _to_float(metrics.get("all_pnl_usd")) or 0.0
        if max(daily, weekly, monthly, all_time) <= 0:
            continue
        control_score = (
            daily * 3.0
            + weekly * 1.0
            + monthly * 0.5
            + all_time * 0.1
            + (_to_float(snapshot.get("score")) or 0.0) * 100.0
            + (_to_float(metrics.get("bot_window_activity_count")) or 0.0) * 10.0
        )
        eligible.append(
            {
                **snapshot,
                "control_profile_key": _profile_identity_key(snapshot),
                "control_selection_score": control_score,
                "control_selection_reasons": {
                    "daily_pnl_weight": daily * 3.0,
                    "weekly_pnl_weight": weekly * 1.0,
                    "monthly_pnl_weight": monthly * 0.5,
                    "all_pnl_weight": all_time * 0.1,
                    "grade_score_weight": (_to_float(snapshot.get("score")) or 0.0) * 100.0,
                    "bot_activity_weight": (_to_float(metrics.get("bot_window_activity_count")) or 0.0) * 10.0,
                    "historical_btc_eth_5m_signal_count": len(signals),
                },
            }
        )
    return sorted(eligible, key=lambda row: row.get("control_selection_score") or 0.0, reverse=True)[: max(0, limit)]


def _profile_control_trade_rows(
    control_profiles: list[dict[str, Any]],
    *,
    resolver: Callable[[str], dict[str, Any]],
    outcome_cache: dict[str, dict[str, Any]],
    blockers: list[str],
    config: ProfileSignalConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in control_profiles:
        profile_name = str(((profile.get("profile") or {}).get("name")) or profile.get("control_profile_key") or "")
        profile_key = str(profile.get("control_profile_key") or profile_name)
        seen_events: set[str] = set()
        signals = sorted(
            [
                signal
                for signal in profile.get("historical_signals") or []
                if _event_slug_starts(signal, ("btc-updown-5m", "eth-updown-5m"))
            ],
            key=lambda signal: signal.get("signal_at_utc") or "",
        )
        for signal in signals:
            event_slug = str(signal.get("event_slug") or "")
            if not event_slug or event_slug in seen_events:
                continue
            seen_events.add(event_slug)
            if event_slug not in outcome_cache:
                outcome_cache[event_slug] = resolver(event_slug)
            resolved = outcome_cache[event_slug]
            if resolved.get("status") != "resolved":
                blockers.append(f"control_unresolved_event:{profile_name}:{event_slug}:{resolved.get('status')}")
                continue
            outcome = str(signal.get("effective_outcome") or "")
            price = _to_float(signal.get("price"))
            if price is None:
                blockers.append(f"control_missing_signal_price:{profile_name}:{event_slug}:{outcome}")
                continue
            win = outcome == resolved.get("resolved_outcome")
            fee = _taker_fee_per_share(price)
            rows.append(
                {
                    "decision_at_utc": signal.get("signal_at_utc"),
                    "event_slug": event_slug,
                    "effective_outcome": outcome,
                    "resolved_outcome": resolved.get("resolved_outcome"),
                    "win": win,
                    "price": price,
                    "fee_per_share": fee,
                    "pnl_net": 5.0 * ((1.0 - price - fee) if win else -(price + fee)),
                    "support_weight": _signal_weight(signal, config),
                    "conflict_weight": 0.0,
                    "supporting_profile_count": 1,
                    "supporting_profiles": [profile_name],
                    "candidate_status": "profile_control_first_signal",
                    "control_profile_name": profile_name,
                    "control_profile_key": profile_key,
                    "sample_id": _sample_bucket(signal.get("signal_at_utc")),
                }
            )
    return rows


def _control_profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    metrics = profile.get("metrics") or {}
    identity = profile.get("profile") or {}
    return {
        "name": identity.get("name"),
        "proxy_wallet": identity.get("proxy_wallet"),
        "grade": profile.get("grade"),
        "score": profile.get("score"),
        "control_profile_key": profile.get("control_profile_key"),
        "control_selection_score": profile.get("control_selection_score"),
        "daily_pnl_usd": metrics.get("daily_pnl_usd"),
        "weekly_pnl_usd": metrics.get("weekly_pnl_usd"),
        "monthly_pnl_usd": metrics.get("monthly_pnl_usd"),
        "all_pnl_usd": metrics.get("all_pnl_usd"),
        "bot_window_activity_count": metrics.get("bot_window_activity_count"),
        "recent_crypto_activity_count_5m": metrics.get("recent_crypto_activity_count_5m"),
        "selection_reasons": profile.get("control_selection_reasons") or {},
    }


def _profile_signal_strategy_variants(
    trade_rows: list[dict[str, Any]],
    *,
    control_trade_rows: list[dict[str, Any]] | None = None,
    control_profiles: list[dict[str, Any]] | None = None,
    config: ProfileSignalConfig,
) -> list[dict[str, Any]]:
    bad_profiles = _exploratory_bad_profiles(trade_rows)
    specs = [
        {
            "strategy_id": "raw_profile_aggregation",
            "description": "All profile aggregation candidates from the configured signal policy.",
            "exploratory_in_sample_profile_filter": False,
            "predicate": lambda row: True,
        },
        {
            "strategy_id": "btc_5m_only",
            "description": "BTC 5m candidates only; excludes ETH/SOL/XRP and 15m windows.",
            "exploratory_in_sample_profile_filter": False,
            "predicate": lambda row: str(row.get("event_slug") or "").startswith("btc-updown-5m"),
        },
        {
            "strategy_id": "btc_eth_5m_mid_high",
            "description": "BTC/ETH 5m candidates with profile-signal reference price between 45c and 99c.",
            "exploratory_in_sample_profile_filter": False,
            "predicate": lambda row: (
                _event_slug_starts(row, ("btc-updown-5m", "eth-updown-5m"))
                and 0.45 <= float(row.get("price") or -1.0) <= 0.99
            ),
        },
        {
            "strategy_id": "btc_eth_5m_mid_high_quality_overlay",
            "description": (
                "BTC/ETH 5m 45c-99c candidates after excluding all-bad support clusters. "
                "The bad-profile list is fitted on this same replay window and is for research/tuning only."
            ),
            "exploratory_in_sample_profile_filter": True,
            "bad_profiles": sorted(bad_profiles),
            "predicate": lambda row: (
                _event_slug_starts(row, ("btc-updown-5m", "eth-updown-5m"))
                and 0.45 <= float(row.get("price") or -1.0) <= 0.99
                and not _all_supporting_profiles_bad(row, bad_profiles)
            ),
        },
        {
            "strategy_id": "btc_eth_5m_quality_favorite",
            "description": (
                "BTC/ETH 5m favorite-side profile signals: 55c-90c reference price, low opposite-signal conflict, "
                "and at least one S/A supporting profile. This prioritizes profile quality and market-implied likelihood "
                "over raw signal count."
            ),
            "exploratory_in_sample_profile_filter": False,
            "predicate": lambda row: (
                _event_slug_starts(row, ("btc-updown-5m", "eth-updown-5m"))
                and 0.55 <= float(row.get("price") or -1.0) <= 0.90
                and float(row.get("conflict_ratio") or 0.0) <= 0.25
                and _has_supporting_grade(row, ("S", "A"))
            ),
        },
    ]
    for profile in control_profiles or []:
        name = str(((profile.get("profile") or {}).get("name")) or "")
        profile_key = str(profile.get("control_profile_key") or name)
        specs.append(
            {
                "strategy_id": f"control_follow_{_strategy_slug(profile_key)}_first_signal",
                "description": (
                    f"Control baseline: follow {name or profile_key} on the first BTC/ETH 5m signal per event, "
                    "without ensemble confirmation."
                ),
                "exploratory_in_sample_profile_filter": False,
                "control_profile": _control_profile_summary(profile),
                "row_source": "control",
                "predicate": lambda row, expected=profile_key: row.get("control_profile_key") == expected,
            }
        )
    variants: list[dict[str, Any]] = []
    control_trade_rows = control_trade_rows or []
    for spec in specs:
        source_rows = control_trade_rows if spec.get("row_source") == "control" else trade_rows
        rows = [row for row in source_rows if spec["predicate"](row)]
        metrics = compute_trade_metrics(pd.DataFrame(rows), sample_column="sample_id")
        target_passed = bool(
            metrics.get("trade_count", 0) >= config.backtest_target_trade_count
            and (metrics.get("win_rate") or 0.0) >= config.backtest_target_win_rate
        )
        exploratory = bool(spec.get("exploratory_in_sample_profile_filter"))
        variants.append(
            strict_jsonable(
                {
                    "strategy_id": spec["strategy_id"],
                    "description": spec["description"],
                    "exploratory_in_sample_profile_filter": exploratory,
                    "bad_profiles": spec.get("bad_profiles", []),
                    "control_profile": spec.get("control_profile"),
                    "target_passed": target_passed,
                    "trade_metrics": metrics,
                    "economics": _variant_economics(rows),
                    "live_readiness": _variant_live_readiness(rows, metrics, config=config, exploratory=exploratory),
                    "trades": rows,
                }
            )
        )
    return variants


def _best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not variants:
        return None

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        metrics = row.get("trade_metrics") or {}
        readiness = row.get("live_readiness") or {}
        return (
            0 if readiness.get("status") == "ready_for_supervised_micro_test" else 1,
            0 if row.get("target_passed") else 1,
            1 if row.get("exploratory_in_sample_profile_filter") else 0,
            -(metrics.get("win_rate") or 0.0),
            -(metrics.get("trade_count") or 0),
            -(metrics.get("return_sum") or 0.0),
        )

    best = sorted(variants, key=key)[0]
    return {
        "strategy_id": best.get("strategy_id"),
        "target_passed": best.get("target_passed"),
        "exploratory_in_sample_profile_filter": best.get("exploratory_in_sample_profile_filter"),
        "trade_count": (best.get("trade_metrics") or {}).get("trade_count"),
        "win_rate": (best.get("trade_metrics") or {}).get("win_rate"),
        "return_sum": (best.get("trade_metrics") or {}).get("return_sum"),
        "max_sequential_losses": (best.get("trade_metrics") or {}).get("max_sequential_losses"),
        "economics": best.get("economics") or {},
        "live_readiness": best.get("live_readiness") or {},
        "bad_profiles": best.get("bad_profiles") or [],
    }


def _variant_economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "entry_price": {},
            "cost": {},
            "breakeven": {},
            "slippage_sensitivity": [],
            "price_buckets": [],
        }
    prices = [_to_float(row.get("price")) or 0.0 for row in rows]
    costs = [5.0 * (price + _taker_fee_per_share(price)) for price in prices]
    pnl_values = [_to_float(row.get("pnl_net")) or 0.0 for row in rows]
    frame = pd.DataFrame({"price": prices, "cost": costs, "pnl": pnl_values})
    return strict_jsonable(
        {
            "entry_price": {
                "avg": _series_float(frame["price"].mean()),
                "min": _series_float(frame["price"].min()),
                "max": _series_float(frame["price"].max()),
                "median": _series_float(frame["price"].median()),
            },
            "cost": {
                "total_estimated_cost_usd": _series_float(frame["cost"].sum()),
                "avg_ticket_cost_usd": _series_float(frame["cost"].mean()),
                "max_ticket_cost_usd": _series_float(frame["cost"].max()),
                "roi_on_cost": _series_float(frame["pnl"].sum() / frame["cost"].sum()) if frame["cost"].sum() else None,
            },
            "breakeven": {
                "avg_win_probability_required_before_slippage": _series_float(
                    pd.Series([price + _taker_fee_per_share(price) for price in prices]).mean()
                ),
                "max_win_probability_required_before_slippage": _series_float(
                    pd.Series([price + _taker_fee_per_share(price) for price in prices]).max()
                ),
            },
            "slippage_sensitivity": [_slippage_scenario(rows, cents) for cents in (0, 1, 2, 3, 5, 10)],
            "price_buckets": _price_bucket_metrics(rows),
        }
    )


def _variant_live_readiness(
    rows: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    config: ProfileSignalConfig,
    exploratory: bool,
) -> dict[str, Any]:
    economics = _variant_economics(rows)
    sensitivity = {row["adverse_slippage_cents"]: row for row in economics.get("slippage_sensitivity") or []}
    supervised_slippage = sensitivity.get(config.backtest_supervised_slippage_cents)
    win_rate = _to_float(metrics.get("win_rate")) or 0.0
    avg_breakeven = _to_float((economics.get("breakeven") or {}).get("avg_win_probability_required_before_slippage"))
    edge_over_breakeven = win_rate - avg_breakeven if avg_breakeven is not None else None
    strict_win_rate_ok = win_rate >= config.backtest_target_win_rate
    economic_edge_ok = bool(
        win_rate >= config.backtest_min_supervised_win_rate
        and edge_over_breakeven is not None
        and edge_over_breakeven >= config.backtest_min_edge_over_breakeven
    )
    failed: list[str] = []
    if int(metrics.get("trade_count") or 0) < config.backtest_target_trade_count:
        failed.append("trade_count_below_target")
    if not strict_win_rate_ok and not economic_edge_ok:
        failed.append("win_rate_below_target_and_no_economic_edge")
    if (metrics.get("return_sum") or 0.0) <= 0:
        failed.append("base_pnl_not_positive")
    if int(metrics.get("max_sequential_losses") or 0) > 3:
        failed.append("max_loss_streak_above_3")
    if not supervised_slippage or (supervised_slippage.get("return_sum") or 0.0) <= 0:
        failed.append(f"not_profitable_after_{config.backtest_supervised_slippage_cents}c_adverse_slippage")
    if exploratory:
        failed.append("exploratory_in_sample_profile_filter_requires_forward_validation")
    readiness_mode = "strict_win_rate" if strict_win_rate_ok else "economic_edge"
    if failed:
        readiness_mode = "not_ready"
    return {
        "status": "ready_for_supervised_micro_test" if not failed else "not_ready",
        "failed_gates": failed,
        "readiness_mode": readiness_mode,
        "strict_win_rate_target_met": strict_win_rate_ok,
        "economic_edge_target_met": economic_edge_ok,
        "win_rate": win_rate,
        "avg_breakeven_win_probability": avg_breakeven,
        "edge_over_breakeven": edge_over_breakeven,
        "supervised_slippage_cents": config.backtest_supervised_slippage_cents,
        "supervised_slippage_return_sum": (supervised_slippage or {}).get("return_sum"),
        "requires_supervision": True,
        "production_unsupervised_allowed": False,
        "budget_cap_usd": 50.0,
        "max_ticket_shares": 5.0,
        "max_trade_count": 12,
        "hard_stop_loss_usd": 5.0,
        "notes": [
            "Readiness uses signal-copy modeled economics, not guaranteed executable fills.",
            "A sub-70% win rate can pass supervised micro-test readiness only when fee-adjusted edge and adverse-slippage economics remain positive.",
            "Any live test must record signal price, JIT quote, realized fill, latency, and settlement.",
        ],
    }


def _slippage_scenario(rows: list[dict[str, Any]], cents: int) -> dict[str, Any]:
    pnl_values: list[float] = []
    costs: list[float] = []
    for row in rows:
        price = min(0.99, (_to_float(row.get("price")) or 0.0) + float(cents) / 100.0)
        fee = _taker_fee_per_share(price)
        costs.append(5.0 * (price + fee))
        pnl_values.append(5.0 * ((1.0 - price - fee) if row.get("win") else -(price + fee)))
    frame = pd.DataFrame({"pnl_net": pnl_values, "cost": costs, "win": [bool(row.get("win")) for row in rows]})
    metrics = compute_trade_metrics(frame)
    return {
        "adverse_slippage_cents": cents,
        "trade_count": metrics.get("trade_count"),
        "win_rate": metrics.get("win_rate"),
        "return_sum": metrics.get("return_sum"),
        "return_avg": metrics.get("return_avg"),
        "roi_on_cost": _series_float(frame["pnl_net"].sum() / frame["cost"].sum()) if frame["cost"].sum() else None,
        "max_sequential_losses": metrics.get("max_sequential_losses"),
    }


def _price_bucket_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [
        ("0.10-0.25", 0.10, 0.25),
        ("0.25-0.40", 0.25, 0.40),
        ("0.40-0.55", 0.40, 0.55),
        ("0.55-0.70", 0.55, 0.70),
        ("0.70-0.90", 0.70, 0.90),
    ]
    output: list[dict[str, Any]] = []
    for label, low, high in buckets:
        subset = [row for row in rows if low <= (_to_float(row.get("price")) or -1.0) < high]
        if not subset:
            continue
        metrics = compute_trade_metrics(pd.DataFrame(subset), sample_column="sample_id")
        output.append(
            {
                "bucket": label,
                "trade_count": metrics.get("trade_count"),
                "win_rate": metrics.get("win_rate"),
                "return_sum": metrics.get("return_sum"),
                "avg_entry_price": _series_float(pd.Series([_to_float(row.get("price")) for row in subset]).mean()),
                "max_sequential_losses": metrics.get("max_sequential_losses"),
            }
        )
    return output


def _exploratory_bad_profiles(trade_rows: list[dict[str, Any]]) -> set[str]:
    stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0.0, "wins": 0.0, "pnl": 0.0})
    for row in trade_rows:
        profiles = [str(item) for item in row.get("supporting_profiles") or [] if item]
        if not profiles:
            continue
        share = float(row.get("pnl_net") or 0.0) / len(profiles)
        for profile in profiles:
            stats[profile]["n"] += 1.0
            stats[profile]["wins"] += 1.0 if row.get("win") else 0.0
            stats[profile]["pnl"] += share
    bad: set[str] = set()
    for profile, row in stats.items():
        n = row["n"]
        win_rate = row["wins"] / n if n else 0.0
        if n >= 2 and (win_rate < 0.50 or row["pnl"] < 0.0):
            bad.add(profile)
    return bad


def _event_slug_starts(row: dict[str, Any], prefixes: tuple[str, ...]) -> bool:
    slug = str(row.get("event_slug") or "")
    return any(slug.startswith(prefix) for prefix in prefixes)


def _all_supporting_profiles_bad(row: dict[str, Any], bad_profiles: set[str]) -> bool:
    profiles = [str(item) for item in row.get("supporting_profiles") or [] if item]
    return bool(profiles) and all(profile in bad_profiles for profile in profiles)


def _supporting_grade_counts(row: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in row.get("supporting_signals") or []:
        grade = str(signal.get("profile_grade") or "U")
        counts[grade] = counts.get(grade, 0) + 1
    return dict(sorted(counts.items()))


def _best_supporting_grade(row: dict[str, Any]) -> str | None:
    grade_order = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, "E": 0}
    grades = [str(signal.get("profile_grade") or "U") for signal in row.get("supporting_signals") or []]
    known = [grade for grade in grades if grade in grade_order]
    if not known:
        return None
    return sorted(known, key=lambda grade: grade_order[grade], reverse=True)[0]


def _has_supporting_grade(row: dict[str, Any], grades: tuple[str, ...]) -> bool:
    wanted = set(grades)
    counts = row.get("supporting_grade_counts")
    if isinstance(counts, dict):
        return any(int(counts.get(grade) or 0) > 0 for grade in wanted)
    return any(str(signal.get("profile_grade") or "") in wanted for signal in row.get("supporting_signals") or [])


def write_profile_signal_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = (
        Path(output_dir)
        if output_dir
        else resolve_shared_root() / "artifacts" / "crypto-options-research" / "profile-signals" / day
    )
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_profile_signal_report_{stamp}.json"
    md_path = root / f"crypto_options_profile_signal_report_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_profile_signal_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_profile_signal_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Profile Signal Report",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Profile seeds: `{payload.get('profile_seed_count')}`",
        f"- Active signals: `{payload.get('active_signal_count')}`",
        f"- Aggregated candidates: `{payload.get('candidate_count')}`",
        f"- Orders allowed: `{(payload.get('boundary') or {}).get('orders_allowed')}`",
    ]
    backtest = payload.get("backtest") or {}
    best = backtest.get("best_variant") or {}
    if backtest:
        lines.extend(
            [
                "## Backtest",
                "",
                f"- Status: `{backtest.get('status')}`",
                f"- Base trades: `{(backtest.get('trade_metrics') or {}).get('trade_count')}`",
                f"- Base win rate: `{_fmt((backtest.get('trade_metrics') or {}).get('win_rate'))}`",
                f"- Best variant: `{best.get('strategy_id')}`",
                f"- Best variant trades: `{best.get('trade_count')}`",
                f"- Best variant win rate: `{_fmt(best.get('win_rate'))}`",
                f"- Best variant ROI on cost: `{_fmt(((best.get('economics') or {}).get('cost') or {}).get('roi_on_cost'))}`",
                f"- Best variant 2c slippage PnL: `{_fmt(_slippage_return_sum(best, 2))}`",
                f"- Best variant live readiness: `{((best.get('live_readiness') or {}).get('status'))}`",
                f"- Best variant exploratory/in-sample: `{best.get('exploratory_in_sample_profile_filter')}`",
                "",
                "## Backtest Variants",
                "",
            ]
        )
        for variant in backtest.get("strategy_variants") or []:
            metrics = variant.get("trade_metrics") or {}
            economics = variant.get("economics") or {}
            readiness = variant.get("live_readiness") or {}
            lines.append(
                "- `{strategy}` passed=`{passed}` readiness=`{readiness}` exploratory=`{exploratory}` trades=`{trades}` win=`{win}` pnl=`{pnl}` roi=`{roi}` 2c_pnl=`{slip}` max_loss_streak=`{losses}`".format(
                    strategy=variant.get("strategy_id"),
                    passed=variant.get("target_passed"),
                    readiness=readiness.get("status"),
                    exploratory=variant.get("exploratory_in_sample_profile_filter"),
                    trades=metrics.get("trade_count"),
                    win=_fmt(metrics.get("win_rate")),
                    pnl=_fmt(metrics.get("return_sum")),
                    roi=_fmt(((economics.get("cost") or {}).get("roi_on_cost"))),
                    slip=_fmt(_slippage_return_sum(variant, 2)),
                    losses=metrics.get("max_sequential_losses"),
                )
            )
        lines.extend(["", "## Aggregated Candidates", ""])
    for candidate in payload.get("aggregated_candidates") or []:
        lines.append(
            "- `{event}` `{outcome}` status=`{status}` weight=`{weight}` conflict=`{conflict}` profiles=`{profiles}` blockers=`{blockers}`".format(
                event=candidate.get("event_slug"),
                outcome=candidate.get("effective_outcome"),
                status=candidate.get("status"),
                weight=candidate.get("support_weight"),
                conflict=candidate.get("conflict_weight"),
                profiles=",".join(candidate.get("supporting_profiles") or []),
                blockers=",".join(candidate.get("blockers") or []),
            )
        )
    lines.extend(["", "## Profile Grades", ""])
    for profile in payload.get("profiles") or []:
        name = ((profile.get("profile") or {}).get("name") or (profile.get("profile_ref") or {}).get("raw_ref"))
        metrics = profile.get("metrics") or {}
        lines.append(
            "- `{name}` grade=`{grade}` score=`{score}` polarity=`{polarity}` recent_crypto_5m=`{recent}` daily_pnl=`{daily}` weekly_pnl=`{weekly}` monthly_pnl=`{monthly}` all_pnl=`{all_pnl}`".format(
                name=name,
                grade=profile.get("grade"),
                score=profile.get("score"),
                polarity=profile.get("polarity"),
                recent=metrics.get("recent_crypto_activity_count_5m"),
                daily=_fmt(metrics.get("daily_pnl_usd")),
                weekly=_fmt(metrics.get("weekly_pnl_usd")),
                monthly=_fmt(metrics.get("monthly_pnl_usd")),
                all_pnl=_fmt(metrics.get("all_pnl_usd")),
            )
        )
    if payload.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend([f"- `{item}`" for item in payload.get("blockers") or []])
    lines.extend(["", "## Boundary", "", (payload.get("boundary") or {}).get("description", "")])
    return "\n".join(lines).rstrip() + "\n"


def _active_signals(
    snapshots: list[dict[str, Any]],
    *,
    active_event_slugs: list[str],
    now_utc: datetime,
    config: ProfileSignalConfig,
) -> list[dict[str, Any]]:
    active_set = {str(item) for item in active_event_slugs if item}
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for signal in snapshot.get("signals") or []:
            if signal.get("age_seconds") is not None and float(signal["age_seconds"]) > config.recent_signal_window_seconds:
                continue
            if active_set and signal.get("event_slug") not in active_set and signal.get("market_slug") not in active_set:
                continue
            rows.append(signal)
    return sorted(rows, key=lambda item: item.get("signal_at_utc") or "", reverse=True)


def _top_holder_discovery(
    condition_ids: list[str],
    *,
    enabled: bool,
    limit: int,
    fetcher: Callable[[list[str]], list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "condition_ids": condition_ids, "holder_payload_count": 0, "profile_refs": []}
    cleaned = [condition_id for condition_id in condition_ids if _is_condition_id(condition_id)]
    if not cleaned:
        return {
            "enabled": True,
            "condition_ids": condition_ids,
            "holder_payload_count": 0,
            "profile_refs": [],
            "blockers": ["top_holder_discovery_requires_condition_ids"],
        }
    payloads = fetcher(cleaned) if fetcher else fetch_top_holders_for_markets(cleaned, limit=limit)
    refs: list[dict[str, Any]] = []
    for token_payload in payloads:
        for holder in token_payload.get("holders") or []:
            if not isinstance(holder, dict):
                continue
            wallet = holder.get("proxyWallet")
            if wallet:
                ref = normalize_profile_ref(str(wallet), source="top_holder_api")
                ref["top_holder_name"] = holder.get("name") or holder.get("pseudonym")
                ref["top_holder_amount"] = _to_float(holder.get("amount"))
                ref["top_holder_token"] = token_payload.get("token")
                ref["top_holder_outcome_index"] = holder.get("outcomeIndex")
                refs.append(ref)
    return {
        "enabled": True,
        "condition_ids": cleaned,
        "holder_payload_count": len(payloads),
        "profile_refs": _dedupe_registry(refs),
        "raw_payloads": payloads,
    }


def _scraped_profile_discovery(
    event_slugs: list[str],
    *,
    enabled: bool,
    limit: int,
    fetcher: Callable[[list[str]], list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "event_slugs": event_slugs, "scraped_row_count": 0, "profile_refs": []}
    cleaned = [str(event_slug).strip() for event_slug in event_slugs if str(event_slug).strip()]
    if not cleaned:
        return {
            "enabled": True,
            "event_slugs": event_slugs,
            "scraped_row_count": 0,
            "profile_refs": [],
            "blockers": ["scraped_profile_discovery_requires_event_slugs"],
        }
    try:
        rows = fetcher(cleaned) if fetcher else scrape_market_profile_refs(cleaned, limit=limit)
    except Exception as exc:  # noqa: BLE001 - scraping is a fallback; do not block API discovery.
        return {
            "enabled": True,
            "event_slugs": cleaned,
            "scraped_row_count": 0,
            "profile_refs": [],
            "blockers": [f"scraped_profile_discovery_failed:{type(exc).__name__}:{exc}"],
        }
    refs: list[dict[str, Any]] = []
    blockers: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_ref = str(row.get("raw_ref") or "").strip()
        if not raw_ref:
            if row.get("blocker"):
                blockers.append(f"scraped_profile_unusable:{row.get('profile_label')}:{row.get('blocker')}")
            continue
        ref = normalize_profile_ref(raw_ref, source=str(row.get("source") or "market_page_scrape"))
        ref["scraped_profile_label"] = row.get("profile_label")
        ref["scraped_side"] = row.get("side")
        ref["scraped_shares"] = _to_float(row.get("shares"))
        ref["scraped_rank"] = row.get("rank")
        ref["scraped_event_slug"] = row.get("event_slug")
        ref["scraped_profile_href"] = row.get("profile_href")
        refs.append(ref)
    return {
        "enabled": True,
        "event_slugs": cleaned,
        "scraped_row_count": len(rows),
        "profile_refs": _dedupe_registry(refs),
        "blockers": sorted(set(blockers)),
        "raw_rows": rows,
    }


def _activity_view(record: dict[str, Any]) -> dict[str, Any]:
    title = str(record.get("title") or "")
    slug = str(record.get("slug") or "")
    event_slug = str(record.get("eventSlug") or record.get("event_slug") or "")
    side = str(record.get("side") or record.get("action_side") or record.get("type") or "").upper()
    if side not in {"BUY", "SELL"}:
        side = str(record.get("side") or "").upper()
    outcome = record.get("outcome")
    if outcome in (None, ""):
        outcome = record.get("effective_outcome") or record.get("raw_outcome")
    return {
        "timestamp": _parse_timestamp(record.get("timestamp") or record.get("signal_at_utc") or record.get("createdAt") or record.get("created_at")),
        "type": str(record.get("type") or "").upper(),
        "side": side,
        "outcome": _normalize_outcome(outcome),
        "price": _to_float(record.get("price")),
        "size": _to_float(record.get("size")),
        "usdc_size": _to_float(record.get("usdcSize") or record.get("usdc_size")),
        "condition_id": record.get("conditionId") or record.get("condition_id"),
        "token_id": record.get("tokenId") or record.get("token_id") or record.get("asset"),
        "transaction_hash": record.get("transactionHash") or record.get("transaction_hash"),
        "slug": slug,
        "event_slug": event_slug,
        "title": title,
        "symbol": _symbol_from_text(f"{title} {slug} {event_slug}"),
        "cadence": _cadence_from_text(f"{title} {slug} {event_slug}"),
    }


def _closed_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _parse_timestamp(record.get("timestamp") or record.get("closedAt") or record.get("endDate")),
        "realized_pnl": _to_float(record.get("realizedPnl") or record.get("realized_pnl")),
        "cost": (_to_float(record.get("totalBought")) or 0.0) * (_to_float(record.get("avgPrice")) or 0.0),
    }


def _closed_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_rows = [row for row in rows if row.get("realized_pnl") is not None and abs(float(row["realized_pnl"])) > 1e-9]
    wins = [row for row in pnl_rows if float(row["realized_pnl"]) > 0]
    losses = [row for row in pnl_rows if float(row["realized_pnl"]) < 0]
    total_pnl = sum(float(row["realized_pnl"]) for row in pnl_rows)
    total_cost = sum(float(row.get("cost") or 0.0) for row in rows)
    streak, max_loss, avg_loss = _loss_streaks(pnl_rows)
    return {
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnl_rows) if pnl_rows else None,
        "realized_pnl": total_pnl,
        "return_pct": total_pnl / total_cost * 100 if total_cost else None,
        "current_streak": streak,
        "max_loss_streak": max_loss,
        "avg_loss_streak": avg_loss,
    }


def _closed_stats_since(rows: list[dict[str, Any]], start: datetime | None) -> dict[str, Any]:
    if start is None:
        return _closed_stats(rows)
    return _closed_stats([row for row in rows if row.get("timestamp") and row["timestamp"] >= start])


def _event_reconstruction_side(value: Any) -> str | None:
    outcome = _normalize_outcome(value)
    if outcome in {"Up", "Yes"}:
        return "Up"
    if outcome in {"Down", "No"}:
        return "Down"
    return None


def _reconstruct_profile_events(crypto_rows: list[dict[str, Any]], *, now_utc: datetime) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in crypto_rows:
        event_key = str(row.get("condition_id") or row.get("event_slug") or row.get("slug") or "").strip()
        if not event_key:
            continue
        grouped[event_key].append(row)

    events: list[dict[str, Any]] = []
    for event_key, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row.get("timestamp") or datetime.fromtimestamp(0, tz=timezone.utc))
        side_state: dict[str, dict[str, float]] = {
            "Up": {"open_shares": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0, "buy_count": 0.0, "sell_count": 0.0, "buy_notional": 0.0, "sell_notional": 0.0},
            "Down": {"open_shares": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0, "buy_count": 0.0, "sell_count": 0.0, "buy_notional": 0.0, "sell_notional": 0.0},
        }
        price_bands: set[tuple[str, int]] = set()
        buy_count = 0
        sell_count = 0
        unmatched_sell_count = 0
        quality_flags = {
            "complete_event_metadata": bool(event_key),
            "complete_order_side": True,
            "complete_outcome_side": True,
            "has_resolution": False,
            "has_token_id": any(row.get("token_id") for row in ordered),
            "has_quote_context": False,
            "dedupe_confidence": "medium",
            "settlement_confidence": "unresolved",
            "pnl_confidence": "none",
        }

        for row in ordered:
            order_side = str(row.get("side") or "").upper()
            outcome_side = _event_reconstruction_side(row.get("outcome"))
            if order_side not in {"BUY", "SELL"}:
                quality_flags["complete_order_side"] = False
                continue
            if outcome_side not in {"Up", "Down"}:
                quality_flags["complete_outcome_side"] = False
                continue
            price = _to_float(row.get("price"))
            shares = _to_float(row.get("size"))
            usdc_size = _to_float(row.get("usdc_size"))
            if shares is None and usdc_size is not None and price and price > 0:
                shares = usdc_size / price
            if price is None or shares is None or shares <= 0:
                continue
            notional = usdc_size if usdc_size is not None else price * shares
            state = side_state[outcome_side]
            if order_side == "BUY":
                buy_count += 1
                state["buy_count"] += 1
                state["buy_notional"] += float(notional)
                state["open_shares"] += float(shares)
                state["cost_basis"] += float(notional)
                price_bands.add((outcome_side, int(max(0.0, min(0.999, price)) / 0.05)))
            else:
                sell_count += 1
                state["sell_count"] += 1
                state["sell_notional"] += float(notional)
                if state["open_shares"] <= 1e-9:
                    unmatched_sell_count += 1
                    state["realized_pnl"] += float(notional)
                    continue
                closed_shares = min(float(shares), state["open_shares"])
                allocated_cost = state["cost_basis"] * (closed_shares / state["open_shares"])
                proceeds = float(notional) * (closed_shares / float(shares))
                state["open_shares"] -= closed_shares
                state["cost_basis"] -= allocated_cost
                state["realized_pnl"] += proceeds - allocated_cost
                if float(shares) > closed_shares + 1e-9:
                    unmatched_sell_count += 1
                    state["realized_pnl"] += float(notional) - proceeds

        first_ts = ordered[0].get("timestamp") if ordered else None
        last_ts = ordered[-1].get("timestamp") if ordered else None
        up = side_state["Up"]
        down = side_state["Down"]
        total_open_shares = up["open_shares"] + down["open_shares"]
        gross_cost_basis = up["cost_basis"] + down["cost_basis"]
        total_buy_notional = up["buy_notional"] + down["buy_notional"]
        total_realized_pnl = up["realized_pnl"] + down["realized_pnl"]
        side_balance_score = (
            1.0 - abs(up["open_shares"] - down["open_shares"]) / total_open_shares
            if total_open_shares > 1e-9
            else 0.0
        )
        net_directional_skew = (
            (up["open_shares"] - down["open_shares"]) / total_open_shares
            if total_open_shares > 1e-9
            else 0.0
        )
        duration_minutes = (
            max((last_ts - first_ts).total_seconds() / 60.0, 1.0)
            if isinstance(first_ts, datetime) and isinstance(last_ts, datetime)
            else 1.0
        )
        event_pnl_known = bool(sell_count > 0 and gross_cost_basis <= 1e-6)
        partial_pnl_known = bool(sell_count > 0)
        if event_pnl_known:
            quality_flags["pnl_confidence"] = "realized_closed"
        elif partial_pnl_known:
            quality_flags["pnl_confidence"] = "realized_partial_open_inventory"
        event_style = _classify_reconstructed_event_style(
            buy_count=buy_count,
            sell_count=sell_count,
            up_buy_count=int(up["buy_count"]),
            down_buy_count=int(down["buy_count"]),
            both_side_buy_seen=up["buy_count"] > 0 and down["buy_count"] > 0,
            side_balance_score=side_balance_score,
            avg_orders_per_minute=len(ordered) / duration_minutes,
            price_band_count=len(price_bands),
        )
        event_slug = str(ordered[0].get("event_slug") or ordered[0].get("slug") or event_key) if ordered else event_key
        events.append(
            {
                "event_key": event_key,
                "event_slug": event_slug,
                "condition_id": ordered[0].get("condition_id") if ordered else None,
                "symbol": ordered[0].get("symbol") if ordered else None,
                "cadence": ordered[0].get("cadence") if ordered else None,
                "first_signal_at_utc": first_ts.isoformat() if isinstance(first_ts, datetime) else None,
                "last_signal_at_utc": last_ts.isoformat() if isinstance(last_ts, datetime) else None,
                "seconds_since_last_signal": (now_utc - last_ts).total_seconds() if isinstance(last_ts, datetime) else None,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "up_buy_count": int(up["buy_count"]),
                "down_buy_count": int(down["buy_count"]),
                "up_sell_count": int(up["sell_count"]),
                "down_sell_count": int(down["sell_count"]),
                "both_side_buy_seen": up["buy_count"] > 0 and down["buy_count"] > 0,
                "both_side_hold_seen": total_open_shares > 1e-9 and up["open_shares"] > 1e-9 and down["open_shares"] > 1e-9,
                "buy_sell_cycle_seen": sell_count > 0 and buy_count > 0,
                "up_open_shares": round(up["open_shares"], 6),
                "down_open_shares": round(down["open_shares"], 6),
                "up_cost_basis_usd": round(up["cost_basis"], 6),
                "down_cost_basis_usd": round(down["cost_basis"], 6),
                "up_avg_price": round(up["cost_basis"] / up["open_shares"], 6) if up["open_shares"] > 1e-9 else None,
                "down_avg_price": round(down["cost_basis"] / down["open_shares"], 6) if down["open_shares"] > 1e-9 else None,
                "gross_cost_basis_usd": round(gross_cost_basis, 6),
                "total_buy_notional_usd": round(total_buy_notional, 6),
                "realized_pnl_usd": round(total_realized_pnl, 6),
                "event_pnl_usd": round(total_realized_pnl, 6) if partial_pnl_known else None,
                "event_pnl_known": event_pnl_known,
                "event_effective_win": (total_realized_pnl > 0) if event_pnl_known else None,
                "event_effective_loss": (total_realized_pnl < 0) if event_pnl_known else None,
                "side_balance_score": round(side_balance_score, 6),
                "net_directional_skew": round(net_directional_skew, 6),
                "hedge_ratio_up": round(up["open_shares"] / total_open_shares, 6) if total_open_shares > 1e-9 else None,
                "hedge_ratio_down": round(down["open_shares"] / total_open_shares, 6) if total_open_shares > 1e-9 else None,
                "price_band_count": len(price_bands),
                "avg_orders_per_minute": round(len(ordered) / duration_minutes, 6),
                "unmatched_sell_count": unmatched_sell_count,
                "event_style": event_style,
                "quality_flags": quality_flags,
            }
        )
    return events


def _classify_reconstructed_event_style(
    *,
    buy_count: int,
    sell_count: int,
    up_buy_count: int,
    down_buy_count: int,
    both_side_buy_seen: bool,
    side_balance_score: float,
    avg_orders_per_minute: float,
    price_band_count: int,
) -> str:
    if buy_count > 0 and sell_count >= 2:
        return "scalping_event"
    if buy_count >= 8 and price_band_count >= 3 and sell_count == 0:
        return "grid_buyer_event"
    if both_side_buy_seen and side_balance_score >= 0.20:
        return "hedger_event"
    if buy_count > 0 and max(up_buy_count, down_buy_count) / max(buy_count, 1) >= 0.80:
        return "outcome_predictor_event"
    if avg_orders_per_minute >= 4.0 and sell_count > 0:
        return "scalping_event"
    return "unknown_event"


def _reconstructed_event_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [event for event in events if event.get("event_pnl_known") and _to_float(event.get("event_pnl_usd")) is not None]
    wins = [event for event in closed if (_to_float(event.get("event_pnl_usd")) or 0.0) > 0]
    losses = [event for event in closed if (_to_float(event.get("event_pnl_usd")) or 0.0) < 0]
    total_pnl = sum(_to_float(event.get("event_pnl_usd")) or 0.0 for event in closed)
    total_cost = sum(_to_float(event.get("total_buy_notional_usd")) or 0.0 for event in closed)
    return {
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "realized_pnl": total_pnl,
        "return_pct": total_pnl / total_cost * 100.0 if total_cost else None,
        "closed_events": len(closed),
    }


def _reconstructed_event_stats_since(events: list[dict[str, Any]], start: datetime | None) -> dict[str, Any]:
    if start is None:
        return _reconstructed_event_stats(events)
    filtered = [
        event
        for event in events
        if (timestamp := _parse_timestamp(event.get("last_signal_at_utc"))) is not None and timestamp >= start
    ]
    return _reconstructed_event_stats(filtered)


def _profile_period_metrics(
    *,
    pnl_recent: list[dict[str, Any]],
    pnl_all: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    reconstructed_events: list[dict[str, Any]],
    now_utc: datetime,
) -> dict[str, dict[str, Any]]:
    periods = {
        "1h": timedelta(hours=1),
        "3h": timedelta(hours=3),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "all_time": None,
    }
    pnl_rows = pnl_recent or pnl_all
    metrics: dict[str, dict[str, Any]] = {}
    for label, delta in periods.items():
        start = now_utc - delta if delta is not None else None
        closed = _closed_stats_since(closed_rows, start)
        reconstructed = _reconstructed_event_stats_since(reconstructed_events, start)
        pnl_usd = _latest_pnl(pnl_all or pnl_recent) if label == "all_time" else _pnl_change(pnl_rows, start)
        metrics[label] = {
            "pnl_usd": pnl_usd,
            "closed_wins": closed["wins"],
            "closed_losses": closed["losses"],
            "closed_outcomes": closed["wins"] + closed["losses"],
            "closed_win_rate": closed["win_rate"],
            "closed_return_pct": closed["return_pct"],
            "closed_profit_usd": closed["realized_pnl"],
            "reconstructed_event_wins": reconstructed["wins"],
            "reconstructed_event_losses": reconstructed["losses"],
            "reconstructed_event_outcomes": reconstructed["wins"] + reconstructed["losses"],
            "reconstructed_event_win_rate": reconstructed["win_rate"],
            "reconstructed_event_return_pct": reconstructed["return_pct"],
            "reconstructed_event_profit_usd": reconstructed["realized_pnl"],
        }
    return metrics


def _classify_profile_trading_behavior(
    crypto_rows: list[dict[str, Any]],
    *,
    reconstructed_events: list[dict[str, Any]],
    now_utc: datetime,
    config: ProfileSignalConfig,
) -> dict[str, Any]:
    style_start = now_utc - timedelta(seconds=config.profile_style_window_seconds)
    frequency_start = now_utc - timedelta(seconds=config.profile_frequency_window_seconds)
    recent_rows = [
        row for row in crypto_rows if row.get("timestamp") and style_start <= row["timestamp"] <= now_utc
    ]
    hour_rows = [
        row for row in crypto_rows if row.get("timestamp") and frequency_start <= row["timestamp"] <= now_utc
    ]
    event_styles: Counter[str] = Counter()
    both_side_events = 0
    buy_sell_events = 0
    style_events = [
        event
        for event in reconstructed_events
        if (timestamp := _parse_timestamp(event.get("last_signal_at_utc"))) is not None and style_start <= timestamp <= now_utc
    ]
    for event in style_events:
        event_style = str(event.get("event_style") or "")
        if event_style == "scalping_event":
            event_styles["scalping_trader"] += 1
            buy_sell_events += 1
        elif event_style == "grid_buyer_event":
            event_styles["hedger"] += 1
            event_styles["grid_buyer"] += 1
            both_side_events += 1
        elif event_style == "hedger_event":
            event_styles["hedger"] += 1
            both_side_events += 1
        elif event_style == "outcome_predictor_event":
            event_styles["outcome_predictor"] += 1

    if event_styles:
        trading_style = sorted(
            event_styles,
            key=lambda label: (-event_styles[label], {"hedger": 0, "outcome_predictor": 1, "scalping_trader": 2, "grid_buyer": 3}.get(label, 9)),
        )[0]
        if trading_style == "grid_buyer":
            trading_style = "hedger"
    else:
        trading_style = "unknown"

    avg_trades_per_event = len(recent_rows) / len(style_events) if style_events else 0.0
    hour_event_slugs = {str(row.get("event_slug") or row.get("slug") or "") for row in hour_rows if row.get("event_slug") or row.get("slug")}
    hour_trade_count = len(hour_rows)
    if hour_trade_count >= 60 or avg_trades_per_event >= 10:
        frequency_class = "high_frequency"
    elif hour_trade_count >= 12 or avg_trades_per_event >= 3:
        frequency_class = "medium_frequency"
    else:
        frequency_class = "low_frequency"

    trading_style_detail = trading_style
    if event_styles.get("grid_buyer", 0) >= max(event_styles.get("hedger", 0), 1) or (
        trading_style == "hedger" and buy_sell_events == 0 and avg_trades_per_event >= 10
    ):
        trading_style_detail = "grid_buyer"

    expected_hourly_events = max(int(config.expected_crypto_events_per_hour), 1)
    coverage_1h = min(1.0, len(hour_event_slugs) / expected_hourly_events)
    return {
        "trading_style": trading_style,
        "trading_style_detail": trading_style_detail,
        "frequency_class": frequency_class,
        "crypto_signal_count_24h": len(recent_rows),
        "crypto_signal_count_1h": hour_trade_count,
        "crypto_event_count_24h": len(style_events),
        "crypto_event_count_1h": len(hour_event_slugs),
        "crypto_event_coverage_1h": coverage_1h,
        "active_crypto_most_of_last_hour": len(hour_event_slugs) >= int(config.s_plus_plus_min_crypto_events_1h),
        "both_side_event_count_24h": both_side_events,
        "buy_sell_event_count_24h": buy_sell_events,
        "event_style_counts_24h": dict(sorted(event_styles.items())),
        "avg_crypto_trades_per_event_24h": avg_trades_per_event,
        "reconstructed_event_count_24h": len(style_events),
        "classification_source": "event_reconstruction",
    }


def _loss_streaks(rows: list[dict[str, Any]]) -> tuple[int, int, float | None]:
    ordered = sorted(rows, key=lambda row: row.get("timestamp") or datetime.fromtimestamp(0, tz=timezone.utc), reverse=True)
    current = 0
    if ordered:
        sign = 1 if float(ordered[0].get("realized_pnl") or 0.0) > 0 else -1
        for row in ordered:
            row_sign = 1 if float(row.get("realized_pnl") or 0.0) > 0 else -1
            if row_sign != sign:
                break
            current += row_sign
    loss_streaks: list[int] = []
    active_loss = 0
    for row in reversed(ordered):
        if float(row.get("realized_pnl") or 0.0) < 0:
            active_loss += 1
        elif active_loss:
            loss_streaks.append(active_loss)
            active_loss = 0
    if active_loss:
        loss_streaks.append(active_loss)
    max_loss = max(loss_streaks, default=0)
    avg_loss = sum(loss_streaks) / len(loss_streaks) if loss_streaks else None
    return current, max_loss, avg_loss


def _pnl_metrics(recent: list[dict[str, Any]], all_history: list[dict[str, Any]], *, now_utc: datetime) -> dict[str, float | None]:
    return {
        "daily_pnl_usd": _pnl_change(recent or all_history, now_utc - timedelta(days=1)),
        "weekly_pnl_usd": _pnl_change(recent or all_history, now_utc - timedelta(days=7)),
        "monthly_pnl_usd": _pnl_change(recent or all_history, now_utc - timedelta(days=30)),
        "quarterly_pnl_usd": _pnl_change(recent or all_history, now_utc - timedelta(days=90)),
        "all_pnl_usd": _latest_pnl(all_history or recent),
    }


def _pnl_change(rows: list[dict[str, Any]], start: datetime) -> float | None:
    series = sorted((_pnl_point(row) for row in rows), key=lambda item: item[0])
    series = [item for item in series if item[1] is not None]
    if not series:
        return None
    latest_ts, latest = series[-1]
    if latest_ts < start:
        return latest
    baseline_candidates = [item for item in series if item[0] <= start]
    baseline = baseline_candidates[-1][1] if baseline_candidates else series[0][1]
    if latest is None or baseline is None:
        return None
    return float(latest) - float(baseline)


def _s_grade_blockers(
    metrics: dict[str, Any],
    *,
    daily: float,
    weekly: float,
    monthly: float,
    win_rate: float | None,
    config: ProfileSignalConfig,
) -> list[str]:
    blockers: list[str] = []
    if daily <= float(config.s_grade_min_daily_pnl_usd):
        blockers.append("s_grade_blocked_daily_pnl_not_positive")
    if weekly <= float(config.s_grade_min_weekly_pnl_usd):
        blockers.append("s_grade_blocked_weekly_pnl_not_positive")
    if monthly <= float(config.s_grade_min_monthly_pnl_usd):
        blockers.append("s_grade_blocked_monthly_pnl_not_positive")
    closed_outcomes = int(metrics.get("closed_wins") or 0) + int(metrics.get("closed_losses") or 0)
    if closed_outcomes < int(config.s_grade_min_closed_outcomes):
        blockers.append("s_grade_blocked_insufficient_closed_outcomes")
    if win_rate is None or win_rate < float(config.s_grade_min_closed_win_rate):
        blockers.append("s_grade_blocked_closed_win_rate_below_threshold")
    return blockers


def _s_plus_grade_blockers(
    metrics: dict[str, Any],
    *,
    daily: float,
    weekly: float,
    monthly: float,
    quarterly: float,
    all_pnl: float,
    win_rate: float | None,
    config: ProfileSignalConfig,
) -> list[str]:
    blockers: list[str] = []
    if daily <= float(config.s_plus_min_daily_pnl_usd):
        blockers.append("s_plus_blocked_daily_pnl_not_positive")
    if weekly <= float(config.s_plus_min_weekly_pnl_usd):
        blockers.append("s_plus_blocked_weekly_pnl_not_positive")
    if monthly <= float(config.s_plus_min_monthly_pnl_usd):
        blockers.append("s_plus_blocked_monthly_pnl_not_positive")
    if quarterly < float(config.s_plus_min_quarterly_pnl_usd):
        blockers.append("s_plus_blocked_quarterly_pnl_below_threshold")
    if all_pnl < float(config.s_plus_min_all_pnl_usd):
        blockers.append("s_plus_blocked_all_pnl_below_threshold")
    closed_outcomes = int(metrics.get("closed_wins") or 0) + int(metrics.get("closed_losses") or 0)
    if closed_outcomes < int(config.s_plus_min_closed_outcomes):
        blockers.append("s_plus_blocked_insufficient_closed_outcomes")
    if win_rate is None or win_rate < float(config.s_plus_min_closed_win_rate):
        blockers.append("s_plus_blocked_closed_win_rate_below_threshold")
    max_loss_streak = int(metrics.get("max_closed_loss_streak") or 0)
    if max_loss_streak > int(config.s_plus_max_closed_loss_streak):
        blockers.append("s_plus_blocked_loss_streak_above_threshold")
    return blockers


def _latest_pnl(rows: list[dict[str, Any]]) -> float | None:
    series = sorted((_pnl_point(row) for row in rows), key=lambda item: item[0])
    series = [item for item in series if item[1] is not None]
    return float(series[-1][1]) if series else None


def _pnl_point(row: dict[str, Any]) -> tuple[datetime, float | None]:
    return _parse_timestamp(row.get("t")) or datetime.fromtimestamp(0, tz=timezone.utc), _to_float(row.get("p"))


def _decision_reference_price(decision: dict[str, Any]) -> float | None:
    prices = [
        _to_float(signal.get("price"))
        for signal in decision.get("supporting_signals") or []
        if _to_float(signal.get("price")) is not None
    ]
    if not prices:
        return None
    return sum(prices) / len(prices)


def _taker_fee_per_share(price: float) -> float:
    return 0.07 * float(price) * (1.0 - float(price))


def _sample_bucket(value: Any) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "unknown"
    return parsed.replace(minute=0, second=0, microsecond=0).isoformat()


def _signal_weight(signal: dict[str, Any], config: ProfileSignalConfig) -> float:
    return float(config.grade_weights.get(str(signal.get("profile_grade") or "C"), 0.0))


def _period_value(period_metrics: dict[str, Any], period: str, field: str, default: float | None = 0.0) -> float | None:
    period_row = period_metrics.get(period) if isinstance(period_metrics, dict) else None
    if not isinstance(period_row, dict):
        return default
    value = _to_float(period_row.get(field))
    return default if value is None else value


def _period_field_map(period_metrics: dict[str, Any], field: str) -> dict[str, float | None]:
    return {period: _period_value(period_metrics, period, field, default=None) for period in ("1h", "1d", "7d", "30d", "all_time")}


def _s_plus_plus_reliability_passes(metrics: dict[str, Any], *, score: float, config: ProfileSignalConfig) -> bool:
    return bool(
        float(score) >= float(config.s_plus_plus_min_score)
        and bool(metrics.get("active_crypto_most_of_last_hour"))
        and (_to_float(metrics.get("daily_pnl_usd")) or 0.0) > float(config.s_plus_plus_min_daily_pnl_usd)
        and (_to_float(metrics.get("weekly_pnl_usd")) or 0.0) > float(config.s_plus_plus_min_weekly_pnl_usd)
        and (_to_float(metrics.get("monthly_pnl_usd")) or 0.0) > float(config.s_plus_plus_min_monthly_pnl_usd)
        and int(metrics.get("crypto_event_count_1h") or 0) >= int(config.s_plus_plus_min_crypto_events_1h)
        and int(metrics.get("crypto_event_count_24h") or 0) >= int(config.s_plus_plus_min_crypto_events_24h)
    )


def _effective_outcome(outcome: str | None, side: str | None, polarity: str) -> str | None:
    normalized = _normalize_outcome(outcome)
    if normalized not in {"Up", "Down", "Yes", "No"}:
        return normalized
    invert = (str(side or "").upper() == "SELL") ^ (polarity == "inverse")
    return _opposite_outcome(normalized) if invert else normalized


def _opposite_outcome(outcome: str | None) -> str | None:
    return {"Up": "Down", "Down": "Up", "Yes": "No", "No": "Yes"}.get(str(outcome or ""))


def _grade_from_score(score: float, thresholds: dict[str, float]) -> str:
    for grade in ("S+", "S", "A", "B", "C", "D"):
        if score >= float(thresholds.get(grade, 0.0)):
            return grade
    return "E"


def _is_crypto_updown(row: dict[str, Any]) -> bool:
    text = f"{row.get('title') or ''} {row.get('slug') or ''} {row.get('event_slug') or ''}".lower()
    return bool(re.search(r"\b(btc|bitcoin|eth|ethereum|sol|solana|xrp|crypto)\b", text)) and "updown" in text


def _symbol_from_text(text: str) -> str | None:
    lowered = text.lower()
    if "bitcoin" in lowered or "btc" in lowered:
        return "BTC"
    if "ethereum" in lowered or "eth" in lowered:
        return "ETH"
    if "solana" in lowered or re.search(r"\bsol\b", lowered):
        return "SOL"
    if "xrp" in lowered:
        return "XRP"
    return None


def _cadence_from_text(text: str) -> str | None:
    match = re.search(r"updown-(\d+m|\d+h)-", text)
    if match:
        return match.group(1)
    title_match = re.search(r"\d+:\d+\s*(?:am|pm)\s*-\s*\d+:\d+\s*(?:am|pm)", text, flags=re.IGNORECASE)
    return "clock_window" if title_match else None


def _normalize_outcome(value: Any) -> str | None:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered in {"up", "yes"}:
        return text[:1].upper() + text[1:].lower()
    if lowered in {"down", "no"}:
        return text[:1].upper() + text[1:].lower()
    return text or None


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) >= 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _floor_time(value: datetime, *, seconds: int) -> datetime:
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % int(seconds)), tz=timezone.utc)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _coerce_datetime(value: Any) -> datetime | None:
    return _parse_timestamp(value)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series_float(value: Any) -> float | None:
    return _to_float(value)


def _latest_iso(rows: list[dict[str, Any]]) -> str | None:
    timestamps = [row["timestamp"] for row in rows if row.get("timestamp")]
    return max(timestamps).isoformat() if timestamps else None


def _is_wallet(value: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", str(value or "").strip()))


def _is_condition_id(value: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", str(value or "").strip()))


def _dedupe_registry(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for ref in refs:
        key = str(ref.get("normalized_ref") or ref.get("raw_ref") or "").lower()
        if not key:
            continue
        if key not in deduped:
            deduped[key] = dict(ref)
        else:
            existing = deduped[key]
            sources = [str(existing.get("source") or ""), str(ref.get("source") or "")]
            existing["source"] = ",".join(dict.fromkeys(source for source in sources if source))
    return list(deduped.values())


def _dedupe_profile_snapshots_by_identity(snapshots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    deduped: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for snapshot in snapshots:
        key = _profile_identity_key(snapshot)
        if key not in deduped:
            deduped[key] = snapshot
            deduped[key]["profile_aliases"] = [_snapshot_alias(snapshot)]
            continue

        existing = deduped[key]
        existing.setdefault("profile_aliases", []).append(_snapshot_alias(snapshot))
        existing["profile_aliases"] = _dedupe_aliases(existing.get("profile_aliases") or [])
        blockers.append(f"profile_alias_merged:{key}:{_snapshot_alias(snapshot).get('normalized_ref')}")
    return list(deduped.values()), blockers


def _profile_identity_key(snapshot: dict[str, Any]) -> str:
    profile = snapshot.get("profile") or {}
    ref = snapshot.get("profile_ref") or {}
    wallet = str(profile.get("proxy_wallet") or ref.get("address") or "").strip().lower()
    if wallet:
        return f"wallet:{wallet}"
    name = str(profile.get("name") or ref.get("handle") or "").strip().lower()
    if name:
        return f"name:{name}"
    return f"ref:{str(ref.get('normalized_ref') or ref.get('raw_ref') or '').lower()}"


def _strategy_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return slug[:80] or "unknown_profile"


def _snapshot_alias(snapshot: dict[str, Any]) -> dict[str, Any]:
    ref = snapshot.get("profile_ref") or {}
    profile = snapshot.get("profile") or {}
    return {
        "raw_ref": ref.get("raw_ref"),
        "normalized_ref": ref.get("normalized_ref"),
        "source": ref.get("source"),
        "resolved_name": profile.get("name"),
        "proxy_wallet": profile.get("proxy_wallet"),
    }


def _dedupe_aliases(aliases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for alias in aliases:
        key = str(alias.get("normalized_ref") or alias.get("raw_ref") or alias.get("proxy_wallet") or "").lower()
        if key and key not in deduped:
            deduped[key] = alias
    return list(deduped.values())


def _fmt(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.2f}"


def _slippage_return_sum(variant: dict[str, Any], cents: int) -> float | None:
    for row in ((variant.get("economics") or {}).get("slippage_sensitivity") or []):
        if row.get("adverse_slippage_cents") == cents:
            return _to_float(row.get("return_sum"))
    return None


__all__ = [
    "CRYPTO_OPTIONS_PROFILE_SIGNAL_SCHEMA_VERSION",
    "DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT",
    "DISCOVERED_TOP_HOLDER_LOSER_PROFILE_REFS",
    "DISCOVERED_TOP_HOLDER_WINNER_PROFILE_REFS",
    "ProfileSignalConfig",
    "USER_CURATED_VISIBLE_TOP_HOLDER_REFS",
    "USER_REQUESTED_PROFILE_REFS",
    "aggregate_profile_signals",
    "build_profile_signal_generator_scores",
    "build_profile_registry",
    "build_profile_signal_report",
    "build_profile_snapshot",
    "compute_profile_metrics",
    "extract_recent_profile_signals",
    "extract_profile_signals",
    "fetch_profile_signal_source",
    "grade_profile",
    "load_active_crypto_profile_pool_refs",
    "load_obsidian_crypto_profile_refs",
    "normalize_profile_ref",
    "refresh_profile_signal_report_from_cache",
    "render_profile_signal_markdown",
    "reconstruct_profile_events_from_activity",
    "resolve_crypto_updown_outcome",
    "write_profile_signal_artifacts",
]
