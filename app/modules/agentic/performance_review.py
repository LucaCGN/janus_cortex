from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.store import (
    append_jsonl,
    artifacts_root,
    read_json,
    reports_root,
    session_date,
    write_json,
    write_text,
)


class ProjectChiefReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    source_type: str
    summary: str = ""


class ProjectChiefRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str
    priority: str
    action: str
    reason: str
    evidence_paths: list[str] = Field(default_factory=list)


class ProjectChiefReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "project_chief_performance_review_v1"
    session_date: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trading_boundary: str = "read_only_no_orders_no_worker_starts"
    input_artifacts: list[ProjectChiefReviewInput] = Field(default_factory=list)
    strategy_score_deltas: list[dict[str, Any]] = Field(default_factory=list)
    missed_opportunity_summary: list[dict[str, Any]] = Field(default_factory=list)
    technical_blockers: list[dict[str, Any]] = Field(default_factory=list)
    superseded_findings: list[dict[str, Any]] = Field(default_factory=list)
    config_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    issue_actions: list[ProjectChiefRecommendation] = Field(default_factory=list)
    next_priority_queue: list[ProjectChiefRecommendation] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_treat_obsidian_or_github_text_as_live_trading_truth",
        ]
    )


def project_chief_review_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "project-chief-review" / session_date(day)


def build_project_chief_review(
    *,
    day: str | None = None,
    report_limit: int = 3,
    reports_dir: Path | None = None,
    artifact_root: Path | None = None,
    issue_task_register_path: Path | None = None,
) -> ProjectChiefReview:
    resolved_day = session_date(day)
    postgame_reports = _latest_postgame_reports(reports_dir or reports_root() / "daily-live-validation", report_limit)
    event_controls = _latest_event_control_paths(artifact_root or artifacts_root(), resolved_day)
    register_state = _issue_task_register_state(
        _read_text(issue_task_register_path or _default_issue_task_register_path())
    )

    input_artifacts = [
        ProjectChiefReviewInput(
            path=str(path),
            source_type="postgame_signal_review",
            summary=_first_non_empty_line(path),
        )
        for path in postgame_reports
    ]
    input_artifacts.extend(
        ProjectChiefReviewInput(
            path=str(path),
            source_type="event_control_readback",
            summary=_event_control_summary(path),
        )
        for path in event_controls
    )

    texts = [(path, _read_text(path)) for path in postgame_reports]
    missed = _missed_opportunities(texts, register_state)
    blockers, superseded = _technical_blockers(texts, register_state)
    issue_actions = _issue_actions(missed, blockers, event_controls, register_state)
    return ProjectChiefReview(
        session_date=resolved_day,
        input_artifacts=input_artifacts,
        strategy_score_deltas=_strategy_score_deltas(missed, blockers),
        missed_opportunity_summary=missed,
        technical_blockers=blockers,
        superseded_findings=superseded,
        config_recommendations=_config_recommendations(missed, blockers, event_controls, register_state),
        issue_actions=issue_actions,
        next_priority_queue=issue_actions[:5],
    )


def write_project_chief_review(
    review: ProjectChiefReview,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = review.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = project_chief_review_root(review.session_date, root=artifact_root)
    json_path = root / f"project_chief_review_{timestamp}.json"
    write_json(json_path, review.model_dump(mode="json"))
    append_jsonl(
        root / "project_chief_reviews.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": review.session_date,
            "issue_action_count": len(review.issue_actions),
            "technical_blocker_count": len(review.technical_blockers),
            "missed_opportunity_count": len(review.missed_opportunity_summary),
            "path": str(json_path),
        },
    )

    markdown_path = (report_dir or reports_root() / "daily-live-validation") / f"project_chief_review_{timestamp}.md"
    write_text(markdown_path, render_project_chief_review_markdown(review, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "project_chief_review_write_result_v1",
        "session_date": review.session_date,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def render_project_chief_review_markdown(review: ProjectChiefReview, *, json_path: str | None = None) -> str:
    lines = [
        f"# Project Chief Performance Review - {review.session_date}",
        "",
        f"- generated_at_utc: `{review.generated_at_utc.isoformat()}`",
        f"- trading_boundary: `{review.trading_boundary}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Inputs"])
    lines.extend(_bullet_lines(f"{item.source_type}: `{item.path}` - {item.summary}" for item in review.input_artifacts))
    lines.extend(["", "## Strategy Score Deltas"])
    lines.extend(_bullet_lines(f"`{item['strategy_family']}`: {item['delta']} ({item['reason']})" for item in review.strategy_score_deltas))
    lines.extend(["", "## Missed Opportunities"])
    lines.extend(_bullet_lines(f"`{item['event_id']}`: {item['summary']}" for item in review.missed_opportunity_summary))
    lines.extend(["", "## Technical Blockers"])
    lines.extend(_bullet_lines(f"`{item['blocker']}` -> {item['next_action']}" for item in review.technical_blockers))
    lines.extend(["", "## Superseded Findings"])
    lines.extend(
        _bullet_lines(
            f"`{item['finding']}` suppressed by `{item['resolution']}` from `{item['evidence_path']}`"
            for item in review.superseded_findings
        )
    )
    lines.extend(["", "## Next Priority Queue"])
    lines.extend(_bullet_lines(f"{item.issue} `{item.priority}`: {item.action}" for item in review.next_priority_queue))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in review.hard_prohibitions))
    return "\n".join(lines).rstrip() + "\n"


def _latest_postgame_reports(reports_dir: Path, limit: int) -> list[Path]:
    if not reports_dir.exists():
        return []
    return sorted(reports_dir.glob("postgame_signal_review_*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _latest_event_control_paths(root: Path, day: str) -> list[Path]:
    event_control_root = root / "event-controls" / day
    if not event_control_root.exists():
        return []
    return sorted(event_control_root.glob("*/current.json"))


def _missed_opportunities(texts: list[tuple[Path, str]], register_state: dict[str, Any]) -> list[dict[str, Any]]:
    missed: list[dict[str, Any]] = []
    for path, text in texts:
        lower = text.lower()
        if "atlanta" in lower or "phx/atl" in lower:
            missed.append(
                {
                    "event_id": "wnba-phx-atl-2026-05-24",
                    "summary": "Atlanta comeback replay candidate from underdog prices to final winner.",
                    "evidence_paths": [str(path)],
                }
            )
        if "dallas" in lower or "dal/nyl" in lower:
            missed.append(
                {
                    "event_id": "wnba-dal-nyl-2026-05-24",
                    "summary": "Dallas low-band entry replay candidate before final winner.",
                    "evidence_paths": [str(path)],
                }
            )
        if "seattle" in lower or "wsh/sea" in lower:
            missed.append(
                {
                    "event_id": "wnba-wsh-sea-2026-05-24",
                    "summary": "Seattle Q1 rebound replay candidate with fresh score, CLOB, and 1c spread.",
                    "evidence_paths": [str(path)],
                }
            )
        if (
            "okc/sas" in lower
            and "unresolved" in lower
            and not _resolved(register_state, "nba_thunder_exposure_reconciliation")
        ):
            missed.append(
                {
                    "event_id": "nba-okc-sas-2026-05-24",
                    "summary": "OKC/SAS final review remains gated by unresolved Thunder exposure evidence.",
                    "evidence_paths": [str(path)],
                }
            )
    return _dedupe_by_event(missed)


def _technical_blockers(
    texts: list[tuple[Path, str]],
    register_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    for path, text in texts:
        lower = text.lower()
        if "score_gap" in lower:
            if _resolved(register_state, "wnba_score_gap_null"):
                superseded.append(_superseded("wnba_score_gap_null", path, "JIT-70-03"))
            else:
                blockers.append(
                    {
                        "blocker": "wnba_score_gap_null",
                        "next_action": "repair WNBA outcome-level score-gap normalization and replay the three final WNBA candidates",
                        "issue": "#70",
                        "evidence_paths": [str(path)],
                    }
                )
        if "http 500" in lower or "typeerror" in lower:
            if _resolved(register_state, "event_review_bundle_export_http_500"):
                superseded.append(_superseded("event_review_bundle_export_http_500", path, "JIT-70-02"))
            else:
                blockers.append(
                    {
                        "blocker": "event_review_bundle_export_http_500",
                        "next_action": "fix review-bundle export TypeError before using bundle output as project-chief input",
                        "issue": "#70",
                        "evidence_paths": [str(path)],
                    }
                )
        if "unresolved direct thunder exposure" in lower or "unresolved thunder exposure" in lower:
            if _resolved(register_state, "nba_thunder_exposure_reconciliation"):
                superseded.append(_superseded("nba_thunder_exposure_reconciliation", path, "JIT-70-06"))
            else:
                blockers.append(
                    {
                        "blocker": "nba_thunder_exposure_reconciliation",
                        "next_action": "finish #61 direct CLOB target/fill/position attribution before fresh NBA live enablement",
                        "issue": "#61",
                        "evidence_paths": [str(path)],
                    }
                )
    return _dedupe_by_key(blockers, "blocker"), _dedupe_superseded(superseded)


def _issue_actions(
    missed: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    event_controls: list[Path],
    register_state: dict[str, Any],
) -> list[ProjectChiefRecommendation]:
    actions: list[ProjectChiefRecommendation] = []
    if any(item.get("blocker") == "wnba_score_gap_null" for item in blockers):
        actions.append(
            ProjectChiefRecommendation(
                issue="#70",
                priority="P1",
                action="create or work a replay/config slice for WNBA score-gap normalization",
                reason="Three WNBA missed-signal candidates are blocked by null outcome-level score_gap.",
                evidence_paths=_evidence_for(blockers, "wnba_score_gap_null"),
            )
        )
    if any(item.get("blocker") == "event_review_bundle_export_http_500" for item in blockers):
        actions.append(
            ProjectChiefRecommendation(
                issue="#70",
                priority="P1",
                action="fix review-bundle export TypeError",
                reason="Project-chief review needs structured event bundles instead of markdown-only evidence.",
                evidence_paths=_evidence_for(blockers, "event_review_bundle_export_http_500"),
            )
        )
    if any(item["event_id"].startswith("wnba-") for item in missed) and not _resolved(
        register_state,
        "wnba_low_band_replay_synchronized",
    ):
        actions.append(
            ProjectChiefRecommendation(
                issue="#55",
                priority="P1",
                action="feed WNBA low-band rebound candidates into entry-timing research cases",
                reason="Atlanta, Dallas, and Seattle final winners each had replayable low-band windows.",
                evidence_paths=sorted({path for item in missed for path in item.get("evidence_paths", [])}),
            )
        )
    if event_controls:
        actions.append(
            ProjectChiefRecommendation(
                issue="#69",
                priority="P1",
                action="use runtime event-control readbacks as the config-change target for project-chief recommendations",
                reason="Event-control artifacts now expose attributable signal toggles and safe cap parameters.",
                evidence_paths=[str(path) for path in event_controls],
            )
        )
    actions.append(
        ProjectChiefRecommendation(
            issue="#71",
            priority="P1",
            action="publish daily project-chief artifact and update issue-task register",
            reason="Close the loop from postgame signal review to next development priorities.",
            evidence_paths=[],
        )
    )
    return actions


def _strategy_score_deltas(missed: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deltas = []
    if any(item["event_id"].startswith("wnba-") for item in missed):
        deltas.append(
            {
                "strategy_family": "wnba_low_band_rebound",
                "delta": "positive_candidate_requires_replay",
                "reason": "Multiple WNBA final winners had low-band or comeback windows but no executable Janus signal.",
            }
        )
    if any(item.get("blocker") == "wnba_score_gap_null" for item in blockers):
        deltas.append(
            {
                "strategy_family": "wnba_score_gap_gate",
                "delta": "negative_blocker",
                "reason": "Outcome-level score_gap stayed null despite score evidence.",
            }
        )
    if any(item.get("blocker") == "nba_thunder_exposure_reconciliation" for item in blockers):
        deltas.append(
            {
                "strategy_family": "nba_protect_only_targeting",
                "delta": "risk_blocker",
                "reason": "Unresolved direct exposure blocks new live enablement regardless of signal quality.",
            }
        )
    return deltas


def _config_recommendations(
    missed: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    event_controls: list[Path],
    register_state: dict[str, Any],
) -> list[dict[str, Any]]:
    recommendations = []
    if missed:
        replay_done = _resolved(register_state, "wnba_low_band_replay_synchronized")
        recommendations.append(
            {
                "config_surface": "event-control replay candidate set",
                "recommendation": (
                    "preserve existing #55/#70 replay conclusions and avoid reopening old score-gap/null blockers"
                    if replay_done
                    else "replay Atlanta, Dallas, and Seattle WNBA candidates with deterministic signals enabled and LLM optional"
                ),
                "evidence_paths": sorted({path for item in missed for path in item.get("evidence_paths", [])}),
            }
        )
    if blockers:
        recommendations.append(
            {
                "config_surface": "postgame review blockers",
                "recommendation": "treat score-gap null and review-bundle export errors as implementation blockers before promotion",
                "evidence_paths": sorted({path for item in blockers for path in item.get("evidence_paths", [])}),
            }
        )
    if event_controls:
        recommendations.append(
            {
                "config_surface": "runtime event controls",
                "recommendation": "target future project-chief config recommendations at event-control artifacts instead of code edits",
                "evidence_paths": [str(path) for path in event_controls],
            }
        )
    return recommendations


def _event_control_summary(path: Path) -> str:
    payload = read_json(path) or {}
    toggles = payload.get("signal_source_toggles", {})
    params = payload.get("parameters", {})
    return f"toggles={toggles}; parameters={params}"


def _first_non_empty_line(path: Path) -> str:
    for line in _read_text(path).splitlines():
        normalized = line.strip(" #")
        if normalized:
            return normalized[:240]
    return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _default_issue_task_register_path() -> Path:
    return Path(__file__).resolve().parents[3] / "app" / "docs" / "planning" / "current" / "final_system" / "automation" / "issue_task_register.md"


def _issue_task_register_state(text: str) -> dict[str, Any]:
    done_tasks: set[str] = set()
    for line in text.splitlines():
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 3:
            continue
        task_id, _issue, status = parts[:3]
        if task_id.startswith("JIT-") and status == "done":
            done_tasks.add(task_id)
    return {
        "done_tasks": done_tasks,
        "resolved_findings": {
            "event_review_bundle_export_http_500": {"JIT-70-02"},
            "wnba_score_gap_null": {"JIT-70-03"},
            "nba_thunder_exposure_reconciliation": {"JIT-70-06", "JIT-61-01"},
            "wnba_low_band_replay_synchronized": {"JIT-55-05", "JIT-70-05"},
        },
    }


def _resolved(register_state: dict[str, Any], finding: str) -> bool:
    done_tasks = register_state.get("done_tasks", set())
    required = register_state.get("resolved_findings", {}).get(finding, set())
    return any(task in done_tasks for task in required)


def _superseded(finding: str, path: Path, resolution: str) -> dict[str, Any]:
    return {
        "finding": finding,
        "resolution": resolution,
        "evidence_path": str(path),
    }


def _dedupe_superseded(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("finding", "")), str(item.get("evidence_path", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _dedupe_by_event(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        event_id = str(item.get("event_id", ""))
        if event_id in seen:
            continue
        seen.add(event_id)
        unique.append(item)
    return unique


def _dedupe_by_key(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        value = str(item.get(key, ""))
        if value not in merged:
            merged[value] = item
            continue
        evidence = set(merged[value].get("evidence_paths", []))
        evidence.update(item.get("evidence_paths", []))
        merged[value]["evidence_paths"] = sorted(evidence)
    return list(merged.values())


def _evidence_for(blockers: list[dict[str, Any]], blocker: str) -> list[str]:
    evidence: set[str] = set()
    for item in blockers:
        if item.get("blocker") == blocker:
            evidence.update(str(path) for path in item.get("evidence_paths", []))
    return sorted(evidence)


def _bullet_lines(values: Any) -> list[str]:
    items = [value for value in values if value]
    if not items:
        return ["- none"]
    return [f"- {value}" for value in items]


__all__ = [
    "ProjectChiefRecommendation",
    "ProjectChiefReview",
    "ProjectChiefReviewInput",
    "build_project_chief_review",
    "project_chief_review_root",
    "render_project_chief_review_markdown",
    "write_project_chief_review",
]
