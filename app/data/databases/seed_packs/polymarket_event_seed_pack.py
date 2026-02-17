from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
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
    outcomes_seeded: int = 0
    history_markets_sampled: int = 0
    history_points_fetched: int = 0
    history_points_inserted: int = 0
    history_window_start: datetime | None = None
    history_window_end: datetime | None = None
    history_sources: dict[str, int] = field(default_factory=dict)
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


def _slug_from_polymarket_url(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        raise ValueError(f"Unable to extract slug from URL: {url}")
    return segments[-1]


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
    market_rows_seeded = 0
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
        prices = _parse_json_list(market.get("outcomePrices"))

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
            outcome_rows_seeded += 1

    history_markets = _select_history_markets(
        [m for m in markets_raw if isinstance(m, dict)],
        selector=config.history_market_selector,
    )
    history_markets_sampled = 0
    history_points_fetched = 0
    history_points_inserted = 0
    history_sources: dict[str, int] = {}
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
        outcomes_seeded=outcome_rows_seeded,
        history_markets_sampled=history_markets_sampled,
        history_points_fetched=history_points_fetched,
        history_points_inserted=history_points_inserted,
        history_window_start=window_start,
        history_window_end=window_end,
        history_sources=history_sources,
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
                    1 + result.markets_seeded + result.outcomes_seeded + result.history_points_inserted
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
        description="Run Polymarket live seed pack for v0.3.6 extra probes (3.7-3.9)."
    )
    parser.add_argument(
        "--step",
        action="append",
        default=[],
        help="Optional step_code filter (can be repeated). Default runs all extras.",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    selected = list(DEFAULT_EXTRA_EVENT_PROBES)
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
                    f"outcomes_seeded={result.outcomes_seeded}",
                    f"history_fetched={result.history_points_fetched}",
                    f"history_inserted={result.history_points_inserted}",
                    f"unique_price_cents={result.unique_price_cents}",
                ]
            )
        )
        if result.error_text:
            print(f"error={result.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

