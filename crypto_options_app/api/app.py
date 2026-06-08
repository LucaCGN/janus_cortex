from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from crypto_options_app.api.routers.dashboard import router as dashboard_router
from crypto_options_app.api.routers.health import router as health_router
from crypto_options_app.api.routers.signals import router as signals_router
from crypto_options_app.api.routers.strategies import router as strategies_router
from crypto_options_app.config import CryptoOptionsAppConfig, DEFAULT_CONFIG
from crypto_options_app.frontend import ASSET_ROOT, render_frontend_index


def create_app(config: CryptoOptionsAppConfig | None = None) -> FastAPI:
    """Build the isolated crypto-options FastAPI app without starting workers."""

    resolved_config = config or DEFAULT_CONFIG
    app = FastAPI(
        title="Crypto Options App",
        version=resolved_config.api_version,
        summary="Modular crypto-options data, replay, strategy, and supervised trading runtime.",
    )
    app.state.crypto_options_config = resolved_config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            resolved_config.frontend_origin,
            "http://127.0.0.1:8012",
            "http://localhost:8012",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.mount(
        "/v1/crypto-options-app/ui/assets",
        StaticFiles(directory=ASSET_ROOT),
        name="crypto_options_app_ui_assets",
    )

    @app.get("/v1/crypto-options-app", response_class=HTMLResponse)
    def crypto_options_app_command_center() -> HTMLResponse:
        """Render the main crypto-options command center SPA."""

        return HTMLResponse(render_frontend_index())

    app.include_router(health_router)
    app.include_router(dashboard_router)
    app.include_router(signals_router)
    app.include_router(strategies_router)
    return app
