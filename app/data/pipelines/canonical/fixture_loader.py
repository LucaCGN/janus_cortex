from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def resolve_fixture_path(name: str) -> Path:
    base = name if name.endswith(".json") else f"{name}.json"
    path = FIXTURES_DIR / base
    if not path.exists():
        raise FileNotFoundError(f"fixture not found: {path}")
    return path


def load_json_fixture(name: str) -> Any:
    path = resolve_fixture_path(name)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_dataframe_fixture(name: str) -> pd.DataFrame:
    payload = load_json_fixture(name)
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return pd.DataFrame(rows)
    return pd.DataFrame()


def load_default_mapping_fixtures() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        load_dataframe_fixture("gamma_nba_events_fixture"),
        load_dataframe_fixture("gamma_nba_moneyline_fixture"),
        load_dataframe_fixture("nba_schedule_fixture"),
    )
