from __future__ import annotations

from datetime import datetime, timezone
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

    def insert_position_snapshot(
        self,
        *,
        account_id: str,
        outcome_id: str,
        captured_at: datetime,
        source: str,
        size: float | None = None,
        avg_price: float | None = None,
        current_price: float | None = None,
        current_value: float | None = None,
        unrealized_pnl: float | None = None,
        realized_pnl: float | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("account_id", account_id)
        _require_non_empty("outcome_id", outcome_id)
        _require_non_empty("source", source)

        conflict_clause = (
            "ON CONFLICT (account_id, outcome_id, captured_at, source) DO NOTHING"
            if ignore_duplicates
            else ""
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO portfolio.position_snapshots (
                    account_id, outcome_id, captured_at, source, size, avg_price, current_price,
                    current_value, unrealized_pnl, realized_pnl, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING account_id;
                """,
                (
                    account_id,
                    outcome_id,
                    captured_at,
                    source,
                    size,
                    avg_price,
                    current_price,
                    current_value,
                    unrealized_pnl,
                    realized_pnl,
                    _json_or_none(raw_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

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

    def insert_market_state_snapshot(
        self,
        *,
        market_state_snapshot_id: str,
        market_id: str,
        sync_run_id: str | None,
        captured_at: datetime,
        last_price: float | None = None,
        volume: float | None = None,
        liquidity: float | None = None,
        best_bid: float | None = None,
        best_ask: float | None = None,
        mid_price: float | None = None,
        market_status: str | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("market_state_snapshot_id", market_state_snapshot_id)
        _require_non_empty("market_id", market_id)

        conflict_clause = "ON CONFLICT (market_id, captured_at, sync_run_id) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO catalog.market_state_snapshots (
                    market_state_snapshot_id, market_id, sync_run_id, captured_at,
                    last_price, volume, liquidity, best_bid, best_ask, mid_price, market_status, raw_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                {conflict_clause}
                RETURNING market_state_snapshot_id;
                """,
                (
                    market_state_snapshot_id,
                    market_id,
                    sync_run_id,
                    captured_at,
                    last_price,
                    volume,
                    liquidity,
                    best_bid,
                    best_ask,
                    mid_price,
                    market_status,
                    _json_or_none(raw_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_order_event(
        self,
        *,
        order_event_id: str,
        order_id: str,
        event_time: datetime,
        event_type: str,
        filled_size_delta: float | None = None,
        filled_notional_delta: float | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("order_event_id", order_event_id)
        _require_non_empty("order_id", order_id)
        _require_non_empty("event_type", event_type)

        conflict_clause = "ON CONFLICT (order_id, event_time, event_type) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO portfolio.order_events (
                    order_event_id, order_id, event_time, event_type,
                    filled_size_delta, filled_notional_delta, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING order_event_id;
                """,
                (
                    order_event_id,
                    order_id,
                    event_time,
                    event_type,
                    filled_size_delta,
                    filled_notional_delta,
                    _json_or_none(raw_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def upsert_nba_team(
        self,
        *,
        team_id: int,
        team_slug: str,
        team_name: str,
        team_city: str | None = None,
        conference: str | None = None,
        division: str | None = None,
        metadata_json: dict[str, Any] | list[Any] | None = None,
    ) -> int:
        if team_id <= 0:
            raise ValueError("team_id must be a positive integer")
        _require_non_empty("team_slug", team_slug)
        _require_non_empty("team_name", team_name)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nba.nba_teams (
                    team_id, team_slug, team_name, team_city, conference, division, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (team_id)
                DO UPDATE SET
                    team_slug = EXCLUDED.team_slug,
                    team_name = EXCLUDED.team_name,
                    team_city = EXCLUDED.team_city,
                    conference = EXCLUDED.conference,
                    division = EXCLUDED.division,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING team_id;
                """,
                (
                    team_id,
                    team_slug,
                    team_name,
                    team_city,
                    conference,
                    division,
                    _json_or_none(metadata_json),
                ),
            )
            return int(cursor.fetchone()[0])

    def upsert_nba_game(
        self,
        *,
        game_id: str,
        season: str | None = None,
        game_date: Any | None = None,
        game_start_time: datetime | None = None,
        game_status: int | None = None,
        game_status_text: str | None = None,
        period: int | None = None,
        game_clock: str | None = None,
        home_team_id: int | None = None,
        away_team_id: int | None = None,
        home_team_slug: str | None = None,
        away_team_slug: str | None = None,
        home_score: int | None = None,
        away_score: int | None = None,
        updated_at: datetime | None = None,
    ) -> str:
        _require_non_empty("game_id", game_id)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nba.nba_games (
                    game_id, season, game_date, game_start_time, game_status, game_status_text,
                    period, game_clock, home_team_id, away_team_id, home_team_slug, away_team_slug,
                    home_score, away_score, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (game_id)
                DO UPDATE SET
                    season = EXCLUDED.season,
                    game_date = EXCLUDED.game_date,
                    game_start_time = EXCLUDED.game_start_time,
                    game_status = EXCLUDED.game_status,
                    game_status_text = EXCLUDED.game_status_text,
                    period = EXCLUDED.period,
                    game_clock = EXCLUDED.game_clock,
                    home_team_id = EXCLUDED.home_team_id,
                    away_team_id = EXCLUDED.away_team_id,
                    home_team_slug = EXCLUDED.home_team_slug,
                    away_team_slug = EXCLUDED.away_team_slug,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score,
                    updated_at = EXCLUDED.updated_at
                RETURNING game_id;
                """,
                (
                    game_id,
                    season,
                    game_date,
                    game_start_time,
                    game_status,
                    game_status_text,
                    period,
                    game_clock,
                    home_team_id,
                    away_team_id,
                    home_team_slug,
                    away_team_slug,
                    home_score,
                    away_score,
                    updated_at,
                ),
            )
            return str(cursor.fetchone()[0])

    def upsert_nba_game_event_link(
        self,
        *,
        nba_game_event_link_id: str,
        game_id: str,
        event_id: str,
        confidence: float | None = None,
        linked_by: str | None = None,
        linked_at: datetime | None = None,
    ) -> str:
        _require_non_empty("nba_game_event_link_id", nba_game_event_link_id)
        _require_non_empty("game_id", game_id)
        _require_non_empty("event_id", event_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nba.nba_game_event_links (
                    nba_game_event_link_id, game_id, event_id, confidence, linked_by, linked_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (game_id, event_id)
                DO UPDATE SET
                    confidence = EXCLUDED.confidence,
                    linked_by = EXCLUDED.linked_by,
                    linked_at = EXCLUDED.linked_at
                RETURNING nba_game_event_link_id;
                """,
                (
                    nba_game_event_link_id,
                    game_id,
                    event_id,
                    confidence,
                    linked_by,
                    linked_at or datetime.now(timezone.utc),
                ),
            )
            return str(cursor.fetchone()[0])

    def insert_nba_team_stats_snapshot(
        self,
        *,
        team_id: int,
        season: str,
        captured_at: datetime,
        metric_set: str,
        stats_json: dict[str, Any] | list[Any],
        source: str | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        if team_id <= 0:
            raise ValueError("team_id must be a positive integer")
        _require_non_empty("season", season)
        _require_non_empty("metric_set", metric_set)

        conflict_clause = (
            "ON CONFLICT (team_id, season, captured_at, metric_set) DO NOTHING"
            if ignore_duplicates
            else ""
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO nba.nba_team_stats_snapshots (
                    team_id, season, captured_at, metric_set, stats_json, source
                ) VALUES (%s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING team_id;
                """,
                (team_id, season, captured_at, metric_set, _json_or_none(stats_json), source),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_nba_player_stats_snapshot(
        self,
        *,
        player_id: int,
        player_name: str | None,
        team_id: int | None,
        season: str,
        captured_at: datetime,
        metric_set: str,
        stats_json: dict[str, Any] | list[Any],
        source: str | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        if player_id <= 0:
            raise ValueError("player_id must be a positive integer")
        _require_non_empty("season", season)
        _require_non_empty("metric_set", metric_set)

        conflict_clause = (
            "ON CONFLICT (player_id, season, captured_at, metric_set) DO NOTHING"
            if ignore_duplicates
            else ""
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO nba.nba_player_stats_snapshots (
                    player_id, player_name, team_id, season, captured_at, metric_set, stats_json, source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING player_id;
                """,
                (
                    player_id,
                    player_name,
                    team_id,
                    season,
                    captured_at,
                    metric_set,
                    _json_or_none(stats_json),
                    source,
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_nba_team_insight(
        self,
        *,
        insight_id: str,
        team_id: int,
        captured_at: datetime,
        insight_type: str | None = None,
        category: str | None = None,
        text: str | None = None,
        condition: str | None = None,
        value: str | None = None,
        source: str | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("insight_id", insight_id)
        if team_id <= 0:
            raise ValueError("team_id must be a positive integer")

        conflict_clause = "ON CONFLICT (insight_id) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO nba.nba_team_insights (
                    insight_id, team_id, insight_type, category, text, condition, value, source, captured_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING insight_id;
                """,
                (
                    insight_id,
                    team_id,
                    insight_type,
                    category,
                    text,
                    condition,
                    value,
                    source,
                    captured_at,
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_nba_live_game_snapshot(
        self,
        *,
        game_id: str,
        captured_at: datetime,
        period: int | None = None,
        clock: str | None = None,
        home_score: int | None = None,
        away_score: int | None = None,
        payload_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("game_id", game_id)
        conflict_clause = "ON CONFLICT (game_id, captured_at) DO NOTHING" if ignore_duplicates else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO nba.nba_live_game_snapshots (
                    game_id, captured_at, period, clock, home_score, away_score, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING game_id;
                """,
                (
                    game_id,
                    captured_at,
                    period,
                    clock,
                    home_score,
                    away_score,
                    _json_or_none(payload_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def upsert_nba_play_by_play_event(
        self,
        *,
        game_id: str,
        event_index: int,
        action_id: str | None = None,
        period: int | None = None,
        clock: str | None = None,
        description: str | None = None,
        home_score: int | None = None,
        away_score: int | None = None,
        is_score_change: bool | None = None,
        payload_json: dict[str, Any] | list[Any] | None = None,
    ) -> bool:
        _require_non_empty("game_id", game_id)
        if event_index < 0:
            raise ValueError("event_index must be >= 0")

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nba.nba_play_by_play (
                    game_id, event_index, action_id, period, clock, description,
                    home_score, away_score, is_score_change, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (game_id, event_index)
                DO UPDATE SET
                    action_id = EXCLUDED.action_id,
                    period = EXCLUDED.period,
                    clock = EXCLUDED.clock,
                    description = EXCLUDED.description,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score,
                    is_score_change = EXCLUDED.is_score_change,
                    payload_json = EXCLUDED.payload_json
                RETURNING game_id;
                """,
                (
                    game_id,
                    event_index,
                    action_id,
                    period,
                    clock,
                    description,
                    home_score,
                    away_score,
                    is_score_change,
                    _json_or_none(payload_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def insert_event_information_score(
        self,
        *,
        event_id: str,
        scored_at: datetime,
        information_profile_id: str | None = None,
        coverage_score: float | None = None,
        quality_score: float | None = None,
        latency_score: float | None = None,
        is_trade_eligible: bool = False,
        missing_fields_json: dict[str, Any] | list[Any] | None = None,
        ignore_duplicates: bool = True,
    ) -> bool:
        _require_non_empty("event_id", event_id)
        conflict_clause = "ON CONFLICT (event_id, scored_at) DO NOTHING" if ignore_duplicates else ""

        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO catalog.event_information_scores (
                    event_id, scored_at, information_profile_id, coverage_score,
                    quality_score, latency_score, is_trade_eligible, missing_fields_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                {conflict_clause}
                RETURNING event_id;
                """,
                (
                    event_id,
                    scored_at,
                    information_profile_id,
                    coverage_score,
                    quality_score,
                    latency_score,
                    is_trade_eligible,
                    _json_or_none(missing_fields_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None

    def upsert_outcome_price_candle(
        self,
        *,
        outcome_id: str,
        timeframe: str,
        open_time: datetime,
        source: str,
        open: float | None = None,
        high: float | None = None,
        low: float | None = None,
        close: float | None = None,
        volume: float | None = None,
        raw_json: dict[str, Any] | list[Any] | None = None,
    ) -> bool:
        _require_non_empty("outcome_id", outcome_id)
        _require_non_empty("timeframe", timeframe)
        _require_non_empty("source", source)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO market_data.outcome_price_candles (
                    outcome_id, timeframe, open_time, source, open, high, low, close, volume, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (outcome_id, timeframe, open_time, source)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    raw_json = EXCLUDED.raw_json
                RETURNING outcome_id;
                """,
                (
                    outcome_id,
                    timeframe,
                    open_time,
                    source,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    _json_or_none(raw_json),
                ),
            )
            row = cursor.fetchone()
            return row is not None
