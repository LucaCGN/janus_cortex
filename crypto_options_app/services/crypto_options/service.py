from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.metrics import metric_contract
from crypto_options_app.pipelines.options.reporting import build_research_report
from crypto_options_app.services.crypto_options.data_sources import crypto_options_data_source_registry
from crypto_options_app.services.crypto_options.readiness import evaluate_crypto_options_readiness, readiness_gate_contract
from crypto_options_app.services.crypto_options.strategy_catalog import crypto_options_strategy_catalog


SERVICE_NAME = "crypto_options_research_service"


def build_crypto_options_service_report(
    *,
    events_df: pd.DataFrame,
    clob_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    reference_df: pd.DataFrame | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the isolated service artifact for issue #47."""

    report = build_research_report(
        events_df=events_df,
        clob_df=clob_df,
        candles_df=candles_df,
        reference_df=reference_df,
        generated_at=generated_at,
    )
    report["service"] = {
        "name": SERVICE_NAME,
        "schema_version": "crypto_options_research_service_v1",
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "isolation_boundary": {
            "separate_from_modules": [
                "app.data.pipelines.daily.nba",
                "app.data.pipelines.daily.wnba",
                "app.modules.agentic.basketball_logic",
                "app.modules.agentic.live_strategy_worker",
                "app.modules.agentic.global_portfolio",
            ],
            "not_wired_to_live_workers": True,
            "not_wired_to_portfolio_execution": True,
            "orders_allowed": False,
            "live_trading_authorized": False,
        },
        "strategy_catalog": crypto_options_strategy_catalog(),
        "metric_contract": metric_contract(),
        "readiness_gate_contract": readiness_gate_contract(),
        "data_source_registry": crypto_options_data_source_registry(),
        "runtime_mode": "research_backtest_only",
    }
    report["real_money_readiness"] = evaluate_crypto_options_readiness(report)
    report["shadow_testing_ready"] = bool(report["real_money_readiness"]["ready_for_shadow"])
    report["live_trading_authorized"] = False
    report["orders_allowed"] = False
    return report


__all__ = ["SERVICE_NAME", "build_crypto_options_service_report"]
