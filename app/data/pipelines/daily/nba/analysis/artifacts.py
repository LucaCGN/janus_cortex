from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.contracts import DEFAULT_OUTPUT_ROOT


def ensure_output_dir(root: str | None, season: str, season_phase: str, analysis_version: str) -> Path:
    base = Path(root) if root else DEFAULT_OUTPUT_ROOT
    target = base / season / season_phase / analysis_version
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def write_markdown(path: Path, body: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return str(path)


def write_frame(path_without_suffix: Path, frame: pd.DataFrame) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    csv_path = path_without_suffix.with_suffix(".csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(csv_path, index=False)
    artifacts["csv"] = str(csv_path)
    try:
        parquet_path = path_without_suffix.with_suffix(".parquet")
        frame.to_parquet(parquet_path, index=False)
        artifacts["parquet"] = str(parquet_path)
    except Exception:
        pass
    return artifacts
