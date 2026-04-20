from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisConsumerRequest, DEFAULT_OUTPUT_ROOT
from app.data.pipelines.daily.nba.analysis.reports import REPORT_SECTION_SPECS


_VERSION_TOKEN_PATTERN = re.compile(r"\d+")


@dataclass(slots=True)
class AnalysisConsumerBundle:
    season: str
    season_phase: str
    analysis_version: str
    output_dir: Path
    artifact_paths: dict[str, str]
    report_payload: dict[str, Any]
    backtest_payload: dict[str, Any]
    model_payload: dict[str, Any]


def _json_load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Required analysis artifact not found: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in analysis artifact: {path}")
    return payload


def _version_sort_key(version: str) -> tuple[int, ...]:
    tokens = _VERSION_TOKEN_PATTERN.findall(str(version))
    if not tokens:
        return (-1,)
    return tuple(int(token) for token in tokens)


def _resolve_output_base(output_root: str | None) -> Path:
    return Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT


def list_available_analysis_versions(
    *,
    season: str,
    season_phase: str,
    output_root: str | None = None,
) -> list[str]:
    phase_root = _resolve_output_base(output_root) / season / season_phase
    if not phase_root.exists():
        return []
    versions = [path.name for path in phase_root.iterdir() if path.is_dir()]
    return sorted(versions, key=lambda value: (_version_sort_key(value), value))


def resolve_analysis_consumer_paths(request: AnalysisConsumerRequest) -> dict[str, str]:
    available_versions = list_available_analysis_versions(
        season=request.season,
        season_phase=request.season_phase,
        output_root=request.output_root,
    )
    if request.analysis_version:
        analysis_version = request.analysis_version
    else:
        if not available_versions:
            raise FileNotFoundError(
                f"No analysis output versions found for {request.season} {request.season_phase}"
            )
        analysis_version = available_versions[-1]

    output_dir = _resolve_output_base(request.output_root) / request.season / request.season_phase / analysis_version
    return {
        "output_dir": str(output_dir),
        "report_json": str(output_dir / "analysis_report.json"),
        "backtest_json": str(output_dir / "backtests" / "run_analysis_backtests.json"),
        "model_json": str(output_dir / "models" / "train_analysis_baselines.json"),
    }


def _validate_payload_identity(
    payload_name: str,
    payload: dict[str, Any],
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
) -> None:
    if payload.get("season") and str(payload.get("season")) != str(season):
        raise ValueError(f"{payload_name} season mismatch: expected {season}, got {payload.get('season')}")
    if payload.get("season_phase") and str(payload.get("season_phase")) != str(season_phase):
        raise ValueError(
            f"{payload_name} season_phase mismatch: expected {season_phase}, got {payload.get('season_phase')}"
        )
    if payload.get("analysis_version") and str(payload.get("analysis_version")) != str(analysis_version):
        raise ValueError(
            f"{payload_name} analysis_version mismatch: expected {analysis_version}, got {payload.get('analysis_version')}"
        )


def _normalized_artifacts(
    *,
    output_dir: Path,
    report_payload: dict[str, Any],
    backtest_payload: dict[str, Any],
    model_payload: dict[str, Any],
    resolved_paths: dict[str, str],
) -> dict[str, Any]:
    report_artifacts = dict(report_payload.get("artifacts") or {})
    report_artifacts.setdefault("json", resolved_paths["report_json"])
    report_artifacts.setdefault("markdown", str(output_dir / "analysis_report.md"))

    backtest_artifacts = dict(backtest_payload.get("artifacts") or {})
    backtest_artifacts.setdefault("json", resolved_paths["backtest_json"])
    backtest_artifacts.setdefault("markdown", str(output_dir / "backtests" / "run_analysis_backtests.md"))

    model_artifacts = dict(model_payload.get("artifacts") or {})
    model_artifacts.setdefault("json", resolved_paths["model_json"])
    model_artifacts.setdefault("markdown", str(output_dir / "models" / "train_analysis_baselines.md"))

    return {
        "report": to_jsonable(report_artifacts),
        "backtests": to_jsonable(backtest_artifacts),
        "models": to_jsonable(model_artifacts),
    }


def load_analysis_consumer_bundle(request: AnalysisConsumerRequest) -> AnalysisConsumerBundle:
    resolved_paths = resolve_analysis_consumer_paths(request)
    output_dir = Path(resolved_paths["output_dir"])
    analysis_version = request.analysis_version or output_dir.name
    report_payload = _json_load(Path(resolved_paths["report_json"]))
    backtest_payload = _json_load(Path(resolved_paths["backtest_json"]))
    model_payload = _json_load(Path(resolved_paths["model_json"]))

    _validate_payload_identity(
        "analysis_report",
        report_payload,
        season=request.season,
        season_phase=request.season_phase,
        analysis_version=analysis_version,
    )
    _validate_payload_identity(
        "run_analysis_backtests",
        backtest_payload,
        season=request.season,
        season_phase=request.season_phase,
        analysis_version=analysis_version,
    )
    _validate_payload_identity(
        "train_analysis_baselines",
        model_payload,
        season=request.season,
        season_phase=request.season_phase,
        analysis_version=analysis_version,
    )

    resolved_experiment_id = ((backtest_payload.get("experiment") or {}).get("experiment_id"))
    if request.backtest_experiment_id and request.backtest_experiment_id != resolved_experiment_id:
        raise ValueError(
            f"Backtest experiment mismatch: expected {request.backtest_experiment_id}, got {resolved_experiment_id}"
        )

    artifact_paths = _normalized_artifacts(
        output_dir=output_dir,
        report_payload=report_payload,
        backtest_payload=backtest_payload,
        model_payload=model_payload,
        resolved_paths=resolved_paths,
    )
    return AnalysisConsumerBundle(
        season=request.season,
        season_phase=request.season_phase,
        analysis_version=analysis_version,
        output_dir=output_dir,
        artifact_paths=artifact_paths,
        report_payload=report_payload,
        backtest_payload=backtest_payload,
        model_payload=model_payload,
    )


def _build_report_sections(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in REPORT_SECTION_SPECS:
        section_rows = list(report_payload.get(spec["key"]) or [])
        rows.append(
            {
                "key": spec["key"],
                "title": spec["title"],
                "columns": list(spec["columns"]),
                "row_count": len(section_rows),
                "rows": section_rows,
            }
        )
    return rows


def _build_strategy_rankings(backtest_payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = backtest_payload.get("benchmark") or {}
    freeze_lookup = {
        str(row.get("strategy_family")): row
        for row in (benchmark.get("candidate_freeze") or [])
    }
    rows = [row for row in (benchmark.get("family_summary") or []) if row.get("sample_name") == "full_sample"]
    ranked = sorted(
        rows,
        key=lambda row: (
            row.get("avg_gross_return_with_slippage") is not None,
            float(row.get("avg_gross_return_with_slippage") or float("-inf")),
            int(row.get("trade_count") or 0),
        ),
        reverse=True,
    )
    results: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked, start=1):
        freeze_row = freeze_lookup.get(str(row.get("strategy_family"))) or {}
        merged = dict(row)
        merged["rank"] = rank
        merged["candidate_label"] = freeze_row.get("candidate_label")
        merged["label_reason"] = freeze_row.get("label_reason")
        results.append(merged)
    return results


def _build_model_tracks(model_payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for track_name, track_payload in sorted((model_payload.get("tracks") or {}).items()):
        summary = {
            "track_name": track_name,
            "status": track_payload.get("status"),
            "model_family": track_payload.get("model_family"),
            "train_rows": track_payload.get("train_rows"),
            "validation_rows": track_payload.get("validation_rows"),
            "metrics": track_payload.get("metrics") or {},
            "naive_comparison": track_payload.get("naive_comparison") or {},
            "targets": [],
        }
        for target_name, target_payload in sorted((track_payload.get("targets") or {}).items()):
            summary["targets"].append(
                {
                    "target_name": target_name,
                    "rmse": target_payload.get("rmse"),
                    "mae": target_payload.get("mae"),
                    "rank_corr": target_payload.get("rank_corr"),
                    "naive_comparison": target_payload.get("naive_comparison") or {},
                }
            )
        tracks.append(summary)
    return tracks


def build_analysis_consumer_snapshot(bundle: AnalysisConsumerBundle) -> dict[str, Any]:
    report_payload = bundle.report_payload
    backtest_payload = bundle.backtest_payload
    model_payload = bundle.model_payload
    benchmark = backtest_payload.get("benchmark") or {}

    snapshot = {
        "season": bundle.season,
        "season_phase": bundle.season_phase,
        "analysis_version": bundle.analysis_version,
        "output_dir": str(bundle.output_dir),
        "artifacts": bundle.artifact_paths,
        "report": {
            "universe": report_payload.get("universe") or {},
            "section_order": list(report_payload.get("section_order") or [spec["key"] for spec in REPORT_SECTION_SPECS]),
            "sections": _build_report_sections(report_payload),
        },
        "benchmark": {
            "contract_version": benchmark.get("contract_version"),
            "minimum_trade_count": benchmark.get("minimum_trade_count"),
            "experiment": backtest_payload.get("experiment") or {},
            "strategy_rankings": _build_strategy_rankings(backtest_payload),
            "candidate_freeze": list(benchmark.get("candidate_freeze") or []),
            "split_summary": list(benchmark.get("split_summary") or []),
            "comparators": list(benchmark.get("comparators") or []),
            "comparator_summary": list(benchmark.get("comparator_summary") or []),
            "context_rankings": list(benchmark.get("context_rankings") or []),
        },
        "models": {
            "feature_set_version": model_payload.get("feature_set_version"),
            "train_cutoff": model_payload.get("train_cutoff"),
            "validation_window": model_payload.get("validation_window"),
            "tracks": _build_model_tracks(model_payload),
        },
    }
    return to_jsonable(snapshot)


def load_analysis_consumer_snapshot(request: AnalysisConsumerRequest) -> dict[str, Any]:
    return build_analysis_consumer_snapshot(load_analysis_consumer_bundle(request))


__all__ = [
    "AnalysisConsumerBundle",
    "build_analysis_consumer_snapshot",
    "list_available_analysis_versions",
    "load_analysis_consumer_bundle",
    "load_analysis_consumer_snapshot",
    "resolve_analysis_consumer_paths",
]
