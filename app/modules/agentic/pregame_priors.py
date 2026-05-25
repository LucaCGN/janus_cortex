from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.store import artifacts_root, session_date, write_json


PregamePriorStatus = Literal["current", "stale", "missing", "invalid"]


class PregameResearchPrior(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "pregame_research_prior_v1"
    event_id: str = Field(min_length=1)
    league: str = Field(min_length=1)
    session_date: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at_utc: datetime
    source: str = Field(min_length=1)
    teams: list[str] = Field(default_factory=list)
    likely_regimes: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    proposed_signal_config_changes: list[dict[str, Any]] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    notes: str | None = None


class OptionalPregamePriorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "optional_pregame_prior_evidence_v1"
    event_id: str = Field(min_length=1)
    league: str = Field(min_length=1)
    session_date: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: PregamePriorStatus
    liveness_blocking: bool = False
    live_disabled: bool = False
    event_control_mutation_allowed: bool = False
    strategy_plan_revision_required: bool = False
    prior_path: str | None = None
    prior_schema_version: str | None = None
    prior_generated_at_utc: datetime | None = None
    prior_expires_at_utc: datetime | None = None
    reason_codes: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    likely_regimes: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    proposed_signal_config_changes: list[dict[str, Any]] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)


def write_pregame_prior_artifact(
    prior: PregameResearchPrior,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    base = (root or artifacts_root()) / "pregame-priors" / prior.session_date / _safe_name(prior.event_id)
    timestamp = prior.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version_path = base / f"pregame_prior_{timestamp}.json"
    current_path = base / "current.json"
    payload = prior.model_dump(mode="json")
    write_json(version_path, payload)
    write_json(current_path, payload)
    return {
        "status": "stored",
        "schema_version": "pregame_research_prior_artifact_v1",
        "event_id": prior.event_id,
        "league": prior.league,
        "session_date": prior.session_date,
        "version_path": str(version_path),
        "current_path": str(current_path),
    }


def build_optional_pregame_prior_evidence(
    *,
    event_id: str,
    league: str,
    day: str | None = None,
    root: Path | None = None,
    now: datetime | None = None,
    max_age_hours: float = 18.0,
) -> OptionalPregamePriorEvidence:
    generated_at = _as_utc(now or datetime.now(timezone.utc))
    resolved_day = session_date(day)
    prior_path = _find_prior_path(event_id=event_id, day=resolved_day, root=root)
    if prior_path is None:
        return OptionalPregamePriorEvidence(
            event_id=event_id,
            league=league,
            session_date=resolved_day,
            generated_at_utc=generated_at,
            status="missing",
            reason_codes=["optional_prior_missing"],
            source_caveats=[f"{league.lower()}_pregame_prior_not_found"],
        )

    payload = _read_json(prior_path)
    if not isinstance(payload, dict):
        return OptionalPregamePriorEvidence(
            event_id=event_id,
            league=league,
            session_date=resolved_day,
            generated_at_utc=generated_at,
            status="invalid",
            prior_path=str(prior_path),
            reason_codes=["optional_prior_invalid"],
            source_caveats=["prior_json_unreadable"],
        )

    prior_generated_at = _parse_dt(payload.get("generated_at_utc") or payload.get("created_at_utc"))
    prior_expires_at = _parse_dt(payload.get("expires_at_utc"))
    reason_codes: list[str] = []
    status: PregamePriorStatus = "current"
    if prior_expires_at is not None and prior_expires_at <= generated_at:
        status = "stale"
        reason_codes.append("optional_prior_expired")
    if prior_generated_at is not None and generated_at - prior_generated_at > timedelta(hours=max_age_hours):
        status = "stale"
        reason_codes.append("optional_prior_age_exceeded")
    if prior_generated_at is None:
        status = "invalid"
        reason_codes.append("optional_prior_missing_generated_at")
    if not reason_codes:
        reason_codes.append("optional_prior_current")

    return OptionalPregamePriorEvidence(
        event_id=event_id,
        league=league,
        session_date=resolved_day,
        generated_at_utc=generated_at,
        status=status,
        prior_path=str(prior_path),
        prior_schema_version=_text(payload.get("schema_version")),
        prior_generated_at_utc=prior_generated_at,
        prior_expires_at_utc=prior_expires_at,
        reason_codes=_unique(reason_codes),
        teams=_string_list(payload.get("teams")),
        likely_regimes=_string_list(payload.get("likely_regimes")),
        risk_flags=_string_list(payload.get("risk_flags")),
        proposed_signal_config_changes=_dict_list(payload.get("proposed_signal_config_changes")),
        source_caveats=_string_list(payload.get("source_caveats")),
    )


def _find_prior_path(*, event_id: str, day: str, root: Path | None) -> Path | None:
    base = (root or artifacts_root()) / "pregame-priors" / day / _safe_name(event_id)
    current = base / "current.json"
    if current.exists():
        return current
    candidates = sorted(base.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _unique([str(item).strip() for item in value if str(item or "").strip()])
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return safe or "unknown"


__all__ = [
    "OptionalPregamePriorEvidence",
    "PregameResearchPrior",
    "PregamePriorStatus",
    "build_optional_pregame_prior_evidence",
    "write_pregame_prior_artifact",
]
