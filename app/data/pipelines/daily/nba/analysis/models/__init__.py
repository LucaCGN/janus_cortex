from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import ensure_output_dir, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, DEFAULT_SEASON, DEFAULT_SEASON_PHASE, ModelRunRequest
from app.data.pipelines.daily.nba.analysis.models.features import load_model_input_frames, resolve_train_cutoff
from app.data.pipelines.daily.nba.analysis.models.trade_quality import run_trade_quality_baseline
from app.data.pipelines.daily.nba.analysis.models.volatility import run_volatility_inversion_baseline
from app.data.pipelines.daily.nba.analysis.models.winner_timing import run_winner_definition_timing_baseline


def _format_num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _append_naive_comparison(lines: list[str], comparison: dict[str, Any]) -> None:
    if not comparison:
        return
    lines.append(f"- Naive baseline metric: `{_format_num(comparison.get('naive_value'))}`")
    lines.append(f"- Model vs naive delta: `{_format_num(comparison.get('delta'))}`")
    lines.append(f"- Better than naive: `{comparison.get('better_than_naive')}`")


def _render_model_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# NBA Analysis Baselines",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        f"- Train cutoff: `{payload.get('train_cutoff')}`",
        "",
    ]
    for track_name, track_payload in (payload.get("tracks") or {}).items():
        lines.extend([f"## {track_name}", ""])
        if track_payload.get("status") != "success":
            lines.append(f"- Status: `{track_payload.get('status', 'unknown')}`")
            lines.append("")
            continue
        lines.append(f"- Model family: `{track_payload.get('model_family')}`")
        lines.append(f"- Train rows: `{track_payload.get('train_rows')}`")
        lines.append(f"- Validation rows: `{track_payload.get('validation_rows')}`")
        metrics = track_payload.get("metrics") or {}
        if metrics:
            for key, value in metrics.items():
                lines.append(f"- {key}: `{_format_num(value)}`")
        _append_naive_comparison(lines, track_payload.get("naive_comparison") or {})
        targets = track_payload.get("targets") or {}
        for target_name, target_payload in targets.items():
            lines.append(f"- {target_name} rmse: `{_format_num(target_payload.get('rmse'))}`")
            lines.append(f"- {target_name} mae: `{_format_num(target_payload.get('mae'))}`")
            lines.append(f"- {target_name} rank_corr: `{_format_num(target_payload.get('rank_corr'))}`")
            _append_naive_comparison(lines, target_payload.get("naive_comparison") or {})
        lines.append("")
    return "\n".join(lines)


def train_analysis_baselines(request: ModelRunRequest) -> dict[str, Any]:
    output_dir = ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version) / "models"
    output_dir.mkdir(parents=True, exist_ok=True)
    with managed_connection() as connection:
        frames = load_model_input_frames(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )

    train_cutoff = resolve_train_cutoff(
        frames.state_df["game_date"] if not frames.state_df.empty and "game_date" in frames.state_df.columns else None,
        requested_cutoff=request.train_cutoff,
    )

    tracks: dict[str, Any] = {}
    artifacts: dict[str, Any] = {}
    requested_tracks = (
        [request.target_family]
        if request.target_family != "all"
        else ["volatility_inversion", "trade_window_quality", "winner_definition_timing"]
    )
    if "volatility_inversion" in requested_tracks:
        track_payload, track_artifacts = run_volatility_inversion_baseline(
            frames.state_df,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
            train_cutoff=train_cutoff,
            output_dir=output_dir,
        )
        tracks["volatility_inversion"] = track_payload
        artifacts["volatility_inversion"] = track_artifacts
    if "trade_window_quality" in requested_tracks:
        track_payload, track_artifacts = run_trade_quality_baseline(
            frames.state_df,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
            train_cutoff=train_cutoff,
            output_dir=output_dir,
        )
        tracks["trade_window_quality"] = track_payload
        artifacts["trade_window_quality"] = track_artifacts
    if "winner_definition_timing" in requested_tracks:
        track_payload, track_artifacts = run_winner_definition_timing_baseline(
            profiles_df=frames.profiles_df,
            state_df=frames.state_df,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
            train_cutoff=train_cutoff,
            output_dir=output_dir,
        )
        tracks["winner_definition_timing"] = track_payload
        artifacts["winner_definition_timing"] = track_artifacts

    payload = {
        "season": request.season,
        "season_phase": request.season_phase,
        "analysis_version": request.analysis_version,
        "feature_set_version": request.feature_set_version,
        "train_cutoff": train_cutoff.isoformat() if train_cutoff is not None else None,
        "validation_window": request.validation_window,
        "tracks": tracks,
        "artifacts": {},
    }
    payload["artifacts"]["json"] = str(output_dir / "train_analysis_baselines.json")
    payload["artifacts"]["markdown"] = write_markdown(output_dir / "train_analysis_baselines.md", _render_model_markdown(payload))
    payload["artifacts"]["tracks"] = artifacts
    write_json(Path(payload["artifacts"]["json"]), payload)
    return to_jsonable(payload)


__all__ = [
    "ANALYSIS_VERSION",
    "DEFAULT_SEASON",
    "DEFAULT_SEASON_PHASE",
    "train_analysis_baselines",
]
