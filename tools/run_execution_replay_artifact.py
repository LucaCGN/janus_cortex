from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from dataclasses import asdict, replace
from types import SimpleNamespace

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.controller_vnext import (  # noqa: E402
    DEFAULT_VNEXT_PROFILE,
    DEFAULT_VNEXT_STOP_MAP,
    apply_stop_overlay,
    build_state_lookup,
    decorate_trade_frame_with_vnext_sizing,
)
from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS, build_backtest_result  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.llm_experiment import _LLMBudgetState  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (  # noqa: E402
    DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    build_master_router_selection_priors,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.backtests.registry import REPLAY_HF_STRATEGY_GROUP  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.replay import (  # noqa: E402
    REPLAY_BLOCKER_SUMMARY_COLUMNS,
    REPLAY_CANDIDATE_LIFECYCLE_COLUMNS,
    REPLAY_DIVERGENCE_COLUMNS,
    REPLAY_GAME_GAP_COLUMNS,
    REPLAY_HISTORICAL_BIDASK_COLUMNS,
    REPLAY_PORTFOLIO_COLUMNS,
    REPLAY_QUARTER_SUMMARY_COLUMNS,
    REPLAY_QUOTE_COVERAGE_COLUMNS,
    REPLAY_SIGNAL_SUMMARY_COLUMNS,
    REPLAY_SLATE_EXPECTATION_COLUMNS,
    REPLAY_WINDOW_SUMMARY_COLUMNS,
    ReplaySubject,
    _LIVE_MASTER_ROUTER_KWARGS,
    _LIVE_UNIFIED_KWARGS,
    _LIVE_UNIFIED_LLM_LANE,
    _blocker_summary_frame,
    _candidate_lifecycle_frame,
    _divergence_frame,
    _game_gap_frame,
    _load_regular_season_trade_frames,
    _portfolio_summary_for_frames,
    _quarter_summary_frame,
    _subject_summary_frame,
    _window_summary_frame,
    build_controller_context,
    build_replay_slate_expectation_frame,
    load_finished_replay_contexts,
    run_postseason_execution_replay,
    simulate_replay_trade_frames,
    write_replay_artifacts,
)
from app.data.pipelines.daily.nba.analysis.backtests.specs import ReplayRunResult  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.unified_router import build_unified_router_trade_frame  # noqa: E402
from app.data.pipelines.daily.nba.analysis.contracts import ReplayRunRequest  # noqa: E402
from app.modules.nba.execution.contracts import LIVE_FALLBACK_CONTROLLER, LIVE_PRIMARY_CONTROLLER  # noqa: E402


DEFAULT_SHARED_ROOT = Path(r"C:\code-personal\janus-local\janus_cortex\shared")
ML_FOCUS_REPLAY_FAMILIES = ("inversion", "quarter_open_reprice", "micro_momentum_continuation")
REPLAY_HF_FAMILIES = (
    "micro_momentum_continuation",
    "panic_fade_fast",
    "quarter_open_reprice",
    "halftime_gap_fill",
    "lead_fragility",
)
REGULAR_REPLAY_FAMILIES = (
    *DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    *DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    *REPLAY_HF_FAMILIES,
)


def _normalize_game_id(value: object) -> str:
    text = str(value or "").strip()
    return text.zfill(10) if text.isdigit() else text


def _normalize_trade_frame_game_ids(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "game_id" not in frame.columns:
        return frame
    copy = frame.copy()
    copy["game_id"] = copy["game_id"].map(_normalize_game_id)
    return copy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a phase-scoped execution replay artifact without publishing replay-lane reports."
    )
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--season-phase", default="regular_season")
    parser.add_argument("--season-phase-member", action="append", default=[])
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--artifact-name", default="regular_season_execution_replay")
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--signal-max-age-seconds", type=float, default=60.0)
    parser.add_argument("--quote-max-age-seconds", type=float, default=30.0)
    parser.add_argument("--max-spread-cents", type=float, default=2.0)
    parser.add_argument("--proxy-min-spread-cents", type=float, default=1.0)
    parser.add_argument("--proxy-max-spread-cents", type=float, default=6.0)
    parser.add_argument("--aggressive-exit-slippage-cents", type=float, default=1.0)
    parser.add_argument("--quote-source-mode", default="historical_bidask_l1")
    parser.add_argument("--quote-source-fallback-mode", default="cross_side_last_trade")
    parser.add_argument("--llm-max-budget-usd", type=float, default=0.0)
    parser.add_argument(
        "--fast-standard-frames",
        action="store_true",
        help="Build regular-season replay from precomputed regular trade frames plus selected HF families.",
    )
    parser.add_argument(
        "--family-scope",
        choices=("focus", "all"),
        default="focus",
        help="Replay only the ML focus families or all deterministic families available for regular-season replay.",
    )
    parser.add_argument(
        "--controller-family-scope",
        choices=("focus", "all"),
        default=None,
        help="Keep controller source rows focused or allow all source families. Defaults to --family-scope.",
    )
    return parser.parse_args()


def _regular_replay_families_for_scope(family_scope: str) -> tuple[str, ...]:
    if str(family_scope).strip() == "all":
        return REGULAR_REPLAY_FAMILIES
    return ML_FOCUS_REPLAY_FAMILIES


def _filter_controller_trade_frame_by_family_scope(
    frame: pd.DataFrame,
    *,
    controller_family_scope: str,
) -> pd.DataFrame:
    if frame.empty or str(controller_family_scope).strip() != "focus":
        return frame.copy()
    if "source_strategy_family" not in frame.columns:
        return frame.copy()
    return frame[frame["source_strategy_family"].astype(str).isin(ML_FOCUS_REPLAY_FAMILIES)].copy()


def _regular_fast_subject_frames(
    *,
    combined_state_df: pd.DataFrame,
    request: ReplayRunRequest,
    output_dir: Path,
    family_scope: str = "focus",
    controller_family_scope: str = "focus",
) -> tuple[list[ReplaySubject], dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, object]]:
    regular_router_frames, selection_sample_name = _load_regular_season_trade_frames()
    regular_router_frames = {
        family: _normalize_trade_frame_game_ids(frame)
        for family, frame in regular_router_frames.items()
    }
    replay_families = _regular_replay_families_for_scope(family_scope)
    standard_trade_frames: dict[str, pd.DataFrame] = {}
    for family in replay_families:
        if family in REPLAY_HF_FAMILIES:
            hf_request = replace(
                request,
                season_phase="regular_season",
                season_phases=("regular_season",),
                strategy_family=family,
                strategy_group=REPLAY_HF_STRATEGY_GROUP,
            )
            hf_result = build_backtest_result(combined_state_df, hf_request)
            frame = hf_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            standard_trade_frames[family] = _normalize_trade_frame_game_ids(frame)
            continue
        standard_trade_frames[family] = regular_router_frames.get(
            family,
            pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS),
        ).copy()

    controller_context = build_controller_context(output_dir / "regular_replay_llm_cache.json")
    controller_context.llm_client = None
    controller_context.llm_budget_state = _LLMBudgetState(spent_usd=0.0)
    controller_source = SimpleNamespace(trade_frames=regular_router_frames, state_df=combined_state_df)
    controller_state_lookup = build_state_lookup(combined_state_df)
    deterministic_trades, deterministic_decisions = build_master_router_trade_frame(
        controller_source,
        sample_name="regular_season",
        selection_sample_name=selection_sample_name,
        priors=controller_context.priors,
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
        extra_strategy_families=DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
        **_LIVE_MASTER_ROUTER_KWARGS,
    )
    deterministic_trades = decorate_trade_frame_with_vnext_sizing(
        apply_stop_overlay(deterministic_trades, state_lookup=controller_state_lookup, stop_map=DEFAULT_VNEXT_STOP_MAP),
        profile=DEFAULT_VNEXT_PROFILE,
    )
    deterministic_trades = _normalize_trade_frame_game_ids(deterministic_trades)
    deterministic_trades = _filter_controller_trade_frame_by_family_scope(
        deterministic_trades,
        controller_family_scope=controller_family_scope,
    )
    unified_request = replace(request, llm_max_budget_usd=0.0)
    unified_trades, unified_decisions, unified_token_totals = build_unified_router_trade_frame(
        controller_source,
        sample_name="regular_season",
        selection_sample_name=selection_sample_name,
        priors=controller_context.priors,
        family_profiles=controller_context.family_profiles,
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
        extra_strategy_families=DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
        llm_lane=_LIVE_UNIFIED_LLM_LANE,
        request=unified_request,
        client=None,
        budget_state=controller_context.llm_budget_state,
        cache_store=controller_context.llm_cache_store,
        historical_team_context_lookup=controller_context.historical_team_context_lookup,
        **_LIVE_UNIFIED_KWARGS,
    )
    unified_trades = decorate_trade_frame_with_vnext_sizing(
        apply_stop_overlay(unified_trades, state_lookup=controller_state_lookup, stop_map=DEFAULT_VNEXT_STOP_MAP),
        profile=DEFAULT_VNEXT_PROFILE,
    )
    unified_trades = _normalize_trade_frame_game_ids(unified_trades)
    unified_trades = _filter_controller_trade_frame_by_family_scope(
        unified_trades,
        controller_family_scope=controller_family_scope,
    )
    standard_trade_frames[LIVE_FALLBACK_CONTROLLER] = deterministic_trades.copy()
    standard_trade_frames[LIVE_PRIMARY_CONTROLLER] = unified_trades.copy()
    standard_decision_frames = {
        LIVE_FALLBACK_CONTROLLER: deterministic_decisions.copy(),
        LIVE_PRIMARY_CONTROLLER: unified_decisions.copy(),
    }
    subjects = [
        ReplaySubject(subject_name=family, subject_type="family", standard_frame=frame.copy())
        for family, frame in standard_trade_frames.items()
        if family not in {LIVE_FALLBACK_CONTROLLER, LIVE_PRIMARY_CONTROLLER}
    ]
    subjects.extend(
        [
            ReplaySubject(subject_name=LIVE_FALLBACK_CONTROLLER, subject_type="controller", standard_frame=deterministic_trades.copy()),
            ReplaySubject(subject_name=LIVE_PRIMARY_CONTROLLER, subject_type="controller", standard_frame=unified_trades.copy()),
        ]
    )
    metadata = {
        "fast_standard_frame_replay": True,
        "family_scope": str(family_scope).strip() or "focus",
        "controller_family_scope": str(controller_family_scope).strip() or "focus",
        "replayed_families": list(replay_families),
        "focus_replay_families": list(ML_FOCUS_REPLAY_FAMILIES),
        "selection_sample_name": selection_sample_name,
        "llm_client_available": False,
        "llm_spent_usd": 0.0,
        "unified_token_totals": to_jsonable(unified_token_totals),
    }
    return subjects, standard_trade_frames, standard_decision_frames, metadata


def _run_fast_standard_frame_replay(
    *,
    request: ReplayRunRequest,
    output_dir: Path,
    family_scope: str = "focus",
    controller_family_scope: str = "focus",
) -> ReplayRunResult:
    contexts, combined_state_df, manifest_df = load_finished_replay_contexts(
        season=request.season,
        analysis_version=request.analysis_version,
        season_phase=request.season_phase,
        season_phases=tuple(request.season_phases or (request.season_phase,)),
    )
    for context in contexts.values():
        context.historical_bidask_df = pd.DataFrame(columns=REPLAY_HISTORICAL_BIDASK_COLUMNS)
        context.quote_coverage = None
    subjects, standard_trade_frames, standard_decision_frames, controller_meta = _regular_fast_subject_frames(
        combined_state_df=combined_state_df,
        request=request,
        output_dir=output_dir,
        family_scope=family_scope,
        controller_family_scope=controller_family_scope,
    )
    replay_trade_frames, signal_summary_df, attempt_trace_df = simulate_replay_trade_frames(
        subjects,
        contexts=contexts,
        request=request,
    )
    standard_portfolio_df = _portfolio_summary_for_frames(standard_trade_frames, request=request)
    standard_portfolio_df["mode"] = "standard"
    replay_portfolio_df = _portfolio_summary_for_frames(replay_trade_frames, request=request)
    replay_portfolio_df["mode"] = "replay"
    live_summary_df = pd.DataFrame(columns=["run_id", "subject_name", "game_id", "live_trade_count", "entry_submitted_count", "position_opened_count"])
    subject_summary_df = _subject_summary_frame(
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        signal_summary_df=signal_summary_df,
        standard_portfolio_df=standard_portfolio_df,
        replay_portfolio_df=replay_portfolio_df,
        live_summary_df=live_summary_df,
        games_replayed=len(contexts),
    )
    game_gap_df = _game_gap_frame(
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        signal_summary_df=signal_summary_df,
        manifest_df=manifest_df,
    )
    divergence_df = _divergence_frame(signal_summary_df)
    quarter_summary_df = _quarter_summary_frame(
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        signal_summary_df=signal_summary_df,
    )
    window_summary_df = _window_summary_frame(signal_summary_df)
    candidate_lifecycle_df = _candidate_lifecycle_frame(signal_summary_df)
    slate_expectation_df = build_replay_slate_expectation_frame(game_gap_df=game_gap_df, signal_summary_df=signal_summary_df)
    blocker_summary_df = _blocker_summary_frame(signal_summary_df)
    quote_coverage_df = pd.DataFrame(columns=REPLAY_QUOTE_COVERAGE_COLUMNS)
    historical_bidask_df = pd.DataFrame(columns=REPLAY_HISTORICAL_BIDASK_COLUMNS)
    portfolio_summary_df = pd.concat([standard_portfolio_df, replay_portfolio_df], ignore_index=True, sort=False)
    payload = {
        "season": request.season,
        "season_phase": request.season_phase,
        "season_phases": list(request.season_phases or (request.season_phase,)),
        "analysis_version": request.analysis_version,
        "finished_game_count": int(len(contexts)),
        "state_panel_game_count": int((manifest_df["state_source"] == "state_panel").sum()) if not manifest_df.empty else 0,
        "derived_bundle_game_count": int((manifest_df["state_source"] == "derived_bundle").sum()) if not manifest_df.empty else 0,
        "replay_contract": {
            "poll_interval_seconds": request.poll_interval_seconds,
            "signal_max_age_seconds": request.signal_max_age_seconds,
            "quote_max_age_seconds": request.quote_max_age_seconds,
            "max_spread_cents": request.max_spread_cents,
            "proxy_min_spread_cents": request.proxy_min_spread_cents,
            "proxy_max_spread_cents": request.proxy_max_spread_cents,
            "quote_source_mode": request.quote_source_mode,
            "quote_source_fallback_mode": request.quote_source_fallback_mode,
            "quote_proxy": request.quote_proxy,
        },
        "controller_meta": controller_meta,
        "benchmark": {
            "game_manifest": to_jsonable(manifest_df.to_dict(orient="records")),
            "subject_summary": to_jsonable(subject_summary_df.to_dict(orient="records")),
            "game_gap": to_jsonable(game_gap_df.to_dict(orient="records")),
            "divergence_summary": to_jsonable(divergence_df.to_dict(orient="records")),
            "signal_summary": to_jsonable(signal_summary_df.to_dict(orient="records")),
            "quarter_summary": to_jsonable(quarter_summary_df.to_dict(orient="records")),
            "window_summary": to_jsonable(window_summary_df.to_dict(orient="records")),
            "candidate_lifecycle": to_jsonable(candidate_lifecycle_df.to_dict(orient="records")),
            "slate_expectation": to_jsonable(slate_expectation_df.to_dict(orient="records")),
            "blocker_summary": to_jsonable(blocker_summary_df.to_dict(orient="records")),
            "historical_bidask_l1": [],
            "quote_coverage_summary": [],
            "portfolio_summary": to_jsonable(portfolio_summary_df.to_dict(orient="records")),
            "live_summary": [],
            "standard_controller_decisions": {
                subject: to_jsonable(frame.to_dict(orient="records"))
                for subject, frame in standard_decision_frames.items()
            },
        },
    }
    return ReplayRunResult(
        payload=payload,
        standard_trade_frames=standard_trade_frames,
        replay_trade_frames=replay_trade_frames,
        benchmark_frames={
            "game_manifest": manifest_df,
            "subject_summary": subject_summary_df,
            "game_gap": game_gap_df if not game_gap_df.empty else pd.DataFrame(columns=REPLAY_GAME_GAP_COLUMNS),
            "divergence_summary": divergence_df if not divergence_df.empty else pd.DataFrame(columns=REPLAY_DIVERGENCE_COLUMNS),
            "signal_summary": signal_summary_df if not signal_summary_df.empty else pd.DataFrame(columns=REPLAY_SIGNAL_SUMMARY_COLUMNS),
            "attempt_trace": attempt_trace_df,
            "portfolio_summary": portfolio_summary_df[list(REPLAY_PORTFOLIO_COLUMNS)],
            "quarter_summary": quarter_summary_df if not quarter_summary_df.empty else pd.DataFrame(columns=REPLAY_QUARTER_SUMMARY_COLUMNS),
            "window_summary": window_summary_df if not window_summary_df.empty else pd.DataFrame(columns=REPLAY_WINDOW_SUMMARY_COLUMNS),
            "candidate_lifecycle": candidate_lifecycle_df if not candidate_lifecycle_df.empty else pd.DataFrame(columns=REPLAY_CANDIDATE_LIFECYCLE_COLUMNS),
            "slate_expectation": slate_expectation_df if not slate_expectation_df.empty else pd.DataFrame(columns=REPLAY_SLATE_EXPECTATION_COLUMNS),
            "blocker_summary": blocker_summary_df if not blocker_summary_df.empty else pd.DataFrame(columns=REPLAY_BLOCKER_SUMMARY_COLUMNS),
            "historical_bidask_l1": historical_bidask_df,
            "quote_coverage_summary": quote_coverage_df,
            "live_summary": live_summary_df,
        },
    )


def main() -> None:
    args = _parse_args()
    shared_root = Path(args.shared_root).expanduser().resolve()
    output_dir = shared_root / "artifacts" / "replay-engine-hf" / args.season / args.artifact_name
    output_dir.mkdir(parents=True, exist_ok=True)
    season_phases = tuple(
        str(value).strip()
        for value in (args.season_phase_member or [args.season_phase])
        if str(value).strip()
    )
    request = ReplayRunRequest(
        season=args.season,
        season_phase=args.season_phase,
        season_phases=season_phases,
        analysis_version=args.analysis_version,
        poll_interval_seconds=args.poll_interval_seconds,
        signal_max_age_seconds=args.signal_max_age_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
        max_spread_cents=args.max_spread_cents,
        proxy_min_spread_cents=args.proxy_min_spread_cents,
        proxy_max_spread_cents=args.proxy_max_spread_cents,
        aggressive_exit_slippage_cents=args.aggressive_exit_slippage_cents,
        quote_source_mode=args.quote_source_mode,
        quote_source_fallback_mode=args.quote_source_fallback_mode,
        llm_max_budget_usd=args.llm_max_budget_usd,
    )
    if args.fast_standard_frames:
        request = replace(request, quote_source_mode="cross_side_last_trade", quote_source_fallback_mode="")
        controller_family_scope = args.controller_family_scope or args.family_scope
        result = _run_fast_standard_frame_replay(
            request=request,
            output_dir=output_dir,
            family_scope=args.family_scope,
            controller_family_scope=controller_family_scope,
        )
    else:
        result = run_postseason_execution_replay(request=request, output_dir=output_dir)
    payload = write_replay_artifacts(result, output_dir)
    summary = {
        "artifact_root": str(output_dir),
        "season": payload.get("season"),
        "season_phase": payload.get("season_phase"),
        "season_phases": payload.get("season_phases"),
        "finished_game_count": payload.get("finished_game_count"),
        "state_panel_game_count": payload.get("state_panel_game_count"),
        "derived_bundle_game_count": payload.get("derived_bundle_game_count"),
        "subject_count": len(payload.get("benchmark", {}).get("subject_summary") or []),
        "signal_count": len(payload.get("benchmark", {}).get("signal_summary") or []),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
