from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.data.pipelines.daily.nba.analysis import mart_state_panel as mod


def _build_item(
    *,
    event_index: int,
    event_at: datetime,
    period: int,
    period_label: str,
    clock_elapsed_seconds: float,
    home_score: int,
    away_score: int,
    delta_home: int,
    delta_away: int,
    home_price: float,
) -> dict[str, object]:
    return {
        "event_index": event_index,
        "action_id": str(event_index),
        "time_actual": event_at.isoformat(),
        "period": period,
        "period_label": period_label,
        "clock": f"PT{max(0, 720 - int(clock_elapsed_seconds))}S",
        "clock_elapsed_seconds": clock_elapsed_seconds,
        "home_score": home_score,
        "away_score": away_score,
        "delta_home": delta_home,
        "delta_away": delta_away,
        "scoring_side": "home" if delta_home > delta_away else "away" if delta_away > delta_home else None,
        "points_scored": max(delta_home, delta_away),
        "market_points": {
            "home-outcome": {
                "price": home_price,
                "mode": "tick",
                "gap_before_seconds": 5.0,
                "gap_after_seconds": 7.0,
            }
        },
    }


def _build_state_rows(timed_items: list[dict[str, object]], *, final_winner_flag: bool = True) -> list[dict[str, object]]:
    return mod.build_state_rows_for_side(
        game={"game_id": "002TESTA3", "game_date": "2026-02-22"},
        timed_items=timed_items,
        team_side="home",
        team_id=1610612738,
        team_slug="BOS",
        opponent_team_id=1610612747,
        opponent_team_slug="LAL",
        outcome_id="home-outcome",
        event_id="event-1",
        market_id="market-1",
        opening_price=0.60,
        opening_band="60-70",
        final_winner_flag=final_winner_flag,
        season="2025-26",
        season_phase="regular_season",
        analysis_version="v1_0_1",
        computed_at=datetime(2026, 2, 22, 23, 0, tzinfo=timezone.utc),
    )


def test_build_state_rows_for_side_sorts_and_keeps_context_deterministic() -> None:
    base = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    timed_items = [
        _build_item(
            event_index=3,
            event_at=base + timedelta(minutes=3),
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=180.0,
            home_score=5,
            away_score=2,
            delta_home=2,
            delta_away=0,
            home_price=0.45,
        ),
        _build_item(
            event_index=1,
            event_at=base + timedelta(minutes=1),
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=60.0,
            home_score=0,
            away_score=2,
            delta_home=0,
            delta_away=2,
            home_price=0.40,
        ),
        _build_item(
            event_index=2,
            event_at=base + timedelta(minutes=2),
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=120.0,
            home_score=3,
            away_score=2,
            delta_home=3,
            delta_away=0,
            home_price=0.72,
        ),
    ]

    rows = _build_state_rows(timed_items)

    assert [row["state_index"] for row in rows] == [0, 1, 2]
    assert [row["event_index"] for row in rows] == [1, 2, 3]
    assert [row["event_at"] for row in rows] == sorted(row["event_at"] for row in rows)
    assert [row["lead_changes_so_far"] for row in rows] == [0, 1, 1]
    assert rows[0]["score_diff_bucket"] == "trail_1_4"
    assert rows[1]["context_bucket"] == "Q1|lead_1_4"
    assert rows[2]["scoreboard_control_mismatch_flag"] is True
    assert rows[2]["team_points_last_5_events"] == 5
    assert rows[2]["opponent_points_last_5_events"] == 2
    assert rows[2]["net_points_last_5_events"] == 3
    assert rows[2]["winner_stable_70_after_state_flag"] is False
    assert rows[2]["mfe_from_state"] == 0.0
    assert rows[0]["mae_from_state"] == 0.0


def test_build_state_rows_for_side_breaks_same_timestamp_ties_deterministically() -> None:
    base = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    shared_event_at = base + timedelta(minutes=2)
    timed_items = [
        _build_item(
            event_index=3,
            event_at=shared_event_at,
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=120.0,
            home_score=5,
            away_score=2,
            delta_home=2,
            delta_away=0,
            home_price=0.75,
        ),
        _build_item(
            event_index=1,
            event_at=shared_event_at,
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=120.0,
            home_score=1,
            away_score=2,
            delta_home=1,
            delta_away=0,
            home_price=0.55,
        ),
        _build_item(
            event_index=2,
            event_at=shared_event_at,
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=120.0,
            home_score=3,
            away_score=2,
            delta_home=2,
            delta_away=0,
            home_price=0.65,
        ),
    ]

    rows = _build_state_rows(timed_items)

    assert [row["state_index"] for row in rows] == [0, 1, 2]
    assert [row["event_index"] for row in rows] == [1, 2, 3]
    assert all(row["event_at"] == shared_event_at for row in rows)


def test_build_state_rows_for_side_does_not_leak_lookahead_beyond_horizon() -> None:
    base = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    timed_items: list[dict[str, object]] = []
    for index in range(14):
        home_price = 0.60 if index < 13 else 0.49
        timed_items.append(
            _build_item(
                event_index=index + 1,
                event_at=base + timedelta(seconds=index * 30),
                period=1,
                period_label="Q1",
                clock_elapsed_seconds=float(index * 30),
                home_score=index,
                away_score=max(index - 1, 0),
                delta_home=1,
                delta_away=0,
                home_price=home_price,
            )
        )

    rows = _build_state_rows(timed_items)

    assert rows[0]["large_swing_next_12_states_flag"] is False
    assert rows[0]["crossed_50c_next_12_states_flag"] is False
    assert rows[1]["large_swing_next_12_states_flag"] is True
    assert rows[1]["crossed_50c_next_12_states_flag"] is True
    assert rows[-1]["large_swing_next_12_states_flag"] is False
    assert rows[-1]["crossed_50c_next_12_states_flag"] is False


def test_build_winner_definition_profile_rows_reconciles_with_state_flags() -> None:
    base = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    timed_items = [
        _build_item(
            event_index=1,
            event_at=base + timedelta(minutes=1),
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=60.0,
            home_score=0,
            away_score=2,
            delta_home=0,
            delta_away=2,
            home_price=0.68,
        ),
        _build_item(
            event_index=2,
            event_at=base + timedelta(minutes=2),
            period=1,
            period_label="Q1",
            clock_elapsed_seconds=120.0,
            home_score=3,
            away_score=2,
            delta_home=3,
            delta_away=0,
            home_price=0.72,
        ),
        _build_item(
            event_index=3,
            event_at=base + timedelta(minutes=3),
            period=2,
            period_label="Q2",
            clock_elapsed_seconds=780.0,
            home_score=5,
            away_score=2,
            delta_home=2,
            delta_away=0,
            home_price=0.83,
        ),
        _build_item(
            event_index=4,
            event_at=base + timedelta(minutes=4),
            period=4,
            period_label="Q4",
            clock_elapsed_seconds=2760.0,
            home_score=7,
            away_score=4,
            delta_home=2,
            delta_away=2,
            home_price=0.91,
        ),
        _build_item(
            event_index=5,
            event_at=base + timedelta(minutes=5),
            period=4,
            period_label="Q4",
            clock_elapsed_seconds=2879.0,
            home_score=8,
            away_score=4,
            delta_home=1,
            delta_away=0,
            home_price=0.96,
        ),
    ]

    winner_rows = _build_state_rows(timed_items, final_winner_flag=True)
    loser_rows = _build_state_rows(timed_items, final_winner_flag=False)
    state_df = pd.DataFrame([*winner_rows, *loser_rows])

    profiles = mod.build_winner_definition_profile_rows(
        state_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version="v1_0_1",
        computed_at=datetime(2026, 2, 22, 23, 0, tzinfo=timezone.utc),
    )

    assert profiles
    for profile in profiles:
        threshold = int(profile["threshold_cents"])
        threshold_df = state_df[
            (state_df["final_winner_flag"] == True)
            & (state_df["team_price"] >= threshold / 100.0)
            & (state_df["context_bucket"] == profile["context_bucket"])
        ]
        stable_column = f"winner_stable_{threshold}_after_state_flag"
        assert profile["sample_states"] == len(threshold_df)
        assert profile["distinct_games"] == threshold_df["game_id"].nunique()
        assert profile["stable_states"] == int(threshold_df[stable_column].fillna(False).sum())
        assert profile["reopen_rate"] == 1.0 - profile["stable_rate"]
