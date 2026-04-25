from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.modules.nba.execution.contracts import LiveRunConfig, build_live_order_metadata
from app.modules.nba.execution.shadow import (
    SHADOW_SNAPSHOT_CSV_NAME,
    SHADOW_SNAPSHOT_JSON_NAME,
    build_live_shadow_snapshot,
)


def _build_state_row(
    *,
    game_id: str,
    team_side: str,
    state_index: int,
    event_at: datetime,
    opening_price: float,
    team_price: float,
    period_label: str,
    clock_elapsed_seconds: float,
    score_diff: int,
    net_points_last_5_events: float,
) -> dict[str, object]:
    return {
        "game_id": game_id,
        "team_side": team_side,
        "state_index": state_index,
        "team_id": 1,
        "team_slug": team_side.upper(),
        "opponent_team_id": 2,
        "opponent_team_slug": "OPP",
        "event_id": f"event-{game_id}",
        "market_id": f"market-{game_id}",
        "outcome_id": f"outcome-{game_id}-{team_side}",
        "season": "2025-26",
        "season_phase": "playoffs",
        "analysis_version": "v1_0_1",
        "computed_at": event_at,
        "game_date": event_at.date(),
        "event_index": state_index,
        "action_id": str(state_index),
        "event_at": event_at,
        "period": 1 if period_label == "Q1" else 2,
        "period_label": period_label,
        "clock": "PT10M00.00S",
        "clock_elapsed_seconds": clock_elapsed_seconds,
        "seconds_to_game_end": 1800.0,
        "score_for": 50 + score_diff,
        "score_against": 50,
        "score_diff": score_diff,
        "score_diff_bucket": "lead_1_4",
        "context_bucket": f"{period_label}|lead_1_4",
        "team_led_flag": score_diff > 0,
        "team_trailed_flag": score_diff < 0,
        "tied_flag": score_diff == 0,
        "market_favorite_flag": opening_price >= 0.5,
        "scoreboard_control_mismatch_flag": False,
        "final_winner_flag": True,
        "scoring_side": team_side,
        "points_scored": 2,
        "delta_for": 2,
        "delta_against": 0,
        "lead_changes_so_far": 0,
        "team_points_last_5_events": max(0.0, net_points_last_5_events),
        "opponent_points_last_5_events": 0,
        "net_points_last_5_events": net_points_last_5_events,
        "opening_price": opening_price,
        "opening_band": "40-50",
        "team_price": team_price,
        "price_delta_from_open": team_price - opening_price,
        "abs_price_delta_from_open": abs(team_price - opening_price),
        "price_mode": "tick",
        "gap_before_seconds": 5.0,
        "gap_after_seconds": 5.0,
        "mfe_from_state": 0.05,
        "mae_from_state": 0.02,
        "large_swing_next_12_states_flag": False,
        "crossed_50c_next_12_states_flag": False,
        "winner_stable_70_after_state_flag": False,
        "winner_stable_80_after_state_flag": False,
        "winner_stable_90_after_state_flag": False,
        "winner_stable_95_after_state_flag": False,
    }


def test_build_live_shadow_snapshot_marks_fresh_probe_and_persists_artifacts_pytest(tmp_path: Path) -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    state_df = pd.DataFrame(
        [
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=0,
                event_at=base,
                opening_price=0.45,
                team_price=0.45,
                period_label="Q1",
                clock_elapsed_seconds=0.0,
                score_diff=0,
                net_points_last_5_events=0.0,
            ),
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=1,
                event_at=base + timedelta(seconds=30),
                opening_price=0.45,
                team_price=0.51,
                period_label="Q1",
                clock_elapsed_seconds=180.0,
                score_diff=1,
                net_points_last_5_events=3.0,
            ),
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=2,
                event_at=base + timedelta(seconds=60),
                opening_price=0.45,
                team_price=0.57,
                period_label="Q1",
                clock_elapsed_seconds=240.0,
                score_diff=2,
                net_points_last_5_events=4.0,
            ),
        ]
    )
    snapshot = {
        "state_df": state_df,
        "bundles": {
            "G-Q1": {
                "game": {"away_team_slug": "AWY", "home_team_slug": "HME"},
                "live_orderbooks": {
                    "home": {
                        "best_bid": 0.56,
                        "best_ask": 0.57,
                        "spread_cents": 1.0,
                        "timestamp": (base + timedelta(seconds=60)).isoformat(),
                    }
                },
            }
        },
        "diagnostics_by_game": {"G-Q1": {"coverage_status": "covered_partial"}},
    }

    payload = build_live_shadow_snapshot(
        run_id="demo-run",
        run_root=tmp_path,
        snapshot=snapshot,
        controller_cards=[],
        game_ids=["G-Q1"],
        families=["quarter_open_reprice"],
        persist=True,
    )

    assert payload["summary"][0]["subject_name"] == "quarter_open_reprice"
    assert payload["summary"][0]["active_signal_count"] == 1
    assert payload["active_signals"][0]["shadow_action"] == "would_enter"
    assert payload["active_signals"][0]["shadow_reason"] == "eligible"
    assert (tmp_path / SHADOW_SNAPSHOT_JSON_NAME).exists()
    assert (tmp_path / SHADOW_SNAPSHOT_CSV_NAME).exists()


def test_build_live_shadow_snapshot_marks_stale_signal_pytest(tmp_path: Path) -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    state_df = pd.DataFrame(
        [
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=0,
                event_at=base,
                opening_price=0.45,
                team_price=0.45,
                period_label="Q1",
                clock_elapsed_seconds=0.0,
                score_diff=0,
                net_points_last_5_events=0.0,
            ),
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=1,
                event_at=base + timedelta(seconds=30),
                opening_price=0.45,
                team_price=0.51,
                period_label="Q1",
                clock_elapsed_seconds=180.0,
                score_diff=1,
                net_points_last_5_events=3.0,
            ),
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=2,
                event_at=base + timedelta(seconds=60),
                opening_price=0.45,
                team_price=0.57,
                period_label="Q1",
                clock_elapsed_seconds=240.0,
                score_diff=2,
                net_points_last_5_events=4.0,
            ),
            _build_state_row(
                game_id="G-Q1",
                team_side="home",
                state_index=3,
                event_at=base + timedelta(seconds=150),
                opening_price=0.45,
                team_price=0.58,
                period_label="Q1",
                clock_elapsed_seconds=330.0,
                score_diff=2,
                net_points_last_5_events=4.0,
            ),
        ]
    )
    snapshot = {
        "state_df": state_df,
        "bundles": {
            "G-Q1": {
                "game": {"away_team_slug": "AWY", "home_team_slug": "HME"},
                "live_orderbooks": {
                    "home": {
                        "best_bid": 0.57,
                        "best_ask": 0.58,
                        "spread_cents": 1.0,
                        "timestamp": (base + timedelta(seconds=150)).isoformat(),
                    }
                },
            }
        },
        "diagnostics_by_game": {"G-Q1": {"coverage_status": "covered_partial"}},
    }

    payload = build_live_shadow_snapshot(
        run_id="demo-run",
        run_root=tmp_path,
        snapshot=snapshot,
        controller_cards=[],
        game_ids=["G-Q1"],
        families=["quarter_open_reprice"],
        persist=False,
    )

    blocked = [row for row in payload["blocked_signals"] if row["signal_id"]]
    assert len(blocked) == 1
    assert blocked[0]["shadow_reason"] == "stale_signal"
    assert blocked[0]["shadow_action"] == "wait"


def test_build_live_order_metadata_sanitizes_pandas_timestamp_pytest() -> None:
    config = LiveRunConfig(run_id="live-demo")
    metadata = build_live_order_metadata(
        config=config,
        controller_name="controller_vnext_unified_v1 :: balanced",
        controller_source="primary",
        game_id="0042500173",
        market_id="market-demo",
        outcome_id="outcome-demo",
        strategy_family="inversion",
        signal_id="inversion|0042500173|away|80",
        signal_price=0.47,
        signal_timestamp=pd.Timestamp("2026-04-25T01:02:03Z"),
        entry_reason="live_signal",
        stop_price=0.42,
        order_policy="limit_best_ask",
        extra={"entry_metadata": {"captured_at": pd.Timestamp("2026-04-25T01:02:05Z")}},
    )

    assert metadata["signal_timestamp"] == "2026-04-25T01:02:03+00:00"
    assert metadata["entry_metadata"]["captured_at"] == "2026-04-25T01:02:05+00:00"
