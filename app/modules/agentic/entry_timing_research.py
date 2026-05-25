from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.store import append_jsonl, artifacts_root, reports_root, session_date, write_json, write_text


class EntryTimingRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    source_case_id: str
    issue_source: str
    event_id: str
    league: str
    side: str
    timing_policy: str
    timing_bucket: str
    entry_price: float | None
    target_fill_pnl_usd: float | None
    final_score_pnl_usd: float
    fillability_status: str
    score_context_status: str
    recommendation: str
    blockers: list[str] = Field(default_factory=list)
    evidence_note: str


class EntryTimingPolicySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timing_policy: str
    eligible_case_count: int
    blocked_case_count: int
    net_final_score_pnl_usd: float
    recommendation: str
    blocker_codes: list[str] = Field(default_factory=list)


class EntryTimingMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "entry_timing_matrix_v1"
    session_date: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#55"
    related_issues: list[str] = Field(default_factory=lambda: ["#70", "#69", "#61", "#62"])
    trading_boundary: str = "read_only_research_no_live_promotion"
    source_artifacts: list[str] = Field(default_factory=list)
    rows: list[EntryTimingRow] = Field(default_factory=list)
    policy_summaries: list[EntryTimingPolicySummary] = Field(default_factory=list)
    acceptance_progress: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_promote_strategyplan_templates_without_operator_and_janus_gate_review",
        ]
    )


def entry_timing_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "entry-timing-research" / session_date(day)


def latest_replay_fixture_backtest_path(day: str | None = None, *, artifact_root: Path | None = None) -> Path | None:
    root = (artifact_root if artifact_root is not None else artifacts_root()) / "postgame-replay-config-review" / session_date(day)
    if not root.exists():
        return None
    paths = sorted(root.glob("postgame_replay_fixture_backtest_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return paths[0] if paths else None


def build_entry_timing_matrix_from_fixture_backtest(
    backtest: dict[str, Any],
    *,
    source_path: str | None = None,
    day: str | None = None,
) -> EntryTimingMatrix:
    resolved_day = session_date(day or backtest.get("session_date"))
    rows = [_row_from_result(result) for result in backtest.get("results", []) if isinstance(result, dict)]
    rows.extend(_baseline_rows())
    summaries = _policy_summaries(rows)
    return EntryTimingMatrix(
        session_date=resolved_day,
        source_artifacts=[source_path] if source_path else [],
        rows=rows,
        policy_summaries=summaries,
        acceptance_progress=_acceptance_progress(rows, backtest),
        next_actions=_next_actions(rows),
    )


def build_entry_timing_matrix(
    *,
    day: str | None = None,
    fixture_backtest_path: Path | None = None,
    artifact_root: Path | None = None,
) -> EntryTimingMatrix:
    path = fixture_backtest_path or latest_replay_fixture_backtest_path(day, artifact_root=artifact_root)
    if path is None:
        return EntryTimingMatrix(
            session_date=session_date(day),
            acceptance_progress={"source_fixture_found": False, "live_promotion_allowed": False},
            next_actions=["Generate #70 postgame_replay_fixture_backtest_v1 before updating the entry-timing matrix."],
        )
    backtest = json.loads(path.read_text(encoding="utf-8"))
    return build_entry_timing_matrix_from_fixture_backtest(backtest, source_path=str(path), day=day)


def write_entry_timing_matrix(
    matrix: EntryTimingMatrix,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = matrix.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = entry_timing_root(matrix.session_date, root=artifact_root)
    json_path = root / f"entry_timing_matrix_{timestamp}.json"
    write_json(json_path, matrix.model_dump(mode="json"))
    append_jsonl(
        root / "entry_timing_matrices.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": matrix.session_date,
            "row_count": len(matrix.rows),
            "eligible_case_count": matrix.acceptance_progress.get("eligible_case_count", 0),
            "blocked_case_count": matrix.acceptance_progress.get("blocked_case_count", 0),
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / f"entry_timing_matrix_{timestamp}.md"
    write_text(markdown_path, render_entry_timing_matrix_markdown(matrix, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "entry_timing_matrix_write_result_v1",
        "session_date": matrix.session_date,
        "row_count": len(matrix.rows),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def render_entry_timing_matrix_markdown(matrix: EntryTimingMatrix, *, json_path: str | None = None) -> str:
    lines = [
        f"# Entry Timing Matrix - {matrix.session_date}",
        "",
        f"- generated_at_utc: `{matrix.generated_at_utc.isoformat()}`",
        f"- issue: `{matrix.issue}`",
        f"- trading_boundary: `{matrix.trading_boundary}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Rows"])
    for row in matrix.rows:
        lines.append(
            f"- `{row.row_id}` `{row.timing_policy}` `{row.recommendation}`: "
            f"{row.event_id} {row.side}, final_pnl=`{row.final_score_pnl_usd:.4f}`, "
            f"blockers=`{','.join(row.blockers) or 'none'}`"
        )
    lines.extend(["", "## Policy Summaries"])
    for summary in matrix.policy_summaries:
        lines.append(
            f"- `{summary.timing_policy}`: eligible=`{summary.eligible_case_count}`, "
            f"blocked=`{summary.blocked_case_count}`, net_final_pnl=`{summary.net_final_score_pnl_usd:.4f}`, "
            f"recommendation=`{summary.recommendation}`"
        )
    lines.extend(["", "## Acceptance Progress"])
    for key, value in matrix.acceptance_progress.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(matrix.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in matrix.hard_prohibitions))
    return "\n".join(lines).rstrip() + "\n"


def _row_from_result(result: dict[str, Any]) -> EntryTimingRow:
    case_id = str(result.get("case_id", "unknown"))
    timing_policy = _timing_policy_for_case(case_id)
    blockers = [str(item) for item in result.get("blockers", [])]
    recommendation = "candidate_for_replay_matrix_only"
    if result.get("recommendation") == "quarantine_until_independent_replay_proves_edge":
        recommendation = "negative_bucket_quarantine"
    elif blockers:
        recommendation = "monitor_only_until_blockers_clear"
    return EntryTimingRow(
        row_id=f"jit55-{case_id}",
        source_case_id=case_id,
        issue_source="#70",
        event_id=str(result.get("event_id", "")),
        league=str(result.get("league", "")),
        side=str(result.get("side", "")),
        timing_policy=timing_policy,
        timing_bucket=_timing_bucket_for_policy(timing_policy),
        entry_price=_entry_price_for_case(case_id),
        target_fill_pnl_usd=result.get("target_fill_pnl_usd"),
        final_score_pnl_usd=float(result.get("final_score_pnl_usd") or 0.0),
        fillability_status="passed" if result.get("fillability_passed") else "blocked",
        score_context_status="available" if result.get("score_gap_available") else "missing",
        recommendation=recommendation,
        blockers=blockers,
        evidence_note=str(result.get("evidence_note", "")),
    )


def _baseline_rows() -> list[EntryTimingRow]:
    return [
        EntryTimingRow(
            row_id="jit55-policy-pregame-resting-limit-order",
            source_case_id="policy-baseline-pregame-resting-limit-order",
            issue_source="#55",
            event_id="policy-baseline",
            league="basketball",
            side="n/a",
            timing_policy="pregame_resting_limit_order",
            timing_bucket="pregame",
            entry_price=None,
            target_fill_pnl_usd=None,
            final_score_pnl_usd=0.0,
            fillability_status="needs_event_start_expiry_evidence",
            score_context_status="no_live_score_context",
            recommendation="monitor_only_until_cancellation_expiry_backtest_exists",
            blockers=["event_start_expiry_not_scored", "no_live_score_context"],
            evidence_note="Baseline required by #55; no current fixture row proves pregame resting orders survived event-start cancellation.",
        ),
        EntryTimingRow(
            row_id="jit55-policy-post-q1-stability-confirmed",
            source_case_id="policy-baseline-post-q1-stability-confirmed",
            issue_source="#55",
            event_id="policy-baseline",
            league="basketball",
            side="n/a",
            timing_policy="post_q1_plus_market_stability_confirmation",
            timing_bucket="post_q1_stability",
            entry_price=None,
            target_fill_pnl_usd=None,
            final_score_pnl_usd=0.0,
            fillability_status="needs_replay_window",
            score_context_status="available_after_q1",
            recommendation="preferred_research_bucket_needs_side_by_side_replay",
            blockers=["post_q1_stability_window_not_scored"],
            evidence_note="Baseline required by #55; Seattle end-Q1 comparison is noted by #70 but not yet separately scored in the fixture artifact.",
        ),
    ]


def _policy_summaries(rows: list[EntryTimingRow]) -> list[EntryTimingPolicySummary]:
    summaries: list[EntryTimingPolicySummary] = []
    policies = sorted({row.timing_policy for row in rows})
    for policy in policies:
        policy_rows = [row for row in rows if row.timing_policy == policy]
        eligible = [row for row in policy_rows if not row.blockers and row.recommendation != "negative_bucket_quarantine"]
        blocked = [row for row in policy_rows if row.blockers or row.recommendation == "negative_bucket_quarantine"]
        summaries.append(
            EntryTimingPolicySummary(
                timing_policy=policy,
                eligible_case_count=len(eligible),
                blocked_case_count=len(blocked),
                net_final_score_pnl_usd=round(sum(row.final_score_pnl_usd for row in policy_rows), 4),
                recommendation=_summary_recommendation(policy, eligible, blocked),
                blocker_codes=sorted({blocker for row in blocked for blocker in row.blockers}),
            )
        )
    return summaries


def _acceptance_progress(rows: list[EntryTimingRow], backtest: dict[str, Any]) -> dict[str, Any]:
    eligible_rows = [row.row_id for row in rows if not row.blockers and row.recommendation != "negative_bucket_quarantine"]
    blocked_rows = [row.row_id for row in rows if row.row_id not in eligible_rows]
    return {
        "source_fixture_found": True,
        "source_schema_version": backtest.get("schema_version"),
        "row_count": len(rows),
        "eligible_case_count": len(eligible_rows),
        "eligible_row_ids": eligible_rows,
        "blocked_case_count": len(blocked_rows),
        "blocked_row_ids": blocked_rows,
        "includes_fillability": True,
        "includes_event_start_cancellation_bucket": True,
        "includes_live_promotion": False,
        "live_promotion_allowed": False,
    }


def _next_actions(rows: list[EntryTimingRow]) -> list[str]:
    eligible = [row for row in rows if not row.blockers and row.recommendation != "negative_bucket_quarantine"]
    return [
        f"Use {len(eligible)} positive low-band rows as #55 entry-timing candidates, not live authority.",
        "Backfill side-by-side pregame, immediate-live, post-Q1, and post-Q1-stability replay windows before StrategyPlan template changes.",
        "Keep Q4 subpenny/min-price buys quarantined until duplicate cooldown and final-score edge are independently positive.",
        "Route any eventual signal enablement through #69 event-control readbacks and Janus live gates.",
    ]


def _timing_policy_for_case(case_id: str) -> str:
    if "dallas-q2" in case_id:
        return "first_live_window_after_event_start"
    if "seattle-q1" in case_id:
        return "first_live_window_after_event_start"
    if "atlanta" in case_id:
        return "immediate_live_low_band_rebound"
    if "q4-subpenny" in case_id:
        return "late_game_min_price_add"
    return "unclassified_replay_case"


def _timing_bucket_for_policy(policy: str) -> str:
    if policy.startswith("pregame"):
        return "pregame"
    if policy.startswith("post_q1"):
        return "post_q1"
    if policy == "late_game_min_price_add":
        return "late_game_negative_control"
    return "live_low_band"


def _entry_price_for_case(case_id: str) -> float | None:
    prices = {
        "wnba-phx-atl-atlanta-comeback-low-band": 0.32,
        "wnba-dal-nyl-dallas-q2-low-band": 0.23,
        "wnba-wsh-sea-seattle-q1-rebound": 0.26,
        "nba-okc-sas-thunder-q4-subpenny-negative": 0.005,
    }
    return prices.get(case_id)


def _summary_recommendation(policy: str, eligible: list[EntryTimingRow], blocked: list[EntryTimingRow]) -> str:
    if policy == "late_game_min_price_add":
        return "quarantine"
    if policy == "pregame_resting_limit_order":
        return "needs_event_start_expiry_backtest"
    if policy == "post_q1_plus_market_stability_confirmation":
        return "needs_separate_stability_fixture"
    if eligible and not blocked:
        return "candidate_for_deeper_replay"
    return "monitor_only_until_blockers_clear"


def _bullet_lines(items: Any) -> list[str]:
    rendered = [f"- {item}" for item in items if item]
    return rendered or ["- none"]
