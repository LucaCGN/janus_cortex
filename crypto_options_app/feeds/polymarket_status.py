from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SUMMARY_URL = "https://status.polymarket.com/v3/summary.json"
COMPONENTS_URL = "https://status.polymarket.com/v3/components.json"
STATUS_PAGE_URL = "https://status.polymarket.com/"


def fetch_polymarket_status(*, timeout_seconds: float = 8.0) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    try:
        summary = _fetch_json(SUMMARY_URL, timeout_seconds=timeout_seconds)
        components_payload = _fetch_json(COMPONENTS_URL, timeout_seconds=timeout_seconds)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        fallback = _fetch_status_page_fallback(timeout_seconds=timeout_seconds)
        if fallback is not None:
            fallback["json_api_error"] = f"{type(exc).__name__}:{exc}"
            return fallback
        return {
            "schema_version": "polymarket_status_v1",
            "generated_at_utc": generated_at,
            "source": "polymarket_status_api",
            "status": "unknown",
            "clob_api": {"status": "unknown", "trading_available": False},
            "blockers": ["polymarket_status_unavailable"],
            "error": f"{type(exc).__name__}:{exc}",
        }
    components = components_payload.get("components") if isinstance(components_payload, dict) else components_payload
    return normalize_polymarket_status(summary=summary, components=components, generated_at_utc=generated_at)


def normalize_polymarket_status(
    *,
    summary: dict[str, Any] | None,
    components: list[dict[str, Any]] | None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or datetime.now(UTC).isoformat()
    summary = summary or {}
    components = components or []
    page = summary.get("page") if isinstance(summary.get("page"), dict) else {}
    page_status = _normalize_status(page.get("status"))
    clob_component = _component_by_name(components, "CLOB API")
    clob_status = _normalize_status(clob_component.get("status") if clob_component else None)
    active_maintenance = [
        _maintenance_summary(item)
        for item in summary.get("activeMaintenances") or []
        if isinstance(item, dict) and _maintenance_is_active(item)
    ]
    active_incidents = [
        _incident_summary(item)
        for item in summary.get("activeIncidents") or []
        if isinstance(item, dict)
    ]
    blocking_maintenance = [
        item
        for item in active_maintenance
        if _status_item_affects_clob_trading(item, fallback_to_page=clob_status != "operational")
    ]
    blocking_incidents = [
        item
        for item in active_incidents
        if _status_item_affects_clob_trading(item, fallback_to_page=clob_status != "operational")
    ]
    blockers: list[str] = []
    warnings: list[str] = []
    if clob_status and clob_status != "operational":
        blockers.append("exchange_status_not_operational")
    if page_status in {"undermaintenance", "degraded", "partialoutage", "majoroutage"} and clob_status != "operational":
        blockers.append("polymarket_page_not_operational")
    elif page_status in {"undermaintenance", "degraded", "partialoutage", "majoroutage"}:
        warnings.append("polymarket_page_not_operational_nonblocking_clob_operational")
    if blocking_maintenance:
        blockers.append("polymarket_active_maintenance")
    elif active_maintenance:
        warnings.append("polymarket_active_maintenance_nonblocking_clob_operational")
    if blocking_incidents:
        blockers.append("polymarket_active_incident")
    elif active_incidents:
        warnings.append("polymarket_active_incident_nonblocking_clob_operational")
    trading_available = not blockers and clob_status == "operational"
    return {
        "schema_version": "polymarket_status_v1",
        "generated_at_utc": generated_at_utc,
        "source": "polymarket_status_api",
        "status": "operational" if trading_available else "maintenance" if active_maintenance or clob_status == "undermaintenance" else "degraded" if blockers else "unknown",
        "page": {
            "name": page.get("name"),
            "url": page.get("url"),
            "status": page_status,
        },
        "clob_api": {
            "status": clob_status or "unknown",
            "trading_available": trading_available,
            "component": clob_component or None,
        },
        "active_maintenances": active_maintenance,
        "active_incidents": active_incidents,
        "blocking_maintenances": blocking_maintenance,
        "blocking_incidents": blocking_incidents,
        "warnings": sorted(set(warnings)),
        "blockers": sorted(set(blockers)),
    }


def clob_trading_available(status_report: dict[str, Any]) -> bool:
    clob = status_report.get("clob_api") if isinstance(status_report.get("clob_api"), dict) else {}
    return bool(clob.get("trading_available")) and not status_report.get("blockers")


def build_cached_polymarket_status_provider(
    *,
    fetcher: Callable[[], dict[str, Any]] | None = None,
    fresh_seconds: float = 60.0,
    ttl_seconds: float = 120.0,
    clock: Callable[[], float] | None = None,
) -> Callable[[], dict[str, Any]]:
    fetcher = fetcher or fetch_polymarket_status
    clock = clock or time.monotonic
    last_operational: dict[str, Any] | None = None
    last_operational_at: float | None = None

    def provider() -> dict[str, Any]:
        nonlocal last_operational, last_operational_at
        now = clock()
        if (
            last_operational is not None
            and last_operational_at is not None
            and now - last_operational_at <= max(0.0, fresh_seconds)
        ):
            cached = deepcopy(last_operational)
            cached["source"] = "polymarket_status_api_cache"
            cached["cache_fallback_used"] = False
            cached["cache_age_seconds"] = round(now - last_operational_at, 6)
            return cached
        try:
            report = fetcher()
        except Exception as exc:  # noqa: BLE001 - status should fail closed unless cache is fresh.
            report = {
                "schema_version": "polymarket_status_v1",
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "source": "polymarket_status_api",
                "status": "unknown",
                "clob_api": {"status": "unknown", "trading_available": False},
                "blockers": ["polymarket_status_unavailable"],
                "error": f"{type(exc).__name__}:{exc}",
            }
        if clob_trading_available(report):
            last_operational = deepcopy(report)
            last_operational_at = now
            return report
        if (
            "polymarket_status_unavailable" in set(report.get("blockers") or [])
            and last_operational is not None
            and last_operational_at is not None
            and now - last_operational_at <= max(0.0, ttl_seconds)
        ):
            cached = deepcopy(last_operational)
            cached["source"] = "polymarket_status_api_cache"
            cached["cache_fallback_used"] = True
            cached["cache_age_seconds"] = round(now - last_operational_at, 6)
            warnings = list(cached.get("warnings") or [])
            warnings.append("polymarket_status_unavailable_using_recent_operational_cache")
            cached["warnings"] = sorted(set(warnings))
            return cached
        return report

    return provider


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 janus-cortex-crypto-options-health/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed public status API URL.
        return json.loads(response.read().decode("utf-8"))


def _fetch_status_page_fallback(*, timeout_seconds: float) -> dict[str, Any] | None:
    try:
        html = _fetch_text(STATUS_PAGE_URL, timeout_seconds=timeout_seconds)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    text = _compact_status_page_text(html)
    page_operational = "all systems operational" in text
    clob_operational = bool(re.search(r"\bclob api\b.{0,80}\boperational\b", text))
    blockers: list[str] = []
    if not clob_operational:
        blockers.append("exchange_status_not_operational")
    if not page_operational:
        blockers.append("polymarket_page_not_operational")
    trading_available = page_operational and clob_operational and not blockers
    return {
        "schema_version": "polymarket_status_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": "polymarket_status_page_html_fallback",
        "status": "operational" if trading_available else "degraded",
        "page": {
            "name": "Polymarket",
            "url": STATUS_PAGE_URL,
            "status": "operational" if page_operational else "unknown",
        },
        "clob_api": {
            "status": "operational" if clob_operational else "unknown",
            "trading_available": trading_available,
            "component": None,
        },
        "active_maintenances": [],
        "active_incidents": [],
        "blockers": sorted(set(blockers)),
        "warnings": ["polymarket_status_json_unavailable_using_status_page_fallback"],
    }


def _fetch_text(url: str, *, timeout_seconds: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 janus-cortex-crypto-options-health/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed public status page URL.
        return response.read().decode("utf-8", errors="replace")


def _compact_status_page_text(html: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", without_tags).lower()


def _component_by_name(components: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    normalized_name = name.strip().lower()
    for component in components:
        if str(component.get("name") or "").strip().lower() == normalized_name:
            return component
    return None


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().replace("_", "").replace(" ", "").lower()


def _maintenance_is_active(item: dict[str, Any]) -> bool:
    status = _normalize_status(item.get("status"))
    return status not in {"completed", "cancelled", "canceled", "resolved"}


def _maintenance_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "status": _normalize_status(item.get("status")),
        "start": item.get("start"),
        "duration": item.get("duration"),
        "url": item.get("url"),
        "updated_at": item.get("updatedAt"),
        "affected_components": _affected_component_names(item),
    }


def _incident_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "status": _normalize_status(item.get("status")),
        "impact": _normalize_status(item.get("impact")),
        "url": item.get("url"),
        "started": item.get("started"),
        "updated_at": item.get("updatedAt"),
        "affected_components": _affected_component_names(item),
    }


def _affected_component_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("components", "affectedComponents", "affected_components"):
        raw_components = item.get(key)
        if not isinstance(raw_components, list):
            continue
        for component in raw_components:
            if isinstance(component, dict):
                name = str(component.get("name") or component.get("displayName") or "").strip()
                if name:
                    names.append(name)
            elif component:
                names.append(str(component).strip())
    return sorted(set(name for name in names if name))


def _status_item_affects_clob_trading(item: dict[str, Any], *, fallback_to_page: bool) -> bool:
    component_text = " ".join(str(name).lower() for name in item.get("affected_components") or [])
    if "clob" in component_text:
        return True
    name = str(item.get("name") or "").lower()
    text = " ".join(
        str(value or "").lower()
        for value in (
            item.get("name"),
            item.get("description"),
            item.get("message"),
            item.get("impact"),
            item.get("status"),
        )
    )
    clob_keywords = (
        "clob",
        "order book",
        "orderbook",
        "order submission",
        "orders unavailable",
        "submit orders",
        "order placement",
        "matching engine",
        "exchange api",
        "trading unavailable",
        "trading disabled",
        "trading paused",
        "trading halted",
        "execution unavailable",
        "fills unavailable",
    )
    if any(keyword in text for keyword in clob_keywords):
        return True
    account_scope = ("email", "login", "account", "authentication", "oauth", "sign in", "signin")
    if any(keyword in name for keyword in account_scope):
        return False
    return fallback_to_page
