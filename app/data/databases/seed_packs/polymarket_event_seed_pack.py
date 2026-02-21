from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from nba_api.live.nba.endpoints import scoreboard as nba_live_scoreboard
from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.nodes.polymarket.gamma.nba.fallback_stream_history_collector import (
    NBAFallbackStreamRequest,
    collect_nba_fallback_stream_df,
)
from app.data.nodes.polymarket.gamma.nba.odds_history_node import (
    NBAOddsHistoryRequest,
    fetch_clob_prices_history,
)


logger = logging.getLogger(__name__)

_NAMESPACE = uuid.UUID("ca386461-dc06-46db-83d8-8ce57895b95b")
_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


@dataclass(frozen=True)
class EventProbeConfig:
    step_code: str
    url: str
    event_type_code: str
    history_mode: str  # game_period | rolling_recent | interval_only
    history_market_selector: str  # moneyline | primary | all
    history_interval: str = "1m"
    history_fidelity: int = 10
    recent_lookback_days: int = 7
    allow_snapshot_fallback: bool = True
    stream_enabled: bool = False
    stream_sample_count: int = 0
    stream_sample_interval_sec: float = 1.0
    stream_max_outcomes: int = 30


@dataclass
class EventProbeResult:
    step_code: str
    url: str
    slug: str
    gamma_event_id: str | None
    canonical_event_id: str | None
    event_title: str | None
    status: str
    error_text: str | None = None
    markets_total: int = 0
    markets_seeded: int = 0
    market_state_snapshots_inserted: int = 0
    outcomes_seeded: int = 0
    history_markets_sampled: int = 0
    history_points_fetched: int = 0
    history_points_inserted: int = 0
    history_window_start: datetime | None = None
    history_window_end: datetime | None = None
    history_sources: dict[str, int] = field(default_factory=dict)
    stream_rows_fetched: int = 0
    stream_rows_inserted: int = 0
    stream_samples_requested: int = 0
    unique_price_cents: int = 0
    min_price: float | None = None
    max_price: float | None = None


@dataclass
class EventSeedPackSummary:
    sync_run_id: str | None
    persisted: bool
    rows_read: int
    rows_written: int
    status: str
    results: list[EventProbeResult]


DEFAULT_EXTRA_EVENT_PROBES: tuple[EventProbeConfig, ...] = (
    EventProbeConfig(
        step_code="extra_3_7_past_nba_game_period",
        url="https://polymarket.com/sports/nba/nba-mem-den-2026-02-11",
        event_type_code="sports_nba_game",
        history_mode="game_period",
        history_market_selector="moneyline",
        history_interval="1m",
        history_fidelity=10,
    ),
    EventProbeConfig(
        step_code="extra_3_8_upcoming_nba_availability",
        url="https://polymarket.com/sports/nba/nba-ind-was-2026-02-19",
        event_type_code="sports_nba_game",
        history_mode="rolling_recent",
        history_market_selector="moneyline",
        history_interval="1m",
        history_fidelity=10,
        recent_lookback_days=7,
    ),
    EventProbeConfig(
        step_code="extra_3_9_aliens_grid_history",
        url="https://polymarket.com/event/will-the-us-confirm-that-aliens-exist-before-2027",
        event_type_code="culture_binary_event",
        history_mode="interval_only",
        history_market_selector="primary",
        history_interval="1m",
        history_fidelity=10,
    ),
)

_POLYMARKET_NBA_EVENT_URL_PREFIX = "https://polymarket.com/sports/nba"


@dataclass(frozen=True)
class ScoreboardProbeSelection:
    finished: list[EventProbeConfig]
    live: list[EventProbeConfig]
    upcoming: list[EventProbeConfig]

    @property
    def all(self) -> list[EventProbeConfig]:
        return [*self.finished, *self.live, *self.upcoming]


def _uuid_for(*parts: str) -> str:
    key = "|".join(parts)
    return str(uuid.uuid5(_NAMESPACE, key))


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(loaded, list):
            return loaded
    return []


def _parse_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 1.0:
        parsed = parsed / 100.0
    if parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slug_from_polymarket_url(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        raise ValueError(f"Unable to extract slug from URL: {url}")
    return segments[-1]


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_slug_part(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_scoreboard_game_slug(game: dict[str, Any]) -> str | None:
    away = _safe_slug_part(game.get("awayTeam", {}).get("teamTricode"))
    home = _safe_slug_part(game.get("homeTeam", {}).get("teamTricode"))
    game_et = str(game.get("gameEt") or "").strip()
    if not away or not home or len(game_et) < 10:
        return None
    game_date = game_et[:10]
    return f"nba-{away}-{home}-{game_date}"


def _fetch_live_scoreboard_games() -> list[dict[str, Any]]:
    try:
        board = nba_live_scoreboard.ScoreBoard()
        games = board.games.get_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_today_nba_event_probes_from_scoreboard: scoreboard fetch failed error=%r", exc)
        return []
    if not isinstance(games, list):
        return []
    return [item for item in games if isinstance(item, dict)]


def build_today_nba_event_probes_from_scoreboard(
    *,
    max_finished: int = 2,
    max_live: int = 2,
    max_upcoming: int = 1,
    include_upcoming: bool = False,
    stream_sample_count: int = 3,
    stream_sample_interval_sec: float = 1.0,
    stream_max_outcomes: int = 30,
    games: list[dict[str, Any]] | None = None,
) -> ScoreboardProbeSelection:
    rows = games if games is not None else _fetch_live_scoreboard_games()

    finished: list[EventProbeConfig] = []
    live: list[EventProbeConfig] = []
    upcoming: list[EventProbeConfig] = []
    seen_slugs: set[str] = set()

    for game in rows:
        slug = _extract_scoreboard_game_slug(game)
        status = _coerce_int(game.get("gameStatus"))
        if not slug or status is None:
            continue
        if slug in seen_slugs:
            continue

        if status == 3 and len(finished) < max(max_finished, 0):
            finished.append(
                EventProbeConfig(
                    step_code=f"v0_4_1_today_finished_{slug}",
                    url=f"{_POLYMARKET_NBA_EVENT_URL_PREFIX}/{slug}",
                    event_type_code="sports_nba_game",
                    history_mode="game_period",
                    history_market_selector="moneyline",
                    history_interval="1m",
                    history_fidelity=10,
                    recent_lookback_days=2,
                    allow_snapshot_fallback=True,
                )
            )
            seen_slugs.add(slug)
            continue

        if status == 2 and len(live) < max(max_live, 0):
            live.append(
                EventProbeConfig(
                    step_code=f"v0_4_1_today_live_{slug}",
                    url=f"{_POLYMARKET_NBA_EVENT_URL_PREFIX}/{slug}",
                    event_type_code="sports_nba_game",
                    history_mode="rolling_recent",
                    history_market_selector="moneyline",
                    history_interval="1m",
                    history_fidelity=10,
                    recent_lookback_days=1,
                    allow_snapshot_fallback=True,
                    stream_enabled=True,
                    stream_sample_count=max(stream_sample_count, 1),
                    stream_sample_interval_sec=max(stream_sample_interval_sec, 0.0),
                    stream_max_outcomes=max(stream_max_outcomes, 1),
                )
            )
            seen_slugs.add(slug)
            continue

        if include_upcoming and status == 1 and len(upcoming) < max(max_upcoming, 0):
            upcoming.append(
                EventProbeConfig(
                    step_code=f"v0_4_1_today_upcoming_{slug}",
                    url=f"{_POLYMARKET_NBA_EVENT_URL_PREFIX}/{slug}",
                    event_type_code="sports_nba_game",
                    history_mode="rolling_recent",
                    history_market_selector="moneyline",
                    history_interval="1m",
                    history_fidelity=10,
                    recent_lookback_days=2,
                    allow_snapshot_fallback=True,
                )
            )
            seen_slugs.add(slug)

    return ScoreboardProbeSelection(finished=finished, live=live, upcoming=upcoming)


def _fetch_gamma_event_by_slug(slug: str) -> dict[str, Any]:
    url = f"{_GAMMA_BASE_URL}/events/slug/{slug}"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected payload type for slug={slug}: {type(payload)}")
    return payload


def _event_type_defaults(code: str) -> tuple[str, str]:
    if code.startswith("sports_nba"):
        return "NBA Event", "sports"
    if code.startswith("culture"):
        return "Culture Binary Event", "culture"
    return "General Event", "general"


def _select_history_markets(markets: list[dict[str, Any]], selector: str) -> list[dict[str, Any]]:
    if not markets:
        return []
    if selector == "all":
        return list(markets)
    if selector == "primary":
        return [markets[0]]
    if selector == "moneyline":
        picked = [
            market
            for market in markets
            if str(market.get("sportsMarketType") or "").strip().lower() == "moneyline"
        ]
        if picked:
            return picked
        return [markets[0]]
    return [markets[0]]


def _history_window_for_market(
    config: EventProbeConfig,
    event_payload: dict[str, Any],
    market_payload: dict[str, Any],
    now: datetime,
) -> tuple[datetime | None, datetime | None]:
    if config.history_mode == "interval_only":
        return None, None

    if config.history_mode == "rolling_recent":
        start_dt = now - timedelta(days=max(config.recent_lookback_days, 1))
        return start_dt, now

    if config.history_mode == "game_period":
        reference = (
            _parse_dt(market_payload.get("gameStartTime"))
            or _parse_dt(event_payload.get("endDate") or event_payload.get("endTime"))
            or _parse_dt(event_payload.get("startDate") or event_payload.get("startTime"))
        )
        if reference is None:
            return now - timedelta(days=1), now
        return reference - timedelta(hours=6), reference + timedelta(hours=4)

    return now - timedelta(days=1), now


def _ensure_baselines(
    repo: JanusUpsertRepository,
    event_type_codes: set[str],
) -> dict[str, str]:
    gamma_provider_id = repo.upsert_provider(
        provider_id=_uuid_for("provider", "gamma"),
        code="gamma",
        name="Polymarket Gamma",
        category="prediction_market",
        base_url=_GAMMA_BASE_URL,
        auth_type="none",
    )
    repo.upsert_provider(
        provider_id=_uuid_for("provider", "clob"),
        code="clob",
        name="Polymarket CLOB",
        category="prediction_market",
        base_url="https://clob.polymarket.com",
        auth_type="none",
    )
    module_id = repo.upsert_module(
        module_id=_uuid_for("module", "polymarket_event_seed_pack"),
        code="polymarket_event_seed_pack",
        name="Polymarket Event Seed Pack",
        description="Live integration seed pack for event-level DB validation",
        owner="janus",
    )
    profile_id = repo.upsert_information_profile(
        information_profile_id=_uuid_for("information_profile", "seed_pack_live_probe"),
        code="seed_pack_live_probe",
        name="Seed Pack Live Probe",
        description="Manual live probe profile for DB integration seed packs",
        min_sources=1,
        required_fields_json=["title", "status", "source_refs"],
        refresh_interval_sec=300,
    )

    event_type_ids: dict[str, str] = {}
    for code in sorted(event_type_codes):
        name, domain = _event_type_defaults(code)
        event_type_ids[code] = repo.upsert_event_type(
            event_type_id=_uuid_for("event_type", code),
            code=code,
            name=name,
            domain=domain,
        )

    return {
        "gamma_provider_id": gamma_provider_id,
        "module_id": module_id,
        "information_profile_id": profile_id,
        **{f"event_type::{k}": v for k, v in event_type_ids.items()},
    }


def _insert_sync_run(
    connection: Any,
    *,
    provider_id: str,
    module_id: str,
    event_urls: list[str],
) -> str:
    sync_run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.sync_runs (
                sync_run_id, provider_id, module_id, pipeline_name, run_type, status,
                started_at, meta_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                sync_run_id,
                provider_id,
                module_id,
                "db_seed_pack.polymarket_event_probe",
                "manual_seed_pack",
                "running",
                now,
                Json({"event_urls": event_urls}),
            ),
        )
    return sync_run_id


def _update_sync_run(
    connection: Any,
    *,
    sync_run_id: str,
    status: str,
    rows_read: int,
    rows_written: int,
    error_text: str | None = None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE core.sync_runs
            SET status = %s,
                ended_at = %s,
                rows_read = %s,
                rows_written = %s,
                error_text = %s
            WHERE sync_run_id = %s;
            """,
            (status, datetime.now(timezone.utc), rows_read, rows_written, error_text, sync_run_id),
        )


def _insert_raw_payload(
    connection: Any,
    *,
    sync_run_id: str,
    provider_id: str,
    slug: str,
    payload: dict[str, Any],
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.raw_payloads (
                raw_payload_id, sync_run_id, provider_id, endpoint, external_id, fetched_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                str(uuid.uuid4()),
                sync_run_id,
                provider_id,
                f"/events/slug/{slug}",
                str(payload.get("id") or slug),
                datetime.now(timezone.utc),
                Json(payload),
            ),
        )


def _seed_single_event(
    *,
    connection: Any,
    repo: JanusUpsertRepository,
    config: EventProbeConfig,
    baselines: dict[str, str],
    sync_run_id: str,
) -> EventProbeResult:
    slug = _slug_from_polymarket_url(config.url)
    payload = _fetch_gamma_event_by_slug(slug)
    now = datetime.now(timezone.utc)

    gamma_event_id = str(payload.get("id") or slug)
    event_uuid = _uuid_for("event", "gamma", gamma_event_id)

    event_type_id = baselines[f"event_type::{config.event_type_code}"]
    profile_id = baselines["information_profile_id"]
    provider_id = baselines["gamma_provider_id"]

    event_start = _parse_dt(payload.get("startDate") or payload.get("startTime"))
    event_end = _parse_dt(payload.get("endDate") or payload.get("endTime"))
    event_status = "closed" if bool(payload.get("closed")) else "open"
    event_title = str(payload.get("title") or slug)

    _insert_raw_payload(
        connection,
        sync_run_id=sync_run_id,
        provider_id=provider_id,
        slug=slug,
        payload=payload,
    )

    repo.upsert_event(
        event_id=event_uuid,
        event_type_id=event_type_id,
        information_profile_id=profile_id,
        title=event_title,
        status=event_status,
        canonical_slug=slug,
        start_time=event_start,
        end_time=event_end,
        metadata_json={
            "category": payload.get("category"),
            "subcategory": payload.get("subcategory"),
            "volume": payload.get("volume"),
            "liquidity": payload.get("liquidity"),
            "source_url": config.url,
        },
    )
    repo.upsert_event_external_ref(
        event_ref_id=_uuid_for("event_ref", "gamma", gamma_event_id),
        event_id=event_uuid,
        provider_id=provider_id,
        external_id=gamma_event_id,
        external_slug=slug,
        external_url=config.url,
        is_primary=True,
        raw_summary_json={
            "title": event_title,
            "startDate": payload.get("startDate"),
            "endDate": payload.get("endDate"),
            "closed": payload.get("closed"),
        },
    )

    markets_raw = payload.get("markets")
    if not isinstance(markets_raw, list):
        markets_raw = []

    outcome_uuid_by_market_and_index: dict[tuple[str, int], str] = {}
    outcome_uuid_by_market_and_label: dict[tuple[str, str], str] = {}
    outcome_uuid_by_token: dict[str, str] = {}
    market_rows_seeded = 0
    market_state_snapshots_inserted = 0
    outcome_rows_seeded = 0

    for market in markets_raw:
        if not isinstance(market, dict):
            continue
        external_market_id = str(market.get("id") or "").strip()
        if not external_market_id:
            continue

        market_uuid = _uuid_for("market", "gamma", external_market_id)
        market_slug = market.get("slug")
        market_question = str(market.get("question") or market_slug or external_market_id)
        market_type = str(market.get("sportsMarketType") or market.get("marketType") or "binary")
        market_start = _parse_dt(market.get("startDate"))
        market_end = _parse_dt(market.get("endDate"))
        settlement_status = "closed" if bool(market.get("closed")) else "open"
        outcome_prices = _parse_json_list(market.get("outcomePrices"))
        parsed_probs = [_parse_price(item) for item in outcome_prices]
        parsed_probs = [item for item in parsed_probs if item is not None]
        last_price = parsed_probs[0] if parsed_probs else _parse_price(market.get("lastTradePrice"))
        best_bid = _parse_price(market.get("bestBid"))
        best_ask = _parse_price(market.get("bestAsk"))
        mid_price = (
            (best_bid + best_ask) / 2.0
            if best_bid is not None and best_ask is not None
            else last_price
        )
        volume = _parse_float(market.get("volume") or market.get("volumeNum"))
        liquidity = _parse_float(market.get("liquidity") or market.get("liquidityNum"))

        repo.upsert_market(
            market_id=market_uuid,
            event_id=event_uuid,
            question=market_question,
            market_type=market_type,
            condition_id=str(market.get("conditionId")) if market.get("conditionId") is not None else None,
            market_slug=str(market_slug) if market_slug is not None else None,
            open_time=market_start,
            close_time=market_end,
            settlement_status=settlement_status,
            metadata_json={
                "enableOrderBook": market.get("enableOrderBook"),
                "volume": market.get("volume"),
                "liquidity": market.get("liquidity"),
            },
        )
        inserted_state = repo.insert_market_state_snapshot(
            market_state_snapshot_id=str(uuid.uuid4()),
            market_id=market_uuid,
            sync_run_id=sync_run_id,
            captured_at=now,
            last_price=last_price,
            volume=volume,
            liquidity=liquidity,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            market_status=settlement_status,
            raw_json={
                "outcomePrices": outcome_prices,
                "closed": market.get("closed"),
                "enableOrderBook": market.get("enableOrderBook"),
            },
            ignore_duplicates=True,
        )
        if inserted_state:
            market_state_snapshots_inserted += 1
        repo.upsert_market_external_ref(
            market_ref_id=_uuid_for("market_ref", "gamma", external_market_id),
            market_id=market_uuid,
            provider_id=provider_id,
            external_market_id=external_market_id,
            external_condition_id=str(market.get("conditionId")) if market.get("conditionId") is not None else None,
            external_slug=str(market_slug) if market_slug is not None else None,
            raw_summary_json={
                "question": market_question,
                "sportsMarketType": market.get("sportsMarketType"),
                "closed": market.get("closed"),
            },
        )
        market_rows_seeded += 1

        outcomes = _parse_json_list(market.get("outcomes"))
        tokens = _parse_json_list(market.get("clobTokenIds") or market.get("clobTokenIDs"))
        prices = outcome_prices

        for idx, raw_label in enumerate(outcomes):
            label = str(raw_label).strip() or f"outcome_{idx}"
            token_id = str(tokens[idx]).strip() if idx < len(tokens) else None
            token_id = token_id or None
            implied = _parse_price(prices[idx]) if idx < len(prices) else None

            outcome_uuid = repo.upsert_outcome(
                outcome_id=_uuid_for("outcome", "gamma", external_market_id, str(idx)),
                market_id=market_uuid,
                outcome_index=idx,
                outcome_label=label,
                token_id=token_id,
                is_winner=None,
                metadata_json={"source_market_type": market_type, "implied_prob": implied},
            )
            outcome_uuid_by_market_and_index[(external_market_id, idx)] = outcome_uuid
            outcome_uuid_by_market_and_label[(external_market_id, label.lower())] = outcome_uuid
            if token_id:
                outcome_uuid_by_token[token_id] = outcome_uuid
            outcome_rows_seeded += 1

    history_markets = _select_history_markets(
        [m for m in markets_raw if isinstance(m, dict)],
        selector=config.history_market_selector,
    )
    history_markets_sampled = 0
    history_points_fetched = 0
    history_points_inserted = 0
    history_sources: dict[str, int] = {}
    stream_rows_fetched = 0
    stream_rows_inserted = 0
    unique_price_cents: set[int] = set()
    min_price: float | None = None
    max_price: float | None = None
    windows: list[tuple[datetime | None, datetime | None]] = []

    for market in history_markets:
        external_market_id = str(market.get("id") or "").strip()
        if not external_market_id:
            continue

        start_dt, end_dt = _history_window_for_market(config, payload, market, now)
        windows.append((start_dt, end_dt))
        req = NBAOddsHistoryRequest(
            start_date_min=start_dt,
            start_date_max=end_dt,
            interval=config.history_interval,
            fidelity=config.history_fidelity,
            retries=1,
            allow_snapshot_fallback=config.allow_snapshot_fallback,
        )
        outcomes = _parse_json_list(market.get("outcomes"))
        tokens = _parse_json_list(market.get("clobTokenIds") or market.get("clobTokenIDs"))
        prices = _parse_json_list(market.get("outcomePrices"))
        history_markets_sampled += 1

        for idx, token in enumerate(tokens):
            token_id = str(token).strip()
            if not token_id:
                continue
            outcome_uuid = outcome_uuid_by_market_and_index.get((external_market_id, idx))
            if outcome_uuid is None:
                continue

            points = fetch_clob_prices_history(token_id=token_id, req=req, session=requests)
            if points:
                history_points_fetched += len(points)
                history_sources["clob_prices_history"] = history_sources.get("clob_prices_history", 0) + len(points)
                for point in points:
                    point_price = _parse_price(point.get("price"))
                    if point_price is None:
                        continue
                    inserted = repo.insert_outcome_price_tick(
                        outcome_id=outcome_uuid,
                        ts=point["ts"],
                        source="clob_prices_history",
                        price=point_price,
                        raw_json=point.get("raw") if isinstance(point.get("raw"), dict) else None,
                        ignore_duplicates=True,
                    )
                    if inserted:
                        history_points_inserted += 1
                    cents = int(round(point_price * 100))
                    unique_price_cents.add(cents)
                    min_price = point_price if min_price is None else min(min_price, point_price)
                    max_price = point_price if max_price is None else max(max_price, point_price)
                continue

            if config.allow_snapshot_fallback:
                snap_price = _parse_price(prices[idx]) if idx < len(prices) else None
                if snap_price is not None:
                    inserted = repo.insert_outcome_price_tick(
                        outcome_id=outcome_uuid,
                        ts=now,
                        source="snapshot_fallback",
                        price=snap_price,
                        raw_json={"fallback_reason": "empty_clob_history", "token_id": token_id},
                        ignore_duplicates=True,
                    )
                    history_points_fetched += 1
                    history_sources["snapshot_fallback"] = history_sources.get("snapshot_fallback", 0) + 1
                    if inserted:
                        history_points_inserted += 1
                    cents = int(round(snap_price * 100))
                    unique_price_cents.add(cents)
                    min_price = snap_price if min_price is None else min(min_price, snap_price)
                    max_price = snap_price if max_price is None else max(max_price, snap_price)

    if config.stream_enabled and config.stream_sample_count > 0:
        stream_start = (event_start - timedelta(hours=4)) if event_start else now - timedelta(hours=8)
        stream_end = (event_end + timedelta(hours=2)) if event_end else now + timedelta(hours=8)
        stream_req = NBAFallbackStreamRequest(
            only_open=False,
            start_date_min=stream_start,
            start_date_max=stream_end,
            use_events_fallback=True,
            sample_count=config.stream_sample_count,
            sample_interval_sec=config.stream_sample_interval_sec,
            max_outcomes=config.stream_max_outcomes,
            max_pages=15,
            retries_per_sample=1,
            retry_backoff_sec=0.5,
            continue_on_error=True,
        )
        try:
            stream_df = collect_nba_fallback_stream_df(req=stream_req)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "seed_pack_event_stream_failed step=%s slug=%s error=%r",
                config.step_code,
                slug,
                exc,
            )
            stream_df = None
        if stream_df is not None and not stream_df.empty:
            if "event_slug" in stream_df.columns:
                stream_df = stream_df[
                    stream_df["event_slug"].astype(str).str.strip().str.lower() == slug.lower()
                ].reset_index(drop=True)
            for _, row in stream_df.iterrows():
                token_id = str(row.get("token_id") or "").strip()
                market_id = str(row.get("market_id") or "").strip()
                label = str(row.get("outcome") or "").strip().lower()
                outcome_uuid = None
                if token_id:
                    outcome_uuid = outcome_uuid_by_token.get(token_id)
                if outcome_uuid is None and market_id and label:
                    outcome_uuid = outcome_uuid_by_market_and_label.get((market_id, label))
                if outcome_uuid is None:
                    continue
                ts = _parse_dt(row.get("ts"))
                if ts is None:
                    continue
                tick_price = _parse_price(row.get("last_price"))
                if tick_price is None:
                    tick_price = _parse_price(row.get("implied_prob"))
                if tick_price is None:
                    continue

                raw_json = {
                    "event_slug": str(row.get("event_slug") or ""),
                    "market_id": market_id,
                    "outcome": str(row.get("outcome") or ""),
                    "token_id": token_id,
                    "sample_no": _coerce_int(row.get("sample_no")),
                    "ingestion_source": str(row.get("ingestion_source") or ""),
                }
                inserted = repo.insert_outcome_price_tick(
                    outcome_id=outcome_uuid,
                    ts=ts,
                    source="fallback_stream",
                    price=tick_price,
                    raw_json=raw_json,
                    ignore_duplicates=True,
                )
                stream_rows_fetched += 1
                history_points_fetched += 1
                history_sources["fallback_stream"] = history_sources.get("fallback_stream", 0) + 1
                if inserted:
                    stream_rows_inserted += 1
                    history_points_inserted += 1
                cents = int(round(tick_price * 100))
                unique_price_cents.add(cents)
                min_price = tick_price if min_price is None else min(min_price, tick_price)
                max_price = tick_price if max_price is None else max(max_price, tick_price)
            logger.info(
                "seed_pack_event_stream_ingested step=%s slug=%s rows=%d inserted=%d",
                config.step_code,
                slug,
                stream_rows_fetched,
                stream_rows_inserted,
            )
        if stream_rows_fetched == 0:
            logger.info(
                "seed_pack_event_stream_empty_using_slug_snapshot_fallback step=%s slug=%s",
                config.step_code,
                slug,
            )
            for sample_no in range(config.stream_sample_count):
                sample_ts = datetime.now(timezone.utc)
                try:
                    sample_payload = _fetch_gamma_event_by_slug(slug)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "seed_pack_event_stream_slug_sample_failed step=%s slug=%s sample_no=%d error=%r",
                        config.step_code,
                        slug,
                        sample_no + 1,
                        exc,
                    )
                    continue
                sample_markets = sample_payload.get("markets")
                if not isinstance(sample_markets, list):
                    sample_markets = []
                for market in sample_markets:
                    if not isinstance(market, dict):
                        continue
                    external_market_id = str(market.get("id") or "").strip()
                    if not external_market_id:
                        continue
                    sample_tokens = _parse_json_list(market.get("clobTokenIds") or market.get("clobTokenIDs"))
                    sample_outcomes = _parse_json_list(market.get("outcomes"))
                    sample_prices = _parse_json_list(market.get("outcomePrices"))

                    for idx, _ in enumerate(sample_outcomes):
                        outcome_uuid = outcome_uuid_by_market_and_index.get((external_market_id, idx))
                        if outcome_uuid is None:
                            continue
                        tick_price = _parse_price(sample_prices[idx]) if idx < len(sample_prices) else None
                        if tick_price is None:
                            continue
                        token_id = str(sample_tokens[idx]).strip() if idx < len(sample_tokens) else ""
                        inserted = repo.insert_outcome_price_tick(
                            outcome_id=outcome_uuid,
                            ts=sample_ts,
                            source="fallback_stream",
                            price=tick_price,
                            raw_json={
                                "fallback_reason": "event_slug_snapshot_stream",
                                "sample_no": sample_no + 1,
                                "token_id": token_id,
                                "market_id": external_market_id,
                            },
                            ignore_duplicates=True,
                        )
                        stream_rows_fetched += 1
                        history_points_fetched += 1
                        history_sources["fallback_stream"] = history_sources.get("fallback_stream", 0) + 1
                        if inserted:
                            stream_rows_inserted += 1
                            history_points_inserted += 1
                        cents = int(round(tick_price * 100))
                        unique_price_cents.add(cents)
                        min_price = tick_price if min_price is None else min(min_price, tick_price)
                        max_price = tick_price if max_price is None else max(max_price, tick_price)
                if sample_no < config.stream_sample_count - 1 and config.stream_sample_interval_sec > 0:
                    time.sleep(config.stream_sample_interval_sec)

    window_start = None
    window_end = None
    concrete_starts = [start for start, _ in windows if start is not None]
    concrete_ends = [end for _, end in windows if end is not None]
    if concrete_starts:
        window_start = min(concrete_starts)
    if concrete_ends:
        window_end = max(concrete_ends)

    return EventProbeResult(
        step_code=config.step_code,
        url=config.url,
        slug=slug,
        gamma_event_id=gamma_event_id,
        canonical_event_id=event_uuid,
        event_title=event_title,
        status="ok",
        markets_total=len(markets_raw),
        markets_seeded=market_rows_seeded,
        market_state_snapshots_inserted=market_state_snapshots_inserted,
        outcomes_seeded=outcome_rows_seeded,
        history_markets_sampled=history_markets_sampled,
        history_points_fetched=history_points_fetched,
        history_points_inserted=history_points_inserted,
        history_window_start=window_start,
        history_window_end=window_end,
        history_sources=history_sources,
        stream_rows_fetched=stream_rows_fetched,
        stream_rows_inserted=stream_rows_inserted,
        stream_samples_requested=config.stream_sample_count if config.stream_enabled else 0,
        unique_price_cents=len(unique_price_cents),
        min_price=min_price,
        max_price=max_price,
    )


def run_polymarket_event_seed_pack(
    configs: list[EventProbeConfig] | None = None,
    *,
    persist: bool = True,
) -> EventSeedPackSummary:
    selected = configs or list(DEFAULT_EXTRA_EVENT_PROBES)
    if not selected:
        return EventSeedPackSummary(
            sync_run_id=None,
            persisted=persist,
            rows_read=0,
            rows_written=0,
            status="success",
            results=[],
        )

    if not persist:
        raise ValueError("persist=False mode is not supported for this seed pack.")

    rows_read = 0
    rows_written = 0
    results: list[EventProbeResult] = []
    sync_run_id: str | None = None

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        baselines = _ensure_baselines(repo, event_type_codes={cfg.event_type_code for cfg in selected})
        sync_run_id = _insert_sync_run(
            connection,
            provider_id=baselines["gamma_provider_id"],
            module_id=baselines["module_id"],
            event_urls=[cfg.url for cfg in selected],
        )
        connection.commit()

        for config in selected:
            try:
                result = _seed_single_event(
                    connection=connection,
                    repo=repo,
                    config=config,
                    baselines=baselines,
                    sync_run_id=sync_run_id,
                )
                rows_read += result.markets_total + result.history_points_fetched
                rows_written += (
                    1
                    + result.markets_seeded
                    + result.market_state_snapshots_inserted
                    + result.outcomes_seeded
                    + result.history_points_inserted
                )
                results.append(result)
                connection.commit()
            except Exception as exc:  # noqa: BLE001
                connection.rollback()
                logger.exception("Seed probe failed for %s", config.url)
                results.append(
                    EventProbeResult(
                        step_code=config.step_code,
                        url=config.url,
                        slug=_slug_from_polymarket_url(config.url),
                        gamma_event_id=None,
                        canonical_event_id=None,
                        event_title=None,
                        status="error",
                        error_text=repr(exc),
                    )
                )

        had_errors = any(item.status != "ok" for item in results)
        status = "partial_success" if had_errors else "success"
        error_text = "; ".join(
            f"{item.step_code}: {item.error_text}" for item in results if item.error_text
        ) or None

        _update_sync_run(
            connection,
            sync_run_id=sync_run_id,
            status=status,
            rows_read=rows_read,
            rows_written=rows_written,
            error_text=error_text,
        )
        connection.commit()

    return EventSeedPackSummary(
        sync_run_id=sync_run_id,
        persisted=True,
        rows_read=rows_read,
        rows_written=rows_written,
        status="partial_success" if any(item.status != "ok" for item in results) else "success",
        results=results,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Polymarket live seed pack probes (static extras or dynamic today-NBA set)."
    )
    parser.add_argument(
        "--probe-set",
        choices=["extras", "today_nba", "combined"],
        default="extras",
        help="Probe set to execute. 'today_nba' discovers finished/live games from NBA scoreboard.",
    )
    parser.add_argument(
        "--step",
        action="append",
        default=[],
        help="Optional step_code filter (can be repeated). Default runs all extras.",
    )
    parser.add_argument("--max-finished", type=int, default=2)
    parser.add_argument("--max-live", type=int, default=2)
    parser.add_argument("--max-upcoming", type=int, default=1)
    parser.add_argument(
        "--include-upcoming",
        action="store_true",
        help="Include scheduled-yet-not-live games from today's scoreboard set.",
    )
    parser.add_argument("--stream-sample-count", type=int, default=3)
    parser.add_argument("--stream-sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--stream-max-outcomes", type=int, default=30)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    selected: list[EventProbeConfig] = []
    if args.probe_set in {"extras", "combined"}:
        selected.extend(DEFAULT_EXTRA_EVENT_PROBES)
    if args.probe_set in {"today_nba", "combined"}:
        today = build_today_nba_event_probes_from_scoreboard(
            max_finished=args.max_finished,
            max_live=args.max_live,
            max_upcoming=args.max_upcoming,
            include_upcoming=args.include_upcoming,
            stream_sample_count=args.stream_sample_count,
            stream_sample_interval_sec=args.stream_sample_interval_sec,
            stream_max_outcomes=args.stream_max_outcomes,
        )
        selected.extend(today.all)

    if not selected:
        print("No probes selected.")
        return 2

    if args.step:
        wanted = set(args.step)
        selected = [item for item in selected if item.step_code in wanted]
        missing = sorted(wanted - {item.step_code for item in selected})
        if missing:
            print(f"Unknown step_code values: {', '.join(missing)}")
            return 2

    summary = run_polymarket_event_seed_pack(selected, persist=True)
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
    for result in summary.results:
        print(
            " | ".join(
                [
                    f"step={result.step_code}",
                    f"status={result.status}",
                    f"slug={result.slug}",
                    f"markets_seeded={result.markets_seeded}",
                    f"market_state_snapshots={result.market_state_snapshots_inserted}",
                    f"outcomes_seeded={result.outcomes_seeded}",
                    f"history_fetched={result.history_points_fetched}",
                    f"history_inserted={result.history_points_inserted}",
                    f"stream_fetched={result.stream_rows_fetched}",
                    f"stream_inserted={result.stream_rows_inserted}",
                    f"unique_price_cents={result.unique_price_cents}",
                ]
            )
        )
        if result.error_text:
            print(f"error={result.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
