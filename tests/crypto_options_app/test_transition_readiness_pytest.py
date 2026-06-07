from __future__ import annotations

from crypto_options_app.reports import transition_readiness


def test_classifies_dirty_paths_for_cleanup() -> None:
    assert transition_readiness._classify_dirty_path("crypto_options_app/main.py") == "crypto_active"
    assert transition_readiness._classify_dirty_path("tests/crypto_options_app/test_x.py") == "crypto_active"
    assert transition_readiness._classify_dirty_path("app/modules/agentic/live_game_context.py") == "global_reference_candidate"
    assert transition_readiness._classify_dirty_path("app/docs/nba_wnba_plan.md") == "wnba_nba_reference_candidate"
    assert transition_readiness._classify_dirty_path(".github/workflows/test.yml") == "github_review_required"


def test_latest_repo_cleanup_inventory_state(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    report_dir = artifact_root / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "repo_cleanup_inventory_latest.json").write_text(
        """
        {
          "status": "degraded",
          "summary": {
            "dirty_path_count": 3,
            "legacy_move_candidate_count": 2,
            "review_required_count": 2,
            "classification_counts": {"crypto_active": 1, "global_reference_candidate": 2}
          },
          "path_entries": [
            {"status": "M", "path": "crypto_options_app/config.py"},
            {"status": "M", "path": "app/modules/agentic/store.py"}
          ],
          "gates": {"fixed_chats_start_ready": false}
        }
        """,
        encoding="utf-8",
    )
    repo = transition_readiness._latest_repo_cleanup_inventory_state(artifact_root)

    assert repo is not None
    assert repo["source"] == "repo_cleanup_inventory_latest"
    assert repo["dirty_path_count"] == 3
    assert repo["reference_candidate_count"] == 2
    assert repo["review_required_count"] == 2


def test_promotion_state_blocks_accidental_live() -> None:
    promotion = transition_readiness._promotion_state(
        {
            "strategy_count": 2,
            "by_promotion_state": {"SHADOW_READY": 1, "SHADOW_REVIEW": 1},
            "strategies": [
                {
                    "promotion_state": "LIVE_CANDIDATE",
                    "orders_allowed": False,
                    "live_trading_authorized": False,
                    "signal_gate": {"strict_replay_required_count": 0},
                },
                {
                    "promotion_state": "SHADOW_READY",
                    "orders_allowed": True,
                    "live_trading_authorized": False,
                    "signal_gate": {"strict_replay_required_count": 1},
                },
            ],
            "manual_orders_avoided": True,
        }
    )

    assert promotion["live_candidate_count"] == 1
    assert promotion["shadow_ready_count"] == 1
    assert promotion["strict_signal_blocker_count"] == 1
    assert promotion["accidental_live_authorized_count"] == 1


def test_transition_blockers_require_postgres_and_disabled_orders() -> None:
    blockers, warnings = transition_readiness._transition_blockers(
        coordination={"status": "ok"},
        storage={"status": "degraded"},
        endpoints={
            "health": {
                "status": "ok",
                "payload": {
                    "status": "degraded",
                    "db": {"connection_is_postgres": True},
                    "orders_allowed": False,
                    "live_trading_authorized": False,
                },
            }
        },
        repo={"dirty_path_count": 1},
        promotion={"accidental_live_authorized_count": 0, "live_candidate_count": 0},
    )

    assert blockers == []
    assert "storage_audit_degraded" in warnings
    assert "health_degraded" in warnings
    assert "repo_dirty_requires_inventory_cleanup" in warnings


def test_build_transition_review_with_mocked_inputs(monkeypatch) -> None:
    monkeypatch.setattr(transition_readiness, "_coordination_state", lambda: {"status": "ok"})
    monkeypatch.setattr(
        transition_readiness,
        "_load_latest_storage_audit",
        lambda _root: {"status": "ok", "decision": "postgres_only_for_now"},
    )
    monkeypatch.setattr(
        transition_readiness,
        "_endpoint_state",
        lambda _options: {
            "health": {
                "status": "ok",
                "payload": {
                    "status": "ok",
                    "db": {"connection_is_postgres": True},
                    "orders_allowed": False,
                    "live_trading_authorized": False,
                },
            },
            "strategies_promotion": {
                "status": "ok",
                "payload": {
                    "strategy_count": 0,
                    "strategies": [],
                    "by_promotion_state": {},
                    "manual_orders_avoided": True,
                },
            },
            "signals_validation_status": {"status": "ok", "payload": {"schema_version": "x"}},
        },
    )
    monkeypatch.setattr(transition_readiness, "_repo_state", lambda: {"dirty_path_count": 0})

    review = transition_readiness.build_transition_readiness_review()

    assert review["schema_version"] == "crypto_options_transition_readiness_v1"
    assert review["status"] == "ok"
    assert review["manual_orders_avoided"] is True
