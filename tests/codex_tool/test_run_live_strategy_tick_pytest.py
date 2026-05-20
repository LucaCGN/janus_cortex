from __future__ import annotations

from datetime import datetime, timezone

import codex_tool.run_live_strategy_tick as live_tick


def test_wnba_event_slug_parsing_and_aliases_pytest() -> None:
    assert live_tick._parse_wnba_event_id("wnba-por-ind-2026-05-20") == ("por", "ind", "2026-05-20")
    assert live_tick._parse_wnba_event_id("nba-sas-okc-2026-05-20") is None
    assert live_tick._wnba_slug_alias("POR") == "pdx"
    assert live_tick._wnba_slug_alias("Indiana") == "ind"


def test_jsonable_mapping_handles_datetime_and_nan_pytest() -> None:
    payload = live_tick._jsonable_mapping(
        {
            "captured_at": datetime(2026, 5, 20, 21, 4, tzinfo=timezone.utc),
            "spread": float("nan"),
            "team": "IND",
        }
    )

    assert payload == {
        "captured_at": "2026-05-20T21:04:00+00:00",
        "spread": None,
        "team": "IND",
    }
