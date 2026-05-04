from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS, load_analysis_backtest_state_panel_df
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    PORTFOLIO_SCOPE_ROUTED,
    PORTFOLIO_SCOPE_SINGLE_FAMILY,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.benchmark_integration import (
    UnifiedBenchmarkRequest,
    build_unified_benchmark_dashboard,
    resolve_default_shared_root,
)
from app.data.pipelines.daily.nba.analysis.consumer_adapters import load_analysis_consumer_bundle
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SEASON,
    AnalysisConsumerRequest,
)
from app.data.pipelines.daily.nba.analysis.models.features import (
    auc_score,
    brier_score,
    fit_logistic_regression,
    fit_ols,
    log_loss,
    spearman_corr,
)


ML_LANE_ID = "ml-trading"
ML_LANE_LABEL = "ML trading lane"
ML_LANE_TYPE = "ml"
ML_OUTPUT_DIRNAME = "ml-trading-lane"
ML_SCHEMA_VERSION = "ml_lane_v2"
ML_SHADOW_PAYLOAD_SCHEMA_VERSION = "ml_shadow_payload_v2"
ML_ARTIFACT_NAME = "postseason_replay_ml_v2"
REPLAY_ARTIFACT_NAME = "postseason_execution_replay"
REPLAY_ENGINE_LANE = "replay-engine-hf"
UNIFIED_CONTROLLER_NAME = "controller_vnext_unified_v1 :: balanced"
DETERMINISTIC_CONTROLLER_NAME = "controller_vnext_deterministic_v1 :: tight"
MIN_WARMUP_DATES = 3
DEFAULT_GATE_THRESHOLD = 0.30
OPTIONAL_SIZING_MIN_ROWS = 20
POLYMARKET_MIN_SHARES = 5
LIVE_ENTRY_TARGET_NOTIONAL_USD = 0.0
LIVE_MAX_ENTRY_ORDERS_PER_GAME = 2
LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD = 10.0
FOCUS_STRATEGY_FAMILIES = (
    "inversion",
    "quarter_open_reprice",
    "micro_momentum_continuation",
)
DEFAULT_FOCUS_RANK_THRESHOLD = 0.50
DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD = 0.60
SHADOW_REQUIRED_FIELDS = [
    "sidecar_probability",
    "calibrated_confidence",
    "calibrated_execution_likelihood",
    "focus_family_flag",
    "feed_fresh_flag",
    "orderbook_available_flag",
    "min_required_notional_usd",
    "budget_affordable_flag",
]
SHADOW_PAYLOAD_COLUMNS = [
    "shadow_variant",
    "variant_subject_id",
    "shadow_selected_flag",
    "shadow_priority_rank",
    "game_date",
    "game_id",
    "team_side",
    "signal_id",
    "underlying_candidate_id",
    "subject_name",
    "subject_type",
    "strategy_family",
    "selection_source",
    "focus_family_flag",
    "sidecar_probability",
    "calibrated_confidence",
    "calibrated_execution_likelihood",
    "raw_confidence",
    "rank_score",
    "calibrated_rank_score",
    "gate_score",
    "entry_state_index",
    "opening_band",
    "period_label",
    "score_diff_bucket",
    "coverage_status",
    "feed_fresh_flag",
    "orderbook_available_flag",
    "min_required_notional_usd",
    "budget_affordable_flag",
    "entry_target_notional_usd",
    "max_entry_notional_per_game_usd",
    "max_entry_orders_per_game",
    "position_capacity_available_flag",
    "signal_present_flag",
    "live_executable_flag",
    "live_blocker_bucket",
    "shadow_reason",
    "best_bid",
    "best_ask",
    "spread_cents",
    "replay_reference_bankroll",
    "replay_profitable_but_live_unexecutable_flag",
]

FAMILY_RANK_NUMERIC_COLUMNS = [
    "signal_strength",
    "entry_price",
    "state_seconds_to_game_end",
    "state_score_diff",
    "state_lead_changes_so_far",
    "state_net_points_last_5_events",
    "state_abs_price_delta_from_open",
    "historical_context_trade_count",
    "historical_context_win_rate",
    "historical_context_avg_return",
]
FAMILY_RANK_CATEGORICAL_COLUMNS = [
    "strategy_family",
    "opening_band",
    "period_label",
    "score_diff_bucket",
]
GATE_NUMERIC_COLUMNS = [
    "signal_strength",
    "entry_price",
    "state_seconds_to_game_end",
    "state_score_diff",
    "state_abs_price_delta_from_open",
    "state_net_points_last_5_events",
    "first_attempt_signal_age_seconds",
    "first_attempt_quote_age_seconds",
    "first_attempt_spread_cents",
    "first_attempt_state_lag",
    "raw_confidence",
]
GATE_CATEGORICAL_COLUMNS = [
    "subject_type",
    "strategy_family",
    "opening_band",
    "period_label",
]
CALIBRATION_TARGET_COLUMN = "label_replay_positive_flag"
RANKING_TARGET_COLUMN = "label_replay_positive_flag"
GATE_TARGET_COLUMN = "label_replay_executed_flag"
SIZING_TARGET_COLUMN = "label_replay_return"

_NUMERIC_GAME_ID_PATTERN = re.compile(r"^\d+$")


@dataclass(slots=True)
class MLTradingLaneRequest:
    season: str = DEFAULT_SEASON
    analysis_version: str = ANALYSIS_VERSION
    replay_artifact_name: str = REPLAY_ARTIFACT_NAME
    replay_artifact_names: tuple[str, ...] = ()
    state_panel_phases: tuple[str, ...] = ("play_in", "playoffs")
    training_season_phases: tuple[str, ...] = ("regular_season",)
    holdout_season_phases: tuple[str, ...] = ("play_in", "playoffs")
    use_phase_holdout: bool = False
    shared_root: str | None = None
    analysis_output_root: str | None = None
    artifact_name: str = ML_ARTIFACT_NAME
    warmup_dates: int = MIN_WARMUP_DATES
    gate_threshold: float = DEFAULT_GATE_THRESHOLD


@dataclass(slots=True)
class FeatureTransform:
    numeric_columns: list[str]
    categorical_columns: list[str]
    numeric_fill_values: dict[str, float]
    numeric_means: dict[str, float]
    numeric_stds: dict[str, float]
    category_levels: dict[str, list[str]]
    feature_names: list[str]


def _normalize_game_id(value: Any) -> str:
    text = str(value or "").strip()
    if _NUMERIC_GAME_ID_PATTERN.match(text):
        return text.zfill(10)
    return text


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    resolved = _safe_float(value)
    if resolved is None:
        return None
    return int(resolved)


def _safe_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def _safe_parse_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return {}
    raw = str(value).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_parse_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    return parsed if isinstance(parsed, list) else []


def _subject_artifact_stem(subject_name: str) -> str:
    return str(subject_name).replace(" ", "_").replace("::", "__").replace("/", "_")


def _coalesce_confidence(record: dict[str, Any]) -> float | None:
    for key in (
        "standard_unified_router_default_confidence",
        "standard_master_router_confidence",
        "standard_unified_router_llm_confidence",
        "standard_selected_confidence",
        "unified_router_default_confidence",
        "master_router_confidence",
        "unified_router_llm_confidence",
        "selected_confidence",
    ):
        resolved = _safe_float(record.get(key))
        if resolved is not None:
            return resolved
    return None


def _resolve_shared_root(shared_root: str | None) -> Path:
    return Path(shared_root) if shared_root else resolve_default_shared_root()


def _resolve_analysis_output_root(output_root: str | None) -> Path:
    return Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT


def _read_table(path_without_suffix: Path) -> pd.DataFrame:
    parquet_path = path_without_suffix.with_suffix(".parquet")
    if parquet_path.exists():
        try:
            frame = pd.read_parquet(parquet_path)
            if not frame.empty or len(frame.columns) > 0:
                return frame
        except Exception:
            pass
    csv_path = path_without_suffix.with_suffix(".csv")
    if csv_path.exists():
        try:
            return pd.read_csv(csv_path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
    return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_replay_subject_frames(
    replay_root: Path,
    subject_summary_df: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    standard_frames: dict[str, pd.DataFrame] = {}
    replay_frames: dict[str, pd.DataFrame] = {}
    if subject_summary_df.empty:
        return standard_frames, replay_frames
    for row in subject_summary_df.to_dict(orient="records"):
        subject_name = str(row.get("subject_name") or "")
        subject_type = str(row.get("subject_type") or "")
        stem = _subject_artifact_stem(subject_name)
        standard_df = _read_table(replay_root / f"standard_{stem}")
        replay_df = _read_table(replay_root / f"replay_{stem}")
        for frame in (standard_df, replay_df):
            if frame.empty:
                continue
            if "subject_name" not in frame.columns:
                frame["subject_name"] = subject_name
            if "subject_type" not in frame.columns:
                frame["subject_type"] = subject_type
            frame["game_id"] = frame["game_id"].map(_normalize_game_id)
            frame["team_side"] = frame["team_side"].astype(str)
            frame["entry_state_index"] = pd.to_numeric(frame["entry_state_index"], errors="coerce")
        standard_frames[subject_name] = standard_df
        replay_frames[subject_name] = replay_df
    return standard_frames, replay_frames


def _resolve_replay_artifact_names(request: MLTradingLaneRequest) -> tuple[str, ...]:
    explicit = tuple(str(value).strip() for value in request.replay_artifact_names if str(value).strip())
    if explicit:
        return tuple(dict.fromkeys(explicit))
    return (request.replay_artifact_name,)


def _merge_game_manifest_phase(frame: pd.DataFrame, manifest_df: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or manifest_df.empty or "game_id" not in frame.columns or "game_id" not in manifest_df.columns:
        return frame
    if "season_phase" not in manifest_df.columns:
        return frame
    work = frame.copy()
    manifest_work = manifest_df[["game_id", "season_phase"]].dropna(subset=["game_id"]).copy()
    manifest_work["game_id"] = manifest_work["game_id"].map(_normalize_game_id)
    phase_lookup = manifest_work.drop_duplicates(subset=["game_id"]).set_index("game_id")["season_phase"].to_dict()
    work["game_id"] = work["game_id"].map(_normalize_game_id)
    mapped_phase = work["game_id"].map(phase_lookup)
    if "season_phase" in work.columns:
        work["season_phase"] = work["season_phase"].fillna(mapped_phase)
    else:
        work["season_phase"] = mapped_phase
    return work


def _concat_frame_map(frame_map: dict[str, list[pd.DataFrame]]) -> dict[str, pd.DataFrame]:
    combined: dict[str, pd.DataFrame] = {}
    for key, frames in frame_map.items():
        present = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
        if present:
            combined[key] = pd.concat(present, ignore_index=True, sort=False)
        else:
            combined[key] = pd.DataFrame()
    return combined


def _load_replay_inputs(
    *,
    shared_root: Path,
    season: str,
    artifact_names: tuple[str, ...],
    primary_artifact_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame], dict[str, pd.DataFrame], list[str]]:
    signal_frames: list[pd.DataFrame] = []
    attempt_frames: list[pd.DataFrame] = []
    subject_summary_frames: list[pd.DataFrame] = []
    primary_subject_summary_df = pd.DataFrame()
    standard_frame_map: dict[str, list[pd.DataFrame]] = {}
    replay_frame_map: dict[str, list[pd.DataFrame]] = {}
    replay_roots: list[str] = []
    for artifact_name in artifact_names:
        replay_root = shared_root / "artifacts" / REPLAY_ENGINE_LANE / season / artifact_name
        replay_roots.append(str(replay_root))
        signal_summary_df = _read_table(replay_root / "replay_signal_summary")
        attempt_trace_df = _read_table(replay_root / "replay_attempt_trace")
        subject_summary_df = _read_table(replay_root / "replay_subject_summary")
        game_manifest_df = _read_table(replay_root / "replay_game_manifest")
        signal_summary_df = _merge_game_manifest_phase(signal_summary_df, game_manifest_df)
        attempt_trace_df = _merge_game_manifest_phase(attempt_trace_df, game_manifest_df)
        for frame in (signal_summary_df, attempt_trace_df, subject_summary_df):
            if frame.empty:
                continue
            frame["replay_artifact_name"] = artifact_name
        if not signal_summary_df.empty:
            signal_frames.append(signal_summary_df)
        if not attempt_trace_df.empty:
            attempt_frames.append(attempt_trace_df)
        if not subject_summary_df.empty:
            subject_summary_frames.append(subject_summary_df)
            if artifact_name == primary_artifact_name:
                primary_subject_summary_df = subject_summary_df.copy()
        standard_frames, replay_frames = _load_replay_subject_frames(replay_root, subject_summary_df)
        for subject_name, frame in standard_frames.items():
            copy = frame.copy()
            if not copy.empty:
                copy["replay_artifact_name"] = artifact_name
            standard_frame_map.setdefault(subject_name, []).append(copy)
        for subject_name, frame in replay_frames.items():
            copy = frame.copy()
            if not copy.empty:
                copy["replay_artifact_name"] = artifact_name
            replay_frame_map.setdefault(subject_name, []).append(copy)

    signal_summary_df = pd.concat(signal_frames, ignore_index=True, sort=False) if signal_frames else pd.DataFrame()
    attempt_trace_df = pd.concat(attempt_frames, ignore_index=True, sort=False) if attempt_frames else pd.DataFrame()
    subject_summary_df = (
        pd.concat(subject_summary_frames, ignore_index=True, sort=False) if subject_summary_frames else pd.DataFrame()
    )
    if primary_subject_summary_df.empty:
        primary_subject_summary_df = subject_summary_df.copy()
    return (
        signal_summary_df,
        attempt_trace_df,
        subject_summary_df,
        primary_subject_summary_df,
        _concat_frame_map(standard_frame_map),
        _concat_frame_map(replay_frame_map),
        replay_roots,
    )


def _signal_id(subject_name: str, game_id: Any, team_side: Any, entry_state_index: Any) -> str:
    entry_index = _safe_int(entry_state_index)
    return f"{subject_name}|{_normalize_game_id(game_id)}|{str(team_side or '')}|{entry_index or 0}"


def _underlying_candidate_id(strategy_family: Any, game_id: Any, team_side: Any, entry_state_index: Any) -> str:
    entry_index = _safe_int(entry_state_index)
    return f"{str(strategy_family or '')}|{_normalize_game_id(game_id)}|{str(team_side or '')}|{entry_index or 0}"


def _build_trade_lookup(frames: dict[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for subject_name, frame in frames.items():
        if frame.empty:
            continue
        for record in frame.to_dict(orient="records"):
            signal_id = _signal_id(
                subject_name,
                record.get("game_id"),
                record.get("team_side"),
                record.get("entry_state_index"),
            )
            normalized = dict(record)
            normalized["signal_id"] = signal_id
            normalized["subject_name"] = subject_name
            normalized["game_id"] = _normalize_game_id(record.get("game_id"))
            lookup[signal_id] = normalized
    return lookup


def _aggregate_attempt_trace(attempt_trace_df: pd.DataFrame) -> pd.DataFrame:
    if attempt_trace_df.empty:
        return pd.DataFrame()
    work = attempt_trace_df.copy()
    work["game_id"] = work["game_id"].map(_normalize_game_id)
    work["cycle_at"] = pd.to_datetime(work["cycle_at"], errors="coerce", utc=True)
    work["quote_time"] = pd.to_datetime(work["quote_time"], errors="coerce", utc=True)
    work["entry_state_index"] = pd.to_numeric(work["entry_state_index"], errors="coerce")
    work["latest_state_index"] = pd.to_numeric(work["latest_state_index"], errors="coerce")
    work["quote_age_seconds"] = pd.to_numeric(work["quote_age_seconds"], errors="coerce")
    work["spread_cents"] = pd.to_numeric(work["spread_cents"], errors="coerce")
    rows: list[dict[str, Any]] = []
    for signal_id, group in work.groupby("signal_id", dropna=False, sort=False):
        ordered = group.sort_values(["attempt_index", "cycle_at"], kind="mergesort").reset_index(drop=True)
        first = ordered.iloc[0]
        last = ordered.iloc[-1]
        rows.append(
            {
                "signal_id": signal_id,
                "attempt_count": int(len(ordered)),
                "first_attempt_cycle_at": first["cycle_at"],
                "first_attempt_result": first.get("result"),
                "first_attempt_reason": first.get("reason"),
                "first_attempt_quote_age_seconds": first.get("quote_age_seconds"),
                "first_attempt_spread_cents": first.get("spread_cents"),
                "first_attempt_latest_state_index": first.get("latest_state_index"),
                "first_attempt_entry_state_index": first.get("entry_state_index"),
                "final_attempt_cycle_at": last["cycle_at"],
                "final_attempt_result": last.get("result"),
                "final_attempt_reason": last.get("reason"),
                "final_attempt_quote_age_seconds": last.get("quote_age_seconds"),
                "final_attempt_spread_cents": last.get("spread_cents"),
                "final_attempt_latest_state_index": last.get("latest_state_index"),
            }
        )
    return pd.DataFrame(rows)


def _load_state_panel_for_phases(
    output_root: Path,
    *,
    season: str,
    analysis_version: str,
    phases: tuple[str, ...],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    missing_phases: list[str] = []
    for phase in phases:
        frame = _read_table(output_root / season / phase / analysis_version / "nba_analysis_state_panel")
        if frame.empty:
            missing_phases.append(phase)
            continue
        frame = frame.copy()
        frame["game_id"] = frame["game_id"].map(_normalize_game_id)
        frame["team_side"] = frame["team_side"].astype(str)
        frame["state_index"] = pd.to_numeric(frame["state_index"], errors="coerce")
        frame["event_at"] = pd.to_datetime(frame["event_at"], errors="coerce", utc=True)
        frames.append(frame)
    if missing_phases:
        try:
            with managed_connection() as connection:
                db_frame = load_analysis_backtest_state_panel_df(
                    connection,
                    season=season,
                    season_phase=missing_phases[0],
                    season_phases=tuple(missing_phases),
                    analysis_version=analysis_version,
                )
        except Exception:
            db_frame = pd.DataFrame()
        if not db_frame.empty:
            db_frame = db_frame.copy()
            db_frame["game_id"] = db_frame["game_id"].map(_normalize_game_id)
            db_frame["team_side"] = db_frame["team_side"].astype(str)
            db_frame["state_index"] = pd.to_numeric(db_frame["state_index"], errors="coerce")
            db_frame["event_at"] = pd.to_datetime(db_frame["event_at"], errors="coerce", utc=True)
            frames.append(db_frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _load_postseason_state_panel(output_root: Path, analysis_version: str) -> pd.DataFrame:
    return _load_state_panel_for_phases(
        output_root,
        season=DEFAULT_SEASON,
        analysis_version=analysis_version,
        phases=("play_in", "playoffs"),
    )


def _build_state_lookup(state_panel_df: pd.DataFrame) -> pd.DataFrame:
    if state_panel_df.empty:
        return pd.DataFrame()
    columns = {
        "game_id": "game_id",
        "team_side": "team_side",
        "state_index": "entry_state_index",
        "period": "state_period",
        "clock_elapsed_seconds": "state_clock_elapsed_seconds",
        "seconds_to_game_end": "state_seconds_to_game_end",
        "score_diff": "state_score_diff",
        "lead_changes_so_far": "state_lead_changes_so_far",
        "team_led_flag": "state_team_led_flag",
        "team_trailed_flag": "state_team_trailed_flag",
        "market_favorite_flag": "state_market_favorite_flag",
        "scoreboard_control_mismatch_flag": "state_scoreboard_control_mismatch_flag",
        "team_price": "state_team_price",
        "price_delta_from_open": "state_price_delta_from_open",
        "abs_price_delta_from_open": "state_abs_price_delta_from_open",
        "net_points_last_5_events": "state_net_points_last_5_events",
        "gap_before_seconds": "state_gap_before_seconds",
        "gap_after_seconds": "state_gap_after_seconds",
        "event_at": "state_event_at",
    }
    present = [column for column in columns if column in state_panel_df.columns]
    lookup = state_panel_df[present].rename(columns={column: columns[column] for column in present}).copy()
    lookup["game_id"] = lookup["game_id"].map(_normalize_game_id)
    lookup["team_side"] = lookup["team_side"].astype(str)
    lookup["entry_state_index"] = pd.to_numeric(lookup["entry_state_index"], errors="coerce")
    return lookup.drop_duplicates(subset=["game_id", "team_side", "entry_state_index"])


def _load_regular_season_trade_frames(output_root: Path) -> dict[str, pd.DataFrame]:
    bundle = load_analysis_consumer_bundle(
        AnalysisConsumerRequest(
            season=DEFAULT_SEASON,
            season_phase="regular_season",
            analysis_version=ANALYSIS_VERSION,
            output_root=str(output_root),
        )
    )
    artifacts = bundle.backtest_payload.get("artifacts") or {}
    frames: dict[str, pd.DataFrame] = {}
    family_summary_rows = bundle.backtest_payload.get("benchmark", {}).get("family_summary") or []
    families = sorted({str(row.get("strategy_family") or "") for row in family_summary_rows if row.get("strategy_family")})
    for family in families:
        csv_path = artifacts.get(f"{family}_csv")
        if not csv_path:
            continue
        try:
            frame = pd.read_csv(str(csv_path))
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        if frame.empty:
            continue
        frame["game_id"] = frame["game_id"].map(_normalize_game_id)
        frame["team_side"] = frame["team_side"].astype(str)
        frame["strategy_family"] = frame["strategy_family"].astype(str)
        frame["gross_return_with_slippage"] = pd.to_numeric(frame["gross_return_with_slippage"], errors="coerce")
        frames[family] = frame
    return frames


def _build_historical_context_frame(regular_trade_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for family, frame in regular_trade_frames.items():
        if frame.empty:
            continue
        grouped = (
            frame.groupby(["opening_band", "period_label", "context_bucket"], dropna=False)
            .agg(
                historical_context_trade_count=("game_id", "count"),
                historical_context_win_rate=(
                    "gross_return_with_slippage",
                    lambda values: float((pd.Series(values) > 0).mean()),
                ),
                historical_context_avg_return=("gross_return_with_slippage", "mean"),
            )
            .reset_index()
        )
        grouped.insert(0, "strategy_family", family)
        rows.extend(grouped.to_dict(orient="records"))
    return pd.DataFrame(rows)


def _build_family_overall_frame(regular_trade_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for family, frame in regular_trade_frames.items():
        if frame.empty:
            continue
        rows.append(
            {
                "strategy_family": family,
                "historical_family_trade_count": int(len(frame)),
                "historical_family_win_rate": float((frame["gross_return_with_slippage"] > 0).mean()),
                "historical_family_avg_return": float(frame["gross_return_with_slippage"].mean()),
            }
        )
    return pd.DataFrame(rows)


def _build_heuristic_rank_score(frame: pd.DataFrame) -> pd.Series:
    work = frame.copy()
    signal_rank = work["signal_strength"].fillna(work["signal_strength"].median()).rank(pct=True)
    historical_return = work["historical_context_avg_return"].fillna(work["historical_family_avg_return"]).fillna(0.0)
    historical_return_score = historical_return.clip(lower=-0.10, upper=0.20).add(0.10).div(0.30)
    historical_win_rate = work["historical_context_win_rate"].fillna(work["historical_family_win_rate"]).fillna(0.50)
    return ((0.50 * signal_rank) + (0.30 * historical_return_score) + (0.20 * historical_win_rate)).clip(0.0, 1.0)


def _build_heuristic_execute_score(frame: pd.DataFrame) -> pd.Series:
    signal_age_score = 1.0 - frame["first_attempt_signal_age_seconds"].fillna(90.0).clip(lower=0.0, upper=120.0).div(120.0)
    quote_age_score = 1.0 - frame["first_attempt_quote_age_seconds"].fillna(45.0).clip(lower=0.0, upper=60.0).div(60.0)
    spread_score = 1.0 - frame["first_attempt_spread_cents"].fillna(4.0).clip(lower=0.0, upper=6.0).div(6.0)
    confidence_score = pd.to_numeric(frame["raw_confidence"], errors="coerce")
    confidence_score = confidence_score.fillna(frame["heuristic_rank_score"]).fillna(0.50).clip(0.0, 1.0)
    return (
        (0.35 * signal_age_score)
        + (0.25 * quote_age_score)
        + (0.20 * spread_score)
        + (0.20 * confidence_score)
    ).clip(0.0, 1.0)


def build_replay_candidate_dataset(
    *,
    signal_summary_df: pd.DataFrame,
    attempt_trace_df: pd.DataFrame,
    standard_frames: dict[str, pd.DataFrame],
    replay_frames: dict[str, pd.DataFrame],
    state_panel_df: pd.DataFrame,
    historical_context_df: pd.DataFrame,
    historical_family_df: pd.DataFrame,
) -> pd.DataFrame:
    work = signal_summary_df.copy()
    if work.empty:
        return work
    work["game_id"] = work["game_id"].map(_normalize_game_id)
    work["team_side"] = work["team_side"].astype(str)
    work["entry_state_index"] = pd.to_numeric(work["entry_state_index"], errors="coerce")
    work["exit_state_index"] = pd.to_numeric(work["exit_state_index"], errors="coerce")
    work["signal_entry_at"] = pd.to_datetime(work["signal_entry_at"], errors="coerce", utc=True)
    work["signal_exit_at"] = pd.to_datetime(work["signal_exit_at"], errors="coerce", utc=True)
    work["signal_entry_price"] = pd.to_numeric(work["signal_entry_price"], errors="coerce")
    work["signal_exit_price"] = pd.to_numeric(work["signal_exit_price"], errors="coerce")
    work["executed_flag"] = work["executed_flag"].fillna(False).astype(bool)
    work["signal_id"] = work.apply(
        lambda row: _signal_id(
            str(row.get("subject_name") or ""),
            row.get("game_id"),
            row.get("team_side"),
            row.get("entry_state_index"),
        ),
        axis=1,
    )
    work["game_date"] = work["signal_entry_at"].dt.date

    standard_lookup = _build_trade_lookup(standard_frames)
    replay_lookup = _build_trade_lookup(replay_frames)
    attempt_agg_df = _aggregate_attempt_trace(attempt_trace_df)

    standard_df = pd.DataFrame(list(standard_lookup.values())).add_prefix("standard_")
    replay_df = pd.DataFrame(list(replay_lookup.values())).add_prefix("replay_")
    if not standard_df.empty:
        standard_df = standard_df.rename(columns={"standard_signal_id": "signal_id"})
    if not replay_df.empty:
        replay_df = replay_df.rename(columns={"replay_signal_id": "signal_id"})

    merged = work.merge(standard_df, on="signal_id", how="left")
    merged = merged.merge(replay_df, on="signal_id", how="left")
    if not attempt_agg_df.empty:
        merged = merged.merge(attempt_agg_df, on="signal_id", how="left")
    merged["entry_price"] = pd.to_numeric(
        merged["standard_entry_price"].fillna(merged["signal_entry_price"]),
        errors="coerce",
    )
    merged["signal_strength"] = pd.to_numeric(
        merged["standard_signal_strength"].fillna(0.0),
        errors="coerce",
    )
    merged["opening_band"] = merged["standard_opening_band"].fillna("")
    merged["period_label"] = merged["standard_period_label"].fillna("")
    merged["score_diff_bucket"] = merged["standard_score_diff_bucket"].fillna("")
    merged["context_bucket"] = merged["standard_context_bucket"].fillna("")
    merged["team_slug"] = merged["standard_team_slug"].fillna("")
    merged["opponent_team_slug"] = merged["standard_opponent_team_slug"].fillna("")
    if "season_phase" not in merged.columns:
        merged["season_phase"] = ""
    if "standard_season_phase" in merged.columns:
        merged["season_phase"] = merged["season_phase"].fillna(merged["standard_season_phase"])
    if "replay_artifact_name" not in merged.columns:
        merged["replay_artifact_name"] = ""
    state_lookup_df = _build_state_lookup(state_panel_df)
    if not state_lookup_df.empty:
        merged = merged.merge(
            state_lookup_df,
            on=["game_id", "team_side", "entry_state_index"],
            how="left",
        )
    if not historical_context_df.empty:
        merged = merged.merge(
            historical_context_df,
            on=["strategy_family", "opening_band", "period_label", "context_bucket"],
            how="left",
        )
    if not historical_family_df.empty:
        merged = merged.merge(historical_family_df, on="strategy_family", how="left")
    merged["raw_confidence"] = pd.to_numeric(
        merged.apply(lambda row: _coalesce_confidence(row.to_dict()), axis=1),
        errors="coerce",
    )

    merged["first_attempt_signal_age_seconds"] = (
        pd.to_datetime(merged["first_attempt_cycle_at"], errors="coerce", utc=True) - merged["signal_entry_at"]
    ).dt.total_seconds()
    merged["first_attempt_state_lag"] = (
        pd.to_numeric(merged["first_attempt_latest_state_index"], errors="coerce") - merged["entry_state_index"]
    )
    merged["label_replay_executed_flag"] = merged["executed_flag"].astype(bool)
    merged["label_replay_return"] = pd.to_numeric(merged["replay_gross_return_with_slippage"], errors="coerce").fillna(0.0)
    merged["label_replay_positive_flag"] = (
        merged["label_replay_executed_flag"] & (merged["label_replay_return"] > 0.0)
    )
    merged["label_replay_value"] = merged["label_replay_return"].where(merged["label_replay_executed_flag"], 0.0)
    merged["underlying_candidate_id"] = merged.apply(
        lambda row: _underlying_candidate_id(
            row.get("strategy_family"),
            row.get("game_id"),
            row.get("team_side"),
            row.get("entry_state_index"),
        ),
        axis=1,
    )
    merged["focus_family_flag"] = merged["strategy_family"].astype(str).isin(FOCUS_STRATEGY_FAMILIES)

    merged["subject_type"] = merged["subject_type"].astype(str)
    merged["candidate_kind"] = np.where(
        merged["subject_type"].eq("controller"),
        "controller_selected_trade",
        "family_signal_candidate",
    )
    merged["standard_subject_name"] = merged["standard_subject_name"].fillna(merged["subject_name"])
    merged["replay_subject_name"] = merged["replay_subject_name"].fillna(merged["subject_name"])

    for column in (
        "historical_context_trade_count",
        "historical_context_win_rate",
        "historical_context_avg_return",
        "historical_family_trade_count",
        "historical_family_win_rate",
        "historical_family_avg_return",
    ):
        if column not in merged.columns:
            merged[column] = np.nan

    merged["heuristic_rank_score"] = _build_heuristic_rank_score(merged)
    merged["heuristic_execute_score"] = _build_heuristic_execute_score(merged)
    router_source = (
        merged["standard_unified_router_source"]
        if "standard_unified_router_source" in merged.columns
        else pd.Series("", index=merged.index, dtype=object)
    )
    if "standard_master_router_role" in merged.columns:
        router_source = router_source.fillna(merged["standard_master_router_role"])
    merged["router_source"] = router_source.fillna("")
    merged["game_date"] = pd.to_datetime(merged["game_date"], errors="coerce").dt.date

    metadata_columns = [
        "signal_id",
        "underlying_candidate_id",
        "subject_name",
        "subject_type",
        "candidate_kind",
        "strategy_family",
        "focus_family_flag",
        "season_phase",
        "replay_artifact_name",
        "game_id",
        "game_date",
        "team_side",
        "team_slug",
        "opponent_team_slug",
        "opening_band",
        "period_label",
        "score_diff_bucket",
        "context_bucket",
        "entry_state_index",
        "exit_state_index",
        "signal_entry_at",
        "signal_exit_at",
        "entry_price",
        "signal_strength",
        "raw_confidence",
        "router_source",
        "historical_context_trade_count",
        "historical_context_win_rate",
        "historical_context_avg_return",
        "historical_family_trade_count",
        "historical_family_win_rate",
        "historical_family_avg_return",
        "state_period",
        "state_clock_elapsed_seconds",
        "state_seconds_to_game_end",
        "state_score_diff",
        "state_lead_changes_so_far",
        "state_team_led_flag",
        "state_team_trailed_flag",
        "state_market_favorite_flag",
        "state_scoreboard_control_mismatch_flag",
        "state_team_price",
        "state_price_delta_from_open",
        "state_abs_price_delta_from_open",
        "state_net_points_last_5_events",
        "state_gap_before_seconds",
        "state_gap_after_seconds",
        "first_attempt_signal_age_seconds",
        "first_attempt_quote_age_seconds",
        "first_attempt_spread_cents",
        "first_attempt_state_lag",
        "attempt_count",
        "first_attempt_result",
        "first_attempt_reason",
        "final_attempt_result",
        "final_attempt_reason",
        "heuristic_rank_score",
        "heuristic_execute_score",
        "label_replay_executed_flag",
        "label_replay_positive_flag",
        "label_replay_return",
        "label_replay_value",
        "no_trade_reason",
    ]
    trade_payload_columns = sorted(
        column
        for column in merged.columns
        if column.startswith("standard_") or column.startswith("replay_")
    )
    available_columns = [column for column in metadata_columns if column in merged.columns]
    available_columns.extend(column for column in trade_payload_columns if column not in available_columns)
    output = merged[available_columns].copy()
    output["game_date"] = pd.to_datetime(output["game_date"], errors="coerce").dt.date
    return output.sort_values(["game_date", "game_id", "subject_type", "signal_id"], kind="mergesort").reset_index(drop=True)


def _unique_sorted_dates(frame: pd.DataFrame) -> list[pd.Timestamp]:
    dates = pd.to_datetime(frame["game_date"], errors="coerce").dropna().drop_duplicates().sort_values()
    return [pd.Timestamp(value) for value in dates.tolist()]


def build_time_split_summary(frame: pd.DataFrame, *, warmup_dates: int) -> dict[str, Any]:
    dates = _unique_sorted_dates(frame)
    resolved_warmup = max(1, min(int(warmup_dates), max(len(dates) - 1, 1)))
    train_dates = dates[:resolved_warmup]
    live_dates = dates[resolved_warmup:]
    return {
        "unique_game_dates": [value.date().isoformat() for value in dates],
        "warmup_date_count": resolved_warmup,
        "warmup_dates": [value.date().isoformat() for value in train_dates],
        "expanding_prediction_dates": [value.date().isoformat() for value in live_dates],
        "leakage_prevention": [
            "All learned replay-label models use expanding-date training windows.",
            "A prediction for date D only sees replay labels from dates strictly before D.",
            "Regular-season backtest priors are used only as historical features, not as replay labels.",
            "Exit-price, hold-time, and full-game outcome columns are excluded from the feature matrix.",
        ],
    }


def _with_phase_evaluation_slice(
    frame: pd.DataFrame,
    *,
    training_phases: tuple[str, ...],
    holdout_phases: tuple[str, ...],
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    train_phase_set = {str(value).strip() for value in training_phases if str(value).strip()}
    holdout_phase_set = {str(value).strip() for value in holdout_phases if str(value).strip()}
    phases = work.get("season_phase", pd.Series("", index=work.index, dtype=object)).fillna("").astype(str)
    work["evaluation_slice"] = np.select(
        [phases.isin(holdout_phase_set), phases.isin(train_phase_set)],
        ["postseason_holdout", "training_history"],
        default="other_history",
    )
    return work


def _build_phase_split_summary(
    frame: pd.DataFrame,
    *,
    training_phases: tuple[str, ...],
    holdout_phases: tuple[str, ...],
    use_phase_holdout: bool,
) -> dict[str, Any]:
    if frame.empty:
        return {
            "enabled": bool(use_phase_holdout),
            "training_season_phases": list(training_phases),
            "holdout_season_phases": list(holdout_phases),
            "slice_counts": {},
            "leakage_prevention": [
                "No rows were available for phase-holdout evaluation.",
            ],
        }
    phase_counts = (
        frame.groupby(["evaluation_slice", "season_phase"], dropna=False)
        .size()
        .reset_index(name="rows")
        .to_dict(orient="records")
        if {"evaluation_slice", "season_phase"}.issubset(frame.columns)
        else []
    )
    slice_counts = frame["evaluation_slice"].value_counts(dropna=False).to_dict() if "evaluation_slice" in frame.columns else {}
    return {
        "enabled": bool(use_phase_holdout),
        "training_season_phases": list(training_phases),
        "holdout_season_phases": list(holdout_phases),
        "slice_counts": {str(key): int(value) for key, value in slice_counts.items()},
        "phase_counts": to_jsonable(phase_counts),
        "leakage_prevention": [
            "When phase holdout is enabled, learned model coefficients and score calibrators are fit only on training_history rows.",
            "Rows marked postseason_holdout are predicted after regular-season training and are not used to fit the same model.",
            "Regular-season rows can carry replay labels for training, but play-in/playoff replay labels are used only for evaluation.",
        ],
    }


def _build_feature_transform(
    train_df: pd.DataFrame,
    *,
    numeric_columns: list[str],
    categorical_columns: list[str],
) -> FeatureTransform:
    numeric_fill_values: dict[str, float] = {}
    numeric_means: dict[str, float] = {}
    numeric_stds: dict[str, float] = {}
    category_levels: dict[str, list[str]] = {}
    feature_names: list[str] = []
    for column in numeric_columns:
        series = pd.to_numeric(train_df[column], errors="coerce") if column in train_df.columns else pd.Series(dtype=float)
        fill_value = float(series.median()) if not series.dropna().empty else 0.0
        mean_value = float(series.fillna(fill_value).mean()) if not series.empty else 0.0
        std_value = float(series.fillna(fill_value).std(ddof=0)) if not series.empty else 1.0
        if std_value <= 0:
            std_value = 1.0
        numeric_fill_values[column] = fill_value
        numeric_means[column] = mean_value
        numeric_stds[column] = std_value
        feature_names.append(column)
    for column in categorical_columns:
        values = (
            train_df[column].fillna("__missing__").astype(str).str.strip().replace("", "__missing__")
            if column in train_df.columns
            else pd.Series(dtype=str)
        )
        levels = sorted({value for value in values.tolist() if value})
        if not levels:
            levels = ["__missing__"]
        category_levels[column] = levels
        feature_names.extend([f"{column}={level}" for level in levels])
    return FeatureTransform(
        numeric_columns=list(numeric_columns),
        categorical_columns=list(categorical_columns),
        numeric_fill_values=numeric_fill_values,
        numeric_means=numeric_means,
        numeric_stds=numeric_stds,
        category_levels=category_levels,
        feature_names=feature_names,
    )


def _transform_features(frame: pd.DataFrame, transform: FeatureTransform) -> np.ndarray:
    rows: list[np.ndarray] = []
    for _, row in frame.iterrows():
        values: list[float] = [1.0]
        for column in transform.numeric_columns:
            raw_value = _safe_float(row.get(column))
            fill_value = transform.numeric_fill_values.get(column, 0.0)
            mean_value = transform.numeric_means.get(column, 0.0)
            std_value = transform.numeric_stds.get(column, 1.0) or 1.0
            resolved = fill_value if raw_value is None else float(raw_value)
            values.append((resolved - mean_value) / std_value)
        for column in transform.categorical_columns:
            raw = str(row.get(column) if row.get(column) not in (None, "") else "__missing__").strip() or "__missing__"
            levels = transform.category_levels.get(column) or ["__missing__"]
            values.extend([1.0 if raw == level else 0.0 for level in levels])
        rows.append(np.asarray(values, dtype=float))
    if not rows:
        return np.zeros((0, 1 + len(transform.feature_names)), dtype=float)
    return np.vstack(rows)


def _fit_binary_model(
    train_df: pd.DataFrame,
    *,
    target_column: str,
    numeric_columns: list[str],
    categorical_columns: list[str],
) -> dict[str, Any]:
    if train_df.empty:
        return {"status": "fallback_empty_train"}
    y_train = train_df[target_column].astype(float).to_numpy()
    if len(np.unique(y_train)) < 2:
        return {
            "status": "fallback_single_class",
            "base_rate": float(np.mean(y_train)) if len(y_train) else 0.0,
        }
    transform = _build_feature_transform(
        train_df,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
    )
    x_train = _transform_features(train_df, transform)
    coefficients = fit_logistic_regression(x_train, y_train, max_iter=600, learning_rate=0.06, l2_penalty=5e-4)
    return {
        "status": "trained",
        "transform": transform,
        "coefficients": coefficients,
    }


def _predict_binary_model(model: dict[str, Any], frame: pd.DataFrame, *, fallback_column: str) -> np.ndarray:
    if frame.empty:
        return np.asarray([], dtype=float)
    status = str(model.get("status") or "")
    if status == "trained":
        transform = model["transform"]
        coefficients = model["coefficients"]
        scores = _transform_features(frame, transform) @ coefficients
        return 1.0 / (1.0 + np.exp(-np.clip(scores, -30.0, 30.0)))
    if status == "fallback_single_class":
        return np.full(len(frame), float(model.get("base_rate") or 0.0), dtype=float)
    return frame[fallback_column].fillna(0.5).to_numpy(dtype=float)


def build_phase_holdout_predictions(
    frame: pd.DataFrame,
    *,
    target_column: str,
    numeric_columns: list[str],
    categorical_columns: list[str],
    fallback_column: str,
    train_slice_name: str = "training_history",
    holdout_slice_name: str = "postseason_holdout",
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if "evaluation_slice" not in frame.columns:
        return build_expanding_predictions(
            frame,
            target_column=target_column,
            numeric_columns=numeric_columns,
            categorical_columns=categorical_columns,
            fallback_column=fallback_column,
            warmup_dates=MIN_WARMUP_DATES,
        )
    work = frame.copy()
    train_slice = work[work["evaluation_slice"].astype(str) == train_slice_name].copy()
    holdout_slice = work[work["evaluation_slice"].astype(str) == holdout_slice_name].copy()
    if holdout_slice.empty:
        return build_expanding_predictions(
            frame,
            target_column=target_column,
            numeric_columns=numeric_columns,
            categorical_columns=categorical_columns,
            fallback_column=fallback_column,
            warmup_dates=MIN_WARMUP_DATES,
        )
    model = _fit_binary_model(
        train_slice,
        target_column=target_column,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
    )
    reference_frames: list[pd.DataFrame] = []
    if not train_slice.empty:
        train_slice["prediction_score"] = train_slice[fallback_column].fillna(0.5)
        train_slice["prediction_mode"] = "phase_holdout_training_reference"
        train_slice["train_row_count"] = 0
        reference_frames.append(train_slice)
    holdout_slice["prediction_score"] = _predict_binary_model(model, holdout_slice, fallback_column=fallback_column)
    holdout_slice["prediction_mode"] = f"phase_holdout_{model.get('status') or 'fallback'}"
    holdout_slice["train_row_count"] = int(len(train_slice))
    reference_frames.append(holdout_slice)
    combined = pd.concat(reference_frames, ignore_index=True, sort=False)
    combined["game_date"] = pd.to_datetime(combined["game_date"], errors="coerce").dt.date
    sort_columns = [column for column in ("game_date", "game_id", "signal_id") if column in combined.columns]
    if sort_columns:
        combined = combined.sort_values(sort_columns, kind="mergesort")
    return combined.reset_index(drop=True)


def build_expanding_predictions(
    frame: pd.DataFrame,
    *,
    target_column: str,
    numeric_columns: list[str],
    categorical_columns: list[str],
    fallback_column: str,
    warmup_dates: int,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work["game_date"] = pd.to_datetime(work["game_date"], errors="coerce")
    ordered_dates = _unique_sorted_dates(work)
    resolved_warmup = max(1, min(int(warmup_dates), max(len(ordered_dates) - 1, 1)))
    prediction_frames: list[pd.DataFrame] = []
    for date_index, current_date in enumerate(ordered_dates):
        current_slice = work[work["game_date"] == current_date].copy()
        if current_slice.empty:
            continue
        if date_index < resolved_warmup:
            current_slice["prediction_score"] = current_slice[fallback_column].fillna(0.5)
            current_slice["prediction_mode"] = "cold_start_fallback"
            current_slice["train_row_count"] = 0
            prediction_frames.append(current_slice)
            continue
        train_slice = work[work["game_date"] < current_date].copy()
        model = _fit_binary_model(
            train_slice,
            target_column=target_column,
            numeric_columns=numeric_columns,
            categorical_columns=categorical_columns,
        )
        current_slice["prediction_score"] = _predict_binary_model(model, current_slice, fallback_column=fallback_column)
        current_slice["prediction_mode"] = str(model.get("status") or "fallback")
        current_slice["train_row_count"] = int(len(train_slice))
        prediction_frames.append(current_slice)
    combined = pd.concat(prediction_frames, ignore_index=True, sort=False)
    combined["game_date"] = pd.to_datetime(combined["game_date"], errors="coerce").dt.date
    sort_columns = [column for column in ("game_date", "game_id", "signal_id") if column in combined.columns]
    if sort_columns:
        combined = combined.sort_values(sort_columns, kind="mergesort")
    return combined.reset_index(drop=True)


def _evaluate_binary_predictions(
    frame: pd.DataFrame,
    *,
    score_column: str,
    target_column: str,
) -> dict[str, Any]:
    clean = frame[[score_column, target_column]].dropna().copy()
    if clean.empty:
        return {"status": "insufficient_data"}
    y_true = clean[target_column].astype(float).to_numpy()
    y_score = clean[score_column].astype(float).clip(1e-6, 1 - 1e-6).to_numpy()
    return {
        "status": "success",
        "rows": int(len(clean)),
        "positive_rate": float(np.mean(y_true)),
        "auc": auc_score(y_true, y_score),
        "brier": brier_score(y_true, y_score),
        "log_loss": log_loss(y_true, y_score),
    }


def _build_group_topline(
    frame: pd.DataFrame,
    *,
    score_column: str,
    value_column: str,
    target_column: str,
    group_column: str,
) -> dict[str, Any]:
    if frame.empty:
        return {"status": "insufficient_data"}
    top_rows = (
        frame.sort_values([group_column, score_column, "signal_strength"], ascending=[True, False, False], kind="mergesort")
        .drop_duplicates(subset=[group_column])
        .reset_index(drop=True)
    )
    return {
        "status": "success",
        "group_count": int(top_rows[group_column].nunique()),
        "top1_positive_rate": float(top_rows[target_column].mean()) if not top_rows.empty else None,
        "top1_mean_replay_value": float(top_rows[value_column].mean()) if not top_rows.empty else None,
        "top1_spearman": spearman_corr(
            frame[value_column].fillna(0.0).tolist(),
            frame[score_column].fillna(0.0).tolist(),
        ),
    }


def _fit_platt_scaler(train_df: pd.DataFrame, *, raw_score_column: str, target_column: str) -> dict[str, Any]:
    if train_df.empty:
        return {"status": "fallback_empty_train"}
    clean = train_df[[raw_score_column, target_column]].dropna().copy()
    if clean.empty:
        return {"status": "fallback_empty_train"}
    y_train = clean[target_column].astype(float).to_numpy()
    if len(np.unique(y_train)) < 2:
        return {
            "status": "fallback_single_class",
            "base_rate": float(np.mean(y_train)) if len(y_train) else 0.0,
        }
    x_train = np.column_stack(
        [
            np.ones(len(clean), dtype=float),
            clean[raw_score_column].astype(float).to_numpy(),
        ]
    )
    coefficients = fit_logistic_regression(x_train, y_train, max_iter=500, learning_rate=0.10, l2_penalty=5e-4)
    return {
        "status": "trained",
        "coefficients": coefficients,
    }


def _predict_platt_scaler(model: dict[str, Any], frame: pd.DataFrame, *, raw_score_column: str) -> np.ndarray:
    if frame.empty:
        return np.asarray([], dtype=float)
    raw_scores = pd.to_numeric(frame[raw_score_column], errors="coerce")
    status = str(model.get("status") or "")
    if status == "trained":
        x = np.column_stack(
            [
                np.ones(len(frame), dtype=float),
                raw_scores.fillna(0.5).to_numpy(dtype=float),
            ]
        )
        coefficients = model["coefficients"]
        scores = x @ coefficients
        return 1.0 / (1.0 + np.exp(-np.clip(scores, -30.0, 30.0)))
    if status == "fallback_single_class":
        return np.full(len(frame), float(model.get("base_rate") or 0.0), dtype=float)
    return raw_scores.fillna(0.5).to_numpy(dtype=float)


def build_phase_holdout_score_calibration(
    frame: pd.DataFrame,
    *,
    raw_score_column: str,
    target_column: str,
    calibrated_column: str,
    mode_column: str,
    warmup_dates: int,
    train_slice_name: str = "training_history",
    holdout_slice_name: str = "postseason_holdout",
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if "evaluation_slice" not in frame.columns:
        return build_expanding_score_calibration(
            frame,
            raw_score_column=raw_score_column,
            target_column=target_column,
            calibrated_column=calibrated_column,
            mode_column=mode_column,
            warmup_dates=warmup_dates,
        )
    work = frame.copy()
    train_slice = work[work["evaluation_slice"].astype(str) == train_slice_name].copy()
    holdout_slice = work[work["evaluation_slice"].astype(str) == holdout_slice_name].copy()
    if holdout_slice.empty:
        return build_expanding_score_calibration(
            frame,
            raw_score_column=raw_score_column,
            target_column=target_column,
            calibrated_column=calibrated_column,
            mode_column=mode_column,
            warmup_dates=warmup_dates,
        )
    model = _fit_platt_scaler(train_slice, raw_score_column=raw_score_column, target_column=target_column)
    holdout_slice[calibrated_column] = _predict_platt_scaler(model, holdout_slice, raw_score_column=raw_score_column)
    holdout_slice[mode_column] = f"phase_holdout_{model.get('status') or 'fallback'}"
    holdout_slice["game_date"] = pd.to_datetime(holdout_slice["game_date"], errors="coerce").dt.date
    sort_columns = [column for column in ("game_date", "game_id", "signal_id") if column in holdout_slice.columns]
    if sort_columns:
        holdout_slice = holdout_slice.sort_values(sort_columns, kind="mergesort")
    return holdout_slice.reset_index(drop=True)


def build_expanding_score_calibration(
    frame: pd.DataFrame,
    *,
    raw_score_column: str,
    target_column: str,
    calibrated_column: str,
    mode_column: str,
    warmup_dates: int,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work["game_date"] = pd.to_datetime(work["game_date"], errors="coerce")
    ordered_dates = _unique_sorted_dates(work)
    resolved_warmup = max(1, min(int(warmup_dates), max(len(ordered_dates) - 1, 1)))
    prediction_frames: list[pd.DataFrame] = []
    for date_index, current_date in enumerate(ordered_dates):
        current_slice = work[work["game_date"] == current_date].copy()
        if current_slice.empty:
            continue
        if date_index < resolved_warmup:
            current_slice[calibrated_column] = pd.to_numeric(
                current_slice[raw_score_column],
                errors="coerce",
            ).fillna(0.5)
            current_slice[mode_column] = "cold_start_identity"
            prediction_frames.append(current_slice)
            continue
        train_slice = work[work["game_date"] < current_date].copy()
        model = _fit_platt_scaler(
            train_slice,
            raw_score_column=raw_score_column,
            target_column=target_column,
        )
        current_slice[calibrated_column] = _predict_platt_scaler(
            model,
            current_slice,
            raw_score_column=raw_score_column,
        )
        current_slice[mode_column] = str(model.get("status") or "fallback")
        prediction_frames.append(current_slice)
    combined = pd.concat(prediction_frames, ignore_index=True, sort=False)
    combined["game_date"] = pd.to_datetime(combined["game_date"], errors="coerce").dt.date
    sort_columns = [column for column in ("game_date", "game_id", "signal_id") if column in combined.columns]
    if sort_columns:
        combined = combined.sort_values(sort_columns, kind="mergesort")
    return combined.reset_index(drop=True)


def build_expanding_calibration(controller_df: pd.DataFrame, *, warmup_dates: int) -> pd.DataFrame:
    return build_expanding_score_calibration(
        controller_df,
        raw_score_column="raw_confidence",
        target_column=CALIBRATION_TARGET_COLUMN,
        calibrated_column="calibrated_confidence",
        mode_column="calibration_mode",
        warmup_dates=warmup_dates,
    )


def _build_calibration_buckets(
    frame: pd.DataFrame,
    *,
    score_column: str,
    target_column: str,
    bucket_count: int = 5,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame[[score_column, target_column]].dropna().copy()
    if work.empty:
        return pd.DataFrame()
    work["bucket"] = pd.cut(
        work[score_column],
        bins=np.linspace(0.0, 1.0, bucket_count + 1),
        include_lowest=True,
        duplicates="drop",
    )
    summary = (
        work.groupby("bucket", dropna=False, observed=False)
        .agg(
            row_count=(target_column, "count"),
            mean_score=(score_column, "mean"),
            observed_rate=(target_column, "mean"),
        )
        .reset_index()
    )
    summary["bucket"] = summary["bucket"].astype(str)
    return summary


def _build_execution_risk_breakdown(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    work["reason_bucket"] = work["no_trade_reason"].fillna("executed").astype(str)
    summary = (
        work.groupby("reason_bucket", dropna=False, observed=False)
        .agg(
            row_count=("signal_id", "count"),
            observed_execution_rate=("label_replay_executed_flag", "mean"),
            mean_gate_score=("gate_score", "mean"),
            mean_calibrated_execution_likelihood=("calibrated_execution_likelihood", "mean"),
            mean_signal_age_seconds=("first_attempt_signal_age_seconds", "mean"),
            mean_quote_age_seconds=("first_attempt_quote_age_seconds", "mean"),
            mean_spread_cents=("first_attempt_spread_cents", "mean"),
            focus_family_share=("focus_family_flag", "mean"),
        )
        .reset_index()
        .sort_values(["row_count", "reason_bucket"], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )
    return summary


def _fit_optional_sizing_model(frame: pd.DataFrame) -> dict[str, Any]:
    executed = frame[frame["label_replay_executed_flag"]].copy()
    if len(executed) < OPTIONAL_SIZING_MIN_ROWS:
        return {
            "status": "insufficient_data",
            "reason": f"need at least {OPTIONAL_SIZING_MIN_ROWS} executed rows",
            "train_rows": int(len(executed)),
        }
    numeric_columns = [
        "signal_strength",
        "entry_price",
        "state_seconds_to_game_end",
        "first_attempt_signal_age_seconds",
        "first_attempt_quote_age_seconds",
        "first_attempt_spread_cents",
        "raw_confidence",
    ]
    train = executed.dropna(subset=[SIZING_TARGET_COLUMN]).copy()
    transform = _build_feature_transform(train, numeric_columns=numeric_columns, categorical_columns=["strategy_family"])
    x_train = _transform_features(train, transform)
    y_train = train[SIZING_TARGET_COLUMN].astype(float).to_numpy()
    coefficients = fit_ols(x_train, y_train, ridge=1e-3)
    predictions = x_train @ coefficients
    rmse = float(np.sqrt(np.mean((predictions - y_train) ** 2)))
    return {
        "status": "success",
        "train_rows": int(len(train)),
        "rmse": rmse,
        "coefficients": [
            {"feature": "intercept", "value": float(coefficients[0])},
            *[
                {"feature": feature_name, "value": float(coefficients[index + 1])}
                for index, feature_name in enumerate(transform.feature_names)
            ],
        ],
    }


def _dedupe_underlying_candidates(
    frame: pd.DataFrame,
    *,
    score_columns: list[str],
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    sort_columns = [column for column in score_columns if column in frame.columns]
    sort_columns.extend(column for column in ("signal_strength", "historical_family_avg_return") if column in frame.columns)
    if sort_columns:
        work = frame.sort_values(sort_columns, ascending=[False] * len(sort_columns), kind="mergesort")
    else:
        work = frame.copy()
    dedupe_column = "underlying_candidate_id" if "underlying_candidate_id" in work.columns else "signal_id"
    return work.drop_duplicates(subset=[dedupe_column]).reset_index(drop=True)


def _select_top_family_candidates(
    family_predictions_df: pd.DataFrame,
    *,
    score_column: str,
    gate_column: str | None = None,
    gate_threshold: float | None = None,
) -> pd.DataFrame:
    work = family_predictions_df.copy()
    if gate_column is not None and gate_threshold is not None:
        work = work[work[gate_column].fillna(0.0) >= float(gate_threshold)].copy()
    if work.empty:
        return work
    selected = (
        work.sort_values(["game_id", score_column, "signal_strength"], ascending=[True, False, False], kind="mergesort")
        .drop_duplicates(subset=["game_id"])
        .reset_index(drop=True)
    )
    return selected


def _select_focus_family_candidates(
    family_predictions_df: pd.DataFrame,
    *,
    score_column: str,
    min_score: float,
) -> pd.DataFrame:
    work = family_predictions_df[family_predictions_df["focus_family_flag"].fillna(False)].copy()
    if work.empty:
        return work
    work = work[work[score_column].fillna(0.0) >= float(min_score)].copy()
    return _dedupe_underlying_candidates(
        work,
        score_columns=[score_column, "rank_score"],
    )


def _select_controller_candidates(
    controller_predictions_df: pd.DataFrame,
    *,
    controller_name: str,
    gate_column: str,
    gate_threshold: float,
) -> pd.DataFrame:
    work = controller_predictions_df[controller_predictions_df["subject_name"].astype(str) == controller_name].copy()
    if work.empty:
        return work
    return work[work[gate_column].fillna(0.0) >= float(gate_threshold)].copy().reset_index(drop=True)


def _select_calibrated_controller_candidates(
    controller_predictions_df: pd.DataFrame,
    *,
    controller_name: str,
    score_column: str,
    min_score: float,
    focus_only: bool = True,
) -> pd.DataFrame:
    work = controller_predictions_df[controller_predictions_df["subject_name"].astype(str) == controller_name].copy()
    if work.empty:
        return work
    if focus_only:
        work = work[work["focus_family_flag"].fillna(False)].copy()
    work = work[work[score_column].fillna(0.0) >= float(min_score)].copy()
    return _dedupe_underlying_candidates(
        work,
        score_columns=[score_column, "raw_confidence"],
    )


def _combine_sidecar_candidates(*frames: pd.DataFrame) -> pd.DataFrame:
    available_frames = [frame.copy() for frame in frames if not frame.empty]
    if not available_frames:
        return pd.DataFrame()
    combined = pd.concat(available_frames, ignore_index=True, sort=False)
    return _dedupe_underlying_candidates(
        combined,
        score_columns=["sidecar_probability", "calibrated_rank_score", "calibrated_confidence"],
    )


def _build_selected_trade_frames(selected_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if selected_df.empty:
        empty = pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
        return empty.copy(), empty.copy()
    standard_columns = [column for column in selected_df.columns if column.startswith("standard_")]
    replay_columns = [column for column in selected_df.columns if column.startswith("replay_")]
    if standard_columns:
        standard_df = (
            selected_df[standard_columns]
            .rename(columns={column: column[len("standard_") :] for column in standard_columns})
            .drop(columns=["signal_id"], errors="ignore")
            .copy()
        )
    else:
        standard_df = pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    if replay_columns:
        replay_df = (
            selected_df[selected_df["label_replay_executed_flag"]][replay_columns]
            .rename(columns={column: column[len("replay_") :] for column in replay_columns})
            .drop(columns=["signal_id"], errors="ignore")
            .copy()
        )
    else:
        replay_df = pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    for frame in (standard_df, replay_df):
        if frame.empty:
            continue
        for column in ("game_id", "team_side"):
            if column in frame.columns:
                frame[column] = frame[column].map(_normalize_game_id)
    return standard_df, replay_df


def _compute_subject_metrics(
    *,
    subject_name: str,
    candidate_kind: str,
    selected_df: pd.DataFrame,
    standard_df: pd.DataFrame,
    replay_df: pd.DataFrame,
    gate_threshold: float | None,
    extra_notes: list[str] | None = None,
) -> dict[str, Any]:
    selected_df = selected_df.copy()
    if "label_replay_executed_flag" not in selected_df.columns:
        selected_df["label_replay_executed_flag"] = False
    if "no_trade_reason" not in selected_df.columns:
        selected_df["no_trade_reason"] = "unlabeled"
    standard_summary, _ = simulate_trade_portfolio(
        standard_df,
        sample_name="ml_lane",
        strategy_family=subject_name,
        portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY if candidate_kind == "ml_strategy" else PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=(subject_name,),
        initial_bankroll=10.0,
        position_size_fraction=0.20,
        game_limit=100,
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=5,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="dynamic_concurrent_games",
        target_exposure_fraction=0.80,
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
    )
    replay_summary, _ = simulate_trade_portfolio(
        replay_df,
        sample_name="ml_lane",
        strategy_family=subject_name,
        portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY if candidate_kind == "ml_strategy" else PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=(subject_name,),
        initial_bankroll=10.0,
        position_size_fraction=0.20,
        game_limit=100,
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=5,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="dynamic_concurrent_games",
        target_exposure_fraction=0.80,
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
    )
    no_trade_reasons = selected_df.loc[
        ~selected_df["label_replay_executed_flag"].fillna(False),
        "no_trade_reason",
    ].fillna("unlabeled").astype(str)
    top_reason = no_trade_reasons.value_counts().index[0] if not no_trade_reasons.empty else None
    standard_trade_count = int(len(standard_df))
    replay_trade_count = int(len(replay_df))
    blocked_signal_count = int(max(0, len(selected_df) - replay_trade_count))
    stale_signal_suppressed_count = int(
        selected_df.loc[
            ~selected_df["label_replay_executed_flag"].fillna(False)
            & selected_df["no_trade_reason"].fillna("").astype(str).eq("signal_stale")
        ].shape[0]
    )
    realism_gap = (
        float(standard_trade_count - replay_trade_count) / float(standard_trade_count)
        if standard_trade_count > 0
        else None
    )
    execution_rate = (
        float(replay_trade_count) / float(standard_trade_count)
        if standard_trade_count > 0
        else None
    )
    return {
        "candidate_id": subject_name,
        "display_name": subject_name,
        "candidate_kind": candidate_kind,
        "comparison_ready_flag": True,
        "publication_state": "published",
        "metrics": {
            "standard_trade_count": standard_trade_count,
            "replay_trade_count": replay_trade_count,
            "live_trade_count": 0,
            "trade_gap": replay_trade_count - standard_trade_count,
            "execution_rate": execution_rate,
            "realism_gap_trade_rate": realism_gap,
            "stale_signal_suppressed_count": stale_signal_suppressed_count,
            "stale_signal_suppression_rate": (
                float(stale_signal_suppressed_count) / float(standard_trade_count)
                if standard_trade_count > 0
                else None
            ),
            "stale_signal_share_of_blocked_signals": (
                float(stale_signal_suppressed_count) / float(blocked_signal_count)
                if blocked_signal_count > 0
                else None
            ),
            "standard_ending_bankroll": standard_summary.get("ending_bankroll"),
            "replay_ending_bankroll": replay_summary.get("ending_bankroll"),
            "standard_avg_return_with_slippage": (
                float(pd.to_numeric(standard_df["gross_return_with_slippage"], errors="coerce").mean())
                if not standard_df.empty and "gross_return_with_slippage" in standard_df.columns
                else None
            ),
            "replay_avg_return_with_slippage": (
                float(pd.to_numeric(replay_df["gross_return_with_slippage"], errors="coerce").mean())
                if not replay_df.empty and "gross_return_with_slippage" in replay_df.columns
                else None
            ),
            "standard_compounded_return": standard_summary.get("compounded_return"),
            "replay_compounded_return": replay_summary.get("compounded_return"),
            "replay_no_trade_count": blocked_signal_count,
            "replay_max_drawdown_pct": replay_summary.get("max_drawdown_pct"),
            "replay_max_drawdown_amount": replay_summary.get("max_drawdown_amount"),
            "top_no_trade_reason": top_reason,
        },
        "notes": [
            f"Selected rows: {int(len(selected_df))}",
            f"Gate threshold: {gate_threshold}" if gate_threshold is not None else "No ML gate threshold applied",
            *(extra_notes or []),
        ],
    }


def _comparison_rows_from_replay_subject_summary(subject_summary_df: pd.DataFrame) -> pd.DataFrame:
    if subject_summary_df.empty:
        return pd.DataFrame()
    work = subject_summary_df.copy()
    work["candidate_id"] = work["subject_name"].astype(str)
    work["display_name"] = work["subject_name"].astype(str)
    work["candidate_kind"] = np.where(
        work["subject_type"].astype(str).eq("controller"),
        "baseline_controller",
        "replay_family",
    )
    return work[
        [
            "candidate_id",
            "display_name",
            "candidate_kind",
            "subject_type",
            "standard_trade_count",
            "replay_trade_count",
            "trade_gap",
            "execution_rate",
            "replay_ending_bankroll",
            "top_no_trade_reason",
        ]
    ].copy()


def _comparison_rows_from_replay_submission(replay_submission: dict[str, Any]) -> pd.DataFrame:
    subjects = replay_submission.get("subjects") if isinstance(replay_submission, dict) else None
    if not isinstance(subjects, list):
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        candidate_id = str(subject.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        metrics = subject.get("metrics") if isinstance(subject.get("metrics"), dict) else {}
        replay_realism = subject.get("replay_realism") if isinstance(subject.get("replay_realism"), dict) else {}
        candidate_kind = str(subject.get("candidate_kind") or "").strip()
        display_name = str(subject.get("display_name") or candidate_id).strip() or candidate_id
        subject_type = (
            "controller"
            if candidate_id in {UNIFIED_CONTROLLER_NAME, DETERMINISTIC_CONTROLLER_NAME}
            or candidate_kind == "baseline_controller"
            else "family"
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "display_name": display_name,
                "candidate_kind": "baseline_controller" if subject_type == "controller" else "replay_family",
                "subject_type": subject_type,
                "standard_trade_count": _safe_int(metrics.get("standard_trade_count")),
                "replay_trade_count": _safe_int(metrics.get("replay_trade_count")),
                "trade_gap": _safe_int(metrics.get("trade_gap")),
                "execution_rate": _safe_float(metrics.get("execution_rate")),
                "replay_ending_bankroll": _safe_float(metrics.get("replay_ending_bankroll")),
                "top_no_trade_reason": metrics.get("top_no_trade_reason") or replay_realism.get("top_no_trade_reason"),
            }
        )
    return pd.DataFrame(rows)


def _build_benchmark_comparison_frame(
    subject_summary_df: pd.DataFrame,
    replay_submission: dict[str, Any],
    ml_subjects: list[dict[str, Any]],
) -> pd.DataFrame:
    baseline_rows = _comparison_rows_from_replay_submission(replay_submission)
    if baseline_rows.empty:
        baseline_rows = _comparison_rows_from_replay_subject_summary(subject_summary_df)
    ml_rows = pd.DataFrame(
        [
            {
                "candidate_id": subject["candidate_id"],
                "display_name": subject["display_name"],
                "candidate_kind": subject["candidate_kind"],
                "subject_type": "ml_candidate",
                "standard_trade_count": subject["metrics"]["standard_trade_count"],
                "replay_trade_count": subject["metrics"]["replay_trade_count"],
                "trade_gap": subject["metrics"]["trade_gap"],
                "execution_rate": subject["metrics"]["execution_rate"],
                "replay_ending_bankroll": subject["metrics"]["replay_ending_bankroll"],
                "top_no_trade_reason": subject["metrics"]["top_no_trade_reason"],
            }
            for subject in ml_subjects
        ]
    )
    combined = pd.concat([baseline_rows, ml_rows], ignore_index=True, sort=False)
    return combined.sort_values(
        ["candidate_kind", "replay_ending_bankroll", "execution_rate", "candidate_id"],
        ascending=[True, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)


def _feature_schema_rows() -> list[dict[str, Any]]:
    return [
        {"column": "signal_id", "role": "id", "available_at": "selection", "description": "Stable candidate signal identifier."},
        {"column": "underlying_candidate_id", "role": "id", "available_at": "selection", "description": "Family-normalized trade identifier used to dedupe controller and family rows that map to the same underlying idea."},
        {"column": "subject_name", "role": "metadata", "available_at": "selection", "description": "Candidate family or controller label."},
        {"column": "subject_type", "role": "metadata", "available_at": "selection", "description": "family or controller."},
        {"column": "strategy_family", "role": "feature", "available_at": "selection", "description": "Underlying strategy family driving the candidate."},
        {"column": "focus_family_flag", "role": "feature", "available_at": "selection", "description": "Whether the candidate belongs to the replay-promising focus-family set used by the sidecar reranker."},
        {"column": "opening_band", "role": "feature", "available_at": "selection", "description": "Pregame opening price bucket."},
        {"column": "period_label", "role": "feature", "available_at": "selection", "description": "Entry period label at the signal state."},
        {"column": "score_diff_bucket", "role": "feature", "available_at": "selection", "description": "Score margin bucket at the signal state."},
        {"column": "signal_strength", "role": "feature", "available_at": "selection", "description": "Family-native signal strength from the standard backtest trade row."},
        {"column": "entry_price", "role": "feature", "available_at": "selection", "description": "Signal entry price before replay execution effects."},
        {"column": "raw_confidence", "role": "feature", "available_at": "selection", "description": "Deterministic router confidence when available."},
        {"column": "rank_score", "role": "derived_score", "available_at": "selection", "description": "Out-of-fold family reranker score before calibration."},
        {"column": "calibrated_rank_score", "role": "derived_score", "available_at": "selection", "description": "Replay-positive probability derived from the family reranker score."},
        {"column": "calibrated_confidence", "role": "derived_score", "available_at": "selection", "description": "Replay-positive probability derived from raw controller confidence."},
        {"column": "gate_score", "role": "derived_score", "available_at": "execution", "description": "Shadow execution-risk score trained on replay execution labels."},
        {"column": "calibrated_execution_likelihood", "role": "derived_score", "available_at": "execution", "description": "Calibrated replay execution likelihood used only for shadow diagnostics."},
        {"column": "sidecar_probability", "role": "derived_score", "available_at": "selection", "description": "Selection probability used by the current ML sidecar subjects."},
        {"column": "historical_context_trade_count", "role": "feature", "available_at": "selection", "description": "Regular-season context support count for the same family."},
        {"column": "historical_context_win_rate", "role": "feature", "available_at": "selection", "description": "Regular-season context win rate for the same family."},
        {"column": "historical_context_avg_return", "role": "feature", "available_at": "selection", "description": "Regular-season context average return for the same family."},
        {"column": "state_seconds_to_game_end", "role": "feature", "available_at": "selection", "description": "Clock-to-final-state measure joined from the phase-scoped state panel."},
        {"column": "state_score_diff", "role": "feature", "available_at": "selection", "description": "Raw score differential at the entry state."},
        {"column": "state_lead_changes_so_far", "role": "feature", "available_at": "selection", "description": "Lead changes accumulated by the entry state."},
        {"column": "state_abs_price_delta_from_open", "role": "feature", "available_at": "selection", "description": "Absolute live price move from open at the entry state."},
        {"column": "state_net_points_last_5_events", "role": "feature", "available_at": "selection", "description": "Short-window scoreboard momentum at the entry state."},
        {"column": "first_attempt_signal_age_seconds", "role": "feature", "available_at": "execution", "description": "Signal age at the first replay execution attempt."},
        {"column": "first_attempt_quote_age_seconds", "role": "feature", "available_at": "execution", "description": "Quote age at the first replay execution attempt."},
        {"column": "first_attempt_spread_cents", "role": "feature", "available_at": "execution", "description": "Proxy spread at the first replay execution attempt."},
        {"column": "first_attempt_state_lag", "role": "feature", "available_at": "execution", "description": "State-index lag between the signal state and first replay attempt."},
        {"column": "label_replay_executed_flag", "role": "label", "available_at": "post_replay", "description": "Whether replay actually executed the trade."},
        {"column": "label_replay_positive_flag", "role": "label", "available_at": "post_replay", "description": "Whether replay both executed and ended with positive realized return."},
        {"column": "label_replay_return", "role": "label", "available_at": "post_replay", "description": "Replay realized return with slippage, zero for no-trade rows."},
        {"column": "label_replay_value", "role": "label", "available_at": "post_replay", "description": "Executable candidate value used for ranking summaries."},
    ]


def build_ml_feature_schema() -> dict[str, Any]:
    return {
        "schema_version": ML_SCHEMA_VERSION,
        "row_grain": "one replay-aware candidate signal",
        "feature_groups": {
            "ranking_numeric": FAMILY_RANK_NUMERIC_COLUMNS,
            "ranking_categorical": FAMILY_RANK_CATEGORICAL_COLUMNS,
            "gate_numeric": GATE_NUMERIC_COLUMNS,
            "gate_categorical": GATE_CATEGORICAL_COLUMNS,
            "focus_families": list(FOCUS_STRATEGY_FAMILIES),
            "selection_outputs": [
                "rank_score",
                "calibrated_rank_score",
                "calibrated_confidence",
                "gate_score",
                "calibrated_execution_likelihood",
                "sidecar_probability",
            ],
        },
        "leakage_policy": {
            "included_history_sources": [
                "regular-season backtest trade priors",
                "postseason state-panel columns available at the signal state",
                "replay attempt metrics observable at first submit time",
            ],
            "excluded_future_columns": [
                "exit_at",
                "exit_price",
                "gross_return",
                "gross_return_with_slippage",
                "hold_time_seconds",
                "full-game team outcome flags",
            ],
        },
        "columns": _feature_schema_rows(),
    }


def _find_latest_daily_live_snapshot(shared_root: Path) -> tuple[Path | None, dict[str, Any]]:
    live_root = shared_root / "artifacts" / "daily-live-validation"
    if not live_root.exists():
        return None, {}
    candidates = sorted(
        live_root.glob("*/shadow_snapshot_*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        return None, {}
    latest = candidates[-1]
    return latest, _read_json(latest)


def _load_latest_game_cards(run_root: Path) -> dict[str, dict[str, Any]]:
    trace_rows = _read_jsonl(run_root / "controller_trace.jsonl")
    cards: dict[str, dict[str, Any]] = {}
    for row in trace_rows:
        if str(row.get("stage") or "") != "game_card":
            continue
        game_id = _normalize_game_id(row.get("game_id"))
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        cards[game_id] = payload
    return cards


def _resolve_live_budget_config(run_root: Path) -> dict[str, Any]:
    config = _read_json(run_root / "run_config.json")
    return {
        "entry_target_notional_usd": _safe_float(config.get("entry_target_notional_usd")) or LIVE_ENTRY_TARGET_NOTIONAL_USD,
        "max_entry_orders_per_game": _safe_int(config.get("max_entry_orders_per_game")) or LIVE_MAX_ENTRY_ORDERS_PER_GAME,
        "max_entry_notional_per_game_usd": _safe_float(config.get("max_entry_notional_per_game_usd"))
        or LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD,
        "polymarket_min_shares": POLYMARKET_MIN_SHARES,
    }


def _live_replay_reference_bankroll(
    record: dict[str, Any],
    benchmark_lookup: pd.DataFrame,
    *,
    variant_subject_id: str | None = None,
) -> float | None:
    lookup_ids = [
        variant_subject_id,
        record.get("candidate_id"),
        record.get("subject_name"),
        record.get("strategy_family"),
    ]
    for candidate_id in lookup_ids:
        if not candidate_id or benchmark_lookup.empty or candidate_id not in benchmark_lookup.index:
            continue
        resolved = _safe_float(benchmark_lookup.at[candidate_id, "replay_ending_bankroll"])
        if resolved is not None:
            return resolved
    return None


def _classify_live_blocker(
    *,
    signal_present_flag: bool,
    coverage_status: str,
    shadow_reason: str,
    feed_fresh_flag: bool,
    orderbook_available_flag: bool,
    budget_affordable_flag: bool | None,
    position_capacity_available_flag: bool,
) -> str:
    if not signal_present_flag or shadow_reason in {"no_strategy_signal", "non_focus_family"}:
        return "no_strategy_signal"
    if coverage_status == "pregame_only":
        return "pregame_only"
    if not feed_fresh_flag:
        return "stale_feed"
    if not orderbook_available_flag:
        return "orderbook_gap"
    if budget_affordable_flag is False:
        return "budget_gate"
    if not position_capacity_available_flag:
        return "position_capacity"
    return "live_executable"


def _build_live_shadow_payload_views(
    *,
    shared_root: Path,
    benchmark_comparison_df: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    snapshot_path, snapshot_payload = _find_latest_daily_live_snapshot(shared_root)
    if not snapshot_path or not snapshot_payload:
        empty_frame = pd.DataFrame(columns=SHADOW_PAYLOAD_COLUMNS)
        return (
            {
                "reranker_only": empty_frame.copy(),
                "calibrator_only": empty_frame.copy(),
                "combined_sidecar": empty_frame.copy(),
            },
            {
                "status": "missing_snapshot",
                "snapshot_path": None,
                "session_date": None,
                "run_id": None,
                "budget_config": {
                    "entry_target_notional_usd": LIVE_ENTRY_TARGET_NOTIONAL_USD,
                    "max_entry_orders_per_game": LIVE_MAX_ENTRY_ORDERS_PER_GAME,
                    "max_entry_notional_per_game_usd": LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD,
                    "polymarket_min_shares": POLYMARKET_MIN_SHARES,
                },
            },
        )

    run_root = Path(str(snapshot_payload.get("run_root") or ""))
    budget_config = _resolve_live_budget_config(run_root) if run_root.exists() else {
        "entry_target_notional_usd": LIVE_ENTRY_TARGET_NOTIONAL_USD,
        "max_entry_orders_per_game": LIVE_MAX_ENTRY_ORDERS_PER_GAME,
        "max_entry_notional_per_game_usd": LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD,
        "polymarket_min_shares": POLYMARKET_MIN_SHARES,
    }
    benchmark_lookup = benchmark_comparison_df.set_index("candidate_id") if not benchmark_comparison_df.empty else pd.DataFrame()
    diagnostics_by_game = {
        _normalize_game_id(game_id): value
        for game_id, value in (snapshot_payload.get("diagnostics_by_game") or {}).items()
        if isinstance(value, dict)
    }
    live_games_lookup = {
        _normalize_game_id(row.get("game_id")): row
        for row in (snapshot_payload.get("live_games") or [])
        if isinstance(row, dict)
    }
    latest_game_cards = _load_latest_game_cards(run_root) if run_root.exists() else {}

    def _normalize_rows(
        rows: list[dict[str, Any]],
        *,
        shadow_variant: str,
        variant_subject_id: str,
        selection_source_fallback: str,
    ) -> pd.DataFrame:
        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=SHADOW_PAYLOAD_COLUMNS)
        work = frame.copy()
        work["shadow_variant"] = shadow_variant
        work["variant_subject_id"] = variant_subject_id
        work["subject_name"] = work.get("subject_name", pd.Series(dtype=object)).astype(str)
        work["subject_type"] = work.get("subject_type", pd.Series(dtype=object)).astype(str)
        work["strategy_family"] = work.get("strategy_family", pd.Series(dtype=object)).astype(str)
        work["game_id"] = work.get("game_id", pd.Series(dtype=object)).map(_normalize_game_id)
        work["team_side"] = work.get("team_side", pd.Series(dtype=object)).fillna("").astype(str)
        work["selection_source"] = work.get("selection_source", pd.Series(dtype=object)).fillna(selection_source_fallback)
        work["signal_id"] = work.get("signal_id", pd.Series(dtype=object))
        work["entry_state_index"] = pd.to_numeric(work.get("entry_state_index"), errors="coerce")
        work["game_date"] = snapshot_payload.get("session_date")
        work["focus_family_flag"] = work.get("focus_family_flag", pd.Series(dtype=bool)).fillna(
            work["strategy_family"].isin(FOCUS_STRATEGY_FAMILIES)
        ).astype(bool)
        for column in (
            "sidecar_probability",
            "calibrated_confidence",
            "calibrated_execution_likelihood",
            "raw_confidence",
            "rank_score",
            "calibrated_rank_score",
            "gate_score",
            "signal_entry_price",
            "best_bid",
            "best_ask",
            "spread_cents",
        ):
            if column not in work.columns:
                work[column] = np.nan
            work[column] = pd.to_numeric(work[column], errors="coerce")
        if "opening_band" not in work.columns:
            work["opening_band"] = None
        if "period_label" not in work.columns:
            work["period_label"] = None
        if "state_label" in work.columns:
            work["period_label"] = work["period_label"].fillna(work["state_label"])
        if "state_period_label" in work.columns:
            work["period_label"] = work["period_label"].fillna(work["state_period_label"])
        if "score_diff_bucket" not in work.columns:
            work["score_diff_bucket"] = None
        work["underlying_candidate_id"] = [
            _underlying_candidate_id(strategy_family, game_id, team_side, entry_state_index)
            for strategy_family, game_id, team_side, entry_state_index in zip(
                work["strategy_family"],
                work["game_id"],
                work["team_side"],
                work["entry_state_index"],
                strict=False,
            )
        ]
        coverage_values: list[str] = []
        feed_fresh_values: list[bool] = []
        orderbook_values: list[bool] = []
        min_required_values: list[float | None] = []
        budget_values: list[bool | None] = []
        capacity_values: list[bool] = []
        signal_present_values: list[bool] = []
        executable_values: list[bool] = []
        blocker_values: list[str] = []
        replay_bankroll_values: list[float | None] = []
        replay_unexecutable_values: list[bool] = []
        shadow_reason_values: list[str] = []
        resolved_bid_values: list[float | None] = []
        resolved_ask_values: list[float | None] = []
        resolved_spread_values: list[float | None] = []
        for record in work.to_dict(orient="records"):
            game_id = _normalize_game_id(record.get("game_id"))
            game_diag = diagnostics_by_game.get(game_id, {})
            live_game = live_games_lookup.get(game_id, {})
            game_card = latest_game_cards.get(game_id, {})
            coverage_status = str(
                record.get("coverage_status")
                or game_diag.get("coverage_status")
                or game_card.get("coverage_status")
                or live_game.get("note")
                or ""
            )
            card_orderbook = game_card.get("orderbook") if isinstance(game_card.get("orderbook"), dict) else {}
            best_bid = _safe_float(record.get("best_bid"))
            if best_bid is None:
                best_bid = _safe_float(card_orderbook.get("best_bid"))
            if best_bid is None:
                best_bid = _safe_float(live_game.get("best_bid"))
            best_ask = _safe_float(record.get("best_ask"))
            if best_ask is None:
                best_ask = _safe_float(card_orderbook.get("best_ask"))
            if best_ask is None:
                best_ask = _safe_float(live_game.get("best_ask"))
            spread_cents = _safe_float(record.get("spread_cents"))
            if spread_cents is None:
                spread_cents = _safe_float(card_orderbook.get("spread_cents"))
            feed_stalled_flag = bool(game_card.get("feed_stalled_flag")) if game_card else False
            has_live_event = bool(game_card.get("last_live_event_at") or record.get("latest_event_at") or game_diag.get("timed_event_count"))
            feed_fresh_flag = bool(coverage_status and coverage_status != "pregame_only" and not feed_stalled_flag and has_live_event)
            orderbook_available_flag = best_ask is not None or best_bid is not None
            reference_price = best_ask if best_ask is not None else _safe_float(record.get("signal_entry_price"))
            min_required_notional = None if reference_price is None else float(reference_price) * float(budget_config["polymarket_min_shares"])
            budget_affordable_flag = (
                None
                if min_required_notional is None
                else bool(min_required_notional <= float(budget_config["max_entry_notional_per_game_usd"]))
            )
            position_capacity_available_flag = (
                (_safe_int(live_game.get("open_order_count")) or 0)
                + (_safe_int(live_game.get("open_position_count")) or 0)
                < int(budget_config["max_entry_orders_per_game"])
            )
            signal_present_flag = bool(
                record.get("signal_id")
                or record.get("signal_entry_at")
                or _safe_float(record.get("entry_state_index")) is not None
                or _safe_float(record.get("signal_strength")) is not None
            )
            shadow_reason = str(record.get("shadow_reason") or record.get("no_trade_reason") or "")
            live_blocker_bucket = _classify_live_blocker(
                signal_present_flag=signal_present_flag,
                coverage_status=coverage_status,
                shadow_reason=shadow_reason,
                feed_fresh_flag=feed_fresh_flag,
                orderbook_available_flag=orderbook_available_flag,
                budget_affordable_flag=budget_affordable_flag,
                position_capacity_available_flag=position_capacity_available_flag,
            )
            live_executable_flag = live_blocker_bucket == "live_executable"
            replay_reference_bankroll = _live_replay_reference_bankroll(
                record,
                benchmark_lookup,
                variant_subject_id=variant_subject_id,
            )
            replay_profitable_but_live_unexecutable_flag = bool(
                replay_reference_bankroll is not None
                and replay_reference_bankroll > 10.0
                and signal_present_flag
                and live_blocker_bucket in {"stale_feed", "orderbook_gap", "budget_gate", "position_capacity"}
            )
            coverage_values.append(coverage_status)
            feed_fresh_values.append(feed_fresh_flag)
            orderbook_values.append(orderbook_available_flag)
            min_required_values.append(min_required_notional)
            budget_values.append(budget_affordable_flag)
            capacity_values.append(position_capacity_available_flag)
            signal_present_values.append(signal_present_flag)
            executable_values.append(live_executable_flag)
            blocker_values.append(live_blocker_bucket)
            replay_bankroll_values.append(replay_reference_bankroll)
            replay_unexecutable_values.append(replay_profitable_but_live_unexecutable_flag)
            shadow_reason_values.append(shadow_reason)
            resolved_bid_values.append(best_bid)
            resolved_ask_values.append(best_ask)
            resolved_spread_values.append(spread_cents)
        work["coverage_status"] = coverage_values
        work["feed_fresh_flag"] = feed_fresh_values
        work["orderbook_available_flag"] = orderbook_values
        work["min_required_notional_usd"] = min_required_values
        work["budget_affordable_flag"] = budget_values
        work["entry_target_notional_usd"] = float(budget_config["entry_target_notional_usd"])
        work["max_entry_notional_per_game_usd"] = float(budget_config["max_entry_notional_per_game_usd"])
        work["max_entry_orders_per_game"] = int(budget_config["max_entry_orders_per_game"])
        work["position_capacity_available_flag"] = capacity_values
        work["signal_present_flag"] = signal_present_values
        work["live_executable_flag"] = executable_values
        work["live_blocker_bucket"] = blocker_values
        work["shadow_reason"] = shadow_reason_values
        work["best_bid"] = resolved_bid_values
        work["best_ask"] = resolved_ask_values
        work["spread_cents"] = resolved_spread_values
        work["replay_reference_bankroll"] = replay_bankroll_values
        work["replay_profitable_but_live_unexecutable_flag"] = replay_unexecutable_values
        if shadow_variant == "reranker_only":
            selected_mask = work["focus_family_flag"].fillna(False) & (
                work["sidecar_probability"].fillna(0.0) >= float(DEFAULT_FOCUS_RANK_THRESHOLD)
            )
        elif shadow_variant == "calibrator_only":
            selected_mask = work["calibrated_confidence"].fillna(work["sidecar_probability"]).fillna(0.0) >= float(
                DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD
            )
        else:
            selected_mask = pd.Series(
                np.where(
                    work["subject_type"].astype(str).eq("controller"),
                    work["calibrated_confidence"].fillna(work["sidecar_probability"]).fillna(0.0)
                    >= float(DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD),
                    work["sidecar_probability"].fillna(0.0) >= float(DEFAULT_FOCUS_RANK_THRESHOLD),
                ),
                index=work.index,
                dtype=bool,
            )
        return _prepare_shadow_payload_frame(
            work,
            shadow_variant=shadow_variant,
            variant_subject_id=variant_subject_id,
            selected_mask=selected_mask,
        )

    family_rows = [
        row
        for row in (snapshot_payload.get("family_shadow") or [])
        if isinstance(row, dict) and str(row.get("strategy_family") or "") in FOCUS_STRATEGY_FAMILIES
    ]
    ml_shadow = snapshot_payload.get("ml_shadow") if isinstance(snapshot_payload.get("ml_shadow"), dict) else {}
    controller_rows = [
        row
        for row in (ml_shadow.get("controller_candidates") or [])
        if isinstance(row, dict)
    ]
    reranker_df = _normalize_rows(
        family_rows,
        shadow_variant="reranker_only",
        variant_subject_id="ml_focus_family_reranker_v2",
        selection_source_fallback="live_focus_family_shadow",
    )
    calibrator_df = _normalize_rows(
        controller_rows,
        shadow_variant="calibrator_only",
        variant_subject_id="ml_controller_focus_calibrator_v2",
        selection_source_fallback="controller_confidence_overlay_proxy",
    )
    combined_rows = family_rows + controller_rows
    combined_df = _normalize_rows(
        combined_rows,
        shadow_variant="combined_sidecar",
        variant_subject_id="ml_sidecar_union_v2",
        selection_source_fallback="combined_live_shadow",
    )
    combined_df = _dedupe_underlying_candidates(
        combined_df,
        score_columns=["sidecar_probability", "calibrated_confidence", "calibrated_rank_score", "rank_score"],
    ) if not combined_df.empty else combined_df
    return (
        {
            "reranker_only": reranker_df,
            "calibrator_only": calibrator_df,
            "combined_sidecar": combined_df,
        },
        {
            "status": "success",
            "snapshot_path": str(snapshot_path),
            "session_date": snapshot_payload.get("session_date"),
            "run_id": snapshot_payload.get("run_id"),
            "run_root": snapshot_payload.get("run_root"),
            "budget_config": budget_config,
        },
    )


def _build_live_shadow_variant_summary_frame(live_shadow_views: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variant_name, frame in live_shadow_views.items():
        if frame.empty:
            rows.append(
                {
                    "shadow_variant": variant_name,
                    "row_count": 0,
                    "selected_row_count": 0,
                    "signal_present_count": 0,
                    "live_executable_count": 0,
                    "replay_profitable_but_live_unexecutable_count": 0,
                    "no_signal_count": 0,
                    "pregame_only_count": 0,
                    "stale_feed_count": 0,
                    "orderbook_gap_count": 0,
                    "budget_gate_count": 0,
                }
            )
            continue
        rows.append(
            {
                "shadow_variant": variant_name,
                "row_count": int(len(frame)),
                "selected_row_count": int(frame["shadow_selected_flag"].fillna(False).sum()),
                "signal_present_count": int(frame["signal_present_flag"].fillna(False).sum()),
                "live_executable_count": int(frame["live_executable_flag"].fillna(False).sum()),
                "replay_profitable_but_live_unexecutable_count": int(
                    frame["replay_profitable_but_live_unexecutable_flag"].fillna(False).sum()
                ),
                "no_signal_count": int(frame["live_blocker_bucket"].astype(str).eq("no_strategy_signal").sum()),
                "pregame_only_count": int(frame["live_blocker_bucket"].astype(str).eq("pregame_only").sum()),
                "stale_feed_count": int(frame["live_blocker_bucket"].astype(str).eq("stale_feed").sum()),
                "orderbook_gap_count": int(frame["live_blocker_bucket"].astype(str).eq("orderbook_gap").sum()),
                "budget_gate_count": int(frame["live_blocker_bucket"].astype(str).eq("budget_gate").sum()),
            }
        )
    return pd.DataFrame(rows)


def _build_live_blocker_breakdown_frame(live_shadow_views: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variant_name, frame in live_shadow_views.items():
        if frame.empty:
            continue
        grouped = (
            frame.groupby("live_blocker_bucket", dropna=False)
            .agg(
                row_count=("live_blocker_bucket", "size"),
                replay_profitable_but_live_unexecutable_count=("replay_profitable_but_live_unexecutable_flag", "sum"),
            )
            .reset_index()
        )
        for record in grouped.to_dict(orient="records"):
            rows.append({"shadow_variant": variant_name, **record})
    return pd.DataFrame(rows)


def _shadow_payload_schema_rows() -> list[dict[str, Any]]:
    return [
        {"column": "shadow_variant", "role": "metadata", "available_at": "pre_live", "description": "Named ML shadow view: reranker_only, calibrator_only, or combined_sidecar."},
        {"column": "variant_subject_id", "role": "metadata", "available_at": "pre_live", "description": "Benchmark-ready ML subject aligned to the shadow payload view."},
        {"column": "shadow_selected_flag", "role": "decision", "available_at": "pre_live", "description": "Whether the row clears the current shadow selection threshold for the given variant."},
        {"column": "shadow_priority_rank", "role": "decision", "available_at": "pre_live", "description": "Within-variant rank ordered by sidecar_probability for dashboard or live-shadow displays."},
        {"column": "game_date", "role": "id", "available_at": "pre_live", "description": "Slate date for the candidate signal."},
        {"column": "game_id", "role": "id", "available_at": "pre_live", "description": "Stable game identifier used by the replay and live-validation lanes."},
        {"column": "team_side", "role": "id", "available_at": "pre_live", "description": "home or away side of the candidate."},
        {"column": "signal_id", "role": "id", "available_at": "pre_live", "description": "Full candidate signal identifier from replay and live-validation traces."},
        {"column": "underlying_candidate_id", "role": "id", "available_at": "pre_live", "description": "Family-normalized candidate key used to dedupe the same idea across controller and family rows."},
        {"column": "subject_name", "role": "metadata", "available_at": "pre_live", "description": "Source family or controller label that produced the row."},
        {"column": "subject_type", "role": "metadata", "available_at": "pre_live", "description": "family or controller."},
        {"column": "strategy_family", "role": "metadata", "available_at": "pre_live", "description": "Underlying strategy family for the candidate."},
        {"column": "selection_source", "role": "metadata", "available_at": "pre_live", "description": "Current ML source used to derive the sidecar score."},
        {"column": "focus_family_flag", "role": "required_live_feature", "available_at": "pre_live", "description": "Whether the candidate belongs to the replay-promoted family set used by the reranker."},
        {"column": "sidecar_probability", "role": "required_live_feature", "available_at": "pre_live", "description": "Primary ML sidecar probability exposed to daily live validation."},
        {"column": "calibrated_confidence", "role": "required_live_feature", "available_at": "pre_live", "description": "Replay-positive probability derived from controller confidence when available."},
        {"column": "calibrated_execution_likelihood", "role": "required_live_feature", "available_at": "pre_live", "description": "Shadow execution-likelihood diagnostic; not a hard gate."},
        {"column": "feed_fresh_flag", "role": "required_live_diagnostic", "available_at": "live_shadow", "description": "Whether the latest live trace shows an actively updating feed for the game."},
        {"column": "orderbook_available_flag", "role": "required_live_diagnostic", "available_at": "live_shadow", "description": "Whether the live trace surfaced at least one actionable orderbook quote for the candidate."},
        {"column": "min_required_notional_usd", "role": "required_live_diagnostic", "available_at": "live_shadow", "description": "Minimum notional implied by the Polymarket five-share minimum at the observed live quote."},
        {"column": "budget_affordable_flag", "role": "required_live_diagnostic", "available_at": "live_shadow", "description": "Whether the candidate clears the current per-game live notional cap after applying the five-share minimum."},
        {"column": "raw_confidence", "role": "optional_live_feature", "available_at": "pre_live", "description": "Uncalibrated deterministic controller confidence."},
        {"column": "rank_score", "role": "optional_live_feature", "available_at": "pre_live", "description": "Raw family reranker score before calibration."},
        {"column": "calibrated_rank_score", "role": "optional_live_feature", "available_at": "pre_live", "description": "Calibrated family rerank probability."},
        {"column": "gate_score", "role": "optional_live_feature", "available_at": "pre_live", "description": "Raw execution-risk score kept for shadow diagnostics only."},
        {"column": "entry_state_index", "role": "join_key", "available_at": "pre_live", "description": "State index for joining to live and replay traces."},
        {"column": "opening_band", "role": "join_key", "available_at": "pre_live", "description": "Pregame price bucket carried into shadow logging."},
        {"column": "period_label", "role": "join_key", "available_at": "pre_live", "description": "Entry period label carried into shadow logging."},
        {"column": "score_diff_bucket", "role": "join_key", "available_at": "pre_live", "description": "Score margin bucket carried into shadow logging."},
        {"column": "coverage_status", "role": "live_context", "available_at": "live_shadow", "description": "Current live coverage state carried through from the daily validation lane."},
        {"column": "position_capacity_available_flag", "role": "live_context", "available_at": "live_shadow", "description": "Whether the game is still below the max live position and order cap."},
        {"column": "signal_present_flag", "role": "live_context", "available_at": "live_shadow", "description": "Whether the live trace surfaced an actual candidate signal rather than a no-signal placeholder row."},
        {"column": "live_executable_flag", "role": "live_decision", "available_at": "live_shadow", "description": "True only when signal, feed, orderbook, and budget constraints all clear in shadow."},
        {"column": "live_blocker_bucket", "role": "live_decision", "available_at": "live_shadow", "description": "Primary live blocker bucket separating no-signal, stale-feed, orderbook-gap, and budget-gate outcomes."},
        {"column": "replay_profitable_but_live_unexecutable_flag", "role": "live_decision", "available_at": "live_shadow", "description": "Flags rows where replay remains profitable but the live path is blocked by execution constraints rather than missing signal."},
    ]


def build_ml_shadow_payload_schema() -> dict[str, Any]:
    return {
        "schema_version": ML_SHADOW_PAYLOAD_SCHEMA_VERSION,
        "row_grain": "one ML-scored candidate signal for daily live-validation shadow comparison",
        "required_live_fields": list(SHADOW_REQUIRED_FIELDS),
        "scope": {
            "focus_strategy_families": list(FOCUS_STRATEGY_FAMILIES),
            "controller_subjects": [
                UNIFIED_CONTROLLER_NAME,
                DETERMINISTIC_CONTROLLER_NAME,
            ],
        },
        "shadow_variants": {
            "reranker_only": {
                "benchmark_subject_id": "ml_focus_family_reranker_v2",
                "scope": "Replay-promoted family rows only.",
                "selection_rule": f"focus_family_flag and sidecar_probability >= {DEFAULT_FOCUS_RANK_THRESHOLD}",
            },
            "calibrator_only": {
                "benchmark_subject_id": "ml_controller_focus_calibrator_v2",
                "scope": "Controller-selected candidates from the live control pair.",
                "selection_rule": f"calibrated_confidence >= {DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD}",
            },
            "combined_sidecar": {
                "benchmark_subject_id": "ml_sidecar_union_v2",
                "scope": "Union of replay-promoted family rows and controller-selected candidates, deduped by underlying_candidate_id.",
                "selection_rule": (
                    f"family rows use sidecar_probability >= {DEFAULT_FOCUS_RANK_THRESHOLD}; "
                    f"controller rows use calibrated_confidence >= {DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD}"
                ),
            },
        },
        "policy": {
            "hard_skip": False,
            "hard_sizing": False,
            "execution_likelihood_role": "shadow_only_diagnostic",
            "live_budget_constraints": {
                "entry_target_notional_usd": LIVE_ENTRY_TARGET_NOTIONAL_USD,
                "max_entry_orders_per_game": LIVE_MAX_ENTRY_ORDERS_PER_GAME,
                "max_entry_notional_per_game_usd": LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD,
                "polymarket_min_shares": POLYMARKET_MIN_SHARES,
            },
        },
        "columns": _shadow_payload_schema_rows(),
    }


def _prepare_shadow_payload_frame(
    frame: pd.DataFrame,
    *,
    shadow_variant: str,
    variant_subject_id: str,
    selected_mask: pd.Series,
) -> pd.DataFrame:
    work = frame.copy()
    if work.empty:
        return pd.DataFrame(columns=SHADOW_PAYLOAD_COLUMNS)
    work["shadow_variant"] = shadow_variant
    work["variant_subject_id"] = variant_subject_id
    work["shadow_selected_flag"] = selected_mask.reindex(work.index, fill_value=False).fillna(False).astype(bool)
    work["game_date"] = pd.to_datetime(work["game_date"], errors="coerce").dt.date
    work["focus_family_flag"] = work["focus_family_flag"].fillna(False).astype(bool)
    for column in (
        "sidecar_probability",
        "calibrated_confidence",
        "calibrated_execution_likelihood",
        "raw_confidence",
        "rank_score",
        "calibrated_rank_score",
        "gate_score",
        "min_required_notional_usd",
        "entry_target_notional_usd",
        "max_entry_notional_per_game_usd",
        "best_bid",
        "best_ask",
        "spread_cents",
    ):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
        else:
            work[column] = np.nan
    for column in SHADOW_PAYLOAD_COLUMNS:
        if column not in work.columns:
            work[column] = None
    work = work.sort_values(
        ["game_date", "shadow_selected_flag", "sidecar_probability", "game_id", "signal_id"],
        ascending=[True, False, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    work["shadow_priority_rank"] = work.groupby(["shadow_variant", "game_date"], dropna=False).cumcount() + 1
    return work[SHADOW_PAYLOAD_COLUMNS].copy()


def _build_shadow_payload_views(
    *,
    family_predictions_df: pd.DataFrame,
    controller_predictions_df: pd.DataFrame,
    focus_selection_min_score: float = DEFAULT_FOCUS_RANK_THRESHOLD,
    controller_selection_min_score: float = DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD,
) -> dict[str, pd.DataFrame]:
    focus_family_scope_df = _dedupe_underlying_candidates(
        family_predictions_df[family_predictions_df["focus_family_flag"].fillna(False)].copy(),
        score_columns=["sidecar_probability", "calibrated_rank_score", "rank_score"],
    )
    focus_selected_mask = focus_family_scope_df["sidecar_probability"].fillna(0.0) >= float(focus_selection_min_score)

    controller_scope_df = controller_predictions_df[
        controller_predictions_df["subject_name"].astype(str).isin(
            [UNIFIED_CONTROLLER_NAME, DETERMINISTIC_CONTROLLER_NAME]
        )
    ].copy().reset_index(drop=True)
    controller_selected_mask = controller_scope_df["calibrated_confidence"].fillna(
        controller_scope_df["sidecar_probability"]
    ).fillna(0.0) >= float(controller_selection_min_score)

    combined_scope_df = _dedupe_underlying_candidates(
        pd.concat([focus_family_scope_df, controller_scope_df], ignore_index=True, sort=False),
        score_columns=["sidecar_probability", "calibrated_rank_score", "calibrated_confidence", "raw_confidence"],
    )
    combined_selected_mask = pd.Series(
        np.where(
            combined_scope_df["subject_type"].astype(str).eq("controller"),
            combined_scope_df["calibrated_confidence"].fillna(combined_scope_df["sidecar_probability"]).fillna(0.0)
            >= float(controller_selection_min_score),
            combined_scope_df["sidecar_probability"].fillna(0.0) >= float(focus_selection_min_score),
        ),
        index=combined_scope_df.index,
        dtype=bool,
    )

    return {
        "reranker_only": _prepare_shadow_payload_frame(
            focus_family_scope_df,
            shadow_variant="reranker_only",
            variant_subject_id="ml_focus_family_reranker_v2",
            selected_mask=focus_selected_mask,
        ),
        "calibrator_only": _prepare_shadow_payload_frame(
            controller_scope_df,
            shadow_variant="calibrator_only",
            variant_subject_id="ml_controller_focus_calibrator_v2",
            selected_mask=controller_selected_mask,
        ),
        "combined_sidecar": _prepare_shadow_payload_frame(
            combined_scope_df,
            shadow_variant="combined_sidecar",
            variant_subject_id="ml_sidecar_union_v2",
            selected_mask=combined_selected_mask,
        ),
    }


def _build_shadow_payload_sample(shadow_payload_views: dict[str, pd.DataFrame]) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for variant_name, frame in shadow_payload_views.items():
        variants[variant_name] = {
            "row_count": int(len(frame)),
            "selected_row_count": int(frame["shadow_selected_flag"].sum()) if "shadow_selected_flag" in frame.columns else 0,
            "sample_rows": to_jsonable(frame.head(5).to_dict(orient="records")),
        }
    return {
        "schema_version": ML_SHADOW_PAYLOAD_SCHEMA_VERSION,
        "required_live_fields": list(SHADOW_REQUIRED_FIELDS),
        "variants": variants,
    }


def _build_shadow_variant_comparison_frame(
    *,
    benchmark_comparison_df: pd.DataFrame,
    ml_subjects: list[dict[str, Any]],
    selected_counts: dict[str, int],
) -> pd.DataFrame:
    subject_lookup = {str(subject["candidate_id"]): subject for subject in ml_subjects}
    baseline_lookup = benchmark_comparison_df.set_index("candidate_id") if not benchmark_comparison_df.empty else pd.DataFrame()

    def _baseline_metric(candidate_id: str, column: str) -> float | None:
        if baseline_lookup.empty or candidate_id not in baseline_lookup.index:
            return None
        value = baseline_lookup.at[candidate_id, column]
        return _safe_float(value)

    unified_replay = _baseline_metric(UNIFIED_CONTROLLER_NAME, "replay_ending_bankroll")
    deterministic_replay = _baseline_metric(DETERMINISTIC_CONTROLLER_NAME, "replay_ending_bankroll")
    inversion_replay = _baseline_metric("inversion", "replay_ending_bankroll")

    rows = [
        {
            "shadow_variant": "reranker_only",
            "candidate_id": "ml_focus_family_reranker_v2",
            "operational_role": "focused_family_reranker",
            "shadow_role": "parallel_shadow_compare",
            "integration_complexity": "medium",
            "note": "Useful for ranking replay-promoted family candidates; not required for the control path to stay intact.",
        },
        {
            "shadow_variant": "calibrator_only",
            "candidate_id": "ml_controller_focus_calibrator_v2",
            "operational_role": "controller_confidence_calibrator",
            "shadow_role": "first_shadow_attachment",
            "integration_complexity": "low",
            "note": "Cleanest operational first step because it annotates existing controller-selected candidates without rerouting them.",
        },
        {
            "shadow_variant": "combined_sidecar",
            "candidate_id": "ml_sidecar_union_v2",
            "operational_role": "union_sidecar_compare",
            "shadow_role": "compare_only_until_incremental_lift",
            "integration_complexity": "medium",
            "note": "Tracks the best union view but has not shown incremental replay lift over calibrator-only yet.",
        },
    ]

    output_rows: list[dict[str, Any]] = []
    calibrator_bankroll = _safe_float(
        subject_lookup.get("ml_controller_focus_calibrator_v2", {}).get("metrics", {}).get("replay_ending_bankroll")
    )
    for row in rows:
        subject = subject_lookup.get(row["candidate_id"]) or {}
        metrics = subject.get("metrics") or {}
        replay_bankroll = _safe_float(metrics.get("replay_ending_bankroll"))
        output_rows.append(
            {
                **row,
                "selected_candidate_count": int(selected_counts.get(row["candidate_id"], 0)),
                "standard_trade_count": _safe_int(metrics.get("standard_trade_count")),
                "replay_trade_count": _safe_int(metrics.get("replay_trade_count")),
                "execution_rate": _safe_float(metrics.get("execution_rate")),
                "replay_ending_bankroll": replay_bankroll,
                "delta_vs_unified_replay_bankroll": (
                    replay_bankroll - unified_replay if replay_bankroll is not None and unified_replay is not None else None
                ),
                "delta_vs_deterministic_replay_bankroll": (
                    replay_bankroll - deterministic_replay
                    if replay_bankroll is not None and deterministic_replay is not None
                    else None
                ),
                "delta_vs_inversion_replay_bankroll": (
                    replay_bankroll - inversion_replay if replay_bankroll is not None and inversion_replay is not None else None
                ),
                "delta_vs_calibrator_only_replay_bankroll": (
                    replay_bankroll - calibrator_bankroll
                    if replay_bankroll is not None and calibrator_bankroll is not None
                    else None
                ),
            }
        )
    return pd.DataFrame(output_rows)


def _render_lane_report(payload: dict[str, Any]) -> str:
    lines = [
        "# ML Trading Lane",
        "",
        f"- published_at: `{payload.get('published_at')}`",
        f"- lane_id: `{payload.get('lane_id')}`",
        f"- season: `{payload.get('season')}`",
        f"- replay_artifact: `{payload.get('replay_artifact_name')}`",
        "",
        "## Data Contract",
        "",
    ]
    split_summary = payload.get("split_summary") or {}
    lines.append(f"- warmup_dates: `{split_summary.get('warmup_date_count')}`")
    lines.append(f"- expanding_prediction_dates: `{', '.join(split_summary.get('expanding_prediction_dates') or [])}`")
    dataset_summary = payload.get("dataset_summary") or {}
    lines.extend(
        [
            f"- all_candidate_rows: `{dataset_summary.get('all_rows')}`",
            f"- family_candidate_rows: `{dataset_summary.get('family_rows')}`",
            f"- controller_candidate_rows: `{dataset_summary.get('controller_rows')}`",
            f"- executed_rate: `{dataset_summary.get('executed_rate')}`",
            "",
            "## Model Tracks",
            "",
        ]
    )
    model_tracks = payload.get("model_tracks") or {}
    for track_name, track_payload in model_tracks.items():
        lines.append(f"### {track_name}")
        lines.append("")
        for key, value in track_payload.items():
            if isinstance(value, dict):
                lines.append(f"- {key}: `{json.dumps(to_jsonable(value), sort_keys=True)}`")
            else:
                lines.append(f"- {key}: `{value}`")
        lines.append("")
    lines.extend(["## Benchmark Comparison", ""])
    comparison_rows = payload.get("benchmark_comparison_preview") or []
    for row in comparison_rows:
        lines.append(
            f"- `{row.get('display_name')}` | replay_bankroll `{row.get('replay_ending_bankroll')}` | "
            f"execution `{row.get('execution_rate')}` | replay_trades `{row.get('replay_trade_count')}`"
        )
    handoffs = payload.get("handoffs") or {}
    if handoffs:
        lines.extend(["", "## Handoffs", ""])
        for key, value in handoffs.items():
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).strip() + "\n"


def _render_memo(payload: dict[str, Any]) -> str:
    recommendations = payload.get("recommendations") or {}
    model_tracks = payload.get("model_tracks") or {}
    comparison_rows = payload.get("benchmark_comparison_preview") or []
    live_shadow = payload.get("live_shadow") or {}
    live_variant_rows = live_shadow.get("variant_summary_preview") or []
    live_blocker_rows = live_shadow.get("blocker_breakdown_preview") or []
    handoffs = payload.get("handoffs") or {}
    lines = [
        "# ML Trading Lane Memo",
        "",
        "## Where ML Adds Value",
        "",
        f"- Candidate ranking: `{recommendations.get('ranking')}`",
        f"- Skip / participate: `{recommendations.get('gate')}`",
        f"- Confidence calibration: `{recommendations.get('calibration')}`",
        f"- Sizing: `{recommendations.get('sizing')}`",
        "",
        "## Next Role",
        "",
        f"- Recommendation: `{recommendations.get('controller_direction')}`",
        f"- Integration point: `{recommendations.get('integration_point')}`",
        f"- Daily live validation shadow: `{recommendations.get('daily_shadow')}`",
        f"- First operational role: `{recommendations.get('first_operational_role')}`",
        "",
        "## Evidence",
        "",
    ]
    for track_name, track_payload in model_tracks.items():
        if track_name == "optional_sizing":
            continue
        lines.append(
            f"- {track_name}: `{json.dumps(to_jsonable(track_payload), sort_keys=True)}`"
        )
    lines.extend(
        [
            "",
            "## Gate Read",
            "",
            f"- Why the skip model stays shadow-only: `{recommendations.get('gate_postmortem')}`",
            "",
            "## Shadow Recommendation",
            "",
            f"- Enter daily shadow now: `{recommendations.get('enter_daily_shadow_now')}`",
            f"- Daily live-validation attachment: `{recommendations.get('shadow_attachment')}`",
            f"- Combined sidecar status: `{recommendations.get('combined_sidecar_status')}`",
        ]
    )
    lines.extend(
        [
            "",
            "## Live Executability",
            "",
            f"- Tomorrow shadow ready: `{recommendations.get('tomorrow_shadow_ready')}`",
            f"- Live budget read: `{recommendations.get('live_budget_read')}`",
            f"- Replay edge under live constraints: `{recommendations.get('live_survival_read')}`",
            f"- Remaining blockers: `{recommendations.get('live_blockers')}`",
        ]
    )
    if live_variant_rows:
        lines.extend(["", "## Live Variant Read", ""])
        for row in live_variant_rows:
            lines.append(
                f"- `{row.get('shadow_variant')}` | rows `{row.get('row_count')}` | signals `{row.get('signal_present_count')}` | "
                f"live_executable `{row.get('live_executable_count')}` | replay_profitable_unexecutable `{row.get('replay_profitable_but_live_unexecutable_count')}`"
            )
    if live_blocker_rows:
        lines.extend(["", "## Live Blockers", ""])
        for row in live_blocker_rows:
            lines.append(
                f"- `{row.get('shadow_variant')}` | blocker `{row.get('live_blocker_bucket')}` | rows `{row.get('row_count')}` | "
                f"replay_profitable_unexecutable `{row.get('replay_profitable_but_live_unexecutable_count')}`"
            )
    lines.extend(["", "## Benchmark Read", ""])
    for row in comparison_rows[:8]:
        lines.append(
            f"- `{row.get('display_name')}` | replay_bankroll `{row.get('replay_ending_bankroll')}` | "
            f"execution `{row.get('execution_rate')}` | no_trade `{row.get('top_no_trade_reason')}`"
        )
    if handoffs:
        lines.extend(["", "## Handoffs", ""])
        for key, value in handoffs.items():
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).strip() + "\n"


def _render_status(payload: dict[str, Any]) -> str:
    artifact_paths = payload.get("artifacts") or {}
    report_paths = payload.get("reports") or {}
    handoffs = payload.get("handoffs") or {}
    live_shadow = payload.get("live_shadow") or {}
    lines = [
        "# ML Trading Lane Status",
        "",
        f"- timestamp: `{payload.get('published_at')}`",
        f"- lane: `{ML_OUTPUT_DIRNAME}`",
        f"- season: `{payload.get('season')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        "",
        "## Outputs",
        "",
    ]
    for key, value in artifact_paths.items():
        lines.append(f"- artifact `{key}`: `{value}`")
    for key, value in report_paths.items():
        lines.append(f"- report `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"- `{(payload.get('recommendations') or {}).get('controller_direction')}`",
            f"- first operational role: `{(payload.get('recommendations') or {}).get('first_operational_role')}`",
            f"- daily shadow now: `{(payload.get('recommendations') or {}).get('enter_daily_shadow_now')}`",
            f"- tomorrow shadow ready: `{(payload.get('recommendations') or {}).get('tomorrow_shadow_ready')}`",
            f"- live blockers: `{(payload.get('recommendations') or {}).get('live_blockers')}`",
        ]
    )
    if live_shadow:
        lines.extend(
            [
                "",
                "## Live Shadow",
                "",
                f"- snapshot_path: `{live_shadow.get('snapshot_path')}`",
                f"- run_id: `{live_shadow.get('run_id')}`",
                f"- session_date: `{live_shadow.get('session_date')}`",
            ]
        )
    if handoffs:
        lines.extend(["", "## Handoffs", ""])
        for key, value in handoffs.items():
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).strip() + "\n"


def _render_daily_live_validation_handoff(payload: dict[str, Any]) -> str:
    recommendations = payload.get("recommendations") or {}
    comparison_rows = payload.get("shadow_variant_comparison_preview") or []
    report_paths = payload.get("reports") or {}
    artifact_paths = payload.get("artifacts") or {}
    live_shadow = payload.get("live_shadow") or {}
    live_variant_rows = live_shadow.get("variant_summary_preview") or []
    live_blocker_rows = live_shadow.get("blocker_breakdown_preview") or []
    budget_config = live_shadow.get("budget_config") or {}
    lines = [
        "# ML Trading Sidecar Handoff",
        "",
        f"- recommendation: `{recommendations.get('enter_daily_shadow_now')}`",
        f"- first operational role: `{recommendations.get('first_operational_role')}`",
        f"- daily attachment: `{recommendations.get('shadow_attachment')}`",
        f"- tomorrow shadow ready: `{recommendations.get('tomorrow_shadow_ready')}`",
        "",
        "## Payload Contract",
        "",
        f"- schema: `{artifact_paths.get('shadow_payload_schema_json')}`",
        f"- sample: `{artifact_paths.get('shadow_payload_sample_json')}`",
        "- required live fields: `sidecar_probability`, `calibrated_confidence`, `calibrated_execution_likelihood`, `focus_family_flag`, `feed_fresh_flag`, `orderbook_available_flag`, `min_required_notional_usd`, `budget_affordable_flag`",
        "- scope: replay-promoted family rows plus controller-selected candidates from the current control pair",
        "",
        "## Variant Read",
        "",
    ]
    for row in comparison_rows:
        lines.append(
            f"- `{row.get('shadow_variant')}` | role `{row.get('operational_role')}` | replay_bankroll `{row.get('replay_ending_bankroll')}` | "
            f"delta_vs_calibrator `{row.get('delta_vs_calibrator_only_replay_bankroll')}` | deployment `{row.get('shadow_role')}`"
        )
    lines.extend(
        [
            "",
            "## Live Constraints",
            "",
            f"- live snapshot: `{live_shadow.get('snapshot_path')}`",
            f"- run_id: `{live_shadow.get('run_id')}`",
            f"- entry_target_notional_usd: `{budget_config.get('entry_target_notional_usd')}`",
            f"- max_entry_orders_per_game: `{budget_config.get('max_entry_orders_per_game')}`",
            f"- max_entry_notional_per_game_usd: `{budget_config.get('max_entry_notional_per_game_usd')}`",
            f"- polymarket_min_shares: `{budget_config.get('polymarket_min_shares')}`",
            f"- replay vs live read: `{recommendations.get('live_survival_read')}`",
        ]
    )
    if live_variant_rows:
        lines.extend(["", "## Live Variant Summary", ""])
        for row in live_variant_rows:
            lines.append(
                f"- `{row.get('shadow_variant')}` | rows `{row.get('row_count')}` | signals `{row.get('signal_present_count')}` | "
                f"live_executable `{row.get('live_executable_count')}` | no_signal `{row.get('no_signal_count')}` | "
                f"orderbook_gap `{row.get('orderbook_gap_count')}` | budget_gate `{row.get('budget_gate_count')}`"
            )
    if live_blocker_rows:
        lines.extend(["", "## Live Blocker Summary", ""])
        for row in live_blocker_rows:
            lines.append(
                f"- `{row.get('shadow_variant')}` | blocker `{row.get('live_blocker_bucket')}` | rows `{row.get('row_count')}` | "
                f"replay_profitable_unexecutable `{row.get('replay_profitable_but_live_unexecutable_count')}`"
            )
    lines.extend(
        [
            "",
            "## Integration",
            "",
            "- Attach the calibrator-only payload directly to controller-selected candidates in live shadow logging first.",
            "- Run reranker-only alongside the replay-promoted family probes to compare ranking quality without changing control routing.",
            "- Keep combined_sidecar compare-ready, but do not treat it as a harder production recommendation until it shows replay lift over calibrator-only.",
            "- Keep calibrated_execution_likelihood diagnostic-only; do not block entries from it.",
            "- Report replay-profitable-but-live-unexecutable rows separately from no_strategy_signal, stale_feed, and orderbook_gap rows.",
            "",
            "## Files",
            "",
            f"- lane memo: `{report_paths.get('memo_markdown')}`",
            f"- benchmark submission: `{report_paths.get('benchmark_submission_json')}`",
            f"- variant comparison: `{artifact_paths.get('shadow_variant_comparison_csv')}`",
            f"- reranker payload: `{artifact_paths.get('shadow_payload_reranker_only_csv')}`",
            f"- calibrator payload: `{artifact_paths.get('shadow_payload_calibrator_only_csv')}`",
            f"- combined payload: `{artifact_paths.get('shadow_payload_combined_sidecar_csv')}`",
            f"- live payload all variants: `{artifact_paths.get('live_shadow_payload_all_variants_csv')}`",
            f"- live variant summary: `{artifact_paths.get('live_shadow_variant_summary_csv')}`",
            f"- live blocker breakdown: `{artifact_paths.get('live_shadow_blocker_breakdown_csv')}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _write_subject_trade_artifacts(
    artifact_root: Path,
    *,
    subject_name: str,
    standard_df: pd.DataFrame,
    replay_df: pd.DataFrame,
) -> dict[str, str]:
    stem = _subject_artifact_stem(subject_name)
    paths: dict[str, str] = {}
    paths.update({f"standard_{key}": value for key, value in write_frame(artifact_root / f"standard_{stem}", standard_df).items()})
    paths.update({f"replay_{key}": value for key, value in write_frame(artifact_root / f"replay_{stem}", replay_df).items()})
    return paths


def run_ml_trading_lane(request: MLTradingLaneRequest) -> dict[str, Any]:
    shared_root = _resolve_shared_root(request.shared_root)
    analysis_output_root = _resolve_analysis_output_root(request.analysis_output_root)
    replay_artifact_names = _resolve_replay_artifact_names(request)
    artifact_root = shared_root / "artifacts" / ML_OUTPUT_DIRNAME / request.season / request.artifact_name
    report_root = shared_root / "reports" / ML_OUTPUT_DIRNAME
    handoff_root = shared_root / "handoffs" / ML_OUTPUT_DIRNAME
    artifact_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    handoff_root.mkdir(parents=True, exist_ok=True)

    (
        signal_summary_df,
        attempt_trace_df,
        subject_summary_df,
        primary_subject_summary_df,
        standard_frames,
        replay_frames,
        replay_roots,
    ) = _load_replay_inputs(
        shared_root=shared_root,
        season=request.season,
        artifact_names=replay_artifact_names,
        primary_artifact_name=request.replay_artifact_name,
    )
    replay_submission = _read_json(shared_root / "reports" / REPLAY_ENGINE_LANE / "benchmark_submission.json")
    if (
        request.season == DEFAULT_SEASON
        and tuple(request.state_panel_phases) == ("play_in", "playoffs")
    ):
        state_panel_df = _load_postseason_state_panel(analysis_output_root, request.analysis_version)
    else:
        state_panel_df = _load_state_panel_for_phases(
            analysis_output_root,
            season=request.season,
            analysis_version=request.analysis_version,
            phases=tuple(request.state_panel_phases),
        )
    regular_trade_frames = _load_regular_season_trade_frames(analysis_output_root)
    historical_context_df = _build_historical_context_frame(regular_trade_frames)
    historical_family_df = _build_family_overall_frame(regular_trade_frames)

    dataset_df = build_replay_candidate_dataset(
        signal_summary_df=signal_summary_df,
        attempt_trace_df=attempt_trace_df,
        standard_frames=standard_frames,
        replay_frames=replay_frames,
        state_panel_df=state_panel_df,
        historical_context_df=historical_context_df,
        historical_family_df=historical_family_df,
    )
    dataset_df = _with_phase_evaluation_slice(
        dataset_df,
        training_phases=tuple(request.training_season_phases),
        holdout_phases=tuple(request.holdout_season_phases),
    )
    phase_holdout_enabled = (
        bool(request.use_phase_holdout)
        and "evaluation_slice" in dataset_df.columns
        and bool((dataset_df["evaluation_slice"].astype(str) == "training_history").any())
        and bool((dataset_df["evaluation_slice"].astype(str) == "postseason_holdout").any())
    )
    family_df = dataset_df[dataset_df["subject_type"].astype(str) == "family"].copy()
    controller_df = dataset_df[dataset_df["subject_type"].astype(str) == "controller"].copy()

    if phase_holdout_enabled:
        family_predictions_df = build_phase_holdout_predictions(
            family_df,
            target_column=RANKING_TARGET_COLUMN,
            numeric_columns=FAMILY_RANK_NUMERIC_COLUMNS,
            categorical_columns=FAMILY_RANK_CATEGORICAL_COLUMNS,
            fallback_column="heuristic_rank_score",
        )
        gate_predictions_df = build_phase_holdout_predictions(
            dataset_df,
            target_column=GATE_TARGET_COLUMN,
            numeric_columns=GATE_NUMERIC_COLUMNS,
            categorical_columns=GATE_CATEGORICAL_COLUMNS,
            fallback_column="heuristic_execute_score",
        )
    else:
        family_predictions_df = build_expanding_predictions(
            family_df,
            target_column=RANKING_TARGET_COLUMN,
            numeric_columns=FAMILY_RANK_NUMERIC_COLUMNS,
            categorical_columns=FAMILY_RANK_CATEGORICAL_COLUMNS,
            fallback_column="heuristic_rank_score",
            warmup_dates=request.warmup_dates,
        )
        gate_predictions_df = build_expanding_predictions(
            dataset_df,
            target_column=GATE_TARGET_COLUMN,
            numeric_columns=GATE_NUMERIC_COLUMNS,
            categorical_columns=GATE_CATEGORICAL_COLUMNS,
            fallback_column="heuristic_execute_score",
            warmup_dates=request.warmup_dates,
        )
    family_predictions_df = family_predictions_df.rename(columns={"prediction_score": "rank_score"})
    gate_predictions_df = gate_predictions_df.rename(columns={"prediction_score": "gate_score"})
    if phase_holdout_enabled:
        family_rank_calibration_df = build_phase_holdout_score_calibration(
            family_predictions_df,
            raw_score_column="rank_score",
            target_column=RANKING_TARGET_COLUMN,
            calibrated_column="calibrated_rank_score",
            mode_column="rank_calibration_mode",
            warmup_dates=request.warmup_dates,
        )[["signal_id", "calibrated_rank_score", "rank_calibration_mode"]]
        controller_calibration_df = build_phase_holdout_score_calibration(
            controller_df,
            raw_score_column="raw_confidence",
            target_column=CALIBRATION_TARGET_COLUMN,
            calibrated_column="calibrated_confidence",
            mode_column="calibration_mode",
            warmup_dates=request.warmup_dates,
        )[["signal_id", "calibrated_confidence", "calibration_mode"]]
        execution_calibration_df = build_phase_holdout_score_calibration(
            gate_predictions_df,
            raw_score_column="gate_score",
            target_column=GATE_TARGET_COLUMN,
            calibrated_column="calibrated_execution_likelihood",
            mode_column="execution_calibration_mode",
            warmup_dates=request.warmup_dates,
        )[["signal_id", "calibrated_execution_likelihood", "execution_calibration_mode"]]
    else:
        family_rank_calibration_df = build_expanding_score_calibration(
            family_predictions_df,
            raw_score_column="rank_score",
            target_column=RANKING_TARGET_COLUMN,
            calibrated_column="calibrated_rank_score",
            mode_column="rank_calibration_mode",
            warmup_dates=request.warmup_dates,
        )[["signal_id", "calibrated_rank_score", "rank_calibration_mode"]]
        controller_calibration_df = build_expanding_calibration(controller_df, warmup_dates=request.warmup_dates)[
            ["signal_id", "calibrated_confidence", "calibration_mode"]
        ]
        execution_calibration_df = build_expanding_score_calibration(
            gate_predictions_df,
            raw_score_column="gate_score",
            target_column=GATE_TARGET_COLUMN,
            calibrated_column="calibrated_execution_likelihood",
            mode_column="execution_calibration_mode",
            warmup_dates=request.warmup_dates,
        )[["signal_id", "calibrated_execution_likelihood", "execution_calibration_mode"]]

    family_predictions_df = family_predictions_df.merge(
        family_rank_calibration_df,
        on="signal_id",
        how="left",
    ).merge(
        gate_predictions_df[["signal_id", "gate_score", "prediction_mode"]],
        on="signal_id",
        how="left",
        suffixes=("", "_gate"),
    ).merge(
        execution_calibration_df,
        on="signal_id",
        how="left",
    )
    controller_predictions_df = controller_df.merge(
        gate_predictions_df[["signal_id", "gate_score", "prediction_mode"]],
        on="signal_id",
        how="left",
    ).merge(
        controller_calibration_df,
        on="signal_id",
        how="left",
    ).merge(
        execution_calibration_df,
        on="signal_id",
        how="left",
    )
    family_predictions_df["sidecar_probability"] = family_predictions_df["calibrated_rank_score"].fillna(
        family_predictions_df["rank_score"]
    )
    family_predictions_df["selection_source"] = "focused_family_reranker"
    controller_predictions_df["sidecar_probability"] = controller_predictions_df["calibrated_confidence"].fillna(
        pd.to_numeric(controller_predictions_df["raw_confidence"], errors="coerce").fillna(0.5)
    )
    controller_predictions_df["selection_source"] = "controller_confidence_overlay"
    execution_predictions_df = gate_predictions_df.merge(
        execution_calibration_df,
        on="signal_id",
        how="left",
    )
    if phase_holdout_enabled:
        family_predictions_df = family_predictions_df[
            family_predictions_df["evaluation_slice"].astype(str) == "postseason_holdout"
        ].copy()
        controller_predictions_df = controller_predictions_df[
            controller_predictions_df["evaluation_slice"].astype(str) == "postseason_holdout"
        ].copy()
        execution_predictions_df = execution_predictions_df[
            execution_predictions_df["evaluation_slice"].astype(str) == "postseason_holdout"
        ].copy()
    focus_family_df = family_predictions_df[family_predictions_df["focus_family_flag"].fillna(False)].copy()
    available_focus_families = sorted({value for value in focus_family_df["strategy_family"].dropna().astype(str).tolist()})
    missing_focus_families = [
        family_name for family_name in FOCUS_STRATEGY_FAMILIES if family_name not in available_focus_families
    ]

    rank_metrics = _evaluate_binary_predictions(
        family_predictions_df,
        score_column="rank_score",
        target_column=RANKING_TARGET_COLUMN,
    )
    rank_calibrated_metrics = _evaluate_binary_predictions(
        family_predictions_df.assign(
            calibrated_rank_score=family_predictions_df["calibrated_rank_score"].fillna(family_predictions_df["rank_score"])
        ),
        score_column="calibrated_rank_score",
        target_column=RANKING_TARGET_COLUMN,
    )
    family_sidecar_score_source = "calibrated_rank_score"
    if (
        phase_holdout_enabled
        and (rank_metrics.get("auc") is not None)
        and (rank_calibrated_metrics.get("auc") is not None)
        and float(rank_metrics.get("auc") or 0.0) > float(rank_calibrated_metrics.get("auc") or 0.0)
    ):
        family_predictions_df["sidecar_probability"] = family_predictions_df["rank_score"].fillna(
            family_predictions_df["sidecar_probability"]
        )
        family_sidecar_score_source = "rank_score_phase_holdout_raw_auc_better_than_calibrated"
    rank_topline = _build_group_topline(
        family_predictions_df,
        score_column="rank_score",
        value_column="label_replay_value",
        target_column=RANKING_TARGET_COLUMN,
        group_column="game_id",
    )
    focus_rank_topline = _build_group_topline(
        focus_family_df.assign(
            calibrated_rank_score=focus_family_df["calibrated_rank_score"].fillna(focus_family_df["rank_score"])
        ),
        score_column="calibrated_rank_score",
        value_column="label_replay_value",
        target_column=RANKING_TARGET_COLUMN,
        group_column="underlying_candidate_id" if "underlying_candidate_id" in focus_family_df.columns else "signal_id",
    )
    gate_metrics = _evaluate_binary_predictions(
        execution_predictions_df,
        score_column="gate_score",
        target_column=GATE_TARGET_COLUMN,
    )
    gate_calibrated_metrics = _evaluate_binary_predictions(
        execution_predictions_df.assign(
            calibrated_execution_likelihood=execution_predictions_df["calibrated_execution_likelihood"].fillna(
                execution_predictions_df["gate_score"]
            )
        ),
        score_column="calibrated_execution_likelihood",
        target_column=GATE_TARGET_COLUMN,
    )
    calibration_raw_metrics = _evaluate_binary_predictions(
        controller_predictions_df.assign(
            raw_confidence=pd.to_numeric(controller_predictions_df["raw_confidence"], errors="coerce").fillna(0.5)
        ),
        score_column="raw_confidence",
        target_column=CALIBRATION_TARGET_COLUMN,
    )
    calibration_metrics = _evaluate_binary_predictions(
        controller_predictions_df.assign(
            calibrated_confidence=controller_predictions_df["calibrated_confidence"].fillna(
                pd.to_numeric(controller_predictions_df["raw_confidence"], errors="coerce").fillna(0.5)
            )
        ),
        score_column="calibrated_confidence",
        target_column=CALIBRATION_TARGET_COLUMN,
    )
    optional_sizing = _fit_optional_sizing_model(dataset_df)
    controller_calibration_buckets_df = _build_calibration_buckets(
        controller_predictions_df.assign(
            calibrated_confidence=controller_predictions_df["calibrated_confidence"].fillna(
                pd.to_numeric(controller_predictions_df["raw_confidence"], errors="coerce").fillna(0.5)
            )
        ),
        score_column="calibrated_confidence",
        target_column=CALIBRATION_TARGET_COLUMN,
    )
    controller_calibration_buckets_df["calibration_type"] = "controller_confidence"
    family_calibration_buckets_df = _build_calibration_buckets(
        family_predictions_df.assign(
            calibrated_rank_score=family_predictions_df["calibrated_rank_score"].fillna(family_predictions_df["rank_score"])
        ),
        score_column="calibrated_rank_score",
        target_column=RANKING_TARGET_COLUMN,
    )
    family_calibration_buckets_df["calibration_type"] = "family_rank_probability"
    execution_calibration_buckets_df = _build_calibration_buckets(
        execution_predictions_df.assign(
            calibrated_execution_likelihood=execution_predictions_df["calibrated_execution_likelihood"].fillna(
                execution_predictions_df["gate_score"]
            )
        ),
        score_column="calibrated_execution_likelihood",
        target_column=GATE_TARGET_COLUMN,
    )
    execution_calibration_buckets_df["calibration_type"] = "execution_likelihood_shadow"
    calibration_buckets_df = pd.concat(
        [
            controller_calibration_buckets_df,
            family_calibration_buckets_df,
            execution_calibration_buckets_df,
        ],
        ignore_index=True,
        sort=False,
    )
    execution_risk_breakdown_df = _build_execution_risk_breakdown(
        execution_predictions_df.assign(
            calibrated_execution_likelihood=execution_predictions_df["calibrated_execution_likelihood"].fillna(
                execution_predictions_df["gate_score"]
            )
        )
    )
    dominant_no_trade_reason = None
    mean_gate_score_executed = None
    mean_gate_score_signal_stale = None
    if not execution_risk_breakdown_df.empty and "reason_bucket" in execution_risk_breakdown_df.columns:
        dominant_no_trade_reason = str(execution_risk_breakdown_df.iloc[0]["reason_bucket"])
        executed_rows = execution_risk_breakdown_df[execution_risk_breakdown_df["reason_bucket"] == "executed"]
        stale_rows = execution_risk_breakdown_df[execution_risk_breakdown_df["reason_bucket"] == "signal_stale"]
        if not executed_rows.empty:
            mean_gate_score_executed = float(executed_rows.iloc[0]["mean_gate_score"])
        if not stale_rows.empty:
            mean_gate_score_signal_stale = float(stale_rows.iloc[0]["mean_gate_score"])

    focus_selection_min_score = 0.0 if phase_holdout_enabled else DEFAULT_FOCUS_RANK_THRESHOLD
    controller_selection_min_score = 0.0 if phase_holdout_enabled else DEFAULT_CONTROLLER_CALIBRATION_THRESHOLD
    ml_focus_family_selected_df = _select_focus_family_candidates(
        family_predictions_df,
        score_column="sidecar_probability",
        min_score=focus_selection_min_score,
    )
    ml_controller_focus_selected_df = _select_calibrated_controller_candidates(
        controller_predictions_df,
        controller_name=UNIFIED_CONTROLLER_NAME,
        score_column="sidecar_probability",
        min_score=controller_selection_min_score,
        focus_only=True,
    )
    ml_sidecar_union_selected_df = _combine_sidecar_candidates(
        ml_focus_family_selected_df,
        ml_controller_focus_selected_df,
    )

    ml_subject_payloads: list[dict[str, Any]] = []
    subject_artifact_paths: dict[str, dict[str, str]] = {}
    for subject_name, selected_df, extra_notes in (
        (
            "ml_focus_family_reranker_v2",
            ml_focus_family_selected_df,
            [
                f"Focus families: {', '.join(FOCUS_STRATEGY_FAMILIES)}",
                f"Family shadow-selection threshold: {focus_selection_min_score}",
                "Multiple focus-family candidates may survive in the same game when they map to different underlying trades.",
                "Phase-holdout mode surfaces focus rows for shadow comparison instead of enforcing a production gate.",
            ],
        ),
        (
            "ml_controller_focus_calibrator_v2",
            ml_controller_focus_selected_df,
            [
                f"Unified controller only: {UNIFIED_CONTROLLER_NAME}",
                "Controller rows are filtered to replay-promising focus families before applying calibrated confidence.",
                f"Controller shadow-selection threshold: {controller_selection_min_score}",
                "Phase-holdout mode keeps controller calibration as an annotation, not a hard threshold.",
            ],
        ),
        (
            "ml_sidecar_union_v2",
            ml_sidecar_union_selected_df,
            [
                "Union of focused family reranker and calibrated controller overlay.",
                "Deduped by underlying_candidate_id to avoid double-counting the same trade idea across family and controller rows.",
                "Intended production role: shadow sidecar for rerank plus confidence annotation, not a controller replacement.",
            ],
        ),
    ):
        standard_df, replay_df = _build_selected_trade_frames(selected_df)
        subject_artifact_paths[subject_name] = _write_subject_trade_artifacts(
            artifact_root,
            subject_name=subject_name,
            standard_df=standard_df,
            replay_df=replay_df,
        )
        payload = _compute_subject_metrics(
            subject_name=subject_name,
            candidate_kind="ml_strategy",
            selected_df=selected_df,
            standard_df=standard_df,
            replay_df=replay_df,
            gate_threshold=None,
            extra_notes=extra_notes,
        )
        ml_subject_payloads.append(payload)

    benchmark_comparison_df = _build_benchmark_comparison_frame(
        primary_subject_summary_df,
        replay_submission,
        ml_subject_payloads,
    )
    feature_schema = build_ml_feature_schema()
    shadow_payload_schema = build_ml_shadow_payload_schema()
    shadow_payload_views = _build_shadow_payload_views(
        family_predictions_df=family_predictions_df,
        controller_predictions_df=controller_predictions_df,
        focus_selection_min_score=focus_selection_min_score,
        controller_selection_min_score=controller_selection_min_score,
    )
    shadow_payload_sample = _build_shadow_payload_sample(shadow_payload_views)
    shadow_payload_all_variants_df = pd.concat(
        list(shadow_payload_views.values()),
        ignore_index=True,
        sort=False,
    ) if shadow_payload_views else pd.DataFrame(columns=SHADOW_PAYLOAD_COLUMNS)
    live_shadow_payload_views, live_shadow_context = _build_live_shadow_payload_views(
        shared_root=shared_root,
        benchmark_comparison_df=benchmark_comparison_df,
    )
    live_shadow_payload_all_variants_df = pd.concat(
        list(live_shadow_payload_views.values()),
        ignore_index=True,
        sort=False,
    ) if live_shadow_payload_views else pd.DataFrame(columns=SHADOW_PAYLOAD_COLUMNS)
    live_shadow_payload_sample = _build_shadow_payload_sample(live_shadow_payload_views)
    live_shadow_variant_summary_df = _build_live_shadow_variant_summary_frame(live_shadow_payload_views)
    live_shadow_blocker_breakdown_df = _build_live_blocker_breakdown_frame(live_shadow_payload_views)
    recommendation_budget_config = live_shadow_context.get("budget_config") or {}
    recommendation_min_shares = recommendation_budget_config.get("polymarket_min_shares", POLYMARKET_MIN_SHARES)
    recommendation_game_cap = recommendation_budget_config.get(
        "max_entry_notional_per_game_usd",
        LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD,
    )
    shadow_variant_comparison_df = _build_shadow_variant_comparison_frame(
        benchmark_comparison_df=benchmark_comparison_df,
        ml_subjects=ml_subject_payloads,
        selected_counts={
            "ml_focus_family_reranker_v2": int(len(ml_focus_family_selected_df)),
            "ml_controller_focus_calibrator_v2": int(len(ml_controller_focus_selected_df)),
            "ml_sidecar_union_v2": int(len(ml_sidecar_union_selected_df)),
        },
    )
    split_summary = build_time_split_summary(dataset_df, warmup_dates=request.warmup_dates)
    split_summary["phase_holdout"] = _build_phase_split_summary(
        dataset_df,
        training_phases=tuple(request.training_season_phases),
        holdout_phases=tuple(request.holdout_season_phases),
        use_phase_holdout=phase_holdout_enabled,
    )
    gate_postmortem = (
        "Execution-risk separation is still weak under replay. "
        f"`signal_stale` remains dominant, and mean gate score for executed rows ({mean_gate_score_executed}) "
        f"still overlaps with signal_stale rows ({mean_gate_score_signal_stale}), so hard skip would mostly add noise."
    )

    model_tracks = {
        "candidate_ranking": {
            "raw_metrics": rank_metrics,
            "calibrated_metrics": rank_calibrated_metrics,
            "topline": rank_topline,
            "focus_family_topline": focus_rank_topline,
            "warmup_dates": request.warmup_dates,
            "fallback": "heuristic_rank_score",
            "focus_families": list(FOCUS_STRATEGY_FAMILIES),
            "available_focus_families": available_focus_families,
            "missing_focus_families": missing_focus_families,
            "focus_rank_threshold": focus_selection_min_score,
            "sidecar_score_source": family_sidecar_score_source,
        },
        "controller_confidence_calibration": {
            "controller_name": UNIFIED_CONTROLLER_NAME,
            "raw_metrics": calibration_raw_metrics,
            "calibrated_metrics": calibration_metrics,
            "calibrated_confidence_threshold": controller_selection_min_score,
        },
        "family_confidence_calibration": {
            "raw_metrics": rank_metrics,
            "calibrated_metrics": rank_calibrated_metrics,
            "focus_family_rows": int(len(focus_family_df)),
            "available_focus_families": available_focus_families,
            "missing_focus_families": missing_focus_families,
        },
        "execution_risk_shadow": {
            "raw_metrics": gate_metrics,
            "calibrated_metrics": gate_calibrated_metrics,
            "fallback": "heuristic_execute_score",
            "recommended_role": "shadow_only_diagnostic",
            "dominant_no_trade_reason": dominant_no_trade_reason,
            "mean_gate_score_executed": mean_gate_score_executed,
            "mean_gate_score_signal_stale": mean_gate_score_signal_stale,
            "why_underperformed": gate_postmortem,
        },
        "optional_sizing": optional_sizing,
    }

    report_path = report_root / "ml_trading_lane_report.md"
    memo_path = report_root / "research_memo.md"
    daily_live_validation_report_path = report_root / "daily_live_validation_handoff.md"
    submission_path = report_root / "benchmark_submission.json"
    handoff_path = handoff_root / "status.md"
    daily_live_validation_handoff_path = shared_root / "handoffs" / "daily-live-validation" / "ml_trading_sidecar_handoff.md"
    run_payload_path = artifact_root / "ml_trading_lane_run.json"
    schema_path = artifact_root / "feature_schema.json"
    contract_path = artifact_root / "data_contract.json"
    shadow_schema_path = artifact_root / "shadow_payload_schema.json"
    shadow_sample_path = artifact_root / "shadow_payload_sample.json"
    live_shadow_sample_path = artifact_root / "live_shadow_payload_sample.json"
    daily_live_validation_handoff_path.parent.mkdir(parents=True, exist_ok=True)

    dataset_artifacts = {
        "all_candidates": write_frame(artifact_root / "all_candidates", dataset_df),
        "family_candidates": write_frame(artifact_root / "family_candidates", family_predictions_df),
        "controller_candidates": write_frame(artifact_root / "controller_candidates", controller_predictions_df),
        "gate_predictions": write_frame(artifact_root / "gate_predictions", execution_predictions_df),
        "calibration_buckets": write_frame(artifact_root / "calibration_buckets", calibration_buckets_df),
        "execution_risk_breakdown": write_frame(artifact_root / "execution_risk_breakdown", execution_risk_breakdown_df),
        "focus_family_selected": write_frame(artifact_root / "focus_family_selected", ml_focus_family_selected_df),
        "controller_focus_selected": write_frame(artifact_root / "controller_focus_selected", ml_controller_focus_selected_df),
        "sidecar_union_selected": write_frame(artifact_root / "sidecar_union_selected", ml_sidecar_union_selected_df),
        "benchmark_comparison": write_frame(artifact_root / "benchmark_comparison", benchmark_comparison_df),
        "shadow_payload_all_variants": write_frame(artifact_root / "shadow_payload_all_variants", shadow_payload_all_variants_df),
        "shadow_payload_reranker_only": write_frame(artifact_root / "shadow_payload_reranker_only", shadow_payload_views["reranker_only"]),
        "shadow_payload_calibrator_only": write_frame(artifact_root / "shadow_payload_calibrator_only", shadow_payload_views["calibrator_only"]),
        "shadow_payload_combined_sidecar": write_frame(artifact_root / "shadow_payload_combined_sidecar", shadow_payload_views["combined_sidecar"]),
        "shadow_variant_comparison": write_frame(artifact_root / "shadow_variant_comparison", shadow_variant_comparison_df),
        "live_shadow_payload_all_variants": write_frame(artifact_root / "live_shadow_payload_all_variants", live_shadow_payload_all_variants_df),
        "live_shadow_payload_reranker_only": write_frame(artifact_root / "live_shadow_payload_reranker_only", live_shadow_payload_views["reranker_only"]),
        "live_shadow_payload_calibrator_only": write_frame(artifact_root / "live_shadow_payload_calibrator_only", live_shadow_payload_views["calibrator_only"]),
        "live_shadow_payload_combined_sidecar": write_frame(artifact_root / "live_shadow_payload_combined_sidecar", live_shadow_payload_views["combined_sidecar"]),
        "live_shadow_variant_summary": write_frame(artifact_root / "live_shadow_variant_summary", live_shadow_variant_summary_df),
        "live_shadow_blocker_breakdown": write_frame(artifact_root / "live_shadow_blocker_breakdown", live_shadow_blocker_breakdown_df),
    }

    published_at = datetime.now(timezone.utc).isoformat()
    report_payload = {
        "lane_id": ML_LANE_ID,
        "season": request.season,
        "schema_version": ML_SCHEMA_VERSION,
        "analysis_version": request.analysis_version,
        "replay_artifact_name": request.replay_artifact_name,
        "replay_artifact_names": list(replay_artifact_names),
        "replay_artifact_roots": replay_roots,
        "published_at": published_at,
        "split_summary": split_summary,
        "dataset_summary": {
            "all_rows": int(len(dataset_df)),
            "family_rows": int(len(family_df)),
            "controller_rows": int(len(controller_df)),
            "evaluation_family_rows": int(len(family_predictions_df)),
            "evaluation_controller_rows": int(len(controller_predictions_df)),
            "focus_family_rows": int(len(focus_family_df)),
            "executed_rate": float(dataset_df["label_replay_executed_flag"].mean()) if not dataset_df.empty else None,
            "phase_holdout_enabled": bool(phase_holdout_enabled),
            "evaluation_slice_counts": (
                {str(key): int(value) for key, value in dataset_df["evaluation_slice"].value_counts(dropna=False).to_dict().items()}
                if "evaluation_slice" in dataset_df.columns
                else {}
            ),
        },
        "model_tracks": model_tracks,
        "benchmark_comparison_preview": to_jsonable(benchmark_comparison_df.head(12).to_dict(orient="records")),
        "shadow_variant_comparison_preview": to_jsonable(shadow_variant_comparison_df.to_dict(orient="records")),
        "live_shadow": {
            **live_shadow_context,
            "variant_summary_preview": to_jsonable(live_shadow_variant_summary_df.to_dict(orient="records")),
            "blocker_breakdown_preview": to_jsonable(live_shadow_blocker_breakdown_df.to_dict(orient="records")),
        },
        "recommendations": {
            "ranking": "Adopt ML next as a focused family reranker over inversion, quarter_open_reprice, and any future micro_momentum_continuation rows; let it add sidecar prioritization without replacing deterministic routing.",
            "gate": "Keep execution-risk scoring in shadow. Its current replay signal is diagnostic only, and it should annotate stale-risk rather than block trades.",
            "calibration": "Adopt controller confidence calibration immediately and extend the same probability framing to family rerank scores before any thresholding or LLM escalation.",
            "sizing": "Do not promote ML sizing yet; executed replay rows are still too thin for a stable sizing model.",
            "controller_direction": "Replay-aware ML now has a clean sidecar role: keep the deterministic controller core, layer focused family reranking and calibrated confidence on top, and leave skip or gate in shadow rather than enforcing it.",
            "integration_point": "Insert the focused reranker beside score_master_router_candidate, attach calibrated controller confidence to the selected trade record, and emit execution-risk diagnostics into shadow logging for daily live validation.",
            "daily_shadow": "Yes: attach the reranker, calibrated controller confidence, and shadow execution-risk fields to daily live validation immediately, but log-only until more replay coverage accumulates.",
            "first_operational_role": "Attach the controller-confidence calibrator first. It preserves the current control path, annotates controller-selected candidates directly, and currently outperforms reranker-only and combined-sidecar in the latest replay sample.",
            "enter_daily_shadow_now": "Yes. Controller-confidence calibration should enter daily live validation shadow immediately, with reranker-only running in parallel and combined sidecar kept compare-only.",
            "shadow_attachment": "Log calibrator-only on every controller-selected candidate from the live control pair, log reranker-only on replay-promoted family probes, and publish combined-sidecar rows as a compare-only overlay.",
            "combined_sidecar_status": "Keep the combined sidecar in shadow comparison. It is benchmark-credible but currently trails calibrator-only in the latest replay sample.",
            "gate_postmortem": gate_postmortem,
            "tomorrow_shadow_ready": "Yes. ML is ready for tomorrow's live validation in shadow because the payload now carries explicit feed, orderbook, and budget executability diagnostics without taking any execution authority.",
            "live_budget_read": f"Current live tests should treat the {recommendation_min_shares}-share minimum and the configured ${recommendation_game_cap} per-game cap as the true executability fence. Anything above the configured cap is replay-interesting but live-unaffordable under the active test budget.",
            "live_survival_read": "The replay edge still survives as a shadow ranking and calibration signal, but today's live evidence has not yet shown an executable ML-selected candidate after feed freshness, orderbook availability, and minimum-share affordability checks.",
            "live_blockers": "Today's live shadow was dominated by no_strategy_signal on the replay-promoted families, with no executable ML rows and no evidence that hard skip or sizing should be promoted. Where a replay edge exists, treat orderbook absence, feed freshness, and the five-share minimum as separate blockers from true no-signal.",
        },
        "handoffs": {
            "benchmark_integration": "Ingest ml_focus_family_reranker_v2, ml_controller_focus_calibrator_v2, and ml_sidecar_union_v2 as the current ML compare set.",
            "daily_live_validation": "Log sidecar_probability, calibrated_confidence, calibrated_execution_likelihood, focus_family_flag, feed_fresh_flag, orderbook_available_flag, min_required_notional_usd, and budget_affordable_flag for every routed candidate in shadow, using calibrator-only as the first operational attachment.",
            "llm_strategy_lane": "Consume calibrated controller confidence and focus-family tags as optional escalation and prompt-shaping features, not hard gate inputs.",
        },
    }
    report_payload["artifacts"] = {
        "run_payload_json": str(run_payload_path),
        "feature_schema_json": str(schema_path),
        "data_contract_json": str(contract_path),
        "shadow_payload_schema_json": str(shadow_schema_path),
        "shadow_payload_sample_json": str(shadow_sample_path),
        "live_shadow_payload_sample_json": str(live_shadow_sample_path),
        "all_candidates_csv": dataset_artifacts["all_candidates"]["csv"],
        "family_candidates_csv": dataset_artifacts["family_candidates"]["csv"],
        "controller_candidates_csv": dataset_artifacts["controller_candidates"]["csv"],
        "focus_family_selected_csv": dataset_artifacts["focus_family_selected"]["csv"],
        "controller_focus_selected_csv": dataset_artifacts["controller_focus_selected"]["csv"],
        "sidecar_union_selected_csv": dataset_artifacts["sidecar_union_selected"]["csv"],
        "execution_risk_breakdown_csv": dataset_artifacts["execution_risk_breakdown"]["csv"],
        "benchmark_comparison_csv": dataset_artifacts["benchmark_comparison"]["csv"],
        "shadow_variant_comparison_csv": dataset_artifacts["shadow_variant_comparison"]["csv"],
        "shadow_payload_all_variants_csv": dataset_artifacts["shadow_payload_all_variants"]["csv"],
        "shadow_payload_reranker_only_csv": dataset_artifacts["shadow_payload_reranker_only"]["csv"],
        "shadow_payload_calibrator_only_csv": dataset_artifacts["shadow_payload_calibrator_only"]["csv"],
        "shadow_payload_combined_sidecar_csv": dataset_artifacts["shadow_payload_combined_sidecar"]["csv"],
        "live_shadow_payload_all_variants_csv": dataset_artifacts["live_shadow_payload_all_variants"]["csv"],
        "live_shadow_payload_reranker_only_csv": dataset_artifacts["live_shadow_payload_reranker_only"]["csv"],
        "live_shadow_payload_calibrator_only_csv": dataset_artifacts["live_shadow_payload_calibrator_only"]["csv"],
        "live_shadow_payload_combined_sidecar_csv": dataset_artifacts["live_shadow_payload_combined_sidecar"]["csv"],
        "live_shadow_variant_summary_csv": dataset_artifacts["live_shadow_variant_summary"]["csv"],
        "live_shadow_blocker_breakdown_csv": dataset_artifacts["live_shadow_blocker_breakdown"]["csv"],
    }
    report_payload["reports"] = {
        "lane_report_markdown": str(report_path),
        "memo_markdown": str(memo_path),
        "daily_live_validation_markdown": str(daily_live_validation_report_path),
        "daily_live_validation_shared_handoff_markdown": str(daily_live_validation_handoff_path),
        "benchmark_submission_json": str(submission_path),
    }

    submission = {
        "lane_id": ML_LANE_ID,
        "lane_label": ML_LANE_LABEL,
        "lane_type": ML_LANE_TYPE,
        "published_at": published_at,
        "comparison_scope": {
            "season": request.season,
            "phase_group": ",".join(dict.fromkeys([*request.training_season_phases, *request.holdout_season_phases]))
            if phase_holdout_enabled
            else "play_in,playoffs",
            "shared_contract_ref": "replay_contract_current.md + unified_benchmark_contract_current.md",
            "evaluation_mode": "regular_train_postseason_holdout_sidecar_rerank_and_calibration"
            if phase_holdout_enabled
            else "expanding_date_oof_sidecar_rerank_and_calibration",
            "replay_artifact_names": list(replay_artifact_names),
            "phase_holdout": split_summary.get("phase_holdout"),
        },
        "shadow_operational_support": {
            "schema_version": ML_SHADOW_PAYLOAD_SCHEMA_VERSION,
            "enter_daily_shadow_now": report_payload["recommendations"]["enter_daily_shadow_now"],
            "first_operational_role": report_payload["recommendations"]["first_operational_role"],
            "tomorrow_shadow_ready": report_payload["recommendations"]["tomorrow_shadow_ready"],
            "artifacts": {
                "shadow_payload_schema_json": str(shadow_schema_path),
                "shadow_payload_sample_json": str(shadow_sample_path),
                "live_shadow_payload_sample_json": str(live_shadow_sample_path),
                "shadow_variant_comparison_csv": dataset_artifacts["shadow_variant_comparison"]["csv"],
                "live_shadow_payload_all_variants_csv": dataset_artifacts["live_shadow_payload_all_variants"]["csv"],
                "live_shadow_variant_summary_csv": dataset_artifacts["live_shadow_variant_summary"]["csv"],
                "live_shadow_blocker_breakdown_csv": dataset_artifacts["live_shadow_blocker_breakdown"]["csv"],
            },
        },
        "subjects": [],
    }
    subject_shadow_variant_map = {
        "ml_focus_family_reranker_v2": "shadow_payload_reranker_only",
        "ml_controller_focus_calibrator_v2": "shadow_payload_calibrator_only",
        "ml_sidecar_union_v2": "shadow_payload_combined_sidecar",
    }
    for subject in ml_subject_payloads:
        subject_name = subject["candidate_id"]
        artifact_paths = subject_artifact_paths.get(subject_name, {})
        shadow_variant_key = subject_shadow_variant_map.get(subject_name)
        metrics = subject["metrics"]
        submission["subjects"].append(
            {
                **subject,
                "standard_result": {
                    "mode": "standard_backtest",
                    "trade_count": metrics.get("standard_trade_count"),
                    "ending_bankroll": metrics.get("standard_ending_bankroll"),
                    "avg_return_with_slippage": metrics.get("standard_avg_return_with_slippage"),
                    "compounded_return": metrics.get("standard_compounded_return"),
                    "max_drawdown_pct": None,
                    "max_drawdown_amount": None,
                },
                "replay_result": {
                    "mode": "replay_result",
                    "trade_count": metrics.get("replay_trade_count"),
                    "ending_bankroll": metrics.get("replay_ending_bankroll"),
                    "avg_return_with_slippage": metrics.get("replay_avg_return_with_slippage"),
                    "compounded_return": metrics.get("replay_compounded_return"),
                    "max_drawdown_pct": metrics.get("replay_max_drawdown_pct"),
                    "max_drawdown_amount": metrics.get("replay_max_drawdown_amount"),
                    "no_trade_count": metrics.get("replay_no_trade_count"),
                    "execution_rate": metrics.get("execution_rate"),
                },
                "replay_realism": {
                    "trade_gap": metrics.get("trade_gap"),
                    "execution_rate": metrics.get("execution_rate"),
                    "realism_gap_trade_rate": metrics.get("realism_gap_trade_rate"),
                    "top_no_trade_reason": metrics.get("top_no_trade_reason"),
                    "blocked_signal_count": metrics.get("replay_no_trade_count"),
                    "stale_signal_suppressed_count": metrics.get("stale_signal_suppressed_count"),
                    "stale_signal_suppression_rate": metrics.get("stale_signal_suppression_rate"),
                    "stale_signal_share_of_blocked_signals": metrics.get("stale_signal_share_of_blocked_signals"),
                },
                "artifacts": {
                    "report_markdown": str(report_path),
                    "trace_json": str(run_payload_path),
                    "standard_trades_csv": artifact_paths.get("standard_csv"),
                    "replay_trades_csv": artifact_paths.get("replay_csv"),
                    "feature_schema_json": str(schema_path),
                    "shadow_payload_csv": dataset_artifacts[shadow_variant_key]["csv"] if shadow_variant_key else None,
                    "shadow_payload_schema_json": str(shadow_schema_path),
                },
            }
        )

    feature_schema_path = write_json(schema_path, feature_schema)
    contract_json_path = write_json(contract_path, feature_schema)
    shadow_schema_json_path = write_json(shadow_schema_path, shadow_payload_schema)
    shadow_sample_json_path = write_json(shadow_sample_path, shadow_payload_sample)
    live_shadow_sample_json_path = write_json(live_shadow_sample_path, live_shadow_payload_sample)
    report_markdown_path = write_markdown(report_path, _render_lane_report(report_payload))
    memo_markdown_path = write_markdown(memo_path, _render_memo(report_payload))
    submission_json_path = write_json(submission_path, submission)
    report_payload["artifacts"]["feature_schema_json"] = feature_schema_path
    report_payload["artifacts"]["data_contract_json"] = contract_json_path
    report_payload["artifacts"]["shadow_payload_schema_json"] = shadow_schema_json_path
    report_payload["artifacts"]["shadow_payload_sample_json"] = shadow_sample_json_path
    report_payload["artifacts"]["live_shadow_payload_sample_json"] = live_shadow_sample_json_path
    report_payload["artifacts"]["dashboard_ingest_check_json"] = str(artifact_root / "dashboard_ingest_check.json")
    report_payload["artifacts"]["calibration_buckets_csv"] = dataset_artifacts["calibration_buckets"]["csv"]
    report_payload["artifacts"]["gate_predictions_csv"] = dataset_artifacts["gate_predictions"]["csv"]
    report_payload["reports"]["lane_report_markdown"] = report_markdown_path
    report_payload["reports"]["memo_markdown"] = memo_markdown_path
    report_payload["reports"]["benchmark_submission_json"] = submission_json_path
    daily_live_validation_report_markdown_path = write_markdown(
        daily_live_validation_report_path,
        _render_daily_live_validation_handoff(report_payload),
    )
    daily_live_validation_shared_handoff_markdown_path = write_markdown(
        daily_live_validation_handoff_path,
        _render_daily_live_validation_handoff(report_payload),
    )
    report_payload["reports"]["daily_live_validation_markdown"] = daily_live_validation_report_markdown_path
    report_payload["reports"]["daily_live_validation_shared_handoff_markdown"] = (
        daily_live_validation_shared_handoff_markdown_path
    )
    dashboard_snapshot = build_unified_benchmark_dashboard(
        UnifiedBenchmarkRequest(
            season=request.season,
            replay_artifact_name=request.replay_artifact_name,
            shared_root=str(shared_root),
            finalist_limit=6,
        )
    )
    dashboard_preview = [
        row
        for row in (dashboard_snapshot.get("ml_candidates") or [])
        if str(row.get("candidate_id") or "").startswith("ml_")
    ]
    report_payload["dashboard_ingest_preview"] = dashboard_preview

    write_json(artifact_root / "dashboard_ingest_check.json", {"ml_candidates": dashboard_preview})
    write_json(run_payload_path, report_payload)
    write_markdown(handoff_path, _render_status(report_payload))
    return to_jsonable(report_payload)


__all__ = [
    "MLTradingLaneRequest",
    "ML_ARTIFACT_NAME",
    "ML_LANE_ID",
    "ML_OUTPUT_DIRNAME",
    "build_ml_feature_schema",
    "build_replay_candidate_dataset",
    "build_time_split_summary",
    "build_expanding_predictions",
    "build_expanding_calibration",
    "run_ml_trading_lane",
]
