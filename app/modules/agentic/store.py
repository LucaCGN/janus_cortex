from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.api.db import to_jsonable
from app.modules.agentic.contracts import LiveSignal, StrategyPlan
from app.modules.agentic.repository import (
    get_agentic_database_status,
    resolve_catalog_event_strategy_plan_aliases,
    try_persist_strategy_plan,
)
from app.runtime.local_paths import resolve_shared_root


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def session_date(value: str | None = None) -> str:
    return value or date.today().isoformat()


def shared_root() -> Path:
    return resolve_shared_root()


def artifacts_root() -> Path:
    return shared_root() / "artifacts"


def reports_root() -> Path:
    return shared_root() / "reports"


def handoffs_root() -> Path:
    return shared_root() / "handoffs"


def strategy_plan_root(day: str | None = None) -> Path:
    return artifacts_root() / "strategy-plans" / session_date(day)


def ops_artifact_root(day: str | None = None) -> Path:
    return artifacts_root() / "ops" / session_date(day)


def live_signal_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "live-signals" / session_date(day)


def _json_default(value: Any) -> Any:
    return to_jsonable(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=_json_default))
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else {"items": payload}


def write_live_signals(
    signals: list[LiveSignal],
    *,
    day: str | None = None,
    root: Path | None = None,
    source: str = "janus",
) -> dict[str, Any]:
    resolved_day = session_date(day)
    written: list[dict[str, Any]] = []
    for signal in signals:
        payload = signal.model_dump(mode="json")
        event_dir = live_signal_root(resolved_day, root=root) / _safe_name(signal.event_id)
        path = event_dir / f"{signal.signal_id}.json"
        write_json(path, payload)
        append_jsonl(
            live_signal_root(resolved_day, root=root) / "live_signals.jsonl",
            {
                "recorded_at_utc": utc_now().isoformat(),
                "session_date": resolved_day,
                "source": source,
                "signal_id": signal.signal_id,
                "event_id": signal.event_id,
                "signal_type": signal.signal_type,
                "signal_source": signal.source,
                "execution_boundary": signal.execution_boundary,
                "path": str(path),
            },
        )
        written.append(
            {
                "signal_id": signal.signal_id,
                "event_id": signal.event_id,
                "signal_type": signal.signal_type,
                "signal_source": signal.source,
                "path": str(path),
            }
        )
    return {
        "status": "stored",
        "schema_version": "live_signal_artifact_batch_v1",
        "session_date": resolved_day,
        "signal_count": len(signals),
        "signals": written,
        "jsonl_path": str(live_signal_root(resolved_day, root=root) / "live_signals.jsonl"),
    }


def append_pregame_research(
    *,
    day: str | None,
    research_markdown: str | None,
    research_path: str | None,
    source: str,
    event_ids: list[str],
    notes: str | None,
) -> dict[str, Any]:
    resolved_day = session_date(day)
    content = research_markdown or _read_optional_text(research_path)
    if not content:
        return {
            "status": "skipped",
            "reason": "no_research_markdown_or_readable_path",
            "path": str(reports_root() / "daily-live-validation" / f"pregame_research_{resolved_day}.md"),
        }

    path = reports_root() / "daily-live-validation" / f"pregame_research_{resolved_day}.md"
    now = utc_now().isoformat()
    section = "\n".join(
        [
            "",
            f"## Submission - {now}",
            "",
            f"- source: `{source}`",
            f"- event_ids: `{', '.join(event_ids) if event_ids else 'all'}`",
            f"- original_path: `{research_path or ''}`",
            f"- notes: {notes or ''}",
            "",
            content.strip(),
            "",
        ]
    )
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        write_text(path, existing.rstrip() + "\n" + section)
    else:
        write_text(path, f"# Pregame Research - {resolved_day}\n{section}")
    append_jsonl(
        reports_root() / "daily-live-validation" / "pregame_research_submissions.jsonl",
        {
            "timestamp_utc": now,
            "session_date": resolved_day,
            "source": source,
            "event_ids": event_ids,
            "path": str(path),
            "original_path": research_path,
            "char_count": len(content),
        },
    )
    return {"status": "stored", "path": str(path), "char_count": len(content)}


def write_strategy_plan(plan: StrategyPlan, *, day: str | None = None) -> dict[str, Any]:
    generated_at = plan.generated_at_utc.astimezone(timezone.utc)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    event_dir = strategy_plan_root(day) / _safe_name(plan.event_id)
    version_path = event_dir / f"plan_{timestamp}.json"
    current_path = event_dir / "current.json"
    payload = plan.model_dump(mode="json")
    write_json(version_path, payload)
    write_json(current_path, payload)
    append_jsonl(
        strategy_plan_root(day) / "strategy_plan_versions.jsonl",
        {
            "timestamp_utc": utc_now().isoformat(),
            "event_id": plan.event_id,
            "market_id": plan.market_id,
            "plan_owner": plan.plan_owner,
            "strategy_count": len(plan.active_strategies),
            "path": str(version_path),
        },
    )
    db_persistence = try_persist_strategy_plan(plan)
    return {
        "status": "stored",
        "event_id": plan.event_id,
        "market_id": plan.market_id,
        "strategy_count": len(plan.active_strategies),
        "version_path": str(version_path),
        "current_path": str(current_path),
        "db_persistence": db_persistence,
    }


def load_current_strategy_plan(event_id: str, *, day: str | None = None) -> dict[str, Any] | None:
    current_path = strategy_plan_root(day) / _safe_name(event_id) / "current.json"
    return read_json(current_path)


def load_current_strategy_plan_for_event(
    event_id: str,
    *,
    day: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    lookup_event_ids = _unique_strings([event_id])
    for lookup_event_id in lookup_event_ids:
        current_plan = load_current_strategy_plan(lookup_event_id, day=day)
        if current_plan is not None:
            return current_plan, lookup_event_id, lookup_event_ids
    lookup_event_ids = _unique_strings([event_id, *resolve_catalog_event_strategy_plan_aliases(event_id)])
    for lookup_event_id in lookup_event_ids[1:]:
        current_plan = load_current_strategy_plan(lookup_event_id, day=day)
        if current_plan is not None:
            return current_plan, lookup_event_id, lookup_event_ids
    return None, None, lookup_event_ids


def record_ops_stage(stage: str, payload: dict[str, Any], *, day: str | None = None) -> dict[str, Any]:
    now = utc_now()
    root = ops_artifact_root(day)
    safe_stage = _safe_name(stage)
    stage_payload = {
        "stage": stage,
        "recorded_at_utc": now.isoformat(),
        **payload,
    }
    path = root / f"{safe_stage}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(path, stage_payload)
    append_jsonl(root / "ops_events.jsonl", {**stage_payload, "path": str(path)})
    return {"status": "recorded", "stage": stage, "path": str(path), "recorded_at_utc": now.isoformat()}


def latest_handoff_statuses() -> dict[str, dict[str, Any]]:
    root = handoffs_root()
    statuses: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return statuses
    for path in sorted(root.glob("*/status.md")):
        lane = path.parent.name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        statuses[lane] = {
            "path": str(path),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "preview": "\n".join(text.splitlines()[:12]),
        }
    return statuses


def build_ops_status() -> dict[str, Any]:
    plans = sorted(strategy_plan_root().glob("*/current.json")) if strategy_plan_root().exists() else []
    return {
        "status": "ok",
        "timestamp_utc": utc_now().isoformat(),
        "local_roots": {
            "shared_root": str(shared_root()),
            "artifacts_root": str(artifacts_root()),
            "reports_root": str(reports_root()),
            "handoffs_root": str(handoffs_root()),
        },
        "strategy_plans": {
            "current_plan_count_today": len(plans),
            "current_plan_paths": [str(path) for path in plans],
        },
        "database": get_agentic_database_status(),
        "handoffs": latest_handoff_statuses(),
    }


def build_event_agent_context(event_id: str, *, day: str | None = None) -> dict[str, Any]:
    current_plan, resolved_strategy_plan_event_id, strategy_plan_lookup_event_ids = load_current_strategy_plan_for_event(
        event_id,
        day=day,
    )
    report_dir = reports_root() / "daily-live-validation"
    pregame_path = report_dir / f"pregame_research_{session_date(day)}.md"
    live_plan_path = report_dir / f"live_test_plan_{session_date(day)}.md"
    return {
        "event_id": event_id,
        "timestamp_utc": utc_now().isoformat(),
        "strategy_plan_lookup_event_ids": strategy_plan_lookup_event_ids,
        "resolved_strategy_plan_event_id": resolved_strategy_plan_event_id,
        "current_strategy_plan": current_plan,
        "pregame_research": _read_text_preview(pregame_path),
        "live_test_plan": _read_text_preview(live_plan_path),
        "handoffs": latest_handoff_statuses(),
    }


def _read_text_preview(path: Path, *, max_chars: int = 12000) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "text": ""}
    text = path.read_text(encoding="utf-8")
    return {"path": str(path), "exists": True, "text": text[:max_chars]}


def _read_optional_text(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))[:160] or "unknown"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


__all__ = [
    "append_jsonl",
    "append_pregame_research",
    "build_event_agent_context",
    "build_ops_status",
    "load_current_strategy_plan",
    "load_current_strategy_plan_for_event",
    "live_signal_root",
    "record_ops_stage",
    "write_live_signals",
    "write_json",
    "write_strategy_plan",
    "write_text",
]
