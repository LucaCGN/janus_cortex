from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.local_paths import resolve_shared_root


GLOBAL_PORTFOLIO_KILL_SWITCH_SCHEMA = "global_portfolio_kill_switch_clearance_v1"
GLOBAL_PORTFOLIO_KILL_SWITCH_FILE_ENV = "JANUS_GLOBAL_PORTFOLIO_KILL_SWITCH_FILE"
GLOBAL_PORTFOLIO_KILL_SWITCH_REQUIRED_SCOPE = "global-portfolio"


def default_global_portfolio_kill_switch_path() -> Path:
    return resolve_shared_root() / "handoffs" / "global-portfolio-manager" / "kill_switch.json"


def resolve_global_portfolio_kill_switch_path(path: str | Path | None = None) -> Path:
    configured = path or os.getenv(GLOBAL_PORTFOLIO_KILL_SWITCH_FILE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return default_global_portfolio_kill_switch_path()


def load_global_portfolio_kill_switch_clearance(
    path: str | Path | None = None,
    *,
    checked_at_utc: datetime | None = None,
) -> dict[str, Any]:
    resolved_path = resolve_global_portfolio_kill_switch_path(path)
    checked_at = _utc_text(checked_at_utc)
    if not resolved_path.exists():
        return _blocked_state(
            resolved_path,
            checked_at_utc=checked_at,
            blocked_reasons=["global_portfolio_kill_switch_file_missing"],
            source=f"missing_runtime_file:{resolved_path}",
        )

    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return _blocked_state(
            resolved_path,
            checked_at_utc=checked_at,
            blocked_reasons=["global_portfolio_kill_switch_file_unreadable"],
            source=f"unreadable_runtime_file:{resolved_path}",
            error=str(exc),
        )

    if not isinstance(raw, dict):
        return _blocked_state(
            resolved_path,
            checked_at_utc=checked_at,
            blocked_reasons=["global_portfolio_kill_switch_payload_not_object"],
            source=f"invalid_runtime_file:{resolved_path}",
        )

    return _normalize_kill_switch_payload(raw, resolved_path=resolved_path, checked_at_utc=checked_at)


def _normalize_kill_switch_payload(
    raw: dict[str, Any],
    *,
    resolved_path: Path,
    checked_at_utc: str,
) -> dict[str, Any]:
    configured_clear = _truthy(raw.get("clear"))
    source = str(raw.get("source") or f"runtime_file:{resolved_path}").strip()
    blocked_reasons = _blocked_reasons(raw.get("blocked_reasons"))
    scope = str(raw.get("scope") or GLOBAL_PORTFOLIO_KILL_SWITCH_REQUIRED_SCOPE).strip()
    if scope != GLOBAL_PORTFOLIO_KILL_SWITCH_REQUIRED_SCOPE:
        blocked_reasons.append("global_portfolio_kill_switch_scope_mismatch")
    if not configured_clear:
        blocked_reasons.append("global_portfolio_kill_switch_not_clear")
    if not source:
        blocked_reasons.append("global_portfolio_kill_switch_source_missing")

    unique_blockers = _unique(blocked_reasons)
    clear = bool(configured_clear and not unique_blockers)
    return {
        "schema_version": GLOBAL_PORTFOLIO_KILL_SWITCH_SCHEMA,
        "scope": scope,
        "clear": clear,
        "configured_clear": configured_clear,
        "source": source,
        "checked_at_utc": checked_at_utc,
        "source_updated_at_utc": raw.get("updated_at_utc") or raw.get("checked_at_utc"),
        "blocked_reasons": unique_blockers,
        "required_for_non_dry_run": True,
        "runtime_state_path": str(resolved_path),
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
    }


def _blocked_state(
    resolved_path: Path,
    *,
    checked_at_utc: str,
    blocked_reasons: list[str],
    source: str,
    error: str | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version": GLOBAL_PORTFOLIO_KILL_SWITCH_SCHEMA,
        "scope": GLOBAL_PORTFOLIO_KILL_SWITCH_REQUIRED_SCOPE,
        "clear": False,
        "configured_clear": False,
        "source": source,
        "checked_at_utc": checked_at_utc,
        "source_updated_at_utc": None,
        "blocked_reasons": _unique(blocked_reasons),
        "required_for_non_dry_run": True,
        "runtime_state_path": str(resolved_path),
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
    }
    if error:
        state["error"] = error
    return state


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "clear"}


def _blocked_reasons(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.replace("\n", ",").replace(";", ",").split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    return [str(candidate).strip() for candidate in candidates if str(candidate).strip()]


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values


def _utc_text(value: datetime | None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "GLOBAL_PORTFOLIO_KILL_SWITCH_FILE_ENV",
    "GLOBAL_PORTFOLIO_KILL_SWITCH_SCHEMA",
    "default_global_portfolio_kill_switch_path",
    "load_global_portfolio_kill_switch_clearance",
    "resolve_global_portfolio_kill_switch_path",
]
