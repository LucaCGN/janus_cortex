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


class EntryTimingCasePolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    event_id: str
    league: str
    side: str
    timing_policy: str
    timing_bucket: str
    evaluation_basis: str
    filled: bool
    fillable: bool
    cancelled_or_expired: bool
    missed_entry: bool
    adverse_selection: bool
    target_fill_pnl_usd: float | None
    final_score_pnl_usd: float
    missed_entry_cost_usd: float
    avoided_loss_usd: float
    blockers: list[str] = Field(default_factory=list)
    evidence_note: str


class EntryTimingPricePathReplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    event_id: str
    side: str
    outcome_label: str | None = None
    outcome_id: str | None = None
    source_tick_path: str | None = None
    observed_tick_count: int = 0
    priced_tick_count: int = 0
    entry_price: float | None = None
    first_observed_at_utc: str | None = None
    last_observed_at_utc: str | None = None
    min_ask: float | None = None
    max_ask: float | None = None
    first_entry_fill_at_utc: str | None = None
    first_entry_fill_price: float | None = None
    entry_fill_observed: bool = False
    stability_window_tick_count: int = 0
    stability_first_fill_at_utc: str | None = None
    stability_fill_observed: bool = False
    event_start_expired_order_count: int = 0
    order_lifecycle_status: str
    duplicate_cooldown_status: str
    blockers: list[str] = Field(default_factory=list)
    evidence_note: str


class EntryTimingSideBySidePolicySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timing_policy: str
    evaluated_case_count: int
    filled_case_count: int
    fill_rate: float
    cancelled_or_expired_count: int
    missed_entry_count: int
    adverse_selection_count: int
    net_target_fill_pnl_usd: float
    net_final_score_pnl_usd: float
    missed_entry_cost_usd: float
    avoided_loss_usd: float
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
    side_by_side_results: list[EntryTimingCasePolicyResult] = Field(default_factory=list)
    side_by_side_policy_summaries: list[EntryTimingSideBySidePolicySummary] = Field(default_factory=list)
    price_path_replays: list[EntryTimingPricePathReplay] = Field(default_factory=list)
    acceptance_progress: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_promote_strategyplan_templates_without_operator_and_janus_gate_review",
        ]
    )


class EventControlRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    source_case_id: str
    event_id: str
    league: str
    side: str
    timing_policy: str
    event_control_action: str
    recommended_signal_toggles: dict[str, bool] = Field(default_factory=dict)
    recommended_parameters: dict[str, Any] = Field(default_factory=dict)
    entry_price: float | None = None
    max_entry_price_ceiling: float | None = None
    runtime_mutation_allowed: bool = False
    live_promotion_allowed: bool = False
    blockers: list[str] = Field(default_factory=list)
    required_gates: list[str] = Field(default_factory=list)
    evidence_note: str


class EventControlRecommendationPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "entry_timing_event_control_recommendation_pack_v1"
    session_date: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#55"
    related_issues: list[str] = Field(default_factory=lambda: ["#69", "#70", "#61", "#62"])
    trading_boundary: str = "read_only_recommendations_no_runtime_control_mutation"
    source_matrix_path: str | None = None
    recommendations: list[EventControlRecommendation] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_update_event_control_current_json_from_this_artifact",
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


def latest_live_worker_ticks_path(day: str | None = None, *, artifact_root: Path | None = None) -> Path | None:
    root = (artifact_root if artifact_root is not None else artifacts_root()) / "live-strategy-worker"
    path = root / session_date(day) / "ticks.jsonl"
    if path.exists():
        return path
    if not root.exists():
        return None
    paths = sorted(root.glob("*/ticks.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    return paths[0] if paths else None


def build_entry_timing_matrix_from_fixture_backtest(
    backtest: dict[str, Any],
    *,
    source_path: str | None = None,
    day: str | None = None,
    live_worker_ticks_path: Path | None = None,
) -> EntryTimingMatrix:
    resolved_day = session_date(day or backtest.get("session_date"))
    rows = [_row_from_result(result) for result in backtest.get("results", []) if isinstance(result, dict)]
    rows.extend(_baseline_rows())
    price_path_replays = _price_path_replays(rows, tick_path=live_worker_ticks_path)
    replay_by_case = {replay.case_id: replay for replay in price_path_replays}
    side_by_side_results = _side_by_side_results(rows, replay_by_case)
    summaries = _policy_summaries(rows)
    return EntryTimingMatrix(
        session_date=resolved_day,
        source_artifacts=[source_path] if source_path else [],
        rows=rows,
        policy_summaries=summaries,
        side_by_side_results=side_by_side_results,
        side_by_side_policy_summaries=_side_by_side_policy_summaries(side_by_side_results),
        price_path_replays=price_path_replays,
        acceptance_progress=_acceptance_progress(rows, side_by_side_results, price_path_replays, backtest),
        next_actions=_next_actions(rows, price_path_replays),
    )


def build_entry_timing_matrix(
    *,
    day: str | None = None,
    fixture_backtest_path: Path | None = None,
    live_worker_ticks_path: Path | None = None,
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
    tick_path = live_worker_ticks_path or latest_live_worker_ticks_path(day or backtest.get("session_date"), artifact_root=artifact_root)
    return build_entry_timing_matrix_from_fixture_backtest(backtest, source_path=str(path), day=day, live_worker_ticks_path=tick_path)


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
            "side_by_side_result_count": len(matrix.side_by_side_results),
            "price_path_replay_count": len(matrix.price_path_replays),
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


def build_event_control_recommendation_pack(
    matrix: EntryTimingMatrix,
    *,
    source_matrix_path: str | None = None,
) -> EventControlRecommendationPack:
    recommendations = [_event_control_recommendation_for_row(row) for row in matrix.rows if row.issue_source == "#70"]
    candidates = [item for item in recommendations if item.event_control_action == "candidate_review_only"]
    quarantined = [item for item in recommendations if item.event_control_action == "quarantine_disabled"]
    blocked = [item for item in recommendations if item.blockers and item.event_control_action != "quarantine_disabled"]
    return EventControlRecommendationPack(
        session_date=matrix.session_date,
        source_matrix_path=source_matrix_path,
        recommendations=recommendations,
        summary={
            "recommendation_count": len(recommendations),
            "candidate_review_count": len(candidates),
            "quarantine_count": len(quarantined),
            "blocked_candidate_count": len(blocked),
            "runtime_mutation_allowed": False,
            "live_promotion_allowed": False,
            "wnba_candidate_event_ids": sorted({item.event_id for item in candidates if item.league.upper() == "WNBA"}),
            "quarantined_case_ids": [item.source_case_id for item in quarantined],
            "source_matrix_live_promotion_allowed": matrix.acceptance_progress.get("live_promotion_allowed", False),
        },
        next_actions=[
            "Review WNBA low-band candidates through #69 event-control readbacks before any runtime update.",
            "Keep Thunder Q4 subpenny/min-price behavior disabled until duplicate cooldown and final-score edge blockers are cleared by independent replay.",
            "Require fresh Janus StrategyPlan, feed, CLOB, worker, risk, kill-switch, and explicit operator/Janus gates before any live promotion.",
        ],
    )


def write_event_control_recommendation_pack(
    pack: EventControlRecommendationPack,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = pack.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = entry_timing_root(pack.session_date, root=artifact_root)
    json_path = root / f"entry_timing_event_control_recommendation_pack_{timestamp}.json"
    write_json(json_path, pack.model_dump(mode="json"))
    append_jsonl(
        root / "entry_timing_event_control_recommendation_packs.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": pack.session_date,
            "recommendation_count": len(pack.recommendations),
            "candidate_review_count": pack.summary.get("candidate_review_count", 0),
            "quarantine_count": pack.summary.get("quarantine_count", 0),
            "runtime_mutation_allowed": False,
            "live_promotion_allowed": False,
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / (
        f"entry_timing_event_control_recommendation_pack_{timestamp}.md"
    )
    write_text(markdown_path, render_event_control_recommendation_pack_markdown(pack, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "entry_timing_event_control_recommendation_pack_write_result_v1",
        "session_date": pack.session_date,
        "recommendation_count": len(pack.recommendations),
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
    lines.extend(["", "## Side-By-Side Policy Summaries"])
    for summary in matrix.side_by_side_policy_summaries:
        lines.append(
            f"- `{summary.timing_policy}`: cases=`{summary.evaluated_case_count}`, fill_rate=`{summary.fill_rate:.2f}`, "
            f"cancelled_or_expired=`{summary.cancelled_or_expired_count}`, missed_entry=`{summary.missed_entry_count}`, "
            f"adverse_selection=`{summary.adverse_selection_count}`, net_final_pnl=`{summary.net_final_score_pnl_usd:.4f}`, "
            f"missed_entry_cost=`{summary.missed_entry_cost_usd:.4f}`, recommendation=`{summary.recommendation}`"
        )
    lines.extend(["", "## Price-Path And Order-Lifecycle Replay"])
    for replay in matrix.price_path_replays:
        lines.append(
            f"- `{replay.case_id}`: ticks=`{replay.observed_tick_count}`, priced=`{replay.priced_tick_count}`, "
            f"entry_fill=`{replay.entry_fill_observed}`, stability_fill=`{replay.stability_fill_observed}`, "
            f"min_ask=`{replay.min_ask}`, lifecycle=`{replay.order_lifecycle_status}`, "
            f"blockers=`{','.join(replay.blockers) or 'none'}`"
        )
    lines.extend(["", "## Acceptance Progress"])
    for key, value in matrix.acceptance_progress.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(matrix.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in matrix.hard_prohibitions))
    return "\n".join(lines).rstrip() + "\n"


def render_event_control_recommendation_pack_markdown(
    pack: EventControlRecommendationPack,
    *,
    json_path: str | None = None,
) -> str:
    lines = [
        f"# Entry Timing Event-Control Recommendation Pack - {pack.session_date}",
        "",
        f"- generated_at_utc: `{pack.generated_at_utc.isoformat()}`",
        f"- issue: `{pack.issue}`",
        f"- trading_boundary: `{pack.trading_boundary}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    if pack.source_matrix_path:
        lines.append(f"- source_matrix: `{pack.source_matrix_path}`")
    lines.extend(["", "## Summary"])
    for key, value in pack.summary.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Recommendations"])
    for item in pack.recommendations:
        lines.append(
            f"- `{item.recommendation_id}` `{item.event_control_action}`: {item.event_id} {item.side}, "
            f"entry_price=`{item.entry_price}`, ceiling=`{item.max_entry_price_ceiling}`, "
            f"blockers=`{','.join(item.blockers) or 'none'}`"
        )
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(pack.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in pack.hard_prohibitions))
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


def _side_by_side_results(
    rows: list[EntryTimingRow],
    replay_by_case: dict[str, EntryTimingPricePathReplay] | None = None,
) -> list[EntryTimingCasePolicyResult]:
    source_rows = [row for row in rows if row.issue_source == "#70"]
    results: list[EntryTimingCasePolicyResult] = []
    policies = [
        "pregame_resting_limit_order",
        "first_live_window_after_event_start",
        "post_q1_entry",
        "post_q1_plus_market_stability_confirmation",
    ]
    replay_by_case = replay_by_case or {}
    for row in source_rows:
        for policy in policies:
            results.append(_case_policy_result(row, policy, replay_by_case.get(row.source_case_id)))
    return results


def _case_policy_result(
    row: EntryTimingRow,
    policy: str,
    replay: EntryTimingPricePathReplay | None = None,
) -> EntryTimingCasePolicyResult:
    positive_edge = row.final_score_pnl_usd > 0 and row.recommendation != "negative_bucket_quarantine"
    negative_edge = row.final_score_pnl_usd < 0 or row.recommendation == "negative_bucket_quarantine"
    blockers = list(row.blockers)
    filled = False
    fillable = row.fillability_status == "passed"
    cancelled_or_expired = False
    missed_entry = False
    adverse_selection = False
    target_fill_pnl = row.target_fill_pnl_usd
    final_pnl = 0.0
    missed_cost = 0.0
    avoided_loss = 0.0
    basis = "derived_from_fixture_row"
    note = row.evidence_note

    if policy == "pregame_resting_limit_order":
        cancelled_or_expired = True
        missed_entry = positive_edge
        missed_cost = row.final_score_pnl_usd if positive_edge else 0.0
        avoided_loss = abs(row.final_score_pnl_usd) if negative_edge else 0.0
        target_fill_pnl = None
        blockers = ["event_start_expiry_assumed", "no_live_score_context"]
        basis = "expiry_stress_case"
        note = "Pregame resting order is treated as expired/cancelled at event start until order-lifecycle replay proves otherwise."
    elif policy == "first_live_window_after_event_start":
        filled = fillable
        final_pnl = row.final_score_pnl_usd if filled else 0.0
        adverse_selection = filled and negative_edge
        if not filled:
            missed_entry = positive_edge
            missed_cost = row.final_score_pnl_usd if positive_edge else 0.0
            blockers.append("first_live_fillability_missing")
        basis = "observed_or_fixture_low_band_window"
    elif policy == "post_q1_entry":
        filled = (
            replay.entry_fill_observed
            if replay is not None
            else fillable and row.score_context_status == "available" and row.timing_policy != "late_game_min_price_add"
        )
        final_pnl = row.final_score_pnl_usd if filled else 0.0
        missed_entry = positive_edge and not filled
        missed_cost = row.final_score_pnl_usd if missed_entry else 0.0
        adverse_selection = filled and negative_edge
        target_fill_pnl = None
        if replay is None and row.timing_policy in {"immediate_live_low_band_rebound", "first_live_window_after_event_start"}:
            blockers.append("post_q1_price_path_proxy_only")
        if replay is not None and not replay.entry_fill_observed:
            blockers.append("post_q1_price_path_entry_not_observed")
        if row.timing_policy == "late_game_min_price_add":
            blockers.append("post_q1_entry_not_applicable_to_late_game_min_price_case")
        basis = "price_path_replay_at_entry_price" if replay is not None else "post_q1_proxy_from_score_context_and_final_result"
    else:
        filled = bool(replay and replay.stability_fill_observed)
        final_pnl = row.final_score_pnl_usd if filled else 0.0
        missed_entry = positive_edge and not filled
        missed_cost = row.final_score_pnl_usd if missed_entry else 0.0
        adverse_selection = filled and negative_edge
        avoided_loss = abs(row.final_score_pnl_usd) if negative_edge else 0.0
        target_fill_pnl = None
        if replay is None:
            blockers = sorted(set(blockers + ["market_stability_confirmation_not_replayed", "post_q1_stability_price_path_missing"]))
            basis = "stability_confirmation_gap"
            note = "Post-Q1 stability remains monitor-only until price-path and stability-window replay exist."
        elif not replay.stability_fill_observed:
            blockers = sorted(set(blockers + ["post_q1_stability_fill_not_observed"]))
            basis = "price_path_replay_no_stability_fill"
            note = "Price-path replay did not show enough consecutive stable entry-price ticks for this case."
        else:
            basis = "price_path_replay_stability_window"
            note = "Price-path replay found a consecutive stable entry-price window; this is research evidence only."

    return EntryTimingCasePolicyResult(
        case_id=row.source_case_id,
        event_id=row.event_id,
        league=row.league,
        side=row.side,
        timing_policy=policy,
        timing_bucket=_timing_bucket_for_policy(policy),
        evaluation_basis=basis,
        filled=filled,
        fillable=fillable,
        cancelled_or_expired=cancelled_or_expired,
        missed_entry=missed_entry,
        adverse_selection=adverse_selection,
        target_fill_pnl_usd=target_fill_pnl,
        final_score_pnl_usd=round(final_pnl, 4),
        missed_entry_cost_usd=round(missed_cost, 4),
        avoided_loss_usd=round(avoided_loss, 4),
        blockers=sorted(set(blockers)),
        evidence_note=note,
    )


def _side_by_side_policy_summaries(
    results: list[EntryTimingCasePolicyResult],
) -> list[EntryTimingSideBySidePolicySummary]:
    summaries: list[EntryTimingSideBySidePolicySummary] = []
    for policy in sorted({result.timing_policy for result in results}):
        policy_results = [result for result in results if result.timing_policy == policy]
        filled = [result for result in policy_results if result.filled]
        target_values = [result.target_fill_pnl_usd for result in policy_results if result.target_fill_pnl_usd is not None and result.filled]
        summaries.append(
            EntryTimingSideBySidePolicySummary(
                timing_policy=policy,
                evaluated_case_count=len(policy_results),
                filled_case_count=len(filled),
                fill_rate=round(len(filled) / len(policy_results), 4) if policy_results else 0.0,
                cancelled_or_expired_count=sum(1 for result in policy_results if result.cancelled_or_expired),
                missed_entry_count=sum(1 for result in policy_results if result.missed_entry),
                adverse_selection_count=sum(1 for result in policy_results if result.adverse_selection),
                net_target_fill_pnl_usd=round(sum(target_values), 4),
                net_final_score_pnl_usd=round(sum(result.final_score_pnl_usd for result in policy_results), 4),
                missed_entry_cost_usd=round(sum(result.missed_entry_cost_usd for result in policy_results), 4),
                avoided_loss_usd=round(sum(result.avoided_loss_usd for result in policy_results), 4),
                recommendation=_side_by_side_recommendation(policy, policy_results),
                blocker_codes=sorted({blocker for result in policy_results for blocker in result.blockers}),
            )
        )
    return summaries


def _acceptance_progress(
    rows: list[EntryTimingRow],
    side_by_side_results: list[EntryTimingCasePolicyResult],
    price_path_replays: list[EntryTimingPricePathReplay],
    backtest: dict[str, Any],
) -> dict[str, Any]:
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
        "includes_side_by_side_policy_windows": bool(side_by_side_results),
        "side_by_side_policy_count": len({result.timing_policy for result in side_by_side_results}),
        "side_by_side_result_count": len(side_by_side_results),
        "separates_return_fill_missed_entry_adverse_selection_and_expiry": bool(side_by_side_results),
        "includes_real_price_path_replay": bool(price_path_replays),
        "price_path_replay_case_count": len(price_path_replays),
        "price_path_entry_fill_case_count": sum(1 for replay in price_path_replays if replay.entry_fill_observed),
        "price_path_stability_fill_case_count": sum(1 for replay in price_path_replays if replay.stability_fill_observed),
        "includes_order_lifecycle_replay": any(replay.event_start_expired_order_count > 0 for replay in price_path_replays),
        "order_lifecycle_replay_case_count": sum(1 for replay in price_path_replays if replay.event_start_expired_order_count > 0),
        "strategy_template_promotion_allowed": False,
        "includes_live_promotion": False,
        "live_promotion_allowed": False,
    }


def _next_actions(rows: list[EntryTimingRow], price_path_replays: list[EntryTimingPricePathReplay] | None = None) -> list[str]:
    eligible = [row for row in rows if not row.blockers and row.recommendation != "negative_bucket_quarantine"]
    replay_note = (
        "Use real price-path replay results to separate immediate entry, post-Q1 entry, and stability-confirmed entry; keep order-lifecycle gaps as blockers."
        if price_path_replays
        else "Backfill side-by-side pregame, immediate-live, post-Q1, and post-Q1-stability replay windows before StrategyPlan template changes."
    )
    return [
        f"Use {len(eligible)} positive low-band rows as #55 entry-timing candidates, not live authority.",
        replay_note,
        "Keep Q4 subpenny/min-price buys quarantined until duplicate cooldown and final-score edge are independently positive.",
        "Route any eventual signal enablement through #69 event-control readbacks and Janus live gates.",
    ]


def _event_control_recommendation_for_row(row: EntryTimingRow) -> EventControlRecommendation:
    required_gates = [
        "fresh_strategy_plan_json",
        "event_control_readback_review",
        "direct_clob_event_inventory_clear",
        "feed_and_orderbook_fresh",
        "live_worker_scope_aligned",
        "risk_budget_and_kill_switch_green",
        "explicit_operator_and_janus_approval",
    ]
    if row.recommendation == "negative_bucket_quarantine" or row.timing_policy == "late_game_min_price_add":
        return EventControlRecommendation(
            recommendation_id=f"jit55-ec-{row.source_case_id}",
            source_case_id=row.source_case_id,
            event_id=row.event_id,
            league=row.league,
            side=row.side,
            timing_policy=row.timing_policy,
            event_control_action="quarantine_disabled",
            recommended_signal_toggles={
                "late_game_min_price_add": False,
                "q4_subpenny_hype_bounce": False,
                "no_bid_min_price_lottery_v1": False,
            },
            recommended_parameters={
                "min_price_lottery_allowed": False,
                "duplicate_intent_cooldown_required": True,
            },
            entry_price=row.entry_price,
            max_entry_price_ceiling=row.entry_price,
            blockers=sorted(set(row.blockers + ["negative_control_case"])),
            required_gates=required_gates + ["independent_positive_replay_before_unquarantine"],
            evidence_note=row.evidence_note,
        )

    action = "candidate_review_only" if row.league.upper() == "WNBA" and not row.blockers else "monitor_only_blocked"
    ceiling = min(0.45, float(row.entry_price or 0.45))
    return EventControlRecommendation(
        recommendation_id=f"jit55-ec-{row.source_case_id}",
        source_case_id=row.source_case_id,
        event_id=row.event_id,
        league=row.league,
        side=row.side,
        timing_policy=row.timing_policy,
        event_control_action=action,
        recommended_signal_toggles={
            "deterministic_low_band_rebound": True,
            "wnba_low_band_rebound": row.league.upper() == "WNBA",
            "late_game_min_price_add": False,
        },
        recommended_parameters={
            "max_entry_price": ceiling,
            "wnba_max_price_ceiling": 0.45,
            "max_signal_age_seconds": 180.0,
            "cooldown_seconds": 90.0,
            "min_confidence": 0.55,
            "rebuy_review_required": True,
            "allow_inventory_adding": False,
        },
        entry_price=row.entry_price,
        max_entry_price_ceiling=ceiling,
        blockers=sorted(set(row.blockers)),
        required_gates=required_gates,
        evidence_note=row.evidence_note,
    )


def _price_path_replays(rows: list[EntryTimingRow], *, tick_path: Path | None) -> list[EntryTimingPricePathReplay]:
    if tick_path is None or not tick_path.exists():
        return []
    source_rows = [row for row in rows if row.issue_source == "#70"]
    if not source_rows:
        return []
    ticks = _load_live_worker_ticks(tick_path)
    outcome_labels = _outcome_label_map(ticks)
    series = _orderbook_series(ticks, outcome_labels)
    replays: list[EntryTimingPricePathReplay] = []
    for row in source_rows:
        replays.append(_price_path_replay_for_row(row, tick_path=tick_path, series=series))
    return replays


def _load_live_worker_ticks(tick_path: Path) -> list[dict[str, Any]]:
    ticks: list[dict[str, Any]] = []
    for line in tick_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            ticks.append(value)
    return ticks


def _outcome_label_map(ticks: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    labels: dict[tuple[str, str], str] = {}
    for tick in ticks:
        stdout = tick.get("stdout") if isinstance(tick.get("stdout"), dict) else {}
        for event in stdout.get("events", []):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id") or "")
            trace = event.get("llm_runtime_trace") if isinstance(event.get("llm_runtime_trace"), dict) else {}
            plan = (trace.get("revision_request") or {}).get("current_plan") if isinstance(trace.get("revision_request"), dict) else {}
            if not isinstance(plan, dict):
                continue
            for strategy in plan.get("active_strategies", []):
                if not isinstance(strategy, dict):
                    continue
                entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
                outcome_id = entry_rules.get("outcome_id")
                outcome_label = entry_rules.get("outcome_label") or strategy.get("side")
                if event_id and outcome_id and outcome_label:
                    labels[(event_id, str(outcome_id))] = str(outcome_label)
    return labels


def _orderbook_series(
    ticks: list[dict[str, Any]],
    outcome_labels: dict[tuple[str, str], str],
) -> dict[tuple[str, str], dict[str, Any]]:
    series: dict[tuple[str, str], dict[str, Any]] = {}
    for tick in ticks:
        stdout = tick.get("stdout") if isinstance(tick.get("stdout"), dict) else {}
        tick_time = tick.get("finished_at_utc") or tick.get("started_at_utc")
        for event in stdout.get("events", []):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id") or "")
            portfolio_state = event.get("portfolio_state") if isinstance(event.get("portfolio_state"), dict) else {}
            expired_count = int(portfolio_state.get("event_start_expired_order_count") or 0)
            for outcome_id, orderbook in (event.get("orderbook_results") or {}).items():
                if not isinstance(orderbook, dict):
                    continue
                label = outcome_labels.get((event_id, str(outcome_id)))
                if not label:
                    continue
                key = (event_id, _normalize_label(label))
                bucket = series.setdefault(
                    key,
                    {
                        "event_id": event_id,
                        "outcome_label": label,
                        "outcome_id": str(outcome_id),
                        "event_start_expired_order_count": 0,
                        "points": [],
                    },
                )
                bucket["event_start_expired_order_count"] = max(bucket["event_start_expired_order_count"], expired_count)
                bucket["points"].append(
                    {
                        "observed_at_utc": str(tick_time) if tick_time else None,
                        "best_bid": _float_or_none(orderbook.get("best_bid")),
                        "best_ask": _float_or_none(orderbook.get("best_ask")),
                        "spread": _float_or_none(orderbook.get("spread")),
                    }
                )
    return series


def _price_path_replay_for_row(
    row: EntryTimingRow,
    *,
    tick_path: Path,
    series: dict[tuple[str, str], dict[str, Any]],
) -> EntryTimingPricePathReplay:
    bucket = _matching_series(row, series)
    if bucket is None:
        return EntryTimingPricePathReplay(
            case_id=row.source_case_id,
            event_id=row.event_id,
            side=row.side,
            source_tick_path=str(tick_path),
            entry_price=row.entry_price,
            order_lifecycle_status="price_path_missing",
            duplicate_cooldown_status=_duplicate_cooldown_status(row),
            blockers=["price_path_missing_for_case"],
            evidence_note="No matching outcome price path was found in the live-worker tick artifact.",
        )
    points = [point for point in bucket["points"] if isinstance(point, dict)]
    priced = [point for point in points if point.get("best_ask") is not None]
    entry_fills = [point for point in priced if row.entry_price is not None and point["best_ask"] <= row.entry_price]
    stability_window = _first_stability_window(priced, row.entry_price)
    blockers = list(row.blockers)
    if not entry_fills:
        blockers.append("entry_price_not_seen_in_real_price_path")
    if not stability_window:
        blockers.append("stability_entry_window_not_seen_in_real_price_path")
    expired_count = int(bucket.get("event_start_expired_order_count") or 0)
    lifecycle_status = "event_start_expired_orders_observed" if expired_count else "no_resting_order_lifecycle_evidence_observed"
    asks = [point["best_ask"] for point in priced if point.get("best_ask") is not None]
    return EntryTimingPricePathReplay(
        case_id=row.source_case_id,
        event_id=row.event_id,
        side=row.side,
        outcome_label=str(bucket.get("outcome_label")) if bucket.get("outcome_label") else None,
        outcome_id=str(bucket.get("outcome_id")) if bucket.get("outcome_id") else None,
        source_tick_path=str(tick_path),
        observed_tick_count=len(points),
        priced_tick_count=len(priced),
        entry_price=row.entry_price,
        first_observed_at_utc=str(points[0].get("observed_at_utc")) if points else None,
        last_observed_at_utc=str(points[-1].get("observed_at_utc")) if points else None,
        min_ask=round(min(asks), 4) if asks else None,
        max_ask=round(max(asks), 4) if asks else None,
        first_entry_fill_at_utc=str(entry_fills[0].get("observed_at_utc")) if entry_fills else None,
        first_entry_fill_price=round(float(entry_fills[0]["best_ask"]), 4) if entry_fills else None,
        entry_fill_observed=bool(entry_fills),
        stability_window_tick_count=len(stability_window),
        stability_first_fill_at_utc=str(stability_window[0].get("observed_at_utc")) if stability_window else None,
        stability_fill_observed=bool(stability_window),
        event_start_expired_order_count=expired_count,
        order_lifecycle_status=lifecycle_status,
        duplicate_cooldown_status=_duplicate_cooldown_status(row),
        blockers=sorted(set(blockers)),
        evidence_note=(
            f"Observed {len(priced)} priced ticks for {bucket.get('outcome_label')} from live-worker ticks; "
            f"entry_fill_observed={bool(entry_fills)}, stability_fill_observed={bool(stability_window)}."
        ),
    )


def _matching_series(row: EntryTimingRow, series: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    wanted = _normalize_label(row.side)
    for (event_id, label), bucket in series.items():
        if event_id == row.event_id and (wanted == label or wanted in label or label in wanted):
            return bucket
    return None


def _first_stability_window(points: list[dict[str, Any]], entry_price: float | None, *, min_ticks: int = 3) -> list[dict[str, Any]]:
    if entry_price is None:
        return []
    window: list[dict[str, Any]] = []
    for point in points:
        ask = point.get("best_ask")
        spread = point.get("spread")
        stable = ask is not None and ask <= entry_price and (spread is None or spread <= 0.02)
        if stable:
            window.append(point)
            if len(window) >= min_ticks:
                return window[-min_ticks:]
        else:
            window = []
    return []


def _duplicate_cooldown_status(row: EntryTimingRow) -> str:
    if "duplicate_intent_cooldown_required" in row.blockers:
        return "cooldown_required"
    return "cooldown_not_blocking_fixture"


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _normalize_label(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    noise = {"the", "team"}
    return " ".join(part for part in normalized.split() if part not in noise)


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


def _side_by_side_recommendation(policy: str, results: list[EntryTimingCasePolicyResult]) -> str:
    if policy == "pregame_resting_limit_order":
        return "avoid_until_event_start_expiry_replay_proves_resting_order_survival"
    if policy == "post_q1_plus_market_stability_confirmation":
        return "preferred_control_policy_but_blocked_until_stability_price_path_replay"
    if any(result.adverse_selection for result in results):
        return "candidate_with_negative_bucket_quarantine"
    if any(result.blockers for result in results):
        return "proxy_only_until_price_path_replay"
    if any(result.missed_entry for result in results):
        return "monitor_only_until_missed_entry_cost_is_replayed"
    return "candidate_for_deeper_replay_no_live_promotion"


def _bullet_lines(items: Any) -> list[str]:
    rendered = [f"- {item}" for item in items if item]
    return rendered or ["- none"]
