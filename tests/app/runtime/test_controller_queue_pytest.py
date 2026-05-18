from __future__ import annotations

import json
from datetime import datetime, timezone

from app.runtime import controller_queue


def _paths(tmp_path):
    return controller_queue.controller_queue_paths(tmp_path / "shared")


def _now() -> datetime:
    return datetime(2026, 5, 18, 6, 0, tzinfo=timezone.utc)


def _ledger_entries(paths) -> list[dict]:
    return [
        json.loads(line)
        for line in paths.pass_ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_claim_and_release_move_lock_and_record_ledger_pytest(tmp_path) -> None:
    paths = _paths(tmp_path)

    claimed = controller_queue.claim_lock(
        issue="#39",
        persona="development-agent",
        owner="janus-master-controller",
        branch="main",
        worktree="C:/repo",
        files=["app/runtime/controller_queue.py"],
        modules=["controller-queue"],
        lock_id="lock-39",
        paths=paths,
        now=_now(),
    )

    assert claimed["status"] == "claimed"
    assert claimed["lock"]["issue"] == "39"
    assert controller_queue.queue_status(paths=paths)["active_lock_count"] == 1

    released = controller_queue.release_lock(
        "lock-39",
        outcome="implemented",
        material_outputs=["commit dbfb5f8"],
        evidence_links=["https://github.com/LucaCGN/janus_cortex/issues/39"],
        paths=paths,
        now=_now(),
    )

    assert released["status"] == "released"
    assert controller_queue.queue_status(paths=paths)["active_lock_count"] == 0
    assert (paths.completed_locks / "2026-05-18" / "lock-39.json").exists()
    assert [entry["outcome"] for entry in _ledger_entries(paths)] == ["claimed", "implemented"]


def test_duplicate_issue_claim_is_blocked_without_overwriting_pytest(tmp_path) -> None:
    paths = _paths(tmp_path)
    first = controller_queue.claim_lock(
        issue=37,
        persona="development-agent",
        owner="controller-a",
        files=["app/api/models.py"],
        lock_id="lock-37-a",
        paths=paths,
        now=_now(),
    )
    second = controller_queue.claim_lock(
        issue="https://github.com/LucaCGN/janus_cortex/issues/37",
        persona="development-agent",
        owner="controller-b",
        files=["app/api/routers/sync.py"],
        lock_id="lock-37-b",
        paths=paths,
        now=_now(),
    )

    assert first["status"] == "claimed"
    assert second["status"] == "blocked_duplicate_lock"
    assert controller_queue.queue_status(paths=paths)["active_lock_count"] == 1
    assert _ledger_entries(paths)[-1]["outcome"] == "blocked_duplicate_lock"


def test_non_overlapping_issue_claims_can_run_together_pytest(tmp_path) -> None:
    paths = _paths(tmp_path)

    first = controller_queue.claim_lock(
        issue=39,
        persona="development-agent",
        owner="controller-a",
        files=["app/runtime/controller_queue.py"],
        lock_id="lock-39",
        paths=paths,
        now=_now(),
    )
    second = controller_queue.claim_lock(
        issue=40,
        persona="docs-memory-agent",
        owner="controller-b",
        files=["app/docs/planning/current/final_system/architecture/current_architecture_map.md"],
        lock_id="lock-40",
        paths=paths,
        now=_now(),
    )

    assert first["status"] == "claimed"
    assert second["status"] == "claimed"
    assert controller_queue.queue_status(paths=paths)["active_lock_count"] == 2


def test_stale_conflict_is_surfaced_not_overwritten_pytest(tmp_path) -> None:
    paths = _paths(tmp_path)
    start = datetime(2026, 5, 18, 6, 0, tzinfo=timezone.utc)
    later = datetime(2026, 5, 18, 6, 2, tzinfo=timezone.utc)

    controller_queue.claim_lock(
        issue=37,
        persona="development-agent",
        owner="controller-a",
        files=["app/data/pipelines/daily/polymarket/sync_portfolio.py"],
        lock_id="stale-lock",
        stale_after_minutes=1,
        paths=paths,
        now=start,
    )
    second = controller_queue.claim_lock(
        issue=37,
        persona="development-agent",
        owner="controller-b",
        files=["app/data/pipelines/daily/polymarket/sync_portfolio.py"],
        lock_id="new-lock",
        paths=paths,
        now=later,
    )

    assert second["status"] == "blocked_stale_lock"
    assert second["conflicts"][0]["stale"] is True
    assert controller_queue.queue_status(paths=paths, now=later)["stale_lock_count"] == 1
    assert not (paths.active_locks / "new-lock.json").exists()


def test_dirty_worktree_blocks_claim_when_required_pytest(monkeypatch, tmp_path) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(controller_queue, "git_dirty_paths", lambda **_: ["app/api/models.py"])

    result = controller_queue.claim_lock(
        issue=39,
        persona="development-agent",
        owner="janus-master-controller",
        files=["app/runtime/controller_queue.py"],
        lock_id="lock-39",
        paths=paths,
        now=_now(),
        require_clean_worktree=True,
    )

    assert result["status"] == "blocked_dirty_worktree"
    assert result["dirty_paths"] == ["app/api/models.py"]
    assert controller_queue.queue_status(paths=paths)["active_lock_count"] == 0
