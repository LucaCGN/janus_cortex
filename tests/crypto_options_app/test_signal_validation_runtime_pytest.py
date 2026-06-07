from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from crypto_options_app import CryptoOptionsAppConfig, create_app
from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.signals.validation.result_store import (
    claim_next_queue_item,
    expire_stale_owned_queue_items,
    queue_status,
    sync_signal_catalog,
    validation_results,
    validation_status,
)
from crypto_options_app.workers.signal_design_reviewer import (
    SignalDesignReviewerConfig,
    run_signal_design_reviewer_once,
)
from crypto_options_app.workers.signal_validation_worker import (
    SignalValidationWorkerConfig,
    run_signal_validation_worker_batch,
    run_signal_validation_worker_once,
)


def test_signal_validation_schema_syncs_catalog_and_queue_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync = sync_signal_catalog(conn)
        status = validation_status(conn)
        queue = queue_status(conn)

        assert sync["signal_specs"] >= 20
        assert sync["signal_versions"] >= 20
        assert count_rows(conn, "signal_specs") == sync["signal_specs"]
        assert count_rows(conn, "signal_versions") == sync["signal_versions"]
        assert count_rows(conn, "signal_queue_items") == sync["signal_specs"]
        assert status["orders_allowed"] is False
        assert status["live_trading_authorized"] is False
        assert status["by_queue_status"]["QUEUED"] == sync["signal_specs"]
        assert status["review_queue"]["pending_review_request_count"] == 0
        assert status["integrity"]["signal_history_trust_state"] == "consistent"
        assert status["integrity"]["orphaned_run_count"] == 0
        assert queue["queue_count"] == sync["signal_specs"]
        signal_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3",),
        ).fetchone()
        queue_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_queue_items
             WHERE signal_id=?
               AND phase='last_week_backtest'
            """,
            ("master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3",),
        ).fetchone()
        assert signal_version_row is not None
        assert signal_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_microstructure_context_v2"
        )
        assert queue_row is not None
        assert queue_row["supersedes_signal_id"] == signal_version_row["supersedes_signal_id"]
        inversion_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_inversion_intensity_optionprice_forward_cashout_edge_clean_flip_density_v3",),
        ).fetchone()
        inversion_queue_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_queue_items
             WHERE signal_id=?
               AND phase='last_week_backtest'
            """,
            ("master_hedge_grid_scalping_inversion_intensity_optionprice_forward_cashout_edge_clean_flip_density_v3",),
        ).fetchone()
        profile_price_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_profile_price_context_v4",),
        ).fetchone()
        profile_price_queue_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_queue_items
             WHERE signal_id=?
               AND phase='last_week_backtest'
            """,
            ("master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_profile_price_context_v4",),
        ).fetchone()
        hedge_floor_price_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_hedge_floor_state_profiles_optionprice_profile_price_confidence_floor_v3",),
        ).fetchone()
        tail_budget_price_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_surplus_tail_budget_profiles_optionprice_tail_touch_profile_price_cap_v4",),
        ).fetchone()
        tail_budget_price_queue_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_queue_items
             WHERE signal_id=?
               AND phase='last_week_backtest'
            """,
            ("master_hedge_grid_scalping_surplus_tail_budget_profiles_optionprice_tail_touch_profile_price_cap_v4",),
        ).fetchone()
        assert inversion_version_row is not None
        assert inversion_version_row["parent_signal_id"] is None
        assert inversion_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_inversion_intensity_optionprice_tail_touch_rebound_flip_density_v2"
        )
        assert inversion_queue_row is not None
        assert inversion_queue_row["supersedes_signal_id"] == inversion_version_row["supersedes_signal_id"]
        assert profile_price_version_row is not None
        assert profile_price_version_row["parent_signal_id"] is None
        assert profile_price_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3"
        )
        assert profile_price_queue_row is not None
        assert profile_price_queue_row["supersedes_signal_id"] == profile_price_version_row["supersedes_signal_id"]
        assert hedge_floor_price_version_row is not None
        assert hedge_floor_price_version_row["parent_signal_id"] is None
        assert hedge_floor_price_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_hedge_floor_state_profiles_optionprice_scenario_outcome_confidence_floor_v2"
        )
        assert tail_budget_price_version_row is not None
        assert tail_budget_price_version_row["parent_signal_id"] is None
        assert tail_budget_price_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_surplus_tail_budget_profiles_optionprice_tail_touch_subgroup_alignment_cap_v3"
        )
        assert tail_budget_price_queue_row is not None
        assert tail_budget_price_queue_row["supersedes_signal_id"] == tail_budget_price_version_row["supersedes_signal_id"]
        subgroup_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_subgroup_coherent_consensus_v5",),
        ).fetchone()
        distribution_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2",),
        ).fetchone()
        distribution_profile_price_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3",),
        ).fetchone()
        distribution_profile_price_queue_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_queue_items
             WHERE signal_id=?
               AND phase='last_week_backtest'
            """,
            ("master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3",),
        ).fetchone()
        distribution_profile_price_execution_version_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_versions
             WHERE signal_id=?
            """,
            ("master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_execution_context_reference_v4",),
        ).fetchone()
        distribution_profile_price_execution_queue_row = conn.execute(
            """
            SELECT parent_signal_id, supersedes_signal_id
              FROM signal_queue_items
             WHERE signal_id=?
               AND phase='last_week_backtest'
            """,
            ("master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_execution_context_reference_v4",),
        ).fetchone()
        assert subgroup_version_row is not None
        assert subgroup_version_row["parent_signal_id"] == (
            "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_grade_style_side_tilt_reference_v1"
        )
        assert subgroup_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_coherent_consensus_v4"
        )
        assert distribution_version_row is not None
        assert distribution_version_row["parent_signal_id"] is None
        assert distribution_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_grade_style_side_tilt_reference_v1"
        )
        assert distribution_profile_price_version_row is not None
        assert distribution_profile_price_version_row["parent_signal_id"] is None
        assert distribution_profile_price_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2"
        )
        assert distribution_profile_price_queue_row is not None
        assert distribution_profile_price_queue_row["supersedes_signal_id"] == (
            distribution_profile_price_version_row["supersedes_signal_id"]
        )
        assert distribution_profile_price_execution_version_row is not None
        assert distribution_profile_price_execution_version_row["parent_signal_id"] is None
        assert distribution_profile_price_execution_version_row["supersedes_signal_id"] == (
            "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3"
        )
        assert distribution_profile_price_execution_queue_row is not None
        assert distribution_profile_price_execution_queue_row["supersedes_signal_id"] == (
            distribution_profile_price_execution_version_row["supersedes_signal_id"]
        )


def test_signal_queue_claims_one_item_and_respects_ttl_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync_signal_catalog(conn)
        first = claim_next_queue_item(conn, owner_id="worker-a", ttl_minutes=10)
        second = claim_next_queue_item(conn, owner_id="worker-b", ttl_minutes=10)

        assert first is not None
        assert second is not None
        assert first.queue_item_key != second.queue_item_key
        assert first.status == "OWNED"
        assert second.owner_id == "worker-b"

        expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        conn.execute(
            "UPDATE signal_queue_items SET owner_expires_at_utc=? WHERE queue_item_key=?",
            (expired_at, first.queue_item_key),
        )
        assert expire_stale_owned_queue_items(conn) == 1
        reclaimed = claim_next_queue_item(conn, owner_id="worker-c", ttl_minutes=10)
        expired_row = conn.execute(
            "SELECT status, owner_id FROM signal_queue_items WHERE queue_item_key=?",
            (first.queue_item_key,),
        ).fetchone()

        assert reclaimed is not None
        assert reclaimed.queue_item_key != second.queue_item_key
        assert reclaimed.owner_id == "worker-c"
        assert expired_row["status"] == "QUEUED"
        assert expired_row["owner_id"] is None


def test_signal_validation_worker_records_phase_result_from_replay_frame_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)
    _insert_signal_validation_frame(db_path)

    payload = run_signal_validation_worker_once(
        SignalValidationWorkerConfig(db_path=db_path, owner_id="worker-test", max_frames=5)
    )

    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["status"] == "passed"
    assert payload["phase_result"]["sample_count"] == 1
    assert payload["observation_count"] == 1
    assert payload["phase_result"]["metrics"]["structural_validation_only"] is False

    with connect(db_path) as conn:
        results = validation_results(conn)
        status = validation_status(conn)

    assert results["result_count"] == 1
    assert any(row["latest_result_status"] == "passed" for row in status["signals"])
    assert any(row["queue_status"] == "QUEUED" and row["queue_phase"] == "last_month_backtest" for row in status["signals"])
    assert status["by_promotion_state"]["INCOMPLETE"] >= 1
    assert any(row["promotion_ready"] is False for row in status["signals"])


def test_signal_validation_worker_batch_exhausts_first_phase_before_next_phase_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)
    _insert_signal_validation_frame(db_path)

    payload = run_signal_validation_worker_batch(
        SignalValidationWorkerConfig(db_path=db_path, owner_id="worker-batch-test", max_frames=5, batch_size=3)
    )

    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["run_count"] == 3
    assert payload["phase_counts"] == {"last_week_backtest": 3}
    assert payload["status_counts"] == {"passed": 3}


def test_signal_design_reviewer_summarizes_incomplete_queue_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"

    payload = run_signal_design_reviewer_once(SignalDesignReviewerConfig(db_path=db_path))

    assert payload["schema_version"] == "crypto_options_signal_design_reviewer_result_v1"
    assert payload["status"] == "needs_work"
    assert payload["signal_count"] >= 20
    assert payload["queue_count"] >= 20
    assert payload["promotion_state_counts"]["INCOMPLETE"] >= 20
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False


def test_signal_promotion_review_blocks_weak_structural_pass_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync_signal_catalog(conn)
        row = conn.execute("SELECT signal_id, version FROM signal_specs ORDER BY signal_id LIMIT 1").fetchone()
        assert row is not None
        signal_id = row["signal_id"]
        version = row["version"]
        now = "2026-06-04T12:00:00+00:00"
        for index, phase in enumerate(("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test")):
            conn.execute(
                """
                INSERT INTO signal_validation_results(
                    validation_result_key, validation_run_key, queue_item_key, signal_id,
                    version, phase, status, evaluated_at_utc, sample_count, hit_rate,
                    average_forward_return, blockers_json, metrics_json, inserted_at_utc
                )
                VALUES (?, NULL, NULL, ?, ?, ?, 'passed', ?, 100, 0.43, -0.01, '[]', ?, ?)
                """,
                (
                    f"weak-result-{index}",
                    signal_id,
                    version,
                    phase,
                    now,
                    json.dumps(
                        {
                            "structural_validation_only": True,
                            "frame_sources": {"abc_tables_synthetic": 100},
                        },
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        status = validation_status(conn)
        reviewed = next(item for item in status["signals"] if item["signal_id"] == signal_id)

    assert reviewed["promotion_state"] == "NEEDS_V2_REVIEW"
    assert reviewed["promotion_ready"] is False
    assert reviewed["needs_stricter_variant"] is True
    assert "live_shadow_test_hit_rate_below_0.62" in reviewed["strict_review_reasons"]


def test_signal_promotion_allows_non_directional_reference_negative_forward_return_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync_signal_catalog(conn)
        row = conn.execute(
            """
            SELECT signal_id, version
              FROM signal_specs
             WHERE signal_type='support_resistance'
               AND purpose='price_reference'
             ORDER BY signal_id
             LIMIT 1
            """
        ).fetchone()
        assert row is not None
        signal_id = row["signal_id"]
        version = row["version"]
        now = "2026-06-04T12:00:00+00:00"
        for index, phase in enumerate(("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test")):
            conn.execute(
                """
                INSERT INTO signal_validation_results(
                    validation_result_key, validation_run_key, queue_item_key, signal_id,
                    version, phase, status, evaluated_at_utc, sample_count, hit_rate,
                    average_forward_return, blockers_json, metrics_json, inserted_at_utc
                )
                VALUES (?, NULL, NULL, ?, ?, ?, 'passed', ?, 100, 1.0, -0.03, '[]', ?, ?)
                """,
                (
                    f"reference-result-{index}",
                    signal_id,
                    version,
                    phase,
                    now,
                    json.dumps({"frame_sources": {"strict_pair_snapshot_replay": 100}}, sort_keys=True),
                    now,
                ),
            )
        for index in range(20):
            event_key = f"reference-event-{index}"
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds, event_start_time_utc,
                    event_end_time_utc, source_table, source_json, inserted_at_utc, updated_at_utc
                )
                VALUES (?, ?, 'BTC', 300, ?, ?, 'pytest', '{}', ?, ?)
                """,
                (
                    event_key,
                    f"btc-reference-{index}",
                    f"2026-06-04T12:{index:02d}:00+00:00",
                    f"2026-06-04T12:{index:02d}:00+00:00",
                    now,
                    now,
                ),
            )
            for phase in ("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test"):
                conn.execute(
                    """
                    INSERT INTO signal_observations(
                        observation_key, validation_run_key, signal_id, version, phase, event_key,
                        event_token_key, decision_at_utc, emitted_signal, observed_value,
                        expected_direction, outcome_direction, hit, payload_json, blockers_json, inserted_at_utc
                    )
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 1, 1.0, NULL, NULL, 1, ?, '[]', ?)
                    """,
                    (
                        f"reference-observation-{phase}-{index}",
                        signal_id,
                        version,
                        phase,
                        event_key,
                        f"token-{index}",
                        f"2026-06-04T12:{index:02d}:01+00:00",
                        json.dumps({"frame_source": "strict_pair_snapshot_replay"}, sort_keys=True),
                        now,
                    ),
                )

        status = validation_status(conn)
        reviewed = next(item for item in status["signals"] if item["signal_id"] == signal_id)

    assert reviewed["promotion_state"] == "PROMOTION_READY"
    assert reviewed["promotion_ready"] is True
    assert reviewed["requires_positive_forward_return"] is False
    assert not any("negative_average_forward_return" in reason for reason in reviewed["strict_review_reasons"])


def test_signal_promotion_blocks_logic_gate_trigger_even_when_variant_contains_depth_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync_signal_catalog(conn)
        row = conn.execute(
            """
            SELECT signal_id, version
              FROM signal_specs
             WHERE signal_type='grid_viability'
               AND purpose='logic_gate_trigger'
               AND variant LIKE '%depth%'
             ORDER BY signal_id
             LIMIT 1
            """
        ).fetchone()
        assert row is not None
        signal_id = row["signal_id"]
        version = row["version"]
        now = "2026-06-04T12:00:00+00:00"
        for index, phase in enumerate(("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test")):
            conn.execute(
                """
                INSERT INTO signal_validation_results(
                    validation_result_key, validation_run_key, queue_item_key, signal_id,
                    version, phase, status, evaluated_at_utc, sample_count, hit_rate,
                    average_forward_return, blockers_json, metrics_json, inserted_at_utc
                )
                VALUES (?, NULL, NULL, ?, ?, ?, 'passed', ?, 100, 0.94, -0.012, '[]', ?, ?)
                """,
                (
                    f"logic-gate-depth-result-{index}",
                    signal_id,
                    version,
                    phase,
                    now,
                    json.dumps({"frame_sources": {"strict_pair_snapshot_replay": 100}}, sort_keys=True),
                    now,
                ),
            )
        for index in range(20):
            event_key = f"logic-gate-depth-event-{index}"
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds, event_start_time_utc,
                    event_end_time_utc, source_table, source_json, inserted_at_utc, updated_at_utc
                )
                VALUES (?, ?, 'BTC', 300, ?, ?, 'pytest', '{}', ?, ?)
                """,
                (
                    event_key,
                    f"btc-logic-gate-depth-{index}",
                    f"2026-06-04T12:{index:02d}:00+00:00",
                    f"2026-06-04T12:{index:02d}:00+00:00",
                    now,
                    now,
                ),
            )
            for phase in ("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test"):
                conn.execute(
                    """
                    INSERT INTO signal_observations(
                        observation_key, validation_run_key, signal_id, version, phase, event_key,
                        event_token_key, decision_at_utc, emitted_signal, observed_value,
                        expected_direction, outcome_direction, hit, payload_json, blockers_json, inserted_at_utc
                    )
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 1, 1.0, NULL, NULL, 1, ?, '[]', ?)
                    """,
                    (
                        f"logic-gate-depth-observation-{phase}-{index}",
                        signal_id,
                        version,
                        phase,
                        event_key,
                        f"{event_key}:up",
                        f"2026-06-04T12:{index:02d}:01+00:00",
                        json.dumps({"frame_source": "strict_pair_snapshot_replay"}, sort_keys=True),
                        now,
                    ),
                )

        status = validation_status(conn)
        reviewed = next(item for item in status["signals"] if item["signal_id"] == signal_id)

    assert reviewed["promotion_state"] == "NEEDS_V2_REVIEW"
    assert reviewed["promotion_ready"] is False
    assert reviewed["requires_positive_forward_return"] is True
    assert "live_shadow_test_negative_average_forward_return" in reviewed["strict_review_reasons"]


def test_signal_validation_reporting_surfaces_phase_diversity_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync_signal_catalog(conn)
        row = conn.execute("SELECT signal_id, version FROM signal_specs ORDER BY signal_id LIMIT 1").fetchone()
        assert row is not None
        signal_id = row["signal_id"]
        version = row["version"]
        now = "2026-06-04T12:00:00+00:00"
        phases = ("last_week_backtest", "last_month_backtest", "random_sampling_backtest", "live_shadow_test")
        symbols = ("BTC", "ETH", "BTC", "ETH")

        for index, (phase, symbol) in enumerate(zip(phases, symbols, strict=True)):
            event_key = f"event-{index}"
            event_start = f"2026-06-0{4 + index}T12:00:00+00:00"
            decision_at = f"2026-06-0{4 + index}T12:00:0{index}+00:00"
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds, event_start_time_utc,
                    event_end_time_utc, source_table, source_json, inserted_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, 300, ?, ?, 'pytest', '{}', ?, ?)
                """,
                (
                    event_key,
                    f"{symbol.lower()}-pytest-{index}",
                    symbol,
                    event_start,
                    event_start,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO signal_validation_results(
                    validation_result_key, validation_run_key, queue_item_key, signal_id,
                    version, phase, status, evaluated_at_utc, sample_count, hit_rate,
                    average_forward_return, blockers_json, metrics_json, inserted_at_utc
                )
                VALUES (?, NULL, NULL, ?, ?, ?, 'passed', ?, 100, 0.70, 0.05, '[]', ?, ?)
                """,
                (
                    f"diversity-result-{index}",
                    signal_id,
                    version,
                    phase,
                    decision_at,
                    json.dumps(
                        {
                            "structural_validation_only": True,
                            "frame_sources": {"abc_tables_synthetic": 100},
                        },
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO signal_observations(
                    observation_key, validation_run_key, signal_id, version, phase, event_key,
                    event_token_key, decision_at_utc, emitted_signal, observed_value,
                    expected_direction, outcome_direction, hit, payload_json, blockers_json, inserted_at_utc
                )
                VALUES (?, NULL, ?, ?, ?, ?, NULL, ?, 1, 1.0, 'up', 'up', 1, ?, '[]', ?)
                """,
                (
                    f"diversity-observation-{index}",
                    signal_id,
                    version,
                    phase,
                    event_key,
                    decision_at,
                    json.dumps({"frame_source": "abc_tables_synthetic"}, sort_keys=True),
                    now,
                ),
            )

        status = validation_status(conn)
        results = validation_results(conn)
        reviewed = next(item for item in status["signals"] if item["signal_id"] == signal_id)
        latest_live_shadow = next(item for item in results["results"] if item["signal_id"] == signal_id and item["phase"] == "live_shadow_test")

    assert reviewed["distinct_event_count"] == 4
    assert reviewed["distinct_symbol_count"] == 2
    assert reviewed["distinct_window_count"] == 4
    assert status["generated_at_utc"] is not None
    assert reviewed["type"] == reviewed["signal_type"]
    assert reviewed["phase"] == "live_shadow_test"
    assert reviewed["sources"]
    assert reviewed["source_blocks"]
    assert reviewed["latest_phase_hit_rate"] == 0.70
    assert reviewed["latest_phase_sample_count"] == 100
    assert reviewed["latest_phase_frame_sources"] == {"abc_tables_synthetic": 100}
    assert reviewed["live_shadow_hit_rate"] == 0.70
    assert reviewed["live_shadow_sample_count"] == 100
    assert reviewed["live_shadow_frame_sources"] == {"abc_tables_synthetic": 100}
    assert reviewed["live_shadow_diversity"]["distinct_symbol_count"] == 1
    assert reviewed["phase_diversity"]["live_shadow_test"]["distinct_symbol_count"] == 1
    assert latest_live_shadow["diversity"]["distinct_event_count"] == 1
    assert latest_live_shadow["diversity"]["distinct_symbol_count"] == 1
    assert latest_live_shadow["diversity"]["distinct_window_count"] == 1
    assert latest_live_shadow["diversity"]["decision_span"]["earliest_decision_at_utc"] is not None


def test_signal_backtest_api_and_webui_are_read_only_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    app = create_app(CryptoOptionsAppConfig(db_path=db_path))
    client = TestClient(app)

    page = client.get("/v1/crypto-options-app/signals/backtests")
    status = client.get("/v1/crypto-options-app/signals/validation/status")
    selection = client.get("/v1/crypto-options-app/signals/selection")
    queue = client.get("/v1/crypto-options-app/signals/validation/queue")
    results = client.get("/v1/crypto-options-app/signals/validation/results")
    review = client.post(
        "/v1/crypto-options-app/signals/validation/request-review",
        json={"message": "Need another profile-distribution variant for B coverage."},
    )

    assert page.status_code == 200
    assert "Signal Backtest Lab" in page.text
    assert "End-to-end loop" in page.text
    assert "Crypto Options Control Surface" in page.text
    assert "Needs Revision" in page.text
    assert "Needs V2" not in page.text
    assert "Next Action" in page.text
    assert "review" in page.text
    assert status.status_code == 200
    assert selection.status_code == 200
    assert queue.status_code == 200
    assert results.status_code == 200
    assert review.status_code == 200
    assert status.json()["orders_allowed"] is False
    assert selection.json()["orders_allowed"] is False
    assert selection.json()["live_trading_authorized"] is False
    assert selection.json()["schema_version"] == "crypto_options_signal_selection_v1"
    assert selection.json()["signal_count"] >= 20
    assert "selection_policy" in selection.json()
    assert "action_state_counts" in selection.json()
    assert selection.json()["action_state_counts"]
    assert all("next_action" in row for row in selection.json()["signals"])
    assert all("action_state" in row for row in selection.json()["signals"])
    assert all("action_owner" in row for row in selection.json()["signals"])
    assert all("next_action_detail" in row for row in selection.json()["signals"])
    assert status.json()["review_queue"]["pending_review_request_count"] == 0
    assert queue.json()["live_trading_authorized"] is False
    assert review.json()["request"]["status"] == "pending"

    status_after_review = client.get("/v1/crypto-options-app/signals/validation/status")

    assert status_after_review.status_code == 200
    assert status_after_review.json()["review_queue"]["pending_review_request_count"] == 1
    assert "orphan runs" in page.text


def test_signal_status_surfaces_orphaned_run_integrity_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite"
    initialize_schema(db_path)

    with connect(db_path) as conn:
        sync_signal_catalog(conn)
        conn.execute(
            """
            INSERT INTO signal_validation_runs(
                validation_run_key, queue_item_key, signal_id, version, phase,
                owner_id, started_at_utc, completed_at_utc, status, summary_json, inserted_at_utc
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, NULL, 'running', '{}', ?)
            """,
            (
                "orphan-run-1",
                "master_hedge_grid_scalping_outcome_prediction_cryptoprice_multiframe_trend_conflict_v1",
                "v1",
                "last_week_backtest",
                "worker-test",
                "2026-06-05T09:00:00+00:00",
                "2026-06-05T09:00:00+00:00",
            ),
        )
        status = validation_status(conn)

    assert status["integrity"]["signal_history_trust_state"] == "needs_reconciliation"
    assert status["integrity"]["orphaned_run_count"] == 1
    assert status["integrity"]["latest_orphaned_runs"][0]["validation_run_key"] == "orphan-run-1"


def _insert_signal_validation_frame(db_path: Path) -> None:
    with connect(db_path) as conn:
        now = "2026-06-04T12:00:00+00:00"
        frame_json = {
            "profile_state": {
                "top_profiles_distribution": {"up": 0.82, "down": 0.18},
                "source": "fixture",
            },
            "market_state": {
                "outcome": "up",
                "up_price": 0.51,
                "down_price": 0.49,
            },
            "underlying_state": {
                "symbol": "BTC",
                "price": 63500.0,
            },
        }
        conn.execute(
            """
            INSERT INTO replay_frames(
                replay_frame_key, replay_dataset_key, event_key, event_token_key,
                replay_timestamp_utc, source_observed_at_utc, decision_at_utc,
                frame_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "signal-frame-1",
                "signal-dataset-1",
                "event-1",
                "event-1:up",
                now,
                now,
                now,
                json.dumps(frame_json, sort_keys=True),
                now,
            ),
        )
