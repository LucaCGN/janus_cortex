from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.agentic.contracts import (
    LiveSignal,
    LiveSignalFalsification,
    LiveSignalFreshness,
    LiveSignalPriceBand,
    LiveSignalRiskRequest,
)
from app.modules.agentic.store import write_live_signals


def test_live_signal_generates_stable_evidence_only_id_pytest() -> None:
    source_timestamp = datetime(2026, 5, 25, 0, 1, tzinfo=timezone.utc)
    first = LiveSignal(
        event_id="nba-okc-sas-2026-05-24",
        market_id="market-okc-sas",
        outcome_id="outcome-okc",
        market_token_id="token-okc",
        source="deterministic",
        signal_type="buy",
        side="Thunder",
        emitted_at_utc=datetime(2026, 5, 25, 0, 2, tzinfo=timezone.utc),
        price_band=LiveSignalPriceBand(current_price=0.22, lower_price=0.19, upper_price=0.24),
        confidence=0.71,
        confidence_source="scoreboard_clob_rule_v1",
        freshness=LiveSignalFreshness(source_timestamp_utc=source_timestamp, latency_ms=420, stale=False),
        reason_codes=["score_gap_inside_rule", "fresh_orderbook", "score_gap_inside_rule"],
        risk_request=LiveSignalRiskRequest(sleeve_id="okc-grid", requested_shares=5, max_price=0.24),
        falsification=LiveSignalFalsification(condition_codes=["score_gap_expands", "spread_too_wide"]),
        evidence_paths=["local/shared/artifacts/ops/sample.json"],
    )
    second = LiveSignal(
        event_id="nba-okc-sas-2026-05-24",
        market_id="market-okc-sas",
        outcome_id="outcome-okc",
        market_token_id="token-okc",
        source="deterministic",
        signal_type="buy",
        side="Thunder",
        emitted_at_utc=datetime.now(timezone.utc),
        price_band=LiveSignalPriceBand(current_price=0.22, lower_price=0.19, upper_price=0.24),
        confidence=0.71,
        confidence_source="scoreboard_clob_rule_v1",
        freshness=LiveSignalFreshness(source_timestamp_utc=source_timestamp, latency_ms=420, stale=False),
        reason_codes=["score_gap_inside_rule", "fresh_orderbook"],
        risk_request=LiveSignalRiskRequest(sleeve_id="okc-grid", requested_shares=5, max_price=0.24),
        falsification=LiveSignalFalsification(condition_codes=["score_gap_expands", "spread_too_wide"]),
        evidence_paths=["local/shared/artifacts/ops/sample.json"],
    )

    assert first.signal_id == second.signal_id
    assert first.signal_id and first.signal_id.startswith("lsig-")
    assert first.execution_boundary == "evidence_only"
    assert first.reason_codes == ["score_gap_inside_rule", "fresh_orderbook"]


def test_live_signal_rejects_invalid_price_band_and_confidence_pytest() -> None:
    with pytest.raises(ValidationError, match="lower_price cannot exceed upper_price"):
        LiveSignalPriceBand(lower_price=0.4, upper_price=0.3)

    with pytest.raises(ValidationError):
        LiveSignal(
            event_id="event-1",
            source="operator",
            signal_type="monitor",
            confidence=1.5,
        )


def test_live_signal_artifact_writer_records_json_and_jsonl_pytest(tmp_path) -> None:
    deterministic_signal = LiveSignal(
        event_id="wnba-dal-nyl-2026-05-24",
        market_id="market-dal-nyl",
        market_token_id="token-dal",
        source="deterministic",
        signal_type="block",
        side="Dallas",
        price_band=LiveSignalPriceBand(current_price=0.22, lower_price=0.03, upper_price=0.45),
        freshness=LiveSignalFreshness(stale=False, latency_ms=185),
        reason_codes=["orderbook_spread_too_wide"],
        evidence_paths=["local/shared/artifacts/ops/2026-05-24/live-monitor.json"],
    )
    codex_signal = LiveSignal(
        event_id="wnba-dal-nyl-2026-05-24",
        source="codex",
        signal_type="monitor",
        confidence=0.5,
        reason_codes=["controlled_entry_requires_next_pregame_validation"],
        payload={"issue": "#62", "task": "JIT-62-02"},
    )

    result = write_live_signals(
        [deterministic_signal, codex_signal],
        day="2026-05-25",
        root=tmp_path,
        source="pytest",
    )

    assert result["status"] == "stored"
    assert result["signal_count"] == 2
    for item in result["signals"]:
        payload = json.loads((tmp_path / "live-signals" / "2026-05-25" / item["event_id"] / f"{item['signal_id']}.json").read_text(encoding="utf-8"))
        assert payload["execution_boundary"] == "evidence_only"
        assert payload["signal_id"] == item["signal_id"]

    jsonl_path = tmp_path / "live-signals" / "2026-05-25" / "live_signals.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert [row["signal_id"] for row in rows] == [deterministic_signal.signal_id, codex_signal.signal_id]
    assert {row["execution_boundary"] for row in rows} == {"evidence_only"}
