from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg2.extensions import connection as PsycopgConnection
from psycopg2.extras import Json


def _json_or_none(value: dict[str, Any] | list[Any] | None) -> Json | None:
    if value is None:
        return None
    return Json(value)


def _require_non_empty(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


class JanusUpsertRepository:
    """Upsert and append-only insert primitives for active v0.3.* tables."""

    def __init__(self, connection: PsycopgConnection):
        self.connection = connection

    def upsert_provider(
        self,
        *,
        provider_id: str,
        code: str,
        name: str,
        category: str,
        base_url: str | None = None,
        auth_type: str | None = None,
        is_active: bool = True,
    ) -> str:
        _require_non_empty("provider_id", provider_id)
        _require_non_empty("code", code)
        _require_non_empty("name", name)
        _require_non_empty("category", category)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO core.providers (
                    provider_id, code, name, category, base_url, auth_type, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    base_url = EXCLUDED.base_url,
                    auth_type = EXCLUDED.auth_type,
                    is_active = EXCLUDED.is_active,
                    updated_at = now()
                RETURNING provider_id;
                """,
                (provider_id, code, name, category, base_url, auth_type, is_active),
            )
            return str(cursor.fetchone()[0])

    def upsert_module(
        self,
        *,
        module_id: str,
        code: str,
        name: str,
        description: str | None = None,
        owner: str | None = None,
        is_active: bool = True,
    ) -> str:
        _require_non_empty("module_id", module_id)
        _require_non_empty("code", code)
        _require_non_empty("name", name)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO core.modules (
                    module_id, code, name, description, owner, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    owner = EXCLUDED.owner,
                    is_active = EXCLUDED.is_active,
                    updated_at = now()
                RETURNING module_id;
                """,
                (module_id, code, name, description, owner, is_active),
            )
            return str(cursor.fetchone()[0])

    def upsert_event_type(
        self,
        *,
        event_type_id: str,
        code: str,
        name: str,
        domain: str,
        description: str | None = None,
        default_horizon: str | None = None,
        resolution_policy: str | None = None,
    ) -> str:
        _require_non_empty("event_type_id", event_type_id)
        _require_non_empty("code", code)
        _require_non_empty("name", name)
        _require_non_empty("domain", domain)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.event_types (
                    event_type_id, code, name, domain, description, default_horizon, resolution_policy
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    domain = EXCLUDED.domain,
                    description = EXCLUDED.description,
                    default_horizon = EXCLUDED.default_horizon,
                    resolution_policy = EXCLUDED.resolution_policy
                RETURNING event_type_id;
                """,
                (
                    event_type_id,
                    code,
                    name,
                    domain,
                    description,
                    default_horizon,
                    resolution_policy,
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_information_profile(
        self,
        *,
        information_profile_id: str,
        code: str,
        name: str,
        description: str | None = None,
        min_sources: int = 1,
        required_fields_json: dict[str, Any] | list[Any] | None = None,
        refresh_interval_sec: int | None = None,
    ) -> str:
        _require_non_empty("information_profile_id", information_profile_id)
        _require_non_empty("code", code)
        _require_non_empty("name", name)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.information_profiles (
                    information_profile_id, code, name, description, min_sources,
                    required_fields_json, refresh_interval_sec
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    min_sources = EXCLUDED.min_sources,
                    required_fields_json = EXCLUDED.required_fields_json,
                    refresh_interval_sec = EXCLUDED.refresh_interval_sec
                RETURNING information_profile_id;
                """,
                (
                    information_profile_id,
                    code,
                    name,
                    description,
                    min_sources,
                    _json_or_none(required_fields_json),
                    refresh_interval_sec,
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_event(
        self,
        *,
        event_id: str,
        event_type_id: str,
        information_profile_id: str | None,
        title: str,
        status: str,
        canonical_slug: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        resolution_time: datetime | None = None,
        metadata_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("event_id", event_id)
        _require_non_empty("event_type_id", event_type_id)
        _require_non_empty("title", title)
        _require_non_empty("status", status)

        if canonical_slug:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO catalog.events (
                        event_id, event_type_id, information_profile_id, title, canonical_slug, status,
                        start_time, end_time, resolution_time, metadata_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (canonical_slug) WHERE canonical_slug IS NOT NULL
                    DO UPDATE SET
                        event_type_id = EXCLUDED.event_type_id,
                        information_profile_id = EXCLUDED.information_profile_id,
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        resolution_time = EXCLUDED.resolution_time,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now()
                    RETURNING event_id;
                    """,
                    (
                        event_id,
                        event_type_id,
                        information_profile_id,
                        title,
                        canonical_slug,
                        status,
                        start_time,
                        end_time,
                        resolution_time,
                        _json_or_none(metadata_json),
                    ),
                )
                return str(cursor.fetchone()[0])

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.events (
                    event_id, event_type_id, information_profile_id, title, canonical_slug, status,
                    start_time, end_time, resolution_time, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id)
                DO UPDATE SET
                    event_type_id = EXCLUDED.event_type_id,
                    information_profile_id = EXCLUDED.information_profile_id,
                    title = EXCLUDED.title,
                    canonical_slug = EXCLUDED.canonical_slug,
                    status = EXCLUDED.status,
                    start_time = EXCLUDED.start_time,
                    end_time = EXCLUDED.end_time,
                    resolution_time = EXCLUDED.resolution_time,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING event_id;
                """,
                (
                    event_id,
                    event_type_id,
                    information_profile_id,
                    title,
                    canonical_slug,
                    status,
                    start_time,
                    end_time,
                    resolution_time,
                    _json_or_none(metadata_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_event_external_ref(
        self,
        *,
        event_ref_id: str,
        event_id: str,
        provider_id: str,
        external_id: str,
        external_slug: str | None = None,
        external_url: str | None = None,
        is_primary: bool = False,
        raw_summary_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("event_ref_id", event_ref_id)
        _require_non_empty("event_id", event_id)
        _require_non_empty("provider_id", provider_id)
        _require_non_empty("external_id", external_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.event_external_refs (
                    event_ref_id, event_id, provider_id, external_id, external_slug,
                    external_url, is_primary, raw_summary_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider_id, external_id)
                DO UPDATE SET
                    event_id = EXCLUDED.event_id,
                    external_slug = EXCLUDED.external_slug,
                    external_url = EXCLUDED.external_url,
                    is_primary = EXCLUDED.is_primary,
                    raw_summary_json = EXCLUDED.raw_summary_json
                RETURNING event_ref_id;
                """,
                (
                    event_ref_id,
                    event_id,
                    provider_id,
                    external_id,
                    external_slug,
                    external_url,
                    is_primary,
                    _json_or_none(raw_summary_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_market(
        self,
        *,
        market_id: str,
        event_id: str,
        question: str,
        market_type: str | None = None,
        condition_id: str | None = None,
        market_slug: str | None = None,
        open_time: datetime | None = None,
        close_time: datetime | None = None,
        settled_time: datetime | None = None,
        settlement_status: str | None = None,
        metadata_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("market_id", market_id)
        _require_non_empty("event_id", event_id)
        _require_non_empty("question", question)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.markets (
                    market_id, event_id, question, market_type, condition_id, market_slug,
                    open_time, close_time, settled_time, settlement_status, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (market_id)
                DO UPDATE SET
                    event_id = EXCLUDED.event_id,
                    question = EXCLUDED.question,
                    market_type = EXCLUDED.market_type,
                    condition_id = EXCLUDED.condition_id,
                    market_slug = EXCLUDED.market_slug,
                    open_time = EXCLUDED.open_time,
                    close_time = EXCLUDED.close_time,
                    settled_time = EXCLUDED.settled_time,
                    settlement_status = EXCLUDED.settlement_status,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING market_id;
                """,
                (
                    market_id,
                    event_id,
                    question,
                    market_type,
                    condition_id,
                    market_slug,
                    open_time,
                    close_time,
                    settled_time,
                    settlement_status,
                    _json_or_none(metadata_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_market_external_ref(
        self,
        *,
        market_ref_id: str,
        market_id: str,
        provider_id: str,
        external_market_id: str,
        external_condition_id: str | None = None,
        external_slug: str | None = None,
        raw_summary_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("market_ref_id", market_ref_id)
        _require_non_empty("market_id", market_id)
        _require_non_empty("provider_id", provider_id)
        _require_non_empty("external_market_id", external_market_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.market_external_refs (
                    market_ref_id, market_id, provider_id, external_market_id,
                    external_condition_id, external_slug, raw_summary_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider_id, external_market_id)
                DO UPDATE SET
                    market_id = EXCLUDED.market_id,
                    external_condition_id = EXCLUDED.external_condition_id,
                    external_slug = EXCLUDED.external_slug,
                    raw_summary_json = EXCLUDED.raw_summary_json
                RETURNING market_ref_id;
                """,
                (
                    market_ref_id,
                    market_id,
                    provider_id,
                    external_market_id,
                    external_condition_id,
                    external_slug,
                    _json_or_none(raw_summary_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_outcome(
        self,
        *,
        outcome_id: str,
        market_id: str,
        outcome_index: int,
        outcome_label: str,
        token_id: str | None = None,
        is_winner: bool | None = None,
        metadata_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("outcome_id", outcome_id)
        _require_non_empty("market_id", market_id)
        _require_non_empty("outcome_label", outcome_label)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog.outcomes (
                    outcome_id, market_id, outcome_index, outcome_label, token_id, is_winner, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (market_id, outcome_index)
                DO UPDATE SET
                    outcome_label = EXCLUDED.outcome_label,
                    token_id = EXCLUDED.token_id,
                    is_winner = EXCLUDED.is_winner,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING outcome_id;
                """,
                (
                    outcome_id,
                    market_id,
                    outcome_index,
                    outcome_label,
                    token_id,
                    is_winner,
                    _json_or_none(metadata_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_trading_account(
        self,
        *,
        account_id: str,
        provider_id: str,
        account_label: str,
        wallet_address: str | None = None,
        proxy_wallet_address: str | None = None,
        chain_id: int | None = None,
        is_active: bool = True,
    ) -> str:
        _require_non_empty("account_id", account_id)
        _require_non_empty("provider_id", provider_id)
        _require_non_empty("account_label", account_label)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO portfolio.trading_accounts (
                    account_id, provider_id, account_label, wallet_address,
                    proxy_wallet_address, chain_id, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_id)
                DO UPDATE SET
                    provider_id = EXCLUDED.provider_id,
                    account_label = EXCLUDED.account_label,
                    wallet_address = EXCLUDED.wallet_address,
                    proxy_wallet_address = EXCLUDED.proxy_wallet_address,
                    chain_id = EXCLUDED.chain_id,
                    is_active = EXCLUDED.is_active,
                    updated_at = now()
                RETURNING account_id;
                """,
                (
                    account_id,
                    provider_id,
                    account_label,
                    wallet_address,
                    proxy_wallet_address,
                    chain_id,
                    is_active,
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_order(
        self,
        *,
        order_id: str,
        account_id: str,
        market_id: str,
        outcome_id: str | None,
        side: str,
        order_type: str,
        status: str,
        placed_at: datetime,
        updated_at: datetime,
        external_order_id: str | None = None,
        client_order_id: str | None = None,
        time_in_force: str | None = None,
        limit_price: float | None = None,
        size: float | None = None,
        metadata_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("order_id", order_id)
        _require_non_empty("account_id", account_id)
        _require_non_empty("market_id", market_id)
        _require_non_empty("side", side)
        _require_non_empty("order_type", order_type)
        _require_non_empty("status", status)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO portfolio.orders (
                    order_id, account_id, market_id, outcome_id, external_order_id, client_order_id,
                    side, order_type, time_in_force, limit_price, size, status, placed_at, updated_at, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (order_id)
                DO UPDATE SET
                    account_id = EXCLUDED.account_id,
                    market_id = EXCLUDED.market_id,
                    outcome_id = EXCLUDED.outcome_id,
                    external_order_id = EXCLUDED.external_order_id,
                    client_order_id = EXCLUDED.client_order_id,
                    side = EXCLUDED.side,
                    order_type = EXCLUDED.order_type,
                    time_in_force = EXCLUDED.time_in_force,
                    limit_price = EXCLUDED.limit_price,
                    size = EXCLUDED.size,
                    status = EXCLUDED.status,
                    placed_at = EXCLUDED.placed_at,
                    updated_at = EXCLUDED.updated_at,
                    metadata_json = EXCLUDED.metadata_json
                RETURNING order_id;
                """,
                (
                    order_id,
                    account_id,
                    market_id,
                    outcome_id,
                    external_order_id,
                    client_order_id,
                    side,
                    order_type,
                    time_in_force,
                    limit_price,
                    size,
                    status,
                    placed_at,
                    updated_at,
                    _json_or_none(metadata_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_trade(
        self,
        *,
        trade_id: str,
        account_id: str,
        market_id: str,
        side: str,
        trade_time: datetime,
        order_id: str | None = None,
        outcome_id: str | None = None,
        external_trade_id: str | None = None,
        tx_hash: str | None = None,
        price: float | None = None,
        size: float | None = None,
        fee: float | None = None,
        fee_asset: str | None = None,
        liquidity_role: str | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
    ) -> str:
        _require_non_empty("trade_id", trade_id)
        _require_non_empty("account_id", account_id)
        _require_non_empty("market_id", market_id)
        _require_non_empty("side", side)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO portfolio.trades (
                    trade_id, account_id, order_id, market_id, outcome_id, external_trade_id, tx_hash,
                    side, price, size, fee, fee_asset, liquidity_role, trade_time, raw_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (trade_id)
                DO UPDATE SET
                    account_id = EXCLUDED.account_id,
                    order_id = EXCLUDED.order_id,
                    market_id = EXCLUDED.market_id,
                    outcome_id = EXCLUDED.outcome_id,
                    external_trade_id = EXCLUDED.external_trade_id,
                    tx_hash = EXCLUDED.tx_hash,
                    side = EXCLUDED.side,
                    price = EXCLUDED.price,
                    size = EXCLUDED.size,
                    fee = EXCLUDED.fee,
                    fee_asset = EXCLUDED.fee_asset,
                    liquidity_role = EXCLUDED.liquidity_role,
                    trade_time = EXCLUDED.trade_time,
                    raw_json = EXCLUDED.raw_json
                RETURNING trade_id;
                """,
                (
                    trade_id,
                    account_id,
                    order_id,
                    market_id,
                    outcome_id,
                    external_trade_id,
                    tx_hash,
                    side,
                    price,
                    size,
                    fee,
                    fee_asset,
                    liquidity_role,
                    trade_time,
                    _json_or_none(raw_json),
                ),
            )
            return str(cursor.fetchone()[0])

    def insert_outcome_price_tick(
        self,
        *,
        outcome_id: str,
        ts: datetime,
        source: str,
        price: float | None = None,
        bid: float | None = None,
        ask: float | None = None,
        volume: float | None = None,
        liquidity: float | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("outcome_id", outcome_id)
        _require_non_empty("source", source)

        conflict_clause = "ON CONFLICT (outcome_id, ts, source) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO market_data.outcome_price_ticks (
                    outcome_id, ts, source, price, bid, ask, volume, liquidity, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING outcome_id;
                """,
                (
                    outcome_id,
                    ts,
                    source,
                    price,
                    bid,
                    ask,
                    volume,
                    liquidity,
                    _json_or_none(raw_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_orderbook_snapshot(
        self,
        *,
        orderbook_snapshot_id: str,
        outcome_id: str,
        captured_at: datetime,
        best_bid: float | None = None,
        best_ask: float | None = None,
        spread: float | None = None,
        mid_price: float | None = None,
        bid_depth: float | None = None,
        ask_depth: float | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("orderbook_snapshot_id", orderbook_snapshot_id)
        _require_non_empty("outcome_id", outcome_id)

        conflict_clause = "ON CONFLICT (orderbook_snapshot_id) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO market_data.orderbook_snapshots (
                    orderbook_snapshot_id, outcome_id, captured_at, best_bid, best_ask, spread,
                    mid_price, bid_depth, ask_depth, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING orderbook_snapshot_id;
                """,
                (
                    orderbook_snapshot_id,
                    outcome_id,
                    captured_at,
                    best_bid,
                    best_ask,
                    spread,
                    mid_price,
                    bid_depth,
                    ask_depth,
                    _json_or_none(raw_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_orderbook_level(
        self,
        *,
        orderbook_snapshot_id: str,
        side: str,
        level_no: int,
        price: float | None = None,
        size: float | None = None,
        order_count: int | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("orderbook_snapshot_id", orderbook_snapshot_id)
        if side not in {"bid", "ask"}:
            raise ValueError("side must be 'bid' or 'ask'")

        conflict_clause = "ON CONFLICT (orderbook_snapshot_id, side, level_no) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO market_data.orderbook_levels (
                    orderbook_snapshot_id, side, level_no, price, size, order_count
                ) VALUES (%s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING orderbook_snapshot_id;
                """,
                (orderbook_snapshot_id, side, level_no, price, size, order_count),
            )
            row = cursor.fetchone()
            return row is not None

