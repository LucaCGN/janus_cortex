"""Janus ops-cycle helpers for the target Codex tools namespace."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from typing import Any

from codex_tool._client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response

DATA_REFRESH_PATH = "/v1/ops/data-refresh"
INTEGRITY_CHECK_PATH = "/v1/ops/integrity-check"
LIVE_MONITOR_PATH = "/v1/ops/live-monitor"
POSTGAME_REVIEW_PATH = "/v1/ops/postgame-review"


def build_cycle_parser(description: str) -> ArgumentParser:
    """Build the shared parser for Janus ops-cycle commands."""
    return add_cycle_args(base_parser(description))


def build_cycle_payload(args: Namespace) -> dict[str, Any]:
    """Return the shared Janus ops-cycle payload."""
    return cycle_payload(args)


def run_ops_cycle(api_root: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call a Janus ops-cycle endpoint."""
    return api_json(api_root, "POST", path, payload)


def main_for_cycle(description: str, path: str) -> None:
    """Parse shared cycle args, call the selected endpoint, and print JSON."""
    parser = build_cycle_parser(description)
    args = parser.parse_args()
    exit_for_response(run_ops_cycle(args.api_root, path, build_cycle_payload(args)))
