from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.modules.agentic.live_snapshot import (
    build_normalized_live_snapshot,
    build_normalized_live_snapshot_review,
    render_normalized_live_snapshot_review_markdown,
    sample_snapshot_pair,
    write_normalized_live_snapshot_review,
)


def test_normalized_live_snapshot_exposes_same_shape_for_nba_and_wnba() -> None:
    snapshots = sample_snapshot_pair()

    assert {snapshot.league for snapshot in snapshots} == {"nba", "wnba"}
    for snapshot in snapshots:
        assert snapshot.schema_version == "normalized_live_snapshot_v1"
        assert snapshot.execution_boundary == "evidence_only"
        assert set(snapshot.feeds) == {"scoreboard", "play_by_play", "stats"}
        assert snapshot.game.home.side == "home"
        assert snapshot.game.away.side == "away"
        assert len(snapshot.clob) == 2
        assert snapshot.runtime.current_plan_count == 1


def test_snapshot_parity_gaps_are_league_scoped_not_global() -> None:
    generated_at = datetime(2026, 5, 25, 6, 20, tzinfo=timezone.utc)
    snapshot = build_normalized_live_snapshot(
        event_id="wnba-test",
        league="wnba",
        generated_at_utc=generated_at,
        game={
            "game_status_text": "Live",
            "period": 1,
            "clock": "PT05M00.00S",
            "updated_at": "2026-05-25T06:19:45Z",
            "home_team_name": "Seattle Storm",
            "home_score": 12,
            "away_team_name": "Washington Mystics",
            "away_score": 9,
        },
        orderbooks={},
        direct_clob={},
        strategy_plan_gate={"status": "missing", "current_plan_count": 0},
        worker={"status": "stopped", "running": False},
    )
    review = build_normalized_live_snapshot_review(day="2026-05-25", snapshots=[snapshot])

    blocker_codes = [gap["blocker_code"] for gap in review.parity_gaps]
    assert "wnba_execution:clob_orderbook_missing" in blocker_codes
    assert "wnba_execution:direct_clob_event_inventory_missing" in blocker_codes
    assert all(gap["scope"].startswith("wnba execution only") for gap in review.parity_gaps)
    assert "Backfill or classify stale/missing feed fields" in review.next_actions[0]


def test_write_normalized_live_snapshot_review_persists_json_markdown_and_index(tmp_path: Path) -> None:
    review = build_normalized_live_snapshot_review(day="2026-05-25")
    result = write_normalized_live_snapshot_review(review, artifact_root=tmp_path, report_dir=tmp_path / "reports")

    json_path = Path(result["json_path"])
    markdown_path = Path(result["markdown_path"])
    assert json_path.exists()
    assert markdown_path.exists()
    assert (json_path.parent / "normalized_live_snapshot_reviews.jsonl").exists()
    assert result["snapshot_count"] == 2
    assert result["schema_version"] == "normalized_live_snapshot_review_write_result_v1"
    assert "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders" in markdown_path.read_text()


def test_render_normalized_live_snapshot_review_lists_parity_fields() -> None:
    review = build_normalized_live_snapshot_review(day="2026-05-25")
    markdown = render_normalized_live_snapshot_review_markdown(review, json_path="sample.json")

    assert "# Normalized Live Snapshot Review - 2026-05-25" in markdown
    assert "`feeds.scoreboard`" in markdown
    assert "`account.event_scoped_inventory`" in markdown
    assert "sample.json" in markdown
