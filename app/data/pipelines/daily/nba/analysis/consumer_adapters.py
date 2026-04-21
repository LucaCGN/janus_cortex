from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisConsumerRequest, DEFAULT_OUTPUT_ROOT
from app.data.pipelines.daily.nba.analysis.reports import REPORT_SECTION_SPECS


_VERSION_TOKEN_PATTERN = re.compile(r"\d+")
_FAMILY_ARTIFACT_KEY_MAP = {
    "trades_csv": "{family}_csv",
    "trades_parquet": "{family}_parquet",
    "best_trades_csv": "{family}_best_trades_csv",
    "best_trades_parquet": "{family}_best_trades_parquet",
    "worst_trades_csv": "{family}_worst_trades_csv",
    "worst_trades_parquet": "{family}_worst_trades_parquet",
    "context_summary_csv": "{family}_context_summary_csv",
    "context_summary_parquet": "{family}_context_summary_parquet",
    "trade_traces_json": "{family}_trade_traces_json",
}


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
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
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


def _read_table_artifact(
    *,
    artifacts: dict[str, Any],
    csv_key: str,
    parquet_key: str,
) -> pd.DataFrame:
    parquet_path = artifacts.get(parquet_key)
    if parquet_path:
        path = Path(str(parquet_path))
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception:
                pass

    csv_path = artifacts.get(csv_key)
    if csv_path:
        path = Path(str(csv_path))
        if path.exists():
            return pd.read_csv(path)
    return pd.DataFrame()


def _read_json_list(path_value: Any) -> list[dict[str, Any]]:
    if not path_value:
        return []
    path = Path(str(path_value))
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _bounded_records(frame: pd.DataFrame, *, limit: int | None = None) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    records = frame.to_dict(orient="records")
    if limit is not None:
        records = records[:limit]
    return list(to_jsonable(records))


def _family_rows(payload_rows: list[dict[str, Any]], *, strategy_family: str) -> list[dict[str, Any]]:
    token = str(strategy_family)
    return [row for row in payload_rows if str(row.get("strategy_family")) == token]


def _family_artifact_paths(backtest_artifacts: dict[str, Any], *, strategy_family: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for public_key, template in _FAMILY_ARTIFACT_KEY_MAP.items():
        artifact_key = template.format(family=strategy_family)
        artifact_path = backtest_artifacts.get(artifact_key)
        if artifact_path:
            result[public_key] = str(artifact_path)
    return result


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


def _portfolio_rows_for_sample(backtest_payload: dict[str, Any], *, sample_name: str) -> list[dict[str, Any]]:
    benchmark = backtest_payload.get("benchmark") or {}
    return [
        row
        for row in (benchmark.get("portfolio_summary") or [])
        if str(row.get("sample_name")) == str(sample_name)
    ]


def _build_individual_strategy_rankings(backtest_payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = backtest_payload.get("benchmark") or {}
    rankings_lookup = {
        str(row.get("strategy_family")): row
        for row in _build_strategy_rankings(backtest_payload)
    }
    robustness_lookup = {
        str(row.get("strategy_family")): row
        for row in (benchmark.get("portfolio_robustness_summary") or [])
    }
    candidate_lookup = {
        str(row.get("strategy_family")): row
        for row in (benchmark.get("candidate_freeze") or [])
    }
    rows = [
        row
        for row in _portfolio_rows_for_sample(backtest_payload, sample_name="full_sample")
        if str(row.get("portfolio_scope")) == "single_family"
    ]
    ranked = sorted(
        rows,
        key=lambda row: (
            row.get("ending_bankroll") is not None,
            float(row.get("ending_bankroll") or float("-inf")),
            float(row.get("avg_executed_trade_return_with_slippage") or float("-inf")),
            int(row.get("executed_trade_count") or 0),
        ),
        reverse=True,
    )
    results: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked, start=1):
        strategy_family = str(row.get("strategy_family"))
        strategy_summary = rankings_lookup.get(strategy_family) or {}
        robustness = robustness_lookup.get(strategy_family) or {}
        candidate = candidate_lookup.get(strategy_family) or {}
        results.append(
            {
                "rank": rank,
                "strategy_family": strategy_family,
                "portfolio_scope": row.get("portfolio_scope"),
                "ending_bankroll": row.get("ending_bankroll"),
                "compounded_return": row.get("compounded_return"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "executed_trade_count": row.get("executed_trade_count"),
                "avg_executed_trade_return_with_slippage": row.get("avg_executed_trade_return_with_slippage"),
                "mean_ending_bankroll": robustness.get("mean_ending_bankroll"),
                "median_ending_bankroll": robustness.get("median_ending_bankroll"),
                "positive_seed_rate": robustness.get("positive_seed_rate"),
                "worst_max_drawdown_pct": robustness.get("worst_max_drawdown_pct"),
                "robustness_label": robustness.get("robustness_label"),
                "candidate_label": candidate.get("candidate_label"),
                "label_reason": candidate.get("label_reason"),
                "trade_count": strategy_summary.get("trade_count"),
                "win_rate": strategy_summary.get("win_rate"),
                "avg_gross_return_with_slippage": strategy_summary.get("avg_gross_return_with_slippage"),
                "entry_rule": strategy_summary.get("entry_rule"),
                "exit_rule": strategy_summary.get("exit_rule"),
            }
        )
    return results


def _build_portfolio_rankings(backtest_payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = backtest_payload.get("benchmark") or {}
    robustness_lookup = {
        str(row.get("strategy_family")): row
        for row in (benchmark.get("portfolio_robustness_summary") or [])
    }
    family_set = set(str(row.get("strategy_family")) for row in _build_individual_strategy_rankings(backtest_payload))
    rows = _portfolio_rows_for_sample(backtest_payload, sample_name="full_sample")
    ranked = sorted(
        rows,
        key=lambda row: (
            row.get("ending_bankroll") is not None,
            float(row.get("ending_bankroll") or float("-inf")),
            float(row.get("avg_executed_trade_return_with_slippage") or float("-inf")),
            int(row.get("executed_trade_count") or 0),
        ),
        reverse=True,
    )
    results: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked, start=1):
        strategy_family = str(row.get("strategy_family"))
        robustness = robustness_lookup.get(strategy_family) or {}
        results.append(
            {
                "rank": rank,
                "strategy_family": strategy_family,
                "portfolio_scope": row.get("portfolio_scope"),
                "strategy_family_members": row.get("strategy_family_members"),
                "family_type": "individual_strategy" if strategy_family in family_set else "portfolio_lane",
                "ending_bankroll": row.get("ending_bankroll"),
                "compounded_return": row.get("compounded_return"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "executed_trade_count": row.get("executed_trade_count"),
                "avg_executed_trade_return_with_slippage": row.get("avg_executed_trade_return_with_slippage"),
                "mean_ending_bankroll": robustness.get("mean_ending_bankroll"),
                "positive_seed_rate": robustness.get("positive_seed_rate"),
                "robustness_label": robustness.get("robustness_label"),
            }
        )
    return results


def _finalist_scalar(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _finalist_score(row: dict[str, Any]) -> float:
    bankroll = _finalist_scalar(
        row,
        "mean_ending_bankroll",
        "ending_bankroll",
        "portfolio_ending_bankroll",
        "compounded_bankroll",
        "avg_gross_return_with_slippage",
        "avg_executed_trade_return_with_slippage",
        "starting_bankroll",
    )
    if bankroll is None:
        bankroll = 0.0
    positive_rate = _finalist_scalar(row, "positive_seed_rate", "win_rate")
    if positive_rate is None:
        positive_rate = 0.0
    drawdown = _finalist_scalar(row, "max_drawdown_pct", "worst_max_drawdown_pct", "avg_mae_after_entry")
    if drawdown is None:
        drawdown = 0.0
    return float(bankroll) + (2.0 * float(positive_rate)) - (1.5 * abs(float(drawdown)))


def _finalist_identifier(row: dict[str, Any]) -> str:
    lane_name = str(row.get("lane_name") or row.get("strategy_family") or row.get("portfolio_scope") or "").strip()
    strategy_family = str(row.get("strategy_family") or "").strip()
    if lane_name and strategy_family and lane_name != strategy_family:
        return f"{lane_name}|{strategy_family}"
    return lane_name or strategy_family or "unknown_lane"


def _finalist_family_type(row: dict[str, Any]) -> str:
    family_type = str(row.get("family_type") or row.get("portfolio_scope") or "").strip()
    if family_type:
        return family_type
    if row.get("lane_type"):
        return str(row.get("lane_type"))
    return "deterministic"


def _lane_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("lane_summary", "mean_path", "mean_daily_path", "daily_paths", "series", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        flattened: list[dict[str, Any]] = []
        for value in payload.values():
            if isinstance(value, list):
                flattened.extend(row for row in value if isinstance(row, dict))
            elif isinstance(value, dict):
                nested_rows = _lane_rows_from_payload(value)
                if nested_rows:
                    flattened.extend(nested_rows)
        if flattened:
            return flattened
    return []


def _load_llm_experiment(benchmark: dict[str, Any]) -> dict[str, Any]:
    llm_experiment = benchmark.get("llm_experiment")
    return llm_experiment if isinstance(llm_experiment, dict) else {}


def _build_finalist_pool(snapshot_benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    llm_experiment = _load_llm_experiment(snapshot_benchmark)
    showdown = llm_experiment.get("showdown") if isinstance(llm_experiment.get("showdown"), dict) else {}
    showdown_finalists = _lane_rows_from_payload(showdown.get("finalists"))
    if showdown_finalists:
        normalized: list[dict[str, Any]] = []
        for row in showdown_finalists:
            normalized.append(
                {
                    "finalist_id": _finalist_identifier(row),
                    "lane_name": row.get("lane_name") or row.get("strategy_family"),
                    "strategy_family": row.get("strategy_family") or row.get("lane_name"),
                    "family_type": _finalist_family_type(row),
                    "source_name": "llm_showdown",
                    "score": row.get("finalist_score") if row.get("finalist_score") is not None else _finalist_score(row),
                    "score_components": {
                        "mean_ending_bankroll": _finalist_scalar(row, "mean_ending_bankroll", "ending_bankroll"),
                        "positive_rate": _finalist_scalar(row, "positive_iteration_rate", "positive_seed_rate", "win_rate"),
                        "drawdown_pct": _finalist_scalar(row, "mean_max_drawdown_pct", "max_drawdown_pct"),
                    },
                    "mean_ending_bankroll": row.get("mean_ending_bankroll"),
                    "ending_bankroll": row.get("ending_bankroll"),
                    "positive_seed_rate": row.get("positive_iteration_rate") or row.get("positive_seed_rate"),
                    "max_drawdown_pct": row.get("mean_max_drawdown_pct") or row.get("max_drawdown_pct"),
                    "trade_count": row.get("mean_executed_trade_count") or row.get("executed_trade_count"),
                    "win_rate": row.get("win_rate"),
                    "candidate_label": row.get("candidate_label"),
                    "label_reason": row.get("label_reason"),
                    "portfolio_scope": row.get("portfolio_scope"),
                    "strategy_family_members": row.get("strategy_family_members"),
                    "robustness_label": row.get("robustness_label"),
                    "prompt_profile": row.get("prompt_profile"),
                    "reasoning_effort": row.get("reasoning_effort"),
                    "include_rationale": row.get("include_rationale"),
                    "use_confidence_gate": row.get("use_confidence_gate"),
                }
            )
        return normalized[:6]

    candidates: list[dict[str, Any]] = []

    deterministic_sources = [
        ("portfolio_rankings", snapshot_benchmark.get("portfolio_rankings") or []),
        ("individual_strategy_rankings", snapshot_benchmark.get("individual_strategy_rankings") or []),
        ("strategy_rankings", snapshot_benchmark.get("strategy_rankings") or []),
    ]
    for source_name, rows in deterministic_sources:
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = dict(row)
            candidate.setdefault("source_name", source_name)
            candidate.setdefault("lane_name", candidate.get("strategy_family") or candidate.get("portfolio_scope"))
            candidate.setdefault("family_type", candidate.get("portfolio_scope") or "deterministic")
            candidates.append(candidate)

    for row in _lane_rows_from_payload(llm_experiment.get("lane_summary")):
        candidate = dict(row)
        candidate.setdefault("source_name", "llm_experiment")
        candidate.setdefault("lane_name", candidate.get("lane_name") or candidate.get("variant_name") or candidate.get("strategy_family"))
        candidate.setdefault("family_type", "llm_lane")
        candidate.setdefault("strategy_family", candidate.get("strategy_family") or candidate.get("lane_name"))
        candidates.append(candidate)

    seen: set[str] = set()
    ranked: list[dict[str, Any]] = []
    for row in candidates:
        identifier = _finalist_identifier(row)
        if identifier in seen:
            continue
        seen.add(identifier)
        score = _finalist_score(row)
        ranked.append(
            {
                "finalist_id": identifier,
                "lane_name": row.get("lane_name") or row.get("strategy_family") or row.get("portfolio_scope"),
                "strategy_family": row.get("strategy_family"),
                "family_type": _finalist_family_type(row),
                "source_name": row.get("source_name"),
                "score": score,
                "score_components": {
                    "mean_ending_bankroll": _finalist_scalar(
                        row, "mean_ending_bankroll", "ending_bankroll", "portfolio_ending_bankroll", "compounded_bankroll"
                    ),
                    "positive_rate": _finalist_scalar(row, "positive_seed_rate", "win_rate"),
                    "drawdown_pct": _finalist_scalar(row, "max_drawdown_pct", "worst_max_drawdown_pct"),
                },
                "mean_ending_bankroll": row.get("mean_ending_bankroll"),
                "ending_bankroll": row.get("ending_bankroll"),
                "positive_seed_rate": row.get("positive_seed_rate"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "trade_count": row.get("trade_count") or row.get("executed_trade_count"),
                "win_rate": row.get("win_rate"),
                "candidate_label": row.get("candidate_label"),
                "label_reason": row.get("label_reason"),
                "portfolio_scope": row.get("portfolio_scope"),
                "strategy_family_members": row.get("strategy_family_members"),
                "robustness_label": row.get("robustness_label"),
            }
        )

    ranked.sort(
        key=lambda row: (
            row.get("score") is not None,
            float(row.get("score") or float("-inf")),
            float(row.get("mean_ending_bankroll") or row.get("ending_bankroll") or float("-inf")),
            float(row.get("positive_seed_rate") or row.get("win_rate") or float("-inf")),
            -(abs(float(row.get("max_drawdown_pct") or 0.0))),
            int(row.get("trade_count") or 0),
            str(row.get("lane_name") or ""),
        ),
        reverse=True,
    )
    return ranked[:6]


def _row_matches_finalist(row: dict[str, Any], finalist: dict[str, Any]) -> bool:
    finalist_lane = str(finalist.get("lane_name") or "")
    finalist_family = str(finalist.get("strategy_family") or "")
    finalist_id = str(finalist.get("finalist_id") or "")
    row_lane = str(row.get("lane_name") or row.get("strategy_family") or row.get("portfolio_scope") or "")
    row_family = str(row.get("strategy_family") or "")
    row_candidates = {
        str(row.get("lane_name") or ""),
        str(row.get("strategy_family") or ""),
        str(row.get("portfolio_scope") or ""),
        str(row.get("family_name") or ""),
        str(row.get("portfolio_lane_name") or ""),
        str(row.get("variant_name") or ""),
        str(row.get("controller_name") or ""),
        str(row.get("finalist_id") or ""),
    }
    if finalist_lane and row_lane == finalist_lane:
        return True
    if finalist_family and row_family == finalist_family:
        return True
    if finalist_id and finalist_id in row_candidates:
        return True
    if finalist.get("portfolio_scope") and row.get("portfolio_scope") == finalist.get("portfolio_scope"):
        return True
    return False


def _series_from_rows(
    rows: list[dict[str, Any]],
    *,
    finalist: dict[str, Any],
    source_name: str,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    matched = [dict(row) for row in rows if _row_matches_finalist(row, finalist)]
    if not matched:
        return []

    normalized: list[dict[str, Any]] = []
    for row in matched:
        x_value = (
            row.get("date")
            or row.get("day")
            or row.get("game_date")
            or row.get("timestamp")
            or row.get("step")
            or row.get("sequence_index")
            or row.get("day_index")
        )
        y_value = (
            row.get("bankroll")
            or row.get("ending_bankroll")
            or row.get("equity")
            or row.get("value")
            or row.get("mean_bankroll")
            or row.get("mean_ending_bankroll")
        )
        normalized.append(
            to_jsonable(
                {
                    "finalist_id": finalist.get("finalist_id"),
                    "lane_name": finalist.get("lane_name"),
                    "strategy_family": finalist.get("strategy_family"),
                    "family_type": finalist.get("family_type"),
                    "source_name": source_name,
                    "x": x_value,
                    "y": y_value,
                    "row": row,
                }
            )
        )
    return normalized


def _build_finalist_series(snapshot_benchmark: dict[str, Any], finalists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    llm_experiment = _load_llm_experiment(snapshot_benchmark)
    showdown = llm_experiment.get("showdown") if isinstance(llm_experiment.get("showdown"), dict) else {}
    showdown_rows = _lane_rows_from_payload(showdown.get("daily_paths"))
    deterministic_rows = _lane_rows_from_payload(snapshot_benchmark.get("portfolio_daily_paths"))
    llm_rows = _lane_rows_from_payload(
        llm_experiment.get("mean_path")
        or llm_experiment.get("mean_daily_path")
        or llm_experiment.get("daily_paths")
        or llm_experiment.get("series")
    )

    series_rows: list[dict[str, Any]] = []
    for finalist in finalists:
        source_name = str(finalist.get("source_name") or "")
        finalist_rows: list[dict[str, Any]] = []
        if showdown_rows:
            finalist_rows = _series_from_rows(showdown_rows, finalist=finalist, source_name="llm_showdown")
        elif source_name in {"llm_experiment", "llm_showdown"}:
            finalist_rows = _series_from_rows(llm_rows, finalist=finalist, source_name="llm_experiment")
        else:
            finalist_rows = _series_from_rows(deterministic_rows, finalist=finalist, source_name="portfolio_daily_paths")
        if finalist_rows:
            series_rows.append(
                {
                    "finalist_id": finalist.get("finalist_id"),
                    "lane_name": finalist.get("lane_name"),
                    "strategy_family": finalist.get("strategy_family"),
                    "family_type": finalist.get("family_type"),
                    "source_name": "llm_showdown" if showdown_rows else (source_name or "portfolio_daily_paths"),
                    "rows": finalist_rows,
                }
            )
    return series_rows


def _build_llm_variant_rankings(snapshot_benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    llm_experiment = _load_llm_experiment(snapshot_benchmark)
    rows = _lane_rows_from_payload(llm_experiment.get("lane_summary"))
    ranked: list[dict[str, Any]] = []
    for rank, row in enumerate(
        sorted(
            rows,
            key=lambda item: (
                item.get("mean_ending_bankroll") is not None,
                float(item.get("mean_ending_bankroll") or item.get("ending_bankroll") or float("-inf")),
                float(item.get("positive_seed_rate") or item.get("win_rate") or float("-inf")),
                -(abs(float(item.get("max_drawdown_pct") or item.get("worst_max_drawdown_pct") or 0.0))),
                int(item.get("trade_count") or item.get("executed_trade_count") or 0),
                str(item.get("lane_name") or item.get("variant_name") or ""),
            ),
            reverse=True,
        ),
        start=1,
    ):
        ranked.append(
            to_jsonable(
                {
                    "rank": rank,
                    "lane_name": row.get("lane_name") or row.get("variant_name"),
                    "strategy_family": row.get("strategy_family"),
                    "mean_ending_bankroll": row.get("mean_ending_bankroll"),
                    "ending_bankroll": row.get("ending_bankroll"),
                    "positive_seed_rate": row.get("positive_seed_rate"),
                    "max_drawdown_pct": row.get("max_drawdown_pct"),
                    "trade_count": row.get("trade_count") or row.get("executed_trade_count"),
                    "win_rate": row.get("win_rate"),
                    "score": _finalist_score(row),
                    "notes": row.get("notes") or row.get("label_reason"),
                }
            )
        )
    return ranked


def _build_controller_summary(snapshot_benchmark: dict[str, Any], finalists: list[dict[str, Any]]) -> dict[str, Any]:
    master_router = snapshot_benchmark.get("master_router") or {}
    llm_experiment = _load_llm_experiment(snapshot_benchmark)
    showdown = llm_experiment.get("showdown") if isinstance(llm_experiment.get("showdown"), dict) else {}
    showdown_summary_rows = _lane_rows_from_payload(showdown.get("summary"))
    showdown_lookup = {
        str(row.get("lane_name") or row.get("strategy_family") or ""): row
        for row in showdown_summary_rows
    }
    winner_definition = next(
        (
            row
            for row in finalists
            if str(row.get("strategy_family")) == "winner_definition"
        ),
        {},
    )
    best_llm = next(
        (
            row
            for row in finalists
            if (
                str(row.get("family_type")) in {"llm_lane", "llm_variant"}
                or str(row.get("lane_name") or "").startswith("llm_")
            )
        ),
        {},
    )
    return to_jsonable(
        {
            "master_router": {
                "family_name": master_router.get("family_name"),
                "selection_sample_name": master_router.get("selection_sample_name"),
                "core_families": list(master_router.get("core_families") or []),
                "extra_families": list(master_router.get("extra_families") or []),
                "route_summary_count": len(master_router.get("route_summary") or []),
            },
            "winner_definition": {
                "finalist_id": winner_definition.get("finalist_id"),
                "lane_name": winner_definition.get("lane_name"),
                "score": winner_definition.get("score"),
                "mean_ending_bankroll": winner_definition.get("mean_ending_bankroll"),
                "positive_seed_rate": winner_definition.get("positive_seed_rate"),
                "max_drawdown_pct": winner_definition.get("max_drawdown_pct"),
                "showdown_ending_bankroll": (showdown_lookup.get("winner_definition") or {}).get("ending_bankroll"),
                "showdown_drawdown_pct": (showdown_lookup.get("winner_definition") or {}).get("max_drawdown_pct"),
            },
            "best_llm_lane": {
                "finalist_id": best_llm.get("finalist_id"),
                "lane_name": best_llm.get("lane_name"),
                "score": best_llm.get("score"),
                "mean_ending_bankroll": best_llm.get("mean_ending_bankroll"),
                "positive_seed_rate": best_llm.get("positive_seed_rate"),
                "max_drawdown_pct": best_llm.get("max_drawdown_pct"),
                "showdown_ending_bankroll": (showdown_lookup.get(str(best_llm.get("lane_name") or "")) or {}).get("ending_bankroll"),
                "showdown_drawdown_pct": (showdown_lookup.get(str(best_llm.get("lane_name") or "")) or {}).get("max_drawdown_pct"),
            },
            "comparison_note": (
                "Finalists come from the LLM showdown when available; otherwise they fall back to benchmark-only rankings."
            ),
        }
    )


def _build_master_router_summary(backtest_payload: dict[str, Any]) -> dict[str, Any]:
    benchmark = backtest_payload.get("benchmark") or {}
    master_router_family = str(benchmark.get("master_router_family_name") or "")
    if not master_router_family:
        return {}

    summary_lookup = {
        (str(row.get("sample_name")), str(row.get("strategy_family"))): row
        for row in (benchmark.get("portfolio_summary") or [])
    }
    comparison_families = [
        master_router_family,
        str(benchmark.get("portfolio_routing_family_name") or ""),
        str(benchmark.get("portfolio_combined_family_name") or ""),
        "winner_definition",
        "inversion",
        "underdog_liftoff",
        "favorite_panic_fade_v1",
        "q1_repricing",
        "halftime_q3_repricing_v1",
        "q4_clutch",
    ]
    comparison_rows: list[dict[str, Any]] = []
    for sample_name in ("full_sample", "time_validation", "random_holdout"):
        for strategy_family in comparison_families:
            if not strategy_family:
                continue
            row = summary_lookup.get((sample_name, strategy_family))
            if row is None:
                continue
            comparison_rows.append(
                {
                    "sample_name": sample_name,
                    "strategy_family": strategy_family,
                    "portfolio_scope": row.get("portfolio_scope"),
                    "ending_bankroll": row.get("ending_bankroll"),
                    "compounded_return": row.get("compounded_return"),
                    "max_drawdown_pct": row.get("max_drawdown_pct"),
                    "executed_trade_count": row.get("executed_trade_count"),
                    "avg_executed_trade_return_with_slippage": row.get("avg_executed_trade_return_with_slippage"),
                }
            )

    decision_frame = pd.DataFrame(list(benchmark.get("master_router_decisions") or []))
    selection_counts: list[dict[str, Any]] = []
    band_counts: list[dict[str, Any]] = []
    low_confidence_summary: list[dict[str, Any]] = []
    extra_trigger_counts: list[dict[str, Any]] = []
    if not decision_frame.empty:
        decision_frame["selected_confidence"] = pd.to_numeric(decision_frame["selected_confidence"], errors="coerce")
        selection_counts = list(
            to_jsonable(
                decision_frame.groupby(["sample_name", "selected_core_family"], dropna=False)
                .agg(
                    selection_count=("game_id", "count"),
                    mean_confidence=("selected_confidence", "mean"),
                    median_confidence=("selected_confidence", "median"),
                )
                .reset_index()
                .sort_values(["sample_name", "selection_count", "selected_core_family"], ascending=[True, False, True])
                .to_dict(orient="records")
            )
        )
        low_confidence_summary = list(
            to_jsonable(
                decision_frame.groupby(["sample_name"], dropna=False)
                .agg(
                    decision_count=("game_id", "count"),
                    mean_confidence=("selected_confidence", "mean"),
                    median_confidence=("selected_confidence", "median"),
                    low_confidence_count_060=("selected_confidence", lambda values: int((values.fillna(-1) < 0.60).sum())),
                    low_confidence_count_070=("selected_confidence", lambda values: int((values.fillna(-1) < 0.70).sum())),
                )
                .reset_index()
                .sort_values(["sample_name"], ascending=[True])
                .to_dict(orient="records")
            )
        )
        band_counts = list(
            to_jsonable(
                decision_frame[decision_frame["sample_name"].astype(str) == "full_sample"]
                .groupby(["opening_band", "selected_core_family"], dropna=False)
                .agg(selection_count=("game_id", "count"))
                .reset_index()
                .sort_values(["opening_band", "selection_count"], ascending=[True, False])
                .to_dict(orient="records")
            )
        )
        extra_trigger_rows: list[dict[str, Any]] = []
        for row in decision_frame.to_dict(orient="records"):
            raw_value = row.get("triggered_extra_families_json")
            if raw_value in (None, "", "[]"):
                continue
            try:
                family_values = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            except json.JSONDecodeError:
                continue
            if not isinstance(family_values, list):
                continue
            for extra_family in family_values:
                extra_trigger_rows.append(
                    {
                        "sample_name": row.get("sample_name"),
                        "extra_family": str(extra_family),
                    }
                )
        if extra_trigger_rows:
            extra_trigger_counts = list(
                to_jsonable(
                    pd.DataFrame(extra_trigger_rows)
                    .groupby(["sample_name", "extra_family"], dropna=False)
                    .agg(trigger_count=("extra_family", "count"))
                    .reset_index()
                    .sort_values(["sample_name", "trigger_count", "extra_family"], ascending=[True, False, True])
                    .to_dict(orient="records")
                )
            )

    route_summary = list(to_jsonable((benchmark.get("route_summary") or [])))
    return {
        "family_name": master_router_family,
        "selection_sample_name": benchmark.get("master_router_selection_sample_name"),
        "core_families": list(benchmark.get("master_router_core_families") or []),
        "extra_families": list(benchmark.get("master_router_extra_families") or []),
        "comparison_rows": comparison_rows,
        "selection_counts": selection_counts,
        "low_confidence_summary": low_confidence_summary,
        "extra_trigger_counts": extra_trigger_counts,
        "band_counts": band_counts,
        "route_summary": route_summary,
    }


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
    master_router_summary = _build_master_router_summary(backtest_payload)
    strategy_rankings = _build_strategy_rankings(backtest_payload)
    individual_strategy_rankings = _build_individual_strategy_rankings(backtest_payload)
    portfolio_rankings = _build_portfolio_rankings(backtest_payload)
    benchmark_view = {
        **benchmark,
        "strategy_rankings": strategy_rankings,
        "individual_strategy_rankings": individual_strategy_rankings,
        "portfolio_rankings": portfolio_rankings,
        "master_router": master_router_summary,
    }
    finalists = _build_finalist_pool(benchmark_view)

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
            "strategy_rankings": strategy_rankings,
            "individual_strategy_rankings": individual_strategy_rankings,
            "portfolio_rankings": portfolio_rankings,
            "master_router": master_router_summary,
            "candidate_freeze": list(benchmark.get("candidate_freeze") or []),
            "portfolio_candidate_freeze": list(benchmark.get("portfolio_candidate_freeze") or []),
            "split_summary": list(benchmark.get("split_summary") or []),
            "portfolio_summary": list(benchmark.get("portfolio_summary") or []),
            "portfolio_robustness_summary": list(benchmark.get("portfolio_robustness_summary") or []),
            "comparators": list(benchmark.get("comparators") or []),
            "comparator_summary": list(benchmark.get("comparator_summary") or []),
            "context_rankings": list(benchmark.get("context_rankings") or []),
            "game_strategy_classification": list(benchmark.get("game_strategy_classification") or []),
            "studio_dashboard": {
                "finalists": finalists,
                "finalist_series": _build_finalist_series(benchmark_view, finalists),
                "llm_variant_rankings": _build_llm_variant_rankings(benchmark_view),
                "controller_summary": _build_controller_summary(benchmark_view, finalists),
                "metadata": {
                    "selection_method": "prefer_llm_showdown_finalists_then_fallback_to_ranked_benchmark_pool",
                    "series_rule": "prefer shared llm_showdown daily paths; otherwise fall back to deterministic portfolio daily paths",
                    "llm_policy": "llm lanes surface when llm_experiment summary exists, and their line chart uses showdown paths when present",
                    "notes": [
                        "Top 6 finalists are deduplicated by lane and family identity.",
                        "This payload is additive and leaves existing benchmark fields unchanged.",
                    ],
                },
            },
        },
        "models": {
            "feature_set_version": model_payload.get("feature_set_version"),
            "train_cutoff": model_payload.get("train_cutoff"),
            "validation_window": model_payload.get("validation_window"),
            "tracks": _build_model_tracks(model_payload),
        },
    }
    return to_jsonable(snapshot)


def build_analysis_backtest_index(bundle: AnalysisConsumerBundle) -> dict[str, Any]:
    snapshot = build_analysis_consumer_snapshot(bundle)
    backtest_artifacts = dict((snapshot.get("artifacts") or {}).get("backtests") or {})
    benchmark = snapshot.get("benchmark") or {}
    rankings = list(benchmark.get("strategy_rankings") or [])
    portfolio_lookup = {
        str(row.get("strategy_family")): row
        for row in (benchmark.get("individual_strategy_rankings") or [])
    }
    families: list[dict[str, Any]] = []
    for row in rankings:
        strategy_family = str(row.get("strategy_family"))
        portfolio_row = portfolio_lookup.get(strategy_family) or {}
        families.append(
            {
                "strategy_family": strategy_family,
                "summary": {
                    "trade_count": row.get("trade_count"),
                    "win_rate": row.get("win_rate"),
                    "avg_gross_return": row.get("avg_gross_return"),
                    "avg_gross_return_with_slippage": row.get("avg_gross_return_with_slippage"),
                    "avg_hold_time_seconds": row.get("avg_hold_time_seconds"),
                    "avg_mfe_after_entry": row.get("avg_mfe_after_entry"),
                    "avg_mae_after_entry": row.get("avg_mae_after_entry"),
                    "ending_bankroll": portfolio_row.get("ending_bankroll"),
                    "max_drawdown_pct": portfolio_row.get("max_drawdown_pct"),
                    "mean_ending_bankroll": portfolio_row.get("mean_ending_bankroll"),
                    "positive_seed_rate": portfolio_row.get("positive_seed_rate"),
                    "robustness_label": portfolio_row.get("robustness_label"),
                    "candidate_label": row.get("candidate_label"),
                    "label_reason": row.get("label_reason"),
                },
                "artifact_paths": _family_artifact_paths(backtest_artifacts, strategy_family=strategy_family),
            }
        )

    payload = {
        "season": bundle.season,
        "season_phase": bundle.season_phase,
        "analysis_version": bundle.analysis_version,
        "output_dir": str(bundle.output_dir),
        "benchmark": snapshot.get("benchmark") or {},
        "families": families,
    }
    return to_jsonable(payload)


def build_analysis_backtest_family_detail(
    bundle: AnalysisConsumerBundle,
    *,
    strategy_family: str,
    trade_limit: int = 5,
    context_limit: int = 10,
    trace_limit: int = 3,
) -> dict[str, Any]:
    snapshot = build_analysis_consumer_snapshot(bundle)
    backtest_payload = bundle.backtest_payload
    benchmark = backtest_payload.get("benchmark") or {}
    strategy_family_text = str(strategy_family)

    sample_summaries = _family_rows(list(benchmark.get("family_summary") or []), strategy_family=strategy_family_text)
    summary_lookup = {
        str(row.get("strategy_family")): row for row in (snapshot.get("benchmark") or {}).get("strategy_rankings") or []
    }
    summary = summary_lookup.get(strategy_family_text)
    if summary is None and not sample_summaries:
        raise ValueError(f"Unknown strategy_family for analysis backtest detail: {strategy_family_text}")

    candidate_freeze = next(
        (row for row in (snapshot.get("benchmark") or {}).get("candidate_freeze") or [] if str(row.get("strategy_family")) == strategy_family_text),
        None,
    )
    individual_ranking = next(
        (
            row
            for row in (snapshot.get("benchmark") or {}).get("individual_strategy_rankings") or []
            if str(row.get("strategy_family")) == strategy_family_text
        ),
        None,
    )
    comparator_summary = _family_rows(
        list((snapshot.get("benchmark") or {}).get("comparator_summary") or []),
        strategy_family=strategy_family_text,
    )
    context_rankings = _family_rows(
        list((snapshot.get("benchmark") or {}).get("context_rankings") or []),
        strategy_family=strategy_family_text,
    )

    backtest_artifacts = dict((snapshot.get("artifacts") or {}).get("backtests") or {})
    artifact_paths = _family_artifact_paths(backtest_artifacts, strategy_family=strategy_family_text)
    best_trades = _bounded_records(
        _read_table_artifact(
            artifacts=backtest_artifacts,
            csv_key=f"{strategy_family_text}_best_trades_csv",
            parquet_key=f"{strategy_family_text}_best_trades_parquet",
        ),
        limit=trade_limit,
    )
    worst_trades = _bounded_records(
        _read_table_artifact(
            artifacts=backtest_artifacts,
            csv_key=f"{strategy_family_text}_worst_trades_csv",
            parquet_key=f"{strategy_family_text}_worst_trades_parquet",
        ),
        limit=trade_limit,
    )
    context_summary = _bounded_records(
        _read_table_artifact(
            artifacts=backtest_artifacts,
            csv_key=f"{strategy_family_text}_context_summary_csv",
            parquet_key=f"{strategy_family_text}_context_summary_parquet",
        ),
        limit=context_limit,
    )
    trade_traces = _read_json_list(backtest_artifacts.get(f"{strategy_family_text}_trade_traces_json"))
    if trace_limit is not None:
        trade_traces = trade_traces[:trace_limit]

    payload = {
        "season": bundle.season,
        "season_phase": bundle.season_phase,
        "analysis_version": bundle.analysis_version,
        "output_dir": str(bundle.output_dir),
        "strategy_family": strategy_family_text,
        "summary": summary or to_jsonable(sample_summaries[0]),
        "sample_summaries": sample_summaries,
        "candidate_freeze": candidate_freeze,
        "individual_ranking": individual_ranking,
        "comparator_summary": comparator_summary,
        "context_rankings": context_rankings,
        "artifact_paths": artifact_paths,
        "best_trades": best_trades,
        "worst_trades": worst_trades,
        "context_summary": context_summary,
        "trade_traces": to_jsonable(trade_traces),
    }
    return to_jsonable(payload)


def load_analysis_consumer_snapshot(request: AnalysisConsumerRequest) -> dict[str, Any]:
    return build_analysis_consumer_snapshot(load_analysis_consumer_bundle(request))


def load_analysis_backtest_index(request: AnalysisConsumerRequest) -> dict[str, Any]:
    return build_analysis_backtest_index(load_analysis_consumer_bundle(request))


def load_analysis_backtest_family_detail(
    request: AnalysisConsumerRequest,
    *,
    strategy_family: str,
    trade_limit: int = 5,
    context_limit: int = 10,
    trace_limit: int = 3,
) -> dict[str, Any]:
    return build_analysis_backtest_family_detail(
        load_analysis_consumer_bundle(request),
        strategy_family=strategy_family,
        trade_limit=trade_limit,
        context_limit=context_limit,
        trace_limit=trace_limit,
    )


__all__ = [
    "AnalysisConsumerBundle",
    "build_analysis_backtest_family_detail",
    "build_analysis_backtest_index",
    "build_analysis_consumer_snapshot",
    "list_available_analysis_versions",
    "load_analysis_backtest_family_detail",
    "load_analysis_backtest_index",
    "load_analysis_consumer_bundle",
    "load_analysis_consumer_snapshot",
    "resolve_analysis_consumer_paths",
]
