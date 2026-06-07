from __future__ import annotations

from crypto_options_app.feeds.polymarket_status import (
    build_cached_polymarket_status_provider,
    clob_trading_available,
    fetch_polymarket_status,
    normalize_polymarket_status,
)


def test_polymarket_status_marks_clob_maintenance_unavailable_pytest() -> None:
    report = normalize_polymarket_status(
        summary={
            "page": {"name": "Polymarket", "status": "UNDERMAINTENANCE"},
            "activeMaintenances": [
                {
                    "id": "maintenance-1",
                    "name": "Scheduled CLOB maintenance",
                    "status": "INPROGRESS",
                    "start": "2026-06-03T12:10:00Z",
                    "duration": "10",
                }
            ],
            "activeIncidents": [],
        },
        components=[
            {"name": "Website", "status": "OPERATIONAL"},
            {"name": "CLOB API", "status": "UNDERMAINTENANCE"},
        ],
        generated_at_utc="2026-06-03T12:12:00+00:00",
    )

    assert report["status"] == "maintenance"
    assert report["clob_api"]["trading_available"] is False
    assert clob_trading_available(report) is False
    assert "exchange_status_not_operational" in report["blockers"]
    assert "polymarket_active_maintenance" in report["blockers"]


def test_polymarket_status_marks_operational_clob_available_pytest() -> None:
    report = normalize_polymarket_status(
        summary={"page": {"name": "Polymarket", "status": "OPERATIONAL"}, "activeMaintenances": [], "activeIncidents": []},
        components=[{"name": "CLOB API", "status": "OPERATIONAL"}],
        generated_at_utc="2026-06-03T12:30:00+00:00",
    )

    assert report["status"] == "operational"
    assert report["blockers"] == []
    assert clob_trading_available(report) is True


def test_polymarket_status_does_not_block_operational_clob_for_email_account_incident_pytest() -> None:
    report = normalize_polymarket_status(
        summary={
            "page": {"name": "Polymarket", "status": "HASISSUES"},
            "activeMaintenances": [],
            "activeIncidents": [
                {
                    "id": "incident-email",
                    "name": "Trading and login issues with email accounts",
                    "status": "IDENTIFIED",
                    "impact": "PARTIALOUTAGE",
                }
            ],
        },
        components=[{"name": "CLOB API", "status": "OPERATIONAL"}],
        generated_at_utc="2026-06-03T18:30:00+00:00",
    )

    assert report["status"] == "operational"
    assert report["clob_api"]["trading_available"] is True
    assert report["blockers"] == []
    assert "polymarket_active_incident_nonblocking_clob_operational" in report["warnings"]
    assert clob_trading_available(report) is True


def test_polymarket_status_blocks_operational_clob_when_incident_names_clob_pytest() -> None:
    report = normalize_polymarket_status(
        summary={
            "page": {"name": "Polymarket", "status": "HASISSUES"},
            "activeMaintenances": [],
            "activeIncidents": [
                {
                    "id": "incident-clob",
                    "name": "CLOB API order submission degraded",
                    "status": "IDENTIFIED",
                    "impact": "PARTIALOUTAGE",
                }
            ],
        },
        components=[{"name": "CLOB API", "status": "OPERATIONAL"}],
        generated_at_utc="2026-06-03T18:30:00+00:00",
    )

    assert report["status"] == "degraded"
    assert report["clob_api"]["trading_available"] is False
    assert "polymarket_active_incident" in report["blockers"]
    assert clob_trading_available(report) is False


def test_cached_polymarket_status_uses_recent_operational_status_on_transient_failure_pytest() -> None:
    now = 100.0

    def clock() -> float:
        return now

    reports = [
        normalize_polymarket_status(
            summary={"page": {"name": "Polymarket", "status": "OPERATIONAL"}, "activeMaintenances": [], "activeIncidents": []},
            components=[{"name": "CLOB API", "status": "OPERATIONAL"}],
            generated_at_utc="2026-06-03T13:00:00+00:00",
        ),
        {
            "schema_version": "polymarket_status_v1",
            "generated_at_utc": "2026-06-03T13:00:30+00:00",
            "source": "polymarket_status_api",
            "status": "unknown",
            "clob_api": {"status": "unknown", "trading_available": False},
            "blockers": ["polymarket_status_unavailable"],
        },
    ]

    provider = build_cached_polymarket_status_provider(fetcher=lambda: reports.pop(0), fresh_seconds=0.0, ttl_seconds=120.0, clock=clock)

    first = provider()
    assert clob_trading_available(first) is True

    now = 145.0
    second = provider()

    assert clob_trading_available(second) is True
    assert second["source"] == "polymarket_status_api_cache"
    assert second["cache_fallback_used"] is True
    assert second["cache_age_seconds"] == 45.0
    assert "polymarket_status_unavailable_using_recent_operational_cache" in second["warnings"]


def test_cached_polymarket_status_does_not_mask_maintenance_or_expired_cache_pytest() -> None:
    now = 100.0

    def clock() -> float:
        return now

    maintenance_report = normalize_polymarket_status(
        summary={
            "page": {"name": "Polymarket", "status": "UNDERMAINTENANCE"},
            "activeMaintenances": [{"id": "maintenance-1", "name": "Scheduled CLOB maintenance", "status": "INPROGRESS"}],
            "activeIncidents": [],
        },
        components=[{"name": "CLOB API", "status": "UNDERMAINTENANCE"}],
        generated_at_utc="2026-06-03T12:12:00+00:00",
    )
    unavailable_report = {
        "schema_version": "polymarket_status_v1",
        "generated_at_utc": "2026-06-03T13:04:00+00:00",
        "source": "polymarket_status_api",
        "status": "unknown",
        "clob_api": {"status": "unknown", "trading_available": False},
        "blockers": ["polymarket_status_unavailable"],
    }
    reports = [
        normalize_polymarket_status(
            summary={"page": {"name": "Polymarket", "status": "OPERATIONAL"}, "activeMaintenances": [], "activeIncidents": []},
            components=[{"name": "CLOB API", "status": "OPERATIONAL"}],
            generated_at_utc="2026-06-03T13:00:00+00:00",
        ),
        maintenance_report,
        unavailable_report,
    ]
    provider = build_cached_polymarket_status_provider(fetcher=lambda: reports.pop(0), fresh_seconds=0.0, ttl_seconds=120.0, clock=clock)

    assert clob_trading_available(provider()) is True
    now = 110.0
    assert clob_trading_available(provider()) is False
    now = 250.0
    expired = provider()

    assert clob_trading_available(expired) is False
    assert expired["source"] == "polymarket_status_api"
    assert "polymarket_status_unavailable" in expired["blockers"]


def test_cached_polymarket_status_reuses_fresh_operational_status_without_refetch_pytest() -> None:
    now = 100.0
    call_count = 0

    def clock() -> float:
        return now

    def fetcher() -> dict:
        nonlocal call_count
        call_count += 1
        return normalize_polymarket_status(
            summary={"page": {"name": "Polymarket", "status": "OPERATIONAL"}, "activeMaintenances": [], "activeIncidents": []},
            components=[{"name": "CLOB API", "status": "OPERATIONAL"}],
            generated_at_utc="2026-06-03T13:00:00+00:00",
        )

    provider = build_cached_polymarket_status_provider(fetcher=fetcher, fresh_seconds=60.0, ttl_seconds=120.0, clock=clock)

    assert clob_trading_available(provider()) is True
    now = 130.0
    second = provider()

    assert call_count == 1
    assert clob_trading_available(second) is True
    assert second["source"] == "polymarket_status_api_cache"
    assert second["cache_fallback_used"] is False


def test_fetch_polymarket_status_uses_status_page_fallback_when_json_api_unavailable_pytest(monkeypatch) -> None:
    def fail_json(*args, **kwargs):
        raise OSError("json api unavailable")

    def html_page(*args, **kwargs):
        return """
        <html>
          <body>
            <h2>All systems operational</h2>
            <div>CLOB API - Operational</div>
          </body>
        </html>
        """

    monkeypatch.setattr("crypto_options_app.feeds.polymarket_status._fetch_json", fail_json)
    monkeypatch.setattr("crypto_options_app.feeds.polymarket_status._fetch_text", html_page)

    report = fetch_polymarket_status(timeout_seconds=0.1)

    assert report["source"] == "polymarket_status_page_html_fallback"
    assert report["status"] == "operational"
    assert report["clob_api"]["trading_available"] is True
    assert clob_trading_available(report) is True
    assert report["blockers"] == []


def test_fetch_polymarket_status_fallback_still_blocks_when_clob_not_operational_pytest(monkeypatch) -> None:
    def fail_json(*args, **kwargs):
        raise OSError("json api unavailable")

    def html_page(*args, **kwargs):
        return """
        <html>
          <body>
            <h2>Partial outage</h2>
            <div>CLOB API - Under maintenance</div>
          </body>
        </html>
        """

    monkeypatch.setattr("crypto_options_app.feeds.polymarket_status._fetch_json", fail_json)
    monkeypatch.setattr("crypto_options_app.feeds.polymarket_status._fetch_text", html_page)

    report = fetch_polymarket_status(timeout_seconds=0.1)

    assert report["source"] == "polymarket_status_page_html_fallback"
    assert clob_trading_available(report) is False
    assert "exchange_status_not_operational" in report["blockers"]
    assert "polymarket_page_not_operational" in report["blockers"]
