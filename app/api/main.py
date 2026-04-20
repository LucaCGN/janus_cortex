from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.errors import RequestContextMiddleware, install_exception_handlers
from app.api.routers.analysis_studio import ANALYSIS_STUDIO_STATIC_ROOT
from app.api.routers import (
    analysis_studio_router,
    catalog_router,
    market_data_router,
    nba_read_router,
    portfolio_router,
    sync_router,
    system_registry_router,
)


logger = logging.getLogger(__name__)
API_VERSION = "0.8.1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Janus Cortex API",
        version=API_VERSION,
        summary="FastAPI service layer for Janus Cortex data platform",
    )

    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)

    if ANALYSIS_STUDIO_STATIC_ROOT.exists():
        app.mount(
            "/analysis-studio/static",
            StaticFiles(directory=str(ANALYSIS_STUDIO_STATIC_ROOT)),
            name="analysis-studio-static",
        )

    app.include_router(analysis_studio_router)
    app.include_router(system_registry_router)
    app.include_router(catalog_router)
    app.include_router(market_data_router)
    app.include_router(portfolio_router)
    app.include_router(sync_router)
    app.include_router(nba_read_router)

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
