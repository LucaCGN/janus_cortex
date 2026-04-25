from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.contracts import DEFAULT_SEASON, WINDOWS_LOCAL_ROOT


UNIFIED_BENCHMARK_SCHEMA_VERSION = "integration_v1"
COMPARE_READY_CRITERIA_VERSION = "compare_ready_v1"
SUBMISSION_EXAMPLE_VERSION = "submission_example_v1"
DEFAULT_REPLAY_ARTIFACT_NAME = "postseason_execution_replay"
DEFAULT_FINALIST_LIMIT = 6
LOCKED_BASELINE_SUBJECTS = (
    "controller_vnext_unified_v1 :: balanced",
    "controller_vnext_deterministic_v1 :: tight",
)
REPLAY_HIGH_FREQUENCY_FAMILIES = {
    "micro_momentum_continuation",
    "panic_fade_fast",
    "quarter_open_reprice",
    "halftime_gap_fill",
    "lead_fragility",
}
TRACE_ARTIFACT_KEYS = (
    "decision_trace_json",
    "decision_trace_csv",
    "attempt_trace_json",
    "attempt_trace_csv",
    "signal_summary_json",
    "signal_summary_csv",
    "subject_trace_json",
    "subject_trace_csv",
    "trade_trace_json",
    "trade_trace_csv",
    "replay_signal_summary_csv",
    "replay_attempt_trace_csv",
    "trace_json",
    "trace_csv",
)
KNOWN_LANE_SPECS = (
    {
        "lane_id": "locked-baselines",
        "label": "Locked baselines",
        "lane_type": "baseline",
        "submission_mode": "synthesized",
    },
    {
        "lane_id": "replay-engine-hf",
        "label": "Replay + deterministic/HF",
        "lane_type": "deterministic_hf",
        "submission_mode": "synthesized",
    },
    {
        "lane_id": "ml-trading",
        "label": "ML trading",
        "lane_type": "ml",
        "submission_mode": "manifest",
    },
    {
        "lane_id": "llm-strategy",
        "label": "LLM strategy",
        "lane_type": "llm",
        "submission_mode": "manifest",
    },
)
LANE_SPEC_LOOKUP = {spec["lane_id"]: spec for spec in KNOWN_LANE_SPECS}
LANE_REPORT_FOLDER_HINTS = {
    "ml-trading": "ml-trading-lane",
    "llm-strategy": "llm-strategy-lane",
}
VISIBILITY_BUCKET_COMPARE_READY = "compare_ready"
VISIBILITY_BUCKET_SHADOW_ONLY = "shadow_only"
VISIBILITY_BUCKET_BENCH_ONLY = "bench_only"
PROMOTION_BUCKET_LIVE_READY = "live_ready"
PROMOTION_BUCKET_LIVE_PROBE = "live_probe"
PROMOTION_BUCKET_SHADOW_ONLY = "shadow_only"
PROMOTION_BUCKET_BENCH_ONLY = "bench_only"
REPLAY_FORCE_COMPARE_READY_IDS = {"inversion"}
LIVE_VALIDATION_ALIAS_MAP = {
    "mlcalibratedcontrollersidecar": "ml_controller_focus_calibrator_v2",
    "llmtemplatecompilerv1": "llm_template_compiler_core_windows_v2",
}


@dataclass(slots=True)
class UnifiedBenchmarkRequest:
    season: str = DEFAULT_SEASON
    replay_artifact_name: str = DEFAULT_REPLAY_ARTIFACT_NAME
    shared_root: str | None = None
    finalist_limit: int = DEFAULT_FINALIST_LIMIT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_default_shared_root() -> Path:
    configured_root = os.getenv("JANUS_LOCAL_ROOT")
    if configured_root:
        return Path(configured_root) / "shared"
    if WINDOWS_LOCAL_ROOT.exists():
        return WINDOWS_LOCAL_ROOT / "shared"
    return Path("output") / "shared"


def _resolve_shared_root(shared_root: str | None) -> Path:
    return Path(shared_root) if shared_root else resolve_default_shared_root()


def _resolve_replay_root(request: UnifiedBenchmarkRequest) -> Path:
    return (
        _resolve_shared_root(request.shared_root)
        / "artifacts"
        / "replay-engine-hf"
        / request.season
        / request.replay_artifact_name
    )


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _read_markdown(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _read_table(root: Path, stem: str) -> pd.DataFrame:
    parquet_path = root / f"{stem}.parquet"
    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path)
        except Exception:
            pass
    csv_path = root / f"{stem}.csv"
    if csv_path.exists():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


def _clean_number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _clean_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n", ""}:
        return False
    return None


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value.item() if hasattr(value, "item") and not isinstance(value, (str, bytes)) else value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in record.items()}


def _numeric_sort(value: Any, *, default: float = float("-inf")) -> float:
    numeric = _clean_number(value)
    return default if numeric is None else float(numeric)


def _parse_markdown_value(markdown: str, label: str) -> str | None:
    prefix = f"- {label}:"
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip().strip("`")
    return None


def _subject_slug(subject_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", subject_name)


def _normalize_candidate_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _existing_path_string(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _dashboard_bucket_for_lane(lane_id: str, candidate_kind: str | None = None) -> str:
    if lane_id == "locked-baselines":
        return "baseline_controllers"
    if lane_id == "replay-engine-hf":
        return "deterministic_hf_candidates"
    if candidate_kind == "ml_strategy" or lane_id == "ml-trading":
        return "ml_candidates"
    if candidate_kind == "llm_strategy" or lane_id == "llm-strategy":
        return "llm_candidates"
    return "external_candidates"


def _result_view(
    *,
    mode: str,
    trade_count: Any = None,
    ending_bankroll: Any = None,
    avg_return_with_slippage: Any = None,
    compounded_return: Any = None,
    max_drawdown_pct: Any = None,
    max_drawdown_amount: Any = None,
    no_trade_count: Any = None,
    execution_rate: Any = None,
    live_observed_flag: Any = None,
    live_run_count: Any = None,
    entry_submitted_count: Any = None,
    position_opened_count: Any = None,
    live_vs_backtest_gap_trade_rate: Any = None,
) -> dict[str, Any]:
    payload = {
        "mode": mode,
        "trade_count": _clean_number(trade_count),
        "ending_bankroll": _clean_number(ending_bankroll),
        "avg_return_with_slippage": _clean_number(avg_return_with_slippage),
        "compounded_return": _clean_number(compounded_return),
        "max_drawdown_pct": _clean_number(max_drawdown_pct),
        "max_drawdown_amount": _clean_number(max_drawdown_amount),
    }
    if no_trade_count is not None:
        payload["no_trade_count"] = _clean_number(no_trade_count)
    if execution_rate is not None:
        payload["execution_rate"] = _clean_number(execution_rate)
    if live_observed_flag is not None:
        payload["live_observed_flag"] = bool(live_observed_flag)
    if live_run_count is not None:
        payload["live_run_count"] = _clean_number(live_run_count)
    if entry_submitted_count is not None:
        payload["entry_submitted_count"] = _clean_number(entry_submitted_count)
    if position_opened_count is not None:
        payload["position_opened_count"] = _clean_number(position_opened_count)
    if live_vs_backtest_gap_trade_rate is not None:
        payload["live_vs_backtest_gap_trade_rate"] = _clean_number(live_vs_backtest_gap_trade_rate)
    return _clean_record(payload)


def _realism_view(
    *,
    trade_gap: Any = None,
    execution_rate: Any = None,
    realism_gap_trade_rate: Any = None,
    top_no_trade_reason: Any = None,
    blocked_signal_count: Any = None,
    stale_signal_suppressed_count: Any = None,
    stale_signal_suppression_rate: Any = None,
    stale_signal_share_of_blocked_signals: Any = None,
) -> dict[str, Any]:
    return _clean_record(
        {
            "trade_gap": _clean_number(trade_gap),
            "execution_rate": _clean_number(execution_rate),
            "realism_gap_trade_rate": _clean_number(realism_gap_trade_rate),
            "top_no_trade_reason": top_no_trade_reason,
            "blocked_signal_count": _clean_number(blocked_signal_count),
            "stale_signal_suppressed_count": _clean_number(stale_signal_suppressed_count),
            "stale_signal_suppression_rate": _clean_number(stale_signal_suppression_rate),
            "stale_signal_share_of_blocked_signals": _clean_number(
                stale_signal_share_of_blocked_signals
            ),
        }
    )


def _normalize_artifact_paths(
    raw_artifacts: dict[str, Any],
    *,
    base_dir: Path,
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw_artifacts.items():
        if value in (None, ""):
            continue
        path = Path(str(value))
        normalized[key] = str((base_dir / path).resolve()) if not path.is_absolute() else str(path)
    return normalized


def _has_trace_artifacts(artifact_paths: dict[str, Any]) -> bool:
    return any(clean_key in artifact_paths for clean_key in TRACE_ARTIFACT_KEYS)


def build_compare_ready_criteria() -> dict[str, Any]:
    return {
        "version": COMPARE_READY_CRITERIA_VERSION,
        "lane_requirements": [
            {
                "id": "shared_handoff",
                "description": "Lane publishes a handoff status file under shared/handoffs/<lane>/status.md.",
            },
            {
                "id": "shared_submission",
                "description": "Lane publishes benchmark_submission.json or the integration lane can synthesize it from published artifacts.",
            },
            {
                "id": "contract_reference",
                "description": "Submission names the replay contract and unified benchmark contract it targeted.",
            },
        ],
        "candidate_requirements": [
            {
                "id": "standard_trade_sample",
                "description": "Candidate has a non-zero standard backtest trade sample.",
            },
            {
                "id": "replay_execution",
                "description": "Candidate has at least one replay-executed trade.",
            },
            {
                "id": "replay_result_metrics",
                "description": "Replay ending bankroll and replay max drawdown are published.",
            },
            {
                "id": "stale_signal_metrics",
                "description": "Stale-signal suppression count and rate are published or derivable.",
            },
            {
                "id": "trace_artifacts",
                "description": "At least one decision-, attempt-, signal-, or trade-trace artifact path is referenced.",
            },
            {
                "id": "live_observed_clarity",
                "description": "Live observed result is explicit: either a live trade count is published or live_observed_flag=false is stated.",
            },
        ],
        "finalist_rule": [
            "Locked baseline controllers always remain visible.",
            "Only compare-ready challengers can join the replay-aware finalist set.",
            "Replay-aware challenger ranking favors replay ending bankroll, replay execution rate, then replay drawdown control.",
        ],
        "trace_artifact_keys": list(TRACE_ARTIFACT_KEYS),
    }


def build_result_modes() -> list[dict[str, Any]]:
    return [
        {
            "id": "standard_backtest",
            "label": "Standard backtest",
            "headline": "Research-only benchmark",
            "description": (
                "Shows raw trade emission before replay execution realism suppresses stale or non-executable entries."
            ),
        },
        {
            "id": "replay_result",
            "label": "Replay result",
            "headline": "Realism baseline",
            "description": (
                "The benchmark-control layer treats replay as the executable baseline for challengers and baselines."
            ),
        },
        {
            "id": "live_observed",
            "label": "Live observed",
            "headline": "Observed separately",
            "description": (
                "Live rows stay separate from replay. Candidates without live runs must publish live_observed_flag=false"
            ),
        },
    ]


def _manifest_submission_records(shared_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    reports_root = shared_root / "reports"
    if not reports_root.exists():
        return records
    for submission_path in sorted(reports_root.glob("*/benchmark_submission.json")):
        payload = _read_json(submission_path)
        if not isinstance(payload, dict):
            continue
        lane_id = str(payload.get("lane_id") or submission_path.parent.name)
        records.append(
            {
                "lane_id": lane_id,
                "report_folder": submission_path.parent.name,
                "report_root": submission_path.parent,
                "submission_path": submission_path,
                "payload": payload,
            }
        )
    return records


def _resolve_lane_handoff_status_path(
    shared_root: Path,
    *,
    lane_id: str,
    report_folder: str | None = None,
) -> str:
    candidate_folders = [folder for folder in (report_folder, lane_id, LANE_REPORT_FOLDER_HINTS.get(lane_id)) if folder]
    for folder in candidate_folders:
        path = shared_root / "handoffs" / folder / "status.md"
        if path.exists():
            return str(path)
    folder = report_folder or LANE_REPORT_FOLDER_HINTS.get(lane_id) or lane_id
    return str(shared_root / "handoffs" / folder / "status.md")


def _discover_lane_publications(
    shared_root: Path,
    *,
    manifest_records: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    publications: dict[str, dict[str, Any]] = {}
    for record in manifest_records or _manifest_submission_records(shared_root):
        lane_id = str(record["lane_id"])
        report_root = Path(record["report_root"])
        report_folder = str(record["report_folder"])
        submission_path = Path(record["submission_path"])
        publications[lane_id] = {
            "lane_id": lane_id,
            "report_root": str(report_root),
            "report_folder": report_folder,
            "submission_path": str(submission_path),
            "handoff_status": _resolve_lane_handoff_status_path(
                shared_root,
                lane_id=lane_id,
                report_folder=report_folder,
            ),
            "payload": record["payload"],
        }
    return publications


def _load_daily_live_validation(shared_root: Path) -> dict[str, Any]:
    artifacts_root = shared_root / "artifacts" / "daily-live-validation"
    reports_root = shared_root / "reports" / "daily-live-validation"
    if not artifacts_root.exists():
        return {}

    dated_roots = sorted(
        [path for path in artifacts_root.iterdir() if path.is_dir()],
        key=lambda path: path.name,
        reverse=True,
    )
    for dated_root in dated_roots:
        summary_path = dated_root / "session_summary.json"
        payload = _read_json(summary_path)
        if not isinstance(payload, dict):
            continue
        comparison_frame = _read_table(dated_root, "live_vs_replay_comparison")
        comparison_rows = (
            [_clean_record(row) for row in comparison_frame.to_dict(orient="records")]
            if not comparison_frame.empty
            else []
        )
        report_path = reports_root / f"postgame_report_{dated_root.name}.md"
        return {
            "session_date": payload.get("session_date") or dated_root.name,
            "status": payload.get("status"),
            "snapshot_published_at": payload.get("snapshot_published_at"),
            "summary": payload,
            "comparison_rows": comparison_rows,
            "source_paths": {
                "session_summary_json": str(summary_path),
                "comparison_csv": str(dated_root / "live_vs_replay_comparison.csv"),
                "postgame_report_markdown": str(report_path),
            },
        }
    return {}


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _merge_nested_records(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_nested_records(merged[key], value)
            continue
        if key == "live_observed_flag" and merged.get(key) is True and value is False:
            continue
        if _value_present(value):
            merged[key] = value
    return merged


def _dedupe_notes(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        output.append(text)
        seen.add(text)
    return output


def _candidate_row_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("lane_id") or ""), str(row.get("candidate_id") or "")


def _merge_candidate_rows(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in {
            "artifact_paths",
            "comparison_scope",
            "compare_ready_checks",
            "standard_result",
            "replay_result",
            "live_observed_result",
            "replay_realism",
        } and isinstance(value, dict):
            merged[key] = _merge_nested_records(
                merged.get(key) if isinstance(merged.get(key), dict) else {},
                value,
            )
            continue
        if key == "notes":
            merged[key] = _dedupe_notes(list(merged.get(key) or []) + list(value or []))
            continue
        if _value_present(value):
            merged[key] = value
    return merged


def _merge_candidate_row_sets(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidate_rows:
        key = _candidate_row_key(row)
        existing = merged.get(key)
        merged[key] = _merge_candidate_rows(existing, row) if existing else dict(row)
    return list(merged.values())


def _candidate_performance_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float, str]:
    return (
        _numeric_sort(row.get("replay_ending_bankroll")),
        _numeric_sort(row.get("execution_rate")),
        -_numeric_sort(row.get("replay_max_drawdown_pct"), default=float("inf")),
        -_numeric_sort(row.get("stale_signal_suppression_rate")),
        _numeric_sort(row.get("standard_trade_count")),
        str(row.get("candidate_id")),
    )


def _candidate_visibility_bucket(row: dict[str, Any]) -> str:
    if bool(row.get("baseline_locked_flag")):
        return VISIBILITY_BUCKET_COMPARE_READY
    if not bool(row.get("comparison_ready_flag")):
        return VISIBILITY_BUCKET_BENCH_ONLY

    lane_id = str(row.get("lane_id") or "")
    candidate_id = str(row.get("candidate_id") or "")
    live_test_recommendation = str(row.get("live_test_recommendation") or "")
    focus_rank = _clean_number(row.get("focus_rank"))
    replay_rank = _clean_number(row.get("replay_rank"))

    if lane_id == "ml-trading":
        return (
            VISIBILITY_BUCKET_BENCH_ONLY
            if "gate" in candidate_id.lower()
            else VISIBILITY_BUCKET_SHADOW_ONLY
        )
    if lane_id == "llm-strategy":
        return VISIBILITY_BUCKET_COMPARE_READY
    if lane_id == "replay-engine-hf":
        if candidate_id in REPLAY_FORCE_COMPARE_READY_IDS:
            return VISIBILITY_BUCKET_COMPARE_READY
        if live_test_recommendation in {"priority_live_probe"}:
            return VISIBILITY_BUCKET_COMPARE_READY
        if live_test_recommendation in {"live_probe"}:
            if (focus_rank is not None and focus_rank <= 2) or (replay_rank is not None and replay_rank <= 2):
                return VISIBILITY_BUCKET_COMPARE_READY
            return VISIBILITY_BUCKET_SHADOW_ONLY
        if live_test_recommendation in {"shadow_live_probe", "shadow_only"}:
            return VISIBILITY_BUCKET_SHADOW_ONLY
        if live_test_recommendation in {"bench", "bench_only"}:
            return VISIBILITY_BUCKET_BENCH_ONLY
        return VISIBILITY_BUCKET_BENCH_ONLY
    return VISIBILITY_BUCKET_COMPARE_READY


def _candidate_visibility_reason(row: dict[str, Any], visibility_bucket: str) -> str:
    if bool(row.get("baseline_locked_flag")):
        return "locked baseline controller anchor"
    lane_id = str(row.get("lane_id") or "")
    candidate_id = str(row.get("candidate_id") or "")
    live_test_recommendation = str(row.get("live_test_recommendation") or "")
    if visibility_bucket == VISIBILITY_BUCKET_COMPARE_READY:
        if lane_id == "llm-strategy":
            return "compare-ready constrained LLM select/gate/compile role"
        if lane_id == "replay-engine-hf" and candidate_id in REPLAY_FORCE_COMPARE_READY_IDS:
            return "strongest current non-controller replay family"
        if lane_id == "replay-engine-hf":
            return "replay-backed deterministic or HF live-probe candidate"
        return "meets the shared benchmark gate and is finalist-eligible"
    if visibility_bucket == VISIBILITY_BUCKET_SHADOW_ONLY:
        if lane_id == "ml-trading":
            return "ML sidecar ranking and calibration only"
        if lane_id == "replay-engine-hf" and live_test_recommendation in {
            "live_probe",
            "shadow_live_probe",
            "shadow_only",
        }:
            return "published replay live-probe candidate, but outside the current top compare-ready probe tier"
        return "keep visible for shadow-only review, not direct finalist promotion"
    if lane_id == "ml-trading":
        return "gate or hard-routing variant is not ready for use beyond benchmarking"
    if lane_id == "replay-engine-hf" and live_test_recommendation in {"bench", "bench_only"}:
        return "replay lane marked this family for bench-only review"
    return "published for bench review but not recommended for promotion"


def _finalize_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(row.get("candidate_id") or row.get("display_name") or "")
    baseline_locked_flag = bool(row.get("baseline_locked_flag")) or candidate_id in LOCKED_BASELINE_SUBJECTS
    lane_id = str(row.get("lane_id") or ("locked-baselines" if baseline_locked_flag else ""))
    if baseline_locked_flag:
        lane_id = "locked-baselines"
    lane_spec = LANE_SPEC_LOOKUP.get(lane_id, {})
    candidate_kind = str(row.get("candidate_kind") or "").strip() or None

    if not candidate_kind:
        if baseline_locked_flag:
            candidate_kind = "baseline_controller"
        elif lane_id == "replay-engine-hf":
            candidate_kind = (
                "hf_family" if candidate_id in REPLAY_HIGH_FREQUENCY_FAMILIES else "deterministic_family"
            )
        elif lane_id == "ml-trading":
            candidate_kind = "ml_strategy"
        elif lane_id == "llm-strategy":
            candidate_kind = "llm_strategy"

    standard_source = row.get("standard_result") if isinstance(row.get("standard_result"), dict) else {}
    replay_source = row.get("replay_result") if isinstance(row.get("replay_result"), dict) else {}
    live_source = row.get("live_observed_result") if isinstance(row.get("live_observed_result"), dict) else {}
    realism_source = row.get("replay_realism") if isinstance(row.get("replay_realism"), dict) else {}

    live_flag = live_source.get("live_observed_flag")
    if live_flag is None:
        if row.get("replay_live_run_count") is not None:
            live_flag = True
        elif row.get("live_trade_count") is not None:
            live_flag = False

    standard_result = _result_view(
        mode="standard_backtest",
        trade_count=standard_source.get("trade_count", row.get("standard_trade_count")),
        ending_bankroll=standard_source.get("ending_bankroll", row.get("standard_ending_bankroll")),
        avg_return_with_slippage=standard_source.get(
            "avg_return_with_slippage",
            row.get("standard_avg_return_with_slippage"),
        ),
        compounded_return=standard_source.get("compounded_return", row.get("standard_compounded_return")),
        max_drawdown_pct=standard_source.get("max_drawdown_pct", row.get("standard_max_drawdown_pct")),
        max_drawdown_amount=standard_source.get(
            "max_drawdown_amount",
            row.get("standard_max_drawdown_amount"),
        ),
    )
    replay_result = _result_view(
        mode="replay_result",
        trade_count=replay_source.get("trade_count", row.get("replay_trade_count")),
        ending_bankroll=replay_source.get("ending_bankroll", row.get("replay_ending_bankroll")),
        avg_return_with_slippage=replay_source.get(
            "avg_return_with_slippage",
            row.get("replay_avg_return_with_slippage"),
        ),
        compounded_return=replay_source.get("compounded_return", row.get("replay_compounded_return")),
        max_drawdown_pct=replay_source.get("max_drawdown_pct", row.get("replay_max_drawdown_pct")),
        max_drawdown_amount=replay_source.get(
            "max_drawdown_amount",
            row.get("replay_max_drawdown_amount"),
        ),
        no_trade_count=replay_source.get("no_trade_count", row.get("replay_no_trade_count")),
        execution_rate=replay_source.get("execution_rate", row.get("execution_rate")),
    )
    live_result = _result_view(
        mode="live_observed",
        trade_count=live_source.get("trade_count", row.get("live_trade_count")),
        live_observed_flag=live_flag,
        live_run_count=live_source.get("live_run_count", row.get("replay_live_run_count")),
        entry_submitted_count=live_source.get(
            "entry_submitted_count",
            row.get("live_entry_submitted_count"),
        ),
        position_opened_count=live_source.get(
            "position_opened_count",
            row.get("live_position_opened_count"),
        ),
        live_vs_backtest_gap_trade_rate=live_source.get(
            "live_vs_backtest_gap_trade_rate",
            row.get("live_vs_backtest_gap_trade_rate"),
        ),
    )
    replay_realism = _realism_view(
        trade_gap=realism_source.get("trade_gap", row.get("trade_gap")),
        execution_rate=realism_source.get("execution_rate", row.get("execution_rate")),
        realism_gap_trade_rate=realism_source.get(
            "realism_gap_trade_rate",
            row.get("realism_gap_trade_rate"),
        ),
        top_no_trade_reason=realism_source.get("top_no_trade_reason", row.get("top_no_trade_reason")),
        blocked_signal_count=realism_source.get(
            "blocked_signal_count",
            row.get("replay_no_trade_count"),
        ),
        stale_signal_suppressed_count=realism_source.get(
            "stale_signal_suppressed_count",
            row.get("stale_signal_suppressed_count"),
        ),
        stale_signal_suppression_rate=realism_source.get(
            "stale_signal_suppression_rate",
            row.get("stale_signal_suppression_rate"),
        ),
        stale_signal_share_of_blocked_signals=realism_source.get(
            "stale_signal_share_of_blocked_signals",
            row.get("stale_signal_share_of_blocked_signals"),
        ),
    )

    artifact_paths = row.get("artifact_paths") if isinstance(row.get("artifact_paths"), dict) else {}
    compare_ready_checks = _build_compare_ready_checks(
        publication_state=str(row.get("publication_state") or "published"),
        standard_trade_count=standard_result.get("trade_count"),
        replay_trade_count=replay_result.get("trade_count"),
        replay_ending_bankroll=replay_result.get("ending_bankroll"),
        replay_max_drawdown_pct=replay_result.get("max_drawdown_pct"),
        stale_signal_suppressed_count=replay_realism.get("stale_signal_suppressed_count"),
        stale_signal_suppression_rate=replay_realism.get("stale_signal_suppression_rate"),
        artifact_paths=artifact_paths,
        live_observed_flag=bool(live_result.get("live_observed_flag")),
        live_trade_count=live_result.get("trade_count"),
    )
    comparison_ready_flag = compare_ready_checks["compare_ready_flag"]
    visibility_bucket = _candidate_visibility_bucket(
        {
            **row,
            "candidate_id": candidate_id,
            "baseline_locked_flag": baseline_locked_flag,
            "lane_id": lane_id,
            "comparison_ready_flag": comparison_ready_flag,
        }
    )
    visibility_reason = _candidate_visibility_reason(row, visibility_bucket)

    finalized = dict(row)
    finalized.update(
        {
            "candidate_id": candidate_id,
            "display_name": row.get("display_name") or candidate_id,
            "lane_id": lane_id,
            "lane_label": (
                lane_spec.get("label")
                if baseline_locked_flag
                else (row.get("lane_label") or lane_spec.get("label") or lane_id)
            ),
            "lane_type": row.get("lane_type") or lane_spec.get("lane_type") or "external",
            "dashboard_bucket": _dashboard_bucket_for_lane(lane_id, candidate_kind),
            "candidate_kind": candidate_kind,
            "baseline_locked_flag": baseline_locked_flag,
            "standard_result": standard_result,
            "replay_result": replay_result,
            "live_observed_result": live_result,
            "replay_realism": replay_realism,
            "artifact_paths": artifact_paths,
            "compare_ready_checks": compare_ready_checks,
            "comparison_ready_flag": comparison_ready_flag,
            "criteria_ready_flag": comparison_ready_flag,
            "visibility_bucket": visibility_bucket,
            "visibility_bucket_reason": visibility_reason,
            "finalist_eligible_flag": visibility_bucket == VISIBILITY_BUCKET_COMPARE_READY
            and comparison_ready_flag,
            "shadow_only_flag": visibility_bucket == VISIBILITY_BUCKET_SHADOW_ONLY,
            "bench_only_flag": visibility_bucket == VISIBILITY_BUCKET_BENCH_ONLY,
            "standard_trade_count": standard_result.get("trade_count"),
            "replay_trade_count": replay_result.get("trade_count"),
            "live_trade_count": live_result.get("trade_count"),
            "trade_gap": replay_realism.get("trade_gap"),
            "execution_rate": replay_realism.get("execution_rate"),
            "realism_gap_trade_rate": replay_realism.get("realism_gap_trade_rate"),
            "stale_signal_suppressed_count": replay_realism.get("stale_signal_suppressed_count"),
            "stale_signal_suppression_rate": replay_realism.get("stale_signal_suppression_rate"),
            "stale_signal_share_of_blocked_signals": replay_realism.get(
                "stale_signal_share_of_blocked_signals"
            ),
            "top_no_trade_reason": replay_realism.get("top_no_trade_reason"),
            "standard_avg_return_with_slippage": standard_result.get("avg_return_with_slippage"),
            "replay_avg_return_with_slippage": replay_result.get("avg_return_with_slippage"),
            "standard_ending_bankroll": standard_result.get("ending_bankroll"),
            "replay_ending_bankroll": replay_result.get("ending_bankroll"),
            "standard_compounded_return": standard_result.get("compounded_return"),
            "replay_compounded_return": replay_result.get("compounded_return"),
            "standard_max_drawdown_pct": standard_result.get("max_drawdown_pct"),
            "standard_max_drawdown_amount": standard_result.get("max_drawdown_amount"),
            "replay_max_drawdown_pct": replay_result.get("max_drawdown_pct"),
            "replay_max_drawdown_amount": replay_result.get("max_drawdown_amount"),
            "replay_no_trade_count": replay_result.get("no_trade_count"),
            "replay_live_run_count": live_result.get("live_run_count"),
            "live_entry_submitted_count": live_result.get("entry_submitted_count"),
            "live_position_opened_count": live_result.get("position_opened_count"),
            "live_vs_backtest_gap_trade_rate": live_result.get("live_vs_backtest_gap_trade_rate"),
        }
    )
    return _clean_record(finalized)


def _build_daily_live_rollup(daily_live_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rollup: dict[str, dict[str, Any]] = {}
    for row in daily_live_snapshot.get("comparison_rows") or []:
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        item = rollup.setdefault(
            candidate_id,
            {
                "candidate_id": candidate_id,
                "row_count": 0,
                "trade_count": 0,
                "entry_submitted_count": 0,
                "position_opened_count": 0,
                "today_execution": None,
                "target_mode": None,
                "benchmark_compare_ready_state": None,
                "no_trade_buckets": [],
                "notes": [],
            },
        )
        item["row_count"] += 1
        live_attempted_entry = _clean_bool(row.get("live_attempted_entry"))
        live_filled = _clean_bool(row.get("live_filled"))
        live_trade_id = row.get("live_trade_id")
        if live_attempted_entry:
            item["entry_submitted_count"] += 1
        if live_filled or _value_present(live_trade_id):
            item["position_opened_count"] += 1
            item["trade_count"] += 1
        if not item["today_execution"] and _value_present(row.get("today_execution")):
            item["today_execution"] = row.get("today_execution")
        if not item["target_mode"] and _value_present(row.get("target_mode")):
            item["target_mode"] = row.get("target_mode")
        if not item["benchmark_compare_ready_state"] and _value_present(
            row.get("benchmark_compare_ready_state")
        ):
            item["benchmark_compare_ready_state"] = row.get("benchmark_compare_ready_state")
        no_trade_bucket = str(row.get("no_trade_bucket") or "").strip()
        if no_trade_bucket and no_trade_bucket not in item["no_trade_buckets"]:
            item["no_trade_buckets"].append(no_trade_bucket)
        note = str(row.get("notes") or "").strip()
        if note and note not in item["notes"]:
            item["notes"].append(note)
    return rollup


def _candidate_promotion_bucket(
    row: dict[str, Any],
    *,
    daily_live_snapshot: dict[str, Any],
) -> str:
    if bool(row.get("baseline_locked_flag")):
        return PROMOTION_BUCKET_LIVE_READY
    if not bool(row.get("comparison_ready_flag")):
        return PROMOTION_BUCKET_BENCH_ONLY

    lane_id = str(row.get("lane_id") or "")
    candidate_id = str(row.get("candidate_id") or "")
    live_test_recommendation = str(row.get("live_test_recommendation") or "").strip().lower()
    lane_recommendation = row.get("lane_recommendation") if isinstance(row.get("lane_recommendation"), dict) else {}
    deployment_recommendation = str(
        lane_recommendation.get("deployment_recommendation") or ""
    ).strip().lower()

    if lane_id == "replay-engine-hf":
        if live_test_recommendation in {"priority_live_probe", "live_probe"}:
            return PROMOTION_BUCKET_LIVE_PROBE
        if live_test_recommendation in {"shadow_live_probe", "shadow_only"}:
            return PROMOTION_BUCKET_SHADOW_ONLY
        if live_test_recommendation in {"bench", "bench_only"}:
            return PROMOTION_BUCKET_BENCH_ONLY
        if candidate_id in REPLAY_FORCE_COMPARE_READY_IDS:
            return PROMOTION_BUCKET_SHADOW_ONLY
        return PROMOTION_BUCKET_SHADOW_ONLY

    if lane_id == "ml-trading":
        if "gate" in candidate_id.lower() or "sizing" in candidate_id.lower():
            return PROMOTION_BUCKET_BENCH_ONLY
        return PROMOTION_BUCKET_SHADOW_ONLY

    if lane_id == "llm-strategy":
        if deployment_recommendation in {"bench", "bench_only"}:
            return PROMOTION_BUCKET_BENCH_ONLY
        if deployment_recommendation in {"live_probe", "probe"}:
            return PROMOTION_BUCKET_LIVE_PROBE
        return PROMOTION_BUCKET_SHADOW_ONLY

    summary = daily_live_snapshot.get("summary") if isinstance(daily_live_snapshot.get("summary"), dict) else {}
    control = summary.get("control") if isinstance(summary.get("control"), dict) else {}
    if candidate_id in {
        str(control.get("primary_controller") or ""),
        str(control.get("fallback_controller") or ""),
    }:
        return PROMOTION_BUCKET_LIVE_READY
    return PROMOTION_BUCKET_SHADOW_ONLY


def _candidate_promotion_reason(
    row: dict[str, Any],
    promotion_bucket: str,
    *,
    daily_live_snapshot: dict[str, Any],
    today_execution_mode: str | None,
) -> str:
    lane_id = str(row.get("lane_id") or "")
    candidate_id = str(row.get("candidate_id") or "")
    live_test_recommendation = str(row.get("live_test_recommendation") or "").strip().lower()
    lane_recommendation = row.get("lane_recommendation") if isinstance(row.get("lane_recommendation"), dict) else {}
    deployment_recommendation = str(
        lane_recommendation.get("deployment_recommendation") or ""
    ).strip().lower()
    harness = ((daily_live_snapshot.get("summary") or {}).get("harness_capabilities") or {})

    if promotion_bucket == PROMOTION_BUCKET_LIVE_READY:
        return "locked baseline controller pair remains the only live-ready routing stack"
    if lane_id == "replay-engine-hf" and promotion_bucket == PROMOTION_BUCKET_LIVE_PROBE:
        if today_execution_mode == "shadow" and not bool(harness.get("supports_standalone_probe_candidates")):
            return "replay lane marked this family as live-probe, but today it stays shadow because executor v1 cannot route standalone probes"
        return "replay lane marked this family as the current live-probe tier"
    if lane_id == "replay-engine-hf" and promotion_bucket == PROMOTION_BUCKET_SHADOW_ONLY:
        if candidate_id in REPLAY_FORCE_COMPARE_READY_IDS:
            return "strongest current non-controller replay family, but still kept in shadow rather than promoted to probe"
        if live_test_recommendation in {"shadow_live_probe", "shadow_only"}:
            return "replay lane published this family for shadow observation rather than direct probing"
        return "replay compare-ready family remains visible but outside the live-probe tier"
    if lane_id == "ml-trading" and promotion_bucket == PROMOTION_BUCKET_SHADOW_ONLY:
        return "ML v2 is compare-ready as sidecar ranking/calibration only; keep it out of the live router and away from sizing or hard gates"
    if lane_id == "llm-strategy" and promotion_bucket == PROMOTION_BUCKET_SHADOW_ONLY:
        reason = str(lane_recommendation.get("reason") or "").strip()
        if reason:
            return reason
        if deployment_recommendation in {"shadow", "shadow_only"}:
            return "LLM v2 is compare-ready, but lane recommendation still says shadow validation rather than live promotion"
        return "LLM lane remains constrained and shadow-only for now"
    if lane_id == "replay-engine-hf" and promotion_bucket == PROMOTION_BUCKET_BENCH_ONLY:
        return "replay lane marked this family for bench-only review"
    if lane_id == "ml-trading" and promotion_bucket == PROMOTION_BUCKET_BENCH_ONLY:
        return "ML gate or sizing variants stay on the bench until replay-aware sidecar use is the only role in scope"
    if lane_id == "llm-strategy" and promotion_bucket == PROMOTION_BUCKET_BENCH_ONLY:
        return "LLM bench variants stay out of promotion until constrained shadow validation succeeds"
    return "candidate remains bench-only until replay-aware evidence or deployment posture improves"


def _candidate_today_execution(
    row: dict[str, Any],
    *,
    promotion_bucket: str,
    daily_live_snapshot: dict[str, Any],
    daily_live_rollup: dict[str, dict[str, Any]],
    planned_probe_map: dict[str, dict[str, Any]],
    shadow_map: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    candidate_id = str(row.get("candidate_id") or "")
    summary = daily_live_snapshot.get("summary") if isinstance(daily_live_snapshot.get("summary"), dict) else {}
    control = summary.get("control") if isinstance(summary.get("control"), dict) else {}
    harness = summary.get("harness_capabilities") if isinstance(summary.get("harness_capabilities"), dict) else {}

    if candidate_id in daily_live_rollup:
        live_row = daily_live_rollup[candidate_id]
        return (
            str(live_row.get("today_execution") or "live"),
            "latest daily live validation already has an observed row for this candidate",
        )
    if candidate_id == str(control.get("primary_controller") or ""):
        return "live", "current daily live validation primary controller"
    if candidate_id == str(control.get("fallback_controller") or ""):
        return "live", "current daily live validation fallback controller"
    if candidate_id in planned_probe_map:
        probe = planned_probe_map[candidate_id]
        return (
            str(probe.get("today_execution") or "shadow"),
            str(probe.get("reason") or "planned probe from the latest daily live validation"),
        )
    if candidate_id in shadow_map:
        shadow = shadow_map[candidate_id]
        return (
            str(shadow.get("today_execution") or "shadow"),
            "latest daily live validation keeps this candidate in the shadow set",
        )
    if promotion_bucket == PROMOTION_BUCKET_LIVE_PROBE:
        if not bool(harness.get("supports_standalone_probe_candidates")):
            return "shadow", "executor v1 does not support standalone replay probe routing yet"
        return "probe", "candidate is in the replay live-probe tier"
    if promotion_bucket == PROMOTION_BUCKET_SHADOW_ONLY:
        return "shadow", "candidate is compare-ready but not promoted beyond shadow review"
    if promotion_bucket == PROMOTION_BUCKET_BENCH_ONLY:
        return "bench", "candidate is visible for benchmark review only"
    return None, None


def _apply_operational_posture(
    candidate_rows: list[dict[str, Any]],
    *,
    daily_live_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    if not candidate_rows:
        return []

    candidate_lookup: dict[str, str] = {}
    candidate_ids = {str(row.get("candidate_id")) for row in candidate_rows}
    for row in candidate_rows:
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        for token_source in (candidate_id, row.get("display_name")):
            token = _normalize_candidate_token(token_source)
            if token:
                candidate_lookup[token] = candidate_id
    for alias, target_id in LIVE_VALIDATION_ALIAS_MAP.items():
        if target_id in candidate_ids:
            candidate_lookup[alias] = target_id

    summary = daily_live_snapshot.get("summary") if isinstance(daily_live_snapshot.get("summary"), dict) else {}
    planned_probe_map: dict[str, dict[str, Any]] = {}
    for item in summary.get("planned_probes") or []:
        if not isinstance(item, dict):
            continue
        resolved_id = candidate_lookup.get(
            _normalize_candidate_token(item.get("candidate_id")),
            str(item.get("candidate_id") or ""),
        )
        if resolved_id:
            planned_probe_map[resolved_id] = item

    shadow_map: dict[str, dict[str, Any]] = {}
    for item in summary.get("shadow_set") or []:
        if not isinstance(item, dict):
            continue
        resolved_id = candidate_lookup.get(
            _normalize_candidate_token(item.get("candidate_id")),
            str(item.get("candidate_id") or ""),
        )
        if resolved_id:
            shadow_map[resolved_id] = item

    daily_live_rollup = _build_daily_live_rollup(daily_live_snapshot)
    enriched: list[dict[str, Any]] = []
    for source_row in candidate_rows:
        row = dict(source_row)
        candidate_id = str(row.get("candidate_id") or "")
        promotion_bucket = _candidate_promotion_bucket(row, daily_live_snapshot=daily_live_snapshot)
        today_execution_mode, today_execution_reason = _candidate_today_execution(
            row,
            promotion_bucket=promotion_bucket,
            daily_live_snapshot=daily_live_snapshot,
            daily_live_rollup=daily_live_rollup,
            planned_probe_map=planned_probe_map,
            shadow_map=shadow_map,
        )

        live_result = dict(row.get("live_observed_result") or {})
        live_rollup = daily_live_rollup.get(candidate_id)
        if live_rollup is not None:
            standard_trade_count = _clean_number(row.get("standard_trade_count"))
            live_trade_count = _clean_number(live_rollup.get("trade_count"))
            live_gap = None
            if standard_trade_count and standard_trade_count > 0 and live_trade_count is not None:
                live_gap = (float(standard_trade_count) - float(live_trade_count)) / float(standard_trade_count)
            live_result = _result_view(
                mode="live_observed",
                trade_count=live_rollup.get("trade_count"),
                live_observed_flag=True,
                live_run_count=1,
                entry_submitted_count=live_rollup.get("entry_submitted_count"),
                position_opened_count=live_rollup.get("position_opened_count"),
                live_vs_backtest_gap_trade_rate=live_gap,
            )

        row.update(
            {
                "promotion_bucket": promotion_bucket,
                "promotion_bucket_reason": _candidate_promotion_reason(
                    row,
                    promotion_bucket,
                    daily_live_snapshot=daily_live_snapshot,
                    today_execution_mode=today_execution_mode,
                ),
                "today_execution_mode": today_execution_mode,
                "today_execution_reason": today_execution_reason,
                "live_observed_result": live_result,
                "live_trade_count": live_result.get("trade_count"),
                "replay_live_run_count": live_result.get("live_run_count"),
                "live_entry_submitted_count": live_result.get("entry_submitted_count"),
                "live_position_opened_count": live_result.get("position_opened_count"),
                "live_vs_backtest_gap_trade_rate": live_result.get("live_vs_backtest_gap_trade_rate"),
                "finalist_eligible_flag": bool(row.get("comparison_ready_flag")),
                "shadow_only_flag": promotion_bucket == PROMOTION_BUCKET_SHADOW_ONLY,
                "bench_only_flag": promotion_bucket == PROMOTION_BUCKET_BENCH_ONLY,
                "daily_live_validation": {
                    "session_date": daily_live_snapshot.get("session_date"),
                    "status": daily_live_snapshot.get("status"),
                    "today_execution_mode": today_execution_mode,
                    "today_execution_reason": today_execution_reason,
                    "planned_probe": planned_probe_map.get(candidate_id),
                    "shadow_set_entry": shadow_map.get(candidate_id),
                    "live_rollup": live_rollup,
                },
            }
        )
        enriched.append(_clean_record(row))
    return enriched


def _build_global_compare_ready_ranking(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in candidate_rows
        if row.get("comparison_ready_flag")
    ]
    ordered = sorted(rows, key=_candidate_performance_sort_key, reverse=True)
    for rank, row in enumerate(ordered, start=1):
        row["global_rank"] = rank
    return [_clean_record(row) for row in ordered]


def _build_visibility_bucket_rows(
    candidate_rows: list[dict[str, Any]],
    visibility_bucket: str,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in candidate_rows if row.get("visibility_bucket") == visibility_bucket]
    ordered = sorted(rows, key=_candidate_performance_sort_key, reverse=True)
    rank_key = {
        VISIBILITY_BUCKET_SHADOW_ONLY: "shadow_rank",
        VISIBILITY_BUCKET_BENCH_ONLY: "bench_rank",
    }.get(visibility_bucket)
    if rank_key:
        for rank, row in enumerate(ordered, start=1):
            row[rank_key] = rank
    return [_clean_record(row) for row in ordered]


def _build_promotion_bucket_rows(
    candidate_rows: list[dict[str, Any]],
    promotion_bucket: str,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in candidate_rows if row.get("promotion_bucket") == promotion_bucket]
    ordered = sorted(rows, key=_candidate_performance_sort_key, reverse=True)
    rank_key = {
        PROMOTION_BUCKET_LIVE_READY: "live_ready_rank",
        PROMOTION_BUCKET_LIVE_PROBE: "live_probe_rank",
        PROMOTION_BUCKET_SHADOW_ONLY: "shadow_rank",
        PROMOTION_BUCKET_BENCH_ONLY: "bench_rank",
    }.get(promotion_bucket)
    if rank_key:
        for rank, row in enumerate(ordered, start=1):
            row[rank_key] = rank
    return [_clean_record(row) for row in ordered]


def _build_current_promoted_stack(
    *,
    live_ready_rows: list[dict[str, Any]],
    live_probe_rows: list[dict[str, Any]],
    shadow_rows: list[dict[str, Any]],
    bench_rows: list[dict[str, Any]],
    daily_live_snapshot: dict[str, Any],
) -> dict[str, Any]:
    summary = daily_live_snapshot.get("summary") if isinstance(daily_live_snapshot.get("summary"), dict) else {}
    control = summary.get("control") if isinstance(summary.get("control"), dict) else {}
    live_ready_ids = [str(row.get("candidate_id")) for row in live_ready_rows]
    live_probe_ids = [str(row.get("candidate_id")) for row in live_probe_rows]
    ml_shadow = [str(row.get("candidate_id")) for row in shadow_rows if row.get("lane_id") == "ml-trading"]
    llm_shadow = [str(row.get("candidate_id")) for row in shadow_rows if row.get("lane_id") == "llm-strategy"]
    replay_shadow = [str(row.get("candidate_id")) for row in shadow_rows if row.get("lane_id") == "replay-engine-hf"]

    note_parts = [
        "Live-ready now: "
        + (", ".join(live_ready_ids) if live_ready_ids else "no live-ready candidates are published"),
        "next probes: " + (", ".join(live_probe_ids) if live_probe_ids else "none"),
    ]
    if replay_shadow:
        note_parts.append("keep replay shadow set on review: " + ", ".join(replay_shadow))
    if ml_shadow:
        note_parts.append("keep ML v2 sidecars in shadow: " + ", ".join(ml_shadow))
    if llm_shadow:
        note_parts.append("keep LLM v2 in shadow: " + ", ".join(llm_shadow))

    return {
        "session_date": daily_live_snapshot.get("session_date"),
        "live_status": daily_live_snapshot.get("status"),
        "control_primary": control.get("primary_controller"),
        "control_fallback": control.get("fallback_controller"),
        "live_ready": live_ready_rows,
        "live_probe": live_probe_rows,
        "shadow_only": shadow_rows,
        "bench_only": bench_rows,
        "operator_note": ". ".join(note_parts) + ".",
    }


def _build_lane_rankings(candidate_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rankings: list[dict[str, Any]] = []
    for lane_id, lane_rows in (
        pd.DataFrame(candidate_rows).groupby("lane_id", dropna=False)
        if candidate_rows
        else []
    ):
        lane_rows_list = [row for row in candidate_rows if str(row.get("lane_id")) == str(lane_id)]
        compare_ready_rows = [row for row in lane_rows_list if row.get("comparison_ready_flag")]
        shadow_rows = [row for row in lane_rows_list if row.get("promotion_bucket") == PROMOTION_BUCKET_SHADOW_ONLY]
        bench_rows = [row for row in lane_rows_list if row.get("promotion_bucket") == PROMOTION_BUCKET_BENCH_ONLY]

        live_ready_rows = [row for row in lane_rows_list if row.get("promotion_bucket") == PROMOTION_BUCKET_LIVE_READY]
        live_probe_rows = [row for row in lane_rows_list if row.get("promotion_bucket") == PROMOTION_BUCKET_LIVE_PROBE]

        active_promotion_buckets = sum(
            1 for rows in (live_ready_rows, live_probe_rows, shadow_rows, bench_rows) if rows
        )
        if active_promotion_buckets > 1:
            lane_bucket = "mixed"
        elif live_ready_rows:
            lane_bucket = PROMOTION_BUCKET_LIVE_READY
        elif live_probe_rows:
            lane_bucket = PROMOTION_BUCKET_LIVE_PROBE
        elif shadow_rows:
            lane_bucket = VISIBILITY_BUCKET_SHADOW_ONLY
        elif compare_ready_rows:
            lane_bucket = VISIBILITY_BUCKET_COMPARE_READY
        else:
            lane_bucket = VISIBILITY_BUCKET_BENCH_ONLY

        top_row = None
        if compare_ready_rows:
            top_row = sorted(compare_ready_rows, key=_candidate_performance_sort_key, reverse=True)[0]
        elif shadow_rows:
            top_row = sorted(shadow_rows, key=_candidate_performance_sort_key, reverse=True)[0]
        elif bench_rows:
            top_row = sorted(bench_rows, key=_candidate_performance_sort_key, reverse=True)[0]
        if top_row is None:
            continue

        rankings.append(
            _clean_record(
                {
                    "lane_id": top_row.get("lane_id"),
                    "lane_label": top_row.get("lane_label"),
                    "lane_type": top_row.get("lane_type"),
                    "lane_bucket": lane_bucket,
                    "top_candidate_id": top_row.get("candidate_id"),
                    "top_candidate_name": top_row.get("display_name"),
                    "top_candidate_visibility_bucket": top_row.get("visibility_bucket"),
                    "top_candidate_promotion_bucket": top_row.get("promotion_bucket"),
                    "top_candidate_replay_ending_bankroll": top_row.get("replay_ending_bankroll"),
                    "top_candidate_execution_rate": top_row.get("execution_rate"),
                    "top_candidate_realism_gap_trade_rate": top_row.get("realism_gap_trade_rate"),
                    "compare_ready_subject_count": len(compare_ready_rows),
                    "live_ready_subject_count": len(live_ready_rows),
                    "live_probe_subject_count": len(live_probe_rows),
                    "shadow_only_subject_count": len(shadow_rows),
                    "bench_only_subject_count": len(bench_rows),
                }
            )
        )

    ordered = sorted(
        rankings,
        key=lambda row: (
            {
                PROMOTION_BUCKET_LIVE_READY: 0,
                PROMOTION_BUCKET_LIVE_PROBE: 1,
                VISIBILITY_BUCKET_COMPARE_READY: 0,
                "mixed": 0,
                VISIBILITY_BUCKET_SHADOW_ONLY: 2,
                VISIBILITY_BUCKET_BENCH_ONLY: 3,
            }.get(str(row.get("lane_bucket")), 4),
            -_numeric_sort(row.get("top_candidate_replay_ending_bankroll")),
            -_numeric_sort(row.get("top_candidate_execution_rate")),
            str(row.get("lane_id")),
        ),
    )
    compare_ready_only = [
        dict(row)
        for row in ordered
        if row.get("compare_ready_subject_count")
    ]
    for rank, row in enumerate(compare_ready_only, start=1):
        row["lane_rank"] = rank
    lane_rank_by_id = {str(row.get("lane_id")): row.get("lane_rank") for row in compare_ready_only}
    ordered_with_ranks: list[dict[str, Any]] = []
    for row in ordered:
        item = dict(row)
        if str(item.get("lane_id")) in lane_rank_by_id:
            item["lane_rank"] = lane_rank_by_id[str(item.get("lane_id"))]
        ordered_with_ranks.append(item)
    return [_clean_record(row) for row in ordered_with_ranks], [_clean_record(row) for row in compare_ready_only]


def _build_merge_recommendation(
    candidate_rows: list[dict[str, Any]],
    compare_ready_lane_rankings: list[dict[str, Any]],
    *,
    daily_live_snapshot: dict[str, Any],
) -> dict[str, Any]:
    compare_ready_ids = {str(row.get("candidate_id")) for row in candidate_rows if row.get("comparison_ready_flag")}
    merge_now: list[dict[str, Any]] = []
    wait: list[dict[str, Any]] = []
    merge_order: list[dict[str, Any]] = []
    harness = ((daily_live_snapshot.get("summary") or {}).get("harness_capabilities") or {})

    replay_candidates = [
        candidate_id
        for candidate_id in ("quarter_open_reprice", "micro_momentum_continuation", "inversion")
        if candidate_id in compare_ready_ids
    ]
    if replay_candidates:
        merge_now.append(
            {
                "lane_id": "replay-engine-hf",
                "recommendation": "merge_first_when_branch_handoff_is_explicit",
                "scope": (
                    "Replay engine, replay diagnostics, realism contract updates, and replay-backed deterministic/HF publications."
                ),
                "rationale": (
                    "Replay remains the realism source of truth. Merge it first so quarter_open_reprice and "
                    "micro_momentum_continuation stay authoritative as live-probe candidates and inversion stays visible as the strongest replay shadow family."
                ),
            }
        )
        merge_order.append(
            {
                "priority": 1,
                "lane_id": "replay-engine-hf",
                "status": "merge_now",
                "reason": "realism source of truth + current replay-backed probe tier",
            }
        )

    ml_top = next((row for row in compare_ready_lane_rankings if str(row.get("lane_id")) == "ml-trading"), None)
    if ml_top is not None:
        merge_now.append(
            {
                "lane_id": "ml-trading",
                "recommendation": "merge_second_for_sidecar_scope_only",
                "scope": "Replay-aware ML v2 ranking and calibration sidecars only. No ML sizing, no hard gate routing, no live-router ownership.",
                "rationale": (
                    "ML v2 is now compare-ready as a sidecar. The merge scope should stop at ranking and calibration overlays, "
                    "while the live executor still lacks dedicated ML sidecar routing."
                ),
            }
        )
        merge_order.append(
            {
                "priority": 2,
                "lane_id": "ml-trading",
                "status": "merge_now",
                "reason": "compare-ready sidecar contribution with bounded scope",
            }
        )

    llm_top = next((row for row in compare_ready_lane_rankings if str(row.get("lane_id")) == "llm-strategy"), None)
    if llm_top is not None:
        wait.append(
            {
                "lane_id": "llm-strategy",
                "recommendation": "wait_for_shadow_validation_to_clear",
                "scope": (
                    "Constrained LLM select/gate/compile lane led by "
                    f"{llm_top.get('top_candidate_id') or 'the current top compare-ready candidate'}."
                ),
                "rationale": (
                    "LLM v2 is compare-ready on replay, but the lane recommendation still says shadow. Keep it benchmark-visible "
                    "and merge only after shadow validation or deployment guidance changes."
                ),
            }
        )
        merge_order.append(
            {
                "priority": 3,
                "lane_id": "llm-strategy",
                "status": "wait",
                "reason": "compare-ready but still shadow-only by lane recommendation",
            }
        )

    if any(row.get("promotion_bucket") == PROMOTION_BUCKET_BENCH_ONLY for row in candidate_rows):
        wait.append(
            {
                "lane_id": "replay-engine-hf",
                "recommendation": "keep_bench_only_families_out_of_merge_scope",
                "scope": "Bench-only replay families plus any gate-or-sizing variants from other lanes.",
                "rationale": (
                    "Bench-only families stay in the dashboard for audit, but they should not move with the promoted merge stack "
                    "while replay remains the realism gate."
                ),
            }
        )

    if not bool(harness.get("supports_standalone_probe_candidates")):
        wait.append(
            {
                "lane_id": "daily-live-validation",
                "recommendation": "upgrade_executor_probe_routing_before_live_probe_rollout",
                "scope": "Standalone replay probe routing and optional sidecar paths in the live executor.",
                "rationale": (
                    "Daily live validation is running, but executor v1 still cannot route standalone replay probes or ML/LLM sidecars, "
                    "so live-probe and shadow lanes remain observational today."
                ),
            }
        )

    return {"merge_now": merge_now, "wait": wait, "merge_order": merge_order}

def _build_compare_ready_checks(
    *,
    publication_state: str,
    standard_trade_count: Any,
    replay_trade_count: Any,
    replay_ending_bankroll: Any,
    replay_max_drawdown_pct: Any,
    stale_signal_suppressed_count: Any,
    stale_signal_suppression_rate: Any,
    artifact_paths: dict[str, Any],
    live_observed_flag: bool,
    live_trade_count: Any,
) -> dict[str, Any]:
    standard_trade_sample = (_clean_number(standard_trade_count) or 0) > 0
    replay_execution = (_clean_number(replay_trade_count) or 0) > 0
    replay_result_metrics = (
        _clean_number(replay_ending_bankroll) is not None
        and _clean_number(replay_max_drawdown_pct) is not None
    )
    stale_signal_metrics = (
        _clean_number(stale_signal_suppressed_count) is not None
        and _clean_number(stale_signal_suppression_rate) is not None
    )
    trace_artifacts = _has_trace_artifacts(artifact_paths)
    live_observed_clarity = live_observed_flag or live_trade_count is None or _clean_number(live_trade_count) is not None

    missing_requirements: list[str] = []
    if publication_state != "published":
        missing_requirements.append("candidate_not_published")
    if not standard_trade_sample:
        missing_requirements.append("standard_trade_sample")
    if not replay_execution:
        missing_requirements.append("replay_execution")
    if not replay_result_metrics:
        missing_requirements.append("replay_result_metrics")
    if not stale_signal_metrics:
        missing_requirements.append("stale_signal_metrics")
    if not trace_artifacts:
        missing_requirements.append("trace_artifacts")
    if not live_observed_clarity:
        missing_requirements.append("live_observed_clarity")

    compare_ready_flag = len(missing_requirements) == 0
    return {
        "publication_state_ok": publication_state == "published",
        "standard_trade_sample": standard_trade_sample,
        "replay_execution": replay_execution,
        "replay_result_metrics": replay_result_metrics,
        "stale_signal_metrics": stale_signal_metrics,
        "trace_artifacts": trace_artifacts,
        "live_observed_clarity": live_observed_clarity,
        "missing_requirements": missing_requirements,
        "compare_ready_flag": compare_ready_flag,
    }


def _replay_candidate_artifacts(replay_root: Path, subject_name: str) -> dict[str, str]:
    slug = _subject_slug(subject_name)
    artifact_paths: dict[str, str] = {}
    for key, path in (
        ("standard_trade_csv", replay_root / f"standard_{slug}.csv"),
        ("standard_trade_parquet", replay_root / f"standard_{slug}.parquet"),
        ("replay_trade_csv", replay_root / f"replay_{slug}.csv"),
        ("replay_trade_parquet", replay_root / f"replay_{slug}.parquet"),
        ("replay_signal_summary_csv", replay_root / "replay_signal_summary.csv"),
        ("replay_attempt_trace_csv", replay_root / "replay_attempt_trace.csv"),
        ("replay_divergence_summary_csv", replay_root / "replay_divergence_summary.csv"),
    ):
        resolved = _existing_path_string(path)
        if resolved:
            artifact_paths[key] = resolved
    return artifact_paths


def _build_replay_signal_rollup(replay_root: Path) -> pd.DataFrame:
    signal_summary = _read_table(replay_root, "replay_signal_summary")
    if signal_summary.empty:
        return pd.DataFrame()

    signal_summary["executed_flag_bool"] = signal_summary["executed_flag"].fillna(False).astype(bool)
    signal_summary["blocked_flag"] = signal_summary["no_trade_reason"].notna()
    signal_summary["stale_signal_blocked_flag"] = signal_summary["no_trade_reason"].astype(str) == "signal_stale"

    return (
        signal_summary.groupby(["subject_name", "subject_type"], dropna=False)
        .agg(
            observed_signal_count=("signal_id", "count"),
            replay_executed_signal_count=("executed_flag_bool", "sum"),
            blocked_signal_count=("blocked_flag", "sum"),
            stale_signal_suppressed_count=("stale_signal_blocked_flag", "sum"),
        )
        .reset_index()
    )


def _build_replay_subject_rows(
    request: UnifiedBenchmarkRequest,
    *,
    replay_root: Path,
    replay_run_payload: dict[str, Any],
    run_metadata_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    subject_summary = _read_table(replay_root, "replay_subject_summary")
    if subject_summary.empty:
        return []

    portfolio_summary = _read_table(replay_root, "replay_portfolio_summary")
    live_summary = _read_table(replay_root, "replay_live_summary")
    signal_rollup = _build_replay_signal_rollup(replay_root)

    for column in (
        "standard_trade_count",
        "replay_trade_count",
        "trade_gap",
        "execution_rate",
        "standard_avg_return_with_slippage",
        "replay_avg_return_with_slippage",
        "replay_no_trade_count",
        "standard_ending_bankroll",
        "replay_ending_bankroll",
    ):
        if column in subject_summary.columns:
            subject_summary[column] = pd.to_numeric(subject_summary[column], errors="coerce")

    standard_metrics = (
        portfolio_summary[portfolio_summary["mode"].astype(str) == "standard"]
        .rename(
            columns={
                "ending_bankroll": "standard_portfolio_ending_bankroll",
                "compounded_return": "standard_compounded_return",
                "max_drawdown_pct": "standard_max_drawdown_pct",
                "max_drawdown_amount": "standard_max_drawdown_amount",
                "executed_trade_count": "standard_portfolio_trade_count",
            }
        )
        .drop(columns=["mode"], errors="ignore")
    )
    replay_metrics = (
        portfolio_summary[portfolio_summary["mode"].astype(str) == "replay"]
        .rename(
            columns={
                "ending_bankroll": "replay_portfolio_ending_bankroll",
                "compounded_return": "replay_compounded_return",
                "max_drawdown_pct": "replay_max_drawdown_pct",
                "max_drawdown_amount": "replay_max_drawdown_amount",
                "executed_trade_count": "replay_portfolio_trade_count",
            }
        )
        .drop(columns=["mode"], errors="ignore")
    )

    merged = subject_summary.merge(
        standard_metrics,
        on=["subject_name", "subject_type"],
        how="left",
    ).merge(
        replay_metrics,
        on=["subject_name", "subject_type"],
        how="left",
    )
    if not signal_rollup.empty:
        merged = merged.merge(signal_rollup, on=["subject_name", "subject_type"], how="left")

    live_rollup = pd.DataFrame()
    if not live_summary.empty:
        live_summary["live_trade_count"] = pd.to_numeric(live_summary["live_trade_count"], errors="coerce")
        live_summary["entry_submitted_count"] = pd.to_numeric(
            live_summary["entry_submitted_count"], errors="coerce"
        )
        live_summary["position_opened_count"] = pd.to_numeric(
            live_summary["position_opened_count"], errors="coerce"
        )
        live_rollup = (
            live_summary.groupby("subject_name", dropna=False)
            .agg(
                live_trade_count_rollup=("live_trade_count", "sum"),
                live_entry_submitted_count=("entry_submitted_count", "sum"),
                live_position_opened_count=("position_opened_count", "sum"),
                live_run_count=("run_id", pd.Series.nunique),
            )
            .reset_index()
        )
        merged = merged.merge(live_rollup, on="subject_name", how="left")

    published_at = _parse_markdown_value(
        _read_markdown(_resolve_shared_root(request.shared_root) / "handoffs" / "replay-engine-hf" / "status.md"),
        "timestamp",
    )
    rows: list[dict[str, Any]] = []
    for record in merged.to_dict(orient="records"):
        subject_name = str(record.get("subject_name") or "")
        subject_type = str(record.get("subject_type") or "")
        lane_id = "locked-baselines" if subject_name in LOCKED_BASELINE_SUBJECTS else "replay-engine-hf"
        candidate_kind = (
            "baseline_controller"
            if lane_id == "locked-baselines"
            else ("hf_strategy" if subject_name in REPLAY_HIGH_FREQUENCY_FAMILIES else "deterministic_strategy")
        )
        standard_trade_count = _clean_number(record.get("standard_trade_count"))
        replay_trade_count = _clean_number(record.get("replay_trade_count"))
        blocked_signal_count = _clean_number(record.get("blocked_signal_count") or record.get("replay_no_trade_count"))
        stale_signal_suppressed_count = _clean_number(record.get("stale_signal_suppressed_count"))
        realism_gap = None
        if standard_trade_count and standard_trade_count > 0 and replay_trade_count is not None:
            realism_gap = (float(standard_trade_count) - float(replay_trade_count)) / float(standard_trade_count)
        stale_signal_rate = None
        if standard_trade_count and standard_trade_count > 0 and stale_signal_suppressed_count is not None:
            stale_signal_rate = float(stale_signal_suppressed_count) / float(standard_trade_count)
        stale_signal_share = None
        if blocked_signal_count and blocked_signal_count > 0 and stale_signal_suppressed_count is not None:
            stale_signal_share = float(stale_signal_suppressed_count) / float(blocked_signal_count)

        live_observed_flag = _clean_number(record.get("live_run_count")) is not None
        live_trade_count = _clean_number(record.get("live_trade_count_rollup")) if live_observed_flag else None
        live_vs_backtest_gap = None
        if live_observed_flag and standard_trade_count and standard_trade_count > 0 and live_trade_count is not None:
            live_vs_backtest_gap = (float(standard_trade_count) - float(live_trade_count)) / float(standard_trade_count)

        artifact_paths = {
            **_replay_candidate_artifacts(replay_root, subject_name),
            "replay_root": str(replay_root),
            "replay_json": run_metadata_payload.get("replay_json") or str(replay_root / "replay_run.json"),
            "replay_markdown": run_metadata_payload.get("replay_markdown")
            or str(replay_root / "replay_run.md"),
            "lane_report": run_metadata_payload.get("ranked_memo")
            or str(_resolve_shared_root(request.shared_root) / "reports" / "replay-engine-hf" / "ranked_memo.md"),
        }

        standard_result = _result_view(
            mode="standard_backtest",
            trade_count=standard_trade_count,
            ending_bankroll=record.get("standard_ending_bankroll") or record.get("standard_portfolio_ending_bankroll"),
            avg_return_with_slippage=record.get("standard_avg_return_with_slippage"),
            compounded_return=record.get("standard_compounded_return"),
            max_drawdown_pct=record.get("standard_max_drawdown_pct"),
            max_drawdown_amount=record.get("standard_max_drawdown_amount"),
        )
        replay_result = _result_view(
            mode="replay_result",
            trade_count=replay_trade_count,
            ending_bankroll=record.get("replay_ending_bankroll") or record.get("replay_portfolio_ending_bankroll"),
            avg_return_with_slippage=record.get("replay_avg_return_with_slippage"),
            compounded_return=record.get("replay_compounded_return"),
            max_drawdown_pct=record.get("replay_max_drawdown_pct"),
            max_drawdown_amount=record.get("replay_max_drawdown_amount"),
            no_trade_count=record.get("replay_no_trade_count"),
            execution_rate=record.get("execution_rate"),
        )
        live_result = _result_view(
            mode="live_observed",
            trade_count=live_trade_count,
            live_observed_flag=live_observed_flag,
            live_run_count=record.get("live_run_count"),
            entry_submitted_count=record.get("live_entry_submitted_count"),
            position_opened_count=record.get("live_position_opened_count"),
            live_vs_backtest_gap_trade_rate=live_vs_backtest_gap,
        )
        replay_realism = _realism_view(
            trade_gap=record.get("trade_gap"),
            execution_rate=record.get("execution_rate"),
            realism_gap_trade_rate=realism_gap,
            top_no_trade_reason=record.get("top_no_trade_reason"),
            blocked_signal_count=blocked_signal_count,
            stale_signal_suppressed_count=stale_signal_suppressed_count,
            stale_signal_suppression_rate=stale_signal_rate,
            stale_signal_share_of_blocked_signals=stale_signal_share,
        )
        compare_ready_checks = _build_compare_ready_checks(
            publication_state="published",
            standard_trade_count=standard_result.get("trade_count"),
            replay_trade_count=replay_result.get("trade_count"),
            replay_ending_bankroll=replay_result.get("ending_bankroll"),
            replay_max_drawdown_pct=replay_result.get("max_drawdown_pct"),
            stale_signal_suppressed_count=replay_realism.get("stale_signal_suppressed_count"),
            stale_signal_suppression_rate=replay_realism.get("stale_signal_suppression_rate"),
            artifact_paths=artifact_paths,
            live_observed_flag=bool(live_result.get("live_observed_flag")),
            live_trade_count=live_result.get("trade_count"),
        )

        rows.append(
            _clean_record(
                {
                    "candidate_id": subject_name,
                    "display_name": subject_name,
                    "lane_id": lane_id,
                    "lane_label": LANE_SPEC_LOOKUP[lane_id]["label"],
                    "lane_type": LANE_SPEC_LOOKUP[lane_id]["lane_type"],
                    "dashboard_bucket": _dashboard_bucket_for_lane(lane_id, candidate_kind),
                    "candidate_kind": candidate_kind,
                    "subject_type": subject_type,
                    "baseline_locked_flag": subject_name in LOCKED_BASELINE_SUBJECTS,
                    "publication_state": "published",
                    "published_at": published_at,
                    "comparison_scope": {
                        "season": request.season,
                        "phase_group": replay_run_payload.get("season_phase") or "play_in,playoffs",
                        "replay_contract_version": (
                            (replay_run_payload.get("replay_contract") or {}).get("maturity")
                            or _parse_markdown_value(
                                _read_markdown(
                                    _resolve_shared_root(request.shared_root)
                                    / "benchmark_contract"
                                    / "replay_contract_current.md"
                                ),
                                "maturity",
                            )
                        ),
                    },
                    "standard_result": standard_result,
                    "replay_result": replay_result,
                    "live_observed_result": live_result,
                    "replay_realism": replay_realism,
                    "artifact_paths": artifact_paths,
                    "compare_ready_checks": compare_ready_checks,
                    "comparison_ready_flag": compare_ready_checks["compare_ready_flag"],
                    # Flat compatibility fields.
                    "standard_trade_count": standard_result.get("trade_count"),
                    "replay_trade_count": replay_result.get("trade_count"),
                    "live_trade_count": live_result.get("trade_count"),
                    "trade_gap": replay_realism.get("trade_gap"),
                    "execution_rate": replay_realism.get("execution_rate"),
                    "realism_gap_trade_rate": replay_realism.get("realism_gap_trade_rate"),
                    "stale_signal_suppressed_count": replay_realism.get("stale_signal_suppressed_count"),
                    "stale_signal_suppression_rate": replay_realism.get("stale_signal_suppression_rate"),
                    "stale_signal_share_of_blocked_signals": replay_realism.get(
                        "stale_signal_share_of_blocked_signals"
                    ),
                    "top_no_trade_reason": replay_realism.get("top_no_trade_reason"),
                    "standard_avg_return_with_slippage": standard_result.get("avg_return_with_slippage"),
                    "replay_avg_return_with_slippage": replay_result.get("avg_return_with_slippage"),
                    "standard_ending_bankroll": standard_result.get("ending_bankroll"),
                    "replay_ending_bankroll": replay_result.get("ending_bankroll"),
                    "standard_compounded_return": standard_result.get("compounded_return"),
                    "replay_compounded_return": replay_result.get("compounded_return"),
                    "standard_max_drawdown_pct": standard_result.get("max_drawdown_pct"),
                    "standard_max_drawdown_amount": standard_result.get("max_drawdown_amount"),
                    "replay_max_drawdown_pct": replay_result.get("max_drawdown_pct"),
                    "replay_max_drawdown_amount": replay_result.get("max_drawdown_amount"),
                    "replay_no_trade_count": replay_result.get("no_trade_count"),
                    "replay_live_run_count": live_result.get("live_run_count"),
                    "live_entry_submitted_count": live_result.get("entry_submitted_count"),
                    "live_position_opened_count": live_result.get("position_opened_count"),
                    "live_vs_backtest_gap_trade_rate": live_result.get("live_vs_backtest_gap_trade_rate"),
                }
            )
        )
    return rows


def _manifest_metric_from_result_view(result_view: dict[str, Any], key: str) -> Any:
    if not isinstance(result_view, dict):
        return None
    return result_view.get(key)


def _load_manifest_submission_rows(
    shared_root: Path,
    *,
    manifest_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in manifest_records or _manifest_submission_records(shared_root):
        payload = record["payload"]
        submission_path = Path(record["submission_path"])
        lane_id = str(record["lane_id"])
        subjects = payload.get("subjects")
        if not isinstance(subjects, list):
            continue

        for subject in subjects:
            if not isinstance(subject, dict):
                continue
            candidate_id = str(
                subject.get("candidate_id")
                or subject.get("subject_name")
                or subject.get("display_name")
                or ""
            )
            effective_lane_id = (
                "locked-baselines"
                if lane_id == "replay-engine-hf" and candidate_id in LOCKED_BASELINE_SUBJECTS
                else lane_id
            )
            lane_spec = LANE_SPEC_LOOKUP.get(effective_lane_id, {})
            candidate_kind = str(subject.get("candidate_kind") or "").strip() or None
            raw_artifacts = subject.get("artifacts") if isinstance(subject.get("artifacts"), dict) else {}
            raw_trace_artifacts = (
                subject.get("trace_artifacts") if isinstance(subject.get("trace_artifacts"), dict) else {}
            )
            artifact_paths = {
                **_normalize_artifact_paths(raw_artifacts, base_dir=submission_path.parent),
                **_normalize_artifact_paths(raw_trace_artifacts, base_dir=submission_path.parent),
                "submission_json": str(submission_path),
            }

            metrics = subject.get("metrics") if isinstance(subject.get("metrics"), dict) else {}
            result_views = subject.get("result_views") if isinstance(subject.get("result_views"), dict) else {}
            standard_result = result_views.get("standard_backtest") or {
                "mode": "standard_backtest",
                "trade_count": metrics.get("standard_trade_count"),
                "ending_bankroll": metrics.get("standard_ending_bankroll"),
                "avg_return_with_slippage": metrics.get("standard_avg_return_with_slippage"),
                "compounded_return": metrics.get("standard_compounded_return"),
                "max_drawdown_pct": metrics.get("standard_max_drawdown_pct"),
                "max_drawdown_amount": metrics.get("standard_max_drawdown_amount"),
            }
            replay_result = result_views.get("replay_result") or {
                "mode": "replay_result",
                "trade_count": metrics.get("replay_trade_count"),
                "ending_bankroll": metrics.get("replay_ending_bankroll"),
                "avg_return_with_slippage": metrics.get("replay_avg_return_with_slippage"),
                "compounded_return": metrics.get("replay_compounded_return"),
                "max_drawdown_pct": metrics.get("replay_max_drawdown_pct"),
                "max_drawdown_amount": metrics.get("replay_max_drawdown_amount"),
                "no_trade_count": metrics.get("replay_no_trade_count"),
                "execution_rate": metrics.get("execution_rate"),
            }
            live_result = result_views.get("live_observed") or {
                "mode": "live_observed",
                "trade_count": metrics.get("live_trade_count"),
                "live_observed_flag": subject.get("live_observed_flag"),
                "live_run_count": metrics.get("live_run_count"),
                "entry_submitted_count": metrics.get("live_entry_submitted_count"),
                "position_opened_count": metrics.get("live_position_opened_count"),
                "live_vs_backtest_gap_trade_rate": metrics.get("live_vs_backtest_gap_trade_rate"),
            }
            replay_realism = (
                subject.get("replay_realism") if isinstance(subject.get("replay_realism"), dict) else {}
            ) or {
                "trade_gap": metrics.get("trade_gap"),
                "execution_rate": metrics.get("execution_rate"),
                "realism_gap_trade_rate": metrics.get("realism_gap_trade_rate"),
                "top_no_trade_reason": metrics.get("top_no_trade_reason"),
                "blocked_signal_count": metrics.get("blocked_signal_count") or metrics.get("replay_no_trade_count"),
                "stale_signal_suppressed_count": metrics.get("stale_signal_suppressed_count"),
                "stale_signal_suppression_rate": metrics.get("stale_signal_suppression_rate"),
                "stale_signal_share_of_blocked_signals": metrics.get(
                    "stale_signal_share_of_blocked_signals"
                ),
            }

            live_flag = live_result.get("live_observed_flag")
            standard_result = _result_view(
                mode="standard_backtest",
                trade_count=_manifest_metric_from_result_view(standard_result, "trade_count"),
                ending_bankroll=_manifest_metric_from_result_view(standard_result, "ending_bankroll"),
                avg_return_with_slippage=_manifest_metric_from_result_view(
                    standard_result, "avg_return_with_slippage"
                ),
                compounded_return=_manifest_metric_from_result_view(standard_result, "compounded_return"),
                max_drawdown_pct=_manifest_metric_from_result_view(standard_result, "max_drawdown_pct"),
                max_drawdown_amount=_manifest_metric_from_result_view(
                    standard_result, "max_drawdown_amount"
                ),
            )
            replay_result = _result_view(
                mode="replay_result",
                trade_count=_manifest_metric_from_result_view(replay_result, "trade_count"),
                ending_bankroll=_manifest_metric_from_result_view(replay_result, "ending_bankroll"),
                avg_return_with_slippage=_manifest_metric_from_result_view(
                    replay_result, "avg_return_with_slippage"
                ),
                compounded_return=_manifest_metric_from_result_view(replay_result, "compounded_return"),
                max_drawdown_pct=_manifest_metric_from_result_view(replay_result, "max_drawdown_pct"),
                max_drawdown_amount=_manifest_metric_from_result_view(replay_result, "max_drawdown_amount"),
                no_trade_count=_manifest_metric_from_result_view(replay_result, "no_trade_count"),
                execution_rate=_manifest_metric_from_result_view(replay_result, "execution_rate"),
            )
            live_result = _result_view(
                mode="live_observed",
                trade_count=_manifest_metric_from_result_view(live_result, "trade_count"),
                live_observed_flag=live_flag if live_flag is not None else None,
                live_run_count=_manifest_metric_from_result_view(live_result, "live_run_count"),
                entry_submitted_count=_manifest_metric_from_result_view(
                    live_result, "entry_submitted_count"
                ),
                position_opened_count=_manifest_metric_from_result_view(
                    live_result, "position_opened_count"
                ),
                live_vs_backtest_gap_trade_rate=_manifest_metric_from_result_view(
                    live_result, "live_vs_backtest_gap_trade_rate"
                ),
            )
            replay_realism = _realism_view(
                trade_gap=replay_realism.get("trade_gap"),
                execution_rate=replay_realism.get("execution_rate"),
                realism_gap_trade_rate=replay_realism.get("realism_gap_trade_rate"),
                top_no_trade_reason=replay_realism.get("top_no_trade_reason"),
                blocked_signal_count=replay_realism.get("blocked_signal_count"),
                stale_signal_suppressed_count=replay_realism.get("stale_signal_suppressed_count"),
                stale_signal_suppression_rate=replay_realism.get("stale_signal_suppression_rate"),
                stale_signal_share_of_blocked_signals=replay_realism.get(
                    "stale_signal_share_of_blocked_signals"
                ),
            )

            compare_ready_checks = _build_compare_ready_checks(
                publication_state=str(subject.get("publication_state") or "published"),
                standard_trade_count=standard_result.get("trade_count"),
                replay_trade_count=replay_result.get("trade_count"),
                replay_ending_bankroll=replay_result.get("ending_bankroll"),
                replay_max_drawdown_pct=replay_result.get("max_drawdown_pct"),
                stale_signal_suppressed_count=replay_realism.get("stale_signal_suppressed_count"),
                stale_signal_suppression_rate=replay_realism.get("stale_signal_suppression_rate"),
                artifact_paths=artifact_paths,
                live_observed_flag=bool(live_result.get("live_observed_flag")),
                live_trade_count=live_result.get("trade_count"),
            )

            rows.append(
                _clean_record(
                    {
                        "candidate_id": candidate_id,
                        "display_name": subject.get("display_name") or candidate_id,
                        "lane_id": effective_lane_id,
                        "lane_label": payload.get("lane_label") or lane_spec.get("label") or effective_lane_id,
                        "lane_type": payload.get("lane_type") or lane_spec.get("lane_type") or "external",
                        "dashboard_bucket": _dashboard_bucket_for_lane(effective_lane_id, candidate_kind),
                        "candidate_kind": candidate_kind
                        or ("ml_strategy" if effective_lane_id == "ml-trading" else "llm_strategy"),
                        "subject_type": subject.get("subject_type") or "candidate",
                        "baseline_locked_flag": bool(subject.get("baseline_locked_flag"))
                        or candidate_id in LOCKED_BASELINE_SUBJECTS,
                        "publication_state": subject.get("publication_state") or "published",
                        "published_at": payload.get("published_at"),
                        "comparison_scope": subject.get("comparison_scope")
                        or payload.get("comparison_scope")
                        or {},
                        "lane_recommendation": payload.get("lane_recommendation")
                        if isinstance(payload.get("lane_recommendation"), dict)
                        else {},
                        "notes": list(subject.get("notes") or []),
                        "manifest_compare_ready_flag": subject.get("comparison_ready_flag"),
                        "manifest_report_folder": record["report_folder"],
                        "focus_rank": subject.get("focus_rank"),
                        "live_test_recommendation": subject.get("live_test_recommendation"),
                        "replay_rank": subject.get("replay_rank"),
                        "standard_result": standard_result,
                        "replay_result": replay_result,
                        "live_observed_result": live_result,
                        "replay_realism": replay_realism,
                        "artifact_paths": artifact_paths,
                        "compare_ready_checks": compare_ready_checks,
                        "comparison_ready_flag": compare_ready_checks["compare_ready_flag"],
                        # Flat compatibility fields.
                        "standard_trade_count": standard_result.get("trade_count"),
                        "replay_trade_count": replay_result.get("trade_count"),
                        "live_trade_count": live_result.get("trade_count"),
                        "trade_gap": replay_realism.get("trade_gap"),
                        "execution_rate": replay_realism.get("execution_rate"),
                        "realism_gap_trade_rate": replay_realism.get("realism_gap_trade_rate"),
                        "stale_signal_suppressed_count": replay_realism.get("stale_signal_suppressed_count"),
                        "stale_signal_suppression_rate": replay_realism.get("stale_signal_suppression_rate"),
                        "stale_signal_share_of_blocked_signals": replay_realism.get(
                            "stale_signal_share_of_blocked_signals"
                        ),
                        "top_no_trade_reason": replay_realism.get("top_no_trade_reason"),
                        "standard_avg_return_with_slippage": standard_result.get("avg_return_with_slippage"),
                        "replay_avg_return_with_slippage": replay_result.get("avg_return_with_slippage"),
                        "standard_ending_bankroll": standard_result.get("ending_bankroll"),
                        "replay_ending_bankroll": replay_result.get("ending_bankroll"),
                        "standard_compounded_return": standard_result.get("compounded_return"),
                        "replay_compounded_return": replay_result.get("compounded_return"),
                        "standard_max_drawdown_pct": standard_result.get("max_drawdown_pct"),
                        "standard_max_drawdown_amount": standard_result.get("max_drawdown_amount"),
                        "replay_max_drawdown_pct": replay_result.get("max_drawdown_pct"),
                        "replay_max_drawdown_amount": replay_result.get("max_drawdown_amount"),
                        "replay_no_trade_count": replay_result.get("no_trade_count"),
                        "replay_live_run_count": live_result.get("live_run_count"),
                        "live_entry_submitted_count": live_result.get("entry_submitted_count"),
                        "live_position_opened_count": live_result.get("position_opened_count"),
                        "live_vs_backtest_gap_trade_rate": live_result.get("live_vs_backtest_gap_trade_rate"),
                    }
                )
            )
    return rows


def _default_lane_status_rows(
    shared_root: Path,
    *,
    lane_publications: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lane_publications = lane_publications or {}
    for spec in KNOWN_LANE_SPECS:
        lane_id = str(spec["lane_id"])
        publication = lane_publications.get(lane_id, {})
        rows.append(
            {
                "lane_id": lane_id,
                "lane_label": spec["label"],
                "lane_type": spec["lane_type"],
                "submission_mode": spec["submission_mode"],
                "publication_state": "awaiting_first_submission",
                "criteria_ready_flag": False,
                "compare_ready_flag": False,
                "criteria_ready_subject_count": 0,
                "compare_ready_subject_count": 0,
                "live_ready_subject_count": 0,
                "live_probe_subject_count": 0,
                "shadow_only_subject_count": 0,
                "bench_only_subject_count": 0,
                "published_subject_count": 0,
                "pending_subject_count": 0,
                "lane_bucket": VISIBILITY_BUCKET_BENCH_ONLY,
                "notes": [],
                "artifact_paths": {
                    "reports_root": publication.get("report_root") or str(shared_root / "reports" / lane_id),
                    "handoff_status": publication.get("handoff_status")
                    or _resolve_lane_handoff_status_path(shared_root, lane_id=lane_id),
                    "submission_json": publication.get("submission_path")
                    or str(shared_root / "reports" / lane_id / "benchmark_submission.json"),
                },
            }
        )
    return rows


def _build_lane_status_rows(
    shared_root: Path,
    *,
    candidate_rows: list[dict[str, Any]],
    replay_root: Path,
    replay_contract_markdown: str,
    run_metadata_payload: dict[str, Any],
    lane_publications: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = {
        row["lane_id"]: dict(row)
        for row in _default_lane_status_rows(shared_root, lane_publications=lane_publications)
    }
    replay_status = rows["replay-engine-hf"]
    replay_status["artifact_paths"].update(
        {
            "replay_contract": str(shared_root / "benchmark_contract" / "replay_contract_current.md"),
            "replay_root": str(replay_root),
            "run_metadata_json": str(shared_root / "reports" / "replay-engine-hf" / "run_metadata.json"),
            "ranked_memo": run_metadata_payload.get("ranked_memo")
            or str(shared_root / "reports" / "replay-engine-hf" / "ranked_memo.md"),
        }
    )
    replay_status["publication_state"] = "published" if replay_root.exists() else "awaiting_first_submission"
    replay_status["notes"] = [
        note
        for note in [
            f"Replay contract maturity: {_parse_markdown_value(replay_contract_markdown, 'maturity') or 'unknown'}",
            "Replay artifacts are the realism baseline for deterministic and HF challengers."
            if replay_root.exists()
            else None,
        ]
        if note
    ]

    baseline_status = rows["locked-baselines"]
    baseline_status["publication_state"] = "published" if replay_root.exists() else "awaiting_replay_publication"
    baseline_status["notes"] = [
        "Locked controller pair remains the benchmark anchor.",
    ]
    baseline_status["artifact_paths"].update(
        {
            "current_system_state": str(
                Path(__file__).resolve().parents[5]
                / "docs"
                / "reference"
                / "current_analysis_system_state.md"
            ),
        }
    )

    candidate_frame = pd.DataFrame(candidate_rows)
    if not candidate_frame.empty and "lane_id" in candidate_frame.columns:
        for lane_id, lane_rows in candidate_frame.groupby("lane_id", dropna=False):
            lane_key = str(lane_id)
            lane_status = rows.get(lane_key)
            publication = (lane_publications or {}).get(lane_key, {})
            if lane_status is None:
                lane_status = {
                    "lane_id": lane_key,
                    "lane_label": lane_key,
                    "lane_type": "external",
                    "submission_mode": "manifest",
                    "publication_state": "published",
                    "criteria_ready_flag": False,
                    "compare_ready_flag": False,
                    "criteria_ready_subject_count": 0,
                    "compare_ready_subject_count": 0,
                    "live_ready_subject_count": 0,
                    "live_probe_subject_count": 0,
                    "shadow_only_subject_count": 0,
                    "bench_only_subject_count": 0,
                    "published_subject_count": 0,
                    "pending_subject_count": 0,
                    "lane_bucket": VISIBILITY_BUCKET_BENCH_ONLY,
                    "notes": [],
                    "artifact_paths": {
                        "reports_root": publication.get("report_root") or str(shared_root / "reports" / lane_key),
                        "handoff_status": publication.get("handoff_status")
                        or _resolve_lane_handoff_status_path(shared_root, lane_id=lane_key),
                        "submission_json": publication.get("submission_path")
                        or str(shared_root / "reports" / lane_key / "benchmark_submission.json"),
                    },
                }
                rows[lane_key] = lane_status
            criteria_ready_count = int(lane_rows["comparison_ready_flag"].fillna(False).astype(bool).sum())
            compare_ready_count = int(
                lane_rows["comparison_ready_flag"].fillna(False).astype(bool).sum()
            )
            live_ready_count = int(
                (lane_rows["promotion_bucket"] == PROMOTION_BUCKET_LIVE_READY).fillna(False).astype(bool).sum()
            ) if "promotion_bucket" in lane_rows else 0
            live_probe_count = int(
                (lane_rows["promotion_bucket"] == PROMOTION_BUCKET_LIVE_PROBE).fillna(False).astype(bool).sum()
            ) if "promotion_bucket" in lane_rows else 0
            shadow_count = int(lane_rows["shadow_only_flag"].fillna(False).astype(bool).sum())
            bench_count = int(lane_rows["bench_only_flag"].fillna(False).astype(bool).sum())
            active_bucket_count = sum(
                1
                for count in (live_ready_count, live_probe_count, shadow_count, bench_count)
                if count > 0
            )
            lane_bucket = (
                PROMOTION_BUCKET_LIVE_READY if live_ready_count > 0 else (
                    PROMOTION_BUCKET_LIVE_PROBE if live_probe_count > 0 else (
                        VISIBILITY_BUCKET_SHADOW_ONLY if shadow_count > 0 else (
                            VISIBILITY_BUCKET_COMPARE_READY if compare_ready_count > 0 else VISIBILITY_BUCKET_BENCH_ONLY
                        )
                    )
                )
            )
            if active_bucket_count > 1:
                lane_bucket = "mixed"
            lane_status["published_subject_count"] = int(len(lane_rows))
            lane_status["criteria_ready_subject_count"] = criteria_ready_count
            lane_status["compare_ready_subject_count"] = compare_ready_count
            lane_status["live_ready_subject_count"] = live_ready_count
            lane_status["live_probe_subject_count"] = live_probe_count
            lane_status["shadow_only_subject_count"] = shadow_count
            lane_status["bench_only_subject_count"] = bench_count
            lane_status["pending_subject_count"] = int(len(lane_rows) - criteria_ready_count)
            lane_status["criteria_ready_flag"] = criteria_ready_count > 0
            lane_status["compare_ready_flag"] = compare_ready_count > 0
            lane_status["lane_bucket"] = lane_bucket
            if lane_status["publication_state"].startswith("awaiting") and len(lane_rows) > 0:
                lane_status["publication_state"] = "published"
            if lane_key == "replay-engine-hf" and compare_ready_count > 0:
                lane_status["notes"].append("Only replay-executed compare-ready candidates join the finalist set.")
            if lane_key == "ml-trading" and criteria_ready_count > 0:
                lane_status["notes"].append("ML v2 is compare-ready as sidecar only; keep it out of sizing, gates, and the live router.")
            if lane_key == "llm-strategy" and compare_ready_count > 0:
                lane_status["notes"].append("LLM v2 is compare-ready in constrained select/gate/compile mode, but current deployment posture remains shadow.")

    ordered = []
    for spec in KNOWN_LANE_SPECS:
        ordered.append(_clean_record(rows[spec["lane_id"]]))
    for lane_id in sorted(set(rows).difference(LANE_SPEC_LOOKUP)):
        ordered.append(_clean_record(rows[lane_id]))
    return ordered


def _rank_replay_hf_candidates(candidate_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    replay_lane_rows = [
        row for row in candidate_rows if row.get("lane_id") == "replay-engine-hf" and not row.get("baseline_locked_flag")
    ]
    compare_ready = [
        row for row in replay_lane_rows if row.get("comparison_ready_flag")
    ]
    pending = [
        row for row in replay_lane_rows if not row.get("comparison_ready_flag")
    ]

    compare_ready_sorted = sorted(compare_ready, key=_candidate_performance_sort_key, reverse=True)
    pending_sorted = sorted(
        pending,
        key=lambda row: (
            row.get("visibility_bucket") == VISIBILITY_BUCKET_SHADOW_ONLY,
            *_candidate_performance_sort_key(row),
        ),
        reverse=True,
    )

    for rank, row in enumerate(compare_ready_sorted, start=1):
        row["challenger_rank"] = rank
        row["challenger_slot"] = "compare_ready_replay_challenger"
    for row in pending_sorted:
        row["challenger_slot"] = "published_pending_replay_challenger"
    return compare_ready_sorted, pending_sorted


def _build_finalists(
    compare_ready_ranking: list[dict[str, Any]],
    *,
    finalist_limit: int,
) -> list[dict[str, Any]]:
    locked = [row for row in compare_ready_ranking if row.get("baseline_locked_flag")]
    challengers = [row for row in compare_ready_ranking if not row.get("baseline_locked_flag")]
    locked_sorted = sorted(
        locked,
        key=_candidate_performance_sort_key,
        reverse=True,
    )
    challenger_sorted = sorted(challengers, key=_candidate_performance_sort_key, reverse=True)

    finalists = locked_sorted[:]
    seen = {str(row.get("candidate_id")) for row in finalists}
    for row in challenger_sorted:
        key = str(row.get("candidate_id"))
        if key in seen:
            continue
        finalists.append(row)
        seen.add(key)
        if len(finalists) >= finalist_limit:
            break

    output: list[dict[str, Any]] = []
    for rank, row in enumerate(finalists[:finalist_limit], start=1):
        item = dict(row)
        item["finalist_rank"] = rank
        item["finalist_reason"] = (
            "locked baseline" if row.get("baseline_locked_flag") else "strongest compare-ready challenger"
        )
        output.append(_clean_record(item))
    return output


def _build_replay_metadata(
    request: UnifiedBenchmarkRequest,
    *,
    shared_root: Path,
    replay_root: Path,
    replay_run_payload: dict[str, Any],
    replay_contract_markdown: str,
    run_metadata_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "shared_root": str(shared_root),
        "replay_root": str(replay_root),
        "season": request.season,
        "replay_artifact_name": request.replay_artifact_name,
        "schema_version": UNIFIED_BENCHMARK_SCHEMA_VERSION,
        "replay_contract_path": str(shared_root / "benchmark_contract" / "replay_contract_current.md"),
        "replay_contract_owner": _parse_markdown_value(replay_contract_markdown, "owner lane"),
        "replay_contract_maturity": _parse_markdown_value(replay_contract_markdown, "maturity"),
        "replay_contract_snapshot_date": _parse_markdown_value(replay_contract_markdown, "snapshot date"),
        "finished_game_count": replay_run_payload.get("finished_game_count")
        or run_metadata_payload.get("finished_game_count"),
        "state_panel_game_count": replay_run_payload.get("state_panel_game_count")
        or run_metadata_payload.get("state_panel_game_count"),
        "derived_bundle_game_count": replay_run_payload.get("derived_bundle_game_count")
        or run_metadata_payload.get("derived_bundle_game_count"),
    }


def _build_divergence_rows(replay_root: Path) -> list[dict[str, Any]]:
    frame = _read_table(replay_root, "replay_divergence_summary")
    if frame.empty:
        return []
    frame["signal_count"] = pd.to_numeric(frame["signal_count"], errors="coerce")
    ordered = frame.sort_values(["signal_count", "subject_name"], ascending=[False, True])
    return [_clean_record(row) for row in ordered.head(12).to_dict(orient="records")]


def _build_game_gap_rows(replay_root: Path, finalists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = _read_table(replay_root, "replay_game_gap")
    if frame.empty:
        return []
    finalist_names = {str(row.get("candidate_id")) for row in finalists}
    filtered = frame[frame["subject_name"].astype(str).isin(finalist_names)].copy()
    if not filtered.empty:
        frame = filtered
    frame["trade_gap"] = pd.to_numeric(frame["trade_gap"], errors="coerce")
    frame["abs_trade_gap"] = frame["trade_gap"].abs()
    ordered = frame.sort_values(["abs_trade_gap", "subject_name", "game_id"], ascending=[False, True, True])
    return [_clean_record(row) for row in ordered.head(12).drop(columns=["abs_trade_gap"]).to_dict(orient="records")]


def _build_dashboard_summary(
    candidate_rows: list[dict[str, Any]],
    lane_status_rows: list[dict[str, Any]],
    finalists: list[dict[str, Any]],
    replay_metadata: dict[str, Any],
    *,
    compare_ready_ranking: list[dict[str, Any]],
    live_ready_rows: list[dict[str, Any]],
    live_probe_rows: list[dict[str, Any]],
    shadow_only_rows: list[dict[str, Any]],
    bench_only_rows: list[dict[str, Any]],
    lane_rankings: list[dict[str, Any]],
    replay_compare_ready_rows: list[dict[str, Any]],
    replay_pending_rows: list[dict[str, Any]],
    daily_live_snapshot: dict[str, Any],
) -> dict[str, Any]:
    criteria_ready_rows = [row for row in candidate_rows if row.get("comparison_ready_flag")]
    execution_values = [
        float(row["execution_rate"]) for row in compare_ready_ranking if row.get("execution_rate") is not None
    ]
    realism_values = [
        float(row["realism_gap_trade_rate"])
        for row in compare_ready_ranking
        if row.get("realism_gap_trade_rate") is not None
    ]
    stale_values = [
        float(row["stale_signal_suppression_rate"])
        for row in compare_ready_ranking
        if row.get("stale_signal_suppression_rate") is not None
    ]
    live_observed_count = sum(
        1 for row in candidate_rows if bool((row.get("live_observed_result") or {}).get("live_observed_flag"))
    )
    daily_live_summary = daily_live_snapshot.get("summary") if isinstance(daily_live_snapshot.get("summary"), dict) else {}
    return {
        "published_lane_count": sum(
            1 for row in lane_status_rows if str(row.get("publication_state", "")).startswith("published")
        ),
        "criteria_ready_lane_count": sum(1 for row in lane_status_rows if row.get("criteria_ready_flag")),
        "compare_ready_lane_count": sum(1 for row in lane_status_rows if row.get("compare_ready_flag")),
        "published_candidate_count": len(candidate_rows),
        "criteria_ready_candidate_count": len(criteria_ready_rows),
        "compare_ready_candidate_count": len(compare_ready_ranking),
        "live_ready_candidate_count": len(live_ready_rows),
        "live_probe_candidate_count": len(live_probe_rows),
        "shadow_only_candidate_count": len(shadow_only_rows),
        "bench_only_candidate_count": len(bench_only_rows),
        "locked_baseline_count": sum(1 for row in candidate_rows if row.get("baseline_locked_flag")),
        "top_finalist_count": len(finalists),
        "finished_game_count": replay_metadata.get("finished_game_count"),
        "live_observed_candidate_count": live_observed_count,
        "daily_live_session_date": daily_live_snapshot.get("session_date"),
        "daily_live_status": daily_live_snapshot.get("status"),
        "daily_live_cycle_count": ((daily_live_summary.get("current_live_truth") or {}).get("cycle_count")),
        "lane_ranking_count": len(lane_rankings),
        "replay_compare_ready_challenger_count": len(replay_compare_ready_rows),
        "replay_pending_candidate_count": len(replay_pending_rows),
        "mean_execution_rate": sum(execution_values) / len(execution_values) if execution_values else None,
        "mean_realism_gap_trade_rate": sum(realism_values) / len(realism_values) if realism_values else None,
        "mean_stale_signal_suppression_rate": sum(stale_values) / len(stale_values) if stale_values else None,
    }


def build_submission_example_payloads(shared_root: str | None = None) -> dict[str, dict[str, Any]]:
    resolved_shared_root = _resolve_shared_root(shared_root)
    replay_contract_path = resolved_shared_root / "benchmark_contract" / "replay_contract_current.md"
    unified_contract_path = resolved_shared_root / "benchmark_contract" / "unified_benchmark_contract_current.md"
    base_scope = {
        "season": "2025-26",
        "phase_group": "play_in,playoffs",
        "replay_contract_ref": str(replay_contract_path),
        "benchmark_contract_ref": str(unified_contract_path),
    }
    examples = {
        "ml-trading": {
            "schema_version": SUBMISSION_EXAMPLE_VERSION,
            "lane_id": "ml-trading",
            "lane_label": "ML trading",
            "lane_type": "ml",
            "published_at": "2026-04-24T21:30:00+00:00",
            "comparison_scope": base_scope,
            "subjects": [
                {
                    "candidate_id": "ml_ranker_v1",
                    "display_name": "ml_ranker_v1",
                    "candidate_kind": "ml_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "result_views": {
                        "standard_backtest": {
                            "trade_count": 8,
                            "ending_bankroll": 13.18,
                            "avg_return_with_slippage": 0.121,
                            "compounded_return": 0.318,
                            "max_drawdown_pct": 0.21,
                            "max_drawdown_amount": 2.1,
                        },
                        "replay_result": {
                            "trade_count": 3,
                            "ending_bankroll": 12.44,
                            "avg_return_with_slippage": 0.149,
                            "compounded_return": 0.244,
                            "max_drawdown_pct": 0.18,
                            "max_drawdown_amount": 1.8,
                            "no_trade_count": 5,
                            "execution_rate": 0.375,
                        },
                        "live_observed": {
                            "live_observed_flag": False,
                        },
                    },
                    "replay_realism": {
                        "trade_gap": -5,
                        "execution_rate": 0.375,
                        "realism_gap_trade_rate": 0.625,
                        "blocked_signal_count": 5,
                        "stale_signal_suppressed_count": 4,
                        "stale_signal_suppression_rate": 0.5,
                        "stale_signal_share_of_blocked_signals": 0.8,
                        "top_no_trade_reason": "signal_stale",
                    },
                    "trace_artifacts": {
                        "decision_trace_json": "ml_ranker_v1_decisions.json",
                        "attempt_trace_csv": "ml_ranker_v1_attempt_trace.csv",
                    },
                    "artifacts": {
                        "report_markdown": "ml_ranker_v1_report.md",
                    },
                    "notes": [
                        "Use explicit live_observed_flag=false when the lane was not run live.",
                    ],
                }
            ],
        },
        "llm-strategy": {
            "schema_version": SUBMISSION_EXAMPLE_VERSION,
            "lane_id": "llm-strategy",
            "lane_label": "LLM strategy",
            "lane_type": "llm",
            "published_at": "2026-04-24T21:30:00+00:00",
            "comparison_scope": base_scope,
            "subjects": [
                {
                    "candidate_id": "llm_selector_v1",
                    "display_name": "llm_selector_v1",
                    "candidate_kind": "llm_strategy",
                    "subject_type": "candidate",
                    "publication_state": "published",
                    "result_views": {
                        "standard_backtest": {
                            "trade_count": 7,
                            "ending_bankroll": 12.91,
                            "avg_return_with_slippage": 0.109,
                            "compounded_return": 0.291,
                            "max_drawdown_pct": 0.24,
                            "max_drawdown_amount": 2.4,
                        },
                        "replay_result": {
                            "trade_count": 2,
                            "ending_bankroll": 11.82,
                            "avg_return_with_slippage": 0.138,
                            "compounded_return": 0.182,
                            "max_drawdown_pct": 0.17,
                            "max_drawdown_amount": 1.7,
                            "no_trade_count": 5,
                            "execution_rate": 0.2857142857,
                        },
                        "live_observed": {
                            "trade_count": 1,
                            "live_observed_flag": True,
                            "live_run_count": 1,
                            "entry_submitted_count": 1,
                            "position_opened_count": 1,
                            "live_vs_backtest_gap_trade_rate": 0.8571428571,
                        },
                    },
                    "replay_realism": {
                        "trade_gap": -5,
                        "execution_rate": 0.2857142857,
                        "realism_gap_trade_rate": 0.7142857143,
                        "blocked_signal_count": 5,
                        "stale_signal_suppressed_count": 3,
                        "stale_signal_suppression_rate": 0.4285714286,
                        "stale_signal_share_of_blocked_signals": 0.6,
                        "top_no_trade_reason": "signal_stale",
                    },
                    "trace_artifacts": {
                        "decision_trace_json": "llm_selector_v1_decisions.json",
                        "attempt_trace_csv": "llm_selector_v1_attempt_trace.csv",
                    },
                    "artifacts": {
                        "report_markdown": "llm_selector_v1_report.md",
                        "runtime_note_markdown": "llm_selector_v1_runtime_notes.md",
                    },
                    "notes": [
                        "LLM submissions should include runtime or cost notes when model calls remain in path.",
                    ],
                }
            ],
        },
    }
    return examples


def build_unified_benchmark_dashboard(request: UnifiedBenchmarkRequest) -> dict[str, Any]:
    shared_root = _resolve_shared_root(request.shared_root)
    replay_root = _resolve_replay_root(request)
    manifest_records = _manifest_submission_records(shared_root)
    lane_publications = _discover_lane_publications(shared_root, manifest_records=manifest_records)
    daily_live_snapshot = _load_daily_live_validation(shared_root)
    replay_contract_markdown = _read_markdown(shared_root / "benchmark_contract" / "replay_contract_current.md")
    replay_run_payload = _read_json(replay_root / "replay_run.json")
    if not isinstance(replay_run_payload, dict):
        replay_run_payload = {}
    run_metadata_payload = _read_json(shared_root / "reports" / "replay-engine-hf" / "run_metadata.json")
    if not isinstance(run_metadata_payload, dict):
        run_metadata_payload = {}

    candidate_rows = _merge_candidate_row_sets(
        _build_replay_subject_rows(
            request,
            replay_root=replay_root,
            replay_run_payload=replay_run_payload,
            run_metadata_payload=run_metadata_payload,
        )
        + _load_manifest_submission_rows(shared_root, manifest_records=manifest_records)
    )
    candidate_rows = [
        _finalize_candidate_row(row)
        for row in candidate_rows
    ]
    candidate_rows = _apply_operational_posture(candidate_rows, daily_live_snapshot=daily_live_snapshot)
    candidate_rows = [
        _clean_record(row)
        for row in sorted(
            candidate_rows,
            key=lambda row: (
                row.get("dashboard_bucket") != "baseline_controllers",
                row.get("promotion_bucket") != PROMOTION_BUCKET_LIVE_READY,
                row.get("promotion_bucket") == PROMOTION_BUCKET_BENCH_ONLY,
                row.get("lane_id"),
                -_numeric_sort(row.get("replay_ending_bankroll")),
                -_numeric_sort(row.get("execution_rate")),
                str(row.get("candidate_id")),
            ),
        )
    ]

    compare_ready_ranking = _build_global_compare_ready_ranking(candidate_rows)
    live_ready_rows = _build_promotion_bucket_rows(candidate_rows, PROMOTION_BUCKET_LIVE_READY)
    live_probe_rows = _build_promotion_bucket_rows(candidate_rows, PROMOTION_BUCKET_LIVE_PROBE)
    shadow_only_rows = _build_promotion_bucket_rows(candidate_rows, PROMOTION_BUCKET_SHADOW_ONLY)
    bench_only_rows = _build_promotion_bucket_rows(candidate_rows, PROMOTION_BUCKET_BENCH_ONLY)
    replay_compare_ready_rows, replay_pending_rows = _rank_replay_hf_candidates(candidate_rows)
    replay_shadow_rows = [
        row for row in shadow_only_rows if row.get("lane_id") == "replay-engine-hf"
    ]
    replay_bench_rows = [
        row for row in bench_only_rows if row.get("lane_id") == "replay-engine-hf"
    ]
    replay_live_probe_rows = [
        row for row in live_probe_rows if row.get("lane_id") == "replay-engine-hf"
    ]
    lane_rankings, compare_ready_lane_rankings = _build_lane_rankings(candidate_rows)
    lane_status_rows = _build_lane_status_rows(
        shared_root,
        candidate_rows=candidate_rows,
        replay_root=replay_root,
        replay_contract_markdown=replay_contract_markdown,
        run_metadata_payload=run_metadata_payload,
        lane_publications=lane_publications,
    )
    finalists = _build_finalists(compare_ready_ranking, finalist_limit=request.finalist_limit)
    replay_metadata = _build_replay_metadata(
        request,
        replay_root=replay_root,
        replay_run_payload=replay_run_payload,
        replay_contract_markdown=replay_contract_markdown,
        shared_root=shared_root,
        run_metadata_payload=run_metadata_payload,
    )
    compare_ready_criteria = build_compare_ready_criteria()
    result_modes = build_result_modes()
    submission_examples = build_submission_example_payloads(str(shared_root))
    merge_recommendation = _build_merge_recommendation(
        candidate_rows,
        compare_ready_lane_rankings,
        daily_live_snapshot=daily_live_snapshot,
    )
    current_promoted_stack = _build_current_promoted_stack(
        live_ready_rows=live_ready_rows,
        live_probe_rows=live_probe_rows,
        shadow_rows=shadow_only_rows,
        bench_rows=bench_only_rows,
        daily_live_snapshot=daily_live_snapshot,
    )

    payload = {
        "generated_at": _now_iso(),
        "schema_version": UNIFIED_BENCHMARK_SCHEMA_VERSION,
        "season": request.season,
        "shared_root": str(shared_root),
        "replay_contract": replay_metadata,
        "daily_live_validation": daily_live_snapshot,
        "result_modes": result_modes,
        "summary": _build_dashboard_summary(
            candidate_rows,
            lane_status_rows,
            finalists,
            replay_metadata,
            compare_ready_ranking=compare_ready_ranking,
            live_ready_rows=live_ready_rows,
            live_probe_rows=live_probe_rows,
            shadow_only_rows=shadow_only_rows,
            bench_only_rows=bench_only_rows,
            lane_rankings=lane_rankings,
            replay_compare_ready_rows=replay_compare_ready_rows,
            replay_pending_rows=replay_pending_rows,
            daily_live_snapshot=daily_live_snapshot,
        ),
        "lane_statuses": lane_status_rows,
        "lane_rankings": lane_rankings,
        "compare_ready_lane_rankings": compare_ready_lane_rankings,
        "candidates": candidate_rows,
        "compare_ready_ranking": compare_ready_ranking,
        "live_ready_ranking": live_ready_rows,
        "live_probe_ranking": live_probe_rows,
        "promotion_shadow_only_ranking": shadow_only_rows,
        "promotion_bench_only_ranking": bench_only_rows,
        "baseline_controllers": [
            row for row in candidate_rows if row.get("dashboard_bucket") == "baseline_controllers"
        ],
        "deterministic_hf_candidates": [
            row for row in candidate_rows if row.get("dashboard_bucket") == "deterministic_hf_candidates"
        ],
        "deterministic_hf_compare_ready": replay_compare_ready_rows,
        "deterministic_hf_live_probe": replay_live_probe_rows,
        "deterministic_hf_shadow_only": replay_shadow_rows,
        "deterministic_hf_bench_only": replay_bench_rows,
        "deterministic_hf_pending": replay_pending_rows,
        "ml_candidates": [row for row in candidate_rows if row.get("dashboard_bucket") == "ml_candidates"],
        "llm_candidates": [row for row in candidate_rows if row.get("dashboard_bucket") == "llm_candidates"],
        "finalists": finalists,
        "current_promoted_stack": current_promoted_stack,
        "shadow_only_candidates": shadow_only_rows,
        "bench_only_candidates": bench_only_rows,
        "divergence_summary": _build_divergence_rows(replay_root),
        "game_gap_summary": _build_game_gap_rows(replay_root, finalists),
        "compare_ready_criteria": compare_ready_criteria,
        "merge_recommendation": merge_recommendation,
        "submission_examples": {
            "schema_version": SUBMISSION_EXAMPLE_VERSION,
            "example_file_paths": {
                "ml-trading": str(
                    shared_root / "reports" / "benchmark-integration" / "ml_benchmark_submission_example.json"
                ),
                "llm-strategy": str(
                    shared_root / "reports" / "benchmark-integration" / "llm_benchmark_submission_example.json"
                ),
            },
            "examples": submission_examples,
        },
    }
    return to_jsonable(payload)


def render_compare_ready_criteria_markdown(criteria: dict[str, Any]) -> str:
    lines = [
        "# Compare-Ready Criteria",
        "",
        f"- version: `{criteria.get('version')}`",
        "",
        "## Lane Requirements",
        "",
    ]
    for row in criteria.get("lane_requirements") or []:
        lines.append(f"- `{row.get('id')}`: {row.get('description')}")
    lines.extend(["", "## Candidate Requirements", ""])
    for row in criteria.get("candidate_requirements") or []:
        lines.append(f"- `{row.get('id')}`: {row.get('description')}")
    lines.extend(["", "## Finalist Rule", ""])
    for row in criteria.get("finalist_rule") or []:
        lines.append(f"- {row}")
    lines.extend(["", "## Accepted Trace Artifact Keys", ""])
    for key in criteria.get("trace_artifact_keys") or []:
        lines.append(f"- `{key}`")
    return "\n".join(lines).strip() + "\n"


def render_current_promoted_stack_markdown(snapshot: dict[str, Any]) -> str:
    promoted_stack = snapshot.get("current_promoted_stack") or {}
    lines = [
        "# Current Promoted Stack",
        "",
        f"- session date: `{promoted_stack.get('session_date')}`",
        f"- live status: `{promoted_stack.get('live_status')}`",
        f"- primary controller: `{promoted_stack.get('control_primary')}`",
        f"- fallback controller: `{promoted_stack.get('control_fallback')}`",
        "",
        "## Operator Note",
        "",
        f"- {promoted_stack.get('operator_note')}",
        "",
        "## Buckets",
        "",
    ]
    for label, rows in (
        ("live-ready", promoted_stack.get("live_ready") or []),
        ("live-probe", promoted_stack.get("live_probe") or []),
        ("shadow-only", promoted_stack.get("shadow_only") or []),
        ("bench-only", promoted_stack.get("bench_only") or []),
    ):
        lines.append(f"- {label}: " + (", ".join(str(row.get("candidate_id")) for row in rows) or "none"))
    return "\n".join(lines).strip() + "\n"


def render_milestone_merge_plan_markdown(snapshot: dict[str, Any]) -> str:
    merge_recommendation = snapshot.get("merge_recommendation") or {}
    promoted_stack = snapshot.get("current_promoted_stack") or {}
    lines = [
        "# Milestone Merge Plan",
        "",
        "## Snapshot",
        "",
        f"- generated at: `{snapshot.get('generated_at')}`",
        f"- season: `{snapshot.get('season')}`",
        f"- daily live session: `{(snapshot.get('summary') or {}).get('daily_live_session_date')}`",
        "",
        "## Current Promoted Stack",
        "",
        f"- {promoted_stack.get('operator_note')}",
        "",
        "## Merge Order",
        "",
    ]
    for row in merge_recommendation.get("merge_order") or []:
        lines.append(
            f"- `#{row.get('priority')}` `{row.get('lane_id')}` | status `{row.get('status')}` | {row.get('reason')}"
        )
    lines.extend(["", "## Merge Now", ""])
    for row in merge_recommendation.get("merge_now") or []:
        lines.append(f"- `{row.get('lane_id')}`: {row.get('rationale')}")
    lines.extend(["", "## Wait", ""])
    for row in merge_recommendation.get("wait") or []:
        lines.append(f"- `{row.get('lane_id')}`: {row.get('rationale')}")
    return "\n".join(lines).strip() + "\n"


def render_benchmark_integration_status_markdown(
    snapshot: dict[str, Any],
    *,
    repo_root: Path,
    shared_root: Path,
) -> str:
    summary = snapshot.get("summary") or {}
    promoted_stack = snapshot.get("current_promoted_stack") or {}
    lines = [
        "# Benchmark Integration Status",
        "",
        "## Snapshot",
        "",
        f"- timestamp: `{snapshot.get('generated_at')}`",
        "- lane: `benchmark-integration`",
        f"- repo: `{repo_root}`",
        "- active branch: `codex/benchmark-integration`",
        f"- shared root: `{shared_root}`",
        "",
        "## Current Read",
        "",
        f"- compare-ready lanes: `{summary.get('compare_ready_lane_count')}`",
        f"- compare-ready candidates: `{summary.get('compare_ready_candidate_count')}`",
        f"- live-ready candidates: `{summary.get('live_ready_candidate_count')}`",
        f"- live-probe candidates: `{summary.get('live_probe_candidate_count')}`",
        f"- shadow-only candidates: `{summary.get('shadow_only_candidate_count')}`",
        f"- bench-only candidates: `{summary.get('bench_only_candidate_count')}`",
        f"- daily live session: `{summary.get('daily_live_session_date')}` / `{summary.get('daily_live_status')}`",
        "",
        "## Operator Stack",
        "",
        f"- {promoted_stack.get('operator_note')}",
        "",
        "## Current Merge Order",
        "",
    ]
    for row in (snapshot.get("merge_recommendation") or {}).get("merge_order") or []:
        lines.append(
            f"- `#{row.get('priority')}` `{row.get('lane_id')}` | status `{row.get('status')}` | {row.get('reason')}"
        )
    return "\n".join(lines).strip() + "\n"


def render_unified_benchmark_markdown(snapshot: dict[str, Any]) -> str:
    replay_contract = snapshot.get("replay_contract") or {}
    daily_live = snapshot.get("daily_live_validation") or {}
    result_modes = list(snapshot.get("result_modes") or [])
    summary = snapshot.get("summary") or {}
    lane_statuses = list(snapshot.get("lane_statuses") or [])
    lane_rankings = list(snapshot.get("lane_rankings") or [])
    compare_ready_lane_rankings = list(snapshot.get("compare_ready_lane_rankings") or [])
    compare_ready_ranking = list(snapshot.get("compare_ready_ranking") or [])
    baselines = list(snapshot.get("baseline_controllers") or [])
    replay_challengers = list(snapshot.get("deterministic_hf_compare_ready") or [])
    replay_live_probe = list(snapshot.get("deterministic_hf_live_probe") or [])
    replay_shadow = list(snapshot.get("deterministic_hf_shadow_only") or [])
    replay_bench = list(snapshot.get("deterministic_hf_bench_only") or [])
    ml_candidates = list(snapshot.get("ml_candidates") or [])
    llm_candidates = list(snapshot.get("llm_candidates") or [])
    finalists = list(snapshot.get("finalists") or [])
    live_ready_rows = list(snapshot.get("live_ready_ranking") or [])
    live_probe_rows = list(snapshot.get("live_probe_ranking") or [])
    shadow_only_rows = list(snapshot.get("promotion_shadow_only_ranking") or snapshot.get("shadow_only_candidates") or [])
    bench_only_rows = list(snapshot.get("promotion_bench_only_ranking") or snapshot.get("bench_only_candidates") or [])
    divergence_rows = list(snapshot.get("divergence_summary") or [])
    criteria = snapshot.get("compare_ready_criteria") or {}
    merge_recommendation = snapshot.get("merge_recommendation") or {}
    promoted_stack = snapshot.get("current_promoted_stack") or {}
    examples = (snapshot.get("submission_examples") or {}).get("example_file_paths") or {}

    def _live_markdown_value(row: dict[str, Any]) -> Any:
        live_result = row.get("live_observed_result") or {}
        return row.get("live_trade_count") if live_result.get("live_observed_flag") else "not observed"

    lines = [
        "# Unified Benchmark Dashboard",
        "",
        f"- generated at: `{snapshot.get('generated_at')}`",
        f"- schema version: `{snapshot.get('schema_version')}`",
        f"- season: `{snapshot.get('season')}`",
        f"- replay contract maturity: `{replay_contract.get('replay_contract_maturity') or 'unknown'}`",
        f"- finished postseason games: `{replay_contract.get('finished_game_count')}`",
        f"- compare-ready lanes: `{summary.get('compare_ready_lane_count')}` / `{summary.get('published_lane_count')}`",
        f"- compare-ready candidates: `{summary.get('compare_ready_candidate_count')}` / `{summary.get('published_candidate_count')}`",
        f"- live-ready candidates: `{summary.get('live_ready_candidate_count')}`",
        f"- live-probe candidates: `{summary.get('live_probe_candidate_count')}`",
        f"- shadow-only candidates: `{summary.get('shadow_only_candidate_count')}`",
        f"- bench-only candidates: `{summary.get('bench_only_candidate_count')}`",
        f"- daily live session: `{summary.get('daily_live_session_date')}` | status `{summary.get('daily_live_status')}` | cycles `{summary.get('daily_live_cycle_count')}`",
        "",
        "## Result Modes",
        "",
    ]
    for row in result_modes:
        lines.append(f"- `{row.get('label')}` | `{row.get('headline')}` | {row.get('description')}")

    lines.extend(["", "## Daily Live Validation", ""])
    if daily_live:
        control = ((daily_live.get("summary") or {}).get("control") or {})
        harness = ((daily_live.get("summary") or {}).get("harness_capabilities") or {})
        lines.extend(
            [
                f"- session date: `{daily_live.get('session_date')}`",
                f"- status: `{daily_live.get('status')}`",
                f"- primary controller: `{control.get('primary_controller')}`",
                f"- fallback controller: `{control.get('fallback_controller')}`",
                f"- standalone probe routing supported: `{harness.get('supports_standalone_probe_candidates')}`",
                f"- ML sidecar live routing supported: `{harness.get('supports_ml_sidecar_live_routing')}`",
                f"- LLM sidecar live routing supported: `{harness.get('supports_llm_sidecar_live_routing')}`",
            ]
        )

    lines.extend(["", "## Lane Status", ""])
    for row in lane_statuses:
        lines.append(
            f"- `{row.get('lane_id')}` `{row.get('publication_state')}` | published `{row.get('published_subject_count')}` | "
            f"compare-ready `{row.get('compare_ready_subject_count')}` | live-ready `{row.get('live_ready_subject_count')}` | "
            f"live-probe `{row.get('live_probe_subject_count')}` | shadow `{row.get('shadow_only_subject_count')}` | "
            f"bench `{row.get('bench_only_subject_count')}` | bucket `{row.get('lane_bucket')}`"
        )

    lines.extend(["", "## Lane Ranking", ""])
    for row in compare_ready_lane_rankings:
        lines.append(
            f"- `#{row.get('lane_rank')}` `{row.get('lane_label')}` | top `{row.get('top_candidate_name')}` | "
            f"replay bankroll `{row.get('top_candidate_replay_ending_bankroll')}` | promotion `{row.get('top_candidate_promotion_bucket')}`"
        )
    for row in lane_rankings:
        if row.get("compare_ready_subject_count"):
            continue
        lines.append(
            f"- `{row.get('lane_label')}` | top `{row.get('top_candidate_name')}` | "
            f"replay bankroll `{row.get('top_candidate_replay_ending_bankroll')}` | bucket `{row.get('lane_bucket')}`"
        )

    lines.extend(["", "## Compare-Ready Ranking", ""])
    for row in compare_ready_ranking:
        lines.append(
            f"- `#{row.get('global_rank')}` `{row.get('display_name')}` | lane `{row.get('lane_label')}` | "
            f"promotion `{row.get('promotion_bucket')}` | standard `{row.get('standard_trade_count')}` | replay `{row.get('replay_trade_count')}` | "
            f"live `{_live_markdown_value(row)}` | bankroll `{row.get('replay_ending_bankroll')}` | gap `{row.get('realism_gap_trade_rate')}`"
        )

    lines.extend(["", "## Current Promoted Stack", ""])
    lines.append(f"- operator note: {promoted_stack.get('operator_note')}")
    for label, rows in (
        ("live-ready", live_ready_rows),
        ("live-probe", live_probe_rows),
        ("shadow-only", shadow_only_rows),
        ("bench-only", bench_only_rows[:8]),
    ):
        if not rows:
            continue
        lines.append(f"- {label}: " + ", ".join(str(row.get("candidate_id")) for row in rows))

    lines.extend(["", "## Baseline Controllers", ""])
    for row in baselines:
        lines.append(
            f"- `{row.get('display_name')}` | promotion `{row.get('promotion_bucket')}` | standard `{row.get('standard_trade_count')}` | "
            f"replay `{row.get('replay_trade_count')}` | live `{_live_markdown_value(row)}` | "
            f"stale suppressed `{row.get('stale_signal_suppressed_count')}` ({row.get('stale_signal_suppression_rate')})"
        )

    lines.extend(["", "## Replay Compare-Ready Families", ""])
    for row in replay_challengers:
        lines.append(
            f"- `#{row.get('challenger_rank')}` `{row.get('display_name')}` | promotion `{row.get('promotion_bucket')}` | "
            f"standard `{row.get('standard_trade_count')}` | replay `{row.get('replay_trade_count')}` | bankroll `{row.get('replay_ending_bankroll')}`"
        )

    lines.extend(["", "## Replay Live-Probe Tier", ""])
    for row in replay_live_probe:
        lines.append(
            f"- `{row.get('display_name')}` | today `{row.get('today_execution_mode')}` | reason `{row.get('promotion_bucket_reason')}`"
        )

    lines.extend(["", "## Replay Shadow Tier", ""])
    for row in replay_shadow:
        lines.append(
            f"- `{row.get('display_name')}` | replay bankroll `{row.get('replay_ending_bankroll')}` | today `{row.get('today_execution_mode')}` | reason `{row.get('promotion_bucket_reason')}`"
        )

    lines.extend(["", "## Replay Bench Tier", ""])
    for row in replay_bench:
        missing = ", ".join((row.get("compare_ready_checks") or {}).get("missing_requirements") or []) or "none"
        lines.append(
            f"- `{row.get('display_name')}` | standard `{row.get('standard_trade_count')}` | replay `{row.get('replay_trade_count')}` | bankroll `{row.get('replay_ending_bankroll')}` | missing `{missing}`"
        )

    lines.extend(["", "## ML Candidates", ""])
    for row in ml_candidates:
        lines.append(
            f"- `{row.get('display_name')}` | promotion `{row.get('promotion_bucket')}` | standard `{row.get('standard_trade_count')}` | replay `{row.get('replay_trade_count')}` | bankroll `{row.get('replay_ending_bankroll')}` | reason `{row.get('promotion_bucket_reason')}`"
        )

    lines.extend(["", "## LLM Candidates", ""])
    for row in llm_candidates:
        lines.append(
            f"- `{row.get('display_name')}` | promotion `{row.get('promotion_bucket')}` | standard `{row.get('standard_trade_count')}` | replay `{row.get('replay_trade_count')}` | bankroll `{row.get('replay_ending_bankroll')}` | reason `{row.get('promotion_bucket_reason')}`"
        )

    lines.extend(["", "## Compare-Ready Finalists", ""])
    for row in finalists:
        lines.append(
            f"- `#{row.get('finalist_rank')}` `{row.get('display_name')}` | promotion `{row.get('promotion_bucket')}` | replay bankroll `{row.get('replay_ending_bankroll')}` | reason `{row.get('finalist_reason')}`"
        )

    lines.extend(["", "## Top Divergence Causes", ""])
    for row in divergence_rows[:8]:
        lines.append(f"- `{row.get('subject_name')}` -> `{row.get('no_trade_reason')}` on `{row.get('signal_count')}` signals")

    lines.extend(["", "## Merge Recommendation", ""])
    for row in merge_recommendation.get("merge_order") or []:
        lines.append(
            f"- `#{row.get('priority')}` `{row.get('lane_id')}` | status `{row.get('status')}` | {row.get('reason')}"
        )
    for row in merge_recommendation.get("merge_now") or []:
        lines.append(f"- merge now `{row.get('lane_id')}`: {row.get('rationale')}")
    for row in merge_recommendation.get("wait") or []:
        lines.append(f"- wait `{row.get('lane_id')}`: {row.get('rationale')}")

    lines.extend(
        [
            "",
            "## Compare-Ready Criteria",
            "",
            f"- criteria version: `{criteria.get('version')}`",
            f"- candidate requirements: `{len(criteria.get('candidate_requirements') or [])}`",
            "",
            "## Submission Example Files",
            "",
            f"- ML example: `{examples.get('ml-trading')}`",
            f"- LLM example: `{examples.get('llm-strategy')}`",
        ]
    )

    return "\n".join(lines).strip() + "\n"


def export_unified_benchmark_snapshot(
    request: UnifiedBenchmarkRequest,
    *,
    output_json_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    snapshot = build_unified_benchmark_dashboard(request)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    output_markdown_path.write_text(render_unified_benchmark_markdown(snapshot), encoding="utf-8")
    return snapshot


def export_benchmark_integration_bundle(
    request: UnifiedBenchmarkRequest,
    *,
    artifact_root: Path,
    report_root: Path,
) -> dict[str, Any]:
    snapshot = export_unified_benchmark_snapshot(
        request,
        output_json_path=artifact_root / "unified_benchmark_dashboard.json",
        output_markdown_path=report_root / "unified_benchmark_dashboard.md",
    )
    criteria = build_compare_ready_criteria()
    examples = build_submission_example_payloads(request.shared_root)

    criteria_json_path = artifact_root / "compare_ready_criteria.json"
    criteria_markdown_path = report_root / "compare_ready_criteria.md"
    criteria_json_path.parent.mkdir(parents=True, exist_ok=True)
    criteria_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    criteria_json_path.write_text(json.dumps(criteria, indent=2, sort_keys=True), encoding="utf-8")
    criteria_markdown_path.write_text(render_compare_ready_criteria_markdown(criteria), encoding="utf-8")

    example_paths = {
        "ml-trading": report_root / "ml_benchmark_submission_example.json",
        "llm-strategy": report_root / "llm_benchmark_submission_example.json",
    }
    for lane_id, path in example_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(examples[lane_id], indent=2, sort_keys=True), encoding="utf-8")

    promoted_stack_path = report_root / "current_promoted_stack.md"
    promoted_stack_path.write_text(render_current_promoted_stack_markdown(snapshot), encoding="utf-8")

    milestone_merge_plan_path = report_root / "milestone_merge_plan.md"
    milestone_merge_plan_path.write_text(render_milestone_merge_plan_markdown(snapshot), encoding="utf-8")

    shared_root = _resolve_shared_root(request.shared_root)
    handoff_root = shared_root / "handoffs" / "benchmark-integration"
    handoff_root.mkdir(parents=True, exist_ok=True)
    status_path = handoff_root / "status.md"
    status_path.write_text(
        render_benchmark_integration_status_markdown(
            snapshot,
            repo_root=Path(__file__).resolve().parents[6],
            shared_root=shared_root.parent if shared_root.name == "shared" else shared_root,
        ),
        encoding="utf-8",
    )

    return {
        "snapshot": snapshot,
        "compare_ready_criteria_path": str(criteria_markdown_path),
        "compare_ready_criteria_json_path": str(criteria_json_path),
        "submission_example_paths": {lane_id: str(path) for lane_id, path in example_paths.items()},
        "current_promoted_stack_path": str(promoted_stack_path),
        "milestone_merge_plan_path": str(milestone_merge_plan_path),
        "status_path": str(status_path),
    }


__all__ = [
    "COMPARE_READY_CRITERIA_VERSION",
    "DEFAULT_FINALIST_LIMIT",
    "DEFAULT_REPLAY_ARTIFACT_NAME",
    "SUBMISSION_EXAMPLE_VERSION",
    "UNIFIED_BENCHMARK_SCHEMA_VERSION",
    "UnifiedBenchmarkRequest",
    "build_compare_ready_criteria",
    "build_result_modes",
    "build_submission_example_payloads",
    "build_unified_benchmark_dashboard",
    "export_benchmark_integration_bundle",
    "export_unified_benchmark_snapshot",
    "render_benchmark_integration_status_markdown",
    "render_compare_ready_criteria_markdown",
    "render_current_promoted_stack_markdown",
    "render_milestone_merge_plan_markdown",
    "render_unified_benchmark_markdown",
    "resolve_default_shared_root",
]
