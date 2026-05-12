from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.nodes.wnba.schedule.season_schedule import parse_polymarket_wnba_slug


_NAMESPACE = uuid.UUID("96a1c39f-8135-4657-9127-9d01385a6a6f")


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(part) for part in parts)))


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _build_team_aliases(schedule_df: pd.DataFrame) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if schedule_df.empty:
        return aliases
    for _, row in schedule_df.iterrows():
        for side in ("home", "away"):
            tricode = str(row.get(f"{side}_team_tricode") or "").upper()
            if not tricode:
                continue
            values = [
                tricode,
                row.get(f"{side}_team_slug"),
                row.get(f"{side}_team_name"),
                row.get(f"{side}_team_city"),
                f"{row.get(f'{side}_team_city') or ''} {row.get(f'{side}_team_name') or ''}",
            ]
            for value in values:
                key = _norm(value)
                if key:
                    aliases[key] = tricode
    return aliases


def _market_reference_date(market_rows: pd.DataFrame) -> str | None:
    for column in ("game_start_time", "start_time", "event_start_time"):
        if column not in market_rows.columns:
            continue
        for value in market_rows[column].tolist():
            if value is None or value == "":
                continue
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).date().isoformat() if value.tzinfo else value.date().isoformat()
            raw = str(value)
            try:
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                return datetime.fromisoformat(raw).date().isoformat()
            except ValueError:
                continue
    return None


def match_wnba_moneyline_markets_to_schedule(
    moneyline_df: pd.DataFrame,
    schedule_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build passive WNBA moneyline watch targets from Polymarket-like outcome rows."""
    if moneyline_df.empty or schedule_df.empty:
        return pd.DataFrame()

    aliases = _build_team_aliases(schedule_df)
    targets: list[dict[str, Any]] = []
    group_cols = ["market_id"]
    if "event_slug" in moneyline_df.columns:
        group_cols.append("event_slug")

    for group_key, market_rows in moneyline_df.groupby(group_cols, dropna=False):
        market_id = str(group_key[0] if isinstance(group_key, tuple) else group_key)
        event_slug = None
        if isinstance(group_key, tuple) and len(group_key) > 1 and pd.notna(group_key[1]):
            event_slug = str(group_key[1])
        if not event_slug and "event_slug" in market_rows.columns:
            first_slug = market_rows["event_slug"].dropna()
            event_slug = str(first_slug.iloc[0]) if not first_slug.empty else None

        away_slug, home_slug, slug_date = parse_polymarket_wnba_slug(event_slug or "")
        reference_date = slug_date or _market_reference_date(market_rows)
        candidate_games = schedule_df
        if reference_date:
            candidate_games = candidate_games[candidate_games["game_date"].astype(str) == reference_date]
        if candidate_games.empty:
            continue

        outcomes = [str(value) for value in market_rows.get("outcome", pd.Series(dtype=str)).tolist()]
        outcome_tricodes = {aliases.get(_norm(value), str(value).upper()) for value in outcomes}
        match = pd.DataFrame()
        if away_slug and home_slug:
            match = candidate_games[
                (candidate_games["away_team_tricode"].astype(str).str.upper() == away_slug)
                & (candidate_games["home_team_tricode"].astype(str).str.upper() == home_slug)
            ]
        if match.empty and len(outcome_tricodes) >= 2:
            match = candidate_games[
                candidate_games["home_team_tricode"].astype(str).str.upper().isin(outcome_tricodes)
                & candidate_games["away_team_tricode"].astype(str).str.upper().isin(outcome_tricodes)
            ]
        if match.empty:
            continue

        game = match.iloc[0]
        home_tri = str(game.get("home_team_tricode") or "").upper()
        away_tri = str(game.get("away_team_tricode") or "").upper()
        token_by_tri: dict[str, str] = {}
        for _, outcome in market_rows.iterrows():
            tri = aliases.get(_norm(outcome.get("outcome")), str(outcome.get("outcome") or "").upper())
            token_id = outcome.get("token_id")
            if tri and pd.notna(token_id):
                token_by_tri[tri] = str(token_id)

        game_id = str(game.get("game_id"))
        confidence = 0.95 if away_slug and home_slug else 0.75
        event_key = f"wnba-{away_tri.lower()}-{home_tri.lower()}-{str(game.get('game_date'))}"
        targets.append(
            {
                "watch_target_id": _uuid_for("wnba_watch_target", game_id, market_id),
                "game_id": game_id,
                "agentic_event_key": event_key,
                "polymarket_event_slug": event_slug,
                "polymarket_market_id": market_id,
                "home_outcome_token_id": token_by_tri.get(home_tri),
                "away_outcome_token_id": token_by_tri.get(away_tri),
                "match_status": "candidate",
                "passive_only": True,
                "clob_capture_required": True,
                "confidence": confidence,
                "matching_json": {
                    "method": "slug" if away_slug and home_slug else "date_outcome_alias",
                    "outcomes": outcomes,
                    "home_team_tricode": home_tri,
                    "away_team_tricode": away_tri,
                    "reference_date": reference_date,
                },
                "watch_plan_json": {
                    "league": "wnba",
                    "market_type": "moneyline",
                    "capture_orderbook_ticks": True,
                    "capture_public_trades": True,
                    "passive_only": True,
                    "orders_allowed": False,
                    "reason": "WNBA CLOB tick/trade capture is required for future replay and backtesting.",
                },
            }
        )
    return pd.DataFrame(targets)
