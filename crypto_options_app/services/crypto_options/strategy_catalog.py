from __future__ import annotations

from typing import Any


def crypto_options_strategy_catalog() -> list[dict[str, Any]]:
    """Return the first research strategy set for 5m crypto up/down markets."""

    return [
        {
            "strategy_id": "early_directional_hold_30s",
            "family": "directional_prediction",
            "position_lifecycle": "buy_outcome_then_hold_to_settlement",
            "premise": "Use the first 30 seconds of BTC movement to choose Up/Down before the market fully reprices.",
            "minimum_data": ["Chainlink/reference labels", "exchange candles or ticks", "executable Polymarket ask"],
            "primary_features": ["return_15s", "return_30s", "momentum_3", "distance_to_start", "spread"],
            "core_metrics": ["net_ev", "win_rate", "max_sequential_losses", "sample_return_min", "latency_sensitivity"],
            "promotion_risk": "High false-confidence risk if scored with midpoint or random event splits.",
        },
        {
            "strategy_id": "four_minute_continuation_hold",
            "family": "directional_prediction",
            "position_lifecycle": "buy_outcome_then_hold_to_settlement",
            "premise": "If BTC remains on one side or trends consistently through minute four, the final minute may continue.",
            "minimum_data": ["reference labels", "1s or 15s BTC path", "executable ask near 60s remaining"],
            "primary_features": ["return_4m", "distance_sigma", "time_to_close_seconds", "volatility"],
            "core_metrics": ["net_ev_after_fees", "late_reversal_rate", "max_drawdown", "loss_streak_p95"],
            "promotion_risk": "Likely crowded near settlement and very sensitive to execution latency.",
        },
        {
            "strategy_id": "fair_value_gap_taker",
            "family": "hybrid",
            "position_lifecycle": "buy_mispriced_side_then_hold_or_exit",
            "premise": "Estimate fair probability from distance, time, volatility, and regime, then buy only when fair probability exceeds executable price plus costs.",
            "minimum_data": ["reference labels", "exchange candles/ticks", "Polymarket bid/ask/depth", "fee model"],
            "primary_features": ["distance_sigma", "time_to_close_seconds", "rv_1m", "rv_5m", "spread", "depth_top3"],
            "core_metrics": ["calibration_error", "brier", "net_ev", "coverage", "sample_return_min"],
            "promotion_risk": "Overfitting probability thresholds or using stale exchange data.",
        },
        {
            "strategy_id": "fakeout_reversal_opening_range",
            "family": "mean_reversion",
            "position_lifecycle": "buy_reversal_side_then_exit_or_hold",
            "premise": "A break outside the initial range that re-enters can signal a failed breakout in 5m BTC direction contracts.",
            "minimum_data": ["sub-minute BTC high/low", "opening range fields", "reference labels", "executable ask"],
            "primary_features": ["opening_range_high", "opening_range_low", "reentry_flag", "rsi", "zscore"],
            "core_metrics": ["false_breakout_hit_rate", "net_ev", "avg_loss", "max_sequential_losses"],
            "promotion_risk": "Needs tick-level path; candles alone can fabricate sequence order.",
        },
        {
            "strategy_id": "polymarket_odds_momentum_scalp",
            "family": "microstructure_scalping",
            "position_lifecycle": "buy_and_sell_before_settlement",
            "premise": "Never redeem; trade short bursts of outcome-token odds momentum when spread and depth allow entry and exit.",
            "minimum_data": ["Polymarket order book stream", "trade prints", "bid/ask/depth", "latency assumptions"],
            "primary_features": ["odds_delta", "spread", "depth_top1", "depth_top3", "book_churn"],
            "core_metrics": ["fill_rate", "partial_fill_rate", "net_return", "edge_decay_by_latency", "profit_factor"],
            "promotion_risk": "Midpoint-only backtests are not executable; queue position dominates.",
        },
        {
            "strategy_id": "passive_fair_value_maker",
            "family": "microstructure_market_making",
            "position_lifecycle": "post_passive_quotes_then_exit_or_expire",
            "premise": "Quote below fair value only when adverse-selection risk and time-to-close are acceptable.",
            "minimum_data": ["event-level order book", "fills/trades", "queue proxy", "fair probability model"],
            "primary_features": ["fair_prob", "bid_gap", "ask_gap", "depth_ahead", "cancel_churn", "time_to_close_seconds"],
            "core_metrics": ["fill_rate", "adverse_selection_loss", "maker_ev", "inventory_time", "drawdown"],
            "promotion_risk": "Cannot be validated without fill probability and queue modelling.",
        },
        {
            "strategy_id": "binary_pair_sum_arb",
            "family": "relative_value",
            "position_lifecycle": "buy_both_sides_when_sum_under_one",
            "premise": "If executable Up ask plus Down ask is below one after fees, buy both for settlement convergence.",
            "minimum_data": ["synchronized Up/Down asks", "fees", "minimum size", "depth"],
            "primary_features": ["up_ask", "down_ask", "sum_ask", "top_of_book_depth", "fees"],
            "core_metrics": ["arb_count", "net_locked_ev", "fill_ratio", "partial_fill_risk"],
            "promotion_risk": "Usually tiny and can disappear after partial fills, fees, or stale books.",
        },
        {
            "strategy_id": "no_trade_filter",
            "family": "risk_control",
            "position_lifecycle": "do_not_trade_unless_gates_pass",
            "premise": "The most valuable model may be the classifier that blocks bad regimes and non-executable setups.",
            "minimum_data": ["all candidate strategy metrics", "spread/depth", "latency stress"],
            "primary_features": ["spread_bucket", "vol_regime", "confidence_bucket", "latency_ms", "sample_drawdown"],
            "core_metrics": ["coverage", "avoided_loss", "blocked_false_positive_rate", "shadow_readiness"],
            "promotion_risk": "Over-filtering can leave too few trades to validate.",
        },
    ]


__all__ = ["crypto_options_strategy_catalog"]
