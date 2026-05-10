from __future__ import annotations

import json

from codex_tool import evaluate_strategy_plan
from codex_tool._client import read_text


def test_strategy_plan_tool_reads_utf8_sig_json_files_pytest(tmp_path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"event_id": "nba-test"}), encoding="utf-8-sig")

    assert evaluate_strategy_plan._read_json(str(path)) == {"event_id": "nba-test"}
    assert read_text(str(path)) == json.dumps({"event_id": "nba-test"})


def test_strategy_plan_tool_loads_market_state_from_file_pytest(tmp_path) -> None:
    path = tmp_path / "market_state.json"
    path.write_text(json.dumps({"price": 0.31, "spread": 0.01}), encoding="utf-8-sig")

    assert evaluate_strategy_plan._loads_dict(None, str(path)) == {"price": 0.31, "spread": 0.01}
