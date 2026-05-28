from __future__ import annotations

from app.modules.agentic.pbp_annotation import build_pbp_annotation_evidence


def test_pbp_annotation_detects_score_run_without_emitting_trade_trigger_pytest() -> None:
    evidence = build_pbp_annotation_evidence(
        event_id="nba-test",
        live_state={
            "latest_snapshot": {
                "period": 3,
                "clock": "PT06M10.00S",
                "home_score": 72,
                "away_score": 47,
            },
            "live_snapshots": [
                {
                    "period": 3,
                    "clock": "PT06M10.00S",
                    "home_score": 72,
                    "away_score": 47,
                },
                {
                    "period": 3,
                    "clock": "PT08M40.00S",
                    "home_score": 62,
                    "away_score": 45,
                },
            ],
        },
        plan={"active_strategies": [{"sleeve_role": "grid_scalp"}, {"sleeve_role": "core_hold"}]},
        source="pytest",
    )

    assert evidence["schema_version"] == "pbp_annotation_evidence_v1"
    assert evidence["execution_boundary"] == "evidence_only"
    assert evidence["must_not_place_orders"] is True
    assert evidence["emit_trigger"] is False
    assert "grid_scalp" in evidence["plan_sleeve_roles"]
    assert any(tag["tag_type"] == "score_run" for tag in evidence["tags"])
    assert all(signal["emit_trigger"] is False for signal in evidence["signals"])


def test_pbp_annotation_marks_late_game_uncertainty_pytest() -> None:
    evidence = build_pbp_annotation_evidence(
        event_id="nba-test",
        live_state={
            "latest_snapshot": {
                "period": 4,
                "clock": "PT04M22.00S",
                "home_score": 99,
                "away_score": 96,
            },
            "recent_play_by_play": [{"description": "OKC 3PT Jump Shot"}],
        },
        source="pytest",
    )

    late_tags = [tag for tag in evidence["tags"] if tag["tag_type"] == "late_game_uncertainty"]
    assert late_tags
    assert late_tags[0]["severity"] == "elevated"
    assert "reduce_stop" in late_tags[0]["sleeve_relevance"]


def test_pbp_annotation_detects_player_status_text_watch_pytest() -> None:
    evidence = build_pbp_annotation_evidence(
        event_id="nba-test",
        live_state={
            "latest_snapshot": {
                "period": 2,
                "clock": "PT09M00.00S",
                "home_score": 35,
                "away_score": 31,
            },
            "recent_play_by_play": [
                {"description": "Player substitution"},
                {"payload_json": {"description": "Defensive foul on SAS"}},
            ],
        },
        source="pytest",
    )

    assert any(tag["tag_type"] == "player_status_text_watch" for tag in evidence["tags"])
    assert evidence["recommended_escalation"] == "mini_review_if_strategy_revision_triggered"


def test_pbp_annotation_handles_missing_live_rows_as_evidence_only_status_pytest() -> None:
    evidence = build_pbp_annotation_evidence(event_id="nba-test", live_state={}, source="pytest")

    assert evidence["status"] == "no_live_pbp_or_scoreboard_rows"
    assert evidence["tag_count"] == 0
    assert evidence["signals"] == []


def test_pbp_annotation_can_dispatch_nano_and_emit_review_escalation_pytest() -> None:
    def dispatcher(payload: dict) -> dict:
        assert payload["model"] == "gpt-5.4-nano"
        assert payload["must_not_place_orders"] is True
        return {
            "summary": "A bench shock and quick run make the underdog price worth review.",
            "llm_escalation": "mini_review_if_strategy_revision_triggered",
            "valuation_signal": "undervaluation",
            "tags": [
                {
                    "tag_type": "nano_underdog_context",
                    "severity": "elevated",
                    "confidence": 0.71,
                    "sleeve_relevance": ["grid_scalp", "ultra_low_rebound"],
                    "reason": "Nano saw a fresh PBP context shift.",
                    "evidence": {"source": "pytest"},
                }
            ],
        }

    evidence = build_pbp_annotation_evidence(
        event_id="wnba-test",
        live_state={
            "latest_snapshot": {"period": 3, "clock": "PT07M00.00S", "home_score": 60, "away_score": 55},
            "recent_play_by_play": [{"description": "Substitution and 3PT shot"}],
        },
        source="pytest",
        nano_dispatcher=dispatcher,
        enable_nano_dispatch=True,
        allow_llm_escalation_triggers=True,
    )

    assert evidence["model_tier"] == "nano"
    assert evidence["nano_dispatch"]["status"] == "response_recorded"
    assert evidence["recommended_escalation"] == "mini_review_if_strategy_revision_triggered"
    assert any(signal["emit_trigger"] is True for signal in evidence["signals"])
    assert all(signal["trigger_type"] in {"compression_or_tagging", "undervaluation"} for signal in evidence["signals"])
