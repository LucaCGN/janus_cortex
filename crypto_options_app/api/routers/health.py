from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Any

from fastapi import APIRouter, Request

from crypto_options_app.feeds.polymarket_status import build_cached_polymarket_status_provider, fetch_polymarket_status
from crypto_options_app.reports.system_integrity import HealthBuildOptions, build_system_integrity_health

router = APIRouter(prefix="/v1/crypto-options-app/health", tags=["crypto-options-app-health"])
HEALTH_CACHE_TTL_SECONDS = 10.0


@router.get("")
def get_health(request: Request) -> dict[str, Any]:
    config = request.app.state.crypto_options_config
    provider = getattr(request.app.state, "crypto_options_health_polymarket_status_provider", None)
    if provider is None:
        provider = build_cached_polymarket_status_provider(
            fetcher=lambda: fetch_polymarket_status(timeout_seconds=0.4),
            fresh_seconds=30.0,
            ttl_seconds=180.0,
        )
        request.app.state.crypto_options_health_polymarket_status_provider = provider
    now = monotonic()
    cached = getattr(request.app.state, "crypto_options_health_cache", None)
    if isinstance(cached, dict) and now < float(cached.get("expires_at", 0.0)):
        payload = dict(cached["payload"])
        payload["health_cache"] = {
            "status": "hit",
            "generated_at_utc": cached.get("generated_at_utc"),
            "ttl_seconds": HEALTH_CACHE_TTL_SECONDS,
        }
        return payload

    try:
        payload = build_system_integrity_health(
            config,
            options=HealthBuildOptions(
                artifact_root=config.artifact_root,
                polymarket_status_provider=provider,
                external_status_timeout_seconds=0.6,
                prefer_cached_db_report=config.database_backend != "postgres",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - health should degrade before wedging the monitor.
        if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
            payload = dict(cached["payload"])
            payload["status"] = "degraded"
            payload["health_cache"] = {
                "status": "stale_after_error",
                "generated_at_utc": cached.get("generated_at_utc"),
                "error": f"{type(exc).__name__}:{exc}",
            }
            return payload
        raise

    request.app.state.crypto_options_health_cache = {
        "payload": dict(payload),
        "expires_at": now + HEALTH_CACHE_TTL_SECONDS,
        "generated_at_utc": payload.get("generated_at_utc"),
    }
    payload["health_cache"] = {
        "status": "refresh",
        "generated_at_utc": payload.get("generated_at_utc"),
        "ttl_seconds": HEALTH_CACHE_TTL_SECONDS,
    }
    return payload


@router.get("/ping")
def get_health_ping(request: Request) -> dict[str, Any]:
    config = request.app.state.crypto_options_config
    return {
        "schema_version": "crypto_options_app_health_ping_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "service": config.app_name,
        "api_version": config.api_version,
        "status": "ok",
    }
