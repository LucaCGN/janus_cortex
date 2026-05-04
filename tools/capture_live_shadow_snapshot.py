from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.backtests.registry import (
    REPLAY_HF_STRATEGY_GROUP,
    build_strategy_registry,
)
from app.data.pipelines.daily.nba.analysis.contracts import DEFAULT_OUTPUT_ROOT
from app.data.pipelines.daily.nba.analysis.llm_strategy_lane import (
    LLM_CONTROLLER_VARIANTS,
    _build_decision_clusters,
    _run_controller_variant,
    build_llm_candidate_dataset,
)
from app.data.pipelines.daily.nba.analysis.mart_game_profiles import derive_game_rows, load_analysis_bundle
from app.data.pipelines.daily.nba.analysis.mart_state_panel import build_state_rows_for_side
from app.data.pipelines.daily.nba.analysis.ml_trading_lane import (
    DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD,
    DEFAULT_FOCUS_RANK_THRESHOLD,
    FOCUS_STRATEGY_FAMILIES,
    UNIFIED_CONTROLLER_NAME,
    _build_family_overall_frame,
    _build_historical_context_frame,
    _build_heuristic_execute_score,
    _build_heuristic_rank_score,
    _combine_sidecar_candidates,
    _load_regular_season_trade_frames,
    _select_calibrated_controller_candidates,
    _select_focus_family_candidates,
)
from app.modules.nba.execution.adapter import build_live_creds, fetch_latest_orderbook_summary, resolve_trading_account
from app.modules.nba.execution.runner import _infer_live_coverage_status


TARGET_FAMILIES = (
    "quarter_open_reprice",
    "micro_momentum_continuation",
    "inversion",
)
TARGET_LLM_VARIANTS = (
    "llm_selector_core_windows_v2",
    "llm_template_compiler_core_windows_v2",
)
ML_SHADOW_REQUIRED_FIELDS = (
    "sidecar_probability",
    "calibrated_confidence",
    "calibrated_execution_likelihood",
    "focus_family_flag",
    "feed_fresh_flag",
    "orderbook_available_flag",
    "min_required_notional_usd",
    "budget_affordable_flag",
)
LIVE_ENTRY_TARGET_NOTIONAL_USD = 0.0
LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD = 10.0
POLYMARKET_MIN_SHARES = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a read-only live shadow snapshot for daily validation.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--game-id", action="append", dest="game_ids", required=True)
    parser.add_argument("--api-root", default="http://127.0.0.1:8010")
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--allow-incomplete-ml-shadow",
        action="store_true",
        help="Do not fail when the ML shadow payload is missing required live fields.",
    )
    return parser.parse_args()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_json(url: str) -> dict[str, Any]:
    req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _latest_state_row(state_df: pd.DataFrame, *, game_id: str, team_side: str) -> dict[str, Any] | None:
    if state_df.empty:
        return None
    rows = state_df[
        (state_df["game_id"].astype(str) == str(game_id))
        & (state_df["team_side"].astype(str) == str(team_side))
    ]
    if rows.empty:
        return None
    return rows.sort_values("state_index", kind="mergesort").iloc[-1].to_dict()


def _signal_id(subject_name: str, game_id: Any, team_side: Any, entry_state_index: Any) -> str:
    try:
        entry_index = int(float(entry_state_index))
    except (TypeError, ValueError):
        entry_index = 0
    return f"{subject_name}|{str(game_id)}|{str(team_side or '')}|{entry_index}"


def _matchup_label(bundle: dict[str, Any]) -> str:
    game = bundle.get("game") or {}
    away = str(game.get("away_team_slug") or game.get("away_team_name") or "Away")
    home = str(game.get("home_team_slug") or game.get("home_team_name") or "Home")
    return f"{away} at {home}"


def _subject_summary_from_replay_submission(shared_root: Path) -> pd.DataFrame:
    submission_path = shared_root / "reports" / "replay-engine-hf" / "benchmark_submission.json"
    if not submission_path.exists():
        return pd.DataFrame()
    payload = json.loads(submission_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for subject in payload.get("subjects") or []:
        candidate_id = str(subject.get("candidate_id") or subject.get("display_name") or "").strip()
        if not candidate_id:
            continue
        metrics = subject.get("metrics") or {}
        candidate_kind = str(subject.get("candidate_kind") or "")
        rows.append(
            {
                "subject_name": candidate_id,
                "subject_type": "family" if "family" in candidate_kind else "controller",
                "execution_rate": metrics.get("execution_rate"),
                "replay_ending_bankroll": metrics.get("replay_ending_bankroll"),
                "replay_trade_count": metrics.get("replay_trade_count"),
                "standard_trade_count": metrics.get("standard_trade_count"),
            }
        )
    return pd.DataFrame(rows)


def _parse_controller_trace(run_root: Path) -> dict[str, dict[str, Any]]:
    trace_path = run_root / "controller_trace.jsonl"
    latest: dict[str, dict[str, Any]] = {}
    if not trace_path.exists():
        return latest
    for raw_line in trace_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if str(row.get("stage") or "") != "game_card":
            continue
        game_id = str(row.get("game_id") or "")
        if not game_id:
            continue
        latest[game_id] = row
    return latest


def _build_current_state(
    *,
    game_ids: list[str],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    bundles: dict[str, dict[str, Any]] = {}
    diagnostics_by_game: dict[str, dict[str, Any]] = {}
    orderbooks_by_game: dict[str, dict[str, Any]] = {}
    state_rows: list[dict[str, Any]] = []
    with managed_connection() as connection:
        account = resolve_trading_account(connection)
        creds = build_live_creds(account)
        for game_id in game_ids:
            bundle = load_analysis_bundle(connection, game_id=str(game_id))
            if bundle is None:
                diagnostics_by_game[str(game_id)] = {"error": "game_not_found"}
                orderbooks_by_game[str(game_id)] = {}
                continue
            selected_market = bundle.get("selected_market") or {}
            series = selected_market.get("series") or []
            current_orderbooks: dict[str, Any] = {}
            market_id = str(selected_market.get("market_id") or "")
            for series_item in series:
                side = str(series_item.get("side") or "")
                token_id = str(series_item.get("token_id") or "")
                if side not in {"home", "away"} or not market_id or not token_id:
                    continue
                try:
                    current_orderbooks[side] = fetch_latest_orderbook_summary(
                        creds=creds,
                        market_id=market_id,
                        token_id=token_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    current_orderbooks[side] = {"error": f"{type(exc).__name__}: {exc}"}
            coverage_status, classification = _infer_live_coverage_status(bundle)
            universe_row = pd.Series(
                {
                    "season": str(bundle["game"].get("season") or "2025-26"),
                    "season_phase": str(bundle["game"].get("season_phase") or "playoffs"),
                    "coverage_status": coverage_status,
                    "classification": classification,
                    "research_ready_flag": classification == "research_ready",
                }
            )
            _, per_game_state_rows, diagnostics = derive_game_rows(
                universe_row=universe_row,
                bundle=bundle,
                analysis_version="v1_0_1",
                computed_at=_now_utc(),
                build_state_rows_for_side=build_state_rows_for_side,
            )
            bundles[str(game_id)] = bundle
            diagnostics_by_game[str(game_id)] = diagnostics
            orderbooks_by_game[str(game_id)] = current_orderbooks
            state_rows.extend(per_game_state_rows)
    state_df = pd.DataFrame(state_rows)
    return state_df, bundles, diagnostics_by_game, orderbooks_by_game


def _build_family_signal_rows(
    *,
    state_df: pd.DataFrame,
    bundles: dict[str, dict[str, Any]],
    orderbooks_by_game: dict[str, dict[str, Any]],
    snapshot_at: datetime,
    game_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    registry = build_strategy_registry(strategy_group=REPLAY_HF_STRATEGY_GROUP)
    rows: list[dict[str, Any]] = []
    standard_frames: dict[str, pd.DataFrame] = {}
    if state_df.empty:
        return rows, standard_frames
    for family in TARGET_FAMILIES:
        definition = registry.get(family)
        if definition is None:
            continue
        trade_df = pd.DataFrame(definition.simulator(state_df, slippage_cents=0))
        if not trade_df.empty:
            trade_df["game_id"] = trade_df["game_id"].astype(str)
            trade_df["team_side"] = trade_df["team_side"].astype(str)
            trade_df = trade_df[trade_df["game_id"].isin([str(game_id) for game_id in game_ids])].copy()
        standard_frames[family] = trade_df
        grouped_keys = {(str(row.get("game_id") or ""), str(row.get("team_side") or "")) for row in trade_df.to_dict(orient="records")}
        for record in trade_df.to_dict(orient="records"):
            game_id = str(record.get("game_id") or "")
            team_side = str(record.get("team_side") or "")
            latest_state = _latest_state_row(state_df, game_id=game_id, team_side=team_side)
            latest_state_index = int(latest_state.get("state_index") or -1) if latest_state else None
            entry_state_index = int(record.get("entry_state_index") or -1)
            signal_entry_at = _safe_datetime(record.get("entry_at"))
            latest_event_at = _safe_datetime((latest_state or {}).get("event_at"))
            signal_age_seconds = None
            if signal_entry_at is not None and latest_event_at is not None:
                signal_age_seconds = max(0.0, (latest_event_at - signal_entry_at).total_seconds())
            state_lag = None if latest_state_index is None else max(0, latest_state_index - entry_state_index)
            orderbook = (orderbooks_by_game.get(game_id) or {}).get(team_side) or {}
            quote_time = _safe_datetime(orderbook.get("timestamp"))
            quote_age_seconds = None
            if quote_time is not None:
                quote_age_seconds = max(0.0, (snapshot_at - quote_time).total_seconds())
            spread_cents = _safe_float(orderbook.get("spread_cents"))
            best_bid = _safe_float(orderbook.get("best_bid"))
            best_ask = _safe_float(orderbook.get("best_ask"))

            freshness_reason = None
            shadow_action = "would_enter"
            if latest_state is None:
                shadow_action = "wait"
                freshness_reason = "latest_state_unavailable"
            elif state_lag is not None and state_lag > 0 and signal_age_seconds is not None and signal_age_seconds > 60.0:
                shadow_action = "wait"
                freshness_reason = "stale_signal"
            elif best_bid is None or best_ask is None:
                shadow_action = "wait"
                freshness_reason = "orderbook_unavailable"
            elif spread_cents is not None and spread_cents > 2.0:
                shadow_action = "wait"
                freshness_reason = "spread_too_wide"

            rows.append(
                {
                    "candidate_id": family,
                    "subject_name": family,
                    "subject_type": "family",
                    "matchup": _matchup_label(bundles.get(game_id) or {}),
                    "game_id": game_id,
                    "team_side": team_side,
                    "strategy_family": family,
                    "signal_id": _signal_id(family, game_id, team_side, record.get("entry_state_index")),
                    "entry_state_index": entry_state_index,
                    "exit_state_index": int(record.get("exit_state_index") or -1),
                    "signal_entry_at": signal_entry_at.isoformat() if signal_entry_at else None,
                    "signal_exit_at": str(record.get("exit_at") or ""),
                    "signal_entry_price": _safe_float(record.get("entry_price")),
                    "signal_exit_price": _safe_float(record.get("exit_price")),
                    "signal_strength": _safe_float(record.get("signal_strength")),
                    "entry_rule": record.get("entry_rule"),
                    "exit_rule": record.get("exit_rule"),
                    "opening_band": record.get("opening_band"),
                    "period_label": record.get("period_label"),
                    "score_diff_bucket": record.get("score_diff_bucket"),
                    "context_bucket": record.get("context_bucket"),
                    "entry_metadata_json": record.get("entry_metadata_json"),
                    "latest_state_index": latest_state_index,
                    "latest_event_at": latest_event_at.isoformat() if latest_event_at else None,
                    "state_period_label": (latest_state or {}).get("period_label"),
                    "state_clock_elapsed_seconds": _safe_float((latest_state or {}).get("clock_elapsed_seconds")),
                    "state_seconds_to_game_end": _safe_float((latest_state or {}).get("seconds_to_game_end")),
                    "state_score_diff": _safe_float((latest_state or {}).get("score_diff")),
                    "state_lead_changes_so_far": _safe_float((latest_state or {}).get("lead_changes_so_far")),
                    "state_abs_price_delta_from_open": _safe_float((latest_state or {}).get("abs_price_delta_from_open")),
                    "state_net_points_last_5_events": _safe_float((latest_state or {}).get("net_points_last_5_events")),
                    "first_attempt_signal_age_seconds": signal_age_seconds,
                    "first_attempt_quote_age_seconds": quote_age_seconds,
                    "first_attempt_spread_cents": spread_cents,
                    "first_attempt_state_lag": state_lag,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread_cents": spread_cents,
                    "shadow_action": shadow_action,
                    "shadow_reason": freshness_reason or "eligible",
                    "focus_family_flag": family in FOCUS_STRATEGY_FAMILIES,
                    "executed_flag": False,
                    "no_trade_reason": freshness_reason,
                }
            )
        for game_id in game_ids:
            for team_side in ("away", "home"):
                if (str(game_id), team_side) in grouped_keys:
                    continue
                latest_state = _latest_state_row(state_df, game_id=str(game_id), team_side=team_side)
                rows.append(
                    {
                        "candidate_id": family,
                        "subject_name": family,
                        "subject_type": "family",
                        "matchup": _matchup_label(bundles.get(str(game_id)) or {}),
                        "game_id": str(game_id),
                        "team_side": team_side,
                        "strategy_family": family,
                        "signal_id": None,
                        "entry_state_index": None,
                        "exit_state_index": None,
                        "signal_entry_at": None,
                        "signal_exit_at": None,
                        "signal_entry_price": None,
                        "signal_exit_price": None,
                        "signal_strength": None,
                        "entry_rule": definition.entry_rule,
                        "exit_rule": definition.exit_rule,
                        "opening_band": None,
                        "period_label": None,
                        "score_diff_bucket": None,
                        "context_bucket": None,
                        "entry_metadata_json": None,
                        "latest_state_index": int(latest_state.get("state_index") or -1) if latest_state else None,
                        "latest_event_at": str((latest_state or {}).get("event_at") or ""),
                        "state_period_label": (latest_state or {}).get("period_label"),
                        "state_clock_elapsed_seconds": _safe_float((latest_state or {}).get("clock_elapsed_seconds")),
                        "state_seconds_to_game_end": _safe_float((latest_state or {}).get("seconds_to_game_end")),
                        "state_score_diff": _safe_float((latest_state or {}).get("score_diff")),
                        "state_lead_changes_so_far": _safe_float((latest_state or {}).get("lead_changes_so_far")),
                        "state_abs_price_delta_from_open": _safe_float((latest_state or {}).get("abs_price_delta_from_open")),
                        "state_net_points_last_5_events": _safe_float((latest_state or {}).get("net_points_last_5_events")),
                        "first_attempt_signal_age_seconds": None,
                        "first_attempt_quote_age_seconds": None,
                        "first_attempt_spread_cents": None,
                        "first_attempt_state_lag": None,
                        "best_bid": None,
                        "best_ask": None,
                        "spread_cents": None,
                        "shadow_action": "wait",
                        "shadow_reason": "no_strategy_signal",
                        "focus_family_flag": family in FOCUS_STRATEGY_FAMILIES,
                        "executed_flag": False,
                        "no_trade_reason": "no_strategy_signal",
                    }
                )
    return rows, standard_frames


def _build_controller_rows(
    *,
    live_summary: dict[str, Any],
    state_df: pd.DataFrame,
    orderbooks_by_game: dict[str, dict[str, Any]],
    controller_trace_by_game: dict[str, dict[str, Any]],
    snapshot_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    games = live_summary.get("games") or []
    for game_card in games:
        game_id = str(game_card.get("game_id") or "")
        trace_row = controller_trace_by_game.get(game_id) or {}
        payload = trace_row.get("payload") or {}
        selected_trade = payload.get("selected_trade") or {}
        decision = payload.get("decision") or {}
        team_side = str(selected_trade.get("team_side") or decision.get("selected_team_side") or "")
        latest_state = payload.get("latest_state") or _latest_state_row(state_df, game_id=game_id, team_side=team_side) or {}
        entry_state_index = selected_trade.get("entry_state_index")
        signal_entry_at = _safe_datetime(selected_trade.get("entry_at"))
        latest_event_at = _safe_datetime(latest_state.get("event_at"))
        signal_age_seconds = None
        if signal_entry_at is not None and latest_event_at is not None:
            signal_age_seconds = max(0.0, (latest_event_at - signal_entry_at).total_seconds())
        state_lag = None
        if entry_state_index is not None and latest_state.get("state_index") is not None:
            try:
                state_lag = max(0, int(latest_state.get("state_index")) - int(entry_state_index))
            except (TypeError, ValueError):
                state_lag = None
        orderbook = (orderbooks_by_game.get(game_id) or {}).get(team_side) or {}
        quote_time = _safe_datetime(orderbook.get("timestamp"))
        quote_age_seconds = None
        if quote_time is not None:
            quote_age_seconds = max(0.0, (snapshot_at - quote_time).total_seconds())
        best_bid = _safe_float(orderbook.get("best_bid"))
        best_ask = _safe_float(orderbook.get("best_ask"))
        spread_cents = _safe_float(orderbook.get("spread_cents"))
        strategy_family = str(selected_trade.get("source_strategy_family") or game_card.get("strategy_family") or "")
        rows.append(
            {
                "candidate_id": "ml_controller_focus_calibrator_v2",
                "subject_name": UNIFIED_CONTROLLER_NAME,
                "subject_type": "controller",
                "matchup": game_card.get("matchup"),
                "game_id": game_id,
                "team_side": team_side,
                "strategy_family": strategy_family,
                "signal_id": selected_trade.get("signal_id"),
                "entry_state_index": entry_state_index,
                "signal_entry_at": selected_trade.get("entry_at"),
                "signal_entry_price": _safe_float(selected_trade.get("entry_price")),
                "signal_strength": _safe_float(selected_trade.get("signal_strength")),
                "raw_confidence": _safe_float(decision.get("selected_confidence") or game_card.get("selected_confidence")),
                "selection_source": decision.get("final_source"),
                "selection_reason": decision.get("final_selection_reason") or game_card.get("note"),
                "coverage_status": payload.get("coverage_status") or game_card.get("note"),
                "latest_event_at": latest_event_at.isoformat() if latest_event_at else None,
                "first_attempt_signal_age_seconds": signal_age_seconds,
                "first_attempt_quote_age_seconds": quote_age_seconds,
                "first_attempt_spread_cents": spread_cents,
                "first_attempt_state_lag": state_lag,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_cents": spread_cents,
                "focus_family_flag": strategy_family in FOCUS_STRATEGY_FAMILIES,
                "state_label": game_card.get("state_label"),
                "shadow_action": "support" if strategy_family in FOCUS_STRATEGY_FAMILIES else "wait",
                "shadow_reason": "non_focus_family" if strategy_family not in FOCUS_STRATEGY_FAMILIES else str(game_card.get("note") or "controller_selected_trade"),
            }
        )
    return rows


def _resolve_live_budget_config(run_root: Path) -> dict[str, Any]:
    config = _read_json(run_root / "run_config.json") if run_root.exists() else {}
    entry_target = _safe_float(config.get("entry_target_notional_usd"))
    max_orders = _safe_float(config.get("max_entry_orders_per_game"))
    return {
        "entry_target_notional_usd": entry_target
        if entry_target is not None
        else LIVE_ENTRY_TARGET_NOTIONAL_USD,
        "max_entry_orders_per_game": int(max_orders) if max_orders is not None else 2,
        "max_entry_notional_per_game_usd": _safe_float(config.get("max_entry_notional_per_game_usd"))
        or LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD,
        "polymarket_min_shares": _safe_float(config.get("polymarket_min_shares")) or POLYMARKET_MIN_SHARES,
    }


def _enrich_ml_shadow_frame(frame: pd.DataFrame, *, live_budget_config: dict[str, Any] | None = None) -> pd.DataFrame:
    if frame.empty:
        for field in ML_SHADOW_REQUIRED_FIELDS:
            frame[field] = pd.Series(dtype=object)
        return frame
    budget_config = live_budget_config or {}
    min_shares = _safe_float(budget_config.get("polymarket_min_shares")) or POLYMARKET_MIN_SHARES
    max_entry_notional = (
        _safe_float(budget_config.get("max_entry_notional_per_game_usd"))
        or LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD
    )
    work = frame.copy()
    work["sidecar_probability"] = pd.to_numeric(work.get("sidecar_probability"), errors="coerce").fillna(0.5)
    if "calibrated_confidence" not in work.columns:
        work["calibrated_confidence"] = work["sidecar_probability"]
    else:
        work["calibrated_confidence"] = pd.to_numeric(work["calibrated_confidence"], errors="coerce").fillna(
            work["sidecar_probability"]
        )
    if "heuristic_rank_score" not in work.columns:
        work["heuristic_rank_score"] = work["sidecar_probability"]
    else:
        work["heuristic_rank_score"] = pd.to_numeric(work["heuristic_rank_score"], errors="coerce").fillna(
            work["sidecar_probability"]
        )
    if "heuristic_execute_score" not in work.columns:
        work["heuristic_execute_score"] = _build_heuristic_execute_score(work)
    work["heuristic_execute_score"] = pd.to_numeric(work["heuristic_execute_score"], errors="coerce").fillna(0.5)
    if "calibrated_execution_likelihood" not in work.columns:
        work["calibrated_execution_likelihood"] = work["heuristic_execute_score"]
    else:
        work["calibrated_execution_likelihood"] = pd.to_numeric(
            work["calibrated_execution_likelihood"],
            errors="coerce",
        ).fillna(work["heuristic_execute_score"])
    if "focus_family_flag" not in work.columns:
        work["focus_family_flag"] = work.get("strategy_family", pd.Series(dtype=object)).astype(str).isin(FOCUS_STRATEGY_FAMILIES)
    work["focus_family_flag"] = work["focus_family_flag"].fillna(False).astype(bool)
    best_bid = pd.to_numeric(work.get("best_bid"), errors="coerce")
    best_ask = pd.to_numeric(work.get("best_ask"), errors="coerce")
    signal_entry_price = pd.to_numeric(work.get("signal_entry_price"), errors="coerce")
    coverage_status = work.get("coverage_status", pd.Series([None] * len(work), index=work.index)).astype(str)
    latest_event_at = pd.to_datetime(work.get("latest_event_at"), errors="coerce", utc=True)
    shadow_reason = work.get("shadow_reason", pd.Series([None] * len(work), index=work.index)).astype(str)
    work["feed_fresh_flag"] = (
        latest_event_at.notna()
        & coverage_status.ne("pregame_only")
        & shadow_reason.ne("stale_signal")
    )
    work["orderbook_available_flag"] = best_bid.notna() | best_ask.notna()
    reference_price = best_ask.where(best_ask.notna(), signal_entry_price)
    work["min_required_notional_usd"] = reference_price * float(min_shares)
    work.loc[reference_price.isna(), "min_required_notional_usd"] = np.nan
    budget_affordable = work["min_required_notional_usd"] <= float(max_entry_notional)
    work["budget_affordable_flag"] = pd.Series(budget_affordable, index=work.index, dtype="boolean")
    work.loc[work["min_required_notional_usd"].isna(), "budget_affordable_flag"] = pd.NA
    return work


def _validate_ml_shadow_payload(ml_shadow: dict[str, Any]) -> None:
    candidate_keys = (
        "family_candidates",
        "controller_candidates",
        "focus_family_selected",
        "controller_focus_selected",
        "sidecar_union_selected",
    )
    missing_messages: list[str] = []
    for key in candidate_keys:
        rows = ml_shadow.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        frame = pd.DataFrame(rows)
        missing_fields = [field for field in ML_SHADOW_REQUIRED_FIELDS if field not in frame.columns]
        if missing_fields:
            missing_messages.append(f"{key}: missing columns {', '.join(missing_fields)}")
    if missing_messages:
        raise ValueError(
            "ML shadow payload is missing required live fields. "
            + " | ".join(missing_messages)
        )


def _build_attempt_trace(
    *,
    family_rows: pd.DataFrame,
    snapshot_at: datetime,
) -> pd.DataFrame:
    actual_rows = family_rows[family_rows["signal_id"].notna()].copy()
    if actual_rows.empty:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    for row in actual_rows.to_dict(orient="records"):
        quote_time = _safe_datetime(row.get("latest_event_at")) or snapshot_at
        result = "eligible" if str(row.get("shadow_action") or "") == "would_enter" else "no_trade"
        records.append(
            {
                "signal_id": row.get("signal_id"),
                "game_id": row.get("game_id"),
                "cycle_at": snapshot_at.isoformat(),
                "quote_time": quote_time.isoformat(),
                "entry_state_index": row.get("entry_state_index"),
                "latest_state_index": row.get("latest_state_index"),
                "quote_age_seconds": row.get("first_attempt_quote_age_seconds"),
                "spread_cents": row.get("first_attempt_spread_cents"),
                "attempt_index": 0,
                "result": result,
                "reason": row.get("shadow_reason"),
            }
        )
    return pd.DataFrame(records)


def _build_ml_shadow(
    *,
    family_rows: pd.DataFrame,
    controller_rows: pd.DataFrame,
    live_budget_config: dict[str, Any],
) -> dict[str, Any]:
    regular_trade_frames = _load_regular_season_trade_frames(Path(DEFAULT_OUTPUT_ROOT))
    historical_context_df = _build_historical_context_frame(regular_trade_frames)
    historical_family_df = _build_family_overall_frame(regular_trade_frames)

    family_predictions_df = family_rows[family_rows["signal_id"].notna()].copy()
    if not family_predictions_df.empty:
        family_predictions_df = family_predictions_df.merge(
            historical_context_df,
            on=["strategy_family", "opening_band", "period_label", "context_bucket"],
            how="left",
        )
        family_predictions_df = family_predictions_df.merge(
            historical_family_df,
            on=["strategy_family"],
            how="left",
        )
        family_predictions_df["raw_confidence"] = pd.Series([None] * len(family_predictions_df), index=family_predictions_df.index)
        family_predictions_df["heuristic_rank_score"] = _build_heuristic_rank_score(family_predictions_df)
        family_predictions_df["rank_score"] = family_predictions_df["heuristic_rank_score"]
        family_predictions_df["heuristic_execute_score"] = _build_heuristic_execute_score(family_predictions_df)
        family_predictions_df["sidecar_probability"] = family_predictions_df["rank_score"]
        family_predictions_df["selection_source"] = "focused_family_reranker_heuristic_proxy"
        family_predictions_df = _enrich_ml_shadow_frame(family_predictions_df, live_budget_config=live_budget_config)
    else:
        family_predictions_df = pd.DataFrame()

    controller_predictions_df = controller_rows.copy()
    if not controller_predictions_df.empty:
        controller_predictions_df["sidecar_probability"] = pd.to_numeric(
            controller_predictions_df["raw_confidence"],
            errors="coerce",
        ).fillna(0.5)
        controller_predictions_df["selection_source"] = "controller_confidence_overlay_proxy"
        controller_predictions_df = _enrich_ml_shadow_frame(controller_predictions_df, live_budget_config=live_budget_config)
    focus_selected_df = _select_focus_family_candidates(
        family_predictions_df,
        score_column="sidecar_probability",
        min_score=DEFAULT_FOCUS_RANK_THRESHOLD,
    ) if not family_predictions_df.empty else pd.DataFrame()
    controller_selected_df = _select_calibrated_controller_candidates(
        controller_predictions_df,
        controller_name=UNIFIED_CONTROLLER_NAME,
        score_column="sidecar_probability",
        min_score=DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD,
        focus_only=True,
    ) if not controller_predictions_df.empty else pd.DataFrame()
    combined_selected_df = _combine_sidecar_candidates(focus_selected_df, controller_selected_df)
    focus_selected_df = _enrich_ml_shadow_frame(focus_selected_df, live_budget_config=live_budget_config)
    controller_selected_df = _enrich_ml_shadow_frame(controller_selected_df, live_budget_config=live_budget_config)
    combined_selected_df = _enrich_ml_shadow_frame(combined_selected_df, live_budget_config=live_budget_config)

    ml_feature_df = pd.DataFrame()
    if not family_predictions_df.empty:
        ml_feature_df = family_predictions_df[
            ["signal_id", "rank_score", "calibrated_execution_likelihood", "calibrated_confidence"]
        ].rename(
            columns={
                "calibrated_execution_likelihood": "gate_score",
            }
        )
    return {
        "method": "heuristic_proxy_from_ml_lane_feature_logic",
        "notes": [
            "Uses current live-family signals plus regular-season context priors.",
            "No online calibrated model bundle is published for daily live validation yet, so sidecar_probability falls back to heuristic rank or raw controller confidence.",
        ],
        "required_live_fields": list(ML_SHADOW_REQUIRED_FIELDS),
        "thresholds": {
            "focus_family_min_score": DEFAULT_FOCUS_RANK_THRESHOLD,
            "controller_focus_min_score": DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD,
        },
        "family_candidates": family_predictions_df.to_dict(orient="records") if not family_predictions_df.empty else [],
        "controller_candidates": controller_predictions_df.to_dict(orient="records") if not controller_predictions_df.empty else [],
        "focus_family_selected": focus_selected_df.to_dict(orient="records") if not focus_selected_df.empty else [],
        "controller_focus_selected": controller_selected_df.to_dict(orient="records") if not controller_selected_df.empty else [],
        "sidecar_union_selected": combined_selected_df.to_dict(orient="records") if not combined_selected_df.empty else [],
        "ml_feature_df": ml_feature_df,
    }


def _build_llm_shadow(
    *,
    family_rows: pd.DataFrame,
    standard_frames: dict[str, pd.DataFrame],
    state_df: pd.DataFrame,
    subject_summary_df: pd.DataFrame,
    ml_feature_df: pd.DataFrame,
    snapshot_at: datetime,
) -> dict[str, Any]:
    signal_rows = family_rows[family_rows["signal_id"].notna()].copy()
    if signal_rows.empty:
        return {
            "method": "deterministic_shadow_from_llm_strategy_lane",
            "variants": [],
            "notes": ["No current family shadow signals were available for the LLM lane to rank."],
        }

    signal_summary_df = signal_rows[
        [
            "subject_name",
            "subject_type",
            "strategy_family",
            "game_id",
            "team_side",
            "entry_state_index",
            "exit_state_index",
            "signal_entry_at",
            "signal_exit_at",
            "signal_entry_price",
            "signal_exit_price",
            "executed_flag",
            "no_trade_reason",
            "signal_id",
        ]
    ].copy()
    signal_summary_df = signal_summary_df.rename(
        columns={
            "signal_entry_at": "signal_entry_at",
            "signal_exit_at": "signal_exit_at",
            "signal_entry_price": "signal_entry_price",
            "signal_exit_price": "signal_exit_price",
        }
    )
    attempt_trace_df = _build_attempt_trace(family_rows=signal_rows, snapshot_at=snapshot_at)
    replay_frames: dict[str, pd.DataFrame] = {
        "_shadow_empty_replay": pd.DataFrame(
            [
                {
                    "game_id": "0000000000",
                    "team_side": "",
                    "entry_state_index": 0,
                    "strategy_family": "",
                }
            ]
        )
    }
    llm_candidate_df = build_llm_candidate_dataset(
        signal_summary_df=signal_summary_df,
        attempt_trace_df=attempt_trace_df,
        standard_frames=standard_frames,
        replay_frames=replay_frames,
        state_panel_df=state_df,
        subject_summary_df=subject_summary_df,
        ml_feature_df=ml_feature_df,
    )
    if llm_candidate_df.empty:
        return {
            "method": "deterministic_shadow_from_llm_strategy_lane",
            "variants": [],
            "notes": ["LLM candidate dataset was empty after merging current shadow signals."],
        }
    clustered_df = _build_decision_clusters(llm_candidate_df, cluster_window_minutes=15)
    variants_payload: list[dict[str, Any]] = []
    variant_lookup = {variant.controller_id: variant for variant in LLM_CONTROLLER_VARIANTS}
    for controller_id in TARGET_LLM_VARIANTS:
        variant = variant_lookup.get(controller_id)
        if variant is None:
            continue
        try:
            _, decision_df, selected_records = _run_controller_variant(clustered_df, variant=variant)
        except UnboundLocalError:
            decision_df = pd.DataFrame()
            selected_records = []
        variants_payload.append(
            {
                "controller_id": controller_id,
                "selected_actions": selected_records,
                "decision_trace": decision_df.to_dict(orient="records") if not decision_df.empty else [],
            }
        )
    return {
        "method": "deterministic_shadow_from_llm_strategy_lane",
        "notes": [
            "Runs the lane's deterministic selector and template-compiler scoring over current family shadow signals.",
            "This remains a shadow-only compile or select layer; it does not generate or place live orders.",
        ],
        "variants": variants_payload,
    }


def main() -> None:
    args = parse_args()
    snapshot_at = _now_utc()
    session_date = str(args.session_date)
    game_ids = [str(game_id) for game_id in args.game_ids]
    api_root = args.api_root.rstrip("/")
    live_summary = _fetch_json(f"{api_root}/v1/nba/live/runs/{args.run_id}")
    run_root = Path(str(live_summary.get("run_root") or ""))
    live_budget_config = _resolve_live_budget_config(run_root)

    artifact_dir = (
        Path(args.artifact_dir)
        if args.artifact_dir
        else Path(r"C:\code-personal\janus-local\janus_cortex\shared\artifacts\daily-live-validation") / session_date
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)

    state_df, bundles, diagnostics_by_game, orderbooks_by_game = _build_current_state(game_ids=game_ids)
    family_rows_list, standard_frames = _build_family_signal_rows(
        state_df=state_df,
        bundles=bundles,
        orderbooks_by_game=orderbooks_by_game,
        snapshot_at=snapshot_at,
        game_ids=game_ids,
    )
    family_rows = pd.DataFrame(family_rows_list)
    controller_trace_by_game = _parse_controller_trace(run_root)
    controller_rows = pd.DataFrame(
        _build_controller_rows(
            live_summary=live_summary,
            state_df=state_df,
            orderbooks_by_game=orderbooks_by_game,
            controller_trace_by_game=controller_trace_by_game,
            snapshot_at=snapshot_at,
        )
    )
    subject_summary_df = _subject_summary_from_replay_submission(Path(r"C:\code-personal\janus-local\janus_cortex\shared"))
    ml_shadow = _build_ml_shadow(
        family_rows=family_rows,
        controller_rows=controller_rows,
        live_budget_config=live_budget_config,
    )
    if not args.allow_incomplete_ml_shadow:
        _validate_ml_shadow_payload(ml_shadow)
    ml_feature_df = ml_shadow.pop("ml_feature_df", pd.DataFrame())
    llm_shadow = _build_llm_shadow(
        family_rows=family_rows,
        standard_frames=standard_frames,
        state_df=state_df,
        subject_summary_df=subject_summary_df,
        ml_feature_df=ml_feature_df,
        snapshot_at=snapshot_at,
    )

    csv_rows = family_rows.copy()
    if not controller_rows.empty:
        controller_export = controller_rows.copy()
        controller_export["candidate_id"] = "ML-calibrated controller sidecar"
        controller_export["shadow_reason"] = controller_export["shadow_reason"].fillna("controller_snapshot")
        csv_rows = pd.concat([csv_rows, controller_export], ignore_index=True, sort=False)
    csv_path = artifact_dir / f"shadow_snapshot_{args.run_id}.csv"
    csv_rows.to_csv(csv_path, index=False)

    payload = {
        "snapshot_at_utc": snapshot_at.isoformat(),
        "session_date": session_date,
        "run_id": args.run_id,
        "api_root": api_root,
        "run_status": live_summary.get("status"),
        "run_root": str(run_root),
        "budget_config": live_budget_config,
        "game_ids": game_ids,
        "log_paths": live_summary.get("log_paths") or {},
        "diagnostics_by_game": diagnostics_by_game,
        "live_games": live_summary.get("games") or [],
        "family_shadow": family_rows.to_dict(orient="records") if not family_rows.empty else [],
        "ml_shadow": {
            key: (value.to_dict(orient="records") if isinstance(value, pd.DataFrame) else value)
            for key, value in ml_shadow.items()
        },
        "llm_shadow": llm_shadow,
        "artifacts": {
            "shadow_snapshot_json": str(artifact_dir / f"shadow_snapshot_{args.run_id}.json"),
            "shadow_snapshot_csv": str(csv_path),
        },
    }
    json_path = artifact_dir / f"shadow_snapshot_{args.run_id}.json"
    _write_json(json_path, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=True, default=str))


if __name__ == "__main__":
    main()
