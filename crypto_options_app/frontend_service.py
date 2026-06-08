from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from crypto_options_app.frontend import ASSET_ROOT, INDEX_HTML


API_BASE = os.getenv(
    "JANUS_CRYPTO_OPTIONS_FRONTEND_API_BASE",
    "http://127.0.0.1:8011/v1/crypto-options-app",
)


def create_frontend_app() -> FastAPI:
    app = FastAPI(
        title="Crypto Options Frontend",
        version="0.1.0",
        summary="Standalone frontend shell for the crypto-options command center.",
    )
    app.mount("/assets", StaticFiles(directory=ASSET_ROOT), name="crypto_options_frontend_assets")

    @app.get("/{path:path}", response_class=HTMLResponse)
    def frontend_shell(path: str = "") -> HTMLResponse:
        html = INDEX_HTML.read_text(encoding="utf-8")
        html = html.replace('/v1/crypto-options-app/ui/assets/', '/assets/')
        html = html.replace(
            "</head>",
            f'<script>window.CRYPTO_OPTIONS_API_BASE = "{API_BASE}";</script>\n</head>',
        )
        return HTMLResponse(html)

    return app


app = create_frontend_app()


__all__ = ["app", "create_frontend_app"]
