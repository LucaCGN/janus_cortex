"""Janus status helper for the target Codex tools namespace."""

from __future__ import annotations

from typing import Any

from codex_tools.janus.client import DEFAULT_API_ROOT, api_json


def get_status(api_root: str = DEFAULT_API_ROOT) -> dict[str, Any]:
    """Return the Janus ops status payload through the Janus API."""
    return api_json(api_root, "GET", "/v1/ops/status")
