from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_options_app.profiles.classifier import classify_account_type
from crypto_options_app.profiles.generator_scores import build_generator_outputs
from crypto_options_app.profiles.grading import PeriodMetrics, compute_global_grade, compute_usage_eligibility
from crypto_options_app.profiles.reconstruction import ProfileOrder, reconstruct_profile_event


def test_reconstruction_computes_inventory_cost_basis_and_effective_win_pytest() -> None:
    reconstruction = reconstruct_profile_event(
        profile_key="profile-1",
        event_key="event-1",
        resolved_outcome="Up",
        orders=[
            ProfileOrder("profile-1", "event-1", "Up", "BUY", 0.4, 5.0, raw_activity_key="up-buy"),
            ProfileOrder("profile-1", "event-1", "Down", "BUY", 0.45, 5.0, raw_activity_key="down-buy"),
        ],
    )

    assert reconstruction.up.open_shares == 5.0
    assert reconstruction.down.open_shares == 5.0
    assert reconstruction.up.weighted_avg_price == 0.4
    assert reconstruction.down.weighted_avg_price == 0.45
    assert reconstruction.hedge_balance == 1.0
    assert reconstruction.side_skew == 0.0
    assert reconstruction.settlement_pnl_usd == 0.75
    assert reconstruction.event_effective_win is True
    assert reconstruction.source_order_keys == ("up-buy", "down-buy")


def test_account_type_classification_uses_reconstructed_event_behavior_pytest() -> None:
    outcome = reconstruct_profile_event(
        profile_key="outcome",
        event_key="event-1",
        orders=[ProfileOrder("outcome", "event-1", "Up", "BUY", 0.6, 10.0)],
    )
    hedger = reconstruct_profile_event(
        profile_key="hedger",
        event_key="event-1",
        orders=[
            ProfileOrder("hedger", "event-1", "Up", "BUY", 0.4, 10.0),
            ProfileOrder("hedger", "event-1", "Down", "BUY", 0.4, 9.0),
        ],
    )
    grid_orders = [
        ProfileOrder("grid", "event-1", "Up", "BUY", 0.1, 2.0),
        ProfileOrder("grid", "event-1", "Up", "BUY", 0.15, 2.0),
        ProfileOrder("grid", "event-1", "Up", "BUY", 0.2, 2.0),
        ProfileOrder("grid", "event-1", "Down", "BUY", 0.1, 2.0),
        ProfileOrder("grid", "event-1", "Down", "BUY", 0.15, 2.0),
        ProfileOrder("grid", "event-1", "Down", "BUY", 0.25, 2.0),
    ]
    grid = reconstruct_profile_event(profile_key="grid", event_key="event-1", orders=grid_orders)
    scalper = reconstruct_profile_event(
        profile_key="scalper",
        event_key="event-1",
        orders=[
            ProfileOrder("scalper", "event-1", "Up", "BUY", 0.4, 10.0),
            ProfileOrder("scalper", "event-1", "Up", "SELL", 0.5, 10.0),
        ],
    )

    assert classify_account_type([outcome]).account_type == "outcome_predictor"
    assert classify_account_type([hedger]).account_type == "hedger"
    assert classify_account_type([grid]).account_type == "grid_buyer"
    assert classify_account_type([scalper]).account_type == "scalping_trader"


def test_recent_activity_does_not_affect_global_grade_pytest() -> None:
    metrics = [
        PeriodMetrics("1h", pnl_usd=100.0, win_rate=0.8, return_pct=20.0, trade_count=10),
        PeriodMetrics("1d", pnl_usd=400.0, win_rate=0.75, return_pct=18.0, trade_count=30),
        PeriodMetrics("7d", pnl_usd=800.0, win_rate=0.7, return_pct=15.0, trade_count=100),
        PeriodMetrics("30d", pnl_usd=1000.0, win_rate=0.7, return_pct=12.0, trade_count=200),
        PeriodMetrics("all_time", pnl_usd=2000.0, win_rate=0.68, return_pct=10.0, trade_count=500),
    ]

    inactive_grade = compute_global_grade(
        period_metrics=metrics,
        bot_frequency_score=0.9,
        crypto_event_coverage=0.9,
        recent_crypto_share=0.9,
        style_quality=0.9,
        recent_activity_score=0.0,
    )
    active_grade = compute_global_grade(
        period_metrics=metrics,
        bot_frequency_score=0.9,
        crypto_event_coverage=0.9,
        recent_crypto_share=0.9,
        style_quality=0.9,
        recent_activity_score=1.0,
    )

    assert inactive_grade.score == active_grade.score
    assert inactive_grade.grade == active_grade.grade
    assert inactive_grade.components["recent_activity_score_ignored"] == 0.0
    assert active_grade.components["recent_activity_score_ignored"] == 1.0


def test_generator_outputs_include_confidence_sources_and_low_quality_blocks_live_pytest() -> None:
    now = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    reconstruction = reconstruct_profile_event(
        profile_key="profile-1",
        event_key="event-1",
        orders=[
            ProfileOrder("profile-1", "event-1", "Up", "BUY", 0.4, 5.0, raw_activity_key="up-buy"),
            ProfileOrder("profile-1", "event-1", "Down", "BUY", 0.4, 5.0, raw_activity_key="down-buy"),
        ],
    )
    classification = classify_account_type([reconstruction])
    grade = compute_global_grade(
        period_metrics=[PeriodMetrics(period, pnl_usd=500.0, win_rate=0.8, return_pct=20.0, trade_count=20) for period in ("1h", "1d", "7d", "30d", "all_time")],
        bot_frequency_score=1.0,
        crypto_event_coverage=1.0,
        recent_crypto_share=1.0,
        style_quality=1.0,
    )
    usage = compute_usage_eligibility(
        last_activity_at_utc=now - timedelta(minutes=1),
        now_utc=now,
        active_most_last_hour=True,
        crypto_events_1h=2,
        crypto_events_24h=12,
        style_eligible=True,
        grade=grade.grade,
        reconstruction_quality=reconstruction.reconstruction_quality,
    )

    outputs = build_generator_outputs(
        profile_key="profile-1",
        classification=classification,
        reconstructions=[reconstruction],
        grade=grade,
        usage=usage,
        buying_ahead_rows=[{"raw_activity_key": "up-buy", "seconds_before_event_start": 60}],
    )

    assert {output.generator_id for output in outputs} == {"outcome_expectation", "hedge_proportion", "buying_ahead"}
    for output in outputs:
        assert output.confidence > 0
        assert output.source_reconstruction_keys == (reconstruction.reconstruction_key,)
        assert output.usage_eligible is True
        assert output.can_emit_live is True

    low_quality_usage = compute_usage_eligibility(
        last_activity_at_utc=now - timedelta(minutes=1),
        now_utc=now,
        active_most_last_hour=True,
        crypto_events_1h=2,
        crypto_events_24h=12,
        style_eligible=True,
        grade="S++",
        reconstruction_quality=0.2,
    )
    blocked_outputs = build_generator_outputs(
        profile_key="profile-1",
        classification=classification,
        reconstructions=[reconstruction],
        grade=grade,
        usage=low_quality_usage,
    )

    assert low_quality_usage.usage_eligible is False
    assert low_quality_usage.reason == "low_reconstruction_quality"
    assert all(output.can_emit_live is False for output in blocked_outputs)
