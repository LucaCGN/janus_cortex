from __future__ import annotations

from pathlib import Path

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies.pipeline_queue import (
    PIPELINE_PHASE_HISTORICAL_BACKTEST,
    PIPELINE_PHASE_LIVE_CANDIDATE,
    PIPELINE_PHASE_RECENT_SHADOW_SAMPLE,
    PIPELINE_PHASE_SHADOW_REPLAY,
    claim_strategy_pipeline_work,
    complete_strategy_pipeline_work,
    enqueue_strategy_pipeline_work,
    pipeline_queue_summary,
)
from crypto_options_app.strategies.manager import sync_strategy_registry
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.workers import strategy_pipeline_queue_worker as queue_worker
from crypto_options_app.workers.strategy_pipeline_scheduler import _action_payload


def test_strategy_registry_sync_enqueues_initial_backtest_work_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-sync.sqlite")

    with connect(db_path) as conn:
        counts = sync_strategy_registry(conn)
        expected_count = len(all_strategy_specs())

        assert counts["strategy_pipeline_queue"] == expected_count
        assert count_rows(conn, "strategy_pipeline_queue") == expected_count
        summary = pipeline_queue_summary(conn)

    assert summary["by_status"] == {"queued": expected_count}
    assert summary["by_phase"] == {PIPELINE_PHASE_HISTORICAL_BACKTEST: expected_count}
    assert summary["active_by_status"] == {"queued": expected_count}
    assert summary["active_by_phase"] == {PIPELINE_PHASE_HISTORICAL_BACKTEST: expected_count}


def test_strategy_registry_sync_does_not_requeue_advanced_strategy_versions_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-advanced.sqlite")
    advanced_spec = all_strategy_specs()[0]

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_promotion_state(
                promotion_key, strategy_id, strategy_version, promotion_state,
                updated_at_utc, evidence_json
            )
            VALUES(?, ?, ?, 'SHADOW_READY', '2026-06-07T21:55:00Z', '{}')
            """,
            (
                f"{advanced_spec.strategy_id}:{advanced_spec.strategy_version}",
                advanced_spec.strategy_id,
                advanced_spec.strategy_version,
            ),
        )
        counts = sync_strategy_registry(conn)
        expected_count = len(all_strategy_specs()) - 1

        assert counts["strategy_pipeline_queue"] == expected_count
        assert count_rows(conn, "strategy_pipeline_queue") == expected_count


def test_strategy_pipeline_queue_claim_and_complete_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-claim.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_strategy_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_HISTORICAL_BACKTEST,
            reason="pytest",
            priority=1,
        )
        claimed = claim_strategy_pipeline_work(conn, owner="pytest-worker", lease_seconds=120)
        assert len(claimed) == 1
        assert claimed[0]["queue_status"] == "running"
        assert claimed[0]["attempt_count"] == 1
        complete_strategy_pipeline_work(
            conn,
            claimed[0]["queue_key"],
            queue_status="done",
            result={"status": "executed"},
            run_id="pytest-run",
        )
        summary = pipeline_queue_summary(conn)

    assert summary["by_status"] == {"done": 1}
    assert summary["active_by_status"] == {}
    assert summary["active_by_phase"] == {}


def test_strategy_pipeline_queue_does_not_starve_historical_with_recurring_recent_shadow_pytest(
    tmp_path: Path,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-fairness.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="priority_recent_shadow",
            strategy_version="v1",
            phase=PIPELINE_PHASE_RECENT_SHADOW_SAMPLE,
            reason="pytest_recent_shadow",
            priority=-44,
        )
        claimed = claim_strategy_pipeline_work(conn, owner="pytest-worker", lease_seconds=120)
        assert claimed[0]["phase"] == PIPELINE_PHASE_RECENT_SHADOW_SAMPLE
        complete_strategy_pipeline_work(
            conn,
            claimed[0]["queue_key"],
            queue_status="queued",
            result={"status": "executed", "passed_count": 1},
            run_id="pytest-recent-shadow",
            next_run_after_seconds=0,
        )
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="fresh_historical",
            strategy_version="v1",
            phase=PIPELINE_PHASE_HISTORICAL_BACKTEST,
            reason="pytest_historical",
            priority=5,
        )
        next_claimed = claim_strategy_pipeline_work(conn, owner="pytest-worker", lease_seconds=120)

    assert next_claimed[0]["phase"] == PIPELINE_PHASE_HISTORICAL_BACKTEST
    assert next_claimed[0]["strategy_id"] == "fresh_historical"


def test_strategy_pipeline_queue_worker_drains_one_item_and_enqueues_next_phase_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-worker.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_strategy_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_HISTORICAL_BACKTEST,
            reason="pytest",
            priority=1,
        )

    def fake_run_backtest_action(planned, *, config, run_id):
        return planned | {
            "status": "executed",
            "run_id": f"{run_id}:pytest",
            "passed_count": 1,
            "blocked_count": 0,
            "live_submission_attempted": False,
            "manual_orders_avoided": True,
        }

    def fake_load_promotion_summary(db_path, *, refresh):
        return {
            "by_promotion_state": {"BACKTEST_READY": 1},
            "strategies": [
                {
                    "strategy_id": "pytest_strategy_v1",
                    "strategy_version": "v1",
                    "promotion_state": "BACKTEST_READY",
                }
            ],
        }

    monkeypatch.setattr(queue_worker, "_run_backtest_action", fake_run_backtest_action)
    monkeypatch.setattr(queue_worker, "_load_promotion_summary", fake_load_promotion_summary)

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker",
            max_items=1,
            backfill_from_promotions=False,
            refresh_promotions=False,
            report_dir=tmp_path / "reports-drains-one",
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 1
    assert payload["executed_action_count"] == 1
    with connect(db_path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT phase, queue_status
                FROM strategy_pipeline_queue
                ORDER BY phase
                """
            ).fetchall()
        ]

    assert {row["phase"]: row["queue_status"] for row in rows} == {
        PIPELINE_PHASE_HISTORICAL_BACKTEST: "done",
        PIPELINE_PHASE_SHADOW_REPLAY: "queued",
    }


def test_strategy_pipeline_queue_worker_blocks_empty_backtest_selector_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-empty-selector.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_strategy_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_HISTORICAL_BACKTEST,
            reason="pytest",
            priority=1,
        )

    def fake_run_backtest_action(planned, *, config, run_id):
        return _action_payload(
            planned,
            payload={
                "run_id": f"{run_id}:pytest-empty",
                "scenario_count": 0,
                "result_count": 0,
                "passed_count": 0,
                "blocked_count": 0,
                "blockers": {"scenario_selector": ["no_matching_scenarios"]},
                "manual_orders_avoided": True,
            },
        )

    def fake_load_promotion_summary(db_path, *, refresh):
        return {
            "by_promotion_state": {"NEEDS_BACKTEST": 1},
            "strategies": [
                {
                    "strategy_id": "pytest_strategy_v1",
                    "strategy_version": "v1",
                    "promotion_state": "NEEDS_BACKTEST",
                }
            ],
        }

    monkeypatch.setattr(queue_worker, "_run_backtest_action", fake_run_backtest_action)
    monkeypatch.setattr(queue_worker, "_load_promotion_summary", fake_load_promotion_summary)

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker-empty-selector",
            max_items=1,
            backfill_from_promotions=False,
            refresh_promotions=False,
            report_dir=tmp_path / "reports-empty-selector",
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 1
    assert payload["executed_action_count"] == 0
    assert payload["actions"][0]["status"] == "blocked"
    assert payload["actions"][0]["blockers"] == ["scenario_selector:no_matching_scenarios"]


def test_strategy_pipeline_queue_worker_runs_live_candidate_without_chat_approval_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-live-candidate.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_live_candidate_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_LIVE_CANDIDATE,
            reason="pytest_live_candidate",
            priority=1,
        )

    def fake_live_candidate_action(planned, *, config):
        return planned | {
            "status": "executed",
            "run_id": "pytest-live-candidate",
            "passed_count": 1,
            "blocked_count": 0,
            "live_submission_attempted": True,
            "manual_orders_avoided": True,
        }

    def fake_load_promotion_summary(db_path, *, refresh):
        return {
            "by_promotion_state": {"LIVE_CANDIDATE": 1},
            "strategies": [
                {
                    "strategy_id": "pytest_live_candidate_v1",
                    "strategy_version": "v1",
                    "promotion_state": "LIVE_CANDIDATE",
                }
            ],
        }

    monkeypatch.setattr(queue_worker, "_live_candidate_action", fake_live_candidate_action)
    monkeypatch.setattr(queue_worker, "_load_promotion_summary", fake_load_promotion_summary)

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker-live-candidate",
            max_items=1,
            backfill_from_promotions=False,
            refresh_promotions=False,
            report_dir=tmp_path / "reports-live-candidate",
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 1
    assert payload["executed_action_count"] == 1
    assert payload["live_submission_attempted"] is True
    assert payload["manual_orders_avoided"] is True
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT phase, queue_status
            FROM strategy_pipeline_queue
            WHERE strategy_id = ?
            """,
            ("pytest_live_candidate_v1",),
        ).fetchone()

    assert row["phase"] == PIPELINE_PHASE_LIVE_CANDIDATE
    assert row["queue_status"] == "done"


def test_strategy_pipeline_queue_worker_requeues_live_candidate_market_context_blockers_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-live-candidate-retry.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_live_candidate_retry_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_LIVE_CANDIDATE,
            reason="pytest_live_candidate",
            priority=1,
        )

    def fake_live_candidate_action(planned, *, config):
        return planned | {
            "status": "blocked",
            "run_id": "pytest-live-candidate-retry",
            "passed_count": 0,
            "blocked_count": 1,
            "blockers": [
                "hedge_floor_inversion_intensity_low",
                "option_path_rebound_flips_low",
            ],
            "live_submission_attempted": False,
            "manual_orders_avoided": True,
        }

    def fake_load_promotion_summary(db_path, *, refresh):
        return {
            "by_promotion_state": {"LIVE_CANDIDATE": 1},
            "strategies": [
                {
                    "strategy_id": "pytest_live_candidate_retry_v1",
                    "strategy_version": "v1",
                    "promotion_state": "LIVE_CANDIDATE",
                }
            ],
        }

    monkeypatch.setattr(queue_worker, "_live_candidate_action", fake_live_candidate_action)
    monkeypatch.setattr(queue_worker, "_load_promotion_summary", fake_load_promotion_summary)

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker-live-candidate-retry",
            max_items=1,
            backfill_from_promotions=False,
            refresh_promotions=False,
            recent_shadow_retry_seconds=120,
            report_dir=tmp_path / "reports-live-candidate-retry",
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 1
    assert payload["executed_action_count"] == 0
    assert payload["actions"][0]["status"] == "blocked"
    assert payload["actions"][0]["queue_status_after_action"] == "queued"
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT queue_status, next_run_after_utc, last_result_json
            FROM strategy_pipeline_queue
            WHERE strategy_id = ?
            """,
            ("pytest_live_candidate_retry_v1",),
        ).fetchone()

    assert row["queue_status"] == "queued"
    assert row["next_run_after_utc"] is not None
    assert "hedge_floor_inversion_intensity_low" in row["last_result_json"]


def test_strategy_pipeline_queue_worker_limits_live_candidates_to_three_slots_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-live-slots.sqlite")
    strategy_ids = [f"pytest_live_candidate_v{index}" for index in range(1, 5)]

    with connect(db_path) as conn:
        for strategy_id in strategy_ids:
            enqueue_strategy_pipeline_work(
                conn,
                strategy_id=strategy_id,
                strategy_version="v1",
                phase=PIPELINE_PHASE_LIVE_CANDIDATE,
                reason="pytest_live_candidate",
                priority=1,
            )

    executed: list[str] = []

    def fake_live_candidate_action(planned, *, config):
        strategy_id = planned["strategy_ids"][0]
        executed.append(strategy_id)
        return planned | {
            "status": "executed",
            "run_id": f"pytest-live-candidate:{strategy_id}",
            "passed_count": 1,
            "blocked_count": 0,
            "live_submission_attempted": True,
            "manual_orders_avoided": True,
        }

    def fake_load_promotion_summary(db_path, *, refresh):
        return {
            "by_promotion_state": {"LIVE_CANDIDATE": len(strategy_ids)},
            "strategies": [
                {
                    "strategy_id": strategy_id,
                    "strategy_version": "v1",
                    "promotion_state": "LIVE_CANDIDATE",
                }
                for strategy_id in strategy_ids
            ],
        }

    monkeypatch.setattr(queue_worker, "_live_candidate_action", fake_live_candidate_action)
    monkeypatch.setattr(queue_worker, "_load_promotion_summary", fake_load_promotion_summary)

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker-live-slots",
            max_items=4,
            max_parallel_live_candidates=4,
            max_live_strategy_slots=3,
            backfill_from_promotions=False,
            refresh_promotions=False,
            report_dir=tmp_path / "reports-live-slots",
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 4
    assert payload["executed_action_count"] == 3
    assert len(executed) == 3
    assert payload["live_submission_attempted"] is True
    skipped = [action for action in payload["actions"] if action.get("status") == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["blockers"] == ["live_strategy_slot_capacity_reached"]
    with connect(db_path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT strategy_id, phase, queue_status
                FROM strategy_pipeline_queue
                ORDER BY strategy_id, phase
                """
            ).fetchall()
        ]

    live_rows = [row for row in rows if row["phase"] == PIPELINE_PHASE_LIVE_CANDIDATE]
    shadow_rows = [row for row in rows if row["phase"] == PIPELINE_PHASE_RECENT_SHADOW_SAMPLE]
    live_status_by_strategy = {row["strategy_id"]: row["queue_status"] for row in live_rows}
    assert sum(1 for strategy_id in executed if live_status_by_strategy[strategy_id] == "done") == 3
    assert sum(1 for row in live_rows if row["queue_status"] == "queued") == 1
    assert len(shadow_rows) == 1
    assert shadow_rows[0]["queue_status"] == "queued"


def test_strategy_pipeline_queue_worker_keeps_live_candidates_queued_when_slots_are_full_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-live-slots-full.sqlite")

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_waiting_live_candidate_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_LIVE_CANDIDATE,
            reason="pytest_live_candidate",
            priority=1,
        )

    monkeypatch.setattr(
        queue_worker,
        "active_live_position_summary",
        lambda conn: {
            "active_live_position_count": 3,
            "active_live_strategy_count": 3,
            "active_live_open_cost_usd": 30.0,
            "active_live_latest_updated_at_utc": "2026-06-08T12:00:00+00:00",
            "active_live_position_keys": [],
            "active_live_strategies": ["live_a", "live_b", "live_c"],
        },
    )
    monkeypatch.setattr(
        queue_worker,
        "active_live_order_summary",
        lambda conn: {
            "active_live_order_count": 0,
            "active_live_order_latest_updated_at_utc": None,
            "active_live_order_keys": [],
            "active_live_order_strategies": [],
        },
    )
    monkeypatch.setattr(
        queue_worker,
        "_live_candidate_action",
        lambda planned, *, config: (_ for _ in ()).throw(AssertionError("live candidate should not run")),
    )
    monkeypatch.setattr(
        queue_worker,
        "_load_promotion_summary",
        lambda db_path, *, refresh: {
            "by_promotion_state": {"LIVE_CANDIDATE": 1},
            "strategies": [
                {
                    "strategy_id": "pytest_waiting_live_candidate_v1",
                    "strategy_version": "v1",
                    "promotion_state": "LIVE_CANDIDATE",
                }
            ],
        },
    )

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker-live-slots-full",
            max_items=1,
            max_live_strategy_slots=3,
            backfill_from_promotions=False,
            refresh_promotions=False,
            report_dir=tmp_path / "reports-live-slots-full",
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 1
    assert payload["executed_action_count"] == 0
    assert payload["actions"][0]["status"] == "skipped"
    assert payload["actions"][0]["blockers"] == ["live_strategy_slot_capacity_reached"]
    assert payload["actions"][0]["live_slot_status"]["open_live_slots"] == 0
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT queue_status, next_run_after_utc
            FROM strategy_pipeline_queue
            WHERE strategy_id = ?
              AND phase = ?
            """,
            ("pytest_waiting_live_candidate_v1", PIPELINE_PHASE_LIVE_CANDIDATE),
        ).fetchone()
        shadow_row = conn.execute(
            """
            SELECT queue_status
            FROM strategy_pipeline_queue
            WHERE strategy_id = ?
              AND phase = ?
            """,
            ("pytest_waiting_live_candidate_v1", PIPELINE_PHASE_RECENT_SHADOW_SAMPLE),
        ).fetchone()

    assert row["queue_status"] == "queued"
    assert row["next_run_after_utc"] is not None
    assert shadow_row["queue_status"] == "queued"


def test_strategy_pipeline_queue_worker_skips_when_process_lock_is_active_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-pipeline-queue-worker-lock.sqlite")
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    lock_path = report_dir / "strategy_pipeline_queue_worker.lock"
    lock_path.write_text(
        '{"run_id":"already-running","created_at_utc":"2100-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )

    with connect(db_path) as conn:
        enqueue_strategy_pipeline_work(
            conn,
            strategy_id="pytest_waiting_strategy_v1",
            strategy_version="v1",
            phase=PIPELINE_PHASE_HISTORICAL_BACKTEST,
            reason="pytest",
            priority=1,
        )

    payload = queue_worker.run_strategy_pipeline_queue_worker(
        queue_worker.StrategyPipelineQueueWorkerConfig(
            db_path=db_path,
            run_id="pytest-queue-worker-locked",
            max_items=1,
            backfill_from_promotions=False,
            refresh_promotions=False,
            report_dir=report_dir,
            max_postgres_cpu_percent=9999.0,
        )
    )

    assert payload["claimed_count"] == 0
    assert payload["executed_action_count"] == 0
    assert payload["actions"][0]["phase"] == "process_lock"
    assert payload["actions"][0]["status"] == "skipped"
    assert payload["manual_orders_avoided"] is True
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT queue_status
            FROM strategy_pipeline_queue
            WHERE strategy_id = ?
            """,
            ("pytest_waiting_strategy_v1",),
        ).fetchone()

    assert row["queue_status"] == "queued"
