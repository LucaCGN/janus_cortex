from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.modules.nba.execution.contracts import LiveRunConfig, build_live_order_metadata
from app.modules.nba.execution.runner import (
    _controller_probe_family,
    _controller_source_name,
    _apply_live_orderbook_to_latest_state_rows,
    _detect_live_feed_stall,
    _live_event_slug_candidates,
    _live_series_event_slug_candidates,
    _select_live_trade_and_decision_frames,
    _select_live_event_link_candidate,
)
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


def test_probe_family_controller_uses_probe_frames_pytest() -> None:
    probe_trades = pd.DataFrame([{"game_id": "G1", "strategy_family": "quarter_open_reprice"}])
    probe_decisions = pd.DataFrame([{"game_id": "G1", "selected_core_family": "quarter_open_reprice"}])
    unified_trades = pd.DataFrame([{"game_id": "G2", "strategy_family": "winner_definition"}])
    deterministic_trades = pd.DataFrame([{"game_id": "G3", "strategy_family": "winner_definition"}])
    snapshot = {
        "probe_trades": probe_trades,
        "probe_decisions": probe_decisions,
        "unified_trades": unified_trades,
        "unified_decisions": pd.DataFrame([{"game_id": "G2"}]),
        "deterministic_trades": deterministic_trades,
        "deterministic_decisions": pd.DataFrame([{"game_id": "G3"}]),
    }

    selected_trades, selected_decisions = _select_live_trade_and_decision_frames(
        snapshot,
        controller_name="quarter_open_reprice",
    )

    assert _controller_probe_family("quarter_open_reprice") == "quarter_open_reprice"
    assert _controller_source_name("quarter_open_reprice") == "probe_family"
    assert selected_trades.equals(probe_trades)
    assert selected_decisions.equals(probe_decisions)


def test_apply_live_orderbook_updates_latest_state_price_only_pytest() -> None:
    base = datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc)
    rows = [
        _build_state_row(
            game_id="G-LIVE",
            team_side="away",
            state_index=0,
            event_at=base,
            opening_price=0.24,
            team_price=0.16,
            period_label="Q2",
            clock_elapsed_seconds=720.0,
            score_diff=-12,
            net_points_last_5_events=0,
        ),
        _build_state_row(
            game_id="G-LIVE",
            team_side="away",
            state_index=1,
            event_at=base + timedelta(seconds=20),
            opening_price=0.24,
            team_price=0.16,
            period_label="Q2",
            clock_elapsed_seconds=740.0,
            score_diff=-10,
            net_points_last_5_events=2,
        ),
    ]

    _apply_live_orderbook_to_latest_state_rows(
        rows,
        {
            "away": {
                "best_bid": 0.11,
                "best_ask": 0.12,
                "spread_cents": 1.0,
                "timestamp": (base + timedelta(seconds=30)).isoformat(),
            }
        },
    )

    assert rows[0]["team_price"] == 0.16
    assert rows[1]["team_price"] == 0.115
    assert rows[1]["price_mode"] == "live_orderbook_mid"
    assert rows[1]["live_best_bid"] == 0.11
    assert rows[1]["live_best_ask"] == 0.12
    assert rows[1]["price_delta_from_open"] == pytest.approx(-0.125)


def test_probe_family_controller_defaults_to_empty_frames_when_probe_keys_missing_pytest() -> None:
    selected_trades, selected_decisions = _select_live_trade_and_decision_frames(
        {
            "unified_trades": pd.DataFrame([{"game_id": "G2"}]),
            "unified_decisions": pd.DataFrame([{"game_id": "G2"}]),
        },
        controller_name="quarter_open_reprice",
    )

    assert selected_trades.empty
    assert selected_decisions.empty


def test_live_event_slug_candidates_include_both_team_orders_pytest() -> None:
    slugs = _live_event_slug_candidates(
        {
            "game_date": "2026-05-02",
            "away_team_slug": "MIN",
            "home_team_slug": "DEN",
        }
    )

    assert slugs == [
        "nba-min-den-2026-05-02",
        "nba-den-min-2026-05-02",
    ]


def test_live_series_event_slug_candidates_use_team_names_pytest() -> None:
    slugs = _live_series_event_slug_candidates(
        {
            "away_team_name": "Rockets",
            "home_team_name": "Lakers",
        }
    )

    assert slugs == [
        "nba-playoffs-who-will-win-series-rockets-vs-lakers",
        "nba-playoffs-who-will-win-series-lakers-vs-rockets",
    ]


def test_select_live_event_link_candidate_rejects_stale_series_fallback_pytest() -> None:
    candidate = _select_live_event_link_candidate(
        [
            {
                "event_id": "older-closed",
                "canonical_slug": "nba-min-den-2026-04-20",
                "status": "closed",
                "start_time": "2026-04-15T22:10:48+00:00",
            },
            {
                "event_id": "series-open",
                "canonical_slug": "nba-den-min-2026-04-25",
                "status": "open",
                "start_time": "2026-04-19T14:08:14+00:00",
            },
        ],
        exact_slugs=["nba-min-den-2026-05-02", "nba-den-min-2026-05-02"],
    )

    assert candidate is None


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


def test_detect_live_feed_stall_marks_active_clock_freeze_pytest() -> None:
    observed_at = datetime(2026, 4, 28, 4, 25, tzinfo=timezone.utc)
    last_event_at = observed_at - timedelta(minutes=11)

    diagnostics = _detect_live_feed_stall(
        {
            "game": {"game_status": 2, "game_status_text": "Q2 4:43"},
            "play_by_play": {
                "summary": {"last_event_at": last_event_at.isoformat()},
                "items": [{"time_actual": last_event_at.isoformat()}],
            },
        },
        per_game_state_rows=[],
        observed_at=observed_at,
    )

    assert diagnostics["feed_stalled_flag"] is True
    assert diagnostics["feed_stall_age_seconds"] == 660.0
    assert diagnostics["last_live_event_at"] == last_event_at.isoformat()


def test_detect_live_feed_stall_ignores_halftime_gap_pytest() -> None:
    observed_at = datetime(2026, 4, 28, 4, 25, tzinfo=timezone.utc)
    last_event_at = observed_at - timedelta(minutes=11)

    diagnostics = _detect_live_feed_stall(
        {
            "game": {"game_status": 2, "game_status_text": "Half"},
            "play_by_play": {
                "summary": {"last_event_at": last_event_at.isoformat()},
                "items": [{"time_actual": last_event_at.isoformat()}],
            },
        },
        per_game_state_rows=[],
        observed_at=observed_at,
    )

    assert diagnostics["feed_stalled_flag"] is False
    assert diagnostics["feed_stall_age_seconds"] == 660.0
