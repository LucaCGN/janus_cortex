from __future__ import annotations

from types import SimpleNamespace

from tools import run_janus_operational_cycle as operational_cycle


def test_summaries_need_attention_detects_failed_api_calls_pytest() -> None:
    summaries = [
        {
            "stage": "data-refresh",
            "failed_calls": [
                {
                    "path": "/v1/sync/polymarket/events",
                    "result": {"ok": False, "status_code": 422},
                }
            ],
        }
    ]

    assert operational_cycle._summaries_need_attention(summaries) is True


def test_summaries_need_attention_detects_not_ok_stage_result_pytest() -> None:
    summaries = [{"stage": "live-test", "result": {"ok": False, "error": "missing run"}}]

    assert operational_cycle._summaries_need_attention(summaries) is True


def test_summaries_need_attention_detects_blocked_api_call_pytest() -> None:
    summaries = [
        {
            "stage": "data-refresh",
            "calls": [
                {
                    "path": "/v1/sync/polymarket/events",
                    "result": {"ok": True, "status": "blocked"},
                }
            ],
        }
    ]

    assert operational_cycle._summaries_need_attention(summaries) is True


def test_summaries_need_attention_allows_clean_stage_pytest() -> None:
    summaries = [{"stage": "data-refresh", "failed_calls": [], "result": {"ok": True}}]

    assert operational_cycle._summaries_need_attention(summaries) is False


def test_data_refresh_sends_account_catalog_backfill_limit_pytest(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    def fake_api_json(api_root, method, path, payload=None, **kwargs):
        calls.append((path, payload))
        if path == "/v1/nba/games":
            return {"ok": True, "items": []}
        return {"ok": True, "status": "success", "summary": {"rows_read": 0, "rows_written": 0}}

    monkeypatch.setattr(operational_cycle, "_api_json", fake_api_json)
    monkeypatch.setattr(operational_cycle, "_discover_games", lambda *args, **kwargs: [])

    args = SimpleNamespace(
        api_root="http://janus.local",
        season="2025-26",
        session_date="2026-05-18",
        game_ids=[],
        account_catalog_backfill_limit=100,
    )

    operational_cycle._run_data_refresh(args, tmp_path)

    portfolio_payloads = [
        payload
        for path, payload in calls
        if path.startswith("/v1/sync/polymarket/")
        and path.rsplit("/", 1)[-1] in {"positions", "orders", "trades"}
    ]
    assert portfolio_payloads
    assert all(payload and payload["account_catalog_backfill_limit"] == 100 for payload in portfolio_payloads)
