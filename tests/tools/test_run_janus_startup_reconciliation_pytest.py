from __future__ import annotations

import json
from types import SimpleNamespace

import tools.run_janus_startup_reconciliation as startup_reconciliation


def test_date_range_uses_requested_window_pytest() -> None:
    assert startup_reconciliation._date_range("2026-05-17", 3) == [
        "2026-05-17",
        "2026-05-18",
        "2026-05-19",
    ]


def test_main_runs_data_refresh_for_each_session_date_pytest(monkeypatch, capsys) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == startup_reconciliation.REPO_ROOT
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(startup_reconciliation.subprocess, "run", fake_run)
    monkeypatch.setattr(
        startup_reconciliation.sys,
        "argv",
        [
            "run_janus_startup_reconciliation.py",
            "--start-date",
            "2026-05-17",
            "--days",
            "2",
            "--api-root",
            "http://janus.local",
            "--season",
            "2025-26",
            "--account-catalog-backfill-limit",
            "100",
        ],
    )

    assert startup_reconciliation.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["live_order_impact"] == "none"
    assert [item["session_date"] for item in payload["results"]] == ["2026-05-17", "2026-05-18"]
    assert "--account-catalog-backfill-limit" in calls[0]
    assert calls[0][calls[0].index("--account-catalog-backfill-limit") + 1] == "100"
    assert calls[0][-4:] == ["--session-date", "2026-05-17", "--stage", "data-refresh"]
    assert calls[1][-4:] == ["--session-date", "2026-05-18", "--stage", "data-refresh"]
