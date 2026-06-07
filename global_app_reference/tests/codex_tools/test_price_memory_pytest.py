from __future__ import annotations

import json
from datetime import UTC, datetime

from codex_tools.polymarket import (
    GRID_BACKTEST_SCHEMA_VERSION,
    GRID_VALIDATION_SCHEMA_VERSION,
    PRICE_MEMORY_COLLECTION_SCHEMA_VERSION,
    PRICE_MEMORY_FEATURE_SCHEMA_VERSION,
    build_grid_scalp_validation,
    build_price_memory_collection,
    build_price_memory_features,
    simulate_grid_backtest,
)
from codex_tools.polymarket.cli import main as polymarket_cli_main


def _oscillating_history() -> dict[str, object]:
    return {
        "history": [
            {"t": "2026-05-30T00:00:00Z", "p": "0.10"},
            {"t": "2026-05-30T04:00:00Z", "p": "0.16"},
            {"t": "2026-05-30T08:00:00Z", "p": "0.10"},
            {"t": "2026-05-30T12:00:00Z", "p": "0.16"},
            {"t": "2026-05-30T16:00:00Z", "p": "0.10"},
            {"t": "2026-05-31T00:00:00Z", "p": "0.16"},
        ]
    }


def test_price_memory_features_builds_window_ranges_pytest() -> None:
    features = build_price_memory_features(
        _oscillating_history(),
        now_utc=datetime(2026, 5, 31, 0, 0, 0, tzinfo=UTC),
    )

    assert features["schema_version"] == PRICE_MEMORY_FEATURE_SCHEMA_VERSION
    assert features["point_count"] == 6
    assert features["windows"]["1d"]["range_cents"] == "6"
    assert features["windows"]["1d"]["midpoint_cross_count"] >= 4
    assert features["order_preparation_attempted"] is False
    assert features["order_submission_attempted"] is False


def test_grid_backtest_detects_profitable_sideways_cycles_pytest() -> None:
    backtest = simulate_grid_backtest(
        _oscillating_history(),
        grid_step_price="0.05",
        leg_size="5",
        now_utc="2026-05-31T00:00:00Z",
    )

    assert backtest["schema_version"] == GRID_BACKTEST_SCHEMA_VERSION
    assert backtest["status"] == "positive_after_costs"
    assert backtest["completed_cycle_count"] >= 2
    assert backtest["profitable"] is True
    assert backtest["order_preparation_attempted"] is False
    assert backtest["order_submission_attempted"] is False


def test_grid_scalp_validation_emits_review_fields_pytest() -> None:
    validation = build_grid_scalp_validation(
        _oscillating_history(),
        premise_state="sideways_valid",
        spread_cents="1",
        depth_usd="50",
        grid_step_price="0.05",
        leg_size="5",
        now_utc="2026-05-31T00:00:00Z",
    )

    fields = validation["grid_eligibility_review_fields"]
    assert validation["schema_version"] == GRID_VALIDATION_SCHEMA_VERSION
    assert validation["status"] == "grid_validation_candidate"
    assert fields["validator_profile"] == "1d_5c_sideways_positive_backtest"
    assert fields["one_day_backtest_positive"] is True
    assert fields["recommended_worker_duration_hours"] == 24
    assert validation["order_preparation_attempted"] is False
    assert validation["order_submission_attempted"] is False


def test_polymarket_cli_price_memory_outputs_features_pytest(tmp_path, capsys) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps(_oscillating_history()), encoding="utf-8")

    exit_code = polymarket_cli_main(
        [
            "build-price-memory-features",
            "--price-history-json",
            str(history_path),
            "--now-utc",
            "2026-05-31T00:00:00Z",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema_version"] == PRICE_MEMORY_FEATURE_SCHEMA_VERSION
    assert payload["order_preparation_attempted"] is False


def test_price_memory_collection_features_current_past_and_candidate_rows_pytest() -> None:
    collection = build_price_memory_collection(
        {
            "open_positions": [
                {
                    "source": "current_slot",
                    "market_title": "Current global slot",
                    "market_slug": "current-global-slot",
                    "token_id": "token-current",
                }
            ],
            "closed_positions": [
                {
                    "source": "past_sleeve",
                    "market_title": "Past global sleeve",
                    "market_slug": "past-global-sleeve",
                    "token_id": "token-past",
                }
            ],
            "candidate_rows": [
                {
                    "source": "candidate",
                    "market_title": "Candidate global slot",
                    "market_slug": "candidate-global-slot",
                    "token_id": "token-candidate",
                }
            ],
        },
        price_history_by_token={
            "token-current": _oscillating_history(),
            "token-past": _oscillating_history(),
            "token-candidate": _oscillating_history(),
        },
        now_utc="2026-05-31T00:00:00Z",
    )

    assert collection["schema_version"] == PRICE_MEMORY_COLLECTION_SCHEMA_VERSION
    assert collection["featured_count"] == 3
    assert collection["blocked_count"] == 0
    assert {row["token_id"] for row in collection["snapshots"]} == {
        "token-current",
        "token-past",
        "token-candidate",
    }


def test_price_memory_collection_blocks_missing_token_without_failing_pytest() -> None:
    collection = build_price_memory_collection(
        [{"market_title": "Missing token", "market_slug": "missing-token"}],
        now_utc="2026-05-31T00:00:00Z",
    )

    assert collection["snapshot_count"] == 1
    assert collection["blocked_count"] == 1
    assert collection["snapshots"][0]["status"] == "blocked_missing_token_id"


def test_price_memory_collection_dedupes_by_token_pytest() -> None:
    collection = build_price_memory_collection(
        [
            {"market_title": "Duplicate A", "market_slug": "duplicate-a", "token_id": "token-dup"},
            {"market_title": "Duplicate B", "market_slug": "duplicate-b", "token_id": "token-dup"},
        ],
        price_history_by_token={"token-dup": _oscillating_history()},
        now_utc="2026-05-31T00:00:00Z",
    )

    assert collection["snapshot_count"] == 1
    assert collection["featured_count"] == 1
    assert "duplicate_market_row_skipped:token-dup" in collection["source_caveats"]


def test_polymarket_cli_collect_price_memory_outputs_collection_pytest(tmp_path, capsys) -> None:
    rows_path = tmp_path / "markets.json"
    history_path = tmp_path / "history_by_token.json"
    rows_path.write_text(
        json.dumps([{"market_title": "CLI market", "market_slug": "cli-market", "token_id": "token-cli"}]),
        encoding="utf-8",
    )
    history_path.write_text(json.dumps({"token-cli": _oscillating_history()}), encoding="utf-8")

    exit_code = polymarket_cli_main(
        [
            "collect-price-memory",
            "--market-rows-json",
            str(rows_path),
            "--price-history-by-token-json",
            str(history_path),
            "--now-utc",
            "2026-05-31T00:00:00Z",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema_version"] == PRICE_MEMORY_COLLECTION_SCHEMA_VERSION
    assert payload["featured_count"] == 1
