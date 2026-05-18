from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.build_risk_ladder_calibration import build_calibration, render_report, write_outputs


def test_build_calibration_keeps_tail_risk_review_only_pytest(tmp_path: Path) -> None:
    source_dir = _write_source_fixture(tmp_path)

    calibration = build_calibration(
        source_dir=source_dir,
        portfolio_value=100.0,
        generated_at_utc="2026-05-18T11:03:48Z",
    )

    assert calibration["schema_version"] == "risk_ladder_calibration_v1"
    assert calibration["execution_authorized"] is False
    assert calibration["live_order_impact"] == "none"
    assert calibration["tradable_profile"]["levels"] == ["A", "B", "C", "S"]
    assert calibration["tradable_profile"]["weighted_return"] > 0
    assert calibration["destructive_profile"]["levels"] == ["D", "U"]
    assert calibration["destructive_profile"]["weighted_return"] < 0
    assert calibration["janus_db_performance"]["linked_fills_total"] == 7
    assert calibration["drawdown_analysis"]["all_events"]["max_drawdown_usd"] == 5.0
    assert calibration["drawdown_analysis"]["tradable_profile"]["max_drawdown_usd"] == 2.0
    assert calibration["drawdown_analysis"]["destructive_profile"]["worst_event"]["event_slug"] == "nba-c"

    blockers = {item["reason"] for item in calibration["recommendation"]["promotion_blockers"]}
    assert "sparse_janus_linked_fills" in blockers
    assert "autonomous_janus_sample_negative" in blockers
    assert "destructive_d_u_profile" in blockers

    zero_profit = calibration["ladder_samples"][0]
    assert zero_profit["realized_profit_usd"] == 0.0
    assert zero_profit["scenario_s_tail_candidate"]["tail_risk_budget_usd"] == 0.0
    assert zero_profit["scenario_d_blocked"]["blocked"] is True

    profit_50 = next(item for item in calibration["ladder_samples"] if item["realized_profit_usd"] == 50.0)
    assert profit_50["scenario_s_tail_candidate"]["tail_risk_budget_usd"] > 0
    assert profit_50["scenario_d_blocked"]["tail_risk_budget_usd"] == 0.0


def test_write_outputs_emits_artifact_and_report_pytest(tmp_path: Path) -> None:
    source_dir = _write_source_fixture(tmp_path)
    calibration = build_calibration(
        source_dir=source_dir,
        portfolio_value=100.0,
        generated_at_utc="2026-05-18T11:03:48Z",
    )

    paths = write_outputs(
        calibration,
        output_dir=tmp_path / "out",
        report_path=tmp_path / "risk_ladder_calibration_2026-05-18.md",
    )

    artifact = Path(paths["artifact_path"])
    report = Path(paths["report_path"])
    assert artifact.exists()
    assert report.exists()
    assert json.loads(artifact.read_text(encoding="utf-8"))["execution_authorized"] is False
    text = report.read_text(encoding="utf-8")
    assert "trading authority: `review_only_no_risk_promotion`" in text
    assert "No live-money path ran" in text


def test_render_report_includes_default_ladder_pytest(tmp_path: Path) -> None:
    source_dir = _write_source_fixture(tmp_path)
    calibration = build_calibration(
        source_dir=source_dir,
        portfolio_value=100.0,
        generated_at_utc="2026-05-18T11:03:48Z",
    )

    report = render_report(calibration)

    assert "| 0-20% | 3.0% | 10.0% | disabled |" in report
    assert "`D/U` profiles stay mechanically blocked" in report
    assert "account path drawdown" in report


def _write_source_fixture(tmp_path: Path) -> Path:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "scenario_stats.json").write_text(
        json.dumps(
            {
                "A": {
                    "events": 34,
                    "wins": 30,
                    "buy_notional": 149.8492,
                    "net": 37.5257,
                    "weighted_return": 0.250423,
                    "win_rate": 0.882353,
                },
                "B": {
                    "events": 19,
                    "wins": 14,
                    "buy_notional": 180.0871,
                    "net": 39.3721,
                    "weighted_return": 0.218628,
                    "win_rate": 0.736842,
                },
                "C": {
                    "events": 2,
                    "wins": 2,
                    "buy_notional": 8.145,
                    "net": 1.01,
                    "weighted_return": 0.124002,
                    "win_rate": 1.0,
                },
                "D": {
                    "events": 27,
                    "wins": 0,
                    "buy_notional": 180.1539,
                    "net": -159.7854,
                    "weighted_return": -0.886938,
                    "win_rate": 0.0,
                },
                "S": {
                    "events": 17,
                    "wins": 14,
                    "buy_notional": 128.8184,
                    "net": 52.7043,
                    "weighted_return": 0.409136,
                    "win_rate": 0.823529,
                },
                "U": {
                    "events": 4,
                    "wins": 0,
                    "buy_notional": 82.6584,
                    "net": -52.2136,
                    "weighted_return": -0.631679,
                    "win_rate": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    (source_dir / "janus_db_system_performance.json").write_text(
        json.dumps(
            {
                "db_scope": "portfolio.orders joined to portfolio.trades only",
                "orders_total": 85,
                "linked_fills_total": 7,
                "janus_strict": {
                    "fill_rows": 6,
                    "buy_notional": 8.25,
                    "sell_notional": 5.1,
                    "net_cashflow": -3.15,
                    "weighted_return": -0.381818,
                },
                "janus_plus_codex_assisted": {
                    "fill_rows": 7,
                    "buy_notional": 8.25,
                    "sell_notional": 6.25,
                    "net_cashflow": -2.0,
                    "weighted_return": -0.242424,
                },
            }
        ),
        encoding="utf-8",
    )
    with (source_dir / "basketball_event_trade_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "event_slug",
                "title",
                "first_ts",
                "context_coverage",
                "scenario_level",
                "net_cashflow",
                "net_including_open_value",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "event_slug": "nba-a",
                "title": "A",
                "first_ts": "1",
                "context_coverage": "price_ledger_only",
                "scenario_level": "A",
                "net_cashflow": "3.0",
                "net_including_open_value": "3.0",
            }
        )
        writer.writerow(
            {
                "event_slug": "nba-b",
                "title": "B",
                "first_ts": "2",
                "context_coverage": "price_ledger_only",
                "scenario_level": "B",
                "net_cashflow": "-2.0",
                "net_including_open_value": "-2.0",
            }
        )
        writer.writerow(
            {
                "event_slug": "nba-c",
                "title": "C",
                "first_ts": "3",
                "context_coverage": "price_ledger_only",
                "scenario_level": "D",
                "net_cashflow": "-3.0",
                "net_including_open_value": "-3.0",
            }
        )
        writer.writerow(
            {
                "event_slug": "nba-d",
                "title": "D",
                "first_ts": "4",
                "context_coverage": "local_pbp/report",
                "scenario_level": "S",
                "net_cashflow": "4.0",
                "net_including_open_value": "4.0",
            }
        )
    return source_dir
