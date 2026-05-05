import pandas as pd

from tools.run_execution_replay_artifact import (
    ML_FOCUS_REPLAY_FAMILIES,
    REGULAR_REPLAY_FAMILIES,
    _filter_controller_trade_frame_by_family_scope,
    _regular_replay_families_for_scope,
)


def test_regular_replay_families_all_scope_expands_beyond_ml_focus() -> None:
    focus_families = _regular_replay_families_for_scope("focus")
    all_families = _regular_replay_families_for_scope("all")

    assert focus_families == ML_FOCUS_REPLAY_FAMILIES
    assert all_families == REGULAR_REPLAY_FAMILIES
    assert len(all_families) > len(focus_families)
    assert set(focus_families).issubset(all_families)
    assert "winner_definition" in all_families
    assert "lead_fragility" in all_families


def test_controller_family_scope_filter_preserves_all_when_requested() -> None:
    frame = pd.DataFrame(
        {
            "source_strategy_family": [
                "inversion",
                "winner_definition",
                "micro_momentum_continuation",
                "lead_fragility",
            ],
            "value": [1, 2, 3, 4],
        }
    )

    focused = _filter_controller_trade_frame_by_family_scope(frame, controller_family_scope="focus")
    all_scope = _filter_controller_trade_frame_by_family_scope(frame, controller_family_scope="all")

    assert focused["source_strategy_family"].tolist() == [
        "inversion",
        "micro_momentum_continuation",
    ]
    pd.testing.assert_frame_equal(all_scope.reset_index(drop=True), frame)
