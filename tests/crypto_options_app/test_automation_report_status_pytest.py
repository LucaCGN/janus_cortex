from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from crypto_options_app.reports.automation_report_status import (
    AUTOMATION_REPORT_STATUS_SCHEMA_VERSION,
    AutomationReportStatusOptions,
    build_automation_report_status,
    render_automation_report_status_markdown,
)


def test_automation_report_status_pending_when_no_reports(tmp_path) -> None:
    status_root = tmp_path / "automation_status"
    now = datetime(2026, 6, 7, 7, 0, tzinfo=UTC)

    review = build_automation_report_status(
        AutomationReportStatusOptions(automation_status_root=status_root, now_utc=now)
    )

    assert review["schema_version"] == AUTOMATION_REPORT_STATUS_SCHEMA_VERSION
    assert review["status"] == "pending_first_reports"
    assert review["status_counts"]["pending_first_report"] == 3


def test_automation_report_status_fresh_when_reports_have_required_sections(tmp_path) -> None:
    status_root = tmp_path / "automation_status"
    status_root.mkdir()
    now = datetime(2026, 6, 7, 7, 0, tzinfo=UTC)
    reports = {
        "db_data_observability_latest.md": "DB backend\nA/B/C/D freshness\nManual orders avoided\n",
        "signal_strategy_queue_worker_latest.md": "Rows considered\nDecision\nManual orders avoided\n",
        "frontend_status_reporter_latest.md": "Endpoints checked\nFrontend blockers\nManual orders avoided\n",
    }
    for name, text in reports.items():
        path = status_root / name
        path.write_text(text, encoding="utf-8")
        ts = now.timestamp()
        os.utime(path, (ts, ts))

    review = build_automation_report_status(
        AutomationReportStatusOptions(automation_status_root=status_root, now_utc=now)
    )

    assert review["status"] == "fresh"
    assert review["status_counts"]["fresh"] == 3


def test_automation_report_status_detects_stale_and_malformed(tmp_path) -> None:
    status_root = tmp_path / "automation_status"
    status_root.mkdir()
    now = datetime(2026, 6, 7, 7, 0, tzinfo=UTC)
    fresh = status_root / "db_data_observability_latest.md"
    fresh.write_text("DB backend\nA/B/C/D freshness\nManual orders avoided\n", encoding="utf-8")
    stale = status_root / "signal_strategy_queue_worker_latest.md"
    stale.write_text("Rows considered\nDecision\nManual orders avoided\n", encoding="utf-8")
    malformed = status_root / "frontend_status_reporter_latest.md"
    malformed.write_text("Endpoints checked\nManual orders avoided\n", encoding="utf-8")
    os.utime(fresh, (now.timestamp(), now.timestamp()))
    old = (now - timedelta(minutes=60)).timestamp()
    os.utime(stale, (old, old))
    os.utime(malformed, (now.timestamp(), now.timestamp()))

    review = build_automation_report_status(
        AutomationReportStatusOptions(automation_status_root=status_root, now_utc=now)
    )

    assert review["status"] == "degraded"
    assert review["automations"]["crypto-options-signal-strategy-queue-worker"]["status"] == "stale"
    assert review["automations"]["crypto-options-frontend-status-reporter"]["status"] == "malformed"


def test_render_automation_report_status_markdown(tmp_path) -> None:
    status_root = tmp_path / "automation_status"
    now = datetime(2026, 6, 7, 7, 0, tzinfo=UTC)
    review = build_automation_report_status(
        AutomationReportStatusOptions(automation_status_root=status_root, now_utc=now)
    )

    markdown = render_automation_report_status_markdown(review)

    assert "Crypto Options Automation Report Status" in markdown
    assert "crypto-options-db-data-observability" in markdown
    assert "Live authority: `none`" in markdown
