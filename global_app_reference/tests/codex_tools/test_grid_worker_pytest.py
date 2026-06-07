from __future__ import annotations

import json
from datetime import UTC, datetime

from codex_tools.polymarket import (
    GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION,
    GRID_WORKER_HEARTBEAT_SCHEMA_VERSION,
    GRID_WORKER_LADDER_SCHEMA_VERSION,
    GRID_WORKER_STATUS_SCHEMA_VERSION,
    build_grid_worker_status,
    build_grid_worker_config,
    build_grid_worker_ladder_plan,
    load_grid_worker_config,
    run_grid_worker_dry_run_tick,
)
from codex_tools.polymarket.cli import main as polymarket_cli_main
from codex_tools.polymarket.grid_worker import GRID_WORKER_LEDGER_ENTRY_SCHEMA_VERSION


def test_grid_worker_ladder_plan_builds_inert_sell_and_rebuy_legs_pytest() -> None:
    plan = build_grid_worker_ladder_plan(
        {
            "market_slug": "aliens-confirmed-2026",
            "title": "Will aliens be confirmed in 2026?",
            "token_id": "alien-yes",
            "side": "YES",
            "size": "12.5",
            "current_price": "0.20",
            "lower_band_price": "0.17",
            "upper_band_price": "0.23",
            "volatility_band_percent": "30",
        },
        now_utc=datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
        grid_step_cents=1,
        max_ladder_legs_per_side=2,
    )

    assert plan.schema_version == GRID_WORKER_LADDER_SCHEMA_VERSION
    assert plan.status == "ladder_plan_ready"
    assert plan.order_preparation_attempted is False
    assert plan.order_submission_attempted is False
    assert plan.leg_count == 4
    assert [(leg["side"], leg["limit_price"]) for leg in plan.legs] == [
        ("sell", "0.21"),
        ("buy", "0.19"),
        ("sell", "0.22"),
        ("buy", "0.18"),
    ]
    assert {leg["order_preparation_allowed"] for leg in plan.legs} == {False}
    assert {leg["order_submission_allowed"] for leg in plan.legs} == {False}
    assert "No order was placed" in plan.no_execution_statement


def test_grid_worker_ladder_plan_blocks_below_volatility_threshold_pytest() -> None:
    plan = build_grid_worker_ladder_plan(
        {
            "market_slug": "ai-model-benchmark-x",
            "token_id": "ai-yes",
            "size": "8",
            "current_price": "0.42",
            "volatility_band_percent": "4",
        },
        min_volatility_band_percent="10",
    )

    assert plan.status == "blocked_ladder_plan"
    assert plan.leg_count == 0
    assert plan.blockers == [
        {
            "reason": "volatility_band_below_grid_threshold",
            "volatility_band_percent": "4",
            "min_volatility_band_percent": "10",
        }
    ]
    assert plan.order_preparation_attempted is False
    assert plan.order_submission_attempted is False


def test_grid_worker_ladder_plan_blocks_missing_durable_inputs_pytest() -> None:
    plan = build_grid_worker_ladder_plan(
        {
            "market_slug": "missing-price",
            "token_id": "token-1",
            "size": "5",
        }
    )

    assert plan.status == "blocked_missing_ladder_inputs"
    assert plan.leg_count == 0
    assert plan.missing_inputs == ["current_price", "volatility_band_percent"]
    assert plan.order_preparation_attempted is False
    assert plan.order_submission_attempted is False


def test_grid_worker_ladder_plan_accepts_explicit_price_tick_pytest() -> None:
    plan = build_grid_worker_ladder_plan(
        {
            "market_slug": "low-price-sideways",
            "token_id": "low-yes",
            "side": "YES",
            "size": "10",
            "current_price": "0.045",
            "lower_band_price": "0.035",
            "upper_band_price": "0.055",
            "volatility_band_percent": "40",
        },
        grid_step_price="0.005",
        price_tick="0.005",
        max_ladder_legs_per_side=2,
    )

    assert plan.status == "ladder_plan_ready"
    assert plan.grid_step_price == "0.005"
    assert plan.price_tick == "0.005"
    assert [(leg["side"], leg["limit_price"]) for leg in plan.legs] == [
        ("sell", "0.050"),
        ("buy", "0.040"),
        ("sell", "0.055"),
        ("buy", "0.035"),
    ]
    assert plan.order_preparation_attempted is False
    assert plan.order_submission_attempted is False


def test_grid_worker_loads_config_and_reports_dry_run_status_pytest(tmp_path) -> None:
    config_path = tmp_path / "grid-worker-config.json"
    config_path.write_text(
        json.dumps(
            {
                "worker_id": "grid-worker-1",
                "enabled": True,
                "dry_run": True,
                "grid_step_cents": 1,
                "min_volatility_band_percent": "10",
                "max_ladder_legs_per_side": 2,
                "heartbeat_path": str(tmp_path / "heartbeat.json"),
                "candidates": [
                    {
                        "market_slug": "aliens-confirmed-2026",
                        "token_id": "alien-yes",
                        "size": "12.5",
                        "current_price": "0.20",
                        "lower_band_price": "0.17",
                        "upper_band_price": "0.23",
                        "volatility_band_percent": "30",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    config = load_grid_worker_config(config_path)
    status = build_grid_worker_status(
        config,
        now_utc=datetime(2026, 5, 30, 13, 0, 0, tzinfo=UTC),
    )

    assert config.worker_id == "grid-worker-1"
    assert config.order_preparation_allowed is False
    assert config.order_submission_allowed is False
    assert status.schema_version == GRID_WORKER_STATUS_SCHEMA_VERSION
    assert status.status == "dry_run_ready"
    assert status.config["candidate_count"] == 1
    assert status.order_preparation_attempted is False
    assert status.order_submission_attempted is False


def test_grid_worker_dry_run_tick_writes_heartbeat_and_plans_ladders_pytest(tmp_path) -> None:
    heartbeat_path = tmp_path / "handoffs" / "grid-worker-heartbeat.json"
    config = build_grid_worker_config(
        {
            "worker_id": "grid-worker-1",
            "enabled": True,
            "dry_run": True,
            "state": "idle",
            "heartbeat_path": str(heartbeat_path),
            "candidates": [
                {
                    "market_slug": "ai-model-benchmark-x",
                    "title": "Will an AI model pass benchmark X?",
                    "token_id": "ai-yes",
                    "side": "YES",
                    "size": "8",
                    "current_price": "0.42",
                    "lower_band_price": "0.39",
                    "upper_band_price": "0.45",
                    "volatility_band_percent": "14.2",
                }
            ],
        }
    )

    tick = run_grid_worker_dry_run_tick(
        config,
        now_utc=datetime(2026, 5, 30, 13, 5, 0, tzinfo=UTC),
    )

    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert tick.schema_version == GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION
    assert tick.status == "dry_run_tick_completed"
    assert tick.ok is True
    assert tick.plan_count == 1
    assert tick.ready_plan_count == 1
    assert tick.plans[0]["status"] == "ladder_plan_ready"
    assert tick.plans[0]["order_submission_attempted"] is False
    assert tick.state_transition["idempotency_key"] == tick.idempotency_key
    assert tick.heartbeat_write["status"] == "written"
    assert heartbeat["schema_version"] == GRID_WORKER_HEARTBEAT_SCHEMA_VERSION
    assert heartbeat["tick_id"] == tick.tick_id
    assert heartbeat["idempotency_key"] == tick.idempotency_key
    assert heartbeat["order_preparation_attempted"] is False
    assert heartbeat["order_submission_attempted"] is False


def test_grid_worker_dry_run_tick_suppresses_replayed_ledger_key_pytest(tmp_path) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    ledger_path = tmp_path / "grid-worker-ledger.jsonl"
    config = build_grid_worker_config(
        {
            "worker_id": "grid-worker-1",
            "enabled": True,
            "dry_run": True,
            "state": "idle",
            "heartbeat_path": str(heartbeat_path),
            "ledger_path": str(ledger_path),
            "candidates": [
                {
                    "market_slug": "ai-model-benchmark-x",
                    "title": "Will an AI model pass benchmark X?",
                    "token_id": "ai-yes",
                    "side": "YES",
                    "size": "8",
                    "current_price": "0.42",
                    "lower_band_price": "0.39",
                    "upper_band_price": "0.45",
                    "volatility_band_percent": "14.2",
                }
            ],
        }
    )

    first_tick = run_grid_worker_dry_run_tick(
        config,
        now_utc=datetime(2026, 5, 30, 13, 20, 0, tzinfo=UTC),
    )
    replay_tick = run_grid_worker_dry_run_tick(
        config,
        now_utc=datetime(2026, 5, 30, 13, 21, 0, tzinfo=UTC),
    )

    ledger_entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert first_tick.status == "dry_run_tick_completed"
    assert first_tick.replay_suppressed is False
    assert first_tick.ledger_write["status"] == "written"
    assert replay_tick.status == "dry_run_tick_replayed"
    assert replay_tick.replay_suppressed is True
    assert replay_tick.idempotency_key == first_tick.idempotency_key
    assert replay_tick.tick_id != first_tick.tick_id
    assert replay_tick.previous_state == "dry_run_completed"
    assert replay_tick.next_state == "dry_run_completed"
    assert replay_tick.state_transition["changed"] is False
    assert replay_tick.plan_count == 1
    assert replay_tick.plans == []
    assert replay_tick.ledger_read["matched_idempotency_key"] is True
    assert replay_tick.ledger_read["replay_of_tick_id"] == first_tick.tick_id
    assert replay_tick.ledger_write["status"] == "skipped"
    assert replay_tick.ledger_write["reason"] == "idempotency_key_already_recorded"
    assert len(ledger_entries) == 1
    assert ledger_entries[0]["schema_version"] == GRID_WORKER_LEDGER_ENTRY_SCHEMA_VERSION
    assert ledger_entries[0]["idempotency_key"] == first_tick.idempotency_key
    assert ledger_entries[0]["order_preparation_attempted"] is False
    assert ledger_entries[0]["order_submission_attempted"] is False
    assert heartbeat["status"] == "dry_run_tick_replayed"
    assert heartbeat["replay_suppressed"] is True


def test_grid_worker_tick_blocks_non_dry_run_config_without_plans_pytest() -> None:
    tick = run_grid_worker_dry_run_tick(
        {
            "worker_id": "grid-worker-live-request",
            "enabled": True,
            "dry_run": False,
            "candidates": [
                {
                    "token_id": "token-1",
                    "size": "5",
                    "current_price": "0.30",
                    "volatility_band_percent": "20",
                }
            ],
        },
        now_utc=datetime(2026, 5, 30, 13, 10, 0, tzinfo=UTC),
        write_heartbeat=False,
    )

    assert tick.status == "blocked_non_dry_run_not_supported"
    assert tick.ok is False
    assert tick.plan_count == 0
    assert tick.blockers == [
        {
            "reason": "non_dry_run_not_supported",
            "detail": "This worker slice is dry-run only and cannot prepare or submit orders.",
        }
    ]
    assert tick.order_preparation_attempted is False
    assert tick.order_submission_attempted is False


def test_polymarket_cli_grid_worker_dry_run_tick_outputs_status_and_heartbeat_pytest(tmp_path, capsys) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    config_path = tmp_path / "grid-worker-config.json"
    config_path.write_text(
        json.dumps(
            {
                "worker_id": "grid-worker-cli",
                "enabled": True,
                "dry_run": True,
                "heartbeat_path": str(heartbeat_path),
                "candidates": [
                    {
                        "market_slug": "openai-best-model-june-2026",
                        "token_id": "openai-yes",
                        "side": "YES",
                        "size": "5",
                        "current_price": "0.06",
                        "lower_band_price": "0.04",
                        "upper_band_price": "0.08",
                        "volatility_band_percent": "66.7",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = polymarket_cli_main(
        [
            "grid-worker-dry-run-tick",
            "--worker-config-json",
            str(config_path),
            "--now-utc",
            "2026-05-30T13:15:00Z",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema_version"] == GRID_WORKER_DRY_RUN_TICK_SCHEMA_VERSION
    assert payload["status"] == "dry_run_tick_completed"
    assert payload["order_preparation_attempted"] is False
    assert payload["order_submission_attempted"] is False
    assert heartbeat_path.exists()
