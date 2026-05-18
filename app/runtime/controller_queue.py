from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from app.api.db import to_jsonable
from app.runtime.local_paths import repo_root as default_repo_root
from app.runtime.local_paths import resolve_shared_root

LOCK_RESOURCE_FIELDS = {
    "issues": "issue_locks",
    "files": "file_locks",
    "modules": "module_locks",
    "events": "event_locks",
    "services": "service_locks",
    "domains": "domain_locks",
    "markets": "market_locks",
    "runtimes": "runtime_locks",
}


@dataclass(frozen=True)
class ControllerQueuePaths:
    root: Path
    active_locks: Path
    completed_locks: Path
    pass_ledger: Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def controller_queue_paths(shared_root: Path | None = None) -> ControllerQueuePaths:
    root = (shared_root or resolve_shared_root()) / "artifacts" / "final-system-controller" / "queue"
    return ControllerQueuePaths(
        root=root,
        active_locks=root / "active_locks",
        completed_locks=root / "completed_locks",
        pass_ledger=root / "pass_ledger.jsonl",
    )


def ensure_queue_dirs(paths: ControllerQueuePaths) -> None:
    paths.active_locks.mkdir(parents=True, exist_ok=True)
    paths.completed_locks.mkdir(parents=True, exist_ok=True)


def normalize_issue(issue: str | int | None) -> str | None:
    if issue is None:
        return None
    value = str(issue).strip()
    if not value:
        return None
    if "/issues/" in value:
        value = value.rsplit("/issues/", 1)[-1]
    return value.strip().lstrip("#")


def normalize_resource(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\\", "/")
    return text or None


def normalize_resources(values: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values or []:
        item = normalize_resource(value)
        if item and item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def normalize_resource_claims(
    *,
    issue: str | int | None = None,
    files: Iterable[str] | None = None,
    modules: Iterable[str] | None = None,
    events: Iterable[str] | None = None,
    services: Iterable[str] | None = None,
    domains: Iterable[str] | None = None,
    markets: Iterable[str] | None = None,
    runtimes: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    claims = {
        "issues": [],
        "files": normalize_resources(files),
        "modules": normalize_resources(modules),
        "events": normalize_resources(events),
        "services": normalize_resources(services),
        "domains": normalize_resources(domains),
        "markets": normalize_resources(markets),
        "runtimes": normalize_resources(runtimes),
    }
    normalized_issue = normalize_issue(issue)
    if normalized_issue:
        claims["issues"] = [normalized_issue]
    return claims


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_stale(lock: dict[str, Any], *, now: datetime | None = None) -> bool:
    stale_after = parse_timestamp(lock.get("stale_after_utc"))
    return bool(stale_after and stale_after <= (now or utc_now()))


def _lock_path(lock_id: str, paths: ControllerQueuePaths) -> Path:
    safe = normalize_resource(lock_id)
    if not safe:
        raise ValueError("lock_id is required")
    return paths.active_locks / f"{safe.replace('/', '__')}.json"


def _completed_lock_path(lock_id: str, paths: ControllerQueuePaths, *, now: datetime | None = None) -> Path:
    safe = normalize_resource(lock_id)
    if not safe:
        raise ValueError("lock_id is required")
    timestamp = now or utc_now()
    return paths.completed_locks / timestamp.strftime("%Y-%m-%d") / f"{safe.replace('/', '__')}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def active_locks(paths: ControllerQueuePaths | None = None) -> list[dict[str, Any]]:
    resolved_paths = paths or controller_queue_paths()
    ensure_queue_dirs(resolved_paths)
    locks: list[dict[str, Any]] = []
    for path in sorted(resolved_paths.active_locks.glob("*.json")):
        payload = _read_json(path)
        if payload:
            payload.setdefault("path", str(path))
            locks.append(payload)
    return locks


def _conflicting_values(requested: list[str], existing: list[str], *, resource_type: str) -> list[str]:
    if not requested or not existing:
        return []
    if resource_type == "files":
        return sorted(set(requested).intersection(existing))
    return sorted(set(requested).intersection(existing))


def find_conflicts(
    resource_claims: dict[str, list[str]],
    *,
    paths: ControllerQueuePaths | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for lock in active_locks(paths):
        lock_claims = lock.get("resource_claims") if isinstance(lock.get("resource_claims"), dict) else {}
        overlap: dict[str, list[str]] = {}
        for resource_type, requested in resource_claims.items():
            existing = lock_claims.get(resource_type)
            if not isinstance(existing, list):
                existing = []
            values = _conflicting_values(
                requested,
                [str(item) for item in existing],
                resource_type=resource_type,
            )
            if values:
                overlap[resource_type] = values
        if overlap:
            conflicts.append(
                {
                    "lock_id": lock.get("lock_id"),
                    "issue": lock.get("issue"),
                    "persona": lock.get("persona"),
                    "owner": lock.get("owner"),
                    "path": lock.get("path"),
                    "started_at_utc": lock.get("started_at_utc"),
                    "stale_after_utc": lock.get("stale_after_utc"),
                    "stale": is_stale(lock, now=now),
                    "overlap": overlap,
                }
            )
    return conflicts


def git_dirty_paths(*, repo_root: Path | None = None) -> list[str]:
    root = repo_root or default_repo_root()
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"git_status_failed:{result.stderr.strip() or result.returncode}"]
    paths: list[str] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        paths.append(text[3:].strip() if len(text) > 3 else text)
    return paths


def append_pass_ledger(
    entry: dict[str, Any],
    *,
    paths: ControllerQueuePaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_paths = paths or controller_queue_paths()
    ensure_queue_dirs(resolved_paths)
    timestamp = now or utc_now()
    payload = {
        "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
        **entry,
    }
    resolved_paths.pass_ledger.parent.mkdir(parents=True, exist_ok=True)
    with resolved_paths.pass_ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(to_jsonable(payload), sort_keys=True))
        handle.write("\n")
    return payload


def build_claim_payload(
    *,
    issue: str | int | None,
    persona: str,
    owner: str,
    branch: str | None,
    worktree: str | None,
    resource_claims: dict[str, list[str]],
    lock_id: str | None = None,
    stale_after_minutes: int = 120,
    now: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = now or utc_now()
    normalized_issue = normalize_issue(issue)
    generated_lock_id = lock_id or (
        f"issue{normalized_issue or 'none'}-{normalize_resource(persona) or 'persona'}-"
        f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    return {
        "schema_version": "janus_controller_active_lock_v1",
        "lock_id": generated_lock_id,
        "status": "active",
        "issue": normalized_issue,
        "persona": persona,
        "owner": owner,
        "branch": branch,
        "worktree": worktree,
        "resource_claims": resource_claims,
        "started_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "last_update_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "stale_after_utc": (timestamp + timedelta(minutes=stale_after_minutes)).isoformat().replace("+00:00", "Z"),
        "metadata": metadata or {},
    }


def check_claim(
    *,
    issue: str | int | None = None,
    files: Iterable[str] | None = None,
    modules: Iterable[str] | None = None,
    events: Iterable[str] | None = None,
    services: Iterable[str] | None = None,
    domains: Iterable[str] | None = None,
    markets: Iterable[str] | None = None,
    runtimes: Iterable[str] | None = None,
    paths: ControllerQueuePaths | None = None,
    now: datetime | None = None,
    require_clean_worktree: bool = False,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    resource_claims = normalize_resource_claims(
        issue=issue,
        files=files,
        modules=modules,
        events=events,
        services=services,
        domains=domains,
        markets=markets,
        runtimes=runtimes,
    )
    conflicts = find_conflicts(resource_claims, paths=paths, now=now)
    dirty_paths = git_dirty_paths(repo_root=repo_root) if require_clean_worktree else []
    stale_conflicts = [item for item in conflicts if item.get("stale")]
    active_conflicts = [item for item in conflicts if not item.get("stale")]
    if active_conflicts:
        status = "blocked_duplicate_lock"
    elif stale_conflicts:
        status = "blocked_stale_lock"
    elif dirty_paths:
        status = "blocked_dirty_worktree"
    else:
        status = "claim_available"
    return {
        "status": status,
        "ok": status == "claim_available",
        "resource_claims": resource_claims,
        "conflicts": conflicts,
        "dirty_paths": dirty_paths,
    }


def claim_lock(
    *,
    issue: str | int | None,
    persona: str,
    owner: str,
    branch: str | None = None,
    worktree: str | None = None,
    files: Iterable[str] | None = None,
    modules: Iterable[str] | None = None,
    events: Iterable[str] | None = None,
    services: Iterable[str] | None = None,
    domains: Iterable[str] | None = None,
    markets: Iterable[str] | None = None,
    runtimes: Iterable[str] | None = None,
    lock_id: str | None = None,
    stale_after_minutes: int = 120,
    paths: ControllerQueuePaths | None = None,
    now: datetime | None = None,
    require_clean_worktree: bool = False,
    repo_root: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_paths = paths or controller_queue_paths()
    ensure_queue_dirs(resolved_paths)
    check = check_claim(
        issue=issue,
        files=files,
        modules=modules,
        events=events,
        services=services,
        domains=domains,
        markets=markets,
        runtimes=runtimes,
        paths=resolved_paths,
        now=now,
        require_clean_worktree=require_clean_worktree,
        repo_root=repo_root,
    )
    if not check["ok"]:
        append_pass_ledger(
            {
                "outcome": check["status"],
                "issue": normalize_issue(issue),
                "selected_persona": persona,
                "owner": owner,
                "resource_claims": check["resource_claims"],
                "conflicts": check["conflicts"],
                "dirty_paths": check["dirty_paths"],
            },
            paths=resolved_paths,
            now=now,
        )
        return check

    payload = build_claim_payload(
        issue=issue,
        persona=persona,
        owner=owner,
        branch=branch,
        worktree=worktree,
        resource_claims=check["resource_claims"],
        lock_id=lock_id,
        stale_after_minutes=stale_after_minutes,
        now=now,
        metadata=metadata,
    )
    path = _lock_path(str(payload["lock_id"]), resolved_paths)
    if path.exists():
        return {
            "status": "blocked_existing_lock_id",
            "ok": False,
            "path": str(path),
            "lock_id": payload["lock_id"],
        }
    path.write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
    append_pass_ledger(
        {
            "outcome": "claimed",
            "issue": payload["issue"],
            "selected_persona": persona,
            "owner": owner,
            "lock_id": payload["lock_id"],
            "resource_claims": check["resource_claims"],
            "path": str(path),
        },
        paths=resolved_paths,
        now=now,
    )
    return {"status": "claimed", "ok": True, "lock": payload, "path": str(path)}


def release_lock(
    lock_id: str,
    *,
    outcome: str,
    material_outputs: Iterable[str] | None = None,
    evidence_links: Iterable[str] | None = None,
    paths: ControllerQueuePaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_paths = paths or controller_queue_paths()
    ensure_queue_dirs(resolved_paths)
    active_path = _lock_path(lock_id, resolved_paths)
    payload = _read_json(active_path)
    if not payload:
        result = {"status": "missing_lock", "ok": False, "lock_id": lock_id, "path": str(active_path)}
        append_pass_ledger(result, paths=resolved_paths, now=now)
        return result

    timestamp = now or utc_now()
    payload["status"] = "released"
    payload["released_at_utc"] = timestamp.isoformat().replace("+00:00", "Z")
    payload["release_outcome"] = outcome
    payload["material_outputs"] = list(material_outputs or [])
    payload["evidence_links"] = list(evidence_links or [])
    completed_path = _completed_lock_path(lock_id, resolved_paths, now=timestamp)
    completed_path.parent.mkdir(parents=True, exist_ok=True)
    completed_path.write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
    active_path.unlink()
    append_pass_ledger(
        {
            "outcome": outcome,
            "issue": payload.get("issue"),
            "selected_persona": payload.get("persona"),
            "owner": payload.get("owner"),
            "lock_id": lock_id,
            "material_outputs": list(material_outputs or []),
            "evidence_links": list(evidence_links or []),
            "completed_path": str(completed_path),
        },
        paths=resolved_paths,
        now=timestamp,
    )
    return {"status": "released", "ok": True, "lock": payload, "path": str(completed_path)}


def queue_status(*, paths: ControllerQueuePaths | None = None, now: datetime | None = None) -> dict[str, Any]:
    locks = active_locks(paths)
    return {
        "status": "ok",
        "active_lock_count": len(locks),
        "stale_lock_count": sum(1 for lock in locks if is_stale(lock, now=now)),
        "active_locks": locks,
        "queue_root": str((paths or controller_queue_paths()).root),
    }


def reset_queue_for_tests(paths: ControllerQueuePaths) -> None:
    if paths.root.exists():
        shutil.rmtree(paths.root)


__all__ = [
    "ControllerQueuePaths",
    "active_locks",
    "append_pass_ledger",
    "check_claim",
    "claim_lock",
    "controller_queue_paths",
    "find_conflicts",
    "normalize_issue",
    "queue_status",
    "release_lock",
    "reset_queue_for_tests",
]
