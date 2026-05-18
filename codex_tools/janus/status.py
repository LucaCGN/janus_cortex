"""Janus status helper for the target Codex tools namespace."""

from __future__ import annotations

from typing import Any

from codex_tools.janus.client import DEFAULT_API_ROOT, api_json, base_parser, exit_for_response

STATUS_PATH = "/v1/ops/status"


def get_status(api_root: str = DEFAULT_API_ROOT) -> dict[str, Any]:
    """Return the Janus ops status payload through the Janus API."""
    return api_json(api_root, "GET", STATUS_PATH)


def main_for_status(description: str) -> None:
    """Parse shared status args, call the Janus status endpoint, and print JSON."""
    parser = base_parser(description)
    args = parser.parse_args()
    exit_for_response(get_status(args.api_root))
