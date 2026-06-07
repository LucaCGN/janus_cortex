from __future__ import annotations

import json
from pathlib import Path

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.reports.settlement_performance import (
    reconcile_live_run_settlements,
    resolve_outcome_from_gamma_payload,
)


def test_resolve_outcome_from_gamma_final_price_and_outcome_prices_pytest() -> None:
    assert (
        resolve_outcome_from_gamma_payload({"eventMetadata": {"finalPrice": 99.0, "priceToBeat": 100.0}})
        == "Down"
    )
    assert (
        resolve_outcome_from_gamma_payload({"eventMetadata": {"finalPrice": 100.0, "priceToBeat": 100.0}})
        == "Up"
    )
    assert (
        resolve_outcome_from_gamma_payload(
            {"markets": [{"outcomes": "[\"Up\", \"Down\"]", "outcomePrices": "[\"0\", \"1\"]"}]}
        )
        == "Down"
    )


def test_reconcile_live_run_settlements_persists_pnl_and_lifecycle_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_path = tmp_path / "run.json"
    artifact_path.write_text(
        json.dumps(
            {
                "run_id": "run-settlement-1",
                "strategy_rows": [
                    {
                        "strategy_id": "hedger_ratio_replication_v1",
                        "status": "live_structural_executed",
                        "event_key": "btc-updown-5m-test",
                        "event_slug": "btc-updown-5m-test",
                        "event_token_key": "btc-updown-5m-test:up",
                        "outcome": "Up",
                        "order_key": "order-1",
                        "exchange_order_id": "0x1",
                        "order_status": "filled",
                        "filled_shares": 10,
                        "fill_price": 0.40,
                        "position_key": "position-1",
                    },
                    {
                        "strategy_id": "grid_buyer_band_rebound_v1",
                        "status": "live_structural_executed",
                        "event_key": "btc-updown-5m-test",
                        "event_slug": "btc-updown-5m-test",
                        "event_token_key": "btc-updown-5m-test:down",
                        "outcome": "Down",
                        "order_key": "order-2",
                        "exchange_order_id": "0x2",
                        "order_status": "filled",
                        "filled_shares": 5,
                        "fill_price": 0.70,
                        "position_key": "position-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO positions(position_key, strategy_id, event_token_key, shares, cost_basis_usd, status, opened_at_utc, updated_at_utc)
            VALUES('position-1', 'hedger_ratio_replication_v1', 'btc-updown-5m-test:up', 10, 4, 'open', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO positions(position_key, strategy_id, event_token_key, shares, cost_basis_usd, status, opened_at_utc, updated_at_utc)
            VALUES('position-2', 'grid_buyer_band_rebound_v1', 'btc-updown-5m-test:down', 5, 3.5, 'open', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO exit_plans(exit_plan_key, position_key, coverage_type, status, plan_json, created_at_utc, updated_at_utc)
            VALUES('exit-1', 'position-1', 'managed_exit_or_settlement', 'active', '{}', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO exit_plans(exit_plan_key, position_key, coverage_type, status, plan_json, created_at_utc, updated_at_utc)
            VALUES('exit-2', 'position-2', 'managed_exit_or_settlement', 'active', '{}', 'now', 'now')
            """
        )

    report = reconcile_live_run_settlements(
        run_artifact_path=artifact_path,
        db_path=db_path,
        report_dir=tmp_path / "reports",
        gamma_fetcher=lambda _slug: {"eventMetadata": {"finalPrice": 101.0, "priceToBeat": 100.0}},
    )

    assert report["status"] == "settled"
    assert report["summary"]["realized_pnl_usd"] == 2.5
    assert report["summary"]["wins"] == 1
    assert report["summary"]["losses"] == 1
    assert report["summary"]["by_strategy"]["hedger_ratio_replication_v1"]["realized_pnl_usd"] == 6.0
    assert report["summary"]["by_strategy"]["grid_buyer_band_rebound_v1"]["realized_pnl_usd"] == -3.5
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM event_outcomes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM pnl_snapshots").fetchone()[0] == 2
        assert {row[0] for row in conn.execute("SELECT status FROM positions")} == {"settled"}
        assert {row[0] for row in conn.execute("SELECT status FROM exit_plans")} == {"settled"}
