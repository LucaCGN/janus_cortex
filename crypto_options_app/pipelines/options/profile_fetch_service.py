from __future__ import annotations

"""Async profile fetch service for the crypto options profile store.

The service is intentionally read-only with respect to trading.  It fetches
public Polymarket profile/activity data, builds profile snapshots, and persists
the raw and reconstructed profile data into the separate profile SQLite store.
"""

import asyncio
import hashlib
import inspect
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from crypto_options_app.pipelines.options.profile_signals import (
    CRYPTO_OPTIONS_PROFILE_SIGNAL_SCHEMA_VERSION,
    DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT,
    ProfileSignalConfig,
    build_profile_snapshot,
    fetch_profile_signal_source,
    load_active_crypto_profile_pool_refs,
    normalize_profile_ref,
)
from crypto_options_app.pipelines.options.profile_store import (
    default_profile_store_path,
    ingest_profile_signal_report,
    record_profile_fetch_run,
)
from crypto_options_app.pipelines.options.reporting import strict_jsonable


PROFILE_FETCH_SERVICE_SCHEMA_VERSION = "crypto_options_profile_fetch_service_v1"


@dataclass(frozen=True)
class ProfileFetchServiceConfig:
    activity_pages: int = 1
    positions_pages: int = 1
    trades_pages: int = 1
    closed_pages: int = 1
    page_limit: int = 200
    max_concurrency: int = 8
    active_profile_pool_limit: int = DEFAULT_ACTIVE_CRYPTO_PROFILE_POOL_LIMIT


async def fetch_profile_sources_async(
    profile_refs: list[str | dict[str, Any]],
    *,
    now_utc: datetime | None = None,
    config: ProfileFetchServiceConfig | None = None,
    signal_config: ProfileSignalConfig | None = None,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fetch profile sources with a queue and bounded semaphore."""

    now_utc = now_utc or datetime.now(timezone.utc)
    config = config or ProfileFetchServiceConfig()
    signal_config = signal_config or ProfileSignalConfig()
    seeds = _dedupe_seeds([_normalize_seed(ref) for ref in profile_refs])
    started_at = now_utc.isoformat()
    fetch_run_id = _fetch_run_id(seeds, started_at)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    for seed in seeds:
        queue.put_nowait(seed)
    worker_count = max(1, min(int(config.max_concurrency), max(len(seeds), 1)))
    for _ in range(worker_count):
        queue.put_nowait(None)
    semaphore = asyncio.Semaphore(worker_count)
    profiles_by_key: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    async def worker() -> None:
        while True:
            seed = await queue.get()
            try:
                if seed is None:
                    return
                key = str(seed.get("normalized_ref") or seed.get("raw_ref") or "")
                try:
                    async with semaphore:
                        source = await _fetch_one(seed, config=config, fetcher=fetcher)
                    profiles_by_key[key] = build_profile_snapshot(seed, source, now_utc=now_utc, config=signal_config)
                except Exception as exc:  # noqa: BLE001 - one profile must not block the full pool.
                    failures.append(
                        {
                            "profile_ref": seed,
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    await queue.join()
    await asyncio.gather(*workers)
    profiles = [profiles_by_key[key] for seed in seeds if (key := str(seed.get("normalized_ref") or seed.get("raw_ref") or "")) in profiles_by_key]
    return strict_jsonable(
        {
            "schema_version": PROFILE_FETCH_SERVICE_SCHEMA_VERSION,
            "fetch_run_id": fetch_run_id,
            "started_at_utc": started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "complete" if not failures else "partial",
            "profile_registry": seeds,
            "profile_seed_count": len(seeds),
            "profile_snapshot_count": len(profiles),
            "failed_profile_count": len(failures),
            "profiles": profiles,
            "failures": failures,
            "config": asdict(config),
            "boundary": _read_only_boundary(),
        }
    )


async def run_profile_fetch_service_once(
    profile_refs: list[str | dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    source_label: str = "profile_fetch_service",
    now_utc: datetime | None = None,
    config: ProfileFetchServiceConfig | None = None,
    signal_config: ProfileSignalConfig | None = None,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fetch a profile pool once and persist snapshots/raw rows into SQLite."""

    fetch_payload = await fetch_profile_sources_async(
        profile_refs,
        now_utc=now_utc,
        config=config,
        signal_config=signal_config,
        fetcher=fetcher,
    )
    report = {
        "schema_version": CRYPTO_OPTIONS_PROFILE_SIGNAL_SCHEMA_VERSION,
        "generated_at_utc": fetch_payload["completed_at_utc"],
        "profile_seed_count": fetch_payload["profile_seed_count"],
        "profile_fetch_max_workers": (fetch_payload.get("config") or {}).get("max_concurrency"),
        "profile_registry": fetch_payload["profile_registry"],
        "profiles": fetch_payload["profiles"],
        "active_signals": _active_signals_from_profiles(fetch_payload["profiles"]),
        "aggregated_candidates": [],
        "blockers": [f"profile_fetch_failed:{row.get('profile_ref')}:{row.get('error_type')}" for row in fetch_payload.get("failures") or []],
        "boundary": _read_only_boundary(),
    }
    fetch_run_id = str(fetch_payload["fetch_run_id"])
    record_profile_fetch_run(
        fetch_run_id=fetch_run_id,
        started_at_utc=str(fetch_payload["started_at_utc"]),
        completed_at_utc=str(fetch_payload["completed_at_utc"]),
        status=str(fetch_payload["status"]),
        requested_ref_count=int(fetch_payload["profile_seed_count"]),
        fetched_profile_count=int(fetch_payload["profile_snapshot_count"]),
        failed_profile_count=int(fetch_payload["failed_profile_count"]),
        max_concurrency=int((fetch_payload.get("config") or {}).get("max_concurrency") or 1),
        page_limit=int((fetch_payload.get("config") or {}).get("page_limit") or 0),
        summary={"source_label": source_label, "failures": fetch_payload.get("failures") or []},
        db_path=db_path,
    )
    ingest_payload = ingest_profile_signal_report(
        report,
        db_path=db_path or default_profile_store_path(),
        source_artifact=source_label,
        fetch_run_id=fetch_run_id,
        config=signal_config,
    )
    return {
        "schema_version": "crypto_options_profile_fetch_service_once_result_v1",
        "fetch": fetch_payload,
        "ingest": ingest_payload,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


async def run_active_pool_profile_fetch_once(
    *,
    active_profile_pool_path: str | Path | None = None,
    db_path: str | Path | None = None,
    source_label: str = "active_pool_profile_fetch_service",
    config: ProfileFetchServiceConfig | None = None,
    signal_config: ProfileSignalConfig | None = None,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = config or ProfileFetchServiceConfig()
    refs = load_active_crypto_profile_pool_refs(
        active_profile_pool_path,
        limit=config.active_profile_pool_limit,
        enabled=True,
    )
    return await run_profile_fetch_service_once(
        refs,
        db_path=db_path,
        source_label=source_label,
        config=config,
        signal_config=signal_config,
        fetcher=fetcher,
    )


async def _fetch_one(
    seed: dict[str, Any],
    *,
    config: ProfileFetchServiceConfig,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    if fetcher is not None:
        if inspect.iscoroutinefunction(fetcher):
            return await fetcher(seed)  # type: ignore[misc]
        return await asyncio.to_thread(fetcher, seed)
    return await asyncio.to_thread(
        fetch_profile_signal_source,
        seed,
        activity_pages=config.activity_pages,
        positions_pages=config.positions_pages,
        trades_pages=config.trades_pages,
        closed_pages=config.closed_pages,
        page_limit=config.page_limit,
    )


def _active_signals_from_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        for signal in profile.get("signals") or []:
            if isinstance(signal, dict):
                rows.append(signal)
    return rows


def _normalize_seed(ref: str | dict[str, Any]) -> dict[str, Any]:
    return ref if isinstance(ref, dict) else normalize_profile_ref(ref, source="profile_fetch_service")


def _dedupe_seeds(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for seed in seeds:
        key = str(seed.get("normalized_ref") or seed.get("raw_ref") or "").lower()
        if key and key not in out:
            out[key] = seed
    return list(out.values())


def _fetch_run_id(seeds: list[dict[str, Any]], started_at_utc: str) -> str:
    joined = "|".join(str(seed.get("normalized_ref") or seed.get("raw_ref") or "") for seed in seeds)
    return "profile-fetch-" + hashlib.sha256(f"{started_at_utc}|{joined}".encode("utf-8")).hexdigest()[:16]


def _read_only_boundary() -> dict[str, Any]:
    return {
        "orders_allowed": False,
        "live_trading_authorized": False,
        "description": "Profile fetches and signal streams are data infrastructure only; they do not authorize or place orders.",
    }


__all__ = [
    "PROFILE_FETCH_SERVICE_SCHEMA_VERSION",
    "ProfileFetchServiceConfig",
    "fetch_profile_sources_async",
    "run_active_pool_profile_fetch_once",
    "run_profile_fetch_service_once",
]
