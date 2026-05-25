from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.store import append_jsonl, artifacts_root, reports_root, session_date, write_json, write_text


class ReplayCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    event_id: str
    league: str
    side: str
    candidate_type: str
    timing_policy: str
    classification: str
    expected_direction: str
    evidence_summary: str
    evidence_paths: list[str] = Field(default_factory=list)
    replay_requirements: list[str] = Field(default_factory=list)
    config_recommendation: str | None = None


class ReplayConfigReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "postgame_replay_config_review_v1"
    session_date: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#70"
    related_issues: list[str] = Field(default_factory=lambda: ["#55", "#69", "#61"])
    trading_boundary: str = "read_only_no_orders_no_worker_starts"
    input_reports: list[str] = Field(default_factory=list)
    replay_cases: list[ReplayCase] = Field(default_factory=list)
    event_control_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    entry_timing_research_updates: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_promote_no_bid_or_lottery_strategies_without_replay_edge",
        ]
    )


def replay_config_review_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "postgame-replay-config-review" / session_date(day)


def build_replay_config_review(
    *,
    day: str | None = None,
    reports_dir: Path | None = None,
    report_limit: int = 6,
) -> ReplayConfigReview:
    resolved_day = session_date(day)
    report_paths = _latest_postgame_reports(reports_dir or reports_root() / "daily-live-validation", report_limit)
    texts = [(path, _read_text(path)) for path in report_paths]
    replay_cases = _build_replay_cases(texts)
    return ReplayConfigReview(
        session_date=resolved_day,
        input_reports=[str(path) for path in report_paths],
        replay_cases=replay_cases,
        event_control_recommendations=_event_control_recommendations(replay_cases),
        entry_timing_research_updates=_entry_timing_updates(replay_cases),
        next_actions=_next_actions(replay_cases),
    )


def write_replay_config_review(
    review: ReplayConfigReview,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = review.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = replay_config_review_root(review.session_date, root=artifact_root)
    json_path = root / f"postgame_replay_config_review_{timestamp}.json"
    write_json(json_path, review.model_dump(mode="json"))
    append_jsonl(
        root / "postgame_replay_config_reviews.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": review.session_date,
            "case_count": len(review.replay_cases),
            "positive_case_count": sum(1 for case in review.replay_cases if case.expected_direction == "positive_candidate"),
            "negative_case_count": sum(1 for case in review.replay_cases if case.expected_direction == "negative_case"),
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / f"postgame_replay_config_review_{timestamp}.md"
    write_text(markdown_path, render_replay_config_review_markdown(review, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "postgame_replay_config_review_write_result_v1",
        "session_date": review.session_date,
        "case_count": len(review.replay_cases),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def render_replay_config_review_markdown(review: ReplayConfigReview, *, json_path: str | None = None) -> str:
    lines = [
        f"# Postgame Replay Config Review - {review.session_date}",
        "",
        f"- generated_at_utc: `{review.generated_at_utc.isoformat()}`",
        f"- issue: `{review.issue}`",
        f"- trading_boundary: `{review.trading_boundary}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Replay Cases"])
    for case in review.replay_cases:
        lines.append(
            f"- `{case.case_id}` `{case.expected_direction}` `{case.timing_policy}`: "
            f"{case.event_id} {case.side} - {case.evidence_summary}"
        )
    lines.extend(["", "## Event-Control Recommendations"])
    lines.extend(_bullet_lines(item["recommendation"] for item in review.event_control_recommendations))
    lines.extend(["", "## Entry-Timing Updates"])
    lines.extend(_bullet_lines(item["recommendation"] for item in review.entry_timing_research_updates))
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(review.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in review.hard_prohibitions))
    return "\n".join(lines).rstrip() + "\n"


def _latest_postgame_reports(reports_dir: Path, limit: int) -> list[Path]:
    if not reports_dir.exists():
        return []
    return sorted(reports_dir.glob("postgame_signal_review_*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _build_replay_cases(texts: list[tuple[Path, str]]) -> list[ReplayCase]:
    lower_blob = "\n".join(text.lower() for _, text in texts)
    cases: list[ReplayCase] = []

    if "atlanta" in lower_blob or "phx/atl" in lower_blob:
        cases.append(
            _case(
                texts,
                case_id="wnba-phx-atl-atlanta-comeback-low-band",
                event_id="wnba-phx-atl-2026-05-24",
                league="WNBA",
                side="Atlanta",
                candidate_type="low_band_rebound",
                timing_policy="live_low_band_after_adverse_score_gap",
                classification="replay_required",
                expected_direction="positive_candidate",
                evidence_summary="Atlanta won 82-80 after underdog/comeback windows that previously had null outcome score_gap.",
                markers=("atlanta", "phx/atl"),
                replay_requirements=[
                    "patched_wnba_score_gap_aliases",
                    "fresh_score_and_clob_window",
                    "max_price_band_unchanged_until_replay",
                ],
                config_recommendation="Keep WNBA max entry price unchanged until the Atlanta window is replay-scored.",
            )
        )
    if "dallas" in lower_blob or "dal/nyl" in lower_blob:
        cases.append(
            _case(
                texts,
                case_id="wnba-dal-nyl-dallas-q2-low-band",
                event_id="wnba-dal-nyl-2026-05-24",
                league="WNBA",
                side="Dallas",
                candidate_type="low_band_rebound",
                timing_policy="q2_low_band_after_early_deficit",
                classification="replay_required",
                expected_direction="positive_candidate",
                evidence_summary="Dallas had a clean Q2 low-band entry candidate near 23c before winning 91-76.",
                markers=("dallas", "dal/nyl"),
                replay_requirements=[
                    "patched_wnba_score_gap_aliases",
                    "2c_or_tighter_spread_gate",
                    "fillability_and_late_confirmation_split",
                ],
                config_recommendation="Replay the 23c Dallas window before considering any WNBA max-price widening.",
            )
        )
    if "seattle" in lower_blob or "wsh/sea" in lower_blob:
        cases.append(
            _case(
                texts,
                case_id="wnba-wsh-sea-seattle-q1-rebound",
                event_id="wnba-wsh-sea-2026-05-24",
                league="WNBA",
                side="Seattle",
                candidate_type="low_band_rebound",
                timing_policy="q1_rebound_after_early_deficit",
                classification="replay_required",
                expected_direction="positive_candidate",
                evidence_summary="Seattle was around 25c/26c in Q1 after an early deficit and later won 97-85.",
                markers=("seattle", "wsh/sea"),
                replay_requirements=[
                    "patched_wnba_score_gap_aliases",
                    "fresh_score_and_subsecond_clob",
                    "end_q1_conversion_comparison",
                ],
                config_recommendation="Replay Q1 and end-Q1 Seattle windows as separate entry-timing cases.",
            )
        )
    if "q4_subpenny_hype_bounce" in lower_blob or "subpenny hype-bounce" in lower_blob:
        cases.append(
            _case(
                texts,
                case_id="nba-okc-sas-thunder-q4-subpenny-negative",
                event_id="nba-okc-sas-2026-05-24",
                league="NBA",
                side="Thunder",
                candidate_type="subpenny_lottery_risk",
                timing_policy="q4_garbage_time_min_price_add",
                classification="quarantine_until_replay_edge",
                expected_direction="negative_case",
                evidence_summary="Three late Q4 Thunder subpenny buys filled before a 103-82 Spurs final and unfilled targets.",
                markers=("q4_subpenny_hype_bounce", "subpenny", "thunder"),
                replay_requirements=[
                    "duplicate_intent_cooldown",
                    "final_score_costs",
                    "target_fill_costs",
                    "unresolved_position_reconciliation",
                ],
                config_recommendation="Disable or quarantine subpenny/no-bid lottery promotion until replay proves edge after target-fill costs.",
            )
        )
    return cases


def _case(
    texts: list[tuple[Path, str]],
    *,
    markers: tuple[str, ...],
    **kwargs: Any,
) -> ReplayCase:
    evidence_paths = [str(path) for path, text in texts if any(marker in text.lower() for marker in markers)]
    return ReplayCase(evidence_paths=evidence_paths, **kwargs)


def _event_control_recommendations(cases: list[ReplayCase]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    if any(case.league == "WNBA" for case in cases):
        recommendations.append(
            {
                "target": "#69 event-control readback",
                "recommendation": "Keep deterministic WNBA replay enabled with LLM optional and hold max_price at 0.45 until low-band cases are scored.",
                "case_ids": [case.case_id for case in cases if case.league == "WNBA"],
            }
        )
    if any(case.case_id == "nba-okc-sas-thunder-q4-subpenny-negative" for case in cases):
        recommendations.append(
            {
                "target": "#69 event-control readback",
                "recommendation": "Quarantine q4_subpenny_hype_bounce/no_bid_min_price_lottery_v1 and require explicit replay edge before reactivation.",
                "case_ids": ["nba-okc-sas-thunder-q4-subpenny-negative"],
            }
        )
    return recommendations


def _entry_timing_updates(cases: list[ReplayCase]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    positive_cases = [case.case_id for case in cases if case.expected_direction == "positive_candidate"]
    if positive_cases:
        updates.append(
            {
                "issue": "#55",
                "recommendation": "Add WNBA low-band windows to the entry-timing matrix alongside NBA pregame/immediate-live/post-Q1 policies.",
                "case_ids": positive_cases,
            }
        )
    negative_cases = [case.case_id for case in cases if case.expected_direction == "negative_case"]
    if negative_cases:
        updates.append(
            {
                "issue": "#55",
                "recommendation": "Add Q4 subpenny/min-price buys as a negative timing bucket with target-fill and final-score cost accounting.",
                "case_ids": negative_cases,
            }
        )
    return updates


def _next_actions(cases: list[ReplayCase]) -> list[str]:
    if not cases:
        return ["No replay cases found in the selected reports; rerun with current postgame reports before editing config."]
    return [
        "Run fixture replay over the listed cases before changing StrategyPlan live behavior.",
        "Update #55 with entry-timing matrix rows for positive WNBA low-band candidates and the negative OKC/SAS subpenny case.",
        "Target any future enable/disable recommendation at #69 event-control artifacts instead of code toggles.",
        "Keep #61 Thunder residual reconciliation separate from replay promotion and block new NBA live enablement until resolved.",
    ]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _bullet_lines(items: list[str] | Any) -> list[str]:
    rendered = [f"- {item}" for item in items if item]
    return rendered or ["- none"]
