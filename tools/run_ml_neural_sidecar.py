from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.benchmark_integration import resolve_default_shared_root
from app.data.pipelines.daily.nba.analysis.contracts import DEFAULT_SEASON
from app.data.pipelines.daily.nba.analysis.ml_trading_lane import (
    CALIBRATION_TARGET_COLUMN,
    GATE_TARGET_COLUMN,
    ML_LANE_ID,
    ML_LANE_LABEL,
    ML_LANE_TYPE,
    ML_OUTPUT_DIRNAME,
    RANKING_TARGET_COLUMN,
    _build_feature_transform,
    _build_selected_trade_frames,
    _compute_subject_metrics,
    _evaluate_binary_predictions,
    _fit_platt_scaler,
    _predict_platt_scaler,
    _subject_artifact_stem,
    _transform_features,
    _write_subject_trade_artifacts,
)


SOURCE_ARTIFACT_NAME = "expanded_regular_replay_ml_v1"
NEURAL_ARTIFACT_NAME = "ml_neural_sidecar_v1"
RAW_SUBJECT_ID = "ml_neural_sidecar_v1_raw"
CALIBRATED_SUBJECT_ID = "ml_neural_sidecar_v1_calibrated"
NEURAL_SCHEMA_VERSION = "ml_neural_sidecar_v1"
NEURAL_NUMERIC_COLUMNS = [
    "signal_strength",
    "entry_price",
    "raw_confidence",
    "heuristic_rank_score",
    "heuristic_execute_score",
    "historical_context_trade_count",
    "historical_context_win_rate",
    "historical_context_avg_return",
    "historical_family_trade_count",
    "historical_family_win_rate",
    "historical_family_avg_return",
    "state_seconds_to_game_end",
    "state_score_diff",
    "state_lead_changes_so_far",
    "state_net_points_last_5_events",
    "state_abs_price_delta_from_open",
    "state_gap_before_seconds",
    "state_gap_after_seconds",
    "first_attempt_signal_age_seconds",
    "first_attempt_quote_age_seconds",
    "first_attempt_spread_cents",
    "first_attempt_state_lag",
]
NEURAL_CATEGORICAL_COLUMNS = [
    "subject_type",
    "candidate_kind",
    "strategy_family",
    "opening_band",
    "period_label",
    "score_diff_bucket",
]


@dataclass(slots=True)
class NeuralSidecarRequest:
    season: str = DEFAULT_SEASON
    shared_root: str | None = None
    source_artifact_name: str = SOURCE_ARTIFACT_NAME
    artifact_name: str = NEURAL_ARTIFACT_NAME
    train_slice_name: str = "training_history"
    holdout_slice_name: str = "postseason_holdout"
    seed: int = 20260504
    hidden_units: int = 16
    epochs: int = 180
    learning_rate: float = 0.01
    weight_decay: float = 0.001
    calibration_date_fraction: float = 0.20


def _resolve_shared_root(shared_root: str | None) -> Path:
    return Path(shared_root) if shared_root else resolve_default_shared_root()


def _load_source_dataset(shared_root: Path, *, season: str, source_artifact_name: str) -> pd.DataFrame:
    path = shared_root / "artifacts" / ML_OUTPUT_DIRNAME / season / source_artifact_name / "all_candidates.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing source ML candidate dataset: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        raise ValueError(f"Source ML candidate dataset is empty: {path}")
    if "evaluation_slice" not in frame.columns:
        raise ValueError("Source dataset must include evaluation_slice for regular/postseason leakage checks.")
    for column in (RANKING_TARGET_COLUMN, CALIBRATION_TARGET_COLUMN, GATE_TARGET_COLUMN):
        if column not in frame.columns:
            raise ValueError(f"Source dataset missing required label column: {column}")
        frame[column] = frame[column].fillna(False).astype(bool)
    frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce")
    return frame


def _split_regular_train_calibration(
    train_df: pd.DataFrame,
    *,
    calibration_date_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(train_df["game_date"], errors="coerce").dropna().drop_duplicates().sort_values().tolist()
    if len(dates) < 3:
        return train_df.copy(), train_df.copy()
    calibration_count = max(1, int(round(len(dates) * float(calibration_date_fraction))))
    calibration_dates = set(dates[-calibration_count:])
    core_train_df = train_df[~train_df["game_date"].isin(calibration_dates)].copy()
    calibration_df = train_df[train_df["game_date"].isin(calibration_dates)].copy()
    if core_train_df.empty or calibration_df.empty:
        return train_df.copy(), train_df.copy()
    return core_train_df, calibration_df


def _train_torch_mlp(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_calibration: np.ndarray,
    y_calibration: np.ndarray,
    *,
    seed: int,
    hidden_units: int,
    epochs: int,
    learning_rate: float,
    weight_decay: float,
) -> tuple[Any, dict[str, Any]]:
    import torch
    from torch import nn

    torch.manual_seed(seed)
    np.random.seed(seed)
    x_train_tensor = torch.tensor(x_train, dtype=torch.float32)
    y_train_tensor = torch.tensor(y_train.reshape(-1, 1), dtype=torch.float32)
    x_calibration_tensor = torch.tensor(x_calibration, dtype=torch.float32)
    y_calibration_tensor = torch.tensor(y_calibration.reshape(-1, 1), dtype=torch.float32)
    model = nn.Sequential(
        nn.Linear(x_train.shape[1], hidden_units),
        nn.ReLU(),
        nn.Dropout(p=0.05),
        nn.Linear(hidden_units, 1),
    )
    positives = float(np.sum(y_train))
    negatives = float(len(y_train) - positives)
    pos_weight = torch.tensor([min(max(negatives / positives, 1.0), 8.0)], dtype=torch.float32) if positives > 0 else None
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
    best_loss = float("inf")
    patience = max(12, min(40, epochs // 4))
    stale_epochs = 0
    history: list[dict[str, Any]] = []
    for epoch in range(int(epochs)):
        model.train()
        optimizer.zero_grad()
        train_logits = model(x_train_tensor)
        train_loss = loss_fn(train_logits, y_train_tensor)
        train_loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            calibration_logits = model(x_calibration_tensor)
            calibration_loss = loss_fn(calibration_logits, y_calibration_tensor)
        train_loss_value = float(train_loss.detach().cpu().item())
        calibration_loss_value = float(calibration_loss.detach().cpu().item())
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": train_loss_value,
                "calibration_loss": calibration_loss_value,
            }
        )
        if calibration_loss_value + 1e-6 < best_loss:
            best_loss = calibration_loss_value
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
        if stale_epochs >= patience:
            break
    model.load_state_dict(best_state)
    return model, {
        "epochs_requested": int(epochs),
        "epochs_completed": int(len(history)),
        "best_calibration_loss": best_loss,
        "positive_train_rows": int(positives),
        "negative_train_rows": int(negatives),
        "pos_weight": float(pos_weight.item()) if pos_weight is not None else None,
        "loss_history_tail": history[-8:],
    }


def _predict_torch_mlp(model: Any, x_values: np.ndarray) -> np.ndarray:
    import torch

    model.eval()
    with torch.no_grad():
        logits = model(torch.tensor(x_values, dtype=torch.float32)).detach().cpu().numpy().reshape(-1)
    return 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))


def _top_per_game(frame: pd.DataFrame, *, score_column: str, subject_id: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work["shadow_variant"] = subject_id
    work["shadow_selected_flag"] = True
    work["sidecar_probability"] = pd.to_numeric(work[score_column], errors="coerce").fillna(0.0)
    work["selection_source"] = "neural_sidecar_shadow_top_per_game"
    work["shadow_reason"] = "top neural sidecar score per postseason game; shadow-only, no execution authority"
    sort_columns = ["game_id", score_column, "heuristic_rank_score", "signal_strength", "signal_id"]
    ascending = [True, False, False, False, True]
    available_sort_columns = [column for column in sort_columns if column in work.columns]
    available_ascending = [ascending[index] for index, column in enumerate(sort_columns) if column in work.columns]
    selected = (
        work.sort_values(available_sort_columns, ascending=available_ascending, kind="mergesort")
        .drop_duplicates(subset=["game_id"])
        .reset_index(drop=True)
    )
    selected["shadow_priority_rank"] = np.arange(1, len(selected) + 1)
    return selected


def _prediction_topline(frame: pd.DataFrame, *, score_column: str) -> dict[str, Any]:
    if frame.empty:
        return {"status": "insufficient_data"}
    selected = _top_per_game(frame, score_column=score_column, subject_id="topline")
    return {
        "status": "success",
        "holdout_rows": int(len(frame)),
        "top_game_count": int(len(selected)),
        "top_positive_rate": float(selected[RANKING_TARGET_COLUMN].astype(float).mean()) if not selected.empty else None,
        "top_executed_rate": float(selected[GATE_TARGET_COLUMN].astype(float).mean()) if not selected.empty else None,
        "top_mean_replay_value": float(pd.to_numeric(selected["label_replay_value"], errors="coerce").mean())
        if "label_replay_value" in selected.columns and not selected.empty
        else None,
    }


def _subject_submission_payload(
    subject: dict[str, Any],
    *,
    artifact_paths: dict[str, str],
    report_path: Path,
    model_summary_path: Path,
    feature_schema_path: Path,
    prediction_path: Path,
    shadow_payload_path: Path,
) -> dict[str, Any]:
    metrics = subject["metrics"]
    return {
        **subject,
        "live_observed_flag": False,
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
            "trace_json": str(model_summary_path),
            "feature_schema_json": str(feature_schema_path),
            "prediction_csv": str(prediction_path),
            "shadow_payload_csv": str(shadow_payload_path),
            "standard_trades_csv": artifact_paths.get("standard_csv"),
            "replay_trades_csv": artifact_paths.get("replay_csv"),
        },
    }


def _update_benchmark_submission(
    submission_path: Path,
    *,
    published_at: str,
    neural_subjects: list[dict[str, Any]],
    artifact_root: Path,
    report_path: Path,
    model_summary_path: Path,
) -> dict[str, Any]:
    payload = json.loads(submission_path.read_text(encoding="utf-8-sig")) if submission_path.exists() else {}
    payload.setdefault("lane_id", ML_LANE_ID)
    payload.setdefault("lane_label", ML_LANE_LABEL)
    payload.setdefault("lane_type", ML_LANE_TYPE)
    payload["published_at"] = published_at
    payload.setdefault("subjects", [])
    existing_subjects = [
        subject
        for subject in payload.get("subjects", [])
        if str(subject.get("candidate_id") or "") not in {RAW_SUBJECT_ID, CALIBRATED_SUBJECT_ID}
    ]
    payload["subjects"] = [*existing_subjects, *neural_subjects]
    neural_support = payload.setdefault("neural_shadow_support", {})
    neural_support.update(
        {
            "schema_version": NEURAL_SCHEMA_VERSION,
            "artifact_root": str(artifact_root),
            "report_markdown": str(report_path),
            "model_summary_json": str(model_summary_path),
            "promotion": "shadow_only",
            "live_routing_change": False,
            "order_placement_change": False,
        }
    )
    write_json(submission_path, payload)
    return payload


def _replace_markdown_section(path: Path, *, heading: str, content: str) -> None:
    existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    section = content.strip() + "\n"
    if heading not in existing:
        separator = "\n\n" if existing.strip() else ""
        path.write_text(existing.rstrip() + separator + section, encoding="utf-8")
        return
    before, after = existing.split(heading, 1)
    next_heading_index = after.find("\n## ")
    tail = after[next_heading_index:] if next_heading_index >= 0 else ""
    path.write_text(before.rstrip() + "\n\n" + section + tail.lstrip("\n"), encoding="utf-8")


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload["model_summary"]
    raw_metrics = summary["holdout_metrics"]["raw"]
    calibrated_metrics = summary["holdout_metrics"]["calibrated"]
    raw_subject = payload["subjects"][RAW_SUBJECT_ID]
    calibrated_subject = payload["subjects"][CALIBRATED_SUBJECT_ID]
    return "\n".join(
        [
            "# ML Neural Sidecar V1",
            "",
            f"- published_at: `{payload['published_at']}`",
            f"- source_artifact: `{payload['source_artifact_name']}`",
            f"- artifact_root: `{payload['artifact_root']}`",
            f"- training_rows: `{summary['split']['core_train_rows']}`",
            f"- calibration_rows: `{summary['split']['regular_calibration_rows']}`",
            f"- postseason_holdout_rows: `{summary['split']['holdout_rows']}`",
            "",
            "## Holdout Metrics",
            "",
            f"- raw: `auc={raw_metrics.get('auc')}`, `brier={raw_metrics.get('brier')}`, `log_loss={raw_metrics.get('log_loss')}`, `rows={raw_metrics.get('rows')}`",
            f"- calibrated: `auc={calibrated_metrics.get('auc')}`, `brier={calibrated_metrics.get('brier')}`, `log_loss={calibrated_metrics.get('log_loss')}`, `rows={calibrated_metrics.get('rows')}`",
            "",
            "## Shadow Selection",
            "",
            f"- raw top-per-game: standard `{raw_subject['metrics'].get('standard_trade_count')}`, replay `{raw_subject['metrics'].get('replay_trade_count')}`, bankroll `{raw_subject['metrics'].get('replay_ending_bankroll')}`",
            f"- calibrated top-per-game: standard `{calibrated_subject['metrics'].get('standard_trade_count')}`, replay `{calibrated_subject['metrics'].get('replay_trade_count')}`, bankroll `{calibrated_subject['metrics'].get('replay_ending_bankroll')}`",
            "",
            "## Recommendation",
            "",
            "- Keep `ml_neural_sidecar_v1` shadow-only. It is a model-complexity probe on the expanded replay labels, not a live routing, budget, sizing, or order-placement change.",
        ]
    ).strip() + "\n"


def _render_shared_section(payload: dict[str, Any]) -> str:
    summary = payload["model_summary"]
    raw_metrics = summary["holdout_metrics"]["raw"]
    calibrated_metrics = summary["holdout_metrics"]["calibrated"]
    calibrated_subject = payload["subjects"][CALIBRATED_SUBJECT_ID]
    return "\n".join(
        [
            "## Neural Sidecar V1",
            "",
            f"- Artifact: `{payload['artifact_root']}`",
            f"- Source dataset: `{payload['source_artifact_name']}`",
            f"- Split: regular core train `{summary['split']['core_train_rows']}`, regular calibration `{summary['split']['regular_calibration_rows']}`, postseason holdout `{summary['split']['holdout_rows']}`",
            f"- Raw holdout: `auc={raw_metrics.get('auc')}`, `brier={raw_metrics.get('brier')}`, `log_loss={raw_metrics.get('log_loss')}`",
            f"- Calibrated holdout: `auc={calibrated_metrics.get('auc')}`, `brier={calibrated_metrics.get('brier')}`, `log_loss={calibrated_metrics.get('log_loss')}`",
            f"- Calibrated top-per-game replay: standard `{calibrated_subject['metrics'].get('standard_trade_count')}`, replay `{calibrated_subject['metrics'].get('replay_trade_count')}`, bankroll `{calibrated_subject['metrics'].get('replay_ending_bankroll')}`",
            "- Decision: keep neural sidecar shadow-only; do not use it for hard skip, sizing, live routing, budget changes, or order placement.",
        ]
    ).strip() + "\n"


def run_neural_sidecar(request: NeuralSidecarRequest) -> dict[str, Any]:
    shared_root = _resolve_shared_root(request.shared_root)
    artifact_root = shared_root / "artifacts" / ML_OUTPUT_DIRNAME / request.season / request.artifact_name
    report_root = shared_root / "reports" / ML_OUTPUT_DIRNAME
    handoff_root = shared_root / "handoffs" / ML_OUTPUT_DIRNAME
    artifact_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    handoff_root.mkdir(parents=True, exist_ok=True)

    dataset_df = _load_source_dataset(shared_root, season=request.season, source_artifact_name=request.source_artifact_name)
    train_df = dataset_df[dataset_df["evaluation_slice"].astype(str) == request.train_slice_name].copy()
    holdout_df = dataset_df[dataset_df["evaluation_slice"].astype(str) == request.holdout_slice_name].copy()
    if train_df.empty or holdout_df.empty:
        raise ValueError("Neural sidecar requires non-empty training_history and postseason_holdout slices.")
    core_train_df, regular_calibration_df = _split_regular_train_calibration(
        train_df,
        calibration_date_fraction=request.calibration_date_fraction,
    )
    numeric_columns = [column for column in NEURAL_NUMERIC_COLUMNS if column in dataset_df.columns]
    categorical_columns = [column for column in NEURAL_CATEGORICAL_COLUMNS if column in dataset_df.columns]
    transform = _build_feature_transform(
        core_train_df,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
    )
    x_train = _transform_features(core_train_df, transform)
    x_regular_calibration = _transform_features(regular_calibration_df, transform)
    x_holdout = _transform_features(holdout_df, transform)
    y_train = core_train_df[RANKING_TARGET_COLUMN].astype(float).to_numpy()
    y_regular_calibration = regular_calibration_df[RANKING_TARGET_COLUMN].astype(float).to_numpy()
    model, training_summary = _train_torch_mlp(
        x_train,
        y_train,
        x_regular_calibration,
        y_regular_calibration,
        seed=request.seed,
        hidden_units=request.hidden_units,
        epochs=request.epochs,
        learning_rate=request.learning_rate,
        weight_decay=request.weight_decay,
    )

    regular_calibration_df = regular_calibration_df.copy()
    holdout_df = holdout_df.copy()
    regular_calibration_df["neural_raw_score"] = _predict_torch_mlp(model, x_regular_calibration)
    holdout_df["neural_raw_score"] = _predict_torch_mlp(model, x_holdout)
    calibrator = _fit_platt_scaler(
        regular_calibration_df,
        raw_score_column="neural_raw_score",
        target_column=RANKING_TARGET_COLUMN,
    )
    regular_calibration_df["neural_calibrated_score"] = _predict_platt_scaler(
        calibrator,
        regular_calibration_df,
        raw_score_column="neural_raw_score",
    )
    holdout_df["neural_calibrated_score"] = _predict_platt_scaler(
        calibrator,
        holdout_df,
        raw_score_column="neural_raw_score",
    )
    regular_calibration_df["model_slice"] = "regular_calibration"
    holdout_df["model_slice"] = "postseason_holdout"
    predictions_df = pd.concat([regular_calibration_df, holdout_df], ignore_index=True, sort=False)
    raw_selected_df = _top_per_game(holdout_df, score_column="neural_raw_score", subject_id=RAW_SUBJECT_ID)
    calibrated_selected_df = _top_per_game(
        holdout_df,
        score_column="neural_calibrated_score",
        subject_id=CALIBRATED_SUBJECT_ID,
    )

    raw_standard_df, raw_replay_df = _build_selected_trade_frames(raw_selected_df)
    calibrated_standard_df, calibrated_replay_df = _build_selected_trade_frames(calibrated_selected_df)
    raw_artifact_paths = _write_subject_trade_artifacts(
        artifact_root,
        subject_name=RAW_SUBJECT_ID,
        standard_df=raw_standard_df,
        replay_df=raw_replay_df,
    )
    calibrated_artifact_paths = _write_subject_trade_artifacts(
        artifact_root,
        subject_name=CALIBRATED_SUBJECT_ID,
        standard_df=calibrated_standard_df,
        replay_df=calibrated_replay_df,
    )
    raw_subject = _compute_subject_metrics(
        subject_name=RAW_SUBJECT_ID,
        candidate_kind="ml_strategy",
        selected_df=raw_selected_df,
        standard_df=raw_standard_df,
        replay_df=raw_replay_df,
        gate_threshold=None,
        extra_notes=[
            "Small PyTorch tabular MLP trained only on regular-season replay labels.",
            "Raw neural score; top one shadow candidate per postseason game.",
            "Shadow-only: no live routing, budget, sizing, or order-placement authority.",
        ],
    )
    calibrated_subject = _compute_subject_metrics(
        subject_name=CALIBRATED_SUBJECT_ID,
        candidate_kind="ml_strategy",
        selected_df=calibrated_selected_df,
        standard_df=calibrated_standard_df,
        replay_df=calibrated_replay_df,
        gate_threshold=None,
        extra_notes=[
            "Small PyTorch tabular MLP trained only on regular-season replay labels.",
            "Platt calibration fit only on regular-season calibration rows.",
            "Top one shadow candidate per postseason game.",
            "Shadow-only: no live routing, budget, sizing, or order-placement authority.",
        ],
    )

    predictions_path = artifact_root / "neural_predictions"
    raw_holdout_path = artifact_root / "neural_holdout_predictions_raw"
    calibrated_holdout_path = artifact_root / "neural_holdout_predictions_calibrated"
    raw_shadow_path = artifact_root / "shadow_payload_raw"
    calibrated_shadow_path = artifact_root / "shadow_payload_calibrated"
    prediction_artifacts = {
        "neural_predictions": write_frame(predictions_path, predictions_df),
        "neural_holdout_predictions_raw": write_frame(
            raw_holdout_path,
            holdout_df.sort_values(["game_date", "game_id", "neural_raw_score"], ascending=[True, True, False]),
        ),
        "neural_holdout_predictions_calibrated": write_frame(
            calibrated_holdout_path,
            holdout_df.sort_values(["game_date", "game_id", "neural_calibrated_score"], ascending=[True, True, False]),
        ),
        "shadow_payload_raw": write_frame(raw_shadow_path, raw_selected_df),
        "shadow_payload_calibrated": write_frame(calibrated_shadow_path, calibrated_selected_df),
    }
    feature_schema = {
        "schema_version": NEURAL_SCHEMA_VERSION,
        "target": RANKING_TARGET_COLUMN,
        "numeric_columns": numeric_columns,
        "categorical_columns": categorical_columns,
        "feature_names": ["intercept", *transform.feature_names],
        "leakage_prevention": [
            "MLP fitting uses only training_history rows from regular-season replay labels.",
            "Platt calibration uses only a date-held-out slice of regular-season rows.",
            "Rows marked postseason_holdout are predicted after training/calibration and are never used for fitting.",
            "Replay outcome columns and no_trade_reason are not model features.",
        ],
    }
    model_summary = {
        "schema_version": NEURAL_SCHEMA_VERSION,
        "source_artifact_name": request.source_artifact_name,
        "split": {
            "core_train_rows": int(len(core_train_df)),
            "regular_calibration_rows": int(len(regular_calibration_df)),
            "holdout_rows": int(len(holdout_df)),
            "core_train_positive_rate": float(y_train.mean()) if len(y_train) else None,
            "regular_calibration_positive_rate": float(y_regular_calibration.mean()) if len(y_regular_calibration) else None,
            "holdout_positive_rate": float(holdout_df[RANKING_TARGET_COLUMN].astype(float).mean()) if not holdout_df.empty else None,
        },
        "training": training_summary,
        "calibrator": {
            key: value
            for key, value in calibrator.items()
            if key != "coefficients"
        }
        | {
            "coefficients": to_jsonable(calibrator.get("coefficients").tolist())
            if hasattr(calibrator.get("coefficients"), "tolist")
            else to_jsonable(calibrator.get("coefficients"))
        },
        "holdout_metrics": {
            "raw": _evaluate_binary_predictions(
                holdout_df,
                score_column="neural_raw_score",
                target_column=RANKING_TARGET_COLUMN,
            ),
            "calibrated": _evaluate_binary_predictions(
                holdout_df,
                score_column="neural_calibrated_score",
                target_column=RANKING_TARGET_COLUMN,
            ),
        },
        "holdout_topline": {
            "raw": _prediction_topline(holdout_df, score_column="neural_raw_score"),
            "calibrated": _prediction_topline(holdout_df, score_column="neural_calibrated_score"),
        },
    }
    published_at = datetime.now(timezone.utc).isoformat()
    feature_schema_path = artifact_root / "feature_schema.json"
    model_summary_path = artifact_root / "model_summary.json"
    report_path = report_root / "neural_sidecar_report.md"
    prediction_csv = prediction_artifacts["neural_predictions"]["csv"]
    raw_shadow_csv = prediction_artifacts["shadow_payload_raw"]["csv"]
    calibrated_shadow_csv = prediction_artifacts["shadow_payload_calibrated"]["csv"]

    neural_subjects = {
        RAW_SUBJECT_ID: raw_subject,
        CALIBRATED_SUBJECT_ID: calibrated_subject,
    }
    payload = {
        "lane_id": ML_LANE_ID,
        "lane_label": ML_LANE_LABEL,
        "lane_type": ML_LANE_TYPE,
        "schema_version": NEURAL_SCHEMA_VERSION,
        "published_at": published_at,
        "season": request.season,
        "source_artifact_name": request.source_artifact_name,
        "artifact_root": str(artifact_root),
        "model_summary": model_summary,
        "artifacts": {
            "feature_schema_json": str(feature_schema_path),
            "model_summary_json": str(model_summary_path),
            "neural_predictions_csv": prediction_csv,
            "neural_holdout_predictions_raw_csv": prediction_artifacts["neural_holdout_predictions_raw"]["csv"],
            "neural_holdout_predictions_calibrated_csv": prediction_artifacts["neural_holdout_predictions_calibrated"]["csv"],
            "shadow_payload_raw_csv": raw_shadow_csv,
            "shadow_payload_calibrated_csv": calibrated_shadow_csv,
        },
        "subjects": neural_subjects,
        "recommendation": {
            "promotion": "shadow_only",
            "live_routing_change": False,
            "budget_change": False,
            "order_placement_change": False,
            "reason": "Small neural probe does not clear a promotion bar beyond shadow comparison.",
        },
    }
    report_markdown = _render_report(payload)
    write_json(feature_schema_path, feature_schema)
    write_json(model_summary_path, model_summary)
    write_markdown(report_path, report_markdown)

    raw_submission_subject = _subject_submission_payload(
        raw_subject,
        artifact_paths=raw_artifact_paths,
        report_path=report_path,
        model_summary_path=model_summary_path,
        feature_schema_path=feature_schema_path,
        prediction_path=Path(prediction_csv),
        shadow_payload_path=Path(raw_shadow_csv),
    )
    calibrated_submission_subject = _subject_submission_payload(
        calibrated_subject,
        artifact_paths=calibrated_artifact_paths,
        report_path=report_path,
        model_summary_path=model_summary_path,
        feature_schema_path=feature_schema_path,
        prediction_path=Path(prediction_csv),
        shadow_payload_path=Path(calibrated_shadow_csv),
    )
    submission_path = report_root / "benchmark_submission.json"
    benchmark_submission = _update_benchmark_submission(
        submission_path,
        published_at=published_at,
        neural_subjects=[raw_submission_subject, calibrated_submission_subject],
        artifact_root=artifact_root,
        report_path=report_path,
        model_summary_path=model_summary_path,
    )
    write_json(artifact_root / "benchmark_submission_neural_patch.json", {
        "subjects": [raw_submission_subject, calibrated_submission_subject],
        "updated_submission": str(submission_path),
    })

    shared_section = _render_shared_section(payload)
    _replace_markdown_section(report_root / "research_memo.md", heading="## Neural Sidecar V1", content=shared_section)
    _replace_markdown_section(handoff_root / "status.md", heading="## Neural Sidecar V1", content=shared_section)
    _replace_markdown_section(
        report_root / "daily_live_validation_handoff.md",
        heading="## Neural Sidecar V1",
        content="\n".join(
            [
                "## Neural Sidecar V1",
                "",
                f"- Artifact: `{artifact_root}`",
                "- Live handling: do not attach neural outputs to live routing or order placement; keep logging to the current ML shadow payloads.",
                "- Use: offline shadow comparison only until postseason stability improves.",
            ]
        ),
    )

    payload["benchmark_submission_subject_count"] = len(benchmark_submission.get("subjects", []))
    payload["reports"] = {
        "neural_sidecar_report_markdown": str(report_path),
        "benchmark_submission_json": str(submission_path),
        "research_memo_markdown": str(report_root / "research_memo.md"),
        "status_handoff_markdown": str(handoff_root / "status.md"),
    }
    write_json(artifact_root / "neural_sidecar_run.json", payload)
    return to_jsonable(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train and publish a small PyTorch neural ML sidecar in shadow-only mode."
    )
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--shared-root", default=None)
    parser.add_argument("--source-artifact-name", default=SOURCE_ARTIFACT_NAME)
    parser.add_argument("--artifact-name", default=NEURAL_ARTIFACT_NAME)
    parser.add_argument("--seed", type=int, default=20260504)
    parser.add_argument("--hidden-units", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--weight-decay", type=float, default=0.001)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run_neural_sidecar(
        NeuralSidecarRequest(
            season=args.season,
            shared_root=args.shared_root,
            source_artifact_name=args.source_artifact_name,
            artifact_name=args.artifact_name,
            seed=args.seed,
            hidden_units=args.hidden_units,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
        )
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
