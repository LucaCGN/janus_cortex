from __future__ import annotations

from crypto_options_app.reports import storage_architecture
from crypto_options_app.reports.storage_architecture import decide_storage_architecture, render_storage_architecture_markdown


def test_storage_decision_keeps_redis_gated_for_hot_plane_pressure() -> None:
    decision = decide_storage_architecture(
        {
            "blockers": [],
            "hot_plane_reasons": ["dashboard_control_center_state:slow_endpoint_over_2000ms"],
            "query_layer_reasons": ["runtime_sqlite_connect_offenders_exist"],
        }
    )

    assert decision["decision"] == "postgres_plus_redis_hot_plane_candidate"
    assert decision["postgres"] == "required_durable_source_of_truth"
    assert "cache_queue_ttl" in decision["redis"]
    assert decision["non_redis_fixes_still_required"] == ["runtime_sqlite_connect_offenders_exist"]


def test_storage_decision_blocks_redis_when_runtime_unstable() -> None:
    decision = decide_storage_architecture(
        {
            "blockers": ["runtime_audit_blocked"],
            "hot_plane_reasons": ["postgres_container_memory_over_6gib"],
            "query_layer_reasons": [],
        }
    )

    assert decision["decision"] == "defer_storage_change_until_runtime_stable"
    assert decision["redis"] == "do_not_enable_while_blocked"


def test_storage_audit_can_run_with_mocked_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        storage_architecture,
        "build_runtime_audit",
        lambda _options: {
            "status": "ok",
            "database": {"runtime_connection_is_postgres": True, "runtime_code": {"status": "ok"}},
            "docker": {"resources": {"status": "ok", "warnings": [], "blockers": []}},
        },
    )
    monkeypatch.setattr(
        storage_architecture,
        "_endpoint_timing_audit",
        lambda _options: {"status": "ok", "warnings": [], "blockers": [], "endpoints": {}},
    )
    monkeypatch.setattr(
        storage_architecture,
        "check_redis_hot_plane",
        lambda: {"status": "disabled", "enabled": False},
    )

    audit = storage_architecture.build_storage_architecture_audit()

    assert audit["schema_version"] == "crypto_options_storage_architecture_audit_v1"
    assert audit["postgres_role"] == "durable_source_of_truth"
    assert audit["redis_must_not_store_authoritative_trading_truth"] is True


def test_storage_markdown_renders_decision() -> None:
    markdown = render_storage_architecture_markdown(
        {
            "generated_at_utc": "2026-06-07T00:00:00+00:00",
            "status": "ok",
            "decision": {"decision": "postgres_only_for_now", "reasons": ["no_hot_plane_pressure"]},
            "postgres_role": "durable_source_of_truth",
            "redis_role": "gated_hot_plane_candidate",
            "manual_orders_avoided": True,
            "endpoint_timings": {"endpoints": {}},
            "runtime": {},
            "redis_hot_plane": {"status": "disabled"},
        }
    )

    assert "postgres_only_for_now" in markdown
    assert "durable_source_of_truth" in markdown
    assert "Redis hot plane" in markdown
