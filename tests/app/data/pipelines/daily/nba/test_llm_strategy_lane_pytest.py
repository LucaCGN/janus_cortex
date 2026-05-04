from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.data.pipelines.daily.nba.analysis.benchmark_integration import (
    UnifiedBenchmarkRequest,
    build_unified_benchmark_dashboard,
)
from app.data.pipelines.daily.nba.analysis.llm_strategy_lane import (
    LLMStrategyLaneRequest,
    _aggregate_attempt_trace,
    _build_decision_clusters,
    _load_optional_ml_features,
    run_llm_strategy_lane,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _build_trade_row(
    *,
    strategy_family: str,
    game_id: str,
    team_side: str,
    entry_state_index: int,
    exit_state_index: int,
    entry_at: datetime,
    exit_at: datetime,
    entry_price: float,
    exit_price: float,
    signal_strength: float,
    period_label: str,
    opening_band: str,
    score_diff_bucket: str,
    context_bucket: str,
    team_slug: str,
    opponent_team_slug: str,
) -> dict[str, object]:
    return {
        "season": "2025-26",
        "season_phase": "playoffs",
        "analysis_version": "v1_0_1",
        "strategy_family": strategy_family,
        "entry_rule": "entry_rule",
        "exit_rule": "plus_6c_or_minus_4c_or_timebox",
        "game_id": game_id,
        "team_side": team_side,
        "team_slug": team_slug,
        "opponent_team_slug": opponent_team_slug,
        "opening_band": opening_band,
        "period_label": period_label,
        "score_diff_bucket": score_diff_bucket,
        "context_bucket": context_bucket,
        "context_tags_json": "{}",
        "entry_metadata_json": '{"target_price": 0.68, "stop_price": 0.50}',
        "signal_strength": signal_strength,
        "entry_state_index": entry_state_index,
        "exit_state_index": exit_state_index,
        "entry_at": entry_at.isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return": (exit_price - entry_price) / entry_price,
        "gross_return_with_slippage": (exit_price - entry_price) / entry_price,
        "max_favorable_excursion_after_entry": max(0.0, exit_price - entry_price),
        "max_adverse_excursion_after_entry": 0.0,
        "hold_time_seconds": float((exit_at - entry_at).total_seconds()),
        "slippage_cents": 0,
    }


def _build_replay_trade_row(standard_row: dict[str, object], *, subject_name: str) -> dict[str, object]:
    row = dict(standard_row)
    row.update(
        {
            "engine_mode": "execution_replay",
            "execution_profile_version": "replay_v1",
            "signal_entry_at": row["entry_at"],
            "signal_exit_at": row["exit_at"],
            "signal_entry_price": row["entry_price"],
            "signal_exit_price": row["exit_price"],
            "signal_age_seconds_at_submit": 2.0,
            "quote_age_seconds_at_submit": 12.0,
            "spread_cents_at_submit": 1.0,
            "submission_window_label": "signal_fresh",
            "replay_trade_status": "executed",
            "skip_reason": None,
            "no_trade_reason": None,
            "exit_fill_mode": "quote_exit",
            "subject_name": subject_name,
            "subject_type": "family",
        }
    )
    return row


def _build_state_row(
    *,
    game_id: str,
    team_side: str,
    state_index: int,
    event_at: datetime,
    period_label: str,
    clock_elapsed_seconds: float,
    seconds_to_game_end: float,
    score_diff: int,
    lead_changes_so_far: int,
    abs_price_delta_from_open: float,
    net_points_last_5_events: float,
) -> dict[str, object]:
    period_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    opening_price = 0.55
    team_price = opening_price + abs_price_delta_from_open
    return {
        "game_id": game_id,
        "team_side": team_side,
        "state_index": state_index,
        "period": period_map.get(period_label, 1),
        "period_label": period_label,
        "clock_elapsed_seconds": clock_elapsed_seconds,
        "seconds_to_game_end": seconds_to_game_end,
        "score_diff": score_diff,
        "lead_changes_so_far": lead_changes_so_far,
        "team_led_flag": score_diff > 0,
        "team_trailed_flag": score_diff < 0,
        "market_favorite_flag": True,
        "scoreboard_control_mismatch_flag": False,
        "team_price": team_price,
        "price_delta_from_open": abs_price_delta_from_open,
        "abs_price_delta_from_open": abs_price_delta_from_open,
        "net_points_last_5_events": net_points_last_5_events,
        "gap_before_seconds": 5.0,
        "gap_after_seconds": 5.0,
        "event_at": event_at.isoformat(),
    }


def test_aggregate_attempt_trace_prefers_first_filled_then_first_no_trade() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    attempt_trace_df = pd.DataFrame(
        [
            {
                "signal_id": "sig-a",
                "game_id": "42500152",
                "attempt_index": 0,
                "cycle_at": (base + timedelta(seconds=5)).isoformat(),
                "quote_time": base.isoformat(),
                "result": "retry",
                "reason": "quote_stale",
                "entry_state_index": 10,
                "latest_state_index": 11,
                "quote_age_seconds": 45.0,
                "spread_cents": 1.0,
            },
            {
                "signal_id": "sig-a",
                "game_id": "42500152",
                "attempt_index": 1,
                "cycle_at": (base + timedelta(seconds=15)).isoformat(),
                "quote_time": (base + timedelta(seconds=10)).isoformat(),
                "result": "filled",
                "reason": "entry_filled",
                "entry_state_index": 10,
                "latest_state_index": 10,
                "quote_age_seconds": 5.0,
                "spread_cents": 1.0,
            },
            {
                "signal_id": "sig-b",
                "game_id": "42500172",
                "attempt_index": 0,
                "cycle_at": (base + timedelta(seconds=25)).isoformat(),
                "quote_time": (base + timedelta(seconds=15)).isoformat(),
                "result": "retry",
                "reason": "quote_stale",
                "entry_state_index": 20,
                "latest_state_index": 23,
                "quote_age_seconds": 90.0,
                "spread_cents": 3.0,
            },
            {
                "signal_id": "sig-b",
                "game_id": "42500172",
                "attempt_index": 1,
                "cycle_at": (base + timedelta(seconds=40)).isoformat(),
                "quote_time": (base + timedelta(seconds=35)).isoformat(),
                "result": "no_trade",
                "reason": "signal_stale",
                "entry_state_index": 20,
                "latest_state_index": 25,
                "quote_age_seconds": 5.0,
                "spread_cents": 1.0,
            },
        ]
    )

    aggregated = _aggregate_attempt_trace(attempt_trace_df)
    row_a = aggregated.loc[aggregated["signal_id"] == "sig-a"].iloc[0]
    row_b = aggregated.loc[aggregated["signal_id"] == "sig-b"].iloc[0]

    assert row_a["submit_attempt_result"] == "filled"
    assert float(row_a["submit_attempt_quote_age_seconds"]) == 5.0
    assert row_b["submit_attempt_result"] == "no_trade"
    assert row_b["submit_attempt_reason"] == "signal_stale"


def test_build_decision_clusters_groups_same_game_with_time_window() -> None:
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame(
        [
            {"game_id": "0042500152", "strategy_family": "inversion", "signal_id": "a", "signal_entry_at": base},
            {"game_id": "0042500152", "strategy_family": "quarter_open_reprice", "signal_id": "b", "signal_entry_at": base + timedelta(minutes=4)},
            {"game_id": "0042500152", "strategy_family": "winner_definition", "signal_id": "c", "signal_entry_at": base + timedelta(minutes=21)},
        ]
    )

    clustered = _build_decision_clusters(frame, cluster_window_minutes=15)

    assert clustered.loc[clustered["signal_id"].eq("a"), "cluster_id"].iloc[0] == clustered.loc[
        clustered["signal_id"].eq("b"), "cluster_id"
    ].iloc[0]
    assert clustered.loc[clustered["signal_id"].eq("a"), "cluster_id"].iloc[0] != clustered.loc[
        clustered["signal_id"].eq("c"), "cluster_id"
    ].iloc[0]


def test_load_optional_ml_features_prefers_v2_and_exposes_focus_helpers(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared"
    ml_v1_root = shared_root / "artifacts" / "ml-trading-lane" / "2025-26" / "postseason_replay_ml_v1"
    ml_v2_root = shared_root / "artifacts" / "ml-trading-lane" / "2025-26" / "postseason_replay_ml_v2"

    _write_csv(
        ml_v1_root / "family_candidates.csv",
        [
            {
                "signal_id": "inversion|old",
                "strategy_family": "inversion",
                "focus_family_flag": False,
                "rank_score": 0.31,
                "gate_score": 0.40,
                "selection_source": "legacy",
            }
        ],
    )
    _write_csv(
        ml_v2_root / "family_candidates.csv",
        [
            {
                "signal_id": "inversion|new",
                "strategy_family": "inversion",
                "focus_family_flag": True,
                "rank_score": 0.55,
                "calibrated_rank_score": 0.58,
                "gate_score": 0.62,
                "calibrated_execution_likelihood": 0.67,
                "sidecar_probability": 0.64,
                "selection_source": "focused_family_reranker",
            }
        ],
    )
    _write_csv(
        ml_v2_root / "focus_family_selected.csv",
        [
            {
                "strategy_family": "inversion",
                "focus_family_flag": True,
            }
        ],
    )

    ml_df, summary = _load_optional_ml_features(shared_root, "2025-26")

    assert summary["available"] is True
    assert summary["artifact_name"] == "postseason_replay_ml_v2"
    assert summary["artifact_version"] == 2
    assert summary["focus_families"] == ["inversion"]
    assert bool(ml_df.loc[0, "ml_focus_family_flag"]) is True
    assert ml_df.loc[0, "ml_focus_family_tag"] == "inversion"
    assert float(ml_df.loc[0, "ml_calibrated_execution_likelihood"]) == 0.67
    assert float(ml_df.loc[0, "ml_sidecar_probability"]) == 0.64


def test_run_llm_strategy_lane_publishes_submission_and_dashboard_ingests_it(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared"
    replay_root = shared_root / "artifacts" / "replay-engine-hf" / "2025-26" / "postseason_execution_replay"
    analysis_output_root = tmp_path / "analysis_output"
    base = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)

    (shared_root / "benchmark_contract").mkdir(parents=True, exist_ok=True)
    (shared_root / "benchmark_contract" / "replay_contract_current.md").write_text("maturity: stable\n", encoding="utf-8")
    (shared_root / "benchmark_contract" / "unified_benchmark_contract_current.md").write_text(
        "version: v1\n",
        encoding="utf-8",
    )

    replay_root.mkdir(parents=True, exist_ok=True)
    _write_csv(
        replay_root / "replay_subject_summary.csv",
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "standard_trade_count": 2,
                "replay_trade_count": 1,
                "trade_gap": -1,
                "execution_rate": 0.5,
                "standard_avg_return_with_slippage": 0.10,
                "replay_avg_return_with_slippage": 0.22,
                "replay_no_trade_count": 1,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 11.0,
                "replay_ending_bankroll": 12.5,
                "live_trade_count": 0,
            },
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "standard_trade_count": 1,
                "replay_trade_count": 1,
                "trade_gap": 0,
                "execution_rate": 1.0,
                "standard_avg_return_with_slippage": 0.14,
                "replay_avg_return_with_slippage": 0.18,
                "replay_no_trade_count": 0,
                "top_no_trade_reason": None,
                "standard_ending_bankroll": 11.4,
                "replay_ending_bankroll": 11.8,
                "live_trade_count": 0,
            },
            {
                "subject_name": "micro_momentum_continuation",
                "subject_type": "family",
                "standard_trade_count": 1,
                "replay_trade_count": 1,
                "trade_gap": 0,
                "execution_rate": 1.0,
                "standard_avg_return_with_slippage": 0.12,
                "replay_avg_return_with_slippage": 0.16,
                "replay_no_trade_count": 0,
                "top_no_trade_reason": None,
                "standard_ending_bankroll": 11.2,
                "replay_ending_bankroll": 11.6,
                "live_trade_count": 0,
            },
            {
                "subject_name": "lead_fragility",
                "subject_type": "family",
                "standard_trade_count": 1,
                "replay_trade_count": 1,
                "trade_gap": 0,
                "execution_rate": 1.0,
                "standard_avg_return_with_slippage": 0.08,
                "replay_avg_return_with_slippage": 0.11,
                "replay_no_trade_count": 0,
                "top_no_trade_reason": None,
                "standard_ending_bankroll": 10.8,
                "replay_ending_bankroll": 11.1,
                "live_trade_count": 0,
            },
            {
                "subject_name": "winner_definition",
                "subject_type": "family",
                "standard_trade_count": 1,
                "replay_trade_count": 0,
                "trade_gap": -1,
                "execution_rate": 0.0,
                "standard_avg_return_with_slippage": 0.03,
                "replay_avg_return_with_slippage": 0.0,
                "replay_no_trade_count": 1,
                "top_no_trade_reason": "signal_stale",
                "standard_ending_bankroll": 10.3,
                "replay_ending_bankroll": 10.0,
                "live_trade_count": 0,
            },
        ],
    )

    signal_rows = [
        {
            "subject_name": "quarter_open_reprice",
            "subject_type": "family",
            "game_id": "0052500201",
            "team_side": "home",
            "signal_id": "quarter_open_reprice|0052500201|home|42",
            "strategy_family": "quarter_open_reprice",
            "entry_state_index": 42,
            "exit_state_index": 63,
            "signal_entry_at": (base + timedelta(minutes=1)).isoformat(),
            "signal_exit_at": (base + timedelta(minutes=8)).isoformat(),
            "signal_entry_price": 0.56,
            "signal_exit_price": 0.64,
            "executed_flag": True,
            "no_trade_reason": None,
        },
        {
            "subject_name": "inversion",
            "subject_type": "family",
            "game_id": "0052500201",
            "team_side": "home",
            "signal_id": "inversion|0052500201|home|42",
            "strategy_family": "inversion",
            "entry_state_index": 42,
            "exit_state_index": 63,
            "signal_entry_at": (base + timedelta(minutes=1)).isoformat(),
            "signal_exit_at": (base + timedelta(minutes=8)).isoformat(),
            "signal_entry_price": 0.56,
            "signal_exit_price": 0.70,
            "executed_flag": True,
            "no_trade_reason": None,
        },
        {
            "subject_name": "micro_momentum_continuation",
            "subject_type": "family",
            "game_id": "0052500201",
            "team_side": "home",
            "signal_id": "micro_momentum_continuation|0052500201|home|42",
            "strategy_family": "micro_momentum_continuation",
            "entry_state_index": 42,
            "exit_state_index": 63,
            "signal_entry_at": (base + timedelta(minutes=1)).isoformat(),
            "signal_exit_at": (base + timedelta(minutes=8)).isoformat(),
            "signal_entry_price": 0.56,
            "signal_exit_price": 0.65,
            "executed_flag": True,
            "no_trade_reason": None,
        },
        {
            "subject_name": "lead_fragility",
            "subject_type": "family",
            "game_id": "0042500172",
            "team_side": "home",
            "signal_id": "lead_fragility|0042500172|home|317",
            "strategy_family": "lead_fragility",
            "entry_state_index": 317,
            "exit_state_index": 355,
            "signal_entry_at": (base + timedelta(minutes=15)).isoformat(),
            "signal_exit_at": (base + timedelta(minutes=24)).isoformat(),
            "signal_entry_price": 0.44,
            "signal_exit_price": 0.50,
            "executed_flag": True,
            "no_trade_reason": None,
        },
        {
            "subject_name": "winner_definition",
            "subject_type": "family",
            "game_id": "0042500111",
            "team_side": "home",
            "signal_id": "winner_definition|0042500111|home|20",
            "strategy_family": "winner_definition",
            "entry_state_index": 20,
            "exit_state_index": 60,
            "signal_entry_at": (base + timedelta(minutes=30)).isoformat(),
            "signal_exit_at": (base + timedelta(minutes=38)).isoformat(),
            "signal_entry_price": 0.61,
            "signal_exit_price": 0.66,
            "executed_flag": False,
            "no_trade_reason": "signal_stale",
        },
    ]
    _write_csv(replay_root / "replay_signal_summary.csv", signal_rows)

    attempt_rows = [
        {
            "subject_name": "quarter_open_reprice",
            "subject_type": "family",
            "game_id": "0052500201",
            "signal_id": "quarter_open_reprice|0052500201|home|42",
            "attempt_stage": "entry",
            "cycle_at": (base + timedelta(minutes=1, seconds=2)).isoformat(),
            "attempt_index": 0,
            "result": "filled",
            "reason": "entry_filled",
            "entry_state_index": 42,
            "latest_state_index": 42,
            "quote_time": (base + timedelta(seconds=40)).isoformat(),
            "quote_age_seconds": 22.0,
            "best_bid": 0.55,
            "best_ask": 0.56,
            "spread_cents": 1.0,
        },
        {
            "subject_name": "inversion",
            "subject_type": "family",
            "game_id": "0052500201",
            "signal_id": "inversion|0052500201|home|42",
            "attempt_stage": "entry",
            "cycle_at": (base + timedelta(minutes=1, seconds=3)).isoformat(),
            "attempt_index": 0,
            "result": "filled",
            "reason": "entry_filled",
            "entry_state_index": 42,
            "latest_state_index": 42,
            "quote_time": (base + timedelta(seconds=39)).isoformat(),
            "quote_age_seconds": 24.0,
            "best_bid": 0.55,
            "best_ask": 0.56,
            "spread_cents": 1.0,
        },
        {
            "subject_name": "micro_momentum_continuation",
            "subject_type": "family",
            "game_id": "0052500201",
            "signal_id": "micro_momentum_continuation|0052500201|home|42",
            "attempt_stage": "entry",
            "cycle_at": (base + timedelta(minutes=1, seconds=4)).isoformat(),
            "attempt_index": 0,
            "result": "filled",
            "reason": "entry_filled",
            "entry_state_index": 42,
            "latest_state_index": 42,
            "quote_time": (base + timedelta(seconds=38)).isoformat(),
            "quote_age_seconds": 26.0,
            "best_bid": 0.55,
            "best_ask": 0.56,
            "spread_cents": 1.0,
        },
        {
            "subject_name": "lead_fragility",
            "subject_type": "family",
            "game_id": "0042500172",
            "signal_id": "lead_fragility|0042500172|home|317",
            "attempt_stage": "entry",
            "cycle_at": (base + timedelta(minutes=16)).isoformat(),
            "attempt_index": 0,
            "result": "filled",
            "reason": "entry_filled",
            "entry_state_index": 317,
            "latest_state_index": 320,
            "quote_time": (base + timedelta(minutes=15, seconds=55)).isoformat(),
            "quote_age_seconds": 5.0,
            "best_bid": 0.43,
            "best_ask": 0.44,
            "spread_cents": 1.0,
        },
        {
            "subject_name": "winner_definition",
            "subject_type": "family",
            "game_id": "0042500111",
            "signal_id": "winner_definition|0042500111|home|20",
            "attempt_stage": "entry",
            "cycle_at": (base + timedelta(minutes=31)).isoformat(),
            "attempt_index": 0,
            "result": "no_trade",
            "reason": "signal_stale",
            "entry_state_index": 20,
            "latest_state_index": 28,
            "quote_time": (base + timedelta(minutes=20)).isoformat(),
            "quote_age_seconds": 660.0,
            "best_bid": 0.60,
            "best_ask": 0.61,
            "spread_cents": 1.0,
        },
    ]
    _write_csv(replay_root / "replay_attempt_trace.csv", attempt_rows)
    _write_csv(
        replay_root / "replay_portfolio_summary.csv",
        [
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 11.0,
                "compounded_return": 0.10,
                "max_drawdown_pct": 0.05,
                "max_drawdown_amount": 0.5,
                "executed_trade_count": 2,
            },
            {
                "subject_name": "inversion",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 12.5,
                "compounded_return": 0.25,
                "max_drawdown_pct": 0.03,
                "max_drawdown_amount": 0.3,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 11.4,
                "compounded_return": 0.14,
                "max_drawdown_pct": 0.04,
                "max_drawdown_amount": 0.4,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "quarter_open_reprice",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 11.8,
                "compounded_return": 0.18,
                "max_drawdown_pct": 0.03,
                "max_drawdown_amount": 0.3,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "micro_momentum_continuation",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 11.2,
                "compounded_return": 0.12,
                "max_drawdown_pct": 0.04,
                "max_drawdown_amount": 0.4,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "micro_momentum_continuation",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 11.6,
                "compounded_return": 0.16,
                "max_drawdown_pct": 0.03,
                "max_drawdown_amount": 0.3,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "lead_fragility",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 10.8,
                "compounded_return": 0.08,
                "max_drawdown_pct": 0.05,
                "max_drawdown_amount": 0.5,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "lead_fragility",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 11.1,
                "compounded_return": 0.11,
                "max_drawdown_pct": 0.03,
                "max_drawdown_amount": 0.3,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "winner_definition",
                "subject_type": "family",
                "mode": "standard",
                "ending_bankroll": 10.3,
                "compounded_return": 0.03,
                "max_drawdown_pct": 0.04,
                "max_drawdown_amount": 0.4,
                "executed_trade_count": 1,
            },
            {
                "subject_name": "winner_definition",
                "subject_type": "family",
                "mode": "replay",
                "ending_bankroll": 10.0,
                "compounded_return": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_amount": 0.0,
                "executed_trade_count": 0,
            },
        ],
    )

    qor_standard = _build_trade_row(
        strategy_family="quarter_open_reprice",
        game_id="0052500201",
        team_side="home",
        entry_state_index=42,
        exit_state_index=63,
        entry_at=base + timedelta(minutes=1),
        exit_at=base + timedelta(minutes=8),
        entry_price=0.56,
        exit_price=0.64,
        signal_strength=9.1,
        period_label="Q1",
        opening_band="50-60",
        score_diff_bucket="lead_5_9",
        context_bucket="Q1|lead_5_9",
        team_slug="ORL",
        opponent_team_slug="ATL",
    )
    inversion_standard = _build_trade_row(
        strategy_family="inversion",
        game_id="0052500201",
        team_side="home",
        entry_state_index=42,
        exit_state_index=63,
        entry_at=base + timedelta(minutes=1),
        exit_at=base + timedelta(minutes=8),
        entry_price=0.56,
        exit_price=0.70,
        signal_strength=7.0,
        period_label="Q1",
        opening_band="50-60",
        score_diff_bucket="lead_5_9",
        context_bucket="Q1|lead_5_9",
        team_slug="ORL",
        opponent_team_slug="ATL",
    )
    micro_standard = _build_trade_row(
        strategy_family="micro_momentum_continuation",
        game_id="0052500201",
        team_side="home",
        entry_state_index=42,
        exit_state_index=63,
        entry_at=base + timedelta(minutes=1),
        exit_at=base + timedelta(minutes=8),
        entry_price=0.56,
        exit_price=0.65,
        signal_strength=8.4,
        period_label="Q1",
        opening_band="50-60",
        score_diff_bucket="lead_5_9",
        context_bucket="Q1|lead_5_9",
        team_slug="ORL",
        opponent_team_slug="ATL",
    )
    frag_standard = _build_trade_row(
        strategy_family="lead_fragility",
        game_id="0042500172",
        team_side="home",
        entry_state_index=317,
        exit_state_index=355,
        entry_at=base + timedelta(minutes=15),
        exit_at=base + timedelta(minutes=24),
        entry_price=0.44,
        exit_price=0.50,
        signal_strength=7.8,
        period_label="Q4",
        opening_band="40-50",
        score_diff_bucket="lead_1_4",
        context_bucket="Q4|lead_1_4",
        team_slug="DAL",
        opponent_team_slug="LAC",
    )
    winner_standard = _build_trade_row(
        strategy_family="winner_definition",
        game_id="0042500111",
        team_side="home",
        entry_state_index=20,
        exit_state_index=60,
        entry_at=base + timedelta(minutes=30),
        exit_at=base + timedelta(minutes=38),
        entry_price=0.61,
        exit_price=0.66,
        signal_strength=3.0,
        period_label="Q4",
        opening_band="60-70",
        score_diff_bucket="lead_5_9",
        context_bucket="Q4|lead_5_9",
        team_slug="BOS",
        opponent_team_slug="MIA",
    )

    _write_csv(replay_root / "standard_quarter_open_reprice.csv", [qor_standard])
    _write_csv(replay_root / "replay_quarter_open_reprice.csv", [_build_replay_trade_row(qor_standard, subject_name="quarter_open_reprice")])
    _write_csv(replay_root / "standard_inversion.csv", [inversion_standard])
    _write_csv(replay_root / "replay_inversion.csv", [_build_replay_trade_row(inversion_standard, subject_name="inversion")])
    _write_csv(replay_root / "standard_micro_momentum_continuation.csv", [micro_standard])
    _write_csv(
        replay_root / "replay_micro_momentum_continuation.csv",
        [_build_replay_trade_row(micro_standard, subject_name="micro_momentum_continuation")],
    )
    _write_csv(replay_root / "standard_lead_fragility.csv", [frag_standard])
    _write_csv(replay_root / "replay_lead_fragility.csv", [_build_replay_trade_row(frag_standard, subject_name="lead_fragility")])
    _write_csv(replay_root / "standard_winner_definition.csv", [winner_standard])

    for phase in ("play_in", "playoffs"):
        output_dir = analysis_output_root / "2025-26" / phase / "v1_0_1"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_rows = [
            _build_state_row(
                game_id="0052500201",
                team_side="home",
                state_index=42,
                event_at=base + timedelta(minutes=1),
                period_label="Q1",
                clock_elapsed_seconds=240.0,
                seconds_to_game_end=2640.0,
                score_diff=8,
                lead_changes_so_far=2,
                abs_price_delta_from_open=0.12,
                net_points_last_5_events=4.0,
            ),
            _build_state_row(
                game_id="0042500172",
                team_side="home",
                state_index=317,
                event_at=base + timedelta(minutes=15),
                period_label="Q4",
                clock_elapsed_seconds=2500.0,
                seconds_to_game_end=380.0,
                score_diff=2,
                lead_changes_so_far=8,
                abs_price_delta_from_open=0.10,
                net_points_last_5_events=3.0,
            ),
            _build_state_row(
                game_id="0042500111",
                team_side="home",
                state_index=20,
                event_at=base + timedelta(minutes=30),
                period_label="Q4",
                clock_elapsed_seconds=2600.0,
                seconds_to_game_end=280.0,
                score_diff=7,
                lead_changes_so_far=1,
                abs_price_delta_from_open=0.03,
                net_points_last_5_events=1.0,
            ),
        ]
        _write_csv(output_dir / "nba_analysis_state_panel.csv", state_rows)

    ml_root = shared_root / "artifacts" / "ml-trading-lane" / "2025-26" / "postseason_replay_ml_v2"
    _write_csv(
        ml_root / "family_candidates.csv",
        [
            {
                "signal_id": "inversion|0052500201|home|42",
                "strategy_family": "inversion",
                "focus_family_flag": True,
                "rank_score": 0.61,
                "calibrated_rank_score": 0.63,
                "gate_score": 0.66,
                "calibrated_execution_likelihood": 0.71,
                "sidecar_probability": 0.69,
                "selection_source": "focused_family_reranker",
            },
            {
                "signal_id": "quarter_open_reprice|0052500201|home|42",
                "strategy_family": "quarter_open_reprice",
                "focus_family_flag": True,
                "rank_score": 0.55,
                "calibrated_rank_score": 0.57,
                "gate_score": 0.60,
                "calibrated_execution_likelihood": 0.65,
                "sidecar_probability": 0.62,
                "selection_source": "focused_family_reranker",
            },
            {
                "signal_id": "micro_momentum_continuation|0052500201|home|42",
                "strategy_family": "micro_momentum_continuation",
                "focus_family_flag": True,
                "rank_score": 0.52,
                "calibrated_rank_score": 0.54,
                "gate_score": 0.58,
                "calibrated_execution_likelihood": 0.63,
                "sidecar_probability": 0.60,
                "selection_source": "focused_family_reranker",
            },
        ],
    )
    _write_csv(
        ml_root / "focus_family_selected.csv",
        [
            {"strategy_family": "inversion", "focus_family_flag": True},
            {"strategy_family": "quarter_open_reprice", "focus_family_flag": True},
            {"strategy_family": "micro_momentum_continuation", "focus_family_flag": True},
        ],
    )

    payload = run_llm_strategy_lane(
        LLMStrategyLaneRequest(
            season="2025-26",
            analysis_version="v1_0_1",
            shared_root=str(shared_root),
            analysis_output_root=str(analysis_output_root),
            build_dashboard_check=True,
        )
    )

    submission_path = shared_root / "reports" / "llm-strategy-lane" / "benchmark_submission.json"
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    shadow_payload_path = shared_root / "handoffs" / "llm-strategy-lane" / "shadow_sidecar_payload.json"
    shadow_payload = json.loads(shadow_payload_path.read_text(encoding="utf-8"))

    assert payload["lane_id"] == "llm-strategy"
    assert submission["lane_id"] == "llm-strategy"
    assert len(submission["subjects"]) == 2
    assert submission["lane_recommendation"]["deployment_recommendation"] in {"bench", "shadow", "live-probe"}
    assert "promotion_gate" in submission["lane_recommendation"]
    assert all(subject["result_views"]["live_observed"]["live_observed_flag"] is False for subject in submission["subjects"])
    assert all("decision_trace_json" in subject["trace_artifacts"] for subject in submission["subjects"])
    assert all("daily_live_validation_markdown" in subject["artifacts"] for subject in submission["subjects"])
    assert all("shadow_sidecar_payload_json" in subject["artifacts"] for subject in submission["subjects"])
    assert (shared_root / "handoffs" / "llm-strategy-lane" / "daily_live_validation.md").exists()
    assert shadow_payload["active_variants"] == [
        "llm_selector_core_windows_v2",
        "llm_template_compiler_core_windows_v2",
    ]
    assert shadow_payload["primary_target_families"] == [
        "inversion",
        "quarter_open_reprice",
        "micro_momentum_continuation",
    ]
    assert "failed_checks" in shadow_payload["promotion_gate"]
    assert payload["dataset_summary"]["optional_ml_summary"]["artifact_name"] == "postseason_replay_ml_v2"

    dashboard = build_unified_benchmark_dashboard(
        UnifiedBenchmarkRequest(
            season="2025-26",
            replay_artifact_name="postseason_execution_replay",
            shared_root=str(shared_root),
            finalist_limit=6,
        )
    )
    llm_rows = dashboard.get("llm_candidates") or []

    assert any(row.get("candidate_id") == "llm_selector_core_windows_v2" for row in llm_rows)
    assert any(row.get("candidate_id") == "llm_template_compiler_core_windows_v2" for row in llm_rows)
