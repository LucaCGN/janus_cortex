"""Crypto options/up-down research and replay foundation.

The package is intentionally lazy at import time. Read-only research modules
such as profile discovery should not require live execution dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "build_dynamic_family_candidate_decisions": ("crypto_options_app.pipelines.options.v2_candidates", "build_dynamic_family_candidate_decisions"),
    "build_event_labels": ("crypto_options_app.pipelines.options.labels", "build_event_labels"),
    "build_event_state_panel": ("crypto_options_app.pipelines.options.panels", "build_event_state_panel"),
    "build_exit_execution_decision": ("crypto_options_app.pipelines.options.exit_execution_policy", "build_exit_execution_decision"),
    "build_five_lane_system_package": ("crypto_options_app.pipelines.options.lane_system", "build_five_lane_system_package"),
    "build_indicator_frame": ("crypto_options_app.pipelines.options.indicators", "build_indicator_frame"),
    "build_lane_execution_packets": ("crypto_options_app.pipelines.options.lane_system", "build_lane_execution_packets"),
    "build_live_decision_review": ("crypto_options_app.pipelines.options.live_review", "build_live_decision_review"),
    "build_price_path_trace_report": ("crypto_options_app.pipelines.options.price_path_trace", "build_price_path_trace_report"),
    "build_profile_signal_monitor_tick": ("crypto_options_app.pipelines.options.profile_signal_monitor", "build_profile_signal_monitor_tick"),
    "build_profile_signal_report": ("crypto_options_app.pipelines.options.profile_signals", "build_profile_signal_report"),
    "build_research_report": ("crypto_options_app.pipelines.options.reporting", "build_research_report"),
    "build_specialized_candidate_decisions": ("crypto_options_app.pipelines.options.v2_candidates", "build_specialized_candidate_decisions"),
    "build_v2_candidate_comparison_report": ("crypto_options_app.pipelines.options.v2_reporting", "build_v2_candidate_comparison_report"),
    "build_v2_candidate_packets": ("crypto_options_app.pipelines.options.v2_candidates", "build_v2_candidate_packets"),
    "build_v2_decision_set": ("crypto_options_app.pipelines.options.v2_service", "build_v2_decision_set"),
    "build_v2_execution_packet_bundle": ("crypto_options_app.pipelines.options.v2_service", "build_v2_execution_packet_bundle"),
    "build_v2_supervised_live_run_protocol": ("crypto_options_app.pipelines.options.v2_live_protocol", "build_v2_supervised_live_run_protocol"),
    "compare_crypto_options_strategies": ("crypto_options_app.pipelines.options.backtests", "compare_crypto_options_strategies"),
    "compute_trade_metrics": ("crypto_options_app.pipelines.options.metrics", "compute_trade_metrics"),
    "default_promotion_policy": ("crypto_options_app.pipelines.options.promotion_policy", "default_promotion_policy"),
    "default_v2_candidate_configs": ("crypto_options_app.pipelines.options.v2_candidates", "default_v2_candidate_configs"),
    "dry_run_v2_launch_rehearsal": ("crypto_options_app.pipelines.options.v2_live_protocol", "dry_run_v2_launch_rehearsal"),
    "evaluate_crypto_options_data_sufficiency": ("crypto_options_app.pipelines.options.audit", "evaluate_crypto_options_data_sufficiency"),
    "evaluate_global_discovery_cap": ("crypto_options_app.pipelines.options.promotion_policy", "evaluate_global_discovery_cap"),
    "evaluate_promotion_state": ("crypto_options_app.pipelines.options.promotion_policy", "evaluate_promotion_state"),
    "make_statistical_signal": ("crypto_options_app.pipelines.options.statistical_signals", "make_statistical_signal"),
    "metric_contract": ("crypto_options_app.pipelines.options.metrics", "metric_contract"),
    "run_crypto_options_replay_engine": ("crypto_options_app.pipelines.options.engine", "run_crypto_options_replay_engine"),
    "run_registered_statistical_component_backtests": ("crypto_options_app.pipelines.options.statistical_components", "run_registered_statistical_component_backtests"),
    "run_registered_strategy_backtests": ("crypto_options_app.pipelines.options.strategies", "run_registered_strategy_backtests"),
    "run_statistical_signal_backtest": ("crypto_options_app.pipelines.options.statistical_signals", "run_statistical_signal_backtest"),
    "simulate_bucketed_cashout": ("crypto_options_app.pipelines.options.cashout_simulator", "simulate_bucketed_cashout"),
    "write_five_lane_system_artifacts": ("crypto_options_app.pipelines.options.lane_system", "write_five_lane_system_artifacts"),
    "write_lane_execution_packets": ("crypto_options_app.pipelines.options.lane_system", "write_lane_execution_packets"),
    "write_live_decision_review_artifacts": ("crypto_options_app.pipelines.options.live_review", "write_live_decision_review_artifacts"),
    "write_price_path_trace_artifacts": ("crypto_options_app.pipelines.options.price_path_trace", "write_price_path_trace_artifacts"),
    "write_profile_signal_artifacts": ("crypto_options_app.pipelines.options.profile_signals", "write_profile_signal_artifacts"),
    "write_research_artifacts": ("crypto_options_app.pipelines.options.reporting", "write_research_artifacts"),
    "write_v2_execution_packets": ("crypto_options_app.pipelines.options.v2_service", "write_v2_execution_packets"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


__all__ = sorted(_EXPORTS)
