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


class ReplayFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    entry_price: float
    target_price: float | None = None
    shares: float = 5.0
    score_gap: int | None = None
    spread: float
    max_allowed_price: float
    max_allowed_spread: float
    final_side_won: bool
    target_filled: bool = False
    duplicate_intent_count: int = 1
    evidence_note: str


class ReplayFixtureBacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    event_id: str
    league: str
    side: str
    expected_direction: str
    fillability_passed: bool
    score_gap_available: bool
    duplicate_cooldown_passed: bool
    target_fill_pnl_usd: float | None
    final_score_pnl_usd: float
    recommendation: str
    blockers: list[str] = Field(default_factory=list)
    evidence_note: str


class ReplayFixtureBacktest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "postgame_replay_fixture_backtest_v1"
    session_date: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#70"
    related_issues: list[str] = Field(default_factory=lambda: ["#55", "#69", "#61"])
    trading_boundary: str = "read_only_no_orders_no_worker_starts"
    source_case_count: int = 0
    results: list[ReplayFixtureBacktestResult] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_promote_strategyplan_config_without_live_gate_review",
        ]
    )


class NoBidMinPriceLotteryStudyCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    event_id: str
    league: str
    side: str
    observed_entry_price: float
    observed_target_price: float | None
    observed_shares: float
    entry_fillability_status: str
    exit_fillability_status: str
    human_observed_rebound_status: str
    reproducible_edge_status: str
    final_score_pnl_usd: float
    target_fill_pnl_usd: float | None
    duplicate_cooldown_passed: bool
    no_bid_or_ask_only_period: bool
    blockers: list[str] = Field(default_factory=list)
    event_control_recommendation: dict[str, Any] = Field(default_factory=dict)
    evidence_note: str


class NoBidMinPriceLotteryStudy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "no_bid_min_price_lottery_study_v1"
    session_date: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#70"
    related_issues: list[str] = Field(default_factory=lambda: ["#55", "#61", "#63", "#69"])
    trading_boundary: str = "read_only_replay_no_runtime_mutation"
    source_backtest_schema_version: str = "postgame_replay_fixture_backtest_v1"
    source_case_count: int = 0
    cases: list[NoBidMinPriceLotteryStudyCase] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_update_event_control_current_json_from_this_artifact",
            "do_not_promote_no_bid_min_price_lottery_without_independent_positive_replay",
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


def build_replay_fixture_backtest(review: ReplayConfigReview) -> ReplayFixtureBacktest:
    results: list[ReplayFixtureBacktestResult] = []
    for case in review.replay_cases:
        fixture = _fixture_for_case(case.case_id)
        if fixture is None:
            results.append(
                ReplayFixtureBacktestResult(
                    case_id=case.case_id,
                    event_id=case.event_id,
                    league=case.league,
                    side=case.side,
                    expected_direction=case.expected_direction,
                    fillability_passed=False,
                    score_gap_available=False,
                    duplicate_cooldown_passed=False,
                    target_fill_pnl_usd=None,
                    final_score_pnl_usd=0.0,
                    recommendation="needs_fixture_definition_before_config_promotion",
                    blockers=["missing_fixture_definition"],
                    evidence_note="No deterministic fixture was registered for this replay case.",
                )
            )
            continue
        results.append(_score_fixture(case, fixture))
    summary = _fixture_backtest_summary(results)
    return ReplayFixtureBacktest(
        session_date=review.session_date,
        source_case_count=len(review.replay_cases),
        results=results,
        summary=summary,
        next_actions=_fixture_backtest_next_actions(summary),
    )


def build_no_bid_min_price_lottery_study(backtest: ReplayFixtureBacktest) -> NoBidMinPriceLotteryStudy:
    cases = [
        _build_no_bid_case(result, fixture)
        for result in backtest.results
        if (fixture := _fixture_for_case(result.case_id)) is not None and _is_no_bid_min_price_case(result)
    ]
    summary = _no_bid_study_summary(cases)
    return NoBidMinPriceLotteryStudy(
        session_date=backtest.session_date,
        source_case_count=len(backtest.results),
        cases=cases,
        summary=summary,
        next_actions=_no_bid_study_next_actions(summary),
    )


def write_no_bid_min_price_lottery_study(
    study: NoBidMinPriceLotteryStudy,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = study.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = replay_config_review_root(study.session_date, root=artifact_root)
    json_path = root / f"no_bid_min_price_lottery_study_{timestamp}.json"
    write_json(json_path, study.model_dump(mode="json"))
    append_jsonl(
        root / "no_bid_min_price_lottery_studies.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": study.session_date,
            "source_case_count": study.source_case_count,
            "study_case_count": len(study.cases),
            "reproducible_positive_case_count": study.summary.get("reproducible_positive_case_count", 0),
            "quarantined_case_count": study.summary.get("quarantined_case_count", 0),
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / (
        f"no_bid_min_price_lottery_study_{timestamp}.md"
    )
    write_text(markdown_path, render_no_bid_min_price_lottery_study_markdown(study, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "no_bid_min_price_lottery_study_write_result_v1",
        "session_date": study.session_date,
        "source_case_count": study.source_case_count,
        "study_case_count": len(study.cases),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def write_replay_fixture_backtest(
    backtest: ReplayFixtureBacktest,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = backtest.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = replay_config_review_root(backtest.session_date, root=artifact_root)
    json_path = root / f"postgame_replay_fixture_backtest_{timestamp}.json"
    write_json(json_path, backtest.model_dump(mode="json"))
    append_jsonl(
        root / "postgame_replay_fixture_backtests.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": backtest.session_date,
            "source_case_count": backtest.source_case_count,
            "eligible_positive_case_count": backtest.summary.get("eligible_positive_case_count", 0),
            "quarantined_case_count": backtest.summary.get("quarantined_case_count", 0),
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / (
        f"postgame_replay_fixture_backtest_{timestamp}.md"
    )
    write_text(markdown_path, render_replay_fixture_backtest_markdown(backtest, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "postgame_replay_fixture_backtest_write_result_v1",
        "session_date": backtest.session_date,
        "source_case_count": backtest.source_case_count,
        "result_count": len(backtest.results),
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


def render_replay_fixture_backtest_markdown(
    backtest: ReplayFixtureBacktest,
    *,
    json_path: str | None = None,
) -> str:
    lines = [
        f"# Postgame Replay Fixture Backtest - {backtest.session_date}",
        "",
        f"- generated_at_utc: `{backtest.generated_at_utc.isoformat()}`",
        f"- issue: `{backtest.issue}`",
        f"- trading_boundary: `{backtest.trading_boundary}`",
        f"- source_case_count: `{backtest.source_case_count}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Results"])
    for result in backtest.results:
        lines.append(
            f"- `{result.case_id}` `{result.recommendation}`: final_pnl="
            f"`{result.final_score_pnl_usd:.4f}`, target_pnl=`{_format_optional_money(result.target_fill_pnl_usd)}`, "
            f"blockers=`{','.join(result.blockers) or 'none'}`"
        )
    lines.extend(["", "## Summary"])
    for key, value in backtest.summary.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(backtest.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in backtest.hard_prohibitions))
    return "\n".join(lines).rstrip() + "\n"


def render_no_bid_min_price_lottery_study_markdown(
    study: NoBidMinPriceLotteryStudy,
    *,
    json_path: str | None = None,
) -> str:
    lines = [
        f"# No-Bid Min-Price Lottery Study - {study.session_date}",
        "",
        f"- generated_at_utc: `{study.generated_at_utc.isoformat()}`",
        f"- issue: `{study.issue}`",
        f"- trading_boundary: `{study.trading_boundary}`",
        f"- source_case_count: `{study.source_case_count}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Cases"])
    for case in study.cases:
        lines.append(
            f"- `{case.case_id}` `{case.reproducible_edge_status}`: entry_fillability="
            f"`{case.entry_fillability_status}`, exit_fillability=`{case.exit_fillability_status}`, "
            f"human_rebound=`{case.human_observed_rebound_status}`, final_pnl="
            f"`{case.final_score_pnl_usd:.4f}`, blockers=`{','.join(case.blockers) or 'none'}`"
        )
    lines.extend(["", "## Summary"])
    for key, value in study.summary.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(study.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in study.hard_prohibitions))
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


def _fixture_for_case(case_id: str) -> ReplayFixture | None:
    fixtures = {
        "wnba-phx-atl-atlanta-comeback-low-band": ReplayFixture(
            case_id=case_id,
            entry_price=0.32,
            target_price=0.45,
            score_gap=-8,
            spread=0.02,
            max_allowed_price=0.45,
            max_allowed_spread=0.02,
            final_side_won=True,
            evidence_note="Atlanta low-band comeback window: 5-share fixture wins at final score and stays within the held WNBA max-price gate.",
        ),
        "wnba-dal-nyl-dallas-q2-low-band": ReplayFixture(
            case_id=case_id,
            entry_price=0.23,
            target_price=0.45,
            score_gap=-6,
            spread=0.02,
            max_allowed_price=0.45,
            max_allowed_spread=0.02,
            final_side_won=True,
            evidence_note="Dallas Q2 low-band fixture wins at final score and remains fillable under the 2c spread gate.",
        ),
        "wnba-wsh-sea-seattle-q1-rebound": ReplayFixture(
            case_id=case_id,
            entry_price=0.26,
            target_price=0.45,
            score_gap=-7,
            spread=0.01,
            max_allowed_price=0.45,
            max_allowed_spread=0.02,
            final_side_won=True,
            evidence_note="Seattle Q1 rebound fixture compares the early 25c/26c window with final-score conversion.",
        ),
        "nba-okc-sas-thunder-q4-subpenny-negative": ReplayFixture(
            case_id=case_id,
            entry_price=0.005,
            target_price=0.01,
            shares=606.0,
            score_gap=-21,
            spread=0.005,
            max_allowed_price=0.01,
            max_allowed_spread=0.01,
            final_side_won=False,
            duplicate_intent_count=3,
            evidence_note="Thunder Q4 subpenny fixture approximates the three duplicate late buys and final loser accounting.",
        ),
    }
    return fixtures.get(case_id)


def _score_fixture(case: ReplayCase, fixture: ReplayFixture) -> ReplayFixtureBacktestResult:
    fillability_passed = fixture.entry_price <= fixture.max_allowed_price and fixture.spread <= fixture.max_allowed_spread
    score_gap_available = fixture.score_gap is not None
    duplicate_cooldown_passed = fixture.duplicate_intent_count <= 1
    target_fill_pnl = None
    if fixture.target_price is not None:
        target_fill_pnl = round((fixture.target_price - fixture.entry_price) * fixture.shares, 4)
    final_price = 1.0 if fixture.final_side_won else 0.0
    final_score_pnl = round((final_price - fixture.entry_price) * fixture.shares, 4)
    blockers: list[str] = []
    if not fillability_passed:
        blockers.append("fillability_gate_failed")
    if not score_gap_available:
        blockers.append("score_gap_missing")
    if not duplicate_cooldown_passed:
        blockers.append("duplicate_intent_cooldown_required")
    if final_score_pnl <= 0:
        blockers.append("final_score_negative_edge")
    recommendation = _fixture_recommendation(case, blockers)
    return ReplayFixtureBacktestResult(
        case_id=case.case_id,
        event_id=case.event_id,
        league=case.league,
        side=case.side,
        expected_direction=case.expected_direction,
        fillability_passed=fillability_passed,
        score_gap_available=score_gap_available,
        duplicate_cooldown_passed=duplicate_cooldown_passed,
        target_fill_pnl_usd=target_fill_pnl,
        final_score_pnl_usd=final_score_pnl,
        recommendation=recommendation,
        blockers=blockers,
        evidence_note=fixture.evidence_note,
    )


def _fixture_recommendation(case: ReplayCase, blockers: list[str]) -> str:
    if case.expected_direction == "negative_case":
        return "quarantine_until_independent_replay_proves_edge"
    if blockers:
        return "keep_monitor_only_until_blockers_clear"
    return "eligible_for_entry_timing_matrix_not_live_promotion"


def _fixture_backtest_summary(results: list[ReplayFixtureBacktestResult]) -> dict[str, Any]:
    eligible_positive = [
        result.case_id
        for result in results
        if result.expected_direction == "positive_candidate" and not result.blockers
    ]
    quarantined = [
        result.case_id
        for result in results
        if result.recommendation == "quarantine_until_independent_replay_proves_edge"
    ]
    return {
        "result_count": len(results),
        "eligible_positive_case_count": len(eligible_positive),
        "eligible_positive_case_ids": eligible_positive,
        "quarantined_case_count": len(quarantined),
        "quarantined_case_ids": quarantined,
        "net_final_score_pnl_usd": round(sum(result.final_score_pnl_usd for result in results), 4),
        "live_promotion_allowed": False,
    }


def _fixture_backtest_next_actions(summary: dict[str, Any]) -> list[str]:
    actions = [
        "Feed eligible positive WNBA cases into #55 entry-timing matrix rows with no live promotion by this artifact alone.",
        "Keep q4_subpenny_hype_bounce and no_bid_min_price_lottery_v1 quarantined unless independent replay shows durable edge.",
        "Route any future signal enable/disable change through #69 event-control readback artifacts.",
    ]
    if summary.get("quarantined_case_count", 0):
        actions.append("Keep #61 residual Thunder reconciliation separate from fixture replay scoring.")
    return actions


def _is_no_bid_min_price_case(result: ReplayFixtureBacktestResult) -> bool:
    case_id = result.case_id.lower()
    return (
        "subpenny" in case_id
        or "min-price" in case_id
        or result.recommendation == "quarantine_until_independent_replay_proves_edge"
    )


def _build_no_bid_case(
    result: ReplayFixtureBacktestResult,
    fixture: ReplayFixture,
) -> NoBidMinPriceLotteryStudyCase:
    entry_fillability_status = "entry_fillable_from_observed_direct_clob" if result.fillability_passed else "entry_unproven"
    human_observed_rebound_status = (
        "hype_rebound_observed_but_exit_unproven"
        if result.target_fill_pnl_usd is not None and result.target_fill_pnl_usd > 0
        else "no_rebound_observed"
    )
    reproducible_blockers = list(dict.fromkeys([*result.blockers, "independent_positive_replay_missing"]))
    if result.target_fill_pnl_usd is not None and result.target_fill_pnl_usd > 0:
        reproducible_blockers.append("target_fill_observed_as_theoretical_not_reconciled_exit")
    if result.final_score_pnl_usd <= 0 and "final_score_negative_edge" not in reproducible_blockers:
        reproducible_blockers.append("final_score_negative_edge")
    exit_fillability_status = (
        "target_exit_unproven_no_live_reconciled_fill"
        if result.target_fill_pnl_usd is not None and result.target_fill_pnl_usd > 0
        else "target_exit_missing"
    )
    reproducible_edge_status = "quarantine_disabled"
    return NoBidMinPriceLotteryStudyCase(
        case_id=result.case_id,
        event_id=result.event_id,
        league=result.league,
        side=result.side,
        observed_entry_price=fixture.entry_price,
        observed_target_price=fixture.target_price,
        observed_shares=fixture.shares,
        entry_fillability_status=entry_fillability_status,
        exit_fillability_status=exit_fillability_status,
        human_observed_rebound_status=human_observed_rebound_status,
        reproducible_edge_status=reproducible_edge_status,
        final_score_pnl_usd=result.final_score_pnl_usd,
        target_fill_pnl_usd=result.target_fill_pnl_usd,
        duplicate_cooldown_passed=result.duplicate_cooldown_passed,
        no_bid_or_ask_only_period=fixture.entry_price <= 0.01,
        blockers=reproducible_blockers,
        event_control_recommendation={
            "event_control_action": "quarantine_disabled",
            "runtime_mutation_allowed": False,
            "live_promotion_allowed": False,
            "recommended_signal_toggles": {
                "late_game_min_price_add": False,
                "no_bid_min_price_lottery_v1": False,
                "q4_subpenny_hype_bounce": False,
            },
            "recommended_parameters": {
                "min_price_lottery_allowed": False,
                "max_entry_price": fixture.entry_price,
                "max_event_notional_usd": 0.0,
                "duplicate_intent_cooldown_required": True,
                "required_positive_replay_case_count": 3,
            },
            "required_gates": [
                "direct_clob_entry_and_exit_fillability_replay",
                "duplicate_intent_cooldown_replay",
                "positive_final_score_or_target_exit_edge",
                "event_control_readback_review",
                "fresh_strategy_plan_json",
                "explicit_operator_and_janus_approval",
            ],
        },
        evidence_note=(
            f"{result.evidence_note} This study treats the apparent target rebound as human-observed "
            "hype only until independent replay proves both entry and exit fillability."
        ),
    )


def _no_bid_study_summary(cases: list[NoBidMinPriceLotteryStudyCase]) -> dict[str, Any]:
    reproducible = [case.case_id for case in cases if case.reproducible_edge_status == "reproducible_positive_edge"]
    quarantined = [case.case_id for case in cases if case.reproducible_edge_status == "quarantine_disabled"]
    return {
        "study_case_count": len(cases),
        "human_observed_rebound_count": sum(
            1 for case in cases if case.human_observed_rebound_status == "hype_rebound_observed_but_exit_unproven"
        ),
        "reproducible_positive_case_count": len(reproducible),
        "reproducible_positive_case_ids": reproducible,
        "quarantined_case_count": len(quarantined),
        "quarantined_case_ids": quarantined,
        "runtime_mutation_allowed": False,
        "live_promotion_allowed": False,
        "strategy_confidence": "negative_control_quarantine",
    }


def _no_bid_study_next_actions(summary: dict[str, Any]) -> list[str]:
    if not summary.get("study_case_count"):
        return ["No no-bid/min-price cases found; rerun after a postgame artifact records one."]
    return [
        "Keep no_bid_min_price_lottery_v1 disabled in event-control and StrategyPlan templates.",
        "Require independent direct-CLOB replay showing entry fillability, exit fillability, cooldown behavior, and positive edge before unquarantine.",
        "Use #69 only for readback/recommendation review; do not mutate event-control current.json from this artifact.",
        "Keep WNBA low-band rebound review separate from NBA no-bid/min-price lottery behavior.",
    ]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _bullet_lines(items: list[str] | Any) -> list[str]:
    rendered = [f"- {item}" for item in items if item]
    return rendered or ["- none"]


def _format_optional_money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"
