from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    PORTFOLIO_SCOPE_ROUTED,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.benchmark_integration import (
    UnifiedBenchmarkRequest,
    build_unified_benchmark_dashboard,
    resolve_default_shared_root,
)
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SEASON,
)


LLM_LANE_ID = "llm-strategy"
LLM_LANE_LABEL = "LLM strategy"
LLM_LANE_TYPE = "llm"
LLM_OUTPUT_DIRNAME = "llm-strategy-lane"
LLM_SCHEMA_VERSION = "llm_lane_v1"
LLM_ACTION_SCHEMA_VERSION = "llm_action_v2"
LLM_PROMPT_CONTRACT_VERSION = "llm_prompt_contract_v2"
LLM_SHADOW_PAYLOAD_VERSION = "llm_shadow_sidecar_v1"
LLM_ARTIFACT_NAME = "postseason_replay_llm_v1"
REPLAY_ARTIFACT_NAME = "postseason_execution_replay"
REPLAY_ENGINE_LANE = "replay-engine-hf"
ML_OUTPUT_DIRNAME = "ml-trading-lane"

INITIAL_BANKROLL = 10.0
POSITION_SIZE_FRACTION = 0.20
TARGET_EXPOSURE_FRACTION = 0.80
MAX_CONCURRENT_POSITIONS = 5
MIN_ORDER_DOLLARS = 1.0
MIN_SHARES = 5.0
RANDOM_SLIPPAGE_SEED = 20260424

_NUMERIC_GAME_ID_PATTERN = re.compile(r"^\d+$")
_PLUS_CENTS_PATTERN = re.compile(r"plus_(\d+)c")
_MINUS_CENTS_PATTERN = re.compile(r"minus_(\d+)c")

ARCHETYPE_QUARTER_OPEN = "quarter_open"
ARCHETYPE_HALFTIME_DISLOCATION = "halftime_dislocation"
ARCHETYPE_UNSTABLE_LEAD = "unstable_lead"
ARCHETYPE_PANIC_SWING = "panic_swing"
ARCHETYPE_ANOMALY = "anomalous_score_price_move"
ARCHETYPE_BROAD_WINNER = "broad_winner_drift"

ARCHETYPE_SEVERITY = {
    ARCHETYPE_QUARTER_OPEN: 0.82,
    ARCHETYPE_HALFTIME_DISLOCATION: 0.76,
    ARCHETYPE_UNSTABLE_LEAD: 0.88,
    ARCHETYPE_PANIC_SWING: 0.92,
    ARCHETYPE_ANOMALY: 0.89,
    ARCHETYPE_BROAD_WINNER: 0.38,
}

ARCHETYPE_FAMILY_PREFERENCES: dict[str, tuple[str, ...]] = {
    ARCHETYPE_QUARTER_OPEN: ("quarter_open_reprice", "micro_momentum_continuation", "inversion", "q1_repricing"),
    ARCHETYPE_HALFTIME_DISLOCATION: ("halftime_gap_fill", "inversion"),
    ARCHETYPE_UNSTABLE_LEAD: ("inversion", "micro_momentum_continuation", "q4_clutch"),
    ARCHETYPE_PANIC_SWING: ("panic_fade_fast", "inversion", "underdog_liftoff"),
    ARCHETYPE_ANOMALY: ("inversion", "micro_momentum_continuation", "panic_fade_fast"),
    ARCHETYPE_BROAD_WINNER: ("winner_definition", "inversion"),
}

FAMILY_FALLBACK_ARCHETYPE = {
    "quarter_open_reprice": ARCHETYPE_QUARTER_OPEN,
    "micro_momentum_continuation": ARCHETYPE_QUARTER_OPEN,
    "q1_repricing": ARCHETYPE_QUARTER_OPEN,
    "halftime_gap_fill": ARCHETYPE_HALFTIME_DISLOCATION,
    "lead_fragility": ARCHETYPE_UNSTABLE_LEAD,
    "q4_clutch": ARCHETYPE_UNSTABLE_LEAD,
    "panic_fade_fast": ARCHETYPE_PANIC_SWING,
    "underdog_liftoff": ARCHETYPE_PANIC_SWING,
    "inversion": ARCHETYPE_ANOMALY,
    "winner_definition": ARCHETYPE_BROAD_WINNER,
}

CORE_LLM_TARGET_ARCHETYPES = (
    ARCHETYPE_QUARTER_OPEN,
    ARCHETYPE_UNSTABLE_LEAD,
    ARCHETYPE_ANOMALY,
)

CORE_LLM_REPLAY_FAMILIES = (
    "inversion",
    "quarter_open_reprice",
    "micro_momentum_continuation",
)

FOCUSED_BASELINE_IDS = (
    "inversion",
    "quarter_open_reprice",
    "micro_momentum_continuation",
    "controller_vnext_unified_v1 :: balanced",
    "controller_vnext_deterministic_v1 :: tight",
)

COMPILE_TEMPLATE_POLICIES: dict[str, dict[str, Any]] = {
    "quarter_open_impulse": {
        "template_name": "quarter_open_impulse",
        "description": "Prioritize replay-promising quarter-open repricing families and fail closed if they cannot compile back to a fresh candidate.",
        "target_archetypes": (ARCHETYPE_QUARTER_OPEN,),
        "preferred_families": ("quarter_open_reprice", "micro_momentum_continuation", "inversion"),
        "entry_rule_hint": "Enter only on fresh quarter-open signals with one-tick or tighter spread and no state lag drift.",
        "exit_rule_hint": "Reuse the candidate exit rule and keep the move short-duration.",
        "stop_hint": "Abort on replay freshness drift or a fast price reversal against entry.",
    },
    "anomaly_reversal": {
        "template_name": "anomaly_reversal",
        "description": "Compile anomalous score-price dislocations back to inversion first, then to micro momentum if the anomaly is still moving.",
        "target_archetypes": (ARCHETYPE_ANOMALY, ARCHETYPE_PANIC_SWING),
        "preferred_families": ("inversion", "micro_momentum_continuation"),
        "entry_rule_hint": "Use only fresh anomaly candidates with visible price dislocation and bounded spread.",
        "exit_rule_hint": "Prefer the existing replay rule that mean-reverts quickly or exits on timebox.",
        "stop_hint": "Flatten if the anomaly extends and the replay freshness window breaks.",
    },
    "unstable_lead_flip": {
        "template_name": "unstable_lead_flip",
        "description": "Treat unstable late leads as inversion-first flips and fail closed when no fresh inversion candidate is available.",
        "target_archetypes": (ARCHETYPE_UNSTABLE_LEAD,),
        "preferred_families": ("inversion",),
        "entry_rule_hint": "Enter only if the unstable lead state is current and the quote is still fresh.",
        "exit_rule_hint": "Reuse the candidate take-profit or timebox and avoid extending the hold.",
        "stop_hint": "Cut quickly on scoreboard stabilization or quote drift.",
    },
}

ARCHETYPE_TEMPLATE_PRIORITY: dict[str, tuple[str, ...]] = {
    ARCHETYPE_QUARTER_OPEN: ("quarter_open_impulse",),
    ARCHETYPE_UNSTABLE_LEAD: ("unstable_lead_flip",),
    ARCHETYPE_ANOMALY: ("anomaly_reversal",),
    ARCHETYPE_PANIC_SWING: ("anomaly_reversal",),
}

LIVE_PROBE_PROMOTION_THRESHOLDS = {
    "min_replay_trade_count": 6,
    "min_executed_cluster_count": 5,
    "min_executed_family_count": 2,
    "min_focus_family_alignment_rate": 0.75,
    "max_top_trade_return_share": 0.35,
    "max_top_cluster_return_share": 0.40,
    "max_top_family_return_share": 0.75,
    "min_leave_one_out_bankroll_vs_inversion": 1.0,
}


@dataclass(slots=True)
class LLMStrategyLaneRequest:
    season: str = DEFAULT_SEASON
    analysis_version: str = ANALYSIS_VERSION
    replay_artifact_name: str = REPLAY_ARTIFACT_NAME
    shared_root: str | None = None
    analysis_output_root: str | None = None
    artifact_name: str = LLM_ARTIFACT_NAME
    cluster_window_minutes: int = 15
    build_dashboard_check: bool = True


@dataclass(slots=True)
class LLMVariantSpec:
    controller_id: str
    workflow: str
    selection_mode: str
    hypothesis: str
    max_actions_total: int
    max_signal_age_seconds: float
    max_quote_age_seconds: float
    max_spread_cents: float
    max_state_lag: float
    min_score: float
    optional_ml_gate_threshold: float | None
    llm_escalation_gap: float
    llm_escalation_min_candidates: int
    weights: dict[str, float]
    llm_target_archetypes: tuple[str, ...]
    allowed_families: tuple[str, ...]
    preferred_families: tuple[str, ...]
    compile_template_names: tuple[str, ...]
    compile_back_required: bool


LLM_CONTROLLER_VARIANTS: tuple[LLMVariantSpec, ...] = (
    LLMVariantSpec(
        controller_id="llm_selector_core_windows_v2",
        workflow="select_known_strategy",
        selection_mode="known_selector",
        hypothesis="Select only among replay-fresh existing signals inside the core replay windows.",
        max_actions_total=4,
        max_signal_age_seconds=75.0,
        max_quote_age_seconds=35.0,
        max_spread_cents=2.0,
        max_state_lag=5.0,
        min_score=0.58,
        optional_ml_gate_threshold=None,
        llm_escalation_gap=0.07,
        llm_escalation_min_candidates=2,
        weights={
            "actionability": 0.25,
            "family_prior": 0.18,
            "signal_strength": 0.12,
            "state_fit": 0.20,
            "price_dislocation": 0.10,
            "core_family_fit": 0.10,
            "optional_ml_support": 0.05,
        },
        llm_target_archetypes=CORE_LLM_TARGET_ARCHETYPES,
        allowed_families=CORE_LLM_REPLAY_FAMILIES,
        preferred_families=("inversion", "quarter_open_reprice", "micro_momentum_continuation"),
        compile_template_names=(),
        compile_back_required=False,
    ),
    LLMVariantSpec(
        controller_id="llm_template_compiler_core_windows_v2",
        workflow="compile_constrained_strategy",
        selection_mode="template_compiler",
        hypothesis="Compile constrained templates only in quarter-open, anomaly, and unstable-lead windows.",
        max_actions_total=4,
        max_signal_age_seconds=75.0,
        max_quote_age_seconds=35.0,
        max_spread_cents=2.0,
        max_state_lag=5.0,
        min_score=0.60,
        optional_ml_gate_threshold=None,
        llm_escalation_gap=0.10,
        llm_escalation_min_candidates=1,
        weights={
            "template_fit": 0.24,
            "actionability": 0.26,
            "archetype_severity": 0.08,
            "signal_strength": 0.08,
            "family_prior": 0.14,
            "price_dislocation": 0.10,
            "core_family_fit": 0.06,
            "optional_ml_support": 0.04,
        },
        llm_target_archetypes=CORE_LLM_TARGET_ARCHETYPES,
        allowed_families=CORE_LLM_REPLAY_FAMILIES,
        preferred_families=("quarter_open_reprice", "micro_momentum_continuation", "inversion"),
        compile_template_names=("quarter_open_impulse", "anomaly_reversal", "unstable_lead_flip"),
        compile_back_required=True,
    ),
)


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


def _safe_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    resolved = _safe_bool(value)
    return default if resolved is None else bool(resolved)


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


def _safe_to_datetime(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, utc=True, format="mixed", errors="coerce")
    except TypeError:
        return pd.to_datetime(series, utc=True, errors="coerce")


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


def _subject_artifact_stem(subject_name: str) -> str:
    return str(subject_name).replace(" ", "_").replace("::", "__").replace("/", "_")


def _signal_id(subject_name: str, game_id: Any, team_side: Any, entry_state_index: Any) -> str:
    entry_index = _safe_int(entry_state_index)
    return f"{subject_name}|{_normalize_game_id(game_id)}|{str(team_side or '')}|{entry_index or 0}"


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
        subject_stem = _subject_artifact_stem(subject_name)
        standard_frame = _read_table(replay_root / f"standard_{subject_stem}")
        replay_frame = _read_table(replay_root / f"replay_{subject_stem}")
        if not standard_frame.empty:
            standard_frames[subject_name] = standard_frame
        if not replay_frame.empty:
            replay_frames[subject_name] = replay_frame
    return standard_frames, replay_frames


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
    work["cycle_at"] = _safe_to_datetime(work["cycle_at"])
    work["quote_time"] = _safe_to_datetime(work["quote_time"])
    work["entry_state_index"] = pd.to_numeric(work["entry_state_index"], errors="coerce")
    work["latest_state_index"] = pd.to_numeric(work["latest_state_index"], errors="coerce")
    work["quote_age_seconds"] = pd.to_numeric(work["quote_age_seconds"], errors="coerce")
    work["spread_cents"] = pd.to_numeric(work["spread_cents"], errors="coerce")
    rows: list[dict[str, Any]] = []
    for signal_id, group in work.groupby("signal_id", dropna=False, sort=False):
        ordered = group.sort_values(["attempt_index", "cycle_at"], kind="mergesort").reset_index(drop=True)
        first = ordered.iloc[0]
        final = ordered.iloc[-1]
        submit = ordered[ordered["result"].astype(str).eq("filled")]
        if submit.empty:
            submit = ordered[ordered["result"].astype(str).eq("no_trade")]
        if submit.empty:
            submit = ordered.tail(1)
        submit_row = submit.iloc[0]
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
                "submit_attempt_cycle_at": submit_row["cycle_at"],
                "submit_attempt_result": submit_row.get("result"),
                "submit_attempt_reason": submit_row.get("reason"),
                "submit_attempt_quote_age_seconds": submit_row.get("quote_age_seconds"),
                "submit_attempt_spread_cents": submit_row.get("spread_cents"),
                "submit_attempt_latest_state_index": submit_row.get("latest_state_index"),
                "submit_attempt_quote_time": submit_row.get("quote_time"),
                "final_attempt_cycle_at": final["cycle_at"],
                "final_attempt_result": final.get("result"),
                "final_attempt_reason": final.get("reason"),
                "final_attempt_quote_age_seconds": final.get("quote_age_seconds"),
                "final_attempt_spread_cents": final.get("spread_cents"),
                "final_attempt_latest_state_index": final.get("latest_state_index"),
            }
        )
    return pd.DataFrame(rows)


def _load_postseason_state_panel(output_root: Path, analysis_version: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for phase in ("play_in", "playoffs"):
        frame = _read_table(output_root / DEFAULT_SEASON / phase / analysis_version / "nba_analysis_state_panel")
        if frame.empty:
            continue
        frame = frame.copy()
        frame["game_id"] = frame["game_id"].map(_normalize_game_id)
        frame["team_side"] = frame["team_side"].astype(str)
        frame["state_index"] = pd.to_numeric(frame["state_index"], errors="coerce")
        frame["event_at"] = _safe_to_datetime(frame["event_at"])
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _build_state_lookup(state_panel_df: pd.DataFrame) -> pd.DataFrame:
    if state_panel_df.empty:
        return pd.DataFrame()
    columns = {
        "game_id": "game_id",
        "team_side": "team_side",
        "state_index": "entry_state_index",
        "period": "state_period",
        "period_label": "state_period_label",
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


def _load_family_prior_frame(subject_summary_df: pd.DataFrame) -> pd.DataFrame:
    if subject_summary_df.empty:
        return pd.DataFrame()
    family_df = subject_summary_df[subject_summary_df["subject_type"].astype(str).eq("family")].copy()
    if family_df.empty:
        return pd.DataFrame()
    family_df["execution_rate"] = pd.to_numeric(family_df["execution_rate"], errors="coerce").fillna(0.0)
    family_df["replay_ending_bankroll"] = pd.to_numeric(family_df["replay_ending_bankroll"], errors="coerce").fillna(10.0)
    family_df["replay_trade_count"] = pd.to_numeric(family_df["replay_trade_count"], errors="coerce").fillna(0.0)
    family_df["standard_trade_count"] = pd.to_numeric(family_df["standard_trade_count"], errors="coerce").fillna(0.0)
    family_df["family_bankroll_score"] = ((family_df["replay_ending_bankroll"] - 10.0) / 5.0).clip(lower=0.0, upper=1.0)
    family_df["family_volume_score"] = (family_df["replay_trade_count"] / family_df["standard_trade_count"].replace(0.0, np.nan)).fillna(0.0).clip(0.0, 1.0)
    family_df["family_prior_score"] = (
        (0.55 * family_df["execution_rate"].clip(0.0, 1.0))
        + (0.30 * family_df["family_bankroll_score"])
        + (0.15 * family_df["family_volume_score"])
    ).clip(0.0, 1.0)
    return family_df[
        [
            "subject_name",
            "execution_rate",
            "replay_ending_bankroll",
            "replay_trade_count",
            "standard_trade_count",
            "family_prior_score",
        ]
    ].rename(
        columns={
            "subject_name": "strategy_family",
            "execution_rate": "family_execution_rate",
            "replay_ending_bankroll": "family_replay_ending_bankroll",
            "replay_trade_count": "family_replay_trade_count",
            "standard_trade_count": "family_standard_trade_count",
        }
    )


def _load_optional_ml_features(shared_root: Path, season: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    ml_root = shared_root / "artifacts" / ML_OUTPUT_DIRNAME / season
    if not ml_root.exists():
        return pd.DataFrame(), {"available": False, "reason": "ml_artifacts_missing"}

    def _artifact_version(path: Path) -> int:
        matches = re.findall(r"v(\d+)", path.parent.name.lower())
        return int(matches[-1]) if matches else 0

    def _optional_series(frame: pd.DataFrame, column: str) -> pd.Series:
        if column in frame.columns:
            return frame[column]
        return pd.Series([pd.NA] * len(frame), index=frame.index, dtype="object")

    candidates = sorted(
        [path for path in ml_root.glob("*/family_candidates.csv") if path.is_file()],
        key=lambda path: (
            int(_artifact_version(path) >= 2),
            _artifact_version(path),
            path.stat().st_mtime,
        ),
        reverse=True,
    )
    if not candidates:
        return pd.DataFrame(), {"available": False, "reason": "ml_family_candidates_missing"}

    last_error: dict[str, Any] = {"available": False, "reason": "ml_family_candidates_empty"}
    for path in candidates:
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            last_error = {"available": False, "reason": "ml_family_candidates_empty", "path": str(path)}
            continue
        if frame.empty or "signal_id" not in frame.columns:
            last_error = {"available": False, "reason": "ml_family_candidates_empty", "path": str(path)}
            continue

        focus_path = path.parent / "focus_family_selected.csv"
        focus_family_df = pd.DataFrame()
        if focus_path.exists():
            try:
                focus_family_df = pd.read_csv(focus_path)
            except pd.errors.EmptyDataError:
                focus_family_df = pd.DataFrame()

        focus_family_flag = _optional_series(frame, "focus_family_flag").map(_coerce_bool)
        ml_strategy_family = _optional_series(frame, "strategy_family").fillna("").astype(str)
        ml_df = pd.DataFrame(
            {
                "signal_id": _optional_series(frame, "signal_id").astype(str),
                "ml_strategy_family": ml_strategy_family,
                "ml_rank_score_raw": pd.to_numeric(_optional_series(frame, "rank_score"), errors="coerce"),
                "ml_calibrated_rank_score": pd.to_numeric(
                    _optional_series(frame, "calibrated_rank_score"),
                    errors="coerce",
                ),
                "ml_gate_score": pd.to_numeric(_optional_series(frame, "gate_score"), errors="coerce"),
                "ml_calibrated_execution_likelihood": pd.to_numeric(
                    _optional_series(frame, "calibrated_execution_likelihood"),
                    errors="coerce",
                ),
                "ml_sidecar_probability": pd.to_numeric(
                    _optional_series(frame, "sidecar_probability"),
                    errors="coerce",
                ),
                "ml_focus_family_flag": focus_family_flag.fillna(False).astype(bool),
                "ml_focus_family_tag": ml_strategy_family.where(focus_family_flag.fillna(False).astype(bool)),
                "ml_selection_source": _optional_series(frame, "selection_source").fillna("").astype(str),
                "ml_prediction_mode": _optional_series(frame, "prediction_mode").fillna("").astype(str),
                "ml_prediction_mode_gate": _optional_series(frame, "prediction_mode_gate").fillna("").astype(str),
                "ml_rank_calibration_mode": _optional_series(frame, "rank_calibration_mode").fillna("").astype(str),
                "ml_execution_calibration_mode": _optional_series(frame, "execution_calibration_mode")
                .fillna("")
                .astype(str),
            }
        )
        ml_df = ml_df.drop_duplicates(subset=["signal_id"]).copy()

        focus_families: list[str] = []
        if not focus_family_df.empty and "strategy_family" in focus_family_df.columns:
            if "focus_family_flag" in focus_family_df.columns:
                focus_source = focus_family_df.loc[focus_family_df["focus_family_flag"].map(_coerce_bool), "strategy_family"]
            else:
                focus_source = focus_family_df["strategy_family"]
            focus_families = sorted({str(value) for value in focus_source.fillna("").astype(str) if str(value)})
        if not focus_families:
            focus_families = sorted(
                {
                    str(value)
                    for value in ml_df.loc[ml_df["ml_focus_family_flag"], "ml_strategy_family"].fillna("").astype(str)
                    if str(value)
                }
            )

        selection_sources = sorted(
            {str(value) for value in ml_df["ml_selection_source"].fillna("").astype(str) if str(value)}
        )
        artifact_version = _artifact_version(path)
        return ml_df, {
            "available": True,
            "path": str(path),
            "artifact_name": path.parent.name,
            "artifact_version": artifact_version,
            "row_count": int(len(ml_df)),
            "focus_families": focus_families,
            "selection_sources": selection_sources,
            "helper_fields": [
                "ml_calibrated_confidence",
                "ml_calibrated_execution_likelihood",
                "ml_focus_family_flag",
                "ml_focus_family_tag",
            ],
        }

    return pd.DataFrame(), last_error


def _classify_state_archetype(record: dict[str, Any]) -> str:
    period_label = str(record.get("state_period_label") or record.get("period_label") or "").upper()
    clock_elapsed_seconds = _safe_float(record.get("state_clock_elapsed_seconds")) or 0.0
    seconds_to_game_end = _safe_float(record.get("state_seconds_to_game_end")) or 0.0
    score_diff = _safe_float(record.get("state_score_diff"))
    abs_score_diff = abs(score_diff) if score_diff is not None else None
    lead_changes = _safe_float(record.get("state_lead_changes_so_far")) or 0.0
    abs_price_delta = _safe_float(record.get("state_abs_price_delta_from_open")) or 0.0
    net_points = abs(_safe_float(record.get("state_net_points_last_5_events")) or 0.0)
    strategy_family = str(record.get("strategy_family") or "")

    if period_label == "Q1" and clock_elapsed_seconds <= 360.0:
        return ARCHETYPE_QUARTER_OPEN
    if period_label == "Q3" and clock_elapsed_seconds <= 300.0:
        return ARCHETYPE_HALFTIME_DISLOCATION
    if period_label in {"Q4", "OT1", "OT2"} and (
        (abs_score_diff is not None and abs_score_diff <= 5.0) or lead_changes >= 4.0
    ):
        return ARCHETYPE_UNSTABLE_LEAD
    if abs_price_delta >= 0.16 and net_points >= 3.0:
        return ARCHETYPE_PANIC_SWING
    if abs_price_delta >= 0.09:
        return ARCHETYPE_ANOMALY
    if period_label in {"Q4", "OT1", "OT2"} and lead_changes >= 2.0:
        return ARCHETYPE_UNSTABLE_LEAD
    if period_label == "Q3" and seconds_to_game_end <= 1500.0 and lead_changes >= 6.0:
        return ARCHETYPE_UNSTABLE_LEAD
    return FAMILY_FALLBACK_ARCHETYPE.get(strategy_family, ARCHETYPE_BROAD_WINNER)


def _parse_rule_cents(pattern: re.Pattern[str], text: Any) -> int | None:
    raw = str(text or "")
    match = pattern.search(raw)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _build_actionability_score(frame: pd.DataFrame) -> pd.Series:
    signal_age_score = 1.0 - frame["submit_signal_age_seconds"].fillna(120.0).clip(0.0, 120.0).div(120.0)
    quote_age_score = 1.0 - frame["submit_quote_age_seconds"].fillna(60.0).clip(0.0, 60.0).div(60.0)
    spread_score = 1.0 - frame["submit_spread_cents"].fillna(6.0).clip(0.0, 6.0).div(6.0)
    state_lag_score = 1.0 - frame["submit_state_lag"].fillna(10.0).clip(0.0, 10.0).div(10.0)
    return (
        (0.35 * signal_age_score)
        + (0.25 * quote_age_score)
        + (0.20 * spread_score)
        + (0.20 * state_lag_score)
    ).clip(0.0, 1.0)


def _build_price_dislocation_score(frame: pd.DataFrame) -> pd.Series:
    score = pd.to_numeric(frame["state_abs_price_delta_from_open"], errors="coerce").fillna(0.0).div(0.30)
    return score.clip(0.0, 1.0)


def _build_signal_strength_score(frame: pd.DataFrame) -> pd.Series:
    signal_strength = pd.to_numeric(frame["signal_strength"], errors="coerce")
    if signal_strength.notna().sum() == 0:
        return pd.Series(np.full(len(frame), 0.5), index=frame.index)
    return signal_strength.fillna(signal_strength.median()).rank(pct=True)


def _build_state_fit_score(frame: pd.DataFrame) -> pd.Series:
    values: list[float] = []
    for record in frame.to_dict(orient="records"):
        archetype = str(record.get("state_archetype") or ARCHETYPE_BROAD_WINNER)
        family = str(record.get("strategy_family") or "")
        preferred = ARCHETYPE_FAMILY_PREFERENCES.get(archetype, ())
        if family in preferred:
            index = preferred.index(family)
            values.append(max(0.40, 1.0 - (0.22 * index)))
            continue
        if FAMILY_FALLBACK_ARCHETYPE.get(family) == archetype:
            values.append(0.62)
            continue
        if family == "winner_definition":
            values.append(0.50 if archetype == ARCHETYPE_BROAD_WINNER else 0.28)
            continue
        values.append(0.25)
    return pd.Series(values, index=frame.index).clip(0.0, 1.0)


def _build_template_fit_score(frame: pd.DataFrame) -> pd.Series:
    values: list[float] = []
    for record in frame.to_dict(orient="records"):
        archetype = str(record.get("state_archetype") or ARCHETYPE_BROAD_WINNER)
        family = str(record.get("strategy_family") or "")
        preferred = ARCHETYPE_FAMILY_PREFERENCES.get(archetype, ())
        if family in preferred:
            index = preferred.index(family)
            values.append(max(0.35, 1.0 - (0.18 * index)))
            continue
        values.append(0.20)
    return pd.Series(values, index=frame.index).clip(0.0, 1.0)


def _optional_ml_support_score(record: dict[str, Any]) -> float:
    weighted_scores: list[tuple[float, float]] = []
    for key, weight in (
        ("ml_calibrated_confidence", 0.40),
        ("ml_calibrated_execution_likelihood", 0.30),
        ("ml_rank_score", 0.20),
        ("ml_gate_score", 0.10),
    ):
        value = _safe_float(record.get(key))
        if value is None:
            continue
        weighted_scores.append((float(max(0.0, min(1.0, value))), weight))
    if not weighted_scores:
        return 0.5
    numerator = sum(value * weight for value, weight in weighted_scores)
    denominator = sum(weight for _, weight in weighted_scores)
    support_score = numerator / denominator if denominator else 0.5
    if _coerce_bool(record.get("ml_focus_family_flag")):
        support_score = min(1.0, support_score + 0.05)
    return float(support_score)


def _variant_family_allowed(record: dict[str, Any], variant: LLMVariantSpec) -> bool:
    allowed_families = set(variant.allowed_families)
    if not allowed_families:
        return True
    family = str(record.get("strategy_family") or "")
    return family in allowed_families


def _variant_family_fit_score(record: dict[str, Any], variant: LLMVariantSpec) -> float:
    family = str(record.get("strategy_family") or "")
    if not _variant_family_allowed(record, variant):
        return 0.0
    if family in variant.preferred_families:
        index = variant.preferred_families.index(family)
        return float(max(0.45, 1.0 - (0.18 * index)))
    return 0.60


def build_llm_candidate_dataset(
    *,
    signal_summary_df: pd.DataFrame,
    attempt_trace_df: pd.DataFrame,
    standard_frames: dict[str, pd.DataFrame],
    replay_frames: dict[str, pd.DataFrame],
    state_panel_df: pd.DataFrame,
    subject_summary_df: pd.DataFrame,
    ml_feature_df: pd.DataFrame,
) -> pd.DataFrame:
    work = signal_summary_df.copy()
    if work.empty:
        return work
    work["game_id"] = work["game_id"].map(_normalize_game_id)
    work["team_side"] = work["team_side"].astype(str)
    work["entry_state_index"] = pd.to_numeric(work["entry_state_index"], errors="coerce")
    work["exit_state_index"] = pd.to_numeric(work["exit_state_index"], errors="coerce")
    work["signal_entry_at"] = _safe_to_datetime(work["signal_entry_at"])
    work["signal_exit_at"] = _safe_to_datetime(work["signal_exit_at"])
    work["signal_entry_price"] = pd.to_numeric(work["signal_entry_price"], errors="coerce")
    work["signal_exit_price"] = pd.to_numeric(work["signal_exit_price"], errors="coerce")
    work["executed_flag"] = work["executed_flag"].fillna(False).astype(bool)
    work["signal_id"] = work.apply(
        lambda row: str(row.get("signal_id") or _signal_id(
            str(row.get("subject_name") or ""),
            row.get("game_id"),
            row.get("team_side"),
            row.get("entry_state_index"),
        )),
        axis=1,
    )
    work = work[work["subject_type"].astype(str).eq("family")].copy()
    work["game_date"] = work["signal_entry_at"].dt.date

    standard_lookup = _build_trade_lookup(standard_frames)
    replay_lookup = _build_trade_lookup(replay_frames)
    attempt_agg_df = _aggregate_attempt_trace(attempt_trace_df)
    family_prior_df = _load_family_prior_frame(subject_summary_df)
    state_lookup_df = _build_state_lookup(state_panel_df)

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
    if not state_lookup_df.empty:
        merged = merged.merge(
            state_lookup_df,
            on=["game_id", "team_side", "entry_state_index"],
            how="left",
        )
    if not family_prior_df.empty:
        merged = merged.merge(family_prior_df, on="strategy_family", how="left")
    if not ml_feature_df.empty:
        merged = merged.merge(ml_feature_df, on="signal_id", how="left")

    merged["entry_rule"] = merged["standard_entry_rule"].fillna("")
    merged["exit_rule"] = merged["standard_exit_rule"].fillna("")
    merged["entry_metadata_json"] = merged["standard_entry_metadata_json"].fillna("{}")
    merged["context_tags_json"] = merged["standard_context_tags_json"].fillna("{}")
    merged["signal_strength"] = pd.to_numeric(
        merged["standard_signal_strength"].fillna(merged["signal_entry_price"]).fillna(0.0),
        errors="coerce",
    ).fillna(0.0)
    merged["entry_price"] = pd.to_numeric(
        merged["standard_entry_price"].fillna(merged["signal_entry_price"]),
        errors="coerce",
    )
    merged["opening_band"] = merged["standard_opening_band"].fillna("")
    merged["period_label"] = merged["standard_period_label"].fillna("")
    merged["score_diff_bucket"] = merged["standard_score_diff_bucket"].fillna("")
    merged["context_bucket"] = merged["standard_context_bucket"].fillna("")
    merged["team_slug"] = merged["standard_team_slug"].fillna("")
    merged["opponent_team_slug"] = merged["standard_opponent_team_slug"].fillna("")

    merged["submit_signal_age_seconds"] = (
        _safe_to_datetime(merged["submit_attempt_cycle_at"]) - merged["signal_entry_at"]
    ).dt.total_seconds()
    merged["submit_quote_age_seconds"] = pd.to_numeric(merged["submit_attempt_quote_age_seconds"], errors="coerce")
    merged["submit_spread_cents"] = pd.to_numeric(merged["submit_attempt_spread_cents"], errors="coerce")
    merged["submit_state_lag"] = (
        pd.to_numeric(merged["submit_attempt_latest_state_index"], errors="coerce") - merged["entry_state_index"]
    )
    merged["state_archetype"] = merged.apply(lambda row: _classify_state_archetype(row.to_dict()), axis=1)
    merged["archetype_severity"] = merged["state_archetype"].map(ARCHETYPE_SEVERITY).fillna(0.40)
    merged["actionability_score"] = _build_actionability_score(merged)
    merged["price_dislocation_score"] = _build_price_dislocation_score(merged)
    merged["signal_strength_score"] = _build_signal_strength_score(merged)
    merged["state_fit_score"] = _build_state_fit_score(merged)
    merged["template_fit_score"] = _build_template_fit_score(merged)
    merged["family_prior_score"] = pd.to_numeric(merged["family_prior_score"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    raw_ml_rank_score = (
        pd.to_numeric(merged["ml_rank_score_raw"], errors="coerce")
        if "ml_rank_score_raw" in merged.columns
        else pd.Series(np.nan, index=merged.index)
    )
    calibrated_ml_rank_score = (
        pd.to_numeric(merged["ml_calibrated_rank_score"], errors="coerce")
        if "ml_calibrated_rank_score" in merged.columns
        else pd.Series(np.nan, index=merged.index)
    )
    ml_sidecar_probability = (
        pd.to_numeric(merged["ml_sidecar_probability"], errors="coerce")
        if "ml_sidecar_probability" in merged.columns
        else pd.Series(np.nan, index=merged.index)
    )
    merged["ml_gate_score"] = (
        pd.to_numeric(merged["ml_gate_score"], errors="coerce")
        if "ml_gate_score" in merged.columns
        else pd.Series(np.nan, index=merged.index)
    )
    merged["ml_rank_score"] = calibrated_ml_rank_score.fillna(raw_ml_rank_score)
    merged["ml_calibrated_execution_likelihood"] = (
        pd.to_numeric(merged["ml_calibrated_execution_likelihood"], errors="coerce")
        if "ml_calibrated_execution_likelihood" in merged.columns
        else pd.Series(np.nan, index=merged.index)
    )
    merged["ml_calibrated_confidence"] = ml_sidecar_probability.fillna(calibrated_ml_rank_score).fillna(raw_ml_rank_score)
    if "ml_focus_family_flag" in merged.columns:
        merged["ml_focus_family_flag"] = merged["ml_focus_family_flag"].map(_coerce_bool)
    else:
        merged["ml_focus_family_flag"] = False
    if "ml_focus_family_tag" not in merged.columns:
        merged["ml_focus_family_tag"] = pd.Series([None] * len(merged), index=merged.index, dtype="object")
    merged["ml_focus_family_tag"] = merged["ml_focus_family_tag"].where(
        merged["ml_focus_family_tag"].notna(),
        merged["strategy_family"].where(merged["ml_focus_family_flag"]),
    )
    if "ml_selection_source" not in merged.columns:
        merged["ml_selection_source"] = ""
    merged["optional_ml_available_flag"] = (
        merged["ml_gate_score"].notna()
        | merged["ml_rank_score"].notna()
        | merged["ml_calibrated_confidence"].notna()
        | merged["ml_calibrated_execution_likelihood"].notna()
    )
    merged["candidate_id"] = merged["signal_id"]
    return merged


def _build_decision_clusters(frame: pd.DataFrame, *, cluster_window_minutes: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.sort_values(["game_id", "signal_entry_at", "strategy_family"], kind="mergesort").copy()
    if "state_archetype" not in work.columns:
        work["state_archetype"] = ARCHETYPE_BROAD_WINNER
    cluster_ids: list[str] = []
    cluster_orders: list[int] = []
    current_game: str | None = None
    current_anchor: pd.Timestamp | None = None
    current_cluster_index = -1
    for record in work.to_dict(orient="records"):
        game_id = _normalize_game_id(record.get("game_id"))
        signal_entry_at = pd.Timestamp(record.get("signal_entry_at")) if pd.notna(record.get("signal_entry_at")) else None
        if (
            game_id != current_game
            or current_anchor is None
            or signal_entry_at is None
            or (signal_entry_at - current_anchor).total_seconds() > float(cluster_window_minutes * 60)
        ):
            current_cluster_index += 1
            current_game = game_id
            current_anchor = signal_entry_at
            cluster_orders.append(0)
        else:
            cluster_orders.append(cluster_orders[-1] + 1 if cluster_ids else 0)
        cluster_ids.append(f"{game_id}|cluster|{current_cluster_index:03d}")
    work["cluster_id"] = cluster_ids
    work["cluster_order"] = cluster_orders

    cluster_summary = (
        work.groupby("cluster_id", dropna=False)
        .agg(
            cluster_game_id=("game_id", "first"),
            cluster_signal_count=("signal_id", "count"),
            cluster_entry_at=("signal_entry_at", "min"),
            cluster_archetype=("state_archetype", lambda values: values.mode().iloc[0] if len(values.mode()) else values.iloc[0]),
        )
        .reset_index()
    )
    return work.merge(cluster_summary, on="cluster_id", how="left")


def _resolve_compile_template(cluster_archetype: str, variant: LLMVariantSpec) -> dict[str, Any] | None:
    if not variant.compile_template_names:
        return None
    allowed_template_names = set(variant.compile_template_names)
    for template_name in ARCHETYPE_TEMPLATE_PRIORITY.get(cluster_archetype, ()):
        if template_name in allowed_template_names and template_name in COMPILE_TEMPLATE_POLICIES:
            return dict(COMPILE_TEMPLATE_POLICIES[template_name])
    for template_name in variant.compile_template_names:
        template_policy = COMPILE_TEMPLATE_POLICIES.get(template_name)
        if template_policy is None:
            continue
        if cluster_archetype in set(template_policy.get("target_archetypes") or ()):
            return dict(template_policy)
    return None


def _template_family_priority_score(strategy_family: str, template_policy: dict[str, Any] | None) -> float:
    if not template_policy:
        return 0.0
    preferred_families = tuple(template_policy.get("preferred_families") or ())
    if strategy_family not in preferred_families:
        return 0.0
    index = preferred_families.index(strategy_family)
    return float(max(0.40, 1.0 - (0.18 * index)))


def _derive_cluster_failure_reason(
    cluster_work: pd.DataFrame,
    *,
    variant: LLMVariantSpec,
    archetype_allowed: bool,
    template_policy: dict[str, Any] | None,
    viable_df: pd.DataFrame,
    eligible_df: pd.DataFrame,
    selected_score: float | None,
) -> str | None:
    if not archetype_allowed:
        return "state_window_not_targeted"
    if variant.compile_back_required and template_policy is None:
        return "template_not_configured"
    if viable_df.empty:
        if cluster_work["optional_ml_gate_pass"].fillna(True).eq(False).any():
            return "optional_ml_gate_failed"
        if (
            cluster_work["signal_age_pass"].fillna(True).eq(False).any()
            or cluster_work["quote_age_pass"].fillna(True).eq(False).any()
            or cluster_work["state_lag_pass"].fillna(True).eq(False).any()
            or cluster_work["state_context_pass"].fillna(True).eq(False).any()
        ):
            return "freshness_gate_failed"
        return "replay_gate_failed"
    if eligible_df.empty:
        return "no_compile_eligible_candidate" if variant.compile_back_required else "family_not_targeted"
    if selected_score is not None and selected_score < variant.min_score:
        return "selection_score_below_threshold"
    return None


def _hard_gate_flags(record: dict[str, Any], variant: LLMVariantSpec) -> dict[str, Any]:
    signal_age = _safe_float(record.get("submit_signal_age_seconds"))
    quote_age = _safe_float(record.get("submit_quote_age_seconds"))
    spread_cents = _safe_float(record.get("submit_spread_cents"))
    state_lag = _safe_float(record.get("submit_state_lag"))
    ml_gate_score = _safe_float(record.get("ml_gate_score"))
    state_period_label = str(record.get("state_period_label") or "").strip()
    state_score_diff = _safe_float(record.get("state_score_diff"))

    signal_age_pass = signal_age is not None and signal_age <= variant.max_signal_age_seconds
    quote_age_pass = quote_age is not None and quote_age <= variant.max_quote_age_seconds
    spread_pass = spread_cents is not None and spread_cents <= variant.max_spread_cents
    state_lag_pass = state_lag is not None and state_lag <= variant.max_state_lag
    state_context_pass = bool(state_period_label) and state_score_diff is not None
    ml_gate_pass = (
        True
        if variant.optional_ml_gate_threshold is None or ml_gate_score is None
        else ml_gate_score >= variant.optional_ml_gate_threshold
    )
    hard_gate_pass = (
        signal_age_pass
        and quote_age_pass
        and spread_pass
        and state_lag_pass
        and state_context_pass
        and ml_gate_pass
    )
    return {
        "signal_age_pass": signal_age_pass,
        "quote_age_pass": quote_age_pass,
        "spread_pass": spread_pass,
        "state_lag_pass": state_lag_pass,
        "state_context_pass": state_context_pass,
        "optional_ml_gate_pass": ml_gate_pass,
        "hard_gate_pass": hard_gate_pass,
    }


def _score_record(record: dict[str, Any], variant: LLMVariantSpec) -> tuple[float, dict[str, float]]:
    components = {
        "actionability": float(_safe_float(record.get("actionability_score")) or 0.0),
        "family_prior": float(_safe_float(record.get("family_prior_score")) or 0.0),
        "signal_strength": float(_safe_float(record.get("signal_strength_score")) or 0.0),
        "state_fit": float(_safe_float(record.get("state_fit_score")) or 0.0),
        "template_fit": float(_safe_float(record.get("template_fit_score")) or 0.0),
        "price_dislocation": float(_safe_float(record.get("price_dislocation_score")) or 0.0),
        "archetype_severity": float(_safe_float(record.get("archetype_severity")) or 0.0),
        "core_family_fit": _variant_family_fit_score(record, variant),
        "optional_ml_support": _optional_ml_support_score(record),
    }
    if variant.selection_mode == "template_compiler":
        components["state_fit"] = components["template_fit"]
    score = 0.0
    for key, weight in variant.weights.items():
        score += weight * components.get(key, 0.0)
    return float(max(0.0, min(1.0, score))), components


def _infer_confidence_band(score: float) -> str:
    if score >= 0.82:
        return "very_high"
    if score >= 0.70:
        return "high"
    if score >= 0.58:
        return "medium"
    return "low"


def _compile_stop_concept(record: dict[str, Any]) -> dict[str, Any]:
    metadata = _safe_parse_mapping(record.get("entry_metadata_json"))
    stop_price = _safe_float(metadata.get("stop_price"))
    target_price = _safe_float(metadata.get("target_price"))
    exit_rule = str(record.get("exit_rule") or "")
    stop_cents = _parse_rule_cents(_MINUS_CENTS_PATTERN, exit_rule)
    target_cents = _parse_rule_cents(_PLUS_CENTS_PATTERN, exit_rule)
    if stop_price is not None:
        return {
            "mode": "absolute_price_stop",
            "stop_price": stop_price,
            "target_price": target_price,
            "description": "Use the candidate's encoded stop_price as the primary loss cap.",
        }
    if stop_cents is not None:
        return {
            "mode": "rule_bound_price_reversal",
            "stop_cents_from_entry": stop_cents,
            "target_cents_from_entry": target_cents,
            "description": "Honor the replay candidate's exit_rule loss cap before the timebox expires.",
        }
    return {
        "mode": "state_staleness_guard",
        "description": "Abort or flatten if state lag or quote freshness drifts beyond the replay-aware gate.",
    }


def _build_prompt_payload(
    cluster_rows: pd.DataFrame,
    *,
    variant: LLMVariantSpec,
    viable_count: int,
    eligible_count: int,
    template_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    archetype = str(cluster_rows["cluster_archetype"].iloc[0]) if not cluster_rows.empty else ARCHETYPE_BROAD_WINNER
    candidate_rows: list[dict[str, Any]] = []
    for record in cluster_rows.to_dict(orient="records"):
        candidate_rows.append(
            {
                "signal_id": record.get("signal_id"),
                "strategy_family": record.get("strategy_family"),
                "side": record.get("team_side"),
                "family_allowed": bool(record.get("variant_family_allowed")),
                "template_priority": _safe_float(record.get("template_family_priority")),
                "compile_candidate_eligible": bool(record.get("compile_candidate_eligible_flag")),
                "selection_score": _safe_float(record.get("selection_score")),
                "entry_rule": record.get("entry_rule"),
                "exit_rule": record.get("exit_rule"),
                "signal_strength": _safe_float(record.get("signal_strength")),
                "submit_signal_age_seconds": _safe_float(record.get("submit_signal_age_seconds")),
                "submit_quote_age_seconds": _safe_float(record.get("submit_quote_age_seconds")),
                "submit_spread_cents": _safe_float(record.get("submit_spread_cents")),
                "submit_state_lag": _safe_float(record.get("submit_state_lag")),
                "state_archetype": record.get("state_archetype"),
                "score_diff": _safe_float(record.get("state_score_diff")),
                "lead_changes_so_far": _safe_float(record.get("state_lead_changes_so_far")),
                "abs_price_delta_from_open": _safe_float(record.get("state_abs_price_delta_from_open")),
                "optional_ml_gate_score": _safe_float(record.get("ml_gate_score")),
                "optional_ml_rank_score": _safe_float(record.get("ml_rank_score")),
                "optional_ml_calibrated_confidence": _safe_float(record.get("ml_calibrated_confidence")),
                "optional_ml_calibrated_execution_likelihood": _safe_float(
                    record.get("ml_calibrated_execution_likelihood")
                ),
                "optional_ml_focus_family_flag": _coerce_bool(record.get("ml_focus_family_flag")),
                "optional_ml_focus_family_tag": record.get("ml_focus_family_tag"),
                "optional_ml_selection_source": record.get("ml_selection_source"),
                "optional_ml_support_score": _optional_ml_support_score(record),
            }
        )
    return {
        "schema_version": LLM_PROMPT_CONTRACT_VERSION,
        "controller_id": variant.controller_id,
        "workflow": variant.workflow,
        "selection_mode": variant.selection_mode,
        "hypothesis": variant.hypothesis,
        "decision_provider": "shadow_deterministic",
        "cluster_id": cluster_rows["cluster_id"].iloc[0] if not cluster_rows.empty else None,
        "game_id": cluster_rows["game_id"].iloc[0] if not cluster_rows.empty else None,
        "archetype": archetype,
        "viable_candidate_count": int(viable_count),
        "compile_candidate_count": int(eligible_count),
        "optional_ml_summary": {
            "available": (
                bool(cluster_rows["optional_ml_available_flag"].fillna(False).any())
                if "optional_ml_available_flag" in cluster_rows.columns
                else False
            ),
            "focus_family_tags": (
                sorted(
                    {
                        str(value)
                        for value in cluster_rows["ml_focus_family_tag"].fillna("").astype(str)
                        if str(value)
                    }
                )
                if "ml_focus_family_tag" in cluster_rows.columns
                else []
            ),
        },
        "selection_policy": {
            "target_archetypes": list(variant.llm_target_archetypes),
            "allowed_families": list(variant.allowed_families),
            "preferred_families": list(variant.preferred_families),
            "compile_back_required": variant.compile_back_required,
        },
        "template_policy": (
            {
                "template_name": template_policy.get("template_name"),
                "description": template_policy.get("description"),
                "preferred_families": list(template_policy.get("preferred_families") or ()),
                "entry_rule_hint": template_policy.get("entry_rule_hint"),
                "exit_rule_hint": template_policy.get("exit_rule_hint"),
                "stop_hint": template_policy.get("stop_hint"),
            }
            if template_policy
            else None
        ),
        "constraints": {
            "return_json_only": True,
            "must_map_to_existing_candidate_signal_id": True,
            "must_publish_strategy_family_side_entry_exit_stop_and_gates": True,
            "max_signal_age_seconds": variant.max_signal_age_seconds,
            "max_quote_age_seconds": variant.max_quote_age_seconds,
            "max_spread_cents": variant.max_spread_cents,
            "max_state_lag": variant.max_state_lag,
            "fail_closed_on_compile_miss": True,
            "optional_ml_is_not_required": True,
        },
        "candidates": candidate_rows,
    }


def _compile_action(
    record: dict[str, Any],
    *,
    variant: LLMVariantSpec,
    template_policy: dict[str, Any] | None,
    cluster_rank: int,
    cluster_viable_count: int,
    llm_call_recommended: bool,
    score: float,
    score_components: dict[str, float],
    compile_status: str,
    compile_failure_reason: str | None,
) -> dict[str, Any]:
    metadata = _safe_parse_mapping(record.get("entry_metadata_json"))
    target_price = _safe_float(metadata.get("target_price"))
    signal_entry_at = pd.Timestamp(record.get("signal_entry_at")) if pd.notna(record.get("signal_entry_at")) else None
    signal_exit_at = pd.Timestamp(record.get("signal_exit_at")) if pd.notna(record.get("signal_exit_at")) else None
    gates = _hard_gate_flags(record, variant)
    stop_concept = _compile_stop_concept(record)
    template_family = str(record.get("strategy_family") or "") if variant.compile_back_required else None
    return {
        "schema_version": LLM_ACTION_SCHEMA_VERSION,
        "action_id": f"{variant.controller_id}|{record.get('cluster_id')}|{record.get('signal_id')}",
        "controller_id": variant.controller_id,
        "workflow": variant.workflow,
        "decision_provider": "shadow_deterministic",
        "candidate_signal_id": record.get("signal_id"),
        "strategy_family": record.get("strategy_family"),
        "side": record.get("team_side"),
        "entry_condition": {
            "entry_rule": record.get("entry_rule"),
            "entry_state_index": _safe_int(record.get("entry_state_index")),
            "signal_entry_at": signal_entry_at.isoformat() if signal_entry_at is not None else None,
            "max_signal_age_seconds": variant.max_signal_age_seconds,
            "observed_signal_age_seconds": _safe_float(record.get("submit_signal_age_seconds")),
            "max_quote_age_seconds": variant.max_quote_age_seconds,
            "observed_quote_age_seconds": _safe_float(record.get("submit_quote_age_seconds")),
            "max_spread_cents": variant.max_spread_cents,
            "observed_spread_cents": _safe_float(record.get("submit_spread_cents")),
            "max_state_lag": variant.max_state_lag,
            "observed_state_lag": _safe_float(record.get("submit_state_lag")),
        },
        "exit_rule": {
            "exit_rule": record.get("exit_rule"),
            "exit_state_index": _safe_int(record.get("exit_state_index")),
            "signal_exit_at": signal_exit_at.isoformat() if signal_exit_at is not None else None,
            "target_price": target_price,
            "target_cents_from_rule": _parse_rule_cents(_PLUS_CENTS_PATTERN, record.get("exit_rule")),
        },
        "stop_concept": stop_concept,
        "confidence": {
            "selection_score": score,
            "confidence_band": _infer_confidence_band(score),
            "cluster_rank": cluster_rank,
            "cluster_viable_count": cluster_viable_count,
            "score_components": score_components,
        },
        "gating": {
            **gates,
            "target_window_pass": str(record.get("cluster_archetype") or "") in set(variant.llm_target_archetypes),
            "family_target_pass": _variant_family_allowed(record, variant),
            "compile_candidate_eligible_flag": bool(record.get("compile_candidate_eligible_flag")),
            "llm_call_recommended_flag": llm_call_recommended,
            "optional_ml_gate_score": _safe_float(record.get("ml_gate_score")),
            "optional_ml_rank_score": _safe_float(record.get("ml_rank_score")),
            "optional_ml_calibrated_confidence": _safe_float(record.get("ml_calibrated_confidence")),
            "optional_ml_calibrated_execution_likelihood": _safe_float(
                record.get("ml_calibrated_execution_likelihood")
            ),
            "optional_ml_focus_family_flag": _coerce_bool(record.get("ml_focus_family_flag")),
            "optional_ml_focus_family_tag": record.get("ml_focus_family_tag"),
            "optional_ml_selection_source": record.get("ml_selection_source"),
            "optional_ml_support_score": _optional_ml_support_score(record),
        },
        "state_context": {
            "cluster_id": record.get("cluster_id"),
            "cluster_archetype": record.get("cluster_archetype"),
            "game_id": record.get("game_id"),
            "team_slug": record.get("team_slug"),
            "opponent_team_slug": record.get("opponent_team_slug"),
            "opening_band": record.get("opening_band"),
            "period_label": record.get("state_period_label") or record.get("period_label"),
            "score_diff": _safe_float(record.get("state_score_diff")),
            "lead_changes_so_far": _safe_float(record.get("state_lead_changes_so_far")),
            "abs_price_delta_from_open": _safe_float(record.get("state_abs_price_delta_from_open")),
            "net_points_last_5_events": _safe_float(record.get("state_net_points_last_5_events")),
        },
        "compilation": {
            "selection_mode": variant.selection_mode,
            "compile_status": compile_status,
            "compile_back_required": variant.compile_back_required,
            "template_name": template_policy.get("template_name") if template_policy else None,
            "template_family": template_family,
            "template_family_priority": _safe_float(record.get("template_family_priority")),
            "compiled_from_existing_candidate": True,
            "compile_failure_reason": compile_failure_reason,
            "template_description": template_policy.get("description") if template_policy else None,
        },
    }


def _run_controller_variant(
    candidate_df: pd.DataFrame,
    *,
    variant: LLMVariantSpec,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    if candidate_df.empty:
        return pd.DataFrame(), pd.DataFrame(), []
    scored = candidate_df.copy()
    selected_df = pd.DataFrame()
    gate_columns = [
        "signal_age_pass",
        "quote_age_pass",
        "spread_pass",
        "state_lag_pass",
        "state_context_pass",
        "optional_ml_gate_pass",
        "hard_gate_pass",
    ]
    gate_rows = scored.apply(lambda row: pd.Series(_hard_gate_flags(row.to_dict(), variant)), axis=1)
    scored[gate_columns] = gate_rows[gate_columns]
    score_values: list[float] = []
    component_rows: list[dict[str, float]] = []
    for record in scored.to_dict(orient="records"):
        score, components = _score_record(record, variant)
        score_values.append(score)
        component_rows.append(components)
    scored["selection_score"] = score_values
    for key in (
        "actionability",
        "family_prior",
        "signal_strength",
        "state_fit",
        "template_fit",
        "price_dislocation",
        "archetype_severity",
        "core_family_fit",
        "optional_ml_support",
    ):
        scored[f"score_component_{key}"] = [components.get(key, 0.0) for components in component_rows]
    scored["variant_family_allowed"] = scored["score_component_core_family_fit"].astype(float) > 0.0

    decision_records: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for cluster_id, cluster_rows in scored.groupby("cluster_id", dropna=False, sort=False):
        cluster_work = cluster_rows.sort_values(
            ["hard_gate_pass", "variant_family_allowed", "selection_score", "signal_strength"],
            ascending=[False, False, False, False],
            kind="mergesort",
        ).copy()
        cluster_archetype = str(cluster_work["cluster_archetype"].iloc[0])
        archetype_allowed = cluster_archetype in set(variant.llm_target_archetypes)
        template_policy = _resolve_compile_template(cluster_archetype, variant)
        cluster_work["template_name"] = template_policy.get("template_name") if template_policy else None
        cluster_work["template_family_priority"] = cluster_work["strategy_family"].astype(str).map(
            lambda family: _template_family_priority_score(family, template_policy)
        )
        cluster_work["compile_candidate_eligible_flag"] = cluster_work["variant_family_allowed"].fillna(False)
        if variant.compile_back_required:
            cluster_work["compile_candidate_eligible_flag"] = (
                cluster_work["compile_candidate_eligible_flag"] & cluster_work["template_family_priority"].gt(0.0)
            )
        viable = cluster_work[cluster_work["hard_gate_pass"].fillna(False)].copy()
        eligible = viable[viable["compile_candidate_eligible_flag"].fillna(False)].copy()
        viable_count = int(len(viable))
        eligible_count = int(len(eligible))
        top_gap = None
        llm_call_recommended = False
        if archetype_allowed and eligible_count >= variant.llm_escalation_min_candidates:
            ranked_for_gap = eligible.sort_values(
                ["template_family_priority", "selection_score", "signal_strength"],
                ascending=[False, False, False],
                kind="mergesort",
            )
            top_scores = ranked_for_gap["selection_score"].astype(float).tolist()[:2]
            if len(top_scores) >= 2:
                top_gap = top_scores[0] - top_scores[1]
            llm_call_recommended = eligible_count > 1 and (top_gap is None or top_gap <= variant.llm_escalation_gap)

        selected_record: dict[str, Any] | None = None
        selected_score: float | None = None
        if archetype_allowed and not eligible.empty:
            ranked_eligible = eligible.sort_values(
                ["template_family_priority", "selection_score", "signal_strength"],
                ascending=[False, False, False],
                kind="mergesort",
            )
            candidate_record = ranked_eligible.iloc[0].to_dict()
            candidate_score = float(candidate_record.get("selection_score") or 0.0)
            if candidate_score >= variant.min_score:
                selected_record = candidate_record
                selected_score = candidate_score

        cluster_failure_reason = _derive_cluster_failure_reason(
            cluster_work,
            variant=variant,
            archetype_allowed=archetype_allowed,
            template_policy=template_policy,
            viable_df=viable,
            eligible_df=eligible,
            selected_score=selected_score,
        )
        prompt_payload = _build_prompt_payload(
            cluster_work,
            variant=variant,
            viable_count=viable_count,
            eligible_count=eligible_count,
            template_policy=template_policy,
        )
        for rank, record in enumerate(cluster_work.to_dict(orient="records"), start=1):
            decision_records.append(
                {
                    "controller_id": variant.controller_id,
                    "workflow": variant.workflow,
                    "hypothesis": variant.hypothesis,
                    "cluster_id": cluster_id,
                    "cluster_archetype": cluster_archetype,
                    "cluster_archetype_allowed": archetype_allowed,
                    "game_id": record.get("game_id"),
                    "signal_id": record.get("signal_id"),
                    "strategy_family": record.get("strategy_family"),
                    "team_side": record.get("team_side"),
                    "variant_family_allowed": bool(record.get("variant_family_allowed")),
                    "compile_back_required": variant.compile_back_required,
                    "template_name": template_policy.get("template_name") if template_policy else None,
                    "template_family_priority": _safe_float(record.get("template_family_priority")),
                    "compile_candidate_eligible_flag": bool(record.get("compile_candidate_eligible_flag")),
                    "cluster_failure_reason": cluster_failure_reason,
                    "hard_gate_pass": bool(record.get("hard_gate_pass")),
                    "signal_age_pass": bool(record.get("signal_age_pass")),
                    "quote_age_pass": bool(record.get("quote_age_pass")),
                    "spread_pass": bool(record.get("spread_pass")),
                    "state_lag_pass": bool(record.get("state_lag_pass")),
                    "state_context_pass": bool(record.get("state_context_pass")),
                    "optional_ml_gate_pass": bool(record.get("optional_ml_gate_pass")),
                    "optional_ml_focus_family_flag": _coerce_bool(record.get("ml_focus_family_flag")),
                    "optional_ml_focus_family_tag": record.get("ml_focus_family_tag"),
                    "optional_ml_selection_source": record.get("ml_selection_source"),
                    "optional_ml_calibrated_execution_likelihood": _safe_float(
                        record.get("ml_calibrated_execution_likelihood")
                    ),
                    "selection_score": _safe_float(record.get("selection_score")),
                    "llm_call_recommended_flag": llm_call_recommended,
                    "cluster_rank": rank,
                    "viable_candidate_count": viable_count,
                    "eligible_candidate_count": eligible_count,
                    "score_component_actionability": _safe_float(record.get("score_component_actionability")),
                    "score_component_family_prior": _safe_float(record.get("score_component_family_prior")),
                    "score_component_signal_strength": _safe_float(record.get("score_component_signal_strength")),
                    "score_component_state_fit": _safe_float(record.get("score_component_state_fit")),
                    "score_component_template_fit": _safe_float(record.get("score_component_template_fit")),
                    "score_component_price_dislocation": _safe_float(record.get("score_component_price_dislocation")),
                    "score_component_archetype_severity": _safe_float(record.get("score_component_archetype_severity")),
                    "score_component_core_family_fit": _safe_float(record.get("score_component_core_family_fit")),
                    "score_component_optional_ml_support": _safe_float(record.get("score_component_optional_ml_support")),
                    "prompt_payload_json": json.dumps(to_jsonable(prompt_payload), sort_keys=True),
                }
            )
        if selected_record is None:
            continue
        score_components = {
            "actionability": float(selected_record.get("score_component_actionability") or 0.0),
            "family_prior": float(selected_record.get("score_component_family_prior") or 0.0),
            "signal_strength": float(selected_record.get("score_component_signal_strength") or 0.0),
            "state_fit": float(selected_record.get("score_component_state_fit") or 0.0),
            "template_fit": float(selected_record.get("score_component_template_fit") or 0.0),
            "price_dislocation": float(selected_record.get("score_component_price_dislocation") or 0.0),
            "archetype_severity": float(selected_record.get("score_component_archetype_severity") or 0.0),
            "core_family_fit": float(selected_record.get("score_component_core_family_fit") or 0.0),
            "optional_ml_support": float(selected_record.get("score_component_optional_ml_support") or 0.0),
        }
        action = _compile_action(
            selected_record,
            variant=variant,
            template_policy=template_policy,
            cluster_rank=1,
            cluster_viable_count=viable_count,
            llm_call_recommended=llm_call_recommended,
            score=float(selected_score or 0.0),
            score_components=score_components,
            compile_status="compiled_from_template" if variant.compile_back_required else "selected_existing_candidate",
            compile_failure_reason=cluster_failure_reason,
        )
        selected_record["compiled_action"] = action
        selected_record["compiled_action_json"] = json.dumps(to_jsonable(action), sort_keys=True)
        selected_record["llm_call_recommended_flag"] = llm_call_recommended
        selected_record["cluster_failure_reason"] = cluster_failure_reason
        selected_rows.append(selected_record)

        selected_df = pd.DataFrame(selected_rows)
    if not selected_df.empty:
        selected_df = selected_df.sort_values(
            ["selection_score", "signal_entry_at"],
            ascending=[False, True],
            kind="mergesort",
        ).head(variant.max_actions_total)
        selected_signal_ids = set(selected_df["signal_id"].astype(str))
    else:
        selected_signal_ids = set()

    decision_df = pd.DataFrame(decision_records)
    if not decision_df.empty:
        decision_df["selected_flag"] = decision_df["signal_id"].astype(str).isin(selected_signal_ids)
        decision_df["decision_status"] = np.where(
            decision_df["selected_flag"],
            "selected",
            np.where(
                decision_df["cluster_archetype_allowed"].fillna(False).eq(False),
                "state_window_not_targeted",
                np.where(
                    decision_df["variant_family_allowed"].fillna(False).eq(False),
                    "family_not_targeted",
                    np.where(
                        decision_df["hard_gate_pass"].fillna(False).eq(False),
                        "filtered_by_replay_gate",
                        np.where(
                            decision_df["compile_candidate_eligible_flag"].fillna(False).eq(False),
                            "compile_family_not_supported",
                            decision_df["cluster_failure_reason"].fillna("viable_not_selected"),
                        ),
                    ),
                ),
            ),
        )
    return scored, decision_df, list(to_jsonable(selected_df.to_dict(orient="records")))


def _build_selected_trade_frames(selected_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if selected_df.empty:
        empty = pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
        return empty.copy(), empty.copy()
    standard_columns = [column for column in selected_df.columns if column.startswith("standard_")]
    replay_columns = [column for column in selected_df.columns if column.startswith("replay_")]
    standard_df = (
        selected_df[standard_columns]
        .rename(columns={column: column[len("standard_") :] for column in standard_columns})
        .drop(columns=["signal_id"], errors="ignore")
        .copy()
    )
    replay_df = (
        selected_df[selected_df["executed_flag"].fillna(False)][replay_columns]
        .rename(columns={column: column[len("replay_") :] for column in replay_columns})
        .drop(columns=["signal_id"], errors="ignore")
        .copy()
    )
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
    selected_df: pd.DataFrame,
    standard_df: pd.DataFrame,
    replay_df: pd.DataFrame,
) -> dict[str, Any]:
    family_members = tuple(sorted(set(selected_df["strategy_family"].astype(str)))) if not selected_df.empty else tuple()
    standard_summary, _ = simulate_trade_portfolio(
        standard_df,
        sample_name="llm_lane",
        strategy_family=subject_name,
        portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=family_members,
        initial_bankroll=INITIAL_BANKROLL,
        position_size_fraction=POSITION_SIZE_FRACTION,
        game_limit=100,
        min_order_dollars=MIN_ORDER_DOLLARS,
        min_shares=MIN_SHARES,
        max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="dynamic_concurrent_games",
        target_exposure_fraction=TARGET_EXPOSURE_FRACTION,
        random_slippage_max_cents=0,
        random_slippage_seed=RANDOM_SLIPPAGE_SEED,
    )
    replay_summary, _ = simulate_trade_portfolio(
        replay_df,
        sample_name="llm_lane",
        strategy_family=subject_name,
        portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=family_members,
        initial_bankroll=INITIAL_BANKROLL,
        position_size_fraction=POSITION_SIZE_FRACTION,
        game_limit=100,
        min_order_dollars=MIN_ORDER_DOLLARS,
        min_shares=MIN_SHARES,
        max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="dynamic_concurrent_games",
        target_exposure_fraction=TARGET_EXPOSURE_FRACTION,
        random_slippage_max_cents=0,
        random_slippage_seed=RANDOM_SLIPPAGE_SEED,
    )
    standard_trade_count = int(len(standard_df))
    replay_trade_count = int(len(replay_df))
    blocked_signal_count = int(max(0, len(selected_df) - replay_trade_count))
    stale_signal_suppressed_count = int(
        selected_df.loc[
            ~selected_df["executed_flag"].fillna(False)
            & selected_df["no_trade_reason"].fillna("").astype(str).eq("signal_stale")
        ].shape[0]
    )
    no_trade_reasons = (
        selected_df.loc[~selected_df["executed_flag"].fillna(False), "no_trade_reason"]
        .fillna("none")
        .astype(str)
    )
    top_reason = None if no_trade_reasons.empty else str(no_trade_reasons.value_counts().index[0])
    execution_rate = (
        float(replay_trade_count) / float(standard_trade_count)
        if standard_trade_count > 0
        else None
    )
    realism_gap = (
        float(standard_trade_count - replay_trade_count) / float(standard_trade_count)
        if standard_trade_count > 0
        else None
    )
    stale_rate = (
        float(stale_signal_suppressed_count) / float(standard_trade_count)
        if standard_trade_count > 0
        else 0.0
    )
    stale_share = (
        float(stale_signal_suppressed_count) / float(blocked_signal_count)
        if blocked_signal_count > 0
        else 0.0
    )
    return {
        "candidate_id": subject_name,
        "display_name": subject_name,
        "candidate_kind": "llm_strategy",
        "subject_type": "candidate",
        "publication_state": "published",
        "comparison_ready_flag": True,
        "metrics": {
            "standard_trade_count": standard_trade_count,
            "replay_trade_count": replay_trade_count,
            "live_trade_count": 0,
            "trade_gap": replay_trade_count - standard_trade_count,
            "execution_rate": execution_rate,
            "realism_gap_trade_rate": realism_gap,
            "stale_signal_suppressed_count": stale_signal_suppressed_count,
            "stale_signal_suppression_rate": stale_rate,
            "stale_signal_share_of_blocked_signals": stale_share,
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
            f"Selected families: {', '.join(family_members) if family_members else 'none'}",
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
            "execution_rate",
            "replay_ending_bankroll",
            "standard_ending_bankroll",
            "trade_gap",
            "top_no_trade_reason",
        ]
    ]


def _build_benchmark_comparison_frame(
    subject_summary_df: pd.DataFrame,
    llm_subject_payloads: list[dict[str, Any]],
) -> pd.DataFrame:
    baseline_df = _comparison_rows_from_replay_subject_summary(subject_summary_df)
    llm_rows = [
        {
            "candidate_id": subject["candidate_id"],
            "display_name": subject["display_name"],
            "candidate_kind": subject["candidate_kind"],
            "subject_type": subject["subject_type"],
            "standard_trade_count": subject["metrics"].get("standard_trade_count"),
            "replay_trade_count": subject["metrics"].get("replay_trade_count"),
            "execution_rate": subject["metrics"].get("execution_rate"),
            "replay_ending_bankroll": subject["metrics"].get("replay_ending_bankroll"),
            "standard_ending_bankroll": subject["metrics"].get("standard_ending_bankroll"),
            "trade_gap": subject["metrics"].get("trade_gap"),
            "top_no_trade_reason": subject["metrics"].get("top_no_trade_reason"),
        }
        for subject in llm_subject_payloads
    ]
    llm_df = pd.DataFrame(llm_rows)
    combined = pd.concat([baseline_df, llm_df], ignore_index=True, sort=False)
    combined["replay_rank"] = pd.to_numeric(combined["replay_ending_bankroll"], errors="coerce").rank(
        method="dense",
        ascending=False,
    )
    return combined.sort_values(["candidate_kind", "replay_rank", "candidate_id"], kind="mergesort").reset_index(drop=True)


def _build_focused_comparison_frame(comparison_df: pd.DataFrame) -> pd.DataFrame:
    if comparison_df.empty:
        return pd.DataFrame()
    focus_ids = set(FOCUSED_BASELINE_IDS) | set(
        comparison_df.loc[comparison_df["candidate_kind"].astype(str).eq("llm_strategy"), "candidate_id"].astype(str)
    )
    focused = comparison_df[comparison_df["candidate_id"].astype(str).isin(focus_ids)].copy()
    return focused.sort_values(["replay_rank", "candidate_id"], kind="mergesort").reset_index(drop=True)


def _simulate_selected_replay_portfolio(subject_name: str, selected_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    _, replay_df = _build_selected_trade_frames(selected_df)
    family_members = tuple(sorted(set(selected_df["strategy_family"].astype(str)))) if not selected_df.empty else tuple()
    replay_summary, _ = simulate_trade_portfolio(
        replay_df,
        sample_name="llm_lane",
        strategy_family=subject_name,
        portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
        strategy_family_members=family_members,
        initial_bankroll=INITIAL_BANKROLL,
        position_size_fraction=POSITION_SIZE_FRACTION,
        game_limit=100,
        min_order_dollars=MIN_ORDER_DOLLARS,
        min_shares=MIN_SHARES,
        max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="dynamic_concurrent_games",
        target_exposure_fraction=TARGET_EXPOSURE_FRACTION,
        random_slippage_max_cents=0,
        random_slippage_seed=RANDOM_SLIPPAGE_SEED,
    )
    return replay_df, replay_summary


def _build_variant_robustness_payload(
    selected_frames: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    robustness_rows: list[dict[str, Any]] = []
    leave_one_out_rows: list[dict[str, Any]] = []
    for controller_id, selected_df in selected_frames.items():
        executed_selected = selected_df[selected_df["executed_flag"].fillna(False)].copy() if not selected_df.empty else pd.DataFrame()
        replay_returns = (
            pd.to_numeric(executed_selected["replay_gross_return_with_slippage"], errors="coerce").fillna(0.0)
            if not executed_selected.empty and "replay_gross_return_with_slippage" in executed_selected.columns
            else pd.Series(dtype=float, index=executed_selected.index)
        )
        executed_selected["replay_gross_return_with_slippage"] = replay_returns
        total_return = float(executed_selected["replay_gross_return_with_slippage"].sum()) if not executed_selected.empty else 0.0
        cluster_returns = (
            executed_selected.groupby("cluster_id", dropna=False)["replay_gross_return_with_slippage"].sum()
            if not executed_selected.empty
            else pd.Series(dtype=float)
        )
        family_returns = (
            executed_selected.groupby("strategy_family", dropna=False)["replay_gross_return_with_slippage"].sum()
            if not executed_selected.empty
            else pd.Series(dtype=float)
        )
        top_trade_share = None
        top_cluster_share = None
        top_family_share = None
        trade_return_hhi = None
        family_return_hhi = None
        dominant_family = None
        if total_return > 0.0 and not executed_selected.empty:
            top_trade_share = float(executed_selected["replay_gross_return_with_slippage"].max() / total_return)
            trade_shares = executed_selected["replay_gross_return_with_slippage"].clip(lower=0.0).div(total_return)
            trade_return_hhi = float(trade_shares.pow(2).sum())
        if total_return > 0.0 and not cluster_returns.empty:
            top_cluster_share = float(cluster_returns.max() / total_return)
        if total_return > 0.0 and not family_returns.empty:
            top_family_share = float(family_returns.max() / total_return)
            dominant_family = str(family_returns.sort_values(ascending=False).index[0])
            family_shares = family_returns.clip(lower=0.0).div(total_return)
            family_return_hhi = float(family_shares.pow(2).sum())

        focus_family_alignment_rate = (
            float(selected_df["strategy_family"].astype(str).isin(CORE_LLM_REPLAY_FAMILIES).mean())
            if not selected_df.empty
            else None
        )

        leave_one_out_bankrolls: list[float] = []
        for drop_index, drop_row in executed_selected.iterrows():
            reduced_selected = selected_df.drop(index=drop_index)
            _, replay_summary = _simulate_selected_replay_portfolio(controller_id, reduced_selected)
            leave_one_out_bankroll = _safe_float(replay_summary.get("ending_bankroll"))
            if leave_one_out_bankroll is not None:
                leave_one_out_bankrolls.append(leave_one_out_bankroll)
            leave_one_out_rows.append(
                {
                    "controller_id": controller_id,
                    "removed_signal_id": drop_row.get("signal_id"),
                    "removed_cluster_id": drop_row.get("cluster_id"),
                    "removed_strategy_family": drop_row.get("strategy_family"),
                    "removed_replay_return": _safe_float(drop_row.get("replay_gross_return_with_slippage")),
                    "replay_ending_bankroll_without_signal": leave_one_out_bankroll,
                }
            )

        robustness_rows.append(
            {
                "controller_id": controller_id,
                "selected_action_count": int(len(selected_df)),
                "executed_action_count": int(len(executed_selected)),
                "selected_cluster_count": int(selected_df["cluster_id"].nunique()) if not selected_df.empty else 0,
                "executed_cluster_count": int(executed_selected["cluster_id"].nunique()) if not executed_selected.empty else 0,
                "selected_family_count": int(selected_df["strategy_family"].astype(str).nunique()) if not selected_df.empty else 0,
                "executed_family_count": int(executed_selected["strategy_family"].astype(str).nunique())
                if not executed_selected.empty
                else 0,
                "focus_family_alignment_rate": focus_family_alignment_rate,
                "top_trade_return_share": top_trade_share,
                "top_cluster_return_share": top_cluster_share,
                "top_family_return_share": top_family_share,
                "trade_return_hhi": trade_return_hhi,
                "family_return_hhi": family_return_hhi,
                "dominant_family": dominant_family,
                "leave_one_out_min_bankroll": min(leave_one_out_bankrolls) if leave_one_out_bankrolls else None,
                "leave_one_out_avg_bankroll": (
                    float(sum(leave_one_out_bankrolls) / len(leave_one_out_bankrolls)) if leave_one_out_bankrolls else None
                ),
                "narrow_dependency_flag": bool(
                    (top_trade_share is not None and top_trade_share >= 0.45)
                    or (top_cluster_share is not None and top_cluster_share >= 0.55)
                    or (top_family_share is not None and top_family_share >= 0.75)
                ),
            }
        )
    return pd.DataFrame(robustness_rows), pd.DataFrame(leave_one_out_rows)


def _build_head_to_head_summary(
    focused_comparison_df: pd.DataFrame,
    *,
    best_variant_id: str | None,
) -> list[dict[str, Any]]:
    if focused_comparison_df.empty or not best_variant_id:
        return []
    lookup = focused_comparison_df.set_index("candidate_id", drop=False).to_dict(orient="index")
    best_row = lookup.get(best_variant_id)
    if best_row is None:
        return []
    best_bankroll = _safe_float(best_row.get("replay_ending_bankroll"))
    rows: list[dict[str, Any]] = []
    for candidate_id in FOCUSED_BASELINE_IDS:
        comparison_row = lookup.get(candidate_id)
        if comparison_row is None:
            continue
        baseline_bankroll = _safe_float(comparison_row.get("replay_ending_bankroll"))
        rows.append(
            {
                "best_variant_id": best_variant_id,
                "comparison_candidate_id": candidate_id,
                "comparison_candidate_kind": comparison_row.get("candidate_kind"),
                "comparison_replay_ending_bankroll": baseline_bankroll,
                "best_minus_comparison_replay_ending_bankroll": (
                    best_bankroll - baseline_bankroll if best_bankroll is not None and baseline_bankroll is not None else None
                ),
                "comparison_execution_rate": _safe_float(comparison_row.get("execution_rate")),
                "comparison_replay_trade_count": _safe_int(comparison_row.get("replay_trade_count")),
            }
        )
    return rows


def _evaluate_live_probe_promotion(
    *,
    best_variant: dict[str, Any],
    best_robustness: dict[str, Any],
    inversion_bankroll: float | None,
    focused_baseline_ceiling: float | None,
) -> dict[str, Any]:
    thresholds = dict(LIVE_PROBE_PROMOTION_THRESHOLDS)
    best_replay_bankroll = _safe_float(best_variant.get("replay_ending_bankroll"))
    leave_one_out_min_bankroll = _safe_float(best_robustness.get("leave_one_out_min_bankroll"))
    leave_one_out_vs_inversion = (
        leave_one_out_min_bankroll / inversion_bankroll
        if leave_one_out_min_bankroll is not None and inversion_bankroll not in {None, 0.0}
        else None
    )
    metrics = {
        "replay_trade_count": _safe_int(best_variant.get("replay_trade_count")),
        "executed_cluster_count": _safe_int(best_robustness.get("executed_cluster_count")),
        "executed_family_count": _safe_int(best_robustness.get("executed_family_count")),
        "focus_family_alignment_rate": _safe_float(best_robustness.get("focus_family_alignment_rate")),
        "top_trade_return_share": _safe_float(best_robustness.get("top_trade_return_share")),
        "top_cluster_return_share": _safe_float(best_robustness.get("top_cluster_return_share")),
        "top_family_return_share": _safe_float(best_robustness.get("top_family_return_share")),
        "leave_one_out_min_bankroll": leave_one_out_min_bankroll,
        "leave_one_out_min_bankroll_vs_inversion": leave_one_out_vs_inversion,
    }
    checks = [
        {
            "metric": "replay_trade_count",
            "operator": ">=",
            "threshold": thresholds["min_replay_trade_count"],
            "actual": metrics["replay_trade_count"],
            "pass": metrics["replay_trade_count"] is not None
            and metrics["replay_trade_count"] >= thresholds["min_replay_trade_count"],
        },
        {
            "metric": "executed_cluster_count",
            "operator": ">=",
            "threshold": thresholds["min_executed_cluster_count"],
            "actual": metrics["executed_cluster_count"],
            "pass": metrics["executed_cluster_count"] is not None
            and metrics["executed_cluster_count"] >= thresholds["min_executed_cluster_count"],
        },
        {
            "metric": "executed_family_count",
            "operator": ">=",
            "threshold": thresholds["min_executed_family_count"],
            "actual": metrics["executed_family_count"],
            "pass": metrics["executed_family_count"] is not None
            and metrics["executed_family_count"] >= thresholds["min_executed_family_count"],
        },
        {
            "metric": "focus_family_alignment_rate",
            "operator": ">=",
            "threshold": thresholds["min_focus_family_alignment_rate"],
            "actual": metrics["focus_family_alignment_rate"],
            "pass": metrics["focus_family_alignment_rate"] is not None
            and metrics["focus_family_alignment_rate"] >= thresholds["min_focus_family_alignment_rate"],
        },
        {
            "metric": "top_trade_return_share",
            "operator": "<=",
            "threshold": thresholds["max_top_trade_return_share"],
            "actual": metrics["top_trade_return_share"],
            "pass": metrics["top_trade_return_share"] is not None
            and metrics["top_trade_return_share"] <= thresholds["max_top_trade_return_share"],
        },
        {
            "metric": "top_cluster_return_share",
            "operator": "<=",
            "threshold": thresholds["max_top_cluster_return_share"],
            "actual": metrics["top_cluster_return_share"],
            "pass": metrics["top_cluster_return_share"] is not None
            and metrics["top_cluster_return_share"] <= thresholds["max_top_cluster_return_share"],
        },
        {
            "metric": "top_family_return_share",
            "operator": "<=",
            "threshold": thresholds["max_top_family_return_share"],
            "actual": metrics["top_family_return_share"],
            "pass": metrics["top_family_return_share"] is not None
            and metrics["top_family_return_share"] <= thresholds["max_top_family_return_share"],
        },
        {
            "metric": "leave_one_out_min_bankroll_vs_inversion",
            "operator": ">=",
            "threshold": thresholds["min_leave_one_out_bankroll_vs_inversion"],
            "actual": metrics["leave_one_out_min_bankroll_vs_inversion"],
            "pass": metrics["leave_one_out_min_bankroll_vs_inversion"] is not None
            and metrics["leave_one_out_min_bankroll_vs_inversion"]
            >= thresholds["min_leave_one_out_bankroll_vs_inversion"],
        },
    ]
    baseline_clearance = {
        "best_replay_ending_bankroll": best_replay_bankroll,
        "inversion_replay_ending_bankroll": inversion_bankroll,
        "focused_baseline_ceiling": focused_baseline_ceiling,
        "beats_inversion_flag": best_replay_bankroll is not None
        and inversion_bankroll is not None
        and best_replay_bankroll > inversion_bankroll,
        "beats_focused_baseline_ceiling_flag": best_replay_bankroll is not None
        and focused_baseline_ceiling is not None
        and best_replay_bankroll > focused_baseline_ceiling,
    }
    live_probe_eligible_flag = baseline_clearance["beats_inversion_flag"] and baseline_clearance[
        "beats_focused_baseline_ceiling_flag"
    ] and all(check["pass"] for check in checks)
    return {
        "live_probe_eligible_flag": live_probe_eligible_flag,
        "thresholds": thresholds,
        "metrics": metrics,
        "checks": checks,
        "failed_checks": [check["metric"] for check in checks if not check["pass"]],
        "baseline_clearance": baseline_clearance,
    }


def _determine_lane_recommendation(
    variant_summary_rows: list[dict[str, Any]],
    robustness_df: pd.DataFrame,
    focused_comparison_df: pd.DataFrame,
) -> dict[str, Any]:
    if not variant_summary_rows:
        return {
            "best_variant_id": None,
            "alternate_variant_id": None,
            "deployment_recommendation": "bench",
            "reason": "No LLM controller variants produced a replay-aware benchmark result.",
            "promotion_gate": {
                "live_probe_eligible_flag": False,
                "thresholds": dict(LIVE_PROBE_PROMOTION_THRESHOLDS),
                "checks": [],
                "failed_checks": ["no_variant_results"],
                "baseline_clearance": {},
            },
        }

    variant_summary_df = pd.DataFrame(variant_summary_rows).sort_values(
        ["replay_ending_bankroll", "replay_trade_count"],
        ascending=[False, False],
        kind="mergesort",
    )
    best_variant = variant_summary_df.iloc[0].to_dict()
    best_variant_id = str(best_variant.get("controller_id") or "")
    alternate_variant_id = (
        str(variant_summary_df.iloc[1].get("controller_id") or "") if len(variant_summary_df) > 1 else None
    )
    best_replay_bankroll = _safe_float(best_variant.get("replay_ending_bankroll"))
    best_replay_trade_count = _safe_int(best_variant.get("replay_trade_count")) or 0

    robustness_lookup = (
        robustness_df.set_index("controller_id", drop=False).to_dict(orient="index") if not robustness_df.empty else {}
    )
    best_robustness = robustness_lookup.get(best_variant_id, {})

    focused_lookup = (
        focused_comparison_df.set_index("candidate_id", drop=False).to_dict(orient="index")
        if not focused_comparison_df.empty
        else {}
    )
    inversion_bankroll = _safe_float((focused_lookup.get("inversion") or {}).get("replay_ending_bankroll"))
    focused_baseline_bankrolls = [
        _safe_float((focused_lookup.get(candidate_id) or {}).get("replay_ending_bankroll"))
        for candidate_id in FOCUSED_BASELINE_IDS
    ]
    focused_baseline_ceiling = max((value for value in focused_baseline_bankrolls if value is not None), default=None)
    promotion_gate = _evaluate_live_probe_promotion(
        best_variant=best_variant,
        best_robustness=best_robustness,
        inversion_bankroll=inversion_bankroll,
        focused_baseline_ceiling=focused_baseline_ceiling,
    )
    beats_inversion = bool((promotion_gate.get("baseline_clearance") or {}).get("beats_inversion_flag"))

    deployment_recommendation = "bench"
    if beats_inversion:
        deployment_recommendation = "shadow"
        if bool(promotion_gate.get("live_probe_eligible_flag")):
            deployment_recommendation = "live-probe"

    failed_checks = promotion_gate.get("failed_checks") or []
    failed_checks_text = ", ".join(failed_checks[:4]) if failed_checks else "none"

    if deployment_recommendation == "live-probe":
        reason = (
            f"{best_variant_id} clears the focused replay baselines and the concentration gate, so a tiny live probe is justified."
        )
    elif deployment_recommendation == "shadow":
        reason = (
            f"{best_variant_id} stays replay-executable and clears inversion, but it remains shadow-only because the live-probe gate still fails on {failed_checks_text}."
        )
    else:
        if best_replay_bankroll is None or inversion_bankroll is None or not beats_inversion:
            reason = f"{best_variant_id} does not clear inversion decisively enough on replay, so it remains benchmark-only."
        else:
            reason = (
                f"{best_variant_id} is still best used as a benchmark artifact because replay breadth is too thin to justify shadow validation."
            )

    return {
        "best_variant_id": best_variant_id,
        "alternate_variant_id": alternate_variant_id,
        "deployment_recommendation": deployment_recommendation,
        "reason": reason,
        "promotion_gate": promotion_gate,
    }


def build_llm_action_schema() -> dict[str, Any]:
    return {
        "schema_version": LLM_ACTION_SCHEMA_VERSION,
        "description": "Executable action schema for replay-aware LLM strategy decisions.",
        "required_top_level_fields": [
            "action_id",
            "controller_id",
            "workflow",
            "candidate_signal_id",
            "strategy_family",
            "side",
            "entry_condition",
            "exit_rule",
            "stop_concept",
            "confidence",
            "gating",
            "state_context",
            "compilation",
        ],
        "strategy_family_enum": sorted(FAMILY_FALLBACK_ARCHETYPE),
        "workflow_enum": sorted({variant.workflow for variant in LLM_CONTROLLER_VARIANTS}),
        "side_enum": ["home", "away"],
        "confidence_band_enum": ["low", "medium", "high", "very_high"],
        "compile_status_enum": ["selected_existing_candidate", "compiled_from_template"],
        "compile_template_enum": sorted(COMPILE_TEMPLATE_POLICIES),
        "shadow_primary_target_families": list(CORE_LLM_REPLAY_FAMILIES),
        "entry_condition_required_fields": [
            "entry_rule",
            "entry_state_index",
            "signal_entry_at",
            "max_signal_age_seconds",
            "observed_signal_age_seconds",
            "max_quote_age_seconds",
            "observed_quote_age_seconds",
            "max_spread_cents",
            "observed_spread_cents",
            "max_state_lag",
            "observed_state_lag",
        ],
        "exit_rule_required_fields": [
            "exit_rule",
            "exit_state_index",
            "signal_exit_at",
        ],
        "stop_concept_required_fields": [
            "mode",
            "description",
        ],
        "gating_required_fields": [
            "signal_age_pass",
            "quote_age_pass",
            "spread_pass",
            "state_lag_pass",
            "state_context_pass",
            "target_window_pass",
            "family_target_pass",
            "compile_candidate_eligible_flag",
            "optional_ml_gate_pass",
            "hard_gate_pass",
            "llm_call_recommended_flag",
        ],
        "gating_optional_helper_fields": [
            "optional_ml_gate_score",
            "optional_ml_rank_score",
            "optional_ml_calibrated_confidence",
            "optional_ml_calibrated_execution_likelihood",
            "optional_ml_focus_family_flag",
            "optional_ml_focus_family_tag",
            "optional_ml_selection_source",
            "optional_ml_support_score",
        ],
        "compilation_required_fields": [
            "selection_mode",
            "compile_status",
            "compile_back_required",
            "compiled_from_existing_candidate",
        ],
        "compilation_contract": {
            "compiled_from_existing_candidate": True,
            "free_generation_allowed": False,
            "selection_must_reference_existing_signal_id": True,
            "evaluation_mode": "selected_candidate_reuses_existing_standard_and_replay_trade_rows",
        },
    }


def build_llm_prompt_contracts() -> dict[str, Any]:
    contracts = []
    for variant in LLM_CONTROLLER_VARIANTS:
        template_contracts = [
            {
                "template_name": template_name,
                "description": COMPILE_TEMPLATE_POLICIES[template_name]["description"],
                "target_archetypes": list(COMPILE_TEMPLATE_POLICIES[template_name]["target_archetypes"]),
                "preferred_families": list(COMPILE_TEMPLATE_POLICIES[template_name]["preferred_families"]),
            }
            for template_name in variant.compile_template_names
            if template_name in COMPILE_TEMPLATE_POLICIES
        ]
        contracts.append(
            {
                "schema_version": LLM_PROMPT_CONTRACT_VERSION,
                "controller_id": variant.controller_id,
                "workflow": variant.workflow,
                "selection_mode": variant.selection_mode,
                "hypothesis": variant.hypothesis,
                "decision_provider_default": "shadow_deterministic",
                "system_prompt": (
                    "Select at most one replay-executable action from the provided candidate set. "
                    "Return JSON only. Do not invent a new signal_id. If a compile template is provided, "
                    "fail closed unless you can map it back to an allowed existing candidate."
                ),
                "input_contract": {
                    "cluster_id": "string",
                    "game_id": "string",
                    "archetype": sorted(ARCHETYPE_FAMILY_PREFERENCES),
                    "shadow_primary_target_families": list(CORE_LLM_REPLAY_FAMILIES),
                    "target_archetypes": list(variant.llm_target_archetypes),
                    "allowed_families": list(variant.allowed_families),
                    "preferred_families": list(variant.preferred_families),
                    "template_contracts": template_contracts,
                    "constraints": {
                        "max_signal_age_seconds": variant.max_signal_age_seconds,
                        "max_quote_age_seconds": variant.max_quote_age_seconds,
                        "max_spread_cents": variant.max_spread_cents,
                        "max_state_lag": variant.max_state_lag,
                        "must_map_to_existing_candidate_signal_id": True,
                        "compile_back_required": variant.compile_back_required,
                        "optional_ml_is_not_required": True,
                    },
                    "candidate_fields": [
                        "signal_id",
                        "strategy_family",
                        "side",
                        "family_allowed",
                        "template_priority",
                        "compile_candidate_eligible",
                        "selection_score",
                        "entry_rule",
                        "exit_rule",
                        "signal_strength",
                        "submit_signal_age_seconds",
                        "submit_quote_age_seconds",
                        "submit_spread_cents",
                        "submit_state_lag",
                        "state_archetype",
                        "score_diff",
                        "lead_changes_so_far",
                        "abs_price_delta_from_open",
                        "optional_ml_gate_score",
                        "optional_ml_rank_score",
                        "optional_ml_calibrated_confidence",
                        "optional_ml_calibrated_execution_likelihood",
                        "optional_ml_focus_family_flag",
                        "optional_ml_focus_family_tag",
                        "optional_ml_selection_source",
                        "optional_ml_support_score",
                    ],
                },
                "output_contract": {
                    "must_use_action_schema_version": LLM_ACTION_SCHEMA_VERSION,
                    "must_return_json_only": True,
                    "must_include_fields": [
                        "candidate_signal_id",
                        "strategy_family",
                        "side",
                        "entry_condition",
                        "exit_rule",
                        "stop_concept",
                        "confidence",
                        "gating",
                        "compilation",
                    ],
                },
                "llm_call_policy": {
                    "call_only_when": {
                        "cluster_has_multiple_viable_candidates": True,
                        "top_score_gap_lte": variant.llm_escalation_gap,
                        "target_archetypes": list(variant.llm_target_archetypes),
                    },
                    "skip_when": [
                        "no_candidate_passes_the_replay_freshness_gate",
                        "no_candidate_can_compile_back_to_an_allowed_signal",
                        "deterministic_top_candidate_is_clear_and_uncontested",
                    ],
                },
            }
        )
    return {
        "schema_version": LLM_PROMPT_CONTRACT_VERSION,
        "controller_contracts": contracts,
    }


def _build_shadow_sidecar_payload(
    *,
    published_at: str,
    season: str,
    variant_summary_rows: list[dict[str, Any]],
    subject_artifact_paths: dict[str, dict[str, str]],
    recommendation: dict[str, Any],
    robustness_df: pd.DataFrame,
    focused_comparison_df: pd.DataFrame,
    ml_summary: dict[str, Any],
) -> dict[str, Any]:
    variant_lookup = {variant.controller_id: variant for variant in LLM_CONTROLLER_VARIANTS}
    ranked_variants = sorted(
        variant_summary_rows,
        key=lambda row: (
            _safe_float(row.get("replay_ending_bankroll")) or float("-inf"),
            _safe_float(row.get("replay_trade_count")) or float("-inf"),
        ),
        reverse=True,
    )
    robustness_lookup = (
        robustness_df.set_index("controller_id", drop=False).to_dict(orient="index") if not robustness_df.empty else {}
    )
    focus_lookup = (
        focused_comparison_df.set_index("candidate_id", drop=False).to_dict(orient="index")
        if not focused_comparison_df.empty
        else {}
    )
    focused_baselines = [
        focus_lookup[candidate_id]
        for candidate_id in FOCUSED_BASELINE_IDS
        if candidate_id in focus_lookup
    ]
    variant_payloads: list[dict[str, Any]] = []
    for row in ranked_variants:
        controller_id = str(row.get("controller_id") or "")
        variant = variant_lookup.get(controller_id)
        artifact_paths = subject_artifact_paths.get(controller_id, {})
        variant_payloads.append(
            {
                "controller_id": controller_id,
                "workflow": row.get("workflow"),
                "selection_mode": variant.selection_mode if variant else None,
                "replay_trade_count": row.get("replay_trade_count"),
                "replay_ending_bankroll": row.get("replay_ending_bankroll"),
                "execution_rate": row.get("execution_rate"),
                "primary_target_families": list(variant.preferred_families) if variant else [],
                "decision_trace_json": artifact_paths.get("decision_trace_json"),
                "selected_actions_json": artifact_paths.get("selected_actions_json"),
                "scored_candidates_csv": artifact_paths.get("scored_candidates_csv"),
                "robustness": to_jsonable(robustness_lookup.get(controller_id, {})),
            }
        )
    best_variant_id = recommendation.get("best_variant_id")
    return {
        "schema_version": LLM_SHADOW_PAYLOAD_VERSION,
        "published_at": published_at,
        "lane_id": LLM_LANE_ID,
        "season": season,
        "mode": "shadow_sidecar",
        "deployment_recommendation": recommendation.get("deployment_recommendation"),
        "best_variant_id": best_variant_id,
        "alternate_variant_id": recommendation.get("alternate_variant_id"),
        "active_variants": [str(row.get("controller_id") or "") for row in ranked_variants],
        "primary_target_families": list(CORE_LLM_REPLAY_FAMILIES),
        "target_archetypes": list(CORE_LLM_TARGET_ARCHETYPES),
        "optional_ml_policy": {
            "required": False,
            "helper_roles": [
                "calibrated_confidence",
                "calibrated_execution_likelihood",
                "focus_family_tag",
            ],
            "summary": ml_summary,
        },
        "focused_baseline_snapshot": to_jsonable(focused_baselines),
        "best_variant_concentration": to_jsonable(robustness_lookup.get(str(best_variant_id or ""), {})),
        "promotion_gate": recommendation.get("promotion_gate"),
        "variant_payloads": variant_payloads,
    }


def _render_lane_report(payload: dict[str, Any]) -> str:
    recommendation = payload.get("recommendations", {})
    promotion_gate = recommendation.get("promotion_gate") or {}
    failed_checks = promotion_gate.get("failed_checks") or []
    lines = [
        "# LLM Strategy Lane Report",
        "",
        f"- lane id: `{payload.get('lane_id')}`",
        f"- schema version: `{payload.get('schema_version')}`",
        f"- season: `{payload.get('season')}`",
        f"- published at: `{payload.get('published_at')}`",
        "",
        "## Summary",
        "",
        f"- candidate rows: `{payload.get('dataset_summary', {}).get('candidate_rows')}`",
        f"- replay-aware decision clusters: `{payload.get('dataset_summary', {}).get('cluster_count')}`",
        f"- optional ML features loaded: `{payload.get('dataset_summary', {}).get('optional_ml_available_flag')}`",
        f"- active variants: `{', '.join(payload.get('active_variants') or [])}`",
        f"- primary target families: `{', '.join(payload.get('primary_target_families') or [])}`",
        "",
        "## Controller Variants",
        "",
    ]
    for row in payload.get("variant_summary", []):
        lines.append(
            f"- `{row.get('controller_id')}` | workflow `{row.get('workflow')}` | "
            f"standard `{row.get('standard_trade_count')}` | replay `{row.get('replay_trade_count')}` | "
            f"replay bankroll `{row.get('replay_ending_bankroll')}` | execution `{row.get('execution_rate')}` | "
            f"hypothesis `{row.get('hypothesis')}`"
        )
    lines.extend(
        [
            "",
            "## Focused Comparison",
            "",
        ]
    )
    for row in payload.get("focused_comparison_preview", []):
        lines.append(
            f"- `{row.get('candidate_id')}` | kind `{row.get('candidate_kind')}` | replay bankroll "
            f"`{row.get('replay_ending_bankroll')}` | replay trades `{row.get('replay_trade_count')}`"
        )
    lines.extend(
        [
            "",
            "## Robustness",
            "",
        ]
    )
    for row in payload.get("robustness_summary", []):
        lines.append(
            f"- `{row.get('controller_id')}` | top trade share `{row.get('top_trade_return_share')}` | "
            f"top cluster share `{row.get('top_cluster_return_share')}` | "
            f"top family share `{row.get('top_family_return_share')}` | "
            f"focus alignment `{row.get('focus_family_alignment_rate')}` | "
            f"leave-one-out min bankroll `{row.get('leave_one_out_min_bankroll')}` | "
            f"narrow dependency `{row.get('narrow_dependency_flag')}`"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"- best variant: `{payload.get('recommendations', {}).get('best_variant_id')}`",
            f"- deployment: `{payload.get('recommendations', {}).get('deployment_recommendation')}`",
            f"- {payload.get('recommendations', {}).get('reason')}",
            f"- {payload.get('recommendations', {}).get('llm_role')}",
            f"- {payload.get('recommendations', {}).get('where_value_adds')}",
            f"- {payload.get('recommendations', {}).get('viability')}",
            f"- live-probe eligible: `{promotion_gate.get('live_probe_eligible_flag')}` | failed checks `{', '.join(failed_checks) if failed_checks else 'none'}`",
        ]
    )
    return "\n".join(lines)


def _render_memo(payload: dict[str, Any]) -> str:
    memo = payload.get("memo_answers") or {}
    recommendation = payload.get("recommendations") or {}
    promotion_gate = recommendation.get("promotion_gate") or {}
    lines = [
        "# LLM Strategy Lane Memo",
        "",
        "## Where The LLM Adds Value",
        "",
        f"- {memo.get('where_the_llm_adds_value')}",
        "",
        "## Select, Gate, Or Generate",
        "",
        f"- {memo.get('select_gate_or_generate')}",
        "",
        "## Viability Under Replay",
        "",
        f"- {memo.get('viable_under_replay')}",
        "",
        "## Deployment Recommendation",
        "",
        f"- {memo.get('deployment_recommendation')}",
        "",
        "## Concentration Risk",
        "",
        f"- {memo.get('concentration_risk')}",
        "",
        "## Shadow To Live-Probe Rule",
        "",
        f"- {memo.get('promotion_rule')}",
        f"- current blockers: {', '.join(promotion_gate.get('failed_checks') or ['none'])}",
        "",
        "## Daily Live Validation",
        "",
        f"- {memo.get('daily_live_validation')}",
    ]
    return "\n".join(lines)


def _render_daily_live_validation(payload: dict[str, Any]) -> str:
    recommendation = payload.get("recommendations") or {}
    promotion_gate = recommendation.get("promotion_gate") or {}
    thresholds = promotion_gate.get("thresholds") or LIVE_PROBE_PROMOTION_THRESHOLDS
    failed_checks = promotion_gate.get("failed_checks") or []
    best_variant_id = recommendation.get("best_variant_id")
    deployment = recommendation.get("deployment_recommendation")
    lines = [
        "# Daily Live Validation Handoff",
        "",
        f"- recommended mode: `{deployment}`",
        f"- best current controller: `{best_variant_id}`",
        f"- shadow payload json: `{payload.get('artifacts', {}).get('shadow_sidecar_payload_json')}`",
        "",
        "## Scope",
        "",
        "- Keep the lane constrained to quarter-open, anomaly, and unstable-lead windows.",
        "- Compile only to existing `inversion`, `quarter_open_reprice`, or `micro_momentum_continuation` candidates.",
        "- Use ML v2 only for calibrated confidence, calibrated execution likelihood, and focus-family tagging when present.",
        "",
        "## Daily Checks",
        "",
        "- Publish the selected action JSON and decision trace for each run.",
        "- Compare the best LLM variant directly against inversion, quarter_open_reprice, micro_momentum_continuation, and the locked controller baselines.",
        "- Flag any day where the compiled action cannot map back to an existing signal_id or where replay freshness would have rejected the trade.",
        "",
        "## Promotion Gate",
        "",
        f"- Live-probe only when replay trades are at least `{thresholds.get('min_replay_trade_count')}`, executed clusters are at least `{thresholds.get('min_executed_cluster_count')}`, executed families are at least `{thresholds.get('min_executed_family_count')}`, focus-family alignment is at least `{thresholds.get('min_focus_family_alignment_rate')}`, top trade share is at most `{thresholds.get('max_top_trade_return_share')}`, top cluster share is at most `{thresholds.get('max_top_cluster_return_share')}`, top family share is at most `{thresholds.get('max_top_family_return_share')}`, leave-one-out min bankroll stays at or above inversion, and the best variant still beats the focused baseline ceiling.",
        f"- Current failed checks: `{', '.join(failed_checks) if failed_checks else 'none'}`",
    ]
    return "\n".join(lines)


def _render_status(payload: dict[str, Any]) -> str:
    recommendation = payload.get("recommendations") or {}
    promotion_gate = recommendation.get("promotion_gate") or {}
    lines = [
        "# LLM Strategy Lane Status",
        "",
        f"- published at: `{payload.get('published_at')}`",
        f"- report: `{payload.get('reports', {}).get('lane_report_markdown')}`",
        f"- memo: `{payload.get('reports', {}).get('memo_markdown')}`",
        f"- daily live validation: `{payload.get('reports', {}).get('daily_live_validation_markdown')}`",
        f"- submission: `{payload.get('reports', {}).get('benchmark_submission_json')}`",
        f"- shadow payload: `{payload.get('artifacts', {}).get('shadow_sidecar_payload_json')}`",
        "",
        "## Recommendation",
        "",
        f"- best variant: `{payload.get('recommendations', {}).get('best_variant_id')}`",
        f"- deployment: `{payload.get('recommendations', {}).get('deployment_recommendation')}`",
        f"- reason: {payload.get('recommendations', {}).get('reason')}",
        f"- live-probe eligible: `{promotion_gate.get('live_probe_eligible_flag')}`",
        f"- promotion blockers: `{', '.join(promotion_gate.get('failed_checks') or ['none'])}`",
        "",
        "## Published Subjects",
        "",
    ]
    for row in payload.get("variant_summary", []):
        lines.append(
            f"- `{row.get('controller_id')}` | replay bankroll `{row.get('replay_ending_bankroll')}` | "
            f"execution `{row.get('execution_rate')}` | selected `{row.get('replay_trade_count')}`"
        )
    return "\n".join(lines)


def _write_subject_trade_artifacts(
    artifact_root: Path,
    *,
    subject_name: str,
    standard_df: pd.DataFrame,
    replay_df: pd.DataFrame,
) -> dict[str, str]:
    stem = _subject_artifact_stem(subject_name)
    standard_paths = write_frame(artifact_root / f"{stem}_standard_trades", standard_df)
    replay_paths = write_frame(artifact_root / f"{stem}_replay_trades", replay_df)
    return {
        "standard_csv": standard_paths["csv"],
        "replay_csv": replay_paths["csv"],
    }


def run_llm_strategy_lane(request: LLMStrategyLaneRequest) -> dict[str, Any]:
    shared_root = _resolve_shared_root(request.shared_root)
    analysis_output_root = _resolve_analysis_output_root(request.analysis_output_root)
    replay_root = shared_root / "artifacts" / REPLAY_ENGINE_LANE / request.season / request.replay_artifact_name
    artifact_root = shared_root / "artifacts" / LLM_OUTPUT_DIRNAME / request.season / request.artifact_name
    report_root = shared_root / "reports" / LLM_OUTPUT_DIRNAME
    handoff_root = shared_root / "handoffs" / LLM_OUTPUT_DIRNAME
    artifact_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    handoff_root.mkdir(parents=True, exist_ok=True)

    subject_summary_df = _read_table(replay_root / "replay_subject_summary")
    signal_summary_df = _read_table(replay_root / "replay_signal_summary")
    attempt_trace_df = _read_table(replay_root / "replay_attempt_trace")
    standard_frames, replay_frames = _load_replay_subject_frames(replay_root, subject_summary_df)
    state_panel_df = _load_postseason_state_panel(analysis_output_root, request.analysis_version)
    ml_feature_df, ml_summary = _load_optional_ml_features(shared_root, request.season)

    candidate_df = build_llm_candidate_dataset(
        signal_summary_df=signal_summary_df,
        attempt_trace_df=attempt_trace_df,
        standard_frames=standard_frames,
        replay_frames=replay_frames,
        state_panel_df=state_panel_df,
        subject_summary_df=subject_summary_df,
        ml_feature_df=ml_feature_df,
    )
    clustered_df = _build_decision_clusters(candidate_df, cluster_window_minutes=request.cluster_window_minutes)

    action_schema = build_llm_action_schema()
    prompt_contracts = build_llm_prompt_contracts()

    llm_subject_payloads: list[dict[str, Any]] = []
    variant_summary_rows: list[dict[str, Any]] = []
    subject_artifact_paths: dict[str, dict[str, str]] = {}
    selected_action_examples: dict[str, list[dict[str, Any]]] = {}
    selected_variant_frames: dict[str, pd.DataFrame] = {}
    for variant in LLM_CONTROLLER_VARIANTS:
        scored_df, decision_df, selected_records = _run_controller_variant(clustered_df, variant=variant)
        selected_df = pd.DataFrame(selected_records)
        selected_variant_frames[variant.controller_id] = selected_df.copy()
        standard_df, replay_df = _build_selected_trade_frames(selected_df)
        selected_actions_payload = [
            {
                "signal_id": record.get("signal_id"),
                "strategy_family": record.get("strategy_family"),
                "selection_score": record.get("selection_score"),
                "cluster_id": record.get("cluster_id"),
                "compiled_action": record.get("compiled_action"),
            }
            for record in selected_records
        ]

        artifact_paths = _write_subject_trade_artifacts(
            artifact_root,
            subject_name=variant.controller_id,
            standard_df=standard_df,
            replay_df=replay_df,
        )
        stem = _subject_artifact_stem(variant.controller_id)
        decision_trace_paths = write_frame(artifact_root / f"{stem}_decision_trace", decision_df)
        decision_trace_json_path = write_json(
            artifact_root / f"{stem}_decision_trace.json",
            decision_df.to_dict(orient="records"),
        )
        selected_actions_path = write_json(
            artifact_root / f"{stem}_selected_actions.json",
            selected_actions_payload,
        )
        cluster_scores_path = write_frame(artifact_root / f"{stem}_scored_candidates", scored_df)

        subject_artifact_paths[variant.controller_id] = {
            **artifact_paths,
            "decision_trace_csv": decision_trace_paths["csv"],
            "decision_trace_json": decision_trace_json_path,
            "selected_actions_json": selected_actions_path,
            "scored_candidates_csv": cluster_scores_path["csv"],
        }
        selected_action_examples[variant.controller_id] = selected_actions_payload[:2]

        payload = _compute_subject_metrics(
            subject_name=variant.controller_id,
            selected_df=selected_df,
            standard_df=standard_df,
            replay_df=replay_df,
        )
        llm_subject_payloads.append(payload)
        variant_summary_rows.append(
            {
                "controller_id": variant.controller_id,
                "workflow": variant.workflow,
                "hypothesis": variant.hypothesis,
                "standard_trade_count": payload["metrics"].get("standard_trade_count"),
                "replay_trade_count": payload["metrics"].get("replay_trade_count"),
                "replay_ending_bankroll": payload["metrics"].get("replay_ending_bankroll"),
                "execution_rate": payload["metrics"].get("execution_rate"),
            }
        )

    benchmark_comparison_df = _build_benchmark_comparison_frame(subject_summary_df, llm_subject_payloads)
    focused_comparison_df = _build_focused_comparison_frame(benchmark_comparison_df)
    robustness_df, leave_one_out_df = _build_variant_robustness_payload(selected_variant_frames)
    recommendation = _determine_lane_recommendation(variant_summary_rows, robustness_df, focused_comparison_df)
    head_to_head_summary = _build_head_to_head_summary(
        focused_comparison_df,
        best_variant_id=str(recommendation.get("best_variant_id") or ""),
    )
    candidate_artifacts = {
        "candidate_dataset": write_frame(artifact_root / "candidate_dataset", clustered_df),
        "benchmark_comparison": write_frame(artifact_root / "benchmark_comparison", benchmark_comparison_df),
        "focused_comparison": write_frame(artifact_root / "focused_comparison", focused_comparison_df),
        "robustness_summary": write_frame(artifact_root / "robustness_summary", robustness_df),
        "leave_one_out_robustness": write_frame(artifact_root / "leave_one_out_robustness", leave_one_out_df),
        "head_to_head_summary_json": write_json(artifact_root / "head_to_head_summary.json", head_to_head_summary),
    }

    report_path = report_root / "llm_strategy_lane_report.md"
    memo_path = report_root / "research_memo.md"
    submission_path = report_root / "benchmark_submission.json"
    handoff_path = handoff_root / "status.md"
    daily_live_validation_path = handoff_root / "daily_live_validation.md"
    shadow_sidecar_payload_path = handoff_root / "shadow_sidecar_payload.json"
    run_payload_path = artifact_root / "llm_strategy_lane_run.json"
    schema_path = artifact_root / "action_schema.json"
    prompt_contract_path = artifact_root / "prompt_contracts.json"

    published_at = datetime.now(timezone.utc).isoformat()
    shadow_sidecar_payload = _build_shadow_sidecar_payload(
        published_at=published_at,
        season=request.season,
        variant_summary_rows=variant_summary_rows,
        subject_artifact_paths=subject_artifact_paths,
        recommendation=recommendation,
        robustness_df=robustness_df,
        focused_comparison_df=focused_comparison_df,
        ml_summary=ml_summary,
    )
    promotion_gate = recommendation.get("promotion_gate") or {}
    promotion_failures = promotion_gate.get("failed_checks") or []
    report_payload: dict[str, Any] = {
        "lane_id": LLM_LANE_ID,
        "lane_output_dirname": LLM_OUTPUT_DIRNAME,
        "season": request.season,
        "schema_version": LLM_SCHEMA_VERSION,
        "analysis_version": request.analysis_version,
        "replay_artifact_name": request.replay_artifact_name,
        "published_at": published_at,
        "dataset_summary": {
            "candidate_rows": int(len(clustered_df)),
            "cluster_count": int(clustered_df["cluster_id"].nunique()) if not clustered_df.empty else 0,
            "optional_ml_available_flag": bool(ml_summary.get("available")),
            "optional_ml_summary": ml_summary,
        },
        "active_variants": [variant.controller_id for variant in LLM_CONTROLLER_VARIANTS],
        "primary_target_families": list(CORE_LLM_REPLAY_FAMILIES),
        "shadow_sidecar": shadow_sidecar_payload,
        "variant_summary": variant_summary_rows,
        "benchmark_comparison_preview": to_jsonable(benchmark_comparison_df.head(12).to_dict(orient="records")),
        "focused_comparison_preview": to_jsonable(focused_comparison_df.head(8).to_dict(orient="records")),
        "robustness_summary": to_jsonable(robustness_df.to_dict(orient="records")),
        "head_to_head_summary": head_to_head_summary,
        "recommendations": {
            "best_variant_id": recommendation.get("best_variant_id"),
            "alternate_variant_id": recommendation.get("alternate_variant_id"),
            "deployment_recommendation": recommendation.get("deployment_recommendation"),
            "reason": recommendation.get("reason"),
            "promotion_gate": promotion_gate,
            "llm_role": "Use the LLM as a select / gate / compile controller over replay-executable candidates, not as an unconstrained trade generator.",
            "where_value_adds": "Value concentrates where quarter-open, anomaly, and unstable-lead windows present multiple executable inversion, quarter_open_reprice, and micro_momentum_continuation candidates and the controller must choose one compact action.",
            "viability": "A true LLM lane is viable only as a replay-executable shadow sidecar where every action compiles back to an allowed existing signal_id after deterministic freshness gates have already filtered stale states.",
        },
        "memo_answers": {
            "where_the_llm_adds_value": "The LLM adds value only at compact replay-fresh decision points where inversion, quarter_open_reprice, and micro_momentum_continuation compete inside the same core window.",
            "select_gate_or_generate": "The right long-term role is select and gate first, then compile only inside constrained templates. Unconstrained generation should stay off the table.",
            "viable_under_replay": "Yes, as a JSON-constrained controller shadow lane. No, as a free-form trader. Replay viability comes from compile-back discipline, not from more activity.",
            "deployment_recommendation": (
                f"{recommendation.get('deployment_recommendation')}: {recommendation.get('reason')}"
            ),
            "concentration_risk": (
                f"Concentration remains the main blocker: current live-probe gate failures are {', '.join(promotion_failures) if promotion_failures else 'none'}, so the lane stays a lower-risk shadow sidecar."
            ),
            "promotion_rule": (
                "Promote from shadow to live-probe only after the best variant beats the focused replay baseline ceiling and clears the replay breadth and concentration thresholds encoded in the shadow payload."
            ),
            "daily_live_validation": "Run the best controller in shadow only, publish the selected actions, decision trace, and shadow sidecar payload daily, and require direct comparison against inversion, quarter_open_reprice, micro_momentum_continuation, and the locked controller baselines.",
        },
    }
    report_payload["artifacts"] = {
        "run_payload_json": str(run_payload_path),
        "action_schema_json": str(schema_path),
        "prompt_contracts_json": str(prompt_contract_path),
        "shadow_sidecar_payload_json": str(shadow_sidecar_payload_path),
        "candidate_dataset_csv": candidate_artifacts["candidate_dataset"]["csv"],
        "benchmark_comparison_csv": candidate_artifacts["benchmark_comparison"]["csv"],
        "focused_comparison_csv": candidate_artifacts["focused_comparison"]["csv"],
        "robustness_summary_csv": candidate_artifacts["robustness_summary"]["csv"],
        "leave_one_out_robustness_csv": candidate_artifacts["leave_one_out_robustness"]["csv"],
        "head_to_head_summary_json": candidate_artifacts["head_to_head_summary_json"],
    }
    report_payload["selected_action_examples"] = selected_action_examples

    submission = {
        "schema_version": LLM_SCHEMA_VERSION,
        "lane_id": LLM_LANE_ID,
        "lane_label": LLM_LANE_LABEL,
        "lane_type": LLM_LANE_TYPE,
        "published_at": published_at,
        "comparison_scope": {
            "season": request.season,
            "phase_group": "play_in,playoffs",
            "shared_contract_ref": "replay_contract_current.md + unified_benchmark_contract_current.md",
            "evaluation_mode": "replay_aware_shadow_llm_compilation",
        },
        "lane_recommendation": {
            "best_variant_id": recommendation.get("best_variant_id"),
            "alternate_variant_id": recommendation.get("alternate_variant_id"),
            "deployment_recommendation": recommendation.get("deployment_recommendation"),
            "reason": recommendation.get("reason"),
            "promotion_gate": promotion_gate,
            "active_variants": [variant.controller_id for variant in LLM_CONTROLLER_VARIANTS],
            "primary_target_families": list(CORE_LLM_REPLAY_FAMILIES),
            "shadow_sidecar_payload_json": str(shadow_sidecar_payload_path),
        },
        "subjects": [],
    }
    variant_summary_lookup = {row["controller_id"]: row for row in variant_summary_rows}
    for subject in llm_subject_payloads:
        controller_id = subject["candidate_id"]
        metrics = subject["metrics"]
        artifact_paths = subject_artifact_paths.get(controller_id, {})
        variant_summary = variant_summary_lookup.get(controller_id, {})
        subject_entry = {
            **subject,
            "result_views": {
                "standard_backtest": {
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
                "live_observed": {
                    "mode": "live_observed",
                    "live_observed_flag": False,
                    "trade_count": 0,
                },
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
            "trace_artifacts": {
                "decision_trace_json": artifact_paths.get("decision_trace_json"),
                "decision_trace_csv": artifact_paths.get("decision_trace_csv"),
                "signal_summary_csv": candidate_artifacts["candidate_dataset"]["csv"],
                "attempt_trace_csv": str(replay_root / "replay_attempt_trace.csv"),
            },
            "artifacts": {
                "report_markdown": str(report_path),
                "runtime_note_markdown": str(memo_path),
                "daily_live_validation_markdown": str(daily_live_validation_path),
                "shadow_sidecar_payload_json": str(shadow_sidecar_payload_path),
                "standard_trades_csv": artifact_paths.get("standard_csv"),
                "replay_trades_csv": artifact_paths.get("replay_csv"),
                "selected_actions_json": artifact_paths.get("selected_actions_json"),
                "action_schema_json": str(schema_path),
                "prompt_contracts_json": str(prompt_contract_path),
            },
            "notes": [
                f"Hypothesis: {variant_summary.get('hypothesis')}",
                "Replay-aware selection uses deterministic freshness gates before any optional LLM escalation.",
                "Generated strategies are constrained templates that compile back to existing candidate signal_ids when template compilation is enabled.",
                "ML signals are optional confidence helpers, not hard dependencies.",
                "live_observed_flag=false is explicit because this lane was not run live.",
            ],
        }
        submission["subjects"].append(subject_entry)

    action_schema_path = write_json(schema_path, action_schema)
    prompt_contracts_path = write_json(prompt_contract_path, prompt_contracts)
    shadow_sidecar_json_path = write_json(shadow_sidecar_payload_path, shadow_sidecar_payload)
    submission_json_path = write_json(submission_path, submission)
    report_markdown_path = write_markdown(report_path, _render_lane_report(report_payload))
    memo_markdown_path = write_markdown(memo_path, _render_memo(report_payload))
    daily_live_validation_markdown_path = write_markdown(daily_live_validation_path, _render_daily_live_validation(report_payload))

    report_payload["reports"] = {
        "lane_report_markdown": report_markdown_path,
        "memo_markdown": memo_markdown_path,
        "daily_live_validation_markdown": daily_live_validation_markdown_path,
        "benchmark_submission_json": submission_json_path,
    }
    report_payload["artifacts"]["action_schema_json"] = action_schema_path
    report_payload["artifacts"]["prompt_contracts_json"] = prompt_contracts_path
    report_payload["artifacts"]["shadow_sidecar_payload_json"] = shadow_sidecar_json_path

    if request.build_dashboard_check:
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
            for row in (dashboard_snapshot.get("llm_candidates") or [])
            if str(row.get("candidate_id") or "").startswith("llm_")
        ]
        report_payload["dashboard_ingest_preview"] = dashboard_preview
        report_payload["artifacts"]["dashboard_ingest_check_json"] = str(artifact_root / "dashboard_ingest_check.json")
        write_json(artifact_root / "dashboard_ingest_check.json", {"llm_candidates": dashboard_preview})

    write_json(run_payload_path, report_payload)
    write_markdown(handoff_path, _render_status(report_payload))
    return to_jsonable(report_payload)


__all__ = [
    "LLMStrategyLaneRequest",
    "LLM_ACTION_SCHEMA_VERSION",
    "LLM_ARTIFACT_NAME",
    "LLM_LANE_ID",
    "LLM_OUTPUT_DIRNAME",
    "LLM_PROMPT_CONTRACT_VERSION",
    "_aggregate_attempt_trace",
    "_build_decision_clusters",
    "build_llm_action_schema",
    "build_llm_candidate_dataset",
    "build_llm_prompt_contracts",
    "run_llm_strategy_lane",
]
