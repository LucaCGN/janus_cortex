from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba import sync_postgres as sync


def test_select_scoreboard_context_game_ids_includes_capped_finished_games_pytest() -> None:
    selected = sync._select_scoreboard_context_game_ids(
        ongoing_game_ids=["live-1", "live-2"],
        finished_game_ids=["final-1", "final-2", "final-3"],
        include_final_context=True,
        final_context_game_limit=2,
    )

    assert selected == ["live-1", "live-2", "final-1", "final-2"]


def test_select_scoreboard_context_game_ids_can_skip_finished_games_pytest() -> None:
    selected = sync._select_scoreboard_context_game_ids(
        ongoing_game_ids=["live-1"],
        finished_game_ids=["final-1"],
        include_final_context=False,
        final_context_game_limit=4,
    )

    assert selected == ["live-1"]


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.connection.statements.append((query, params))

    def fetchone(self) -> tuple[int] | None:
        return (1,)

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []

    def cursor(self, *args: object, **kwargs: object) -> _FakeCursor:
        _ = (args, kwargs)
        return _FakeCursor(self)


class _FakeRepo:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, object]] = []
        self.play_by_play: list[dict[str, object]] = []

    def insert_nba_live_game_snapshot(self, **kwargs: object) -> bool:
        self.snapshots.append(kwargs)
        return True

    def upsert_nba_play_by_play_event(self, **kwargs: object) -> bool:
        self.play_by_play.append(kwargs)
        return True


def test_sync_nba_game_live_context_persists_final_snapshot_and_play_by_play_pytest(monkeypatch) -> None:
    repo = _FakeRepo()
    connection = _FakeConnection()

    monkeypatch.setattr(
        sync,
        "fetch_live_scoreboard",
        lambda game_id: {
            "game_id": game_id,
            "game_status": 3,
            "game_status_text": "Final",
            "period": 4,
            "game_clock": "",
            "home_score": 116,
            "visitor_score": 109,
        },
    )
    monkeypatch.setattr(
        sync,
        "fetch_play_by_play_df",
        lambda request: pd.DataFrame(
            [
                {
                    "event_index": 1,
                    "action_id": 101,
                    "period": 4,
                    "clock": "PT00M10.00S",
                    "description": "Final scoring play",
                    "score_home": 116,
                    "score_away": 109,
                    "is_score_change": True,
                    "raw": {"actionId": 101},
                },
                {
                    "event_index": 2,
                    "action_id": 102,
                    "period": 4,
                    "clock": "PT00M00.00S",
                    "description": "Game end",
                    "score_home": 116,
                    "score_away": 109,
                    "is_score_change": False,
                    "raw": {"actionId": 102},
                },
            ]
        ),
    )

    result = sync._sync_nba_game_live_context(
        connection=connection,
        repo=repo,  # type: ignore[arg-type]
        sync_run_id="sync-1",
        provider_id="provider-1",
        game_id="0042500203",
        include_live_snapshots=True,
        include_play_by_play=True,
    )

    assert result.rows_read == 3
    assert result.rows_written == 3
    assert result.live_snapshots_written == 1
    assert result.play_by_play_rows_written == 2
    assert repo.snapshots[0]["game_id"] == "0042500203"
    assert repo.snapshots[0]["period"] == 4
    assert repo.snapshots[0]["home_score"] == 116
    assert repo.snapshots[0]["away_score"] == 109
    assert [row["event_index"] for row in repo.play_by_play] == [1, 2]

    raw_payload_endpoints = [
        str(params[3])
        for query, params in connection.statements
        if "INSERT INTO core.raw_payloads" in query and isinstance(params, tuple)
    ]
    assert raw_payload_endpoints == [
        "/nba/live/0042500203/boxscore",
        "/nba/live/0042500203/play-by-play",
    ]
