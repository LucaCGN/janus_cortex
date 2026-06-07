from __future__ import annotations

from crypto_options_app.api.app import create_app


app = create_app()


__all__ = ["app", "create_app"]
