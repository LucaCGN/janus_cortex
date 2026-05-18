from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from psycopg2.extras import Json

from app.data.databases.postgres import managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    _fetch_gamma_event_by_slug,
    _parse_dt as _parse_gamma_dt,
    _parse_json_list as _parse_gamma_json_list,
    _parse_price as _parse_gamma_price,
    _uuid_for as _seed_pack_uuid_for,
)
from app.data.nodes.polymarket.blockchain.manage_portfolio import (
    PolymarketCredentials,
    _extract_base_address,
)
from app.data.nodes.polymarket.gamma.gamma_client import PolymarketDataClient


_NAMESPACE = uuid.UUID("44ecb08d-f092-4a67-b542-c944bcf1c352")
POLYGON_RPC_URLS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-rpc.com",
)
POLYGON_USDC_TOKENS = {
    "usdc_e": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "usdc": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
}


@dataclass
class PortfolioMirrorSummary:
    sync_run_id: str | None
    status: str
    wallet_address: str
    rows_read: int
    rows_written: int
    positions_written: int
    orders_written: int
    order_events_written: int
    trades_written: int
    unresolved_positions: int
    unresolved_orders: int
    unresolved_trades: int
    unresolved_blockers: dict[str, dict[str, int]] = field(default_factory=dict)
    unresolved_samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    catalog_backfill: dict[str, Any] = field(default_factory=dict)
    error_text: str | None = None


@dataclass
class _ResolutionMaps:
    token_to_pair: dict[str, tuple[str, str]]
    condition_to_market: dict[str, str]
    external_market_to_market: dict[str, str]
    market_to_first_outcome: dict[str, str]
    market_label_to_outcome: dict[tuple[str, str], str] = field(default_factory=dict)


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get("data")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_dt(value: Any, *, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return default or datetime.now(timezone.utc)


def _safe_sum(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        for key in keys:
            value = _safe_float(row.get(key))
            if value is not None:
                total += value
                found = True
                break
    return total if found else None


def _fetch_erc20_balance(
    *,
    rpc_url: str,
    token_address: str,
    wallet_address: str,
    timeout_seconds: float = 20.0,
) -> float:
    clean_wallet = str(wallet_address or "").strip().lower()
    if not clean_wallet.startswith("0x") or len(clean_wallet) < 42:
        return 0.0
    data = "0x70a08231" + clean_wallet.replace("0x", "")[:40].rjust(64, "0")
    response = requests.post(
        rpc_url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": token_address, "data": data}, "latest"],
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result")
    if not result:
        raise RuntimeError(str(payload.get("error") or "missing eth_call result"))
    return int(str(result), 16) / 1_000_000


def _fetch_polygon_cash_balances(wallet_address: str) -> dict[str, Any]:
    errors: list[str] = []
    for rpc_url in POLYGON_RPC_URLS:
        try:
            balances = {
                token_name: _fetch_erc20_balance(
                    rpc_url=rpc_url,
                    token_address=token_address,
                    wallet_address=wallet_address,
                )
                for token_name, token_address in POLYGON_USDC_TOKENS.items()
            }
            return {
                "status": "success",
                "rpc_url": rpc_url,
                "wallet_address": wallet_address,
                "balances": balances,
                "total_usd": sum(float(value or 0.0) for value in balances.values()),
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rpc_url}: {exc!r}")
    return {
        "status": "error",
        "wallet_address": wallet_address,
        "balances": {},
        "total_usd": None,
        "errors": errors,
    }


def _insert_valuation_snapshot(
    connection: Any,
    *,
    account_id: str,
    captured_at: datetime,
    cash_usd: float | None,
    positions_value_usd: float | None,
    realized_pnl_usd: float | None,
    unrealized_pnl_usd: float | None,
    raw_json: dict[str, Any],
) -> None:
    equity_usd = None
    if cash_usd is not None or positions_value_usd is not None:
        equity_usd = float(cash_usd or 0.0) + float(positions_value_usd or 0.0)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO portfolio.valuation_snapshots (
                account_id, captured_at, equity_usd, cash_usd, positions_value_usd,
                realized_pnl_usd, unrealized_pnl_usd, raw_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, captured_at) DO NOTHING;
            """,
            (
                account_id,
                captured_at,
                equity_usd,
                cash_usd,
                positions_value_usd,
                realized_pnl_usd,
                unrealized_pnl_usd,
                Json(raw_json),
            ),
        )


def _first_present(raw: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = raw.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return None


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_trade_identity_number(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        normalized = Decimal(text).normalize()
    except (InvalidOperation, ValueError):
        return text
    number_text = format(normalized, "f")
    if "." in number_text:
        number_text = number_text.rstrip("0").rstrip(".")
    return number_text or "0"


def _portfolio_trade_id(
    *,
    account_id: str,
    market_id: str,
    outcome_id: str | None,
    external_trade_id: str | None,
    tx_hash: str | None,
    side: str,
    price: Any,
    size: Any,
    trade_time: datetime,
) -> str:
    if external_trade_id:
        return _uuid_for("trade", external_trade_id)
    trade_time_utc = _safe_dt(trade_time).isoformat(timespec="microseconds")
    return _uuid_for(
        "trade_fallback",
        account_id,
        tx_hash or "",
        market_id,
        outcome_id or "",
        str(side or "").strip().lower(),
        _normalize_trade_identity_number(price),
        _normalize_trade_identity_number(size),
        trade_time_utc,
    )


def _insert_sync_run(connection: Any, *, provider_id: str, module_id: str, wallet_address: str) -> str:
    sync_run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.sync_runs (
                sync_run_id, provider_id, module_id, pipeline_name, run_type, status, started_at, meta_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                sync_run_id,
                provider_id,
                module_id,
                "daily.polymarket.sync_portfolio",
                "scheduled",
                "running",
                now,
                Json({"wallet_address": wallet_address}),
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
    endpoint: str,
    external_id: str,
    payload: Any,
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
                endpoint,
                external_id,
                datetime.now(timezone.utc),
                Json(payload),
            ),
        )


def _load_resolution_maps(connection: Any) -> _ResolutionMaps:
    token_to_pair: dict[str, tuple[str, str]] = {}
    condition_to_market: dict[str, str] = {}
    external_market_to_market: dict[str, str] = {}
    market_to_first_outcome: dict[str, str] = {}
    market_label_to_outcome: dict[tuple[str, str], str] = {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT token_id, outcome_id, market_id
            FROM catalog.outcomes
            WHERE token_id IS NOT NULL AND token_id <> '';
            """
        )
        for token_id, outcome_id, market_id in cursor.fetchall():
            token_to_pair[str(token_id)] = (str(market_id), str(outcome_id))

        cursor.execute(
            """
            SELECT external_condition_id, market_id
            FROM catalog.market_external_refs
            WHERE external_condition_id IS NOT NULL AND external_condition_id <> '';
            """
        )
        for condition_id, market_id in cursor.fetchall():
            condition_to_market[str(condition_id)] = str(market_id)

        cursor.execute(
            """
            SELECT external_market_id, market_id
            FROM catalog.market_external_refs
            WHERE external_market_id IS NOT NULL AND external_market_id <> '';
            """
        )
        for external_market_id, market_id in cursor.fetchall():
            external_market_to_market[str(external_market_id)] = str(market_id)

        cursor.execute(
            """
            SELECT market_id, outcome_id, outcome_label
            FROM catalog.outcomes
            ORDER BY market_id, outcome_index;
            """
        )
        for market_id, outcome_id, outcome_label in cursor.fetchall():
            market_key = str(market_id)
            if market_key not in market_to_first_outcome:
                market_to_first_outcome[market_key] = str(outcome_id)
            label_key = _normalize_label(outcome_label)
            if label_key:
                market_label_to_outcome[(market_key, label_key)] = str(outcome_id)

    return _ResolutionMaps(
        token_to_pair=token_to_pair,
        condition_to_market=condition_to_market,
        external_market_to_market=external_market_to_market,
        market_to_first_outcome=market_to_first_outcome,
        market_label_to_outcome=market_label_to_outcome,
    )


def _resolve_outcome_for_market(raw: dict[str, Any], market_id: str, maps: _ResolutionMaps) -> str | None:
    outcome_label = _first_present(raw, ["outcome", "outcomeLabel", "outcome_label", "name"])
    if outcome_label:
        matched = maps.market_label_to_outcome.get((market_id, _normalize_label(outcome_label)))
        if matched:
            return matched
        return None
    return maps.market_to_first_outcome.get(market_id)


def _resolve_market_outcome(raw: dict[str, Any], maps: _ResolutionMaps) -> tuple[str | None, str | None]:
    token = _first_present(
        raw,
        ["asset", "asset_id", "token_id", "tokenId", "outcomeTokenId", "clobTokenId"],
    )
    if token and token in maps.token_to_pair:
        market_id, outcome_id = maps.token_to_pair[token]
        return market_id, outcome_id

    condition_id = _first_present(raw, ["conditionId", "condition_id", "condition"])
    if condition_id and condition_id in maps.condition_to_market:
        market_id = maps.condition_to_market[condition_id]
        return market_id, _resolve_outcome_for_market(raw, market_id, maps)

    external_market_id = _first_present(raw, ["market", "marketId", "market_id"])
    if external_market_id and external_market_id in maps.external_market_to_market:
        market_id = maps.external_market_to_market[external_market_id]
        return market_id, _resolve_outcome_for_market(raw, market_id, maps)

    return None, None


def _resolution_blocker_category(
    raw: dict[str, Any],
    maps: _ResolutionMaps,
    *,
    require_outcome: bool,
) -> str:
    token = _first_present(
        raw,
        ["asset", "asset_id", "token_id", "tokenId", "outcomeTokenId", "clobTokenId"],
    )
    if token:
        pair = maps.token_to_pair.get(token)
        if pair is not None:
            _market_id, outcome_id = pair
            if require_outcome and outcome_id is None:
                return "token_mapping_missing_outcome"
            return "unknown_resolution_blocker"
        condition_id = _first_present(raw, ["conditionId", "condition_id", "condition"])
        if condition_id and condition_id in maps.condition_to_market:
            market_id = maps.condition_to_market[condition_id]
            if not require_outcome:
                return "unknown_resolution_blocker"
            if maps.market_to_first_outcome.get(market_id) is None:
                return "condition_market_missing_outcome"
            if _resolve_outcome_for_market(raw, market_id, maps) is None:
                return "condition_market_outcome_label_missing"
            return "unknown_resolution_blocker"
        external_market_id = _first_present(raw, ["market", "marketId", "market_id"])
        if external_market_id and external_market_id in maps.external_market_to_market:
            market_id = maps.external_market_to_market[external_market_id]
            if not require_outcome:
                return "unknown_resolution_blocker"
            if maps.market_to_first_outcome.get(market_id) is None:
                return "external_market_missing_outcome"
            if _resolve_outcome_for_market(raw, market_id, maps) is None:
                return "external_market_outcome_label_missing"
            return "unknown_resolution_blocker"
        return "missing_token_catalog_mapping"

    condition_id = _first_present(raw, ["conditionId", "condition_id", "condition"])
    if condition_id:
        market_id = maps.condition_to_market.get(condition_id)
        if market_id is None:
            return "missing_condition_catalog_mapping"
        if require_outcome and maps.market_to_first_outcome.get(market_id) is None:
            return "condition_market_missing_outcome"
        if require_outcome and _resolve_outcome_for_market(raw, market_id, maps) is None:
            return "condition_market_outcome_label_missing"
        return "unknown_resolution_blocker"

    external_market_id = _first_present(raw, ["market", "marketId", "market_id"])
    if external_market_id:
        market_id = maps.external_market_to_market.get(external_market_id)
        if market_id is None:
            return "missing_external_market_catalog_mapping"
        if require_outcome and maps.market_to_first_outcome.get(market_id) is None:
            return "external_market_missing_outcome"
        if require_outcome and _resolve_outcome_for_market(raw, market_id, maps) is None:
            return "external_market_outcome_label_missing"
        return "unknown_resolution_blocker"

    return "missing_resolvable_identifier"


def _unresolved_sample(raw: dict[str, Any], *, category: str) -> dict[str, Any]:
    sample_keys = (
        "id",
        "orderID",
        "tradeID",
        "asset",
        "asset_id",
        "token_id",
        "conditionId",
        "condition_id",
        "market",
        "marketId",
        "market_id",
        "slug",
        "eventSlug",
        "title",
        "outcome",
        "side",
        "timestamp",
        "createdAt",
    )
    sample: dict[str, Any] = {"category": category}
    for key in sample_keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            sample[key] = value
    return sample


def _record_resolution_blocker(
    *,
    scope: str,
    raw: dict[str, Any],
    maps: _ResolutionMaps,
    blockers: dict[str, dict[str, int]],
    samples: dict[str, list[dict[str, Any]]],
    require_outcome: bool,
    sample_limit: int = 5,
) -> None:
    category = _resolution_blocker_category(raw, maps, require_outcome=require_outcome)
    scope_counts = blockers.setdefault(scope, {})
    scope_counts[category] = int(scope_counts.get(category, 0)) + 1
    scope_samples = samples.setdefault(scope, [])
    if len(scope_samples) < sample_limit:
        scope_samples.append(_unresolved_sample(raw, category=category))


def _portfolio_slug_candidates(raw: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("eventSlug", "event_slug", "slug", "marketSlug", "market_slug"):
        value = raw.get(key)
        text = str(value or "").strip().strip("/")
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def _event_type_for_account_slug(slug: str) -> tuple[str, str, str]:
    normalized = slug.lower()
    if "nba" in normalized:
        return "sports_nba_account_event", "NBA Account Portfolio Event", "sports"
    return "portfolio_account_event", "Account Portfolio Event", "prediction_market"


def _source_url_for_account_slug(slug: str) -> str:
    if "nba" in slug.lower():
        return f"https://polymarket.com/sports/nba/{slug}"
    return f"https://polymarket.com/event/{slug}"


def _load_existing_event_ids_by_slug(connection: Any, slugs: list[str]) -> dict[str, str]:
    if not slugs:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT canonical_slug, event_id
            FROM catalog.events
            WHERE canonical_slug = ANY(%s);
            """,
            (slugs,),
        )
        return {str(row[0]): str(row[1]) for row in cursor.fetchall()}


def _collect_catalog_backfill_slug_groups(
    payload: dict[str, list[dict[str, Any]]],
    maps: _ResolutionMaps,
    *,
    limit: int,
) -> list[list[str]]:
    if limit <= 0:
        return []
    selected: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for scope in ("open_positions", "closed_positions", "orders", "trades"):
        for raw in payload.get(scope, []):
            market_id, outcome_id = _resolve_market_outcome(raw, maps)
            if market_id is not None and (scope in {"orders", "trades"} or outcome_id is not None):
                continue
            candidates = _portfolio_slug_candidates(raw)
            if not candidates:
                continue
            group_key = tuple(candidates)
            if group_key in seen:
                continue
            seen.add(group_key)
            selected.append(candidates)
            if len(selected) >= limit:
                return selected
    return selected


def _collect_catalog_backfill_slugs(
    payload: dict[str, list[dict[str, Any]]],
    maps: _ResolutionMaps,
    *,
    limit: int,
) -> list[str]:
    return [group[0] for group in _collect_catalog_backfill_slug_groups(payload, maps, limit=limit)]


def _ensure_account_backfill_baselines(repo: JanusUpsertRepository, slugs: list[str]) -> dict[str, str]:
    provider_id = repo.upsert_provider(
        provider_id=_seed_pack_uuid_for("provider", "gamma"),
        code="gamma",
        name="Polymarket Gamma",
        category="prediction_market",
        base_url="https://gamma-api.polymarket.com",
        auth_type="none",
    )
    module_id = repo.upsert_module(
        module_id=_uuid_for("module", "polymarket_account_catalog_backfill"),
        code="polymarket_account_catalog_backfill",
        name="Polymarket Account Catalog Backfill",
        description="Catalog-only Gamma backfill for account portfolio rows",
        owner="janus",
    )
    profile_id = repo.upsert_information_profile(
        information_profile_id=_uuid_for("information_profile", "account_portfolio_catalog_backfill"),
        code="account_portfolio_catalog_backfill",
        name="Account Portfolio Catalog Backfill",
        description="Minimal catalog coverage for direct account portfolio mapping",
        min_sources=1,
        required_fields_json=["title", "markets", "outcomes"],
        refresh_interval_sec=None,
    )
    baselines = {"provider_id": provider_id, "module_id": module_id, "profile_id": profile_id}
    event_types: dict[str, tuple[str, str]] = {}
    for slug in slugs:
        code, name, domain = _event_type_for_account_slug(slug)
        event_types[code] = (name, domain)
    for code, (name, domain) in sorted(event_types.items()):
        baselines[f"event_type::{code}"] = repo.upsert_event_type(
            event_type_id=_uuid_for("event_type", code),
            code=code,
            name=name,
            domain=domain,
            description="Catalog-only portfolio account backfill event type.",
        )
    return baselines


def _seed_account_gamma_catalog_slug(
    *,
    repo: JanusUpsertRepository,
    slug: str,
    baselines: dict[str, str],
    existing_event_id: str | None = None,
) -> dict[str, Any]:
    payload = _fetch_gamma_event_by_slug(slug)
    gamma_event_id = str(payload.get("id") or slug)
    event_uuid = _seed_pack_uuid_for("event", "gamma", gamma_event_id)
    code, _name, _domain = _event_type_for_account_slug(slug)
    provider_id = baselines["provider_id"]

    event_start = _parse_gamma_dt(payload.get("startDate") or payload.get("startTime"))
    event_end = _parse_gamma_dt(payload.get("endDate") or payload.get("endTime"))
    event_status = "closed" if bool(payload.get("closed")) else "open"
    event_title = str(payload.get("title") or slug)
    source_url = _source_url_for_account_slug(slug)

    event_id = existing_event_id
    if event_id is None:
        event_id = repo.upsert_event(
            event_id=event_uuid,
            event_type_id=baselines[f"event_type::{code}"],
            information_profile_id=baselines["profile_id"],
            title=event_title,
            status=event_status,
            canonical_slug=slug,
            start_time=event_start,
            end_time=event_end,
            metadata_json={
                "category": payload.get("category"),
                "subcategory": payload.get("subcategory"),
                "source": "portfolio_account_catalog_backfill",
                "source_url": source_url,
            },
        )
    repo.upsert_event_external_ref(
        event_ref_id=_seed_pack_uuid_for("event_ref", "gamma", gamma_event_id),
        event_id=event_id,
        provider_id=provider_id,
        external_id=gamma_event_id,
        external_slug=slug,
        external_url=source_url,
        is_primary=True,
        raw_summary_json={
            "title": event_title,
            "startDate": payload.get("startDate"),
            "endDate": payload.get("endDate"),
            "closed": payload.get("closed"),
            "source": "portfolio_account_catalog_backfill",
        },
    )

    markets_raw = payload.get("markets")
    if not isinstance(markets_raw, list):
        markets_raw = []
    markets_seeded = 0
    outcomes_seeded = 0
    for market in markets_raw:
        if not isinstance(market, dict):
            continue
        external_market_id = str(market.get("id") or "").strip()
        if not external_market_id:
            continue
        market_uuid = _seed_pack_uuid_for("market", "gamma", external_market_id)
        market_slug = market.get("slug")
        market_question = str(market.get("question") or market_slug or external_market_id)
        market_type = str(market.get("sportsMarketType") or market.get("marketType") or "").strip() or None
        condition_id = str(market.get("conditionId")) if market.get("conditionId") is not None else None
        repo.upsert_market(
            market_id=market_uuid,
            event_id=event_id,
            question=market_question,
            market_type=market_type,
            condition_id=condition_id,
            market_slug=str(market_slug) if market_slug is not None else None,
            open_time=_parse_gamma_dt(market.get("startDate") or market.get("startTime")),
            close_time=_parse_gamma_dt(market.get("endDate") or market.get("endTime")),
            settled_time=None,
            settlement_status="closed" if bool(market.get("closed")) else "open",
            metadata_json={
                "source": "portfolio_account_catalog_backfill",
                "enableOrderBook": market.get("enableOrderBook"),
                "volume": market.get("volume"),
                "liquidity": market.get("liquidity"),
            },
        )
        repo.upsert_market_external_ref(
            market_ref_id=_seed_pack_uuid_for("market_ref", "gamma", external_market_id),
            market_id=market_uuid,
            provider_id=provider_id,
            external_market_id=external_market_id,
            external_condition_id=condition_id,
            external_slug=str(market_slug) if market_slug is not None else None,
            raw_summary_json={
                "question": market_question,
                "sportsMarketType": market.get("sportsMarketType"),
                "closed": market.get("closed"),
                "source": "portfolio_account_catalog_backfill",
            },
        )
        markets_seeded += 1
        outcomes = _parse_gamma_json_list(market.get("outcomes"))
        tokens = _parse_gamma_json_list(market.get("clobTokenIds") or market.get("clobTokenIDs"))
        prices = _parse_gamma_json_list(market.get("outcomePrices"))
        for idx, raw_label in enumerate(outcomes):
            label = str(raw_label).strip() or f"outcome_{idx}"
            token_id = str(tokens[idx]).strip() if idx < len(tokens) else None
            token_id = token_id or None
            implied = _parse_gamma_price(prices[idx]) if idx < len(prices) else None
            repo.upsert_outcome(
                outcome_id=_seed_pack_uuid_for("outcome", "gamma", external_market_id, str(idx)),
                market_id=market_uuid,
                outcome_index=idx,
                outcome_label=label,
                token_id=token_id,
                is_winner=None,
                metadata_json={
                    "source": "portfolio_account_catalog_backfill",
                    "source_market_type": market_type,
                    "implied_prob": implied,
                },
            )
            outcomes_seeded += 1
    return {
        "slug": slug,
        "gamma_event_id": gamma_event_id,
        "event_id": event_id,
        "event_existing": existing_event_id is not None,
        "markets_seeded": markets_seeded,
        "outcomes_seeded": outcomes_seeded,
    }


def _backfill_account_catalog(
    connection: Any,
    *,
    payload: dict[str, list[dict[str, Any]]],
    maps: _ResolutionMaps,
    limit: int,
) -> dict[str, Any]:
    candidate_groups = _collect_catalog_backfill_slug_groups(payload, maps, limit=limit)
    candidates: list[str] = []
    for group in candidate_groups:
        for slug in group:
            if slug not in candidates:
                candidates.append(slug)
    existing = _load_existing_event_ids_by_slug(connection, candidates)
    selected: list[str] = []
    seen_selected: set[str] = set()
    for group in candidate_groups:
        for slug in group:
            if slug in seen_selected:
                continue
            if slug in existing and len(group) > 1:
                continue
            selected.append(slug)
            seen_selected.add(slug)
            break
        if len(selected) >= max(limit, 0):
            break
    result: dict[str, Any] = {
        "status": "skipped" if not selected else "success",
        "candidate_slugs": candidates,
        "existing_slugs": sorted(existing),
        "attempted_slugs": selected,
        "seeded": [],
        "errors": [],
    }
    if not selected:
        return result
    repo = JanusUpsertRepository(connection)
    baselines = _ensure_account_backfill_baselines(repo, selected)
    for slug in selected:
        try:
            result["seeded"].append(
                _seed_account_gamma_catalog_slug(
                    repo=repo,
                    slug=slug,
                    baselines=baselines,
                    existing_event_id=existing.get(slug),
                )
            )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append({"slug": slug, "error": repr(exc)})
    if result["errors"] and result["seeded"]:
        result["status"] = "partial_success"
    elif result["errors"]:
        result["status"] = "error"
    return result


def _fetch_data_api_payload(
    *,
    data_client: PolymarketDataClient,
    wallet_address: str,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    orders_merged: dict[str, dict[str, Any]] = {}
    for status in ("OPEN", "FILLED", "CANCELED"):
        try:
            rows = _as_list(data_client.get_orders(user=wallet_address, limit=limit, status=status))
        except Exception:
            rows = []
        for row in rows:
            key = str(row.get("id") or row.get("orderID") or f"{status}:{len(orders_merged)}")
            orders_merged[key] = row

    try:
        open_positions = _as_list(data_client.get_positions(user=wallet_address, size_threshold=0.0, limit=limit))
    except Exception:
        open_positions = []
    try:
        closed_positions = _as_list(data_client.get_closed_positions(user=wallet_address, limit=limit))
    except Exception:
        closed_positions = []
    try:
        trades = _as_list(data_client.get_trades(user=wallet_address, limit=limit, taker_only=False))
    except Exception:
        trades = []

    return {
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "orders": list(orders_merged.values()),
        "trades": trades,
    }


def _lookup_existing_trading_account(
    connection: Any,
    *,
    provider_id: str,
    wallet_address: str,
    proxy_wallet_address: str | None,
) -> dict[str, Any] | None:
    wallet = str(wallet_address or "").strip()
    proxy_wallet = str(proxy_wallet_address or "").strip()
    if not wallet and not proxy_wallet:
        return None

    lookup_values = [value for value in (wallet, proxy_wallet) if value]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                account_id,
                account_label,
                wallet_address,
                proxy_wallet_address,
                chain_id
            FROM portfolio.trading_accounts
            WHERE provider_id = %s
              AND (
                lower(wallet_address) = ANY(%s)
                OR lower(proxy_wallet_address) = ANY(%s)
              )
            ORDER BY
                is_active DESC,
                CASE WHEN account_label ILIKE 'wallet:%%' THEN 1 ELSE 0 END,
                updated_at DESC NULLS LAST,
                created_at DESC NULLS LAST
            LIMIT 1;
            """,
            (
                provider_id,
                [value.lower() for value in lookup_values],
                [value.lower() for value in lookup_values],
            ),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "account_id": str(row[0]),
        "account_label": str(row[1]),
        "wallet_address": str(row[2] or wallet),
        "proxy_wallet_address": str(row[3] or proxy_wallet or wallet),
        "chain_id": int(row[4] or 137),
    }


def run_portfolio_mirror_sync(
    *,
    wallet_address: str,
    limit: int = 250,
    payload_override: dict[str, list[dict[str, Any]]] | None = None,
    data_client: PolymarketDataClient | None = None,
    account_id: str | None = None,
    account_label: str | None = None,
    proxy_wallet_address: str | None = None,
    chain_id: int = 137,
    account_catalog_backfill_limit: int = 25,
) -> PortfolioMirrorSummary:
    client = data_client or PolymarketDataClient()
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    positions_written = 0
    orders_written = 0
    order_events_written = 0
    trades_written = 0
    unresolved_positions = 0
    unresolved_orders = 0
    unresolved_trades = 0
    unresolved_blockers: dict[str, dict[str, int]] = {}
    unresolved_samples: dict[str, list[dict[str, Any]]] = {}
    catalog_backfill: dict[str, Any] = {}

    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=_uuid_for("provider", "polymarket_data_api"),
            code="polymarket_data_api",
            name="Polymarket Data API",
            category="prediction_market",
            base_url="https://data-api.polymarket.com",
            auth_type="none",
        )
        module_id = repo.upsert_module(
            module_id=_uuid_for("module", "polymarket_portfolio_mirror"),
            code="polymarket_portfolio_mirror",
            name="Polymarket Portfolio Mirror",
            description="Data API portfolio mirror ingestion pipeline",
            owner="janus",
        )
        existing_account = None
        if account_id is None:
            existing_account = _lookup_existing_trading_account(
                connection,
                provider_id=provider_id,
                wallet_address=wallet_address,
                proxy_wallet_address=proxy_wallet_address,
            )
        resolved_account_id = (
            account_id
            or (existing_account or {}).get("account_id")
            or _uuid_for("account", wallet_address)
        )
        resolved_account_label = (
            account_label
            or (existing_account or {}).get("account_label")
            or f"wallet:{wallet_address[:10]}"
        )
        resolved_proxy_wallet = (
            proxy_wallet_address
            or (existing_account or {}).get("proxy_wallet_address")
            or wallet_address
        )
        resolved_chain_id = int((existing_account or {}).get("chain_id") or chain_id)
        account_id = repo.upsert_trading_account(
            account_id=resolved_account_id,
            provider_id=provider_id,
            account_label=resolved_account_label,
            wallet_address=wallet_address,
            proxy_wallet_address=resolved_proxy_wallet,
            chain_id=resolved_chain_id,
            is_active=True,
        )
        sync_run_id = _insert_sync_run(
            connection,
            provider_id=provider_id,
            module_id=module_id,
            wallet_address=wallet_address,
        )
        connection.commit()

        try:
            payload = payload_override or _fetch_data_api_payload(
                data_client=client,
                wallet_address=wallet_address,
                limit=limit,
            )
            open_position_rows = payload.get("open_positions", [])
            closed_position_rows = payload.get("closed_positions", [])
            cash_balances = _fetch_polygon_cash_balances(resolved_proxy_wallet or wallet_address)
            cash_usd = _safe_float(cash_balances.get("total_usd"))
            positions_value_usd = _safe_sum(open_position_rows, ("currentValue", "current_value"))
            unrealized_pnl_usd = _safe_sum(open_position_rows, ("cashPnl", "unrealizedPnl", "unrealized_pnl"))
            realized_pnl_usd = _safe_sum(closed_position_rows, ("realizedPnl", "realized_pnl"))
            _insert_valuation_snapshot(
                connection,
                account_id=account_id,
                captured_at=now,
                cash_usd=cash_usd,
                positions_value_usd=positions_value_usd,
                realized_pnl_usd=realized_pnl_usd,
                unrealized_pnl_usd=unrealized_pnl_usd,
                raw_json={
                    "source": "portfolio_mirror_wallet_balance",
                    "cash_balances": cash_balances,
                    "open_position_count": len(open_position_rows),
                    "closed_position_count": len(closed_position_rows),
                    "order_count": len(payload.get("orders", [])),
                    "trade_count": len(payload.get("trades", [])),
                },
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/open-positions",
                external_id=wallet_address,
                payload=payload.get("open_positions", []),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/closed-positions",
                external_id=wallet_address,
                payload=payload.get("closed_positions", []),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/orders",
                external_id=wallet_address,
                payload=payload.get("orders", []),
            )
            _insert_raw_payload(
                connection,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                endpoint="/portfolio-mirror/trades",
                external_id=wallet_address,
                payload=payload.get("trades", []),
            )
            maps = _load_resolution_maps(connection)
            catalog_backfill = _backfill_account_catalog(
                connection,
                payload=payload,
                maps=maps,
                limit=account_catalog_backfill_limit,
            )
            if catalog_backfill.get("seeded"):
                maps = _load_resolution_maps(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT order_id
                    FROM portfolio.orders
                    WHERE account_id = %s;
                    """,
                    (account_id,),
                )
                known_order_ids = {str(row[0]) for row in cursor.fetchall()}

            for raw in payload.get("open_positions", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if outcome_id is None:
                    unresolved_positions += 1
                    _record_resolution_blocker(
                        scope="open_positions",
                        raw=raw,
                        maps=maps,
                        blockers=unresolved_blockers,
                        samples=unresolved_samples,
                        require_outcome=True,
                    )
                    continue
                size = _safe_float(raw.get("size"))
                current_value = _safe_float(raw.get("currentValue"))
                current_price = (
                    (current_value / size)
                    if current_value is not None and size is not None and abs(size) > 0
                    else _safe_float(raw.get("currentPrice"))
                )
                inserted = repo.insert_position_snapshot(
                    account_id=account_id,
                    outcome_id=outcome_id,
                    captured_at=now,
                    source="data_api_open_position",
                    size=size,
                    avg_price=_safe_float(raw.get("avgPrice")),
                    current_price=current_price,
                    current_value=current_value,
                    unrealized_pnl=_safe_float(raw.get("cashPnl")),
                    realized_pnl=None,
                    raw_json=raw,
                    ignore_duplicates=True,
                )
                if inserted:
                    positions_written += 1
                    rows_written += 1

            for raw in payload.get("closed_positions", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if outcome_id is None:
                    unresolved_positions += 1
                    _record_resolution_blocker(
                        scope="closed_positions",
                        raw=raw,
                        maps=maps,
                        blockers=unresolved_blockers,
                        samples=unresolved_samples,
                        require_outcome=True,
                    )
                    continue
                inserted = repo.insert_position_snapshot(
                    account_id=account_id,
                    outcome_id=outcome_id,
                    captured_at=now,
                    source="data_api_closed_position",
                    size=_safe_float(raw.get("size")),
                    avg_price=_safe_float(raw.get("avgPrice")),
                    current_price=None,
                    current_value=None,
                    unrealized_pnl=None,
                    realized_pnl=_safe_float(raw.get("realizedPnl")),
                    raw_json=raw,
                    ignore_duplicates=True,
                )
                if inserted:
                    positions_written += 1
                    rows_written += 1

            for raw in payload.get("orders", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if market_id is None:
                    unresolved_orders += 1
                    _record_resolution_blocker(
                        scope="orders",
                        raw=raw,
                        maps=maps,
                        blockers=unresolved_blockers,
                        samples=unresolved_samples,
                        require_outcome=False,
                    )
                    continue
                external_order_id = _first_present(raw, ["id", "orderID", "order_id"])
                order_id = _uuid_for("order", external_order_id or str(uuid.uuid4()))
                placed_at = _safe_dt(raw.get("createdAt") or raw.get("created_at") or raw.get("timestamp"), default=now)
                updated_at = _safe_dt(raw.get("updatedAt") or raw.get("updated_at"), default=now)
                side = str(raw.get("side") or "buy").lower()
                order_type = str(raw.get("type") or raw.get("orderType") or "limit").lower()
                status = str(raw.get("status") or "open").lower()
                repo.upsert_order(
                    order_id=order_id,
                    account_id=account_id,
                    market_id=market_id,
                    outcome_id=outcome_id,
                    side=side,
                    order_type=order_type,
                    status=status,
                    placed_at=placed_at,
                    updated_at=updated_at,
                    external_order_id=external_order_id,
                    client_order_id=_first_present(raw, ["clientOrderId", "client_order_id"]),
                    time_in_force=_first_present(raw, ["timeInForce", "time_in_force"]),
                    limit_price=_safe_float(raw.get("price")),
                    size=_safe_float(raw.get("size")),
                    metadata_json=raw,
                )
                known_order_ids.add(order_id)
                orders_written += 1
                rows_written += 1

                inserted_event = repo.insert_order_event(
                    order_event_id=_uuid_for("order_event", order_id, updated_at.isoformat(), status),
                    order_id=order_id,
                    event_time=updated_at,
                    event_type=f"mirror_status_{status}",
                    filled_size_delta=_safe_float(raw.get("filledSize") or raw.get("filled_size")),
                    filled_notional_delta=_safe_float(raw.get("filledNotional") or raw.get("filled_notional")),
                    raw_json=raw,
                    ignore_duplicates=True,
                )
                if inserted_event:
                    order_events_written += 1
                    rows_written += 1

            seen_trade_ids: set[str] = set()
            for raw in payload.get("trades", []):
                rows_read += 1
                market_id, outcome_id = _resolve_market_outcome(raw, maps)
                if market_id is None:
                    unresolved_trades += 1
                    _record_resolution_blocker(
                        scope="trades",
                        raw=raw,
                        maps=maps,
                        blockers=unresolved_blockers,
                        samples=unresolved_samples,
                        require_outcome=False,
                    )
                    continue
                external_trade_id = _first_present(raw, ["id", "tradeID", "trade_id"])
                trade_time = _safe_dt(raw.get("timestamp") or raw.get("createdAt"), default=now)
                tx_hash = _first_present(raw, ["transactionHash", "txHash", "tx_hash"])
                side = str(raw.get("side") or "buy").lower()
                price = _safe_float(raw.get("price"))
                size = _safe_float(raw.get("size"))
                trade_id = _portfolio_trade_id(
                    account_id=account_id,
                    market_id=market_id,
                    outcome_id=outcome_id,
                    external_trade_id=external_trade_id,
                    tx_hash=tx_hash,
                    side=side,
                    price=price,
                    size=size,
                    trade_time=trade_time,
                )
                if trade_id in seen_trade_ids:
                    continue
                seen_trade_ids.add(trade_id)
                order_external_id = _first_present(raw, ["orderID", "orderId", "order_id"])
                resolved_order_id = None
                if order_external_id:
                    candidate_order_id = _uuid_for("order", order_external_id)
                    if candidate_order_id in known_order_ids:
                        resolved_order_id = candidate_order_id
                repo.upsert_trade(
                    trade_id=trade_id,
                    account_id=account_id,
                    order_id=resolved_order_id,
                    market_id=market_id,
                    outcome_id=outcome_id,
                    external_trade_id=external_trade_id,
                    tx_hash=tx_hash,
                    side=side,
                    price=price,
                    size=size,
                    fee=_safe_float(raw.get("fee")),
                    fee_asset=_first_present(raw, ["feeAsset", "fee_asset"]),
                    liquidity_role=_first_present(raw, ["liquidityRole", "liquidity_role"]),
                    trade_time=trade_time,
                    raw_json=raw,
                )
                trades_written += 1
                rows_written += 1

            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="success",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=None,
            )
            connection.commit()
            return PortfolioMirrorSummary(
                sync_run_id=sync_run_id,
                status="success",
                wallet_address=wallet_address,
                rows_read=rows_read,
                rows_written=rows_written,
                positions_written=positions_written,
                orders_written=orders_written,
                order_events_written=order_events_written,
                trades_written=trades_written,
                unresolved_positions=unresolved_positions,
                unresolved_orders=unresolved_orders,
                unresolved_trades=unresolved_trades,
                unresolved_blockers=unresolved_blockers,
                unresolved_samples=unresolved_samples,
                catalog_backfill=catalog_backfill,
            )
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            _update_sync_run(
                connection,
                sync_run_id=sync_run_id,
                status="error",
                rows_read=rows_read,
                rows_written=rows_written,
                error_text=repr(exc),
            )
            connection.commit()
            return PortfolioMirrorSummary(
                sync_run_id=sync_run_id,
                status="error",
                wallet_address=wallet_address,
                rows_read=rows_read,
                rows_written=rows_written,
                positions_written=positions_written,
                orders_written=orders_written,
                order_events_written=order_events_written,
                trades_written=trades_written,
                unresolved_positions=unresolved_positions,
                unresolved_orders=unresolved_orders,
                unresolved_trades=unresolved_trades,
                unresolved_blockers=unresolved_blockers,
                unresolved_samples=unresolved_samples,
                catalog_backfill=catalog_backfill,
                error_text=repr(exc),
            )


def _resolve_wallet_address(explicit_wallet: str | None) -> str | None:
    if explicit_wallet:
        wallet = _extract_base_address(explicit_wallet) or explicit_wallet
        return wallet
    creds = PolymarketCredentials.from_env()
    if creds.wallet_address:
        return creds.wallet_address
    return None


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run polymarket portfolio mirror ingestion.")
    parser.add_argument("--wallet", default=None, help="0x wallet address (base) to mirror.")
    parser.add_argument("--limit", type=int, default=250)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    wallet_address = _resolve_wallet_address(args.wallet)
    if not wallet_address:
        print("Wallet address is required (set --wallet or POLYMARKET_PROXY_WALLET/POLYMARKET_PRIMARY_WALLET).")
        return 2

    summary = run_portfolio_mirror_sync(wallet_address=wallet_address, limit=args.limit)
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} wallet={summary.wallet_address}")
    print(
        " | ".join(
            [
                f"rows_read={summary.rows_read}",
                f"rows_written={summary.rows_written}",
                f"positions_written={summary.positions_written}",
                f"orders_written={summary.orders_written}",
                f"order_events_written={summary.order_events_written}",
                f"trades_written={summary.trades_written}",
                f"unresolved_positions={summary.unresolved_positions}",
                f"unresolved_orders={summary.unresolved_orders}",
                f"unresolved_trades={summary.unresolved_trades}",
            ]
        )
    )
    if summary.error_text:
        print(f"error={summary.error_text}")
    if summary.catalog_backfill:
        print(f"catalog_backfill={summary.catalog_backfill}")
    if summary.unresolved_blockers:
        print(f"unresolved_blockers={summary.unresolved_blockers}")
    if summary.unresolved_samples:
        print(f"unresolved_samples={summary.unresolved_samples}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
