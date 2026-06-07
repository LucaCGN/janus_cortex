from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.frontend import render_frontend_index
from crypto_options_app.strategies.manager import (
    strategy_catalog_summary,
    strategy_readiness_summary,
    strategy_replay_summary,
    strategy_validation_lab_summary,
)
from crypto_options_app.strategies.promotion import (
    evaluate_and_persist_strategy_promotions,
    promotion_state_summary,
)
from crypto_options_app.strategies.registry import get_strategy


router = APIRouter(prefix="/v1/crypto-options-app/strategies", tags=["crypto-options-app-strategies"])


@router.get("/catalog")
def crypto_options_strategy_catalog(request: Request) -> dict:
    """Return read-only strategy definitions mirrored into the canonical DB."""

    with _connect_request_db(request) as conn:
        return strategy_catalog_summary(conn)


@router.get("/catalog/{strategy_id}")
def crypto_options_strategy_catalog_item(request: Request, strategy_id: str) -> dict:
    """Return one read-only strategy definition."""

    try:
        spec = get_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy_not_found") from exc
    with _connect_request_db(request) as conn:
        payload = strategy_catalog_summary(conn)
    for row in payload["strategies"]:
        if row["strategy_id"] == spec.strategy_id:
            return row
    raise HTTPException(status_code=404, detail="strategy_not_found")


@router.get("/readiness")
def crypto_options_strategy_readiness(request: Request) -> dict:
    """Return compact replay/pulse readiness for the strategy-validation lab."""

    with _connect_request_db(request) as conn:
        return strategy_readiness_summary(conn)


@router.get("/validation-lab")
def crypto_options_strategy_validation_lab(request: Request) -> dict:
    """Return compact read-only validation and budget-ledger state for the strategy lab."""

    with _connect_request_db(request) as conn:
        return strategy_validation_lab_summary(conn)


@router.get("/promotion")
def crypto_options_strategy_promotion(request: Request) -> dict:
    """Return read-only strategy promotion/demotion state.

    The default dashboard path reads persisted promotion labels so it does not
    block behind full SQLite evidence scans while data services are writing.
    Pass ``?refresh=1`` to run the bounded state evaluator explicitly. Neither
    path authorizes live execution; supervised runtime gates remain the only
    live order path.
    """

    with _connect_request_db(request) as conn:
        refresh = str(request.query_params.get("refresh") or "").lower() in {"1", "true", "yes"}
        if refresh:
            return evaluate_and_persist_strategy_promotions(conn)
        return promotion_state_summary(conn)


@router.get("/replay/backtests")
def crypto_options_strategy_replay_backtests(request: Request, limit: int = 100) -> dict:
    """Return read-only historical strategy replay/backtest runs."""

    with _connect_request_db(request) as conn:
        return strategy_replay_summary(conn, replay_mode="historical_backtest", limit=limit)


@router.get("/replay/live")
def crypto_options_strategy_live_replay(request: Request, limit: int = 100) -> dict:
    """Return read-only shadow/live-replay strategy runs separate from live trading."""

    with _connect_request_db(request) as conn:
        return strategy_replay_summary(conn, replay_mode="live_replay", limit=limit)


@router.get("/lab", response_class=HTMLResponse)
def crypto_options_strategy_lab() -> HTMLResponse:
    """Render the read-only strategy validation lab dashboard."""

    return HTMLResponse(render_frontend_index())


def _connect_request_db(request: Request):
    config = getattr(request.app.state, "crypto_options_config", DEFAULT_CONFIG)
    db_path = Path(getattr(config, "db_path", DEFAULT_CONFIG.db_path))
    if not db_path.exists():
        initialize_schema(db_path)
    return connect(db_path)
