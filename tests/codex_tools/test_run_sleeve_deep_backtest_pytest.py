from __future__ import annotations

import pandas as pd
import pytest

from codex_tool.run_sleeve_deep_backtest import _aggregate_family_summary, _recommendations


def test_aggregate_family_summary_groups_across_seeds_pytest() -> None:
    summary = pd.DataFrame(
        [
            {
                "cohort": "nba_regular",
                "sample_seed": 1,
                "live_sleeve_role": "core_hold",
                "strategy_family": "inversion",
                "trade_count": 3,
                "avg_return_with_slippage": 0.10,
                "win_rate": 2 / 3,
                "min_order_pnl_total": 1.20,
                "avg_entry_price": 0.40,
                "avg_exit_price": 0.44,
            },
            {
                "cohort": "nba_regular",
                "sample_seed": 2,
                "live_sleeve_role": "core_hold",
                "strategy_family": "inversion",
                "trade_count": 4,
                "avg_return_with_slippage": 0.20,
                "win_rate": 0.75,
                "min_order_pnl_total": 2.30,
                "avg_entry_price": 0.42,
                "avg_exit_price": 0.49,
            },
            {
                "cohort": "nba_regular",
                "sample_seed": 2,
                "live_sleeve_role": "grid_scalp",
                "strategy_family": "empty_family",
                "trade_count": 0,
                "avg_return_with_slippage": None,
                "win_rate": None,
                "min_order_pnl_total": 0.0,
                "avg_entry_price": None,
                "avg_exit_price": None,
            },
        ]
    )

    aggregate = _aggregate_family_summary(summary)

    assert len(aggregate) == 1
    row = aggregate.iloc[0].to_dict()
    assert row["cohort"] == "nba_regular"
    assert row["strategy_family"] == "inversion"
    assert row["live_sleeve_role"] == "core_hold"
    assert row["sample_rows"] == 2
    assert row["trade_count"] == 7
    assert row["avg_return_with_slippage"] == pytest.approx(0.15)
    assert row["min_order_pnl_total"] == pytest.approx(3.5)


def test_recommendations_use_aggregate_family_rows_pytest() -> None:
    summary = pd.DataFrame(
        [
            {
                "cohort": "nba_regular",
                "sample_seed": seed,
                "strategy_family": "winner_definition",
                "live_sleeve_role": "core_hold",
                "trade_count": 3,
                "win_rate": 0.67,
                "avg_return_with_slippage": 0.05,
                "min_order_pnl_total": 1.0,
                "avg_entry_price": 0.8,
                "avg_exit_price": 0.84,
            }
            for seed in (1, 2)
        ]
    )

    recommendations = _recommendations(summary, [])

    promotion = next(item for item in recommendations if item["area"] == "sleeve_promotion_candidates")
    top = promotion["top_rows"][0]
    assert top["sample_rows"] == 2
    assert top["trade_count"] == 6
    assert top["avg_win_rate"] == 0.67
