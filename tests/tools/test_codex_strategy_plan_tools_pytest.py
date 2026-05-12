from __future__ import annotations

import json
from argparse import Namespace

from codex_tool import adopt_llm_revision
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


def test_adopt_llm_revision_tool_builds_candidate_payload_from_trace_pytest() -> None:
    payload = adopt_llm_revision._build_payload(
        Namespace(
            session_date="2026-05-12",
            source="pytest",
            reviewed_by="reviewer",
            review_reason="valid recorded response",
            notes=None,
            response_path=None,
            trace_artifact_path="C:\\trace.json",
            apply_current=False,
        )
    )

    assert payload == {
        "session_date": "2026-05-12",
        "source": "pytest",
        "reviewed_by": "reviewer",
        "review_reason": "valid recorded response",
        "trace_artifact_path": "C:\\trace.json",
        "apply_current": False,
    }


def test_adopt_llm_revision_tool_reads_response_payload_pytest(tmp_path) -> None:
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps({"request_id": "request-1"}), encoding="utf-8-sig")

    payload = adopt_llm_revision._build_payload(
        Namespace(
            session_date=None,
            source="pytest",
            reviewed_by="reviewer",
            review_reason="valid recorded response",
            notes="candidate only",
            response_path=str(response_path),
            trace_artifact_path=None,
            apply_current=True,
        )
    )

    assert payload["response"] == {"request_id": "request-1"}
    assert payload["apply_current"] is True
    assert payload["notes"] == "candidate only"
