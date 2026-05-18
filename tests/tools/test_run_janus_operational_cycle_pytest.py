from __future__ import annotations

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
