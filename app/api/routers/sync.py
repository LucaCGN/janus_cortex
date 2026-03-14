from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extras import Json
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.jobs import ensure_api_sync_job_definition, insert_job_run
from app.api.models import (
    MappingSyncRequest,
    PolymarketClosedPositionConsolidationRequest,
    NbaGameLiveSyncRequest,
    NbaScheduleSyncRequest,
    NbaSeasonSyncRequest,
    PolymarketOrderbookSyncRequest,
    PolymarketPortfolioSyncRequest,
    PolymarketPricesSyncRequest,
    PolymarketSyncRequest,
    SyncTriggerResponse,
)
from app.data.databases.repositories import JanusUpsertRepository
from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    DEFAULT_EXTRA_EVENT_PROBES,
    EventProbeConfig,
    build_today_nba_event_probes_from_scoreboard,
    run_polymarket_event_seed_pack,
)
from app.data.nodes.polymarket.blockchain.stream_orderbook import (
    OrderbookStreamConfig,
    stream_orderbook,
)
from app.data.nodes.polymarket.gamma.nba.odds_history_node import (
    NBAOddsHistoryRequest,
    fetch_clob_prices_history,
)
from app.data.nodes.nba.players.leaguedash_player_base_season import fetch_player_base_df
from app.data.nodes.nba.teams.leaguedash_team_advanced_season import fetch_team_advanced_df
from app.data.nodes.nba.teams.leaguedash_team_base_season import fetch_team_base_df
from app.data.nodes.nba.teams.team_recent_form_last5 import compute_last5_metrics_df
from app.data.pipelines.daily.cross_domain.sync_mappings import run_cross_domain_mapping_sync
from app.data.pipelines.daily.nba.sync_postgres import run_nba_live_game_sync, run_nba_metadata_sync
from app.data.pipelines.daily.polymarket.consolidate_closed_positions import (
    consolidate_closed_positions_for_wallet,
)
from app.data.pipelines.daily.polymarket.sync_portfolio import run_portfolio_mirror_sync


router = APIRouter(prefix="/v1/sync", tags=["sync"])


def _slug_from_probe(probe: EventProbeConfig) -> str:
    return probe.url.rstrip("/").split("/")[-1]


def _select_polymarket_probes(
    request: PolymarketSyncRequest,
    connection: PsycopgConnection,
) -> list[EventProbeConfig]:
    selected: list[EventProbeConfig] = []
    if request.probe_set in {"extras", "combined"}:
        selected.extend(DEFAULT_EXTRA_EVENT_PROBES)
    if request.probe_set in {"today_nba", "combined"}:
        today = build_today_nba_event_probes_from_scoreboard(
            max_finished=request.max_finished,
            max_live=request.max_live,
            max_upcoming=request.max_upcoming,
            include_upcoming=request.include_upcoming,
            stream_sample_count=request.stream_sample_count,
            stream_sample_interval_sec=request.stream_sample_interval_sec,
            stream_max_outcomes=request.stream_max_outcomes,
        )
        selected.extend(today.all)

    if request.steps:
        wanted = set(request.steps)
        selected = [item for item in selected if item.step_code in wanted]

    if request.missing_only and selected:
        slugs = [_slug_from_probe(item) for item in selected]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT canonical_slug
                FROM catalog.events
                WHERE canonical_slug = ANY(%s);
                """,
                (slugs,),
            )
            existing = {str(row[0]) for row in cursor.fetchall()}
        selected = [item for item in selected if _slug_from_probe(item) not in existing]

    return selected


def _create_manual_sync_run(
    connection: PsycopgConnection,
    *,
    provider_code: str,
    provider_name: str,
    module_code: str,
    module_name: str,
    pipeline_name: str,
    meta_json: dict[str, Any],
) -> str:
    repo = JanusUpsertRepository(connection)
    provider_id = repo.upsert_provider(
        provider_id=str(uuid4()),
        code=provider_code,
        name=provider_name,
        category="sports_data",
        base_url=None,
        auth_type="none",
    )
    module_id = repo.upsert_module(
        module_id=str(uuid4()),
        code=module_code,
        name=module_name,
        description=pipeline_name,
        owner="janus",
        is_active=True,
    )
    sync_run_id = str(uuid4())
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
                pipeline_name,
                "manual_api_trigger",
                "running",
                datetime.now(timezone.utc),
                Json(meta_json),
            ),
        )
    return sync_run_id


def _finalize_manual_sync_run(
    connection: PsycopgConnection,
    *,
    sync_run_id: str,
    status: str,
    rows_read: int,
    rows_written: int,
    error_text: str | None,
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


def _run_nba_team_stats_sync(
    connection: PsycopgConnection,
    *,
    season: str,
) -> dict[str, Any]:
    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="nba_stats_api",
        provider_name="NBA Stats API",
        module_code="nba_team_stats_sync",
        module_name="NBA Team Stats Sync",
        pipeline_name="api.sync.nba.teams",
        meta_json={"season": season},
    )
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    error_text: str | None = None
    run_status = "success"
    try:
        base_df = fetch_team_base_df(season=season)
        advanced_df = fetch_team_advanced_df(season=season)
        rows_read += int(len(base_df) + len(advanced_df))

        repo = JanusUpsertRepository(connection)
        for metric_set, frame in (("base", base_df), ("advanced", advanced_df)):
            for row in frame.to_dict(orient="records"):
                team_id_raw = row.get("team_id")
                if team_id_raw is None:
                    continue
                team_id = int(team_id_raw)
                team_slug = str(row.get("team_slug") or f"T{team_id}").upper()
                team_name = str(row.get("team_name") or team_slug)
                repo.upsert_nba_team(
                    team_id=team_id,
                    team_slug=team_slug,
                    team_name=team_name,
                )
                inserted = repo.insert_nba_team_stats_snapshot(
                    team_id=team_id,
                    season=season,
                    captured_at=now,
                    metric_set=metric_set,
                    stats_json=row,
                    source=f"nba_stats_{metric_set}",
                    ignore_duplicates=True,
                )
                if inserted:
                    rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "error_text": error_text,
    }


def _run_nba_player_stats_sync(
    connection: PsycopgConnection,
    *,
    season: str,
) -> dict[str, Any]:
    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="nba_stats_api",
        provider_name="NBA Stats API",
        module_code="nba_player_stats_sync",
        module_name="NBA Player Stats Sync",
        pipeline_name="api.sync.nba.players",
        meta_json={"season": season},
    )
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    error_text: str | None = None
    run_status = "success"
    try:
        base_df = fetch_player_base_df(season=season)
        rows_read += int(len(base_df))

        repo = JanusUpsertRepository(connection)
        for row in base_df.to_dict(orient="records"):
            team_id_raw = row.get("team_id")
            team_id = int(team_id_raw) if team_id_raw is not None else None
            team_slug = str(row.get("team_slug") or "").upper().strip()
            if team_id is not None and team_slug:
                repo.upsert_nba_team(
                    team_id=team_id,
                    team_slug=team_slug,
                    team_name=team_slug,
                )
            player_id_raw = row.get("player_nba_id")
            if player_id_raw is None:
                continue
            inserted = repo.insert_nba_player_stats_snapshot(
                player_id=int(player_id_raw),
                player_name=str(row.get("player_name") or "") or None,
                team_id=team_id,
                season=season,
                captured_at=now,
                metric_set="base",
                stats_json=row,
                source="nba_players_base",
                ignore_duplicates=True,
            )
            if inserted:
                rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "error_text": error_text,
    }


def _run_nba_team_insights_sync(
    connection: PsycopgConnection,
    *,
    season: str,
) -> dict[str, Any]:
    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="nba_stats_api",
        provider_name="NBA Stats API",
        module_code="nba_team_insights_sync",
        module_name="NBA Team Insights Sync",
        pipeline_name="api.sync.nba.insights",
        meta_json={"season": season},
    )
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    error_text: str | None = None
    run_status = "success"
    try:
        insights_df = compute_last5_metrics_df(season=season)
        rows_read += int(len(insights_df))
        repo = JanusUpsertRepository(connection)
        for row in insights_df.to_dict(orient="records"):
            team_id_raw = row.get("team_id")
            if team_id_raw is None:
                continue
            team_id = int(team_id_raw)
            team_slug = str(row.get("team_slug") or f"T{team_id}").upper()
            team_name = str(row.get("team_name") or team_slug)
            repo.upsert_nba_team(team_id=team_id, team_slug=team_slug, team_name=team_name)

            insight_id = str(uuid4())
            inserted = repo.insert_nba_team_insight(
                insight_id=insight_id,
                team_id=team_id,
                captured_at=now,
                insight_type="recent_form_last5",
                category="performance",
                text=(
                    f"Last 5 win rate {row.get('last_5_games_win_rate')} "
                    f"with avg points {row.get('last_5_avg_points')}"
                ),
                condition="last_5_win_rate",
                value=str(row.get("last_5_games_win_rate")),
                source="nba_team_recent_form_last5",
                ignore_duplicates=True,
            )
            if inserted:
                rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "error_text": error_text,
    }


def _run_nba_live_game_sync(
    connection: PsycopgConnection,
    *,
    game_id: str,
    payload: NbaGameLiveSyncRequest,
) -> dict[str, Any]:
    _ = connection
    summary = run_nba_live_game_sync(
        game_id=game_id,
        include_live_snapshots=payload.include_live_snapshots,
        include_play_by_play=payload.include_play_by_play,
    )
    return to_jsonable(summary.__dict__)


def _parse_wallet_address(raw_value: str | None) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    if text.startswith("0x") and len(text) >= 42:
        return text[:42]
    return None


def _resolve_portfolio_wallet(
    connection: PsycopgConnection,
    payload: PolymarketPortfolioSyncRequest | PolymarketClosedPositionConsolidationRequest,
) -> str | None:
    from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials

    requested_wallet = _parse_wallet_address(payload.wallet_address)
    if requested_wallet:
        # If an account exists and has a dedicated proxy wallet, prefer proxy for Data-API mirror calls.
        with cursor_dict(connection) as cursor:
            cursor.execute(
                """
                SELECT wallet_address, proxy_wallet_address
                FROM portfolio.trading_accounts
                WHERE lower(COALESCE(wallet_address, '')) = lower(%s)
                   OR lower(COALESCE(proxy_wallet_address, '')) = lower(%s)
                ORDER BY created_at DESC;
                """,
                (requested_wallet, requested_wallet),
            )
            rows = fetchall_dicts(cursor)
        if rows:
            # Prefer a dedicated proxy wallet different from the requested wallet.
            for item in rows:
                proxy_wallet = _parse_wallet_address(item.get("proxy_wallet_address"))
                if proxy_wallet and proxy_wallet.lower() != requested_wallet.lower():
                    return proxy_wallet
            # Fallback to any proxy if present.
            for item in rows:
                proxy_wallet = _parse_wallet_address(item.get("proxy_wallet_address"))
                if proxy_wallet:
                    return proxy_wallet
        # No account mapping found yet: if requested wallet matches configured primary,
        # prefer configured proxy for Data-API calls.
        creds = PolymarketCredentials.from_env()
        configured_primary = _parse_wallet_address(creds.primary_wallet_raw) or _parse_wallet_address(
            creds.wallet_address
        )
        configured_proxy = _parse_wallet_address(creds.proxy_wallet_raw)
        if (
            configured_proxy
            and configured_primary
            and requested_wallet.lower() == configured_primary.lower()
        ):
            return configured_proxy
        return requested_wallet

    if payload.wallet_address:
        wallet = _parse_wallet_address(payload.wallet_address)
        if wallet:
            return wallet
    creds = PolymarketCredentials.from_env()
    candidates = (
        creds.proxy_wallet_raw,
        creds.wallet_address,
        creds.primary_wallet_raw,
        creds.funder_address,
    )
    for item in candidates:
        wallet = _parse_wallet_address(item)
        if wallet:
            return wallet
    return None


def _resolve_outcome_token(
    connection: PsycopgConnection,
    *,
    outcome_id: UUID | None,
    token_id: str | None,
) -> tuple[str, str]:
    with cursor_dict(connection) as cursor:
        if outcome_id is not None:
            cursor.execute(
                """
                SELECT outcome_id, token_id
                FROM catalog.outcomes
                WHERE outcome_id = %s
                LIMIT 1;
                """,
                (str(outcome_id),),
            )
            row = fetchall_dicts(cursor)
            if not row:
                raise HTTPException(status_code=404, detail="outcome_id not found")
            resolved_token_id = str(row[0].get("token_id") or "").strip()
            if not resolved_token_id:
                raise HTTPException(status_code=422, detail="outcome token_id is missing")
            return str(outcome_id), resolved_token_id

        token = str(token_id or "").strip()
        if not token:
            raise HTTPException(status_code=422, detail="outcome_id or token_id is required")
        cursor.execute(
            """
            SELECT outcome_id, token_id
            FROM catalog.outcomes
            WHERE token_id = %s
            LIMIT 1;
            """,
            (token,),
        )
        row = fetchall_dicts(cursor)
        if not row:
            raise HTTPException(status_code=404, detail="token_id not found in catalog.outcomes")
        return str(row[0]["outcome_id"]), str(row[0]["token_id"])


def _run_polymarket_portfolio_sync(
    connection: PsycopgConnection,
    payload: PolymarketPortfolioSyncRequest,
) -> dict[str, Any]:
    wallet_address = _resolve_portfolio_wallet(connection, payload)
    if wallet_address is None:
        raise HTTPException(
            status_code=422,
            detail="wallet_address is required (payload or env POLYMARKET_PROXY_WALLET/POLYMARKET_PRIMARY_WALLET)",
        )

    summary = run_portfolio_mirror_sync(
        wallet_address=wallet_address,
        limit=payload.limit,
        payload_override=payload.payload_override,
    )
    return to_jsonable(summary.__dict__)


def _run_polymarket_closed_positions_consolidation(
    connection: PsycopgConnection,
    payload: PolymarketClosedPositionConsolidationRequest,
) -> dict[str, Any]:
    wallet_address = _resolve_portfolio_wallet(connection, payload)
    if wallet_address is None:
        raise HTTPException(
            status_code=422,
            detail="wallet_address is required (payload or env POLYMARKET_PROXY_WALLET/POLYMARKET_PRIMARY_WALLET)",
        )

    mirror_summary: dict[str, Any] | None = None
    mirror_rows_read = 0
    mirror_rows_written = 0
    mirror_error: str | None = None
    if payload.run_portfolio_sync:
        mirror = run_portfolio_mirror_sync(
            wallet_address=wallet_address,
            limit=payload.limit,
            payload_override=None,
        )
        mirror_summary = to_jsonable(mirror.__dict__)
        mirror_rows_read = int(mirror.rows_read)
        mirror_rows_written = int(mirror.rows_written)
        mirror_error = mirror.error_text

    consolidation = consolidate_closed_positions_for_wallet(
        wallet_address=wallet_address,
        stale_sample_limit=payload.stale_sample_limit,
    )
    consolidation_summary = to_jsonable(consolidation.__dict__)
    status_value = "success"
    errors: list[str] = []
    if mirror_error:
        status_value = "partial_success" if consolidation.status == "success" else "error"
        errors.append(f"mirror={mirror_error}")
    if consolidation.status != "success":
        status_value = "error"
        if consolidation.error_text:
            errors.append(f"consolidation={consolidation.error_text}")

    return {
        "sync_run_id": None,
        "status": status_value,
        "rows_read": mirror_rows_read + int(consolidation.rows_read),
        "rows_written": mirror_rows_written + int(consolidation.rows_written),
        "wallet_address": wallet_address,
        "mirror_summary": mirror_summary,
        "consolidation_summary": consolidation_summary,
        "error_text": "; ".join(errors) if errors else None,
    }


def _run_polymarket_orderbook_sync(
    connection: PsycopgConnection,
    payload: PolymarketOrderbookSyncRequest,
) -> dict[str, Any]:
    outcome_id, resolved_token_id = _resolve_outcome_token(
        connection,
        outcome_id=payload.outcome_id,
        token_id=payload.token_id,
    )

    config = OrderbookStreamConfig(
        token_id=resolved_token_id,
        market_id=None,
        poll_interval_seconds=payload.sample_interval_sec,
        max_iterations=payload.sample_count,
        continue_on_error=True,
    )
    snapshots = stream_orderbook(config=config)
    rows_read = len(snapshots)
    levels_read = sum(len(item.bids) + len(item.asks) for item in snapshots)
    if payload.dry_run:
        return {
            "sync_run_id": None,
            "status": "success",
            "rows_read": rows_read,
            "rows_written": 0,
            "outcome_id": outcome_id,
            "token_id": resolved_token_id,
            "snapshots_read": rows_read,
            "levels_read": levels_read,
            "dry_run": True,
        }

    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="polymarket_clob_api",
        provider_name="Polymarket CLOB API",
        module_code="polymarket_orderbook_sync",
        module_name="Polymarket Orderbook Sync",
        pipeline_name="api.sync.polymarket.orderbook",
        meta_json={
            "outcome_id": outcome_id,
            "token_id": resolved_token_id,
            "sample_count": payload.sample_count,
            "sample_interval_sec": payload.sample_interval_sec,
            "max_levels_per_side": payload.max_levels_per_side,
        },
    )

    repo = JanusUpsertRepository(connection)
    rows_written = 0
    levels_written = 0
    run_status = "success"
    error_text: str | None = None
    try:
        for snapshot in snapshots:
            snapshot_id = str(uuid4())
            best_bid = max((float(level.price) for level in snapshot.bids), default=None)
            best_ask = min((float(level.price) for level in snapshot.asks), default=None)
            spread = (
                float(best_ask - best_bid)
                if best_bid is not None and best_ask is not None
                else None
            )
            mid_price = (
                float((best_bid + best_ask) / 2)
                if best_bid is not None and best_ask is not None
                else None
            )
            inserted_snapshot = repo.insert_orderbook_snapshot(
                orderbook_snapshot_id=snapshot_id,
                outcome_id=outcome_id,
                captured_at=snapshot.timestamp,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                mid_price=mid_price,
                bid_depth=float(sum(level.size for level in snapshot.bids)),
                ask_depth=float(sum(level.size for level in snapshot.asks)),
                raw_json={
                    "token_id": resolved_token_id,
                    "bids": [level.__dict__ for level in snapshot.bids],
                    "asks": [level.__dict__ for level in snapshot.asks],
                },
                ignore_duplicates=True,
            )
            if inserted_snapshot:
                rows_written += 1

            bids = sorted(snapshot.bids, key=lambda item: item.price, reverse=True)
            asks = sorted(snapshot.asks, key=lambda item: item.price)

            for idx, level in enumerate(bids[: payload.max_levels_per_side], start=1):
                inserted_level = repo.insert_orderbook_level(
                    orderbook_snapshot_id=snapshot_id,
                    side="bid",
                    level_no=idx,
                    price=float(level.price),
                    size=float(level.size),
                    order_count=None,
                    ignore_duplicates=True,
                )
                if inserted_level:
                    levels_written += 1

            for idx, level in enumerate(asks[: payload.max_levels_per_side], start=1):
                inserted_level = repo.insert_orderbook_level(
                    orderbook_snapshot_id=snapshot_id,
                    side="ask",
                    level_no=idx,
                    price=float(level.price),
                    size=float(level.size),
                    order_count=None,
                    ignore_duplicates=True,
                )
                if inserted_level:
                    levels_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read + levels_read,
        rows_written=rows_written + levels_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read + levels_read,
        "rows_written": rows_written + levels_written,
        "outcome_id": outcome_id,
        "token_id": resolved_token_id,
        "snapshots_read": rows_read,
        "levels_read": levels_read,
        "snapshots_written": rows_written,
        "levels_written": levels_written,
        "dry_run": False,
        "error_text": error_text,
    }


def _run_polymarket_prices_sync(
    connection: PsycopgConnection,
    payload: PolymarketPricesSyncRequest,
) -> dict[str, Any]:
    outcome_id, resolved_token_id = _resolve_outcome_token(
        connection,
        outcome_id=payload.outcome_id,
        token_id=None,
    )
    now = datetime.now(timezone.utc)
    req = NBAOddsHistoryRequest(
        interval=payload.interval,
        fidelity=payload.fidelity,
        start_date_min=now - timedelta(hours=payload.lookback_hours),
        start_date_max=now,
        allow_snapshot_fallback=payload.allow_snapshot_fallback,
        max_outcomes=1,
    )
    points = fetch_clob_prices_history(token_id=resolved_token_id, req=req)
    rows_read = len(points)

    if payload.dry_run:
        return {
            "sync_run_id": None,
            "status": "success",
            "rows_read": rows_read,
            "rows_written": 0,
            "outcome_id": outcome_id,
            "token_id": resolved_token_id,
            "dry_run": True,
        }

    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="polymarket_clob_api",
        provider_name="Polymarket CLOB API",
        module_code="polymarket_prices_sync",
        module_name="Polymarket Prices Sync",
        pipeline_name="api.sync.polymarket.prices",
        meta_json={
            "outcome_id": outcome_id,
            "token_id": resolved_token_id,
            "lookback_hours": payload.lookback_hours,
            "interval": payload.interval,
            "fidelity": payload.fidelity,
            "allow_snapshot_fallback": payload.allow_snapshot_fallback,
        },
    )
    repo = JanusUpsertRepository(connection)
    rows_written = 0
    run_status = "success"
    error_text: str | None = None

    try:
        for item in points:
            inserted = repo.insert_outcome_price_tick(
                outcome_id=outcome_id,
                ts=item["ts"],
                source="clob_prices_history",
                price=float(item["price"]),
                bid=None,
                ask=None,
                volume=None,
                liquidity=None,
                raw_json=item.get("raw"),
                ignore_duplicates=True,
            )
            if inserted:
                rows_written += 1

        if rows_written == 0 and payload.allow_snapshot_fallback:
            with cursor_dict(connection) as cursor:
                cursor.execute(
                    """
                    SELECT mss.last_price
                    FROM catalog.outcomes o
                    JOIN catalog.market_state_snapshots mss ON mss.market_id = o.market_id
                    WHERE o.outcome_id = %s AND mss.last_price IS NOT NULL
                    ORDER BY mss.captured_at DESC
                    LIMIT 1;
                    """,
                    (outcome_id,),
                )
                row = fetchone_dict(cursor)
            fallback_price = float(row["last_price"]) if row is not None else None
            if fallback_price is not None:
                inserted = repo.insert_outcome_price_tick(
                    outcome_id=outcome_id,
                    ts=now,
                    source="snapshot_fallback",
                    price=fallback_price,
                    bid=None,
                    ask=None,
                    volume=None,
                    liquidity=None,
                    raw_json={"route": "sync_polymarket_prices"},
                    ignore_duplicates=True,
                )
                if inserted:
                    rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "outcome_id": outcome_id,
        "token_id": resolved_token_id,
        "dry_run": False,
        "error_text": error_text,
    }


def _with_job_run(
    connection: PsycopgConnection,
    *,
    job_code: str,
    description: str,
    runner: Callable[[], dict[str, Any]],
) -> SyncTriggerResponse:
    started_at = datetime.now(timezone.utc)
    summary = runner()
    ended_at = datetime.now(timezone.utc)

    sync_run_id_value = summary.get("sync_run_id")
    if sync_run_id_value:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM core.sync_runs WHERE sync_run_id = %s;",
                (str(sync_run_id_value),),
            )
            if cursor.fetchone() is None:
                sync_run_id_value = None

    job_id = ensure_api_sync_job_definition(connection, job_code=job_code, description=description)
    job_run_id = insert_job_run(
        connection,
        job_id=job_id,
        sync_run_id=sync_run_id_value,
        status=str(summary.get("status") or "unknown"),
        started_at=started_at,
        ended_at=ended_at,
        error_text=summary.get("error_text"),
        metrics={
            "rows_read": summary.get("rows_read"),
            "rows_written": summary.get("rows_written"),
        },
    )
    return SyncTriggerResponse(
        job_run_id=UUID(job_run_id),
        sync_run_id=UUID(str(sync_run_id_value)) if sync_run_id_value else None,
        status=str(summary.get("status") or "unknown"),
        rows_read=summary.get("rows_read"),
        rows_written=summary.get("rows_written"),
        summary=to_jsonable(summary),
    )


@router.post(
    "/polymarket/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_events(
    payload: PolymarketSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    probes = _select_polymarket_probes(payload, connection)
    if not probes:
        raise HTTPException(status_code=400, detail="No probes selected.")

    def _runner() -> dict[str, Any]:
        summary = run_polymarket_event_seed_pack(probes, persist=True)
        return {
            "sync_run_id": summary.sync_run_id,
            "status": summary.status,
            "rows_read": summary.rows_read,
            "rows_written": summary.rows_written,
            "results": [result.__dict__ for result in summary.results],
        }

    return _with_job_run(
        connection,
        job_code="sync_polymarket_events",
        description="Sync polymarket events payload into catalog graph",
        runner=_runner,
    )


@router.post(
    "/polymarket/markets",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_markets(
    payload: PolymarketSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    probes = _select_polymarket_probes(payload, connection)
    if not probes:
        raise HTTPException(status_code=400, detail="No probes selected.")

    def _runner() -> dict[str, Any]:
        summary = run_polymarket_event_seed_pack(probes, persist=True)
        return {
            "sync_run_id": summary.sync_run_id,
            "status": summary.status,
            "rows_read": summary.rows_read,
            "rows_written": summary.rows_written,
            "results": [result.__dict__ for result in summary.results],
        }

    return _with_job_run(
        connection,
        job_code="sync_polymarket_markets",
        description="Sync polymarket markets/outcomes/state snapshots",
        runner=_runner,
    )


@router.post(
    "/nba/schedule",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_schedule(
    payload: NbaScheduleSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = run_nba_metadata_sync(
            season=payload.season,
            schedule_window_days=payload.schedule_window_days,
            include_live_snapshots=payload.include_live_snapshots,
            include_play_by_play=payload.include_play_by_play,
        )
        return to_jsonable(summary.__dict__)

    return _with_job_run(
        connection,
        job_code="sync_nba_schedule",
        description="Sync nba schedule + scoreboard metadata",
        runner=_runner,
    )


@router.post(
    "/nba/live/{game_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_live_game(
    game_id: str,
    payload: NbaGameLiveSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_live_game_sync(connection, game_id=game_id, payload=payload)

    return _with_job_run(
        connection,
        job_code="sync_nba_live_game",
        description="Sync nba live snapshot and play-by-play for a single game",
        runner=_runner,
    )


@router.post(
    "/nba/teams",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_teams(
    payload: NbaSeasonSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_team_stats_sync(connection, season=payload.season)

    return _with_job_run(
        connection,
        job_code="sync_nba_teams",
        description="Sync nba team stats snapshots",
        runner=_runner,
    )


@router.post(
    "/nba/players",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_players(
    payload: NbaSeasonSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_player_stats_sync(connection, season=payload.season)

    return _with_job_run(
        connection,
        job_code="sync_nba_players",
        description="Sync nba player stats snapshots",
        runner=_runner,
    )


@router.post(
    "/nba/insights",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_insights(
    payload: NbaSeasonSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_team_insights_sync(connection, season=payload.season)

    return _with_job_run(
        connection,
        job_code="sync_nba_insights",
        description="Sync nba insight rows",
        runner=_runner,
    )


@router.post(
    "/nba/mappings",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_mappings(
    payload: MappingSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = run_cross_domain_mapping_sync(
            lookback_days=payload.lookback_days,
            lookahead_days=payload.lookahead_days,
        )
        return to_jsonable(summary.__dict__)

    return _with_job_run(
        connection,
        job_code="sync_nba_mappings",
        description="Sync nba<->catalog mappings and information scores",
        runner=_runner,
    )


@router.post(
    "/polymarket/positions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_positions(
    payload: PolymarketPortfolioSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = _run_polymarket_portfolio_sync(connection, payload)
        summary["route_scope"] = "positions"
        return summary

    return _with_job_run(
        connection,
        job_code="sync_polymarket_positions",
        description="Sync polymarket portfolio positions snapshots",
        runner=_runner,
    )


@router.post(
    "/polymarket/orders",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_orders(
    payload: PolymarketPortfolioSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = _run_polymarket_portfolio_sync(connection, payload)
        summary["route_scope"] = "orders"
        return summary

    return _with_job_run(
        connection,
        job_code="sync_polymarket_orders",
        description="Sync polymarket orders mirror",
        runner=_runner,
    )


@router.post(
    "/polymarket/trades",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_trades(
    payload: PolymarketPortfolioSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = _run_polymarket_portfolio_sync(connection, payload)
        summary["route_scope"] = "trades"
        return summary

    return _with_job_run(
        connection,
        job_code="sync_polymarket_trades",
        description="Sync polymarket trades mirror",
        runner=_runner,
    )


@router.post(
    "/polymarket/closed-positions/consolidate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_closed_positions_consolidation(
    payload: PolymarketClosedPositionConsolidationRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = _run_polymarket_closed_positions_consolidation(connection, payload)
        summary["route_scope"] = "closed_positions_consolidation"
        return summary

    return _with_job_run(
        connection,
        job_code="sync_polymarket_closed_positions_consolidation",
        description="Validate event conclusion candidates and consolidate closed positions",
        runner=_runner,
    )


@router.post(
    "/polymarket/orderbook",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_orderbook(
    payload: PolymarketOrderbookSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_polymarket_orderbook_sync(connection, payload)

    return _with_job_run(
        connection,
        job_code="sync_polymarket_orderbook",
        description="Sync polymarket orderbook snapshots and levels",
        runner=_runner,
    )


@router.post(
    "/polymarket/prices",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_prices(
    payload: PolymarketPricesSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_polymarket_prices_sync(connection, payload)

    return _with_job_run(
        connection,
        job_code="sync_polymarket_prices",
        description="Sync polymarket outcome price ticks",
        runner=_runner,
    )


@router.get("/jobs/runs")
def list_job_runs(
    limit: int = 200,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                jr.job_run_id,
                jd.job_code,
                jr.sync_run_id,
                jr.started_at,
                jr.ended_at,
                jr.status,
                jr.error_text,
                jr.metrics_json
            FROM ops.job_runs jr
            JOIN ops.job_definitions jd ON jd.job_id = jr.job_id
            ORDER BY jr.started_at DESC
            LIMIT %s;
            """,
            (limit,),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}
