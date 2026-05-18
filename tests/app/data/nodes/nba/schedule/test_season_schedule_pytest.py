from __future__ import annotations

from app.data.nodes.nba.schedule import season_schedule


class _FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "leagueSchedule": {
                "gameDates": [
                    {
                        "gameDate": "05/17/2026 00:00:00",
                        "games": [
                            {
                                "gameId": "0042500204",
                                "gameDateTimeUTC": "2026-05-17T23:00:00Z",
                                "gameStatus": 3,
                                "gameStatusText": "Final",
                                "homeTeam": {
                                    "teamId": 1610612765,
                                    "teamTricode": "DET",
                                    "teamName": "Pistons",
                                    "teamCity": "Detroit",
                                    "score": 94,
                                },
                                "awayTeam": {
                                    "teamId": 1610612739,
                                    "teamTricode": "CLE",
                                    "teamName": "Cavaliers",
                                    "teamCity": "Cleveland",
                                    "score": 125,
                                },
                            }
                        ],
                    }
                ]
            }
        }


def test_fetch_season_schedule_uses_nba_cdn_headers_pytest(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, *, headers: dict[str, str], timeout: int):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(season_schedule.requests, "get", fake_get)

    frame = season_schedule.fetch_season_schedule_df(season="2025-26")

    assert captured["url"] == season_schedule.CDN_URL
    assert captured["headers"]["Referer"] == "https://www.nba.com/"
    assert captured["headers"]["Origin"] == "https://www.nba.com"
    assert frame.iloc[0]["game_date"] == "2026-05-17"
    assert frame.iloc[0]["away_team_slug"] == "CLE"
    assert frame.iloc[0]["home_team_slug"] == "DET"
