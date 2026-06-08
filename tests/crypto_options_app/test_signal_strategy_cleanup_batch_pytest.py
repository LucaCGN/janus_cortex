from __future__ import annotations

import json

from crypto_options_app.strategies.cleanup_batch import (
    CLEANUP_BATCH_SCHEMA_VERSION,
    classify_signal_cleanup_row,
    classify_strategy_cleanup_row,
    write_signal_strategy_cleanup_batch_report,
)


def test_cleanup_signal_classification_uses_policy_contract_labels_pytest() -> None:
    passed = classify_signal_cleanup_row(
        {
            "signal_id": "selected_passed",
            "signal_type": "grid_spacing",
            "source_blocks": ["C"],
            "promotion_state": "PASSED",
            "queue_status": "PASSED",
        }
    )
    ready = classify_signal_cleanup_row(
        {
            "signal_id": "ready_signal",
            "signal_type": "liquidity_depth",
            "source_blocks": ["C"],
            "promotion_state": "PROMOTION_READY",
            "queue_status": "PASSED",
        }
    )
    revision = classify_signal_cleanup_row(
        {
            "signal_id": "weak_signal",
            "signal_type": "outcome_prediction",
            "source_blocks": ["B"],
            "promotion_state": "NEEDS_V2_REVIEW",
            "queue_status": "PASSED",
        }
    )
    blocked = classify_signal_cleanup_row(
        {
            "signal_id": "blocked_signal",
            "signal_type": "outcome_prediction",
            "source_blocks": ["A"],
            "promotion_state": "PROMOTION_READY",
            "queue_status": "BLOCKED",
        }
    )

    assert passed["cleanup_classification"] == "STRICT_REPLAY_REQUIRED"
    assert ready["cleanup_classification"] == "PROMOTED"
    assert revision["cleanup_classification"] == "NEEDS_VARIANT"
    assert blocked["cleanup_classification"] == "BLOCKED"


def test_cleanup_strategy_classification_enforces_live_policy_thresholds_pytest() -> None:
    good = classify_strategy_cleanup_row(
        {
            "strategy_id": "good",
            "promotion_state": "LIVE_CANDIDATE",
            "recent_samples": 12,
            "recent_win_rate": 0.75,
            "recent_pnl": 0.25,
            "clears_live_policy": True,
            "blockers": [],
        }
    )
    exact_threshold = classify_strategy_cleanup_row(
        {
            "strategy_id": "exact_threshold",
            "promotion_state": "SHADOW_READY",
            "recent_samples": 12,
            "recent_win_rate": 0.70,
            "recent_pnl": 0.25,
            "clears_live_policy": False,
            "blockers": [],
        }
    )
    blocked = classify_strategy_cleanup_row(
        {
            "strategy_id": "blocked",
            "promotion_state": "SHADOW_READY",
            "recent_samples": 12,
            "recent_win_rate": 0.75,
            "recent_pnl": 0.25,
            "clears_live_policy": False,
            "blockers": ["latest_reconciliation_not_reconciled"],
        }
    )
    review = classify_strategy_cleanup_row(
        {
            "strategy_id": "review",
            "promotion_state": "SHADOW_READY",
            "recent_samples": 12,
            "recent_win_rate": 0.75,
            "recent_pnl": 0.25,
            "clears_live_policy": False,
            "blockers": [],
        }
    )

    assert good["cleanup_classification"] == "PROMOTED"
    assert exact_threshold["cleanup_classification"] == "SHADOW_REQUIRED"
    assert blocked["cleanup_classification"] == "BLOCKED"
    assert review["cleanup_classification"] == "REVIEW"


def test_cleanup_batch_report_renders_read_only_policy_context_pytest(tmp_path) -> None:
    payload = {
        "schema_version": CLEANUP_BATCH_SCHEMA_VERSION,
        "policy_contract_schema_version": "crypto_options_promotion_policy_contract_v1",
        "generated_at_utc": "2026-06-07T07:30:00Z",
        "summary": {
            "signal_total": 1,
            "strategy_total": 1,
            "signal_classification_counts": {"STRICT_REPLAY_REQUIRED": 1},
            "strategy_classification_counts": {"SHADOW_REQUIRED": 1},
        },
        "signals": [
            {
                "cleanup_classification": "STRICT_REPLAY_REQUIRED",
                "signal_id": "signal_a",
                "promotion_state": "PASSED",
                "blockers": [],
                "strict_review_reasons": ["strict replay required"],
                "cleanup_next_action": "Run strict replay.",
            }
        ],
        "strategies": [
            {
                "cleanup_classification": "SHADOW_REQUIRED",
                "strategy_id": "strategy_a",
                "promotion_state": "SHADOW_READY",
                "blockers": [],
                "cleanup_next_action": "Accumulate samples.",
            }
        ],
    }

    path = write_signal_strategy_cleanup_batch_report(payload, tmp_path / "cleanup.md")
    text = path.read_text(encoding="utf-8")

    assert "Signal And Strategy Cleanup Batch" in text
    assert "crypto_options_promotion_policy_contract_v1" in text
    assert "This batch is read-only" in text
    assert json.dumps(payload["summary"]["signal_classification_counts"], sort_keys=True) in text
