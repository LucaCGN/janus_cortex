from __future__ import annotations

from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from app.api.main import app

        return app
    if name == "create_app":
        from app.api.main import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
