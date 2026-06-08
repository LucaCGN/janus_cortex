from __future__ import annotations

from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parent
ASSET_ROOT = FRONTEND_ROOT / "assets"
INDEX_HTML = FRONTEND_ROOT / "index.html"


def render_frontend_index() -> str:
    """Return the app shell used by crypto-options read-only UI routes."""

    return INDEX_HTML.read_text(encoding="utf-8")
