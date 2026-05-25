from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import FastAPI

from app.api.errors import RequestContextMiddleware, install_exception_handlers
from app.api.routers import (
    analysis_studio_router,
    catalog_router,
    market_data_router,
    nba_live_router,
    nba_read_router,
    ops_router,
    portfolio_router,
    runtime_control_router,
    sync_router,
    system_registry_router,
    wnba_read_router,
)
from app.modules.agentic.live_strategy_worker import get_live_strategy_worker


logger = logging.getLogger(__name__)
API_VERSION = "1.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    status = get_live_strategy_worker().start_if_env_enabled()
    if status.get("start_status") == "started":
        logger.info("Janus live strategy worker started from environment configuration")
    try:
        yield
    finally:
        get_live_strategy_worker().stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Janus Cortex API",
        version=API_VERSION,
        summary="FastAPI service layer for Janus Cortex data platform",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)

    app.include_router(analysis_studio_router)
    app.include_router(system_registry_router)
    app.include_router(catalog_router)
    app.include_router(market_data_router)
    app.include_router(nba_live_router)
    app.include_router(ops_router)
    app.include_router(portfolio_router)
    app.include_router(runtime_control_router)
    app.include_router(sync_router)
    app.include_router(nba_read_router)
    app.include_router(wnba_read_router)

    @app.get("/")
    def root() -> dict[str, Any]:
        return {
            "service": "janus-cortex-api",
            "version": API_VERSION,
            "docs": "/docs",
        }

    logger.info("Janus Cortex API initialized")
    return app


app = create_app()
