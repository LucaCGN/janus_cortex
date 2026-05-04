from __future__ import annotations

import pandas as pd
import pytest

from tools.capture_live_shadow_snapshot import (
    ML_SHADOW_REQUIRED_FIELDS,
    _enrich_ml_shadow_frame,
    _validate_ml_shadow_payload,
)


def test_enrich_ml_shadow_frame_adds_required_live_fields_pytest() -> None:
    frame = pd.DataFrame(
        [
            {
                "game_id": "0042500115",
                "team_side": "home",
                "strategy_family": "quarter_open_reprice",
                "signal_id": "quarter_open_reprice|0042500115|home|12",
                "entry_state_index": 12,
                "signal_entry_price": 0.38,
                "sidecar_probability": 0.71,
                "heuristic_execute_score": 0.66,
                "focus_family_flag": True,
                "latest_event_at": "2026-04-29T00:10:00Z",
                "coverage_status": "covered_partial",
                "best_bid": 0.37,
                "best_ask": 0.38,
                "shadow_reason": "eligible",
            }
        ]
    )

    enriched = _enrich_ml_shadow_frame(frame)

    assert set(ML_SHADOW_REQUIRED_FIELDS).issubset(set(enriched.columns))
    row = enriched.iloc[0]
    assert row["calibrated_confidence"] == pytest.approx(0.71)
    assert row["calibrated_execution_likelihood"] == pytest.approx(0.66)
    assert bool(row["feed_fresh_flag"]) is True
    assert bool(row["orderbook_available_flag"]) is True
    assert row["min_required_notional_usd"] == pytest.approx(1.9)
    assert bool(row["budget_affordable_flag"]) is True


def test_validate_ml_shadow_payload_raises_for_missing_required_fields_pytest() -> None:
    with pytest.raises(ValueError, match="missing required live fields"):
        _validate_ml_shadow_payload(
            {
                "family_candidates": [
                    {
                        "game_id": "0042500115",
                        "strategy_family": "quarter_open_reprice",
                        "sidecar_probability": 0.7,
                    }
                ]
            }
        )
