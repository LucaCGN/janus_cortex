from __future__ import annotations

from datetime import UTC, datetime, timedelta
import subprocess

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.trading.live_candidates import verify_live_market_candidate
from crypto_options_app.workers.signal_live_runner import (
    FIRST_SIX_SIGNAL_STRATEGY_IDS,
    EventSignalPlan,
    SignalLiveRunConfig,
    StrategySignalPlan,
    _profile_candidates_for_event,
    _preferred_profile_event_slug,
    _lane_stop_decisions_from_settlement_report,
    _projected_strategy_event_cost,
    _strategy_budget_cap_would_be_exceeded,
    _run_profile_monitor,
    _structural_order_type,
    _strategy_plan,
    _structural_slippage_cents,
    run_signal_live_test,
)


def test_signal_live_runner_executes_strategy_specific_candidates_pytest(tmp_path):
    calls = []
    events = iter(["event-a", "event-b"])

    def provider(_processed, config):
        event = next(events)
        plans = []
        for index, strategy_id in enumerate(config.strategy_ids):
            verification = verify_live_market_candidate(
                {
                    "token_id": f"token-{event}-{strategy_id}",
                    "event_key": event,
                    "event_token_key": f"{event}:{strategy_id}:up",
                    "event_slug": event,
                    "outcome": "Up",
                    "best_bid": 0.50,
                    "best_ask": 0.51,
                    "spread": 0.01,
                    "ask_size": 20.0,
                    "depth_top3_ask_size": 30.0,
                    "observed_at_utc": datetime.now(UTC).isoformat(),
                    "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                }
            )
            plans.append(
                StrategySignalPlan(
                    strategy_id=strategy_id,
                    verification=verification,
                    blockers=(),
                    signal_context={"signal_type": f"pytest-{index}", "profile_age_seconds": 1, "signal_age_seconds": 1},
                )
            )
        return EventSignalPlan(event_slug=event, monitor_artifact="pytest-monitor.json", plans=tuple(plans))

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": f"exchange-{len(calls)}"},
            "remote_order": {
                "id": f"exchange-{len(calls)}",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=FIRST_SIX_SIGNAL_STRATEGY_IDS,
            max_event_cycles=2,
            total_budget_cap_usd=15.0,
            poll_seconds=0.01,
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert len(result.event_results) == 2
    assert len(calls) == len(FIRST_SIX_SIGNAL_STRATEGY_IDS) * 2
    assert len({call["token_id"] for call in calls}) == len(calls)
    with connect(tmp_path / "signal.sqlite") as conn:
        assert count_rows(conn, "orders") == len(calls)
        assert count_rows(conn, "fills") == len(calls)
        assert count_rows(conn, "positions") == len(calls)
        assert count_rows(conn, "exit_plans") == len(calls)


def test_signal_live_runner_can_repeat_same_event_when_high_volume_mode_enabled_pytest(tmp_path):
    calls = []

    def provider(processed, config):
        assert "event-repeat" not in processed
        verification = verify_live_market_candidate(
            {
                "token_id": f"token-repeat-{len(calls)}",
                "event_key": "event-repeat",
                "event_token_key": "event-repeat:up",
                "event_slug": "event-repeat",
                "outcome": "Up",
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        )
        return EventSignalPlan(
            event_slug="event-repeat",
            monitor_artifact="pytest-monitor.json",
            plans=(
                StrategySignalPlan(
                    strategy_id="profile_hedge_scalping_v1",
                    verification=verification,
                    blockers=(),
                    signal_context={"signal_type": "repeat-pulse", "profile_age_seconds": 1, "signal_age_seconds": 1},
                ),
            ),
        )

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": f"exchange-repeat-{len(calls)}"},
            "remote_order": {
                "id": f"exchange-repeat-{len(calls)}",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-repeat",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=("profile_hedge_scalping_v1",),
            max_event_cycles=2,
            max_cycles_per_event_slug=2,
            min_repeat_event_seconds=0.0,
            total_budget_cap_usd=5.0,
            poll_seconds=0.01,
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert len(result.event_results) == 2
    assert [item.event_slug for item in result.event_results] == ["event-repeat", "event-repeat"]
    assert len(calls) == 2


def test_signal_live_runner_stops_on_target_filled_event_count_pytest(tmp_path):
    calls = []
    events = iter(["target-event-a", "target-event-b", "target-event-c"])

    def provider(_processed, _config):
        event = next(events)
        verification = verify_live_market_candidate(
            {
                "token_id": f"token-{event}",
                "event_key": event,
                "event_token_key": f"{event}:up",
                "event_slug": event,
                "outcome": "Up",
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        )
        return EventSignalPlan(
            event_slug=event,
            monitor_artifact="pytest-monitor.json",
            plans=(
                StrategySignalPlan(
                    strategy_id="event_context_outcome_v2",
                    verification=verification,
                    blockers=(),
                    signal_context={"signal_type": "target-filled", "profile_age_seconds": 1, "signal_age_seconds": 1},
                ),
            ),
        )

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": f"exchange-target-{len(calls)}"},
            "remote_order": {
                "id": f"exchange-target-{len(calls)}",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-target-filled",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=("event_context_outcome_v2",),
            max_event_cycles=5,
            target_filled_event_count=2,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert len(result.event_results) == 2
    assert len(calls) == 2


def test_signal_live_runner_records_no_fill_submit_error_without_stopping_bundle_pytest(tmp_path):
    calls = []
    events = iter(["nofill-event-a", "nofill-event-b"])

    def provider(_processed, _config):
        event = next(events)
        verification = verify_live_market_candidate(
            {
                "token_id": f"token-{event}",
                "event_key": event,
                "event_token_key": f"{event}:up",
                "event_slug": event,
                "outcome": "Up",
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        )
        return EventSignalPlan(
            event_slug=event,
            monitor_artifact="pytest-monitor.json",
            plans=(
                StrategySignalPlan(
                    strategy_id="event_context_outcome_v2",
                    verification=verification,
                    blockers=(),
                    signal_context={"signal_type": "nofill-continues", "profile_age_seconds": 1, "signal_age_seconds": 1},
                ),
            ),
        )

    def submitter(order_request):
        calls.append(order_request)
        if len(calls) == 1:
            return {
                "success": False,
                "raw": {"orderID": "exchange-nofill-1"},
                "remote_order": {
                    "id": "exchange-nofill-1",
                    "status": "submit_error",
                    "filledSize": "0",
                    "price": str(order_request["price"]),
                },
                "status": "submit_error",
                "filled_shares": 0.0,
                "remote_filled_shares": 0.0,
                "order_request": order_request,
            }
        return {
            "success": True,
            "raw": {"orderID": "exchange-filled-2"},
            "remote_order": {
                "id": "exchange-filled-2",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-nofill-continues",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=("event_context_outcome_v2",),
            max_event_cycles=2,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert len(result.event_results) == 2
    assert result.event_results[0].runtime_blockers == ()
    assert result.event_results[1].runtime_blockers == ()
    assert "submit_error_no_fill" not in result.blockers
    assert len(calls) == 2
    with connect(tmp_path / "signal.sqlite") as conn:
        assert count_rows(conn, "orders") == 2
        assert count_rows(conn, "fills") == 1
        assert count_rows(conn, "positions") == 1


def test_signal_live_runner_applies_manual_strategy_order_notional_pytest(tmp_path):
    calls = []

    def provider(_processed, _config):
        verification = verify_live_market_candidate(
            {
                "token_id": "token-profile-size",
                "event_key": "event-profile-size",
                "event_token_key": "event-profile-size:down",
                "event_slug": "event-profile-size",
                "outcome": "Down",
                "best_bid": 0.49,
                "best_ask": 0.50,
                "spread": 0.01,
                "ask_size": 25.0,
                "depth_top3_ask_size": 40.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        )
        return EventSignalPlan(
            event_slug="event-profile-size",
            monitor_artifact="pytest-monitor.json",
            plans=(
                StrategySignalPlan(
                    strategy_id="profile_hedge_scalping_v3",
                    verification=verification,
                    blockers=(),
                    signal_context={"signal_type": "manual-size", "profile_age_seconds": 1, "signal_age_seconds": 1},
                ),
            ),
        )

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": "exchange-sized"},
            "remote_order": {
                "id": "exchange-sized",
                "status": "FILLED",
                "filledSize": str(order_request["size"]),
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-manual-size",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=("profile_hedge_scalping_v3",),
            max_event_cycles=1,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
            strategy_order_notional_usd={"profile_hedge_scalping_v3": 2.5},
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert calls[0]["min_order_notional_usd"] == 2.5
    assert calls[0]["size"] == 5.0


def test_signal_live_runner_executes_rotation9_managed_variant_with_exit_plan_pytest(tmp_path):
    calls = []

    def provider(_processed, _config):
        verification = verify_live_market_candidate(
            {
                "token_id": "token-profile-v4",
                "event_key": "event-profile-v4",
                "event_token_key": "event-profile-v4:up",
                "event_slug": "event-profile-v4",
                "outcome": "Up",
                "best_bid": 0.49,
                "best_ask": 0.50,
                "spread": 0.01,
                "ask_size": 25.0,
                "depth_top3_ask_size": 40.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        )
        return EventSignalPlan(
            event_slug="event-profile-v4",
            monitor_artifact="pytest-monitor.json",
            plans=(
                StrategySignalPlan(
                    strategy_id="profile_hedge_scalping_v4",
                    verification=verification,
                    blockers=(),
                    signal_context={
                        "signal_type": "managed-v4",
                        "profile_age_seconds": 1,
                        "signal_age_seconds": 1,
                        "best_bid": 0.49,
                        "best_ask": 0.50,
                    },
                ),
            ),
        )

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": "exchange-managed-v4"},
            "remote_order": {
                "id": "exchange-managed-v4",
                "status": "FILLED",
                "filledSize": str(order_request["size"]),
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-managed-v4",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=("profile_hedge_scalping_v4",),
            max_event_cycles=1,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
            strategy_order_notional_usd={"profile_hedge_scalping_v4": 2.5},
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert calls[0]["order_type"] == "FAK"
    with connect(tmp_path / "signal.sqlite") as conn:
        assert count_rows(conn, "fills") == 1
        assert count_rows(conn, "exit_plans") == 1
        assert count_rows(conn, "exit_orders") >= 1


def test_strategy_event_projection_uses_manual_notional_pytest():
    verification = verify_live_market_candidate(
        {
            "token_id": "token-projection",
            "event_key": "event-projection",
            "event_token_key": "event-projection:up",
            "event_slug": "event-projection",
            "outcome": "Up",
            "best_bid": 0.49,
            "best_ask": 0.50,
            "spread": 0.01,
            "ask_size": 25.0,
            "depth_top3_ask_size": 40.0,
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        }
    )

    projected = _projected_strategy_event_cost(
        "profile_hedge_scalping_v3",
        verification,
        config=SignalLiveRunConfig(
            run_id="pytest-projection",
            strategy_order_notional_usd={"profile_hedge_scalping_v3": 2.5},
        ),
    )

    assert projected >= 2.6


def test_signal_live_runner_marks_missing_filled_event_target_as_blocked_pytest(tmp_path):
    calls = []
    events = iter(["unfilled-event-a", "unfilled-event-b"])

    def provider(_processed, _config):
        event = next(events)
        verification = verify_live_market_candidate(
            {
                "token_id": f"token-{event}",
                "event_key": event,
                "event_token_key": f"{event}:up",
                "event_slug": event,
                "outcome": "Up",
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        )
        return EventSignalPlan(
            event_slug=event,
            monitor_artifact="pytest-monitor.json",
            plans=(
                StrategySignalPlan(
                    strategy_id="event_context_outcome_v2",
                    verification=verification,
                    blockers=(),
                    signal_context={"signal_type": "target-unfilled", "profile_age_seconds": 1, "signal_age_seconds": 1},
                ),
            ),
        )

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": f"exchange-unfilled-{len(calls)}"},
            "remote_order": {
                "id": f"exchange-unfilled-{len(calls)}",
                "status": "OPEN",
                "filledSize": "0",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-target-unfilled",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=("event_context_outcome_v2",),
            max_event_cycles=2,
            target_filled_event_count=1,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
        ),
        signal_plan_provider=provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "blocked"
    assert len(result.event_results) == 2
    assert "filled_event_target_not_reached" in result.blockers


def test_signal_live_runner_records_valid_signal_blockers_without_submission_pytest(tmp_path):
    def provider(_processed, config):
        return EventSignalPlan(
            event_slug="event-blocked",
            monitor_artifact="pytest-monitor.json",
            plans=tuple(
                StrategySignalPlan(strategy_id=strategy_id, verification=None, blockers=("no_strategy_signal",), signal_context={})
                for strategy_id in config.strategy_ids
            ),
        )

    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id="pytest-signal-live-blocked",
            db_path=tmp_path / "signal.sqlite",
            artifact_root=tmp_path / "artifacts",
            strategy_ids=FIRST_SIX_SIGNAL_STRATEGY_IDS[:2],
            max_event_cycles=1,
            max_wall_seconds=0.01,
            poll_seconds=0.01,
        ),
        signal_plan_provider=provider,
        submitter=lambda _request: {"success": True},
        sleep=lambda _seconds: None,
    )

    assert result.status == "blocked"
    assert result.estimated_spent_usd == 0.0
    assert "signal_live_wall_clock_limit_reached" in result.blockers


def test_signal_live_runner_supports_hedger_and_grid_signal_plans_pytest(monkeypatch):
    row = {
        "event_slug": "event-hedge",
        "event_key": "event-hedge",
        "event_token_key": "event-hedge:up",
        "token_id": "token-hedge",
        "outcome": "Up",
        "best_bid": 0.49,
        "best_ask": 0.50,
        "spread": 0.01,
        "ask_size": 20,
        "depth_top3_ask_size": 30,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "support_weight": 10,
        "monitor_quote_age_seconds": 1,
        "supporting_signals": [
            {
                "profile_grade": "S++",
                "profile_score": 100,
                "profile_trading_style": "hedger",
                "profile_trading_style_detail": "grid_buyer",
                "action_side": "BUY",
                "effective_outcome": "Up",
                "age_seconds": 2,
            }
        ],
    }
    monkeypatch.setattr("crypto_options_app.workers.signal_live_runner._refresh_profile_candidate_quote", lambda candidate: candidate)

    grid_plan = _strategy_plan(
        "grid_buyer_band_rebound_v1",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )
    hedge_plan = _strategy_plan(
        "hedger_ratio_replication_v1",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert grid_plan.verification is not None and grid_plan.verification.verified
    assert hedge_plan.verification is not None and hedge_plan.verification.verified
    assert grid_plan.blockers == ()
    assert hedge_plan.blockers == ()
    assert grid_plan.signal_context["signal_type"] == "band_rebound_minimal_probe"
    assert hedge_plan.signal_context["signal_type"] == "hedge_proportion_minimal_probe"


def test_signal_live_runner_distinguishes_cashout_from_hold_signal_plans_pytest(monkeypatch):
    row = {
        "event_slug": "event-outcome",
        "event_key": "event-outcome",
        "event_token_key": "event-outcome:up",
        "token_id": "token-outcome",
        "outcome": "Up",
        "best_bid": 0.49,
        "best_ask": 0.50,
        "spread": 0.01,
        "ask_size": 20,
        "depth_top3_ask_size": 30,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "supporting_signals": [
            {
                "profile_grade": "S++",
                "profile_score": 98,
                "profile_trading_style": "outcome_predictor",
                "profile_trading_style_detail": "outcome_predictor",
                "action_side": "BUY",
                "effective_outcome": "Up",
                "age_seconds": 2,
            }
        ],
    }
    monkeypatch.setattr("crypto_options_app.workers.signal_live_runner._refresh_profile_candidate_quote", lambda candidate: candidate)

    cashout_plan = _strategy_plan(
        "s_tier_outcome_consensus_cashout_v1",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )
    hold_plan = _strategy_plan(
        "s_tier_outcome_hold_to_settlement_v1",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert cashout_plan.verification is not None and cashout_plan.verification.verified
    assert hold_plan.verification is not None and hold_plan.verification.verified
    assert cashout_plan.signal_context["signal_type"] == "s_tier_outcome_cashout"
    assert hold_plan.signal_context["signal_type"] == "s_tier_outcome_hold_to_settlement"


def test_signal_live_runner_supports_scalping_signal_plans_pytest(monkeypatch):
    row = {
        "event_slug": "event-scalp",
        "event_key": "event-scalp",
        "event_token_key": "event-scalp:down",
        "token_id": "token-scalp",
        "outcome": "Down",
        "best_bid": 0.48,
        "best_ask": 0.49,
        "spread": 0.01,
        "ask_size": 20,
        "depth_top3_ask_size": 30,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "support_weight": 10,
        "monitor_quote_age_seconds": 1,
        "supporting_signals": [
            {
                "profile_grade": "S+",
                "profile_score": 93,
                "profile_trading_style": "scalping_trader",
                "profile_trading_style_detail": "scalping_trader",
                "action_side": "BUY",
                "effective_outcome": "Down",
                "age_seconds": 2,
            }
        ],
    }
    monkeypatch.setattr("crypto_options_app.workers.signal_live_runner._refresh_profile_candidate_quote", lambda candidate: candidate)

    hedge_scalp_plan = _strategy_plan(
        "profile_hedge_scalping_v1",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )
    volatility_plan = _strategy_plan(
        "volatility_spread_scalping_probe_v1",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert hedge_scalp_plan.verification is not None and hedge_scalp_plan.verification.verified
    assert volatility_plan.verification is not None and volatility_plan.verification.verified
    assert hedge_scalp_plan.blockers == ()
    assert volatility_plan.blockers == ()
    assert hedge_scalp_plan.signal_context["signal_type"] == "profile_hedge_scalping_minimal_probe"
    assert volatility_plan.signal_context["signal_type"] == "volatility_liquidity_minimal_probe"


def test_hedger_v2_uses_a_fallback_and_event_context_quote_pytest():
    event_context = verify_live_market_candidate(
        {
            "token_id": "token-event-context",
            "event_key": "event-hedge-v2",
            "event_token_key": "event-hedge-v2:up",
            "event_slug": "event-hedge-v2",
            "outcome": "Up",
            "best_bid": 0.51,
            "best_ask": 0.52,
            "spread": 0.01,
            "ask_size": 25,
            "depth_top3_ask_size": 40,
            "observed_at_utc": datetime.now(UTC).isoformat(),
        }
    )
    rows = [
        {
            "event_slug": "event-hedge-v2",
            "outcome": "Up",
            "supporting_signals": [
                {
                    "profile_grade": "A",
                    "profile_score": 88,
                    "profile_trading_style": "hedger",
                    "profile_trading_style_detail": "hedger",
                    "action_side": "BUY",
                    "effective_outcome": "Up",
                    "age_seconds": 3,
                }
            ],
        }
    ]

    plan = _strategy_plan(
        "hedger_ratio_replication_v2",
        event_context=event_context,
        profile_candidates=rows,
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert plan.verification is event_context
    assert plan.blockers == ()
    assert plan.signal_context["grade_pool"] == "A_fallback"
    assert plan.signal_context["signal_type"] == "hedge_proportion_v2_volume_probe"
    assert plan.signal_context["executable_quote_source"] == "event_context_fallback"


def test_profile_hedge_scalping_v2_accepts_high_grade_outcome_flow_pytest(monkeypatch):
    row = {
        "event_slug": "event-profile-scalp-v2",
        "event_key": "event-profile-scalp-v2",
        "event_token_key": "event-profile-scalp-v2:down",
        "token_id": "token-profile-scalp-v2",
        "outcome": "Down",
        "best_bid": 0.47,
        "best_ask": 0.48,
        "spread": 0.01,
        "ask_size": 20,
        "depth_top3_ask_size": 30,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "support_weight": 10,
        "monitor_quote_age_seconds": 1,
        "supporting_signals": [
            {
                "profile_grade": "S+",
                "profile_score": 94,
                "profile_trading_style": "outcome_predictor",
                "profile_trading_style_detail": "outcome_predictor",
                "action_side": "BUY",
                "effective_outcome": "Down",
                "age_seconds": 2,
            }
        ],
    }
    monkeypatch.setattr("crypto_options_app.workers.signal_live_runner._refresh_profile_candidate_quote", lambda candidate: candidate)

    plan = _strategy_plan(
        "profile_hedge_scalping_v2",
        event_context=verify_live_market_candidate(row),
        profile_candidates=[row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert plan.verification is not None and plan.verification.verified
    assert plan.blockers == ()
    assert plan.signal_context["grade_pool"] == "S_or_better"
    assert plan.signal_context["signal_type"] == "profile_hedge_scalping_v2_volume_probe"


def test_profile_hedge_scalping_v3_carries_same_symbol_outcome_pressure_pytest():
    event_context = verify_live_market_candidate(
        {
            "token_id": "token-current-btc-down",
            "event_key": "btc-updown-5m-1780520100",
            "event_token_key": "btc-updown-5m-1780520100:down",
            "event_slug": "btc-updown-5m-1780520100",
            "outcome": "Down",
            "best_bid": 0.49,
            "best_ask": 0.50,
            "spread": 0.01,
            "ask_size": 20,
            "depth_top3_ask_size": 30,
            "observed_at_utc": datetime.now(UTC).isoformat(),
        }
    )
    previous_event_row = {
        "event_slug": "btc-updown-5m-1780519800",
        "event_key": "btc-updown-5m-1780519800",
        "outcome": "Down",
        "support_weight": 40,
        "conflict_weight": 5,
        "supporting_signals": [
            {
                "profile_grade": "S++",
                "profile_score": 99,
                "profile_trading_style": "grid_buyer",
                "profile_trading_style_detail": "grid_buyer",
                "action_side": "BUY",
                "effective_outcome": "Down",
                "age_seconds": 12,
            }
        ],
    }

    plan = _strategy_plan(
        "profile_hedge_scalping_v3",
        event_context=event_context,
        profile_candidates=[],
        broad_profile_candidates=[previous_event_row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert plan.verification is event_context
    assert plan.blockers == ()
    assert plan.signal_context["cross_event_profile_pressure"] is True
    assert plan.signal_context["volume_adjustment"] == "same_symbol_same_outcome_profile_pressure_carry_forward"


def test_managed_profile_v4_rejects_extreme_conflicted_event_context_entry_pytest():
    event_context = verify_live_market_candidate(
        {
            "token_id": "token-current-btc-down",
            "event_key": "btc-updown-5m-1780554600",
            "event_token_key": "btc-updown-5m-1780554600:down",
            "event_slug": "btc-updown-5m-1780554600",
            "outcome": "Down",
            "best_bid": 0.97,
            "best_ask": 0.98,
            "spread": 0.01,
            "ask_size": 20,
            "depth_top3_ask_size": 30,
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        }
    )
    previous_event_row = {
        "event_slug": "btc-updown-5m-1780554300",
        "event_key": "btc-updown-5m-1780554300",
        "outcome": "Down",
        "support_weight": 199.7,
        "conflict_weight": 96.6,
        "supporting_signals": [
            {
                "profile_grade": "S++",
                "profile_score": 99,
                "profile_trading_style": "hedger",
                "profile_trading_style_detail": "hedger",
                "action_side": "BUY",
                "effective_outcome": "Down",
                "age_seconds": 12,
            }
        ],
    }

    plan = _strategy_plan(
        "profile_hedge_scalping_v4",
        event_context=event_context,
        profile_candidates=[],
        broad_profile_candidates=[previous_event_row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert plan.verification is None
    assert "managed_profile_pressure_conflict_too_high" in plan.blockers
    assert "managed_profile_entry_extreme_ask_with_conflict" in plan.blockers
    assert "managed_cashout_rebuy_entry_price_too_high" in plan.blockers


def test_grid_buyer_band_rebound_v2_carries_same_symbol_grid_pressure_pytest():
    event_context = verify_live_market_candidate(
        {
            "token_id": "token-current-eth-up",
            "event_key": "eth-updown-5m-1780520100",
            "event_token_key": "eth-updown-5m-1780520100:up",
            "event_slug": "eth-updown-5m-1780520100",
            "outcome": "Up",
            "best_bid": 0.49,
            "best_ask": 0.50,
            "spread": 0.01,
            "ask_size": 20,
            "depth_top3_ask_size": 30,
            "observed_at_utc": datetime.now(UTC).isoformat(),
        }
    )
    previous_event_row = {
        "event_slug": "eth-updown-5m-1780519800",
        "event_key": "eth-updown-5m-1780519800",
        "outcome": "Up",
        "support_weight": 25,
        "conflict_weight": 3,
        "supporting_signals": [
            {
                "profile_grade": "S+",
                "profile_score": 92,
                "profile_trading_style": "grid_buyer",
                "profile_trading_style_detail": "grid_buyer",
                "action_side": "BUY",
                "effective_outcome": "Up",
                "age_seconds": 9,
            }
        ],
    }

    plan = _strategy_plan(
        "grid_buyer_band_rebound_v2",
        event_context=event_context,
        profile_candidates=[],
        broad_profile_candidates=[previous_event_row],
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert plan.verification is event_context
    assert plan.blockers == ()
    assert plan.signal_context["cross_event_profile_pressure"] is True
    assert plan.signal_context["signal_type"] == "band_rebound_v2_carry_forward_probe"
    assert plan.signal_context["volume_adjustment"] == "same_symbol_same_outcome_grid_pressure_carry_forward"


def test_volatility_scalping_can_use_event_context_quote_fallback_pytest():
    event_context = verify_live_market_candidate(
        {
            "token_id": "token-executable",
            "event_key": "event-scalp",
            "event_token_key": "event-scalp:up",
            "event_slug": "event-scalp",
            "outcome": "Up",
            "best_bid": 0.5,
            "best_ask": 0.51,
            "spread": 0.01,
            "ask_size": 25,
            "depth_top3_ask_size": 40,
            "observed_at_utc": datetime.now(UTC).isoformat(),
        }
    )
    rows = [
        {
            "event_slug": "event-scalp",
            "outcome": "Up",
            "supporting_signals": [
                {
                    "profile_grade": "S++",
                    "profile_score": 96,
                    "profile_trading_style": "scalping_trader",
                    "profile_trading_style_detail": "scalping_trader",
                    "action_side": "BUY",
                    "effective_outcome": "Up",
                    "age_seconds": 3,
                }
            ],
        }
    ]

    plan = _strategy_plan(
        "volatility_spread_scalping_probe_v1",
        event_context=event_context,
        profile_candidates=rows,
        monitor_artifact="monitor.json",
        used_token_ids=set(),
    )

    assert plan.verification is event_context
    assert plan.blockers == ()
    assert plan.signal_context["executable_quote_source"] == "event_context_fallback"
    assert plan.signal_context["signal_type"] == "volatility_liquidity_minimal_probe"


def test_profile_candidates_fall_back_to_observed_candidates_pytest():
    monitor = {
        "eligible_manual_candidates": [],
        "observed_candidates": [
            {
                "event_slug": "event-profile",
                "outcome": "Up",
                "manual_execution_ticket": {"token_id": "token-profile"},
                "quote_at_utc": datetime.now(UTC).isoformat(),
                "supporting_signals": [
                    {
                        "profile_grade": "S++",
                        "profile_trading_style": "hedger",
                        "profile_trading_style_detail": "grid_buyer",
                        "action_side": "BUY",
                        "effective_outcome": "Up",
                    }
                ],
            }
        ],
    }

    rows = _profile_candidates_for_event(monitor, event_slug="event-profile")

    assert len(rows) == 1
    assert rows[0]["token_id"] == "token-profile"
    assert rows[0]["event_key"] == "event-profile"
    assert rows[0]["event_token_key"] == "event-profile:up"


def test_profile_candidates_merge_aggregated_signals_with_observed_quotes_pytest():
    now = datetime.now(UTC).isoformat()
    monitor = {
        "eligible_manual_candidates": [],
        "observed_candidates": [
            {
                "event_slug": "event-profile",
                "outcome": "Down",
                "token_id": "token-down",
                "best_ask": 0.42,
                "best_bid": 0.41,
                "spread": 0.01,
                "ask_size": 20,
                "depth_top3_ask_size": 40,
                "quote_at_utc": now,
                "window_end_time": datetime.now(UTC).replace(microsecond=0).isoformat(),
            }
        ],
        "profile_signal_report": {
            "aggregated_candidates": [
                {
                    "event_slug": "event-profile",
                    "effective_outcome": "Down",
                    "supporting_signals": [
                        {
                            "profile_grade": "S++",
                            "profile_trading_style": "hedger",
                            "profile_trading_style_detail": "grid_buyer",
                            "action_side": "BUY",
                            "effective_outcome": "Down",
                        }
                    ],
                }
            ]
        },
    }

    rows = _profile_candidates_for_event(monitor, event_slug="event-profile")
    merged = [row for row in rows if row.get("supporting_signals")][0]

    assert merged["token_id"] == "token-down"
    assert merged["best_ask"] == 0.42
    assert merged["event_token_key"] == "event-profile:down"


def test_profile_candidates_split_mixed_aggregates_before_quote_merge_pytest():
    now = datetime.now(UTC).isoformat()
    monitor = {
        "eligible_manual_candidates": [],
        "observed_candidates": [
            {
                "event_slug": "event-profile",
                "outcome": "Up",
                "token_id": "token-up",
                "best_ask": 0.52,
                "best_bid": 0.51,
                "spread": 0.01,
                "ask_size": 20,
                "depth_top3_ask_size": 40,
                "quote_at_utc": now,
            },
            {
                "event_slug": "event-profile",
                "outcome": "Down",
                "token_id": "token-down",
                "best_ask": 0.49,
                "best_bid": 0.48,
                "spread": 0.01,
                "ask_size": 20,
                "depth_top3_ask_size": 40,
                "quote_at_utc": now,
            },
        ],
        "profile_signal_report": {
            "aggregated_candidates": [
                {
                    "event_slug": "event-profile",
                    "supporting_signals": [
                        {
                            "profile_grade": "S++",
                            "profile_trading_style": "hedger",
                            "profile_trading_style_detail": "grid_buyer",
                            "action_side": "BUY",
                            "effective_outcome": "Up",
                        },
                        {
                            "profile_grade": "S++",
                            "profile_trading_style": "hedger",
                            "profile_trading_style_detail": "grid_buyer",
                            "action_side": "BUY",
                            "effective_outcome": "Down",
                        },
                    ],
                }
            ]
        },
    }

    rows = _profile_candidates_for_event(monitor, event_slug="event-profile")
    by_token = {row.get("token_id"): row for row in rows if row.get("supporting_signals")}

    assert by_token["token-up"]["outcome"] == "Up"
    assert by_token["token-up"]["best_ask"] == 0.52
    assert by_token["token-down"]["outcome"] == "Down"
    assert by_token["token-down"]["best_ask"] == 0.49


def test_preferred_profile_event_slug_falls_back_to_observed_candidates_pytest():
    monitor = {
        "eligible_manual_candidates": [],
        "observed_candidates": [
            {"event_slug": "event-observed-old"},
            {"event_slug": "event-observed-new"},
        ],
    }

    assert _preferred_profile_event_slug(monitor, processed_events={"event-observed-old"}, min_seconds_remaining=75.0) == "event-observed-new"


def test_preferred_profile_event_slug_prefers_executable_observed_rows_pytest():
    monitor = {
        "eligible_manual_candidates": [],
        "observed_candidates": [
            {"event_slug": "event-missing-quote", "best_ask": None, "spread": None},
            {
                "event_slug": "event-executable",
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20,
                "depth_top3_ask_size": 30,
            },
        ],
    }

    assert _preferred_profile_event_slug(monitor, processed_events=set(), min_seconds_remaining=75.0) == "event-executable"


def test_preferred_profile_event_slug_skips_nearly_expired_rows_pytest():
    now = datetime.now(UTC)
    monitor = {
        "eligible_manual_candidates": [],
        "observed_candidates": [
            {
                "event_slug": "event-late",
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20,
                "depth_top3_ask_size": 30,
                "window_end_time": now.isoformat(),
            },
            {
                "event_slug": "event-fresh",
                "best_ask": 0.52,
                "spread": 0.01,
                "ask_size": 20,
                "depth_top3_ask_size": 30,
                "time_remaining_seconds": 180,
            },
        ],
    }

    assert _preferred_profile_event_slug(monitor, processed_events=set(), min_seconds_remaining=75.0) == "event-fresh"


def test_profile_monitor_timeout_is_non_crashing_blocker_pytest(monkeypatch, tmp_path):
    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["profile-monitor"], timeout=180)

    monkeypatch.setattr("crypto_options_app.workers.signal_live_runner.subprocess.run", timeout_run)

    result = _run_profile_monitor(
        SignalLiveRunConfig(
            run_id="pytest-profile-timeout",
            artifact_root=tmp_path / "artifacts",
            db_path=tmp_path / "db.sqlite",
        )
    )

    assert result is None


def test_profile_monitor_uses_runner_min_remaining_seconds_pytest(monkeypatch, tmp_path):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "artifact_monitor=monitor.json\n"

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return Completed()

    monkeypatch.setattr("crypto_options_app.workers.signal_live_runner.subprocess.run", fake_run)

    result = _run_profile_monitor(
        SignalLiveRunConfig(
            run_id="pytest-profile-min-time",
            artifact_root=tmp_path / "artifacts",
            db_path=tmp_path / "db.sqlite",
            min_seconds_remaining=90.0,
        )
    )

    index = captured["cmd"].index("--min-time-remaining-seconds")
    assert captured["cmd"][index + 1] == "180.0"
    max_index = captured["cmd"].index("--max-time-remaining-seconds")
    assert captured["cmd"][max_index + 1] == "900"
    assert "--compact-live-artifact" in captured["cmd"]
    assert result == "monitor.json"


def test_structural_slippage_is_strategy_specific_pytest():
    assert _structural_slippage_cents("grid_buyer_band_rebound_v1") == 3.0
    assert _structural_slippage_cents("grid_buyer_band_rebound_v2") == 3.0
    assert _structural_slippage_cents("hedger_ratio_replication_v1") == 3.0
    assert _structural_slippage_cents("hedger_ratio_replication_v2") == 3.0
    assert _structural_slippage_cents("volatility_spread_scalping_probe_v1") == 3.0
    assert _structural_slippage_cents("profile_hedge_scalping_v1") == 1.0
    assert _structural_slippage_cents("profile_hedge_scalping_v2") == 2.0
    assert _structural_slippage_cents("event_context_outcome_v2") == 2.0


def test_structural_order_type_is_strategy_specific_pytest():
    assert _structural_order_type("grid_buyer_band_rebound_v1") == "FAK"
    assert _structural_order_type("grid_buyer_band_rebound_v2") == "FAK"
    assert _structural_order_type("hedger_ratio_replication_v1") == "FAK"
    assert _structural_order_type("hedger_ratio_replication_v2") == "FAK"
    assert _structural_order_type("volatility_spread_scalping_probe_v1") == "FAK"
    assert _structural_order_type("profile_hedge_scalping_v1") == "FOK"
    assert _structural_order_type("profile_hedge_scalping_v2") == "FAK"
    assert _structural_order_type("event_context_outcome_v2") == "FAK"


def test_lane_stop_decisions_require_loss_streak_pnl_and_win_rate_pytest():
    config = SignalLiveRunConfig(
        run_id="pytest-lane-stops",
        enable_lane_stop_gates=True,
        lane_loss_streak_limit=3,
        lane_stop_min_settled=3,
        lane_stop_max_win_rate=0.5,
        lane_stop_max_pnl_usd=0.0,
    )
    report = {
        "summary": {
            "by_strategy": {
                "bad_lane": {
                    "settled_position_count": 3,
                    "win_rate": 0.0,
                    "realized_pnl_usd": -3.0,
                },
                "mixed_lane": {
                    "settled_position_count": 4,
                    "win_rate": 0.5,
                    "realized_pnl_usd": -1.0,
                },
            }
        },
        "rows": [
            {"strategy_id": "bad_lane", "status": "settled", "pnl_usd": -1.0},
            {"strategy_id": "bad_lane", "status": "settled", "pnl_usd": -1.0},
            {"strategy_id": "bad_lane", "status": "settled", "pnl_usd": -1.0},
            {"strategy_id": "mixed_lane", "status": "settled", "pnl_usd": -1.0},
            {"strategy_id": "mixed_lane", "status": "settled", "pnl_usd": -1.0},
            {"strategy_id": "mixed_lane", "status": "settled", "pnl_usd": 1.0},
            {"strategy_id": "mixed_lane", "status": "settled", "pnl_usd": -1.0},
        ],
    }

    decisions = _lane_stop_decisions_from_settlement_report(report, config=config)

    assert set(decisions) == {"bad_lane"}
    assert decisions["bad_lane"]["gate"] == "lane_loss_streak_quality_stop"
    assert decisions["bad_lane"]["loss_streak"] == 3


def test_lane_stop_decisions_include_lane_only_pnl_and_win_rate_floors_pytest():
    config = SignalLiveRunConfig(
        run_id="pytest-lane-floor-stops",
        enable_lane_stop_gates=True,
        lane_stop_min_settled=3,
        lane_stop_min_realized_pnl_usd=-5.0,
        lane_stop_min_win_rate=0.30,
    )
    report = {
        "summary": {
            "by_strategy": {
                "pnl_floor_lane": {
                    "settled_position_count": 1,
                    "win_rate": 1.0,
                    "realized_pnl_usd": -5.01,
                },
                "win_rate_floor_lane": {
                    "settled_position_count": 3,
                    "win_rate": 0.25,
                    "realized_pnl_usd": 1.0,
                },
                "healthy_lane": {
                    "settled_position_count": 3,
                    "win_rate": 0.34,
                    "realized_pnl_usd": -1.0,
                },
            }
        },
        "rows": [],
    }

    decisions = _lane_stop_decisions_from_settlement_report(report, config=config)

    assert decisions["pnl_floor_lane"]["gate"] == "lane_realized_pnl_floor_stop"
    assert decisions["win_rate_floor_lane"]["gate"] == "lane_win_rate_floor_stop"
    assert "healthy_lane" not in decisions


def test_strategy_budget_projection_uses_per_lane_cap_pytest():
    verification = verify_live_market_candidate(
        {
            "token_id": "token-budget",
            "event_key": "event-budget",
            "event_token_key": "event-budget:up",
            "event_slug": "event-budget",
            "outcome": "Up",
            "best_bid": 0.50,
            "best_ask": 0.51,
            "spread": 0.01,
            "ask_size": 20.0,
            "depth_top3_ask_size": 30.0,
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "event_end_time_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        }
    )

    assert _strategy_budget_cap_would_be_exceeded(
        "lane",
        verification,
        {"lane": 19.75},
        SignalLiveRunConfig(run_id="pytest-budget", per_strategy_budget_cap_usd=20.0),
    )
    assert not _strategy_budget_cap_would_be_exceeded(
        "lane",
        verification,
        {"lane": 18.0},
        SignalLiveRunConfig(run_id="pytest-budget", per_strategy_budget_cap_usd=20.0),
    )
