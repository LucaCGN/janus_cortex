from __future__ import annotations

import json
import logging

import pandas as pd
import pytest

from app.data.nodes.nba.live import live_stats, play_by_play
from app.data.nodes.nba.live.play_by_play import PlayByPlayRequest


def test_fetch_live_scoreboard_transient_decode_error_is_rate_limited_pytest(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _BrokenBoxScore:
        def __init__(self, game_id: str) -> None:
            raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(live_stats.boxscore, "BoxScore", _BrokenBoxScore)
    live_stats._PROVIDER_ERROR_LOG_STATE.clear()

    with caplog.at_level(logging.DEBUG):
        assert live_stats.fetch_live_scoreboard("0042500104") == {}
        assert live_stats.fetch_live_scoreboard("0042500104") == {}

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    debugs = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert "transient decode failure" in warnings[0].message
    assert any("suppressed" in record.message for record in debugs)


def test_fetch_play_by_play_transient_decode_error_is_rate_limited_pytest(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _BrokenPlayByPlay:
        def __init__(self, game_id: str) -> None:
            raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(play_by_play.playbyplay, "PlayByPlay", _BrokenPlayByPlay)
    play_by_play._PROVIDER_ERROR_LOG_STATE.clear()

    with caplog.at_level(logging.DEBUG):
        first = play_by_play.fetch_play_by_play_df(PlayByPlayRequest(game_id="0042500104"))
        second = play_by_play.fetch_play_by_play_df(PlayByPlayRequest(game_id="0042500104"))

    assert isinstance(first, pd.DataFrame) and first.empty
    assert isinstance(second, pd.DataFrame) and second.empty
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    debugs = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert "transient decode failure" in warnings[0].message
    assert any("suppressed" in record.message for record in debugs)
