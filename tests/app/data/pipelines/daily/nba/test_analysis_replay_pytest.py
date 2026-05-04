from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.halftime_gap_fill import simulate_halftime_gap_fill_trades
from app.data.pipelines.daily.nba.analysis.backtests.historical_bidask import build_historical_bidask_samples
from app.data.pipelines.daily.nba.analysis.backtests.lead_fragility import simulate_lead_fragility_trades
from app.data.pipelines.daily.nba.analysis.backtests.micro_momentum_continuation import (
    simulate_micro_momentum_continuation_trades,
)
from app.data.pipelines.daily.nba.analysis.backtests.panic_fade_fast import simulate_panic_fade_fast_trades
from app.data.pipelines.daily.nba.analysis.backtests.quarter_open_reprice import simulate_quarter_open_reprice_trades
from app.data.pipelines.daily.nba.analysis.backtests.registry import REPLAY_HF_STRATEGY_GROUP, build_strategy_registry
from app.data.pipelines.daily.nba.analysis.backtests.replay import (
    ReplayGameContext,
    ReplaySubject,
    build_replay_slate_expectation_frame,
    simulate_replay_trade_frames,
)
from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, ReplayRunRequest


def _build_state_row(
    *,
    game_id: str,
    state_index: int,
    event_at: datetime,
    opening_price: float,
    team_price: float,
    period_label: str,
    clock_elapsed_seconds: float = 120.0,
    seconds_to_game_end: float = 1800.0,
    score_diff: int = 2,
    net_points_last_5_events: float = 4.0,
    lead_changes_so_far: int = 0,
) -> dict[str, object]:
    return {
        "game_id": game_id,
        "team_side": "home",
        "state_index": state_index,
        "team_id": 1,
        "team_slug": "HOME",
        "opponent_team_id": 2,
        "opponent_team_slug": "AWAY",
        "event_id": f"event-{game_id}",
        "market_id": f"market-{game_id}",
        "outcome_id": f"outcome-{game_id}",
        "season": "2025-26",
        "season_phase": "playoffs",
        "analysis_version": ANALYSIS_VERSION,
        "computed_at": event_at,
        "game_date": event_at.date(),
        "event_index": state_index,
        "action_id": str(state_index),
        "event_at": event_at,
        "period": 1 if period_label == "Q1" else 3 if period_label == "Q3" else 4 if period_label == "Q4" else 2,
        "period_label": period_label,
        "clock": "PT10M00.00S",
        "clock_elapsed_seconds": clock_elapsed_seconds,
        "seconds_to_game_end": seconds_to_game_end,
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
        "scoring_side": "home",
        "points_scored": 2,
        "delta_for": 2,
        "delta_against": 0,
        "lead_changes_so_far": lead_changes_so_far,
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


def test_replay_registry_group_keeps_default_contract_stable() -> None:
    default_registry = build_strategy_registry()
    replay_registry = build_strategy_registry(strategy_group=REPLAY_HF_STRATEGY_GROUP)

    assert "micro_momentum_continuation" not in default_registry
    assert "panic_fade_fast" not in default_registry
    assert "micro_momentum_continuation" in replay_registry
    assert "panic_fade_fast" in replay_registry
    assert "lead_fragility" in replay_registry


def test_new_higher_frequency_strategy_hooks_produce_candidate_trades() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    micro_frame = pd.DataFrame(
        [
            _build_state_row(game_id="G-MICRO", state_index=0, event_at=base, opening_price=0.45, team_price=0.45, period_label="Q1", score_diff=0, net_points_last_5_events=0.0),
            _build_state_row(game_id="G-MICRO", state_index=1, event_at=base + timedelta(seconds=30), opening_price=0.45, team_price=0.50, period_label="Q1", score_diff=1, net_points_last_5_events=2.0),
            _build_state_row(game_id="G-MICRO", state_index=2, event_at=base + timedelta(seconds=60), opening_price=0.45, team_price=0.56, period_label="Q1", score_diff=3, net_points_last_5_events=5.0),
            _build_state_row(game_id="G-MICRO", state_index=3, event_at=base + timedelta(seconds=90), opening_price=0.45, team_price=0.61, period_label="Q1", score_diff=4, net_points_last_5_events=6.0),
        ]
    )
    panic_frame = pd.DataFrame(
        [
            _build_state_row(game_id="G-PANIC", state_index=0, event_at=base, opening_price=0.70, team_price=0.70, period_label="Q2", score_diff=6, net_points_last_5_events=0.0),
            _build_state_row(game_id="G-PANIC", state_index=1, event_at=base + timedelta(seconds=30), opening_price=0.70, team_price=0.46, period_label="Q2", score_diff=-2, net_points_last_5_events=1.0),
            _build_state_row(game_id="G-PANIC", state_index=2, event_at=base + timedelta(seconds=60), opening_price=0.70, team_price=0.54, period_label="Q2", score_diff=0, net_points_last_5_events=3.0),
            _build_state_row(game_id="G-PANIC", state_index=3, event_at=base + timedelta(seconds=90), opening_price=0.70, team_price=0.58, period_label="Q2", score_diff=1, net_points_last_5_events=2.0),
        ]
    )
    q1_frame = pd.DataFrame(
        [
            _build_state_row(game_id="G-Q1", state_index=0, event_at=base, opening_price=0.45, team_price=0.45, period_label="Q1", clock_elapsed_seconds=0.0, score_diff=0, net_points_last_5_events=0.0),
            _build_state_row(game_id="G-Q1", state_index=1, event_at=base + timedelta(seconds=30), opening_price=0.45, team_price=0.51, period_label="Q1", clock_elapsed_seconds=180.0, score_diff=1, net_points_last_5_events=3.0),
            _build_state_row(game_id="G-Q1", state_index=2, event_at=base + timedelta(seconds=60), opening_price=0.45, team_price=0.57, period_label="Q1", clock_elapsed_seconds=240.0, score_diff=2, net_points_last_5_events=4.0),
        ]
    )
    halftime_frame = pd.DataFrame(
        [
            _build_state_row(game_id="G-HALF", state_index=0, event_at=base, opening_price=0.48, team_price=0.48, period_label="Q2", clock_elapsed_seconds=1410.0, seconds_to_game_end=1770.0, score_diff=0, net_points_last_5_events=0.0),
            _build_state_row(game_id="G-HALF", state_index=1, event_at=base + timedelta(seconds=30), opening_price=0.48, team_price=0.48, period_label="Q3", clock_elapsed_seconds=1440.0, seconds_to_game_end=1740.0, score_diff=0, net_points_last_5_events=0.0),
            _build_state_row(game_id="G-HALF", state_index=2, event_at=base + timedelta(seconds=60), opening_price=0.48, team_price=0.53, period_label="Q3", clock_elapsed_seconds=1470.0, seconds_to_game_end=1710.0, score_diff=1, net_points_last_5_events=3.0),
            _build_state_row(game_id="G-HALF", state_index=3, event_at=base + timedelta(seconds=90), opening_price=0.48, team_price=0.59, period_label="Q3", clock_elapsed_seconds=1500.0, seconds_to_game_end=1680.0, score_diff=2, net_points_last_5_events=4.0),
        ]
    )
    fragility_frame = pd.DataFrame(
        [
            _build_state_row(game_id="G-FRAG", state_index=0, event_at=base, opening_price=0.40, team_price=0.40, period_label="Q3", score_diff=-4, net_points_last_5_events=0.0, lead_changes_so_far=1),
            _build_state_row(game_id="G-FRAG", state_index=1, event_at=base + timedelta(seconds=30), opening_price=0.40, team_price=0.39, period_label="Q3", score_diff=-3, net_points_last_5_events=2.0, lead_changes_so_far=1),
            _build_state_row(game_id="G-FRAG", state_index=2, event_at=base + timedelta(seconds=60), opening_price=0.40, team_price=0.42, period_label="Q3", score_diff=-1, net_points_last_5_events=4.0, lead_changes_so_far=2),
            _build_state_row(game_id="G-FRAG", state_index=3, event_at=base + timedelta(seconds=90), opening_price=0.40, team_price=0.48, period_label="Q3", score_diff=0, net_points_last_5_events=5.0, lead_changes_so_far=2),
        ]
    )

    assert len(simulate_micro_momentum_continuation_trades(micro_frame, slippage_cents=0)) == 1
    assert len(simulate_panic_fade_fast_trades(panic_frame, slippage_cents=0)) == 1
    assert len(simulate_quarter_open_reprice_trades(q1_frame, slippage_cents=0)) == 1
    assert len(simulate_halftime_gap_fill_trades(halftime_frame, slippage_cents=0)) == 1
    assert len(simulate_lead_fragility_trades(fragility_frame, slippage_cents=0)) == 1


def test_replay_runner_executes_trade_when_quote_is_fresh_and_spread_is_tight() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    state_df = pd.DataFrame(
        [
            _build_state_row(game_id="G-REPLAY", state_index=0, event_at=base + timedelta(seconds=10), opening_price=0.55, team_price=0.55, period_label="Q2"),
            _build_state_row(game_id="G-REPLAY", state_index=1, event_at=base + timedelta(seconds=40), opening_price=0.55, team_price=0.62, period_label="Q2"),
        ]
    )
    bundle = {
        "game": {"game_id": "G-REPLAY", "game_start_time": base.isoformat()},
        "selected_market": {
            "series": [
                {"side": "home", "ticks": [{"ts": (base + timedelta(seconds=10)).isoformat(), "price": 0.55}, {"ts": (base + timedelta(seconds=40)).isoformat(), "price": 0.62}]},
                {"side": "away", "ticks": [{"ts": (base + timedelta(seconds=10)).isoformat(), "price": 0.45}, {"ts": (base + timedelta(seconds=40)).isoformat(), "price": 0.38}]},
            ]
        },
    }
    context = ReplayGameContext(
        game_id="G-REPLAY",
        season_phase="playoffs",
        game=bundle["game"],
        bundle=bundle,
        state_df=state_df,
        state_source="state_panel",
        coverage_status="covered_partial",
        classification="research_ready",
        anchor_at=base,
        end_at=base + timedelta(minutes=1),
    )
    standard_frame = pd.DataFrame(
        [
            {
                "season": "2025-26",
                "season_phase": "playoffs",
                "analysis_version": ANALYSIS_VERSION,
                "strategy_family": "micro_momentum_continuation",
                "entry_rule": "entry",
                "exit_rule": "exit",
                "game_id": "G-REPLAY",
                "team_side": "home",
                "team_slug": "HOME",
                "opponent_team_slug": "AWAY",
                "opening_band": "50-60",
                "period_label": "Q2",
                "score_diff_bucket": "lead_1_4",
                "context_bucket": "Q2|lead_1_4",
                "context_tags_json": "{}",
                "entry_metadata_json": "{}",
                "signal_strength": 5.0,
                "entry_state_index": 0,
                "exit_state_index": 1,
                "entry_at": base + timedelta(seconds=10),
                "exit_at": base + timedelta(seconds=40),
                "entry_price": 0.55,
                "exit_price": 0.62,
                "gross_return": 0.127,
                "gross_return_with_slippage": 0.127,
                "max_favorable_excursion_after_entry": 0.07,
                "max_adverse_excursion_after_entry": 0.0,
                "hold_time_seconds": 30.0,
                "slippage_cents": 0,
            }
        ]
    )
    subject = ReplaySubject(subject_name="micro_momentum_continuation", subject_type="family", standard_frame=standard_frame)
    request = ReplayRunRequest(
        season="2025-26",
        season_phase="playoffs",
        poll_interval_seconds=5.0,
        quote_max_age_seconds=30.0,
        max_spread_cents=2.0,
        proxy_min_spread_cents=1.0,
        proxy_max_spread_cents=2.0,
    )

    replay_frames, signal_summary_df, _ = simulate_replay_trade_frames([subject], contexts={"G-REPLAY": context}, request=request)

    assert len(replay_frames["micro_momentum_continuation"]) == 1
    assert bool(signal_summary_df.iloc[0]["executed_flag"]) is True
    assert signal_summary_df.iloc[0]["period_label"] == "Q2"
    assert signal_summary_df.iloc[0]["entry_window_label"] == "opening_0_60"
    assert int(signal_summary_df.iloc[0]["attempt_count"]) >= 1
    assert float(signal_summary_df.iloc[0]["max_quote_age_seconds"]) >= 0.0


def test_replay_runner_records_spread_gate_no_trade() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    state_df = pd.DataFrame(
        [
            _build_state_row(game_id="G-WIDE", state_index=0, event_at=base + timedelta(seconds=10), opening_price=0.60, team_price=0.60, period_label="Q2"),
            _build_state_row(game_id="G-WIDE", state_index=1, event_at=base + timedelta(seconds=40), opening_price=0.60, team_price=0.63, period_label="Q2"),
        ]
    )
    bundle = {
        "game": {"game_id": "G-WIDE", "game_start_time": base.isoformat()},
        "selected_market": {
            "series": [
                {"side": "home", "ticks": [{"ts": (base + timedelta(seconds=10)).isoformat(), "price": 0.60}]},
                {"side": "away", "ticks": [{"ts": (base + timedelta(seconds=10)).isoformat(), "price": 0.45}]},
            ]
        },
    }
    context = ReplayGameContext(
        game_id="G-WIDE",
        season_phase="playoffs",
        game=bundle["game"],
        bundle=bundle,
        state_df=state_df,
        state_source="derived_bundle",
        coverage_status="covered_partial",
        classification="research_ready",
        anchor_at=base,
        end_at=base + timedelta(minutes=1),
    )
    standard_frame = pd.DataFrame(
        [
            {
                "season": "2025-26",
                "season_phase": "playoffs",
                "analysis_version": ANALYSIS_VERSION,
                "strategy_family": "panic_fade_fast",
                "entry_rule": "entry",
                "exit_rule": "exit",
                "game_id": "G-WIDE",
                "team_side": "home",
                "team_slug": "HOME",
                "opponent_team_slug": "AWAY",
                "opening_band": "60-70",
                "period_label": "Q2",
                "score_diff_bucket": "lead_1_4",
                "context_bucket": "Q2|lead_1_4",
                "context_tags_json": "{}",
                "entry_metadata_json": "{}",
                "signal_strength": 4.0,
                "entry_state_index": 0,
                "exit_state_index": 1,
                "entry_at": base + timedelta(seconds=10),
                "exit_at": base + timedelta(seconds=40),
                "entry_price": 0.60,
                "exit_price": 0.63,
                "gross_return": 0.05,
                "gross_return_with_slippage": 0.05,
                "max_favorable_excursion_after_entry": 0.03,
                "max_adverse_excursion_after_entry": 0.0,
                "hold_time_seconds": 30.0,
                "slippage_cents": 0,
            }
        ]
    )
    subject = ReplaySubject(subject_name="panic_fade_fast", subject_type="family", standard_frame=standard_frame)
    request = ReplayRunRequest(
        season="2025-26",
        season_phase="playoffs",
        poll_interval_seconds=5.0,
        quote_max_age_seconds=30.0,
        max_spread_cents=2.0,
        proxy_min_spread_cents=1.0,
        proxy_max_spread_cents=6.0,
    )

    replay_frames, signal_summary_df, _ = simulate_replay_trade_frames([subject], contexts={"G-WIDE": context}, request=request)

    assert replay_frames["panic_fade_fast"].empty
    assert signal_summary_df.iloc[0]["no_trade_reason"] == "spread_too_wide"
    assert signal_summary_df.iloc[0]["terminal_no_trade_reason"] == "entry_after_exit_signal"
    assert signal_summary_df.iloc[0]["dominant_retry_reason"] == "spread_too_wide"


def test_historical_bidask_sample_builder_prefers_direct_bidask_and_tracks_coverage() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    context = ReplayGameContext(
        game_id="G-BIDASK",
        season_phase="playoffs",
        game={"game_id": "G-BIDASK", "game_start_time": base.isoformat()},
        bundle={
            "game": {"game_id": "G-BIDASK", "game_start_time": base.isoformat()},
            "selected_market": {
                "market_id": "market-G-BIDASK",
                "series": [
                    {
                        "side": "home",
                        "outcome_id": "home-outcome",
                        "ticks": [
                            {
                                "ts": (base + timedelta(seconds=10)).isoformat(),
                                "price": 0.55,
                                "bid": 0.54,
                                "ask": 0.56,
                                "source": "clob_prices_history",
                            }
                        ],
                    },
                    {
                        "side": "away",
                        "outcome_id": "away-outcome",
                        "ticks": [
                            {
                                "ts": (base + timedelta(seconds=10)).isoformat(),
                                "price": 0.45,
                                "source": "clob_prices_history",
                            }
                        ],
                    },
                ],
            },
        },
        state_df=pd.DataFrame(
            [
                _build_state_row(game_id="G-BIDASK", state_index=0, event_at=base + timedelta(seconds=10), opening_price=0.55, team_price=0.55, period_label="Q1"),
                _build_state_row(game_id="G-BIDASK", state_index=1, event_at=base + timedelta(seconds=40), opening_price=0.55, team_price=0.61, period_label="Q1"),
            ]
        ),
        state_source="derived_bundle",
        coverage_status="covered_partial",
        classification="research_ready",
        anchor_at=base,
        end_at=base + timedelta(seconds=40),
    )
    request = ReplayRunRequest(
        season="2025-26",
        season_phase="playoffs",
        quote_source_mode="historical_bidask_l1",
        quote_source_fallback_mode="cross_side_last_trade",
        poll_interval_seconds=5.0,
        quote_max_age_seconds=30.0,
        proxy_min_spread_cents=1.0,
        proxy_max_spread_cents=2.0,
    )

    frames_by_game, combined_df, coverage_df = build_historical_bidask_samples(
        contexts={"G-BIDASK": context},
        season="2025-26",
        request=request,
    )

    assert "G-BIDASK" in frames_by_game
    assert not combined_df.empty
    home_row = combined_df[combined_df["team_side"].astype(str) == "home"].iloc[0].to_dict()
    assert home_row["capture_status"] == "direct_bidask"
    assert float(home_row["best_bid_price"]) == 0.54
    assert float(home_row["best_ask_price"]) == 0.56
    assert int(coverage_df.iloc[0]["direct_bidask_quote_count"]) >= 1
    assert float(coverage_df.iloc[0]["coverage_ratio"]) > 0.0


def test_replay_runner_uses_historical_bidask_row_before_proxy_fallback() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    state_df = pd.DataFrame(
        [
            _build_state_row(game_id="G-L1-REPLAY", state_index=0, event_at=base + timedelta(seconds=10), opening_price=0.55, team_price=0.55, period_label="Q1"),
            _build_state_row(game_id="G-L1-REPLAY", state_index=1, event_at=base + timedelta(seconds=40), opening_price=0.55, team_price=0.62, period_label="Q1"),
        ]
    )
    bundle = {
        "game": {"game_id": "G-L1-REPLAY", "game_start_time": base.isoformat()},
        "selected_market": {
            "market_id": "market-G-L1-REPLAY",
            "series": [
                {
                    "side": "home",
                    "outcome_id": "home-outcome",
                    "ticks": [
                        {
                            "ts": (base + timedelta(seconds=10)).isoformat(),
                            "price": 0.55,
                            "bid": 0.54,
                            "ask": 0.56,
                            "source": "clob_prices_history",
                        }
                    ],
                },
                {
                    "side": "away",
                    "outcome_id": "away-outcome",
                    "ticks": [
                        {
                            "ts": (base + timedelta(seconds=10)).isoformat(),
                            "price": 0.45,
                            "source": "clob_prices_history",
                        }
                    ],
                },
            ]
        },
    }
    context = ReplayGameContext(
        game_id="G-L1-REPLAY",
        season_phase="playoffs",
        game=bundle["game"],
        bundle=bundle,
        state_df=state_df,
        state_source="derived_bundle",
        coverage_status="covered_partial",
        classification="research_ready",
        anchor_at=base,
        end_at=base + timedelta(minutes=1),
    )
    request = ReplayRunRequest(
        season="2025-26",
        season_phase="playoffs",
        quote_source_mode="historical_bidask_l1",
        quote_source_fallback_mode="",
        poll_interval_seconds=5.0,
        quote_max_age_seconds=30.0,
        max_spread_cents=3.0,
        proxy_min_spread_cents=1.0,
        proxy_max_spread_cents=2.0,
    )
    frames_by_game, _, _ = build_historical_bidask_samples(
        contexts={"G-L1-REPLAY": context},
        season="2025-26",
        request=request,
    )
    context.historical_bidask_df = frames_by_game["G-L1-REPLAY"]
    standard_frame = pd.DataFrame(
        [
            {
                "season": "2025-26",
                "season_phase": "playoffs",
                "analysis_version": ANALYSIS_VERSION,
                "strategy_family": "quarter_open_reprice",
                "entry_rule": "entry",
                "exit_rule": "exit",
                "game_id": "G-L1-REPLAY",
                "team_side": "home",
                "team_slug": "HOME",
                "opponent_team_slug": "AWAY",
                "opening_band": "50-60",
                "period_label": "Q1",
                "score_diff_bucket": "lead_1_4",
                "context_bucket": "Q1|lead_1_4",
                "context_tags_json": "{}",
                "entry_metadata_json": "{}",
                "signal_strength": 4.0,
                "entry_state_index": 0,
                "exit_state_index": 1,
                "entry_at": base + timedelta(seconds=10),
                "exit_at": base + timedelta(seconds=40),
                "entry_price": 0.55,
                "exit_price": 0.62,
                "gross_return": 0.127,
                "gross_return_with_slippage": 0.127,
                "max_favorable_excursion_after_entry": 0.07,
                "max_adverse_excursion_after_entry": 0.0,
                "hold_time_seconds": 30.0,
                "slippage_cents": 0,
            }
        ]
    )
    subject = ReplaySubject(subject_name="quarter_open_reprice", subject_type="family", standard_frame=standard_frame)

    replay_frames, signal_summary_df, _ = simulate_replay_trade_frames([subject], contexts={"G-L1-REPLAY": context}, request=request)

    assert len(replay_frames["quarter_open_reprice"]) == 1
    assert signal_summary_df.iloc[0]["quote_source_mode"] == "historical_bidask_l1"
    assert signal_summary_df.iloc[0]["quote_resolution_status"] == "direct_bidask"
    assert signal_summary_df.iloc[0]["capture_source"] == "clob_prices_history"
    assert float(signal_summary_df.iloc[0]["entry_fill_price"]) == 0.56


def test_build_replay_slate_expectation_frame_emits_first_party_candidate_surface() -> None:
    game_gap_df = pd.DataFrame(
        [
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "game_id": "G-QOR",
                "state_source": "state_panel",
                "standard_trade_count": 1,
                "replay_trade_count": 1,
                "trade_gap": 0,
                "top_no_trade_reason": None,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "G-INV",
                "state_source": "derived_bundle",
                "standard_trade_count": 1,
                "replay_trade_count": 0,
                "trade_gap": -1,
                "top_no_trade_reason": "quote_stale",
            },
            {
                "subject_name": "halftime_gap_fill",
                "subject_type": "family",
                "game_id": "G-HALF-NO",
                "state_source": "state_panel",
                "standard_trade_count": 0,
                "replay_trade_count": 0,
                "trade_gap": 0,
                "top_no_trade_reason": None,
            },
        ]
    )
    signal_summary_df = pd.DataFrame(
        [
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "game_id": "G-QOR",
                "signal_id": "quarter_open_reprice|G-QOR|home|0",
                "signal_entry_at": datetime(2026, 4, 24, 20, 0, 10, tzinfo=timezone.utc),
                "period_label": "Q1",
                "entry_window_label": "opening_0_60",
                "executed_flag": True,
                "no_trade_reason": None,
                "terminal_no_trade_reason": None,
                "replay_blocker_class": "executed",
                "replay_blocker_detail": "filled",
                "cadence_vs_stale_blocker": None,
                "first_visible_at": datetime(2026, 4, 24, 20, 0, 10, tzinfo=timezone.utc),
                "first_executable_event_at": datetime(2026, 4, 24, 20, 0, 10, tzinfo=timezone.utc),
                "first_executable_poll_at": datetime(2026, 4, 24, 20, 0, 12, tzinfo=timezone.utc),
                "stale_at": None,
                "quote_source_mode": "historical_bidask_l1",
                "quote_resolution_status": "synthetic_cross_side",
                "capture_source": "clob_prices_history",
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "game_id": "G-INV",
                "signal_id": "inversion|G-INV|away|8",
                "signal_entry_at": datetime(2026, 4, 24, 21, 0, 10, tzinfo=timezone.utc),
                "period_label": "Q4",
                "entry_window_label": "mid_360_720",
                "executed_flag": False,
                "no_trade_reason": "quote_stale",
                "terminal_no_trade_reason": "entry_after_exit_signal",
                "replay_blocker_class": "quote_freshness",
                "replay_blocker_detail": "quote_stale",
                "cadence_vs_stale_blocker": "signal_stale",
                "first_visible_at": datetime(2026, 4, 24, 21, 0, 10, tzinfo=timezone.utc),
                "first_executable_event_at": None,
                "first_executable_poll_at": None,
                "stale_at": datetime(2026, 4, 24, 21, 1, 10, tzinfo=timezone.utc),
                "quote_source_mode": "historical_bidask_l1",
                "quote_resolution_status": "synthetic_cross_side",
                "capture_source": "clob_prices_history",
            },
        ]
    )

    slate_df = build_replay_slate_expectation_frame(
        game_gap_df=game_gap_df,
        signal_summary_df=signal_summary_df,
    )

    executed_row = slate_df[slate_df["candidate_id"].astype(str) == "quarter_open_reprice"].iloc[0].to_dict()
    assert bool(executed_row["replay_expected_trade"]) is True
    assert executed_row["replay_expected_reason"] == "executed"
    assert executed_row["replay_signal_id"] == "quarter_open_reprice|G-QOR|home|0"

    blocked_row = slate_df[slate_df["candidate_id"].astype(str) == "inversion"].iloc[0].to_dict()
    assert bool(blocked_row["replay_expected_trade"]) is False
    assert blocked_row["replay_expected_reason"] == "quote_stale"
    assert blocked_row["replay_blocker_class"] == "quote_freshness"

    no_signal_row = slate_df[slate_df["candidate_id"].astype(str) == "halftime_gap_fill"].iloc[0].to_dict()
    assert bool(no_signal_row["replay_expected_trade"]) is False
    assert no_signal_row["replay_expected_reason"] == "no_standard_candidate"


def test_replay_runner_flags_poll_cadence_when_event_level_opportunity_dies_stale() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    state_df = pd.DataFrame(
        [
            _build_state_row(game_id="G-CADENCE", state_index=0, event_at=base + timedelta(seconds=10), opening_price=0.52, team_price=0.52, period_label="Q1"),
            _build_state_row(game_id="G-CADENCE", state_index=1, event_at=base + timedelta(seconds=14), opening_price=0.52, team_price=0.56, period_label="Q1", score_diff=2, net_points_last_5_events=3.0),
        ]
    )
    bundle = {
        "game": {"game_id": "G-CADENCE", "game_start_time": base.isoformat()},
        "selected_market": {
            "series": [
                {"side": "home", "ticks": [{"ts": (base + timedelta(seconds=11)).isoformat(), "price": 0.54}]},
                {"side": "away", "ticks": [{"ts": (base + timedelta(seconds=11)).isoformat(), "price": 0.44}]},
            ]
        },
    }
    context = ReplayGameContext(
        game_id="G-CADENCE",
        season_phase="playoffs",
        game=bundle["game"],
        bundle=bundle,
        state_df=state_df,
        state_source="derived_bundle",
        coverage_status="covered_partial",
        classification="research_ready",
        anchor_at=base,
        end_at=base + timedelta(seconds=30),
    )
    standard_frame = pd.DataFrame(
        [
            {
                "season": "2025-26",
                "season_phase": "playoffs",
                "analysis_version": ANALYSIS_VERSION,
                "strategy_family": "quarter_open_reprice",
                "entry_rule": "entry",
                "exit_rule": "exit",
                "game_id": "G-CADENCE",
                "team_side": "home",
                "team_slug": "HOME",
                "opponent_team_slug": "AWAY",
                "opening_band": "50-60",
                "period_label": "Q1",
                "score_diff_bucket": "lead_1_4",
                "context_bucket": "Q1|lead_1_4",
                "context_tags_json": "{}",
                "entry_metadata_json": "{}",
                "signal_strength": 4.0,
                "entry_state_index": 0,
                "exit_state_index": 2,
                "entry_at": base + timedelta(seconds=10),
                "exit_at": base + timedelta(seconds=20),
                "entry_price": 0.52,
                "exit_price": 0.58,
                "gross_return": 0.115,
                "gross_return_with_slippage": 0.115,
                "max_favorable_excursion_after_entry": 0.06,
                "max_adverse_excursion_after_entry": 0.0,
                "hold_time_seconds": 10.0,
                "slippage_cents": 0,
            }
        ]
    )
    subject = ReplaySubject(subject_name="quarter_open_reprice", subject_type="family", standard_frame=standard_frame)
    request = ReplayRunRequest(
        season="2025-26",
        season_phase="playoffs",
        poll_interval_seconds=5.0,
        signal_max_age_seconds=3.0,
        quote_max_age_seconds=30.0,
        max_spread_cents=2.0,
        proxy_min_spread_cents=1.0,
        proxy_max_spread_cents=2.0,
    )

    replay_frames, signal_summary_df, _ = simulate_replay_trade_frames([subject], contexts={"G-CADENCE": context}, request=request)

    assert replay_frames["quarter_open_reprice"].empty
    assert signal_summary_df.iloc[0]["no_trade_reason"] == "signal_stale"
    assert bool(signal_summary_df.iloc[0]["event_level_opportunity_flag"]) is True
    assert bool(signal_summary_df.iloc[0]["poll_level_opportunity_flag"]) is False
    assert bool(signal_summary_df.iloc[0]["cadence_blocker_flag"]) is True
    assert signal_summary_df.iloc[0]["replay_blocker_class"] == "polling_cadence"
    assert signal_summary_df.iloc[0]["cadence_vs_stale_blocker"] == "polling_cadence"
    assert signal_summary_df.iloc[0]["first_executable_event_at"] == base + timedelta(seconds=11)
    assert signal_summary_df.iloc[0]["stale_at"] == base + timedelta(seconds=14)
